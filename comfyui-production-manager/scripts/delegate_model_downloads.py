#!/usr/bin/env python3
"""Prepare and register ComfyUI model downloads through the generic Meifu queue.

This script deliberately has no transfer implementation.  It keeps the
ComfyUI-specific responsibilities (approved dependency candidates, model
category, chosen storage root, and catalog registration), while every remote
transfer is owned by ``meifu-resumable-download``'s single Windows worker.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from urllib.parse import urlsplit, urlunsplit

from model_paths import (
    DEFAULT_PRIMARY_THRESHOLD_GIB,
    DEFAULT_SHARED_PATHS,
    choose_primary_and_fallback,
    choose_storage,
    ensure_safe_category,
)


WORKSPACE = Path(r"D:\12070\Documents\workspaces\Comfy-Codex-Workspace")
DEFAULT_DEPENDENCY_REPORT = WORKSPACE / "models" / "template_dependency_report.json"
DEFAULT_MANIFEST = WORKSPACE / "models" / "generic_meifu_model_downloads.json"
DEFAULT_CATALOG = WORKSPACE / "models" / "catalog.json"
DEFAULT_GENERIC_QUEUE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MeifuDownloadQueue" / "queue.json"
DEFAULT_GENERIC_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "meifu-resumable-download"
    / "scripts"
    / "meifu_download_queue.py"
)
MANIFEST_SCHEMA = "ComfyUIGenericMeifuDownloadManifestV1"
MANIFEST_WRITE_STATE_SCHEMA = "ComfyUIGenericMeifuManifestWriteStateV1"
MANIFEST_LOCK_SCHEMA = "ComfyUIGenericMeifuManifestLeaseV1"
MANIFEST_WRITE_PROTOCOL = "lease-compare-and-replace-v1"
MANIFEST_LOCK_GRACE_SECONDS = 120
CATALOG_SCHEMA = "ComfyUIModelCatalogV1"
MODEL_SUFFIXES = {".safetensors", ".ckpt", ".pth", ".pt", ".bin", ".gguf", ".onnx"}
SHA256_RE = re.compile(r"(?i)^[0-9a-f]{64}$")


class DelegateError(RuntimeError):
    """A request was rejected before any model transfer started."""


@dataclass(frozen=True)
class Candidate:
    filename: str
    category: str
    source_url: str
    expected_sha256: str | None
    workflows: list[str]
    workflow_count: int
    reference_count: int


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json_object(path: Path, *, missing_message: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise DelegateError(missing_message) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DelegateError(f"无法读取 JSON 文件：{path}") from exc
    if not isinstance(payload, dict):
        raise DelegateError(f"JSON 根节点必须是对象：{path}")
    return payload


def read_optional_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_json_object(path, missing_message="")


def manifest_write_state(manifest: dict[str, Any]) -> dict[str, Any]:
    """Accept older manifests once, then upgrade them on the next safe commit."""
    state = manifest.get("write_state")
    if state is None:
        return {
            "schema": MANIFEST_WRITE_STATE_SCHEMA,
            "revision": 0,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
        }
    revision = state.get("revision") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("schema") != MANIFEST_WRITE_STATE_SCHEMA
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or state.get("write_protocol") != MANIFEST_WRITE_PROTOCOL
    ):
        raise DelegateError("模型委托清单写入元数据无效，未覆盖原文件。")
    return state


def manifest_revision(manifest: dict[str, Any]) -> int:
    return int(manifest_write_state(manifest)["revision"])


def manifest_lock_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f".{manifest_path.name}.lock")


def lock_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def process_is_running(process_id: int | None) -> bool:
    if not isinstance(process_id, int) or process_id <= 0:
        return False
    if os.name == "nt":
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


@dataclass
class ManifestLease:
    """Short-lived owner token for one ComfyUI delegation-manifest update."""

    path: Path
    manifest_path: Path
    token: str
    operation: str
    released: bool = False

    def assert_owner(self) -> None:
        if self.released:
            raise DelegateError("模型委托清单写入租约已经释放。")
        owner = read_optional_json_object(self.path)
        if owner is None or owner.get("token") != self.token:
            raise DelegateError("模型委托清单写入租约所有者已变化，拒绝覆盖。")

    def release(self) -> None:
        if self.released:
            return
        try:
            owner = read_optional_json_object(self.path)
            if owner is not None and owner.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, DelegateError):
            pass
        finally:
            self.released = True

    def __enter__(self) -> "ManifestLease":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()


def create_manifest_lease(manifest_path: Path, *, operation: str) -> ManifestLease:
    """Serialise local manifest writers without holding a lock during transfer."""
    path = manifest_lock_path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise DelegateError("模型委托清单写入锁不是普通文件，拒绝处理。")
        owner = read_optional_json_object(path) or {}
        process_id = owner.get("pid")
        if isinstance(process_id, int) and process_is_running(process_id):
            raise DelegateError("另一个任务正在写入模型委托清单；请稍后重试，未覆盖任何条目。")
        age = lock_age_seconds(path)
        if not isinstance(process_id, int) and age is not None and age < MANIFEST_LOCK_GRACE_SECONDS:
            raise DelegateError("发现正在初始化的模型委托清单写入锁；两分钟后仍未就绪再人工确认。")
        try:
            path.unlink()
        except OSError as exc:
            raise DelegateError("无法精确回收已停止的模型委托清单写入锁。") from exc

    token = hashlib.sha256(
        f"{os.getpid()}:{time.time_ns()}:{manifest_path}:{operation}".encode("utf-8")
    ).hexdigest()
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise DelegateError("无法获得模型委托清单写入租约，可能有另一个请求刚刚到达。") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "schema": MANIFEST_LOCK_SCHEMA,
                    "pid": os.getpid(),
                    "token": token,
                    "operation": operation,
                    "created_at": now(),
                    "manifest": str(manifest_path),
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
    return ManifestLease(path=path, manifest_path=manifest_path, token=token, operation=operation)


def safe_source_url(value: object) -> str:
    source = str(value or "").strip()
    parts = urlsplit(source)
    if parts.scheme != "https" or not parts.netloc:
        raise DelegateError("模型候选必须是无签名 HTTPS 直链。")
    if parts.query or parts.fragment:
        raise DelegateError("带签名参数或片段的模型链接不能写入持久通用队列。")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def safe_filename(value: object) -> str:
    filename = str(value or "").strip()
    if not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise DelegateError("模型文件名必须是单一文件名，不能包含目录。")
    if Path(filename).suffix.lower() not in MODEL_SUFFIXES:
        raise DelegateError("模型候选扩展名不在允许范围内。")
    return filename


def optional_sha256(value: object) -> str | None:
    if value in {None, ""}:
        return None
    digest = str(value).strip().lower()
    if not SHA256_RE.fullmatch(digest):
        raise DelegateError("候选提供的 SHA-256 格式无效。")
    return digest


def candidate_from_row(row: object) -> Candidate:
    if not isinstance(row, dict):
        raise DelegateError("依赖报告中的下载候选不是对象。")
    category = ensure_safe_category(str(row.get("category") or ""))
    workflows = row.get("workflows")
    if not isinstance(workflows, list) or not all(isinstance(item, str) for item in workflows):
        workflows = []
    return Candidate(
        filename=safe_filename(row.get("filename")),
        category=category,
        source_url=safe_source_url(row.get("source_url")),
        expected_sha256=optional_sha256(row.get("sha256")),
        workflows=sorted(set(workflows)),
        workflow_count=int(row.get("workflow_count") or 0),
        reference_count=int(row.get("reference_count") or 0),
    )


def choose_model_root(args: argparse.Namespace) -> Path:
    if args.model_root:
        root = Path(args.model_root)
        if not root.is_absolute():
            raise DelegateError("--model-root 必须是绝对路径。")
        return root.resolve(strict=False)
    primary, fallback = choose_primary_and_fallback(
        args.shared_paths_config,
        args.primary_root,
        args.fallback_root,
    )
    selection = choose_storage(
        primary_root=primary,
        fallback_root=fallback,
        required_bytes=0,
        primary_threshold_gib=args.primary_threshold_gib,
    )
    return selection.model_root.resolve(strict=False)


def manifest_template(model_root: Path, generic_queue: Path) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "created_at": now(),
        "updated_at": now(),
        "generic_queue": str(generic_queue),
        "entries": [],
        "write_state": {
            "schema": MANIFEST_WRITE_STATE_SCHEMA,
            "revision": 0,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
        },
    }


def read_manifest(path: Path, model_root: Path, generic_queue: Path) -> dict[str, Any]:
    manifest = read_optional_json_object(path)
    if manifest is None:
        return manifest_template(model_root, generic_queue)
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("entries"), list):
        raise DelegateError("模型委托清单结构无效，未覆盖原文件。")
    manifest_write_state(manifest)
    return manifest


def commit_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    lease: ManifestLease,
    operation: str,
) -> None:
    """Compare the disk revision before atomically replacing the manifest."""
    if str(lease.manifest_path.resolve(strict=False)).casefold() != str(path.resolve(strict=False)).casefold():
        raise DelegateError("模型委托清单写入租约不属于当前清单。")
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("entries"), list):
        raise DelegateError("模型委托清单结构无效，未覆盖原文件。")
    lease.assert_owner()
    expected_revision = manifest_revision(manifest)
    current = read_optional_json_object(path)
    if current is None:
        actual_revision = 0
    else:
        if current.get("schema") != MANIFEST_SCHEMA or not isinstance(current.get("entries"), list):
            raise DelegateError("模型委托清单结构无效，未覆盖原文件。")
        actual_revision = manifest_revision(current)
    if actual_revision != expected_revision:
        raise DelegateError("模型委托清单已被其他任务更新；请重新读取后再提交，未覆盖已有条目。")
    write_state = dict(manifest_write_state(manifest))
    write_state.update(
        {
            "schema": MANIFEST_WRITE_STATE_SCHEMA,
            "revision": expected_revision + 1,
            "write_protocol": MANIFEST_WRITE_PROTOCOL,
            "last_mutation": {"operation": operation, "pid": os.getpid(), "at": now()},
        }
    )
    manifest["write_state"] = write_state
    manifest["updated_at"] = now()
    atomic_write_json(path, manifest)


def save_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    operation: str = "adapter",
    lease: ManifestLease | None = None,
) -> None:
    if lease is not None:
        commit_manifest(path, manifest, lease=lease, operation=operation)
        return
    with create_manifest_lease(path, operation=operation) as write_lease:
        commit_manifest(path, manifest, lease=write_lease, operation=operation)


def entry_id(target: Path, source_url: str) -> str:
    digest = hashlib.sha256(f"{target}\0{source_url}".encode("utf-8")).hexdigest()[:20]
    return f"comfy-{digest}"


def read_generic_entries(queue_path: Path) -> dict[str, dict[str, Any]]:
    queue = read_optional_json_object(queue_path)
    if queue is None:
        return {}
    entries = queue.get("entries")
    if not isinstance(entries, list):
        raise DelegateError("通用下载队列结构无效，未尝试修改。")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("output"), str):
            result[str(Path(entry["output"]).resolve(strict=False))] = entry
    return result


def run_generic(args: argparse.Namespace, command: list[str]) -> dict[str, Any]:
    if not args.generic_queue_script.is_file():
        raise DelegateError("找不到通用 Meifu 队列脚本；未开始任何模型传输。")
    try:
        result = subprocess.run(
            [str(args.python), "-B", "-X", "utf8", str(args.generic_queue_script), *command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DelegateError("无法完成通用队列的短暂本地操作；未开始模型传输。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\r", " ").replace("\n", " ")
        raise DelegateError(f"通用队列拒绝该模型条目：{detail[-500:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DelegateError("通用队列没有返回可解析的本地结果。") from exc
    if not isinstance(payload, dict):
        raise DelegateError("通用队列返回结构无效。")
    return payload


def merge_manifest_entry(manifest: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    existing = next((item for item in manifest["entries"] if item.get("id") == row["id"]), None)
    if isinstance(existing, dict):
        existing.update(row)
        return existing
    manifest["entries"].append(row)
    return row


def manifest_row(candidate: Candidate, target: Path, root: Path) -> dict[str, Any]:
    return {
        "id": entry_id(target, candidate.source_url),
        "filename": candidate.filename,
        "category": candidate.category,
        "model_root": str(root),
        "relative_path": str(Path(candidate.category) / candidate.filename),
        "target": str(target),
        "source_url": candidate.source_url,
        "expected_sha256": candidate.expected_sha256,
        "workflows": candidate.workflows,
        "workflow_count": candidate.workflow_count,
        "reference_count": candidate.reference_count,
        "updated_at": now(),
    }


def prepare(args: argparse.Namespace) -> int:
    report = read_json_object(args.dependency_report, missing_message="找不到 ComfyUI 依赖报告。")
    candidates = report.get("download_candidates")
    if not isinstance(candidates, list):
        raise DelegateError("依赖报告没有 download_candidates 列表。")
    root = choose_model_root(args)
    limit = args.limit if args.limit is not None else len(candidates)
    if limit <= 0:
        raise DelegateError("--limit 必须大于 0。")

    result: dict[str, Any] = {
        "status": "prepared",
        "model_root": str(root),
        "generic_queue": str(args.generic_queue),
        "queued": [],
        "already_queued": [],
        "existing_files": [],
        "excluded": [],
        "blocked": [],
    }
    admitted = 0
    # This lease covers the ComfyUI-side read, admission decision, and commit.
    # The generic queue has its own shorter lease, so neither side can lose a
    # concurrent update or turn a duplicate-output race into a second transfer.
    with create_manifest_lease(args.manifest, operation="prepare") as manifest_lease:
        manifest = read_manifest(args.manifest, root, args.generic_queue)
        generic_entries = read_generic_entries(args.generic_queue)
        for priority, raw_candidate in enumerate(candidates, start=args.priority):
            if admitted >= limit:
                break
            try:
                candidate = candidate_from_row(raw_candidate)
                target = (root / candidate.category / candidate.filename).resolve(strict=False)
                row = manifest_row(candidate, target, root)
                existing_file = target.is_file()
                generic_entry = generic_entries.get(str(target))
                if existing_file:
                    row.update({"status": "existing_file", "observed_at": now()})
                    merge_manifest_entry(manifest, row)
                    result["existing_files"].append(str(target))
                    admitted += 1
                    continue
                if generic_entry is not None:
                    if generic_entry.get("source_url") != candidate.source_url:
                        row.update({
                            "status": "blocked",
                            "last_error": "同一输出路径已由通用队列的另一来源占用。",
                        })
                        result["blocked"].append(str(target))
                    else:
                        row.update({
                            "status": str(generic_entry.get("status") or "queued"),
                            "generic_entry_id": generic_entry.get("id"),
                            "observed_at": now(),
                        })
                        result["already_queued"].append(str(target))
                    merge_manifest_entry(manifest, row)
                    admitted += 1
                    continue

                try:
                    response = run_generic(
                        args,
                        [
                            "enqueue",
                            "--queue", str(args.generic_queue),
                            "--url", candidate.source_url,
                            "--storage-root", str(root),
                            "--target", str(Path(candidate.category) / candidate.filename),
                            "--priority", str(priority),
                            *( ["--sha256", candidate.expected_sha256] if candidate.expected_sha256 else [] ),
                        ],
                    )
                except DelegateError:
                    # A second caller may have won the generic queue race after
                    # our read.  Re-read once and record that truthful result;
                    # never retry the enqueue blindly.
                    refreshed_entries = read_generic_entries(args.generic_queue)
                    generic_entry = refreshed_entries.get(str(target))
                    if generic_entry is None:
                        raise
                    generic_entries = refreshed_entries
                    if generic_entry.get("source_url") == candidate.source_url:
                        row.update({
                            "status": str(generic_entry.get("status") or "queued"),
                            "generic_entry_id": generic_entry.get("id"),
                            "observed_at": now(),
                        })
                        result["already_queued"].append(str(target))
                    else:
                        row.update({
                            "status": "blocked",
                            "last_error": "同一输出路径已由通用队列的另一来源占用。",
                        })
                        result["blocked"].append(str(target))
                    merge_manifest_entry(manifest, row)
                    admitted += 1
                    continue

                row.update({
                    "status": "queued",
                    "generic_entry_id": response.get("entry_id"),
                    "queued_at": now(),
                })
                merge_manifest_entry(manifest, row)
                generic_entries[str(target)] = {"status": "queued", "source_url": candidate.source_url}
                result["queued"].append(str(target))
                admitted += 1
            except (DelegateError, ValueError) as exc:
                result["excluded"].append({"index": priority, "reason": str(exc)})

        save_manifest(args.manifest, manifest, operation="prepare", lease=manifest_lease)
    result["manifest"] = str(args.manifest)
    result["counts"] = {key: len(value) for key, value in result.items() if isinstance(value, list)}
    if args.start and result["queued"]:
        result["start"] = run_generic(args, ["start", "--queue", str(args.generic_queue)])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def generic_status(args: argparse.Namespace) -> dict[str, Any]:
    return run_generic(args, ["status", "--queue", str(args.generic_queue)])


def status(args: argparse.Namespace) -> int:
    manifest = read_manifest(args.manifest, Path(), args.generic_queue)
    queue_entries = read_generic_entries(args.generic_queue)
    write_state = manifest_write_state(manifest)
    write_owner = read_optional_json_object(manifest_lock_path(args.manifest)) or {}
    counts: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for row in manifest["entries"]:
        if not isinstance(row, dict):
            continue
        target = Path(str(row.get("target") or ""))
        queue_entry = queue_entries.get(str(target.resolve(strict=False)))
        state = str(queue_entry.get("status")) if queue_entry else str(row.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        entries.append({
            "id": row.get("id"),
            "filename": row.get("filename"),
            "category": row.get("category"),
            "state": state,
            "file_present": target.is_file(),
        })
    print(json.dumps({
        "status": "ok",
        "generic_queue": generic_status(args),
        "manifest": str(args.manifest),
        "manifest_revision": write_state["revision"],
        "manifest_write_active": process_is_running(write_owner.get("pid")),
        "manifest_write_operation": write_owner.get("operation"),
        "counts": counts,
        "entries": entries,
    }, ensure_ascii=False, indent=2))
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read_catalog(path: Path) -> dict[str, Any]:
    catalog = read_optional_json_object(path)
    if catalog is None:
        return {"schema": CATALOG_SCHEMA, "models": []}
    if catalog.get("schema") != CATALOG_SCHEMA or not isinstance(catalog.get("models"), list):
        raise DelegateError("ComfyUI 模型目录登记结构无效，未覆盖原文件。")
    return catalog


def reconcile(args: argparse.Namespace) -> int:
    registered: list[str] = []
    integrity_failed: list[str] = []
    pending: list[str] = []
    rows: list[dict[str, Any]] = []

    # Reconciliation also changes the same manifest, so it shares the exact
    # writer lease with prepare.  The catalog update stays inside that section
    # and cannot observe a half-registered manifest from this adapter.
    with create_manifest_lease(args.manifest, operation="reconcile") as manifest_lease:
        manifest = read_manifest(args.manifest, Path(), args.generic_queue)
        catalog = read_catalog(args.catalog)
        for entry in manifest["entries"]:
            if not isinstance(entry, dict):
                continue
            target = Path(str(entry.get("target") or ""))
            if entry.get("registration_status") == "registered":
                continue
            if not target.is_file():
                pending.append(str(target))
                continue
            actual_sha256 = sha256_file(target)
            expected_sha256 = entry.get("expected_sha256")
            if expected_sha256 and actual_sha256 != expected_sha256:
                entry.update({
                    "registration_status": "integrity_failed",
                    "actual_sha256": actual_sha256,
                    "last_error": "最终文件 SHA-256 与已提供的官方值不一致。",
                    "updated_at": now(),
                })
                integrity_failed.append(str(target))
                continue
            row = {
                "filename": entry.get("filename"),
                "category": entry.get("category"),
                "relative_path": entry.get("relative_path"),
                "model_root": entry.get("model_root"),
                "absolute_path": str(target),
                "size_bytes": target.stat().st_size,
                "sha256": actual_sha256,
                "source_url": entry.get("source_url"),
                "license_status": "pending_manual_verification",
                "workflows": entry.get("workflows") or [],
                "transport": "meifu_resumable_download_queue",
                "verified_at": now(),
            }
            rows.append(row)
            entry.update({
                "registration_status": "registered",
                "actual_sha256": actual_sha256,
                "registered_at": now(),
                "updated_at": now(),
            })
            registered.append(str(target))

        if rows:
            new_paths = {row["absolute_path"] for row in rows}
            new_hashes = {row["sha256"] for row in rows}
            catalog["models"] = [
                row for row in catalog["models"]
                if row.get("absolute_path") not in new_paths and row.get("sha256") not in new_hashes
            ]
            catalog["models"].extend(rows)
            catalog["models"].sort(key=lambda item: (str(item.get("category") or ""), str(item.get("filename") or "")))
            catalog["updated_at"] = now()
            atomic_write_json(args.catalog, catalog)
        save_manifest(args.manifest, manifest, operation="reconcile", lease=manifest_lease)
    print(json.dumps({
        "status": "reconciled",
        "catalog": str(args.catalog),
        "registered": registered,
        "integrity_failed": integrity_failed,
        "pending": pending,
    }, ensure_ascii=False, indent=2))
    return 0


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dependency-report", type=Path, default=DEFAULT_DEPENDENCY_REPORT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--generic-queue", type=Path, default=DEFAULT_GENERIC_QUEUE)
    parser.add_argument("--generic-queue-script", type=Path, default=DEFAULT_GENERIC_SCRIPT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="仅把已确认 ComfyUI 模型候选委托给通用队列")
    add_common_paths(prepare_parser)
    prepare_parser.add_argument("--model-root", type=Path)
    prepare_parser.add_argument("--shared-paths-config", type=Path, default=DEFAULT_SHARED_PATHS)
    prepare_parser.add_argument("--primary-root", type=Path)
    prepare_parser.add_argument("--fallback-root", type=Path)
    prepare_parser.add_argument("--primary-threshold-gib", type=int, default=DEFAULT_PRIMARY_THRESHOLD_GIB)
    prepare_parser.add_argument("--priority", type=int, default=100)
    prepare_parser.add_argument("--limit", type=int)
    prepare_parser.add_argument("--start", action="store_true", help="明确请求通用 Windows 后台任务开始处理。")
    prepare_parser.set_defaults(handler=prepare)

    status_parser = subparsers.add_parser("status", help="只读查看 ComfyUI 委托条目与通用队列状态")
    add_common_paths(status_parser)
    status_parser.set_defaults(handler=status)

    reconcile_parser = subparsers.add_parser("reconcile", help="校验已落盘文件并登记到 ComfyUI 模型目录")
    add_common_paths(reconcile_parser)
    reconcile_parser.set_defaults(handler=reconcile)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return int(args.handler(args))
    except (DelegateError, OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
