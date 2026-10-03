from __future__ import annotations

import io
import stat
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

import scripts.office_package_guard as guard


def _xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _parts() -> dict[str, bytes]:
    # Match a normal Word/WPS package: content table, package relationships,
    # main document, formatting parts, metadata and an actual passive image.
    content = ET.Element(f"{{{guard.CONTENT_NS}}}Types")
    for extension, content_type in (
        ("rels", guard.REL_CONTENT_TYPE),
        ("xml", "application/xml"),
        ("gif", "image/gif"),
        ("JPG", "image/.jpg"),
    ):
        ET.SubElement(
            content, f"{{{guard.CONTENT_NS}}}Default", Extension=extension, ContentType=content_type
        )
    types = {
        "word/document.xml": guard.MAIN_CONTENT_TYPE,
        "word/styles.xml": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
        ),
        "word/settings.xml": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
        ),
        "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    }
    for name, content_type in types.items():
        ET.SubElement(
            content,
            f"{{{guard.CONTENT_NS}}}Override",
            PartName="/" + name,
            ContentType=content_type,
        )
    package_rels = ET.Element(f"{{{guard.REL_NS}}}Relationships")
    for number, (target, relation_type) in enumerate(
        (
            ("word/document.xml", guard.OFFICE_REL_PREFIXES[0] + "officeDocument"),
            (
                "docProps/core.xml",
                "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
            ),
        ),
        1,
    ):
        ET.SubElement(
            package_rels,
            f"{{{guard.REL_NS}}}Relationship",
            Id=f"rId{number}",
            Type=relation_type,
            Target=target,
        )
    document_rels = ET.Element(f"{{{guard.REL_NS}}}Relationships")
    for number, (target, relation_type) in enumerate(
        (
            ("styles.xml", "styles"),
            ("settings.xml", "settings"),
            ("media/image1.gif", "image"),
        ),
        1,
    ):
        ET.SubElement(
            document_rels,
            f"{{{guard.REL_NS}}}Relationship",
            Id=f"rId{number}",
            Type=guard.OFFICE_REL_PREFIXES[0] + relation_type,
            Target=target,
        )
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    return {
        "[Content_Types].xml": _xml(content),
        "_rels/.rels": _xml(package_rels),
        "word/_rels/document.xml.rels": _xml(document_rels),
        "word/document.xml": (
            f'<w:document xmlns:w="{word_namespace}"><w:body>'
            '<w:p><w:r><w:t>Fixture</w:t></w:r></w:p></w:body></w:document>'
        ).encode(),
        "word/styles.xml": f'<w:styles xmlns:w="{word_namespace}"/>'.encode(),
        "word/settings.xml": f'<w:settings xmlns:w="{word_namespace}"/>'.encode(),
        "docProps/core.xml": (
            b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            b'package/2006/metadata/core-properties"/>'
        ),
        "word/media/image1.gif": (
            b"GIF89a\x01\0\x01\0\x80\0\0\0\0\0\xff\xff\xff!\xf9\x04\x01"
            b"\0\0\0\0,\0\0\0\0\x01\0\x01\0\0\x02\x02D\x01\0;"
        ),
    }


def _archive(
    parts: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    directories: bool = False,
    extra: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        if directories:
            for name in ("_rels/", "word/", "word/_rels/", "word/media/", "docProps/"):
                archive.writestr(name, b"")
        for name, data in parts.items():
            archive.writestr(name, data)
        for name, data in extra or []:
            archive.writestr(name, data)
    return buffer.getvalue()


def _check(parts: dict[str, bytes], **limits: object) -> bool:
    return guard.is_safe_docx_package(
        io.BytesIO(_archive(parts)), filename="fixture.docx", **limits
    )


def _change_root(parts: dict[str, bytes], name: str, edit) -> None:
    root = ET.fromstring(parts[name])
    edit(root)
    parts[name] = _xml(root)


@pytest.mark.parametrize("directories", [False, True])
@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_normal_nonmacro_docx_passes_and_preserves_stream(directories, compression):
    parts = _parts()
    stream = io.BytesIO(_archive(parts, directories=directories, compression=compression))
    stream.seek(17)
    summary = guard.inspect_docx_package(stream, filename="reports/fixture.DOCX")
    assert stream.tell() == 17 and not stream.closed
    assert summary == {
        "documentType": "DOCX",
        "entryCount": len(parts) + (5 if directories else 0),
        "partCount": len(parts) - 1,
        "totalSizeBytes": sum(map(len, parts.values())),
        "mainPart": "word/document.xml",
    }


def test_path_interface_is_read_only_and_does_not_extract(tmp_path: Path):
    path = tmp_path / "document.docx"
    content = _archive(_parts())
    path.write_bytes(content)
    before = path.stat()
    assert guard.is_safe_docx_package(path)
    assert path.read_bytes() == content and path.stat().st_mtime_ns == before.st_mtime_ns
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "fixture.zip",
        "fixture.docm",
        "fixture.dotx",
        "fixture.xlsx",
        "fixture.pptx",
        "fixture.docx.exe",
    ],
)
def test_zip_identity_requires_docx_extension(filename):
    assert not guard.is_safe_docx_package(io.BytesIO(_archive(_parts())), filename=filename)


def test_path_extension_cannot_be_overridden(tmp_path: Path):
    path = tmp_path / "ordinary.zip"
    path.write_bytes(_archive(_parts()))
    assert not guard.is_safe_docx_package(path, filename="disguised.docx")


def test_ordinary_zip_disguised_as_docx_is_not_accepted():
    assert not _check({"inside.txt": b"ordinary archive"})


@pytest.mark.parametrize("missing", ["[Content_Types].xml", "_rels/.rels", "word/document.xml"])
def test_missing_identity_parts_are_not_accepted(missing):
    parts = _parts()
    del parts[missing]
    assert not _check(parts)


@pytest.mark.parametrize(
    "document",
    [
        b"plain text",
        b"<document><body/></document>",
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/><w:body/></w:document>',
    ],
)
def test_main_document_must_have_real_word_structure(document):
    parts = _parts()
    parts["word/document.xml"] = document
    assert not _check(parts)


@pytest.mark.parametrize("prefix", [b"MZ", b"arbitrary-prefix", b"PK\x03\x04junk"])
def test_self_extracting_or_prefixed_zip_is_not_a_docx(prefix):
    assert not guard.is_safe_docx_package(
        io.BytesIO(prefix + _archive(_parts())), filename="disguised.docx"
    )


@pytest.mark.parametrize(
    "name",
    [
        "../escape.xml",
        "word/../../escape.xml",
        "/absolute.xml",
        "C:/absolute.xml",
        "word\\escape.xml",
        "word//bad.xml",
        "word/./bad.xml",
        "word/%2e%2e/escape.xml",
        "word/NUL.xml",
        "word/file.xml ",
        "word/file.xml.",
        "word/control\x01.xml",
        "word/file:stream.xml",
    ],
)
def test_unsafe_inner_paths_are_rejected(name):
    parts = _parts()
    parts[name] = b"<fixture/>"
    data = _archive(parts)
    # ZipInfo converts the Windows separator when constructing a normal ZIP;
    # mutate the written local and central names to exercise an actual bad ZIP.
    if "\\" in name:
        data = data.replace(name.replace("\\", "/").encode(), name.encode())
    assert not guard.is_safe_docx_package(io.BytesIO(data), filename="fixture.docx")


@pytest.mark.parametrize("duplicate", ["word/styles.xml", "WORD/STYLES.XML"])
def test_exact_and_case_duplicate_parts_are_rejected(duplicate):
    with pytest.warns(UserWarning) if duplicate == "word/styles.xml" else _no_warning():
        data = _archive(_parts(), extra=[(duplicate, b"<fixture/>")])
    assert not guard.is_safe_docx_package(io.BytesIO(data), filename="fixture.docx")


def _no_warning():
    from contextlib import nullcontext

    return nullcontext()


def test_unicode_normalization_duplicate_parts_are_rejected():
    parts = _parts()
    parts["word/caf\u00e9.xml"] = b"<fixture/>"
    parts["word/cafe\u0301.xml"] = b"<fixture/>"
    assert not _check(parts)


@pytest.mark.parametrize("name", ["word", "word/styles.xml/child.xml"])
def test_file_and_child_path_conflicts_are_rejected(name):
    parts = _parts()
    parts[name] = b"<fixture/>"
    assert not _check(parts)


@pytest.mark.parametrize(
    "mode,attributes",
    [
        (stat.S_IFLNK | 0o777, 0),
        (stat.S_IFIFO | 0o600, 0),
        (stat.S_IFREG | 0o600, 0x400),
        (stat.S_IFDIR | 0o700, 0),
    ],
)
def test_links_reparse_points_and_special_parts_are_rejected(mode, attributes):
    info = zipfile.ZipInfo("word/linked.xml")
    info.create_system = 3
    info.external_attr = (mode << 16) | attributes
    data = _archive(_parts(), extra=[(info, b"<fixture/>")])
    assert not guard.is_safe_docx_package(io.BytesIO(data), filename="fixture.docx")


@pytest.mark.parametrize(
    "limits",
    [{"max_entries": 2}, {"max_member_bytes": 2}, {"max_total_bytes": 2}, {"max_ratio": 1.0}],
)
def test_caller_resource_limits_are_applied(limits):
    assert not _check(_parts(), **limits)


def test_inner_zipbomb_is_rejected_before_xml_parse():
    parts = _parts()
    parts["word/styles.xml"] = b"x" * 100_000
    with pytest.raises(guard.OfficePackageError, match="压缩炸弹"):
        guard.inspect_docx_package(io.BytesIO(_archive(parts)), filename="fixture.docx")


def test_caller_cannot_raise_docx_resource_ceiling(monkeypatch):
    monkeypatch.setattr(guard, "MAX_DOCX_TOTAL_BYTES", 50)
    assert not _check(_parts(), max_total_bytes=2**31)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_entries": 0},
        {"max_member_bytes": -1},
        {"max_total_bytes": True},
        {"max_ratio": float("nan")},
        {"max_ratio": float("inf")},
    ],
)
def test_invalid_resource_limits_fail_closed(limits):
    assert not _check(_parts(), **limits)


@pytest.mark.parametrize(
    "payload", [b"<root>" + b"<x/>" * 10 + b"</root>", b"<root>" + b"a" * 100 + b"</root>"]
)
def test_xml_limits_are_independent_from_outer_zip_limits(payload, monkeypatch):
    parts = _parts()
    parts["word/styles.xml"] = payload
    monkeypatch.setattr(guard, "MAX_XML_NODES", 5)
    monkeypatch.setattr(guard, "MAX_XML_BYTES", 100)
    assert not _check(parts, max_member_bytes=512 * 1024 * 1024, max_total_bytes=2**31)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_dtd_entities_are_rejected_for_supported_xml_encodings(encoding):
    parts = _parts()
    parts["word/styles.xml"] = (
        '<?xml version="1.0" encoding="' + encoding + '"?>'
        '<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///C:/untrusted">]><root>&secret;</root>'
    ).encode(encoding)
    with pytest.raises(guard.OfficePackageError, match="DTD"):
        guard.inspect_docx_package(io.BytesIO(_archive(parts)), filename="fixture.docx")


@pytest.mark.parametrize(
    "content_type",
    [
        "application/vnd.ms-word.document.macroEnabled.main+xml",
        "application/vnd.ms-office.vbaProject",
        "application/vnd.ms-office.activeX+xml",
    ],
)
def test_macro_or_active_content_type_is_rejected(content_type):
    parts = _parts()

    def edit(root):
        for item in root:
            if item.get("PartName") == "/word/document.xml":
                item.set("ContentType", content_type)

    _change_root(parts, "[Content_Types].xml", edit)
    assert not _check(parts)


@pytest.mark.parametrize(
    "name",
    [
        "word/vbaProject.bin",
        "word/embeddings/embedded.docx",
        "word/activeX/control.xml",
        "word/object.bin",
        "word/script.exe",
    ],
)
def test_macro_embedded_and_unknown_binary_parts_are_rejected(name):
    parts = _parts()
    parts[name] = _archive({"inside.txt": b"nested"})
    assert not _check(parts)


@pytest.mark.parametrize(
    "payload", [_archive({"inside.txt": b"nested"}), b"MZ" + _archive({"inside.txt": b"nested"})]
)
def test_zip_and_self_extracting_zip_disguised_as_media_are_rejected(payload):
    parts = _parts()
    parts["word/media/image1.gif"] = b"GIF89a" + payload
    with pytest.raises(guard.OfficePackageError, match="嵌套 ZIP"):
        guard.inspect_docx_package(io.BytesIO(_archive(parts)), filename="fixture.docx")


def test_media_extension_and_type_must_match_content():
    parts = _parts()
    parts["word/media/image1.gif"] = b"MZ executable bytes"
    assert not _check(parts)


@pytest.mark.parametrize(
    "relation_type",
    ["vbaProject", "oleObject", "package", "control", "aFChunk", "attachedTemplate"],
)
def test_active_or_embedded_relationships_are_rejected(relation_type):
    parts = _parts()
    _change_root(
        parts,
        "word/_rels/document.xml.rels",
        lambda root: root[0].set("Type", guard.OFFICE_REL_PREFIXES[0] + relation_type),
    )
    assert not _check(parts)


@pytest.mark.parametrize(
    "target",
    [
        "../../escape.xml",
        "%2e%2e/escape.xml",
        "missing.xml",
        "C:/secret.xml",
        "https://external.example/document.xml",
        "styles.xml?query=1",
        "styles.xml#fragment",
        "\\server\\secret.xml",
    ],
)
def test_missing_unsafe_or_external_internal_targets_are_rejected(target):
    parts = _parts()
    _change_root(parts, "word/_rels/document.xml.rels", lambda root: root[0].set("Target", target))
    assert not _check(parts)


def test_duplicate_relationship_ids_are_rejected():
    parts = _parts()
    _change_root(
        parts, "word/_rels/document.xml.rels", lambda root: root[1].set("Id", root[0].get("Id"))
    )
    assert not _check(parts)


@pytest.mark.parametrize(
    "target,relation_type,expected",
    [
        ("https://example.invalid/", "hyperlink", True),
        ("mailto:fixture@example.invalid", "hyperlink", True),
        ("file:///C:/secret", "hyperlink", False),
        ("javascript:fixture", "hyperlink", False),
        ("https://user:password@example.invalid/", "hyperlink", False),
        ("https://example.invalid/picture.png", "image", False),
    ],
)
def test_only_passive_http_and_mailto_external_links_are_allowed(target, relation_type, expected):
    parts = _parts()

    def edit(root):
        ET.SubElement(
            root,
            f"{{{guard.REL_NS}}}Relationship",
            Id="rExternal",
            Type=guard.OFFICE_REL_PREFIXES[0] + relation_type,
            Target=target,
            TargetMode="External",
        )

    _change_root(parts, "word/_rels/document.xml.rels", edit)
    assert _check(parts) is expected


@pytest.mark.parametrize("tag", ["object", "control", "altChunk", "subDoc"])
def test_active_word_markup_is_rejected_even_without_relationship(tag):
    parts = _parts()
    _change_root(
        parts,
        "word/document.xml",
        lambda root: ET.SubElement(
            root[0], "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}" + tag
        ),
    )
    assert not _check(parts)


def test_read_failure_restores_stream_and_returns_false():
    stream = io.BytesIO(b"not a complete ZIP")
    stream.seek(3)
    assert not guard.is_safe_docx_package(stream, filename="fixture.docx")
    assert stream.tell() == 3 and not stream.closed


def test_crc_failure_is_rejected():
    parts = _parts()
    data = bytearray(_archive(parts, compression=zipfile.ZIP_STORED))
    index = data.index(parts["word/document.xml"])
    data[index] ^= 1
    assert not guard.is_safe_docx_package(io.BytesIO(data), filename="fixture.docx")


def test_encrypted_zip_flags_are_rejected():
    data = bytearray(_archive(_parts(), compression=zipfile.ZIP_STORED))
    local_flag = struct.unpack_from("<H", data, 6)[0]
    struct.pack_into("<H", data, 6, local_flag | 1)
    central_offset = data.index(b"PK\x01\x02")
    central_flag = struct.unpack_from("<H", data, central_offset + 8)[0]
    struct.pack_into("<H", data, central_offset + 8, central_flag | 1)
    assert not guard.is_safe_docx_package(io.BytesIO(data), filename="fixture.docx")


def test_existing_outer_nested_zip_rejection_is_preserved(tmp_path: Path):
    import scripts.source_intake as source

    nested = _archive({"inside.txt": b"nested"})
    for number, (name, payload) in enumerate(
        (
            ("nested.zip", nested),
            ("disguised.docx", nested),
            ("attachment.bin", b"MZ" + nested),
        )
    ):
        outer = tmp_path / f"outer-{number}.zip"
        outer.write_bytes(_archive({name: payload}))
        target = tmp_path / f"output-{number}"
        with pytest.raises(source.SourceIntakeError, match="嵌套 ZIP"):
            source.safe_extract_package(outer, target)
        assert not target.exists()
