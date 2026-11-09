from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import scripts.field_validation as fv
import scripts.registry_cli as cli

PROJECT = "32002207C202600033"


def row(phase: str = "初查", **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "project_no": PROJECT,
        "inspection_phase": phase,
        "unit_name": "合成单位",
        "unit_address": "地址",
        "nominal_producers": ["甲厂", "乙厂"],
        "case_handler": "张三",
        "inspector": "李四",
        "inspection_date": "2026-10-01",
        "products": ["产品A", "产品B"],
        "station_or_team": "否",
        "method": "抽样送检",
        "qualified": "不合格",
        "case_type": "否",
        "online_sale": "",
        "source_files": ["1.png"],
        "file_roles": {"1.png": "案卷清单"},
        "missing_fields": ["online_sale"],
        "notes": "",
    }
    value.update(changes)
    return value


def test_valid_pair_does_not_modify_input_and_reports_unknown_tristate() -> None:
    cases = [row(), row("复查", inspection_date="2026-10-20", inspection_date_source="1.png")]
    before = copy.deepcopy(cases)
    report = fv.validate_cases(cases)
    assert report["status"] == "ok" and report["inputModified"] is False
    assert cases == before
    unknown = {(u["phase"], u["field"]) for u in report["unknownFields"]}
    assert ("初查", "online_sale") in unknown  # blank stays unknown, never defaulted to 否


def test_missing_phase_defaults_locally_without_writing_back() -> None:
    case = row()
    del case["inspection_phase"]
    original = copy.deepcopy(case)
    assert fv.validate_cases([case])["status"] == "ok"
    assert case == original and "inspection_phase" not in case


def test_duplicate_stable_key_and_three_rows_are_errors() -> None:
    report = fv.validate_cases([row(), row()])
    assert any("重复" in e for e in report["errors"])
    report = fv.validate_cases([row(), row("复查"), row("复查")])
    assert any("最多只能有" in e for e in report["errors"])


def test_rounds_extend_the_stable_key_without_deciding_case_type() -> None:
    first = row(
        "复查", inspection_date="2026-10-20", inspection_date_source="1.png", qualified="不合格"
    )
    second = row(
        "复查",
        inspection_round=2,
        inspection_date="2026-11-02",
        inspection_date_source="1.png",
        qualified="合格",
        case_type="否",
    )
    assert fv.validate_cases([row(), first, second])["status"] == "ok"
    duplicate = fv.validate_cases([row(), first, dict(first)])
    assert any("重复" in e for e in duplicate["errors"])
    bad = fv.validate_cases([row(), row("复查", inspection_round=0)])
    assert any("inspection_round" in e for e in bad["errors"])
    # A later passing round does not erase an independently evidenced case type.
    flagged = dict(first, case_type="行案", case_type_source="1.png")
    assert fv.validate_cases([row(), flagged, second])["status"] == "ok"


def test_recheck_requires_source_phase_when_initial_is_pending_and_own_date_source() -> None:
    passed = fv.validate_cases([row(qualified="合格"), row("复查")])
    assert any("inspection_phase_source" in e for e in passed["errors"])
    evidenced = [row(qualified=""), row("复查", inspection_date="2026-10-20",
        inspection_date_source="1.png", inspection_phase_source="1.png")]
    original = copy.deepcopy(evidenced)
    assert fv.validate_cases(evidenced)["status"] == "ok"
    assert evidenced == original
    copied = fv.validate_cases([row(), row("复查", inspection_date="2026-10-01")])
    assert any("疑似复制初查日期" in e for e in copied["errors"])
    unsourced = fv.validate_cases([row(), row("复查", inspection_date="2026-10-20")])
    assert unsourced["status"] == "ok" and any("来源未证明" in w for w in unsourced["warnings"])
    wrong = fv.validate_cases(
        [row(), row("复查", inspection_date="2026-10-20", inspection_date_source="9.png")]
    )
    assert any("不在 source_files" in e for e in wrong["errors"])
    blank = fv.validate_cases([row(), row("复查", inspection_date="")])
    assert blank["status"] == "ok"
    assert {"phase": "复查", "field": "inspection_date", "projectNo": PROJECT} in blank[
        "unknownFields"
    ]


def test_administrative_case_has_independent_source_and_preserves_legacy_manual_values() -> None:
    bad = fv.validate_cases([row(case_type="行案")])
    assert bad["status"] == "ok"
    assert any("缺少独立来源" in w for w in bad["warnings"])
    assert fv.validate_cases([row(case_type="行案", case_type_source="1.png")])["status"] == "ok"
    assert fv.validate_cases([row(case_type="行案", case_type_source="missing.pdf")])["status"] == "errors_found"
    good = fv.validate_cases(
        [
            row(case_type="行案"),
            row(
                "复查",
                qualified="不合格",
                case_type="行案",
                inspection_date="2026-10-20",
                inspection_date_source="1.png",
            ),
        ]
    )
    assert good["status"] == "ok"
    manual = fv.validate_cases(
        [row(case_type="行案", manual_fields=["case_type"], manual_note="用户裁决")]
    )
    assert manual["status"] == "ok"
    assert manual["protectedManualFields"] == [
        {"projectNo": PROJECT, "phase": "初查", "field": "case_type"}
    ]
    assert any("人工裁决" in w for w in manual["warnings"])


def test_product_producer_mismatch_and_bad_enums_are_reported() -> None:
    report = fv.validate_cases([row(nominal_producers=["仅一家"], online_sale="也许")])
    assert any("数量不一致" in w for w in report["warnings"])
    assert any("online_sale 值不在允许范围" in e for e in report["errors"])


def test_source_files_checked_only_when_image_dir_given(tmp_path: Path) -> None:
    assert fv.validate_cases([row()])["status"] == "ok"
    missing = fv.validate_cases([row()], tmp_path)
    assert any("文件不存在" in e for e in missing["errors"])
    (tmp_path / "1.png").write_bytes(b"x")
    assert fv.validate_cases([row()], tmp_path)["status"] == "ok"
    assert [p.name for p in tmp_path.iterdir()] == ["1.png"]  # never renamed or deleted


def test_cli_validate_is_readonly_and_fails_on_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "extractions.json"
    path.write_text(json.dumps({"cases": [row()]}, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    assert cli.main(["fields", "validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    path.write_text(json.dumps([row(), row()], ensure_ascii=False), encoding="utf-8")
    assert cli.main(["fields", "validate", str(path)]) == 2
    assert path.read_bytes() != before and "字段校验发现错误" in capsys.readouterr().err


def test_unit_name_and_address_must_not_be_mixed_up() -> None:
    same = row(unit_name="无锡市梁溪区解放东路851号", unit_address="无锡市梁溪区解放东路851号")
    assert any("串栏" in e for e in fv.validate_cases([same])["errors"])
    contained = row(unit_name="某酒店 无锡市解放东路851号", unit_address="无锡市解放东路851号")
    assert any("串栏" in e for e in fv.validate_cases([contained])["errors"])
    assert not any("串栏" in e for e in fv.validate_cases([row()])["errors"])


def test_initial_date_prefers_electronic_record_and_records_acceptance_fallback() -> None:
    both = fv.resolve_initial_date("2026-11-04", "2026-11-05")
    assert both["value"] == "2026-11-04" and both["fallback"] is False and both["conflict"] is True
    assert both["externalSource"] == "监督系统" and both["internalSource"] == "电子监督检查记录"
    fallback = fv.resolve_initial_date("", "2026-11-05")
    assert fallback["value"] == "2026-11-05" and fallback["fallback"] is True
    assert fallback["externalSource"] == "监督系统" and "后备" in fallback["internalSource"]
    unreadable = fv.resolve_initial_date("2026年", "2026-11-05")
    assert unreadable["fallback"] is True and unreadable["electronicRaw"] == "2026年"
    assert fv.resolve_initial_date("", "")["value"] is None


@pytest.mark.parametrize(
    ("notice", "due"),
    [
        ("2026-01-31", "2026-02-28"),
        ("2028-01-31", "2028-02-29"),
        ("2026-03-15", "2026-04-15"),
        ("2026-12-31", "2027-01-31"),
        ("2026-05-31", "2026-06-30"),
    ],
)
def test_review_due_date_is_shanghai_natural_month_with_month_end_clamp(notice: str, due: str) -> None:
    assert fv.review_due_date(notice).isoformat() == due
    assert fv.review_due_date(None) is None and fv.review_due_date("2026-02-30") is None


def test_recheck_status_branches() -> None:
    st = fv.recheck_status
    assert st("待结果")["state"] == "初查结果待定" and not st("待结果")["closed"]
    assert st("合格")["state"] == "初查合格无需复查" and st("合格")["closed"]
    assert st("不合格", recheck_result="不合格")["state"] == "复查有有效结果"
    started = st("不合格", notice_date="2026-01-01", recheck_started=True, observed_on="2026-12-31", observed_not_started=True)
    assert started["state"] == "已开展复查待结果" and not started["closed"]  # 一个月规则不改写已开展
    no_notice = st("不合格", recheck_started=False, observed_on="2026-12-31", observed_not_started=True)
    assert no_notice["state"] == "待核实" and "dueDate" not in no_notice  # 缺下发日不倒计时
    unknown_started = st("不合格", notice_date="2026-01-01", recheck_started=None)
    assert unknown_started["state"] == "待核实"  # 缺复查文书不等于未开展
    before = st("不合格", notice_date="2026-01-31", recheck_started=False, observed_on="2026-02-27", observed_not_started=True)
    assert before["state"] == "观察期内未开展" and before["dueDate"] == "2026-02-28"
    on_due = st("不合格", notice_date="2026-01-31", recheck_started=False, observed_on="2026-02-28", observed_not_started=True)
    assert on_due["state"] == "到期确认未开展" and on_due["closed"]
    no_observation = st("不合格", notice_date="2026-01-31", recheck_started=False)
    assert no_observation["state"] == "待核实"  # 今天已过期不能倒推无复查


def test_administrative_class_uses_same_product_chain_only() -> None:
    yes_chain = {"linked": True, "initial_result": "不合格", "recheck_result": "不合格", "recheck_state": "复查有有效结果"}
    closed_no = {"linked": True, "initial_result": "不合格", "recheck_result": None, "recheck_state": "到期确认未开展"}
    passed = {"linked": True, "initial_result": "合格", "recheck_result": None, "recheck_state": "初查合格无需复查"}
    pending = {"linked": True, "initial_result": "不合格", "recheck_result": None, "recheck_state": "观察期内未开展"}
    unlinked = {"linked": False, "recheck_result": "不合格"}
    assert fv.administrative_class([yes_chain, pending]) == "是"
    assert fv.administrative_class([closed_no, passed]) == "否"
    assert fv.administrative_class([pending]) == "待核实"
    assert fv.administrative_class([]) == "待核实"
    # 产品A初查不合格(无复查) + 产品B复查不合格(未连接到A)：不得拼成“是”
    cross = fv.product_chains(
        [{"clientRef": "a", "result": "不合格"}, {"clientRef": "b", "result": "合格"}],
        [{"clientRef": "r", "result": "不合格"}],
    )
    assert [c["linked"] for c in cross] == [False, True, True]
    rows = [
        {"linked": c["linked"], "initial_result": c.get("initialResult"), "recheck_result": None, "recheck_state": "观察期内未开展"}
        for c in cross
    ]
    assert fv.administrative_class(rows) == "待核实"
    linked = fv.product_chains(
        [{"clientRef": "a", "result": "不合格"}], [{"clientRef": "r", "result": "不合格", "initialProductRef": "a"}]
    )
    assert linked == [{"initialRef": "a", "linked": True, "initialResult": "不合格", "recheckRefs": ["r"], "recheckResults": ["不合格"]}]
    # 引用不存在的初查产品：保持未连接
    assert fv.product_chains([{"clientRef": "a"}], [{"clientRef": "r", "initialProductRef": "zzz"}])[0]["linked"] is False


def test_stable_case_needs_final_initial_terminal_recheck_and_no_new_evidence() -> None:
    kw = {"identity_complete": True, "initial_result": "不合格", "recheck_state": "到期确认未开展"}
    assert fv.is_stable_case(**kw)
    assert not fv.is_stable_case(**{**kw, "has_new_evidence": True})
    assert not fv.is_stable_case(**{**kw, "initial_result": "待结果"})
    assert not fv.is_stable_case(**{**kw, "recheck_state": "观察期内未开展"})
    assert not fv.is_stable_case(**{**kw, "identity_complete": False})


def test_r35_d3_manual_and_nonapplicable_blanks_are_not_source_unknowns() -> None:
    manual = row(online_sale="", station_or_team="", manual_fields=["online_sale", "station_or_team"], manual_note="大队填报")
    report = fv.validate_cases([manual])
    assert report["status"] == "ok"
    assert not [u for u in report["unknownFields"] if u["field"] in {"online_sale", "station_or_team"}]
    assert {m["field"] for m in report["manualBlankFields"]} == {"online_sale", "station_or_team"}
    assert len(report["protectedManualFields"]) == 2
    na = fv.validate_cases([row(online_sale="", not_applicable_fields=["online_sale"])])
    assert {m["field"] for m in na["nonApplicableFields"]} == {"online_sale"} and not [u for u in na["unknownFields"] if u["field"] == "online_sale"]
    plain = fv.validate_cases([row(online_sale="")])  # a blank nobody classified is still a source gap
    assert [u["field"] for u in plain["unknownFields"] if u["field"] == "online_sale"] == ["online_sale"]


def test_r35_d3_pending_report_is_accepted_but_unsupported_values_still_fail() -> None:
    ok = fv.validate_cases([row(qualified="待结果", method="抽样送检")])
    assert ok["status"] == "ok" and ok["pendingResultFields"] == [{"projectNo": PROJECT, "phase": "初查", "field": "qualified", "label": "待结果"}]
    assert fv.validate_cases([row(qualified="待核实")])["status"] == "ok"
    assert not [u for u in ok["unknownFields"] if u["field"] == "qualified"]
    bad = fv.validate_cases([row(qualified="差不多合格")])
    assert bad["status"] == "errors_found" and any("qualified" in e for e in bad["errors"])


def test_r35_d3_lifecycle_rules_run_in_the_normal_validation_report() -> None:
    cases = [row(notice_date="2026-10-31"), row("复查", inspection_date="2026-11-20", inspection_date_source="1.png", qualified="不合格", products=["产品A"])]
    cases[0]["products"] = ["产品A"]
    life = fv.validate_cases(cases)["lifecycle"]
    assert life == [{"projectNo": PROJECT, "recheckState": "复查有有效结果", "recheckReason": "已有复查有效结果", "administrativeClass": "是"}]
    # two products per stage cannot be joined by name or position: stays 待核实
    multi = fv.validate_cases([row(), row("复查", inspection_date="2026-11-20", inspection_date_source="1.png", qualified="不合格")])["lifecycle"][0]
    assert multi["administrativeClass"] == "待核实"
    # initial pending result never becomes a 否; missing notice date does not count down
    pending = fv.validate_cases([row(qualified="待结果")])["lifecycle"][0]
    assert pending["recheckState"] == "初查结果待定" and pending["administrativeClass"] == "待核实"
    undated = fv.validate_cases([row(products=["产品A"])])["lifecycle"][0]
    assert undated["recheckState"] == "待核实" and undated["administrativeClass"] == "待核实"


def test_r35_d3_normal_cli_exit_code_accepts_pending_and_keeps_input_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "extractions.json"
    path.write_text(json.dumps({"cases": [row(qualified="待结果", online_sale="", manual_fields=["online_sale"], manual_note="大队填报")]}, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    assert cli.main(["fields", "validate", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before and report["pendingResultFields"] and not [u for u in report["unknownFields"] if u["field"] == "online_sale"]
