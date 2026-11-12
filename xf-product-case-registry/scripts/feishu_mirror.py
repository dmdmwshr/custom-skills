"""Feishu -> local mirror of brigade-maintained fields, and the independent statistics recount (2026-11-12 mechanism).

Brigade-maintained fields (the yes/no labels, the enterprise record) have Feishu as their authority: every
save takes effect at once. This module copies them back to the business root so the local side always
has the current value, a daily snapshot and a list of what changed and who changed it. It never writes
to Feishu.

Three values are compared per cell:

- last synced   the value in the local mirror from the previous run;
- Feishu now    the value read in this run;
- assistant     the value this tool last wrote and saw confirmed (the write ledger).

A cell whose Feishu value differs from the mirror is a change. It is the assistant's own when Feishu
now equals the ledger value, otherwise somebody saved it in Feishu. Supervision facts go the other way
(local -> Feishu): a fact whose Feishu value no longer equals what the assistant last wrote is reported
as drift and left alone.

``recount`` recomputes the monthly figures from the detail tables and compares them with the formula
columns, so a broken formula or a missed row shows up the same night.

Layout under ``<业务根>/运行记录/飞书镜像``::

    当前镜像/<库>-<表>.json      current mirror (stable key -> field -> value)
    每日快照/<日期>/<库>-<表>.json one snapshot per Asia/Shanghai date (first read of the day is kept as -首次)
    变更记录/<日期>.jsonl         appended changes

Standard library only.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__:
    from . import feishu_plans as plans
    from . import feishu_source_sync as sync
    from . import field_policy
else:
    import feishu_plans as plans
    import feishu_source_sync as sync
    import field_policy

MIRROR_DIR = "飞书镜像"
MIRROR_SCHEMA = "FeishuMirrorReportV1"
RECOUNT_SCHEMA = "FeishuStatRecountReportV1"
BRIGADE_CLASSES = frozenset({field_policy.CLASS_LABEL, field_policy.CLASS_BRIGADE, field_policy.CLASS_HUMAN})
MAIN = "案卷库"
SECTION_NAMES = {None: MAIN, "maintenanceBase": "维修库"}


def _atomic(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    text = json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    return sync.digest(text)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _value(field_type: str, cell: Any) -> Any:
    """A cell as the plain comparable value kept in the mirror."""
    if field_type in sync.SCALAR_TYPES:
        return plans._plain(field_type, cell)
    return None if sync.cell_is_empty(cell) else cell


def mirror_table(*, base: str, table_name: str, table_id: str, fields: dict[str, dict[str, Any]], facts: dict[str, dict[str, Any]],
                 transport: Any, ledger: sync.Ledger | None, root: Path, label: str, day: str, observed_at: str) -> dict[str, Any]:
    """Mirror one table; returns its part of the report. Reads Feishu once; writes only under ``root``."""
    key_field = sync.STABLE_KEYS[table_name]
    rows = transport.list_rows(base, table_id, [key_field, *fields, *facts])
    current: dict[str, dict[str, Any]] = {}
    record_of: dict[str, str] = {}
    problems: list[dict[str, Any]] = []
    for record_id, row in sorted(rows.items()):
        key = row.get(key_field)
        if not isinstance(key, str) or not key:
            problems.append({"recordId": record_id, "problem": "没有稳定键，不入镜像"})
            continue
        if key in current:
            problems.append({"recordId": record_id, "stableKey": key, "problem": "稳定键重复，只保留先读到的一条"})
            continue
        current[key] = {meta["name"]: _value(meta["type"], row.get(fid)) for fid, meta in fields.items()}
        record_of[key] = record_id
    name = f"{label}-{table_name}"
    mirror_path = root / "当前镜像" / f"{name}.json"
    previous = _load(mirror_path)
    changes: list[dict[str, Any]] = []
    if previous is not None:
        old_rows = previous["rows"]
        for key in sorted(set(old_rows) | set(current)):
            if key not in current:
                changes.append({"table": name, "stableKey": key, "change": "record-removed", "previous": old_rows[key]})
                continue
            if key not in old_rows:
                changes.append({"table": name, "stableKey": key, "change": "record-added", "current": current[key]})
                continue
            for fid, meta in fields.items():
                before, after = old_rows[key].get(meta["name"]), current[key][meta["name"]]
                if before == after:
                    continue
                by = "feishu-user"
                if ledger is not None:
                    wrote = plans.last_assistant_value(ledger, sync.Ledger.target_key(base, table_id, record_of[key]), fid)
                    if wrote is not plans._NEVER and _value(meta["type"], wrote) == after:
                        by = "assistant"
                changes.append({"table": name, "stableKey": key, "change": "value", "field": meta["name"], "previous": before, "current": after, "by": by})
    drift: list[dict[str, Any]] = []
    if ledger is not None and facts:
        for key, record_id in sorted(record_of.items()):
            target = sync.Ledger.target_key(base, table_id, record_id)
            if not ledger.events(target):
                continue
            for fid, meta in facts.items():
                wrote = plans.last_assistant_value(ledger, target, fid)
                if wrote is plans._NEVER:
                    continue
                now_value = _value(meta["type"], rows[record_id].get(fid))
                if now_value != _value(meta["type"], wrote):
                    drift.append({"table": name, "stableKey": key, "field": meta["name"], "assistantLastWrote": wrote, "feishuNow": now_value})
    snapshot = {"table": name, "tableId": table_id, "readAt": observed_at, "fields": [m["name"] for m in fields.values()], "rows": current}
    day_dir = root / "每日快照" / day
    first = day_dir / f"{name}-首次.json"
    if not first.is_file():
        _atomic(first, snapshot)
    snapshot_sha = _atomic(day_dir / f"{name}.json", snapshot)
    _atomic(mirror_path, snapshot)
    if changes:
        log = root / "变更记录" / f"{day}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            for change in changes:
                stream.write(json.dumps({"at": observed_at, **change}, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return {"table": name, "rows": len(current), "fields": len(fields), "firstMirror": previous is None, "changes": len(changes),
            "changesByUser": sum(1 for c in changes if c.get("by") == "feishu-user"), "changeSample": changes[:20],
            "factDrift": drift, "problems": problems, "snapshotSha256": snapshot_sha}


def mirror(*, contract_path: Path, contract_sha: str, sections: list[str | None], targets: dict[str | None, dict[str, str]], transport: Any,
           business_root: Path, ledger_dir: Path | None, day: str, isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Mirror every table that has a stable key and brigade-maintained fields, in each listed contract section."""
    ledger = None
    if ledger_dir is not None:
        ledger = sync.Ledger(ledger_dir, isolated_test=isolated_test_ledger)
        ledger.validate_root()
    root = Path(business_root) / "运行记录" / MIRROR_DIR
    observed_at = sync.now()
    tables: list[dict[str, Any]] = []
    for section in sections:
        contract = sync.load_contract(contract_path, contract_sha, section)
        target = targets[section]
        sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
        index = sync.field_index(contract)
        for table_name, table in contract["tables"].items():
            if table_name not in sync.STABLE_KEYS:
                continue
            table_id = table["tableId"]
            fields = {fid: meta for fid, meta in index[table_id].items() if meta["class"] in BRIGADE_CLASSES and meta["type"] not in sync.DERIVED_TYPES}
            facts = {fid: meta for fid, meta in index[table_id].items()
                     if meta["class"] == sync.WHITELIST and meta["type"] in sync.SCALAR_TYPES and fid != sync.STABLE_KEYS[table_name]}
            if not fields:
                continue
            tables.append(mirror_table(base=target["baseToken"], table_name=table_name, table_id=table_id, fields=fields, facts=facts,
                                       transport=transport, ledger=ledger, root=root, label=SECTION_NAMES.get(section, section or MAIN),
                                       day=day, observed_at=observed_at))
    quiet = not any(t["changes"] or t["factDrift"] or t["problems"] for t in tables)
    return {"schema": MIRROR_SCHEMA, "day": day, "readAt": observed_at, "externalCalls": "read-only", "mirrorRoot": str(root),
            "tables": tables, "quiet": quiet, "status": "OK" if not any(t["factDrift"] or t["problems"] for t in tables) else "ATTENTION"}


# ---------------------------------------------------------------------------------------------
# Independent recount of the monthly statistics
# ---------------------------------------------------------------------------------------------

UNKNOWN_BRIGADE = "待核实"
RECOUNT_COLUMNS = ("R02_初查次数", "R02_复查次数", "R02_初查合格", "R02_初查不合格", "R02_初查案卷数", "R02_初查产品项数",
                   "R02_初查产品数量缺项数", "R02_初查产品数量缺数值项数", "R02_初查现场检查数量合计", "R02_产品归属缺项案卷数")
NO_UNIT = "单位未载明"


def _one(cell: Any) -> Any:
    if isinstance(cell, list):
        return None if not cell else (cell[0] if len(cell) == 1 else "|".join(str(c) for c in cell))
    return cell


def _by_name(index: dict[str, dict[str, Any]], names: list[str], table: str) -> dict[str, str]:
    found = {meta["name"]: fid for fid, meta in index.items()}
    missing = [n for n in names if n not in found]
    sync._require(not missing, f"合同里 {table} 缺少字段：{'、'.join(missing)}")
    return {n: found[n] for n in names}


def recount(*, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any) -> dict[str, Any]:
    """Recompute the monthly figures from 检查明细/产品明细/案卷主表 and compare with the 月度工作统计 formula columns."""
    contract = sync.load_contract(contract_path, contract_sha)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    index = sync.field_index(contract)
    monthly = next((name for name in ("月度工作统计", "月度") if name in contract["tables"]), None)   # 合同里的表名历来写“月度”
    sync._require(monthly is not None, "合同里没有月度统计表")
    ids = {name: contract["tables"][name]["tableId"] for name in ("案卷主表", "检查明细", "产品明细")}
    ids["月度工作统计"] = contract["tables"][monthly]["tableId"]
    base = target["baseToken"]
    cf = _by_name(index[ids["检查明细"]], ["检查键", "通报月份", "所属大队(案卷)", "阶段", "检查结果"], "检查明细")
    pf = _by_name(index[ids["产品明细"]], ["产品键", "数量数值", "数量单位", "数量含义", "是否维修后", "本产品涉及网售"], "产品明细")
    mf = _by_name(index[ids["案卷主表"]], ["项目编号", "是否网售", "是否维修后灭火器"], "案卷主表")
    month_index = {meta["name"]: fid for fid, meta in index[ids["月度工作统计"]].items()}
    sync._require("月份" in month_index and "大队" in month_index, "合同里 月度工作统计 缺少 月份/大队")
    columns = [c for c in RECOUNT_COLUMNS if c in month_index]
    checks = transport.list_rows(base, ids["检查明细"], list(cf.values()))
    products = transport.list_rows(base, ids["产品明细"], list(pf.values()))
    cases = transport.list_rows(base, ids["案卷主表"], list(mf.values()))
    months = transport.list_rows(base, ids["月度工作统计"], [month_index["月份"], month_index["大队"], *[month_index[c] for c in columns]])

    info: dict[str, tuple[Any, Any, Any]] = {}
    counts: dict[str, Counter] = {c: Counter() for c in RECOUNT_COLUMNS}
    case_sets: dict[tuple[Any, Any], set[str]] = defaultdict(set)
    for row in checks.values():
        key = row.get(cf["检查键"])
        if not isinstance(key, str) or not key:
            continue
        month, brigade, stage = _one(row.get(cf["通报月份"])), _one(row.get(cf["所属大队(案卷)"])) or UNKNOWN_BRIGADE, _one(row.get(cf["阶段"]))
        info[key] = (month, brigade, stage)
        bucket, result = (month, brigade), _one(row.get(cf["检查结果"]))
        if stage == "初查":
            counts["R02_初查次数"][bucket] += 1
            case_sets[bucket].add(key[:18])
            if result in ("合格", "不合格"):
                counts["R02_初查" + result][bucket] += 1
        elif stage == "复查":
            counts["R02_复查次数"][bucket] += 1
    quantity: dict[str, Counter] = defaultdict(Counter)
    located: dict[str, set[str]] = {"网售": set(), "维修后": set()}
    for row in products.values():
        product_key = row.get(pf["产品键"])
        if not isinstance(product_key, str) or "|" not in product_key:
            continue
        check_key = product_key.rsplit("|", 1)[0]
        if _one(row.get(pf["本产品涉及网售"])) == "是":
            located["网售"].add(check_key[:18])
        if _one(row.get(pf["是否维修后"])) == "是":
            located["维修后"].add(check_key[:18])
        if check_key not in info or info[check_key][2] != "初查":
            continue
        bucket = info[check_key][:2]
        counts["R02_初查产品项数"][bucket] += 1
        number, unit, meaning = row.get(pf["数量数值"]), _one(row.get(pf["数量单位"])), _one(row.get(pf["数量含义"]))
        if number is None or not unit or not meaning:
            counts["R02_初查产品数量缺项数"][bucket] += 1   # a missing quantity is a gap, never counted as 1
        if number is None:
            counts["R02_初查产品数量缺数值项数"][bucket] += 1
        elif meaning:
            quantity[f"{bucket[0]}|{bucket[1]}"][f"{meaning}/{unit or NO_UNIT}"] += number   # 原文没写单位的单列，不并入任何单位
            if meaning == "现场检查数量":
                counts["R02_初查现场检查数量合计"][bucket] += number
    unlocated: set[str] = set()
    for row in cases.values():
        project = row.get(mf["项目编号"])
        if not isinstance(project, str):
            continue
        if (_one(row.get(mf["是否网售"])) == "是" and project not in located["网售"]) or \
           (_one(row.get(mf["是否维修后灭火器"])) == "是" and project not in located["维修后"]):
            unlocated.add(project)
    for bucket, projects in case_sets.items():
        counts["R02_初查案卷数"][bucket] = len(projects)
        counts["R02_产品归属缺项案卷数"][bucket] = len(projects & unlocated)

    diffs: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for record_id, row in sorted(months.items()):
        bucket = (row.get(month_index["月份"]), _one(row.get(month_index["大队"])))
        seen.add(bucket)
        for column in columns:
            cloud = row.get(month_index[column])
            try:
                cloud_number = float(cloud or 0)
            except (TypeError, ValueError):
                diffs.append({"month": bucket[0], "brigade": bucket[1], "column": column, "feishu": cloud, "problem": "不是数字"})
                continue
            if cloud_number != float(counts[column].get(bucket, 0)):
                diffs.append({"month": bucket[0], "brigade": bucket[1], "column": column, "feishu": cloud_number, "independent": counts[column].get(bucket, 0)})
    uncovered = sorted(f"{m}|{b}" for (m, b) in set(case_sets) - seen)
    totals = {column: sum(counts[column].values()) for column in RECOUNT_COLUMNS}
    return {"schema": RECOUNT_SCHEMA, "readAt": sync.now(), "externalCalls": "read-only",
            "rows": {"案卷主表": len(cases), "检查明细": len(checks), "产品明细": len(products), "月度工作统计": len(months)},
            "comparedColumns": columns, "notInFeishu": [c for c in RECOUNT_COLUMNS if c not in month_index], "totals": totals,
            "quantityByUnit": {k: dict(v) for k, v in sorted(quantity.items())}, "productAttributionGaps": sorted(unlocated),
            "bucketsWithoutMonthlyRow": uncovered, "diffs": diffs, "status": "OK" if not diffs else "DIFF"}
