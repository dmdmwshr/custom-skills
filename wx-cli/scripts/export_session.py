from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


WX_EXE = Path(r"D:\Program_Files\wx-cli\wx.exe")
OUTPUT_ROOT = Path(r"D:\Program_Files\wx-cli\output")
XWECHAT_ROOT = Path(r"D:\Users\12070\Documents\xwechat_files")
INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
MULTI_SPACE_RE = re.compile(r"\s+")
FILE_MESSAGE_RE = re.compile(r"^\[文件\]\s+(?P<name>.+?)\s+\((?P<size>[^,]+),\s*(?P<ext>[^)]+)\)$")
INIT_DATA_DIR_RE = re.compile(r"数据目录:\s*(?P<path>.+?)(?:\\db_storage)?\s*$")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class FileMessage:
    local_id: int
    sender: str
    time: str
    timestamp: int
    content: str
    file_name: str
    size_label: str
    file_ext: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出单个微信会话，并把文件归档到同一目录。")
    parser.add_argument("--chat", required=True, help="会话名称")
    parser.add_argument("--since", help="起始时间 YYYY-MM-DD")
    parser.add_argument("--until", help="结束时间 YYYY-MM-DD")
    parser.add_argument(
        "--include-files",
        nargs="?",
        const="true",
        default="true",
        help="是否归档直接文件消息，默认开启；可传 false 关闭",
    )
    return parser.parse_args()


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"无法识别布尔值: {value}")


def sanitize_segment(value: str) -> str:
    cleaned = INVALID_CHARS_RE.sub("_", value)
    cleaned = MULTI_SPACE_RE.sub(" ", cleaned).strip().rstrip(".")
    return cleaned or "_"


def run_wx(*args: str) -> str:
    if not WX_EXE.exists():
        raise FileNotFoundError(f"未找到 wx-cli: {WX_EXE}")
    completed = subprocess.run(
        [str(WX_EXE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"退出码 {completed.returncode}"
        raise RuntimeError(f"wx {' '.join(args)} 执行失败: {detail}")
    return completed.stdout


def load_json_output(*args: str) -> dict[str, Any]:
    raw = run_wx(*args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"wx {' '.join(args)} 返回的 JSON 无法解析") from exc


def get_current_data_dir() -> tuple[Path, list[str]]:
    if not XWECHAT_ROOT.exists():
        raise FileNotFoundError(f"未找到微信数据根目录: {XWECHAT_ROOT}")

    candidates = sorted(
        [
            child
            for child in XWECHAT_ROOT.iterdir()
            if child.is_dir() and child.name.startswith("wxid_") and (child / "db_storage").exists()
        ],
        key=lambda path: path.name,
    )
    if not candidates:
        raise FileNotFoundError(f"未在 {XWECHAT_ROOT} 下发现带 db_storage 的 wxid 目录")

    init_output = run_wx("init")
    for line in init_output.splitlines():
        match = INIT_DATA_DIR_RE.search(line.strip())
        if not match:
            continue
        db_storage = Path(match.group("path"))
        data_dir = db_storage.parent if db_storage.name == "db_storage" else db_storage
        if data_dir.exists():
            return data_dir, [str(path) for path in candidates]

    return candidates[0], [str(path) for path in candidates]


def add_date_range(args: list[str], since: str | None, until: str | None) -> list[str]:
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    return args


def parse_file_messages(history: dict[str, Any]) -> list[FileMessage]:
    items: list[FileMessage] = []
    for message in history.get("messages", []):
        content = message.get("content", "")
        if not isinstance(content, str) or not content.startswith("[文件] "):
            continue
        match = FILE_MESSAGE_RE.match(content.strip())
        if not match:
            continue
        items.append(
            FileMessage(
                local_id=int(message["local_id"]),
                sender=str(message.get("sender") or "未知发送者"),
                time=str(message.get("time") or ""),
                timestamp=int(message["timestamp"]),
                content=content,
                file_name=match.group("name"),
                size_label=match.group("size"),
                file_ext=match.group("ext"),
            )
        )
    return items


def dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def select_file_candidates(data_dir: Path, file_name: str, message_ts: int) -> tuple[list[Path], Path | None]:
    year_month = datetime.fromtimestamp(message_ts).strftime("%Y-%m")
    file_root = data_dir / "msg" / "file" / year_month / file_name
    attach_root = data_dir / "msg" / "attach"
    msg_root = data_dir / "msg"

    candidates: list[Path] = []
    if file_root.exists():
        candidates.append(file_root)

    if attach_root.exists():
        for path in attach_root.rglob(file_name):
            parts = path.parts
            if year_month in parts and "Rec" in parts and "F" in parts:
                candidates.append(path)

    if msg_root.exists():
        for path in msg_root.rglob(file_name):
            candidates.append(path)

    candidates = dedupe_paths(candidates)
    if not candidates:
        return [], None

    exact_name = file_name.casefold()
    candidates.sort(
        key=lambda path: (
            0 if path.name.casefold() == exact_name else 1,
            abs(path.stat().st_mtime - message_ts),
            str(path),
        )
    )
    return candidates, candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_output_name(message: FileMessage) -> str:
    dt = datetime.fromtimestamp(message.timestamp)
    prefix = dt.strftime("%Y-%m-%d_%H%M%S")
    sender = sanitize_segment(message.sender)
    file_name = sanitize_segment(message.file_name)
    return f"{prefix}__{sender}__{file_name}"


def copy_with_version(source: Path, files_dir: Path, target_name: str) -> dict[str, Any]:
    files_dir.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    destination = files_dir / target_name
    stem = destination.stem
    suffix = destination.suffix

    if not destination.exists():
        shutil.copy2(source, destination)
        return {
            "status": "copied",
            "output_path": str(destination),
            "output_name": destination.name,
            "sha256": source_hash,
        }

    if sha256_file(destination) == source_hash:
        return {
            "status": "skipped_same_content",
            "output_path": str(destination),
            "output_name": destination.name,
            "sha256": source_hash,
        }

    version = 2
    while True:
        candidate = files_dir / f"{stem}__v{version}{suffix}"
        if not candidate.exists():
            shutil.copy2(source, candidate)
            return {
                "status": "copied_versioned",
                "output_path": str(candidate),
                "output_name": candidate.name,
                "sha256": source_hash,
            }
        if sha256_file(candidate) == source_hash:
            return {
                "status": "skipped_same_content",
                "output_path": str(candidate),
                "output_name": candidate.name,
                "sha256": source_hash,
            }
        version += 1


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_chat_markdown(chat: str, total: int, session_dir: Path, since: str | None, until: str | None) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    output_path = session_dir / "chat.md"
    if total <= 0:
        output_path.write_text(f"# {chat}\n\n> 当前筛选范围内没有消息\n", encoding="utf-8")
        return output_path

    args = ["export", chat, "-n", str(total), "--format", "markdown", "-o", str(output_path)]
    add_date_range(args, since, until)
    run_wx(*args)
    return output_path


def main() -> int:
    args = parse_args()
    include_files = parse_bool(args.include_files)

    data_dir, data_dir_candidates = get_current_data_dir()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    stats_args = ["stats", args.chat, "--json"]
    add_date_range(stats_args, args.since, args.until)
    stats = load_json_output(*stats_args)

    total = int(stats.get("total", 0))
    session_name = str(stats.get("chat") or args.chat)
    session_username = str(stats.get("username") or "unknown")
    session_dir = OUTPUT_ROOT / f"{sanitize_segment(session_name)}__{sanitize_segment(session_username)}"
    files_dir = session_dir / "files"

    chat_md_path = export_chat_markdown(session_name, total, session_dir, args.since, args.until)

    history_args = ["history", session_name, "--type", "file", "-n", str(max(total, 1)), "--json"]
    add_date_range(history_args, args.since, args.until)
    history = load_json_output(*history_args)
    file_messages = parse_file_messages(history)

    file_results: list[dict[str, Any]] = []
    for message in file_messages:
        record: dict[str, Any] = {
            "local_id": message.local_id,
            "sender": message.sender,
            "time": message.time,
            "timestamp": message.timestamp,
            "message_content": message.content,
            "original_file_name": message.file_name,
            "size_label": message.size_label,
            "file_ext": message.file_ext,
        }

        if not include_files:
            record["status"] = "files_disabled"
            file_results.append(record)
            continue

        candidates, selected = select_file_candidates(data_dir, message.file_name, message.timestamp)
        record["candidate_paths"] = [str(path) for path in candidates]

        if selected is None:
            record["status"] = "missing"
            file_results.append(record)
            continue

        record["selected_source_path"] = str(selected)
        record["selected_source_mtime"] = datetime.fromtimestamp(selected.stat().st_mtime).isoformat()
        copy_result = copy_with_version(selected, files_dir, build_output_name(message))
        record.update(copy_result)
        file_results.append(record)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "wx_exe": str(WX_EXE),
        "output_root": str(OUTPUT_ROOT),
        "selected_data_dir": str(data_dir),
        "data_dir_candidates": data_dir_candidates,
        "session": {
            "chat": session_name,
            "username": session_username,
            "chat_type": stats.get("chat_type"),
            "is_group": stats.get("is_group"),
            "total_messages": total,
            "since": args.since,
            "until": args.until,
        },
        "artifacts": {
            "session_dir": str(session_dir),
            "chat_md": str(chat_md_path),
            "files_dir": str(files_dir),
        },
        "include_files": include_files,
        "stats_meta": stats.get("meta", {}),
        "history_meta": history.get("meta", {}),
        "file_messages": file_results,
    }

    manifest_path = session_dir / "manifest.json"
    write_manifest(manifest_path, manifest)

    copied = sum(1 for item in file_results if item.get("status") in {"copied", "copied_versioned"})
    skipped = sum(1 for item in file_results if item.get("status") == "skipped_same_content")
    missing = sum(1 for item in file_results if item.get("status") == "missing")

    print(f"会话目录: {session_dir}")
    print(f"聊天文档: {chat_md_path}")
    print(f"清单文件: {manifest_path}")
    print(f"文件消息: {len(file_results)} 条，新增复制 {copied}，跳过同内容 {skipped}，缺失 {missing}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)
