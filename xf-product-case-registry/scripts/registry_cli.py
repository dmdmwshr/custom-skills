from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import mimetypes
import re
import shutil
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader, PdfWriter

if __package__:
    from . import image_pdf, ledger_views, normalized_sources, office_normalized
    from .ocr_runtime import OcrError, adopt_existing_results, run_ocr
    from .workflow_reporting import ReportingError, record_timing, render_report, write_report
else:
    import image_pdf
    import ledger_views
    import normalized_sources
    import office_normalized
    from ocr_runtime import OcrError, adopt_existing_results, run_ocr
    from workflow_reporting import ReportingError, record_timing, render_report, write_report

VERSION = "2.1.0"
SKILL_ROOT = Path(__file__).resolve().parents[1]


class RegistryError(RuntimeError):
    pass


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
# Local business entities beyond the eight cloud brigades; they never widen cloud reporting scopes.
LOCAL_ONLY_BRIGADES = {"SHUISHANG": "水上大队"}
LOCAL_BRIGADES = BRIGADES | set(LOCAL_ONLY_BRIGADES)
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
            try:
                record["pageCount"] = pdf_info(path)[1]
            except RegistryError:
                # a %PDF- file the reader cannot parse is kept as a flagged member: it can only be
                # used through normalize-office (verified external rendering); compose refuses it
                # otherwise. Zero-page or non-PDF files still stop the inventory.
                if not office_normalized.is_unparseable_pdf(path.read_bytes()):
                    raise
                record["unparseablePdf"] = True
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


def pdf_parts(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit ordered source ranges; never mix legacy and multipart syntax."""
    if "parts" in item:
        if any(key in item for key in ("sourceRelativePath", "pageStart", "pageEnd")):
            raise RegistryError("parts 不得与单段来源混用")
        parts = item["parts"]
        if not isinstance(parts, list) or not parts:
            raise RegistryError("parts 必须是非空数组")
    else:
        parts = [item]
    result = []
    for part in parts:
        if not isinstance(part, dict):
            raise RegistryError("PDF 来源分段必须是对象")
        start, end = part.get("pageStart"), part.get("pageEnd")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise RegistryError("页码范围不合法")
        if not isinstance(part.get("sourceRelativePath"), str):
            raise RegistryError("PDF 来源相对路径不合法")
        result.append(
            {
                "sourceRelativePath": safe_relative(part.get("sourceRelativePath")),
                "pageStart": start,
                "pageEnd": end,
            }
        )
    return result


def local_layout(args: argparse.Namespace, work: Path) -> tuple[Any, str] | None:
    resolved = resolve_local_case_workspace(args, work)
    return (resolved[1], resolved[2]) if resolved else None


def supplement_entries(args: argparse.Namespace, work: Path) -> list[dict[str, Any]]:
    """Verified real external supplements (own source kind, never ZIP members)."""
    located = local_layout(args, work)
    if located is None:
        return []
    layout, project = located
    module = importlib.import_module(f"{__package__}.supplements" if __package__ else "supplements")
    try:
        return module.entries(layout, project)
    except workspace_api().WorkspaceStateError as error:
        raise RegistryError(str(error)) from error


def image_source_bytes(work: Path, entries: dict[str, dict[str, Any]]) -> Any:
    def read(item: dict[str, Any]) -> bytes:
        entry = entries.get(item["sourceRelativePath"])
        if entry is None or entry.get("sha256") != item.get("sourceSha256"):
            raise OSError("source image not in verified material")
        return Path(entry["absolutePath"]).read_bytes()

    return read


SPLIT_INTENT = "split-incremental-intent.json"


def load_incremental_baseline(work: Path) -> tuple[dict[str, Any], dict[Path, dict[str, Any]]]:
    """增量拆分基线：旧索引必须存在且无未收尾的增量意图，旧条目保持原样。"""
    if (work / SPLIT_INTENT).exists():
        raise RegistryError(
            f"存在未收尾的增量拆分记录 {SPLIT_INTENT}，必须先核对目标实际摘要，不得盲重跑"
        )
    path = work / "split-index.json"
    if not path.is_file():
        raise RegistryError("增量拆分需要已存在的 split-index.json；首次拆分不带 --incremental")
    old = read_json(path)
    items = old.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RegistryError("旧 split-index.items 必须是对象数组")
    by_target: dict[Path, dict[str, Any]] = {}
    for item in items:
        relative = item.get("relativePath")
        if not isinstance(relative, str):
            raise RegistryError("旧 split-index 条目缺少 relativePath")
        target = (work / safe_relative(relative)).resolve()
        if target in by_target:
            raise RegistryError(f"旧 split-index 含重复目标：{relative}")
        by_target[target] = item
    return old, by_target


def check_incremental_reuse(
    item: dict[str, Any], parts: list[dict[str, Any]], target: Path, old: dict[str, Any]
) -> None:
    """同一目标已在旧索引：来源分段与计划元数据必须一致，输出字节必须仍等于旧摘要。"""
    if pdf_parts(old) != parts:
        raise RegistryError(f"旧条目来源分段与本次计划冲突，拒绝改写：{old.get('relativePath')}")
    for key, value in item.items():
        if key in {"relativePath", "parts", "sourceRelativePath", "pageStart", "pageEnd"}:
            continue
        if old.get(key) != value:
            raise RegistryError(
                f"旧条目元数据与本次计划冲突（{key}），拒绝改写：{old.get('relativePath')}"
            )
    if not target.is_file():
        raise RegistryError(f"旧条目输出已缺失，需人工对账：{old.get('relativePath')}")
    sha, pages = pdf_info(target)
    if sha != old.get("sha256") or pages != old.get("pageCount"):
        raise RegistryError(f"旧条目输出字节已变化，拒绝复用：{old.get('relativePath')}")


def commit_incremental_split(
    args: argparse.Namespace,
    work: Path,
    plan: dict[str, Any],
    by_rel: dict[str, dict[str, Any]],
    planned: list[tuple[dict[str, Any], Path, list[dict[str, Any]]]],
    old_index: dict[str, Any],
    reused: list[str],
) -> None:
    """新输出先在批次临时目录完成并核验，再逐个独占写入并追加索引；失败保留意图记录。"""
    if not planned:
        return  # 全部复用：不改索引、不动水位
    intent_path = work / SPLIT_INTENT
    token = f"{int(time.time() * 1000)}"
    stage = work / f".split-incremental-{token}"
    old_sha = file_sha256(work / "split-index.json")
    intent = {
        "phase": "STAGING",
        "startedAt": utc_now(),
        "oldIndexSha256": old_sha,
        "stageDir": stage.name,
        "newTargets": [target.relative_to(work).as_posix() for _, target, _ in planned],
        "reused": reused,
    }
    try:
        with intent_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(intent, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as error:
        raise RegistryError(f"另一增量拆分仍在进行或未收尾：{SPLIT_INTENT}") from error
    stage.mkdir()
    staged = []
    for index, (item, target, parts) in enumerate(planned):
        writer = PdfWriter()
        for part in parts:
            reader = PdfReader(str(by_rel[part["sourceRelativePath"]]["absolutePath"]))
            for page in reader.pages[part["pageStart"] - 1 : part["pageEnd"]]:
                writer.add_page(page)
        temporary = stage / f"{index:03d}.pdf"
        with temporary.open("xb") as stream:
            writer.write(stream)
        sha, pages = pdf_info(temporary)
        staged.append((item, target, parts, temporary, sha, pages))
    new_items = [
        {
            **item,
            "sourceKinds": sorted(
                {by_rel[p["sourceRelativePath"]].get("sourceKind", "ZIP_MEMBER") for p in parts}
            ),
            "relativePath": target.relative_to(work).as_posix(),
            "absolutePath": str(target),
            "sha256": sha,
            "mimeType": "application/pdf",
            "pageCount": pages,
        }
        for item, target, parts, _, sha, pages in staged
    ]
    merged = {
        **old_index,
        "splitIndexVersion": max(
            old_index.get("splitIndexVersion") or 0,
            5 if any("parts" in item for item in plan["items"]) else 4,
        ),
        "lastIncrementalAt": utc_now(),
        "items": [*old_index["items"], *new_items],
    }
    intent["phase"] = "COMMITTING"
    intent["expectedNewSha256"] = {entry["relativePath"]: entry["sha256"] for entry in new_items}
    write_json(intent_path, intent)
    for _, target, _, temporary, sha, _ in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:  # 目标被并发占用时在此失败，意图记录保留
            stream.write(temporary.read_bytes())
        if pdf_info(target)[0] != sha:
            raise RegistryError(f"新输出写入后摘要不符：{target}")
    write_json(work / "split-index.json", merged)
    written = read_json(work / "split-index.json")["items"][-len(new_items) :]
    if [item["sha256"] for item in written] != [entry["sha256"] for entry in new_items]:
        raise RegistryError("split-index 回读与预期不符，保留增量意图记录")
    shutil.rmtree(stage)
    intent_path.unlink()
    update_local_case_waterline(args, work, state="ORGANIZING", local_status="ORGANIZING")


def split_command(args: argparse.Namespace) -> None:
    work, plan = Path(args.work_dir).resolve(), read_json(Path(args.plan).resolve())
    enforce_local_workspace_preflight(args, work)
    inventory = project_local_path_fields(read_json(work / "inventory.json"), work, args)
    by_rel = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    for entry in supplement_entries(args, work):
        if entry["relativePath"] in by_rel:
            raise RegistryError(f"补采文件名与 ZIP 成员冲突：{entry['relativePath']}")
        by_rel[entry["relativePath"]] = entry
    planned: list[tuple[dict[str, Any], Path, list[dict[str, Any]]]] = []
    seen: set[Path] = set()
    normalized = (work / "normalized").resolve()
    if not isinstance(plan.get("items"), list):
        raise RegistryError("split-plan.items 必须是数组")
    incremental = bool(getattr(args, "incremental", False))
    old_index: dict[str, Any] = {}
    old_by_target: dict[Path, dict[str, Any]] = {}
    reused: list[str] = []
    if incremental:
        old_index, old_by_target = load_incremental_baseline(work)
    for index, item in enumerate(plan["items"], 1):
        if not isinstance(item, dict):
            raise RegistryError("split-plan 每项必须是对象")
        parts = pdf_parts(item)
        source_paths = []
        for part in parts:
            source = by_rel.get(part["sourceRelativePath"])
            if not source or source.get("mimeType") != "application/pdf":
                raise RegistryError("拆分来源必须是 inventory 中的 PDF")
            source_path = Path(source["absolutePath"]).resolve()
            if source.get("sourceKind") != "EXTERNAL_SUPPLEMENT" and not inside(
                source_path, Path(inventory["sourceRoot"]).resolve()
            ):
                raise RegistryError("PDF 拆分来源路径越界")
            sha, pages = pdf_info(source_path)
            if sha != source.get("sha256"):
                raise RegistryError("PDF 拆分来源哈希已变化")
            if part["pageEnd"] > pages:
                raise RegistryError("页码超出来源 PDF")
            source_paths.append(source_path)
        relative = safe_relative(str(item.get("relativePath") or f"normalized/{index:03d}.pdf"))
        target = (work / relative).resolve()
        if (
            not relative.startswith("normalized/")
            or target.suffix.lower() != ".pdf"
            or not inside(target, normalized)
        ):
            raise RegistryError("拆分输出只能位于 normalized/ 且必须为 PDF")
        if incremental and target in old_by_target and target not in seen:
            check_incremental_reuse(item, parts, target, old_by_target[target])
            reused.append(relative)
            seen.add(target)
            continue
        if (
            target in seen
            or target.exists()
            or target in source_paths
            or any(inside(source_path, normalized) for source_path in source_paths)
        ):
            raise RegistryError("拆分目标重复、已存在或与源重叠")
        seen.add(target)
        planned.append((item, target, parts))
    if incremental:
        commit_incremental_split(args, work, plan, by_rel, planned, old_index, reused)
        return
    result = []
    for item, target, parts in planned:
        writer = PdfWriter()
        for part in parts:
            reader = PdfReader(str(by_rel[part["sourceRelativePath"]]["absolutePath"]))
            for page in reader.pages[part["pageStart"] - 1 : part["pageEnd"]]:
                writer.add_page(page)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            writer.write(stream)
        sha, pages = pdf_info(target)
        result.append(
            {
                **item,
                "sourceKinds": sorted(
                    {by_rel[p["sourceRelativePath"]].get("sourceKind", "ZIP_MEMBER") for p in parts}
                ),
                "relativePath": target.relative_to(work).as_posix(),
                "absolutePath": str(target),
                "sha256": sha,
                "mimeType": "application/pdf",
                "pageCount": pages,
            }
        )
    write_json(
        work / "split-index.json",
        {
            "splitIndexVersion": 5 if any("parts" in item for item in plan["items"]) else 4,
            "generatedAt": utc_now(),
            "items": result,
        },
    )
    update_local_case_waterline(
        args,
        work,
        state="ORGANIZING",
        local_status="ORGANIZING",
    )


def normalize_images_command(args: argparse.Namespace) -> None:
    """Faithfully convert selected PNG/JPEG originals to single-page PDFs with a source mapping."""
    work = Path(args.work_dir).resolve()
    enforce_local_workspace_preflight(args, work)
    inventory = project_local_path_fields(read_json(work / "inventory.json"), work, args)
    sources = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    for entry in supplement_entries(args, work):
        sources.setdefault(entry["relativePath"], entry)
    try:
        index = normalized_sources.load(work)
    except (ValueError, KeyError, OSError) as error:
        raise RegistryError(f"image-normalization.json 无法读取：{error}") from error
    if not args.relative_path:
        raise RegistryError("必须用 --relative-path 明确选择要转换的图片，不批量猜测")
    produced = []
    for requested in args.relative_path:
        relative = safe_relative(requested)
        entry = sources.get(relative)
        if not entry or entry.get("mimeType") not in {"image/png", "image/jpeg"}:
            raise RegistryError(f"只能转换 inventory 或补采材料中的 PNG/JPEG：{relative}")
        path = Path(entry["absolutePath"]).resolve()
        if entry.get("sourceKind") != "EXTERNAL_SUPPLEMENT" and not inside(
            path, Path(inventory["sourceRoot"]).resolve()
        ):
            raise RegistryError("图片来源路径越界")
        data = path.read_bytes()
        if image_pdf.sha256(data) != entry.get("sha256"):
            raise RegistryError(f"源图片哈希已变化：{relative}")
        try:
            pdf, mapping = image_pdf.build_pdf(data)
        except image_pdf.ImageNormalizationError as error:
            raise RegistryError(f"{relative} 无法忠实转换：{error}") from error
        stem = re.sub(r"[^\w.\-]+", "_", PurePosixPath(relative).stem, flags=re.UNICODE)[:60]
        output = f"normalized/image_{mapping['sourceSha256'][7:19]}_{stem}.pdf"
        target = (work / output).resolve()
        if not inside(target, (work / "normalized").resolve()):
            raise RegistryError("图片规范化输出越界")
        if target.exists():
            problems = image_pdf.verify_pdf(target.read_bytes(), data, mapping)
            if problems:
                raise RegistryError(f"规范化目标已存在且与源图片不符，拒绝覆盖：{output}")
            status = "REUSED"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(pdf)
            status = "CREATED"
        sha, pages = pdf_info(target)
        item = {
            **mapping,
            "sourceRelativePath": relative,
            "sourceKind": entry.get("sourceKind", "ZIP_MEMBER"),
            "outputRelativePath": output,
            "outputSha256": sha,
            "outputPageCount": pages,
            "normalizedAt": utc_now(),
        }
        previous = index.get(output)
        if previous and previous.get("outputSha256") == sha:
            item["normalizedAt"] = previous.get("normalizedAt", item["normalizedAt"])
        index[output] = item
        produced.append({"status": status, "source": relative, "output": output, "sha256": sha})
    normalized_sources.save(work, index, utc_now())
    update_local_case_waterline(args, work, state="ORGANIZING", local_status="ORGANIZING")
    print(json.dumps({"status": "NORMALIZED", "items": produced}, ensure_ascii=False))


def normalize_office_command(args: argparse.Namespace) -> None:
    """Bind one operator-rendered PDF to its Word (.docx/.doc) original with a verifiable record."""
    work = Path(args.work_dir).resolve()
    enforce_local_workspace_preflight(args, work)
    inventory = project_local_path_fields(read_json(work / "inventory.json"), work, args)
    sources = {
        item["relativePath"]: item for item in inventory.get("files", []) if isinstance(item, dict)
    }
    try:
        index = office_normalized.load(work)
    except (ValueError, KeyError, OSError) as error:
        raise RegistryError(f"office-normalization.json 无法读取：{error}") from error
    relative = safe_relative(args.relative_path)
    entry = sources.get(relative)
    if not entry:
        raise RegistryError(f"只能转换 inventory 中的 Word 或无法解析的 PDF 原件：{relative}")
    path = Path(entry["absolutePath"]).resolve()
    if not inside(path, Path(inventory["sourceRoot"]).resolve()):
        raise RegistryError("Word 原件路径越界")
    data = path.read_bytes()
    if office_normalized.sha256(data) != entry.get("sha256"):
        raise RegistryError(f"Word 原件哈希已变化：{relative}")
    converted = Path(args.converted_pdf).resolve()
    if inside(converted, work):
        raise RegistryError("转换结果必须是工作区之外的文件，由本命令复制入 normalized/")
    if not converted.is_file():
        raise RegistryError(f"转换结果不存在：{converted}")
    pdf = converted.read_bytes()
    try:
        mapping = office_normalized.build_mapping(
            data,
            relative,
            pdf,
            converter_name=args.converter_name,
            converter_version=args.converter_version,
            rendering_check=args.rendering_check,
        )
    except office_normalized.OfficeNormalizationError as error:
        raise RegistryError(f"{relative} 无法绑定：{error}") from error
    stem = re.sub(r"[^\w.\-]+", "_", PurePosixPath(relative).stem, flags=re.UNICODE)[:60]
    output = f"normalized/office_{mapping['sourceSha256'][7:19]}_{stem}.pdf"
    target = (work / output).resolve()
    if not inside(target, (work / "normalized").resolve()):
        raise RegistryError("Word 规范化输出越界")
    if target.exists():
        if target.read_bytes() != pdf:
            raise RegistryError(f"规范化目标已存在且与本次转换结果不符，拒绝覆盖：{output}")
        status = "REUSED"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(pdf)
        status = "CREATED"
    sha, pages = pdf_info(target)
    item = {
        **mapping,
        "sourceRelativePath": relative,
        "sourceKind": entry.get("sourceKind", "ZIP_MEMBER"),
        "outputRelativePath": output,
        "normalizedAt": utc_now(),
    }
    previous = index.get(output)
    if previous and previous.get("outputSha256") == sha:
        item["normalizedAt"] = previous.get("normalizedAt", item["normalizedAt"])
    index[output] = item
    office_normalized.save(work, index, utc_now())
    update_local_case_waterline(args, work, state="ORGANIZING", local_status="ORGANIZING")
    print(
        json.dumps(
            {
                "status": "NORMALIZED",
                "items": [
                    {
                        "status": status,
                        "source": relative,
                        "output": output,
                        "sha256": sha,
                        "pageCount": pages,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )


def ocr_command(args: argparse.Namespace) -> None:
    work, inventory = (
        Path(args.work_dir).resolve(),
        read_json(Path(args.work_dir).resolve() / "inventory.json"),
    )
    enforce_local_workspace_preflight(args, work)
    allowed_roots = None
    path_transform = None
    project_no = None
    if getattr(args, "workspace_required", False):
        resolved = resolve_local_case_workspace(args, work)
        assert resolved is not None
        _workspace, layout, project = resolved
        project_no = project
        allowed_roots = [work / "source", layout.pending_case_dir(project)]
        path_transform = layout.project_recorded_paths
        inventory = path_transform(inventory)
    if getattr(args, "adopt_existing", None):
        # Explicit reuse of finished results: verifies recorded digests, never starts an
        # engine and never sends a request.
        try:
            result = adopt_existing_results(
                work=work,
                inventory=inventory,
                output=Path(args.output_dir),
                receipts=read_json(Path(args.adopt_existing)),
                allowed_roots=allowed_roots,
                project=project_no,
                path_transform=path_transform,
            )
        except OcrError as error:
            raise RegistryError(str(error)) from error
        update_local_case_waterline(args, work, state="ORGANIZING", local_status="ORGANIZING")
        print(json.dumps(result, ensure_ascii=False))
        return
    try:
        result = run_ocr(
            work=work,
            inventory=inventory,
            output=Path(args.output_dir),
            selected=args.relative_path or [],
            wrapper=MINERU_SCRIPT,
            powershell=SYSTEM_POWERSHELL,
            timeout=args.timeout,
            batch=getattr(args, "batch", False),
            allowed_roots=allowed_roots,
            path_transform=path_transform,
        )
    except OcrError as error:
        raise RegistryError(str(error)) from error
    if (
        result.get("status") != "COMPLETED"
        or result.get("engineInvocations")
        or result.get("adoptedFiles")
        or result.get("cachedFiles") != result.get("selectedFiles")
    ):
        # A run answered entirely from valid cached results changes nothing about the case, so it
        # must not push an already verified or ready case back to ORGANIZING.
        update_local_case_waterline(
            args,
            work,
            state="ORGANIZING",
            local_status="ORGANIZING",
        )
    print(json.dumps(result, ensure_ascii=False))


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
            "brigadeName",
        },
        "case",
        errors,
    )
    if not isinstance(case.get("projectNo"), str) or not PROJECT_NO.fullmatch(case["projectNo"]):
        errors.append("case.projectNo 不合法")
    if case.get("brigadeCode") not in LOCAL_BRIGADES:
        errors.append("case.brigadeCode 不合法")
    elif case["brigadeCode"] in LOCAL_ONLY_BRIGADES:
        if case.get("brigadeName") != LOCAL_ONLY_BRIGADES[case["brigadeCode"]]:
            errors.append("本地大队案卷必须填写与来源一致的 brigadeName")
    elif "brigadeName" in case:
        errors.append("八个云端大队不得填写 brigadeName")
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
                    "repairSiteId",
                    "marketAccessMode",
                    "marketAccessRaw",
                    "certificateNumberRaw",
                    "qualityCheckRaw",
                    "qualityCheckPerformed",
                    "problemDescription",
                    "reinspectionApplied",
                    "reinspectionResult",
                    "initialProductRef",
                },
                f"{label}.products[{index}]",
                errors,
            )
            product_ref = product.get("clientRef")
            if not ref(product_ref) or product_ref in products:
                errors.append("产品 clientRef 重复或不合法")
            else:
                products[product_ref] = (stage, product)
            field_limits = {
                "name": 300,
                "modelSpec": 300,
                "nominalProducer": 300,
                "location": 300,
                "problemDescription": 2000,
                "marketAccessRaw": 5000,
                "certificateNumberRaw": 300,
                "qualityCheckRaw": 5000,
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
            if "initialProductRef" in product:
                if stage != "RECHECK":
                    errors.append("initialProductRef 只适用于 RECHECK 产品")
                elif not ref(product["initialProductRef"]):
                    errors.append("initialProductRef 不合法")
            if "method" in product and product["method"] not in METHODS:
                errors.append("产品 method 不合法")
            if "result" in product and product["result"] not in RESULTS:
                errors.append("产品 result 不合法")
            if "result" in product and "method" not in product:
                errors.append("产品 result 必须同时声明 method")
            if product.get("qualityCheckPerformed") == "NO" and ("result" in product or "method" in product):
                errors.append("未开展质量检查时不得声明质量检查 method/result")
            for key in ("onlineSale", "maintenance", "reinspectionApplied", "qualityCheckPerformed"):
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
    for product_ref, (product_stage, product) in products.items():
        target = product.get("initialProductRef")
        if target is None:
            continue
        if target == product_ref or target not in products or products[target][0] != "INITIAL_CHECK":
            errors.append(f"产品 {product_ref} 的 initialProductRef 必须指向本案 INITIAL_CHECK 产品")
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
    inventory = project_local_path_fields(inventory, work, args)
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
                for item in project_local_path_fields(read_json(split_path), work, args).get(
                    "items", []
                )
                if isinstance(item, dict)
            }
        )
    for entry in supplement_entries(args, work):
        if entry["relativePath"] in sources:
            raise RegistryError(f"补采文件名与 ZIP 成员冲突：{entry['relativePath']}")
        sources[entry["relativePath"]] = entry
    try:
        image_items = normalized_sources.load(work)
    except (ValueError, KeyError, OSError) as error:
        raise RegistryError(f"image-normalization.json 无法读取：{error}") from error
    for relative, item in image_items.items():
        reasons = normalized_sources.verify_item(work, item, image_source_bytes(work, sources))
        if reasons:
            raise RegistryError("图片规范化校验失败：\n- " + "\n- ".join(reasons))
        sources[relative] = {
            "relativePath": relative,
            "absolutePath": str((work / relative).resolve()),
            "sha256": item["outputSha256"],
            "mimeType": "application/pdf",
            "pageCount": 1,
            "sourceKind": "IMAGE_NORMALIZED",
        }
    try:
        office_items = office_normalized.load(work)
    except (ValueError, KeyError, OSError) as error:
        raise RegistryError(f"office-normalization.json 无法读取：{error}") from error
    for relative, item in office_items.items():
        reasons = office_normalized.verify_item(work, item, image_source_bytes(work, sources))
        if reasons:
            raise RegistryError("Word 规范化校验失败：\n- " + "\n- ".join(reasons))
        sources[relative] = {
            "relativePath": relative,
            "absolutePath": str((work / relative).resolve()),
            "sha256": item["outputSha256"],
            "mimeType": "application/pdf",
            "pageCount": item["outputPageCount"],
            "sourceKind": "OFFICE_NORMALIZED",
        }
    bound_sources = {item["sourceRelativePath"] for item in office_items.values()}
    loose = sorted(
        relative
        for relative, entry in sources.items()
        if entry.get("unparseablePdf") and relative not in bound_sources
    )
    if loose:
        raise RegistryError(
            "来源 PDF 无法解析且未经 normalize-office 绑定规范 PDF，不能组装："
            + "、".join(loose)
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
        if source and source.get("sourceKind") == "EXTERNAL_SUPPLEMENT":
            raise RegistryError(f"补采文件必须先经 split 生成规范化 PDF 后再引用：{source_rel}")
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
    located = local_layout(args, work)
    if located is not None:
        brigade = importlib.import_module(
            f"{__package__}.local_brigade" if __package__ else "local_brigade"
        )
        try:
            brigade.check_case(located[0], manifest["case"]["projectNo"], manifest["case"])
        except workspace_api().WorkspaceStateError as error:
            raise RegistryError(str(error)) from error
    write_json(work / "manifest.json", manifest)
    write_json(work / "upload-map.json", {"files": upload})
    update_local_case_waterline(
        args,
        work,
        state="PENDING_LOCAL_VERIFY",
        local_status="READY_FOR_LOCAL_VERIFY",
        project_no=manifest["case"]["projectNo"],
    )


def load_inputs(
    manifest_path: str, map_path: str, args: argparse.Namespace | None = None
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path, manifest, upload = (
        Path(manifest_path).resolve(),
        read_json(Path(manifest_path).resolve()),
        read_json(Path(map_path).resolve()).get("files"),
    )
    if not isinstance(upload, dict):
        raise RegistryError("upload-map.files 必须是对象")
    upload = project_upload_paths(upload, path, args)
    errors = validate_manifest(manifest, upload)
    if errors:
        raise RegistryError("本地校验失败：\n- " + "\n- ".join(errors))
    return path, manifest, upload


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


def validate_command(args: argparse.Namespace) -> None:
    _path, _manifest, _upload = (
        load_inputs(args.manifest, args.upload_map, args)
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


def configured_layout_for_path(path: Path, args: argparse.Namespace | None = None) -> Any:
    workspace = workspace_api()
    options = workspace_kwargs(args) if args is not None else {}
    if (
        not options.get("work_root")
        and not options.get("config_path")
        and not workspace.default_workspace_config_path().exists()
    ):
        return None
    try:
        _config, layout = workspace.resolve_workspace(**options)
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    return layout if inside(path.resolve(), layout.root) else None


def project_local_path_fields(
    value: Any, path: Path, args: argparse.Namespace | None = None
) -> Any:
    layout = configured_layout_for_path(path, args)
    return layout.project_recorded_paths(value) if layout else value


def project_upload_paths(
    upload: dict[str, Any], manifest_path: Path, args: argparse.Namespace | None = None
) -> dict[str, Any]:
    layout = configured_layout_for_path(manifest_path, args)
    if layout is None:
        return upload
    result = {}
    for key, value in upload.items():
        try:
            result[key] = (
                str(layout.resolve_recorded_path(value)) if isinstance(value, str) else value
            )
        except workspace_api().WorkspaceStateError:
            result[key] = value
    return result


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


def workspace_switch_mirror_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        result = workspace.switch_mirror_access(
            work_root=args.work_root, config_path=getattr(args, "workspace_config", None)
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


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
        _config, layout = workspace.resolve_workspace(
            **workspace_kwargs(args), create_layout=False, require_download_dir=False
        )
        output = workspace.export_waterline_xlsx(layout)
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps({"status": "exported", "path": str(output)}, ensure_ascii=False))


def ledger_status_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    source_year = ledger_source_year(args)
    try:
        _config, layout = workspace.resolve_workspace(
            **workspace_kwargs(args), create_layout=False, require_download_dir=False
        )
        result = (
            workspace.workspace_progress(layout, batch_id=args.batch_id)
            if getattr(args, "scope", "batch") == "batch"
            else ledger_views.ledger_view(
                layout,
                view=getattr(args, "view", "all"),
                batch_id=args.batch_id,
                source_year=source_year,
            )
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def ledger_lifecycle_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    today = None
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError as error:
            raise RegistryError("--today 必须是 YYYY-MM-DD") from error
    try:
        _config, layout = workspace.resolve_workspace(
            **workspace_kwargs(args), create_layout=False, require_download_dir=False
        )
        result = ledger_views.lifecycle_view(layout, from_year=args.from_year, today=today)
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def ledger_source_year(args: argparse.Namespace) -> int | None:
    scope = getattr(args, "scope", "all")
    year = getattr(args, "year", None)
    if year is not None and scope != "source-year":
        raise RegistryError("--year 仅用于 --scope source-year")
    if scope == "source-year":
        if getattr(args, "batch_id", None):
            raise RegistryError("来源年度查询不能同时限定批次")
        return year if year is not None else ledger_views.coverage_api.execution_year()
    return None


def ledger_index_history_command(args: argparse.Namespace) -> None:
    _config, layout = workspace_api().resolve_workspace(
        **workspace_kwargs(args), create_layout=False, require_download_dir=False
    )
    print(json.dumps(ledger_views.index_history(layout, apply=args.apply), ensure_ascii=False))


def ledger_report_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    source_year = ledger_source_year(args)
    try:
        _config, layout = workspace.resolve_workspace(
            **workspace_kwargs(args), create_layout=False, require_download_dir=False
        )
        if getattr(args, "scope", "batch") == "batch":
            progress = workspace.workspace_progress(layout, batch_id=args.batch_id)
            markdown = render_report(progress)
            case_count = progress["waterline"]["caseCount"]
        else:
            progress = ledger_views.ledger_view(
                layout,
                view=getattr(args, "view", "all"),
                batch_id=args.batch_id,
                source_year=source_year,
            )
            markdown = ledger_views.render_ledger_report(
                progress, include_all=getattr(args, "all_cases", False)
            )
            case_count = progress["counts"]["cases"]
        if args.output:
            target = write_report(Path(args.output), layout, markdown)
            result = {
                "status": "exported",
                "reportPath": str(target),
                "caseCount": case_count,
            }
        else:
            result = {"status": "read_only", "markdown": markdown}
    except (workspace.WorkspaceStateError, ReportingError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_scan_plan_command(args: argparse.Namespace) -> None:
    workspace = workspace_api()
    try:
        layout = resolve_source_layout(args)
        result = ledger_views.scan_plan(
            layout,
            getattr(args, "batch_id", None),
            mode=args.mode,
            year=args.year,
            limit=args.limit,
            package_months=args.package_window_months,
            package_refresh_days=args.package_refresh_days,
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_coverage_command(args: argparse.Namespace) -> None:
    try:
        result = ledger_views.coverage_api.annual_coverage(
            resolve_source_layout(args), args.year, details=args.details
        )
    except workspace_api().WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    if not args.details:
        result = ledger_views.coverage_api.summary(result)
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
    if not getattr(args, "filter_json", None) and not getattr(args, "acceptance_sample", False):
        raise RegistryError(
            "正式采集必须提供实际查询筛选；先完成年度案卷基线，"
            "再按 source scan-plan 使用近三个月窗口，页面回读后保存"
        )
    source = source_intake_api()
    workspace = workspace_api()
    try:
        _, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        if getattr(args, "filter_json", None):
            requested_filters = source._json_input(Path(args.filter_json), "筛选条件")
            if requested_filters.get("selectionMode") == "RECENT_DOCUMENT_ACTIVITY":
                coverage = ledger_views.coverage_api.annual_coverage(layout)
                if not coverage["complete"]:
                    raise RegistryError(
                        "年度来源总水位尚未对齐，请先完成年度基线；既有批次仍可续跑"
                    )
        result = source.begin_capture(
            layout,
            filter_json=Path(args.filter_json) if getattr(args, "filter_json", None) else None,
            batch_id=getattr(args, "batch_id", None),
            origin=args.origin,
            scope="acceptance" if getattr(args, "acceptance_sample", False) else "all",
        )
        args.batch_id = result["batchId"]
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
        supersede_carried=bool(getattr(args, "supersede_carried", False)),
    )


def source_add_observation_command(args: argparse.Namespace) -> None:
    if not str(args.source_url).strip():
        raise RegistryError("详情来源地址不能为空")
    run_source_action(
        args,
        "add_followup_observation",
        batch_id=args.batch_id,
        rwid=args.rwid,
        detail=Path(args.detail_json),
        source_url=args.source_url,
        screenshot=[Path(item) for item in args.screenshot],
        observed_at=args.observed_at,
    )


def source_admit_observed_case_command(args: argparse.Namespace) -> None:
    run_source_action(
        args,
        "admit_observed_material_case",
        batch_id=args.batch_id,
        observation=Path(args.detail_json),
    )


def source_attach_detail_segments_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    try:
        result = source.attach_detail_segments(
            resolve_source_layout(args), batch_id=args.batch_id, index_path=Path(args.index_json)
        )
    except (source.SourceIntakeError, workspace_api().WorkspaceStateError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_accept_manual_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.manual_intake" if __package__ else "manual_intake"
    )
    try:
        result = module.accept(resolve_source_layout(args), read_json(Path(args.review_json)))
    except (workspace_api().WorkspaceStateError, source_intake_api().SourceIntakeError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_resolve_manual_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.manual_intake" if __package__ else "manual_intake"
    )
    try:
        result = module.resolve_pending(
            resolve_source_layout(args), args.project, getattr(args, "intake_id", None)
        )
    except (workspace_api().WorkspaceStateError, source_intake_api().SourceIntakeError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_acknowledge_gaps_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.manual_intake" if __package__ else "manual_intake"
    )
    try:
        result = module.acknowledge_gaps(resolve_source_layout(args), args.project, args.reason)
    except workspace_api().WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def _source_module(name: str) -> Any:
    return importlib.import_module(f"{__package__}.{name}" if __package__ else name)


def _run_source_module(args: argparse.Namespace, module: str, function: str, *call: Any) -> None:
    workspace = workspace_api()
    try:
        result = getattr(_source_module(module), function)(resolve_source_layout(args), *call)
    except (workspace.WorkspaceStateError, source_intake_api().SourceIntakeError) as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def source_amend_archived_command(args: argparse.Namespace) -> None:
    _run_source_module(args, "amendment", "amend", args.project, args.reason)


def source_abandon_amendment_command(args: argparse.Namespace) -> None:
    _run_source_module(args, "amendment", "abandon", args.project, args.reason)


def source_attach_supplement_command(args: argparse.Namespace) -> None:
    _run_source_module(args, "supplements", "attach", args.project, args.file, args.receipt)


def source_link_products_command(args: argparse.Namespace) -> None:
    _run_source_module(args, "product_links", "attach", args.project, args.links_file)


def source_rebind_evidence_command(args: argparse.Namespace) -> None:
    _run_source_module(
        args,
        "manual_intake",
        "rebind_classification_evidence",
        args.project,
        args.intake_id,
        args.reason,
    )


def source_rebind_retained_command(args: argparse.Namespace) -> None:
    _run_source_module(args, "classification_rebind", "rebind", args.project, args.reason)


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


def explicit_refresh(
    source: Any, layout: Any, batch_id: str, rwid: str, reason: str
) -> dict[str, Any]:
    """Explicit audited refresh of the current package: same gate as the packaging adapter."""

    module_name = f"{__package__}.package_source_case" if __package__ else "package_source_case"
    packager = importlib.import_module(module_name)
    _, state = source._load_capture(layout, batch_id)
    record = (state.get("records") or {}).get(str(rwid)) or {}
    project = record.get("projectNo") or (record.get("detail") or {}).get("projectNo")
    if not project:
        raise RegistryError("显式更新找不到该 RWID 在批次内的项目编号")
    try:
        return packager._validated_refresh(layout, str(project).strip().upper(), reason, None)
    except source.SourceIntakeError as error:
        raise RegistryError(str(error)) from error


def source_await_download_command(args: argparse.Namespace) -> None:
    source = source_intake_api()
    workspace = workspace_api()
    try:
        config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        refresh: dict[str, Any] = {}
        if args.refresh_reason:
            if not args.attach:
                raise RegistryError("显式更新必须同时使用 --attach")
            refresh = explicit_refresh(source, layout, args.batch_id, args.rwid, args.refresh_reason)
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
            refresh_of_sha=refresh.get("ofSha"),
            refresh_reason=refresh.get("reason"),
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
    ocr.add_argument(
        "--batch",
        action="store_true",
        help="同案所选文件共用一次 MinerU；总期限为单文件期限乘待处理文件数",
    )
    ocr.add_argument(
        "--adopt-existing",
        metavar="RECEIPTS_JSON",
        help="OcrAdoptionReceiptsV1：校验摘要后采纳本案既有识别结果；不启动引擎、不发请求",
    )
    add_workspace_resolution_options(ocr)
    ocr.set_defaults(func=ocr_command, workspace_required=True)
    split = sub.add_parser("split")
    split.add_argument("--work-dir", required=True)
    split.add_argument("--plan", required=True)
    split.add_argument(
        "--incremental",
        action="store_true",
        help="在既有 split-index 之后追加新条目；相同条目复用，冲突或未收尾记录拒绝",
    )
    add_workspace_resolution_options(split)
    split.set_defaults(func=split_command, workspace_required=True)
    normalize = sub.add_parser(
        "normalize-images", help="把选定的 PNG/JPEG 原图忠实转换为单页 PDF 并保存源映射"
    )
    normalize.add_argument("--work-dir", required=True)
    normalize.add_argument("--relative-path", action="append")
    add_workspace_resolution_options(normalize)
    normalize.set_defaults(func=normalize_images_command, workspace_required=True)
    normalize_office = sub.add_parser(
        "normalize-office",
        help="绑定操作者转换并目视核对的规范 PDF（来源为 Word 或无法结构解析的 PDF）",
    )
    normalize_office.add_argument("--work-dir", required=True)
    normalize_office.add_argument("--relative-path", required=True)
    normalize_office.add_argument("--converted-pdf", required=True)
    normalize_office.add_argument("--converter-name", required=True)
    normalize_office.add_argument("--converter-version", required=True)
    normalize_office.add_argument("--rendering-check", required=True)
    add_workspace_resolution_options(normalize_office)
    normalize_office.set_defaults(func=normalize_office_command, workspace_required=True)
    compose = sub.add_parser("compose")
    compose.add_argument("--work-dir", required=True)
    compose.add_argument("--case-data", required=True)
    add_workspace_resolution_options(compose)
    compose.set_defaults(func=compose_command, workspace_required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--upload-map")
    validate.set_defaults(func=validate_command)

    workspace = sub.add_parser("workspace", help="配置或检查业务工作根")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_configure = workspace_sub.add_parser("configure", help="写入稳定的本地工作根配置")
    workspace_configure.add_argument("--work-root", required=True)
    workspace_configure.add_argument("--download-dir")
    workspace_configure.add_argument("--workspace-config")
    workspace_configure.set_defaults(func=workspace_configure_command)
    workspace_mirror = workspace_sub.add_parser(
        "switch-mirror", help="核对两端完整文件树后仅切换主操作入口，保留原业务身份与证据"
    )
    workspace_mirror.add_argument("--work-root", required=True)
    workspace_mirror.add_argument("--workspace-config")
    workspace_mirror.set_defaults(func=workspace_switch_mirror_command)
    workspace_doctor = workspace_sub.add_parser("doctor", help="只读检查工作根与布局")
    add_workspace_resolution_options(workspace_doctor)
    workspace_doctor.set_defaults(func=workspace_doctor_command)

    source = sub.add_parser("source", help="接收已登录浏览器生成的本地采集物")
    source_sub = source.add_subparsers(dest="source_command", required=True)

    source_facts = source_sub.add_parser(
        "facts-validate", help="只读核验分层来源原文与本地证据绑定；不裁定源事实、不写云端"
    )
    source_facts.add_argument("snapshot", help="SourceFactsSnapshotV1 JSON")
    source_facts.add_argument("--evidence-root", help="可选：核验本地证据路径和摘要")
    source_facts.set_defaults(func=source_facts_validate_command)

    source_ingest = source_sub.add_parser(
        "facts-ingest",
        help="把已核验的来源观察追加写入本案 source-facts.json 源事实层；不改人工层、不写云端",
    )
    source_ingest.add_argument("snapshot", help="SourceFactsSnapshotV1 JSON")
    source_ingest.add_argument("--work-dir", required=True)
    source_ingest.add_argument("--evidence-root", required=True, help="本地证据根；路径和摘要必须可核验")
    add_workspace_resolution_options(source_ingest)
    source_ingest.set_defaults(func=source_facts_ingest_command, workspace_required=True)

    source_plan = source_sub.add_parser(
        "scan-plan", help="只读规划年度基线或近三个月增量及未完成断点"
    )
    add_workspace_resolution_options(source_plan)
    source_plan.add_argument("--batch-id", help="可选：对账已稳定的增量清单")
    source_plan.add_argument(
        "--mode", choices=("auto", "annual-baseline", "recent"), default="auto"
    )
    source_plan.add_argument("--year", type=int, help="来源年度；默认上海执行年")
    source_plan.add_argument(
        "--limit", type=int, default=10, help="最多展开多少项来源动作；默认 10"
    )
    source_plan.add_argument(
        "--package-window-months", type=int, default=ledger_views.PACKAGE_WINDOW_MONTHS_DEFAULT,
        help="只读列出近期不合格未结案包更新候选的滚动自然月窗口；默认 2",
    )
    source_plan.add_argument(
        "--package-refresh-days", type=int, default=ledger_views.PACKAGE_REFRESH_DAYS_DEFAULT,
        help="候选的最小更新间隔天数；默认 7（可配置，不建调度）",
    )
    source_plan.set_defaults(func=source_scan_plan_command)
    source_coverage = source_sub.add_parser(
        "coverage", help="只读逐项核对年度来源覆盖，不把文书数当案卷数"
    )
    add_workspace_resolution_options(source_coverage)
    source_coverage.add_argument("--year", type=int)
    source_coverage.add_argument(
        "--details", action="store_true", help="展开全部来源身份及未关联项"
    )
    source_coverage.set_defaults(func=source_coverage_command)

    source_begin = source_sub.add_parser("begin", help="创建 BrowserCaptureV1 采集批次")
    add_workspace_resolution_options(source_begin)
    source_begin.add_argument(
        "--filter-json",
        help="正式批次必填；保存来源页面实际回读的筛选条件",
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
    source_detail.add_argument(
        "--supersede-carried",
        action="store_true",
        help="仅在旧详情经来源批次证明为继承且现场新观察通过全部校验时，移为前代并解除对应冲突",
    )
    source_detail.set_defaults(func=source_add_detail_command)

    source_observation = source_sub.add_parser(
        "add-observation",
        help="已绑包案卷的后续真实观察：另存可信观察证据，不改已绑详情、案卷包、完成凭证和归档代际",
    )
    add_workspace_resolution_options(source_observation)
    source_observation.add_argument("--batch-id", required=True)
    source_observation.add_argument("--rwid", required=True)
    source_observation.add_argument("--detail-json", required=True)
    source_observation.add_argument("--source-url", required=True)
    source_observation.add_argument(
        "--screenshot", action="append", required=True, help="完整详情截图；长页分段时重复传入同一次观察的全部分段"
    )
    source_observation.add_argument(
        "--observed-at", required=True, help="真实回源观察时间(ISO 8601，必须带时区偏移，原值保留；业务观察日按上海时区解释)"
    )
    source_observation.set_defaults(func=source_add_observation_command)

    source_admit = source_sub.add_parser(
        "admit-observed-case", help="已证单案材料准入：现场详情原文证明本案四身份与 [产] 产品根，不计年度清单"
    )
    add_workspace_resolution_options(source_admit)
    source_admit.add_argument("--batch-id", required=True)
    source_admit.add_argument("--detail-json", required=True)
    source_admit.set_defaults(func=source_admit_observed_case_command)

    source_segments = source_sub.add_parser(
        "attach-detail-segments",
        help="把一案已有的多段详情截图按覆盖索引绑定为永久 D 引用(首段须为已登记截图；只增叶，同索引重入 0 写)",
    )
    add_workspace_resolution_options(source_segments)
    source_segments.add_argument("--batch-id", required=True)
    source_segments.add_argument("--index-json", required=True)
    source_segments.set_defaults(func=source_attach_detail_segments_command)

    manual_intake = source_sub.add_parser(
        "accept-manual", help="接收已逐图核清的手动原始 ZIP 和详情截图"
    )
    add_workspace_resolution_options(manual_intake)
    manual_intake.add_argument("--review-json", required=True)
    manual_intake.set_defaults(func=source_accept_manual_command)

    resolve_manual = source_sub.add_parser(
        "resolve-manual",
        help="比对待比对的手动追加材料与既有代际：相同则收口，不同则开新代际并可续接",
    )
    add_workspace_resolution_options(resolve_manual)
    resolve_manual.add_argument("--project", required=True)
    resolve_manual.add_argument("--intake-id")
    resolve_manual.set_defaults(func=source_resolve_manual_command)

    acknowledge = source_sub.add_parser(
        "acknowledge-gaps", help="按当前用户口径保留来源缺件并收口现有材料"
    )
    add_workspace_resolution_options(acknowledge)
    acknowledge.add_argument("--project", required=True)
    acknowledge.add_argument("--reason", required=True)
    acknowledge.set_defaults(func=source_acknowledge_gaps_command)

    amend_archived = source_sub.add_parser(
        "amend-archived",
        help="对已归档案卷（同一 ZIP）显式开启字段/分类修订：保留父代并建立独立新代际工作材料",
    )
    add_workspace_resolution_options(amend_archived)
    amend_archived.add_argument("--project", required=True)
    amend_archived.add_argument("--reason", required=True, help="修订原因（写入总账与回执）")
    amend_archived.set_defaults(func=source_amend_archived_command)

    abandon_amendment = source_sub.add_parser(
        "abandon-amendment",
        help="放弃未完成的修订：恢复父代完成记录，修订副本移入保留目录而不删除",
    )
    add_workspace_resolution_options(abandon_amendment)
    abandon_amendment.add_argument("--project", required=True)
    abandon_amendment.add_argument("--reason", required=True)
    abandon_amendment.set_defaults(func=source_abandon_amendment_command)

    attach_supplement = source_sub.add_parser(
        "attach-supplement",
        help="绑定真实外加补采文件及其来源回执（不属于 ZIP 成员），供拆分/组装/核验使用",
    )
    add_workspace_resolution_options(attach_supplement)
    attach_supplement.add_argument("--project", required=True)
    attach_supplement.add_argument("--file", required=True, help="补采文件绝对路径")
    attach_supplement.add_argument(
        "--receipt", required=True, help="SourceDocumentRecoveryV1 回执绝对路径"
    )
    attach_supplement.set_defaults(func=source_attach_supplement_command)

    link_products = source_sub.add_parser(
        "link-products",
        help="已归档案卷：登记经文书核对的复查产品→初查产品对应(ProductLinkReviewV1)，不改归档清单",
    )
    add_workspace_resolution_options(link_products)
    link_products.add_argument("--project", required=True)
    link_products.add_argument(
        "--links-file",
        required=True,
        help="含 reviewer 与 links 的 JSON 绝对路径（字段见 manual-intake.md）",
    )
    link_products.set_defaults(func=source_link_products_command)

    rebind_evidence = source_sub.add_parser(
        "rebind-evidence",
        help="分类证据文件已丢失时，以本案真实手动交件回执显式新绑定；不改旧摘要、不构造旧证据",
    )
    add_workspace_resolution_options(rebind_evidence)
    rebind_evidence.add_argument("--project", required=True)
    rebind_evidence.add_argument("--intake-id", required=True)
    rebind_evidence.add_argument("--reason", required=True)
    rebind_evidence.set_defaults(func=source_rebind_evidence_command)

    rebind_retained = source_sub.add_parser(
        "rebind-retained-observation",
        help="分类证据文件被同案后续采集追加改写（摘要变化）时，凭保留的早期观察与变更轨迹显式新绑定；不改旧摘要",
    )
    add_workspace_resolution_options(rebind_retained)
    rebind_retained.add_argument("--project", required=True)
    rebind_retained.add_argument("--reason", required=True)
    rebind_retained.set_defaults(func=source_rebind_retained_command)

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
    source_await.add_argument(
        "--refresh-reason",
        choices=("RECENT_UNQUALIFIED", "BAD_LOCAL", "SPECIAL_ISSUE", "USER_REQUEST"),
        help="受审计的显式更新：新包作为新一代接入，旧代原件保留；须同时 --attach",
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
        help="显式限定正式批次；scope=all 不指定时包含所有已知历史案卷",
    )
    ledger_status.add_argument("--scope", choices=("all", "batch", "source-year"), default="all")
    ledger_status.add_argument(
        "--year", type=int, help="来源年度范围，默认执行年；不是初查统计年份"
    )
    ledger_status.add_argument(
        "--view", choices=("all", "unqualified", "unknown", "unfinished"), default="all"
    )
    ledger_status.set_defaults(func=ledger_status_command)
    ledger_lifecycle = ledger_sub.add_parser(
        "lifecycle",
        help="只读：2026+ 产品案按生命周期规则分为可直接复用/仅本地可补/必须核源，并列出具体原因",
    )
    add_workspace_resolution_options(ledger_lifecycle)
    ledger_lifecycle.add_argument("--from-year", type=int, default=2026)
    ledger_lifecycle.add_argument("--today", help="固定判断日（YYYY-MM-DD，上海日历日）；默认今天")
    ledger_lifecycle.set_defaults(func=ledger_lifecycle_command)
    ledger_index = ledger_sub.add_parser(
        "index-history", help="复用本地正式批次清单补充总账索引，不扫描来源网页"
    )
    add_workspace_resolution_options(ledger_index)
    ledger_index.add_argument("--apply", action="store_true")
    ledger_index.set_defaults(func=ledger_index_history_command)
    ledger_report = ledger_sub.add_parser(
        "report",
        help="只读汇总事实源；可显式输出 Markdown 核验摘要",
    )
    add_workspace_resolution_options(ledger_report)
    ledger_report.add_argument("--batch-id")
    ledger_report.add_argument("--scope", choices=("all", "batch", "source-year"), default="all")
    ledger_report.add_argument("--year", type=int)
    ledger_report.add_argument(
        "--view", choices=("all", "unqualified", "unknown", "unfinished"), default="all"
    )
    ledger_report.add_argument("--output", help="核验记录目录内的新 .md 文件；省略时只返回摘要")
    ledger_report.add_argument(
        "--all-cases", action="store_true", help="显式展开所有案卷明细；默认只列活动待办及异常"
    )
    ledger_report.set_defaults(func=ledger_report_command)

    fields = sub.add_parser("fields", help="案卷字段抽取结果的只读校验（原 filler 规则）")
    fields_sub = fields.add_subparsers(dest="fields_command", required=True)
    fields_validate = fields_sub.add_parser(
        "validate", help="校验抽取 JSON；不改输入、不写表、不改名或删除任何图片"
    )
    fields_validate.add_argument("extractions", help="抽取结果 JSON（数组或含 cases 的对象）")
    fields_validate.add_argument("--image-dir", help="核对 source_files 存在性的截图目录")
    fields_validate.set_defaults(func=fields_validate_command)

    feishu_sync = sub.add_parser(
        "feishu-source-sync",
        help="按 fieldId/record_id 白名单把来源字段增量写入飞书（官方 lark-cli；缺省只计划，不写云）",
    )
    feishu_sync.add_argument("--spec", required=True, help="FeishuSourceSyncSpecV1 JSON")
    feishu_sync.add_argument(
        "--execute", action="store_true", help="真实云写；spec.mode 也必须为 execute，缺一不可"
    )
    feishu_sync.add_argument(
        "--reconcile", action="store_true", help="只读云回读，对账早先未决写入；spec.mode 须为 reconcile"
    )
    feishu_sync.add_argument("--cli", help="官方 lark-cli 路径；省略用模块默认")
    feishu_sync.add_argument(
        "--ledger-dir", help="追加式账本目录；按精确目标建档，与 spec.logDir 无关；省略用模块默认"
    )
    feishu_sync.add_argument(
        "--user-risk-acceptance",
        help="UserRiskAcceptanceV1：用户亲自对精确意图接受迟到写风险的记录；spec 含 humanRelease 时必填，助手不得自署",
    )
    feishu_sync.set_defaults(func=feishu_source_sync_command)
    feishu_build = sub.add_parser(
        "feishu-source-build",
        help="由权威已存详情(采集批次)+fresh云全页/八队/schema 生成带字段依据的基础字段 V3 创建说明与 V2 补空说明；只读云，不写",
    )
    for flag in ("--capture", "--capture-sha", "--waterline", "--contract-v3", "--contract-v3-sha", "--contract-v9", "--contract-v9-sha",
                 "--routes", "--routes-sha", "--base-token", "--profile", "--out-dir", "--log-root"):
        feishu_build.add_argument(flag, required=True)
    feishu_build.add_argument("--identity", default="user", choices=["user", "bot"])
    feishu_build.add_argument("--ledger-dir", required=True, help="唯一既有 P18 账本(必填)：未决 UNKNOWN 与未获用户接受的 human_release 的目标保留未决，不生成说明")
    feishu_build.add_argument("--user-accepted-releases", help="UserRiskAcceptanceV1：用户亲自接受的精确意图清单")
    feishu_build.add_argument("--batch-size", type=int, default=20)
    feishu_build.add_argument("--cli", help="官方 lark-cli 路径；省略用模块默认")
    feishu_build.set_defaults(func=feishu_source_build_command)

    local_verify = sub.add_parser(
        "local-verify",
        help="本地核验清单、文件与原包并归档；不访问服务器、飞牛或任何认证",
    )
    local_verify.add_argument("--project", required=True, help="项目编号；对应工作区/<项目编号>")
    local_verify.add_argument(
        "--check-only",
        action="store_true",
        help="只在内存中核验并输出摘要，不写凭证、不归档、不改总账",
    )
    local_verify.add_argument(
        "--no-archive", action="store_true", help="只形成本地核验凭证，暂不归档"
    )
    add_workspace_resolution_options(local_verify)
    local_verify.set_defaults(func=local_verify_command)

    confirm_docs = sub.add_parser(
        "confirm-documents",
        help="由 split-index 与确认依据形成已确认文书子集清单；不等于全案核验或归档",
    )
    confirm_docs.add_argument("--project", required=True)
    confirm_docs.add_argument("--confirmation", required=True, help="确认清单 JSON（projectNo/items/held；依据为现有证据文件及摘要）")
    confirm_docs.add_argument("--check-only", action="store_true", help="只核验并输出摘要，不写文件")
    add_workspace_resolution_options(confirm_docs)
    confirm_docs.set_defaults(func=confirm_documents_command)

    archive_subset = sub.add_parser(
        "archive-document-subset",
        help="把已确认文书子集只复制到已处理案卷/<项目>/文书子集/<内容清单SHA>；不移动原包/工作区，不冒全案完成",
    )
    archive_subset.add_argument("--project", required=True)
    archive_subset.add_argument("--check-only", action="store_true", help="只核验源与目标，不复制、不写总账")
    add_workspace_resolution_options(archive_subset)
    archive_subset.set_defaults(func=archive_document_subset_command)

    for name, replacement in RETIRED_COMMANDS.items():
        retired = sub.add_parser(name, help=f"已退役：{replacement}")
        retired.add_argument("legacy_args", nargs=argparse.REMAINDER)
        retired.set_defaults(func=retired_command, retired=True, retired_name=name)
    ledger_retired = ledger_sub.add_parser("reconcile", help="已退役：旧服务器对账")
    ledger_retired.add_argument("legacy_args", nargs=argparse.REMAINDER)
    ledger_retired.set_defaults(func=retired_command, retired=True, retired_name="ledger reconcile")
    return parser


RETIRED_REPLACEMENT = (
    "案卷不再上传到服务器或飞牛，只保留本地原件与记录；统计数据由飞书维护。"
    "请改用 inventory/ocr/split/compose/validate 整理后执行 local-verify 核验并归档。"
    "历史上传凭证与状态文件仍可只读查看。"
)
RETIRED_COMMANDS = {
    "upload": "旧服务器上传",
    "upload-batch": "旧服务器批量上传",
    "verify": "旧服务器与飞牛核验",
    "verify-existing": "旧服务器既有案卷核验",
    "supplement": "旧服务器补录",
    "supplement-batch": "旧服务器批量补录",
    "init-auth-config": "旧服务器认证配置初始化",
}


def retired_command(args: argparse.Namespace) -> None:
    """Fail before authentication, network access, timing records or any business write."""
    raise RegistryError(f"命令 {args.retired_name} 已退役。{RETIRED_REPLACEMENT}")



def source_facts_validate_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.source_facts" if __package__ else "source_facts"
    )
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise RegistryError(f"无法读取来源快照：{error}") from error
    report = module.validate_snapshot(
        snapshot, Path(args.evidence_root) if args.evidence_root else None
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise RegistryError("来源快照绑定有误，未改动输入或水位")


def source_facts_ingest_command(args: argparse.Namespace) -> None:
    """Store validated source observations for one project in the source layer only."""
    module = importlib.import_module(
        f"{__package__}.source_facts" if __package__ else "source_facts"
    )
    work = Path(args.work_dir).resolve()
    enforce_local_workspace_preflight(args, work)
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise RegistryError(f"无法读取来源快照：{error}") from error
    report = module.validate_snapshot(snapshot, Path(args.evidence_root))
    if report["status"] != "ok":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise RegistryError("来源快照绑定有误，未写入源事实层")
    store_path = work / module.STORE_FILE
    store = read_json(store_path) if store_path.is_file() else None
    try:
        merged, counts = module.merge_snapshot(store, snapshot, work.name, utc_now())
    except module.SourceFactsConflict as error:
        raise RegistryError(str(error)) from error
    if counts["added"]:
        write_json(store_path, merged)
    print(
        json.dumps(
            {
                "project": work.name,
                **counts,
                "stationActivity": module.station_activity(merged),
                "storePath": str(store_path),
                "manualVerificationTouched": False,
                "cloudWritten": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def feishu_source_sync_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.feishu_source_sync" if __package__ else "feishu_source_sync"
    )
    try:
        report = module.run(
            Path(args.spec),
            execute_flag=args.execute,
            reconcile_flag=args.reconcile,
            cli=args.cli or module.DEFAULT_CLI,
            ledger_dir=Path(args.ledger_dir) if args.ledger_dir else None,
            user_risk_acceptance=Path(args.user_risk_acceptance) if getattr(args, "user_risk_acceptance", None) else None,
        )
    except module.SyncError as error:
        raise RegistryError(f"飞书来源同步已拒绝：{error}") from error
    except module.RuntimeSyncError as error:
        # 3 = 已发起外部调用后失败或结果未知；与 2(外部调用之前即拒绝)分开，便于调用方对账而不是重放
        print(f"ERROR: 飞书来源同步失败或结果未知，已保留证据并封锁目标，未重试：{error}", file=sys.stderr)
        raise SystemExit(3) from error
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if isinstance(report, dict) and report.get("status") == "PARTIAL":
        print("WARNING: 同步结果为 PARTIAL，存在被挡实体(见 blockedEntities)，退出码 4，不代表整体完成", file=sys.stderr)
        raise SystemExit(4)


def feishu_source_build_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.feishu_source_sync" if __package__ else "feishu_source_sync"
    )
    target = {"baseToken": args.base_token, "profile": args.profile, "identity": args.identity}
    try:
        transport = module.LarkCliTransport(args.cli or module.DEFAULT_CLI, args.profile, args.identity)
        report = module.build_basic_specs(
            capture_path=Path(args.capture), capture_sha=args.capture_sha,
            waterline_path=Path(args.waterline),
            v3_path=Path(args.contract_v3), v3_sha=args.contract_v3_sha,
            v9_path=Path(args.contract_v9), v9_sha=args.contract_v9_sha,
            routes_path=Path(args.routes), routes_sha=args.routes_sha,
            target=target, transport=transport, out_dir=Path(args.out_dir), log_root=Path(args.log_root),
            batch_size=args.batch_size,
            ledger_dir=Path(args.ledger_dir) if args.ledger_dir else None,
            user_accepted_releases=Path(args.user_accepted_releases) if args.user_accepted_releases else None,
        )
    except module.SyncError as error:
        raise RegistryError(f"基础字段说明生成已拒绝：{error}") from error
    except module.RuntimeSyncError as error:
        print(f"ERROR: fresh 云读取失败，未生成说明：{error}", file=sys.stderr)
        raise SystemExit(3) from error
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "OK":
        print("WARNING: 有被挡实体或路由问题(PARTIAL)，其余说明已生成；退出码 4，不代表全部可同步", file=sys.stderr)
        raise SystemExit(4)


def fields_validate_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.field_validation" if __package__ else "field_validation"
    )
    try:
        cases = module.load_cases(Path(args.extractions))
    except (OSError, ValueError) as error:
        raise RegistryError(f"无法读取抽取文件：{error}") from error
    image_dir = Path(args.image_dir) if args.image_dir else None
    report = module.validate_cases(cases, image_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise RegistryError("字段校验发现错误，详见输出的 errors")


def local_verify_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.local_completion" if __package__ else "local_completion"
    )
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        project = args.project
        if not PROJECT_NO.fullmatch(project):
            raise RegistryError("项目编号格式不合法")
        existing = (workspace.load_waterline(layout).get("cases") or {}).get(project) or {}
        if args.check_only and module.local_validity.is_local_completion(existing):
            reasons = module.local_validity.validate_local_completion(layout, project, existing)
            result = {
                "status": "NEEDS_REVIEW" if reasons else "ALREADY_COMPLETED",
                "projectNo": project,
                "reasons": reasons,
                "wrote": False,
            }
        elif args.check_only:
            proof = module.build_verification(sys.modules[__name__], layout, project)
            result: dict[str, Any] = {
                "status": "CHECK_ONLY",
                "projectNo": project,
                "files": len(proof["files"]),
                "manifestSha256": proof["manifestSha256"],
                "packageSha256": proof["packageSha256"],
                "wrote": False,
            }
        else:
            result = module.verify_and_archive(
                sys.modules[__name__], layout, project, archive=not args.no_archive
            )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))
    if result.get("status") == "NEEDS_REVIEW":
        raise RegistryError("本地完成需复核，已保留全部证据且未改动任何文件")


def confirm_documents_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.local_completion" if __package__ else "local_completion"
    )
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        project = args.project
        if not PROJECT_NO.fullmatch(project):
            raise RegistryError("项目编号格式不合法")
        confirmation = read_json(Path(args.confirmation))
        document = module.build_confirmed_documents(sys.modules[__name__], layout, project, confirmation)
        result: dict[str, Any] = {
            "status": "CHECK_ONLY" if args.check_only else "CONFIRMED",
            "projectNo": project,
            "items": len(document["items"]),
            "held": len(document["held"]),
            "contentListSha256": document["contentListSha256"],
            "caseLevelVerified": False,
            "wrote": False,
        }
        if not args.check_only:
            path, status = module.write_confirmed_documents(sys.modules[__name__], layout, project, document)
            result.update(file=str(path), write=status, wrote=status != "REUSED")
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def archive_document_subset_command(args: argparse.Namespace) -> None:
    module = importlib.import_module(
        f"{__package__}.local_completion" if __package__ else "local_completion"
    )
    workspace = workspace_api()
    try:
        _config, layout = workspace.resolve_workspace(**workspace_kwargs(args), create_layout=False)
        project = args.project
        if not PROJECT_NO.fullmatch(project):
            raise RegistryError("项目编号格式不合法")
        confirmed_file = layout.work_case_dir(project) / module.CONFIRMED_NAME
        if not confirmed_file.is_file():
            raise RegistryError("没有 confirmed-documents.json，请先执行 confirm-documents")
        confirmed = read_json(confirmed_file)
        result = workspace.archive_document_subset(
            layout, project, confirmed=confirmed, confirmed_file=confirmed_file, check_only=args.check_only
        )
    except workspace.WorkspaceStateError as error:
        raise RegistryError(str(error)) from error
    print(json.dumps(result, ensure_ascii=False))


def record_command_timing(
    args: argparse.Namespace, started_at: str, duration: float, outcome: str
) -> None:
    """Diagnostics only for commands that already mutate their explicit local scope.

    Queries, offline validation/dry-runs and planning commands never write timing files.
    Missing end-of-command workspaces (e.g. already archived) are not recreated.
    """
    command = args.command
    if getattr(args, "retired", False) or command not in {
        "inventory",
        "ocr",
        "split",
        "compose",
        "normalize-images",
        "normalize-office",
        "source",
    }:
        return
    if getattr(args, "dry_run", False) or getattr(args, "plan", False) is True:
        return
    category = "processing"
    operation = command
    if command == "source":
        subcommand = args.source_command
        if subcommand in {"tail-cursor", "scan-plan"} or not getattr(args, "batch_id", None):
            return
        layout = resolve_source_layout(args)
        directory = layout.batch_dir(args.batch_id)
        if not (directory / "browser-capture.json").is_file():
            return
        scope_key = f"batch:{args.batch_id}"
        operation += "/" + subcommand
        category = "networkWait" if subcommand == "await-download" else "processing"
    else:
        raw_work = getattr(args, "work_dir", None)
        if not raw_work:
            manifest_path = getattr(args, "manifest", None)
            if not manifest_path:
                return
            raw_work = str(Path(manifest_path).resolve().parent)
        directory = Path(raw_work).resolve()
        resolved = resolve_local_case_workspace(args, directory)
        if resolved is None or not directory.is_dir():
            return
        _workspace, layout, project = resolved
        scope_key = f"project:{project}"
        if command in {"upload", "verify", "supplement"}:
            category = "unclassified"  # Total duration cannot distinguish network from processing.
    record_timing(
        directory,
        layout.root,
        scope_key=scope_key,
        operation=operation,
        category=category,
        started_at=started_at,
        duration=duration,
        outcome=outcome,
    )


def retired_name(raw: list[str]) -> str | None:
    """Recognize retired commands before argparse so legacy flags cannot cause side effects."""
    if raw and raw[0] in RETIRED_COMMANDS:
        return raw[0]
    if len(raw) >= 2 and raw[0] == "ledger" and raw[1] == "reconcile":
        return "ledger reconcile"
    return None


def main(argv: list[str] | None = None) -> int:
    configure_utf8()
    raw = list(sys.argv[1:] if argv is None else argv)
    legacy = retired_name(raw)
    if legacy is not None:
        print(f"ERROR: 命令 {legacy} 已退役。{RETIRED_REPLACEMENT}", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    started_at, started = utc_now(), time.monotonic()
    outcome = "FAILED"
    try:
        args.func(args)
        outcome = "COMPLETED"
    except RegistryError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        outcome = "CANCELLED"
        print("已中断；已确认的进度保留，未完成操作未自动重试", file=sys.stderr)
        return 130
    finally:
        try:
            record_command_timing(args, started_at, time.monotonic() - started, outcome)
        except (RegistryError, ReportingError, OSError, ValueError):
            print("计时记录未保存；业务结果未回滚，历史缺失耗时仍为未知", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
