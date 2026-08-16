#!/usr/bin/env python3
"""按需访问本机 ComfyUI HTTP API，不启动任何 MCP 或 ComfyUI 进程。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT_SECONDS = 10
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ComfyApiError(RuntimeError):
    """本机 ComfyUI API 无法安全完成请求。"""


def positive_int(value: str) -> int:
    result = int(value)
    if result < 1 or result > 120:
        raise argparse.ArgumentTypeError("超时必须是 1 到 120 秒。")
    return result


def bounded_limit(value: str) -> int:
    result = int(value)
    if result < 1 or result > 200:
        raise argparse.ArgumentTypeError("数量必须是 1 到 200。")
    return result


def normalize_base_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("ComfyUI 地址必须使用 http 或 https。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ComfyUI 地址不能包含凭据、查询参数或片段。")
    if parsed.path not in {"", "/"}:
        raise ValueError("ComfyUI 地址不能包含额外路径。")
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise ValueError("仅允许访问本机回环地址，不连接远程 ComfyUI。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ComfyUI 地址端口无效。") from exc
    host_text = "[::1]" if host == "::1" else host
    port_text = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{host_text}{port_text}"


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """执行一次固定本机 HTTP 请求；绝不启动、重启或重试服务。"""

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ComfyApiError(f"本机 ComfyUI 返回 HTTP {exc.code}：{detail}") from exc
    except URLError as exc:
        raise ComfyApiError(
            f"本机 ComfyUI 不可用：{exc.reason}。不会自动启动、重启或更改服务。"
        ) from exc
    except OSError as exc:
        raise ComfyApiError(
            f"调用本机 ComfyUI 失败：{exc}。不会自动启动、重启或更改服务。"
        ) from exc

    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComfyApiError("本机 ComfyUI 返回了无法解析的 JSON。") from exc


def load_workflow(path_text: str) -> tuple[Path, dict[str, Any], str]:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"工作流文件不存在：{path}")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"无法读取工作流文件：{exc}") from exc
    try:
        workflow = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"工作流不是有效 JSON：第 {exc.lineno} 行，第 {exc.colno} 列。") from exc
    if not isinstance(workflow, dict):
        raise ValueError("工作流顶层必须是 API 格式的对象。")
    return path, workflow, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def inspect_workflow(workflow: Mapping[str, Any], workflow_hash: str) -> dict[str, Any]:
    errors: list[str] = []
    node_types: set[str] = set()
    node_ids = {str(node_id) for node_id in workflow}
    if not workflow:
        errors.append("工作流为空。")

    for node_id, node in workflow.items():
        label = str(node_id)
        if not isinstance(node, Mapping):
            errors.append(f"节点 {label} 不是对象。")
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type.strip():
            errors.append(f"节点 {label} 缺少 class_type。")
        else:
            node_types.add(class_type)
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            errors.append(f"节点 {label} 的 inputs 必须是对象。")
            continue
        for input_name, value in inputs.items():
            if (
                isinstance(value, Sequence)
                and not isinstance(value, (str, bytes))
                and len(value) == 2
                and isinstance(value[0], (str, int))
                and isinstance(value[1], int)
                and str(value[0]) not in node_ids
            ):
                errors.append(
                    f"节点 {label} 的输入 {input_name} 引用了不存在的节点 {value[0]}。"
                )

    return {
        "valid": not errors,
        "node_count": len(workflow),
        "node_types": sorted(node_types),
        "workflow_sha256": workflow_hash,
        "errors": errors,
    }


def require_valid_workflow(path_text: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path, workflow, workflow_hash = load_workflow(path_text)
    report = inspect_workflow(workflow, workflow_hash)
    if not report["valid"]:
        raise ValueError("工作流结构不合格：" + "；".join(report["errors"]))
    return path, workflow, report


def require_yes(args: argparse.Namespace, action: str) -> None:
    if not args.yes:
        raise ValueError(f"{action}会改变本机 ComfyUI 状态；仅在用户当前明确授权后追加 --yes。")


def system_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ComfyApiError("本机 ComfyUI 的 system_stats 响应格式异常。")
    system = payload.get("system") if isinstance(payload.get("system"), Mapping) else {}
    devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    device_rows: list[dict[str, Any]] = []
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        device_rows.append(
            {
                "name": device.get("name"),
                "type": device.get("type"),
                "vram_total": device.get("vram_total"),
                "vram_free": device.get("vram_free"),
            }
        )
    return {
        "ready": True,
        "comfyui_version": system.get("comfyui_version"),
        "python_version": system.get("python_version"),
        "os": system.get("os"),
        "devices": device_rows,
    }


def queue_item_summary(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {
            "number": item.get("number"),
            "prompt_id": item.get("prompt_id"),
        }
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
        return {
            "number": item[0] if len(item) > 0 else None,
            "prompt_id": item[1] if len(item) > 1 else None,
        }
    return {"number": None, "prompt_id": None}


def queue_summary(payload: Any, limit: int = 20) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ComfyApiError("本机 ComfyUI 的 queue 响应格式异常。")
    running = payload.get("queue_running") if isinstance(payload.get("queue_running"), list) else []
    pending = payload.get("queue_pending") if isinstance(payload.get("queue_pending"), list) else []
    return {
        "running_count": len(running),
        "pending_count": len(pending),
        "running": [queue_item_summary(item) for item in running[:limit]],
        "pending": [queue_item_summary(item) for item in pending[:limit]],
    }


def history_item_summary(prompt_id: str, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        return {"prompt_id": prompt_id, "status": None, "output_nodes": []}
    status = entry.get("status") if isinstance(entry.get("status"), Mapping) else {}
    outputs = entry.get("outputs") if isinstance(entry.get("outputs"), Mapping) else {}
    return {
        "prompt_id": prompt_id,
        "status": status.get("status_str") or status.get("status"),
        "completed": status.get("completed"),
        "output_nodes": list(outputs.keys()),
    }


def history_summary(payload: Any, limit: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ComfyApiError("本机 ComfyUI 的 history 响应格式异常。")
    entries = list(payload.items())
    recent = list(reversed(entries[-limit:]))
    return {
        "count": len(entries),
        "items": [history_item_summary(str(prompt_id), entry) for prompt_id, entry in recent],
    }


def nodes_summary(payload: Any, query: str | None, limit: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ComfyApiError("本机 ComfyUI 的 object_info 响应格式异常。")
    needle = (query or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for class_type, info in payload.items():
        info_map = info if isinstance(info, Mapping) else {}
        display_name = str(info_map.get("display_name") or class_type)
        category = info_map.get("category")
        haystack = f"{class_type} {display_name} {category or ''}".lower()
        if needle and needle not in haystack:
            continue
        inputs = info_map.get("input") if isinstance(info_map.get("input"), Mapping) else {}
        matches.append(
            {
                "class_type": class_type,
                "display_name": display_name,
                "category": category,
                "required_inputs": sorted(
                    (inputs.get("required") or {}).keys()
                    if isinstance(inputs.get("required"), Mapping)
                    else []
                ),
            }
        )
    return {"count": len(matches), "items": matches[:limit]}


def models_summary(payload: Any, folder: str | None, query: str | None, limit: int) -> dict[str, Any]:
    if folder is None:
        return {"folders": payload}
    candidates = payload if isinstance(payload, list) else []
    needle = (query or "").strip().lower()
    items = [str(item) for item in candidates if not needle or needle in str(item).lower()]
    return {"folder": folder, "count": len(items), "items": items[:limit]}


def templates_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return {"count": len(payload), "sources": sorted(str(key) for key in payload.keys())}
    return {"count": 0, "sources": []}


def preflight(args: argparse.Namespace) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    path, workflow, report = require_valid_workflow(args.workflow)
    system = system_summary(request_json(args.url, "GET", "/system_stats", timeout=args.timeout))
    object_info = request_json(args.url, "GET", "/object_info", timeout=args.timeout)
    if not isinstance(object_info, Mapping):
        raise ComfyApiError("本机 ComfyUI 的 object_info 响应格式异常。")
    missing_node_types = [
        node_type for node_type in report["node_types"] if node_type not in object_info
    ]
    result = {
        "workflow_path": str(path),
        "workflow": report,
        "system": system,
        "missing_node_types": missing_node_types,
        "ready_to_submit": not missing_node_types,
    }
    return path, workflow, report, result


def run_operation(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation == "health":
        return system_summary(request_json(args.url, "GET", "/system_stats", timeout=args.timeout))
    if args.operation == "status":
        return {
            "system": system_summary(
                request_json(args.url, "GET", "/system_stats", timeout=args.timeout)
            ),
            "queue": queue_summary(
                request_json(args.url, "GET", "/queue", timeout=args.timeout), args.limit
            ),
        }
    if args.operation == "nodes":
        return nodes_summary(
            request_json(args.url, "GET", "/object_info", timeout=args.timeout),
            args.query,
            args.limit,
        )
    if args.operation == "models":
        path = "/models" if args.folder is None else f"/models/{args.folder}"
        return models_summary(
            request_json(args.url, "GET", path, timeout=args.timeout),
            args.folder,
            args.query,
            args.limit,
        )
    if args.operation == "templates":
        return templates_summary(
            request_json(args.url, "GET", "/workflow_templates", timeout=args.timeout)
        )
    if args.operation == "workflow-check":
        _, _, report = require_valid_workflow(args.workflow)
        return report
    if args.operation == "preflight":
        _, _, _, result = preflight(args)
        return result
    if args.operation == "submit":
        require_yes(args, "提交工作流")
        path, workflow, report, result = preflight(args)
        if result["missing_node_types"]:
            raise ValueError(
                "当前 ComfyUI 缺少工作流节点：" + "、".join(result["missing_node_types"])
            )
        body: dict[str, Any] = {"prompt": workflow}
        if args.client_id:
            body["client_id"] = args.client_id
        response = request_json(args.url, "POST", "/prompt", body=body, timeout=args.timeout)
        return {
            "submitted": True,
            "workflow_path": str(path),
            "workflow_sha256": report["workflow_sha256"],
            "response": response,
        }
    if args.operation == "queue":
        return queue_summary(
            request_json(args.url, "GET", "/queue", timeout=args.timeout), args.limit
        )
    if args.operation == "history":
        path = "/history" if not args.prompt_id else f"/history/{args.prompt_id}"
        payload = request_json(args.url, "GET", path, timeout=args.timeout)
        return history_summary(payload, args.limit)
    if args.operation == "cancel-running":
        require_yes(args, "中断正在运行的渲染")
        response = request_json(args.url, "POST", "/interrupt", body={}, timeout=args.timeout)
        return {"interrupted_running_job": True, "response": response}
    if args.operation == "cancel-pending":
        require_yes(args, "取消待处理任务")
        response = request_json(
            args.url,
            "POST",
            "/queue",
            body={"delete": [args.prompt_id]},
            timeout=args.timeout,
        )
        return {"cancelled_pending_prompt_id": args.prompt_id, "response": response}
    if args.operation == "free-vram":
        require_yes(args, "释放显存")
        response = request_json(
            args.url,
            "POST",
            "/free",
            body={"unload_models": True, "free_memory": True},
            timeout=args.timeout,
        )
        return {"freed_vram": True, "response": response}
    raise ValueError(f"不支持的操作：{args.operation}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按需访问本机 ComfyUI HTTP API；不会启动 MCP 或 ComfyUI 服务。"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="仅允许本机回环地址。")
    parser.add_argument("--timeout", type=positive_int, default=DEFAULT_TIMEOUT_SECONDS)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("health")
    status = subparsers.add_parser("status")
    status.add_argument("--limit", type=bounded_limit, default=20)
    nodes = subparsers.add_parser("nodes")
    nodes.add_argument("--query")
    nodes.add_argument("--limit", type=bounded_limit, default=50)
    models = subparsers.add_parser("models")
    models.add_argument("--folder")
    models.add_argument("--query")
    models.add_argument("--limit", type=bounded_limit, default=50)
    subparsers.add_parser("templates")
    workflow_check = subparsers.add_parser("workflow-check")
    workflow_check.add_argument("--workflow", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--workflow", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("--workflow", required=True)
    submit.add_argument("--client-id")
    submit.add_argument("--yes", action="store_true")
    queue = subparsers.add_parser("queue")
    queue.add_argument("--limit", type=bounded_limit, default=20)
    history = subparsers.add_parser("history")
    history.add_argument("--prompt-id")
    history.add_argument("--limit", type=bounded_limit, default=10)
    cancel_running = subparsers.add_parser("cancel-running")
    cancel_running.add_argument("--yes", action="store_true")
    cancel_pending = subparsers.add_parser("cancel-pending")
    cancel_pending.add_argument("--prompt-id", required=True)
    cancel_pending.add_argument("--yes", action="store_true")
    free_vram = subparsers.add_parser("free-vram")
    free_vram.add_argument("--yes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.url = normalize_base_url(args.url)
        result = run_operation(args)
    except (ComfyApiError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
