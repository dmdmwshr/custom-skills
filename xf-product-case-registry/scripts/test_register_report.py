from __future__ import annotations

import json

import pytest

import scripts.register_report as report


def _spec(tmp_path, **override):
    body = {"schemaVersion": report.SCHEMA, "sheets": [
        {"name": "主清单", "columns": ["序号", "单位", "说明"], "rows": [{"序号": 1, "单位": "甲 店", "说明": None}, {"序号": 2, "单位": "乙", "说明": "多行\n文本"}]},
        {"name": "历史数量", "columns": ["大队", "总数"], "rows": [{"大队": "全市", "总数": 52}]}], **override}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def test_export_saves_reopens_and_matches_every_cell_and_blank_stays_blank(tmp_path):
    result = report.export_workbook(spec_path=_spec(tmp_path), out_path=tmp_path / "o" / "清单.xlsx")
    assert result == {"path": str(tmp_path / "o" / "清单.xlsx"), "sheets": {"主清单": 2, "历史数量": 1}, "reopened": True}
    from openpyxl import load_workbook
    ws = load_workbook(tmp_path / "o" / "清单.xlsx")["主清单"]
    assert ws["C2"].value is None and ws.freeze_panes == "A2" and ws["A1"].font.bold


def test_export_never_overwrites_and_rejects_bad_specs(tmp_path):
    out = tmp_path / "清单.xlsx"
    out.write_bytes(b"keep")
    with pytest.raises(report.ReportError, match="不覆盖"):
        report.export_workbook(spec_path=_spec(tmp_path), out_path=out)
    assert out.read_bytes() == b"keep"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schemaVersion": report.SCHEMA, "sheets": [{"name": "a", "columns": ["x"], "rows": [{"y": 1}]}]}), encoding="utf-8")
    with pytest.raises(report.ReportError, match="未声明"):
        report.export_workbook(spec_path=bad, out_path=tmp_path / "n.xlsx")


def test_verification_catches_a_changed_file(tmp_path):
    spec_path = _spec(tmp_path)
    report.export_workbook(spec_path=spec_path, out_path=tmp_path / "a.xlsx")
    from openpyxl import load_workbook
    book = load_workbook(tmp_path / "a.xlsx")
    book["历史数量"]["B2"] = 53
    book.save(tmp_path / "a.xlsx")
    with pytest.raises(report.ReportError, match="内容与规格不一致"):
        report.verify_workbook(spec=json.loads(spec_path.read_text(encoding="utf-8")), path=tmp_path / "a.xlsx")


def test_strings_starting_with_equals_stay_text_and_verification_rejects_a_formula(tmp_path):
    body = {"schemaVersion": report.SCHEMA, "sheets": [{"name": "主清单", "columns": ["单位", "数量"], "rows": [{"单位": "=1+1", "数量": 3}, {"单位": "+SUM(A1)", "数量": None}]}]}
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    report.export_workbook(spec_path=spec, out_path=tmp_path / "t.xlsx")
    from openpyxl import load_workbook
    cell = load_workbook(tmp_path / "t.xlsx")["主清单"]["A2"]
    assert cell.value == "=1+1" and cell.data_type == "s"
    book = load_workbook(tmp_path / "t.xlsx")
    book["主清单"]["A2"] = "=1+1"                    # 同样的字符数据，但被存成公式
    book.save(tmp_path / "t.xlsx")
    with pytest.raises(report.ReportError, match="内容与规格不一致"):
        report.verify_workbook(spec=body, path=tmp_path / "t.xlsx")
