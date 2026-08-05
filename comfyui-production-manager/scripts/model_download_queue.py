#!/usr/bin/env python3
"""Durable, single-worker queue for missing ComfyUI template models.

The queue is deliberately conservative:
* one model worker and one Meifu cache chunk at a time;
* all progress is written atomically to a local JSON queue file;
* ``pause`` lets the current transfer reach a safe boundary, while
  ``pause --immediate`` asks the worker to terminate its exact child tree;
* failed sources become ``blocked`` and do not create an automatic retry loop.

It invokes ``stage_model_download.py`` for the actual verified, resumable
Meifu-to-local transfer.  It does not install a model merely because its name
appears in a workflow report: only the report's pre-approved download candidates
are admitted to the queue.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from stage_model_download import DEFAULT_HOST, DEFAULT_REMOTE_CACHE, ensure_direct_meifu_route


WORKSPACE = Path(r"D:\12070\Documents\workspaces\Comfy-Codex-Workspace")
DEFAULT_REPORT = WORKSPACE / "models" / "template_dependency_report.json"
DEFAULT_QUEUE = WORKSPACE / "models" / "download_queue.json"
DEFAULT_CONTROL = WORKSPACE / "models" / "download_queue.control.json"
DEFAULT_LOG = WORKSPACE / "models" / "logs" / "model_download_queue.log"
DEFAULT_CATALOG = WORKSPACE / "models" / "catalog.json"
DEFAULT_STAGE_SCRIPT = Path(__file__).with_name("stage_model_download.py")
DEFAULT_EXPECTED_HOSTNAME = "192.129.128.54"
DEFAULT_CHUNK_GIB = 2.0
PAUSE_EXIT_CODE = 75
QUEUE_SCHEMA = "ComfyUIModelDownloadQueueV1"
ENTRY_STATES = {"queued", "running", "completed", "blocked", "skipped"}
FILENAME_RE = re.compile(r"^[^\\/:*?\"<>|]+\.(?:safetensors|ckpt|pth|pt|bin|gguf|onnx)$", re.IGNORECASE)
CATEGORY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class QueueError(RuntimeError):
    """A safe queue-management failure that needs user attention."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise QueueError(f"找不到队列文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise QueueError(f"队列 JSON 无法解析，未尝试覆盖：{path}") from exc
    if not isinstance(payload, dict):
        raise QueueError("队列文件根节点必须是对象。")
    return payload


def save_queue(path: Path, queue: dict[str, Any]) -> None:
    queue["updated_at"] = now()
    atomic_write_json(path, queue)


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{now()}] {message}\n")


def safe_candidate(candidate: dict[str, Any], priority: int) -> dict[str, Any]:
    filename = str(candidate.get("filename", "")).strip()
    category = str(candidate.get("category", "")).strip()
    source_url = str(candidate.get("source_url", "")).strip()
    if not FILENAME_RE.fullmatch(filename) or Path(filename).name != filename:
        raise QueueError(f"候选模型文件名不安全：{filename!r}")
    if not CATEGORY_RE.fullmatch(category):
        raise QueueError(f"候选模型类别不安全：{category!r}")
    parts = urlsplit(source_url)
    if parts.scheme != "https" or not parts.netloc:
        raise QueueError(f"候选模型来源不是 HTTPS：{filename}")
    entry_id = hashlib.sha256(f"{filename}\0{category}\0{source_url}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"{priority:03d}-{entry_id}",
        "priority": priority,
        "filename": filename,
        "category": category,
        "source_url": source_url,
        "workflow_count": int(candidate.get("workflow_count", 0)),
        "reference_count": int(candidate.get("reference_count", 0)),
        "workflows": sorted({str(item) for item in candidate.get("workflows", []) if str(item).strip()}),
        "status": "queued",
        "attempts": 0,
        "created_at": now(),
        "updated_at": now(),
    }


def validate_queue(queue: dict[str, Any]) -> None:
    if queue.get("schema") != QUEUE_SCHEMA:
        raise QueueError("队列 schema 不匹配，拒绝用未知结构继续下载。")
    if not isinstance(queue.get("entries"), list):
        raise QueueError("队列缺少 entries 列表。")
    for entry in queue["entries"]:
        if not isinstance(entry, dict) or entry.get("status") not in ENTRY_STATES:
            raise QueueError("队列中存在未知条目状态，拒绝继续。")


def queue_summary(queue: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {state: 0 for state in sorted(ENTRY_STATES)}
    for entry in queue["entries"]:
        counts[str(entry.get("status"))] = counts.get(str(entry.get("status")), 0) + 1
    active = next((entry for entry in queue["entries"] if entry.get("status") == "running"), None)
    return {
        "queue_state": queue.get("state"),
        "counts": counts,
        "active": {
            "id": active.get("id"),
            "filename": active.get("filename"),
            "category": active.get("category"),
        } if active else None,
        "worker": queue.get("worker", {}),
        "direct_route": queue.get("config", {}).get("direct_route"),
        "excluded": queue.get("excluded", {}),
        "updated_at": queue.get("updated_at"),
    }


def read_control(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"暂停控制文件无效：{path}") from exc
    if not isinstance(payload, dict):
        raise QueueError("暂停控制文件必须是 JSON 对象。")
    action = str(payload.get("action", "")).strip().lower()
    if action not in {"pause", "stop"}:
        raise QueueError("暂停控制文件 action 只能是 pause 或 stop。")
    return payload


def write_control(path: Path, action: str, *, requested_by: str, discard_remote_cache: bool = False) -> None:
    atomic_write_json(path, {
        "schema": "ComfyUIModelDownloadControlV1",
        "action": action,
        "requested_at": now(),
        "requested_by": requested_by,
        "discard_remote_cache": discard_remote_cache,
    })


def pid_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_path(queue_path: Path) -> Path:
    return queue_path.with_name(f"{queue_path.name}.lock")


def acquire_lock(queue_path: Path, *, recover_stale: bool) -> Path:
    path = lock_path(queue_path)
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8-sig"))
            old_pid = int(old.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            old_pid = 0
        if pid_is_running(old_pid):
            raise QueueError(f"已有下载队列进程正在运行（PID {old_pid}），拒绝启动第二个后台任务。")
        if not recover_stale:
            raise QueueError("发现已停止的旧队列锁；先用 --recover-stale-lock 明确回收。")
        path.unlink(missing_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise QueueError("无法获得队列锁，可能有另一个任务刚刚启动。") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"pid": os.getpid(), "created_at": now()}, handle, ensure_ascii=False)
        handle.write("\n")
    return path


def release_lock(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if int(payload.get("pid", 0) or 0) == os.getpid():
        path.unlink(missing_ok=True)


def stage_job_id(entry: dict[str, Any]) -> str:
    filename = str(entry["filename"])
    source_url = str(entry["source_url"])
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).stem).strip("-.")
    return f"{stem[:56]}-{hashlib.sha256(source_url.encode('utf-8')).hexdigest()[:12]}"


def require_standard_remote_cache(remote_cache: str) -> None:
    if remote_cache.rstrip("/") != DEFAULT_REMOTE_CACHE:
        raise QueueError(f"队列仅允许使用受管美服缓存目录 {DEFAULT_REMOTE_CACHE}。")


def cleanup_remote_jobs(
    host: str,
    remote_cache: str,
    entries: list[dict[str, Any]],
    expected_hostname: str = DEFAULT_EXPECTED_HOSTNAME,
) -> list[str]:
    """Delete only queue-owned job folders, after the worker has stopped."""

    require_standard_remote_cache(remote_cache)
    if not entries:
        return []
    ensure_direct_meifu_route(host, expected_hostname, allow_proxy_route=False)
    job_ids = sorted({stage_job_id(entry) for entry in entries})
    encoded_cache = base64.b64encode(remote_cache.encode("utf-8")).decode("ascii")
    encoded_jobs = " ".join(base64.b64encode(job.encode("utf-8")).decode("ascii") for job in job_ids)
    script = f'''set -euo pipefail
CACHE=$(printf %s '{encoded_cache}' | base64 -d)
case "$CACHE" in '{DEFAULT_REMOTE_CACHE}') ;; *) echo "非法美服缓存根目录" >&2; exit 2;; esac
for encoded in {encoded_jobs}; do
  JOB=$(printf %s "$encoded" | base64 -d)
  case "$JOB" in *[!A-Za-z0-9_.-]*|'') echo "非法任务目录" >&2; exit 2;; esac
  TARGET="$CACHE/$JOB"
  case "$TARGET" in "$CACHE"/*) ;; *) echo "非法清理路径" >&2; exit 2;; esac
  rm -rf -- "$TARGET"
done
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host, "bash", "-s"],
        input=script.encode("utf-8"),
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise QueueError(f"美服缓存清理失败：{detail[-1000:]}")
    return job_ids


def tail_text(path: Path, limit: int = 1600) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def next_queued_entry(queue: dict[str, Any]) -> dict[str, Any] | None:
    queued = [entry for entry in queue["entries"] if entry.get("status") == "queued"]
    return min(queued, key=lambda entry: (int(entry.get("priority", 0)), str(entry.get("id", "")))) if queued else None


def update_entry(entry: dict[str, Any], status: str, **fields: Any) -> None:
    if status not in ENTRY_STATES:
        raise QueueError(f"无效队列状态：{status}")
    entry["status"] = status
    entry.update(fields)
    entry["updated_at"] = now()


def child_command(entry: dict[str, Any], args: argparse.Namespace) -> list[str]:
    if not args.stage_script.exists():
        raise QueueError(f"缺少单模型传输脚本：{args.stage_script}")
    return [
        str(args.python), "-B", "-u", str(args.stage_script),
        "--url", str(entry["source_url"]),
        "--filename", str(entry["filename"]),
        "--category", str(entry["category"]),
        "--sha256", "auto",
        "--host", args.host,
        "--expected-hostname", args.expected_hostname,
        "--remote-cache", args.remote_cache,
        "--chunk-gib", str(args.chunk_gib),
        "--catalog", str(args.catalog),
        "--control-file", str(args.control_file),
        "--execute",
    ]


def stop_exact_process_tree(pid: int, log_path: Path) -> None:
    """Terminate only the stage downloader and children it spawned."""

    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
    )
    detail = (result.stdout + result.stderr).strip().replace("\r", " ").replace("\n", " | ")
    append_log(log_path, f"已请求结束当前下载子进程树 PID={pid}: {detail[-700:]}")


def wait_for_child(process: subprocess.Popen[str], control_file: Path, log_path: Path) -> tuple[int, bool]:
    """Return child exit code and whether a hard stop was requested."""

    hard_stopped = False
    while process.poll() is None:
        control = read_control(control_file)
        if control and control.get("action") == "stop":
            stop_exact_process_tree(process.pid, log_path)
            hard_stopped = True
            break
        time.sleep(2)
    return process.wait(), hard_stopped


def run_queue(args: argparse.Namespace) -> int:
    queue = read_json(args.queue)
    validate_queue(queue)
    require_standard_remote_cache(args.remote_cache)
    route = ensure_direct_meifu_route(args.host, args.expected_hostname, allow_proxy_route=False)
    lock = acquire_lock(args.queue, recover_stale=args.recover_stale_lock)
    processed = 0
    try:
        queue["state"] = "running"
        queue["worker"] = {
            "pid": os.getpid(),
            "started_at": now(),
            "max_concurrent_models": 1,
            "max_remote_chunks": 1,
            "child_pid": None,
        }
        queue.setdefault("config", {})["direct_route"] = route
        save_queue(args.queue, queue)
        append_log(args.log_file, f"队列启动；SSH 直连地址={route['hostname']}，无代理跳板。")

        while True:
            control = read_control(args.control_file)
            if control:
                queue["state"] = "paused"
                queue["worker"]["child_pid"] = None
                save_queue(args.queue, queue)
                append_log(args.log_file, f"队列在启动下一模型前响应 {control['action']} 请求。")
                print(json.dumps({"status": "paused", **queue_summary(queue)}, ensure_ascii=False, indent=2))
                return PAUSE_EXIT_CODE
            if args.max_models is not None and processed >= args.max_models:
                queue["state"] = "queued"
                queue["worker"]["child_pid"] = None
                save_queue(args.queue, queue)
                print(json.dumps({"status": "batch_limit_reached", **queue_summary(queue)}, ensure_ascii=False, indent=2))
                return 0
            entry = next_queued_entry(queue)
            if entry is None:
                queue["state"] = "completed"
                queue["worker"]["child_pid"] = None
                queue["worker"]["finished_at"] = now()
                save_queue(args.queue, queue)
                append_log(args.log_file, "队列没有剩余可启动的模型。")
                print(json.dumps({"status": "queue_finished", **queue_summary(queue)}, ensure_ascii=False, indent=2))
                return 0

            update_entry(entry, "running", started_at=now(), last_error=None)
            queue["active_entry_id"] = entry["id"]
            save_queue(args.queue, queue)
            command = child_command(entry, args)
            append_log(args.log_file, f"开始模型 {entry['id']} {entry['filename']}；单工作者模式。")
            with args.log_file.open("a", encoding="utf-8", newline="\n") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(args.stage_script.parent),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                queue["worker"]["child_pid"] = process.pid
                queue["worker"]["child_started_at"] = now()
                save_queue(args.queue, queue)
                exit_code, hard_stopped = wait_for_child(process, args.control_file, args.log_file)

            queue["worker"]["child_pid"] = None
            processed += 1
            control = read_control(args.control_file)
            if exit_code == 0:
                update_entry(entry, "completed", completed_at=now(), last_exit_code=0)
                append_log(args.log_file, f"模型完成 {entry['id']} {entry['filename']}。")
            elif exit_code == PAUSE_EXIT_CODE or hard_stopped or control:
                update_entry(entry, "queued", last_exit_code=exit_code, pause_requested_at=now())
                if control and control.get("discard_remote_cache"):
                    removed = cleanup_remote_jobs(args.host, args.remote_cache, [entry], args.expected_hostname)
                    entry["remote_cache_cleared_at"] = now()
                    append_log(args.log_file, f"暂停后已清理美服缓存任务目录：{', '.join(removed)}")
                queue["state"] = "paused"
                save_queue(args.queue, queue)
                append_log(args.log_file, f"模型在安全续传状态暂停 {entry['id']} exit={exit_code}。")
                print(json.dumps({"status": "paused", **queue_summary(queue)}, ensure_ascii=False, indent=2))
                return PAUSE_EXIT_CODE
            else:
                attempts = int(entry.get("attempts", 0)) + 1
                update_entry(
                    entry,
                    "blocked",
                    attempts=attempts,
                    last_exit_code=exit_code,
                    last_error=tail_text(args.log_file),
                )
                append_log(args.log_file, f"模型已阻塞，不自动重试 {entry['id']} exit={exit_code}。")
            save_queue(args.queue, queue)
    finally:
        queue["worker"] = {"pid": None, "stopped_at": now(), "max_concurrent_models": 1, "max_remote_chunks": 1}
        queue.pop("active_entry_id", None)
        save_queue(args.queue, queue)
        release_lock(lock)


def initialize_queue(args: argparse.Namespace) -> int:
    if args.queue.exists() and not args.replace:
        raise QueueError(f"队列已存在：{args.queue}。确认要重建时使用 --replace。")
    report = read_json(args.dependency_report)
    candidates = report.get("download_candidates")
    if not isinstance(candidates, list):
        raise QueueError("依赖报告没有可下载候选清单。")
    entries: list[dict[str, Any]] = []
    dedupe: set[tuple[str, str, str]] = set()
    for priority, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        entry = safe_candidate(candidate, priority)
        key = (entry["filename"].lower(), entry["category"], entry["source_url"])
        if key not in dedupe:
            entries.append(entry)
            dedupe.add(key)
    if not entries:
        raise QueueError("依赖报告没有通过安全筛选的下载候选。")
    exclusions: dict[str, list[str]] = {}
    for status in ("ambiguous_category", "unresolved"):
        filenames = sorted({
            str(row.get("filename"))
            for row in report.get("dependencies", [])
            if isinstance(row, dict) and row.get("status") == status
        })
        exclusions[status] = filenames
    queue = {
        "schema": QUEUE_SCHEMA,
        "created_at": now(),
        "updated_at": now(),
        "state": "queued",
        "source_report": str(args.dependency_report),
        "config": {
            "host": args.host,
            "expected_hostname": args.expected_hostname,
            "remote_cache": args.remote_cache,
            "chunk_gib": args.chunk_gib,
            "catalog": str(args.catalog),
            "max_concurrent_models": 1,
            "max_remote_chunks": 1,
            "retry_policy": "manual_only_after_blocked",
        },
        "worker": {"pid": None, "max_concurrent_models": 1, "max_remote_chunks": 1},
        "entries": entries,
        "excluded": exclusions,
    }
    atomic_write_json(args.queue, queue)
    print(json.dumps({"status": "initialized", "queue": str(args.queue), **queue_summary(queue)}, ensure_ascii=False, indent=2))
    return 0


def pause_queue(args: argparse.Namespace) -> int:
    queue = read_json(args.queue)
    validate_queue(queue)
    action = "stop" if args.immediate else "pause"
    write_control(args.control_file, action, requested_by="manual", discard_remote_cache=args.discard_remote_cache)
    queue["state"] = "pausing"
    save_queue(args.queue, queue)
    print(json.dumps({
        "status": "pause_requested",
        "action": action,
        "queue": str(args.queue),
        "control_file": str(args.control_file),
        "remote_cache_note": "默认保留当前最多一个分块以便续传；使用 cleanup-remote 可在任务停止后释放该缓存。",
    }, ensure_ascii=False, indent=2))
    return 0


def resume_queue(args: argparse.Namespace) -> int:
    queue = read_json(args.queue)
    validate_queue(queue)
    args.control_file.unlink(missing_ok=True)
    if queue.get("state") in {"paused", "pausing"}:
        queue["state"] = "queued"
    save_queue(args.queue, queue)
    print(json.dumps({"status": "resumed", "queue": str(args.queue), **queue_summary(queue)}, ensure_ascii=False, indent=2))
    return 0


def status_queue(args: argparse.Namespace) -> int:
    queue = read_json(args.queue)
    validate_queue(queue)
    control = read_control(args.control_file)
    print(json.dumps({
        "status": "ok",
        "queue": str(args.queue),
        "control": control,
        **queue_summary(queue),
    }, ensure_ascii=False, indent=2))
    return 0


def retry_queue(args: argparse.Namespace) -> int:
    queue = read_json(args.queue)
    validate_queue(queue)
    selected = [entry for entry in queue["entries"] if entry.get("status") == "blocked"]
    if args.entry_id:
        selected = [entry for entry in selected if entry.get("id") == args.entry_id]
    if not selected:
        raise QueueError("没有匹配的 blocked 模型可重试。")
    for entry in selected:
        update_entry(entry, "queued", retry_requested_at=now())
    if queue.get("state") == "completed":
        queue["state"] = "queued"
    save_queue(args.queue, queue)
    print(json.dumps({"status": "retry_queued", "entry_ids": [entry["id"] for entry in selected]}, ensure_ascii=False, indent=2))
    return 0


def cleanup_remote_cache(args: argparse.Namespace) -> int:
    """Free queue-owned Meifu cache only after its active worker is gone."""

    queue = read_json(args.queue)
    validate_queue(queue)
    lock = lock_path(args.queue)
    if lock.exists():
        try:
            owner = json.loads(lock.read_text(encoding="utf-8-sig"))
            owner_pid = int(owner.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            owner_pid = 0
        if pid_is_running(owner_pid):
            raise QueueError("下载队列仍在运行；请先安全暂停或停止该精确任务，再清理美服缓存。")
    entries = [entry for entry in queue["entries"] if entry.get("status") != "completed"]
    if args.entry_id:
        entries = [entry for entry in entries if entry.get("id") == args.entry_id]
    if not entries:
        raise QueueError("没有可清理美服缓存的未完成队列条目。")
    removed = cleanup_remote_jobs(args.host, args.remote_cache, entries, args.expected_hostname)
    timestamp = now()
    for entry in entries:
        entry["remote_cache_cleared_at"] = timestamp
        entry["updated_at"] = timestamp
    save_queue(args.queue, queue)
    print(json.dumps({
        "status": "remote_cache_cleaned",
        "remote_cache": args.remote_cache,
        "job_ids": removed,
        "note": "本地续传状态仍保留；恢复时只会重新从来源获取尚未验证的当前分块。",
    }, ensure_ascii=False, indent=2))
    return 0


def add_common_queue_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize", help="从依赖报告建立安全下载队列")
    initialize.add_argument("--dependency-report", type=Path, default=DEFAULT_REPORT)
    initialize.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    initialize.add_argument("--host", default=DEFAULT_HOST)
    initialize.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    initialize.add_argument("--remote-cache", default=DEFAULT_REMOTE_CACHE)
    initialize.add_argument("--chunk-gib", type=float, default=DEFAULT_CHUNK_GIB)
    initialize.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    initialize.add_argument("--replace", action="store_true")
    initialize.set_defaults(handler=initialize_queue)

    run = subparsers.add_parser("run", help="以单工作者模式处理队列")
    add_common_queue_path_arguments(run)
    run.add_argument("--host", default=DEFAULT_HOST)
    run.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    run.add_argument("--remote-cache", default=DEFAULT_REMOTE_CACHE)
    run.add_argument("--chunk-gib", type=float, default=DEFAULT_CHUNK_GIB)
    run.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    run.add_argument("--stage-script", type=Path, default=DEFAULT_STAGE_SCRIPT)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    run.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    run.add_argument("--max-models", type=int, help="本次最多启动几个模型；0 可用于只验证队列。")
    run.add_argument("--recover-stale-lock", action="store_true")
    run.set_defaults(handler=run_queue)

    pause = subparsers.add_parser("pause", help="请求安全暂停；默认不杀死正在传输的分块")
    add_common_queue_path_arguments(pause)
    pause.add_argument("--immediate", action="store_true", help="请求精确结束当前下载子进程树，保留本地续传状态。")
    pause.add_argument("--discard-remote-cache", action="store_true", help="仅记录意图；任务停止后可运行 cleanup-remote 释放当前缓存。")
    pause.set_defaults(handler=pause_queue)

    resume = subparsers.add_parser("resume", help="移除暂停控制，允许下次队列任务续传")
    add_common_queue_path_arguments(resume)
    resume.set_defaults(handler=resume_queue)

    status = subparsers.add_parser("status", help="查看队列、控制文件和当前单工作者状态")
    add_common_queue_path_arguments(status)
    status.set_defaults(handler=status_queue)

    retry = subparsers.add_parser("retry", help="把明确处理过的 blocked 条目重新放入队列")
    retry.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    retry.add_argument("--entry-id")
    retry.set_defaults(handler=retry_queue)

    cleanup = subparsers.add_parser("cleanup-remote", help="在队列停止后释放未完成任务占用的美服缓存")
    cleanup.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    cleanup.add_argument("--host", default=DEFAULT_HOST)
    cleanup.add_argument("--expected-hostname", default=DEFAULT_EXPECTED_HOSTNAME)
    cleanup.add_argument("--remote-cache", default=DEFAULT_REMOTE_CACHE)
    cleanup.add_argument("--entry-id")
    cleanup.set_defaults(handler=cleanup_remote_cache)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return int(args.handler(args))
    except QueueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "message": "未覆盖队列进度；可先查看 status 后续传。"}, ensure_ascii=False), file=sys.stderr)
        return PAUSE_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
