#!/usr/bin/env python3
"""Small offline checks for the generic downloader's request and state safety."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("download_via_meifu.py")
SPEC = importlib.util.spec_from_file_location("download_via_meifu", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DownloadRequestTests(unittest.TestCase):
    def test_windows_safe_current_process_liveness_check(self) -> None:
        self.assertTrue(MODULE.process_is_running(os.getpid()))

    def test_windows_liveness_probe_never_uses_posix_kill(self) -> None:
        result = MODULE.subprocess.CompletedProcess(
            ["tasklist"],
            0,
            stdout='"python.exe","12345","Console","1","10 K"\n',
            stderr="",
        )
        with patch.object(MODULE.os, "name", "nt"), patch.object(
            MODULE.subprocess, "run", return_value=result
        ), patch.object(MODULE.os, "kill") as kill:
            self.assertTrue(MODULE.process_is_running(12345))
            kill.assert_not_called()

    def test_remote_cache_parent_is_posix(self) -> None:
        self.assertEqual(MODULE.remote_cache_parent(), "/root/.cache")

    def test_rejects_relative_output(self) -> None:
        with self.assertRaises(MODULE.DownloadError):
            MODULE.validate_request("https://example.com/file.bin", "file.bin", None, allow_http=False)

    def test_storage_root_and_relative_target_resolve_to_one_safe_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "models"
            output = MODULE.resolve_storage_target(str(root), "speech/tts/model.bin")
            self.assertEqual(output, (root / "speech" / "tts" / "model.bin").resolve(strict=False))

    def test_storage_target_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MODULE.DownloadError):
                MODULE.resolve_storage_target(temporary, "../outside.bin")

    def test_redacts_query_from_state_identity(self) -> None:
        request = MODULE.validate_request(
            "https://example.com/path/file.bin?token=secret",
            str(Path(tempfile.gettempdir()) / "file.bin"),
            None,
            allow_http=False,
        )
        self.assertEqual(request.safe_url, "https://example.com/path/file.bin")
        self.assertNotIn("token", request.safe_url)

    def test_regular_etag_is_not_treated_as_source_sha256(self) -> None:
        request = MODULE.validate_request(
            "https://example.com/file.bin",
            str(Path(tempfile.gettempdir()) / "file.bin"),
            None,
            allow_http=False,
        )
        resolved, source = MODULE.resolve_expected_hash(
            request,
            {"etag": "a" * 64, "x_linked_etag": None},
        )
        self.assertIsNone(resolved.expected_sha256)
        self.assertEqual(source, "computed_only")

    def test_linked_etag_can_provide_source_sha256(self) -> None:
        request = MODULE.validate_request(
            "https://example.com/file.bin",
            str(Path(tempfile.gettempdir()) / "file.bin"),
            None,
            allow_http=False,
        )
        resolved, source = MODULE.resolve_expected_hash(
            request,
            {"etag": "not-a-sha", "x_linked_etag": "b" * 64},
        )
        self.assertEqual(resolved.expected_sha256, "b" * 64)
        self.assertEqual(source, "source_linked_etag")

    def test_state_refuses_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = MODULE.validate_request(
                "https://example.com/a.bin",
                str(root / "a.bin"),
                None,
                allow_http=False,
            )
            state_path = root / "state.json"
            MODULE.load_or_create_state(
                state_path,
                first,
                size_bytes=10,
                chunk_bytes=4,
                etag="etag-a",
            )
            second = MODULE.validate_request(
                "https://example.com/b.bin",
                str(root / "a.bin"),
                None,
                allow_http=False,
            )
            with self.assertRaises(MODULE.DownloadError):
                MODULE.load_or_create_state(
                    state_path,
                    second,
                    size_bytes=10,
                    chunk_bytes=4,
                    etag="etag-a",
                )

    def test_state_does_not_store_signed_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = MODULE.validate_request(
                "https://example.com/a.bin?sig=top-secret",
                str(root / "a.bin"),
                None,
                allow_http=False,
            )
            state_path = root / "state.json"
            MODULE.load_or_create_state(
                state_path,
                request,
                size_bytes=10,
                chunk_bytes=4,
                etag="etag-a",
            )
            self.assertNotIn("top-secret", state_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema"], "MeifuResumableDownloadStateV1")

    def test_same_output_allows_only_one_active_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = MODULE.validate_request(
                "https://example.com/file.bin",
                str(Path(temporary) / "file.bin"),
                None,
                allow_http=False,
            )
            with MODULE.acquire_output_lock(request):
                with self.assertRaises(MODULE.DownloadInProgress):
                    with MODULE.acquire_output_lock(request):
                        pass
            with MODULE.acquire_output_lock(request):
                pass

    def test_dead_owner_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = MODULE.validate_request(
                "https://example.com/file.bin",
                str(Path(temporary) / "file.bin"),
                None,
                allow_http=False,
            )
            lock_dir = MODULE.output_lock_dir(request)
            lock_dir.mkdir(parents=True)
            (lock_dir / "owner.json").write_text(
                json.dumps({"schema": "MeifuOutputLockV1", "pid": 0}),
                encoding="utf-8",
            )
            with MODULE.acquire_output_lock(request):
                pass

    def test_reserved_lock_blocks_duplicates_until_the_worker_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = MODULE.validate_request(
                "https://example.com/file.bin",
                str(Path(temporary) / "file.bin"),
                None,
                allow_http=False,
            )
            launcher_lock = MODULE.create_output_lock(request, phase="launching")
            try:
                with self.assertRaises(MODULE.DownloadInProgress):
                    MODULE.create_output_lock(request, phase="launching")
                worker_lock = MODULE.claim_reserved_output_lock(request, launcher_lock.token)
                launcher_lock.disown()
                worker_lock.release()
                self.assertFalse(MODULE.output_lock_dir(request).exists())
            finally:
                launcher_lock.release()

    def test_background_command_keeps_signed_source_off_the_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = MODULE.validate_request(
                "https://example.com/file.bin?token=top-secret",
                str(Path(temporary) / "file.bin"),
                "a" * 64,
                allow_http=False,
            )
            args = type(
                "Args",
                (),
                {
                    "host": "meifu-test",
                    "expected_hostname": "192.0.2.1",
                    "chunk_gib": 0.5,
                    "remote_reserve_gib": 1.0,
                    "local_reserve_gib": 1.0,
                    "allow_http": False,
                },
            )()
            command = MODULE.background_worker_command(request, args, "lock-token")
            rendered = " ".join(command)
            self.assertIn("--url-stdin", command)
            self.assertIn("--_background-worker", command)
            self.assertNotIn("top-secret", rendered)
            self.assertNotIn(request.source_url, rendered)

    def test_local_status_and_persisted_worker_state_redact_signed_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = MODULE.validate_request(
                "https://example.com/file.bin?token=top-secret",
                str(Path(temporary) / "file.bin"),
                None,
                allow_http=False,
            )
            MODULE.write_worker_status(
                request,
                status="waiting_for_network",
                detail="来源 https://example.com/file.bin?token=top-secret 暂不可用",
                exit_code=MODULE.TEMPORARY_TRANSFER_EXIT_CODE,
            )
            persisted = MODULE.worker_status_path(request).read_text(encoding="utf-8")
            inspected = MODULE.inspect_download_status(str(request.output))
            self.assertNotIn("top-secret", persisted)
            self.assertNotIn("top-secret", json.dumps(inspected, ensure_ascii=False))
            self.assertEqual(inspected["status"], "waiting_for_network")


if __name__ == "__main__":
    unittest.main()
