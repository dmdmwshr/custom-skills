#!/usr/bin/env python3
"""Publish reviewed source trees to WSL Codex; verify before replacing any copy."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
INSTALL = Path('/root/.codex/skills')
RECORDS = Path('/root/Documents/work/host-baseline/skill-releases')
SKILLS = ('cc-connect-collaboration', 'x-message-monitoring', 'codex-project-task-handoff',
          'codex-archive-retrospective', 'codex-local-state-diagnostics',
          'okxnew-backtest-operations', 'okxnew-data-operations')


def files(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError('skill_symlink_rejected')
    result = {}
    for entry in sorted(path.rglob('*')):
        if any(part in {'__pycache__', '.pytest_cache', '.git'} for part in entry.relative_to(path).parts) or entry.suffix in {'.pyc', '.pyo'}:
            continue
        if entry.is_symlink():
            raise ValueError('skill_symlink_rejected')
        if entry.is_file():
            relative = entry.relative_to(path).as_posix()
            result[relative] = hashlib.sha256(entry.read_bytes()).hexdigest()
    if 'SKILL.md' not in result:
        raise ValueError('skill_entrypoint_required')
    return result


def git_output(source: Path, *args: str) -> str:
    return subprocess.check_output(
        ['git', '-C', str(source), *args], text=True, encoding='utf-8',
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, timeout=30).strip()


def require_published_source(source: Path) -> str:
    """Read-only gate: require a clean main already visible on the live remote."""
    if git_output(source, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
        raise ValueError('source_branch_must_be_main')
    if git_output(source, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('source_dirty_commit_and_push_before_install')
    if git_output(source, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}') != 'origin/main':
        raise ValueError('source_upstream_must_be_origin_main')
    revision = git_output(source, 'rev-parse', 'HEAD')
    live = git_output(source, 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').split()
    if len(live) != 2 or live[0] != revision or live[1] != 'refs/heads/main':
        raise ValueError('source_not_equal_to_live_remote_commit')
    return revision


def require_tracked_tree(source: Path, name: str, tree: dict[str, str]) -> None:
    tracked = git_output(source, 'ls-files', '-z', '--', name)
    prefix = name + '/'
    paths = {p[len(prefix):] for p in tracked.split('\0') if p.startswith(prefix)}
    if paths != set(tree):
        raise ValueError('source_tree_contains_untracked_or_ignored_files')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--skill', choices=SKILLS, action='append',
                        help='select a reviewed skill; repeat to select several (default: all)')
    args = parser.parse_args()
    if args.apply and args.verify:
        parser.error('choose apply or verify')
    if SOURCE != Path('/root/workspaces/custom-skills') or INSTALL.resolve() != INSTALL:
        raise ValueError('source_or_install_path_invalid')
    remote = subprocess.check_output(['git', '-C', str(SOURCE), 'remote', 'get-url', 'origin'], text=True, encoding='utf-8').strip()
    if remote not in {'https://github.com/dmdmwshr/custom-skills.git', 'git@github.com:dmdmwshr/custom-skills.git'}:
        raise ValueError('source_repository_mismatch')
    revision = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True, encoding='utf-8').strip()
    selected = tuple(dict.fromkeys(args.skill or SKILLS))
    trees = {name: files(SOURCE / name) for name in selected}
    if args.verify:
        for name, tree in trees.items():
            if files(INSTALL / name) != tree:
                raise ValueError('installed_skill_differs_from_source')
            recorded = json.loads((RECORDS / (name + '.json')).read_text(encoding='utf-8'))
            if recorded['files'] != tree:
                raise ValueError('installed_skill_release_record_mismatch')
            if recorded['source_commit'] != revision or recorded.get('source_dirty') is not False:
                raise ValueError('installed_skill_not_from_current_clean_commit')
        print(json.dumps({'verified': True, 'skills': list(selected)}))
        return
    if not args.apply:
        print(json.dumps({'dry_run': True, 'source_commit': revision,
                          'source_dirty': bool(git_output(SOURCE, 'status', '--porcelain=v1', '--untracked-files=all')),
                          'apply_requires': 'clean main matching live origin/main; all payload files tracked',
                          'files': {n: len(t) for n, t in trees.items()}}))
        return
    if require_published_source(SOURCE) != revision:
        raise ValueError('source_revision_changed_during_preflight')
    # Preflight every selected target before creating a release or replacing any copy.
    for name, tree in trees.items():
        require_tracked_tree(SOURCE, name, tree)
        target = INSTALL / name
        if target.is_symlink():
            raise ValueError('installed_symlink_rejected')
        if target.exists():
            previous = json.loads((RECORDS / (name + '.json')).read_text(encoding='utf-8'))
            if files(target) != previous['files']:
                raise ValueError('installed_copy_modified_keep_existing')
    RECORDS.mkdir(parents=True, exist_ok=True, mode=0o700)
    run = RECORDS / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(mode=0o700)
    for name, tree in trees.items():
        target = INSTALL / name
        if target.is_symlink():
            raise ValueError('installed_symlink_rejected')
        if target.exists():
            previous = json.loads((RECORDS / (name + '.json')).read_text(encoding='utf-8'))
            if files(target) != previous['files']:
                raise ValueError('installed_copy_modified_keep_existing')
        with tempfile.TemporaryDirectory(prefix='.wsl-skill-', dir=INSTALL) as temporary:
            staged = Path(temporary) / name
            shutil.copytree(SOURCE / name, staged, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc', '*.pyo', '.git'))
            if files(staged) != tree:
                raise ValueError('staged_tree_mismatch')
            backup = run / name
            if target.exists():
                shutil.copytree(target, backup)
                # Same-filesystem renames keep rollback available until publish.
                old = Path(temporary) / 'previous'
                target.rename(old)
            else:
                old = None
            try:
                staged.rename(target)
            except BaseException:
                if old is not None:
                    old.rename(target)
                raise
        record = {'schema': 'WslSkillReleaseV1', 'source_repository': 'dmdmwshr/custom-skills',
                  'source_commit': revision, 'source_dirty': False,
                  'remote_verified_commit': revision,
                  'files': tree, 'tree_sha256': hashlib.sha256(json.dumps(tree, sort_keys=True).encode('utf-8')).hexdigest()}
        encoded = json.dumps(record, ensure_ascii=False, indent=2) + '\n'
        (run / (name + '.json')).write_text(encoded, encoding='utf-8')
        pending = RECORDS / (name + '.pending')
        pending.write_text(encoded, encoding='utf-8')
        os.replace(pending, RECORDS / (name + '.json'))
    print(json.dumps({'installed': list(selected), 'source_commit': revision, 'release_record': str(run)}))


if __name__ == '__main__':
    main()
