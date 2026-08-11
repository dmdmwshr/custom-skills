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
LOCK_INITIALIZATION_GRACE_SECONDS = 120
DEFAULT_RETRY_DELAY_SECONDS = 15 * 60
MAX_RETRY_DELAY_SECONDS = 6 * 60 * 60


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


def queue_revision(queue: dict[str, Any]) -> int:
    return int(queue_manifest_metadata(queue)["revision"])


def queue_template() -> dict[str, Any]:
    return {
        "schema": QUEUE_SCHEMA,
        "state": "queued",
        "created_at": now(),
        "updated_at": now(),
        "entries": [],
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


def summary(queue: dict[str, Any]) -> dict[str, Any]:
    metadata = queue_manifest_metadata(queue)
    counts = {state: 0 for state in sorted(ENTRY_STATES)}
    active: dict[str, Any] | None = None
    for entry in queue["entries"]:
        status = str(entry.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if status == "running":
            active = {"id": entry.get("id"), "output": entry.get("output")}
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
    return min(eligible, key=lambda item: (int(item.get("priority", 0)), str(item.get("id", ""))))


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


def run_queue_with_lock(args: argparse.Namespace, lock: QueueLock) -> int:
    if not args.downloader.is_file():
        raise QueueError("找不到通用单文件下载器。")
    queue: dict[str, Any] | None = None
    try:
        # The worker lock is acquired before this short manifest lease.  An
        # enqueue that was already in flight therefore commits first; the
        # worker then reads that newest revision before taking responsibility.
        with create_queue_manifest_lease(args.queue, operation="worker_start") as write_lease:
            queue = read_queue(args.queue)
            recovered = recover_interrupted_entries(queue)
            queue["state"] = "running"
            queue["worker"] = {
                "pid": os.getpid(),
                "started_at": now(),
                "child_pid": None,
                "max_concurrent_downloads": 1,
                "max_remote_chunks": 1,
            }
            save_queue(args.queue, queue, operation="worker_start", lease=write_lease)
        if recovered:
            append_log(args.log_file, f"已恢复意外停止的条目：{', '.join(recovered)}；断点保持不变。")

        while True:
            control = read_control(args.control_file)
            if control:
                queue["state"] = "paused"
                queue["worker"]["child_pid"] = None
                save_queue(args.queue, queue)
                append_log(args.log_file, f"队列响应 {control['action']} 请求并停止。")
                return ACTIVE_TRANSFER_EXIT_CODE

            entry = next_entry(queue)
            if entry is None:
                queue["worker"]["child_pid"] = None
                queue["worker"]["finished_at"] = now()
                queue["state"] = "attention_required" if any(
                    item.get("status") == "blocked" for item in queue["entries"]
                ) else "completed"
                save_queue(args.queue, queue)
                append_log(args.log_file, "队列没有剩余可启动的条目。")
                return 0

            update_entry(entry, "running", started_at=now(), last_error=None)
            queue["active_entry_id"] = entry["id"]
            save_queue(args.queue, queue)
            append_log(args.log_file, f"开始条目 {entry['id']}；单工作者模式。")
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            with args.log_file.open("ab") as log_handle:
                process = subprocess.Popen(
                    child_command(entry, args),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
                queue["worker"]["child_pid"] = process.pid
                queue["worker"]["child_started_at"] = now()
                save_queue(args.queue, queue)
                exit_code, hard_stopped = wait_for_child(process, args.control_file, args.log_file)

            queue["worker"]["child_pid"] = None
            control = read_control(args.control_file)
            if exit_code == 0:
                update_entry(entry, "completed", completed_at=now(), last_exit_code=0)
                append_log(args.log_file, f"条目完成 {entry['id']}。")
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
                save_queue(args.queue, queue)
                append_log(args.log_file, f"条目暂时网络不可用；等待至 {retry_at.isoformat()} 后续传。")
                if not wait_until_or_control(retry_at, args.control_file):
                    continue
                queue["state"] = "running"
            elif exit_code == ACTIVE_TRANSFER_EXIT_CODE:
                update_entry(
                    entry,
                    "blocked",
                    last_exit_code=exit_code,
                    last_error="同一输出路径已有独立传输，队列未重复启动。",
                )
                append_log(args.log_file, f"条目被输出路径锁阻止，等待人工确认：{entry['id']}。")
            elif hard_stopped or control:
                update_entry(entry, "queued", last_exit_code=exit_code, paused_at=now())
                queue["state"] = "paused"
                save_queue(args.queue, queue)
                append_log(args.log_file, f"条目在安全续传状态暂停：{entry['id']}。")
                return ACTIVE_TRANSFER_EXIT_CODE
            else:
                update_entry(
                    entry,
                    "blocked",
                    last_exit_code=exit_code,
                    last_error="传输失败；请查看本地脱敏队列日志后人工处理。",
                )
                append_log(args.log_file, f"条目失败并转为人工处理：{entry['id']} exit={exit_code}。")
            save_queue(args.queue, queue)
    finally:
        if queue is not None:
            queue["worker"] = {
                "pid": None,
                "stopped_at": now(),
                "max_concurrent_downloads": 1,
                "max_remote_chunks": 1,
            }
            queue.pop("active_entry_id", None)
            save_queue(args.queue, queue)
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


def make_entry(args: argparse.Namespace, queue: dict[str, Any]) -> dict[str, Any]:
    parts = urlsplit(args.url)
    if parts.query or parts.fragment:
        raise QueueError("带查询参数或签名的链接不能写入持久队列；请改用单文件 --url-stdin 后台模式。")
    request = validate_request(args.url, requested_output_path(args), args.sha256, allow_http=False)
    if request.source_url != request.safe_url:
        raise QueueError("持久队列只保存无查询参数的来源链接。")
    for existing in queue["entries"]:
        if str(existing.get("output")) == str(request.output):
            raise QueueError("该输出路径已经在队列历史中，拒绝重复入队。")
    entry_id = hashlib.sha256(f"{request.output}\0{request.safe_url}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"{int(args.priority):04d}-{entry_id}",
        "priority": int(args.priority),
        "source_url": request.safe_url,
        "output": str(request.output),
        "sha256": request.expected_sha256,
        "status": "queued",
        "created_at": now(),
        "updated_at": now(),
        "network_failures": 0,
    }


def ensure_queue_not_running(queue_path: Path) -> None:
    owner = read_json_object(queue_lock_path(queue_path)) or {}
    if process_is_running(owner.get("pid")):
        raise QueueError("队列工作进程正在运行；为避免覆盖其内存状态，本次不修改队列。")


def enqueue(args: argparse.Namespace) -> int:
    # Check the worker lock *inside* the same lease as read-modify-write, so a
    # just-starting worker cannot be handed an older in-memory queue snapshot.
    with create_queue_manifest_lease(args.queue, operation="enqueue") as write_lease:
        ensure_queue_not_running(args.queue)
        queue = read_queue(args.queue, allow_missing=True)
        entry = make_entry(args, queue)
        queue["entries"].append(entry)
        queue["state"] = "queued"
        save_queue(args.queue, queue, operation="enqueue", lease=write_lease)
    print(json.dumps({"status": "queued", "entry_id": entry["id"], **summary(queue)}, ensure_ascii=False, indent=2))
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
        **summary(queue),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def add_common_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
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
    enqueue_parser.set_defaults(handler=enqueue)

    start_parser = subparsers.add_parser("start", help="请求 Windows 后台任务处理默认通用队列")
    start_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
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

    status_parser = subparsers.add_parser("status", help="只读查看本地队列状态")
    status_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    status_parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)
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
