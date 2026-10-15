from __future__ import annotations

import hashlib
import io
import json
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
    verification = {"status": "VERIFIED", "caseId": "case", "filesVerified": 1}
    with pytest.raises(ws.WorkspaceStateError, match="来源材料"):
        ws.archive_verified_case(
            layout,
            PROJECT,
            upload_status="VERIFIED",
            verification=verification,
            manifest_sha256="sha256:" + "a" * 64,
            package_sha256="sha256:" + "b" * 64,
        )
    manual.acknowledge_gaps(layout, PROJECT, "User directs available-material closeout")
    result = ws.archive_verified_case(
        layout,
        PROJECT,
        upload_status="VERIFIED",
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
        upload_status="VERIFIED",
        verification={"status": "VERIFIED", "caseId": "case", "filesVerified": 1},
        manifest_sha256="sha256:" + "a" * 64,
        package_sha256=review["packageSha256"],
    )
    replay = accept_pair(layout, review)
    assert replay["status"] == "REUSED"
    assert Path(replay["evidencePath"]).parent == Path(result["archivedOriginal"])
    assert not Path(original["evidencePath"]).exists()
    source = ws.load_waterline(layout)["cases"][PROJECT]["source"]
    assert (layout.root / source["packageRelativePath"]).is_file()
