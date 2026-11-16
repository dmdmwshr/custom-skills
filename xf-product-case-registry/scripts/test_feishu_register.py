"""Offline tests for active registration from enterprise and online-sale lists (in-memory cloud)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.feishu_plans as plans
import scripts.feishu_register as register
import scripts.feishu_source_sync as sync
from scripts.test_feishu_plans import BASE, CASE, ENT, EKEY, ENAME, ESTATE, EPHONE, KEY, MBASE, NET, GAS, UNIT, STATES, Cloud, _field, _ledger, _sha

ECODE, EBRIGADE = "fldECODE001", "fldEBRIG001"
MTARGET, CTARGET = {"baseToken": MBASE, "profile": "offline", "identity": "user"}, {"baseToken": BASE, "profile": "offline", "identity": "user"}


def _code(body17: str) -> str:
    total = sum(register.CODE_ALPHABET.index(ch) * w for ch, w in zip(body17, register.CODE_WEIGHTS))
    return body17 + register.CODE_ALPHABET[(31 - total % 31) % 31]


C1, C2, C3 = _code("91320200MA1XXXXX1"), _code("91320200MA1XXXXX2"), _code("91320200MA1XXXXX3")


@pytest.fixture()
def contract(tmp_path):
    body = {
        "baseId": BASE,
        "tables": {"案卷主表": {"tableId": CASE, "fields": [
            _field(KEY, "项目编号", "text", "source-sync-whitelist"), _field(UNIT, "单位名称", "text", "source-sync-whitelist"),
            _field(NET, "是否网售", "select", "business-label", ["是", "否"]), _field(GAS, "是否加油加气站", "select", "business-label", ["是", "否"])]}},
        "maintenanceBase": {"baseId": MBASE, "tables": {"维修企业": {"tableId": ENT, "fields": [
            _field(EKEY, "企业编号", "text", "admin-only"), _field(ENAME, "企业名称", "text", "brigade-maintained"),
            _field(ECODE, "统一社会信用代码", "text", "brigade-maintained"), _field(EBRIGADE, "所属大队", "select", "admin-only", ["梁溪大队", "锡山大队"]),
            _field(ESTATE, "维修业务状态", "select", "brigade-maintained", STATES), _field(EPHONE, "负责人手机号", "text", "brigade-maintained")]}}},
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _enterprises():
    return {
        (ENT, "recE00000000001"): {EKEY: "WXQ-001", ENAME: "甲消防（无锡）有限公司", ECODE: C1, EBRIGADE: ["梁溪大队"], ESTATE: ["直接维修"], EPHONE: None},
        (ENT, "recE00000000002"): {EKEY: "WXQ-002", ENAME: "乙消防有限公司", ECODE: None, EBRIGADE: ["锡山大队"], ESTATE: ["不维修"], EPHONE: "13800000000"},
    }


def _list(tmp_path, schema, entries, name="list.json", **extra):
    path = tmp_path / name
    path.write_text(json.dumps({"schemaVersion": schema, "sourceRef": "用户交来清单", **extra, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_code_check_character():
    assert register.code_valid(C1) and not register.code_valid(C1[:17] + ("0" if C1[17] != "0" else "1")) and not register.code_valid("9132")


def test_enterprise_list_matches_stable_numbers_fills_empty_cells_numbers_new_ones_and_holds_conflicts(tmp_path, contract):
    cloud, ledger = Cloud(_enterprises()), _ledger(tmp_path)
    listing = _list(tmp_path, register.ENTERPRISE_LIST, userQuote="把这份企业清单登记上", entries=[
        {"企业名称": "甲消防(无锡)有限公司", "统一社会信用代码": C1, "负责人手机号": "13911111111", "维修业务状态": "不维修"},   # 全半角括号视同；空格补、已有值只列差异
        {"企业名称": "乙消防有限公司", "统一社会信用代码": C2},                                                              # 同名、原无代码 -> 补代码
        {"企业名称": "丙消防有限公司", "统一社会信用代码": C3, "所属大队": "锡山大队", "维修业务状态": "代收转交"},                  # 新企业
        {"企业名称": "丁消防有限公司", "统一社会信用代码": "91320200MA1XXXXX40"},                                             # 代码异常
        {"企业名称": "另一个名字", "统一社会信用代码": C1},                                                                   # 清单内重复代码
        {"企业名称": "戊消防有限公司"},                                                                                      # 新企业但缺大队
    ])
    report = register.plan_enterprises(list_path=listing, list_sha=_sha(listing), contract_path=contract, contract_sha=_sha(contract), target=MTARGET,
                                       transport=cloud, business_root=tmp_path / "root", day="2026-11-12")
    assert report["counts"] == {"matched": 2, "new": 1, "held": 3, "differences": 1, "recordsToUpdate": 2} and not cloud.writes
    assert [(m["企业编号"], m["fieldsToWrite"]) for m in report["matched"]] == [("WXQ-001", ["负责人手机号"]), ("WXQ-002", ["统一社会信用代码"])]
    assert report["differences"] == [{"企业编号": "WXQ-001", "field": "维修业务状态", "feishu": "直接维修", "list": "不维修"}]
    assert report["new"][0]["企业编号"] == "WXQ-003" and [h["reason"][:4] for h in report["held"]] == ["信用代码", "清单内重", "新企业缺"]
    batch = Path(report["batchDir"])
    assert json.loads((batch / "匹配结果.json").read_text(encoding="utf-8"))["input"]["sha256"] == _sha(listing)

    built = plans.build_delegated_specs(plan_path=Path(report["delegatedPlan"]["path"]), plan_sha=report["delegatedPlan"]["sha256"], contract_path=contract,
                                        contract_sha=_sha(contract), target=MTARGET, transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log",
                                        ledger_dir=ledger, isolated_test_ledger=True)
    for spec in built["specs"]:
        sync.run(Path(spec["path"]), execute_flag=True, reconcile_flag=False, cli="unused", transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)
    assert cloud.rows[(ENT, "recE00000000001")][EPHONE] == "13911111111" and cloud.rows[(ENT, "recE00000000001")][ESTATE] == ["直接维修"]
    assert cloud.rows[(ENT, "recE00000000002")][ECODE] == C2


class CreatingCloud(Cloud):
    def __init__(self, rows):
        super().__init__(rows)
        self.created = 0
        self.lose_receipt = False

    def create_record(self, base, table, fields):
        self.created += 1
        record_id = f"recN{self.created:011d}"
        self.rows[(table, record_id)] = dict(fields)
        if self.lose_receipt:
            self.lose_receipt = False
            raise sync.RuntimeSyncError("UNKNOWN: 模拟断线")
        return {"data": {"record": {"record_id": record_id}}}


def test_new_enterprise_is_created_once_even_when_the_receipt_is_lost_and_the_run_repeats(tmp_path, contract):
    cloud = CreatingCloud(_enterprises())
    listing = _list(tmp_path, register.ENTERPRISE_LIST, userQuote="登记", entries=[
        {"企业名称": "丙消防有限公司", "统一社会信用代码": C3, "所属大队": "锡山大队", "维修业务状态": "代收转交"},
        {"企业名称": "己消防有限公司", "所属大队": "梁溪大队"}])
    report = register.plan_enterprises(list_path=listing, list_sha=_sha(listing), contract_path=contract, contract_sha=_sha(contract), target=MTARGET,
                                       transport=cloud, business_root=tmp_path / "root", day="2026-11-12")
    kwargs = dict(batch_dir=Path(report["batchDir"]), contract_path=contract, contract_sha=_sha(contract), target=MTARGET, transport=cloud)
    cloud.lose_receipt = True
    first = register.create_enterprises(**kwargs)
    assert first["status"] == "STOPPED" and first["entries"]["WXQ-003"]["status"] == "UNKNOWN" and cloud.created == 1
    second = register.create_enterprises(**kwargs)                                  # 先回读：已在，不重放
    assert second["status"] == "OK" and second["entries"]["WXQ-003"]["confirmedBy"] == "readback" and cloud.created == 2
    assert register.create_enterprises(**kwargs)["status"] == "OK" and cloud.created == 2
    made = [row for (table, rid), row in cloud.rows.items() if rid.startswith("recN")]
    assert [(r[EKEY], r[EBRIGADE]) for r in made] == [("WXQ-003", ["锡山大队"]), ("WXQ-004", ["梁溪大队"])] and made[0][ESTATE] == ["代收转交"]
    again = register.plan_enterprises(list_path=listing, list_sha=_sha(listing), contract_path=contract, contract_sha=_sha(contract), target=MTARGET,
                                      transport=cloud, business_root=tmp_path / "root2", day="2026-11-13")
    assert again["counts"]["new"] == 0 and again["counts"]["matched"] == 2 and again["delegatedPlan"] is None   # 重复交同一清单不再新建


def test_online_sale_list_only_touches_matched_cases_and_keeps_the_rest_local_until_they_match(tmp_path, contract):
    rows = {(CASE, "recA00000000001"): {KEY: "P-1", UNIT: "甲", NET: ["否"], GAS: ["否"]},
            (CASE, "recA00000000002"): {KEY: "P-2", UNIT: "乙", NET: ["否"], GAS: ["否"]},
            (CASE, "recA00000000003"): {KEY: "P-3", UNIT: "乙", NET: ["否"], GAS: ["否"]}}
    cloud, ledger = Cloud(rows), _ledger(tmp_path)
    listing = _list(tmp_path, register.ONLINE_LIST, entries=[{"项目编号": "P-1"}, {"单位名称": "乙"}, {"单位名称": "丙", "说明": "某平台"}, {"项目编号": "P-9"}])
    kwargs = dict(contract_path=contract, contract_sha=_sha(contract), target=CTARGET, transport=cloud, business_root=tmp_path / "root")
    report = register.plan_online_sales(list_path=listing, list_sha=_sha(listing), day="2026-11-12", **kwargs)
    assert report["counts"] == {"matchedCases": 1, "unmatched": 3, "retriedPending": 0} and not cloud.writes
    assert sorted(u["reason"][:6] for u in report["unmatched"]) == ["单位名称对应", "单位名称没有", "项目编号不在"]
    pending = json.loads(Path(report["pendingFile"]).read_text(encoding="utf-8"))
    assert len(pending["entries"]) == 3
    built = plans.build_label_evidence_specs(plan_path=Path(report["evidencePlan"]["path"]), plan_sha=report["evidencePlan"]["sha256"], contract_path=contract,
                                             contract_sha=_sha(contract), target=CTARGET, transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log",
                                             ledger_dir=ledger, isolated_test_ledger=True)
    assert not built["specs"] and cloud.rows[(CASE, "recA00000000001")][NET] == ["否"]   # 已有明确“否”：只给差异，不覆盖
    cloud.rows[(CASE, "recA00000000004")] = {KEY: "P-4", UNIT: "丙", NET: None, GAS: ["否"]}   # 之后该单位的案卷采集进来
    other = _list(tmp_path, register.ONLINE_LIST, entries=[{"项目编号": "P-2"}], name="list2.json")
    later = register.plan_online_sales(list_path=other, list_sha=_sha(other), day="2026-11-20", **kwargs)
    assert [m["项目编号"] for m in later["matched"]] == ["P-2", "P-4"] and later["counts"]["unmatched"] == 2 and later["counts"]["retriedPending"] == 3
    plan = json.loads(Path(later["evidencePlan"]["path"]).read_text(encoding="utf-8"))
    assert plan["entries"][1]["fields"]["是否网售"]["evidenceSha256"] == _sha(listing)      # 待匹配条目保留原清单摘要


def test_label_list_matches_by_brigade_and_keeps_mismatches_ambiguities_and_unknown_names_local_with_hints(tmp_path, contract):
    body = json.loads(contract.read_text(encoding="utf-8"))
    body["tables"]["案卷主表"]["fields"] += [_field("fldBRCASE01", "所属大队", "select", "admin-only", ["梁溪大队", "锡山大队"]),
                                           _field("fldFIX00001", "是否维修后灭火器", "select", "business-label", ["是", "否"])]
    contract.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    rows = {(CASE, "recA00000000001"): {KEY: "P-1", UNIT: "甲店", "fldBRCASE01": ["梁溪大队"], "fldFIX00001": None},
            (CASE, "recA00000000002"): {KEY: "P-2", UNIT: "乙店", "fldBRCASE01": ["梁溪大队"], "fldFIX00001": None},
            (CASE, "recA00000000003"): {KEY: "P-3", UNIT: "乙店", "fldBRCASE01": ["锡山大队"], "fldFIX00001": None},
            (CASE, "recA00000000004"): {KEY: "P-4", UNIT: "丙商贸有限公司", "fldBRCASE01": ["锡山大队"], "fldFIX00001": None}}
    cloud = Cloud(rows)
    entries = [{"单位名称": "甲店", "大队": "梁溪"}, {"单位名称": "甲店", "大队": "锡山大队"}, {"单位名称": "乙店"}, {"单位名称": "乙店", "大队": "锡山"},
               {"单位名称": "丙商贸", "大队": "锡山"}, {"项目编号": "P-4", "大队": "梁溪大队"}, {"项目编号": "P-9"}]
    listing = _list(tmp_path, register.LABEL_LIST, entries, label="是否维修后灭火器", value="是")
    kwargs = dict(contract_path=contract, contract_sha=_sha(contract), target=CTARGET, transport=cloud, business_root=tmp_path / "root")
    report = register.plan_label_list(list_path=listing, list_sha=_sha(listing), day="2026-11-16", **kwargs)
    assert [m["项目编号"] for m in report["matched"]] == ["P-1", "P-3"] and not cloud.writes
    reasons = {json.dumps(u["entry"], ensure_ascii=False): u for u in report["unmatched"]}
    assert "没有一个在清单大队" in reasons['{"单位名称": "甲店", "大队": "锡山大队"}']["reason"]
    assert "需给出项目编号" in reasons['{"单位名称": "乙店"}']["reason"] and reasons['{"单位名称": "乙店"}']["hints"] == ["P-2", "P-3"]
    assert reasons['{"单位名称": "丙商贸", "大队": "锡山"}']["hints"] == ["P-4|丙商贸有限公司|锡山"]          # 简称：不猜，只给近名提示
    assert "不一致" in reasons['{"项目编号": "P-4", "大队": "梁溪大队"}']["reason"] and "不在案卷主表" in reasons['{"项目编号": "P-9"}']["reason"]
    plan = json.loads(Path(report["evidencePlan"]["path"]).read_text(encoding="utf-8"))
    assert [(e["stableKey"], e["fields"]) for e in plan["entries"]][0][1]["是否维修后灭火器"]["new"] == "是"
    cloud.rows[(CASE, "recA00000000005")] = {KEY: "P-5", UNIT: "丙商贸", "fldBRCASE01": ["锡山大队"], "fldFIX00001": None}   # 之后采集进来
    again = register.plan_label_list(list_path=listing, list_sha=_sha(listing), day="2026-11-20", **kwargs)
    assert "P-5" in [m["项目编号"] for m in again["matched"]] and again["counts"]["retriedPending"] == 5
    with pytest.raises(sync.SyncError):
        bad = _list(tmp_path, register.LABEL_LIST, entries, name="bad.json", label="核实说明", value="是")
        register.plan_label_list(list_path=bad, list_sha=_sha(bad), day="2026-11-16", **kwargs)


def _label_contract(contract):
    body = json.loads(contract.read_text(encoding="utf-8"))
    body["tables"]["案卷主表"]["fields"] += [_field("fldBRCASE01", "所属大队", "select", "admin-only", ["梁溪大队", "锡山大队"])]
    contract.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return dict(contract_path=contract, contract_sha=_sha(contract), target=CTARGET)


def _row(key, unit, brigade="梁溪大队"):
    return {KEY: key, UNIT: unit, "fldBRCASE01": [brigade], NET: None, GAS: ["否"]}


def test_label_list_holds_an_entry_whose_project_number_and_unit_name_disagree(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000001"): _row("P-1", "甲店")})
    entries = [{"项目编号": "P-1", "单位名称": "另一家完全不同公司", "大队": "梁溪大队"}, {"项目编号": "P-1", "单位名称": " 甲 店 "}, {"项目编号": "P-1"}]
    listing = _list(tmp_path, register.LABEL_LIST, entries, label="是否网售", value="是")
    report = register.plan_label_list(list_path=listing, list_sha=_sha(listing), day="2026-11-16", transport=cloud, business_root=tmp_path / "root", **common)
    assert [m["项目编号"] for m in report["matched"]] == ["P-1"] and report["counts"]["unmatched"] == 1
    assert "不一致" in report["unmatched"][0]["reason"] and report["unmatched"][0]["entry"]["单位名称"] == "另一家完全不同公司"


def test_label_list_pending_entries_keep_their_own_value_and_evidence_and_never_borrow_a_later_lists_value(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000001"): _row("P-1", "甲店")})
    kwargs = dict(transport=cloud, business_root=tmp_path / "root", **common)
    first = _list(tmp_path, register.LABEL_LIST, [{"单位名称": "乙店"}], name="first.json", label="是否网售", value="是")
    register.plan_label_list(list_path=first, list_sha=_sha(first), day="2026-11-16", **kwargs)
    cloud.rows[(CASE, "recA00000000002")] = _row("P-2", "乙店")                  # 乙店案卷之后采集进来
    second = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-1"}], name="second.json", label="是否网售", value="否")
    later = register.plan_label_list(list_path=second, list_sha=_sha(second), day="2026-11-20", **kwargs)
    plan = json.loads(Path(later["evidencePlan"]["path"]).read_text(encoding="utf-8"))
    got = {e["stableKey"]: e["fields"]["是否网售"] for e in plan["entries"]}
    assert got["P-2"]["new"] == "是" and got["P-2"]["evidenceSha256"] == _sha(first)
    assert got["P-1"]["new"] == "否" and got["P-1"]["evidenceSha256"] == _sha(second)


def test_label_list_legacy_pending_without_a_value_stays_pending_and_the_same_case_with_two_values_is_held(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000001"): _row("P-1", "甲店"), (CASE, "recA00000000002"): _row("P-2", "乙店")})
    kwargs = dict(transport=cloud, business_root=tmp_path / "root", **common)
    pending = tmp_path / "root" / "运行记录" / register.REGISTER_DIR / "是否网售待匹配.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({"schema": "LabelPendingV1", "label": "是否网售", "entries": {
        "old": {"entry": {"单位名称": "乙店"}, "evidence": {"evidenceRef": "旧清单", "evidenceSha256": "a" * 64}, "firstSeen": "2026-11-01"}}}, ensure_ascii=False), encoding="utf-8")
    one = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-1"}], name="one.json", label="是否网售", value="是")
    report = register.plan_label_list(list_path=one, list_sha=_sha(one), day="2026-11-16", **kwargs)
    assert [m["项目编号"] for m in report["matched"]] == ["P-1"] and "缺少当时的标签取值" in report["unmatched"][0]["reason"]
    first = json.loads(pending.read_text(encoding="utf-8"))["entries"]
    assert list(first.values())[0]["firstSeen"] == "2026-11-01"
    two = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-1"}], name="two.json", label="是否网售", value="否")
    held = register.plan_label_list(list_path=two, list_sha=_sha(two), day="2026-11-17", **kwargs)
    assert held["conflicts"] == [] and [m["value"] for m in held["matched"]] == ["否"]     # 另一天、另一清单：各条按自己的取值处理
    both = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}, {"单位名称": "乙店"}], name="both.json", label="是否网售", value="是")
    clash = register.plan_label_list(list_path=both, list_sha=_sha(both), day="2026-11-18", **kwargs)
    assert any(m["项目编号"] == "P-2" for m in clash["matched"])


def _pending_file(tmp_path):
    return json.loads((tmp_path / "root" / "运行记录" / register.REGISTER_DIR / "是否网售待匹配.json").read_text(encoding="utf-8"))


def test_a_value_conflict_survives_across_runs_and_repeating_the_same_list_does_not_resolve_it(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000001"): _row("P-1", "甲店")})
    kwargs = dict(transport=cloud, business_root=tmp_path / "root", **common)
    first = _list(tmp_path, register.LABEL_LIST, [{"单位名称": "乙店"}], name="r1.json", label="是否网售", value="是")
    register.plan_label_list(list_path=first, list_sha=_sha(first), day="2026-11-16", **kwargs)
    cloud.rows[(CASE, "recA00000000002")] = _row("P-2", "乙店")
    second = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], name="r2.json", label="是否网售", value="否")
    r2 = register.plan_label_list(list_path=second, list_sha=_sha(second), day="2026-11-17", **kwargs)
    assert r2["conflicts"][0]["values"] == ["否", "是"] and r2["evidencePlan"] is None
    saved = _pending_file(tmp_path)                                           # 重新读盘：双方条目、取值、证据都在
    held = saved["conflicts"]["P-2"]["items"]
    assert saved["entries"] == {} and sorted(i["value"] for i in held) == ["否", "是"]
    assert {i["evidence"]["evidenceSha256"] for i in held} == {_sha(first), _sha(second)} and {i["firstSeen"] for i in held} == {"2026-11-16", "2026-11-17"}
    for day in ("2026-11-18", "2026-11-19"):                                  # 同字节清单重交多次：仍阻断，不放行任何一方
        again = register.plan_label_list(list_path=second, list_sha=_sha(second), day=day, **kwargs)
        assert again["evidencePlan"] is None and again["matched"] == [] and again["conflicts"][0]["since"] == "2026-11-17"
    other = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-1"}], name="r3.json", label="是否网售", value="否")
    third = register.plan_label_list(list_path=other, list_sha=_sha(other), day="2026-11-20", **kwargs)
    assert [m["项目编号"] for m in third["matched"]] == ["P-1"] and third["counts"]["conflicts"] == 1      # 其他案卷不受影响
    single = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], name="r4.json", label="是否网售", value="是")
    assert register.plan_label_list(list_path=single, list_sha=_sha(single), day="2026-11-21", **kwargs)["evidencePlan"] is None


def test_only_an_entry_with_a_stated_basis_resolves_a_held_conflict_and_the_history_is_kept(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000002"): _row("P-2", "乙店")})
    kwargs = dict(transport=cloud, business_root=tmp_path / "root", **common)
    cloud.rows.pop((CASE, "recA00000000002"))
    first = _list(tmp_path, register.LABEL_LIST, [{"单位名称": "乙店"}], name="a.json", label="是否网售", value="是")
    register.plan_label_list(list_path=first, list_sha=_sha(first), day="2026-11-16", **kwargs)
    cloud.rows[(CASE, "recA00000000002")] = _row("P-2", "乙店")
    second = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], name="b.json", label="是否网售", value="否")
    report = register.plan_label_list(list_path=second, list_sha=_sha(second), day="2026-11-17", **kwargs)
    assert report["counts"]["conflicts"] == 1
    decided = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2", "裁决依据": "用户2026-11-18答复：以大队复核为准，是"}], name="c.json", label="是否网售", value="是")
    done = register.plan_label_list(list_path=decided, list_sha=_sha(decided), day="2026-11-18", **kwargs)
    plan = json.loads(Path(done["evidencePlan"]["path"]).read_text(encoding="utf-8"))
    assert plan["entries"][0]["stableKey"] == "P-2" and plan["entries"][0]["fields"]["是否网售"]["new"] == "是" and done["conflicts"] == []
    saved = _pending_file(tmp_path)
    assert saved["conflicts"] == {} and saved["resolved"][0]["裁决依据"].startswith("用户2026-11-18") and len(saved["resolved"][0]["items"]) == 3


def test_a_pending_file_from_before_conflict_tracking_is_still_read(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000001"): _row("P-1", "甲店")})
    pending = tmp_path / "root" / "运行记录" / register.REGISTER_DIR / "是否网售待匹配.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({"schema": "LabelPendingV1", "label": "是否网售", "entries": {}}), encoding="utf-8")
    lst = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-1"}], label="是否网售", value="是")
    report = register.plan_label_list(list_path=lst, list_sha=_sha(lst), day="2026-11-16", transport=cloud, business_root=tmp_path / "root", **common)
    assert [m["项目编号"] for m in report["matched"]] == ["P-1"] and _pending_file(tmp_path)["conflicts"] == {}


def test_an_invalid_value_or_basis_never_clears_a_held_conflict_and_ordinary_retries_stay_blocked(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({})
    kwargs = dict(transport=cloud, business_root=tmp_path / "root", **common)
    first = _list(tmp_path, register.LABEL_LIST, [{"单位名称": "乙店"}], name="a.json", label="是否网售", value="是")
    register.plan_label_list(list_path=first, list_sha=_sha(first), day="2026-11-16", **kwargs)
    cloud.rows[(CASE, "recA00000000002")] = _row("P-2", "乙店")
    second = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], name="b.json", label="是否网售", value="否")
    assert register.plan_label_list(list_path=second, list_sha=_sha(second), day="2026-11-17", **kwargs)["counts"]["conflicts"] == 1
    pending_path = tmp_path / "root" / "运行记录" / register.REGISTER_DIR / "是否网售待匹配.json"
    held_bytes = pending_path.read_bytes()
    for bad in ("也许", "", None, True, 1):                                      # 非法取值带着裁决依据：拒绝，冲突与证据原样保留
        lst = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2", "裁决依据": "有依据"}], name="bad.json", label="是否网售", value=bad)
        with pytest.raises(sync.SyncError):
            register.plan_label_list(list_path=lst, list_sha=_sha(lst), day="2026-11-18", **kwargs)
        assert pending_path.read_bytes() == held_bytes
    for basis in (True, 1, "", "  ", ["x"]):                                      # 合法取值但依据不是非空文本：不解除
        lst = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2", "裁决依据": basis}], name="basis.json", label="是否网售", value="是")
        report = register.plan_label_list(list_path=lst, list_sha=_sha(lst), day="2026-11-19", **kwargs)
        assert report["evidencePlan"] is None and report["counts"]["conflicts"] == 1
        assert json.loads(pending_path.read_text(encoding="utf-8"))["resolved"] == []
    ordinary = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], name="ord.json", label="是否网售", value="否")      # 抛错后的普通重试仍被阻断
    again = register.plan_label_list(list_path=ordinary, list_sha=_sha(ordinary), day="2026-11-20", **kwargs)
    assert again["evidencePlan"] is None and again["matched"] == [] and again["conflicts"][0]["values"] == ["否", "是"]


def test_a_pending_entry_with_a_value_outside_the_options_is_not_matched_and_does_not_decide_anything(tmp_path, contract):
    common = _label_contract(contract)
    cloud = Cloud({(CASE, "recA00000000002"): _row("P-2", "乙店")})
    pending = tmp_path / "root" / "运行记录" / register.REGISTER_DIR / "是否网售待匹配.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({"schema": "LabelPendingV1", "label": "是否网售", "entries": {
        "x": {"entry": {"项目编号": "P-2", "裁决依据": "旧"}, "value": "也许", "evidence": {"evidenceRef": "旧", "evidenceSha256": "a" * 64}, "firstSeen": "2026-11-01"}}}, ensure_ascii=False), encoding="utf-8")
    lst = _list(tmp_path, register.LABEL_LIST, [{"项目编号": "P-2"}], label="是否网售", value="是")
    report = register.plan_label_list(list_path=lst, list_sha=_sha(lst), day="2026-11-16", transport=cloud, business_root=tmp_path / "root", **common)
    assert [m["value"] for m in report["matched"]] == ["是"] and "不在 是否网售 的可选项内" in report["unmatched"][0]["reason"]
    assert json.loads(pending.read_text(encoding="utf-8"))["conflicts"] == {}
