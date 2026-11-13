"""Offline tests for the inspection form read from the checkboxes of the initial electronic record (fake box reader, temp business root)."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.record_forms as rf
import scripts.record_quantities as q

P1, P2, P3, P4, P5 = (f"32002201C20260000{n}" for n in range(1, 6))


def _case(root: Path, project: str, *, kind: str = "ELECTRONIC", stamp: str = "20261110T010101Z") -> Path:
    folder = root / "工作区" / "历史工作区" / f"{project}-{stamp}-0123456789ab"
    (folder / "source").mkdir(parents=True)
    pdf = folder / "source" / "消防产品监督检查记录.pdf"
    pdf.write_bytes(b"%PDF " + project.encode())
    manifest = {"initialInspection": {"products": []},
                "files": [{"clientRef": "initial-record", "relativePath": "改过名.pdf", "sha256": "sha256:" + q.file_sha(pdf)}],
                "documentSlots": [{"slotCode": "INITIAL_INSPECTION_RECORD", "versions": [{"kind": kind, "fileRef": "initial-record"}]}]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return folder


BOXES = {
    P1: {"专项监督抽查": False, "举报投诉核查": False, "复查": False, "其他": False, "日常检查": True},
    P2: {"专项监督抽查": True, "举报投诉核查": False, "复查": False, "其他": False, "日常检查": False},
    P3: {"专项监督抽查": True, "举报投诉核查": False, "复查": False, "其他": False, "日常检查": True},        # 勾了两项
    P4: {"专项监督抽查": False, "举报投诉核查": False, "复查": True, "其他": False, "日常检查": False},        # 只勾复查：不是初查检查形式
    P5: {"消防安全重点单位": True},                                                                       # 没有检查形式方框
}


def _boxes(path: Path):
    return BOXES[path.parent.parent.name[:18]]


def test_decide_accepts_exactly_one_allowed_box_and_refuses_everything_else():
    assert rf.decide(BOXES[P1]) == ("日常检查", None) and rf.decide(BOXES[P2]) == ("专项监督抽查", None)
    assert rf.decide(BOXES[P3])[0] is None and "不唯一" in rf.decide(BOXES[P3])[1]
    assert rf.decide(BOXES[P4])[0] is None and "不是初查检查形式" in rf.decide(BOXES[P4])[1]
    assert rf.decide(BOXES[P5])[0] is None and "没有读到" in rf.decide(BOXES[P5])[1]
    assert rf.decide({"日常检查(含其他说明)": True, "其他": False})[0] == "日常检查"        # 选项文字后面带说明也按前缀认


def test_scan_writes_one_evidence_file_with_record_and_manifest_digests_and_holds_what_cannot_be_proven(tmp_path):
    for project in (P1, P2, P3, P4, P5):
        _case(tmp_path, project)
    _case(tmp_path, "32002201C202600006", kind="SCANNED")
    out = tmp_path / "证据.json"
    result = rf.scan(tmp_path, out, "2026-11-13T00:00:00Z", _boxes_with_default)
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["schemaVersion"] == rf.EVIDENCE_SCHEMA and result["counts"]["withForm"] == 2 and result["counts"]["held"] == 4
    assert body["cases"][P1]["inspectionForm"] == "日常检查" and body["cases"][P1]["record"]["sha256"] and body["cases"][P1]["manifest"]["sha256"]
    assert "inspectionForm" not in body["cases"][P3] and "不唯一" in body["cases"][P3]["hold"]
    assert "没有初查电子" in body["cases"]["32002201C202600006"]["hold"]


def _boxes_with_default(path: Path):
    return BOXES.get(path.parent.parent.name[:18], {})


def test_a_record_whose_digest_already_has_a_result_is_reused_without_parsing_again(tmp_path):
    _case(tmp_path, P1)
    first = tmp_path / "first.json"
    rf.scan(tmp_path, first, "2026-11-13T00:00:00Z", _boxes)

    def boom(path):   # 复用命中时不得再解析
        raise AssertionError("reparsed")

    second = tmp_path / "second.json"
    result = rf.scan(tmp_path, second, "2026-11-14T00:00:00Z", boom, reuse_path=first)
    body = json.loads(second.read_text(encoding="utf-8"))
    assert result["counts"]["reused"] == 1 and body["cases"][P1]["inspectionForm"] == "日常检查" and body["cases"][P1]["reused"] is True


def test_a_broken_pdf_holds_the_case_instead_of_failing_the_whole_scan(tmp_path):
    _case(tmp_path, P1)
    _case(tmp_path, P2)

    def reader(path):
        if P1 in str(path):
            raise ValueError("bad pdf")
        return BOXES[P2]

    body = json.loads(Path(rf.scan(tmp_path, tmp_path / "e.json", "t", reader)["path"]).read_text(encoding="utf-8"))
    assert "记录读不出" in body["cases"][P1]["hold"] and body["cases"][P2]["inspectionForm"] == "专项监督抽查"


def test_key_unit_boxes_are_read_only_when_exactly_one_of_the_pair_is_selected():
    assert rf.key_unit({"消防安全重点单位": True, "非消防安全重点单位": False}) is True
    assert rf.key_unit({"消防安全重点单位": False, "非消防安全重点单位": True}) is False
    assert rf.key_unit({"消防安全重点单位": True, "非消防安全重点单位": True}) is None and rf.key_unit({}) is None
    assert rf.key_unit({"消防安全重点单位": True}) is True and rf.key_unit({"消防安全重点单位": False}) is None   # 只读到“重点”方框且已选


def test_scan_records_the_key_unit_flag_next_to_the_form(tmp_path):
    _case(tmp_path, P1)
    boxes = {**BOXES[P1], "消防安全重点单位": True, "非消防安全重点单位": False}
    body = json.loads(Path(rf.scan(tmp_path, tmp_path / "e.json", "t", lambda path: boxes)["path"]).read_text(encoding="utf-8"))
    assert body["cases"][P1]["keyUnit"] is True and body["cases"][P1]["inspectionForm"] == "日常检查"
