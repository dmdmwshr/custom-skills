"""Same-product chain plan: only explicit links, only empty cloud cells, names must agree, reviewed links applied."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import scripts.product_chain_plan as chain
import scripts.product_links as product_links

GENERATION = "20260920T094620Z-85d891366100"
P1, P2, P3 = "32002207C202600033", "32002207C202600034", "32002207C202600035"
LINK = "fldLINK0001"


def _manifest(project, *, direct_ref=None, recheck_name="直流水枪"):
    recheck = {"name": recheck_name, "clientRef": "product:recheck:1", "result": "QUALIFIED"}
    if direct_ref:
        recheck["initialProductRef"] = direct_ref
    return {"case": {"projectNo": project}, "initialInspection": {"products": [{"name": "直流水枪", "clientRef": "product:initial:1", "result": "UNQUALIFIED"}]},
            "recheckInspection": {"products": [recheck]}}


def _case(root: Path, project: str, manifest: dict, *, review=None):
    workspace = root / "工作区" / "历史工作区" / f"{project}-{GENERATION}"
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    record = {"state": "COMPLETED", "archive": {"workspacePath": str(workspace)}}
    if review:
        record["productLinks"] = {"schemaVersion": "ProductLinkReviewV1", "archiveGeneration": GENERATION, "manifestDigest": product_links.manifest_digest(manifest),
                                  "links": review}
    return record


def _layout(root):
    return SimpleNamespace(root=root, resolve_recorded_path=lambda p: Path(p))


def _cloud(project, *, link=None, recheck_name="直流水枪"):
    return {"recA": {chain.PRODUCT_KEY: f"{project}|初查|1", chain.PRODUCT_NAME: "直流水枪", LINK: None},
            "recB": {chain.PRODUCT_KEY: f"{project}|复查|1", chain.PRODUCT_NAME: recheck_name, LINK: link}}


def test_a_manifest_link_and_a_reviewed_link_both_become_one_entry_each_and_unlinked_recheck_products_get_nothing(tmp_path):
    root = tmp_path
    cases = {P1: _case(root, P1, _manifest(P1, direct_ref="product:initial:1")),
             P2: _case(root, P2, _manifest(P2), review=[{"recheckRef": "product:recheck:1", "initialRef": "product:initial:1", "basis": "IDENTICAL_ATTRIBUTES"}]),
             P3: _case(root, P3, _manifest(P3))}                       # 没有任何对应：名称一样也不连
    chains, held = chain.local_chains(_layout(root), cases, root)
    assert set(chains) == {P1, P2} and not held and chains[P2]["reviewed"] is True
    cloud = {}
    for project in (P1, P2, P3):
        for rid, row in _cloud(project).items():
            cloud[f"{rid}{project[-2:]}"] = row
    report = chain.build_plan(chains=chains, cloud_rows=cloud, link_field_id=LINK, observed_at="2026-11-17T06:00:00Z", out_dir=tmp_path / "out")
    assert report["counts"] == {"casesWithChains": 2, "links": 2, "toFill": 2, "alreadyLinked": 0, "held": 0}
    plan = json.loads(Path(report["plan"]["path"]).read_text(encoding="utf-8"))
    assert {e["stableKey"]["value"]: e["fields"][LINK]["new"] for e in plan["entries"]} == {f"{P1}|复查|1": f"{P1}|初查|1", f"{P2}|复查|1": f"{P2}|初查|1"}
    assert all(e["fields"][LINK]["expectedOld"] is None and e["fields"][LINK]["evidence"][0]["sha256"] for e in plan["entries"])


def test_existing_equal_value_is_left_a_different_value_or_a_name_mismatch_is_held_and_nothing_is_overwritten(tmp_path):
    cases = {P1: _case(tmp_path, P1, _manifest(P1, direct_ref="product:initial:1")), P2: _case(tmp_path, P2, _manifest(P2, direct_ref="product:initial:1"))}
    chains, _ = chain.local_chains(_layout(tmp_path), cases, tmp_path)
    cloud = {f"{rid}1": row for rid, row in _cloud(P1, link=f"{P1}|初查|1").items()}                       # 已经相同
    cloud.update({f"{rid}2": row for rid, row in _cloud(P2, link="别的值").items()})                      # 已有不同值
    report = chain.build_plan(chains=chains, cloud_rows=cloud, link_field_id=LINK, observed_at="t", out_dir=tmp_path / "o1")
    assert report["counts"]["toFill"] == 0 and report["counts"]["alreadyLinked"] == 1 and report["plan"] is None
    assert [h["projectNo"] for h in report["held"]] == [P2] and "已有不同的对应值" in report["held"][0]["reason"]
    mismatch = chain.build_plan(chains={P1: chains[P1]}, cloud_rows={f"{rid}1": row for rid, row in _cloud(P1, recheck_name="灭火器").items()}, link_field_id=LINK, observed_at="t", out_dir=tmp_path / "o2")
    assert mismatch["counts"]["toFill"] == 0 and "名称" in mismatch["held"][0]["reason"]
    missing = chain.build_plan(chains={P1: chains[P1]}, cloud_rows={}, link_field_id=LINK, observed_at="t", out_dir=tmp_path / "o3")
    assert "没有对应" in missing["held"][0]["reason"]


def test_a_changed_manifest_makes_the_old_review_not_apply(tmp_path):
    manifest = _manifest(P1)
    record = _case(tmp_path, P1, manifest, review=[{"recheckRef": "product:recheck:1", "initialRef": "product:initial:1", "basis": "IDENTICAL_ATTRIBUTES"}])
    workspace = Path(record["archive"]["workspacePath"])
    changed = dict(manifest, extra="新增字段")
    (workspace / "manifest.json").write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    chains, _ = chain.local_chains(_layout(tmp_path), {P1: record}, tmp_path)
    assert chains == {}


def test_a_foreign_drive_path_is_mapped_by_its_fixed_tail_under_the_business_root_and_anything_else_is_held(tmp_path):
    def refuse(_path):
        raise chain.ws.WorkspaceStateError("历史业务路径必须是绝对路径")
    layout = SimpleNamespace(root=tmp_path, resolve_recorded_path=refuse)
    record = _case(tmp_path, P1, _manifest(P1, direct_ref="product:initial:1"))
    historical = dict(record, archive={"workspacePath": f"E:\\文件夹\\案卷汇总\\工作区\\历史工作区\\{P1}-{GENERATION}"})
    elsewhere = dict(record, archive={"workspacePath": f"E:\\文件夹\\案卷汇总\\其他\\{P1}-{GENERATION}"})
    wrong_case = dict(record, archive={"workspacePath": f"E:\\文件夹\\案卷汇总\\工作区\\历史工作区\\{P2}-{GENERATION}"})
    chains, held = chain.local_chains(layout, {P1: historical}, tmp_path)
    assert set(chains) == {P1} and not held
    chains, held = chain.local_chains(layout, {P1: elsewhere}, tmp_path)
    assert not chains and P1 in held
    chains, held = chain.local_chains(layout, {P1: wrong_case}, tmp_path)
    assert not chains and P1 in held
