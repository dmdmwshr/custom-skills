#!/usr/bin/env python3
"""Read-only verification of a desktop list_projects rename transaction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class VerificationError(ValueError):
    pass


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def project_rows(payload):
    """Accept only desktop projects or one MCP text wrapper, never infer native IDs."""
    if isinstance(payload, dict) and payload.get("isError"):
        raise VerificationError("Snapshot is an error response")
    if isinstance(payload, dict) and "content" in payload and "projects" not in payload:
        candidates = []
        for block in payload["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    decoded = json.loads(block.get("text", ""))
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(decoded, dict) and isinstance(decoded.get("projects"), list):
                    candidates.append(decoded)
        if len(candidates) != 1:
            raise VerificationError("MCP response must contain one desktop projects snapshot")
        payload = candidates[0]
    if isinstance(payload, dict) and payload.get("isError"):
        raise VerificationError("Unwrapped snapshot is an error response")
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        raise VerificationError("Expected desktop list_projects {projects:[...]}; native data is a separate view")
    rows = payload["projects"]
    if not rows:
        raise VerificationError("Empty project snapshot is not a rename baseline")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise VerificationError("Project row must be an object")
        project_id = row.get("projectId")
        if not isinstance(project_id, str) or not project_id:
            raise VerificationError("Project row lacks a non-empty projectId")
        if project_id in result:
            raise VerificationError(f"Duplicate projectId: {project_id}")
        if not isinstance(row.get("label"), str):
            raise VerificationError(f"Project lacks a string label: {project_id}")
        result[project_id] = row
    return result


def verify(before_payload, after_payload, plan):
    before, after = project_rows(before_payload), project_rows(after_payload)
    if set(before) != set(after):
        raise VerificationError("Project ID set changed: wrong record view, creation or removal")
    if not isinstance(plan, list) or not plan:
        raise VerificationError("Plan must be a non-empty array")
    planned = {}
    for item in plan:
        if not isinstance(item, dict):
            raise VerificationError("Plan row must be an object")
        project_id = item.get("projectId")
        if not isinstance(project_id, str) or project_id not in before:
            raise VerificationError("Plan contains an unknown projectId")
        if project_id in planned:
            raise VerificationError(f"Duplicate plan projectId: {project_id}")
        original = before[project_id]
        if original.get("projectKind") != "local" or not isinstance(original.get("path"), str) or not original["path"]:
            raise VerificationError(f"Rename target must be a local project with a path: {project_id}")
        if item.get("label") != before[project_id]["label"]:
            raise VerificationError(f"Plan original name differs from baseline: {project_id}")
        target = item.get("target")
        if not isinstance(target, str) or not target.strip():
            raise VerificationError(f"Plan target must be a non-empty name: {project_id}")
        for identity in ("path", "hostId"):
            if identity in item and item[identity] != before[project_id].get(identity):
                raise VerificationError(f"Plan {identity} differs from baseline: {project_id}")
        planned[project_id] = target
    for project_id, old in before.items():
        new = after[project_id]
        expected = planned.get(project_id, old["label"])
        if new["label"] != expected:
            raise VerificationError(f"Saved name differs from plan: {project_id}")
        old_metadata = {key: value for key, value in old.items() if key != "label"}
        new_metadata = {key: value for key, value in new.items() if key != "label"}
        if old_metadata != new_metadata:
            raise VerificationError(f"Non-name project metadata changed: {project_id}")
    return {
        "status": "verified",
        "scope": "local_desktop_project_names_and_returned_metadata",
        "projects": len(before),
        "planned": len(planned),
        "renamed": sum(before[key]["label"] != after[key]["label"] for key in before),
        "ids_unchanged": True,
        "other_returned_metadata_unchanged": True,
        "not_verified": ["gui_rendering", "sidebar_sections", "thread_membership", "disk_files", "runtime_cwd"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(load_json(args.before), load_json(args.after), load_json(args.plan))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
