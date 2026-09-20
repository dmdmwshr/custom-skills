import httpx
import pytest

from scripts import content_leases
from scripts import registry_cli as cli

BASE = "https://fixture.example"
BINDING = {
    "projectNo": "fixture-project",
    "caseId": "fixture-case",
    "origin": BASE,
    "manifestSha256": "fixture-manifest",
    "fileIdentities": {"a": {"fileId": "a"}},
}


def projection(status="READY"):
    return {
        "preparationId": "fixture-case",
        "caseId": "fixture-case",
        "leaseId": "fixture-lease",
        "status": status,
        "total": 1,
        "ready": int(status == "READY"),
        "pending": int(status != "READY"),
    }


def test_prepare_timeout_saves_lease_then_restart_reuses_and_releases(tmp_path, monkeypatch):
    now, calls = [0.0], []
    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    path = tmp_path / "content-lease.json"
    checkpoint = content_leases.Checkpoint(cli, path, BINDING)
    ready = [False]

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(200, json={"released": True})
        return httpx.Response(200, json=projection("READY" if ready[0] else "PENDING"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cli.RegistryWaitError, match="等待超时"):
            cli.prepare_case_content(
                client, BASE, "fixture-case", 1, {}, 30, checkpoint=checkpoint.save
            )
        assert cli.read_json(path)["status"] == "ACTIVE"
        ready[0] = True
        restarted = content_leases.Checkpoint(cli, path, BINDING)
        recovered = restarted.restore(client, BASE)
        cli.prepare_case_content(
            client,
            BASE,
            "fixture-case",
            1,
            {},
            30,
            existing_projection=recovered,
            checkpoint=restarted.save,
        )
        restarted.release(client, BASE, {})
        assert cli.read_json(path)["status"] == "RELEASED"
        assert content_leases.Checkpoint(cli, path, BINDING).restore(client, BASE) is None
    assert sum(method == "POST" for method, route in calls) == 1
    assert calls[-1] == ("DELETE", "/api/v2/case-export-leases/fixture-lease")


def test_expired_lease_is_not_replaced_during_recovery(tmp_path):
    checkpoint = content_leases.Checkpoint(cli, tmp_path / "lease.json", BINDING)
    checkpoint.save(projection())
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(410)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert checkpoint.restore(client, BASE) is None
    assert calls == ["GET"] and checkpoint.value["status"] == "EXPIRED"


@pytest.mark.parametrize(
    "change",
    [
        {"projectNo": "foreign"},
        {"caseId": "foreign"},
        {"origin": "https://foreign.example"},
        {"manifestSha256": "changed"},
        {"fileIdentities": {}},
    ],
)
def test_active_checkpoint_is_bound_to_case_manifest_and_contents(tmp_path, change):
    path = tmp_path / "lease.json"
    content_leases.Checkpoint(cli, path, BINDING).save(projection())
    with pytest.raises(cli.RegistryError):
        content_leases.Checkpoint(cli, path, BINDING | change)


def test_failed_release_retains_checkpoint_for_resume(tmp_path):
    path = tmp_path / "lease.json"
    checkpoint = content_leases.Checkpoint(cli, path, BINDING)
    checkpoint.save(projection())
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    429, headers={"Retry-After": "10"}, json={"code": "RATE_LIMITED"}
                )
            )
        ) as client,
        pytest.raises(cli.RegistryWaitError),
    ):
        checkpoint.release(client, BASE, {})
    assert cli.read_json(path)["status"] == "ACTIVE"
