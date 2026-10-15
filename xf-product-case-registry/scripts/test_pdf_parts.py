import argparse
import json

import pytest
from pypdf import PdfReader, PdfWriter

import scripts.pdf_split_verify as verifier
from scripts.registry_cli import RegistryError, inventory_command, read_json, split_command


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name, widths in (("a.pdf", [100, 200, 300]), ("b.pdf", [400, 500])):
        writer = PdfWriter()
        for width in widths:
            writer.add_blank_page(width=width, height=100)
        with (source / name).open("wb") as stream:
            writer.write(stream)
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    parts = [
        {"sourceRelativePath": "a.pdf", "pageStart": 3, "pageEnd": 3},
        {"sourceRelativePath": "b.pdf", "pageStart": 1, "pageEnd": 2},
        {"sourceRelativePath": "a.pdf", "pageStart": 1, "pageEnd": 1},
    ]
    return work, source, {"items": [{"parts": parts, "relativePath": "normalized/result.pdf"}]}


def execute(work, plan):
    path = work / "split-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    split_command(argparse.Namespace(work_dir=str(work), plan=str(path)))


def test_ordered_multipart_split_and_source_pixel_binding(tmp_path, monkeypatch):
    work, source, plan = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    execute(work, plan)
    target = work / "normalized/result.pdf"
    assert [int(p.mediabox.width) for p in PdfReader(target).pages] == [300, 400, 500, 100]
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    index = read_json(work / "split-index.json")
    assert index["splitIndexVersion"] == 5
    assert index["items"][0]["parts"] == plan["items"][0]["parts"]
    monkeypatch.setattr(
        verifier, "render_page", lambda _, path, p: str(PdfReader(path).pages[p - 1].mediabox)
    )
    assert verifier.verify_split(work, "fixture")["pageCount"] == 4


@pytest.mark.parametrize("kind", ["empty", "mixed", "range", "missing", "bool", "hash"])
def test_multipart_failure_preflights_all_outputs(tmp_path, kind):
    work, source, plan = fixture(tmp_path)
    bad = plan["items"][0]
    if kind == "empty":
        bad["parts"] = []
    elif kind == "mixed":
        bad["sourceRelativePath"] = "a.pdf"
    elif kind == "range":
        bad["parts"][-1]["pageEnd"] = 9
    elif kind == "missing":
        bad["parts"][-1]["sourceRelativePath"] = "missing.pdf"
    elif kind == "bool":
        bad["parts"][-1]["pageStart"] = True
    elif kind == "hash":
        with (source / "b.pdf").open("ab") as stream:
            stream.write(b"\n% changed")
    with pytest.raises(RegistryError):
        execute(work, plan)
    assert not (work / "normalized").exists()


def test_multipart_pixel_order_mismatch_is_rejected(tmp_path, monkeypatch):
    work, _source, plan = fixture(tmp_path)
    execute(work, plan)
    path = work / "split-index.json"
    index = read_json(path)
    index["items"][0]["parts"].reverse()
    path.write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(
        verifier, "render_page", lambda _, path, p: str(PdfReader(path).pages[p - 1].mediabox)
    )
    with pytest.raises(RegistryError, match="页面与原件不一致"):
        verifier.verify_split(work, "fixture")
