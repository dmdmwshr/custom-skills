import copy
import unittest

import pytest

from scripts.source_facts import resolve_station, validate_snapshot


class SourceFactsTests(unittest.TestCase):
    def snapshot(self):
        return {
            "formatVersion": "SourceFactsSnapshotV1",
            "observations": [{
                "observationId": "synthetic-task-1",
                "projectNo": "00000000C202600001",
                "rwid": "synthetic-rwid",
                "level": "TASK", "taskKind": "synthetic", "taskId": "task-1",
                "facts": {"statusRaw": "source raw status", "handlersRaw": "handler A (primary)"},
                "provenance": {
                    "entry": "synthetic visible page",
                    "method": "VISIBLE_DOM", "observedAt": "2026-11-06T17:00:00+08:00",
                    "evidence": [{"relativePath": "synthetic.json", "sha256": "0" * 64}],
                },
            }],
        }

    def test_different_task_states_preserved_without_input_mutation(self):
        data = self.snapshot()
        other = copy.deepcopy(data["observations"][0])
        other.update(observationId="synthetic-task-2", taskId="task-2")
        other["facts"]["statusRaw"] = "other source status"
        data["observations"].append(other)
        before = copy.deepcopy(data)
        self.assertEqual(validate_snapshot(data)["status"], "ok")
        self.assertEqual(data, before)

    def test_task_identity_required(self):
        data = self.snapshot()
        del data["observations"][0]["taskId"]
        self.assertEqual(validate_snapshot(data)["status"], "error")

    def test_internal_method_and_unknown_raw_rejected(self):
        data = self.snapshot()
        data["observations"][0]["provenance"]["method"] = "VUE_INTERNAL"
        data["observations"][0]["facts"]["statusRaw"] = None
        self.assertEqual(validate_snapshot(data)["status"], "error")

    def test_legacy_unknown_time_warns_without_claiming_current(self):
        data = self.snapshot()
        data["observations"][0]["provenance"].update(method="LEGACY_VISIBLE_EXPORT", observedAt=None)
        report = validate_snapshot(data)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["warnings"])
        self.assertFalse(report["sourceTruthVerified"])

    def test_path_escape_rejected(self):
        data = self.snapshot()
        data["observations"][0]["provenance"]["evidence"][0]["relativePath"] = "../private.json"
        self.assertEqual(validate_snapshot(data)["status"], "error")

    def test_malformed_values_return_errors(self):
        for field in ("level", "projectNo"):
            for value in ([], {}):
                data = self.snapshot()
                data["observations"][0][field] = value
                data["observations"][0]["stationAssertions"] = [
                    {"scope": [], "value": {}, "evidenceRef": "synthetic.json"}
                ]
                self.assertEqual(validate_snapshot(data)["status"], "error")
        data = self.snapshot()
        data["observations"][0]["provenance"]["method"] = []
        self.assertEqual(validate_snapshot(data)["status"], "error")

    def test_tri_state_combination_and_conflict(self):
        self.assertEqual(resolve_station([])["value"], "UNKNOWN")
        self.assertEqual(resolve_station([{"scope": "PETROL", "value": "NO"}])["value"], "UNKNOWN")
        negative = [{"scope": "PETROL", "value": "NO"}, {"scope": "GAS", "value": "NO"}]
        self.assertEqual(resolve_station(negative)["value"], "NO")
        conflict = [{"scope": "COMBINED", "value": "NO"}, {"scope": "GAS", "value": "YES"}]
        self.assertEqual(resolve_station(conflict)["value"], "YES")
        self.assertTrue(resolve_station(conflict)["needsReview"])


PROJECT = "32002205C202600113"


def _write_evidence(root, name="detail.json", data=b"{}"):
    import hashlib

    (root / name).write_bytes(data)
    return {"relativePath": name, "sha256": hashlib.sha256(data).hexdigest()}


def _snapshot(root, observation_id="obs-1", project=PROJECT, status="processing", assertions=None):
    ref = _write_evidence(root)
    item = {
        "observationId": observation_id, "projectNo": project, "rwid": "32100988338",
        "level": "TASK", "taskKind": "daily", "taskId": "task-1",
        "facts": {"statusRaw": status, "handlersRaw": "A(main)、B", "mainHandlerMarkRaw": "(main)"},
        "provenance": {"entry": "visible detail", "method": "VISIBLE_DOM",
                       "observedAt": "2026-11-07T08:00:00+08:00", "evidence": [ref]},
    }
    if assertions is not None:
        item["stationAssertions"] = [
            {"scope": scope, "value": value, "evidenceRef": ref["relativePath"]}
            for scope, value in assertions
        ]
    return {"formatVersion": "SourceFactsSnapshotV1", "observations": [item]}


def test_merge_is_append_only_and_leaves_other_layers_untouched(tmp_path):
    from scripts import source_facts as sf

    first = _snapshot(tmp_path)
    store, counts = sf.merge_snapshot(None, first, PROJECT, "t1")
    assert counts == {"added": 1, "reused": 0}
    store["manualVerification"] = {"note": "human value"}
    store["internalManagement"] = {"step": "internal"}
    again, counts = sf.merge_snapshot(store, first, PROJECT, "t2")
    assert counts == {"added": 0, "reused": 1}
    assert again["manualVerification"] == {"note": "human value"}
    assert again["internalManagement"] == {"step": "internal"}
    second = _snapshot(tmp_path, observation_id="obs-2", status="closed")
    second["observations"][0]["taskId"] = "task-2"
    merged, counts = sf.merge_snapshot(again, second, PROJECT, "t3")
    assert counts == {"added": 1, "reused": 0}
    statuses = [o["observation"]["facts"]["statusRaw"] for o in merged["sourceFacts"]["observations"]]
    assert statuses == ["processing", "closed"]  # earlier task state is never overwritten
    changed = _snapshot(tmp_path, status="rewritten")
    with pytest.raises(sf.SourceFactsConflict):
        sf.merge_snapshot(merged, changed, PROJECT, "t4")
    with pytest.raises(sf.SourceFactsConflict):
        sf.merge_snapshot(None, first, "32002205C202600114", "t5")


def test_station_activity_tri_state_keeps_history_and_unknown_without_evidence(tmp_path):
    from scripts import source_facts as sf

    assert sf.station_activity(None)["value"] == "UNKNOWN"
    store, _ = sf.merge_snapshot(None, _snapshot(tmp_path), PROJECT, "t1")
    assert sf.station_activity(store)["value"] == "UNKNOWN"  # no source evidence is not NO
    yes = _snapshot(tmp_path, observation_id="obs-y", assertions=[("COMBINED", "YES")])
    store, _ = sf.merge_snapshot(store, yes, PROJECT, "t2")
    assert sf.station_activity(store)["value"] == "YES"
    no = _snapshot(tmp_path, observation_id="obs-n", assertions=[("COMBINED", "NO")])
    store, _ = sf.merge_snapshot(store, no, PROJECT, "t3")
    activity = sf.station_activity(store)
    assert activity["needsReview"] is True
    assert [h["value"] for h in activity["history"]] == ["YES", "NO"]


def _ingest(tmp_path, snapshot, work, evidence_root):
    import argparse
    import json

    from scripts.registry_cli import source_facts_ingest_command

    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    source_facts_ingest_command(
        argparse.Namespace(snapshot=str(path), work_dir=str(work), evidence_root=str(evidence_root))
    )


def test_ingest_command_writes_source_layer_only_and_rejects_bad_input(tmp_path, capsys):
    import json

    from scripts import source_facts as sf
    from scripts.registry_cli import RegistryError

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    work = tmp_path / "work" / PROJECT
    work.mkdir(parents=True)
    (work / "case-data.json").write_text('{"manual": "keep"}', encoding="utf-8")
    snapshot = _snapshot(evidence, assertions=[("COMBINED", "YES")])
    _ingest(tmp_path, snapshot, work, evidence)
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 1 and out["stationActivity"]["value"] == "YES"
    assert out["manualVerificationTouched"] is False and out["cloudWritten"] is False
    stored = (work / sf.STORE_FILE).read_bytes()
    assert (work / "case-data.json").read_text(encoding="utf-8") == '{"manual": "keep"}'
    _ingest(tmp_path, snapshot, work, evidence)  # identical rerun reuses
    assert json.loads(capsys.readouterr().out)["reused"] == 1
    assert (work / sf.STORE_FILE).read_bytes() == stored
    (evidence / "detail.json").write_bytes(b"tampered")
    with pytest.raises(RegistryError):
        _ingest(tmp_path, snapshot, work, evidence)
    other = tmp_path / "work" / "32002205C202600114"
    other.mkdir()
    (evidence / "detail.json").write_bytes(b"{}")
    with pytest.raises(RegistryError):
        _ingest(tmp_path, snapshot, other, evidence)  # observation belongs to another project
    assert (work / sf.STORE_FILE).read_bytes() == stored and not (other / sf.STORE_FILE).exists()


if __name__ == "__main__":
    unittest.main()
