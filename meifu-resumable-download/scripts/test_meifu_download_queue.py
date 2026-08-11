#!/usr/bin/env python3
"""Offline checks for the persistent generic Meifu queue.

These tests do not start a worker, connect to Meifu, or create a Scheduled Task.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("meifu_download_queue.py")
SPEC = importlib.util.spec_from_file_location("meifu_download_queue", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def enqueue_args(root: Path, *, url: str = "https://example.com/file.bin") -> argparse.Namespace:
    return argparse.Namespace(
        queue=root / "queue.json",
        url=url,
        output=str(root / "file.bin"),
        storage_root=None,
        target=None,
        sha256=None,
        priority=100,
    )


class GenericQueueTests(unittest.TestCase):
    def test_enqueue_public_link_persists_a_single_safe_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = enqueue_args(root)
            self.assertEqual(MODULE.enqueue(args), 0)
            queue = MODULE.read_queue(args.queue)
            self.assertEqual(len(queue["entries"]), 1)
            self.assertEqual(queue["entries"][0]["status"], "queued")
            self.assertEqual(queue["entries"][0]["source_url"], args.url)

    def test_signed_or_query_link_is_rejected_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = enqueue_args(Path(temporary), url="https://example.com/file.bin?token=top-secret")
            with self.assertRaises(MODULE.QueueError):
                MODULE.enqueue(args)
            self.assertFalse(args.queue.exists())

    def test_duplicate_output_is_rejected_before_a_worker_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = enqueue_args(root)
            MODULE.enqueue(args)
            with self.assertRaises(MODULE.QueueError):
                MODULE.enqueue(args)

    def test_enqueue_accepts_a_storage_root_and_relative_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = enqueue_args(root)
            args.output = None
            args.storage_root = str(root / "models")
            args.target = "speech/tts/model.bin"
            MODULE.enqueue(args)
            queue = MODULE.read_queue(args.queue)
            self.assertEqual(
                queue["entries"][0]["output"],
                str((root / "models" / "speech" / "tts" / "model.bin").resolve(strict=False)),
            )

    def test_next_entry_respects_network_backoff(self) -> None:
        queue = MODULE.queue_template()
        queue["entries"] = [
            {
                "id": "future",
                "priority": 1,
                "source_url": "https://example.com/future.bin",
                "output": str(Path(tempfile.gettempdir()) / "future.bin"),
                "status": "queued",
                "next_attempt_after": "2999-01-01T00:00:00+00:00",
            }
        ]
        self.assertIsNone(MODULE.next_entry(queue))

    def test_unclean_running_entry_is_requeued_without_touching_transfer_state(self) -> None:
        queue = MODULE.queue_template()
        queue["entries"] = [
            {
                "id": "resume-me",
                "priority": 1,
                "source_url": "https://example.com/resume.bin",
                "output": str(Path(tempfile.gettempdir()) / "resume.bin"),
                "status": "running",
            }
        ]
        recovered = MODULE.recover_interrupted_entries(queue)
        self.assertEqual(recovered, ["resume-me"])
        self.assertEqual(queue["entries"][0]["status"], "queued")

    def test_queue_lock_rejects_second_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            lock = MODULE.create_queue_lock(queue_path, recover_stale=False, phase="launching")
            try:
                with self.assertRaises(MODULE.QueueError):
                    MODULE.create_queue_lock(queue_path, recover_stale=False, phase="launching")
            finally:
                lock.release()

    def test_manifest_lease_rejects_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.json"
            lease = MODULE.create_queue_manifest_lease(queue_path, operation="test")
            try:
                with self.assertRaises(MODULE.QueueError):
                    MODULE.create_queue_manifest_lease(queue_path, operation="test")
            finally:
                lease.release()

    def test_external_process_cannot_enqueue_while_manifest_lease_is_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            lease = MODULE.create_queue_manifest_lease(queue_path, operation="test_owner")
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        str(SCRIPT),
                        "enqueue",
                        "--queue",
                        str(queue_path),
                        "--url",
                        "https://example.com/external.bin",
                        "--output",
                        str(root / "external.bin"),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
            finally:
                lease.release()
            self.assertEqual(result.returncode, 2)
            self.assertFalse(queue_path.exists())

    def test_stale_manifest_snapshot_cannot_overwrite_newer_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.json"
            MODULE.save_queue(queue_path, MODULE.queue_template(), operation="test_setup")
            fresh = MODULE.read_queue(queue_path)
            stale = copy.deepcopy(fresh)

            fresh["state"] = "first_writer"
            with MODULE.create_queue_manifest_lease(queue_path, operation="first_writer") as lease:
                MODULE.save_queue(queue_path, fresh, operation="first_writer", lease=lease)

            stale["state"] = "stale_writer"
            with MODULE.create_queue_manifest_lease(queue_path, operation="stale_writer") as lease:
                with self.assertRaises(MODULE.QueueError):
                    MODULE.save_queue(queue_path, stale, operation="stale_writer", lease=lease)

            current = MODULE.read_queue(queue_path)
            self.assertEqual(current["state"], "first_writer")
            self.assertEqual(current["manifest"]["revision"], 2)

    def test_pre_lease_v1_queue_is_upgraded_on_its_next_safe_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.json"
            legacy = MODULE.queue_template()
            legacy.pop("manifest")
            MODULE.atomic_write_json(queue_path, legacy)
            queue = MODULE.read_queue(queue_path)
            self.assertEqual(MODULE.queue_revision(queue), 0)
            MODULE.save_queue(queue_path, queue, operation="legacy_upgrade")
            upgraded = MODULE.read_queue(queue_path)
            self.assertEqual(upgraded["manifest"]["revision"], 1)
            self.assertEqual(upgraded["manifest"]["write_protocol"], MODULE.MANIFEST_WRITE_PROTOCOL)

    def test_start_only_requests_the_windows_task_not_a_codex_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.json"
            MODULE.save_queue(queue_path, MODULE.queue_template())
            args = argparse.Namespace(
                queue=queue_path,
            )
            calls: list[list[str]] = []

            def fake_schtasks(command: list[str]):
                calls.append(command)
                return MODULE.subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(MODULE, "DEFAULT_QUEUE", queue_path), patch.object(
                MODULE, "run_schtasks", side_effect=fake_schtasks
            ):
                self.assertEqual(MODULE.start(args), 0)
            self.assertEqual(
                calls,
                [
                    ["/Query", "/TN", MODULE.WINDOWS_TASK_FULL_NAME],
                    ["/Run", "/TN", MODULE.WINDOWS_TASK_FULL_NAME],
                ],
            )


if __name__ == "__main__":
    unittest.main()
