"""Initial-inspection-date scope of the active case set (2026-11-11 user rule).

The active set is every fire-product supervision case whose *real* initial inspection date is on or after
2026-01-01, whatever its project-number year and including later years. The project-number year, the task creation
date, the acceptance date and download time never decide scope. The source system's task list filters by the task's
"检查日期" (the initial inspection day: it equalled the electronic record's JCRQ in every case checked), and that
list, not the number year, is the evidence that a case is inside or outside the set.

This module provides three things, all keyed by the one project-number ledger:

* ``reconcile_listing``   read-only join of the ledger with a saved source listing taken with the 检查日期 filter;
* ``exclude_out_of_scope`` move proven out-of-scope cases out of the active set with a byte-exact, restorable copy;
* ``restore_excluded``    the exact inverse, so an exclusion is never destructive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import workspace_state as ws
else:
    import workspace_state as ws

SCOPE_FROM = date(2026, 1, 1)
LISTING_SCHEMA = "InitialDateScopeListingV1"
PLAN_SCHEMA = "InitialDateScopeExclusionPlanV1"
REGISTER_SCHEMA = "InitialDateScopeExclusionRegisterV1"
RETENTION_DIR = "范围外保留"
REGISTER_NAME = "exclusion-register.json"
PRODUCT_CASE = re.compile(r"^\d{8}C\d{9}$")
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{5,79}$")
# Registry dates taken from the acceptance day may sit up to about a month after the true inspection day, so a
# fallback date this close to the boundary proves nothing by itself.
FALLBACK_BOUNDARY_DAYS = 31


class ScopeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day(value: Any, label: str) -> date:
    if not isinstance(value, str) or ISO_DAY.fullmatch(value) is None:
        raise ScopeError(f"{label}必须是 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ScopeError(f"{label}不是有效日期") from error


def normalize_unit(text: Any) -> str:
    """Unit-name join key: NFKC, brackets unified, all whitespace dropped (the source pads names inconsistently)."""

    value = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


def in_scope_day(day: date | None, *, scope_from: date = SCOPE_FROM) -> bool | None:
    """True/False for a proven day; None when the day is unknown (unknown is never excluded)."""

    return None if day is None else day >= scope_from


def date_basis_proves_scope(value: str | None, *, fallback: bool, scope_from: date = SCOPE_FROM) -> str:
    """IN / OUT / UNPROVEN for a registry initial date and how it was obtained.

    A fallback (acceptance-day) date proves nothing within ``FALLBACK_BOUNDARY_DAYS`` of the boundary either way, and an
    unknown date is never out of scope.
    """

    if not value:
        return "UNPROVEN"
    day = _day(value, "初查日期")
    if fallback:
        delta = (day - scope_from).days
        if -FALLBACK_BOUNDARY_DAYS <= delta < FALLBACK_BOUNDARY_DAYS:
            return "UNPROVEN"
    return "IN" if day >= scope_from else "OUT"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ScopeError(f"{label}无法读取或不是 JSON：{path}") from error


# --------------------------------------------------------------------------------------------- listing & reconcile


def load_listing(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate a saved source listing taken with the 检查日期 filter (read-only evidence)."""

    source = Path(path)
    value = _read_json(source, "来源清单")
    if not isinstance(value, dict) or value.get("schemaVersion") != LISTING_SCHEMA:
        raise ScopeError("来源清单版本不支持")
    filters = value.get("filters")
    if not isinstance(filters, dict) or filters.get("dateField") != "检查日期":
        raise ScopeError("来源清单必须按“检查日期”筛选，创建时间/受理日期不是初查日期")
    start = _day(filters.get("start"), "筛选起始日")
    if start > SCOPE_FROM:
        raise ScopeError("检查日期筛选起始日晚于 2026-01-01，不能证明覆盖")
    if filters.get("creationRangeCleared") is not True:
        raise ScopeError("创建时间筛选必须放宽(creationRangeCleared)，否则 2026 年前创建的任务会被漏掉")
    if not str(filters.get("jurisdiction") or "").startswith("全部管辖单位"):
        raise ScopeError("管辖范围必须是全部管辖单位")
    types = value.get("taskTypes")
    if not isinstance(types, list) or not types:
        raise ScopeError("来源清单缺少任务类型")
    seen: set[str] = set()
    for item in types:
        name = item.get("type") if isinstance(item, dict) else None
        rows = item.get("rows") if isinstance(item, dict) else None
        if not name or name in seen or not isinstance(rows, list):
            raise ScopeError("来源清单任务类型重复或缺少明细")
        seen.add(name)
        total = item.get("totalCount")
        if total != len(rows):
            raise ScopeError(f"{name}：页面总数 {total} 与读取行数 {len(rows)} 不一致，不能当作完整清单")
        for row in rows:
            if not isinstance(row, dict) or not row.get("unit"):
                raise ScopeError(f"{name}：存在缺单位名称的行")
            if _day(row.get("inspectionDate"), f"{name}检查日期") < start:
                raise ScopeError(f"{name}：行检查日期早于筛选起始日，筛选未生效")
    value["_sha256"] = _sha256_file(source)
    return value


def _ledger_cases(layout: Any) -> dict[str, dict[str, Any]]:
    cases = ws.load_waterline(layout)["cases"]
    return {p: r for p, r in cases.items() if PRODUCT_CASE.fullmatch(p)}


def _registry_day(record: Mapping[str, Any]) -> str | None:
    source = record.get("source") or {}
    classification = source.get("classification") or {}
    return classification.get("initialInspectionDate")


def reconcile_listing(layout: Any, listing: Mapping[str, Any]) -> dict[str, Any]:
    """Join the ledger with the listing by normalized unit and per-unit multiplicity (read-only).

    The task list shows neither project number nor RWID, so a unit with as many ledger cases as listing rows is matched
    and a unit with fewer rows has *candidates* only: each candidate still needs case-level proof (detail identity plus a
    dated source record) before it may be excluded. A listing row of a unit with no ledger case is a new source task.
    """

    cases = _ledger_cases(layout)
    by_unit: dict[str, list[str]] = defaultdict(list)
    for project, record in sorted(cases.items()):
        by_unit[normalize_unit(record.get("unitName"))].append(project)
    rows_by_unit: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for item in listing["taskTypes"]:
        for row in item["rows"]:
            rows_by_unit[normalize_unit(row["unit"])].append((item["type"], row))
    matched = 0
    candidates: list[dict[str, Any]] = []
    new_tasks: list[dict[str, Any]] = []
    for unit in sorted(set(by_unit) | set(rows_by_unit)):
        projects, rows = by_unit.get(unit, []), rows_by_unit.get(unit, [])
        paired = min(len(projects), len(rows))
        matched += paired
        if len(projects) > len(rows):
            candidates.append(
                {
                    "unit": unit,
                    "caseCount": len(projects),
                    "listingRows": len(rows),
                    "unpairedCases": len(projects) - len(rows),
                    "projects": [
                        {"projectNo": p, "registryDay": _registry_day(cases[p])} for p in projects
                    ],
                    "listingDays": sorted(row["inspectionDate"] for _, row in rows),
                    "needsCaseLevelProof": True,
                }
            )
        elif len(rows) > len(projects):
            for task_type, row in rows[len(projects):]:
                new_tasks.append({"taskType": task_type, **{k: row.get(k) for k in ("unit", "inspectionDate", "status", "org")}})
    total_rows = sum(len(item["rows"]) for item in listing["taskTypes"])
    return {
        "schemaVersion": "InitialDateScopeReconcileV1",
        "readOnly": True,
        "listingSha256": listing.get("_sha256"),
        "filters": listing["filters"],
        "counts": {
            "ledgerProductCases": len(cases),
            "listingRows": total_rows,
            "matched": matched,
            "ledgerCasesWithoutListingRow": sum(c["unpairedCases"] for c in candidates),
            "listingRowsWithoutLedgerCase": len(new_tasks),
        },
        "outOfScopeCandidates": candidates,
        "newSourceTasks": new_tasks,
    }


# --------------------------------------------------------------------------------------------- exclusion plan


def _resolve_evidence(plan_dir: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else plan_dir / path


def _check_item_evidence(item: Mapping[str, Any], plan_dir: Path, scope_from: date) -> list[dict[str, Any]]:
    project, rwid = item["projectNo"], item["rwid"]
    day = _day(item.get("initialInspectionDate"), f"{project} 初查日期")
    if day >= scope_from:
        raise ScopeError(f"{project} 的初查日期 {day} 不早于 {scope_from}，不能剔除")
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ScopeError(f"{project} 缺少证据")
    checked: list[dict[str, Any]] = []
    kinds: set[str] = set()
    for entry in evidence:
        kind, raw, recorded = entry.get("kind"), entry.get("path"), entry.get("sha256")
        if kind not in {"SOURCE_DETAIL_WITH_TASK_ROW", "PRINTED_INSPECTION_RECORD"} or not raw:
            raise ScopeError(f"{project} 证据类型或路径无效")
        path = _resolve_evidence(plan_dir, raw)
        if not path.is_file():
            raise ScopeError(f"{project} 证据文件不存在：{path}")
        actual = _sha256_file(path)
        if recorded != actual:
            raise ScopeError(f"{project} 证据摘要与登记不一致：{path.name}")
        if kind == "SOURCE_DETAIL_WITH_TASK_ROW":
            body = _read_json(path, "来源详情证据")
            text, url, row = str(body.get("text") or ""), str(body.get("url") or ""), body.get("listRow") or []
            if re.findall(r"RWID=(\d+)", url) != [rwid]:
                raise ScopeError(f"{project} 详情证据的 RWID 与登记不一致")
            if not re.search(rf"项目编号[:：]\s*{re.escape(project)}", text):
                raise ScopeError(f"{project} 详情证据的项目编号与登记不一致")
            if len(row) < 4 or normalize_unit(row[0]) != normalize_unit(item.get("unitName")):
                raise ScopeError(f"{project} 任务列表行的单位与登记不一致")
            if row[3] != item["initialInspectionDate"]:
                raise ScopeError(f"{project} 任务列表检查日期 {row[3]} 与剔除依据 {item['initialInspectionDate']} 不一致")
        kinds.add(kind)
        checked.append({"kind": kind, "path": str(path), "sha256": actual})
    if "SOURCE_DETAIL_WITH_TASK_ROW" not in kinds:
        raise ScopeError(f"{project} 缺少来源详情与任务列表行证据")
    return checked


def _tree_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _project_artifacts(layout: Any, project: str) -> list[Path]:
    """Every on-disk object that belongs only to this project (never batch captures, which are shared evidence)."""

    found: list[Path] = []
    for directory in (layout.completed_cases, layout.pending_cases, layout.raw_root / "手动交件"):
        candidate = directory / project
        if candidate.exists():
            found.append(candidate)
    for directory in (layout.history_workspaces, layout.verification_records):
        if directory.is_dir():
            found.extend(sorted(p for p in directory.iterdir() if p.name.startswith(project + "-")))
    work = layout.work_case_dir(project)
    if work.exists():
        found.append(work)
    return found


def _retention_root(layout: Any, plan_id: str) -> Path:
    return layout.root / RETENTION_DIR / plan_id


def _manifest_for(layout: Any, artifacts: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for artifact in artifacts:
        files = _tree_files(artifact) if artifact.is_dir() else [artifact]
        for file in files:
            entries.append(
                {
                    "relativePath": str(file.relative_to(layout.root)).replace("\\", "/"),
                    "bytes": file.stat().st_size,
                    "sha256": _sha256_file(file),
                }
            )
    return entries


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_plan(path: str | os.PathLike[str]) -> tuple[dict[str, Any], Path]:
    source = Path(path)
    plan = _read_json(source, "剔除计划")
    if not isinstance(plan, dict) or plan.get("schemaVersion") != PLAN_SCHEMA:
        raise ScopeError("剔除计划版本不支持")
    if not isinstance(plan.get("planId"), str) or PLAN_ID.fullmatch(plan["planId"]) is None:
        raise ScopeError("planId 不合法")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ScopeError("剔除计划没有对象")
    if len({i.get("projectNo") for i in items}) != len(items):
        raise ScopeError("剔除计划含重复项目")
    return plan, source.parent


def exclude_out_of_scope(
    layout: Any, plan_path: str | os.PathLike[str], *, apply: bool = False, scope_from: date = SCOPE_FROM
) -> dict[str, Any]:
    """Validate (and with ``apply`` execute) the removal of proven out-of-scope cases from the active set.

    Nothing is deleted: a case's files move, byte for byte, under ``范围外保留/<planId>/<项目>/`` with a SHA-256
    manifest taken before the move and re-checked after it; the full ledger record goes into the register; the case
    leaves the waterline. ``restore_excluded`` reverses it. Re-entry on a finished plan changes nothing.
    """

    plan, plan_dir = _load_plan(plan_path)
    plan_id = plan["planId"]
    retention = _retention_root(layout, plan_id)
    register_path = retention / REGISTER_NAME
    if _day(plan.get("scopeFromDate"), "scopeFromDate") != scope_from:
        raise ScopeError("剔除计划的起始日与当前范围规则不一致")
    with ws._waterline_lock(layout):
        ledger = ws.load_waterline(layout)
        register = _read_json(register_path, "排除登记") if register_path.is_file() else None
        report: list[dict[str, Any]] = []
        todo: list[tuple[dict[str, Any], dict[str, Any], list[Path], list[dict[str, Any]], list[dict[str, Any]]]] = []
        for item in plan["items"]:
            project = item.get("projectNo")
            if not isinstance(project, str) or PRODUCT_CASE.fullmatch(project) is None:
                raise ScopeError("项目编号格式不正确")
            done = register and next((e for e in register["entries"] if e["projectNo"] == project), None)
            if project not in ledger["cases"]:
                if done and done["status"] == "EXCLUDED":
                    report.append({"projectNo": project, "status": "ALREADY_EXCLUDED"})
                    continue
                raise ScopeError(f"{project} 不在总账，也没有本计划的排除登记")
            record = ledger["cases"][project]
            source = record.get("source") or {}
            ledger_rwid = str(source.get("rwid") or "")
            # 手动交件的案卷总账可能没有 RWID：此时身份由详情证据(项目编号<->RWID 同页)证明；总账有 RWID 则必须相等
            if ledger_rwid and ledger_rwid != str(item.get("rwid") or ""):
                raise ScopeError(f"{project} 的 RWID 与总账不一致")
            if normalize_unit(record.get("unitName")) != normalize_unit(item.get("unitName")):
                raise ScopeError(f"{project} 的单位名称与总账不一致")
            if (record.get("amendment") or {}).get("status") == "OPEN":
                raise ScopeError(f"{project} 有未完成的已归档修订，先处理")
            if record.get("materialCollection"):
                raise ScopeError(f"{project} 有进行中的材料采集断点，先处理")
            evidence = _check_item_evidence(item, plan_dir, scope_from)
            artifacts = _project_artifacts(layout, project)
            for artifact in artifacts:
                ws._validated_workspace_path(artifact, layout.root, "范围外对象", must_exist=True)
                if artifact.is_dir():
                    ws._assert_safe_tree(artifact, layout.root, "范围外对象")
            todo.append((item, record, artifacts, evidence, _manifest_for(layout, artifacts)))
            report.append({"projectNo": project, "status": "WOULD_EXCLUDE" if not apply else "EXCLUDING",
                           "artifacts": [str(a.relative_to(layout.root)) for a in artifacts],
                           "files": len(todo[-1][4])})
        if not apply or not todo:
            return {"schemaVersion": "InitialDateScopeExclusionResultV1", "applied": False, "planId": plan_id, "items": report}

        if register is None:
            register = {"schemaVersion": REGISTER_SCHEMA, "planId": plan_id, "scopeFromDate": scope_from.isoformat(),
                        "createdAt": _now(), "authorization": plan.get("authorization"), "entries": []}
        moved: list[tuple[Path, Path]] = []
        previous_register = register_path.read_bytes() if register_path.is_file() else None
        register_written = False
        try:
            for item, record, artifacts, evidence, manifest in todo:
                project = item["projectNo"]
                target_root = retention / project
                target_root.mkdir(parents=True, exist_ok=True)
                entry = {
                    "projectNo": project, "rwid": item["rwid"], "unitName": item["unitName"],
                    "initialInspectionDate": item["initialInspectionDate"], "reason": item.get("reason"),
                    "excludedAt": _now(), "status": "EXCLUDED", "evidence": evidence,
                    "ledgerRecord": record, "manifest": manifest,
                    "artifacts": [str(a.relative_to(layout.root)).replace("\\", "/") for a in artifacts],
                }
                for artifact in artifacts:
                    destination = target_root / "files" / artifact.relative_to(layout.root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        raise ScopeError(f"保留目标已存在，拒绝覆盖：{destination}")
                    os.rename(artifact, destination)
                    moved.append((artifact, destination))
                kept = _manifest_for_retained(layout, target_root, artifacts)
                if [(m["relativePath"], m["bytes"], m["sha256"]) for m in manifest] != kept:
                    raise ScopeError(f"{project} 移动后的字节清单与移动前不一致")
                register["entries"] = [e for e in register["entries"] if e["projectNo"] != project] + [entry]
            _atomic_json(register_path, register)
            register_written = True
            for item, *_ in todo:
                del ledger["cases"][item["projectNo"]]
            ws._save_waterline_unlocked(layout, ledger)
        except BaseException:
            for original, destination in reversed(moved):
                if destination.exists() and not original.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(destination, original)
            if register_written:
                if previous_register is None:
                    register_path.unlink(missing_ok=True)
                else:
                    register_path.write_bytes(previous_register)
            raise
    try:
        xlsx = str(ws.export_waterline_xlsx(layout))
    except ws.WorkspaceStateError as error:  # the JSON is the fact source; the xlsx is a derived view
        xlsx = f"EXPORT_FAILED: {error}"
    return {"schemaVersion": "InitialDateScopeExclusionResultV1", "applied": True, "planId": plan_id,
            "registerPath": str(register_path), "xlsx": xlsx, "items": report}


def _manifest_for_retained(layout: Any, target_root: Path, artifacts: list[Path]) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for artifact in artifacts:
        base = target_root / "files" / artifact.relative_to(layout.root)
        files = _tree_files(base) if base.is_dir() else [base]
        for file in files:
            relative = str(artifact.relative_to(layout.root) / file.relative_to(base)) if base.is_dir() else str(artifact.relative_to(layout.root))
            out.append((relative.replace("\\", "/"), file.stat().st_size, _sha256_file(file)))
    return out


def restore_excluded(layout: Any, plan_id: str, project: str) -> dict[str, Any]:
    """Put one excluded case back exactly as it was: files, then its ledger record."""

    retention = _retention_root(layout, plan_id)
    register_path = retention / REGISTER_NAME
    if not register_path.is_file():
        raise ScopeError("没有该计划的排除登记")
    with ws._waterline_lock(layout):
        register = _read_json(register_path, "排除登记")
        entry = next((e for e in register["entries"] if e["projectNo"] == project and e["status"] == "EXCLUDED"), None)
        if entry is None:
            raise ScopeError(f"{project} 没有处于排除状态的登记")
        ledger = ws.load_waterline(layout)
        if project in ledger["cases"]:
            raise ScopeError(f"{project} 已在总账，拒绝覆盖")
        target_root = retention / project
        pairs: list[tuple[Path, Path]] = []
        for relative in entry["artifacts"]:
            kept, original = target_root / "files" / relative, layout.root / relative
            if not kept.exists():
                raise ScopeError(f"保留副本缺失：{relative}")
            if original.exists():
                raise ScopeError(f"原位置已有对象，拒绝覆盖：{relative}")
            pairs.append((kept, original))
        for kept, original in pairs:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.rename(kept, original)
        artifacts = [original for _, original in pairs]
        restored = [(m["relativePath"], m["bytes"], m["sha256"]) for m in _manifest_for(layout, artifacts)]
        if restored != [(m["relativePath"], m["bytes"], m["sha256"]) for m in entry["manifest"]]:
            for kept, original in pairs:
                os.rename(original, kept)
            raise ScopeError("恢复后的字节清单与排除前不一致，已撤回")
        ledger["cases"][project] = entry["ledgerRecord"]
        ws._save_waterline_unlocked(layout, ledger)
        entry["status"] = "RESTORED"
        entry["restoredAt"] = _now()
        _atomic_json(register_path, register)
    try:
        xlsx = str(ws.export_waterline_xlsx(layout))
    except ws.WorkspaceStateError as error:
        xlsx = f"EXPORT_FAILED: {error}"
    return {"schemaVersion": "InitialDateScopeRestoreResultV1", "planId": plan_id, "projectNo": project, "xlsx": xlsx, "files": len(restored)}


def excluded_projects(layout: Any) -> set[str]:
    """Projects currently excluded by any plan (read-only), so builders never recreate them."""

    return excluded_projects_in_root(layout.root)


def excluded_projects_in_root(business_root: str | os.PathLike[str]) -> set[str]:
    root = Path(business_root) / RETENTION_DIR
    found: set[str] = set()
    if not root.is_dir():
        return found
    for register_path in root.glob(f"*/{REGISTER_NAME}"):
        try:
            register = json.loads(register_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found |= {e["projectNo"] for e in register.get("entries", []) if e.get("status") == "EXCLUDED"}
    return found
