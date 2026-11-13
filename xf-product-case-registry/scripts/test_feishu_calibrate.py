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
    _rows(transport, **{I["date_source"]: ["监督系统"], I["month"]: "2026-06", I["bad"]: 0, I["method"]: ["现场判定"], I["team"]: ["梁溪大队"]})
    transport.rows[(PRODUCT, "recPRD初查1")].update({P["location"]: "大厅", P["method"]: ["现场判定"]})
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


def test_a_name_ruling_never_rewrites_the_create_once_name_and_only_a_same_product_ruling_opens_the_row(tmp_path):
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

    _, writes, diffs = run()                                  # 行序不一致：整行不动
    assert not [k for k in writes if k[0] == key] and diffs[(key, "产品名称")]["kind"].startswith("产品行序与检查记录不一致")
    _, writes, diffs = run(sameProduct=True)                  # 同一产品：名称不改，其余字段照常补
    assert (key, "产品名称") not in writes and writes[(key, "规格型号")] == (None, "GFM-1")
    assert diffs[(key, "产品名称")]["kind"].startswith("产品名称与监督系统原文不同")
