import argparse
import json

import pytest
from pypdf import PdfWriter

import scripts.registry_cli as cli
from scripts.registry_cli import RegistryError, inventory_command, read_json, split_command


def make_work(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    writer = PdfWriter()
    for width in (100, 200, 300, 400):
        writer.add_blank_page(width=width, height=100)
    with (source / "a.pdf").open("wb") as stream:
        writer.write(stream)
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    return work


def item(name, start, end, **extra):
    return {
        "sourceRelativePath": "a.pdf",
        "pageStart": start,
        "pageEnd": end,
        "relativePath": f"normalized/{name}.pdf",
        **extra,
    }


def run(work, items, incremental=False):
    path = work / "split-plan.json"
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    split_command(argparse.Namespace(work_dir=str(work), plan=str(path), incremental=incremental))


def snapshot(work):
    return {
        p.relative_to(work).as_posix(): p.read_bytes()
        for p in sorted(work.rglob("*"))
        if p.is_file() and p.name not in {"split-plan.json"}
    }


def test_incremental_appends_and_preserves_old_entries(tmp_path):
    work = make_work(tmp_path)
    run(work, [item("one", 1, 2)])
    old_index = read_json(work / "split-index.json")
    old_pdf = (work / "normalized/one.pdf").read_bytes()
    run(work, [item("one", 1, 2), item("two", 3, 4)], incremental=True)
    new_index = read_json(work / "split-index.json")
    assert new_index["items"][0] == old_index["items"][0]
    assert [i["relativePath"] for i in new_index["items"]] == [
        "normalized/one.pdf",
        "normalized/two.pdf",
    ]
    assert (work / "normalized/one.pdf").read_bytes() == old_pdf
    assert not (work / cli.SPLIT_INTENT).exists()
    assert not list(work.glob(".split-incremental-*"))


def test_incremental_full_reuse_changes_nothing(tmp_path):
    work = make_work(tmp_path)
    run(work, [item("one", 1, 2)])
    before = snapshot(work)
    run(work, [item("one", 1, 2)], incremental=True)
    assert snapshot(work) == before


@pytest.mark.parametrize("kind", ["parts", "metadata", "tampered", "missing", "orphan"])
def test_incremental_conflicts_are_rejected_without_writes(tmp_path, kind):
    work = make_work(tmp_path)
    run(work, [item("one", 1, 2, documentType="A")])
    if kind == "tampered":
        (work / "normalized/one.pdf").write_bytes(b"%PDF-1.4\nbroken")
    if kind == "missing":
        (work / "normalized/one.pdf").unlink()
    if kind == "orphan":
        (work / "normalized/two.pdf").write_bytes(b"x")
    before = snapshot(work)
    plan = {
        "parts": [item("one", 1, 3, documentType="A")],
        "metadata": [item("one", 1, 2, documentType="B")],
    }.get(kind, [item("one", 1, 2, documentType="A"), item("two", 3, 4)])
    with pytest.raises(RegistryError):
        run(work, plan, incremental=True)
    assert snapshot(work) == before


def test_incremental_requires_existing_index_and_no_intent(tmp_path):
    work = make_work(tmp_path)
    with pytest.raises(RegistryError, match="split-index"):
        run(work, [item("one", 1, 2)], incremental=True)
    run(work, [item("one", 1, 2)])
    (work / cli.SPLIT_INTENT).write_text("{}", encoding="utf-8")
    with pytest.raises(RegistryError, match="未收尾"):
        run(work, [item("one", 1, 2), item("two", 3, 4)], incremental=True)


def test_incremental_failure_keeps_intent_and_old_index(tmp_path, monkeypatch):
    work = make_work(tmp_path)
    run(work, [item("one", 1, 2)])
    old_index_bytes = (work / "split-index.json").read_bytes()
    real = cli.write_json

    def fail_on_index(path, value):
        if path.name == "split-index.json":
            raise OSError("disk full")
        real(path, value)

    monkeypatch.setattr(cli, "write_json", fail_on_index)
    with pytest.raises(OSError):
        run(work, [item("one", 1, 2), item("two", 3, 4)], incremental=True)
    assert (work / "split-index.json").read_bytes() == old_index_bytes
    intent = read_json(work / cli.SPLIT_INTENT)
    assert intent["phase"] == "COMMITTING" and intent["newTargets"] == ["normalized/two.pdf"]
    monkeypatch.setattr(cli, "write_json", real)
    with pytest.raises(RegistryError, match="未收尾"):
        run(work, [item("one", 1, 2), item("two", 3, 4)], incremental=True)
