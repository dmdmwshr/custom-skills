import httpx

from scripts import ledger_reconcile
from scripts import registry_cli as cli
from scripts import workspace_state as ws
from scripts.test_registry_cli import (
    PROJECT,
    admin_state_identity,
    detail_for,
    directory_file,
    import_snapshot,
    manifest,
    pdf,
)


def test_finalized_job_with_cleared_import_cache_uses_formal_files_and_only_advances_local(
    tmp_path,
):
    layout = ws.ensure_workspace_layout(ws.BusinessLayout.from_root(tmp_path / "work"))
    work = layout.work_case_dir(PROJECT)
    work.mkdir()
    source = work / "one.pdf"
    pdf(source)
    data = manifest(source)
    path = work / "manifest.json"
    cli.write_json(path, data)
    projection = cli.files_projection(data, {"file:one": str(source)})
    identity = admin_state_identity()
    state = {
        "stateVersion": 6,
        "status": "UPLOADING",
        "origin": "https://registry.example",
        "manifestSha256": cli.file_sha256(path),
        "packageSha256": data["packageSha256"],
        "projectNo": PROJECT,
        "brigadeCode": "XISHAN",
        "jobId": "original-job",
        "authIdentity": identity,
        "filesProjection": projection,
        "immutableBindingDigest": cli.immutable_manifest_binding(data, projection),
        "uploadedFileRefs": [],
    }
    cli.write_json(work / "upload-state.json", state)
    ws.upsert_case(layout, PROJECT, state="UPLOADING")
    detail = detail_for(data)
    snapshot = import_snapshot(data)
    snapshot["case"]["id"] = detail["id"]
    result = {
        "caseId": detail["id"],
        "created": True,
        "added": {"products": 0, "slots": 0, "attachments": 1},
        "replaced": {"slots": 0},
        "conflicts": [],
        "skipped": [],
    }
    job = {
        "id": "original-job",
        "packageName": PROJECT,
        "packageHash": data["packageSha256"],
        "projectNo": PROJECT,
        "status": "FINALIZED",
        "receivedFiles": [],
        "resultSummary": result,
        "case": {"id": detail["id"], "projectNo": PROJECT, "brigade": {"routePath": "/xishan"}},
        "finalizedAt": "2026-09-20T00:00:00Z",
    }
    seen = []

    def handler(request):
        assert request.method == "GET"
        seen.append(request.url.path)
        routes = {
            "/api/v2/case-import-state": snapshot,
            "/api/v2/import-jobs/original-job": job,
            "/api/v2/cases": {"data": [{"id": detail["id"], "projectNo": PROJECT}]},
            f"/api/v2/cases/{detail['id']}": detail,
            f"/api/v2/cases/{detail['id']}/directory": {
                "rows": [
                    {
                        "slotKey": "OTHER_ATTACHMENT",
                        "children": [
                            {
                                "title": "附件",
                                "files": [
                                    {
                                        **directory_file(source),
                                        "remoteState": "PENDING",
                                        "nasVerifiedAt": None,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        }
        return httpx.Response(200, json=routes[request.url.path])

    before = {
        p.relative_to(layout.root): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()
    }
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = ledger_reconcile.reconcile(
            cli, client, state["origin"], state["origin"], layout, [PROJECT], identity
        )
        assert report["cases"][0]["uploadStatus"] == "FINALIZED_UNVERIFIED"
        assert before == {
            p.relative_to(layout.root): p.read_bytes()
            for p in layout.root.rglob("*")
            if p.is_file()
        }
        report = ledger_reconcile.reconcile(
            cli, client, state["origin"], state["origin"], layout, [PROJECT], identity, apply=True
        )
    assert report["serverWrites"] == []
    current = cli.read_json(work / "upload-state.json")
    assert current["jobId"] == "original-job" and current["status"] == "FINALIZED_UNVERIFIED"
    assert current["uploadedFileRefs"] == ["file:one"]
    assert not (work / "content-verification.json").exists()
    assert ws.load_waterline(layout)["cases"][PROJECT]["state"] == "UPLOADED_AWAITING_NAS"
