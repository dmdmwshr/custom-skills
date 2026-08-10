from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

import scripts.registry_cli as cli
from scripts.registry_cli import (
    RegistryError,
    compose_command,
    file_sha256,
    inventory_command,
    ocr_command,
    read_json,
    split_command,
    upload_command,
    validate_manifest,
    verify_command,
)

PROJECT = "32002207C202600033"
ROOT = Path(__file__).resolve().parents[1]


def pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def manifest(source: Path) -> dict[str, object]:
    return {
        "schemaVersion": "CaseImportManifestV2",
        "packageSha256": "sha256:" + "a" * 64,
        "createdAt": "2026-08-10T00:00:00Z",
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
            "inspectionDate": "2026-08-01",
            "products": [],
        },
        "files": [
            {
                "clientRef": "file:one",
                "relativePath": "files/one.pdf",
                "sha256": file_sha256(source),
                "mimeType": "application/pdf",
                "pageCount": 1,
            }
        ],
        "documentSlots": [],
        "otherAttachments": [
            {
                "clientRef": "attachment:one",
                "slotCode": "OTHER_ATTACHMENT",
                "title": "附件",
                "fileRef": "file:one",
            }
        ],
    }


def test_explicit_51_slot_map_is_correct() -> None:
    assert len(cli.SLOT_META) == 51
    for code in (
        "ONSITE_UNQUALIFIED_NOTICE",
        "RECTIFICATION_ORDER",
        "EVIDENCE_PRESERVATION_DECISION",
    ):
        assert cli.SLOT_META[code] == ("INSPECTION", "INITIAL_CHECK")
    assert cli.SLOT_META["ILLEGAL_PRODUCT_NOTIFICATION_LETTER"] == (
        "NOTIFICATION_TARGET",
        "INITIAL_CHECK",
    )
    assert cli.SLOT_META["INITIAL_CCC_CERTIFICATE"] == ("PRODUCT", "INITIAL_CHECK")


@pytest.mark.parametrize("code", sorted(cli.SLOT_META))
def test_each_slot_accepts_only_its_explicit_owner(code: str, tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    multiplicity, stage = cli.SLOT_META[code]
    if multiplicity == "OTHER":
        assert code == "OTHER_ATTACHMENT"
        return
    data["initialInspection"]["products"] = [{"clientRef": "product:initial", "name": "产品"}]
    data["recheckInspection"] = {
        "clientRef": "recheck",
        "stage": "RECHECK",
        "products": [{"clientRef": "product:recheck", "name": "复查产品"}],
    }
    slot: dict[str, object] = {
        "clientRef": "slot:one",
        "slotCode": code,
        "versions": [
            {"kind": "SCANNED" if code in cli.PHOTO_SLOTS else "ELECTRONIC", "fileRef": "file:one"}
        ],
    }
    if multiplicity == "INSPECTION":
        slot["inspectionRef"] = "initial" if stage == "INITIAL_CHECK" else "recheck"
    if multiplicity == "PRODUCT":
        slot["productRef"] = "product:initial" if stage == "INITIAL_CHECK" else "product:recheck"
    if multiplicity == "NOTIFICATION_TARGET":
        data["case"]["notificationTarget"] = "PRODUCTION"
        slot["notificationTarget"] = "PRODUCTION"
    data["otherAttachments"] = []
    data["documentSlots"] = [slot]
    assert validate_manifest(data, {"file:one": str(source)}) == []
    if multiplicity == "PRODUCT":
        slot["inspectionRef"] = "initial"
        assert any(
            "只填写" in error for error in validate_manifest(data, {"file:one": str(source)})
        )


def test_bom_compose_uses_only_inventory_hash_and_source_relative_path(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf(source_root / "一.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source_root), work_dir=str(work)))
    data = manifest(source_root / "一.pdf")
    data.pop("packageSha256")
    data["files"][0].update({"sourceRelativePath": "一.pdf", "relativePath": "normalized/一.pdf"})
    case_data = tmp_path / "case.json"
    case_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))
    assert (
        read_json(work / "manifest.json")["packageSha256"]
        == read_json(work / "inventory.json")["packageSha256"]
    )
    data["packageSha256"] = "sha256:" + "b" * 64
    case_data.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RegistryError, match="精确一致"):
        compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))


def test_split_preflights_png_collision_and_never_writes_outside_normalized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "a.pdf", 2)
    (source / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    plan = {
        "items": [
            {
                "sourceRelativePath": "a.pdf",
                "pageStart": 1,
                "pageEnd": 1,
                "relativePath": "normalized/a.pdf",
            },
            {
                "sourceRelativePath": "a.pdf",
                "pageStart": 2,
                "pageEnd": 2,
                "relativePath": "normalized/a.pdf",
            },
        ]
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(RegistryError, match="重复"):
        split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))
    assert not (work / "normalized").exists()
    plan["items"] = [
        {
            "sourceRelativePath": "bad.png",
            "pageStart": 1,
            "pageEnd": 1,
            "relativePath": "normalized/no.pdf",
        }
    ]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(RegistryError, match="PDF"):
        split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))


def test_mineru_uses_explicit_sources_and_records_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "one.pdf")
    (source / "two.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    mineru = tmp_path / "run-mineru-docker.ps1"
    powershell = tmp_path / "powershell.exe"
    mineru.write_text("# fixture", encoding="utf-8")
    powershell.write_bytes(b"fixture")
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "MINERU_SCRIPT", mineru)
    monkeypatch.setattr(cli, "SYSTEM_POWERSHELL", powershell)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command) or subprocess.CompletedProcess(command, 0, "ok", "")
        ),
    )
    ocr_command(
        argparse.Namespace(
            work_dir=str(work),
            output_dir=str(work / "ocr"),
            relative_path=["one.pdf", "two.png"],
            timeout=10,
        )
    )
    assert len(calls) == 2 and all(command[-1] == "-NoBuild" for command in calls)
    assert len(read_json(work / "ocr-result.json")["mappings"]) == 2
    with pytest.raises(RegistryError, match="显式"):
        ocr_command(
            argparse.Namespace(
                work_dir=str(work), output_dir=str(work / "ocr"), relative_path=[], timeout=10
            )
        )


def test_schema_semantics_reject_missing_method_bad_date_and_bad_scope(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["initialInspection"]["products"] = [
        {"clientRef": "product:one", "name": "产品", "result": "QUALIFIED"}
    ]
    data["documentSlots"] = [
        {
            "clientRef": "slot:one",
            "slotCode": "INITIAL_CCC_CERTIFICATE",
            "inspectionRef": "initial",
            "versions": [{"kind": "ELECTRONIC", "fileRef": "file:one"}],
            "documentDate": "2026-02-30",
        }
    ]
    data["otherAttachments"] = []
    errors = validate_manifest(data, {"file:one": str(source)})
    assert (
        any("method" in error for error in errors)
        and any("documentDate" in error for error in errors)
        and any("只填写" in error for error in errors)
    )


def test_reference_v2_example_is_readable_and_valid_after_product_owner_fix() -> None:
    example = read_json(ROOT / "references" / "CaseImportManifestV2.example.json")
    assert example["schemaVersion"] == "CaseImportManifestV2"
    assert validate_manifest(example) == []


def test_finalized_unverified_retries_read_only_and_origin_is_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path = tmp_path / "manifest.json"
    map_path = tmp_path / "map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    remote = {"case": False, "fail_verify": True}
    posts: list[str] = []
    case_id = "11111111-1111-4111-8111-111111111111"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.url.path)
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v2/cases":
            return httpx.Response(
                200,
                json={"data": [{"id": case_id, "projectNo": PROJECT}] if remote["case"] else []},
            )
        if request.url.path == "/api/v2/import-jobs":
            return httpx.Response(200, json={"id": "job"})
        if request.url.path.endswith("/files"):
            return httpx.Response(201, json={})
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/finalize"):
            remote["case"] = True
            return httpx.Response(200, json={"caseId": case_id, "created": True})
        if request.url.path == f"/api/v2/cases/{case_id}":
            return httpx.Response(
                200,
                json={
                    "id": case_id,
                    "projectNo": PROJECT,
                    "brigade": {"code": "XISHAN"},
                    "inspectionForm": "ROUTINE",
                    "notificationTarget": "UNKNOWN",
                    "inspections": [
                        {
                            "id": "initial-id",
                            "stage": "INITIAL_CHECK",
                            "inspectionDate": "2026-08-01",
                            "products": [],
                        }
                    ],
                },
            )
        if request.url.path == f"/api/v2/cases/{case_id}/directory":
            return httpx.Response(
                500 if remote["fail_verify"] else 200,
                json={
                    "rows": [
                        {
                            "slotKey": "OTHER_ATTACHMENT",
                            "children": [{"title": "附件", "files": [{"id": "file-id"}]}],
                        }
                    ]
                },
            )
        if request.url.path == "/api/v2/files/file-id":
            return httpx.Response(200, content=source.read_bytes())
        raise AssertionError(request.url)

    real = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kwargs: real(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
            headers=kwargs.get("headers"),
            follow_redirects=False,
        ),
    )
    args = argparse.Namespace(
        manifest=str(manifest_path),
        upload_map=str(map_path),
        api_base="https://registry.example",
        timeout=10.0,
        dry_run=False,
        finalize=True,
    )
    with pytest.raises(RegistryError):
        upload_command(args)
    state = read_json(tmp_path / "upload-state.json")
    assert (
        state["status"] == "FINALIZED_UNVERIFIED" and state["origin"] == "https://registry.example"
    )
    count = len(posts)
    remote["fail_verify"] = False
    upload_command(args)
    assert len(posts) == count and read_json(tmp_path / "upload-state.json")["status"] == "VERIFIED"
    with pytest.raises(RegistryError, match="origin"):
        upload_command(argparse.Namespace(**{**vars(args), "api_base": "https://other.example"}))
    verify_command(
        argparse.Namespace(
            manifest=str(manifest_path),
            upload_map=str(map_path),
            api_base="https://registry.example",
            timeout=10.0,
        )
    )


def test_uploading_state_with_existing_case_recovers_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path, map_path = tmp_path / "manifest.json", tmp_path / "map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    origin, case_id = "https://registry.example", "11111111-1111-4111-8111-111111111111"
    cli.write_json(
        tmp_path / "upload-state.json",
        {
            "stateVersion": 4,
            "status": "UPLOADING",
            "origin": origin,
            "manifestSha256": file_sha256(manifest_path),
            "packageSha256": data["packageSha256"],
            "projectNo": PROJECT,
            "jobId": "job",
            "uploadedFileRefs": ["file:one"],
        },
    )
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request.url.path)
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v2/cases":
            return httpx.Response(200, json={"data": [{"id": case_id, "projectNo": PROJECT}]})
        if request.url.path == f"/api/v2/cases/{case_id}":
            return httpx.Response(
                200,
                json={
                    "id": case_id,
                    "projectNo": PROJECT,
                    "brigade": {"code": "XISHAN"},
                    "inspectionForm": "ROUTINE",
                    "notificationTarget": "UNKNOWN",
                    "inspections": [
                        {
                            "id": "initial-id",
                            "stage": "INITIAL_CHECK",
                            "inspectionDate": "2026-08-01",
                            "products": [],
                        }
                    ],
                },
            )
        if request.url.path == f"/api/v2/cases/{case_id}/directory":
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "slotKey": "OTHER_ATTACHMENT",
                            "children": [{"title": "附件", "files": [{"id": "file-id"}]}],
                        }
                    ]
                },
            )
        if request.url.path == "/api/v2/files/file-id":
            return httpx.Response(200, content=source.read_bytes())
        raise AssertionError(request.url)

    real = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kwargs: real(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
            headers=kwargs.get("headers"),
            follow_redirects=False,
        ),
    )
    upload_command(
        argparse.Namespace(
            manifest=str(manifest_path),
            upload_map=str(map_path),
            api_base=origin,
            timeout=10.0,
            dry_run=False,
            finalize=True,
        )
    )
    assert posts == []
    assert read_json(tmp_path / "upload-state.json")["status"] == "VERIFIED"


def test_dry_run_is_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    path = tmp_path / "manifest.json"
    mapping = tmp_path / "map.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    mapping.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run 不得联网")),
    )
    upload_command(
        argparse.Namespace(
            manifest=str(path),
            upload_map=str(mapping),
            api_base="https://registry.example",
            timeout=10.0,
            dry_run=True,
            finalize=False,
        )
    )
