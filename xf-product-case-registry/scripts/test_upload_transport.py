from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from scripts import registry_cli as cli
from scripts import upload_transport as transport


def test_upload_keeps_session_and_stdout_and_does_not_retry(capsys):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["cookie"] == "session=synthetic"
        assert request.headers["x-csrf-token"] == "synthetic-csrf"
        assert b"%PDF-1.4" in request.content
        return httpx.Response(429, headers={"Retry-After": "1"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), cookies={"session": "synthetic"}
    ) as client:
        response = transport.upload_request(
            client,
            "POST",
            "https://registry.example/files",
            files={"file": ("sample.pdf", b"%PDF-1.4", "application/pdf")},
            headers={"x-csrf-token": "synthetic-csrf"},
            upload_progress=(2, 4),
        )
    captured = capsys.readouterr()
    assert response.status_code == 429 and len(calls) == 1
    assert captured.out == "" and "已接收 2/4" in captured.err
    assert "synthetic" not in captured.err and "registry.example" not in captured.err


@pytest.mark.parametrize("kind", ["total", "idle"])
def test_deadlines_cancel_inflight_request(monkeypatch, kind):
    closed = []

    async def handler(request):
        try:
            await asyncio.sleep(5)
            return httpx.Response(200)
        finally:
            closed.append(True)

    monkeypatch.setattr(transport, "UPLOAD_TOTAL_SECONDS", 0.05 if kind == "total" else 5)
    monkeypatch.setattr(transport, "UPLOAD_IDLE_SECONDS", 0.1 if kind == "idle" else 0.2)
    started = time.monotonic()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.TimeoutException),
    ):
        transport.upload_request(
            client,
            "POST",
            "https://registry.example/files",
            files={"file": ("sample.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert time.monotonic() - started < 1
    assert closed == [True]


@pytest.mark.parametrize("status", ["CREATED", "UPLOADING", "MANIFEST_RECEIVED"])
def test_missing_projection_fails_closed_without_advancing(status):
    state = {"uploadedFileRefs": []}
    with pytest.raises(cli.RegistryError, match="receivedFiles"):
        cli.reconcile_uploaded_file_refs(
            state,
            {"status": status},
            [
                {
                    "clientRef": "f1",
                    "relativePath": "one.pdf",
                }
            ],
        )
    assert state["uploadedFileRefs"] == []


@pytest.mark.parametrize("failure", ["408", "429", "lost-reply", "cancel", "bad-receipt"])
@pytest.mark.parametrize("received", [True, False])
def test_interrupted_upload_reads_same_job_once_and_never_resends(
    tmp_path: Path,
    monkeypatch,
    failure,
    received,
):
    source = tmp_path / "one.pdf"
    source.write_bytes(b"%PDF-1.4")
    item = {
        "clientRef": "f1",
        "relativePath": "one.pdf",
        "sha256": cli.file_sha256(source),
        "mimeType": "application/pdf",
        "sizeBytes": source.stat().st_size,
    }
    state = {"uploadedFileRefs": []}
    state_path = tmp_path / "upload-state.json"
    sent, reads = [], []

    def upload(*args, **kwargs):
        sent.append(True)
        if failure == "lost-reply":
            raise httpx.ReadError("synthetic disconnected reply")
        if failure == "cancel":
            raise KeyboardInterrupt
        return httpx.Response(int(failure) if failure.isdigit() else 200, json={})

    def read_job():
        reads.append(True)
        return {"status": "UPLOADING", "receivedFiles": [item] if received else []}

    monkeypatch.setattr(cli, "upload_request", upload)
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client,
        pytest.raises((cli.RegistryError, httpx.ReadError, KeyboardInterrupt)),
    ):
        cli.upload_pdf_with_receipt(
            client,
            "https://registry.example",
            "synthetic-job",
            {},
            source,
            item,
            state,
            state_path,
            [item],
            read_job,
        )
    assert sent == [True] and reads == [True]
    assert json.loads(state_path.read_text(encoding="utf-8"))["uploadedFileRefs"] == (
        ["f1"] if received else []
    )
