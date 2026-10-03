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


def verified_package(layout, project: str) -> dict | None:
    """Existing packages lead back to their original capture, never to another click."""
    found = []
    for state in coverage.formal_captures(layout):
        for key, record in state.get("records", {}).items():
            if record.get("projectNo") != project or not record.get("package"):
                continue
            package = record["package"]
            path = source._workspace_relative_path(layout, package.get("relativePath"), "原案卷包")
            if not path.is_file() or source._sha256(path) != package.get("sha256"):
                raise source.SourceIntakeError("原案卷包与哈希无法回读；保留失败断点，不重新打包")
            found.append({"status": "RESUME_ORIGINAL_PACKAGE", "projectNo": project,
                          "batchId": state["batchId"], "rwid": key, "package": package})
    if len({item["package"]["sha256"] for item in found}) > 1:
        raise source.SourceIntakeError("同一项目已有不同原包，保留冲突，不重新采集")
    return found[0] if found else None


def pending_download_request(layout, project: str) -> dict | None:
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
