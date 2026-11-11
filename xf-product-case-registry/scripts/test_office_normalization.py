"""Word(.docx/.doc) 原件经操作者转换的 PDF 绑定：记录、compose、local-verify 与归档深度复核。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import scripts.image_pdf as image_pdf
import scripts.office_normalized as office
import scripts.workspace_state as ws
from scripts.test_local_amendment import PROJECT, Case, make_png, out
from scripts.test_office_package_guard import _archive, _parts

OLE = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 600


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return Case(tmp_path)


def docx_bytes(text: str = "Fixture") -> bytes:
    parts = _parts()
    parts["word/document.xml"] = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    return _archive(parts)


def render(variant: int = 0) -> bytes:
    """Stand-in for the operator's Office rendering: any plain, valid PDF."""
    return image_pdf.build_pdf(make_png(width=30 + variant, height=20))[0]


def test_source_inspection_accepts_plain_word_and_rejects_unsafe_input() -> None:
    assert office.inspect_source(docx_bytes(), "现场照片.docx")["sourceFormat"] == "DOCX"
    assert office.inspect_source(OLE, "产品照片.DOC")["sourceFormat"] == "DOC"
    with pytest.raises(office.OfficeNormalizationError, match="只支持"):
        office.inspect_source(OLE, "表.xlsx")
    with pytest.raises(office.OfficeNormalizationError, match="DOCX"):
        office.inspect_source(b"PK\x03\x04not-a-word-package", "假.docx")
    with pytest.raises(office.OfficeNormalizationError, match="OLE2"):
        office.inspect_source(b"%PDF-1.4 renamed", "假.doc")
    macro = OLE + "_VBA_PROJECT".encode("utf-16le")
    with pytest.raises(office.OfficeNormalizationError, match="宏"):
        office.inspect_source(macro, "带宏.doc")


def test_pdf_inspection_rejects_active_content_and_truncated_files() -> None:
    pdf = render()
    assert office.inspect_pdf(pdf) == 1
    with pytest.raises(office.OfficeNormalizationError, match="不是 PDF"):
        office.inspect_pdf(b"hello")
    with pytest.raises(office.OfficeNormalizationError, match="脚本"):
        office.inspect_pdf(pdf.replace(b"%%EOF", b"/JavaScript (x)\n%%EOF"))
    with pytest.raises(office.OfficeNormalizationError):
        office.inspect_pdf(pdf[: len(pdf) // 3])


def test_pdf_inspection_ignores_action_lookalikes_inside_compressed_stream_bytes() -> None:
    pdf = render()
    noisy = pdf.replace(b"endstream", b"\x01/JS\xb8\x02/AA\xff\nendstream", 1)
    assert noisy != pdf and office.inspect_pdf(noisy) == 1
    with pytest.raises(office.OfficeNormalizationError, match="脚本"):
        office.inspect_pdf(noisy.replace(b"%%EOF", b"<< /AA << /O 1 0 R >> >>\n%%EOF"))


def test_mapping_requires_converter_and_rendering_check_and_verifies_both_files() -> None:
    source, pdf = docx_bytes(), render()
    with pytest.raises(office.OfficeNormalizationError, match="转换器"):
        office.build_mapping(
            source,
            "a.docx",
            pdf,
            converter_name="",
            converter_version="1",
            rendering_check="逐页目视核对完毕",
        )
    with pytest.raises(office.OfficeNormalizationError, match="目视"):
        office.build_mapping(
            source,
            "a.docx",
            pdf,
            converter_name="LibreOffice",
            converter_version="24.2",
            rendering_check="ok",
        )
    mapping = office.build_mapping(
        source,
        "a.docx",
        pdf,
        converter_name="LibreOffice",
        converter_version="24.2",
        rendering_check="逐页目视核对完毕：1页，文字与图片完整",
    )
    item = {**mapping, "sourceRelativePath": "a.docx"}
    assert office.verify_pdf(pdf, source, item) == []
    assert office.verify_pdf(render(1), source, item)  # different rendering
    assert office.verify_pdf(pdf, docx_bytes("changed"), item)  # swapped Word original


def test_normalize_office_cli_binds_and_verification_rejects_swaps(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    word = docx_bytes()
    legacy = OLE
    with zipfile.ZipFile(case.zip, "a") as archive:
        archive.writestr("现场照片.docx", word)
        archive.writestr("产品照片.doc", legacy)
    new_sha = "sha256:" + hashlib.sha256(case.zip.read_bytes()).hexdigest()
    ws.upsert_case(case.layout, PROJECT, source={"packageSha256": new_sha})
    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    capsys.readouterr()
    rendered = tmp_path / "render" / "现场照片.pdf"
    rendered.parent.mkdir()
    rendered.write_bytes(render())
    base = (
        "normalize-office",
        "--work-dir",
        str(case.work),
        "--relative-path",
        "现场照片.docx",
        "--converted-pdf",
        str(rendered),
        "--converter-name",
        "LibreOffice",
        "--converter-version",
        "24.2.7",
    )
    # the operator's rendering check statement is mandatory and meaningful
    assert case.run(*base, "--rendering-check", "ok") == 2
    capsys.readouterr()
    # a rendering placed inside the workspace, or a non-Word member, is refused
    inside = case.work / "x.pdf"
    inside.write_bytes(render())
    assert case.run(*base[:6], str(inside), *base[7:], "--rendering-check", "逐页目视核对完毕") == 2
    capsys.readouterr()
    check = "逐页目视核对完毕：1页，图片与说明文字完整"
    assert case.run(*base, "--rendering-check", check) == 0
    produced = out(capsys)["items"][0]
    assert produced["status"] == "CREATED" and produced["pageCount"] == 1
    assert case.run(*base, "--rendering-check", check) == 0
    assert out(capsys)["items"][0]["status"] == "REUSED"
    other = tmp_path / "render" / "other.pdf"
    other.write_bytes(render(1))
    assert case.run(*base[:6], str(other), *base[7:], "--rendering-check", check) == 2
    assert "拒绝覆盖" in capsys.readouterr().err
    index = json.loads((case.work / "office-normalization.json").read_text(encoding="utf-8"))
    item = index["items"][0]
    assert item["sourceFormat"] == "DOCX"
    assert item["converter"]["mode"] == "EXTERNAL_RENDER_RECORDED"
    assert item["sourceSha256"] == "sha256:" + hashlib.sha256(word).hexdigest()

    data = {
        "schemaVersion": "CaseImportManifestV2",
        "createdAt": "2026-11-03T00:00:00Z",
        "extractor": {"name": "fixture", "version": "1"},
        "case": {"projectNo": PROJECT, "brigadeCode": "XISHAN", "unitName": "合成单位"},
        "initialInspection": {
            "clientRef": "initial",
            "stage": "INITIAL_CHECK",
            "inspectionDate": "2026-10-01",
            "products": [],
        },
        "files": [
            {
                "clientRef": "file:photo",
                "sourceRelativePath": produced["output"],
                "relativePath": "normalized/photo.pdf",
            }
        ],
        "documentSlots": [],
        "otherAttachments": [
            {
                "clientRef": "attachment:photo",
                "slotCode": "OTHER_ATTACHMENT",
                "title": "现场照片",
                "fileRef": "file:photo",
            }
        ],
    }
    data_path = case.work / "case-data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pdf_path = case.work / produced["output"]
    original_pdf = pdf_path.read_bytes()

    # swapped PDF: compose rejects it
    pdf_path.write_bytes(render(1))
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 2
    assert "Word 规范化" in capsys.readouterr().err
    pdf_path.write_bytes(original_pdf)
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 0
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    # swapped Word original after compose: local-verify refuses
    extracted = case.work / "source" / "现场照片.docx"
    extracted.write_bytes(docx_bytes("changed"))
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
    extracted.write_bytes(word)
    assert case.run("local-verify", "--project", PROJECT) == 0
    done = out(capsys)
    proof = json.loads(Path(done["archive"]["verificationRecord"]).read_text(encoding="utf-8"))[
        "localVerification"
    ]
    assert proof["officeNormalization"]["file:photo"]["sourceSha256"] == item["sourceSha256"]
    assert proof["officeNormalization"]["file:photo"]["renderingCheck"] == check
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    assert out(capsys)["status"] == "ALREADY_COMPLETED"
    # the archived rendering is deep-verified against the recorded digest
    archived_pdf = next(Path(done["archive"]["archivedWorkspace"]).glob("normalized/office_*.pdf"))
    archived_pdf.write_bytes(render(1))
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2


BROKEN_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Info 1 0 R /Info 2 0 R >>\nendobj\ntrailer\n<< /Info 1 0 R >>\n%%EOF\n"
)


def test_unparseable_pdf_is_recorded_normalized_and_never_left_loose(
    tmp_path: Path, case: Case, capsys: pytest.CaptureFixture[str]
) -> None:
    assert office.is_unparseable_pdf(BROKEN_PDF) and not office.is_unparseable_pdf(render())
    with zipfile.ZipFile(case.zip, "a") as archive:
        archive.writestr("营业执照.pdf", BROKEN_PDF)
    new_sha = "sha256:" + hashlib.sha256(case.zip.read_bytes()).hexdigest()
    ws.upsert_case(case.layout, PROJECT, source={"packageSha256": new_sha})
    assert case.run("inventory", str(case.zip), "--work-dir", str(case.work)) == 0
    capsys.readouterr()
    inventory = json.loads((case.work / "inventory.json").read_text(encoding="utf-8"))
    flagged = {f["relativePath"]: f for f in inventory["files"]}["营业执照.pdf"]
    assert flagged["unparseablePdf"] is True and "pageCount" not in flagged
    assert "unparseablePdf" not in {f["relativePath"]: f for f in inventory["files"]}["通知书.pdf"]

    data = {
        "schemaVersion": "CaseImportManifestV2",
        "createdAt": "2026-11-03T00:00:00Z",
        "extractor": {"name": "fixture", "version": "1"},
        "case": {"projectNo": PROJECT, "brigadeCode": "XISHAN", "unitName": "合成单位"},
        "initialInspection": {
            "clientRef": "initial",
            "stage": "INITIAL_CHECK",
            "inspectionDate": "2026-10-01",
            "products": [],
        },
        "files": [],
        "documentSlots": [],
        "otherAttachments": [],
    }
    data_path = case.work / "case-data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # loose: composing without binding the flagged member is refused
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 2
    assert "无法解析" in capsys.readouterr().err

    rendered = tmp_path / "render" / "营业执照.pdf"
    rendered.parent.mkdir()
    rendered.write_bytes(render())
    base = (
        "normalize-office",
        "--work-dir",
        str(case.work),
        "--relative-path",
        "营业执照.pdf",
        "--converted-pdf",
        str(rendered),
        "--converter-name",
        "pdfium",
        "--converter-version",
        "test",
        "--rendering-check",
        "逐页目视核对完毕：1页，执照内容完整可读",
    )
    # a readable PDF must not use this route
    readable = case.work / "source" / "通知书.pdf"
    assert readable.is_file()
    assert case.run(*base[:4], "通知书.pdf", *base[5:]) == 2
    assert "仍可被结构解析" in capsys.readouterr().err
    assert case.run(*base) == 0
    produced = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["items"][0]
    assert produced["status"] == "CREATED"
    index = json.loads((case.work / "office-normalization.json").read_text(encoding="utf-8"))
    assert index["items"][0]["sourceFormat"] == "PDF_UNPARSEABLE"
    data["files"] = [
        {
            "clientRef": "file:license",
            "sourceRelativePath": produced["output"],
            "relativePath": "normalized/license.pdf",
        }
    ]
    data["documentSlots"] = [
        {
            "clientRef": "slot:license",
            "slotCode": "BUSINESS_LICENSE",
            "versions": [{"kind": "SCANNED", "fileRef": "file:license"}],
        }
    ]
    data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert case.run("compose", "--work-dir", str(case.work), "--case-data", str(data_path)) == 0
    capsys.readouterr()
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 0
    # swapping the broken original after composing is caught by local-verify
    (case.work / "source" / "营业执照.pdf").write_bytes(BROKEN_PDF + b"\n%changed")
    assert case.run("local-verify", "--project", PROJECT, "--check-only") == 2
