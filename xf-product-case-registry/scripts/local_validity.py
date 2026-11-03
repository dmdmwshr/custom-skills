"""Shared deep validity check for a locally completed case (LocalVerificationV1).

One validator serves ``local-verify``, the ledger views and the source skip decisions, so a
completion only counts while the archived workspace PDFs, the original package, the proof, the
manifest and the receipt still agree. It is read-only, never repairs anything and returns the
reasons so a corrupted completion can be shown as "needs review" with its evidence untouched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import normalized_sources
    from . import workspace_state as ws
else:
    import normalized_sources
    import workspace_state as ws

UNTRUSTED_ISSUE = "LOCAL_COMPLETION_UNTRUSTED"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    entries = sorted(
        (p.relative_to(root).as_posix(), p) for p in root.rglob("*") if p.is_file()
    )
    for relative, path in entries:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(_sha(path).encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def is_local_completion(record: dict[str, Any]) -> bool:
    completion = record.get("completion") or {}
    return (
        record.get("state") == "COMPLETED"
        and completion.get("mode") == "LOCAL_V1"
        and completion.get("status") == "LOCAL_VERIFIED"
    )


def validate_local_completion(layout: Any, project: str, record: dict[str, Any]) -> list[str]:
    """Return the reasons a local completion is not trustworthy; an empty list means valid."""
    reasons: list[str] = []
    if not is_local_completion(record):
        return ["不是本地完成记录"]
    completion, archive = record.get("completion") or {}, record.get("archive") or {}
    source = record.get("source") or {}
    if source.get("changePending"):
        reasons.append("来源变化尚未收口（changePending）")
    if source.get("materialGaps") and not archive.get("sourceIssuesRetained"):
        reasons.append("来源缺口未经用户口径确认")
    if not archive.get("verificationRecord"):
        return reasons + ["缺少归档核验回执"]
    try:
        receipt_path = layout.resolve_recorded_path(archive["verificationRecord"])
        receipt = _json(receipt_path)
        embedded = receipt.get("localVerification") or {}
        work = layout.resolve_recorded_path(receipt.get("archivedWorkspace", ""))
        original = layout.resolve_recorded_path(archive.get("originalPath", ""))
    except (OSError, ValueError, ws.WorkspaceStateError, AttributeError, TypeError) as error:
        return reasons + [f"归档回执无法读取：{type(error).__name__}"]
    if not (
        receipt.get("recordVersion") == 2
        and receipt.get("completionMode") == "LOCAL_V1"
        and receipt.get("projectNo") == project
        and receipt.get("manifestSha256") == completion.get("manifestSha256")
        and receipt.get("packageSha256") == completion.get("packageSha256")
        and embedded.get("schemaVersion") == "LocalVerificationV1"
        and embedded.get("status") == "LOCAL_VERIFIED"
        and embedded.get("manifestSha256") == completion.get("manifestSha256")
        and embedded.get("packageSha256") == completion.get("packageSha256")
    ):
        reasons.append("回执与总账完成记录不一致")
    if not work.is_dir():
        return reasons + ["归档工作区缺失"]
    try:
        if _sha(work / "manifest.json") != completion.get("manifestSha256"):
            reasons.append("归档清单摘要已变化")
        if _json(work / "local-verification.json") != embedded:
            reasons.append("归档工作区的核验凭证与回执不一致")
        files = embedded.get("files")
        if not isinstance(files, dict) or not files:
            reasons.append("核验凭证没有文件清单")
            files = {}
        # The bound manifest, not the (mutable) proof, defines the complete file set.
        manifest_items = {
            item.get("clientRef"): item
            for item in (_json(work / "manifest.json").get("files") or [])
            if isinstance(item, dict)
        }
        proof_files = _json(work / "local-verification.json").get("files")
        if set(manifest_items) != set(files) or (
            isinstance(proof_files, dict) and set(proof_files) != set(manifest_items)
        ):
            missing = sorted(set(manifest_items) - set(files))
            extra = sorted(set(files) - set(manifest_items))
            reasons.append(f"核验凭证文件集合与清单不一致（缺 {missing}，多 {extra}）")
        if completion.get("fileCount") != len(manifest_items):
            reasons.append("完成记录 fileCount 与清单文件数不一致")
        for ref, item in files.items():
            bound = manifest_items.get(ref)
            if bound and (
                bound.get("sha256") != item.get("sha256")
                or bound.get("pageCount") != item.get("pageCount")
                or bound.get("relativePath") != item.get("relativePath")
            ):
                reasons.append(f"凭证文件项与清单绑定不符：{ref}")
        for ref, item in sorted(files.items()):
            target = (work / item.get("workRelativePath", "")).resolve()
            if not target.is_relative_to(work.resolve()) or not target.is_file():
                reasons.append(f"归档 PDF 缺失：{ref}")
            elif target.stat().st_size != item.get("sizeBytes") or _sha(target) != item.get(
                "sha256"
            ):
                reasons.append(f"归档 PDF 已损坏或被改动：{ref}")
        package = embedded.get("package") or {}
        inventory = work / "inventory.json"
        if not inventory.is_file() or _sha(inventory) != package.get("inventorySha256"):
            reasons.append("归档材料清点已变化或缺失")
        pending = layout.pending_case_dir(project)
        relative = (layout.root / package.get("sourceInput", "")).relative_to(pending)
        raw = original / relative
        if package.get("containerKind") == "ARCHIVE":
            if not raw.is_file():
                reasons.append("归档原始 ZIP 缺失")
            elif _sha(raw) != completion.get("packageSha256"):
                reasons.append("归档原始 ZIP 已损坏或被改动")
        elif package.get("containerKind") == "DIRECTORY":
            if not raw.is_dir():
                reasons.append("归档原始材料目录缺失")
            elif _directory_hash(raw) != completion.get("packageSha256"):
                reasons.append("归档原始材料目录已被改动")
        else:
            reasons.append("核验凭证的原始材料种类未知")
        reasons.extend(
            _extension_reasons(layout, project, receipt, embedded, work, original, files)
        )
    except (OSError, ValueError, ws.WorkspaceStateError, AttributeError, TypeError) as error:
        reasons.append(f"深度核验无法完成：{type(error).__name__}")
    return reasons


def _extension_reasons(
    layout: Any, project: str, receipt: dict[str, Any], embedded: dict[str, Any],
    work: Path, original: Path, files: dict[str, Any],
) -> list[str]:
    """Supplements, image normalization and amendment parents: older proofs simply lack them."""
    reasons: list[str] = []
    folder = original / "补采材料"
    for item in embedded.get("supplements") or []:
        file = original / item["relativePath"]
        if not file.is_file() or _sha(file) != item["sha256"]:
            reasons.append(f"归档补采文件缺失或被改动：{item['relativePath']}")
        if not any(_sha(r) == item["receiptSha256"] for r in folder.glob("*.receipt.json")):
            reasons.append(f"归档补采回执缺失或被改动：{item['relativePath']}")
    images = embedded.get("imageNormalization") or {}
    if images:
        try:
            index = normalized_sources.load(work)
        except (ValueError, KeyError, OSError):
            index = {}
            reasons.append("归档工作区的图片规范化记录无法读取")

        def source_bytes(item: dict[str, Any]) -> bytes:
            relative = item["sourceRelativePath"]
            for base in (work / "source", original):
                candidate = base / relative
                if candidate.is_file():
                    return candidate.read_bytes()
            raise OSError(relative)

        for ref, proof in images.items():
            item = index.get(files.get(ref, {}).get("workRelativePath", ""))
            if item is None or item.get("outputSha256") != proof.get("outputSha256"):
                reasons.append(f"图片规范化记录与核验凭证不符：{ref}")
                continue
            reasons.extend(normalized_sources.verify_item(work, item, source_bytes))
    parent = (receipt.get("amendment") or {}).get("parent")
    if parent:
        if __package__:
            from . import amendment
        else:
            import amendment
        reasons.extend(f"修订{problem}" for problem in amendment.parent_problems(layout, parent))
    return reasons
