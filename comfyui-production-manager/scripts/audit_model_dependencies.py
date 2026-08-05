#!/usr/bin/env python3
"""Audit model dependencies declared by ComfyUI workflow templates.

The official templates commonly place direct model links inside MarkdownNote
nodes.  This scanner reads those links first, then falls back to model filenames
in loader widgets.  It never downloads or moves a model.

Example:
  python audit_model_dependencies.py \
    --workflow-root "D:/12070/Documents/workspaces/Comfy-Codex-Workspace/workflows/模板库" \
    --model-root "D:/Comfy-Desktop/ComfyUI-Shared/models" \
    --model-root "C:/Users/12070/Documents/ComfyUI/models" \
    --output "D:/12070/Documents/workspaces/Comfy-Codex-Workspace/models/template_dependency_report.json"
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable

from model_paths import DEFAULT_SHARED_PATHS, discover_model_roots


MODEL_SUFFIXES = {".safetensors", ".ckpt", ".pth", ".pt", ".bin", ".gguf", ".onnx"}
MODEL_NAME_RE = re.compile(
    r"(?i)([^\\/\"'<>\s]+\.(?:safetensors|ckpt|pth|pt|bin|gguf|onnx))"
)
MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+\.(?:safetensors|ckpt|pth|pt|bin|gguf|onnx))\]\((https?://[^\s)]+)\)",
    re.IGNORECASE,
)
BOLD_HEADING_RE = re.compile(r"^\s*\*\*([^*]+)\*\*\s*$")
MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")

NODE_CATEGORY_HINTS = {
    "checkpoint": "checkpoints",
    "unet": "diffusion_models",
    "diffusion": "diffusion_models",
    "clipvision": "clip_vision",
    "clip_vision": "clip_vision",
    "cliploader": "text_encoders",
    "textencoder": "text_encoders",
    "vae": "vae",
    "lora": "loras",
    "controlnet": "controlnet",
    "upscale": "upscale_models",
    "sam": "detection",
}


def canonical_category(label: str) -> str:
    text = label.lower().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("diffusion", "unet", "dit", "transformer")):
        return "diffusion_models"
    if "checkpoint" in text or "ckpt" in text:
        return "checkpoints"
    if "lora" in text:
        return "loras"
    if "text" in text and any(token in text for token in ("encoder", "model", "clip", "llm")):
        return "text_encoders"
    if text in {"clip", "text_encoders"} or "text_encoder" in text:
        return "text_encoders"
    if "clip_vision" in text or "vision" in text:
        return "clip_vision"
    if "vae" in text:
        return "vae"
    if "control" in text or "adapter" in text:
        return "controlnet"
    if "upscale" in text or "super_resolution" in text:
        return "upscale_models"
    if any(token in text for token in ("sam", "segmentation", "detection", "pose", "depth")):
        return "detection"
    if any(token in text for token in ("audio", "vocoder", "wav2vec")):
        return "audio_encoders"
    return "unknown"


def node_category(node_type: str) -> str:
    normalized = node_type.lower().replace(" ", "")
    for token, category in NODE_CATEGORY_HINTS.items():
        if token in normalized:
            return category
    return "unknown"


def walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from walk_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_strings(nested)
    elif isinstance(value, str):
        yield value


def model_index(model_roots: list[Path]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for root in model_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                index[path.name.lower()].append(str(path))
    return dict(index)


def add_dependency(
    registry: dict[tuple[str, str], dict[str, Any]],
    *,
    filename: str,
    category: str,
    workflow: Path,
    node: dict[str, Any],
    source_url: str | None,
    evidence: str,
) -> None:
    filename = Path(filename).name
    if Path(filename).suffix.lower() not in MODEL_SUFFIXES:
        return
    key = (filename.lower(), category)
    row = registry.setdefault(
        key,
        {
            "filename": filename,
            "expected_category": category,
            "source_urls": [],
            "references": [],
        },
    )
    if source_url and source_url not in row["source_urls"]:
        row["source_urls"].append(source_url)
    reference = {
        "workflow": workflow.name,
        "node_id": node.get("id"),
        "node_type": node.get("type", node.get("class_type", "unknown")),
        "evidence": evidence,
    }
    if reference not in row["references"]:
        row["references"].append(reference)


def scan_workflow(workflow: Path, registry: dict[tuple[str, str], dict[str, Any]]) -> None:
    try:
        payload = json.loads(workflow.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    nodes = payload.get("nodes", [])
    if not isinstance(nodes, list):
        return

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", node.get("class_type", "")))
        values = list(walk_strings(node.get("widgets_values", [])))
        if node_type.lower() == "markdownnote":
            for note in values:
                heading = "unknown"
                for line in note.splitlines():
                    heading_match = BOLD_HEADING_RE.match(line) or MARKDOWN_HEADING_RE.match(line)
                    if heading_match:
                        heading = canonical_category(heading_match.group(1))
                    for match in MARKDOWN_LINK_RE.finditer(line):
                        add_dependency(
                            registry,
                            filename=match.group(1),
                            category=heading,
                            workflow=workflow,
                            node=node,
                            source_url=match.group(2),
                            evidence="markdown_model_link",
                        )
            continue
        category = node_category(node_type)
        for value in values:
            for match in MODEL_NAME_RE.finditer(value):
                add_dependency(
                    registry,
                    filename=match.group(1),
                    category=category,
                    workflow=workflow,
                    node=node,
                    source_url=None,
                    evidence="widget_or_note_filename",
                )


def classify(row: dict[str, Any], installed: dict[str, list[str]]) -> str:
    filename = row["filename"]
    paths = installed.get(filename.lower(), [])
    row["installed_paths"] = paths
    if filename.lower() == "model.safetensors" and not row["source_urls"]:
        return "unresolved"
    if paths:
        expected_segment = f"\\{row['expected_category']}\\"
        row["category_match_paths"] = [
            path for path in paths if expected_segment.lower() in path.lower()
        ]
        return "duplicate_installed" if len(paths) > 1 else "installed"
    if row["expected_category"] == "unknown" or not row["source_urls"]:
        return "unresolved"
    return "missing"


def collapse_unknown_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avoid reporting one Markdown link again as an ``unknown`` widget value."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["filename"].lower()].append(row)
    collapsed: list[dict[str, Any]] = []
    for group in grouped.values():
        known = [row for row in group if row["expected_category"] != "unknown"]
        unknown = [row for row in group if row["expected_category"] == "unknown"]
        if len(known) == 1:
            # Keep the complete workflow reference list without manufacturing a
            # second download candidate for the same exact filename.
            target = known[0]
            for row in unknown:
                for url in row["source_urls"]:
                    if url not in target["source_urls"]:
                        target["source_urls"].append(url)
                for reference in row["references"]:
                    if reference not in target["references"]:
                        target["references"].append(reference)
        if known:
            collapsed.extend(known)
        else:
            collapsed.extend(unknown)
    return collapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-root", action="append", type=Path, required=True)
    parser.add_argument("--model-root", action="append", type=Path)
    parser.add_argument("--shared-paths-config", type=Path, default=DEFAULT_SHARED_PATHS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    registry: dict[tuple[str, str], dict[str, Any]] = {}
    workflow_files: list[Path] = []
    for root_arg in args.workflow_root:
        root = root_arg.expanduser().resolve()
        if not root.exists():
            continue
        for workflow in sorted(root.rglob("*.json")):
            if workflow.name in {"catalog.json", "curated_registry.json"}:
                continue
            workflow_files.append(workflow)
            scan_workflow(workflow, registry)

    model_roots = args.model_root or [
        row.path for row in discover_model_roots(args.shared_paths_config)
    ]
    resolved_model_roots = [root.expanduser().resolve() for root in model_roots]
    installed = model_index(resolved_model_roots)
    rows = []
    for row in registry.values():
        row["status"] = classify(row, installed)
        row["reference_count"] = len(row["references"])
        rows.append(row)
    rows = collapse_unknown_duplicates(rows)
    for row in rows:
        row["reference_count"] = len(row["references"])
    grouped_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_by_name[row["filename"].lower()].append(row)
    for group in grouped_by_name.values():
        known_categories = {row["expected_category"] for row in group if row["expected_category"] != "unknown"}
        if len(known_categories) > 1:
            for row in group:
                if row["status"] == "missing":
                    row["status"] = "ambiguous_category"
    rows.sort(key=lambda item: (item["status"], item["filename"].lower(), item["expected_category"]))
    summary = {status: sum(row["status"] == status for row in rows) for status in (
        "installed", "duplicate_installed", "missing", "ambiguous_category", "unresolved"
    )}
    candidates = [
        {
            "filename": row["filename"],
            "category": row["expected_category"],
            "source_url": row["source_urls"][0],
            "workflow_count": len({ref["workflow"] for ref in row["references"]}),
            "reference_count": len(row["references"]),
            "workflows": sorted({ref["workflow"] for ref in row["references"]}),
        }
        for row in rows
        if row["status"] == "missing" and len(row["source_urls"]) == 1
    ]
    candidates.sort(key=lambda item: (-item["workflow_count"], -item["reference_count"], item["filename"].lower()))
    report = {
        "schema": "ComfyUIModelDependencyReportV1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_roots": [str(root.expanduser().resolve()) for root in args.workflow_root],
        "model_roots": [str(root) for root in resolved_model_roots],
        "workflow_count": len(workflow_files),
        "summary": {"dependency_count": len(rows), **summary},
        "dependencies": rows,
        "download_candidates": candidates,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({
        "schema": report["schema"],
        "workflow_count": report["workflow_count"],
        "summary": report["summary"],
        "download_candidate_count": len(report["download_candidates"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
