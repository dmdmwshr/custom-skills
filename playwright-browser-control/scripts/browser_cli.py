"""Thin Windows launcher for the official pinned Playwright CLI extension mode."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from group_identity import identity
import execution_state as lifecycle
import browser_audit
from saved_groups import snapshot as saved_snapshot


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
            command: str, arguments: list[str], cwd: Path, physical_session: str | None = None) -> tuple[list[str], dict, list[str]]:
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
    args = [settings['node_executable'], settings['cli_entry'], '-s=' + (physical_session or f'{browser}-{session}')]
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


def parse_result(output, schema):
    normalized = output.replace('\r\n', '\n')
    if '### Result\n' not in normalized:
        raise ValueError('No cleanup receipt returned; inspect the same execution before repeating.')
    value, _ = json.JSONDecoder().raw_decode(normalized.split('### Result\n', 1)[1].lstrip())
    if not isinstance(value, dict) or value.get('schema') != schema:
        raise ValueError('Unexpected cleanup receipt.')
    return value


def profile_path(settings, browser):
    selected = settings['browsers'][browser]
    if selected.get('profile_path'):
        return Path(selected['profile_path'])
    base = ('Google/Chrome/User Data' if browser == 'chrome' else 'Microsoft/Edge/User Data')
    return Path(os.environ['LOCALAPPDATA']) / base / selected['profile_directory']


def saved_groups(profile):
    store = profile / 'Sync Data' / 'LevelDB'
    if not (store / 'CURRENT').is_file():
        raise ValueError('Saved-group store unavailable; cleanup cannot be declared verified.')
    return saved_snapshot(store)


def cleanup_code(function, value):
    source = Path(__file__).with_name('group_cleanup.js').read_text(encoding='utf-8')
    return 'async bootstrap => {\n' + source + '\nreturn await ' + function + '(bootstrap,' + json.dumps(value, ensure_ascii=False) + ');\n}'


def audit_code():
    source = Path(__file__).with_name('native_browser_audit.js').read_text(encoding='utf-8')
    return 'async bootstrap => {\n' + source + '\nreturn await captureNativeBrowserInventory(bootstrap);\n}'


def release_status(code, output, session):
    if f"Browser '{session}' is not attached." in output:
        return 'already_not_attached'
    return 'detached' if code == 0 else 'unverified'


def managed_main(args, root, settings):
    cwd = Path.cwd()
    owner = lifecycle.conversation(args.conversation)
    path = lifecycle.receipt_path(cwd, args.browser, args.session)
    profile = profile_path(settings, args.browser)
    if args.arguments and args.command in {'connect','disconnect','finish','status','audit','group-info','group-name'}:
        raise ValueError('This management command takes no extra arguments.')
    with lifecycle.locked(path):
        receipt = lifecycle.read(path)
        if args.command == 'status':
            print(json.dumps(receipt or {'state':'no_execution'}, ensure_ascii=False, indent=2))
            return 0
        prior = None
        if receipt:
            lifecycle.check_owner(receipt, cwd, profile, owner)
        if args.command == 'connect':
            if receipt and receipt['state'] not in {'completed','cleanup_pending'}:
                raise ValueError('This execution is still active or unknown; finish or reconcile it before creating another.')
            baseline = saved_groups(profile)
            if receipt and receipt['state'] == 'cleanup_pending':
                if not receipt.get('cleanup', {}).get('scheduled') or any(g in baseline for g in receipt['saved_group_ids']):
                    raise ValueError('Previous cleanup is incomplete; do not recreate its business operation.')
                if receipt['cleanup'].get('connection_release') not in {'detached','already_not_attached'}:
                    raise ValueError('Previous connection release is unverified; inspect its exact physical session.')
                prior = receipt
            elif receipt:
                lifecycle.archive(path, receipt)
            group_identity = identity(args.project or cwd.name, args.session, args.browser)
            receipt = lifecycle.fresh(group_identity, cwd, profile, owner)
            receipt['title'] += ' · ' + receipt['execution_id'][:8]
            receipt['saved_baseline_ids'] = list(baseline)
            if prior:
                receipt['previous_cleanup'] = prior
            lifecycle.save(path, receipt)  # Intent precedes browser input.
        elif not receipt:
            raise ValueError('Connect first; history is not an active browser session.')
        elif receipt['state'] == 'completed':
            if args.command in {'finish','disconnect'}:
                print(json.dumps({**receipt, 'reused_completed_receipt':True}, ensure_ascii=False, indent=2)); return 0
            raise ValueError('This execution is completed; connect creates fresh objects for the next execution.')
        elif receipt['state'] in {'connecting','unknown','cleanup_pending'} and args.command not in {'group-info','audit'}:
            raise ValueError('Execution requires readback; no business commands or blind reconnect are allowed.')

        def invoke(command, arguments=None):
            argv, env, secrets = prepare(settings, root, args.browser, args.session, command,
                                         arguments or [], cwd, receipt['cli_session'])
            try:
                code, output = run_cli(argv, env, args.timeout, secrets)
            except subprocess.TimeoutExpired:
                receipt['state'] = 'unknown'; persist()
                raise
            return code, output

        def metadata():
            safe_identity = {k:receipt[k] for k in ('schema','computer_name','environment','project_name','session_name','browser_name','title')}
            code, output = invoke('run-code', [group_code(safe_identity, args.command in {'group-info','audit'})])
            if code:
                raise ValueError('Native group readback failed; keep this execution for inspection.')
            return group_result(output)

        def persist():
            lifecycle.save(path, receipt)
            browser_audit.register(root, receipt, path)

        def inspect_browser():
            code, output = invoke('run-code', [audit_code()])
            if code: raise ValueError('Native end-of-execution inventory failed; cleanup duty remains unverified.')
            native = parse_result(output, 'NativeBrowserInventoryV1')
            epoch = browser_audit.browser_epoch(args.browser, profile)
            if receipt.get('browser_epoch') and epoch and receipt['browser_epoch'] != epoch:
                raise ValueError('Browser process identity changed; preserve the original execution for reconciliation.')
            if epoch: receipt['browser_epoch'] = epoch
            persist()
            report = browser_audit.classify(native, saved_groups(profile), receipt,
                browser_audit.registry(root, receipt['profile_key']), epoch)
            report['reconciled_own_history'] = browser_audit.complete_verified_own_history(root,receipt,report,epoch)
            receipt['end_audit'] = report
            persist()
            return report

        try:
            if args.command == 'connect':
                code, output = invoke('connect')
                if code:
                    receipt['state'] = 'unknown'; persist()
                    print(output); return code
                actual = metadata()
                receipt.update(actual)
                receipt.update(owned_tab_ids=actual['native_tab_ids'], state='active')
                for _ in range(10):
                    current = saved_groups(profile)
                    new = [gid for gid,g in current.items() if gid not in receipt['saved_baseline_ids'] and g['title'] == receipt['title']]
                    if new: break
                    time.sleep(.2)
                receipt['saved_group_ids'] = new
                receipt['saved_group_status'] = 'bound' if new else 'not_saved_at_readback'
                persist()
                if receipt.get('previous_cleanup'):
                    previous = receipt['previous_cleanup']
                    code, output = invoke('run-code', [cleanup_code('verifyNativeCleanup', previous['cleanup']['tab_ids'])])
                    checked = parse_result(output, 'BrowserCleanupReadbackV1') if not code else {'verified':False}
                    if not checked['verified']:
                        raise ValueError('Previous native pages remain; its cleanup stays pending and those pages are not reused.')
                    previous['state'] = 'completed'; previous['completed_at'] = lifecycle.now()
                    previous['cleanup'].update(native_pages_status='verified_absent', saved_group_status='verified_absent')
                    lifecycle.archive(path, previous)
                    browser_audit.register(root, previous, path)
                    receipt.pop('previous_cleanup'); lifecycle.save(path, receipt)
                inspect_browser()
                print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 0

            actual = metadata()
            if (actual['group_id'] != receipt['group_id'] or actual['connection_id'] != receipt['connection_id']
                    or not set(actual['native_tab_ids']).issubset(receipt['owned_tab_ids'])):
                raise ValueError('Group or page membership changed outside this execution; inspect exact objects before cleanup.')
            if args.command in {'group-info','group-name'}:
                print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 0
            if args.command == 'audit':
                print(json.dumps(inspect_browser(), ensure_ascii=False, indent=2)); return 0
            if args.command in {'finish','disconnect'}:
                receipt['owned_tab_ids'] = actual['native_tab_ids']
                inspect_browser()
                receipt['end_audit']['native_snapshot_phase'] = 'before_own_cleanup'
                current = saved_groups(profile)
                receipt['saved_group_ids'] = sorted(set(receipt['saved_group_ids']) | {gid for gid,g in current.items()
                    if gid not in receipt['saved_baseline_ids'] and g['title'] == receipt['title']})
                receipt['state'] = 'closing'; persist()
                code, output = invoke('run-code', [cleanup_code('executionCleanup', receipt)])
                if code:
                    receipt['state'] = 'unknown'; persist(); print(output); return code
                cleanup = parse_result(output, 'BrowserCleanupV1')
                receipt['cleanup'] = cleanup
                if not cleanup['scheduled']:
                    receipt['state'] = 'active'; persist()
                    print(json.dumps(receipt, ensure_ascii=False, indent=2)); return 1
                # Native IDs become detached before their tabs close. Empty CLI pages
                # alone do not prove closure; the next fresh execution checks exact IDs.
                # An unsaved group may already be absent; let the native timer run
                # before detaching so its ownership check can still succeed.
                time.sleep(3)
                for _ in range(30):
                    time.sleep(.2)
                    current = saved_groups(profile)
                    if not any(g in current for g in receipt['saved_group_ids']) and not any(g['title']==receipt['title'] for g in current.values()):
                        cleanup['saved_group_status'] = 'verified_absent'; break
                if cleanup['saved_group_status'] == 'verified_absent':
                    release_code, release_output = invoke('disconnect')
                    cleanup['connection_release'] = release_status(release_code, release_output, receipt['cli_session'])
                else:
                    cleanup['connection_release'] = 'not_attempted'
                receipt['state'] = 'cleanup_pending'
                receipt['finished_at'] = lifecycle.now()
                receipt['end_audit']['post_cleanup_saved'] = {'captured_at':lifecycle.now(),
                    'own_saved_records_absent':cleanup['saved_group_status']=='verified_absent',
                    'remaining_saved_records':len(current),
                    'saved_without_local_marker':sum(not g.get('hasLocalGroupId') for g in current.values())}
                receipt['end_audit']['end_duties'] = {'native_inventory':'verified',
                    'previous_residuals_checked':True,'connection_release':cleanup['connection_release'],
                    'last_native_helper':'pending_next_native_readback'}
                persist(); lifecycle.archive(path, receipt)
                print(json.dumps(receipt, ensure_ascii=False, indent=2))
                return 0 if cleanup['saved_group_status'] == 'verified_absent' and cleanup['connection_release'] != 'unverified' else 1
            if args.command == 'tab-close' and len(receipt['owned_tab_ids']) <= 1:
                raise ValueError('Use finish to delete the final group before closing its final tab.')
            code, output = invoke(args.command, args.arguments)
            print(output, end='' if output.endswith('\n') else '\n')
            if code:
                receipt['state'] = 'unknown'; persist(); return code
            after = metadata()
            if (after['group_id'] != receipt['group_id'] or after['connection_id'] != receipt['connection_id']
                    or args.command not in {'tab-new','run-code'} and not set(after['native_tab_ids']).issubset(receipt['owned_tab_ids'])):
                receipt['state'] = 'unknown'; persist()
                raise ValueError('Unexpected native objects after the command; ownership was not expanded.')
            receipt['owned_tab_ids'] = after['native_tab_ids']
            persist()
            return 0
        except (ValueError, OSError):
            if receipt['state'] in {'connecting','closing','active'}:
                receipt['state'] = 'unknown'; lifecycle.save(path, receipt)
                browser_audit.register(root, receipt, path)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'edge'], default='chrome')
    parser.add_argument('--session', help='Task-specific name; browser prefix is added automatically')
    parser.add_argument('--project', help='Visible project name; defaults to the current working directory name')
    parser.add_argument('--conversation', help='Real Codex conversation ID when CODEX_THREAD_ID is unavailable')
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
        return managed_main(args, root, settings)
    except subprocess.TimeoutExpired:
        print('RESULT_UNKNOWN: CLI wait expired. Read back this named session before repeating an action.', file=sys.stderr)
        return 124
    except (ValueError, OSError, KeyError) as error:
        print(redact(str(error), secrets), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
