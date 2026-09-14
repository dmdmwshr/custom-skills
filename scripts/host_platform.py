"""Read actual execution-platform facts and optional machine-maintained paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform

HOST_CONFIG = Path('/etc/codex-dev/paths.json')


def detect_platform(system: str | None = None, release: str | None = None) -> str:
    system = platform.system() if system is None else system
    release = platform.release() if release is None else release
    if system == 'Windows':
        return 'windows'
    if system == 'Linux':
        return 'wsl' if 'microsoft' in release.lower() else 'linux'
    return 'unsupported'


def host_paths(config_file: Path | None = None) -> dict[str, Path]:
    """Explicit per-process paths win; a missing host config has portable defaults."""
    config_file = HOST_CONFIG if config_file is None else config_file
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.is_file() else {}
    defaults = {'project_root': Path.home() / 'workspaces',
                'work_root': Path.home() / 'Documents/work'}
    variables = {'project_root': 'CODEX_PROJECTS_ROOT', 'work_root': 'CODEX_WORK_ROOT'}
    result = {}
    for key, fallback in defaults.items():
        path = Path(os.environ.get(variables[key]) or config.get(key) or fallback).expanduser()
        if not path.is_absolute():
            raise ValueError('host_path_must_be_absolute:' + key)
        result[key] = path.resolve()
    return result


def require_skill_platform(name: str, manifest: dict, actual: str | None = None) -> dict:
    actual = detect_platform() if actual is None else actual
    entry = manifest['skills'].get(name)
    if entry is None:
        raise ValueError('skill_missing_platform_declaration:' + name)
    if actual not in entry['execution_platforms']:
        raise ValueError('skill_platform_unsupported:' + name + ':' + actual)
    return entry
