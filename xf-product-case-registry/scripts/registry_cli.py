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

VERSION = "0.3.1"
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
DIRECT_CRIMINAL_EVIDENCE_RE = re.compile(
    r"刑事案件|刑案|移送\s*(?:公安|公安机关)|公安机关.*移送"
)
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
                "pdfPages": sum(item.get("pageCount", 0) for item in files),
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
        "--pages",
        str(page_number),
        "--maintain-format",
        "true",
    ]
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
        file_name = (
            f"{project_no}_{STAGE_LABELS[stage]}_{document_label}_"
            f"{number_or_date}_{sequence:02d}.pdf"
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
                "stage": stage,
                "documentType": document_type,
                "sequence": sequence,
            }
        )
    split_index = {
        "splitIndexVersion": 1,
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
    return isinstance(source, dict) and bool(
        source.get("relativePath") or source.get("fileRef")
    )


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
            item
            for item in evidence_items
            if is_case_type_evidence(item, case_ref, "CRIMINAL")
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


def compose_command(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    inventory = read_json(work_dir / "inventory.json")
    case_data = read_json(Path(args.case_data).resolve())
    split_index_path = work_dir / "split-index.json"
    split_index = read_json(split_index_path) if split_index_path.exists() else {"items": []}
    case, _ = build_entities(case_data)
    normalize_case_type(case_data, case)

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
    for item in split_index.get("items", []):
        add_file(item, "NORMALIZED_FILE")

    documents: list[dict[str, Any]] = []
    for index, source_document in enumerate(case_data.get("documents", []), start=1):
        document = dict(source_document)
        document.setdefault("clientRef", f"document:{index}")
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
        document["fileLinks"] = links
        document.setdefault("productRefs", [])
        document.setdefault("inspectionRefs", [])
        documents.append(document)

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
                if (
                    not isinstance(case_inspection_ref, str)
                    or not CASE_INSPECTION_REF_RE.match(case_inspection_ref)
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
                        errors.append(
                            f"案卷检查分组 {case_inspection_ref} 的阶段或检查日期不一致"
                        )
            if (
                inspection.get("stage") == "RECHECK"
                and inspection.get("inspectionResult") == "UNQUALIFIED"
                and isinstance(inspection.get("clientRef"), str)
                and inspection["clientRef"]
            ):
                failed_recheck_refs.append(inspection["clientRef"])
            reinspection_status = inspection.get(
                "reinspectionStatus", "NOT_APPLIED"
            )
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
                inspection.get(field) not in (None, "")
                for field in reinspection_detail_fields
            )
            has_reinspection_details = any(
                inspection.get(field) not in (None, "")
                for field in reinspection_detail_fields
            )
            if (
                reinspection_status == "NOT_APPLIED"
                and has_reinspection_details
            ):
                errors.append(f"{label} 复检状态为未申请时不能包含复检详情")
            if has_reinspection and inspection.get("method") != "SAMPLING":
                errors.append(f"{label} 只有抽样送检记录可以包含复检信息")
            reinspection_result = inspection.get("reinspectionResult")
            if (
                reinspection_result is not None
                and reinspection_result not in RESULTS
            ):
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

    for index, document in enumerate(manifest.get("documents", []), start=1):
        add_entity(document, f"documents[{index}]")
        if not isinstance(document, dict):
            continue
        for ref in document.get("productRefs", []):
            if ref not in entity_map:
                errors.append(f"文书引用了未知 productRef：{ref}")
        for ref in document.get("inspectionRefs", []):
            if ref not in entity_map:
                errors.append(f"文书引用了未知 inspectionRef：{ref}")
        for link in document.get("fileLinks", []):
            if not isinstance(link, dict) or link.get("fileRef") not in file_refs:
                errors.append(f"文书引用了未知 fileRef：{link}")
            elif link.get("relationRole") not in FILE_ROLES:
                errors.append(f"文书文件关系不合法：{link.get('relationRole')}")

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

    case_ref = str(case.get("clientRef", ""))
    case_type = case.get("caseType")
    evidence_items = manifest.get("fieldEvidence")
    evidence_items = evidence_items if isinstance(evidence_items, list) else []
    missing_items = manifest.get("missingItems")
    missing_items = missing_items if isinstance(missing_items, list) else []
    if case_type == "CRIMINAL" and not has_direct_criminal_evidence(
        evidence_items, case_ref
    ):
        errors.append("刑案必须具有含页码和直接刑事表述的 caseType 字段证据")
    if case_type == "ADMINISTRATIVE":
        if not failed_recheck_refs:
            errors.append("行案必须存在整改复查不合格检查记录")
        elif not has_administrative_rule_evidence(
            evidence_items, case_ref, failed_recheck_refs
        ):
            errors.append("行案必须具有引用整改复查不合格记录的 RULE 字段证据")
    if failed_recheck_refs and case_type not in {"ADMINISTRATIVE", "CRIMINAL"}:
        errors.append("存在整改复查不合格记录时，案卷类型必须为 ADMINISTRATIVE 或已证实的 CRIMINAL")
    if case_type == "UNKNOWN" and not has_case_type_missing_item(missing_items, case_ref):
        errors.append("UNKNOWN 案卷类型必须创建 caseType 待核对项")
    if case_type == "NONE":
        none_evidence = [
            item
            for item in evidence_items
            if is_case_type_evidence(item, case_ref, "NONE")
            and item.get("trustLevel") == "MANUAL"
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

    ocr_parser = subparsers.add_parser("ocr", help="只对无有效文本层的 PDF 页运行 Zerox")
    ocr_parser.add_argument("--work-dir", required=True)
    ocr_parser.add_argument("--zerox", default=str(DEFAULT_ZEROX))
    ocr_parser.add_argument("--poppler", default=str(DEFAULT_POPPLER))
    ocr_parser.add_argument("--concurrency", type=int, default=1, choices=range(1, 5))
    ocr_parser.add_argument("--timeout", type=int, default=600)
    ocr_parser.add_argument("--continue-on-error", action="store_true")
    ocr_parser.set_defaults(func=ocr_command)

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
