import json
from copy import deepcopy

import pytest

from scripts import ledger_views as views
from scripts import source_coverage as coverage
from scripts import source_intake as source
from scripts import workspace_state as ws

NOW = "2099-09-20T10:00:00+08:00"
PROJECT = "99999999T209900001"


@pytest.fixture
def layout(tmp_path):
    return ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))


def filters(layout, category, group="fixture"):
    evidence = layout.root / f"query-{coverage.TASK_CATEGORIES.index(category)}.json"
    evidence.write_text(json.dumps({"fixture": True, "category": category}), encoding="utf-8")
    return {
        "selectionMode": coverage.ANNUAL_MODE,
        "year": 2099,
        "dateShortcut": "本年",
        "baselineId": group,
        "taskCategory": category,
        "sourceListKind": "CASE_TASK",
        "taskStatus": "ALL",
        "lawEnforcementUnit": "ALL",
        "brigadeScope": "ALL",
        "jurisdiction": "全部管辖单位(含派出所)",
        "startDate": "2099-01-01",
        "endDate": "2099-12-31",
        "dateFieldLabel": "创建时间",
        "queryRoute": "#/xfjd/cpjd/cxtj/jcwcx/fixture",
        "queryEvidencePath": evidence.name,
    }


def capture(layout, batch, category, rows, group="fixture", rounds=2):
    source.begin_capture(
        layout,
        filters(layout, category, group),
        origin="https://source.example/",
        now=NOW,
        batch_id=batch,
    )
    pages = max(1, (len(rows) + 49) // 50)
    for round_no in range(1, rounds + 1):
        for page in range(1, pages + 1):
            source.add_page(
                layout,
                batch,
                page,
                rows[(page - 1) * 50 : page * 50],
                len(rows),
                pages,
                round_no=round_no,
                observed_at=NOW,
            )
        source.finalize_capture(layout, batch, now=NOW)


def test_500_case_census_requires_every_category_and_all_identities(layout):
    all_rows = [
        {"RWID": f"rwid-{i}", "单位名称": f"单位{i}", "项目编号": f"99999999T2099{i:05}"}
        for i in range(500)
    ]
    # Multiple source tasks for the same project and duplicate appearances.
    all_rows += [{**all_rows[i], "RWID": f"alias-{i}"} for i in range(5)]
    all_rows += deepcopy(all_rows[:2])
    for index, category in enumerate(coverage.TASK_CATEGORIES):
        capture(layout, f"annual-{index}", category, all_rows if index == 0 else [])
        if index < 5:
            assert coverage.annual_coverage(layout, 2099)["complete"] is False
    first = coverage.annual_coverage(layout, 2099, details=True)
    assert first["status"] == "IDENTITY_PENDING"
    assert first["sourceListCount"] == 507
    assert first["sourceIdentityCount"] == 505
    assert first["sourceDocumentCount"] == first["uniqueProjectCount"] == 0
    assert first["unresolvedSourceIdentities"] == 505
    # Fixture setup: a saved, individually confirmed identity for each source row.
    path = layout.capture_batches / "annual-0" / "browser-capture.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    for row in value["records"].values():
        row["detail"] = {
            "projectNo": row["projectNo"],
            "fields": {"projectNo": row["projectNo"], "unitName": row["fields"]["单位名称"]},
            "capturedAt": NOW,
        }
    path.write_text(json.dumps(value), encoding="utf-8")
    assert coverage.annual_coverage(layout, 2099)["projectsMissingLedger"] == 500
    planned = views.index_history(layout)
    assert planned["sourceIdentitiesWithoutProject"] == 0
    applied = views.index_history(layout, apply=True)
    assert len(planned["changes"]) == len(applied["changes"])
    completed = coverage.annual_coverage(layout, 2099)
    assert completed["complete"] is True
    assert completed["uniqueProjectCount"] == completed["indexedProjectCount"] == 500
    assert completed["sourceObservedAt"] == NOW
    before = layout.waterline_json.read_bytes()
    assert views.index_history(layout, apply=True)["changes"] == []
    assert layout.waterline_json.read_bytes() == before
    scoped = views.ledger_view(layout, source_year=2099)
    assert scoped["counts"]["cases"] == 500
    assert all(r["sourceAnnualMember"] and r["sourceAnnualYear"] == 2099 for r in scoped["cases"])
    assert scoped["counts"]["materialsCollected"] == scoped["counts"]["systemRegistered"] == 0
    plan = views.scan_plan(layout, year=2099)
    assert plan["mode"] == "recent"
    assert plan["pendingOutsideWindowRetained"] is True


def test_document_notice_is_candidate_not_annual_baseline_or_unqualified(layout):
    source.begin_capture(layout, now=NOW, batch_id="documents", origin="https://source.example/")
    row = {
        "RWID": "rwid-a",
        "单位名称": "测试",
        "文书名称": "责令限期改正通知书",
        "创建日期": "2099-02-01",
    }
    for round_no in (1, 2):
        source.add_page(layout, "documents", 1, [row], 1, 1, round_no=round_no, observed_at=NOW)
        source.finalize_capture(layout, "documents", now=NOW)
    ws.upsert_case(layout, PROJECT, source={"projectIdentitySource": "DETAIL", "rwid": "rwid-a"})
    result = coverage.annual_coverage(layout, 2099)
    assert result["status"] == "UNVERIFIED"
    assert result["sourceListCount"] is None
    assert result["candidateProjectNos"] == [PROJECT]
    assert views.ledger_view(layout, view="unqualified", source_year=2099)["counts"]["cases"] == 0
    assert views.ledger_view(layout, view="unknown", source_year=2099)["counts"]["cases"] == 1
    plan = views.scan_plan(layout, year=2099, mode="recent")
    assert plan["readyToScan"] is False


def test_annual_filters_do_not_assume_inspection_date_or_accept_one_brigade(layout):
    original = filters(layout, coverage.TASK_CATEGORIES[0])
    assert source._default_filters(NOW, original)["dateFieldLabel"] == "创建时间"
    for patch in (
        {"taskStatus": "已结案"},
        {"brigadeCode": "XS"},
        {"documentType": "ALL"},
        {"queryRoute": "#/xfjd/cpjd/cxtj/flwscx/flws"},
        {"taskCategory": ""},
        {"dateFieldLabel": ""},
        {"endDate": "2099-08-01"},
    ):
        with pytest.raises(source.SourceIntakeError):
            source._default_filters(NOW, {**original, **patch})


def test_annual_identity_is_minimal_and_idempotent_and_keeps_original_workflow(layout):
    ws.upsert_case(layout, PROJECT, state="UPLOADING", upload={"jobId": "original-job"})
    category = coverage.TASK_CATEGORIES[0]
    capture(layout, "identity", category, [{"RWID": "rwid-a", "单位名称": "测试"}])
    image = layout.root / "fixture.png"
    image.write_bytes(b"synthetic-image")
    detail = {
        "projectNo": PROJECT,
        "unitName": "测试",
        "initialInspection": {
            "inspectionDate": "2098-12-01",
            "products": [{"method": "ONSITE", "result": "QUALIFIED"}],
        },
    }
    source.add_detail(
        layout,
        "identity",
        "rwid-a",
        detail,
        source_url="https://source.example/#/xfjd/projectDetail?RWID=rwid-a",
        screenshot=image,
        captured_at=NOW,
    )
    record = ws.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "UPLOADING"
    assert record["upload"]["jobId"] == "original-job"
    assert not layout.work_case_dir(PROJECT).exists()
    assert not layout.pending_case_dir(PROJECT).exists()
    row = views.ledger_view(layout, source_year=2099)["cases"][0]
    assert row["statisticsYear"] == 2098
    assert row["initialResult"] == "QUALIFIED"
    assert row["stages"]["materialsCollected"] is False
    before = layout.waterline_json.read_bytes()
    source.add_detail(
        layout,
        "identity",
        "rwid-a",
        detail,
        source_url="https://source.example/#/xfjd/projectDetail?RWID=rwid-a",
        screenshot=image,
        captured_at="2099-09-21T10:00:00+08:00",
    )
    assert layout.waterline_json.read_bytes() == before


def test_conflicting_identity_and_unfinished_new_census_never_hide_coverage_gap(layout):
    ws.upsert_case(layout, PROJECT, source={"projectIdentitySource": "DETAIL", "rwid": "same"})
    ws.upsert_case(
        layout, "99999999T209900002", source={"projectIdentitySource": "DETAIL", "rwid": "same"}
    )
    capture(layout, "conflict", coverage.TASK_CATEGORIES[0], [{"RWID": "same", "单位名称": "测试"}])
    result = coverage.annual_coverage(layout, 2099)
    assert result["conflictingSourceIdentities"] == 1
    assert result["complete"] is False
    assert views.index_history(layout, apply=True)["sourceIdentityConflicts"] == ["same"]


def test_query_evidence_tampering_and_no_data_categories(layout):
    for index, category in enumerate(coverage.TASK_CATEGORIES):
        capture(layout, f"zero-{index}", category, [])
    assert coverage.annual_coverage(layout, 2099)["complete"] is True
    assert coverage.annual_coverage(layout, 2099)["sourceListCount"] == 0
    (layout.root / "query-0.json").write_text("changed", encoding="utf-8")
    assert coverage.annual_coverage(layout, 2099)["status"] == "QUERY_EVIDENCE_INVALID"


def test_wrong_menu_does_not_count_old_page_as_another_category(layout):
    capture(layout, "daily", coverage.TASK_CATEGORIES[0], [{"RWID": "a", "单位名称": "甲"}])
    capture(layout, "wrong", coverage.TASK_CATEGORIES[1], [{"RWID": "b", "单位名称": "乙"}])
    evidence = layout.root / "query-1.json"
    evidence.write_text(
        json.dumps({"queryCategory": coverage.TASK_CATEGORIES[0]}), encoding="utf-8"
    )
    # Even a correctly hashed observation must agree with the actual menu category.
    path = layout.capture_batches / "wrong" / "browser-capture.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["queryEvidence"] = source._evidence_file(evidence, layout.root)
    path.write_text(json.dumps(value), encoding="utf-8")
    result = coverage.annual_coverage(layout, 2099, details=True)
    assert result["sourceListCount"] is None
    assert result["observedSourceListCount"] == result["sourceIdentityCount"] == 1
    assert result["identities"][0]["rwid"] == "a"
    assert result["invalidTaskCategories"] == {
        coverage.TASK_CATEGORIES[1]: "QUERY_CATEGORY_MISMATCH"
    }


def test_notice_candidates_precede_generic_check_records():
    assert (
        coverage.candidate_priority(["NOTICE_CANDIDATE"])
        < coverage.candidate_priority(["RESULT_REQUIRES_REVIEW"])
        < coverage.candidate_priority([])
    )


def test_unavailable_category_is_a_gap_with_no_zero_or_wrong_count(layout):
    capture(layout, "available", coverage.TASK_CATEGORIES[0], [{"RWID": "a", "单位名称": "甲"}])
    directory = layout.capture_batches / "fixture"
    directory.mkdir()
    image = directory / "unavailable.png"
    image.write_bytes(b"synthetic-unavailable-screenshot")
    (directory / "availability-2.json").write_text(
        json.dumps(
            {
                "schemaVersion": "SourceCategoryAvailabilityV1",
                "baselineId": "fixture",
                "sourceYear": 2099,
                "category": coverage.TASK_CATEGORIES[2],
                "status": "UNAVAILABLE",
                "message": "开发测试中，暂时不能使用。。。",
                "observedAt": NOW,
                "screenshot": source._evidence_file(image, layout.root),
            }
        ),
        encoding="utf-8",
    )
    result = coverage.annual_coverage(layout, 2099)
    assert result["complete"] is False
    assert result["sourceListCount"] is None
    assert result["observedSourceListCount"] == 1
    assert coverage.TASK_CATEGORIES[2] in result["unavailableTaskCategories"]
    assert coverage.TASK_CATEGORIES[2] in result["missingTaskCategories"]
    image.write_bytes(b"changed")
    with pytest.raises(ws.WorkspaceStateError, match="摘要不一致"):
        coverage.annual_coverage(layout, 2099)


def test_new_scan_keeps_known_members_but_never_reuses_old_completion(layout):
    category = coverage.TASK_CATEGORIES[0]
    capture(layout, "old", category, [{"RWID": "a", "单位名称": "甲"}])
    ws.upsert_case(layout, PROJECT, source={"projectIdentitySource": "DETAIL", "rwid": "a"})
    source.begin_capture(
        layout,
        filters(layout, category),
        origin="https://source.example/",
        now="2099-09-21T10:00:00+08:00",
        batch_id="new",
    )
    result = coverage.annual_coverage(layout, 2099)
    assert result["complete"] is False
    assert result["sourceIdentityCount"] == result["indexedProjectCount"] == 1
    assert result["baselineBatchIds"][category] == "new"
    assert result["membershipBatchIds"][category] == "old"
    assert result["sourceListCount"] is None


def test_mid_run_source_growth_revokes_stability(layout):
    for i, category in enumerate(coverage.TASK_CATEGORIES):
        capture(layout, f"stable-{i}", category, [])
    assert coverage.annual_coverage(layout, 2099)["complete"]
    path = layout.capture_batches / "stable-0" / "browser-capture.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["listDrift"] = {"observedAt": NOW, "observedTotal": 1}
    path.write_text(json.dumps(value), encoding="utf-8")
    result = coverage.annual_coverage(layout, 2099)
    assert result["status"] == "LIST_UNSTABLE"
    assert result["complete"] is False
    assert result["sourceListCount"] is None
