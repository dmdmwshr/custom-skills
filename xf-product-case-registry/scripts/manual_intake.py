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
    from . import image_pdf
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import image_pdf
    import source_intake as source
    import workspace_state as ws


RETAINED_OBSERVATION = "SUPERSEDED_OBSERVATION_RETAINED"  # classification_rebind.STATUS
_REPLACEMENT_STATUSES = ("REPLACED_BY_MANUAL_INTAKE", RETAINED_OBSERVATION)


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


RESOLVED_STATUSES = {"RESOLVED_IDENTICAL", "RESOLVED_NO_NEW_MATERIAL", "RESOLVED_COMPLETED"}


def _member_digests(package: Path) -> dict[str, str]:
    """Safe-inspect a ZIP and map normalized member name -> content digest (files only)."""
    source.inspect_zip_package(package)
    digests: dict[str, str] = {}
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name, _key = source._zip_member_name(info)
            digests[name] = "sha256:" + hashlib.sha256(archive.read(info)).hexdigest()
    return digests


def _baseline_packages(layout: Any, record: dict[str, Any]) -> list[Path]:
    """Original ZIPs of earlier generations whose bytes still match their recorded digest."""
    known: set[str] = set()
    folders: list[Path] = []
    state = record.get("source") or {}
    if state.get("packageSha256"):
        known.add(state["packageSha256"])
    for entry in (record.get("history") or {}).get("completions") or []:
        if entry.get("packageSha256"):
            known.add(entry["packageSha256"])
        if entry.get("originalPath"):
            folders.append(layout.resolve_recorded_path(entry["originalPath"]))
    archive = record.get("archive") or {}
    if archive.get("originalPath"):
        folders.append(layout.resolve_recorded_path(archive["originalPath"]))
    for previous in state.get("previousGenerations") or []:
        if previous.get("packageSha256"):
            known.add(previous["packageSha256"])
    found: dict[str, Path] = {}
    candidates: list[Path] = []
    if state.get("packageRelativePath"):
        candidates.append(layout.root / state["packageRelativePath"])
    for folder in folders:
        if folder.is_dir():
            candidates.extend(sorted(folder.rglob("*.zip")))
    for path in candidates:
        if path.is_file():
            digest = source._sha256(path)
            if digest in known:
                found.setdefault(digest, path)
    return list(found.values())


def resolve_pending(layout: Any, project: str, intake_id: str | None = None) -> dict[str, Any]:
    """Compare PENDING_COMPARISON manual material with earlier generations and continue.

    Identical or already-covered material is only marked resolved (nothing is reprocessed).
    New material starts a new pending generation next to the untouched earlier ones; the
    normal inventory -> compose -> local-verify -> archive path then completes it. Every step
    is idempotent and re-runnable; the original package, old workspace, archive generation and
    manual fields are never moved, rewritten or removed.
    """
    project = ws._project_no(project)
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        record = data.get("cases", {}).get(project)
        if not record:
            raise ws.WorkspaceStateError("总账没有该项目")
        intakes = (record.get("source") or {}).get("manualIntakes") or {}
        wanted = [intake_id] if intake_id else sorted(intakes)
        if intake_id and intake_id not in intakes:
            raise ws.WorkspaceStateError("总账没有该手动交件")
        before = json.dumps(data, sort_keys=True)
        results = [
            _resolve_one(layout, data, project, record, key, intakes[key])
            for key in wanted
            if intakes[key].get("status") not in {"PRIMARY_RECEIVED", "ARCHIVED_PRIMARY"}
        ]
        if json.dumps(data, sort_keys=True) != before:  # re-runs of a resolved intake write nothing
            ws._save_waterline_unlocked(layout, data)
    return {"projectNo": project, "results": results, "nothingToCompare": not results}


def _resolve_one(
    layout: Any, data: dict[str, Any], project: str, record: dict[str, Any], key: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    out = {"intakeId": key}
    status = binding.get("status")
    package_sha = binding["packageSha256"]
    if status in RESOLVED_STATUSES:
        return {**out, "status": "REUSED", "resolution": status}
    if status not in {"PENDING_COMPARISON", "NEW_GENERATION_STARTED"}:
        return {**out, "status": "SKIPPED", "reason": f"状态 {status} 不需要比对"}
    receipt_path = layout.root / binding["evidencePath"]
    if not receipt_path.is_file() or source._sha256(receipt_path) != binding["evidenceSha256"]:
        raise ws.WorkspaceStateError("手动交件回执无法对账，保持待比对")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    package = layout.root / receipt["package"]["relativePath"]
    screenshot = layout.root / receipt["screenshot"]["relativePath"]
    if source._sha256(package) != package_sha or source._sha256(screenshot) != binding[
        "screenshotSha256"
    ]:
        raise ws.WorkspaceStateError("手动交件原件摘要已变化，保持待比对")
    completion = record.get("completion") or {}
    if status == "NEW_GENERATION_STARTED" and (
        record.get("state") == "COMPLETED" and completion.get("packageSha256") == package_sha
    ):
        binding.update(
            status="RESOLVED_COMPLETED",
            resolvedAt=ws._utc_now(),
            completedGeneration=(record.get("archive") or {}).get("generation"),
        )
        return {**out, "status": "RESOLVED_COMPLETED", "generation": binding["completedGeneration"]}

    baselines = _baseline_packages(layout, record)
    if status == "PENDING_COMPARISON" and not baselines:
        return {
            **out,
            "status": "BASELINE_MISSING",
            "reason": "找不到摘要仍与总账一致的既有原始 ZIP，无法证明相同或不同；保持待比对",
        }
    new_members = _member_digests(package)
    old_members: dict[str, set[str]] = {}
    for path in baselines:
        for name, digest in _member_digests(path).items():
            old_members.setdefault(name, set()).add(digest)
    added = sorted(n for n in new_members if n not in old_members)
    changed = sorted(
        n for n, d in new_members.items() if n in old_members and d not in old_members[n]
    )
    removed = sorted(n for n in old_members if n not in new_members)
    diff = {"added": added, "changed": changed, "removedFromNew": removed}
    if status == "PENDING_COMPARISON" and not added and not changed:
        resolution = "RESOLVED_IDENTICAL" if not removed else "RESOLVED_NO_NEW_MATERIAL"
        binding.update(
            status=resolution,
            resolvedAt=ws._utc_now(),
            comparedAgainst=[source._sha256(p) for p in baselines],
            diff=diff,
        )
        return {**out, "status": resolution, "diff": diff}

    # New material: needs a finished earlier generation and a free pending slot.
    pending = layout.pending_case_dir(project)
    if record.get("state") != "COMPLETED" and status == "PENDING_COMPARISON":
        return {
            **out,
            "status": "BLOCKED_ACTIVE_CASE",
            "reason": "原案卷仍在处理中，先完成或明确处置它再续接新增材料；保持待比对",
            "diff": diff,
        }
    target_package = pending / package.name
    target_screenshot = pending / screenshot.name
    if pending.exists():
        own = {package.name, screenshot.name}
        others = [c.name for c in pending.iterdir() if c.name not in own]
        if others:
            return {
                **out,
                "status": "BLOCKED_PENDING_OCCUPIED",
                "reason": "待处理目录已有其他内容，不覆盖；保持待比对",
                "diff": diff,
            }
    ws._validated_workspace_path(pending, layout.root, "新代际待处理目录", must_exist=False)
    pending.mkdir(parents=True, exist_ok=True)
    source._copy_immutable(package, target_package)
    source._copy_immutable(screenshot, target_screenshot)
    now = ws._utc_now()
    state = record.setdefault("source", {})
    if state.get("packageSha256") != package_sha:
        state.setdefault("previousGenerations", []).append(
            {
                "packageSha256": state.get("packageSha256"),
                "packageRelativePath": state.get("packageRelativePath"),
                "archiveGeneration": (record.get("archive") or {}).get("generation"),
                "supersededAt": now,
            }
        )
    state.update(
        packageSha256=package_sha,
        packageRelativePath=target_package.relative_to(layout.root).as_posix(),
        screenshotRelativePath=target_screenshot.relative_to(layout.root).as_posix(),
        status="PACKAGE_READY",
    )
    record.update(
        state="PENDING_ORGANIZATION",
        local={"status": "PENDING_ORGANIZATION"},
        errorSummary=None,
        workflowUpdatedAt=now,
        lastSeenAt=now,
    )
    binding.update(
        status="NEW_GENERATION_STARTED",
        startedAt=binding.get("startedAt") or now,
        diff=diff,
        pendingPackagePath=target_package.relative_to(layout.root).as_posix(),
    )
    return {
        **out,
        "status": "NEW_GENERATION_STARTED",
        "diff": diff,
        "pendingPackage": str(target_package),
        "next": "inventory -> compose -> local-verify；完成后再次运行 source resolve-manual 收口",
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


def _recorded(layout: Any, value: str) -> Path:
    return layout.root / str(value).replace("\\", "/")


def rebind_classification_evidence(
    layout: Any, project: str, intake_id: str, reason: str
) -> dict[str, Any]:
    """Bind a real manual intake as the replacement for a lost classification evidence file.

    The old recorded digest and its missing/mismatching state stay in the ledger as facts. Nothing
    is reconstructed or rehashed; the replacement is the case's own reviewed manual intake receipt
    (same ZIP digest, readable screenshot) and is bound by its own digest.
    """
    project = ws._project_no(project)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceStateError("新绑定来源证据必须记录明确原因")
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        record = data.get("cases", {}).get(project)
        if not record:
            raise ws.WorkspaceStateError("总账没有该项目")
        state = record.get("source") or {}
        classification = state.get("classification") or {}
        if not classification.get("evidencePath") or not classification.get("evidenceSha256"):
            raise ws.WorkspaceStateError("该案没有需要重新绑定的分类证据记录")
        old_path = _recorded(layout, classification["evidencePath"])
        actual = source._sha256(old_path) if old_path.is_file() else None
        if actual == classification["evidenceSha256"]:
            raise ws.WorkspaceStateError("原分类证据文件仍然完整，无需重新绑定")
        binding = (state.get("manualIntakes") or {}).get(intake_id)
        if not binding:
            raise ws.WorkspaceStateError("总账没有该手动交件")
        receipt_path = _recorded(layout, binding["evidencePath"])
        if not receipt_path.is_file() or source._sha256(receipt_path) != binding["evidenceSha256"]:
            raise ws.WorkspaceStateError("手动交件回执缺失或摘要不符，不能作为新绑定来源")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schemaVersion") != "ManualCaseIntakeReceiptV1"
            or (receipt.get("fields") or {}).get("projectNo") != project
        ):
            raise ws.WorkspaceStateError(
                "手动交件回执缺少基础身份记录（版本/项目编号），不能作为新绑定来源"
            )
        package = _recorded(layout, receipt["package"]["relativePath"])
        screenshot = _recorded(layout, receipt["screenshot"]["relativePath"])
        if (
            receipt.get("projectNo") != project
            or receipt["package"]["sha256"] != binding["packageSha256"]
            or binding["packageSha256"] != state.get("packageSha256")
            or not package.is_file()
            or source._sha256(package) != binding["packageSha256"]
            or not screenshot.is_file()
            or source._sha256(screenshot) != binding["screenshotSha256"]
        ):
            raise ws.WorkspaceStateError("手动交件与本案当前原始 ZIP/截图不一致，拒绝新绑定")
        try:
            size = image_pdf.decodable_png_size(screenshot.read_bytes())
        except image_pdf.ImageNormalizationError as error:
            raise ws.WorkspaceStateError(f"手动交件截图不是可解码的 PNG：{error}") from error
        if list(size) != list((receipt.get("screenshot") or {}).get("dimensions") or size):
            raise ws.WorkspaceStateError("手动交件截图实际尺寸与回执记录不符，拒绝新绑定")
        replacement = {
            "status": "REPLACED_BY_MANUAL_INTAKE",
            "intakeId": intake_id,
            "evidencePath": receipt_path.relative_to(layout.root).as_posix(),
            "evidenceSha256": binding["evidenceSha256"],
            "packageSha256": binding["packageSha256"],
            "screenshotSha256": binding["screenshotSha256"],
            "reason": reason.strip(),
            "replacedAt": ws._utc_now(),
            "supersededEvidencePath": classification["evidencePath"],
            "supersededEvidenceSha256": classification["evidenceSha256"],
            "supersededEvidenceState": "MISSING" if actual is None else "DIGEST_MISMATCH",
            **({"supersededActualSha256": actual} if actual else {}),
        }
        old = classification.get("evidenceReplacement")
        if old:
            same = ("intakeId", "evidenceSha256", "supersededEvidenceSha256")
            if all(old.get(k) == replacement[k] for k in same):
                return {"status": "REUSED", "projectNo": project, "intakeId": intake_id}
            raise ws.WorkspaceStateError("该案已绑定其他替代证据，拒绝覆盖")
        ws._deep_merge(record, {"source": {"classification": {"evidenceReplacement": replacement}}})
        ws._save_waterline_unlocked(layout, data)
    return {"status": "REBOUND", "projectNo": project, "intakeId": intake_id,
            "supersededEvidenceState": replacement["supersededEvidenceState"]}


def relocated_original_bindings(
    layout: Any, source_state: dict[str, Any], previous: Path, archived: Path
) -> dict[str, Any]:
    """Relocate exact evidence references after a verified move, keeping receipt bytes."""
    changes: dict[str, Any] = {}
    for key, binding in (source_state.get("manualIntakes") or {}).items():
        old_path = _recorded(layout, binding["evidencePath"])
        if not old_path.is_relative_to(previous):
            continue
        new_path = archived / old_path.relative_to(previous)
        if source._sha256(new_path) != binding["evidenceSha256"]:
            raise ws.WorkspaceStateError("归档后的手动交件回执摘要不一致")
        changes.setdefault("manualIntakes", {})[key] = {
            **binding,
            "evidencePath": new_path.relative_to(layout.root).as_posix(),
            "status": "ARCHIVED_PRIMARY",
            "archivedOriginalPath": str(archived),
        }
    for field in ("packageRelativePath", "screenshotRelativePath"):
        value = source_state.get(field)
        if value and _recorded(layout, value).is_relative_to(previous):
            path = archived / _recorded(layout, value).relative_to(previous)
            if not path.is_file():
                raise ws.WorkspaceStateError("归档后的手动原件不存在")
            changes[field] = path.relative_to(layout.root).as_posix()
    classification = source_state.get("classification") or {}
    evidence = classification.get("evidencePath")
    if evidence and _recorded(layout, evidence).is_relative_to(previous):
        new_path = archived / _recorded(layout, evidence).relative_to(previous)
        replacement = classification.get("evidenceReplacement")
        if new_path.is_file() and source._sha256(new_path) == classification.get("evidenceSha256"):
            changes["classification"] = {
                **classification,
                "evidencePath": new_path.relative_to(layout.root).as_posix(),
            }
        elif replacement and replacement.get("status") in _REPLACEMENT_STATUSES:
            # The original evidence file never existed in the archived folder (or was extended by a
            # later capture): keep the recorded old digest as a fact and relocate the real
            # replacement instead of failing.
            rebound = dict(replacement)
            old_replacement = _recorded(layout, replacement["evidencePath"])
            if old_replacement.is_relative_to(previous):
                moved = archived / old_replacement.relative_to(previous)
                if not moved.is_file() or source._sha256(moved) != replacement["evidenceSha256"]:
                    raise ws.WorkspaceStateError("归档后的替代来源证据摘要不一致")
                rebound["evidencePath"] = moved.relative_to(layout.root).as_posix()
            relocated = {**classification, "evidenceReplacement": rebound}
            if replacement["status"] == RETAINED_OBSERVATION and new_path.is_file():
                relocated["evidencePath"] = new_path.relative_to(layout.root).as_posix()
            changes["classification"] = relocated
        else:
            raise ws.WorkspaceStateError("归档后的分类证据摘要不一致")
    return changes
