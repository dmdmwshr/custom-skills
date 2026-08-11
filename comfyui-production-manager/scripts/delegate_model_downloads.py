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
    }


def read_manifest(path: Path, model_root: Path, generic_queue: Path) -> dict[str, Any]:
    manifest = read_optional_json_object(path)
    if manifest is None:
        return manifest_template(model_root, generic_queue)
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("entries"), list):
        raise DelegateError("模型委托清单结构无效，未覆盖原文件。")
    return manifest


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = now()
    atomic_write_json(path, manifest)


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
    manifest = read_manifest(args.manifest, root, args.generic_queue)
    generic_entries = read_generic_entries(args.generic_queue)
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

    save_manifest(args.manifest, manifest)
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
    manifest = read_manifest(args.manifest, Path(), args.generic_queue)
    catalog = read_catalog(args.catalog)
    registered: list[str] = []
    integrity_failed: list[str] = []
    pending: list[str] = []
    rows: list[dict[str, Any]] = []

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
    save_manifest(args.manifest, manifest)
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
