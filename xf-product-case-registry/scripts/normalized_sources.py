"""Index of image->PDF normalizations (ImageNormalizationV1) kept next to the case workspace.

The index maps every normalized PDF to the exact source image (digest, size, pixel size, kind and
source path) so compose, local-verify and the later deep check can prove the association and
reject a swapped image or a swapped PDF.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__:
    from . import image_pdf
else:
    import image_pdf

INDEX_NAME = "image-normalization.json"
SCHEMA_VERSION = "ImageNormalizationV1"


def load(work: Path) -> dict[str, dict[str, Any]]:
    path = work / INDEX_NAME
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schemaVersion") != SCHEMA_VERSION or not isinstance(value.get("items"), list):
        raise ValueError("image-normalization.json 版本或结构不合法")
    return {item["outputRelativePath"]: item for item in value["items"]}


def save(work: Path, items: dict[str, dict[str, Any]], now: str) -> None:
    path = work / INDEX_NAME
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "items": [items[key] for key in sorted(items)],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def verify_item(
    work: Path, item: dict[str, Any], source_bytes: Callable[[dict[str, Any]], bytes]
) -> list[str]:
    """Reasons the PDF no longer faithfully carries exactly the recorded source image."""
    target = work / item["outputRelativePath"]
    if not target.is_file():
        return [f"规范化 PDF 缺失：{item['outputRelativePath']}"]
    pdf = target.read_bytes()
    reasons = []
    if image_pdf.sha256(pdf) != item.get("outputSha256"):
        reasons.append(f"规范化 PDF 摘要与转换记录不一致：{item['outputRelativePath']}")
    try:
        source = source_bytes(item)
    except OSError:
        return reasons + [f"转换源图片缺失：{item.get('sourceRelativePath')}"]
    mapping = {
        k: item[k]
        for k in ("sourceSha256", "sourceSizeBytes", "pixelWidth", "pixelHeight", "pageSizePt")
    }
    reasons += image_pdf.verify_pdf(pdf, source, mapping)
    return reasons
