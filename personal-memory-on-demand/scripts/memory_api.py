#!/usr/bin/env python3
"""按需访问固定本机个人事实记忆 API。"""

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
DEFAULT_TIMEOUT_SECONDS = 20
ARCHIVE_REBUILD_TIMEOUT_SECONDS = 120


class MemoryApiError(RuntimeError):
    """本机记忆 API 无法安全完成请求。"""


def _configure_utf8_stdio() -> None:
    """让 Windows 控制台与 Codex 调用链稳定输出 UTF-8 中文。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class RequestSpec:
    operation: str
    method: str
    path: str
    query: Mapping[str, Any] | None = None
    body: Mapping[str, Any] | None = None


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "")}


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


def _filters(args: argparse.Namespace) -> dict[str, Any]:
    return _compact(
        {
            "namespace": args.namespace,
            "entity_type": args.entity_type,
            "group_id": args.group_id,
            "at": args.at,
        }
    )


def build_request(args: argparse.Namespace) -> RequestSpec:
    """将受限参数映射为固定本机 API 请求。"""

    operation = args.operation
    if operation in {"readiness", "health", "overview"}:
        return RequestSpec(operation, "GET", f"/{operation}")
    if operation == "search":
        query = {
            "query": _required(args.query, "检索关键词"),
            "limit": _limit(args.limit, 20, 100),
            **_filters(args),
        }
        return RequestSpec(operation, "GET", "/memory/search", query)
    if operation == "list-entities":
        query = {
            "query": args.query,
            "status": args.status,
            "limit": _limit(args.limit, 200, 10_000),
            "offset": args.offset,
            **_filters(args),
        }
        return RequestSpec(operation, "GET", "/entities", _compact(query))
    if operation == "entity-context":
        path = f"/memory/entities/{quote(_required(args.entity_id, '实体 ID'), safe='')}"
        return RequestSpec(operation, "GET", path, _compact({"at": args.at}))
    if operation == "list-projects":
        return RequestSpec(operation, "GET", "/projects")
    if operation == "project-context":
        path = f"/memory/projects/{quote(_required(args.entity_id, '项目实体 ID'), safe='')}/context"
        return RequestSpec(operation, "GET", path, _compact({"at": args.at}))
    if operation == "groups":
        return RequestSpec(operation, "GET", "/groups")
    if operation == "ontology":
        return RequestSpec(operation, "GET", "/ontology", _compact({"kind": args.kind}))
    if operation == "inventory-status":
        return RequestSpec(operation, "GET", "/inventory/runs", {"limit": _limit(args.limit, 30, 100)})
    if operation == "archive-status":
        return RequestSpec(operation, "GET", "/chat-archives/status")
    if operation == "automation-status":
        return RequestSpec(operation, "GET", "/automation/status")
    if operation == "model-settings":
        return RequestSpec(operation, "GET", "/settings/models")
    if operation == "inventory-scan":
        return RequestSpec(operation, "POST", "/inventory/scan")
    if operation == "archive-scan":
        if args.all and args.limit is not None:
            raise ValueError("“--all”与“--limit”不能同时使用。")
        return RequestSpec(
            operation,
            "POST",
            "/chat-archives/scan",
            body={"limit": None if args.all else _limit(args.limit, 10, 5000)},
        )
    if operation == "archive-rebuild":
        if not args.confirm_rebuild:
            raise ValueError("归档事实重建必须显式提供 --confirm-rebuild。")
        return RequestSpec(
            operation,
            "POST",
            "/chat-archives/rebuild",
            body={"confirm": "REBUILD_ARCHIVE_FACTS", "primary_only": True},
        )
    raise ValueError(f"不支持的操作：{operation}")


def request_json(
    spec: RequestSpec,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """执行单次固定回环请求，不启动、重试或修复服务。"""

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


def _health_notices(health: Any) -> list[str]:
    if not isinstance(health, dict):
        return []
    notices: list[str] = []
    graph = health.get("graphiti") or {}
    models = health.get("models") or {}
    if not graph.get("reachable", False):
        notices.append("Graphiti/Neo4j 当前不可达；没有自动重启或伪造结果。")
    if not models.get("embedding_available", False):
        notices.append("固定向量模型当前不可用；没有自动切换模型。")
    if not models.get("primary_available", False):
        notices.append("代理主模型当前不可用；事实查询不受影响，新的归档提取可能使用本地备用。")
    return notices


def preflight(*, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    readiness = request_json(RequestSpec("readiness", "GET", "/readiness"), opener=opener)
    if not isinstance(readiness, dict) or readiness.get("status") != "ready":
        raise MemoryApiError("本机记忆 API 尚未就绪，已停止本次请求。")
    health = request_json(RequestSpec("health", "GET", "/health"), opener=opener)
    return {"readiness": readiness, "health": health, "notices": _health_notices(health)}


def run(args: argparse.Namespace, *, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    spec = build_request(args)
    if args.operation == "readiness":
        return {"operation": args.operation, "result": request_json(spec, opener=opener)}
    if args.operation == "health":
        health = request_json(spec, opener=opener)
        return {"operation": args.operation, "result": health, "notices": _health_notices(health)}
    return {
        "operation": args.operation,
        "preflight": preflight(opener=opener),
        "result": request_json(
            spec,
            opener=opener,
            timeout=(
                ARCHIVE_REBUILD_TIMEOUT_SECONDS
                if args.operation == "archive-rebuild"
                else DEFAULT_TIMEOUT_SECONDS
            ),
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按需访问固定本机个人事实记忆 API。")
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
            "groups",
            "ontology",
            "inventory-status",
            "archive-status",
            "automation-status",
            "model-settings",
            "inventory-scan",
            "archive-scan",
            "archive-rebuild",
        ),
    )
    parser.add_argument("--query", help="检索关键词")
    parser.add_argument("--limit", type=int, help="返回或扫描数量")
    parser.add_argument("--offset", type=int, default=0, help="实体列表偏移量")
    parser.add_argument("--namespace", choices=("personal", "work", "creative"), help="命名空间")
    parser.add_argument("--entity-type", help="实体类型筛选")
    parser.add_argument("--group-id", help="知识分组筛选")
    parser.add_argument("--status", default="active", help="实体状态筛选")
    parser.add_argument("--at", help="ISO 8601 历史时点")
    parser.add_argument("--kind", choices=("entity", "relation"), help="本体类别")
    parser.add_argument("--entity-id", help="实体或项目实体 ID")
    parser.add_argument("--all", action="store_true", help="扫描全部已归档用户消息")
    parser.add_argument(
        "--confirm-rebuild",
        action="store_true",
        help="确认只清除并重建归档用户消息产生的事实",
    )
    args = parser.parse_args(argv)
    if args.offset < 0:
        parser.error("--offset 不能小于 0。")
    if args.all and args.operation != "archive-scan":
        parser.error("--all 仅可用于 archive-scan。")
    if args.confirm_rebuild and args.operation != "archive-rebuild":
        parser.error("--confirm-rebuild 仅可用于 archive-rebuild。")
    return args


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    try:
        output = run(parse_args(argv))
    except (MemoryApiError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
