from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader, PdfWriter

VERSION = "1.4.1"
WRITE_HEADER, WRITE_HEADER_VALUE = "X-Product-Case-Client", "web-v2"
SKILL_ROOT = Path(__file__).resolve().parents[1]
SESSION_COOKIE_NAME = "__Host-product_case_session"
AUTH_CONFIG_TEMPLATE = '[auth]\nusername = ""\npassword = ""\n'


class RegistryError(RuntimeError):
    pass


def default_auth_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    if not root.is_absolute():
        raise RegistryError("LOCALAPPDATA 必须是绝对路径")
    return Path(os.path.abspath(root)) / "xf-product-case-registry" / "admin-upload-config.toml"


DEFAULT_AUTH_CONFIG = default_auth_config_path()
# MinerU 包装脚本是无 BOM 的 UTF-8。Windows PowerShell 5.1 会按本机 ANSI
# 代码页误读其中的中文，因此必须使用能原生解析 UTF-8 的 PowerShell 7。
SYSTEM_POWERSHELL = Path(shutil.which("pwsh.exe") or "__missing_pwsh__")
MINERU_SCRIPT = Path(r"D:\Program_Files\MinerU-Docker\run-mineru-docker.ps1")
SCHEMA_PATH = SKILL_ROOT / "references" / "CaseImportManifestV2.schema.json"
PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
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
RECALL_STATUSES = {"READY", "PENDING", "PROCESSING", "OFFLINE", "FAILED"}
RECALL_POLL_INTERVAL_SECONDS = 2.0
RECALL_MAX_POLLS = 30
MAX_DEEP_VERIFY_BYTES = 100 * 1024 * 1024
MAX_ERROR_RESPONSE_BYTES = 16 * 1024

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


def secure_auth_config_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise RegistryError("认证配置路径必须是绝对路径")
    absolute = Path(os.path.abspath(path))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for candidate in (absolute, *absolute.parents):
        if not (candidate.exists() or candidate.is_symlink()):
            continue
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise RegistryError(f"无法检查认证配置路径：{candidate}") from error
        attributes = getattr(metadata, "st_file_attributes", 0)
        if candidate.is_symlink() or attributes & reparse_flag:
            raise RegistryError(f"认证配置路径不允许符号链接、联接或其他重解析点：{candidate}")
    return absolute


def read_auth_config(path: Path) -> tuple[str, str]:
    path = secure_auth_config_path(path)
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RegistryError(
            f"认证配置不存在：{path}；请把 references/admin-upload-config.example.toml "
            "复制到默认本地配置路径后填写"
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


def restrict_auth_config_acl(path: Path) -> None:
    if os.name != "nt":
        raise RegistryError("认证配置 ACL 收紧仅支持 Windows")
    try:
        identity = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
        ).stdout.strip()
        match = re.search(rb'"(S-1-[0-9-]+)"\s*$', identity)
        if not match:
            raise ValueError("current SID missing")
        current_sid = match.group(1).decode("ascii")
        subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{current_sid}:(R,W)",
                "*S-1-5-18:(F)",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        raise RegistryError("无法把认证配置 ACL 收紧为当前用户和 SYSTEM") from error


def init_auth_config_command(args: argparse.Namespace) -> None:
    path = secure_auth_config_path(Path(args.auth_config))
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_auth_config_path(path)
    created = False
    if not path.exists():
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(AUTH_CONFIG_TEMPLATE)
            created = True
        except OSError as error:
            raise RegistryError(f"无法创建认证配置：{path}") from error
    elif not path.is_file():
        raise RegistryError("认证配置目标必须是普通文件")
    try:
        restrict_auth_config_acl(path)
    except RegistryError:
        if created:
            path.unlink(missing_ok=True)
        raise
    print(
        json.dumps(
            {"status": "created" if created else "secured", "authConfig": str(path)},
            ensure_ascii=False,
        )
    )


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
        or "." in parts
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
    if destination.exists():
        destination.rmdir()
    intake = source_intake_api()
    try:
        intake.safe_extract_package(archive, destination)
    except intake.SourceIntakeError as error:
        raise RegistryError(str(error)) from error


def inventory_command(args: argparse.Namespace) -> None:
    source, work = Path(args.input).resolve(), Path(args.work_dir).resolve()
    enforce_local_workspace_preflight(args, work, source_path=source)
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
    update_local_case_waterline(
        args,
        work,
        state="PENDING_ORGANIZATION",
        local_status="INVENTORIED",
    )


def split_command(args: argparse.Namespace) -> None:
    work, plan = Path(args.work_dir).resolve(), read_json(Path(args.plan).resolve())
    enforce_local_workspace_preflight(args, work)
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
    update_local_case_waterline(
        args,
        work,
        state="ORGANIZING",
        local_status="ORGANIZING",
    )


def ocr_command(args: argparse.Namespace) -> None:
    work, inventory = (
        Path(args.work_dir).resolve(),
        read_json(Path(args.work_dir).resolve() / "inventory.json"),
    )
    enforce_local_workspace_preflight(args, work)
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
    update_local_case_waterline(
        args,
        work,
        state="ORGANIZING",
        local_status="ORGANIZING",
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
    product_identities: set[tuple[str, str, str]] = set()

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
                    "repairSiteId",
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
            product_identity = (
                stage,
                str(product.get("name", "")).strip(),
                str(product.get("modelSpec", "")).strip(),
            )
            if product_identity in product_identities:
                errors.append("同一检查中产品名称和型号重复，远端核验会产生歧义")
            else:
                product_identities.add(product_identity)
            field_limits = {
                "name": 300,
                "modelSpec": 300,
                "nominalProducer": 300,
                "location": 300,
                "problemDescription": 2000,
            }
            for key, limit in field_limits.items():
                if key not in product:
                    continue
                value = product[key]
                if not isinstance(value, str) or value != value.strip() or not value:
                    errors.append(f"产品 {key} 必须是已 trim 且非空的文本")
                elif len(value) > limit:
                    errors.append(f"产品 {key} 长度不得超过 {limit} 个字符")
            if not isinstance(product.get("name"), str) or not product["name"].strip():
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
            if "repairSiteId" in product:
                repair_site_id = product["repairSiteId"]
                if repair_site_id is not None and (
                    not isinstance(repair_site_id, str)
                    or not UUID.fullmatch(repair_site_id)
                    or repair_site_id != repair_site_id.lower()
                ):
                    errors.append("产品 repairSiteId 必须为规范小写 UUID 或 null")
                if repair_site_id is not None and product.get("maintenance") != "YES":
                    errors.append("填写 repairSiteId 时 maintenance 必须为 YES")
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
            if text(path) and ("\\" in path or "//" in path):
                raise RegistryError("路径包含不允许的分隔符")
            path = safe_relative(path) if text(path) else ""
        except RegistryError:
            path = ""
        if not path or not path.lower().endswith(".pdf") or path in paths:
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
    attachment_refs: set[str] = set()
    attachment_titles: set[str] = set()
    missing_attachment_titles = 0
    for attachment in attachments:
        attachment = only_keys(
            attachment, {"clientRef", "slotCode", "title", "fileRef"}, "otherAttachment", errors
        )
        file_ref = attachment.get("fileRef")
        attachment_ref = attachment.get("clientRef")
        if (
            not ref(attachment_ref)
            or attachment_ref in attachment_refs
            or attachment.get("slotCode") != "OTHER_ATTACHMENT"
        ):
            errors.append("其他附件不合法")
        else:
            attachment_refs.add(attachment_ref)
        title = attachment.get("title")
        normalized_title = title.strip() if isinstance(title, str) else None
        if "title" in attachment and (
            not isinstance(title, str) or title != normalized_title or not 1 <= len(title) <= 300
        ):
            errors.append("其他附件 title 必须是已 trim 且长度为 1 至 300 的文本")
        if not normalized_title:
            missing_attachment_titles += 1
        elif normalized_title in attachment_titles:
            errors.append("其他附件 title 不能重复，避免核验歧义")
        else:
            attachment_titles.add(normalized_title)
        if file_ref not in refs:
            errors.append("其他附件引用未知文件")
        elif file_ref in used:
            errors.append("同一 fileRef 不得复用")
        else:
            used.add(file_ref)
    if missing_attachment_titles > 1:
        errors.append("多个无 title 的其他附件会导致核验歧义")
    errors.extend(f"文件 {item} 未被引用" for item in refs - used)
    return errors


def compose_command(args: argparse.Namespace) -> None:
    work, data, inventory = (
        Path(args.work_dir).resolve(),
        read_json(Path(args.case_data).resolve()),
        read_json(Path(args.work_dir).resolve() / "inventory.json"),
    )
    project_no = (
        data.get("case", {}).get("projectNo") if isinstance(data.get("case"), dict) else None
    )
    enforce_local_workspace_preflight(args, work, project_no)
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
    update_local_case_waterline(
        args,
        work,
        state="PENDING_UPLOAD",
        local_status="READY_FOR_UPLOAD",
        project_no=manifest["case"]["projectNo"],
    )


def response_json(response: httpx.Response, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        # Never echo a response body here. Authentication and validation responses
        # may contain data that must not be copied into terminals, logs, or task output.
        message = safe_error_message(response)
        auth_error = label.startswith("登录") or label.startswith("读取登录会话")
        suffix = f"：{message}" if message and not auth_error else ""
        raise RegistryError(f"{label} 失败：HTTP {response.status_code}{suffix}")
    try:
        value = response.json()
    except ValueError as error:
        raise RegistryError(f"{label} 未返回 JSON") from error
    if not isinstance(value, dict):
        raise RegistryError(f"{label} 响应不是对象")
    return value


def safe_error_message(response: httpx.Response) -> str | None:
    """Allow only short, non-sensitive validation hints from JSON errors."""
    value = limited_json_value(response)
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "statusCode",
        "message",
        "error",
        "code",
        "errors",
    }:
        return None
    raw: Any = value.get("message", value.get("errors"))
    parts = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    if not parts or len(parts) > 8 or any(not isinstance(item, str) for item in parts):
        return None
    cleaned: list[str] = []
    sensitive = re.compile(
        r"(?:password|passwd|cookie|csrf|token|secret|stack|trace|internal|session|bearer|[A-Za-z]:\\|\\\\|/(?:[^\s；，。]+)|https?://|\b(?:select|insert|update|delete|drop|alter|from|where)\b|\b(?:id|jobid|userid|fileid|caseid)\s*[:=]|[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
        re.IGNORECASE,
    )
    for item in parts:
        if (
            not 1 <= len(item) <= 240
            or any(ord(char) < 32 for char in item)
            or sensitive.search(item)
        ):
            return None
        cleaned.append(item)
    return "；".join(cleaned)


def limited_json_value(response: httpx.Response) -> Any | None:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return None
    body = bytearray()
    try:
        for chunk in response.iter_bytes(4096):
            body.extend(chunk)
            if len(body) > MAX_ERROR_RESPONSE_BYTES:
                return None
        return json.loads(bytes(body))
    except (ValueError, UnicodeError, httpx.HTTPError, httpx.ResponseNotRead):
        return None


def origin_of(api_base: str) -> tuple[str, str]:
    parsed = urlsplit(api_base)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RegistryError("api-base 必须是无用户信息、路径、查询串的 HTTPS 地址")
    return api_base.rstrip("/"), f"{parsed.scheme}://{parsed.netloc}"


def authenticated_identity(user: dict[str, Any], top_level_csrf: Any) -> dict[str, Any]:
    user_id, username, display_name = user.get("id"), user.get("username"), user.get("displayName")
    role, version = user.get("role"), user.get("version")
    brigade_id, brigade_code = user.get("brigadeId"), user.get("brigadeCode")
    if (
        not isinstance(user_id, str)
        or not isinstance(username, str)
        or not isinstance(display_name, str)
        or role not in {"ADMIN", "BRIGADE"}
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise RegistryError("认证会话缺少有效的用户身份字段")
    if user.get("authMethod") != "SESSION":
        raise RegistryError("认证会话的 authMethod 必须是 SESSION")
    if user.get("mustChangePassword") is not False:
        raise RegistryError("mustChangePassword 必须显式为 false；首次登录请先在网站修改密码")
    if (
        not isinstance(top_level_csrf, str)
        or not top_level_csrf
        or user.get("csrfToken") != top_level_csrf
    ):
        raise RegistryError("用户信息与响应顶层的 CSRF 防护令牌不一致")
    brigade = user.get("brigade")
    if role == "ADMIN":
        if brigade_id is not None or brigade_code is not None or brigade is not None:
            raise RegistryError("ADMIN 账户不得绑定大队")
    else:
        if (
            not isinstance(brigade_id, str)
            or not isinstance(brigade_code, str)
            or not isinstance(brigade, dict)
            or brigade.get("id") != brigade_id
            or brigade.get("code") != brigade_code
            or not isinstance(brigade.get("name"), str)
            or not isinstance(brigade.get("routePath"), str)
        ):
            raise RegistryError("BRIGADE 账户的平铺与嵌套大队绑定不一致")
    return {
        "userId": user_id,
        "username": username,
        "displayName": display_name,
        "role": role,
        "brigadeId": brigade_id,
        "brigadeCode": brigade_code,
        "brigadeName": brigade.get("name") if isinstance(brigade, dict) else None,
        "brigadeRoutePath": brigade.get("routePath") if isinstance(brigade, dict) else None,
        "version": version,
    }


def state_identity(identity: dict[str, Any]) -> dict[str, str | None]:
    stable_identity = {key: identity[key] for key in ("userId", "role", "brigadeId", "brigadeCode")}
    canonical = json.dumps(
        stable_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "digest": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "role": identity["role"],
        "brigadeCode": identity["brigadeCode"],
    }


def authenticate_client(
    client: httpx.Client,
    api_base: str,
    origin: str,
    manifest: dict[str, Any],
    auth_config: Path,
) -> tuple[dict[str, str | None], dict[str, str]]:
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
    login_csrf, csrf_token = login.get("csrfToken"), session.get("csrfToken")
    for label, payload in (("登录", login), ("会话", session)):
        if not isinstance(payload.get("expiresAt"), str) or not isinstance(
            payload.get("absoluteExpiresAt"), str
        ):
            raise RegistryError(f"{label}响应缺少会话到期时间")
    if not isinstance(csrf_token, str) or not csrf_token or login_csrf != csrf_token:
        raise RegistryError("登录响应与会话的 CSRF 防护令牌不一致")
    login_identity = authenticated_identity(login_user, login_csrf)
    session_identity = authenticated_identity(session_user, csrf_token)
    if login_identity != session_identity:
        raise RegistryError("登录响应与会话身份不一致")
    if not any(
        cookie.name == SESSION_COOKIE_NAME and cookie.value for cookie in client.cookies.jar
    ):
        raise RegistryError("登录成功但未收到安全会话 Cookie")
    brigade_code = manifest["case"]["brigadeCode"]
    if session_identity["role"] == "BRIGADE" and session_identity["brigadeCode"] != brigade_code:
        raise RegistryError("当前大队账户与 manifest 的 brigadeCode 不一致")
    return state_identity(session_identity), {
        "Origin": origin,
        WRITE_HEADER: WRITE_HEADER_VALUE,
        "X-CSRF-Token": csrf_token,
    }


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


def files_projection(manifest: dict[str, Any], upload: dict[str, Any]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for item in sorted(manifest["files"], key=lambda value: value["clientRef"]):
        source = upload.get(item["clientRef"])
        if not isinstance(source, str):
            raise RegistryError(f"文件投影缺少本地来源：{item['clientRef']}")
        source_path = Path(source)
        actual_sha256, actual_page_count = pdf_info(source_path)
        if actual_sha256 != item["sha256"] or (
            "pageCount" in item and actual_page_count != item["pageCount"]
        ):
            raise RegistryError(f"文件投影与本地 PDF 不一致：{item['clientRef']}")
        projection.append(
            {
                key: item[key]
                for key in ("clientRef", "relativePath", "sha256", "mimeType", "pageCount")
                if key in item
            }
            | {"pageCount": actual_page_count, "sizeBytes": source_path.stat().st_size}
        )
    return projection


def immutable_manifest_binding(manifest: dict[str, Any], projection: list[dict[str, Any]]) -> str:
    inspections = []
    for key in ("initialInspection", "recheckInspection"):
        inspection = manifest.get(key)
        if isinstance(inspection, dict):
            inspections.append(
                {
                    "clientRef": inspection.get("clientRef"),
                    "stage": inspection.get("stage"),
                    "products": sorted(
                        product.get("clientRef")
                        for product in inspection.get("products", [])
                        if isinstance(product, dict)
                    ),
                }
            )
    slots = [
        {
            "clientRef": slot.get("clientRef"),
            "slotCode": slot.get("slotCode"),
            "owner": {
                "inspectionRef": slot.get("inspectionRef"),
                "productRef": slot.get("productRef"),
                "notificationTarget": slot.get("notificationTarget"),
            },
            "versions": sorted(
                (
                    {
                        "kind": version.get("kind"),
                        "fileRef": version.get("fileRef"),
                    }
                    for version in slot.get("versions", [])
                    if isinstance(version, dict)
                ),
                key=lambda item: (item["kind"], item["fileRef"]),
            ),
        }
        for slot in manifest.get("documentSlots", [])
        if isinstance(slot, dict)
    ]
    attachments = [
        {
            "clientRef": attachment.get("clientRef"),
            "slotCode": attachment.get("slotCode"),
            "fileRef": attachment.get("fileRef"),
        }
        for attachment in manifest.get("otherAttachments", [])
        if isinstance(attachment, dict)
    ]
    binding = {
        "schemaVersion": manifest.get("schemaVersion"),
        "packageSha256": manifest.get("packageSha256"),
        "case": {
            "projectNo": manifest.get("case", {}).get("projectNo"),
            "brigadeCode": manifest.get("case", {}).get("brigadeCode"),
        },
        "inspections": sorted(inspections, key=lambda item: (item["stage"], item["clientRef"])),
        "documentSlots": sorted(slots, key=lambda item: item["clientRef"]),
        "otherAttachments": sorted(attachments, key=lambda item: item["clientRef"]),
        "filesProjection": projection,
    }
    canonical = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


STATE_BASE_KEYS = {
    "stateVersion",
    "status",
    "origin",
    "manifestSha256",
    "packageSha256",
    "projectNo",
    "brigadeCode",
    "jobId",
    "authIdentity",
    "filesProjection",
    "immutableBindingDigest",
    "uploadedFileRefs",
}
STATE_OPTIONAL_KEYS = {"caseId", "finalizedAt", "finalizeSummary", "verification", "verifiedAt"}


def validate_upload_state(state: dict[str, Any]) -> None:
    if not state:
        return
    if state.get("stateVersion") == 5:
        raise RegistryError("现有 upload-state 是旧 V5；不得自动续传，请改用 verify 只读核验")
    extra = set(state) - STATE_BASE_KEYS - STATE_OPTIONAL_KEYS
    missing = STATE_BASE_KEYS - set(state)
    if extra or missing or state.get("stateVersion") != 6:
        raise RegistryError("upload-state V6 字段不完整、包含额外字段或版本不受支持")
    status = state.get("status")
    if status not in {
        "UPLOADING",
        "FINALIZED_UNVERIFIED",
        "FINALIZED_WITH_CONFLICTS",
        "VERIFIED",
    }:
        raise RegistryError("upload-state V6 状态无效")
    if (
        not isinstance(state.get("origin"), str)
        or not SHA256.fullmatch(str(state.get("manifestSha256")))
        or not SHA256.fullmatch(str(state.get("packageSha256")))
        or not isinstance(state.get("projectNo"), str)
        or not PROJECT_NO.fullmatch(state["projectNo"])
        or state.get("brigadeCode") not in BRIGADES
        or not isinstance(state.get("jobId"), str)
        or not state.get("jobId")
        or not isinstance(state.get("filesProjection"), list)
        or not SHA256.fullmatch(str(state.get("immutableBindingDigest")))
        or not isinstance(state.get("uploadedFileRefs"), list)
        or any(not isinstance(item, str) for item in state["uploadedFileRefs"])
        or len(set(state["uploadedFileRefs"])) != len(state["uploadedFileRefs"])
    ):
        raise RegistryError("upload-state V6 基础字段无效")
    if not state["filesProjection"]:
        raise RegistryError("upload-state V6 文件投影无效")
    projection_refs: set[str] = set()
    for item in state["filesProjection"]:
        if not isinstance(item, dict) or set(item) != {
            "clientRef",
            "relativePath",
            "sha256",
            "mimeType",
            "pageCount",
            "sizeBytes",
        }:
            raise RegistryError("upload-state V6 文件投影无效")
        if (
            not ref(item.get("clientRef"))
            or item["clientRef"] in projection_refs
            or not isinstance(item.get("relativePath"), str)
            or not SHA256.fullmatch(str(item.get("sha256")))
            or item.get("mimeType") != "application/pdf"
            or not isinstance(item.get("pageCount"), int)
            or isinstance(item.get("pageCount"), bool)
            or item["pageCount"] < 1
            or not isinstance(item.get("sizeBytes"), int)
            or isinstance(item.get("sizeBytes"), bool)
            or item["sizeBytes"] < 1
        ):
            raise RegistryError("upload-state V6 文件投影无效")
        try:
            normalized_path = safe_relative(item["relativePath"])
        except RegistryError as exc:
            raise RegistryError("upload-state V6 文件投影无效") from exc
        if normalized_path != item["relativePath"] or not normalized_path.lower().endswith(".pdf"):
            raise RegistryError("upload-state V6 文件投影无效")
        projection_refs.add(item["clientRef"])
    if set(state["uploadedFileRefs"]) - projection_refs:
        raise RegistryError("upload-state V6 已上传引用超出文件投影")
    identity = state.get("authIdentity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"digest", "role", "brigadeCode"}
        or not SHA256.fullmatch(str(identity.get("digest")))
        or identity.get("role") not in {"ADMIN", "BRIGADE"}
        or (
            identity.get("brigadeCode") is not None
            and not isinstance(identity.get("brigadeCode"), str)
        )
        or (identity.get("role") == "ADMIN" and identity.get("brigadeCode") is not None)
        or (identity.get("role") == "BRIGADE" and not identity.get("brigadeCode"))
    ):
        raise RegistryError("upload-state V6 身份摘要无效")
    if identity.get("role") == "BRIGADE" and identity.get("brigadeCode") != state["brigadeCode"]:
        raise RegistryError("upload-state V6 大队与身份摘要不一致")
    if status == "UPLOADING" and set(state) != STATE_BASE_KEYS:
        raise RegistryError("UPLOADING 状态包含不允许的完成字段")
    if status in {"FINALIZED_UNVERIFIED", "FINALIZED_WITH_CONFLICTS", "VERIFIED"}:
        if not isinstance(state.get("caseId"), str) or not isinstance(
            state.get("finalizedAt"), str
        ):
            raise RegistryError("已终结状态缺少 caseId 或 finalizedAt")
        summary = state.get("finalizeSummary")
        if status == "VERIFIED" and summary is None:
            summary = None
        elif not isinstance(summary, dict):
            raise RegistryError("upload-state V6 的 finalizeSummary 无效")
        if summary is not None:
            if set(summary) != {
                "caseId",
                "created",
                "addedProducts",
                "addedSlots",
                "addedAttachments",
                "replacedSlots",
                "conflictCount",
                "skippedCount",
            }:
                raise RegistryError("upload-state V6 的 finalizeSummary 无效")
            if summary.get("caseId") != state["caseId"] or not isinstance(
                summary.get("created"), bool
            ):
                raise RegistryError("upload-state V6 的 finalizeSummary 身份无效")
            for key in set(summary) - {"caseId", "created"}:
                value = summary[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise RegistryError("upload-state V6 的 finalizeSummary 计数无效")
            if status == "VERIFIED" and (
                not summary["created"]
                or summary["conflictCount"] > 0
                or summary["skippedCount"] > 0
            ):
                raise RegistryError("存在冲突或跳过项时不得进入 VERIFIED")
    if status in {"FINALIZED_UNVERIFIED", "FINALIZED_WITH_CONFLICTS"} and set(
        state
    ) != STATE_BASE_KEYS | {
        "caseId",
        "finalizedAt",
        "finalizeSummary",
    }:
        raise RegistryError("终结待处理状态字段不封闭")
    if status == "VERIFIED":
        complete_keys = STATE_BASE_KEYS | {
            "caseId",
            "finalizedAt",
            "verification",
            "verifiedAt",
        }
        if frozenset(state) not in {
            frozenset(complete_keys),
            frozenset(complete_keys | {"finalizeSummary"}),
        }:
            raise RegistryError("VERIFIED 状态字段不封闭")
        verification = state.get("verification")
        if (
            not isinstance(verification, dict)
            or set(verification) != {"caseId", "inspections", "products", "filesVerified"}
            or verification.get("caseId") != state["caseId"]
            or not isinstance(state.get("verifiedAt"), str)
        ):
            raise RegistryError("VERIFIED 状态核验摘要无效")
        for key in ("inspections", "products", "filesVerified"):
            value = verification[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RegistryError("VERIFIED 状态核验计数无效")


def finalize_summary(result: dict[str, Any]) -> dict[str, Any]:
    case_id, created = result.get("caseId"), result.get("created")
    added, replaced = result.get("added"), result.get("replaced")
    conflicts, skipped = result.get("conflicts"), result.get("skipped")
    if (
        not isinstance(case_id, str)
        or not isinstance(created, bool)
        or not isinstance(added, dict)
        or not isinstance(replaced, dict)
        or not isinstance(conflicts, list)
        or not isinstance(skipped, list)
    ):
        raise RegistryError("finalize 响应缺少受支持的摘要字段")
    counts = {
        "addedProducts": added.get("products"),
        "addedSlots": added.get("slots"),
        "addedAttachments": added.get("attachments"),
        "replacedSlots": replaced.get("slots"),
        "conflictCount": len(conflicts),
        "skippedCount": len(skipped),
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        raise RegistryError("finalize 响应计数无效")
    return {"caseId": case_id, "created": created, **counts}


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


def get_import_job(
    client: httpx.Client,
    api_base: str,
    job_id: str,
    package_sha: str,
    project_no: str,
    brigade_code: str,
) -> dict[str, Any]:
    try:
        job = response_json(client.get(f"{api_base}/api/v2/import-jobs/{job_id}"), "读取导入任务")
    except RegistryError as error:
        if "HTTP 404" in str(error):
            raise RegistryError("服务端导入任务不存在；不得猜测或重建任务") from error
        raise
    if job.get("id") != job_id:
        raise RegistryError("服务端导入任务 id 对账失败")
    if job.get("packageHash") != package_sha:
        raise RegistryError("服务端导入任务包哈希对账失败")
    status = job.get("status")
    if status == "FAILED":
        raise RegistryError("服务端导入任务已 FAILED，停止续传")
    if status not in {"CREATED", "UPLOADING", "MANIFEST_RECEIVED", "FINALIZED"}:
        raise RegistryError("服务端导入任务状态不受支持，停止续传")
    case = job.get("case")
    if status == "FINALIZED":
        brigade = case.get("brigade") if isinstance(case, dict) else None
        route_path = brigade.get("routePath") if isinstance(brigade, dict) else None
        if (
            not isinstance(case, dict)
            or case.get("projectNo") != project_no
            or not isinstance(route_path, str)
            or route_path.strip("/").upper() != brigade_code
        ):
            raise RegistryError("服务端已终结任务项目或大队归属对账失败")
        if job.get("packageName") != project_no or case.get("projectNo") != project_no:
            raise RegistryError("服务端已终结任务项目编号对账失败")
        if not isinstance(job.get("finalizedAt"), str) or not isinstance(
            job.get("resultSummary"), dict
        ):
            raise RegistryError("服务端已终结任务缺少 finalizedAt 或结果摘要")
    elif status == "MANIFEST_RECEIVED" and job.get("packageName") != project_no:
        raise RegistryError("服务端已接收清单任务项目编号对账失败")
    return job


def check_uploaded_file_response(value: dict[str, Any], item: dict[str, Any], job_id: str) -> None:
    size_bytes = value.get("sizeBytes")
    if (
        value.get("jobId") != job_id
        or value.get("relativePath") != item["relativePath"]
        or value.get("sha256") != item["sha256"]
        or value.get("mimeType") != item["mimeType"]
        or not isinstance(size_bytes, str)
        or not re.fullmatch(r"(?:0|[1-9][0-9]*)", size_bytes)
        or int(size_bytes) != item["sizeBytes"]
    ):
        raise RegistryError("上传 PDF 响应与文件投影、大小或任务归属不一致")


def check_manifest_response(value: dict[str, Any], job_id: str) -> None:
    if value.get("id") != job_id or value.get("status") != "MANIFEST_RECEIVED":
        raise RegistryError("提交清单响应与任务状态不一致")


def finalized_summary_from_job(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") != "FINALIZED":
        raise RegistryError("服务端导入任务尚未终结")
    if not isinstance(job.get("finalizedAt"), str) or not isinstance(
        job.get("resultSummary"), dict
    ):
        raise RegistryError("服务端终结任务缺少 finalizedAt 或结果摘要")
    return finalize_summary(job["resultSummary"])


def expected_products(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        product["clientRef"]: {**product, "_stage": inspection["stage"]}
        for inspection in (manifest["initialInspection"], manifest.get("recheckInspection"))
        if isinstance(inspection, dict)
        for product in inspection.get("products", [])
    }


def response_error_code(response: httpx.Response) -> str | None:
    """Read only a non-sensitive discriminator from an error response."""
    value = limited_json_value(response)
    if value is None:
        return None
    return (
        value.get("code")
        if isinstance(value, dict) and isinstance(value.get("code"), str)
        else None
    )


def recall_projection(response: httpx.Response, label: str) -> dict[str, Any]:
    projection = response_json(response, label)
    status = projection.get("status")
    recall_id = projection.get("recallId")
    if status not in RECALL_STATUSES or not isinstance(recall_id, str) or not recall_id:
        raise RegistryError(f"{label} 返回的取回状态无效")
    return projection


def recall_until_ready(
    client: httpx.Client,
    api_base: str,
    file_id: str,
    write_headers: dict[str, str],
) -> None:
    """Recall a remote-only file, reusing the server's idempotent recall job."""
    started = time.monotonic()
    deadline = started + RECALL_POLL_INTERVAL_SECONDS * RECALL_MAX_POLLS + 30.0

    def request_recall() -> dict[str, Any]:
        try:
            response = client.post(
                f"{api_base}/api/v2/files/{file_id}/recall", headers=write_headers
            )
        except httpx.HTTPError as error:
            raise RegistryError("发起文件取回时网络异常") from error
        return recall_projection(response, "发起文件取回")

    projection = request_recall()
    if projection["status"] == "READY":
        return
    if projection["status"] in {"OFFLINE", "FAILED"}:
        raise RegistryError(f"文件取回未完成：{projection['status']}")

    for _ in range(RECALL_MAX_POLLS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(RECALL_POLL_INTERVAL_SECONDS, remaining))
        try:
            response = client.get(f"{api_base}/api/v2/file-recalls/{projection['recallId']}")
        except httpx.HTTPError as error:
            raise RegistryError("读取文件取回进度时网络异常") from error
        if response.status_code == 409 and response_error_code(response) == "RECALL_REQUIRED":
            # The relay may have expired while polling. The server reuses an
            # existing task, so issuing recall again is safe and idempotent.
            projection = request_recall()
        else:
            projection = recall_projection(response, "读取文件取回进度")
        if projection["status"] == "READY":
            return
        if projection["status"] in {"OFFLINE", "FAILED"}:
            raise RegistryError(f"文件取回未完成：{projection['status']}")
    raise RegistryError("文件取回等待超时；取回任务仍由服务端保留")


def download_verified_file(
    client: httpx.Client,
    api_base: str,
    remote_id: str,
    expected_sha256: str,
    write_headers: dict[str, str] | None,
    file_ref: str,
) -> None:
    for _ in range(2):
        try:
            with client.stream("GET", f"{api_base}/api/v2/files/{remote_id}") as response:
                if (
                    response.status_code == 409
                    and response_error_code(response) == "RECALL_REQUIRED"
                ):
                    needs_recall = True
                else:
                    needs_recall = False
                    if response.status_code != 200:
                        raise RegistryError(
                            f"下载文件失败：HTTP {response.status_code}：{file_ref}"
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_bytes = int(content_length)
                        except ValueError as error:
                            raise RegistryError(f"下载文件长度无效：{file_ref}") from error
                        if declared_bytes < 0 or declared_bytes > MAX_DEEP_VERIFY_BYTES:
                            raise RegistryError(f"下载文件超过深度核验大小限制：{file_ref}")
                    digest = hashlib.sha256()
                    total_bytes = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_DEEP_VERIFY_BYTES:
                            raise RegistryError(f"下载文件超过深度核验大小限制：{file_ref}")
                        digest.update(chunk)
                    if content_length is not None and total_bytes != declared_bytes:
                        raise RegistryError(f"下载文件长度不一致：{file_ref}")
                    actual_sha256 = "sha256:" + digest.hexdigest()
                    if actual_sha256 != expected_sha256:
                        raise RegistryError(f"下载 SHA-256 不一致：{file_ref}")
                    return
        except httpx.HTTPError as error:
            raise RegistryError(f"下载文件时网络异常：{file_ref}") from error
        if needs_recall:
            if write_headers is None:
                raise RegistryError(f"文件需要先从飞牛取回，无法安全核验：{file_ref}")
            recall_until_ready(client, api_base, remote_id, write_headers)
            continue
    raise RegistryError(f"文件取回后仍无法下载：{file_ref}")


def verify_with_client(
    client: httpx.Client,
    api_base: str,
    manifest: dict[str, Any],
    write_headers: dict[str, str] | None = None,
    deep_content_verify: bool = False,
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
    reported_inspection_count = len(actual_inspections)
    initial_products = manifest["initialInspection"].get("products", [])

    def effective_result(product: dict[str, Any]) -> Any:
        result = product.get("result")
        if (
            product.get("method") == "SAMPLING"
            and product.get("reinspectionApplied") == "YES"
            and product.get("reinspectionResult") in RESULTS
        ):
            return product["reinspectionResult"]
        return result

    allows_derived_empty_recheck = "recheckInspection" not in manifest and any(
        isinstance(product, dict) and effective_result(product) == "UNQUALIFIED"
        for product in initial_products
    )
    derived_recheck = actual_inspections.get("RECHECK")
    if (
        allows_derived_empty_recheck
        and isinstance(derived_recheck, dict)
        and derived_recheck.get("products") == []
        and sum(item.get("stage") == "RECHECK" for item in actual_rows) == 1
    ):
        actual_rows = [item for item in actual_rows if item is not derived_recheck]
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
                "repairSiteId",
                "reinspectionApplied",
                "reinspectionResult",
            ):
                if field == "repairSiteId":
                    local_repair_site_id = product.get(field)
                    remote_repair_site_id = remote.get(field)
                    if isinstance(local_repair_site_id, str):
                        local_repair_site_id = local_repair_site_id.lower()
                    if isinstance(remote_repair_site_id, str):
                        remote_repair_site_id = remote_repair_site_id.lower()
                    if remote_repair_site_id != local_repair_site_id:
                        raise RegistryError(f"产品字段不一致：{field}")
                elif field in product and remote.get(field) != product[field]:
                    raise RegistryError(f"产品字段不一致：{field}")
            remote_product_by_ref[product["clientRef"]] = remote
    source_files = {item["clientRef"]: item for item in manifest["files"]}
    rows = directory.get("rows", [])

    def check_file(file_ref: str, remote: dict[str, Any]) -> None:
        expected = source_files[file_ref]
        if remote.get("sha256") != expected["sha256"]:
            raise RegistryError(f"目录 SHA-256 不一致：{file_ref}")
        remote_state = remote.get("remoteState")
        if remote_state != "AVAILABLE":
            if remote_state == "PENDING":
                raise RegistryError(f"飞牛落盘核验中：{file_ref}")
            raise RegistryError(f"飞牛正式库不可用：{file_ref}")
        nas_verified_at = remote.get("nasVerifiedAt")
        if not isinstance(nas_verified_at, str) or not nas_verified_at.strip():
            raise RegistryError(f"飞牛落盘核验中：{file_ref}")
        remote_id = remote.get("id")
        if not isinstance(remote_id, str):
            raise RegistryError(f"目录缺少文件标识：{file_ref}")
        if deep_content_verify:
            download_verified_file(
                client,
                api_base,
                remote_id,
                expected["sha256"],
                write_headers,
                file_ref,
            )

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
            check_file(version["fileRef"], remote)
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
        check_file(attachment["fileRef"], children[0]["files"][0])
    return {
        "caseId": case_id,
        "inspections": reported_inspection_count,
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


def complete_verified_upload_state(
    state_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    upload: dict[str, Any],
    origin: str,
    identity: dict[str, str | None],
    verification: dict[str, Any],
) -> dict[str, Any] | None:
    """Close a clean finalized V6 state after a standalone successful verify."""

    if not state_path.exists():
        return None
    state = read_json(state_path)
    # Old states remain eligible for read-only remote verification, but they are
    # never upgraded or archived implicitly.
    if state.get("stateVersion") != 6:
        return state
    validate_upload_state(state)
    if state["status"] not in {"FINALIZED_UNVERIFIED", "VERIFIED"}:
        return state
    projection = files_projection(manifest, upload)
    binding_digest = immutable_manifest_binding(manifest, projection)
    if (
        state.get("origin") != origin
        or state.get("manifestSha256") != file_sha256(manifest_path)
        or state.get("packageSha256") != manifest.get("packageSha256")
        or state.get("projectNo") != manifest.get("case", {}).get("projectNo")
        or state.get("brigadeCode") != manifest.get("case", {}).get("brigadeCode")
        or state.get("filesProjection") != projection
        or state.get("immutableBindingDigest") != binding_digest
    ):
        raise RegistryError("verify 成功，但 upload-state V6 与当前清单或目标不一致，未收口")
    require_same_state_identity(state, identity)
    if state["status"] == "FINALIZED_UNVERIFIED":
        state.update(
            {
                "status": "VERIFIED",
                "verification": verification,
                "verifiedAt": utc_now(),
            }
        )
        validate_upload_state(state)
        write_json(state_path, state)
    return state


def archive_verified_state(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not state or state.get("status") != "VERIFIED" or getattr(args, "no_archive", False):
        return None
    resolved = resolve_manifest_workspace(args, manifest_path, manifest)
    if resolved is None:
        return None
    workspace, layout = resolved
    try:
        return workspace.archive_verified_case(
            layout,
            manifest["case"]["projectNo"],
            upload_status="VERIFIED",
            verification={**state["verification"], "status": "VERIFIED"},
            manifest_sha256=file_sha256(manifest_path),
            package_sha256=manifest["packageSha256"],
            verified_at=state.get("verifiedAt"),
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"服务端核验已完成，但本地归档失败：{error}") from error


def resolve_manifest_workspace(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    require_membership: bool = False,
) -> tuple[Any, Any] | None:
    workspace = workspace_api()
    work_root = getattr(args, "work_root", None)
    config_path = getattr(args, "workspace_config", None)
    default_config = workspace.default_workspace_config_path()
    if work_root is None and config_path is None and not default_config.exists():
        if require_membership:
            raise RegistryError("正式上传前必须先配置 workspace.toml 或显式指定 --work-root")
        return None
    try:
        _config, layout = workspace.resolve_workspace(
            work_root=work_root,
            config_path=config_path,
            create_layout=False,
        )
        active_case_dir = layout.work_case_dir(manifest["case"]["projectNo"])
        if not inside(manifest_path, active_case_dir):
            if require_membership or work_root is not None or config_path is not None:
                raise RegistryError("当前 manifest 不在选定工作根的项目工作区，拒绝更新水位或归档")
            return None
        return workspace, layout
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"无法安全解析当前案卷的工作根：{error}") from error


def enforce_upload_workspace_preflight(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    """Reject out-of-workspace production uploads before authentication."""

    if not getattr(args, "workspace_required", False):
        # Keep the pre-1.4 in-process function contract for callers that invoke
        # upload_command directly. Every real 1.4 CLI upload sets this marker.
        return
    resolve_manifest_workspace(args, manifest_path, manifest, require_membership=True)


def update_case_waterline(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    state: str,
    upload: dict[str, Any],
    nas_verification: dict[str, Any] | None = None,
    error_summary: str | None = None,
) -> dict[str, Any] | None:
    resolved = resolve_manifest_workspace(args, manifest_path, manifest)
    if resolved is None:
        return None
    workspace, layout = resolved
    try:
        return workspace.upsert_case(
            layout,
            manifest["case"]["projectNo"],
            state=state,
            upload=upload,
            **({"nasVerification": nas_verification} if nas_verification is not None else {}),
            errorSummary=error_summary,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"业务操作状态已变更，但案卷水位更新失败：{error}") from error


def mark_uploading(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    update_case_waterline(
        args,
        manifest_path,
        manifest,
        state="UPLOADING",
        upload={"status": "UPLOADING", "startedAt": utc_now()},
        nas_verification={"status": "NOT_STARTED"},
        error_summary=None,
    )


def mark_verified_pending_archive(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> None:
    verification = state.get("verification")
    if state.get("status") != "VERIFIED" or not isinstance(verification, dict):
        return
    update_case_waterline(
        args,
        manifest_path,
        manifest,
        state="VERIFIED_PENDING_ARCHIVE",
        upload={"status": "VERIFIED", "finalizedAt": state.get("finalizedAt")},
        nas_verification={**verification, "status": "VERIFIED"},
        error_summary=None,
    )


def archive_result_fields(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {}
    fields: dict[str, Any] = {"archive": result}
    excel_error = result.get("waterlineXlsxError")
    if isinstance(excel_error, str) and excel_error:
        fields["warning"] = f"案卷已完成归档，但 Excel 水位表导出失败：{excel_error}"
    return fields


def mark_awaiting_nas(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    error_summary: str,
) -> None:
    update_case_waterline(
        args,
        manifest_path,
        manifest,
        state="UPLOADED_AWAITING_NAS",
        upload={
            "status": "FINALIZED_UNVERIFIED",
            "finalizedAt": state.get("finalizedAt"),
        },
        nas_verification={"status": "PENDING"},
        error_summary=error_summary,
    )


def mark_verification_failure(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    error_summary: str,
) -> None:
    update_case_waterline(
        args,
        manifest_path,
        manifest,
        state="NEEDS_MANUAL_REVIEW",
        upload={
            "status": "FINALIZED_UNVERIFIED",
            "finalizedAt": state.get("finalizedAt"),
        },
        nas_verification={"status": "FAILED"},
        error_summary=error_summary,
    )


def verification_error_is_waiting(error: RegistryError | str) -> bool:
    message = str(error)
    if message.startswith(("飞牛落盘核验中：", "飞牛正式库不可用：", "飞牛正式库暂不可用：")):
        return True
    if message.startswith("文件取回未完成："):
        return message.rsplit("：", 1)[-1] in {"PENDING", "PROCESSING", "OFFLINE"}
    return message.startswith(
        (
            "发起文件取回时网络异常",
            "读取文件取回进度时网络异常",
            "文件取回等待超时",
            "文件需要先从飞牛取回",
            "文件取回后仍无法下载",
            "下载文件时网络异常",
        )
    )


def mark_verification_error(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    error: RegistryError,
) -> None:
    if verification_error_is_waiting(error):
        mark_awaiting_nas(args, manifest_path, manifest, state, str(error))
    else:
        mark_verification_failure(args, manifest_path, manifest, state, str(error))


def mark_upload_conflict(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> None:
    summary = state.get("finalizeSummary") if isinstance(state.get("finalizeSummary"), dict) else {}
    update_case_waterline(
        args,
        manifest_path,
        manifest,
        state="NEEDS_MANUAL_REVIEW",
        upload={
            "status": "FINALIZED_WITH_CONFLICTS",
            "finalizedAt": state.get("finalizedAt"),
            "created": summary.get("created"),
            "conflictCount": summary.get("conflictCount"),
            "skippedCount": summary.get("skippedCount"),
        },
        nas_verification={"status": "NOT_STARTED"},
        error_summary="终结结果包含冲突、跳过项或未新建案卷，需人工处理",
    )


def resolve_local_case_workspace(
    args: argparse.Namespace,
    work_dir: Path,
    project_no: str | None = None,
) -> tuple[Any, Any, str] | None:
    workspace = workspace_api()
    work_root = getattr(args, "work_root", None)
    config_path = getattr(args, "workspace_config", None)
    if (
        work_root is None
        and config_path is None
        and not workspace.default_workspace_config_path().exists()
    ):
        return None
    try:
        _config, layout = workspace.resolve_workspace(
            work_root=work_root,
            config_path=config_path,
            create_layout=False,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"无法安全解析本地整理工作根：{error}") from error
    candidate = project_no or work_dir.name
    if not isinstance(candidate, str) or not PROJECT_NO.fullmatch(candidate):
        return None
    expected = layout.work_case_dir(candidate)
    if work_dir.resolve() != expected.resolve():
        return None
    return workspace, layout, candidate


def update_local_case_waterline(
    args: argparse.Namespace,
    work_dir: Path,
    *,
    state: str,
    local_status: str,
    project_no: str | None = None,
) -> None:
    resolved = resolve_local_case_workspace(args, work_dir, project_no)
    if resolved is None:
        return
    workspace, layout, project = resolved
    try:
        workspace.upsert_case(
            layout,
            project,
            state=state,
            local={"status": local_status},
            errorSummary=None,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"本地处理已完成，但案卷水位更新失败：{error}") from error


def enforce_local_workspace_preflight(
    args: argparse.Namespace,
    work_dir: Path,
    project_no: str | None = None,
    source_path: Path | None = None,
) -> None:
    """Require real CLI organization writes to stay in the configured case workspace."""

    if not getattr(args, "workspace_required", False):
        return
    resolved = resolve_local_case_workspace(args, work_dir, project_no)
    if resolved is None:
        raise RegistryError(
            "本地处理前必须先配置工作根，且 --work-dir 必须精确指向当前工作根的工作区/<项目编号>"
        )
    _workspace, layout, project = resolved
    if source_path is not None and not inside(source_path, layout.pending_case_dir(project)):
        raise RegistryError("inventory 原始输入必须位于当前工作根的原始案卷/待处理案卷/<项目编号>")


def verify_command(args: argparse.Namespace) -> None:
    path, manifest, upload = load_inputs(args.manifest, args.upload_map)
    api_base, origin = origin_of(args.api_base)
    state_path = path.parent / "upload-state.json"
    existing_state = read_json(state_path) if state_path.exists() else {}
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)), follow_redirects=False
    ) as client:
        identity, write_headers = authenticate_client(
            client,
            api_base,
            origin,
            manifest,
            secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
        )
        try:
            verification = verify_with_poll(
                client,
                api_base,
                manifest,
                write_headers,
                getattr(args, "deep_content_verify", False),
                min(max(float(args.timeout), 1.0), 60.0),
            )
        except RegistryError as error:
            if existing_state.get("status") == "FINALIZED_UNVERIFIED":
                mark_verification_error(args, path, manifest, existing_state, error)
            raise
        if verification is None:
            if existing_state.get("status") == "FINALIZED_UNVERIFIED":
                mark_awaiting_nas(
                    args,
                    path,
                    manifest,
                    existing_state,
                    "飞牛落盘核验仍在进行，本次等待超时",
                )
            raise RegistryError("飞牛落盘核验仍在进行，请稍后重新运行 verify")
        state = complete_verified_upload_state(
            state_path,
            path,
            manifest,
            upload,
            origin,
            identity,
            verification,
        )
        if state and state.get("status") == "VERIFIED":
            mark_verified_pending_archive(args, path, manifest, state)
        archive_result = archive_verified_state(args, path, manifest, state)
        print(
            json.dumps(
                {**verification, **archive_result_fields(archive_result)},
                ensure_ascii=False,
            )
        )


def verify_with_poll(
    client: httpx.Client,
    api_base: str,
    manifest: dict[str, Any],
    write_headers: dict[str, str],
    deep_content_verify: bool,
    max_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max_seconds
    while True:
        try:
            return verify_with_client(
                client, api_base, manifest, write_headers, deep_content_verify
            )
        except RegistryError as error:
            if not str(error).startswith("飞牛落盘核验中："):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(RECALL_POLL_INTERVAL_SECONDS, remaining))


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
    enforce_upload_workspace_preflight(args, path, manifest)
    api_base, origin = origin_of(args.api_base)
    manifest_sha, package_sha = file_sha256(path), manifest["packageSha256"]
    brigade_code = manifest["case"]["brigadeCode"]
    projection = files_projection(manifest, upload)
    binding_digest = immutable_manifest_binding(manifest, projection)
    state_path = path.parent / "upload-state.json"
    state = read_json(state_path) if state_path.exists() else {}
    validate_upload_state(state)
    if state and (
        state.get("packageSha256") != package_sha
        or state.get("origin") != origin
        or state.get("projectNo") != manifest["case"]["projectNo"]
        or state.get("brigadeCode") != brigade_code
        or state.get("filesProjection") != projection
        or state.get("immutableBindingDigest") != binding_digest
    ):
        raise RegistryError("upload-state V6 与当前清单、文件投影、origin 或目标不匹配")
    if (
        state
        and state["status"]
        in {
            "FINALIZED_UNVERIFIED",
            "FINALIZED_WITH_CONFLICTS",
            "VERIFIED",
        }
        and state.get("manifestSha256") != manifest_sha
    ):
        raise RegistryError("已终结 upload-state 要求 manifestSha256 精确不变")
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
        follow_redirects=False,
    ) as client:
        identity, write_headers = authenticate_client(
            client,
            api_base,
            origin,
            manifest,
            secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
        )
        require_same_state_identity(state, identity)
        if response_json(client.get(f"{api_base}/api/ready"), "服务就绪").get("status") != "ready":
            raise RegistryError("服务未就绪")
        job_id: str
        if state:
            job = get_import_job(
                client,
                api_base,
                state["jobId"],
                package_sha,
                manifest["case"]["projectNo"],
                brigade_code,
            )
            if job["status"] == "FINALIZED":
                summary = finalized_summary_from_job(job)
                conflict_status = (
                    "FINALIZED_WITH_CONFLICTS"
                    if (
                        not summary["created"]
                        or summary["conflictCount"] > 0
                        or summary["skippedCount"] > 0
                    )
                    else "FINALIZED_UNVERIFIED"
                )
                state.pop("verification", None)
                state.pop("verifiedAt", None)
                state.update(
                    {
                        "status": conflict_status,
                        "caseId": summary["caseId"],
                        "finalizedAt": job["finalizedAt"],
                        "finalizeSummary": summary,
                    }
                )
                validate_upload_state(state)
                write_json(state_path, state)
                if conflict_status == "FINALIZED_WITH_CONFLICTS":
                    mark_upload_conflict(args, path, manifest, state)
                    print(
                        json.dumps(
                            {
                                "status": state["status"],
                                "caseId": state["caseId"],
                                "finalize": state.get("finalizeSummary"),
                                "verification": None,
                                "manualReviewRequired": True,
                            },
                            ensure_ascii=False,
                        )
                    )
                    return
                mark_awaiting_nas(
                    args,
                    path,
                    manifest,
                    state,
                    "服务端已终结，等待飞牛落盘核验",
                )
                try:
                    verification = verify_with_poll(
                        client,
                        api_base,
                        manifest,
                        write_headers,
                        getattr(args, "deep_content_verify", False),
                        min(max(float(args.timeout), 1.0), 60.0),
                    )
                except RegistryError as error:
                    mark_verification_error(args, path, manifest, state, error)
                    raise
                if verification is not None:
                    state.update(
                        {
                            "status": "VERIFIED",
                            "verification": verification,
                            "verifiedAt": utc_now(),
                        }
                    )
                    validate_upload_state(state)
                    write_json(state_path, state)
                else:
                    mark_awaiting_nas(
                        args,
                        path,
                        manifest,
                        state,
                        "飞牛落盘核验仍在进行，本次等待超时",
                    )
                if state.get("status") == "VERIFIED":
                    mark_verified_pending_archive(args, path, manifest, state)
                archive_result = archive_verified_state(args, path, manifest, state)
                print(
                    json.dumps(
                        {
                            "status": state["status"],
                            "caseId": state["caseId"],
                            "finalize": state.get("finalizeSummary"),
                            "verification": state.get("verification"),
                            **archive_result_fields(archive_result),
                        },
                        ensure_ascii=False,
                    )
                )
                return
            elif job["status"] == "FAILED":
                raise RegistryError("服务端导入任务已 FAILED，停止续传；请修正后新建任务")
            elif job["status"] not in {"CREATED", "UPLOADING", "MANIFEST_RECEIVED"}:
                raise RegistryError("服务端任务状态或项目编号无法安全对账")
            job_id = state["jobId"]
            state["manifestSha256"] = manifest_sha
        else:
            existing = exact_case(client, api_base, manifest["case"]["projectNo"])
            if existing:
                raise RegistryError("目标项目编号已存在；为防覆盖人工数据已停止")
            job = response_json(
                client.post(
                    f"{api_base}/api/v2/import-jobs",
                    headers={
                        **write_headers,
                        "Idempotency-Key": f"xfpcr-v2-{package_sha[7:]}",
                    },
                    json={"packageSha256": package_sha},
                ),
                "创建导入任务",
            )
            job_id = job.get("id") if isinstance(job.get("id"), str) else ""
            if (
                not job_id
                or job.get("packageHash") != package_sha
                or job.get("status") not in {"CREATED", "UPLOADING"}
            ):
                raise RegistryError("创建导入任务响应无法安全对账")
            state = {
                "stateVersion": 6,
                "status": "UPLOADING",
                "origin": origin,
                "manifestSha256": manifest_sha,
                "packageSha256": package_sha,
                "projectNo": manifest["case"]["projectNo"],
                "brigadeCode": brigade_code,
                "jobId": job_id,
                "authIdentity": identity,
                "filesProjection": projection,
                "immutableBindingDigest": binding_digest,
                "uploadedFileRefs": [],
            }
        validate_upload_state(state)
        write_json(state_path, state)
        mark_uploading(args, path, manifest)
        projection_by_ref = {item["clientRef"]: item for item in projection}
        for item in manifest["files"]:
            projection_item = projection_by_ref[item["clientRef"]]
            with Path(upload[item["clientRef"]]).open("rb") as stream:
                uploaded_response = response_json(
                    client.post(
                        f"{api_base}/api/v2/import-jobs/{job_id}/files",
                        headers=write_headers,
                        params={"relativePath": item["relativePath"]},
                        files={"file": (Path(stream.name).name, stream, "application/pdf")},
                    ),
                    "上传 PDF",
                )
            check_uploaded_file_response(uploaded_response, projection_item, job_id)
            state["uploadedFileRefs"] = sorted({*state["uploadedFileRefs"], item["clientRef"]})
            write_json(state_path, state)
        manifest_response = response_json(
            client.put(
                f"{api_base}/api/v2/import-jobs/{job_id}/manifest",
                headers=write_headers,
                json=manifest,
            ),
            "提交清单",
        )
        check_manifest_response(manifest_response, job_id)
        response_json(
            client.post(
                f"{api_base}/api/v2/import-jobs/{job_id}/finalize",
                headers=write_headers,
            ),
            "完成导入",
        )
        finalized_job = get_import_job(
            client,
            api_base,
            job_id,
            package_sha,
            manifest["case"]["projectNo"],
            brigade_code,
        )
        summary = finalized_summary_from_job(finalized_job)
        conflict_status = (
            "FINALIZED_WITH_CONFLICTS"
            if (
                not summary["created"]
                or summary["conflictCount"] > 0
                or summary["skippedCount"] > 0
            )
            else "FINALIZED_UNVERIFIED"
        )
        state.update(
            {
                "status": conflict_status,
                "finalizedAt": finalized_job["finalizedAt"],
                "finalizeSummary": summary,
                "caseId": summary["caseId"],
            }
        )
        validate_upload_state(state)
        write_json(state_path, state)
        if conflict_status == "FINALIZED_WITH_CONFLICTS":
            mark_upload_conflict(args, path, manifest, state)
            print(
                json.dumps(
                    {
                        "status": state["status"],
                        "caseId": state["caseId"],
                        "finalize": state.get("finalizeSummary"),
                        "verification": None,
                        "manualReviewRequired": True,
                    },
                    ensure_ascii=False,
                )
            )
            return
        mark_awaiting_nas(
            args,
            path,
            manifest,
            state,
            "服务端已终结，等待飞牛落盘核验",
        )
        try:
            verification = verify_with_poll(
                client,
                api_base,
                manifest,
                write_headers,
                getattr(args, "deep_content_verify", False),
                min(max(float(args.timeout), 1.0), 60.0),
            )
        except RegistryError as error:
            mark_verification_error(args, path, manifest, state, error)
            raise
        if verification is not None:
            state.update(
                {"status": "VERIFIED", "verification": verification, "verifiedAt": utc_now()}
            )
            validate_upload_state(state)
            write_json(state_path, state)
            status = "VERIFIED"
        else:
            status = "FINALIZED_UNVERIFIED"
            mark_awaiting_nas(
                args,
                path,
                manifest,
                state,
                "飞牛落盘核验仍在进行，本次等待超时",
            )
    if state.get("status") == "VERIFIED":
        mark_verified_pending_archive(args, path, manifest, state)
    archive_result = archive_verified_state(args, path, manifest, state)
    print(
        json.dumps(
            {
                "status": status,
                "caseId": state["caseId"],
                "finalize": state.get("finalizeSummary"),
                "verification": state.get("verification"),
                **archive_result_fields(archive_result),
            },
            ensure_ascii=False,
        )
    )


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


def workspace_api() -> Any:
    module_name = f"{__package__}.workspace_state" if __package__ else "workspace_state"
    return importlib.import_module(module_name)


def source_intake_api() -> Any:
    module_name = f"{__package__}.source_intake" if __package__ else "source_intake"
    return importlib.import_module(module_name)


def workspace_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "work_root": getattr(args, "work_root", None),
        "config_path": getattr(args, "workspace_config", None),
    }


def workspace_configure_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        config = workspace.configure_workspace(
            work_root=args.work_root,
            download_dir=getattr(args, "download_dir", None),
            config_path=getattr(args, "workspace_config", None),
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(
        json.dumps(
            {
                "status": "configured",
                "workRoot": str(config.work_root),
                "downloadDir": str(config.download_dir) if config.download_dir else None,
                "configPath": str(
                    Path(args.workspace_config).resolve()
                    if getattr(args, "workspace_config", None)
                    else workspace.default_workspace_config_path()
                ),
            },
            ensure_ascii=False,
        )
    )


def workspace_doctor_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        result = workspace.doctor_workspace(**workspace_kwargs(args))
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def ledger_export_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        output = workspace.export_waterline_xlsx(layout)
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps({"status": "exported", "path": str(output)}, ensure_ascii=False))


def source_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "schemaVersion",
        "batchId",
        "status",
        "currentRound",
        "stableRounds",
        "listResult",
        "progress",
        "conflicts",
        "scope",
        "listContract",
        "updatesGlobalWaterline",
    }
    return {key: result[key] for key in keys if key in result}


def resolve_source_layout(args: argparse.Namespace) -> Any:
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        return layout
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error


def run_source_action(args: argparse.Namespace, action: str, **kwargs: Any) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        result = getattr(source, action)(resolve_source_layout(args), **kwargs)
    except (source.SourceIntakeError, workspace.WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(source_result_summary(result), ensure_ascii=False))


def source_begin_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        _, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        result = source.begin_capture(
            layout,
            filter_json=Path(args.filter_json) if getattr(args, "filter_json", None) else None,
            batch_id=getattr(args, "batch_id", None),
            origin=args.origin,
            scope="acceptance" if getattr(args, "acceptance_sample", False) else "all",
        )
    except (source.SourceIntakeError, workspace.WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(source_result_summary(result), ensure_ascii=False))


def source_add_page_command(args: argparse.Namespace) -> None:
    run_source_action(
        args,
        "add_page",
        batch_id=args.batch_id,
        page=Path(args.page_json),
        screenshot=Path(args.screenshot) if getattr(args, "screenshot", None) else None,
        round_no=getattr(args, "round_no", None),
    )


def source_add_detail_command(args: argparse.Namespace) -> None:
    if not str(args.source_url).strip():
        raise RegistryError("详情来源地址不能为空")
    run_source_action(
        args,
        "add_detail",
        batch_id=args.batch_id,
        rwid=args.rwid,
        detail=Path(args.detail_json),
        source_url=getattr(args, "source_url", None),
        screenshot=Path(args.screenshot) if getattr(args, "screenshot", None) else None,
    )


def source_snapshot_downloads_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        result = source.record_download_baseline(
            layout,
            args.batch_id,
            args.rwid,
            config.download_dir,
        )
    except (source.SourceIntakeError, workspace.WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    relative = result.get("relativePath")
    output = (layout.root / relative).resolve() if isinstance(relative, str) else None
    print(
        json.dumps(
            {
                "status": "captured",
                "path": str(output) if output is not None else None,
                "fileCount": len(result.get("files", [])),
                "fingerprint": result.get("fingerprint"),
                "rwid": result.get("rwid"),
                "projectNo": result.get("projectNo"),
            },
            ensure_ascii=False,
        )
    )


def source_attach_package_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        result = source.attach_package(
            layout,
            batch_id=args.batch_id,
            rwid=args.rwid,
            download_path=Path(args.download),
            original_name=getattr(args, "original_name", None),
            project_no=getattr(args, "project_no", None),
            download_baseline=Path(args.download_baseline),
            allowed_download_dir=config.download_dir,
        )
    except (source.SourceIntakeError, workspace.WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(source_result_summary(result), ensure_ascii=False))


def source_finalize_command(args: argparse.Namespace) -> None:
    run_source_action(args, "finalize_capture", batch_id=args.batch_id)


def add_workspace_resolution_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--work-root",
        help="本次使用的业务工作根；优先于本地 workspace.toml 配置",
    )
    command.add_argument(
        "--workspace-config",
        help=(
            "workspace.toml 路径；默认使用 %%LOCALAPPDATA%%/xf-product-case-registry/workspace.toml"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="消防产品案卷 CaseImportManifestV2 工具")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("input")
    inventory.add_argument("--work-dir", required=True)
    add_workspace_resolution_options(inventory)
    inventory.set_defaults(func=inventory_command, workspace_required=True)
    ocr = sub.add_parser("ocr")
    ocr.add_argument("--work-dir", required=True)
    ocr.add_argument("--output-dir", required=True)
    ocr.add_argument("--relative-path", action="append")
    ocr.add_argument("--timeout", type=int, default=3600)
    add_workspace_resolution_options(ocr)
    ocr.set_defaults(func=ocr_command, workspace_required=True)
    split = sub.add_parser("split")
    split.add_argument("--work-dir", required=True)
    split.add_argument("--plan", required=True)
    add_workspace_resolution_options(split)
    split.set_defaults(func=split_command, workspace_required=True)
    compose = sub.add_parser("compose")
    compose.add_argument("--work-dir", required=True)
    compose.add_argument("--case-data", required=True)
    add_workspace_resolution_options(compose)
    compose.set_defaults(func=compose_command, workspace_required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--upload-map")
    validate.set_defaults(func=validate_command)
    init_auth = sub.add_parser(
        "init-auth-config",
        help="在稳定本地目录创建空认证配置并收紧 Windows ACL",
    )
    init_auth.add_argument(
        "--auth-config",
        default=str(DEFAULT_AUTH_CONFIG),
        help=(
            "本地 TOML 认证配置；默认使用 "
            "%%LOCALAPPDATA%%/xf-product-case-registry/admin-upload-config.toml"
        ),
    )
    init_auth.set_defaults(func=init_auth_config_command)

    workspace = sub.add_parser("workspace", help="配置或检查业务工作根")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_configure = workspace_sub.add_parser("configure", help="写入稳定的本地工作根配置")
    workspace_configure.add_argument("--work-root", required=True)
    workspace_configure.add_argument("--download-dir")
    workspace_configure.add_argument("--workspace-config")
    workspace_configure.set_defaults(func=workspace_configure_command)
    workspace_doctor = workspace_sub.add_parser("doctor", help="只读检查工作根与布局")
    add_workspace_resolution_options(workspace_doctor)
    workspace_doctor.set_defaults(func=workspace_doctor_command)

    source = sub.add_parser("source", help="接收已登录浏览器生成的本地采集物")
    source_sub = source.add_subparsers(dest="source_command", required=True)

    source_begin = source_sub.add_parser("begin", help="创建 BrowserCaptureV1 采集批次")
    add_workspace_resolution_options(source_begin)
    source_begin.add_argument(
        "--filter-json",
        help="可选的筛选条件 JSON；缺省时生成上海时间本年、全部大队默认筛选",
    )
    source_begin.add_argument("--origin", required=True)
    source_begin.add_argument("--batch-id")
    source_begin.add_argument(
        "--acceptance-sample",
        action="store_true",
        help="仅做真实单案下载验收；证据留在批次目录，不进入正式水位或待处理案卷",
    )
    source_begin.set_defaults(func=source_begin_command)

    source_page = source_sub.add_parser("add-page", help="接收一页列表清单")
    add_workspace_resolution_options(source_page)
    source_page.add_argument("--batch-id", required=True)
    source_page.add_argument("--page-json", required=True)
    source_page.add_argument("--screenshot", required=True)
    source_page.add_argument("--round", dest="round_no", type=int, choices=(1, 2, 3))
    source_page.set_defaults(func=source_add_page_command)

    source_detail = source_sub.add_parser("add-detail", help="接收案卷详情与本地核查截图")
    add_workspace_resolution_options(source_detail)
    source_detail.add_argument("--batch-id", required=True)
    source_detail.add_argument("--rwid", "--record-key", dest="rwid", required=True)
    source_detail.add_argument("--detail-json", required=True)
    source_detail.add_argument("--source-url", required=True)
    source_detail.add_argument("--screenshot", required=True)
    source_detail.set_defaults(func=source_add_detail_command)

    source_snapshot = source_sub.add_parser(
        "snapshot-downloads",
        help="在每个案卷点击打包前保存本次下载基线",
    )
    add_workspace_resolution_options(source_snapshot)
    source_snapshot.add_argument("--batch-id", required=True)
    source_snapshot.add_argument("--rwid", "--record-key", dest="rwid", required=True)
    source_snapshot.set_defaults(func=source_snapshot_downloads_command)

    source_package = source_sub.add_parser("attach-package", help="校验、规范命名并绑定已下载 ZIP")
    add_workspace_resolution_options(source_package)
    source_package.add_argument("--batch-id", required=True)
    source_package.add_argument("--rwid", "--record-key", dest="rwid", required=True)
    source_package.add_argument("--download", required=True)
    source_package.add_argument("--original-name")
    source_package.add_argument("--project-no")
    source_package.add_argument(
        "--download-baseline",
        required=True,
        help="本案点击打包前由 source snapshot-downloads 生成的下载基线 JSON",
    )
    source_package.set_defaults(func=source_attach_package_command)

    source_finalize = source_sub.add_parser("finalize", help="收口当前清单扫描轮次或批次进度")
    add_workspace_resolution_options(source_finalize)
    source_finalize.add_argument("--batch-id", required=True)
    source_finalize.set_defaults(func=source_finalize_command)

    ledger = sub.add_parser("ledger", help="管理全局案卷水位表")
    ledger_sub = ledger.add_subparsers(dest="ledger_command", required=True)
    ledger_export = ledger_sub.add_parser("export", help="从 JSON 事实源重建 Excel 水位表")
    add_workspace_resolution_options(ledger_export)
    ledger_export.set_defaults(func=ledger_export_command)

    for name, func in (("upload", upload_command), ("verify", verify_command)):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True)
        command.add_argument("--upload-map", required=True)
        command.add_argument("--api-base", required=True)
        command.add_argument(
            "--auth-config",
            default=str(DEFAULT_AUTH_CONFIG),
            help=(
                "本地 TOML 认证配置；默认使用 "
                "%%LOCALAPPDATA%%/xf-product-case-registry/admin-upload-config.toml"
            ),
        )
        command.add_argument("--timeout", type=float, default=60.0)
        command.add_argument(
            "--deep-content-verify",
            action="store_true",
            help="显式取回飞牛正文并下载校验内容 SHA-256（默认只核对目录和飞牛落盘证据）",
        )
        add_workspace_resolution_options(command)
        command.add_argument(
            "--no-archive",
            action="store_true",
            help="核验成功后不自动收口本地工作目录",
        )
        command.set_defaults(func=func)
        if name == "upload":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--finalize", action="store_true")
            command.set_defaults(workspace_required=True)
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
