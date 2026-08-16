"""不连接真实服务的个人记忆 API 适配器测试。"""

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
        self.assertEqual(
            {key: [str(item) for item in value] for key, value in (query or {}).items()},
            {
                key: [str(item) for item in value]
                for key, value in parse_qs(
                    urlparse(
                        f"http://invalid{spec.path}?{memory_api.urlencode(spec.query or {}, doseq=True)}"
                    ).query,
                    keep_blank_values=True,
                ).items()
            },
        )
        self.assertEqual(spec.body, body)

    def test_all_read_mappings(self) -> None:
        cases = [
            (["readiness"], "GET", "/readiness", {}, None),
            (["health"], "GET", "/health", {}, None),
            (["overview"], "GET", "/overview", {}, None),
            (["search", "--query", "消防"], "GET", "/memory/search", {"query": ["消防"], "limit": ["20"]}, None),
            (["list-entities"], "GET", "/entities", {"status": ["active"], "limit": ["200"], "offset": ["0"]}, None),
            (["entity-context", "--entity-id", "entity/1"], "GET", "/entities/entity%2F1", {}, None),
            (["list-projects"], "GET", "/projects", {}, None),
            (["project-context", "--entity-id", "project/1"], "GET", "/projects/project%2F1/context", {}, None),
            (["inventory-status"], "GET", "/inventory/runs", {"limit": ["30"]}, None),
            (["archive-status"], "GET", "/chat-archives/status", {}, None),
            (["code-graph-status"], "GET", "/code-graph/status", {}, None),
            (
                [
                    "code-graph-search",
                    "--project-entity-id",
                    "project-1",
                    "--query-type",
                    "架构",
                ],
                "GET",
                "/code-graph/search",
                {
                    "project_entity_id": ["project-1"],
                    "query_type": ["架构"],
                    "query": [""],
                    "limit": ["10"],
                },
                None,
            ),
        ]
        for argv, method, path, query, body in cases:
            with self.subTest(argv=argv):
                self.assert_mapping(argv, method, path, query, body)

    def test_two_explicit_write_mappings(self) -> None:
        self.assert_mapping(
            ["inventory-scan", "--no-embeddings"],
            "POST",
            "/inventory/scan",
            {},
            {"refresh_embeddings": False, "sync_graph": True},
        )
        self.assert_mapping(
            ["archive-scan", "--all", "--no-graph"],
            "POST",
            "/chat-archives/scan",
            {},
            {"limit": None, "refresh_embeddings": True, "sync_graph": False},
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

    def test_preflight_reports_embedding_gap_without_repair(self) -> None:
        opener = FakeOpener(
            [
                {"status": "ready", "version": "2.0.0"},
                {"status": "healthy", "ollama": {"embedding_model_ready": False}},
            ]
        )
        result = memory_api.preflight(opener=opener)
        self.assertIn("本地向量模型未就绪", result["notice"])
        self.assertEqual(len(opener.requests), 2)


if __name__ == "__main__":
    unittest.main()
