from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from scripts import registry_cli as cli
from scripts import source_intake as source
from scripts import workflow_reporting as report
from scripts import workspace_state as workspace

PROJECT = "99999999T209900001"
OTHER = "99999999T209900002"


@pytest.fixture
def layout(tmp_path):
    return workspace.ensure_workspace_layout(workspace.BusinessLayout.from_root(tmp_path / "root"))


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "DIRECTORY"
        )
        for path in root.rglob("*")
    }


def describe(layout, record=None, *, detail=True, package=True):
    return report.describe_case(
        layout, PROJECT, record or {}, has_detail=detail, has_package=package
    )


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"state": "NEEDS_MANUAL_REVIEW"}, "MANUAL_REVIEW"),
        ({"upload": {"status": "UPLOADING"}}, "RESUME_UPLOAD"),
        ({"state": "UPLOADED_AWAITING_NAS"}, "VERIFY"),
        ({"state": "VERIFIED_PENDING_ARCHIVE"}, "ARCHIVE"),
        (
            {
                "state": "COMPLETED",
                "upload": {"status": "VERIFIED"},
                "nasVerification": {"status": "VERIFIED"},
            },
            "NONE",
        ),
    ],
)
def test_upload_next_action_is_derived_without_restarting_anything(layout, record, expected):
    before = snapshot(layout.root)
    assert describe(layout, record)["nextAction"]["code"] == expected
    assert snapshot(layout.root) == before


def test_local_stage_order_and_pending_ocr(layout):
    work = layout.work_case_dir(PROJECT)
    assert describe(layout, detail=False)["nextAction"]["code"] == "COLLECT_DETAIL"
    assert describe(layout, package=False)["nextAction"]["code"] == "AWAIT_PACKAGE"
    assert describe(layout)["nextAction"]["code"] == "INVENTORY"
    put(work / "inventory.json", {"inventoryVersion": 3})
    assert describe(layout)["nextAction"]["code"] == "ORGANIZE"
    ocr = {
        "schemaVersion": "OcrResultV1",
        "workDir": str(work),
        "status": "PARTIAL",
        "mappings": [{}],
        "pending": ["未完成.pdf"],
    }
    put(work / "ocr-result.json", ocr)
    result = describe(layout)
    assert result["nextAction"]["code"] == "OCR_RESUME"
    assert result["ocr"]["pendingFiles"] == 1 and "OCR" not in result["completedStages"]
    ocr.update(status="COMPLETED", pending=[])
    put(work / "ocr-result.json", ocr)
    put(work / "case-data.json", {"case": {"projectNo": PROJECT}})
    assert describe(layout)["nextAction"]["code"] == "COMPOSE"
    put(work / "manifest.json", {"case": {"projectNo": PROJECT}})
    put(work / "upload-map.json", {"version": 1})
    assert describe(layout)["nextAction"]["code"] == "VALIDATE"
    result = describe(layout, {"local": {"status": "READY_FOR_UPLOAD"}})
    assert result["nextAction"]["code"] == "UPLOAD"
    assert result["nextAction"]["requiresCurrentAuthorization"] is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("upload-state", {"stateVersion": 5, "projectNo": PROJECT, "status": "VERIFIED"}),
        ("upload-state", {"stateVersion": 6, "projectNo": OTHER, "status": "VERIFIED"}),
        ("upload-state", {"stateVersion": 6, "projectNo": PROJECT, "status": {}}),
        ("manifest", {"case": []}),
        ("manifest", {"case": {"projectNo": OTHER}}),
        ("case-data", {"case": {"projectNo": OTHER}}),
        ("inventory", []),
        (
            "ocr-result",
            {
                "schemaVersion": "OcrResultV1",
                "status": "COMPLETED",
                "workDir": "another-project",
                "mappings": [],
                "pending": [],
            },
        ),
    ],
)
def test_malformed_stale_and_cross_case_inputs_do_not_offer_business_writes(layout, name, value):
    put(layout.work_case_dir(PROJECT) / f"{name}.json", value)
    before = snapshot(layout.root)
    result = describe(layout)
    assert result["nextAction"]["code"] == "INSPECT_STATE"
    assert result["nextAction"]["requiresCurrentAuthorization"] is False
    assert snapshot(layout.root) == before


def test_legacy_ocr_is_readable_but_not_claimed_verified(layout):
    put(layout.work_case_dir(PROJECT) / "ocr-result.json", {"mappings": [{"outputDir": "ocr/old"}]})
    result = describe(layout)
    assert result["ocr"]["status"] == "LEGACY_UNVERIFIED"
    assert "OCR" not in result["completedStages"]


def test_archive_resolution_requires_exact_existing_generation_not_prefix_guess(layout):
    generation = "20990821T010203Z-" + "a" * 12
    archived = layout.history_workspaces / f"{PROJECT}-{generation}"
    put(archived / "inventory.json", {"inventoryVersion": 3})
    record = {"archive": {"workspacePath": str(archived), "generation": generation}}
    assert "INVENTORY" in describe(layout, record)["completedStages"]
    record["archive"]["workspacePath"] = str(layout.history_workspaces / f"{OTHER}-{generation}")
    assert describe(layout, record)["nextAction"]["code"] == "INSPECT_STATE"


def test_missing_timings_are_unknown_and_explicit_metadata_is_deduplicated(layout):
    work = layout.work_case_dir(PROJECT)
    before = snapshot(layout.root)
    unknown = report.timing_projection(work, layout.root, f"project:{PROJECT}")
    assert unknown["coverage"] == "UNKNOWN"
    assert unknown["processingSeconds"] is None
    assert snapshot(layout.root) == before
    report.record_timing(
        work,
        layout.root,
        scope_key=f"project:{PROJECT}",
        operation="ocr",
        category="processing",
        started_at="2099-01-01T00:00:00Z",
        duration=1.234,
        outcome="COMPLETED",
    )
    timing_dir = work / "diagnostics" / "timings"
    timing = next(timing_dir.glob("*.json"))
    put(timing_dir / "timing-duplicate.json", json.loads(timing.read_text(encoding="utf-8")))
    put(timing_dir / "timing-malformed.json", {"recordId": []})
    result = report.timing_projection(work, layout.root, f"project:{PROJECT}")
    assert result["recordCount"] == 1 and result["processingSeconds"] == 1.234
    assert result["manualPauseSeconds"] is None
    assert result["issues"] == ["TIMING_RECORD_INVALID"]
    assert report.timing_projection(work, layout.root, f"project:{OTHER}")["recordCount"] == 0


def test_browser_timing_categories_use_metadata_only(layout):
    batch = layout.batch_dir("report-test")
    put(
        batch / "阶段_LIST_fixture.json",
        {
            "schemaVersion": "SourceStageV1",
            "recordId": str(uuid.uuid4()),
            "processingMs": 1200,
            "networkWaitMs": 45000,
            "manualPauseMs": 360000,
        },
    )
    result = report.timing_projection(batch, layout.root, "batch:report-test", browser_stages=True)
    assert result["processingSeconds"] == 1.2
    assert result["networkWaitSeconds"] == 45
    assert result["manualPauseSeconds"] == 360
    assert result["unclassifiedSeconds"] is None


def test_report_and_status_cli_are_pure_local_queries(layout, monkeypatch, capsys):
    source.begin_capture(
        layout,
        {"year": 2099},
        batch_id="report-test",
        now="2099-01-01T00:00:00Z",
        origin="https://source.example/cases",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only query attempted an external or write operation")

    monkeypatch.setattr(cli.httpx, "Client", forbidden)
    monkeypatch.setattr(cli, "record_timing", forbidden)
    monkeypatch.setattr(cli, "ocr_command", forbidden)
    monkeypatch.setattr(cli, "upload_command", forbidden)
    before = snapshot(layout.root)
    for command in ("status", "report"):
        assert cli.main(["ledger", command, "--work-root", str(layout.root)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result
    assert snapshot(layout.root) == before
    output = layout.verification_records / "workflow.md"
    assert (
        cli.main(["ledger", "report", "--work-root", str(layout.root), "--output", str(output)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["reportPath"] == str(output)
    after = snapshot(layout.root)
    assert set(after) - set(before) == {output.relative_to(layout.root).as_posix()}
    assert all(after[key] == value for key, value in before.items())
    assert "不等同于本次重新访问服务器核验" in output.read_text(encoding="utf-8")
    assert "未知" in output.read_text(encoding="utf-8")


def test_report_does_not_replace_evidence_or_escape_output_scope(layout):
    output = layout.verification_records / "report.md"
    report.write_report(output, layout, "original\n")
    with pytest.raises(report.ReportingError, match="不覆盖"):
        report.write_report(output, layout, "replacement\n")
    assert output.read_text(encoding="utf-8") == "original\n"
    with pytest.raises(report.ReportingError, match="超出"):
        report.write_report(layout.root / "waterline.md", layout, "not allowed")


def test_source_strings_cannot_inject_report_rows_or_html():
    assert report._cell("单位|<script>\n下一行") == "单位\\|&lt;script&gt; 下一行"
