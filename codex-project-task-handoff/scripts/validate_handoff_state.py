#!/usr/bin/env python3
"""Validate the immutable state used to start a project successor task.

This helper is deliberately read-only.  It never stages, commits, pushes,
creates a worktree, or edits the handoff document.  The exact byte SHA-256 is
the authoritative file identity; the LF-normalized digest is diagnostic only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence


EXIT_OK = 0
EXIT_BLOCKED = 2
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ValidationBlocked(Exception):
    """A requested handoff invariant could not be proved."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _run_git(repo: Path, args: Sequence[str]) -> tuple[int, str, str]:
    """Run a Git read-only query with UTF-8 decoding and no shell."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _resolve_file(raw_file: str | None, repo_root: Path | None) -> tuple[Path | None, str | None]:
    if raw_file is None:
        return None, None

    candidate = Path(raw_file).expanduser()
    if not candidate.is_absolute() and repo_root is not None:
        candidate = repo_root / candidate
    resolved = candidate.resolve(strict=False)
    if repo_root is None:
        return resolved, None
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValidationBlocked(
            "FILE_OUTSIDE_REPO",
            "handoff file is outside the Git repository root",
        ) from exc
    return resolved, relative.as_posix()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalization_fingerprint(data: bytes) -> tuple[str | None, bool, str | None]:
    """Return (digest, changed, error) for UTF-8 LF-normalized bytes."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, False, f"handoff file is not valid UTF-8: {exc}"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return _sha256(normalized), normalized != data, None


def _git_status(repo_root: Path) -> list[str]:
    code, stdout, stderr = _run_git(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    )
    if code != 0:
        raise ValidationBlocked("GIT_STATUS_FAILED", stderr or "git status failed")
    return [line for line in stdout.splitlines() if line]


def validate(args: argparse.Namespace) -> dict[str, Any]:
    raw_repo = Path(args.repo).expanduser().resolve(strict=False)
    if not raw_repo.exists() or not raw_repo.is_dir():
        raise ValidationBlocked("NO_GIT", "repo path does not exist or is not a directory")

    code, inside, stderr = _run_git(raw_repo, ["rev-parse", "--is-inside-work-tree"])
    if code != 0 or inside.lower() != "true":
        raise ValidationBlocked("NO_GIT", stderr or "path is not a Git worktree")

    code, root_text, stderr = _run_git(raw_repo, ["rev-parse", "--show-toplevel"])
    if code != 0 or not root_text:
        raise ValidationBlocked("NO_GIT", stderr or "cannot resolve Git repository root")
    repo_root = Path(root_text).resolve()

    code, head, stderr = _run_git(raw_repo, ["rev-parse", "--verify", "HEAD"])
    if code != 0 or not FULL_SHA_RE.fullmatch(head):
        raise ValidationBlocked("NO_HEAD", stderr or "Git repository has no verifiable HEAD")

    branch_code, branch, _ = _run_git(raw_repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    detached = branch_code != 0 or not branch
    if detached:
        branch = None

    status = _git_status(repo_root)
    handoff_file, relative_file = _resolve_file(args.file, repo_root)
    file_sha256: str | None = None
    normalized_sha256: str | None = None
    normalization_changed = False
    tracked = False

    if handoff_file is not None:
        if not handoff_file.exists() or not handoff_file.is_file():
            raise ValidationBlocked("FILE_MISSING", "handoff file does not exist as a regular file")
        assert relative_file is not None
        tracked_code, _, tracked_stderr = _run_git(
            repo_root,
            ["ls-files", "--error-unmatch", "--", relative_file],
        )
        tracked = tracked_code == 0
        if not tracked:
            raise ValidationBlocked(
                "FILE_NOT_TRACKED",
                tracked_stderr or "handoff file is not tracked by Git",
            )
        raw_bytes = handoff_file.read_bytes()
        file_sha256 = _sha256(raw_bytes)
        normalized_sha256, normalization_changed, normalization_error = _normalization_fingerprint(
            raw_bytes
        )
        if normalization_error is not None:
            raise ValidationBlocked("FILE_NOT_UTF8", normalization_error)

    result: dict[str, Any] = {
        "ok": True,
        "code": "OK",
        "decision": "READY",
        "repo": str(raw_repo),
        "repo_root": str(repo_root),
        "branch": branch,
        "detached": detached,
        "head": head,
        "status_count": len(status),
        "status_paths": [line[3:] if len(line) >= 4 else line for line in status],
        "handoff_file": str(handoff_file) if handoff_file is not None else None,
        "handoff_relative_file": relative_file,
        "tracked": tracked,
        "file_sha256": file_sha256,
        "normalized_sha256": normalized_sha256,
        "normalization_changed": normalization_changed,
    }

    if args.expected_branch is not None:
        expected = args.expected_branch.removeprefix("refs/heads/")
        if detached or branch != expected:
            raise ValidationBlocked(
                "BRANCH_MISMATCH",
                f"expected branch {expected!r}, observed {branch or '(detached)'!r}",
                result,
            )

    if args.head is not None:
        if not FULL_SHA_RE.fullmatch(args.head):
            raise ValidationBlocked("INVALID_EXPECTED_HEAD", "--head must be a full 40-character SHA-1")
        if head.lower() != args.head.lower():
            raise ValidationBlocked(
                "HEAD_MISMATCH",
                f"expected HEAD {args.head.lower()}, observed {head.lower()}",
                result,
            )

    if args.sha256 is not None:
        if handoff_file is None:
            raise ValidationBlocked("FILE_REQUIRED", "--sha256 requires --file")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", args.sha256):
            raise ValidationBlocked("INVALID_EXPECTED_SHA256", "--sha256 must be a 64-character hex digest")
        assert file_sha256 is not None
        if file_sha256.lower() != args.sha256.lower():
            raise ValidationBlocked(
                "FILE_SHA256_MISMATCH",
                "exact handoff file SHA-256 does not match; normalized digest is auxiliary only",
                result,
            )

    if args.require_clean and status:
        raise ValidationBlocked(
            "DIRTY_WORKTREE",
            f"Git worktree has {len(status)} tracked or untracked status entries",
            result,
        )

    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only validation of a project handoff Git ref and document fingerprint."
    )
    parser.add_argument("--repo", required=True, help="Git repository or worktree path")
    parser.add_argument("--file", help="handoff document path, absolute or relative to --repo")
    parser.add_argument("--expected-branch", help="expected symbolic branch name")
    parser.add_argument("--head", help="expected full 40-character HEAD SHA-1")
    parser.add_argument("--sha256", help="expected exact handoff file SHA-256")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="fail when tracked or untracked Git status entries exist",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="output format (default: json)",
    )
    return parser.parse_args(argv)


def _emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        print(f"{key}={value}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = validate(args)
    except ValidationBlocked as exc:
        payload = {
            **exc.details,
            "ok": False,
            "code": exc.code,
            "decision": "HANDOFF_BLOCKED",
            "message": exc.message,
        }
        _emit(payload, args.format)
        return EXIT_BLOCKED
    _emit(payload, args.format)
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(EXIT_OK)
