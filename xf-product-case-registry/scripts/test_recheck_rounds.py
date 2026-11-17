"""多轮复查（第 2 轮及以后）：清单表示、校验、子行生成、校准、产品链、生命周期。旧清单（只有 recheckInspection）行为不变。"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import scripts.feishu_calibrate as cal
import scripts.feishu_source_sync as sync
import scripts.product_chain_plan as chain
import scripts.recheck_rounds as rr
import scripts.test_feishu_calibrate as calt
import scripts.test_feishu_source_sync as base
import scripts.test_product_chain_plan as chaint
from scripts import case_lifecycle as lifecycle
from scripts.test_feishu_source_sync import CP, PRODUCT, CHECK, CASE, sha
from scripts.test_registry_cli import pdf, validate_manifest
from scripts.test_registry_cli import manifest as cli_manifest

I, P = sync.INSPECT_F, sync.PRODUCT_F


def _recheck(day="2026-07-20", result="UNQUALIFIED", ref="p2"):
    return {"clientRef": "rc1", "stage": "RECHECK", "inspectionDate": day,
            "products": [{"clientRef": "r1", "name": "洒水喷头", "method": "ONSITE", "result": result, "initialProductRef": ref,
                          **({"problemDescription": "仍不合格"} if result == "UNQUALIFIED" else {})}]}


def _later(round_no=2, day="2026-08-20", result="QUALIFIED", ref="p2", client="rc2"):
    return {"clientRef": client, "stage": "RECHECK", "round": round_no, "inspectionDate": day,
            "products": [{"clientRef": f"r{round_no}", "name": "洒水喷头", "method": "ONSITE", "result": result, "initialProductRef": ref,
                          **({"problemDescription": "仍不合格"} if result == "UNQUALIFIED" else {})}]}


# ---------------------------------------------------------------- 清单表示
def test_old_manifest_reads_exactly_as_before_and_rounds_are_ordered_and_named():
    old = {"recheckInspection": _recheck()}
    assert [(n, path) for n, path, _ in rr.rounds(old)] == [(1, "recheckInspection")] and rr.check(old) == []
    assert rr.rounds({}) == [] and rr.round_products({}) == []
    many = {"recheckInspection": _recheck(), "laterRecheckInspections": [_later(3, "2026-09-20", client="rc3"), _later(2)]}
    assert [n for n, _, _ in rr.rounds(many)] == [1, 2, 3] and rr.check(many) == []
    assert [rr.stage_label(n) for n in (1, 2, 3)] == ["复查", "复查2", "复查3"]
    assert [n for n, _p in rr.round_products(many)] == [1, 2, 3]


def test_structure_problems_are_reported_never_repaired():
    first = _recheck()
    assert rr.check({"laterRecheckInspections": [_later()]})                                                      # 缺第 1 轮
    assert rr.check({"recheckInspection": first, "laterRecheckInspections": [_later(3, "2026-09-20")]})            # 空轮
    assert rr.check({"recheckInspection": first, "laterRecheckInspections": [_later(), _later(client="x")]})       # 重复
    assert rr.check({"recheckInspection": first, "laterRecheckInspections": [_later(1)]})                          # 不是 ≥2
    assert rr.check({"recheckInspection": first, "laterRecheckInspections": [_later(2, "2026-07-01")]})            # 日期倒退
    assert rr.check({"recheckInspection": first, "laterRecheckInspections": {}})


def test_manifest_validation_accepts_later_rounds_and_rejects_bad_ones(tmp_path: Path):
    source = tmp_path / "one.pdf"
    pdf(source)
    data = cli_manifest(source)
    data["initialInspection"]["products"] = [{"clientRef": "p2", "name": "洒水喷头", "result": "UNQUALIFIED", "method": "ONSITE", "problemDescription": "x"}]
    data["recheckInspection"] = _recheck()
    uploads = {"file:one": str(source)}
    assert validate_manifest(data, uploads) == []                              # 旧清单仍有效
    data["laterRecheckInspections"] = [_later()]
    assert validate_manifest(data, uploads) == []
    data["laterRecheckInspections"] = [_later(2, "2026-07-01")]
    assert any("早于上一轮" in e for e in validate_manifest(data, uploads))
    data["laterRecheckInspections"] = [_later(3, "2026-09-01")]
    assert any("连续" in e for e in validate_manifest(data, uploads))
    bad = _later()
    bad["products"][0]["initialProductRef"] = "nope"
    data["laterRecheckInspections"] = [bad]
    assert any("initialProductRef" in e for e in validate_manifest(data, uploads))
    stray = _later()
    del stray["round"]
    data["laterRecheckInspections"] = [stray]
    assert validate_manifest(data, uploads)                                    # 缺 round：Schema 与校验都拒绝
    data["recheckInspection"]["round"] = 1
    data["laterRecheckInspections"] = []
    assert any("round" in e for e in validate_manifest(data, uploads))         # round 1 不另写，旧块不带 round


# ---------------------------------------------------------------- 子行生成
INITIAL = [{"clientRef": "p1", "name": "手提式干粉灭火器", "method": "ONSITE", "result": "QUALIFIED"},
           {"clientRef": "p2", "name": "洒水喷头", "method": "SAMPLING", "result": "UNQUALIFIED", "problemDescription": "标志不符合"}]


def _spec_by_key(report):
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    return {c["stableKey"]["value"]: c for c in spec["creates"]}


def test_child_build_gives_round_two_its_own_key_round_field_and_parent_and_keeps_round_one_keys(tmp_path):
    contract, water, transport = base._child_setup(tmp_path, initial=INITIAL, recheck=_recheck(), later=[_later()])
    report = base._child_build(tmp_path, contract, water, transport)
    assert report["status"] == "OK" and report["counts"]["inspections"] == 3 and report["counts"]["products"] == 4
    by_key = _spec_by_key(report)
    assert {f"{CP}|初查", f"{CP}|复查", f"{CP}|复查2", f"{CP}|复查|1", f"{CP}|复查2|1"} <= set(by_key)
    one, two = by_key[f"{CP}|复查"]["fields"], by_key[f"{CP}|复查2"]["fields"]
    assert (one[I["round"]]["new"], one[I["stage"]]["new"], one[I["date"]]["new"], one[I["result"]]["new"]) == (1, "复查", "2026-07-20", "不合格")
    assert (two[I["round"]]["new"], two[I["stage"]]["new"], two[I["date"]]["new"], two[I["result"]]["new"]) == (2, "复查", "2026-08-20", "合格")
    assert two[I["date_source"]]["new"] == "监督系统" and "laterRecheckInspections[0]" in two[I["date"]]["basis"]["sourceRef"]
    assert by_key[f"{CP}|复查2|1"]["parent"]["createRef"] == f"{CP}|复查2"
    relations = {r["recheckProduct"]: r for r in report["cases"][0]["recheckRelations"]}
    assert relations[f"{CP}|复查2|1"]["initialProduct"] == f"{CP}|初查|2" and relations[f"{CP}|复查2|1"]["relation"] == "proven-by-initialProductRef"


def test_child_build_adds_only_the_new_round_when_the_cloud_already_has_the_earlier_ones_and_is_idempotent(tmp_path):
    contract, water, transport = base._child_setup(tmp_path, initial=INITIAL, recheck=_recheck(), later=[_later()])
    for key in (f"{CP}|初查", f"{CP}|复查"):
        transport.rows[(CHECK, "rec" + key[-6:])] = {I["key"]: key}
    for key in (f"{CP}|初查|1", f"{CP}|初查|2", f"{CP}|复查|1"):
        transport.rows[(PRODUCT, "rec" + key[-8:].replace("|", "_"))] = {P["key"]: key}
    report = base._child_build(tmp_path, contract, water, transport)
    assert report["counts"]["inspections"] == 1 and report["counts"]["products"] == 1
    assert set(_spec_by_key(report)) == {f"{CP}|复查2", f"{CP}|复查2|1"}                   # 旧三项一个都不动
    transport.rows[(CHECK, "recCHKRND2")] = {I["key"]: f"{CP}|复查2"}
    transport.rows[(PRODUCT, "recPRDRND2")] = {P["key"]: f"{CP}|复查2|1"}
    again = base._child_build(tmp_path, contract, water, transport)
    assert again["counts"]["cases"] == 0 and again["fresh"]["casesAlreadyWithChildren"] == 1 and not again["specs"]


def test_child_build_holds_a_case_whose_round_goes_backwards_or_has_no_round_one(tmp_path):
    contract, water, transport = base._child_setup(tmp_path, initial=INITIAL, recheck=_recheck(), later=[_later(2, "2026-07-01")])
    report = base._child_build(tmp_path, contract, water, transport)
    assert report["status"] == "PARTIAL" and not report["specs"] and "检查日早于" in json.dumps(report["blockedEntities"], ensure_ascii=False)
    sub = tmp_path / "noone"
    sub.mkdir()
    contract, water, transport = base._child_setup(sub, initial=INITIAL, later=[_later()])
    facts = sync.child_facts(CP, json.loads(next((sub / "工作区" / "历史工作区").iterdir()).joinpath("manifest.json").read_text(encoding="utf-8")), "2026-01-01")
    assert [f is None for f, _ in facts] == [False, True] and "缺少第 1 轮" in facts[1][1]


def test_sampling_reinspection_updates_the_original_product_and_does_not_add_a_round(tmp_path):
    initial = [{"clientRef": "p1", "name": "洒水喷头", "method": "SAMPLING", "result": "UNQUALIFIED", "problemDescription": "x",
                "reinspectionApplied": "YES", "reinspectionResult": "QUALIFIED"}]
    contract, water, transport = base._child_setup(tmp_path, initial=initial)
    report = base._child_build(tmp_path, contract, water, transport)
    assert set(_spec_by_key(report)) == {f"{CP}|初查", f"{CP}|初查|1"} and report["counts"]["inspections"] == 1


# ---------------------------------------------------------------- 产品链
def test_product_chain_plan_keys_each_round_with_its_own_stage_label(tmp_path):
    manifest = chaint._manifest(chaint.P1, direct_ref="product:initial:1")
    manifest["laterRecheckInspections"] = [{"round": 2, "inspectionDate": "2026-08-20", "products": [
        {"name": "直流水枪", "clientRef": "product:recheck:2", "result": "QUALIFIED", "initialProductRef": "product:initial:1"}]}]
    cases = {chaint.P1: chaint._case(tmp_path, chaint.P1, manifest)}
    chains, held = chain.local_chains(chaint._layout(tmp_path), cases, tmp_path)
    assert not held and [(l["recheckRound"], l["recheckSeq"]) for l in chains[chaint.P1]["links"]] == [(1, 1), (2, 1)]
    cloud = dict(chaint._cloud(chaint.P1))
    cloud["recC"] = {chain.PRODUCT_KEY: f"{chaint.P1}|复查2|1", chain.PRODUCT_NAME: "直流水枪", chaint.LINK: None}
    report = chain.build_plan(chains=chains, cloud_rows=cloud, link_field_id=chaint.LINK, observed_at="t", out_dir=tmp_path / "out")
    plan = json.loads(Path(report["plan"]["path"]).read_text(encoding="utf-8"))
    assert {e["stableKey"]["value"]: e["fields"][chaint.LINK]["new"] for e in plan["entries"]} == {
        f"{chaint.P1}|复查|1": f"{chaint.P1}|初查|1", f"{chaint.P1}|复查2|1": f"{chaint.P1}|初查|1"}


# ---------------------------------------------------------------- 生命周期（最后一轮有效结果决定）
def _lifecycle(rounds_results, date="2099-01-01"):
    products = lambda rows: [{"clientRef": r[0], "result": r[1], "method": "ONSITE", **({"initialProductRef": r[2]} if len(r) > 2 else {})} for r in rows]  # noqa: E731
    manifest = {"case": {"unitName": "独验单位", "unitAddress": "独验地址", "unitCategory": "重点单位", "brigadeCode": "XISHAN", "inspectionForm": "ROUTINE"},
                "initialInspection": {"inspectionDate": date, "products": products([("a", "UNQUALIFIED")])},
                "recheckInspection": {"inspectionDate": date, "products": products([("r1", rounds_results[0], "a")])}}
    if len(rounds_results) > 1:
        manifest["laterRecheckInspections"] = [{"round": number, "inspectionDate": date, "products": products([(f"r{number}", result, "a")])}
                                               for number, result in enumerate(rounds_results[1:], start=2)]
    return lifecycle.assess_case(project=CP, manifest=manifest, has_package=True, today=_dt.date(2099, 2, 20))


def test_the_latest_round_with_a_valid_result_decides_the_chain_and_the_administrative_class():
    assert _lifecycle(["UNQUALIFIED"])["administrativeClass"] == "是"                 # 旧清单：不变
    assert _lifecycle(["UNQUALIFIED", "QUALIFIED"])["administrativeClass"] == "否"    # 第 2 轮整改合格
    assert _lifecycle(["QUALIFIED", "UNQUALIFIED"])["administrativeClass"] == "是"    # 第 2 轮仍不合格
    result = _lifecycle(["UNQUALIFIED", "UNKNOWN"])                                    # 第 2 轮无有效结果：沿用第 1 轮的有效结果
    assert result["administrativeClass"] == "是"


# ---------------------------------------------------------------- 校准
def test_calibration_fills_round_two_and_the_main_row_recheck_follows_the_latest_round(tmp_path):
    contract, water, transport = calt._setup(tmp_path, initial=INITIAL, recheck=_recheck(), later=[_later()])
    calt._rows(transport, result="不合格", products=(("手提式干粉灭火器", "合格"), ("洒水喷头", "不合格")))
    calt._rows(transport, stage="复查", day="2026-07-20T00:00:00.000+08:00", products=(("洒水喷头", "不合格"),), **{I["round"]: 1})
    calt._rows(transport, stage="复查2", day="2026-08-20T00:00:00.000+08:00", products=(("洒水喷头", "合格"),))
    _, writes, diffs = calt._plan(tmp_path, contract, water, transport)
    assert writes[(CP, "复查日期")] == (None, "2026-08-20") and writes[(CP, "复查结果")] == (None, "合格")
    assert writes[(f"{CP}|复查2", "检查序次")] == (None, 2)
    assert not any("云端另有清单之外的复查行" in d["kind"] for d in diffs.values())


# ---------------------------------------------------------------- 正常入口：inventory -> compose -> 清单 -> 子行事实
def test_compose_entry_carries_later_rounds_into_the_manifest_and_a_round_two_slot_binds_to_its_own_inspection(tmp_path: Path):
    import argparse

    from scripts.registry_cli import compose_command, inventory_command, read_json

    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf(source_root / "一.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source_root), work_dir=str(work)))
    data = cli_manifest(source_root / "一.pdf")
    data.pop("packageSha256")
    data["case"]["projectNo"] = CP
    data["initialInspection"]["products"] = [{"clientRef": "p2", "name": "洒水喷头", "result": "UNQUALIFIED", "method": "ONSITE", "problemDescription": "x"}]
    data["recheckInspection"] = _recheck()
    data["laterRecheckInspections"] = [_later()]
    data["files"][0].update({"sourceRelativePath": "一.pdf", "relativePath": "normalized/一.pdf"})
    data["otherAttachments"] = []
    data["documentSlots"] = [{"clientRef": "slot:two", "slotCode": "RECHECK_INSPECTION_RECORD", "inspectionRef": "rc2",
                              "versions": [{"kind": "ELECTRONIC", "fileRef": "file:one"}]}]
    case_data = tmp_path / "case.json"
    case_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))
    except Exception as error:   # 槽位编码以 SLOT_META 为准；本测试只关心轮次，不关心具体编码
        raise AssertionError(f"compose 失败：{error}") from error
    manifest = read_json(work / "manifest.json")
    assert [n for n, _p, _b in rr.rounds(manifest)] == [1, 2]
    facts = sync.child_facts(CP, manifest, "2026-01-01")
    assert [(f["key"], f["round"]) for f, _ in facts] == [(f"{CP}|初查", 1), (f"{CP}|复查", 1), (f"{CP}|复查2", 2)]
