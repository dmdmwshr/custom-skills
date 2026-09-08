import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

import scripts.pdf_split_verify as subject
from scripts.registry_cli import RegistryError, pdf_info


@pytest.mark.parametrize("truncated", [False, True])
def test_renderer_uses_file_and_checks_exact_payload(tmp_path, monkeypatch, truncated):
    expected = b"P6\n1 1\n255\n\xff\xff\xff"
    created = []

    def run(command, **kwargs):
        output = Path(command[-1]).with_suffix(".ppm")
        created.append(output)
        output.write_bytes(expected[:-1] if truncated else expected)
        return SimpleNamespace(returncode=0, stdout=b"truncated pipe", stderr=b"")

    monkeypatch.setattr(subject.subprocess, "run", run)
    if truncated:
        with pytest.raises(RegistryError, match="字节数不完整"):
            subject.render_page("renderer", tmp_path / "fixture.pdf", 1)
    else:
        assert subject.render_page("renderer", tmp_path / "fixture.pdf", 1) == expected
    assert created and not created[0].parent.exists()


def fixture_split(tmp_path: Path) -> Path:
    source = tmp_path / "source" / "fixture.pdf"
    target = tmp_path / "normalized" / "fixture.pdf"
    source.parent.mkdir()
    target.parent.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as stream:
        writer.write(stream)
    with target.open("wb") as stream:
        writer.write(stream)
    sha, pages = pdf_info(source)
    inventory = {
        "sourceRoot": str(source.parent),
        "files": [
            {
                "relativePath": source.name,
                "absolutePath": str(source),
                "sha256": sha,
                "pageCount": pages,
            }
        ],
    }
    split = {
        "items": [
            {
                "sourceRelativePath": source.name,
                "relativePath": "normalized/fixture.pdf",
                "sha256": pdf_info(target)[0],
                "pageStart": 1,
                "pageEnd": 1,
            }
        ]
    }
    (tmp_path / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    (tmp_path / "split-index.json").write_text(json.dumps(split), encoding="utf-8")
    return tmp_path


def test_pixel_equal_and_read_only(tmp_path, monkeypatch):
    work = fixture_split(tmp_path)
    before = {str(p): p.read_bytes() for p in work.rglob("*") if p.is_file()}
    monkeypatch.setattr(subject, "render_page", lambda *_: b"P6\n1 1\n255\n\xff\xff\xff")
    result = subject.verify_split(work, "fixture-renderer")
    assert result["allPagesEqual"] and result["pageCount"] == result["fileCount"] == 1
    assert before == {str(p): p.read_bytes() for p in work.rglob("*") if p.is_file()}


def test_pixel_difference_is_rejected(tmp_path, monkeypatch):
    work = fixture_split(tmp_path)
    monkeypatch.setattr(subject, "render_page", lambda _, path, _page: path.parent.name.encode())
    with pytest.raises(RegistryError, match="页面与原件不一致"):
        subject.verify_split(work, "fixture-renderer")


def test_changed_hash_is_rejected(tmp_path, monkeypatch):
    work = fixture_split(tmp_path)
    monkeypatch.setattr(
        subject, "render_page", lambda *_: pytest.fail("must fail before rendering")
    )
    with (work / "normalized/fixture.pdf").open("ab") as stream:
        stream.write(b"\n% changed fixture")
    with pytest.raises(RegistryError, match="哈希已变化"):
        subject.verify_split(work, "fixture-renderer")


def test_target_outside_normalized_is_rejected(tmp_path):
    work = fixture_split(tmp_path)
    split = json.loads((work / "split-index.json").read_text())
    split["items"][0]["relativePath"] = "source/fixture.pdf"
    (work / "split-index.json").write_text(json.dumps(split), encoding="utf-8")
    with pytest.raises(RegistryError, match="路径越界"):
        subject.verify_split(work, "fixture-renderer")
