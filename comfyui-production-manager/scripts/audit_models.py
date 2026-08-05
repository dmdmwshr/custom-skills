#!/usr/bin/env python3
"""Create a read-only inventory of ComfyUI model files.

Example:
  python audit_models.py --root "C:/Users/12070/Documents/ComfyUI/models" \
    --output models-catalog.json

By default this records paths, category, size and timestamps. Use --sha256 when
you explicitly need content hashes; hashing multi-gigabyte model files is slow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sha256", action="store_true")
    args = parser.parse_args()

    files: list[dict[str, object]] = []
    for root_arg in args.root:
        root = root_arg.expanduser().resolve()
        if not root.exists():
            files.append({"root": str(root), "status": "missing_root"})
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            stat = path.stat()
            row: dict[str, object] = {
                "root": str(root),
                "relative_path": path.relative_to(root).as_posix(),
                "category": path.relative_to(root).parts[0]
                if len(path.relative_to(root).parts) > 1
                else "uncategorized",
                "filename": path.name,
                "suffix": path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
            if args.sha256:
                row["sha256"] = sha256_file(path)
            files.append(row)

    report = {
        "schema": "ComfyUIModelInventoryV1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hashes_included": args.sha256,
        "file_count": len([row for row in files if "relative_path" in row]),
        "files": files,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "schema", "generated_at", "hashes_included", "file_count",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
