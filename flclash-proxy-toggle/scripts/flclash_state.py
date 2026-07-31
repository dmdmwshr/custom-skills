#!/usr/bin/env python3
"""Print redacted, read-only FlClash configuration-layer and runtime metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows import guard
    winreg = None  # type: ignore[assignment]


APPDATA = Path(os.environ.get("APPDATA", ""))
FLCLASH_DIR = APPDATA / "com.follow" / "clash"
CONFIG_PATH = FLCLASH_DIR / "config.yaml"
DATABASE_PATH = FLCLASH_DIR / "database.sqlite"
PREFS_PATH = FLCLASH_DIR / "shared_preferences.json"
PROFILE_DIR = FLCLASH_DIR / "profiles"
SYSTEM_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
INTERESTING_GROUPS = {"代理出口", "OpenAI", "OKX", "CN2", "Meifu", "链式代理"}
DATABASE_TABLES = (
    "profiles",
    "scripts",
    "rules",
    "profile_rule_mapping",
    "proxy_groups",
    "icon_records",
)
SENSITIVE_LABEL_PATTERN = re.compile(
    r"(?:https?://|ss://|vmess://|vless://|trojan://|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|"
    r"\b[a-z0-9.-]+\.(?:com|net|org|top|xyz|io|cc|cn)\b|"
    r"password|passwd|secret|token|uuid|private|short.?id|cookie)",
    re.IGNORECASE,
)


def utc_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def file_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_utc": utc_mtime(path),
    }


def safe_label(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if len(text) > 96 or SENSITIVE_LABEL_PATTERN.search(text):
        return "<已脱敏节点>"
    return text


def run_powershell(command: str) -> str:
    if not SYSTEM_POWERSHELL.exists():
        return ""
    command = (
        "$OutputEncoding=[Console]::OutputEncoding="
        "[System.Text.UTF8Encoding]::new($false);"
        + command
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            str(SYSTEM_POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    return completed.stdout.strip()


def process_summary() -> list[str]:
    command = (
        "Get-Process FlClash,FlClashCore,FlClashHelperService "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty ProcessName | Sort-Object -Unique | "
        "ConvertTo-Json -Compress"
    )
    output = run_powershell(command)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    values = parsed if isinstance(parsed, list) else [parsed]
    return sorted({str(value) for value in values if value})


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError):
        return ""


def parse_scalar_config(text: str) -> dict[str, str]:
    allowed = {
        "mixed-port",
        "port",
        "socks-port",
        "redir-port",
        "tproxy-port",
        "mode",
        "allow-lan",
        "external-controller",
        "log-level",
        "ipv6",
    }
    result: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith((" ", "-")) or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if key in allowed:
            result[key] = value.strip().strip('"').strip("'")
    return result


def parse_tun_block(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        if raw.strip() != "tun:":
            continue
        for child in lines[index + 1 : index + 24]:
            if child and not child.startswith(" "):
                break
            match = re.match(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$", child)
            if not match:
                continue
            key, value = match.groups()
            if key in {"enable", "device", "auto-route", "stack"}:
                result[key] = value.strip().strip('"').strip("'")
        break
    return result


def parse_proxy_groups(text: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current: str | None = None
    in_proxies = False
    proxies_indent = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        name_match = re.match(r'^\s*-\s+name:\s*["\']?(.+?)["\']?\s*$', line)
        if name_match:
            candidate = name_match.group(1).strip()
            current = candidate if candidate in INTERESTING_GROUPS else None
            in_proxies = False
            if current:
                groups.setdefault(current, [])
            continue
        if not current:
            continue
        proxies_match = re.match(r"^(\s+)proxies:\s*$", line)
        if proxies_match:
            in_proxies = True
            proxies_indent = len(proxies_match.group(1))
            continue
        if in_proxies:
            item_match = re.match(r'^\s+-\s+["\']?(.+?)["\']?\s*$', line)
            if item_match and len(line) - len(line.lstrip()) > proxies_indent:
                label = safe_label(item_match.group(1))
                if label and label not in groups[current]:
                    groups[current].append(label)
                continue
            if line and len(line) - len(line.lstrip()) <= proxies_indent:
                in_proxies = False
    return groups


def _decoded_json(value: Any) -> Any:
    current = value
    for _ in range(2):
        if not isinstance(current, str):
            break
        stripped = current.strip()
        if not stripped.startswith(("{", "[")):
            break
        try:
            current = json.loads(stripped)
        except json.JSONDecodeError:
            break
    return current


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    decoded = _decoded_json(value)
    if isinstance(decoded, dict):
        yield decoded
        for child in decoded.values():
            yield from _walk_json(child)
    elif isinstance(decoded, list):
        for child in decoded:
            yield from _walk_json(child)


def parse_preferences() -> tuple[dict[str, Any], int | None]:
    metadata = file_metadata(PREFS_PATH)
    if not metadata["exists"]:
        return metadata, None
    try:
        raw = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        metadata["read_error"] = type(exc).__name__
        return metadata, None

    active_profile_id: int | None = None
    for item in _walk_json(raw):
        if active_profile_id is None:
            for key in ("currentProfileId", "current_profile_id"):
                value = item.get(key)
                if isinstance(value, int):
                    active_profile_id = value
                    break
        network = item.get("networkProps")
        if isinstance(network, dict):
            metadata["network_system_proxy"] = network.get("systemProxy")
        vpn = item.get("vpnProps")
        if isinstance(vpn, dict):
            metadata["vpn_system_proxy"] = vpn.get("systemProxy")
        patch = item.get("patchClashConfig")
        if isinstance(patch, dict):
            metadata["patch_external_controller"] = bool(
                patch.get("external-controller")
                not in (None, "", "0", "close", False)
            )
            metadata["patch_mixed_port"] = patch.get("mixed-port")
            tun = patch.get("tun")
            if isinstance(tun, dict):
                metadata["patch_tun"] = {
                    key: tun.get(key)
                    for key in ("enable", "auto-route", "stack")
                    if key in tun
                }
    return metadata, active_profile_id


def system_proxy_enabled() -> bool | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProxyEnable")
            return bool(value)
    except OSError:
        return None


def database_summary(active_profile_id: int | None) -> dict[str, Any]:
    result = file_metadata(DATABASE_PATH)
    if not result["exists"]:
        return result
    uri = DATABASE_PATH.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only = ON")
        result["user_version"] = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        result["quick_check"] = connection.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        result["table_counts"] = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in DATABASE_TABLES
            if table in existing
        }
        if "profiles" in existing:
            result["overwrite_type_counts"] = {
                str(kind): count
                for kind, count in connection.execute(
                    "SELECT overwrite_type, COUNT(*) "
                    "FROM profiles GROUP BY overwrite_type"
                )
            }
            if active_profile_id is not None:
                row = connection.execute(
                    "SELECT selected_map FROM profiles WHERE id = ?",
                    (active_profile_id,),
                ).fetchone()
                if row and row[0]:
                    try:
                        selected = json.loads(row[0])
                    except (TypeError, json.JSONDecodeError):
                        selected = {}
                    if isinstance(selected, dict):
                        result["active_profile_selected_groups"] = {
                            group: safe_label(selected[group])
                            for group in sorted(INTERESTING_GROUPS)
                            if group in selected
                        }
    except (OSError, sqlite3.Error) as exc:
        result["read_error"] = type(exc).__name__
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    return result


def source_profiles_summary() -> dict[str, Any]:
    if not PROFILE_DIR.exists():
        return {"directory_exists": False, "yaml_count": 0}
    files = list(PROFILE_DIR.glob("*.yaml"))
    latest = max((path.stat().st_mtime for path in files), default=None)
    return {
        "directory_exists": True,
        "yaml_count": len(files),
        "latest_modified_utc": (
            datetime.fromtimestamp(latest, timezone.utc).isoformat()
            if latest is not None
            else None
        ),
    }


def controller_summary(value: str | None) -> dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        return {"configured": False, "reachable": False}
    normalized = re.sub(r"^https?://", "", raw).strip("/")
    host = ""
    port: int | None = None
    if normalized.startswith("[") and "]:" in normalized:
        host, _, port_text = normalized[1:].partition("]:")
    elif ":" in normalized:
        host, port_text = normalized.rsplit(":", 1)
    else:
        port_text = ""
    try:
        port = int(port_text)
    except ValueError:
        port = None
    is_loopback = host.lower() in {"127.0.0.1", "localhost", "::1"}
    reachable = False
    if is_loopback and port is not None and 0 < port < 65536:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.35)
            reachable = sock.connect_ex(("127.0.0.1", port)) == 0
    return {
        "configured": True,
        "bind_scope": "loopback" if is_loopback else "non_loopback_or_unknown",
        "port": port,
        "reachable": reachable,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="只读输出脱敏后的 FlClash 配置分层与运行元数据。"
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="输出单行 JSON。",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="转义非 ASCII 字符，适合旧版 PowerShell 管道读取。",
    )
    args = parser.parse_args()

    config_text = read_text(CONFIG_PATH)
    scalar = parse_scalar_config(config_text)
    preferences, active_profile_id = parse_preferences()
    result = {
        "safety": {
            "read_only": True,
            "process_control_used": False,
            "proxy_state_changed": False,
            "message": "本脚本不会暂停、恢复、退出、重启或结束 FlClash。",
        },
        "processes": process_summary(),
        "windows_system_proxy_enabled": system_proxy_enabled(),
        "configuration_layers": {
            "application_preferences": preferences,
            "profile_database": database_summary(active_profile_id),
            "source_profiles": source_profiles_summary(),
            "generated_config": file_metadata(CONFIG_PATH),
        },
        "generated_config_metadata": {
            "scalar": {
                key: value
                for key, value in scalar.items()
                if key != "external-controller"
            },
            "tun": parse_tun_block(config_text),
            "controller": controller_summary(
                scalar.get("external-controller")
            ),
            "interesting_proxy_groups": parse_proxy_groups(config_text),
        },
    }
    print(
        json.dumps(
            result,
            ensure_ascii=args.ascii,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
