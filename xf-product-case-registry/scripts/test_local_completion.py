"""Real CLI lifecycle for the local-only completion path: no server, no NAS, no credentials."""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

import scripts.ledger_views as views
import scripts.manual_intake as manual
import scripts.registry_cli as cli
import scripts.source_intake as source_intake
import scripts.workspace_state as ws

PROJECT = "32002207C202600033"


def make_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        writer.write(stream)


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "DIR"
        for p in sorted(root.rglob("*"))
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the local lifecycle must not open any network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


class Case:
    """An isolated workspace holding one ingested ZIP, ready for the real CLI."""

    def __init__(self, tmp_path: Path) -> None:
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        self.config = tmp_path / "workspace.toml"
        cfg = ws.configure_workspace(
            work_root=tmp_path / "business", download_dir=downloads, config_path=self.config
        )
        self.layout = ws.ensure_workspace_layout(ws.BusinessLayout.from_root(cfg.work_root))
        self.pending = self.layout.pending_case_dir(PROJECT)
        self.work = self.layout.work_case_dir(PROJECT)
        self.pending.mkdir(parents=True)
        staging = tmp_path / "zip-src"
        make_pdf(staging / "通知书.pdf", pages=2)
        make_pdf(staging / "检验报告.pdf", pages=1)
        self.zip = self.pending / f"{PROJECT}_案卷包_fixture.zip"
        with zipfile.ZipFile(self.zip, "w") as archive:
            for item in sorted(staging.iterdir()):
                archive.write(item, item.name)
        self.zip_sha = "sha256:" + hashlib.sha256(self.zip.read_bytes()).hexdigest()
        ws.upsert_case(
            self.layout,
            PROJECT,
            unitName="合成单位",
            state="PENDING_ORGANIZATION",
            local={"status": "PENDING_ORGANIZATION"},
            source={
                "status": "PACKAGE_READY",
                "projectIdentitySource": "MANUAL_DETAIL",
                "packageSha256": self.zip_sha,
                "packageRelativePath": self.zip.relative_to(self.layout.root).as_posix(),
            },
        )

    def run(self, *argv: str) -> int:
        return cli.main([*argv, "--workspace-config", str(self.config)])

    def organize(self, zip_path: Path | None = None, extra: bool = False) -> None:
        zip_path = zip_path or self.zip
        assert self.run("inventory", str(zip_path), "--work-dir", str(self.work)) == 0
        data = {
            "schemaVersion": "CaseImportManifestV2",
            "createdAt": "2026-11-03T00:00:00Z",
            "extractor": {"name": "fixture", "version": "1"},
            "case": {
                "projectNo": PROJECT,
                "brigadeCode": "XISHAN",
                "unitName": "合成单位",
                "inspectionForm": "ROUTINE",
                "notificationTarget": "UNKNOWN",
            },
            "initialInspection": {
                "clientRef": "initial",
                "stage": "INITIAL_CHECK",
                "inspectionDate": "2026-10-01",
                "products": [],
            },
            "files": [
                {
                    "clientRef": "file:notice",
                    "sourceRelativePath": "通知书.pdf",
                    "relativePath": "normalized/notice.pdf",
                },
                {
                    "clientRef": "file:report",
                    "sourceRelativePath": "检验报告.pdf",
                    "relativePath": "normalized/report.pdf",
                },
            ],
            "documentSlots": [],
            "otherAttachments": [
                {
                    "clientRef": "attachment:notice",
                    "slotCode": "OTHER_ATTACHMENT",
                    "title": "通知书",
                    "fileRef": "file:notice",
                },
                {
                    "clientRef": "attachment:report",
                    "slotCode": "OTHER_ATTACHMENT",
                    "title": "检验报告",
                    "fileRef": "file:report",
                },
            ],
        }
        if extra:
            data["files"].append(
                {
                    "clientRef": "file:added",
                    "sourceRelativePath": "追加材料.pdf",
                    "relativePath": "normalized/added.pdf",
                }
            )
            data["otherAttachments"].append(
                {
                    "clientRef": "attachment:added",
                    "slotCode": "OTHER_ATTACHMENT",
                    "title": "追加材料",
                    "fileRef": "file:added",
                }
            )
        case_data = self.work / "case-data.json"
        case_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert self.run("compose", "--work-dir", str(self.work), "--case-data", str(case_data)) == 0

    def record(self) -> dict:
        return ws.load_waterline(self.layout)["cases"][PROJECT]


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return Case(tmp_path)


def test_new_case_composes_verifies_archives_and_ledger_recognizes_it(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    assert case.record()["local"]["status"] == "READY_FOR_LOCAL_VERIFY"
    capsys.readouterr()

    before = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "CHECK_ONLY" and checked["files"] == 2 and checked["wrote"] is False
    assert snapshot(case.layout.root) == before  # validation never mutates anything

    assert case.run("local-verify", "--project", PROJECT) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPLETED"
    record = case.record()
    assert record["state"] == "COMPLETED"
    assert record["completion"]["mode"] == "LOCAL_V1"
    assert record["upload"]["status"] == "NOT_STARTED"  # never claims a server upload
    assert record["nasVerification"]["status"] == "NOT_STARTED"
    assert not case.pending.exists() and not case.work.exists()
    originals = list(case.layout.completed_case_dir(PROJECT).rglob("*.zip"))
    assert len(originals) == 1
    assert "sha256:" + hashlib.sha256(originals[0].read_bytes()).hexdigest() == case.zip_sha
    receipt = json.loads(Path(result["archive"]["verificationRecord"]).read_text(encoding="utf-8"))
    assert receipt["completionMode"] == "LOCAL_V1" and "verification" not in receipt
    proof = receipt["localVerification"]
    assert proof["serverUploadClaimed"] is False and proof["nasVerificationClaimed"] is False
    assert proof["packageSha256"] == case.zip_sha
    assert set(proof["files"]) == {"file:notice", "file:report"}

    row = views.describe(case.layout, PROJECT, case.record())
    assert row["complete"] is True and row["activePending"] is False
    assert row["evidenceLevel"] == "LOCAL_VERIFIED" and row["completionMode"] == "LOCAL_V1"
    assert row["stages"]["localVerified"] and not row["stages"]["bodyVerified"]
    assert row["historicalComplete"] is False
    plan = views.scan_plan(case.layout)
    assert PROJECT not in {item["projectNo"] for item in plan["pending"]}
    assert source_intake._verified_completed_waterline(case.layout, PROJECT) is not None

    # Idempotent: rerunning neither rewrites the archive nor creates a second generation.
    after = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ALREADY_COMPLETED"
    assert snapshot(case.layout.root) == after


def test_no_archive_leaves_originals_and_a_reusable_proof(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--no-archive") == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "LOCAL_VERIFIED" and first["archive"] is None
    assert case.pending.is_dir() and case.work.is_dir()
    assert case.record()["state"] == "LOCAL_VERIFIED_PENDING_ARCHIVE"
    assert case.record().get("completion") is None
    proof_bytes = (case.work / "local-verification.json").read_bytes()
    assert case.run("local-verify", "--project", PROJECT, "--no-archive") == 0
    assert json.loads(capsys.readouterr().out)["proofWrite"] == "REUSED"
    assert (case.work / "local-verification.json").read_bytes() == proof_bytes


def test_missing_normalized_file_fails_and_preserves_everything(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    upload_map = json.loads((case.work / "upload-map.json").read_text(encoding="utf-8"))
    Path(upload_map["files"]["file:report"]).unlink()
    before = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert snapshot(case.layout.root) == before
    assert case.record()["state"] != "COMPLETED"
    assert "file:report" in capsys.readouterr().err


def test_same_name_file_with_different_digest_blocks_and_preserves_both(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    upload_map = json.loads((case.work / "upload-map.json").read_text(encoding="utf-8"))
    victim = Path(upload_map["files"]["file:notice"])
    original = victim.read_bytes()
    make_pdf(victim, pages=3)  # same name and path, different bytes and page count
    replaced = victim.read_bytes()
    before = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "file:notice" in capsys.readouterr().err
    assert snapshot(case.layout.root) == before and victim.read_bytes() == replaced != original
    assert case.record()["state"] != "COMPLETED"


def test_original_package_change_after_inventory_blocks_completion(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    case.zip.write_bytes(case.zip.read_bytes() + b"tampered")
    before = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "原始材料自清点后已变化" in capsys.readouterr().err
    assert snapshot(case.layout.root) == before


def test_ledger_source_digest_mismatch_blocks_completion(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    ws.upsert_case(case.layout, PROJECT, source={"packageSha256": "sha256:" + "9" * 64})
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "来源包摘要" in capsys.readouterr().err
    assert case.record()["state"] != "COMPLETED"


def test_unresolved_field_resolution_blocks_completion(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    (case.work / "field-resolution.json").write_text(
        json.dumps({"projectNo": PROJECT, "unresolvedFields": ["case.unitName"]}),
        encoding="utf-8",
    )
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "未解决" in capsys.readouterr().err
    assert case.work.is_dir() and case.pending.is_dir()


def test_source_gap_needs_user_ack_and_change_pending_blocks(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.manual_intake as manual

    case.organize()
    capsys.readouterr()
    ws.upsert_case(case.layout, PROJECT, source={"materialGaps": [{"document": "缺失通知书"}]})
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "缺口" in capsys.readouterr().err
    manual.acknowledge_gaps(case.layout, PROJECT, "用户口径：按现有材料收口")
    ws.upsert_case(case.layout, PROJECT, source={"changePending": True})
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "来源变化" in capsys.readouterr().err
    ws.upsert_case(case.layout, PROJECT, source={"changePending": False})
    assert case.run("local-verify", "--project", PROJECT) == 0
    receipt = json.loads(Path(json.loads(capsys.readouterr().out)["archive"]["verificationRecord"])
                         .read_text(encoding="utf-8"))
    assert receipt["sourceIssuesRetained"] is True  # missing documents stay recorded
    row = views.describe(case.layout, PROJECT, case.record())
    assert row["stages"]["materialsCollected"] is False and row["complete"] is False
    assert row["availableMaterialWorkflowComplete"] is True


def test_interrupted_archive_rolls_back_and_a_retry_completes(
    case: Case, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    real = ws._save_waterline_unlocked
    calls = {"count": 0}

    def explode(layout: object, value: object) -> object:
        calls["count"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(ws, "_save_waterline_unlocked", explode)
    with pytest.raises(OSError, match="disk full"):
        case.run("local-verify", "--project", PROJECT)
    monkeypatch.setattr(ws, "_save_waterline_unlocked", real)
    assert case.pending.is_dir() and case.work.is_dir()
    assert not list(case.layout.verification_records.glob(f"{PROJECT}-*.json"))
    assert not list(case.layout.history_workspaces.glob(f"{PROJECT}-*"))
    assert case.record()["state"] != "COMPLETED"
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETED"


def test_existing_archive_target_conflict_is_refused_without_overwrite(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--no-archive") == 0
    proof = json.loads((case.work / "local-verification.json").read_text(encoding="utf-8"))
    stamp = ws._timestamp_slug(proof["verifiedAt"])
    blocker = case.layout.history_workspaces / f"{PROJECT}-{stamp}-{proof['packageSha256'][7:19]}"
    blocker.mkdir(parents=True)
    (blocker / "keep.txt").write_text("user data", encoding="utf-8")
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert (blocker / "keep.txt").read_text(encoding="utf-8") == "user data"
    assert case.pending.is_dir() and case.work.is_dir()
    assert case.record()["state"] != "COMPLETED"


def test_tampered_receipt_or_changed_source_makes_completion_untrusted(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    result = json.loads(capsys.readouterr().out)
    assert views.describe(case.layout, PROJECT, case.record())["complete"] is True

    # New source documents on a completed case keep it pending (never silently skipped).
    ws.upsert_case(case.layout, PROJECT, source={"changePending": True})
    changed = views.describe(case.layout, PROJECT, case.record())
    assert changed["complete"] is False and changed["activePending"] is True
    ws.upsert_case(case.layout, PROJECT, source={"changePending": False})
    assert views.describe(case.layout, PROJECT, case.record())["complete"] is True

    receipt_path = Path(result["archive"]["verificationRecord"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["localVerification"]["files"]["file:report"]["sha256"] = "sha256:" + "0" * 64
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    row = views.describe(case.layout, PROJECT, case.record())
    assert row["complete"] is False and "LOCAL_COMPLETION_UNTRUSTED" in row["issues"]
    assert row["activePending"] is True


def _completed(case: Case, capsys: pytest.CaptureFixture[str]) -> dict:
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    return json.loads(capsys.readouterr().out)


def _assert_needs_review(case: Case, capsys: pytest.CaptureFixture[str], reason: str) -> None:
    capsys.readouterr()
    row = views.describe(case.layout, PROJECT, case.record())
    assert row["complete"] is False and row["activePending"] is True
    assert "LOCAL_COMPLETION_UNTRUSTED" in row["issues"]
    assert any(reason in item for item in row["issues"]), row["issues"]
    assert source_intake._verified_completed_waterline(case.layout, PROJECT) is None
    before = snapshot(case.layout.root)
    assert case.run("local-verify", "--project", PROJECT) == 2  # explicit, nothing rewritten
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "NEEDS_REVIEW" and any(reason in r for r in out["reasons"])
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    capsys.readouterr()
    assert snapshot(case.layout.root) == before  # evidence is kept untouched
    assert case.record()["completion"]["status"] == "LOCAL_VERIFIED"  # history kept


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_corrupt_or_missing_archived_pdf_is_not_completed(
    case: Case, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    result = _completed(case, capsys)
    work = Path(result["archive"]["archivedWorkspace"])
    proof = json.loads((work / "local-verification.json").read_text(encoding="utf-8"))
    target = work / sorted(proof["files"].values(), key=lambda f: f["workRelativePath"])[0][
        "workRelativePath"
    ]
    if damage == "corrupt":
        target.write_bytes(target.read_bytes() + b"x")
    else:
        target.unlink()
    _assert_needs_review(case, capsys, "归档 PDF")


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_corrupt_or_missing_archived_zip_is_not_completed(
    case: Case, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    result = _completed(case, capsys)
    target = next(Path(result["archive"]["archivedOriginal"]).rglob("*.zip"))
    if damage == "corrupt":
        target.write_bytes(target.read_bytes() + b"x")
    else:
        target.unlink()
    _assert_needs_review(case, capsys, "归档原始 ZIP")


@pytest.mark.parametrize("name", ["manifest.json", "local-verification.json"])
def test_changed_archived_manifest_or_proof_is_not_completed(
    case: Case, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    result = _completed(case, capsys)
    target = Path(result["archive"]["archivedWorkspace"]) / name
    if name == "manifest.json":
        target.write_text(target.read_text(encoding="utf-8") + "\n ", encoding="utf-8")
    else:
        data = json.loads(target.read_text(encoding="utf-8"))
        data["verifiedAt"] = "2000-01-01T00:00:00Z"
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _assert_needs_review(case, capsys, "归档")


def test_coordinated_member_omission_in_proof_and_receipt_is_not_completed(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dropping one manifest member from proof and receipt and deleting its PDF must not pass."""
    result = _completed(case, capsys)
    work = Path(result["archive"]["archivedWorkspace"])
    receipt_path = Path(result["archive"]["verificationRecord"])
    proof_path = work / "local-verification.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    victim = "file:report"
    (work / proof["files"][victim]["workRelativePath"]).unlink()
    del proof["files"][victim]
    proof_path.write_text(json.dumps(proof, ensure_ascii=False), encoding="utf-8")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["localVerification"]["files"][victim]
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    _assert_needs_review(case, capsys, "文件集合与清单不一致")


def test_completion_file_count_must_match_the_bound_manifest(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    _completed(case, capsys)
    ws.upsert_case(case.layout, PROJECT, completion={"fileCount": 1})
    _assert_needs_review(case, capsys, "fileCount")


def test_change_pending_blocks_already_completed_and_source_skip(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    _completed(case, capsys)
    ws.upsert_case(case.layout, PROJECT, source={"changePending": True})
    _assert_needs_review(case, capsys, "changePending")


def _png(path: Path) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2200, 1100, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * 6600) * 1100))
        + chunk(b"IEND", b"")
    )


def _manual_review(case: Case, tmp_path: Path, name: str, extra: bool) -> dict:
    package = tmp_path / f"{name}.zip"
    first_zip = next(case.layout.completed_case_dir(PROJECT).rglob("*.zip"))
    with zipfile.ZipFile(first_zip) as old, zipfile.ZipFile(package, "w") as out:
        for info in old.infolist():
            out.writestr(info.filename, old.read(info))
        if extra:
            make_pdf(tmp_path / "追加材料.pdf")
            out.write(tmp_path / "追加材料.pdf", "追加材料.pdf")
    shot = tmp_path / f"{name}.png"
    _png(shot)
    entry = "通知书.pdf"
    with zipfile.ZipFile(package) as z:
        entry_sha = "sha256:" + hashlib.sha256(z.read(entry)).hexdigest()
    return {
        "schemaVersion": "ManualCaseIntakeV1",
        "projectNo": PROJECT,
        "fields": {"projectNo": PROJECT, "unitName": "合成单位", "brigadeCode": "XISHAN"},
        "packagePath": str(package),
        "packageSha256": "sha256:" + hashlib.sha256(package.read_bytes()).hexdigest(),
        "screenshotPath": str(shot),
        "screenshotSha256": "sha256:" + hashlib.sha256(shot.read_bytes()).hexdigest(),
        "screenshotReview": {
            "readable": True,
            "projectNo": PROJECT,
            "unitName": "合成单位",
            "brigadeCode": "XISHAN",
            "dimensions": [2200, 1100],
            "readableFields": [
                "projectNo", "unitName", "brigade", "productResults", "documentDirectory",
            ],
        },
        "documentMatch": {
            "entryName": entry, "unitName": "合成单位", "documentNo": "N1", "sha256": entry_sha,
        },
    }


def test_manual_additions_resolve_and_complete_a_new_generation_end_to_end(
    case: Case, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _completed(case, capsys)
    old_original = Path(first["archive"]["archivedOriginal"])
    old_tree = snapshot(old_original)
    old_workspace_tree = snapshot(Path(first["archive"]["archivedWorkspace"]))

    def accept(review: dict) -> str:
        with patch("pypdf._page.PageObject.extract_text", return_value="合成单位 N1"):
            return manual.accept(case.layout, review)["status"]

    # 1) identical material: only a resolution mark, nothing is reprocessed.
    assert accept(_manual_review(case, tmp_path, "same", extra=False)) == "PENDING_COMPARISON"
    assert case.run("source", "resolve-manual", "--project", PROJECT) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["status"] for r in out["results"]] == ["RESOLVED_IDENTICAL"]
    assert case.record()["state"] == "COMPLETED" and not case.pending.exists()
    assert views.describe(case.layout, PROJECT, case.record())["complete"] is True

    # 2) additional material: a resumable second generation completes via the normal path.
    assert accept(_manual_review(case, tmp_path, "more", extra=True)) == "PENDING_COMPARISON"
    assert case.run("source", "resolve-manual", "--project", PROJECT) == 0
    out = json.loads(capsys.readouterr().out)
    started = [r for r in out["results"] if r["status"] == "NEW_GENERATION_STARTED"]
    assert len(started) == 1 and started[0]["diff"]["added"] == ["追加材料.pdf"]
    assert case.record()["state"] == "PENDING_ORGANIZATION"
    assert views.describe(case.layout, PROJECT, case.record())["complete"] is False
    new_zip = Path(started[0]["pendingPackage"])
    case.organize(zip_path=new_zip, extra=True)
    assert case.run("local-verify", "--project", PROJECT) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "COMPLETED" and second["files"] == 3
    assert second["archive"]["archivedOriginal"] != first["archive"]["archivedOriginal"]
    assert snapshot(old_original) == old_tree  # earlier generation is untouched
    assert snapshot(Path(first["archive"]["archivedWorkspace"])) == old_workspace_tree
    assert len(case.record()["history"]["completions"]) == 2
    assert views.describe(case.layout, PROJECT, case.record())["complete"] is True

    # 3) the resolve entry closes the loop idempotently.
    assert case.run("source", "resolve-manual", "--project", PROJECT) == 0
    statuses = {r["status"] for r in json.loads(capsys.readouterr().out)["results"]}
    assert statuses == {"REUSED", "RESOLVED_COMPLETED"}
    before = snapshot(case.layout.root)
    assert case.run("source", "resolve-manual", "--project", PROJECT) == 0
    assert {r["status"] for r in json.loads(capsys.readouterr().out)["results"]} == {"REUSED"}
    assert snapshot(case.layout.root) == before


def test_historical_server_completion_stays_distinguishable(case: Case) -> None:
    other = "32002207C202600034"
    ws.upsert_case(
        case.layout,
        other,
        state="COMPLETED",
        upload={"status": "VERIFIED"},
        nasVerification={"status": "VERIFIED", "caseId": "legacy-case"},
    )
    assert source_intake._verified_completed_waterline(case.layout, other) is not None
    row = views.describe(case.layout, other, ws.load_waterline(case.layout)["cases"][other])
    assert row["completionMode"] != "LOCAL_V1" and row["evidenceLevel"] != "LOCAL_VERIFIED"
    assert row["stages"]["localVerified"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["upload", "--manifest", "m.json", "--upload-map", "u.json", "--api-base", "https://x"],
        ["upload-batch", "--project", PROJECT, "--api-base", "https://x", "--finalize"],
        ["verify", "--manifest", "m.json", "--upload-map", "u.json", "--api-base", "https://x"],
        ["verify-existing", "--manifest", "m.json", "--api-base", "https://x"],
        ["supplement", "--manifest", "m.json", "--plan"],
        ["supplement-batch", "--project", PROJECT],
        ["init-auth-config"],
        ["ledger", "reconcile", "--api-base", "https://x", "--apply"],
    ],
)
def test_retired_commands_fail_before_any_side_effect(
    case: Case, argv: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    case.organize()
    capsys.readouterr()
    before = snapshot(case.layout.root)
    home_before = snapshot(tmp_path)
    assert cli.main([*argv, "--work-root", str(case.layout.root)]) == 2
    err = capsys.readouterr().err
    assert "已退役" in err and "local-verify" in err
    assert snapshot(case.layout.root) == before  # no ledger, timing or workspace write
    assert snapshot(tmp_path) == home_before  # no auth config or any other file created


def _subset_fixture(case: Case, tmp_path: Path, *, inventory: bool = True, name: str = "notice.pdf") -> tuple[Path, dict]:
    """inventory 后手工写一个 split-index 条目（复制来源PDF为 normalized 件），并生成确认清单与依据文件。"""
    if inventory:
        assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    inventory = json.loads((case.work / "inventory.json").read_text(encoding="utf-8"))
    origin = next(f for f in inventory["files"] if f["relativePath"] == "通知书.pdf")
    target = case.work / "normalized" / name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(Path(origin["absolutePath"]).read_bytes())
    sha, pages = cli.pdf_info(target)
    item = {
        "sourceRelativePath": "通知书.pdf", "pageStart": 1, "pageEnd": 2, "relativePath": f"normalized/{name}",
        "docTypeCandidate": "责令限期改正通知书", "typeEvidenceLevel": "L2", "localRef": f"LREF2|{PROJECT}|x#p1-2#s1",
        "sourceSha256": origin["sha256"], "absolutePath": str(target), "sha256": sha, "pageCount": pages,
    }
    (case.work / "split-index.json").write_text(
        json.dumps({"splitIndexVersion": 2, "generatedAt": "2026-11-07T00:00:00Z", "items": [item]}, ensure_ascii=False),
        encoding="utf-8")
    basis = tmp_path / "basis.json"
    basis.write_text('{"reviewed": true}', encoding="utf-8")
    confirmation = {
        "projectNo": PROJECT, "held": [{"seq": 9, "docType": "空白页候选"}],
        "items": [{"relativePath": f"normalized/{name}", "sha256": sha, "level": "REVIEWED_PAGE_BOUNDS",
                   "basis": [{"kind": "review", "path": str(basis), "sha256": hashlib.sha256(basis.read_bytes()).hexdigest()}]}],
    }
    path = tmp_path / "confirmation.json"
    path.write_text(json.dumps(confirmation, ensure_ascii=False), encoding="utf-8")
    return path, confirmation


def test_confirmed_documents_and_copy_only_subset_leave_originals_and_case_state_alone(
    case: Case, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    confirmation_path, confirmation = _subset_fixture(case, tmp_path)
    capsys.readouterr()
    before_state = case.record()["state"]
    pending_before, work_before = snapshot(case.pending), snapshot(case.work)
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(confirmation_path), "--check-only") == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "CHECK_ONLY" and checked["wrote"] is False and checked["items"] == 1
    assert snapshot(case.work) == work_before
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(confirmation_path)) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["write"] == "CREATED" and created["caseLevelVerified"] is False
    document = json.loads((case.work / "confirmed-documents.json").read_text(encoding="utf-8"))
    assert document["held"] and document["unconfirmedSplitItems"] == [] and document["caseLevelVerified"] is False
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(confirmation_path)) == 0
    assert json.loads(capsys.readouterr().out)["write"] == "REUSED"

    work_after_confirm = snapshot(case.work)
    assert case.run("archive-document-subset", "--project", PROJECT, "--check-only") == 0
    assert json.loads(capsys.readouterr().out)["status"] == "WOULD_CREATE"
    assert not case.layout.completed_case_dir(PROJECT).exists()
    assert case.run("archive-document-subset", "--project", PROJECT) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CREATED" and result["pointer"] == "ADDED"
    leaf = Path(result["target"])
    assert leaf.name == document["contentListSha256"] and (leaf / "normalized" / "notice.pdf").is_file()
    assert snapshot(case.pending) == pending_before                                  # 原ZIP/待处理原位原字节
    assert snapshot(case.work) == work_after_confirm                                  # 活动工作区原位原字节
    record = case.record()
    assert record["state"] == before_state and "completions" not in (record.get("history") or {})
    assert record["documentSubsets"][0]["caseLevelVerified"] is False
    assert not views.describe(case.layout, PROJECT, record)["complete"]               # 不冒全案完成
    leaf_snapshot = snapshot(leaf)
    assert case.run("archive-document-subset", "--project", PROJECT) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["status"] == "REUSED" and again["pointer"] == "REUSED" and snapshot(leaf) == leaf_snapshot
    (leaf / "normalized" / "notice.pdf").write_bytes(b"tampered")                      # 同叶内容不同：拒绝覆盖
    assert case.run("archive-document-subset", "--project", PROJECT) != 0
    capsys.readouterr()


def test_confirmation_with_wrong_digest_or_unknown_item_is_rejected(
    case: Case, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    confirmation_path, confirmation = _subset_fixture(case, tmp_path)
    capsys.readouterr()
    bad = json.loads(json.dumps(confirmation))
    bad["items"][0]["sha256"] = "sha256:" + "0" * 64
    other = tmp_path / "bad.json"
    other.write_text(json.dumps(bad), encoding="utf-8")
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(other), "--check-only") != 0
    bad = json.loads(json.dumps(confirmation))
    bad["items"][0]["relativePath"] = "normalized/missing.pdf"
    other.write_text(json.dumps(bad), encoding="utf-8")
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(other), "--check-only") != 0
    assert not (case.work / "confirmed-documents.json").exists()
    capsys.readouterr()


def test_full_case_archive_still_works_after_a_document_subset_leaf_exists(
    case: Case, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    confirmation_path, _ = _subset_fixture(case, tmp_path, inventory=False, name="subset_notice.pdf")
    assert case.run("confirm-documents", "--project", PROJECT, "--confirmation", str(confirmation_path)) == 0
    assert case.run("archive-document-subset", "--project", PROJECT) == 0
    leaf_parent = case.layout.completed_case_dir(PROJECT) / ws.DOCUMENT_SUBSET_DIR
    subset_snapshot = snapshot(leaf_parent)
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0           # 保留目录不被当成“旧版平铺内容”
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETED"
    assert snapshot(leaf_parent) == subset_snapshot                      # 子集叶原位原字节
    assert case.record()["documentSubsets"]                              # 子集指针保留
