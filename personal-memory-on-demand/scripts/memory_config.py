"""Non-secret connection and device configuration; standard library only."""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def execution_platform() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system() != "Linux":
        raise ValueError("当前执行平台未在 Windows、WSL 或 Linux 适配范围内。")
    return "wsl" if "microsoft" in platform.release().lower() else "linux"


def config_path(explicit: str | None = None) -> Path:
    if explicit or os.environ.get("MEMORY_CLIENT_CONFIG"):
        return Path(explicit or os.environ["MEMORY_CLIENT_CONFIG"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PersonalMemorySystemV1/config/client.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "personal-memory/client.json"


def windows_host() -> str:
    command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); @(Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 | Select-Object IPAddress) | ConvertTo-Json -Compress"
    executable = shutil.which("powershell.exe") or "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8-sig", timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Windows 内部地址查询不可用，请显式配置服务地址；没有切换到其他服务。") from None
    rows = json.loads(result.stdout)
    rows = [rows] if isinstance(rows, dict) else rows
    addresses = [row["IPAddress"] for row in rows if ipaddress.ip_address(row["IPAddress"]).is_private]
    if len(addresses) != 1:
        raise ValueError("不能唯一确定 Windows 内部地址，请配置记忆服务地址；不会使用 Wi-Fi 默认网关代替宿主机。")
    return addresses[0]


def resolve(explicit_url: str | None = None, explicit_config: str | None = None) -> dict:
    path = config_path(explicit_config)
    config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    host = execution_platform()
    if config.get("platform") and config["platform"] != host:
        raise ValueError("采集配置的平台与当前执行端不符，未代替另一台设备操作。")
    value = explicit_url or os.environ.get("MEMORY_API_URL") or config.get("api_url")
    if not value:
        value = f"http://{windows_host() if host == 'wsl' else '127.0.0.1'}:5175/api"
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("记忆服务地址须为不含凭据的 HTTP 或 HTTPS 地址。")
    api_path = parts.path.rstrip("/")
    if not api_path.endswith("/api"):
        api_path += "/api"
    host_file = Path("/etc/codex-dev/paths.json")
    managed = json.loads(host_file.read_text(encoding="utf-8")) if host_file.is_file() else {}
    project_default = Path.home() / "Desktop/项目开发" if host == "windows" else Path.home() / "workspaces"
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    project_root = Path(os.environ.get("MEMORY_PROJECT_ROOT") or config.get("project_root") or os.environ.get("CODEX_PROJECTS_ROOT") or managed.get("project_root") or project_default).expanduser()
    archive_root = Path(os.environ.get("MEMORY_ARCHIVE_ROOT") or config.get("archive_root") or codex_home / "archived_sessions").expanduser()
    if not project_root.is_absolute() or not archive_root.is_absolute() or archive_root.name != "archived_sessions":
        raise ValueError("项目根与归档根须为绝对路径，归档根须为 archived_sessions；不读取活跃会话。")
    try:
        local_service = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        local_service = parts.hostname.lower() == "localhost"
    return {"api_url": urlunsplit((parts.scheme, parts.netloc, api_path, "", "")),
        "platform": host, "source_id": os.environ.get("MEMORY_SOURCE_ID") or config.get("source_id") or ("windows-local" if host == "windows" and local_service else None),
        "project_root": str(project_root), "archive_root": str(archive_root),
        "instance_id": config.get("instance_id"), "use_proxy": bool(config.get("use_proxy", False)),
        "config_file": str(path)}


def select_source(value: str | None, config: dict, *, mutation: bool = False) -> str | None:
    if value == "all" and mutation:
        raise ValueError("管理操作必须指定一个来源设备。")
    selected = config["source_id"] if value == "self" or (not value and mutation) else value
    if selected == "all":
        return None
    if (mutation or value == "self") and not selected:
        raise ValueError("当前设备尚未配置持久来源编号，请先安装采集端；不会触发 Windows 盘点。")
    return selected
