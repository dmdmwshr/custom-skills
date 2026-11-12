"""Offline tests for the Feishu -> local mirror and the independent statistics recount (in-memory cloud)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.feishu_mirror as mirror
import scripts.feishu_plans as plans
import scripts.feishu_source_sync as sync
from scripts.test_feishu_plans import BASE, CASE, ENT, EKEY, ENAME, ESTATE, EPHONE, KEY, MBASE, NET, GAS, UNIT, STATES, Cloud, _field, _ledger, _sha

CHK, PRD, MON = "tblCHK00000001", "tblPRD00000001", "tblMON00000001"
CKEY, PKEY = "fld5ujrPv4", "fldqlwHjUW"
TARGETS = {None: {"baseToken": BASE}, "maintenanceBase": {"baseToken": MBASE}}


@pytest.fixture()
def contract(tmp_path):
    def text(fid, name, cls="source-sync-whitelist"):
        return _field(fid, name, "text", cls)
    body = {
        "baseId": BASE,
        "tables": {
            "案卷主表": {"tableId": CASE, "fields": [
                text(KEY, "项目编号"), text(UNIT, "单位名称"),
                _field(NET, "是否网售", "select", "business-label", ["是", "否"]), _field(GAS, "是否加油加气站", "select", "business-label", ["是", "否"]),
                _field("fldFIX00001", "是否维修后灭火器", "select", "business-label", ["是", "否"])]},
            "检查明细": {"tableId": CHK, "fields": [
                text(CKEY, "检查键"), _field("fldCMON0001", "通报月份", "formula", "derived"), _field("fldCBRI0001", "所属大队(案卷)", "lookup", "derived"),
                _field("fldCSTG0001", "阶段", "select", "source-sync-whitelist"), _field("fldCRES0001", "检查结果", "select", "source-sync-whitelist")]},
            "产品明细": {"tableId": PRD, "fields": [
                text(PKEY, "产品键"), _field("fldPNUM0001", "数量数值", "number", "source-sync-whitelist"), text("fldPUNI0001", "数量单位"),
                _field("fldPMEA0001", "数量含义", "select", "source-sync-whitelist"),
                _field("fldPFIX0001", "是否维修后", "select", "source-sync-whitelist"), _field("fldPNET0001", "本产品涉及网售", "select", "source-sync-whitelist")]},
            "月度工作统计": {"tableId": MON, "fields": [
                text("fldMMON0001", "月份", "admin-only"), text("fldMBRI0001", "大队", "admin-only"),
                *[_field(f"fldM{i:07d}", name, "formula", "derived") for i, name in enumerate(mirror.RECOUNT_COLUMNS)]]},
        },
        "maintenanceBase": {"baseId": MBASE, "tables": {"维修企业": {"tableId": ENT, "fields": [
            _field(EKEY, "企业编号", "text", "admin-only"), _field(ENAME, "企业名称", "text", "brigade-maintained"),
            _field(ESTATE, "维修业务状态", "select", "brigade-maintained", STATES), _field(EPHONE, "负责人手机号", "text", "brigade-maintained")]}}},
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _rows():
    return {
        (CASE, "recA00000000001"): {KEY: "P-1", UNIT: "甲", NET: None, GAS: ["待核实"], "fldFIX00001": ["否"]},
        (CASE, "recA00000000002"): {KEY: "P-2", UNIT: "乙", NET: ["是"], GAS: ["否"], "fldFIX00001": ["否"]},
        (ENT, "recE00000000001"): {EKEY: "WXQ-001", ENAME: "企业一", ESTATE: ["直接维修"], EPHONE: "无"},
    }


def _run(contract, cloud, tmp_path, day, ledger=None):
    return mirror.mirror(contract_path=contract, contract_sha=_sha(contract), sections=[None, "maintenanceBase"], targets=TARGETS, transport=cloud,
                         business_root=tmp_path / "root", ledger_dir=ledger, day=day, isolated_test_ledger=True)


def test_mirror_is_read_only_quiet_when_nothing_changed_and_tells_assistant_from_brigade(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    first = _run(contract, cloud, tmp_path, "2026-11-12", ledger)
    assert first["quiet"] and [t["firstMirror"] for t in first["tables"]] == [True, True] and not cloud.writes
    root = tmp_path / "root" / "运行记录" / "飞书镜像"
    saved = json.loads((root / "当前镜像" / "维修库-维修企业.json").read_text(encoding="utf-8"))
    assert saved["rows"]["WXQ-001"] == {"企业名称": "企业一", "维修业务状态": "直接维修", "负责人手机号": "无"}   # “无”是值，原样镜像
    assert "企业编号" not in saved["fields"] and BASE not in json.dumps(saved) and MBASE not in json.dumps(saved)
    assert _run(contract, cloud, tmp_path, "2026-11-12", ledger)["quiet"]

    built = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    for spec in built["specs"]:
        sync.run(Path(spec["path"]), execute_flag=True, reconcile_flag=False, cli="unused", transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)
    cloud.rows[(CASE, "recA00000000002")][GAS] = ["是"]             # 大队在飞书保存
    cloud.rows[(ENT, "recE00000000001")][ESTATE] = ["不维修"]
    writes = len(cloud.writes)
    second = _run(contract, cloud, tmp_path, "2026-11-13", ledger)
    assert len(cloud.writes) == writes and not second["quiet"] and second["status"] == "OK"
    changes = [json.loads(line) for line in (root / "变更记录" / "2026-11-13.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {(c["stableKey"], c["field"]): (c["previous"], c["current"], c["by"]) for c in changes}
    assert by == {("P-1", "是否网售"): (None, "否", "assistant"), ("P-1", "是否加油加气站"): ("待核实", "否", "assistant"),
                  ("P-2", "是否加油加气站"): ("否", "是", "feishu-user"), ("WXQ-001", "维修业务状态"): ("直接维修", "不维修", "feishu-user")}
    assert (root / "每日快照" / "2026-11-12" / "案卷库-案卷主表-首次.json").is_file() and (root / "每日快照" / "2026-11-13" / "案卷库-案卷主表.json").is_file()
    assert json.loads((root / "每日快照" / "2026-11-12" / "案卷库-案卷主表.json").read_text(encoding="utf-8"))["rows"]["P-2"]["是否加油加气站"] == "否"


def test_mirror_reports_a_supervision_fact_that_no_longer_equals_what_the_assistant_wrote(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    spec = {"mode": "execute", "logDir": str(tmp_path / "log"), "schemaVersion": sync.SPEC_SCHEMA, "target": {"baseToken": BASE, "profile": "offline", "identity": "user"},
            "limits": {"maxRecords": 20}, "contract": {"path": str(contract), "sha256": _sha(contract)},
            "allowlist": {CASE: {"records": ["recA00000000001"], "fields": [UNIT]}},
            "updates": [{"tableId": CASE, "recordId": "recA00000000001", "stableKey": {"fieldId": KEY, "value": "P-1"},
                         "fields": {UNIT: {"new": "甲公司", "basis": {"kind": "source-observed", "valueState": "observed", "sourceRef": "ref", "observedAt": "2026-11-12T00:00:00Z"}}}}]}
    path = tmp_path / "fact.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    result = sync.run(path, execute_flag=True, reconcile_flag=False, cli="unused", transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)
    assert cloud.rows[(CASE, "recA00000000001")][UNIT] == "甲公司", result
    assert _run(contract, cloud, tmp_path, "2026-11-12", ledger)["status"] == "OK"
    cloud.rows[(CASE, "recA00000000001")][UNIT] = "被人改过"
    report = _run(contract, cloud, tmp_path, "2026-11-13", ledger)
    assert report["status"] == "ATTENTION" and report["tables"][0]["factDrift"] == [
        {"table": "案卷库-案卷主表", "stableKey": "P-1", "field": "单位名称", "assistantLastWrote": "甲公司", "feishuNow": "被人改过"}]
    assert cloud.rows[(CASE, "recA00000000001")][UNIT] == "被人改过"   # 只报告，不覆盖


def _stat_rows(monthly):
    project_a, project_b = "A" * 18, "B" * 18
    rows = {
        (CASE, "recA00000000001"): {KEY: project_a, "fldFIX00001": ["否"], NET: ["是"]},      # 案卷是网售但产品未定位
        (CASE, "recA00000000002"): {KEY: project_b, "fldFIX00001": ["是"], NET: ["否"]},
        (CHK, "recC00000000001"): {CKEY: project_a + "|初查|1", "fldCMON0001": "2026-03", "fldCBRI0001": ["梁溪"], "fldCSTG0001": ["初查"], "fldCRES0001": ["不合格"]},
        (CHK, "recC00000000002"): {CKEY: project_a + "|复查|1", "fldCMON0001": "2026-04", "fldCBRI0001": ["梁溪"], "fldCSTG0001": ["复查"], "fldCRES0001": ["合格"]},
        (CHK, "recC00000000003"): {CKEY: project_b + "|初查|1", "fldCMON0001": "2026-03", "fldCBRI0001": ["梁溪"], "fldCSTG0001": ["初查"], "fldCRES0001": ["合格"]},
        (PRD, "recP00000000001"): {PKEY: project_a + "|初查|1|1", "fldPNUM0001": 3, "fldPUNI0001": "具", "fldPMEA0001": ["抽样数量"]},
        (PRD, "recP00000000002"): {PKEY: project_a + "|初查|1|2", "fldPNUM0001": None, "fldPUNI0001": None, "fldPMEA0001": None},
        (PRD, "recP00000000003"): {PKEY: project_b + "|初查|1|1", "fldPNUM0001": 2, "fldPUNI0001": "米", "fldPMEA0001": ["现场检查数量"], "fldPFIX0001": ["是"]},
        (PRD, "recP00000000004"): {PKEY: project_a + "|复查|1|1", "fldPNUM0001": 9, "fldPUNI0001": "具", "fldPMEA0001": ["抽样数量"]},  # 复查不重复计
        (PRD, "recP00000000005"): {PKEY: project_b + "|初查|1|2", "fldPNUM0001": 5, "fldPUNI0001": None, "fldPMEA0001": ["现场检查数量"]},  # 记录没写单位
    }
    for index, (bucket, values) in enumerate(monthly.items()):
        row = {"fldMMON0001": bucket[0], "fldMBRI0001": [bucket[1]]}
        row.update({f"fldM{i:07d}": str(values.get(name, 0)) for i, name in enumerate(mirror.RECOUNT_COLUMNS)})   # 公式列按文本返回
        rows[(MON, f"recM{index:011d}")] = row
    return rows


def test_recount_counts_cases_items_gaps_and_units_separately_and_never_fills_a_missing_quantity(tmp_path, contract):
    right = {("2026-03", "梁溪"): {"R02_初查次数": 2, "R02_初查合格": 1, "R02_初查不合格": 1, "R02_初查案卷数": 2, "R02_初查产品项数": 4,
                                 "R02_初查产品数量缺项数": 2, "R02_初查产品数量缺数值项数": 1, "R02_初查现场检查数量合计": 7, "R02_产品归属缺项案卷数": 1},
             ("2026-04", "梁溪"): {"R02_复查次数": 1}}
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(_stat_rows(right)))
    assert report["status"] == "OK" and not report["diffs"], report["diffs"]
    assert report["quantityByUnit"] == {"2026-03|梁溪": {"抽样数量/具": 3, "现场检查数量/米": 2, "现场检查数量/单位未载明": 5}}   # 单位分开，缺数量不补 1
    assert report["productAttributionGaps"] == ["A" * 18]
    wrong = {**right, ("2026-03", "梁溪"): {**right[("2026-03", "梁溪")], "R02_初查产品项数": 5}}
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(_stat_rows(wrong)))
    assert report["status"] == "DIFF" and report["diffs"] == [
        {"month": "2026-03", "brigade": "梁溪", "column": "R02_初查产品项数", "feishu": 5.0, "independent": 4}]
