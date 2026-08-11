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
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit


DEFAULT_HOST = "meifu主机"
DEFAULT_EXPECTED_HOSTNAME = "192.129.128.54"
DEFAULT_REMOTE_CACHE = "/root/.cache/meifu-downloads"
DEFAULT_CHUNK_GIB = 2.0
DEFAULT_REMOTE_RESERVE_GIB = 8.0
DEFAULT_LOCAL_RESERVE_GIB = 4.0
TEMPORARY_TRANSFER_EXIT_CODE = 74
ACTIVE_TRANSFER_EXIT_CODE = 75
LOCK_INITIALIZATION_GRACE_SECONDS = 120
OUTPUT_LOCK_SCHEMA = "MeifuOutputLockV1"
WORKER_STATUS_SCHEMA = "MeifuDownloadWorkerStatusV1"
WORKER_LOG_TAIL_BYTES = 4096
SHA256_RE = re.compile(r"(?i)^[0-9a-f]{64}$")
SAFE_JOB_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
URL_IN_TEXT_RE = re.compile(r"(?i)https?://[^\s'\"<>]+")

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


class DownloadInProgress(DownloadError):
    """Another local process owns the same final output path."""


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


def output_lock_dir(request: DownloadRequest) -> Path:
    """Return the local single-flight lock directory for one final output."""
    return request.output.parent / ".meifu-download-staging" / f".{request.output.name}.lock"


def worker_status_path(request: DownloadRequest) -> Path:
    """Keep worker state outside the disposable per-source staging directory."""
    return request.output.parent / ".meifu-download-staging" / f".{request.output.name}.status.json"


def worker_log_path(request: DownloadRequest) -> Path:
    """Keep a source-safe worker log beside the status file."""
    return request.output.parent / ".meifu-download-staging" / f".{request.output.name}.worker.log"


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def redact_urls(value: str) -> str:
    """Ensure child logs and persisted status never retain signed source URLs."""
    return URL_IN_TEXT_RE.sub("已脱敏链接", value)


def process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is a POSIX liveness probe, not a safe Windows
        # equivalent.  On Windows it can map to process termination semantics,
        # so use a read-only tasklist query and fail closed on query errors.
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        return any(f'"{process_id}"' in line for line in result.stdout.splitlines())
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def recover_abandoned_output_lock(lock_dir: Path) -> bool:
    """Remove only an unowned or dead-owner lock directory; never recurse."""
    owner_path = lock_dir / "owner.json"
    owner = read_json_object(owner_path)
    if owner is not None:
        process_id = owner.get("pid")
        if isinstance(process_id, int) and process_is_running(process_id):
            return False
        if isinstance(process_id, int):
            # A known dead process cannot still own the lock.
            pass
        else:
            # A malformed or not-yet-published owner gets the same grace period
            # as a brand-new directory, preventing a startup race from deleting it.
            try:
                age_seconds = time.time() - lock_dir.stat().st_mtime
            except OSError:
                return False
            if age_seconds < LOCK_INITIALIZATION_GRACE_SECONDS:
                return False
    else:
        try:
            age_seconds = time.time() - lock_dir.stat().st_mtime
        except OSError:
            return False
        if age_seconds < LOCK_INITIALIZATION_GRACE_SECONDS:
            return False

    try:
        owner_path.unlink(missing_ok=True)
        lock_dir.rmdir()
    except OSError:
        return False
    return True


@dataclass
class OutputLock:
    """An atomically-created directory lock, released only by its token owner."""

    lock_dir: Path
    owner_path: Path
    token: str
    released: bool = False

    def update_owner(self, *, process_id: int, phase: str) -> None:
        if self.released:
            raise DownloadError("下载锁已释放，不能继续转交。")
        owner = read_json_object(self.owner_path)
        if owner is None or owner.get("token") != self.token:
            raise DownloadInProgress("下载锁所有者已变化，拒绝接管传输。")
        owner.update(
            {
                "pid": process_id,
                "phase": phase,
                "updated_at": now(),
            }
        )
        atomic_write_json(self.owner_path, owner)

    def disown(self) -> None:
        """Leave the lock for a detached worker that uses the same token."""
        self.released = True

    def release(self) -> None:
        if self.released:
            return
        try:
            owner = read_json_object(self.owner_path)
            if owner is None or owner.get("token") != self.token:
                return
            self.owner_path.unlink(missing_ok=True)
            self.lock_dir.rmdir()
        except OSError:
            # Preserve any unexpected contents rather than recursively deleting them.
            pass
        finally:
            self.released = True


def create_output_lock(request: DownloadRequest, *, phase: str) -> OutputLock:
    """Claim an output path before either a foreground or detached worker starts."""
    lock_dir = output_lock_dir(request)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            if attempt == 0 and recover_abandoned_output_lock(lock_dir):
                continue
            raise DownloadInProgress(
                "同一输出文件已有下载任务正在运行；已保留断点状态，请等待其完成后再重试。"
            )
    else:  # pragma: no cover - defensive, the loop always breaks or raises.
        raise DownloadInProgress("同一输出文件已有下载任务正在运行。")

    owner_path = lock_dir / "owner.json"
    token = secrets.token_urlsafe(24)
    try:
        atomic_write_json(
            owner_path,
            {
                "schema": OUTPUT_LOCK_SCHEMA,
                "pid": os.getpid(),
                "token": token,
                "phase": phase,
                "acquired_at": now(),
                "output_path": str(request.output),
            },
        )
    except Exception:
        try:
            lock_dir.rmdir()
        except OSError:
            pass
        raise
    return OutputLock(lock_dir=lock_dir, owner_path=owner_path, token=token)


def claim_reserved_output_lock(request: DownloadRequest, token: str) -> OutputLock:
    """Let only the detached child accept the exact reservation made by its launcher."""
    lock_dir = output_lock_dir(request)
    owner_path = lock_dir / "owner.json"
    owner = read_json_object(owner_path)
    if owner is None or owner.get("token") != token:
        raise DownloadInProgress("下载任务的后台启动锁不存在或已由其他任务接管。")
    lock = OutputLock(lock_dir=lock_dir, owner_path=owner_path, token=token)
    lock.update_owner(process_id=os.getpid(), phase="running")
    return lock


@contextmanager
def acquire_output_lock(request: DownloadRequest) -> Iterator[OutputLock]:
    """Make one foreground output path single-flight across downloader processes."""
    lock = create_output_lock(request, phase="foreground")
    try:
        yield lock
    finally:
        lock.release()


def write_worker_status(
    request: DownloadRequest,
    *,
    status: str,
    process_id: int | None = None,
    detail: str | None = None,
    exit_code: int | None = None,
) -> None:
    """Persist a small, query-free worker record for read-only polling."""
    path = worker_status_path(request)
    previous = read_json_object(path) or {}
    payload: dict[str, Any] = {
        "schema": WORKER_STATUS_SCHEMA,
        "status": status,
        "output_path": str(request.output),
        "source_identity": request.source_identity,
        "safe_url": request.safe_url,
        "started_at": previous.get("started_at") or now(),
        "updated_at": now(),
    }
    if process_id is not None:
        payload["pid"] = process_id
    if detail:
        payload["detail"] = redact_urls(detail)[-1200:]
    if exit_code is not None:
        payload["exit_code"] = exit_code
    atomic_write_json(path, payload)


def read_log_tail(path: Path) -> str | None:
    """Read a small local worker-log tail without exposing a signed URL."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - WORKER_LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return None
    return redact_urls(tail) if tail else None


def normalize_output_path(output: str) -> Path:
    value = output.strip()
    if not value:
        raise DownloadError("输出路径不能为空。")
    target = Path(value).expanduser()
    if not target.is_absolute():
        raise DownloadError("输出路径必须是绝对路径。")
    if not target.name or target.name in {".", ".."}:
        raise DownloadError("输出路径必须指向一个文件。")
    if target.exists() and target.is_dir():
        raise DownloadError("输出路径不能是目录。")
    return target.resolve(strict=False)


def resolve_storage_target(storage_root: str, target: str) -> Path:
    """Build one final output below an explicit storage root without path escape."""
    root_value = storage_root.strip()
    target_value = target.strip()
    if not root_value or not target_value:
        raise DownloadError("存储根目录和相对下载目标都不能为空。")
    root = Path(root_value).expanduser()
    relative = Path(target_value)
    if not root.is_absolute():
        raise DownloadError("存储根目录必须是绝对路径。")
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise DownloadError("相对下载目标不得是绝对路径，也不得包含路径回退。")
    if root.exists() and not root.is_dir():
        raise DownloadError("存储根目录不能是文件。")
    resolved_root = root.resolve(strict=False)
    candidate = (resolved_root / relative).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise DownloadError("相对下载目标超出了指定存储根目录。") from exc
    return normalize_output_path(str(candidate))


def requested_output_path(args: argparse.Namespace) -> str:
    """Accept either a final absolute path or a storage-root/relative-target pair."""
    if args.output:
        if args.storage_root or args.target:
            raise DownloadError("--output 不能与 --storage-root 或 --target 同时使用。")
        return str(normalize_output_path(args.output))
    if args.storage_root or args.target:
        if not args.storage_root or not args.target:
            raise DownloadError("使用存储根目录模式时，必须同时提供 --storage-root 和 --target。")
        return str(resolve_storage_target(args.storage_root, args.target))
    raise DownloadError("必须提供 --output，或同时提供 --storage-root 和 --target。")


def find_state_for_output(output: Path) -> dict[str, Any] | None:
    """Find only a matching local state file; never contact Meifu while polling."""
    staging_root = output.parent / ".meifu-download-staging"
    try:
        entries = list(staging_root.iterdir())
    except OSError:
        return None
    newest: tuple[float, dict[str, Any]] | None = None
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        state = read_json_object(entry / "state.json")
        request = state.get("request") if state else None
        if not isinstance(request, dict) or request.get("output_path") != str(output):
            continue
        try:
            modified = (entry / "state.json").stat().st_mtime
        except OSError:
            continue
        if newest is None or modified > newest[0]:
            newest = (modified, state)
    return newest[1] if newest else None


def inspect_download_status(output: str) -> dict[str, Any]:
    """Return local-only progress, lock ownership and a sanitized worker-log tail."""
    target = normalize_output_path(output)
    request_stub = DownloadRequest(source_url="https://status.invalid/", output=target, expected_sha256=None)
    # The status and lock filenames depend only on the final output path, not the URL.
    status_path = worker_status_path(request_stub)
    log_path = worker_log_path(request_stub)
    status_record = read_json_object(status_path) or {}
    state = find_state_for_output(target)
    lock_owner = read_json_object(output_lock_dir(request_stub) / "owner.json") or {}
    process_id = lock_owner.get("pid")
    lock_active = isinstance(process_id, int) and process_is_running(process_id)

    completed_chunks = 0
    bytes_complete = 0
    total_bytes: int | None = None
    if state:
        completed = state.get("completed_chunks", [])
        if isinstance(completed, list):
            completed_chunks = len(completed)
            bytes_complete = sum(
                int(row.get("size_bytes", 0))
                for row in completed
                if isinstance(row, dict)
            )
        request_state = state.get("request")
        if isinstance(request_state, dict) and isinstance(request_state.get("size_bytes"), int):
            total_bytes = request_state["size_bytes"]

    target_exists = target.exists() and target.is_file()
    recorded_status = str(status_record.get("status") or "")
    if lock_active:
        status = "running"
    elif target_exists and recorded_status == "completed":
        status = "completed"
    elif recorded_status:
        status = recorded_status
    elif target_exists:
        status = "target_present_unverified"
    elif state:
        status = "inactive_with_resume_state"
    else:
        status = "not_started"

    result: dict[str, Any] = {
        "mode": "local_status",
        "status": status,
        "output": str(target),
        "target_exists": target_exists,
        "lock_active": lock_active,
        "worker_pid": process_id if isinstance(process_id, int) else None,
        "completed_chunks": completed_chunks,
        "bytes_complete": bytes_complete,
        "total_bytes": total_bytes,
        "updated_at": status_record.get("updated_at"),
    }
    if isinstance(status_record.get("detail"), str):
        result["detail"] = redact_urls(status_record["detail"])
    log_tail = read_log_tail(log_path)
    if log_tail:
        result["recent_log"] = log_tail
    return result


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
    return DownloadRequest(
        source_url=normalized_url,
        output=normalize_output_path(output),
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
    with acquire_output_lock(request):
        execute_locked(request, args, route)


def execute_locked(
    request: DownloadRequest, args: argparse.Namespace, route: dict[str, str]
) -> None:
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


def background_worker_command(
    request: DownloadRequest, args: argparse.Namespace, lock_token: str
) -> list[str]:
    """Build a detached-child command without placing the source URL on its command line."""
    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--url-stdin",
        "--output",
        str(request.output),
        "--execute",
        "--host",
        args.host,
        "--expected-hostname",
        args.expected_hostname,
        "--chunk-gib",
        str(args.chunk_gib),
        "--remote-reserve-gib",
        str(args.remote_reserve_gib),
        "--local-reserve-gib",
        str(args.local_reserve_gib),
        "--_background-worker",
        "--_lock-token",
        lock_token,
    ]
    if request.expected_sha256:
        command.extend(["--sha256", request.expected_sha256])
    if args.allow_http:
        command.append("--allow-http")
    return command


def background_popen_options() -> dict[str, Any]:
    """Detach the worker from the caller's terminal and hide Windows console windows."""
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


def start_background_worker(request: DownloadRequest, args: argparse.Namespace) -> None:
    """Reserve the output first, then quickly hand it to a detached worker process."""
    lock = create_output_lock(request, phase="launching")
    worker: subprocess.Popen[bytes] | None = None
    try:
        write_worker_status(request, status="launching")
        log_path = worker_log_path(request)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as log_handle:
            worker = subprocess.Popen(
                background_worker_command(request, args, lock.token),
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **background_popen_options(),
            )
            # Transfer ownership before feeding stdin.  If the launcher then
            # exits unexpectedly, a live child still keeps the lock; a dead
            # child is recoverable by its exact PID on the next request.
            lock.update_owner(process_id=worker.pid, phase="starting")
            lock.disown()
            if worker.stdin is None:  # pragma: no cover - Popen PIPE guarantees a stream.
                raise DownloadError("后台下载器未获得标准输入通道。")
            worker.stdin.write((request.source_url + "\n").encode("utf-8"))
            worker.stdin.close()
        # The child validates this token before starting network I/O.  The lock
        # remains continuously owned from the launch reservation through transfer.
    except Exception as exc:
        if worker is not None:
            try:
                worker.terminate()
                worker.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        write_worker_status(request, status="failed_to_start", detail=str(exc), exit_code=2)
        raise
    finally:
        lock.release()

    print(
        json.dumps(
            {
                "status": "started",
                "worker_pid": worker.pid if worker else None,
                "output": str(request.output),
                "status_check": "使用相同输出路径加 --status 进行本地只读轮询。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def execute_background_worker(request: DownloadRequest, args: argparse.Namespace) -> None:
    """Run the long transfer only after proving it owns the launch reservation."""
    if not args.lock_token:
        raise DownloadError("后台工作进程缺少下载锁令牌。")
    lock = claim_reserved_output_lock(request, args.lock_token)
    try:
        write_worker_status(request, status="running", process_id=os.getpid())
        route = ensure_direct_meifu_route(args.host, args.expected_hostname)
        execute_locked(request, args, route)
        write_worker_status(request, status="completed", process_id=os.getpid(), exit_code=0)
    except TransferUnavailable as exc:
        write_worker_status(
            request,
            status="waiting_for_network",
            process_id=os.getpid(),
            detail=str(exc),
            exit_code=TEMPORARY_TRANSFER_EXIT_CODE,
        )
        raise
    except (DownloadError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        write_worker_status(
            request,
            status="failed",
            process_id=os.getpid(),
            detail=str(exc),
            exit_code=2,
        )
        raise
    finally:
        lock.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--url", help="用户确认后的 HTTP(S) 直链。")
    source.add_argument("--url-stdin", action="store_true", help="从标准输入读取敏感链接。")
    parser.add_argument("--output", help="最终文件的绝对路径。")
    parser.add_argument("--storage-root", help="可配置的绝对存储根目录。")
    parser.add_argument("--target", help="存储根目录下的相对文件目标。")
    parser.add_argument("--sha256", help="可选的官方 SHA-256。")
    parser.add_argument("--allow-http", action="store_true", help="明确允许非加密 HTTP 来源。")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    parser.add_argument("--chunk-gib", type=float, default=DEFAULT_CHUNK_GIB)
    parser.add_argument("--remote-reserve-gib", type=float, default=DEFAULT_REMOTE_RESERVE_GIB)
    parser.add_argument("--local-reserve-gib", type=float, default=DEFAULT_LOCAL_RESERVE_GIB)
    parser.add_argument("--probe-only", action="store_true", help="只检查，不创建缓存。")
    parser.add_argument("--execute", action="store_true", help="启动受控后台下载。")
    parser.add_argument(
        "--status",
        action="store_true",
        help="只读取本地状态、锁和日志，不连接 Meifu、不创建缓存。",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="仅供人工值守排障；禁止在 Codex 会话中运行长传输。",
    )
    parser.add_argument("--_background-worker", dest="background_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_lock-token", dest="lock_token", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = requested_output_path(args)
        if args.status:
            if (
                args.url
                or args.url_stdin
                or args.probe_only
                or args.execute
                or args.foreground
                or args.background_worker
                or args.lock_token
                or args.sha256
                or args.allow_http
            ):
                raise DownloadError("--status 只接受输出位置，不能混用来源或传输参数。")
            print(json.dumps(inspect_download_status(output), ensure_ascii=False, indent=2))
            return 0
        if args.probe_only and args.execute:
            raise DownloadError("--probe-only 不能与 --execute 同时使用。")
        if args.foreground and not args.execute:
            raise DownloadError("--foreground 只能与 --execute 一起使用。")
        if args.background_worker and not args.execute:
            raise DownloadError("内部后台工作进程必须使用 --execute。")
        if args.lock_token and not args.background_worker:
            raise DownloadError("下载锁令牌只能由内部后台工作进程使用。")
        if not args.url and not args.url_stdin:
            raise DownloadError("必须提供 --url 或 --url-stdin；本地查询请使用 --status。")
        source_url = (
            sys.stdin.read().strip()
            if args.url_stdin
            else str(args.url or "").strip()
        )
        request = validate_request(
            source_url,
            output,
            args.sha256,
            allow_http=args.allow_http,
        )
        if args.chunk_gib <= 0:
            raise DownloadError("分块大小必须大于 0。")
        if args.probe_only:
            route = ensure_direct_meifu_route(args.host, args.expected_hostname)
            print_probe(request, remote_probe(args.host, request.source_url), args, route)
            return 0
        if not args.execute:
            route = ensure_direct_meifu_route(args.host, args.expected_hostname)
            print_dry_run(request, args, route)
            return 0
        if args.background_worker:
            execute_background_worker(request, args)
            return 0
        if args.foreground:
            execute(request, args)
            return 0
        start_background_worker(request, args)
        return 0
    except DownloadInProgress as exc:
        print(
            json.dumps({"status": "already_running", "error": redact_urls(str(exc))}, ensure_ascii=False),
            file=sys.stderr,
        )
        return ACTIVE_TRANSFER_EXIT_CODE
    except TransferUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "waiting_for_network",
                    "error": redact_urls(str(exc)),
                    "resume": "已保留本机状态和 Meifu 当前分块；网络恢复后使用相同链接和输出路径重跑。",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return TEMPORARY_TRANSFER_EXIT_CODE
    except (DownloadError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps({"status": "failed", "error": redact_urls(str(exc))}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
