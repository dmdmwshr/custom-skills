from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_handoff_state.py"


class HandoffStateValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "handoff-test")
        self._git("config", "user.email", "handoff-test@example.invalid")
        self.handoff = self.repo / "PROJECT_HANDOFF.md"
        self.handoff.write_text("# 项目交接\n\n- 状态：已冻结\n", encoding="utf-8", newline="\n")
        (self.repo / "main.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
        self._git("add", "PROJECT_HANDOFF.md", "main.py")
        self._git("commit", "-m", "initial")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _sha256(self) -> str:
        return hashlib.sha256(self.handoff.read_bytes()).hexdigest()

    def _run(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
        return completed, payload

    def _base_args(self) -> tuple[str, ...]:
        return (
            "--repo",
            str(self.repo),
            "--file",
            "PROJECT_HANDOFF.md",
            "--expected-branch",
            "main",
            "--head",
            self._head(),
            "--sha256",
            self._sha256(),
            "--require-clean",
        )

    def test_success_reports_exact_and_normalized_fingerprints(self) -> None:
        completed, payload = self._run(*self._base_args())
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["code"], "OK")
        self.assertEqual(payload["decision"], "READY")
        self.assertEqual(payload["branch"], "main")
        self.assertFalse(payload["detached"])
        self.assertEqual(payload["head"], self._head())
        self.assertEqual(payload["file_sha256"], self._sha256())
        self.assertEqual(payload["normalized_sha256"], self._sha256())
        self.assertFalse(payload["normalization_changed"])
        self.assertEqual(payload["status_count"], 0)
        self.assertTrue(payload["tracked"])

    def test_dirty_worktree_is_blocked(self) -> None:
        (self.repo / "main.py").write_text("print('changed')\n", encoding="utf-8", newline="\n")
        completed, payload = self._run(*self._base_args())
        self.assertEqual(completed.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["decision"], "HANDOFF_BLOCKED")
        self.assertEqual(payload["code"], "DIRTY_WORKTREE")

    def test_wrong_branch_is_blocked(self) -> None:
        self._git("switch", "-c", "other")
        completed, payload = self._run(*self._base_args())
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["decision"], "HANDOFF_BLOCKED")
        self.assertEqual(payload["code"], "BRANCH_MISMATCH")

    def test_exact_hash_mismatch_blocks_even_when_normalized_digest_matches(self) -> None:
        original_hash = self._sha256()
        self.handoff.write_bytes(self.handoff.read_bytes().replace(b"\n", b"\r\n"))
        normalized_hash = hashlib.sha256(
            self.handoff.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        self.assertEqual(normalized_hash, original_hash)
        completed, payload = self._run(
            "--repo",
            str(self.repo),
            "--file",
            "PROJECT_HANDOFF.md",
            "--sha256",
            original_hash,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["code"], "FILE_SHA256_MISMATCH")
        self.assertEqual(payload["normalized_sha256"], normalized_hash)
        self.assertTrue(payload["normalization_changed"])

    def test_untracked_handoff_is_blocked(self) -> None:
        self._git("rm", "--cached", "PROJECT_HANDOFF.md")
        completed, payload = self._run(
            "--repo",
            str(self.repo),
            "--file",
            "PROJECT_HANDOFF.md",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["code"], "FILE_NOT_TRACKED")

    def test_no_git_is_blocked_without_initializing_repository(self) -> None:
        plain = Path(self.tempdir.name) / "plain"
        plain.mkdir()
        handoff = plain / "PROJECT_HANDOFF.md"
        handoff.write_text("# 项目交接\n", encoding="utf-8", newline="\n")
        completed, payload = self._run(
            "--repo",
            str(plain),
            "--file",
            str(handoff),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["code"], "NO_GIT")
        self.assertFalse((plain / ".git").exists())

    def test_head_mismatch_is_blocked(self) -> None:
        wrong_head = "0" * 40
        completed, payload = self._run(
            "--repo",
            str(self.repo),
            "--head",
            wrong_head,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["code"], "HEAD_MISMATCH")


if __name__ == "__main__":
    unittest.main()
