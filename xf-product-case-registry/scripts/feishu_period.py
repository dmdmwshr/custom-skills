"""Period rows of 月度工作统计: nine brigade rows per statistics month, ten rows per statistics year (2026-11-17, a31).

A statistics month runs from the 26th of the previous month to the 25th of its own month (Asia/Shanghai); a statistics year
is its twelve statistics months, normally 26 December of the previous year to 25 December. The fifteen indicator columns are
Feishu formulas that read the case/inspection/product tables directly with each row's own start day, end day and brigade;
nothing here computes a number. This module only keeps the rows alive:

- ``plan``   which month and year rows are missing, which carry blank period metadata, which month has ended (结期) and
             which rows are history (the old 待核实 buckets). Pure; reads nothing from Feishu itself.
- ``apply``  writes exactly the planned creates and updates, each preceded by an intent line and followed by a read-back,
             into ``<业务根>/运行记录/月度统计账``. Idempotent: the key ``月份|大队`` is read first, a row that already exists is
             never created again, a missed night is caught up by the next run, and an unknown write result is read back
             before anything is repeated.
- ``stamp``  after the independent recount agrees with the formulas, records ``回算更新时间`` on an already closed period whose
             numbers changed because later material, results or corrections arrived. The numbers themselves follow the
             formulas at once; only the update time is stored.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
BRIGADES = ("梁溪大队", "经开大队", "滨湖大队", "新吴大队", "锡山大队", "惠山大队", "江阴大队", "宜兴大队", "水上大队")
CITY = "全市"
FIRST_MONTH = "2026-01"          # 采集范围自 2026-01-01 起：2026 年起算，更早的统计月不建
LEVEL_MONTH, LEVEL_YEAR, LEVEL_HISTORY = "月度", "年度", "历史待核实"
OPEN, CLOSED = "进行中", "已结期"
LEDGER_DIR = "月度统计账"
BASELINE_FILE = "统计值基线.json"
SCHEMA = "FeishuPeriodRowsV1"

PERIOD_FIELDS = ("月份大队", "月份", "大队", "统计层级", "统计起日", "统计止日", "人数截止日", "结期状态", "结期时间", "回算更新时间")
INDICATORS = (
    "每月监督检查次数（初查）", "每月监督检查次数（含复查）", "大队执法监督员人数", "每月不合格案卷数", "现场判定不合格案卷数", "抽样送检不合格案卷数",
    "抽样送检批次数", "检查微型消防站或消防快速处置队消防产品次数", "行政案卷数量", "刑事案卷数量", "检查光伏锂电等相关企业次数",
    "检查消防处置力量配备的消防产品次数", "网售消防产品检查次数", "维修后灭火器检查次数", "加油加气站消防产品检测次数",
)
GAPS = ("方式缺项不合格案卷数", "送检批次缺项案卷数", "初查结果待出案卷数", "缺所属大队案卷数")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEAR = re.compile(r"^(\d{4})年度$")


class PeriodError(Exception):
    """Refused before any external call."""


class PeriodRuntimeError(Exception):
    """A write or its read-back did not finish; the ledger says what is open."""


# ---------------------------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------------------------


def today_shanghai() -> date:
    return datetime.now(SHANGHAI).date()


def stat_month(day: date) -> str:
    """The statistics month a calendar day belongs to: the 26th and later roll into the next month."""
    year, month = (day.year, day.month + 1) if day.day >= 26 else (day.year, day.month)
    if month == 13:
        year, month = year + 1, 1
    return f"{year}-{month:02d}"


def month_bounds(month: str) -> tuple[date, date]:
    if not _MONTH.match(month):
        raise PeriodError(f"统计月格式应为 YYYY-MM：{month}")
    year, mon = int(month[:4]), int(month[5:])
    prev_year, prev_mon = (year, mon - 1) if mon > 1 else (year - 1, 12)
    return date(prev_year, prev_mon, 26), date(year, mon, 25)


def year_bounds(year: int) -> tuple[date, date]:
    return date(year - 1, 12, 26), date(year, 12, 25)


def month_after(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"


def months_between(first: str, last: str) -> list[str]:
    result, current = [], first
    while current <= last:
        result.append(current)
        current = month_after(current)
    return result


def month_key(month: str, brigade: str) -> str:
    return f"{month}|{brigade}"


def year_key(year: int, brigade: str) -> str:
    return f"{year}年度|{brigade}"


def staff_date_for_year(year: int, today: date) -> date:
    """The day whose roster counts for a year row: the 25th of the latest statistics month of that year that has started."""
    current = stat_month(today)
    last_month = f"{year}-12"
    chosen = min(current, last_month)
    if chosen < f"{year}-01":
        chosen = f"{year}-01"
    return month_bounds(chosen)[1]


def desired_rows(today: date, first_month: str = FIRST_MONTH) -> list[dict[str, Any]]:
    """Every row that must exist on ``today``: nine brigades for each month from ``first_month`` to the current statistics
    month, and nine brigades plus the city for each statistics year that has started."""
    current = stat_month(today)
    rows: list[dict[str, Any]] = []
    for month in months_between(first_month, current):
        start, end = month_bounds(month)
        for brigade in BRIGADES:
            rows.append({"key": month_key(month, brigade), "月份": month, "大队": brigade, "层级": LEVEL_MONTH, "起": start, "止": end, "人数截止日": end})
    for year in range(int(first_month[:4]), int(current[:4]) + 1):
        start, end = year_bounds(year)
        for brigade in (*BRIGADES, CITY):
            rows.append({"key": year_key(year, brigade), "月份": f"{year}年度", "大队": brigade, "层级": LEVEL_YEAR, "起": start, "止": end,
                         "人数截止日": staff_date_for_year(year, today)})
    return rows


# ---------------------------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------------------------


def one(cell: Any) -> Any:
    if isinstance(cell, list):
        return cell[0] if len(cell) == 1 else (None if not cell else "|".join(str(c) for c in cell))
    return cell


def day_of(cell: Any) -> str | None:
    value = one(cell)
    return str(value)[:10] if value not in (None, "") else None


def _date_text(value: date) -> str:
    return value.isoformat()


def _stamp_text(now: datetime) -> str:
    return now.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:00")


def _desired_state(end: date, today: date) -> str:
    return CLOSED if end < today else OPEN


def classify(row: dict[str, Any]) -> tuple[str, Any]:
    """('month'|'year'|'history'|'unknown', parsed) for an existing row, from the stable key (or month and brigade)."""
    key = one(row.get("月份大队"))
    month, brigade = one(row.get("月份")), one(row.get("大队"))
    if (not isinstance(key, str) or not key) and isinstance(month, str) and brigade:
        key = f"{month}|{brigade}"
    if not isinstance(key, str) or "|" not in key:
        return "unknown", key
    head, team = key.split("|", 1)
    if team == "待核实" or head == "待核实":
        return "history", key
    if _MONTH.match(head) and team in BRIGADES:
        return "month", (key, head, team)
    year = _YEAR.match(head)
    if year and team in (*BRIGADES, CITY):
        return "year", (key, int(year.group(1)), team)
    return "unknown", key


# ---------------------------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------------------------


def plan(rows: dict[str, dict[str, Any]], today: date, now: datetime, first_month: str = FIRST_MONTH) -> dict[str, Any]:
    """``rows``: record id -> {field name: raw cell} for ``PERIOD_FIELDS``. Pure."""
    wanted = {item["key"]: item for item in desired_rows(today, first_month)}
    present: dict[str, list[str]] = {}
    legacy: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    meta: dict[str, dict[str, Any]] = {}
    for record_id, row in sorted(rows.items()):
        kind, parsed = classify(row)
        if kind == "month":
            key, month, brigade = parsed
            start, end = month_bounds(month)
            meta[key] = {"月份": month, "大队": brigade, "层级": LEVEL_MONTH, "起": start, "止": end, "人数截止日": end}
        elif kind == "year":
            key, year, brigade = parsed
            start, end = year_bounds(year)
            meta[key] = {"月份": f"{year}年度", "大队": brigade, "层级": LEVEL_YEAR, "起": start, "止": end, "人数截止日": staff_date_for_year(year, today)}
        elif kind == "history":
            legacy.append({"recordId": record_id, "key": parsed, "现层级": one(row.get("统计层级"))})
            continue
        else:
            unrecognized.append({"recordId": record_id, "key": parsed})
            continue
        present.setdefault(key, []).append(record_id)
    duplicates = {key: ids for key, ids in present.items() if len(ids) > 1}
    creates = [{"key": key, "item": item} for key, item in wanted.items() if key not in present]
    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key, ids in sorted(present.items()):
        if key in duplicates:
            continue
        record_id, row, item = ids[0], rows[ids[0]], meta[key]
        fields: dict[str, Any] = {}
        old: dict[str, Any] = {}

        def fill(name: str, new: Any, current: Any, *, may_move_forward: bool = False) -> None:
            if current in (None, ""):
                fields[name], old[name] = new, None
            elif current != new:
                if may_move_forward and str(new) > str(current):
                    fields[name], old[name] = new, current
                else:
                    conflicts.append({"recordId": record_id, "key": key, "field": name, "current": current, "expected": new})

        fill("统计层级", item["层级"], one(row.get("统计层级")))
        fill("统计起日", _date_text(item["起"]), day_of(row.get("统计起日")))
        fill("统计止日", _date_text(item["止"]), day_of(row.get("统计止日")))
        fill("人数截止日", _date_text(item["人数截止日"]), day_of(row.get("人数截止日")), may_move_forward=item["层级"] == LEVEL_YEAR)
        state, current_state = _desired_state(item["止"], today), one(row.get("结期状态"))
        if current_state in (None, ""):
            fields["结期状态"], old["结期状态"] = state, None
            if state == CLOSED:
                fields["结期时间"], old["结期时间"] = _stamp_text(now), one(row.get("结期时间"))
        elif current_state == OPEN and state == CLOSED:
            fields["结期状态"], old["结期状态"] = CLOSED, OPEN
            fields["结期时间"], old["结期时间"] = _stamp_text(now), one(row.get("结期时间"))
        elif current_state == CLOSED and state == OPEN:
            conflicts.append({"recordId": record_id, "key": key, "field": "结期状态", "current": current_state, "expected": state})
        if fields:
            updates.append({"recordId": record_id, "key": key, "fields": fields, "old": old})
    for item in legacy:
        if item["现层级"] in (None, ""):
            updates.append({"recordId": item["recordId"], "key": item["key"], "fields": {"统计层级": LEVEL_HISTORY}, "old": {"统计层级": None}})
    return {"schema": SCHEMA, "today": today.isoformat(), "currentStatMonth": stat_month(today), "creates": creates, "updates": updates, "conflicts": conflicts,
            "duplicates": duplicates, "legacy": [i["key"] for i in legacy], "unrecognized": unrecognized,
            "counts": {"existing": len(rows), "creates": len(creates), "updates": len(updates), "conflicts": len(conflicts), "duplicates": len(duplicates)},
            "status": "OK" if not (conflicts or duplicates or unrecognized) else "ATTENTION"}


# ---------------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------------


def _wire(name: str, value: Any) -> Any:
    if name in ("统计层级", "结期状态", "大队"):
        return [value]
    if name in ("统计起日", "统计止日", "人数截止日"):
        return f"{value} 00:00:00"
    return value


def _create_fields(item: dict[str, Any], now: datetime, today: date) -> dict[str, Any]:
    state = _desired_state(item["止"], today)
    fields = {"月份大队": item["key"], "月份": item["月份"], "大队": item["大队"], "统计层级": item["层级"], "统计起日": _date_text(item["起"]),
              "统计止日": _date_text(item["止"]), "人数截止日": _date_text(item["人数截止日"]), "结期状态": state}
    if state == CLOSED:
        fields["结期时间"] = _stamp_text(now)
    return fields


class _Ledger:
    def __init__(self, directory: Path, day: str):
        self.path = Path(directory) / f"{day}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def apply(planned: dict[str, Any], *, transport: Any, base: str, table_id: str, field_ids: dict[str, str], ledger_dir: Path, now: datetime, today: date) -> dict[str, Any]:
    """Write the creates and updates of ``planned``. Intent line first, result line after; read-back at the end.

    Stops at the first write whose outcome is unknown: nothing after it is attempted and nothing is repeated; the next run reads the
    key first and decides from what is there."""
    if planned["conflicts"] or planned["duplicates"] or planned["unrecognized"]:
        raise PeriodError("计划里有冲突、重复键或不认识的行，先核对，不写")
    missing = [n for n in PERIOD_FIELDS if n not in field_ids]
    if missing:
        raise PeriodError(f"合同里月度表缺少字段：{'、'.join(missing)}")
    ledger = _Ledger(ledger_dir, now.astimezone(SHANGHAI).date().isoformat())
    written: list[dict[str, Any]] = []
    stamp = now.astimezone(SHANGHAI).isoformat(timespec="seconds")

    def wire(fields: dict[str, Any]) -> dict[str, Any]:
        return {field_ids[name]: _wire(name, value) for name, value in fields.items()}

    for create in planned["creates"]:
        fields = _create_fields(create["item"], now, today)
        ledger.add({"event": "intent", "at": stamp, "op": "create", "key": create["key"], "fields": fields})
        try:
            body = transport.create_record(base, table_id, wire(fields))
        except Exception as error:   # the transport raises RuntimeSyncError; any failure leaves the outcome open
            ledger.add({"event": "unknown", "at": stamp, "op": "create", "key": create["key"], "error": str(error)[:300]})
            raise PeriodRuntimeError(f"UNKNOWN: 创建 {create['key']} 结果未知，已记账；下次运行先读该键再决定，不重放") from error
        record_id = (((body or {}).get("data") or {}).get("record") or {}).get("record_id")
        ledger.add({"event": "result", "at": stamp, "op": "create", "key": create["key"], "recordId": record_id})
        written.append({"op": "create", "key": create["key"], "recordId": record_id, "fields": fields})
    for update in planned["updates"]:
        ledger.add({"event": "intent", "at": stamp, "op": "update", "key": update["key"], "recordId": update["recordId"], "fields": update["fields"], "old": update["old"]})
        try:
            transport.upsert(base, table_id, update["recordId"], wire(update["fields"]))
        except Exception as error:
            ledger.add({"event": "unknown", "at": stamp, "op": "update", "key": update["key"], "recordId": update["recordId"], "error": str(error)[:300]})
            raise PeriodRuntimeError(f"UNKNOWN: 更新 {update['key']} 结果未知，已记账；下次运行先读该行再决定，不重放") from error
        ledger.add({"event": "result", "at": stamp, "op": "update", "key": update["key"], "recordId": update["recordId"]})
        written.append({"op": "update", "key": update["key"], "recordId": update["recordId"], "fields": update["fields"]})
    problems = verify(transport, base, table_id, field_ids, written, today)
    ledger.add({"event": "readback", "at": stamp, "written": len(written), "problems": problems})
    return {"schema": SCHEMA, "written": written, "readbackProblems": problems, "ledger": str(ledger.path),
            "status": "OK" if not problems else "READBACK_PROBLEM"}


def verify(transport: Any, base: str, table_id: str, field_ids: dict[str, str], written: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Read the table once and check that each written key exists exactly once with the written values."""
    if not written:
        return []
    rows = transport.list_rows(base, table_id, [field_ids[n] for n in PERIOD_FIELDS])
    named = {rid: {n: row.get(field_ids[n]) for n in PERIOD_FIELDS} for rid, row in rows.items()}
    by_key: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for rid, row in named.items():
        kind, parsed = classify(row)
        key = parsed[0] if kind in ("month", "year") else (parsed if kind == "history" else None)
        if isinstance(key, str):
            by_key.setdefault(key, []).append((rid, row))
    problems: list[dict[str, Any]] = []
    for item in written:
        found = by_key.get(item["key"], [])
        if len(found) != 1:
            problems.append({"key": item["key"], "problem": f"读回该键 {len(found)} 行，应为 1 行"})
            continue
        _, row = found[0]
        for name, value in item["fields"].items():
            got = day_of(row.get(name)) if name in ("统计起日", "统计止日", "人数截止日") else one(row.get(name))
            want = str(value)[:10] if name in ("统计起日", "统计止日", "人数截止日") else value
            if name in ("结期时间", "回算更新时间"):
                if not got:
                    problems.append({"key": item["key"], "field": name, "expected": value, "got": got})
            elif got != want:
                problems.append({"key": item["key"], "field": name, "expected": value, "got": got})
    return problems


# ---------------------------------------------------------------------------------------------
# Recalculation stamp
# ---------------------------------------------------------------------------------------------


def _numbers(row: dict[str, Any]) -> dict[str, str]:
    values = {}
    for name in (*INDICATORS, *GAPS):
        cell = one(row.get(name))
        values[name] = "" if cell is None else str(cell)
    return values


def stamp(rows: dict[str, dict[str, Any]], baseline: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Compare the settled numbers with the last kept ones. A change on a row that was already closed when the baseline was taken
    gets a ``回算更新时间``; every row's kept numbers are refreshed. Pure: returns the updates and the new baseline."""
    new_baseline: dict[str, Any] = {}
    updates: list[dict[str, Any]] = []
    stamp_text = _stamp_text(now)
    for record_id, row in sorted(rows.items()):
        kind, parsed = classify(row)
        if kind not in ("month", "year"):
            continue
        key = parsed[0]
        values = _numbers(row)
        previous = baseline.get(key)
        entry = {"values": values, "observedAt": now.astimezone(SHANGHAI).isoformat(timespec="seconds")}
        if previous is not None and previous.get("values") != values and one(row.get("结期状态")) == CLOSED and previous.get("closed") is True:
            changed = {n: {"before": previous["values"].get(n), "after": values[n]} for n in values if previous["values"].get(n) != values[n]}
            updates.append({"recordId": record_id, "key": key, "fields": {"回算更新时间": stamp_text}, "old": {"回算更新时间": one(row.get("回算更新时间"))}, "changed": changed})
            entry["stampedAt"] = stamp_text
        elif previous is not None and previous.get("stampedAt"):
            entry["stampedAt"] = previous["stampedAt"]
        entry["closed"] = one(row.get("结期状态")) == CLOSED
        new_baseline[key] = entry
    return {"updates": updates, "baseline": new_baseline}


def load_baseline(directory: Path) -> dict[str, Any]:
    path = Path(directory) / BASELINE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_baseline(directory: Path, baseline: dict[str, Any]) -> None:
    path = Path(directory) / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(baseline, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def resolve_fields(contract: dict[str, Any], table_name: str = "月度") -> tuple[str, dict[str, str]]:
    """(table id, {field name: field id}) of the 月度 table in the contract; every name this module touches must be listed."""
    table = (contract.get("tables") or {}).get(table_name)
    if not isinstance(table, dict):
        raise PeriodError(f"合同里没有 {table_name} 表")
    ids = {f["name"]: f["fieldId"] for f in table.get("fields", [])}
    missing = [n for n in (*PERIOD_FIELDS, *INDICATORS, *GAPS) if n not in ids]
    if missing:
        raise PeriodError(f"合同里 {table_name} 缺少字段：{'、'.join(missing)}")
    return table["tableId"], ids


def run(*, contract: dict[str, Any], base: str, transport: Any, business_root: Path, today: date, now: datetime, execute: bool, do_stamp: bool = False,
        first_month: str = FIRST_MONTH) -> dict[str, Any]:
    """One night's pass. Plan from live rows; with ``execute`` write the plan; with ``do_stamp`` also record recalculation times."""
    table_id, ids = resolve_fields(contract)
    ledger_dir = Path(business_root) / "运行记录" / LEDGER_DIR
    read = [ids[n] for n in (*PERIOD_FIELDS, *INDICATORS, *GAPS)]
    raw = transport.list_rows(base, table_id, read)
    rows = {rid: {n: row.get(ids[n]) for n in (*PERIOD_FIELDS, *INDICATORS, *GAPS)} for rid, row in raw.items()}
    planned = plan(rows, today, now, first_month)
    report: dict[str, Any] = {"schema": SCHEMA, "plan": planned, "executed": False}
    if execute:
        if planned["status"] != "OK":
            report["status"] = "ATTENTION"
            return report
        result = apply(planned, transport=transport, base=base, table_id=table_id, field_ids=ids, ledger_dir=ledger_dir, now=now, today=today)
        report.update(executed=True, apply=result, status=result["status"])
    else:
        report["status"] = planned["status"]
    if do_stamp:
        if planned["creates"] or planned["updates"]:
            report["stamp"] = {"skipped": "本次还有行要建或补，公式数字未稳定；下次对账通过后再登记"}
        else:
            outcome = stamp(rows, load_baseline(ledger_dir), now)
            if execute and outcome["updates"]:
                writes = {"schema": SCHEMA, "creates": [], "updates": outcome["updates"], "conflicts": [], "duplicates": {}, "unrecognized": []}
                result = apply(writes, transport=transport, base=base, table_id=table_id, field_ids=ids, ledger_dir=ledger_dir, now=now, today=today)
                report["stamp"] = {"stamped": [u["key"] for u in outcome["updates"]], "changed": {u["key"]: u["changed"] for u in outcome["updates"]},
                                   "status": result["status"]}
            else:
                report["stamp"] = {"wouldStamp": [u["key"] for u in outcome["updates"]]}
            if execute:
                save_baseline(ledger_dir, outcome["baseline"])
    return report
