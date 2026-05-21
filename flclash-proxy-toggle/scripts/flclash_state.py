#!/usr/bin/env python3
"""Print redacted FlClash state for local routing diagnostics."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any


APPDATA = Path(os.environ.get("APPDATA", ""))
FLCLASH_DIR = APPDATA / "com.follow" / "clash"
CONFIG_PATH = FLCLASH_DIR / "config.yaml"
PREFS_PATH = FLCLASH_DIR / "shared_preferences.json"
PROFILE_DIR = FLCLASH_DIR / "profiles"
SENSITIVE_KEYS = (
    "server",
    "password",
    "uuid",
    "private",
    "public",
    "short",
    "token",
    "secret",
    "url",
    "username",
    "cookie",
    "key",
)
INTERESTING_GROUPS = {"代理出口", "OpenAI", "OKX", "CN2", "Meifu", "链式代理"}


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run_powershell(command: str) -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
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
    )
    return completed.stdout.strip()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def parse_scalar_config(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith(" ") or raw.startswith("-"):
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if key in {
            "mixed-port",
            "port",
            "socks-port",
            "redir-port",
            "tproxy-port",
            "mode",
            "allow-lan",
            "bind-address",
            "external-controller",
            "external-controller-pipe",
            "external-controller-unix",
            "log-level",
            "ipv6",
        }:
            result[key] = value.strip().strip('"').strip("'")
    return result


def parse_proxy_groups(text: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current: str | None = None
    in_proxies = False
    for raw in text.splitlines():
        line = raw.rstrip()
        name_match = re.match(r'^\s*-\s+name:\s*"?([^"]+?)"?\s*$', line)
        if name_match:
            candidate = name_match.group(1).strip()
            current = candidate if candidate in INTERESTING_GROUPS else None
            in_proxies = False
            if current:
                groups.setdefault(current, [])
            continue
        if not current:
            continue
        if re.match(r"^\s+proxies:\s*$", line):
            in_proxies = True
            continue
        if in_proxies:
            item_match = re.match(r'^\s+-\s+"?([^"]+?)"?\s*$', line)
            if item_match:
                item = item_match.group(1).strip()
                if not any(key in item.lower() for key in SENSITIVE_KEYS):
                    groups[current].append(item)
                continue
            if line and not line.startswith(" " * 6):
                in_proxies = False
    return groups


def parse_tun_block(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        if raw.strip() != "tun:":
            continue
        for child in lines[index + 1 : index + 20]:
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


def parse_preferences() -> dict[str, Any]:
    if not PREFS_PATH.exists():
        return {"exists": False}
    try:
        raw = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"exists": True, "error": type(exc).__name__}
    result: dict[str, Any] = {"exists": True}
    for value in raw.values():
        if not isinstance(value, str) or not value.strip().startswith("{"):
            continue
        try:
            nested = json.loads(value)
        except Exception:  # noqa: BLE001
            continue
        profiles = nested.get("profiles")
        if isinstance(profiles, list) and profiles:
            profile = profiles[0]
            if isinstance(profile, dict):
                result["current_group_name"] = profile.get("currentGroupName")
        network = nested.get("networkProps")
        if isinstance(network, dict):
            result["network_system_proxy"] = network.get("systemProxy")
        vpn = nested.get("vpnProps")
        if isinstance(vpn, dict):
            result["vpn_system_proxy"] = vpn.get("systemProxy")
        patch = nested.get("patchClashConfig")
        if isinstance(patch, dict):
            result["patch_external_controller"] = patch.get("external-controller")
            result["patch_mixed_port"] = patch.get("mixed-port")
            tun = patch.get("tun")
            if isinstance(tun, dict):
                result["patch_tun"] = {
                    key: tun.get(key) for key in ("enable", "device", "auto-route", "stack")
                }
    return result


def process_summary() -> list[dict[str, Any]]:
    command = (
        "Get-Process FlClash,FlClashCore,FlClashHelperService -ErrorAction SilentlyContinue | "
        "Select-Object ProcessName,Id | ConvertTo-Json -Compress"
    )
    output = run_powershell(command)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [
        {"name": str(item.get("ProcessName", "")), "pid": int(item.get("Id", 0))}
        for item in parsed
        if isinstance(item, dict)
    ]


def latest_profile_path() -> str | None:
    if not PROFILE_DIR.exists():
        return None
    files = sorted(PROFILE_DIR.glob("*.yaml"), key=lambda path: path.stat().st_mtime, reverse=True)
    return str(files[0]) if files else None


def main() -> int:
    config_text = read_text(CONFIG_PATH)
    scalar = parse_scalar_config(config_text)
    result = {
        "flclash_dir_exists": FLCLASH_DIR.exists(),
        "config_path": str(CONFIG_PATH) if CONFIG_PATH.exists() else None,
        "latest_profile_path": latest_profile_path(),
        "processes": process_summary(),
        "config": scalar,
        "tun": parse_tun_block(config_text),
        "preferences": parse_preferences(),
        "proxy_groups": parse_proxy_groups(config_text),
        "controller_ports": {str(port): port_open(port) for port in (9090, 9097, 9098, 19090, 19091)},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
