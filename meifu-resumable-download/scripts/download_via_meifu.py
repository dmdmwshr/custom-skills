#!/usr/bin/env python3
"""Download one user-approved file through a resumable Meifu chunk cache.

Transport:
source HTTPS -> Meifu cache chunk -> local SFTP reget -> local staging
-> optional SHA-256 verification -> atomic move to the requested output.

No network or filesystem mutation occurs until --execute is supplied.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_HOST = "meifu主机"
DEFAULT_EXPECTED_HOSTNAME = "192.129.128.54"
DEFAULT_REMOTE_CACHE = "/root/.cache/meifu-downloads"
DEFAULT_CHUNK_GIB = 2.0
DEFAULT_REMOTE_RESERVE_GIB = 8.0
DEFAULT_LOCAL_RESERVE_GIB = 4.0
TEMPORARY_TRANSFER_EXIT_CODE = 74
SHA256_RE = re.compile(r"(?i)^[0-9a-f]{64}$")
SAFE_JOB_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

TRANSIENT_TRANSFER_PATTERNS = (
    "unknown error",
    "connection timed out",
    "operation timed out",
    "connection reset",
    "connection refused",
    "connection closed",
    "connection lost",
    "connection aborted",
    "network is unreachable",
    "no route to host",
    "could not resolve host",
    "temporary failure in name resolution",
    "kex_exchange_identification",
    "broken pipe",
    "ssh: connect to host",
    "connection to ",
    "the requested url returned error: 408",
    "the requested url returned error: 425",
    "the requested url returned error: 429",
    "the requested url returned error: 500",
    "the requested url returned error: 502",
    "the requested url returned error: 503",
    "the requested url returned error: 504",
    "http 408",
    "http 425",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)
PERMANENT_TRANSFER_PATTERNS = (
    "permission denied",
    "authentication failed",
    "host key verification failed",
    "the requested url returned error: 400",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "the requested url returned error: 404",
    "http 400",
    "http 401",
    "http 403",
    "http 404",
    "no space left on device",
)


class DownloadError(RuntimeError):
    """Expected and user-actionable failure."""


class TransferUnavailable(DownloadError):
    """A retryable network failure that leaves resumable state intact."""


@dataclass(frozen=True)
class DownloadRequest:
    source_url: str
    output: Path
    expected_sha256: str | None

    @property
    def safe_url(self) -> str:
        parts = urlsplit(self.source_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @property
    def source_identity(self) -> str:
        return hashlib.sha256(self.safe_url.encode("utf-8")).hexdigest()

    @property
    def job_id(self) -> str:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.output.stem).strip("-.")
        return f"{stem[:48] or 'download'}-{self.source_identity[:12]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def gib(value: float, *, allow_zero: bool = False) -> int:
    if value < 0 or (value == 0 and not allow_zero):
        raise DownloadError("GiB 参数必须是正数。")
    return int(value * 1024**3)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def normalize_sha256(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip('"').lower()
    if not SHA256_RE.fullmatch(normalized):
        raise DownloadError("SHA-256 必须是 64 位十六进制值。")
    return normalized


def validate_public_source(parts: Any) -> None:
    hostname = parts.hostname
    if not hostname:
        raise DownloadError("下载链接缺少主机名。")
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".local"):
        raise DownloadError("拒绝 localhost 或 .local 来源。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise DownloadError("拒绝私有、回环或保留 IP 来源。")


def validate_request(
    source_url: str,
    output: str,
    expected_sha256: str | None,
    *,
    allow_http: bool,
) -> DownloadRequest:
    source_url = source_url.strip()
    if not source_url:
        raise DownloadError("下载链接不能为空。")
    parts = urlsplit(source_url)
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if parts.scheme.casefold() not in allowed_schemes:
        expected = "HTTPS 或经明确允许的 HTTP" if allow_http else "HTTPS"
        raise DownloadError(f"下载链接必须是{expected}。")
    if parts.username or parts.password:
        raise DownloadError("下载链接不得包含用户名或密码。")
    validate_public_source(parts)
    normalized_url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    target = Path(output).expanduser()
    if not target.is_absolute():
        raise DownloadError("输出路径必须是绝对路径。")
    if not target.name or target.name in {".", ".."}:
        raise DownloadError("输出路径必须指向一个文件。")
    if target.exists() and target.is_dir():
        raise DownloadError("输出路径不能是目录。")
    return DownloadRequest(
        source_url=normalized_url,
        output=target.resolve(strict=False),
        expected_sha256=normalize_sha256(expected_sha256),
    )


def is_transient_transfer_detail(detail: str, *, returncode: int | None = None) -> bool:
    normalized = detail.casefold()
    if any(pattern in normalized for pattern in PERMANENT_TRANSFER_PATTERNS):
        return False
    if returncode == 255:
        return True
    return any(pattern in normalized for pattern in TRANSIENT_TRANSFER_PATTERNS)


def ssh_effective_config(host: str) -> dict[str, str]:
    result = subprocess.run(
        ["ssh", "-G", host],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DownloadError(f"无法读取 SSH 连接配置：{detail[-800:]}")
    config: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator:
            config[key.strip().casefold()] = value.strip()
    return config


def ensure_direct_meifu_route(host: str, expected_hostname: str) -> dict[str, str]:
    config = ssh_effective_config(host)
    proxy_command = config.get("proxycommand", "none").strip().casefold()
    proxy_jump = config.get("proxyjump", "none").strip().casefold()
    effective_host = config.get("hostname", "").strip()
    if proxy_command not in {"", "none"} or proxy_jump not in {"", "none"}:
        raise DownloadError("SSH 配置包含 ProxyJump 或 ProxyCommand，拒绝使用中转路径。")
    if expected_hostname and effective_host != expected_hostname:
        raise DownloadError(
            f"SSH 别名 {host} 解析到 {effective_host or '空'}，不是要求的 Meifu 直连地址。"
        )
    return {
        "alias": host,
        "hostname": effective_host,
        "port": config.get("port", ""),
        "proxycommand": proxy_command,
        "proxyjump": proxy_jump,
        "direct_config_verified": True,
    }


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def remote_preamble(values: dict[str, str]) -> str:
    lines = ["set -euo pipefail", "umask 077"]
    for key, value in values.items():
        lines.append(f"{key}=$(printf %s '{b64(value)}' | base64 -d)")
    return "\n".join(lines) + "\n"


def remote_cache_parent() -> str:
    parent, separator, _ = DEFAULT_REMOTE_CACHE.rpartition("/")
    if not separator or not parent:
        raise DownloadError("受控 Meifu 缓存根目录无效。")
    return parent


def run_remote(host: str, script: str) -> str:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        host,
        "bash",
        "-s",
    ]
    try:
        result = subprocess.run(command, input=script.encode("utf-8"), capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise TransferUnavailable(f"Meifu SSH 连接超时：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        if is_transient_transfer_detail(detail, returncode=result.returncode):
            raise TransferUnavailable(f"Meifu 或来源网络暂不可用：{detail[-1200:]}")
        raise DownloadError(f"Meifu 命令失败：{detail[-1200:]}")
    return result.stdout.decode("utf-8", errors="replace").strip()


def remote_probe(host: str, source_url: str) -> dict[str, Any]:
    script = remote_preamble(
        {"URL": source_url, "CACHE_PARENT": remote_cache_parent()}
    ) + r'''
headers=$(mktemp)
trap 'rm -f "$headers"' EXIT
curl --fail --location --silent --show-error --retry 8 --retry-all-errors --retry-delay 3 \
  --connect-timeout 30 --max-time 600 --range 0-0 --dump-header "$headers" --output /dev/null "$URL"
python3 - "$headers" "$CACHE_PARENT" <<'PY'
import json
import re
import shutil
import sys
from pathlib import Path

headers = Path(sys.argv[1]).read_text(encoding="latin-1")
pairs = re.findall(r"^([^:\r\n]+):\s*(.*?)\s*$", headers, flags=re.MULTILINE)
last = {}
for key, value in pairs:
    last[key.lower()] = value
content_range = last.get("content-range", "")
match = re.search(r"/([0-9]+)\s*$", content_range)
size = int(match.group(1)) if match else None
print(json.dumps({
    "size_bytes": size,
    "x_linked_etag": last.get("x-linked-etag"),
    "etag": last.get("etag"),
    "content_range": content_range,
    "remote_free_bytes": shutil.disk_usage(sys.argv[2]).free,
}))
PY
'''
    response = run_remote(host, script)
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        if is_transient_transfer_detail(response):
            raise TransferUnavailable(f"Meifu 来源探测收到临时上游响应：{response[-800:]}") from exc
        raise DownloadError(f"Meifu 来源探测返回格式无效：{exc}") from exc
    if not isinstance(payload.get("size_bytes"), int) or payload["size_bytes"] <= 0:
        raise DownloadError("来源未提供可用 Content-Range，不能安全进行分块续传。")
    return payload


def remote_fetch_chunk(
    *,
    host: str,
    remote_job: str,
    remote_part: str,
    source_url: str,
    safe_url: str,
    source_identity: str,
    start: int,
    end: int,
) -> str:
    expected_size = end - start + 1
    script = remote_preamble(
        {
            "CACHE_ROOT": DEFAULT_REMOTE_CACHE,
            "JOB": remote_job,
            "PART": remote_part,
            "URL": source_url,
            "SAFE_URL": safe_url,
            "SOURCE_IDENTITY": source_identity,
        }
    ) + f'''
case "$JOB" in "$CACHE_ROOT"/*) ;; *) echo "非法缓存任务路径" >&2; exit 2;; esac
case "$PART" in "$JOB"/parts/*) ;; *) echo "非法缓存分块路径" >&2; exit 2;; esac
mkdir -p "$JOB/parts"
chmod 700 "$JOB"
printf '%s\n' "$SAFE_URL" > "$JOB/.safe_source_url"
printf '%s\n' "$SOURCE_IDENTITY" > "$JOB/.source_identity"
touch "$JOB/.last_activity"
expected={expected_size}
global_start={start}
current=0
if [ -f "$PART" ]; then current=$(stat -c %s "$PART"); fi
if [ "$current" -gt "$expected" ]; then rm -f -- "$PART"; current=0; fi
if [ "$current" -lt "$expected" ]; then
  remaining_start=$((global_start + current))
  set -o pipefail
  curl --fail --location --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
    --connect-timeout 30 --max-time 0 --range "$remaining_start-{end}" "$URL" | \
    dd of="$PART" oflag=append conv=notrunc status=none
fi
actual=$(stat -c %s "$PART")
if [ "$actual" -ne "$expected" ]; then
  rm -f -- "$PART"
  echo "分块大小异常：$actual，期望：$expected" >&2
  exit 3
fi
touch "$JOB/.last_activity"
sha256sum "$PART" | awk '{{print $1}}'
'''
    response = run_remote(host, script)
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    if not lines:
        raise TransferUnavailable("Meifu 分块请求没有返回 SHA-256，已保留当前块等待网络恢复。")
    digest = lines[-1].lower()
    if not SHA256_RE.fullmatch(digest):
        raise DownloadError("Meifu 未返回有效的分块 SHA-256。")
    return digest


def remote_remove(host: str, remote_path: str, *, directory: bool = False) -> None:
    script = remote_preamble({"CACHE_ROOT": DEFAULT_REMOTE_CACHE, "TARGET": remote_path}) + r'''
case "$TARGET" in "$CACHE_ROOT"/*) ;; *) echo "拒绝清理缓存根目录外的路径" >&2; exit 2;; esac
if [ "$TARGET" = "$CACHE_ROOT" ]; then echo "拒绝清理缓存根目录" >&2; exit 2; fi
'''
    script += 'rm -rf -- "$TARGET"\n' if directory else 'rm -f -- "$TARGET"\n'
    run_remote(host, script)


def sftp_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sftp_reget(host: str, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_value = local_path.as_posix()
    batch = f"reget {sftp_quote(remote_path)} {sftp_quote(local_value)}\n"
    command = [
        "sftp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-b",
        "-",
        host,
    ]
    try:
        result = subprocess.run(command, input=batch.encode("utf-8"), capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise TransferUnavailable(f"本机从 Meifu 续传分块超时：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        if is_transient_transfer_detail(detail, returncode=result.returncode):
            raise TransferUnavailable(f"本机到 Meifu 网络暂不可用：{detail[-1200:]}")
        raise DownloadError(f"本机从 Meifu 续传分块失败：{detail[-1200:]}")


def chunk_size_at(index: int, total_size: int, chunk_bytes: int) -> int:
    return min(chunk_bytes, total_size - index * chunk_bytes)


def completed_bytes(state: dict[str, Any]) -> int:
    return sum(int(row["size_bytes"]) for row in state.get("completed_chunks", []))


def request_state(
    request: DownloadRequest,
    *,
    size_bytes: int,
    chunk_bytes: int,
    etag: str | None,
) -> dict[str, Any]:
    return {
        "source_identity": request.source_identity,
        "safe_url": request.safe_url,
        "output_path": str(request.output),
        "size_bytes": size_bytes,
        "chunk_bytes": chunk_bytes,
        "etag": (etag or "").strip().strip('"'),
        "expected_sha256": request.expected_sha256,
    }


def load_or_create_state(
    state_path: Path,
    request: DownloadRequest,
    *,
    size_bytes: int,
    chunk_bytes: int,
    etag: str | None,
) -> dict[str, Any]:
    expected = request_state(
        request,
        size_bytes=size_bytes,
        chunk_bytes=chunk_bytes,
        etag=etag,
    )
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DownloadError(f"下载状态文件无效：{state_path}") from exc
        if state.get("schema") != "MeifuResumableDownloadStateV1":
            raise DownloadError("下载状态 schema 不匹配，拒绝混用未知状态。")
        if state.get("request") != expected:
            raise DownloadError("已有临时下载状态与本次请求或来源版本不同，拒绝混合续传。")
        return state
    state = {
        "schema": "MeifuResumableDownloadStateV1",
        "created_at": now(),
        "request": expected,
        "completed_chunks": [],
    }
    atomic_write_json(state_path, state)
    return state


def reconcile_partial(assembled: Path, state: dict[str, Any]) -> None:
    expected = completed_bytes(state)
    actual = assembled.stat().st_size if assembled.exists() else 0
    if actual < expected:
        raise DownloadError("本地合成文件小于已登记进度，拒绝继续以防混入错误数据。")
    if actual > expected:
        with assembled.open("r+b") as handle:
            handle.truncate(expected)


def append_chunk(assembled: Path, chunk: Path) -> None:
    with assembled.open("ab") as destination, chunk.open("rb") as source:
        shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
        destination.flush()
        os.fsync(destination.fileno())


def resolve_expected_hash(request: DownloadRequest, probe: dict[str, Any]) -> tuple[DownloadRequest, str]:
    if request.expected_sha256:
        return request, "provided"
    linked_etag = str(probe.get("x_linked_etag") or "").strip().strip('"').lower()
    if SHA256_RE.fullmatch(linked_etag):
        return replace(request, expected_sha256=linked_etag), "source_linked_etag"
    return request, "computed_only"


def nearest_existing_directory(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            raise DownloadError(f"无法定位输出盘符：{path}")
        candidate = candidate.parent
    if not candidate.is_dir():
        raise DownloadError(f"输出位置的上级不是目录：{candidate}")
    return candidate


def local_free_bytes(target: Path) -> int:
    return shutil.disk_usage(nearest_existing_directory(target.parent)).free


def ensure_target_available(request: DownloadRequest) -> bool:
    if not request.output.exists():
        return False
    if request.expected_sha256 and sha256_file(request.output) == request.expected_sha256:
        print(
            json.dumps(
                {
                    "status": "already_verified",
                    "target": str(request.output),
                    "sha256": request.expected_sha256,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    raise DownloadError("目标位置已有不同文件，拒绝覆盖。")


def staging_paths(request: DownloadRequest) -> tuple[Path, Path, Path, Path]:
    job_root = request.output.parent / ".meifu-download-staging" / request.job_id
    return (
        job_root,
        job_root / "state.json",
        job_root / f"{request.output.name}.part",
        job_root / "chunks",
    )


def print_dry_run(request: DownloadRequest, args: argparse.Namespace, route: dict[str, str]) -> None:
    print(
        json.dumps(
            {
                "mode": "dry_run",
                "source_url": request.safe_url,
                "output": str(request.output),
                "expected_sha256": request.expected_sha256,
                "transport": {
                    "remote_host": args.host,
                    "expected_hostname": args.expected_hostname,
                    "remote_cache": DEFAULT_REMOTE_CACHE,
                    "chunk_gib": args.chunk_gib,
                    "remote_reserve_gib": args.remote_reserve_gib,
                    "local_resume": "sftp reget",
                    "ssh_route": route,
                },
                "execution_required": "确认后使用 --execute。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def print_probe(
    request: DownloadRequest,
    probe: dict[str, Any],
    args: argparse.Namespace,
    route: dict[str, str],
) -> None:
    request, hash_source = resolve_expected_hash(request, probe)
    total_size = int(probe["size_bytes"])
    chunk_bytes = gib(args.chunk_gib)
    local_required = total_size + min(total_size, chunk_bytes) + gib(args.local_reserve_gib, allow_zero=True)
    remote_required = min(total_size, chunk_bytes) + gib(args.remote_reserve_gib, allow_zero=True)
    print(
        json.dumps(
            {
                "mode": "probe_only",
                "source_url": request.safe_url,
                "output": str(request.output),
                "size_bytes": total_size,
                "expected_sha256": request.expected_sha256,
                "hash_source": hash_source,
                "remote_free_bytes": int(probe["remote_free_bytes"]),
                "remote_minimum_bytes": remote_required,
                "local_free_bytes": local_free_bytes(request.output),
                "local_minimum_bytes": local_required,
                "ssh_route": route,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def execute(request: DownloadRequest, args: argparse.Namespace) -> None:
    route = ensure_direct_meifu_route(args.host, args.expected_hostname)
    probe = remote_probe(args.host, request.source_url)
    request, hash_source = resolve_expected_hash(request, probe)
    if ensure_target_available(request):
        return

    total_size = int(probe["size_bytes"])
    chunk_bytes = gib(args.chunk_gib)
    remote_required = min(total_size, chunk_bytes) + gib(args.remote_reserve_gib, allow_zero=True)
    if int(probe["remote_free_bytes"]) < remote_required:
        raise DownloadError(
            f"Meifu 缓存空间不足：至少需要 {remote_required} 字节，当前只有 {probe['remote_free_bytes']} 字节。"
        )
    local_required = total_size + min(total_size, chunk_bytes) + gib(args.local_reserve_gib, allow_zero=True)
    if local_free_bytes(request.output) < local_required:
        raise DownloadError(f"本机输出盘空间不足：至少需要 {local_required} 字节。")

    job_root, state_path, assembled, local_chunks = staging_paths(request)
    remote_job = f"{DEFAULT_REMOTE_CACHE}/{request.job_id}"
    if not SAFE_JOB_RE.fullmatch(request.job_id):
        raise DownloadError("内部任务标识不安全，拒绝创建缓存。")
    job_root.mkdir(parents=True, exist_ok=True)
    state = load_or_create_state(
        state_path,
        request,
        size_bytes=total_size,
        chunk_bytes=chunk_bytes,
        etag=str(probe.get("etag") or ""),
    )
    reconcile_partial(assembled, state)
    completed = {int(row["index"]) for row in state.get("completed_chunks", [])}
    chunk_count = (total_size + chunk_bytes - 1) // chunk_bytes

    for index in range(chunk_count):
        if index in completed:
            continue
        size = chunk_size_at(index, total_size, chunk_bytes)
        start = index * chunk_bytes
        remote_part = f"{remote_job}/parts/{index:06d}.part"
        local_part = local_chunks / f"{index:06d}.part"
        remote_hash = remote_fetch_chunk(
            host=args.host,
            remote_job=remote_job,
            remote_part=remote_part,
            source_url=request.source_url,
            safe_url=request.safe_url,
            source_identity=request.source_identity,
            start=start,
            end=start + size - 1,
        )
        sftp_reget(args.host, remote_part, local_part)
        if local_part.stat().st_size != size:
            raise DownloadError(f"本地分块 {index + 1} 大小不符，已保留临时文件等待续传。")
        local_hash = sha256_file(local_part)
        if local_hash != remote_hash:
            local_part.unlink(missing_ok=True)
            raise DownloadError(f"分块 {index + 1} 校验失败，已删除本地损坏块，可重新续传。")
        append_chunk(assembled, local_part)
        state["completed_chunks"].append(
            {"index": index, "size_bytes": size, "sha256": local_hash}
        )
        state["completed_chunks"].sort(key=lambda row: int(row["index"]))
        atomic_write_json(state_path, state)
        local_part.unlink(missing_ok=True)
        remote_remove(args.host, remote_part)
        print(
            json.dumps(
                {
                    "status": "chunk_verified",
                    "chunk": index + 1,
                    "chunk_count": chunk_count,
                    "bytes_complete": completed_bytes(state),
                    "total_bytes": total_size,
                },
                ensure_ascii=False,
            )
        )

    if assembled.stat().st_size != total_size:
        raise DownloadError("所有分块完成后，本地合成文件大小仍不正确。")
    actual_sha256 = sha256_file(assembled)
    if request.expected_sha256 and actual_sha256 != request.expected_sha256:
        raise DownloadError("完整文件 SHA-256 不匹配；保留临时文件和状态，未写入最终位置。")
    request.output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(assembled, request.output)
    shutil.rmtree(job_root)
    remote_remove(args.host, remote_job, directory=True)
    print(
        json.dumps(
            {
                "status": "completed",
                "target": str(request.output),
                "size_bytes": total_size,
                "sha256": actual_sha256,
                "source_integrity": (
                    "verified_by_provided_sha256"
                    if hash_source == "provided"
                    else "verified_by_source_linked_sha256"
                    if hash_source == "source_linked_etag"
                    else "computed_only_no_official_sha256"
                ),
                "ssh_route": route,
                "remote_cache_cleaned": remote_job,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="用户确认后的 HTTP(S) 直链。")
    source.add_argument("--url-stdin", action="store_true", help="从标准输入读取敏感链接。")
    parser.add_argument("--output", required=True, help="绝对输出文件路径。")
    parser.add_argument("--sha256", help="可选的官方 SHA-256。")
    parser.add_argument("--allow-http", action="store_true", help="明确允许非加密 HTTP 来源。")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    parser.add_argument("--chunk-gib", type=float, default=DEFAULT_CHUNK_GIB)
    parser.add_argument("--remote-reserve-gib", type=float, default=DEFAULT_REMOTE_RESERVE_GIB)
    parser.add_argument("--local-reserve-gib", type=float, default=DEFAULT_LOCAL_RESERVE_GIB)
    parser.add_argument("--probe-only", action="store_true", help="只检查，不创建缓存。")
    parser.add_argument("--execute", action="store_true", help="允许下载和写入最终文件。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.probe_only and args.execute:
            raise DownloadError("--probe-only 不能与 --execute 同时使用。")
        source_url = (
            sys.stdin.read().strip()
            if args.url_stdin
            else str(args.url or "").strip()
        )
        request = validate_request(
            source_url,
            args.output,
            args.sha256,
            allow_http=args.allow_http,
        )
        if args.chunk_gib <= 0:
            raise DownloadError("分块大小必须大于 0。")
        route = ensure_direct_meifu_route(args.host, args.expected_hostname)
        if args.probe_only:
            print_probe(request, remote_probe(args.host, request.source_url), args, route)
            return 0
        if not args.execute:
            print_dry_run(request, args, route)
            return 0
        execute(request, args)
        return 0
    except TransferUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "waiting_for_network",
                    "error": str(exc),
                    "resume": "已保留本机状态和 Meifu 当前分块；网络恢复后使用相同链接和输出路径重跑。",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return TEMPORARY_TRANSFER_EXIT_CODE
    except (DownloadError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
