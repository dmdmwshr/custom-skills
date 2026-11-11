"""Remove proven out-of-scope cases (and only their own child rows) from the Feishu case Base.

The initial-inspection-date rule (see ``initial_scope``) leaves some cloud rows that no longer belong to the active set.
This is the one place that deletes cloud records, and it does it the same careful way the other cloud writers work:

* the plan names the exact case rows and the exact child rows, and pins a full-field snapshot taken beforehand
  (the recovery source: every deleted cell value and relation is in it);
* a fresh full read must show exactly those children, no more and no less, bound by their relation fields, before any
  call is made;
* an append-only intent is written per target into the one formal ledger before the request, and a final status after the
  read-back; a result that cannot be proven stays BLOCKED and is never replayed;
* children go first (product, then inspection), the case row last, so no child is ever left without its parent.

It never creates or edits a record, never touches a field, and never deletes anything the plan does not list.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

if __package__:
    from . import feishu_source_sync as sync
else:
    import feishu_source_sync as sync

PLAN_SCHEMA = "FeishuScopeRetirePlanV1"
ORDER = ("product", "inspect", "main")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise sync.SyncError(f"{label}无法读取：{path}") from error


def _ids(cell: Any) -> list[str]:
    """Record ids of a link cell as read by ``+record-list --format json`` (list of {"id": ...})."""

    if not isinstance(cell, list):
        return []
    return [item["id"] for item in cell if isinstance(item, dict) and isinstance(item.get("id"), str)]


def _text(cell: Any) -> str:
    return cell if isinstance(cell, str) else ""


def load_plan(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _load(plan_path, "云端剔除计划")
    if not isinstance(plan, dict) or plan.get("schemaVersion") != PLAN_SCHEMA:
        raise sync.SyncError("云端剔除计划版本不支持")
    base = plan.get("target", {}).get("baseToken")
    tables = plan.get("tables")
    if not base or not isinstance(tables, dict) or set(tables) != set(ORDER):
        raise sync.SyncError("计划必须给出 baseToken 和 main/inspect/product 三张表")
    snapshot = plan.get("snapshot") or {}
    snapshot_path = Path(snapshot.get("path", ""))
    if not snapshot_path.is_file() or "sha256:" + _sha(snapshot_path) != snapshot.get("sha256"):
        raise sync.SyncError("全字段快照不存在或摘要与登记不一致")
    snap = _load(snapshot_path, "全字段快照")
    if snap.get("baseToken") != base:
        raise sync.SyncError("快照的 Base 与计划不一致")
    for key in ORDER:
        if snap.get("tables", {}).get(key) != tables[key].get("tableId"):
            raise sync.SyncError(f"快照的 {key} 表与计划不一致")
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases or len({c.get("projectNo") for c in cases}) != len(cases):
        raise sync.SyncError("计划没有对象或含重复项目")
    return plan, snap


def _expected(plan: dict[str, Any], snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """projectNo -> exact main/inspect/product record ids from the pinned snapshot."""

    by_project = {c["projectNo"]: c for c in snap["cases"]}
    result: dict[str, dict[str, Any]] = {}
    for case in plan["cases"]:
        project = case["projectNo"]
        entry = by_project.get(project)
        if entry is None:
            raise sync.SyncError(f"{project} 不在快照里")
        result[project] = {
            "main": entry["main"]["record_id"],
            "inspect": [r["record_id"] for r in entry["inspect"]],
            "product": [r["record_id"] for r in entry["product"]],
        }
    return result


def _fresh_state(plan: dict[str, Any], transport: Any) -> dict[str, dict[str, dict[str, Any]]]:
    base, tables = plan["target"]["baseToken"], plan["tables"]
    main, inspect, product = tables["main"], tables["inspect"], tables["product"]
    return {
        "main": transport.list_rows(base, main["tableId"], [main["keyFieldId"], main["childLinkFieldId"]]),
        "inspect": transport.list_rows(base, inspect["tableId"], [inspect["keyFieldId"], inspect["parentLinkFieldId"], inspect["childLinkFieldId"]]),
        "product": transport.list_rows(base, product["tableId"], [product["keyFieldId"], product["parentLinkFieldId"]]),
    }


def verify_live(plan: dict[str, Any], expected: dict[str, dict[str, Any]], live: dict[str, dict[str, dict[str, Any]]]) -> None:
    """The live children of each case row must be exactly the planned ones (bound by relation fields), or nothing happens."""

    tables = plan["tables"]
    for project, want in expected.items():
        main_row = live["main"].get(want["main"])
        if main_row is None:
            raise sync.SyncError(f"{project}：案卷行已不在云端（{want['main']}），按计划对象不符停止")
        if _text(main_row.get(tables["main"]["keyFieldId"])) != project:
            raise sync.SyncError(f"{project}：案卷行的稳定键与计划不一致")
        children = sorted(rid for rid, row in live["inspect"].items() if want["main"] in _ids(row.get(tables["inspect"]["parentLinkFieldId"])))
        if children != sorted(want["inspect"]):
            raise sync.SyncError(f"{project}：云端检查行 {children} 与计划 {sorted(want['inspect'])} 不一致")
        for rid in children:
            row = live["inspect"][rid]
            if not _text(row.get(tables["inspect"]["keyFieldId"])).startswith(project + "|"):
                raise sync.SyncError(f"{project}：检查行 {rid} 的检查键不属于该案")
        products = sorted(rid for rid, row in live["product"].items() if set(_ids(row.get(tables["product"]["parentLinkFieldId"]))) & set(want["inspect"]))
        if products != sorted(want["product"]):
            raise sync.SyncError(f"{project}：云端产品行 {products} 与计划 {sorted(want['product'])} 不一致")
        for rid in products:
            if not _text(live["product"][rid].get(tables["product"]["keyFieldId"])).startswith(project + "|"):
                raise sync.SyncError(f"{project}：产品行 {rid} 的产品键不属于该案")
    # 同一批里没有行被两个案卷共用：被共用的子行一删会影响别的案
    for key in ("inspect", "product"):
        all_ids = [rid for want in expected.values() for rid in (want[key] if isinstance(want[key], list) else [want[key]])]
        if len(set(all_ids)) != len(all_ids):
            raise sync.SyncError(f"计划里 {key} 行重复")


def retire(
    plan_path: Path,
    *,
    execute_flag: bool = False,
    cli: str | None = None,
    ledger_dir: Path | None = None,
    log_dir: Path | None = None,
    transport: Any = None,
    ledger: Any = None,
) -> dict[str, Any]:
    plan, snap = load_plan(plan_path)
    expected = _expected(plan, snap)
    target = plan["target"]
    transport = transport or sync.LarkCliTransport(cli or sync.DEFAULT_CLI, target["profile"], target["identity"])
    live = _fresh_state(plan, transport)
    verify_live(plan, expected, live)
    targets = {
        "product": sorted(r for want in expected.values() for r in want["product"]),
        "inspect": sorted(r for want in expected.values() for r in want["inspect"]),
        "main": sorted(want["main"] for want in expected.values()),
    }
    counts_before = {k: len(v) for k, v in live.items()}
    report: dict[str, Any] = {
        "schemaVersion": "FeishuScopeRetireReportV1", "planId": plan["planId"], "mode": "execute" if execute_flag else "plan",
        "counts": {"cases": len(expected), **{k: len(v) for k, v in targets.items()}}, "liveBefore": counts_before,
        "externalWrites": 0, "targets": targets,
    }
    if not execute_flag:
        report["status"] = "PLAN_ONLY"
        return report
    if ledger is None:
        if ledger_dir is None:
            raise sync.SyncError("execute 必须显式给出 --ledger-dir（唯一正式 P18 账）")
        ledger = sync.Ledger(Path(ledger_dir))
    ledger.validate_root()
    base = target["baseToken"]
    keys = {(t, rid): sync.Ledger.target_key(base, plan["tables"][t]["tableId"], rid) for t in ORDER for rid in targets[t]}
    blocked = {f"{t}:{rid}": ledger.state(k) for (t, rid), k in keys.items() if ledger.state(k)["state"] != "CLEAR"}
    if blocked:
        raise sync.SyncError("账本里有未决目标，按目标封锁，先对账：" + json.dumps(blocked, ensure_ascii=False)[:600])
    run_id = uuid.uuid4().hex[:12]
    log = Path(log_dir) if log_dir else None
    if log:
        log.mkdir(parents=True, exist_ok=True)
        (log / "before.json").write_text(json.dumps({"live": {k: sorted(v) for k, v in live.items()}, "expected": expected}, ensure_ascii=False, indent=1), encoding="utf-8")
    results: list[dict[str, Any]] = []
    with ledger.locks(list(keys.values()), run_id):
        for table in ORDER:
            ids = targets[table]
            if not ids:
                continue
            intents = {}
            for rid in ids:
                intent_id = f"{run_id}:{table}:{rid}"
                intents[rid] = intent_id
                ledger.append(keys[(table, rid)], {"event": "intent", "intentId": intent_id, "runId": run_id, "op": "delete-record", "table": plan["tables"][table]["tableId"],
                                                   "recordId": rid, "planId": plan["planId"], "snapshotSha256": plan["snapshot"]["sha256"], "operator": sync.operator()})
            try:
                receipt = transport.delete_records(base, plan["tables"][table]["tableId"], ids)
                outcome_error = None
            except sync.RuntimeSyncError as error:
                receipt, outcome_error = None, str(error)
            if log:
                with (log / "requests.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"table": table, "ids": ids, "receipt": receipt, "error": outcome_error}, ensure_ascii=False) + "\n")
            try:
                after_rows = transport.list_rows(base, plan["tables"][table]["tableId"], [plan["tables"][table]["keyFieldId"]])
                gone = [rid for rid in ids if rid not in after_rows]
            except sync.RuntimeSyncError as error:
                after_rows, gone, outcome_error = {}, [], (outcome_error or "") + f" | 读回失败：{error}"
            for rid in ids:
                ok = rid in gone
                status = "OK" if ok else "UNKNOWN"   # 读回仍在/读不到：不能证明未执行，封锁而不重放
                ledger.append(keys[(table, rid)], {"event": "final", "intentId": intents[rid], "status": status, "detail": "" if ok else (outcome_error or "读回仍存在")})
                results.append({"table": table, "recordId": rid, "status": status})
            if len(gone) != len(ids):
                report.update({"status": "FAILED_OR_UNKNOWN", "results": results, "externalWrites": len(results)})
                if log:
                    (log / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
                raise sync.RuntimeSyncError("UNKNOWN: 部分记录读回仍存在或读回失败，已落账封锁，未重试：" + json.dumps(results, ensure_ascii=False)[:600])
    after = _fresh_state(plan, transport)
    for table in ORDER:
        if any(rid in after[table] for rid in targets[table]):
            raise sync.RuntimeSyncError("UNKNOWN: 终读发现目标仍在，已落账")
        if len(after[table]) != counts_before[table] - len(targets[table]):
            raise sync.RuntimeSyncError(f"UNKNOWN: {table} 终读行数 {len(after[table])} 不等于 {counts_before[table]} - {len(targets[table])}")
    report.update({"status": "OK", "results": results, "externalWrites": len(results), "liveAfter": {k: len(v) for k, v in after.items()}})
    if log:
        (log / "after.json").write_text(json.dumps({k: sorted(v) for k, v in after.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
        (log / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
