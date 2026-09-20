import httpx
import pytest

from scripts import content_receipts
from scripts import registry_cli as cli
from scripts.test_registry_cli import PROJECT, detail_for, directory_file, manifest, pdf


def test_interruption_resumes_only_missing_and_repeat_has_no_download_or_prepare(tmp_path):
    first, second = tmp_path / "one.pdf", tmp_path / "two.pdf"
    pdf(first)
    pdf(second, pages=2)
    data = manifest(first)
    data["files"].append(
        {
            "clientRef": "file:two",
            "relativePath": "files/two.pdf",
            "sha256": cli.file_sha256(second),
            "mimeType": "application/pdf",
        }
    )
    data["otherAttachments"].append(
        {
            "clientRef": "attachment:two",
            "slotCode": "OTHER_ATTACHMENT",
            "title": "第二附件",
            "fileRef": "file:two",
        }
    )
    path = tmp_path / "manifest.json"
    cli.write_json(path, data)
    detail = detail_for(data)
    children = [
        {
            "title": title,
            "files": [
                {
                    **directory_file(source),
                    "id": f"file-{i}",
                    "sizeBytes": str(source.stat().st_size),
                    "contentGeneration": 1,
                }
            ],
        }
        for i, (title, source) in enumerate([("附件", first), ("第二附件", second)])
    ]
    seen, fail = [], [True]

    def handler(request):
        method, route = request.method, request.url.path
        seen.append((method, route))
        if route == "/api/v2/cases":
            return httpx.Response(200, json={"data": [{"id": detail["id"], "projectNo": PROJECT}]})
        if route == f"/api/v2/cases/{detail['id']}":
            return httpx.Response(200, json=detail)
        if route.endswith("/directory"):
            return httpx.Response(
                200, json={"rows": [{"slotKey": "OTHER_ATTACHMENT", "children": children}]}
            )
        if route.endswith("/export-preparations") or "/case-export-preparations/" in route:
            return httpx.Response(
                200,
                json={
                    "preparationId": detail["id"],
                    "caseId": detail["id"],
                    "status": "READY",
                    "total": 2,
                    "ready": 2,
                    "pending": 0,
                    "leaseId": "fixture-lease",
                },
            )
        if route.endswith("/renew") or method == "DELETE":
            return httpx.Response(200, json={"ok": True, "released": True})
        if route.startswith("/api/v2/files/"):
            index = int(route[-1])
            if index == 1 and fail[0]:
                return httpx.Response(500)
            return httpx.Response(200, content=[first, second][index].read_bytes())
        raise AssertionError((method, route))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:

        def run():
            return cli.verify_with_client(
                client, "https://registry.example", data, {}, True, receipt_manifest_path=path
            )

        with pytest.raises(cli.RegistryError):
            run()
        proof = cli.read_json(tmp_path / "content-verification.json")
        assert set(proof["files"]) == {"file:one"} and "completedAt" not in proof
        assert cli.read_json(tmp_path / "content-lease.json")["status"] == "ACTIVE"
        fail[0] = False
        seen.clear()
        run()
        assert [route for method, route in seen if route.startswith("/api/v2/files/")] == [
            "/api/v2/files/file-1"
        ]
        assert not any(route.endswith("/export-preparations") for method, route in seen)
        assert ("GET", f"/api/v2/case-export-preparations/{detail['id']}") in seen
        assert ("DELETE", "/api/v2/case-export-leases/fixture-lease") in seen
        assert cli.read_json(tmp_path / "content-lease.json")["status"] == "RELEASED"
        assert cli.read_json(tmp_path / "content-verification.json")["completedAt"]
        completed_receipt = (tmp_path / "content-verification.json").read_bytes()
        seen.clear()
        run()
        assert (tmp_path / "content-verification.json").read_bytes() == completed_receipt
        assert all(
            method == "GET" and not route.startswith("/api/v2/files/") for method, route in seen
        )
        children[0]["files"][0]["contentGeneration"] = 2
        seen.clear()
        run()
        assert [route for method, route in seen if route.startswith("/api/v2/files/")] == [
            "/api/v2/files/file-0"
        ]


def test_manifest_changes_reuse_only_same_file_identity():
    identity = {
        "fileId": "one",
        "sha256": "sha256:" + "a" * 64,
        "contentGeneration": 1,
        "sizeBytes": "42",
    }
    old = {
        "schemaVersion": "ContentVerificationV1",
        "origin": "https://fixture.example",
        "caseId": "case",
        "projectNo": PROJECT,
        "files": {
            "one": {
                **identity,
                "verifiedAt": "2026-09-20T00:00:00Z",
                "verifiedAgainstManifestSha256": "old",
            }
        },
    }
    new = content_receipts.resume_receipt(
        old,
        project_no=PROJECT,
        case_id="case",
        origin="https://fixture.example",
        manifest_sha="new",
        identities={"one": identity, "two": {**identity, "fileId": "two"}},
    )
    assert set(new["files"]) == {"one"}
    assert new["manifestSha256"] == "new"
    assert new["files"]["one"]["verifiedAgainstManifestSha256"] == "old"
    with pytest.raises(ValueError):
        content_receipts.file_identity({"id": "one", "sha256": "hash", "sizeBytes": "42"})
