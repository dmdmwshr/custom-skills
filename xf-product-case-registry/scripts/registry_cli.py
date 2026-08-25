from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import mimetypes
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader, PdfWriter

VERSION = "1.6.3"
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
SUPPLEMENT_SCHEMA_PATH = SKILL_ROOT / "references" / "CaseFileSupplementManifestV1.schema.json"
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
GENERAL_REQUEST_MIN_INTERVAL_SECONDS = 1.05
RATE_LIMIT_MAX_RETRIES = 8
RATE_LIMIT_MAX_AUTO_WAIT_SECONDS = 60.0
RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS = 60.0
RATE_LIMIT_JITTER_SECONDS = (0.1, 0.35)
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


def validate_supplement_manifest(
    manifest: dict[str, Any], upload_map: dict[str, Any] | None = None
) -> list[str]:
    """Validate the published missing-file supplement contract without network I/O."""

    errors: list[str] = []
    root = only_keys(
        manifest,
        {
            "schemaVersion",
            "projectNo",
            "baseSnapshotDigest",
            "mode",
            "packageSha256",
            "createdAt",
            "extractor",
            "files",
            "documentSlots",
        },
        "supplement",
        errors,
    )
    if root.get("schemaVersion") != "CaseFileSupplementManifestV1":
        errors.append("supplement.schemaVersion 必须为 CaseFileSupplementManifestV1")
    if not isinstance(root.get("projectNo"), str) or not PROJECT_NO.fullmatch(
        root["projectNo"]
    ):
        errors.append("supplement.projectNo 不合法")
    if not isinstance(root.get("baseSnapshotDigest"), str) or not SHA256.fullmatch(
        root["baseSnapshotDigest"]
    ):
        errors.append("supplement.baseSnapshotDigest 不合法")
    if root.get("mode") != "MISSING_ONLY":
        errors.append("supplement.mode 仅支持 MISSING_ONLY")
    if "packageSha256" in root and (
        not isinstance(root["packageSha256"], str)
        or not SHA256.fullmatch(root["packageSha256"])
    ):
        errors.append("supplement.packageSha256 不合法")
    try:
        datetime.fromisoformat(str(root.get("createdAt")).replace("Z", "+00:00"))
    except ValueError:
        errors.append("supplement.createdAt 必须为 ISO 时间")
    if "extractor" in root:
        extractor = only_keys(
            root["extractor"], {"name", "version"}, "supplement.extractor", errors
        )
        if (
            not text(extractor.get("name"))
            or not extractor["name"].strip()
            or not text(extractor.get("version"))
            or not extractor["version"].strip()
        ):
            errors.append("supplement.extractor.name/version 必须为非空文本")

    files = root.get("files") if isinstance(root.get("files"), list) else []
    if not isinstance(root.get("files"), list) or not files:
        errors.append("supplement.files 必须是非空数组")
    refs: set[str] = set()
    paths: set[str] = set()
    for item in files:
        file = only_keys(
            item,
            {"clientRef", "relativePath", "sha256", "mimeType", "pageCount"},
            "supplement.file",
            errors,
        )
        file_ref, relative_path = file.get("clientRef"), file.get("relativePath")
        if not ref(file_ref) or file_ref in refs:
            errors.append("supplement 文件 clientRef 重复或不合法")
        else:
            refs.add(file_ref)
        try:
            if text(relative_path) and ("\\" in relative_path or "//" in relative_path):
                raise RegistryError("路径包含不允许的分隔符")
            normalized_path = safe_relative(relative_path) if text(relative_path) else ""
        except RegistryError:
            normalized_path = ""
        if (
            not normalized_path
            or not normalized_path.lower().endswith(".pdf")
            or normalized_path in paths
        ):
            errors.append("supplement 文件 relativePath 必须唯一安全 PDF")
        else:
            paths.add(normalized_path)
        if not isinstance(file.get("sha256"), str) or not SHA256.fullmatch(file["sha256"]):
            errors.append("supplement 文件 sha256 不合法")
        if file.get("mimeType") != "application/pdf":
            errors.append("supplement 文件 mimeType 必须为 application/pdf")
        if "pageCount" in file and (
            not isinstance(file["pageCount"], int)
            or isinstance(file["pageCount"], bool)
            or file["pageCount"] < 1
        ):
            errors.append("supplement 文件 pageCount 不合法")

    slots = root.get("documentSlots") if isinstance(root.get("documentSlots"), list) else []
    if not isinstance(root.get("documentSlots"), list) or not slots:
        errors.append("supplement.documentSlots 必须是非空数组")
    slot_refs: set[str] = set()
    slot_identities: set[tuple[str, str]] = set()
    used_refs: set[str] = set()
    for item in slots:
        slot = only_keys(
            item,
            {"clientRef", "slotCode", "ownerKey", "versions"},
            "supplement.documentSlot",
            errors,
        )
        slot_ref, code, owner_key = (
            slot.get("clientRef"),
            slot.get("slotCode"),
            slot.get("ownerKey"),
        )
        if not ref(slot_ref) or slot_ref in slot_refs:
            errors.append("supplement 槽位 clientRef 重复或不合法")
        else:
            slot_refs.add(slot_ref)
        if code not in SLOT_META or code == "OTHER_ATTACHMENT":
            errors.append("supplement slotCode 不在固定文书槽位中")
            continue
        multiplicity, stage = SLOT_META[code]
        owner_valid = False
        if multiplicity == "CASE":
            owner_valid = owner_key == "case"
        elif multiplicity == "INSPECTION":
            owner_valid = owner_key == f"inspection:{stage}"
        elif multiplicity == "PRODUCT":
            owner_valid = (
                isinstance(owner_key, str)
                and owner_key.startswith("product:")
                and bool(UUID.fullmatch(owner_key.removeprefix("product:")))
            )
        elif multiplicity == "NOTIFICATION_TARGET":
            owner_valid = owner_key in {"notification:PRODUCTION", "notification:SALES"}
        if not owner_valid:
            errors.append("supplement 槽位 ownerKey 与槽位类型不匹配")
        if isinstance(code, str) and isinstance(owner_key, str):
            identity = (code, owner_key)
            if identity in slot_identities:
                errors.append("supplement 同一逻辑槽位重复")
            slot_identities.add(identity)
        versions = slot.get("versions")
        if not isinstance(versions, list) or not 1 <= len(versions) <= 2:
            errors.append("supplement 槽位 versions 必须有 1 至 2 项")
            continue
        kinds: set[str] = set()
        for item_version in versions:
            version = only_keys(
                item_version, {"kind", "fileRef"}, "supplement.version", errors
            )
            kind, file_ref = version.get("kind"), version.get("fileRef")
            if kind not in VERSIONS or kind in kinds:
                errors.append("supplement 文件版本重复或不合法")
            kinds.add(kind)
            if file_ref not in refs:
                errors.append("supplement 版本引用未知文件")
            elif file_ref in used_refs:
                errors.append("supplement 同一 fileRef 不得复用")
            else:
                used_refs.add(file_ref)
        if code in PHOTO_SLOTS and kinds != {"SCANNED"}:
            errors.append("supplement 现场照片仅允许一个 SCANNED 版本")
    errors.extend(f"supplement 文件 {item} 未被引用" for item in refs - used_refs)

    if upload_map is not None:
        if set(upload_map) != refs:
            errors.append("supplement-upload-map 必须精确覆盖 supplement.files")
        for file in files:
            file_ref = file.get("clientRef")
            local = upload_map.get(file_ref) if isinstance(file_ref, str) else None
            try:
                sha256, pages = pdf_info(Path(local)) if text(local) else ("", 0)
            except RegistryError:
                sha256, pages = "", 0
            if sha256 != file.get("sha256") or (
                "pageCount" in file and pages != file["pageCount"]
            ):
                errors.append(f"supplement {file_ref} 本地 PDF/哈希/页数不匹配")
    return errors


def validate_case_import_state(snapshot: dict[str, Any], project_no: str) -> None:
    if (
        snapshot.get("schemaVersion") != "CaseImportStateV1"
        or snapshot.get("projectNo") != project_no
        or not isinstance(snapshot.get("snapshotDigest"), str)
        or not SHA256.fullmatch(snapshot["snapshotDigest"])
        or not isinstance(snapshot.get("case"), dict)
        or not isinstance(snapshot.get("inspections"), list)
        or not isinstance(snapshot.get("documentSlots"), list)
        or not isinstance(snapshot.get("unresolvedConflicts"), list)
    ):
        raise RegistryError("服务器 CaseImportStateV1 快照字段不完整或项目编号不一致")
    brigade = snapshot["case"].get("brigade")
    if not isinstance(brigade, dict) or brigade.get("code") not in BRIGADES:
        raise RegistryError("服务器案卷快照缺少可信大队编号")
    product_owners: set[str] = set()
    for inspection in snapshot["inspections"]:
        if not isinstance(inspection, dict) or not isinstance(inspection.get("products"), list):
            raise RegistryError("服务器案卷快照检查或产品投影无效")
        for product in inspection["products"]:
            owner_key = product.get("ownerKey") if isinstance(product, dict) else None
            client_refs = product.get("clientRefs") if isinstance(product, dict) else None
            if (
                not isinstance(owner_key, str)
                or not owner_key.startswith("product:")
                or not UUID.fullmatch(owner_key.removeprefix("product:"))
                or owner_key in product_owners
                or not isinstance(client_refs, list)
                or any(not isinstance(item, str) for item in client_refs)
            ):
                raise RegistryError("服务器案卷快照产品 ownerKey/clientRefs 无效")
            product_owners.add(owner_key)
    identities: set[tuple[str, str]] = set()
    for slot in snapshot["documentSlots"]:
        if not isinstance(slot, dict) or not isinstance(slot.get("files"), list):
            raise RegistryError("服务器案卷快照槽位投影无效")
        code, owner_key = slot.get("slotCode"), slot.get("ownerKey")
        if not isinstance(code, str) or not isinstance(owner_key, str):
            raise RegistryError("服务器案卷快照槽位身份无效")
        identity = (code, owner_key)
        if identity in identities:
            raise RegistryError("服务器案卷快照存在重复逻辑槽位")
        identities.add(identity)
        kinds: set[str] = set()
        for file in slot["files"]:
            kind = file.get("versionKind") if isinstance(file, dict) else None
            sha256 = file.get("sha256") if isinstance(file, dict) else None
            if kind not in VERSIONS or kind in kinds or not isinstance(sha256, str):
                raise RegistryError("服务器案卷快照槽位文件版本无效")
            if not SHA256.fullmatch(sha256):
                raise RegistryError("服务器案卷快照槽位文件哈希无效")
            kinds.add(kind)


def supplement_owner_key(
    slot: dict[str, Any], full_manifest: dict[str, Any], snapshot: dict[str, Any]
) -> str:
    code = slot.get("slotCode")
    if code not in SLOT_META or code == "OTHER_ATTACHMENT":
        raise RegistryError("补录只支持固定文书槽位")
    multiplicity, stage = SLOT_META[code]
    if multiplicity == "CASE":
        return "case"
    if multiplicity == "INSPECTION":
        inspection_ref = slot.get("inspectionRef")
        inspections = [
            item
            for key in ("initialInspection", "recheckInspection")
            if isinstance((item := full_manifest.get(key)), dict)
        ]
        matches = [item for item in inspections if item.get("clientRef") == inspection_ref]
        if len(matches) != 1 or matches[0].get("stage") != stage:
            raise RegistryError("本地检查级槽位无法映射到唯一检查阶段")
        return f"inspection:{stage}"
    if multiplicity == "NOTIFICATION_TARGET":
        target = slot.get("notificationTarget")
        if target not in {"PRODUCTION", "SALES"}:
            raise RegistryError("本地通报槽位缺少明确生产或销售对象")
        return f"notification:{target}"
    product_ref = slot.get("productRef")
    candidates = [
        product.get("ownerKey")
        for inspection in snapshot["inspections"]
        for product in inspection.get("products", [])
        if isinstance(product, dict) and product_ref in product.get("clientRefs", [])
    ]
    if len(candidates) != 1 or not isinstance(candidates[0], str):
        raise RegistryError("本地产品级槽位无法通过 clientRef 映射到唯一服务器产品")
    return candidates[0]


def build_supplement_manifest(
    full_manifest: dict[str, Any],
    full_upload_map: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, int]]:
    """Project the full local manifest onto server-confirmed missing slot versions only."""

    project_no = full_manifest.get("case", {}).get("projectNo")
    validate_case_import_state(snapshot, project_no)
    files_by_ref = {
        item["clientRef"]: item
        for item in full_manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("clientRef"), str)
    }
    remote_slots = {
        (slot["slotCode"], slot["ownerKey"]): slot for slot in snapshot["documentSlots"]
    }
    open_conflicts = {
        item.get("path")
        for item in snapshot["unresolvedConflicts"]
        if isinstance(item, dict)
        and item.get("category") == "DOCUMENT_SLOT"
        and isinstance(item.get("path"), str)
    }
    selected_slots: list[dict[str, Any]] = []
    selected_refs: set[str] = set()
    already_present = 0
    for local_slot in full_manifest.get("documentSlots", []):
        if not isinstance(local_slot, dict):
            continue
        owner_key = supplement_owner_key(local_slot, full_manifest, snapshot)
        remote_slot = remote_slots.get((local_slot["slotCode"], owner_key))
        remote_versions = {
            item["versionKind"]: item
            for item in (remote_slot.get("files", []) if remote_slot else [])
            if isinstance(item, dict) and item.get("versionKind") in VERSIONS
        }
        local_kinds = {
            version.get("kind")
            for version in local_slot.get("versions", [])
            if isinstance(version, dict)
        }
        unexpected_kinds = set(remote_versions) - local_kinds
        if unexpected_kinds:
            raise RegistryError("正式槽位包含本地清单未声明的文件版本，缺失补录已停止")
        missing_versions: list[dict[str, str]] = []
        for version in local_slot.get("versions", []):
            if not isinstance(version, dict):
                continue
            kind, file_ref = version.get("kind"), version.get("fileRef")
            local_file = files_by_ref.get(file_ref)
            if kind not in VERSIONS or not isinstance(local_file, dict):
                raise RegistryError("本地槽位版本或文件引用无效")
            remote_file = remote_versions.get(kind)
            conflict_path = f"slot:{local_slot['clientRef']}.{kind}"
            if remote_file:
                if remote_file.get("sha256") != local_file.get("sha256"):
                    raise RegistryError("正式槽位已有不同哈希文件，MISSING_ONLY 禁止覆盖")
                already_present += 1
                continue
            if conflict_path not in open_conflicts:
                raise RegistryError("缺失槽位版本没有对应的未解决历史冲突，停止自动补录")
            missing_versions.append({"kind": kind, "fileRef": file_ref})
            selected_refs.add(file_ref)
        if missing_versions:
            selected_slots.append(
                {
                    "clientRef": local_slot["clientRef"],
                    "slotCode": local_slot["slotCode"],
                    "ownerKey": owner_key,
                    "versions": missing_versions,
                }
            )
    if not selected_slots:
        unresolved_local = {
            f"slot:{slot['clientRef']}.{version['kind']}"
            for slot in full_manifest.get("documentSlots", [])
            if isinstance(slot, dict)
            for version in slot.get("versions", [])
            if isinstance(version, dict)
        } & open_conflicts
        if unresolved_local:
            raise RegistryError("文件已存在但历史槽位冲突仍未解决，停止自动收口")
        return None, {}, {"missingVersions": 0, "alreadyPresentVersions": already_present}
    selected_files = [
        item for item in full_manifest["files"] if item.get("clientRef") in selected_refs
    ]
    selected_upload = {
        file_ref: full_upload_map[file_ref]
        for file_ref in sorted(selected_refs)
        if file_ref in full_upload_map
    }
    supplement = {
        "schemaVersion": "CaseFileSupplementManifestV1",
        "projectNo": project_no,
        "baseSnapshotDigest": snapshot["snapshotDigest"],
        "mode": "MISSING_ONLY",
        "createdAt": utc_now(),
        "extractor": {"name": "xf-product-case-registry", "version": VERSION},
        "files": selected_files,
        "documentSlots": selected_slots,
    }
    errors = validate_supplement_manifest(supplement, selected_upload)
    if errors:
        raise RegistryError("补录清单校验失败：\n- " + "\n- ".join(errors))
    return supplement, selected_upload, {
        "missingVersions": len(selected_files),
        "missingSlots": len(selected_slots),
        "alreadyPresentVersions": already_present,
    }


def load_supplement_inputs(
    manifest_path: Path, upload_map_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(manifest_path)
    upload_map = read_json(upload_map_path).get("files")
    if not isinstance(upload_map, dict):
        raise RegistryError("supplement-upload-map.files 必须是对象")
    errors = validate_supplement_manifest(manifest, upload_map)
    if errors:
        raise RegistryError("补录状态文件校验失败：\n- " + "\n- ".join(errors))
    return manifest, upload_map


def api_request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    request_class: str = "general",
    retry_on_429: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Apply the published API pacing and machine-readable 429 retry contract."""

    if request_class not in {"general", "upload"}:
        raise RegistryError("未知 API 限流类别")
    transport = getattr(client, "_transport", None)
    pacing_enabled = not isinstance(transport, httpx.MockTransport) or bool(
        getattr(client, "_xfpcr_force_pacing", False)
    )
    total_retry_wait = 0.0
    attempts = 0
    while True:
        if request_class == "general" and pacing_enabled:
            last_started = getattr(client, "_xfpcr_last_general_request_started", None)
            now = time.monotonic()
            if isinstance(last_started, (int, float)):
                pacing_wait = GENERAL_REQUEST_MIN_INTERVAL_SECONDS - (now - last_started)
                if pacing_wait > 0:
                    time.sleep(pacing_wait)
            client._xfpcr_last_general_request_started = time.monotonic()  # type: ignore[attr-defined]
        response = client.request(method.upper(), url, **kwargs)
        if response.status_code != 429 or not retry_on_429:
            return response
        retry_after = response.headers.get("retry-after", "").strip()
        if not re.fullmatch(r"[1-9][0-9]{0,5}", retry_after):
            return response
        delay = float(retry_after)
        jitter = random.uniform(*RATE_LIMIT_JITTER_SECONDS)
        wait_seconds = delay + jitter
        if (
            attempts >= RATE_LIMIT_MAX_RETRIES
            or delay > RATE_LIMIT_MAX_AUTO_WAIT_SECONDS
            or total_retry_wait + wait_seconds > RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS
        ):
            return response
        response.close()
        time.sleep(wait_seconds)
        total_retry_wait += wait_seconds
        attempts += 1


def response_json(response: httpx.Response, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "").strip()
            wait_hint = (
                f"，请等待 {retry_after} 秒后重试"
                if re.fullmatch(r"[1-9][0-9]{0,5}", retry_after)
                else "，请按服务端提示稍后重试"
            )
            raise RegistryError(
                f"{label} 触发登记系统限流{wait_hint}；"
                "多案上传必须使用 upload-batch 共用一次登录会话"
            )
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
        api_request(
            client,
            "POST",
            f"{api_base}/api/auth/login",
            headers={"Origin": origin, WRITE_HEADER: WRITE_HEADER_VALUE},
            json={"username": username, "password": password},
        ),
        "登录",
    )
    session = response_json(
        api_request(client, "GET", f"{api_base}/api/auth/session"), "读取登录会话"
    )
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


def require_identity_scope(identity: dict[str, str | None], manifest: dict[str, Any]) -> None:
    brigade_code = manifest["case"]["brigadeCode"]
    if identity.get("role") == "BRIGADE" and identity.get("brigadeCode") != brigade_code:
        raise RegistryError("当前大队账户与 manifest 的 brigadeCode 不一致")


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
        api_request(
            client,
            "GET",
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
        job = response_json(
            api_request(client, "GET", f"{api_base}/api/v2/import-jobs/{job_id}"),
            "读取导入任务",
        )
    except RegistryError as error:
        if "HTTP 404" in str(error):
            raise RegistryError("服务端导入任务不存在；不得猜测或重建任务") from error
        raise
    if job.get("id") != job_id:
        raise RegistryError("服务端导入任务 id 对账失败")
    if job.get("packageHash") != package_sha:
        raise RegistryError("服务端导入任务包哈希对账失败")
    if job.get("projectNo") is not None and job.get("projectNo") != project_no:
        raise RegistryError("服务端导入任务项目编号对账失败")
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


SUPPLEMENT_STATE_BASE_KEYS = {
    "stateVersion",
    "status",
    "origin",
    "sourceManifestSha256",
    "supplementManifestSha256",
    "baseSnapshotDigest",
    "projectNo",
    "brigadeCode",
    "jobId",
    "authIdentity",
    "filesProjection",
    "uploadedFileRefs",
}
SUPPLEMENT_STATE_OPTIONAL_KEYS = {
    "caseId",
    "finalizedAt",
    "finalizeSummary",
    "verification",
    "verifiedAt",
}


def validate_supplement_state(state: dict[str, Any]) -> None:
    extra = set(state) - SUPPLEMENT_STATE_BASE_KEYS - SUPPLEMENT_STATE_OPTIONAL_KEYS
    missing = SUPPLEMENT_STATE_BASE_KEYS - set(state)
    if extra or missing or state.get("stateVersion") != 1:
        raise RegistryError("supplement-state V1 字段不完整或包含额外字段")
    status = state.get("status")
    if status not in {
        "UPLOADING",
        "FINALIZED_UNVERIFIED",
        "FINALIZED_WITH_CONFLICTS",
        "VERIFIED",
    }:
        raise RegistryError("supplement-state V1 状态无效")
    if (
        not isinstance(state.get("origin"), str)
        or not SHA256.fullmatch(str(state.get("sourceManifestSha256")))
        or not SHA256.fullmatch(str(state.get("supplementManifestSha256")))
        or not SHA256.fullmatch(str(state.get("baseSnapshotDigest")))
        or not isinstance(state.get("projectNo"), str)
        or not PROJECT_NO.fullmatch(state["projectNo"])
        or state.get("brigadeCode") not in BRIGADES
        or not isinstance(state.get("jobId"), str)
        or not state["jobId"]
        or not isinstance(state.get("filesProjection"), list)
        or not state["filesProjection"]
        or not isinstance(state.get("uploadedFileRefs"), list)
    ):
        raise RegistryError("supplement-state V1 基础字段无效")
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
            raise RegistryError("supplement-state V1 文件投影无效")
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
            raise RegistryError("supplement-state V1 文件投影无效")
        projection_refs.add(item["clientRef"])
    uploaded_refs = state["uploadedFileRefs"]
    if (
        any(not isinstance(item, str) for item in uploaded_refs)
        or len(set(uploaded_refs)) != len(uploaded_refs)
        or set(uploaded_refs) - projection_refs
    ):
        raise RegistryError("supplement-state V1 已上传引用无效")
    identity = state.get("authIdentity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"digest", "role", "brigadeCode"}
        or not SHA256.fullmatch(str(identity.get("digest")))
        or identity.get("role") not in {"ADMIN", "BRIGADE"}
        or (identity.get("role") == "ADMIN" and identity.get("brigadeCode") is not None)
        or (identity.get("role") == "BRIGADE" and not identity.get("brigadeCode"))
    ):
        raise RegistryError("supplement-state V1 身份摘要无效")
    if identity.get("role") == "BRIGADE" and identity.get("brigadeCode") != state[
        "brigadeCode"
    ]:
        raise RegistryError("supplement-state V1 大队与身份摘要不一致")
    if status == "UPLOADING" and set(state) != SUPPLEMENT_STATE_BASE_KEYS:
        raise RegistryError("UPLOADING supplement-state 包含完成字段")
    if status != "UPLOADING":
        summary = state.get("finalizeSummary")
        if (
            not isinstance(state.get("caseId"), str)
            or not isinstance(state.get("finalizedAt"), str)
            or not isinstance(summary, dict)
            or set(summary)
            != {"caseId", "addedFiles", "replacedFiles", "conflictCount", "skippedCount"}
            or summary.get("caseId") != state.get("caseId")
        ):
            raise RegistryError("supplement-state V1 终结摘要无效")
        for key in {"addedFiles", "replacedFiles", "conflictCount", "skippedCount"}:
            value = summary[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RegistryError("supplement-state V1 终结计数无效")
        if summary["replacedFiles"] != 0:
            raise RegistryError("MISSING_ONLY 补录不得替换正式文件")
        if status in {"FINALIZED_UNVERIFIED", "VERIFIED"} and summary["conflictCount"]:
            raise RegistryError("补录存在冲突时不得进入待核验或 VERIFIED")
    if status == "VERIFIED":
        verification = state.get("verification")
        if (
            not isinstance(verification, dict)
            or set(verification) != {"caseId", "inspections", "products", "filesVerified"}
            or verification.get("caseId") != state.get("caseId")
            or not isinstance(state.get("verifiedAt"), str)
        ):
            raise RegistryError("supplement-state V1 核验摘要无效")
    elif "verification" in state or "verifiedAt" in state:
        raise RegistryError("未 VERIFIED 的 supplement-state 不得包含核验字段")


def get_supplement_job(
    client: httpx.Client,
    api_base: str,
    job_id: str,
    project_no: str,
    base_snapshot_digest: str,
    brigade_code: str,
) -> dict[str, Any]:
    try:
        job = response_json(
            api_request(client, "GET", f"{api_base}/api/v2/import-jobs/{job_id}"),
            "读取补录任务",
        )
    except RegistryError as error:
        if "HTTP 404" in str(error):
            raise RegistryError("服务端补录任务不存在；不得猜测或另建任务") from error
        raise
    if (
        job.get("id") != job_id
        or job.get("mode") != "SUPPLEMENT_EXISTING"
        or job.get("projectNo") != project_no
        or job.get("baseSnapshotDigest") != base_snapshot_digest
        or job.get("packageHash") not in {None, ""}
    ):
        raise RegistryError("服务端补录任务模式、项目或快照对账失败")
    status = job.get("status")
    if status == "FAILED":
        raise RegistryError("服务端补录任务已 FAILED，停止续传并保留断点")
    if status not in {"CREATED", "UPLOADING", "MANIFEST_RECEIVED", "FINALIZED"}:
        raise RegistryError("服务端补录任务状态不受支持")
    if status == "FINALIZED":
        case = job.get("case")
        brigade = case.get("brigade") if isinstance(case, dict) else None
        route_path = brigade.get("routePath") if isinstance(brigade, dict) else None
        if (
            not isinstance(case, dict)
            or case.get("projectNo") != project_no
            or not isinstance(route_path, str)
            or route_path.strip("/").upper() != brigade_code
            or not isinstance(job.get("finalizedAt"), str)
            or not isinstance(job.get("resultSummary"), dict)
        ):
            raise RegistryError("服务端已终结补录任务的案卷归属或摘要无效")
    return job


def finalized_supplement_summary(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") != "FINALIZED" or not isinstance(job.get("resultSummary"), dict):
        raise RegistryError("服务端补录任务尚未终结")
    result = job["resultSummary"]
    added, replaced = result.get("added"), result.get("replaced")
    conflicts, skipped = result.get("conflicts"), result.get("skipped")
    if (
        not isinstance(result.get("caseId"), str)
        or result.get("created") is not False
        or result.get("mode") != "SUPPLEMENT_EXISTING"
        or result.get("policy") != "MISSING_ONLY"
        or not isinstance(added, dict)
        or not isinstance(replaced, dict)
        or not isinstance(conflicts, list)
        or not isinstance(skipped, list)
    ):
        raise RegistryError("补录 finalize 响应缺少受支持的摘要字段")
    added_files, replaced_files = added.get("files"), replaced.get("files")
    if (
        not isinstance(added_files, int)
        or isinstance(added_files, bool)
        or added_files < 0
        or replaced_files != 0
    ):
        raise RegistryError("补录 finalize 文件计数无效或发生了禁止的替换")
    return {
        "caseId": result["caseId"],
        "addedFiles": added_files,
        "replacedFiles": 0,
        "conflictCount": len(conflicts),
        "skippedCount": len(skipped),
    }


def close_original_upload_state_after_supplement(
    state_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    upload_map: dict[str, Any],
    origin: str,
    identity: dict[str, str | None],
    verification: dict[str, Any],
    finalized_at: str,
) -> dict[str, Any]:
    if not state_path.exists():
        raise RegistryError("缺少原始 upload-state，不能证明历史冲突案卷的补录来源")
    state = read_json(state_path)
    validate_upload_state(state)
    if state["status"] == "VERIFIED":
        return state
    if state["status"] != "FINALIZED_WITH_CONFLICTS":
        raise RegistryError("只有 FINALIZED_WITH_CONFLICTS 案卷允许自动补录收口")
    summary = state.get("finalizeSummary")
    if not isinstance(summary, dict) or summary.get("created") is not True:
        raise RegistryError("原始任务未证明新建案卷成功，禁止自动补录")
    projection = files_projection(manifest, upload_map)
    if (
        state.get("origin") != origin
        or state.get("manifestSha256") != file_sha256(manifest_path)
        or state.get("packageSha256") != manifest.get("packageSha256")
        or state.get("projectNo") != manifest.get("case", {}).get("projectNo")
        or state.get("brigadeCode") != manifest.get("case", {}).get("brigadeCode")
        or state.get("filesProjection") != projection
        or state.get("immutableBindingDigest") != immutable_manifest_binding(manifest, projection)
        or state.get("caseId") != verification.get("caseId")
    ):
        raise RegistryError("补录核验成功，但原始 upload-state 与当前案卷清单不一致")
    require_same_state_identity(state, identity)
    state.pop("finalizeSummary", None)
    state.update(
        {
            "status": "VERIFIED",
            "finalizedAt": finalized_at,
            "verification": verification,
            "verifiedAt": utc_now(),
        }
    )
    validate_upload_state(state)
    write_json(state_path, state)
    return state


def reconcile_uploaded_file_refs(
    state: dict[str, Any],
    job: dict[str, Any],
    projection: list[dict[str, Any]],
) -> None:
    """Reconcile resumable progress without re-sending an already accepted PDF."""

    expected_by_path = {item["relativePath"]: item for item in projection}
    expected_refs = {item["clientRef"] for item in projection}
    server_field = None
    for candidate in ("receivedFiles", "files", "uploadedFiles"):
        if candidate in job:
            if server_field is not None:
                raise RegistryError("服务端导入任务同时返回多个文件投影字段，停止续传")
            server_field = candidate
    if server_field is None:
        if job.get("status") == "MANIFEST_RECEIVED":
            # The manifest endpoint accepts only a complete file graph. On an older
            # server that does not expose its file projection, this terminal pre-
            # finalize state is sufficient evidence that every expected PDF exists.
            state["uploadedFileRefs"] = sorted(expected_refs)
        return

    server_files = job.get(server_field)
    if not isinstance(server_files, list):
        raise RegistryError("服务端导入任务文件投影不是数组，停止续传")
    server_refs: set[str] = set()
    seen_paths: set[str] = set()
    for server_item in server_files:
        if not isinstance(server_item, dict):
            raise RegistryError("服务端导入任务文件投影包含无效条目，停止续传")
        relative_path = server_item.get("relativePath")
        if not isinstance(relative_path, str) or relative_path in seen_paths:
            raise RegistryError("服务端导入任务文件路径无效或重复，停止续传")
        seen_paths.add(relative_path)
        expected = expected_by_path.get(relative_path)
        raw_size = server_item.get("sizeBytes")
        if isinstance(raw_size, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)", raw_size):
            size_bytes = int(raw_size)
        elif isinstance(raw_size, int) and not isinstance(raw_size, bool):
            size_bytes = raw_size
        else:
            raise RegistryError("服务端导入任务文件大小无效，停止续传")
        if (
            expected is None
            or server_item.get("sha256") != expected["sha256"]
            or (
                server_field != "receivedFiles"
                and server_item.get("mimeType") != expected["mimeType"]
            )
            or (
                server_field == "receivedFiles"
                and server_item.get("mimeType") is not None
                and server_item.get("mimeType") != expected["mimeType"]
            )
            or size_bytes != expected["sizeBytes"]
        ):
            raise RegistryError("服务端导入任务文件投影与本地规范 PDF 不一致，停止续传")
        server_refs.add(expected["clientRef"])

    local_refs = set(state["uploadedFileRefs"])
    if local_refs - server_refs:
        raise RegistryError("本地记录为已上传的 PDF 在服务端文件投影中缺失，停止续传")
    if job.get("status") == "MANIFEST_RECEIVED" and server_refs != expected_refs:
        raise RegistryError("服务端已接收清单但文件投影不完整，停止终结")
    state["uploadedFileRefs"] = sorted(server_refs)


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
            response = api_request(
                client,
                "POST",
                f"{api_base}/api/v2/files/{file_id}/recall",
                headers=write_headers,
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
            response = api_request(
                client,
                "GET",
                f"{api_base}/api/v2/file-recalls/{projection['recallId']}",
            )
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
    detail = response_json(
        api_request(client, "GET", f"{api_base}/api/v2/cases/{case_id}"), "读取详情"
    )
    directory = response_json(
        api_request(client, "GET", f"{api_base}/api/v2/cases/{case_id}/directory"),
        "读取目录",
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
    shared_session = getattr(args, "_shared_session", None)
    if shared_session is None:
        client_scope = httpx.Client(
            timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
            follow_redirects=False,
        )
    elif (
        not isinstance(shared_session, dict)
        or shared_session.get("apiBase") != api_base
        or shared_session.get("origin") != origin
        or not isinstance(shared_session.get("client"), httpx.Client)
        or not isinstance(shared_session.get("identity"), dict)
        or not isinstance(shared_session.get("writeHeaders"), dict)
    ):
        raise RegistryError("批量上传共享会话与当前目标不一致")
    else:
        client_scope = nullcontext(shared_session["client"])
    with client_scope as client:
        if shared_session is None:
            identity, write_headers = authenticate_client(
                client,
                api_base,
                origin,
                manifest,
                secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
            )
            if (
                response_json(
                    api_request(client, "GET", f"{api_base}/api/ready"), "服务就绪"
                ).get("status")
                != "ready"
            ):
                raise RegistryError("服务未就绪")
        else:
            identity = shared_session["identity"]
            write_headers = shared_session["writeHeaders"]
            require_identity_scope(identity, manifest)
        require_same_state_identity(state, identity)
        job_id: str
        job_status = "CREATED"
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
            job_status = job["status"]
            reconcile_uploaded_file_refs(state, job, projection)
            state["manifestSha256"] = manifest_sha
        else:
            existing = exact_case(client, api_base, manifest["case"]["projectNo"])
            if existing:
                raise RegistryError("目标项目编号已存在；为防覆盖人工数据已停止")
            idempotency_key = f"xfpcr-v2-{package_sha[7:]}"
            job = response_json(
                api_request(
                    client,
                    "POST",
                    f"{api_base}/api/v2/import-jobs",
                    headers={
                        **write_headers,
                        "Idempotency-Key": idempotency_key,
                    },
                    json={
                        "idempotencyKey": idempotency_key,
                        "packageSha256": package_sha,
                        "projectNo": manifest["case"]["projectNo"],
                    },
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
            job_status = job["status"]
        validate_upload_state(state)
        write_json(state_path, state)
        mark_uploading(args, path, manifest)
        projection_by_ref = {item["clientRef"]: item for item in projection}
        for item in manifest["files"]:
            if item["clientRef"] in state["uploadedFileRefs"]:
                continue
            projection_item = projection_by_ref[item["clientRef"]]
            with Path(upload[item["clientRef"]]).open("rb") as stream:
                uploaded_response = response_json(
                    api_request(
                        client,
                        "POST",
                        f"{api_base}/api/v2/import-jobs/{job_id}/files",
                        request_class="upload",
                        retry_on_429=False,
                        headers=write_headers,
                        params={"relativePath": item["relativePath"]},
                        files={"file": (Path(stream.name).name, stream, "application/pdf")},
                    ),
                    "上传 PDF",
                )
            check_uploaded_file_response(uploaded_response, projection_item, job_id)
            state["uploadedFileRefs"] = sorted({*state["uploadedFileRefs"], item["clientRef"]})
            write_json(state_path, state)
        expected_refs = {item["clientRef"] for item in projection}
        if set(state["uploadedFileRefs"]) != expected_refs:
            raise RegistryError("本地上传进度未覆盖全部规范 PDF，停止提交清单")
        if job_status != "MANIFEST_RECEIVED":
            manifest_response = response_json(
                api_request(
                    client,
                    "PUT",
                    f"{api_base}/api/v2/import-jobs/{job_id}/manifest",
                    headers=write_headers,
                    json=manifest,
                ),
                "提交清单",
            )
            check_manifest_response(manifest_response, job_id)
        response_json(
            api_request(
                client,
                "POST",
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


def supplement_artifact_paths(manifest_path: Path) -> tuple[Path, Path, Path]:
    return (
        manifest_path.parent / "supplement-manifest.json",
        manifest_path.parent / "supplement-upload-map.json",
        manifest_path.parent / "supplement-state.json",
    )


def supplement_case(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path, full_manifest, full_upload = load_inputs(args.manifest, args.upload_map)
    enforce_upload_workspace_preflight(args, manifest_path, full_manifest)
    project_no = full_manifest["case"]["projectNo"]
    brigade_code = full_manifest["case"]["brigadeCode"]
    source_manifest_sha = file_sha256(manifest_path)
    main_state_path = manifest_path.parent / "upload-state.json"
    if not main_state_path.exists():
        raise RegistryError("补录前必须存在原始 upload-state")
    main_state = read_json(main_state_path)
    validate_upload_state(main_state)
    if main_state.get("projectNo") != project_no:
        raise RegistryError("原始 upload-state 与补录项目编号不一致")
    if main_state.get("status") not in {"FINALIZED_WITH_CONFLICTS", "VERIFIED"}:
        raise RegistryError("只有历史冲突案卷或已核验案卷可进入 supplement")
    if main_state.get("status") == "FINALIZED_WITH_CONFLICTS":
        summary = main_state.get("finalizeSummary")
        if not isinstance(summary, dict) or summary.get("created") is not True:
            raise RegistryError("原始任务未证明新建案卷成功，禁止自动补录")
    if getattr(args, "dry_run", False):
        return {
            "projectNo": project_no,
            "status": "SUPPLEMENT_DRY_RUN_OK",
            "network": False,
            "writes": [],
        }

    api_base, origin = origin_of(args.api_base)
    supplement_path, supplement_map_path, supplement_state_path = supplement_artifact_paths(
        manifest_path
    )
    supplement_state = read_json(supplement_state_path) if supplement_state_path.exists() else {}
    if supplement_state:
        validate_supplement_state(supplement_state)
    shared_session = getattr(args, "_shared_session", None)
    if shared_session is None:
        client_scope = httpx.Client(
            timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
            follow_redirects=False,
        )
    elif (
        not isinstance(shared_session, dict)
        or shared_session.get("apiBase") != api_base
        or shared_session.get("origin") != origin
        or not isinstance(shared_session.get("client"), httpx.Client)
        or not isinstance(shared_session.get("identity"), dict)
        or not isinstance(shared_session.get("writeHeaders"), dict)
    ):
        raise RegistryError("批量补录共享会话与当前目标不一致")
    else:
        client_scope = nullcontext(shared_session["client"])

    with client_scope as client:
        if shared_session is None:
            identity, write_headers = authenticate_client(
                client,
                api_base,
                origin,
                full_manifest,
                secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
            )
            ready = response_json(
                api_request(client, "GET", f"{api_base}/api/ready"), "服务就绪"
            )
            if ready.get("status") != "ready":
                raise RegistryError("服务未就绪")
        else:
            identity = shared_session["identity"]
            write_headers = shared_session["writeHeaders"]
            require_identity_scope(identity, full_manifest)
        require_same_state_identity(main_state, identity)
        if supplement_state:
            require_same_state_identity(supplement_state, identity)
            supplement, supplement_upload = load_supplement_inputs(
                supplement_path, supplement_map_path
            )
            projection = files_projection(supplement, supplement_upload)
            if (
                supplement_state.get("origin") != origin
                or supplement_state.get("sourceManifestSha256") != source_manifest_sha
                or supplement_state.get("supplementManifestSha256")
                != file_sha256(supplement_path)
                or supplement_state.get("baseSnapshotDigest")
                != supplement.get("baseSnapshotDigest")
                or supplement_state.get("projectNo") != project_no
                or supplement_state.get("brigadeCode") != brigade_code
                or supplement_state.get("filesProjection") != projection
            ):
                raise RegistryError("supplement-state 与当前清单、快照或文件投影不一致")
            job = get_supplement_job(
                client,
                api_base,
                supplement_state["jobId"],
                project_no,
                supplement["baseSnapshotDigest"],
                brigade_code,
            )
            if getattr(args, "plan", False):
                return {
                    "projectNo": project_no,
                    "status": "SUPPLEMENT_PLAN_EXISTING",
                    "jobStatus": job["status"],
                    "missingVersions": len(supplement["files"]),
                    "missingSlots": len(supplement["documentSlots"]),
                }
            job_id = supplement_state["jobId"]
            job_status = job["status"]
            if job_status != "FINALIZED":
                reconcile_uploaded_file_refs(supplement_state, job, projection)
                write_json(supplement_state_path, supplement_state)
        else:
            snapshot = response_json(
                api_request(
                    client,
                    "GET",
                    f"{api_base}/api/v2/case-import-state", params={"projectNo": project_no}
                ),
                "读取案卷同步快照",
            )
            validate_case_import_state(snapshot, project_no)
            if snapshot["case"]["brigade"].get("code") != brigade_code:
                raise RegistryError("服务器案卷快照与本地 manifest 大队不一致")
            supplement, supplement_upload, plan = build_supplement_manifest(
                full_manifest, full_upload, snapshot
            )
            if getattr(args, "plan", False):
                return {
                    "projectNo": project_no,
                    "status": "SUPPLEMENT_PLAN_OK",
                    **plan,
                    "writes": [],
                }
            if supplement is None:
                try:
                    verification = verify_with_poll(
                        client,
                        api_base,
                        full_manifest,
                        write_headers,
                        getattr(args, "deep_content_verify", False),
                        min(max(float(args.timeout), 1.0), 60.0),
                    )
                except RegistryError as error:
                    mark_verification_error(args, manifest_path, full_manifest, main_state, error)
                    raise
                if verification is None:
                    raise RegistryError("正式槽位已齐全，但飞牛落盘核验仍在进行")
                closed = close_original_upload_state_after_supplement(
                    main_state_path,
                    manifest_path,
                    full_manifest,
                    full_upload,
                    origin,
                    identity,
                    verification,
                    main_state.get("finalizedAt", utc_now()),
                )
                mark_verified_pending_archive(args, manifest_path, full_manifest, closed)
                archive_result = archive_verified_state(args, manifest_path, full_manifest, closed)
                return {
                    "projectNo": project_no,
                    "status": "VERIFIED",
                    "missingVersions": 0,
                    "verification": verification,
                    **archive_result_fields(archive_result),
                }
            write_json(supplement_path, supplement)
            write_json(supplement_map_path, {"files": supplement_upload})
            projection = files_projection(supplement, supplement_upload)
            supplement_sha = file_sha256(supplement_path)
            digest_material = json.dumps(
                {
                    "projectNo": project_no,
                    "baseSnapshotDigest": supplement["baseSnapshotDigest"],
                    "supplementManifestSha256": supplement_sha,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            idempotency_key = "xfpcr-supplement-v1-" + hashlib.sha256(
                digest_material.encode("utf-8")
            ).hexdigest()
            job = response_json(
                api_request(
                    client,
                    "POST",
                    f"{api_base}/api/v2/import-jobs",
                    headers={**write_headers, "Idempotency-Key": idempotency_key},
                    json={
                        "idempotencyKey": idempotency_key,
                        "projectNo": project_no,
                        "mode": "SUPPLEMENT_EXISTING",
                        "baseSnapshotDigest": supplement["baseSnapshotDigest"],
                    },
                ),
                "创建缺失文件补录任务",
            )
            job_id = job.get("id") if isinstance(job.get("id"), str) else ""
            if (
                not job_id
                or job.get("mode") != "SUPPLEMENT_EXISTING"
                or job.get("baseSnapshotDigest") != supplement["baseSnapshotDigest"]
                or job.get("status") not in {"CREATED", "UPLOADING"}
                or job.get("packageHash") not in {None, ""}
                or job.get("projectNoHint", project_no) != project_no
            ):
                raise RegistryError("创建补录任务响应无法安全对账")
            supplement_state = {
                "stateVersion": 1,
                "status": "UPLOADING",
                "origin": origin,
                "sourceManifestSha256": source_manifest_sha,
                "supplementManifestSha256": supplement_sha,
                "baseSnapshotDigest": supplement["baseSnapshotDigest"],
                "projectNo": project_no,
                "brigadeCode": brigade_code,
                "jobId": job_id,
                "authIdentity": identity,
                "filesProjection": projection,
                "uploadedFileRefs": [],
            }
            validate_supplement_state(supplement_state)
            write_json(supplement_state_path, supplement_state)
            job_status = job["status"]

        if job_status != "FINALIZED":
            projection_by_ref = {item["clientRef"]: item for item in projection}
            for item in supplement["files"]:
                if item["clientRef"] in supplement_state["uploadedFileRefs"]:
                    continue
                projection_item = projection_by_ref[item["clientRef"]]
                with Path(supplement_upload[item["clientRef"]]).open("rb") as stream:
                    uploaded = response_json(
                        api_request(
                            client,
                            "POST",
                            f"{api_base}/api/v2/import-jobs/{job_id}/files",
                            request_class="upload",
                            retry_on_429=False,
                            headers=write_headers,
                            params={"relativePath": item["relativePath"]},
                            files={"file": (Path(stream.name).name, stream, "application/pdf")},
                        ),
                        "上传补录 PDF",
                    )
                check_uploaded_file_response(uploaded, projection_item, job_id)
                supplement_state["uploadedFileRefs"] = sorted(
                    {*supplement_state["uploadedFileRefs"], item["clientRef"]}
                )
                write_json(supplement_state_path, supplement_state)
            expected_refs = {item["clientRef"] for item in projection}
            if set(supplement_state["uploadedFileRefs"]) != expected_refs:
                raise RegistryError("补录文件上传进度不完整，停止提交清单")
            if job_status != "MANIFEST_RECEIVED":
                manifest_response = response_json(
                    api_request(
                        client,
                        "PUT",
                        f"{api_base}/api/v2/import-jobs/{job_id}/manifest",
                        headers=write_headers,
                        json=supplement,
                    ),
                    "提交缺失文件补录清单",
                )
                check_manifest_response(manifest_response, job_id)
            response_json(
                api_request(
                    client,
                    "POST",
                    f"{api_base}/api/v2/import-jobs/{job_id}/finalize",
                    headers=write_headers,
                ),
                "完成缺失文件补录",
            )
            job = get_supplement_job(
                client,
                api_base,
                job_id,
                project_no,
                supplement["baseSnapshotDigest"],
                brigade_code,
            )
        summary = finalized_supplement_summary(job)
        if summary["addedFiles"] + summary["skippedCount"] + summary["conflictCount"] != len(
            supplement["files"]
        ):
            raise RegistryError("补录 finalize 计数未覆盖全部补录文件")
        supplement_state.update(
            {
                "status": (
                    "FINALIZED_WITH_CONFLICTS"
                    if summary["conflictCount"]
                    else "FINALIZED_UNVERIFIED"
                ),
                "caseId": summary["caseId"],
                "finalizedAt": job["finalizedAt"],
                "finalizeSummary": summary,
            }
        )
        validate_supplement_state(supplement_state)
        write_json(supplement_state_path, supplement_state)
        if summary["conflictCount"]:
            update_case_waterline(
                args,
                manifest_path,
                full_manifest,
                state="NEEDS_MANUAL_REVIEW",
                upload={
                    "status": "FINALIZED_WITH_CONFLICTS",
                    "finalizedAt": job["finalizedAt"],
                    "created": False,
                    "conflictCount": summary["conflictCount"],
                    "skippedCount": summary["skippedCount"],
                },
                nas_verification={"status": "NOT_STARTED"},
                error_summary="缺失文件补录仍存在正式文件冲突，需人工核对",
            )
            return {
                "projectNo": project_no,
                "status": "FINALIZED_WITH_CONFLICTS",
                "finalize": summary,
            }
        mark_awaiting_nas(
            args,
            manifest_path,
            full_manifest,
            supplement_state,
            "缺失文件补录已终结，等待飞牛落盘核验",
        )
        try:
            verification = verify_with_poll(
                client,
                api_base,
                full_manifest,
                write_headers,
                getattr(args, "deep_content_verify", False),
                min(max(float(args.timeout), 1.0), 60.0),
            )
        except RegistryError as error:
            mark_verification_error(
                args, manifest_path, full_manifest, supplement_state, error
            )
            raise
        if verification is None:
            return {
                "projectNo": project_no,
                "status": "FINALIZED_UNVERIFIED",
                "finalize": summary,
            }
        fresh_snapshot = response_json(
            api_request(
                client,
                "GET",
                f"{api_base}/api/v2/case-import-state", params={"projectNo": project_no}
            ),
            "补录后读取案卷同步快照",
        )
        remaining, _unused_map, remaining_plan = build_supplement_manifest(
            full_manifest, full_upload, fresh_snapshot
        )
        if remaining is not None or remaining_plan["missingVersions"]:
            raise RegistryError("补录后服务器仍存在本地清单中的缺失槽位版本")
        supplement_state.update(
            {"status": "VERIFIED", "verification": verification, "verifiedAt": utc_now()}
        )
        validate_supplement_state(supplement_state)
        write_json(supplement_state_path, supplement_state)
        closed = close_original_upload_state_after_supplement(
            main_state_path,
            manifest_path,
            full_manifest,
            full_upload,
            origin,
            identity,
            verification,
            job["finalizedAt"],
        )
        mark_verified_pending_archive(args, manifest_path, full_manifest, closed)
        archive_result = archive_verified_state(args, manifest_path, full_manifest, closed)
        return {
            "projectNo": project_no,
            "status": "VERIFIED",
            "finalize": summary,
            "verification": verification,
            **archive_result_fields(archive_result),
        }


def supplement_command(args: argparse.Namespace) -> None:
    print(json.dumps(supplement_case(args), ensure_ascii=False))


def supplement_batch_command(args: argparse.Namespace) -> None:
    projects = list(dict.fromkeys(args.project))
    if len(projects) != len(args.project):
        raise RegistryError("supplement-batch 的 --project 不得重复")
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(
            work_root=getattr(args, "work_root", None),
            config_path=getattr(args, "workspace_config", None),
            create_layout=False,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"无法安全解析批量补录工作根：{error}") from error
    prepared: list[tuple[str, Path, Path, dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    for project_no in projects:
        if not PROJECT_NO.fullmatch(project_no):
            results.append(
                {"projectNo": project_no, "status": "PRECHECK_FAILED", "error": "项目编号无效"}
            )
            continue
        manifest_path = layout.work_case_dir(project_no) / "manifest.json"
        upload_map_path = layout.work_case_dir(project_no) / "upload-map.json"
        try:
            resolved_path, manifest, _upload = load_inputs(
                str(manifest_path), str(upload_map_path)
            )
            if manifest.get("case", {}).get("projectNo") != project_no:
                raise RegistryError("manifest 项目编号与所选项目不一致")
            enforce_upload_workspace_preflight(args, resolved_path, manifest)
            main_state = read_json(resolved_path.parent / "upload-state.json")
            validate_upload_state(main_state)
            if main_state.get("status") not in {"FINALIZED_WITH_CONFLICTS", "VERIFIED"}:
                raise RegistryError("案卷不是可补录的历史冲突或 VERIFIED 状态")
        except RegistryError as error:
            results.append(
                {"projectNo": project_no, "status": "PRECHECK_FAILED", "error": str(error)}
            )
            continue
        prepared.append((project_no, resolved_path, upload_map_path, manifest))
    if args.dry_run:
        results.extend(
            {"projectNo": project_no, "status": "SUPPLEMENT_DRY_RUN_OK"}
            for project_no, *_rest in prepared
        )
        print(
            json.dumps(
                {
                    "status": "supplement-batch-dry-run",
                    "network": False,
                    "selected": len(projects),
                    "ready": len(prepared),
                    "failed": len(projects) - len(prepared),
                    "cases": results,
                },
                ensure_ascii=False,
            )
        )
        return
    if not prepared:
        print(
            json.dumps(
                {"status": "supplement-batch-not-started", "cases": results},
                ensure_ascii=False,
            )
        )
        return
    api_base, origin = origin_of(args.api_base)
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
        follow_redirects=False,
    ) as client:
        identity, write_headers = authenticate_client(
            client,
            api_base,
            origin,
            prepared[0][3],
            secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
        )
        brigades = {manifest["case"]["brigadeCode"] for *_paths, manifest in prepared}
        if len(brigades) > 1 and identity.get("role") != "ADMIN":
            raise RegistryError("跨大队批量补录必须使用 ADMIN 账户")
        for *_paths, manifest in prepared:
            require_identity_scope(identity, manifest)
        ready = response_json(
            api_request(client, "GET", f"{api_base}/api/ready"), "服务就绪"
        )
        if ready.get("status") != "ready":
            raise RegistryError("服务未就绪")
        shared_session = {
            "client": client,
            "apiBase": api_base,
            "origin": origin,
            "identity": identity,
            "writeHeaders": write_headers,
        }
        for project_no, manifest_path, upload_map_path, _manifest in prepared:
            case_args = argparse.Namespace(**vars(args))
            case_args.manifest = str(manifest_path)
            case_args.upload_map = str(upload_map_path)
            case_args._shared_session = shared_session
            try:
                results.append(supplement_case(case_args))
            except RegistryError as error:
                results.append({"projectNo": project_no, "status": "FAILED", "error": str(error)})
            except httpx.TransportError:
                results.append(
                    {
                        "projectNo": project_no,
                        "status": "FAILED",
                        "error": "网络传输失败，请检查连接后从本案补录断点重试",
                    }
                )
    excel_warning = None
    if args.finalize:
        try:
            workspace.export_waterline_xlsx(layout)
        except workspace.WorkspaceStateError as error:
            excel_warning = f"JSON 水位已保留，但 Excel 水位表刷新失败：{error}"
    failed = sum(1 for item in results if item["status"] in {"PRECHECK_FAILED", "FAILED"})
    verified = sum(1 for item in results if item["status"] == "VERIFIED")
    planned = sum(1 for item in results if item["status"].startswith("SUPPLEMENT_PLAN"))
    awaiting_nas = sum(1 for item in results if item["status"] == "FINALIZED_UNVERIFIED")
    conflicts = sum(1 for item in results if item["status"] == "FINALIZED_WITH_CONFLICTS")
    print(
        json.dumps(
            {
                "status": (
                    "supplement-batch-completed"
                    if failed + awaiting_nas + conflicts == 0
                    else "supplement-batch-completed-with-attention"
                ),
                "selected": len(projects),
                "planned": planned,
                "verified": verified,
                "awaitingNas": awaiting_nas,
                "manualReview": conflicts,
                "failed": failed,
                "cases": results,
                **({"warning": excel_warning} if excel_warning else {}),
            },
            ensure_ascii=False,
        )
    )


def upload_batch_command(args: argparse.Namespace) -> None:
    projects = list(dict.fromkeys(args.project))
    if len(projects) != len(args.project):
        raise RegistryError("upload-batch 的 --project 不得重复")
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(
            work_root=getattr(args, "work_root", None),
            config_path=getattr(args, "workspace_config", None),
            create_layout=False,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(f"无法安全解析批量上传工作根：{error}") from error

    prepared: list[tuple[str, Path, Path, dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    for project_no in projects:
        if not PROJECT_NO.fullmatch(project_no):
            results.append(
                {"projectNo": project_no, "status": "PRECHECK_FAILED", "error": "项目编号无效"}
            )
            continue
        manifest_path = layout.work_case_dir(project_no) / "manifest.json"
        upload_map_path = layout.work_case_dir(project_no) / "upload-map.json"
        try:
            resolved_path, manifest, _upload = load_inputs(str(manifest_path), str(upload_map_path))
            if manifest.get("case", {}).get("projectNo") != project_no:
                raise RegistryError("manifest 项目编号与所选项目不一致")
            enforce_upload_workspace_preflight(args, resolved_path, manifest)
        except RegistryError as error:
            results.append(
                {"projectNo": project_no, "status": "PRECHECK_FAILED", "error": str(error)}
            )
            continue
        prepared.append((project_no, resolved_path, upload_map_path, manifest))

    if args.dry_run:
        for project_no, _manifest_path, _map_path, _manifest in prepared:
            results.append({"projectNo": project_no, "status": "DRY_RUN_OK"})
        print(
            json.dumps(
                {
                    "status": "batch-dry-run",
                    "network": False,
                    "selected": len(projects),
                    "ready": len(prepared),
                    "failed": len(results) - len(prepared),
                    "cases": results,
                },
                ensure_ascii=False,
            )
        )
        return
    if not args.finalize:
        raise RegistryError("批量正式写入必须显式指定 --finalize")
    if not prepared:
        print(
            json.dumps(
                {"status": "batch-not-started", "selected": len(projects), "cases": results},
                ensure_ascii=False,
            )
        )
        return

    api_base, origin = origin_of(args.api_base)
    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
        follow_redirects=False,
    ) as client:
        identity, write_headers = authenticate_client(
            client,
            api_base,
            origin,
            prepared[0][3],
            secure_auth_config_path(Path(getattr(args, "auth_config", DEFAULT_AUTH_CONFIG))),
        )
        brigades = {manifest["case"]["brigadeCode"] for *_paths, manifest in prepared}
        if len(brigades) > 1 and identity.get("role") != "ADMIN":
            raise RegistryError("跨大队批量上传必须使用 ADMIN 账户")
        for *_paths, manifest in prepared:
            require_identity_scope(identity, manifest)
        if (
            response_json(
                api_request(client, "GET", f"{api_base}/api/ready"), "服务就绪"
            ).get("status")
            != "ready"
        ):
            raise RegistryError("服务未就绪")
        shared_session = {
            "client": client,
            "apiBase": api_base,
            "origin": origin,
            "identity": identity,
            "writeHeaders": write_headers,
        }
        for project_no, manifest_path, upload_map_path, _manifest in prepared:
            print(
                json.dumps({"projectNo": project_no, "batchPhase": "STARTED"}, ensure_ascii=False)
            )
            case_args = argparse.Namespace(**vars(args))
            case_args.manifest = str(manifest_path)
            case_args.upload_map = str(upload_map_path)
            case_args.dry_run = False
            case_args._shared_session = shared_session
            try:
                upload_command(case_args)
                state_path = manifest_path.parent / "upload-state.json"
                case_status = (
                    read_json(state_path).get("status")
                    if state_path.exists()
                    else "VERIFIED_ARCHIVED"
                )
                results.append({"projectNo": project_no, "status": case_status})
            except RegistryError as error:
                results.append({"projectNo": project_no, "status": "FAILED", "error": str(error)})
            except httpx.TransportError:
                results.append(
                    {
                        "projectNo": project_no,
                        "status": "FAILED",
                        "error": "网络传输失败，请检查连接后从本案断点重试",
                    }
                )

    excel_warning = None
    try:
        workspace.export_waterline_xlsx(layout)
    except workspace.WorkspaceStateError as error:
        excel_warning = f"JSON 水位已保留，但 Excel 水位表刷新失败：{error}"
    failed = sum(1 for item in results if item["status"] in {"PRECHECK_FAILED", "FAILED"})
    verified = sum(1 for item in results if item["status"] in {"VERIFIED", "VERIFIED_ARCHIVED"})
    awaiting_nas = sum(1 for item in results if item["status"] == "FINALIZED_UNVERIFIED")
    manual_review = sum(1 for item in results if item["status"] == "FINALIZED_WITH_CONFLICTS")
    needs_attention = failed + awaiting_nas + manual_review
    print(
        json.dumps(
            {
                "status": (
                    "batch-completed" if needs_attention == 0 else "batch-completed-with-attention"
                ),
                "selected": len(projects),
                "verified": verified,
                "awaitingNas": awaiting_nas,
                "manualReview": manual_review,
                "failed": failed,
                "cases": results,
                **({"warning": excel_warning} if excel_warning else {}),
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


def ledger_status_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        result = workspace.workspace_progress(layout, batch_id=args.batch_id)
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


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


def source_tail_cursor_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    try:
        result = source.plan_tail_first_cursor(
            args.total_count,
            args.page_size,
            args.page_number,
            args.visible_row_count,
            args.row_number,
        )
    except source.SourceIntakeError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_await_download_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        result = source.await_download(
            layout,
            batch_id=args.batch_id,
            rwid=args.rwid,
            download_baseline=Path(args.download_baseline),
            download_dir=config.download_dir,
            allowed_download_dir=config.download_dir,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            stalled_after_seconds=args.stalled_after_seconds,
            attach=args.attach,
        )
    except (source.SourceIntakeError, workspace.WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    keys = {
        "schemaVersion",
        "status",
        "batchId",
        "rwid",
        "projectNo",
        "reason",
        "elapsedSeconds",
        "partialCandidates",
        "zipCandidates",
        "originalSuggestedName",
        "zipInspection",
    }
    summary = {key: result[key] for key in keys if key in result}
    if isinstance(result.get("capture"), dict):
        summary["capture"] = source_result_summary(result["capture"])
    print(json.dumps(summary, ensure_ascii=False))


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

    source_tail_cursor = source_sub.add_parser(
        "tail-cursor",
        help="按页面实时总数和可见行数计算尾页优先的当前与下一案卷位置",
    )
    source_tail_cursor.add_argument("--total-count", type=int, required=True)
    source_tail_cursor.add_argument("--page-size", type=int, required=True)
    source_tail_cursor.add_argument("--page-number", type=int, required=True)
    source_tail_cursor.add_argument("--visible-row-count", type=int, required=True)
    source_tail_cursor.add_argument(
        "--row-number",
        type=int,
        help="当前处理的行；省略时取当前页最后一个可见行",
    )
    source_tail_cursor.set_defaults(func=source_tail_cursor_command)

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

    source_await = source_sub.add_parser(
        "await-download",
        help="等待来源系统异步生成 ZIP；完成后可自动校验、规范命名并绑定",
    )
    add_workspace_resolution_options(source_await)
    source_await.add_argument("--batch-id", required=True)
    source_await.add_argument("--rwid", "--record-key", dest="rwid", required=True)
    source_await.add_argument(
        "--download-baseline",
        required=True,
        help="本案点击打包前由 source snapshot-downloads 生成的下载基线 JSON",
    )
    source_await.add_argument(
        "--timeout-seconds",
        type=float,
        default=30 * 60,
        help="最长等待秒数，默认 1800 秒",
    )
    source_await.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="下载目录轮询间隔秒数，默认 5 秒",
    )
    source_await.add_argument(
        "--stalled-after-seconds",
        type=float,
        default=5 * 60,
        help="临时下载文件持续无变化后判定停滞的秒数，默认 300 秒",
    )
    source_await.add_argument(
        "--attach",
        action="store_true",
        help="唯一完整 ZIP 稳定并通过检查后自动绑定到当前案卷",
    )
    source_await.set_defaults(func=source_await_download_command)

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
    ledger_status = ledger_sub.add_parser(
        "status",
        help="只读汇总当前正式批次的查询、存储、整理、上传与飞牛核验进度",
    )
    add_workspace_resolution_options(ledger_status)
    ledger_status.add_argument(
        "--batch-id",
        help="指定 scope=all 的正式批次；省略时选择更新时间最新的正式批次",
    )
    ledger_status.set_defaults(func=ledger_status_command)

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

    supplement = sub.add_parser(
        "supplement",
        help="按服务器 CaseImportStateV1 快照只补既有案卷的缺失文书版本",
    )
    supplement.add_argument("--manifest", required=True)
    supplement.add_argument("--upload-map", required=True)
    supplement.add_argument("--api-base", required=True)
    supplement.add_argument(
        "--auth-config",
        default=str(DEFAULT_AUTH_CONFIG),
        help=(
            "本地 TOML 认证配置；默认使用 "
            "%%LOCALAPPDATA%%/xf-product-case-registry/admin-upload-config.toml"
        ),
    )
    supplement.add_argument("--timeout", type=float, default=60.0)
    supplement.add_argument(
        "--deep-content-verify",
        action="store_true",
        help="显式取回飞牛正文并下载校验内容 SHA-256",
    )
    supplement.add_argument(
        "--no-archive",
        action="store_true",
        help="核验成功后不自动收口本地工作目录",
    )
    supplement_action = supplement.add_mutually_exclusive_group(required=True)
    supplement_action.add_argument("--dry-run", action="store_true")
    supplement_action.add_argument(
        "--plan", action="store_true", help="只读取实时服务器快照并输出缺失数量，不写入"
    )
    supplement_action.add_argument("--finalize", action="store_true")
    add_workspace_resolution_options(supplement)
    supplement.set_defaults(func=supplement_command, workspace_required=True)

    supplement_batch = sub.add_parser(
        "supplement-batch",
        help="在一个认证会话中按项目编号依次补录服务器确认缺失的文书版本",
    )
    supplement_batch.add_argument(
        "--project",
        action="append",
        required=True,
        help="项目编号；每个案卷重复指定一次，严格按给定顺序处理",
    )
    supplement_batch.add_argument("--api-base", required=True)
    supplement_batch.add_argument(
        "--auth-config",
        default=str(DEFAULT_AUTH_CONFIG),
        help=(
            "本地 TOML 认证配置；默认使用 "
            "%%LOCALAPPDATA%%/xf-product-case-registry/admin-upload-config.toml"
        ),
    )
    supplement_batch.add_argument("--timeout", type=float, default=60.0)
    supplement_batch.add_argument(
        "--deep-content-verify",
        action="store_true",
        help="显式取回飞牛正文并下载校验内容 SHA-256",
    )
    supplement_batch.add_argument(
        "--no-archive",
        action="store_true",
        help="核验成功后不自动收口本地工作目录",
    )
    supplement_batch_action = supplement_batch.add_mutually_exclusive_group(required=True)
    supplement_batch_action.add_argument("--dry-run", action="store_true")
    supplement_batch_action.add_argument(
        "--plan", action="store_true", help="只读取实时服务器快照并输出缺失数量，不写入"
    )
    supplement_batch_action.add_argument("--finalize", action="store_true")
    add_workspace_resolution_options(supplement_batch)
    supplement_batch.set_defaults(func=supplement_batch_command, workspace_required=True)

    upload_batch = sub.add_parser(
        "upload-batch",
        help="在一个认证会话中按项目编号依次续传、终结、核验多个案卷",
    )
    upload_batch.add_argument(
        "--project",
        action="append",
        required=True,
        help="项目编号；每个案卷重复指定一次，严格按给定顺序处理",
    )
    upload_batch.add_argument("--api-base", required=True)
    upload_batch.add_argument(
        "--auth-config",
        default=str(DEFAULT_AUTH_CONFIG),
        help=(
            "本地 TOML 认证配置；默认使用 "
            "%%LOCALAPPDATA%%/xf-product-case-registry/admin-upload-config.toml"
        ),
    )
    upload_batch.add_argument("--timeout", type=float, default=60.0)
    upload_batch.add_argument("--dry-run", action="store_true")
    upload_batch.add_argument("--finalize", action="store_true")
    upload_batch.add_argument(
        "--deep-content-verify",
        action="store_true",
        help="显式取回飞牛正文并下载校验内容 SHA-256",
    )
    upload_batch.add_argument(
        "--no-archive",
        action="store_true",
        help="核验成功后不自动收口本地工作目录",
    )
    add_workspace_resolution_options(upload_batch)
    upload_batch.set_defaults(func=upload_batch_command, workspace_required=True)
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
