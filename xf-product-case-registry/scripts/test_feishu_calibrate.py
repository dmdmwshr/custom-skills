"""监督事实校准规划器：只补空或推进未决，云端与证据不同只列差异，整案保护对象不动。"""

from __future__ import annotations

import json

import scripts.feishu_calibrate as cal
import scripts.feishu_source_sync as sync
import scripts.test_feishu_source_sync as base
from scripts.test_feishu_source_sync import BASE, CASE, CHECK, CP, DATE, DATE_SRC_F, KEY, PRODUCT, RESULT_F, _f, sha

I, P, M = sync.INSPECT_F, sync.PRODUCT_F, cal.MAIN_F
DAY = "2026-06-17T00:00:00.000+08:00"


def _setup(tmp_path, **kw):
    v3, water, transport = base._child_setup(tmp_path, **kw)
    doc = json.loads(v3.read_text(encoding="utf-8"))["baseContract"]
    doc["tables"]["案卷主表"]["fields"] += [
        {**_f(DATE_SRC_F, "初查日期来源", "select", "source-sync-whitelist", "allowed"), "options": [{"name": n} for n in ("底册", "监督系统")]},
        _f(M["recheck_date"], "复查日期", "datetime", "source-sync-whitelist", "allowed"),
        {**_f(M["recheck_result"], "复查结果", "select", "source-sync-whitelist", "allowed"),
         "options": [{"name": n} for n in ("合格", "不合格", "无需复查", "观察期内未开展", "已开展待结果", "到期确认未开展", "待核实")]}]
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return contract, water, transport


def _rows(transport, *, result="合格", products=(("手提式干粉灭火器", "合格"),), stage="初查", day=DAY, **inspect):
    transport.rows[(CHECK, f"recCHK{stage}")] = {I["key"]: f"{CP}|{stage}", I["date"]: day, I["result"]: [result] if result else None, I["count"]: len(products), **inspect}
    for number, (name, value) in enumerate(products, start=1):
        transport.rows[(PRODUCT, f"recPRD{stage}{number}")] = {P["key"]: f"{CP}|{stage}|{number}", P["name"]: name, P["result"]: [value] if value else None}


def _plan(tmp_path, contract, water, transport, **kw):
    ledger = tmp_path / "ledger"
    if not (ledger / "targets").is_dir():
        sync.Ledger(ledger, isolated_test=True).initialize()
    report = cal.build_calibration_plan(waterline_path=water, contract_path=contract, contract_sha=sha(contract), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
                                        transport=transport, out_dir=tmp_path / "cal", ledger_dir=ledger, isolated_test_ledger=True, **kw)
    writes = {(w["key"], w["field"]): (w["old"], w["new"]) for w in report["writes"]}
    diffs = {(d["key"], d["field"]): d for d in report["differences"]}
    return report, writes, diffs


ONE_OK = [{"name": "手提式干粉灭火器", "method": "ONSITE", "result": "QUALIFIED", "location": "大厅"}]


def test_qualified_case_without_recheck_becomes_no_recheck_needed_and_old_source_label_is_kept_in_the_report(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["底册"]})
    _rows(transport, **{I["date_source"]: ["底册"], I["month"]: "2026-06", I["bad"]: 0})
    report, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert writes[(CP, "复查结果")] == (None, "无需复查") and writes[(CP, "初查日期来源")] == ("底册", "监督系统")
    assert writes[(f"{CP}|初查", "日期来源")] == ("底册", "监督系统") and writes[(f"{CP}|初查|1", "所在部位")] == (None, "大厅")
    assert (CP, "初查结果") not in writes and (CP, "复查日期") not in writes and not diffs and report["status"] == "PLANNED"
    plan = json.loads((tmp_path / "cal" / "calibrate-plan.json").read_text(encoding="utf-8"))
    main = next(e for e in plan["entries"] if e["table"] == "案卷主表")
    assert main["fields"][DATE_SRC_F]["expectedOld"] == "底册" and main["fields"][M["recheck_result"]]["evidence"][0]["path"].endswith("manifest.json")


def test_second_run_after_values_agree_plans_nothing(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["监督系统"], M["recheck_result"]: ["无需复查"]})
    _rows(transport, **{I["date_source"]: ["监督系统"], I["month"]: "2026-06", I["bad"]: 0, I["method"]: ["现场判定"], I["team"]: ["梁溪大队"], I["source"]: ["监督系统"]})
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["location"]: "大厅", P["method"]: ["现场判定"], P["source"]: ["监督系统"]})
    report, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert not writes and not diffs and report["status"] == "OK" and report["plan"] is None


def test_cloud_recheck_without_local_evidence_is_kept_and_never_called_no_recheck(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["监督系统"], M["recheck_date"]: "2026-07-01T00:00:00.000+08:00"})
    _rows(transport, **{I["date_source"]: ["监督系统"], I["month"]: "2026-06", I["bad"]: 0})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert (CP, "复查结果") not in writes and diffs[(CP, "复查结果")]["kind"] == "云端有复查而本地清单无复查记录"


def test_manifest_recheck_fills_blank_date_and_result_but_a_different_cloud_conclusion_is_only_listed(tmp_path):
    recheck = {"inspectionDate": "2026-07-20", "products": [{"name": "洒水喷头", "method": "ONSITE", "result": "QUALIFIED"}]}
    contract, water, transport = _setup(tmp_path, recheck=recheck)
    _rows(transport, result="不合格", products=(("手提式干粉灭火器", "合格"), ("洒水喷头", "不合格")))
    _rows(transport, stage="复查", day="2026-07-20T00:00:00.000+08:00", products=(("洒水喷头", "合格"),))
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert writes[(CP, "复查日期")] == (None, "2026-07-20") and writes[(CP, "复查结果")] == (None, "合格") and writes[(f"{CP}|复查", "检查序次")] == (None, 1)
    transport.rows[(CASE, "recEXIST000001")].update({M["recheck_date"]: "2026-07-21T00:00:00.000+08:00", M["recheck_result"]: ["不合格"]})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert (CP, "复查日期") not in writes and (CP, "复查结果") not in writes
    assert diffs[(CP, "复查日期")]["cloud"] == "2026-07-21" and diffs[(CP, "复查结果")]["local"] == "合格"


def test_terminal_cloud_value_that_differs_from_evidence_is_a_difference_and_pending_is_advanced(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["不合格"]})
    _rows(transport, result="待核实", products=(("手提式干粉灭火器", "待核实"),))
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert diffs[(CP, "初查结果")]["cloud"] == "不合格" and (CP, "初查结果") not in writes
    assert writes[(f"{CP}|初查", "检查结果")] == ("待核实", "合格") and writes[(f"{CP}|初查|1", "检查结果")] == ("待核实", "合格")
    assert writes[(f"{CP}|初查", "不合格产品数")] == (None, 0)
    plan = json.loads((tmp_path / "cal" / "calibrate-plan.json").read_text(encoding="utf-8"))
    check = next(e for e in plan["entries"] if e["table"] == "检查明细")
    assert check["fields"][I["bad"]]["explicitNegative"] is True


def test_date_difference_keeps_both_sides_and_leaves_the_source_label_alone(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK, cloud_day="2026-06-18")
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["底册"]})
    _rows(transport, day="2026-06-18T00:00:00.000+08:00", **{I["date_source"]: ["底册"]})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert diffs[(CP, "初查日期")]["local"] == "2026-06-17" and diffs[(f"{CP}|初查", "检查日期")]["cloud"] == "2026-06-18"
    assert (CP, "初查日期来源") not in writes and (f"{CP}|初查", "日期来源") not in writes and (f"{CP}|初查", "统计月份") not in writes


def test_product_rows_are_compared_only_when_name_and_count_match(tmp_path):
    contract, water, transport = _setup(tmp_path)   # 清单两项：灭火器(合格)、洒水喷头(不合格)
    _rows(transport, result="不合格", products=(("洒水喷头", None), ("手提式干粉灭火器", None)))
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert diffs[(f"{CP}|初查|1", "产品名称")]["kind"].startswith("同序号产品名称与清单不同") and not [k for k in writes if k[0].startswith(f"{CP}|初查|")]
    del transport.rows[(PRODUCT, "recPRD初查2")]
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert diffs[(f"{CP}|初查", "行")]["kind"] == "云端产品行数与清单不同" and (f"{CP}|初查", "不合格产品数") not in writes


def test_unverified_manifest_and_only_projects_leave_the_case_alone(tmp_path):
    contract, water, transport = _setup(tmp_path, tamper=True)
    _rows(transport)
    report, writes, _ = _plan(tmp_path, contract, water, transport)
    assert not writes and report["casesLeftAlone"][0]["projectNo"] == CP and report["counts"]["casesCompared"] == 0
    report, _, _ = _plan(tmp_path, contract, water, transport, only_projects=frozenset({"X"}))
    assert report["counts"]["cases"] == 0


def test_a_text_difference_is_closed_only_by_a_ruling_that_still_matches_both_sides_and_its_evidence(tmp_path):
    product = {**ONE_OK[0], "nominalProducer": "甲消防器材有限公司", "problemDescription": "来源详情现场检查情况「1项不合格」；检查记录正文：无永久性铭牌"}
    contract, water, transport = _setup(tmp_path, initial=[product])
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"]})
    _rows(transport)
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["producer"]: "乙消防器材有限公司", P["problem"]: "无永久性铭牌"})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    key = f"{CP}|初查|1"
    assert diffs[(key, "标称生产者")]["cloud"] == "乙消防器材有限公司" and (key, "标称生产者") not in writes and (key, "问题描述") not in diffs
    doc = water.parent / "检查记录.pdf"
    doc.write_bytes(b"record text")
    entry = {"projectNo": CP, "table": "产品明细", "key": key, "field": "标称生产者", "cloud": "乙消防器材有限公司", "local": "甲消防器材有限公司",
             "adopt": "local", "evidence": [{"path": "检查记录.pdf", "sha256": sha(doc)}], "basis": "检查记录电子文字第2页产品表"}
    rulings = tmp_path / "rulings.json"

    def run(**change):
        rulings.write_text(json.dumps({"schema": cal.RESOLUTION_SCHEMA, "entries": [{**entry, **change}]}, ensure_ascii=False), encoding="utf-8")
        return _plan(tmp_path, contract, water, transport, resolutions_path=rulings)

    report, writes, diffs = run()
    assert writes[(key, "标称生产者")] == ("乙消防器材有限公司", "甲消防器材有限公司") and (key, "标称生产者") not in diffs
    plan = json.loads((tmp_path / "cal" / "calibrate-plan.json").read_text(encoding="utf-8"))
    field = next(e for e in plan["entries"] if e["table"] == "产品明细")["fields"][P["producer"]]
    assert field["expectedOld"] == "乙消防器材有限公司" and [e["path"] for e in field["evidence"]][-1] == "检查记录.pdf"
    report, writes, diffs = run(adopt="cloud")
    assert (key, "标称生产者") not in writes and (key, "标称生产者") not in diffs and report["resolved"][0]["adopt"] == "cloud"
    _, writes, diffs = run(cloud="丙消防器材有限公司")       # 裁决时的云端值已不是现在的值：失效
    assert (key, "标称生产者") not in writes and diffs[(key, "标称生产者")]["kind"] == "标称生产者与清单不同"
    doc.write_bytes(b"changed")                               # 证据文件摘要变了：不写，列为证据问题
    _, writes, diffs = run()
    assert (key, "标称生产者") not in writes and diffs[(key, "标称生产者")]["kind"] == "证据文件不可读或摘要已变"


def test_a_name_ruling_that_adopts_the_manifest_corrects_the_name_with_evidence_and_the_cloud_adopting_ruling_writes_nothing(tmp_path):
    product = {**ONE_OK[0], "name": "钢质防火门", "modelSpec": "GFM-1"}
    contract, water, transport = _setup(tmp_path, initial=[product])
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"]})
    _rows(transport)
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["name"]: "钢质防火门(GFM-1)"})
    key = f"{CP}|初查|1"
    doc = water.parent / "检查记录.pdf"
    doc.write_bytes(b"record text")
    entry = {"projectNo": CP, "table": "产品明细", "key": key, "field": "产品名称", "cloud": "钢质防火门(GFM-1)", "local": "钢质防火门",
             "adopt": "local", "evidence": [{"path": "检查记录.pdf", "sha256": sha(doc)}], "basis": "检查记录电子文字第2页产品表"}
    rulings = tmp_path / "rulings.json"

    def run(**change):
        rulings.write_text(json.dumps({"schema": cal.RESOLUTION_SCHEMA, "entries": [{**entry, **change}]}, ensure_ascii=False), encoding="utf-8")
        return _plan(tmp_path, contract, water, transport, resolutions_path=rulings)

    _, writes, diffs = run()                                  # 采用清单值：名称与其余事实字段同批更正，旧值门禁
    assert writes[(key, "产品名称")] == ("钢质防火门(GFM-1)", "钢质防火门") and writes[(key, "规格型号")] == (None, "GFM-1") and (key, "产品名称") not in diffs
    plan = json.loads((tmp_path / "cal" / "calibrate-plan.json").read_text(encoding="utf-8"))
    name_item = next(e for e in plan["entries"] if e["table"] == "产品明细")["fields"][P["name"]]
    assert name_item["expectedOld"] == "钢质防火门(GFM-1)" and name_item["evidence"] and name_item["basisRef"]
    _, writes, diffs = run(adopt="cloud")                     # 采用云端值：名称不写，规格等其余字段照常
    assert (key, "产品名称") not in writes
    doc.write_bytes(b"changed")                               # 证据摘要变了：裁决失效，名称不写
    _, writes, diffs = run()
    assert (key, "产品名称") not in writes


def _name_case(tmp_path, cloud_name="钢质防火门(GFM-1)"):
    product = {**ONE_OK[0], "name": "钢质防火门", "modelSpec": "GFM-1"}
    contract, water, transport = _setup(tmp_path, initial=[product])
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"]})
    _rows(transport)
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["name"]: cloud_name})
    return contract, water, transport, f"{CP}|初查|1"


def test_without_correction_mode_a_wrong_cloud_name_is_only_listed_and_with_it_the_fact_is_corrected_with_old_value_and_evidence(tmp_path):
    contract, water, transport, key = _name_case(tmp_path)
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert (key, "产品名称") not in writes and diffs[(key, "产品名称")]["kind"].startswith("同序号产品名称与清单不同")
    report, writes, diffs = _plan(tmp_path, contract, water, transport, correct_facts=True)
    assert writes[(key, "产品名称")] == ("钢质防火门(GFM-1)", "钢质防火门") and (key, "产品名称") not in diffs
    assert report["counts"]["corrections"] >= 1 and any(w.get("correction") for w in report["writes"] if w["key"] == key)


def test_a_manifest_value_that_looks_like_a_duplicated_extraction_is_never_auto_corrected(tmp_path):
    plan = cal._Plan(tmp_path, {"t": {"f": {"name": "产品名称"}}}, {"产品明细": "t"}, correct=True)
    assert plan.malformed("钢质防火门钢质防火门") and plan.malformed(" ") and not plan.malformed("钢质防火门")
    assert plan.try_correct("P", "产品明细", "rec", "k", "K", "f", "钢质防火门", "钢质防火门钢质防火门", "产品名称与清单不同", "how", [{"path": "x", "sha256": "y"}], "t0") is True
    assert not plan.staged and plan.diffs[0]["action"] == "不更正；交核对" and "疑似提取错误" in plan.diffs[0]["kind"]
    assert plan.try_correct("P", "产品明细", "rec", "k", "K", "f", None, "新值", "补空走普通路径", "how", [], "t0") is False


def test_more_corrections_than_the_limit_are_all_held_and_listed_not_written(tmp_path):
    contract, water, transport, key = _name_case(tmp_path)
    report, writes, diffs = _plan(tmp_path, contract, water, transport, correct_facts=True, max_corrections=0)
    assert (key, "产品名称") not in writes and report["counts"]["correctionsHeld"] >= 1
    assert "超过单次上限" in diffs[(key, "产品名称")]["action"]


def test_legacy_data_source_labels_on_both_child_tables_are_unified_to_the_supervision_system_with_the_old_label_as_expected_old(tmp_path):
    contract, water, transport = _setup(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["监督系统"], M["recheck_result"]: ["无需复查"]})
    _rows(transport, **{I["date_source"]: ["监督系统"], I["month"]: "2026-06", I["bad"]: 0, I["method"]: ["现场判定"], I["team"]: ["梁溪大队"], I["source"]: ["人工底册"]})
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["location"]: "大厅", P["method"]: ["现场判定"], P["source"]: ["来源详情"]})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert writes[(f"{CP}|初查", "数据来源")] == ("人工底册", "监督系统") and writes[(f"{CP}|初查|1", "数据来源")] == ("来源详情", "监督系统") and not diffs


def _setup_unit_form(tmp_path, **kw):
    contract, water, transport = _setup(tmp_path, **kw)
    doc = json.loads(contract.read_text(encoding="utf-8"))
    doc["tables"]["案卷主表"]["fields"] += [
        {**_f(M["unit"], "单位类别", "select", "source-sync-whitelist", "allowed"), "options": [{"name": n} for n in cal.UNIT_OPTIONS]},
        {**_f(M["form"], "检查形式", "select", "source-sync-whitelist", "allowed"), "options": [{"name": n} for n in ("专项监督抽查", "日常检查", "举报投诉核查", "其他", "待核实")]}]
    contract.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    transport.rows[(CASE, "recEXIST000001")].update({RESULT_F: ["合格"], DATE_SRC_F: ["监督系统"], M["recheck_result"]: ["无需复查"]})
    _rows(transport, **{I["date_source"]: ["监督系统"], I["month"]: "2026-06", I["bad"]: 0, I["method"]: ["现场判定"], I["team"]: ["梁溪大队"], I["source"]: ["监督系统"]})
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["location"]: "大厅", P["method"]: ["现场判定"], P["source"]: ["监督系统"]})
    return contract, water, transport


def _form_evidence(tmp_path, water, value="日常检查", manifest_sha=None):
    record = water.parent / "记录.pdf"
    record.write_bytes(b"record bytes")
    manifest = next(water.parent.rglob("manifest.json"))
    body = {"schemaVersion": "RecordFormEvidenceV1", "cases": {CP: {"inspectionForm": value, "record": {"path": "记录.pdf", "sha256": sha(record)},
                                                                    "manifest": {"path": "m", "sha256": manifest_sha or sha(manifest)}}}}
    path = tmp_path / "form-evidence.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def test_inspection_form_from_the_record_checkbox_fills_a_blank_with_both_files_as_evidence(tmp_path):
    contract, water, transport = _setup_unit_form(tmp_path, initial=ONE_OK)
    _, writes, diffs = _plan(tmp_path, contract, water, transport, form_evidence_path=_form_evidence(tmp_path, water))
    assert writes[(CP, "检查形式")] == (None, "日常检查")
    plan = json.loads((tmp_path / "cal" / "calibrate-plan.json").read_text(encoding="utf-8"))
    item = next(e for e in plan["entries"] if e["table"] == "案卷主表")["fields"][M["form"]]
    assert item["expectedOld"] is None and {e["path"].split("/")[-1] for e in item["evidence"]} == {"manifest.json", "记录.pdf"} and not diffs


def test_a_different_cloud_inspection_form_is_listed_unless_correction_mode_is_on_and_stale_evidence_never_writes(tmp_path):
    contract, water, transport = _setup_unit_form(tmp_path, initial=ONE_OK)
    transport.rows[(CASE, "recEXIST000001")][M["form"]] = ["专项监督抽查"]
    evidence = _form_evidence(tmp_path, water)
    _, writes, diffs = _plan(tmp_path, contract, water, transport, form_evidence_path=evidence)
    assert (CP, "检查形式") not in writes and diffs[(CP, "检查形式")]["kind"] == "检查形式与记录勾选不同"
    _, writes, _ = _plan(tmp_path, contract, water, transport, form_evidence_path=evidence, correct_facts=True)
    assert writes[(CP, "检查形式")] == ("专项监督抽查", "日常检查")
    stale = _form_evidence(tmp_path, water, manifest_sha="0" * 64)
    _, writes, diffs = _plan(tmp_path, contract, water, transport, form_evidence_path=stale, correct_facts=True)
    assert (CP, "检查形式") not in writes and diffs[(CP, "检查形式")]["kind"].startswith("检查形式证据与现行清单或记录摘要不符")


def test_unit_category_comes_from_the_saved_source_detail_and_an_unknown_value_is_only_listed(tmp_path, monkeypatch):
    contract, water, transport = _setup_unit_form(tmp_path, initial=ONE_OK)
    detail = water.parent / "source-evidence.json"
    detail.write_text("{}", encoding="utf-8")
    value = {"v": "重点单位"}
    monkeypatch.setattr(sync, "saved_detail_evidence", lambda root, project, case: {"fields": {"单位类别": value["v"]}, "observedAt": "2026-11-01T00:00:00+08:00", "path": "source-evidence.json", "sha256": sha(detail)})
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert writes[(CP, "单位类别")] == (None, "重点单位")
    value["v"] = "神秘类别"
    _, writes, diffs = _plan(tmp_path, contract, water, transport)
    assert (CP, "单位类别") not in writes and diffs[(CP, "单位类别")]["kind"] == "来源详情的单位类别不在合同选项里"


def test_unit_category_that_contradicts_the_record_key_unit_box_is_listed_and_never_written_or_corrected(tmp_path, monkeypatch):
    contract, water, transport = _setup_unit_form(tmp_path, initial=ONE_OK)
    detail = water.parent / "source-evidence.json"
    detail.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sync, "saved_detail_evidence", lambda root, project, case: {"fields": {"单位类别": "其他单位"}, "observedAt": "t", "path": "source-evidence.json", "sha256": sha(detail)})
    evidence = _form_evidence(tmp_path, water)
    body = json.loads(evidence.read_text(encoding="utf-8"))
    body["cases"][CP]["keyUnit"] = True                                  # 记录勾“重点单位”，详情写“其他单位”
    evidence.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    for blank in (True, False):
        if not blank:
            transport.rows[(CASE, "recEXIST000001")][M["unit"]] = ["重点单位"]
        _, writes, diffs = _plan(tmp_path, contract, water, transport, form_evidence_path=evidence, correct_facts=True)
        assert (CP, "单位类别") not in writes and diffs[(CP, "单位类别")]["kind"] == "单位类别：来源详情与初查电子记录勾选互相矛盾"
    body["cases"][CP]["keyUnit"] = False                                 # 一致时照常补空
    evidence.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    transport.rows[(CASE, "recEXIST000001")][M["unit"]] = None
    _, writes, _ = _plan(tmp_path, contract, water, transport, form_evidence_path=evidence)
    assert writes[(CP, "单位类别")] == (None, "其他单位")
