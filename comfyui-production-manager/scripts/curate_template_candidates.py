#!/usr/bin/env python3
"""Compare candidate workflows with the unified template library.

This is a review tool, not a deletion tool. It reports semantic neighbours so a
human can decide whether a hand-downloaded workflow adds a learning angle.

Example:
  python curate_template_candidates.py \
    --template-root "D:/.../workflows/模板库" \
    --candidate-root "D:/.../incoming" \
    --output curation-review.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)


def load(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        return None, "not a workflow JSON"
    return payload, None


def signature(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for node in payload.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", ""))
        if node_type:
            values.add(f"type:{node_type}")
        if UUID_RE.match(node_type):
            values.add(f"subgraph:{node_type}")
        for widget in node.get("widgets_values") or []:
            if isinstance(widget, (str, int, float, bool)):
                text = str(widget)
                if len(text) <= 240 and (
                    ".safetensors" in text
                    or "video" in text.lower()
                    or "audio" in text.lower()
                    or "image" in text.lower()
                ):
                    values.add(f"widget:{text}")
    return values


def row(path: Path, root: Path) -> dict[str, Any] | None:
    payload, error = load(path)
    if payload is None:
        return {"path": str(path), "status": "invalid", "error": error}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "status": "workflow",
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "node_count": len(payload["nodes"]),
        "signature": sorted(signature(payload)),
    }


def scan(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*.json")):
        if path.name in {"catalog.json", "curated_registry.json"}:
            continue
        item = row(path, root)
        if item is not None:
            rows.append(item)
    return rows


def compare(candidate: dict[str, Any], library: list[dict[str, Any]]) -> dict[str, Any]:
    if candidate.get("status") != "workflow":
        return {"candidate": candidate, "decision_hint": "invalid_candidate", "matches": []}
    candidate_sig = set(candidate["signature"])
    matches: list[dict[str, Any]] = []
    for item in library:
        if item.get("status") != "workflow":
            continue
        other_sig = set(item["signature"])
        union = candidate_sig | other_sig
        score = len(candidate_sig & other_sig) / len(union) if union else 0.0
        matches.append({
            "path": item["path"],
            "relative_path": item["relative_path"],
            "jaccard": round(score, 4),
            "same_canonical_json": candidate["canonical_sha256"] == item["canonical_sha256"],
            "node_count": item["node_count"],
        })
    matches.sort(key=lambda item: (item["same_canonical_json"], item["jaccard"]), reverse=True)
    best = matches[0] if matches else None
    hint = "review_learning_value"
    if best and (best["same_canonical_json"] or best["jaccard"] >= 0.9):
        hint = "likely_duplicate_check_references_then_delete_if_authorized"
    elif best and best["jaccard"] < 0.5:
        hint = "likely_unique_candidate_register_if_learning_value_exists"
    return {"candidate": candidate, "decision_hint": hint, "matches": matches[:5]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    template_root = args.template_root.expanduser().resolve()
    candidate_root = args.candidate_root.expanduser().resolve()
    library = scan(template_root)
    candidates = scan(candidate_root)
    report = {
        "schema": "ComfyUITemplateCurationReviewV1",
        "template_root": str(template_root),
        "candidate_root": str(candidate_root),
        "library_count": len(library),
        "candidate_count": len(candidates),
        "reviews": [compare(item, library) for item in candidates],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("schema", "library_count", "candidate_count")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
