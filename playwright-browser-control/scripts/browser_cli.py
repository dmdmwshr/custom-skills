"""Thin Windows launcher for the official pinned Playwright CLI extension mode."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def runtime_root() -> Path:
    return Path(os.environ['LOCALAPPDATA']) / 'CodexBrowser' / 'playwright'


def read_settings(root: Path) -> dict:
    return json.loads((root / 'runtime.json').read_text(encoding='utf-8-sig'))


def redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if value:
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'([?&]token=)[A-Za-z0-9_-]+', r'\1[REDACTED]', text)


def prepare(settings: dict, root: Path, browser: str, session: str,
            command: str, arguments: list[str], cwd: Path) -> tuple[list[str], dict, list[str]]:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', session):
        raise ValueError('Session must be 1-48 lowercase letters, digits or hyphens.')
    if command in {'open', 'close', 'close-all', 'kill-all', 'delete-data', 'attach', 'detach', 'install', 'install-browser'}:
        raise ValueError('Use connect/disconnect for this extension workflow; global close/install commands are not supported.')
    if any(a.startswith(('-s=', '--session', '--extension', '--cdp', '--endpoint', '--config')) for a in arguments):
        raise ValueError('Browser/session/connection configuration is owned by this launcher.')
    selected = settings['browsers'][browser]
    secrets = [(root / p['token_file']).read_text(encoding='utf-8').strip()
               for p in settings['browsers'].values()]
    token = (root / selected['token_file']).read_text(encoding='utf-8').strip()
    if not token:
        raise ValueError('Selected browser token is empty.')
    # Avoid inheriting another task/browser connection or caller-enabled secret logging.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PLAYWRIGHT_MCP_', 'PLAYWRIGHT_CLI_', 'PWTEST_'))
           and k not in {'DEBUG', 'DEBUG_FILE', 'NODE_OPTIONS'}}
    env.update({
        'PLAYWRIGHT_MCP_EXTENSION_TOKEN': token,
        'PLAYWRIGHT_MCP_PROFILE_DIR_NAME': selected['profile_directory'],
        'PLAYWRIGHT_MCP_BROWSER': selected['channel'],
        'PLAYWRIGHT_MCP_OUTPUT_DIR': str(cwd / 'output' / 'playwright' / f'{browser}-{session}'),
        'PLAYWRIGHT_MCP_CONSOLE_LEVEL': 'error',
    })
    args = [settings['node_executable'], settings['cli_entry'], f'-s={browser}-{session}']
    if command == 'connect':
        if arguments:
            raise ValueError('connect takes no extra arguments; create a task-owned tab after connecting.')
        args += ['attach', f"--extension={selected['channel']}"]
    elif command == 'disconnect':
        if arguments:
            raise ValueError('disconnect takes no extra arguments.')
        args += ['detach']
    else:
        args += [command, *arguments]
    return args, env, secrets


def doctor(settings: dict, root: Path) -> dict:
    entry = Path(settings['cli_entry'])
    package = entry.parent / 'package.json'
    actual = json.loads(package.read_text(encoding='utf-8'))['version'] if package.exists() else None
    return {
        'node_exists': Path(settings['node_executable']).is_file(),
        'cli_exists': entry.is_file(),
        'expected_version': settings['expected_cli_version'],
        'installed_version': actual,
        'version_matches': actual == settings['expected_cli_version'],
        'browsers': {name: {'channel': p['channel'], 'profile_directory': p['profile_directory'],
                            'credential_present': (root / p['token_file']).is_file()}
                     for name, p in settings['browsers'].items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'edge'], default='chrome')
    parser.add_argument('--session', help='Task-specific name; browser prefix is added automatically')
    parser.add_argument('--timeout', type=int, default=90, help='CLI wait limit in seconds; timeout means result unknown')
    parser.add_argument('command')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    secrets: list[str] = []
    try:
        root = runtime_root()
        settings = read_settings(root)
        status = doctor(settings, root)
        if args.command == 'doctor':
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if status['version_matches'] and status['node_exists'] else 1
        if not status['version_matches']:
            raise ValueError('CLI version differs from the validated pin; inspect the upgrade before using it.')
        if not args.session:
            raise ValueError('--session is required for browser commands.')
        if args.timeout < 1:
            raise ValueError('--timeout must be positive.')
        command, env, secrets = prepare(settings, root, args.browser, args.session,
                                        args.command, args.arguments, Path.cwd())
        # Official CLI hashes the nearest .playwright directory; without it unrelated
        # projects share the package-root namespace. Never overwrite an existing config.
        (Path.cwd() / '.playwright').mkdir(exist_ok=True)
        result = subprocess.run(command, env=env, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=args.timeout, shell=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = redact(result.stdout + result.stderr, secrets)
        print(output, end='' if output.endswith('\n') else '\n')
        # Upstream may describe an action failure while returning exit status zero.
        return result.returncode or (1 if '### Error' in output else 0)
    except subprocess.TimeoutExpired:
        print('RESULT_UNKNOWN: CLI wait expired. Read back this named session before repeating an action.', file=sys.stderr)
        return 124
    except (ValueError, OSError, KeyError) as error:
        print(redact(str(error), secrets), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
