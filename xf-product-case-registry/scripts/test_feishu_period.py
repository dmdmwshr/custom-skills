"""Month and year rows of 月度工作统计: calendar, idempotent creation, catch-up, closing, history rows, recalculation stamp,
persistent unknown-write blocks, fresh checks before every write, and strict technical recovery."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

import scripts.feishu_period as period

SHANGHAI = period.SHANGHAI
T = lambda text: datetime.fromisoformat(text).replace(tzinfo=SHANGHAI)  # noqa: E731
BASE, TABLE = "BaseMONTH0000000000000000000", "tblMONTH0000001"
NAMES = (*period.PERIOD_FIELDS, *period.INDICATORS, *period.GAPS)
IDS = {name: f"fld{index:03d}" for index, name in enumerate(NAMES)}
DATE_FIELDS = ("统计起日", "统计止日", "人数截止日")


class Cloud:
    """In-memory 月度 table with the wire shapes of the official CLI (select -> [name], datetime -> ISO with offset)."""

    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}
        self.counter = 0
        self.fail_create: set[str] = set()          # keys whose create raises (outcome unknown, nothing stored)
        self.lost_create: set[str] = set()          # keys whose create is stored but the receipt is lost (raises)
        self.no_receipt_id: set[str] = set()        # keys whose create is stored and answered without a record id
        self.fail_update: set[str] = set()
        self.corrupt: dict[str, tuple[str, object]] = {}   # record key -> (field name, wrong value stored instead)
        self.create_calls: list[str] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.epoch = T("2026-11-17T02:00:00").timestamp()
        self.created_at: dict[str, float] = {}
        self.history_extra: dict[str, list[dict]] = {}

    def _store(self, row: dict, fields: dict) -> None:
        name_of = {fid: name for name, fid in IDS.items()}
        for fid, value in fields.items():
            name = name_of[fid]
            if name in DATE_FIELDS:
                row[fid] = value.replace(" 00:00:00", "") + "T00:00:00.000+08:00"
            elif name in ("结期时间", "回算更新时间"):
                row[fid] = value.replace(" ", "T") + ".000+08:00"
            else:
                row[fid] = value

    def list_rows(self, base, table, field_ids=None):
        return {rid: {f: row.get(f) for f in (field_ids or row)} for rid, row in self.rows.items()}

    def get_records(self, base, table, record_ids):
        return {rid: dict(self.rows[rid]) for rid in record_ids if rid in self.rows}

    def create_record(self, base, table, fields):
        key = fields.get(IDS["月份大队"])
        self.create_calls.append(key)
        if key in self.fail_create:
            raise RuntimeError("模拟断线")
        self.counter += 1
        rid = f"rec{self.counter:012d}"
        self.rows[rid] = {}
        self._store(self.rows[rid], fields)
        self.created_at[rid] = self.epoch
        self.writes.append(("create", rid, dict(fields)))
        if key in self.corrupt_keys():
            name, wrong = self.corrupt[key]
            self.rows[rid][IDS[name]] = wrong
        if key in self.lost_create:
            raise RuntimeError("模拟回执丢失")
        if key in self.no_receipt_id:
            return {"data": {"record": {}}}
        return {"data": {"record": {"record_id": rid}}}

    def corrupt_keys(self):
        return set(self.corrupt)

    def upsert(self, base, table, record_id, fields):
        if record_id in self.fail_update:
            raise RuntimeError("模拟断线")
        self._store(self.rows[record_id], fields)
        self.writes.append(("update", record_id, dict(fields)))
        return {"data": {"record": {"record_id": record_id}}}

    def get_record_history(self, base, table, record_id):
        first = {"rev": 1, "createTime": self.created_at.get(record_id, self.epoch), "operator": "tool", "activity": "create", "fieldIds": []}
        return sorted([first, *self.history_extra.get(record_id, [])], key=lambda i: -i["rev"])

    def named(self):
        return {rid: {name: row.get(fid) for name, fid in IDS.items()} for rid, row in self.rows.items()}


def _contract():
    return {"baseId": BASE, "tables": {"月度": {"tableId": TABLE, "fields": [{"fieldId": fid, "name": name, "type": "text", "class": "admin-only"} for name, fid in IDS.items()]}}}


class Clock:
    """Ledger clock that the test moves by hand; every event gets its own second."""

    def __init__(self, start: str):
        self.moment = T(start)

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment


def _run(cloud, tmp_path, day, hour="02:00:00", clock=None, **options):
    today = date.fromisoformat(day)
    clock = clock or Clock(f"{day}T{hour}")
    return period.run(contract=_contract(), base=BASE, transport=cloud, business_root=tmp_path, today=today, now=T(f"{day}T{hour}"), clock=clock,
                      execute=options.pop("execute", True), **options)


def _ledger(tmp_path):
    return period.Ledger(tmp_path / "运行记录" / period.LEDGER_DIR)


def _events(tmp_path):
    out = []
    for path in sorted((tmp_path / "运行记录" / period.LEDGER_DIR).glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def _reconcile(cloud, tmp_path, intent_id, at, **options):
    return period.reconcile(contract=_contract(), base=BASE, transport=cloud, business_root=tmp_path, intent_id=intent_id, now=T(at), clock=Clock(at), **options)


def _key_rows(cloud, key):
    return [rid for rid, r in cloud.named().items() if period.one(r["月份大队"]) == key]


# ---------------------------------------------------------------------------------------------
# Calendar, creation, catch-up (unchanged behaviour)
# ---------------------------------------------------------------------------------------------


def test_the_26th_belongs_to_the_next_statistics_month_and_the_year_edges_follow():
    assert [period.stat_month(date.fromisoformat(d)) for d in ("2026-03-25", "2026-03-26", "2026-12-25", "2026-12-26", "2027-01-25")] == \
        ["2026-03", "2026-04", "2026-12", "2027-01", "2027-01"]
    assert period.month_bounds("2026-01") == (date(2025, 12, 26), date(2026, 1, 25))
    assert period.month_bounds("2027-01") == (date(2026, 12, 26), date(2027, 1, 25))
    assert period.year_bounds(2026) == (date(2025, 12, 26), date(2026, 12, 25))
    with pytest.raises(period.PeriodError):
        period.month_bounds("2026-13")


def test_rows_wanted_on_a_day_are_nine_brigades_per_month_and_ten_per_year_and_the_staff_day_follows_the_latest_started_month():
    rows = period.desired_rows(date(2026, 11, 17))
    months = [r for r in rows if r["层级"] == "月度"]
    years = [r for r in rows if r["层级"] == "年度"]
    assert len(months) == 9 * 11 and {r["月份"] for r in months} == {f"2026-{m:02d}" for m in range(1, 12)}
    assert len(years) == 10 and {r["大队"] for r in years} == {*period.BRIGADES, "全市"}
    assert {r["人数截止日"] for r in years} == {date(2026, 11, 25)}          # 年内最近已生成的统计月是 2026-11，取其 25 日
    on_26th = period.desired_rows(date(2026, 11, 26))
    assert {r["月份"] for r in on_26th if r["层级"] == "月度"} >= {"2026-12"} and {r["人数截止日"] for r in on_26th if r["层级"] == "年度"} == {date(2026, 12, 25)}
    new_year = period.desired_rows(date(2026, 12, 26))
    assert {r["月份"] for r in new_year if r["层级"] == "年度"} == {"2026年度", "2027年度"}
    assert {r["人数截止日"] for r in new_year if r["月份"] == "2026年度"} == {date(2026, 12, 25)}
    assert {r["人数截止日"] for r in new_year if r["月份"] == "2027年度"} == {date(2027, 1, 25)}


def test_the_annual_staff_day_follows_the_latest_generated_month_even_when_it_is_ahead_of_today():
    assert period.staff_date_for_year(2026, date(2026, 11, 17)) == date(2026, 11, 25)
    assert period.staff_date_for_year(2026, date(2026, 11, 17), {"2026-11", "2026-12"}) == date(2026, 12, 25)
    assert period.staff_date_for_year(2026, date(2026, 11, 17), {"2025-12", "2027-01"}) == date(2026, 11, 25)       # 别的年份的月不算
    assert period.staff_date_for_year(2027, date(2026, 12, 26)) == date(2027, 1, 25)


def test_first_run_creates_every_row_once_and_a_repeat_run_writes_nothing(tmp_path):
    cloud = Cloud()
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "OK" and first["apply"]["readbackProblems"] == [] and len(cloud.rows) == 9 * 11 + 10
    assert first["plan"]["counts"]["creates"] == 109
    writes = len(cloud.writes)
    again = _run(cloud, tmp_path, "2026-11-17", "02:30:00")
    assert again["plan"]["counts"] == {"existing": 109, "creates": 0, "updates": 0, "conflicts": 0, "duplicates": 0, "blocked": 0} and len(cloud.writes) == writes
    keys = [period.one(r["月份大队"]) for r in cloud.named().values()]
    assert len(keys) == len(set(keys)) and "2026-11|梁溪大队" in keys and "2026年度|全市" in keys


def test_a_missed_night_is_caught_up_by_the_next_run_and_closing_happens_on_the_26th(tmp_path):
    cloud = Cloud()
    _run(cloud, tmp_path, "2026-11-17")
    closed = {period.one(r["月份大队"]): period.one(r["结期状态"]) for r in cloud.named().values()}
    assert closed["2026-10|梁溪大队"] == "已结期" and closed["2026-11|梁溪大队"] == "进行中"      # 11-25 才结前月；10 月早已结
    late = _run(cloud, tmp_path, "2026-11-27")
    assert late["plan"]["counts"]["creates"] == 9 and late["status"] == "OK"
    state = {period.one(r["月份大队"]): (period.one(r["结期状态"]), period.one(r["结期时间"])) for r in cloud.named().values()}
    assert state["2026-12|梁溪大队"][0] == "进行中" and state["2026-11|梁溪大队"][0] == "已结期" and state["2026-11|梁溪大队"][1]
    assert period.day_of(next(r for r in cloud.named().values() if period.one(r["月份大队"]) == "2026年度|全市")["人数截止日"]) == "2026-12-25"
    on_the_day = Cloud()
    _run(on_the_day, tmp_path / "other", "2026-11-25")
    assert {period.one(r["结期状态"]) for r in on_the_day.named().values() if period.one(r["月份"]) == "2026-11"} == {"进行中"}   # 25 日当天仍进行中


def test_an_existing_december_row_moves_the_annual_staff_day_to_25_december_on_17_november(tmp_path):
    cloud = Cloud()
    _run(cloud, tmp_path, "2026-11-17")
    assert {period.day_of(r["人数截止日"]) for r in cloud.named().values() if period.one(r["月份"]) == "2026年度"} == {"2026-11-25"}
    for brigade in period.BRIGADES:                                   # 12 月月行早已存在（早先建好）
        cloud.counter += 1
        rid = f"recDEC{cloud.counter:010d}"
        cloud.rows[rid] = {IDS["月份大队"]: f"2026-12|{brigade}", IDS["月份"]: "2026-12", IDS["大队"]: [brigade], IDS["统计层级"]: ["月度"],
                           IDS["统计起日"]: "2026-11-26T00:00:00.000+08:00", IDS["统计止日"]: "2026-12-25T00:00:00.000+08:00",
                           IDS["人数截止日"]: "2026-12-25T00:00:00.000+08:00", IDS["结期状态"]: ["进行中"]}
    planned = period.plan(cloud.named(), date(2026, 11, 17), T("2026-11-17T03:00:00"))
    annual = [u for u in planned["updates"] if u["key"].startswith("2026年度|")]
    assert len(annual) == 10 and all(u["fields"] == {"人数截止日": "2026-12-25"} and u["old"] == {"人数截止日": "2026-11-25"} for u in annual)
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert again["status"] == "OK" and {period.day_of(r["人数截止日"]) for r in cloud.named().values() if period.one(r["月份"]) == "2026年度"} == {"2026-12-25"}


def test_existing_rows_get_only_blank_period_fields_filled_and_old_buckets_become_history(tmp_path):
    cloud = Cloud()
    cloud.rows["recOLD000000001"] = {IDS["月份大队"]: "2026-05|梁溪大队", IDS["月份"]: "2026-05", IDS["大队"]: ["梁溪大队"]}
    cloud.rows["recOLD000000002"] = {IDS["月份大队"]: "2026-05|待核实", IDS["月份"]: "2026-05", IDS["大队"]: ["待核实"]}
    cloud.rows["recOLD000000003"] = {IDS["月份大队"]: "待核实|待核实", IDS["月份"]: "待核实", IDS["大队"]: ["待核实"]}
    cloud.rows["recOLD000000004"] = {IDS["月份大队"]: "2026-06|经开大队", IDS["月份"]: "2026-06", IDS["大队"]: ["经开大队"], IDS["统计层级"]: ["月度"],
                                       IDS["统计起日"]: "2026-05-25T00:00:00.000+08:00", IDS["统计止日"]: "2026-06-25T00:00:00.000+08:00"}   # 起日写错：不覆盖，列冲突
    planned = period.plan(cloud.named(), date(2026, 11, 17), T("2026-11-17T02:00:00"))
    by_key = {u["key"]: u for u in planned["updates"]}
    assert by_key["2026-05|梁溪大队"]["fields"]["统计起日"] == "2026-04-26" and by_key["2026-05|梁溪大队"]["fields"]["结期状态"] == "已结期"
    assert by_key["2026-05|待核实"]["fields"] == {"统计层级": "历史待核实"} and by_key["待核实|待核实"]["fields"] == {"统计层级": "历史待核实"}
    assert "2026-06|经开大队" not in by_key                           # 有冲突的键整体不写
    assert planned["conflicts"] == [{"recordId": "recOLD000000004", "key": "2026-06|经开大队", "field": "统计起日", "current": "2026-05-25", "expected": "2026-05-26"}]
    assert planned["status"] == "ATTENTION"
    held = _run(cloud, tmp_path, "2026-11-17")
    assert held["status"] == "ATTENTION" and period.one(cloud.rows["recOLD000000002"][IDS["统计层级"]]) == "历史待核实"      # 无关键照常写
    assert IDS["统计止日"] in cloud.rows["recOLD000000004"] and cloud.rows["recOLD000000004"][IDS["统计起日"]].startswith("2026-05-25")  # 冲突键原样
    cloud.rows["recOLD000000004"][IDS["统计起日"]] = "2026-05-26T00:00:00.000+08:00"
    ok = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert ok["status"] == "OK" and period.one(cloud.rows["recOLD000000001"][IDS["统计层级"]]) == "月度"


def test_a_duplicate_key_or_an_unrecognized_row_holds_only_its_own_key(tmp_path):
    cloud = Cloud()
    for rid in ("recDUP000000001", "recDUP000000002"):
        cloud.rows[rid] = {IDS["月份大队"]: "2026-03|梁溪大队", IDS["月份"]: "2026-03", IDS["大队"]: ["梁溪大队"]}
    cloud.rows["recUNK000000001"] = {IDS["月份大队"]: "随手记", IDS["月份"]: "?", IDS["大队"]: ["梁溪大队"]}
    result = _run(cloud, tmp_path, "2026-11-17")
    assert result["status"] == "ATTENTION"
    assert result["plan"]["duplicates"] == {"2026-03|梁溪大队": ["recDUP000000001", "recDUP000000002"]} and result["plan"]["unrecognized"][0]["key"] == "随手记"
    assert all(w[1] not in ("recDUP000000001", "recDUP000000002") for w in cloud.writes) and len(cloud.rows) > 3        # 无关的月行照常建出


# ---------------------------------------------------------------------------------------------
# Unknown outcomes stay blocked across runs
# ---------------------------------------------------------------------------------------------


def test_an_unknown_create_is_blocked_on_every_later_run_and_never_sent_twice(tmp_path):
    cloud = Cloud()
    cloud.fail_create = {"2026-02|梁溪大队"}
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "UNKNOWN" and [u["key"] for u in first["apply"]["unknown"]] == ["2026-02|梁溪大队"]
    assert len(cloud.rows) == 9 * 11 + 10 - 1                          # 无关键照常建出
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    third = _run(cloud, tmp_path, "2026-11-18", "03:00:00")
    assert cloud.create_calls.count("2026-02|梁溪大队") == 1            # 正常再入口不重发
    assert again["status"] == "ATTENTION" and third["status"] == "ATTENTION"
    assert [b["key"] for b in again["plan"]["blocked"]] == ["2026-02|梁溪大队"] and again["plan"]["blocked"][0]["reasons"][0]["kind"] == "UNKNOWN"
    assert "2026-02|梁溪大队" not in [c["key"] for c in again["plan"]["creates"]]
    kinds = [e["event"] for e in _events(tmp_path) if e.get("key") == "2026-02|梁溪大队"]
    assert kinds == ["intent", "unknown"]                              # 原未知事件原样保留，没有被改写


def test_an_unknown_create_whose_write_actually_landed_stays_blocked_until_it_is_proven(tmp_path):
    cloud = Cloud()
    cloud.lost_create = {"2026-02|梁溪大队"}
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "UNKNOWN" and len(_key_rows(cloud, "2026-02|梁溪大队")) == 1
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert again["status"] == "ATTENTION" and cloud.create_calls.count("2026-02|梁溪大队") == 1
    assert not [w for w in cloud.writes if w[0] == "update" and w[1] == _key_rows(cloud, "2026-02|梁溪大队")[0]]     # 迟到出现的行也不被改
    intent = next(e["intentId"] for e in _events(tmp_path) if e["event"] == "unknown")
    stamped = next(e["at"] for e in _events(tmp_path) if e["event"] == "intent" and e["intentId"] == intent)
    cloud.created_at[_key_rows(cloud, "2026-02|梁溪大队")[0]] = datetime.fromisoformat(stamped).timestamp() + 1      # 行是该请求创建的
    early = _reconcile(cloud, tmp_path, intent, "2026-11-17T02:30:00")
    assert early["status"] == "RECOVERY_REFUSED" and "静默期" in early["reason"]
    proven = _reconcile(cloud, tmp_path, intent, "2026-11-17T04:00:00")
    assert proven["status"] == "APPLIED_PROVEN" and proven["evidence"]["basis"] == "creation-revision-in-own-window"
    final = _run(cloud, tmp_path, "2026-11-17", "04:30:00")
    assert final["status"] == "OK" and not final["plan"]["blocked"]
    assert cloud.create_calls.count("2026-02|梁溪大队") == 1


def test_a_receipt_without_a_record_id_is_unknown_and_blocks_its_key(tmp_path):
    cloud = Cloud()
    cloud.no_receipt_id = {"2026-03|经开大队"}
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "UNKNOWN" and "record_id" in next(e["error"] for e in _events(tmp_path) if e["event"] == "unknown")
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert again["plan"]["blocked"][0]["key"] == "2026-03|经开大队" and cloud.create_calls.count("2026-03|经开大队") == 1


def test_a_legacy_result_line_without_a_record_id_also_blocks(tmp_path):
    ledger = tmp_path / "运行记录" / period.LEDGER_DIR
    ledger.mkdir(parents=True)
    lines = [{"at": "2026-11-17T10:00:00+08:00", "event": "intent", "key": "2026-04|新吴大队", "op": "create"},
             {"at": "2026-11-17T10:00:00+08:00", "event": "result", "key": "2026-04|新吴大队", "op": "create"}]
    (ledger / "2026-11-17.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    assert [r["kind"] for r in period.Ledger(ledger).blocked()["2026-04|新吴大队"]] == ["MISSING_RECORD_ID"]


def test_an_intent_without_any_terminal_line_blocks_and_cannot_be_recovered(tmp_path):
    ledger = tmp_path / "运行记录" / period.LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "2026-11-16.jsonl").write_text(json.dumps({"at": "2026-11-16T10:00:00+08:00", "event": "intent", "intentId": "pending1", "key": "2026-05|惠山大队", "op": "create",
                                                           "fields": {"月份": "2026-05", "大队": "惠山大队", "统计层级": "月度"}}, ensure_ascii=False) + "\n", encoding="utf-8")
    cloud = Cloud()
    result = _run(cloud, tmp_path, "2026-11-17")
    assert result["plan"]["blocked"][0]["reasons"][0]["kind"] == "PENDING" and "2026-05|惠山大队" not in cloud.create_calls
    refused = _reconcile(cloud, tmp_path, "pending1", "2026-11-17T09:00:00")
    assert refused["status"] == "RECOVERY_REFUSED" and "终态" in refused["reason"]


def test_a_corrupt_ledger_line_stops_the_run_before_anything_is_written(tmp_path):
    ledger = tmp_path / "运行记录" / period.LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "2026-11-16.jsonl").write_text('{"event": "intent", "key": "x"\n', encoding="utf-8")
    cloud = Cloud()
    with pytest.raises(period.PeriodRuntimeError, match="无法解析"):
        _run(cloud, tmp_path, "2026-11-17")
    assert not cloud.writes


def test_no_more_than_three_unknowns_per_run_and_a_key_is_never_sent_twice_across_runs(tmp_path):
    cloud = Cloud()
    cloud.fail_create = {f"2026-{m:02d}|{b}" for m in range(1, 12) for b in period.BRIGADES} | {f"2026年度|{b}" for b in (*period.BRIGADES, "全市")}
    for step in range(4):
        result = _run(cloud, tmp_path, "2026-11-17", f"0{2 + step}:00:00")
        assert result["status"] == "UNKNOWN" and len(result["apply"]["unknown"]) == period.MAX_UNKNOWN_PER_RUN
    assert len(cloud.create_calls) == 12 and len(set(cloud.create_calls)) == 12


def test_a_failed_update_is_unknown_too_and_blocks_its_row(tmp_path):
    cloud = Cloud()
    cloud.rows["recOLD000000001"] = {IDS["月份大队"]: "2026-05|梁溪大队", IDS["月份"]: "2026-05", IDS["大队"]: ["梁溪大队"]}
    cloud.fail_update = {"recOLD000000001"}
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "UNKNOWN" and first["apply"]["unknown"][0]["op"] == "update"
    cloud.fail_update = set()
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert again["plan"]["blocked"][0]["key"] == "2026-05|梁溪大队" and not [w for w in cloud.writes if w[1] == "recOLD000000001"]
    intent = next(e["intentId"] for e in _events(tmp_path) if e["event"] == "unknown")
    assert _reconcile(cloud, tmp_path, intent, "2026-11-17T04:00:00")["status"] == "NOT_APPLIED_PROVEN"
    final = _run(cloud, tmp_path, "2026-11-17", "04:30:00")
    assert final["status"] == "OK" and period.one(cloud.rows["recOLD000000001"][IDS["结期状态"]]) == "已结期"


def test_an_update_that_did_land_is_proven_applied_only_with_a_revision_in_its_window(tmp_path):
    cloud = Cloud()
    cloud.rows["recOLD000000001"] = {IDS["月份大队"]: "2026-05|梁溪大队", IDS["月份"]: "2026-05", IDS["大队"]: ["梁溪大队"]}
    cloud.fail_update = {"recOLD000000001"}
    _run(cloud, tmp_path, "2026-11-17")
    intent = next(e for e in _events(tmp_path) if e["event"] == "unknown")
    planned_fields = next(e for e in _events(tmp_path) if e["event"] == "intent" and e["intentId"] == intent["intentId"])["fields"]
    cloud._store(cloud.rows["recOLD000000001"], {IDS[n]: period._wire(n, v) for n, v in planned_fields.items()})      # 写入其实生效了
    refused = _reconcile(cloud, tmp_path, intent["intentId"], "2026-11-17T04:00:00")
    assert refused["status"] == "RECOVERY_REFUSED"                      # 没有服务端修订作证据
    intent_at = datetime.fromisoformat(next(e["at"] for e in _events(tmp_path) if e["event"] == "intent" and e["intentId"] == intent["intentId"])).timestamp()
    cloud.history_extra["recOLD000000001"] = [{"rev": 2, "createTime": intent_at + 1, "operator": "tool", "activity": "update", "fieldIds": [IDS["结期状态"]]}]
    final = _reconcile(cloud, tmp_path, intent["intentId"], "2026-11-17T04:30:00")
    assert final["status"] == "APPLIED_PROVEN", final


def test_a_row_changed_between_the_plan_and_the_write_is_not_overwritten(tmp_path):
    cloud = Cloud()
    cloud.rows["recOLD000000001"] = {IDS["月份大队"]: "2026-05|梁溪大队", IDS["月份"]: "2026-05", IDS["大队"]: ["梁溪大队"]}
    planned = period.plan(cloud.named(), date(2026, 11, 17), T("2026-11-17T02:00:00"))
    planned["creates"] = []
    cloud.rows["recOLD000000001"][IDS["统计层级"]] = ["人工改的"]            # 计划之后被人改了旧值（计划里旧值是空）
    ledger = _ledger(tmp_path)
    result = period.apply(planned, transport=cloud, base=BASE, table_id=TABLE, field_ids=IDS, ledger=ledger, now=T("2026-11-17T02:00:00"), today=date(2026, 11, 17))
    assert not cloud.writes and result["skipped"][0]["key"] == "2026-05|梁溪大队" and "旧值" in result["skipped"][0]["reason"]


def test_a_row_that_appeared_after_the_plan_is_not_created_again(tmp_path):
    cloud = Cloud()
    planned = period.plan(cloud.named(), date(2026, 11, 17), T("2026-11-17T02:00:00"))
    planned["creates"] = planned["creates"][:1]
    key = planned["creates"][0]["key"]
    cloud.rows["recLATE000000001"] = {IDS["月份大队"]: key, IDS["月份"]: planned["creates"][0]["item"]["月份"], IDS["大队"]: [planned["creates"][0]["item"]["大队"]]}
    result = period.apply(planned, transport=cloud, base=BASE, table_id=TABLE, field_ids=IDS, ledger=_ledger(tmp_path), now=T("2026-11-17T02:00:00"), today=date(2026, 11, 17))
    assert not cloud.create_calls and "已有行" in result["skipped"][0]["reason"]


def test_a_read_back_problem_is_stored_and_blocks_the_key_until_it_reads_back_clean(tmp_path):
    cloud = Cloud()
    cloud.corrupt = {"2026-02|梁溪大队": ("结期状态", ["进行中"])}          # 写进去的状态与写入值不符（应为已结期）
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "READBACK_PROBLEM" and first["apply"]["readbackProblems"][0]["key"] == "2026-02|梁溪大队"
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert [b["reasons"][0]["kind"] for b in again["plan"]["blocked"]] == ["READBACK_PROBLEM"] and again["status"] == "ATTENTION"
    intent = again["plan"]["blocked"][0]["reasons"][0]["intentId"]
    refused = _reconcile(cloud, tmp_path, intent, "2026-11-17T04:00:00")
    assert refused["status"] == "RECOVERY_REFUSED" and "读回" in refused["reason"]
    rid = _key_rows(cloud, "2026-02|梁溪大队")[0]
    cloud.rows[rid][IDS["结期状态"]] = ["已结期"]
    assert _reconcile(cloud, tmp_path, intent, "2026-11-17T04:30:00")["status"] == "READBACK_OK"
    assert not _run(cloud, tmp_path, "2026-11-17", "05:00:00")["plan"]["blocked"]


# ---------------------------------------------------------------------------------------------
# The real a31 shape: the first create of 2026年度|经开大队 was unknown, a later run created the row
# ---------------------------------------------------------------------------------------------


def _a31_ledger(tmp_path):
    ledger = tmp_path / "运行记录" / period.LEDGER_DIR
    ledger.mkdir(parents=True)
    key = "2026年度|经开大队"
    lines = [{"at": "2026-11-17T15:54:50+08:00", "event": "intent", "key": key, "op": "create", "fields": {"月份": "2026年度", "大队": "经开大队", "统计层级": "年度"}},
             {"at": "2026-11-17T15:54:50+08:00", "event": "unknown", "key": key, "op": "create", "error": "UNKNOWN: 官方 CLI 输出不是 JSON"},
             {"at": "2026-11-17T15:55:43+08:00", "event": "intent", "key": key, "op": "create", "fields": {"月份": "2026年度", "大队": "经开大队", "统计层级": "年度"}},
             {"at": "2026-11-17T15:55:43+08:00", "event": "result", "key": key, "op": "create", "recordId": "recKAIFA000001"}]
    (ledger / "2026-11-17.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return key


def _a31_cloud(first_revision: str = "2026-11-17T15:55:48"):
    cloud = Cloud()
    cloud.rows["recKAIFA000001"] = {IDS["月份大队"]: "2026年度|经开大队", IDS["月份"]: "2026年度", IDS["大队"]: ["经开大队"], IDS["统计层级"]: ["年度"]}
    cloud.created_at["recKAIFA000001"] = T(first_revision).timestamp()
    return cloud


def test_the_a31_unknown_create_stays_blocked_on_normal_runs_even_though_one_row_exists_now(tmp_path):
    key = _a31_ledger(tmp_path)
    cloud = _a31_cloud()
    result = _run(cloud, tmp_path, "2026-11-18")
    assert [b["key"] for b in result["plan"]["blocked"]] == [key] and result["plan"]["blocked"][0]["reasons"][0]["kind"] == "UNKNOWN"
    assert not [w for w in cloud.writes if w[1] == "recKAIFA000001"] and key not in cloud.create_calls
    assert result["plan"]["duplicates"] == {}                          # 当前唯一不能当作原请求终态的证明


def test_the_a31_unknown_is_proven_not_applied_because_the_only_row_was_returned_by_the_later_request(tmp_path):
    key = _a31_ledger(tmp_path)
    cloud = _a31_cloud()
    intent = next(i["intentId"] for i in period.Ledger(tmp_path / "运行记录" / period.LEDGER_DIR).state()["intents"].values() if i["status"] == "UNKNOWN")
    assert intent.startswith("legacy-")
    proven = _reconcile(cloud, tmp_path, intent, "2026-11-17T20:00:00")
    assert proven["status"] == "NOT_APPLIED_PROVEN" and proven["evidence"]["basis"] == "single-row-returned-by-later-result" and proven["evidence"]["recordId"] == "recKAIFA000001"
    events = _events(tmp_path)
    assert [e["event"] for e in events if e.get("key") == key] == ["intent", "unknown", "intent", "result"]          # 原事件保留
    assert events[-1]["event"] == "reconciled" and "humanRelease" not in json.dumps(events, ensure_ascii=False)
    assert not _run(cloud, tmp_path, "2026-11-18")["plan"]["blocked"]


def test_the_a31_proof_is_refused_when_the_row_was_created_outside_the_later_requests_window_or_a_twin_exists(tmp_path):
    _a31_ledger(tmp_path)
    cloud = _a31_cloud(first_revision="2026-11-17T09:00:00")
    intent = next(i["intentId"] for i in period.Ledger(tmp_path / "运行记录" / period.LEDGER_DIR).state()["intents"].values() if i["status"] == "UNKNOWN")
    assert "窗口" in _reconcile(cloud, tmp_path, intent, "2026-11-17T20:00:00")["reason"]
    cloud = _a31_cloud()
    cloud.rows["recTWIN00000001"] = {IDS["月份大队"]: "2026年度|经开大队（复）", IDS["月份"]: "2026年度", IDS["大队"]: ["经开大队"], IDS["统计层级"]: ["年度"]}
    refused = _reconcile(cloud, tmp_path, intent, "2026-11-17T20:30:00")
    assert refused["status"] == "RECOVERY_REFUSED" and "内容相同但键不同" in refused["reason"]


def test_a_zero_match_create_needs_the_quiet_period_and_a_clean_full_scan(tmp_path):
    cloud = Cloud()
    cloud.fail_create = {"2026-02|梁溪大队"}
    _run(cloud, tmp_path, "2026-11-17")
    intent = next(e["intentId"] for e in _events(tmp_path) if e["event"] == "unknown")
    assert _reconcile(cloud, tmp_path, intent, "2026-11-17T02:10:00")["status"] == "RECOVERY_REFUSED"
    with pytest.raises(period.PeriodError, match="静默期"):
        _reconcile(cloud, tmp_path, intent, "2026-11-17T09:00:00", min_quiet_seconds=60)
    cloud.rows["recTWIN00000002"] = {IDS["月份大队"]: "2026-02|梁溪（旧）", IDS["月份"]: "2026-02", IDS["大队"]: ["梁溪大队"], IDS["统计层级"]: ["月度"]}
    assert "内容相同但键不同" in _reconcile(cloud, tmp_path, intent, "2026-11-17T09:00:00")["reason"]
    del cloud.rows["recTWIN00000002"]
    proven = _reconcile(cloud, tmp_path, intent, "2026-11-17T09:30:00")
    assert proven["status"] == "NOT_APPLIED_PROVEN"
    assert _reconcile(cloud, tmp_path, intent, "2026-11-17T09:40:00")["status"] == "ALREADY_RECONCILED"
    cloud.fail_create = set()
    assert _run(cloud, tmp_path, "2026-11-17", "10:00:00")["status"] == "OK" and cloud.create_calls.count("2026-02|梁溪大队") == 2     # 解封后才再建，一次


def test_reconcile_never_touches_rows_and_only_appends_events(tmp_path):
    cloud = Cloud()
    cloud.fail_create = {"2026-02|梁溪大队"}
    _run(cloud, tmp_path, "2026-11-17")
    before = list(cloud.writes)
    path = next((tmp_path / "运行记录" / period.LEDGER_DIR).glob("*.jsonl"))
    old = path.read_bytes()
    intent = next(e["intentId"] for e in _events(tmp_path) if e["event"] == "unknown")
    _reconcile(cloud, tmp_path, intent, "2026-11-17T09:30:00")
    assert cloud.writes == before and path.read_bytes().startswith(old)


# ---------------------------------------------------------------------------------------------
# Annual staff day, recalculation stamp, contract
# ---------------------------------------------------------------------------------------------


def test_the_annual_staff_day_moves_forward_never_back_and_a_new_year_adds_ten_rows(tmp_path):
    cloud = Cloud()
    _run(cloud, tmp_path, "2026-11-17")
    first = {period.one(r["月份大队"]): period.day_of(r["人数截止日"]) for r in cloud.named().values() if period.one(r["月份"]) == "2026年度"}
    assert set(first.values()) == {"2026-11-25"}
    _run(cloud, tmp_path, "2026-12-26")
    after = {period.one(r["月份大队"]): period.day_of(r["人数截止日"]) for r in cloud.named().values() if period.one(r["月份"]) == "2026年度"}
    assert set(after.values()) == {"2026-12-25"}
    assert sum(1 for r in cloud.named().values() if period.one(r["月份"]) == "2027年度") == 10
    rid = next(rid for rid, r in cloud.named().items() if period.one(r["月份大队"]) == "2026年度|全市")
    cloud.rows[rid][IDS["人数截止日"]] = "2027-03-25T00:00:00.000+08:00"            # 手工改到未来：不拉回，列冲突
    planned = period.plan(cloud.named(), date(2026, 12, 27), T("2026-12-27T02:00:00"))
    assert planned["conflicts"] and planned["conflicts"][0]["field"] == "人数截止日"


def _with_numbers(cloud, key, **numbers):
    rid = next(rid for rid, r in cloud.named().items() if period.one(r["月份大队"]) == key)
    for name, value in numbers.items():
        cloud.rows[rid][IDS[name]] = str(value)
    return rid


def test_a_closed_period_whose_numbers_changed_later_gets_a_recalculation_time_and_open_ones_do_not(tmp_path):
    cloud = Cloud()
    _run(cloud, tmp_path, "2026-11-17")
    _with_numbers(cloud, "2026-10|梁溪大队", **{"每月不合格案卷数": 3})
    _with_numbers(cloud, "2026-11|梁溪大队", **{"每月不合格案卷数": 1})
    first = _run(cloud, tmp_path, "2026-11-17", "02:40:00", do_stamp=True)
    assert first["stamp"].get("stamped", []) == [] and first["stamp"].get("wouldStamp", []) == []        # 首次只建基线
    rid_closed = _with_numbers(cloud, "2026-10|梁溪大队", **{"每月不合格案卷数": 4})                       # 结期后补进一案
    _with_numbers(cloud, "2026-11|梁溪大队", **{"每月不合格案卷数": 2})                                     # 进行中的月份本来就在变
    second = _run(cloud, tmp_path, "2026-11-18", "02:40:00", do_stamp=True)
    assert second["stamp"]["stamped"] == ["2026-10|梁溪大队"]
    assert second["stamp"]["changed"]["2026-10|梁溪大队"]["每月不合格案卷数"] == {"before": "3", "after": "4"}
    assert period.one(cloud.rows[rid_closed][IDS["回算更新时间"]]) is not None
    third = _run(cloud, tmp_path, "2026-11-19", "02:40:00", do_stamp=True)
    assert third["stamp"].get("stamped", []) == []                                                         # 同一数字不重复登记
    plain = _run(cloud, tmp_path, "2026-11-19", "02:50:00", execute=False)
    assert plain["executed"] is False and plain["status"] == "OK"


def test_stamping_waits_while_rows_are_still_being_created(tmp_path):
    cloud = Cloud()
    result = _run(cloud, tmp_path, "2026-11-17", do_stamp=True)
    assert "公式数字未稳定" in result["stamp"]["skipped"] and not (tmp_path / "运行记录" / period.LEDGER_DIR / period.BASELINE_FILE).exists()


def test_the_contract_must_list_every_field_this_module_touches():
    broken = _contract()
    broken["tables"]["月度"]["fields"] = [f for f in broken["tables"]["月度"]["fields"] if f["name"] != "结期状态"]
    with pytest.raises(period.PeriodError, match="结期状态"):
        period.resolve_fields(broken)
