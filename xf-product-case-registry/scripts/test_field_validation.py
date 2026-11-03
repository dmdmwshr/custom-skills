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


def test_rounds_extend_the_stable_key_and_the_latest_round_decides() -> None:
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
    # A later passing round means the administrative-case rule no longer holds.
    flagged = dict(first, case_type="行案")
    assert any("只有复查不合格" in e for e in fv.validate_cases([row(), flagged, second])["errors"])


def test_recheck_requires_failed_initial_and_own_date_source() -> None:
    passed = fv.validate_cases([row(qualified="合格"), row("复查")])
    assert any("只有初查不合格" in e for e in passed["errors"])
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


def test_administrative_case_only_after_failed_recheck_unless_manual() -> None:
    bad = fv.validate_cases([row(case_type="行案")])
    assert any("只有复查不合格" in e for e in bad["errors"])
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
