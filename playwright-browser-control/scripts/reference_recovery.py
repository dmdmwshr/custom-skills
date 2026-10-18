"""Narrow recovery for a pinned CLI click rejected before input dispatch."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse


VALIDATED_CLI = '0.1.22'


def rejected_reference(command, arguments, output, version):
    if version != VALIDATED_CLI or command != 'click' or len(arguments) != 1:
        return None
    ref = arguments[0]
    if not isinstance(ref, str) or not re.fullmatch(r'(?:f\d+)?e\d+', ref):
        return None
    expected = f'### Error\nError: Ref {ref} not found in the current page snapshot. Try capturing new snapshot.'
    normalized = re.sub(r'\x1b\[[0-9;]*m', '', output.replace('\r\n', '\n')).strip()
    classification = 'reference_not_found_before_input'
    if normalized != expected:
        # The pinned core waits here before scrolling, hit testing and input.
        # Accept exactly this first-attempt log, never retries or later phases.
        pattern = (r"### Error\nTimeoutError: Timeout \d+ms exceeded\.\nCall log:\n"
            r"  - waiting for locator\('aria-ref=" + re.escape(ref) + r"'\)\n"
            r"    - locator resolved to [^\n]+\n  - attempting click action\n"
            r"    - waiting for element to be visible, enabled and stable")
        if not re.fullmatch(pattern, normalized):
            return None
        classification = 'actionability_wait_before_input'
    return {'classification': classification, 'command': command,
            'reference': ref, 'cli_version': version,
            'output_sha256': hashlib.sha256(output.encode()).hexdigest(), 'output': output}


def legacy_proof(log_path, ordinal, receipt, version):
    """Read one original execution receipt; reject later unaccounted browser input.

    This migration is intentionally limited to the simple Windows Python click
    invocation used by the old launcher. It is not a general unknown-state reset.
    """
    path = Path(log_path).resolve()
    if not path.is_file() or path.suffix != '.jsonl':
        raise ValueError('Recovery requires the original local execution log.')
    proof = None
    owner_verified = False
    session = re.escape(receipt['session_name'])
    invocation = re.compile(
        r"(?:python|\S*python\.exe) -X utf8 ['\"]([^'\"]*browser_cli\.py)['\"] "
        r'--session ' + session + r' click ((?:f\d+)?e\d+)')
    with path.open(encoding='utf-8') as stream:
        for raw in stream:
            row = json.loads(raw)
            payload = row.get('payload', {})
            if row.get('type') == 'session_meta':
                owner_verified = (payload.get('id') == receipt['conversation_id'] and
                    Path(payload.get('cwd', '')).resolve() == Path(receipt['project_root']).resolve())
            if row.get('ordinal') == ordinal:
                item = payload.get('item', {})
                script = item.get('command', [''])[-1]
                lines = script.strip().splitlines()
                match = invocation.fullmatch(lines[-1]) if lines else None
                output = item.get('aggregated_output', '')
                proof = rejected_reference('click', [match[2]] if match else [], output, version)
                cwd = unquote(urlparse(item.get('cwd', '')).path).lstrip('/')
                prefix_ok = len(lines) == 1 or (len(lines) == 2 and lines[0].strip() in {
                    '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)',
                    '$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)'})
                if (not owner_verified or row.get('type') != 'event_msg' or
                    payload.get('type') != 'item_completed' or payload.get('thread_id') != receipt['conversation_id'] or
                    item.get('type') != 'CommandExecution' or item.get('status') != 'failed' or
                    item.get('exit_code') != 1 or receipt['browser_name'] != 'chrome' or
                    Path(cwd).resolve() != Path(receipt['project_root']).resolve() or not prefix_ok or not proof or
                    datetime.fromisoformat(row['timestamp']) < datetime.fromisoformat(receipt['created_at'])):
                    raise ValueError('Original failure does not prove this execution rejected input before dispatch.')
                proof.update(log_path=str(path), ordinal=ordinal, item_id=item['id'], timestamp=row['timestamp'],
                             record_sha256=hashlib.sha256(raw.encode()).hexdigest())
            elif proof and row.get('type') == 'event_msg' and payload.get('type') == 'item_completed':
                item = payload.get('item', {})
                script = item.get('command', [''])[-1]
                # Subsequent known management rejections and read-only commands are safe.
                # A later actual/unknown action invalidates this old proof.
                if item.get('type') == 'CommandExecution':
                    match = re.search(r'--session\s+' + session + r'\s+(\S+)', script)
                    if match and 'browser_cli.py' in script and match[1] not in {'status','audit','group-info','readback'}:
                        rejected = item.get('aggregated_output', '').strip() == (
                            'Execution requires readback; no business commands or blind reconnect are allowed.')
                        if not rejected:
                            raise ValueError('A later browser operation exists; the earlier failure cannot reconcile it.')
    if not proof:
        raise ValueError('Original failure record was not found.')
    return proof


def recovery_proof(arguments, receipt, version):
    failure = receipt.get('last_failure')
    if (failure and failure.get('classification') == 'unclassified_result_unknown' and
        len(arguments) == 2 and receipt['state'] == 'unknown'):
        proof = legacy_proof(arguments[0], int(arguments[1]), receipt, version)
        if abs((datetime.fromisoformat(proof['timestamp']) - datetime.fromisoformat(failure['at'])).total_seconds()) > 2:
            raise ValueError('The original record is not the currently unresolved failure.')
        return proof
    if failure:
        proof = rejected_reference(failure.get('command'), [failure.get('reference')],
                                   failure.get('output', ''), version)
        if not proof or arguments or failure.get('reconciled_at'):
            raise ValueError('This failure is not eligible for reference recovery.')
        return failure
    if len(arguments) != 2 or receipt['state'] != 'unknown':
        raise ValueError('Legacy recovery requires the original log path and exact failed event ordinal.')
    return legacy_proof(arguments[0], int(arguments[1]), receipt, version)
