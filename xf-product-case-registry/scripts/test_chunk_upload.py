from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import chunk_upload as chunks

PROTOCOL = "ImportUploadChunksV1"
CHUNK_SIZE = 1_048_576
SHA_A = "sha256:" + "a" * 64
UPLOAD_ID = "00000000-0000-4000-8000-000000000001"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def capabilities(**changes):
    value = {
        "protocol": PROTOCOL,
        "enabled": True,
        "chunkSizeBytes": CHUNK_SIZE,
        "checksumAlgorithm": "sha256",
        "maxFileBytes": "104857600",
        "maxJobBytes": "104857600",
        "sessionIdleTtlSeconds": 86400,
        "chunkTotalTimeoutSeconds": 120,
        "chunkIdleTimeoutSeconds": 60,
        "chunkClientTimeoutSeconds": 150,
        "completeTotalTimeoutSeconds": 120,
        "completeClientTimeoutSeconds": 150,
        "uploadPlanSupported": True,
        "maxPlanFiles": 512,
        "maxPlanBodyBytes": 524288,
        "maxRelativePathUtf8Bytes": 512,
    }
    value.update(changes)
    return value


def projection(path="fixture.pdf", *, size=1048584, sha=SHA_A, ref="file:one"):
    return [{"clientRef": ref, "relativePath": path, "sizeBytes": size, "sha256": sha}]


def session(item, *, state="RECEIVING", upload_id=UPLOAD_ID, chunks=None, offset=None):
    received = [] if chunks is None else chunks
    return {
        "protocol": PROTOCOL,
        "uploadId": upload_id,
        "state": state,
        "relativePath": item["relativePath"],
        "sizeBytes": str(item["sizeBytes"]),
        "sha256": item["sha256"],
        "chunkSizeBytes": CHUNK_SIZE,
        "nextOffsetBytes": str(
            sum(int(chunk["sizeBytes"]) for chunk in received) if offset is None else offset
        ),
        "receivedChunks": received,
        "expiresAt": "2099-01-01T01:00:00.000Z" if state == "RECEIVING" else None,
        "receivedFile": None,
    }


def test_make_upload_plan_matches_frozen_digest_vector():
    item = projection()
    request = chunks.make_upload_plan(item, capabilities())
    assert request == {
        "schemaVersion": "ImportUploadPlanV1",
        "planDigest": "sha256:41812d28cfcffbfd2d4857ebb3f26f8069d74869bc4b94d7e469a2944126ac37",
        "files": [
            {
                "relativePath": "fixture.pdf",
                "sizeBytes": "1048584",
                "sha256": SHA_A,
            }
        ],
    }


def test_make_upload_plan_sorts_by_relative_path_utf8_without_normalizing():
    files = [
        *projection("中.pdf", size=3, sha=digest(b"c"), ref="c"),
        *projection("é.pdf", size=2, sha=digest(b"b"), ref="b"),
        *projection("a.pdf", size=1, sha=digest(b"a"), ref="a"),
    ]
    request = chunks.make_upload_plan(files, capabilities())
    paths = [item["relativePath"] for item in request["files"]]
    assert paths == sorted(paths, key=lambda value: value.encode("utf-8"))
    assert "é.pdf" in json.dumps(request, ensure_ascii=False)


@pytest.mark.parametrize(
    "relative_path",
    [
        "/absolute.pdf",
        "../parent.pdf",
        "dir/../parent.pdf",
        "dir\\file.pdf",
        "dir//file.pdf",
        "dir/./file.pdf",
        "dir/line\nfeed.pdf",
        "not-a-pdf.txt",
    ],
)
def test_make_upload_plan_rejects_unsafe_paths(relative_path):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.make_upload_plan(projection(relative_path), capabilities())


@pytest.mark.parametrize(
    "items",
    [
        projection("same.pdf", ref="one") + projection("same.pdf", ref="two"),
        projection("same.pdf", ref="one") + projection("same.pdf", ref="one"),
    ],
)
def test_make_upload_plan_rejects_duplicate_paths_or_refs(items):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.make_upload_plan(items, capabilities())


@pytest.mark.parametrize("size", [0, -1, True, "1", 1.5])
def test_make_upload_plan_requires_positive_integer_sizes(size):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.make_upload_plan(projection(size=size), capabilities())


@pytest.mark.parametrize("sha", ["", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "md5:" + "a" * 32])
def test_make_upload_plan_requires_lowercase_sha256(sha):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.make_upload_plan(projection(sha=sha), capabilities())


@pytest.mark.parametrize(
    "changes",
    [
        {"maxPlanFiles": 0},
        {"maxFileBytes": "0"},
        {"maxJobBytes": "1"},
        {"maxRelativePathUtf8Bytes": 3},
        {"maxPlanBodyBytes": 20},
    ],
)
def test_make_upload_plan_obeys_capability_limits(changes):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.make_upload_plan(projection(), capabilities(**changes))


def test_disabled_capability_falls_back_without_validating_optional_fields():
    assert chunks.parse_capabilities({"enabled": False, "protocol": "unknown"}) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol": "OtherProtocol"},
        {"checksumAlgorithm": "sha512"},
        {"chunkSizeBytes": 512},
        {"chunkTotalTimeoutSeconds": 0},
        {"maxPlanFiles": 0},
        {"maxRelativePathUtf8Bytes": 0},
    ],
)
def test_parse_capabilities_rejects_unknown_or_unsupported_parameters(changes):
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.parse_capabilities(capabilities(**changes))


@pytest.mark.parametrize("field", list(capabilities()))
def test_parse_capabilities_rejects_missing_required_fields(field):
    value = capabilities()
    value.pop(field)
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.parse_capabilities(value)


@pytest.mark.parametrize(
    "field",
    [
        "enabled",
        "chunkSizeBytes",
        "maxFileBytes",
        "maxJobBytes",
        "sessionIdleTtlSeconds",
        "chunkTotalTimeoutSeconds",
        "chunkIdleTimeoutSeconds",
        "chunkClientTimeoutSeconds",
        "completeTotalTimeoutSeconds",
        "completeClientTimeoutSeconds",
        "uploadPlanSupported",
        "maxPlanFiles",
        "maxPlanBodyBytes",
        "maxRelativePathUtf8Bytes",
    ],
)
def test_parse_capabilities_rejects_wrong_types_and_bool_integers(field):
    value = capabilities()
    value[field] = 1 if field in {"enabled", "uploadPlanSupported"} else True
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.parse_capabilities(value)


def test_parse_capabilities_returns_validated_capability_projection():
    value = capabilities()
    parsed = chunks.parse_capabilities(value)
    assert parsed == value


def test_validate_session_accepts_contiguous_resume_and_returns_original_dict(tmp_path: Path):
    data = b"resume-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    chunk = {"index": 0, "sizeBytes": str(len(data)), "sha256": digest(data)}
    value = session(item, chunks=[chunk])
    assert chunks.validate_session(value, item, path) is value


def test_validate_completed_session_allows_null_id_and_empty_chunks(tmp_path: Path):
    data = b"already-received"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    value = session(item, state="COMPLETED", upload_id=None, chunks=[])
    value["nextOffsetBytes"] = str(len(data))
    value["receivedFile"] = {
        "relativePath": "fixture.pdf",
        "sizeBytes": str(len(data)),
        "sha256": digest(data),
    }
    assert chunks.validate_session(value, item, path) is value


def test_validate_completed_session_keeps_original_chunks_and_receipt(tmp_path: Path):
    data = b"completed-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    chunk = {"index": 0, "sizeBytes": str(len(data)), "sha256": digest(data)}
    value = session(item, state="COMPLETED", chunks=[chunk])
    value["nextOffsetBytes"] = str(len(data))
    value["receivedFile"] = {
        "relativePath": "fixture.pdf",
        "sizeBytes": str(len(data)),
        "sha256": digest(data),
    }
    assert chunks.validate_session(value, item, path) is value


@pytest.mark.parametrize(
    "field,change",
    [
        ("protocol", "OtherProtocol"),
        ("uploadId", "not-a-uuid"),
        ("relativePath", "other.pdf"),
        ("sizeBytes", "99"),
        ("sha256", "sha256:" + "b" * 64),
    ],
)
def test_validate_session_rejects_identity_or_uuid_mismatch(tmp_path: Path, field, change):
    data = b"identity-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    value = session(item)
    value[field] = change
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, item, path)


def test_validate_session_rejects_expected_upload_id_mismatch(tmp_path: Path):
    data = b"id-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(
            session(item),
            item,
            path,
            upload_id="00000000-0000-4000-8000-000000000002",
        )


def test_validate_session_rejects_local_file_size_or_digest_mismatch(tmp_path: Path):
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"actual")
    item = projection(size=6, sha=digest(b"different"))[0]
    item["relativePath"] = "fixture.pdf"
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(session(item), item, path)


@pytest.mark.parametrize(
    "field,change",
    [
        ("state", "BROKEN"),
        ("uploadId", None),
        ("expiresAt", None),
        ("receivedChunks", None),
        ("nextOffsetBytes", "1"),
        ("chunkSizeBytes", 512),
    ],
)
def test_validate_receiving_session_rejects_invalid_progress(tmp_path: Path, field, change):
    data = b"progress-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    value = session(item)
    value[field] = change
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, item, path)


def test_validate_session_rejects_non_contiguous_or_wrong_chunk_size(tmp_path: Path):
    data = b"x" * (CHUNK_SIZE + 8)
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    first = {"index": 0, "sizeBytes": str(CHUNK_SIZE), "sha256": digest(data[:CHUNK_SIZE])}
    last = {"index": 1, "sizeBytes": "8", "sha256": digest(data[CHUNK_SIZE:])}
    value = session(item, chunks=[first, last])
    assert chunks.validate_session(value, item, path) is value
    for bad_chunks in (
        [{**first, "index": 1}, last],
        [{**first, "sizeBytes": str(CHUNK_SIZE - 1)}, last],
        [first, {**last, "sizeBytes": "7"}],
    ):
        bad = deepcopy(value)
        bad["receivedChunks"] = bad_chunks
        with pytest.raises(chunks.ChunkProtocolError):
            chunks.validate_session(bad, item, path)


@pytest.mark.parametrize(
    "change",
    [
        {"nextOffsetBytes": "1"},
        {"receivedChunks": [{"index": 0, "sizeBytes": "16", "sha256": "sha256:" + "b" * 64}]},
        {"receivedChunks": [{"index": 0, "sizeBytes": "16", "sha256": "bad"}]},
    ],
)
def test_validate_session_rejects_offset_or_chunk_digest_mismatch(tmp_path: Path, change):
    data = b"offset-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    value = session(
        item,
        chunks=[{"index": 0, "sizeBytes": str(len(data)), "sha256": digest(data)}],
    )
    value.update(change)
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, item, path)


@pytest.mark.parametrize(
    "expires_at",
    ["2000-01-01T00:00:00Z", "2099-01-01T00:00:00", "not-a-date"],
)
def test_validate_session_rejects_expired_or_timezone_less_receiving_date(
    tmp_path: Path, expires_at
):
    data = b"expiry-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    value = session(item)
    value["expiresAt"] = expires_at
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, item, path)


@pytest.mark.parametrize(
    "change",
    [
        {"receivedFile": None},
        {
            "receivedFile": {
                "relativePath": "wrong.pdf",
                "sizeBytes": "6",
                "sha256": digest(b"done"),
            }
        },
        {"expiresAt": "2099-01-01T00:00:00.000Z"},
        {"nextOffsetBytes": "0"},
    ],
)
def test_validate_completed_session_rejects_bad_receipt_or_progress(tmp_path: Path, change):
    data = b"done"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(data)
    item = projection(size=len(data), sha=digest(data))[0]
    item["relativePath"] = "fixture.pdf"
    chunk = {"index": 0, "sizeBytes": str(len(data)), "sha256": digest(data)}
    value = session(item, state="COMPLETED", upload_id=UPLOAD_ID, chunks=[chunk])
    value["nextOffsetBytes"] = str(len(data))
    value["receivedFile"] = {
        "relativePath": "fixture.pdf",
        "sizeBytes": str(len(data)),
        "sha256": digest(data),
    }
    value.update(change)
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(value, item, path)


def test_validate_session_rejects_missing_local_file(tmp_path: Path):
    item = projection(size=4, sha=digest(b"data"))[0]
    item["relativePath"] = "fixture.pdf"
    with pytest.raises(chunks.ChunkProtocolError):
        chunks.validate_session(session(item), item, tmp_path / "missing.pdf")
