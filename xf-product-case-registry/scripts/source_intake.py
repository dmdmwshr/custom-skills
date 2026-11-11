"""浏览器采集物的确定性、本地化接收核心。

本模块不驱动浏览器、不接管登录态，也不调用生产上传接口。浏览器只把已登录
会话中取得的列表、详情、截图和下载包交给这里；这里负责断点、去重、证据落盘、
ZIP 安全门禁和工作区交接。

与 :mod:`workspace_state` 的约定只有以下公开接口：

* ``BusinessLayout.from_root(root)``，以及 ``batch_dir``、
  ``pending_case_dir``、``work_case_dir`` 三个路径方法；
* ``upsert_case(layout, project_no, **fields)`` 用于更新 CaseWaterlineV1。

若模块在独立测试中未加载 ``workspace_state``，会使用同形状的最小只读布局适配器；
真实 CLI 仍应先通过 ``workspace_state.resolve_workspace`` 验证工作根。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from .office_package_guard import is_safe_docx_package
else:
    from office_package_guard import is_safe_docx_package

if __package__:
    from . import local_validity
else:
    import local_validity

try:  # 包导入与直接加载脚本两种方式都要可用。
    from .workspace_state import BusinessLayout, load_waterline, upsert_case
except (ImportError, ModuleNotFoundError):  # pragma: no cover - 由独立加载测试覆盖行为
    try:
        from workspace_state import (  # type: ignore[no-redef]
            BusinessLayout,
            load_waterline,
            upsert_case,
        )
    except (ImportError, ModuleNotFoundError):  # pragma: no cover
        BusinessLayout = None  # type: ignore[assignment,misc]
        load_waterline = None  # type: ignore[assignment]
        upsert_case = None  # type: ignore[assignment]


BROWSER_CAPTURE_VERSION = "BrowserCaptureV1"
SOURCE_EVIDENCE_VERSION = "SourceEvidenceV1"
DOWNLOAD_BASELINE_VERSION = "DownloadBaselineV1"
TAIL_CURSOR_VERSION = "TailCursorV1"
DOWNLOAD_WAIT_VERSION = "DownloadWaitV1"
REFRESH_REASONS = frozenset({"RECENT_UNQUALIFIED", "BAD_LOCAL", "SPECIAL_ISSUE", "USER_REQUEST"})
CAPTURE_FILE_NAME = "browser-capture.json"
EVIDENCE_FILE_NAME = "source-evidence.json"
try:
    SHANGHAI = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:  # Windows 的精简 Python 环境可能没有 IANA 时区库。
    SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download")
MAX_ZIP_ENTRIES = 500
MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_RATIO = 200.0
MAX_CAPTURE_PAGES = 10_000
MAX_CAPTURE_RECORDS = 1_000_000
DEFAULT_DOWNLOAD_WAIT_SECONDS = 30 * 60
DEFAULT_DOWNLOAD_POLL_SECONDS = 5.0
DEFAULT_DOWNLOAD_STALL_SECONDS = 5 * 60
WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
BRIGADE_SCOPES = {
    "ALL",
    "JIANGYIN",
    "YIXING",
    "LIANGXI",
    "XISHAN",
    "HUISHAN",
    "BINHU",
    "XINWU",
    "JINGKAI",
}

_SENSITIVE_KEYS = {
    "authorization",
    "auth",
    "cookie",
    "cookies",
    "csrf",
    "csrftoken",
    "password",
    "passwd",
    "secret",
    "session",
    "sessionid",
    "token",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
}
_PROJECT_KEYS = {"projectno", "projectnumber", "projectcode", "项目编号"}
_RWID_KEYS = {"rwid", "recordkey", "recordid", "任务id", "任务编号"}
_CASE_NAME_KEYS = {
    "casename",
    "projectname",
    "关联项目",
    "关联项目案卷名称",
    "案卷名称",
}
_UNIT_NAME_KEYS = {
    "unitname",
    "checkedunit",
    "companyname",
    "单位名称",
    "被检查单位",
}
_SOURCE_APPEARANCE_KEYS = {
    "casename",
    "createdat",
    "createtime",
    "creator",
    "documentname",
    "documentno",
    "documentnumber",
    "documentstatus",
    "documenttitle",
    "projectname",
    "sourceorder",
    "sourcepage",
    "sourcerow",
    "sourcerowid",
    "status",
    "创建人",
    "创建时间",
    "制作人",
    "制作时间",
    "关联项目",
    "关联项目案卷名称",
    "案卷名称",
    "文书名称",
    "文书文号",
    "文书状态",
    "文书编号",
    "文书标题",
    "生成时间",
}


class SourceIntakeError(RuntimeError):
    """可直接向用户显示的采集接收错误。"""


class _RoundUnstable(SourceIntakeError):
    pass


@dataclass(frozen=True)
class _FallbackLayout:
    root: Path

    @property
    def capture_batches(self) -> Path:
        return self.root / "工作区" / "采集批次"

    @property
    def capture_screenshots(self) -> Path:
        return self.root / "原始案卷" / "案卷目录截图"

    def batch_dir(self, batch_id: str) -> Path:
        _require_safe_component(batch_id, "批次编号")
        return self.capture_batches / batch_id

    def pending_case_dir(self, project_no: str) -> Path:
        _require_project_no(project_no)
        return self.root / "原始案卷" / "待处理案卷" / project_no

    def work_case_dir(self, project_no: str) -> Path:
        _require_project_no(project_no)
        return self.root / "工作区" / project_no


def _timestamp(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value or datetime.now(SHANGHAI)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).isoformat(timespec="seconds")


def _default_filters(
    value: datetime | str | None, explicit: dict[str, Any], *, acceptance: bool = False
) -> dict[str, Any]:
    # A user-requested single completed task is a sample, never an annual list.
    if explicit.get("selectionMode") == "LATEST_CLOSED_TASK":
        if not acceptance or explicit.get("acceptanceMode") != "SINGLE_CASE_DOWNLOAD_PROOF":
            raise SourceIntakeError("最近已结案任务仅允许隔离单案验收")
        expected = {
            "taskStatus": "已结案",
            "sortField": "结束日期",
            "sortDirection": "descending",
            "queryRoute": "#/xfjd/cpjd/cxtj/jcwcx/rcjcrw",
            "jurisdiction": "全部管辖单位(含派出所)",
            "brigadeScope": "ALL",
        }
        for key, target in expected.items():
            if explicit.get(key) != target:
                raise SourceIntakeError(f"单案验收筛选 {key} 未明确回读")
        try:
            start = datetime.strptime(str(explicit["startDate"]), "%Y-%m-%d")
            end = datetime.strptime(str(explicit["endDate"]), "%Y-%m-%d")
        except (KeyError, ValueError) as error:
            raise SourceIntakeError("单案验收必须记录实际日期范围") from error
        if start > end:
            raise SourceIntakeError("单案验收日期范围倒置")
        if explicit.get("documentType") or explicit.get("dateShortcut"):
            raise SourceIntakeError("任务样本不能伪称法律文书筛选或日期快捷项")
        return {**explicit, "timezone": "Asia/Shanghai"}
    current = datetime.fromisoformat(_timestamp(value))
    if explicit.get("selectionMode") == "ANNUAL_CASE_BASELINE":
        if __package__:
            from .source_coverage import TASK_CATEGORIES
        else:
            from source_coverage import TASK_CATEGORIES
        if explicit.get("taskCategory") not in TASK_CATEGORIES:
            raise SourceIntakeError("年度清单必须声明页面实际任务类别")
        _require_safe_component(str(explicit.get("baselineId") or ""), "年度基线组编号")
        for key, target in {
            "year": current.year,
            "dateShortcut": "本年",
            "sourceListKind": "CASE_TASK",
            "taskStatus": "ALL",
            "lawEnforcementUnit": "ALL",
            "jurisdiction": "全部管辖单位(含派出所)",
            "brigadeScope": "ALL",
        }.items():
            if explicit.get(key) != target:
                raise SourceIntakeError(f"年度案卷基线 {key} 必须经页面回读为 {target}")
        for key in ("dateFieldLabel", "queryRoute", "queryEvidencePath"):
            if not isinstance(explicit.get(key), str) or not explicit[key].strip():
                raise SourceIntakeError(f"年度案卷基线必须记录实际 {key}")
        if not explicit["queryRoute"].startswith("#/xfjd/") or "flwscx" in explicit["queryRoute"]:
            raise SourceIntakeError("年度基线必须使用案卷或任务总清单，不能以文书查询替代")
        if explicit.get("documentType") or explicit.get("brigadeCode"):
            raise SourceIntakeError("年度案卷基线不能限定文书类型或单个大队")
        try:
            start = datetime.strptime(str(explicit["startDate"]), "%Y-%m-%d").date()
            end = datetime.strptime(str(explicit["endDate"]), "%Y-%m-%d").date()
        except (KeyError, ValueError) as error:
            raise SourceIntakeError("年度基线必须记录本年快捷项实际起止日期") from error
        if (
            start.isoformat() != f"{current.year}-01-01"
            or not current.date() <= end <= current.date().replace(month=12, day=31)
        ):
            raise SourceIntakeError("年度基线的本年日期范围不完整")
        return {**explicit, "timezone": "Asia/Shanghai"}
    if explicit.get("selectionMode") == "RECENT_DOCUMENT_ACTIVITY":
        try:
            start = datetime.strptime(str(explicit["startDate"]), "%Y-%m-%d").date()
            end = datetime.strptime(str(explicit["endDate"]), "%Y-%m-%d").date()
        except (KeyError, ValueError) as error:
            raise SourceIntakeError("增量扫描必须提供实际起止日期") from error
        if start > end or end > current.date():
            raise SourceIntakeError("增量扫描日期倒置或超过执行日")
        for key, target in {
            "documentType": "ALL",
            "dateFieldLabel": "创建日期",
            "jurisdiction": "全部管辖单位(含派出所)",
            "brigadeScope": "ALL",
        }.items():
            if explicit.get(key) != target:
                raise SourceIntakeError(f"增量扫描 {key} 未回读为规定值")
        if (
            not isinstance(explicit.get("queryEvidencePath"), str)
            or not explicit["queryEvidencePath"].strip()
        ):
            raise SourceIntakeError("增量扫描必须记录实际筛选证据 queryEvidencePath")
        if explicit.get("dateShortcut") or explicit.get("documentTypePage") or "year" in explicit:
            raise SourceIntakeError("增量窗口不能伪称本年或精确文书类型查询")
        return {**explicit, "timezone": "Asia/Shanghai"}
    notice_query = explicit.get("selectionMode") == "ANNUAL_RECTIFICATION_NOTICE"
    if notice_query:
        if explicit.get("documentType") != "责令限期改正通知书":
            raise SourceIntakeError("通知书查询必须明确回读精确文书类型")
        if type(explicit.get("documentTypePage")) is not int or explicit["documentTypePage"] < 1:
            raise SourceIntakeError("通知书查询必须记录实际文书选择器页码")
        for key in ("dateFieldLabel", "queryEvidencePath"):
            if not isinstance(explicit.get(key), str) or not explicit[key].strip():
                raise SourceIntakeError(f"通知书查询必须记录 {key}")
    requested_year = explicit.get("year", current.year)
    if requested_year != current.year:
        raise SourceIntakeError("当前采集只允许上海时间本年范围")
    defaults: dict[str, Any] = {
        "year": requested_year,
        "startDate": f"{requested_year}-01-01",
        "endDate": f"{requested_year}-12-31",
        "dateShortcut": "本年",
        "jurisdiction": "全部管辖单位(含派出所)",
        "brigadeScope": "ALL",
        "documentType": "责令限期改正通知书" if notice_query else "消防产品监督检查记录",
        "documentTypePage": explicit["documentTypePage"] if notice_query else 2,
        "timezone": "Asia/Shanghai",
    }
    defaults.update(explicit)
    legacy_brigade = explicit.get("brigade")
    scope_value = str(defaults.get("brigadeScope") or "").strip().upper()
    brigade_code = str(explicit.get("brigadeCode") or "").strip().upper()
    if legacy_brigade not in (None, "", "all", "ALL"):
        brigade_code = str(legacy_brigade).strip().upper()
        scope_value = "SINGLE"
    elif brigade_code and "brigadeScope" not in explicit:
        scope_value = "SINGLE"
    elif scope_value in BRIGADE_SCOPES - {"ALL"}:
        brigade_code = scope_value
        scope_value = "SINGLE"
    if scope_value not in {"ALL", "SINGLE"}:
        raise SourceIntakeError("大队范围只能是 ALL 或 SINGLE")
    if scope_value == "SINGLE" and brigade_code not in BRIGADE_SCOPES - {"ALL"}:
        raise SourceIntakeError("单大队筛选必须提供 8 个标准大队代码之一")
    if scope_value == "ALL" and brigade_code:
        raise SourceIntakeError("全部大队筛选不得同时指定 brigadeCode")
    defaults["brigadeScope"] = scope_value
    defaults.pop("brigade", None)
    if brigade_code:
        defaults["brigadeCode"] = brigade_code
    else:
        defaults.pop("brigadeCode", None)
    fixed_contract = {
        "year": current.year,
        "startDate": f"{current.year}-01-01",
        "endDate": f"{current.year}-12-31",
        "dateShortcut": "本年",
        "jurisdiction": "全部管辖单位(含派出所)",
        "documentType": "责令限期改正通知书" if notice_query else "消防产品监督检查记录",
        "documentTypePage": explicit["documentTypePage"] if notice_query else 2,
        "timezone": "Asia/Shanghai",
    }
    for key, expected in fixed_contract.items():
        if defaults.get(key) != expected:
            raise SourceIntakeError(f"采集筛选 {key} 必须固定为 {expected}")
    return defaults


def plan_tail_first_cursor(
    total_count: int,
    page_size: int,
    page_number: int,
    visible_row_count: int,
    row_number: int | None = None,
) -> dict[str, Any]:
    """按页面已回读的行数计算尾页优先采集游标，绝不只相信历史页码。"""

    values = {
        "列表总数": total_count,
        "每页条数": page_size,
        "当前页码": page_number,
        "当前页可见行数": visible_row_count,
    }
    for label, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SourceIntakeError(f"{label}必须是正整数")
    total_pages = (total_count + page_size - 1) // page_size
    if page_number > total_pages:
        raise SourceIntakeError("当前页码超出页面报告的总页数")
    if visible_row_count > page_size:
        raise SourceIntakeError("当前页可见行数超过每页条数")
    expected_rows = total_count - (page_number - 1) * page_size
    expected_rows = min(page_size, expected_rows)
    if visible_row_count != expected_rows:
        raise SourceIntakeError(
            f"当前页可见行数与页面水位不一致：{visible_row_count}/{expected_rows}"
        )
    selected_row = visible_row_count if row_number is None else row_number
    if (
        not isinstance(selected_row, int)
        or isinstance(selected_row, bool)
        or not 1 <= selected_row <= visible_row_count
    ):
        raise SourceIntakeError("选中行号必须位于当前页可见行范围内")
    source_index = (page_number - 1) * page_size + selected_row
    tail_ordinal = total_count - source_index + 1
    if selected_row > 1:
        next_cursor: dict[str, Any] | None = {
            "pageNumber": page_number,
            "rowNumber": selected_row - 1,
            "rowStrategy": "EXACT_ROW",
            "requiresVisibleRowReadback": False,
        }
    elif page_number > 1:
        next_cursor = {
            "pageNumber": page_number - 1,
            "rowNumber": None,
            "rowStrategy": "LAST_VISIBLE_ROW_AFTER_REFRESH",
            "requiresVisibleRowReadback": True,
        }
    else:
        next_cursor = None
    return {
        "schemaVersion": TAIL_CURSOR_VERSION,
        "order": "TAIL_FIRST",
        "totalCount": total_count,
        "pageSize": page_size,
        "totalPages": total_pages,
        "current": {
            "pageNumber": page_number,
            "visibleRowCount": visible_row_count,
            "rowNumber": selected_row,
            "sourceIndex": source_index,
            "tailOrdinal": tail_ordinal,
        },
        "next": next_cursor,
    }


def _require_safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not SAFE_COMPONENT.fullmatch(value):
        raise SourceIntakeError(f"{label}不合法")
    return value


def _require_project_no(value: str) -> str:
    if not isinstance(value, str) or not PROJECT_NO.fullmatch(value):
        raise SourceIntakeError("项目编号不合法")
    return value


def _require_rwid(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160 or any(char in result for char in "\\/\0\r\n"):
        raise SourceIntakeError("RWID 不合法")
    return result


def _layout(value: Any) -> Any:
    if hasattr(value, "batch_dir") and hasattr(value, "pending_case_dir"):
        return value
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise SourceIntakeError("工作根必须是绝对路径")
    root = Path(os.path.abspath(root))
    if BusinessLayout is not None:
        try:
            return BusinessLayout.from_root(root)
        except Exception as error:
            raise SourceIntakeError(f"工作根不可用：{error}") from error
    return _FallbackLayout(root)


def _capture_path(layout: Any, batch_id: str) -> Path:
    return layout.batch_dir(_require_safe_component(batch_id, "批次编号")) / CAPTURE_FILE_NAME


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_exclusive(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                # Windows 的同卷 rename 不覆盖已存在目标，且适用于不支持硬链接的业务盘。
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError as error:
            if _read_json(path, "不可覆盖 JSON") != value:
                raise SourceIntakeError(f"不可覆盖文件已存在：{path.name}") from error
    except SourceIntakeError:
        raise
    except OSError as error:
        raise SourceIntakeError(f"无法安全发布不可覆盖文件：{path.name}") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceIntakeError(f"{label}无法读取：{error}") from error
    if not isinstance(value, dict):
        raise SourceIntakeError(f"{label}必须是 JSON 对象")
    return value


def _json_input(value: dict[str, Any] | str | Path | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return _read_json(Path(value), label)


def capture_download_baseline(
    download_dir: str | Path,
    *,
    observed_at: datetime | str | None = None,
    include_sha256: bool = False,
) -> dict[str, Any]:
    """记录下载前基线；只保存文件名和不可执行的文件属性。"""

    directory = Path(download_dir)
    if not directory.is_dir():
        raise SourceIntakeError("下载基线目录不存在")
    files = []
    for item in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
        lower_name = item.name.casefold()
        if not item.is_file() or not (
            item.suffix.casefold() == ".zip" or lower_name.endswith(PARTIAL_SUFFIXES)
        ):
            continue
        stat_value = item.stat()
        record: dict[str, Any] = {
            "name": item.name,
            "sizeBytes": stat_value.st_size,
            "mtimeNs": stat_value.st_mtime_ns,
        }
        if include_sha256:
            record["sha256"] = _sha256(item)
        files.append(record)
    value = {
        "schemaVersion": DOWNLOAD_BASELINE_VERSION,
        "observedAt": _timestamp(observed_at),
        "files": files,
    }
    value["fingerprint"] = _fingerprint(files)
    return value


def _normalize_download_baseline(
    value: dict[str, Any] | list[dict[str, Any]] | str | Path | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        raw: dict[str, Any] = {"files": value}
    elif isinstance(value, dict):
        raw = value
    else:
        try:
            loaded = json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SourceIntakeError(f"下载基线无法读取：{error}") from error
        if isinstance(loaded, list):
            raw = {"files": loaded}
        elif isinstance(loaded, dict):
            raw = loaded
        else:
            raise SourceIntakeError("下载基线必须是 JSON 对象或数组")
    files = raw.get("files")
    if not isinstance(files, list):
        raise SourceIntakeError("下载基线缺少 files 数组")
    normalized_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise SourceIntakeError("下载基线文件项必须是对象")
        name = str(item.get("name") or "")
        if not name or Path(name).name != name or name.casefold() in seen:
            raise SourceIntakeError("下载基线文件名不合法或重复")
        lower_name = name.casefold()
        if not (Path(name).suffix.casefold() == ".zip" or lower_name.endswith(PARTIAL_SUFFIXES)):
            continue
        size = item.get("sizeBytes", item.get("size"))
        modified = item.get("mtimeNs", item.get("mtime"))
        if not isinstance(size, int) or size < 0 or not isinstance(modified, int) or modified < 0:
            raise SourceIntakeError("下载基线文件属性不合法")
        record = {"name": name, "sizeBytes": size, "mtimeNs": modified}
        sha = item.get("sha256", item.get("hash"))
        if sha is not None:
            if not isinstance(sha, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", sha):
                raise SourceIntakeError("下载基线 SHA-256 不合法")
            record["sha256"] = sha
        normalized_files.append(record)
        seen.add(name.casefold())
    normalized_files.sort(key=lambda item: item["name"].casefold())
    normalized: dict[str, Any] = {
        "schemaVersion": DOWNLOAD_BASELINE_VERSION,
        "observedAt": raw.get("observedAt"),
        "files": normalized_files,
        "fingerprint": _fingerprint(normalized_files),
    }
    for key in ("batchId", "rwid", "projectNo", "relativePath", "consumedAt"):
        if key in raw:
            normalized[key] = raw[key]
    return normalized


def _download_baseline_dir(layout: Any, batch_id: str, record_key: str) -> Path:
    record_hash = hashlib.sha256(record_key.encode("utf-8")).hexdigest()[:20]
    return layout.batch_dir(batch_id) / "下载基线" / f"rwid-{record_hash}"


def record_download_baseline(
    workspace: Any,
    batch_id: str,
    rwid: str,
    download_dir: str | Path,
    *,
    observed_at: datetime | str | None = None,
    include_sha256: bool = False,
) -> dict[str, Any]:
    """在每个案卷点击打包前保存一次不可覆盖的下载基线。"""

    layout = _layout(workspace)
    _, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("下载基线 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    collecting_annual = _material_collection_active(layout, state, record_key, record)
    if record.get("skippedAsUnchanged") and not collecting_annual:
        raise SourceIntakeError("详情项目编号已在水位中完成并通过完成核验，无需下载案卷包")
    detail = record.get("collectionDetail") if collecting_annual else record.get("detail")
    if not detail:
        raise SourceIntakeError("必须先进入详情读取项目编号并保存完整截图，才能建立下载基线")
    project_no = _require_project_no(
        str(record.get("projectNo") or (record.get("detail") or {}).get("projectNo") or "")
        .strip()
        .upper()
    )
    value = capture_download_baseline(
        download_dir,
        observed_at=observed_at,
        include_sha256=include_sha256,
    )
    observed = datetime.fromisoformat(value["observedAt"])
    name = observed.strftime("%Y%m%d-%H%M%S-%f") + "-" + value["fingerprint"][-12:] + ".json"
    target = _download_baseline_dir(layout, batch_id, record_key) / name
    value["batchId"] = batch_id
    value["rwid"] = record_key
    value["projectNo"] = project_no
    value["consumedAt"] = None
    value["relativePath"] = target.resolve().relative_to(Path(layout.root).resolve()).as_posix()
    _write_json_exclusive(target, value)
    return value


def _has_changed_since_download_baseline(
    item: Path,
    baseline_by_name: dict[str, dict[str, Any]],
) -> bool:
    """仅用本案基线判断文件是否属于当前点击，不按文件名猜测归属。"""

    prior = baseline_by_name.get(item.name.casefold())
    if prior is None:
        return True
    stat_value = item.stat()
    unchanged = (prior["sizeBytes"], prior["mtimeNs"]) == (
        stat_value.st_size,
        stat_value.st_mtime_ns,
    )
    if not unchanged and prior.get("sha256"):
        unchanged = prior["sha256"] == _sha256(item)
    return not unchanged


def _download_artifacts_since_baseline(
    download_dir: Path,
    baseline: dict[str, Any],
) -> tuple[list[Path], list[Path]]:
    baseline_by_name = {item["name"].casefold(): item for item in baseline["files"]}
    zip_candidates: list[Path] = []
    partial_candidates: list[Path] = []
    for item in sorted(download_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not item.is_file():
            continue
        lower_name = item.name.casefold()
        is_zip = item.suffix.casefold() == ".zip"
        is_partial = lower_name.endswith(PARTIAL_SUFFIXES)
        if not (is_zip or is_partial) or not _has_changed_since_download_baseline(
            item, baseline_by_name
        ):
            continue
        if is_zip:
            zip_candidates.append(item)
        else:
            partial_candidates.append(item)
    return zip_candidates, partial_candidates


def _download_artifact_snapshot(item: Path) -> dict[str, Any]:
    stat_value = item.stat()
    return {
        "name": item.name,
        "sizeBytes": stat_value.st_size,
        "mtimeNs": stat_value.st_mtime_ns,
    }


def wait_for_download_candidate(
    download_dir: str | Path,
    *,
    download_baseline: dict[str, Any] | list[dict[str, Any]] | str | Path,
    timeout_seconds: float = DEFAULT_DOWNLOAD_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_DOWNLOAD_POLL_SECONDS,
    stalled_after_seconds: float = DEFAULT_DOWNLOAD_STALL_SECONDS,
    stability_checks: int = 3,
    stability_interval: float = 1.0,
    sleep_fn: Any = time.sleep,
    monotonic_fn: Any = time.monotonic,
) -> dict[str, Any]:
    """等待异步打包下载真正完成；临时扩展名和变化中的文件不会被当作案卷包。"""

    directory = Path(download_dir).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise SourceIntakeError("下载目录不存在")
    baseline = _normalize_download_baseline(download_baseline)
    if baseline is None:
        raise SourceIntakeError("等待下载必须提供本案下载前基线")
    for label, value in {
        "下载等待时长": timeout_seconds,
        "下载轮询间隔": poll_seconds,
        "下载停滞阈值": stalled_after_seconds,
        "下载稳定间隔": stability_interval,
    }.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise SourceIntakeError(f"{label}必须是非负数")
    if stability_checks < 3:
        raise SourceIntakeError("下载稳定性至少需要 3 次检查")

    started_at = monotonic_fn()
    last_partial_signature: tuple[tuple[str, int, int], ...] | None = None
    last_progress_at = started_at
    while True:
        zip_candidates, partial_candidates = _download_artifacts_since_baseline(directory, baseline)
        now = monotonic_fn()
        partial_snapshot = [_download_artifact_snapshot(item) for item in partial_candidates]
        zip_snapshot = [_download_artifact_snapshot(item) for item in zip_candidates]
        common = {
            "schemaVersion": DOWNLOAD_WAIT_VERSION,
            "observedAt": _timestamp(),
            "elapsedSeconds": round(max(0.0, now - started_at), 3),
            "partialCandidates": partial_snapshot,
            "zipCandidates": zip_snapshot,
        }
        if (
            len(zip_candidates) > 1
            or len(partial_candidates) > 1
            or (zip_candidates and partial_candidates)
        ):
            return {
                **common,
                "status": "AMBIGUOUS",
                "reason": "下载目录相对本案基线出现多个候选或同时存在未完成下载",
            }
        if len(zip_candidates) == 1:
            candidate = zip_candidates[0]
            try:
                source, selection, inspection = _resolve_zip(
                    candidate,
                    baseline,
                    stability_checks=stability_checks,
                    stability_interval=stability_interval,
                    sleep_fn=sleep_fn,
                )
            except SourceIntakeError as error:
                return {**common, "status": "INVALID", "reason": str(error)}
            return {
                **common,
                "status": "READY",
                "downloadPath": str(source),
                "originalSuggestedName": source.name,
                "downloadSelection": selection,
                "zipInspection": {
                    "entryCount": inspection["entryCount"],
                    "fileCount": inspection["fileCount"],
                    "totalSizeBytes": inspection["totalSizeBytes"],
                },
            }
        partial_signature = tuple(
            (item["name"], item["sizeBytes"], item["mtimeNs"]) for item in partial_snapshot
        )
        if partial_signature != last_partial_signature:
            last_partial_signature = partial_signature
            last_progress_at = now
        if partial_candidates and now - last_progress_at >= stalled_after_seconds:
            return {
                **common,
                "status": "STALLED",
                "reason": "临时下载文件在停滞阈值内未继续变化",
            }
        if now - started_at >= timeout_seconds:
            return {
                **common,
                "status": "WAITING",
                "reason": "尚未出现相对本案基线唯一、完整且稳定的 ZIP",
            }
        sleep_fn(poll_seconds)


def _record_download_delivery(
    layout: Any,
    capture_path: Path,
    state: dict[str, Any],
    record_key: str,
    project_no: str,
    result: dict[str, Any],
) -> None:
    """把异步交付观察写入当前案卷断点，不把绝对下载路径写入业务证据。"""

    observed_at = str(result.get("observedAt") or _timestamp())
    delivery = {
        "schemaVersion": DOWNLOAD_WAIT_VERSION,
        "status": str(result["status"]),
        "observedAt": observed_at,
        "elapsedSeconds": result.get("elapsedSeconds"),
        "reason": result.get("reason"),
        "partialCandidates": list(result.get("partialCandidates") or []),
        "zipCandidates": list(result.get("zipCandidates") or []),
    }
    if delivery["status"] == "READY" and result.get("downloadPath"):
        # 包一到即登记“已收到”事实（摘要/大小/建议文件名，不含绝对路径），
        # 与后续接件通过、材料齐全、文书处理、云同步分层，互不替代。
        received_path = Path(str(result["downloadPath"]))
        delivery["received"] = {
            "sha256": _sha256(received_path),
            "sizeBytes": received_path.stat().st_size,
            "originalSuggestedName": result.get("originalSuggestedName"),
            "receivedAt": observed_at,
        }
    record = state["records"][record_key]
    record["downloadDelivery"] = delivery
    state["updatedAt"] = observed_at
    _write_json(capture_path, state)

    if _is_acceptance_sample(state):
        return
    source_status = {
        "READY": "PACKAGE_READY_TO_ATTACH",
        "WAITING": "PACKAGE_WAITING",
        "STALLED": "PACKAGE_STALLED",
        "AMBIGUOUS": "PACKAGE_AMBIGUOUS",
        "INVALID": "PACKAGE_INVALID",
    }.get(delivery["status"])
    if source_status is None:
        return
    error_summary = (
        str(delivery["reason"])
        if delivery["status"] in {"STALLED", "AMBIGUOUS", "INVALID"}
        else None
    )
    waterline_state = "DETAIL_CAPTURED" if record.get("detail") else "DISCOVERED"
    _maybe_waterline(
        layout,
        project_no,
        state=waterline_state,
        source={
            "status": source_status,
            "batchId": state["batchId"],
            "rwid": record_key,
            "downloadDelivery": delivery,
        },
        errorSummary=error_summary,
    )


def _validate_bound_download_baseline(
    layout: Any,
    batch_id: str,
    record_key: str,
    project_no: str,
    baseline_input: dict[str, Any] | list[dict[str, Any]] | str | Path | None,
) -> tuple[dict[str, Any], Path]:
    """只接受由当前批次、当前案卷生成且仍可核验的本地基线文件。"""

    if baseline_input is None:
        raise SourceIntakeError("必须提供本案点击打包前生成的下载基线")
    baseline = _normalize_download_baseline(baseline_input)
    if baseline is None:
        raise SourceIntakeError("必须提供本案点击打包前生成的下载基线")
    if baseline.get("batchId") != batch_id:
        raise SourceIntakeError("下载基线不属于当前采集批次")
    if baseline.get("rwid") != record_key:
        raise SourceIntakeError("下载基线不属于当前 RWID")
    if baseline.get("projectNo") != project_no:
        raise SourceIntakeError("下载基线不属于当前项目编号")
    if baseline.get("consumedAt") is not None:
        raise SourceIntakeError("下载基线状态异常，拒绝使用")
    baseline_path = _workspace_relative_path(layout, baseline.get("relativePath"), "下载基线")
    expected_parent = _download_baseline_dir(layout, batch_id, record_key).resolve()
    if baseline_path.parent != expected_parent or baseline_path.suffix.casefold() != ".json":
        raise SourceIntakeError("下载基线路径与当前案卷不匹配")
    persisted = _normalize_download_baseline(_read_json(baseline_path, "下载基线"))
    if persisted != baseline:
        raise SourceIntakeError("下载基线文件与传入内容不一致")
    return baseline, baseline_path


def _consume_download_baseline(
    baseline: dict[str, Any],
    baseline_path: Path,
    package_sha256: str,
    *,
    consumed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """写入不可覆盖的消费回执；相同案卷包可幂等重放，不得改绑其他包。"""

    receipt_path = baseline_path.with_name(baseline_path.name + ".consumed.json")
    receipt = {
        "schemaVersion": "DownloadBaselineConsumptionV1",
        "batchId": baseline["batchId"],
        "rwid": baseline["rwid"],
        "projectNo": baseline["projectNo"],
        "baselineRelativePath": baseline["relativePath"],
        "baselineFingerprint": baseline["fingerprint"],
        "packageSha256": package_sha256,
        "consumedAt": _timestamp(consumed_at),
    }
    if receipt_path.exists():
        existing = _read_json(receipt_path, "下载基线消费回执")
        if any(
            existing.get(key) != receipt.get(key)
            for key in (
                "schemaVersion",
                "batchId",
                "rwid",
                "projectNo",
                "baselineRelativePath",
                "baselineFingerprint",
                "packageSha256",
            )
        ):
            raise SourceIntakeError("下载基线已被其他案卷包消费") from None
        return existing
    try:
        _write_json_exclusive(receipt_path, receipt)
    except SourceIntakeError:
        if not receipt_path.is_file():
            raise
        existing = _read_json(receipt_path, "下载基线消费回执")
        if any(
            existing.get(key) != receipt.get(key)
            for key in (
                "schemaVersion",
                "batchId",
                "rwid",
                "projectNo",
                "baselineRelativePath",
                "baselineFingerprint",
                "packageSha256",
            )
        ):
            raise SourceIntakeError("下载基线已被其他案卷包消费") from None
        return existing
    return _read_json(receipt_path, "下载基线消费回执")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sanitize_source_url(value: str | None) -> str | None:
    """删除 runId 和认证型查询参数，只保留可追溯的页面地址。"""

    if not value:
        return None
    try:
        parts = urlsplit(str(value).strip())
    except ValueError as error:
        raise SourceIntakeError("来源 URL 不合法") from error
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise SourceIntakeError("来源 URL 必须是 HTTP(S) 绝对地址")
    if parts.username or parts.password:
        raise SourceIntakeError("来源 URL 不得包含账号或密码")

    def filtered_query(query: str) -> str:
        safe_query = []
        for key, item in parse_qsl(query, keep_blank_values=True):
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized == "runid" or _is_sensitive_key(normalized, key):
                continue
            safe_query.append((key, item))
        return urlencode(safe_query)

    fragment = parts.fragment
    safe_fragment = ""
    if fragment.startswith("/"):
        route, separator, route_query = fragment.partition("?")
        if "\\" in route or "\0" in route:
            raise SourceIntakeError("来源 URL 的页面路由不合法")
        cleaned_route_query = filtered_query(route_query) if separator else ""
        safe_fragment = route + ("?" + cleaned_route_query if cleaned_route_query else "")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc,
            parts.path or "/",
            filtered_query(parts.query),
            safe_fragment,
        )
    )


def _source_url_rwid(value: str | None) -> str | None:
    """从已脱敏详情地址读取 RWID；不读取浏览器会话状态。"""

    if not value:
        return None
    parts = urlsplit(value)
    queries = [parts.query]
    if parts.fragment:
        _route, separator, fragment_query = parts.fragment.partition("?")
        if separator:
            queries.append(fragment_query)
    values = {
        str(item).strip()
        for query in queries
        for key, item in parse_qsl(query, keep_blank_values=True)
        if _normalized_key(key) == "rwid" and str(item).strip()
    }
    if len(values) > 1:
        raise SourceIntakeError("来源详情 URL 含多个不同 RWID")
    return next(iter(values), None)


def _clean_evidence(value: Any, *, key_hint: str = "") -> Any:
    """建立证据白名单边界：认证字段丢弃，URL 去会话参数。"""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized == "runid" or _is_sensitive_key(normalized, key):
                continue
            result[key] = _clean_evidence(item, key_hint=key)
        return result
    if isinstance(value, list):
        return [_clean_evidence(item, key_hint=key_hint) for item in value]
    if isinstance(value, tuple):
        return [_clean_evidence(item, key_hint=key_hint) for item in value]
    if isinstance(value, Path):
        return value.name
    normalized_hint = re.sub(r"[^a-z0-9]", "", key_hint.casefold())
    if isinstance(value, str) and (
        "url" in normalized_hint or normalized_hint in {"origin", "href", "link"}
    ):
        return sanitize_source_url(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _is_sensitive_key(normalized: str, raw_key: str = "") -> bool:
    if normalized in _SENSITIVE_KEYS:
        return True
    english_sensitive = any(
        marker in normalized
        for marker in (
            "authheader",
            "authorization",
            "clientsecret",
            "cookie",
            "csrf",
            "jwt",
            "password",
            "passwd",
            "sessionid",
            "sessionstorage",
        )
    ) or normalized.endswith("token")
    chinese_sensitive = any(
        marker in raw_key for marker in ("密码", "口令", "会话", "令牌", "凭据", "授权头")
    )
    return english_sensitive or chinese_sensitive


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _find_first(value: Any, wanted: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if (normalized in wanted or str(key).strip() in wanted) and item not in (None, ""):
                return item
        for item in value.values():
            found = _find_first(item, wanted)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first(item, wanted)
            if found not in (None, ""):
                return found
    return None


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_source_appearance_key(value: Any) -> bool:
    raw = str(value).strip()
    return _normalized_key(raw) in _SOURCE_APPEARANCE_KEYS or raw in _SOURCE_APPEARANCE_KEYS


def _sorted_unique_appearances(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {_canonical(value): value for value in values if value}
    return [unique[key] for key in sorted(unique)]


def document_fingerprint(value: dict[str, Any]) -> str:
    # Page position and observation time are not source document changes.
    def stable(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                k: stable(v)
                for k, v in item.items()
                if _normalized_key(k)
                not in {
                    "sourcepage",
                    "sourcerow",
                    "sourceorder",
                    "pagenumber",
                    "observedat",
                    "capturedat",
                }
            }
        return [stable(v) for v in item] if isinstance(item, list) else item

    return _fingerprint(stable(value))


def _normalize_record(item: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_evidence(item)
    rwid = _require_rwid(_find_first(clean, _RWID_KEYS))
    project = _find_first(clean, _PROJECT_KEYS)
    fields: dict[str, Any] = {}
    appearance: dict[str, Any] = {}
    for key, value in clean.items():
        target = appearance if _is_source_appearance_key(key) else fields
        target[key] = value
    normalized: dict[str, Any] = {"rwid": rwid, "fields": fields}
    if appearance:
        normalized["sourceAppearances"] = [appearance]
    if project not in (None, ""):
        normalized["projectNo"] = _require_project_no(str(project).strip().upper())
    return normalized


def _record_core(record: dict[str, Any]) -> dict[str, Any]:
    fields = dict(record.get("fields") or {})
    for key in list(fields):
        normalized = _normalized_key(key)
        raw = str(key).strip()
        if (
            normalized in _RWID_KEYS
            or raw in _RWID_KEYS
            or normalized in _PROJECT_KEYS
            or raw in _PROJECT_KEYS
            or "url" in normalized
        ):
            fields.pop(key, None)
    return {"projectNo": record.get("projectNo"), "fields": fields}


def _merge_rwid_records(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any] | None:
    if existing.get("rwid") != incoming.get("rwid"):
        return None
    existing_project = existing.get("projectNo")
    incoming_project = incoming.get("projectNo")
    if existing_project and incoming_project and existing_project != incoming_project:
        return None
    existing_core = _record_core(existing)
    incoming_core = _record_core(incoming)
    existing_core["projectNo"] = existing_project or incoming_project
    incoming_core["projectNo"] = existing_project or incoming_project
    if existing_core != incoming_core:
        return None
    merged: dict[str, Any] = {
        "rwid": existing["rwid"],
        "fields": dict(existing.get("fields") or incoming.get("fields") or {}),
    }
    project_no = existing_project or incoming_project
    if project_no:
        merged["projectNo"] = project_no
    appearances = _sorted_unique_appearances(
        list(existing.get("sourceAppearances") or [])
        + list(incoming.get("sourceAppearances") or [])
    )
    if appearances:
        merged["sourceAppearances"] = appearances
    return merged


def _record_core_equivalence(record: dict[str, Any]) -> str:
    return _fingerprint(_record_core(record))


def _record_equivalence(record: dict[str, Any]) -> str:
    return _fingerprint(
        {
            **_record_core(record),
            "sourceAppearances": _sorted_unique_appearances(
                list(record.get("sourceAppearances") or [])
            ),
        }
    )


def _positive_int(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _appearance_tail_position(value: dict[str, Any]) -> int:
    source_order = _positive_int(_find_first(value, {"sourceorder"}))
    if source_order is not None:
        return source_order
    page = _positive_int(_find_first(value, {"sourcepage"})) or 0
    row = _positive_int(_find_first(value, {"sourcerow"})) or 0
    return page * (MAX_CAPTURE_RECORDS + 1) + row


def _annotate_inspection_stages(
    records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[int, str, dict[str, Any] | None]]] = {}
    labels: dict[str, str] = {}
    for rwid, record in records.items():
        appearances = list(record.get("sourceAppearances") or [])
        case_names = [
            value
            for value in (
                *(_find_first(item, _CASE_NAME_KEYS) for item in appearances),
                _find_first(record.get("fields") or {}, _CASE_NAME_KEYS),
            )
            if value not in (None, "")
        ]
        normalized_names = {
            value for item in case_names if (value := _normalized_case_name(item)) is not None
        }
        label = str(case_names[0]).strip() if case_names else rwid
        group_key = next(iter(normalized_names)) if len(normalized_names) == 1 else rwid
        labels.setdefault(group_key, label)
        if appearances:
            for appearance in appearances:
                grouped.setdefault(group_key, []).append(
                    (_appearance_tail_position(appearance), rwid, appearance)
                )
        else:
            grouped.setdefault(group_key, []).append((0, rwid, None))

    stages_by_rwid: dict[str, list[str]] = {rwid: [] for rwid in records}
    appearances_by_rwid: dict[str, list[dict[str, Any]]] = {rwid: [] for rwid in records}
    anomalies: list[dict[str, Any]] = []
    for group_key, entries in grouped.items():
        ordered = sorted(entries, key=lambda item: (-item[0], item[1], _canonical(item[2])))
        for index, (_position, rwid, appearance) in enumerate(ordered):
            stage = "INITIAL" if index == 0 else "RECHECK" if index == 1 else "ANOMALY"
            stages_by_rwid[rwid].append(stage)
            if appearance is not None:
                appearances_by_rwid[rwid].append({**appearance, "inspectionStage": stage})
        if len(ordered) > 2:
            anomaly = {
                "type": "INSPECTION_RECORD_COUNT_EXCEEDED",
                "caseName": labels[group_key],
                "recordCount": len(ordered),
                "expectedMaximum": 2,
                "rwids": sorted({rwid for _position, rwid, _appearance in ordered}),
                "blocking": False,
            }
            anomalies.append(anomaly)
            for _position, rwid, _appearance in ordered:
                records[rwid].setdefault("anomalies", []).append(anomaly)

    for rwid, record in records.items():
        if appearances_by_rwid[rwid]:
            record["sourceAppearances"] = appearances_by_rwid[rwid]
        record["inspectionStages"] = list(dict.fromkeys(stages_by_rwid[rwid] or ["INITIAL"]))
    return sorted(anomalies, key=_canonical)


def _tail_ordered_rwids(records: dict[str, dict[str, Any]]) -> list[str]:
    def position(rwid: str) -> int:
        appearances = records[rwid].get("sourceAppearances") or []
        return max((_appearance_tail_position(item) for item in appearances), default=0)

    return sorted(records, key=lambda rwid: (-position(rwid), rwid))


def _load_capture(layout: Any, batch_id: str) -> tuple[Path, dict[str, Any]]:
    path = _capture_path(layout, batch_id)
    state = _read_json(path, "浏览器采集状态")
    if state.get("schemaVersion") != BROWSER_CAPTURE_VERSION:
        raise SourceIntakeError("浏览器采集状态版本不受支持")
    if state.get("batchId") != batch_id:
        raise SourceIntakeError("浏览器采集批次不匹配")
    recorded_root = state.get("workspaceRoot")
    if recorded_root and not layout.accepts_recorded_root(recorded_root):
        raise SourceIntakeError("采集断点属于另一工作根，拒绝跨根续跑")
    return path, state


def _material_collection_active(
    layout: Any, state: dict[str, Any], rwid: str, record: dict[str, Any]
) -> bool:
    """年度最小索引只有显式、同案绑定的采集请求才能进入材料流程。"""

    request = record.get("materialCollection")
    if request is None:
        return False
    expected = {
        "schemaVersion": "SourceMaterialCollectionV1",
        "batchId": state["batchId"],
        "rwid": rwid,
        "projectNo": record.get("projectNo"),
        "sourceRecordFingerprint": _record_equivalence(record),
    }
    if (
        state.get("filters", {}).get("selectionMode") != "ANNUAL_CASE_BASELINE"
        or not isinstance(request, dict)
        or any(request.get(key) != value for key, value in expected.items())
    ):
        raise SourceIntakeError("年度材料采集请求与当前批次、RWID 或项目绑定不一致")
    if _verified_completed_waterline(layout, str(record.get("projectNo") or "")):
        raise SourceIntakeError("该项目已完成并通过完成核验，沿用原断点，不重新采集")
    if load_waterline is not None:
        if __package__:
            from .ledger_views import describe
        else:
            from ledger_views import describe
        project = str(record.get("projectNo") or "")
        current = load_waterline(layout).get("cases", {}).get(project)
        # 2026消防产品案卷整包默认全部获取(不合格优先排序，不以分类为准入)：只要求总账仍有该项目。
        if not current:
            raise SourceIntakeError("项目已不在总账，保留采集请求并先核实身份")
    return True


def request_case_material_collection(
    workspace: Any,
    batch_id: str,
    rwid: str,
    project_no: str,
    *,
    requested_at: datetime | str | None = None,
) -> dict[str, Any]:
    """显式提升已知不合格单案；保留年度身份、跳过标记和所有原下载断点。"""

    if __package__:
        from . import source_coverage as coverage
        from .ledger_views import describe
    else:
        import source_coverage as coverage
        from ledger_views import describe
    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    key, project = _require_rwid(rwid), _require_project_no(project_no)
    record = state.get("records", {}).get(key)
    if (
        state.get("filters", {}).get("selectionMode") != coverage.ANNUAL_MODE
        or state.get("stableRounds", 0) < 2
        or state.get("listResult") != "STABLE"
        or coverage.query_evidence_issue(layout, state)
    ):
        raise SourceIntakeError("单案材料采集必须基于已核实类别的稳定年度清单")
    if not record or record.get("aliasOf") or record.get("projectNo") != project:
        raise SourceIntakeError("年度 RWID 与请求项目编号没有唯一可信绑定")
    if any(
        conflict.get("rwid") == key or key in conflict.get("rwids", [])
        for conflict in state.get("conflicts", [])
    ):
        raise SourceIntakeError("当前案卷仍有未解决的来源冲突，保留原断点")
    ledger = load_waterline(layout) if load_waterline else {"cases": {}}
    bindings = coverage.identity_bindings(coverage.formal_captures(layout), ledger)
    if bindings.get(key) != {project} or project not in ledger.get("cases", {}):
        raise SourceIntakeError("年度身份与项目总账绑定不一致，拒绝提升")
    if _verified_completed_waterline(layout, project):
        raise SourceIntakeError("该项目已完成并通过完成核验，沿用原断点，不重新采集")
    if record.get("materialCollection") is not None:
        _material_collection_active(layout, state, key, record)
        return state
    requested = _timestamp(requested_at)
    record["materialCollection"] = {
        "schemaVersion": "SourceMaterialCollectionV1",
        "batchId": batch_id,
        "rwid": key,
        "projectNo": project,
        "sourceRecordFingerprint": _record_equivalence(record),
        "requestedAt": requested,
        "initialResultAtRequest": describe(layout, project, ledger["cases"][project])["initialResult"],
    }
    state["materialRwids"] = list(dict.fromkeys([*state.get("materialRwids", []), key]))
    _refresh_progress(state)
    state["updatedAt"] = requested
    _write_json(path, state)
    return state


OBSERVED_ADMISSION_VERSION = "ObservedMaterialAdmissionV1"
_ADMITTED_PROJECT = re.compile(r"^\d{8}C2026\d{5}$")
_PRODUCT_ROOT = re.compile(r"\[产\]\S*?-(\d{8}C\d{9})-")
_PRODUCT_BLOCK = re.compile(r"检查产品信息\s*[(（](\d+)[)）]")


def _admission_basis(observation: dict[str, Any], evidence_sha: str) -> dict[str, Any]:
    """只从现场详情原文推出本案身份与产品依据；没有任何调用方可传入的“已确认”布尔。"""

    body = str(observation.get("body") or "")
    project = _require_project_no(str(observation.get("projectNo") or "").strip().upper())
    if not _ADMITTED_PROJECT.fullmatch(project):
        raise SourceIntakeError("准入只接受 2026 年消防产品监督案卷项目编号")
    safe_url = sanitize_source_url(str(observation.get("url") or ""))
    url_rwid = _source_url_rwid(safe_url)
    rwid = _require_rwid(url_rwid)
    if observation.get("rwidFromUrl") not in (None, rwid):
        raise SourceIntakeError("详情读取的 RWID 与来源 URL 不一致")
    unit = str(observation.get("unit") or "").strip()
    brigade = str(observation.get("brigade") or "").strip()
    if not unit or not brigade:
        raise SourceIntakeError("详情缺少单位名称或消防管辖，不能准入")
    if not re.search(rf"项目编号[:：]\s*{re.escape(project)}", body):
        raise SourceIntakeError("详情原文的项目编号与准入项目不一致")
    if not re.search(rf"单位名称\s+{re.escape(unit)}\s", body) or not re.search(
        rf"消防管辖\s+{re.escape(brigade)}\s", body
    ):
        raise SourceIntakeError("详情原文的单位或大队与读取身份不一致")
    roots = set(_PRODUCT_ROOT.findall(body))
    if roots != {project}:
        raise SourceIntakeError("详情文书目录没有唯一的本案 [产] 产品根，不放行")
    # 类别由本案 [产] 根与四身份证明；检查产品信息数量只是事实：匹配到才转整数(0 保留 0)，区段缺失存 None。
    block = _PRODUCT_BLOCK.search(body)
    product_items = int(block.group(1)) if block else None
    return {
        "schemaVersion": OBSERVED_ADMISSION_VERSION,
        "basis": "OBSERVED_DETAIL_PRODUCT_CASE",
        "coverageExcluded": True,
        "projectNo": project,
        "rwid": rwid,
        "unitName": unit,
        "sourceOrganization": brigade,
        "sourceUrl": safe_url,
        "evidenceSha256": evidence_sha,
        "productItems": product_items,
        "productRoot": project,
    }


def admit_observed_material_case(
    workspace: Any,
    batch_id: str,
    observation: dict[str, Any] | str | Path,
    *,
    admitted_at: datetime | str | None = None,
) -> dict[str, Any]:
    """已证单案材料采集准入：不经年度清单，只认现场详情原文证明的本案四身份与 [产] 产品根。

    记录标 coverageExcluded，不计年度清单、不改批级年度条件；同 RWID/项目/证据重复调用 0 写。
    先完整校验，再依次写总账与批次(各自原子写)，重入按同一证据对账补全，不双登记。
    """

    if __package__:
        from . import source_coverage as coverage
    else:
        import source_coverage as coverage
    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    if (
        not state.get("derivedFrom")
        or state.get("filters", {}).get("selectionMode") != coverage.ANNUAL_MODE
        or state.get("stableRounds", 0) < 2
        or state.get("listResult") != "STABLE"
        or state.get("scope") != "all"
    ):
        raise SourceIntakeError("单案材料准入只能进入既有、已核实的整包采集材料批次")
    if isinstance(observation, dict):
        evidence_sha = _fingerprint(observation)
        observed = observation
    else:
        # 摘要与参与身份校验的内容取自同一次读取的字节，文件在两步之间变化也不会失配。
        try:
            raw = Path(observation).read_bytes()
            observed = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError) as error:
            raise SourceIntakeError(f"无法读取现场详情读取文件：{error}") from error
        if not isinstance(observed, dict):
            raise SourceIntakeError("现场详情读取文件必须是 JSON 对象")
        evidence_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    basis = _admission_basis(observed, evidence_sha)
    key, project = basis["rwid"], basis["projectNo"]
    records = state.setdefault("records", {})
    existing = records.get(key)
    if existing is not None:
        prior = existing.get("materialAdmission") or {}
        if existing.get("projectNo") != project or prior.get("evidenceSha256") != evidence_sha or any(
            prior.get(k) != basis[k] for k in ("basis", "unitName", "sourceOrganization", "projectNo")
        ):
            raise SourceIntakeError("批次已有该 RWID 的不同身份或不同依据，拒绝覆盖")
    for other_key, other in records.items():
        if other_key != key and other.get("projectNo") == project:
            raise SourceIntakeError("同一项目已绑定其他 RWID，保留冲突不放行")
    if any(
        conflict.get("rwid") == key or key in conflict.get("rwids", [])
        for conflict in state.get("conflicts", [])
    ):
        raise SourceIntakeError("当前案卷仍有未解决的来源冲突，保留原断点")
    ledger = load_waterline(layout) if load_waterline else {"cases": {}}
    bindings = coverage.identity_bindings(coverage.formal_captures(layout), ledger)
    if bindings.get(key, {project}) != {project}:
        raise SourceIntakeError("该 RWID 已绑定其他项目编号，拒绝准入")
    case = ledger.get("cases", {}).get(project)
    source_part = (case or {}).get("source") or {}
    if case is not None:
        prior_ledger = source_part.get("materialAdmission") or {}
        bound = {source_part.get("rwid"), *(source_part.get("annualIdentitiesByRwid") or {})} - {None}
        if bound and bound != {key}:
            raise SourceIntakeError("总账里该项目已绑定其他 RWID，拒绝准入")
        if prior_ledger:
            # 总账已有本类准入：只有依据与身份逐项相同的原证据才允许恢复；RWID/项目相同不足以接受换证据。
            if any(prior_ledger.get(k) != basis[k] for k in (
                "basis", "projectNo", "rwid", "unitName", "sourceOrganization", "evidenceSha256", "sourceUrl",
            )):
                raise SourceIntakeError("总账已有该项目的不同准入依据或身份，保留冲突不推进")
        else:
            for ledger_key, basis_key in (("unitName", "unitName"), ("brigadeName", "sourceOrganization")):
                if case.get(ledger_key) not in (None, "", basis[basis_key]):
                    raise SourceIntakeError("总账里该项目的单位或大队与现场详情不一致，保留冲突不推进")
    moment = _timestamp(admitted_at)
    if case is None or (source_part.get("materialAdmission") or {}).get("evidenceSha256") != evidence_sha:
        _maybe_waterline(
            layout,
            project,
            unitName=basis["unitName"],
            brigadeName=basis["sourceOrganization"],
            source={
                "batchId": batch_id,
                "rwid": key,
                "projectIdentitySource": "DETAIL",
                "lastObservedAt": moment,
                "materialAdmission": {**basis, "admittedAt": moment},
            },
        )
    if existing is None:
        record = {
            "rwid": key,
            "projectNo": project,
            "fields": {"rwid": key, "unitName": basis["unitName"], "sourceOrganization": basis["sourceOrganization"]},
            "inspectionStages": [],
            "sourceAppearances": [],
            "detail": {
                "projectNo": project,
                "capturedAt": moment,
                "sourceUrl": basis["sourceUrl"],
                "fields": {"projectNo": project, "unitName": basis["unitName"]},
                "admissionEvidenceSha256": evidence_sha,
            },
            "materialAdmission": {**basis, "admittedAt": moment},
        }
        record["sourceRecordFingerprint"] = _record_equivalence(record)
        records[key] = record
        state["updatedAt"] = moment
        _write_json(path, state)
    return request_case_material_collection(layout, batch_id, key, project, requested_at=moment)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SEGMENT_SET_KEY = "screenshotSegmentSets"


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        head = stream.read(24)
    if len(head) < 24 or head[:8] != _PNG_MAGIC or head[12:16] != b"IHDR":
        raise SourceIntakeError(f"分段图不是有效 PNG：{path.name}")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _checked_segment_set(index: dict[str, Any], rwid: str, project: str, index_dir: Path | None = None) -> list[dict[str, Any]]:
    """同案、顺序、重叠和末端都证明过的分段集合；任何一项缺失都不升级为“完整”。"""

    if str(index.get("rwid")) != rwid or index.get("projectNo") != project:
        raise SourceIntakeError("分段索引的 RWID/项目编号与批次记录不是同一案")
    url = index.get("sourceUrl")
    if not isinstance(url, str) or re.findall(r"[?&#]RWID=(\d+)", url) != [rwid]:
        raise SourceIntakeError("分段索引的来源 URL 与 RWID 不一致")
    segments = index.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SourceIntakeError("分段索引没有分段")
    out: list[dict[str, Any]] = []
    scroll_height = None
    for position, item in enumerate(segments):
        if not isinstance(item, dict) or item.get("index") != position:
            raise SourceIntakeError("分段序号必须从 0 连续")
        png = Path(str(item.get("png", "")))
        if not png.is_file() and index_dir is not None:  # 索引里的绝对路径来自另一主机/盘符：只按同目录同文件名找，摘要仍须相符
            png = index_dir / re.split(r"[\\/]", str(item.get("png", "")))[-1]
        if not png.is_file():
            raise SourceIntakeError(f"分段图不存在：第 {position} 段")
        sha = _sha256(png)
        if sha != "sha256:" + str(item.get("sha256", "")).removeprefix("sha256:"):
            raise SourceIntakeError(f"分段图摘要与索引不一致：第 {position} 段")
        width, height = _png_dimensions(png)
        if (item.get("width"), item.get("height")) != (width, height):
            raise SourceIntakeError(f"分段图尺寸与索引不一致：第 {position} 段")
        cover_from, cover_to, height_total = item.get("coverFrom"), item.get("coverTo"), item.get("scrollHeight")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (cover_from, cover_to, height_total)) or cover_to <= cover_from:
            raise SourceIntakeError(f"分段覆盖区间无效：第 {position} 段")
        scroll_height = height_total if scroll_height is None else scroll_height
        if height_total != scroll_height:
            raise SourceIntakeError("各段记录的页面总高度不一致")
        if position == 0 and cover_from != 0:
            raise SourceIntakeError("首段必须从页面顶部开始")
        if position and cover_from > out[-1]["coverTo"]:
            raise SourceIntakeError(f"第 {position - 1} 与 {position} 段之间有未覆盖空隙")
        if position and cover_from < out[-1]["coverFrom"]:
            raise SourceIntakeError("分段覆盖顺序倒退")
        out.append({"index": position, "png": png, "sha256": sha, "width": width, "height": height,
                    "coverFrom": cover_from, "coverTo": cover_to, "scrollHeight": height_total})
    if out[-1]["coverTo"] < scroll_height - 1:
        raise SourceIntakeError("末段没有覆盖到页面末端")
    return out


def attach_detail_segments(
    workspace: Any,
    batch_id: str,
    index_path: str | Path,
    *,
    attached_at: datetime | str | None = None,
) -> dict[str, Any]:
    """把一案已有的多段详情截图按覆盖索引正常绑定为永久 D 引用。

    只增叶：首段必须已是该案登记的详情截图(保留旧引用与原图)，其余段复制为 ``案卷详情_<sha12>.png``，
    索引本身以字节副本保存；同一索引重入 0 写。同案身份、顺序/重叠、末端覆盖和每段摘要/尺寸全部成立才写，
    否则拒绝且不留半成品引用。其他案的单张引用不因此升级。
    """

    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    try:
        raw = Path(index_path).read_bytes()
        index = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as error:
        raise SourceIntakeError(f"无法读取分段覆盖索引：{error}") from error
    if not isinstance(index, dict):
        raise SourceIntakeError("分段覆盖索引必须是 JSON 对象")
    index_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    rwid, project = str(index.get("rwid", "")), str(index.get("projectNo", ""))
    record = (state.get("records") or {}).get(rwid)
    if not isinstance(record, dict) or record.get("projectNo") != project:
        raise SourceIntakeError("批次里没有该 RWID/项目编号的记录")
    detail_key = "collectionDetail" if isinstance(record.get("collectionDetail"), dict) else "detail"
    detail = record.get(detail_key)
    if not isinstance(detail, dict) or not detail.get("screenshot"):
        raise SourceIntakeError("该案还没有已登记的详情截图，不能绑定分段")
    segments = _checked_segment_set(index, rwid, project, Path(index_path).parent)
    registered = {detail["screenshot"].get("sha256")} | {o.get("sha256") for o in detail.get("screenshotObservations") or []}
    if segments[0]["sha256"] not in registered:
        raise SourceIntakeError("首段不是该案已登记的详情截图，拒绝绑定")
    stored_first = _workspace_relative_path(layout, detail["screenshot"].get("relativePath"), "已登记详情截图")
    if not stored_first.is_file() or _sha256(stored_first) != detail["screenshot"].get("sha256"):
        raise SourceIntakeError("已登记详情截图与本地证据无法对账")
    existing_sets = list(detail.get(SEGMENT_SET_KEY) or [])
    if any(item.get("indexSha256") == index_sha for item in existing_sets):
        return {"status": "REUSED", "projectNo": project, "rwid": rwid, "indexSha256": index_sha, "segments": len(segments)}
    case_dir = _case_evidence_dir(layout, state, project, batch_id)
    refs = []
    for item in segments:
        target = case_dir / f"案卷详情_{item['sha256'].removeprefix('sha256:')[:12]}.png"
        _copy_immutable(item["png"], target)
        refs.append({**_evidence_file(target, Path(layout.root)), "index": item["index"], "coverFrom": item["coverFrom"],
                     "coverTo": item["coverTo"], "width": item["width"], "height": item["height"]})
    index_target = case_dir / f"案卷详情覆盖索引_{index_sha.removeprefix('sha256:')[:12]}.json"
    if index_target.exists():
        if _sha256(index_target) != index_sha:
            raise SourceIntakeError("覆盖索引目标已存在且内容不同")
    else:
        index_target.write_bytes(raw)
        if _sha256(index_target) != index_sha:
            raise SourceIntakeError("覆盖索引复制后的哈希不一致")
    moment = _timestamp(attached_at)
    detail[SEGMENT_SET_KEY] = [*existing_sets, {
        "indexSha256": index_sha, "index": _evidence_file(index_target, Path(layout.root)), "attachedAt": moment,
        "sourceUrl": index["sourceUrl"], "scrollHeight": segments[0]["scrollHeight"], "segments": refs,
        "coverage": "CONTIGUOUS_TOP_TO_END", "firstSegmentReusesRegisteredScreenshot": True,
    }]
    state["updatedAt"] = moment
    _write_json(path, state)
    return {"status": "BOUND", "projectNo": project, "rwid": rwid, "indexSha256": index_sha, "segments": len(segments),
            "references": [r["relativePath"] for r in refs]}


def derive_material_batch(
    workspace: Any,
    source_batch_id: str,
    batch_id: str,
    rwids: list[str],
    *,
    created_at: datetime | str | None = None,
) -> dict[str, Any]:
    """从已稳定的年度批次派生“整包采集专用批次”。

    只复制所选 RWID 的年度身份记录、查询证据和稳定结论，原批次不改；派生批次不带年度字段，
    因此不会进入年度覆盖对账，也不会替代原年度基线。写入、下载与接件均落在新批次。
    """

    if __package__:
        from . import source_coverage as coverage
    else:
        import source_coverage as coverage
    layout = _layout(workspace)
    _require_safe_component(batch_id, "批次编号")
    _, source_state = _load_capture(layout, source_batch_id)
    filters = source_state.get("filters", {})
    if (
        filters.get("selectionMode") != coverage.ANNUAL_MODE
        or source_state.get("stableRounds", 0) < 2
        or source_state.get("listResult") != "STABLE"
        or source_state.get("conflicts")
        or coverage.query_evidence_issue(layout, source_state)
    ):
        raise SourceIntakeError("派生整包批次必须基于已核实类别的稳定年度清单")
    path = _capture_path(layout, batch_id)
    if path.exists():
        raise SourceIntakeError("采集批次已存在，拒绝覆盖")
    keys = [_require_rwid(item) for item in rwids]
    records = source_state.get("records", {})
    missing = [key for key in keys if key not in records or records[key].get("aliasOf")]
    if missing or len(set(keys)) != len(keys):
        raise SourceIntakeError("所选 RWID 不在原年度批次或重复，拒绝派生")
    created = _timestamp(created_at)
    derived_filters = {k: v for k, v in filters.items() if k != "year"}
    derived_filters["derivedFromBatchId"] = source_batch_id
    state: dict[str, Any] = {
        "schemaVersion": BROWSER_CAPTURE_VERSION,
        "batchId": batch_id,
        "workspaceRoot": source_state["workspaceRoot"],
        "sourceOrigin": source_state["sourceOrigin"],
        "filters": derived_filters,
        "filterFingerprint": _fingerprint(derived_filters),
        "scope": "all",
        "status": "BASELINE_INDEXED",
        "currentRound": source_state.get("currentRound", 2),
        "stableRounds": source_state["stableRounds"],
        "listResult": "STABLE",
        "rounds": {},
        "records": {
            key: {k: v for k, v in copy.deepcopy(records[key]).items() if k != "materialCollection"}
            for key in keys
        },
        "conflicts": [],
        "anomalies": [],
        "detailRwids": [],
        "actionRwids": [],
        "materialRwids": [],
        "queryEvidence": source_state["queryEvidence"],
        "derivedFrom": {
            "batchId": source_batch_id,
            "sourceListCount": source_state.get("sourceListCount"),
            "sourceFilterFingerprint": source_state.get("filterFingerprint"),
            "rwidCount": len(keys),
        },
        "createdAt": created,
        "updatedAt": created,
    }
    _write_json(path, state)
    return state


def extend_material_batch(
    workspace: Any,
    batch_id: str,
    source_batch_id: str,
    rwids: list[str],
    *,
    updated_at: datetime | str | None = None,
) -> dict[str, Any]:
    """向已派生的整包采集专用批次追加另一类别稳定年度批次中的所选 RWID 身份记录。

    每条追加记录保留 derivedFromBatchId，原稳定批次和查询证据不改；已存在的 RWID 不覆盖。
    """

    if __package__:
        from . import source_coverage as coverage
    else:
        import source_coverage as coverage
    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    if not state.get("derivedFrom") or state.get("filters", {}).get("selectionMode") != coverage.ANNUAL_MODE:
        raise SourceIntakeError("只能向派生整包采集批次追加记录")
    _, source_state = _load_capture(layout, source_batch_id)
    if (
        source_state.get("filters", {}).get("selectionMode") != coverage.ANNUAL_MODE
        or source_state.get("stableRounds", 0) < 2
        or source_state.get("listResult") != "STABLE"
        or source_state.get("conflicts")
        or coverage.query_evidence_issue(layout, source_state)
    ):
        raise SourceIntakeError("追加来源必须是已核实类别的稳定年度清单")
    keys = [_require_rwid(item) for item in rwids]
    records = source_state.get("records", {})
    missing = [key for key in keys if key not in records or records[key].get("aliasOf")]
    if missing or len(set(keys)) != len(keys):
        raise SourceIntakeError("所选 RWID 不在来源年度批次或重复，拒绝追加")
    added = []
    for key in keys:
        if key in state["records"]:
            continue
        record = {k: v for k, v in copy.deepcopy(records[key]).items() if k != "materialCollection"}
        record["derivedFromBatchId"] = source_batch_id
        state["records"][key] = record
        added.append(key)
    if added:
        extra = state["derivedFrom"].setdefault("extendedFrom", {})
        extra[source_batch_id] = {
            "taskCategory": source_state["filters"].get("taskCategory"),
            "sourceListCount": source_state.get("sourceListCount"),
            "sourceFilterFingerprint": source_state.get("filterFingerprint"),
            "queryEvidence": source_state.get("queryEvidence"),
            "rwidCount": len([k for k, r in state["records"].items() if r.get("derivedFromBatchId") == source_batch_id]),
        }
        state["updatedAt"] = _timestamp(updated_at)
        _write_json(path, state)
    return state


def _inherited_material_state(
    layout: Any,
    state: dict[str, Any],
    rwid: str,
    record: dict[str, Any],
    detail_key: str,
    existing: dict[str, Any],
) -> dict[str, Any] | None:
    """证明派生整包批次里的旧 collectionDetail 确由来源批次原样继承；证明不了返回 None。

    只比对来源批次磁盘记录，不信任标签或 derivedFrom 字样：旧详情(以及一同继承的 downloadDelivery)
    必须与来源批次同一 RWID 的记录逐字段相等，且本记录尚未绑定案卷包。
    """

    source_id = (state.get("derivedFrom") or {}).get("batchId")
    if detail_key != "collectionDetail" or not source_id or record.get("package"):
        return None
    try:
        _, source_state = _load_capture(layout, source_id)
    except SourceIntakeError:
        return None
    source_record = (source_state.get("records") or {}).get(rwid) or {}
    if not source_record.get("collectionDetail") or source_record["collectionDetail"] != existing:
        return None
    carried: dict[str, Any] = {"sourceBatchId": source_id, "collectionDetail": copy.deepcopy(existing)}
    delivery = record.get("downloadDelivery")
    if delivery is not None and delivery == source_record.get("downloadDelivery"):
        carried["downloadDelivery"] = copy.deepcopy(delivery)
    return carried


def _is_acceptance_sample(state: dict[str, Any]) -> bool:
    return state.get("scope") == "acceptance"


def _maybe_waterline(layout: Any, project_no: str, **fields: Any) -> None:
    if upsert_case is None:
        return
    try:
        upsert_case(layout, project_no, **fields)
    except TypeError as error:  # 接口漂移必须显式暴露，不能静默丢水位。
        raise SourceIntakeError(f"案卷水位接口不兼容：{error}") from error


def begin_capture(
    workspace: Any,
    filters: dict[str, Any] | str | Path | None = None,
    *,
    now: datetime | str | None = None,
    batch_id: str | None = None,
    origin: str | None = None,
    scope: str = "all",
    filter_json: dict[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """创建 BrowserCaptureV1 批次；已存在批次不会被覆盖。"""

    layout = _layout(workspace)
    filter_value = _json_input(filter_json if filter_json is not None else filters, "筛选条件")
    clean_filters = _default_filters(
        now, _clean_evidence(filter_value), acceptance=scope == "acceptance"
    )
    if scope not in {"all", "acceptance"}:
        raise SourceIntakeError("采集范围只能是 all 或 acceptance")
    if scope == "acceptance":
        if clean_filters.get("acceptanceMode") != "SINGLE_CASE_DOWNLOAD_PROOF":
            raise SourceIntakeError("验收样本必须显式声明 SINGLE_CASE_DOWNLOAD_PROOF")
        live_total = clean_filters.get("liveTotalCount")
        sample_count = clean_filters.get("sampleCount")
        if (
            type(live_total) is not int
            or live_total < 0
            or type(sample_count) is not int
            or sample_count != 1
            or sample_count > live_total
        ):
            raise SourceIntakeError("单案验收必须记录实时总数，并将样本数设为 1")
    created_at = _timestamp(now)
    if batch_id is None:
        seed = _fingerprint({"createdAt": created_at, "filters": clean_filters})[-8:]
        batch_id = datetime.fromisoformat(created_at).strftime("%Y%m%d-%H%M%S") + "-" + seed
    _require_safe_component(batch_id, "批次编号")
    path = _capture_path(layout, batch_id)
    if path.exists():
        raise SourceIntakeError("采集批次已存在，拒绝覆盖")
    source_origin = sanitize_source_url(origin or str(filter_value.get("origin") or ""))
    if source_origin is None:
        raise SourceIntakeError("必须提供来源页面地址")
    state: dict[str, Any] = {
        "schemaVersion": BROWSER_CAPTURE_VERSION,
        "batchId": batch_id,
        "workspaceRoot": str(Path(layout.root)),
        "sourceOrigin": source_origin,
        "filters": clean_filters,
        "filterFingerprint": _fingerprint(clean_filters),
        "scope": scope,
        "status": "COLLECTING_LIST",
        "currentRound": 1,
        "stableRounds": 0,
        "rounds": {},
        "records": {},
        "conflicts": [],
        "anomalies": [],
        "createdAt": created_at,
        "updatedAt": created_at,
    }
    if clean_filters.get("selectionMode") == "ANNUAL_CASE_BASELINE":
        evidence_path = _workspace_relative_path(
            layout, clean_filters["queryEvidencePath"], "年度筛选证据"
        )
        if not evidence_path.is_file():
            raise SourceIntakeError("年度筛选页面回读证据不存在")
        state["queryEvidence"] = _evidence_file(evidence_path, Path(layout.root))
    if scope == "acceptance":
        state["listContract"] = "SAMPLE_ONLY"
        state["updatesGlobalWaterline"] = False
    _write_json(path, state)
    return state


def _page_from_input(
    page: dict[str, Any] | str | Path | None,
    page_number: int | None,
    items: list[dict[str, Any]] | None,
    total_count: int | None,
    total_pages: int | None,
) -> tuple[int, list[dict[str, Any]], int, int]:
    value = _json_input(page, "分页清单") if page is not None else {}
    page_no = page_number if page_number is not None else value.get("pageNumber", value.get("page"))
    rows = items if items is not None else value.get("items", value.get("records"))
    total = total_count if total_count is not None else value.get("totalCount", value.get("total"))
    pages = (
        total_pages if total_pages is not None else value.get("totalPages", value.get("pageCount"))
    )
    if not isinstance(page_no, int) or page_no < 1:
        raise SourceIntakeError("页码必须是正整数")
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise SourceIntakeError("分页 items 必须是对象数组")
    if not isinstance(total, int) or not 0 <= total <= MAX_CAPTURE_RECORDS:
        raise SourceIntakeError("列表总数不合法")
    if (
        not isinstance(pages, int)
        or not 1 <= pages <= MAX_CAPTURE_PAGES
        or page_no > pages
        or len(rows) > MAX_CAPTURE_RECORDS
    ):
        raise SourceIntakeError("列表总页数不合法")
    return page_no, rows, total, pages


def _evidence_file(path: Path, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SourceIntakeError(f"证据文件不存在：{path.name}")
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return {
        "relativePath": relative,
        "sha256": _sha256(path),
        "sizeBytes": path.stat().st_size,
    }


def add_page(
    workspace: Any,
    batch_id: str,
    page_number: int | None = None,
    items: list[dict[str, Any]] | None = None,
    total_count: int | None = None,
    total_pages: int | None = None,
    *,
    page: dict[str, Any] | str | Path | None = None,
    screenshot: str | Path | None = None,
    observed_at: datetime | str | None = None,
    round_no: int | None = None,
) -> dict[str, Any]:
    """向当前扫描轮次加入一页；相同页只允许同哈希幂等重放。"""

    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    if state["status"] not in {"COLLECTING_LIST", "LIST_CHANGING"}:
        raise SourceIntakeError("当前批次已结束列表采集")
    active_round = round_no if round_no is not None else int(state["currentRound"])
    if active_round != int(state["currentRound"]) or not 1 <= active_round <= 3:
        raise SourceIntakeError("扫描轮次与当前断点不一致")
    page_no, rows, total, pages = _page_from_input(
        page, page_number, items, total_count, total_pages
    )
    normalized = [_normalize_record(item) for item in rows]
    by_rwid: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for record in normalized:
        existing = by_rwid.get(record["rwid"])
        if existing is None:
            by_rwid[record["rwid"]] = record
            continue
        merged = _merge_rwid_records(existing, record)
        if merged is None:
            conflict = {"type": "RWID_CONFLICT", "rwid": record["rwid"], "page": page_no}
            if conflict not in conflicts:
                conflicts.append(conflict)
        else:
            by_rwid[record["rwid"]] = merged
    page_value: dict[str, Any] = {
        "pageNumber": page_no,
        "totalCount": total,
        "totalPages": pages,
        "rawItemCount": len(rows),
        "records": list(by_rwid.values()),
        "observedAt": _timestamp(observed_at),
    }
    if screenshot is not None:
        screenshot_source = Path(screenshot)
        if not screenshot_source.is_file():
            raise SourceIntakeError("列表页截图不存在")
        screenshot_sha = _sha256(screenshot_source)
        suffix = screenshot_source.suffix.lower() if screenshot_source.suffix else ".png"
        screenshot_target = (
            layout.capture_screenshots
            / batch_id
            / f"列表_轮{active_round}_页{page_no:03d}_{screenshot_sha[-12:]}{suffix}"
        )
        _copy_immutable(screenshot_source, screenshot_target)
        page_value["screenshot"] = _evidence_file(screenshot_target, Path(layout.root))
    page_value["fingerprint"] = _fingerprint(
        {key: value for key, value in page_value.items() if key not in {"observedAt", "screenshot"}}
    )
    rounds = state.setdefault("rounds", {})
    round_value = rounds.setdefault(str(active_round), {"pages": {}, "conflicts": []})
    existing_page = round_value["pages"].get(str(page_no))
    if existing_page is not None:
        if existing_page.get("fingerprint") != page_value["fingerprint"]:
            raise SourceIntakeError("同一轮次同一页内容发生变化，拒绝覆盖")
        return state
    round_value["pages"][str(page_no)] = page_value
    round_value["conflicts"].extend(conflicts)
    state["updatedAt"] = _timestamp(observed_at)
    _write_json(path, state)
    return state


def _round_records(
    round_value: dict[str, Any], *, inspection_query: bool = True
) -> tuple[dict[str, dict[str, Any]], int, int]:
    pages = round_value.get("pages") or {}
    if not pages:
        raise SourceIntakeError("当前扫描轮次还没有分页数据")
    values = list(pages.values())
    totals = {item.get("totalCount") for item in values}
    page_totals = {item.get("totalPages") for item in values}
    if len(totals) != 1 or len(page_totals) != 1:
        raise _RoundUnstable("同一轮扫描中的列表总数发生变化")
    total = int(next(iter(totals)))
    total_pages = int(next(iter(page_totals)))
    expected = {str(index) for index in range(1, total_pages + 1)}
    if set(pages) != expected:
        raise SourceIntakeError("分页清单尚未连续完整")
    raw_count = sum(int(item.get("rawItemCount", 0)) for item in values)
    if raw_count != total:
        raise _RoundUnstable(f"分页条数与页面报告总数不一致：{raw_count}/{total}")
    records: dict[str, dict[str, Any]] = {}
    for page_value in sorted(values, key=lambda item: item["pageNumber"]):
        for record in page_value["records"]:
            existing = records.get(record["rwid"])
            if existing is None:
                records[record["rwid"]] = record
                continue
            merged = _merge_rwid_records(existing, record)
            if merged is None:
                conflict = {"type": "RWID_CONFLICT", "rwid": record["rwid"]}
                conflicts = round_value.setdefault("conflicts", [])
                if conflict not in conflicts:
                    conflicts.append(conflict)
            else:
                records[record["rwid"]] = merged
    if inspection_query:
        round_value["anomalies"] = _annotate_inspection_stages(records)
    else:
        # Multiple notices cannot establish initial/recheck stages or inspection counts.
        round_value["anomalies"] = []
        for record in records.values():
            record["inspectionStages"] = []
    return records, total, total_pages


def _project_conflicts(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records.values():
        project_no = record.get("projectNo")
        if project_no:
            grouped.setdefault(project_no, []).append(record)
    result = []
    for project_no, items in grouped.items():
        signatures = {_record_core_equivalence(item) for item in items}
        if len(items) > 1 and len(signatures) > 1:
            result.append(
                {
                    "type": "PROJECT_CONFLICT",
                    "projectNo": project_no,
                    "rwids": sorted(item["rwid"] for item in items),
                }
            )
    return result


def _selected_rwids(state: dict[str, Any]) -> set[str]:
    if "actionRwids" in state:
        return set(state.get("actionRwids") or [])
    if state.get("scope") == "selected":
        return set(state.get("selectedRwids") or [])
    return {
        key
        for key, record in (state.get("records") or {}).items()
        if not record.get("aliasOf") and not record.get("skippedAsUnchanged")
    }


def _refresh_progress(state: dict[str, Any]) -> None:
    acceptance = _is_acceptance_sample(state)
    selected = _selected_rwids(state)
    if state.get("filters", {}).get("selectionMode") == "ANNUAL_CASE_BASELINE":
        material_records = [
            state.get("records", {}).get(key, {}) for key in state.get("materialRwids", [])
        ]
        if material_records:
            state["materialCollectionStatus"] = (
                "READY_FOR_ORGANIZATION"
                if all(record.get("package") for record in material_records)
                else "PACKAGE_WAITING"
                if all(record.get("collectionDetail") for record in material_records)
                else "COLLECTING_DETAILS"
            )
        state["status"] = (
            "NEEDS_MANUAL_REVIEW"
            if state.get("conflicts")
            else "BASELINE_INDEXED"
            if not selected
            else "COLLECTING_IDENTITIES"
        )
        return
    if not selected:
        if state.get("listResult") in {"STABLE", "SAMPLE_STABLE"} and not state.get("conflicts"):
            state["status"] = "ACCEPTANCE_COMPLETE" if acceptance else "COMPLETED"
        return
    records = state.get("records") or {}
    all_detail = all(records.get(key, {}).get("detail") for key in selected)
    all_package = all(records.get(key, {}).get("package") for key in selected)
    if all_detail and all_package and not state.get("conflicts"):
        state["status"] = "ACCEPTANCE_COMPLETE" if acceptance else "READY_FOR_ORGANIZATION"
    elif all_package:
        state["status"] = "ACCEPTANCE_PACKAGES_READY" if acceptance else "PACKAGES_READY"
    else:
        state["status"] = "ACCEPTANCE_COLLECTING_DETAILS" if acceptance else "COLLECTING_DETAILS"


def _prepare_acceptance_queue(state: dict[str, Any], records: dict[str, dict[str, Any]]) -> None:
    """验收样本只按 RWID 建详情队列，不读取或更新正式案卷水位。"""

    actions: list[str] = []
    for rwid in _tail_ordered_rwids(records):
        record = records[rwid]
        record["sourceRecordFingerprint"] = _record_equivalence(record)
        actions.append(rwid)
    state["detailRwids"] = list(actions)
    state["actionRwids"] = actions


def _observation_registered(source_part: dict[str, Any], observed_at: datetime | str | None, batch_id: Any) -> bool:
    """True when the ledger already records this exact observation instant and batch, so re-recording it is no change."""

    try:
        recorded = _timestamp(source_part.get("lastObservedAt")) if source_part.get("lastObservedAt") else None
        moment = _timestamp(observed_at)
    except (TypeError, ValueError):
        return False
    return recorded == moment and (source_part.get("batchId") == batch_id or batch_id in (source_part.get("batchIds") or []))


def _prepare_incremental_queue(
    layout: Any,
    state: dict[str, Any],
    records: dict[str, dict[str, Any]],
    observed_at: datetime | str | None,
) -> None:
    """列表阶段只建立 RWID 详情队列，项目水位必须在详情读到项目编号后判断。"""

    if __package__:
        from .ledger_views import describe
    else:
        from ledger_views import describe
    actions: list[str] = []
    incremental = state["filters"].get("selectionMode") == "RECENT_DOCUMENT_ACTIVITY"
    known = (load_waterline(layout).get("cases") or {}) if incremental and load_waterline else {}
    for rwid in _tail_ordered_rwids(records):
        record = records[rwid]
        record["sourceRecordFingerprint"] = _record_equivalence(record)
        matches = [
            (p, r)
            for p, r in known.items()
            if r.get("source", {}).get("rwid") == rwid
            or rwid in r.get("source", {}).get("observationsByRwid", {})
        ]
        if len(matches) == 1:
            project, previous = matches[0]
            old = previous.get("source", {}).get("observationsByRwid", {}).get(rwid, {})
            incoming = {document_fingerprint(a) for a in record.get("sourceAppearances", [])}
            seen = set(old.get("documentFingerprints") or [])
            row = (
                describe(layout, project, previous)
                if incoming and incoming <= seen and not previous.get("source", {}).get("changePending")
                else None
            )
            stable_reuse = bool(
                row is not None
                and row["lifecycle"]["stable"]
                and row["stages"]["packageReceived"]
            )
            # Unchanged documents and no concrete source cause (e.g. only a local gap such as an unrecorded
            # initialProductRef): nothing a source revisit could change, so reuse the received package locally.
            cause_free_reuse = bool(
                row is not None
                and not stable_reuse
                and row["stages"]["packageReceived"]
                and not row["lifecycle"]["sourceCauses"]
                and row["lifecycle"]["category"] != "NEEDS_SOURCE"
            )
            if (
                row is not None
                and not row["systemChanged"]
                and (
                    _verified_completed_waterline(layout, project)
                    or (previous.get("source", {}).get("indexOnly") is True and row["initialResult"] == "QUALIFIED")
                    or stable_reuse
                    or cause_free_reuse
                )
            ):
                record.update(
                    {
                        "projectNo": project,
                        "skippedAsUnchanged": True,
                        "skippedAsCompletedProject": previous.get("state") == "COMPLETED",
                        "skipReason": "STABLE_CASE_LOCAL_REUSE"
                        if stable_reuse and previous.get("state") != "COMPLETED"
                        else "NO_SOURCE_CAUSE_LOCAL_REUSE"
                        if cause_free_reuse and previous.get("state") != "COMPLETED"
                        else "SOURCE_DOCUMENTS_ALREADY_VERIFIED",
                    }
                )
                if not _observation_registered(previous.get("source") or {}, observed_at, state["batchId"]):
                    _maybe_waterline(
                        layout,
                        project,
                        source={"lastObservedAt": _timestamp(observed_at), "batchId": state["batchId"]},
                    )
                continue
        actions.append(rwid)
    state["detailRwids"] = list(actions)
    state["waterlineDecision"] = "AFTER_DETAIL_PROJECT_NO"
    state["actionRwids"] = actions


def _prepare_annual_queue(layout: Any, state: dict, records: dict, observed_at: Any) -> None:
    if __package__:
        from .source_coverage import formal_captures, identity_bindings
    else:
        from source_coverage import formal_captures, identity_bindings
    known = load_waterline(layout) if load_waterline else {"cases": {}}
    bindings = identity_bindings(formal_captures(layout), known)
    actions = []
    for rwid, record in records.items():
        record["sourceRecordFingerprint"] = _record_equivalence(record)
        projects = bindings.get(rwid, set())
        if len(projects) == 1 and (not record.get("projectNo") or record["projectNo"] in projects):
            project = next(iter(projects))
            record.update(projectNo=project, identityReused=True, skippedAsUnchanged=True)
            prior = known["cases"].get(project, {})
            _maybe_waterline(
                layout,
                project,
                source={
                    "projectIdentitySource": "DETAIL",
                    "batchId": state["batchId"],
                    "annualIdentitiesByRwid": {
                        rwid: {
                            "sourceYear": state["filters"]["year"],
                            "batchId": state["batchId"],
                            "observedAt": _timestamp(observed_at),
                        }
                    },
                    "lastObservedAt": _timestamp(observed_at),
                },
                **(
                    {"unitName": _find_first(record.get("fields", {}), _UNIT_NAME_KEYS)}
                    if not prior
                    else {}
                ),
            )
        else:
            actions.append(rwid)
            if len(projects) > 1 or (projects and record.get("projectNo") not in projects):
                state.setdefault("conflicts", []).append(
                    {"type": "IDENTITY_CONFLICT", "rwid": rwid}
                )
    state["detailRwids"] = actions
    state["actionRwids"] = list(actions)
    state["waterlineDecision"] = "ANNUAL_IDENTITY_ONLY"


def _add_annual_identity(
    layout: Any,
    path: Path,
    state: dict,
    record: dict,
    rwid: str,
    project: str,
    fields: dict,
    source_url: str | None,
    screenshot: str | Path | None,
    captured: str,
) -> dict:
    if not source_url or not screenshot or not Path(screenshot).is_file():
        raise SourceIntakeError("年度身份关联必须提供本案详情 URL 和完整详情截图")
    target = path.parent / "identities" / project
    target.mkdir(parents=True, exist_ok=True)
    digest = _fingerprint(fields).removeprefix("sha256:")
    image = target / (f"{rwid}-{_sha256(Path(screenshot))[-12:]}" + Path(screenshot).suffix)
    _copy_immutable(Path(screenshot), image)
    evidence_path = target / f"{rwid}-{digest[:12]}.json"
    evidence = {
        "schemaVersion": "SourceIdentityV1",
        "projectNo": project,
        "rwid": rwid,
        "sourceUrl": source_url,
        "fields": fields,
        "observedAt": captured,
        "screenshot": _evidence_file(image, Path(layout.root)),
    }
    if not evidence_path.exists():
        _write_json(evidence_path, evidence)
    record.update(
        projectNo=project,
        detail={
            "projectNo": project,
            "fields": fields,
            "capturedAt": captured,
            "sourceUrl": source_url,
            "evidence": _evidence_file(evidence_path, Path(layout.root)),
        },
        skippedAsUnchanged=True,
        indexOnly=True,
    )
    source = {
        "projectIdentitySource": "DETAIL",
        "batchId": state["batchId"],
        "lastObservedAt": captured,
        "annualIdentitiesByRwid": {
            rwid: {
                "sourceYear": state["filters"]["year"],
                "batchId": state["batchId"],
                "observedAt": captured,
            }
        },
    }
    if __package__:
        from .ledger_views import source_qualification
    else:
        from ledger_views import source_qualification
    classification = source_qualification(fields, [])
    if classification:
        source["classification"] = {
            **classification,
            "evidencePath": str(evidence_path.relative_to(layout.root)),
            "evidenceSha256": _sha256(evidence_path),
            "observedAt": captured,
        }
        source["indexOnly"] = classification["initialResult"] == "QUALIFIED"
    _maybe_waterline(layout, project, source=source, unitName=_find_first(fields, _UNIT_NAME_KEYS))
    state["actionRwids"] = [key for key in state.get("actionRwids", []) if key != rwid]
    _refresh_progress(state)
    state["updatedAt"] = captured
    _write_json(path, state)
    return state


def finalize_capture(
    workspace: Any,
    batch_id: str,
    *,
    now: datetime | str | None = None,
    max_rounds: int = 3,
) -> dict[str, Any]:
    """结束当前扫描轮；连续两轮相同才进入详情/包接收阶段。"""

    if max_rounds != 3:
        raise SourceIntakeError("浏览器清单最多固定扫描 3 轮")
    layout = _layout(workspace)
    path, state = _load_capture(layout, batch_id)
    if state["status"] not in {"COLLECTING_LIST", "LIST_CHANGING"}:
        _refresh_progress(state)
        state["updatedAt"] = _timestamp(now)
        _write_json(path, state)
        return state
    current = int(state["currentRound"])
    round_value = state["rounds"].get(str(current))
    if not isinstance(round_value, dict):
        raise SourceIntakeError("当前扫描轮次没有数据")
    try:
        records, total, total_pages = _round_records(
            round_value,
            inspection_query=state["filters"].get("selectionMode")
            not in {
                "ANNUAL_RECTIFICATION_NOTICE",
                "RECENT_DOCUMENT_ACTIVITY",
                "ANNUAL_CASE_BASELINE",
            },
        )
    except _RoundUnstable as error:
        round_value.update(
            {
                "invalidReason": str(error),
                "completedAt": _timestamp(now),
                "fingerprint": _fingerprint(round_value.get("pages") or {}),
            }
        )
        state["stableRounds"] = 0
        if current >= max_rounds:
            state["status"] = "LIST_CHANGING"
            state["listResult"] = "CHANGING"
        else:
            state["currentRound"] = current + 1
            state["status"] = "COLLECTING_LIST"
            state["listResult"] = "NEEDS_ANOTHER_ROUND"
        state["updatedAt"] = _timestamp(now)
        _write_json(path, state)
        return state
    if _is_acceptance_sample(state) and total != state["filters"].get("sampleCount"):
        conflict = {
            "type": "ACCEPTANCE_SAMPLE_COUNT_MISMATCH",
            "declaredSampleCount": state["filters"].get("sampleCount"),
            "reportedTotal": total,
        }
        if conflict not in state.setdefault("conflicts", []):
            state["conflicts"].append(conflict)
        state["status"] = "NEEDS_MANUAL_REVIEW"
        state["listResult"] = "SAMPLE_INVALID"
        state["updatedAt"] = _timestamp(now)
        _write_json(path, state)
        return state
    fingerprint = _fingerprint(
        [
            {
                "rwid": key,
                **_record_core(records[key]),
                "documents": sorted(
                    document_fingerprint(a) for a in records[key].get("sourceAppearances", [])
                ),
            }
            for key in sorted(records)
        ]
    )
    round_value.update(
        {
            "fingerprint": fingerprint,
            "documentRecordCount": total,
            "uniqueRecordCount": len(records),
            "uniqueRwidCount": len(records),
            "reportedTotal": total,
            "totalPages": total_pages,
            "completedAt": _timestamp(now),
        }
    )
    previous = state["rounds"].get(str(current - 1))
    stable = bool(previous and previous.get("fingerprint") == fingerprint)
    state["stableRounds"] = 2 if stable else 1
    if stable:
        state["records"] = records
        annual_baseline = state["filters"].get("selectionMode") == "ANNUAL_CASE_BASELINE"
        state["sourceListCount"] = total
        state["sourceDocumentCount"] = 0 if annual_baseline else total
        state["uniqueRwidCount"] = len(records)
        state["anomalies"] = list(round_value.get("anomalies") or [])
        conflicts = list(round_value.get("conflicts") or []) + _project_conflicts(records)
        state["conflicts"] = conflicts
        state["listResult"] = "SAMPLE_STABLE" if _is_acceptance_sample(state) else "STABLE"
        if conflicts:
            state["status"] = "NEEDS_MANUAL_REVIEW"
        else:
            if _is_acceptance_sample(state):
                _prepare_acceptance_queue(state, records)
                state["status"] = "ACCEPTANCE_COLLECTING_DETAILS"
            elif annual_baseline:
                _prepare_annual_queue(layout, state, records, now)
            else:
                _prepare_incremental_queue(layout, state, records, now)
                state["status"] = "COLLECTING_DETAILS"
            _refresh_progress(state)
    elif current >= max_rounds:
        state["status"] = "LIST_CHANGING"
        state["listResult"] = "CHANGING"
    else:
        state["currentRound"] = current + 1
        state["status"] = "COLLECTING_LIST"
        state["listResult"] = "NEEDS_ANOTHER_ROUND"
    state["updatedAt"] = _timestamp(now)
    _write_json(path, state)
    return state


def _derive_tags(detail: dict[str, Any]) -> list[str]:
    text = _canonical(detail)
    assessment_text = text
    for safe_phrase in (
        "未发现不合格现象",
        "未发现不合格",
        "未发现不符合",
        "无不合格",
        "无不符合",
        "未检出不合格",
        "未检出不符合",
    ):
        assessment_text = assessment_text.replace(safe_phrase, "")
    tags: list[str] = []
    if any(word in text for word in ("现场检查", "现场判定", "现场")):
        tags.append("现场检查")
    if any(word in text for word in ("抽样送检", "抽样", "送检")):
        tags.append("抽样送检")
    if any(word in assessment_text for word in ("不合格", "不符合", "不通过")):
        tags.append("不合格")
    if any(word in text for word in ("复查", "复检", "整改")):
        tags.append("复查")
    without_negative = assessment_text.replace("不合格", "").replace("不符合", "")
    if any(word in without_negative for word in ("合格", "符合要求", "通过")):
        tags.append("合格")
    essential = (_find_first(detail, _PROJECT_KEYS), _find_first(detail, {"unitname", "单位名称"}))
    if any(value in (None, "") for value in essential) or not tags:
        tags.append("待确认")
    return tags


def _business_detail_fingerprint(value: Any) -> str:
    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            result = {}
            for raw_key, child in item.items():
                key = str(raw_key)
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if (
                    normalized == "runid"
                    or normalized in _RWID_KEYS
                    or "url" in normalized
                    or _is_sensitive_key(normalized, key)
                ):
                    continue
                result[key] = scrub(child)
            return result
        if isinstance(item, list):
            return [scrub(child) for child in item]
        return item

    return _fingerprint(scrub(value))


def _normalized_identity_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def _normalized_case_name(value: Any) -> str | None:
    normalized = _normalized_identity_value(value)
    if normalized is None:
        return None
    return re.sub(r"\((?:个体工商户|个体户)\)$", "", normalized)


def _detail_identity_fields(value: dict[str, Any]) -> dict[str, str]:
    candidates = {
        "projectNo": _find_first(value, _PROJECT_KEYS),
        "unitName": _find_first(value, _UNIT_NAME_KEYS),
        "brigadeCode": _find_first(
            value,
            {"brigadecode", "brigadenumber", "大队代码", "大队编号"},
        ),
    }
    return {
        key: normalized
        for key, item in candidates.items()
        if (normalized := _normalized_identity_value(item)) is not None
    }


def _detail_identity_mismatches(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    left_fields = _detail_identity_fields(left)
    right_fields = _detail_identity_fields(right)
    return sorted(
        key for key in set(left_fields) & set(right_fields) if left_fields[key] != right_fields[key]
    )


def _list_case_names(record: dict[str, Any]) -> list[str]:
    values = [
        _find_first(item, _CASE_NAME_KEYS)
        for item in list(record.get("sourceAppearances") or [])
        if isinstance(item, dict)
    ]
    values.append(_find_first(record.get("fields") or {}, _CASE_NAME_KEYS | _UNIT_NAME_KEYS))
    return list(dict.fromkeys(str(item).strip() for item in values if item not in (None, "")))


def _raise_case_identity_mismatch(
    capture_path: Path,
    state: dict[str, Any],
    record_key: str,
    project_no: str | None,
    reasons: list[str],
) -> NoReturn:
    """记录只阻塞当前案卷的身份链错误，并保留批次后续处理能力。"""

    record = state["records"][record_key]
    conflict = {
        "type": "CASE_IDENTITY_CHAIN_MISMATCH",
        "rwid": record_key,
        "projectNo": project_no,
        "reasons": reasons,
        "blocking": True,
        "blockingScope": "CURRENT_CASE",
    }
    if conflict not in state.setdefault("conflicts", []):
        state["conflicts"].append(conflict)
    record["caseIdentityStatus"] = "MISMATCH"
    record["identityMismatch"] = conflict
    state["updatedAt"] = _timestamp()
    _write_json(capture_path, state)
    raise SourceIntakeError("CASE_IDENTITY_CHAIN_MISMATCH：" + "；".join(reasons))


def _verified_completed_waterline(layout: Any, project_no: str) -> dict[str, Any] | None:
    """Return a completed record only when it carries a valid completion proof.

    Local completion (LocalVerificationV1) and historical server+NAS VERIFIED are both
    recognized; the evidence level stays distinguishable in the record itself.
    """

    if load_waterline is None:
        return None
    value = load_waterline(layout)
    record = (value.get("cases") or {}).get(project_no)
    if not isinstance(record, dict) or record.get("state") != "COMPLETED":
        return None
    completion = record.get("completion") or {}
    if completion.get("mode") == "LOCAL_V1":
        # Deep check: corrupt/missing archived files or changePending never count as complete.
        if local_validity.validate_local_completion(layout, project_no, record):
            return None
        return record
    upload = record.get("upload") or {}
    nas = record.get("nasVerification") or {}
    if upload.get("status") != "VERIFIED" or nas.get("status") != "VERIFIED":
        return None
    return record


def _copy_immutable(source: Path, destination: Path) -> None:
    if destination.exists():
        if destination.is_file() and _sha256(destination) == _sha256(source):
            return
        raise SourceIntakeError(f"目标证据已存在且内容不同：{destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if _sha256(temporary) != _sha256(source):
            raise SourceIntakeError("证据复制后的哈希不一致")
        os.rename(temporary, destination)
    except FileExistsError as error:
        raise SourceIntakeError(f"目标证据已存在：{destination.name}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _remove_confirmed_download_copy(
    source: Path, stored: Path, package_sha256: str
) -> dict[str, str]:
    """清理已安全转入工作目录的下载副本，绝不按文件名猜测或删除其他候选。"""

    if source.resolve() == stored.resolve():
        raise SourceIntakeError("下载副本与工作目录归档不能是同一文件")
    if not stored.is_file() or _sha256(stored) != package_sha256:
        raise SourceIntakeError("工作目录归档副本无法通过哈希复核，拒绝清理下载副本")
    checked_at = _timestamp()
    if not source.is_file():
        return {"status": "SOURCE_ALREADY_ABSENT", "checkedAt": checked_at}
    if _sha256(source) != package_sha256:
        return {
            "status": "SOURCE_RETAINED_HASH_MISMATCH",
            "checkedAt": checked_at,
            "reason": "下载副本与工作目录归档哈希不一致",
        }
    try:
        source.unlink()
    except OSError as error:
        return {
            "status": "SOURCE_RETAINED_CLEANUP_FAILED",
            "checkedAt": checked_at,
            "reason": f"删除下载副本失败：{error}",
        }
    return {"status": "MOVED_TO_WORKSPACE", "removedAt": _timestamp()}


def _case_evidence_dir(layout: Any, state: dict[str, Any], project_no: str, batch_id: str) -> Path:
    if _is_acceptance_sample(state):
        return layout.batch_dir(batch_id) / "验收样本" / project_no
    return layout.pending_case_dir(project_no)


def _load_evidence(
    layout: Any,
    project_no: str,
    batch_id: str,
    *,
    state: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    directory = _case_evidence_dir(layout, state, project_no, batch_id)
    path = directory / EVIDENCE_FILE_NAME
    if path.exists():
        value = _read_json(path, "来源证据")
        if value.get("schemaVersion") != SOURCE_EVIDENCE_VERSION:
            raise SourceIntakeError("来源证据版本不受支持")
        if value.get("projectNo") != project_no:
            raise SourceIntakeError("来源证据项目编号不匹配")
        return path, value
    return path, {
        "schemaVersion": SOURCE_EVIDENCE_VERSION,
        "projectNo": project_no,
        "batchIds": [batch_id],
        "records": {},
        "tags": [],
        "createdAt": _timestamp(),
        "updatedAt": _timestamp(),
    }


def add_detail(
    workspace: Any,
    batch_id: str,
    rwid: str,
    detail: dict[str, Any] | str | Path,
    source_url: str | None = None,
    screenshot_path: str | Path | None = None,
    *,
    captured_at: datetime | str | None = None,
    screenshot: str | Path | None = None,
    supersede_carried: bool = False,
) -> dict[str, Any]:
    """接收详情结构和截图，生成/更新 SourceEvidenceV1。

    supersede_carried 只是请求：仅当旧 collectionDetail 经来源批次磁盘记录证明确由派生时继承、
    记录未绑包，且本次现场详情通过上方全部身份校验并带有效截图时，才把旧值移为前代引用并解除
    对应的那条 DETAIL_CONFLICT(冲突须明确记录与被移旧指纹相同的 existingFingerprint；缺失则冲突保留待核)；
    证明不了继承关系就拒绝且不写任何状态。
    """

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("详情 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    if record.get("skippedAsUnchanged") and (
        state["filters"].get("selectionMode") != "ANNUAL_CASE_BASELINE"
    ):
        raise SourceIntakeError("详情项目编号已在水位中完成并通过完成核验")
    clean_detail = _clean_evidence(_json_input(detail, "案卷详情"))
    project_raw = _find_first(clean_detail, _PROJECT_KEYS)
    if project_raw in (None, ""):
        _raise_case_identity_mismatch(
            capture_path,
            state,
            record_key,
            None,
            ["详情未显式提供项目编号"],
        )
    project_no = _require_project_no(str(project_raw).strip().upper())
    safe_url = sanitize_source_url(source_url) if source_url else None
    url_rwid = _source_url_rwid(safe_url)
    if source_url and url_rwid != record_key:
        _raise_case_identity_mismatch(
            capture_path,
            state,
            record_key,
            project_no,
            [f"详情 URL 的 RWID {url_rwid or '缺失'} 与清单 RWID 不一致"],
        )
    expected_case_names = _list_case_names(record)
    detail_unit_name = _find_first(clean_detail, _UNIT_NAME_KEYS)
    normalized_expected = {
        item for value in expected_case_names if (item := _normalized_case_name(value)) is not None
    }
    normalized_actual = _normalized_case_name(detail_unit_name)
    if normalized_expected and normalized_actual not in normalized_expected:
        _raise_case_identity_mismatch(
            capture_path,
            state,
            record_key,
            project_no,
            [
                "详情单位名称与本次清单案卷名称不一致"
                if normalized_actual is not None
                else "详情未提供单位名称，无法对账本次清单案卷名称"
            ],
        )
    if record.get("projectNo") and record["projectNo"] != project_no:
        conflict = {"type": "DETAIL_PROJECT_CONFLICT", "rwid": record_key, "projectNo": project_no}
        state.setdefault("conflicts", []).append(conflict)
        state["status"] = "NEEDS_MANUAL_REVIEW"
        _write_json(capture_path, state)
        raise SourceIntakeError("详情项目编号与清单不一致，已转人工处理")
    captured = _timestamp(captured_at)
    annual_materials = _material_collection_active(layout, state, record_key, record)
    if state["filters"].get("selectionMode") == "ANNUAL_CASE_BASELINE" and not annual_materials:
        if record.get("detail", {}).get("fields") == clean_detail:
            return state
        return _add_annual_identity(
            layout,
            capture_path,
            state,
            record,
            record_key,
            project_no,
            clean_detail,
            safe_url,
            screenshot or screenshot_path,
            captured,
        )
    completed_record = (
        None if _is_acceptance_sample(state) else _verified_completed_waterline(layout, project_no)
    )
    if completed_record and state["filters"].get("selectionMode") == "RECENT_DOCUMENT_ACTIVITY":
        old_source = completed_record.get("source") or {}
        old_observation = old_source.get("observationsByRwid", {}).get(record_key, {})
        observed_docs = sorted(
            {document_fingerprint(a) for a in record.get("sourceAppearances", [])}
        )
        unchanged = bool(
            old_source.get("detailBusinessFingerprint")
            == _business_detail_fingerprint(clean_detail)
        )
        if old_observation.get("documentFingerprints"):
            unchanged = unchanged and set(observed_docs) <= set(
                old_observation["documentFingerprints"]
            )
        observation = {
            "fingerprint": record.get("sourceRecordFingerprint"),
            "documentFingerprints": observed_docs,
            "observedAt": captured,
        }
        record["projectNo"] = project_no
        record["detail"] = {
            "projectNo": project_no,
            "capturedAt": captured,
            "fields": {"projectNo": project_no, "unitName": str(detail_unit_name).strip()},
        }
        if not unchanged:
            # Preserve the accepted package/archive; capture only the new source observation.
            change_dir = capture_path.parent / "changes" / project_no
            change_dir.mkdir(parents=True, exist_ok=True)
            new_documents = [
                a
                for a in record.get("sourceAppearances", [])
                if document_fingerprint(a)
                not in set(old_observation.get("documentFingerprints", []))
            ]
            change_key = _fingerprint({"fields": clean_detail, "documents": observed_docs})[7:]
            change_path = change_dir / f"{record_key}-{change_key[:12]}.json"
            change_evidence = {
                "schemaVersion": "SourceChangeV1",
                "projectNo": project_no,
                "rwid": record_key,
                "observedAt": captured,
                "fields": clean_detail,
                "baseArchivedWorkspace": completed_record.get("archive", {}).get("workspacePath"),
                "newDocumentAppearances": new_documents,
                "documentFingerprints": observed_docs,
                "detailBusinessFingerprint": _business_detail_fingerprint(clean_detail),
            }
            if not change_path.exists():
                _write_json(change_path, change_evidence)
            else:
                prior_evidence = _json_input(change_path, "来源差异证据")
                if {k: v for k, v in prior_evidence.items() if k != "observedAt"} != {
                    k: v for k, v in change_evidence.items() if k != "observedAt"
                }:
                    raise SourceIntakeError("来源差异证据身份或内容变化，拒绝覆盖")
            record["sourceChangePending"] = True
            change_binding = {
                "evidencePath": str(change_path.relative_to(layout.root)),
                "evidenceSha256": _sha256(change_path),
                "newDocumentCount": len(new_documents),
            }
            changes = {
                "changePending": True,
                "lastObservedAt": captured,
                "batchId": batch_id,
                "pendingObservationsByRwid": {record_key: observation},
                "changeEvidencePath": change_binding["evidencePath"],
                "changeEvidenceByFingerprint": {change_key: change_binding},
            }
            if isinstance(clean_detail.get("initialInspection"), dict):
                if __package__:
                    from .ledger_views import qualification
                else:
                    from ledger_views import qualification
                changes["pendingClassification"] = {**qualification(clean_detail), **change_binding}
            _maybe_waterline(
                layout,
                project_no,
                source=changes,
            )
        else:
            record["skippedAsUnchanged"] = True
            record["skippedAsCompletedProject"] = True
            _maybe_waterline(
                layout,
                project_no,
                source={
                    "lastObservedAt": captured,
                    "batchId": batch_id,
                    "observationsByRwid": {record_key: observation},
                },
            )
        state["actionRwids"] = [item for item in state.get("actionRwids", []) if item != record_key]
        _refresh_progress(state)
        if not unchanged:
            state["status"] = "SOURCE_CHANGES_PENDING"
        state["updatedAt"] = captured
        _write_json(capture_path, state)
        return state
    if completed_record:
        # Resolve the live detail identity first, but never recreate pending
        # evidence or replace the provenance of an already archived case.
        record["projectNo"] = project_no
        record["detail"] = {
            "projectNo": project_no,
            "capturedAt": captured,
            "sourceUrl": safe_url,
            "fields": {"projectNo": project_no, "unitName": str(detail_unit_name).strip()},
            "completedProjectObservation": True,
        }
        record["skippedAsUnchanged"] = True
        record["skippedAsCompletedProject"] = True
        record["skipReason"] = "PROJECT_NO_ALREADY_VERIFIED"
        state["actionRwids"] = [item for item in state.get("actionRwids", []) if item != record_key]
        _maybe_waterline(layout, project_no, source={"lastObservedAt": captured})
        _refresh_progress(state)
        state["updatedAt"] = captured
        _write_json(capture_path, state)
        return state
    tags = _derive_tags(clean_detail)
    fingerprint = _fingerprint(clean_detail)
    business_fingerprint = _business_detail_fingerprint(clean_detail)
    alias_of: str | None = None
    for other_rwid, other_record in state["records"].items():
        if other_rwid == record_key:
            continue
        other_detail = other_record.get("collectionDetail" if annual_materials else "detail") or {}
        other_project = other_record.get("projectNo") or other_detail.get("projectNo")
        if other_project != project_no or not other_detail:
            continue
        mismatches = _detail_identity_mismatches(
            clean_detail,
            dict(other_detail.get("fields") or {}),
        )
        if mismatches:
            state.setdefault("conflicts", []).append(
                {
                    "type": "PROJECT_DETAIL_CONFLICT",
                    "projectNo": project_no,
                    "rwids": sorted([record_key, other_rwid]),
                    "identityFields": mismatches,
                }
            )
            state["status"] = "NEEDS_MANUAL_REVIEW"
            _write_json(capture_path, state)
            raise SourceIntakeError("同一项目编号的详情身份字段冲突，已转人工处理")
        canonical = other_record.get("aliasOf") or other_rwid
        canonical_record = state["records"][canonical]
        alias_package = record.get("package")
        canonical_package = canonical_record.get("package")
        if (
            alias_package
            and canonical_package
            and (alias_package.get("sha256") != canonical_package.get("sha256"))
        ):
            state.setdefault("conflicts", []).append(
                {
                    "type": "PROJECT_PACKAGE_CONFLICT",
                    "projectNo": project_no,
                    "rwids": sorted([record_key, canonical]),
                }
            )
            state["status"] = "NEEDS_MANUAL_REVIEW"
            _write_json(capture_path, state)
            raise SourceIntakeError("同一项目的多个 RWID 已绑定不同案卷包")
        if alias_package and not canonical_package:
            canonical_record["package"] = alias_package
        if alias_of is None:
            alias_of = canonical
    detail_key = "collectionDetail" if annual_materials else "detail"
    existing = record.get(detail_key)
    carried_supersede: dict[str, Any] | None = None
    if existing and existing.get("fingerprint") != fingerprint:
        if supersede_carried:
            carried_supersede = _inherited_material_state(
                layout, state, record_key, record, detail_key, existing
            )
            if carried_supersede is None:
                raise SourceIntakeError("无法证明旧详情由来源批次继承或记录已绑包，拒绝移出，冲突保持")
            carried_supersede["supersededFingerprint"] = existing.get("fingerprint")
            existing = None  # 现场新观察重新取证：截图必须为本次提供，不沿用前代截图
        else:
            state.setdefault("conflicts", []).append(
                {
                    "type": "DETAIL_CONFLICT",
                    "rwid": record_key,
                    "projectNo": project_no,
                    "existingFingerprint": existing.get("fingerprint"),
                    "observedFingerprint": fingerprint,
                }
            )
            state["status"] = "NEEDS_MANUAL_REVIEW"
            _write_json(capture_path, state)
            raise SourceIntakeError("同一 RWID 的详情发生变化，已转人工处理")
    screenshot_source = (
        Path(screenshot if screenshot is not None else screenshot_path)
        if (screenshot is not None or screenshot_path is not None)
        else None
    )
    screenshot_record = None
    screenshot_observations = list((existing or {}).get("screenshotObservations") or [])
    if screenshot_source is not None:
        if not screenshot_source.is_file():
            raise SourceIntakeError("详情截图不存在")
        screenshot_sha = _sha256(screenshot_source)
        if existing and existing.get("screenshot"):
            stored_screenshot = _workspace_relative_path(
                layout, existing["screenshot"].get("relativePath"), "已登记详情截图"
            )
            if not stored_screenshot.is_file() or _sha256(stored_screenshot) != existing[
                "screenshot"
            ].get("sha256"):
                raise SourceIntakeError("已登记详情截图与本地证据无法对账")
            screenshot_record = existing["screenshot"]
            if screenshot_record["sha256"] != screenshot_sha:
                # Same structured detail was checked above. Clock, scroll and
                # session presentation changes do not mean the source case changed.
                pending = _case_evidence_dir(layout, state, project_no, batch_id)
                suffix = screenshot_source.suffix.lower() or ".png"
                target = pending / f"案卷详情_{screenshot_sha.removeprefix('sha256:')[:12]}{suffix}"
                _copy_immutable(screenshot_source, target)
                if not any(
                    item.get("sha256") == screenshot_sha for item in screenshot_observations
                ):
                    screenshot_observations.append(
                        {**_evidence_file(target, Path(layout.root)), "capturedAt": captured}
                    )
            remaining_conflicts = []
            for conflict in state.get("conflicts", []):
                if (
                    conflict.get("type") == "DETAIL_SCREENSHOT_CONFLICT"
                    and conflict.get("rwid") == record_key
                    and conflict.get("projectNo") == project_no
                ):
                    state.setdefault("resolvedConflicts", []).append(
                        {
                            **conflict,
                            "resolvedAt": captured,
                            "resolution": "IDENTICAL_DETAIL_VALID_PRIOR_SCREENSHOT",
                        }
                    )
                else:
                    remaining_conflicts.append(conflict)
            state["conflicts"] = remaining_conflicts
        else:
            pending = _case_evidence_dir(layout, state, project_no, batch_id)
            pending.mkdir(parents=True, exist_ok=True)
            suffix = screenshot_source.suffix.lower() if screenshot_source.suffix else ".png"
            target = pending / f"案卷详情_{screenshot_sha.removeprefix('sha256:')[:12]}{suffix}"
            _copy_immutable(screenshot_source, target)
            screenshot_record = _evidence_file(target, Path(layout.root))
    elif existing and existing.get("screenshot"):
        stored_screenshot = _workspace_relative_path(
            layout, existing["screenshot"].get("relativePath"), "已登记详情截图"
        )
        if not stored_screenshot.is_file() or _sha256(stored_screenshot) != existing[
            "screenshot"
        ].get("sha256"):
            raise SourceIntakeError("已登记详情截图与本地证据无法对账")
        screenshot_record = existing["screenshot"]
    else:
        raise SourceIntakeError("每个新处理案卷必须提供一张完整详情截图")
    notice_query = state["filters"].get("selectionMode") in {
        "ANNUAL_RECTIFICATION_NOTICE",
        "RECENT_DOCUMENT_ACTIVITY",
        "ANNUAL_CASE_BASELINE",
    }
    source_stages = [] if notice_query else list(record.get("inspectionStages") or ["INITIAL"])
    project_stages = (
        ["ANOMALY" if "ANOMALY" in source_stages else "RECHECK"]
        if alias_of and not notice_query
        else source_stages
    )
    stage_label = {"INITIAL": "初查", "RECHECK": "复查", "ANOMALY": "检查记录次数异常"}
    stage_tags = {stage_label[item] for item in project_stages}
    tags = sorted(set(tags) | stage_tags)
    detail_record: dict[str, Any] = {
        "projectNo": project_no,
        "capturedAt": captured,
        "sourceUrl": safe_url,
        "fields": clean_detail,
        "tags": tags,
        "sourceInspectionStages": source_stages,
        "projectInspectionStages": project_stages,
        "fingerprint": fingerprint,
        "businessFingerprint": business_fingerprint,
    }
    if record.get("anomalies"):
        detail_record["anomalies"] = list(record["anomalies"])
    if screenshot_record:
        detail_record["screenshot"] = screenshot_record
    if screenshot_observations:
        detail_record["screenshotObservations"] = screenshot_observations
    if alias_of:
        detail_record["aliasOf"] = alias_of
        record["aliasOf"] = alias_of
        state["actionRwids"] = [item for item in state.get("actionRwids", []) if item != record_key]
    record["projectNo"] = project_no
    record[detail_key] = detail_record
    if carried_supersede is not None:
        old_fingerprint = carried_supersede.pop("supersededFingerprint")
        record.setdefault("carriedFromSourceBatch", []).append(
            {
                **carried_supersede,
                "supersededAt": captured,
                "supersededByFingerprint": fingerprint,
                "supersededByScreenshotSha256": (screenshot_record or {}).get("sha256"),
            }
        )
        if "downloadDelivery" in carried_supersede:
            record.pop("downloadDelivery", None)
        remaining_conflicts = []
        for conflict in state.get("conflicts") or []:
            if (
                conflict.get("type") == "DETAIL_CONFLICT"
                and conflict.get("rwid") == record_key
                and conflict.get("projectNo") == project_no
                and conflict.get("existingFingerprint")
                and conflict.get("existingFingerprint") == old_fingerprint
            ):
                # 冲突原字段(含形成冲突时的 observedFingerprint)逐项原样保留；当前处理的新观察另列。
                state.setdefault("resolvedConflicts", []).append(
                    {
                        **copy.deepcopy(conflict),
                        "resolvedAt": captured,
                        "resolution": "CARRIED_DETAIL_SUPERSEDED_BY_VERIFIED_NEW_OBSERVATION",
                        "resolvedByObservedFingerprint": fingerprint,
                        "resolvedByScreenshotSha256": (screenshot_record or {}).get("sha256"),
                    }
                )
            else:
                remaining_conflicts.append(conflict)
        state["conflicts"] = remaining_conflicts
    evidence_path, evidence = _load_evidence(layout, project_no, batch_id, state=state)
    if batch_id not in evidence["batchIds"]:
        evidence["batchIds"].append(batch_id)
    evidence_record = evidence["records"].setdefault(record_key, {})
    prior_fingerprint = evidence_record.get("fingerprint")
    superseded = None
    if prior_fingerprint and prior_fingerprint != detail_record["fingerprint"]:
        if not annual_materials:
            raise SourceIntakeError("本地来源证据已有不同详情，拒绝覆盖")
        # 整包采集中的来源变化：旧观察原样保存到 previousObservations(新代际)，旧原图/旧批次原文不删不改。
        superseded = {k: v for k, v in evidence_record.items() if k != "previousObservations"}
        superseded["supersededAt"] = captured
    previous = list(evidence_record.get("previousObservations") or [])
    if superseded is not None:
        previous.append(superseded)
    evidence_record.update(detail_record)
    if previous:
        evidence_record["previousObservations"] = previous
    if alias_of and record.get("package"):
        evidence["records"].setdefault(alias_of, {"projectNo": project_no})["package"] = record[
            "package"
        ]
    evidence["tags"] = sorted(set(evidence.get("tags") or []) | set(tags))
    evidence["updatedAt"] = captured
    _write_json(evidence_path, evidence)
    source_fields: dict[str, Any] = {
        "batchId": batch_id,
        "rwid": record_key,
        "tags": tags,
        "capturedAt": captured,
        "lastObservedAt": captured,
        "observationsByRwid": {
            record_key: {
                "fingerprint": record.get("sourceRecordFingerprint"),
                "observedAt": captured,
                "documentFingerprints": sorted(
                    {document_fingerprint(a) for a in record.get("sourceAppearances", [])}
                ),
            }
        },
        "projectIdentitySource": "DETAIL",
        "sourceRecordFingerprint": record.get("sourceRecordFingerprint"),
        "detailBusinessFingerprint": business_fingerprint,
        "sourceInspectionStages": source_stages,
        "projectInspectionStages": project_stages,
        "sourceDocumentCount": len(record.get("sourceAppearances") or []) or 1,
    }
    if annual_materials:
        # Task-list appearances are identities, not observed legal documents.
        source_fields.pop("sourceDocumentCount")
        source_fields.pop("observationsByRwid")
    document_dates = [
        _find_first(
            a,
            {
                "createdate",
                "createdat",
                "creationdate",
                "documentdate",
                "创建日期",
                "创建时间",
                "createtime",
            },
        )
        for a in ([] if annual_materials else record.get("sourceAppearances", []))
    ]
    normalized_dates = [
        str(d)[:10] for d in document_dates if d and re.match(r"^\d{4}-\d{2}-\d{2}", str(d))
    ]
    if normalized_dates:
        source_fields["latestDocumentCreatedAt"] = max(normalized_dates)
    # Only typed initial results or exact structured, initial-only source checks.
    if __package__:
        from .ledger_views import source_qualification
    else:
        from ledger_views import source_qualification
    classification = source_qualification(clean_detail, project_stages)
    if classification is not None:
        source_fields["classification"] = {
            **classification,
            "evidencePath": str(evidence_path.relative_to(layout.root)),
            "evidenceSha256": _sha256(evidence_path),
            "observedAt": captured,
        }
        source_fields["indexOnly"] = classification["initialResult"] == "QUALIFIED"
        if source_fields["indexOnly"]:
            record["indexOnly"] = True
            record["skippedAsUnchanged"] = True
            state["actionRwids"] = [
                item for item in state.get("actionRwids", []) if item != record_key
            ]
    if record.get("anomalies"):
        source_fields["anomalies"] = list(record["anomalies"])
    if alias_of:
        source_fields["aliasRwid"] = record_key
        source_fields["canonicalRwid"] = alias_of
    address = _find_first(clean_detail, {"address", "unitaddress", "单位地址", "地址"})
    if address not in (None, ""):
        source_fields["address"] = str(address).strip()
    waterline_fields: dict[str, Any] = {
        "tags": tags,
        "source": source_fields,
    }
    unit_name = _find_first(clean_detail, _UNIT_NAME_KEYS)
    brigade_name = _find_first(
        clean_detail,
        {
            "brigade",
            "brigadename",
            "lawenforcementunit",
            "大队",
            "大队名称",
            "所属大队",
            "执法单位",
        },
    )
    brigade_code = _find_first(
        clean_detail,
        {"brigadecode", "brigadenumber", "大队代码", "大队编号"},
    )
    if unit_name not in (None, ""):
        waterline_fields["unitName"] = str(unit_name).strip()
    if brigade_name not in (None, ""):
        waterline_fields["brigadeName"] = str(brigade_name).strip()
    if brigade_code not in (None, ""):
        waterline_fields["brigadeCode"] = str(brigade_code).strip()
    if not _is_acceptance_sample(state):
        current_case = None
        if load_waterline is not None:
            current_case = (load_waterline(layout).get("cases") or {}).get(project_no)
        prior_tags = set((current_case or {}).get("tags") or [])
        waterline_fields["tags"] = sorted(prior_tags | set(tags))
        verified_completed = _verified_completed_waterline(layout, project_no)
        if verified_completed is not None:
            canonical_record = state["records"][alias_of] if alias_of else record
            canonical_record["skippedAsUnchanged"] = True
            canonical_record["skippedAsCompletedProject"] = True
            canonical_record["skipReason"] = "PROJECT_NO_ALREADY_VERIFIED"
            record["skippedAsCompletedProject"] = True
            state["actionRwids"] = [
                item for item in state.get("actionRwids", []) if item not in {record_key, alias_of}
            ]
            _maybe_waterline(layout, project_no, **waterline_fields)
        elif alias_of:
            _maybe_waterline(layout, project_no, **waterline_fields)
        else:
            existing_state = (current_case or {}).get("state")
            if existing_state in {
                "PENDING_ORGANIZATION",
                "READY_FOR_COMPOSE",
                "READY_FOR_UPLOAD",
                "READY_FOR_LOCAL_VERIFY",
                "PENDING_LOCAL_VERIFY",
                "LOCAL_VERIFIED_PENDING_ARCHIVE",
                "UPLOADING",
                "UPLOADED_PENDING_NAS_VERIFY",
            }:
                record["resumesExistingProject"] = True
            else:
                waterline_fields["state"] = "DETAIL_CAPTURED"
            _maybe_waterline(layout, project_no, **waterline_fields)
    _refresh_progress(state)
    state["updatedAt"] = captured
    _write_json(capture_path, state)
    return state


FOLLOWUP_OBSERVATION_VERSION = "SourceFollowupObservationV1"
FOLLOWUP_CLOCK_TOLERANCE_SECONDS = 300


def _parse_observed_at(value: Any) -> datetime:
    """A real observation time must carry its own zone offset (kept verbatim); naive or future times are refused."""

    if not isinstance(value, str) or not value.strip():
        raise SourceIntakeError("后续观察必须提供真实观察时间")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceIntakeError("后续观察时间格式无效") from error
    if parsed.tzinfo is None:
        raise SourceIntakeError("后续观察时间必须带时区偏移，不按本地处理时刻推断")
    if parsed.timestamp() > datetime.now(SHANGHAI).timestamp() + FOLLOWUP_CLOCK_TOLERANCE_SECONDS:
        raise SourceIntakeError("后续观察时间晚于当前时刻，拒绝")
    return parsed


_BRIGADE_KEYS = {
    "brigade", "brigadename", "lawenforcementunit", "大队", "大队名称", "所属大队", "执法单位", "消防管辖", "消防管辖单位",
}


def detail_brigade(fields: Any) -> str | None:
    """Fire-jurisdiction (brigade) text of a detail, whitespace-insensitive, or None when the detail shows none."""

    value = _find_first(fields, _BRIGADE_KEYS) if isinstance(fields, dict) else None
    compact = re.sub(r"\s+", "", str(value)) if value not in (None, "") else ""
    return compact or None


def _expand_unknown_nodes(roots: list[dict]) -> list[tuple[dict, bool]]:
    """Every node the reader marked ``expandUnknown``, with whether it is a top-level root."""

    found: list[tuple[dict, bool]] = []

    def walk(rows: Any, top: bool) -> None:
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                if row.get("expandUnknown") is True:
                    found.append((row, top))
                walk(row.get("children"), False)

    walk(roots, True)
    return found


def followup_integrity_problem(fields: Any, project: str) -> str | None:
    """Own-tree directory coverage of a follow-up observation, derived from the saved reading itself.

    ``文书目录完整性`` is the page reader's own count (rendered rows, expandable/collapsed nodes, rows without an expand
    control). A follow-up is a trustworthy picture of the case only when its own [产] tree is provably fully read: no
    collapsed node, no scroll overflow, the case's own [产] root has content, and every unknown-expansion row is accounted
    for by a leafless top-level root of *another* project (the reader counts such a root as unknown because it has neither
    an expand icon nor an indent placeholder). The reader's ``complete``/``coverageProven`` flags are never rewritten and
    an unaccounted unknown row, a leafless own root, a collapsed node or a missing reading is refused.
    """

    integrity = fields.get("文书目录完整性") if isinstance(fields, dict) else None
    tree = fields.get("文书目录") if isinstance(fields, dict) else None
    if not isinstance(integrity, dict) or not isinstance(tree, list) or not tree:
        return "INTEGRITY_MISSING"
    if integrity.get("scrollOverflow"):
        return "SCROLL_OVERFLOW"
    try:
        collapsed = int(integrity.get("collapsedNodes") or 0)
        unknown = int(integrity.get("unknownExpandRows") or 0)
    except (TypeError, ValueError):
        return "INTEGRITY_MISSING"
    if collapsed > 0:
        return "OWN_TREE_COLLAPSED"
    roots = [row for row in tree if isinstance(row, dict)]
    own = [row for row in roots if str(row.get("title", "")).lstrip().startswith("[产]") and project in str(row.get("title", ""))]
    if not own or any(not row.get("children") for row in own):
        return "OWN_TREE_UNKNOWN"
    if unknown == 0:
        return None if integrity.get("complete") is True else "COVERAGE_NOT_PROVEN"
    # 未知展开行必须由读数器逐行标记(expandUnknown)，且每个标记恰是他案无子项的顶层根；数量对不上、标记落在
    # 本案树内/子级、或没有任何标记(旧读数无法归属)一律拒绝，不由“外案空根数量”推断归属。
    marked = _expand_unknown_nodes(roots)
    if len(marked) != unknown:
        return "OWN_TREE_UNKNOWN"
    for node, top in marked:
        if not top or node.get("children") or any(node is row for row in own) or project in str(node.get("title", "")):
            return "OWN_TREE_UNKNOWN"
    return None


def followup_brigade_problem(observed_fields: Any, baselines: list[str | None]) -> str | None:
    """Fourth case identity of a follow-up observation: its fire jurisdiction must be shown and equal every known baseline.

    ``baselines`` are the jurisdictions already established for the bound case (bound detail, still-trusted classification
    evidence, ledger). A follow-up that shows none, shows another one, or has nothing to be reconciled against is not
    a trustworthy observation of this case.
    """

    observed = detail_brigade(observed_fields)
    if observed is None:
        return "BRIGADE_MISSING"
    known = [re.sub(r"\s+", "", str(item)) for item in baselines if item not in (None, "")]
    if not known:
        return "BRIGADE_BASELINE_UNKNOWN"
    if any(item != observed for item in known):
        return "BRIGADE_MISMATCH"
    return None


def _normalized_sha(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if text.startswith("sha256:") else "sha256:" + text


def _registered_package_proof(layout: Any, project_no: str, record_key: str) -> dict[str, Any] | None:
    """A package the case ledger already registered for this RWID, proven on disk (None when the ledger holds none).

    A batch capture can lack ``package`` although the case's package was received earlier (another batch, annual
    baseline). The package itself is the evidence of receipt: it must exist inside the business root, its size and
    SHA-256 must equal what the ledger recorded, the ledger must bind this RWID to the case, and its generation must be
    stated. Anything else raises; nothing is downloaded, rewritten or re-bound.
    """

    if load_waterline is None:
        return None
    source = ((load_waterline(layout).get("cases") or {}).get(project_no) or {}).get("source") or {}
    expected, relative = _normalized_sha(source.get("packageSha256")), source.get("packageRelativePath")
    if not expected or not relative:
        return None
    known = {str(source["rwid"])} if source.get("rwid") else set()
    for key in ("observationsByRwid", "pendingObservationsByRwid", "annualIdentitiesByRwid"):
        known |= {str(item) for item in (source.get(key) or {})}
    if record_key not in known:
        raise SourceIntakeError("台账已登记的案卷包与该 RWID 的身份不一致，拒绝后续观察")
    generation = source.get("packageGeneration")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise SourceIntakeError("台账已登记案卷包的代际无法证明，拒绝后续观察")
    root = Path(layout.root).resolve()
    package = (root / str(relative)).resolve()
    if root not in package.parents or not package.is_file():
        raise SourceIntakeError("台账已登记的案卷包不在业务根内或实物缺失，拒绝后续观察")
    actual = _sha256(package)
    if actual != expected:
        raise SourceIntakeError("台账已登记的案卷包实物摘要与水位记录不符，拒绝后续观察")
    return {
        "sha256": actual,
        "relativePath": str(relative),
        "sizeBytes": package.stat().st_size,
        "generation": generation,
        "basis": "LEDGER_REGISTERED_PACKAGE",
    }


def _verify_followup_on_disk(layout: Any, evidence_path: Path, stored: dict[str, Any], entry: dict[str, Any]) -> None:
    """A retry may only complete a follow-up whose saved evidence is still exactly what its capture entry recorded."""

    if _sha256(evidence_path) != entry.get("evidenceSha256"):
        raise SourceIntakeError("已落盘的后续观察证据与批次登记摘要不一致，无法补齐，拒绝")
    for item in stored.get("screenshots") or []:
        target = Path(layout.root) / str(item.get("relativePath"))
        if not target.is_file() or _sha256(target) != item.get("sha256"):
            raise SourceIntakeError("已落盘的后续观察截图缺失或已变化，无法补齐，拒绝")


def _followup_ledger_registered(layout: Any, project_no: str, observation_id: str, entry: dict[str, Any]) -> bool:
    if load_waterline is None:
        return True
    registered = (
        (((load_waterline(layout).get("cases") or {}).get(project_no) or {}).get("source") or {}).get("followupObservations")
        or {}
    ).get(observation_id)
    return isinstance(registered, dict) and all(registered.get(key) == value for key, value in entry.items())


def _register_followup_in_ledger(
    layout: Any, project_no: str, record_key: str, batch_id: str, entry: dict[str, Any], observed: datetime
) -> None:
    ledger_source: dict[str, Any] = {
        "followupObservations": {entry["observationId"]: {**entry, "rwid": record_key, "batchId": batch_id}},
    }
    current_case = (load_waterline(layout).get("cases") or {}).get(project_no) if load_waterline is not None else None
    last_observed = ((current_case or {}).get("source") or {}).get("lastObservedAt")
    if not last_observed or datetime.fromisoformat(str(last_observed).replace("Z", "+00:00")).timestamp() < observed.timestamp():
        ledger_source["lastObservedAt"] = entry["observedAtShanghai"]
    _maybe_waterline(layout, project_no, source=ledger_source)


def add_followup_observation(
    workspace: Any,
    batch_id: str,
    rwid: str,
    detail: dict[str, Any] | str | Path,
    source_url: str | None = None,
    screenshot: str | Path | list[str | Path] | tuple[str | Path, ...] | None = None,
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Save a new real, same-case observation of a case whose package is already bound, without touching the bound material.

    ``add_detail`` refuses a changed detail for a package-bound record (the bound detail, package and download
    delivery must stay together). This keeps all of that byte-for-byte and stores the new observation as its own
    immutable evidence file (``SourceFollowupObservationV1``: full visible fields, real observation time with its zone,
    detail screenshots) next to a ledger entry under ``source.followupObservations``. The ledger's current detail
    binding, classification, package, completion proof and archive generations are not rewritten, so a still-trusted old
    classification can no longer hide the newer source fact: ``ledger_views.describe`` reads the newest trusted one.

    Identity gates are those of ``add_detail`` (project number, RWID in the detail URL, unit name against the list's case
    name) but any mismatch raises without writing anything. The same observation (same RWID, instant and content)
    is idempotent; a later revisit with identical facts is a new observation with its own time. A conflict is released
    only when its recorded old and observed fingerprints are exactly this record's detail and this observation.
    """

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("详情 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    package_proof: dict[str, Any] | None = None
    if record.get("package"):
        ledger_sha = _normalized_sha(
            ((((load_waterline(layout).get("cases") or {}).get(str(record.get("projectNo"))) or {}).get("source") or {}).get("packageSha256"))
            if load_waterline is not None
            else None
        )
        if ledger_sha and ledger_sha != _normalized_sha(record["package"].get("sha256")):
            raise SourceIntakeError("批次绑定的案卷包与台账登记的包代际不一致，拒绝后续观察")
    else:
        package_proof = _registered_package_proof(layout, str(record.get("projectNo") or ""), record_key)
        if package_proof is None:
            raise SourceIntakeError("该 RWID 尚未绑定案卷包，首次详情请使用 add-detail")
    existing = record.get("collectionDetail")
    if not isinstance(existing, dict) or not existing.get("fingerprint") or not existing.get("capturedAt"):
        raise SourceIntakeError("已绑包记录缺少可对账的整包详情，拒绝后续观察")
    if not _material_collection_active(layout, state, record_key, record):
        raise SourceIntakeError("该案没有有效的整包采集请求，拒绝后续观察")
    observed = _parse_observed_at(observed_at)
    if observed.timestamp() < datetime.fromisoformat(str(existing["capturedAt"])).timestamp():
        raise SourceIntakeError("后续观察时间早于已绑定详情的观察时间，拒绝")
    clean_detail = _clean_evidence(_json_input(detail, "案卷详情"))
    project_raw = _find_first(clean_detail, _PROJECT_KEYS)
    if project_raw in (None, ""):
        raise SourceIntakeError("详情未显式提供项目编号")
    project_no = _require_project_no(str(project_raw).strip().upper())
    if record.get("projectNo") != project_no or existing.get("projectNo") not in (None, project_no):
        raise SourceIntakeError("详情项目编号与已绑定案卷不一致")
    safe_url = sanitize_source_url(source_url) if source_url else None
    if not safe_url or _source_url_rwid(safe_url) != record_key:
        raise SourceIntakeError("详情 URL 缺失或其 RWID 与记录不一致")
    expected_names = {
        item for value in _list_case_names(record) if (item := _normalized_case_name(value)) is not None
    }
    actual_name = _normalized_case_name(_find_first(clean_detail, _UNIT_NAME_KEYS))
    if expected_names and actual_name not in expected_names:
        raise SourceIntakeError("详情单位名称与本次清单案卷名称不一致")
    if __package__:
        from .ledger_views import _product_root_problem
    else:
        from ledger_views import _product_root_problem
    if (root_problem := _product_root_problem(clean_detail, project_no)):
        raise SourceIntakeError(f"详情没有本案 [产] 产品根，不能作为本案观察：{root_problem}")
    ledger_case = (load_waterline(layout).get("cases") or {}).get(project_no) if load_waterline is not None else None
    baseline_brigades = [detail_brigade(existing.get("fields"))]
    if baseline_brigades[0] is None and ledger_case:
        baseline_brigades = [(ledger_case or {}).get("brigadeName")]
    if (integrity_problem := followup_integrity_problem(clean_detail, project_no)):
        raise SourceIntakeError(f"详情的本案文书目录覆盖不能证明，不能作为本案观察：{integrity_problem}")
    if (brigade_problem := followup_brigade_problem(clean_detail, baseline_brigades)):
        raise SourceIntakeError(f"详情消防管辖与已绑定案卷不一致或缺失，不能作为本案观察：{brigade_problem}")
    shots = [screenshot] if isinstance(screenshot, (str, Path)) else list(screenshot or [])
    if not shots:
        raise SourceIntakeError("后续观察必须提供至少一张完整详情截图")
    shot_paths = [Path(item) for item in shots]
    for path in shot_paths:
        if not path.is_file():
            raise SourceIntakeError("详情截图不存在")
    shot_shas = sorted({_sha256(path) for path in shot_paths})
    fingerprint = _fingerprint(clean_detail)
    observed_shanghai = _timestamp(observed.astimezone(SHANGHAI))
    observation_id = "obs:" + hashlib.sha256(
        json.dumps(
            {"rwid": record_key, "observedInstant": observed_shanghai, "fingerprint": fingerprint},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    directory = layout.batch_dir(batch_id) / "followup-observations" / project_no
    evidence_path = directory / f"{record_key}-{observation_id[4:20]}.json"
    existing_entries = [
        item for item in record.get("followupObservations") or [] if item.get("observationId") == observation_id
    ]
    if evidence_path.exists():
        stored = _read_json(evidence_path, "后续观察证据")
        stored_shas = sorted(item.get("sha256") for item in stored.get("screenshots") or [])
        if (
            stored.get("schemaVersion") != FOLLOWUP_OBSERVATION_VERSION
            or stored.get("observationId") != observation_id
            or stored.get("fingerprint") != fingerprint
            or stored_shas != shot_shas
        ):
            raise SourceIntakeError("同一观察已登记但证据不一致，拒绝覆盖")
        if existing_entries:
            # 同一观察、同一请求重放：证据与 capture 已落盘，只核实并补齐可能缺失的台账一侧，不重复追加
            _verify_followup_on_disk(layout, evidence_path, stored, existing_entries[0])
            if not _followup_ledger_registered(layout, project_no, observation_id, existing_entries[0]):
                _register_followup_in_ledger(layout, project_no, record_key, batch_id, existing_entries[0], observed)
            return state
    shot_records = []
    for path in sorted(shot_paths, key=lambda item: _sha256(item)):
        sha = _sha256(path)
        suffix = path.suffix.lower() or ".png"
        target = directory / f"{record_key}-{observation_id[4:20]}-案卷详情_{sha.removeprefix('sha256:')[:12]}{suffix}"
        _copy_immutable(path, target)
        shot_records.append({**_evidence_file(target, Path(layout.root))})
    shot_records = list({item["sha256"]: item for item in shot_records}.values())
    prior = {
        "fingerprint": existing.get("fingerprint"),
        "businessFingerprint": existing.get("businessFingerprint"),
        "capturedAt": existing.get("capturedAt"),
        "screenshotSha256": (existing.get("screenshot") or {}).get("sha256"),
        "packageSha256": (record.get("package") or package_proof or {}).get("sha256"),
    }
    evidence = {
        "schemaVersion": FOLLOWUP_OBSERVATION_VERSION,
        "observationId": observation_id,
        "projectNo": project_no,
        "rwid": record_key,
        "batchId": batch_id,
        "observedAt": observed_at.strip(),
        "observedAtShanghai": observed_shanghai,
        "observedDay": observed_shanghai[:10],
        "sourceUrl": safe_url,
        "fields": clean_detail,
        "tags": _derive_tags(clean_detail),
        "fingerprint": fingerprint,
        "businessFingerprint": _business_detail_fingerprint(clean_detail),
        "screenshots": shot_records,
        "priorBoundDetail": prior,
        **({"registeredPackage": package_proof} if package_proof else {}),
    }
    if not evidence_path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(evidence_path, evidence)
    evidence_sha = _sha256(evidence_path)
    relative = str(evidence_path.relative_to(layout.root))
    entry = {
        "observationId": observation_id,
        "observedAt": evidence["observedAt"],
        "observedAtShanghai": observed_shanghai,
        "fingerprint": fingerprint,
        "businessFingerprint": evidence["businessFingerprint"],
        "evidencePath": relative,
        "evidenceSha256": evidence_sha,
    }
    record.setdefault("followupObservations", [])
    if not existing_entries:
        record["followupObservations"].append(entry)
    remaining = []
    for conflict in state.get("conflicts") or []:
        if (
            conflict.get("type") == "DETAIL_CONFLICT"
            and conflict.get("rwid") == record_key
            and conflict.get("projectNo") == project_no
            and conflict.get("existingFingerprint") == existing.get("fingerprint")
            and conflict.get("observedFingerprint") == fingerprint
        ):
            state.setdefault("resolvedConflicts", []).append(
                {
                    **copy.deepcopy(conflict),
                    "resolvedAt": observed_shanghai,
                    "resolution": "FOLLOWUP_OBSERVATION_RECORDED_BOUND_MATERIAL_KEPT",
                    "resolvedByObservationId": observation_id,
                }
            )
        else:
            remaining.append(conflict)
    state["conflicts"] = remaining
    _refresh_progress(state)
    state["updatedAt"] = observed_shanghai
    _write_json(capture_path, state)
    _register_followup_in_ledger(layout, project_no, record_key, batch_id, entry, observed)
    return state


def _zip_member_name(info: zipfile.ZipInfo) -> tuple[str, str]:
    name = info.filename
    if not name or "\\" in name or "\0" in name:
        raise SourceIntakeError("ZIP 包含不安全路径")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise SourceIntakeError("ZIP 包含绝对路径")
    raw = name[:-1] if info.is_dir() and name.endswith("/") else name
    parts = PurePosixPath(raw).parts
    if not raw or any(part in {"", ".", ".."} for part in parts) or "//" in raw:
        raise SourceIntakeError("ZIP 包含路径穿越或空路径段")
    normalized_parts = [unicodedata.normalize("NFC", part) for part in parts]
    for part in normalized_parts:
        if ":" in part or part.endswith((".", " ")) or any(ord(char) < 32 for char in part):
            raise SourceIntakeError("ZIP 包含 Windows 不安全路径段")
        device_stem = part.split(".", 1)[0].rstrip(" .").upper()
        if device_stem in WINDOWS_DEVICE_NAMES:
            raise SourceIntakeError("ZIP 包含 Windows 设备名路径")
    normalized = PurePosixPath(*normalized_parts).as_posix()
    return normalized, normalized.casefold()


def inspect_zip_package(
    zip_path: str | Path,
    *,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_member_bytes: int = MAX_ZIP_MEMBER_BYTES,
    max_total_bytes: int = MAX_ZIP_TOTAL_BYTES,
    max_ratio: float = MAX_ZIP_RATIO,
) -> dict[str, Any]:
    """只读验证 ZIP 目录，返回可审计摘要。"""

    archive = Path(zip_path)
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise SourceIntakeError("下载文件不是完整 ZIP")
    seen: dict[str, str] = {}
    file_keys: set[str] = set()
    total = 0
    members: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(archive) as source:
            infos = source.infolist()
            if not infos:
                raise SourceIntakeError("ZIP 为空")
            if len(infos) > max_entries:
                raise SourceIntakeError(f"ZIP 条目超过 {max_entries} 个")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise SourceIntakeError("ZIP 包含加密条目")
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or (info.external_attr & 0x400):
                    raise SourceIntakeError("ZIP 不允许符号链接或重解析点")
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise SourceIntakeError("ZIP 包含非常规文件")
                normalized, key = _zip_member_name(info)
                if key in seen:
                    raise SourceIntakeError(
                        f"ZIP 存在大小写或 Unicode 规范化重复路径：{normalized}"
                    )
                for parent in PurePosixPath(normalized).parents:
                    if parent.as_posix() != "." and parent.as_posix().casefold() in file_keys:
                        raise SourceIntakeError("ZIP 文件与子路径发生冲突")
                if not info.is_dir() and any(seen_key.startswith(key + "/") for seen_key in seen):
                    raise SourceIntakeError("ZIP 文件与已有子路径发生冲突")
                seen[key] = normalized
                if not info.is_dir():
                    file_keys.add(key)
                    if normalized.casefold().endswith(".zip"):
                        raise SourceIntakeError("ZIP 包含嵌套 ZIP，需转人工处理")
                    if info.file_size > max_member_bytes:
                        raise SourceIntakeError("ZIP 单个文件超过 512 MiB")
                    total += info.file_size
                    if total > max_total_bytes:
                        raise SourceIntakeError("ZIP 解压总量超过 2 GiB")
                    if info.file_size and info.compress_size == 0:
                        raise SourceIntakeError("ZIP 包含异常零压缩大小条目")
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > max_ratio:
                        raise SourceIntakeError("ZIP 压缩比超过 200，疑似压缩炸弹")
                    with source.open(info, "r") as member:
                        if zipfile.is_zipfile(member) and not is_safe_docx_package(
                            member, filename=normalized, max_entries=max_entries,
                            max_member_bytes=max_member_bytes, max_total_bytes=max_total_bytes,
                            max_ratio=max_ratio,
                        ):
                            raise SourceIntakeError("ZIP 包含嵌套 ZIP，需转人工处理")
                members.append(
                    {
                        "name": normalized,
                        "isDirectory": info.is_dir(),
                        "sizeBytes": info.file_size,
                        "compressedBytes": info.compress_size,
                    }
                )
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        raise SourceIntakeError(f"ZIP 无法安全读取：{error}") from error
    return {
        "entryCount": len(members),
        "fileCount": sum(not item["isDirectory"] for item in members),
        "totalSizeBytes": total,
        "members": members,
    }


def safe_extract_package(
    zip_path: str | Path,
    destination: str | Path,
    *,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_member_bytes: int = MAX_ZIP_MEMBER_BYTES,
    max_total_bytes: int = MAX_ZIP_TOTAL_BYTES,
    max_ratio: float = MAX_ZIP_RATIO,
) -> dict[str, Any]:
    """先完整验证，再解压到临时目录并原子发布；绝不覆盖目标。"""

    archive = Path(zip_path).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise SourceIntakeError("ZIP 解压目标已存在，拒绝覆盖")
    summary = inspect_zip_package(
        archive,
        max_entries=max_entries,
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
        max_ratio=max_ratio,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=target.name + ".extract-", dir=target.parent))
    written_total = 0
    try:
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                normalized, _ = _zip_member_name(info)
                output = temporary.joinpath(*PurePosixPath(normalized).parts)
                try:
                    output.resolve().relative_to(temporary.resolve())
                except ValueError as error:
                    raise SourceIntakeError("ZIP 解压路径越界") from error
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=False)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                member_written = 0
                with source.open(info, "r") as incoming, output.open("xb") as outgoing:
                    while True:
                        chunk = incoming.read(
                            min(1024 * 1024, max_member_bytes + 1 - member_written)
                        )
                        if not chunk:
                            break
                        member_written += len(chunk)
                        written_total += len(chunk)
                        if member_written > max_member_bytes or written_total > max_total_bytes:
                            raise SourceIntakeError("ZIP 实际解压量超过安全上限")
                        outgoing.write(chunk)
                if member_written != info.file_size:
                    raise SourceIntakeError("ZIP 条目实际大小与目录声明不一致")
        for extracted_file in temporary.rglob("*"):
            if (
                extracted_file.is_file() and zipfile.is_zipfile(extracted_file)
                and not is_safe_docx_package(
                    extracted_file, max_entries=max_entries, max_member_bytes=max_member_bytes,
                    max_total_bytes=max_total_bytes, max_ratio=max_ratio,
                )
            ):
                raise SourceIntakeError("ZIP 包含嵌套 ZIP，需转人工处理")
        os.rename(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "destination": str(target),
        "entryCount": summary["entryCount"],
        "fileCount": summary["fileCount"],
        "totalSizeBytes": summary["totalSizeBytes"],
    }


def _workspace_relative_path(layout: Any, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise SourceIntakeError(f"{label}相对路径不合法")
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise SourceIntakeError(f"{label}相对路径不安全")
    root = Path(layout.root).resolve()
    result = root.joinpath(*parts).resolve()
    try:
        result.relative_to(root)
    except ValueError as error:
        raise SourceIntakeError(f"{label}路径越出工作根") from error
    return result


def _resolve_zip(
    value: str | Path,
    baseline: dict[str, Any] | None,
    *,
    stability_checks: int,
    stability_interval: float,
    sleep_fn: Any,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    source = Path(value)
    if source.is_dir():
        if baseline is None:
            raise SourceIntakeError("下载路径为目录时必须提供下载前基线")
        baseline_by_name = {item["name"].casefold(): item for item in baseline["files"]}
        candidates = [
            item
            for item in source.iterdir()
            if item.is_file()
            and not item.name.casefold().endswith(PARTIAL_SUFFIXES)
            and item.suffix.casefold() == ".zip"
        ]
        new_candidates = []
        for item in candidates:
            old = baseline_by_name.get(item.name.casefold())
            current_stat = item.stat()
            if old is None:
                new_candidates.append(item)
                continue
            unchanged = (old["sizeBytes"], old["mtimeNs"]) == (
                current_stat.st_size,
                current_stat.st_mtime_ns,
            )
            if not unchanged and old.get("sha256"):
                unchanged = old["sha256"] == _sha256(item)
            if not unchanged:
                new_candidates.append(item)
        candidates = new_candidates
        if len(candidates) != 1:
            raise SourceIntakeError("下载目录相对基线必须恰好新增一个完整 ZIP")
        source = candidates[0]
    if not source.is_file():
        raise SourceIntakeError("下载 ZIP 不存在")
    if source.name.casefold().endswith(PARTIAL_SUFFIXES):
        raise SourceIntakeError("下载尚未完成")
    if baseline is not None:
        baseline_by_name = {item["name"].casefold(): item for item in baseline["files"]}
        old = baseline_by_name.get(source.name.casefold())
        if old is not None:
            current_stat = source.stat()
            unchanged = (old["sizeBytes"], old["mtimeNs"]) == (
                current_stat.st_size,
                current_stat.st_mtime_ns,
            )
            if not unchanged and old.get("sha256"):
                unchanged = old["sha256"] == _sha256(source)
            if unchanged:
                raise SourceIntakeError("候选 ZIP 在下载前基线中已存在且未变化")
    if stability_checks < 3 or stability_interval < 0:
        raise SourceIntakeError("下载稳定性至少需要 3 次非负间隔检查")
    observations = []
    for index in range(stability_checks):
        stat_value = source.stat()
        observations.append((stat_value.st_size, stat_value.st_mtime_ns))
        if index + 1 < stability_checks:
            sleep_fn(stability_interval)
    if observations[0][0] <= 0 or len(set(observations)) != 1:
        raise SourceIntakeError("下载文件仍在变化")
    inspection = inspect_zip_package(source)
    selection = {
        "baselineFingerprint": baseline.get("fingerprint") if baseline else None,
        "candidate": {
            "name": source.name,
            "sizeBytes": observations[-1][0],
            "mtimeNs": observations[-1][1],
        },
        "stabilityChecks": stability_checks,
    }
    return source, selection, inspection


def _validate_configured_download_path(
    download_path: str | Path,
    allowed_download_dir: str | Path | None,
) -> Path:
    """只允许配置下载目录本身，或其中一个直接子文件。"""

    if allowed_download_dir is None:
        raise SourceIntakeError("必须提供已配置的浏览器下载目录")
    allowed = Path(allowed_download_dir).expanduser()
    if not allowed.is_absolute() or not allowed.is_dir():
        raise SourceIntakeError("已配置的浏览器下载目录不存在")
    allowed = allowed.resolve()
    candidate = Path(download_path).expanduser()
    if not candidate.is_absolute():
        raise SourceIntakeError("下载路径必须是绝对路径")
    resolved = candidate.resolve()
    if resolved.is_dir():
        if resolved != allowed:
            raise SourceIntakeError("下载目录必须与工作配置完全一致")
    elif resolved.parent != allowed:
        raise SourceIntakeError("下载文件必须是已配置下载目录的直接子文件")
    return resolved


def _sync_attached_package(
    layout: Any,
    capture_path: Path,
    state: dict[str, Any],
    batch_id: str,
    record_key: str,
    project_no: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    evidence_path, evidence = _load_evidence(layout, project_no, batch_id, state=state)
    classification = None
    if load_waterline is not None and evidence_path.is_file():
        ledger_source = (load_waterline(layout).get("cases") or {}).get(project_no, {}).get(
            "source", {}
        )
        prior = ledger_source.get("classification") or {}
        if (
            prior.get("evidencePath") == str(evidence_path.relative_to(layout.root))
            and prior.get("evidenceSha256") == _sha256(evidence_path)
        ):
            classification = dict(prior)
    if batch_id not in evidence["batchIds"]:
        evidence["batchIds"].append(batch_id)
    evidence_record = evidence["records"].setdefault(record_key, {"projectNo": project_no})
    prior_package = evidence_record.get("package")
    if prior_package and prior_package.get("sha256") != package["sha256"]:
        if package.get("supersedes") != prior_package.get("sha256"):
            raise SourceIntakeError("本地来源证据已绑定不同案卷包，拒绝覆盖")
        evidence_record.setdefault("previousPackages", []).append(
            {key: prior_package.get(key) for key in ("sha256", "relativePath", "attachedAt", "generation")}
        )
    evidence_record["package"] = package
    evidence["updatedAt"] = package["attachedAt"]
    _write_json(evidence_path, evidence)
    if classification is not None:
        # Package metadata changes the file digest, not its original finding/date.
        classification["evidenceSha256"] = _sha256(evidence_path)

    record = state["records"][record_key]
    record["projectNo"] = project_no
    record["package"] = package
    remaining_conflicts = []
    resolved_conflicts = state.setdefault("resolvedConflicts", [])
    for conflict in state.get("conflicts") or []:
        if conflict.get("rwid") == record_key and (
            conflict.get("type") == "PACKAGE_REJECTED"
            or (conflict.get("type") == "PACKAGE_CONFLICT" and package.get("supersedes"))
        ):
            resolution = {**conflict, "resolvedAt": package["attachedAt"]}
            if resolution not in resolved_conflicts:
                resolved_conflicts.append(resolution)
        else:
            remaining_conflicts.append(conflict)
    state["conflicts"] = remaining_conflicts
    _refresh_progress(state)
    state["updatedAt"] = package["attachedAt"]
    _write_json(capture_path, state)
    if not _is_acceptance_sample(state):
        _maybe_waterline(
            layout,
            project_no,
            state="PENDING_ORGANIZATION",
            source={
                "status": "PACKAGE_READY",
                "batchId": batch_id,
                "rwid": record_key,
                "packageSha256": package["sha256"],
                "packageRelativePath": package["relativePath"],
                "downloadSelection": package["downloadSelection"],
                "downloadDisposition": package.get("downloadDisposition"),
                "packageGeneration": package.get("generation") or 1,
                "packageSupersedes": package.get("supersedes"),
                **({"classification": classification} if classification is not None else {}),
            },
            local={
                "status": "PENDING_ORGANIZATION",
            },
        )
    return state


def _sync_and_cleanup_download_copy(
    layout: Any,
    capture_path: Path,
    state: dict[str, Any],
    batch_id: str,
    record_key: str,
    project_no: str,
    package: dict[str, Any],
    source: Path,
    stored: Path,
) -> dict[str, Any]:
    """先持久化工作目录归档，再按哈希清理唯一对应的下载副本。"""

    package["downloadDisposition"] = {"status": "PENDING_SOURCE_REMOVAL"}
    _sync_attached_package(layout, capture_path, state, batch_id, record_key, project_no, package)
    package["downloadDisposition"] = _remove_confirmed_download_copy(
        source, stored, package["sha256"]
    )
    return _sync_attached_package(
        layout, capture_path, state, batch_id, record_key, project_no, package
    )


def attach_package(
    workspace: Any,
    batch_id: str,
    rwid: str,
    download_path: str | Path,
    *,
    original_name: str | None = None,
    project_no: str | None = None,
    download_baseline: dict[str, Any] | list[dict[str, Any]] | str | Path | None = None,
    allowed_download_dir: str | Path | None = None,
    stability_checks: int = 3,
    stability_interval: float = 0.05,
    sleep_fn: Any = time.sleep,
    refresh_of_sha: str | None = None,
    refresh_reason: str | None = None,
) -> dict[str, Any]:
    """接收唯一完整 ZIP，安全检查后按项目号+哈希绑定原始包。

    refresh_of_sha/refresh_reason 仅用于受审计的显式更新：新包作为新一代接入，
    旧包字节与登记永久保留；缺理由、理由不合法或与当前代不符一律拒绝。
    """
    if (refresh_of_sha is None) != (refresh_reason is None):
        raise SourceIntakeError("显式更新必须同时给出被更新的包摘要和理由")
    if refresh_reason is not None and refresh_reason not in REFRESH_REASONS:
        raise SourceIntakeError("显式更新理由不在允许集合内")

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("案卷包 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    collecting_annual = _material_collection_active(layout, state, record_key, record)
    if record.get("skippedAsUnchanged") and not collecting_annual:
        raise SourceIntakeError("详情项目编号已在水位中完成并通过完成核验，无需重新下载")
    if collecting_annual and not record.get("collectionDetail"):
        raise SourceIntakeError("年度材料采集必须先保存本案完整详情与截图")
    expected_project = record.get("projectNo") or (record.get("detail") or {}).get("projectNo")
    selected_project = _require_project_no(str(project_no or expected_project or "").upper())
    if expected_project and expected_project != selected_project:
        raise SourceIntakeError("案卷包项目编号与清单不一致")
    baseline, baseline_path = _validate_bound_download_baseline(
        layout,
        batch_id,
        record_key,
        selected_project,
        download_baseline,
    )
    selected_download = _validate_configured_download_path(download_path, allowed_download_dir)
    try:
        source, download_selection, zip_inspection = _resolve_zip(
            selected_download,
            baseline,
            stability_checks=stability_checks,
            stability_interval=stability_interval,
            sleep_fn=sleep_fn,
        )
    except SourceIntakeError as error:
        rejection = {
            "type": "PACKAGE_REJECTED",
            "rwid": record_key,
            "projectNo": selected_project,
            "reason": str(error),
        }
        if rejection not in state.setdefault("conflicts", []):
            state["conflicts"].append(rejection)
        state["status"] = "NEEDS_MANUAL_REVIEW"
        state["updatedAt"] = _timestamp()
        _write_json(capture_path, state)
        raise
    sha = _sha256(source)
    existing = record.get("package")
    if existing and refresh_of_sha is not None and existing.get("sha256") == refresh_of_sha != sha:
        existing = None  # 显式更新本批记录自己的当前代包：走下方新一代接入，旧代原件与登记保留
    if existing:
        if existing.get("sha256") != sha:
            state.setdefault("conflicts", []).append(
                {
                    "type": "PACKAGE_CONFLICT",
                    "rwid": record_key,
                    "projectNo": selected_project,
                }
            )
            state["status"] = "NEEDS_MANUAL_REVIEW"
            _write_json(capture_path, state)
            raise SourceIntakeError("同一 RWID 已绑定不同案卷包，已转人工处理")
        consumption = _consume_download_baseline(baseline, baseline_path, sha)
        stored_existing = _workspace_relative_path(
            layout, existing.get("relativePath"), "已登记案卷包"
        )
        if not stored_existing.is_file() or _sha256(stored_existing) != sha:
            raise SourceIntakeError("已登记案卷包与本地原始证据无法对账")
        verified_inspection = inspect_zip_package(stored_existing)
        existing = dict(existing)
        existing.setdefault("downloadSelection", download_selection)
        existing.setdefault(
            "zipInspection",
            {
                "entryCount": verified_inspection["entryCount"],
                "fileCount": verified_inspection["fileCount"],
                "totalSizeBytes": verified_inspection["totalSizeBytes"],
            },
        )
        existing.setdefault("attachedAt", _timestamp())
        existing.setdefault(
            "downloadBaselineConsumption",
            {
                "baselineRelativePath": consumption["baselineRelativePath"],
                "consumedAt": consumption["consumedAt"],
            },
        )
        return _sync_and_cleanup_download_copy(
            layout,
            capture_path,
            state,
            batch_id,
            record_key,
            selected_project,
            existing,
            source,
            stored_existing,
        )
    consumption = _consume_download_baseline(baseline, baseline_path, sha)
    short = sha.removeprefix("sha256:")[:12]
    stored_name = f"{selected_project}_案卷包_{short}.zip"
    pending = _case_evidence_dir(layout, state, selected_project, batch_id)
    pending.mkdir(parents=True, exist_ok=True)
    stored = pending / stored_name
    _copy_immutable(source, stored)
    _, existing_evidence = _load_evidence(layout, selected_project, batch_id, state=state)
    prior_package = (existing_evidence.get("records", {}).get(record_key) or {}).get("package")
    generation_prior = None
    if prior_package and prior_package.get("sha256") != sha:
        if refresh_of_sha is None or prior_package.get("sha256") != refresh_of_sha:
            raise SourceIntakeError("来源证据已绑定不同案卷包，拒绝自动恢复")
        generation_prior, prior_package = prior_package, None
    elif refresh_of_sha is not None and not prior_package:
        raise SourceIntakeError("显式更新找不到被更新的当前代包，拒绝")
    attached_at = (prior_package or {}).get("attachedAt") or _timestamp()
    package = {
        "originalSuggestedName": (prior_package or {}).get("originalSuggestedName")
        or Path(original_name or source.name).name,
        "storedName": stored_name,
        "relativePath": stored.resolve().relative_to(Path(layout.root).resolve()).as_posix(),
        "sha256": sha,
        "sizeBytes": stored.stat().st_size,
        "zipInspection": {
            "entryCount": zip_inspection["entryCount"],
            "fileCount": zip_inspection["fileCount"],
            "totalSizeBytes": zip_inspection["totalSizeBytes"],
        },
        "attachedAt": attached_at,
        "downloadSelection": (prior_package or {}).get("downloadSelection") or download_selection,
        "downloadBaselineConsumption": (prior_package or {}).get("downloadBaselineConsumption")
        or {
            "baselineRelativePath": consumption["baselineRelativePath"],
            "consumedAt": consumption["consumedAt"],
        },
    }
    if generation_prior is not None:
        package["supersedes"] = generation_prior["sha256"]
        package["refreshReason"] = refresh_reason
        package["generation"] = int(generation_prior.get("generation") or 1) + 1
    return _sync_and_cleanup_download_copy(
        layout,
        capture_path,
        state,
        batch_id,
        record_key,
        selected_project,
        package,
        source,
        stored,
    )


def resolve_refreshed_package_conflicts(workspace: Any, batch_id: str, rwid: str) -> dict[str, Any]:
    """A PACKAGE_CONFLICT raised before an audited refresh is stale once the record holds the superseding generation."""

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key) or {}
    package = record.get("package") or {}
    if not package.get("supersedes"):
        raise SourceIntakeError("该记录当前包不是显式更新的新一代，无法确认冲突已过期")
    pending = [
        item
        for item in state.get("conflicts") or []
        if item.get("type") == "PACKAGE_CONFLICT" and item.get("rwid") == record_key
    ]
    if not pending:
        return {"status": "REUSED", "batchId": batch_id, "rwid": record_key, "resolved": 0}
    resolved_at = _timestamp()
    state["conflicts"] = [item for item in state["conflicts"] if item not in pending]
    resolved = state.setdefault("resolvedConflicts", [])
    resolved += [
        {**item, "resolvedAt": resolved_at, "resolvedBy": "REFRESHED_PACKAGE_SUPERSEDES"}
        for item in pending
    ]
    _refresh_progress(state)
    state["updatedAt"] = resolved_at
    _write_json(capture_path, state)
    return {"status": "RESOLVED", "batchId": batch_id, "rwid": record_key, "resolved": len(pending)}


def await_download(
    workspace: Any,
    batch_id: str,
    rwid: str,
    *,
    download_baseline: dict[str, Any] | list[dict[str, Any]] | str | Path,
    download_dir: str | Path,
    allowed_download_dir: str | Path | None,
    timeout_seconds: float = DEFAULT_DOWNLOAD_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_DOWNLOAD_POLL_SECONDS,
    stalled_after_seconds: float = DEFAULT_DOWNLOAD_STALL_SECONDS,
    attach: bool = False,
    stability_checks: int = 3,
    stability_interval: float = 1.0,
    sleep_fn: Any = time.sleep,
    monotonic_fn: Any = time.monotonic,
    refresh_of_sha: str | None = None,
    refresh_reason: str | None = None,
) -> dict[str, Any]:
    """等待来源系统异步生成包；可在唯一 ZIP 稳定后自动交给既有绑定门禁。"""

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("等待下载的 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    expected_project = record.get("projectNo") or (record.get("detail") or {}).get("projectNo")
    project_no = _require_project_no(str(expected_project or "").strip().upper())
    baseline, _ = _validate_bound_download_baseline(
        layout,
        batch_id,
        record_key,
        project_no,
        download_baseline,
    )
    selected_download_dir = _validate_configured_download_path(download_dir, allowed_download_dir)
    if not selected_download_dir.is_dir():
        raise SourceIntakeError("等待下载必须传入已配置的浏览器下载目录")
    result = wait_for_download_candidate(
        selected_download_dir,
        download_baseline=baseline,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        stalled_after_seconds=stalled_after_seconds,
        stability_checks=stability_checks,
        stability_interval=stability_interval,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
    )
    envelope = {
        "batchId": batch_id,
        "rwid": record_key,
        "projectNo": project_no,
        **result,
    }
    _record_download_delivery(
        layout,
        capture_path,
        state,
        record_key,
        project_no,
        result,
    )
    if result["status"] != "READY" or not attach:
        return envelope
    capture = attach_package(
        layout,
        batch_id,
        record_key,
        Path(str(result["downloadPath"])),
        original_name=str(result["originalSuggestedName"]),
        project_no=project_no,
        download_baseline=baseline,
        allowed_download_dir=selected_download_dir,
        stability_checks=stability_checks,
        stability_interval=stability_interval,
        sleep_fn=sleep_fn,
        refresh_of_sha=refresh_of_sha,
        refresh_reason=refresh_reason,
    )
    return {**envelope, "status": "ATTACHED", "capture": capture}


# CLI/测试可使用方案中的短名称；公开实现仍保留语义更明确的函数名。
begin = begin_capture
finalize = finalize_capture


__all__ = [
    "BROWSER_CAPTURE_VERSION",
    "DOWNLOAD_WAIT_VERSION",
    "DOWNLOAD_BASELINE_VERSION",
    "SOURCE_EVIDENCE_VERSION",
    "TAIL_CURSOR_VERSION",
    "SourceIntakeError",
    "add_detail",
    "admit_observed_material_case",
    "add_page",
    "attach_package",
    "await_download",
    "begin",
    "begin_capture",
    "capture_download_baseline",
    "finalize",
    "finalize_capture",
    "inspect_zip_package",
    "plan_tail_first_cursor",
    "record_download_baseline",
    "request_case_material_collection",
    "safe_extract_package",
    "sanitize_source_url",
    "wait_for_download_candidate",
]
