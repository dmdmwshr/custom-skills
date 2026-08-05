#!/usr/bin/env python3
"""Discover Comfy Desktop model roots and select a safe download target.

The module deliberately uses only the Python standard library so that it can be
run by the ComfyUI virtual environment or a normal Python installation.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import re
import shutil


DEFAULT_SHARED_PATHS = Path(
    r"C:\Users\12070\AppData\Roaming\Comfy Desktop\shared_model_paths.yaml"
)
DEFAULT_PRIMARY_ROOT = Path(r"D:\Comfy-Desktop\ComfyUI-Shared\models")
DEFAULT_FALLBACK_ROOT = Path(r"C:\Users\12070\Documents\ComfyUI\models")
DEFAULT_PRIMARY_THRESHOLD_GIB = 200
DEFAULT_STAGING_DIRNAME = ".model-download-staging"


@dataclass(frozen=True)
class ModelRoot:
    path: Path
    is_default: bool


@dataclass(frozen=True)
class StorageSelection:
    model_root: Path
    staging_root: Path
    free_bytes: int
    reason: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def discover_model_roots(config_path: Path = DEFAULT_SHARED_PATHS) -> list[ModelRoot]:
    """Read Comfy Desktop's generated shared-model-paths YAML without PyYAML.

    Only ``base_path`` and ``is_default`` are needed here.  If the generated
    file is absent or incomplete, return the known local Comfy Desktop defaults.
    """

    if not config_path.is_file():
        return [
            ModelRoot(DEFAULT_PRIMARY_ROOT, True),
            ModelRoot(DEFAULT_FALLBACK_ROOT, False),
        ]

    section_re = re.compile(r"^[A-Za-z0-9_.-]+:\s*$")
    key_re = re.compile(r"^\s{2}('?[^':]+'?)\s*:\s*(.*?)\s*$")
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw in config_path.read_text(encoding="utf-8-sig").splitlines():
        if not raw or raw.lstrip().startswith("#"):
            continue
        if section_re.match(raw):
            current = {"base_path": None, "is_default": False}
            rows.append(current)
            continue
        if current is None:
            continue
        match = key_re.match(raw)
        if not match:
            continue
        key = _unquote(match.group(1))
        value = _unquote(match.group(2))
        if key == "base_path":
            current["base_path"] = value
        elif key == "is_default":
            current["is_default"] = value.lower() == "true"

    roots = [
        ModelRoot(Path(str(row["base_path"])), bool(row["is_default"]))
        for row in rows
        if row.get("base_path")
    ]
    if not roots:
        return [
            ModelRoot(DEFAULT_PRIMARY_ROOT, True),
            ModelRoot(DEFAULT_FALLBACK_ROOT, False),
        ]
    return roots


def choose_primary_and_fallback(
    config_path: Path = DEFAULT_SHARED_PATHS,
    primary_override: Path | None = None,
    fallback_override: Path | None = None,
) -> tuple[Path, Path]:
    if primary_override and fallback_override:
        return primary_override, fallback_override
    roots = discover_model_roots(config_path)
    primary = primary_override or next(
        (row.path for row in roots if row.is_default), roots[0].path
    )
    fallback = fallback_override or next(
        (row.path for row in roots if row.path != primary), DEFAULT_FALLBACK_ROOT
    )
    return primary, fallback


def free_bytes(path: Path) -> int:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def ensure_safe_category(category: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", category):
        raise ValueError(f"非法模型类别：{category!r}")
    return category


def choose_storage(
    *,
    primary_root: Path,
    fallback_root: Path,
    required_bytes: int = 0,
    primary_threshold_gib: int = DEFAULT_PRIMARY_THRESHOLD_GIB,
) -> StorageSelection:
    """Choose D shared storage unless it is under the user-approved threshold.

    ``required_bytes`` includes the model staging file, one transfer chunk and
    an operating reserve.  A fallback is also selected when the primary still
    exceeds the threshold but cannot physically hold the requested model.
    """

    threshold_bytes = primary_threshold_gib * 1024**3
    primary_free = free_bytes(primary_root)
    fallback_free = free_bytes(fallback_root)

    if primary_free >= threshold_bytes and primary_free >= required_bytes:
        return StorageSelection(
            model_root=primary_root,
            staging_root=primary_root.parent / DEFAULT_STAGING_DIRNAME,
            free_bytes=primary_free,
            reason="primary_has_threshold_and_capacity",
        )
    if fallback_free >= required_bytes:
        reason = (
            "primary_below_threshold"
            if primary_free < threshold_bytes
            else "primary_insufficient_for_requested_model"
        )
        return StorageSelection(
            model_root=fallback_root,
            staging_root=fallback_root.parent / DEFAULT_STAGING_DIRNAME,
            free_bytes=fallback_free,
            reason=reason,
        )
    raise RuntimeError(
        "本机可用空间不足："
        f"D 默认根目录剩余 {primary_free} 字节，"
        f"C 备用根目录剩余 {fallback_free} 字节，"
        f"本次至少需要 {required_bytes} 字节。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-paths-config", type=Path, default=DEFAULT_SHARED_PATHS)
    parser.add_argument("--required-gib", type=float, default=0.0)
    parser.add_argument("--primary-threshold-gib", type=int, default=DEFAULT_PRIMARY_THRESHOLD_GIB)
    args = parser.parse_args()
    roots = discover_model_roots(args.shared_paths_config)
    primary, fallback = choose_primary_and_fallback(args.shared_paths_config)
    required_bytes = int(args.required_gib * 1024**3)
    selection = choose_storage(
        primary_root=primary,
        fallback_root=fallback,
        required_bytes=required_bytes,
        primary_threshold_gib=args.primary_threshold_gib,
    )
    print(json.dumps({
        "schema": "ComfyUIModelStorageLayoutV1",
        "shared_paths_config": str(args.shared_paths_config),
        "roots": [
            {
                "path": str(row.path),
                "is_default": row.is_default,
                "exists": row.path.exists(),
                "free_bytes": free_bytes(row.path),
            }
            for row in roots
        ],
        "selection": {
            "model_root": str(selection.model_root),
            "staging_root": str(selection.staging_root),
            "free_bytes": selection.free_bytes,
            "reason": selection.reason,
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
