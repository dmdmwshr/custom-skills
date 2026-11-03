from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from scripts import manual_intake as manual
from scripts import workspace_state as ws

PROJECT = "99999999T209900001"


@pytest.fixture
def pair(tmp_path):
    layout = ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "business"))
    package = tmp_path / "provided.zip"
    pdf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("record.pdf", pdf.getvalue())
    picture = tmp_path / "original.png"

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    picture.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2200, 1100, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * 6600) * 1100))
        + chunk(b"IEND", b"")
    )
    review = {
        "schemaVersion": "ManualCaseIntakeV1",
        "projectNo": PROJECT,
        "fields": {"projectNo": PROJECT, "unitName": "Example", "brigadeCode": "BINHU"},
        "packagePath": str(package),
        "packageSha256": manual.source._sha256(package),
        "screenshotPath": str(picture),
        "screenshotSha256": manual.source._sha256(picture),
        "screenshotReview": {
            "readable": True,
            "projectNo": PROJECT,
            "unitName": "Example",
            "brigadeCode": "BINHU",
            "dimensions": [2200, 1100],
            "readableFields": [
                "projectNo",
                "unitName",
                "brigade",
                "productResults",
                "documentDirectory",
            ],
        },
        "documentMatch": {
            "entryName": "record.pdf",
            "unitName": "Example",
            "documentNo": "N1",
            "sha256": "sha256:" + hashlib.sha256(pdf.getvalue()).hexdigest(),
        },
    }
    return layout, review


def accept_pair(layout, review):
    with patch("pypdf._page.PageObject.extract_text", return_value="Example N1"):
        return manual.accept(layout, review)


def test_manual_pair_preserves_inputs_and_reuses_receipt(pair):
    layout, review = pair
    result = accept_pair(layout, review)
    assert result["status"] == "PRIMARY_RECEIVED"
    receipt = Path(result["evidencePath"])
    original_bytes = receipt.read_bytes()
    assert json.loads(original_bytes)["browserCapturePerformed"] is False
    assert json.loads(original_bytes)["rwid"] is None
    assert Path(review["packagePath"]).is_file() and Path(review["screenshotPath"]).is_file()
    assert accept_pair(layout, review)["status"] == "REUSED"
    assert receipt.read_bytes() == original_bytes
    record = ws.load_waterline(layout)["cases"][PROJECT]
    assert record["source"]["projectIdentitySource"] == "MANUAL_DETAIL"


@pytest.mark.parametrize("change", ["identity", "blurred", "digest", "document"])
def test_manual_pair_rejects_incomplete_or_misbound_evidence(pair, change):
    layout, review = pair
    if change == "identity":
        review["screenshotReview"]["projectNo"] = "99999999T209900002"
    elif change == "blurred":
        review["screenshotReview"]["readable"] = False
    elif change == "digest":
        review["packageSha256"] = "sha256:" + "a" * 64
    else:
        review["documentMatch"]["documentNo"] = "another-case"
    with pytest.raises(ws.WorkspaceStateError):
        accept_pair(layout, review)
    assert ws.load_waterline(layout)["cases"] == {}


def test_manual_pair_keeps_existing_archive_and_upload_state(pair):
    layout, review = pair
    ws.upsert_case(
        layout,
        PROJECT,
        state="COMPLETED",
        archive={"generation": "original"},
        upload={"status": "VERIFIED"},
        source={"materialGaps": [{"document": "missing"}]},
    )
    assert accept_pair(layout, review)["status"] == "PENDING_COMPARISON"
    current = ws.load_waterline(layout)["cases"][PROJECT]
    assert current["state"] == "COMPLETED"
    assert current["archive"]["generation"] == "original"
    assert current["upload"]["status"] == "VERIFIED"
    assert current["source"]["materialGaps"] == [{"document": "missing"}]


def test_source_gap_disposition_is_bound_to_unchanged_gap_list(pair):
    layout, _review = pair
    ws.upsert_case(layout, PROJECT, source={"materialGaps": [{"document": "missing"}]})
    manual.acknowledge_gaps(layout, PROJECT, "User directs available-material closeout")
    source = ws.load_waterline(layout)["cases"][PROJECT]["source"]
    assert manual.gaps_acknowledged(source)
    source["materialGaps"].append({"document": "new gap"})
    assert not manual.gaps_acknowledged(source)


def test_archive_retains_source_gaps_in_receipt(pair):
    layout, _review = pair
    ws.upsert_case(layout, PROJECT, source={"materialGaps": [{"document": "missing"}]})
    layout.pending_case_dir(PROJECT).mkdir()
    layout.work_case_dir(PROJECT).mkdir()
    verification = {
        "schemaVersion": "LocalVerificationV1",
        "status": "LOCAL_VERIFIED",
        "projectNo": PROJECT,
        "manifestSha256": "sha256:" + "a" * 64,
        "packageSha256": "sha256:" + "b" * 64,
        "files": {"file:one": {"sha256": "sha256:" + "c" * 64}},
    }
    with pytest.raises(ws.WorkspaceStateError, match="来源材料"):
        ws.archive_verified_case(
            layout,
            PROJECT,
            verification=verification,
            manifest_sha256="sha256:" + "a" * 64,
            package_sha256="sha256:" + "b" * 64,
        )
    manual.acknowledge_gaps(layout, PROJECT, "User directs available-material closeout")
    result = ws.archive_verified_case(
        layout,
        PROJECT,
        verification=verification,
        manifest_sha256="sha256:" + "a" * 64,
        package_sha256="sha256:" + "b" * 64,
    )
    receipt = json.loads(Path(result["verificationRecord"]).read_text(encoding="utf-8"))
    assert receipt["sourceIssuesRetained"] is True
    assert receipt["sourceMaterialGaps"] == [{"document": "missing"}]
    assert ws.load_waterline(layout)["cases"][PROJECT]["source"]["materialGaps"]


def test_archived_manual_pair_reuses_moved_receipt(pair):
    layout, review = pair
    original = accept_pair(layout, review)
    layout.work_case_dir(PROJECT).mkdir()
    result = ws.archive_verified_case(
        layout,
        PROJECT,
        verification={
            "schemaVersion": "LocalVerificationV1",
            "status": "LOCAL_VERIFIED",
            "projectNo": PROJECT,
            "manifestSha256": "sha256:" + "a" * 64,
            "packageSha256": review["packageSha256"],
            "files": {"file:one": {"sha256": "sha256:" + "c" * 64}},
        },
        manifest_sha256="sha256:" + "a" * 64,
        package_sha256=review["packageSha256"],
    )
    replay = accept_pair(layout, review)
    assert replay["status"] == "REUSED"
    assert Path(replay["evidencePath"]).parent == Path(result["archivedOriginal"])
    assert not Path(original["evidencePath"]).exists()
    source = ws.load_waterline(layout)["cases"][PROJECT]["source"]
    assert (layout.root / source["packageRelativePath"]).is_file()


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _archive(layout, review) -> dict:
    layout.work_case_dir(PROJECT).mkdir()
    return ws.archive_verified_case(
        layout,
        PROJECT,
        verification={
            "schemaVersion": "LocalVerificationV1",
            "status": "LOCAL_VERIFIED",
            "projectNo": PROJECT,
            "manifestSha256": "sha256:" + "a" * 64,
            "packageSha256": review["packageSha256"],
            "files": {"file:one": {"sha256": "sha256:" + "c" * 64}},
        },
        manifest_sha256="sha256:" + "a" * 64,
        package_sha256=review["packageSha256"],
    )


def _second_review(tmp_path: Path, review: dict, *, extra: bool, repack: bool = True) -> dict:
    """A second manual delivery: same identity file, optionally with an additional document."""
    first = Path(review["packagePath"])
    package = tmp_path / ("second-extra.zip" if extra else "second-same.zip")
    with zipfile.ZipFile(first) as source_zip, zipfile.ZipFile(
        package, "w", zipfile.ZIP_DEFLATED
    ) as out:
        for info in source_zip.infolist():
            out.writestr(info.filename, source_zip.read(info))
        if extra:
            out.writestr("added.pdf", b"%PDF-1.4 extra document")
    shot = tmp_path / "second.png"
    shot.write_bytes(Path(review["screenshotPath"]).read_bytes() + b"")
    # a distinct but equally valid screenshot copy keeps the pair key unique
    return {
        **review,
        "packagePath": str(package),
        "packageSha256": manual.source._sha256(package),
        "screenshotPath": str(shot),
        "screenshotSha256": manual.source._sha256(shot),
    }


def test_resolve_identical_material_is_marked_and_not_reprocessed(pair, tmp_path):
    layout, review = pair
    accept_pair(layout, review)
    _archive(layout, review)
    second = _second_review(tmp_path, review, extra=False)
    # Re-zipping changes the package bytes but not its members.
    assert second["packageSha256"] != review["packageSha256"]
    assert accept_pair(layout, second)["status"] == "PENDING_COMPARISON"
    before = _tree(layout.root)
    result = manual.resolve_pending(layout, PROJECT)
    assert [r["status"] for r in result["results"]] == ["RESOLVED_IDENTICAL"]
    record = ws.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "COMPLETED" and not layout.pending_case_dir(PROJECT).exists()
    assert record["archive"]["generation"] and record["completion"]["mode"] == "LOCAL_V1"
    changed = {k for k, v in _tree(layout.root).items() if before.get(k) != v}
    assert changed == {"案卷水位记录.json"}  # only the ledger evidence reference moves
    again = manual.resolve_pending(layout, PROJECT)
    assert [r["status"] for r in again["results"]] == ["REUSED"]


def test_resolve_new_material_starts_resumable_new_generation(pair, tmp_path):
    layout, review = pair
    accept_pair(layout, review)
    archived = _archive(layout, review)
    original_tree = _tree(Path(archived["archivedOriginal"]))
    workspace_tree = _tree(Path(archived["archivedWorkspace"]))
    second = _second_review(tmp_path, review, extra=True)
    assert accept_pair(layout, second)["status"] == "PENDING_COMPARISON"
    result = manual.resolve_pending(layout, PROJECT)
    item = result["results"][0]
    assert item["status"] == "NEW_GENERATION_STARTED" and item["diff"]["added"] == ["added.pdf"]
    record = ws.load_waterline(layout)["cases"][PROJECT]
    pending = layout.pending_case_dir(PROJECT)
    assert record["state"] == "PENDING_ORGANIZATION"
    assert record["source"]["packageSha256"] == second["packageSha256"]
    assert record["source"]["previousGenerations"][0]["packageSha256"] == review["packageSha256"]
    assert (pending / Path(item["pendingPackage"]).name).is_file()
    # Earlier generation, its workspace and receipts stay exactly as they were.
    assert _tree(Path(archived["archivedOriginal"])) == original_tree
    assert _tree(Path(archived["archivedWorkspace"])) == workspace_tree
    assert record["archive"]["generation"] and record["history"]["completions"]
    # Resume is idempotent.
    after = _tree(pending)
    again = manual.resolve_pending(layout, PROJECT)
    assert again["results"][0]["status"] == "NEW_GENERATION_STARTED"
    assert _tree(pending) == after


def test_resolve_keeps_pending_when_baseline_missing_or_case_active(pair, tmp_path):
    layout, review = pair
    ws.upsert_case(layout, PROJECT, state="COMPLETED", archive={"generation": "g"})
    second = _second_review(tmp_path, review, extra=True)
    assert accept_pair(layout, second)["status"] == "PENDING_COMPARISON"
    missing = manual.resolve_pending(layout, PROJECT)["results"][0]
    assert missing["status"] == "BASELINE_MISSING"
    binding = next(iter(ws.load_waterline(layout)["cases"][PROJECT]["source"]["manualIntakes"].values()))
    assert binding["status"] == "PENDING_COMPARISON"

    # An unfinished earlier generation is never overwritten.
    ws.upsert_case(layout, PROJECT, state="PENDING_ORGANIZATION", source={"packageSha256": review["packageSha256"]})
    original = tmp_path / "business" / "orig.zip"
    shutil.copy(review["packagePath"], original)
    ws.upsert_case(layout, PROJECT, source={"packageRelativePath": "orig.zip"})
    blocked = manual.resolve_pending(layout, PROJECT)["results"][0]
    assert blocked["status"] == "BLOCKED_ACTIVE_CASE"
    assert not layout.pending_case_dir(PROJECT).exists()


def test_resolve_covers_historical_v2_completion(pair, tmp_path):
    layout, review = pair
    folder = layout.completed_case_dir(PROJECT) / "20250101T000000Z-legacy"
    folder.mkdir(parents=True)
    legacy = folder / "old.zip"
    shutil.copy(review["packagePath"], legacy)
    ws.upsert_case(
        layout,
        PROJECT,
        state="COMPLETED",
        upload={"status": "VERIFIED"},
        nasVerification={"status": "VERIFIED"},
        archive={"generation": "legacy", "originalPath": str(folder)},
        source={"packageSha256": review["packageSha256"]},
    )
    second = _second_review(tmp_path, review, extra=True)
    assert accept_pair(layout, second)["status"] == "PENDING_COMPARISON"
    item = manual.resolve_pending(layout, PROJECT)["results"][0]
    assert item["status"] == "NEW_GENERATION_STARTED"
    record = ws.load_waterline(layout)["cases"][PROJECT]
    assert record["upload"]["status"] == "VERIFIED" and record["archive"]["generation"] == "legacy"
    assert legacy.is_file()
