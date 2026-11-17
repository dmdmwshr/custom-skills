"""Same-product chains in Feishu: which initial product each recheck product answers (2026-11-17, a31).

The case manifest ties a recheck product to an initial product only through an explicit ``initialProductRef`` (or a reviewed
``ProductLinkReviewV1``); names, positions and quantities never link products. The 产品明细 column ``对应初查产品键`` carries the
same fact into Feishu so that the automatic 是否行政处罚案 formula can follow the chain. This module reads the local archived
manifests (read-only), compares them with fresh cloud product rows and writes a ``FeishuFieldPlanV1`` that only fills empty
cells (``expectedOld=null``); the plan goes through ``feishu-field-plan-build`` and ``feishu-source-sync --execute`` like the
quantity plan. A cell that already holds a different value is held for a person, never overwritten.

Standard library only (workspace state and product links are the skill's own modules).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import product_links
    from . import workspace_state as ws
else:
    import product_links
    import workspace_state as ws

PLAN_SCHEMA = "FeishuFieldPlanV1"
REPORT_SCHEMA = "ProductChainPlanReportV1"
PRODUCT_TABLE, PRODUCT_KEY, PRODUCT_NAME = "产品明细", "fldqlwHjUW", "fld6QPrwZD"
LINK_NAME = "对应初查产品键"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell(value: Any) -> Any:
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _same_name(left: Any, right: Any) -> bool:
    table = str.maketrans("（）", "()")
    norm = lambda text: "".join(str(text or "").split()).translate(table)  # noqa: E731
    return norm(left) == norm(right)


def _archive_dir(layout: ws.BusinessLayout, project: str, record: dict[str, Any], business_root: Path) -> Path:
    """The archived workspace of a case. Recorded paths of the historical (E side) root are mapped through the layout when it can; where the
    running platform cannot interpret a foreign drive path (WSL), the fixed tail ``工作区/历史工作区/<项目>-<代际>`` is taken under the verified
    business root — the same logical data, checked against the case number and the archive generation name."""
    try:
        return product_links._archived_workspace(layout, project, record)
    except ws.WorkspaceStateError:
        recorded = str((record.get("archive") or {}).get("workspacePath") or "").replace("\\", "/")
        parts = [p for p in recorded.split("/") if p]
        name = parts[-1] if parts else ""
        suffix = name.removeprefix(project + "-")
        if len(parts) < 3 or parts[-3:-1] != ["工作区", "历史工作区"] or not name.startswith(project + "-") or not ws.ARCHIVE_GENERATION.fullmatch(suffix):
            raise
        path = business_root / "工作区" / "历史工作区" / name
        if not path.is_dir():
            raise ws.WorkspaceStateError("历史工作区目录在当前资料根下不存在")
        return path


def local_chains(layout: ws.BusinessLayout, waterline_cases: dict[str, dict[str, Any]], business_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """project -> {manifest ref, links: [{recheckSeq, initialSeq, recheckName, initialName}]} for every archived case whose manifest (with the
    reviewed links applied) names at least one initialProductRef. ``held`` maps projects whose manifest could not be read to the reason."""
    chains: dict[str, dict[str, Any]] = {}
    held: dict[str, str] = {}
    for project, record in sorted(waterline_cases.items()):
        if record.get("state") != "COMPLETED" or not (record.get("archive") or {}).get("workspacePath"):
            continue
        try:
            workspace = _archive_dir(layout, project, record, business_root)
            manifest = product_links._load_manifest(workspace, project)
        except ws.WorkspaceStateError as error:
            held[project] = str(error)
            continue
        effective = product_links.apply(layout, project, record, manifest) or manifest
        initials = [p for p in (effective.get("initialInspection") or {}).get("products") or [] if isinstance(p, dict)]
        rechecks = [p for p in (effective.get("recheckInspection") or {}).get("products") or [] if isinstance(p, dict)]
        by_ref = {p.get("clientRef"): (index, p) for index, p in enumerate(initials, start=1) if p.get("clientRef")}
        links = []
        for number, product in enumerate(rechecks, start=1):
            ref = product.get("initialProductRef")
            if isinstance(ref, str) and ref in by_ref:
                index, target = by_ref[ref]
                links.append({"recheckSeq": number, "initialSeq": index, "recheckName": product.get("name"), "initialName": target.get("name"),
                              "initialRef": ref, "recheckRef": product.get("clientRef")})
        if links:
            manifest_path = Path(workspace) / "manifest.json"
            chains[project] = {"manifest": {"path": manifest_path.relative_to(business_root).as_posix(), "sha256": _sha(manifest_path)}, "links": links,
                               "reviewed": isinstance(record.get("productLinks"), dict)}
    return chains, held


def build_plan(*, chains: dict[str, dict[str, Any]], cloud_rows: dict[str, dict[str, Any]], link_field_id: str, observed_at: str, out_dir: Path) -> dict[str, Any]:
    """``cloud_rows``: recordId -> {fieldId: cell} of 产品明细 (key, name, link). Only empty link cells get an entry."""
    by_key: dict[str, tuple[str, dict[str, Any]]] = {}
    for record_id, row in cloud_rows.items():
        key = _cell(row.get(PRODUCT_KEY))
        if isinstance(key, str) and key:
            by_key[key] = (record_id, row)
    entries: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    already = 0
    for project in sorted(chains):
        info = chains[project]
        for link in info["links"]:
            initial_key, recheck_key = f"{project}|初查|{link['initialSeq']}", f"{project}|复查|{link['recheckSeq']}"
            initial, recheck = by_key.get(initial_key), by_key.get(recheck_key)
            reason = None
            if recheck is None or initial is None:
                reason = "云端没有对应的初查或复查产品行"
            elif not _same_name(_cell(initial[1].get(PRODUCT_NAME)), link["initialName"]) or not _same_name(_cell(recheck[1].get(PRODUCT_NAME)), link["recheckName"]):
                reason = "云端产品名称与清单不一致，不凭位置连接"
            if reason is not None:
                held.append({"projectNo": project, "recheckKey": recheck_key, "initialKey": initial_key, "reason": reason})
                continue
            current = _cell(recheck[1].get(link_field_id))
            if _empty(current):
                entries.append({"table": PRODUCT_TABLE, "recordId": recheck[0], "projectNo": project, "stableKey": {"fieldId": PRODUCT_KEY, "value": recheck_key},
                                "fields": {link_field_id: {"new": initial_key, "expectedOld": None, "evidence": [info["manifest"]],
                                                           "basisRef": f"{link['recheckRef']} 经 {'已审核的 ProductLinkReviewV1' if info['reviewed'] else '清单 initialProductRef'} 对应 {link['initialRef']}",
                                                           "observedAt": observed_at}}})
            elif current == initial_key:
                already += 1
            else:
                held.append({"projectNo": project, "recheckKey": recheck_key, "initialKey": initial_key, "reason": f"云端已有不同的对应值 {current!r}，保留"})
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = None
    if entries:
        plan_path = out_dir / "产品链字段计划.json"
        plan_path.write_text(json.dumps({"schemaVersion": PLAN_SCHEMA, "entries": entries}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        plan = {"path": str(plan_path), "sha256": _sha(plan_path)}
    report = {"schemaVersion": REPORT_SCHEMA, "observedAt": observed_at, "counts": {"casesWithChains": len(chains), "links": sum(len(c["links"]) for c in chains.values()),
                                                                                  "toFill": len(entries), "alreadyLinked": already, "held": len(held)},
              "plan": plan, "held": held}
    (out_dir / "产品链计划报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report
