#!/usr/bin/env python3
"""Audit ComfyUI workflow files without changing them.

Usage:
  python audit_workflows.py \
    --root runtime="C:/Users/12070/Documents/ComfyUI/user/default/workflows" \
    --root library="D:/12070/Documents/workspaces/Comfy-Codex-Workspace/workflows" \
    --output report.json

The report distinguishes byte-identical files from JSON-equivalent files and
records invalid/non-workflow JSON instead of silently dropping it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_root(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--root must use name=path")
    name, raw_path = value.split("=", 1)
    name = name.strip()
    if not name or not raw_path.strip():
        raise argparse.ArgumentTypeError("--root must use a non-empty name=path")
    return name, Path(raw_path).expanduser().resolve()


def workflow_info(payload: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return "non_object_json", {}
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return "non_workflow_json", {}
    node_types = sorted(
        {str(node.get("type")) for node in nodes if isinstance(node, dict) and node.get("type")}
    )
    links = payload.get("links")
    return "workflow", {
        "node_count": len(nodes),
        "link_count": len(links) if isinstance(links, list) else None,
        "node_types": node_types,
        "has_definitions": isinstance(payload.get("definitions"), dict),
        "has_extra": isinstance(payload.get("extra"), dict),
    }


def audit_root(name: str, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return [{"root": name, "root_path": str(root), "status": "missing_root"}]
    for path in sorted(root.rglob("*.json")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        row: dict[str, Any] = {
            "root": name,
            "root_path": str(root),
            "path": str(path),
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": len(raw),
            "raw_sha256": sha256_bytes(raw),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        }
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            row.update({"status": "invalid_json", "error": str(exc)})
            rows.append(row)
            continue
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        row["canonical_sha256"] = sha256_bytes(canonical)
        status, info = workflow_info(payload)
        row["status"] = status
        row.update(info)
        rows.append(row)
    return rows


def duplicate_groups(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value and row.get("status") == "workflow":
            groups[value].append(row)
    result = []
    for digest, members in sorted(groups.items()):
        if len(members) > 1:
            result.append({"digest": digest, "count": len(members), "files": [m["path"] for m in members]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=parse_root, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for name, root in args.root:
        rows.extend(audit_root(name, root))
    report = {
        "schema": "ComfyUIWorkflowAuditV1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": [{"name": name, "path": str(path)} for name, path in args.root],
        "file_count": len(rows),
        "workflow_count": sum(row.get("status") == "workflow" for row in rows),
        "invalid_count": sum(row.get("status") == "invalid_json" for row in rows),
        "non_workflow_count": sum(row.get("status") in {"non_workflow_json", "non_object_json"} for row in rows),
        "exact_duplicate_groups": duplicate_groups(rows, "raw_sha256"),
        "json_equivalent_groups": duplicate_groups(rows, "canonical_sha256"),
        "files": rows,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "schema", "file_count", "workflow_count", "invalid_count",
        "non_workflow_count", "exact_duplicate_groups", "json_equivalent_groups",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
