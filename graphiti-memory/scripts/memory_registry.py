"""Registry CLI for the local Graphiti memory system.

The runtime YAML and .env remain the source of truth for what the Graphiti
service actually runs. This registry records governance metadata used by the
skill and daemon: groups, model profiles, reranker profiles, and policies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GRAPHITI_MCP_DIR = Path(r"D:\Program_Files\graphiti\mcp_server")
GRAPHITI_VENV_PYTHON = GRAPHITI_MCP_DIR / ".venv" / "Scripts" / "python.exe"
GRAPHITI_CONFIG_PATH = GRAPHITI_MCP_DIR / "config" / "config-docker-neo4j.yaml"
REGISTRY_PATH = GRAPHITI_MCP_DIR / "config" / "memory-registry.yaml"
SUMMARY_PATH = Path(__file__).resolve().parent.parent / "references" / "memory-system-registry.md"
BACKUP_DIR = GRAPHITI_MCP_DIR / "config" / "registry-backups"
DEFAULT_DAEMON_CONFIG_PATH = Path(r"D:\Program_Files\graphiti-memory-daemon\config.yaml")
MODEL_TEST_TIMEOUT_SECONDS = 45

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


def load_any_yaml(path: Path, *, label: str = "YAML") -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit(f"PyYAML is required to read {label}: {exc}") from exc
    if not path.exists():
        raise SystemExit(f"{label} file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_any_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        import yaml
    except Exception as exc:
        raise SystemExit(f"PyYAML is required to write YAML: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
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


def cmd_list_profiles(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    profiles = (registry.get("model_profiles") or {}).get(args.kind) or {}
    current = (registry.get("current_profiles") or {}).get(args.kind)
    return {
        "message": "profiles retrieved",
        "kind": args.kind,
        "current": current,
        "items": [
            {
                "profile": name,
                "enabled": bool(raw.get("enabled", False)) if isinstance(raw, dict) else False,
                "provider": raw.get("provider") if isinstance(raw, dict) else None,
                "model": raw.get("model") if isinstance(raw, dict) else None,
                "upstream": raw.get("upstream") if isinstance(raw, dict) else None,
                "api_url": (raw.get("api_url") or raw.get("api_url_env")) if isinstance(raw, dict) else None,
                "tested_at": raw.get("tested_at") if isinstance(raw, dict) else None,
                "test_status": raw.get("test_status") if isinstance(raw, dict) else None,
                "current": name == current,
                "notes": raw.get("notes", raw.get("purpose", "")) if isinstance(raw, dict) else "",
            }
            for name, raw in profiles.items()
        ],
    }


def upstream_items(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    upstreams = registry.setdefault("model_upstreams", {})
    if not isinstance(upstreams, dict):
        raise SystemExit("model_upstreams must be a mapping in registry")
    defaults = {
        "deepseek": {
            "enabled": True,
            "provider": "openai_compatible",
            "api_url": "https://api.deepseek.com",
            "models_url": "https://api.deepseek.com/v1/models",
            "api_key_env": "DEEPSEEK_API_KEY",
            "profile_prefix": "deepseek",
            "notes": "DeepSeek OpenAI-compatible endpoint.",
        },
        "sub2api": {
            "enabled": True,
            "provider": "openai_compatible",
            "api_url": "https://sub2api.meifu.zzxhlyj.top",
            "models_url": "https://sub2api.meifu.zzxhlyj.top/v1/models",
            "api_key_env": "SUB2API_API_KEY",
            "profile_prefix": "sub2api",
            "notes": "GPT-compatible relay endpoint.",
        },
        "ollama": {
            "enabled": True,
            "provider": "ollama",
            "api_url": "http://127.0.0.1:11434",
            "models_url": "http://127.0.0.1:11434/api/tags",
            "api_key": "ollama",
            "profile_prefix": "ollama",
            "notes": "Local Ollama endpoint.",
        },
    }
    for name, raw in defaults.items():
        upstreams.setdefault(name, raw)
    return {str(name): dict(raw) for name, raw in upstreams.items() if isinstance(raw, dict)}


def cmd_list_upstreams(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    return {
        "message": "upstreams retrieved",
        "items": [
            {"name": name, **_public_upstream(raw)}
            for name, raw in upstream_items(registry).items()
        ],
    }


def cmd_refresh_models(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    upstreams = upstream_items(registry)
    names = [args.upstream] if args.upstream else list(upstreams)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for name in names:
        raw = upstreams.get(name)
        if not raw:
            errors.append({"upstream": name, "error": "unknown upstream"})
            continue
        try:
            models = _discover_upstream_models(name, raw)
            for model in models:
                items.append({"upstream": name, "model": model, "provider": raw.get("provider"), "api_url": raw.get("api_url")})
        except Exception as exc:
            errors.append({"upstream": name, "error": redact(str(exc))})
    return {
        "message": "models refreshed",
        "items": items,
        "errors": errors,
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cmd_test_model(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    if args.profile:
        profile = _profile_by_kind(registry, args.kind, args.profile)
        target = _test_target_from_profile(profile)
        profile_name = args.profile
    else:
        if not args.upstream or not args.model:
            raise SystemExit("test-model requires either --profile or both --upstream and --model")
        upstream = upstream_items(registry).get(args.upstream)
        if not upstream:
            raise SystemExit(f"Unknown upstream: {args.upstream}")
        target = _test_target_from_upstream(upstream, args.model, kind=args.kind)
        profile_name = _profile_name_for(args.upstream, args.model)
    result = _test_model_target(target, prompt=args.prompt)
    return {
        "message": "model test completed",
        "ok": bool(result.get("ok")),
        "kind": args.kind,
        "profile": profile_name,
        "target": redact({key: value for key, value in target.items() if key != "api_key"}),
        "result": result,
    }


def cmd_add_model_profile(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    upstreams = upstream_items(registry)
    upstream = upstreams.get(args.upstream)
    if not upstream:
        raise SystemExit(f"Unknown upstream: {args.upstream}")
    profile_name = args.profile or _profile_name_for(args.upstream, args.model)
    profiles_root = registry.setdefault("model_profiles", {})
    profiles = profiles_root.setdefault(args.kind, {})
    existing = _find_profile_by_upstream_model(profiles, args.upstream, args.model)
    if existing and not args.profile and not args.update:
        return {
            "message": "model profile already exists",
            "ok": True,
            "existing": True,
            "kind": args.kind,
            "profile": existing[0],
            "profile_data": existing[1],
        }
    if profile_name in profiles and not args.update:
        raise SystemExit(f"Profile already exists: {args.kind}/{profile_name}. Use --update to replace metadata.")
    target = _test_target_from_upstream(upstream, args.model, kind=args.kind)
    test_result = _test_model_target(target, prompt=args.prompt)
    profile = _profile_from_upstream(args.kind, args.upstream, upstream, args.model, test_result)
    preview = {
        "kind": args.kind,
        "profile": profile_name,
        "upstream": args.upstream,
        "model": args.model,
        "test": test_result,
        "profile_data": profile,
    }
    if not test_result.get("ok"):
        return {"message": "model test failed; profile not added", "ok": False, **preview}
    if args.dry_run:
        return {"message": "dry run; profile not added", "ok": True, **preview}
    if not args.yes:
        raise SystemExit("add-model-profile writes registry. Re-run with --yes or use --dry-run.")
    backup = _backup_selected_files("add-model-profile", [REGISTRY_PATH])
    profiles[profile_name] = profile
    save_yaml(REGISTRY_PATH, registry)
    _maybe_render_summary_after_change()
    return {"message": "model profile added", "ok": True, "backup": backup, **preview}


def cmd_delete_model_profile(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    current = (registry.get("current_profiles") or {}).get(args.kind)
    if current == args.profile:
        raise SystemExit(f"Cannot delete current {args.kind} profile: {args.profile}. Switch to another profile first.")
    profiles = (registry.get("model_profiles") or {}).get(args.kind) or {}
    if args.profile not in profiles:
        raise SystemExit(f"Unknown {args.kind} profile: {args.profile}")
    deleted = profiles[args.profile]
    preview = {"kind": args.kind, "profile": args.profile, "deleted": deleted}
    if args.dry_run:
        return {"message": "dry run; profile not deleted", **preview}
    if not args.yes:
        raise SystemExit("delete-model-profile writes registry. Re-run with --yes or use --dry-run.")
    backup = _backup_selected_files("delete-model-profile", [REGISTRY_PATH])
    del profiles[args.profile]
    save_yaml(REGISTRY_PATH, registry)
    _maybe_render_summary_after_change()
    return {"message": "model profile deleted", "backup": backup, **preview}


def cmd_switch_profile(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind != "graphiti_llm":
        raise SystemExit("switch-profile currently supports --kind graphiti_llm only; daemon/reranker profiles are changed in daemon config/registry policy.")
    registry = load_yaml(REGISTRY_PATH)
    profiles = (registry.get("model_profiles") or {}).get(args.kind) or {}
    profile = profiles.get(args.profile)
    if not isinstance(profile, dict):
        raise SystemExit(f"Unknown {args.kind} profile: {args.profile}")
    runtime = load_runtime_yaml()
    llm = runtime.setdefault("llm", {})
    before = {
        "provider": llm.get("provider"),
        "model": llm.get("model"),
        "api_url": (((llm.get("providers") or {}).get(llm.get("provider")) or {}).get("api_url")),
        "api_key": (((llm.get("providers") or {}).get(llm.get("provider")) or {}).get("api_key")),
    }
    after = {
        "provider": profile.get("provider", before.get("provider")),
        "model": profile.get("model", before.get("model")),
        "api_url": profile.get("api_url") or (f"${{{profile.get('api_url_env')}}}" if profile.get("api_url_env") else before.get("api_url")),
        "api_key": profile.get("api_key") or (f"${{{profile.get('api_key_env')}}}" if profile.get("api_key_env") else before.get("api_key")),
    }
    diff = {"kind": args.kind, "profile": args.profile, "before": before, "after": after}
    test = None
    if args.test_first or args.prepare_only:
        test = _test_model_target(_test_target_from_profile(profile))
        diff["test"] = test
        if not test.get("ok"):
            return {"message": "model test failed; runtime YAML not changed", "ok": False, "diff": diff}
    if args.dry_run or args.prepare_only:
        return {"message": "dry run; runtime YAML not changed", "ok": True, "diff": diff, "prepared": bool(args.prepare_only)}
    if not args.yes:
        raise SystemExit("switch-profile changes runtime YAML and registry. Re-run with --yes or use --dry-run.")
    if args.test_first and test is None:
        test = _test_model_target(_test_target_from_profile(profile))
        diff["test"] = test
        if not test.get("ok"):
            return {"message": "model test failed; runtime YAML not changed", "ok": False, "diff": diff}

    backup = _backup_runtime_files("switch-profile")
    llm["provider"] = after["provider"]
    llm["model"] = after["model"]
    providers = llm.setdefault("providers", {})
    provider_cfg = providers.setdefault(after["provider"], {})
    if after["api_url"]:
        provider_cfg["api_url"] = after["api_url"]
    if after["api_key"]:
        provider_cfg["api_key"] = after["api_key"]
    current = registry.setdefault("current_profiles", {})
    current[args.kind] = args.profile
    _save_runtime_yaml(runtime)
    save_yaml(REGISTRY_PATH, registry)
    return {"message": "profile switched", "ok": True, "backup": backup, "diff": diff}


def cmd_apply_profile_switch(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "graphiti_llm":
        switch_args = argparse.Namespace(kind=args.kind, profile=args.profile, dry_run=False, prepare_only=False, test_first=True, yes=args.yes)
        return cmd_switch_profile(switch_args)
    if args.kind == "daemon_assistant":
        switch_args = argparse.Namespace(profile=args.profile, config=args.config, enabled=args.enabled, dry_run=False, yes=args.yes)
        return cmd_switch_daemon_profile(switch_args)
    if args.kind == "reranker":
        switch_args = argparse.Namespace(
            profile=args.profile,
            config=args.config,
            enabled=args.enabled,
            recall_limit=args.recall_limit,
            output_limit=args.output_limit,
            dry_run=False,
            yes=args.yes,
        )
        return cmd_switch_reranker_profile(switch_args)
    raise SystemExit(f"Unsupported kind: {args.kind}")


def cmd_rollback_profile(args: argparse.Namespace) -> dict[str, Any]:
    backup_root = BACKUP_DIR / args.backup_id
    targets = _backup_restore_targets(backup_root)
    if not targets:
        raise SystemExit(f"Backup not found or incomplete: {backup_root}")
    if args.dry_run:
        return {
            "message": "dry run; no files restored",
            "backup": str(backup_root),
            "restore": [{"from": str(src), "to": str(dst)} for src, dst in targets],
        }
    if not args.yes:
        raise SystemExit("rollback-profile restores backed up config files. Re-run with --yes or use --dry-run.")
    safety = _backup_selected_files("pre-rollback", [dst for _src, dst in targets])
    for source, target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _maybe_render_summary_after_change()
    return {
        "message": "profile rollback restored",
        "backup": str(backup_root),
        "safety_backup": safety,
        "restored": [{"from": str(src), "to": str(dst)} for src, dst in targets],
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
    backup = GRAPHITI_CONFIG_PATH.with_suffix(f".yaml.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(GRAPHITI_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    _save_runtime_yaml(raw)
    return {"message": f"{section_name} added", "backup": str(backup), **preview}


def _save_runtime_yaml(data: dict[str, Any]) -> None:
    import yaml

    GRAPHITI_CONFIG_PATH.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def _backup_runtime_files(reason: str) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_DIR / f"{stamp}-{reason}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GRAPHITI_CONFIG_PATH, backup_dir / GRAPHITI_CONFIG_PATH.name)
    shutil.copy2(REGISTRY_PATH, backup_dir / REGISTRY_PATH.name)
    _write_backup_manifest(backup_dir, [GRAPHITI_CONFIG_PATH, REGISTRY_PATH])
    return {"id": backup_dir.name, "path": str(backup_dir)}


def _backup_selected_files(reason: str, files: list[Path]) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_DIR / f"{stamp}-{reason}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for path in files:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
            copied.append(path)
    _write_backup_manifest(backup_dir, copied)
    return {"id": backup_dir.name, "path": str(backup_dir)}


def _write_backup_manifest(backup_dir: Path, files: list[Path]) -> None:
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [{"name": path.name, "original_path": str(path)} for path in files],
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _backup_restore_targets(backup_root: Path) -> list[tuple[Path, Path]]:
    manifest_path = backup_root / "manifest.json"
    targets: list[tuple[Path, Path]] = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid backup manifest: {manifest_path}: {exc}") from exc
        for item in manifest.get("files", []):
            if not isinstance(item, dict):
                continue
            backup_file = backup_root / str(item.get("name", ""))
            original = Path(str(item.get("original_path", "")))
            if backup_file.exists() and str(original):
                targets.append((backup_file, original))
        if targets:
            return targets
    inferred = [
        (backup_root / GRAPHITI_CONFIG_PATH.name, GRAPHITI_CONFIG_PATH),
        (backup_root / REGISTRY_PATH.name, REGISTRY_PATH),
        (backup_root / DEFAULT_DAEMON_CONFIG_PATH.name, DEFAULT_DAEMON_CONFIG_PATH),
    ]
    return [(src, dst) for src, dst in inferred if src.exists()]


def _daemon_config_path(args: argparse.Namespace) -> Path:
    return Path(args.config or DEFAULT_DAEMON_CONFIG_PATH)


def _profile_by_kind(registry: dict[str, Any], kind: str, profile_name: str) -> dict[str, Any]:
    profiles = (registry.get("model_profiles") or {}).get(kind) or {}
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise SystemExit(f"Unknown {kind} profile: {profile_name}")
    return profile


def _public_upstream(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("***REDACTED***" if is_secret_key(key) else value)
        for key, value in raw.items()
        if key not in {"api_key"}
    }


def _profile_name_for(upstream_name: str, model: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model.strip()).strip("-").lower()
    return f"{upstream_name}-{slug}" if not slug.startswith(f"{upstream_name}-") else slug


def _find_profile_by_upstream_model(profiles: dict[str, Any], upstream: str, model: str) -> tuple[str, dict[str, Any]] | None:
    for name, raw in profiles.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("upstream") == upstream and raw.get("model") == model:
            return str(name), raw
    return None


def _normalize_api_url(url: str | None) -> str:
    text = (url or "").strip().rstrip("/")
    if not text:
        raise ValueError("api_url is required")
    if text.endswith("/v1"):
        return text[:-3]
    return text


def _openai_models_url(api_url: str | None, explicit: str | None = None) -> str:
    if explicit:
        return _resolve_env_template(explicit)
    return f"{_normalize_api_url(api_url)}/v1/models"


def _openai_chat_url(api_url: str | None) -> str:
    return f"{_normalize_api_url(api_url)}/v1/chat/completions"


def _profile_from_upstream(kind: str, upstream_name: str, upstream: dict[str, Any], model: str, test_result: dict[str, Any]) -> dict[str, Any]:
    provider = upstream.get("runtime_provider") or ("openai" if kind == "graphiti_llm" else upstream.get("provider", "openai_compatible"))
    if kind == "reranker" and upstream_name == "ollama":
        provider = "ollama_embedding"
    profile: dict[str, Any] = {
        "enabled": True,
        "provider": provider,
        "model": model,
        "upstream": upstream_name,
        "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "test_status": "ok" if test_result.get("ok") else "failed",
    }
    if upstream.get("api_url_env"):
        profile["api_url_env"] = upstream.get("api_url_env")
    elif upstream.get("api_url"):
        if upstream_name == "ollama" and kind == "graphiti_llm":
            profile["api_url"] = f"{_normalize_api_url(str(upstream.get('api_url')))}/v1"
        else:
            profile["api_url"] = str(upstream.get("api_url"))
    if upstream.get("api_key_env"):
        profile["api_key_env"] = upstream.get("api_key_env")
    if upstream.get("api_key") and not upstream.get("api_key_env"):
        profile["api_key"] = upstream.get("api_key")
    if kind == "daemon_assistant":
        profile["purpose"] = "常驻服务会话 turn 摘要、长期记忆候选提取和冲突解释。"
    elif kind == "reranker":
        profile["notes"] = "外置 reranker profile；由 daemon/skill 在 Graphiti 召回后执行。"
    else:
        profile["notes"] = f"{upstream_name} upstream profile, added after model test."
    return profile


def _test_target_from_upstream(upstream: dict[str, Any], model: str, *, kind: str | None = None) -> dict[str, Any]:
    provider = upstream.get("provider", "openai_compatible")
    if kind == "reranker" and upstream.get("provider") == "ollama":
        provider = "ollama_embedding"
    return {
        "provider": provider,
        "model": model,
        "api_url": _env_or_profile_value(upstream, "api_url"),
        "api_key": _env_or_profile_value(upstream, "api_key"),
        "api_key_env": upstream.get("api_key_env"),
    }


def _test_target_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    provider = profile.get("provider", "openai_compatible")
    if profile.get("upstream") == "ollama" and provider == "openai":
        provider = "openai_compatible"
    return {
        "provider": provider,
        "model": profile.get("model"),
        "api_url": _env_or_profile_value(profile, "api_url"),
        "api_key": _env_or_profile_value(profile, "api_key"),
        "api_key_env": profile.get("api_key_env"),
    }


def _discover_upstream_models(name: str, upstream: dict[str, Any]) -> list[str]:
    provider = str(upstream.get("provider", "")).lower()
    if name == "ollama" or provider == "ollama":
        return _discover_ollama_models(str(upstream.get("models_url") or f"{_normalize_api_url(str(upstream.get('api_url')))}/api/tags"))
    api_url = _env_or_profile_value(upstream, "api_url") or str(upstream.get("api_url") or "")
    url = _openai_models_url(str(api_url), str(upstream.get("models_url") or "") or None)
    headers = {"Accept": "application/json"}
    api_key = _env_or_profile_value(upstream, "api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _http_json("GET", url, headers=headers)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    models = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return sorted(set(models))


def _discover_ollama_models(url: str) -> list[str]:
    payload = _http_json("GET", url, headers={"Accept": "application/json"})
    models = []
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and item.get("name"):
            models.append(str(item["name"]))
    return sorted(set(models))


def _test_model_target(target: dict[str, Any], *, prompt: str = "Return OK as JSON.") -> dict[str, Any]:
    provider = str(target.get("provider", "openai_compatible")).lower()
    model = str(target.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "model is required"}
    try:
        if provider == "ollama_embedding":
            return _test_ollama_embedding_model(target, prompt=prompt)
        if provider in {"ollama", "ollama_embedding"} and not str(target.get("api_url", "")).rstrip("/").endswith("/v1"):
            return _test_ollama_model(target, prompt=prompt)
        return _test_openai_compatible_model(target, prompt=prompt)
    except Exception as exc:
        return {"ok": False, "error": redact(str(exc)), "type": type(exc).__name__}


def _test_openai_compatible_model(target: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    url = _openai_chat_url(str(target.get("api_url") or ""))
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if target.get("api_key"):
        headers["Authorization"] = f"Bearer {target['api_key']}"
    body = {
        "model": target["model"],
        "messages": [
            {"role": "system", "content": "You are a connectivity test. Reply with compact JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 32,
        "temperature": 0,
    }
    payload = _http_json("POST", url, headers=headers, body=body)
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    text = ""
    if choices and isinstance(choices[0], dict):
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
    return {"ok": True, "endpoint": url, "response_preview": text[:160]}


def _test_ollama_model(target: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    base = _normalize_api_url(str(target.get("api_url") or "http://127.0.0.1:11434"))
    url = f"{base}/api/generate"
    body = {"model": target["model"], "prompt": prompt, "stream": False, "options": {"num_predict": 24, "temperature": 0}}
    payload = _http_json("POST", url, headers={"Content-Type": "application/json", "Accept": "application/json"}, body=body)
    text = str(payload.get("response", "")).strip() if isinstance(payload, dict) else ""
    return {"ok": True, "endpoint": url, "response_preview": text[:160]}


def _test_ollama_embedding_model(target: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    base = _normalize_api_url(str(target.get("api_url") or "http://127.0.0.1:11434"))
    url = f"{base}/api/embed"
    body = {"model": target["model"], "input": [prompt, "Graphiti memory daemon reranker test"]}
    payload = _http_json("POST", url, headers={"Content-Type": "application/json", "Accept": "application/json"}, body=body)
    embeddings = payload.get("embeddings", []) if isinstance(payload, dict) else []
    if not embeddings:
        return {"ok": False, "endpoint": url, "error": "Ollama returned no embeddings"}
    first = embeddings[0]
    dimensions = len(first) if isinstance(first, list) else 0
    return {"ok": dimensions > 0, "endpoint": url, "dimensions": dimensions}


def _http_json(method: str, url: str, *, headers: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=MODEL_TEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {redact(raw[:800])}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(redact(str(exc))) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {raw[:400]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return payload


def _env_or_profile_value(profile: dict[str, Any], key: str) -> Any:
    if key in profile:
        return profile.get(key)
    env_key = profile.get(f"{key}_env")
    if env_key:
        return os.environ.get(str(env_key)) or _dotenv_value(GRAPHITI_MCP_DIR / ".env", str(env_key))
    return None


def _resolve_env_template(value: str) -> str:
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]+))?\}")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2) or ""
        return os.environ.get(name) or _dotenv_value(GRAPHITI_MCP_DIR / ".env", name) or default

    return pattern.sub(replace, value)


def _dotenv_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    prefix = f"{key}="
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        return value
    return None


def _require_real_change(args: argparse.Namespace, description: str) -> None:
    if args.dry_run:
        return
    if not args.yes:
        raise SystemExit(f"{description} changes daemon config and registry. Re-run with --yes or use --dry-run.")


def _maybe_render_summary_after_change() -> None:
    namespace = argparse.Namespace(verify_runtime=False)
    cmd_render_summary(namespace)


def cmd_switch_daemon_profile(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    profile = _profile_by_kind(registry, "daemon_assistant", args.profile)
    config_path = _daemon_config_path(args)
    daemon_config = load_any_yaml(config_path, label="daemon config")
    assistant = daemon_config.setdefault("assistant_model", {})
    before = {
        "enabled": assistant.get("enabled"),
        "provider": assistant.get("provider"),
        "profile": assistant.get("profile"),
        "model": assistant.get("model"),
        "api_url": assistant.get("api_url"),
        "api_key_env": assistant.get("api_key_env"),
    }
    after = {
        "enabled": bool(args.enabled) if args.enabled is not None else bool(assistant.get("enabled", False)),
        "provider": profile.get("provider", before.get("provider")),
        "profile": args.profile,
        "model": profile.get("model", before.get("model")),
        "api_url": _env_or_profile_value(profile, "api_url") or before.get("api_url"),
        "api_key_env": profile.get("api_key_env", before.get("api_key_env")),
    }
    diff = {
        "kind": "daemon_assistant",
        "profile": args.profile,
        "config_path": str(config_path),
        "before": before,
        "after": after,
        "note": "只修改常驻服务 config.yaml 与 registry current_profiles，不修改 Graphiti runtime YAML。",
    }
    if args.dry_run:
        return {"message": "dry run; daemon config and registry not changed", "diff": diff}
    _require_real_change(args, "switch-daemon-profile")
    backup = _backup_selected_files("switch-daemon-profile", [config_path, REGISTRY_PATH])
    assistant.update(after)
    current = registry.setdefault("current_profiles", {})
    current["daemon_assistant"] = args.profile
    daemon_policy = registry.setdefault("daemon_policy", {})
    daemon_policy["assistant_enabled"] = after["enabled"]
    save_any_yaml(config_path, daemon_config)
    save_yaml(REGISTRY_PATH, registry)
    _maybe_render_summary_after_change()
    return {"message": "daemon assistant profile switched", "backup": backup, "diff": diff}


def cmd_switch_reranker_profile(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    profile = _profile_by_kind(registry, "reranker", args.profile)
    config_path = _daemon_config_path(args)
    daemon_config = load_any_yaml(config_path, label="daemon config")
    reranker = daemon_config.setdefault("reranker", {})
    policy = registry.setdefault("reranker_policy", {})
    before = {
        "enabled": reranker.get("enabled"),
        "profile": reranker.get("profile"),
        "recall_limit": reranker.get("recall_limit"),
        "output_limit": reranker.get("output_limit"),
        "registry_enabled": policy.get("enabled"),
        "registry_default_profile": policy.get("default_profile"),
    }
    after_enabled = bool(profile.get("enabled", False)) if args.enabled is None else bool(args.enabled)
    after = {
        "enabled": after_enabled,
        "profile": args.profile,
        "recall_limit": args.recall_limit if args.recall_limit is not None else int(policy.get("recall_limit", reranker.get("recall_limit", 30))),
        "output_limit": args.output_limit if args.output_limit is not None else int(policy.get("output_limit", reranker.get("output_limit", 10))),
        "registry_enabled": after_enabled,
        "registry_default_profile": args.profile,
    }
    diff = {
        "kind": "reranker",
        "profile": args.profile,
        "provider": profile.get("provider"),
        "model": profile.get("model"),
        "config_path": str(config_path),
        "before": before,
        "after": after,
        "note": "只修改常驻服务 reranker 配置与 registry policy，不修改 Graphiti core。",
    }
    if args.dry_run:
        return {"message": "dry run; daemon config and registry not changed", "diff": diff}
    _require_real_change(args, "switch-reranker-profile")
    backup = _backup_selected_files("switch-reranker-profile", [config_path, REGISTRY_PATH])
    reranker["enabled"] = after["enabled"]
    reranker["profile"] = after["profile"]
    reranker["recall_limit"] = after["recall_limit"]
    reranker["output_limit"] = after["output_limit"]
    current = registry.setdefault("current_profiles", {})
    current["reranker"] = args.profile
    policy["enabled"] = after["registry_enabled"]
    policy["default_profile"] = after["registry_default_profile"]
    policy["recall_limit"] = after["recall_limit"]
    policy["output_limit"] = after["output_limit"]
    save_any_yaml(config_path, daemon_config)
    save_yaml(REGISTRY_PATH, registry)
    _maybe_render_summary_after_change()
    return {"message": "reranker profile switched", "backup": backup, "diff": diff}


def cmd_set_reranker_enabled(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    config_path = _daemon_config_path(args)
    daemon_config = load_any_yaml(config_path, label="daemon config")
    reranker = daemon_config.setdefault("reranker", {})
    policy = registry.setdefault("reranker_policy", {})
    enabled = args.enabled.lower() == "true"
    before = {"daemon_enabled": reranker.get("enabled"), "registry_enabled": policy.get("enabled")}
    after = {"daemon_enabled": enabled, "registry_enabled": enabled}
    diff = {
        "kind": "reranker_enabled",
        "config_path": str(config_path),
        "before": before,
        "after": after,
        "note": "只启用/关闭外置 rerank；不修改 Graphiti core。",
    }
    if args.dry_run:
        return {"message": "dry run; daemon config and registry not changed", "diff": diff}
    _require_real_change(args, "set-reranker-enabled")
    backup = _backup_selected_files("set-reranker-enabled", [config_path, REGISTRY_PATH])
    reranker["enabled"] = enabled
    policy["enabled"] = enabled
    save_any_yaml(config_path, daemon_config)
    save_yaml(REGISTRY_PATH, registry)
    _maybe_render_summary_after_change()
    return {"message": "reranker enabled flag changed", "backup": backup, "diff": diff}


def _runtime_registry_warnings(registry: dict[str, Any], runtime: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    current = registry.get("current_profiles") or {}
    llm_profile_name = current.get("graphiti_llm")
    if llm_profile_name:
        profile = ((registry.get("model_profiles") or {}).get("graphiti_llm") or {}).get(llm_profile_name) or {}
        llm = runtime.get("llm") or {}
        if isinstance(profile, dict):
            if profile.get("model") and llm.get("model") != profile.get("model"):
                warnings.append(f"Graphiti LLM runtime model={llm.get('model')} 与 registry profile {llm_profile_name} model={profile.get('model')} 不一致")
            if profile.get("provider") and llm.get("provider") != profile.get("provider"):
                warnings.append(f"Graphiti LLM runtime provider={llm.get('provider')} 与 registry profile {llm_profile_name} provider={profile.get('provider')} 不一致")
    embedder_profile_name = current.get("graphiti_embedder")
    if embedder_profile_name:
        profile = ((registry.get("model_profiles") or {}).get("graphiti_embedder") or {}).get(embedder_profile_name) or {}
        embedder = runtime.get("embedder") or {}
        if isinstance(profile, dict):
            if profile.get("model") and embedder.get("model") != profile.get("model"):
                warnings.append(f"Embedding runtime model={embedder.get('model')} 与 registry profile {embedder_profile_name} model={profile.get('model')} 不一致")
            if profile.get("provider") and embedder.get("provider") != profile.get("provider"):
                warnings.append(f"Embedding runtime provider={embedder.get('provider')} 与 registry profile {embedder_profile_name} provider={profile.get('provider')} 不一致")
    return warnings


def _parse_bool_arg(value: str) -> bool:
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def cmd_render_summary(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_yaml(REGISTRY_PATH)
    runtime = runtime_summary()
    runtime_warnings = _runtime_registry_warnings(registry, runtime) if getattr(args, "verify_runtime", False) else []
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
    if runtime_warnings:
        lines.extend(["", "## Runtime 校验警告", ""])
        for warning in runtime_warnings:
            lines.append(f"- {warning}")
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"message": "summary rendered", "path": str(SUMMARY_PATH), "runtime_warnings": runtime_warnings}


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

    profiles = sub.add_parser("list-profiles")
    add_format_arg(profiles)
    profiles.add_argument("--kind", choices=["graphiti_llm", "graphiti_embedder", "daemon_assistant", "reranker"], required=True)
    profiles.set_defaults(func=cmd_list_profiles)

    upstreams = sub.add_parser("list-upstreams")
    add_format_arg(upstreams)
    upstreams.set_defaults(func=cmd_list_upstreams)

    refresh_models = sub.add_parser("refresh-models")
    add_format_arg(refresh_models)
    refresh_models.add_argument("--upstream")
    refresh_models.set_defaults(func=cmd_refresh_models)

    test_model = sub.add_parser("test-model")
    add_format_arg(test_model)
    test_model.add_argument("--kind", choices=["graphiti_llm", "daemon_assistant", "reranker"], default="graphiti_llm")
    test_model.add_argument("--profile")
    test_model.add_argument("--upstream")
    test_model.add_argument("--model")
    test_model.add_argument("--prompt", default="Return OK as JSON.")
    test_model.set_defaults(func=cmd_test_model)

    add_model = sub.add_parser("add-model-profile")
    add_format_arg(add_model)
    add_model.add_argument("--kind", choices=["graphiti_llm", "daemon_assistant", "reranker"], required=True)
    add_model.add_argument("--upstream", required=True)
    add_model.add_argument("--model", required=True)
    add_model.add_argument("--profile")
    add_model.add_argument("--prompt", default="Return OK as JSON.")
    add_model.add_argument("--update", action="store_true")
    add_model.add_argument("--dry-run", action="store_true")
    add_model.add_argument("--yes", action="store_true")
    add_model.set_defaults(func=cmd_add_model_profile)

    delete_model = sub.add_parser("delete-model-profile")
    add_format_arg(delete_model)
    delete_model.add_argument("--kind", choices=["graphiti_llm", "daemon_assistant", "reranker"], required=True)
    delete_model.add_argument("--profile", required=True)
    delete_model.add_argument("--dry-run", action="store_true")
    delete_model.add_argument("--yes", action="store_true")
    delete_model.set_defaults(func=cmd_delete_model_profile)

    switch = sub.add_parser("switch-profile")
    add_format_arg(switch)
    switch.add_argument("--kind", choices=["graphiti_llm"], required=True)
    switch.add_argument("--profile", required=True)
    switch.add_argument("--dry-run", action="store_true")
    switch.add_argument("--test-first", action="store_true")
    switch.add_argument("--prepare-only", action="store_true")
    switch.add_argument("--yes", action="store_true")
    switch.set_defaults(func=cmd_switch_profile)

    apply_switch = sub.add_parser("apply-profile-switch")
    add_format_arg(apply_switch)
    apply_switch.add_argument("--kind", choices=["graphiti_llm", "daemon_assistant", "reranker"], required=True)
    apply_switch.add_argument("--profile", required=True)
    apply_switch.add_argument("--config", default=str(DEFAULT_DAEMON_CONFIG_PATH))
    apply_switch.add_argument("--enabled", type=_parse_bool_arg)
    apply_switch.add_argument("--recall-limit", type=int)
    apply_switch.add_argument("--output-limit", type=int)
    apply_switch.add_argument("--yes", action="store_true")
    apply_switch.set_defaults(func=cmd_apply_profile_switch)

    daemon_switch = sub.add_parser("switch-daemon-profile")
    add_format_arg(daemon_switch)
    daemon_switch.add_argument("--profile", required=True)
    daemon_switch.add_argument("--config", default=str(DEFAULT_DAEMON_CONFIG_PATH))
    daemon_switch.add_argument("--enabled", type=_parse_bool_arg)
    daemon_switch.add_argument("--dry-run", action="store_true")
    daemon_switch.add_argument("--yes", action="store_true")
    daemon_switch.set_defaults(func=cmd_switch_daemon_profile)

    reranker_switch = sub.add_parser("switch-reranker-profile")
    add_format_arg(reranker_switch)
    reranker_switch.add_argument("--profile", required=True)
    reranker_switch.add_argument("--config", default=str(DEFAULT_DAEMON_CONFIG_PATH))
    reranker_switch.add_argument("--enabled", type=_parse_bool_arg)
    reranker_switch.add_argument("--recall-limit", type=int)
    reranker_switch.add_argument("--output-limit", type=int)
    reranker_switch.add_argument("--dry-run", action="store_true")
    reranker_switch.add_argument("--yes", action="store_true")
    reranker_switch.set_defaults(func=cmd_switch_reranker_profile)

    reranker_enabled = sub.add_parser("set-reranker-enabled")
    add_format_arg(reranker_enabled)
    reranker_enabled.add_argument("--enabled", choices=["true", "false"], required=True)
    reranker_enabled.add_argument("--config", default=str(DEFAULT_DAEMON_CONFIG_PATH))
    reranker_enabled.add_argument("--dry-run", action="store_true")
    reranker_enabled.add_argument("--yes", action="store_true")
    reranker_enabled.set_defaults(func=cmd_set_reranker_enabled)

    rollback = sub.add_parser("rollback-profile")
    add_format_arg(rollback)
    rollback.add_argument("--backup-id", required=True)
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--yes", action="store_true")
    rollback.set_defaults(func=cmd_rollback_profile)

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
    render.add_argument("--verify-runtime", action="store_true")
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
