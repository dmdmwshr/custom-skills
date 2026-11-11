"""Cloud removal of out-of-scope cases: exact-target checks, children first, ledgered, never replayed."""

import hashlib
import json

import pytest

from scripts import feishu_scope_retire as retire
from scripts import feishu_source_sync as sync

BASE = "BASEtoken000000000000000"
MAIN, INSP, PROD = "tblMain", "tblInsp", "tblProd"
M_KEY, M_CHILD = "fldMkey", "fldMchild"
I_KEY, I_PARENT, I_CHILD = "fldIkey", "fldIparent", "fldIchild"
P_KEY, P_PARENT = "fldPkey", "fldPparent"
OUT_A, OUT_B, KEEP = "99999999C202500001", "99999999C202500002", "99999999C202600003"


def link(*ids):
    return [{"id": i} for i in ids]


class FakeCloud:
    """The three tables as the full-page list read returns them; records the delete requests."""

    def __init__(self, **behaviour):
        self.b = behaviour
        self.rows = {
            MAIN: {
                "recMA": {M_KEY: OUT_A, M_CHILD: link("recIA1", "recIA2")},
                "recMB": {M_KEY: OUT_B, M_CHILD: link("recIB1")},
                "recMK": {M_KEY: KEEP, M_CHILD: link("recIK1")},
            },
            INSP: {
                "recIA1": {I_KEY: f"{OUT_A}|初查", I_PARENT: link("recMA"), I_CHILD: link("recPA1")},
                "recIA2": {I_KEY: f"{OUT_A}|复查", I_PARENT: link("recMA"), I_CHILD: link("recPA2")},
                "recIB1": {I_KEY: f"{OUT_B}|初查", I_PARENT: link("recMB"), I_CHILD: link("recPB1")},
                "recIK1": {I_KEY: f"{KEEP}|初查", I_PARENT: link("recMK"), I_CHILD: link("recPK1")},
            },
            PROD: {
                "recPA1": {P_KEY: f"{OUT_A}|初查|1", P_PARENT: link("recIA1")},
                "recPA2": {P_KEY: f"{OUT_A}|复查|1", P_PARENT: link("recIA2")},
                "recPB1": {P_KEY: f"{OUT_B}|初查|1", P_PARENT: link("recIB1")},
                "recPK1": {P_KEY: f"{KEEP}|初查|1", P_PARENT: link("recIK1")},
            },
        }
        self.deleted: list[tuple[str, list[str]]] = []

    def list_rows(self, base, table, field_ids=None):
        if self.b.get("list_fails_after_delete") and self.deleted:
            raise sync.RuntimeSyncError("读回失败")
        return {rid: dict(row) for rid, row in self.rows[table].items()}

    def delete_records(self, base, table, ids):
        self.deleted.append((table, list(ids)))
        if self.b.get("timeout_lost"):
            raise sync.RuntimeSyncError("UNKNOWN: 官方 CLI 超时；效果未知，需按 record_id 回读对账")
        if self.b.get("ignored"):
            return {"ok": True}
        for rid in ids:
            del self.rows[table][rid]
        return {"ok": True}


@pytest.fixture
def ledger(tmp_path):
    led = sync.Ledger(tmp_path / "ledger", isolated_test=True)
    led.initialize()
    return led


def _snapshot(tmp_path, *, drop_child=False):
    def rec(rid):
        return {"record_id": rid}
    cases = [
        {"projectNo": OUT_A, "main": rec("recMA"), "inspect": [rec("recIA1"), rec("recIA2")], "product": [rec("recPA1"), rec("recPA2")]},
        {"projectNo": OUT_B, "main": rec("recMB"), "inspect": [rec("recIB1")], "product": [rec("recPB1")]},
    ]
    if drop_child:
        cases[0]["product"] = [rec("recPA1")]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"baseToken": BASE, "tables": {"main": MAIN, "inspect": INSP, "product": PROD}, "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


def _plan(tmp_path, snapshot, projects=(OUT_A, OUT_B)):
    plan = {
        "schemaVersion": retire.PLAN_SCHEMA, "planId": "20261111-cloud-test",
        "target": {"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
        "tables": {
            "main": {"tableId": MAIN, "keyFieldId": M_KEY, "childLinkFieldId": M_CHILD},
            "inspect": {"tableId": INSP, "keyFieldId": I_KEY, "parentLinkFieldId": I_PARENT, "childLinkFieldId": I_CHILD},
            "product": {"tableId": PROD, "keyFieldId": P_KEY, "parentLinkFieldId": P_PARENT},
        },
        "snapshot": {"path": str(snapshot), "sha256": "sha256:" + hashlib.sha256(snapshot.read_bytes()).hexdigest()},
        "cases": [{"projectNo": p} for p in projects],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path, cloud, ledger, *, execute=True, plan=None, **kw):
    plan = plan or _plan(tmp_path, _snapshot(tmp_path))
    return retire.retire(plan, execute_flag=execute, transport=cloud, ledger=ledger, log_dir=tmp_path / "logs", **kw)


def test_plan_mode_checks_the_live_cloud_and_makes_no_write(tmp_path, ledger):
    cloud = FakeCloud()
    report = _run(tmp_path, cloud, ledger, execute=False)
    assert report["status"] == "PLAN_ONLY" and report["externalWrites"] == 0 and not cloud.deleted
    assert report["counts"] == {"cases": 2, "product": 3, "inspect": 3, "main": 2}


def test_execute_deletes_children_first_then_the_case_rows_and_only_the_planned_ones(tmp_path, ledger):
    cloud = FakeCloud()
    report = _run(tmp_path, cloud, ledger)
    assert report["status"] == "OK" and report["externalWrites"] == 8
    assert [t for t, _ in cloud.deleted] == [PROD, INSP, MAIN]
    assert set(cloud.rows[MAIN]) == {"recMK"} and set(cloud.rows[INSP]) == {"recIK1"} and set(cloud.rows[PROD]) == {"recPK1"}
    assert report["liveAfter"] == {"main": 1, "inspect": 1, "product": 1}
    events = ledger.events(sync.Ledger.target_key(BASE, MAIN, "recMA"))
    assert [e["event"] for e in events] == ["intent", "final"] and events[1]["status"] == "OK"
    assert ledger.state(sync.Ledger.target_key(BASE, PROD, "recPA1"))["state"] == "CLEAR"
    assert json.loads((tmp_path / "logs" / "report.json").read_text(encoding="utf-8"))["status"] == "OK"


def test_a_child_row_that_appeared_after_the_snapshot_stops_everything_before_any_delete(tmp_path, ledger):
    cloud = FakeCloud()
    cloud.rows[INSP]["recIA3"] = {I_KEY: f"{OUT_A}|复查2", I_PARENT: link("recMA"), I_CHILD: []}
    with pytest.raises(sync.SyncError, match="检查行"):
        _run(tmp_path, cloud, ledger)
    assert not cloud.deleted


def test_a_missing_planned_child_or_wrong_key_refuses_without_touching_the_cloud(tmp_path, ledger):
    cloud = FakeCloud()
    del cloud.rows[PROD]["recPA2"]
    with pytest.raises(sync.SyncError, match="产品行"):
        _run(tmp_path, cloud, ledger)
    cloud2 = FakeCloud()
    cloud2.rows[MAIN]["recMA"][M_KEY] = "99999999C202500099"
    with pytest.raises(sync.SyncError, match="稳定键"):
        _run(tmp_path, cloud2, ledger)
    assert not cloud.deleted and not cloud2.deleted


def test_a_tampered_snapshot_or_a_plan_that_disagrees_with_it_is_refused(tmp_path, ledger):
    snapshot = _snapshot(tmp_path)
    plan = _plan(tmp_path, snapshot)
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(sync.SyncError, match="摘要"):
        _run(tmp_path, FakeCloud(), ledger, plan=plan)
    with pytest.raises(sync.SyncError, match="不在快照里"):
        _run(tmp_path, FakeCloud(), ledger, plan=_plan(tmp_path, _snapshot(tmp_path), projects=(KEEP,)))


def test_a_delete_that_is_ignored_or_times_out_is_blocked_and_never_replayed(tmp_path, ledger):
    cloud = FakeCloud(ignored=True)
    with pytest.raises(sync.RuntimeSyncError):
        _run(tmp_path, cloud, ledger)
    key = sync.Ledger.target_key(BASE, PROD, "recPA1")
    assert ledger.state(key)["state"] == "BLOCKED" and len(cloud.deleted) == 1            # 子行先试，失败即停，案卷行没动
    with pytest.raises(sync.SyncError, match="未决目标"):                                  # 再跑一次：账本按目标封锁，不重放
        _run(tmp_path, FakeCloud(), ledger)
    lost = FakeCloud(timeout_lost=True)
    other = sync.Ledger(tmp_path / "ledger2", isolated_test=True)
    other.initialize()
    with pytest.raises(sync.RuntimeSyncError):
        _run(tmp_path, lost, other)
    assert other.state(sync.Ledger.target_key(BASE, PROD, "recPA1"))["state"] == "BLOCKED"


def test_a_failed_read_back_after_the_delete_is_unknown_not_success(tmp_path, ledger):
    with pytest.raises(sync.RuntimeSyncError):
        _run(tmp_path, FakeCloud(list_fails_after_delete=True), ledger)
    assert ledger.state(sync.Ledger.target_key(BASE, PROD, "recPA1"))["state"] == "BLOCKED"


def test_execute_without_the_formal_ledger_identity_is_refused(tmp_path):
    with pytest.raises(sync.SyncError, match="账根|--ledger-dir"):
        retire.retire(_plan(tmp_path, _snapshot(tmp_path)), execute_flag=True, transport=FakeCloud(), ledger=sync.Ledger(tmp_path / "not-a-ledger"))
    with pytest.raises(sync.SyncError, match="--ledger-dir"):
        retire.retire(_plan(tmp_path, _snapshot(tmp_path)), execute_flag=True, transport=FakeCloud())


def test_the_cli_transport_builds_one_exact_high_risk_request_and_refuses_bad_ids():
    calls = []

    class Runner:
        def __call__(self, argv, **_):
            calls.append(argv)

            class R:
                returncode, stdout = 0, '{"ok": true}'

            return R()

    transport = sync.LarkCliTransport("lark-cli", "wx-product", "user", runner=Runner())
    transport.delete_records(BASE, MAIN, ["recA", "recB"])
    assert calls[0][:4] == ["lark-cli", "base", "+record-delete", "--base-token"] and calls[0].count("--record-id") == 2 and "--yes" in calls[0]
    for bad in ([], ["recA", "recA"], ["notarecord"]):
        with pytest.raises(sync.RuntimeSyncError):
            transport.delete_records(BASE, MAIN, bad)
