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
                *[_field(f"fldM{i:07d}", name, "formula", "derived") for i, name in enumerate(mirror.RECOUNT_COLUMNS)],
                _field("fldMQTY0001", mirror.QUANTITY_TEXT_COLUMN, "formula", "derived")]},
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


def _run(contract, cloud, tmp_path, day, ledger=None, **extra):
    return mirror.mirror(contract_path=contract, contract_sha=_sha(contract), sections=[None, "maintenanceBase"], targets=TARGETS, transport=cloud,
                         business_root=tmp_path / "root", ledger_dir=ledger, day=day, isolated_test_ledger=True, **extra)


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
        row["fldMQTY0001"] = values.get(mirror.QUANTITY_TEXT_COLUMN, "无数量记录")
        rows[(MON, f"recM{index:011d}")] = row
    return rows


def test_recount_counts_cases_items_gaps_and_units_separately_and_never_fills_a_missing_quantity(tmp_path, contract):
    right = {("2026-03", "梁溪"): {"R02_初查次数": 2, "R02_初查合格": 1, "R02_初查不合格": 1, "R02_初查案卷数": 2, "R02_初查产品项数": 4,
                                 "R02_初查产品数量缺项数": 2, "R02_初查产品数量缺数值项数": 1, "R02_初查现场检查数量有单位项数": 1,
                                 "R02_初查现场检查数量缺单位项数": 1, "R02_产品归属缺项案卷数": 1,
                                 mirror.QUANTITY_TEXT_COLUMN: "2.0米；另1项未载明单位未计入；"},     # 飞书把 2 写成 2.0 也算一致
             ("2026-04", "梁溪"): {"R02_复查次数": 1}}
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(_stat_rows(right)))
    assert report["status"] == "OK" and not report["diffs"], report["diffs"]
    assert report["quantityByUnit"] == {"2026-03|梁溪": {"抽样数量/具": 3, "现场检查数量/米": 2}}           # 同含义同单位才相加，缺数量不补 1
    assert report["quantityWithoutUnit"] == {"2026-03|梁溪": {"现场检查数量": 1}}                          # 没写单位的 5 只记 1 项，不进合计
    assert report["physicalQuantity"]["status"] == "BY_UNIT" and report["physicalQuantity"]["itemsWithoutUnit"] == 1
    assert "合计" not in "".join(report["totals"]) and 5 not in report["physicalQuantity"]["byMeaningAndUnit"].values()
    assert report["productAttributionGaps"] == ["A" * 18]
    pending = report["productAttributionPending"]                                                 # 产品层待确认：单独一轴，原缺项列表不变
    assert pending["cases"] == report["productAttributionGaps"] and pending["count"] == 1 and "不是运行未知" in pending["meaning"]
    wrong = {**right, ("2026-03", "梁溪"): {**right[("2026-03", "梁溪")], "R02_初查产品项数": 5, mirror.QUANTITY_TEXT_COLUMN: "7"}}
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(_stat_rows(wrong)))
    assert report["status"] == "DIFF" and report["diffs"] == [
        {"month": "2026-03", "brigade": "梁溪", "column": "R02_初查产品项数", "feishu": 5.0, "independent": 4},
        {"month": "2026-03", "brigade": "梁溪", "column": mirror.QUANTITY_TEXT_COLUMN, "feishu": "7", "independent": "2米；另1项未载明单位未计入；"}]


def test_quantities_without_any_unit_give_no_physical_total_and_are_not_zero(tmp_path, contract):
    rows = _stat_rows({("2026-03", "梁溪"): {"R02_初查次数": 2, "R02_初查合格": 1, "R02_初查不合格": 1, "R02_初查案卷数": 2, "R02_初查产品项数": 4,
                                           "R02_初查产品数量缺项数": 4, "R02_初查产品数量缺数值项数": 1, "R02_初查现场检查数量缺单位项数": 2,
                                           "R02_产品归属缺项案卷数": 1, mirror.QUANTITY_TEXT_COLUMN: "尚无可分单位汇总：2项有数值但未载明单位"},
                       ("2026-04", "梁溪"): {"R02_复查次数": 1}})
    for key, row in rows.items():
        if key[0] == PRD and row.get("fldPNUM0001") is not None:
            row["fldPUNI0001"] = None
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(rows))
    assert report["status"] == "OK", report["diffs"]
    assert report["physicalQuantity"]["status"] == "NO_UNIT_BASIS" and report["physicalQuantity"]["byMeaningAndUnit"] == {}
    assert report["quantityByUnit"] == {} and report["quantityWithoutUnit"] == {"2026-03|梁溪": {"抽样数量": 1, "现场检查数量": 2}}
    assert mirror.quantity_text({}, {}, 0) == "无数量记录"
    assert mirror.quantity_text({"具": 3, "箱": 4}, {"具": 2, "箱": 1}, 0) == "3具；其他单位1项见产品明细；"


def _write_labels(contract, cloud, ledger, tmp_path):
    built = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    for spec in built["specs"]:
        sync.run(Path(spec["path"]), execute_flag=True, reconcile_flag=False, cli="unused", transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)


def test_each_execution_has_its_own_immutable_pre_and_post_snapshot_and_post_is_compared_with_its_own_pre(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    _run(contract, cloud, tmp_path, "2026-11-16", ledger)                                             # 前一天最后确认的镜像
    cloud.rows[(CASE, "recA00000000002")][GAS] = ["是"]                                               # 白天有人在飞书保存
    pre = _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="pre", run_key="run-a")
    case_pre = next(t for t in pre["tables"] if t["table"] == "案卷库-案卷主表")
    assert case_pre["changesByUser"] == 1 and case_pre["tag"] == "pre" and case_pre["snapshotFile"].endswith("案卷库-案卷主表-写前-run-a.json")
    _write_labels(contract, cloud, ledger, tmp_path)                                                  # 本次执行的写入
    post = _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="post", run_key="run-a")
    case_post = next(t for t in post["tables"] if t["table"] == "案卷库-案卷主表")
    assert case_post["changes"] == 2 and case_post["changesByUser"] == 0                             # 只有本次写入，且都归属助手；白天人工修改不混进来
    day_dir = tmp_path / "root" / "运行记录" / "飞书镜像" / "每日快照" / "2026-11-17"
    pre_file, post_file = day_dir / "案卷库-案卷主表-写前-run-a.json", day_dir / "案卷库-案卷主表-写后-run-a.json"
    assert pre_file.is_file() and post_file.is_file() and not (day_dir / "案卷库-案卷主表-首次.json").exists()
    kept = (pre_file.read_bytes(), post_file.read_bytes())
    cloud.rows[(CASE, "recA00000000001")][GAS] = ["是"]                                               # 之后又有人改：同键重复调用不覆盖、不重读
    again = _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="post", run_key="run-a")
    assert again["reusedAll"] is True and kept == (pre_file.read_bytes(), post_file.read_bytes())
    assert json.loads(pre_file.read_text(encoding="utf-8"))["rows"]["P-1"]["是否网售"] is None
    assert json.loads(post_file.read_text(encoding="utf-8"))["rows"]["P-1"]["是否网售"] == "否"


def test_a_later_execution_never_uses_an_earlier_post_snapshot_as_its_baseline_and_post_needs_its_own_pre(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    _run(contract, cloud, tmp_path, "2026-11-16", ledger)
    with pytest.raises(sync.SyncError, match="没有本次执行"):
        _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="post", run_key="run-b")
    _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="pre", run_key="run-a")
    _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="post", run_key="run-a")
    cloud.rows[(CASE, "recA00000000002")][GAS] = ["是"]
    pre_b = _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="pre", run_key="run-b")
    assert next(t for t in pre_b["tables"] if t["table"] == "案卷库-案卷主表")["changesByUser"] == 1     # 变化出现在 B 的写前，归属“写前以前”的人工修改
    post_b = _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="post", run_key="run-b")
    assert next(t for t in post_b["tables"] if t["table"] == "案卷库-案卷主表")["changes"] == 0           # B 自己没有写，写后相对 B 的写前无变化
    with pytest.raises(sync.SyncError, match="运行键"):
        _run(contract, cloud, tmp_path, "2026-11-17", ledger, tag="pre", run_key="")


class _BadRows:
    """Cloud rows without the stable key: unreadable for the mirror (a problem, not a confirmed read)."""
    def list_rows(self, *args):
        return {"rec1": {"f": ["yes"]}}


def _table_kwargs(root, tag, key, transport):
    return dict(base="base", table_name="案卷主表", table_id="table", fields={"f": {"name": "标签", "type": "select"}}, facts={}, transport=transport,
                ledger=None, root=root, label="case", day="2026-11-17", observed_at="2026-11-17T10:00:00+08:00", tag=tag, run_key=key)


def test_a_reused_snapshot_keeps_the_problems_of_its_first_read_and_an_unconfirmed_read_is_never_the_baseline(tmp_path):
    root = tmp_path / "mirror"
    first = mirror.mirror_table(**_table_kwargs(root, "pre", "execution-one", _BadRows()))
    again = mirror.mirror_table(**_table_kwargs(root, "pre", "execution-one", _BadRows()))
    assert first["problems"] and again["reused"] is True and again["problems"] == first["problems"] and again["confirmed"] is False
    assert not (root / "当前镜像" / "case-案卷主表.json").exists()                                     # 读不清的读回不成为之后的比较基线
    with pytest.raises(sync.SyncError, match="不是已确认读回"):
        mirror.mirror_table(**_table_kwargs(root, "post", "execution-one", _BadRows()))
    legacy = root / "每日快照" / "2026-11-17" / "case-案卷主表-写前-legacy.json"
    legacy.write_text(json.dumps({"table": "x", "fields": [], "rows": {}}), encoding="utf-8")        # 没有保存检查结果的旧快照：不当作已确认
    old = mirror.mirror_table(**_table_kwargs(root, "pre", "legacy", _BadRows()))
    assert old["reused"] is True and old["confirmed"] is False and old["problems"]


def test_a_violated_relation_or_overlapping_roster_assignment_makes_the_recount_fail(tmp_path, contract, monkeypatch):
    right = {("2026-03", "梁溪"): {"R02_初查次数": 2, "R02_初查合格": 1, "R02_初查不合格": 1, "R02_初查案卷数": 2, "R02_初查产品项数": 4,
                                 "R02_初查产品数量缺项数": 2, "R02_初查产品数量缺数值项数": 1, "R02_初查现场检查数量有单位项数": 1,
                                 "R02_初查现场检查数量缺单位项数": 1, "R02_产品归属缺项案卷数": 1,
                                 mirror.QUANTITY_TEXT_COLUMN: "2.0米；另1项未载明单位未计入；"},
             ("2026-04", "梁溪"): {"R02_复查次数": 1}}
    overlap = {"relation": "同一人员ID的任职有效期不得重叠", "id": "P3", "periods": []}
    monkeypatch.setattr(mirror.feishu_stats, "available", lambda _c: True)
    monkeypatch.setattr(mirror.feishu_stats, "recount_periods", lambda **_k: {"diffs": [], "administrativeDiffs": [], "relations": [overlap]})
    report = mirror.recount(contract_path=contract, contract_sha=_sha(contract), target={"baseToken": BASE}, transport=Cloud(_stat_rows(right)))
    assert report["status"] == "DIFF" and [d["scope"] for d in report["diffs"]] == ["relation"] and report["diffs"][0]["id"] == "P3"


# ---------------------------------------------------------------------------------------------
# 写入前检查：本次执行必须有完整、已确认、合同身份一致的写前快照
# ---------------------------------------------------------------------------------------------

import argparse
from datetime import datetime, timedelta

import scripts.nightly as nightly
import scripts.registry_cli as registry_cli

DAY = "2026-11-23"


def _gate_setup(tmp_path, contract):
    runtime = tmp_path / "root" / nightly.RUNTIME_DIR
    nightly.begin(runtime, now=datetime(2026, 11, 23, 2, 0, tzinfo=nightly.SHANGHAI), trigger="scheduled")
    bound = nightly.bind(runtime, DAY, session_id="bb79e9dc-1925", receipt="r.json", runner_pid=9, now=datetime(2026, 11, 23, 2, 1, tzinfo=nightly.SHANGHAI))
    return runtime, nightly.execution_key(bound["execution"])


def _register(runtime, key, report, contract):
    return nightly.record_snapshot(runtime, DAY, tag="pre", run_key=key, contract_sha=_sha(contract),
                                   files={t["table"]: {"file": t["snapshotFile"], "sha256": t["snapshotSha256"]} for t in report["tables"]})


def _gate(tmp_path, contract, runtime, key):
    run = nightly._load(nightly._run_dir(runtime, DAY) / "run.json", None)
    return mirror.pre_gate(contract_path=contract, contract_sha=_sha(contract), sections=[None, "maintenanceBase"], targets=TARGETS,
                           business_root=tmp_path / "root", day=DAY, run_key=key, registered=((run.get("mirrorSnapshots") or {}).get("pre") or {}).get(key))


def test_the_write_gate_needs_this_executions_complete_confirmed_pre_snapshot_of_the_current_contract(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    runtime, key = _gate_setup(tmp_path, contract)
    first = _gate(tmp_path, contract, runtime, key)                                                  # 初次：还没有任何写前快照
    assert first["status"] == "INCOMPLETE" and len(first["missing"]) == len(first["expected"]) >= 2 and first["externalCalls"] == "none"
    pre = _run(contract, cloud, tmp_path, DAY, ledger, tag="pre", run_key=key)
    assert _gate(tmp_path, contract, runtime, key)["status"] == "INCOMPLETE"                       # 文件在但还没登记进运行记录
    _register(runtime, key, pre, contract)
    assert _gate(tmp_path, contract, runtime, key)["status"] == "OK"                               # 正常流程
    again = _run(contract, cloud, tmp_path, DAY, ledger, tag="pre", run_key=key)                     # 无变化重入：复用、不重读、仍然完整
    assert again["reusedAll"] is True and _gate(tmp_path, contract, runtime, key)["status"] == "OK"
    assert _gate(tmp_path, contract, runtime, "e20261123020100-other")["status"] == "INCOMPLETE"    # 其他执行键


def test_the_write_gate_refuses_a_missing_table_an_unconfirmed_read_and_another_contract_identity(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    runtime, key = _gate_setup(tmp_path, contract)
    pre = _run(contract, cloud, tmp_path, DAY, ledger, tag="pre", run_key=key)
    _register(runtime, key, pre, contract)
    day_dir = tmp_path / "root" / "运行记录" / "飞书镜像" / "每日快照" / DAY
    files = sorted(day_dir.glob("*-写前-*.json"))
    detail = next(f for f in files if "维修企业" in f.name)
    kept = detail.read_bytes()
    detail.unlink()                                                                                  # 缺表（如读到第一张就被截断）
    broken = _gate(tmp_path, contract, runtime, key)
    assert broken["status"] == "INCOMPLETE" and broken["missing"] == ["维修库-维修企业"]
    detail.write_bytes(kept)
    snapshot = json.loads(detail.read_text(encoding="utf-8"))
    snapshot["contractSha256"] = "0" * 64
    detail.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")                  # 合同身份不同；同时登记的摘要也不符
    other = _gate(tmp_path, contract, runtime, key)
    assert other["status"] == "INCOMPLETE" and any("合同身份" in r for r in other["reasons"]) and any("摘要" in r for r in other["reasons"])
    detail.write_bytes(kept)
    snapshot = json.loads(kept.decode("utf-8"))
    snapshot["checks"]["confirmed"] = False
    detail.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    assert any("已确认读回" in r for r in _gate(tmp_path, contract, runtime, key)["reasons"])


def _args(tmp_path, contract, key):
    return argparse.Namespace(contract=str(contract), contract_sha=_sha(contract), business_root=str(tmp_path / "root"), date=DAY, run_key=key, manual_run=None)


def _write_ledger_event(runtime, at):
    path = runtime / nightly.LEDGER_DIR / "targets" / "t.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"event": "intent", "at": at, "intentId": "i-1", "runId": "r"}) + "\n", encoding="utf-8")


def test_a_cut_short_pre_is_finished_before_any_write_but_never_completed_after_writes_and_its_evidence_is_kept(tmp_path, contract):
    cloud, ledger = Cloud(_rows()), _ledger(tmp_path)
    runtime, key = _gate_setup(tmp_path, contract)
    day_dir = tmp_path / "root" / "运行记录" / "飞书镜像" / "每日快照" / DAY
    pre = _run(contract, cloud, tmp_path, DAY, ledger, tag="pre", run_key=key)
    for f in sorted(day_dir.glob("*-写前-*.json"))[1:]:
        f.unlink()                                                                                   # 只剩第一张：像被 110 秒超时截断
    targets = {None: {"baseToken": BASE}, "maintenanceBase": {"baseToken": MBASE}}
    registry_cli._refuse_pre_after_writes(_args(tmp_path, contract, key), mirror, nightly, targets)       # 没有写入证据：允许补完（只补缺的表）
    kept_first = sorted(day_dir.glob("*-写前-*.json"))[0].read_bytes()
    done = _run(contract, cloud, tmp_path, DAY, ledger, tag="pre", run_key=key)
    assert sorted(day_dir.glob("*-写前-*.json"))[0].read_bytes() == kept_first and [t.get("reused") for t in done["tables"]].count(True) == 1
    for f in sorted(day_dir.glob("*-写前-*.json"))[1:]:
        f.unlink()                                                                                   # 再次被截断；这次账本已有绑定后的写入
    _write_ledger_event(runtime, (datetime(2026, 11, 23, 2, 30, tzinfo=nightly.SHANGHAI)).isoformat())
    with pytest.raises(registry_cli.RegistryError, match="不能事后补取"):
        registry_cli._refuse_pre_after_writes(_args(tmp_path, contract, key), mirror, nightly, targets)
    run = nightly._load(nightly._run_dir(runtime, DAY) / "run.json", None)
    record = run["mirrorSnapshots"]["preIncomplete"][key]
    assert record["writeEvidence"]["events"] == 1 and len(record["files"]) == 1                   # 原证据保存，不覆盖
    assert len(list(day_dir.glob("*-写前-*.json"))) == 1                                            # 没有事后补出别的表
    blocked = registry_cli.feishu_pre_gate_command
    with pytest.raises(SystemExit) as stop:
        blocked(argparse.Namespace(contract=str(contract), contract_sha=_sha(contract), base_token=BASE, maintenance_base_token=MBASE,
                                   business_root=str(tmp_path / "root"), date=DAY, run_key=key, manual_run=None))
    assert stop.value.code == 2
