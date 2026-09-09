"""Local MinerU execution, content-bound per-file checkpoints and bounded progress.

The existing Docker image and recognition settings remain unchanged. Batch mode
uses its native one-request queue to avoid submission timeouts during cold start.
No credentials, remote OCR, business uploads, or business waterline updates live here.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from pypdf import PdfReader

SCHEMA = "OcrResultV1"
OPTIONS = {
    "backend": "hybrid-auto-engine",
    "method": "auto",
    "lang": "ch",
    "table": True,
    "formula": True,
}
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class OcrError(RuntimeError):
    pass


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


def engine_profile(wrapper: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "mineru:latest", "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OcrError("无法核对本机 MinerU 镜像版本") from error
    image_id = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise OcrError("本机 MinerU 镜像版本不可核实，不使用旧缓存")
    return {"imageId": image_id, "wrapperSha256": file_hash(wrapper), "options": OPTIONS.copy()}


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
        required = [
            middle_path,
            parent / f"{alias}.md",
            parent / f"{alias}_content_list.json",
            parent / f"{alias}_origin.pdf",
        ]
        if any(
            not safe_path(path, directory).is_file() or path.stat().st_size == 0
            for path in required
        ):
            return None
        middle = json.loads(middle_path.read_text(encoding="utf-8"))
        page_data = middle.get("pdf_info")
        if not isinstance(page_data, list) or len(page_data) != pages:
            return None
        if [page.get("page_idx") for page in page_data if isinstance(page, dict)] != list(
            range(pages)
        ):
            return None
        contents = json.loads(required[2].read_text(encoding="utf-8"))
        if not isinstance(contents, list):
            return None
        if len(PdfReader(str(required[3])).pages) != pages:
            return None
        markdown = required[1].read_text(encoding="utf-8")
        if not markdown.strip():
            return None
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
        or mapping.get("engineProfile") != profile
        or mapping.get("sourceRelativePath") != item["relative"]
        or mapping.get("pageCount") != item["pages"]
    ):
        return False
    try:
        directory = safe_path(work / relative_path(mapping["outputDir"]), output)
        snapshot = artifact_snapshot(directory, item["alias"], item["pages"])
        return snapshot is not None and snapshot == mapping.get("artifacts")
    except (KeyError, TypeError, OcrError):
        return False


def _mount_source_key(value: str) -> str:
    value = value.replace("\\", "/").rstrip("/")
    prefix = "/run/desktop/mnt/host/"
    if value.startswith(prefix) and len(value) > len(prefix) + 1:
        tail = value[len(prefix) :]
        value = tail[0] + ":/" + tail[2:]
    return value.casefold() if os.name == "nt" else value


def stop_owned_container(output_dir: Path, image_id: str) -> None:
    """Only stop a container with this invocation's unique /work/output bind mount."""
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"ancestor={image_id}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise OcrError("本次 OCR 容器退出状态无法确认")
    for container in result.stdout.split():
        if not re.fullmatch(r"[0-9a-f]{12,64}", container):
            raise OcrError("本次 OCR 容器标识无法确认")
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if inspected.returncode != 0:
            continue  # The short-lived container may already have exited.
        mounts = json.loads(inspected.stdout)
        if any(
            mount.get("Type") == "bind"
            and mount.get("Destination") == "/work/output"
            and _mount_source_key(mount.get("Source", "")) == _mount_source_key(str(output_dir))
            for mount in mounts
        ):
            stopped = subprocess.run(
                ["docker", "stop", "--time", "10", container],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if stopped.returncode != 0:
                raise OcrError("本次 OCR 容器未确认停止，保留断点")


def run_process(
    command: list[str], timeout: float, on_poll: Any, output_dir: Path, image_id: str
) -> int:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
    )
    started = time.monotonic()
    try:
        while process.poll() is None:
            on_poll()
            if time.monotonic() - started >= timeout:
                raise OcrError("OCR 运行超时；已校验文件保留，未完成文件不自动重试")
            time.sleep(0.25)
        return int(process.returncode)
    except BaseException:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stop_owned_container(output_dir, image_id)
        raise


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
    if not wrapper.is_file() or not powershell.is_file():
        raise OcrError("MinerU 或 PowerShell 7 入口不存在")
    with project_lock(work):
        profile = engine_profile(wrapper)
        output.mkdir(parents=True, exist_ok=True)
        return _run_locked(work, output, items, wrapper, powershell, profile, timeout, batch)


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
            "engineInvocations": 0,
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
    groups = [pending] if batch and pending else [[item] for item in pending]
    try:
        for group in groups:
            run_dir = output / ("run-" + uuid.uuid4().hex)
            input_dir, directory = run_dir / "input", run_dir / "result"
            input_dir.mkdir(parents=True)
            directory.mkdir()
            attempt = {
                "workDir": str(work),
                "engineProfile": profile,
                "inputs": [_input_receipt(item) for item in group],
                "startedAt": utc_now(),
                "executionMode": "BATCH_SINGLE_QUEUE" if batch else "EXISTING_WRAPPER",
            }
            write_json(run_dir / "attempt.json", attempt)
            for item in group:
                target = input_dir / item["inputName"]
                shutil.copyfile(item["source"], target)
                if file_hash(target) != item["sha256"]:
                    raise OcrError("OCR 隔离输入副本哈希不一致")
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

            source_arg = input_dir if batch else input_dir / group[0]["inputName"]
            command = [
                str(powershell),
                "-NoProfile",
                "-File",
                str(wrapper),
                "-Path",
                str(source_arg),
                "-Output",
                str(directory),
                "-Backend",
                OPTIONS["backend"],
                "-Method",
                OPTIONS["method"],
                "-Lang",
                OPTIONS["lang"],
                "-NoBuild",
            ]
            if batch:
                # MinerU 3.1.4's default concurrent submissions can hit its internal
                # 60-second POST timeout while the first model is warming up.
                # Use its supported queue setting, not a patched engine or service.
                command = [
                    "docker",
                    "run",
                    "--rm",
                    "--gpus",
                    "all",
                    "--ipc",
                    "host",
                    "-e",
                    "MINERU_MODEL_SOURCE=local",
                    "-e",
                    "MINERU_API_MAX_CONCURRENT_REQUESTS=1",
                    "-v",
                    f"{input_dir.as_posix()}:/work/input:ro",
                    "-v",
                    f"{directory.as_posix()}:/work/output",
                    profile["imageId"],
                    "mineru",
                    "-p",
                    "/work/input",
                    "-o",
                    "/work/output",
                    "-b",
                    OPTIONS["backend"],
                    "-m",
                    OPTIONS["method"],
                    "-l",
                    OPTIONS["lang"],
                    "-t",
                    "true",
                    "-f",
                    "true",
                ]
            result["runStats"]["engineInvocations"] += 1
            checkpoint()
            try:
                if engine_profile(wrapper) != profile:
                    raise OcrError("本机 MinerU 版本或入口已变化，本次停止")
                code = run_process(
                    command, timeout * len(group), poll, directory, profile["imageId"]
                )
            finally:
                poll(finished=True)
            if engine_profile(wrapper) != profile:
                for item in group:
                    completed.discard(item["relative"])
                    mappings.pop(item["relative"], None)
                raise OcrError("识别期间 MinerU 版本或入口发生变化，结果不进入缓存")
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
