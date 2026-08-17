"""不连接真实服务的个人事实记忆 API 适配器测试。"""

from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlparse

import memory_api


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    def read(self) -> bytes:
        return self.payload


class FakeOpener:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.requests: list[object] = []

    def __call__(self, request: object, *, timeout: int) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self.payloads.pop(0))


class MemoryApiMappingTests(unittest.TestCase):
    def assert_mapping(
        self,
        argv: list[str],
        method: str,
        path: str,
        query: dict[str, list[str]] | None = None,
        body: object | None = None,
    ) -> None:
        spec = memory_api.build_request(memory_api.parse_args(argv))
        self.assertEqual(spec.method, method)
        self.assertEqual(spec.path, path)
        actual_query = parse_qs(
            urlparse(f"http://invalid{spec.path}?{memory_api.urlencode(spec.query or {}, doseq=True)}").query,
            keep_blank_values=True,
        )
        self.assertEqual(query or {}, actual_query)
        self.assertEqual(spec.body, body)

    def test_read_mappings_and_history_filters(self) -> None:
        cases = [
            (["overview"], "GET", "/overview", {}, None),
            (
                ["search", "--query", "消防", "--namespace", "work", "--at", "2026-08-01T00:00:00Z"],
                "GET",
                "/memory/search",
                {"query": ["消防"], "limit": ["20"], "namespace": ["work"], "at": ["2026-08-01T00:00:00Z"]},
                None,
            ),
            (["entity-context", "--entity-id", "entity/1"], "GET", "/memory/entities/entity%2F1", {}, None),
            (["project-context", "--entity-id", "project/1"], "GET", "/memory/projects/project%2F1/context", {}, None),
            (["automation-status"], "GET", "/automation/status", {}, None),
            (["model-settings"], "GET", "/settings/models", {}, None),
        ]
        for argv, method, path, query, body in cases:
            with self.subTest(argv=argv):
                self.assert_mapping(argv, method, path, query, body)

    def test_two_explicit_write_mappings(self) -> None:
        self.assert_mapping(["inventory-scan"], "POST", "/inventory/scan")
        self.assert_mapping(
            ["archive-scan", "--all"],
            "POST",
            "/chat-archives/scan",
            {},
            {"limit": None},
        )

    def test_request_uses_fixed_loopback_api(self) -> None:
        opener = FakeOpener([{"status": "ready"}])
        result = memory_api.request_json(
            memory_api.RequestSpec("readiness", "GET", "/readiness"), opener=opener
        )
        self.assertEqual(result, {"status": "ready"})
        request = opener.requests[0]
        self.assertTrue(request.full_url.startswith(memory_api.API_ROOT))
        self.assertEqual(request.method, "GET")

    def test_preflight_reports_actual_degradation_without_repair(self) -> None:
        opener = FakeOpener(
            [
                {"status": "ready", "version": "3.0.0"},
                {
                    "status": "degraded",
                    "graphiti": {"reachable": False},
                    "models": {"embedding_available": False, "primary_available": False},
                },
            ]
        )
        result = memory_api.preflight(opener=opener)
        self.assertEqual(len(result["notices"]), 3)
        self.assertEqual(len(opener.requests), 2)


if __name__ == "__main__":
    unittest.main()
