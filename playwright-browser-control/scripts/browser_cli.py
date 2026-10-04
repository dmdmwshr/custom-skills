"""Thin Windows launcher for the official pinned Playwright CLI extension mode."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from group_identity import identity


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


def group_code(group_identity: dict, read_only: bool = False) -> str:
    source = Path(__file__).with_name('group_metadata.js').read_text(encoding='utf-8')
    data = json.dumps({'identity': group_identity, 'readOnly': read_only}, ensure_ascii=False)
    return 'async bootstrap => {\n' + source + '\nreturn await groupMetadata(bootstrap,' + data + ');\n}'


def group_result(output: str) -> dict:
    marker = '### Result\n'
    if marker not in output.replace('\r\n', '\n'):
        raise ValueError('Group metadata was not returned; connection may still be active. Do not reconnect blindly.')
    payload = output.replace('\r\n', '\n').split(marker, 1)[1]
    value, _ = json.JSONDecoder().raw_decode(payload.lstrip())
    if not isinstance(value, dict) or value.get('schema') != 'BrowserGroupIdentityV1':
        raise ValueError('Unexpected group metadata; inspect this named session before continuing.')
    return value


def run_cli(command: list[str], env: dict, timeout: int, secrets: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, env=env, capture_output=True, encoding='utf-8',
                            errors='replace', timeout=timeout, shell=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = redact(result.stdout + result.stderr, secrets)
    return result.returncode or (1 if '### Error' in output else 0), output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'edge'], default='chrome')
    parser.add_argument('--session', help='Task-specific name; browser prefix is added automatically')
    parser.add_argument('--project', help='Visible project name; defaults to the current working directory name')
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
        group_identity = identity(args.project or Path.cwd().name, args.session, args.browser)
        metadata_command = args.command in {'group-name', 'group-info'}
        if metadata_command and args.arguments:
            raise ValueError('group-name/group-info take no extra arguments.')
        effective_command = 'run-code' if metadata_command else args.command
        effective_arguments = [group_code(group_identity, args.command == 'group-info')] if metadata_command else args.arguments
        command, env, secrets = prepare(settings, root, args.browser, args.session,
                                        effective_command, effective_arguments, Path.cwd())
        # Official CLI hashes the nearest .playwright directory; without it unrelated
        # projects share the package-root namespace. Never overwrite an existing config.
        (Path.cwd() / '.playwright').mkdir(exist_ok=True)
        code, output = run_cli(command, env, args.timeout, secrets)
        if not metadata_command:
            print(output, end='' if output.endswith('\n') else '\n')
        if code:
            if metadata_command:
                print(output, end='' if output.endswith('\n') else '\n')
            return code
        if args.command == 'connect':
            command, env, secrets = prepare(settings, root, args.browser, args.session,
                                            'run-code', [group_code(group_identity)], Path.cwd())
            code, output = run_cli(command, env, args.timeout, secrets)
            if code:
                print(output)
                return code
        if args.command == 'connect' or metadata_command:
            receipt = group_result(output)
            receipt_path = Path.cwd() / '.playwright' / 'groups' / f'{args.browser}-{args.session}.json'
            receipt_path.parent.mkdir(exist_ok=True)
            pending = receipt_path.with_suffix('.pending')
            pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            pending.replace(receipt_path)
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0 if receipt['naming_verified'] or args.command == 'group-info' else 1
        return code
    except subprocess.TimeoutExpired:
        print('RESULT_UNKNOWN: CLI wait expired. Read back this named session before repeating an action.', file=sys.stderr)
        return 124
    except (ValueError, OSError, KeyError) as error:
        print(redact(str(error), secrets), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
