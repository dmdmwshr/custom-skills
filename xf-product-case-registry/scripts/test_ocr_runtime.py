from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter

from scripts import ocr_runtime as ocr


def command_input(command):
    if "-Path" in command:
        return Path(command[command.index("-Path") + 1])
    mount = next(item for item in command if item.endswith(":/work/input:ro"))
    return Path(mount.removesuffix(":/work/input:ro"))


def make_pdf(path: Path, pages: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    writer.write(path)


def make_output(input_path: Path, output: Path) -> None:
    alias = input_path.stem
    pages = len(PdfReader(input_path).pages) if input_path.suffix == ".pdf" else 1
    directory = output / alias / "hybrid_auto"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "images").mkdir(exist_ok=True)
    (directory / "images" / "sample.jpg").write_bytes(b"fixture-image")
    (directory / f"{alias}.md").write_text("正文 ![](images/sample.jpg)", encoding="utf-8")
    (directory / f"{alias}_middle.json").write_text(
        json.dumps({"pdf_info": [{"page_idx": index} for index in range(pages)]}),
        encoding="utf-8",
    )
    (directory / f"{alias}_content_list.json").write_text("[]", encoding="utf-8")
    make_pdf(directory / f"{alias}_origin.pdf", pages)


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
        self.profile = {"imageId": "sha256:" + "a" * 64, "options": ocr.OPTIONS.copy()}
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
        self.calls: list[list[str]] = []
        monkeypatch.setattr(ocr, "engine_profile", lambda _wrapper: dict(self.profile))
        monkeypatch.setattr(ocr, "run_process", self.run_process)

    def run_process(self, command, timeout, poll, output, image_id):
        self.calls.append(command)
        source = command_input(command)
        for path in sorted(source.iterdir()) if source.is_dir() else [source]:
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
    assert call[:3] == ["docker", "run", "--rm"]
    assert "MINERU_API_MAX_CONCURRENT_REQUESTS=1" in call
    assert "MINERU_MODEL_SOURCE=local" in call
    assert rig.profile["imageId"] in call
    assert "--publish" not in call and "--detach" not in call
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
    assert all(command_input(call).is_file() and "-NoBuild" in call for call in rig.calls)


def test_subset_does_not_discard_other_verified_mappings(rig):
    rig.run(selected=rig.relative[:2])
    result = rig.run(selected=rig.relative[1:])
    assert result["cachedFiles"] == 1 and len(rig.index()["mappings"]) == 3
    assert len(list(command_input(rig.calls[-1]).iterdir())) == 1


def test_partial_batch_and_interruption_resume_only_unconfirmed_files(rig, monkeypatch):
    def partial(command, timeout, poll, output, image_id):
        rig.calls.append(command)
        input_dir = command_input(command)
        make_output(sorted(input_dir.iterdir())[0], output)
        raise KeyboardInterrupt

    monkeypatch.setattr(ocr, "run_process", partial)
    with pytest.raises(KeyboardInterrupt):
        rig.run()
    index = rig.index()
    assert index["status"] == "CANCELLED" and len(index["mappings"]) == 1
    assert len(index["pending"]) == 2
    confirmed = index["mappings"][0]
    monkeypatch.setattr(ocr, "run_process", rig.run_process)
    result = rig.run()
    assert result["cachedFiles"] == 1 and result["engineInvocations"] == 1
    assert len(list(command_input(rig.calls[-1]).iterdir())) == 2
    assert confirmed in rig.index()["mappings"]


def test_nonzero_exit_preserves_success_and_does_not_retry_in_same_call(rig, monkeypatch):
    def partial(command, timeout, poll, output, image_id):
        rig.calls.append(command)
        source = command_input(command)
        make_output(next(source.iterdir()), output)
        return 1

    monkeypatch.setattr(ocr, "run_process", partial)
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
    assert result["cachedFiles"] == 2 and result["engineInvocations"] == 1
    assert rig.index()["mappings"][0]["sourceRelativePath"] == relative
    assert rig.index()["mappings"][0]["outputDir"] != mapping["outputDir"]


def test_markdown_alone_never_counts_as_success(rig, monkeypatch):
    def incomplete(command, timeout, poll, output, image_id):
        (output / "result.md").write_text("nonempty", encoding="utf-8")
        return 0

    monkeypatch.setattr(ocr, "run_process", incomplete)
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


def test_cancel_cleanup_targets_only_its_unique_output_mount(tmp_path, monkeypatch):
    owned, other = "a" * 12, "b" * 12
    calls = []
    output = tmp_path / "unique-result"

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "ps":
            value = owned + "\n" + other
        elif command[1] == "inspect":
            value = json.dumps(
                [
                    {
                        "Type": "bind",
                        "Destination": "/work/output",
                        "Source": str(output if command[-1] == owned else tmp_path / "other"),
                    }
                ]
            )
        else:
            value = ""
        return subprocess.CompletedProcess(command, 0, value, "")

    monkeypatch.setattr(ocr.subprocess, "run", run)
    ocr.stop_owned_container(output, "sha256:" + "f" * 64)
    assert [command[-1] for command in calls if command[1] == "stop"] == [owned]
