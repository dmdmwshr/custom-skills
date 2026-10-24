#!/usr/bin/env python3
"""video-intake: 公开视频下载、提取、BiliNote 笔记、状态/恢复与来源索引的唯一核心。

仅用 Python 标准库；yt-dlp / ffmpeg / ffprobe 为外部引擎，BiliNote 为本机 HTTP 后端。
退出码：0 成功；1 明确失败；2 参数错误；3 结果未知（超时/中断，保留现场，不得盲目重放）。
`note` 子命令保持旧 save-video-note.ps1 的参数含义、JSON 输出键和失败时 exit 1。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_ROOT / "references" / "local-config.json"
DEFAULT_TEMPLATE = SKILL_ROOT / "references" / "video-note-template.md"
MEDIA_EXT = (".mp4", ".mkv", ".webm", ".mov")
UUID_RE = r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_UNKNOWN = 0, 1, 2, 3


class IntakeError(Exception):
    """明确失败（现场已核实，可修正后重试）。"""

    category = "failed"


class UnknownOutcome(IntakeError):
    """结果未知：保留现场，先对账再决定。"""

    category = "unknown"


# ---------------------------------------------------------------- 通用工具
def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def emit(value) -> None:
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sys.stdout.flush()


def load_config(path: str | None) -> dict:
    chosen = path or os.environ.get("VIDEO_INTAKE_CONFIG")
    return read_json(Path(chosen) if chosen else DEFAULT_CONFIG)


def expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def canonical_url(url: str) -> str:
    match = re.search(r"https?://[^\s]+", url)
    trimmed = match.group(0).strip() if match else url.strip()
    try:
        parts = urllib.parse.urlsplit(trimmed)
        if not parts.scheme or not parts.netloc:
            return trimmed
        rebuilt = urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
        return rebuilt.rstrip("/")
    except ValueError:
        return trimmed


def detect_platform(url: str) -> str:
    value = url.lower()
    if re.search(r"bilibili\.com|b23\.tv", value):
        return "bilibili"
    if re.search(r"douyin\.com|iesdouyin\.com", value):
        return "douyin"
    raise IntakeError("无法从 URL 识别平台，只支持 bilibili 和 douyin。")


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def canonical_video_url(platform: str, source_id: str, original: str) -> str:
    if platform == "bilibili":
        return f"https://www.bilibili.com/video/{source_id}"
    if platform == "douyin":
        return f"https://www.douyin.com/video/{source_id}"
    return canonical_url(original)


def safe_file_name(text: str) -> str:
    name = (text or "").strip() or "video-note"
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = name.strip(". ").strip()
    return name or "video-note"


def source_key(platform: str, source_id: str) -> str:
    return f"{platform}:{source_id}"


# ---------------------------------------------------------------- 引擎解析
def find_tool(name: str, config: dict, explicit: str | None = None) -> str | None:
    """显式参数 > 环境变量 VIDEO_INTAKE_<NAME> > 配置 tools.<name> > PATH。"""
    env_key = "VIDEO_INTAKE_" + name.upper().replace("-", "_")
    for candidate in (explicit, os.environ.get(env_key), (config.get("tools") or {}).get(name)):
        if candidate:
            candidate = expand(candidate)
            if Path(candidate).is_file():
                return candidate
    return shutil.which(name)


def run_tool(args, timeout):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise UnknownOutcome(f"外部工具超时，结果未知：{Path(args[0]).name}") from exc
    except OSError as exc:
        raise IntakeError(f"无法启动外部工具 {Path(args[0]).name}：{exc.__class__.__name__}") from exc


def probe_media(path: Path, ffprobe: str | None, expected_duration: float | None = None, require_audio: bool = True) -> dict:
    if not path.is_file() or path.stat().st_size <= 0:
        raise IntakeError("媒体文件不存在或为空。")
    if not ffprobe:
        raise IntakeError("缺少 ffprobe，无法核验时长与音视频轨道。")
    result = run_tool([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], 60)
    if result.returncode != 0:
        raise IntakeError("ffprobe 无法读取媒体文件。")
    data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    kinds = [s.get("codec_type") for s in data.get("streams", [])]
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise IntakeError("媒体时长无效。")
    if "video" not in kinds:
        raise IntakeError("媒体缺少视频轨道。")
    if require_audio and "audio" not in kinds:
        raise IntakeError("媒体缺少音频轨道。")
    if expected_duration and abs(duration - expected_duration) > max(2.0, expected_duration * 0.05):
        raise IntakeError("媒体时长与元数据不一致，疑似未下载完整。")
    return {"duration": round(duration, 3), "video_tracks": kinds.count("video"), "audio_tracks": kinds.count("audio")}


# ---------------------------------------------------------------- 索引（追加式）
class Index:
    def __init__(self, work_root: Path | None):
        self.path = (Path(work_root) / "video-intake-index.jsonl") if work_root else None

    def entries(self):
        if not self.path or not self.path.is_file():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"event": "corrupt_line"})
        return rows

    def append(self, event: str, **fields) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": now_iso(), "event": event, **fields}
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def last_completed(self, key: str | None = None, sha256: str | None = None, url: str | None = None):
        found = None
        for row in self.entries():
            if row.get("event") not in ("downloaded", "adopted"):
                continue
            if ((key and row.get("source_key") == key) or (sha256 and row.get("sha256") == sha256)
                    or (url and row.get("source_url") == url)):
                found = row
        return found


class Lock:
    """每来源一个排他锁；陈旧锁不自动清除，报告未知交人工对账。

    外部下载超时（结果未知）时 keep=True：锁带 unknown 标记保留，下次需 --clear-unknown-lock 明确确认旧进程已结束。
    """

    def __init__(self, work_root: Path | None, key: str, clear_unknown: bool = False):
        self.path = None
        self.keep = False
        self.clear_unknown = clear_unknown
        if work_root:
            self.path = Path(work_root) / "locks" / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".lock")

    def __enter__(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.clear_unknown and self.path.is_file() and self.path.read_text(encoding="utf-8", errors="ignore").startswith("unknown"):
                self.path.unlink()
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise UnknownOutcome(f"同一来源已有在途或遗留锁：{self.path.name}，先核对现场，不重复提交。")
            os.write(fd, f"{os.getpid()} {now_iso()}".encode())
            os.close(fd)
        return self

    def mark_unknown(self):
        self.keep = True
        if self.path and self.path.exists():
            self.path.write_text(f"unknown {os.getpid()} {now_iso()} 外部下载超时，需确认旧进程已结束后用 --clear-unknown-lock", encoding="utf-8")

    def __exit__(self, *exc):
        if self.path and self.path.exists() and not self.keep:
            self.path.unlink()


# ---------------------------------------------------------------- download / extract
def classify_ytdlp_failure(stderr: str) -> str:
    text = stderr.lower()
    if any(x in text for x in ("login", "cookies", "sign in", "private")):
        return "login_required"
    if any(x in text for x in ("unsupported url", "unable to extract", "fresh cookies", "extractor")):
        return "extractor_incompatible"
    if any(x in text for x in ("http error 4", "forbidden", "not available", "removed")):
        return "unavailable"
    if any(x in text for x in ("timed out", "connection", "network", "temporary failure", "incomplete")):
        return "transport"
    return "other"


def ytdlp_base(ytdlp: str) -> list:
    return [ytdlp, "--ignore-config", "--no-playlist", "--no-warnings"]


def fetch_metadata(ytdlp: str, url: str) -> dict:
    result = run_tool(ytdlp_base(ytdlp) + ["--simulate", "--dump-single-json", url], 180)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace")
        raise IntakeError(f"元数据查询失败（{classify_ytdlp_failure(err)}）。")
    return json.loads(result.stdout.decode("utf-8", "replace"))


def source_identity(url: str, meta: dict) -> tuple[str, str]:
    try:
        platform = detect_platform(meta.get("webpage_url") or url)
    except IntakeError:
        platform = (meta.get("extractor_key") or "generic").lower()
    source_id = str(meta.get("id") or url_hash(url))
    return platform, source_id


def finalize_media(path: Path, url: str, platform: str, source_id: str, title: str, args, config, index: Index,
                   action: str, expected_duration=None, reused=False, extra=None) -> dict:
    ffprobe = find_tool("ffprobe", config, args.ffprobe)
    info = probe_media(path, ffprobe, expected_duration, require_audio=not args.allow_silent)
    digest = sha256_file(path)
    key = source_key(platform, source_id)
    prior = index.last_completed(sha256=digest)
    duplicate_of = prior["source_key"] if prior and prior.get("source_key") != key else None
    receipt = {
        "success": True, "status": "completed", "action": "download", "mode": action,
        "source_url": url, "platform": platform, "source_id": source_id, "source_key": key,
        "file_path": str(path), "bytes": path.stat().st_size, "sha256": digest, "title": title,
        "reused": reused, "duplicate_of": duplicate_of, "finished_at": now_iso(), **info, **(extra or {}),
    }
    return receipt


def cmd_download(args, config):
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(args.work_root) if args.work_root else out_dir
    index = Index(work_root)
    ytdlp = find_tool("yt-dlp", config, args.yt_dlp)
    url = args.url
    if args.adopt:
        if not (args.source_id and args.url):
            raise IntakeError("--adopt 需要同时给出 --url 与 --source-id。")
        path = Path(args.adopt).resolve()
        platform = args.platform or detect_platform(url)
        receipt = finalize_media(path, url, platform, args.source_id, args.title or path.stem, args, config, index, "adopted")
        _save_receipt(receipt, args, out_dir)
        index.append("adopted", **_index_fields(receipt, args))
        return receipt
    if not ytdlp:
        raise IntakeError("未找到 yt-dlp。")
    known = index.last_completed(url=url)
    if known and Path(known.get("file_path", "")).is_file() and sha256_file(Path(known["file_path"])) == known.get("sha256"):
        # 同一 URL 已有核验通过的成品：离线复用，不再访问站点
        receipt = finalize_media(Path(known["file_path"]), url, known["platform"], known["source_id"], Path(known["file_path"]).stem,
                                 args, config, index, "reuse", reused=True)
        _save_receipt(receipt, args, out_dir)
        index.append("reused", **_index_fields(receipt, args))
        return receipt
    meta = fetch_metadata(ytdlp, url)
    platform, source_id = source_identity(url, meta)
    key = source_key(platform, source_id)
    title = str(meta.get("title") or source_id)
    with Lock(work_root, key, getattr(args, "clear_unknown_lock", False)) as lock:
        prior = index.last_completed(key=key)
        if prior and Path(prior.get("file_path", "")).is_file():
            existing = Path(prior["file_path"])
            if sha256_file(existing) == prior.get("sha256"):
                receipt = finalize_media(existing, url, platform, source_id, title, args, config, index, "reuse",
                                         meta.get("duration"), reused=True)
                _save_receipt(receipt, args, out_dir)
                index.append("reused", **_index_fields(receipt, args))
                return receipt
            # 已登记成品被改动：不在原路径继续下载，也不把变动文件重新签成原来源
            index.append("integrity_mismatch", source_key=key, source_url=url, file_path=str(existing), expected_sha256=prior.get("sha256"))
            raise IntakeError("索引中的已下载成品摘要已变化，疑似被人工或其他程序改动；原件、原回执与索引保持不动。"
                              "请先核对该文件，需要重新下载请先移走它，需要接受现状请用 --adopt 登记。")
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in out_dir.iterdir() if p.is_file()}
        command = ytdlp_base(ytdlp) + ["--continue", "--no-overwrites", "--retries", "3", "--fragment-retries", "3",
                                       "--socket-timeout", "30", "--paths", str(out_dir), "--output",
                                       "%(title).150B [%(id)s].%(ext)s", "--print", "after_move:filepath", url]
        ffmpeg = find_tool("ffmpeg", config, args.ffmpeg)
        if ffmpeg:
            command[1:1] = ["--ffmpeg-location", ffmpeg]
        index.append("started", source_key=key, source_url=url)
        try:
            result = run_tool(command, args.timeout)
        except UnknownOutcome:
            lock.mark_unknown()
            index.append("unknown", source_key=key, source_url=url, reason="download_timeout")
            raise
        stderr = result.stderr.decode("utf-8", "replace")
        printed = [ln.strip() for ln in result.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
        if result.returncode != 0 or not printed:
            category = classify_ytdlp_failure(stderr)
            index.append("failed", source_key=key, source_url=url, category=category)
            raise IntakeError(f"下载失败（{category}）；保留 .part 断点，可沿同一参数重试。")
        final = Path(printed[-1])
        resolved = final if final.is_absolute() else out_dir / final
        stat = resolved.stat() if resolved.is_file() else None
        if stat is not None and before.get(resolved) == (stat.st_size, stat.st_mtime_ns):
            # --no-overwrites 返回了本次没有写入的既有文件：来源未核实，不签成功回执
            index.append("failed", source_key=key, source_url=url, category="preexisting_unverified")
            raise IntakeError(f"目标路径已有本次未写入的文件：{resolved.name}；来源无法核实，未生成回执。"
                              "确认它就是该来源请用 --adopt 登记，否则先移走再下载。")
        receipt = finalize_media(final, url, platform, source_id, title, args, config, index, "download", meta.get("duration"))
        _save_receipt(receipt, args, out_dir)
        index.append("downloaded", **_index_fields(receipt, args))
        return receipt


def _index_fields(receipt: dict, args) -> dict:
    return {k: receipt.get(k) for k in ("source_key", "source_id", "platform", "source_url", "sha256", "file_path", "bytes")} | {
        "receipt": getattr(args, "receipt", None)}


def _save_receipt(receipt: dict, args, out_dir: Path) -> None:
    path = Path(args.receipt) if getattr(args, "receipt", None) else out_dir / f"receipt-{receipt['platform']}-{receipt['source_id']}.json"
    receipt["receipt_path"] = str(path)
    if path.is_file():
        try:
            old = read_json(path)
        except (OSError, ValueError):
            old = None
        if not (isinstance(old, dict) and old.get("sha256") == receipt.get("sha256") and old.get("file_path") == receipt.get("file_path")):
            # 内容不同的既有回执不覆盖：改名保留为 .prev
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            os.replace(path, path.with_name(f"{path.stem}.prev-{stamp}-{uuid.uuid4().hex[:6]}{path.suffix}"))
    write_json(path, receipt)


def cmd_extract(args, config):
    source = None
    if args.receipt:
        data = read_json(Path(args.receipt))
        if data.get("status") != "completed":
            raise IntakeError("下载回执未完成，不提取。")
        source = Path(data["file_path"])
        if not source.is_file() or sha256_file(source) != data.get("sha256"):
            raise IntakeError("媒体文件缺失或摘要与回执不一致。")
    elif args.file:
        source = Path(args.file)
    if args.subs:
        ytdlp = find_tool("yt-dlp", config, args.yt_dlp)
        if not (ytdlp and args.url):
            raise IntakeError("字幕提取需要 --url 与 yt-dlp。")
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        r = run_tool(ytdlp_base(ytdlp) + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "zh.*,en",
                                           "--paths", str(out), "--output", "%(id)s.%(ext)s", args.url], args.timeout)
        files = sorted(p.name for p in out.glob("*") if p.suffix in (".vtt", ".srt", ".ass", ".json3"))
        return {"success": r.returncode == 0, "action": "extract", "mode": "subtitles", "files": files,
                "message": "" if files else "站点未提供字幕。"}
    if not source or not source.is_file():
        raise IntakeError("缺少有效的媒体文件或回执。")
    ffprobe = find_tool("ffprobe", config, args.ffprobe)
    info = probe_media(source, ffprobe, require_audio=False)
    result = {"success": True, "action": "extract", "source_file": str(source), "probe": info}
    if args.audio:
        ffmpeg = find_tool("ffmpeg", config, args.ffmpeg)
        if not ffmpeg:
            raise IntakeError("未找到 ffmpeg。")
        if info["audio_tracks"] == 0:
            raise IntakeError("媒体没有音频轨道。")
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        target = out / (source.stem + f".{args.audio_format}")
        if target.exists():
            raise IntakeError(f"目标已存在，不覆盖：{target.name}")
        r = run_tool([ffmpeg, "-v", "error", "-n", "-i", str(source), "-vn", str(target)], args.timeout)
        if r.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
            raise IntakeError("音频提取失败。")
        result["audio"] = {"file_path": str(target), "bytes": target.stat().st_size, "sha256": sha256_file(target)}
    return result


# ---------------------------------------------------------------- BiliNote HTTP
class Backend:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, path: str, body=None, timeout=120):
        """GET 为只读，失败可重试；POST 可能已被服务端受理，连接中断/超时/回包异常一律记为结果未知。"""
        data = None
        headers = {"Accept": "application/json"}
        method = "GET"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        where = f"[{method} {self.base}{path}]"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode("utf-8", "replace")).get("msg")
            except Exception:
                msg = None
            detail = f"API 调用失败 {where}：{msg or 'HTTP ' + str(exc.code)}"
            if method == "POST" and exc.code >= 500:
                raise UnknownOutcome(detail + "（服务端 5xx，任务可能已受理，先对账）")
            raise IntakeError(detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            detail = f"API 调用失败 {where}：{exc}"
            # 连接被拒或域名解析失败：请求没有到达服务端，是明确未执行
            not_sent = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
            if method == "POST" and not not_sent:
                raise UnknownOutcome(detail + "（请求可能已被受理，保留进度，先对账不重发）")
            raise IntakeError(detail)
        try:
            return json.loads(text) if text.strip() else None
        except ValueError as exc:
            if method == "POST":
                raise UnknownOutcome(f"API 回包无法解析 {where}，任务可能已受理，先对账不重发。") from exc
            raise IntakeError(f"API 回包无法解析 {where}。") from exc

    def upload(self, file_path: Path, upload_name: str) -> str:
        boundary = uuid.uuid4().hex
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{upload_name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        body = head + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(self.base + "/api/upload", data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            raise IntakeError("BiliNote 本机媒体上传未核实，保留原视频。") from exc
        expected = "/uploads/" + upload_name
        if payload.get("code") != 0 or str((payload.get("data") or {}).get("url")) != expected:
            raise IntakeError("BiliNote 本机媒体上传未核实，保留原视频。")
        return expected


def is_loopback(base: str) -> bool:
    parts = urllib.parse.urlsplit(base)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in ("127.0.0.1", "localhost", "::1")


def resolve_model(config: dict, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    request = config.get("default_request", {})
    role = request.get("model_role", "")
    roles_path = config.get("model_roles_file", "")
    if role and roles_path:
        if role not in ("strong", "light"):
            raise IntakeError("中央模型角色必须是 strong 或 light。")
        path = Path(expand(roles_path))
        if path.is_file():
            if path.stat().st_size > 4096:
                raise IntakeError("中央模型配置超出预期大小。")
            roles = read_json(path)
            if role not in roles:
                raise IntakeError(f"中央模型配置缺少角色：{role}")
            model = roles[role] if isinstance(roles[role], str) else ""
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$", model) or model in ("strong", "light"):
                raise IntakeError(f"中央模型角色缺少有效型号：{role}")
            return model.strip()
    return str(request.get("model_name", ""))


def resolve_verified_media(receipt_path: str, url: str, platform: str) -> dict:
    if platform != "douyin":
        raise IntakeError("已核验媒体入口目前仅支持抖音来源。")
    receipt = read_json(Path(receipt_path))
    if not (receipt.get("success") is True and receipt.get("status") == "completed" and receipt.get("action") == "download"
            and receipt.get("source_url") == url):
        raise IntakeError("下载回执的模式、来源或完成状态不一致。")
    source_id = str(receipt.get("source_id", ""))
    if not re.match(r"^\d{5,30}$", source_id) or not re.match(r"^[a-fA-F0-9]{64}$", str(receipt.get("sha256", ""))):
        raise IntakeError("下载回执缺少有效来源编号或摘要。")
    file = Path(receipt.get("file_path", ""))
    if (not file.is_file() or file.suffix.lower() not in MEDIA_EXT or file.stat().st_size <= 0
            or file.stat().st_size != receipt.get("bytes")):
        raise IntakeError("正式视频文件与回执不一致。")
    if not re.search(r"\[" + re.escape(source_id) + r"\]\.[^.]+$", file.name):
        raise IntakeError("正式视频文件名与来源编号不一致。")
    digest = sha256_file(file)
    if digest != str(receipt["sha256"]).lower():
        raise IntakeError("正式视频摘要已变化，未提交笔记任务。")
    return {"receipt_path": str(Path(receipt_path).resolve()), "file_path": str(file), "sha256": digest, "source_id": source_id,
            "title": str(receipt.get("title", "")), "extension": file.suffix}


def resolve_resume(progress_path: Path, url: str, media: dict | None, result_dir: Path, evidence_dir: Path) -> dict:
    if media is None:
        raise IntakeError("恢复笔记必须提供同一来源的已核验视频回执。")
    progress = read_json(progress_path)
    task = str(progress.get("task_id", ""))
    if not re.match(UUID_RE, task) or progress.get("source_url") != url or progress.get("processing_platform") != "local":
        raise IntakeError("原笔记任务的来源、编号或处理平台未核实。")
    uploaded = str(progress.get("uploaded_path", ""))
    if not re.match(r"^/uploads/douyin-" + re.escape(media["source_id"]) + r"-[a-f0-9]{32}\.(mp4|mkv|webm|mov)$", uploaded):
        raise IntakeError("原笔记任务的本机上传来源未核实。")
    upload_file = result_dir.parent / uploaded.lstrip("/")
    if not upload_file.is_file() or sha256_file(upload_file) != media["sha256"]:
        raise IntakeError("原上传视频与正式下载摘要不一致，未恢复笔记。")
    status_path = result_dir / f"{task}.status.json"
    state = read_json(status_path)
    if state.get("status") not in ("FAILED", "SUCCESS"):
        raise IntakeError("原任务仍在处理或结果不明，不重复提交。")
    transcript_path = result_dir / f"{task}_transcript.json"
    transcript = read_json(transcript_path)
    audio_path = result_dir / f"{task}_audio.json"
    audio = read_json(audio_path)
    if (not str(transcript.get("full_text", "")).strip() or not transcript.get("segments")
            or not Path(str(audio.get("file_path", ""))).is_file()):
        raise IntakeError("原任务的转写或音频缓存未核实，不重新下载或转写。")
    if evidence_dir.exists():
        raise IntakeError("恢复基线已存在，请回读原恢复结果，不重复提交。")
    evidence_dir.mkdir(parents=True)
    files = [progress_path, status_path, transcript_path, audio_path]
    for suffix in ("_markdown.status.json", "_markdown.gpt.checkpoint.json", ".json"):
        candidate = result_dir / (task + suffix)
        if candidate.is_file():
            files.append(candidate)
    manifest = []
    for file in files:
        target = evidence_dir / file.name
        shutil.copy2(file, target)
        digest = sha256_file(target)
        if digest != sha256_file(file):
            raise IntakeError("恢复基线复制摘要不一致。")
        manifest.append({"source": str(file), "snapshot": str(target), "sha256": digest})
    write_json(evidence_dir / "manifest.json", manifest)
    return {"task_id": task, "uploaded_path": uploaded, "status": str(state["status"]),
            "transcript_sha256": sha256_file(transcript_path), "audio_sha256": sha256_file(audio_path),
            "evidence_directory": str(evidence_dir)}


def normalize_markdown(markdown: str) -> str:
    empty = "## BiliNote 提取结果\n\nBiliNote 没有返回 Markdown 内容。"
    if not markdown or not markdown.strip():
        return empty
    lines = markdown.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^>\s*来源链接：", lines[0]):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    if not lines:
        return empty
    if re.match(r"^#\s+", lines[0]):
        lines[0] = "### 原始 BiliNote Markdown"
    else:
        lines.insert(0, "### 原始 BiliNote Markdown")
    return "\n".join(lines).strip()


def front_matter(text: str) -> dict:
    """只解析文件开头 --- 块内的顶层 `key: value` 标量。"""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
        if match:
            meta[match.group(1)] = match.group(2).strip("\"'")
    return {}


def existing_notes(root: Path, source_id: str, platform: str | None = None) -> list:
    """按前文元数据精确匹配 source_id（及声明的 source_platform），不做正文子串匹配。"""
    if not root.is_dir():
        return []
    found = []
    for file in sorted(root.rglob("*.md")):
        try:
            meta = front_matter(file.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if meta.get("source_id") == source_id and (not platform or not meta.get("source_platform") or meta["source_platform"] == platform):
            found.append(file)
    return found


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def publish_file(path: Path, data: bytes, history: Path) -> None:
    """写附件：内容相同则不动；不同则先把旧版本归档到 history 再替换，旧附件不丢。"""
    if path.is_file():
        if hashlib.sha256(data).hexdigest() == sha256_file(path):
            return
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, history / path.name)
    path.write_bytes(data)


def generation_records(attach: Path) -> list:
    log = attach / "generations.jsonl"
    rows = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def find_generation(attach_root: Path, fingerprint: str, source_id: str | None):
    """同一请求指纹且笔记仍在的最近一次生成记录；无变化的重复请求据此复用，不再调用云模型。"""
    if source_id:
        dirs = [attach_root / source_id]
    else:
        dirs = sorted(d for d in attach_root.iterdir() if d.is_dir()) if attach_root.is_dir() else []
    for directory in dirs:
        for row in reversed(generation_records(directory)):
            if row.get("fingerprint") == fingerprint and Path(str(row.get("note_path", ""))).is_file():
                return row, directory
    return None


def rel_link(from_file: Path, target: Path) -> str:
    return os.path.relpath(target, from_file.parent).replace("\\", "/")


def cmd_note(args, config):
    state = {"task_id": None}
    try:
        return _note(args, config, state)
    except IntakeError as exc:
        return {"success": False, "platform": args.platform, "source_id": None, "task_id": state["task_id"], "note_path": None,
                "attachments_dir": None, "backend_result_path": None, "message": str(exc), "outcome": exc.category}


def _write_progress(path, task, status, platform, url, uploaded):
    if path:
        write_json(Path(path), {"task_id": task, "status": status, "processing_platform": platform, "source_url": url,
                                "uploaded_path": uploaded})


def _note(args, config, state):
    url = args.video_url
    template = Path(args.template or DEFAULT_TEMPLATE).read_text(encoding="utf-8-sig")
    platform = args.platform.strip().lower() if args.platform and args.platform.strip() else detect_platform(url)
    if platform not in config["supported_platforms"]:
        raise IntakeError(f"不支持的平台：{platform}。当前只支持 bilibili 和 douyin。")
    provider = args.provider_id.strip() if args.provider_id and args.provider_id.strip() else str(config["default_request"]["provider_id"])
    model = resolve_model(config, args.model_name)
    base = str(config["backend_base_url"])
    media = None
    if args.verified_download_receipt:
        media = resolve_verified_media(args.verified_download_receipt, url, platform)
    request = config["default_request"]
    fingerprint = hashlib.sha256(json.dumps(
        {"url": canonical_url(url), "platform": platform, "provider": provider, "model": model,
         "media": media["sha256"] if media else None, "template": hashlib.sha256(template.encode("utf-8")).hexdigest(),
         "skill": config.get("note_skill_name", "bilinote-video-note"),
         "request": {k: request.get(k) for k in ("quality", "screenshot", "link", "format", "style", "video_understanding")}},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    if not args.resume_progress_receipt:
        reuse = find_generation(Path(expand(str(config["attachment_roots"][platform]))), fingerprint, media["source_id"] if media else None)
        if reuse:
            row, directory = reuse
            state["task_id"] = str(row.get("task_id"))
            _write_progress(args.progress_receipt, row.get("task_id"), "SUCCESS", platform, url, None)
            return {"success": True, "platform": platform, "source_id": row.get("source_id") or directory.name,
                    "task_id": str(row.get("task_id")), "note_path": row["note_path"], "attachments_dir": str(directory),
                    "backend_result_path": row.get("backend_result_path"),
                    "message": "同一请求已有核验成果，直接复用，未再次调用模型；既有笔记与附件未改动。"}
    backend = Backend(base)
    health = backend.call("/api/sys_health")
    if (health or {}).get("code") != 0:
        raise IntakeError(f"BiliNote 后端健康检查失败：{(health or {}).get('msg')}")
    payload = {"video_url": url, "platform": platform, "quality": str(request["quality"]), "screenshot": bool(request["screenshot"]),
               "link": bool(request["link"]), "model_name": model, "provider_id": provider, "format": list(request["format"]),
               "style": str(request["style"]), "video_understanding": bool(request["video_understanding"])}
    result_dir = Path(expand(str(config["backend_result_dir"])))
    extras = None
    if media:
        extras = ("原始来源为抖音公开视频。标题：" + media["title"] + "；来源：https://www.douyin.com/video/" + media["source_id"]
                  + "。以视频实际内容为依据，不把文件名当成内容。")
    resume = None
    if args.resume_progress_receipt:
        if not args.progress_receipt or os.path.abspath(args.resume_progress_receipt) == os.path.abspath(args.progress_receipt):
            raise IntakeError("恢复必须使用新的进度回执路径，保留原失败回执。")
        resume = resolve_resume(Path(args.resume_progress_receipt), url, media, result_dir,
                                Path(args.progress_receipt).parent / "resume-baseline")
        payload.update(video_url=resume["uploaded_path"], platform="local", task_id=resume["task_id"], extras=extras)
    elif media:
        if not is_loopback(base):
            raise IntakeError("已核验媒体仅发送给本机 BiliNote 服务。")
        name = "douyin-" + media["source_id"] + "-" + uuid.uuid4().hex + media["extension"]
        payload["video_url"] = backend.upload(Path(media["file_path"]), name)
        payload["platform"] = "local"
        payload["extras"] = extras
    uploaded = payload["video_url"] if media else None
    if resume and resume["status"] == "SUCCESS":
        state["task_id"] = resume["task_id"]
    else:
        _write_progress(args.progress_receipt, resume["task_id"] if resume else None, "submitting", payload["platform"], url, uploaded)
        try:
            created = backend.call("/api/generate_note", payload)
        except UnknownOutcome:
            # 请求可能已被受理：进度回执标 unknown 并保留，不当作可重派的明确失败
            _write_progress(args.progress_receipt, resume["task_id"] if resume else None, "unknown", payload["platform"], url, uploaded)
            raise
        if (created or {}).get("code") != 0 or not (created.get("data") or {}).get("task_id"):
            raise IntakeError(f"发起任务失败：{(created or {}).get('msg')}")
        state["task_id"] = str(created["data"]["task_id"])
        if resume and state["task_id"] != resume["task_id"]:
            raise IntakeError("恢复返回了其他任务编号，结果未验收。")
    task = state["task_id"]
    _write_progress(args.progress_receipt, task, "submitted", payload["platform"], url, uploaded)
    poll = float(os.environ.get("VIDEO_INTAKE_POLL_SECONDS", config["poll_interval_seconds"]))
    deadline = time.time() + float(config["timeout_seconds"])
    final = None
    seen_active = False
    while True:
        try:
            status = backend.call(f"/api/task_status/{task}")
        except IntakeError as exc:
            # 任务已提交，查询失败不等于任务失败
            raise UnknownOutcome(f"任务已提交但状态查询失败，task_id={task}：{exc}") from exc
        if (status or {}).get("code") != 0:
            raise IntakeError(f"任务执行失败：{(status or {}).get('msg')}")
        final = status
        task_state = str(status["data"]["status"])
        _write_progress(args.progress_receipt, task, task_state, payload["platform"], url, uploaded)
        if task_state != "FAILED":
            seen_active = True
        # 恢复旧 FAILED 任务时，后端状态文件在重启前仍是旧的 FAILED，须先见到非 FAILED 才算新结果
        if task_state == "FAILED" and (seen_active or not (resume and resume["status"] == "FAILED")):
            raise IntakeError(f"BiliNote 任务明确失败，保留原任务不重复提交，task_id={task}")
        if task_state == "SUCCESS" or time.time() >= deadline:
            break
        time.sleep(poll)
    if str(final["data"]["status"]) != "SUCCESS":
        raise UnknownOutcome(f"等待任务超时，task_id={task}")
    result = final["data"].get("result")
    if result is None:
        raise IntakeError("任务完成，但未返回 result。")
    if resume:
        for suffix, key in (("_transcript.json", "transcript_sha256"), ("_audio.json", "audio_sha256")):
            if sha256_file(result_dir / (resume["task_id"] + suffix)) != resume[key]:
                raise IntakeError("原转写缓存已变化，恢复结果未验收。" if "transcript" in suffix else "原音频缓存已变化，恢复结果未验收。")
    audio_meta = result.get("audio_meta") or {}
    source_id = media["source_id"] if media else str(audio_meta.get("video_id") or (audio_meta.get("raw_info") or {}).get("id") or url_hash(url))
    title = media["title"] if media else str(audio_meta.get("title") or "")
    title = re.sub(r"\s+", " ", (title or source_id).strip()).strip("“”\"'") or source_id
    safe_title = safe_file_name(title)
    note_root = Path(expand(str(config["note_roots"][platform])))
    attach_root = Path(expand(str(config["attachment_roots"][platform])))
    attach = attach_root / source_id
    note_root.mkdir(parents=True, exist_ok=True)
    attach.mkdir(parents=True, exist_ok=True)
    backend_result = result_dir / f"{task}.json"
    backend_status = result_dir / f"{task}.status.json"
    raw_md, result_json, task_json = attach / "markdown-source.md", attach / "bilinote-result.json", attach / "task-result.json"
    tr_json, tr_txt, ctx = attach / "transcript.json", attach / "transcript.txt", attach / "context.json"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + task[:8]
    history = attach / "history" / stamp

    def put(path: Path, data: bytes) -> None:
        publish_file(path, data, history)

    put(raw_md, str(result.get("markdown") or "").encode("utf-8"))
    put(result_json, json_bytes(result))
    put(task_json, json_bytes(final))
    put(tr_json, json_bytes(result.get("transcript")))
    put(tr_txt, str((result.get("transcript") or {}).get("full_text") or "").encode("utf-8"))
    canon = canonical_video_url(platform, source_id, url)
    put(ctx, json_bytes({"skill_name": config.get("note_skill_name", "bilinote-video-note"), "generated_at": now_iso()[:19], "source_url": canon,
                         "source_platform": platform, "source_id": source_id, "provider_id": provider, "model_name": model,
                         "task_id": task, "note_root": str(note_root), "attachments_dir": str(attach),
                         "backend_result_path": str(backend_result), "backend_status_path": str(backend_status),
                         "processing_platform": payload["platform"], "verified_media_sha256": media["sha256"] if media else None,
                         "verified_media_receipt": media["receipt_path"] if media else None,
                         "resumed_from_progress": args.resume_progress_receipt if resume else None,
                         "resume_evidence_directory": resume["evidence_directory"] if resume else None}))
    copies = {}
    for src, name in ((backend_result, "backend-result.json"), (backend_status, "backend-status.json")):
        if src.is_file():
            copies[name] = attach / name
            put(copies[name], src.read_bytes())
    prior_notes = existing_notes(note_root, source_id, platform)
    recorded = {}
    for row in generation_records(attach):
        recorded.setdefault(str(row.get("note_path")), set()).add(row.get("note_sha256"))
    canonical_note = note_root / f"{source_id} - {safe_title}.md"
    candidate = False
    if not canonical_note.exists():
        note_path = canonical_note
    elif canonical_note in prior_notes and sha256_file(canonical_note) in recorded.get(str(canonical_note), set()):
        # 本工具生成且未被改动的旧版本：归档后原地更新，仍保持一视频一笔记
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical_note, history / "previous-note.md")
        note_path = canonical_note
    else:
        # 人工、已修改或来源不明的笔记一律不动：新结果写入带追溯标记的候选笔记
        candidate = True
        note_path = note_root / f"{source_id} - {safe_title} (候选 {stamp}).md"
    links = [("BiliNote 原始 Markdown", raw_md), ("转写全文", tr_txt), ("转写 JSON", tr_json), ("任务结果 JSON", task_json),
             ("上下文 JSON", ctx)]
    if "backend-result.json" in copies:
        links.append(("后端导出 JSON", copies["backend-result.json"]))
    if "backend-status.json" in copies:
        links.append(("后端状态 JSON", copies["backend-status.json"]))
    link_text = "\n".join(f"- [{label}](<{rel_link(note_path, target)}>)" for label, target in links)
    defaults = config["frontmatter_defaults"]
    tags = list(defaults["tags"]) + [platform]
    replacements = {
        "{{type}}": defaults["type"], "{{status}}": defaults["status"], "{{area_link}}": defaults["area_link"],
        "{{tags_block}}": "\n".join(f"- '{v}'" for v in tags), "{{related_block}}": "\n".join(f"- '{v}'" for v in defaults["related_links"]),
        "{{skill_name}}": config.get("note_skill_name", "bilinote-video-note"), "{{source_platform}}": platform, "{{source_url}}": canon,
        "{{source_id}}": source_id, "{{provider_id}}": provider, "{{model_name}}": model, "{{task_id}}": task,
        "{{attachments_dir}}": str(attach).replace("\\", "/"), "{{generated_at}}": now_iso()[:19], "{{title}}": title,
        "{{source_url_markdown}}": f"[原始视频链接](<{canon}>)", "{{body_markdown}}": normalize_markdown(str(result.get("markdown") or "")),
        "{{attachments_links}}": link_text}
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, str(value))
    if candidate or not note_path.exists():
        with open(note_path, "x", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        note_path.write_text(rendered, encoding="utf-8")
    with open(attach / "generations.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now_iso(), "task_id": task, "source_id": source_id, "fingerprint": fingerprint,
                                 "note_path": str(note_path), "note_sha256": sha256_file(note_path), "model_name": model,
                                 "provider_id": provider, "candidate": candidate, "backend_result_path": str(backend_result)},
                                ensure_ascii=False) + "\n")
    if args.index_work_root:
        Index(Path(args.index_work_root)).append("noted", source_key=source_key(platform, source_id), source_id=source_id,
                                                 platform=platform, task_id=task, note_path=str(note_path))
    return {"success": True, "platform": platform, "source_id": source_id, "task_id": task, "note_path": str(note_path),
            "attachments_dir": str(attach), "backend_result_path": str(backend_result),
            "message": ("该来源已有人工/已修改/来源不明的笔记，原笔记未改动，新结果写入候选笔记。" if candidate
                        else "BiliNote 视频笔记已写入 Obsidian。")}


# ---------------------------------------------------------------- status / resume / index
def cmd_status(args, config):
    out = {"success": True, "action": "status"}
    if args.receipt:
        data = read_json(Path(args.receipt))
        file = Path(data.get("file_path", ""))
        out["download"] = {"status": data.get("status"), "source_key": data.get("source_key"), "file_exists": file.is_file(),
                           "sha256_match": bool(file.is_file() and sha256_file(file) == data.get("sha256"))}
        part = file.with_name(file.name + ".part")
        out["download"]["part_exists"] = part.is_file()
    if args.progress_receipt:
        progress = read_json(Path(args.progress_receipt))
        out["note"] = {"task_id": progress.get("task_id"), "recorded_status": progress.get("status")}
        if args.query_backend and progress.get("task_id"):
            try:
                live = Backend(config["backend_base_url"]).call(f"/api/task_status/{progress['task_id']}", timeout=20)
                out["note"]["backend_status"] = ((live or {}).get("data") or {}).get("status")
            except IntakeError as exc:
                out["note"]["backend_error"] = str(exc)
    if args.source_key or args.work_root:
        rows = [r for r in Index(Path(args.work_root) if args.work_root else None).entries()
                if not args.source_key or r.get("source_key") == args.source_key]
        out["index"] = {"events": len(rows), "last": rows[-1] if rows else None}
    return out


def cmd_resume(args, config):
    if args.progress_receipt:
        note_args = argparse.Namespace(video_url=args.url, platform=args.platform, provider_id=None, model_name=args.model_name,
                                       verified_download_receipt=args.receipt, progress_receipt=args.new_progress_receipt,
                                       resume_progress_receipt=args.progress_receipt, template=None, index_work_root=args.work_root)
        return cmd_note(note_args, config)
    if args.receipt:
        data = read_json(Path(args.receipt))
        if data.get("status") == "completed":
            status = cmd_status(args, config)
            file = Path(data.get("file_path", ""))
            ok = bool(file.is_file() and file.stat().st_size == data.get("bytes") and status["download"]["sha256_match"])
            status["download"]["bytes_match"] = bool(file.is_file() and file.stat().st_size == data.get("bytes"))
            if not ok:
                # 完成态回执不等于成品通过：不自动重下，不改写回执
                return {**status, "success": False, "action": "resume", "outcome": "failed",
                        "message": "回执为 completed，但媒体文件缺失或字节数/摘要与回执不一致；未重新下载，回执与现场保持不动。"}
            return {"success": True, "action": "resume", "message": "下载已完成并核验通过，仅回读，不重复下载。", **status}
        raise IntakeError("下载回执未完成；请用 download 对同一 URL 重跑（yt-dlp 沿 .part 续传）。")
    raise IntakeError("resume 需要 --progress-receipt（笔记）或 --receipt（下载）。")


def cmd_index(args, config):
    index = Index(Path(args.work_root))
    if args.index_cmd == "list":
        return {"success": True, "entries": index.entries()}
    key = args.source_key
    row = index.last_completed(key=key, sha256=args.sha256)
    return {"success": True, "found": bool(row), "entry": row,
            "file_exists": bool(row and Path(row.get("file_path", "")).is_file())}


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="video_intake.py", description=__doc__)
    p.add_argument("--config", help="替换 references/local-config.json（测试/迁移用）")
    sub = p.add_subparsers(dest="command", required=True)

    def engines(s):
        s.add_argument("--yt-dlp"); s.add_argument("--ffmpeg"); s.add_argument("--ffprobe")
        s.add_argument("--timeout", type=int, default=1800)

    d = sub.add_parser("download"); engines(d)
    d.add_argument("--url", required=True); d.add_argument("--out-dir", required=True)
    d.add_argument("--work-root"); d.add_argument("--receipt"); d.add_argument("--allow-silent", action="store_true")
    d.add_argument("--clear-unknown-lock", action="store_true", help="确认旧下载进程已结束后，清除带 unknown 标记的遗留锁")
    d.add_argument("--adopt", help="登记已下载并将核验的本地文件（需 --source-id）")
    d.add_argument("--source-id"); d.add_argument("--platform"); d.add_argument("--title")
    e = sub.add_parser("extract"); engines(e)
    e.add_argument("--receipt"); e.add_argument("--file"); e.add_argument("--out-dir", default=".")
    e.add_argument("--audio", action="store_true"); e.add_argument("--audio-format", default="mp3")
    e.add_argument("--subs", action="store_true"); e.add_argument("--url")
    n = sub.add_parser("note", help="等价于旧 save-video-note.ps1")
    n.add_argument("--video-url", required=True); n.add_argument("--platform"); n.add_argument("--provider-id")
    n.add_argument("--model-name"); n.add_argument("--verified-download-receipt"); n.add_argument("--progress-receipt")
    n.add_argument("--resume-progress-receipt"); n.add_argument("--template"); n.add_argument("--index-work-root")
    s = sub.add_parser("status")
    s.add_argument("--receipt"); s.add_argument("--progress-receipt"); s.add_argument("--source-key"); s.add_argument("--work-root")
    s.add_argument("--query-backend", action="store_true")
    r = sub.add_parser("resume")
    r.add_argument("--receipt"); r.add_argument("--progress-receipt"); r.add_argument("--new-progress-receipt")
    r.add_argument("--url"); r.add_argument("--platform"); r.add_argument("--model-name"); r.add_argument("--work-root")
    r.add_argument("--source-key"); r.add_argument("--query-backend", action="store_true")
    i = sub.add_parser("index")
    i.add_argument("index_cmd", choices=["list", "check"]); i.add_argument("--work-root", required=True)
    i.add_argument("--source-key"); i.add_argument("--sha256")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    handlers = {"download": cmd_download, "extract": cmd_extract, "note": cmd_note, "status": cmd_status, "resume": cmd_resume,
                "index": cmd_index}
    try:
        result = handlers[args.command](args, config)
    except UnknownOutcome as exc:
        emit({"success": False, "outcome": "unknown", "message": str(exc)})
        return EXIT_UNKNOWN
    except IntakeError as exc:
        emit({"success": False, "outcome": "failed", "message": str(exc)})
        return EXIT_FAIL
    emit(result)
    if args.command in ("note", "resume") and not result.get("success"):
        return EXIT_UNKNOWN if result.get("outcome") == "unknown" and os.environ.get("VIDEO_INTAKE_STRICT_EXIT") else EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
