"""Real external source supplements (e.g. VISIBLE_SOURCE_PREVIEW) bound next to the original ZIP.

A supplement is a file that the source system lists but that is not a member of the received
ZIP. It is stored byte-for-byte in the case's pending folder under ``补采材料/`` together with
its real source receipt (``SourceDocumentRecoveryV1``), bound in the ledger by digest, and offered
to split/compose/local-verify as a source of its own kind. It is never presented as a ZIP member,
never changes the recorded original package digest and never weakens a missing-file check.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

if __package__:
    from . import manual_intake
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import manual_intake
    import source_intake as source
    import workspace_state as ws

DIRECTORY_NAME = "补采材料"
RECEIPT_SCHEMA = "SourceDocumentRecoveryV1"
SOURCE_TYPES = {"VISIBLE_SOURCE_PREVIEW"}
SOURCE_KIND = "EXTERNAL_SUPPLEMENT"
_NAME = re.compile(r"^[^\x00-\x1f/\\:*?\"<>|]{1,120}$")
_SHA = re.compile(r"^sha256:[a-f0-9]{64}$")
_SUFFIX = {".pdf", ".png", ".jpg", ".jpeg"}
_ACTIVE_STATES = {"DISCOVERED", "PENDING_ORGANIZATION", "ORGANIZING", "PENDING_LOCAL_VERIFY"}


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ws.WorkspaceStateError(f"{label}必须使用绝对路径")
    ws._reject_reparse_chain(path, label)
    if not path.is_file():
        raise ws.WorkspaceStateError(f"{label}不存在或不是文件：{path}")
    return path


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ws.WorkspaceStateError("补采回执无法读取或不是 JSON") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != RECEIPT_SCHEMA:
        raise ws.WorkspaceStateError(f"补采回执必须是 {RECEIPT_SCHEMA}")
    return value


def _page_count(path: Path, data: bytes) -> int | None:
    if not data.startswith(b"%PDF-"):
        return None
    try:
        return len(PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception as error:  # noqa: BLE001 - any parser failure means the file is unusable
        raise ws.WorkspaceStateError("补采 PDF 无法读取") from error


def _case_rwids(record: dict[str, Any]) -> set[str]:
    state = record.get("source") or {}
    found = {str(key) for key in (state.get("annualIdentitiesByRwid") or {})}
    for key in ("rwid",):
        if state.get(key):
            found.add(str(state[key]))
    return found


def _known_screenshot_digests(layout: Any, record: dict[str, Any], pending: Path) -> set[str]:
    """Detail screenshots that genuinely belong to this case (current and archived)."""
    digests: set[str] = set()
    folders = [pending]
    for entry in (record.get("history") or {}).get("completions") or []:
        if entry.get("originalPath"):
            folders.append(layout.resolve_recorded_path(entry["originalPath"]))
    archive = record.get("archive") or {}
    if archive.get("originalPath"):
        folders.append(layout.resolve_recorded_path(archive["originalPath"]))
    for folder in folders:
        if folder.is_dir():
            digests.update(source._sha256(p) for p in folder.glob("*.png"))
    state = record.get("source") or {}
    for binding in (state.get("manualIntakes") or {}).values():
        if binding.get("screenshotSha256"):
            digests.add(binding["screenshotSha256"])
    return digests


def _zip_member_digests(layout: Any, record: dict[str, Any]) -> set[str]:
    relative = (record.get("source") or {}).get("packageRelativePath")
    if not relative:
        return set()
    package = layout.root / relative
    if not package.is_file():
        return set()
    return set(manual_intake._member_digests(package).values())


def attach(layout: Any, project: str, file_path: str, receipt_path: str) -> dict[str, Any]:
    """Bind one real supplement file and its real source receipt to the case's pending folder."""
    project = ws._project_no(project)
    file = _absolute(file_path, "补采文件")
    receipt_file = _absolute(receipt_path, "补采回执")
    suffix = file.suffix.lower()
    if suffix not in _SUFFIX or not _NAME.fullmatch(file.name) or file.name.startswith("."):
        raise ws.WorkspaceStateError(
            "补采文件名或类型不受支持（仅 PDF/PNG/JPEG，且不得含路径字符）"
        )
    data = file.read_bytes()
    file_sha = source._sha256(file)
    receipt = _read_receipt(receipt_file)
    if receipt.get("projectNo") != project:
        raise ws.WorkspaceStateError("补采回执的项目编号与目标案卷不一致，拒绝串案")
    if receipt.get("sha256") != file_sha:
        raise ws.WorkspaceStateError("补采文件摘要与回执不一致（缺失、被改动或换文件）")
    pages = _page_count(file, data)
    if pages is not None and receipt.get("pageCount") not in (None, pages):
        raise ws.WorkspaceStateError("补采 PDF 页数与回执不一致")
    download = receipt.get("download") or {}
    if download.get("bytes") not in (None, len(data)):
        raise ws.WorkspaceStateError("补采文件大小与回执不一致")
    source_type = receipt.get("sourceType", "VISIBLE_SOURCE_PREVIEW")
    if source_type not in SOURCE_TYPES:
        raise ws.WorkspaceStateError("补采来源类型不受支持")
    document_id, title = (
        str(receipt.get("sourceDocumentId") or ""),
        str(receipt.get("sourceDocumentTitle") or "").strip(),
    )
    if not document_id.isdigit() or not title:
        raise ws.WorkspaceStateError("补采回执缺少来源文书标识或标题")
    shot = (receipt.get("detailScreenshot") or {}).get("sha256")
    if not isinstance(shot, str) or not _SHA.fullmatch(shot):
        raise ws.WorkspaceStateError("补采回执缺少来源详情截图摘要")
    with ws._waterline_lock(layout):
        data_all = ws.load_waterline(layout)
        record = data_all.get("cases", {}).get(project)
        if not record:
            raise ws.WorkspaceStateError("总账没有该项目")
        if record.get("state") not in _ACTIVE_STATES:
            raise ws.WorkspaceStateError("案卷不在待整理阶段；已完成案卷请先开启修订再补采")
        rwid = str(receipt.get("rwid") or "")
        if not rwid or rwid not in _case_rwids(record):
            raise ws.WorkspaceStateError("补采回执的来源记录标识不属于本案，拒绝串案")
        pending = layout.pending_case_dir(project)
        if not pending.is_dir():
            raise ws.WorkspaceStateError("案卷没有待处理原始目录，无法放置补采材料")
        if shot not in _known_screenshot_digests(layout, record, pending):
            raise ws.WorkspaceStateError("补采回执引用的详情截图不属于本案已保存的原始截图")
        if file_sha in _zip_member_digests(layout, record):
            raise ws.WorkspaceStateError("该文件内容已是原 ZIP 成员，不属于补采，拒绝重复登记")
        key = file_sha.removeprefix("sha256:")[:12]
        existing = (record.get("source") or {}).get("supplements") or {}
        receipt_sha = source._sha256(receipt_file)
        for other_key, other in existing.items():
            if other.get("sourceDocumentId") == document_id and other_key != key:
                raise ws.WorkspaceStateError("同一来源文书已绑定其他摘要的补采文件，拒绝覆盖")
        target_dir = pending / DIRECTORY_NAME
        ws._validated_workspace_path(target_dir, layout.root, "补采材料目录", must_exist=False)
        stem = file.stem.removesuffix(f"_{key}")  # historical receipts already carry the digest
        target = target_dir / f"{stem}_{key}{suffix}"
        receipt_target = target_dir / f"{stem}_{key}.receipt.json"
        if key in existing:
            old = existing[key]
            if old.get("receiptSha256") != receipt_sha:
                raise ws.WorkspaceStateError("同一补采文件已绑定不同的回执，拒绝覆盖")
            problems = _verify_one(layout, key, old)
            if problems:
                raise ws.WorkspaceStateError("已登记补采材料无法对账：" + "；".join(problems))
            return {"status": "REUSED", "projectNo": project, "key": key, "file": str(target)}
        target_dir.mkdir(exist_ok=True)
        ws._assert_safe_tree(target_dir, layout.root, "补采材料目录")
        source._copy_immutable(file, target)
        source._copy_immutable(receipt_file, receipt_target)
        if source._sha256(target) != file_sha or source._sha256(receipt_target) != receipt_sha:
            raise ws.WorkspaceStateError("补采副本与来源摘要不一致")
        binding = {
            "sourceKind": SOURCE_KIND,
            "sourceType": source_type,
            "sourceDocumentId": document_id,
            "title": title,
            "rwid": rwid,
            "detailScreenshotSha256": shot,
            "fileRelativePath": target.relative_to(layout.root).as_posix(),
            "fileSha256": file_sha,
            "sizeBytes": len(data),
            "pageCount": pages,
            "receiptRelativePath": receipt_target.relative_to(layout.root).as_posix(),
            "receiptSha256": receipt_sha,
            "attachedAt": ws._utc_now(),
        }
        ws._deep_merge(record, {"source": {"supplements": {key: binding}}})
        ws._save_waterline_unlocked(layout, data_all)
    return {
        "status": "ATTACHED",
        "projectNo": project,
        "key": key,
        "file": str(target),
        "binding": binding,
    }


def _verify_one(layout: Any, key: str, binding: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    file = layout.root / binding.get("fileRelativePath", "")
    receipt = layout.root / binding.get("receiptRelativePath", "")
    try:
        ws._reject_reparse_chain(file, "补采文件")
        ws._reject_reparse_chain(receipt, "补采回执")
    except ws.WorkspaceStateError:
        return [f"补采 {key} 路径不安全"]
    if not file.is_file() or source._sha256(file) != binding.get("fileSha256"):
        problems.append(f"补采文件 {key} 缺失或被改动")
    if not receipt.is_file() or source._sha256(receipt) != binding.get("receiptSha256"):
        problems.append(f"补采回执 {key} 缺失或被改动")
    else:
        try:
            content = _read_receipt(receipt)
            if content.get("sha256") != binding.get("fileSha256"):
                problems.append(f"补采回执 {key} 与文件摘要不一致")
        except ws.WorkspaceStateError:
            problems.append(f"补采回执 {key} 无法读取")
    return problems


def verify_bindings(layout: Any, project: str) -> list[str]:
    record = ws.load_waterline(layout).get("cases", {}).get(project) or {}
    problems: list[str] = []
    for key, binding in ((record.get("source") or {}).get("supplements") or {}).items():
        problems.extend(_verify_one(layout, key, binding))
    return problems


def entries(layout: Any, project: str) -> list[dict[str, Any]]:
    """Verified supplement files shaped like inventory entries, each marked as its own kind."""
    record = ws.load_waterline(layout).get("cases", {}).get(project) or {}
    result = []
    for key, binding in sorted(((record.get("source") or {}).get("supplements") or {}).items()):
        problems = _verify_one(layout, key, binding)
        if problems:
            raise ws.WorkspaceStateError("补采材料校验失败：" + "；".join(problems))
        file = layout.root / binding["fileRelativePath"]
        mime = {".pdf": "application/pdf", ".png": "image/png"}.get(
            file.suffix.lower(), "image/jpeg"
        )
        item: dict[str, Any] = {
            "relativePath": f"{DIRECTORY_NAME}/{file.name}",
            "absolutePath": str(file),
            "sha256": binding["fileSha256"],
            "sizeBytes": binding["sizeBytes"],
            "mimeType": mime,
            "sourceKind": SOURCE_KIND,
            "sourceType": binding["sourceType"],
            "sourceDocumentId": binding["sourceDocumentId"],
            "receiptSha256": binding["receiptSha256"],
        }
        if binding.get("pageCount"):
            item["pageCount"] = binding["pageCount"]
        result.append(item)
    return result


def relocated(
    layout: Any, source_state: dict[str, Any], previous: Path, archived: Path
) -> dict[str, Any]:
    """Move supplement references with their folder after a verified archive move."""
    changed: dict[str, Any] = {}
    for key, binding in (source_state.get("supplements") or {}).items():
        values = dict(binding)
        for field in ("fileRelativePath", "receiptRelativePath"):
            old = layout.root / binding[field]
            if not old.is_relative_to(previous):
                continue
            new = archived / old.relative_to(previous)
            digest = (
                binding["fileSha256"] if field == "fileRelativePath" else binding["receiptSha256"]
            )
            if not new.is_file() or source._sha256(new) != digest:
                raise ws.WorkspaceStateError("归档后的补采材料摘要不一致")
            values[field] = new.relative_to(layout.root).as_posix()
        changed[key] = values
    return {"supplements": changed} if changed else {}
