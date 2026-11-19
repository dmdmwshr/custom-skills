"""包内电子监督检查记录日期的状态与证据：存在已核实 / 确认不存在 / 存在未核实 / 未知，四种状态互不替代。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import scripts.record_dates as rd

UNIT = "无锡豪讷酒店管理有限公司（桔子酒店）"
RECORD = "消防产品监督检查记录(编号：〔2026〕第0129号).pdf"
KEY = ""


def record_text(date="2026年11月16日", unit=UNIT, number="〔2026〕第0129号"):
    return f"消防产品监督检查记录 编号：{number}\n监督检查时间： {date}\n名 称：{unit}{KEY}消防安全重点单位"


def make_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(zipfile.ZipInfo(name), body if isinstance(body, bytes) else body.encode("utf-8"))
    return path


def reader(data: bytes) -> str:
    if data == b"BROKEN":
        raise ValueError("encrypted")
    return data.decode("utf-8")


def classify(tmp_path, members, unit=UNIT):
    return rd.classify_package(make_zip(tmp_path / "p.zip", members), unit, reader=reader)


def test_a_single_electronic_record_with_a_valid_day_and_the_same_unit_is_verified_before_any_organizing(tmp_path):
    result = classify(tmp_path, {RECORD: record_text(), "消防产品现场检查判定不合格通知书.pdf": "x", "呈请现场检查判定不合格审批表.pdf": "y"})
    assert result["state"] == rd.VERIFIED and result["date"] == "2026-11-16" and result["docNo"] == "〔2026〕第0129号" and len(result["memberSha256"]) == 64
    assert classify(tmp_path, {RECORD: record_text(unit="无锡豪讷酒店管理有限公司(桔子酒店)")})["state"] == rd.VERIFIED       # 全/半角括号视为同一单位


@pytest.mark.parametrize("members,why", [
    ({RECORD: record_text(unit="别的单位")}, "单位名称与总账本案不一致"),
    ({RECORD: record_text(date="2026年13月40日")}, "不是合法日期"),
    ({RECORD: "没有时间栏\n名 称：" + UNIT + KEY + "消防安全重点单位"}, "读不到“监督检查时间”"),
    ({RECORD: b"BROKEN"}, "读不出"),
    ({RECORD: record_text(), "消防产品监督检查记录(编号：〔2026〕第0130号).pdf": record_text()}, "多份"),
    ({"消防产品监督检查记录扫描件.pdf": "scan", "卷内备考表.pdf": "x"}, "疑似检查记录"),
])
def test_a_record_that_exists_but_cannot_be_trusted_is_unverified_and_never_confirmed_absent(tmp_path, members, why):
    result = classify(tmp_path, members)
    assert result["state"] == rd.UNVERIFIED and why in result["reason"]


def test_only_a_package_with_no_record_and_no_record_like_member_is_confirmed_absent(tmp_path):
    result = classify(tmp_path, {"卷内备考表.pdf": "x", "消防产品现场检查判定不合格通知书.pdf": "y", "营业执照.pdf": "z"})
    assert result["state"] == rd.ABSENT and result["memberCount"] == 3
    assert rd.classify_package(tmp_path / "missing.zip", UNIT, reader=reader)["state"] == rd.UNVERIFIED       # 包打不开不是“没有”


def _root(tmp_path, cases):
    (tmp_path / "案卷水位记录.json").write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _case(tmp_path, project, members, *, state="PENDING_ORGANIZATION", unit=UNIT):
    relative = f"原始案卷/待处理案卷/{project}/{project}.zip"
    path = make_zip(tmp_path / relative, members)
    return {"unitName": unit, "state": state, "source": {"packageRelativePath": relative, "packageSha256": "sha256:" + rd.file_sha(path)}}


def test_scan_reuses_unchanged_packages_skips_archived_cases_and_never_trusts_a_package_that_differs_from_the_ledger(tmp_path):
    calls = []
    counting = lambda data: (calls.append(1), reader(data))[1]  # noqa: E731
    cases = {"P-1": _case(tmp_path, "P-1", {RECORD: record_text()}), "P-2": _case(tmp_path, "P-2", {"卷内备考表.pdf": "x"}),
             "P-3": _case(tmp_path, "P-3", {RECORD: record_text()}, state="COMPLETED"),
             "P-4": {"unitName": UNIT, "source": {"packageRelativePath": "原始案卷/待处理案卷/P-4/none.zip"}}}
    cases["P-5"] = {**_case(tmp_path, "P-5", {RECORD: record_text()}), "unitName": UNIT}
    cases["P-5"]["source"]["packageSha256"] = "sha256:" + "0" * 64          # 总账登记的摘要与实际包不符
    root = _root(tmp_path, cases)
    out = tmp_path / "e1.json"
    first = rd.scan(root, out, "2026-11-19T00:00:00Z", reader=counting)
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert first["counts"]["byState"] == {rd.VERIFIED: 1, rd.ABSENT: 1, rd.UNKNOWN: 2} and "P-3" not in evidence["cases"] and calls == [1]
    assert evidence["cases"]["P-5"]["state"] == rd.UNKNOWN and "不符" in evidence["cases"]["P-5"]["reason"] and evidence["cases"]["P-4"]["reason"] == "总账登记的原包文件不存在"
    second = rd.scan(root, tmp_path / "e2.json", "2026-11-20T00:00:00Z", reader=counting, reuse_path=out)
    assert calls == [1] and second["counts"]["reused"] == 2                      # 同一份包摘要的已核实/确认不存在直接复用，不重读
    make_zip(tmp_path / cases["P-1"]["source"]["packageRelativePath"], {RECORD: record_text(date="2026年11月17日")})
    cases["P-1"]["source"]["packageSha256"] = "sha256:" + rd.file_sha(tmp_path / cases["P-1"]["source"]["packageRelativePath"])
    _root(tmp_path, cases)
    third = rd.scan(root, tmp_path / "e3.json", "2026-11-21T00:00:00Z", reader=counting, reuse_path=tmp_path / "e2.json")
    assert json.loads((tmp_path / "e3.json").read_text(encoding="utf-8"))["cases"]["P-1"]["date"] == "2026-11-17" and third["counts"]["reused"] == 1


def test_build_time_state_is_rechecked_against_the_package_and_member_and_a_changed_evidence_file_is_refused(tmp_path):
    cases = {"P-1": _case(tmp_path, "P-1", {RECORD: record_text()}), "P-2": _case(tmp_path, "P-2", {"卷内备考表.pdf": "x"})}
    root = _root(tmp_path, cases)
    out = tmp_path / "e.json"
    summary = rd.scan(root, out, "2026-11-19T00:00:00Z", reader=reader)
    evidence = rd.load_evidence(out, summary["sha256"])
    verified, absent = (rd.state_for(evidence, root, p, cases[p]) for p in ("P-1", "P-2"))
    assert verified["state"] == rd.VERIFIED and verified["date"] == "2026-11-16" and absent["state"] == rd.ABSENT
    assert rd.state_for(evidence, root, "P-9", {})["state"] == rd.UNKNOWN and rd.state_for(None, root, "P-1", cases["P-1"])["state"] == rd.UNKNOWN
    make_zip(tmp_path / cases["P-1"]["source"]["packageRelativePath"], {RECORD: record_text(date="2026年11月18日")})       # 包被换成另一份：证据作废
    assert rd.state_for(evidence, root, "P-1", cases["P-1"])["state"] == rd.UNKNOWN
    out.write_text(out.read_text(encoding="utf-8").replace("2026-11-16", "2026-11-18"), encoding="utf-8")
    with pytest.raises(ValueError, match="摘要"):
        rd.load_evidence(out, summary["sha256"])
