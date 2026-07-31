from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader, PdfWriter

VERSION = "0.5.0"
TEXT_THRESHOLD = 30
MAX_ZIP_FILES = 1_000
MAX_ZIP_BYTES = 1024 * 1024 * 1024
WRITE_HEADER = "X-Product-Case-Client"
WRITE_HEADER_VALUE = "web-v1"
DEFAULT_ZEROX = Path(r"D:\Program_Files\zerox\bin\zerox-local.cmd")
DEFAULT_POPPLER = Path(r"D:\Program_Files\poppler\Library\bin")

BRIGADES = {
    "JIANGYIN",
    "YIXING",
    "LIANGXI",
    "XISHAN",
    "HUISHAN",
    "BINHU",
    "XINWU",
    "JINGKAI",
}
STAGES = {"INITIAL_CHECK", "RECHECK"}
METHODS = {"ONSITE", "SAMPLING", "UNKNOWN"}
RESULTS = {"QUALIFIED", "UNQUALIFIED", "PENDING", "UNKNOWN"}
CASE_TYPES = {"NONE", "ADMINISTRATIVE", "CRIMINAL", "UNKNOWN"}
TRI_STATES = {"YES", "NO", "UNKNOWN"}
DOCUMENT_VERSION_KINDS = {"ELECTRONIC", "SCANNED"}
SPLIT_DOCUMENT_VERSION_KINDS = DOCUMENT_VERSION_KINDS | {"UNKNOWN"}
DOCUMENT_VERSION_LABELS = {
    "ELECTRONIC": "电子版",
    "SCANNED": "扫描件",
    "UNKNOWN": "版本待核对",
}
REINSPECTION_STATUSES = {
    "NOT_APPLIED",
    "APPLIED",
    "ACCEPTED",
    "REJECTED",
    "COMPLETED",
    "UNKNOWN",
}
TRUST_LEVELS = {"DETERMINISTIC", "CORROBORATED", "OCR_ONLY", "MANUAL"}
FILE_KINDS = {
    "ORIGINAL_PACKAGE",
    "DIRECTORY_MANIFEST",
    "DIRECTORY_SNAPSHOT",
    "ORIGINAL_FILE",
    "NORMALIZED_FILE",
}
FILE_ROLES = {"PRIMARY", "SOURCE_COPY", "DUPLICATE_COPY", "SUPPORTING_ATTACHMENT"}
REQUIREMENT_STATUSES = {"PRESENT", "ABSENT", "NOT_REQUIRED", "UNKNOWN"}
REVIEW_ISSUE_TYPES = {
    "VALUE_CONFLICT",
    "LOW_CONFIDENCE",
    "EXTRACTION_FAILED",
    "DATA_ANOMALY",
    "DUPLICATE_CANDIDATE",
}
REVIEWABLE_ENTITY_TYPES = {"case", "product", "inspection", "requirement", "document"}
REVIEWABLE_ENTITY_FIELDS = {
    "case": {
        "projectNo",
        "brigadeCode",
        "unitName",
        "unitAddress",
        "inspectionForm",
        "caseHandler",
        "inspector",
        "caseType",
    },
    "product": {
        "sequence",
        "name",
        "modelSpec",
        "nominalProducer",
        "location",
        "onlineSale",
        "repairStatus",
        "problemSummary",
    },
    "inspection": {
        "caseInspectionRef",
        "stage",
        "method",
        "inspectionDate",
        "inspectionResult",
        "inspectionBase",
        "inspectionQuantity",
        "quantityUnit",
        "marketAccessResult",
        "qualityInspectionResult",
        "problemDescription",
        "submittedSampleName",
        "reinspectionStatus",
        "reinspectionApplicationDate",
        "reinspectionAcceptanceDate",
        "reinspectionAgency",
        "reinspectionReportNo",
        "reinspectionReportDate",
        "reinspectionResult",
        "reinspectionNotes",
    },
    "requirement": {"scope", "stage", "documentType", "status", "reason"},
    "document": {
        "documentType",
        "documentNo",
        "issueDate",
        "stage",
        "classificationEvidence",
        "versions",
    },
}
IMAGE_MIME_TYPES = {"image/png", "image/jpeg"}
SUPERVISION_SCREENSHOT_HINTS = ("截图", "监督系统", "监管系统", "产品信息")
SUPERVISION_SCREENSHOT_HEADERS = (
    "检查产品信息",
    "产品名称",
    "规格型号",
    "标称生产者",
    "产品所在部位",
    "检查基数",
    "检查数量",
    "市场准入检查情况",
    "产品质量现场检查情况",
)
SUPERVISION_RECORD_HINTS = ("消防产品监督检查记录", "监督检查记录")
SUPERVISION_ELECTRONIC_HINTS = ("消防产品", "市场准入", "消防救援", "监督检查")
SIGNED_SCAN_HINTS = ("扫描", "签字", "签名", "盖章", "手写")
DIRECT_CRIMINAL_EVIDENCE_RE = re.compile(r"刑事案件|刑案|移送\s*(?:公安|公安机关)|公安机关.*移送")
STAGE_LABELS = {
    "INITIAL_CHECK": "初查",
    "RECHECK": "复查",
    "CASE": "案卷",
}
CASE_INSPECTION_STAGE_LABELS = {
    "INITIAL_CHECK": "initial",
    "RECHECK": "recheck",
}
CASE_INSPECTION_REF_RE = re.compile(r"^[a-z][a-z0-9-]*:")


class RegistryError(RuntimeError):
    """可向操作者直接展示的流程错误。"""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RegistryError(f"文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise RegistryError(f"JSON 格式错误：{path}：{error}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"JSON 顶层必须是对象：{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def stable_ref(prefix: str, relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
    return f"file:{prefix}:{digest}"


def normalized_text_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def sniff_mime(path: Path) -> str:
    header = path.read_bytes()[:4096]
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    text = header.decode("utf-8", errors="ignore").lstrip()
    if text.startswith(("{", "[")):
        return "application/json"
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def assert_safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".." in path.parts
        or "\x00" in normalized
    ):
        raise RegistryError(f"不安全的相对路径：{value}")
    return path.as_posix()


def safe_extract_zip(archive: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise RegistryError(f"ZIP 解压目录非空，拒绝覆盖：{destination}")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise RegistryError("ZIP 文件数量超过 1000")
        if sum(info.file_size for info in infos) > MAX_ZIP_BYTES:
            raise RegistryError("ZIP 解压总量超过 1 GiB")
        for info in infos:
            relative = assert_safe_relative(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RegistryError(f"ZIP 不允许符号链接：{relative}")
            target = (destination / Path(*PurePosixPath(relative).parts)).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RegistryError(f"ZIP 路径越界：{relative}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as input_stream, target.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream)


def extract_pdf_pages(
    path: Path, text_root: Path, file_key: str
) -> tuple[int, list[dict[str, Any]]]:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise RegistryError(f"PDF 已加密且无法空密码读取：{path.name}")
    except Exception as error:
        if isinstance(error, RegistryError):
            raise
        raise RegistryError(f"PDF 无法读取：{path.name}：{error}") from error

    page_records: list[dict[str, Any]] = []
    file_text_root = text_root / file_key
    file_text_root.mkdir(parents=True, exist_ok=True)
    for page_number, page in enumerate(reader.pages, start=1):
        extraction_error: str | None = None
        try:
            text = page.extract_text() or ""
        except Exception as error:  # pragma: no cover - pypdf 异常类型不稳定
            text = ""
            extraction_error = str(error)
        text_path = file_text_root / f"page-{page_number:04d}.txt"
        text_path.write_text(text, encoding="utf-8", newline="\n")
        chars = normalized_text_length(text)
        page_records.append(
            {
                "page": page_number,
                "textPath": str(text_path.resolve()),
                "textChars": chars,
                "needsOcr": chars < TEXT_THRESHOLD,
                **({"textExtractionError": extraction_error} if extraction_error else {}),
            }
        )
    return len(reader.pages), page_records


def extract_image_page(path: Path, text_root: Path, file_key: str) -> list[dict[str, Any]]:
    """为图片建立与 PDF 页一致的清点记录，强制进入 OCR 队列。"""
    file_text_root = text_root / file_key
    file_text_root.mkdir(parents=True, exist_ok=True)
    text_path = file_text_root / "page-0001.txt"
    text_path.write_text("", encoding="utf-8", newline="\n")
    return [
        {
            "page": 1,
            "textPath": str(text_path.resolve()),
            "textChars": 0,
            "needsOcr": True,
            "inputKind": "IMAGE",
        }
    ]


def package_hash_for_directory(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value["relativePath"]):
        digest.update(item["relativePath"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def inventory_command(args: argparse.Namespace) -> None:
    source_input = Path(args.input).resolve()
    work_dir = Path(args.work_dir).resolve()
    if not source_input.exists():
        raise RegistryError(f"输入不存在：{source_input}")
    if source_input.is_dir() and work_dir.is_relative_to(source_input):
        raise RegistryError("工作目录不能位于原案卷目录内部")
    work_dir.mkdir(parents=True, exist_ok=True)

    package_file: Path | None = None
    if source_input.is_dir():
        source_root = source_input
        container_kind = "DIRECTORY"
        hash_method = "SORTED_RELATIVE_PATH_AND_FILE_SHA256"
        package_name = source_input.name
    elif source_input.is_file() and source_input.suffix.lower() == ".zip":
        package_file = source_input
        source_root = work_dir / "source"
        if not source_root.exists():
            safe_extract_zip(source_input, source_root)
        container_kind = "ARCHIVE"
        hash_method = "ARCHIVE_BYTES"
        package_name = source_input.name
    else:
        raise RegistryError("输入必须是案卷目录或 ZIP")

    files: list[dict[str, Any]] = []
    text_root = work_dir / "text"
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        relative = path.relative_to(source_root).as_posix()
        sha256 = file_sha256(path)
        mime_type = sniff_mime(path)
        file_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
        item: dict[str, Any] = {
            "clientRef": stable_ref("orig", relative),
            "relativePath": relative,
            "uploadRelativePath": f"original/{relative}",
            "absolutePath": str(path.resolve()),
            "sha256": sha256,
            "sizeBytes": path.stat().st_size,
            "mimeType": mime_type,
            "uploadable": mime_type
            in {
                "application/pdf",
                "application/zip",
                "image/png",
                "image/jpeg",
                "application/json",
            },
        }
        if mime_type == "application/pdf":
            page_count, pages = extract_pdf_pages(path, text_root, file_key)
            item["pageCount"] = page_count
            item["pages"] = pages
        elif mime_type in IMAGE_MIME_TYPES:
            item["pageCount"] = 1
            item["pages"] = extract_image_page(path, text_root, file_key)
        files.append(item)

    package_sha256 = (
        file_sha256(package_file) if package_file else package_hash_for_directory(files)
    )
    local_inventory = {
        "inventoryVersion": 1,
        "generatedAt": utc_now(),
        "sourceInput": str(source_input),
        "sourceRoot": str(source_root.resolve()),
        "workDir": str(work_dir),
        "packageName": package_name,
        "containerKind": container_kind,
        "packageSha256": package_sha256,
        "packageHashMethod": hash_method,
        **({"packageFile": str(package_file)} if package_file else {}),
        "files": files,
    }
    portable_manifest = {
        "manifestVersion": 1,
        "generatedAt": local_inventory["generatedAt"],
        "packageName": package_name,
        "containerKind": container_kind,
        "packageSha256": package_sha256,
        "packageHashMethod": hash_method,
        "hashRecipe": (
            "SHA256(UTF8(relativePath) + NUL + ASCII(sha256:<hex>) + LF, sorted by path)"
            if container_kind == "DIRECTORY"
            else "SHA256(raw archive bytes)"
        ),
        "files": [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "relativePath",
                    "uploadRelativePath",
                    "sha256",
                    "sizeBytes",
                    "mimeType",
                    "pageCount",
                    "uploadable",
                }
            }
            for item in files
        ],
    }
    write_json(work_dir / "inventory.json", local_inventory)
    write_json(work_dir / "source-directory-manifest.json", portable_manifest)
    scanned_pages = sum(1 for item in files for page in item.get("pages", []) if page["needsOcr"])
    print(
        json.dumps(
            {
                "status": "ok",
                "files": len(files),
                "pdfPages": sum(
                    item.get("pageCount", 0)
                    for item in files
                    if item["mimeType"] == "application/pdf"
                ),
                "imagePages": sum(
                    item.get("pageCount", 0)
                    for item in files
                    if item["mimeType"] in IMAGE_MIME_TYPES
                ),
                "needsOcrPages": scanned_pages,
                "packageSha256": package_sha256,
                "inventory": str(work_dir / "inventory.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run_zerox_page(
    *,
    source: Path,
    page_number: int,
    output_dir: Path,
    zerox: Path,
    poppler: Path,
    timeout: int,
    input_kind: str = "PDF",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical = output_dir / "page.md"
    if canonical.exists() and canonical.stat().st_size > 0:
        return {
            "status": "SUCCESS",
            "page": page_number,
            "markdownPath": str(canonical.resolve()),
            "resumed": True,
        }
    environment = os.environ.copy()
    environment["PATH"] = f"{poppler}{os.pathsep}{environment.get('PATH', '')}"
    arguments = [
        str(zerox),
        "--input",
        str(source),
        "--output",
        str(output_dir),
        "--maintain-format",
        "true",
    ]
    if input_kind != "IMAGE":
        arguments.extend(["--pages", str(page_number)])
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *arguments]
    else:
        command = arguments
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        return {"status": "FAILED", "page": page_number, "error": f"timeout: {error}"}
    log = (completed.stdout + "\n" + completed.stderr).strip()
    (output_dir / "command.log").write_text(log[-8_000:], encoding="utf-8", newline="\n")
    markdown_files = [
        path for path in output_dir.rglob("*.md") if path.resolve() != canonical.resolve()
    ]
    if completed.returncode != 0 or not markdown_files:
        return {
            "status": "FAILED",
            "page": page_number,
            "returnCode": completed.returncode,
            "error": log[-2_000:] or "Zerox 未生成 Markdown",
        }
    source_markdown = max(markdown_files, key=lambda path: path.stat().st_mtime_ns)
    canonical.write_text(
        source_markdown.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "status": "SUCCESS",
        "page": page_number,
        "markdownPath": str(canonical.resolve()),
        "returnCode": completed.returncode,
    }


def ocr_command(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    inventory = read_json(work_dir / "inventory.json")
    zerox = Path(args.zerox).resolve()
    poppler = Path(args.poppler).resolve()
    if not zerox.is_file():
        raise RegistryError(f"Zerox 入口不存在：{zerox}")
    if not poppler.is_dir():
        raise RegistryError(f"Poppler 目录不存在：{poppler}")
    tasks: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in inventory["files"]:
        for page in item.get("pages", []):
            if page["needsOcr"]:
                tasks.append((item, page))

    results: list[dict[str, Any]] = []

    def execute(task: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        item, page = task
        key = item["clientRef"].replace(":", "-")
        output_dir = work_dir / "ocr" / key / f"page-{page['page']:04d}"
        result = run_zerox_page(
            source=Path(item["absolutePath"]),
            page_number=page["page"],
            output_dir=output_dir,
            zerox=zerox,
            poppler=poppler,
            timeout=args.timeout,
            input_kind=page.get("inputKind", "PDF"),
        )
        return {
            "fileRef": item["clientRef"],
            "relativePath": item["relativePath"],
            **result,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_map = {executor.submit(execute, task): task for task in tasks}
        for future in concurrent.futures.as_completed(future_map):
            try:
                results.append(future.result())
            except Exception as error:  # pragma: no cover - 保护批处理
                item, page = future_map[future]
                results.append(
                    {
                        "fileRef": item["clientRef"],
                        "relativePath": item["relativePath"],
                        "page": page["page"],
                        "status": "FAILED",
                        "error": str(error),
                    }
                )
            write_json(
                work_dir / "ocr-index.json",
                {
                    "ocrIndexVersion": 1,
                    "updatedAt": utc_now(),
                    "results": sorted(
                        results, key=lambda value: (value["relativePath"], value["page"])
                    ),
                },
            )
    failures = [result for result in results if result["status"] != "SUCCESS"]
    print(
        json.dumps(
            {
                "status": "ok" if not failures else "partial",
                "pages": len(results),
                "success": len(results) - len(failures),
                "failed": len(failures),
                "index": str(work_dir / "ocr-index.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures and not args.continue_on_error:
        raise RegistryError(f"{len(failures)} 个 OCR 页失败")


SOURCE_KIND_PRIORITY = {
    "SUPERVISION_SCREENSHOT": 100,
    "SUPERVISION_ELECTRONIC_PDF_TEXT": 90,
    "SUPERVISION_RECORD_PDF_TEXT": 85,
    "SIGNED_SCAN_OCR": 60,
    "FILENAME_HINT": 10,
    "UNKNOWN": 0,
}

FIELD_GROUP_PRIORITIES = {
    "productIdentity": [
        "SUPERVISION_SCREENSHOT",
        "SUPERVISION_ELECTRONIC_PDF_TEXT",
        "SUPERVISION_RECORD_PDF_TEXT",
        "SIGNED_SCAN_OCR",
        "FILENAME_HINT",
        "UNKNOWN",
    ],
    "inspectionFacts": [
        "SUPERVISION_SCREENSHOT",
        "SUPERVISION_ELECTRONIC_PDF_TEXT",
        "SUPERVISION_RECORD_PDF_TEXT",
        "SIGNED_SCAN_OCR",
        "FILENAME_HINT",
        "UNKNOWN",
    ],
    "problemDescription": [
        "SUPERVISION_RECORD_PDF_TEXT",
        "SUPERVISION_SCREENSHOT",
        "SUPERVISION_ELECTRONIC_PDF_TEXT",
        "SIGNED_SCAN_OCR",
        "FILENAME_HINT",
        "UNKNOWN",
    ],
    "signatureAndHandwrittenCorrection": [
        "SIGNED_SCAN_OCR",
        "SUPERVISION_ELECTRONIC_PDF_TEXT",
        "SUPERVISION_SCREENSHOT",
        "FILENAME_HINT",
        "UNKNOWN",
    ],
}


def source_text(item: dict[str, Any], ocr_by_file_ref: dict[str, list[dict[str, Any]]]) -> str:
    text_parts: list[str] = []
    for page in item.get("pages", []):
        text_path = page.get("textPath")
        if isinstance(text_path, str):
            path = Path(text_path)
            if path.is_file():
                text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    for result in ocr_by_file_ref.get(item["clientRef"], []):
        markdown_path = result.get("markdownPath")
        if isinstance(markdown_path, str):
            path = Path(markdown_path)
            if path.is_file():
                text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(text_parts)


def contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def supervision_screenshot_header_hits(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text)
    return [header for header in SUPERVISION_SCREENSHOT_HEADERS if header in normalized]


def classify_source(item: dict[str, Any], extracted_text: str) -> tuple[str, str]:
    """仅说明来源性质；绝不从文件名或 OCR 推断最终业务字段。"""
    relative_path = str(item["relativePath"])
    combined = f"{relative_path}\n{extracted_text}"
    mime_type = item["mimeType"]
    has_text_layer = any(
        page.get("textChars", 0) >= TEXT_THRESHOLD for page in item.get("pages", [])
    )
    has_ocr = any(page.get("needsOcr") for page in item.get("pages", []))
    screenshot_headers = supervision_screenshot_header_hits(extracted_text)

    if mime_type in IMAGE_MIME_TYPES and len(screenshot_headers) >= 3:
        return (
            "SUPERVISION_SCREENSHOT",
            "图片 OCR 命中监督系统产品信息表头：" + "、".join(screenshot_headers) + "。",
        )
    if (
        mime_type in IMAGE_MIME_TYPES
        and screenshot_headers
        and contains_any(relative_path, SUPERVISION_SCREENSHOT_HINTS)
    ):
        return (
            "SUPERVISION_SCREENSHOT",
            "图片 OCR 命中产品信息表头，文件名仅作辅助提示："
            + "、".join(screenshot_headers)
            + "。",
        )
    if (
        mime_type == "application/pdf"
        and has_text_layer
        and contains_any(combined, SUPERVISION_RECORD_HINTS)
    ):
        return "SUPERVISION_RECORD_PDF_TEXT", "PDF 文本层命中消防产品监督检查记录。"
    if (
        mime_type == "application/pdf"
        and has_text_layer
        and contains_any(combined, SUPERVISION_ELECTRONIC_HINTS)
    ):
        return "SUPERVISION_ELECTRONIC_PDF_TEXT", "PDF 具有可用文本层，作为电子文本候选来源。"
    if (
        mime_type == "application/pdf"
        and has_ocr
        and (contains_any(combined, SIGNED_SCAN_HINTS) or not has_text_layer)
    ):
        return "SIGNED_SCAN_OCR", "PDF 无有效文本层，需 OCR；作为扫描签字/归档候选来源。"
    if contains_any(relative_path, SUPERVISION_SCREENSHOT_HINTS + SUPERVISION_RECORD_HINTS):
        return "FILENAME_HINT", "仅文件名提供监督系统或检查记录线索，不能据此确定业务值。"
    return "UNKNOWN", "未识别可验证来源性质。"


def source_analysis_command(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    inventory = read_json(work_dir / "inventory.json")
    ocr_index_path = work_dir / "ocr-index.json"
    ocr_index = read_json(ocr_index_path) if ocr_index_path.exists() else {"results": []}
    ocr_by_file_ref: dict[str, list[dict[str, Any]]] = {}
    for result in ocr_index.get("results", []):
        if isinstance(result, dict) and result.get("status") == "SUCCESS":
            file_ref = result.get("fileRef")
            if isinstance(file_ref, str):
                ocr_by_file_ref.setdefault(file_ref, []).append(result)

    sources: list[dict[str, Any]] = []
    for item in inventory["files"]:
        extracted = source_text(item, ocr_by_file_ref)
        source_kind, reason = classify_source(item, extracted)
        sources.append(
            {
                "fileRef": item["clientRef"],
                "relativePath": item["relativePath"],
                "mimeType": item["mimeType"],
                "sourceKind": source_kind,
                "priority": SOURCE_KIND_PRIORITY[source_kind],
                "classificationReason": reason,
                "textLayerChars": sum(
                    int(page.get("textChars", 0)) for page in item.get("pages", [])
                ),
                "ocrSucceededPages": len(ocr_by_file_ref.get(item["clientRef"], [])),
                "fieldGroupPriorities": FIELD_GROUP_PRIORITIES,
            }
        )

    output = {
        "sourceAnalysisVersion": 1,
        "generatedAt": utc_now(),
        "packageSha256": inventory["packageSha256"],
        "doesNotInferBusinessValues": True,
        "manualValuePolicy": "MANUAL 值最高优先级，自动提取不得覆盖。",
        "conflictPolicy": (
            "不同来源提取到不同值时，不自动选择最终业务值；保留双方证据并在 "
            "case-data.json.reviewItems 写入 VALUE_CONFLICT。"
        ),
        "sourceKinds": SOURCE_KIND_PRIORITY,
        "fieldGroupPriorities": FIELD_GROUP_PRIORITIES,
        "sources": sources,
    }
    target = work_dir / "source-analysis.json"
    write_json(target, output)
    print(
        json.dumps(
            {"status": "ok", "sources": len(sources), "analysis": str(target)},
            ensure_ascii=False,
            indent=2,
        )
    )


def clean_filename_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", "", cleaned).strip(" ._")
    return (cleaned or fallback)[:80]


def split_command(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    inventory = read_json(work_dir / "inventory.json")
    plan = read_json(Path(args.plan).resolve())
    project_no = str(plan.get("projectNo", ""))
    if not re.fullmatch(r"[0-9A-Za-z]{1,18}", project_no):
        raise RegistryError("split-plan.projectNo 必须是 1—18 位字母数字")
    sources = {item["relativePath"]: item for item in inventory["files"]}
    normalized_root = work_dir / "normalized"
    normalized_root.mkdir(parents=True, exist_ok=True)
    output_items: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for position, item in enumerate(plan.get("items", []), start=1):
        document_ref = str(item.get("documentRef", "")).strip()
        if not re.fullmatch(r"document:.+", document_ref):
            raise RegistryError(
                "拆分项必须填写与 case-data.documents[].clientRef 一致的 documentRef"
            )
        source_relative = assert_safe_relative(str(item.get("sourceRelativePath", "")))
        source_record = sources.get(source_relative)
        if not source_record or source_record["mimeType"] != "application/pdf":
            raise RegistryError(f"拆分源不是已清点 PDF：{source_relative}")
        start = int(item.get("pageStart", 0))
        end = int(item.get("pageEnd", 0))
        page_count = int(source_record.get("pageCount", 0))
        if start < 1 or end < start or end > page_count:
            raise RegistryError(
                f"页码范围错误：{source_relative} {start}-{end}，总页数 {page_count}"
            )
        stage = str(item.get("stage", "CASE"))
        if stage not in STAGE_LABELS:
            raise RegistryError(f"拆分阶段不合法：{stage}")
        sequence = int(item.get("sequence", position))
        if sequence < 1:
            raise RegistryError("拆分序号必须为正整数")
        document_type = clean_filename_part(str(item.get("documentType", "")), "UNKNOWN")
        document_label = clean_filename_part(
            str(item.get("documentLabel", document_type)), document_type
        )
        number_or_date = clean_filename_part(
            str(item.get("documentNoOrDate", "未知日期")), "未知日期"
        )
        if "documentVersionKind" not in item:
            raise RegistryError("拆分项必须明确 documentVersionKind；无法判断时填写 UNKNOWN")
        document_version_kind = str(item.get("documentVersionKind", ""))
        if document_version_kind not in SPLIT_DOCUMENT_VERSION_KINDS:
            raise RegistryError(f"文书版本类型不合法：{document_version_kind}")
        version_label = DOCUMENT_VERSION_LABELS[document_version_kind]
        file_name = (
            f"{project_no}_{STAGE_LABELS[stage]}_{document_label}_"
            f"{number_or_date}_{version_label}_{sequence:02d}.pdf"
        )
        if file_name in seen_names:
            raise RegistryError(f"规范化文件名重复：{file_name}")
        seen_names.add(file_name)
        destination = normalized_root / file_name

        reader = PdfReader(source_record["absolutePath"], strict=False)
        writer = PdfWriter()
        for page_index in range(start - 1, end):
            writer.add_page(reader.pages[page_index])
        temporary = destination.with_suffix(".pdf.tmp")
        with temporary.open("wb") as stream:
            writer.write(stream)
        if destination.exists():
            if file_sha256(destination) != file_sha256(temporary):
                temporary.unlink()
                raise RegistryError(f"规范化文件已存在且内容不同：{destination}")
            temporary.unlink()
        else:
            temporary.replace(destination)
        normalized_relative = f"normalized/{file_name}"
        output_items.append(
            {
                "clientRef": stable_ref("norm", normalized_relative),
                "relativePath": normalized_relative,
                "absolutePath": str(destination.resolve()),
                "sha256": file_sha256(destination),
                "sizeBytes": destination.stat().st_size,
                "mimeType": "application/pdf",
                "pageCount": end - start + 1,
                "sourceFileRef": source_record["clientRef"],
                "sourceRelativePath": source_relative,
                "sourcePageStart": start,
                "sourcePageEnd": end,
                "documentRef": document_ref,
                "stage": stage,
                "documentType": document_type,
                "documentVersionKind": document_version_kind,
                "sequence": sequence,
            }
        )
    split_index = {
        "splitIndexVersion": 2,
        "generatedAt": utc_now(),
        "projectNo": project_no,
        "items": output_items,
    }
    write_json(work_dir / "split-index.json", split_index)
    print(
        json.dumps(
            {
                "status": "ok",
                "files": len(output_items),
                "index": str(work_dir / "split-index.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def entity_value(entity: dict[str, Any], field_path: str) -> Any:
    current: Any = entity
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(field_path)
        current = current[part]
    return current


def inspection_date_group_value(inspection: dict[str, Any]) -> str:
    value = inspection.get("inspectionDate")
    return str(value).strip() if value is not None else ""


def generated_case_inspection_ref(stage: str, inspection_date: str, ordinal: int) -> str:
    stage_label = CASE_INSPECTION_STAGE_LABELS.get(stage, "unknown")
    if inspection_date:
        return f"case-inspection:{stage_label}:{inspection_date}"
    return f"case-inspection:{stage_label}:ordinal-{ordinal}"


def populate_case_inspection_refs(products: list[dict[str, Any]]) -> None:
    """为未显式分组的产品检查补齐可跨产品共享的案卷级检查引用。"""
    for product in products:
        stage_ordinals: dict[str, int] = {}
        for inspection in product.get("inspections", []):
            if not isinstance(inspection, dict):
                continue
            stage = str(inspection.get("stage", "UNKNOWN"))
            stage_ordinals[stage] = stage_ordinals.get(stage, 0) + 1
            explicit_ref = inspection.get("caseInspectionRef")
            if isinstance(explicit_ref, str) and explicit_ref.strip():
                continue
            inspection["caseInspectionRef"] = generated_case_inspection_ref(
                stage,
                inspection_date_group_value(inspection),
                stage_ordinals[stage],
            )


def build_entities(case_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    case = dict(case_data.get("case") or {})
    project_no = str(case.get("projectNo", ""))
    case.setdefault("clientRef", f"case:{project_no}")
    entity_map: dict[str, dict[str, Any]] = {case["clientRef"]: case}
    products: list[dict[str, Any]] = []
    for product_position, source_product in enumerate(case_data.get("products", []), start=1):
        product = dict(source_product)
        sequence = int(product.get("sequence", product_position))
        product["sequence"] = sequence
        product.setdefault("clientRef", f"product:{sequence}")
        inspections: list[dict[str, Any]] = []
        stage_counts: dict[str, int] = {}
        for source_inspection in product.get("inspections", []):
            inspection = dict(source_inspection)
            stage = str(inspection.get("stage", "UNKNOWN"))
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            suffix = "" if stage_counts[stage] == 1 else f":{stage_counts[stage]}"
            inspection.setdefault("clientRef", f"inspection:{sequence}:{stage.lower()}{suffix}")
            inspections.append(inspection)
            entity_map[inspection["clientRef"]] = inspection
        product["inspections"] = inspections
        products.append(product)
        entity_map[product["clientRef"]] = product
    case_data["case"] = case
    case_data["products"] = products
    populate_case_inspection_refs(products)
    return case, entity_map


def failed_recheck_inspection_refs(products: list[dict[str, Any]]) -> list[str]:
    return [
        str(inspection["clientRef"])
        for product in products
        if isinstance(product, dict)
        for inspection in product.get("inspections", [])
        if isinstance(inspection, dict)
        and inspection.get("stage") == "RECHECK"
        and inspection.get("inspectionResult") == "UNQUALIFIED"
        and isinstance(inspection.get("clientRef"), str)
        and inspection["clientRef"]
    ]


def is_case_type_evidence(item: Any, case_ref: str, case_type: str) -> bool:
    return (
        isinstance(item, dict)
        and item.get("entityRef") == case_ref
        and item.get("fieldPath") == "caseType"
        and item.get("value") == case_type
    )


def source_has_file_locator(source: Any) -> bool:
    return isinstance(source, dict) and bool(source.get("relativePath") or source.get("fileRef"))


def has_direct_criminal_evidence(evidence_items: list[Any], case_ref: str) -> bool:
    for item in evidence_items:
        if not is_case_type_evidence(item, case_ref, "CRIMINAL"):
            continue
        if item.get("trustLevel") == "OCR_ONLY":
            continue
        sources = item.get("sources")
        if not isinstance(sources, list):
            continue
        if any(
            source_has_file_locator(source)
            and isinstance(source.get("page"), int)
            and source["page"] >= 1
            and DIRECT_CRIMINAL_EVIDENCE_RE.search(str(source.get("evidence", "")))
            for source in sources
            if isinstance(source, dict)
        ):
            return True
    return False


def administrative_rule_evidence(inspection_refs: list[str]) -> dict[str, Any]:
    return {
        "kind": "RULE",
        "value": {
            "inspectionRef": inspection_refs[0],
            "stage": "RECHECK",
            "inspectionResult": "UNQUALIFIED",
        },
        "evidence": "规则判定：存在整改复查不合格检查记录，案卷类型归为行案。",
    }


def has_administrative_rule_evidence(
    evidence_items: list[Any], case_ref: str, inspection_refs: list[str]
) -> bool:
    for item in evidence_items:
        if not is_case_type_evidence(item, case_ref, "ADMINISTRATIVE"):
            continue
        if item.get("trustLevel") != "DETERMINISTIC":
            continue
        sources = item.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict) or source.get("kind") != "RULE":
                continue
            rule_value = source.get("value")
            if (
                isinstance(rule_value, dict)
                and rule_value.get("inspectionRef") in inspection_refs
                and rule_value.get("stage") == "RECHECK"
                and rule_value.get("inspectionResult") == "UNQUALIFIED"
            ):
                return True
    return False


def has_case_type_missing_item(missing_items: list[Any], case_ref: str) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("entityRef") == case_ref
        and item.get("fieldPath") == "caseType"
        and str(item.get("reason", "")).strip()
        for item in missing_items
    )


def normalize_case_type(case_data: dict[str, Any], case: dict[str, Any]) -> None:
    """按已确认刑事证据和整改复查结果归一案卷类型，不以 NONE 作为默认值。"""
    case_ref = str(case["clientRef"])
    products = case_data.get("products")
    product_items = products if isinstance(products, list) else []
    evidence_items = case_data.get("fieldEvidence")
    evidence_items = evidence_items if isinstance(evidence_items, list) else []
    missing_items = case_data.get("missingItems")
    missing_items = missing_items if isinstance(missing_items, list) else []
    failed_refs = failed_recheck_inspection_refs(product_items)

    criminal_confirmed = has_direct_criminal_evidence(evidence_items, case_ref)
    if criminal_confirmed:
        case_type = "CRIMINAL"
    elif failed_refs:
        case_type = "ADMINISTRATIVE"
    else:
        case_type = "UNKNOWN"
    case["caseType"] = case_type

    retained_evidence = [
        item
        for item in evidence_items
        if not (
            isinstance(item, dict)
            and item.get("entityRef") == case_ref
            and item.get("fieldPath") == "caseType"
        )
    ]
    if case_type == "CRIMINAL":
        retained_evidence.extend(
            item for item in evidence_items if is_case_type_evidence(item, case_ref, "CRIMINAL")
        )
    elif case_type == "ADMINISTRATIVE":
        retained_evidence.append(
            {
                "entityRef": case_ref,
                "fieldPath": "caseType",
                "value": "ADMINISTRATIVE",
                "trustLevel": "DETERMINISTIC",
                "sources": [administrative_rule_evidence(failed_refs)],
            }
        )
    case_data["fieldEvidence"] = retained_evidence

    retained_missing = [
        item
        for item in missing_items
        if not (
            isinstance(item, dict)
            and item.get("entityRef") == case_ref
            and item.get("fieldPath") == "caseType"
        )
    ]
    if case_type == "UNKNOWN":
        retained_missing.append(
            {
                "entityRef": case_ref,
                "fieldPath": "caseType",
                "reason": (
                    "未识别到明确刑事直接证据，且不存在整改复查不合格记录；"
                    "案卷类型保持 UNKNOWN，待人工核对。"
                ),
            }
        )
    case_data["missingItems"] = retained_missing


def generated_review_item_ref(review_item: dict[str, Any]) -> str:
    """根据待核对项语义生成稳定标识，避免按数组序号导致幂等键漂移。"""
    semantic_item = {key: value for key, value in review_item.items() if key != "clientRef"}
    serialized = json.dumps(
        semantic_item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"review:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:20]}"


def compose_review_items(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    source_items = case_data.get("reviewItems", [])
    if not isinstance(source_items, list):
        raise RegistryError("case-data.reviewItems 必须是数组")
    review_items: list[dict[str, Any]] = []
    for source_item in source_items:
        if not isinstance(source_item, dict):
            raise RegistryError("case-data.reviewItems 中每项必须是对象")
        review_item = dict(source_item)
        client_ref = review_item.get("clientRef")
        if client_ref is None or not str(client_ref).strip():
            review_item["clientRef"] = generated_review_item_ref(review_item)
        review_items.append(review_item)
    return review_items


def compose_command(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    inventory = read_json(work_dir / "inventory.json")
    case_data = read_json(Path(args.case_data).resolve())
    split_index_path = work_dir / "split-index.json"
    split_index = read_json(split_index_path) if split_index_path.exists() else {"items": []}
    case, _ = build_entities(case_data)
    normalize_case_type(case_data, case)
    source_documents = case_data.get("documents", [])
    if not isinstance(source_documents, list):
        raise RegistryError("case-data.documents 必须是数组")
    prepared_documents: list[dict[str, Any]] = []
    prepared_document_refs: set[str] = set()
    prepared_document_by_ref: dict[str, dict[str, Any]] = {}
    prepared_document_identities: dict[tuple[str, str, str, str], str] = {}
    for index, source_document in enumerate(source_documents, start=1):
        if not isinstance(source_document, dict):
            raise RegistryError(f"case-data.documents[{index}] 必须是对象")
        document = dict(source_document)
        document.setdefault("clientRef", f"document:{index}")
        document_ref = str(document["clientRef"])
        if not re.fullmatch(r"document:.+", document_ref):
            raise RegistryError(f"文书 clientRef 不合法：{document_ref}")
        if document_ref in prepared_document_refs:
            raise RegistryError(f"文书 clientRef 重复：{document_ref}")
        prepared_document_refs.add(document_ref)
        prepared_document_by_ref[document_ref] = document
        identity = normalized_document_identity(document)
        identity_owner = prepared_document_identities.get(identity)
        if identity_owner:
            raise RegistryError(
                f"逻辑文书身份重复：{identity_owner} 与 {document_ref}；"
                "必须合并为一条文书并保留全部原始来源"
            )
        prepared_document_identities[identity] = document_ref
        prepared_documents.append(document)

    split_items = split_index.get("items", [])
    if not isinstance(split_items, list):
        raise RegistryError("split-index.items 必须是数组")
    split_items_by_document: dict[str, list[dict[str, Any]]] = {}
    for index, split_item in enumerate(split_items, start=1):
        if not isinstance(split_item, dict):
            raise RegistryError(f"split-index.items[{index}] 必须是对象")
        document_ref = str(split_item.get("documentRef", ""))
        if document_ref not in prepared_document_refs:
            raise RegistryError(
                f"规范化候选 {split_item.get('relativePath')} 未绑定到唯一逻辑文书：{document_ref}"
            )
        target_document = prepared_document_by_ref[document_ref]
        split_stage = split_item.get("stage")
        target_stage = target_document.get("stage")
        stage_matches = split_stage == target_stage or (
            split_stage == "CASE" and target_stage in (None, "")
        )
        if not stage_matches or split_item.get("documentType") != target_document.get(
            "documentType"
        ):
            raise RegistryError(
                f"规范化候选 {split_item.get('relativePath')} 的阶段或文书类型"
                f"与 {document_ref} 不一致"
            )
        split_items_by_document.setdefault(document_ref, []).append(split_item)
    files: list[dict[str, Any]] = []
    upload_map: dict[str, str] = {}
    path_to_ref: dict[str, str] = {}

    def add_file(record: dict[str, Any], storage_kind: str) -> None:
        relative = assert_safe_relative(record["relativePath"])
        if relative in path_to_ref:
            raise RegistryError(f"上传相对路径重复：{relative}")
        client_ref = record["clientRef"]
        manifest_file = {
            "clientRef": client_ref,
            "storageKind": storage_kind,
            "relativePath": relative,
            "sha256": record["sha256"],
            "mimeType": record["mimeType"],
            **({"pageCount": record["pageCount"]} if record.get("pageCount") is not None else {}),
            **({"sourceFileRef": record["sourceFileRef"]} if record.get("sourceFileRef") else {}),
            **(
                {"sourcePageStart": record["sourcePageStart"]}
                if record.get("sourcePageStart")
                else {}
            ),
            **({"sourcePageEnd": record["sourcePageEnd"]} if record.get("sourcePageEnd") else {}),
            **(
                {"documentVersionKind": record["documentVersionKind"]}
                if record.get("documentVersionKind")
                else {}
            ),
        }
        files.append(manifest_file)
        upload_map[client_ref] = str(Path(record["absolutePath"]).resolve())
        path_to_ref[relative] = client_ref

    directory_manifest = work_dir / "source-directory-manifest.json"
    add_file(
        {
            "clientRef": "file:directory-manifest",
            "relativePath": "metadata/source-directory-manifest.json",
            "absolutePath": str(directory_manifest),
            "sha256": file_sha256(directory_manifest),
            "mimeType": "application/json",
        },
        "DIRECTORY_MANIFEST",
    )
    if inventory["containerKind"] == "ARCHIVE":
        package_path = Path(inventory["packageFile"])
        add_file(
            {
                "clientRef": "file:original-package",
                "relativePath": f"package/{package_path.name}",
                "absolutePath": str(package_path),
                "sha256": file_sha256(package_path),
                "mimeType": "application/zip",
            },
            "ORIGINAL_PACKAGE",
        )
    for item in inventory["files"]:
        if not item.get("uploadable"):
            raise RegistryError(
                f"服务器不支持上传该 MIME，需人工处理：{item['relativePath']} ({item['mimeType']})"
            )
        add_file(
            {
                **item,
                "relativePath": item["uploadRelativePath"],
            },
            "ORIGINAL_FILE",
        )
    for item in split_items:
        add_file(item, "NORMALIZED_FILE")

    documents: list[dict[str, Any]] = []
    file_by_ref = {item["clientRef"]: item for item in files}
    document_candidate_state: dict[str, dict[str, bool]] = {}
    for document in prepared_documents:
        document_ref = str(document["clientRef"])
        candidates = split_items_by_document.get(document_ref, [])
        candidates_by_kind: dict[str, list[dict[str, Any]]] = {
            kind: [] for kind in sorted(DOCUMENT_VERSION_KINDS)
        }
        unknown_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_kind = candidate.get("documentVersionKind")
            if candidate_kind in DOCUMENT_VERSION_KINDS:
                candidates_by_kind[str(candidate_kind)].append(candidate)
            else:
                unknown_candidates.append(candidate)
        versions: list[dict[str, Any]] = []
        for source_version in document.get("versions", []):
            if not isinstance(source_version, dict):
                raise RegistryError("文书 versions 中每项必须是对象")
            version = dict(source_version)
            relative = version.pop("relativePath", None)
            if relative:
                file_ref = path_to_ref.get(assert_safe_relative(relative))
                if not file_ref:
                    raise RegistryError(f"文书版本引用了未知文件：{relative}")
                version["fileRef"] = file_ref
            versions.append(version)
        selected_kinds: dict[str, str] = {}
        for version in versions:
            kind = str(version.get("kind", ""))
            file_ref = str(version.get("fileRef", ""))
            if kind in selected_kinds:
                raise RegistryError(f"文书 {document_ref} 的 {kind} 正式版本重复")
            matching_candidates = {
                str(candidate["clientRef"]) for candidate in candidates_by_kind.get(kind, [])
            }
            if file_ref not in matching_candidates:
                raise RegistryError(
                    f"文书 {document_ref} 的正式版本 {file_ref} 不是该文书同类型规范化候选"
                )
            selected_kinds[kind] = file_ref
        for kind, kind_candidates in candidates_by_kind.items():
            if kind in selected_kinds or len(kind_candidates) != 1:
                continue
            candidate_ref = str(kind_candidates[0]["clientRef"])
            versions.append({"fileRef": candidate_ref, "kind": kind})
            selected_kinds[kind] = candidate_ref
        versions.sort(key=lambda version: 0 if version.get("kind") == "ELECTRONIC" else 1)
        document["versions"] = versions
        links: list[dict[str, Any]] = []
        for source_link in document.get("fileLinks", []):
            link = dict(source_link)
            relative = link.pop("relativePath", None)
            if relative:
                file_ref = path_to_ref.get(assert_safe_relative(relative))
                if not file_ref:
                    raise RegistryError(f"文书引用了未知文件：{relative}")
                link["fileRef"] = file_ref
            links.append(link)
        linked_source_keys = {
            (
                link.get("fileRef"),
                link.get("pageStart"),
                link.get("pageEnd"),
            )
            for link in links
            if isinstance(link.get("fileRef"), str)
        }
        selected_file_refs = set(selected_kinds.values())
        for candidate in candidates:
            source_file_ref = candidate.get("sourceFileRef")
            source_key = (
                source_file_ref,
                candidate.get("sourcePageStart"),
                candidate.get("sourcePageEnd"),
            )
            if not isinstance(source_file_ref, str) or source_key in linked_source_keys:
                continue
            source_link = {
                "fileRef": source_file_ref,
                "relationRole": (
                    "PRIMARY"
                    if candidate.get("clientRef") in selected_file_refs
                    and candidate.get("documentVersionKind") == "ELECTRONIC"
                    else "SOURCE_COPY"
                ),
            }
            if candidate.get("sourcePageStart"):
                source_link["pageStart"] = candidate["sourcePageStart"]
            if candidate.get("sourcePageEnd"):
                source_link["pageEnd"] = candidate["sourcePageEnd"]
            links.append(source_link)
            linked_source_keys.add(source_key)
        document["fileLinks"] = links
        document.setdefault("productRefs", [])
        document.setdefault("inspectionRefs", [])
        documents.append(document)
        document_candidate_state[document_ref] = {
            "hasUnknown": bool(unknown_candidates),
            "hasAmbiguousSelection": any(
                len(kind_candidates) > 1 and kind not in selected_kinds
                for kind, kind_candidates in candidates_by_kind.items()
            ),
            "hasDuplicateCandidates": any(
                len(kind_candidates) > 1 for kind_candidates in candidates_by_kind.values()
            ),
        }

    review_items = compose_review_items(case_data)
    for document in documents:
        document_ref = document["clientRef"]
        versions = document["versions"]
        selected_source_keys = {
            (
                file_by_ref[version["fileRef"]].get("sourceFileRef"),
                file_by_ref[version["fileRef"]].get("sourcePageStart"),
                file_by_ref[version["fileRef"]].get("sourcePageEnd"),
            )
            for version in versions
            if version.get("fileRef") in file_by_ref
        }
        linked_source_keys = {
            (link.get("fileRef"), link.get("pageStart"), link.get("pageEnd"))
            for link in document["fileLinks"]
            if isinstance(link, dict) and isinstance(link.get("fileRef"), str)
        }
        candidate_state = document_candidate_state.get(document_ref, {})
        review_keys = {
            (item.get("entityRef"), item.get("fieldPath"), item.get("issueType"))
            for item in review_items
            if isinstance(item, dict)
        }
        if (
            not versions
            or candidate_state.get("hasUnknown")
            or candidate_state.get("hasAmbiguousSelection")
        ) and (document_ref, "versions", "LOW_CONFIDENCE") not in review_keys:
            review_item = {
                "entityRef": document_ref,
                "fieldPath": "versions",
                "issueType": "LOW_CONFIDENCE",
                "message": "无法可靠判断电子版或扫描件；未写入正式版本，保留原始来源待人工核对。",
            }
            review_item["clientRef"] = generated_review_item_ref(review_item)
            review_items.append(review_item)
        extra_source_keys = sorted(
            (key for key in linked_source_keys - selected_source_keys if isinstance(key[0], str)),
            key=lambda key: (str(key[0]), int(key[1] or 0), int(key[2] or 0)),
        )
        if (candidate_state.get("hasDuplicateCandidates") or (versions and extra_source_keys)) and (
            document_ref,
            "versions",
            "DUPLICATE_CANDIDATE",
        ) not in review_keys:
            extra_sources = [
                f"{file_ref}"
                + (
                    f"（第{page_start}-{page_end}页）"
                    if page_start is not None and page_end is not None
                    else ""
                )
                for file_ref, page_start, page_end in extra_source_keys
            ]
            if not extra_sources:
                extra_sources = ["同类型规范化候选"]
            review_item = {
                "entityRef": document_ref,
                "fieldPath": "versions",
                "issueType": "DUPLICATE_CANDIDATE",
                "message": (
                    "同一逻辑文书存在未选为正式版本的原始来源："
                    + "、".join(extra_sources)
                    + "；保留来源并待人工核对。"
                ),
            }
            review_item["clientRef"] = generated_review_item_ref(review_item)
            review_items.append(review_item)

    requirements: list[dict[str, Any]] = []
    for index, source_requirement in enumerate(case_data.get("documentRequirements", []), start=1):
        requirement = dict(source_requirement)
        requirement.setdefault("clientRef", f"requirement:{index}")
        requirement.setdefault("caseRef", case["clientRef"])
        requirements.append(requirement)

    evidence_items: list[dict[str, Any]] = []
    for source_evidence in case_data.get("fieldEvidence", []):
        evidence = dict(source_evidence)
        sources: list[dict[str, Any]] = []
        for source_item in evidence.get("sources", []):
            evidence_source = dict(source_item)
            relative = evidence_source.pop("relativePath", None)
            if relative:
                file_ref = path_to_ref.get(assert_safe_relative(relative))
                if not file_ref:
                    raise RegistryError(f"字段证据引用了未知文件：{relative}")
                evidence_source["fileRef"] = file_ref
            sources.append(evidence_source)
        evidence["sources"] = sources
        evidence_items.append(evidence)

    manifest_review_items: list[dict[str, Any]] = []
    for source_review_item in review_items:
        review_item = dict(source_review_item)
        candidates: list[dict[str, Any]] = []
        for source_candidate in review_item.get("candidates", []):
            if not isinstance(source_candidate, dict):
                candidates.append(source_candidate)
                continue
            candidate = dict(source_candidate)
            candidate_sources: list[dict[str, Any]] = []
            for source_item in candidate.get("sources", []):
                if not isinstance(source_item, dict):
                    candidate_sources.append(source_item)
                    continue
                candidate_source = dict(source_item)
                relative = candidate_source.pop("relativePath", None)
                if relative:
                    file_ref = path_to_ref.get(assert_safe_relative(relative))
                    if not file_ref:
                        raise RegistryError(f"待核对候选引用了未知文件：{relative}")
                    candidate_source["fileRef"] = file_ref
                candidate_sources.append(candidate_source)
            candidate["sources"] = candidate_sources
            candidates.append(candidate)
        if "candidates" in review_item:
            review_item["candidates"] = candidates
        manifest_review_items.append(review_item)

    manifest = {
        "schemaVersion": "CaseImportManifestV1",
        "source": {
            "sourceType": "LOCAL_SKILL",
            "packageName": inventory["packageName"],
            "containerKind": inventory["containerKind"],
            "packageSha256": inventory["packageSha256"],
            "packageHashMethod": inventory["packageHashMethod"],
            "extractedAt": utc_now(),
            "extractor": {"name": "xf-product-case-registry", "version": VERSION},
        },
        "case": case,
        "products": case_data.get("products", []),
        "documentRequirements": requirements,
        "files": files,
        "documents": documents,
        "fieldEvidence": evidence_items,
        "missingItems": case_data.get("missingItems", []),
        "reviewItems": manifest_review_items,
    }
    manifest_path = work_dir / "manifest.json"
    upload_map_path = work_dir / "upload-map.json"
    write_json(manifest_path, manifest)
    write_json(
        upload_map_path,
        {
            "uploadMapVersion": 1,
            "manifestSha256": file_sha256(manifest_path),
            "files": upload_map,
        },
    )
    errors = validate_manifest(manifest, upload_map)
    if errors:
        raise RegistryError("manifest 组装后校验失败：\n- " + "\n- ".join(errors))
    print(
        json.dumps(
            {
                "status": "ok",
                "manifest": str(manifest_path),
                "uploadMap": str(upload_map_path),
                "files": len(files),
                "documents": len(documents),
                "reviewItems": len(manifest["reviewItems"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def validate_manifest(
    manifest: dict[str, Any], upload_files: dict[str, str] | None = None
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schemaVersion") != "CaseImportManifestV1":
        errors.append("schemaVersion 必须为 CaseImportManifestV1")
    source = manifest.get("source")
    if not isinstance(source, dict):
        errors.append("缺少 source")
        source = {}
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(source.get("packageSha256", ""))):
        errors.append("source.packageSha256 格式错误")
    case = manifest.get("case")
    if not isinstance(case, dict):
        errors.append("缺少 case")
        case = {}
    project_no = str(case.get("projectNo", ""))
    if not re.fullmatch(r"[0-9A-Za-z]{1,18}", project_no):
        errors.append("case.projectNo 必须是 1—18 位字母数字")
    if case.get("brigadeCode") not in BRIGADES:
        errors.append("case.brigadeCode 不在八个大队枚举中")
    if not str(case.get("unitName", "")).strip():
        errors.append("case.unitName 不能为空")
    if case.get("caseType") not in CASE_TYPES:
        errors.append("case.caseType 必须为 NONE、ADMINISTRATIVE、CRIMINAL 或 UNKNOWN")
    if "onlineSale" in case:
        errors.append("case.onlineSale 已停用；网售情况必须分别填写在 products[].onlineSale")

    entity_map: dict[str, dict[str, Any]] = {}

    def add_entity(entity: Any, label: str) -> None:
        if not isinstance(entity, dict):
            errors.append(f"{label} 必须是对象")
            return
        client_ref = entity.get("clientRef")
        if not isinstance(client_ref, str) or not client_ref:
            errors.append(f"{label}.clientRef 不能为空")
        elif client_ref in entity_map:
            errors.append(f"clientRef 重复：{client_ref}")
        else:
            entity_map[client_ref] = entity

    add_entity(case, "case")
    products = manifest.get("products")
    if not isinstance(products, list) or not products:
        errors.append("products 至少需要一项")
        products = []
    failed_recheck_refs: list[str] = []
    case_inspection_groups: dict[str, tuple[Any, str]] = {}
    for product_index, product in enumerate(products, start=1):
        add_entity(product, f"products[{product_index}]")
        if not isinstance(product, dict):
            continue
        if not str(product.get("name", "")).strip():
            errors.append(f"products[{product_index}].name 不能为空")
        if product.get("onlineSale") not in TRI_STATES:
            errors.append(f"products[{product_index}].onlineSale 必须为 YES、NO 或 UNKNOWN")
        inspections = product.get("inspections")
        if not isinstance(inspections, list) or not inspections:
            errors.append(f"products[{product_index}].inspections 至少需要一项")
            continue
        for inspection_index, inspection in enumerate(inspections, start=1):
            label = f"products[{product_index}].inspections[{inspection_index}]"
            add_entity(inspection, label)
            if not isinstance(inspection, dict):
                continue
            if inspection.get("stage") not in STAGES:
                errors.append(f"{label}.stage 不合法")
            if inspection.get("method") not in METHODS:
                errors.append(f"{label}.method 不合法")
            if inspection.get("inspectionResult") not in RESULTS:
                errors.append(f"{label}.inspectionResult 不合法")
            case_inspection_ref = inspection.get("caseInspectionRef")
            if case_inspection_ref is not None:
                if not isinstance(case_inspection_ref, str) or not CASE_INSPECTION_REF_RE.match(
                    case_inspection_ref
                ):
                    errors.append(f"{label}.caseInspectionRef 必须是以小写前缀开头的引用")
                else:
                    current_group = (
                        inspection.get("stage"),
                        inspection_date_group_value(inspection),
                    )
                    existing_group = case_inspection_groups.get(case_inspection_ref)
                    if existing_group is None:
                        case_inspection_groups[case_inspection_ref] = current_group
                    elif existing_group != current_group:
                        errors.append(f"案卷检查分组 {case_inspection_ref} 的阶段或检查日期不一致")
            if (
                inspection.get("stage") == "RECHECK"
                and inspection.get("inspectionResult") == "UNQUALIFIED"
                and isinstance(inspection.get("clientRef"), str)
                and inspection["clientRef"]
            ):
                failed_recheck_refs.append(inspection["clientRef"])
            reinspection_status = inspection.get("reinspectionStatus", "NOT_APPLIED")
            if reinspection_status not in REINSPECTION_STATUSES:
                errors.append(f"{label}.reinspectionStatus 不合法")
            reinspection_detail_fields = (
                "reinspectionApplicationDate",
                "reinspectionAcceptanceDate",
                "reinspectionAgency",
                "reinspectionReportNo",
                "reinspectionReportDate",
                "reinspectionResult",
                "reinspectionNotes",
            )
            has_reinspection = reinspection_status != "NOT_APPLIED" or any(
                inspection.get(field) not in (None, "") for field in reinspection_detail_fields
            )
            has_reinspection_details = any(
                inspection.get(field) not in (None, "") for field in reinspection_detail_fields
            )
            if reinspection_status == "NOT_APPLIED" and has_reinspection_details:
                errors.append(f"{label} 复检状态为未申请时不能包含复检详情")
            if has_reinspection and inspection.get("method") != "SAMPLING":
                errors.append(f"{label} 只有抽样送检记录可以包含复检信息")
            reinspection_result = inspection.get("reinspectionResult")
            if reinspection_result is not None and reinspection_result not in RESULTS:
                errors.append(f"{label}.reinspectionResult 不合法")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files 至少需要一项")
        files = []
    file_refs: dict[str, dict[str, Any]] = {}
    relative_paths: set[str] = set()
    for index, file_item in enumerate(files, start=1):
        label = f"files[{index}]"
        if not isinstance(file_item, dict):
            errors.append(f"{label} 必须是对象")
            continue
        file_ref = file_item.get("clientRef")
        relative = file_item.get("relativePath")
        if not isinstance(file_ref, str) or not file_ref:
            errors.append(f"{label}.clientRef 不能为空")
        elif file_ref in file_refs:
            errors.append(f"文件 clientRef 重复：{file_ref}")
        else:
            file_refs[file_ref] = file_item
        try:
            safe_relative = assert_safe_relative(str(relative))
            if safe_relative in relative_paths:
                errors.append(f"文件 relativePath 重复：{safe_relative}")
            relative_paths.add(safe_relative)
        except RegistryError as error:
            errors.append(str(error))
        if file_item.get("storageKind") not in FILE_KINDS:
            errors.append(f"{label}.storageKind 不合法")
        document_version_kind = file_item.get("documentVersionKind")
        if file_item.get("storageKind") == "NORMALIZED_FILE":
            if document_version_kind not in SPLIT_DOCUMENT_VERSION_KINDS:
                errors.append(f"{label}.documentVersionKind 不合法")
        elif document_version_kind is not None:
            errors.append(f"{label} 只有规范化 PDF 可以填写 documentVersionKind")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(file_item.get("sha256", ""))):
            errors.append(f"{label}.sha256 格式错误")
        source_ref = file_item.get("sourceFileRef")
        if source_ref and source_ref not in file_refs:
            # 前向引用在循环后再次检查。
            pass
    for file_item in files:
        if isinstance(file_item, dict):
            source_ref = file_item.get("sourceFileRef")
            if source_ref and source_ref not in file_refs:
                errors.append(f"规范化文件引用了未知 sourceFileRef：{source_ref}")

    document_review_requirements: set[tuple[str, str]] = set()
    version_file_owners: dict[str, str] = {}
    document_identity_owners: dict[tuple[str, str, str, str], str] = {}
    manifest_documents = manifest.get("documents", [])
    if not isinstance(manifest_documents, list):
        errors.append("documents 必须是数组")
        manifest_documents = []
    for index, document in enumerate(manifest_documents, start=1):
        add_entity(document, f"documents[{index}]")
        if not isinstance(document, dict):
            continue
        document_ref = str(document.get("clientRef", ""))
        identity = normalized_document_identity(document)
        identity_owner = document_identity_owners.get(identity)
        if identity_owner:
            errors.append(
                f"逻辑文书身份重复：{identity_owner} 与 {document_ref}；"
                "阶段、类型、文号、日期必须唯一"
            )
        else:
            document_identity_owners[identity] = document_ref
        for ref in document.get("productRefs", []):
            if ref not in entity_map:
                errors.append(f"文书引用了未知 productRef：{ref}")
        for ref in document.get("inspectionRefs", []):
            if ref not in entity_map:
                errors.append(f"文书引用了未知 inspectionRef：{ref}")
        versions = document.get("versions")
        if not isinstance(versions, list):
            errors.append(f"documents[{index}].versions 必须是数组")
            versions = []
        if len(versions) > len(DOCUMENT_VERSION_KINDS):
            errors.append(f"documents[{index}].versions 最多包含电子版和扫描件各一份")
        version_kinds: set[str] = set()
        version_file_refs: set[str] = set()
        selected_source_keys: set[tuple[str, Any, Any]] = set()
        for version_index, version in enumerate(versions, start=1):
            version_label = f"documents[{index}].versions[{version_index}]"
            if not isinstance(version, dict):
                errors.append(f"{version_label} 必须是对象")
                continue
            if set(version) != {"fileRef", "kind"}:
                errors.append(f"{version_label} 只允许 fileRef 和 kind")
            kind = version.get("kind")
            if kind not in DOCUMENT_VERSION_KINDS:
                errors.append(f"{version_label}.kind 必须为 ELECTRONIC 或 SCANNED")
            elif kind in version_kinds:
                errors.append(f"documents[{index}] 同一 kind 只能有一个正式版本：{kind}")
            else:
                version_kinds.add(kind)
            file_ref = version.get("fileRef")
            if not isinstance(file_ref, str) or file_ref not in file_refs:
                errors.append(f"{version_label}.fileRef 引用了未知文件：{file_ref}")
                continue
            if file_ref in version_file_refs:
                errors.append(f"documents[{index}] 正式版本 fileRef 重复：{file_ref}")
            version_file_refs.add(file_ref)
            owner = version_file_owners.get(file_ref)
            if owner and owner != document_ref:
                errors.append(f"正式版本文件 {file_ref} 已属于其他逻辑文书：{owner}")
            else:
                version_file_owners[file_ref] = document_ref
            version_file = file_refs[file_ref]
            if version_file.get("storageKind") != "NORMALIZED_FILE":
                errors.append(f"{version_label} 只能引用 NORMALIZED_FILE")
            if version_file.get("mimeType") != "application/pdf":
                errors.append(f"{version_label} 只能引用 PDF")
            if version_file.get("documentVersionKind") != kind:
                errors.append(f"{version_label}.kind 与规范化文件版本类型不一致")
            source_file_ref = version_file.get("sourceFileRef")
            if isinstance(source_file_ref, str):
                selected_source_keys.add(
                    (
                        source_file_ref,
                        version_file.get("sourcePageStart"),
                        version_file.get("sourcePageEnd"),
                    )
                )

        linked_source_keys: set[tuple[str, Any, Any]] = set()
        for link in document.get("fileLinks", []):
            if not isinstance(link, dict) or link.get("fileRef") not in file_refs:
                errors.append(f"文书引用了未知 fileRef：{link}")
            elif link.get("relationRole") not in FILE_ROLES:
                errors.append(f"文书文件关系不合法：{link.get('relationRole')}")
            else:
                linked_file_ref = str(link["fileRef"])
                linked_source_keys.add(
                    (linked_file_ref, link.get("pageStart"), link.get("pageEnd"))
                )
                if file_refs[linked_file_ref].get("storageKind") != "ORIGINAL_FILE":
                    errors.append("文书 fileLinks 只保存 ORIGINAL_FILE 原始来源映射")
        missing_source_keys = selected_source_keys - linked_source_keys
        if missing_source_keys:
            missing_sources = [
                f"{file_ref}"
                + (
                    f"（第{page_start}-{page_end}页）"
                    if page_start is not None and page_end is not None
                    else ""
                )
                for file_ref, page_start, page_end in sorted(
                    missing_source_keys,
                    key=lambda key: (str(key[0]), int(key[1] or 0), int(key[2] or 0)),
                )
            ]
            errors.append(
                f"documents[{index}] 正式版本缺少原始来源 fileLinks：" + "、".join(missing_sources)
            )
        if not versions:
            document_review_requirements.add((document_ref, "LOW_CONFIDENCE"))
        if versions and linked_source_keys - selected_source_keys:
            document_review_requirements.add((document_ref, "DUPLICATE_CANDIDATE"))

    for index, requirement in enumerate(manifest.get("documentRequirements", []), start=1):
        add_entity(requirement, f"documentRequirements[{index}]")
        if not isinstance(requirement, dict):
            continue
        if requirement.get("status") not in REQUIREMENT_STATUSES:
            errors.append(f"documentRequirements[{index}].status 不合法")
        for key in ("caseRef", "productRef", "inspectionRef"):
            ref = requirement.get(key)
            if ref and ref not in entity_map:
                errors.append(f"资料要求引用了未知 {key}：{ref}")

    for index, evidence in enumerate(manifest.get("fieldEvidence", []), start=1):
        if not isinstance(evidence, dict):
            errors.append(f"fieldEvidence[{index}] 必须是对象")
            continue
        entity_ref = evidence.get("entityRef")
        entity = entity_map.get(entity_ref)
        if not entity:
            errors.append(f"字段证据引用了未知 entityRef：{entity_ref}")
        else:
            try:
                actual = entity_value(entity, str(evidence.get("fieldPath", "")))
                if actual != evidence.get("value"):
                    errors.append(
                        f"字段证据值与实体不一致：{entity_ref}.{evidence.get('fieldPath')}"
                    )
            except KeyError:
                errors.append(f"字段证据路径不存在：{entity_ref}.{evidence.get('fieldPath')}")
        if evidence.get("trustLevel") not in TRUST_LEVELS:
            errors.append(f"fieldEvidence[{index}].trustLevel 不合法")
        if evidence.get("fieldPath") == "onlineSale" and not str(entity_ref).startswith("product:"):
            errors.append("onlineSale 字段证据只能关联 product 实体")
        sources = evidence.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"fieldEvidence[{index}].sources 至少需要一项")
            continue
        for evidence_source in sources:
            if isinstance(evidence_source, dict):
                file_ref = evidence_source.get("fileRef")
                if file_ref and file_ref not in file_refs:
                    errors.append(f"字段证据引用了未知 fileRef：{file_ref}")

    for index, missing in enumerate(manifest.get("missingItems", []), start=1):
        if not isinstance(missing, dict):
            errors.append(f"missingItems[{index}] 必须是对象")
            continue
        if missing.get("entityRef") not in entity_map:
            errors.append(f"缺失项引用了未知 entityRef：{missing.get('entityRef')}")
        if not str(missing.get("fieldPath", "")).strip():
            errors.append(f"missingItems[{index}].fieldPath 不能为空")
        if not str(missing.get("reason", "")).strip():
            errors.append(f"missingItems[{index}].reason 不能为空")
        if missing.get("fieldPath") == "onlineSale" and not str(
            missing.get("entityRef", "")
        ).startswith("product:"):
            errors.append("onlineSale 缺失项只能关联 product 实体")

    review_items = manifest.get("reviewItems", [])
    if not isinstance(review_items, list):
        errors.append("reviewItems 必须是数组")
        review_items = []
    occupied_client_refs = set(entity_map) | set(file_refs)
    review_item_refs: set[str] = set()
    for index, review in enumerate(review_items, start=1):
        label = f"reviewItems[{index}]"
        if not isinstance(review, dict):
            errors.append(f"{label} 必须是对象")
            continue
        client_ref = review.get("clientRef")
        if not isinstance(client_ref, str) or not re.fullmatch(r"[a-z]+:.+", client_ref):
            errors.append(f"{label}.clientRef 必须是稳定的 lowercase 前缀引用")
        elif client_ref in occupied_client_refs or client_ref in review_item_refs:
            errors.append(f"clientRef 重复：{client_ref}")
        else:
            review_item_refs.add(client_ref)
        entity_ref = review.get("entityRef")
        entity = entity_map.get(entity_ref)
        if not entity:
            errors.append(f"{label}.entityRef 引用了未知实体：{entity_ref}")
        entity_type = str(entity_ref).split(":", maxsplit=1)[0]
        if entity and entity_type not in REVIEWABLE_ENTITY_TYPES:
            errors.append(f"{label} 不能绑定到 {entity_type} 实体")
        field_path = review.get("fieldPath")
        if not isinstance(field_path, str) or not field_path.strip():
            errors.append(f"{label}.fieldPath 不能为空")
        elif (
            entity
            and review.get("issueType") != "DATA_ANOMALY"
            and field_path not in REVIEWABLE_ENTITY_FIELDS.get(entity_type, set())
        ):
            errors.append(f"{label}.fieldPath 不是可抽取字段：{entity_ref}.{field_path}")
        if review.get("issueType") not in REVIEW_ISSUE_TYPES:
            errors.append(f"{label}.issueType 不合法")
        if field_path == "onlineSale" and entity_type != "product":
            errors.append("onlineSale 待核对项只能关联 product 实体")
        for value_key in ("currentValue", "incomingValue"):
            if value_key not in review:
                continue
            try:
                json.dumps(review[value_key], ensure_ascii=False)
            except (TypeError, ValueError):
                errors.append(f"{label}.{value_key} 必须是 JSON 值")
        message = review.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 4_000:
            errors.append(f"{label}.message 必须是 1—4000 字符")
        candidates = review.get("candidates")
        if candidates is None:
            continue
        if not isinstance(candidates, list) or not candidates:
            errors.append(f"{label}.candidates 必须是非空数组")
            continue
        if review.get("issueType") == "VALUE_CONFLICT" and len(candidates) < 2:
            errors.append(f"{label} VALUE_CONFLICT 至少需要两个候选")
        candidate_refs: set[str] = set()
        candidate_values: set[str] = set()
        contains_entity_value = False
        entity_value_for_review = (
            entity.get(field_path) if entity and isinstance(field_path, str) else None
        )
        for candidate_index, candidate in enumerate(candidates, start=1):
            candidate_label = f"{label}.candidates[{candidate_index}]"
            if not isinstance(candidate, dict):
                errors.append(f"{candidate_label} 必须是对象")
                continue
            if set(candidate) - {"candidateRef", "value", "trustLevel", "sources"}:
                errors.append(f"{candidate_label} 包含不受支持的字段")
            candidate_ref = candidate.get("candidateRef")
            if not isinstance(candidate_ref, str) or not re.fullmatch(r"[a-z]+:.+", candidate_ref):
                errors.append(f"{candidate_label}.candidateRef 必须是稳定的 lowercase 前缀引用")
            elif candidate_ref in candidate_refs:
                errors.append(f"{label} 候选标识重复：{candidate_ref}")
            else:
                candidate_refs.add(candidate_ref)
            if "value" not in candidate:
                errors.append(f"{candidate_label}.value 不能为空")
                candidate_value_key = "__missing__"
            else:
                try:
                    candidate_value_key = json.dumps(
                        candidate["value"], ensure_ascii=False, sort_keys=True
                    )
                except (TypeError, ValueError):
                    errors.append(f"{candidate_label}.value 必须是 JSON 值")
                    candidate_value_key = "__invalid__"
                if candidate_value_key in candidate_values:
                    errors.append(f"{label} 候选值重复：{candidate_ref}")
                else:
                    candidate_values.add(candidate_value_key)
                if candidate.get("value") == entity_value_for_review:
                    contains_entity_value = True
            if candidate.get("trustLevel") not in TRUST_LEVELS:
                errors.append(f"{candidate_label}.trustLevel 不合法")
            sources = candidate.get("sources")
            if not isinstance(sources, list) or not sources:
                errors.append(f"{candidate_label}.sources 至少需要一项")
                continue
            for source_index, candidate_source in enumerate(sources, start=1):
                source_label = f"{candidate_label}.sources[{source_index}]"
                if not isinstance(candidate_source, dict):
                    errors.append(f"{source_label} 必须是对象")
                    continue
                if set(candidate_source) - {"kind", "fileRef", "page", "value", "evidence"}:
                    errors.append(f"{source_label} 包含不受支持的字段")
                if (
                    not isinstance(candidate_source.get("kind"), str)
                    or not candidate_source["kind"].strip()
                ):
                    errors.append(f"{source_label}.kind 不能为空")
                source_file_ref = candidate_source.get("fileRef")
                page = candidate_source.get("page")
                if page is not None and (
                    not isinstance(page, int) or isinstance(page, bool) or page < 1
                ):
                    errors.append(f"{source_label}.page 必须是正整数")
                if page is not None and not source_file_ref:
                    errors.append(f"{source_label}.page 必须关联 fileRef")
                if source_file_ref:
                    source_file = file_refs.get(source_file_ref)
                    if source_file is None:
                        errors.append(f"{source_label}.fileRef 引用了未知文件：{source_file_ref}")
                    elif (
                        isinstance(page, int)
                        and source_file.get("pageCount")
                        and page > source_file["pageCount"]
                    ):
                        errors.append(f"{source_label}.page 超出文件页数")
                if "value" in candidate_source and candidate_source["value"] != candidate.get(
                    "value"
                ):
                    errors.append(f"{source_label}.value 必须等于候选 value")
        if review.get("issueType") == "VALUE_CONFLICT" and not contains_entity_value:
            errors.append(f"{label} VALUE_CONFLICT 候选必须包含实体最终值")

    review_resolution_keys = {
        (str(item.get("entityRef", "")), str(item.get("issueType", "")))
        for item in review_items
        if isinstance(item, dict) and item.get("fieldPath") == "versions"
    }
    for document_ref, issue_type in sorted(document_review_requirements):
        if (document_ref, issue_type) not in review_resolution_keys:
            errors.append(f"文书 {document_ref} 的 versions 必须创建 {issue_type} 待核对项")

    case_ref = str(case.get("clientRef", ""))
    case_type = case.get("caseType")
    evidence_items = manifest.get("fieldEvidence")
    evidence_items = evidence_items if isinstance(evidence_items, list) else []
    missing_items = manifest.get("missingItems")
    missing_items = missing_items if isinstance(missing_items, list) else []
    if case_type == "CRIMINAL" and not has_direct_criminal_evidence(evidence_items, case_ref):
        errors.append("刑案必须具有含页码和直接刑事表述的 caseType 字段证据")
    if case_type == "ADMINISTRATIVE":
        if not failed_recheck_refs:
            errors.append("行案必须存在整改复查不合格检查记录")
        elif not has_administrative_rule_evidence(evidence_items, case_ref, failed_recheck_refs):
            errors.append("行案必须具有引用整改复查不合格记录的 RULE 字段证据")
    if failed_recheck_refs and case_type not in {"ADMINISTRATIVE", "CRIMINAL"}:
        errors.append("存在整改复查不合格记录时，案卷类型必须为 ADMINISTRATIVE 或已证实的 CRIMINAL")
    if case_type == "UNKNOWN" and not has_case_type_missing_item(missing_items, case_ref):
        errors.append("UNKNOWN 案卷类型必须创建 caseType 待核对项")
    if case_type == "NONE":
        none_evidence = [
            item
            for item in evidence_items
            if is_case_type_evidence(item, case_ref, "NONE") and item.get("trustLevel") == "MANUAL"
        ]
        if not none_evidence:
            errors.append("NONE 案卷类型不得作为默认值，必须具有明确人工确认的字段证据")

    if upload_files is not None:
        for file_ref, file_item in file_refs.items():
            local_value = upload_files.get(file_ref)
            if not local_value:
                errors.append(f"upload-map 缺少：{file_ref}")
                continue
            local_path = Path(local_value)
            if not local_path.is_file():
                errors.append(f"本地上传文件不存在：{local_path}")
                continue
            if file_sha256(local_path) != file_item["sha256"]:
                errors.append(f"本地上传文件哈希不一致：{file_item['relativePath']}")
    return errors


def validate_command(args: argparse.Namespace) -> None:
    manifest = read_json(Path(args.manifest).resolve())
    upload_map: dict[str, str] | None = None
    if args.upload_map:
        upload_map_doc = read_json(Path(args.upload_map).resolve())
        upload_map = upload_map_doc.get("files")
        if not isinstance(upload_map, dict):
            raise RegistryError("upload-map.files 必须是对象")
    errors = validate_manifest(manifest, upload_map)
    print(
        json.dumps(
            {"valid": not errors, "errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    )
    if errors:
        raise SystemExit(1)


def response_json(response: httpx.Response, action: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        body = response.text[:4_000]
        raise RegistryError(f"{action}失败：HTTP {response.status_code}\n{body}") from error
    try:
        value = response.json()
    except ValueError as error:
        raise RegistryError(f"{action}返回的不是 JSON") from error
    if not isinstance(value, dict):
        raise RegistryError(f"{action}返回 JSON 顶层不是对象")
    return value


def response_json_list(response: httpx.Response, action: str) -> list[dict[str, Any]]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        body = response.text[:4_000]
        raise RegistryError(f"{action}失败：HTTP {response.status_code}\n{body}") from error
    try:
        value = response.json()
    except ValueError as error:
        raise RegistryError(f"{action}返回的不是 JSON") from error
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RegistryError(f"{action}返回 JSON 顶层不是对象数组")
    return value


def normalized_document_identity(document: dict[str, Any]) -> tuple[str, str, str, str]:
    issue_date = str(document.get("issueDate") or "").strip()
    return (
        str(document.get("stage") or "").strip().upper(),
        str(document.get("documentType") or "").strip().upper(),
        str(document.get("documentNo") or "").strip(),
        issue_date[:10] if issue_date else "",
    )


def server_document_version_sha(document: dict[str, Any], kind: str) -> str | None:
    active_versions = [
        version
        for version in document.get("versions", [])
        if isinstance(version, dict)
        and version.get("kind") == kind
        and not version.get("deletedAt")
    ]
    if len(active_versions) != 1:
        return None
    file_asset = active_versions[0].get("fileAsset")
    sha256 = file_asset.get("sha256") if isinstance(file_asset, dict) else None
    return sha256 if isinstance(sha256, str) else None


def sync_document_versions_command(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest).resolve()
    upload_map_path = Path(args.upload_map).resolve()
    manifest = read_json(manifest_path)
    upload_map_doc = read_json(upload_map_path)
    upload_files = upload_map_doc.get("files")
    if not isinstance(upload_files, dict):
        raise RegistryError("upload-map.files 必须是对象")
    project_no = str(manifest.get("case", {}).get("projectNo", ""))
    state_path = manifest_path.parent / "document-version-sync-state.json"
    identity_refs: dict[tuple[str, str, str, str], list[str]] = {}
    manifest_documents = manifest.get("documents", [])
    if isinstance(manifest_documents, list):
        for document in manifest_documents:
            if not isinstance(document, dict):
                continue
            identity_refs.setdefault(normalized_document_identity(document), []).append(
                str(document.get("clientRef", ""))
            )
    duplicate_identities = [
        (identity, refs) for identity, refs in identity_refs.items() if len(refs) > 1
    ]
    if duplicate_identities:
        state = {
            "stateVersion": 1,
            "manifestSha256": file_sha256(manifest_path),
            "projectNo": project_no,
            "apiBase": args.api_base.rstrip("/"),
            "dryRun": bool(args.dry_run),
            "updatedAt": utc_now(),
            "status": "NEEDS_REVIEW",
            "results": [
                {
                    "scope": "local-document",
                    "status": "DUPLICATE_IDENTITY",
                    "identity": list(identity),
                    "documentRefs": refs,
                }
                for identity, refs in duplicate_identities
            ],
        }
        write_json(state_path, state)
        raise RegistryError(
            "本地清单存在重复逻辑文书身份，拒绝向同一服务端文书连续写入；"
            f"待处理状态已写入 {state_path}"
        )
    errors = validate_manifest(manifest, upload_files)
    if errors:
        raise RegistryError("同步文书版本前本地校验失败：\n- " + "\n- ".join(errors))

    api_base = args.api_base.rstrip("/")
    parsed = urlsplit(api_base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RegistryError("文书版本同步 api-base 必须是有效 HTTPS 地址")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    project_no = str(manifest["case"]["projectNo"])
    manifest_files = {item["clientRef"]: item for item in manifest["files"]}
    state: dict[str, Any] = {
        "stateVersion": 1,
        "manifestSha256": file_sha256(manifest_path),
        "projectNo": project_no,
        "apiBase": api_base,
        "dryRun": bool(args.dry_run),
        "updatedAt": utc_now(),
        "status": "RUNNING",
        "results": [],
    }

    def persist_state(status: str | None = None) -> None:
        if status is not None:
            state["status"] = status
        state["updatedAt"] = utc_now()
        write_json(state_path, state)

    persist_state()
    pending_messages: list[str] = []
    headers = {WRITE_HEADER: WRITE_HEADER_VALUE, "Origin": origin}
    timeout = httpx.Timeout(args.timeout, read=max(args.timeout, 300.0))
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        ready = response_json(client.get(f"{api_base}/api/ready"), "readiness")
        if ready.get("status") != "ready":
            raise RegistryError(f"服务未就绪：{ready}")
        cases_result = response_json(
            client.get(
                f"{api_base}/api/v1/cases",
                params={"projectNo": project_no, "page": 1, "pageSize": 100},
            ),
            "按项目编号查询案卷",
        )
        cases = cases_result.get("data")
        if not isinstance(cases, list):
            raise RegistryError("按项目编号查询案卷响应缺少 data 数组")
        exact_cases = [
            item
            for item in cases
            if isinstance(item, dict)
            and item.get("projectNo") == project_no
            and not item.get("deletedAt")
        ]
        if len(exact_cases) != 1:
            state["results"].append(
                {
                    "scope": "case",
                    "status": "NO_UNIQUE_MATCH",
                    "matches": len(exact_cases),
                }
            )
            persist_state("NEEDS_REVIEW")
            raise RegistryError(
                f"项目编号 {project_no} 匹配到 {len(exact_cases)} 个活动案卷，拒绝猜测；"
                f"待处理状态已写入 {state_path}"
            )
        case_id = str(exact_cases[0]["id"])
        server_documents = response_json_list(
            client.get(f"{api_base}/api/v1/cases/{case_id}/documents"),
            "查询案卷文书",
        )

        for local_document in manifest.get("documents", []):
            versions = local_document.get("versions", [])
            if not versions:
                continue
            identity = normalized_document_identity(local_document)
            matches = [
                item
                for item in server_documents
                if not item.get("deletedAt") and normalized_document_identity(item) == identity
            ]
            if len(matches) != 1:
                message = (
                    f"文书 {local_document.get('clientRef')} 按阶段、类型、文号、日期"
                    f"匹配到 {len(matches)} 项"
                )
                pending_messages.append(message)
                state["results"].append(
                    {
                        "documentRef": local_document.get("clientRef"),
                        "identity": identity,
                        "status": "NO_UNIQUE_MATCH",
                        "matches": len(matches),
                    }
                )
                persist_state("NEEDS_REVIEW")
                continue
            server_document = matches[0]
            for version in versions:
                kind = str(version["kind"])
                file_ref = str(version["fileRef"])
                file_item = manifest_files[file_ref]
                local_path = Path(str(upload_files[file_ref])).resolve()
                current_sha256 = server_document_version_sha(server_document, kind)
                if current_sha256 == file_item["sha256"]:
                    state["results"].append(
                        {
                            "documentRef": local_document.get("clientRef"),
                            "documentId": server_document["id"],
                            "kind": kind,
                            "status": "UNCHANGED",
                            "sha256": file_item["sha256"],
                        }
                    )
                    persist_state()
                    continue
                if args.dry_run:
                    state["results"].append(
                        {
                            "documentRef": local_document.get("clientRef"),
                            "documentId": server_document["id"],
                            "kind": kind,
                            "status": "WOULD_UPLOAD",
                            "sha256": file_item["sha256"],
                        }
                    )
                    persist_state()
                    continue
                expected_version = server_document.get("version")
                if not isinstance(expected_version, int) or expected_version < 1:
                    pending_messages.append(
                        f"文书 {server_document.get('id')} 缺少有效 expectedDocumentVersion"
                    )
                    state["results"].append(
                        {
                            "documentRef": local_document.get("clientRef"),
                            "documentId": server_document.get("id"),
                            "kind": kind,
                            "status": "INVALID_SERVER_VERSION",
                        }
                    )
                    persist_state("NEEDS_REVIEW")
                    continue
                sync_result: dict[str, Any] = {
                    "documentRef": local_document.get("clientRef"),
                    "documentId": server_document["id"],
                    "kind": kind,
                    "status": "UPLOADING",
                    "sha256": file_item["sha256"],
                    "expectedDocumentVersion": expected_version,
                }
                state["results"].append(sync_result)
                persist_state()
                phase = "UPLOAD"
                try:
                    with local_path.open("rb") as stream:
                        response_json(
                            client.put(
                                f"{api_base}/api/v1/documents/{server_document['id']}/versions/{kind}",
                                data={"expectedDocumentVersion": str(expected_version)},
                                files={"file": (local_path.name, stream, "application/pdf")},
                            ),
                            f"同步文书 {server_document['id']} 的 {kind} 版本",
                        )
                    sync_result["status"] = "UPLOAD_ACCEPTED"
                    persist_state()
                    phase = "REFRESH"
                    server_document = response_json(
                        client.get(f"{api_base}/api/v1/documents/{server_document['id']}"),
                        "刷新文书版本",
                    )
                    sync_result["status"] = "UPLOADED"
                    sync_result["serverDocumentVersion"] = server_document.get("version")
                    persist_state()
                except Exception as error:
                    error_summary = str(error).strip()[:1_000] or type(error).__name__
                    sync_result.update(
                        {
                            "status": "FAILED",
                            "failedPhase": phase,
                            "error": error_summary,
                            "remoteWriteMayHaveSucceeded": phase == "REFRESH",
                        }
                    )
                    persist_state("NEEDS_REVIEW")
                    raise RegistryError(
                        f"文书 {local_document.get('clientRef')} 的 {kind} 版本同步失败"
                        f"（{phase}）：{error_summary}；状态已写入 {state_path}"
                    ) from error

    final_status = "NEEDS_REVIEW" if pending_messages else "DRY_RUN" if args.dry_run else "DONE"
    persist_state(final_status)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if pending_messages:
        raise RegistryError(
            "文书版本同步存在待处理项：\n- "
            + "\n- ".join(pending_messages)
            + f"\n状态已写入 {state_path}"
        )


def upload_command(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest).resolve()
    upload_map_path = Path(args.upload_map).resolve()
    manifest = read_json(manifest_path)
    upload_map_doc = read_json(upload_map_path)
    upload_files = upload_map_doc.get("files")
    if not isinstance(upload_files, dict):
        raise RegistryError("upload-map.files 必须是对象")
    errors = validate_manifest(manifest, upload_files)
    if errors:
        raise RegistryError("上传前本地校验失败：\n- " + "\n- ".join(errors))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry-run-ok",
                    "apiBase": args.api_base.rstrip("/"),
                    "projectNo": manifest["case"]["projectNo"],
                    "files": len(manifest["files"]),
                    "willFinalize": bool(args.finalize),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    api_base = args.api_base.rstrip("/")
    parsed = urlsplit(api_base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RegistryError("正式上传 api-base 必须是有效 HTTPS 地址")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    state_path = manifest_path.parent / "upload-state.json"
    manifest_sha256 = file_sha256(manifest_path)
    state = read_json(state_path) if state_path.exists() else {}
    if state and state.get("manifestSha256") != manifest_sha256:
        raise RegistryError("upload-state.json 属于另一份 manifest，拒绝混用")
    state.setdefault("stateVersion", 1)
    state.setdefault("manifestSha256", manifest_sha256)
    state.setdefault("uploadedFileRefs", [])

    package_hash = manifest["source"]["packageSha256"].split(":", 1)[1]
    idempotency_key = f"xfpcr-v1-{package_hash}"
    headers = {WRITE_HEADER: WRITE_HEADER_VALUE, "Origin": origin}
    timeout = httpx.Timeout(args.timeout, read=max(args.timeout, 300.0))
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        ready = response_json(client.get(f"{api_base}/api/ready"), "readiness")
        if ready.get("status") != "ready":
            raise RegistryError(f"服务未就绪：{ready}")
        create_body = {
            "sourceType": manifest["source"]["sourceType"],
            "packageName": manifest["source"]["packageName"],
            "containerKind": manifest["source"]["containerKind"],
            "packageSha256": manifest["source"]["packageSha256"],
            "packageHashMethod": manifest["source"]["packageHashMethod"],
            "extractorName": manifest["source"]["extractor"]["name"],
            "extractorVersion": manifest["source"]["extractor"]["version"],
        }
        created = response_json(
            client.post(
                f"{api_base}/api/v1/import-jobs",
                headers={"Idempotency-Key": idempotency_key},
                json=create_body,
            ),
            "创建导入任务",
        )
        job = created.get("job")
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise RegistryError(f"创建任务响应缺少 job.id：{created}")
        job_id = job["id"]
        if state.get("jobId") and state["jobId"] != job_id:
            raise RegistryError("同一 manifest 返回了不同 jobId，拒绝继续")
        state["jobId"] = job_id
        write_json(state_path, state)

        if job.get("status") in {"FINALIZED", "NEEDS_REVIEW"}:
            state["finalized"] = True
            if job.get("resultSummary") is not None:
                state["finalizeResult"] = job["resultSummary"]
            state["updatedAt"] = utc_now()
            write_json(state_path, state)
            print(
                json.dumps(
                    {
                        "jobId": job_id,
                        "status": job["status"],
                        "idempotentReplay": True,
                        "finalize": job.get("resultSummary"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        uploaded = set(state["uploadedFileRefs"])
        manifest_files = {item["clientRef"]: item for item in manifest["files"]}
        for file_ref, file_item in manifest_files.items():
            if file_ref in uploaded:
                continue
            local_path = Path(upload_files[file_ref])
            data = {
                "relativePath": file_item["relativePath"],
                "storageKind": file_item["storageKind"],
                "sha256": file_item["sha256"],
                **(
                    {"pageCount": str(file_item["pageCount"])} if file_item.get("pageCount") else {}
                ),
            }
            with local_path.open("rb") as stream:
                response_json(
                    client.post(
                        f"{api_base}/api/v1/import-jobs/{job_id}/files",
                        data=data,
                        files={
                            "file": (
                                local_path.name,
                                stream,
                                file_item["mimeType"],
                            )
                        },
                    ),
                    f"上传 {file_item['relativePath']}",
                )
            uploaded.add(file_ref)
            state["uploadedFileRefs"] = sorted(uploaded)
            state["updatedAt"] = utc_now()
            write_json(state_path, state)

        manifest_result = response_json(
            client.put(
                f"{api_base}/api/v1/import-jobs/{job_id}/manifest",
                json=manifest,
            ),
            "提交 manifest",
        )
        validation_result = response_json(
            client.post(f"{api_base}/api/v1/import-jobs/{job_id}/validate"),
            "服务端校验 manifest",
        )
        state["manifestSubmitted"] = True
        state["validationResult"] = validation_result
        result: dict[str, Any] = {
            "jobId": job_id,
            "manifest": manifest_result,
            "validation": validation_result,
        }
        if args.finalize:
            finalize_result = response_json(
                client.post(f"{api_base}/api/v1/import-jobs/{job_id}/finalize"),
                "终结导入",
            )
            state["finalized"] = True
            state["finalizeResult"] = finalize_result
            result["finalize"] = finalize_result
        state["updatedAt"] = utc_now()
        write_json(state_path, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="消防产品案卷清点、OCR、拆分、manifest 与幂等上传工具"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory", help="清点目录/ZIP 并提取 PDF 文本层")
    inventory_parser.add_argument("input")
    inventory_parser.add_argument("--work-dir", required=True)
    inventory_parser.set_defaults(func=inventory_command)

    ocr_parser = subparsers.add_parser("ocr", help="对无有效文本层的 PDF 页及单页图片运行 Zerox")
    ocr_parser.add_argument("--work-dir", required=True)
    ocr_parser.add_argument("--zerox", default=str(DEFAULT_ZEROX))
    ocr_parser.add_argument("--poppler", default=str(DEFAULT_POPPLER))
    ocr_parser.add_argument("--concurrency", type=int, default=1, choices=range(1, 5))
    ocr_parser.add_argument("--timeout", type=int, default=600)
    ocr_parser.add_argument("--continue-on-error", action="store_true")
    ocr_parser.set_defaults(func=ocr_command)

    source_analysis_parser = subparsers.add_parser(
        "source-analysis", help="分析截图、电子文本与扫描件来源优先级，不推断业务值"
    )
    source_analysis_parser.add_argument("--work-dir", required=True)
    source_analysis_parser.set_defaults(func=source_analysis_command)

    split_parser = subparsers.add_parser("split", help="按已核对计划生成规范化 PDF")
    split_parser.add_argument("--work-dir", required=True)
    split_parser.add_argument("--plan", required=True)
    split_parser.set_defaults(func=split_command)

    compose_parser = subparsers.add_parser("compose", help="组装 CaseImportManifestV1")
    compose_parser.add_argument("--work-dir", required=True)
    compose_parser.add_argument("--case-data", required=True)
    compose_parser.set_defaults(func=compose_command)

    validate_parser = subparsers.add_parser("validate", help="本地校验 manifest 和上传文件")
    validate_parser.add_argument("--manifest", required=True)
    validate_parser.add_argument("--upload-map")
    validate_parser.set_defaults(func=validate_command)

    upload_parser = subparsers.add_parser("upload", help="幂等上传 manifest 和文件")
    upload_parser.add_argument("--manifest", required=True)
    upload_parser.add_argument("--upload-map", required=True)
    upload_parser.add_argument("--api-base", required=True)
    upload_parser.add_argument("--timeout", type=float, default=60.0)
    upload_parser.add_argument("--dry-run", action="store_true")
    upload_parser.add_argument("--finalize", action="store_true")
    upload_parser.set_defaults(func=upload_command)

    sync_versions_parser = subparsers.add_parser(
        "sync-document-versions",
        help="不依赖导入任务状态，按唯一文书身份幂等同步电子版/扫描件",
    )
    sync_versions_parser.add_argument("--manifest", required=True)
    sync_versions_parser.add_argument("--upload-map", required=True)
    sync_versions_parser.add_argument("--api-base", required=True)
    sync_versions_parser.add_argument("--timeout", type=float, default=60.0)
    sync_versions_parser.add_argument("--dry-run", action="store_true")
    sync_versions_parser.set_defaults(func=sync_document_versions_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RegistryError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
