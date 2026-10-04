"""Non-secret, portable identity for native Playwright tab groups."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import socket


def label(value: str, field: str) -> str:
    value = re.sub(r'[\x00-\x1f\x7f·]+', ' ', str(value)).strip()
    value = re.sub(r'\s+', ' ', value)
    if not value or len(value) > 96:
        raise ValueError(f'{field} must contain 1-96 visible characters.')
    return value


def identity(project: str, session: str | None = None, browser: str = 'chrome') -> dict:
    system = platform.system()
    if system == 'Windows':
        environment = 'Windows'
    elif system == 'Linux' and 'microsoft' in platform.release().lower():
        distro = os.environ.get('WSL_DISTRO_NAME')
        if not distro:
            values = {}
            for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
                key, _, value = line.partition('=')
                values[key] = value.strip('"')
            distro = values.get('NAME', 'Linux') + '-' + values.get('VERSION_ID', 'unknown')
        environment = 'WSL/' + label(distro, 'distribution')
    else:
        environment = label(system, 'environment')
    computer = label(socket.gethostname(), 'computer name')
    project = label(project, 'project name')
    parts = ['Playwright', computer, environment, project]
    if session:
        session = label(session, 'session name')
        if session != project:
            parts.append(session)
    return {'schema': 'BrowserGroupIdentityV1', 'computer_name': computer,
            'environment': environment, 'project_name': project, 'session_name': session,
            'browser_name': browser, 'title': ' · '.join(parts)}
