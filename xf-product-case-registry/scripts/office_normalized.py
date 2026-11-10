"""Word(.docx/.doc) 原件 → 规范 PDF 的绑定记录（OfficeNormalizationV1）。

与图片规范化并列保存在案卷工作区。

转换由操作者用本机 Office 引擎完成并逐页目视核对；本模块不声称渲染可机器复现，只做三件事：
1. 源文件安全识别（DOCX 走 OPC 包检查，DOC 只认 OLE2 容器且拒绝带宏标记者）；
2. 转换结果 PDF 的结构检查（页数 ≥ 1、无加密、无脚本/启动/内嵌文件动作）；
3. 把源摘要、PDF 摘要、页数、转换器与目视核对说明写入 office-normalization.json，
   后续 compose / local-verify / 归档深度复核逐项重核，源文件或 PDF 被换即拒绝。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pypdf import PdfReader

if __package__:
    from .office_package_guard import is_safe_docx_package
else:
    from office_package_guard import is_safe_docx_package

INDEX_NAME = "office-normalization.json"
SCHEMA_VERSION = "OfficeNormalizationV1"
MODE = "EXTERNAL_RENDER_RECORDED"
MAX_SOURCE_BYTES = 256 * 1024 * 1024
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
FORMATS = {".docx": "DOCX", ".doc": "DOC"}
# DOC 的 VBA/宏存储名（UTF-16LE 存放在 OLE 目录里）
_MACRO_MARKERS = tuple(name.encode("utf-16le") for name in ("_VBA_PROJECT", "VBA", "Macros"))
_PDF_ACTIVE = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|SubmitForm|ImportData)\b"
)


class OfficeNormalizationError(ValueError):
    """Source or PDF cannot be bound faithfully."""


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def source_format(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        raise OfficeNormalizationError(f"只支持 .docx/.doc 原件：{name}")
    return FORMATS[suffix]


def inspect_source(data: bytes, name: str) -> dict[str, Any]:
    """Return the recorded facts of a safe Word original; raise otherwise."""
    kind = source_format(name)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise OfficeNormalizationError("Word 原件为空或超过大小上限")
    if kind == "DOCX":
        if not is_safe_docx_package(io.BytesIO(data), filename=Path(name).name):
            raise OfficeNormalizationError(
                "DOCX 不是可识别的非宏 Word 包（含宏/嵌入对象/外部加载或结构不符）"
            )
    else:
        if not data.startswith(OLE_MAGIC):
            raise OfficeNormalizationError("DOC 不是 OLE2 容器")
        if any(marker in data for marker in _MACRO_MARKERS):
            raise OfficeNormalizationError("DOC 含宏存储标记，不做转换")
    return {"sourceFormat": kind, "sourceSha256": sha256(data), "sourceSizeBytes": len(data)}


def inspect_pdf(data: bytes) -> int:
    """Return the page count of a plain, inactive PDF; raise otherwise."""
    if not data.startswith(b"%PDF-"):
        raise OfficeNormalizationError("转换结果不是 PDF")
    if _PDF_ACTIVE.search(data):
        raise OfficeNormalizationError("转换结果 PDF 含脚本/启动/内嵌文件动作")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise OfficeNormalizationError("转换结果 PDF 已加密")
        pages = len(reader.pages)
    except OfficeNormalizationError:
        raise
    except Exception as error:  # pypdf 对损坏文件抛出多种异常
        raise OfficeNormalizationError(f"转换结果 PDF 无法读取：{error}") from error
    if pages < 1:
        raise OfficeNormalizationError("转换结果 PDF 没有页面")
    return pages


def build_mapping(
    source: bytes,
    name: str,
    pdf: bytes,
    *,
    converter_name: str,
    converter_version: str,
    rendering_check: str,
) -> dict[str, Any]:
    if not converter_name.strip() or not converter_version.strip():
        raise OfficeNormalizationError("必须记录转换器名称与版本")
    if len(rendering_check.strip()) < 8:
        raise OfficeNormalizationError("必须记录逐页目视核对说明（至少 8 个字符）")
    facts = inspect_source(source, name)
    pages = inspect_pdf(pdf)
    return {
        **facts,
        "outputSha256": sha256(pdf),
        "outputPageCount": pages,
        "converter": {
            "name": converter_name.strip(),
            "version": converter_version.strip(),
            "mode": MODE,
        },
        "renderingCheck": rendering_check.strip(),
    }


def verify_pdf(pdf: bytes, source: bytes, item: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        pages = inspect_pdf(pdf)
        if pages != item.get("outputPageCount"):
            reasons.append("规范化 PDF 页数与转换记录不一致")
    except OfficeNormalizationError as error:
        reasons.append(str(error))
    if sha256(pdf) != item.get("outputSha256"):
        reasons.append("规范化 PDF 摘要与转换记录不一致")
    try:
        facts = inspect_source(source, item.get("sourceRelativePath") or "")
        same = facts["sourceSha256"] == item.get("sourceSha256")
        if not same or facts["sourceSizeBytes"] != item.get("sourceSizeBytes"):
            reasons.append("Word 源文件与转换记录不一致")
    except OfficeNormalizationError as error:
        reasons.append(str(error))
    return reasons


def load(work: Path) -> dict[str, dict[str, Any]]:
    path = work / INDEX_NAME
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schemaVersion") != SCHEMA_VERSION or not isinstance(value.get("items"), list):
        raise ValueError("office-normalization.json 版本或结构不合法")
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
    """Reasons the PDF no longer faithfully carries the recorded Word original binding."""
    target = work / item["outputRelativePath"]
    if not target.is_file():
        return [f"规范化 PDF 缺失：{item['outputRelativePath']}"]
    try:
        source = source_bytes(item)
    except OSError:
        return [f"转换源 Word 文件缺失：{item.get('sourceRelativePath')}"]
    label = item["outputRelativePath"]
    return [f"{reason}：{label}" for reason in verify_pdf(target.read_bytes(), source, item)]
