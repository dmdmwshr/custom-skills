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
