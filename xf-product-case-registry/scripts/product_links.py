"""Reviewed recheck->initial product links beside an archived generation (ProductLinkReviewV1).

A recheck product is joined to an initial product only through an explicit ``initialProductRef``;
names, positions and counts never link them. Cases archived before the reference existed (or
whose archived generation must stay byte-identical) cannot gain it by amendment. This module
stores a reviewer's decision *next to* the archived generation instead of rewriting it:

* ``attach`` validates each link against the archived manifest and the archived source files the
  reviewer relied on (digest recorded), and writes ``productLinks`` into the case's ledger record,
  bound to the archive generation and to a canonical digest of the manifest.
* ``apply`` is read-only and is used by the lifecycle view: it returns a copy of the manifest in
  which the reviewed recheck products carry ``initialProductRef``. A different generation or a
  changed manifest makes the stored review inert (never silently reused).

The archived manifest, the original package, receipts and proofs are never modified.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__:
    from . import workspace_state as ws
else:
    import workspace_state as ws

SCHEMA_VERSION = "ProductLinkReviewV1"
BASIS = {
    "IDENTICAL_ATTRIBUTES": "名称、型号、标称生产者逐项相同，且本案文书点名该产品",
    "EXPLICIT_STATEMENT": "本案复查文书明确写明复查/整改/更换/仍未整改的就是该初查产品",
}
_MIN_STATEMENT = 8


def manifest_digest(manifest: dict[str, Any]) -> str:
    text = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _archived_workspace(layout: Any, project: str, record: dict[str, Any]) -> Path:
    candidate = (record.get("archive") or {}).get("workspacePath")
    if not candidate:
        raise ws.WorkspaceStateError("案卷没有已归档代际，不能登记复查产品对应")
    path = layout.resolve_recorded_path(candidate)
    suffix = path.name.removeprefix(project + "-")
    if (
        path.parent != layout.root / "工作区" / "历史工作区"
        or not path.name.startswith(project + "-")
        or not ws.ARCHIVE_GENERATION.fullmatch(suffix)
    ):
        raise ws.WorkspaceStateError("归档工作区不在历史工作区或代际名不合法")
    return path


def _load_manifest(path: Path, project: str) -> dict[str, Any]:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ws.WorkspaceStateError("归档清单无法读取") from error
    if not isinstance(manifest, dict) or (manifest.get("case") or {}).get("projectNo") != project:
        raise ws.WorkspaceStateError("归档清单不是本案")
    return manifest


def _products(manifest: dict[str, Any], block: str) -> dict[str, dict[str, Any]]:
    items = (manifest.get(block) or {}).get("products") or []
    return {
        p["clientRef"]: p
        for p in items
        if isinstance(p, dict) and isinstance(p.get("clientRef"), str)
    }


def apply(
    layout: Any, project: str, record: dict[str, Any], manifest: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Manifest copy carrying the reviewed ``initialProductRef`` values; the input is untouched."""
    review = record.get("productLinks")
    if (
        not manifest
        or not isinstance(review, dict)
        or review.get("schemaVersion") != SCHEMA_VERSION
    ):
        return manifest
    archive = record.get("archive") or {}
    workspace = str(archive.get("workspacePath") or "")
    if (
        record.get("state") != "COMPLETED"
        or not workspace
        or Path(workspace.replace("\\", "/")).name.removeprefix(project + "-")
        != review.get("archiveGeneration")
        or manifest_digest(manifest) != review.get("manifestDigest")
    ):
        return manifest  # another generation or a changed manifest: the review no longer applies
    initials = _products(manifest, "initialInspection")
    result = deepcopy(manifest)
    rechecks = _products(result, "recheckInspection")
    for link in review.get("links") or []:
        target = rechecks.get(link.get("recheckRef"))
        if (
            target is not None
            and not target.get("initialProductRef")
            and link.get("initialRef") in initials
        ):
            target["initialProductRef"] = link["initialRef"]
    return result


def attach(layout: Any, project: str, links_file: str) -> dict[str, Any]:
    path = Path(links_file).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ws.WorkspaceStateError("对应清单文件必须是存在的绝对路径")
    try:
        spec = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise ws.WorkspaceStateError("对应清单不是 JSON") from error
    reviewer = str((spec or {}).get("reviewer") or "").strip()
    links = (spec or {}).get("links")
    if not reviewer or not isinstance(links, list) or not links:
        raise ws.WorkspaceStateError("对应清单须含 reviewer 与非空 links")
    record = (ws.load_waterline(layout).get("cases") or {}).get(project)
    if not record or record.get("state") != "COMPLETED":
        raise ws.WorkspaceStateError("只对已归档完成的案卷登记复查产品对应")
    workspace = _archived_workspace(layout, project, record)
    manifest = _load_manifest(workspace, project)
    initials = _products(manifest, "initialInspection")
    rechecks = _products(manifest, "recheckInspection")
    stored: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, link in enumerate(links, 1):
        if not isinstance(link, dict):
            raise ws.WorkspaceStateError(f"第 {index} 条对应不是对象")
        recheck, initial, basis = link.get("recheckRef"), link.get("initialRef"), link.get("basis")
        statement = str(link.get("statement") or "").strip()
        if recheck not in rechecks or initial not in initials:
            raise ws.WorkspaceStateError(
                f"第 {index} 条对应的产品引用不在本案归档清单（复查/初查）内"
            )
        if rechecks[recheck].get("initialProductRef"):
            raise ws.WorkspaceStateError(
                f"第 {index} 条：{recheck} 清单内已有 initialProductRef，不覆盖"
            )
        if recheck in seen:
            raise ws.WorkspaceStateError(f"复查产品 {recheck} 重复对应")
        seen.add(recheck)
        if basis not in BASIS:
            raise ws.WorkspaceStateError(f"第 {index} 条依据类型必须是 {sorted(BASIS)} 之一")
        if len(statement) < _MIN_STATEMENT:
            raise ws.WorkspaceStateError(
                f"第 {index} 条必须写明依据文书与具体事实（至少 {_MIN_STATEMENT} 字）"
            )
        evidence = link.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ws.WorkspaceStateError(f"第 {index} 条必须列出所依据的归档来源文书")
        bound = []
        for item in evidence:
            relative = str((item or {}).get("file") or "")
            target = (workspace / "source" / relative).resolve()
            source_root = (workspace / "source").resolve()
            if not relative or source_root not in target.parents or not target.is_file():
                raise ws.WorkspaceStateError(f"第 {index} 条依据文书不在归档来源目录内：{relative}")
            digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            bound.append(
                {"file": relative, "sha256": digest, "pages": str(item.get("pages") or "")}
            )
        stored.append(
            {
                "recheckRef": recheck,
                "initialRef": initial,
                "basis": basis,
                "statement": statement,
                "evidence": bound,
            }
        )
    generation = workspace.name.removeprefix(project + "-")
    previous = record.get("productLinks") or {}
    merged: dict[str, dict[str, Any]] = {}
    if previous.get("archiveGeneration") == generation and previous.get(
        "manifestDigest"
    ) == manifest_digest(manifest):
        merged = {x["recheckRef"]: x for x in previous.get("links") or []}
    for item in stored:
        old = merged.get(item["recheckRef"])
        if old and old["initialRef"] != item["initialRef"]:
            raise ws.WorkspaceStateError(
                f"{item['recheckRef']} 已登记对应到另一初查产品，拒绝静默改写"
            )
        merged[item["recheckRef"]] = item
    block = {
        "schemaVersion": SCHEMA_VERSION,
        "archiveGeneration": generation,
        "manifestDigest": manifest_digest(manifest),
        "reviewer": reviewer,
        "reviewedAt": ws._utc_now(),
        "links": [merged[k] for k in sorted(merged)],
    }
    ws.upsert_case(layout, project, productLinks=block)
    return {
        "status": "ATTACHED",
        "projectNo": project,
        "generation": generation,
        "links": len(block["links"]),
    }
