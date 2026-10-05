"""Durable execution identity; browser objects are never restored from history."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def conversation(explicit=None):
    value = explicit or os.environ.get('CODEX_THREAD_ID')
    if not value or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        raise ValueError('A real conversation ID is required (CODEX_THREAD_ID or --conversation).')
    return value


def receipt_path(cwd, browser, session):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', session):
        raise ValueError('Invalid session label.')
    return Path(cwd) / '.playwright' / 'groups' / f'{browser}-{session}.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)


def archive(path, value):
    target = path.parent.parent / 'history' / path.stem / (value['execution_id'] + '.json')
    save(target, value)


def fresh(identity, cwd, profile, owner):
    identifier = str(uuid.uuid4())
    return {**identity, 'lifecycle_schema': 'BrowserExecutionV1', 'execution_id': identifier,
            'conversation_id': owner, 'project_root': str(Path(cwd).resolve()),
            'profile_key': hashlib.sha256(str(Path(profile).resolve()).encode()).hexdigest(),
            'cli_session': identity['browser_name'] + '-' + identity['session_name'][:24] + '-' + identifier[:12],
            'state': 'connecting', 'created_at': now(), 'owned_tab_ids': [],
            'saved_group_ids': [], 'cleanup': None}


def check_owner(value, cwd, profile, owner):
    if value.get('lifecycle_schema') != 'BrowserExecutionV1':
        raise ValueError('Legacy connection: inspect and retire its exact objects; do not reuse it.')
    expected = hashlib.sha256(str(Path(profile).resolve()).encode()).hexdigest()
    if (value['conversation_id'] != owner or value['project_root'] != str(Path(cwd).resolve())
            or value['profile_key'] != expected):
        raise ValueError('Conversation, project or browser profile ownership differs.')


@contextmanager
def locked(path):
    lock = path.with_suffix('.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b') as stream:
        stream.seek(0)
        if stream.read(1) == b'':
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError('This execution is already being controlled.') from None
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
