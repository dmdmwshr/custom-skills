from __future__ import annotations

import httpx
import pytest

from scripts import registry_cli as cli

PROJECT = "99999999T209900001"
API_BASE = "https://registry.example"
HEADERS = {"X-CSRF-Token": "fixture"}


def test_body_download_rate_limit_preserves_wait_and_never_replays():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "60"}, json={"code": "RATE_LIMITED"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(cli.RegistryWaitError) as error,
    ):
        cli.download_verified_file(
            client, API_BASE, "fixture-file", "sha256:" + "a" * 64, HEADERS, "file:one"
        )
    assert calls == ["/api/v2/files/fixture-file"]
    wait = cli.verification_wait_details(error.value)
    assert wait["status"] == "RATE_LIMITED"
    assert wait["retryAfterSeconds"] == 60
    assert wait["retryAt"]


def prepare_response(
    *,
    preparation_id="case-1",
    case_id="case-1",
    status="PENDING",
    total=32,
    ready=0,
    pending=32,
):
    return {
        "preparationId": preparation_id,
        "caseId": case_id,
        "status": status,
        "total": total,
        "ready": ready,
        "pending": pending,
    }


def run_prepare(monkeypatch, handler, *, wait_seconds):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(cli, "CASE_RECALL_POLL_SECONDS", 10.0)

    def wrapped(request):
        calls.append((request.method, request.url.path))
        return handler(request, len(calls))

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return client, calls, clock


def test_prepare_wait_can_exceed_old_budget_and_reach_ready(monkeypatch):
    def handler(request, call_no):
        if request.method == "POST":
            return httpx.Response(200, json=prepare_response())
        get_count = call_no - 1
        return httpx.Response(
            200,
            json=prepare_response(
                status="READY" if get_count >= 58 else "PENDING",
                ready=32 if get_count >= 58 else 0,
                pending=0 if get_count >= 58 else 32,
            ),
        )

    client, calls, clock = run_prepare(monkeypatch, handler, wait_seconds=1800.0)
    with client:
        cli.prepare_case_content(client, API_BASE, "case-1", 32, HEADERS, wait_seconds=1800.0)
    assert clock[0] == 580.0
    assert calls.count(("POST", "/api/v2/cases/case-1/export-preparations")) == 1
    assert calls.count(("GET", "/api/v2/case-export-preparations/case-1")) == 58


def test_prepare_wait_budget_stops_before_extra_get_or_post(monkeypatch):
    def handler(request, _call_no):
        assert request.method == "POST" or request.method == "GET"
        return httpx.Response(200, json=prepare_response())

    client, calls, clock = run_prepare(monkeypatch, handler, wait_seconds=30.0)
    with client, pytest.raises(cli.RegistryError, match="等待超时") as caught:
        cli.prepare_case_content(client, API_BASE, "case-1", 32, HEADERS, wait_seconds=30.0)
    assert clock[0] == 30.0
    assert calls.count(("POST", "/api/v2/cases/case-1/export-preparations")) == 1
    assert calls.count(("GET", "/api/v2/case-export-preparations/case-1")) == 2
    assert "SHA" not in str(caught.value) and "哈希" not in str(caught.value)


@pytest.mark.parametrize("retry_after", [None, "10"])
def test_prepare_429_stops_without_body_replay_or_polling(monkeypatch, retry_after):
    def handler(request, _call_no):
        assert request.method == "POST"
        return httpx.Response(
            429,
            json={"code": "RATE_LIMITED"},
            headers={"Retry-After": retry_after} if retry_after else {},
        )

    client, calls, _clock = run_prepare(monkeypatch, handler, wait_seconds=1800.0)
    with client, pytest.raises(cli.RegistryWaitError):
        cli.prepare_case_content(client, API_BASE, "case-1", 32, HEADERS, wait_seconds=1800.0)
    assert calls == [("POST", "/api/v2/cases/case-1/export-preparations")]


def test_prepare_initial_request_time_consumes_same_budget(monkeypatch):
    def handler(request, _call_no):
        assert request.method == "POST"
        assert request.extensions["timeout"]["read"] == 30.0
        clock[0] += 20.0
        return httpx.Response(200, json=prepare_response())

    client, calls, clock = run_prepare(monkeypatch, handler, wait_seconds=30.0)
    with client, pytest.raises(cli.RegistryWaitError, match="等待超时"):
        cli.prepare_case_content(client, API_BASE, "case-1", 32, HEADERS, wait_seconds=30.0)
    assert clock[0] == 30.0
    assert calls == [("POST", "/api/v2/cases/case-1/export-preparations")]


@pytest.mark.parametrize(
    "response",
    [
        prepare_response(preparation_id="other-case"),
        prepare_response(case_id="other-case"),
        prepare_response(total=31, pending=31),
        prepare_response(total=32, ready=1, pending=30),
    ],
)
def test_prepare_bad_identity_or_counts_stops_before_get(monkeypatch, response):
    def handler(request, _call_no):
        assert request.method == "POST"
        return httpx.Response(200, json=response)

    client, calls, _clock = run_prepare(monkeypatch, handler, wait_seconds=1800.0)
    with client, pytest.raises(cli.RegistryError):
        cli.prepare_case_content(client, API_BASE, "case-1", 32, HEADERS, wait_seconds=1800.0)
    assert calls == [("POST", "/api/v2/cases/case-1/export-preparations")]


def command_args(name: str) -> list[str]:
    if name == "upload":
        return [
            "upload",
            "--manifest",
            "manifest.json",
            "--upload-map",
            "upload-map.json",
            "--api-base",
            API_BASE,
            "--finalize",
        ]
    if name == "verify":
        return [
            "verify",
            "--manifest",
            "manifest.json",
            "--upload-map",
            "upload-map.json",
            "--api-base",
            API_BASE,
        ]
    if name == "upload-batch":
        return [
            "upload-batch",
            "--project",
            PROJECT,
            "--api-base",
            API_BASE,
            "--finalize",
        ]
    if name == "supplement":
        return [
            "supplement",
            "--manifest",
            "manifest.json",
            "--upload-map",
            "upload-map.json",
            "--api-base",
            API_BASE,
            "--dry-run",
        ]
    return [
        "supplement-batch",
        "--project",
        PROJECT,
        "--api-base",
        API_BASE,
        "--dry-run",
    ]


@pytest.mark.parametrize(
    "command", ["upload", "verify", "upload-batch", "supplement", "supplement-batch"]
)
@pytest.mark.parametrize("value", ["nan", "inf", "0", "7201"])
def test_cli_recall_wait_seconds_rejects_invalid_values(command, value):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(command_args(command) + ["--recall-wait-seconds", value])


@pytest.mark.parametrize(
    "command", ["upload", "verify", "upload-batch", "supplement", "supplement-batch"]
)
def test_cli_recall_wait_seconds_default_and_explicit_value(command):
    parser = cli.build_parser()
    default_args = parser.parse_args(command_args(command))
    explicit_args = parser.parse_args(command_args(command) + ["--recall-wait-seconds", "321.5"])
    assert default_args.recall_wait_seconds == 1800.0
    assert explicit_args.recall_wait_seconds == 321.5


def test_verify_poll_forwards_recall_wait_seconds(monkeypatch):
    captured = {}

    def fake_verify(*args, **kwargs):
        captured["wait"] = (
            kwargs["recall_wait_seconds"] if "recall_wait_seconds" in kwargs else args[-1]
        )
        return {"status": "READY"}

    monkeypatch.setattr(cli, "verify_with_client", fake_verify)
    result = cli.verify_with_poll(
        None,
        API_BASE,
        {},
        HEADERS,
        False,
        60.0,
        recall_wait_seconds=321.5,
    )
    assert result == {"status": "READY"}
    assert captured == {"wait": 321.5}
