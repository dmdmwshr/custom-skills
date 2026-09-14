#!/usr/bin/env python3
"""Read-only platform preflight; does not impersonate a target operating system."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode = True
from host_platform import detect_platform, host_paths, require_skill_platform

SOURCE = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skill', action='append', default=[])
    args = parser.parse_args()
    manifest = json.loads((SOURCE / 'platforms.json').read_text(encoding='utf-8'))
    actual = detect_platform()
    for name in args.skill:
        require_skill_platform(name, manifest, actual)
    identity = {'user': getpass.getuser()}
    if hasattr(os, 'getuid'):
        import pwd
        identity = {'user': pwd.getpwuid(os.getuid()).pw_name, 'uid': os.getuid(), 'gid': os.getgid()}
    print(json.dumps({'execution_platform': actual, 'system': platform.system(),
                      **identity,
                      'home': str(Path.home()),
                      'codex_home': str(Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').resolve()),
                      'paths': {k: str(v) for k,v in host_paths().items()},
                      'skills_checked': args.skill,
                      'business_dependencies_verified': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
