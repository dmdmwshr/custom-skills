"""Independent recount of the fifteen monthly/annual indicators (2026-11-17, a31).

The Feishu 月度工作统计 rows hold the fifteen indicators as formulas that read the case, inspection and product tables with
each row's own start day, end day and brigade. This module recomputes the same figures from the raw cells with ordinary
code, row by row, so a broken formula, a mis-attributed case or a missed row shows up the same night. It also builds the
located gap lists (missing date, missing brigade, result not yet out, inspection method missing, sampling batch missing)
and checks the relations the plan states: item 4 = item 5 + item 6 + method-missing cases; a year row equals its twelve
month rows; the city row equals its nine brigade rows.

Pure functions plus one reader; standard library only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

if __package__:
    from . import feishu_period as period
else:  # script-style import (scripts/ on sys.path)
    import feishu_period as period

SAMPLE, SITE, MIXED = "抽样送检", "现场判定", "抽样送检、现场判定"
CASE_FIELDS = ("项目编号", "单位名称", "初查日期", "初查结果", "所属大队（受控）", "是否微型消防站", "是否消防快速处置队", "是否刑事案件",
               "是否光伏锂电等相关企业", "是否消防处置力量配备", "是否网售", "是否维修后灭火器", "是否加油加气站", "是否行政处罚案")
CHECK_FIELDS = ("检查键", "阶段", "检查日期", "检查方式", "所属大队(案卷)", "检查序次")
PRODUCT_FIELDS = ("产品键", "检查方式", "检查结果", "对应初查产品键")
ROSTER_FIELDS = ("人员ID", "大队", "有效起日", "有效止日")
BRIGADE_LIST_FIELDS = ("大队（选项）", "人员名册状态")
ADDITIVE = tuple(n for n in (*period.INDICATORS, *period.GAPS) if n not in ("大队执法监督员人数", "缺所属大队案卷数"))


def _one(cell: Any) -> Any:
    return period.one(cell)


def _day(cell: Any) -> str | None:
    return period.day_of(cell)


def _case_no(key: str) -> str:
    """The project number a check or product key starts with (``<项目编号>|初查``, ``<项目编号>|复查2|1``): everything before the first ``|``."""
    return key.split("|", 1)[0]


def _number(cell: Any) -> float | None:
    value = _one(cell)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_cases(case_rows: dict[str, dict[str, Any]], check_rows: dict[str, dict[str, Any]], product_rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per case: date, brigade, labels, method class and sampling batches, administrative class — from the raw cells."""
    cases: dict[str, dict[str, Any]] = {}
    for row in case_rows.values():
        key = _one(row.get("项目编号"))
        if not isinstance(key, str) or not key:
            continue
        cases[key] = {"unit": _one(row.get("单位名称")), "day": _day(row.get("初查日期")), "brigade": _one(row.get("所属大队（受控）")) or None, "result": _one(row.get("初查结果")),
                      "micro": _one(row.get("是否微型消防站")), "quick": _one(row.get("是否消防快速处置队")), "criminal": _one(row.get("是否刑事案件")),
                      "corp": _one(row.get("是否光伏锂电等相关企业")), "force": _one(row.get("是否消防处置力量配备")), "net": _one(row.get("是否网售")),
                      "fix": _one(row.get("是否维修后灭火器")), "gas": _one(row.get("是否加油加气站")), "cloudAdmin": _one(row.get("是否行政处罚案")),
                      "sample": 0, "site": 0, "batches": 0}
    checks: dict[str, dict[str, Any]] = {}
    for row in check_rows.values():
        key = _one(row.get("检查键"))
        if not isinstance(key, str) or not key:
            continue
        stage = _one(row.get("阶段"))
        method = _one(row.get("检查方式"))
        serial = _number(row.get("检查序次")) or 1
        checks[key] = {"case": _case_no(key), "stage": stage, "day": _day(row.get("检查日期")), "serial": serial, "brigade": _one(row.get("所属大队(案卷)")) or None}
        case = cases.get(_case_no(key))
        if case is not None and stage == "初查":
            case["sample"] += 1 if method in (SAMPLE, MIXED) else 0
            case["site"] += 1 if method in (SITE, MIXED) else 0
    products: dict[str, dict[str, Any]] = {}
    for row in product_rows.values():
        key = _one(row.get("产品键"))
        if not isinstance(key, str) or "|" not in key:
            continue
        check_key = key.rsplit("|", 1)[0]
        check = checks.get(check_key)
        stage = check["stage"] if check else None
        method, result = _one(row.get("检查方式")), _one(row.get("检查结果"))
        products[key] = {"checkKey": check_key, "stage": stage, "method": method, "result": result, "link": _one(row.get("对应初查产品键")) or None,
                         "day": check["day"] if check else None, "serial": check["serial"] if check else 1}
        case = cases.get(_case_no(key))
        if case is not None and stage == "初查":
            if method == SAMPLE:
                case["sample"] += 1
                case["batches"] += 1
            elif method == SITE:
                case["site"] += 1
    latest = _latest_valid_rechecks(products)
    admin_cases = {_case_no(key) for key, p in products.items() if p["stage"] == "初查" and p["result"] == "不合格"
                   and any(products[r]["result"] == "不合格" for r in latest.get(key, ()))}
    for key, case in cases.items():
        case["admin"] = "是" if key in admin_cases else "否"
        case["methodClass"] = "" if case["result"] != "不合格" else (SAMPLE if case["sample"] > 0 else (SITE if case["site"] > 0 else "方式缺项"))
    return cases


def _latest_valid_rechecks(products: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """initial product key -> the linked recheck products that are the latest valid result of their chain (ties: all)."""
    chains: dict[str, list[str]] = defaultdict(list)
    for key, p in products.items():
        if p["stage"] == "复查" and p["link"] and p["result"] in ("合格", "不合格"):
            chains[p["link"]].append(key)
    latest: dict[str, list[str]] = {}
    for link, keys in chains.items():
        best = max((products[k]["day"] or "", products[k]["serial"]) for k in keys)
        latest[link] = [k for k in keys if (products[k]["day"] or "", products[k]["serial"]) == best]
    return latest


def build_rechecks(check_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per recheck event key (the same key repeated in the table is one event; the formulas would count the rows)."""
    events: dict[str, dict[str, Any]] = {}
    for row in check_rows.values():
        key = _one(row.get("检查键"))
        if isinstance(key, str) and _one(row.get("阶段")) == "复查":
            events[key] = {"case": _case_no(key), "day": _day(row.get("检查日期")), "brigade": _one(row.get("所属大队(案卷)")) or None}
    return list(events.values())


def duplicate_keys(tables: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[str]]:
    """Stable keys that appear in more than one row: a data error the formulas would count twice."""
    fields = {"案卷主表": "项目编号", "检查明细": "检查键", "产品明细": "产品键"}
    found: dict[str, list[str]] = {}
    for table, name in fields.items():
        seen: dict[str, int] = defaultdict(int)
        for row in tables[table].values():
            key = _one(row.get(name))
            if isinstance(key, str) and key:
                seen[key] += 1
        found[table] = sorted(k for k, n in seen.items() if n > 1)
    return found


def roster_count(roster: list[dict[str, Any]], brigade: str, on: str) -> int:
    count = 0
    for person in roster:
        if brigade != period.CITY and person["brigade"] != brigade:
            continue
        start, end = person["start"] or "0000-00-00", person["end"] or "9999-12-31"
        if start <= on <= end:
            count += 1
    return count


def build_roster(roster_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": _one(row.get("人员ID")), "brigade": _one(row.get("大队")), "start": _day(row.get("有效起日")), "end": _day(row.get("有效止日"))} for row in roster_rows.values()]


def roster_overlaps(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable person IDs with two assignments that share a day (same brigade or not); a transfer is the old brigade up to the 25th and the new one from the 26th."""
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for person in roster:
        if person["id"] and person["brigade"]:
            by_id[person["id"]].append({"brigade": person["brigade"], "start": person["start"] or "0000-00-00", "end": person["end"] or "9999-12-31"})
    found = []
    for person_id, periods in sorted(by_id.items()):
        ordered = sorted(periods, key=lambda p: (p["start"], p["end"]))
        for earlier, later in zip(ordered, ordered[1:]):
            if later["start"] <= earlier["end"]:
                found.append({"id": person_id, "periods": [earlier, later]})
                break
    return found


def expected_row(cases: dict[str, dict[str, Any]], rechecks: list[dict[str, Any]], roster: list[dict[str, Any]], pending_brigades: set[str],
                 *, start: str, end: str, brigade: str, staff_day: str) -> dict[str, Any]:
    """The fifteen indicators plus the gap counts of one period row, by plain code."""
    is_city = brigade == period.CITY
    chosen = [c for c in cases.values() if c["day"] and start <= c["day"] <= end and (is_city or c["brigade"] == brigade)]
    recheck = sum(1 for r in rechecks if r["day"] and start <= r["day"] <= end and (is_city or r["brigade"] == brigade))
    yes = lambda field: sum(1 for c in chosen if c[field] == "是")  # noqa: E731
    values: dict[str, Any] = {
        "每月监督检查次数（初查）": len(chosen), "每月监督检查次数（含复查）": len(chosen) + recheck,
        "每月不合格案卷数": sum(1 for c in chosen if c["result"] == "不合格"),
        "现场判定不合格案卷数": sum(1 for c in chosen if c["methodClass"] == SITE), "抽样送检不合格案卷数": sum(1 for c in chosen if c["methodClass"] == SAMPLE),
        "抽样送检批次数": sum(c["batches"] for c in chosen),
        "检查微型消防站或消防快速处置队消防产品次数": sum(1 for c in chosen if c["micro"] == "是" or c["quick"] == "是"),
        "行政案卷数量": yes("admin"), "刑事案卷数量": yes("criminal"), "检查光伏锂电等相关企业次数": yes("corp"), "检查消防处置力量配备的消防产品次数": yes("force"),
        "网售消防产品检查次数": yes("net"), "维修后灭火器检查次数": yes("fix"), "加油加气站消防产品检测次数": yes("gas"),
        "方式缺项不合格案卷数": sum(1 for c in chosen if c["methodClass"] == "方式缺项"),
        "送检批次缺项案卷数": sum(1 for c in chosen if c["sample"] > 0 and c["batches"] == 0),
        "初查结果待出案卷数": sum(1 for c in chosen if c["result"] in (None, "待结果", "待核实")),
        "缺所属大队案卷数": sum(1 for c in chosen if not c["brigade"]) if is_city else 0,
    }
    values["大队执法监督员人数"] = None if (not is_city and brigade in pending_brigades) else roster_count(roster, brigade, staff_day)
    return values


def gap_lists(cases: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Located gap lists: one entry per case with its project number and unit, for the 问题视图 and the nightly report."""
    lists: dict[str, list[dict[str, Any]]] = {"缺初查日期": [], "缺所属大队": [], "初查结果待出": [], "检查方式缺项": [], "送检批次缺项": []}
    for key, c in sorted(cases.items()):
        entry = {"projectNo": key, "unit": c["unit"], "brigade": c["brigade"], "initialDate": c["day"], "result": c["result"]}
        if not c["day"]:
            lists["缺初查日期"].append(entry)
        if not c["brigade"]:
            lists["缺所属大队"].append(entry)
        if c["result"] in (None, "待结果", "待核实"):
            lists["初查结果待出"].append(entry)
        if c["methodClass"] == "方式缺项":
            lists["检查方式缺项"].append(entry)
        if c["sample"] > 0 and c["batches"] == 0:
            lists["送检批次缺项"].append(entry)
    return lists


def compare(rows: dict[str, dict[str, Any]], cases: dict[str, dict[str, Any]], rechecks: list[dict[str, Any]], roster: list[dict[str, Any]],
            pending_brigades: set[str]) -> dict[str, Any]:
    """Row-by-row comparison of the formula values (``rows``: record id -> {field name: cell}) with the recomputed figures."""
    diffs: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for record_id, row in sorted(rows.items()):
        kind, parsed = period.classify(row)
        if kind not in ("month", "year"):
            continue
        key = parsed[0]
        start, end, staff = _day(row.get("统计起日")), _day(row.get("统计止日")), _day(row.get("人数截止日"))
        brigade = _one(row.get("大队"))
        if not (start and end and staff and brigade):
            diffs.append({"key": key, "problem": "统计期缺起止日、人数截止日或大队，公式无法计算"})
            continue
        expected = expected_row(cases, rechecks, roster, pending_brigades, start=start, end=end, brigade=brigade, staff_day=staff)
        by_key[key] = {"kind": kind, "brigade": brigade, "month": _one(row.get("月份")), "expected": expected, "cloud": {}}
        for name, want in expected.items():
            cell = _one(row.get(name))
            got = None if cell in (None, "") else _number(cell)
            by_key[key]["cloud"][name] = got
            if want is None:
                if got is not None:
                    diffs.append({"key": key, "indicator": name, "feishu": cell, "independent": "空（名册待补）"})
            elif got is None or abs(got - want) > 1e-9:
                if name == "缺所属大队案卷数" and not want and got in (None, 0.0):
                    continue
                diffs.append({"key": key, "indicator": name, "feishu": cell, "independent": want})
        detail.append({"key": key, **{name: want for name, want in expected.items()}})
    relations = _relations(by_key)
    for overlap in roster_overlaps(roster):
        relations.append({"relation": "同一人员ID的任职有效期不得重叠", **overlap})
    blank = sum(1 for person in roster if not person["id"])
    if blank:
        relations.append({"relation": "名录每行必须有人员ID", "rows": blank})
    return {"rowsCompared": len(by_key), "diffs": diffs, "relations": relations, "detail": detail}


def _relations(by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    broken: list[dict[str, Any]] = []
    for key, item in by_key.items():
        e = item["expected"]
        if e["每月不合格案卷数"] != e["现场判定不合格案卷数"] + e["抽样送检不合格案卷数"] + e["方式缺项不合格案卷数"]:
            broken.append({"key": key, "relation": "第4项=第5项+第6项+方式缺项", "values": [e["每月不合格案卷数"], e["现场判定不合格案卷数"], e["抽样送检不合格案卷数"], e["方式缺项不合格案卷数"]]})
    years: dict[tuple[str, str], dict[str, Any]] = {}
    months: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for key, item in by_key.items():
        if item["kind"] == "year":
            years[(item["month"], item["brigade"])] = item
        else:
            months[(int(str(item["month"])[:4]), item["brigade"])].append(item)
    for (label, brigade), item in years.items():
        if brigade == period.CITY:
            continue
        year = int(label[:4])
        parts = months.get((year, brigade), [])
        if len(parts) != 12:
            continue
        for name in ADDITIVE:
            if item["expected"][name] != sum(p["expected"][name] for p in parts):
                broken.append({"key": f"{label}|{brigade}", "relation": "年度=十二个统计月合计", "indicator": name,
                               "year": item["expected"][name], "months": sum(p["expected"][name] for p in parts)})
    for (label, brigade), item in years.items():
        if brigade != period.CITY:
            continue
        teams = [years.get((label, b)) for b in period.BRIGADES]
        if any(t is None for t in teams):
            continue
        for name in ADDITIVE:
            if name == "缺所属大队案卷数":
                continue
            total = sum(t["expected"][name] for t in teams)
            if item["expected"][name] != total:
                broken.append({"key": f"{label}|全市", "relation": "全市=九队合计", "indicator": name, "city": item["expected"][name], "brigades": total})
    return broken


# ---------------------------------------------------------------------------------------------
# Reader used by feishu_mirror.recount
# ---------------------------------------------------------------------------------------------

STATS_TABLES = {"案卷主表": CASE_FIELDS, "检查明细": CHECK_FIELDS, "产品明细": PRODUCT_FIELDS, "执法监督人员名录": ROSTER_FIELDS, "大队名录": BRIGADE_LIST_FIELDS}


def available(contract: dict[str, Any]) -> bool:
    """True when the contract already lists the period columns (older contracts keep the legacy recount only)."""
    month = (contract.get("tables") or {}).get("月度") or {}
    names = {f["name"] for f in month.get("fields", [])}
    return all(n in names for n in (*period.PERIOD_FIELDS, *period.INDICATORS)) and all(t in (contract.get("tables") or {}) for t in STATS_TABLES)


def _ids(contract: dict[str, Any], table: str, names: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    entry = contract["tables"][table]
    found = {f["name"]: f["fieldId"] for f in entry.get("fields", [])}
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"合同里 {table} 缺少字段：{'、'.join(missing)}")
    return entry["tableId"], {n: found[n] for n in names}


def recount_periods(*, contract: dict[str, Any], base: str, transport: Any, settle_attempts: int = 3, settle_seconds: float = 20.0, sleep: Any = None) -> dict[str, Any]:
    """Recompute every period row and compare with the formula values. Formula cells settle a little after a write, so a
    difference is read again (only the month table) up to ``settle_attempts`` times before it is reported."""
    import time

    sleep = sleep or time.sleep
    tables: dict[str, dict[str, Any]] = {}
    for table, names in STATS_TABLES.items():
        table_id, ids = _ids(contract, table, names)
        raw = transport.list_rows(base, table_id, list(ids.values()))
        tables[table] = {rid: {n: row.get(fid) for n, fid in ids.items()} for rid, row in raw.items()}
    cases = build_cases(tables["案卷主表"], tables["检查明细"], tables["产品明细"])
    rechecks = build_rechecks(tables["检查明细"])
    roster = build_roster(tables["执法监督人员名录"])
    pending = {_one(r.get("大队（选项）")) for r in tables["大队名录"].values() if _one(r.get("人员名册状态")) == "待补"}
    month_id, month_ids = _ids(contract, "月度", (*period.PERIOD_FIELDS, *period.INDICATORS, *period.GAPS))
    result: dict[str, Any] = {}
    for attempt in range(settle_attempts + 1):
        raw = transport.list_rows(base, month_id, list(month_ids.values()))
        rows = {rid: {n: row.get(fid) for n, fid in month_ids.items()} for rid, row in raw.items()}
        result = compare(rows, cases, rechecks, roster, pending)
        result["readAttempts"] = attempt + 1
        if not result["diffs"] or attempt == settle_attempts:
            break
        sleep(settle_seconds)
    result["duplicateKeys"] = duplicate_keys(tables)
    result["gaps"] = gap_lists(cases)
    result["gapCounts"] = {k: len(v) for k, v in result["gaps"].items()}
    result["cases"] = len(cases)
    result["rosterRows"] = len(roster)
    result["rosterPendingBrigades"] = sorted(b for b in pending if b)
    adminDiffs = [{"projectNo": k, "feishu": c["cloudAdmin"], "independent": c["admin"]} for k, c in sorted(cases.items()) if (c["cloudAdmin"] or None) != c["admin"]]
    result["administrativeDiffs"] = adminDiffs
    return result
