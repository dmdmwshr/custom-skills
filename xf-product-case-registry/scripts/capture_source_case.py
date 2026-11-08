"""Explicit, resumable material capture for one already indexed annual case.

No login, package click, OCR or upload is performed here. The annual census,
its original identity evidence and all existing download requests are retained.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from uuid import uuid4

if __package__:
    from . import annual_browser as browser_api
    from . import source_coverage as coverage
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import annual_browser as browser_api
    import source_coverage as coverage
    import source_intake as source
    import workspace_state as ws


def case_context(layout, batch_id: str, rwid: str, project: str) -> dict:
    _, state = source._load_capture(layout, batch_id)
    key, project = source._require_rwid(rwid), source._require_project_no(project)
    record = state.get("records", {}).get(key)
    if not record or record.get("projectNo") != project or record.get("aliasOf"):
        raise source.SourceIntakeError("请求的 RWID 与该批次项目编号没有唯一绑定")
    unit = source._find_first(record.get("fields", {}), source._UNIT_NAME_KEYS)
    if not unit:
        unit = source._find_first(
            record.get("detail", {}).get("fields", {}), source._UNIT_NAME_KEYS
        )
    if not unit:
        raise source.SourceIntakeError("已知项目缺少可信单位名称，不能定点采集")
    return {"state": state, "record": record, "rwid": key, "projectNo": project,
            "unitName": str(unit), "batchId": batch_id}


def recorded_package_shas(layout, project: str) -> list[str]:
    """只读已登记的包摘要（不回读字节），供坏包诊断判断唯一登记。"""
    shas = []
    for state in coverage.formal_captures(layout):
        for record in state.get("records", {}).values():
            package = record.get("package")
            if record.get("projectNo") == project and package and package["sha256"] not in shas:
                shas.append(package["sha256"])
    return shas


class PackageUnreadable(source.SourceIntakeError):
    """原包无法回读。state: FILE_MISSING/SHA_MISMATCH 为可靠结论；UNKNOWN 为路径安全/身份/读取失败，不得当作坏包。"""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


_WATERLINE_CACHE: dict = {}


def _waterline_cases(layout) -> dict:
    """同一进程内按文件 mtime/大小复用只读水位解析，避免逐案重复读取大 JSON。"""
    stat = layout.waterline_json.stat()
    key = (str(layout.waterline_json), stat.st_mtime_ns, stat.st_size)
    if _WATERLINE_CACHE.get("key") != key:
        _WATERLINE_CACHE["key"] = key
        _WATERLINE_CACHE["cases"] = ws.load_waterline(layout).get("cases") or {}
    return _WATERLINE_CACHE["cases"]


def _safe_registered(layout, path: Path, label: str) -> Path:
    """读取/枚举前验证完整祖先链（含 Windows 目录联接/重解析点）和真实业务根，不只看最终文件。"""
    return ws._validated_workspace_path(path, layout.root, label, must_exist=False)


def locate_package(layout, project: str, package: dict, rwid: str | None = None) -> dict:
    """按登记位置回读原包：先看捕获登记的待处理路径，再看水位里本案已登记的归档原件位置（历史逻辑根映射）。

    只在本案登记的路径内按摘要核对，不全盘找同名文件；归档代别只认同一摘要。
    每个登记目录/候选文件在读取或枚举前都核查完整祖先链与业务根；不安全/越界/身份冲突为 UNKNOWN。"""
    sha = package.get("sha256")
    if not sha:
        return {"state": "UNKNOWN", "path": None, "reason": "包登记缺少摘要"}
    bad = None
    try:
        pending = source._workspace_relative_path(layout, package.get("relativePath"), "原案卷包")
        _safe_registered(layout, pending, "原案卷包")
        if pending.is_file():
            if source._sha256(pending) == sha:
                return {"state": "OK", "path": pending, "location": "PENDING"}
            bad = pending
    except Exception as error:  # noqa: BLE001 - 路径安全失败不能当作缺失
        return {"state": "UNKNOWN", "path": None, "reason": f"登记路径不可安全回读：{type(error).__name__}"}
    try:
        record = _waterline_cases(layout).get(project)
        folders, files = [], []
        if record:
            registered = (record.get("source") or {}).get("rwid")
            if rwid and registered and str(registered) != str(rwid):
                return {"state": "UNKNOWN", "path": None, "reason": "水位 RWID 与捕获登记不一致"}
            for entry in (record.get("history") or {}).get("completions") or []:
                if entry.get("packageSha256") == sha and entry.get("originalPath"):
                    folders.append(layout.resolve_recorded_path(entry["originalPath"]))
            for previous in (record.get("source") or {}).get("previousGenerations") or []:
                if previous.get("packageSha256") == sha and previous.get("packageRelativePath"):
                    files.append(source._workspace_relative_path(layout, previous["packageRelativePath"], "旧代原包"))
        for folder in dict.fromkeys(folders):
            _safe_registered(layout, folder, "归档原件目录")      # 枚举前先验祖先链
            if folder.is_dir():
                files.extend(sorted(folder.rglob("*.zip")))
        for path in files:
            _safe_registered(layout, path, "归档原包")
            if path.is_file() and not ws._is_reparse(path) and source._sha256(path) == sha:
                return {"state": "OK", "path": path, "location": "ARCHIVED"}
    except Exception as error:  # noqa: BLE001
        return {"state": "UNKNOWN", "path": None, "reason": f"归档位置不可安全回读：{type(error).__name__}"}
    if bad is not None:
        return {"state": "SHA_MISMATCH", "path": bad, "reason": "登记路径文件摘要与登记不符，且归档位置无同摘要原包"}
    return {"state": "FILE_MISSING", "path": None, "reason": "登记路径与本案归档登记位置均无此摘要的原包"}


def verified_package(layout, project: str) -> dict | None:
    """Existing packages lead back to their original capture, never to another click."""
    found = []
    for state in coverage.formal_captures(layout):
        for key, record in state.get("records", {}).items():
            if record.get("projectNo") != project or not record.get("package"):
                continue
            package = record["package"]
            located = locate_package(layout, project, package, key)
            if located["state"] != "OK":
                raise PackageUnreadable(
                    located["state"], "原案卷包与哈希无法回读；保留失败断点，不重新打包（" + located["reason"] + "）")
            found.append({"status": "RESUME_ORIGINAL_PACKAGE", "projectNo": project,
                          "batchId": state["batchId"], "rwid": key, "package": package,
                          "location": located["location"]})
    by_sha = {}
    for item in found:
        by_sha.setdefault(item["package"]["sha256"], item)
    if len(by_sha) > 1:
        # 仅明确合法的新代链才可选当前代：每个更新代的 supersedes 必须指向另一已存在的包，
        # 且链线性（唯一链头、所有旧代都在链上）。attachedAt 最新不是依据；无法识别的多包仍是冲突。
        successors = {}
        for sha, item in by_sha.items():
            prior = item["package"].get("supersedes")
            if prior is None:
                continue
            if prior not in by_sha or prior in successors or prior == sha:
                raise source.SourceIntakeError("同一项目已有不同原包且代链不合法，保留冲突，不重新采集")
            successors[prior] = sha
        heads = [sha for sha in by_sha if sha not in successors]
        if len(heads) != 1:
            raise source.SourceIntakeError("同一项目已有不同原包，保留冲突，不重新采集")
        chain, cursor = [], heads[0]
        while cursor is not None:
            chain.append(cursor)
            cursor = by_sha[cursor]["package"].get("supersedes")
        if set(chain) != set(by_sha):
            raise source.SourceIntakeError("同一项目已有不同原包且不在同一代链上，保留冲突，不重新采集")
        head = dict(by_sha[heads[0]])
        head["generations"] = list(reversed(chain))
        return head
    return found[0] if found else None


def _request_package_attached(directory: Path, request: dict) -> bool:
    capture_path = directory / "browser-capture.json"
    if not capture_path.is_file():
        return False
    state = json.loads(capture_path.read_text(encoding="utf-8"))
    record = (state.get("records") or {}).get(str(request.get("rwid")))
    return bool(record and record.get("package"))


def pending_download_request(layout, project: str, *, ignore_attached: bool = False) -> dict | None:
    """Read only small request metadata; retain unknown action outcomes across batches."""
    for directory in sorted(layout.capture_batches.iterdir()):
        if not directory.is_dir():
            continue
        candidates = [*directory.glob("staging-*/download-request*.json"),
                      *directory.glob("material-capture/*/download-request*.json")]
        for path in candidates:
            request = ws._read_progress_json(path, layout.root, "原下载请求")
            if request.get("projectNo") != project:
                continue
            if ignore_attached and _request_package_attached(directory, request):
                continue  # 显式刷新只忽略已接件完成的旧请求；未接件的在途/未知请求仍须先对账
            checkpoint = Path(str(request.get("checkpointPath") or ""))
            ws._validated_workspace_path(checkpoint, layout.root, "原下载动作", must_exist=False)
            action = {}
            if checkpoint.exists():
                action = ws._read_progress_json(checkpoint, layout.root, "原下载动作")
            return {"status": "RESUME_ORIGINAL_DOWNLOAD", "projectNo": project,
                    "batchId": request.get("batchId"), "rwid": request.get("rwid"),
                    "requestPath": str(path), "actionState": action.get("state"),
                    "submitted": action.get("submitted")}
    return None


def material_directory(layout, batch_id: str, project: str) -> Path:
    parent = layout.capture_batches / batch_id / "material-capture"
    ws._validated_workspace_path(parent, layout.root, "材料采集断点父目录", must_exist=False)
    parent.mkdir(exist_ok=True)
    target = parent / project
    ws._validated_workspace_path(target, layout.root, "单案材料采集断点", must_exist=False)
    target.mkdir(exist_ok=True)
    return target


def verified_detail(layout, record: dict) -> dict | None:
    detail = record.get("collectionDetail")
    if not detail:
        return None
    screenshot = detail.get("screenshot") or {}
    path = source._workspace_relative_path(layout, screenshot.get("relativePath"), "完整详情截图")
    if not path.is_file() or source._sha256(path) != screenshot.get("sha256"):
        raise source.SourceIntakeError("已采集完整详情截图无法回读；保留原证据")
    return detail


def _summary(context: dict, status: str) -> dict:
    return {"status": status, "batchId": context["batchId"],
            "rwid": context["rwid"], "projectNo": context["projectNo"],
            "packageDownloads": 0, "ocr": 0, "uploads": 0}


def save_visibility_wait(layout, directory: Path, context: dict, stage: str) -> dict:
    receipt = {**_summary(context, "WAITING"), "reason": "SOURCE_DOCUMENT_HIDDEN",
               "stage": stage, "at": ws._utc_now()}
    source._write_json(directory / f"{stage.lower()}-wait.json", receipt)
    return receipt


def save_observation_wait(layout, directory: Path, context: dict, stage: str,
                          observation: dict) -> dict:
    keys = {"reason", "pageNumber", "pageSize", "totalCount", "observedFirstRowId",
            "observedLastRowId", "visibilityState", "expectedPage", "ready",
            "documentCount", "missingCheckboxCount", "foreignSelectedCount"}
    receipt = {**_summary(context, "WAITING"), "reason": observation["reason"],
               "stage": stage, "observation": {k: v for k, v in observation.items() if k in keys},
               "at": ws._utc_now()}
    source._write_json(directory / f"{stage.lower()}-wait.json", receipt)
    return receipt


def capture_known_case(
    layout, browser, batch_id: str, rwid: str, project: str, *,
    category: str | None = None, year: int | None = None, max_pages: int = 5,
    configure: bool = False,
) -> dict:
    context = case_context(layout, batch_id, rwid, project)
    filters = context["state"].get("filters", {})
    if filters.get("selectionMode") != coverage.ANNUAL_MODE:
        raise source.SourceIntakeError("当前定点入口要求年度任务批次；旧批次继续其原断点")
    if (category is not None and category != filters.get("taskCategory")) or (
        year is not None and year != filters.get("year")
    ):
        raise source.SourceIntakeError("指定类别或年份与原年度批次不一致")
    if source._verified_completed_waterline(layout, project):
        return _summary(context, "ALREADY_COMPLETED")
    existing = verified_package(layout, project) or pending_download_request(layout, project)
    if existing:
        return {**existing, "packageDownloads": 0, "ocr": 0, "uploads": 0}
    state = source.request_case_material_collection(layout, batch_id, rwid, project)
    record = state["records"][rwid]
    if verified_detail(layout, record):
        return _summary(context, "DETAIL_ALREADY_CAPTURED")
    directory = material_directory(layout, batch_id, project)
    receipt_path = directory / "capture-receipt.json"
    binding = {"batchId": batch_id, "rwid": rwid, "projectNo": project}
    if receipt_path.exists():
        receipt = ws._read_progress_json(receipt_path, layout.root, "单案详情接收断点")
        if any(receipt.get(key) != value for key, value in binding.items()):
            raise source.SourceIntakeError("详情接收断点属于其他案卷")
        screenshot = Path(receipt["screenshotPath"])
        ws._validated_workspace_path(screenshot, layout.root, "详情截图", must_exist=True)
        if source._sha256(screenshot) != receipt["screenshotSha256"]:
            raise source.SourceIntakeError("详情接收断点截图哈希不一致")
    else:
        action_path = directory / "capture-action.json"
        action = None
        if action_path.exists():
            action = ws._read_progress_json(action_path, layout.root, "单案详情动作")
            if any(action.get(key) != value for key, value in binding.items()):
                raise source.SourceIntakeError("详情动作断点属于其他案卷")
        if action is None or (
            action.get("submitted") is False and action.get("state") == "SOURCE_DOCUMENT_HIDDEN"
        ):
            order = record.get("sourceOrder") or record.get("sourceAppearances", [{}])[0].get(
                "sourceOrder"
            )
            try:
                if configure and action is None:
                    browser.configure(filters["taskCategory"], year=filters["year"])
                located = browser.locate_known_case(
                    rwid, context["unitName"], order, year=filters["year"],
                    category=filters["taskCategory"], baseline_count=state["sourceListCount"],
                    max_pages=max_pages,
                )
            except source.SourceIntakeError as error:
                if "SOURCE_DOCUMENT_HIDDEN" in str(error):
                    return save_visibility_wait(layout, directory, context, "DETAIL")
                if getattr(error, "observation", None):
                    return save_observation_wait(layout, directory, context, "LIST",
                                                 error.observation)
                raise
            source._write_json_exclusive(directory / f"location-{uuid4().hex[:16]}.json", {
                **binding, "observedAt": ws._utc_now(),
                "baselineCount": located["baselineCount"],
                "observedCount": located["observedCount"],
                "visitedPages": located["visitedPages"], "row": located["row"],
            })
            intent = {**binding, "state": "ACTION_INTENT_RECORDED", "submitted": None,
                      "at": ws._utc_now(), "resumeIndex": 0}
            resume_path = None
            if action is None:
                source._write_json_exclusive(action_path, intent)
            else:
                intent["resumeIndex"] = action.get("resumeIndex", 0) + 1
                resume_path = directory / f"capture-action-resume-{intent['resumeIndex']}.json"
                source._write_json_exclusive(resume_path, {**intent, "priorRefusal": action})
                source._write_json(action_path, intent)
            try:
                browser.open_case(rwid)
            except (source.SourceIntakeError, subprocess.TimeoutExpired) as error:
                hidden = "SOURCE_DOCUMENT_HIDDEN" in str(error)
                outcome = {**intent, "state": "SOURCE_DOCUMENT_HIDDEN" if hidden else
                           "RESULT_UNKNOWN", "submitted": False if hidden else None}
            else:
                outcome = {**intent, "state": "DETAIL_OPEN_SUBMITTED", "submitted": True}
            source._write_json(action_path, outcome)
            if resume_path is not None:
                resumed = ws._read_progress_json(resume_path, layout.root, "详情恢复意图")
                source._write_json(resume_path, {**resumed, "result": outcome})
            if outcome.get("submitted") is False:
                return save_visibility_wait(layout, directory, context, "DETAIL")
        try:
            observation = browser.read_full_detail(rwid, project, context["unitName"])
        except source.SourceIntakeError as error:
            if "SOURCE_DOCUMENT_HIDDEN" in str(error):
                return save_visibility_wait(layout, directory, context, "DETAIL")
            if getattr(error, "observation", None):
                return save_observation_wait(layout, directory, context, "DETAIL",
                                             error.observation)
            raise
        screenshot = directory / f"detail-{uuid4().hex[:16]}.png"
        try:
            browser.capture_detail_screenshot(screenshot, observation)
        except source.SourceIntakeError as error:
            if "SOURCE_DOCUMENT_HIDDEN" in str(error):
                return save_visibility_wait(layout, directory, context, "DETAIL")
            raise
        receipt = {**binding, "fields": observation["fields"],
                   "sourceUrl": observation["sourceUrl"], "capturedAt": ws._utc_now(),
                   "screenshotPath": str(screenshot),
                   "screenshotSha256": source._sha256(screenshot)}
        source._write_json_exclusive(receipt_path, receipt)
    source.add_detail(
        layout, batch_id, rwid, receipt["fields"], source_url=receipt["sourceUrl"],
        screenshot=screenshot, captured_at=receipt["capturedAt"],
    )
    return _summary(context, "DETAIL_CAPTURED")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="从原年度身份定点采集一个已确认不合格案的完整详情")
    result.add_argument("--batch-id", required=True)
    result.add_argument("--rwid", required=True)
    result.add_argument("--project", required=True)
    result.add_argument("--session", required=True)
    result.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    result.add_argument("--browser-cwd", type=Path, default=Path.cwd())
    result.add_argument("--workspace-root", type=Path)
    result.add_argument("--category", choices=coverage.TASK_CATEGORIES)
    result.add_argument("--year", type=int)
    result.add_argument("--max-pages", type=int, default=5)
    result.add_argument("--configure", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    _, layout = ws.resolve_workspace(work_root=args.workspace_root, create_layout=False)
    browser = browser_api.Session(args.browser_cwd, args.session, args.browser)
    result = capture_known_case(
        layout, browser, args.batch_id, args.rwid, args.project,
        category=args.category, year=args.year, max_pages=args.max_pages, configure=args.configure,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
