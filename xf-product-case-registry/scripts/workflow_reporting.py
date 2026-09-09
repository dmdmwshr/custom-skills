"""Read-only workflow projections and explicit, local diagnostic timing receipts."""

from __future__ import annotations

import html
import json
import math
import os
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STAGE_LABELS = {
    "DETAIL": "详情证据",
    "PACKAGE": "案卷包",
    "INVENTORY": "清点",
    "OCR": "已记录的识别校验",
    "MANIFEST": "上传清单",
    "FINALIZED": "正式建档",
    "NAS_VERIFIED": "飞牛核验",
    "ARCHIVED": "本地归档",
}
ACTIONS = {
    "NONE": ("已完成，无需重做", None, False),
    "MANUAL_REVIEW": ("核查本案异常，不自动重试", "存在未解决异常或身份冲突", False),
    "VERIFY": ("继续核验同一案卷", "已建档，核验尚未完成", True),
    "ARCHIVE": ("继续本地归档", "核验已通过，归档尚未完成", False),
    "RESUME_UPLOAD": ("回读同一任务后续传缺失文件", "已有上传断点，不新建替代任务", True),
    "COLLECT_DETAIL": ("采集并核对来源详情", "尚无正式详情证据", False),
    "AWAIT_PACKAGE": ("按本案下载基线核对案卷包", "尚无唯一正式案卷包", False),
    "INVENTORY": ("清点本案原始材料", "本地清点尚未完成", False),
    "OCR_RESUME": ("仅补做未确认的识别文件", "识别仍有未完成项", False),
    "ORGANIZE": ("分类文书并整理案卷字段", "尚未形成完整案卷数据", False),
    "COMPOSE": ("生成并校验上传清单", "已有案卷数据，清单尚未生成", False),
    "VALIDATE": ("校验清单与文件映射", "尚未确认具备上传条件", False),
    "UPLOAD": ("按本次授权执行正式导入", "已具备本地上传条件", True),
    "INSPECT_STATE": ("核对本地状态证据", "状态文件缺失、损坏或归属不一致", False),
}


class ReportingError(RuntimeError):
    pass


def _safe(path: Path, root: Path) -> Path:
    candidate = Path(os.path.abspath(path))
    if not candidate.is_relative_to(Path(os.path.abspath(root))):
        raise ReportingError("报告证据路径超出当前工作根")
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ReportingError("报告证据路径不允许重解析点")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ReportingError("报告证据路径解析后越界")
    return candidate


def _read(path: Path, root: Path) -> dict[str, Any] | None:
    path = _safe(path, root)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ReportingError("报告状态文件大小或类型无效")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ReportingError("报告状态文件无法读取") from error
    if not isinstance(value, dict):
        raise ReportingError("报告状态文件必须是对象")
    return value


def _status(value: Mapping[str, Any], part: str) -> str:
    child = value.get(part)
    return str(child.get("status", "UNKNOWN")) if isinstance(child, Mapping) else "UNKNOWN"


def _case_workspace(layout: Any, project: str, record: Mapping[str, Any]) -> Path:
    active = layout.work_case_dir(project)
    if active.is_dir():
        return _safe(active, layout.root)
    archive = record.get("archive")
    if isinstance(archive, Mapping) and archive.get("workspacePath"):
        archived = _safe(Path(archive["workspacePath"]), layout.history_workspaces)
        generation = archive.get("generation")
        if not isinstance(generation, str) or not re.fullmatch(
            r"\d{8}T\d{6}Z-[a-f0-9]{12}", generation
        ):
            raise ReportingError("报告归档代际缺少明确身份")
        if (
            archived.parent != layout.history_workspaces
            or archived.name != f"{project}-{generation}"
        ):
            raise ReportingError("报告归档工作区不属于本案")
        return archived
    return active


def record_timing(
    directory: Path,
    root: Path,
    *,
    scope_key: str,
    operation: str,
    category: str,
    started_at: str,
    duration: float,
    outcome: str,
) -> None:
    if category not in {"processing", "networkWait", "manualPause", "unclassified"}:
        raise ReportingError("计时类别无效")
    if not math.isfinite(duration) or duration < 0:
        raise ReportingError("计时数值无效")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}(?:/[a-z][a-z0-9-]{0,40})?", operation):
        raise ReportingError("计时操作名称无效")
    target_dir = _safe(directory / "diagnostics" / "timings", root)
    target_dir.mkdir(parents=True, exist_ok=True)
    record_id = uuid.uuid4().hex
    value = {
        "schemaVersion": "WorkflowTimingV1",
        "recordId": record_id,
        "scopeKey": scope_key,
        "operation": operation,
        "category": category,
        "startedAt": started_at,
        "finishedAt": datetime.now(UTC).isoformat(),
        "durationSeconds": round(duration, 3),
        "outcome": outcome,
    }
    destination = target_dir / f"timing-{record_id}.json"
    temporary = destination.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(destination)


def timing_projection(
    directory: Path, root: Path, scope_key: str, *, browser_stages: bool = False
) -> dict[str, Any]:
    totals: dict[str, float | None] = {
        key: None for key in ("processing", "networkWait", "manualPause", "unclassified")
    }
    records = 0
    issues = []
    seen = set()
    timing_dir = _safe(directory / "diagnostics" / "timings", root)
    candidates = list(timing_dir.glob("timing-*.json")) if timing_dir.is_dir() else []
    if browser_stages and directory.is_dir():
        candidates.extend(directory.glob("阶段_*.json"))
    for path in candidates:
        try:
            value = _read(path, root)
            if not value:
                continue
            record_id = value.get("recordId")
            if not isinstance(record_id, str) or not re.fullmatch(r"[a-f0-9-]{32,36}", record_id):
                raise ReportingError("计时记录标识无效")
            if record_id in seen:
                continue
            entries: dict[str, Any]
            if (
                value.get("schemaVersion") == "WorkflowTimingV1"
                and value.get("scopeKey") == scope_key
            ):
                entries = {value.get("category"): value.get("durationSeconds")}
            elif browser_stages and value.get("schemaVersion") == "SourceStageV1":
                entries = {
                    key: value.get(field) / 1000
                    if isinstance(value.get(field), (int, float))
                    and not isinstance(value.get(field), bool)
                    else None
                    for key, field in (
                        ("processing", "processingMs"),
                        ("networkWait", "networkWaitMs"),
                        ("manualPause", "manualPauseMs"),
                    )
                }
            else:
                continue
            if not value.get("recordId") or any(
                key not in totals
                or isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or number < 0
                for key, number in entries.items()
            ):
                raise ReportingError("计时记录字段无效")
            for key, number in entries.items():
                totals[key] = (totals[key] or 0) + number
            seen.add(value["recordId"])
            records += 1
        except ReportingError:
            issues.append("TIMING_RECORD_INVALID")
    return {
        "coverage": "RECORDED_STAGES_ONLY" if records else "UNKNOWN",
        "recordCount": records,
        **{
            key + "Seconds": round(number, 3) if number is not None else None
            for key, number in totals.items()
        },
        "issues": sorted(set(issues)),
    }


def describe_case(
    layout: Any, project: str, record: Mapping[str, Any], *, has_detail: bool, has_package: bool
) -> dict[str, Any]:
    issues = []
    stage_list = []
    local = _status(record, "local")
    uploaded, nas = _status(record, "upload"), _status(record, "nasVerification")
    state = str(record.get("state", "UNKNOWN"))
    work = layout.work_case_dir(project)
    inputs: dict[str, Any] = {}
    try:
        work = _case_workspace(layout, project, record)
        for name in (
            "inventory",
            "ocr-result",
            "case-data",
            "manifest",
            "upload-map",
            "upload-state",
        ):
            inputs[name] = _read(work / f"{name}.json", layout.root)
        upload_state = inputs.get("upload-state")
        if upload_state and (
            upload_state.get("stateVersion") != 6
            or upload_state.get("projectNo") != project
            or not isinstance(upload_state.get("status"), str)
        ):
            raise ReportingError("上传状态格式或项目不一致")
        for name in ("case-data", "manifest"):
            value = inputs.get(name)
            if value and (
                not isinstance(value.get("case"), Mapping)
                or value["case"].get("projectNo") != project
            ):
                raise ReportingError("本地案卷字段格式或项目不一致")
        ocr_value = inputs.get("ocr-result")
        if (
            ocr_value
            and ocr_value.get("schemaVersion") == "OcrResultV1"
            and (
                not isinstance(ocr_value.get("mappings"), list)
                or not isinstance(ocr_value.get("pending"), list)
                or not isinstance(ocr_value.get("status"), str)
                or ocr_value.get("workDir") not in {str(work), str(layout.work_case_dir(project))}
            )
        ):
            raise ReportingError("识别记录格式或项目不一致")
    except ReportingError:
        issues.append("LOCAL_STATE_UNTRUSTED")
        inputs = {}
    ocr = inputs.get("ocr-result") or {}
    mappings = ocr.get("mappings") if isinstance(ocr.get("mappings"), list) else []
    pending = ocr.get("pending") if isinstance(ocr.get("pending"), list) else []
    ocr_current = ocr.get("schemaVersion") == "OcrResultV1"
    ocr_status = (
        ocr.get("status", "UNKNOWN")
        if ocr_current
        else "LEGACY_UNVERIFIED"
        if ocr
        else "NOT_STARTED"
    )
    upload_state = inputs.get("upload-state") or {}
    job_status = upload_state.get("status", uploaded)
    if has_detail:
        stage_list.append("DETAIL")
    if has_package:
        stage_list.append("PACKAGE")
    if (inputs.get("inventory") or {}).get("inventoryVersion") == 3:
        stage_list.append("INVENTORY")
    if ocr_current and ocr_status == "COMPLETED" and not pending:
        stage_list.append("OCR")
    manifest = inputs.get("manifest")
    if manifest:
        if inputs.get("upload-map"):
            stage_list.append("MANIFEST")
        else:
            issues.append("MANIFEST_IDENTITY_MISMATCH")
    finalize_summary = upload_state.get("finalizeSummary") or {}
    if (
        job_status in {"FINALIZED_UNVERIFIED", "FINALIZED_WITH_CONFLICTS", "VERIFIED"}
        and isinstance(finalize_summary, Mapping)
        and finalize_summary.get("created") is True
        or uploaded == "VERIFIED"
        and nas == "VERIFIED"
    ):
        stage_list.append("FINALIZED")
    if uploaded == "VERIFIED" and nas == "VERIFIED":
        stage_list.append("NAS_VERIFIED")
    if state == "COMPLETED" and "NAS_VERIFIED" in stage_list:
        stage_list.append("ARCHIVED")

    if issues:
        action = "INSPECT_STATE"
    elif "ARCHIVED" in stage_list:
        action = "NONE"
    elif state == "NEEDS_MANUAL_REVIEW" or job_status in {"FAILED", "FINALIZED_WITH_CONFLICTS"}:
        action = "MANUAL_REVIEW"
    elif state == "VERIFIED_PENDING_ARCHIVE" or (uploaded == "VERIFIED" and nas == "VERIFIED"):
        action = "ARCHIVE"
    elif state == "UPLOADED_AWAITING_NAS" or job_status == "FINALIZED_UNVERIFIED":
        action = "VERIFY"
    elif job_status in {"CREATED", "UPLOADING", "MANIFEST_RECEIVED"}:
        action = "RESUME_UPLOAD"
    elif not has_detail:
        action = "COLLECT_DETAIL"
    elif not has_package:
        action = "AWAIT_PACKAGE"
    elif "INVENTORY" not in stage_list:
        action = "INVENTORY"
    elif ocr_current and (pending or ocr_status != "COMPLETED"):
        action = "OCR_RESUME"
    elif not inputs.get("case-data"):
        action = "ORGANIZE"
    elif "MANIFEST" not in stage_list:
        action = "COMPOSE"
    elif local == "READY_FOR_UPLOAD":
        action = "UPLOAD"
    else:
        action = "VALIDATE"
    label, reason, authorization = ACTIONS[action]
    try:
        timings = timing_projection(work, layout.root, f"project:{project}")
    except ReportingError:
        timings = {"coverage": "UNKNOWN", "recordCount": 0, "issues": ["TIMING_PATH_INVALID"]}
    archive = record.get("archive") or {}
    receipt_available = False
    if isinstance(archive, Mapping) and archive.get("verificationRecord"):
        try:
            receipt_path = _safe(Path(archive["verificationRecord"]), layout.verification_records)
            if receipt_path.parent == layout.verification_records and receipt_path.name.startswith(
                project + "-"
            ):
                evidence = _read(receipt_path, layout.root)
                receipt_available = bool(evidence and evidence.get("projectNo") == project)
        except ReportingError:
            issues.append("VERIFICATION_RECEIPT_UNAVAILABLE")
    return {
        "projectNo": project,
        "unitName": record.get("unitName")
        if isinstance(record.get("unitName"), str)
        else "未知单位",
        "state": state,
        "completedStages": stage_list,
        "nextAction": {
            "code": action,
            "label": label,
            "reason": reason,
            "requiresCurrentAuthorization": authorization,
        },
        "ocr": {
            "status": ocr_status,
            "recordedMappings": len(mappings),
            "pendingFiles": len(pending),
        },
        "timings": timings,
        "verificationReceiptAvailable": receipt_available,
        "issues": issues,
    }


def _cell(value: Any) -> str:
    text = str(value) if value is not None else "未知"
    return html.escape(text.replace("\r", " ").replace("\n", " "), quote=False).replace("|", "\\|")


def render_report(progress: dict[str, Any]) -> str:
    batch, uploaded = progress["batch"], progress["upload"]
    lines = [
        "# 案卷流程进度与核验摘要",
        "",
        "本报告是本地事实源的只读汇总，不等同于本次重新访问服务器核验。",
        "",
        f"生成时间：{_cell(progress['generatedAt'])}",
        f"来源文书记录：{_cell(batch.get('sourceDocumentRecords'))}；唯一来源导航记录：{_cell(batch.get('uniqueRwids'))}；"
        f"当前批次案卷：{progress['waterline']['caseCount']}。",
        f"已接收案卷包：{progress['storage']['packageProjects']}；系统成功：{uploaded['successfulSystemCases']}；"
        f"飞牛已核验：{uploaded['nasVerifiedCases']}。",
        "",
        "## 逐案下一步",
        "",
        "| 项目编号 | 单位 | 已确认阶段 | 下一步／等待原因 |",
        "| --- | --- | --- | --- |",
    ]
    for case in progress.get("cases", []):
        stages_text = (
            "、".join(STAGE_LABELS[key] for key in case["completedStages"]) or "尚无完整阶段证据"
        )
        next_action = case["nextAction"]
        action = next_action["label"] + (
            f"；{next_action['reason']}" if next_action["reason"] else ""
        )
        lines.append(
            "| "
            + " | ".join(
                _cell(value) for value in (case["projectNo"], case["unitName"], stages_text, action)
            )
            + " |"
        )
    lines += [
        "",
        "## 已记录耗时",
        "",
        "只累计实际保存的计时记录；未记录项显示未知。并行阶段可能重叠，不能相加当作端到端历时。",
        "",
        "| 范围 | 操作处理（秒） | 网络等待（秒） | 人工暂停（秒） | 未分类（秒） |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    timing_rows = [("浏览器与采集批次", progress.get("timings", {}))]
    timing_rows += [(case["projectNo"], case["timings"]) for case in progress.get("cases", [])]
    for name, timing in timing_rows:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    name,
                    *(
                        timing.get(key + "Seconds")
                        for key in ("processing", "networkWait", "manualPause", "unclassified")
                    ),
                )
            )
            + " |"
        )
    if progress.get("issues") or any(
        case.get("issues") or case.get("timings", {}).get("issues")
        for case in progress.get("cases", [])
    ):
        lines += ["", "存在未完全核实的本地状态或计时记录；已保留原状态，未据此重试或写入系统。"]
    return "\n".join(lines) + "\n"


def write_report(path: Path, layout: Any, markdown: str) -> Path:
    path = _safe(path, layout.verification_records)
    if path.suffix.casefold() != ".md":
        raise ReportingError("报告输出必须是核验记录目录内的 Markdown 文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ReportingError("报告已存在，不覆盖既有核验记录")
    temporary = _safe(
        path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp"), layout.verification_records
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(temporary, path)  # Windows refuses replacing an existing destination.
        else:
            os.link(temporary, path)  # Atomic, no-clobber publication on POSIX.
    finally:
        temporary.unlink(missing_ok=True)
    return path
