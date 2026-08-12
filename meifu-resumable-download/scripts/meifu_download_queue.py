#!/usr/bin/env python3
"""Durable, single-worker queue for user-approved public Meifu downloads.

The queue deliberately mirrors the safe parts of the ComfyUI model queue:

* enqueue and start calls are short; the long transfer never runs in Codex;
* exactly one detached worker and one foreground child transfer run at a time;
* queue progress is atomically persisted, so stopped work is re-queued with its
  existing downloader state intact;
* a temporary network failure waits with backoff in the detached worker;
* signed URLs are intentionally excluded from this persistent queue.  Use the
  one-file downloader's stdin-only detached mode for those URLs instead.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from download_via_meifu import (
    ACTIVE_TRANSFER_EXIT_CODE,
    DEFAULT_CHUNK_GIB,
    DEFAULT_EXPECTED_HOSTNAME,
    DEFAULT_HOST,
    DEFAULT_LOCAL_RESERVE_GIB,
    DEFAULT_REMOTE_RESERVE_GIB,
    TEMPORARY_TRANSFER_EXIT_CODE,
    DownloadError,
    normalize_output_path,
    process_is_running as downloader_process_is_running,
    redact_urls,
    requested_output_path,
    validate_request,
)


QUEUE_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MeifuDownloadQueue"
DEFAULT_QUEUE = QUEUE_ROOT / "queue.json"
DEFAULT_CONTROL = QUEUE_ROOT / "queue.control.json"
DEFAULT_LOG = QUEUE_ROOT / "queue.log"
DEFAULT_AUDIT_LOG = QUEUE_ROOT / "queue.audit.jsonl"
DEFAULT_RUNTIME_MANIFEST = QUEUE_ROOT / "runtime.json"
DEFAULT_DOWNLOADER = Path(__file__).with_name("download_via_meifu.py")
WINDOWS_TASK_PATH = "\\DevProjects\\MEIFU\\AUTO\\"
WINDOWS_TASK_NAME = "DEV-MEIFU-AUTO-01-DownloadQueue"
WINDOWS_TASK_FULL_NAME = f"{WINDOWS_TASK_PATH}{WINDOWS_TASK_NAME}"
QUEUE_SCHEMA = "MeifuDownloadQueueV1"
CONTROL_SCHEMA = "MeifuDownloadQueueControlV1"
LOCK_SCHEMA = "MeifuDownloadQueueLockV1"
MANIFEST_SCHEMA = "MeifuDownloadQueueManifestV1"
MANIFEST_LOCK_SCHEMA = "MeifuDownloadQueueManifestLeaseV1"
MANIFEST_WRITE_PROTOCOL = "lease-compare-and-replace-v1"
ENTRY_STATES = {"queued", "running", "completed", "blocked"}
MUTABLE_ENTRY_STATES = {"queued", "blocked"}
AUDIT_SCHEMA = "MeifuDownloadQueueAuditV1"
AUDIT_MAX_BYTES = 4 * 1024 * 1024
AUDIT_RETENTION_FILES = 3
DEFAULT_VIEW_LIMIT = 20
MAX_VIEW_LIMIT = 100
LOCK_INITIALIZATION_GRACE_SECONDS = 120
DEFAULT_RETRY_DELAY_SECONDS = 15 * 60
MAX_RETRY_DELAY_SECONDS = 6 * 60 * 60
RUNTIME_UNAVAILABLE_EXIT_CODE = 75


class QueueError(RuntimeError):
    """A queue operation was rejected without starting an unsafe transfer."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def assert_plain_file(path: Path, *, description: str) -> None:
    """Reject links and special files before the queue trusts a local path."""
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise QueueError(f"{description}不是可用的普通文件。")


def entry_projection(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the public, non-secret mapping recorded by queue and audit views."""
    return {
        "id": entry.get("id"),
        "position": entry.get("position"),
        "priority": entry.get("priority"),
        "status": entry.get("status"),
        "source_url": entry.get("source_url"),
        "output": entry.get("output"),
        "sha256": entry.get("sha256"),
        "requested_by": entry.get("requested_by"),
        "request_id": entry.get("request_id"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "last_error": entry.get("last_error"),
        "last_runtime_error": entry.get("last_runtime_error"),
        "network_failures": entry.get("network_failures"),
        "next_attempt_after": entry.get("next_attempt_after"),
    }


def rotated_audit_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def rotate_audit_log(path: Path) -> None:
    """Bound audit growth without touching queue data or final downloads."""
    if not path.exists():
        return
    assert_plain_file(path, description="审计日志")
    try:
        if path.stat().st_size < AUDIT_MAX_BYTES:
            return
    except OSError as exc:
        raise QueueError("无法读取审计日志大小。") from exc

    for index in range(AUDIT_RETENTION_FILES, 0, -1):
        older = rotated_audit_path(path, index)
        newer = rotated_audit_path(path, index + 1)
        if older.exists():
            assert_plain_file(older, description="历史审计日志")
            if index == AUDIT_RETENTION_FILES:
                older.unlink()
            else:
                os.replace(older, newer)
    os.replace(path, rotated_audit_path(path, 1))


def append_audit_event(
    path: Path,
    *,
    event: str,
    queue_path: Path,
    actor: str,
    queue: dict[str, Any] | None = None,
    entry: dict[str, Any] | None = None,
    request_id: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a bounded JSONL audit record for cross-task queue operations.

    Persistent queues only accept public, unsigned URLs.  The audit record
    therefore deliberately mirrors that safe URL/output mapping, never a
    signed URL, token, cookie, or query string.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise QueueError("审计日志不是普通文件，拒绝写入。")
    rotate_audit_log(path)
    payload: dict[str, Any] = {
        "schema": AUDIT_SCHEMA,
        "at": now(),
        "event": event,
        "actor": actor,
        "request_id": request_id,
        "queue": str(queue_path),
    }
    if queue is not None:
        payload["manifest_revision"] = queue_revision(queue)
    if entry is not None:
        payload["entry"] = entry_projection(entry)
    if reason:
        payload["reason"] = reason
    if details:
        payload["details"] = details
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def queue_manifest_metadata(queue: dict[str, Any]) -> dict[str, Any]:
    """Return validated write metadata, accepting pre-lease V1 queues once."""
    metadata = queue.get("manifest")
    if metadata is None:
        return {
            "schema": MANIFEST_SCHEMA,
            "revision": 0,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
        }
    if not isinstance(metadata, dict):
        raise QueueError("队列写入元数据无效，未尝试覆盖原文件。")
    revision = metadata.get("revision")
    if (
        metadata.get("schema") != MANIFEST_SCHEMA
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or metadata.get("write_protocol") != MANIFEST_WRITE_PROTOCOL
    ):
        raise QueueError("队列写入元数据不匹配，未尝试覆盖原文件。")
    return metadata


def apply_queue_legacy_defaults(queue: dict[str, Any]) -> bool:
    """Fill only optional metadata when a legacy manifest is first reopened."""
    changed = False
    if "next_position" not in queue:
        queue["next_position"] = 10
        changed = True
    return changed


def queue_revision(queue: dict[str, Any]) -> int:
    return int(queue_manifest_metadata(queue)["revision"])


def queue_template() -> dict[str, Any]:
    return {
        "schema": QUEUE_SCHEMA,
        "state": "queued",
        "created_at": now(),
        "updated_at": now(),
        "entries": [],
        "next_position": 10,
        "worker": {"pid": None, "max_concurrent_downloads": 1, "max_remote_chunks": 1},
        "manifest": {
            "schema": MANIFEST_SCHEMA,
            "revision": 0,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
        },
    }


def read_queue(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return queue_template()
        raise QueueError("找不到下载队列；请先用 enqueue 添加已批准的公共直链。")
    queue = read_json_object(path)
    if queue is None:
        raise QueueError("下载队列无法解析；未尝试覆盖原文件。")
    validate_queue(queue)
    apply_queue_legacy_defaults(queue)
    return queue


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{now()}] {redact_urls(message)}\n")


def process_is_running(process_id: int | None) -> bool:
    if not isinstance(process_id, int) or process_id <= 0:
        return False
    return downloader_process_is_running(process_id)


def queue_lock_path(queue_path: Path) -> Path:
    return queue_path.with_name(f".{queue_path.name}.lock")


def queue_manifest_lock_path(queue_path: Path) -> Path:
    return queue_path.with_name(f".{queue_path.name}.manifest.lock")


def lock_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


@dataclass
class QueueLock:
    path: Path
    token: str
    released: bool = False

    def update_owner(self, *, process_id: int, phase: str) -> None:
        if self.released:
            raise QueueError("队列锁已经释放，不能继续转交。")
        owner = read_json_object(self.path)
        if owner is None or owner.get("token") != self.token:
            raise QueueError("队列锁所有者已变化，拒绝接管后台工作进程。")
        owner.update({"pid": process_id, "phase": phase, "updated_at": now()})
        atomic_write_json(self.path, owner)

    def disown(self) -> None:
        self.released = True

    def release(self) -> None:
        if self.released:
            return
        try:
            owner = read_json_object(self.path)
            if owner is not None and owner.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self.released = True


@dataclass
class QueueManifestLease:
    """A short-lived single-writer lease for queue.json read-modify-write work."""

    path: Path
    queue_path: Path
    token: str
    operation: str
    released: bool = False

    def assert_owner(self) -> None:
        if self.released:
            raise QueueError("队列清单写入租约已经释放。")
        owner = read_json_object(self.path)
        if owner is None or owner.get("token") != self.token:
            raise QueueError("队列清单写入租约所有者已变化，拒绝覆盖。")

    def release(self) -> None:
        if self.released:
            return
        try:
            owner = read_json_object(self.path)
            if owner is not None and owner.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self.released = True

    def __enter__(self) -> "QueueManifestLease":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()


def create_queue_lock(queue_path: Path, *, recover_stale: bool, phase: str) -> QueueLock:
    path = queue_lock_path(queue_path)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise QueueError("队列锁不是普通文件，拒绝处理。")
        owner = read_json_object(path) or {}
        process_id = owner.get("pid")
        if isinstance(process_id, int) and process_is_running(process_id):
            raise QueueError("已有通用 Meifu 队列工作进程正在运行，拒绝启动第二个。")
        age = lock_age_seconds(path)
        if not isinstance(process_id, int) and age is not None and age < LOCK_INITIALIZATION_GRACE_SECONDS:
            raise QueueError("发现正在初始化的队列锁；两分钟后仍未就绪再人工确认。")
        if not recover_stale:
            raise QueueError("发现已停止的旧队列锁；确认无遗留工作进程后使用 --recover-stale-lock。")
        try:
            path.unlink()
        except OSError as exc:
            raise QueueError("无法精确回收旧队列锁。") from exc

    token = hashlib.sha256(f"{os.getpid()}:{time.time_ns()}:{queue_path}".encode("utf-8")).hexdigest()
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise QueueError("无法获得队列锁，可能有另一个启动请求刚刚到达。") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "schema": LOCK_SCHEMA,
                    "pid": os.getpid(),
                    "token": token,
                    "phase": phase,
                    "created_at": now(),
                    "queue": str(queue_path),
                },
                handle,
                ensure_ascii=False,
            )
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return QueueLock(path=path, token=token)


def create_queue_manifest_lease(queue_path: Path, *, operation: str) -> QueueManifestLease:
    """Claim one short queue-manifest writer; never hold this through a transfer."""
    path = queue_manifest_lock_path(queue_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise QueueError("队列运行目录不是普通目录，拒绝写入。")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise QueueError("队列清单写入锁不是普通文件，拒绝处理。")
        owner = read_json_object(path) or {}
        process_id = owner.get("pid")
        if isinstance(process_id, int) and process_is_running(process_id):
            raise QueueError("另一个任务正在写入下载队列；请稍后重试，未覆盖任何条目。")
        age = lock_age_seconds(path)
        if not isinstance(process_id, int) and age is not None and age < LOCK_INITIALIZATION_GRACE_SECONDS:
            raise QueueError("发现正在初始化的队列写入锁；两分钟后仍未就绪再人工确认。")
        try:
            path.unlink()
        except OSError as exc:
            raise QueueError("无法精确回收已停止的队列写入锁。") from exc

    token = hashlib.sha256(
        f"{os.getpid()}:{time.time_ns()}:{queue_path}:{operation}".encode("utf-8")
    ).hexdigest()
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise QueueError("无法获得队列清单写入租约，可能有另一个请求刚刚到达。") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "schema": MANIFEST_LOCK_SCHEMA,
                    "pid": os.getpid(),
                    "token": token,
                    "operation": operation,
                    "created_at": now(),
                    "queue": str(queue_path),
                },
                handle,
                ensure_ascii=False,
            )
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return QueueManifestLease(path=path, queue_path=queue_path, token=token, operation=operation)


def claim_queue_lock(queue_path: Path, token: str) -> QueueLock:
    path = queue_lock_path(queue_path)
    owner = read_json_object(path)
    if owner is None or owner.get("token") != token:
        raise QueueError("后台队列启动令牌不匹配，拒绝运行。")
    lock = QueueLock(path=path, token=token)
    lock.update_owner(process_id=os.getpid(), phase="running")
    return lock


def commit_queue(
    path: Path,
    queue: dict[str, Any],
    *,
    lease: QueueManifestLease,
    operation: str,
) -> None:
    """Atomically replace queue.json only when its read revision still matches."""
    if str(lease.queue_path.resolve(strict=False)).casefold() != str(path.resolve(strict=False)).casefold():
        raise QueueError("队列清单写入租约不属于当前队列。")
    lease.assert_owner()
    expected_revision = queue_revision(queue)
    current = read_queue(path, allow_missing=True)
    if queue_revision(current) != expected_revision:
        raise QueueError("队列清单已被其他任务更新；请重新读取后再提交，未覆盖已有条目。")
    apply_queue_legacy_defaults(queue)
    ensure_entry_metadata(queue)
    metadata = dict(queue_manifest_metadata(queue))
    metadata.update(
        {
            "schema": MANIFEST_SCHEMA,
            "revision": expected_revision + 1,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
            "last_mutation": {"operation": operation, "pid": os.getpid(), "at": now()},
        }
    )
    queue["manifest"] = metadata
    queue["updated_at"] = now()
    validate_queue(queue)
    atomic_write_json(path, queue)


def save_queue(
    path: Path,
    queue: dict[str, Any],
    *,
    operation: str = "worker",
    lease: QueueManifestLease | None = None,
) -> None:
    if lease is not None:
        commit_queue(path, queue, lease=lease, operation=operation)
        return
    with create_queue_manifest_lease(path, operation=operation) as write_lease:
        commit_queue(path, queue, lease=write_lease, operation=operation)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def validate_queue(queue: dict[str, Any]) -> None:
    if queue.get("schema") != QUEUE_SCHEMA or not isinstance(queue.get("entries"), list):
        raise QueueError("队列结构不匹配，拒绝继续。")
    queue_manifest_metadata(queue)
    for entry in queue["entries"]:
        if not isinstance(entry, dict) or entry.get("status") not in ENTRY_STATES:
            raise QueueError("队列中存在未知条目状态，拒绝继续。")
        source_url = str(entry.get("source_url") or "")
        parts = urlsplit(source_url)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise QueueError("持久队列只接受无查询参数的 HTTPS 公共直链。")
        normalize_output_path(str(entry.get("output") or ""))
        if "position" in entry and (
            not isinstance(entry["position"], int)
            or isinstance(entry["position"], bool)
            or entry["position"] <= 0
        ):
            raise QueueError("队列条目的顺序位置无效，拒绝继续。")
        if "requested_by" in entry and not isinstance(entry["requested_by"], str):
            raise QueueError("队列条目的请求方标识无效，拒绝继续。")
        if "request_id" in entry and entry["request_id"] is not None and not isinstance(entry["request_id"], str):
            raise QueueError("队列条目的请求编号无效，拒绝继续。")


def ordered_entries(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the explicit, user-visible execution order used by workers."""
    return sorted(
        queue["entries"],
        key=lambda item: (
            int(item.get("position", 2**31 - 1)),
            str(item.get("id", "")),
        ),
    )


def ensure_entry_metadata(queue: dict[str, Any]) -> bool:
    """Safely enrich legacy V1 entries on their next protected write.

    The existing V1 schema remains readable by older tools.  New optional
    fields provide a visible position and requester record without changing
    completed downloads or their source/output identity.
    """
    changed = False
    assigned: set[int] = set()
    next_position = 10
    for entry in sorted(
        queue["entries"], key=lambda item: (int(item.get("priority", 100)), str(item.get("id", "")))
    ):
        position = entry.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position <= 0 or position in assigned:
            while next_position in assigned:
                next_position += 10
            entry["position"] = next_position
            position = next_position
            changed = True
        assigned.add(int(position))
        next_position = max(next_position, int(position) + 10)
        if not isinstance(entry.get("requested_by"), str) or not entry.get("requested_by", "").strip():
            entry["requested_by"] = "legacy-unattributed"
            changed = True
        if "request_id" not in entry:
            entry["request_id"] = None
            changed = True

    declared_next = queue.get("next_position")
    minimum_next = max(assigned, default=0) + 10
    if (
        not isinstance(declared_next, int)
        or isinstance(declared_next, bool)
        or declared_next < minimum_next
    ):
        queue["next_position"] = minimum_next
        changed = True
    return changed


def next_position(queue: dict[str, Any]) -> int:
    ensure_entry_metadata(queue)
    value = int(queue.get("next_position", 10))
    queue["next_position"] = value + 10
    return value


def summary(queue: dict[str, Any]) -> dict[str, Any]:
    metadata = queue_manifest_metadata(queue)
    counts = {state: 0 for state in sorted(ENTRY_STATES)}
    active: dict[str, Any] | None = None
    for entry in ordered_entries(queue):
        status = str(entry.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if status == "running":
            active = entry_projection(entry)
    return {
        "queue_state": queue.get("state"),
        "counts": counts,
        "active": active,
        "worker": queue.get("worker", {}),
        "updated_at": queue.get("updated_at"),
        "manifest_revision": metadata["revision"],
        "last_manifest_mutation": metadata.get("last_mutation"),
    }


def update_entry(entry: dict[str, Any], status: str, **fields: Any) -> None:
    if status not in ENTRY_STATES:
        raise QueueError("未知队列条目状态。")
    entry["status"] = status
    entry.update(fields)
    entry["updated_at"] = now()


def next_entry(queue: dict[str, Any]) -> dict[str, Any] | None:
    current = datetime.now(timezone.utc)
    eligible = [
        entry
        for entry in queue["entries"]
        if entry.get("status") == "queued"
        and ((retry_at := parse_timestamp(entry.get("next_attempt_after"))) is None or retry_at <= current)
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda item: (
            int(item.get("position", 2**31 - 1)),
            str(item.get("id", "")),
        ),
    )


def recover_interrupted_entries(queue: dict[str, Any]) -> list[str]:
    recovered: list[str] = []
    for entry in queue["entries"]:
        if entry.get("status") == "running":
            update_entry(
                entry,
                "queued",
                recovery_count=int(entry.get("recovery_count", 0)) + 1,
                recovered_at=now(),
            )
            recovered.append(str(entry.get("id")))
    return recovered


def read_control(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    control = read_json_object(path)
    if control is None or control.get("schema") != CONTROL_SCHEMA:
        raise QueueError("队列控制文件无效，未尝试覆盖。")
    if control.get("action") not in {"pause", "stop"}:
        raise QueueError("队列控制动作未知。")
    return control


def write_control(path: Path, action: str) -> None:
    atomic_write_json(
        path,
        {"schema": CONTROL_SCHEMA, "action": action, "requested_at": now()},
    )


def stop_exact_child_tree(process_id: int, log_path: Path) -> None:
    if os.name != "nt":
        return
    result = subprocess.run(
        ["taskkill", "/PID", str(process_id), "/T", "/F"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    detail = (result.stdout + result.stderr).replace("\r", " ").replace("\n", " | ").strip()
    append_log(log_path, f"已请求结束当前队列子进程树：{detail[-700:]}")


def wait_for_child(process: subprocess.Popen[bytes], control_path: Path, log_path: Path) -> tuple[int, bool]:
    immediate_stop = False
    while process.poll() is None:
        control = read_control(control_path)
        if control and control.get("action") == "stop":
            stop_exact_child_tree(process.pid, log_path)
            immediate_stop = True
            break
        time.sleep(2)
    return process.wait(), immediate_stop


def wait_until_or_control(retry_at: datetime, control_path: Path) -> bool:
    """Return false when a pause request arrived before the retry window ended."""
    while datetime.now(timezone.utc) < retry_at:
        if read_control(control_path):
            return False
        time.sleep(2)
    return True


def child_command(entry: dict[str, Any], args: argparse.Namespace) -> list[str]:
    command = [
        str(args.python),
        "-B",
        "-X",
        "utf8",
        str(args.downloader),
        "--url",
        str(entry["source_url"]),
        "--output",
        str(entry["output"]),
        "--execute",
        "--foreground",
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
    ]
    if entry.get("sha256"):
        command.extend(["--sha256", str(entry["sha256"])])
    return command


def find_entry(queue: dict[str, Any], entry_id: str) -> dict[str, Any] | None:
    for entry in queue["entries"]:
        if str(entry.get("id")) == entry_id:
            return entry
    return None


def idle_queue_state(queue: dict[str, Any]) -> str:
    if any(entry.get("status") == "queued" for entry in queue["entries"]):
        return "queued"
    if any(entry.get("status") == "blocked" for entry in queue["entries"]):
        return "attention_required"
    return "completed"


def runtime_preflight_error(args: argparse.Namespace) -> str | None:
    if not args.downloader.is_file() or args.downloader.is_symlink():
        return "后台运行时缺少单文件下载器；已保留所有未完成条目，未将其逐条标记为失败。"
    if not args.python.is_file() or args.python.is_symlink():
        return "后台运行时缺少固定 Python；已保留所有未完成条目，未将其逐条标记为失败。"
    manifest_path = getattr(args, "runtime_manifest", DEFAULT_RUNTIME_MANIFEST)
    runtime = runtime_manifest_status(manifest_path)
    if runtime.get("status") == "ready":
        expected_downloader = Path(str(runtime.get("downloader") or ""))
        if not same_path(expected_downloader, args.downloader):
            return "后台任务的下载器与受管版本化运行时不一致；已保留所有未完成条目，未将其逐条标记为失败。"
    elif manifest_path.exists():
        return "后台运行时清单不可用或脚本哈希不匹配；已保留所有未完成条目，未将其逐条标记为失败。"
    return None


def record_audit(
    args: argparse.Namespace,
    *,
    event: str,
    actor: str,
    queue: dict[str, Any] | None = None,
    entry: dict[str, Any] | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Audit must not turn a healthy transfer into a failed transfer."""
    try:
        audit_log = getattr(args, "audit_log", DEFAULT_AUDIT_LOG)
        append_audit_event(
            audit_log,
            event=event,
            queue_path=args.queue,
            actor=actor,
            queue=queue,
            entry=entry,
            request_id=(str(entry.get("request_id")) if entry and entry.get("request_id") else None),
            reason=reason,
            details=details,
        )
    except (OSError, QueueError) as exc:
        try:
            append_log(getattr(args, "log_file", DEFAULT_LOG), f"审计日志写入失败：{exc}")
        except OSError:
            pass


def mark_runtime_unavailable(
    args: argparse.Namespace,
    *,
    reason: str,
    entry_id: str | None = None,
) -> None:
    """Stop safely when the runtime changes underneath a scheduled worker."""
    affected: dict[str, Any] | None = None
    with create_queue_manifest_lease(args.queue, operation="runtime_unavailable") as write_lease:
        queue = read_queue(args.queue)
        if entry_id:
            affected = find_entry(queue, entry_id)
            if affected is not None and affected.get("status") == "running":
                update_entry(
                    affected,
                    "queued",
                    last_exit_code=RUNTIME_UNAVAILABLE_EXIT_CODE,
                    last_runtime_error=reason,
                    last_error=None,
                )
        queue.pop("active_entry_id", None)
        queue.pop("network_wait", None)
        queue["state"] = "runtime_unavailable"
        queue["runtime_error"] = {"at": now(), "reason": reason}
        worker = dict(queue.get("worker") or {})
        worker["child_pid"] = None
        queue["worker"] = worker
        save_queue(args.queue, queue, operation="runtime_unavailable", lease=write_lease)
    append_log(args.log_file, f"后台运行时不可用，队列安全停止：{reason}")
    record_audit(
        args,
        event="runtime_unavailable",
        actor="windows-task",
        queue=queue,
        entry=affected,
        reason=reason,
    )


def prepare_worker(args: argparse.Namespace) -> list[str]:
    with create_queue_manifest_lease(args.queue, operation="worker_start") as write_lease:
        queue = read_queue(args.queue)
        recovered = recover_interrupted_entries(queue)
        queue["state"] = "running"
        queue.pop("runtime_error", None)
        queue["worker"] = {
            "pid": os.getpid(),
            "started_at": now(),
            "child_pid": None,
            "max_concurrent_downloads": 1,
            "max_remote_chunks": 1,
        }
        save_queue(args.queue, queue, operation="worker_start", lease=write_lease)
    record_audit(
        args,
        event="worker_started",
        actor="windows-task",
        queue=queue,
        details={"recovered_entries": recovered},
    )
    return recovered


def pause_worker(args: argparse.Namespace, control: dict[str, Any]) -> None:
    with create_queue_manifest_lease(args.queue, operation="worker_pause") as write_lease:
        queue = read_queue(args.queue)
        queue["state"] = "paused"
        queue.pop("active_entry_id", None)
        worker = dict(queue.get("worker") or {})
        worker["child_pid"] = None
        queue["worker"] = worker
        save_queue(args.queue, queue, operation="worker_pause", lease=write_lease)
    append_log(args.log_file, f"队列响应 {control['action']} 请求并停止。")
    record_audit(
        args,
        event="worker_paused",
        actor="windows-task",
        queue=queue,
        reason=str(control["action"]),
    )


def claim_next_entry(args: argparse.Namespace) -> tuple[str, dict[str, Any] | None]:
    runtime_error = runtime_preflight_error(args)
    if runtime_error:
        mark_runtime_unavailable(args, reason=runtime_error)
        return "runtime_unavailable", None

    with create_queue_manifest_lease(args.queue, operation="worker_claim") as write_lease:
        queue = read_queue(args.queue)
        entry = next_entry(queue)
        if entry is None:
            queue.pop("active_entry_id", None)
            queue.pop("network_wait", None)
            worker = dict(queue.get("worker") or {})
            worker["child_pid"] = None
            worker["finished_at"] = now()
            queue["worker"] = worker
            queue["state"] = idle_queue_state(queue)
            save_queue(args.queue, queue, operation="worker_idle", lease=write_lease)
            return "idle", None

        update_entry(entry, "running", started_at=now(), last_error=None, last_runtime_error=None)
        queue["active_entry_id"] = entry["id"]
        queue.pop("network_wait", None)
        queue["state"] = "running"
        save_queue(args.queue, queue, operation="worker_claim", lease=write_lease)
        claimed = copy.deepcopy(entry)

    append_log(args.log_file, f"开始条目 {claimed['id']}；单工作者模式。")
    record_audit(args, event="entry_started", actor="windows-task", queue=queue, entry=claimed)
    return "claimed", claimed


def record_child_start(args: argparse.Namespace, entry_id: str, child_pid: int) -> None:
    with create_queue_manifest_lease(args.queue, operation="worker_child_start") as write_lease:
        queue = read_queue(args.queue)
        entry = find_entry(queue, entry_id)
        if entry is None or entry.get("status") != "running":
            raise QueueError("当前条目状态已变化，拒绝把子进程交给错误任务。")
        worker = dict(queue.get("worker") or {})
        worker["child_pid"] = child_pid
        worker["child_started_at"] = now()
        queue["worker"] = worker
        save_queue(args.queue, queue, operation="worker_child_start", lease=write_lease)


def finish_child(
    args: argparse.Namespace,
    *,
    entry_id: str,
    exit_code: int,
    hard_stopped: bool,
) -> tuple[str, datetime | None]:
    """Persist one child result against a freshly loaded manifest.

    This is intentionally the only point that advances a queue entry.  If the
    downloader vanishes during a CC Switch refresh, the running item is put
    back to ``queued`` and the worker stops, instead of blocking every later
    item with the same infrastructure failure.
    """
    event = "entry_failed"
    message = ""
    retry_at: datetime | None = None
    with create_queue_manifest_lease(args.queue, operation="worker_result") as write_lease:
        queue = read_queue(args.queue)
        entry = find_entry(queue, entry_id)
        if entry is None or entry.get("status") != "running":
            raise QueueError("找不到当前运行条目或其状态已变化，拒绝覆盖结果。")
        worker = dict(queue.get("worker") or {})
        worker["child_pid"] = None
        queue["worker"] = worker
        queue.pop("active_entry_id", None)
        control = read_control(args.control_file)

        if exit_code == 0:
            update_entry(entry, "completed", completed_at=now(), last_exit_code=0, last_error=None)
            event = "entry_completed"
            message = f"条目完成 {entry['id']}。"
            action = "continue"
        elif exit_code == TEMPORARY_TRANSFER_EXIT_CODE:
            failures = int(entry.get("network_failures", 0)) + 1
            delay = min(args.retry_delay_seconds * (2 ** max(failures - 1, 0)), MAX_RETRY_DELAY_SECONDS)
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            update_entry(
                entry,
                "queued",
                network_failures=failures,
                next_attempt_after=retry_at.isoformat(),
                last_exit_code=exit_code,
                last_transient_error="传输暂不可用；已保留断点并后台等待。",
            )
            queue["state"] = "waiting_for_network"
            queue["network_wait"] = {"retry_after": retry_at.isoformat(), "entry_id": entry["id"]}
            event = "entry_waiting_for_network"
            message = f"条目暂时网络不可用；等待至 {retry_at.isoformat()} 后续传。"
            action = "wait"
        elif hard_stopped or control:
            update_entry(entry, "queued", last_exit_code=exit_code, paused_at=now())
            queue["state"] = "paused"
            event = "entry_paused"
            message = f"条目在安全续传状态暂停：{entry['id']}。"
            action = "stop"
        elif (runtime_error := runtime_preflight_error(args)) is not None:
            update_entry(
                entry,
                "queued",
                last_exit_code=RUNTIME_UNAVAILABLE_EXIT_CODE,
                last_runtime_error=runtime_error,
                last_error=None,
            )
            queue["state"] = "runtime_unavailable"
            queue["runtime_error"] = {"at": now(), "reason": runtime_error}
            event = "runtime_unavailable"
            message = f"后台运行时不可用，当前条目保留待续传：{entry['id']}。"
            action = "runtime_unavailable"
        elif exit_code == ACTIVE_TRANSFER_EXIT_CODE:
            update_entry(
                entry,
                "blocked",
                last_exit_code=exit_code,
                last_error="同一输出路径已有独立传输，队列未重复启动。",
            )
            event = "entry_blocked_output_lock"
            message = f"条目被输出路径锁阻止，等待人工确认：{entry['id']}。"
            action = "continue"
        else:
            update_entry(
                entry,
                "blocked",
                last_exit_code=exit_code,
                last_error="传输失败；请查看本地脱敏队列日志后人工处理。",
            )
            event = "entry_blocked_transfer_error"
            message = f"条目失败并转为人工处理：{entry['id']} exit={exit_code}。"
            action = "continue"

        save_queue(args.queue, queue, operation="worker_result", lease=write_lease)
        snapshot = copy.deepcopy(entry)

    append_log(args.log_file, message)
    record_audit(args, event=event, actor="windows-task", queue=queue, entry=snapshot, reason=message)
    return action, retry_at


def finalize_worker(args: argparse.Namespace) -> None:
    with create_queue_manifest_lease(args.queue, operation="worker_stop") as write_lease:
        queue = read_queue(args.queue)
        worker = dict(queue.get("worker") or {})
        if worker.get("pid") == os.getpid() or worker.get("pid") is None:
            queue["worker"] = {
                "pid": None,
                "stopped_at": now(),
                "max_concurrent_downloads": 1,
                "max_remote_chunks": 1,
            }
        queue.pop("active_entry_id", None)
        if queue.get("state") == "running":
            queue["state"] = idle_queue_state(queue)
        save_queue(args.queue, queue, operation="worker_stop", lease=write_lease)
    record_audit(args, event="worker_stopped", actor="windows-task", queue=queue)


def run_queue_with_lock(args: argparse.Namespace, lock: QueueLock) -> int:
    worker_started = False
    try:
        preflight = runtime_preflight_error(args)
        if preflight:
            mark_runtime_unavailable(args, reason=preflight)
            return RUNTIME_UNAVAILABLE_EXIT_CODE

        recovered = prepare_worker(args)
        worker_started = True
        if recovered:
            append_log(args.log_file, f"已恢复意外停止的条目：{', '.join(recovered)}；断点保持不变。")

        while True:
            control = read_control(args.control_file)
            if control:
                pause_worker(args, control)
                return ACTIVE_TRANSFER_EXIT_CODE

            state, entry = claim_next_entry(args)
            if state == "runtime_unavailable":
                return RUNTIME_UNAVAILABLE_EXIT_CODE
            if state == "idle":
                append_log(args.log_file, "队列没有剩余可启动的条目。")
                return 0
            assert entry is not None

            second_preflight = runtime_preflight_error(args)
            if second_preflight:
                mark_runtime_unavailable(args, reason=second_preflight, entry_id=str(entry["id"]))
                return RUNTIME_UNAVAILABLE_EXIT_CODE

            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with args.log_file.open("ab") as log_handle:
                    process = subprocess.Popen(
                        child_command(entry, args),
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                    )
                    record_child_start(args, str(entry["id"]), process.pid)
                    exit_code, hard_stopped = wait_for_child(process, args.control_file, args.log_file)
            except OSError as exc:
                mark_runtime_unavailable(
                    args,
                    reason=f"无法启动后台单文件下载器：{exc}",
                    entry_id=str(entry["id"]),
                )
                return RUNTIME_UNAVAILABLE_EXIT_CODE

            action, retry_at = finish_child(
                args,
                entry_id=str(entry["id"]),
                exit_code=exit_code,
                hard_stopped=hard_stopped,
            )
            if action == "runtime_unavailable":
                return RUNTIME_UNAVAILABLE_EXIT_CODE
            if action == "stop":
                return ACTIVE_TRANSFER_EXIT_CODE
            if action == "wait":
                assert retry_at is not None
                if not wait_until_or_control(retry_at, args.control_file):
                    continue
    finally:
        if worker_started:
            finalize_worker(args)
        lock.release()


def run_queue(args: argparse.Namespace) -> int:
    if not args.worker_token:
        raise QueueError("run 只能由 start 创建的后台工作进程调用。")
    lock = claim_queue_lock(args.queue, args.worker_token)
    return run_queue_with_lock(args, lock)


def scheduled_run_queue(args: argparse.Namespace) -> int:
    """Windows Task Scheduler entry: persistent worker, never a Codex child process."""
    if not args.queue.exists():
        return 0
    existing_owner = read_json_object(queue_lock_path(args.queue)) or {}
    if process_is_running(existing_owner.get("pid")):
        # A manual start or an earlier scheduled instance is already the sole
        # worker.  Treat this periodic wake-up as a harmless no-op.
        return 0
    lock = create_queue_lock(args.queue, recover_stale=True, phase="scheduled_running")
    return run_queue_with_lock(args, lock)


def mutation_actor(args: argparse.Namespace) -> tuple[str, str | None]:
    actor = str(getattr(args, "requested_by", "") or "").strip()
    if not actor:
        raise QueueError("变更队列必须提供 --requested-by，便于多任务审计与冲突追踪。")
    if len(actor) > 120:
        raise QueueError("请求方标识过长，拒绝写入审计清单。")
    request_id_value = getattr(args, "request_id", None)
    request_id = str(request_id_value).strip() if request_id_value else None
    if request_id is not None and len(request_id) > 160:
        raise QueueError("请求编号过长，拒绝写入审计清单。")
    return actor, request_id


def state_after_mutation(queue: dict[str, Any]) -> str:
    if any(entry.get("status") == "running" for entry in queue["entries"]):
        return "running"
    return idle_queue_state(queue)


def find_output_entry(queue: dict[str, Any], output: Path) -> dict[str, Any] | None:
    for existing in queue["entries"]:
        existing_output = existing.get("output")
        if isinstance(existing_output, str) and same_path(Path(existing_output), output):
            return existing
    return None


def make_entry(args: argparse.Namespace, queue: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    actor, request_id = mutation_actor(args)
    parts = urlsplit(args.url)
    if parts.query or parts.fragment:
        raise QueueError("带查询参数或签名的链接不能写入持久队列；请改用单文件 --url-stdin 后台模式。")
    request = validate_request(args.url, requested_output_path(args), args.sha256, allow_http=False)
    if request.source_url != request.safe_url:
        raise QueueError("持久队列只保存无查询参数的来源链接。")
    existing = find_output_entry(queue, request.output)
    if existing is not None:
        existing_sha256 = str(existing.get("sha256") or "")
        requested_sha256 = str(request.expected_sha256 or "")
        if str(existing.get("source_url")) == request.safe_url and existing_sha256 == requested_sha256:
            return existing, False
        raise QueueError("该输出路径已经由不同来源或校验值占用，拒绝重复入队。")
    entry_id = hashlib.sha256(f"{request.output}\0{request.safe_url}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"{int(args.priority):04d}-{entry_id}",
        "priority": int(args.priority),
        "position": next_position(queue),
        "source_url": request.safe_url,
        "output": str(request.output),
        "sha256": request.expected_sha256,
        "status": "queued",
        "created_at": now(),
        "updated_at": now(),
        "network_failures": 0,
        "requested_by": actor,
        "request_id": request_id,
    }, True


def enqueue(args: argparse.Namespace) -> int:
    actor, request_id = mutation_actor(args)
    with create_queue_manifest_lease(args.queue, operation="enqueue") as write_lease:
        queue = read_queue(args.queue, allow_missing=True)
        entry, created = make_entry(args, queue)
        if created:
            queue["entries"].append(entry)
            queue["state"] = state_after_mutation(queue)
            save_queue(args.queue, queue, operation="enqueue", lease=write_lease)
        snapshot = copy.deepcopy(entry)
    record_audit(
        args,
        event="entry_enqueued" if created else "entry_enqueue_idempotent",
        actor=actor,
        queue=queue,
        entry=snapshot,
        details={"request_id": request_id},
    )
    print(
        json.dumps(
            {"status": "queued" if created else "already_queued", "entry_id": entry["id"], **summary(queue)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def require_entry_id(queue: dict[str, Any], entry_id: str) -> dict[str, Any]:
    entry = find_entry(queue, entry_id)
    if entry is None:
        raise QueueError("找不到指定队列条目；请先用 list 获取精确条目编号。")
    return entry


def remove(args: argparse.Namespace) -> int:
    actor, request_id = mutation_actor(args)
    reason = str(args.reason or "").strip()
    if not reason:
        raise QueueError("删除条目必须提供 --reason，便于后续追溯。")
    with create_queue_manifest_lease(args.queue, operation="remove") as write_lease:
        queue = read_queue(args.queue)
        entry = require_entry_id(queue, args.id)
        if entry.get("status") == "running":
            raise QueueError("正在传输的条目不能删除；请先安全暂停后再处理。")
        if entry.get("status") == "completed" and not args.allow_completed:
            raise QueueError("已完成条目默认保留作为交付记录；如确需移除，请明确使用 --allow-completed。")
        snapshot = copy.deepcopy(entry)
        queue["entries"] = [candidate for candidate in queue["entries"] if candidate is not entry]
        queue["state"] = state_after_mutation(queue)
        save_queue(args.queue, queue, operation="remove", lease=write_lease)
    record_audit(
        args,
        event="entry_removed",
        actor=actor,
        queue=queue,
        entry=snapshot,
        reason=reason,
        details={"request_id": request_id},
    )
    print(json.dumps({"status": "removed", "entry_id": snapshot["id"], **summary(queue)}, ensure_ascii=False, indent=2))
    return 0


def move(args: argparse.Namespace) -> int:
    actor, request_id = mutation_actor(args)
    reason = str(args.reason or "").strip()
    if not reason:
        raise QueueError("调整顺序必须提供 --reason，便于后续追溯。")
    with create_queue_manifest_lease(args.queue, operation="move") as write_lease:
        queue = read_queue(args.queue)
        entry = require_entry_id(queue, args.id)
        reference_id = args.before or args.after
        reference = require_entry_id(queue, reference_id)
        if entry is reference:
            raise QueueError("不能把条目移动到自身前后。")
        if entry.get("status") not in MUTABLE_ENTRY_STATES:
            raise QueueError("只有排队中或待人工处理的条目可以调整顺序。")
        if reference.get("status") not in MUTABLE_ENTRY_STATES:
            raise QueueError("参照条目必须是排队中或待人工处理的条目。")

        ordering = [candidate for candidate in ordered_entries(queue) if candidate.get("status") in MUTABLE_ENTRY_STATES]
        ordering.remove(entry)
        reference_index = ordering.index(reference)
        insert_at = reference_index if args.before else reference_index + 1
        ordering.insert(insert_at, entry)
        entry["priority"] = int(reference.get("priority", 100))
        terminal_entries = [
            candidate for candidate in ordered_entries(queue) if candidate.get("status") not in MUTABLE_ENTRY_STATES
        ]
        for index, candidate in enumerate([*terminal_entries, *ordering], start=1):
            candidate["position"] = index * 10
        queue["next_position"] = max(
            [int(candidate.get("position", 0)) for candidate in queue["entries"]] or [0]
        ) + 10
        entry["reordered_at"] = now()
        entry["reorder_reason"] = reason
        queue["state"] = state_after_mutation(queue)
        save_queue(args.queue, queue, operation="move", lease=write_lease)
        snapshot = copy.deepcopy(entry)
    record_audit(
        args,
        event="entry_moved",
        actor=actor,
        queue=queue,
        entry=snapshot,
        reason=reason,
        details={"before": args.before, "after": args.after, "request_id": request_id},
    )
    print(json.dumps({"status": "moved", "entry_id": snapshot["id"], **summary(queue)}, ensure_ascii=False, indent=2))
    return 0


def retry(args: argparse.Namespace) -> int:
    actor, request_id = mutation_actor(args)
    reason = str(args.reason or "").strip()
    if not reason:
        raise QueueError("重新排队必须提供 --reason，便于后续追溯。")
    with create_queue_manifest_lease(args.queue, operation="retry") as write_lease:
        queue = read_queue(args.queue)
        if args.all_blocked:
            selected = [entry for entry in queue["entries"] if entry.get("status") == "blocked"]
            if not selected:
                raise QueueError("没有待人工处理的条目可重新排队。")
        else:
            entry = require_entry_id(queue, args.id)
            if entry.get("status") != "blocked":
                raise QueueError("只有待人工处理的条目可以重新排队。")
            selected = [entry]

        snapshots: list[dict[str, Any]] = []
        for entry in selected:
            previous_error = entry.get("last_error")
            update_entry(
                entry,
                "queued",
                retry_count=int(entry.get("retry_count", 0)) + 1,
                retry_requested_at=now(),
                retry_requested_by=actor,
                retry_reason=reason,
                previous_error=previous_error,
                last_error=None,
                next_attempt_after=None,
            )
            snapshots.append(copy.deepcopy(entry))
        queue["state"] = state_after_mutation(queue)
        save_queue(args.queue, queue, operation="retry", lease=write_lease)
    for snapshot in snapshots:
        record_audit(
            args,
            event="entry_requeued",
            actor=actor,
            queue=queue,
            entry=snapshot,
            reason=reason,
            details={"request_id": request_id},
        )
    print(
        json.dumps(
            {
                "status": "requeued",
                "requeued_count": len(snapshots),
                "entry_ids": [entry["id"] for entry in snapshots[:DEFAULT_VIEW_LIMIT]],
                "entry_ids_truncated": max(0, len(snapshots) - DEFAULT_VIEW_LIMIT),
                **summary(queue),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def list_entries(args: argparse.Namespace) -> int:
    queue = read_queue(args.queue, allow_missing=True)
    limit = int(getattr(args, "limit", DEFAULT_VIEW_LIMIT))
    offset = int(getattr(args, "offset", 0))
    if limit <= 0 or limit > MAX_VIEW_LIMIT:
        raise QueueError(f"--limit 必须在 1 到 {MAX_VIEW_LIMIT} 之间。")
    if offset < 0:
        raise QueueError("--offset 不能小于 0。")
    states = set(args.state or [])
    requested_id = str(getattr(args, "id", "") or "").strip()
    if requested_id:
        entries = [require_entry_id(queue, requested_id)]
    else:
        entries = ordered_entries(queue)
    entries = [entry for entry in entries if not states or entry.get("status") in states]
    page = entries[offset : offset + limit]
    next_offset = offset + len(page)
    print(
        json.dumps(
            {
                "status": "ok" if args.queue.exists() else "not_initialized",
                "queue": str(args.queue),
                "total_matching": len(entries),
                "offset": offset,
                "limit": limit,
                "next_offset": next_offset if next_offset < len(entries) else None,
                "entries": [entry_projection(entry) for entry in page],
                **summary(queue),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def show_audit(args: argparse.Namespace) -> int:
    if args.limit <= 0 or args.limit > MAX_VIEW_LIMIT:
        raise QueueError(f"--limit 必须在 1 到 {MAX_VIEW_LIMIT} 之间。")
    if not args.audit_log.exists():
        print(json.dumps({"status": "not_initialized", "events": []}, ensure_ascii=False, indent=2))
        return 0
    assert_plain_file(args.audit_log, description="审计日志")
    events: list[dict[str, Any]] = []
    for line in args.audit_log.read_text(encoding="utf-8-sig").splitlines()[-args.limit :]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("schema") == AUDIT_SCHEMA:
            events.append(parsed)
    print(json.dumps({"status": "ok", "audit_log": str(args.audit_log), "events": events}, ensure_ascii=False, indent=2))
    return 0


def same_path(left: Path, right: Path) -> bool:
    """Compare paths without requiring either side to exist yet."""
    return str(left.resolve(strict=False)).casefold() == str(right.resolve(strict=False)).casefold()


def run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["schtasks.exe", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError("无法调用 Windows 计划任务；未启动任何下载。") from exc


def start(args: argparse.Namespace) -> int:
    """Ask the installed Windows task to own the long-running queue.

    Deliberately do not spawn a detached child here.  This keeps the download
    process outside the Codex command tree and gives it the ComfyUI-proven
    logon/periodic wake-up path for automatic recovery after a network loss.
    """
    read_queue(args.queue)
    if not same_path(args.queue, DEFAULT_QUEUE):
        raise QueueError("受管后台任务只服务默认通用队列；自定义队列只能用于离线检查。")
    owner = read_json_object(queue_lock_path(args.queue)) or {}
    if process_is_running(owner.get("pid")):
        print(
            json.dumps(
                {"status": "already_running", "worker_pid": owner.get("pid"), "queue": str(args.queue)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if os.name != "nt":
        raise QueueError("持久队列只能通过已安装的 Windows 计划任务启动。")

    runtime = runtime_manifest_status(getattr(args, "runtime_manifest", DEFAULT_RUNTIME_MANIFEST))
    if runtime.get("status") != "ready":
        raise QueueError("受管后台运行时未就绪；请先用安装脚本生成并核验版本化运行时，当前未启动下载。")

    task_name = WINDOWS_TASK_FULL_NAME
    check = run_schtasks(["/Query", "/TN", task_name])
    if check.returncode != 0:
        raise QueueError(
            "通用下载后台任务尚未安装；当前不会退回到 Codex 子进程。"
            "请在获得明确授权后安装该 skill 提供的精确任务。"
        )
    launch = run_schtasks(["/Run", "/TN", task_name])
    if launch.returncode != 0:
        raise QueueError("Windows 计划任务未能接受启动请求；未启动任何下载。")
    print(
        json.dumps(
            {
                "status": "scheduled",
                "task": task_name,
                "queue": str(args.queue),
                "message": "已请求 Windows 后台任务处理队列；请用 status 只读轮询。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def pause(args: argparse.Namespace) -> int:
    action = "stop" if args.immediate else "pause"
    write_control(args.control_file, action)
    print(json.dumps({"status": "pause_requested", "action": action}, ensure_ascii=False))
    return 0


def resume(args: argparse.Namespace) -> int:
    try:
        args.control_file.unlink(missing_ok=True)
    except OSError as exc:
        raise QueueError("无法移除队列暂停控制文件。") from exc
    print(json.dumps({"status": "resumed", "message": "控制已解除；如无工作进程，请再运行 start。"}, ensure_ascii=False))
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_manifest_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_installed", "manifest": str(path)}
    if path.is_symlink() or not path.is_file():
        return {"status": "invalid_manifest", "manifest": str(path)}
    manifest = read_json_object(path)
    if manifest is None or manifest.get("schema") != "MeifuDownloadRuntimeV1":
        return {"status": "invalid_manifest", "manifest": str(path)}
    queue_script = Path(str(manifest.get("queue_script") or ""))
    downloader = Path(str(manifest.get("downloader") or ""))
    ready = queue_script.is_file() and not queue_script.is_symlink() and downloader.is_file() and not downloader.is_symlink()
    expected_queue_hash = str(manifest.get("queue_script_sha256") or "")
    expected_downloader_hash = str(manifest.get("downloader_sha256") or "")
    hashes_match = False
    if ready and expected_queue_hash and expected_downloader_hash:
        try:
            hashes_match = sha256_file(queue_script) == expected_queue_hash and sha256_file(downloader) == expected_downloader_hash
        except OSError:
            hashes_match = False
    return {
        "status": "ready" if ready and hashes_match else "unavailable",
        "manifest": str(path),
        "runtime_dir": manifest.get("runtime_dir"),
        "queue_script": str(queue_script),
        "downloader": str(downloader),
        "hashes_match": hashes_match,
        "installed_at": manifest.get("installed_at"),
    }


def status(args: argparse.Namespace) -> int:
    queue = read_queue(args.queue, allow_missing=True)
    owner = read_json_object(queue_lock_path(args.queue)) or {}
    manifest_owner = read_json_object(queue_manifest_lock_path(args.queue)) or {}
    payload = {
        "status": "ok" if args.queue.exists() else "not_initialized",
        "worker_lock_active": process_is_running(owner.get("pid")),
        "worker_pid": owner.get("pid") if isinstance(owner.get("pid"), int) else None,
        "manifest_write_active": process_is_running(manifest_owner.get("pid")),
        "manifest_write_pid": manifest_owner.get("pid") if isinstance(manifest_owner.get("pid"), int) else None,
        "manifest_write_operation": manifest_owner.get("operation"),
        "control": read_control(args.control_file),
        "audit_log": str(args.audit_log),
        "runtime": runtime_manifest_status(args.runtime_manifest),
        **summary(queue),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def add_common_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    parser.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    parser.add_argument("--downloader", type=Path, default=DEFAULT_DOWNLOADER)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    parser.add_argument("--chunk-gib", type=float, default=DEFAULT_CHUNK_GIB)
    parser.add_argument("--remote-reserve-gib", type=float, default=DEFAULT_REMOTE_RESERVE_GIB)
    parser.add_argument("--local-reserve-gib", type=float, default=DEFAULT_LOCAL_RESERVE_GIB)
    parser.add_argument("--retry-delay-seconds", type=int, default=DEFAULT_RETRY_DELAY_SECONDS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = subparsers.add_parser("enqueue", help="把无签名 HTTPS 直链加入持久单工作队列")
    enqueue_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    enqueue_parser.add_argument("--url", required=True)
    enqueue_parser.add_argument("--output", help="最终文件的绝对路径。")
    enqueue_parser.add_argument("--storage-root", help="可配置的绝对存储根目录。")
    enqueue_parser.add_argument("--target", help="存储根目录下的相对文件目标。")
    enqueue_parser.add_argument("--sha256")
    enqueue_parser.add_argument("--priority", type=int, default=100)
    enqueue_parser.add_argument("--requested-by", required=True, help="发起本次入队的稳定任务或智能体标识。")
    enqueue_parser.add_argument("--request-id", help="可选的外部请求编号；不得包含签名链接或凭据。")
    enqueue_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    enqueue_parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    enqueue_parser.set_defaults(handler=enqueue)

    start_parser = subparsers.add_parser("start", help="请求 Windows 后台任务处理默认通用队列")
    start_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    start_parser.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    start_parser.set_defaults(handler=start)

    run_parser = subparsers.add_parser("run", help=argparse.SUPPRESS)
    add_common_runtime_arguments(run_parser)
    run_parser.add_argument("--_worker-token", dest="worker_token", help=argparse.SUPPRESS)
    run_parser.set_defaults(handler=run_queue)

    scheduled_run_parser = subparsers.add_parser("scheduled-run", help=argparse.SUPPRESS)
    add_common_runtime_arguments(scheduled_run_parser)
    scheduled_run_parser.set_defaults(handler=scheduled_run_queue)

    pause_parser = subparsers.add_parser("pause", help="请求安全暂停；--immediate 仅结束当前精确子进程树")
    pause_parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
    pause_parser.add_argument("--immediate", action="store_true")
    pause_parser.set_defaults(handler=pause)

    resume_parser = subparsers.add_parser("resume", help="移除暂停控制")
    resume_parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
    resume_parser.set_defaults(handler=resume)

    remove_parser = subparsers.add_parser("remove", help="从队列移除一个未运行条目，并保留审计记录")
    remove_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    remove_parser.add_argument("--id", required=True, help="list 返回的精确条目编号。")
    remove_parser.add_argument("--reason", required=True, help="删除原因。")
    remove_parser.add_argument("--allow-completed", action="store_true", help="明确允许移除已完成交付记录。")
    remove_parser.add_argument("--requested-by", required=True, help="执行删除的稳定任务或智能体标识。")
    remove_parser.add_argument("--request-id", help="可选的外部请求编号。")
    remove_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    remove_parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    remove_parser.set_defaults(handler=remove)

    move_parser = subparsers.add_parser("move", help="把一个排队中或待处理条目移动到另一条目之前或之后")
    move_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    move_parser.add_argument("--id", required=True, help="要移动的精确条目编号。")
    move_group = move_parser.add_mutually_exclusive_group(required=True)
    move_group.add_argument("--before", help="移动到该精确条目之前。")
    move_group.add_argument("--after", help="移动到该精确条目之后。")
    move_parser.add_argument("--reason", required=True, help="调整顺序原因。")
    move_parser.add_argument("--requested-by", required=True, help="执行排序的稳定任务或智能体标识。")
    move_parser.add_argument("--request-id", help="可选的外部请求编号。")
    move_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    move_parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    move_parser.set_defaults(handler=move)

    retry_parser = subparsers.add_parser("retry", help="把明确指定的待人工处理条目安全恢复为排队状态")
    retry_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    retry_group = retry_parser.add_mutually_exclusive_group(required=True)
    retry_group.add_argument("--id", help="要重新排队的精确条目编号。")
    retry_group.add_argument("--all-blocked", action="store_true", help="明确恢复所有待人工处理条目。")
    retry_parser.add_argument("--reason", required=True, help="重新排队原因。")
    retry_parser.add_argument("--requested-by", required=True, help="执行恢复的稳定任务或智能体标识。")
    retry_parser.add_argument("--request-id", help="可选的外部请求编号。")
    retry_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    retry_parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    retry_parser.set_defaults(handler=retry)

    list_parser = subparsers.add_parser("list", help="只读分页列出任务编号、来源链接、输出位置、顺序和状态")
    list_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    list_parser.add_argument("--state", action="append", choices=sorted(ENTRY_STATES), help="可重复指定，用于按状态筛选。")
    list_parser.add_argument("--id", help="只显示一个精确条目编号；可与 --state 组合验证状态。")
    list_parser.add_argument("--limit", type=int, default=DEFAULT_VIEW_LIMIT, help=f"每页条目数，默认 {DEFAULT_VIEW_LIMIT}，最多 {MAX_VIEW_LIMIT}。")
    list_parser.add_argument("--offset", type=int, default=0, help="从第几个匹配条目开始，默认 0。")
    list_parser.set_defaults(handler=list_entries)

    audit_parser = subparsers.add_parser("audit", help="只读查看最近的队列增删排序与工作者审计记录")
    audit_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    audit_parser.add_argument("--limit", type=int, default=DEFAULT_VIEW_LIMIT, help=f"最近事件数，默认 {DEFAULT_VIEW_LIMIT}，最多 {MAX_VIEW_LIMIT}。")
    audit_parser.set_defaults(handler=show_audit)

    status_parser = subparsers.add_parser("status", help="只读查看本地队列状态")
    status_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    status_parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
    status_parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG)
    status_parser.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    status_parser.set_defaults(handler=status)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if getattr(args, "retry_delay_seconds", DEFAULT_RETRY_DELAY_SECONDS) <= 0:
            raise QueueError("重试等待时间必须大于 0。")
        if getattr(args, "chunk_gib", DEFAULT_CHUNK_GIB) <= 0:
            raise QueueError("分块大小必须大于 0。")
        return int(args.handler(args))
    except (QueueError, DownloadError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        detail = redact_urls(str(exc))
        log_file = getattr(args, "log_file", None)
        if isinstance(log_file, Path):
            try:
                append_log(log_file, f"队列命令失败：{detail}")
            except OSError:
                pass
        print(json.dumps({"status": "failed", "error": detail}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
