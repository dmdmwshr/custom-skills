"""Independent fifteen-indicator recount: date edges, de-duplication, unqualified classes, sampling batches, roster dates, chains."""

from __future__ import annotations

from datetime import date, datetime

import pytest

import scripts.feishu_period as period
import scripts.feishu_stats as stats

SHANGHAI = period.SHANGHAI


def case(no, day, brigade="梁溪大队", result="合格", **flags):
    row = {"项目编号": no, "单位名称": "单位" + no[-3:], "初查日期": f"{day}T00:00:00.000+08:00" if day else None, "初查结果": [result] if result else None,
           "所属大队（受控）": brigade, "是否行政处罚案": ["否"]}
    for name in ("是否微型消防站", "是否消防快速处置队", "是否刑事案件", "是否光伏锂电等相关企业", "是否消防处置力量配备", "是否网售", "是否维修后灭火器", "是否加油加气站"):
        row[name] = ["否"]
    row.update({k: [v] for k, v in flags.items()})
    return row


def check(key, stage, day, method=None, brigade="梁溪大队", serial=None):
    return {"检查键": key, "阶段": [stage], "检查日期": f"{day}T00:00:00.000+08:00" if day else None, "检查方式": [method] if method else None,
            "所属大队(案卷)": brigade, "检查序次": serial}


def product(key, method, result, link=None):
    return {"产品键": key, "检查方式": [method] if method else None, "检查结果": [result], "对应初查产品键": link}


def expected(cases, checks=(), products=(), roster=(), pending=(), start="2026-03-26", end="2026-04-25", brigade="梁溪大队", staff=None):
    built = stats.build_cases({str(i): c for i, c in enumerate(cases)}, {str(i): c for i, c in enumerate(checks)}, {str(i): p for i, p in enumerate(products)})
    rechecks = stats.build_rechecks({str(i): c for i, c in enumerate(checks)})
    people = stats.build_roster({str(i): r for i, r in enumerate(roster)})
    return stats.expected_row(built, rechecks, people, set(pending), start=start, end=end, brigade=brigade, staff_day=staff or end)


A, B, C = "32002201C202600001", "32002201C202600002", "32002201C202600003"


def test_the_25th_and_26th_fall_in_different_periods_and_a_year_row_spans_the_year_edge():
    cases = [case(A, "2026-03-25"), case(B, "2026-03-26"), case(C, "2025-12-31")]
    march = expected(cases, start="2026-02-26", end="2026-03-25")
    april = expected(cases, start="2026-03-26", end="2026-04-25")
    january = expected(cases, start="2025-12-26", end="2026-01-25")
    assert (march["每月监督检查次数（初查）"], april["每月监督检查次数（初查）"], january["每月监督检查次数（初查）"]) == (1, 1, 1)
    year = expected(cases, start="2025-12-26", end="2026-12-25", brigade="全市")
    assert year["每月监督检查次数（初查）"] == 3


def test_a_late_result_counts_in_the_month_of_the_first_inspection_not_the_month_it_arrived():
    june = dict(start="2026-05-26", end="2026-06-25")
    july = dict(start="2026-06-26", end="2026-07-25")
    pending = [case(A, "2026-06-10", result="待结果")]
    assert expected(pending, **june)["每月不合格案卷数"] == 0 and expected(pending, **june)["初查结果待出案卷数"] == 1
    decided = [case(A, "2026-06-10", result="不合格")]
    assert expected(decided, **june)["每月不合格案卷数"] == 1 and expected(decided, **july)["每月不合格案卷数"] == 0


def test_every_recheck_round_counts_on_its_own_date_and_a_repeated_event_counts_once():
    cases = [case(A, "2026-03-10", result="不合格")]
    checks = [check(A + "|初查", "初查", "2026-03-10", "现场判定"), check(A + "|复查", "复查", "2026-04-20"), check(A + "|复查2", "复查", "2026-05-20", serial=2),
              check(A + "|复查2", "复查", "2026-05-20", serial=2)]                      # 同一轮复查被同步了两次
    april = expected(cases, checks, start="2026-03-26", end="2026-04-25")
    may = expected(cases, checks, start="2026-04-26", end="2026-05-25")
    march = expected(cases, checks, start="2026-02-26", end="2026-03-25")
    assert (april["每月监督检查次数（含复查）"], may["每月监督检查次数（含复查）"], march["每月监督检查次数（含复查）"]) == (1, 1, 1)   # 3 月只有初查 1 次；4 月、5 月各只有自己那一轮复查
    assert (april["每月监督检查次数（初查）"], may["每月监督检查次数（初查）"]) == (0, 0)


def test_mixed_cases_go_to_sampling_and_missing_method_is_listed_apart_so_item_four_balances():
    cases = [case(A, "2026-03-30", result="不合格"), case(B, "2026-03-30", result="不合格"), case(C, "2026-03-30", result="不合格"), case("32002201C202600004", "2026-03-30", result="不合格")]
    checks = [check(A + "|初查", "初查", "2026-03-30", "现场判定"), check(B + "|初查", "初查", "2026-03-30", "抽样送检、现场判定"),
              check(C + "|初查", "初查", "2026-03-30", "抽样送检")]                    # 第四案没有任何检查行
    row = expected(cases, checks)
    assert (row["每月不合格案卷数"], row["现场判定不合格案卷数"], row["抽样送检不合格案卷数"], row["方式缺项不合格案卷数"]) == (4, 1, 2, 1)
    assert row["每月不合格案卷数"] == row["现场判定不合格案卷数"] + row["抽样送检不合格案卷数"] + row["方式缺项不合格案卷数"]


def test_one_case_with_two_sampled_products_is_two_batches_and_a_resample_does_not_add_one():
    cases = [case(A, "2026-03-30", result="不合格"), case(B, "2026-03-30", result="合格")]
    checks = [check(A + "|初查", "初查", "2026-03-30", "抽样送检"), check(B + "|初查", "初查", "2026-03-30", "抽样送检"), check(A + "|复查", "复查", "2026-04-10", "抽样送检")]
    products = [product(A + "|初查|1", "抽样送检", "不合格"), product(A + "|初查|2", "抽样送检", "合格"), product(A + "|初查|3", "现场判定", "合格"),
                product(A + "|复查|1", "抽样送检", "合格", link=A + "|初查|1")]            # 复查产品不是新批次；B 案没有送检产品行
    row = expected(cases, checks, products)
    assert row["抽样送检批次数"] == 2 and row["送检批次缺项案卷数"] == 1


def test_roster_counts_people_valid_on_the_staff_day_and_a_brigade_with_no_roster_stays_empty():
    people = [{"人员ID": f"P{i}", "大队": ["梁溪大队"], "有效起日": "2026-01-01T00:00:00.000+08:00", "有效止日": "2026-06-30T00:00:00.000+08:00" if i == 0 else None} for i in range(3)]
    people.append({"人员ID": "Q1", "大队": ["江阴大队"], "有效起日": "2026-07-01T00:00:00.000+08:00", "有效止日": None})
    june = expected([], roster=people, start="2026-05-26", end="2026-06-25")
    july = expected([], roster=people, start="2026-06-26", end="2026-07-25")
    assert (june["大队执法监督员人数"], july["大队执法监督员人数"]) == (3, 2)
    water = expected([], roster=people, pending=["水上大队"], brigade="水上大队")
    assert water["大队执法监督员人数"] is None
    city = expected([], roster=people, pending=["水上大队"], brigade="全市", start="2026-06-26", end="2026-07-25")
    assert city["大队执法监督员人数"] == 3                                               # 2 + 江阴 1，水上不含


def test_administrative_is_yes_only_when_the_same_product_chain_is_unqualified_at_first_and_at_the_latest_valid_recheck():
    cases = [case(A, "2026-03-10", result="不合格"), case(B, "2026-03-10", result="不合格"), case(C, "2026-03-10", result="不合格")]
    checks = [check(A + "|初查", "初查", "2026-03-10", "现场判定"), check(A + "|复查", "复查", "2026-04-10"), check(A + "|复查2", "复查", "2026-05-10", serial=2),
              check(B + "|初查", "初查", "2026-03-10", "现场判定"), check(B + "|复查", "复查", "2026-04-10"),
              check(C + "|初查", "初查", "2026-03-10", "现场判定"), check(C + "|复查", "复查", "2026-04-10")]
    products = [product(A + "|初查|1", "现场判定", "不合格"), product(A + "|复查|1", "现场判定", "不合格", link=A + "|初查|1"), product(A + "|复查2|1", "现场判定", "合格", link=A + "|初查|1"),
                product(B + "|初查|1", "现场判定", "不合格"), product(B + "|复查|1", "现场判定", "不合格", link=B + "|初查|1"),
                product(C + "|初查|1", "现场判定", "不合格"), product(C + "|复查|1", "现场判定", "不合格", link=None)]       # C：复查产品没有已核实的对应关系
    built = stats.build_cases({"0": cases[0], "1": cases[1], "2": cases[2]}, {str(i): c for i, c in enumerate(checks)}, {str(i): p for i, p in enumerate(products)})
    assert (built[A]["admin"], built[B]["admin"], built[C]["admin"]) == ("否", "是", "否")      # A：最新有效复查（第二轮）已合格；C：不凭名称位置推断


def test_compare_flags_a_formula_value_that_differs_and_keeps_relations_and_gap_lists():
    cases = {"0": case(A, "2026-03-30", result="不合格"), "1": case(B, None, result="待核实", **{"所属大队（受控）": "梁溪大队"})}
    checks = {"0": check(A + "|初查", "初查", "2026-03-30", "现场判定")}
    built = stats.build_cases(cases, checks, {})
    rechecks = stats.build_rechecks(checks)
    expected_values = stats.expected_row(built, rechecks, [], set(), start="2026-03-26", end="2026-04-25", brigade="梁溪大队", staff_day="2026-04-25")
    row = {"月份大队": "2026-04|梁溪大队", "月份": "2026-04", "大队": ["梁溪大队"], "统计层级": ["月度"], "统计起日": "2026-03-26T00:00:00.000+08:00",
           "统计止日": "2026-04-25T00:00:00.000+08:00", "人数截止日": "2026-04-25T00:00:00.000+08:00", "结期状态": ["已结期"]}
    for name, value in expected_values.items():
        row[name] = "" if value is None else str(value)
    ok = stats.compare({"rec1": row}, built, rechecks, [], set())
    assert ok["diffs"] == [] and ok["rowsCompared"] == 1 and ok["relations"] == []
    row["每月不合格案卷数"] = "2"
    bad = stats.compare({"rec1": row}, built, rechecks, [], set())
    assert [d["indicator"] for d in bad["diffs"]] == ["每月不合格案卷数"] and bad["diffs"][0]["independent"] == 1
    gaps = stats.gap_lists(built)
    assert [e["projectNo"] for e in gaps["缺初查日期"]] == [B] and [e["projectNo"] for e in gaps["初查结果待出"]] == [B]


def test_year_row_must_equal_the_sum_of_its_twelve_months_and_the_city_row_the_nine_brigades():
    base = {n: 0 for n in stats.ADDITIVE}
    by_key = {}
    for month in range(1, 13):
        by_key[f"2026-{month:02d}|梁溪大队"] = {"kind": "month", "month": f"2026-{month:02d}", "brigade": "梁溪大队", "expected": {**base, "每月监督检查次数（初查）": 1}}
    by_key["2026年度|梁溪大队"] = {"kind": "year", "month": "2026年度", "brigade": "梁溪大队", "expected": {**base, "每月监督检查次数（初查）": 11}}
    broken = stats._relations(by_key)
    assert any(item["relation"] == "年度=十二个统计月合计" and item["year"] == 11 and item["months"] == 12 for item in broken)


def test_the_recount_reader_needs_the_new_columns_in_the_contract_and_the_old_contract_keeps_the_legacy_recount_only():
    assert stats.available({"tables": {"月度": {"fields": [{"name": "月份"}]}}}) is False


def test_a_project_number_that_is_not_eighteen_characters_still_ties_its_checks_and_products_to_the_case():
    short = "SYT20260001"
    cases = [case(short, "2026-03-30", result="不合格")]
    checks = [check(short + "|初查", "初查", "2026-03-30", "现场判定")]
    row = expected(cases, checks)
    assert (row["现场判定不合格案卷数"], row["方式缺项不合格案卷数"]) == (1, 0)


def _person(person_id, brigade, start, end):
    return {"人员ID": person_id, "大队": [brigade], "有效起日": f"{start}T00:00:00.000+08:00", "有效止日": f"{end}T00:00:00.000+08:00" if end else None}


def test_a_person_who_moves_between_brigades_counts_once_per_day_and_the_city_total_does_not_change():
    people = [_person("P0", "梁溪大队", "2026-01-01", "2026-12-31"), _person("P1", "梁溪大队", "2026-01-01", "2026-06-25"), _person("P1", "锡山大队", "2026-06-26", "2026-12-31")]
    june = dict(start="2026-05-26", end="2026-06-25"), dict(start="2026-06-26", end="2026-07-25")
    first, second = ([expected([], roster=people, brigade=b, **window)["大队执法监督员人数"] for b in ("梁溪大队", "锡山大队", "全市")] for window in june)
    assert first == [2, 0, 2] and second == [1, 1, 2]


def test_open_ended_assignment_counts_and_overlapping_assignments_of_one_id_are_reported():
    open_ended = [_person("P2", "锡山大队", "2026-01-01", None)]
    assert expected([], roster=open_ended, brigade="锡山大队", start="2026-06-26", end="2026-07-25")["大队执法监督员人数"] == 1
    roster = stats.build_roster({"a": _person("P3", "梁溪大队", "2026-01-01", "2026-06-26"), "b": _person("P3", "锡山大队", "2026-06-26", "2026-12-31"),
                                 "c": _person("P4", "梁溪大队", "2026-01-01", "2026-06-25"), "d": _person("P4", "锡山大队", "2026-06-26", "2026-12-31")})
    assert [o["id"] for o in stats.roster_overlaps(roster)] == ["P3"]
    result = stats.compare({}, {}, [], roster, set())
    assert [r["relation"] for r in result["relations"]] == ["同一人员ID的任职有效期不得重叠"] and result["relations"][0]["id"] == "P3"
