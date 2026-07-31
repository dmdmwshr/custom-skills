from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(value: Any) -> Any:
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "length": len(value)}
    return type(value).__name__


def inspect_pth(path: Path) -> dict[str, Any]:
    import torch

    payload = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a dictionary checkpoint, got {type(payload).__name__}")

    details: dict[str, Any] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "top_level_keys": sorted(str(key) for key in payload.keys()),
    }
    for key in ("version", "sr", "f0", "model_name", "epoch", "step", "embedder_model"):
        if key in payload:
            details[key] = payload[key]
    if "config" in payload:
        details["config"] = summarize(payload["config"])
    if "weight" in payload:
        details["weight"] = summarize(payload["weight"])
    return details


def inspect_index(path: Path) -> dict[str, Any]:
    import faiss

    index = faiss.read_index(str(path))
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "dimension": int(index.d),
        "vectors": int(index.ntotal),
        "index_type": type(index).__name__,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely inspect an Applio/RVC inference model."
    )
    parser.add_argument("--pth", required=True, type=Path)
    parser.add_argument("--index", type=Path)
    args = parser.parse_args()

    pth = args.pth.resolve(strict=True)
    if pth.suffix.lower() != ".pth":
        raise ValueError("--pth must point to a .pth file")

    result: dict[str, Any] = {"pth": inspect_pth(pth)}
    if args.index:
        index_path = args.index.resolve(strict=True)
        if index_path.suffix.lower() != ".index":
            raise ValueError("--index must point to a .index file")
        result["index"] = inspect_index(index_path)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
