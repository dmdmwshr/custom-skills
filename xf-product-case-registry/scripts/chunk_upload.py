"""ImportUploadChunksV1: validated, sequential uploads without body replay.

This module does not own credentials, business jobs, V6 checkpoints or retries.
The caller provides authenticated, bounded request functions and reconciles the
original job after an uncertain result. Resumption always starts with init.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

PROTOCOL = "ImportUploadChunksV1"
PLAN_VERSION = "ImportUploadPlanV1"
CHUNK_SIZE = 1048576
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
MAX_BYTES = 104857600
FIXED_CAPABILITIES = {
    "chunkSizeBytes": CHUNK_SIZE,
    "sessionIdleTtlSeconds": 86400,
    "chunkTotalTimeoutSeconds": 120,
    "chunkIdleTimeoutSeconds": 60,
    "chunkClientTimeoutSeconds": 150,
    "completeTotalTimeoutSeconds": 120,
    "completeClientTimeoutSeconds": 150,
    "maxPlanFiles": 512,
    "maxPlanBodyBytes": 524288,
    "maxRelativePathUtf8Bytes": 512,
}


class ChunkProtocolError(RuntimeError):
    """An incompatible capability, file identity or server receipt."""


def decimal(value: Any, *, positive: bool = False) -> int:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value) or len(value) > 16:
        raise ChunkProtocolError("分块协议字节数格式无效")
    size = int(value)
    if size > 2**53 - 1 or (positive and size == 0):
        raise ChunkProtocolError("分块协议字节数超出范围")
    return size


def parse_capabilities(value: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ChunkProtocolError("分块能力响应缺少明确启用状态")
    if value["enabled"] is False:
        return None
    if value.get("protocol") != PROTOCOL or value.get("checksumAlgorithm") != "sha256":
        raise ChunkProtocolError("分块协议或摘要算法不兼容")
    for key, expected in FIXED_CAPABILITIES.items():
        if type(value.get(key)) is not int or value[key] != expected:
            raise ChunkProtocolError(f"分块协议参数不兼容：{key}")
    if type(value.get("uploadPlanSupported")) is not bool:
        raise ChunkProtocolError("分块能力缺少上传计划支持状态")
    for key in ("maxFileBytes", "maxJobBytes"):
        if decimal(value.get(key), positive=True) > MAX_BYTES:
            raise ChunkProtocolError("分块能力限额超出已支持范围")
    return dict(value)


def file_identity(item: dict[str, Any]) -> dict[str, str]:
    name, size, digest = item.get("relativePath"), item.get("sizeBytes"), item.get("sha256")
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or not name.lower().endswith(".pdf")
    ):
        raise ChunkProtocolError("分块文件路径不是规范相对 PDF 路径")
    try:
        if len(name.encode("utf-8")) > 512:
            raise ChunkProtocolError("分块文件路径过长")
    except UnicodeError as error:
        raise ChunkProtocolError("分块文件路径编码无效") from error
    if type(size) is not int or not 0 < size <= MAX_BYTES:
        raise ChunkProtocolError("分块文件大小无效")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ChunkProtocolError("分块文件摘要格式无效")
    return {"relativePath": name, "sizeBytes": str(size), "sha256": digest}


def make_upload_plan(projection: list[dict[str, Any]], caps: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(projection, list) or not 0 < len(projection) <= caps["maxPlanFiles"]:
        raise ChunkProtocolError("上传计划文件数量无效")
    if any(not isinstance(item, dict) for item in projection):
        raise ChunkProtocolError("上传计划文件条目无效")
    files = sorted(
        (file_identity(item) for item in projection),
        key=lambda f: f["relativePath"].encode("utf-8"),
    )
    if len({item["relativePath"] for item in files}) != len(files):
        raise ChunkProtocolError("上传计划存在重复路径")
    if any(
        len(item["relativePath"].encode("utf-8")) > caps["maxRelativePathUtf8Bytes"]
        for item in files
    ):
        raise ChunkProtocolError("上传计划路径超过服务端限额")
    sizes = [int(item["sizeBytes"]) for item in files]
    if max(sizes) > int(caps["maxFileBytes"]) or sum(sizes) > int(caps["maxJobBytes"]):
        raise ChunkProtocolError("上传计划超过服务端文件或任务限额")
    core = {"schemaVersion": PLAN_VERSION, "files": files}
    encoded = json.dumps(core, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = {
        "schemaVersion": PLAN_VERSION,
        "planDigest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "files": files,
    }
    if (
        len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > caps["maxPlanBodyBytes"]
    ):
        raise ChunkProtocolError("上传计划正文超过服务端限额")
    return result


def validate_plan_receipt(value: dict[str, Any], plan: dict[str, Any]) -> None:
    if (
        value.get("schemaVersion") != PLAN_VERSION
        or value.get("planDigest") != plan["planDigest"]
        or value.get("files") != plan["files"]
        or type(value.get("fileCount")) is not int
        or value["fileCount"] != len(plan["files"])
        or decimal(value.get("totalSizeBytes"), positive=True)
        != sum(int(f["sizeBytes"]) for f in plan["files"])
    ):
        raise ChunkProtocolError("上传计划回执与本地完整清单不一致")


def fingerprints(local_path: Path, item: dict[str, Any]) -> list[dict[str, Any]]:
    identity = file_identity(item)
    whole = hashlib.sha256()
    blocks = []
    count = 0
    try:
        with local_path.open("rb") as stream:
            while data := stream.read(CHUNK_SIZE):
                whole.update(data)
                count += len(data)
                if count > int(identity["sizeBytes"]):
                    raise ChunkProtocolError("本地文件大小超过分块文件声明")
                blocks.append(
                    {
                        "index": len(blocks),
                        "sizeBytes": str(len(data)),
                        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                    }
                )
    except OSError as error:
        raise ChunkProtocolError("无法读取本地分块文件") from error
    if count != int(identity["sizeBytes"]) or "sha256:" + whole.hexdigest() != identity["sha256"]:
        raise ChunkProtocolError("本地文件与分块文件身份不一致")
    return blocks


def validate_session(
    value: dict[str, Any],
    item: dict[str, Any],
    local_path: Path,
    upload_id: str | None = None,
) -> dict[str, Any]:
    return _validate_session(value, item, fingerprints(local_path, item), upload_id)


def _validate_session(
    value: dict[str, Any], item: dict[str, Any], blocks: list[dict[str, Any]], upload_id: str | None
) -> dict[str, Any]:
    identity = file_identity(item)
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        raise ChunkProtocolError("分块会话协议无效")
    if (
        not {"uploadId", "state", "receivedChunks", "nextOffsetBytes", "expiresAt", "receivedFile"}
        <= value.keys()
    ):
        raise ChunkProtocolError("分块会话缺少必填字段")
    if any(value.get(key) != expected for key, expected in identity.items()):
        raise ChunkProtocolError("分块会话与原文件身份不一致")
    if type(value.get("chunkSizeBytes")) is not int or value["chunkSizeBytes"] != CHUNK_SIZE:
        raise ChunkProtocolError("分块会话块大小无效")
    identifier = value.get("uploadId")
    if identifier is not None:
        try:
            if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
                raise ValueError()
        except ValueError as error:
            raise ChunkProtocolError("分块会话标识无效") from error
    if upload_id is not None and identifier != upload_id:
        raise ChunkProtocolError("分块会话标识发生变化")
    state = value.get("state")
    chunks = value.get("receivedChunks")
    if (
        state not in {"RECEIVING", "COMPLETED"}
        or not isinstance(chunks, list)
        or len(chunks) > len(blocks)
    ):
        raise ChunkProtocolError("分块会话状态或进度无效")
    for index, entry in enumerate(chunks):
        if (
            not isinstance(entry, dict)
            or type(entry.get("index")) is not int
            or any(entry.get(key) != expected for key, expected in blocks[index].items())
        ):
            raise ChunkProtocolError("服务端分块进度或摘要与本地不一致")
    offset = decimal(value.get("nextOffsetBytes"))
    if identifier is not None and offset != sum(int(b["sizeBytes"]) for b in blocks[: len(chunks)]):
        raise ChunkProtocolError("服务端分块偏移不连续")
    if state == "COMPLETED":
        receipt = value.get("receivedFile")
        if (
            offset != item["sizeBytes"]
            or not isinstance(receipt, dict)
            or any(receipt.get(key) != expected for key, expected in identity.items())
            or ("clientRef" in receipt and receipt["clientRef"] != item.get("clientRef"))
            or "expiresAt" not in value
            or value["expiresAt"] is not None
            or (identifier is None and chunks)
        ):
            raise ChunkProtocolError("分块完成回执不完整或文件身份不一致")
    else:
        if identifier is None or "receivedFile" not in value or value["receivedFile"] is not None:
            raise ChunkProtocolError("接收中分块会话标识或回执无效")
        expires = value.get("expiresAt")
        try:
            if not isinstance(expires, str) or not expires.endswith("Z"):
                raise ValueError()
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(UTC):
                raise ValueError()
        except ValueError as error:
            raise ChunkProtocolError("分块会话到期时间无效") from error
    return value


def upload_file(
    local_path: Path,
    item: dict[str, Any],
    control: Callable[..., dict[str, Any]],
    transfer: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Recover init, send missing blocks once, then complete once.

    Callbacks use relative paths under the already authenticated original job.
    A failed callback exits immediately. The caller reconciles the original job;
    the next explicit run recovers the persisted offset by repeating init.
    """
    blocks = fingerprints(local_path, item)
    value = _validate_session(
        control("POST", "/uploads", json=file_identity(item)), item, blocks, None
    )
    if value["state"] == "COMPLETED":
        fingerprints(local_path, item)
        return value["receivedFile"]
    identifier = value["uploadId"]
    initial_count = len(value["receivedChunks"])
    # Read the persistent session before any bytes, also after an init replay.
    value = _validate_session(control("GET", f"/uploads/{identifier}"), item, blocks, identifier)
    if len(value["receivedChunks"]) < initial_count:
        raise ChunkProtocolError("分块持久进度回退，停止发送")
    if value["state"] == "COMPLETED":
        fingerprints(local_path, item)
        return value["receivedFile"]
    with local_path.open("rb") as stream:
        stream.seek(int(value["nextOffsetBytes"]))
        for block in blocks[len(value["receivedChunks"]) :]:
            data = stream.read(int(block["sizeBytes"]))
            if (
                len(data) != int(block["sizeBytes"])
                or "sha256:" + hashlib.sha256(data).hexdigest() != block["sha256"]
            ):
                raise ChunkProtocolError("发送前文件内容发生变化，停止分块")
            value = _validate_session(
                transfer(
                    "PUT",
                    f"/uploads/{identifier}/chunks/{block['index']}",
                    content=data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": block["sizeBytes"],
                        "X-Chunk-SHA256": block["sha256"],
                    },
                ),
                item,
                blocks,
                identifier,
            )
            if value["state"] == "COMPLETED":
                fingerprints(local_path, item)
                return value["receivedFile"]
            if len(value["receivedChunks"]) != block["index"] + 1:
                raise ChunkProtocolError("分块接收回执未确认当前块，停止发送")
    # Catch changes to an already sent prefix before declaring completion.
    fingerprints(local_path, item)
    value = _validate_session(
        control("POST", f"/uploads/{identifier}/complete", bounded_complete=True),
        item,
        blocks,
        identifier,
    )
    if value["state"] != "COMPLETED":
        raise ChunkProtocolError("分块完成操作未返回完整文件接收回执")
    fingerprints(local_path, item)
    return value["receivedFile"]
