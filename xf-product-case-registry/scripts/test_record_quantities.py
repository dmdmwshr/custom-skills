"""Offline tests for product quantities read from the initial inspection record (fake table reader, temp business root)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.feishu_source_sync as sync
import scripts.record_quantities as q

HEADER = ["产品名称及\n规格型号", "标称生产者", "产品所在部位", "检查\n基数", "检查\n数量", "市场准入检查情况", "产品质量现场检查情况"]


def _case(root: Path, project: str, products: list[dict], stamp: str = "20261110T010101Z", record: bytes = b"%PDF record") -> Path:
    folder = root / "工作区" / "历史工作区" / f"{project}-{stamp}-0123456789ab"
    (folder / "source").mkdir(parents=True)
    pdf = folder / "source" / "消防产品监督检查记录.pdf"
    pdf.write_bytes(record + project.encode() + stamp.encode())
    manifest = {"initialInspection": {"products": products},
                "files": [{"clientRef": "initial-record", "relativePath": "改过的名字.pdf", "sha256": "sha256:" + q.file_sha(pdf)}],
                "documentSlots": [{"slotCode": "INITIAL_INSPECTION_RECORD", "versions": [{"kind": "ELECTRONIC", "fileRef": "initial-record"}]}]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return folder


def _row(name, base, checked):
    return [name, "某厂", "一层", base, checked, "符合", "未发现不合格现象"]


P1, P2, P3, P4 = "32002201C202600001", "32002201C202600002", "32002201C202600003", "32002201C202600004"


@pytest.fixture()
def root(tmp_path):
    _case(tmp_path, P1, [{"name": "手提式干粉灭火器", "method": "ONSITE"}, {"name": "消防应急照明灯具", "method": "ONSITE"}], stamp="20261101T010101Z")
    _case(tmp_path, P1, [{"name": "手提式干粉灭火器", "method": "ONSITE"}, {"name": "消防应急照明灯具", "method": "ONSITE"}])
    _case(tmp_path, P2, [{"name": "防火门", "method": "SAMPLING"}])
    _case(tmp_path, P3, [{"name": "防火门", "method": "ONSITE"}, {"name": "消火栓", "method": "ONSITE"}])
    scanned = _case(tmp_path, P4, [{"name": "防火门", "method": "ONSITE"}])
    manifest = json.loads((scanned / "manifest.json").read_text(encoding="utf-8"))
    manifest["documentSlots"][0]["versions"][0]["kind"] = "SCANNED"
    (scanned / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def _tables(path: Path):
    name = path.parent.parent.name
    if name.startswith(P1):
        return [[HEADER, _row("手提式干粉灭火器\nMFZ/ABC4", "8", "6"), _row("消防应急照明灯具", "20", "5 只"), ["备注", "", "", "", "", "", ""]]]
    if name.startswith(P2):
        return [[HEADER, _row("防火门", "3", "1")]]
    return [[HEADER, _row("防火门", "3", "1")]]   # P3：清单两项，记录只有一行


def _scan(root):
    out = root / "运行记录" / "数量证据" / "数量证据.json"
    return out, q.scan(root, out, "2026-11-12T00:00:00Z", tables_of=_tables)


def test_quantity_text_is_kept_and_a_unit_is_only_taken_from_the_text():
    assert q.parse_quantity(" 6 ") == {"text": "6", "value": 6, "unit": None}
    assert q.parse_quantity("5 只") == {"text": "5只", "value": 5, "unit": "只"}
    assert q.parse_quantity("若干") == {"text": "若干", "value": None, "unit": None} and q.parse_quantity(None)["text"] is None


def test_scan_reads_the_latest_generation_by_manifest_digest_and_holds_cases_it_cannot_line_up(root):
    out, result = _scan(root)
    assert result["counts"] == {"cases": 4, "held": 2, "products": 3}
    cases = json.loads(out.read_text(encoding="utf-8"))["cases"]
    assert cases[P1]["generation"].startswith(P1 + "-20261110") and cases[P1]["record"]["path"].endswith("source/消防产品监督检查记录.pdf")
    assert [(p["seq"], p["base"]["value"], p["checked"]["text"], p["checked"]["unit"]) for p in cases[P1]["products"]] == [(1, 8, "6", None), (2, 20, "5只", "只")]
    assert "行数 1 与清单产品数 2" in cases[P3]["hold"] and cases[P4]["hold"] == "清单没有初查电子监督检查记录"


def test_a_changed_record_file_is_not_used(root):
    folder = next(p for p in (root / "工作区" / "历史工作区").iterdir() if p.name.startswith(P2))
    (folder / "source" / "消防产品监督检查记录.pdf").write_bytes(b"changed")
    assert q.extract_case(folder, root, _tables)["hold"] == "找不到与清单摘要一致的初查电子记录"


def _cloud():
    def row(key, name, method, **extra):
        return {q.PRODUCT_KEY: key, q.F_NAME: name, q.F_METHOD: [method], q.F_TEXT: None, q.F_VALUE: None, q.F_UNIT: None, q.F_MEANING: None, **extra}
    return {"recP00000000001": row(f"{P1}|初查|1", "手提式干粉灭火器", "现场判定"),
            "recP00000000002": row(f"{P1}|初查|2", "消防应急照明灯具", "现场判定", **{q.F_UNIT: "个"}),
            "recP00000000003": row(f"{P1}|复查|1", "手提式干粉灭火器", "现场判定"),
            "recP00000000004": row(f"{P2}|初查|1", "防火门", "抽样送检"),
            "recP00000000005": row(f"{P3}|初查|1", "防火门", "现场判定"),
            "recP00000000006": row("32002201C202600009|初查|1", "防火门", "现场判定")}


def test_plan_fills_only_empty_cells_never_invents_a_unit_and_lists_every_gap(root):
    out, result = _scan(root)
    report = q.build_plan(evidence_path=out, evidence_sha=result["sha256"], cloud_rows=_cloud(), out_dir=root / "plan")
    assert report["counts"] == {"initialProducts": 5, "toFill": 2, "alreadyFilled": 0, "missing": 4}
    plan = json.loads(Path(report["plan"]["path"]).read_text(encoding="utf-8"))
    assert plan["schemaVersion"] == sync.FIELD_PLAN_SCHEMA
    first, second = plan["entries"]
    assert {f: v["new"] for f, v in first["fields"].items()} == {q.F_TEXT: "6", q.F_VALUE: 6, q.F_MEANING: "现场检查数量"}       # 原文无单位：不写单位
    assert {f: v["new"] for f, v in second["fields"].items()} == {q.F_TEXT: "5只", q.F_VALUE: 5, q.F_MEANING: "现场检查数量"}     # 单位格已有值：不覆盖
    assert all(v["expectedOld"] is None and len(v["evidence"]) == 2 for v in first["fields"].values())
    assert first["stableKey"] == {"fieldId": q.PRODUCT_KEY, "value": f"{P1}|初查|1"}
    reasons = {m["产品键"]: m["reason"] for m in report["missing"]}
    assert reasons[f"{P1}|初查|1"].startswith("记录原文没有单位") and reasons[f"{P2}|初查|1"].startswith("抽样送检")
    assert "不一致" in reasons[f"{P3}|初查|1"] and reasons["32002201C202600009|初查|1"] == "本地没有该案已核验清单"
    for ref in first["fields"][q.F_TEXT]["evidence"]:
        assert sync._evidence_ref(root, ref)[1] is None                                            # 证据路径相对业务根，摘要可复算


def test_plan_refuses_a_cloud_row_whose_name_differs_and_is_quiet_once_filled(root):
    out, result = _scan(root)
    cloud = _cloud()
    cloud["recP00000000001"][q.F_NAME] = "推车式灭火器"
    report = q.build_plan(evidence_path=out, evidence_sha=result["sha256"], cloud_rows=cloud, out_dir=root / "plan")
    assert report["counts"]["toFill"] == 0 and report["plan"] is None
    done = {"recP00000000001": {q.PRODUCT_KEY: f"{P1}|初查|1", q.F_NAME: "手提式干粉灭火器", q.F_METHOD: ["现场判定"], q.F_TEXT: "6", q.F_VALUE: 6, q.F_UNIT: "具", q.F_MEANING: ["现场检查数量"]},
            "recP00000000002": {q.PRODUCT_KEY: f"{P1}|初查|2", q.F_NAME: "消防应急照明灯具", q.F_METHOD: ["现场判定"], q.F_TEXT: "5只", q.F_VALUE: 5, q.F_UNIT: "只", q.F_MEANING: ["现场检查数量"]}}
    again = q.build_plan(evidence_path=out, evidence_sha=result["sha256"], cloud_rows=done, out_dir=root / "plan2")
    assert again["counts"] == {"initialProducts": 2, "toFill": 0, "alreadyFilled": 2, "missing": 0}
    with pytest.raises(ValueError):
        q.build_plan(evidence_path=out, evidence_sha="0" * 64, cloud_rows=done, out_dir=root / "plan3")


def test_a_headerless_continuation_table_on_a_later_page_is_read_after_the_header_table():
    first = [HEADER, _row("手提式干粉灭火器MF/ABCE5", "3", "2")]
    cont = [_row("手提式二氧化碳灭火器MT/3", "2", "2"), ["备 注", "已拍摄检查视频", "", "", "", "", ""]]
    rows, why = q.record_rows([first, cont])
    assert why is None and [r["checked"] for r in rows] == ["2", "2"]
    # 没有先出现表头的无头表不被当成产品表
    assert q.record_rows([cont]) == (None, "记录里没有产品表")
    # 列数不对的无头表不并入（行数因此对不上时整案仍按行数不一致挂起，不猜）
    assert q.record_rows([first, [["残缺", "行"]]])[0] == [{"nameCell": "手提式干粉灭火器MF/ABCE5", "base": "3", "checked": "2"}]
