from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import ledger_views, ocr_runtime, registry_cli, source_intake, workspace_state

PROJECT = "99999999T209900001"
GENERATION = "20990101T000000Z-0123456789ab"


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def mirrors(tmp_path):
    previous, target = tmp_path / "previous", tmp_path / "primary"
    downloads, config = tmp_path / "downloads", tmp_path / "workspace.toml"
    downloads.mkdir()
    workspace_state.configure_workspace(
        work_root=previous, download_dir=downloads, config_path=config
    )
    _, layout = workspace_state.resolve_workspace(config_path=config)
    archived = layout.history_workspaces / f"{PROJECT}-{GENERATION}"
    manifest = {"case": {"projectNo": PROJECT}, "files": []}
    save(archived / "manifest.json", manifest)
    save(
        archived / "upload-state.json",
        {
            "stateVersion": 6,
            "projectNo": PROJECT,
            "status": "VERIFIED",
            "manifestSha256": "sha256:"
            + hashlib.sha256((archived / "manifest.json").read_bytes()).hexdigest(),
        },
    )
    receipt = layout.verification_records / f"{PROJECT}-{GENERATION}.json"
    save(
        receipt,
        {
            "recordVersion": 1,
            "projectNo": PROJECT,
            "manifestSha256": "sha256:"
            + hashlib.sha256((archived / "manifest.json").read_bytes()).hexdigest(),
            "archivedWorkspace": str(archived),
        },
    )
    workspace_state.upsert_case(
        layout,
        PROJECT,
        state="COMPLETED",
        archive={
            "generation": GENERATION,
            "workspacePath": str(archived),
            "verificationRecord": str(receipt),
        },
    )
    save(
        layout.batch_dir("unfinished") / "browser-capture.json",
        {
            "schemaVersion": "BrowserCaptureV1",
            "batchId": "unfinished",
            "workspaceRoot": str(previous),
            "status": "CAPTURE_FAILED",
        },
    )
    shutil.copytree(previous, target)
    return previous, target, config


def test_switch_preserves_evidence_and_reads_only_primary_with_original_root_offline(mirrors):
    previous, target, config = mirrors
    before = tree(previous)
    with pytest.raises(workspace_state.WorkspaceStateError, match="未完成采集批次"):
        workspace_state.configure_workspace(work_root=target, config_path=config)
    result = workspace_state.switch_mirror_access(work_root=target, config_path=config)
    assert result["businessFilesRewritten"] is False
    assert tree(previous) == before == tree(target)
    previous.rename(previous.with_name("offline-backup"))
    _, layout = workspace_state.resolve_workspace(config_path=config)
    assert layout.root == target
    assert workspace_state.doctor_workspace(config_path=config)["caseCount"] == 1
    ledger = workspace_state.load_waterline(layout)
    assert ledger["workspaceRoot"] == str(previous)
    _, capture = source_intake._load_capture(layout, "unfinished")
    assert capture["status"] == "CAPTURE_FAILED"
    work, manifest, state, issues = ledger_views.case_inputs(
        layout, PROJECT, ledger["cases"][PROJECT]
    )
    assert work.is_relative_to(target) and manifest["case"]["projectNo"] == PROJECT
    assert state["status"] == "VERIFIED" and not issues
    description = ledger_views.describe(layout, PROJECT, ledger["cases"][PROJECT])
    assert description["stages"]["archived"] is True
    assert description["historicalComplete"] is True
    workspace_state.upsert_case(layout, "99999999T209900002", state="DISCOVERED")
    assert len(workspace_state.load_waterline(layout)["cases"]) == 2
    old = json.loads(
        (previous.with_name("offline-backup") / "案卷水位记录.json").read_text(encoding="utf-8")
    )
    assert len(old["cases"]) == 1


@pytest.mark.parametrize("difference", ["content", "missing", "extra"])
def test_switch_rejects_any_unsynchronized_file_and_preserves_config(mirrors, difference):
    previous, target, config = mirrors
    before = config.read_bytes()
    file = target / "工作区" / "历史工作区" / f"{PROJECT}-{GENERATION}" / "manifest.json"
    if difference == "content":
        file.write_text('{"unexpected":"change"}', encoding="utf-8")
    elif difference == "missing":
        file.unlink()
    else:
        (target / "extra.txt").write_text("new", encoding="utf-8")
    original = tree(previous)
    with pytest.raises(workspace_state.WorkspaceStateError, match="不一致"):
        workspace_state.switch_mirror_access(work_root=target, config_path=config)
    assert config.read_bytes() == before and tree(previous) == original


def test_reference_mapping_cannot_escape_the_logical_root(mirrors):
    previous, target, config = mirrors
    workspace_state.switch_mirror_access(work_root=target, config_path=config)
    _, layout = workspace_state.resolve_workspace(config_path=config)
    with pytest.raises(workspace_state.WorkspaceStateError, match="超出"):
        layout.resolve_recorded_path(previous / ".." / "unrelated.txt")
    assert (
        layout.resolve_recorded_path(previous / "工作区" / PROJECT) == target / "工作区" / PROJECT
    )


def test_ocr_checkpoint_and_upload_mapping_reuse_without_rewriting_old_records(
    mirrors, monkeypatch
):
    previous, target, config = mirrors
    workspace_state.switch_mirror_access(work_root=target, config_path=config)
    _, layout = workspace_state.resolve_workspace(config_path=config)
    monkeypatch.setattr(workspace_state, "default_workspace_config_path", lambda: config)
    work = target / "工作区" / PROJECT
    output = work / "ocr"
    item = {
        "relative": "a.pdf",
        "sha256": "sha256:" + "a" * 64,
        "pages": 1,
        "alias": "fixture",
        "inputName": "fixture.pdf",
    }
    attempt = output / "run-original" / "attempt.json"
    save(
        attempt,
        {
            "schemaVersion": ocr_runtime.TASK_SCHEMA,
            "workDir": str(previous / "工作区" / PROJECT),
            "inputs": [item],
            "engineProfile": {"engine": "existing"},
            "taskId": "same-task",
        },
    )
    before = attempt.read_bytes()
    groups = ocr_runtime.task_groups(
        work, output, [item], {"engine": "existing"}, False, layout.project_recorded_paths
    )
    assert len(groups) == 1 and groups[0][1] == attempt.parent
    assert attempt.read_bytes() == before
    old_map = {"file": str(previous / "工作区" / PROJECT / "normalized" / "a.pdf")}
    new_map = registry_cli.project_upload_paths(old_map, work / "manifest.json")
    assert new_map["file"] == str(work / "normalized" / "a.pdf")
    assert old_map["file"].startswith(str(previous))


def test_switching_access_back_keeps_both_generations_of_path_references(mirrors):
    previous, target, config = mirrors
    workspace_state.switch_mirror_access(work_root=target, config_path=config)
    workspace_state.switch_mirror_access(work_root=previous, config_path=config)
    _, layout = workspace_state.resolve_workspace(config_path=config)
    assert (
        layout.resolve_recorded_path(target / "工作区" / PROJECT) == previous / "工作区" / PROJECT
    )
    assert workspace_state.doctor_workspace(config_path=config)["caseCount"] == 1
