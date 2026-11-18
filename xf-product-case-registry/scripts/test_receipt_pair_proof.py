"""a33: an original create run left requests/responses but no report.json and no ledger terminal (the ledger file was briefly unwritable).
Strict recovery from the paired raw logs, and the durable ledger spool that prevents the situation from losing evidence again."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.feishu_source_sync as sync
from scripts.test_feishu_source_sync import (  # noqa: F401  (the autouse fixture gives the engine tests their own isolated ledger)
    _engine_tests_exercise_the_release_mechanics_not_the_user_gate,
)
from scripts.test_feishu_source_sync import BASE, CASE, _creation_history, _late_create, _reconcile, _run_v3, _wire_case, sha, v3_spec

VALUE = "32002299C202600001"


def _stranded(tmp_path, *, siblings: bool = False):
    """Real-shape stranded run: intent in the ledger, the service created the row and answered, but no final/bound and no report.json."""
    contract, ledger, transport, key = _late_create(tmp_path, no_receipt_id=True)
    led = sync.Ledger(ledger)
    intent = led.state(key)["lastIntent"]
    log_dir = Path(intent["logDir"])
    path = led._events_path(key)
    path.write_text(json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")        # only the intent line survives
    record_id = next(rid for (t, rid) in transport.rows if t == CASE)
    request = json.loads((log_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    receipt = {"ok": True, "identity": "user", "data": {"created": True, "record": {"record_id": record_id}}}
    (log_dir / "responses.jsonl").write_text(json.dumps({"at": request["at"], "stableKey": VALUE, "recordId": record_id, "rawReceipt": receipt}) + "\n", encoding="utf-8")
    if (log_dir / "report.json").exists():
        (log_dir / "report.json").unlink()
    transport.b["history"] = _creation_history()
    return contract, ledger, transport, key, log_dir, intent, record_id


def _pair(log_dir, intent, **override):
    ref = {"kind": "service-receipt-log-pair", "logDir": str(log_dir), "intentId": intent["intentId"],
           "requestsSha256": sha(log_dir / "requests.jsonl"), "responsesSha256": sha(log_dir / "responses.jsonl")}
    ref.update(override)
    return [ref]


def test_paired_raw_logs_clear_only_the_exact_stranded_intent_and_a_rerun_writes_nothing(tmp_path):
    contract, ledger, transport, key, log_dir, intent, record_id = _stranded(tmp_path)
    before = sync.Ledger(ledger)._events_path(key).read_bytes()
    target = _reconcile(tmp_path, contract, ledger, transport, "pair-ok", requestTerminalProof=_pair(log_dir, intent))
    assert target["outcome"] == "APPLIED" and target["observed"]["verifiedProof"][0]["kind"] == "service-receipt-log-pair"
    assert target["observed"]["verifiedProof"][0]["recordId"] == record_id
    led = sync.Ledger(ledger)
    assert led.state(key)["state"] == "CLEAR" and len(transport.creates) == 1
    after = led._events_path(key).read_bytes()
    assert after.startswith(before)                                              # the original intent line is preserved byte for byte
    assert [e["event"] for e in led.events(key)] == ["intent", "reconciled"]
    again = _reconcile(tmp_path, contract, ledger, transport, "pair-again", requestTerminalProof=_pair(log_dir, intent))
    assert again["outcome"] == "NOTHING_TO_RECONCILE" and led._events_path(key).read_bytes() == after and len(transport.creates) == 1


@pytest.mark.parametrize("name", ["digest", "intent", "dir", "report", "final", "two-answers", "other-record", "payload", "late-answer", "history-early", "history-none", "sibling"])
def test_paired_raw_logs_are_refused_when_any_link_in_the_chain_does_not_hold(tmp_path, name):
    contract, ledger, transport, key, log_dir, intent, record_id = _stranded(tmp_path)
    led = sync.Ledger(ledger)
    proof = _pair(log_dir, intent)
    responses = (log_dir / "responses.jsonl")
    requests = (log_dir / "requests.jsonl")
    answer = json.loads(responses.read_text(encoding="utf-8").splitlines()[0])
    if name == "digest":
        proof = _pair(log_dir, intent, requestsSha256="0" * 64)
    elif name == "intent":
        proof = _pair(log_dir, intent, intentId="f" * 64)
    elif name == "dir":
        proof = _pair(log_dir, intent, logDir=str(tmp_path))
    elif name == "report":
        (log_dir / "report.json").write_text("{}", encoding="utf-8")
    elif name == "final":
        led.append(key, {"event": "final", "intentId": intent["intentId"], "runId": intent["runId"], "status": "UNKNOWN", "detail": ""})
    elif name == "two-answers":
        responses.write_text(responses.read_text(encoding="utf-8") * 2, encoding="utf-8")
    elif name == "other-record":
        answer["rawReceipt"]["data"]["record"]["record_id"] = "recOTHER000001"
        answer["recordId"] = "recOTHER000001"
        responses.write_text(json.dumps(answer) + "\n", encoding="utf-8")
    elif name == "payload":
        request = json.loads(requests.read_text(encoding="utf-8").splitlines()[0])
        request["fields"][next(iter(request["fields"]))] = "tampered"
        requests.write_text(json.dumps(request) + "\n", encoding="utf-8")
    elif name == "late-answer":
        answer["at"] = "2099-01-01T00:00:00Z"
        responses.write_text(json.dumps(answer) + "\n", encoding="utf-8")
    elif name == "history-early":
        transport.b["history"] = _creation_history(offset=-3600)
    elif name == "history-none":
        del transport.b["history"]
    elif name == "sibling":
        request = json.loads(requests.read_text(encoding="utf-8").splitlines()[0])
        other = dict(request, stableKey={"fieldId": request["stableKey"]["fieldId"], "value": "32002299C202600002"})
        requests.write_text(requests.read_text(encoding="utf-8") + json.dumps(other) + "\n", encoding="utf-8")
        responses.write_text(responses.read_text(encoding="utf-8") + json.dumps({"at": request["at"], "stableKey": "32002299C202600002", "recordId": "recSIBLING0001",
                                                                                 "rawReceipt": {"ok": True, "data": {"created": True, "record": {"record_id": "recSIBLING0001"}}}}) + "\n", encoding="utf-8")
        proof = _pair(log_dir, intent)
    if name not in {"digest", "intent", "dir"}:
        proof = [dict(proof[0], requestsSha256=sha(requests), responsesSha256=sha(responses))]
    target = _reconcile(tmp_path, contract, ledger, transport, "pair-" + name, requestTerminalProof=proof)
    assert target["outcome"] in {"ONE_MATCH_NEEDS_TERMINAL_PROOF", "MULTIPLE_MATCH"} and led.state(key)["state"] == "BLOCKED"
    assert len(transport.creates) == 1


def test_a_sibling_create_in_the_same_log_must_be_corroborated_by_its_own_ledger(tmp_path):
    contract, ledger, transport, key, log_dir, intent, record_id = _stranded(tmp_path)
    led = sync.Ledger(ledger)
    requests, responses = log_dir / "requests.jsonl", log_dir / "responses.jsonl"
    request = json.loads(requests.read_text(encoding="utf-8").splitlines()[0])
    sibling = "32002299C202600002"
    other = dict(request, stableKey={"fieldId": request["stableKey"]["fieldId"], "value": sibling})
    requests.write_text(requests.read_text(encoding="utf-8") + json.dumps(other) + "\n", encoding="utf-8")
    responses.write_text(responses.read_text(encoding="utf-8") + json.dumps({"at": request["at"], "stableKey": sibling, "recordId": "recSIBLING0001",
                                                                             "rawReceipt": {"ok": True, "data": {"created": True, "record": {"record_id": "recSIBLING0001"}}}}) + "\n", encoding="utf-8")
    sibling_key = sync.Ledger.target_key(BASE, CASE, "create:" + sibling)
    led.append(sibling_key, {"event": "intent", "kind": "create", "intentId": "s1", "runId": intent["runId"], "logDir": intent["logDir"], "stableKey": other["stableKey"]})
    led.append(sibling_key, {"event": "bound", "intentId": "s1", "runId": intent["runId"], "recordId": "recSIBLING0001"})
    target = _reconcile(tmp_path, contract, ledger, transport, "pair-sib", requestTerminalProof=_pair(log_dir, intent))
    assert target["outcome"] == "APPLIED", target["observed"]
    assert target["observed"]["verifiedProof"][0]["corroboratedSiblings"] == [sibling]


# ------------------------------------------------------------------ durable ledger: transient file errors never lose or hide a service answer


def _locked(monkeypatch, *, events=("bound", "final"), also_intent=False):
    real = sync.Ledger.__dict__["_write_line"].__func__
    monkeypatch.setattr(_locked, "real", real, raising=False)
    names = set(events) | ({"intent"} if also_intent else set())

    def flaky(path, line):
        if "/targets/" in str(path).replace("\\", "/") and any(f'"event": "{n}"' in line for n in names):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, line)

    monkeypatch.setattr(sync.Ledger, "_write_line", staticmethod(flaky))
    monkeypatch.setattr(sync, "LEDGER_APPEND_RETRY_DELAYS", (0.0, 0.0))


def test_a_ledger_error_after_the_service_answered_is_spooled_counted_and_never_resent(tmp_path, monkeypatch):
    contract, ledger = tmp_path / "c.json", tmp_path / "ledger"
    contract = __import__("scripts.test_feishu_source_sync", fromlist=["make_v3_contract"]).make_v3_contract(tmp_path)
    transport = __import__("scripts.test_feishu_source_sync", fromlist=["MemoryTransport"]).MemoryTransport()
    _locked(monkeypatch)
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    key = sync.Ledger.target_key(BASE, CASE, "create:" + VALUE)
    led = sync.Ledger(ledger)
    assert report["status"] == "OK" and report["ledgerSpooled"] == [VALUE] and len(transport.creates) == 1
    assert (ledger / "pending" / f"{key}.jsonl").is_file()
    assert [e["event"] for e in led.events(key)] == ["intent", "bound", "final"] and led.state(key)["state"] == "CLEAR"
    assert (tmp_path / "log1" / "report.json").is_file()
    monkeypatch.setattr(sync.Ledger, "_write_line", staticmethod(_locked.real))     # the ledger file is writable again
    rerun = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="log2"), transport, ledger, execute=True)
    assert len(transport.creates) == 1 and rerun["records"][0]["status"] in {"EXISTING_VERIFIED", "EXISTING_MATCHES", "EXISTING_DIVERGED"} or len(transport.creates) == 1
    assert not (ledger / "pending" / f"{key}.jsonl").exists()                       # moved back into the ledger when the next run took its lock
    assert [e["event"] for e in sync.Ledger(ledger).events(key)] == ["intent", "bound", "final"]


def test_a_persistent_error_on_the_intent_sends_nothing_and_leaves_no_pending_intent(tmp_path, monkeypatch):
    mod = __import__("scripts.test_feishu_source_sync", fromlist=["x"])
    contract, ledger, transport = mod.make_v3_contract(tmp_path), tmp_path / "ledger", mod.MemoryTransport()
    _locked(monkeypatch, events=(), also_intent=True)
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    key = sync.Ledger.target_key(BASE, CASE, "create:" + VALUE)
    assert not transport.creates and sync.Ledger(ledger).state(key)["state"] == "CLEAR"
    report = json.loads((tmp_path / "log1" / "report.json").read_text(encoding="utf-8"))
    assert report["records"][0]["status"] == "LEDGER_UNAVAILABLE_NOT_SENT" and report["status"] == "FAILED"


def test_an_interrupted_run_always_leaves_a_report(tmp_path, monkeypatch):
    mod = __import__("scripts.test_feishu_source_sync", fromlist=["x"])
    contract, ledger, transport = mod.make_v3_contract(tmp_path), tmp_path / "ledger", mod.MemoryTransport()

    def boom(*_a, **_k):
        raise KeyboardInterrupt("simulated")

    monkeypatch.setattr(transport, "create_record", boom)
    with pytest.raises(KeyboardInterrupt):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    report = json.loads((tmp_path / "log1" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "ABORTED" and "KeyboardInterrupt" in report["error"]


# ------------------------------------------------------------------ 规划只精确剔除被封锁的一案，其余合法动作照常

def _two_case_plan(tmp_path):
    from scripts.test_feishu_source_sync import PN_FILL, _fieldplan_setup
    v3, v9, water, plan, transport = _fieldplan_setup(tmp_path)
    data = json.loads(plan.read_text(encoding="utf-8"))
    other = json.loads(json.dumps(data["entries"][0]))
    other["projectNo"], other["recordId"] = "32002299C202600777", "recEXIST000002"
    other["stableKey"]["value"] = "32002299C202600777"
    data["entries"].append(other)
    plan.write_text(json.dumps(data), encoding="utf-8")
    transport.rows[(CASE, "recEXIST000002")] = {**transport.rows[(CASE, "recEXIST000001")], sync_key(): "32002299C202600777"}
    return v9, water, plan, transport, PN_FILL


def sync_key():
    from scripts.test_feishu_source_sync import KEY
    return KEY


def test_field_plan_leaves_out_only_the_case_with_an_unresolved_create_and_still_builds_the_other(tmp_path):
    v9, water, plan, transport, blocked_case = _two_case_plan(tmp_path)
    ledger_dir = tmp_path / "ledger"
    led = sync.Ledger(ledger_dir, isolated_test=True)
    led.initialize()
    led.append(sync.Ledger.target_key(BASE, CASE, "create:" + blocked_case), {"event": "intent", "kind": "create", "intentId": "i-open", "runId": "r", "logDir": "x"})
    report = sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9),
                                         target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                         out_dir=tmp_path / "specs", log_root=tmp_path / "logs", ledger_dir=ledger_dir, isolated_test_ledger=True)
    assert report["status"] == "PARTIAL" and report["counts"]["toWrite"] == 1 and report["counts"]["refused"] == 1
    assert report["refused"][0]["projectNo"] == blocked_case and "同案稳定键的创建仍未决" in json.dumps(report["refused"], ensure_ascii=False)
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    assert [u["stableKey"]["value"] for u in spec["updates"]] == ["32002299C202600777"]
