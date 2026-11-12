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
