"""Stable local Graphiti CLI for the graphiti-memory skill.

This CLI intentionally bypasses the MCP session layer for daily memory work.
It reads the local Graphiti MCP project's .env and YAML config, then calls
graphiti-core directly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GRAPHITI_MCP_DIR = Path(r"D:\Program_Files\graphiti\mcp_server")
GRAPHITI_VENV_PYTHON = GRAPHITI_MCP_DIR / ".venv" / "Scripts" / "python.exe"
GRAPHITI_CONFIG_PATH = GRAPHITI_MCP_DIR / "config" / "config-docker-neo4j.yaml"
GRAPHITI_ENV_PATH = GRAPHITI_MCP_DIR / ".env"
REGISTRY_PATH = GRAPHITI_MCP_DIR / "config" / "memory-registry.yaml"

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


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ("key", "token", "cookie", "password", "secret", "private"))


def emit(data: Any, fmt: str = "json") -> None:
    safe = redact(data)
    if fmt == "json":
        print(json.dumps(safe, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(safe)


def print_table(data: Any) -> None:
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        rows = data["items"]
    elif isinstance(data, dict):
        rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v} for k, v in data.items()]
    elif isinstance(data, list):
        rows = data
    else:
        print(data)
        return

    if not rows:
        print("(empty)")
        return
    if not all(isinstance(row, dict) for row in rows):
        for row in rows:
            print(row)
        return

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    widths = {col: min(max(len(str(col)), *(len(str(row.get(col, ""))) for row in rows)), 80) for col in columns}
    print(" | ".join(col.ljust(widths[col]) for col in columns))
    print("-+-".join("-" * widths[col] for col in columns))
    for row in rows:
        print(" | ".join(str(row.get(col, ""))[: widths[col]].ljust(widths[col]) for col in columns))


def safe_model_dump(model: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    data = model.model_dump(exclude=exclude or set())
    attributes = data.get("attributes")
    if isinstance(attributes, dict):
        attributes.pop("name_embedding", None)
        attributes.pop("fact_embedding", None)
    return data


def format_node_result_safe(node: Any) -> dict[str, Any]:
    return safe_model_dump(node, exclude={"name_embedding"})


def format_fact_result_safe(edge: Any) -> dict[str, Any]:
    return safe_model_dump(edge, exclude={"fact_embedding"})


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def configure_environment() -> None:
    load_dotenv_file(GRAPHITI_ENV_PATH)
    os.environ["CONFIG_PATH"] = str(GRAPHITI_CONFIG_PATH)
    uri = os.environ.get("NEO4J_URI", "")
    if uri in ("bolt://neo4j:7687", "neo4j://neo4j:7687"):
        os.environ["NEO4J_URI"] = uri.replace("://neo4j:", "://127.0.0.1:")
    embed_url = os.environ.get("OLLAMA_OPENAI_API_URL", "")
    if "host.docker.internal" in embed_url:
        os.environ["OLLAMA_OPENAI_API_URL"] = embed_url.replace("host.docker.internal", "127.0.0.1")
    os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")


def import_graphiti_modules():
    sys.path.insert(0, str(GRAPHITI_MCP_DIR))
    sys.path.insert(0, str(GRAPHITI_MCP_DIR / "src"))
    from graphiti_core import Graphiti
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EpisodeType, EpisodicNode
    from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF
    from graphiti_core.search.search_filters import SearchFilters
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data
    from pydantic import BaseModel, Field, create_model
    from src.config.schema import GraphitiConfig
    from src.services.factories import DatabaseDriverFactory, EmbedderFactory, LLMClientFactory

    return {
        "Graphiti": Graphiti,
        "EntityEdge": EntityEdge,
        "EpisodeType": EpisodeType,
        "EpisodicNode": EpisodicNode,
        "NODE_HYBRID_SEARCH_RRF": NODE_HYBRID_SEARCH_RRF,
        "SearchFilters": SearchFilters,
        "clear_data": clear_data,
        "BaseModel": BaseModel,
        "Field": Field,
        "create_model": create_model,
        "GraphitiConfig": GraphitiConfig,
        "DatabaseDriverFactory": DatabaseDriverFactory,
        "EmbedderFactory": EmbedderFactory,
        "LLMClientFactory": LLMClientFactory,
    }


def build_entity_types(cfg: Any, modules: dict[str, Any]) -> dict[str, type[Any]] | None:
    entity_configs = getattr(cfg.graphiti, "entity_types", []) or []
    if not entity_configs:
        return None
    base_model = modules["BaseModel"]
    field = modules["Field"]
    create_model = modules["create_model"]
    entity_types: dict[str, type[Any]] = {}
    for item in entity_configs:
        name = item.name
        description = item.description
        entity_types[name] = create_model(
            name,
            __base__=base_model,
            __doc__=description,
            description=(str, field(default="", description="Only use information mentioned in the context.")),
        )
    return entity_types


def raw_yaml_graphiti_section() -> dict[str, Any]:
    try:
        import yaml
    except Exception:
        return {}
    if not GRAPHITI_CONFIG_PATH.exists():
        return {}
    raw = yaml.safe_load(GRAPHITI_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("graphiti") or {}


def load_registry() -> dict[str, Any]:
    try:
        import yaml
    except Exception:
        return {}
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def registered_group_ids(registry: dict[str, Any], *, include_archived: bool = True) -> set[str]:
    rows = registry.get("groups") or []
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if include_archived or row.get("status", "active") == "active":
            group_id = row.get("group_id")
            if group_id:
                ids.add(str(group_id))
    return ids


def is_group_registered_or_allowed(registry: dict[str, Any], group_id: str) -> bool:
    if not registry:
        return True
    if group_id in registered_group_ids(registry, include_archived=True):
        return True
    prefixes = (registry.get("group_policy") or {}).get("allow_unregistered_prefixes") or []
    return any(group_id.startswith(str(prefix)) for prefix in prefixes)


def validate_group_ids(groups: list[str], registry: dict[str, Any], *, allow_unregistered: bool = False) -> None:
    if allow_unregistered or not registry:
        return
    policy = (registry.get("group_policy") or {}).get("unknown_group_write", "deny")
    unknown = [group for group in groups if not is_group_registered_or_allowed(registry, group)]
    if unknown and policy == "deny":
        known = sorted(registered_group_ids(registry, include_archived=True))
        raise SystemExit(
            "Unregistered group_id is not allowed by memory registry: "
            f"{', '.join(unknown)}. Register it with memory_registry.py add-group or rerun with "
            "--allow-unregistered-group for an explicit one-off override. Known groups: "
            f"{', '.join(known)}"
        )


def registry_status_summary(registry: dict[str, Any]) -> dict[str, Any]:
    if not registry:
        return {"path": str(REGISTRY_PATH), "loaded": False}
    return {
        "path": str(REGISTRY_PATH),
        "loaded": True,
        "updated_at": registry.get("updated_at"),
        "current_profiles": registry.get("current_profiles") or {},
        "groups": sorted(registered_group_ids(registry, include_archived=True)),
        "group_policy": registry.get("group_policy") or {},
        "reranker_policy": registry.get("reranker_policy") or {},
    }


def build_edge_types(modules: dict[str, Any]) -> tuple[dict[str, type[Any]] | None, dict[tuple[str, str], list[str]] | None]:
    section = raw_yaml_graphiti_section()
    edge_configs = section.get("edge_types") or []
    map_configs = section.get("edge_type_map") or []
    if not edge_configs and not map_configs:
        return None, None

    base_model = modules["BaseModel"]
    field = modules["Field"]
    create_model = modules["create_model"]
    edge_types: dict[str, type[Any]] = {}
    for item in edge_configs:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError("graphiti.edge_types entries must include name")
        name = str(item["name"])
        description = str(item.get("description") or "")
        edge_types[name] = create_model(
            name,
            __base__=base_model,
            __doc__=description,
            fact=(str, field(default="", description=description or "Relationship fact.")),
        )

    edge_type_map: dict[tuple[str, str], list[str]] = {}
    for item in map_configs:
        if not isinstance(item, dict):
            raise ValueError("graphiti.edge_type_map entries must be mappings")
        source = item.get("source") or item.get("source_type")
        target = item.get("target") or item.get("target_type")
        types = item.get("edge_types") or item.get("types")
        if not source or not target or not isinstance(types, list):
            raise ValueError("edge_type_map entries require source, target, and edge_types")
        missing = [edge_name for edge_name in types if edge_name not in edge_types]
        if missing:
            raise ValueError(f"edge_type_map references undefined edge_types: {missing}")
        edge_type_map[(str(source), str(target))] = [str(edge_name) for edge_name in types]

    return edge_types or None, edge_type_map or None


class GraphitiRuntime:
    def __init__(self) -> None:
        configure_environment()
        self.modules = import_graphiti_modules()
        self.cfg = self.modules["GraphitiConfig"]()
        self.client = None
        self.entity_types = build_entity_types(self.cfg, self.modules)
        self.edge_types, self.edge_type_map = build_edge_types(self.modules)
        self._startup_tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> "GraphitiRuntime":
        llm_client = self.modules["LLMClientFactory"].create(self.cfg.llm)
        embedder = self.modules["EmbedderFactory"].create(self.cfg.embedder)
        db = self.modules["DatabaseDriverFactory"].create_config(self.cfg.database)
        graphiti_cls = self.modules["Graphiti"]
        if self.cfg.database.provider.lower() != "neo4j":
            raise ValueError("This stable skill CLI currently supports the local Neo4j Graphiti deployment.")
        self.client = graphiti_cls(
            uri=db["uri"],
            user=db["user"],
            password=db["password"],
            llm_client=llm_client,
            embedder=embedder,
            max_coroutines=int(os.environ.get("SEMAPHORE_LIMIT", "4") or "4"),
        )
        await self._wait_for_startup_tasks()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.client is None:
            return
        await self._wait_for_startup_tasks()
        close = getattr(self.client, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result

    @property
    def group_id(self) -> str:
        return self.cfg.graphiti.group_id or "main"

    async def _wait_for_startup_tasks(self) -> None:
        pending = []
        current = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is current or task.done() or task in self._startup_tasks:
                continue
            coro_name = getattr(task.get_coro(), "__qualname__", "")
            if "build_indices_and_constraints" in coro_name:
                pending.append(task)
                self._startup_tasks.add(task)
        if pending:
            await asyncio.gather(*pending)


def parse_group_ids(group_id: str | None, group_ids: str | None, default: str) -> list[str]:
    if group_ids:
        return [part.strip() for part in group_ids.split(",") if part.strip()]
    if group_id:
        return [group_id]
    return [default]


def episode_type(value: str, modules: dict[str, Any]) -> Any:
    enum = modules["EpisodeType"]
    try:
        return enum[value.lower()]
    except Exception:
        return enum.text


def require_yes(args: argparse.Namespace, action: str) -> None:
    if not getattr(args, "yes", False):
        raise SystemExit(f"{action} is destructive. Re-run with --yes if you are certain.")


def is_transient_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in ("429", "500", "502", "503", "504", "timeout", "temporarily", "rate limit"))


async def retry_async(label: str, attempts: int, func):
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await func()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not is_transient_error(exc):
                raise
            await asyncio.sleep(min(2 ** attempt, 10))
    assert last_exc is not None
    raise last_exc


def item_text_for_rerank(item: dict[str, Any]) -> str:
    candidates = [
        item.get("fact"),
        item.get("name"),
        item.get("summary"),
        item.get("description"),
        item.get("content"),
    ]
    attributes = item.get("attributes")
    if isinstance(attributes, dict):
        candidates.extend([attributes.get("fact"), attributes.get("name"), attributes.get("summary")])
    text = "\n".join(str(value) for value in candidates if value)
    if text:
        return text
    return json.dumps(item, ensure_ascii=False, default=str)


def heuristic_rerank(query: str, items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    query_terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
    ranked = []
    for index, item in enumerate(items):
        text = item_text_for_rerank(item).lower()
        score = 0.0
        for term in query_terms:
            if term in text:
                score += 1.0
        score += max(0.0, 1.0 - index / max(len(items), 1)) * 0.05
        copy = dict(item)
        copy["rerank_score"] = round(score, 4)
        copy["rerank_method"] = "heuristic"
        ranked.append((score, -index, copy))
    ranked.sort(reverse=True, key=lambda row: (row[0], row[1]))
    return [row[2] for row in ranked[:limit]]


def ollama_embedding_rerank(query: str, items: list[dict[str, Any]], profile: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    api_url = str(profile.get("api_url") or "http://127.0.0.1:11434").rstrip("/")
    model = str(profile.get("model") or "")
    if not model:
        raise RuntimeError("reranker profile does not define model")
    texts = [query, *[item_text_for_rerank(item)[:4000] for item in items]]
    body = json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}/api/embed",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ollama embedding reranker failed: {redact(str(exc))}") from exc

    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise RuntimeError(f"ollama embedding reranker returned invalid embeddings: {redact(str(payload)[:500])}")
    query_embedding = _coerce_embedding(embeddings[0])
    ranked = []
    for index, (item, embedding) in enumerate(zip(items, embeddings[1:])):
        score = _cosine_similarity(query_embedding, _coerce_embedding(embedding))
        copy = dict(item)
        copy["rerank_score"] = round(score, 6)
        copy["rerank_method"] = f"ollama-embedding:{model}"
        ranked.append((copy["rerank_score"], -index, copy))
    ranked.sort(reverse=True, key=lambda row: (row[0], row[1]))
    return [row[2] for row in ranked[:limit]]


def _coerce_embedding(value: Any) -> list[float]:
    if not isinstance(value, list):
        raise RuntimeError("embedding value is not a list")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("embedding value contains non-numeric items") from exc


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise RuntimeError("embedding dimensions do not match")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def apply_rerank(query: str, items: list[dict[str, Any]], args: argparse.Namespace, registry: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not getattr(args, "rerank", False):
        return items, {"enabled": False}
    limit = int(getattr(args, "rerank_limit", 10) or 10)
    profile_name = getattr(args, "rerank_profile", None) or ((registry.get("reranker_policy") or {}).get("default_profile")) or "disabled"
    profiles = (((registry.get("model_profiles") or {}).get("reranker") or {}))
    profile = profiles.get(profile_name) or {}
    provider = str(profile.get("provider") or "none")
    if profile_name == "disabled" or provider == "none":
        ranked = heuristic_rerank(query, items, limit)
        return ranked, {"enabled": True, "profile": profile_name, "provider": "heuristic", "note": "registry reranker is disabled; used local heuristic rerank"}
    if provider == "ollama_embedding":
        ranked = ollama_embedding_rerank(query, items, profile, limit)
        return ranked, {"enabled": True, "profile": profile_name, "provider": provider, "model": profile.get("model")}
    raise RuntimeError(f"reranker provider is not implemented: {provider}")


async def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    runtime = GraphitiRuntime()
    registry = load_registry()
    info: dict[str, Any] = {
        "config_path": str(GRAPHITI_CONFIG_PATH),
        "env_path": str(GRAPHITI_ENV_PATH),
        "registry": registry_status_summary(registry),
        "llm": {
            "provider": runtime.cfg.llm.provider,
            "model": runtime.cfg.llm.model,
            "api_url": getattr(runtime.cfg.llm.providers.openai, "api_url", None)
            if runtime.cfg.llm.providers.openai
            else None,
        },
        "embedder": {
            "provider": runtime.cfg.embedder.provider,
            "model": runtime.cfg.embedder.model,
            "dimensions": runtime.cfg.embedder.dimensions,
            "api_url": getattr(runtime.cfg.embedder.providers.openai, "api_url", None)
            if runtime.cfg.embedder.providers.openai
            else None,
        },
        "database": {
            "provider": runtime.cfg.database.provider,
            "uri": os.environ.get("NEO4J_URI"),
            "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
        },
        "graphiti": {
            "group_id": runtime.group_id,
            "entity_types": list((runtime.entity_types or {}).keys()),
            "edge_types": list((runtime.edge_types or {}).keys()),
            "edge_type_map": {f"{k[0]}->{k[1]}": v for k, v in (runtime.edge_type_map or {}).items()},
        },
    }
    async with runtime as rt:
        async with rt.client.driver.session() as session:
            result = await session.run("MATCH (n) RETURN count(n) AS count")
            rows = [record async for record in result]
            info["neo4j"] = {"ok": True, "node_count": rows[0]["count"] if rows else 0}
    return info


async def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for name, path in (
        ("graphiti_mcp_dir", GRAPHITI_MCP_DIR),
        ("venv_python", GRAPHITI_VENV_PYTHON),
        ("config", GRAPHITI_CONFIG_PATH),
        ("env", GRAPHITI_ENV_PATH),
    ):
        checks.append({"name": name, "ok": path.exists(), "path": str(path)})
    try:
        status = await cmd_status(args)
        checks.append({"name": "graphiti_core_status", "ok": True, "details": status})
    except Exception as exc:
        checks.append({"name": "graphiti_core_status", "ok": False, "error": str(exc)})
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


async def cmd_episodes(args: argparse.Namespace) -> dict[str, Any]:
    async with GraphitiRuntime() as rt:
        groups = parse_group_ids(args.group_id, args.group_ids, rt.group_id)
        validate_group_ids(groups, load_registry(), allow_unregistered=getattr(args, "allow_unregistered_group", False))
        episodes = await rt.modules["EpisodicNode"].get_by_group_ids(rt.client.driver, groups, limit=args.limit)
        items = [
            ep.model_dump(exclude={"entity_edges"} if not args.include_edges else set())
            for ep in episodes
        ]
        return {"message": "episodes retrieved", "group_ids": groups, "items": items}


async def cmd_add(args: argparse.Namespace) -> dict[str, Any]:
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    if not body:
        raise SystemExit("add requires --body or --body-file")

    async with GraphitiRuntime() as rt:
        group_id = args.group_id or rt.group_id
        validate_group_ids([group_id], load_registry(), allow_unregistered=getattr(args, "allow_unregistered_group", False))
        result = await retry_async(
            "add_episode",
            args.retries,
            lambda: rt.client.add_episode(
                name=args.name,
                episode_body=body,
                source_description=args.source_description,
                reference_time=datetime.now(timezone.utc),
                source=episode_type(args.source, rt.modules),
                group_id=group_id,
                entity_types=rt.entity_types,
                excluded_entity_types=args.exclude_entity_type or None,
                edge_types=rt.edge_types,
                edge_type_map=rt.edge_type_map,
                custom_extraction_instructions=args.custom_extraction_instructions,
            ),
        )
        payload = {
            "message": "episode written",
            "group_id": group_id,
            "episode": result.episode.model_dump(),
            "edge_count": len(result.edges),
            "node_count": len(result.nodes),
        }
        if args.verify:
            try:
                verified_episode = await rt.modules["EpisodicNode"].get_by_uuid(rt.client.driver, result.episode.uuid)
                payload["verified"] = verified_episode.uuid == result.episode.uuid
            except Exception as exc:
                payload["verified"] = False
                payload["verify_error"] = str(exc)
        return payload


async def cmd_search_facts(args: argparse.Namespace) -> dict[str, Any]:
    async with GraphitiRuntime() as rt:
        registry = load_registry()
        groups = parse_group_ids(args.group_id, args.group_ids, rt.group_id)
        validate_group_ids(groups, registry, allow_unregistered=getattr(args, "allow_unregistered_group", False))
        recall_limit = max(args.limit, int(getattr(args, "rerank_recall_limit", 30) or 30)) if getattr(args, "rerank", False) else args.limit
        edges = await retry_async(
            "search_facts",
            args.retries,
            lambda: rt.client.search(
                query=args.query,
                group_ids=groups,
                num_results=recall_limit,
                center_node_uuid=args.center_node_uuid,
            ),
        )
        items = [format_fact_result_safe(edge) for edge in edges]
        items, rerank_info = apply_rerank(args.query, items, args, registry)
        return {"message": "facts retrieved", "group_ids": groups, "rerank": rerank_info, "items": items[: args.limit if not getattr(args, "rerank", False) else len(items)]}


async def cmd_search_nodes(args: argparse.Namespace) -> dict[str, Any]:
    async with GraphitiRuntime() as rt:
        registry = load_registry()
        groups = parse_group_ids(args.group_id, args.group_ids, rt.group_id)
        validate_group_ids(groups, registry, allow_unregistered=getattr(args, "allow_unregistered_group", False))
        filters = rt.modules["SearchFilters"](node_labels=args.entity_type or None)
        recall_limit = max(args.limit, int(getattr(args, "rerank_recall_limit", 30) or 30)) if getattr(args, "rerank", False) else args.limit
        results = await retry_async(
            "search_nodes",
            args.retries,
            lambda: rt.client.search_(
                query=args.query,
                config=rt.modules["NODE_HYBRID_SEARCH_RRF"],
                group_ids=groups,
                search_filter=filters,
            ),
        )
        nodes = (results.nodes or [])[: recall_limit]
        items = [format_node_result_safe(node) for node in nodes]
        items, rerank_info = apply_rerank(args.query, items, args, registry)
        return {"message": "nodes retrieved", "group_ids": groups, "rerank": rerank_info, "items": items[: args.limit if not getattr(args, "rerank", False) else len(items)]}


async def cmd_get_edge(args: argparse.Namespace) -> dict[str, Any]:
    async with GraphitiRuntime() as rt:
        edge = await rt.modules["EntityEdge"].get_by_uuid(rt.client.driver, args.uuid)
        return format_fact_result_safe(edge)


async def cmd_delete_episode(args: argparse.Namespace) -> dict[str, Any]:
    require_yes(args, "delete-episode")
    async with GraphitiRuntime() as rt:
        ep = await rt.modules["EpisodicNode"].get_by_uuid(rt.client.driver, args.uuid)
        await ep.delete(rt.client.driver)
        return {"message": "episode deleted", "uuid": args.uuid}


async def cmd_delete_edge(args: argparse.Namespace) -> dict[str, Any]:
    require_yes(args, "delete-edge")
    async with GraphitiRuntime() as rt:
        edge = await rt.modules["EntityEdge"].get_by_uuid(rt.client.driver, args.uuid)
        await edge.delete(rt.client.driver)
        return {"message": "edge deleted", "uuid": args.uuid}


async def cmd_clear_group(args: argparse.Namespace) -> dict[str, Any]:
    require_yes(args, "clear-group")
    async with GraphitiRuntime() as rt:
        groups = parse_group_ids(args.group_id, args.group_ids, rt.group_id)
        await rt.modules["clear_data"](rt.client.driver, group_ids=groups)
        return {"message": "groups cleared", "group_ids": groups}


async def cmd_smoke(args: argparse.Namespace) -> dict[str, Any]:
    name = args.name or f"skill-cli-smoke-{int(datetime.now(timezone.utc).timestamp())}"
    args.name = name
    args.body = args.body or "Graphiti skill CLI smoke test. This verifies direct graphiti-core write and read without MCP."
    args.body_file = None
    args.source = "text"
    args.source_description = "graphiti-memory skill CLI smoke test"
    args.exclude_entity_type = None
    args.custom_extraction_instructions = None
    args.verify = True
    result = await cmd_add(args)
    facts_args = argparse.Namespace(
        query="Graphiti skill CLI smoke test",
        group_id=args.group_id,
        group_ids=None,
        allow_unregistered_group=getattr(args, "allow_unregistered_group", False),
        limit=5,
        rerank=False,
        rerank_profile=None,
        rerank_recall_limit=30,
        rerank_limit=10,
        center_node_uuid=None,
        retries=args.retries,
    )
    facts = await cmd_search_facts(facts_args)
    return {"write": result, "facts": facts}


async def cmd_reranker_status(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_registry()
    profiles = ((registry.get("model_profiles") or {}).get("reranker") or {}) if registry else {}
    policy = (registry.get("reranker_policy") or {}) if registry else {}
    return {
        "registry_path": str(REGISTRY_PATH),
        "registry_loaded": bool(registry),
        "policy": policy,
        "current_profile": (registry.get("current_profiles") or {}).get("reranker") if registry else None,
        "profiles": profiles,
        "note": "Reranker is external to Graphiti core in this skill. Disabled means Graphiti hybrid/RRF ordering is returned unchanged unless --rerank is explicitly used.",
    }


async def cmd_reranker_test(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_registry()
    sample_items = [
        {"name": "Graphiti skill CLI", "summary": "本地 CLI 支持 group_id、同步写入、查询 facts 和 nodes。"},
        {"name": "Unrelated UI note", "summary": "页面使用 details 折叠展示长内容。"},
        {"name": "Reranker profile", "summary": "外置 reranker 默认关闭，可用 Ollama bge reranker profile。"},
    ]
    namespace = argparse.Namespace(
        rerank=True,
        rerank_profile=args.rerank_profile,
        rerank_limit=args.limit,
        rerank_recall_limit=len(sample_items),
    )
    ranked, info = apply_rerank(args.query, sample_items, namespace, registry)
    return {"query": args.query, "rerank": info, "items": ranked}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stable local Graphiti CLI for graphiti-memory skill")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_format_arg(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--format", choices=["json", "table"], default=argparse.SUPPRESS)

    status = sub.add_parser("status")
    add_format_arg(status)
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor")
    add_format_arg(doctor)
    doctor.set_defaults(func=cmd_doctor)

    smoke = sub.add_parser("smoke")
    add_format_arg(smoke)
    smoke.add_argument("--group-id")
    smoke.add_argument("--allow-unregistered-group", action="store_true")
    smoke.add_argument("--name")
    smoke.add_argument("--body")
    smoke.add_argument("--verify-limit", type=int, default=20)
    smoke.add_argument("--retries", type=int, default=4)
    smoke.set_defaults(func=cmd_smoke)

    reranker_status = sub.add_parser("reranker-status")
    add_format_arg(reranker_status)
    reranker_status.set_defaults(func=cmd_reranker_status)

    reranker_test = sub.add_parser("reranker-test")
    add_format_arg(reranker_test)
    reranker_test.add_argument("--query", default="Graphiti CLI group reranker")
    reranker_test.add_argument("--rerank-profile")
    reranker_test.add_argument("--limit", type=int, default=3)
    reranker_test.set_defaults(func=cmd_reranker_test)

    add = sub.add_parser("add")
    add_format_arg(add)
    add.add_argument("--name", required=True)
    add.add_argument("--body")
    add.add_argument("--body-file")
    add.add_argument("--group-id")
    add.add_argument("--allow-unregistered-group", action="store_true")
    add.add_argument("--source", default="text", choices=["text", "json", "message"])
    add.add_argument("--source-description", default="graphiti-memory skill CLI")
    add.add_argument("--exclude-entity-type", action="append")
    add.add_argument("--custom-extraction-instructions")
    add.add_argument("--verify", action="store_true", default=True)
    add.add_argument("--no-verify", action="store_false", dest="verify")
    add.add_argument("--verify-limit", type=int, default=20)
    add.add_argument("--retries", type=int, default=4)
    add.set_defaults(func=cmd_add)

    episodes = sub.add_parser("episodes")
    add_format_arg(episodes)
    episodes.add_argument("--group-id")
    episodes.add_argument("--group-ids")
    episodes.add_argument("--allow-unregistered-group", action="store_true")
    episodes.add_argument("--limit", type=int, default=10)
    episodes.add_argument("--include-edges", action="store_true")
    episodes.set_defaults(func=cmd_episodes)

    facts = sub.add_parser("search-facts")
    add_format_arg(facts)
    facts.add_argument("--query", required=True)
    facts.add_argument("--group-id")
    facts.add_argument("--group-ids")
    facts.add_argument("--allow-unregistered-group", action="store_true")
    facts.add_argument("--limit", type=int, default=10)
    facts.add_argument("--rerank", action="store_true")
    facts.add_argument("--rerank-profile")
    facts.add_argument("--rerank-recall-limit", type=int, default=30)
    facts.add_argument("--rerank-limit", type=int, default=10)
    facts.add_argument("--center-node-uuid")
    facts.add_argument("--retries", type=int, default=3)
    facts.set_defaults(func=cmd_search_facts)

    nodes = sub.add_parser("search-nodes")
    add_format_arg(nodes)
    nodes.add_argument("--query", required=True)
    nodes.add_argument("--group-id")
    nodes.add_argument("--group-ids")
    nodes.add_argument("--allow-unregistered-group", action="store_true")
    nodes.add_argument("--limit", type=int, default=10)
    nodes.add_argument("--rerank", action="store_true")
    nodes.add_argument("--rerank-profile")
    nodes.add_argument("--rerank-recall-limit", type=int, default=30)
    nodes.add_argument("--rerank-limit", type=int, default=10)
    nodes.add_argument("--entity-type", action="append")
    nodes.add_argument("--retries", type=int, default=3)
    nodes.set_defaults(func=cmd_search_nodes)

    edge = sub.add_parser("get-edge")
    add_format_arg(edge)
    edge.add_argument("--uuid", required=True)
    edge.set_defaults(func=cmd_get_edge)

    delete_ep = sub.add_parser("delete-episode")
    add_format_arg(delete_ep)
    delete_ep.add_argument("--uuid", required=True)
    delete_ep.add_argument("--yes", action="store_true")
    delete_ep.set_defaults(func=cmd_delete_episode)

    delete_edge = sub.add_parser("delete-edge")
    add_format_arg(delete_edge)
    delete_edge.add_argument("--uuid", required=True)
    delete_edge.add_argument("--yes", action="store_true")
    delete_edge.set_defaults(func=cmd_delete_edge)

    clear = sub.add_parser("clear-group")
    add_format_arg(clear)
    clear.add_argument("--group-id")
    clear.add_argument("--group-ids")
    clear.add_argument("--yes", action="store_true")
    clear.set_defaults(func=cmd_clear_group)

    return parser


def main() -> int:
    ensure_utf8()
    reexec_with_graphiti_python()
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = asyncio.run(args.func(args))
        emit(result, args.format)
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        emit({"error": str(exc), "type": type(exc).__name__}, args.format)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
