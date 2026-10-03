"""Isolated fixtures only: no source browser, credentials or business writes."""

import json
import subprocess
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import annual_browser as annual
from scripts import capture_source_case as capture
from scripts import ledger_views as views
from scripts import package_source_case as package
from scripts import source_coverage as coverage
from scripts import source_intake as source
from scripts import workspace_state as ws

NOW = "2026-10-03T10:00:00+08:00"
PROJECT = "99999999T202600001"
OTHER_PROJECT = "99999999T202600002"
RWID = "90000000001"
BATCH = "fixture-known-annual"
UNIT = "合成测试单位"


def fields(result="UNQUALIFIED"):
    return {
        "项目编号": PROJECT,
        "单位名称": UNIT,
        "initialInspection": {
            "inspectionDate": "2025-12-01",
            "products": [{"method": "ONSITE", "result": result}],
        },
    }


def full_fields():
    return {**fields(), "文书目录": [{"title": "合成初查", "currentProject": True,
                                  "children": [{"title": "合成检查记录"}]}]}


@pytest.fixture
def case(tmp_path):
    layout = ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    query = layout.root / "synthetic-query.json"
    query.write_text(json.dumps({"category": coverage.TASK_CATEGORIES[0]}), encoding="utf-8")
    filters = {
        "selectionMode": coverage.ANNUAL_MODE, "year": 2026, "baselineId": "fixture-baseline",
        "taskCategory": coverage.TASK_CATEGORIES[0], "sourceListKind": "CASE_TASK",
        "dateShortcut": "本年", "startDate": "2026-01-01", "endDate": "2026-12-31",
        "taskStatus": "ALL", "lawEnforcementUnit": "ALL", "brigadeScope": "ALL",
        "jurisdiction": "全部管辖单位(含派出所)", "dateFieldLabel": "创建时间",
        "queryRoute": "#/xfjd/cpjd/cxtj/jcwcx/fixture", "queryEvidencePath": query.name,
    }
    source.begin_capture(layout, filters, now=NOW, batch_id=BATCH, origin="https://source.example/")
    for round_no in (1, 2):
        source.add_page(layout, BATCH, 1, [{"rwid": RWID, "unitName": UNIT, "sourceOrder": 1}],
                        1, 1, round_no=round_no, observed_at=NOW)
        source.finalize_capture(layout, BATCH, now=NOW)
    screenshot = layout.root / "synthetic-identity.png"
    screenshot.write_bytes(b"synthetic identity fixture")
    source.add_detail(layout, BATCH, RWID, fields(),
                      source_url=f"https://source.example/#/xfjd/projectDetail?RWID={RWID}",
                      screenshot=screenshot, captured_at=NOW)
    return SimpleNamespace(layout=layout, downloads=downloads,
                           config=SimpleNamespace(download_dir=downloads))


def promote_and_capture(case):
    source.request_case_material_collection(case.layout, BATCH, RWID, PROJECT, requested_at=NOW)
    screenshot = case.layout.root / "synthetic-material-detail.png"
    screenshot.write_bytes(b"synthetic full detail fixture")
    source.add_detail(case.layout, BATCH, RWID, full_fields(),
                      source_url=f"https://source.example/#/xfjd/projectDetail?RWID={RWID}",
                      screenshot=screenshot, captured_at=NOW)


def test_explicit_promotion_preserves_census_identity_job_and_download_gate(case):
    ws.upsert_case(case.layout, PROJECT, state="UPLOADING",
                   upload={"jobId": "original-fixture-job"})
    _, before = source._load_capture(case.layout, BATCH)
    identity, rounds = deepcopy(before["records"][RWID]["detail"]), deepcopy(before["rounds"])
    with pytest.raises(source.SourceIntakeError, match="无需下载"):
        source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)
    source.request_case_material_collection(case.layout, BATCH, RWID, PROJECT, requested_at=NOW)
    with pytest.raises(source.SourceIntakeError, match="必须先进入详情"):
        source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)
    promote_and_capture(case)
    baseline = source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)
    downloaded = case.downloads / "synthetic-case.zip"
    with zipfile.ZipFile(downloaded, "w") as archive:
        archive.writestr("synthetic.txt", "合成文书内容")
    result = source.attach_package(case.layout, BATCH, RWID, downloaded,
                                   download_baseline=baseline, allowed_download_dir=case.downloads,
                                   stability_interval=0, sleep_fn=lambda _: None)
    record = result["records"][RWID]
    assert result["rounds"] == rounds
    assert result["sourceListCount"] == before["sourceListCount"] == 1
    assert result["stableRounds"] == 2
    assert record["detail"] == identity
    assert record["skippedAsUnchanged"] is True
    assert record["collectionDetail"]["fields"] == full_fields()
    assert result["materialCollectionStatus"] == "READY_FOR_ORGANIZATION"
    ledger = ws.load_waterline(case.layout)["cases"][PROJECT]
    assert ledger["upload"]["jobId"] == "original-fixture-job"
    assert views.describe(case.layout, PROJECT, ledger)["initialInspectionDate"] == "2025-12-01"
    assert "latestDocumentCreatedAt" not in ledger["source"]
    evidence = json.loads((case.layout.pending_case_dir(PROJECT) / "source-evidence.json")
                          .read_text(encoding="utf-8"))
    assert evidence["records"][RWID]["fields"] == full_fields()
    assert capture.verified_package(case.layout, PROJECT)["package"]["sha256"] == (
        record["package"]["sha256"]
    )


def test_promotion_is_idempotent_without_touching_timestamps(case):
    source.request_case_material_collection(case.layout, BATCH, RWID, PROJECT, requested_at=NOW)
    path, _ = source._load_capture(case.layout, BATCH)
    before = path.read_bytes()
    source.request_case_material_collection(case.layout, BATCH, RWID, PROJECT)
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["wrong-project", "duplicate-binding", "completed", "qualified"])
def test_invalid_promotions_do_not_change_original_capture(case, fault):
    project = PROJECT
    if fault == "wrong-project":
        project = OTHER_PROJECT
    elif fault == "duplicate-binding":
        ws.upsert_case(case.layout, OTHER_PROJECT,
                       source={"projectIdentitySource": "DETAIL", "rwid": RWID})
    elif fault == "completed":
        ws.upsert_case(case.layout, PROJECT, state="COMPLETED",
                       upload={"status": "VERIFIED"}, nasVerification={"status": "VERIFIED"})
    else:
        path, state = source._load_capture(case.layout, BATCH)
        state["records"][RWID]["detail"]["fields"] = fields("QUALIFIED")
        source._write_json(path, state)
        ws.upsert_case(case.layout, PROJECT, source={"classification": {
            "initialResult": "QUALIFIED", "inspectionDate": "2025-12-01",
        }})
    path, _ = source._load_capture(case.layout, BATCH)
    before = path.read_bytes()
    with pytest.raises(source.SourceIntakeError):
        source.request_case_material_collection(case.layout, BATCH, RWID, project)
    assert path.read_bytes() == before


def test_tampered_request_and_later_completion_still_block_download(case):
    promote_and_capture(case)
    path, state = source._load_capture(case.layout, BATCH)
    state["records"][RWID]["materialCollection"]["projectNo"] = OTHER_PROJECT
    source._write_json(path, state)
    with pytest.raises(source.SourceIntakeError, match="绑定不一致"):
        source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)
    state["records"][RWID]["materialCollection"]["projectNo"] = PROJECT
    source._write_json(path, state)
    ws.upsert_case(case.layout, PROJECT, state="COMPLETED",
                   upload={"status": "VERIFIED"}, nasVerification={"status": "VERIFIED"})
    with pytest.raises(source.SourceIntakeError, match="已完成"):
        source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)


class FixtureBrowser:
    def __init__(self):
        self.opens = self.shots = self.locations = self.configures = 0
        self.hidden = False
        self.selected = False

    def configure(self, *_args, **_kwargs):
        self.configures += 1

    def locate_known_case(self, *_args, **_kwargs):
        if self.hidden:
            raise source.SourceIntakeError("SOURCE_DOCUMENT_HIDDEN")
        self.locations += 1
        return {"baselineCount": 1, "observedCount": 31, "visitedPages": [1, 2],
                "row": {"rwid": RWID, "unitName": UNIT, "sourceOrder": 31}}

    def open_case(self, _rwid):
        self.opens += 1

    def read_full_detail(self, *_args):
        if self.hidden:
            raise source.SourceIntakeError("SOURCE_DOCUMENT_HIDDEN")
        return {"rwid": RWID, "fields": full_fields(),
                "sourceUrl": f"https://source.example/#/xfjd/projectDetail?RWID={RWID}"}

    def capture_detail_screenshot(self, path, _observation):
        self.shots += 1
        path.write_bytes(b"synthetic material screenshot")

    def run(self, code):
        if "return {panelReady:true}" in code:
            self.selected = False
            return {"panelReady": True}
        if "function readPackageSelection" in code:
            return {"ready": True, "leaves": [{"id": "100", "rwid": RWID,
                                               "title": "合成文书"}], "documentCount": 1,
                    "selectedLeafIds": ["100"] if self.selected else [],
                    "allCurrentSelected": self.selected, "foreignSelectedCount": 0}
        self.selected = True
        return {"selected": True}


def test_capture_resumes_accepted_screenshot_without_reopening(case, monkeypatch):
    browser = FixtureBrowser()
    original = source.add_detail
    monkeypatch.setattr(source, "add_detail", lambda *_a, **_kw: (_ for _ in ()).throw(
        source.SourceIntakeError("synthetic crash before intake")))
    with pytest.raises(source.SourceIntakeError, match="synthetic crash"):
        capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)
    monkeypatch.setattr(source, "add_detail", original)
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT, configure=True)
    assert result["status"] == "DETAIL_CAPTURED"
    assert (browser.opens, browser.shots, browser.locations, browser.configures) == (1, 1, 1, 0)
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT, configure=True)
    assert result["status"] == "DETAIL_ALREADY_CAPTURED"
    assert (browser.opens, browser.shots, browser.locations, browser.configures) == (1, 1, 1, 0)


def test_hidden_capture_saves_wait_and_never_navigates(case):
    browser = FixtureBrowser()
    browser.hidden = True
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)
    assert result["status"] == "WAITING" and result["reason"] == "SOURCE_DOCUMENT_HIDDEN"
    assert browser.opens == browser.shots == browser.locations == 0
    directory = capture.material_directory(case.layout, BATCH, PROJECT)
    assert (directory / "detail-wait.json").exists()
    assert not (directory / "capture-action.json").exists()
    browser.hidden = False
    assert capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)["status"] == (
        "DETAIL_CAPTURED"
    )
    assert browser.opens == 1


def test_hidden_after_detail_action_only_observes_on_resume(case):
    browser = FixtureBrowser()
    original_open = browser.open_case

    def open_and_hide(rwid):
        original_open(rwid)
        browser.hidden = True

    browser.open_case = open_and_hide
    assert capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)["status"] == (
        "WAITING"
    )
    browser.hidden = False
    assert capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT, configure=True)[
        "status"
    ] == "DETAIL_CAPTURED"
    assert browser.opens == 1 and browser.configures == 0


def test_hidden_before_detail_click_retains_a_recoverable_non_submission(case):
    browser = FixtureBrowser()
    original = browser.open_case
    attempts = []

    def guarded_open(rwid):
        attempts.append(rwid)
        if len(attempts) == 1:
            browser.hidden = True
            raise source.SourceIntakeError("SOURCE_DOCUMENT_HIDDEN")
        original(rwid)

    browser.open_case = guarded_open
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)
    assert result["status"] == "WAITING" and browser.opens == 0
    directory = capture.material_directory(case.layout, BATCH, PROJECT)
    action = json.loads((directory / "capture-action.json").read_text(encoding="utf-8"))
    assert action["submitted"] is False
    browser.hidden = False
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT, configure=True)
    assert result["status"] == "DETAIL_CAPTURED" and browser.opens == 1
    assert browser.configures == 0
    resumed = json.loads((directory / "capture-action-resume-1.json").read_text(encoding="utf-8"))
    assert resumed["priorRefusal"] == action and resumed["result"]["submitted"] is True


def test_unknown_detail_click_resumes_by_observation_without_replay(case):
    browser = FixtureBrowser()
    attempts = []
    original_read = browser.read_full_detail

    def unknown_open(rwid):
        attempts.append(rwid)
        raise source.SourceIntakeError("synthetic click result unknown")

    browser.open_case = unknown_open
    browser.read_full_detail = lambda *_args: (_ for _ in ()).throw(
        source.SourceIntakeError("synthetic detail not committed"))
    with pytest.raises(source.SourceIntakeError, match="not committed"):
        capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)
    browser.read_full_detail = original_read
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT, configure=True)
    assert result["status"] == "DETAIL_CAPTURED"
    assert attempts == [RWID] and browser.configures == 0 and browser.locations == 1


def test_old_page_data_is_saved_as_waiting_without_an_identity_action(case):
    browser = FixtureBrowser()
    observation = {"ready": False, "reason": "PAGER_CHANGED_BUT_ROWS_OLD", "pageNumber": 8,
                   "pageSize": 50, "totalCount": 395, "observedFirstRowId": 1,
                   "observedLastRowId": 50, "visibilityState": "visible"}

    def stale_rows(*_args, **_kwargs):
        error = source.SourceIntakeError("PAGER_CHANGED_BUT_ROWS_OLD")
        error.observation = observation
        raise error

    browser.locate_known_case = stale_rows
    result = capture.capture_known_case(case.layout, browser, BATCH, RWID, PROJECT)
    assert result["status"] == "WAITING" and result["observation"] == observation
    directory = capture.material_directory(case.layout, BATCH, PROJECT)
    assert (directory / "list-wait.json").exists()
    assert not (directory / "capture-action.json").exists()
    assert browser.opens == browser.shots == 0
    assert source._load_capture(case.layout, BATCH)[1]["sourceListCount"] == 1


def test_detail_wait_exposes_hidden_state_for_immediate_preservation():
    codes = []
    fake = SimpleNamespace(full_detail_reader=annual.Session.full_detail_reader,
                           run=lambda code: (codes.append(code) or {"waiting": True,
                               "observation": {"reason": "SOURCE_DOCUMENT_HIDDEN"}}))
    with pytest.raises(source.SourceIntakeError, match="SOURCE_DOCUMENT_HIDDEN") as result:
        annual.Session.read_full_detail(fake, RWID, PROJECT, UNIT)
    assert result.value.observation["reason"] == "SOURCE_DOCUMENT_HIDDEN"
    assert "observed.ready||observed.reason==='SOURCE_DOCUMENT_HIDDEN'" in codes[0]


def test_unknown_package_action_reuses_original_request_and_baseline(case, monkeypatch):
    promote_and_capture(case)
    calls = []

    def unknown_action(path):
        calls.append(path)
        request = json.loads(path.read_text(encoding="utf-8"))
        Path(request["checkpointPath"]).write_text(json.dumps({
            "state": "RESULT_UNKNOWN", "submitted": None, "projectNo": PROJECT, "rwid": RWID,
        }), encoding="utf-8")
        raise subprocess.TimeoutExpired("synthetic action", 1)

    monkeypatch.setattr(source, "await_download", lambda *_a, **_kw: {
        "status": "WAITING", "zipCandidates": [], "partialCandidates": [],
    })
    browser = FixtureBrowser()
    first = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                       session="fixture-session", start_request=unknown_action)
    request_bytes = calls[0].read_bytes()
    baseline = Path(json.loads(request_bytes)["baselinePath"])
    before = baseline.read_bytes()
    second = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session", start_request=unknown_action)
    assert first["packageClickAttempts"] == 1 and second["packageClickAttempts"] == 0
    assert len(calls) == 1 and calls[0].read_bytes() == request_bytes
    assert baseline.read_bytes() == before
    with pytest.raises(source.SourceIntakeError, match="结果未知"):
        package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                    session="fixture-session", attempt=2,
                                    start_request=unknown_action)
    assert len(calls) == 1


def test_hidden_package_has_no_download_baseline_or_action(case):
    promote_and_capture(case)
    browser = FixtureBrowser()
    browser.hidden = True
    result = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session")
    assert result["status"] == "WAITING" and result["packageClickAttempts"] == 0
    directory = capture.material_directory(case.layout, BATCH, PROJECT)
    assert (directory / "package-wait.json").exists()
    assert not (directory / "download-request.json").exists()


def test_hidden_at_final_package_guard_keeps_original_request_and_click_count(case, monkeypatch):
    promote_and_capture(case)
    browser, calls, waits = FixtureBrowser(), [], []

    def guarded_start(path):
        calls.append(path)
        request = json.loads(path.read_text(encoding="utf-8"))
        Path(request["checkpointPath"]).write_text(json.dumps({
            "rwid": RWID, "projectNo": PROJECT,
            "state": "SOURCE_DOCUMENT_HIDDEN" if len(calls) == 1 else "NATIVE_FILE_CHECK_REQUIRED",
            "submitted": len(calls) > 1,
        }), encoding="utf-8")

    monkeypatch.setattr(source, "await_download", lambda *_args, **_kwargs: (
        waits.append(True) or {"status": "WAITING", "zipCandidates": [], "partialCandidates": []}))
    result = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session", start_request=guarded_start)
    assert result["submitted"] is False and result["packageClickAttempts"] == 0 and waits == []
    request_bytes = calls[0].read_bytes()
    baseline = Path(json.loads(request_bytes)["baselinePath"])
    before = baseline.read_bytes()
    browser.hidden = True
    result = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session", start_request=guarded_start)
    assert result["packageClickAttempts"] == 0 and len(calls) == 1
    browser.hidden = False
    result = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session", start_request=guarded_start)
    assert result["packageClickAttempts"] == 1 and calls == [calls[0], calls[0]]
    assert calls[0].read_bytes() == request_bytes and baseline.read_bytes() == before
    assert len(waits) == 1
    assert not (calls[0].parent / "download-request-2.json").exists()


def test_incomplete_global_checkbox_state_retains_wait_without_a_download_request(case):
    promote_and_capture(case)
    browser = FixtureBrowser()
    original = browser.run
    browser.run = lambda code: ({"ready": False, "reason": "COMPLETE_SELECTION_STATE_UNAVAILABLE",
                                 "documentCount": 186, "missingCheckboxCount": 163}
                               if "function readPackageSelection" in code else original(code))
    result = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture-session")
    assert result["reason"] == "COMPLETE_SELECTION_STATE_UNAVAILABLE"
    assert result["packageClickAttempts"] == 0
    directory = capture.material_directory(case.layout, BATCH, PROJECT)
    assert not (directory / "download-request.json").exists()
    assert (directory / "package-wait.json").exists()


def test_download_adapter_uses_the_configured_node_entry(tmp_path, monkeypatch):
    runtime = tmp_path / "CodexBrowser/playwright/runtime.json"
    runtime.parent.mkdir(parents=True)
    node = tmp_path / "fixed-node.exe"
    node.write_bytes(b"synthetic runtime fixture")
    runtime.write_text(json.dumps({"node_executable": str(node)}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"checkpointPath": str(tmp_path / "action.json"),
                                  "browserCwd": str(tmp_path)}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(package.subprocess, "run", lambda argv, **kwargs: (
        calls.append((argv, kwargs)) or SimpleNamespace(returncode=0)))
    package._start_request(request)
    assert calls[0][0][0] == str(node) and calls[0][1]["cwd"] == str(tmp_path)


def test_legal_docx_is_retained_and_safely_extracted_as_an_original(tmp_path):
    from scripts.test_office_package_guard import _archive, _parts

    original = _archive(_parts())
    archive = tmp_path / "synthetic-case.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr("附件/合成整改报告.docx", original)
    before = archive.read_bytes()
    assert source.inspect_zip_package(archive)["fileCount"] == 1
    target = tmp_path / "extracted"
    assert source.safe_extract_package(archive, target)["fileCount"] == 1
    assert (target / "附件/合成整改报告.docx").read_bytes() == original
    assert archive.read_bytes() == before
    with pytest.raises(source.SourceIntakeError, match="嵌套 ZIP"):
        source.inspect_zip_package(archive, max_entries=3)


@pytest.mark.parametrize("filename", ["disguised.docx", "unsupported.xlsx"])
def test_ordinary_nested_zip_is_still_rejected_even_with_office_extension(tmp_path, filename):
    from scripts.test_office_package_guard import _archive

    archive = tmp_path / "synthetic-case.zip"
    with zipfile.ZipFile(archive, "w") as outer:
        outer.writestr(filename, _archive({"inside.txt": b"ordinary nested archive"}))
    with pytest.raises(source.SourceIntakeError, match="嵌套 ZIP"):
        source.inspect_zip_package(archive)
    target = tmp_path / "rejected"
    with pytest.raises(source.SourceIntakeError, match="嵌套 ZIP"):
        source.safe_extract_package(archive, target)
    assert not target.exists()


def test_known_case_location_checks_shifted_page_without_rescanning_from_one():
    base = {"dates": ["2026-01-01", "2026-12-31"], "dateFieldLabel": "创建时间",
            "otherDates": [], "lawEnforcementEmpty": True,
            "queryCategory": coverage.TASK_CATEGORIES[0],
            "inputs": [{"placeholder": "请选择管辖范围", "value": "全部管辖单位(含派出所)"}],
            "pageSize": 50, "totalCount": 395, "totalPages": 8}
    visited = []

    def observation(page):
        return {**base, "pageNumber": page,
                "items": [{"rwid": RWID, "unitName": UNIT}] if page == 7 else []}

    fake = SimpleNamespace(read_list=lambda: observation(8))

    def page_to(page, _current):
        visited.append(page)
        return observation(page)

    fake.page_to = page_to
    found = annual.Session.locate_known_case(fake, RWID, UNIT, 300, year=2026,
                                             category=coverage.TASK_CATEGORIES[0],
                                             baseline_count=365)
    assert visited == [8, 6, 7]
    assert found["row"]["rwid"] == RWID and found["observedCount"] == 395
    assert 1 not in visited


def test_known_case_location_has_a_bound_even_when_target_disappears():
    base = {"dates": ["2026-01-01", "2026-12-31"], "dateFieldLabel": "创建时间",
            "otherDates": [], "lawEnforcementEmpty": True, "inputs": [
                {"placeholder": "请选择管辖范围", "value": "全部管辖单位(含派出所)"}],
            "queryCategory": coverage.TASK_CATEGORIES[0], "pageSize": 50,
            "totalCount": 5000, "totalPages": 100, "pageNumber": 50, "items": []}
    visited = []
    fake = SimpleNamespace(read_list=lambda: base)
    fake.page_to = lambda page, _current: (visited.append(page) or {**base, "pageNumber": page})
    with pytest.raises(source.SourceIntakeError, match="SOURCE_KNOWN_CASE_NOT_FOUND"):
        annual.Session.locate_known_case(fake, RWID, UNIT, 3000, year=2026,
                                         category=coverage.TASK_CATEGORIES[0],
                                         baseline_count=4900, max_pages=4)
    assert len(visited) == 4


def test_far_page_jump_submits_once_and_waits_for_actual_target():
    codes, reads = [], []
    fake = SimpleNamespace(run=lambda code: codes.append(code))
    fake.read_list = lambda page: (reads.append(page) or {"pageNumber": page, "totalPages": 100})
    result = annual.Session.page_to(fake, 77, {"pageNumber": 1, "totalPages": 100})
    assert result["pageNumber"] == 77 and reads == [77] and len(codes) == 1
    assert ".btn-next" not in codes[0] and "fill('77')" in codes[0]


def test_approval_date_cannot_claim_creation_year():
    observation = {"dates": ["2026-01-01", "2026-12-31"], "dateFieldLabel": "审批日期",
                   "otherDates": [], "lawEnforcementEmpty": True, "inputs": []}
    with pytest.raises(source.SourceIntakeError, match="日期字段"):
        annual.check_filters(observation, 2026)
