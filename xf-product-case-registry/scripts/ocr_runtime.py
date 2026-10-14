"""Local MinerU 4 execution, content-bound receipts and resumable task polling."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from pypdf import PdfReader

if __package__:
    from .mineru_v4 import TASK_SCHEMA, engine_profile, run_task
    from .mineru_v4 import MinerUV4Error as OcrError
else:
    from mineru_v4 import TASK_SCHEMA, engine_profile, run_task
    from mineru_v4 import MinerUV4Error as OcrError

SCHEMA = "OcrResultV1"
LEGACY_OPTIONS = {
    "backend": "hybrid-auto-engine",
    "method": "auto",
    "lang": "ch",
    "table": True,
    "formula": True,
}
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def object_hash(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )


def safe_path(path: Path, boundary: Path | None = None) -> Path:
    absolute = Path(os.path.abspath(path))
    for part in (absolute, *absolute.parents):
        if part.exists() or part.is_symlink():
            metadata = part.lstat()
            if part.is_symlink() or getattr(metadata, "st_file_attributes", 0) & REPARSE:
                raise OcrError("OCR 路径不允许符号链接或重解析点")
    resolved = absolute.resolve()
    if boundary is not None and not resolved.is_relative_to(boundary.resolve()):
        raise OcrError("OCR 路径超出本案范围")
    return resolved


def relative_path(value: Any) -> str:
    if not isinstance(value, str):
        raise OcrError("OCR 相对路径必须是字符串")
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or any(ord(char) < 32 for char in normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise OcrError("OCR 相对路径不安全")
    return PurePosixPath(normalized).as_posix()


def write_json(path: Path, value: Any) -> None:
    safe_path(path)
    temp = path.with_name(path.name + ".tmp")
    safe_path(temp)
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextmanager
def project_lock(work: Path):
    """Kernel-owned lock: process exit releases it, without stale-PID guessing."""
    path = safe_path(work / ".ocr.lock", work)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OcrError("本案已有 OCR 正在运行，请使用同一执行单元") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate_sources(
    inventory: dict[str, Any],
    selected: list[str],
    allowed_roots: list[Path] | None,
) -> list[dict[str, Any]]:
    if not selected:
        raise OcrError("OCR 必须显式指定至少一个相对 PDF/图片路径")
    source_root = safe_path(Path(inventory["sourceRoot"]))
    if allowed_roots is not None and not any(
        source_root.is_relative_to(safe_path(root)) for root in allowed_roots
    ):
        raise OcrError("OCR inventory 来源不属于当前项目")
    records = inventory.get("files")
    if not isinstance(records, list):
        raise OcrError("OCR inventory.files 必须是数组")
    by_rel: dict[str, Any] = {}
    for item in records:
        if not isinstance(item, dict):
            raise OcrError("OCR inventory 文件记录无效")
        relative = relative_path(item.get("relativePath"))
        if relative in by_rel:
            raise OcrError("OCR inventory 含重复相对路径")
        by_rel[relative] = item
    result, seen = [], set()
    for value in selected:
        relative = relative_path(value)
        if relative in seen:
            continue
        seen.add(relative)
        item = by_rel.get(relative)
        if not item or item.get("mimeType") not in {
            "application/pdf",
            "image/png",
            "image/jpeg",
        }:
            raise OcrError("OCR 来源必须是 inventory 中的 PDF/PNG/JPEG")
        path = safe_path(source_root / relative, source_root)
        if safe_path(Path(item["absolutePath"]), source_root) != path or not path.is_file():
            raise OcrError("OCR 文件路径与本案清点不一致")
        if file_hash(path) != item.get("sha256") or path.stat().st_size != item.get("sizeBytes"):
            raise OcrError("OCR 源文件已变化，请重新清点本案")
        pages = item.get("pageCount", 1)
        if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
            raise OcrError("OCR 来源页数无效")
        key = object_hash({"relative": relative, "sha256": item["sha256"]}).split(":")[1][:24]
        suffix = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}[
            item["mimeType"]
        ]
        result.append(
            {
                "relative": relative,
                "source": path,
                "sha256": item["sha256"],
                "pages": pages,
                "alias": key,
                "inputName": key + suffix,
            }
        )
    return result


def artifact_snapshot(directory: Path, alias: str, pages: int) -> list[dict[str, Any]] | None:
    """Require complete structured pages, original PDF, text, and referenced images.

    Optional visualizations are deliberately excluded: MinerU can finish them later.
    A directory or Markdown file on its own never creates a completion receipt.
    """
    try:
        safe_path(directory)
        if not directory.is_dir():
            return None
        candidates = list(directory.glob(f"**/{alias}_middle.json"))
        if len(candidates) != 1:
            return None
        middle_path = safe_path(candidates[0], directory)
        parent = middle_path.parent
        middle = json.loads(middle_path.read_text(encoding="utf-8"))
        current = "schema" in middle
        if current and (
            middle.get("schema") != "docvortex.middle"
            or middle.get("schema_version") != "2.0"
            or middle.get("is_full_document") is not True
        ):
            return None
        origins = (
            list(parent.glob(f"{alias}_origin.*")) if current else [parent / f"{alias}_origin.pdf"]
        )
        if len(origins) != 1 or origins[0].suffix not in {".pdf", ".png", ".jpg"}:
            return None
        required = [
            middle_path,
            parent / f"{alias}.md",
            parent
            / (f"{alias}_structured_content.json" if current else f"{alias}_content_list.json"),
            origins[0],
        ]
        if any(
            not safe_path(path, directory).is_file() or path.stat().st_size == 0
            for path in required
        ):
            return None
        page_data = middle.get("pages" if current else "pdf_info")
        if not isinstance(page_data, list) or len(page_data) != pages:
            return None
        if [page.get("page_idx") for page in page_data if isinstance(page, dict)] != list(
            range(pages)
        ):
            return None
        contents = json.loads(required[2].read_text(encoding="utf-8"))
        if current:
            if (
                not isinstance(contents, dict)
                or contents.get("is_full_document") is not True
                or not isinstance(contents.get("pages"), list)
                or [page.get("page_idx") for page in contents["pages"] if isinstance(page, dict)]
                != list(range(pages))
                or any(not isinstance(page.get("blocks"), list) for page in page_data)
            ):
                return None
        elif not isinstance(contents, list):
            return None
        if origins[0].suffix == ".pdf" and len(PdfReader(str(origins[0])).pages) != pages:
            return None
        if origins[0].suffix != ".pdf" and pages != 1:
            return None
        markdown = required[1].read_text(encoding="utf-8")
        if not markdown.strip():
            return None
        if current:
            for asset in parent.glob("**/images/**/*"):
                asset = safe_path(asset, parent)
                if asset.is_file():
                    if not asset.stat().st_size:
                        return None
                    required.append(asset)
        image_refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
        image_refs += re.findall(r"<img\b[^>]*\bsrc=[\"\x27]([^\"\x27]+)", markdown, re.I)
        for reference in image_refs:
            reference = html.unescape(unquote(reference.strip().strip("<>")))
            image_path = safe_path(parent / relative_path(reference), directory)
            if not image_path.is_file() or image_path.stat().st_size == 0:
                return None
            required.append(image_path)
        return [
            {
                "relativePath": path.relative_to(directory).as_posix(),
                "sha256": file_hash(path),
                "sizeBytes": path.stat().st_size,
            }
            for path in sorted(set(required))
        ]
    except (OSError, ValueError, KeyError, TypeError, OcrError):
        return None
    except Exception:
        # A truncated PDF may raise several parser-specific exceptions.
        return None


def cached_mapping_valid(
    mapping: dict[str, Any],
    item: dict[str, Any],
    work: Path,
    output: Path,
    profile: dict[str, Any],
) -> bool:
    if (
        mapping.get("sourceSha256") != item["sha256"]
        or not compatible_cached_profile(mapping.get("engineProfile"), profile)
        or mapping.get("sourceRelativePath") != item["relative"]
        or mapping.get("pageCount") != item["pages"]
    ):
        return False
    try:
        directory = safe_path(work / relative_path(mapping["outputDir"]), output)
        snapshot = artifact_snapshot(directory, item["alias"], item["pages"])
        if snapshot is None or snapshot != mapping.get("artifacts"):
            return False
        if mapping.get("engineProfile", {}).get("transport") == TASK_SCHEMA:
            origin = next((entry for entry in snapshot if "_origin." in entry["relativePath"]), {})
            return origin.get("sha256") == item["sha256"]
        return True
    except (KeyError, TypeError, OcrError):
        return False


def compatible_cached_profile(previous: Any, current: dict[str, Any]) -> bool:
    if previous == current:
        return True
    # Keep separately identified, fully hashed MinerU 3 receipts as historical
    # results. Never relabel their engine or fabricate a schema-2.0 document.
    return (
        isinstance(previous, dict)
        and current.get("transport") == TASK_SCHEMA
        and set(previous) == {"imageId", "wrapperSha256", "options"}
        and previous["options"] == LEGACY_OPTIONS
        and all(
            isinstance(previous[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", previous[key])
            for key in ("imageId", "wrapperSha256")
        )
    )


def run_ocr(
    *,
    work: Path,
    inventory: dict[str, Any],
    output: Path,
    selected: list[str],
    wrapper: Path,
    powershell: Path,
    timeout: float,
    batch: bool = False,
    allowed_roots: list[Path] | None = None,
) -> dict[str, Any]:
    work, output = safe_path(work), safe_path(output, work)
    if output == work:
        raise OcrError("OCR 输出必须使用本案 work-dir 内的独立子目录")
    if not math.isfinite(timeout) or timeout <= 0:
        raise OcrError("OCR 超时必须是正数")
    items = validate_sources(inventory, selected, allowed_roots)
    # Signature compatibility: PowerShell is no longer invoked for recognition.
    with project_lock(work):
        profile = engine_profile(wrapper)
        output.mkdir(parents=True, exist_ok=True)
        return _run_locked(work, output, items, wrapper, powershell, profile, timeout, batch)


def task_groups(work, output, pending, profile, batch):
    remaining = {item["relative"]: item for item in pending}
    groups = []
    for run_dir in sorted(output.glob("run-*")):
        run_dir = safe_path(run_dir, output)
        path = run_dir / "attempt.json"
        if not path.is_file():
            continue
        try:
            attempt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise OcrError("OCR 原请求记录损坏；保留断点，不猜测或重发") from error
        if not isinstance(attempt, dict):
            raise OcrError("OCR 原请求记录无效；保留断点")
        if attempt.get("schemaVersion") != TASK_SCHEMA:
            continue
        if attempt.get("workDir") != str(work) or not isinstance(attempt.get("inputs"), list):
            raise OcrError("OCR 原任务与当前工作区不一致")
        matched = []
        for entry in attempt["inputs"]:
            if not isinstance(entry, dict):
                raise OcrError("OCR 原任务输入记录无效")
            item = remaining.get(entry.get("relative"))
            if item is not None and _input_receipt(item) == entry:
                matched.append(item)
        if not matched:
            continue
        if attempt.get("engineProfile") != profile:
            if attempt.get("submissionState") and attempt.get("taskStatus") not in {
                "completed",
                "partial",
                "failed",
                "canceled",
            }:
                raise OcrError("MinerU 原任务未结束且引擎已变化；保留断点")
            continue
        groups.append((matched, run_dir, attempt["inputs"]))
        for item in matched:
            del remaining[item["relative"]]
    left = list(remaining.values())
    groups.extend(
        (group, None, group) for group in ([left] if batch and left else [[item] for item in left])
    )
    return groups


def _run_locked(
    work: Path,
    output: Path,
    items: list[dict[str, Any]],
    wrapper: Path,
    powershell: Path,
    profile: dict[str, Any],
    timeout: float,
    batch: bool,
) -> dict[str, Any]:
    index_path = safe_path(work / "ocr-result.json", work)
    try:
        previous = (
            json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
        )
    except (ValueError, OSError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    previous_mappings = previous.get("mappings", [])
    if not isinstance(previous_mappings, list):
        previous_mappings = []
    mappings = {
        entry["sourceRelativePath"]: entry
        for entry in previous_mappings
        if isinstance(entry, dict) and isinstance(entry.get("sourceRelativePath"), str)
    }
    trusted = previous.get("schemaVersion") == SCHEMA and previous.get("workDir") == str(work)
    if not trusted:
        mappings = {
            key: {
                name: value
                for name, value in entry.items()
                if name not in {"engineProfile", "artifacts"}
            }
            for key, entry in mappings.items()
        }
    completed: set[str] = set()
    cached_count = 0
    for item in items:
        entry = mappings.get(item["relative"], {})
        if trusted and cached_mapping_valid(entry, item, work, output, profile):
            completed.add(item["relative"])
            cached_count += 1
        else:
            mappings.pop(item["relative"], None)
    result = {
        "schemaVersion": SCHEMA,
        "engine": "MinerU-Docker",
        "workDir": str(work),
        "startedAt": utc_now(),
        "status": "RUNNING",
        "mappings": [],
        "pending": [],
        "runStats": {
            "selectedFiles": len(items),
            "cachedFiles": cached_count,
            "legacyCachedFiles": sum(
                1
                for item in items
                if item["relative"] in completed
                and mappings[item["relative"]]["engineProfile"] != profile
            ),
            "engineInvocations": 0,
            "resumedTasks": 0,
            "batch": batch,
        },
    }
    started = time.monotonic()
    last_progress = float("-inf")

    def checkpoint() -> None:
        result["mappings"] = [mappings[key] for key in sorted(mappings)]
        result["pending"] = [
            item["relative"] for item in items if item["relative"] not in completed
        ]
        result["runStats"]["completedFiles"] = len(completed)
        result["runStats"]["durationSeconds"] = round(time.monotonic() - started, 3)
        result["updatedAt"] = utc_now()
        write_json(index_path, result)

    def accept(
        item: dict[str, Any], directory: Path, snapshot: list[dict[str, Any]], attempt_id: str
    ) -> None:
        if file_hash(item["source"]) != item["sha256"]:
            raise OcrError("识别期间源文件发生变化，未推进该文件断点")
        mappings[item["relative"]] = {
            "sourceRelativePath": item["relative"],
            "sourceSha256": item["sha256"],
            "pageCount": item["pages"],
            "outputDir": directory.relative_to(work).as_posix(),
            "engineProfile": profile,
            "artifacts": snapshot,
            "attemptId": attempt_id,
            "completedAt": utc_now(),
        }
        completed.add(item["relative"])
        checkpoint()

    # Unconfirmed output directories are never adopted as a cache. Atomic per-file
    # receipts survive process exit; damaged/lost receipts require re-recognition.
    checkpoint()
    pending = [item for item in items if item["relative"] not in completed]
    try:
        for group, run_dir, task_inputs in task_groups(work, output, pending, profile, batch):
            if run_dir is None:
                run_dir = output / ("run-" + uuid.uuid4().hex)
                input_dir = run_dir / "input"
                input_dir.mkdir(parents=True)
                attempt = {
                    "workDir": str(work),
                    "engineProfile": profile,
                    "inputs": [_input_receipt(item) for item in group],
                    "startedAt": utc_now(),
                    "schemaVersion": TASK_SCHEMA,
                    "executionMode": "V4_BATCH_API" if batch else "V4_SINGLE_FILE_API",
                }
                write_json(run_dir / "attempt.json", attempt)
                for item in group:
                    target = input_dir / item["inputName"]
                    shutil.copyfile(item["source"], target)
                    if file_hash(target) != item["sha256"]:
                        raise OcrError("OCR 隔离输入副本哈希不一致")
            else:
                input_dir = safe_path(run_dir / "input", run_dir)
                attempt = json.loads((run_dir / "attempt.json").read_text(encoding="utf-8"))
            directory = run_dir / ("result-" + uuid.uuid4().hex)
            directory.mkdir()
            if attempt.get("taskId"):
                result["runStats"]["resumedTasks"] += 1
            elif not attempt.get("submissionState"):
                result["runStats"]["engineInvocations"] += 1
            snapshots: dict[str, list[dict[str, Any]]] = {}
            last_scan = float("-inf")

            def poll(
                *,
                finished: bool = False,
                group=group,
                directory=directory,
                snapshots=snapshots,
                run_dir=run_dir,
            ) -> None:
                nonlocal last_scan, last_progress
                now = time.monotonic()
                if not finished and now - last_scan < 1.0:
                    return
                last_scan = now
                for item in group:
                    if item["relative"] in completed:
                        continue
                    snapshot = artifact_snapshot(directory, item["alias"], item["pages"])
                    if snapshot and (finished or snapshots.get(item["relative"]) == snapshot):
                        accept(item, directory, snapshot, run_dir.name)
                    elif snapshot:
                        snapshots[item["relative"]] = snapshot
                if finished or now - last_progress >= 2:
                    print(
                        f"OCR 已校验 {len(completed)}/{len(items)} 份，复用 {cached_count} 份，"
                        f"耗时 {now - started:.0f} 秒",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_progress = now

            checkpoint()
            try:
                if engine_profile(wrapper) != profile:
                    raise OcrError("本机 MinerU 版本或入口已变化，本次停止")
                code = run_task(
                    [input_dir / item["inputName"] for item in task_inputs],
                    timeout * len(group),
                    poll,
                    directory,
                    run_dir / "attempt.json",
                    profile,
                )
            finally:
                try:
                    unchanged = engine_profile(wrapper) == profile
                except OcrError:
                    unchanged = False
                if not unchanged:
                    for item in group:
                        completed.discard(item["relative"])
                        mappings.pop(item["relative"], None)
                    raise OcrError("识别期间 MinerU 版本或入口未确认一致，结果不进入缓存")
                poll(finished=True)
            if code != 0 or any(item["relative"] not in completed for item in group):
                raise OcrError("MinerU 本次未完整完成；已校验文件已保存，未完成文件不自动重试")
        result["status"] = "COMPLETED"
        result["completedAt"] = utc_now()
    except BaseException as error:
        result["status"] = "CANCELLED" if isinstance(error, KeyboardInterrupt) else "PARTIAL"
        result["errorType"] = type(error).__name__
        raise
    finally:
        checkpoint()
    return {"status": result["status"], **result["runStats"]}


def _input_receipt(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in ("relative", "sha256", "pages", "alias", "inputName")}
