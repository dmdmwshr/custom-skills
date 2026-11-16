"""Small stdlib-only path helpers shared by the entry scripts (WSL exporter and Windows launcher)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_WSL_UNC = re.compile(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/][^\\/]+(?:[\\/](.*))?$", re.I)


def host_path(template, year=0, month=0):
    """Fill {year}/{yy}/{month} in a configured path; map a Windows drive path (D:\\...) when running under WSL/Linux."""
    text = str(template).format(year=year, yy=str(year)[-2:], month=month)
    match = _DRIVE.match(text)
    if match and os.name != "nt":
        text = "/mnt/" + match.group(1).lower() + "/" + match.group(2).replace("\\", "/")
    return Path(text)


def wsl_path(path):
    """A Windows path as seen from WSL (C:\\x -> /mnt/c/x, \\\\wsl.localhost\\distro\\x -> /x); other paths are unchanged."""
    text = str(path)
    unc = _WSL_UNC.match(text)
    if unc:  # \\wsl.localhost\<distro>\srv\x -> /srv/x
        return "/" + (unc.group(1) or "").replace("\\", "/")
    match = _DRIVE.match(text)
    return "/mnt/" + match.group(1).lower() + "/" + match.group(2).replace("\\", "/") if match else text


def win_path(text, distro=None):
    """A POSIX path as Windows sees it: /mnt/d/x -> D:\\x; other absolute paths -> \\\\wsl.localhost\\<distro>\\...; non-paths unchanged."""
    text = str(text)
    if not text.startswith("/"):
        return text
    match = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", text)
    if match:
        return match.group(1).upper() + ":\\" + (match.group(2) or "").replace("/", "\\")
    name = distro or os.environ.get("WSL_DISTRO_NAME") or "Ubuntu-24.04"
    return "\\\\wsl.localhost\\" + name + text.replace("/", "\\")
