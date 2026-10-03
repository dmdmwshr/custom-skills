"""只读识别可作为案卷原件保留的非宏 DOCX；不展开或执行内部部件。

扩展名、OPC 内容类型和关系、Word 主文档必须共同成立。识别范围为常见
Word XML 部件及实际图片；宏、嵌入包/OLE/控件、外部加载和未知二进制均拒绝。
路径和流接口共用同一检查，流检查结束后恢复原读指针。
"""

from __future__ import annotations

import io
import math
import re
import stat
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

MAX_DOCX_ENTRIES = 500
MAX_DOCX_PART_BYTES = 512 * 1024 * 1024
MAX_DOCX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOCX_RATIO = 200.0
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_XML_NODES = 100_000
MAX_XML_DEPTH = 128
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
OFFICE_REL_PREFIXES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/",
)
PASSIVE_REL_NAMES = {
    "officeDocument",
    "extended-properties",
    "custom-properties",
    "image",
    "theme",
    "settings",
    "fontTable",
    "styles",
    "numbering",
    "comments",
    "header",
    "footer",
    "footnotes",
    "endnotes",
    "glossaryDocument",
    "customXml",
    "customXmlProps",
    "hyperlink",
}
PASSIVE_REL_TYPES = {
    prefix + name for prefix in OFFICE_REL_PREFIXES for name in PASSIVE_REL_NAMES
} | {
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
}
MAIN_REL_TYPES = {prefix + "officeDocument" for prefix in OFFICE_REL_PREFIXES}
HYPERLINK_REL_TYPES = {prefix + "hyperlink" for prefix in OFFICE_REL_PREFIXES}
XML_CONTENT_TYPES = {
    "application/xml",
    "text/xml",
    MAIN_CONTENT_TYPE,
    REL_CONTENT_TYPE,
    "application/vnd.openxmlformats-package.core-properties+xml",
    "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    "application/vnd.openxmlformats-officedocument.customXmlProperties+xml",
    "application/vnd.openxmlformats-officedocument.theme+xml",
    *(
        "application/vnd.openxmlformats-officedocument.wordprocessingml." + part + "+xml"
        for part in (
            "styles",
            "numbering",
            "comments",
            "settings",
            "fontTable",
            "header",
            "footer",
            "footnotes",
            "endnotes",
            "document.glossary",
        )
    ),
}
IMAGE_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class OfficePackageError(ValueError):
    """文档不属于当前允许的安全 DOCX 结构。"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OfficePackageError(message)


def _safe_name(name: str, *, directory: bool = False) -> tuple[str, str]:
    _require(bool(name) and not any(char in name for char in "\\\0:%"), "DOCX 包含不安全部件路径")
    raw = name[:-1] if directory and name.endswith("/") else name
    parts = raw.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts), "DOCX 包含路径穿越或空路径段")
    normalized = [unicodedata.normalize("NFC", part) for part in parts]
    for part in normalized:
        _require(
            not part.endswith((".", " "))
            and all(ord(char) >= 32 and ord(char) != 127 for char in part),
            "DOCX 包含不安全路径段",
        )
        _require(
            part.split(".", 1)[0].rstrip(" .").upper() not in DEVICE_NAMES,
            "DOCX 包含 Windows 设备名路径",
        )
    result = "/".join(normalized)
    return result, result.casefold()


@contextmanager
def _reader(source: str | Path | BinaryIO, filename: str | None) -> Iterator[BinaryIO]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        _require(path.suffix.casefold() == ".docx", "仅允许 .docx 文档")
        _require(
            filename is None or PurePosixPath(filename).suffix.casefold() == ".docx",
            "文档扩展名不匹配",
        )
        with path.open("rb") as stream:
            yield stream
    else:
        _require(
            filename is not None and PurePosixPath(filename).suffix.casefold() == ".docx",
            "流识别必须提供 .docx 文件名",
        )
        position = source.tell()
        try:
            source.seek(0)
            yield source
        finally:
            source.seek(position)


class _SafeXmlBuilder(ET.TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = self.depth = 0

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise OfficePackageError("DOCX XML 不允许 DTD 或实体声明")

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.nodes += 1
        self.depth += 1
        _require(
            self.nodes <= MAX_XML_NODES and self.depth <= MAX_XML_DEPTH, "DOCX XML 结构超过安全上限"
        )
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        result = super().end(tag)
        self.depth -= 1
        return result


def _xml(data: bytes) -> ET.Element:
    _require(len(data) <= MAX_XML_BYTES, "DOCX XML 部件超过安全上限")
    root = ET.fromstring(data, parser=ET.XMLParser(target=_SafeXmlBuilder()))
    for item in root.iter():
        for namespace in WORD_NAMESPACES:
            if item.tag in {
                f"{{{namespace}}}{name}" for name in ("altChunk", "object", "control", "subDoc")
            }:
                raise OfficePackageError("DOCX 包含嵌入对象或外部内容")
    return root


def _content_types(root: ET.Element, files: dict[str, zipfile.ZipInfo]) -> dict[str, str]:
    _require(root.tag == f"{{{CONTENT_NS}}}Types", "DOCX 缺少真实内容类型表")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for item in root:
        content_type = item.get("ContentType", "")
        _require(
            "/" in content_type and not any(char.isspace() for char in content_type),
            "DOCX 内容类型不合法",
        )
        _require(
            not any(
                token in content_type.casefold()
                for token in ("macroenabled", "vba", "oleobject", "activex")
            ),
            "DOCX 不允许宏或活动对象",
        )
        if item.tag == f"{{{CONTENT_NS}}}Default":
            extension = item.get("Extension", "").casefold()
            _require(
                bool(extension)
                and not any(char in extension for char in ".\\/:%")
                and not any(char.isspace() for char in extension),
                "DOCX 默认内容扩展名不合法",
            )
            _require(extension not in defaults, "DOCX 内容类型存在重复扩展名")
            defaults[extension] = content_type
        elif item.tag == f"{{{CONTENT_NS}}}Override":
            name = item.get("PartName", "")
            _require(
                name.startswith("/") and not name.startswith("//"), "DOCX 内容类型部件名不合法"
            )
            name, _ = _safe_name(name[1:])
            _require(name in files and name not in overrides, "DOCX 内容类型部件缺失或重复")
            overrides[name] = content_type
        else:
            raise OfficePackageError("DOCX 内容类型表包含未知元素")
    result = {}
    for name in files:
        if name == "[Content_Types].xml":
            continue
        extension = "rels" if name.endswith(".rels") else PurePosixPath(name).suffix[1:].casefold()
        content_type = overrides.get(name, defaults.get(extension, ""))
        _require(bool(content_type), "DOCX 部件缺少内容类型")
        result[name] = content_type
    _require(list(result.values()).count(MAIN_CONTENT_TYPE) == 1, "DOCX 必须包含唯一非宏主文档")
    return result


def _relationship_owner(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    path = PurePosixPath(name)
    _require(path.parent.name == "_rels" and path.name != ".rels", "DOCX 关系部件位置不合法")
    return (path.parent.parent / path.name[:-5]).as_posix()


def _internal_target(owner: str, target: str) -> str:
    _require(bool(target) and not any(ord(char) < 32 for char in target), "DOCX 关系目标不合法")
    _require(re.search(r"%(?:2e|2f|5c|00)", target, re.I) is None, "DOCX 关系包含编码路径穿越")
    uri = urlsplit(target)
    _require(
        not any((uri.scheme, uri.netloc, uri.query, uri.fragment)), "DOCX 内部关系不能指向外部资源"
    )
    decoded = unquote(uri.path, encoding="utf-8", errors="strict")
    base = [] if decoded.startswith("/") else list(PurePosixPath(owner).parent.parts)
    for part in decoded.lstrip("/").split("/"):
        if part == "..":
            _require(bool(base), "DOCX 关系路径越出包根")
            base.pop()
        elif part == ".":
            continue
        else:
            _require(bool(part), "DOCX 关系包含空路径段")
            base.append(part)
    return _safe_name("/".join(base))[0]


def _image_matches(data: bytes, content_type: str) -> bool:
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/bmp": (b"BM",),
        "image/tiff": (b"II*\0", b"MM\0*"),
    }
    return data.startswith(signatures[content_type])


def inspect_docx_package(
    source: str | Path | BinaryIO,
    *,
    filename: str | None = None,
    max_entries: int = MAX_DOCX_ENTRIES,
    max_member_bytes: int = MAX_DOCX_PART_BYTES,
    max_total_bytes: int = MAX_DOCX_TOTAL_BYTES,
    max_ratio: float = MAX_DOCX_RATIO,
) -> dict[str, Any]:
    """返回部件数量和大小摘要；未知或不安全结构抛出 OfficePackageError。

    调用方可收紧限制，不能放大本模块的 DOCX 上限；不返回正文或内部二进制。
    """
    limits = (max_entries, max_member_bytes, max_total_bytes)
    _require(
        all(
            isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 for limit in limits
        ),
        "DOCX 资源上限必须为正整数",
    )
    _require(
        isinstance(max_ratio, (int, float)) and math.isfinite(max_ratio) and max_ratio > 0,
        "DOCX 压缩比上限不合法",
    )
    max_entries = min(max_entries, MAX_DOCX_ENTRIES)
    max_member_bytes = min(max_member_bytes, MAX_DOCX_PART_BYTES)
    max_total_bytes = min(max_total_bytes, MAX_DOCX_TOTAL_BYTES)
    max_ratio = min(max_ratio, MAX_DOCX_RATIO)
    try:
        with _reader(source, filename) as stream:
            _require(stream.read(4) == b"PK\x03\x04", "DOCX 不允许自解压或前置数据")
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                _require(0 < len(infos) <= max_entries, "DOCX 条目数超过安全上限或为空")
                _require(min(info.header_offset for info in infos) == 0, "DOCX 包含前置数据")
                files: dict[str, zipfile.ZipInfo] = {}
                seen: set[str] = set()
                file_keys: set[str] = set()
                total = 0
                for info in infos:
                    name, key = _safe_name(info.orig_filename, directory=info.is_dir())
                    _require(key not in seen, "DOCX 部件存在大小写或 Unicode 重复路径")
                    _require(
                        not any(
                            parent.as_posix().casefold() in file_keys
                            for parent in PurePosixPath(name).parents
                        ),
                        "DOCX 文件与子路径发生冲突",
                    )
                    _require(
                        info.is_dir() or not any(item.startswith(key + "/") for item in seen),
                        "DOCX 文件与已有子路径发生冲突",
                    )
                    seen.add(key)
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    _require(
                        kind in {0, stat.S_IFREG, stat.S_IFDIR}
                        and not (info.external_attr & 0x400),
                        "DOCX 不允许链接、重解析点或非常规部件",
                    )
                    _require(
                        not (info.flag_bits & 0x41)
                        and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                        "DOCX 不允许加密或非标准压缩",
                    )
                    if info.is_dir():
                        _require(
                            info.file_size == 0 and kind != stat.S_IFREG, "DOCX 目录条目不合法"
                        )
                        continue
                    _require(
                        kind != stat.S_IFDIR and not (info.external_attr & 0x10),
                        "DOCX 文件条目不合法",
                    )
                    _require(
                        not any(
                            part.casefold() in {"embeddings", "activex"}
                            for part in PurePosixPath(name).parts
                        )
                        and "vba" not in name.casefold(),
                        "DOCX 不允许宏或嵌入对象",
                    )
                    _require(info.file_size <= max_member_bytes, "DOCX 单个部件超过安全上限")
                    total += info.file_size
                    _require(total <= max_total_bytes, "DOCX 总展开量超过安全上限")
                    _require(
                        info.file_size == 0 or info.compress_size > 0, "DOCX 包含异常零压缩大小条目"
                    )
                    _require(
                        info.file_size / max(info.compress_size, 1) <= max_ratio,
                        "DOCX 压缩比异常，疑似压缩炸弹",
                    )
                    file_keys.add(key)
                    files[name] = info
                _require(
                    "[Content_Types].xml" in files and "_rels/.rels" in files,
                    "DOCX 缺少 OPC 必要部件",
                )
                xml_parts: dict[str, ET.Element] = {}
                _require(
                    files["[Content_Types].xml"].file_size <= MAX_XML_BYTES,
                    "DOCX XML 部件超过安全上限",
                )
                content_data = archive.read(files["[Content_Types].xml"])
                types = _content_types(_xml(content_data), files)
                for name, content_type in types.items():
                    suffix = (
                        ".rels" if name.endswith(".rels") else PurePosixPath(name).suffix.casefold()
                    )
                    if suffix in {".xml", ".rels"}:
                        _require(
                            content_type in XML_CONTENT_TYPES, "DOCX 包含不允许的 XML 部件类型"
                        )
                        _require(
                            suffix != ".rels" or content_type == REL_CONTENT_TYPE,
                            "DOCX 关系部件内容类型不匹配",
                        )
                        _require(
                            files[name].file_size <= MAX_XML_BYTES, "DOCX XML 部件超过安全上限"
                        )
                    else:
                        _require(
                            IMAGE_TYPES.get(suffix) == content_type, "DOCX 包含未知或嵌入二进制部件"
                        )
                    data = archive.read(files[name])
                    _require(len(data) == files[name].file_size, "DOCX 部件实际大小与目录不一致")
                    _require(not zipfile.is_zipfile(io.BytesIO(data)), "DOCX 内部仍包含嵌套 ZIP")
                    if suffix in {".xml", ".rels"}:
                        xml_parts[name] = _xml(data)
                    else:
                        _require(_image_matches(data, content_type), "DOCX 图片内容与类型不匹配")
                main_parts: list[str] = []
                for name, root in xml_parts.items():
                    if not name.endswith(".rels"):
                        continue
                    owner = _relationship_owner(name)
                    _require(not owner or owner in types, "DOCX 关系来源部件缺失")
                    _require(root.tag == f"{{{REL_NS}}}Relationships", "DOCX 关系结构不合法")
                    ids: set[str] = set()
                    for rel in root:
                        relation_id, relation_type, target = (
                            rel.get(field, "") for field in ("Id", "Type", "Target")
                        )
                        _require(
                            rel.tag == f"{{{REL_NS}}}Relationship"
                            and not len(rel)
                            and bool(relation_id)
                            and relation_id not in ids
                            and relation_type in PASSIVE_REL_TYPES,
                            "DOCX 关系重复、未知或包含活动对象",
                        )
                        ids.add(relation_id)
                        mode = rel.get("TargetMode", "Internal")
                        _require(mode in {"Internal", "External"}, "DOCX 关系模式不合法")
                        if mode == "External":
                            uri = urlsplit(target)
                            _require(
                                relation_type in HYPERLINK_REL_TYPES
                                and uri.scheme.casefold() in {"http", "https", "mailto"}
                                and not any(ord(char) < 32 for char in target)
                                and not uri.username
                                and not uri.password
                                and (uri.scheme.casefold() == "mailto" or bool(uri.hostname)),
                                "DOCX 不允许外部加载关系",
                            )
                            continue
                        resolved = _internal_target(owner, target)
                        _require(
                            resolved in types and not resolved.endswith(".rels"),
                            "DOCX 关系目标部件缺失或不合法",
                        )
                        if not owner and relation_type in MAIN_REL_TYPES:
                            main_parts.append(resolved)
                _require(
                    len(main_parts) == 1 and types[main_parts[0]] == MAIN_CONTENT_TYPE,
                    "DOCX 包关系必须指向唯一非宏 Word 主文档",
                )
                document = xml_parts.get(main_parts[0])
                _require(document is not None, "DOCX 主文档不是 XML")
                valid_roots = {
                    f"{{{namespace}}}document": namespace for namespace in WORD_NAMESPACES
                }
                _require(document.tag in valid_roots, "DOCX 主文档缺少 Word 结构")
                _require(
                    len(document.findall(f"{{{valid_roots[document.tag]}}}body")) == 1,
                    "DOCX 主文档必须包含唯一正文容器",
                )
                return {
                    "documentType": "DOCX",
                    "entryCount": len(infos),
                    "partCount": len(types),
                    "totalSizeBytes": total,
                    "mainPart": main_parts[0],
                }
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        UnicodeError,
        OSError,
        NotImplementedError,
        RuntimeError,
    ) as error:
        raise OfficePackageError("DOCX 结构无法安全读取") from error


def is_safe_docx_package(
    source: str | Path | BinaryIO, *, filename: str | None = None, **limits: Any
) -> bool:
    """用于外层嵌套 ZIP 判定；任何未认可结构返回 False，保留原拦截行为。"""
    try:
        inspect_docx_package(source, filename=filename, **limits)
        return True
    except (OfficePackageError, ValueError, TypeError):
        return False


__all__ = ["OfficePackageError", "inspect_docx_package", "is_safe_docx_package"]
