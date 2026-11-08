from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter

from scripts import ocr_runtime as ocr
from scripts.mineru_v4 import OPTIONS


def command_input(call):
    return call["inputs"][0].parent if call["batch"] else call["inputs"][0]


def make_pdf(path: Path, pages: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    writer.write(path)


def make_output(input_path: Path, output: Path, *, legacy=False) -> None:
    alias = input_path.stem
    pages = len(PdfReader(input_path).pages) if input_path.suffix == ".pdf" else 1
    directory = output / alias / "standard"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "images").mkdir(exist_ok=True)
    (directory / "images" / "sample.jpg").write_bytes(b"fixture-image")
    (directory / f"{alias}.md").write_text("正文 ![](images/sample.jpg)", encoding="utf-8")
    data = (
        {"pdf_info": [{"page_idx": index} for index in range(pages)]}
        if legacy
        else {
            "schema": "docvortex.middle",
            "schema_version": "2.0",
            "is_full_document": True,
            "pages": [{"page_idx": index, "blocks": []} for index in range(pages)],
        }
    )
    (directory / f"{alias}_middle.json").write_text(json.dumps(data), encoding="utf-8")
    name = f"{alias}_content_list.json" if legacy else f"{alias}_structured_content.json"
    contents = [] if legacy else {"is_full_document": True, "pages": data["pages"]}
    (directory / name).write_text(json.dumps(contents), encoding="utf-8")
    shutil.copyfile(input_path, directory / f"{alias}_origin{input_path.suffix}")


class Rig:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch):
        self.work = root / "project-work"
        self.work.mkdir()
        self.source = root / "project-source"
        self.output = self.work / "ocr"
        self.wrapper = root / "wrapper.ps1"
        self.wrapper.write_text("# known wrapper", encoding="utf-8")
        self.powershell = root / "pwsh.exe"
        self.powershell.write_bytes(b"fixture")
        self.profile = {
            "imageId": "sha256:" + "a" * 64,
            "engineVersion": "4.0.12",
            "options": OPTIONS.copy(),
            "transport": ocr.TASK_SCHEMA,
            "apiBase": "http://127.0.0.1:18000",
        }
        self.relative = ["甲/记录.pdf", "乙/记录.pdf", "中文 空格/复查😀.pdf"]
        self.inventory: dict[str, Any] = {"sourceRoot": str(self.source), "files": []}
        for relative in self.relative:
            path = self.source / relative
            make_pdf(path)
            self.inventory["files"].append(
                {
                    "relativePath": relative,
                    "absolutePath": str(path),
                    "sha256": ocr.file_hash(path),
                    "sizeBytes": path.stat().st_size,
                    "mimeType": "application/pdf",
                    "pageCount": 2,
                }
            )
        self.calls: list[dict] = []
        monkeypatch.setattr(ocr, "engine_profile", lambda _wrapper: dict(self.profile))
        monkeypatch.setattr(ocr, "run_task", self.run_task)

    def record_call(self, inputs, attempt_path):
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        self.calls.append(
            {
                "inputs": inputs,
                "batch": "BATCH" in attempt["executionMode"],
                "resumed": bool(attempt.get("taskId")),
            }
        )
        attempt.update(taskId="job_fixture", submissionState="SUBMITTED", taskStatus="completed")
        ocr.write_json(attempt_path, attempt)

    def run_task(self, inputs, timeout, poll, output, attempt_path, profile):
        self.record_call(inputs, attempt_path)
        for path in inputs:
            make_output(path, output)
        poll()
        return 0

    def run(self, *, selected=None, batch=True, allowed_roots=None):
        return ocr.run_ocr(
            work=self.work,
            inventory=self.inventory,
            output=self.output,
            selected=self.relative if selected is None else selected,
            wrapper=self.wrapper,
            powershell=self.powershell,
            timeout=5,
            batch=batch,
            allowed_roots=allowed_roots,
        )

    def index(self):
        return json.loads((self.work / "ocr-result.json").read_text(encoding="utf-8"))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def test_batch_uses_one_explicit_isolated_directory_and_reuses_verified_files(rig):
    before = [ocr.file_hash(rig.source / relative) for relative in rig.relative]
    result = rig.run()
    assert result["engineInvocations"] == 1 and result["completedFiles"] == 3
    call = rig.calls[0]
    input_dir = command_input(call)
    assert call["batch"] is True and call["resumed"] is False
    assert input_dir.is_relative_to(rig.work)
    assert len(list(input_dir.iterdir())) == 3
    assert len({path.stem for path in input_dir.iterdir()}) == 3
    assert all(path.name.isascii() for path in input_dir.iterdir())
    assert before == [ocr.file_hash(rig.source / relative) for relative in rig.relative]
    assert rig.run()["cachedFiles"] == 3
    assert len(rig.calls) == 1
    assert all("stdout" not in item for item in rig.index()["mappings"])


def test_per_file_mode_remains_available_and_duplicate_selection_is_deduplicated(rig):
    result = rig.run(batch=False, selected=rig.relative + [rig.relative[0]])
    assert result["engineInvocations"] == 3
    assert all(command_input(call).is_file() and not call["batch"] for call in rig.calls)


def test_subset_does_not_discard_other_verified_mappings(rig):
    rig.run(selected=rig.relative[:2])
    result = rig.run(selected=rig.relative[1:])
    assert result["cachedFiles"] == 1 and len(rig.index()["mappings"]) == 3
    assert len(list(command_input(rig.calls[-1]).iterdir())) == 1


def test_partial_batch_and_interruption_resume_only_unconfirmed_files(rig, monkeypatch):
    def partial(inputs, timeout, poll, output, attempt_path, profile):
        rig.record_call(inputs, attempt_path)
        make_output(sorted(inputs)[0], output)
        raise KeyboardInterrupt

    monkeypatch.setattr(ocr, "run_task", partial)
    with pytest.raises(KeyboardInterrupt):
        rig.run()
    index = rig.index()
    assert index["status"] == "CANCELLED" and len(index["mappings"]) == 1
    assert len(index["pending"]) == 2
    confirmed = index["mappings"][0]
    monkeypatch.setattr(ocr, "run_task", rig.run_task)
    result = rig.run()
    assert result["cachedFiles"] == 1 and result["resumedTasks"] == 1
    assert result["engineInvocations"] == 0 and rig.calls[-1]["resumed"]
    assert len(rig.calls[-1]["inputs"]) == 3
    assert confirmed in rig.index()["mappings"]


def test_nonzero_exit_preserves_success_and_does_not_retry_in_same_call(rig, monkeypatch):
    def partial(inputs, timeout, poll, output, attempt_path, profile):
        rig.record_call(inputs, attempt_path)
        make_output(inputs[0], output)
        return 1

    monkeypatch.setattr(ocr, "run_task", partial)
    with pytest.raises(ocr.OcrError, match="未完整"):
        rig.run()
    assert len(rig.calls) == 1 and len(rig.index()["mappings"]) == 1


@pytest.mark.parametrize("damage", ["markdown", "image", "page", "missing", "hash"])
def test_damaged_cached_output_recomputes_only_affected_file(rig, damage):
    rig.run()
    mapping = rig.index()["mappings"][0]
    directory = rig.work / mapping["outputDir"]
    relative = mapping["sourceRelativePath"]
    artifact = next(item for item in mapping["artifacts"] if item["relativePath"].endswith(".md"))
    markdown = directory / artifact["relativePath"]
    if damage == "markdown":
        markdown.write_text("modified", encoding="utf-8")
    elif damage == "image":
        (markdown.parent / "images" / "sample.jpg").unlink()
    elif damage == "page":
        next(markdown.parent.glob("*_middle.json")).write_text('{"pdf_info":[]}', encoding="utf-8")
    elif damage == "missing":
        markdown.unlink()
    else:
        (markdown.parent / "images" / "sample.jpg").write_bytes(b"different-image")
    result = rig.run()
    assert result["cachedFiles"] == 2 and result["resumedTasks"] == 1
    assert result["engineInvocations"] == 0
    assert rig.index()["mappings"][0]["sourceRelativePath"] == relative
    assert rig.index()["mappings"][0]["outputDir"] != mapping["outputDir"]


def test_markdown_alone_never_counts_as_success(rig, monkeypatch):
    def incomplete(inputs, timeout, poll, output, attempt_path, profile):
        (output / "result.md").write_text("nonempty", encoding="utf-8")
        return 0

    monkeypatch.setattr(ocr, "run_task", incomplete)
    with pytest.raises(ocr.OcrError, match="未完整"):
        rig.run()
    assert rig.index()["mappings"] == []


@pytest.mark.parametrize("previous", ["legacy", "damaged", "wrong-root", "invalid-mappings"])
def test_old_or_damaged_index_does_not_create_false_cache_hits(rig, previous):
    rig.run()
    index = rig.index()
    if previous == "legacy":
        index.pop("schemaVersion")
    elif previous == "wrong-root":
        index["workDir"] = str(rig.work.parent / "another-project")
    elif previous == "invalid-mappings":
        index["mappings"] = None
    path = rig.work / "ocr-result.json"
    path.write_text("{" if previous == "damaged" else json.dumps(index), encoding="utf-8")
    assert rig.run()["cachedFiles"] == 0
    assert len(rig.calls) == 2


def test_source_change_fails_before_writing_or_starting_engine(rig):
    rig.run()
    before = (rig.work / "ocr-result.json").read_bytes()
    make_pdf(rig.source / rig.relative[0], pages=3)
    with pytest.raises(ocr.OcrError, match="源文件已变化"):
        rig.run()
    assert (rig.work / "ocr-result.json").read_bytes() == before
    assert len(rig.calls) == 1


def test_engine_change_invalidates_cache(rig):
    rig.run()
    rig.profile["imageId"] = "sha256:" + "b" * 64
    assert rig.run()["cachedFiles"] == 0
    assert len(rig.calls) == 2


def test_cross_project_source_and_output_are_rejected(rig):
    with pytest.raises(ocr.OcrError, match="当前项目"):
        rig.run(allowed_roots=[rig.work / "source"])
    rig.output = rig.work.parent / "another-project"
    with pytest.raises(ocr.OcrError, match="范围"):
        rig.run()
    assert rig.calls == []


@pytest.mark.parametrize(
    "relative", ["../secret.pdf", "/secret.pdf", "C:/secret.pdf", "x:stream", "a/../b.pdf"]
)
def test_unsafe_relative_sources_fail_closed(rig, relative):
    with pytest.raises(ocr.OcrError):
        rig.run(selected=[relative])
    assert rig.calls == []


def test_empty_selection_is_rejected_without_engine(rig):
    with pytest.raises(ocr.OcrError, match="显式"):
        rig.run(selected=[])
    assert rig.calls == []


def test_project_lock_is_released_after_exception(rig):
    with (
        ocr.project_lock(rig.work),
        pytest.raises(ocr.OcrError, match="正在运行"),
        ocr.project_lock(rig.work),
    ):
        pytest.fail("double writer")
    with ocr.project_lock(rig.work):
        pass


def test_verified_legacy_cache_is_reused_without_relabeling_or_engine_run(rig):
    rig.run(selected=rig.relative[:1])
    mapping = rig.index()["mappings"][0]
    item = ocr.validate_sources(rig.inventory, rig.relative[:1], None)[0]
    directory = rig.output / "legacy"
    make_output(rig.source / item["relative"], directory, legacy=True)
    # Historical MinerU used the stable hash alias rather than source basename.
    middle = next(directory.glob("**/*_middle.json"))
    parent = middle.parent
    for path in list(parent.iterdir()):
        if path.is_file():
            path.rename(parent / path.name.replace(Path(item["relative"]).stem, item["alias"], 1))
    legacy = {
        "imageId": "sha256:" + "b" * 64,
        "wrapperSha256": "sha256:" + "c" * 64,
        "options": ocr.LEGACY_OPTIONS.copy(),
    }
    mapping.update(
        outputDir=directory.relative_to(rig.work).as_posix(),
        engineProfile=legacy,
        artifacts=ocr.artifact_snapshot(directory, item["alias"], item["pages"]),
    )
    assert mapping["artifacts"]
    index = rig.index()
    index["mappings"] = [mapping]
    ocr.write_json(rig.work / "ocr-result.json", index)
    result = rig.run(selected=rig.relative[:1])
    assert result["cachedFiles"] == result["legacyCachedFiles"] == 1
    assert len(rig.calls) == 1 and rig.index()["mappings"][0] == mapping


def test_invalid_current_schema_does_not_fall_back_to_legacy(rig):
    rig.run()
    mapping = rig.index()["mappings"][0]
    directory = rig.work / mapping["outputDir"]
    middle = next(directory.glob("**/*_middle.json"))
    middle.write_text(
        json.dumps(
            {
                "schema": "wrong",
                "schema_version": "2.0",
                "pdf_info": [{"page_idx": 0}, {"page_idx": 1}],
            }
        ),
        encoding="utf-8",
    )
    assert ocr.artifact_snapshot(directory, middle.name.removesuffix("_middle.json"), 2) is None


# ---- Adoption of already finished recognition results (no engine, no network) ----


def _zip_result(tmp: Path, name: str, pages: int, *, producer="mineru", version="4.0.12") -> Path:
    import zipfile

    middle = {
        "metadata": {"producer": {"name": producer, "version": version}},
        "extensions": {"mineru": {"tier": "standard", "parse_mode": "ocr"}},
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "is_full_document": True,
        "pages": [{"page_idx": index, "blocks": []} for index in range(pages)],
    }
    contents = {"is_full_document": True, "pages": middle["pages"]}
    archive = tmp / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("markdown.md", "正文 ![](images/sample.jpg)")
        bundle.writestr("middle_json.json", json.dumps(middle))
        bundle.writestr("structured_content.json", json.dumps(contents))
        bundle.writestr("images/sample.jpg", b"fixture-image")
    return archive


def _digest(path: Path) -> str:
    return ocr.file_hash(path)


def mineru_entry(rig, relative: str, tmp: Path, *, task="job_adopt", project="project-work", **bad):
    item = ocr.validate_sources(rig.inventory, [relative], None)[0]
    archive = _zip_result(tmp, f"zip-{abs(hash((relative, task))) % 10**8}", item["pages"])
    attempt = tmp / f"attempt-{abs(hash((relative, task))) % 10**8}.json"
    record = {
        "sha": item["sha256"].split(":", 1)[1],
        "pn": project,
        "rel": relative,
        "pages": item["pages"],
        "alias": item["sha256"][7:23] + ".pdf",
        "state": "DONE",
        "options": {"tier": "standard", "ocr_mode": "auto", "output_formats": "zip", "page_range": "all"},
        "taskId": task,
        "status": "completed",
        "resultSha256": _digest(archive).split(":", 1)[1],
    }
    record.update(bad.get("record", {}))
    attempt.write_text(json.dumps(record), encoding="utf-8")
    return {
        "engine": "MINERU_V4_TASK",
        "sourceRelativePath": relative,
        "sourceSha256": item["sha256"],
        "pageCount": item["pages"],
        "task": {"taskId": task, "status": "completed"},
        "attemptRecord": {"path": str(attempt), "sha256": _digest(attempt)},
        "resultZip": {"path": str(archive), "sha256": _digest(archive)},
    }


def adopt(rig, entries, *, project="project-work"):
    return ocr.adopt_existing_results(
        work=rig.work,
        inventory=rig.inventory,
        output=rig.output,
        receipts={"schemaVersion": ocr.ADOPTION_SCHEMA, "projectNo": project, "entries": entries},
        project=project,
    )


def forbid_engine_and_network(rig, monkeypatch):
    import socket

    def blocked(*_a, **_k):
        pytest.fail("adoption or reuse must not contact an engine or the network")

    monkeypatch.setattr(rig, "run_task", blocked)
    monkeypatch.setattr(ocr, "run_task", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_adopted_result_is_a_real_ocr_result_and_normal_runs_reuse_it(rig, tmp_path, monkeypatch):
    forbid_engine_and_network(rig, monkeypatch)
    entries = [mineru_entry(rig, rel, tmp_path / "evidence", task=f"job_{i}") for i, rel in enumerate(rig.relative)] if (tmp_path / "evidence").mkdir() is None else []
    summary = adopt(rig, entries)
    assert summary["status"] == "COMPLETED" and summary["adoptedFiles"] == 3
    assert summary["engineInvocations"] == 0
    index = rig.index()
    assert index["schemaVersion"] == "OcrResultV1" and index["pending"] == []
    mapping = index["mappings"][0]
    assert mapping["engineProfile"]["transport"] == ocr.ADOPTED_MINERU
    assert mapping["adoption"]["taskId"].startswith("job_") and mapping["adoption"]["resultZipSha256"]
    # The ordinary entry now reuses everything: zero engine runs, zero requests.
    result = rig.run(selected=rig.relative)
    assert result["cachedFiles"] == 3 and result["engineInvocations"] == 0
    assert rig.calls == []
    # A second adoption is a no-op that does not create another result directory.
    before = sorted(p.name for p in rig.output.iterdir())
    again = adopt(rig, entries)
    assert again["adoptedFiles"] == 0 and again["cachedFiles"] == 3
    assert sorted(p.name for p in rig.output.iterdir()) == before


def test_same_bytes_in_two_members_keep_distinct_bindings(rig, tmp_path):
    (tmp_path / "evidence").mkdir()
    twin = "丙/副本.pdf"
    (rig.source / "丙").mkdir()
    shutil.copyfile(rig.source / rig.relative[0], rig.source / twin)
    path = rig.source / twin
    rig.inventory["files"].append(
        {
            "relativePath": twin,
            "absolutePath": str(path),
            "sha256": ocr.file_hash(path),
            "sizeBytes": path.stat().st_size,
            "mimeType": "application/pdf",
            "pageCount": 2,
        }
    )
    first = mineru_entry(rig, rig.relative[0], tmp_path / "evidence", task="job_same")
    second = dict(first, sourceRelativePath=twin)
    adopt(rig, [first, second])
    mappings = {m["sourceRelativePath"]: m for m in rig.index()["mappings"]}
    assert mappings[rig.relative[0]]["sourceSha256"] == mappings[twin]["sourceSha256"]
    assert mappings[rig.relative[0]]["outputDir"] == mappings[twin]["outputDir"]
    aliases = {
        ocr.validate_sources(rig.inventory, [rel], None)[0]["alias"] for rel in (rig.relative[0], twin)
    }
    assert len(aliases) == 2
    for relative in (rig.relative[0], twin):
        item = ocr.validate_sources(rig.inventory, [relative], None)[0]
        assert ocr.artifact_snapshot(rig.work / mappings[relative]["outputDir"], item["alias"], 2)


@pytest.mark.parametrize(
    "damage",
    ["zip-bytes", "zip-digest", "attempt-task", "attempt-state", "pages", "project", "engine", "source"],
)
def test_adoption_refuses_unverifiable_results_and_keeps_earlier_ones(rig, tmp_path, damage):
    (tmp_path / "evidence").mkdir()
    good = mineru_entry(rig, rig.relative[0], tmp_path / "evidence", task="job_good")
    adopt(rig, [good])
    kept = rig.index()["mappings"]
    bad = mineru_entry(rig, rig.relative[1], tmp_path / "evidence", task="job_bad")
    project = "project-work"
    if damage == "zip-bytes":
        Path(bad["resultZip"]["path"]).write_bytes(b"changed")
    elif damage == "zip-digest":
        bad["resultZip"]["sha256"] = "sha256:" + "0" * 64
    elif damage == "attempt-task":
        bad["task"]["taskId"] = "job_other"
    elif damage == "attempt-state":
        record = Path(bad["attemptRecord"]["path"])
        data = json.loads(record.read_text(encoding="utf-8"))
        data["state"], data["status"] = "FAILED", "failed"
        record.write_text(json.dumps(data), encoding="utf-8")
        bad["attemptRecord"]["sha256"] = _digest(record)
    elif damage == "pages":
        bad["pageCount"] = 9
    elif damage == "project":
        project = "other-case"
    elif damage == "engine":
        archive = _zip_result(tmp_path / "evidence", "wrong-engine", 2, version="3.1.4")
        bad["resultZip"] = {"path": str(archive), "sha256": _digest(archive)}
        record = Path(bad["attemptRecord"]["path"])
        data = json.loads(record.read_text(encoding="utf-8"))
        data["resultSha256"] = _digest(archive).split(":", 1)[1]
        record.write_text(json.dumps(data), encoding="utf-8")
        bad["attemptRecord"]["sha256"] = _digest(record)
    elif damage == "source":
        (rig.source / rig.relative[1]).write_bytes(b"%PDF changed")
    with pytest.raises(ocr.OcrError):
        adopt(rig, [bad], project=project)
    after = rig.index()
    if damage == "project":
        assert after["mappings"] == kept
    else:
        assert [m for m in after["mappings"] if m["sourceRelativePath"] == rig.relative[0]] == kept
        assert all(m["sourceRelativePath"] != rig.relative[1] for m in after["mappings"])
        assert rig.relative[1] in after["pending"] or damage == "source"


def test_adoption_never_adopts_across_cases(rig, tmp_path):
    (tmp_path / "evidence").mkdir()
    entry = mineru_entry(rig, rig.relative[0], tmp_path / "evidence", project="another-case")
    with pytest.raises(ocr.OcrError):
        adopt(rig, [entry])
    assert not (rig.work / "ocr-result.json").exists() or rig.index()["mappings"] == []


def docling_entry(rig, relative: str, tmp: Path, *, with_failed=True, project="project-work"):
    item = ocr.validate_sources(rig.inventory, [relative], None)[0]
    document = tmp / "doc.json"
    document.write_text(
        json.dumps({"schema_name": "DoclingDocument", "pages": {str(i + 1): {} for i in range(2)}}),
        encoding="utf-8",
    )
    markdown = tmp / "doc.md"
    markdown.write_text("识别正文", encoding="utf-8")
    failed = tmp / "failed.json"
    failed.write_text(
        json.dumps(
            {
                "sha": item["sha256"].split(":", 1)[1],
                "pn": project,
                "pages": item["pages"],
                "state": "FAILED",
                "status": "failed",
                "taskId": "job_failed",
            }
        ),
        encoding="utf-8",
    )
    return {
        "engine": "DOCLING_CPU",
        "sourceRelativePath": relative,
        "sourceSha256": item["sha256"],
        "pageCount": item["pages"],
        "engineVersion": "2.138.0",
        "device": "CPU",
        "cloudModelCalls": 0,
        "result": {
            "json": {"path": str(document), "sha256": _digest(document)},
            "markdown": {"path": str(markdown), "sha256": _digest(markdown)},
        },
        "supersedes": [{"attemptRecord": {"path": str(failed), "sha256": _digest(failed)}}] if with_failed else [],
    }


def test_docling_replacement_is_a_separate_engine_layer_and_is_reused(rig, tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    mine = mineru_entry(rig, rig.relative[0], tmp_path / "evidence", task="job_ok")
    other = docling_entry(rig, rig.relative[1], tmp_path / "evidence")
    adopt(rig, [mine, other])
    index = rig.index()
    by = {m["sourceRelativePath"]: m for m in index["mappings"]}
    assert by[rig.relative[0]]["engineProfile"]["transport"] == ocr.ADOPTED_MINERU
    docling = by[rig.relative[1]]
    assert docling["engineProfile"]["engine"] == "Docling" and docling["engineProfile"]["cloudModelCalls"] == 0
    assert docling["adoption"]["supersededMinerUAttempts"][0]["taskStatus"] == "failed"
    assert index["engine"] == "MinerU-Docker+Docling-CPU"
    forbid_engine_and_network(rig, monkeypatch)
    result = rig.run(selected=rig.relative[:2])
    assert result["cachedFiles"] == 2 and result["engineInvocations"] == 0
    # A Docling stand-in is never relabelled as MinerU.
    kept = {m["sourceRelativePath"]: m for m in rig.index()["mappings"]}
    assert kept[rig.relative[1]]["engineProfile"]["transport"] == ocr.ADOPTED_DOCLING


def test_docling_replacement_without_the_failed_mineru_attempt_is_refused(rig, tmp_path):
    (tmp_path / "evidence").mkdir()
    entry = docling_entry(rig, rig.relative[0], tmp_path / "evidence", with_failed=False)
    with pytest.raises(ocr.OcrError):
        adopt(rig, [entry])
    assert rig.index()["mappings"] == [] and rig.index()["pending"] == [rig.relative[0]]


def test_damaged_adopted_output_is_recognised_again_only_for_that_file(rig, tmp_path):
    (tmp_path / "evidence").mkdir()
    entries = [mineru_entry(rig, rel, tmp_path / "evidence", task=f"j{i}") for i, rel in enumerate(rig.relative)]
    adopt(rig, entries)
    broken = rig.index()["mappings"][0]
    victim = next((rig.work / broken["outputDir"]).glob("**/*_middle.json"))
    victim.write_text("{}", encoding="utf-8")
    result = rig.run(selected=rig.relative)
    assert result["cachedFiles"] == 2 and len(rig.calls) == 1


def _blank_page_output(rig, relative: str, *, structured_blocks, middle_has_blocks: bool):
    item = ocr.validate_sources(rig.inventory, [relative], None)[0]
    directory = rig.output / ("blank-" + str(abs(hash(relative))))
    make_output(rig.source / relative, directory)
    middle = next(directory.glob("**/*_middle.json"))
    data = json.loads(middle.read_text(encoding="utf-8"))
    if not middle_has_blocks:
        data["pages"][1] = {"page_idx": 1}
    middle.write_text(json.dumps(data), encoding="utf-8")
    structured = next(directory.glob("**/*_structured_content.json"))
    content = json.loads(structured.read_text(encoding="utf-8"))
    content["pages"][1]["blocks"] = structured_blocks
    structured.write_text(json.dumps(content), encoding="utf-8")
    parent = middle.parent
    for path in list(parent.iterdir()):
        if path.is_file() and path.name.startswith(Path(relative).stem):
            path.rename(parent / path.name.replace(Path(relative).stem, item["alias"], 1))
    return directory, item


def test_blank_page_without_block_list_is_complete_only_when_structured_page_is_empty(rig):
    directory, item = _blank_page_output(
        rig, rig.relative[0], structured_blocks=[], middle_has_blocks=False
    )
    assert ocr.artifact_snapshot(directory, item["alias"], 2) is not None
    directory2, item2 = _blank_page_output(
        rig, rig.relative[1], structured_blocks=[{"type": "text"}], middle_has_blocks=False
    )
    assert ocr.artifact_snapshot(directory2, item2["alias"], 2) is None


def test_copy_tree_contents_copies_bytes_without_metadata(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "markdown.md").write_bytes(b"text")
    (source / "images" / "a.png").write_bytes(b"png")

    def refused(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")  # DrvFs-style metadata refusal

    monkeypatch.setattr(shutil, "copystat", refused)
    with pytest.raises((shutil.Error, PermissionError)):
        shutil.copytree(source, tmp_path / "plain")  # the old call fails on such mounts
    target = tmp_path / "target"
    ocr.copy_tree_contents(source, target)
    assert (target / "markdown.md").read_bytes() == b"text"
    assert (target / "images" / "a.png").read_bytes() == b"png"
    with pytest.raises(FileExistsError):
        ocr.copy_tree_contents(source, target)
