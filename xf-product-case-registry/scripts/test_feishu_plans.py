"""Offline tests for the field-ownership rule and the two non-source planners (in-memory cloud)."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import pytest

import scripts.feishu_plans as plans
import scripts.feishu_source_sync as sync
from scripts import field_policy

BASE, MBASE = "BaseCASE0000000000000000000", "BaseMAINT00000000000000000"
CASE, ENT = "tblCASE00000001", "tblENT000000001"
KEY, UNIT, NET, GAS, CHECKED = "fld1lHXHpk", "fldUNIT0001", "fldNET00001", "fldGAS00001", "fldCHECK001"
EKEY, ESTATE, EPHONE, ENAME = "fld7d3PedK", "fldSTATE001", "fldPHONE001", "fldENAME001"
STATES = ["直接维修", "代收转交", "不维修", "已停止维修"]


def _field(fid, name, kind, cls, options=None):
    item = {"fieldId": fid, "name": name, "type": kind, "class": cls}
    if options:
        item["options"] = options
    return item


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def contract(tmp_path):
    body = {
        "baseId": BASE,
        "tables": {"案卷主表": {"tableId": CASE, "fields": [
            _field(KEY, "项目编号", "text", "source-sync-whitelist"), _field(UNIT, "单位名称", "text", "source-sync-whitelist"),
            _field(NET, "是否网售", "select", "business-label", ["是", "否"]), _field(GAS, "是否加油加气站", "select", "business-label", ["是", "否"]),
            _field(CHECKED, "核实说明", "text", "legacy-hidden-protected")]}},
        "maintenanceBase": {"baseId": MBASE, "tables": {"维修企业": {"tableId": ENT, "fields": [
            _field(EKEY, "企业编号", "text", "admin-only"), _field(ENAME, "企业名称", "text", "brigade-maintained"),
            _field(ESTATE, "维修业务状态", "select", "brigade-maintained", STATES), _field(EPHONE, "负责人手机号", "text", "brigade-maintained")]}}},
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


class Cloud:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)   # (table, record) -> {fid: cell}
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_at: int | None = None
        self.history: dict[str, list[dict]] = {}
        self.history_fails = False

    def list_rows(self, base, table, field_ids=None):
        return {r: {f: row.get(f) for f in (field_ids or row)} for (t, r), row in self.rows.items() if t == table}

    def get_records(self, base, table, record_ids):
        fields = {f for (t, _), row in self.rows.items() if t == table for f in row}
        return {r: {f: self.rows[(table, r)].get(f) for f in fields} for r in record_ids}

    def upsert(self, base, table, record_id, fields):
        if self.fail_at is not None and len(self.writes) == self.fail_at:
            raise sync.RuntimeSyncError("UNKNOWN: 模拟限额/断线")
        self.writes.append((table, record_id, copy.deepcopy(fields)))
        self.rows[(table, record_id)].update(fields)
        self.person(record_id, *fields, operator="管理员", after="工具写入", at=time.time())
        return {"data": {"record": {"record_id": record_id}}}

    def person(self, record_id, *field_ids, operator, after, at=1.0):
        """One service-side revision: who changed which cells, as ``+record-history-list`` reports it."""
        revisions = self.history.setdefault(record_id, [{"rev": 1, "createTime": 0.0, "operator": "管理员", "fieldIds": [KEY], "changes": []}])
        revisions.insert(0, {"rev": len(revisions) + 1, "createTime": at, "operator": operator, "fieldIds": sorted(field_ids),
                             "changes": [{"fieldId": f, "before": "", "after": after} for f in field_ids]})

    def get_record_history(self, base, table, record_id):
        if self.history_fails:
            raise sync.RuntimeSyncError("历史接口不可用")
        return self.history.get(record_id, [{"rev": 1, "createTime": 0.0, "operator": "管理员", "fieldIds": [KEY], "changes": []}])


def _case_rows():
    return {
        (CASE, "recA00000000001"): {KEY: "P-1", UNIT: "甲", NET: None, GAS: ["待核实"], CHECKED: "人工说明"},
        (CASE, "recA00000000002"): {KEY: "P-2", UNIT: "乙", NET: ["是"], GAS: ["否"], CHECKED: None},
        (CASE, "recA00000000003"): {KEY: "P-3", UNIT: "丙", NET: [], GAS: ["是"], CHECKED: None},
    }


def _ledger(tmp_path):
    root = tmp_path / "ledger"
    sync.Ledger(root).initialize()
    return root


def _execute(spec_path, cloud, ledger):
    return sync.run(Path(spec_path), execute_flag=True, reconcile_flag=False, cli="unused", transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)


def _spec(tmp_path, contract, fields, *, section=None, base=BASE, table=CASE, record="recA00000000001", key=(KEY, "P-1"), name="s"):
    ref = {"path": str(contract), "sha256": _sha(contract)}
    if section:
        ref["section"] = section
    body = {"mode": "plan", "schemaVersion": sync.SPEC_SCHEMA, "target": {"baseToken": base, "profile": "offline", "identity": "user"},
            "limits": {"maxRecords": 20}, "contract": ref, "allowlist": {table: {"records": [record], "fields": sorted(fields)}},
            "updates": [{"tableId": table, "recordId": record, "stableKey": {"fieldId": key[0], "value": key[1]}, "fields": fields}]}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _basis(kind, **extra):
    return {"kind": kind, "sourceRef": "ref", "observedAt": "2026-11-12T00:00:00Z", **extra}


def _plan(spec, cloud):
    return sync.run(spec, execute_flag=False, reconcile_flag=False, cli="unused", transport=cloud)


def test_label_default_fills_only_empty_and_placeholder_cells_and_keeps_explicit_answers(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    report = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    assert report["status"] == "OK" and report["counts"] == {"rows": 3, "explicit": 3, "empty": 2, "placeholder": 1, "protected": 0, "recordsToWrite": 2, "held": 0}
    saved = json.loads(Path(report["previousValues"]["path"]).read_text(encoding="utf-8"))["values"]
    assert {(v["stableKey"], v["field"], v["old"]) for v in saved} == {("P-1", "是否网售", None), ("P-1", "是否加油加气站", "待核实"), ("P-3", "是否网售", None)}
    assert not cloud.writes                                                          # 规划只读
    result = _execute(report["specs"][0]["path"], cloud, ledger)
    assert result["status"] == "OK"
    assert cloud.rows[(CASE, "recA00000000001")][NET] == ["否"] and cloud.rows[(CASE, "recA00000000001")][GAS] == ["否"]
    assert cloud.rows[(CASE, "recA00000000002")][NET] == ["是"] and cloud.rows[(CASE, "recA00000000003")][GAS] == ["是"]
    assert cloud.rows[(CASE, "recA00000000001")][CHECKED] == "人工说明"
    events = sync.Ledger(ledger).events(sync.Ledger.target_key(BASE, CASE, "recA00000000001"))
    assert events[0]["fields"][NET]["basis"] == "label-initial"                      # AI 写入回执可辨依据
    again = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out2", log_root=tmp_path / "log2", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    assert again["counts"]["recordsToWrite"] == 0 and not again["specs"]             # 重复触发不再写


def test_label_default_sets_aside_a_case_whose_old_human_release_was_never_accepted_and_writes_the_rest(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    sync.Ledger(ledger, isolated_test=True).append(sync.Ledger.target_key(BASE, CASE, "create:P-1"),
                                                   {"event": "human_release", "intentId": "old-intent", "reason": "历史放行"})
    report = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    assert report["status"] == "PARTIAL" and report["counts"]["recordsToWrite"] == 1
    assert report["held"] == [{"recordId": "recA00000000001", "stableKey": "P-1", "problem": "历史 human_release 未获用户风险接受，保护继续，不写"}]
    assert _execute(report["specs"][0]["path"], cloud, ledger)["status"] == "OK"     # 其余记录照常写，不被同批拖住
    assert cloud.rows[(CASE, "recA00000000001")][NET] is None and cloud.rows[(CASE, "recA00000000003")][NET] == ["否"]


def test_label_default_does_not_overwrite_a_value_a_person_saved_between_plan_and_write(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    report = plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    cloud.rows[(CASE, "recA00000000001")][NET] = ["是"]                               # 大队刚保存
    with pytest.raises(sync.RuntimeSyncError, match="旧值应为空但云端已有值"):
        _execute(report["specs"][0]["path"], cloud, ledger)
    assert not cloud.writes and cloud.rows[(CASE, "recA00000000001")][NET] == ["是"]


@pytest.mark.parametrize("fields, message", [
    ({NET: {"new": "否", "basis": _basis("source-observed", valueState="observed", explicitNegative=True)}}, "监督事实更新不能写"),
    ({NET: {"new": "是", "expectedOld": None, "basis": _basis("label-initial")}}, "只能写标签初值"),
    ({NET: {"new": "否", "expectedOld": "是", "basis": _basis("label-initial")}}, "不改已有明确值"),
    ({NET: {"new": "否", "basis": _basis("label-initial")}}, "必须给出 expectedOld"),
    ({UNIT: {"new": "x", "expectedOld": "甲", "basis": _basis("label-initial")}}, "label-initial 不能写"),
    ({UNIT: {"new": "x", "expectedOld": "甲", "basis": _basis("user-delegated", authorizedBy="user", userQuote="请改")}}, "user-delegated 不能写 supervision-fact"),
    ({NET: {"new": "是", "expectedOld": None, "basis": _basis("user-delegated")}}, "authorizedBy=user"),
    ({CHECKED: {"new": "x", "expectedOld": None, "basis": _basis("user-delegated", authorizedBy="user", userQuote="请改")}}, "legacy-hidden-protected"),
])
def test_a_write_basis_cannot_reach_a_field_it_does_not_own(tmp_path, contract, fields, message):
    with pytest.raises(sync.SyncError, match=message):
        _plan(_spec(tmp_path, contract, fields), Cloud(_case_rows()))


def test_missing_supervision_facts_still_never_become_no_or_zero(tmp_path, contract):
    spec = _spec(tmp_path, contract, {UNIT: {"new": "否", "basis": _basis("source-observed", valueState="observed")}})
    with pytest.raises(sync.SyncError, match="explicitNegative"):
        _plan(spec, Cloud(_case_rows()))


def _delegated(tmp_path, entries, **extra):
    body = {"schemaVersion": plans.DELEGATED_PLAN_SCHEMA, "authorizedBy": "user", "userQuote": "按批准计划迁移维修业务状态", "sourceRef": "用户批准计划.md",
            "section": "maintenanceBase", "table": "维修企业", "entries": entries, **extra}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _ent_rows():
    return {(ENT, "recE00000000001"): {EKEY: "WXQ-001", ENAME: "甲厂", ESTATE: None, EPHONE: "111"},
            (ENT, "recE00000000002"): {EKEY: "WXQ-027", ENAME: "乙厂", ESTATE: None, EPHONE: None},
            (ENT, "recE00000000003"): {EKEY: "WXQ-028", ENAME: "丙厂", ESTATE: ["直接维修"], EPHONE: None}}


def test_delegated_entry_writes_the_maintenance_base_and_reports_a_conflict_instead_of_overwriting(tmp_path, contract):
    cloud, ledger = Cloud(_ent_rows()), _ledger(tmp_path)
    plan = _delegated(tmp_path, [
        {"stableKey": "WXQ-001", "fields": {"维修业务状态": {"new": "直接维修", "expectedOld": None}}},
        {"stableKey": "WXQ-027", "fields": {"维修业务状态": {"new": "不维修", "expectedOld": None}, "负责人手机号": {"new": "222"}}},
        {"stableKey": "WXQ-028", "fields": {"维修业务状态": {"new": "不维修", "expectedOld": None}}},     # 大队已改成直接维修：冲突
        {"stableKey": "WXQ-999", "fields": {"维修业务状态": {"new": "不维修"}}},                           # 无此企业
    ])
    report = plans.build_delegated_specs(
        plan_path=plan, plan_sha=_sha(plan), contract_path=contract, contract_sha=_sha(contract), target={"baseToken": MBASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / "out", log_root=tmp_path / "log", ledger_dir=ledger, isolated_test_ledger=True)
    assert report["status"] == "PARTIAL" and report["counts"] == {"entries": 4, "recordsToWrite": 2, "alreadyEqualFields": 0, "conflicts": 1, "refused": 1}
    assert report["conflicts"][0]["current"] == "直接维修" and report["conflicts"][0]["action"] == "REPORT_ONLY"
    assert _execute(report["specs"][0]["path"], cloud, ledger)["status"] == "OK"
    assert cloud.rows[(ENT, "recE00000000001")][ESTATE] == ["直接维修"] and cloud.rows[(ENT, "recE00000000002")] == {EKEY: "WXQ-027", ENAME: "乙厂", ESTATE: ["不维修"], EPHONE: "222"}
    assert cloud.rows[(ENT, "recE00000000003")][ESTATE] == ["直接维修"]               # 冲突行原值保留
    event = sync.Ledger(ledger).events(sync.Ledger.target_key(MBASE, ENT, "recE00000000002"))[0]
    assert event["fields"][ESTATE]["basis"] == "user-delegated"


def test_an_interrupted_write_blocks_only_that_target_and_is_not_replayed(tmp_path, contract):
    cloud, ledger = Cloud(_ent_rows()), _ledger(tmp_path)
    plan = _delegated(tmp_path, [{"stableKey": "WXQ-001", "fields": {"维修业务状态": {"new": "直接维修"}}},
                                 {"stableKey": "WXQ-027", "fields": {"维修业务状态": {"new": "不维修"}}}])
    kwargs = dict(plan_path=plan, plan_sha=_sha(plan), contract_path=contract, contract_sha=_sha(contract),
                  target={"baseToken": MBASE, "profile": "offline", "identity": "user"}, transport=cloud, ledger_dir=ledger, isolated_test_ledger=True)
    report = plans.build_delegated_specs(out_dir=tmp_path / "out", log_root=tmp_path / "log", **kwargs)
    cloud.fail_at = 1                                                                # 第二条写入时断线：效果未知
    with pytest.raises(sync.RuntimeSyncError, match="未重试"):
        _execute(report["specs"][0]["path"], cloud, ledger)
    blocked = sync.Ledger(ledger).unresolved()
    assert list(blocked) == [sync.Ledger.target_key(MBASE, ENT, "recE00000000002")]
    cloud.fail_at = None
    retry = plans.build_delegated_specs(out_dir=tmp_path / "out2", log_root=tmp_path / "log2", **kwargs)
    assert retry["counts"]["recordsToWrite"] == 0 and retry["refused"][0]["problems"] == ["该目标账本仍未决(BLOCKED)，先对账"]
    assert len(cloud.writes) == 1                                                    # 未知写入不重放


def test_ownership_names_one_authority_per_contract_class():
    assert field_policy.ownership("source-sync-whitelist") == "supervision-fact"
    assert field_policy.ownership("business-label") == field_policy.ownership("brigade-maintained") == "brigade-maintained"
    assert field_policy.ownership("derived-readonly") == "statistics" and field_policy.ownership("something-new") == "internal"
    assert field_policy.label_conflict("是", "否") == {"current": "是", "evidence": "否", "action": "REPORT_ONLY"}
    assert field_policy.label_conflict(None, "否") is None


def test_evidence_sets_a_label_the_assistant_defaulted_but_only_reports_against_a_value_a_person_chose(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    target = {"baseToken": BASE, "profile": "offline", "identity": "user"}
    first = plans.build_label_initial_specs(contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target=target, transport=cloud,
                                            out_dir=tmp_path / "o1", log_root=tmp_path / "l1", ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)
    _execute(first["specs"][0]["path"], cloud, ledger)                               # P-1、P-3 的网售由规则初值写成“否”
    cloud.rows[(CASE, "recA00000000003")][NET] = ["否"]                               # 大队保存了同样的“否”：与助手写入无法区分，按助手值处理
    cloud.rows[(CASE, "recA00000000002")][NET] = ["否"]                               # P-2 原是“是”，大队改为“否”
    evidence = {"new": "是", "evidenceRef": "网售清单 2026-11 第3行", "evidenceSha256": "a" * 64}
    plan = tmp_path / "evidence.json"
    plan.write_text(json.dumps({"schemaVersion": "FeishuLabelEvidencePlanV1", "sourceRef": "网售清单", "table": "案卷主表", "entries": [
        {"stableKey": "P-1", "fields": {"是否网售": evidence}}, {"stableKey": "P-2", "fields": {"是否网售": evidence}},
        {"stableKey": "P-9", "fields": {"是否网售": evidence}}]}, ensure_ascii=False), encoding="utf-8")
    report = plans.build_label_evidence_specs(plan_path=plan, plan_sha=_sha(plan), contract_path=contract, contract_sha=_sha(contract), target=target,
                                              transport=cloud, out_dir=tmp_path / "o2", log_root=tmp_path / "l2", ledger_dir=ledger, isolated_test_ledger=True)
    assert report["counts"] == {"entries": 3, "recordsToWrite": 1, "alreadyEqualFields": 0, "differences": 1, "refused": 1}
    assert report["differences"][0]["stableKey"] == "P-2" and report["differences"][0]["action"] == "REPORT_ONLY"
    assert "匹配 0 条" in report["refused"][0]["problems"][0]                          # 未匹配已有案卷：不建记录
    _execute(report["specs"][0]["path"], cloud, ledger)
    assert cloud.rows[(CASE, "recA00000000001")][NET] == ["是"] and cloud.rows[(CASE, "recA00000000002")][NET] == ["否"]
    spec = _spec(tmp_path, contract, {NET: {"new": "是", "expectedOld": "否", "basis": _basis("label-evidence")}}, record="recA00000000002", key=(KEY, "P-2"))
    with pytest.raises(sync.SyncError, match="evidenceSha256"):
        _plan(spec, cloud)


def _initial(tmp_path, contract, cloud, ledger, name="out"):
    return plans.build_label_initial_specs(
        contract_path=contract, contract_sha=_sha(contract), table_name="案卷主表", target={"baseToken": BASE, "profile": "offline", "identity": "user"},
        transport=cloud, out_dir=tmp_path / name, log_root=tmp_path / (name + "-log"), ledger_dir=ledger, source_ref="规则", isolated_test_ledger=True)


def test_a_cell_someone_filled_and_set_back_is_kept_as_a_persons_value(tmp_path, contract):
    body = json.loads(contract.read_text(encoding="utf-8"))
    body["assistantOperators"] = ["管理员"]
    contract.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    cloud.person("recA00000000001", NET, operator="梁溪成员", after="是")                # 大队填“是”
    cloud.person("recA00000000001", NET, operator="梁溪成员", after="", at=2.0)          # 又清空：现值与没人动过一样
    cloud.person("recA00000000001", GAS, operator="管理员", after="待核实")              # 旧迁移写的占位，不是人工答案
    cloud.person("recA00000000003", NET, operator="管理员", after="是")                  # 管理员手工保存过明确值
    cloud.person("recA00000000003", NET, operator="管理员", after="", at=2.0)
    report = _initial(tmp_path, contract, cloud, ledger)
    assert report["counts"]["protected"] == 2 and report["counts"]["recordsToWrite"] == 1 and report["status"] == "PARTIAL"
    assert {(h["stableKey"], h["field"], h["history"]) for h in report["held"]} == {("P-1", "是否网售", "person"), ("P-3", "是否网售", "person")}
    _execute(report["specs"][0]["path"], cloud, ledger)
    assert cloud.rows[(CASE, "recA00000000001")][NET] is None and cloud.rows[(CASE, "recA00000000001")][GAS] == ["否"]
    assert cloud.rows[(CASE, "recA00000000003")][NET] == []


def test_unreadable_history_is_reported_as_uncertain_and_nothing_is_written(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    cloud.history_fails = True
    report = _initial(tmp_path, contract, cloud, ledger)
    assert report["counts"]["recordsToWrite"] == 0 and not report["specs"]
    assert {h["history"] for h in report["held"]} == {"unknown"} and all("不确定" in h["problem"] for h in report["held"])


def test_without_a_named_tool_account_every_unledgered_revision_counts_as_a_person(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    cloud.person("recA00000000001", GAS, operator="管理员", after="待核实")
    report = _initial(tmp_path, contract, cloud, ledger)
    assert [(h["stableKey"], h["field"]) for h in report["held"]] == [("P-1", "是否加油加气站")]


def test_evidence_does_not_replace_a_default_that_a_person_changed_and_changed_back(tmp_path, contract):
    cloud, ledger = Cloud(_case_rows()), _ledger(tmp_path)
    target = {"baseToken": BASE, "profile": "offline", "identity": "user"}
    first = _initial(tmp_path, contract, cloud, ledger, "o1")
    _execute(first["specs"][0]["path"], cloud, ledger)                               # P-1、P-3 网售 = 否（工具写入，在账本窗口内）
    cloud.person("recA00000000001", NET, operator="梁溪成员", after="是", at=time.time() + 600)
    cloud.person("recA00000000001", NET, operator="梁溪成员", after="否", at=time.time() + 700)   # 改回“否”
    evidence = {"new": "是", "evidenceRef": "网售清单 第3行", "evidenceSha256": "a" * 64}
    plan = tmp_path / "evidence.json"
    plan.write_text(json.dumps({"schemaVersion": "FeishuLabelEvidencePlanV1", "sourceRef": "网售清单", "table": "案卷主表", "entries": [
        {"stableKey": "P-1", "fields": {"是否网售": evidence}}, {"stableKey": "P-3", "fields": {"是否网售": evidence}}]}, ensure_ascii=False), encoding="utf-8")
    report = plans.build_label_evidence_specs(plan_path=plan, plan_sha=_sha(plan), contract_path=contract, contract_sha=_sha(contract), target=target,
                                              transport=cloud, out_dir=tmp_path / "o2", log_root=tmp_path / "l2", ledger_dir=ledger, isolated_test_ledger=True)
    assert report["counts"]["recordsToWrite"] == 1 and report["counts"]["differences"] == 1
    assert report["differences"][0]["stableKey"] == "P-1" and report["differences"][0]["history"] == "person"
