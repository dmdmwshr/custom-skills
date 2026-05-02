"""Registry CLI for the local Graphiti memory system.

The runtime YAML and .env remain the source of truth for what the Graphiti
service actually runs. This registry records governance metadata used by the
skill and daemon: groups, model profiles, reranker profiles, and policies.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GRAPHITI_MCP_DIR = Path(r"D:\Program_Files\graphiti\mcp_server")
GRAPHITI_VENV_PYTHON = GRAPHITI_MCP_DIR / ".venv" / "Scripts" / "python.exe"
GRAPHITI_CONFIG_PATH = GRAPHITI_MCP_DIR / "config" / "config-docker-neo4j.yaml"
REGISTRY_PATH = GRAPHITI_MCP_DIR / "config" / "memory-registry.yaml"
SUMMARY_PATH = Path(__file__).resolve().parent.parent / "references" / "memory-system-registry.md"

SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(?i)(api[_-]?key|token|cookie|password|secret|private[_-]?key)(\s*[:=]\s*)([^\s,;'\"]+)"),
]


def ensure_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def reexec_with_graphiti_python() -> None:
    current = Path(sys.executable).resolve()
    expected = GRAPHITI_VENV_PYTHON.resolve()
    if current == expected:
        return
    if not GRAPHITI_VENV_PYTHON.exists():
        return
    raise SystemExit(subprocess.call([str(GRAPHITI_VENV_PYTHON), *sys.argv]))


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ("key", "token", "cookie", "password", "secret", "private"))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if is_secret_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in SECRET_PATTERNS:
            if pattern.pattern.startswith("(?i)"):
                text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", text)
            else:
                text = pattern.sub("***REDACTED***", text)
        return text
    return value


def emit(data: Any, fmt: str) -> None:
    safe = redact(data)
    if fmt == "json":
        print(json.dumps(safe, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(safe)


def print_table(data: Any) -> None:
    rows: list[dict[str, Any]]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        rows = [row for row in data["items"] if isinstance(row, dict)]
    elif isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    elif isinstance(data, dict):
        rows = [{"key": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value} for key, value in data.items()]
    else:
        print(data)
        return
    if not rows:
        print("(empty)")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    widths = {col: min(max(len(col), *(len(str(row.get(col, ""))) for row in rows)), 80) for col in columns}
    print(" | ".join(col.ljust(widths[col]) for col in columns))
    print("-+-".join("-" * widths[col] for col in columns))
    for row in rows:
        print(" | ".join(str(row.get(col, ""))[: widths[col]].ljust(widths[col]) for col in columns))


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit(f"PyYAML is required to read registry YAML: {exc}") from exc
    if not path.exists():
        raise SystemExit(f"Registry file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit(f"PyYAML is required to write registry YAML: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(rendered, encoding="utf-8")


def load_runtime_yaml() -> dict[str, Any]:
    try:
        import yaml
    except Exception:
        return {}
    if not GRAPHITI_CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(GRAPHITI_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def group_items(registry: dict[str, Any], *, include_archived: bool = False) -> list[dict[str, Any]]:
    rows = registry.get("groups") or []
    if include_archived:
        return [row for row in rows if isinstance(row, dict)]
    return [row for row in rows if isinstance(row, dict) and row.get("status", "active") == "active"]


def registered_group_ids(registry: dict[str, Any], *, include_archived: bool = True) -> set[str]:
    return {str(row.get("group_id")) for row in group_items(registry, include_archived=include_archived) if row.get("group_id")}


def is_group_allowed(registry: dict[str, Any], group_id: str) -> bool:
    if group_id in registered_group_ids(registry, include_archived=True):
        return True
    prefixes = ((registry.get("group_policy") or {}).get("allow_unregistered_prefixes") or [])
    return any(group_id.startswith(str(prefix)) for prefix in prefixes)


def runtime_summary() -> dict[str, Any]:
    raw = load_runtime_yaml()
    graphiti = raw.get("graphiti") or {}
    llm = raw.get("llm") or {}
    embedder = raw.get("embedder") or {}
    database = raw.get("database") or {}
    entity_types = graphiti.get("entity_types") or []
    edge_types = graphiti.get("edge_types") or []
    return {
        "config_path": str(GRAPHITI_CONFIG_PATH),
        "llm": {
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "api_url": (((llm.get("providers") or {}).get(llm.get("provider")) or {}).get("api_url")),
        },
        "embedder": {
            "provider": embedder.get("provider"),
            "model": embedder.get("model"),
            "dimensions": embedder.get("dimensions"),
            "api_url": (((embedder.get("providers") or {}).get(embedder.get("provider")) or {}).get("api_url")),
        },
        "database": {
            "provider": database.get("provider"),
            "neo4j_uri": ((((database.get("providers") or {}).get("neo4j") or {}).get("uri"))),
        },
        "graphiti": {
            "default_group": graphiti.get("group_id", "main"),
            "entity_types": [item.get("name") for item in entity_types if isinstance(item, dict)],
            "edge_types": [item.get("name") for item in edge_types if isinstance(item, dict)],
        },
    }


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    runtime = runtime_summary()
    policy = registry.get("group_policy") or {}
    current = registry.get("current_profiles") or {}
    return {
        "registry_path": str(REGISTRY_PATH),
        "summary_path": str(SUMMARY_PATH),
        "version": registry.get("version"),
        "updated_at": registry.get("updated_at"),
        "current_profiles": current,
        "runtime": runtime,
        "groups": {
            "active": len(group_items(registry)),
            "total": len(group_items(registry, include_archived=True)),
            "default_search_groups": policy.get("default_search_groups") or [],
            "unknown_group_write": policy.get("unknown_group_write", "deny"),
        },
        "reranker_policy": registry.get("reranker_policy") or {},
        "daemon_policy": registry.get("daemon_policy") or {},
    }


def cmd_list_groups(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    rows = group_items(registry, include_archived=args.include_archived)
    return {
        "message": "groups retrieved",
        "items": [
            {
                "group_id": row.get("group_id"),
                "status": row.get("status", "active"),
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "default": bool(row.get("default", False)),
                "created_at": row.get("created_at", ""),
            }
            for row in rows
        ],
    }


def cmd_add_group(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    group_id = args.group_id.strip()
    if not re.match(r"^(main|project-[a-z0-9][a-z0-9-]*|domain-[a-z0-9][a-z0-9-]*|source-[a-z0-9][a-z0-9-]*|archive-[a-z0-9][a-z0-9-]*|healthcheck-smoke-[a-zA-Z0-9_.-]+)$", group_id):
        raise SystemExit("group_id must be main or start with project-/domain-/source-/archive-/healthcheck-smoke- and use slug characters")
    groups = registry.setdefault("groups", [])
    for row in groups:
        if isinstance(row, dict) and row.get("group_id") == group_id:
            row["status"] = args.status
            row["title"] = args.title or row.get("title") or group_id
            row["description"] = args.description or row.get("description") or ""
            save_yaml(REGISTRY_PATH, registry)
            return {"message": "group updated", "group_id": group_id}
    groups.append(
        {
            "group_id": group_id,
            "status": args.status,
            "title": args.title or group_id,
            "description": args.description,
            "created_at": datetime.now(timezone.utc).date().isoformat(),
        }
    )
    save_yaml(REGISTRY_PATH, registry)
    return {"message": "group added", "group_id": group_id}


def cmd_add_entity_type(args: argparse.Namespace) -> dict[str, Any]:
    return _append_runtime_graphiti_type(
        section_name="entity_types",
        type_name=args.name,
        description=args.description,
        dry_run=args.dry_run,
    )


def cmd_add_edge_type(args: argparse.Namespace) -> dict[str, Any]:
    result = _append_runtime_graphiti_type(
        section_name="edge_types",
        type_name=args.name,
        description=args.description,
        dry_run=args.dry_run,
    )
    result["note"] = "edge_type_map must be edited in runtime YAML if this edge should be limited to source/target entity pairs."
    return result


def _append_runtime_graphiti_type(*, section_name: str, type_name: str, description: str, dry_run: bool) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit(f"PyYAML is required to update runtime YAML: {exc}") from exc
    raw = load_runtime_yaml()
    graphiti = raw.setdefault("graphiti", {})
    rows = graphiti.setdefault(section_name, [])
    if not isinstance(rows, list):
        raise SystemExit(f"graphiti.{section_name} must be a list in {GRAPHITI_CONFIG_PATH}")
    for row in rows:
        if isinstance(row, dict) and row.get("name") == type_name:
            return {"message": f"{section_name} already exists", "name": type_name, "dry_run": dry_run}
    new_row = {"name": type_name, "description": description}
    preview = {"path": str(GRAPHITI_CONFIG_PATH), "section": f"graphiti.{section_name}", "add": new_row}
    if dry_run:
        return {"message": "dry run; runtime YAML not changed", **preview}
    rows.append(new_row)
    rendered = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120)
    backup = GRAPHITI_CONFIG_PATH.with_suffix(f".yaml.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(GRAPHITI_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    GRAPHITI_CONFIG_PATH.write_text(rendered, encoding="utf-8")
    return {"message": f"{section_name} added", "backup": str(backup), **preview}


def cmd_render_summary(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    runtime = runtime_summary()
    groups = group_items(registry, include_archived=True)
    current = registry.get("current_profiles") or {}
    lines = [
        "# 记忆系统情况清单",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Registry：`{REGISTRY_PATH}`",
        f"- Runtime YAML：`{GRAPHITI_CONFIG_PATH}`",
        "",
        "## 当前运行配置",
        "",
        f"- Graphiti LLM：`{runtime.get('llm', {}).get('model')}`（profile: `{current.get('graphiti_llm')}`）",
        f"- Embedding：`{runtime.get('embedder', {}).get('model')}`（profile: `{current.get('graphiti_embedder')}`）",
        f"- Daemon assistant：`{current.get('daemon_assistant')}`",
        f"- Reranker：`{current.get('reranker')}`，启用：`{(registry.get('reranker_policy') or {}).get('enabled')}`",
        f"- 默认 group：`{runtime.get('graphiti', {}).get('default_group')}`",
        "",
        "## Groups",
        "",
    ]
    for row in groups:
        lines.append(f"- `{row.get('group_id')}` [{row.get('status', 'active')}]：{row.get('title', '')}。{row.get('description', '')}")
    lines.extend(
        [
            "",
            "## 实体与关系",
            "",
            "- 实体类型以 runtime YAML 的 `graphiti.entity_types` 为准："
            + "、".join(f"`{name}`" for name in runtime.get("graphiti", {}).get("entity_types", [])),
            "- 关系类型以 runtime YAML 的 `graphiti.edge_types` / `graphiti.edge_type_map` 为准；未配置时使用 Graphiti 默认关系抽取。",
            "",
            "## 使用规则",
            "",
            "- 写入前先选择已登记 group；未知 group 默认禁止直写。",
            "- `main` 只保存全局长期事实；项目、领域、来源类事实应进入对应登记 group。",
            "- `archive-*`、`system`、`deprecated` 默认不参与日常搜索。",
            "- 常驻服务写入后端为 skill CLI，MCP 仅用于兼容状态与排故。",
        ]
    )
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"message": "summary rendered", "path": str(SUMMARY_PATH)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Graphiti memory registry CLI")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_format_arg(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--format", choices=["json", "table"], default=argparse.SUPPRESS)

    status = sub.add_parser("status")
    add_format_arg(status)
    status.set_defaults(func=cmd_status)

    groups = sub.add_parser("list-groups")
    add_format_arg(groups)
    groups.add_argument("--include-archived", action="store_true")
    groups.set_defaults(func=cmd_list_groups)

    add_group = sub.add_parser("add-group")
    add_format_arg(add_group)
    add_group.add_argument("--group-id", required=True)
    add_group.add_argument("--title")
    add_group.add_argument("--description", required=True)
    add_group.add_argument("--status", choices=["active", "paused", "archive", "deprecated"], default="active")
    add_group.set_defaults(func=cmd_add_group)

    entity = sub.add_parser("add-entity-type")
    add_format_arg(entity)
    entity.add_argument("--name", required=True)
    entity.add_argument("--description", required=True)
    entity.add_argument("--dry-run", action="store_true")
    entity.set_defaults(func=cmd_add_entity_type)

    edge = sub.add_parser("add-edge-type")
    add_format_arg(edge)
    edge.add_argument("--name", required=True)
    edge.add_argument("--description", required=True)
    edge.add_argument("--dry-run", action="store_true")
    edge.set_defaults(func=cmd_add_edge_type)

    render = sub.add_parser("render-summary")
    add_format_arg(render)
    render.set_defaults(func=cmd_render_summary)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    if argv is None:
        reexec_with_graphiti_python()
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        emit(result, args.format)
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        emit({"error": str(exc), "type": type(exc).__name__}, args.format)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
