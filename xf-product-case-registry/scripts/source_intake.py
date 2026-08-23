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
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # 包导入与直接加载脚本两种方式都要可用。
    from .workspace_state import BusinessLayout, load_waterline, save_waterline, upsert_case
except (ImportError, ModuleNotFoundError):  # pragma: no cover - 由独立加载测试覆盖行为
    try:
        from workspace_state import (  # type: ignore[no-redef]
            BusinessLayout,
            load_waterline,
            save_waterline,
            upsert_case,
        )
    except (ImportError, ModuleNotFoundError):  # pragma: no cover
        BusinessLayout = None  # type: ignore[assignment,misc]
        load_waterline = None  # type: ignore[assignment]
        save_waterline = None  # type: ignore[assignment]
        upsert_case = None  # type: ignore[assignment]


BROWSER_CAPTURE_VERSION = "BrowserCaptureV1"
SOURCE_EVIDENCE_VERSION = "SourceEvidenceV1"
DOWNLOAD_BASELINE_VERSION = "DownloadBaselineV1"
TAIL_CURSOR_VERSION = "TailCursorV1"
DOWNLOAD_WAIT_VERSION = "DownloadWaitV1"
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


def _default_filters(value: datetime | str | None, explicit: dict[str, Any]) -> dict[str, Any]:
    current = datetime.fromisoformat(_timestamp(value))
    requested_year = explicit.get("year", current.year)
    if requested_year != current.year:
        raise SourceIntakeError("当前采集只允许上海时间本年范围")
    defaults: dict[str, Any] = {
        "year": requested_year,
        "startDate": f"{requested_year}-01-01",
        "endDate": current.date().isoformat(),
        "jurisdiction": "全部管辖单位(含派出所)",
        "brigadeScope": "ALL",
        "documentType": "消防产品监督检查记录",
        "documentTypePage": 2,
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
        "endDate": current.date().isoformat(),
        "jurisdiction": "全部管辖单位(含派出所)",
        "documentType": "消防产品监督检查记录",
        "documentTypePage": 2,
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
    if record.get("skippedAsUnchanged"):
        raise SourceIntakeError("已完成且列表特征未变化，无需下载案卷包")
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


def _normalize_record(item: dict[str, Any]) -> dict[str, Any]:
    clean = _clean_evidence(item)
    rwid = _require_rwid(_find_first(clean, _RWID_KEYS))
    project = _find_first(clean, _PROJECT_KEYS)
    normalized = {"rwid": rwid, "fields": clean}
    if project not in (None, ""):
        normalized["projectNo"] = _require_project_no(str(project).strip().upper())
    return normalized


def _record_equivalence(record: dict[str, Any]) -> str:
    fields = dict(record.get("fields") or {})
    for key in list(fields):
        normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if normalized in _RWID_KEYS or "url" in normalized:
            fields.pop(key, None)
    return _fingerprint({"projectNo": record.get("projectNo"), "fields": fields})


def _load_capture(layout: Any, batch_id: str) -> tuple[Path, dict[str, Any]]:
    path = _capture_path(layout, batch_id)
    state = _read_json(path, "浏览器采集状态")
    if state.get("schemaVersion") != BROWSER_CAPTURE_VERSION:
        raise SourceIntakeError("浏览器采集状态版本不受支持")
    if state.get("batchId") != batch_id:
        raise SourceIntakeError("浏览器采集批次不匹配")
    recorded_root = state.get("workspaceRoot")
    if recorded_root and Path(recorded_root) != Path(layout.root):
        raise SourceIntakeError("采集断点属于另一工作根，拒绝跨根续跑")
    return path, state


def _is_acceptance_sample(state: dict[str, Any]) -> bool:
    return state.get("scope") == "acceptance"


def _maybe_waterline(layout: Any, project_no: str, **fields: Any) -> None:
    if upsert_case is None:
        return
    try:
        upsert_case(layout, project_no, **fields)
    except TypeError as error:  # 接口漂移必须显式暴露，不能静默丢水位。
        raise SourceIntakeError(f"案卷水位接口不兼容：{error}") from error


def _reopen_completed_case(
    layout: Any,
    project_no: str,
    existing: dict[str, Any],
    source_change: dict[str, Any],
    seen_at: str,
) -> None:
    if load_waterline is None or save_waterline is None:
        _maybe_waterline(
            layout,
            project_no,
            state="DISCOVERED",
            completedAt=None,
            archive=None,
            local={"status": "NOT_STARTED"},
            upload={"status": "NOT_STARTED"},
            nasVerification={"status": "NOT_STARTED"},
            source=source_change,
        )
        return
    data = load_waterline(layout)
    record = data["cases"].get(project_no)
    if not isinstance(record, dict) or record.get("state") != "COMPLETED":
        raise SourceIntakeError("已完成案卷的水位在重开时发生变化，请重试")
    snapshot = {
        "completedAt": record.get("completedAt"),
        "local": record.get("local"),
        "upload": record.get("upload"),
        "nasVerification": record.get("nasVerification"),
        "archive": record.get("archive"),
        "sourceRecordFingerprint": (record.get("source") or {}).get("sourceRecordFingerprint"),
        "listFingerprint": (record.get("source") or {}).get("listFingerprint"),
        "reopenedAt": seen_at,
    }
    history = record.setdefault("history", {})
    prior = history.get("previousCompletion")
    if prior:
        history.setdefault("olderCompletions", []).append(prior)
    history["previousCompletion"] = snapshot
    record.update(
        {
            "state": "DISCOVERED",
            "completedAt": None,
            "archive": None,
            "local": {"status": "NOT_STARTED"},
            "upload": {"status": "NOT_STARTED"},
            "nasVerification": {"status": "NOT_STARTED"},
            "errorSummary": None,
            "lastSeenAt": seen_at,
        }
    )
    record["source"] = {**dict(record.get("source") or {}), **source_change}
    save_waterline(layout, data)


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
    clean_filters = _default_filters(now, _clean_evidence(filter_value))
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
        "createdAt": created_at,
        "updatedAt": created_at,
    }
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
        if existing is not None and existing != record:
            conflicts.append({"type": "RWID_CONFLICT", "rwid": record["rwid"], "page": page_no})
        else:
            by_rwid[record["rwid"]] = record
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


def _round_records(round_value: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], int, int]:
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
            if existing is not None and existing != record:
                round_value.setdefault("conflicts", []).append(
                    {"type": "RWID_CONFLICT", "rwid": record["rwid"]}
                )
            else:
                records[record["rwid"]] = record
    return records, total, total_pages


def _project_conflicts(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records.values():
        project_no = record.get("projectNo")
        if project_no:
            grouped.setdefault(project_no, []).append(record)
    result = []
    for project_no, items in grouped.items():
        signatures = {_record_equivalence(item) for item in items}
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
    """验收样本只做本批次去重，不读取或更新正式案卷水位。"""

    canonical_by_project: dict[str, str] = {}
    actions: list[str] = []
    for rwid in sorted(records):
        record = records[rwid]
        record["sourceRecordFingerprint"] = _record_equivalence(record)
        project_no = record.get("projectNo")
        if project_no:
            canonical = canonical_by_project.get(project_no)
            if canonical is not None:
                record["aliasOf"] = canonical
                continue
            canonical_by_project[project_no] = rwid
        actions.append(rwid)
    state["actionRwids"] = actions


def _prepare_incremental_queue(
    layout: Any,
    state: dict[str, Any],
    records: dict[str, dict[str, Any]],
    observed_at: datetime | str | None,
) -> None:
    """按项目合并重复来源，并用 CaseWaterlineV1 决定本轮实际处理队列。"""

    waterline_cases: dict[str, Any] = {}
    if load_waterline is not None:
        waterline = load_waterline(layout)
        waterline_cases = dict(waterline.get("cases") or {})
    canonical_by_project: dict[str, str] = {}
    actions: list[str] = []
    seen_at = _timestamp(observed_at)
    for rwid in sorted(records):
        record = records[rwid]
        fingerprint = _record_equivalence(record)
        record["sourceRecordFingerprint"] = fingerprint
        project_no = record.get("projectNo")
        if not project_no:
            actions.append(rwid)
            continue
        canonical = canonical_by_project.get(project_no)
        if canonical is not None:
            record["aliasOf"] = canonical
            continue
        canonical_by_project[project_no] = rwid
        existing = waterline_cases.get(project_no) or {}
        previous_source = existing.get("source") or {}
        previous_fingerprint = previous_source.get("listFingerprint") or previous_source.get(
            "sourceRecordFingerprint"
        )
        unchanged_completed = (
            existing.get("state") == "COMPLETED" and previous_fingerprint == fingerprint
        )
        if unchanged_completed:
            record["skippedAsUnchanged"] = True
            _maybe_waterline(
                layout,
                project_no,
                source={
                    "batchId": state["batchId"],
                    "rwid": rwid,
                    "lastSeenAt": seen_at,
                    "listFingerprint": fingerprint,
                    "sourceRecordFingerprint": fingerprint,
                },
            )
            continue
        actions.append(rwid)
        source_change = {
            "status": "DISCOVERED",
            "batchId": state["batchId"],
            "rwid": rwid,
            "lastSeenAt": seen_at,
            "listFingerprint": fingerprint,
            "sourceRecordFingerprint": fingerprint,
        }
        if existing.get("state") == "COMPLETED":
            source_change["changedSinceCompleted"] = True
            source_change["previousCompletedAt"] = existing.get("completedAt")
            _reopen_completed_case(layout, project_no, existing, source_change, seen_at)
        elif existing:
            _maybe_waterline(layout, project_no, source=source_change)
        else:
            _maybe_waterline(layout, project_no, state="DISCOVERED", source=source_change)
    state["actionRwids"] = actions


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
        records, total, total_pages = _round_records(round_value)
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
    fingerprint = _fingerprint([{"rwid": key, **records[key]} for key in sorted(records)])
    round_value.update(
        {
            "fingerprint": fingerprint,
            "uniqueRecordCount": len(records),
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
        conflicts = list(round_value.get("conflicts") or []) + _project_conflicts(records)
        state["conflicts"] = conflicts
        state["listResult"] = "SAMPLE_STABLE" if _is_acceptance_sample(state) else "STABLE"
        if conflicts:
            state["status"] = "NEEDS_MANUAL_REVIEW"
        else:
            if _is_acceptance_sample(state):
                _prepare_acceptance_queue(state, records)
                state["status"] = "ACCEPTANCE_COLLECTING_DETAILS"
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
) -> dict[str, Any]:
    """接收详情结构和截图，生成/更新 SourceEvidenceV1。"""

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("详情 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    if record.get("skippedAsUnchanged"):
        raise SourceIntakeError("已完成且列表特征未变化，无需重新采集详情")
    clean_detail = _clean_evidence(_json_input(detail, "案卷详情"))
    project_raw = _find_first(clean_detail, _PROJECT_KEYS) or record.get("projectNo")
    project_no = _require_project_no(str(project_raw or "").strip().upper())
    if record.get("projectNo") and record["projectNo"] != project_no:
        conflict = {"type": "DETAIL_PROJECT_CONFLICT", "rwid": record_key, "projectNo": project_no}
        state.setdefault("conflicts", []).append(conflict)
        state["status"] = "NEEDS_MANUAL_REVIEW"
        _write_json(capture_path, state)
        raise SourceIntakeError("详情项目编号与清单不一致，已转人工处理")
    captured = _timestamp(captured_at)
    tags = _derive_tags(clean_detail)
    safe_url = sanitize_source_url(source_url) if source_url else None
    fingerprint = _fingerprint(clean_detail)
    business_fingerprint = _business_detail_fingerprint(clean_detail)
    for other_rwid, other_record in state["records"].items():
        if other_rwid == record_key:
            continue
        other_detail = other_record.get("detail") or {}
        other_project = other_record.get("projectNo") or other_detail.get("projectNo")
        if other_project != project_no or not other_detail:
            continue
        other_business_fingerprint = other_detail.get("businessFingerprint") or (
            _business_detail_fingerprint(other_detail.get("fields") or {})
        )
        if other_business_fingerprint != business_fingerprint:
            state.setdefault("conflicts", []).append(
                {
                    "type": "PROJECT_DETAIL_CONFLICT",
                    "projectNo": project_no,
                    "rwids": sorted([record_key, other_rwid]),
                }
            )
            state["status"] = "NEEDS_MANUAL_REVIEW"
            _write_json(capture_path, state)
            raise SourceIntakeError("多个 RWID 在详情阶段指向同一项目但字段冲突")
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
        alias_detail = {
            "projectNo": project_no,
            "capturedAt": captured,
            "sourceUrl": safe_url,
            "fingerprint": fingerprint,
            "businessFingerprint": business_fingerprint,
            "aliasOf": canonical,
        }
        record.update({"projectNo": project_no, "aliasOf": canonical, "detail": alias_detail})
        state["actionRwids"] = [item for item in state.get("actionRwids", []) if item != record_key]
        evidence_path, evidence = _load_evidence(layout, project_no, batch_id, state=state)
        if batch_id not in evidence["batchIds"]:
            evidence["batchIds"].append(batch_id)
        evidence_alias = evidence["records"].setdefault(record_key, {})
        evidence_alias.update(alias_detail)
        if alias_package and not canonical_package:
            evidence["records"].setdefault(canonical, {"projectNo": project_no})["package"] = (
                alias_package
            )
        evidence["updatedAt"] = captured
        _write_json(evidence_path, evidence)
        _refresh_progress(state)
        state["updatedAt"] = captured
        _write_json(capture_path, state)
        if not _is_acceptance_sample(state):
            _maybe_waterline(
                layout,
                project_no,
                source={
                    "lastSeenAt": captured,
                    "aliasRwid": record_key,
                    "canonicalRwid": canonical,
                },
            )
        return state
    existing = record.get("detail")
    if existing and existing.get("fingerprint") != fingerprint:
        state.setdefault("conflicts", []).append(
            {"type": "DETAIL_CONFLICT", "rwid": record_key, "projectNo": project_no}
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
    if screenshot_source is not None:
        if not screenshot_source.is_file():
            raise SourceIntakeError("详情截图不存在")
        screenshot_sha = _sha256(screenshot_source)
        if existing and existing.get("screenshot"):
            if existing["screenshot"].get("sha256") != screenshot_sha:
                state.setdefault("conflicts", []).append(
                    {
                        "type": "DETAIL_SCREENSHOT_CONFLICT",
                        "rwid": record_key,
                        "projectNo": project_no,
                    }
                )
                state["status"] = "NEEDS_MANUAL_REVIEW"
                _write_json(capture_path, state)
                raise SourceIntakeError("同一 RWID 的详情截图发生变化，已转人工处理")
            stored_screenshot = _workspace_relative_path(
                layout, existing["screenshot"].get("relativePath"), "已登记详情截图"
            )
            if not stored_screenshot.is_file() or _sha256(stored_screenshot) != screenshot_sha:
                raise SourceIntakeError("已登记详情截图与本地证据无法对账")
            screenshot_record = existing["screenshot"]
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
    detail_record: dict[str, Any] = {
        "projectNo": project_no,
        "capturedAt": captured,
        "sourceUrl": safe_url,
        "fields": clean_detail,
        "tags": tags,
        "fingerprint": fingerprint,
        "businessFingerprint": business_fingerprint,
    }
    if screenshot_record:
        detail_record["screenshot"] = screenshot_record
    record["projectNo"] = project_no
    record["detail"] = detail_record
    evidence_path, evidence = _load_evidence(layout, project_no, batch_id, state=state)
    if batch_id not in evidence["batchIds"]:
        evidence["batchIds"].append(batch_id)
    evidence_record = evidence["records"].setdefault(record_key, {})
    prior_fingerprint = evidence_record.get("fingerprint")
    if prior_fingerprint and prior_fingerprint != detail_record["fingerprint"]:
        raise SourceIntakeError("本地来源证据已有不同详情，拒绝覆盖")
    evidence_record.update(detail_record)
    evidence["tags"] = sorted(set(evidence.get("tags") or []) | set(tags))
    evidence["updatedAt"] = captured
    _write_json(evidence_path, evidence)
    _refresh_progress(state)
    state["updatedAt"] = captured
    _write_json(capture_path, state)
    source_fields: dict[str, Any] = {
        "batchId": batch_id,
        "rwid": record_key,
        "tags": tags,
        "capturedAt": captured,
    }
    address = _find_first(clean_detail, {"address", "unitaddress", "单位地址", "地址"})
    if address not in (None, ""):
        source_fields["address"] = str(address).strip()
    waterline_fields: dict[str, Any] = {
        "state": "DETAIL_CAPTURED",
        "tags": tags,
        "source": source_fields,
    }
    unit_name = _find_first(
        clean_detail,
        {"unitname", "checkedunit", "companyname", "单位名称", "被检查单位"},
    )
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
        _maybe_waterline(layout, project_no, **waterline_fields)
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
                        if zipfile.is_zipfile(member):
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
            if extracted_file.is_file() and zipfile.is_zipfile(extracted_file):
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
    if batch_id not in evidence["batchIds"]:
        evidence["batchIds"].append(batch_id)
    evidence_record = evidence["records"].setdefault(record_key, {"projectNo": project_no})
    prior_package = evidence_record.get("package")
    if prior_package and prior_package.get("sha256") != package["sha256"]:
        raise SourceIntakeError("本地来源证据已绑定不同案卷包，拒绝覆盖")
    evidence_record["package"] = package
    evidence["updatedAt"] = package["attachedAt"]
    _write_json(evidence_path, evidence)

    record = state["records"][record_key]
    record["projectNo"] = project_no
    record["package"] = package
    remaining_conflicts = []
    resolved_conflicts = state.setdefault("resolvedConflicts", [])
    for conflict in state.get("conflicts") or []:
        if conflict.get("type") == "PACKAGE_REJECTED" and conflict.get("rwid") == record_key:
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
) -> dict[str, Any]:
    """接收唯一完整 ZIP，安全检查后按项目号+哈希绑定原始包。"""

    layout = _layout(workspace)
    capture_path, state = _load_capture(layout, batch_id)
    record_key = _require_rwid(rwid)
    record = (state.get("records") or {}).get(record_key)
    if record is None:
        raise SourceIntakeError("案卷包 RWID 不在已稳定清单中")
    if record.get("aliasOf"):
        raise SourceIntakeError(f"该 RWID 已合并，请使用主记录 {record['aliasOf']}")
    if record.get("skippedAsUnchanged"):
        raise SourceIntakeError("已完成且列表特征未变化，无需重新下载案卷包")
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
    if prior_package and prior_package.get("sha256") != sha:
        raise SourceIntakeError("来源证据已绑定不同案卷包，拒绝自动恢复")
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
    "safe_extract_package",
    "sanitize_source_url",
    "wait_for_download_candidate",
]
