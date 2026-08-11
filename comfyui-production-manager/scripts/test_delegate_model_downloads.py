#!/usr/bin/env python3
"""Offline checks for the ComfyUI-to-generic-Meifu queue adapter.

The tests only write temporary JSON files and invoke the generic queue's local
``enqueue`` command.  They never start a worker, Scheduled Task, SSH, SFTP, or
remote transfer.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("delegate_model_downloads.py")
SPEC = importlib.util.spec_from_file_location("delegate_model_downloads", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_report(path: Path, source_url: str = "https://example.com/voice.safetensors") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "ComfyUIModelDependencyReportV1",
                "download_candidates": [
                    {
                        "filename": "voice.safetensors",
                        "category": "checkpoints",
                        "source_url": source_url,
                        "workflow_count": 2,
                        "reference_count": 3,
                        "workflows": ["voice.json"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def prepare_args(root: Path) -> argparse.Namespace:
    script_root = Path(__file__).resolve().parents[2] / "meifu-resumable-download" / "scripts"
    return argparse.Namespace(
        dependency_report=root / "report.json",
        manifest=root / "manifest.json",
        catalog=root / "catalog.json",
        generic_queue=root / "generic-queue.json",
        generic_queue_script=script_root / "meifu_download_queue.py",
        python=Path(sys.executable),
        model_root=root / "models",
        shared_paths_config=MODULE.DEFAULT_SHARED_PATHS,
        primary_root=None,
        fallback_root=None,
        primary_threshold_gib=200,
        priority=100,
        limit=None,
        start=False,
    )


class DelegateModelDownloadTests(unittest.TestCase):
    def test_prepare_delegates_safe_candidate_without_starting_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = prepare_args(root)
            write_report(args.dependency_report)
            self.assertEqual(MODULE.prepare(args), 0)
            queue = json.loads(args.generic_queue.read_text(encoding="utf-8"))
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            self.assertEqual(len(queue["entries"]), 1)
            self.assertEqual(queue["entries"][0]["status"], "queued")
            self.assertEqual(
                queue["entries"][0]["output"],
                str((args.model_root / "checkpoints" / "voice.safetensors").resolve(strict=False)),
            )
            self.assertEqual(manifest["entries"][0]["status"], "queued")
            self.assertFalse((args.model_root / "checkpoints" / "voice.safetensors").exists())

    def test_prepare_reuses_existing_generic_entry_instead_of_duplicating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = prepare_args(root)
            write_report(args.dependency_report)
            MODULE.prepare(args)
            MODULE.prepare(args)
            queue = json.loads(args.generic_queue.read_text(encoding="utf-8"))
            self.assertEqual(len(queue["entries"]), 1)

    def test_signed_candidate_is_excluded_before_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = prepare_args(root)
            write_report(args.dependency_report, "https://example.com/voice.safetensors?signature=secret")
            self.assertEqual(MODULE.prepare(args), 0)
            self.assertFalse(args.generic_queue.exists())
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"], [])

    def test_reconcile_registers_only_an_existing_file_with_its_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = prepare_args(root)
            write_report(args.dependency_report)
            MODULE.prepare(args)
            target = args.model_root / "checkpoints" / "voice.safetensors"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"offline-model-content")
            self.assertEqual(MODULE.reconcile(args), 0)
            catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            self.assertEqual(catalog["schema"], MODULE.CATALOG_SCHEMA)
            self.assertEqual(catalog["models"][0]["absolute_path"], str(target))
            self.assertEqual(catalog["models"][0]["transport"], "meifu_resumable_download_queue")
            self.assertEqual(manifest["entries"][0]["registration_status"], "registered")


if __name__ == "__main__":
    unittest.main()
