"""S2 regression: same-ZIP amendment, real external supplements, image normalization,
local water brigade and replacement source evidence, via the real CLI in an isolated root."""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import zlib
from pathlib import Path

import pytest

import scripts.image_pdf as image_pdf
import scripts.registry_cli as cli
import scripts.workspace_state as ws
from scripts.test_local_completion import PROJECT, Case, snapshot

RWID = "32100000001"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("no network in the local lifecycle")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return Case(tmp_path)


def out(capsys: pytest.CaptureFixture[str]) -> dict:
    text = capsys.readouterr().out.strip().splitlines()
    return json.loads(text[-1]) if text else {}


def complete(case: Case, capsys: pytest.CaptureFixture[str]) -> dict:
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    return out(capsys)


def tree(path: Path) -> dict[str, str]:
    return snapshot(path)


def not_complete(case: Case) -> bool:
    record = case.record()
    return (
        record["state"] != "COMPLETED"
        and record["completion"]["status"] == "SUPERSEDED_BY_AMENDMENT"
    )


def parent_trees(case: Case, result: dict) -> tuple[dict, dict, str]:
    archive = result["archive"]
    receipt = Path(archive["verificationRecord"])
    return (
        tree(Path(archive["archivedOriginal"])),
        tree(Path(archive["archivedWorkspace"])),
        hashlib.sha256(receipt.read_bytes()).hexdigest(),
    )


def amended_case_data(case: Case, **changes: object) -> Path:
    """Case data for the amendment: same ZIP, a corrected field (inspection form)."""
    seed = json.loads((case.work / "amendment-seed" / "manifest.json").read_text(encoding="utf-8"))
    data = {k: v for k, v in seed.items() if k not in {"packageSha256", "createdAt"}}
    data["files"] = [
        {
            "clientRef": f["clientRef"],
            "relativePath": f["relativePath"],
            "sourceRelativePath": {"file:notice": "通知书.pdf", "file:report": "检验报告.pdf"}[
                f["clientRef"]
            ],
        }
        for f in seed["files"]
    ]
    data["case"]["inspectionForm"] = changes.get("inspectionForm", "SPECIAL")
    if "edit" in changes:
        changes["edit"](data)  # type: ignore[operator]
    path = case.work / "case-data.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def open_amendment(
    case: Case, capsys: pytest.CaptureFixture[str], reason: str = "修正检查形式"
) -> dict:
    assert case.run("source", "amend-archived", "--project", PROJECT, "--reason", reason) == 0
    return out(capsys)


def organize_amendment(
    case: Case,
    capsys: pytest.CaptureFixture[str],
    opened: dict,
    resolution: bool = True,
    **changes: object,
) -> None:
    zip_path = next(case.pending.glob("*.zip"))
    assert case.run("inventory", str(zip_path), "--work-dir", str(case.work)) == 0
    data = amended_case_data(case, **changes)
    if resolution:
        (case.work / "field-resolution.json").write_text(
            json.dumps(
                {"projectNo": PROJECT, "amendmentId": opened["amendmentId"], "unresolvedFields": []}
            ),
            encoding="utf-8",
        )
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data)) == 0
    capsys.readouterr()


# ------------------------------------------------------------------ amendment


def test_same_zip_amendment_keeps_parent_bytes_and_advances_only_after_success(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    result = complete(case, capsys)
    parent_gen = Path(result["archive"]["archivedOriginal"]).name
    before = parent_trees(case, result)
    waterline_before = case.layout.waterline_json.read_bytes()

    opened = open_amendment(case, capsys)
    assert opened["status"] == "OPENED" and opened["parentGeneration"] == parent_gen
    record = case.record()
    assert record["state"] == "PENDING_ORGANIZATION" and record["local"]["status"] == "AMENDING"
    assert (
        record["completion"]["status"] == "SUPERSEDED_BY_AMENDMENT"
    )  # old finish is not a live finish
    assert record["amendment"]["parent"]["completion"]["status"] == "LOCAL_VERIFIED"
    assert parent_trees(case, result) == before  # parent files untouched byte for byte

    # re-running the identical request is answered, not repeated; a different reason is refused
    assert (
        case.run("source", "amend-archived", "--project", PROJECT, "--reason", "修正检查形式") == 0
    )
    assert out(capsys)["status"] == "REUSED"
    assert case.run("source", "amend-archived", "--project", PROJECT, "--reason", "另一原因") == 2
    capsys.readouterr()

    # incomplete work cannot be archived: no field resolution -> refused, nothing advances
    organize_amendment(case, capsys, opened, resolution=False)
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    assert "field-resolution" in capsys.readouterr().err
    assert parent_trees(case, result) == before and not_complete(case)

    (case.work / "field-resolution.json").write_text(
        json.dumps(
            {"projectNo": PROJECT, "amendmentId": opened["amendmentId"], "unresolvedFields": []}
        ),
        encoding="utf-8",
    )
    assert case.run("local-verify", "--project", PROJECT) == 0
    done = out(capsys)
    assert done["status"] == "COMPLETED"
    record = case.record()
    assert record["state"] == "COMPLETED" and record["completion"]["status"] == "LOCAL_VERIFIED"
    generations = [e["generation"] for e in record["history"]["completions"]]
    assert len(generations) == 2 and generations[0] == parent_gen
    assert record["history"]["completions"][1]["amendment"]["parentGeneration"] == parent_gen
    assert record["amendment"]["status"] == "COMPLETED"
    assert parent_trees(case, result) == before  # still identical after the new generation
    assert case.layout.waterline_json.read_bytes() != waterline_before  # waterline advanced once
    receipt = json.loads(Path(done["archive"]["verificationRecord"]).read_text(encoding="utf-8"))
    changes = receipt["localVerification"]["amendment"]["changes"]
    assert "case" in changes["changed"]
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    assert (
        out(capsys)["status"] == "ALREADY_COMPLETED"
    )  # deep verification of the amended generation


def test_amendment_refuses_unchanged_manifest_changed_parent_and_other_zip(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    result = complete(case, capsys)
    opened = open_amendment(case, capsys)
    organize_amendment(case, capsys, opened, inspectionForm="ROUTINE")  # identical to the parent
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "没有实质差异" in capsys.readouterr().err
    assert not_complete(case)

    # a real change composes fine; then a concurrent change of the parent generation is refused
    changed = amended_case_data(case, inspectionForm="SPECIAL")
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(changed)) == 0
    capsys.readouterr()
    parent_manifest = Path(result["archive"]["archivedWorkspace"]) / "manifest.json"
    parent_manifest.write_bytes(parent_manifest.read_bytes() + b" ")
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    assert "父代" in capsys.readouterr().err
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "父代" in capsys.readouterr().err
    record = case.record()
    assert not_complete(case) and len(record["history"]["completions"]) == 1  # nothing was archived
    assert (
        case.run("source", "abandon-amendment", "--project", PROJECT, "--reason", "x") == 2
    )  # parent changed
    capsys.readouterr()


def set_date(data: dict) -> None:
    data["initialInspection"]["inspectionDate"] = "2026-10-02"


def set_unit(data: dict) -> None:
    data["case"]["unitName"] = "合成单位乙"


def set_title(data: dict) -> None:
    data["otherAttachments"][0]["title"] = "通知书（更正）"


def test_amendment_detects_inspection_date_alone_and_keeps_parent(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    result = complete(case, capsys)
    before = parent_trees(case, result)
    opened = open_amendment(case, capsys)
    organize_amendment(case, capsys, opened, inspectionForm="ROUTINE", edit=set_date)
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert out(capsys)["status"] == "COMPLETED"
    assert len(case.record()["history"]["completions"]) == 2
    assert parent_trees(case, result) == before


@pytest.mark.parametrize("edit", [set_unit, set_title], ids=["case-field", "attachment"])
def test_amendment_detects_other_field_changes(
    case: Case, capsys: pytest.CaptureFixture[str], edit: object
) -> None:
    complete(case, capsys)
    opened = open_amendment(case, capsys)
    organize_amendment(case, capsys, opened, inspectionForm="ROUTINE", edit=edit)
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert out(capsys)["status"] == "COMPLETED"


def test_amendment_unchanged_rejected_then_retry_with_real_change(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    result = complete(case, capsys)
    before = parent_trees(case, result)
    opened = open_amendment(case, capsys)
    organize_amendment(case, capsys, opened, inspectionForm="ROUTINE")
    assert case.run("local-verify", "--project", PROJECT) == 2
    assert "没有实质差异" in capsys.readouterr().err
    changed = amended_case_data(case, inspectionForm="ROUTINE", edit=set_date)
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(changed)) == 0
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert parent_trees(case, result) == before


def test_abandon_restores_parent_completion_and_keeps_copies(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    result = complete(case, capsys)
    before = parent_trees(case, result)
    open_amendment(case, capsys)
    assert (
        case.run("source", "abandon-amendment", "--project", PROJECT, "--reason", "不再需要") == 0
    )
    done = out(capsys)
    assert done["status"] == "ABANDONED"
    assert Path(done["retainedPending"]).is_dir() and Path(done["retainedWorkspace"]).is_dir()
    record = case.record()
    assert record["state"] == "COMPLETED" and record["completion"]["status"] == "LOCAL_VERIFIED"
    assert not case.pending.exists() and not case.work.exists()
    assert parent_trees(case, result) == before
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    assert out(capsys)["status"] == "ALREADY_COMPLETED"
    assert case.run("source", "abandon-amendment", "--project", PROJECT, "--reason", "x") == 0
    assert out(capsys)["status"] == "REUSED"  # a repeated abandon is answered, not repeated
    assert (
        case.run("source", "abandon-amendment", "--project", "32002207C202600034", "--reason", "x")
        == 2
    )
    capsys.readouterr()


def test_amend_requires_a_trustworthy_completed_parent_and_free_folders(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    case.organize()
    capsys.readouterr()
    assert (
        case.run("source", "amend-archived", "--project", PROJECT, "--reason", "x") == 2
    )  # not archived
    capsys.readouterr()
    result = complete_after_organized(case, capsys)
    next(Path(result["archive"]["archivedOriginal"]).glob("*.zip")).write_bytes(b"damaged")
    assert case.run("source", "amend-archived", "--project", PROJECT, "--reason", "x") == 2
    assert "不可信" in capsys.readouterr().err
    assert not case.pending.exists() and not case.work.exists()  # nothing left behind


def complete_after_organized(case: Case, capsys: pytest.CaptureFixture[str]) -> dict:
    assert case.run("local-verify", "--project", PROJECT) == 0
    return out(capsys)


# ---------------------------------------------------------------- supplements


def supplement_files(
    tmp_path: Path,
    case: Case,
    *,
    project: str = PROJECT,
    rwid: str = RWID,
    tamper: bool = False,
    name: str = "现场照片.pdf",
) -> tuple[Path, Path]:
    from scripts.test_local_completion import make_pdf

    shot = case.pending / "案卷详情_fixture.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfixture-screenshot")
    shot_sha = "sha256:" + hashlib.sha256(shot.read_bytes()).hexdigest()
    folder = tmp_path / "supplement-src"
    file = folder / name
    make_pdf(file, pages=3)
    sha = "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()
    receipt = folder / (file.stem + ".receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": "SourceDocumentRecoveryV1",
                "projectNo": project,
                "rwid": rwid,
                "sourceDocumentId": "32106369139",
                "sourceDocumentTitle": "现场照片",
                "detailScreenshot": {"sha256": shot_sha},
                "download": {"bytes": file.stat().st_size},
                "sha256": sha,
                "pageCount": 3,
                "reason": "recovered from its own visible preview",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if tamper:
        file.write_bytes(file.read_bytes() + b"\n%tampered")
    return file, receipt


def prepare_rwid(case: Case) -> None:
    ws.upsert_case(
        case.layout, PROJECT, source={"annualIdentitiesByRwid": {RWID: {"sourceYear": 2026}}}
    )


def attach(case: Case, file: Path, receipt: Path) -> int:
    return case.run(
        "source",
        "attach-supplement",
        "--project",
        PROJECT,
        "--file",
        str(file),
        "--receipt",
        str(receipt),
    )


def test_supplement_binds_real_file_survives_archive_and_is_not_a_zip_member(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_rwid(case)
    file, receipt = supplement_files(tmp_path, case)
    assert attach(case, file, receipt) == 0
    attached = out(capsys)
    assert attached["status"] == "ATTACHED"
    assert attach(case, file, receipt) == 0 and out(capsys)["status"] == "REUSED"
    record = case.record()
    binding = next(iter(record["source"]["supplements"].values()))
    assert (
        binding["sourceType"] == "VISIBLE_SOURCE_PREVIEW"
        and binding["sourceKind"] == "EXTERNAL_SUPPLEMENT"
    )
    assert record["source"]["packageSha256"] == case.zip_sha  # the ZIP identity is unchanged

    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    plan = {
        "items": [
            {
                "sourceRelativePath": "通知书.pdf",
                "pageStart": 1,
                "pageEnd": 2,
                "relativePath": "normalized/notice.pdf",
            },
            {
                "sourceRelativePath": "检验报告.pdf",
                "pageStart": 1,
                "pageEnd": 1,
                "relativePath": "normalized/report.pdf",
            },
            {
                "sourceRelativePath": "补采材料/" + Path(binding["fileRelativePath"]).name,
                "pageStart": 1,
                "pageEnd": 3,
                "relativePath": "normalized/photo.pdf",
            },
        ]
    }
    plan_path = case.work / "split-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert case.run("split", "--work-dir", str(case.work), "--plan", str(plan_path)) == 0
    index = json.loads((case.work / "split-index.json").read_text(encoding="utf-8"))
    kinds = {i["relativePath"]: i["sourceKinds"] for i in index["items"]}
    assert kinds["normalized/photo.pdf"] == ["EXTERNAL_SUPPLEMENT"] and kinds[
        "normalized/notice.pdf"
    ] == ["ZIP_MEMBER"]
    data = {
        "schemaVersion": "CaseImportManifestV2",
        "createdAt": "2026-11-03T00:00:00Z",
        "extractor": {"name": "fixture", "version": "1"},
        "case": {"projectNo": PROJECT, "brigadeCode": "XISHAN", "unitName": "合成单位"},
        "initialInspection": {
            "clientRef": "initial",
            "stage": "INITIAL_CHECK",
            "inspectionDate": "2026-10-01",
            "products": [],
        },
        "files": [
            {
                "clientRef": "file:notice",
                "sourceRelativePath": "normalized/notice.pdf",
                "relativePath": "normalized/notice.pdf",
            },
            {
                "clientRef": "file:report",
                "sourceRelativePath": "normalized/report.pdf",
                "relativePath": "normalized/report.pdf",
            },
            {
                "clientRef": "file:photo",
                "sourceRelativePath": "normalized/photo.pdf",
                "relativePath": "normalized/photo.pdf",
            },
        ],
        "documentSlots": [
            {
                "clientRef": "slot:photo",
                "slotCode": "INITIAL_ONSITE_PHOTO",
                "inspectionRef": "initial",
                "versions": [{"kind": "SCANNED", "fileRef": "file:photo"}],
            }
        ],
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
    data_path = case.work / "case-data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 0
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 0
    done = out(capsys)
    receipt_json = json.loads(
        Path(done["archive"]["verificationRecord"]).read_text(encoding="utf-8")
    )
    supplements = receipt_json["localVerification"]["supplements"]
    assert len(supplements) == 1 and supplements[0]["sourceType"] == "VISIBLE_SOURCE_PREVIEW"
    original = Path(done["archive"]["archivedOriginal"])
    assert (original / "补采材料" / Path(binding["fileRelativePath"]).name).is_file()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    assert out(capsys)["status"] == "ALREADY_COMPLETED"
    # a supplement replaced after archiving makes the completion need review, evidence untouched
    archived_file = original / "补采材料" / Path(binding["fileRelativePath"]).name
    archived_file.write_bytes(archived_file.read_bytes() + b"x")
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    assert out(capsys)["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tamper": True}, "摘要"),
        ({"project": "32002207C202600034"}, "串案"),
        ({"rwid": "32199999999"}, "来源记录标识"),
    ],
)
def test_supplement_rejects_tampered_wrong_project_and_foreign_rwid(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str], kwargs: dict, message: str
) -> None:
    prepare_rwid(case)
    file, receipt = supplement_files(tmp_path, case, **kwargs)
    capsys.readouterr()
    assert attach(case, file, receipt) == 2
    assert message in capsys.readouterr().err
    assert "supplements" not in case.record()["source"]
    assert not (case.pending / "补采材料").exists()


def test_supplement_replaced_after_binding_blocks_split_and_verification(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_rwid(case)
    file, receipt = supplement_files(tmp_path, case)
    assert attach(case, file, receipt) == 0
    binding = next(iter(case.record()["source"]["supplements"].values()))
    stored = case.layout.root / binding["fileRelativePath"]
    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    stored.write_bytes(stored.read_bytes() + b"\n%swapped")
    plan = {
        "items": [
            {
                "sourceRelativePath": "补采材料/" + stored.name,
                "pageStart": 1,
                "pageEnd": 1,
                "relativePath": "normalized/photo.pdf",
            }
        ]
    }
    plan_path = case.work / "split-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert case.run("split", "--work-dir", str(case.work), "--plan", str(plan_path)) == 2
    assert "补采材料校验失败" in capsys.readouterr().err
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    capsys.readouterr()


def test_supplement_rejects_missing_receipt_zip_member_and_unsafe_names(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_rwid(case)
    file, receipt = supplement_files(tmp_path, case)
    assert attach(case, file, tmp_path / "missing.receipt.json") == 2
    capsys.readouterr()
    # the same bytes as a ZIP member is not a supplement
    member = tmp_path / "member.pdf"
    import zipfile

    with zipfile.ZipFile(case.zip) as archive:
        member.write_bytes(archive.read("通知书.pdf"))
    sha = "sha256:" + hashlib.sha256(member.read_bytes()).hexdigest()
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["sha256"], data["pageCount"], data["download"]["bytes"] = sha, 2, member.stat().st_size
    receipt2 = tmp_path / "member.receipt.json"
    receipt2.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert attach(case, member, receipt2) == 2
    assert "ZIP 成员" in capsys.readouterr().err
    bad = tmp_path / "..hidden.pdf"
    bad.write_bytes(file.read_bytes())
    assert attach(case, bad, receipt) == 2
    capsys.readouterr()


def test_unconfirmed_source_gaps_still_block_completion_with_supplements(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_rwid(case)
    ws.upsert_case(case.layout, PROJECT, source={"materialGaps": [{"name": "缺件"}]})
    file, receipt = supplement_files(tmp_path, case)
    assert attach(case, file, receipt) == 0
    capsys.readouterr()
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    assert "缺口" in capsys.readouterr().err


# ---------------------------------------------------------------- images


def make_png(
    width: int = 4, height: int = 3, color: int = 2, interlace: int = 0, alpha_trns: bool = False
) -> bytes:
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    rows = b"".join(
        b"\x00" + bytes((x * 7 + y * 13 + c) % 251 for x in range(width) for c in range(channels))
        for y in range(height)
    )

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    extra = chunk(b"tRNS", b"\x00\x00") if alpha_trns else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, interlace))
        + extra
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def make_jpeg(
    width: int = 17,
    height: int = 13,
    orientation: int | None = None,
    sampling: tuple[tuple[int, int], ...] = ((1, 1),),
    restart: int = 0,
    seed: int = 1,
    scan_edit: object = None,
) -> bytes:
    """A real baseline JPEG: valid headers plus a Huffman-coded scan (tiny custom tables)."""
    import random

    rng = random.Random(seed)
    hmax = max(h for h, _v in sampling)
    vmax = max(v for _h, v in sampling)
    if len(sampling) == 1:
        blocks_per_mcu = 1
        mcus = -(-width // 8) * -(-height // 8)
    else:
        blocks_per_mcu = sum(h * v for h, v in sampling)
        mcus = -(-width // (8 * hmax)) * -(-height // (8 * vmax))

    def block(out: list[str]) -> None:
        size = rng.choice([0, 3, 5])
        out.append(format(size, "04b"))  # DC code: 4 bits, symbol == category
        if size:
            out.append(format(rng.getrandbits(size), f"0{size}b"))
        for _ in range(rng.choice([0, 1, 2])):
            out.append(rng.choice(["001", "010"]))  # (0,1) or (1,1) coefficient
            out.append(str(rng.getrandbits(1)))
        out.append("000")  # EOB

    def stuffed(bits: str) -> bytes:
        bits += "1" * (-len(bits) % 8)
        raw = int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""
        return raw.replace(b"\xff", b"\xff\x00")

    scan = b""
    interval = restart or mcus
    for index in range(0, mcus, interval):
        out: list[str] = []
        for _ in range(min(interval, mcus - index) * blocks_per_mcu):
            block(out)
        scan += stuffed("".join(out))
        if restart and index + interval < mcus:
            scan += bytes([0xFF, 0xD0 + (index // interval) % 8])
    if callable(scan_edit):
        scan = scan_edit(scan)

    app1 = b""
    if orientation:
        tiff = (
            b"MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01"
            + struct.pack(">H", orientation)
            + b"\x00\x00\x00\x00\x00\x00"
        )
        app1 = b"\xff\xe1" + struct.pack(">H", len(tiff) + 8) + b"Exif\0\0" + tiff
    comps = b"".join(bytes([i + 1, (h << 4) | v, 0]) for i, (h, v) in enumerate(sampling))
    sof = struct.pack(">BHHB", 8, height, width, len(sampling)) + comps
    dht_dc = b"\x00" + bytes([0] * 3 + [12] + [0] * 12) + bytes(range(12))
    dht_ac = b"\x10" + bytes([0, 0, 4] + [0] * 13) + bytes([0x00, 0x01, 0x11, 0xF0])
    sos = bytes([len(sampling)]) + b"".join(bytes([i + 1, 0]) for i in range(len(sampling)))
    sos += b"\x00\x3f\x00"
    dri = b"\xff\xdd\x00\x04" + struct.pack(">H", restart) if restart else b""

    def seg(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body

    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x48\x00\x48\x00\x00"
        + app1
        + seg(0xDB, b"\x00" + bytes([1] * 64))
        + seg(0xC0, sof)
        + seg(0xC4, dht_dc + dht_ac)
        + dri
        + seg(0xDA, sos)
        + scan
        + b"\xff\xd9"
    )


@pytest.mark.parametrize("color", [0, 2, 4, 6])
def test_png_pdf_keeps_pixels_and_rejects_swaps(color: int) -> None:
    data = make_png(color=color)
    pdf, mapping = image_pdf.build_pdf(data)
    assert image_pdf.verify_pdf(pdf, data, mapping) == []
    assert mapping["pixelWidth"] == 4 and mapping["pixelHeight"] == 3 and mapping["pageCount"] == 1
    other = make_png(width=5, color=color)
    assert image_pdf.verify_pdf(pdf, other, mapping)  # replaced source image
    tampered = bytearray(pdf)
    start = tampered.find(b"stream\n", tampered.find(b"/Subtype/Image")) + 7
    tampered[start + 2] ^= 0xFF
    assert image_pdf.verify_pdf(bytes(tampered), data, mapping)  # altered embedded pixels


def test_jpeg_is_embedded_raw_and_orientation_is_kept_not_redrawn() -> None:
    data = make_jpeg(orientation=6)
    pdf, mapping = image_pdf.build_pdf(data)
    assert mapping["method"] == "EMBED_JPEG_RAW" and mapping["rotate"] == 90
    assert data in pdf and image_pdf.verify_pdf(pdf, data, mapping) == []


@pytest.mark.parametrize(
    "data",
    [
        make_png(interlace=1),
        make_png(alpha_trns=True),
        make_jpeg(orientation=2),
        b"GIF89a....",
        make_png()[:-8],
    ],
)
def test_unfaithful_or_broken_images_are_rejected(data: bytes) -> None:
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(data)


REVIEW_INPUTS = Path("/mnt/d/ws/05-temp/20261103_案卷汇总_S2工具独验/image-review/inputs")


def corrupt_png(kind: str) -> bytes:
    good = make_png(6, 5)
    pos = good.index(b"IDAT")
    length = struct.unpack(">I", good[pos - 4 : pos])[0]
    body = good[pos + 4 : pos + 4 + length]

    def rebuild(new: bytes) -> bytes:
        chunk = (
            struct.pack(">I", len(new))
            + b"IDAT"
            + new
            + struct.pack(">I", zlib.crc32(b"IDAT" + new))
        )
        return good[: pos - 4] + chunk + good[pos + 4 + length + 4 :]

    if kind == "idat_bad":
        return rebuild(body[:2] + bytes(b ^ 0xFF for b in body[2:-4]) + body[-4:])
    if kind == "short":
        return rebuild(zlib.compress(zlib.decompress(body)[:-9]))
    if kind == "filter":
        raw = bytearray(zlib.decompress(body))
        raw[0] = 9
        return rebuild(zlib.compress(bytes(raw)))
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["idat_bad", "short", "filter"])
def test_png_with_undecodable_pixels_is_rejected_on_generate_and_verify(kind: str) -> None:
    bad = corrupt_png(kind)
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(bad)
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.decodable_png_size(bad)
    good = make_png(6, 5)
    pdf = image_pdf.build_pdf(good)
    pdf = pdf if isinstance(pdf, bytes) else pdf[0]
    assert image_pdf.decodable_png_size(good) == (6, 5)


def test_unsupported_density_and_colour_spaces_are_rejected() -> None:
    def phys(units: int, x: int, y: int) -> bytes:
        good = make_png()
        body = struct.pack(">IIB", x, y, units)
        chunk = (
            struct.pack(">I", len(body))
            + b"pHYs"
            + body
            + struct.pack(">I", zlib.crc32(b"pHYs" + body))
        )
        pos = good.index(b"IDAT") - 4
        return good[:pos] + chunk + good[pos:]

    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(phys(1, 2835, 5670))
    image_pdf.build_pdf(phys(1, 2835, 2835))
    cmyk = make_jpeg()
    sof = cmyk.index(b"\xff\xc0")
    cmyk = cmyk[: sof + 9] + b"\x04" + cmyk[sof + 10 :]
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(cmyk)


@pytest.mark.skipif(not REVIEW_INPUTS.is_dir(), reason="独验样本不在本机")
@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("rgb.png", True),
        ("gray.png", True),
        ("palette.png", True),
        ("alpha.png", True),
        ("real032.png", True),
        ("rgb.jpg", True),
        ("png_idat_bad.png", False),
        ("png_scanline_short.png", False),
        ("png_crc_bad.png", False),
        ("cmyk.jpg", False),
        ("density_unequal.png", False),
        ("jpeg_truncated.jpg", False),
    ],
)
def test_review_samples(name: str, ok: bool) -> None:
    data = (REVIEW_INPUTS / name).read_bytes()
    if ok:
        image_pdf.build_pdf(data)
    else:
        with pytest.raises(image_pdf.ImageNormalizationError):
            image_pdf.build_pdf(data)


def jpeg_cases() -> list[tuple[str, bytes]]:
    return [
        ("gray", make_jpeg()),
        ("gray-restart", make_jpeg(40, 24, restart=3)),
        ("rgb444", make_jpeg(20, 9, sampling=((1, 1), (1, 1), (1, 1)))),
        ("rgb420", make_jpeg(35, 21, sampling=((2, 2), (1, 1), (1, 1)))),
        ("rgb422-restart", make_jpeg(33, 17, sampling=((2, 1), (1, 1), (1, 1)), restart=2)),
        ("exif", make_jpeg(orientation=6)),
    ]


@pytest.mark.parametrize(
    ("name", "data"), jpeg_cases(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_healthy_jpegs_keep_raw_bytes_and_verify(name: str, data: bytes) -> None:
    pdf, mapping = image_pdf.build_pdf(data)
    assert data in pdf and image_pdf.verify_pdf(pdf, data, mapping) == []


def _flip_to_invalid(scan: bytes) -> bytes:
    return b"\xff\x00" * 6 + scan[6:]  # all-one prefixes are unused codes in the tables


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s[: len(s) // 2],  # scan cut short, EOI kept
        lambda s: s[:-5],
        lambda s: s + b"\x5a" * 40,  # extra data
        lambda s: s[:10] + s[25:],  # a chunk removed
        _flip_to_invalid,
        lambda s: b"",
    ],
    ids=["half", "short", "extra", "chunk-removed", "invalid-code", "empty"],
)
@pytest.mark.parametrize("sampling", [((1, 1),), ((2, 2), (1, 1), (1, 1))], ids=["gray", "ycc420"])
def test_damaged_jpeg_scan_is_rejected_by_generation_and_verification(
    edit: object, sampling: tuple[tuple[int, int], ...]
) -> None:
    good = make_jpeg(48, 40, sampling=sampling)
    bad = make_jpeg(48, 40, sampling=sampling, scan_edit=edit)
    assert bad != good and bad.endswith(b"\xff\xd9")
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(bad)
    pdf, mapping = image_pdf.build_pdf(good)
    # an old PDF/manifest whose recorded digests matched the damaged source must not verify
    forged = pdf.replace(good, bad)
    matching = {**mapping, "sourceSha256": image_pdf.sha256(bad), "sourceSizeBytes": len(bad)}
    forged = forged.replace(mapping["sourceSha256"].encode(), image_pdf.sha256(bad).encode())
    reasons = image_pdf.verify_pdf(forged, bad, matching)
    assert reasons and "无法读取" in reasons[0]


def test_jpeg_vendor_trailer_after_eoi_is_recorded_not_embedded() -> None:
    good = make_jpeg(48, 40)
    trailer = b"\x00" * 64 + b"\x08\x36\x00\x00ctrace\x00\x00"
    pdf, mapping = image_pdf.build_pdf(good + trailer)
    assert mapping["trailingBytes"] == len(trailer)
    assert mapping["trailingSha256"] == image_pdf.sha256(trailer)
    assert good in pdf and trailer not in pdf
    assert image_pdf.verify_pdf(pdf, good + trailer, mapping) == []
    # the recorded source digest binds the full original bytes, not the trimmed ones
    assert mapping["sourceSha256"] == image_pdf.sha256(good + trailer)
    assert "trailingBytes" not in image_pdf.build_pdf(good)[1]
    with pytest.raises(image_pdf.ImageNormalizationError, match="另一幅图像"):
        image_pdf.build_pdf(good + b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    with pytest.raises(image_pdf.ImageNormalizationError):
        image_pdf.build_pdf(good[:-2] + trailer)  # no EOI after the scan: still truncated


def test_progressive_jpeg_is_rejected_as_unverifiable() -> None:
    data = make_jpeg()
    sof = data.index(b"\xff\xc0")
    progressive = data[: sof + 1] + b"\xc2" + data[sof + 2 :]
    with pytest.raises(image_pdf.ImageNormalizationError, match="渐进"):
        image_pdf.build_pdf(progressive)


def test_generated_jpegs_agree_with_an_independent_decoder() -> None:
    pil = pytest.importorskip("PIL.Image")
    import io

    for _name, data in jpeg_cases():
        pil.open(io.BytesIO(data)).load()  # encoder fixture itself is a real, loadable JPEG


def test_normalize_images_cli_binds_source_and_verification_rejects_swapped_source(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    import zipfile

    png = make_png(width=20, height=10)
    with zipfile.ZipFile(case.zip, "a") as archive:
        archive.writestr("认证证书.png", png)
    new_sha = "sha256:" + hashlib.sha256(case.zip.read_bytes()).hexdigest()
    ws.upsert_case(case.layout, PROJECT, source={"packageSha256": new_sha})
    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    assert (
        case.run("normalize-images", "--work-dir", str(case.work)) == 2
    )  # explicit selection only
    capsys.readouterr()
    assert (
        case.run(
            "normalize-images", "--work-dir", str(case.work), "--relative-path", "认证证书.png"
        )
        == 0
    )
    produced = out(capsys)["items"][0]
    assert (
        case.run(
            "normalize-images", "--work-dir", str(case.work), "--relative-path", "认证证书.png"
        )
        == 0
    )
    assert out(capsys)["items"][0]["status"] == "REUSED"
    index = json.loads((case.work / "image-normalization.json").read_text(encoding="utf-8"))
    item = index["items"][0]
    assert item["sourceSha256"] == "sha256:" + hashlib.sha256(png).hexdigest()
    assert (
        item["pixelWidth"] == 20
        and item["pixelHeight"] == 10
        and item["sourceKind"] == "ZIP_MEMBER"
    )

    data = {
        "schemaVersion": "CaseImportManifestV2",
        "createdAt": "2026-11-03T00:00:00Z",
        "extractor": {"name": "fixture", "version": "1"},
        "case": {"projectNo": PROJECT, "brigadeCode": "XISHAN", "unitName": "合成单位"},
        "initialInspection": {
            "clientRef": "initial",
            "stage": "INITIAL_CHECK",
            "inspectionDate": "2026-10-01",
            "products": [],
        },
        "files": [
            {
                "clientRef": "file:cert",
                "sourceRelativePath": produced["output"],
                "relativePath": "normalized/cert.pdf",
            }
        ],
        "documentSlots": [],
        "otherAttachments": [
            {
                "clientRef": "attachment:cert",
                "slotCode": "OTHER_ATTACHMENT",
                "title": "认证证书",
                "fileRef": "file:cert",
            }
        ],
    }
    data_path = case.work / "case-data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pdf_path = case.work / produced["output"]
    original_pdf = pdf_path.read_bytes()

    # replaced PDF (valid PDF, different pixels): compose rejects it
    swapped, _ = image_pdf.build_pdf(make_png(width=20, height=10, color=0))
    pdf_path.write_bytes(swapped)
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 2
    assert "图片规范化" in capsys.readouterr().err
    pdf_path.write_bytes(original_pdf)
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 0
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    # replaced source image after compose: local-verify refuses
    extracted = case.work / "source" / "认证证书.png"
    extracted.write_bytes(make_png(width=21, height=10))
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    extracted.write_bytes(png)
    assert case.run("local-verify", "--project", PROJECT) == 0
    done = out(capsys)
    proof = json.loads(Path(done["archive"]["verificationRecord"]).read_text(encoding="utf-8"))[
        "localVerification"
    ]
    assert proof["imageNormalization"]["file:cert"]["sourceSha256"] == item["sourceSha256"]
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    assert out(capsys)["status"] == "ALREADY_COMPLETED"
    archived_pdf = next(Path(done["archive"]["archivedWorkspace"]).glob("normalized/image_*.pdf"))
    archived_pdf.write_bytes(swapped)
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2


def test_normalize_images_cli_rejects_damaged_jpeg_and_accepts_healthy_one(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    import zipfile

    good = make_jpeg(48, 40, sampling=((2, 2), (1, 1), (1, 1)))
    bad = make_jpeg(48, 40, sampling=((2, 2), (1, 1), (1, 1)), scan_edit=lambda s: s[:-9])
    with zipfile.ZipFile(case.zip, "a") as archive:
        archive.writestr("好图.jpg", good)
        archive.writestr("坏图.jpg", bad)
    new_sha = "sha256:" + hashlib.sha256(case.zip.read_bytes()).hexdigest()
    ws.upsert_case(case.layout, PROJECT, source={"packageSha256": new_sha})
    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    capsys.readouterr()
    run = ("normalize-images", "--work-dir", str(case.work), "--relative-path")
    assert case.run(*run, "坏图.jpg") == 2
    assert "JPEG" in capsys.readouterr().err
    index = case.work / "image-normalization.json"
    assert not index.exists() or "坏图.jpg" not in index.read_text(encoding="utf-8")
    assert case.run(*run, "好图.jpg") == 0
    capsys.readouterr()


# ------------------------------------------------------------------ brigade


def brigade_data(case: Case, code: str, name: str | None) -> Path:
    case.organize()  # produces a valid composed manifest to copy the file set from
    base = json.loads((case.work / "case-data.json").read_text(encoding="utf-8"))
    base["case"]["brigadeCode"] = code
    if name:
        base["case"]["brigadeName"] = name
    path = case.work / "case-data-brigade.json"
    path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return path


def bind_jurisdiction(
    case: Case,
    text: str | None,
    *,
    project: str = PROJECT,
    record_project: str | None = None,
    field_project: str | None = None,
    schema: str | None = "SourceEvidenceV1",
    rwid: str = RWID,
) -> None:
    prepare_rwid(case)
    evidence = case.pending / "source-evidence.json"
    record = {"fields": {"消防管辖": text}}
    if record_project is not None:
        record["projectNo"] = record_project
    if field_project is not None:
        record["fields"]["项目编号"] = field_project
    value = {"projectNo": project, "records": {rwid: record}}
    if schema:
        value["schemaVersion"] = schema
    evidence.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    sha = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={
            "classification": {
                "evidencePath": evidence.relative_to(case.layout.root).as_posix(),
                "evidenceSha256": sha,
            }
        },
    )


def bind_good(case: Case) -> None:
    bind_jurisdiction(
        case,
        "无锡市消防救援支队水上大队",
        record_project=PROJECT,
        field_project=PROJECT,
    )


def compose_with(case: Case, path: Path) -> int:
    (case.work / "manifest.json").unlink(missing_ok=True)
    (case.work / "upload-map.json").unlink(missing_ok=True)
    return case.run("compose", "--work-dir", str(case.work), "--case-data", str(path))


def test_local_water_brigade_needs_real_source_evidence_and_leaves_cloud_scope(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    data = brigade_data(case, "SHUISHANG", "水上大队")
    bind_jurisdiction(case, None)
    capsys.readouterr()
    assert compose_with(case, data) == 2  # no source statement of the jurisdiction
    assert "水上大队" in capsys.readouterr().err
    bind_good(case)
    assert compose_with(case, data) == 0
    assert case.run("local-verify", "--project", PROJECT) == 0
    done = out(capsys)
    proof = json.loads(Path(done["archive"]["verificationRecord"]).read_text(encoding="utf-8"))[
        "localVerification"
    ]
    assert proof["brigade"] == {
        "code": "SHUISHANG",
        "scope": "LOCAL_ONLY",
        "cloudReportingPermissionChanged": False,
    }
    assert "SHUISHANG" not in cli.BRIGADES  # cloud eight-brigade set is unchanged


def test_water_brigade_accepts_real_shape_evidence_without_schema_field(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    # the real classification evidence file carries projectNo but no top-level schemaVersion
    data = brigade_data(case, "SHUISHANG", "水上大队")
    bind_jurisdiction(
        case,
        "无锡市消防救援支队水上大队",
        schema=None,
        record_project=PROJECT,
        field_project=PROJECT,
    )
    assert compose_with(case, data) == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": "260000000000000"},
        {"project": ""},
        {"schema": "OtherV9"},
        {"record_project": "260000000000000"},
        {"field_project": "260000000000000"},
        {"record_project": None, "field_project": None},
        {"rwid": "999999"},
    ],
    ids=[
        "foreign-top",
        "no-top",
        "wrong-schema",
        "foreign-record",
        "foreign-field",
        "no-record-identity",
        "unbound-rwid",
    ],
)
def test_water_brigade_rejects_foreign_or_unidentified_classification_evidence(
    case: Case, capsys: pytest.CaptureFixture[str], kwargs: dict[str, object]
) -> None:
    data = brigade_data(case, "SHUISHANG", "水上大队")
    base: dict[str, object] = {
        "record_project": PROJECT,
        "field_project": PROJECT,
        "text": "无锡市消防救援支队水上大队",
    }
    base.update(kwargs)
    text = base.pop("text")
    bind_jurisdiction(case, text, **base)  # type: ignore[arg-type]
    capsys.readouterr()
    assert compose_with(case, data) == 2
    assert "水上大队" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("SHUISHANG", None),
        ("SHUISHANG", "水上"),
        ("XISHAN", "水上大队"),
        ("WATER", "水上大队"),
        ("", None),
    ],
)
def test_illegal_or_unnamed_brigade_values_stay_rejected(
    case: Case, capsys: pytest.CaptureFixture[str], code: str, name: str | None
) -> None:
    data = brigade_data(case, code, name)
    bind_good(case)
    capsys.readouterr()
    assert compose_with(case, data) == 2
    assert "brigade" in capsys.readouterr().err


# ------------------------------------------------------- replacement evidence


def setup_manual_intake(
    case: Case,
    *,
    shot_bytes: bytes | None = None,
    schema: str | None = "ManualCaseIntakeReceiptV1",
    field_project: str | None = PROJECT,
    dimensions: list[int] | None = None,
) -> str:
    ledger_sha = "sha256:" + "d3" * 32
    missing = case.pending / "source-evidence.json"
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={
            "classification": {
                "evidencePath": missing.relative_to(case.layout.root).as_posix(),
                "evidenceSha256": ledger_sha,
            }
        },
    )
    # the case's own reviewed manual intake: receipt + screenshot beside the ZIP
    shot = case.pending / "案卷详情_manual.png"
    shot.write_bytes(shot_bytes if shot_bytes is not None else make_png(4, 3))
    shot_sha = "sha256:" + hashlib.sha256(shot.read_bytes()).hexdigest()
    receipt = case.pending / "manual-intake.json"
    body: dict[str, object] = {
        "projectNo": PROJECT,
        "package": {
            "relativePath": case.zip.relative_to(case.layout.root).as_posix(),
            "sha256": case.zip_sha,
        },
        "screenshot": {
            "relativePath": shot.relative_to(case.layout.root).as_posix(),
            "sha256": shot_sha,
            "dimensions": dimensions or [4, 3],
        },
    }
    if schema:
        body["schemaVersion"] = schema
    if field_project:
        body["fields"] = {"projectNo": field_project}
    receipt.write_text(json.dumps(body), encoding="utf-8")
    r_sha = "sha256:" + hashlib.sha256(receipt.read_bytes()).hexdigest()
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={
            "manualIntakes": {
                "abc123": {
                    "evidencePath": receipt.relative_to(case.layout.root).as_posix(),
                    "evidenceSha256": r_sha,
                    "packageSha256": case.zip_sha,
                    "screenshotSha256": shot_sha,
                    "status": "PRIMARY_RECEIVED",
                }
            }
        },
    )
    return ledger_sha


@pytest.mark.parametrize(
    "kwargs",
    [
        {"shot_bytes": b"\x89PNG\r\n\x1a\nmanual"},
        {"shot_bytes": make_png(4, 3)[:-30]},
        {"schema": None},
        {"schema": "OtherV9"},
        {"field_project": None},
        {"field_project": "260000000000000"},
        {"dimensions": [9, 9]},
    ],
    ids=["text-png", "short-png", "no-schema", "bad-schema", "no-identity", "foreign", "dims"],
)
def test_rebind_refuses_fake_or_unidentified_manual_intake(
    case: Case, capsys: pytest.CaptureFixture[str], kwargs: dict[str, object]
) -> None:
    setup_manual_intake(case, **kwargs)  # type: ignore[arg-type]
    case.organize()
    capsys.readouterr()
    args = ("source", "rebind-evidence", "--project", PROJECT, "--intake-id", "abc123")
    assert case.run(*args, "--reason", "旧证据丢失") == 2
    assert "evidenceReplacement" not in (case.record()["source"]["classification"])


def test_rebind_missing_classification_evidence_with_real_manual_intake_then_archive(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger_sha = setup_manual_intake(case)
    case.organize()
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT) == 2  # archive refuses: evidence absent
    capsys.readouterr()
    assert (
        case.run(
            "source",
            "rebind-evidence",
            "--project",
            PROJECT,
            "--intake-id",
            "nope",
            "--reason",
            "x",
        )
        == 2
    )
    capsys.readouterr()
    assert (
        case.run(
            "source",
            "rebind-evidence",
            "--project",
            PROJECT,
            "--intake-id",
            "abc123",
            "--reason",
            "旧证据丢失，改绑真实手动交件",
        )
        == 0
    )
    assert out(capsys)["status"] == "REBOUND"
    classification = case.record()["source"]["classification"]
    assert classification["evidenceSha256"] == ledger_sha  # the old digest stays as recorded fact
    assert classification["evidenceReplacement"]["supersededEvidenceState"] == "MISSING"
    assert (
        case.run(
            "source",
            "rebind-evidence",
            "--project",
            PROJECT,
            "--intake-id",
            "abc123",
            "--reason",
            "旧证据丢失，改绑真实手动交件",
        )
        == 0
    )
    assert out(capsys)["status"] == "REUSED"
    assert case.run("local-verify", "--project", PROJECT) == 0
    assert out(capsys)["status"] == "COMPLETED"
    archived = case.record()["source"]["classification"]
    assert (
        archived["evidenceSha256"] == ledger_sha
        and archived["evidenceReplacement"]["status"] == "REPLACED_BY_MANUAL_INTAKE"
    )
    assert (case.layout.root / archived["evidenceReplacement"]["evidencePath"]).is_file()


def test_rebind_refused_when_original_evidence_intact_or_intake_belongs_elsewhere(
    case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = case.pending / "source-evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    sha = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    ws.upsert_case(
        case.layout,
        PROJECT,
        source={
            "classification": {
                "evidencePath": evidence.relative_to(case.layout.root).as_posix(),
                "evidenceSha256": sha,
            }
        },
    )
    assert (
        case.run(
            "source", "rebind-evidence", "--project", PROJECT, "--intake-id", "x", "--reason", "x"
        )
        == 2
    )
    assert "无需重新绑定" in capsys.readouterr().err


# -------------------------------------------------------------- retired upload


@pytest.mark.parametrize("command", ["upload", "verify", "supplement", "upload-batch"])
def test_retired_upload_commands_stay_rejected(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([command, "--anything"]) == 2
    assert "已退役" in capsys.readouterr().err
