"""Initial-inspection-date scope: listing reconcile, byte-exact exclusion and its exact inverse."""

import hashlib
import json
from datetime import date

import pytest

from scripts import initial_scope as scope
from scripts import workspace_state as ws

P_OUT = "99999999C202500001"      # 旧号段，初查 2025-12-31（范围外）
P_IN_OLD = "99999999C202500002"   # 旧号段，但初查 2026-01-05（范围内）
P_IN = "99999999C202600003"
UNIT_OUT, UNIT_IN_OLD, UNIT_IN = "范围外酒店", "旧号段范围内商场", "范围内单位"
PLAN_ID = "20261111-initial-date-test"


@pytest.fixture
def layout(tmp_path):
    return ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))


def _sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _case(layout, project, unit, rwid):
    ws.upsert_case(layout, project, unitName=unit, source={"rwid": rwid, "status": "PACKAGE_READY"})


def _files(layout, project):
    """A completed-case folder, a history workspace and a verification record: what a real archived case owns."""
    done = layout.completed_case_dir(project) / "20260823T123535Z-282ce76f37ef"
    done.mkdir(parents=True)
    (done / "案卷包.zip").write_bytes(b"zip-bytes-" + project.encode())
    (done / "source-evidence.json").write_text('{"x":1}', encoding="utf-8")
    history = layout.history_workspaces / f"{project}-20260823T123535Z-282ce76f37ef"
    history.mkdir(parents=True)
    (history / "manifest.json").write_text('{"m":1}', encoding="utf-8")
    layout.verification_records.mkdir(parents=True, exist_ok=True)
    (layout.verification_records / f"{project}-20260823T123535Z-fa96780b329d.json").write_text('{"v":1}', encoding="utf-8")


def _detail_evidence(tmp_path, project, rwid, unit, day, name):
    body = {
        "url": f"http://source.invalid/#/xfjd/projectDetail?runId=1&RWID={rwid}&path=x",
        "listRow": [unit, "2025-12-29", "2026-01-13", day, "2026-01-07", "承办人", "已结案", "某消防救援局"],
        "text": f"项目基础信息\n项目编号：{project}\n单位名称\n{unit}\n",
    }
    path = tmp_path / "evidence" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _plan(tmp_path, layout, *, day="2025-12-31", plan_id=PLAN_ID, project=P_OUT, rwid="32100802731", unit=UNIT_OUT):
    evidence = _detail_evidence(tmp_path, project, rwid, unit, day, f"{project}.json")
    plan = {
        "schemaVersion": scope.PLAN_SCHEMA, "planId": plan_id, "scopeFromDate": "2026-01-01",
        "authorization": {"quote": "统一好 从初查时间在26年 及以后的案卷"},
        "items": [{
            "projectNo": project, "rwid": rwid, "unitName": unit, "initialInspectionDate": day,
            "reason": "来源任务检查日期早于 2026-01-01",
            "evidence": [{"kind": "SOURCE_DETAIL_WITH_TASK_ROW", "path": str(evidence), "sha256": _sha(evidence)}],
        }],
    }
    path = tmp_path / f"plan-{plan_id}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path


def _setup(tmp_path, layout):
    _case(layout, P_OUT, UNIT_OUT, "32100802731")
    _case(layout, P_IN_OLD, UNIT_IN_OLD, "32100791178")
    _case(layout, P_IN, UNIT_IN, "32100999999")
    _files(layout, P_OUT)
    _files(layout, P_IN)
    return _plan(tmp_path, layout)


def _tree(layout, project):
    out = {}
    for base in (layout.completed_case_dir(project), layout.history_workspaces, layout.verification_records):
        if base.is_dir():
            for f in base.rglob("*"):
                if f.is_file() and project in str(f):
                    out[str(f.relative_to(layout.root))] = f.read_bytes()
    return out


# ------------------------------------------------------------------------------------------- date semantics


def test_year_of_the_project_number_never_decides_scope():
    assert scope.in_scope_day(date(2026, 1, 5)) is True
    assert scope.in_scope_day(date(2025, 12, 31)) is False
    assert scope.in_scope_day(date(2027, 3, 1)) is True            # 以后年份也在范围内
    assert scope.in_scope_day(None) is None                         # 未知日期永不剔除


@pytest.mark.parametrize("value,fallback,verdict", [
    ("2026-06-08", False, "IN"), ("2025-12-31", False, "OUT"), ("2025-08-27", True, "OUT"),
    ("2025-12-24", True, "UNPROVEN"), ("2026-01-04", True, "UNPROVEN"), ("2026-08-17", True, "IN"), (None, False, "UNPROVEN"),
])
def test_date_basis_never_proves_scope_from_an_acceptance_fallback_near_the_boundary(value, fallback, verdict):
    assert scope.date_basis_proves_scope(value, fallback=fallback) == verdict


# ------------------------------------------------------------------------------------------- listing & reconcile


def _listing(tmp_path, rows, *, total=None, **filters):
    value = {
        "schemaVersion": scope.LISTING_SCHEMA, "observedAt": "2026-11-11T12:00:00+08:00",
        "filters": {"dateField": "检查日期", "start": "2026-01-01", "end": "2027-12-31", "creationRangeCleared": True,
                    "jurisdiction": "全部管辖单位(含派出所)", **filters},
        "taskTypes": [{"type": "daily", "totalCount": len(rows) if total is None else total, "rows": rows}],
    }
    path = tmp_path / "listing.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _row(unit, day):
    return {"unit": unit, "plannedStart": "2026-01-01", "plannedEnd": "2026-01-31", "inspectionDate": day, "status": "已结案"}


def test_reconcile_lists_out_of_scope_candidates_and_new_source_tasks_read_only(tmp_path, layout):
    _case(layout, P_OUT, UNIT_OUT, "1")
    _case(layout, P_IN_OLD, UNIT_IN_OLD, "2")
    _case(layout, P_IN, UNIT_IN, "3")
    listing = scope.load_listing(_listing(tmp_path, [_row(UNIT_IN_OLD, "2026-01-05"), _row(UNIT_IN, "2026-06-08"), _row("全新单位", "2026-11-11")]))
    before = layout.waterline_json.read_bytes()
    report = scope.reconcile_listing(layout, listing)
    assert layout.waterline_json.read_bytes() == before
    assert report["counts"]["matched"] == 2 and report["counts"]["ledgerCasesWithoutListingRow"] == 1
    assert [c["projects"][0]["projectNo"] for c in report["outOfScopeCandidates"]] == [P_OUT]
    assert report["outOfScopeCandidates"][0]["needsCaseLevelProof"] is True
    assert [t["unit"] for t in report["newSourceTasks"]] == ["全新单位"]


def test_reconcile_unit_names_match_through_whitespace_and_bracket_variants(tmp_path, layout):
    _case(layout, P_IN, "威斯康 工程塑料（无锡）有限公司", "3")
    listing = scope.load_listing(_listing(tmp_path, [_row("威斯康工程塑料(无锡)有限公司", "2026-06-08")]))
    assert scope.reconcile_listing(layout, listing)["counts"]["matched"] == 1


@pytest.mark.parametrize("mutation,message", [
    ({"dateField": "创建时间"}, "检查日期"),
    ({"start": "2026-03-01"}, "晚于 2026-01-01"),
    ({"creationRangeCleared": False}, "创建时间筛选必须放宽"),
    ({"jurisdiction": "本单位"}, "全部管辖单位"),
])
def test_listing_must_prove_the_filter_that_makes_it_complete(tmp_path, mutation, message):
    with pytest.raises(scope.ScopeError, match=message):
        scope.load_listing(_listing(tmp_path, [_row(UNIT_IN, "2026-06-08")], **mutation))


def test_listing_with_a_page_total_that_differs_from_the_rows_read_is_not_a_complete_listing(tmp_path):
    with pytest.raises(scope.ScopeError, match="不一致"):
        scope.load_listing(_listing(tmp_path, [_row(UNIT_IN, "2026-06-08")], total=2))


def test_listing_row_earlier_than_the_filter_start_means_the_filter_did_not_apply(tmp_path):
    with pytest.raises(scope.ScopeError, match="筛选未生效"):
        scope.load_listing(_listing(tmp_path, [_row(UNIT_IN, "2025-12-31")]))


# ------------------------------------------------------------------------------------------- exclusion


def test_dry_run_validates_everything_and_changes_nothing(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    before_ledger, before_files = layout.waterline_json.read_bytes(), _tree(layout, P_OUT)
    result = scope.exclude_out_of_scope(layout, plan)
    assert result["applied"] is False and result["items"][0]["status"] == "WOULD_EXCLUDE" and result["items"][0]["files"] == 4 - 0
    assert layout.waterline_json.read_bytes() == before_ledger and _tree(layout, P_OUT) == before_files
    assert not (layout.root / scope.RETENTION_DIR).exists()


def test_apply_moves_bytes_into_retention_removes_only_that_case_and_keeps_a_restorable_register(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    before_files = _tree(layout, P_OUT)
    other = layout.waterline_json.read_text(encoding="utf-8")
    record = ws.load_waterline(layout)["cases"][P_OUT]
    result = scope.exclude_out_of_scope(layout, plan, apply=True)
    assert result["applied"] is True and "xlsx" in result
    cases = ws.load_waterline(layout)["cases"]
    assert P_OUT not in cases and P_IN in cases and P_IN_OLD in cases
    assert _tree(layout, P_OUT) == {} and _tree(layout, P_IN)                    # 范围内案卷原位不动
    register = json.loads((layout.root / scope.RETENTION_DIR / PLAN_ID / scope.REGISTER_NAME).read_text(encoding="utf-8"))
    entry = register["entries"][0]
    assert entry["status"] == "EXCLUDED" and entry["ledgerRecord"] == record and len(entry["manifest"]) == 4
    kept = {}
    for f in (layout.root / scope.RETENTION_DIR / PLAN_ID / P_OUT / "files").rglob("*"):
        if f.is_file():
            kept[str(f.relative_to(layout.root / scope.RETENTION_DIR / PLAN_ID / P_OUT / "files"))] = f.read_bytes()
    assert kept == before_files                                                    # 逐字节保留，没有物理抹掉
    assert scope.excluded_projects(layout) == {P_OUT}
    assert other != layout.waterline_json.read_text(encoding="utf-8")


def test_reapplying_a_finished_plan_changes_nothing(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    scope.exclude_out_of_scope(layout, plan, apply=True)
    ledger, register = layout.waterline_json.read_bytes(), (layout.root / scope.RETENTION_DIR / PLAN_ID / scope.REGISTER_NAME).read_bytes()
    again = scope.exclude_out_of_scope(layout, plan, apply=True)
    assert again["items"] == [{"projectNo": P_OUT, "status": "ALREADY_EXCLUDED"}]
    assert layout.waterline_json.read_bytes() == ledger
    assert (layout.root / scope.RETENTION_DIR / PLAN_ID / scope.REGISTER_NAME).read_bytes() == register


def test_restore_puts_files_and_the_ledger_record_back_exactly(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    before_files, record = _tree(layout, P_OUT), ws.load_waterline(layout)["cases"][P_OUT]
    scope.exclude_out_of_scope(layout, plan, apply=True)
    result = scope.restore_excluded(layout, PLAN_ID, P_OUT)
    assert result["files"] == 4
    assert _tree(layout, P_OUT) == before_files and ws.load_waterline(layout)["cases"][P_OUT] == record
    assert scope.excluded_projects(layout) == set()
    with pytest.raises(scope.ScopeError, match="没有处于排除状态"):
        scope.restore_excluded(layout, PLAN_ID, P_OUT)


@pytest.mark.parametrize("change,message", [
    (lambda i: i.update(initialInspectionDate="2026-01-04"), "不早于"),
    (lambda i: i.update(rwid="1"), "RWID"),
    (lambda i: i.update(unitName="别的单位"), "单位名称"),
    (lambda i: i["evidence"][0].update(sha256="sha256:" + "0" * 64), "摘要"),
    (lambda i: i.update(evidence=[]), "缺少证据"),
])
def test_exclusion_refuses_a_plan_whose_evidence_does_not_prove_that_exact_case(tmp_path, layout, change, message):
    plan = _setup(tmp_path, layout)
    value = json.loads(plan.read_text(encoding="utf-8"))
    change(value["items"][0])
    plan.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    before = layout.waterline_json.read_bytes()
    with pytest.raises(scope.ScopeError, match=message):
        scope.exclude_out_of_scope(layout, plan, apply=True)
    assert layout.waterline_json.read_bytes() == before and ws.load_waterline(layout)["cases"][P_OUT]


def test_exclusion_refuses_when_the_task_row_date_in_the_evidence_differs_from_the_claimed_day(tmp_path, layout):
    _case(layout, P_OUT, UNIT_OUT, "32100802731")
    _files(layout, P_OUT)
    evidence = _detail_evidence(tmp_path, P_OUT, "32100802731", UNIT_OUT, "2026-01-04", "late.json")   # 列表行是 2026 年
    plan = _plan(tmp_path, layout)
    value = json.loads(plan.read_text(encoding="utf-8"))
    value["items"][0]["evidence"] = [{"kind": "SOURCE_DETAIL_WITH_TASK_ROW", "path": str(evidence), "sha256": _sha(evidence)}]
    plan.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(scope.ScopeError, match="检查日期"):
        scope.exclude_out_of_scope(layout, plan, apply=True)


def test_a_case_with_an_open_amendment_or_active_collection_is_never_excluded(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    ws.upsert_case(layout, P_OUT, amendment={"status": "OPEN"})
    with pytest.raises(scope.ScopeError, match="修订"):
        scope.exclude_out_of_scope(layout, plan, apply=True)


def test_a_failure_while_saving_the_ledger_puts_every_moved_file_back(tmp_path, layout, monkeypatch):
    plan = _setup(tmp_path, layout)
    before_files, before_ledger = _tree(layout, P_OUT), layout.waterline_json.read_bytes()

    def boom(*_a, **_k):
        raise ws.WorkspaceStateError("磁盘写入失败")

    monkeypatch.setattr(ws, "_save_waterline_unlocked", boom)
    with pytest.raises(ws.WorkspaceStateError):
        scope.exclude_out_of_scope(layout, plan, apply=True)
    assert _tree(layout, P_OUT) == before_files and layout.waterline_json.read_bytes() == before_ledger
    assert not (layout.root / scope.RETENTION_DIR / PLAN_ID / scope.REGISTER_NAME).exists()


def test_plan_start_date_must_match_the_current_scope_rule(tmp_path, layout):
    plan = _setup(tmp_path, layout)
    value = json.loads(plan.read_text(encoding="utf-8"))
    value["scopeFromDate"] = "2025-01-01"
    plan.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(scope.ScopeError, match="起始日"):
        scope.exclude_out_of_scope(layout, plan)
