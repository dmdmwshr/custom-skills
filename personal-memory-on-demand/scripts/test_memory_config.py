"""Offline platform/configuration tests; never connect to the real service."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_api
import memory_config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Path(self.temporary.name) / "client.json"
        home = patch.object(memory_config.Path, "home", return_value=Path(self.temporary.name))
        home.start()
        self.addCleanup(home.stop)
        self.config.write_text(json.dumps({"api_url": "http://configured:5175", "source_id": "device-one"}), encoding="utf-8")

    def test_url_priority_and_https(self):
        with patch.dict(os.environ, {"MEMORY_API_URL": "https://env.example/memory"}, clear=True):
            self.assertEqual(memory_config.resolve("https://explicit.example/", str(self.config))["api_url"], "https://explicit.example/api")
            self.assertEqual(memory_config.resolve(None, str(self.config))["api_url"], "https://env.example/memory/api")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(memory_config.resolve(None, str(self.config))["api_url"], "http://configured:5175/api")

    def test_platform_defaults_and_no_gateway_fallback(self):
        self.config.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch.object(memory_config, "execution_platform", return_value="wsl"), patch.object(memory_config, "windows_host", return_value="172.20.0.1"):
            self.assertEqual(memory_config.resolve(None, str(self.config))["api_url"], "http://172.20.0.1:5175/api")
        with patch.dict(os.environ, {}, clear=True), patch.object(memory_config, "execution_platform", return_value="linux"), patch.object(memory_config, "windows_host", side_effect=AssertionError):
            value = memory_config.resolve(None, str(self.config))
            self.assertIsNone(value["source_id"])
            with self.assertRaises(ValueError):
                memory_config.select_source(None, value, mutation=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(memory_config, "execution_platform", return_value="wsl"), patch.object(memory_config, "windows_host", side_effect=ValueError("unavailable")):
            with self.assertRaises(ValueError):
                memory_config.resolve(None, str(self.config))

    def test_management_targets_current_device_and_keeps_queries_read_only(self):
        value = {"source_id": "wsl-fixture"}
        self.assertEqual(memory_config.select_source(None, value, mutation=True), "wsl-fixture")
        self.assertIsNone(memory_config.select_source(None, value))
        with self.assertRaises(ValueError):
            memory_config.select_source("all", value, mutation=True)
        spec = memory_api.build_request(memory_api.parse_args(["inventory-scan", "--source", "wsl-fixture", "--request-id", "known-request"]))
        self.assertEqual(spec.query, {"source_id": "wsl-fixture", "request_id": "known-request"})
        query = memory_api.build_request(memory_api.parse_args(["list-entities", "--source", "wsl-fixture"]))
        self.assertEqual(query.method, "GET")
        self.assertEqual(query.query["source_id"], "wsl-fixture")
        for operation in ("entity-context", "project-context"):
            detail = memory_api.build_request(memory_api.parse_args([operation, "--entity-id", "fixture", "--source", "wsl-fixture"]))
            self.assertEqual(detail.query["source_id"], "wsl-fixture")

    def test_remote_windows_service_requires_callers_source_identity(self):
        self.config.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch.object(memory_config, "execution_platform", return_value="windows"):
            local = memory_config.resolve("http://127.0.0.1:5175", str(self.config))
            self.assertEqual(memory_config.select_source(None, local, mutation=True), "windows-local")
            for url in ("http://192.168.1.2:5175", "https://memory.example"):
                remote = memory_config.resolve(url, str(self.config))
                self.assertIsNone(memory_config.select_source(None, remote))
                with self.assertRaises(ValueError):
                    memory_config.select_source(None, remote, mutation=True)

    def test_explicit_platform_mismatch_and_credentials_are_rejected(self):
        self.config.write_text(json.dumps({"platform": "wsl"}), encoding="utf-8")
        with patch.object(memory_config, "execution_platform", return_value="windows"):
            with self.assertRaises(ValueError):
                memory_config.resolve("http://localhost:5175", str(self.config))
        self.config.write_text("{}", encoding="utf-8")
        for url in ["http://user:secret@host/", "ftp://host/", "http://host/?token=value"]:
            with self.assertRaises(ValueError):
                memory_config.resolve(url, str(self.config))


if __name__ == "__main__":
    unittest.main()
