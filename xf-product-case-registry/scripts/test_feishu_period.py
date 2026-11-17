"""Month and year rows of 月度工作统计: calendar, idempotent creation, catch-up, closing, history rows, recalculation stamp."""

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
SELECTS = ("大队", "统计层级", "结期状态")


class Cloud:
    """In-memory 月度 table with the wire shapes of the official CLI (select -> [name], datetime -> ISO with offset)."""

    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}
        self.counter = 0
        self.fail_on: str | None = None
        self.writes: list[tuple[str, str, dict]] = []

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

    def create_record(self, base, table, fields):
        key = fields.get(IDS["月份大队"])
        if self.fail_on == key:
            raise RuntimeError("模拟断线")
        self.counter += 1
        rid = f"rec{self.counter:012d}"
        self.rows[rid] = {}
        self._store(self.rows[rid], fields)
        self.writes.append(("create", rid, dict(fields)))
        return {"data": {"record": {"record_id": rid}}}

    def upsert(self, base, table, record_id, fields):
        if self.fail_on == record_id:
            raise RuntimeError("模拟断线")
        self._store(self.rows[record_id], fields)
        self.writes.append(("update", record_id, dict(fields)))
        return {"data": {"record": {"record_id": record_id}}}

    def named(self):
        return {rid: {name: row.get(fid) for name, fid in IDS.items()} for rid, row in self.rows.items()}


def _contract():
    return {"baseId": BASE, "tables": {"月度": {"tableId": TABLE, "fields": [{"fieldId": fid, "name": name, "type": "text", "class": "admin-only"} for name, fid in IDS.items()]}}}


def _run(cloud, tmp_path, day, hour="02:00:00", **options):
    today = date.fromisoformat(day)
    return period.run(contract=_contract(), base=BASE, transport=cloud, business_root=tmp_path, today=today, now=T(f"{day}T{hour}"), execute=options.pop("execute", True), **options)


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


def test_first_run_creates_every_row_once_and_a_repeat_run_writes_nothing(tmp_path):
    cloud = Cloud()
    first = _run(cloud, tmp_path, "2026-11-17")
    assert first["status"] == "OK" and first["apply"]["readbackProblems"] == [] and len(cloud.rows) == 9 * 11 + 10
    assert first["plan"]["counts"]["creates"] == 109
    writes = len(cloud.writes)
    again = _run(cloud, tmp_path, "2026-11-17", "02:30:00")
    assert again["plan"]["counts"] == {"existing": 109, "creates": 0, "updates": 0, "conflicts": 0, "duplicates": 0} and len(cloud.writes) == writes
    keys = [period.one(r["月份大队"]) for r in cloud.named().values()]
    assert len(keys) == len(set(keys)) and "2026-11|梁溪大队" in keys and "2026年度|全市" in keys


def test_a_missed_night_is_caught_up_by_the_next_run_and_closing_happens_on_the_26th(tmp_path):
    cloud = Cloud()
    _run(cloud, tmp_path, "2026-11-17")
    closed = {period.one(r["月份大队"]): period.one(r["结期状态"]) for r in cloud.named().values()}
    assert closed["2026-10|梁溪大队"] == "已结期" and closed["2026-11|梁溪大队"] == "进行中"      # 11-25 才结前月；10 月早已结
    # 11-26 夜间没有运行，11-27 才运行：下一个统计月行补建，2026-11 结期
    late = _run(cloud, tmp_path, "2026-11-27")
    assert late["plan"]["counts"]["creates"] == 9 and late["status"] == "OK"
    state = {period.one(r["月份大队"]): (period.one(r["结期状态"]), period.one(r["结期时间"])) for r in cloud.named().values()}
    assert state["2026-12|梁溪大队"][0] == "进行中" and state["2026-11|梁溪大队"][0] == "已结期" and state["2026-11|梁溪大队"][1]
    assert period.day_of(next(r for r in cloud.named().values() if period.one(r["月份大队"]) == "2026年度|全市")["人数截止日"]) == "2026-12-25"
    on_the_day = Cloud()
    _run(on_the_day, tmp_path, "2026-11-25")
    assert {period.one(r["结期状态"]) for r in on_the_day.named().values() if period.one(r["月份"]) == "2026-11"} == {"进行中"}   # 25 日当天仍进行中


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
    assert planned["conflicts"] == [{"recordId": "recOLD000000004", "key": "2026-06|经开大队", "field": "统计起日", "current": "2026-05-25", "expected": "2026-05-26"}]
    assert planned["status"] == "ATTENTION"
    blocked = _run(cloud, tmp_path, "2026-11-17")
    assert blocked["status"] == "ATTENTION" and not cloud.writes                  # 有冲突整批不写
    cloud.rows["recOLD000000004"][IDS["统计起日"]] = "2026-05-26T00:00:00.000+08:00"
    ok = _run(cloud, tmp_path, "2026-11-17")
    assert ok["status"] == "OK" and period.one(cloud.rows["recOLD000000002"][IDS["统计层级"]]) == "历史待核实"
    assert period.one(cloud.rows["recOLD000000001"][IDS["统计层级"]]) == "月度"


def test_a_duplicate_key_or_an_unknown_row_blocks_every_write(tmp_path):
    cloud = Cloud()
    for rid in ("recDUP000000001", "recDUP000000002"):
        cloud.rows[rid] = {IDS["月份大队"]: "2026-03|梁溪大队", IDS["月份"]: "2026-03", IDS["大队"]: ["梁溪大队"]}
    cloud.rows["recUNK000000001"] = {IDS["月份大队"]: "随手记", IDS["月份"]: "?", IDS["大队"]: ["梁溪大队"]}
    result = _run(cloud, tmp_path, "2026-11-17")
    assert result["status"] == "ATTENTION" and not cloud.writes
    assert result["plan"]["duplicates"] == {"2026-03|梁溪大队": ["recDUP000000001", "recDUP000000002"]} and result["plan"]["unrecognized"][0]["key"] == "随手记"


def test_an_unknown_create_stops_the_run_and_the_next_run_reads_the_key_before_deciding(tmp_path):
    cloud = Cloud()
    cloud.fail_on = "2026-02|梁溪大队"
    with pytest.raises(period.PeriodRuntimeError, match="UNKNOWN"):
        _run(cloud, tmp_path, "2026-11-17")
    ledger = (tmp_path / "运行记录" / period.LEDGER_DIR / "2026-11-17.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in ledger]
    assert events[-1]["event"] == "unknown" and events[-1]["key"] == "2026-02|梁溪大队"
    created = len(cloud.rows)
    cloud.fail_on = None
    # 未知那一行其实已经在云端（模拟：回执丢了但写入成功）
    cloud.counter += 1
    cloud.rows["recLOST000000001"] = {IDS["月份大队"]: "2026-02|梁溪大队", IDS["月份"]: "2026-02", IDS["大队"]: ["梁溪大队"]}
    again = _run(cloud, tmp_path, "2026-11-17", "03:00:00")
    assert again["status"] == "OK"
    keys = [period.one(r["月份大队"]) for r in cloud.named().values()]
    assert keys.count("2026-02|梁溪大队") == 1 and len(cloud.rows) > created


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
