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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

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
ROUTE_GROUPS = ("代理出口", "Meifu", "CN2", "OpenAI", "OKX", "链式代理")
DATABASE_TABLES = (
    "profiles",
    "scripts",
    "rules",
    "profile_rule_mapping",
    "proxy_groups",
    "icon_records",
)
MAX_CONTROLLER_RESPONSE_BYTES = 2 * 1024 * 1024
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


def parse_port(value: str | None) -> int | None:
    try:
        port = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def system_proxy_diagnostic(mixed_port: int | None) -> dict[str, Any]:
    """Return a redacted WinINET proxy summary without changing it."""
    result: dict[str, Any] = {
        "enabled": None,
        "manual_proxy_endpoint_scope": "unavailable",
        "matches_generated_mixed_port": None,
        "auto_config_present": None,
    }
    if winreg is None:
        return result
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            try:
                proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except OSError:
                proxy_server = ""
            try:
                auto_config, _ = winreg.QueryValueEx(key, "AutoConfigURL")
            except OSError:
                auto_config = ""
    except OSError:
        return result

    proxy_text = str(proxy_server or "").strip()
    lower = proxy_text.casefold()
    uses_loopback = any(
        marker in lower for marker in ("127.0.0.1", "localhost", "[::1]", "::1")
    )
    result["enabled"] = bool(enabled)
    result["auto_config_present"] = bool(str(auto_config or "").strip())
    if not proxy_text:
        result["manual_proxy_endpoint_scope"] = "not_configured"
    elif uses_loopback:
        result["manual_proxy_endpoint_scope"] = "loopback"
    else:
        result["manual_proxy_endpoint_scope"] = "non_loopback_or_unknown"
    if mixed_port is not None and uses_loopback:
        result["matches_generated_mixed_port"] = bool(
            re.search(rf"(?<!\d){mixed_port}(?!\d)", proxy_text)
        )
    return result


def _json_list(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    values = parsed if isinstance(parsed, list) else [parsed]
    return [item for item in values if isinstance(item, dict)]


def local_proxy_listener_summary(port: int | None) -> dict[str, Any]:
    """Check only the local mixed-port listener and redact owner details."""
    result: dict[str, Any] = {"state": "port_not_configured", "reachable": False}
    if port is None:
        return result

    reachable = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.35)
            reachable = sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        reachable = False

    command = (
        f"$items = @(Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue); "
        "$result = foreach ($item in $items) { "
        "$process = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ ProcessName = if ($process) { $process.ProcessName } "
        "else { 'unknown' } } }; "
        "$result | Select-Object -Unique ProcessName | ConvertTo-Json -Compress"
    )
    owners = {
        str(item.get("ProcessName", ""))
        for item in _json_list(run_powershell(command))
        if item.get("ProcessName")
    }
    if "FlClashCore" in owners and reachable:
        state = "flclash_core_listening"
    elif "FlClashCore" in owners:
        state = "flclash_core_listener_unreachable"
    elif reachable and owners:
        state = "other_process_listening"
    elif reachable:
        state = "listener_reachable_owner_unavailable"
    else:
        state = "not_listening"
    return {"state": state, "reachable": reachable}


def is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().casefold() in {"127.0.0.1", "localhost", "::1"}


def controller_base_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    candidate = raw if re.match(r"^https?://", raw, re.IGNORECASE) else f"http://{raw}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or port is None:
        return None
    if not is_loopback_host(parsed.hostname):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def controller_secret(config_text: str) -> str:
    for line in config_text.splitlines():
        if not re.match(r"^secret\s*:", line):
            continue
        value = line.split(":", 1)[1].strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            return value[1:-1]
        return value.split("#", 1)[0].strip()
    return ""


def controller_json(
    base_url: str, path: str, secret: str
) -> tuple[dict[str, Any] | None, bool]:
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(f"{base_url}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=0.75) as response:
            payload = response.read(MAX_CONTROLLER_RESPONSE_BYTES + 1)
    except OSError:
        return None, False
    if len(payload) > MAX_CONTROLLER_RESPONSE_BYTES:
        return None, False
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, False
    return (parsed if isinstance(parsed, dict) else None), isinstance(parsed, dict)


def route_category(values: Iterable[Any]) -> str:
    labels = [str(value).strip() for value in values if str(value).strip()]
    if any(label.startswith("链式") for label in labels):
        return "chain"
    if any(label.startswith("CN2") for label in labels):
        return "cn2"
    if any(label.startswith("Meifu") for label in labels):
        return "meifu"
    if any(label.upper() == "DIRECT" for label in labels):
        return "direct"
    return "other_or_unknown"


def controller_connectivity_snapshot(
    controller_value: str | None, config_text: str
) -> dict[str, Any]:
    base_url = controller_base_url(controller_value)
    if not (controller_value or "").strip():
        return {"state": "not_configured"}
    if base_url is None:
        return {"state": "not_loopback_or_invalid"}

    secret = controller_secret(config_text)
    proxies, proxies_ok = controller_json(base_url, "/proxies", secret)
    if not proxies_ok or proxies is None:
        return {"state": "unavailable"}

    selected_routes: dict[str, str] = {}
    proxy_map = proxies.get("proxies")
    if isinstance(proxy_map, dict):
        for group in ROUTE_GROUPS:
            proxy = proxy_map.get(group)
            if isinstance(proxy, dict):
                selected_routes[group] = route_category([proxy.get("now")])

    result: dict[str, Any] = {
        "state": "available",
        "selected_route_by_group": selected_routes,
        "active_connection_snapshot": "unavailable",
    }
    connections, connections_ok = controller_json(base_url, "/connections", secret)
    if not connections_ok or connections is None:
        return result
    items = connections.get("connections")
    if not isinstance(items, list):
        return result
    counts = {"meifu": 0, "cn2": 0, "chain": 0, "direct": 0, "other_or_unknown": 0}
    for item in items:
        chains = item.get("chains") if isinstance(item, dict) else []
        category = route_category(chains if isinstance(chains, list) else [])
        counts[category] += 1
    result["active_connection_snapshot"] = "available"
    result["active_connection_total"] = len(items)
    result["active_connection_routes"] = counts
    return result


def connectivity_diagnosis(config_text: str, scalar: dict[str, str]) -> dict[str, Any]:
    """Classify the local proxy path before anyone changes a node or server."""
    mixed_port = parse_port(scalar.get("mixed-port"))
    system_proxy = system_proxy_diagnostic(mixed_port)
    listener = local_proxy_listener_summary(mixed_port)
    controller = controller_connectivity_snapshot(
        scalar.get("external-controller"), config_text
    )
    interpretation: list[str] = []
    if (
        system_proxy.get("enabled")
        and system_proxy.get("manual_proxy_endpoint_scope") == "loopback"
    ):
        if system_proxy.get("matches_generated_mixed_port") is False:
            interpretation.append("system_proxy_port_mismatch")
        elif listener.get("state") == "not_listening":
            interpretation.append("local_proxy_not_listening")
        elif listener.get("state") == "other_process_listening":
            interpretation.append("local_port_owned_by_other_process")
        elif listener.get("state") == "flclash_core_listener_unreachable":
            interpretation.append("local_proxy_listener_unreachable")
        elif listener.get("state") == "flclash_core_listening":
            interpretation.append("local_proxy_listener_ready")

    selected = controller.get("selected_route_by_group")
    counts = controller.get("active_connection_routes")
    if isinstance(selected, dict) and isinstance(counts, dict):
        if selected.get("代理出口") != "cn2" and counts.get("cn2") == 0:
            interpretation.append("cn2_idle_by_current_selection")

    return {
        "system_proxy": system_proxy,
        "local_mixed_port_listener": listener,
        "controller_runtime": controller,
        "interpretation": interpretation,
    }


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
    parser.add_argument(
        "--connectivity",
        action="store_true",
        help="只读检查本机代理端口、运行时分流与连接归属，不发起外网探测。",
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
    if args.connectivity:
        result["connection_diagnostics"] = connectivity_diagnosis(
            config_text, scalar
        )
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
