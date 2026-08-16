#!/usr/bin/env python3
"""按需访问本机个人事实资产记忆系统，不启动任何 MCP 服务。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "http://127.0.0.1:8788/api"
DEFAULT_TIMEOUT_SECONDS = 15


class MemoryApiError(RuntimeError):
    """本机记忆 API 无法安全完成请求。"""


@dataclass(frozen=True)
class RequestSpec:
    operation: str
    method: str
    path: str
    query: Mapping[str, Any] | None = None
    body: Mapping[str, Any] | None = None


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _required(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{label}不能为空。")
    return cleaned


def _limit(value: int | None, default: int, maximum: int) -> int:
    result = default if value is None else value
    if not 1 <= result <= maximum:
        raise ValueError(f"返回数量必须在 1 到 {maximum} 之间。")
    return result


def build_request(args: argparse.Namespace) -> RequestSpec:
    """将受限的命令行参数映射为固定本机 API 请求。"""

    operation = args.operation
    if operation == "readiness":
        return RequestSpec(operation, "GET", "/readiness")
    if operation == "health":
        return RequestSpec(operation, "GET", "/health")
    if operation == "overview":
        return RequestSpec(operation, "GET", "/overview")
    if operation == "search":
        return RequestSpec(
            operation,
            "GET",
            "/memory/search",
            {"query": _required(args.query, "检索关键词"), "limit": _limit(args.limit, 20, 50)},
        )
    if operation == "list-entities":
        return RequestSpec(
            operation,
            "GET",
            "/entities",
            _compact(
                {
                    "entity_type": args.entity_type,
                    "group_id": args.group_id,
                    "query": args.query,
                    "status": args.status,
                    "limit": _limit(args.limit, 200, 1000),
                    "offset": args.offset,
                }
            ),
        )
    if operation == "entity-context":
        return RequestSpec(
            operation,
            "GET",
            f"/entities/{quote(_required(args.entity_id, '实体 ID'), safe='')}",
        )
    if operation == "list-projects":
        return RequestSpec(operation, "GET", "/projects")
    if operation == "project-context":
        return RequestSpec(
            operation,
            "GET",
            f"/projects/{quote(_required(args.entity_id, '项目实体 ID'), safe='')}/context",
        )
    if operation == "inventory-status":
        return RequestSpec(
            operation, "GET", "/inventory/runs", {"limit": _limit(args.limit, 30, 100)}
        )
    if operation == "archive-status":
        return RequestSpec(operation, "GET", "/chat-archives/status")
    if operation == "code-graph-status":
        return RequestSpec(operation, "GET", "/code-graph/status")
    if operation == "code-graph-search":
        project_entity_id = _required(args.project_entity_id, "项目实体 ID")
        query_type = _required(args.query_type, "图谱检索类型")
        return RequestSpec(
            operation,
            "GET",
            "/code-graph/search",
            {
                "project_entity_id": project_entity_id,
                "query_type": query_type,
                "query": args.query or "",
                "limit": _limit(args.limit, 10, 50),
            },
        )
    if operation == "inventory-scan":
        return RequestSpec(
            operation,
            "POST",
            "/inventory/scan",
            body={
                "refresh_embeddings": not args.no_embeddings,
                "sync_graph": not args.no_graph,
            },
        )
    if operation == "archive-scan":
        if args.all and args.limit is not None:
            raise ValueError("“--all”与“--limit”不能同时使用。")
        return RequestSpec(
            operation,
            "POST",
            "/chat-archives/scan",
            body={
                "limit": None if args.all else _limit(args.limit, 10, 1000),
                "refresh_embeddings": not args.no_embeddings,
                "sync_graph": not args.no_graph,
            },
        )
    raise ValueError(f"不支持的操作：{operation}")


def request_json(
    spec: RequestSpec,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """执行单次固定回环 HTTP 请求，不启动、重试或修复任何服务。"""

    encoded_query = urlencode(spec.query or {}, doseq=True)
    url = f"{API_ROOT}{spec.path}"
    if encoded_query:
        url = f"{url}?{encoded_query}"

    data = None
    headers = {"Accept": "application/json"}
    if spec.body is not None:
        data = json.dumps(spec.body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, headers=headers, method=spec.method)
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise MemoryApiError(f"本机记忆 API 返回 HTTP {exc.code}：{detail}") from exc
    except URLError as exc:
        raise MemoryApiError(f"无法连接本机记忆 API：{exc.reason}") from exc
    except OSError as exc:
        raise MemoryApiError(f"调用本机记忆 API 失败：{exc}") from exc

    try:
        return json.loads(payload.decode("utf-8")) if payload else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryApiError("本机记忆 API 返回了无法解析的响应。") from exc


def _model_notice(health: Any) -> str | None:
    if not isinstance(health, dict):
        return None
    ollama = health.get("ollama")
    if isinstance(ollama, dict) and not ollama.get("embedding_model_ready", False):
        return "本地向量模型未就绪；本次结果仅反映服务实际返回，未自动修复或切换模型。"
    return None


def preflight(*, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """先检查进程就绪和完整健康状态，保留降级信息但不擅自修复。"""

    readiness = request_json(RequestSpec("readiness", "GET", "/readiness"), opener=opener)
    if not isinstance(readiness, dict) or readiness.get("status") != "ready":
        raise MemoryApiError("本机记忆 API 尚未就绪，已停止本次请求。")
    health = request_json(RequestSpec("health", "GET", "/health"), opener=opener)
    result: dict[str, Any] = {"readiness": readiness, "health": health}
    notice = _model_notice(health)
    if notice:
        result["notice"] = notice
    return result


def run(args: argparse.Namespace, *, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """执行一个按需操作；除状态端点外先做预检。"""

    spec = build_request(args)
    if args.operation == "readiness":
        return {"operation": args.operation, "result": request_json(spec, opener=opener)}
    if args.operation == "health":
        health = request_json(spec, opener=opener)
        result: dict[str, Any] = {"operation": args.operation, "result": health}
        notice = _model_notice(health)
        if notice:
            result["notice"] = notice
        return result

    result = {
        "operation": args.operation,
        "preflight": preflight(opener=opener),
        "result": request_json(spec, opener=opener),
    }
    if args.operation == "code-graph-search":
        graph = result["result"]
        if isinstance(graph, dict) and graph.get("status") == "not_indexed":
            result["notice"] = "代码图谱尚未建图；没有调用模型猜测代码结构。"
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按需访问固定本机个人记忆 API；普通查询不会写入记忆。"
    )
    parser.add_argument(
        "operation",
        choices=(
            "readiness",
            "health",
            "overview",
            "search",
            "list-entities",
            "entity-context",
            "list-projects",
            "project-context",
            "inventory-status",
            "archive-status",
            "code-graph-status",
            "code-graph-search",
            "inventory-scan",
            "archive-scan",
        ),
    )
    parser.add_argument("--query", help="检索关键词或图谱检索词")
    parser.add_argument("--limit", type=int, help="返回或扫描数量")
    parser.add_argument("--offset", type=int, default=0, help="实体列表偏移量")
    parser.add_argument("--entity-type", help="实体类型筛选")
    parser.add_argument("--group-id", help="分组筛选")
    parser.add_argument("--status", default="active", help="实体状态筛选")
    parser.add_argument("--entity-id", help="实体或项目实体 ID")
    parser.add_argument("--project-entity-id", help="代码图谱所属项目实体 ID")
    parser.add_argument("--query-type", help="代码图谱检索类型，例如符号、调用者、被调用者、影响范围或架构")
    parser.add_argument("--all", action="store_true", help="仅归档扫描：扫描全部已归档会话")
    parser.add_argument("--no-embeddings", action="store_true", help="写入时不刷新嵌入")
    parser.add_argument("--no-graph", action="store_true", help="写入时不同步图投影")
    args = parser.parse_args(argv)
    if args.offset < 0:
        parser.error("--offset 不能小于 0。")
    if args.all and args.operation != "archive-scan":
        parser.error("--all 仅可用于 archive-scan。")
    if (args.no_embeddings or args.no_graph) and args.operation not in {
        "inventory-scan",
        "archive-scan",
    }:
        parser.error("--no-embeddings 和 --no-graph 仅可用于写入操作。")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        output = run(parse_args(argv))
    except (MemoryApiError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
