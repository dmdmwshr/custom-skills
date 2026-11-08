import hashlib
import json
from datetime import date
from pathlib import Path

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
                "projectNo": project,
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
                        "sizeBytes": "1",
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


def test_existing_case_body_proof_is_independent_from_local_upload_job(layout):
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
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["systemRegistered"] is True
    assert row["stages"]["bodyVerified"] is True
    assert row["stages"]["archived"] is False
    assert row["complete"] is False
    assert not (work / "upload-state.json").exists()
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "fileSnapshot": {"fixture-file": {"contentGeneration": 2}},
        },
    )
    assert views.ledger_view(layout)["cases"][0]["stages"]["bodyVerified"] is False


def test_body_proof_cannot_be_borrowed_from_another_project(layout):
    add_case(layout, A, deep=True)
    path = layout.work_case_dir(A) / "content-verification.json"
    proof = json.loads(path.read_text(encoding="utf-8"))
    proof["projectNo"] = B
    write(path, proof)
    assert views.ledger_view(layout)["cases"][0]["stages"]["bodyVerified"] is False


def attachment_bundle(layout):
    import copy

    add_case(layout, A, archived=True, deep=True)
    record = ws.load_waterline(layout)["cases"][A]
    work = Path(record["archive"]["workspacePath"])
    manifest = json.loads((work / "manifest.json").read_text())
    state = json.loads((work / "upload-state.json").read_text())
    expanded = copy.deepcopy(manifest)
    expanded["files"].append({"clientRef": "added", "sha256": "sha256:" + "d" * 64})
    expanded["otherAttachments"] = [
        {"clientRef": "attachment-added", "slotCode": "OTHER_ATTACHMENT",
         "title": "Penalty decision", "fileRef": "added"}
    ]
    path = work / "additional" / "manifest.json"
    proof_path = path.with_name("content-verification.json")
    write(path, expanded)
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    proof = json.loads((work / "content-verification.json").read_text())
    proof["manifestSha256"] = digest
    proof["files"]["added"] = {
        "sha256": "sha256:" + "d" * 64,
        "fileId": "new-file",
        "contentGeneration": 1,
        "sizeBytes": "5",
        "verifiedAt": NOW,
        "verifiedAgainstManifestSha256": digest,
    }
    write(proof_path, proof)
    bundle = {
        "schemaVersion": "CaseContentVerificationBundleV1",
        "projectNo": A,
        "caseId": state["caseId"],
        "origin": state["origin"],
        "baseManifestSha256": state["manifestSha256"],
        "manifestPath": str(path),
        "manifestSha256": digest,
        "contentReceiptPath": str(proof_path),
        "contentReceiptSha256": "sha256:" + hashlib.sha256(proof_path.read_bytes()).hexdigest(),
    }
    ws.upsert_case(
        layout, A, contentVerificationBundle=bundle,
        systemObservation={
            "exists": True, "caseId": state["caseId"], "origin": state["origin"],
            "fileSnapshot": {item["fileId"]: {k: item[k] for k in (
                "sha256", "contentGeneration", "sizeBytes"
            )} for item in proof["files"].values()},
        },
    )
    return work, path, proof_path, bundle


def test_attachment_body_extension_keeps_original_import_and_archive(layout):
    work, _, _, _ = attachment_bundle(layout)
    before = {name: (work / name).read_bytes() for name in (
        "manifest.json", "upload-state.json", "content-verification.json"
    )}
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["bodyVerified"] is True
    assert row["stages"]["archived"] is True
    assert row["complete"] is True
    assert row["systemChanged"] is False
    assert before == {name: (work / name).read_bytes() for name in before}


@pytest.mark.parametrize("damage", [
    "wrong-base", "other-case", "outside-case", "manifest-hash", "proof-hash",
    "changed-fields", "removed-original", "not-attachment", "duplicate-file-id",
    "incomplete", "changed-generation", "missing-observation",
])
def test_attachment_body_extension_rejects_unbound_or_stale_evidence(layout, damage):
    work, path, proof_path, bundle = attachment_bundle(layout)
    manifest = json.loads(path.read_text())
    proof = json.loads(proof_path.read_text())
    if damage == "wrong-base":
        bundle["baseManifestSha256"] = "sha256:" + "9" * 64
    elif damage == "other-case":
        bundle["caseId"] = "unrelated"
    elif damage == "outside-case":
        path = layout.work_case_dir(B) / "manifest.json"
        proof_path = path.with_name("content-verification.json")
        bundle.update(manifestPath=str(path), contentReceiptPath=str(proof_path))
    elif damage == "changed-fields":
        manifest["initialInspection"]["products"][0]["result"] = "QUALIFIED"
    elif damage == "removed-original":
        manifest["files"] = manifest["files"][1:]
    elif damage == "not-attachment":
        manifest["otherAttachments"][0]["slotCode"] = "INITIAL_INSPECTION_RECORD"
    elif damage == "duplicate-file-id":
        proof["files"]["added"]["fileId"] = "fixture-file"
    elif damage == "incomplete":
        proof.pop("completedAt")
    write(path, manifest)
    bundle["manifestSha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    proof["manifestSha256"] = bundle["manifestSha256"]
    write(proof_path, proof)
    bundle["contentReceiptSha256"] = "sha256:" + hashlib.sha256(proof_path.read_bytes()).hexdigest()
    if damage == "manifest-hash":
        bundle["manifestSha256"] = "sha256:" + "8" * 64
    elif damage == "proof-hash":
        bundle["contentReceiptSha256"] = "sha256:" + "8" * 64
    ws.upsert_case(layout, A, contentVerificationBundle=bundle)
    if damage == "changed-generation":
        ws.upsert_case(layout, A, systemObservation={"fileSnapshot": {
            "new-file": {"contentGeneration": 2}
        }})
    elif damage == "missing-observation":
        ws.upsert_case(layout, A, systemObservation={"exists": False})
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["bodyVerified"] is False
    assert row["stages"]["archived"] is True
    assert row["historicalComplete"] is True
    assert (work / "upload-state.json").exists()


def test_older_absence_cannot_override_new_server_registration_receipt(layout):
    add_case(layout, A, deep=True)
    path = layout.work_case_dir(A) / "upload-state.json"
    state = json.loads(path.read_text())
    state["finalizedAt"] = "2099-02-20T02:00:00Z"
    write(path, state)
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": False,
            "observedAt": "2099-02-20T01:59:00Z",
        },
    )
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["systemRegistered"] is True
    assert row["stages"]["bodyVerified"] is True
    assert row["systemChanged"] is False
    ws.upsert_case(layout, A, systemObservation={"observedAt": "2099-02-20T02:01:00Z"})
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["systemRegistered"] is False
    assert row["stages"]["bodyVerified"] is False


def test_source_material_gap_keeps_independent_verified_case_pending(layout):
    add_case(layout, A, archived=True, deep=True)
    ws.upsert_case(layout, A, source={"materialGaps": [{"document": "missing source PDF"}]})
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["materialsCollected"] is False
    assert row["stages"]["systemRegistered"] is True
    assert row["stages"]["bodyVerified"] is True
    assert row["historicalComplete"] is True
    assert row["activePending"] is True
    assert row["complete"] is False
    assert "SOURCE_MATERIALS_INCOMPLETE" in row["issues"]


def test_source_material_gap_prevents_archive_even_after_body_verification(layout):
    add_case(layout, A, deep=True)
    pending = layout.pending_case_dir(A)
    pending.mkdir()
    (pending / "original.pdf").write_bytes(b"preserved")
    ws.upsert_case(layout, A, source={"materialGaps": [{"document": "missing source PDF"}]})
    before = (layout.work_case_dir(A) / "manifest.json").read_bytes()
    with pytest.raises(ws.WorkspaceStateError, match="来源材料仍有明确缺口"):
        ws.archive_verified_case(
            layout,
            A,
            verification={
                "schemaVersion": "LocalVerificationV1",
                "status": "LOCAL_VERIFIED",
                "projectNo": A,
                "manifestSha256": "sha256:" + "a" * 64,
                "packageSha256": "sha256:" + "b" * 64,
                "files": {"file:one": {"sha256": "sha256:" + "c" * 64}},
            },
            manifest_sha256="sha256:" + "a" * 64,
            package_sha256="sha256:" + "b" * 64,
        )
    assert (pending / "original.pdf").read_bytes() == b"preserved"
    assert (layout.work_case_dir(A) / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1项不合格", "UNQUALIFIED"),
        ("12项不合格", "UNQUALIFIED"),
        ("0项不合格", None),
        ("未发现不合格现象", None),
        ("不合格待复检", None),
    ],
)
def test_counted_onsite_failures_require_positive_exact_display(text, expected):
    fields = {
        "检查产品信息": [
            {
                "页面记录": {"SFCPFC": "0"},
                "检查结果": "",
                "检查结果标记": ["el-icon-error"],
                "产品质量现场检查情况": text,
            }
        ]
    }
    result = views.source_qualification(fields, [])
    assert (result["initialResult"] if result else None) == expected


@pytest.mark.parametrize(
    "text,stage,result,icon,expected",
    [
        ("1项不合格(2021081810000043)", "0", "", "el-icon-error", "UNQUALIFIED"),
        ("1项不合格（2021081810000043）", "0", "", "el-icon-error", "UNQUALIFIED"),
        ("1项不合格(认证证书编号：Z2016081814000269)", "0", "", "el-icon-error", "UNQUALIFIED"),
        ("1项不合格（认证证书编号:Z2016081814000269）", "0", "", "el-icon-error", "UNQUALIFIED"),
        ("0项不合格(认证证书编号：Z2016081814000269)", "0", "", "el-icon-error", None),
        ("1项不合格(参考：Z2016081814000269)", "0", "", "el-icon-error", None),
        ("1项不合格(认证证书编号：待复检)", "0", "", "el-icon-error", None),
        ("0项不合格(2021081810000043)", "0", "", "el-icon-error", None),
        ("1项不合格(待复检)", "0", "", "el-icon-error", None),
        ("1项不合格(2021081810000043)待复检", "0", "", "el-icon-error", None),
        ("1项不合格(2021081810000043)", "1", "[复查]", "el-icon-error", None),
        ("1项不合格(2021081810000043)", "0", "[检]", "el-icon-error", None),
        ("1项不合格(2021081810000043)", "0", "", "el-icon-success", None),
    ],
)
def test_market_access_failure_certificate_suffix_does_not_change_result_contract(
    text, stage, result, icon, expected
):
    fields = {"检查产品信息": [{
        "页面记录": {"SFCPFC": stage, "JCRQ": "2099-03-02 10:00:00"},
        "检查结果": result, "检查结果标记": [icon],
        "产品质量现场检查情况": "未检查", "市场准入检查情况": text,
    }]}
    observed = views.source_qualification(fields, [])
    assert (observed["initialResult"] if observed else None) == expected
    if observed:
        assert observed["initialInspectionDate"] == "2099-03-02"


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


def test_source_initial_onsite_failure_survives_recheck_and_sampling():
    products = [
        {
            "页面记录": {"SFCPFC": "0", "JCRQ": "2099-05-26 14:30:00", "SFHG": "1"},
            "检查结果": "",
            "检查结果标记": ["el-icon-error"],
            "产品质量现场检查情况": "不合格",
        },
        {
            "页面记录": {"SFCPFC": "0"},
            "检查结果": "[检]",
            "检查结果标记": ["el-icon-success"],
            "产品质量现场检查情况": "未发现不合格现象",
        },
        {
            "页面记录": {"SFCPFC": "1", "SFHG": "0"},
            "检查结果": "[复查]",
            "检查结果标记": ["el-icon-success"],
            "产品质量现场检查情况": "未发现不合格现象",
        },
    ]
    result = views.source_qualification({"检查产品信息": products}, [])
    assert result["initialResult"] == "UNQUALIFIED"
    assert result["statisticsYear"] == 2099
    assert views.source_qualification({"检查产品信息": products[1:]}, []) is None


@pytest.mark.parametrize(
    "change",
    [
        {"检查结果": "[检]"},
        {"检查结果": "[复检]"},
        {"检查结果": "[复查]"},
        {"页面记录": {"SFCPFC": "1"}},
        {"页面记录": {}},
        {"检查结果标记": ["el-icon-success"]},
        {"检查结果标记": []},
        {"产品质量现场检查情况": "未发现不合格现象"},
    ],
)
def test_source_notice_or_ambiguous_product_is_not_final_unqualified(change):
    product = {
        "页面记录": {"SFCPFC": "0", "SFHG": "1"},
        "检查结果": "",
        "检查结果标记": ["el-icon-error"],
        "产品质量现场检查情况": "不合格",
    }
    product.update(change)
    fields = {
        "检查产品信息": [product],
        "检查情况": "已结案[复查不合格]",
        "文书目录": ["责令限期改正通知书"],
    }
    assert views.source_qualification(fields, []) is None


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


def test_system_snapshot_values_preserve_effective_result_and_initial_year(layout):
    snapshot = {
        "inspections": [
            {
                "stage": "INITIAL_CHECK",
                "fields": {"inspectionDate": {"value": "2098-12-10", "source": "MANUAL"}},
                "products": [
                    {
                        "fields": {
                            "method": {"value": "SAMPLING"},
                            "result": {"value": "UNQUALIFIED"},
                            "reinspectionApplied": {"value": "YES"},
                            "reinspectionResult": {"value": "QUALIFIED"},
                        }
                    }
                ],
            },
            {"stage": "RECHECK", "products": [{"fields": {"result": {"value": "UNQUALIFIED"}}}]},
        ]
    }
    classification = views.snapshot_qualification(snapshot)
    assert classification["initialResult"] == "QUALIFIED"
    assert classification["statisticsYear"] == 2098
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": True,
            "caseId": "known-system-case",
            "snapshotDigest": "sha256:" + "a" * 64,
            "qualification": classification,
        },
    )
    row = views.ledger_view(layout)["cases"][0]
    assert row["initialResult"] == "QUALIFIED"
    assert row["stages"]["systemRegistered"] is True
    assert row["stages"]["materialsCollected"] is False


def test_recent_activity_keeps_archive_and_only_queues_new_document_evidence(layout):
    add_case(layout, A, result="QUALIFIED", archived=True, deep=True)
    old_document = {"文书名称": "检查记录", "创建日期": "2098-12-10"}
    ws.upsert_case(
        layout,
        A,
        source={
            "rwid": "fixture-rwid",
            "observationsByRwid": {
                "fixture-rwid": {
                    "documentFingerprints": [source.document_fingerprint(old_document)]
                }
            },
        },
    )
    archive = ws.load_waterline(layout)["cases"][A]["archive"].copy()
    work = Path(archive["workspacePath"])
    original = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    filters = {**views.scan_filters(date(2099, 2, 20)), "queryEvidencePath": "synthetic-query.json"}
    source.begin_capture(
        layout, filters, origin="https://source.example/", now=NOW, batch_id="new-doc"
    )
    row = {
        "RWID": "fixture-rwid",
        "单位名称": "测试单位",
        "文书名称": "抽样复检报告",
        "创建日期": "2099-02-20",
    }
    for round_no in (1, 2):
        source.add_page(layout, "new-doc", 1, [row], 1, 1, round_no=round_no, observed_at=NOW)
        source.finalize_capture(layout, "new-doc", now=NOW)
    detail = {
        "projectNo": A,
        "unitName": "测试单位",
        "initialInspection": {
            "inspectionDate": "2098-12-10",
            "products": [{"method": "ONSITE", "result": "UNQUALIFIED"}],
        },
    }
    source.add_detail(layout, "new-doc", "fixture-rwid", detail, captured_at=NOW)
    case = ws.load_waterline(layout)["cases"][A]
    assert case["archive"] == archive
    assert original == {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    assert not layout.work_case_dir(A).exists()
    planned = views.scan_plan(layout)["pending"]
    assert planned[0]["initialResult"] == "UNQUALIFIED"
    assert planned[0]["changeEvidence"][0]["newDocumentCount"] == 1
    evidence_path = layout.root / planned[0]["changeEvidence"][0]["evidencePath"]
    before = evidence_path.read_bytes()
    source.add_detail(layout, "new-doc", "fixture-rwid", detail, captured_at=NOW)
    assert evidence_path.read_bytes() == before
    current = views.ledger_view(layout)
    assert current["counts"]["cases"] == 1
    assert current["cases"][0]["complete"] is False
    assert current["cases"][0]["stages"]["bodyVerified"] is True


def test_excel_views_share_one_snapshot_and_keep_business_stages_independent(layout):
    from openpyxl import load_workbook

    add_case(layout, A, archived=True, deep=True)
    ws.upsert_case(
        layout,
        B,
        systemObservation={
            "exists": True,
            "caseId": "case-only-in-system",
            "snapshotDigest": "sha256:" + "b" * 64,
            "qualification": {
                "initialResult": "UNQUALIFIED",
                "initialInspectionDate": "2098-11-20",
                "statisticsYear": 2098,
                "reinspectionPending": False,
            },
        },
    )
    before = layout.waterline_json.read_bytes()
    target = ws.export_waterline_xlsx(layout)
    assert layout.waterline_json.read_bytes() == before
    with __import__("contextlib").closing(load_workbook(target)) as book:
        assert book.sheetnames[:4] == ["所有案卷", "不合格案卷", "待确认案卷", "未完成案卷"]
        assert book["所有案卷"].max_row == book["不合格案卷"].max_row == 3
        assert book["待确认案卷"].max_row == 1
        assert book["未完成案卷"]["A2"].value == B
        assert book["未完成案卷"]["G2"].value == "未确认"
        assert book["未完成案卷"]["H2"].value == "是"


def test_excel_body_proof_does_not_hide_source_material_gap(layout):
    from contextlib import closing

    from openpyxl import load_workbook

    add_case(layout, A, deep=True)
    ws.upsert_case(layout, A, source={"materialGaps": [{"code": "SOURCE_REPORT_BODY_UNAVAILABLE"}]})
    with closing(load_workbook(ws.export_waterline_xlsx(layout))) as book:
        row = book["所有案卷"]
        assert row["G2"].value == "未确认" and row["I2"].value == "是"
        assert "来源材料缺件" in row["P2"].value
        summary = {r[0].value: r[1].value for r in book["年度来源覆盖"] if r[0].value}
        assert summary["材料已采集"] == 0 and summary["历史正文已核验"] == 1


def native_qualified_fields():
    return {
        "检查情况": "已结案[合格]",
        "预定检查日期": "2098-11-20",
        "创建日期": "2098-11-21",
        "检查产品信息": [
            {
                "产品质量现场检查情况": "未发现不合格现象",
                "市场准入检查情况": "未发现不合格现象",
                "检查结果": "",
            }
        ],
    }


def test_native_source_classification_requires_exact_initial_product_evidence():
    fields = native_qualified_fields()
    observed = views.source_qualification(fields, ["INITIAL"])
    assert observed["initialResult"] == "QUALIFIED"
    assert observed["statisticsYear"] is None  # planned/created dates are not actual inspection
    assert views.source_qualification(fields, ["INITIAL", "RECHECK"]) is None
    assert views.source_qualification(fields, []) is None
    fields["检查产品信息"][0]["产品质量现场检查情况"] = ""
    assert views.source_qualification(fields, ["INITIAL"]) is None


def test_saved_source_classification_is_read_only_until_applied_and_reuses_index(layout):
    ws.upsert_case(layout, A, source={"projectIdentitySource": "DETAIL"})
    path = layout.pending_case_dir(A) / "source-evidence.json"
    write(
        path,
        {
            "projectNo": A,
            "updatedAt": NOW,
            "records": {
                "rwid": {
                    "projectNo": A,
                    "fields": native_qualified_fields(),
                    "projectInspectionStages": ["INITIAL"],
                },
            },
        },
    )
    before = layout.waterline_json.read_bytes()
    assert views.classify_saved_details(layout)[0]["initialResult"] == "QUALIFIED"
    assert layout.waterline_json.read_bytes() == before
    assert views.index_history(layout, apply=True)["classificationChanges"][0]["applied"]
    assert views.classify_saved_details(layout, apply=True) == []
    row = views.ledger_view(layout)["cases"][0]
    assert row["initialResult"] == "QUALIFIED"
    assert row["stages"]["materialsCollected"] is False
    assert row["stages"]["systemRegistered"] is False
    assert views.scan_plan(layout)["pending"] == []
    path.write_text("{}", encoding="utf-8")
    assert views.ledger_view(layout)["cases"][0]["initialResult"] == "UNKNOWN"


def test_saved_source_classification_does_not_discard_ambiguous_or_foreign_evidence(layout):
    ws.upsert_case(layout, A, source={"projectIdentitySource": "DETAIL"})
    proof = {
        "projectNo": A,
        "records": {
            "first": {
                "projectNo": A,
                "fields": native_qualified_fields(),
                "projectInspectionStages": ["INITIAL"],
            },
            "second": {
                "projectNo": A,
                "fields": native_qualified_fields(),
                "projectInspectionStages": ["RECHECK"],
            },
        },
    }
    path = layout.pending_case_dir(A) / "source-evidence.json"
    write(path, proof)
    assert views.classify_saved_details(layout, apply=True) == []
    proof["records"].pop("second")
    proof["projectNo"] = B
    write(path, proof)
    assert views.classify_saved_details(layout, apply=True) == []


def test_current_system_reinspection_overrides_old_manifest_result(layout):
    add_case(layout, A, archived=True, deep=True)
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": True,
            "caseId": "fixture-case",
            "snapshotDigest": "sha256:" + "c" * 64,
            "qualification": {
                "initialResult": "QUALIFIED",
                "initialInspectionDate": "2098-12-10",
                "statisticsYear": 2098,
                "reinspectionPending": False,
            },
        },
    )
    assert views.ledger_view(layout)["cases"][0]["initialResult"] == "QUALIFIED"
    assert views.ledger_view(layout, view="unqualified")["counts"]["cases"] == 0


def test_changed_system_generation_reopens_only_that_case_without_touching_archive(layout):
    add_case(layout, A, archived=True, deep=True)
    add_case(layout, B, archived=True, deep=True)
    archive = ws.load_waterline(layout)["cases"][A]["archive"].copy()
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": True,
            "caseId": "fixture-case",
            "snapshotDigest": "sha256:" + "c" * 64,
            "fileSnapshot": {
                "fixture-file": {
                    "sha256": "sha256:" + "b" * 64,
                    "sizeBytes": 1,
                    "contentGeneration": 2,
                }
            },
        },
    )
    current = views.ledger_view(layout)
    assert current["cases"][0]["systemChanged"] is True
    assert current["cases"][0]["stages"]["bodyVerified"] is False
    assert current["cases"][0]["historicalComplete"] is True
    assert [item["projectNo"] for item in views.scan_plan(layout)["pending"]] == [A]
    assert ws.load_waterline(layout)["cases"][A]["archive"] == archive


def test_confirmed_missing_system_case_is_not_reported_registered_from_old_state(layout):
    add_case(layout, A, archived=True, deep=True)
    ws.upsert_case(layout, A, systemObservation={"exists": False, "observedAt": NOW})
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["systemRegistered"] is False
    assert row["complete"] is False
    assert row["activePending"] is True


def test_unchanged_current_file_identity_reuses_receipt(layout):
    add_case(layout, A, archived=True, deep=True)
    ws.upsert_case(
        layout,
        A,
        systemObservation={
            "exists": True,
            "caseId": "fixture-case",
            "snapshotDigest": "sha256:" + "c" * 64,
            "fileSnapshot": {
                "fixture-file": {
                    "sha256": "sha256:" + "b" * 64,
                    "sizeBytes": "1",
                    "contentGeneration": 1,
                }
            },
        },
    )
    row = views.ledger_view(layout)["cases"][0]
    assert row["stages"]["bodyVerified"] is True
    assert row["complete"] is True
    assert views.scan_plan(layout)["pending"] == []


def test_daily_report_only_expands_pending_or_anomalous_cases(layout):
    add_case(layout, A, archived=True, deep=True)
    add_case(layout, B)
    snapshot = views.ledger_view(layout)
    daily = views.render_ledger_report(snapshot)
    assert A not in daily and B in daily
    assert "本视图 2 案" in daily
    assert A in views.render_ledger_report(snapshot, include_all=True)


def test_excel_views_give_every_header_column_a_width_and_a_tall_enough_header(layout):
    """回归：业务视图曾 18 列只给 17 项宽度(现为 22 列)，最后一列“文书提示不合格候选”回落默认窄宽度，表头在 LibreOffice 导出中被挤压；
    相邻长表头(如“历史系统已登记/历史正文已核验/本地已核验”)也因宽度不足连排。"""
    import math
    from contextlib import closing

    from openpyxl import load_workbook

    add_case(layout, A, archived=True, deep=True)
    with closing(load_workbook(ws.export_waterline_xlsx(layout))) as book:
        for name in ("所有案卷", "不合格案卷", "待确认案卷", "未完成案卷"):
            sheet = book[name]
            headers = [c.value for c in sheet[1]]
            assert len(headers) == len(views.LEDGER_COLUMN_WIDTHS) == 22
            height = sheet.row_dimensions[1].height
            for index, header in enumerate(headers, 1):
                letter = chr(64 + index)
                width = sheet.column_dimensions[letter].width
                assert width and width >= 10, (name, letter, header, width)
                need = math.ceil(sum(2 if ord(ch) > 127 else 1 for ch in header) / (width - 1))
                assert height >= 16 * need, (name, letter, header, width, height, need)
            assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref


def add_source_classified_case(layout, project, *, manifest_products, cells, digest_ok=True):
    """Local record (case-data manifest) plus a saved source classification bound to its evidence digest."""
    if manifest_products is not None:
        write(
            layout.work_case_dir(project) / "manifest.json",
            {
                "case": {"projectNo": project},
                "initialInspection": {"inspectionDate": "2098-12-10", "products": manifest_products},
                "files": [],
            },
        )
    evidence = layout.root / "证据" / f"{project}.json"
    write(
        evidence,
        {
            "projectNo": project,
            "records": {
                "rwid": {
                    "projectNo": project,
                    "fields": {
                        "检查产品信息": [
                            {"产品质量现场检查情况": quality, "市场准入检查情况": market, "检查结果": mark}
                            for quality, market, mark in cells
                        ]
                    },
                }
            },
        },
    )
    digest = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    ws.upsert_case(
        layout,
        project,
        source={
            "projectIdentitySource": "DETAIL",
            "classification": {
                "initialResult": "UNQUALIFIED",
                "initialInspectionDate": "2098-12-10",
                "statisticsYear": 2098,
                "reinspectionPending": False,
                "evidencePath": f"证据/{project}.json",
                "evidenceSha256": digest if digest_ok else "sha256:" + "0" * 64,
            },
        },
    )
    return next(r for r in views.ledger_view(layout)["cases"] if r["projectNo"] == project)


SAMPLING_PENDING = [{"method": "SAMPLING", "result": "PENDING"}]


def test_pending_sampling_record_does_not_hide_a_valid_source_conclusion(layout):
    row = add_source_classified_case(
        layout, A, manifest_products=SAMPLING_PENDING, cells=[("不合格", "未发现不合格现象", "[检]"), ("未发现不合格现象", "未发现不合格现象", "[复查]")]
    )
    assert row["initialResult"] == "UNQUALIFIED" and row["statisticsYear"] == 2098
    assert row["sourceProduct"]["unqualifiedBasis"] == "QUALITY"
    assert row["recordInitialFact"] == "抽样送检·结果待定"
    assert "CLASSIFICATION_EVIDENCE_UNTRUSTED" not in row["issues"]


def test_market_access_finding_is_never_reported_as_a_quality_finding(layout):
    row = add_source_classified_case(
        layout, A, manifest_products=[{"method": None, "result": None}], cells=[("未检查", "1项不合格", "[检]")]
    )
    assert row["initialResult"] == "UNQUALIFIED"
    summary = row["sourceProduct"]
    assert summary["unqualifiedBasis"] == "MARKET_ACCESS"
    assert summary["qualityOutcome"] == "NOT_CHECKED" and summary["marketAccessOutcome"] == "UNQUALIFIED"
    assert summary["qualityCells"] == ["未检查"] and summary["marketAccessCells"] == ["1项不合格"]
    assert row["recordInitialFact"] == ""  # 本地未登记初查事实时不打印占位


def test_quality_and_market_access_findings_are_both_reported_when_both_present(layout):
    row = add_source_classified_case(
        layout, A, manifest_products=SAMPLING_PENDING, cells=[("不合格", "1项不合格(2021081810000043)", "[检]")]
    )
    assert row["sourceProduct"]["unqualifiedBasis"] == "QUALITY_AND_MARKET_ACCESS"


def test_untrusted_source_evidence_does_not_classify_a_pending_record(layout):
    row = add_source_classified_case(layout, A, manifest_products=SAMPLING_PENDING, cells=[("不合格", "", "[检]")], digest_ok=False)
    assert row["initialResult"] == "UNKNOWN" and row["sourceProduct"] is None


def test_pending_record_without_source_classification_stays_unknown(layout):
    write(layout.work_case_dir(A) / "manifest.json", {"case": {"projectNo": A}, "initialInspection": {"inspectionDate": "2098-12-10", "products": SAMPLING_PENDING}, "files": []})
    ws.upsert_case(layout, A, source={"projectIdentitySource": "DETAIL"})
    row = views.ledger_view(layout)["cases"][0]
    assert row["initialResult"] == "UNKNOWN" and row["recordInitialFact"] == "抽样送检·结果待定"


def test_a_final_local_result_is_not_overridden_by_a_different_source_classification(layout):
    row = add_source_classified_case(
        layout, A, manifest_products=[{"method": "ONSITE", "result": "QUALIFIED"}], cells=[("不合格", "", "[检]")]
    )
    assert row["initialResult"] == "QUALIFIED"


def test_excel_keeps_record_fact_quality_market_access_and_basis_in_separate_columns(layout):
    from contextlib import closing

    from openpyxl import load_workbook

    add_source_classified_case(layout, A, manifest_products=[{"method": None, "result": None}], cells=[("未检查", "1项不合格", "[检]")])
    add_source_classified_case(layout, B, manifest_products=SAMPLING_PENDING, cells=[("不合格", "未发现不合格现象", "[检]")])
    with closing(load_workbook(ws.export_waterline_xlsx(layout))) as book:
        sheet = book["不合格案卷"]
        headers = [c.value for c in sheet[1]]
        rows = {r[0]: dict(zip(headers, r, strict=True)) for r in sheet.iter_rows(min_row=2, values_only=True)}
    assert rows[A]["不合格依据"] == "市场准入不合格" and rows[A]["来源产品质量现场检查原文"] == "未检查"
    assert rows[A]["来源市场准入检查原文"] == "1项不合格"
    assert rows[B]["不合格依据"] == "产品质量不合格" and rows[B]["本地检查记录初查事实"] == "抽样送检·结果待定"


def test_ledger_row_reads_source_facts_layer_without_writing_or_inferring(layout):
    from scripts import source_facts as sf

    add_case(layout, A)
    record = ws.load_waterline(layout)["cases"][A]
    work = layout.work_case_dir(A)
    row = views.describe(layout, A, record)["sourceFacts"]
    assert row["stored"] is False and row["stationActivity"]["value"] == "UNKNOWN"
    store, _ = sf.merge_snapshot(
        None,
        {
            "observations": [
                {
                    "observationId": "o1", "projectNo": A, "rwid": "r", "level": "TASK",
                    "taskKind": "daily", "taskId": "t",
                    "stationAssertions": [{"scope": "COMBINED", "value": "YES", "evidenceRef": "e.json"}],
                    "provenance": {"observedAt": "2099-01-01T00:00:00+08:00"},
                }
            ]
        },
        A,
        "2099-01-01T00:00:00Z",
    )
    write(work / sf.STORE_FILE, store)
    before = (work / sf.STORE_FILE).read_bytes()
    row = views.describe(layout, A, record)["sourceFacts"]
    assert row["stored"] is True and row["observationCount"] == 1
    assert row["stationActivity"]["value"] == "YES"
    assert (work / sf.STORE_FILE).read_bytes() == before
    (work / sf.STORE_FILE).write_text("not json", encoding="utf-8")
    broken = views.describe(layout, A, record)["sourceFacts"]
    assert broken["stored"] is False and broken["stationActivity"]["value"] == "UNKNOWN"


def test_ledger_stage_package_received_is_independent_of_materials_and_attach(layout):
    add_case(layout, A)
    record = ws.load_waterline(layout)["cases"][A]
    base = views.describe(layout, A, record)["stages"]
    assert "packageReceived" in base
    # 仅收到包、未接件：已收到为真，材料已采集不因此为真
    received = {"source": {"status": "PACKAGE_READY_TO_ATTACH", "downloadDelivery": {"status": "READY", "received": {"sha256": "x"}}}}
    stages = views.describe(layout, A, {**record, "source": received["source"]})["stages"]
    assert stages["packageReceived"] is True


import datetime as _dt


@pytest.mark.parametrize(
    "today,start",
    [
        ("2026-11-07", "2026-09-07"),
        ("2026-03-31", "2026-01-31"),
        ("2026-04-30", "2026-02-28"),
        ("2028-04-29", "2028-02-29"),
        ("2027-04-30", "2027-02-28"),
        ("2026-01-15", "2025-11-15"),
        ("2026-02-28", "2025-12-28"),
    ],
)
def test_rolling_two_month_window_clamps_month_end_and_crosses_year(today, start):
    window = views.rolling_window(2, _dt.date.fromisoformat(today))
    assert window == {"startDate": start, "endDate": today}


def _facts(**over):
    base = {"hasPackage": True, "packageValid": True, "attachedAt": "2026-10-01T10:00:00+08:00",
            "sourceStatus": "OPEN", "initialResult": "UNQUALIFIED",
            "checkDate": _dt.date(2026, 10, 20), "dateIssue": None, "localComplete": False}
    return {**base, **over}


TODAY = _dt.date(2026, 11, 7)


def _code(**over):
    return views.package_decision(_facts(**over), today=TODAY)["code"]


def test_package_decision_default_reuses_and_never_downloads_for_missing_material():
    assert _code(sourceStatus="CLOSED") == "PKG_REUSE_CLOSED"
    assert _code(sourceStatus=None) == "PKG_REUSE_EXISTING"          # 状态未知不妨碍复用
    assert _code(checkDate=None, dateIssue="DATE_UNKNOWN") == "PKG_REUSE_EXISTING"
    assert _code(initialResult="QUALIFIED") == "PKG_REUSE_EXISTING"       # 合格案已有包也复用(不再仅索引)
    assert _code(initialResult="UNKNOWN") == "PKG_REUSE_EXISTING"
    assert _code(hasPackage=False, initialResult="UNKNOWN") == "PKG_ACQUIRE_FIRST"   # 结果未知无包：首次获取，不暂缓
    assert _code(hasPackage=False, initialResult="QUALIFIED") == "PKG_ACQUIRE_FIRST"
    assert _code(hasPackage=False, sourceStatus="CLOSED") == "PKG_ACQUIRE_FIRST"
    assert _code(packageValid=False) == "PKG_BAD_LOCAL"
    assert _code(packageValid=False, packageIssue="FILE_MISSING") == "PKG_BAD_LOCAL"
    assert _code(packageValid=False, packageIssue="UNKNOWN") == "PKG_HOLD_PACKAGE_UNKNOWN"   # 无法确认不是坏包
    assert _code(packageValid=False, packageIssue="AMBIGUOUS_CHAIN") == "PKG_HOLD_PACKAGE_UNKNOWN"


def test_package_decision_recent_unqualified_open_is_candidate_even_when_complete():
    assert _code() == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"
    assert _code(localComplete=True) == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"  # complete 不盖过合法更新


def test_package_decision_interval_window_edges_and_unknown_dates():
    # 7 天间隔：第 6 天不到期，第 7 天到期
    assert _code(attachedAt="2026-11-02T10:00:00+08:00") == "PKG_REUSE_EXISTING"
    assert _code(attachedAt="2026-10-31T10:00:00+08:00") == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"
    # 窗口两端均含：起点 2026-09-07 与今日
    assert _code(checkDate=_dt.date(2026, 9, 7)) == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"
    assert _code(checkDate=_dt.date(2026, 9, 6)) == "PKG_ACTIVE_OLDER_MANUAL"
    assert _code(checkDate=TODAY) == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"
    # 未来/冲突/无值不自动入候选
    assert _code(checkDate=_dt.date(2026, 11, 8)) == "PKG_REUSE_EXISTING"
    assert _code(checkDate=None, dateIssue="DATE_CONFLICT") == "PKG_REUSE_EXISTING"
    # 可配置间隔
    d = views.package_decision(_facts(attachedAt="2026-11-02T10:00:00+08:00"), today=TODAY, refresh_days=3)
    assert d["code"] == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED"


def test_scan_plan_lists_package_candidates_read_only(layout):
    add_case(layout, A)
    before = ws.load_waterline(layout)
    plan = views.scan_plan(layout, mode="annual-baseline", today=_dt.date(2099, 1, 1))
    assert plan["packagePolicy"]["windowMonths"] == 2 and plan["packagePolicy"]["readOnly"] is True
    assert "packageDecisionCounts" in plan and ws.load_waterline(layout) == before
