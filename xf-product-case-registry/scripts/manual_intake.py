"""Explicit, reviewed manual ZIP/screenshot intake; never simulates browser capture."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader

if __package__:
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import source_intake as source
    import workspace_state as ws


def _digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _file(path: str, expected: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ws.WorkspaceStateError("手动交件文件必须使用绝对路径")
    ws._reject_reparse_chain(candidate, "手动交件")
    if not candidate.is_file() or source._sha256(candidate) != expected:
        raise ws.WorkspaceStateError("手动交件文件不存在或摘要已变化")
    return candidate


def _png_dimensions(path: Path) -> list[int]:
    raw = path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ws.WorkspaceStateError("截图不是有效 PNG")
    position, dimensions, has_data = 8, None, False
    while position + 12 <= len(raw):
        size = struct.unpack(">I", raw[position : position + 4])[0]
        kind = raw[position + 4 : position + 8]
        end = position + 8 + size
        if (
            end + 4 > len(raw)
            or zlib.crc32(raw[position + 4 : end]) != struct.unpack(">I", raw[end : end + 4])[0]
        ):
            break
        if kind == b"IHDR" and position == 8 and size == 13:
            dimensions = list(struct.unpack(">II", raw[position + 8 : position + 16]))
        if kind == b"IDAT":
            has_data = True
        if kind == b"IEND" and dimensions and has_data and end + 4 == len(raw):
            return dimensions
        position = end + 4
    raise ws.WorkspaceStateError("PNG 截图不完整或校验失败")


def accept(layout: Any, review: dict[str, Any]) -> dict[str, Any]:
    """Receive a reviewed pair, preserving user inputs and existing workflow state."""
    if review.get("schemaVersion") != "ManualCaseIntakeV1":
        raise ws.WorkspaceStateError("手动交件核查单版本不支持")
    project = ws._project_no(review.get("projectNo", ""))
    fields = source._clean_evidence(review.get("fields"))
    if not isinstance(fields, dict) or fields.get("projectNo") != project:
        raise ws.WorkspaceStateError("手动详情必须显式确认项目编号")
    unit, brigade = fields.get("unitName"), fields.get("brigadeCode")
    if (
        not isinstance(unit, str)
        or not unit.strip()
        or brigade not in source.BRIGADE_SCOPES - {"ALL"}
    ):
        raise ws.WorkspaceStateError("手动详情缺少单位或可信大队代码")
    image_review = review.get("screenshotReview") or {}
    required = {"projectNo", "unitName", "brigade", "productResults", "documentDirectory"}
    if (
        image_review.get("readable") is not True
        or not required <= set(image_review.get("readableFields") or [])
        or image_review.get("projectNo") != project
        or image_review.get("unitName") != unit
        or image_review.get("brigadeCode") != brigade
    ):
        raise ws.WorkspaceStateError("必须逐图核清案号、单位、大队、产品结果及文书目录")
    package = _file(review.get("packagePath", ""), review.get("packageSha256", ""))
    screenshot = _file(review.get("screenshotPath", ""), review.get("screenshotSha256", ""))
    if package.suffix.lower() != ".zip" or screenshot.suffix.lower() != ".png":
        raise ws.WorkspaceStateError("手动交件当前支持原始 ZIP 和原始 PNG 截图")
    dimensions = _png_dimensions(screenshot)
    if dimensions != image_review.get("dimensions") or min(dimensions) < 400:
        raise ws.WorkspaceStateError("截图尺寸与实测核查不符，或仅为缩略图")
    inspection = source.inspect_zip_package(package)
    proof = review.get("documentMatch") or {}
    match = None
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            name, _key = source._zip_member_name(info)
            if name != proof.get("entryName"):
                continue
            content = archive.read(info)
            if "sha256:" + hashlib.sha256(content).hexdigest() != proof.get("sha256"):
                raise ws.WorkspaceStateError("包内身份文书摘要不符")
            reader = PdfReader(io.BytesIO(content), strict=False)
            text = "".join(page.extract_text() or "" for page in reader.pages)
            compact = "".join(text.split())
            document_unit, document_no = proof.get("unitName"), proof.get("documentNo")
            if not all(isinstance(v, str) and v.strip() for v in (document_unit, document_no)):
                raise ws.WorkspaceStateError("包内身份文书缺少可核对单位、文号")
            if any("".join(v.split()) not in compact for v in (document_unit, document_no)):
                raise ws.WorkspaceStateError("包内文书正文无法支持核查单身份")
            if document_unit != unit and not proof.get("identityDifferenceResolution"):
                raise ws.WorkspaceStateError("截图与文书单位名称有差异，必须保存对应证据与裁决")
            match = {
                k: proof[k]
                for k in (
                    "entryName",
                    "sha256",
                    "unitName",
                    "documentNo",
                    "identityDifferenceResolution",
                )
                if k in proof
            }
            break
    if match is None:
        raise ws.WorkspaceStateError("ZIP 中没有核查单指定的身份文书")
    pair = {
        "projectNo": project,
        "packageSha256": review["packageSha256"],
        "screenshotSha256": review["screenshotSha256"],
    }
    key = _digest(pair).removeprefix("sha256:")[:24]
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        current = data.get("cases", {}).get(project, {})
        old = (current.get("source") or {}).get("manualIntakes", {}).get(key)
        if old:
            path = layout.root / old["evidencePath"]
            if source._sha256(path) != old["evidenceSha256"]:
                raise ws.WorkspaceStateError("已接收手动交件回执无法对账")
            return {
                "status": "REUSED",
                "projectNo": project,
                "intakeId": key,
                "evidencePath": str(path),
            }
        primary = not current.get("archive") and not (current.get("source") or {}).get(
            "packageRelativePath"
        )
        target = (
            layout.pending_case_dir(project)
            if primary
            else layout.raw_root / "手动交件" / project / key
        )
        ws._reject_reparse_chain(target, "手动交件目标")
        target.relative_to(layout.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        ws._validated_workspace_path(target, layout.root, "手动交件目标", must_exist=False)
        target.mkdir(parents=True, exist_ok=True)
        package_target = target / f"{project}_案卷包_{review['packageSha256'][7:19]}.zip"
        screenshot_target = target / f"案卷详情_{review['screenshotSha256'][7:19]}.png"
        source._copy_immutable(package, package_target)
        source._copy_immutable(screenshot, screenshot_target)
        if (
            source._sha256(package_target) != review["packageSha256"]
            or source._sha256(screenshot_target) != review["screenshotSha256"]
        ):
            raise ws.WorkspaceStateError("归档副本与手动原件摘要不一致")
        now = ws._utc_now()
        receipt = {
            "schemaVersion": "ManualCaseIntakeReceiptV1",
            "projectNo": project,
            "intakeId": key,
            "intakeMode": "MANUAL_PROVIDED",
            "receivedAt": now,
            "fields": fields,
            "screenshotReview": image_review,
            "documentMatch": match,
            "package": {
                "originalName": package.name,
                "providedPath": str(package),
                "relativePath": package_target.relative_to(layout.root).as_posix(),
                "sha256": review["packageSha256"],
                "sizeBytes": package.stat().st_size,
                "fileCount": inspection["fileCount"],
            },
            "screenshot": {
                "originalName": screenshot.name,
                "providedPath": str(screenshot),
                "relativePath": screenshot_target.relative_to(layout.root).as_posix(),
                "sha256": review["screenshotSha256"],
                "dimensions": dimensions,
            },
            "userInputsPreserved": True,
            "browserCapturePerformed": False,
            "downloadBaseline": None,
            "rwid": None,
        }
        receipt_path = target / "manual-intake.json"
        if receipt_path.exists():
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
            if any(previous.get(k) != receipt.get(k) for k in receipt if k != "receivedAt"):
                raise ws.WorkspaceStateError("手动交件回执已存在不同内容，拒绝覆盖")
            receipt = previous
        else:
            ws._atomic_bytes(
                receipt_path,
                (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode(),
                overwrite=False,
            )
        binding = {
            "evidencePath": receipt_path.relative_to(layout.root).as_posix(),
            "evidenceSha256": source._sha256(receipt_path),
            "packageSha256": review["packageSha256"],
            "screenshotSha256": review["screenshotSha256"],
            "status": "PRIMARY_RECEIVED" if primary else "PENDING_COMPARISON",
        }
        changes: dict[str, Any] = {"source": {"manualIntakes": {key: binding}}}
        if primary:
            changes.update(
                unitName=unit,
                brigadeCode=brigade,
                state="PENDING_ORGANIZATION",
                local={"status": "PENDING_ORGANIZATION"},
            )
            changes["source"].update(
                status="PACKAGE_READY",
                intakeMode="MANUAL_PROVIDED",
                projectIdentitySource="MANUAL_DETAIL",
                packageSha256=review["packageSha256"],
                packageRelativePath=receipt["package"]["relativePath"],
                screenshotRelativePath=receipt["screenshot"]["relativePath"],
                manualReceivedAt=receipt["receivedAt"],
            )
        if not current:
            current = {
                "projectNo": project,
                "state": "DISCOVERED",
                "firstSeenAt": now,
                "lastSeenAt": now,
                "source": {},
                "local": {"status": "NOT_STARTED"},
                "upload": {"status": "NOT_STARTED"},
                "nasVerification": {"status": "NOT_STARTED"},
                "completedAt": None,
                "errorSummary": None,
            }
        ws._deep_merge(current, changes)
        current["workflowUpdatedAt"] = now
        data["cases"][project] = current
        ws._save_waterline_unlocked(layout, data)
        return {
            "status": binding["status"],
            "projectNo": project,
            "intakeId": key,
            "evidencePath": str(receipt_path),
        }


def acknowledge_gaps(layout: Any, project: str, reason: str) -> dict[str, Any]:
    """Record explicit user direction; retain gaps and bind the precise current list."""
    project = ws._project_no(project)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceStateError("来源问题收口必须记录当前用户明确口径")
    record = ws.load_waterline(layout).get("cases", {}).get(project, {})
    gaps = (record.get("source") or {}).get("materialGaps")
    if not isinstance(gaps, list) or not gaps:
        raise ws.WorkspaceStateError("当前案卷没有已记录来源材料缺口")
    disposition = {
        "status": "SOURCE_ISSUES_RETAINED",
        "owner": "SOURCE_BRIGADE",
        "reason": reason.strip(),
        "acknowledgedAt": ws._utc_now(),
        "materialGapsSha256": _digest(gaps),
        "localOrganizationFailure": False,
    }
    ws.upsert_case(layout, project, source={"materialGapDisposition": disposition})
    return {"projectNo": project, "status": disposition["status"], "retainedGapCount": len(gaps)}


def gaps_acknowledged(source_state: dict[str, Any]) -> bool:
    gaps = source_state.get("materialGaps")
    disposition = source_state.get("materialGapDisposition") or {}
    return bool(
        gaps
        and disposition.get("status") == "SOURCE_ISSUES_RETAINED"
        and disposition.get("owner") == "SOURCE_BRIGADE"
        and disposition.get("reason")
        and disposition.get("acknowledgedAt")
        and disposition.get("materialGapsSha256") == _digest(gaps)
    )
