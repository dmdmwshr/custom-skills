#!/usr/bin/env python3
"""Small offline checks for the generic downloader's request and state safety."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("download_via_meifu.py")
SPEC = importlib.util.spec_from_file_location("download_via_meifu", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DownloadRequestTests(unittest.TestCase):
    def test_remote_cache_parent_is_posix(self) -> None:
        self.assertEqual(MODULE.remote_cache_parent(), "/root/.cache")

    def test_rejects_relative_output(self) -> None:
        with self.assertRaises(MODULE.DownloadError):
            MODULE.validate_request("https://example.com/file.bin", "file.bin", None, allow_http=False)

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


if __name__ == "__main__":
    unittest.main()
