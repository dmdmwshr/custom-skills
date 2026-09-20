import hashlib
import json
from datetime import date

import pytest

from scripts import ledger_views as views
from scripts import source_intake as source
from scripts import workspace_state as ws

A = "99999999T209900001"
B = "99999999T209900002"
NOW = "2099-02-20T10:00:00+08:00"


@pytest.fixture
def layout(tmp_path):
    return ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def add_case(layout, project, result="UNQUALIFIED", archived=False, deep=False):
    work = layout.work_case_dir(project)
    generation = "20990220T020000Z-aaaaaaaaaaaa"
    if archived:
        work = layout.root / "工作区" / "历史工作区" / f"{project}-{generation}"
    manifest = {
        "case": {"projectNo": project},
        "packageSha256": "sha256:" + "a" * 64,
        "initialInspection": {
            "inspectionDate": "2098-12-10",
            "products": [{"result": result, "method": "ONSITE"}],
        },
        "files": [{"clientRef": "file-a", "sha256": "sha256:" + "b" * 64}],
    }
    write(work / "manifest.json", manifest)
    digest = "sha256:" + hashlib.sha256((work / "manifest.json").read_bytes()).hexdigest()
    state = {
        "stateVersion": 6,
        "projectNo": project,
        "manifestSha256": digest,
        "caseId": "fixture-case",
        "origin": "https://fixture.example",
        "status": "VERIFIED",
        "finalizeSummary": {"created": True, "conflictCount": 0, "skippedCount": 0},
        "filesProjection": [{"clientRef": "file-a"}],
        "uploadedFileRefs": ["file-a"],
    }
    write(work / "upload-state.json", state)
    archive = {}
    if archived:
        receipt_path = layout.verification_records / f"{project}-receipt.json"
        write(
            receipt_path,
            {
                "recordVersion": 1,
                "projectNo": project,
                "manifestSha256": digest,
                "archivedWorkspace": str(work),
            },
        )
        archive = {"workspacePath": str(work), "verificationRecord": str(receipt_path)}
    if deep:
        write(
            work / "content-verification.json",
            {
                "schemaVersion": "ContentVerificationV1",
                "manifestSha256": digest,
                "completedAt": NOW,
                "caseId": state["caseId"],
                "origin": state["origin"],
                "files": {
                    "file-a": {
                        "sha256": "sha256:" + "b" * 64,
                        "fileId": "fixture-file",
                        "verifiedAgainstManifestSha256": digest,
                        "verifiedAt": NOW,
                        "contentGeneration": 1,
                    }
                },
            },
        )
    ws.upsert_case(
        layout,
        project,
        state="COMPLETED" if archived else "UPLOADED_AWAITING_NAS",
        source={
            "status": "PACKAGE_READY",
            "rwid": project,
            "batchId": "first",
            "projectIdentitySource": "DETAIL",
        },
        upload={"status": "VERIFIED"},
        nasVerification={"status": "VERIFIED"},
        archive=archive,
    )


def test_all_and_unqualified_share_identity_and_include_archived_file_counts(layout):
    add_case(layout, A, archived=True, deep=True)
    add_case(layout, B, result="QUALIFIED")
    ws.upsert_case(layout, A, source={"batchId": "second"})
    full = views.ledger_view(layout)
    subset = views.ledger_view(layout, view="unqualified")
    assert full["counts"]["cases"] == 2
    assert full["counts"]["receivedFiles"] == 2
    assert subset["counts"]["cases"] == 1
    assert subset["cases"][0]["projectNo"] == A
    assert subset["cases"][0]["complete"] is True
    assert subset["cases"][0]["statisticsYear"] == 2098
    assert ws.load_waterline(layout)["cases"][A]["source"]["batchIds"] == ["first", "second"]


def test_legacy_verified_is_not_deep_content_proof(layout):
    add_case(layout, A, archived=True)
    row = views.ledger_view(layout)["cases"][0]
    assert row["historicalComplete"] is True
    assert row["stages"]["bodyVerified"] is False
    assert row["complete"] is False
    assert views.scan_plan(layout)["pending"] == []  # no automatic historical re-download


def test_workflow_mutation_does_not_refresh_source_observation(layout):
    ws.upsert_case(layout, A, source={"lastObservedAt": NOW})
    ws.upsert_case(layout, A, upload={"status": "UPLOADING"})
    record = ws.load_waterline(layout)["cases"][A]
    assert record["lastSeenAt"] == NOW
    assert record["source"]["lastObservedAt"] == NOW
    assert record["workflowUpdatedAt"]


def test_rolling_calendar_window_handles_year_and_short_months():
    assert views.recent_window(date(2026, 9, 20)) == {
        "startDate": "2026-06-20",
        "endDate": "2026-09-20",
    }
    assert views.recent_window(date(2024, 5, 31))["startDate"] == "2024-02-29"
    assert views.recent_window(date(2026, 2, 20))["startDate"] == "2025-11-20"


def test_reinspection_result_and_recheck_are_independent():
    manifest = {
        "initialInspection": {
            "products": [
                {
                    "method": "SAMPLING",
                    "result": "UNQUALIFIED",
                    "reinspectionApplied": "YES",
                    "reinspectionResult": "QUALIFIED",
                }
            ]
        },
        "recheckInspection": {"products": [{"result": "UNQUALIFIED"}]},
    }
    assert views.qualification(manifest)["initialResult"] == "QUALIFIED"
    manifest["initialInspection"]["products"][0]["reinspectionResult"] = "PENDING"
    assert views.qualification(manifest)["initialResult"] == "UNKNOWN"
    manifest["initialInspection"]["products"].append({"method": "ONSITE", "result": "UNQUALIFIED"})
    assert views.qualification(manifest)["initialResult"] == "UNQUALIFIED"


def test_incremental_contract_all_types_and_old_pending_survives(layout):
    filters = {**views.scan_filters(date(2099, 2, 20)), "queryEvidencePath": "query.json"}
    clean = source._default_filters(NOW, filters)
    assert clean["startDate"] == "2098-11-20"
    assert clean["documentType"] == "ALL"
    with pytest.raises(source.SourceIntakeError):
        source._default_filters(NOW, {**filters, "documentType": "责令限期改正通知书"})
    add_case(layout, A)
    assert views.scan_plan(layout)["pending"][0]["projectNo"] == A


def test_known_documents_skip_without_entering_detail_and_new_document_does_not(layout):
    add_case(layout, A, archived=True)
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    ws.upsert_case(
        layout,
        A,
        source={
            "rwid": "fixture-rwid",
            "observationsByRwid": {
                "fixture-rwid": {"documentFingerprints": [source._fingerprint(appearance)]}
            },
        },
    )
    state = {"filters": {"selectionMode": "RECENT_DOCUMENT_ACTIVITY"}, "batchId": "recent"}
    records = {
        "fixture-rwid": {"rwid": "fixture-rwid", "fields": {}, "sourceAppearances": [appearance]}
    }
    source._prepare_incremental_queue(layout, state, records, NOW)
    assert state["actionRwids"] == []
    assert records["fixture-rwid"]["skippedAsUnchanged"] is True
    records = {
        "fixture-rwid": {
            "rwid": "fixture-rwid",
            "fields": {},
            "sourceAppearances": [appearance, {"文书名称": "复查记录", "创建日期": "2099-02-20"}],
        }
    }
    source._prepare_incremental_queue(layout, state, records, NOW)
    assert state["actionRwids"] == ["fixture-rwid"]


def test_bad_archive_reference_cannot_supply_registration_evidence(layout):
    ws.upsert_case(layout, A, state="COMPLETED", archive={"workspacePath": str(layout.root.parent)})
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["systemRegistered"] is False
    assert row["issues"] == ["ARCHIVE_REFERENCE_INVALID"]
