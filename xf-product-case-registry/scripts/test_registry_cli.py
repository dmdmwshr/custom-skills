from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfWriter

import scripts.registry_cli as cli
import scripts.source_intake as source
import scripts.workspace_state as workspace
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
TEST_CSRF = "csrf-token-value-at-least-32-characters"
TEST_USER_ID = "22222222-2222-4222-8222-222222222222"


def auth_config(tmp_path: Path) -> Path:
    path = tmp_path / "auth.toml"
    path.write_text(
        '[auth]\nusername = "fixture-admin"\npassword = "fixture-password-not-real"\n',
        encoding="utf-8",
    )
    return path


def auth_session(
    *,
    role: str = "ADMIN",
    brigade_id: str | None = None,
    brigade_code: str | None = None,
    must_change_password: bool = False,
) -> dict[str, object]:
    brigade = (
        {
            "id": brigade_id,
            "code": brigade_code,
            "name": "测试大队",
            "routePath": "/fixture",
        }
        if role == "BRIGADE" and brigade_id and brigade_code
        else None
    )
    return {
        "user": {
            "id": TEST_USER_ID,
            "username": "fixture-admin",
            "displayName": "测试账户",
            "role": role,
            "brigadeId": brigade_id,
            "brigadeCode": brigade_code,
            "brigade": brigade,
            "csrfToken": TEST_CSRF,
            "authMethod": "SESSION",
            "mustChangePassword": must_change_password,
            "version": 1,
        },
        "csrfToken": TEST_CSRF,
        "expiresAt": "2026-08-13T01:00:00Z",
        "absoluteExpiresAt": "2026-08-13T12:00:00Z",
    }


def auth_route(
    request: httpx.Request, session: dict[str, object] | None = None
) -> httpx.Response | None:
    session = session or auth_session()
    if request.url.path == "/api/auth/login":
        assert request.method == "POST"
        assert request.headers["origin"] == "https://registry.example"
        return httpx.Response(
            200,
            json=session,
            headers={
                "Set-Cookie": (
                    "__Host-product_case_session=fixture-session; Path=/; "
                    "Secure; HttpOnly; SameSite=Lax"
                )
            },
        )
    if request.url.path == "/api/auth/session":
        assert request.method == "GET"
        assert "__Host-product_case_session=fixture-session" in request.headers.get("cookie", "")
        assert "x-csrf-token" not in request.headers
        assert "origin" not in request.headers
        return httpx.Response(200, json=session)
    return None


def admin_state_identity() -> dict[str, str | None]:
    session = auth_session()
    return cli.state_identity(cli.authenticated_identity(session["user"], session["csrfToken"]))


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


def verify_client(
    source: Path,
    detail: dict[str, object],
    directory: dict[str, object],
    client_class: type[httpx.Client] = httpx.Client,
) -> httpx.Client:
    case_id = str(detail["id"])

    def handler(request: httpx.Request) -> httpx.Response:
        auth_response = auth_route(request)
        if auth_response is not None:
            return auth_response
        if request.method == "GET":
            assert "x-csrf-token" not in request.headers
            assert "origin" not in request.headers
        if request.url.path == "/api/v2/cases":
            return httpx.Response(200, json={"data": [{"id": case_id, "projectNo": PROJECT}]})
        if request.url.path == f"/api/v2/cases/{case_id}":
            return httpx.Response(200, json=detail)
        if request.url.path == f"/api/v2/cases/{case_id}/directory":
            return httpx.Response(200, json=directory)
        if request.url.path == "/api/v2/files/file-id":
            return httpx.Response(200, content=source.read_bytes())
        raise AssertionError(request.url)

    return client_class(transport=httpx.MockTransport(handler))


def detail_for(data: dict[str, object]) -> dict[str, object]:
    products = data["initialInspection"]["products"]
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "projectNo": PROJECT,
        "brigade": {"code": "XISHAN"},
        "unitName": "合成单位",
        "inspectionForm": "ROUTINE",
        "notificationTarget": data["case"]["notificationTarget"],
        "inspections": [
            {
                "id": "initial-id",
                "stage": "INITIAL_CHECK",
                "inspectionDate": "2026-08-01",
                "products": [
                    {
                        "id": "product-id",
                        "name": product["name"],
                        "modelSpec": product.get("modelSpec"),
                    }
                    for product in products
                ],
            }
        ],
    }


def directory_file(source: Path) -> dict[str, object]:
    return {
        "id": "file-id",
        "sha256": file_sha256(source),
        "remoteState": "AVAILABLE",
        "nasVerifiedAt": "2026-08-10T00:00:00.000Z",
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


@pytest.mark.parametrize("work_relative", [".", "work", ".."])
def test_inventory_rejects_directory_source_and_work_dir_overlap(
    tmp_path: Path, work_relative: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    work = (
        source
        if work_relative == "."
        else tmp_path
        if work_relative == ".."
        else source / work_relative
    )
    with pytest.raises(RegistryError, match="不得重叠"):
        inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))


def test_compose_rejects_duplicate_source_relative_path_for_multiple_file_refs(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf(source_root / "one.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source_root), work_dir=str(work)))
    data = manifest(source_root / "one.pdf")
    data.pop("packageSha256")
    data["files"] = [
        {
            "clientRef": "file:one",
            "sourceRelativePath": "one.pdf",
            "relativePath": "normalized/one.pdf",
        },
        {
            "clientRef": "file:two",
            "sourceRelativePath": "one.pdf",
            "relativePath": "normalized/two.pdf",
        },
    ]
    data["otherAttachments"] = [
        {"clientRef": "attachment:one", "slotCode": "OTHER_ATTACHMENT", "fileRef": "file:one"},
        {"clientRef": "attachment:two", "slotCode": "OTHER_ATTACHMENT", "fileRef": "file:two"},
    ]
    case_data = tmp_path / "case.json"
    case_data.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RegistryError, match="不得供多个 fileRef 复用"):
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

    def successful_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        destination = Path(command[command.index("-Output") + 1])
        (destination / "result.md").write_text("识别结果", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(cli.subprocess, "run", successful_run)
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
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "ok", ""),
    )
    with pytest.raises(RegistryError, match="未生成非空 Markdown"):
        ocr_command(
            argparse.Namespace(
                work_dir=str(work),
                output_dir=str(work / "empty-ocr"),
                relative_path=["one.pdf"],
                timeout=10,
            )
        )
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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unitName", "错误单位", "unitName"),
        ("brigade", {"code": "BINHU"}, "案卷关键字段不一致"),
    ],
)
def test_verify_requires_exact_unit_and_brigade(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    detail = detail_for(data)
    detail[field] = value
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [directory_file(source)]}],
            }
        ]
    }
    with (
        verify_client(source, detail, directory) as client,
        pytest.raises(RegistryError, match=message),
    ):
        cli.verify_with_client(client, "https://registry.example", data)


def document_slot_data(source: Path, code: str) -> dict[str, object]:
    data = manifest(source)
    data["otherAttachments"] = []
    multiplicity, stage = cli.SLOT_META[code]
    slot: dict[str, object] = {
        "clientRef": "slot:one",
        "slotCode": code,
        "versions": [{"kind": "ELECTRONIC", "fileRef": "file:one"}],
    }
    if multiplicity == "INSPECTION":
        slot["inspectionRef"] = "initial"
    elif multiplicity == "PRODUCT":
        data["initialInspection"]["products"] = [
            {"clientRef": "product:one", "name": "产品", "modelSpec": "M"}
        ]
        slot["productRef"] = "product:one"
    elif multiplicity == "NOTIFICATION_TARGET":
        data["case"]["notificationTarget"] = "PRODUCTION"
        slot["notificationTarget"] = "PRODUCTION"
    else:
        assert multiplicity == "CASE" and stage is None
    data["documentSlots"] = [slot]
    return data


def slot_owner(data: dict[str, object], source: Path) -> dict[str, object]:
    slot = data["documentSlots"][0]
    multiplicity, _stage = cli.SLOT_META[slot["slotCode"]]
    owner: dict[str, object] = {"versions": {"ELECTRONIC": directory_file(source)}}
    if multiplicity == "INSPECTION":
        owner["inspectionId"] = "initial-id"
    elif multiplicity == "PRODUCT":
        owner["productId"] = "product-id"
    elif multiplicity == "NOTIFICATION_TARGET":
        owner["notificationTarget"] = "PRODUCTION"
    return owner


@pytest.mark.parametrize(
    "code",
    [
        "AUTHORIZATION_LETTER",
        "INITIAL_INSPECTION_RECORD",
        "INITIAL_CCC_CERTIFICATE",
        "ILLEGAL_PRODUCT_NOTIFICATION_LETTER",
    ],
)
def test_verify_rejects_ambiguous_fixed_slot_owner(tmp_path: Path, code: str) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = document_slot_data(source, code)
    owner = slot_owner(data, source)
    directory = {"rows": [{"slotKey": code, "children": [owner, owner.copy()]}]}
    with (
        verify_client(source, detail_for(data), directory) as client,
        pytest.raises(RegistryError, match="目录 owner 不唯一或缺失"),
    ):
        cli.verify_with_client(client, "https://registry.example", data)


@pytest.mark.parametrize("actual_kinds", [{"SCANNED"}, {"ELECTRONIC", "SCANNED"}])
def test_verify_requires_exact_remote_slot_version_kinds(
    tmp_path: Path, actual_kinds: set[str]
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    code = "AUTHORIZATION_LETTER"
    data = document_slot_data(source, code)
    owner = slot_owner(data, source)
    owner["versions"] = {kind: directory_file(source) for kind in actual_kinds}
    directory = {"rows": [{"slotKey": code, "children": [owner]}]}
    with (
        verify_client(source, detail_for(data), directory) as client,
        pytest.raises(RegistryError, match="目录版本种类不一致"),
    ):
        cli.verify_with_client(client, "https://registry.example", data)


def test_verify_uses_long_read_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path, map_path = tmp_path / "manifest.json", tmp_path / "map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [directory_file(source)]}],
            }
        ]
    }
    captured: dict[str, object] = {}
    real_client = httpx.Client

    def client_factory(**kwargs: object) -> httpx.Client:
        captured.update(kwargs)
        return verify_client(source, detail_for(data), directory, real_client)

    monkeypatch.setattr(cli.httpx, "Client", client_factory)
    verify_command(
        argparse.Namespace(
            manifest=str(manifest_path),
            upload_map=str(map_path),
            api_base="https://registry.example",
            auth_config=str(auth_config(tmp_path)),
            timeout=60.0,
        )
    )
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout) and timeout.read == 300.0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RegistryError("业务校验失败"), "ERROR: 业务校验失败"),
        (httpx.ReadTimeout("read timed out"), "ERROR: 网络传输失败，请检查连接后重试"),
    ],
)
def test_main_returns_single_line_error_for_business_and_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected: str,
) -> None:
    def fail(_args: argparse.Namespace) -> None:
        raise error

    monkeypatch.setattr(cli, "verify_command", fail)
    status = cli.main(
        [
            "verify",
            "--manifest",
            "unused.json",
            "--upload-map",
            "unused-map.json",
            "--api-base",
            "https://registry.example",
        ]
    )
    assert status == 2 and capsys.readouterr().err == expected + "\n"


def test_reference_v2_example_is_readable_and_valid_after_product_owner_fix() -> None:
    example = read_json(ROOT / "references" / "CaseImportManifestV2.example.json")
    assert example["schemaVersion"] == "CaseImportManifestV2"
    assert validate_manifest(example) == []


def test_finalized_unverified_retries_read_only_and_origin_is_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        auth_response = auth_route(request)
        if auth_response is not None:
            return auth_response
        if request.method == "GET":
            assert "x-csrf-token" not in request.headers
            assert "origin" not in request.headers
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            assert request.headers["origin"] == "https://registry.example"
            assert request.headers["x-csrf-token"] == TEST_CSRF
        if request.method == "POST" and request.url.path.startswith("/api/v2/"):
            posts.append(request.url.path)
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v2/cases":
            return httpx.Response(
                200,
                json={"data": [{"id": case_id, "projectNo": PROJECT}] if remote["case"] else []},
            )
        if request.url.path == "/api/v2/import-jobs":
            return httpx.Response(
                200,
                json={"id": "job", "packageHash": data["packageSha256"], "status": "CREATED"},
            )
        if request.url.path.endswith("/files"):
            return httpx.Response(
                201,
                json={
                    "jobId": "job",
                    "relativePath": "files/one.pdf",
                    "sha256": data["files"][0]["sha256"],
                    "mimeType": "application/pdf",
                    "sizeBytes": str(source.stat().st_size),
                },
            )
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json={"id": "job", "status": "MANIFEST_RECEIVED"})
        if request.url.path.endswith("/finalize"):
            remote["case"] = True
            return httpx.Response(
                200,
                json={
                    "caseId": case_id,
                    "created": True,
                    "added": {"products": 0, "slots": 0, "attachments": 1},
                    "replaced": {"slots": 0},
                    "conflicts": [],
                    "skipped": [],
                    "serverInternalField": "must-not-persist",
                },
            )
        if request.url.path == "/api/v2/import-jobs/job":
            return httpx.Response(
                200,
                json={
                    "id": "job",
                    "packageHash": data["packageSha256"],
                    "packageName": PROJECT,
                    "status": "FINALIZED",
                    "finalizedAt": "2026-08-10T00:00:00Z",
                    "case": {"projectNo": PROJECT, "brigade": {"routePath": "xishan"}},
                    "resultSummary": {
                        "caseId": case_id,
                        "created": True,
                        "added": {"products": 0, "slots": 0, "attachments": 1},
                        "replaced": {"slots": 0},
                        "conflicts": [],
                        "skipped": [],
                    },
                },
            )
        if request.url.path == f"/api/v2/cases/{case_id}":
            return httpx.Response(
                200,
                json={
                    "id": case_id,
                    "projectNo": PROJECT,
                    "brigade": {"code": "XISHAN"},
                    "unitName": "合成单位",
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
                            "children": [{"title": "附件", "files": [directory_file(source)]}],
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
        auth_config=str(auth_config(tmp_path)),
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
    assert "finalizeResult" not in state
    assert "serverInternalField" not in json.dumps(state)
    count = len(posts)
    remote["fail_verify"] = False
    upload_command(args)
    verified_state = read_json(tmp_path / "upload-state.json")
    assert len(posts) == count and verified_state["status"] == "VERIFIED"
    upload_command(args)
    rerun_state = read_json(tmp_path / "upload-state.json")
    assert len(posts) == count and rerun_state["status"] == "VERIFIED"
    assert set(verified_state["authIdentity"]) == {"digest", "role", "brigadeCode"}
    output = capsys.readouterr().out
    assert "manifestSha256" not in output and "serverInternalField" not in output
    assert TEST_USER_ID not in output and TEST_CSRF not in output
    with pytest.raises(RegistryError, match="origin"):
        upload_command(argparse.Namespace(**{**vars(args), "api_base": "https://other.example"}))
    verify_command(
        argparse.Namespace(
            manifest=str(manifest_path),
            upload_map=str(map_path),
            api_base="https://registry.example",
            auth_config=str(auth_config(tmp_path)),
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
            "stateVersion": 5,
            "status": "UPLOADING",
            "origin": origin,
            "manifestSha256": file_sha256(manifest_path),
            "packageSha256": data["packageSha256"],
            "projectNo": PROJECT,
            "jobId": "job",
            "authIdentity": admin_state_identity(),
            "uploadedFileRefs": ["file:one"],
        },
    )
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_response = auth_route(request)
        if auth_response is not None:
            return auth_response
        if request.method == "GET":
            assert "x-csrf-token" not in request.headers
            assert "origin" not in request.headers
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            assert request.headers["origin"] == "https://registry.example"
            assert request.headers["x-csrf-token"] == TEST_CSRF
        if request.method == "POST" and request.url.path.startswith("/api/v2/"):
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
                    "unitName": "合成单位",
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
                            "children": [{"title": "附件", "files": [directory_file(source)]}],
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
    with pytest.raises(RegistryError, match="旧 V5"):
        upload_command(
            argparse.Namespace(
                manifest=str(manifest_path),
                upload_map=str(map_path),
                api_base=origin,
                auth_config=str(auth_config(tmp_path)),
                timeout=10.0,
                dry_run=False,
                finalize=True,
            )
        )
    assert posts == []
    assert read_json(tmp_path / "upload-state.json")["status"] == "UPLOADING"


@pytest.mark.parametrize("job_status", ["CREATED", "UPLOADING", "MANIFEST_RECEIVED"])
def test_upload_resumes_all_active_states_with_case_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_status: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path = tmp_path / "manifest.json"
    map_path = tmp_path / "map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    projection = cli.files_projection(data, {"file:one": str(source)})
    state = {
        "stateVersion": 6,
        "status": "UPLOADING",
        "origin": "https://registry.example",
        "manifestSha256": cli.file_sha256(manifest_path),
        "packageSha256": data["packageSha256"],
        "projectNo": PROJECT,
        "brigadeCode": "XISHAN",
        "jobId": "job",
        "authIdentity": admin_state_identity(),
        "filesProjection": projection,
        "immutableBindingDigest": cli.immutable_manifest_binding(data, projection),
        "uploadedFileRefs": [],
    }
    cli.write_json(tmp_path / "upload-state.json", state)
    final_job = {
        "id": "job",
        "packageHash": data["packageSha256"],
        "packageName": PROJECT,
        "status": "FINALIZED",
        "finalizedAt": "2026-08-10T00:00:00Z",
        "case": {"projectNo": PROJECT, "brigade": {"routePath": "xishan"}},
        "resultSummary": {
            "caseId": "11111111-1111-4111-8111-111111111111",
            "created": True,
            "added": {"products": 0, "slots": 0, "attachments": 1},
            "replaced": {"slots": 0},
            "conflicts": [],
            "skipped": [],
        },
    }
    jobs = iter(
        [
            {
                "id": "job",
                "packageHash": data["packageSha256"],
                "status": job_status,
                **({"packageName": PROJECT} if job_status == "MANIFEST_RECEIVED" else {}),
                "case": None,
            },
            final_job,
        ]
    )
    monkeypatch.setattr(cli, "get_import_job", lambda *_args, **_kwargs: next(jobs))
    monkeypatch.setattr(
        cli,
        "verify_with_poll",
        lambda *_args, **_kwargs: {
            "caseId": "11111111-1111-4111-8111-111111111111",
            "inspections": 1,
            "products": 0,
            "filesVerified": 1,
        },
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_response = auth_route(request)
        if auth_response is not None:
            return auth_response
        requests.append(request.url.path)
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.method == "POST" and request.url.path.endswith("/files"):
            return httpx.Response(
                201,
                json={
                    "jobId": "job",
                    "relativePath": "files/one.pdf",
                    "sha256": data["files"][0]["sha256"],
                    "mimeType": "application/pdf",
                    "sizeBytes": str(source.stat().st_size),
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/manifest"):
            return httpx.Response(200, json={"id": "job", "status": "MANIFEST_RECEIVED"})
        if request.method == "POST" and request.url.path.endswith("/finalize"):
            return httpx.Response(200, json=final_job["resultSummary"])
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
            api_base="https://registry.example",
            auth_config=str(auth_config(tmp_path)),
            timeout=10.0,
            dry_run=False,
            finalize=True,
        )
    )
    assert "/api/v2/import-jobs" not in requests
    assert requests.count("/api/v2/import-jobs/job/files") == 1
    assert requests.count("/api/v2/import-jobs/job/manifest") == 1
    assert requests.count("/api/v2/import-jobs/job/finalize") == 1
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


def test_auth_config_uses_toml_and_rejects_empty_values(tmp_path: Path) -> None:
    path = auth_config(tmp_path)
    assert cli.read_auth_config(path) == ("fixture-admin", "fixture-password-not-real")
    path.write_text('[auth]\nusername = ""\npassword = ""\n', encoding="utf-8")
    with pytest.raises(RegistryError, match="username"):
        cli.read_auth_config(path)


@pytest.mark.parametrize(
    ("session", "message"),
    [
        (auth_session(must_change_password=True), "必须显式为 false"),
        (
            auth_session(
                role="BRIGADE",
                brigade_id="33333333-3333-4333-8333-333333333333",
                brigade_code="BINHU",
            ),
            "brigadeCode 不一致",
        ),
    ],
)
def test_authentication_stops_for_password_change_or_wrong_brigade(
    tmp_path: Path, session: dict[str, object], message: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)

    def handler(request: httpx.Request) -> httpx.Response:
        response = auth_route(request, session)
        if response is None:
            raise AssertionError(request.url)
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match=message),
    ):
        cli.authenticate_client(
            client,
            "https://registry.example",
            "https://registry.example",
            manifest(source),
            auth_config(tmp_path),
        )


def test_matching_brigade_account_is_accepted_and_csrf_is_kept_in_memory(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    session = auth_session(
        role="BRIGADE",
        brigade_id="33333333-3333-4333-8333-333333333333",
        brigade_code="XISHAN",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = auth_route(request, session)
        if response is None:
            raise AssertionError(request.url)
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        identity, write_headers = cli.authenticate_client(
            client,
            "https://registry.example",
            "https://registry.example",
            manifest(source),
            auth_config(tmp_path),
        )
        assert identity == {
            "digest": identity["digest"],
            "role": "BRIGADE",
            "brigadeCode": "XISHAN",
        }
        assert identity["digest"].startswith("sha256:")
        assert "Origin" not in client.headers and "X-CSRF-Token" not in client.headers
        assert write_headers["Origin"] == "https://registry.example"
        assert write_headers["X-CSRF-Token"] == TEST_CSRF


def test_upload_state_rejects_identity_switch_and_legacy_unbound_state() -> None:
    identity = admin_state_identity()
    with pytest.raises(RegistryError, match="未绑定认证身份"):
        cli.require_same_state_identity({"status": "UPLOADING"}, identity)
    switched = {**identity, "digest": "sha256:" + "4" * 64}
    with pytest.raises(RegistryError, match="禁止切换身份续传"):
        cli.require_same_state_identity({"authIdentity": identity}, switched)


def test_http_error_does_not_echo_response_body() -> None:
    response = httpx.Response(403, text="password=do-not-log; csrf=do-not-log")
    with pytest.raises(RegistryError) as raised:
        cli.response_json(response, "认证")
    assert str(raised.value) == "认证 失败：HTTP 403"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "manifest 的 repairSiteId 必须为 UUID 或 null"},
        {"errors": ["文件哈希不一致", "槽位引用缺失"]},
    ],
)
def test_http_error_allows_short_json_validation_hint(payload: dict[str, object]) -> None:
    response = httpx.Response(400, json=payload)
    with pytest.raises(RegistryError) as raised:
        cli.response_json(response, "提交清单")
    assert "HTTP 400" in str(raised.value)
    assert "repairSiteId" in str(raised.value) or "槽位引用缺失" in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "x" * 241},
        {"message": {"nested": "not allowed"}},
        {"message": "C:\\private\\secret.txt"},
        {"stack": "trace must not leak", "message": "bad"},
    ],
)
def test_http_error_rejects_unsafe_json_error_details(payload: dict[str, object]) -> None:
    response = httpx.Response(400, json=payload)
    with pytest.raises(RegistryError) as raised:
        cli.response_json(response, "提交清单")
    assert str(raised.value) == "提交清单 失败：HTTP 400"


def test_parser_defaults_to_stable_local_auth_config() -> None:
    args = cli.build_parser().parse_args(
        [
            "verify",
            "--manifest",
            "manifest.json",
            "--upload-map",
            "upload-map.json",
            "--api-base",
            "https://registry.example",
        ]
    )
    assert Path(args.auth_config) == cli.DEFAULT_AUTH_CONFIG


def test_real_upload_cli_requires_configured_case_workspace_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path = tmp_path / "manifest.json"
    map_path = tmp_path / "upload-map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    args = cli.build_parser().parse_args(
        [
            "upload",
            "--manifest",
            str(manifest_path),
            "--upload-map",
            str(map_path),
            "--api-base",
            "https://registry.example",
            "--workspace-config",
            str(tmp_path / "missing-workspace.toml"),
            "--finalize",
        ]
    )

    def unexpected_client(**_kwargs: object) -> None:
        raise AssertionError("工作根门禁必须早于网络客户端")

    monkeypatch.setattr(cli.httpx, "Client", unexpected_client)
    with pytest.raises(RegistryError, match="未配置工作根"):
        upload_command(args)


def test_real_upload_cli_rejects_manifest_outside_selected_case_workspace_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    manifest_path = tmp_path / "legacy" / "manifest.json"
    manifest_path.parent.mkdir()
    map_path = manifest_path.parent / "upload-map.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    map_path.write_text(json.dumps({"files": {"file:one": str(source)}}), encoding="utf-8")
    args = cli.build_parser().parse_args(
        [
            "upload",
            "--manifest",
            str(manifest_path),
            "--upload-map",
            str(map_path),
            "--api-base",
            "https://registry.example",
            "--workspace-config",
            str(config_path),
            "--finalize",
        ]
    )

    def unexpected_client(**_kwargs: object) -> None:
        raise AssertionError("越界清单必须在网络前被拒绝")

    monkeypatch.setattr(cli.httpx, "Client", unexpected_client)
    with pytest.raises(RegistryError, match="manifest 不在选定工作根"):
        upload_command(args)


def test_real_compose_cli_updates_case_waterline_to_pending_upload(tmp_path: Path) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    config = workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    layout = workspace.BusinessLayout.from_root(config.work_root)
    pending = layout.pending_case_dir(PROJECT)
    pending.mkdir(parents=True)
    source = pending / "one.pdf"
    pdf(source)
    work = layout.work_case_dir(PROJECT)
    inventory_args = cli.build_parser().parse_args(
        [
            "inventory",
            str(pending),
            "--work-dir",
            str(work),
            "--workspace-config",
            str(config_path),
        ]
    )
    inventory_command(inventory_args)
    record = workspace.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "PENDING_ORGANIZATION"
    assert record["local"]["status"] == "INVENTORIED"

    data = manifest(source)
    data.pop("packageSha256")
    data["files"][0].update({"sourceRelativePath": "one.pdf", "relativePath": "normalized/one.pdf"})
    case_data = work / "case-data.json"
    case_data.write_text(json.dumps(data), encoding="utf-8")
    compose_args = cli.build_parser().parse_args(
        [
            "compose",
            "--work-dir",
            str(work),
            "--case-data",
            str(case_data),
            "--workspace-config",
            str(config_path),
        ]
    )
    compose_command(compose_args)
    record = workspace.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "PENDING_UPLOAD"
    assert record["local"]["status"] == "READY_FOR_UPLOAD"


def test_real_inventory_cli_rejects_originals_outside_pending_case(tmp_path: Path) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    config = workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    layout = workspace.BusinessLayout.from_root(config.work_root)
    source = tmp_path / "external-source"
    source.mkdir()
    pdf(source / "one.pdf")
    work = layout.work_case_dir(PROJECT)
    args = cli.build_parser().parse_args(
        [
            "inventory",
            str(source),
            "--work-dir",
            str(work),
            "--workspace-config",
            str(config_path),
        ]
    )
    with pytest.raises(RegistryError, match="原始案卷/待处理案卷"):
        inventory_command(args)
    assert not (work / "inventory.json").exists()


@pytest.mark.parametrize(
    ("message", "waiting"),
    [
        ("飞牛落盘核验中：file:one", True),
        ("飞牛正式库暂不可用：file:one", True),
        ("文件取回等待超时；取回任务仍由服务端保留", True),
        ("目录 SHA-256 不一致：file:one", False),
        ("下载 SHA-256 不一致：file:one", False),
    ],
)
def test_verification_error_classification_distinguishes_waiting_from_real_fault(
    message: str, waiting: bool
) -> None:
    assert cli.verification_error_is_waiting(RegistryError(message)) is waiting


def test_verified_waterline_stops_at_pending_archive_until_archive_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def capture_update(*_args: object, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(cli, "update_case_waterline", capture_update)
    cli.mark_verified_pending_archive(
        argparse.Namespace(),
        tmp_path / "manifest.json",
        {"case": {"projectNo": PROJECT}},
        {
            "status": "VERIFIED",
            "finalizedAt": "2026-08-21T00:00:00Z",
            "verification": {"caseId": "case-id", "filesVerified": 1},
        },
    )
    assert captured["state"] == "VERIFIED_PENDING_ARCHIVE"
    assert captured["upload"]["status"] == "VERIFIED"
    assert captured["nas_verification"]["status"] == "VERIFIED"
    fields = cli.archive_result_fields(
        {"status": "COMPLETED", "waterlineXlsxError": "Excel 正在被占用"}
    )
    assert "waterlineXlsxError" in fields["archive"]
    assert "Excel 水位表导出失败" in fields["warning"]


def test_source_attach_package_parser_accepts_download_baseline_override() -> None:
    args = cli.build_parser().parse_args(
        [
            "source",
            "attach-package",
            "--batch-id",
            "batch-1",
            "--rwid",
            "fixture-rwid",
            "--download",
            "downloads",
            "--download-baseline",
            "baseline.json",
        ]
    )
    assert args.download_baseline == "baseline.json"


def test_source_begin_parser_supports_isolated_acceptance_sample() -> None:
    args = cli.build_parser().parse_args(
        [
            "source",
            "begin",
            "--origin",
            "https://source.example/cases",
            "--acceptance-sample",
        ]
    )
    assert args.acceptance_sample is True


def test_source_attach_package_parser_requires_per_case_download_baseline() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "attach-package",
                "--batch-id",
                "batch-1",
                "--rwid",
                "fixture-rwid",
                "--download",
                "download.zip",
            ]
        )


def test_source_cli_requires_page_screenshot_detail_url_and_snapshot_rwid() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "add-page",
                "--batch-id",
                "batch-1",
                "--page-json",
                "page.json",
            ]
        )
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "add-detail",
                "--batch-id",
                "batch-1",
                "--rwid",
                "fixture-rwid",
                "--detail-json",
                "detail.json",
                "--screenshot",
                "detail.png",
            ]
        )
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["source", "snapshot-downloads", "--batch-id", "batch-1"])


def test_source_snapshot_downloads_persists_per_case_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    begin_args = cli.build_parser().parse_args(
        [
            "source",
            "begin",
            "--batch-id",
            "batch-1",
            "--origin",
            "http://registry-source.example/#/xfjd/list?runId=secret",
            "--workspace-config",
            str(config_path),
        ]
    )
    cli.source_begin_command(begin_args)
    capsys.readouterr()
    layout = workspace.BusinessLayout.from_root(root)
    record = {
        "RWID": "fixture-rwid",
        "项目编号": PROJECT,
        "单位名称": "测试单位",
    }
    for round_no in (1, 2):
        source.add_page(
            layout,
            "batch-1",
            1,
            [record],
            1,
            1,
            round_no=round_no,
        )
        source.finalize_capture(layout, "batch-1")
    (downloads / "existing.zip").write_bytes(b"baseline fixture")
    snapshot_args = cli.build_parser().parse_args(
        [
            "source",
            "snapshot-downloads",
            "--batch-id",
            "batch-1",
            "--rwid",
            "fixture-rwid",
            "--workspace-config",
            str(config_path),
        ]
    )
    cli.source_snapshot_downloads_command(snapshot_args)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "captured"
    assert output["fileCount"] == 1
    assert output["rwid"] == "fixture-rwid"
    assert output["projectNo"] == PROJECT
    assert Path(output["path"]).is_file()
    persisted = json.loads(Path(output["path"]).read_text(encoding="utf-8"))
    assert persisted["batchId"] == "batch-1"
    assert persisted["rwid"] == "fixture-rwid"
    assert persisted["projectNo"] == PROJECT
    assert persisted["consumedAt"] is None


def test_default_auth_config_uses_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cli.default_auth_config_path() == (
        tmp_path / "xf-product-case-registry" / "admin-upload-config.toml"
    )


def test_verify_help_displays_windows_default_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["verify", "--help"])
    assert raised.value.code == 0
    output = "".join(capsys.readouterr().out.split())
    assert "%LOCALAPPDATA%/xf-product-case-registry/admin-upload-config.toml" in output


def test_auth_config_requires_absolute_non_reparse_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RegistryError, match="绝对路径"):
        cli.secure_auth_config_path(Path("relative-auth.toml"))
    link = tmp_path / "linked"
    link.mkdir()
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate == link or real_is_symlink(candidate),
    )
    with pytest.raises(RegistryError, match="重解析点"):
        cli.secure_auth_config_path(link / "admin-upload-config.toml")


def test_init_auth_config_creates_once_and_secures_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "local" / "admin-upload-config.toml"
    secured: list[Path] = []
    monkeypatch.setattr(cli, "restrict_auth_config_acl", lambda path: secured.append(path))
    args = argparse.Namespace(auth_config=str(target))
    cli.init_auth_config_command(args)
    assert target.read_text(encoding="utf-8") == cli.AUTH_CONFIG_TEMPLATE
    assert secured == [target]
    target.write_text('[auth]\nusername = "kept"\npassword = "kept"\n', encoding="utf-8")
    cli.init_auth_config_command(args)
    assert 'username = "kept"' in target.read_text(encoding="utf-8")
    assert secured == [target, target]
    output = capsys.readouterr().out
    assert "kept" not in output and "password" not in output


def test_init_auth_config_removes_new_file_when_acl_hardening_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "local" / "admin-upload-config.toml"

    def fail_acl(_path: Path) -> None:
        raise RegistryError("ACL failed")

    monkeypatch.setattr(cli, "restrict_auth_config_acl", fail_acl)
    with pytest.raises(RegistryError, match="ACL failed"):
        cli.init_auth_config_command(argparse.Namespace(auth_config=str(target)))
    assert not target.exists()


def test_acl_hardening_grants_only_current_user_and_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "admin-upload-config.toml"
    target.write_text(cli.AUTH_CONFIG_TEMPLATE, encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[0] == "whoami.exe":
            return subprocess.CompletedProcess(command, 0, b'"fixture","S-1-5-21-123"\r\n', b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.restrict_auth_config_acl(target)
    assert calls[1] == [
        "icacls.exe",
        str(target),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-21-123:(R,W)",
        "*S-1-5-18:(F)",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["user"].pop("authMethod"), "authMethod"),
        (lambda payload: payload["user"].pop("mustChangePassword"), "显式为 false"),
        (lambda payload: payload["user"].update({"csrfToken": "wrong"}), "CSRF"),
        (
            lambda payload: payload["user"].update(
                {
                    "brigadeId": "33333333-3333-4333-8333-333333333333",
                    "brigadeCode": "XISHAN",
                    "brigade": {
                        "id": "33333333-3333-4333-8333-333333333333",
                        "code": "XISHAN",
                    },
                }
            ),
            "ADMIN 账户不得绑定大队",
        ),
    ],
)
def test_authentication_strictly_rejects_malformed_user_contract(
    tmp_path: Path, mutate: Any, message: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    session = auth_session()
    mutate(session)

    def handler(request: httpx.Request) -> httpx.Response:
        response = auth_route(request, session)
        if response is None:
            raise AssertionError(request.url)
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match=message),
    ):
        cli.authenticate_client(
            client,
            "https://registry.example",
            "https://registry.example",
            manifest(source),
            auth_config(tmp_path),
        )


def test_authentication_rejects_missing_cookie_and_login_session_identity_mismatch(
    tmp_path: Path,
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    config = auth_config(tmp_path)
    login, session = auth_session(), auth_session()
    session["user"]["id"] = "55555555-5555-4555-8555-555555555555"

    def mismatch_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json=login,
                headers={"Set-Cookie": f"{cli.SESSION_COOKIE_NAME}=fixture; Secure; Path=/"},
            )
        if request.url.path == "/api/auth/session":
            return httpx.Response(200, json=session)
        raise AssertionError(request.url)

    with (
        httpx.Client(transport=httpx.MockTransport(mismatch_handler)) as client,
        pytest.raises(RegistryError, match="身份不一致"),
    ):
        cli.authenticate_client(
            client, "https://registry.example", "https://registry.example", manifest(source), config
        )

    def no_cookie_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=login)

    with (
        httpx.Client(transport=httpx.MockTransport(no_cookie_handler)) as client,
        pytest.raises(RegistryError, match="Cookie"),
    ):
        cli.authenticate_client(
            client, "https://registry.example", "https://registry.example", manifest(source), config
        )


def test_authentication_rejects_flat_nested_brigade_mismatch_and_missing_expiry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    config = auth_config(tmp_path)
    session = auth_session(
        role="BRIGADE",
        brigade_id="33333333-3333-4333-8333-333333333333",
        brigade_code="XISHAN",
    )
    session["user"]["brigade"]["code"] = "BINHU"

    def mismatch_handler(request: httpx.Request) -> httpx.Response:
        response = auth_route(request, session)
        if response is None:
            raise AssertionError(request.url)
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(mismatch_handler)) as client,
        pytest.raises(RegistryError, match="平铺与嵌套"),
    ):
        cli.authenticate_client(
            client, "https://registry.example", "https://registry.example", manifest(source), config
        )

    missing_expiry = auth_session()
    missing_expiry.pop("absoluteExpiresAt")

    def expiry_handler(request: httpx.Request) -> httpx.Response:
        response = auth_route(request, missing_expiry)
        if response is None:
            raise AssertionError(request.url)
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(expiry_handler)) as client,
        pytest.raises(RegistryError, match="到期时间"),
    ):
        cli.authenticate_client(
            client, "https://registry.example", "https://registry.example", manifest(source), config
        )


def test_state_v5_is_closed_and_stores_only_identity_digest() -> None:
    state = {
        "stateVersion": 5,
        "status": "UPLOADING",
        "origin": "https://registry.example",
        "manifestSha256": "sha256:" + "1" * 64,
        "packageSha256": "sha256:" + "2" * 64,
        "projectNo": PROJECT,
        "jobId": "job",
        "authIdentity": admin_state_identity(),
        "uploadedFileRefs": [],
    }
    serialized = json.dumps(state)
    assert (
        TEST_USER_ID not in serialized
        and "brigadeId" not in serialized
        and "userId" not in serialized
    )
    with pytest.raises(RegistryError, match="旧 V5"):
        cli.validate_upload_state(state)


def test_state_v6_requires_file_projection_and_target_binding() -> None:
    state = {
        "stateVersion": 6,
        "status": "UPLOADING",
        "origin": "https://registry.example",
        "manifestSha256": "sha256:" + "1" * 64,
        "packageSha256": "sha256:" + "2" * 64,
        "projectNo": PROJECT,
        "brigadeCode": "XISHAN",
        "jobId": "job",
        "authIdentity": {"digest": "sha256:" + "3" * 64, "role": "ADMIN", "brigadeCode": None},
        "immutableBindingDigest": "sha256:" + "5" * 64,
        "filesProjection": [
            {
                "clientRef": "file:one",
                "relativePath": "files/one.pdf",
                "sha256": "sha256:" + "4" * 64,
                "mimeType": "application/pdf",
                "pageCount": 1,
                "sizeBytes": 1,
            }
        ],
        "uploadedFileRefs": [],
    }
    cli.validate_upload_state(state)
    with pytest.raises(RegistryError, match="文件投影"):
        cli.validate_upload_state({**state, "filesProjection": []})

    with pytest.raises(RegistryError, match="文件投影"):
        cli.validate_upload_state(
            {
                **state,
                "filesProjection": [
                    {
                        **state["filesProjection"][0],
                        "relativePath": "folder/../one.pdf",
                    }
                ],
            }
        )


def test_files_projection_fills_optional_page_count_and_size(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source, pages=2)
    data = manifest(source)
    data["files"][0].pop("pageCount")
    projection = cli.files_projection(data, {"file:one": str(source)})
    assert projection == [
        {
            "clientRef": "file:one",
            "relativePath": "files/one.pdf",
            "sha256": file_sha256(source),
            "mimeType": "application/pdf",
            "pageCount": 2,
            "sizeBytes": source.stat().st_size,
        }
    ]


def test_immutable_binding_supports_electronic_and_scanned_versions(tmp_path: Path) -> None:
    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    pdf(first)
    pdf(second, pages=2)
    data = manifest(first)
    data["files"].append(
        {
            "clientRef": "file:two",
            "relativePath": "files/two.pdf",
            "sha256": file_sha256(second),
            "mimeType": "application/pdf",
        }
    )
    data["otherAttachments"] = []
    data["documentSlots"] = [
        {
            "clientRef": "slot:authorization",
            "slotCode": "AUTHORIZATION_LETTER",
            "versions": [
                {"kind": "ELECTRONIC", "fileRef": "file:one"},
                {"kind": "SCANNED", "fileRef": "file:two"},
            ],
        }
    ]
    upload = {"file:one": str(first), "file:two": str(second)}
    assert validate_manifest(data, upload) == []
    projection = cli.files_projection(data, upload)
    digest = cli.immutable_manifest_binding(data, projection)
    assert cli.SHA256.fullmatch(digest)


@pytest.mark.parametrize("status", ["CREATED", "UPLOADING", "MANIFEST_RECEIVED"])
def test_get_import_job_allows_active_production_case_null(status: str) -> None:
    package_sha = "sha256:" + "a" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "job",
                "packageHash": package_sha,
                "status": status,
                **({"packageName": PROJECT} if status == "MANIFEST_RECEIVED" else {}),
                "case": None,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = cli.get_import_job(
            client, "https://registry.example", "job", package_sha, PROJECT, "XISHAN"
        )
    assert result["status"] == status


@pytest.mark.parametrize(
    "response, expected",
    [
        (
            httpx.Response(
                200, json={"id": "job", "packageHash": "sha256:" + "a" * 64, "status": "FAILED"}
            ),
            "FAILED",
        ),
        (httpx.Response(404, text="not found"), "不存在"),
    ],
)
def test_get_import_job_failed_or_missing_stops_without_guessing(
    response: httpx.Response, expected: str
) -> None:
    package_sha = "sha256:" + "a" * 64

    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match=expected),
    ):
        cli.get_import_job(
            client, "https://registry.example", "job", package_sha, PROJECT, "XISHAN"
        )


def test_response_error_code_reads_bounded_unread_stream() -> None:
    class ErrorStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"code":"RECALL_REQUIRED","message":"do not echo"}'

    response = httpx.Response(
        409,
        headers={"content-type": "application/json"},
        stream=ErrorStream(),
    )
    assert cli.response_error_code(response) == "RECALL_REQUIRED"
    response.close()

    class HugeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{" + b"x" * (cli.MAX_ERROR_RESPONSE_BYTES + 1)

    huge = httpx.Response(
        409,
        headers={"content-type": "application/json"},
        stream=HugeStream(),
    )
    assert cli.response_error_code(huge) is None
    huge.close()


def test_repair_site_id_contract_is_preserved_and_enforced(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    valid = manifest(source)
    valid["initialInspection"]["products"] = [
        {
            "clientRef": "product:one",
            "name": "产品",
            "maintenance": "YES",
            "repairSiteId": "11111111-1111-4111-8111-111111111111",
        }
    ]
    assert validate_manifest(valid, {"file:one": str(source)}) == []

    invalid_uuid = json.loads(json.dumps(valid))
    invalid_uuid["initialInspection"]["products"][0]["repairSiteId"] = "not-a-uuid"
    assert any("repairSiteId" in error for error in validate_manifest(invalid_uuid))

    invalid_maintenance = json.loads(json.dumps(valid))
    invalid_maintenance["initialInspection"]["products"][0]["maintenance"] = "NO"
    assert any("maintenance" in error for error in validate_manifest(invalid_maintenance))

    no_repair_site = json.loads(json.dumps(valid))
    no_repair_site["initialInspection"]["products"][0]["repairSiteId"] = None
    no_repair_site["initialInspection"]["products"][0]["maintenance"] = "UNKNOWN"
    assert validate_manifest(no_repair_site, {"file:one": str(source)}) == []

    uppercase_uuid = json.loads(json.dumps(valid))
    uppercase_uuid["initialInspection"]["products"][0]["repairSiteId"] = (
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    )
    assert any("repairSiteId" in error for error in validate_manifest(uppercase_uuid))


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", " 产品"),
        ("modelSpec", " "),
        ("nominalProducer", "生产企业 "),
        ("location", "位置" + "x" * 300),
        ("problemDescription", "问题" + "x" * 2000),
    ],
)
def test_product_text_fields_are_trimmed_nonempty_and_bounded(
    tmp_path: Path, field: str, value: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    product = {"clientRef": "product:one", "name": "产品"}
    data["initialInspection"]["products"] = [product]
    product[field] = value
    errors = validate_manifest(data)
    assert any(field in error for error in errors)


@pytest.mark.parametrize(
    "created, conflict_count, skipped_count",
    [(True, 1, 0), (True, 0, 1), (False, 0, 0)],
)
def test_finalize_conflicts_are_closed_non_verified_state(
    created: bool, conflict_count: int, skipped_count: int
) -> None:
    state = {
        "stateVersion": 6,
        "status": "FINALIZED_WITH_CONFLICTS",
        "origin": "https://registry.example",
        "manifestSha256": "sha256:" + "1" * 64,
        "packageSha256": "sha256:" + "2" * 64,
        "projectNo": PROJECT,
        "brigadeCode": "XISHAN",
        "jobId": "job",
        "authIdentity": admin_state_identity(),
        "immutableBindingDigest": "sha256:" + "5" * 64,
        "filesProjection": [
            {
                "clientRef": "file:one",
                "relativePath": "files/one.pdf",
                "sha256": "sha256:" + "4" * 64,
                "mimeType": "application/pdf",
                "pageCount": 1,
                "sizeBytes": 1,
            }
        ],
        "uploadedFileRefs": [],
        "caseId": "11111111-1111-4111-8111-111111111111",
        "finalizedAt": "2026-08-10T00:00:00Z",
        "finalizeSummary": {
            "caseId": "11111111-1111-4111-8111-111111111111",
            "created": created,
            "addedProducts": 0,
            "addedSlots": 0,
            "addedAttachments": 0,
            "replacedSlots": 0,
            "conflictCount": conflict_count,
            "skippedCount": skipped_count,
        },
    }
    cli.validate_upload_state(state)
    with pytest.raises(RegistryError, match="不得进入 VERIFIED"):
        cli.validate_upload_state(
            {**state, "status": "VERIFIED", "verification": {}, "verifiedAt": "now"}
        )


def test_one_untitled_attachment_is_allowed_but_multiple_are_not(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["otherAttachments"][0].pop("title")
    assert validate_manifest(data, {"file:one": str(source)}) == []
    second = json.loads(json.dumps(data["otherAttachments"][0]))
    second["clientRef"] = "attachment:two"
    data["otherAttachments"].append(second)
    assert any("多个无 title" in error for error in validate_manifest(data))
    titled = manifest(source)
    titled["otherAttachments"][0]["title"] = " 附件"
    assert any("title" in error for error in validate_manifest(titled))
    titled["otherAttachments"][0]["title"] = "x" * 301
    assert any("title" in error for error in validate_manifest(titled))


def test_verify_recalls_remote_only_file_before_hash_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    detail = detail_for(data)
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [directory_file(source)]}],
            }
        ]
    }
    statuses = iter(["PENDING", "PROCESSING", "READY"])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_response = auth_route(request)
        if auth_response is not None:
            return auth_response
        seen.append(request)
        if request.url.path == "/api/v2/cases":
            return httpx.Response(200, json={"data": [{"id": detail["id"], "projectNo": PROJECT}]})
        if request.url.path == f"/api/v2/cases/{detail['id']}":
            return httpx.Response(200, json=detail)
        if request.url.path == f"/api/v2/cases/{detail['id']}/directory":
            return httpx.Response(200, json=directory)
        if request.url.path == "/api/v2/files/file-id":
            if sum(1 for item in seen if item.url.path == request.url.path) == 1:
                return httpx.Response(409, json={"code": "RECALL_REQUIRED", "message": "secret"})
            return httpx.Response(200, content=source.read_bytes())
        if request.url.path == "/api/v2/files/file-id/recall":
            assert request.method == "POST"
            assert request.headers["origin"] == "https://registry.example"
            assert request.headers["x-product-case-client"] == "web-v2"
            assert request.headers["x-csrf-token"] == TEST_CSRF
            return httpx.Response(
                200, json={"recallId": "recall-1", "fileId": "file-id", "status": "PENDING"}
            )
        if request.url.path == "/api/v2/file-recalls/recall-1":
            assert request.method == "GET"
            assert "x-csrf-token" not in request.headers
            return httpx.Response(
                200, json={"recallId": "recall-1", "fileId": "file-id", "status": next(statuses)}
            )
        raise AssertionError(request.url)

    monkeypatch.setattr(cli, "RECALL_POLL_INTERVAL_SECONDS", 0.0)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = cli.verify_with_client(
            client,
            "https://registry.example",
            data,
            {
                "Origin": "https://registry.example",
                "X-Product-Case-Client": "web-v2",
                "X-CSRF-Token": TEST_CSRF,
            },
            True,
        )
    assert result["filesVerified"] == 1


def test_default_verify_stops_on_missing_flynn_landed_evidence(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [
                    {
                        "title": "附件",
                        "files": [
                            {
                                "id": "file-id",
                                "sha256": file_sha256(source),
                                "remoteState": "PENDING",
                                "nasVerifiedAt": None,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    with (
        verify_client(source, detail_for(data), directory) as client,
        pytest.raises(RegistryError, match="飞牛落盘核验中"),
    ):
        cli.verify_with_client(client, "https://registry.example", data)


def test_verify_allows_server_derived_empty_recheck_for_initial_failure(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["initialInspection"]["products"] = [
        {
            "clientRef": "product:one",
            "name": "产品",
            "method": "ONSITE",
            "result": "UNQUALIFIED",
            "problemDescription": "问题",
        }
    ]
    assert validate_manifest(data, {"file:one": str(source)}) == []
    detail = detail_for(data)
    detail["inspections"].append({"id": "derived-recheck", "stage": "RECHECK", "products": []})
    detail["inspections"][0]["products"][0].update(
        {"method": "ONSITE", "result": "UNQUALIFIED", "problemDescription": "问题"}
    )
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [directory_file(source)]}],
            }
        ]
    }
    with verify_client(source, detail, directory) as client:
        result = cli.verify_with_client(client, "https://registry.example", data)
    assert result["inspections"] == 2


def test_verify_uses_effective_reinspection_result_for_derived_recheck(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["initialInspection"]["products"] = [
        {
            "clientRef": "product:one",
            "name": "产品",
            "method": "SAMPLING",
            "result": "QUALIFIED",
            "reinspectionApplied": "YES",
            "reinspectionResult": "UNQUALIFIED",
        }
    ]
    detail = detail_for(data)
    detail["inspections"].append({"id": "derived-recheck", "stage": "RECHECK", "products": []})
    detail["inspections"][0]["products"][0].update(
        {
            "method": "SAMPLING",
            "result": "QUALIFIED",
            "reinspectionApplied": "YES",
            "reinspectionResult": "UNQUALIFIED",
        }
    )
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [directory_file(source)]}],
            }
        ]
    }
    with verify_client(source, detail, directory) as client:
        result = cli.verify_with_client(client, "https://registry.example", data)
    assert result["inspections"] == 2

    negative = json.loads(json.dumps(data))
    negative["initialInspection"]["products"][0].update(
        {"result": "UNQUALIFIED", "reinspectionResult": "QUALIFIED"}
    )
    negative_detail = detail_for(negative)
    negative_detail["inspections"].append(
        {"id": "derived-recheck", "stage": "RECHECK", "products": []}
    )
    negative_detail["inspections"][0]["products"][0].update(
        {"method": "SAMPLING", "result": "UNQUALIFIED", "reinspectionResult": "QUALIFIED"}
    )
    with (
        verify_client(source, negative_detail, directory) as client,
        pytest.raises(RegistryError, match="实际检查数量"),
    ):
        cli.verify_with_client(client, "https://registry.example", negative)


def test_deep_download_streams_and_enforces_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "MAX_DEEP_VERIFY_BYTES", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"12345",
            headers={"Content-Length": "5"},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match="大小限制"),
    ):
        cli.download_verified_file(
            client,
            "https://registry.example",
            "file-id",
            "sha256:" + "0" * 64,
            {"Origin": "https://registry.example", "X-CSRF-Token": TEST_CSRF},
            "file:one",
        )


def test_deep_download_non_recall_conflict_is_not_hash_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": "OTHER_CONFLICT", "message": "busy"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match="HTTP 409"),
    ):
        cli.download_verified_file(
            client,
            "https://registry.example",
            "file-id",
            "sha256:" + "0" * 64,
            {"Origin": "https://registry.example", "X-CSRF-Token": TEST_CSRF},
            "file:one",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "sha256:" + "0" * 64, "目录 SHA-256"),
        ("remoteState", None, "飞牛正式库"),
        ("nasVerifiedAt", None, "飞牛落盘核验中"),
    ],
)
def test_default_verify_requires_complete_attachment_evidence(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    evidence = directory_file(source)
    evidence[field] = value
    directory = {
        "rows": [
            {
                "slotKey": "OTHER_ATTACHMENT",
                "children": [{"title": "附件", "files": [evidence]}],
            }
        ]
    }
    with (
        verify_client(source, detail_for(data), directory) as client,
        pytest.raises(RegistryError, match=message),
    ):
        cli.verify_with_client(client, "https://registry.example", data)


@pytest.mark.parametrize("status", ["OFFLINE", "FAILED"])
def test_recall_terminal_status_does_not_become_hash_mismatch(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/recall"):
            return httpx.Response(
                200, json={"recallId": "recall-1", "fileId": "file-id", "status": status}
            )
        raise AssertionError(request.url)

    monkeypatch.setattr(cli, "RECALL_POLL_INTERVAL_SECONDS", 0.0)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RegistryError, match=f"{status}"),
    ):
        cli.recall_until_ready(
            client,
            "https://registry.example",
            "file-id",
            {
                "Origin": "https://registry.example",
                "X-Product-Case-Client": "web-v2",
                "X-CSRF-Token": TEST_CSRF,
            },
        )


def test_finalize_summary_whitelists_only_boolean_and_counts() -> None:
    summary = cli.finalize_summary(
        {
            "caseId": "11111111-1111-4111-8111-111111111111",
            "created": True,
            "added": {"products": 2, "slots": 3, "attachments": 1, "secret": "drop"},
            "replaced": {"slots": 4},
            "conflicts": ["details must not persist"],
            "skipped": ["details must not persist"],
            "internal": {"password": "drop"},
        }
    )
    assert summary == {
        "caseId": "11111111-1111-4111-8111-111111111111",
        "created": True,
        "addedProducts": 2,
        "addedSlots": 3,
        "addedAttachments": 1,
        "replacedSlots": 4,
        "conflictCount": 1,
        "skippedCount": 1,
    }
