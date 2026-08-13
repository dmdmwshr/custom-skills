from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader, PdfWriter

VERSION = "1.1.0"
WRITE_HEADER, WRITE_HEADER_VALUE = "X-Product-Case-Client", "web-v2"
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_CONFIG = SKILL_ROOT / "admin-upload-config.toml"
# MinerU 包装脚本是无 BOM 的 UTF-8。Windows PowerShell 5.1 会按本机 ANSI
# 代码页误读其中的中文，因此必须使用能原生解析 UTF-8 的 PowerShell 7。
SYSTEM_POWERSHELL = Path(shutil.which("pwsh.exe") or "__missing_pwsh__")
MINERU_SCRIPT = Path(r"D:\Program_Files\MinerU-Docker\run-mineru-docker.ps1")
SCHEMA_PATH = SKILL_ROOT / "references" / "CaseImportManifestV2.schema.json"
PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
CLIENT_REF = re.compile(r"^[a-z][a-z0-9:_-]{0,159}$")
BRIGADES = {"JIANGYIN", "YIXING", "LIANGXI", "XISHAN", "HUISHAN", "BINHU", "XINWU", "JINGKAI"}
FORMS = {"ROUTINE", "SPECIAL", "COMPLAINT", "OTHER", "UNKNOWN"}
METHODS, RESULTS, TRI = (
    {"ONSITE", "SAMPLING"},
    {"QUALIFIED", "UNQUALIFIED", "PENDING", "UNKNOWN"},
    {"YES", "NO", "UNKNOWN"},
)
ACCESS = {"CCC", "TECHNICAL_APPRAISAL", "NOT_APPLICABLE", "UNKNOWN"}
VERSIONS = {"ELECTRONIC", "SCANNED"}

# Explicit production fixed-slot map: code -> (multiplicity, stage).
SLOT_META: dict[str, tuple[str, str | None]] = {
    "OTHER_ATTACHMENT": ("OTHER", None),
    "AUTHORIZATION_LETTER": ("CASE", None),
    "BUSINESS_LICENSE": ("CASE", None),
    "INITIAL_INSPECTION_RECORD": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_ONSITE_PHOTO": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_CCC_CERTIFICATE": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_TECHNICAL_APPRAISAL_CERTIFICATE": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_TYPE_TEST_REPORT": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_PURCHASE_RECORD_INVOICE": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_MAINTENANCE_RECORD": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_SAMPLING_FORM": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_SAMPLING_TEST_REPORT": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_QUALITY_RESULT_APPROVAL": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_QUALITY_RESULT_NOTICE": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_QUALITY_RESULT_RECEIPT": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_APPLICATION": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_ACCEPTANCE": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_REPORT": ("PRODUCT", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_QUALITY_RESULT_APPROVAL": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_QUALITY_RESULT_NOTICE": ("INSPECTION", "INITIAL_CHECK"),
    "INITIAL_REINSPECTION_QUALITY_RESULT_RECEIPT": ("INSPECTION", "INITIAL_CHECK"),
    "ONSITE_UNQUALIFIED_APPROVAL": ("INSPECTION", "INITIAL_CHECK"),
    "ONSITE_UNQUALIFIED_NOTICE": ("INSPECTION", "INITIAL_CHECK"),
    "ONSITE_UNQUALIFIED_RECEIPT": ("INSPECTION", "INITIAL_CHECK"),
    "RECTIFICATION_APPROVAL": ("INSPECTION", "INITIAL_CHECK"),
    "RECTIFICATION_ORDER": ("INSPECTION", "INITIAL_CHECK"),
    "RECTIFICATION_RECEIPT": ("INSPECTION", "INITIAL_CHECK"),
    "EVIDENCE_PRESERVATION_APPROVAL": ("INSPECTION", "INITIAL_CHECK"),
    "EVIDENCE_PRESERVATION_DECISION": ("INSPECTION", "INITIAL_CHECK"),
    "EVIDENCE_PRESERVATION_INVENTORY": ("INSPECTION", "INITIAL_CHECK"),
    "ILLEGAL_PRODUCT_NOTIFICATION_APPROVAL": ("NOTIFICATION_TARGET", "INITIAL_CHECK"),
    "ILLEGAL_PRODUCT_NOTIFICATION_LETTER": ("NOTIFICATION_TARGET", "INITIAL_CHECK"),
    "ILLEGAL_PRODUCT_NOTIFICATION_RECEIPT": ("NOTIFICATION_TARGET", "INITIAL_CHECK"),
    "RECHECK_INSPECTION_RECORD": ("INSPECTION", "RECHECK"),
    "RECHECK_ONSITE_PHOTO": ("INSPECTION", "RECHECK"),
    "RECHECK_CCC_CERTIFICATE": ("PRODUCT", "RECHECK"),
    "RECHECK_TECHNICAL_APPRAISAL_CERTIFICATE": ("PRODUCT", "RECHECK"),
    "RECHECK_TYPE_TEST_REPORT": ("PRODUCT", "RECHECK"),
    "RECHECK_PURCHASE_RECORD_INVOICE": ("PRODUCT", "RECHECK"),
    "RECHECK_MAINTENANCE_RECORD": ("PRODUCT", "RECHECK"),
    "RECHECK_SAMPLING_FORM": ("PRODUCT", "RECHECK"),
    "RECHECK_SAMPLING_TEST_REPORT": ("PRODUCT", "RECHECK"),
    "RECHECK_QUALITY_RESULT_APPROVAL": ("INSPECTION", "RECHECK"),
    "RECHECK_QUALITY_RESULT_NOTICE": ("INSPECTION", "RECHECK"),
    "RECHECK_QUALITY_RESULT_RECEIPT": ("INSPECTION", "RECHECK"),
    "RECHECK_REINSPECTION_APPLICATION": ("PRODUCT", "RECHECK"),
    "RECHECK_REINSPECTION_ACCEPTANCE": ("PRODUCT", "RECHECK"),
    "RECHECK_REINSPECTION_REPORT": ("PRODUCT", "RECHECK"),
    "RECHECK_REINSPECTION_QUALITY_RESULT_APPROVAL": ("INSPECTION", "RECHECK"),
    "RECHECK_REINSPECTION_QUALITY_RESULT_NOTICE": ("INSPECTION", "RECHECK"),
    "RECHECK_REINSPECTION_QUALITY_RESULT_RECEIPT": ("INSPECTION", "RECHECK"),
}
PHOTO_SLOTS = {"INITIAL_ONSITE_PHOTO", "RECHECK_ONSITE_PHOTO"}


class RegistryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RegistryError(f"文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise RegistryError(f"JSON 格式错误：{path}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"JSON 顶层必须是对象：{path}")
    return value


def read_auth_config(path: Path) -> tuple[str, str]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RegistryError(
            f"认证配置不存在：{path}；请复制 references/admin-upload-config.example.toml 后填写"
        ) from error
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise RegistryError(f"认证配置无法读取或 TOML 格式错误：{path}") from error
    auth = value.get("auth")
    if not isinstance(auth, dict):
        raise RegistryError("认证配置必须包含 [auth] 段")
    username, password = auth.get("username"), auth.get("password")
    if not isinstance(username, str) or not username.strip():
        raise RegistryError("认证配置中的 auth.username 不能为空")
    if not isinstance(password, str) or not password:
        raise RegistryError("认证配置中的 auth.password 不能为空")
    return username.strip(), password


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".." in parts
        or "\0" in normalized
    ):
        raise RegistryError(f"不安全的相对路径：{value}")
    return PurePosixPath(normalized).as_posix()


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def pdf_info(path: Path) -> tuple[str, int]:
    if not path.is_file() or not path.read_bytes()[:5].startswith(b"%PDF-"):
        raise RegistryError(f"不是 PDF：{path}")
    try:
        pages = len(PdfReader(str(path), strict=False).pages)
    except Exception as error:
        raise RegistryError(f"PDF 无法读取：{path}") from error
    if pages < 1:
        raise RegistryError(f"PDF 没有页面：{path}")
    return file_sha256(path), pages


def sniff_mime(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def directory_hash(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value["relativePath"]):
        digest.update(item["relativePath"].encode())
        digest.update(b"\0")
        digest.update(item["sha256"].encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def safe_extract_zip(archive: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise RegistryError("ZIP 解压目标非空")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            relative = safe_relative(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise RegistryError("ZIP 不允许符号链接")
            target = (destination / Path(*PurePosixPath(relative).parts)).resolve()
            if not inside(target, destination):
                raise RegistryError("ZIP 路径越界")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def inventory_command(args: argparse.Namespace) -> None:
    source, work = Path(args.input).resolve(), Path(args.work_dir).resolve()
    if source.is_file() and source.suffix.lower() == ".zip":
        root = work / "source"
        safe_extract_zip(source, root)
        package_sha, container = file_sha256(source), "ARCHIVE"
    elif source.is_dir():
        if inside(source, work) or inside(work, source):
            raise RegistryError("目录输入与 work-dir 不得重叠或互为父子目录")
        root, container, package_sha = source, "DIRECTORY", ""
    else:
        raise RegistryError("输入必须是目录或 ZIP")
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        mime = sniff_mime(path)
        record: dict[str, Any] = {
            "relativePath": path.relative_to(root).as_posix(),
            "absolutePath": str(path),
            "sha256": file_sha256(path),
            "sizeBytes": path.stat().st_size,
            "mimeType": mime,
        }
        if mime == "application/pdf":
            record["pageCount"] = pdf_info(path)[1]
        files.append(record)
    if container == "DIRECTORY":
        package_sha = directory_hash(files)
    write_json(
        work / "inventory.json",
        {
            "inventoryVersion": 3,
            "generatedAt": utc_now(),
            "sourceInput": str(source),
            "sourceRoot": str(root),
            "containerKind": container,
            "packageSha256": package_sha,
            "files": files,
        },
    )


def split_command(args: argparse.Namespace) -> None:
    work, plan = Path(args.work_dir).resolve(), read_json(Path(args.plan).resolve())
    inventory = read_json(work / "inventory.json")
    by_rel = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    planned: list[tuple[dict[str, Any], Path, int, int]] = []
    seen: set[Path] = set()
    normalized = (work / "normalized").resolve()
    if not isinstance(plan.get("items"), list):
        raise RegistryError("split-plan.items 必须是数组")
    for index, item in enumerate(plan["items"], 1):
        if not isinstance(item, dict):
            raise RegistryError("split-plan 每项必须是对象")
        source = by_rel.get(item.get("sourceRelativePath"))
        start, end = item.get("pageStart"), item.get("pageEnd")
        if not source or source.get("mimeType") != "application/pdf":
            raise RegistryError("拆分来源必须是 inventory 中的 PDF")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise RegistryError("页码范围不合法")
        source_path = Path(source["absolutePath"]).resolve()
        _sha, pages = pdf_info(source_path)
        if end > pages:
            raise RegistryError("页码超出来源 PDF")
        relative = safe_relative(str(item.get("relativePath") or f"normalized/{index:03d}.pdf"))
        target = (work / relative).resolve()
        if (
            not relative.startswith("normalized/")
            or target.suffix.lower() != ".pdf"
            or not inside(target, normalized)
        ):
            raise RegistryError("拆分输出只能位于 normalized/ 且必须为 PDF")
        if (
            target in seen
            or target.exists()
            or target == source_path
            or inside(source_path, normalized)
        ):
            raise RegistryError("拆分目标重复、已存在或与源重叠")
        seen.add(target)
        planned.append((item, target, start, end))
    result = []
    for item, target, start, end in planned:
        reader, writer = (
            PdfReader(str(by_rel[item["sourceRelativePath"]]["absolutePath"])),
            PdfWriter(),
        )
        for page in reader.pages[start - 1 : end]:
            writer.add_page(page)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            writer.write(stream)
        sha, pages = pdf_info(target)
        result.append(
            {
                **item,
                "relativePath": target.relative_to(work).as_posix(),
                "absolutePath": str(target),
                "sha256": sha,
                "mimeType": "application/pdf",
                "pageCount": pages,
            }
        )
    write_json(
        work / "split-index.json",
        {"splitIndexVersion": 4, "generatedAt": utc_now(), "items": result},
    )


def ocr_command(args: argparse.Namespace) -> None:
    work, inventory = (
        Path(args.work_dir).resolve(),
        read_json(Path(args.work_dir).resolve() / "inventory.json"),
    )
    output = Path(args.output_dir).resolve()
    if not inside(output, work):
        raise RegistryError("OCR 输出必须位于 work-dir 内")
    selected = args.relative_path or []
    if not selected:
        raise RegistryError("OCR 必须显式指定至少一个相对 PDF/图片路径")
    by_rel = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    mappings = []
    if not MINERU_SCRIPT.is_file() or not SYSTEM_POWERSHELL.is_file():
        raise RegistryError("MinerU 或 PowerShell 7 入口不存在")
    for relative in selected:
        safe = safe_relative(relative)
        source = by_rel.get(safe)
        if not source or source.get("mimeType") not in {
            "application/pdf",
            "image/png",
            "image/jpeg",
        }:
            raise RegistryError(f"OCR 来源必须是 inventory 中的 PDF/PNG/JPEG：{relative}")
        destination = (output / hashlib.sha256(safe.encode()).hexdigest()[:16]).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [
                    str(SYSTEM_POWERSHELL),
                    "-NoProfile",
                    "-File",
                    str(MINERU_SCRIPT),
                    "-Path",
                    source["absolutePath"],
                    "-Output",
                    str(destination),
                    "-NoBuild",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RegistryError(f"MinerU 超时：{relative}") from error
        if completed.returncode != 0:
            raise RegistryError(
                f"MinerU 处理失败：{relative}：{(completed.stderr or completed.stdout)[-1000:]}"
            )
        markdown_files = [
            path for path in destination.rglob("*.md") if path.is_file() and path.stat().st_size > 0
        ]
        if not markdown_files:
            raise RegistryError(f"MinerU 未生成非空 Markdown：{relative}")
        mappings.append(
            {
                "sourceRelativePath": safe,
                "sourceSha256": source["sha256"],
                "outputDir": str(destination.relative_to(work)),
                "stdout": completed.stdout[-1000:],
            }
        )
    write_json(
        work / "ocr-result.json",
        {"engine": "MinerU-Docker", "completedAt": utc_now(), "mappings": mappings},
    )


def only_keys(value: Any, allowed: set[str], path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return {}
    errors.extend(f"{path} 不允许未知字段 {key}" for key in value if key not in allowed)
    return value


def date_only(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        return datetime.fromisoformat(value).date().isoformat() == value
    except ValueError:
        return False


def text(value: Any) -> bool:
    return isinstance(value, str)


def ref(value: Any) -> bool:
    return isinstance(value, str) and bool(CLIENT_REF.fullmatch(value))


def validate_manifest(
    manifest: dict[str, Any], upload_map: dict[str, Any] | None = None
) -> list[str]:
    errors: list[str] = []
    try:
        schema = read_json(SCHEMA_PATH)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.absolute_path) or "manifest"
            errors.append(f"Schema {location}：{error.message}")
    except RegistryError as error:
        errors.append(f"无法读取生产 Schema：{error}")
    root = only_keys(
        manifest,
        {
            "schemaVersion",
            "packageSha256",
            "createdAt",
            "extractor",
            "case",
            "initialInspection",
            "recheckInspection",
            "files",
            "documentSlots",
            "otherAttachments",
        },
        "manifest",
        errors,
    )
    if root.get("schemaVersion") != "CaseImportManifestV2":
        errors.append("schemaVersion 必须为 CaseImportManifestV2")
    if not isinstance(root.get("packageSha256"), str) or not SHA256.fullmatch(
        root["packageSha256"]
    ):
        errors.append("packageSha256 不合法")
    try:
        datetime.fromisoformat(str(root.get("createdAt")).replace("Z", "+00:00"))
    except ValueError:
        errors.append("createdAt 必须为 ISO 时间")
    if "extractor" in root:
        extractor = only_keys(root["extractor"], {"name", "version"}, "extractor", errors)
        if (
            not text(extractor.get("name"))
            or not extractor["name"].strip()
            or not text(extractor.get("version"))
            or not extractor["version"].strip()
        ):
            errors.append("extractor.name/version 必须为非空文本")
    case = only_keys(
        root.get("case"),
        {
            "projectNo",
            "brigadeCode",
            "unitName",
            "unitAddress",
            "inspectionForm",
            "handler",
            "inspector",
            "legalRepresentative",
            "documentRecipient",
            "criminalCaseOverride",
            "notificationTarget",
        },
        "case",
        errors,
    )
    if not isinstance(case.get("projectNo"), str) or not PROJECT_NO.fullmatch(case["projectNo"]):
        errors.append("case.projectNo 不合法")
    if case.get("brigadeCode") not in BRIGADES:
        errors.append("case.brigadeCode 不合法")
    if not text(case.get("unitName")) or not case["unitName"].strip():
        errors.append("case.unitName 不能为空")
    for key in ("unitAddress", "handler", "inspector", "legalRepresentative", "documentRecipient"):
        if key in case and not text(case[key]):
            errors.append(f"case.{key} 必须为文本")
    if "inspectionForm" in case and case["inspectionForm"] not in FORMS:
        errors.append("case.inspectionForm 不合法")
    if "notificationTarget" in case and case["notificationTarget"] not in {
        "PRODUCTION",
        "SALES",
        "BOTH",
        "UNKNOWN",
    }:
        errors.append("case.notificationTarget 不合法")
    if "criminalCaseOverride" in case and not isinstance(case["criminalCaseOverride"], bool):
        errors.append("case.criminalCaseOverride 必须为布尔值")
    inspections: dict[str, str] = {}
    products: dict[str, tuple[str, dict[str, Any]]] = {}

    def inspect(value: Any, stage: str, label: str) -> None:
        item = only_keys(value, {"clientRef", "stage", "inspectionDate", "products"}, label, errors)
        item_ref = item.get("clientRef")
        if not ref(item_ref) or item_ref in inspections:
            errors.append(f"{label}.clientRef 重复或不合法")
        else:
            inspections[item_ref] = stage
        if item.get("stage") != stage:
            errors.append(f"{label}.stage 不匹配")
        if "inspectionDate" in item and not date_only(item["inspectionDate"]):
            errors.append(f"{label}.inspectionDate 不合法")
        if not isinstance(item.get("products"), list):
            errors.append(f"{label}.products 必须是数组")
            return
        for index, value in enumerate(item["products"]):
            product = only_keys(
                value,
                {
                    "clientRef",
                    "name",
                    "modelSpec",
                    "nominalProducer",
                    "location",
                    "method",
                    "result",
                    "onlineSale",
                    "maintenance",
                    "marketAccessMode",
                    "problemDescription",
                    "reinspectionApplied",
                    "reinspectionResult",
                },
                f"{label}.products[{index}]",
                errors,
            )
            product_ref = product.get("clientRef")
            if not ref(product_ref) or product_ref in products:
                errors.append("产品 clientRef 重复或不合法")
            else:
                products[product_ref] = (stage, product)
            for key in ("name", "modelSpec", "nominalProducer", "location", "problemDescription"):
                if key in product and not text(product[key]):
                    errors.append(f"产品 {key} 必须为文本")
            if not text(product.get("name")) or not product["name"].strip():
                errors.append("产品名称不能为空")
            if "method" in product and product["method"] not in METHODS:
                errors.append("产品 method 不合法")
            if "result" in product and product["result"] not in RESULTS:
                errors.append("产品 result 不合法")
            if "result" in product and "method" not in product:
                errors.append("产品 result 必须同时声明 method")
            for key in ("onlineSale", "maintenance", "reinspectionApplied"):
                if key in product and product[key] not in TRI:
                    errors.append(f"产品 {key} 不合法")
            if "marketAccessMode" in product and product["marketAccessMode"] not in ACCESS:
                errors.append("产品 marketAccessMode 不合法")
            if (
                product.get("result") == "UNQUALIFIED"
                and not str(product.get("problemDescription", "")).strip()
            ):
                errors.append("不合格产品必须填写问题描述")
            if product.get("method") == "ONSITE" and product.get("result") == "PENDING":
                errors.append("现场判定不能为 PENDING")
            if (
                any(key in product for key in ("reinspectionApplied", "reinspectionResult"))
                and product.get("method") != "SAMPLING"
            ):
                errors.append("复检字段只适用于抽样送检")
            if "reinspectionResult" in product and (
                product["reinspectionResult"] not in RESULTS
                or product.get("reinspectionApplied") != "YES"
            ):
                errors.append("reinspectionResult 不合法或未申请复检")

    inspect(root.get("initialInspection"), "INITIAL_CHECK", "initialInspection")
    if "recheckInspection" in root:
        inspect(root["recheckInspection"], "RECHECK", "recheckInspection")
    refs: set[str] = set()
    paths: set[str] = set()
    files = root.get("files") if isinstance(root.get("files"), list) else []
    if not isinstance(root.get("files"), list):
        errors.append("files 必须是数组")
    for item in files:
        file = only_keys(
            item, {"clientRef", "relativePath", "sha256", "mimeType", "pageCount"}, "file", errors
        )
        file_ref, path = file.get("clientRef"), file.get("relativePath")
        if not ref(file_ref) or file_ref in refs:
            errors.append("文件 clientRef 重复或不合法")
        else:
            refs.add(file_ref)
        try:
            path = safe_relative(path) if text(path) else ""
        except RegistryError:
            path = ""
        if not path or not path.endswith(".pdf") or path in paths:
            errors.append("文件 relativePath 必须唯一安全 PDF")
        else:
            paths.add(path)
        if not isinstance(file.get("sha256"), str) or not SHA256.fullmatch(file["sha256"]):
            errors.append("文件 sha256 不合法")
        if file.get("mimeType") != "application/pdf":
            errors.append("文件 mimeType 必须为 application/pdf")
        if "pageCount" in file and (
            not isinstance(file["pageCount"], int) or file["pageCount"] < 1
        ):
            errors.append("文件 pageCount 不合法")
        if upload_map is not None and isinstance(file_ref, str):
            local = upload_map.get(file_ref)
            try:
                sha, pages = pdf_info(Path(local)) if text(local) else ("", 0)
            except RegistryError:
                sha, pages = "", 0
            if sha != file.get("sha256") or ("pageCount" in file and pages != file["pageCount"]):
                errors.append(f"{file_ref} 本地 PDF/哈希/页数不匹配")
    used: set[str] = set()
    identities: set[str] = set()
    slot_refs: set[str] = set()
    slots = root.get("documentSlots") if isinstance(root.get("documentSlots"), list) else []
    if not isinstance(root.get("documentSlots"), list):
        errors.append("documentSlots 必须是数组")
    for slot in slots:
        slot = only_keys(
            slot,
            {
                "clientRef",
                "slotCode",
                "inspectionRef",
                "productRef",
                "notificationTarget",
                "documentNo",
                "documentDate",
                "versions",
            },
            "documentSlot",
            errors,
        )
        code, slot_ref = slot.get("slotCode"), slot.get("clientRef")
        if not ref(slot_ref) or slot_ref in slot_refs:
            errors.append("槽位 clientRef 重复或不合法")
        else:
            slot_refs.add(slot_ref)
        if code not in SLOT_META or code == "OTHER_ATTACHMENT":
            errors.append("slotCode 不在固定文书槽位中")
            continue
        multiplicity, stage = SLOT_META[code]
        inspection_ref, product_ref, target = (
            slot.get("inspectionRef"),
            slot.get("productRef"),
            slot.get("notificationTarget"),
        )
        if multiplicity == "CASE" and any(
            value is not None for value in (inspection_ref, product_ref, target)
        ):
            errors.append("案卷级槽位不得指定 owner")
        if multiplicity == "INSPECTION" and (
            inspections.get(inspection_ref) != stage
            or product_ref is not None
            or target is not None
        ):
            errors.append("检查级槽位 owner 不匹配")
        if multiplicity == "PRODUCT" and (
            product_ref not in products
            or products[product_ref][0] != stage
            or inspection_ref is not None
            or target is not None
        ):
            errors.append("产品级槽位必须只填写同阶段 productRef")
        if multiplicity == "NOTIFICATION_TARGET":
            permitted = (
                {"PRODUCTION", "SALES"}
                if case.get("notificationTarget") == "BOTH"
                else {case.get("notificationTarget")}
            )
            if (
                target not in {"PRODUCTION", "SALES"}
                or target not in permitted
                or inspection_ref is not None
                or product_ref is not None
            ):
                errors.append("通报槽位 target/owner 不匹配")
        if "documentNo" in slot and not text(slot["documentNo"]):
            errors.append("documentNo 必须为文本")
        if "documentDate" in slot and not date_only(slot["documentDate"]):
            errors.append("documentDate 不合法")
        identity = f"{code}:{target or product_ref or inspection_ref or 'case'}"
        if identity in identities:
            errors.append("同一逻辑槽位重复")
        identities.add(identity)
        versions = slot.get("versions")
        if not isinstance(versions, list) or not 1 <= len(versions) <= 2:
            errors.append("槽位 versions 必须有 1 至 2 项")
            continue
        kinds: set[str] = set()
        for version in versions:
            version = only_keys(version, {"kind", "fileRef"}, "version", errors)
            kind, file_ref = version.get("kind"), version.get("fileRef")
            if kind not in VERSIONS or kind in kinds:
                errors.append("文件版本重复或不合法")
            kinds.add(kind)
            if file_ref not in refs:
                errors.append("版本引用未知文件")
            elif file_ref in used:
                errors.append("同一 fileRef 不得复用")
            else:
                used.add(file_ref)
        if code in PHOTO_SLOTS and kinds != {"SCANNED"}:
            errors.append("现场照片仅允许一个 SCANNED 版本")
    attachments = (
        root.get("otherAttachments", [])
        if isinstance(root.get("otherAttachments", []), list)
        else []
    )
    if not isinstance(root.get("otherAttachments", []), list):
        errors.append("otherAttachments 必须是数组")
    for attachment in attachments:
        attachment = only_keys(
            attachment, {"clientRef", "slotCode", "title", "fileRef"}, "otherAttachment", errors
        )
        file_ref = attachment.get("fileRef")
        if not ref(attachment.get("clientRef")) or attachment.get("slotCode") != "OTHER_ATTACHMENT":
            errors.append("其他附件不合法")
        if "title" in attachment and not text(attachment["title"]):
            errors.append("其他附件 title 必须为文本")
        if file_ref not in refs:
            errors.append("其他附件引用未知文件")
        elif file_ref in used:
            errors.append("同一 fileRef 不得复用")
        else:
            used.add(file_ref)
    errors.extend(f"文件 {item} 未被引用" for item in refs - used)
    return errors


def compose_command(args: argparse.Namespace) -> None:
    work, data, inventory = (
        Path(args.work_dir).resolve(),
        read_json(Path(args.case_data).resolve()),
        read_json(Path(args.work_dir).resolve() / "inventory.json"),
    )
    inventory_hash = inventory.get("packageSha256")
    if not isinstance(inventory_hash, str) or not SHA256.fullmatch(inventory_hash):
        raise RegistryError("inventory.packageSha256 不合法")
    if "packageSha256" in data and data["packageSha256"] != inventory_hash:
        raise RegistryError("case-data.packageSha256 必须与 inventory 精确一致")
    sources = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    split_path = work / "split-index.json"
    if split_path.exists():
        sources.update(
            {
                item["relativePath"]: item
                for item in read_json(split_path).get("items", [])
                if isinstance(item, dict)
            }
        )
    manifest = {key: value for key, value in data.items() if key != "packageSha256"}
    manifest.update(
        {
            "schemaVersion": "CaseImportManifestV2",
            "packageSha256": inventory_hash,
            "createdAt": data.get("createdAt", utc_now()),
        }
    )
    upload: dict[str, str] = {}
    normalized_files = []
    source_refs: set[str] = set()
    if not isinstance(manifest.get("files"), list):
        raise RegistryError("case-data.files 必须是数组")
    for item in manifest["files"]:
        if (
            not isinstance(item, dict)
            or not text(item.get("sourceRelativePath"))
            or not text(item.get("relativePath"))
            or not ref(item.get("clientRef"))
        ):
            raise RegistryError(
                "每个 case-data.files 必须提供 clientRef/sourceRelativePath/relativePath"
            )
        source_rel, output_rel = (
            safe_relative(item["sourceRelativePath"]),
            safe_relative(item["relativePath"]),
        )
        if source_rel in source_refs:
            raise RegistryError(
                f"同一 sourceRelativePath 不得供多个 fileRef 复用：{source_rel}；"
                "请先生成独立规范 PDF"
            )
        source_refs.add(source_rel)
        source = sources.get(source_rel)
        if not source or source.get("mimeType") != "application/pdf":
            raise RegistryError(f"sourceRelativePath 必须指向 inventory/split 中 PDF：{source_rel}")
        sha, pages = pdf_info(Path(source["absolutePath"]))
        if sha != source.get("sha256") or pages != source.get("pageCount"):
            raise RegistryError(f"源 PDF 哈希或页数已变化：{source_rel}")
        normalized_files.append(
            {
                "clientRef": item["clientRef"],
                "relativePath": output_rel,
                "sha256": sha,
                "mimeType": "application/pdf",
                "pageCount": pages,
            }
        )
        upload[item["clientRef"]] = source["absolutePath"]
    manifest["files"] = normalized_files
    manifest.setdefault("documentSlots", [])
    manifest.setdefault("otherAttachments", [])
    errors = validate_manifest(manifest, upload)
    if errors:
        raise RegistryError("compose 校验失败：\n- " + "\n- ".join(errors))
    write_json(work / "manifest.json", manifest)
    write_json(work / "upload-map.json", {"files": upload})


def response_json(response: httpx.Response, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        # Never echo a response body here. Authentication and validation responses
        # may contain data that must not be copied into terminals, logs, or task output.
        raise RegistryError(f"{label} 失败：HTTP {response.status_code}")
    try:
        value = response.json()
    except ValueError as error:
        raise RegistryError(f"{label} 未返回 JSON") from error
    if not isinstance(value, dict):
        raise RegistryError(f"{label} 响应不是对象")
    return value


def origin_of(api_base: str) -> tuple[str, str]:
    parsed = urlsplit(api_base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise RegistryError("api-base 必须是无查询串的 HTTPS 地址")
    return api_base.rstrip("/"), f"{parsed.scheme}://{parsed.netloc}"


def authenticated_identity(user: dict[str, Any]) -> dict[str, str | None]:
    user_id, role = user.get("id"), user.get("role")
    brigade_id, brigade_code = user.get("brigadeId"), user.get("brigadeCode")
    if not isinstance(user_id, str) or role not in {"ADMIN", "BRIGADE"}:
        raise RegistryError("认证会话缺少有效的用户标识或角色")
    if brigade_id is not None and not isinstance(brigade_id, str):
        raise RegistryError("认证会话的 brigadeId 无效")
    if brigade_code is not None and not isinstance(brigade_code, str):
        raise RegistryError("认证会话的 brigadeCode 无效")
    return {
        "userId": user_id,
        "role": role,
        "brigadeId": brigade_id,
        "brigadeCode": brigade_code,
    }


def authenticate_client(
    client: httpx.Client,
    api_base: str,
    origin: str,
    manifest: dict[str, Any],
    auth_config: Path,
) -> dict[str, str | None]:
    username, password = read_auth_config(auth_config)
    login = response_json(
        client.post(
            f"{api_base}/api/auth/login",
            headers={"Origin": origin, WRITE_HEADER: WRITE_HEADER_VALUE},
            json={"username": username, "password": password},
        ),
        "登录",
    )
    session = response_json(client.get(f"{api_base}/api/auth/session"), "读取登录会话")
    login_user, session_user = login.get("user"), session.get("user")
    if not isinstance(login_user, dict) or not isinstance(session_user, dict):
        raise RegistryError("认证响应缺少用户信息")
    identity = authenticated_identity(session_user)
    if authenticated_identity(login_user) != identity:
        raise RegistryError("登录响应与会话身份不一致")
    login_csrf, csrf_token = login.get("csrfToken"), session.get("csrfToken")
    if not isinstance(csrf_token, str) or not csrf_token or login_csrf != csrf_token:
        raise RegistryError("登录响应与会话的 CSRF 防护令牌不一致")
    if (
        login_user.get("mustChangePassword") is True
        or session_user.get("mustChangePassword") is True
    ):
        raise RegistryError("当前账户首次登录必须先在网站修改密码；已停止上传")
    brigade_code = manifest["case"]["brigadeCode"]
    if identity["role"] == "BRIGADE" and (
        not identity["brigadeId"] or identity["brigadeCode"] != brigade_code
    ):
        raise RegistryError("当前大队账户与 manifest 的 brigadeCode 不一致")
    client.headers.update(
        {
            "Origin": origin,
            WRITE_HEADER: WRITE_HEADER_VALUE,
            "X-CSRF-Token": csrf_token,
        }
    )
    return identity


def require_same_state_identity(state: dict[str, Any], identity: dict[str, str | None]) -> None:
    if not state:
        return
    stored = state.get("authIdentity")
    if not isinstance(stored, dict):
        raise RegistryError(
            "现有 upload-state 未绑定认证身份；不得用新版命令续传，请改用 verify 只读核验"
        )
    if stored != identity:
        raise RegistryError("当前登录身份与 upload-state 绑定身份不一致；禁止切换身份续传")


def exact_case(client: httpx.Client, api_base: str, project_no: str) -> dict[str, Any] | None:
    data = response_json(
        client.get(
            f"{api_base}/api/v2/cases", params={"search": project_no, "page": 1, "pageSize": 100}
        ),
        "精确案卷查询",
    )
    matches = [
        item
        for item in data.get("data", [])
        if isinstance(item, dict) and item.get("projectNo") == project_no
    ]
    if len(matches) > 1:
        raise RegistryError("项目编号匹配多个案卷")
    return matches[0] if matches else None


def expected_products(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        product["clientRef"]: {**product, "_stage": inspection["stage"]}
        for inspection in (manifest["initialInspection"], manifest.get("recheckInspection"))
        if isinstance(inspection, dict)
        for product in inspection.get("products", [])
    }


def verify_with_client(
    client: httpx.Client, api_base: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    listed = exact_case(client, api_base, manifest["case"]["projectNo"])
    if not listed:
        raise RegistryError("目标案卷不存在")
    case_id = listed.get("id")
    if not isinstance(case_id, str):
        raise RegistryError("目标案卷缺少 id")
    detail = response_json(client.get(f"{api_base}/api/v2/cases/{case_id}"), "读取详情")
    directory = response_json(
        client.get(f"{api_base}/api/v2/cases/{case_id}/directory"), "读取目录"
    )
    case_map = {
        "unitName": "unitName",
        "unitAddress": "unitAddress",
        "handler": "caseHandler",
        "inspector": "inspector",
        "legalRepresentative": "legalRepresentative",
        "documentRecipient": "documentRecipient",
        "inspectionForm": "inspectionForm",
        "notificationTarget": "notificationTarget",
    }
    brigade = detail.get("brigade")
    if (
        detail.get("projectNo") != manifest["case"]["projectNo"]
        or not isinstance(brigade, dict)
        or brigade.get("code") != manifest["case"]["brigadeCode"]
    ):
        raise RegistryError("案卷关键字段不一致")
    for local, remote in case_map.items():
        if local in manifest["case"] and detail.get(remote) != manifest["case"][local]:
            raise RegistryError(f"案卷字段不一致：{local}")
    expected_inspections = [manifest["initialInspection"]] + (
        [manifest["recheckInspection"]] if "recheckInspection" in manifest else []
    )
    actual_rows = [item for item in detail.get("inspections", []) if isinstance(item, dict)]
    actual_inspections = {item.get("stage"): item for item in actual_rows}
    if (
        len(actual_rows) != len(expected_inspections)
        or len(actual_inspections) != len(actual_rows)
        or set(actual_inspections) != {item["stage"] for item in expected_inspections}
    ):
        raise RegistryError("实际检查数量或阶段不一致")
    remote_product_by_ref: dict[str, dict[str, Any]] = {}
    for expected in expected_inspections:
        actual = actual_inspections[expected["stage"]]
        if (
            "inspectionDate" in expected
            and str(actual.get("inspectionDate") or "")[:10] != expected["inspectionDate"]
        ):
            raise RegistryError("检查日期不一致")
        candidates = actual.get("products", [])
        if not isinstance(candidates, list) or len(candidates) != len(expected["products"]):
            raise RegistryError("实际产品数量不一致")
        used: set[str] = set()
        for product in expected["products"]:
            matches = [
                item
                for item in candidates
                if isinstance(item, dict)
                and item.get("id") not in used
                and item.get("name") == product["name"]
                and item.get("modelSpec") == product.get("modelSpec")
            ]
            if len(matches) != 1:
                raise RegistryError("产品匹配不唯一或缺失")
            remote = matches[0]
            used.add(remote["id"])
            for field in (
                "nominalProducer",
                "location",
                "method",
                "result",
                "onlineSale",
                "maintenance",
                "marketAccessMode",
                "problemDescription",
                "reinspectionApplied",
                "reinspectionResult",
            ):
                if field in product and remote.get(field) != product[field]:
                    raise RegistryError(f"产品字段不一致：{field}")
            remote_product_by_ref[product["clientRef"]] = remote
    source_files = {item["clientRef"]: item for item in manifest["files"]}
    rows = directory.get("rows", [])

    def check_file(file_ref: str, remote: dict[str, Any], require_directory_sha: bool) -> None:
        expected = source_files[file_ref]
        if require_directory_sha and remote.get("sha256") != expected["sha256"]:
            raise RegistryError(f"目录 SHA-256 不一致：{file_ref}")
        remote_id = remote.get("id")
        if not isinstance(remote_id, str):
            raise RegistryError(f"目录缺少文件标识：{file_ref}")
        response = client.get(f"{api_base}/api/v2/files/{remote_id}")
        if (
            response.status_code != 200
            or "sha256:" + hashlib.sha256(response.content).hexdigest() != expected["sha256"]
        ):
            raise RegistryError(f"下载 SHA-256 不一致：{file_ref}")

    for slot in manifest.get("documentSlots", []):
        code, (multiplicity, stage) = slot["slotCode"], SLOT_META[slot["slotCode"]]
        row_matches = [row for row in rows if isinstance(row, dict) and row.get("slotKey") == code]
        if len(row_matches) != 1:
            raise RegistryError(f"目录槽位不唯一或缺失：{code}")
        children = row_matches[0].get("children", [])
        if not isinstance(children, list):
            raise RegistryError(f"目录 children 不是数组：{code}")
        if multiplicity == "CASE":
            candidates = [
                child
                for child in children
                if isinstance(child, dict) and isinstance(child.get("versions"), dict)
            ]
            if not candidates and isinstance(row_matches[0].get("versions"), dict):
                candidates = [row_matches[0]]
        elif multiplicity == "INSPECTION":
            candidates = [
                child
                for child in children
                if isinstance(child, dict)
                and child.get("inspectionId") == actual_inspections[stage].get("id")
            ]
        elif multiplicity == "PRODUCT":
            candidates = [
                child
                for child in children
                if isinstance(child, dict)
                and child.get("productId") == remote_product_by_ref[slot["productRef"]].get("id")
            ]
        else:
            candidates = [
                child
                for child in children
                if isinstance(child, dict)
                and (
                    child.get("notificationTarget") == slot["notificationTarget"]
                    or child.get("key") == slot["notificationTarget"]
                )
            ]
        if len(candidates) != 1:
            raise RegistryError(f"目录 owner 不唯一或缺失：{code}")
        owner = candidates[0]
        if not isinstance(owner.get("versions"), dict):
            raise RegistryError(f"目录 owner 缺失：{code}")
        expected_versions = {version["kind"] for version in slot["versions"]}
        if set(owner["versions"]) != expected_versions:
            raise RegistryError(f"目录版本种类不一致：{code}")
        for version in slot["versions"]:
            remote = owner["versions"].get(version["kind"])
            if not isinstance(remote, dict):
                raise RegistryError(f"目录版本缺失：{code}/{version['kind']}")
            check_file(version["fileRef"], remote, True)
    for attachment in manifest.get("otherAttachments", []):
        rows_match = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("slotKey") == "OTHER_ATTACHMENT"
        ]
        if len(rows_match) != 1:
            raise RegistryError("其他附件目录缺失")
        children = [
            child
            for child in rows_match[0].get("children", [])
            if child.get("title") == attachment.get("title")
        ]
        if (
            len(children) != 1
            or not isinstance(children[0].get("files"), list)
            or len(children[0]["files"]) != 1
        ):
            raise RegistryError("其他附件匹配不唯一或缺失")
        check_file(attachment["fileRef"], children[0]["files"][0], False)
    return {
        "caseId": case_id,
        "inspections": len(actual_inspections),
        "products": len(remote_product_by_ref),
        "filesVerified": len(source_files),
    }


def load_inputs(manifest_path: str, map_path: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path, manifest, upload = (
        Path(manifest_path).resolve(),
        read_json(Path(manifest_path).resolve()),
        read_json(Path(map_path).resolve()).get("files"),
    )
    if not isinstance(upload, dict):
        raise RegistryError("upload-map.files 必须是对象")
    errors = validate_manifest(manifest, upload)
    if errors:
        raise RegistryError("本地校验失败：\n- " + "\n- ".join(errors))
    return path, manifest, upload


def verify_command(args: argparse.Namespace) -> None:
    _path, manifest, _upload = load_inputs(args.manifest, args.upload_map)
    api_base, origin = origin_of(args.api_base)
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)), follow_redirects=False
    ) as client:
        authenticate_client(
            client,
            api_base,
            origin,
            manifest,
            Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG)).resolve(),
        )
        print(json.dumps(verify_with_client(client, api_base, manifest), ensure_ascii=False))


def upload_command(args: argparse.Namespace) -> None:
    path, manifest, upload = load_inputs(args.manifest, args.upload_map)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry-run-ok",
                    "network": False,
                    "writes": ["create", "files", "manifest", "finalize"] if args.finalize else [],
                },
                ensure_ascii=False,
            )
        )
        return
    if not args.finalize:
        raise RegistryError("正式写入必须显式指定 --finalize")
    api_base, origin = origin_of(args.api_base)
    manifest_sha, package_sha = file_sha256(path), manifest["packageSha256"]
    state_path = path.parent / "upload-state.json"
    state = read_json(state_path) if state_path.exists() else {}
    if state and (
        state.get("manifestSha256") != manifest_sha
        or state.get("packageSha256") != package_sha
        or state.get("origin") != origin
    ):
        raise RegistryError("upload-state 与当前清单或 origin 不匹配")
    headers = {WRITE_HEADER: WRITE_HEADER_VALUE, "Origin": origin}
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
        headers=headers,
        follow_redirects=False,
    ) as client:
        identity = authenticate_client(
            client,
            api_base,
            origin,
            manifest,
            Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG)).resolve(),
        )
        require_same_state_identity(state, identity)
        if state.get("status") in {"FINALIZED_UNVERIFIED", "VERIFIED"}:
            verification = verify_with_client(client, api_base, manifest)
            state.update(
                {"status": "VERIFIED", "verification": verification, "verifiedAt": utc_now()}
            )
            write_json(state_path, state)
            print(
                json.dumps(
                    {"idempotentReplay": True, "verification": verification}, ensure_ascii=False
                )
            )
            return
        if response_json(client.get(f"{api_base}/api/ready"), "服务就绪").get("status") != "ready":
            raise RegistryError("服务未就绪")
        existing = exact_case(client, api_base, manifest["case"]["projectNo"])
        if existing and state.get("status") == "UPLOADING" and isinstance(state.get("jobId"), str):
            # A committed finalize response may be lost.  Verification is the
            # only safe recovery: never retry a write in this ambiguous state.
            verification = verify_with_client(client, api_base, manifest)
            state.update(
                {"status": "VERIFIED", "verification": verification, "verifiedAt": utc_now()}
            )
            write_json(state_path, state)
            print(
                json.dumps(
                    {"recoveredAfterLostFinalizeResponse": True, "verification": verification},
                    ensure_ascii=False,
                )
            )
            return
        if existing:
            raise RegistryError("目标项目编号已存在；为防覆盖人工数据已停止")
        previous_job = state.get("jobId")
        job = response_json(
            client.post(
                f"{api_base}/api/v2/import-jobs",
                headers={"Idempotency-Key": f"xfpcr-v2-{package_sha[7:]}"},
                json={"packageSha256": package_sha},
            ),
            "创建导入任务",
        )
        job_id = job.get("id")
        if not isinstance(job_id, str):
            raise RegistryError("导入任务响应缺少 id")
        uploaded = set(state.get("uploadedFileRefs", [])) if previous_job == job_id else set()
        state = {
            "stateVersion": 5,
            "status": "UPLOADING",
            "origin": origin,
            "manifestSha256": manifest_sha,
            "packageSha256": package_sha,
            "projectNo": manifest["case"]["projectNo"],
            "jobId": job_id,
            "authIdentity": identity,
            "uploadedFileRefs": sorted(uploaded),
        }
        write_json(state_path, state)
        for item in manifest["files"]:
            if item["clientRef"] in uploaded:
                continue
            with Path(upload[item["clientRef"]]).open("rb") as stream:
                response_json(
                    client.post(
                        f"{api_base}/api/v2/import-jobs/{job_id}/files",
                        params={"relativePath": item["relativePath"]},
                        files={"file": (Path(stream.name).name, stream, "application/pdf")},
                    ),
                    "上传 PDF",
                )
            uploaded.add(item["clientRef"])
            state["uploadedFileRefs"] = sorted(uploaded)
            write_json(state_path, state)
        response_json(
            client.put(f"{api_base}/api/v2/import-jobs/{job_id}/manifest", json=manifest),
            "提交清单",
        )
        result = response_json(
            client.post(f"{api_base}/api/v2/import-jobs/{job_id}/finalize"), "完成导入"
        )
        if not isinstance(result.get("caseId"), str):
            raise RegistryError("finalize 响应缺少 caseId")
        state.update(
            {
                "status": "FINALIZED_UNVERIFIED",
                "finalizedAt": utc_now(),
                "finalizeResult": result,
                "caseId": result["caseId"],
            }
        )
        write_json(state_path, state)
        verification = verify_with_client(client, api_base, manifest)
        state.update({"status": "VERIFIED", "verification": verification, "verifiedAt": utc_now()})
        write_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def validate_command(args: argparse.Namespace) -> None:
    _path, _manifest, _upload = (
        load_inputs(args.manifest, args.upload_map)
        if args.upload_map
        else (Path(args.manifest), read_json(Path(args.manifest)), {})
    )
    if not args.upload_map:
        errors = validate_manifest(_manifest)
        if errors:
            raise RegistryError("\n- ".join(errors))
    print(json.dumps({"status": "valid"}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="消防产品案卷 CaseImportManifestV2 工具")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("input")
    inventory.add_argument("--work-dir", required=True)
    inventory.set_defaults(func=inventory_command)
    ocr = sub.add_parser("ocr")
    ocr.add_argument("--work-dir", required=True)
    ocr.add_argument("--output-dir", required=True)
    ocr.add_argument("--relative-path", action="append")
    ocr.add_argument("--timeout", type=int, default=3600)
    ocr.set_defaults(func=ocr_command)
    split = sub.add_parser("split")
    split.add_argument("--work-dir", required=True)
    split.add_argument("--plan", required=True)
    split.set_defaults(func=split_command)
    compose = sub.add_parser("compose")
    compose.add_argument("--work-dir", required=True)
    compose.add_argument("--case-data", required=True)
    compose.set_defaults(func=compose_command)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--upload-map")
    validate.set_defaults(func=validate_command)
    for name, func in (("upload", upload_command), ("verify", verify_command)):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--upload-map", required=True)
        command.add_argument("--api-base", required=True)
        command.add_argument(
            "--auth-config",
            default=str(DEFAULT_AUTH_CONFIG),
            help="本地 TOML 认证配置；默认使用 skill 根目录的 admin-upload-config.toml",
        )
        command.add_argument("--timeout", type=float, default=60.0)
        command.set_defaults(func=func)
        if name == "upload":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--finalize", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RegistryError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except httpx.TransportError:
        print("ERROR: 网络传输失败，请检查连接后重试", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
