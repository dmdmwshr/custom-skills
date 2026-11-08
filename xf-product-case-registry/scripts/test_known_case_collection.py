import copy
"""Isolated fixtures only: no source browser, credentials or business writes."""

import hashlib
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


@pytest.mark.parametrize("fault", ["wrong-project", "duplicate-binding", "completed"])
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
    path, _ = source._load_capture(case.layout, BATCH)
    before = path.read_bytes()
    with pytest.raises(source.SourceIntakeError):
        source.request_case_material_collection(case.layout, BATCH, RWID, project)
    assert path.read_bytes() == before


@pytest.mark.parametrize("result", ["QUALIFIED", "UNKNOWN"])
def test_qualified_or_unknown_cases_may_start_full_package_collection(case, result):
    """2026消防产品案卷整包默认全部获取：合格/未知分类也允许提升到材料采集，并记录请求时的分类。"""
    if result == "QUALIFIED":
        path, state = source._load_capture(case.layout, BATCH)
        state["records"][RWID]["detail"]["fields"] = fields("QUALIFIED")
        source._write_json(path, state)
        ws.upsert_case(case.layout, PROJECT, source={"classification": {
            "initialResult": "QUALIFIED", "inspectionDate": "2025-12-01",
        }})
    promote_and_capture(case)
    _, state = source._load_capture(case.layout, BATCH)
    request = state["records"][RWID]["materialCollection"]
    assert request["initialResultAtRequest"] in {"QUALIFIED", "UNKNOWN", "UNQUALIFIED"}
    if result == "QUALIFIED":
        assert request["initialResultAtRequest"] == "QUALIFIED"
    baseline = source.record_download_baseline(case.layout, BATCH, RWID, case.downloads)
    assert baseline["rwid"] == RWID and baseline["projectNo"] == PROJECT


def test_derived_material_batch_keeps_source_and_annual_coverage_untouched(case):
    """整包采集专用批次：只复制所选 RWID 的稳定年度身份，原批次不改、不进入年度覆盖，请求/下载基线可在新批次进行。"""
    before_source = (case.layout.capture_batches / BATCH / "browser-capture.json").read_bytes()
    derived = "fixture-material-next"
    state = source.derive_material_batch(case.layout, BATCH, derived, [RWID], created_at=NOW)
    assert state["derivedFrom"]["batchId"] == BATCH and "year" not in state["filters"]
    assert set(state["records"]) == {RWID} and state["materialRwids"] == []
    assert (case.layout.capture_batches / BATCH / "browser-capture.json").read_bytes() == before_source
    with pytest.raises(source.SourceIntakeError):
        source.derive_material_batch(case.layout, BATCH, derived, [RWID], created_at=NOW)
    with pytest.raises(source.SourceIntakeError):
        source.derive_material_batch(case.layout, BATCH, "fixture-material-bad", ["90000000999"], created_at=NOW)
    cover = coverage.annual_coverage(case.layout, 2026)
    assert cover["baselineBatchId"] == BATCH
    source.request_case_material_collection(case.layout, derived, RWID, PROJECT, requested_at=NOW)
    screenshot = case.layout.root / "synthetic-derived-detail.png"
    screenshot.write_bytes(b"synthetic derived detail fixture")
    source.add_detail(case.layout, derived, RWID, full_fields(),
                      source_url=f"https://source.example/#/xfjd/projectDetail?RWID={RWID}",
                      screenshot=screenshot, captured_at=NOW)
    baseline = source.record_download_baseline(case.layout, derived, RWID, case.downloads)
    assert baseline["rwid"] == RWID and baseline["projectNo"] == PROJECT


def test_extend_material_batch_marks_provenance_and_never_overwrites(case):
    derived = "fixture-material-extend"
    source.derive_material_batch(case.layout, BATCH, derived, [RWID], created_at=NOW)
    state = source.extend_material_batch(case.layout, derived, BATCH, [RWID], updated_at=NOW)
    assert "derivedFromBatchId" not in state["records"][RWID]  # 已存在的 RWID 不覆盖
    with pytest.raises(source.SourceIntakeError):
        source.extend_material_batch(case.layout, BATCH, BATCH, [RWID], updated_at=NOW)  # 非派生批次不可追加


def test_changed_source_detail_in_material_collection_keeps_old_observation_as_previous_generation(case):
    """整包采集遇到与旧来源证据不同的现场详情：旧观察保存为新代际前代，不覆盖；非整包采集仍拒绝覆盖。"""
    derived = "fixture-material-newgen"
    source.derive_material_batch(case.layout, BATCH, derived, [RWID], created_at=NOW)
    source.request_case_material_collection(case.layout, derived, RWID, PROJECT, requested_at=NOW)
    shot = case.layout.root / "synthetic-newgen.png"
    shot.write_bytes(b"synthetic newgen fixture")
    url = f"https://source.example/#/xfjd/projectDetail?RWID={RWID}"
    # 旧批次留下的本案来源证据(指纹不同于现场详情)：整包采集不再拒绝，旧观察保存为前代
    old_dir = case.layout.root / "原始案卷" / "待处理案卷" / PROJECT
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "source-evidence.json").write_text(json.dumps({
        "schemaVersion": source.SOURCE_EVIDENCE_VERSION, "projectNo": PROJECT, "batchIds": ["fixture-old"],
        "records": {RWID: {"projectNo": PROJECT, "fingerprint": "sha256:old", "fields": {"项目编号": PROJECT}}},
        "tags": [], "createdAt": NOW, "updatedAt": NOW}), encoding="utf-8")
    changed = full_fields()
    source.add_detail(case.layout, derived, RWID, changed, source_url=url, screenshot=shot, captured_at=NOW)
    evidence_file = next((case.layout.root / "原始案卷" / "待处理案卷" / PROJECT).glob("source-evidence.json"))
    record = json.loads(evidence_file.read_text(encoding="utf-8"))["records"][RWID]
    assert "文书目录" in record["fields"]
    assert len(record["previousObservations"]) == 1
    assert "文书目录" not in record["previousObservations"][0]["fields"]
    # 相同详情重放不再追加前代
    source.add_detail(case.layout, derived, RWID, changed, source_url=url, screenshot=shot, captured_at=NOW)
    record = json.loads(evidence_file.read_text(encoding="utf-8"))["records"][RWID]
    assert len(record["previousObservations"]) == 1


CARRIED_DETAIL = {"fingerprint": "sha256:old-detail", "fields": {"项目编号": PROJECT}}
CARRIED_DELIVERY = {"status": "INVALID", "reason": "旧拒接"}
DETAIL_URL = f"https://source.example/#/xfjd/projectDetail?RWID={RWID}"


def carried_batch(case, name):
    """来源批次先带旧 collectionDetail/downloadDelivery，再派生：派生记录的旧值确由来源批次继承。"""
    path, state = source._load_capture(case.layout, BATCH)
    state["records"][RWID]["collectionDetail"] = copy.deepcopy(CARRIED_DETAIL)
    state["records"][RWID]["downloadDelivery"] = copy.deepcopy(CARRIED_DELIVERY)
    source._write_json(path, state)
    source.derive_material_batch(case.layout, BATCH, name, [RWID], created_at=NOW)
    source.request_case_material_collection(case.layout, name, RWID, PROJECT, requested_at=NOW)
    return name


def new_screenshot(case, label):
    shot = case.layout.root / f"synthetic-{label}.png"
    shot.write_bytes(f"synthetic {label} fixture".encode())
    return shot


def capture_bytes(case, batch):
    return (case.layout.capture_batches / batch / "browser-capture.json").read_bytes()


def test_changed_detail_without_request_still_records_conflict_with_fingerprints(case):
    derived = carried_batch(case, "fixture-supersede-plain")
    with pytest.raises(source.SourceIntakeError):
        source.add_detail(case.layout, derived, RWID, full_fields(), source_url=DETAIL_URL,
                          screenshot=new_screenshot(case, "plain"), captured_at=NOW)
    _, state = source._load_capture(case.layout, derived)
    conflict = state["conflicts"][0]
    assert conflict["type"] == "DETAIL_CONFLICT" and conflict["existingFingerprint"] == "sha256:old-detail"
    assert conflict["observedFingerprint"].startswith("sha256:") and state["status"] == "NEEDS_MANUAL_REVIEW"
    assert state["records"][RWID]["collectionDetail"] == CARRIED_DETAIL  # 旧值未动


def test_verified_new_observation_supersedes_only_its_inherited_conflict(case):
    derived = carried_batch(case, "fixture-supersede-ok")
    observation_a = full_fields()
    with pytest.raises(source.SourceIntakeError):                    # 观察 A 形成冲突
        source.add_detail(case.layout, derived, RWID, observation_a, source_url=DETAIL_URL,
                          screenshot=new_screenshot(case, "first"), captured_at=NOW)
    path, state = source._load_capture(case.layout, derived)
    formed = copy.deepcopy(state["conflicts"][0])
    assert formed["existingFingerprint"] == "sha256:old-detail" and formed["observedFingerprint"].startswith("sha256:")
    state["conflicts"] += [
        {"type": "DETAIL_CONFLICT", "rwid": "90000000999", "projectNo": PROJECT},        # 别的 RWID
        {"type": "DETAIL_CONFLICT", "rwid": RWID, "projectNo": PROJECT, "existingFingerprint": "sha256:other"},  # 非对应旧指纹
        {"type": "DETAIL_CONFLICT", "rwid": RWID, "projectNo": PROJECT},                 # 缺旧指纹绑定：不得默认关联
        {"type": "PACKAGE_REJECTED", "rwid": RWID, "projectNo": PROJECT, "reason": "other"},
    ]
    source._write_json(path, state)
    observation_b = {**full_fields(), "备注": "第二次有效观察"}      # 不同于 A 的另一次有效观察
    shot = new_screenshot(case, "second")
    result = source.add_detail(case.layout, derived, RWID, observation_b, source_url=DETAIL_URL,
                               screenshot=shot, captured_at=NOW, supersede_carried=True)
    record = result["records"][RWID]
    carried = record["carriedFromSourceBatch"][0]
    new_fp = record["collectionDetail"]["fingerprint"]
    assert new_fp not in {"sha256:old-detail", formed["observedFingerprint"]}
    assert carried["sourceBatchId"] == BATCH and carried["collectionDetail"] == CARRIED_DETAIL
    assert carried["downloadDelivery"] == CARRIED_DELIVERY and "downloadDelivery" not in record
    assert carried["supersededByFingerprint"] == new_fp
    assert carried["supersededByScreenshotSha256"] == record["collectionDetail"]["screenshot"]["sha256"]
    assert [(c["type"], c["rwid"], c.get("existingFingerprint")) for c in result["conflicts"]] == [
        ("DETAIL_CONFLICT", "90000000999", None),
        ("DETAIL_CONFLICT", RWID, "sha256:other"),
        ("DETAIL_CONFLICT", RWID, None),
        ("PACKAGE_REJECTED", RWID, None),
    ]
    assert result["status"] == "NEEDS_MANUAL_REVIEW"
    resolved = result["resolvedConflicts"]
    assert len(resolved) == 1 and resolved[0]["resolution"] == "CARRIED_DETAIL_SUPERSEDED_BY_VERIFIED_NEW_OBSERVATION"
    # 原冲突字段逐项原样保留(含观察 A 的 observedFingerprint)；当前观察 B 另列
    assert {k: resolved[0][k] for k in formed} == formed
    assert resolved[0]["observedFingerprint"] == formed["observedFingerprint"] != new_fp
    assert resolved[0]["resolvedByObservedFingerprint"] == new_fp
    assert resolved[0]["resolvedByScreenshotSha256"] == carried["supersededByScreenshotSha256"]
    # 相同新观察重放：不再追加前代，也不重复解除
    again = source.add_detail(case.layout, derived, RWID, observation_b, source_url=DETAIL_URL,
                              screenshot=shot, captured_at=NOW, supersede_carried=True)
    assert len(again["records"][RWID]["carriedFromSourceBatch"]) == 1 and len(again["resolvedConflicts"]) == 1


def test_conflict_without_bound_old_fingerprint_is_never_cleared_by_a_valid_observation(case):
    derived = carried_batch(case, "fixture-supersede-unbound")
    path, state = source._load_capture(case.layout, derived)
    state["conflicts"] = [{"type": "DETAIL_CONFLICT", "rwid": RWID, "projectNo": PROJECT}]
    state["status"] = "NEEDS_MANUAL_REVIEW"
    source._write_json(path, state)
    result = source.add_detail(case.layout, derived, RWID, full_fields(), source_url=DETAIL_URL,
                               screenshot=new_screenshot(case, "unbound"), captured_at=NOW,
                               supersede_carried=True)
    assert result["conflicts"] == [{"type": "DETAIL_CONFLICT", "rwid": RWID, "projectNo": PROJECT}]
    assert not result.get("resolvedConflicts") and result["status"] == "NEEDS_MANUAL_REVIEW"
    assert result["records"][RWID]["carriedFromSourceBatch"][0]["collectionDetail"] == CARRIED_DETAIL


def test_supersede_request_without_verifiable_evidence_is_refused_and_writes_nothing(case):
    derived = carried_batch(case, "fixture-supersede-refuse")
    good_shot = new_screenshot(case, "good")

    def refuse(rwid=RWID, screenshot=good_shot):
        before = capture_bytes(case, derived)
        with pytest.raises(source.SourceIntakeError):
            source.add_detail(case.layout, derived, rwid, full_fields(), source_url=DETAIL_URL,
                              screenshot=screenshot, captured_at=NOW, supersede_carried=True)
        assert capture_bytes(case, derived) == before

    refuse(screenshot=None)                                            # 无截图
    refuse(screenshot=case.layout.root / "missing-shot.png")           # 截图不存在
    refuse(rwid="90000000999")                                         # 错 RWID
    path, state = source._load_capture(case.layout, derived)
    state["records"][RWID]["collectionDetail"] = {"fingerprint": "sha256:old-detail", "fields": {"项目编号": PROJECT}, "tampered": True}
    source._write_json(path, state)
    refuse()                                                           # 旧值与来源批次不一致：继承关系不能证明
    state["records"][RWID]["collectionDetail"] = copy.deepcopy(CARRIED_DETAIL)
    state["records"][RWID]["package"] = {"sha256": "sha256:p"}
    source._write_json(path, state)
    refuse()                                                           # 已绑包
    state["records"][RWID].pop("package")
    state.pop("derivedFrom")
    source._write_json(path, state)
    refuse()                                                           # 非派生批次


def test_supersede_request_with_wrong_case_identity_never_moves_carried_detail(case):
    derived = carried_batch(case, "fixture-supersede-identity")
    wrong = full_fields()
    wrong = {**wrong, "项目编号": "32002205C202699999"}
    with pytest.raises(source.SourceIntakeError):
        source.add_detail(case.layout, derived, RWID, wrong, source_url=DETAIL_URL,
                          screenshot=new_screenshot(case, "wrong"), captured_at=NOW, supersede_carried=True)
    with pytest.raises(source.SourceIntakeError):
        source.add_detail(case.layout, derived, RWID, full_fields(),
                          source_url="https://source.example/#/xfjd/projectDetail?RWID=90000000999",
                          screenshot=new_screenshot(case, "wrongurl"), captured_at=NOW, supersede_carried=True)
    _, state = source._load_capture(case.layout, derived)
    record = state["records"][RWID]
    assert record["collectionDetail"] == CARRIED_DETAIL and "carriedFromSourceBatch" not in record
    assert not state.get("resolvedConflicts")


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
    """目标页 77 不在可见页码里：填入一次、动作后新读确认输入框、再提交一次，最后等待实际目标页。
    每个脚本只含一个页面状态动作，动作与观察交替(不再把 fill 与 Enter 放进同一脚本)。"""
    events, reads = [], []

    def run(code):  # 只读探测：目标页码按钮不可见
        events.append("probe-visible-page-number")
        return {"count": 0}

    def act(code):
        kind = "fill" if "fill('77')" in code else "enter" if "press('Enter')" in code else "other"
        assert not ("fill(" in code and "press(" in code), "同一脚本不得同时填入与提交"
        assert ".btn-next" not in code
        events.append(kind)
        return {}

    def observe_form():
        events.append("observe-form")
        return {"pageInput": "77"}

    def read_list(page):
        events.append("read-list")
        reads.append(page)
        return {"pageNumber": page, "totalPages": 100}

    fake = SimpleNamespace(run=run, _act=act, observe_form=observe_form, read_list=read_list)
    result = annual.Session.page_to(fake, 77, {"pageNumber": 1, "totalPages": 100})
    assert result["pageNumber"] == 77 and reads == [77]
    assert events == ["probe-visible-page-number", "fill", "observe-form", "enter", "read-list"]
    assert events.count("fill") == 1 and events.count("enter") == 1  # 目标值只填一次，只提交一次


def test_far_page_jump_refuses_to_submit_when_input_did_not_take_the_target():
    events = []
    fake = SimpleNamespace(
        run=lambda code: {"count": 0},
        _act=lambda code: events.append("fill" if "fill(" in code else "enter") or {},
        observe_form=lambda: {"pageInput": "1"},
        read_list=lambda page: (_ for _ in ()).throw(AssertionError("must not read as if jumped")))
    with pytest.raises(source.SourceIntakeError, match="页码输入框未接受目标页"):
        annual.Session.page_to(fake, 77, {"pageNumber": 1, "totalPages": 100})
    assert events == ["fill"]  # 输入未被接受时不回车提交


def test_approval_date_cannot_claim_creation_year():
    observation = {"dates": ["2026-01-01", "2026-12-31"], "dateFieldLabel": "审批日期",
                   "otherDates": [], "lawEnforcementEmpty": True, "inputs": []}
    with pytest.raises(source.SourceIntakeError, match="日期字段"):
        annual.check_filters(observation, 2026)


BATCH2 = "fixture-known-annual-refresh"


def _zip(path, text):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("synthetic.txt", text)


def _second_batch(case):
    """同一案卷的第二个正式批次（显式更新只在新批次记录里接入新一代）。"""
    _, first = source._load_capture(case.layout, BATCH)
    source.begin_capture(case.layout, first["filters"], now=NOW, batch_id=BATCH2,
                         origin="https://source.example/")
    for round_no in (1, 2):
        source.add_page(case.layout, BATCH2, 1, [{"rwid": RWID, "unitName": UNIT, "sourceOrder": 1}],
                        1, 1, round_no=round_no, observed_at=NOW)
        source.finalize_capture(case.layout, BATCH2, now=NOW)
    shot = case.layout.root / "synthetic-identity-2.png"
    shot.write_bytes(b"synthetic identity fixture 2")
    source.add_detail(case.layout, BATCH2, RWID, fields(),
                      source_url=f"https://source.example/#/xfjd/projectDetail?RWID={RWID}",
                      screenshot=shot, captured_at=NOW)
    source.request_case_material_collection(case.layout, BATCH2, RWID, PROJECT, requested_at=NOW)
    shot2 = case.layout.root / "synthetic-material-detail-2.png"
    shot2.write_bytes(b"synthetic full detail fixture 2")
    source.add_detail(case.layout, BATCH2, RWID, full_fields(),
                      source_url=f"https://source.example/#/xfjd/projectDetail?RWID={RWID}",
                      screenshot=shot2, captured_at=NOW)


def _attach(case, batch, name, text, **kwargs):
    baseline = source.record_download_baseline(case.layout, batch, RWID, case.downloads)
    path = case.downloads / name
    _zip(path, text)
    return source.attach_package(case.layout, batch, RWID, path, download_baseline=baseline,
                                 allowed_download_dir=case.downloads, stability_interval=0,
                                 sleep_fn=lambda _: None, **kwargs)


def test_explicit_refresh_adds_a_legal_new_generation_and_keeps_the_old_one(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    old = first["records"][RWID]["package"]
    old_path = case.layout.root / old["relativePath"]
    old_bytes = old_path.read_bytes()
    _second_batch(case)
    with pytest.raises(source.SourceIntakeError, match="拒绝自动恢复"):          # 无理由不得换包
        _attach(case, BATCH2, "b0.zip", "第二代")
    with pytest.raises(source.SourceIntakeError, match="理由"):                  # 理由必须在允许集合内
        _attach(case, BATCH2, "b1.zip", "第二代", refresh_of_sha=old["sha256"], refresh_reason="WHATEVER")
    with pytest.raises(source.SourceIntakeError, match="拒绝自动恢复"):          # 被更新的包必须是当前代
        _attach(case, BATCH2, "b2.zip", "第二代", refresh_of_sha="sha256:" + "0" * 64,
                refresh_reason="USER_REQUEST")
    second = _attach(case, BATCH2, "b.zip", "第二代", refresh_of_sha=old["sha256"],
                     refresh_reason="RECENT_UNQUALIFIED")
    new = second["records"][RWID]["package"]
    assert new["supersedes"] == old["sha256"] and new["generation"] == 2
    assert new["refreshReason"] == "RECENT_UNQUALIFIED" and new["sha256"] != old["sha256"]
    assert old_path.read_bytes() == old_bytes                                    # 旧代字节与登记永久保留
    head = capture.verified_package(case.layout, PROJECT)
    assert head["package"]["sha256"] == new["sha256"] and head["generations"] == [
        old["sha256"], new["sha256"]]
    evidence = json.loads((case.layout.pending_case_dir(PROJECT) / "source-evidence.json")
                          .read_text(encoding="utf-8"))
    assert evidence["records"][RWID]["previousPackages"][0]["sha256"] == old["sha256"]


def test_two_unrelated_packages_stay_a_conflict_and_never_pick_the_newest(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    _second_batch(case)
    path, state = source._load_capture(case.layout, BATCH2)
    stranger = deepcopy(first["records"][RWID]["package"])
    stranger["sha256"] = "sha256:" + "f" * 64
    stranger["relativePath"] = first["records"][RWID]["package"]["relativePath"]
    state["records"][RWID]["package"] = stranger          # 没有 supersedes 的第二个不同包
    source._write_json(path, state)
    with pytest.raises(source.SourceIntakeError):
        capture.verified_package(case.layout, PROJECT)


def test_unreadable_package_without_explicit_reason_is_not_silently_redownloaded(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    package_path = case.layout.root / first["records"][RWID]["package"]["relativePath"]
    original = package_path.read_bytes()
    package_path.write_bytes(b"corrupt")
    with pytest.raises(source.SourceIntakeError, match="无法回读"):
        capture.verified_package(case.layout, PROJECT)
    with pytest.raises(source.SourceIntakeError, match="理由不在允许集合"):
        package._validated_refresh(case.layout, PROJECT, None, None)
    assert package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)["ofSha"] == (
        first["records"][RWID]["package"]["sha256"])
    package_path.write_bytes(original)                      # 字节还原后坏包前提不再成立
    with pytest.raises(source.SourceIntakeError, match="BAD_LOCAL 不成立"):
        package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)


def _archive_package(case, package_info, *, register=True, rwid=RWID):
    """把待处理原包移到归档代目录，并按真实归档形态只在水位 history.completions 登记原件位置。"""
    src = case.layout.root / package_info["relativePath"]
    folder = case.layout.root / "原始案卷" / "已处理案卷" / PROJECT / "GEN1"
    folder.mkdir(parents=True)
    (folder / src.name).write_bytes(src.read_bytes())
    src.unlink()
    if register:
        with ws._waterline_lock(case.layout):
            data = ws.load_waterline(case.layout)
            record = data.setdefault("cases", {}).setdefault(PROJECT, {"projectNo": PROJECT})
            record.setdefault("source", {})["rwid"] = rwid
            record.setdefault("history", {}).setdefault("completions", []).append(
                {"generation": "GEN1", "packageSha256": package_info["sha256"], "originalPath": str(folder)})
            ws._save_waterline_unlocked(case.layout, data)
    return folder


def test_archived_original_resolves_through_registered_generation_not_stale_pending_path(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    info = first["records"][RWID]["package"]
    _archive_package(case, info)
    head = capture.verified_package(case.layout, PROJECT)
    assert head["package"]["sha256"] == info["sha256"] and head["location"] == "ARCHIVED"
    with pytest.raises(source.SourceIntakeError, match="BAD_LOCAL 不成立"):      # 已归档有效原包不是坏包
        package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)
    assert package._validated_refresh(case.layout, PROJECT, "USER_REQUEST", None)["ofSha"] == info["sha256"]


def test_unregistered_or_mismatched_archive_stays_reliably_missing_or_unknown(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    info = first["records"][RWID]["package"]
    _archive_package(case, info, register=False)            # 未在水位登记：不全盘找同名文件
    with pytest.raises(capture.PackageUnreadable) as missing:
        capture.verified_package(case.layout, PROJECT)
    assert missing.value.state == "FILE_MISSING"
    assert package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)["ofSha"] == info["sha256"]


def test_archive_identity_conflict_is_unknown_and_never_a_bad_local_sha(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    info = first["records"][RWID]["package"]
    _archive_package(case, info, rwid="99999999999")        # 水位 RWID 与捕获登记冲突
    with pytest.raises(capture.PackageUnreadable) as unknown:
        capture.verified_package(case.layout, PROJECT)
    assert unknown.value.state == "UNKNOWN"
    with pytest.raises(capture.PackageUnreadable):
        package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)


def test_archive_through_reparse_ancestor_is_unknown_not_reusable(case):
    """归档目录的祖先是链接/联接时，读取与枚举前即拒绝：UNKNOWN，不得复用或给BAD_LOCAL许可。"""
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    info = first["records"][RWID]["package"]
    folder = _archive_package(case, info)
    outside = case.layout.root.parent / (case.layout.root.name + "-outside-fixture")
    outside.mkdir()
    real = outside / "GEN1"
    folder.rename(real)
    try:
        folder.symlink_to(real, target_is_directory=True)     # 目录链接占据登记位置（Windows 联接同属重解析点）
    except OSError:
        pytest.skip("环境不支持创建目录链接")
    with pytest.raises(capture.PackageUnreadable) as unknown:
        capture.verified_package(case.layout, PROJECT)
    assert unknown.value.state == "UNKNOWN"
    with pytest.raises(capture.PackageUnreadable):
        package._validated_refresh(case.layout, PROJECT, "BAD_LOCAL", None)
    with pytest.raises(capture.PackageUnreadable):
        package._validated_refresh(case.layout, PROJECT, "USER_REQUEST", None)
    assert views.package_decision_for_project(case.layout, PROJECT)["code"] == "PKG_HOLD_PACKAGE_UNKNOWN"


def test_ledger_decision_carries_captured_rwid_and_never_reuses_on_identity_conflict(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    info = first["records"][RWID]["package"]
    _archive_package(case, info, rwid="99999999999")            # 水位RWID与捕获登记冲突
    decision = views.package_decision_for_project(case.layout, PROJECT)
    assert decision["code"] == "PKG_HOLD_PACKAGE_UNKNOWN" and "UNKNOWN" in decision["reasons"]


def test_known_case_default_is_zero_click_reuse_and_bad_refresh_never_touches_the_browser(case):
    promote_and_capture(case)
    first = _attach(case, BATCH, "a.zip", "第一代")
    browser = FixtureBrowser()
    reused = package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                        session="fixture")
    assert reused["status"] == "RESUME_ORIGINAL_PACKAGE" and reused["packageClickAttempts"] == 0
    assert reused["package"]["sha256"] == first["records"][RWID]["package"]["sha256"]
    for reason in ("WHATEVER", "RECENT_UNQUALIFIED"):      # 非法理由 / 当前源证据不构成近期候选
        with pytest.raises(source.SourceIntakeError):
            package.package_known_case(case.layout, case.config, browser, BATCH, RWID, PROJECT,
                                       session="fixture", refresh_reason=reason)
    assert browser.opens == browser.locations == browser.shots == 0


# ---- 已证单案材料采集准入 ----
ADM_PROJECT = "99999999C202600701"
ADM_RWID = "90000000701"
ADM_UNIT = "合成准入单位"
ADM_BRIGADE = "合成消防救援局"


def admission_observation(project=ADM_PROJECT, rwid=ADM_RWID, unit=ADM_UNIT, brigade=ADM_BRIGADE, **changes):
    body = (
        f"项目详情 项目基础信息 项目编号：{project} 单位名称 {unit} 单位类别 一般单位 受理日期 2026年11月06日 "
        f"检查情况 已结案[合格] 消防管辖 {brigade} 单位电话 1 预定检查日期 2026-11-01/2026-11-30 返回 "
        f"检查产品信息(1) 产品名称 手提式干粉灭火器 标题 创建人 "
        f"[监]日常监督抽查-99999999J202601500-{unit} 双随机 [处理中] "
        f"[产]日常监督-{project}-{unit} 张三 [已检查] 产品监督检查 消防产品监督检查记录[合格]"
    )
    value = {"url": f"https://source.example/#/xfjd/projectDetail?RWID={rwid}&path=%2Fx", "observedAt": NOW,
             "projectNo": project, "unit": unit, "brigade": brigade, "body": body, "rwidFromUrl": rwid}
    value.update(changes)
    return value


def derived_for_admission(case, name="fixture-admission"):
    source.derive_material_batch(case.layout, BATCH, name, [RWID], created_at=NOW)
    return name


def snapshot_state(case, batch):
    cap = (case.layout.capture_batches / batch / "browser-capture.json").read_bytes()
    return cap, json.dumps(ws.load_waterline(case.layout), sort_keys=True, ensure_ascii=False)


def test_admission_creates_one_material_record_without_touching_annual_scope(case):
    derived = derived_for_admission(case)
    annual_before = coverage.annual_coverage(case.layout, 2026)
    source_bytes = (case.layout.capture_batches / BATCH / "browser-capture.json").read_bytes()
    _, before = source._load_capture(case.layout, derived)
    state = source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    record = state["records"][ADM_RWID]
    assert record["materialAdmission"]["coverageExcluded"] is True and record["materialAdmission"]["basis"] == "OBSERVED_DETAIL_PRODUCT_CASE"
    assert record["materialAdmission"]["productItems"] == 1                             # 既有正数兼容
    assert record["materialCollection"]["projectNo"] == ADM_PROJECT and ADM_RWID in state["materialRwids"]
    assert state["records"][RWID] == before["records"][RWID]                       # 旧成员原样
    assert state["filters"] == before["filters"] and state["derivedFrom"] == before["derivedFrom"]
    assert state["stableRounds"] == before["stableRounds"] and state["listResult"] == "STABLE"
    assert (case.layout.capture_batches / BATCH / "browser-capture.json").read_bytes() == source_bytes
    assert coverage.annual_coverage(case.layout, 2026) == annual_before
    ledger = ws.load_waterline(case.layout)["cases"][ADM_PROJECT]
    assert ledger["source"]["rwid"] == ADM_RWID and ledger["source"]["projectIdentitySource"] == "DETAIL"
    assert ledger["source"]["materialAdmission"]["evidenceSha256"] == record["materialAdmission"]["evidenceSha256"]


def test_admission_replay_with_same_evidence_writes_nothing(case):
    derived = derived_for_admission(case)
    source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    before = snapshot_state(case, derived)
    again = source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at="2026-11-09T10:00:00+08:00")
    assert snapshot_state(case, derived) == before
    assert len([k for k in again["records"] if k == ADM_RWID]) == 1


@pytest.mark.parametrize("mutate", [
    lambda o: {**o, "projectNo": "99999999C202600702"},                                     # 项目号与原文不符
    lambda o: {**o, "url": "https://source.example/#/xfjd/projectDetail?RWID=90000000799"},  # URL RWID 与读取不符
    lambda o: {**o, "rwidFromUrl": "90000000799"},
    lambda o: {**o, "unit": "别的单位"},
    lambda o: {**o, "brigade": "别的大队"},
    lambda o: {**o, "body": o["body"].replace("[产]", "[监]")},                               # 没有 [产] 根
    lambda o: {**o, "body": o["body"] + " [产]日常监督-99999999C202600709-别案 x"},           # 另有别案 [产] 根
    # (数量0/区段缺失曾在此作为“非产品”拒绝：与用户确认“小旗+[产]即产品监督案卷”冲突，已移到 test_admission_product_quantity_zero_or_missing_is_a_fact_not_the_category)
    lambda o: {**o, "body": o["body"].replace("检查产品信息(1)", "检查产品信息(0)").replace("[产]", "[监]")},   # 数量0且没有[产]根仍拒绝
    lambda o: {**o, "body": o["body"].replace("检查产品信息(1)", "").replace("[产]", "[监]")},                 # 区段缺失且没有[产]根仍拒绝
    lambda o: {k: v for k, v in o.items() if k != "unit"},
    lambda o: {**o, "projectNo": "99999999C202500701", "body": o["body"].replace(ADM_PROJECT, "99999999C202500701")},  # 非2026
    lambda o: {**o, "projectNo": "99999999T202600701", "body": o["body"].replace(ADM_PROJECT, "99999999T202600701")},  # 非C案
])
def test_admission_rejects_unproven_identity_or_product_root_and_writes_nothing(case, mutate):
    derived = derived_for_admission(case)
    before = snapshot_state(case, derived)
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, mutate(admission_observation()), admitted_at=NOW)
    assert snapshot_state(case, derived) == before


def test_admission_refuses_conflicting_rwid_project_or_non_material_batch(case):
    derived = derived_for_admission(case)
    source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    before = snapshot_state(case, derived)
    other_rwid = admission_observation(rwid="90000000702")                                   # 同项目另一 RWID
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, other_rwid, admitted_at=NOW)
    other_project = admission_observation(project="99999999C202600703")                      # 同 RWID 另一项目
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, other_project, admitted_at=NOW)
    changed = admission_observation(); changed["body"] += " 备注"                            # 同案不同证据
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, changed, admitted_at=NOW)
    with pytest.raises(source.SourceIntakeError):                                            # 年度源批次本身不接受
        source.admit_observed_material_case(case.layout, BATCH, admission_observation(), admitted_at=NOW)
    assert snapshot_state(case, derived) == before


def test_admission_requires_the_material_batch_to_keep_its_stable_annual_conditions(case):
    derived = derived_for_admission(case)
    path, state = source._load_capture(case.layout, derived)
    state["stableRounds"] = 1
    source._write_json(path, state)
    before = snapshot_state(case, derived)
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    assert snapshot_state(case, derived) == before


@pytest.mark.parametrize("fail_at", ["after_ledger", "after_record"])
def test_admission_interrupted_save_resumes_without_double_registration(case, monkeypatch, fail_at):
    derived = derived_for_admission(case)
    obs = admission_observation()
    if fail_at == "after_ledger":
        real = source._write_json
        def boom(path, value):
            if str(path).endswith("browser-capture.json") and ADM_RWID in (value.get("records") or {}):
                raise OSError("synthetic write failure")
            return real(path, value)
        monkeypatch.setattr(source, "_write_json", boom)
    else:
        def boom2(*a, **k):
            raise OSError("synthetic request failure")
        monkeypatch.setattr(source, "request_case_material_collection", boom2)
    with pytest.raises(OSError):
        source.admit_observed_material_case(case.layout, derived, obs, admitted_at=NOW)
    monkeypatch.undo()
    state = source.admit_observed_material_case(case.layout, derived, obs, admitted_at=NOW)
    assert list(state["records"]).count(ADM_RWID) == 1 and state["records"][ADM_RWID]["materialCollection"]
    cases = ws.load_waterline(case.layout)["cases"]
    assert list(cases).count(ADM_PROJECT) == 1
    settled = snapshot_state(case, derived)
    source.admit_observed_material_case(case.layout, derived, obs, admitted_at=NOW)
    assert snapshot_state(case, derived) == settled


def _interrupt_after_ledger(case, derived, monkeypatch, obs):
    """总账已写、批记录未写：批次写入阶段注入故障。"""
    real = source._write_json
    def boom(path, value):
        if str(path).endswith("browser-capture.json") and ADM_RWID in (value.get("records") or {}):
            raise OSError("synthetic write failure")
        return real(path, value)
    monkeypatch.setattr(source, "_write_json", boom)
    with pytest.raises(OSError):
        source.admit_observed_material_case(case.layout, derived, obs, admitted_at=NOW)
    monkeypatch.undo()
    _, state = source._load_capture(case.layout, derived)
    assert ADM_RWID not in state["records"]
    assert ws.load_waterline(case.layout)["cases"][ADM_PROJECT]["source"]["materialAdmission"]["rwid"] == ADM_RWID


def test_ledger_only_recovery_accepts_original_evidence_but_never_changed_evidence_or_identity(case, monkeypatch):
    derived = derived_for_admission(case)
    original = admission_observation()
    _interrupt_after_ledger(case, derived, monkeypatch, original)
    before = snapshot_state(case, derived)
    changed_sha = admission_observation(); changed_sha["body"] += " 另一次读取"                 # 同身份、换证据摘要
    changed_unit = admission_observation(unit="换名单位")                                       # 同号同RWID、换单位
    changed_brigade = admission_observation(brigade="换名大队")
    for bad in (changed_sha, changed_unit, changed_brigade):
        with pytest.raises(source.SourceIntakeError):
            source.admit_observed_material_case(case.layout, derived, bad, admitted_at=NOW)
        assert snapshot_state(case, derived) == before                                        # 保冲突，业务 0 写
    state = source.admit_observed_material_case(case.layout, derived, original, admitted_at=NOW)   # 原证据恢复仍可继续
    assert state["records"][ADM_RWID]["materialCollection"]["projectNo"] == ADM_PROJECT
    assert list(ws.load_waterline(case.layout)["cases"]).count(ADM_PROJECT) == 1


def test_existing_ledger_identity_from_other_origin_must_agree_on_unit_and_brigade(case):
    derived = derived_for_admission(case)
    ws.upsert_case(case.layout, ADM_PROJECT, unitName="别的单位", brigadeName=ADM_BRIGADE,
                   source={"rwid": ADM_RWID, "projectIdentitySource": "DETAIL"})
    before = snapshot_state(case, derived)
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    assert snapshot_state(case, derived) == before
    ws.upsert_case(case.layout, ADM_PROJECT, unitName=ADM_UNIT)
    state = source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)
    assert state["records"][ADM_RWID]["projectNo"] == ADM_PROJECT


def test_file_admission_binds_digest_to_the_same_bytes_that_were_parsed(case, monkeypatch, tmp_path):
    derived = derived_for_admission(case)
    target = tmp_path / "observation.json"
    v1 = admission_observation()
    v2 = admission_observation(unit="读取之后被改的单位")
    raw1 = json.dumps(v1, ensure_ascii=False).encode("utf-8")
    target.write_bytes(raw1)
    def swap_after(real):
        def wrapper(*args, **kwargs):
            out = real(*args, **kwargs)
            if args and Path(args[0]) == target:
                target.write_bytes(json.dumps(v2, ensure_ascii=False).encode("utf-8"))   # 读完或哈希完立刻改文件
            return out
        return wrapper
    monkeypatch.setattr(source, "_sha256", swap_after(source._sha256))
    monkeypatch.setattr(source, "_read_json", swap_after(source._read_json))
    real_read = Path.read_bytes
    reads = []
    def counting(self):
        out = real_read(self)
        if self == target:
            reads.append(1)
            target.write_bytes(json.dumps(v2, ensure_ascii=False).encode("utf-8"))
        return out
    monkeypatch.setattr(Path, "read_bytes", counting)
    state = source.admit_observed_material_case(case.layout, derived, target, admitted_at=NOW)
    monkeypatch.undo()
    admission = state["records"][ADM_RWID]["materialAdmission"]
    assert admission["unitName"] == ADM_UNIT                                                   # 校验的是被哈希的那份字节
    assert admission["evidenceSha256"] == "sha256:" + hashlib.sha256(raw1).hexdigest()
    assert len(reads) == 1
    assert json.loads(target.read_text(encoding="utf-8"))["unit"] == "读取之后被改的单位"         # 文件后来确实变了
    with pytest.raises(source.SourceIntakeError):                                              # 之后用变了的文件重放 = 不同依据，拒绝
        source.admit_observed_material_case(case.layout, derived, target, admitted_at=NOW)


def test_file_admission_rejects_unreadable_or_non_object_files(case, tmp_path):
    derived = derived_for_admission(case)
    before = snapshot_state(case, derived)
    for name, data in (("missing.json", None), ("broken.json", b"{not json"), ("list.json", b"[]")):
        target = tmp_path / name
        if data is not None:
            target.write_bytes(data)
        with pytest.raises(source.SourceIntakeError):
            source.admit_observed_material_case(case.layout, derived, target, admitted_at=NOW)
    assert snapshot_state(case, derived) == before


@pytest.mark.parametrize("label,edit,expected", [
    ("zero", lambda b: b.replace("检查产品信息(1)", "检查产品信息(0)"), 0),
    ("missing", lambda b: b.replace("检查产品信息(1)", ""), None),
    ("unknown-count", lambda b: b.replace("检查产品信息(1)", "检查产品信息(未知)"), None),
])
def test_admission_product_quantity_zero_or_missing_is_a_fact_not_the_category(case, label, edit, expected):
    derived = derived_for_admission(case)
    obs = admission_observation(); obs["body"] = edit(obs["body"])
    state = source.admit_observed_material_case(case.layout, derived, obs, admitted_at=NOW)
    admission = state["records"][ADM_RWID]["materialAdmission"]
    assert admission["productItems"] == expected                                             # 0 保留 0；缺失存 None
    assert state["records"][ADM_RWID]["materialCollection"]["projectNo"] == ADM_PROJECT       # 首次包图门槛打开
    assert ws.load_waterline(case.layout)["cases"][ADM_PROJECT]["source"]["materialAdmission"]["productItems"] == expected
    before = snapshot_state(case, derived)                                                   # 同证据重入不刷新、不写
    source.admit_observed_material_case(case.layout, derived, obs, admitted_at="2026-11-10T10:00:00+08:00")
    assert snapshot_state(case, derived) == before


def test_admission_quantity_change_is_a_different_evidence_and_never_refreshes_old_metadata(case):
    derived = derived_for_admission(case)
    source.admit_observed_material_case(case.layout, derived, admission_observation(), admitted_at=NOW)   # 数量1
    before = snapshot_state(case, derived)
    zero = admission_observation(); zero["body"] = zero["body"].replace("检查产品信息(1)", "检查产品信息(0)")
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, zero, admitted_at=NOW)
    assert snapshot_state(case, derived) == before
    _, state = source._load_capture(case.layout, derived)
    assert state["records"][ADM_RWID]["materialAdmission"]["productItems"] == 1


@pytest.mark.parametrize("edit", [
    lambda o: {**o, "rwidFromUrl": "90000000799"},
    lambda o: {**o, "projectNo": "99999999C202600702"},
    lambda o: {**o, "body": o["body"].replace("[产]", "[监]")},
    lambda o: {**o, "unit": "别的单位"},
])
def test_admission_with_zero_quantity_still_requires_identity_and_own_product_root(case, edit):
    derived = derived_for_admission(case)
    obs = admission_observation(); obs["body"] = obs["body"].replace("检查产品信息(1)", "检查产品信息(0)")
    before = snapshot_state(case, derived)
    with pytest.raises(source.SourceIntakeError):
        source.admit_observed_material_case(case.layout, derived, edit(obs), admitted_at=NOW)
    assert snapshot_state(case, derived) == before


# ---------------------------------------------------------------------------------------------
# 多段详情截图按覆盖索引绑定为永久 D 引用(attach_detail_segments)
# ---------------------------------------------------------------------------------------------

def _fake_png(label, width=1200, height=900):
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + b"\x00\x00\x00\x00" + label.encode()


def _segment_fixture(case, covers=((0, 900), (770, 1670), (880, 1780)), scroll=1780):
    """已登记的首段 + 同案索引；返回 (索引路径, 分段文件列表)。"""
    first = case.layout.root / "seg-0.png"
    first.write_bytes(_fake_png("seg0"))
    pending = case.layout.pending_case_dir(PROJECT)
    pending.mkdir(parents=True, exist_ok=True)
    stored = pending / f"案卷详情_{source._sha256(first).removeprefix('sha256:')[:12]}.png"
    stored.write_bytes(first.read_bytes())
    path, state = source._load_capture(case.layout, BATCH)
    state["records"][RWID]["detail"]["screenshot"] = source._evidence_file(stored, Path(case.layout.root))
    source._write_json(path, state)
    files = [first]
    for number in range(1, len(covers)):
        extra = case.layout.root / f"seg-{number}.png"
        extra.write_bytes(_fake_png(f"seg{number}"))
        files.append(extra)
    segments = [{"index": n, "scrollTop": c[0], "clientHeight": 900, "scrollHeight": scroll, "coverFrom": c[0], "coverTo": c[1], "png": str(f),
                 "width": 1200, "height": 900, "sha256": source._sha256(f).removeprefix("sha256:")} for n, (c, f) in enumerate(zip(covers, files, strict=True))]
    index = {"projectNo": PROJECT, "rwid": RWID, "capturedAt": NOW, "sourceUrl": f"https://source.example/#/xfjd/projectDetail?RWID={RWID}", "segments": segments}
    index_path = case.layout.root / "segments-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return index_path, files


def _pending_names(case):
    return sorted(p.name for p in case.layout.pending_case_dir(PROJECT).iterdir())


def test_segments_bind_as_permanent_references_keep_the_first_registered_image_and_replay_with_zero_writes(case):
    index_path, files = _segment_fixture(case)
    before = capture_bytes(case, BATCH)
    result = source.attach_detail_segments(case.layout, BATCH, index_path, attached_at=NOW)
    assert result["status"] == "BOUND" and result["segments"] == 3
    names = _pending_names(case)
    for file in files:
        assert f"案卷详情_{source._sha256(file).removeprefix('sha256:')[:12]}.png" in names
    assert any(name.startswith("案卷详情覆盖索引_") for name in names)
    detail = source._load_capture(case.layout, BATCH)[1]["records"][RWID]["detail"]
    entry = detail["screenshotSegmentSets"][0]
    assert entry["coverage"] == "CONTIGUOUS_TOP_TO_END" and [s["index"] for s in entry["segments"]] == [0, 1, 2]
    assert detail["screenshot"]["sha256"] == source._sha256(files[0]) and capture_bytes(case, BATCH) != before
    stable, listing = capture_bytes(case, BATCH), _pending_names(case)
    again = source.attach_detail_segments(case.layout, BATCH, index_path, attached_at=NOW)
    assert again["status"] == "REUSED" and capture_bytes(case, BATCH) == stable and _pending_names(case) == listing


@pytest.mark.parametrize("mutate,message", [
    (lambda i, files: i["segments"][1].update(coverFrom=1000, coverTo=1700), "空隙"),
    (lambda i, files: [s.update(scrollHeight=2400) for s in i["segments"]], "末端"),
    (lambda i, files: i["segments"][2].update(sha256="0" * 64), "摘要"),
    (lambda i, files: i["segments"][1].update(width=640), "尺寸"),
    (lambda i, files: i.update(rwid="99999999999"), "批次里没有"),
    (lambda i, files: i.update(projectNo="32002299C209999999"), "批次里没有"),
    (lambda i, files: i["segments"][0].update(coverFrom=40), "顶部"),
    (lambda i, files: i["segments"].reverse(), "序号"),
    (lambda i, files: (files[2].write_bytes(b"not a png at all, and long enough to read header"),
                       i["segments"][2].update(sha256=hashlib.sha256(files[2].read_bytes()).hexdigest())), "PNG"),
])
def test_segments_that_are_not_proven_contiguous_for_the_same_case_are_refused_without_any_write(case, mutate, message):
    index_path, files = _segment_fixture(case)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    mutate(index, files)
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    before, listing = capture_bytes(case, BATCH), _pending_names(case)
    with pytest.raises(source.SourceIntakeError, match=message):
        source.attach_detail_segments(case.layout, BATCH, index_path, attached_at=NOW)
    assert capture_bytes(case, BATCH) == before and _pending_names(case) == listing


def test_a_first_segment_that_is_not_the_registered_screenshot_is_refused(case):
    index_path, files = _segment_fixture(case)
    stranger = case.layout.root / "other-first.png"
    stranger.write_bytes(_fake_png("stranger"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["segments"][0].update(png=str(stranger), sha256=source._sha256(stranger).removeprefix("sha256:"))
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    before, listing = capture_bytes(case, BATCH), _pending_names(case)
    with pytest.raises(source.SourceIntakeError, match="首段"):
        source.attach_detail_segments(case.layout, BATCH, index_path, attached_at=NOW)
    assert capture_bytes(case, BATCH) == before and _pending_names(case) == listing


def test_segment_index_with_foreign_absolute_paths_resolves_only_by_same_directory_and_the_recorded_digest(case):
    index_path, files = _segment_fixture(case)
    folder = case.layout.root / "from-another-host"
    folder.mkdir()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for item, file in zip(index["segments"], files, strict=True):
        (folder / file.name).write_bytes(file.read_bytes())
        item["png"] = "D:\\\\somewhere\\\\else\\\\" + file.name
    moved = folder / "segments-index.json"
    moved.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    assert source.attach_detail_segments(case.layout, BATCH, moved, attached_at=NOW)["status"] == "BOUND"
    (folder / files[2].name).write_bytes(_fake_png("tampered"))
    again_dir = case.layout.root / "tampered"
    again_dir.mkdir()
    for file in folder.iterdir():
        (again_dir / file.name).write_bytes(file.read_bytes())
    # 索引里的摘要必须与同目录文件相符；改过的文件被拒绝(前一次绑定已是 BOUND，故用新批次记录前的状态对照不在此测)
    with pytest.raises(source.SourceIntakeError, match="摘要"):
        source._checked_segment_set(index, RWID, PROJECT, again_dir)
