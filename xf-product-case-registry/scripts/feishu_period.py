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

import hashlib
import json
import os
import re
import uuid
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


def staff_date_for_year(year: int, today: date, generated: Any = ()) -> date:
    """The day whose roster counts for a year row: the 25th of the latest statistics month of that year that has been generated.

    A month counts as generated when its row already exists (``generated``) or when it has started (``today``); the later of
    the two decides, so a December row that already exists fixes the year at 25 December even in November."""
    months = [m for m in generated if isinstance(m, str) and m.startswith(f"{year}-") and _MONTH.match(m)]
    current = stat_month(today)
    last_month = f"{year}-12"
    chosen = max([min(current, last_month), *months])
    if chosen < f"{year}-01":
        chosen = f"{year}-01"
    return month_bounds(chosen)[1]


def desired_rows(today: date, first_month: str = FIRST_MONTH, generated: Any = ()) -> list[dict[str, Any]]:
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
                         "人数截止日": staff_date_for_year(year, today, generated)})
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


def plan(rows: dict[str, dict[str, Any]], today: date, now: datetime, first_month: str = FIRST_MONTH,
         blocked: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """``rows``: record id -> {field name: raw cell} for ``PERIOD_FIELDS``; ``blocked``: key -> open ledger reasons. Pure.

    A key with an open ledger block, duplicate rows or a field that disagrees is left out of the writes; every other key proceeds."""
    existing_months = {parsed[1] for kind, parsed in (classify(row) for row in rows.values()) if kind == "month"}
    wanted = {item["key"]: item for item in desired_rows(today, first_month, existing_months)}
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
            meta[key] = {"月份": f"{year}年度", "大队": brigade, "层级": LEVEL_YEAR, "起": start, "止": end, "人数截止日": staff_date_for_year(year, today, existing_months)}
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
    blocked = blocked or {}
    held = [{"key": key, "reasons": reasons} for key, reasons in sorted(blocked.items())]
    held_keys = set(blocked) | {c["key"] for c in conflicts}
    creates = [c for c in creates if c["key"] not in held_keys]
    updates = [u for u in updates if u["key"] not in held_keys]
    return {"schema": SCHEMA, "today": today.isoformat(), "currentStatMonth": stat_month(today), "creates": creates, "updates": updates, "conflicts": conflicts,
            "duplicates": duplicates, "legacy": [i["key"] for i in legacy], "unrecognized": unrecognized, "blocked": held,
            "counts": {"existing": len(rows), "creates": len(creates), "updates": len(updates), "conflicts": len(conflicts), "duplicates": len(duplicates), "blocked": len(held)},
            "status": "OK" if not (conflicts or duplicates or unrecognized or held) else "ATTENTION"}


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


# ---------------------------------------------------------------------------------------------
# Ledger: one append-only JSONL file per day; the state is read from every file on every run
# ---------------------------------------------------------------------------------------------

RECONCILED_OK = ("NOT_APPLIED_PROVEN", "APPLIED_PROVEN", "READBACK_OK")
MIN_QUIET_SECONDS = 3600
CREATE_WINDOW_BEFORE, CREATE_WINDOW_AFTER = 5, 120
MAX_UNKNOWN_PER_RUN = 3


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stamp(moment: datetime) -> str:
    return moment.astimezone(SHANGHAI).isoformat(timespec="seconds")


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


class Ledger:
    """Append-only intent / terminal / reconciliation events for period writes.

    Events: ``intent`` (op create|update, key, fields, old, intentId), ``result`` (recordId), ``unknown`` (error), ``readback``
    (written, problems), ``reconciled`` (intentId, status, evidence). Events written before intent ids existed (the first a31 run)
    are read as they are: an id is derived from the line, and a terminal belongs to the last open intent of the same op and key in
    the same file. Nothing is ever rewritten or removed; a block is lifted only by appending a ``reconciled`` event."""

    def __init__(self, directory: Path, clock: Any = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(SHANGHAI))

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        moment = self.clock()
        event = {"at": _stamp(moment), **event}
        path = self.directory / f"{moment.astimezone(SHANGHAI).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def state(self) -> dict[str, Any]:
        intents: dict[str, dict[str, Any]] = {}
        corrupt: list[str] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            open_by_target: dict[tuple[Any, Any], str] = {}
            last_result: dict[Any, str] = {}
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    kind = event["event"]
                except (ValueError, KeyError, TypeError):
                    corrupt.append(f"{path.name}:{number}")
                    continue
                if kind == "intent":
                    iid = event.get("intentId") or "legacy-" + hashlib.sha256(f"{path.name}:{number}:{_canonical(event)}".encode()).hexdigest()[:16]
                    intents[iid] = {"intentId": iid, "op": event.get("op"), "key": event.get("key"), "recordId": event.get("recordId"), "fields": event.get("fields") or {},
                                    "old": event.get("old") or {}, "intentAt": event.get("at"), "status": "PENDING", "terminalAt": None, "resultRecordId": None,
                                    "error": None, "reconciled": None, "readbackProblem": None, "file": path.name, "line": number}
                    open_by_target[(event.get("op"), event.get("key"))] = iid
                elif kind in ("result", "unknown"):
                    iid = event.get("intentId") or open_by_target.get((event.get("op"), event.get("key")))
                    item = intents.get(iid or "")
                    if item is not None and item["status"] == "PENDING":
                        item.update(status="RESULT" if kind == "result" else "UNKNOWN", terminalAt=event.get("at"), resultRecordId=event.get("recordId"), error=event.get("error"))
                        last_result[item["key"]] = item["intentId"]
                elif kind == "reconciled":
                    item = intents.get(event.get("intentId") or "")
                    if item is not None:
                        item["reconciled"] = event
                elif kind == "readback":
                    for problem in event.get("problems") or []:
                        iid = problem.get("intentId") or last_result.get(problem.get("key"))
                        if iid in intents:
                            intents[iid]["readbackProblem"] = problem
        return {"intents": intents, "corrupt": corrupt}

    def blocked(self, state: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
        """key -> open reasons. A key stays blocked until every one of its open intents has a ``reconciled`` event."""
        state = state or self.state()
        out: dict[str, list[dict[str, Any]]] = {}
        for item in state["intents"].values():
            reconciled = item["reconciled"]
            if reconciled and reconciled.get("status") in RECONCILED_OK:
                continue
            kind = None
            if item["status"] == "PENDING":
                kind = "PENDING"
            elif item["status"] == "UNKNOWN":
                kind = "UNKNOWN"
            elif item["status"] == "RESULT" and item["op"] == "create" and not item["resultRecordId"]:
                kind = "MISSING_RECORD_ID"
            elif item["status"] == "RESULT" and item["readbackProblem"]:
                kind = "READBACK_PROBLEM"
            if kind:
                out.setdefault(item["key"], []).append({"intentId": item["intentId"], "op": item["op"], "kind": kind, "since": item["terminalAt"] or item["intentAt"],
                                                        "detail": (item["error"] or item["readbackProblem"] or "")})
        return out


def _named(raw: dict[str, dict[str, Any]], field_ids: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {rid: {n: row.get(field_ids[n]) for n in PERIOD_FIELDS} for rid, row in raw.items()}


def _key_of(row: dict[str, Any]) -> str | None:
    kind, parsed = classify(row)
    if kind in ("month", "year"):
        return parsed[0]
    return parsed if kind == "history" and isinstance(parsed, str) else None


def _receipt_record_id(body: Any) -> str | None:
    record = (((body or {}).get("data") or {}).get("record") or {}) if isinstance(body, dict) else {}
    rid = record.get("record_id") if isinstance(record, dict) else None
    return rid if isinstance(rid, str) and rid.startswith("rec") else None


def _cell_value(name: str, cell: Any) -> Any:
    """A cell in the form it is compared in: dates as ``YYYY-MM-DD``, time stamps as ``YYYY-MM-DD HH:MM:SS`` (the read-back carries an
    ISO string with milliseconds and an offset), everything else as the single value."""
    if name in ("统计起日", "统计止日", "人数截止日"):
        return day_of(cell)
    value = one(cell)
    if name in ("结期时间", "回算更新时间") and isinstance(value, str):
        return value.replace("T", " ")[:19]
    return None if value == "" else value


def apply(planned: dict[str, Any], *, transport: Any, base: str, table_id: str, field_ids: dict[str, str], ledger: Ledger, now: datetime, today: date,
          run_id: str | None = None) -> dict[str, Any]:
    """Write the creates and updates of ``planned`` (blocked, duplicate and conflicting keys were already left out by ``plan``).

    Before every write the live state is read again: a create happens only while the key still has no row and no open ledger
    block, an update only while the row still holds exactly the planned old values. An intent line comes first; a write whose
    outcome is unknown (transport failure, receipt without record id) is recorded as UNKNOWN and its key stays blocked on every later
    run until a ``reconciled`` event proves what happened; unrelated keys carry on (at most ``MAX_UNKNOWN_PER_RUN`` unknowns per run).
    A read-back problem is stored as a ledger event as well and blocks its key the same way."""
    missing = [n for n in PERIOD_FIELDS if n not in field_ids]
    if missing:
        raise PeriodError(f"合同里月度表缺少字段：{'、'.join(missing)}")
    run_id = run_id or uuid.uuid4().hex
    operator = {"pid": os.getpid(), "runId": run_id}
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    def wire(fields: dict[str, Any]) -> dict[str, Any]:
        return {field_ids[name]: _wire(name, value) for name, value in fields.items()}

    def stop() -> bool:
        return len(unknown) >= MAX_UNKNOWN_PER_RUN

    for create in planned["creates"]:
        key = create["key"]
        if stop():
            skipped.append({"key": key, "reason": "本次已有多个未知结果，其余留到下次"})
            continue
        if key in ledger.blocked():
            skipped.append({"key": key, "reason": "账本里该键仍有未决写入"})
            continue
        fresh = _named(transport.list_rows(base, table_id, [field_ids[n] for n in PERIOD_FIELDS]), field_ids)
        if any(_key_of(row) == key for row in fresh.values()):
            skipped.append({"key": key, "reason": "写前新鲜读取发现该键已有行，不再创建"})
            continue
        fields = _create_fields(create["item"], now, today)
        intent_id = uuid.uuid4().hex
        ledger.add({"event": "intent", "intentId": intent_id, "runId": run_id, "operator": operator, "op": "create", "key": key, "fields": fields})
        try:
            body = transport.create_record(base, table_id, wire(fields))
        except Exception as error:   # any failure leaves the outcome open
            ledger.add({"event": "unknown", "intentId": intent_id, "op": "create", "key": key, "error": str(error)[:300]})
            unknown.append({"key": key, "intentId": intent_id, "op": "create"})
            continue
        record_id = _receipt_record_id(body)
        if record_id is None:
            ledger.add({"event": "unknown", "intentId": intent_id, "op": "create", "key": key, "error": "创建回执缺少 record_id，效果未知"})
            unknown.append({"key": key, "intentId": intent_id, "op": "create"})
            continue
        ledger.add({"event": "result", "intentId": intent_id, "op": "create", "key": key, "recordId": record_id})
        written.append({"op": "create", "key": key, "recordId": record_id, "fields": fields, "intentId": intent_id})
    for update in planned["updates"]:
        key, record_id = update["key"], update["recordId"]
        if stop():
            skipped.append({"key": key, "reason": "本次已有多个未知结果，其余留到下次"})
            continue
        if key in ledger.blocked():
            skipped.append({"key": key, "reason": "账本里该键仍有未决写入"})
            continue
        live = _named(transport.get_records(base, table_id, [record_id]), field_ids).get(record_id)
        if live is None or any(_cell_value(name, live.get(name)) != _cell_value(name, old) for name, old in update["old"].items()):
            skipped.append({"key": key, "reason": "写前新鲜读取的旧值与计划不符，不覆盖", "live": live})
            continue
        intent_id = uuid.uuid4().hex
        ledger.add({"event": "intent", "intentId": intent_id, "runId": run_id, "operator": operator, "op": "update", "key": key, "recordId": record_id,
                    "fields": update["fields"], "old": update["old"]})
        try:
            transport.upsert(base, table_id, record_id, wire(update["fields"]))
        except Exception as error:
            ledger.add({"event": "unknown", "intentId": intent_id, "op": "update", "key": key, "recordId": record_id, "error": str(error)[:300]})
            unknown.append({"key": key, "intentId": intent_id, "op": "update"})
            continue
        ledger.add({"event": "result", "intentId": intent_id, "op": "update", "key": key, "recordId": record_id})
        written.append({"op": "update", "key": key, "recordId": record_id, "fields": update["fields"], "intentId": intent_id})
    problems = verify(transport, base, table_id, field_ids, written, today)
    by_key = {w["key"]: w["intentId"] for w in written}
    for problem in problems:
        problem["intentId"] = by_key.get(problem.get("key"))
    ledger.add({"event": "readback", "runId": run_id, "written": len(written), "problems": problems})
    status = "UNKNOWN" if unknown else ("READBACK_PROBLEM" if problems else "OK")
    return {"schema": SCHEMA, "written": written, "skipped": skipped, "unknown": unknown, "readbackProblems": problems, "ledger": str(ledger.directory), "status": status}


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
        first_month: str = FIRST_MONTH, clock: Any = None) -> dict[str, Any]:
    """One night's pass. Plan from live rows and the ledger's open blocks; with ``execute`` write the plan; with ``do_stamp`` also record recalculation times."""
    table_id, ids = resolve_fields(contract)
    ledger = Ledger(Path(business_root) / "运行记录" / LEDGER_DIR, clock)
    state = ledger.state()
    if state["corrupt"]:
        raise PeriodRuntimeError("月度统计账有无法解析的行，先人工核对，不写：" + "、".join(state["corrupt"][:5]))
    read = [ids[n] for n in (*PERIOD_FIELDS, *INDICATORS, *GAPS)]
    raw = transport.list_rows(base, table_id, read)
    rows = {rid: {n: row.get(ids[n]) for n in (*PERIOD_FIELDS, *INDICATORS, *GAPS)} for rid, row in raw.items()}
    planned = plan(rows, today, now, first_month, ledger.blocked(state))
    report: dict[str, Any] = {"schema": SCHEMA, "plan": planned, "executed": False}
    if execute:
        result = apply(planned, transport=transport, base=base, table_id=table_id, field_ids=ids, ledger=ledger, now=now, today=today)
        report.update(executed=True, apply=result, status=result["status"] if result["status"] != "OK" else planned["status"])
    else:
        report["status"] = planned["status"]
    if do_stamp:
        if planned["creates"] or planned["updates"]:
            report["stamp"] = {"skipped": "本次还有行要建或补，公式数字未稳定；下次对账通过后再登记"}
        else:
            baseline_dir = ledger.directory
            outcome = stamp(rows, load_baseline(baseline_dir), now)
            if execute and outcome["updates"]:
                writes = {"schema": SCHEMA, "creates": [], "updates": outcome["updates"]}
                result = apply(writes, transport=transport, base=base, table_id=table_id, field_ids=ids, ledger=ledger, now=now, today=today)
                report["stamp"] = {"stamped": [u["key"] for u in outcome["updates"]], "changed": {u["key"]: u["changed"] for u in outcome["updates"]},
                                   "status": result["status"]}
            else:
                report["stamp"] = {"wouldStamp": [u["key"] for u in outcome["updates"]]}
            if execute:
                save_baseline(baseline_dir, outcome["baseline"])
    return report


# ---------------------------------------------------------------------------------------------
# Technical recovery of an open ledger block (same strictness as feishu_source_sync's)
# ---------------------------------------------------------------------------------------------


def reconcile(*, contract: dict[str, Any], base: str, transport: Any, business_root: Path, intent_id: str, now: datetime,
              min_quiet_seconds: int = MIN_QUIET_SECONDS, clock: Any = None) -> dict[str, Any]:
    """Prove what an UNKNOWN (or read-back-problem / receipt-without-id) intent did, then append a ``reconciled`` event.

    Never rewrites an event and never invents a human release. Evidence that is not enough ends in ``RECOVERY_REFUSED`` and the key
    stays blocked. Create: quiet period over, fresh full scan, no row of the same content under another key; zero matches prove
    ``NOT_APPLIED``; one match is accepted only when it is the exact row a later intent of the same key returned (so this
    request created nothing), or when the row's first server revision falls inside this request's own window and no other create
    intent shares that window (``APPLIED``). Update: the row still holds every old value (and the server history shows no revision of
    those fields after the intent) = ``NOT_APPLIED``; every new value present and a revision inside the window = ``APPLIED``."""
    if min_quiet_seconds < MIN_QUIET_SECONDS:
        raise PeriodError(f"静默期不得少于 {MIN_QUIET_SECONDS} 秒")
    table_id, ids = resolve_fields(contract)
    ledger = Ledger(Path(business_root) / "运行记录" / LEDGER_DIR, clock)
    state = ledger.state()
    item = state["intents"].get(intent_id)
    if item is None:
        raise PeriodError("账本里没有这个 intentId")
    report: dict[str, Any] = {"schema": SCHEMA, "intentId": intent_id, "key": item["key"], "op": item["op"], "wasStatus": item["status"]}

    def refuse(reason: str) -> dict[str, Any]:
        ledger.add({"event": "reconcile-refused", "intentId": intent_id, "reason": reason})
        report.update(status="RECOVERY_REFUSED", reason=reason)
        return report

    def prove(status: str, evidence: dict[str, Any]) -> dict[str, Any]:
        ledger.add({"event": "reconciled", "intentId": intent_id, "status": status, "evidence": evidence})
        report.update(status=status, evidence=evidence)
        return report

    if item["reconciled"] and item["reconciled"].get("status") in RECONCILED_OK:
        report["status"] = "ALREADY_RECONCILED"
        return report
    if item["status"] == "PENDING":
        return refuse("意图没有终态记录，进程是否结束不可证明，不能恢复")
    blocked_kind = next((r["kind"] for r in ledger.blocked(state).get(item["key"], []) if r["intentId"] == intent_id), None)
    if blocked_kind is None:
        report["status"] = "NOT_BLOCKED"
        return report
    quiet = now.timestamp() - _epoch(item["terminalAt"] or item["intentAt"])
    if quiet < min_quiet_seconds:
        return refuse(f"距终态仅 {int(quiet)} 秒，未满静默期 {min_quiet_seconds} 秒")
    if blocked_kind == "READBACK_PROBLEM":
        problems = verify(transport, base, table_id, ids, [{"key": item["key"], "fields": item["fields"]}], now.date())
        if problems:
            return refuse("重新读回仍与写入值不符：" + json.dumps(problems[:2], ensure_ascii=False))
        return prove("READBACK_OK", {"quietSeconds": int(quiet), "readback": "写入值与当前读回一致"})
    fresh = _named(transport.list_rows(base, table_id, [ids[n] for n in PERIOD_FIELDS]), ids)
    matches = [rid for rid, row in fresh.items() if _key_of(row) == item["key"]]
    if item["op"] == "create":
        fields = item["fields"]
        twins = [rid for rid, row in fresh.items() if _key_of(row) != item["key"] and one(row.get("月份")) == fields.get("月份") and one(row.get("大队")) == fields.get("大队")
                 and one(row.get("统计层级")) == fields.get("统计层级")]
        if twins:
            return refuse("整表有内容相同但键不同的行：" + "、".join(twins[:5]))
        evidence = {"freshRows": len(fresh), "matches": matches, "quietSeconds": int(quiet)}
        if not matches:
            return prove("NOT_APPLIED_PROVEN", evidence)
        if len(matches) > 1:
            return refuse("该键有多行，先按重复键处理")
        rid = matches[0]
        owners = [o for o in state["intents"].values() if o["key"] == item["key"] and o["intentId"] != intent_id and o["op"] == "create" and o["status"] == "RESULT"
                  and o["resultRecordId"] == rid and (o["intentAt"] or "") >= (item["intentAt"] or "")]
        history = sorted(transport.get_record_history(base, table_id, rid), key=lambda i: i["rev"])
        first = history[0] if history else None
        if first is None or first.get("activity") != "create":
            return refuse("服务端历史缺少创建修订，不能证明该行的创建时间")
        evidence.update(recordId=rid, firstRevisionAt=first["createTime"])
        if owners:
            owner = owners[0]
            low, high = _epoch(owner["intentAt"]) - CREATE_WINDOW_BEFORE, _epoch(owner["terminalAt"]) + CREATE_WINDOW_AFTER
            if not (low <= first["createTime"] <= high):
                return refuse("唯一行的创建时间不在后一次创建意图的窗口内，不能归属于它")
            evidence.update(basis="single-row-returned-by-later-result", ownerIntentId=owner["intentId"])
            return prove("NOT_APPLIED_PROVEN", evidence)
        low, high = _epoch(item["intentAt"]) - CREATE_WINDOW_BEFORE, _epoch(item["terminalAt"] or item["intentAt"]) + CREATE_WINDOW_AFTER
        rivals = [o["intentId"] for o in state["intents"].values() if o["key"] == item["key"] and o["intentId"] != intent_id and o["op"] == "create"
                  and low <= _epoch(o["intentAt"]) <= high]
        if rivals:
            return refuse("窗口内还有同键的其他创建意图：" + "、".join(rivals[:3]))
        if not (low <= first["createTime"] <= high):
            return refuse("唯一行的创建时间不在本次请求窗口内")
        evidence.update(basis="creation-revision-in-own-window")
        return prove("APPLIED_PROVEN", evidence)
    # update intent
    record_id = item["recordId"]
    live = _named(transport.get_records(base, table_id, [record_id]), ids).get(record_id)
    if live is None:
        return refuse("该记录读不到")
    if _key_of(live) != item["key"]:
        return refuse("该记录的键与意图不符")
    equal_old = all(_cell_value(n, live.get(n)) == _cell_value(n, v) for n, v in item["old"].items())
    equal_new = all(_cell_value(n, live.get(n)) == _cell_value(n, v) for n, v in item["fields"].items())
    touched = {ids[n] for n in item["fields"]}
    since = _epoch(item["intentAt"]) - CREATE_WINDOW_BEFORE
    revisions = [h for h in transport.get_record_history(base, table_id, record_id) if h["createTime"] >= since and (not h["fieldIds"] or touched & set(h["fieldIds"]))]
    evidence = {"recordId": record_id, "quietSeconds": int(quiet), "revisionsAfterIntent": len(revisions)}
    if equal_old and not revisions:
        return prove("NOT_APPLIED_PROVEN", evidence)
    if equal_new and revisions and all(h["createTime"] <= _epoch(item["terminalAt"] or item["intentAt"]) + CREATE_WINDOW_AFTER for h in revisions):
        return prove("APPLIED_PROVEN", evidence)
    return refuse("读回值与历史不足以证明该更新是否生效")
