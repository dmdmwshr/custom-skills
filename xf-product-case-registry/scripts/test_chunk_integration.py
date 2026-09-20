from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from scripts import chunk_upload as chunks
from scripts import registry_cli as cli
from scripts import upload_transport as transport
from scripts.test_chunk_upload import UPLOAD_ID, capabilities, digest, session


class Server:
    """Offline receipt server; faults happen after or before acceptance explicitly."""

    def __init__(self, tmp_path, *, received=0, fault=None):
        self.path = tmp_path / "fixture.pdf"
        self.data = b"%PDF-1.4\n" + b"x" * chunks.CHUNK_SIZE
        self.path.write_bytes(self.data)
        self.item = {
            "clientRef": "f1",
            "relativePath": "fixture.pdf",
            "sizeBytes": len(self.data),
            "sha256": digest(self.data),
            "mimeType": "application/pdf",
        }
        self.blocks = chunks.fingerprints(self.path, self.item)
        self.received = received
        self.complete = False
        self.fault = fault
        self.calls = []
        self.job_reads = 0

    def value(self):
        value = session(self.item, chunks=deepcopy(self.blocks[: self.received]))
        if self.complete:
            value.update(
                state="COMPLETED", receivedFile=chunks.file_identity(self.item), expiresAt=None
            )
        return value

    def handler(self, request):
        assert request.headers["cookie"] == "session=synthetic"
        if request.method != "GET":
            assert request.headers["x-csrf-token"] == "synthetic-csrf"
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/uploads"):
            assert json.loads(request.content) == chunks.file_identity(self.item)
            return httpx.Response(200, json=self.value())
        if path.endswith("/" + UPLOAD_ID):
            return httpx.Response(200, json=self.value())
        if "/chunks/" in path:
            if self.fault in {"408", "429", "410", "500"}:
                return httpx.Response(int(self.fault), headers={"Retry-After": "9"})
            index = int(path.rsplit("/", 1)[1])
            assert index == self.received
            assert (
                request.content
                == self.data[index * chunks.CHUNK_SIZE : (index + 1) * chunks.CHUNK_SIZE]
            )
            assert request.headers["x-chunk-sha256"] == self.blocks[index]["sha256"]
            self.received += 1
            if self.fault == "lost-chunk":
                self.fault = None
                raise httpx.ReadError("synthetic lost chunk response")
            return httpx.Response(200, json=self.value())
        if path.endswith("/complete"):
            assert self.received == len(self.blocks)
            self.complete = True
            if self.fault == "lost-complete":
                self.fault = None
                raise httpx.ReadError("synthetic lost complete response")
            return httpx.Response(200, json=self.value())
        raise AssertionError((request.method, path))

    def read_job(self):
        self.job_reads += 1
        return {
            "status": "UPLOADING",
            "receivedFiles": [chunks.file_identity(self.item)] if self.complete else [],
        }

    def run(self, tmp_path, state):
        with httpx.Client(
            transport=httpx.MockTransport(self.handler), cookies={"session": "synthetic"}
        ) as client:
            cli.upload_pdf_chunked_with_receipt(
                client,
                "https://registry.example",
                "original-job",
                {"x-csrf-token": "synthetic-csrf"},
                self.path,
                self.item,
                state,
                tmp_path / "upload-state.json",
                [self.item],
                self.read_job,
            )


def test_resume_skips_accepted_block_and_only_checkpoints_complete_file(tmp_path, capsys):
    server = Server(tmp_path, received=1)
    state = {"uploadedFileRefs": []}
    server.run(tmp_path, state)
    assert [p for m, p in server.calls if m == "PUT"] == [
        f"/api/v2/import-jobs/original-job/uploads/{UPLOAD_ID}/chunks/1"
    ]
    assert state == {"uploadedFileRefs": ["f1"]}
    assert server.job_reads == 0
    assert json.loads((tmp_path / "upload-state.json").read_text()) == state
    output = capsys.readouterr()
    assert not output.out and "synthetic" not in output.err


def test_lost_chunk_response_stops_then_next_run_recovers_offset(tmp_path):
    server = Server(tmp_path, fault="lost-chunk")
    state = {"uploadedFileRefs": []}
    with pytest.raises(httpx.ReadError):
        server.run(tmp_path, state)
    assert server.received == 1 and server.job_reads == 1
    assert state == {"uploadedFileRefs": []}
    assert len([p for m, p in server.calls if m == "PUT"]) == 1
    server.run(tmp_path, state)
    puts = [p for m, p in server.calls if m == "PUT"]
    assert len(puts) == 2 and puts[0].endswith("/0") and puts[1].endswith("/1")
    assert state == {"uploadedFileRefs": ["f1"]}


def test_lost_complete_response_reconciles_original_job_without_second_complete(tmp_path):
    server = Server(tmp_path, fault="lost-complete")
    state = {"uploadedFileRefs": []}
    with pytest.raises(httpx.ReadError):
        server.run(tmp_path, state)
    assert server.complete and server.job_reads == 1
    assert state == {"uploadedFileRefs": ["f1"]}
    calls = len(server.calls)
    server.run(tmp_path, state)
    assert len(server.calls) == calls + 1  # init recovers completion; no bytes/finalize.
    assert len([p for _, p in server.calls if p.endswith("/complete")]) == 1


@pytest.mark.parametrize("fault", ["408", "429", "410", "500"])
def test_chunk_failure_reads_original_job_once_and_never_retries_or_recreates(tmp_path, fault):
    server = Server(tmp_path, fault=fault)
    state = {"uploadedFileRefs": []}
    with pytest.raises(cli.RegistryError):
        server.run(tmp_path, state)
    assert state == {"uploadedFileRefs": []} and server.job_reads == 1
    assert len([p for m, p in server.calls if m == "PUT"]) == 1
    assert not any(p.endswith("/complete") or p.endswith("/finalize") for _, p in server.calls)


def test_mismatched_init_receipt_never_sends_blocks(tmp_path):
    server = Server(tmp_path)
    server.value = lambda: {"protocol": chunks.PROTOCOL, "relativePath": "wrong.pdf"}
    with pytest.raises(cli.RegistryError):
        server.run(tmp_path, {"uploadedFileRefs": []})
    assert len(server.calls) == 1 and server.job_reads == 1


@pytest.mark.parametrize(
    "response", [httpx.Response(404), httpx.Response(200, json={"enabled": False})]
)
def test_capabilities_unavailable_are_cached_only_for_current_client(response):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert cli.chunk_capabilities(client, "https://registry.example", "whole-file") is None
        assert not calls
        for _ in range(2):
            assert cli.chunk_capabilities(client, "https://registry.example", "resumable") is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(200, json={"enabled": True}),
        httpx.Response(302, headers={"Location": "https://other.example"}),
    ],
)
def test_auth_network_or_incompatible_capability_never_downgrades(response):
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client,
        pytest.raises(cli.RegistryError),
    ):
        cli.chunk_capabilities(client, "https://registry.example", "resumable")


def test_plan_declaration_checks_exact_receipt(tmp_path):
    server = Server(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        plan = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                **plan,
                "fileCount": len(plan["files"]),
                "totalSizeBytes": str(server.item["sizeBytes"]),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cli.declare_upload_plan(
            client, "https://registry.example", "original-job", {}, [server.item], capabilities()
        )
    assert len(calls) == 1 and calls[0].url.path.endswith("/original-job/upload-plan")


@pytest.mark.parametrize("failure", ["digest", "files", "count", "size"])
def test_malformed_plan_receipt_is_rejected(tmp_path, failure):
    item = Server(tmp_path).item
    plan = chunks.make_upload_plan([item], capabilities())
    receipt = {**plan, "fileCount": 1, "totalSizeBytes": str(item["sizeBytes"])}
    receipt.update(
        {
            "digest": {"planDigest": "bad"},
            "files": {"files": []},
            "count": {"fileCount": True},
            "size": {"totalSizeBytes": "1"},
        }[failure]
    )
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_plan_receipt(receipt, plan)


@pytest.mark.parametrize("total,idle", [(0.04, 0.2), (5.0, 0.04)])
def test_raw_block_request_is_cancelled_and_not_replayed(total, idle):
    cancelled = []

    async def handler(request):
        try:
            assert request.content == b"raw block"
            await asyncio.sleep(5)
            return httpx.Response(200)
        finally:
            cancelled.append(True)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.TimeoutException),
    ):
        transport.bounded_request(
            client,
            "PUT",
            "https://registry.example/block",
            total_seconds=total,
            idle_seconds=idle,
            label="合成文件.pdf",
            content=b"raw block",
        )
    assert cancelled == [True]


def test_cli_keeps_whole_file_default_and_accepts_explicit_resumable():
    parser = cli.build_parser()
    base = [
        "upload",
        "--manifest",
        "synthetic.json",
        "--upload-map",
        "map.json",
        "--api-base",
        "https://registry.example",
    ]
    assert parser.parse_args(base).upload_mode == "whole-file"
    assert parser.parse_args([*base, "--upload-mode", "resumable"]).upload_mode == "resumable"


def test_initial_receipt_cannot_regress_before_transfer(tmp_path):
    server = Server(tmp_path, received=1)
    calls = []

    def control(method, path, **kwargs):
        calls.append(method)
        result = server.value()
        if method == "GET":
            result.update(receivedChunks=[], nextOffsetBytes="0")
        return result

    with pytest.raises(chunks.ChunkProtocolError, match="回退"):
        chunks.upload_file(
            server.path, server.item, control, lambda *_a, **_kw: pytest.fail("不得发送")
        )
    assert calls == ["POST", "GET"]


def test_changed_local_prefix_is_detected_before_complete(tmp_path):
    server = Server(tmp_path, received=1)

    def control(method, path, **kwargs):
        assert not path.endswith("/complete")
        return server.value()

    def transfer(*args, **kwargs):
        server.path.write_bytes(b"y" + server.data[1:])
        server.received += 1
        return server.value()

    with pytest.raises(chunks.ChunkProtocolError, match="身份不一致"):
        chunks.upload_file(server.path, server.item, control, transfer)


@pytest.mark.parametrize("field", ["uploadId", "expiresAt", "receivedChunks", "receivedFile"])
def test_completed_old_file_requires_explicit_null_fields(tmp_path, field):
    server = Server(tmp_path)
    value = server.value()
    value.update(
        state="COMPLETED",
        uploadId=None,
        expiresAt=None,
        nextOffsetBytes=str(server.item["sizeBytes"]),
        receivedFile=chunks.file_identity(server.item),
    )
    value.pop(field)
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, server.item, server.path)


def test_completed_non_null_session_cannot_omit_chunk_evidence(tmp_path):
    server = Server(tmp_path)
    value = server.value()
    value.update(
        state="COMPLETED",
        expiresAt=None,
        nextOffsetBytes=str(server.item["sizeBytes"]),
        receivedFile=chunks.file_identity(server.item),
    )
    with pytest.raises(chunks.ChunkProtocolError, match="偏移不连续"):
        chunks.validate_session(value, server.item, server.path)
