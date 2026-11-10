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


# ---------------------------------------------------------------------------------------------
# R35-D4: lifecycle rules reach the normal ledger view, package decision and classification report
# ---------------------------------------------------------------------------------------------

L1, L2, L3 = "99999999C209900001", "99999999C209900002", "99999999C209900003"


def _detail_fields(*, status="已结案[合格]", quality="未发现不合格现象", market="未发现不合格现象", rows=None, accept="2099年01月05日"):
    return {
        "单位名称": "合成单位", "单位地址": "合成地址", "单位类别": "重点单位", "受理日期": accept, "消防管辖": "合成管辖",
        "项目编号": "x", "检查情况": status,
        "检查产品信息": rows if rows is not None else [{"产品质量现场检查情况": quality, "市场准入检查情况": market}],
    }


def _detail_record(project, fields, *, captured="2099-02-10T10:00:00+08:00", stages=None):
    """A saved-detail record the way intake stores it: product root, own business fingerprint of its fields."""
    full = {"文书目录": [{"title": f"[产]监督检查-{project}-1", "children": []}], **fields, "项目编号": project}
    return {"projectNo": project, "capturedAt": captured, "fields": full, "projectInspectionStages": stages or [],
            "businessFingerprint": source._business_detail_fingerprint(full)}


def _save_detail(layout, project, fields, *, captured="2099-02-10T10:00:00+08:00", stages=None, rwid="777"):
    record = _detail_record(project, fields, captured=captured, stages=stages)
    write(layout.pending_case_dir(project) / "source-evidence.json", {
        "projectNo": project, "schemaVersion": "SourceIdentityV1", "records": {rwid: record}})
    # intake registers the content and the time together: detailBusinessFingerprint + capturedAt for the current RWID
    ws.upsert_case(layout, project, unitName="合成单位", source={"projectIdentitySource": "DETAIL", "rwid": rwid, "status": "PACKAGE_READY",
                                                              "detailBusinessFingerprint": record["businessFingerprint"], "capturedAt": captured,
                                                              "packageSha256": "sha256:" + "c" * 64, "downloadDelivery": {"status": "READY", "zipCandidates": [{"name": "a.zip"}]}})


def test_r35_d4_describe_uses_the_later_saved_detail_even_when_classification_digest_no_longer_matches(layout):
    _save_detail(layout, L1, _detail_fields())
    ws.upsert_case(layout, L1, source={"classification": {"initialResult": "QUALIFIED", "evidencePath": f"原始案卷\\待处理案卷\\{L1}\\source-evidence.json",
                                                        "evidenceSha256": "sha256:" + "0" * 64, "observedAt": NOW}})
    life = views.describe(layout, L1, ws.load_waterline(layout)["cases"][L1], today=_dt.date(2099, 2, 20))["lifecycle"]
    assert life["required"]["unitName"]["state"] == "present" and life["required"]["unitCategory"]["value"] == "重点单位"
    assert life["initialResult"] == "合格" and life["recheck"]["state"] == "初查合格无需复查" and life["stable"] is True
    assert life["category"] == "REUSE_DIRECT" and life["initialDate"]["value"] == "2099-01-05" and life["initialDate"]["fallback"] is True
    assert life["initialDate"]["externalSource"] == "监督系统"


def test_r35_d4_waiting_for_a_sampling_report_is_pending_result_and_a_source_cause_not_a_guess(layout):
    _save_detail(layout, L2, _detail_fields(status="处理中[抽样送检]", quality="抽样送检"))
    life = views.describe(layout, L2, ws.load_waterline(layout)["cases"][L2], today=_dt.date(2099, 2, 20))["lifecycle"]
    assert life["initialResult"] == "待结果" and life["sourceCauses"] == ["INITIAL_RESULT_PENDING"] and life["category"] == "NEEDS_SOURCE"
    assert life["administrativeClass"] == "待核实"


def test_r35_d4_package_decision_reuses_stable_cases_and_refreshes_only_for_a_cause():
    stable = {"stable": True, "sourceCauses": [], "nextCheckOn": None}
    assert views.package_decision(_facts(lifecycle=stable, attachedAt="2026-01-01T00:00:00+08:00"), today=TODAY)["code"] == "PKG_REUSE_STABLE"
    quiet = {"stable": False, "sourceCauses": [], "nextCheckOn": "2026-12-01"}
    d = views.package_decision(_facts(lifecycle=quiet, attachedAt="2026-01-01T00:00:00+08:00"), today=TODAY)
    assert d["code"] == "PKG_REUSE_EXISTING" and d["nextEligibleAt"] == "2026-12-01" and "NO_CAUSE_YET" in d["reasons"]
    caused = {"stable": False, "sourceCauses": ["RECHECK_RESULT_PENDING"], "nextCheckOn": None}
    d = views.package_decision(_facts(lifecycle=caused, attachedAt="2026-10-01T00:00:00+08:00"), today=TODAY)
    assert d["code"] == "PKG_REFRESH_DUE_RECENT_UNQUALIFIED" and "RECHECK_RESULT_PENDING" in d["reasons"]
    paced = views.package_decision(_facts(lifecycle=caused, attachedAt="2026-11-05T00:00:00+08:00"), today=TODAY)
    assert paced["code"] == "PKG_REUSE_EXISTING" and "WITHIN_REFRESH_INTERVAL" in paced["reasons"]


def test_r35_d4_lifecycle_view_counts_processed_and_undecided_and_is_read_only(layout):
    _save_detail(layout, L1, _detail_fields())
    _save_detail(layout, L2, _detail_fields(status="处理中[抽样送检]", quality="抽样送检"))
    ws.upsert_case(layout, B, unitName="旧案")        # not a 2099 C-case: outside the product-case scope
    before = layout.waterline_json.read_bytes()
    view = views.lifecycle_view(layout, from_year=2099, today=_dt.date(2099, 2, 20))
    assert layout.waterline_json.read_bytes() == before
    assert view["counts"]["cases"] == 2 and view["counts"]["byCategory"] == {"REUSE_DIRECT": 1, "NEEDS_SOURCE": 1}
    assert view["counts"]["byCause"] == {"INITIAL_RESULT_PENDING": 1}
    assert "年度分母" in view["note"]


def _queue_state(layout, project, fields, *, appearances=None, stages=None):
    _save_detail(layout, project, fields, stages=stages, rwid="fixture-rwid")
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    ws.upsert_case(layout, project, source={"observationsByRwid": {"fixture-rwid": {"documentFingerprints": [source._fingerprint(appearance)]}}})
    state = {"filters": {"selectionMode": "RECENT_DOCUMENT_ACTIVITY"}, "batchId": "recent"}
    records = {"fixture-rwid": {"rwid": "fixture-rwid", "fields": {}, "sourceAppearances": appearances or [appearance]}}
    source._prepare_incremental_queue(layout, state, records, NOW)
    return state, records["fixture-rwid"]


def test_r35_d4_stable_unarchived_case_with_unchanged_documents_is_reused_locally_without_entering_detail(layout):
    state, record = _queue_state(layout, L1, _detail_fields())
    assert state["actionRwids"] == [] and record["skippedAsUnchanged"] is True
    assert record["skipReason"] == "STABLE_CASE_LOCAL_REUSE" and record["projectNo"] == L1


def test_r35_d4_new_document_pending_result_or_missing_package_still_enters_detail(layout):
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    new_doc = [appearance, {"文书名称": "复查记录", "创建日期": "2099-02-20"}]
    state, _ = _queue_state(layout, L1, _detail_fields(), appearances=new_doc)               # 新证据：重开
    assert state["actionRwids"] == ["fixture-rwid"]
    state, _ = _queue_state(layout, L2, _detail_fields(status="处理中[抽样送检]", quality="抽样送检"))   # 待结果：不稳定
    assert state["actionRwids"] == ["fixture-rwid"]
    _save_detail(layout, L3, _detail_fields())
    ws.upsert_case(layout, L3, source={"status": "DETAIL_CAPTURED", "downloadDelivery": {}, "packageSha256": None})
    row = views.describe(layout, L3, ws.load_waterline(layout)["cases"][L3])
    assert row["lifecycle"]["stable"] is True        # 稳定与是否已收包是两个轴；跳过要求已收包


def test_r35_d4_a_report_title_in_the_directory_does_not_turn_a_pending_sampling_result_into_a_local_supplement(layout):
    waiting = _detail_fields(status="处理中[抽样送检]", quality="抽样送检")
    waiting["文书目录"] = [{"title": "[产]开业检查", "children": [{"title": "产品检验", "children": [{"title": "检验报告"}]}]}]   # 实读多为型式试验报告
    _save_detail(layout, L1, waiting)
    life = views.describe(layout, L1, ws.load_waterline(layout)["cases"][L1], today=_dt.date(2099, 2, 20))["lifecycle"]
    assert life["category"] == "NEEDS_SOURCE" and life["sourceCauses"] == ["INITIAL_RESULT_PENDING"] and life["localCauses"] == []


# ---------------------------------------------------------------------------------------------
# R36-D4-A: a saved detail is consumed only through its verifiable digest, ledger-bound RWID, same case and product root
# ---------------------------------------------------------------------------------------------

STALE = {"initialResult": "QUALIFIED", "evidenceSha256": "sha256:" + "0" * 64, "observedAt": NOW}


def _bind_stale_classification(layout, project):
    ws.upsert_case(layout, project, source={"classification": {**STALE, "evidencePath": f"原始案卷\\待处理案卷\\{project}\\source-evidence.json"}})


def _life(layout, project):
    return views.describe(layout, project, ws.load_waterline(layout)["cases"][project], today=_dt.date(2099, 2, 20))["lifecycle"]


def _rewrite_evidence(layout, project, change):
    path = layout.pending_case_dir(project) / "source-evidence.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write(path, data)


def _tamper_address(data):
    data["records"]["777"]["fields"]["单位地址"] = "未绑定摘要的新地址"          # 改了内容但没有新的业务摘要


def _drop_digest(data):
    del data["records"]["777"]["businessFingerprint"]


def _wrong_rwid(data):
    data["records"] = {"999": data["records"]["777"]}                               # 账本只绑定 777


def _wrong_field_project(data):
    record = data["records"]["777"]
    record["fields"]["项目编号"] = L2
    record["businessFingerprint"] = source._business_detail_fingerprint(record["fields"])   # 摘要自洽也不能救跨案项目编号


def _non_product_root(data):
    record = data["records"]["777"]
    record["fields"]["文书目录"] = [{"title": "[消]其他监督", "children": []}]
    record["businessFingerprint"] = source._business_detail_fingerprint(record["fields"])


def _foreign_product_root(data):
    record = data["records"]["777"]
    record["fields"]["文书目录"] = [{"title": f"[产]监督检查-{L2}-1", "children": []}]
    record["businessFingerprint"] = source._business_detail_fingerprint(record["fields"])


@pytest.mark.parametrize("tamper,reason", [
    (_tamper_address, "DIGEST_MISMATCH"), (_drop_digest, "NO_DIGEST"), (_wrong_rwid, "RWID_NOT_BOUND_TO_CASE"),
    (_wrong_field_project, "PROJECT_MISMATCH"), (_non_product_root, "NO_PRODUCT_ROOT"), (_foreign_product_root, "FOREIGN_PRODUCT_ROOT"),
])
def test_r36_d4_a_an_unbound_expired_or_cross_case_saved_detail_is_never_stable_and_keeps_the_case_open(layout, tamper, reason):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    assert _life(layout, L1)["category"] == "REUSE_DIRECT"                       # 同一夹具未篡改前是合法已有详情
    _rewrite_evidence(layout, L1, tamper)
    life = _life(layout, L1)
    assert life["stable"] is False and life["category"] == "NEEDS_SOURCE"
    assert "SAVED_DETAIL_UNTRUSTED" in life["sourceCauses"] and any(reason in r for r in life["evidenceRefused"])


def test_r36_d4_a_a_ledger_digest_that_disagrees_with_the_saved_current_rwid_detail_is_refused(layout):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    ws.upsert_case(layout, L1, source={"detailBusinessFingerprint": "sha256:" + "a" * 64})
    life = _life(layout, L1)
    assert life["stable"] is False and any("LEDGER_DIGEST_MISMATCH" in r for r in life["evidenceRefused"])


def test_r36_d4_a_a_later_observation_with_a_matching_digest_and_ledger_binding_is_consumed(layout):
    _save_detail(layout, L1, _detail_fields(status="处理中[抽样送检]", quality="抽样送检"))
    _bind_stale_classification(layout, L1)
    later = _detail_record(L1, _detail_fields(), captured="2099-02-18T09:00:00+08:00")      # 合格终局的新观察，写入时同时更新了业务摘要
    _rewrite_evidence(layout, L1, lambda data: data["records"].update({"777": later}))
    ws.upsert_case(layout, L1, source={"detailBusinessFingerprint": later["businessFingerprint"], "capturedAt": later["capturedAt"]})
    life = _life(layout, L1)
    assert life["initialResult"] == "合格" and life["stable"] is True and life["category"] == "REUSE_DIRECT" and life["evidenceRefused"] == []


def test_r36_d4_a_one_case_may_have_several_rwids_each_judged_on_its_own_evidence(layout):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    second = _detail_record(L1, _detail_fields())
    _rewrite_evidence(layout, L1, lambda data: (data["records"]["777"]["fields"].update({"单位地址": "被改且无摘要"}), data["records"].update({"888": second})))
    assert _life(layout, L1)["category"] == "NEEDS_SOURCE"                               # 888 有记录但账本没有它的依据
    ws.upsert_case(layout, L1, source={"observationsByRwid": {"888": {"fingerprint": source._fingerprint(second), "observedAt": second["capturedAt"]}}})
    life = _life(layout, L1)
    assert life["stable"] is True and life["category"] == "REUSE_DIRECT"                 # 888 现在有账本依据，777 的坏记录不影响它
    _rewrite_evidence(layout, L1, lambda data: data["records"]["888"]["fields"].update({"单位地址": "也被改"}))
    assert _life(layout, L1)["category"] == "NEEDS_SOURCE"                               # 两个都不可核：不稳定


def test_r36_d4_a_repeated_normal_reads_of_a_stable_case_write_nothing(layout):
    _save_detail(layout, L1, _detail_fields())
    before = {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}
    for _ in range(2):
        assert _life(layout, L1)["category"] == "REUSE_DIRECT"
    assert before == {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------------------------
# R36-D4-B: a recheck closes only the initial product it is linked to by initialProductRef
# ---------------------------------------------------------------------------------------------

from scripts import case_lifecycle as lifecycle  # noqa: E402


def _chain_manifest(initial, recheck, *, date="2099-01-01"):
    products = lambda rows: [{"clientRef": r[0], "result": r[1], "method": "ONSITE", **({"initialProductRef": r[2]} if len(r) > 2 else {})} for r in rows]  # noqa: E731
    return {"case": {"unitName": "独验单位", "unitAddress": "独验地址", "unitCategory": "重点单位", "brigadeCode": "XISHAN", "inspectionForm": "ROUTINE"},
            "initialInspection": {"inspectionDate": date, "products": products(initial)},
            "recheckInspection": {"products": products(recheck)}}


def _assess(manifest, **kw):
    return lifecycle.assess_case(project=L1, manifest=manifest, has_package=True, today=_dt.date(2099, 2, 20), **kw)


def test_r36_d4_b_a_recheck_linked_to_the_same_unqualified_product_closes_that_chain():
    r = _assess(_chain_manifest([("a", "UNQUALIFIED")], [("r1", "QUALIFIED", "a")]))
    assert r["recheck"]["closed"] is True and r["recheck"]["state"] == "复查有有效结果" and r["stable"] is True
    assert r["sourceCauses"] == [] and r["localCauses"] == [] and r["category"] == "REUSE_DIRECT" and r["recheck"]["linkUnproven"] == 0


def test_r36_d4_b_a_valid_recheck_of_another_product_cannot_close_an_unqualified_initial_product():
    r = _assess(_chain_manifest([("product-a", "UNQUALIFIED")], [("product-b", "QUALIFIED")]))        # 未关联，数量相同
    assert r["recheck"]["closed"] is False and r["stable"] is False and r["needsFollowUp"] is True
    assert r["localCauses"] == ["RECHECK_PRODUCT_LINK_UNPROVEN"] and r["sourceCauses"] == [] and r["category"] == "LOCAL_SUPPLEMENT"
    assert r["administrativeClass"] == "待核实" and r["recheck"]["linkUnproven"] == 2


@pytest.mark.parametrize("recheck", [
    [("r1", "QUALIFIED", "no-such-product")],            # 引用不存在
    [("r1", "QUALIFIED", "b")],                           # 引用了另一个（合格）初查产品
    [("r1", "UNKNOWN", "a")],                             # 关联正确但没有有效复查结果
    [],                                                   # 没有任何复查
])
def test_r36_d4_b_wrong_unlinked_or_result_less_rechecks_never_close_the_chain(recheck):
    r = _assess(_chain_manifest([("a", "UNQUALIFIED"), ("b", "QUALIFIED")], recheck))
    assert r["recheck"]["closed"] is False and r["stable"] is False and r["category"] != "REUSE_DIRECT"
    assert r["administrativeClass"] == "待核实"


def test_r36_d4_b_in_a_mixed_case_an_unknown_link_blocks_only_its_own_chain():
    manifest = _chain_manifest([("a", "UNQUALIFIED"), ("c", "UNQUALIFIED"), ("q", "QUALIFIED")],
                               [("r-a", "UNQUALIFIED", "a"), ("r-x", "QUALIFIED")])
    r = _assess(manifest)
    by_ref = {c.get("initialRef"): c for c in r["chains"] if c["linked"]}
    assert by_ref["a"]["recheck_state"] == "复查有有效结果" and by_ref["a"]["recheck_result"] == "不合格"        # 有证链照常终态
    assert by_ref["q"]["recheck_state"] == "初查合格无需复查"
    assert by_ref["c"]["recheck_state"] == "待核实" and by_ref["c"]["reasonCode"] == "LINK_UNPROVEN"
    assert r["recheck"]["closed"] is False and r["recheck"]["otherState"] == "复查有有效结果" and r["stable"] is False
    assert r["localCauses"] == ["RECHECK_PRODUCT_LINK_UNPROVEN"] and r["sourceCauses"] == []
    assert r["administrativeClass"] == "是"            # a 链初查/复查都不合格，已证


def test_r36_d4_b_another_chains_real_source_cause_is_not_hidden_by_an_unknown_link():
    manifest = _chain_manifest([("a", "UNQUALIFIED"), ("c", "UNQUALIFIED")], [("r-a", "UNKNOWN", "a"), ("r-x", "QUALIFIED")])
    r = _assess(manifest)
    assert "RECHECK_PRODUCT_LINK_UNPROVEN" in r["localCauses"] and "RECHECK_RESULT_PENDING" in r["sourceCauses"]     # a: 复查已开展待结果


def test_r36_d4_b_page_rows_without_product_references_stay_unlinked_and_open():
    fields = {"单位名称": "独验单位", "单位地址": "独验地址", "单位类别": "重点单位", "消防管辖": "独验管辖", "受理日期": "2099年01月05日", "检查情况": "已结案",
              "检查产品信息": [{"页面记录": {"SFCPFC": "0", "JCRQ": "2099-01-04"}, "产品质量现场检查情况": "1项不合格", "市场准入检查情况": "未发现不合格现象", "检查结果": "不合格"},
                                {"页面记录": {"SFCPFC": "1"}, "产品质量现场检查情况": "未发现不合格现象", "市场准入检查情况": "未发现不合格现象", "检查结果": "合格"}]}
    r = lifecycle.assess_case(project=L1, evidence_fields=fields, has_package=True, today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    assert r["recheck"]["closed"] is False and r["stable"] is False and "RECHECK_PRODUCT_LINK_UNPROVEN" in r["localCauses"]
    assert r["administrativeClass"] == "待核实"


def test_r36_d4_b_an_unlinked_recheck_alone_is_a_local_gap_and_does_not_send_the_case_back_to_the_source(layout):
    rows = [{"页面记录": {"SFCPFC": "0", "JCRQ": "2099-01-04"}, "产品质量现场检查情况": "1项不合格", "市场准入检查情况": "未发现不合格现象", "检查结果": "不合格"},
            {"页面记录": {"SFCPFC": "1"}, "产品质量现场检查情况": "未发现不合格现象", "市场准入检查情况": "未发现不合格现象", "检查结果": "合格"}]
    state, record = _queue_state(layout, L1, _detail_fields(status="已结案", rows=rows))
    life = _life(layout, L1)
    assert life["category"] == "LOCAL_SUPPLEMENT" and life["sourceCauses"] == [] and life["localCauses"] == ["RECHECK_PRODUCT_LINK_UNPROVEN"]
    assert state["actionRwids"] == [] and record["skipReason"] == "NO_SOURCE_CAUSE_LOCAL_REUSE"


# ---------------------------------------------------------------------------------------------
# R37-D4-A: content, ledger registration and observation time are bound separately; R37-D4-QUEUE: zero repeated writes
# ---------------------------------------------------------------------------------------------

def _edit_and_refingerprint(data, **changes):
    record = data["records"]["777"]
    record["fields"].update(changes)
    record["businessFingerprint"] = source._business_detail_fingerprint(record["fields"])


def test_r37_d4_a_a_self_consistent_edit_without_ledger_registration_is_not_stable(layout):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    _rewrite_evidence(layout, L1, lambda data: _edit_and_refingerprint(data, 单位地址="文件内改并重算自指纹的新地址"))
    life = _life(layout, L1)
    assert life["stable"] is False and life["category"] == "NEEDS_SOURCE" and any("LEDGER_DIGEST_MISMATCH" in r for r in life["evidenceRefused"])
    ws.upsert_case(layout, L1, source={"detailBusinessFingerprint": None})
    # a ledger with no registered detail fingerprint at all cannot bind the current RWID either
    data = ws.load_waterline(layout)
    data["cases"][L1]["source"].pop("detailBusinessFingerprint", None)
    ws.save_waterline(layout, data)
    life = _life(layout, L1)
    assert life["stable"] is False and any("LEDGER_BINDING_MISSING" in r for r in life["evidenceRefused"])


def test_r37_d4_a_an_edited_timestamp_is_not_a_registered_observation_time(layout):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    _rewrite_evidence(layout, L1, lambda data: data["records"]["777"].update({"capturedAt": "2099-12-31T10:00:00+08:00"}))
    life = _life(layout, L1)
    assert life["stable"] is False and any("OBSERVED_AT_UNREGISTERED" in r for r in life["evidenceRefused"])


def _due_case(layout):
    """Unqualified case whose notice is dated 2099-01-05 (due 2099-02-05) with a recorded 2099-01-10 observation."""
    rows = [{"页面记录": {"SFCPFC": "0", "JCRQ": "2099-01-04"}, "产品质量现场检查情况": "1项不合格", "市场准入检查情况": "未发现不合格现象", "检查结果": "不合格"}]
    _save_detail(layout, L1, _detail_fields(status="已结案", rows=rows), captured="2099-01-10T10:00:00+08:00")
    _bind_stale_classification(layout, L1)
    manifest = _chain_manifest([("a", "UNQUALIFIED")], [])
    manifest.pop("recheckInspection")
    manifest["case"]["projectNo"] = L1
    manifest["documentSlots"] = [{"slotCode": "RECTIFICATION_ORDER", "documentDate": "2099-01-05"}]
    write(layout.work_case_dir(L1) / "manifest.json", manifest)


def test_r37_d4_a_a_forged_later_capture_time_cannot_turn_a_pre_due_observation_into_a_due_one(layout):
    _due_case(layout)
    before = _life(layout, L1)
    _rewrite_evidence(layout, L1, lambda data: data["records"]["777"].update({"capturedAt": "2099-02-10T10:00:00+08:00"}))
    after = _life(layout, L1)
    assert before["recheck"]["closed"] is False and after["recheck"]["closed"] is False and after["stable"] is False
    assert any("OBSERVED_AT_UNREGISTERED" in r for r in after["evidenceRefused"])


def test_r37_d4_a_a_registered_later_observation_does_close_the_due_chain(layout):
    _due_case(layout)
    later = _detail_record(L1, _detail_fields(status="已结案", rows=[{"页面记录": {"SFCPFC": "0", "JCRQ": "2099-01-04"}, "产品质量现场检查情况": "1项不合格", "市场准入检查情况": "未发现不合格现象", "检查结果": "不合格"}]),
                           captured="2099-02-10T10:00:00+08:00")
    _rewrite_evidence(layout, L1, lambda data: data["records"].update({"777": later}))
    ws.upsert_case(layout, L1, source={"detailBusinessFingerprint": later["businessFingerprint"], "capturedAt": later["capturedAt"]})   # intake registers content and time together
    life = _life(layout, L1)
    assert life["evidenceRefused"] == [] and life["recheck"]["observedOn"] == "2099-02-10" and life["recheck"]["state"] == "到期确认未开展"


def test_r37_d4_a_another_rwid_with_changed_content_but_an_old_registered_fingerprint_is_refused(layout):
    _save_detail(layout, L1, _detail_fields())
    _bind_stale_classification(layout, L1)
    second = _detail_record(L1, _detail_fields())
    old_fp = source._fingerprint(second)
    second["fields"]["单位地址"] = "同RWID但未经新观察登记的内容"
    second["businessFingerprint"] = source._business_detail_fingerprint(second["fields"])
    _rewrite_evidence(layout, L1, lambda data: data["records"].clear() or data["records"].update({"888": second}))
    ws.upsert_case(layout, L1, source={"observationsByRwid": {"888": {"fingerprint": old_fp, "observedAt": second["capturedAt"]}}})
    life = _life(layout, L1)
    assert life["stable"] is False and any("888:OBSERVATION_NOT_REGISTERED" in r for r in life["evidenceRefused"])


def _count_persistence(monkeypatch):
    counts = {"upsert": 0, "persistentSave": 0}
    real_upsert, real_save = source.upsert_case, ws._save_waterline_unlocked

    def upsert(*a, **k):
        counts["upsert"] += 1
        return real_upsert(*a, **k)

    def save(*a, **k):
        counts["persistentSave"] += 1
        return real_save(*a, **k)

    monkeypatch.setattr(source, "upsert_case", upsert)
    monkeypatch.setattr(ws, "_save_waterline_unlocked", save)
    return counts


def _run_queue(layout, project, at, appearances=None, batch="synthetic"):
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    state = {"filters": {"selectionMode": "RECENT_DOCUMENT_ACTIVITY"}, "batchId": batch}
    records = {"777": {"rwid": "777", "fields": {}, "sourceAppearances": appearances or [appearance]}}
    source._prepare_incremental_queue(layout, state, records, at)
    return state, records["777"]


def _prime_observation(layout, project):
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    ws.upsert_case(layout, project, source={"observationsByRwid": {"777": {"documentFingerprints": [source._fingerprint(appearance)]}}})


@pytest.mark.parametrize("kind", ["stable", "local-gap"])
def test_r37_queue_the_same_registered_observation_makes_no_persistent_write_on_repeat(layout, monkeypatch, kind):
    if kind == "stable":
        _save_detail(layout, L1, _detail_fields())
    else:
        rows = [{"页面记录": {"SFCPFC": "0", "JCRQ": "2099-01-04"}, "产品质量现场检查情况": "1项不合格", "市场准入检查情况": "未发现不合格现象", "检查结果": "不合格"},
                {"页面记录": {"SFCPFC": "1"}, "产品质量现场检查情况": "未发现不合格现象", "市场准入检查情况": "未发现不合格现象", "检查结果": "合格"}]
        _save_detail(layout, L1, _detail_fields(status="已结案", rows=rows))
    _prime_observation(layout, L1)
    counts = _count_persistence(monkeypatch)
    state, record = _run_queue(layout, L1, "2099-02-20T00:00:00Z")
    assert state["actionRwids"] == [] and record["skipReason"] in {"STABLE_CASE_LOCAL_REUSE", "NO_SOURCE_CAUSE_LOCAL_REUSE"}
    first = dict(counts)
    assert first == {"upsert": 1, "persistentSave": 1}                       # 第一次登记这个观察
    before = {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}
    for _ in range(3):
        state, _ = _run_queue(layout, L1, "2099-02-20T00:00:00Z")
        assert state["actionRwids"] == []
    assert counts == first                                                    # 连续三次相同观察：底层 upsert/save 零调用
    assert before == {str(p.relative_to(layout.root)): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}
    state, _ = _run_queue(layout, L1, "2099-02-21T00:00:00Z")                 # 真正新的观察时点要登记一次，之后再重复又是零
    assert state["actionRwids"] == [] and counts == {"upsert": 2, "persistentSave": 2}
    _run_queue(layout, L1, "2099-02-21T00:00:00Z")
    assert counts == {"upsert": 2, "persistentSave": 2}


def test_r37_queue_zero_write_does_not_hide_new_documents_missing_packages_or_pending_results(layout, monkeypatch):
    _save_detail(layout, L1, _detail_fields())
    _prime_observation(layout, L1)
    _run_queue(layout, L1, "2099-02-20T00:00:00Z")
    counts = _count_persistence(monkeypatch)
    state, _ = _run_queue(layout, L1, "2099-02-20T00:00:00Z", appearances=[{"文书名称": "检查记录", "创建日期": "2099-01-20"}, {"文书名称": "复查记录", "创建日期": "2099-02-20"}])
    assert state["actionRwids"] == ["777"] and counts == {"upsert": 0, "persistentSave": 0}      # 新文书进入队列；队列本身不写
    _save_detail(layout, L2, _detail_fields(status="处理中[抽样送检]", quality="抽样送检"))
    ws.upsert_case(layout, L2, source={"rwid": "888", "observationsByRwid": {"888": {"documentFingerprints": [source._fingerprint({"文书名称": "检查记录", "创建日期": "2099-01-20"})]}}})
    appearance = {"文书名称": "检查记录", "创建日期": "2099-01-20"}
    state = {"filters": {"selectionMode": "RECENT_DOCUMENT_ACTIVITY"}, "batchId": "synthetic"}
    source._prepare_incremental_queue(layout, state, {"888": {"rwid": "888", "fields": {}, "sourceAppearances": [appearance]}}, "2099-02-20T00:00:00Z")
    assert state["actionRwids"] == ["888"]                                                     # 待结果有来源原因：仍回源


@pytest.mark.parametrize("name,address,state", [
    ("江阴市沈港桥东五金厂", "江阴市", "present"),                       # 名称含地名，不是地址栏错位
    ("江阴市某某开发区管理委员会", "江阴市", "present"),
    ("江阴市", "江阴市", "conflict"),                                      # 名称等于地址
    ("某单位江阴市", "江阴市", "conflict"),                                # 以地名地址结尾
    ("江阴市长江路88号某单位", "江阴市长江路88号", "conflict"),            # 把门牌写入名称栏
    ("", "江阴市", "missing"),                                              # 必填为空仍判缺失
])
def test_r43_lifecycle_unit_name_containing_place_text_is_not_a_column_mixup(name, address, state):
    manifest = _chain_manifest([("a", "UNQUALIFIED")], [("r1", "QUALIFIED", "a")])
    manifest["case"].update(unitName=name, unitAddress=address)
    required = _assess(manifest)["required"]["unitName"]
    assert required["state"] == state


def _marked_rows(initial_result="不合格", recheck=True, mark_recheck="[复查]", mark_initial="[检]"):
    """保存的可见详情行：结果列显示阶段标记，没有 页面记录(SFCPFC)。"""
    rows = [{"产品名称（规格型号）": "手提式干粉灭火器(MF/ABCE4)", "产品质量现场检查情况": initial_result, "市场准入检查情况": "未发现不合格现象(1)",
             "检查结果": mark_initial, "检查结果标记": ["el-icon-error" if initial_result == "不合格" else "el-icon-success"]}]
    if recheck:
        rows.append({"产品名称（规格型号）": "手提式干粉灭火器(MF/ABCE4)", "产品质量现场检查情况": "未发现不合格现象", "市场准入检查情况": "未发现不合格现象(2)",
                     "检查结果": mark_recheck, "检查结果标记": ["el-icon-success"]})
    return rows


def _evidence_with_rows(rows):
    return {"单位名称": "独验单位", "单位地址": "独验地址", "单位类别": "重点单位", "消防管辖": "独验管辖", "受理日期": "2099年01月05日",
            "检查情况": "处理中", "检查产品信息": rows}


def test_r47_visible_stage_marks_decide_the_row_stage_and_unmarked_rows_have_none():
    assert lifecycle._row_stage({"检查结果": "[复查]"}) == "RECHECK"
    assert lifecycle._row_stage({"检查结果": "[检]"}) == "INITIAL"
    assert lifecycle._row_stage({"检查结果": "不合格"}) is None and lifecycle._row_stage({}) is None
    assert lifecycle._row_stage({"页面记录": {"SFCPFC": "1"}, "检查结果": "[检]"}) == "RECHECK"      # 显式阶段优先于可见标记
    assert lifecycle._row_stage({"页面记录": {"SFCPFC": "0"}, "检查结果": "[复查]"}) == "INITIAL"


def test_r47_visible_recheck_row_without_manifest_is_started_but_unlinked_not_closed_and_not_reuse_direct():
    r = lifecycle.assess_case(project=L1, evidence_fields=_evidence_with_rows(_marked_rows()), has_package=True,
                              today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    assert r["recheck"]["state"] != "观察期内未开展" and r["recheck"]["state"] == "待核实"
    assert r["recheck"]["closed"] is False and r["recheck"]["linkUnproven"] >= 1
    assert r["category"] != "REUSE_DIRECT" and "RECHECK_PRODUCT_LINK_UNPROVEN" in r["localCauses"]
    assert r["administrativeClass"] == "待核实"                      # 不据单行复查合格推出“否”


def test_r47_visible_recheck_row_is_not_hidden_behind_an_old_manifest_with_initial_products_only():
    manifest = _chain_manifest([("a", "UNQUALIFIED")], [])
    manifest.pop("recheckInspection")                                 # 旧 manifest：只有初查产品，没有复查块
    with_manifest = lifecycle.assess_case(project=L1, manifest=manifest, evidence_fields=_evidence_with_rows(_marked_rows()),
                                          has_package=True, today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    without_manifest = lifecycle.assess_case(project=L1, evidence_fields=_evidence_with_rows(_marked_rows()), has_package=True,
                                             today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    for r in (with_manifest, without_manifest):
        assert r["recheck"]["state"] == "待核实" and r["recheck"]["closed"] is False and r["recheck"]["linkUnproven"] >= 1
        assert r["category"] != "REUSE_DIRECT"
    # 对照：同一旧 manifest、证据没有复查行时，仍是原有的“观察期内未开展”判断(未受影响)
    quiet = lifecycle.assess_case(project=L1, manifest=manifest, evidence_fields=_evidence_with_rows(_marked_rows(recheck=False)),
                                  has_package=True, today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    assert quiet["recheck"]["linkUnproven"] == 0 and "RECHECK_PRODUCT_LINK_UNPROVEN" not in quiet["localCauses"]


def test_r47_manifest_with_its_own_recheck_products_is_not_double_counted_by_page_rows():
    manifest = _chain_manifest([("a", "UNQUALIFIED")], [("r1", "QUALIFIED", "a")])
    r = lifecycle.assess_case(project=L1, manifest=manifest, evidence_fields=_evidence_with_rows(_marked_rows()),
                              has_package=True, today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    assert r["recheck"]["closed"] is True and r["recheck"]["linkUnproven"] == 0 and r["recheck"]["state"] == "复查有有效结果"


def test_r47_unmarked_rows_are_not_guessed_into_a_recheck():
    rows = _marked_rows(mark_recheck="", mark_initial="")
    r = lifecycle.assess_case(project=L1, evidence_fields=_evidence_with_rows(rows), has_package=True,
                              today=_dt.date(2099, 2, 20), observed_on="2099-02-10")
    assert r["recheck"]["linkUnproven"] == 0
