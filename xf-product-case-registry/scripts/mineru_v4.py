"""The existing local MinerU 4 task API, with durable task IDs and safe ZIP output.

This client never starts/stops services, launches another model, or sends files
outside the loopback endpoint. Unknown submissions and failed tasks are retained.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

API_BASE = "http://127.0.0.1:18000"
IMAGE = "mineru:4.0.12-20261014"
TASK_SCHEMA = "MinerUV4TaskAttemptV1"
OPTIONS = {"tier": "standard", "ocrMode": "auto", "pageRange": "all", "outputFormats": ["zip"]}
TERMINAL = {"completed", "partial", "failed", "canceled"}
STATUSES = TERMINAL | {"queued", "running"}
MAX_ZIP_BYTES = 1024 * 1024 * 1024


class MinerUV4Error(RuntimeError):
    pass


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200 and response.status_code != 202:
        raise MinerUV4Error(f"MinerU 本机接口返回 {response.status_code}；保留原任务")
    try:
        value = response.json()
    except ValueError as error:
        raise MinerUV4Error("MinerU 本机接口 JSON 无效；保留原任务") from error
    if not isinstance(value, dict):
        raise MinerUV4Error("MinerU 本机接口响应必须是对象")
    return value


def engine_profile(_wrapper: Path) -> dict[str, Any]:
    """Bind the API to the active, pinned container, rather than retained :latest."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        active = subprocess.run(
            ["docker", "inspect", "mineru-api", "--format", "{{.Image}} {{.State.Running}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        image_id = result.stdout.strip()
        if (
            result.returncode
            or active.returncode
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or active.stdout.strip() != image_id + " true"
        ):
            raise MinerUV4Error("MinerU 4 固定镜像与活动 API 容器不一致；不启动替代容器")
        with httpx.Client(base_url=API_BASE, timeout=15, trust_env=False) as client:
            health = _json(client.get("/v1/health"))
        if health.get("status") != "ok" or health.get("version") != "4.0.12":
            raise MinerUV4Error("MinerU 4 本机服务未就绪或版本不符")
        return {
            "imageId": image_id,
            "engineVersion": health["version"],
            "transport": TASK_SCHEMA,
            "apiBase": API_BASE,
            "options": OPTIONS.copy(),
        }
    except (OSError, subprocess.TimeoutExpired, httpx.HTTPError) as error:
        raise MinerUV4Error("无法核对本机 MinerU 4 服务；不启动或重建服务") from error


def _task(value: dict[str, Any], expected: str | None = None) -> str:
    task_id = value.get("task_id")
    if (
        not isinstance(task_id, str)
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", task_id)
        or expected is not None
        and task_id != expected
        or value.get("status") not in STATUSES
        or not isinstance(value.get("files"), list)
    ):
        raise MinerUV4Error("MinerU 任务身份或状态无效；不重新提交")
    return task_id


def _member(name: str) -> PurePosixPath:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(char in name for char in ':<>"|?*')
        or any(ord(char) < 32 for char in name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise MinerUV4Error("MinerU 结果包含不安全路径")
    if any(
        part != part.rstrip(" .")
        or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I)
        for part in name.split("/")
    ):
        raise MinerUV4Error("MinerU 结果包含不安全的 Windows 文件名")
    return PurePosixPath(name)


def _unpack(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 10000 or sum(item.file_size for item in members) > MAX_ZIP_BYTES:
            raise MinerUV4Error("MinerU 结果包超出本次资源上限")
        seen = set()
        for item in members:
            path = _member(item.filename.rstrip("/") if item.is_dir() else item.filename)
            key = str(path).casefold()
            if key in seen or stat.S_ISLNK(item.external_attr >> 16):
                raise MinerUV4Error("MinerU 结果包含重复路径或符号链接")
            seen.add(key)
            target = destination.joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if item.file_size > max(item.compress_size, 1) * 1000:
                raise MinerUV4Error("MinerU 结果包压缩比例异常")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def materialize(
    archive: Path, destination: Path, inputs: list[Path], status: dict[str, Any]
) -> None:
    """Keep native schema 2.0 bytes and images; do not fabricate legacy pdf_info."""
    unpacked = destination / "native-zip"
    unpacked.mkdir()
    _unpack(archive, unpacked)
    expected = {path.name: path for path in inputs}
    observed = status["files"]
    if len(observed) != len(expected) or {
        item.get("name") for item in observed if isinstance(item, dict)
    } != set(expected):
        raise MinerUV4Error("MinerU 结果文件身份与本次输入不一致")
    manifest_path = unpacked / "manifest.json"
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _task(manifest, _task(status))
        known = {item["name"]: item for item in observed}
        if (
            manifest["status"] != status["status"]
            or len(manifest["files"]) != len(known)
            or {item.get("name") for item in manifest["files"]} != set(known)
            or any(
                any(
                    item.get(key) != known[item["name"]].get(key)
                    for key in ("status", "file_id", "page_range", "output_files")
                )
                for item in manifest["files"]
            )
        ):
            raise MinerUV4Error("MinerU ZIP 清单与原任务文件身份不一致")
    for record in observed:
        if record.get("status") != "completed":
            continue
        source = expected[record["name"]]
        alias = source.stem
        middles = list(unpacked.glob("**/middle_json.json"))
        candidates = [
            path
            for path in middles
            if alias in path.relative_to(unpacked).parts
            or source.name in path.relative_to(unpacked).parts
        ]
        if manifest is not None:
            ordinal = next(
                index
                for index, item in enumerate(manifest["files"], 1)
                if item["name"] == source.name
            )
            candidate = unpacked / f"{ordinal:04}" / "middle_json.json"
            candidates = [candidate] if candidate.is_file() else []
        if not candidates and len(inputs) == 1 and len(middles) == 1:
            candidates = middles
        if len(candidates) != 1:
            raise MinerUV4Error("MinerU ZIP 无法唯一绑定输入文件")
        folder = candidates[0].parent
        required = ("middle_json.json", "markdown.md", "structured_content.json")
        if any(not (folder / name).is_file() for name in required):
            raise MinerUV4Error("MinerU 结果缺少正文、结构或内容索引")
        target = destination / alias
        shutil.copytree(folder, target)
        for before, after in (
            ("middle_json.json", alias + "_middle.json"),
            ("markdown.md", alias + ".md"),
            ("structured_content.json", alias + "_structured_content.json"),
        ):
            (target / before).rename(target / after)
        shutil.copyfile(source, target / (alias + "_origin" + source.suffix))


def run_task(
    inputs: list[Path],
    timeout: float,
    poll: Any,
    destination: Path,
    attempt_path: Path,
    profile: dict[str, Any],
) -> int:
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    if attempt.get("engineProfile") != profile or profile.get("apiBase") != API_BASE:
        raise MinerUV4Error("MinerU 任务绑定已变化；不重新提交")
    records = {item["inputName"]: item for item in attempt["inputs"]}
    if set(records) != {path.name for path in inputs} or any(
        _digest(path) != records[path.name]["sha256"] for path in inputs
    ):
        raise MinerUV4Error("MinerU 隔离输入哈希不一致；保留原任务")
    task_id = attempt.get("taskId")
    if not task_id and attempt.get("submissionState"):
        raise MinerUV4Error("MinerU 上次提交结果未知；保留请求断点，不自动重发")
    started = time.monotonic()
    with httpx.Client(base_url=API_BASE, timeout=min(timeout, 30), trust_env=False) as client:
        try:
            if not task_id:
                attempt["submissionState"] = "SUBMITTING"
                _write(attempt_path, attempt)
                params = [
                    ("tier", "standard"),
                    ("ocr_mode", "auto"),
                    ("output_formats", "zip"),
                ]
                # Non-PDF inputs use full-document parsing and reject page_range.
                # In a mixed task the default full-document range also covers PDFs.
                if all(path.suffix == ".pdf" for path in inputs):
                    params.append(("page_range", "all"))
                with ExitStack() as stack:
                    files = [
                        (
                            "files",
                            (
                                path.name,
                                stack.enter_context(path.open("rb")),
                                "application/pdf"
                                if path.suffix == ".pdf"
                                else "image/" + ("png" if path.suffix == ".png" else "jpeg"),
                            ),
                        )
                        for path in inputs
                    ]
                    response = client.post("/v1/tasks", params=params, files=files)
                    attempt["submissionResponseStatus"] = response.status_code
                    if response.status_code not in {200, 202}:
                        attempt["submissionState"] = "REJECTED"
                        _write(attempt_path, attempt)
                    value = _json(response)
                task_id = _task(value)
                attempt.update(taskId=task_id, submissionState="SUBMITTED")
                _write(attempt_path, attempt)
            while True:
                poll()
                if time.monotonic() - started >= timeout:
                    raise MinerUV4Error("OCR 运行超时；保留原任务，下次仅继续查询和取回")
                value = _json(client.get("/v1/tasks/" + task_id))
                _task(value, task_id)
                attempt["taskStatus"] = value["status"]
                attempt["taskFiles"] = [
                    {key: item.get(key) for key in ("name", "status", "error")}
                    for item in value["files"]
                    if isinstance(item, dict)
                ]
                _write(attempt_path, attempt)
                if value["status"] in TERMINAL:
                    break
                time.sleep(min(1, max(0, timeout - (time.monotonic() - started))))
            if value["status"] in {"failed", "canceled"}:
                raise MinerUV4Error("MinerU 原任务已失败或取消；保留失败证据，不自动重试")
            archive = destination / "result.zip"
            temporary = destination / "result.zip.part"
            with client.stream(
                "GET", "/v1/tasks/" + task_id + "/result", params={"response_format": "zip"}
            ) as response:
                if response.status_code != 200:
                    raise MinerUV4Error("MinerU 结果尚未完整取得；保留原任务")
                size = 0
                with temporary.open("xb") as stream:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_ZIP_BYTES or time.monotonic() - started >= timeout:
                            raise MinerUV4Error("MinerU 结果下载达到资源或时间上限；保留原任务")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            temporary.rename(archive)
            attempt["resultSha256"] = _digest(archive)
            _write(attempt_path, attempt)
            materialize(archive, destination, inputs, value)
            return 0 if value["status"] == "completed" else 1
        except BaseException as error:
            attempt["lastErrorType"] = type(error).__name__
            _write(attempt_path, attempt)
            if isinstance(error, httpx.HTTPError):
                raise MinerUV4Error("MinerU 请求中断；保留原任务，不自动重发") from error
            if isinstance(error, (OSError, ValueError, zipfile.BadZipFile)):
                raise MinerUV4Error("MinerU 结果或本地回执无效；保留原任务和产物") from error
            raise
