"""Explicit local amendment of an already archived case generation (same original ZIP).

``amend`` never edits the parent generation. It copies the archived original material into a
fresh pending folder, prepares an empty active workspace with read-only seeds of the parent's
manifest/case data, fixes the parent identity and digests in the ledger and marks the old
completion as superseded (kept in ``history``). The normal inventory -> split -> compose ->
local-verify path then produces the new generation; archiving it re-checks that the parent is
unchanged, that the original ZIP is the same and that the manifest really differs. Failures roll
back; a re-run of the same request is answered from the recorded open amendment.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__:
    from . import local_validity
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import local_validity
    import source_intake as source
    import workspace_state as ws

SCHEMA_VERSION = "LocalAmendmentV1"
SEED_DIRECTORY = "amendment-seed"
ABANDONED_DIRECTORY = "已放弃修订"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def tree_digest(root: Path) -> str:
    """Digest over every file (relative path + content digest); order independent of the OS."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(source._sha256(path).encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _copy_tree(source_root: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for path in sorted(source_root.rglob("*")):
        destination = target / path.relative_to(source_root)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


def _parent_paths(layout: Any, parent: dict[str, Any]) -> tuple[Path, Path, Path]:
    return (
        layout.resolve_recorded_path(parent["originalPath"]),
        layout.resolve_recorded_path(parent["workspacePath"]),
        layout.resolve_recorded_path(parent["verificationRecord"]),
    )


def parent_problems(layout: Any, parent: dict[str, Any]) -> list[str]:
    """Why the recorded parent generation no longer matches what was fixed at amendment time."""
    try:
        original, workspace, receipt = _parent_paths(layout, parent)
    except (KeyError, ws.WorkspaceStateError) as error:
        return [f"父代路径无法解析：{type(error).__name__}"]
    problems: list[str] = []
    if not original.is_dir() or tree_digest(original) != parent.get("originalTreeSha256"):
        problems.append("父代原始材料目录已变化或缺失")
    if not workspace.is_dir() or tree_digest(workspace) != parent.get("workspaceTreeSha256"):
        problems.append("父代历史工作区已变化或缺失")
    if not receipt.is_file() or source._sha256(receipt) != parent.get("verificationRecordSha256"):
        problems.append("父代核验回执已变化或缺失")
    return problems


def _repoint(
    layout: Any, source_state: dict[str, Any], old_root: Path, new_root: Path
) -> dict[str, Any]:
    """Source bindings that point into the parent's archived folder, repointed to the copy."""

    def moved(value: str) -> str | None:
        path = layout.root / value
        if not path.is_relative_to(old_root):
            return None
        target = new_root / path.relative_to(old_root)
        if not target.is_file():
            raise ws.WorkspaceStateError(f"修订副本缺少原始证据：{value}")
        return target.relative_to(layout.root).as_posix()

    changes: dict[str, Any] = {}
    for field in ("packageRelativePath", "screenshotRelativePath"):
        if source_state.get(field) and (new := moved(source_state[field])):
            changes[field] = new
    for key, binding in (source_state.get("manualIntakes") or {}).items():
        if new := moved(binding.get("evidencePath", "")):
            changes.setdefault("manualIntakes", {})[key] = {**binding, "evidencePath": new}
    classification = source_state.get("classification") or {}
    if classification.get("evidencePath") and (new := moved(classification["evidencePath"])):
        changes["classification"] = {**classification, "evidencePath": new}
    for key, binding in (source_state.get("supplements") or {}).items():
        values = dict(binding)
        for field in ("fileRelativePath", "receiptRelativePath"):
            if new := moved(binding[field]):
                values[field] = new
        if values != binding:
            changes.setdefault("supplements", {})[key] = values
    return changes


def amend(layout: Any, project: str, reason: str) -> dict[str, Any]:
    project = ws._project_no(project)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceStateError("修订必须记录明确原因")
    reason = reason.strip()
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        record = (data.get("cases") or {}).get(project)
        if not record:
            raise ws.WorkspaceStateError("总账没有该项目")
        current = record.get("amendment") or {}
        if current.get("status") == "OPEN":
            if current.get("reason") != reason:
                raise ws.WorkspaceStateError(
                    "该案已有未完成修订，请先完成或放弃，不能用不同原因另开"
                )
            problems = parent_problems(layout, current["parent"])
            if problems:
                raise ws.WorkspaceStateError("未完成修订的父代已变化：" + "；".join(problems))
            return {"status": "REUSED", "projectNo": project, "amendmentId": current["id"]}
        if not local_validity.is_local_completion(record):
            raise ws.WorkspaceStateError("仅已完成并可信的本地归档案卷可以修订")
        reasons = local_validity.validate_local_completion(layout, project, record)
        if reasons:
            raise ws.WorkspaceStateError("父代完成记录不可信，先复核：" + "；".join(reasons))
        archive, completion = record["archive"], record["completion"]
        generation = archive["generation"]
        entries = (record.get("history") or {}).get("completions") or []
        if not entries or entries[-1].get("generation") != generation:
            raise ws.WorkspaceStateError("总账当前归档不是最新完成代际，拒绝在不明父代上修订")
        original = layout.resolve_recorded_path(archive["originalPath"])
        workspace = layout.resolve_recorded_path(archive["workspacePath"])
        receipt = layout.resolve_recorded_path(archive["verificationRecord"])
        ws._assert_safe_tree(original, layout.root, "父代原始材料")
        ws._assert_safe_tree(workspace, layout.root, "父代历史工作区")
        pending, work = layout.pending_case_dir(project), layout.work_case_dir(project)
        if pending.exists() or work.exists():
            raise ws.WorkspaceStateError("待处理目录或活动工作区已存在，拒绝覆盖；不会清理任何内容")
        parent = {
            "generation": generation,
            "manifestSha256": completion["manifestSha256"],
            "packageSha256": completion["packageSha256"],
            "originalPath": archive["originalPath"],
            "workspacePath": archive["workspacePath"],
            "verificationRecord": archive["verificationRecord"],
            "verificationRecordSha256": source._sha256(receipt),
            "originalTreeSha256": tree_digest(original),
            "workspaceTreeSha256": tree_digest(workspace),
            "fileCount": completion.get("fileCount"),
            "completedAt": record.get("completedAt"),
            "completion": deepcopy(completion),
        }
        created = ws._utc_now()
        amendment_id = "amd-" + _hash_text(f"{project}|{generation}|{reason}|{created}")[:20]
        tmp_pending = pending.with_name(f"{pending.name}.{amendment_id}.tmp")
        tmp_work = work.with_name(f"{work.name}.{amendment_id}.tmp")
        for tmp in (tmp_pending, tmp_work):
            ws._validated_workspace_path(tmp, layout.root, "修订临时目录", must_exist=False)
            if tmp.exists():
                raise ws.WorkspaceStateError("修订临时目录已存在，拒绝覆盖")
        placed: list[Path] = []
        try:
            _copy_tree(original, tmp_pending)
            if tree_digest(tmp_pending) != parent["originalTreeSha256"]:
                raise ws.WorkspaceStateError("修订副本与父代原始材料摘要不一致")
            tmp_work.mkdir(parents=True)
            seed = tmp_work / SEED_DIRECTORY
            seed.mkdir()
            for name in (
                "manifest.json",
                "case-data.json",
                "split-plan.json",
                "field-resolution.json",
            ):
                if (workspace / name).is_file():
                    shutil.copyfile(workspace / name, seed / name)
            (tmp_work / "amendment-parent.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "id": amendment_id,
                        "reason": reason,
                        "createdAt": created,
                        "parent": {k: v for k, v in parent.items() if k != "completion"},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            previous_source = deepcopy(record.get("source") or {})
            tmp_pending.replace(pending)
            placed.append(pending)
            tmp_work.replace(work)
            placed.append(work)
            source_changes = _repoint(layout, previous_source, original, pending)
            amendment = {
                "schemaVersion": SCHEMA_VERSION,
                "id": amendment_id,
                "status": "OPEN",
                "reason": reason,
                "createdAt": created,
                "parent": parent,
                "pendingPath": str(pending),
                "workPath": str(work),
                "previousSource": {
                    k: deepcopy(previous_source[k]) for k in source_changes if k in previous_source
                },
            }
            ws._deep_merge(record, {"source": source_changes})
            record["amendment"] = amendment
            record["completion"] = {
                "mode": "LOCAL_V1",
                "status": "SUPERSEDED_BY_AMENDMENT",
                "amendmentId": amendment_id,
                "supersededGeneration": generation,
            }
            record["state"] = "PENDING_ORGANIZATION"
            record["local"] = {"status": "AMENDING"}
            record["errorSummary"] = None
            record["workflowUpdatedAt"] = created
            ws._save_waterline_unlocked(layout, data)
        except Exception:
            for path in reversed(placed):
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
            for tmp in (tmp_pending, tmp_work):
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
            raise
    return {
        "status": "OPENED",
        "projectNo": project,
        "amendmentId": amendment_id,
        "parentGeneration": generation,
        "pending": str(pending),
        "workDir": str(work),
        "seed": str(work / SEED_DIRECTORY),
        "next": "inventory -> 编辑 case-data/split-plan -> split -> compose -> local-verify；"
        "须在工作区提供含 amendmentId 的 field-resolution.json",
    }


def preflight_archive(
    layout: Any, project: str, record: dict[str, Any], verification: dict[str, Any]
) -> dict[str, Any] | None:
    """Checks that must hold right before archiving an amendment's new generation."""
    amendment = record.get("amendment") or {}
    if amendment.get("status") != "OPEN":
        return None
    parent = amendment["parent"]
    problems = parent_problems(layout, parent)
    if problems:
        raise ws.WorkspaceStateError("父代已变化，拒绝归档修订：" + "；".join(problems))
    entries = (record.get("history") or {}).get("completions") or []
    if not entries or entries[-1].get("generation") != parent["generation"]:
        raise ws.WorkspaceStateError("总账最新完成代际已不是修订的父代，拒绝归档")
    if verification.get("packageSha256") != parent["packageSha256"]:
        raise ws.WorkspaceStateError("修订必须基于同一原始 ZIP；包摘要与父代不同请走新增材料流程")
    proof = verification.get("amendment") or {}
    if proof.get("id") != amendment.get("id"):
        raise ws.WorkspaceStateError("核验凭证没有绑定当前修订")
    changes = proof.get("changes") or {}
    if verification.get("manifestSha256") == parent["manifestSha256"] or not any(
        changes.get(key) for key in ("added", "removed", "changed")
    ):
        raise ws.WorkspaceStateError("修订后的清单与父代没有实质差异，没有可归档的变化")
    return amendment


def completed_block(
    amendment: dict[str, Any], generation: str, completed_at: str
) -> dict[str, Any]:
    return {
        **{k: v for k, v in amendment.items() if k != "parent"},
        "parent": {k: v for k, v in amendment["parent"].items() if k != "completion"},
        "status": "COMPLETED",
        "completedGeneration": generation,
        "completedAt": completed_at,
    }


def summarize_changes(parent_manifest: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Traceable diff of slots/products/files between parent and amended manifest."""

    def index(value: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for section in ("initialInspection", "recheckInspection"):
            inspection = value.get(section)
            if inspection is not None:  # own fields (date, stage, ref), not only products
                result[f"inspection:{section}"] = {
                    k: v for k, v in inspection.items() if k != "products"
                }
            for product in (value.get(section) or {}).get("products") or []:
                result[f"product:{section}:{product.get('clientRef')}"] = product
        for slot in value.get("documentSlots") or []:
            owner = (
                slot.get("inspectionRef")
                or slot.get("productRef")
                or slot.get("notificationTarget")
                or "case"
            )
            result[f"slot:{slot.get('slotCode')}:{owner}"] = slot
        for item in value.get("otherAttachments") or []:
            result[f"attachment:{item.get('title')}"] = item
        result["case"] = value.get("case")
        for item in value.get("files") or []:
            result[f"file:{item.get('clientRef')}"] = {
                k: item.get(k) for k in ("relativePath", "sha256", "pageCount")
            }
        return result

    old, new = index(parent_manifest), index(manifest)
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(
            k
            for k in set(old) & set(new)
            if json.dumps(old[k], sort_keys=True) != json.dumps(new[k], sort_keys=True)
        ),
    }


def abandon(layout: Any, project: str, reason: str) -> dict[str, Any]:
    project = ws._project_no(project)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceStateError("放弃修订必须记录明确原因")
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        record = (data.get("cases") or {}).get(project)
        amendment = (record or {}).get("amendment") or {}
        if amendment.get("status") == "ABANDONED":
            return {"status": "REUSED", "projectNo": project, "amendmentId": amendment["id"]}
        if amendment.get("status") != "OPEN":
            raise ws.WorkspaceStateError("没有可放弃的未完成修订")
        problems = parent_problems(layout, amendment["parent"])
        if problems:
            raise ws.WorkspaceStateError("父代已变化，不自动回滚：" + "；".join(problems))
        pending, work = layout.pending_case_dir(project), layout.work_case_dir(project)
        stamp = ws._timestamp_slug(ws._utc_now())
        keep_root = layout.raw_root / ABANDONED_DIRECTORY / project / amendment["id"]
        work_target = layout.history_workspaces / f"{project}-amendment-abandoned-{amendment['id']}"
        keep_root.parent.mkdir(parents=True, exist_ok=True)
        ws._assert_safe_tree(layout.raw_root, layout.root, "原始案卷根")
        for target in (keep_root, work_target):
            ws._validated_workspace_path(target, layout.root, "放弃修订保留目录", must_exist=False)
            if target.exists():
                raise ws.WorkspaceStateError("放弃修订的保留目录已存在，拒绝覆盖")
        moved: list[tuple[Path, Path]] = []
        try:
            if pending.exists():
                ws._assert_safe_tree(pending, layout.root, "修订待处理目录")
                pending.replace(keep_root)
                moved.append((keep_root, pending))
            if work.exists():
                ws._assert_safe_tree(work, layout.root, "修订活动工作区")
                work.replace(work_target)
                moved.append((work_target, work))
            parent = amendment["parent"]
            ws._deep_merge(record, {"source": amendment.get("previousSource") or {}})
            record["completion"] = deepcopy(parent["completion"])
            record["state"] = "COMPLETED"
            record["local"] = {"status": "ARCHIVED"}
            record["amendment"] = {
                **amendment,
                "status": "ABANDONED",
                "abandonedAt": ws._utc_now(),
                "abandonReason": reason.strip(),
                "retainedPending": str(keep_root),
                "retainedWorkspace": str(work_target),
            }
            history = record.setdefault("history", {})
            history.setdefault("amendments", []).append(
                {
                    "id": amendment["id"],
                    "status": "ABANDONED",
                    "parentGeneration": parent["generation"],
                    "reason": amendment["reason"],
                    "abandonReason": reason.strip(),
                    "at": stamp,
                }
            )
            ws._save_waterline_unlocked(layout, data)
        except Exception:
            for moved_to, original in reversed(moved):
                if moved_to.exists() and not original.exists():
                    moved_to.replace(original)
            raise
    return {
        "status": "ABANDONED",
        "projectNo": project,
        "amendmentId": amendment["id"],
        "retainedPending": str(keep_root),
        "retainedWorkspace": str(work_target),
    }


__all__ = [
    "abandon",
    "amend",
    "completed_block",
    "parent_problems",
    "preflight_archive",
    "summarize_changes",
    "tree_digest",
]
