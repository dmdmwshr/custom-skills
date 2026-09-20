import argparse
import json
from pathlib import Path

import pytest

from scripts import existing_case
from scripts import ledger_views as views
from scripts import registry_cli as cli
from scripts import workspace_state as ws
from scripts.test_ledger_views import A, add_case, write


@pytest.fixture
def existing(tmp_path, monkeypatch):
    layout = ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))
    add_case(layout, A, deep=True)
    work = layout.work_case_dir(A)
    (work / "upload-state.json").unlink()
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": True,
            "caseId": "fixture-case",
            "origin": "https://fixture.example",
            "snapshotDigest": "sha256:" + "c" * 64,
            "fileSnapshot": {
                "fixture-file": {
                    "sha256": "sha256:" + "b" * 64,
                    "contentGeneration": 1,
                    "sizeBytes": "1",
                }
            },
        },
    )
    raw = layout.pending_case_dir(A) / "original.zip"
    raw.parent.mkdir()
    raw.write_bytes(b"immutable original collection")
    write(
        work / "inventory.json",
        {
            "inventoryVersion": 3,
            "sourceInput": str(raw),
            "containerKind": "ARCHIVE",
            "packageSha256": cli.file_sha256(raw),
            "files": [{"relativePath": "original.pdf"}],
        },
    )
    monkeypatch.setattr(cli, "resolve_source_layout", lambda args: layout)
    monkeypatch.setattr(cli, "validate_manifest", lambda manifest: [])
    monkeypatch.setattr(cli, "secure_auth_config_path", lambda p: p)
    monkeypatch.setattr(cli, "authenticate_client", lambda *a: ({}, {}))
    calls = []

    def observe(*a, **kw):
        calls.append("observation")
        return {"cases": [{"applied": True, "systemRegistered": True, "differences": []}]}

    monkeypatch.setattr(existing_case.ledger_reconcile, "reconcile", observe)

    def verify(*a, **kw):
        calls.append("body verification")
        return {"caseId": "fixture-case", "filesVerified": 1, "inspections": 1, "products": 1}

    monkeypatch.setattr(cli, "verify_with_poll", verify)
    args = argparse.Namespace(
        manifest=str(work / "manifest.json"),
        api_base="https://fixture.example",
        auth_config="fixture-auth.toml",
        timeout=1,
        recall_wait_seconds=1,
        archive=True,
    )
    return layout, work, raw, args, calls


def test_existing_formal_case_archives_and_reuses_without_upload_state(existing):
    layout, work, raw, args, calls = existing
    raw_sha = cli.file_sha256(raw)
    result = existing_case.verify(cli, args)
    assert result["serverImportWrites"] == []
    assert result["uploadStateCreated"] is False
    assert result["archiveReused"] is False
    history = result["archive"]["archivedWorkspace"]
    receipt = cli.read_json(Path(result["archive"]["verificationRecord"]))
    assert receipt["packageSha256"] == raw_sha
    assert receipt["verification"]["originalManifestPackageSha256"] != raw_sha
    assert not work.exists()
    assert views.ledger_view(layout)["cases"][0]["complete"] is True
    assert views.ledger_view(layout)["cases"][0]["files"] == {"expected": 1, "received": 1}
    args.manifest = str(Path(history) / "manifest.json")
    assert existing_case.verify(cli, args)["archiveReused"] is True
    assert not (Path(history) / "upload-state.json").exists()
    assert len(ws.load_waterline(layout)["cases"][A]["history"]["completions"]) == 1
    assert calls == ["observation", "body verification", "observation"] * 2


@pytest.mark.parametrize("source", [{"materialGaps": ["missing report"]}, {"changePending": True}])
def test_incomplete_source_preserves_verified_work_without_archiving(existing, source):
    layout, work, raw, args, calls = existing
    ws.upsert_case(layout, A, source=source)
    with pytest.raises(cli.RegistryError, match="来源材料或变化"):
        existing_case.verify(cli, args)
    assert work.is_dir() and raw.is_file()
    assert not (work / "upload-state.json").exists()


def test_changed_raw_package_does_not_archive(existing):
    layout, work, raw, args, calls = existing
    raw.write_bytes(b"changed collection")
    with pytest.raises(cli.RegistryError, match="原始材料自采集后已变化"):
        existing_case.verify(cli, args)
    assert work.is_dir() and raw.is_file()


def test_existing_upload_job_must_use_its_original_verify_path(existing):
    layout, work, raw, args, calls = existing
    add_case(layout, A, deep=True)
    with pytest.raises(cli.RegistryError, match="已有上传断点"):
        existing_case.verify(cli, args)
    assert calls == []


def test_server_content_generation_change_cannot_reuse_old_body_proof(existing):
    layout, work, raw, args, calls = existing
    ws.upsert_case(
        layout, A, systemObservation={"fileSnapshot": {"fixture-file": {"contentGeneration": 2}}}
    )
    with pytest.raises(cli.RegistryError, match="正文回执与最新服务器"):
        existing_case.verify(cli, args)
    assert work.is_dir() and raw.is_file()


def test_altered_archive_content_receipt_is_not_completed(existing):
    layout, work, raw, args, calls = existing
    result = existing_case.verify(cli, args)
    proof = Path(result["archive"]["archivedWorkspace"]) / "content-verification.json"
    value = json.loads(proof.read_text())
    value["completedAt"] = "2100-01-01T00:00:00Z"
    write(proof, value)
    assert views.ledger_view(layout)["cases"][0]["complete"] is False
