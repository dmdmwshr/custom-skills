#!/usr/bin/env python3
"""Verify skill discovery in a fresh native Codex process without a model turn."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
from publish_wsl_skills import SKILLS, SOURCE


def verify(names: list[str], cwds: list[str]) -> dict:
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            ['/root/.local/bin/codex', 'app-server', '--stdio'], cwd=SOURCE,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            text=True, encoding='utf-8')
        inbox: queue.Queue = queue.Queue()

        def read_output() -> None:
            for line in process.stdout:
                try:
                    inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            inbox.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        def send(message: dict) -> None:
            process.stdin.write(json.dumps(message) + '\n')
            process.stdin.flush()

        def request(identifier: int, method: str, params: dict) -> dict:
            send({'id': identifier, 'method': method, 'params': params})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                message = inbox.get(timeout=max(.01, deadline - time.monotonic()))
                if message is None:
                    raise RuntimeError('discovery_process_exited')
                if message.get('id') == identifier:
                    if 'error' in message:
                        raise RuntimeError('discovery_rpc_failed:' + method)
                    return message['result']
            raise TimeoutError(method)

        try:
            request(1, 'initialize', {'clientInfo': {'name': 'wsl-skill-discovery', 'version': '1.0.0'}})
            send({'method': 'initialized', 'params': {}})
            result = request(2, 'skills/list', {'cwds': cwds, 'forceReload': True})
            rows = []
            if len(result['data']) != len(cwds):
                raise ValueError('discovery_cwd_count_mismatch')
            for entry in result['data']:
                if entry.get('errors'):
                    raise ValueError('skill_load_errors')
                found = []
                for name in names:
                    matches = [s for s in entry['skills'] if s['name'] == name]
                    expected = f'/root/.codex/skills/{name}/SKILL.md'
                    if len(matches) != 1 or matches[0].get('path') != expected or matches[0].get('enabled') is not True:
                        raise ValueError('skill_not_uniquely_enabled:' + name)
                    found.append({'name': name, 'path': expected, 'enabled': True})
                rows.append({'cwd': entry['cwd'], 'skills': found, 'errors': []})
            return {'discovery_verified': True, 'model_turn_started': False, 'data': rows}
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(timeout=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skill', action='append', choices=SKILLS)
    parser.add_argument('--cwd', action='append')
    args = parser.parse_args()
    names = list(dict.fromkeys(args.skill or SKILLS))
    cwds = list(dict.fromkeys(str(Path(cwd).resolve()) for cwd in (args.cwd or [str(SOURCE)])))
    print(json.dumps(verify(names, cwds), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
