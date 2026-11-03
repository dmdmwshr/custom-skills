"""Local-only case completion: verify files against the manifest and original package, then archive.

LocalVerificationV1 never claims a server upload or NAS check. It binds the stable project number,
the original package digest, the manifest digest, every normalized PDF (digest and page count),
the split chain and the source identity so the ledger and source intake can trust a completion
without any retired service.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import (
        amendment,
        local_brigade,
        local_validity,
        manual_intake,
        normalized_sources,
        supplements,
    )
    from . import workspace_state as ws
else:
    import amendment
    import local_brigade
    import local_validity
    import manual_intake
    import normalized_sources
    import supplements
    import workspace_state as ws

SCHEMA_VERSION = "LocalVerificationV1"
PROOF_NAME = "local-verification.json"
_VOLATILE = {"verifiedAt"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _raw_package_digest(api: Any, layout: Any, work: Path, project: str) -> tuple[str, dict]:
    """Recompute the collected original material digest; it must still equal the inventory."""
    inventory = api.read_json(work / "inventory.json")
    if inventory.get("inventoryVersion") != 3 or not inventory.get("files"):
        raise api.RegistryError("本地核验需要已校验的完整材料清点（inventory.json）")
    raw = ws._validated_workspace_path(
        layout.resolve_recorded_path(inventory["sourceInput"]),
        layout.pending_case_dir(project),
        "当前原始材料",
        must_exist=True,
    )
    kind = inventory.get("containerKind")
    if kind == "ARCHIVE" and raw.is_file():
        digest = api.file_sha256(raw)
    elif kind == "DIRECTORY" and raw.is_dir():
        ws._assert_safe_tree(raw, layout.root, "当前原始材料目录")
        digest = api.directory_hash(
            [
                {"relativePath": p.relative_to(raw).as_posix(), "sha256": api.file_sha256(p)}
                for p in raw.rglob("*")
                if p.is_file()
            ]
        )
    else:
        raise api.RegistryError("原始材料种类与清点记录不一致")
    if digest != inventory.get("packageSha256"):
        raise api.RegistryError("原始材料自清点后已变化，不形成本地核验凭证")
    return digest, {
        "containerKind": kind,
        "sourceInput": raw.relative_to(layout.root).as_posix(),
        "inventorySha256": _sha(work / "inventory.json"),
    }


def _split_chain(api: Any, layout: Any, work: Path, project: str) -> dict[str, Any] | None:
    path = work / "split-index.json"
    if not path.is_file():
        return None
    index = layout.project_recorded_paths(api.read_json(path))
    inventory = layout.project_recorded_paths(api.read_json(work / "inventory.json"))
    sources = {f["relativePath"]: f for f in inventory.get("files", []) if isinstance(f, dict)}
    sources.update({e["relativePath"]: e for e in supplements.entries(layout, project)})
    items = index.get("items")
    if not isinstance(items, list) or not items:
        raise api.RegistryError("split-index.json 没有拆分记录")
    for item in items:
        target = Path(item["absolutePath"]).resolve()
        if not api.inside(target, (work / "normalized").resolve()) or not target.is_file():
            raise api.RegistryError(f"拆分件缺失或越界：{item.get('relativePath')}")
        sha, pages = api.pdf_info(target)
        if sha != item.get("sha256") or pages != item.get("pageCount"):
            raise api.RegistryError(f"拆分件摘要或页数与拆分记录不符：{item.get('relativePath')}")
        for part in api.pdf_parts(item):
            origin = sources.get(part["sourceRelativePath"])
            if origin is None:
                raise api.RegistryError("拆分来源不在材料清点中")
            origin_sha, origin_pages = api.pdf_info(Path(origin["absolutePath"]))
            if origin_sha != origin.get("sha256") or part["pageEnd"] > origin_pages:
                raise api.RegistryError("拆分来源自清点后已变化或页码越界")
    return {"indexSha256": _sha(path), "items": len(items)}


def _field_resolution(
    api: Any, work: Path, project: str, amendment_id: str | None = None
) -> dict[str, Any] | None:
    path = work / "field-resolution.json"
    if not path.is_file():
        if amendment_id:
            raise api.RegistryError(
                "修订必须提供重新核对的 field-resolution.json（含 amendmentId）"
            )
        return None
    value = api.read_json(path)
    if amendment_id and value.get("amendmentId") != amendment_id:
        raise api.RegistryError("field-resolution.json 没有绑定当前修订（amendmentId 不符）")
    unresolved = value.get("unresolvedFields") or value.get("unresolved") or []
    if value.get("projectNo") not in (None, project) or unresolved:
        raise api.RegistryError("字段裁决仍有未解决项或项目编号不一致，暂不形成本地核验凭证")
    return {"sha256": _sha(path), "unresolvedFields": 0}


def _image_sources(api: Any, layout: Any, work: Path, project: str) -> dict[str, dict[str, Any]]:
    entries = {
        f["relativePath"]: f
        for f in layout.project_recorded_paths(api.read_json(work / "inventory.json")).get(
            "files", []
        )
    }
    entries.update({e["relativePath"]: e for e in supplements.entries(layout, project)})
    return entries


def _image_proof(
    api: Any, layout: Any, work: Path, project: str, files: dict[str, Any]
) -> dict[str, Any]:
    try:
        index = normalized_sources.load(work)
    except (ValueError, KeyError, OSError) as error:
        raise api.RegistryError(f"image-normalization.json 无法读取：{error}") from error
    used = {
        ref: index[item["workRelativePath"]]
        for ref, item in files.items()
        if item["workRelativePath"] in index
    }
    if not used:
        return {}
    entries = _image_sources(api, layout, work, project)
    reasons: list[str] = []
    for item in used.values():
        reasons += normalized_sources.verify_item(work, item, api.image_source_bytes(work, entries))
    if reasons:
        raise api.RegistryError(
            "图片规范化与源图片不一致：\n- " + "\n- ".join(sorted(set(reasons)))
        )
    return {
        ref: {
            k: item[k]
            for k in (
                "sourceRelativePath",
                "sourceKind",
                "sourceSha256",
                "sourceSizeBytes",
                "pixelWidth",
                "pixelHeight",
                "pageSizePt",
                "method",
                "outputSha256",
                "converter",
            )
        }
        for ref, item in sorted(used.items())
    }


def build_verification(api: Any, layout: Any, project: str, *, now: str | None = None) -> dict:
    """Build the proof from current disk state; never mutates any file."""
    work = layout.work_case_dir(project)
    ws._assert_safe_tree(work, layout.root, "活动项目工作区")
    manifest_path = work / "manifest.json"
    manifest = api.read_json(manifest_path)
    upload_map = api.read_json(work / "upload-map.json").get("files")
    if not isinstance(upload_map, dict):
        raise api.RegistryError("upload-map.json 的 files 必须是对象")
    upload_map = {
        ref: str(layout.resolve_recorded_path(path)) if isinstance(path, str) else path
        for ref, path in upload_map.items()
    }
    errors = api.validate_manifest(manifest, upload_map)
    if errors:
        raise api.RegistryError("清单本地校验失败：\n- " + "\n- ".join(errors))
    if manifest["case"]["projectNo"] != project:
        raise api.RegistryError("清单项目编号与工作区不一致")

    package_sha, package = _raw_package_digest(api, layout, work, project)
    if package_sha != manifest["packageSha256"]:
        raise api.RegistryError("清单 packageSha256 与当前原始材料不一致")

    record = (ws.load_waterline(layout).get("cases") or {}).get(project)
    source = (record or {}).get("source") or {}
    local_brigade.check_case(layout, project, manifest["case"])
    supplement_problems = supplements.verify_bindings(layout, project)
    if supplement_problems:
        raise api.RegistryError("补采材料校验失败：" + "；".join(supplement_problems))
    open_amendment = (record or {}).get("amendment") or {}
    open_amendment = open_amendment if open_amendment.get("status") == "OPEN" else {}
    if not source.get("packageSha256") and package["containerKind"] == "ARCHIVE":
        raise api.RegistryError("总账缺少该案来源包摘要，来源映射无法绑定")
    if source.get("packageSha256") and source["packageSha256"] != package_sha:
        raise api.RegistryError("总账记录的来源包摘要与当前原始材料不一致，保留断点待核对")
    if source.get("materialGaps") and not manual_intake.gaps_acknowledged(source):
        raise api.RegistryError("来源材料仍有明确缺口且未经用户口径确认，暂不形成本地核验凭证")
    if source.get("changePending"):
        raise api.RegistryError("来源变化尚未收口，暂不形成本地核验凭证")

    files: dict[str, Any] = {}
    for item in manifest["files"]:
        path = Path(upload_map[item["clientRef"]]).resolve()
        if not api.inside(path, work.resolve()):
            raise api.RegistryError(f"文件不在本案工作区内：{item['clientRef']}")
        if not path.is_file():
            raise api.RegistryError(f"文件缺失：{item['clientRef']}")
        sha, pages = api.pdf_info(path)
        if sha != item["sha256"] or pages != item["pageCount"]:
            raise api.RegistryError(f"文件摘要或页数与清单不一致：{item['clientRef']}")
        files[item["clientRef"]] = {
            "relativePath": item["relativePath"],
            "workRelativePath": path.relative_to(work.resolve()).as_posix(),
            "sha256": sha,
            "pageCount": pages,
            "sizeBytes": path.stat().st_size,
        }
    if not files:
        raise api.RegistryError("清单没有任何文件，不能形成本地核验凭证")

    amendment_proof = None
    if open_amendment:
        parent_problems = amendment.parent_problems(layout, open_amendment["parent"])
        if parent_problems:
            raise api.RegistryError("修订父代已变化：" + "；".join(parent_problems))
        parent_manifest = api.read_json(
            layout.resolve_recorded_path(open_amendment["parent"]["workspacePath"])
            / "manifest.json"
        )
        amendment_proof = {
            "id": open_amendment["id"],
            "reason": open_amendment["reason"],
            "parentGeneration": open_amendment["parent"]["generation"],
            "parentManifestSha256": open_amendment["parent"]["manifestSha256"],
            "parentPackageSha256": open_amendment["parent"]["packageSha256"],
            "changes": amendment.summarize_changes(parent_manifest, manifest),
        }
    supplement_items = [
        {
            "relativePath": entry["relativePath"],
            "sha256": entry["sha256"],
            "sourceType": entry["sourceType"],
            "sourceDocumentId": entry["sourceDocumentId"],
            "receiptSha256": entry["receiptSha256"],
        }
        for entry in supplements.entries(layout, project)
    ]
    case = manifest["case"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "LOCAL_VERIFIED",
        "projectNo": project,
        "verifiedAt": now or ws._utc_now(),
        "manifestSha256": _sha(manifest_path),
        "packageSha256": package_sha,
        "package": package,
        "sourceBinding": {
            "packageSha256": source.get("packageSha256"),
            "packageRelativePath": source.get("packageRelativePath"),
            "intakeMode": source.get("intakeMode"),
            "rwid": source.get("rwid"),
        },
        "files": files,
        "split": _split_chain(api, layout, work, project),
        "fieldResolution": _field_resolution(
            api, work, project, open_amendment.get("id") if open_amendment else None
        ),
        "supplements": supplement_items,
        "imageNormalization": _image_proof(api, layout, work, project, files),
        "brigade": {
            "code": case["brigadeCode"],
            "scope": "LOCAL_ONLY"
            if case["brigadeCode"] in local_brigade.LOCAL_ONLY_BRIGADES
            else "CLOUD_EIGHT",
            "cloudReportingPermissionChanged": False,
        },
        **({"amendment": amendment_proof} if amendment_proof else {}),
        "serverUploadClaimed": False,
        "nasVerificationClaimed": False,
    }


def _comparable(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in _VOLATILE}


def write_verification(api: Any, layout: Any, project: str, proof: dict) -> tuple[Path, str]:
    """Persist the proof next to the manifest; identical content is reused, differences are kept."""
    path = layout.work_case_dir(project) / PROOF_NAME
    payload = (json.dumps(proof, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        previous = _json(path)
        if _comparable(previous) == _comparable(proof):
            return path, "REUSED"
        stamp = ws._timestamp_slug(previous.get("verifiedAt") or ws._utc_now())
        archived = path.with_name(f"local-verification.superseded-{stamp}.json")
        if archived.exists():
            raise api.RegistryError("旧本地核验凭证的保留副本已存在，拒绝覆盖")
        path.replace(archived)
        status = "SUPERSEDED_PREVIOUS"
    else:
        status = "CREATED"
    ws._atomic_bytes(path, payload, overwrite=False)
    return path, status


def verify_and_archive(api: Any, layout: Any, project: str, *, archive: bool) -> dict[str, Any]:
    """Run the full local lifecycle tail: verify, record, then archive (unless disabled)."""
    record = (ws.load_waterline(layout).get("cases") or {}).get(project)
    if record is None:
        raise api.RegistryError("总账没有该项目，请先通过来源接件建立身份")
    if local_validity.is_local_completion(record):
        reasons = local_validity.validate_local_completion(layout, project, record)
        if not reasons:
            return {
                "status": "ALREADY_COMPLETED",
                "projectNo": project,
                "archive": record.get("archive"),
            }
        # Evidence stays untouched: nothing is rewritten, repaired or re-archived here.
        return {
            "status": "NEEDS_REVIEW",
            "projectNo": project,
            "issue": local_validity.UNTRUSTED_ISSUE,
            "reasons": reasons,
            "archive": record.get("archive"),
        }
    proof = build_verification(api, layout, project)
    path, write_status = write_verification(api, layout, project, proof)
    proof = _json(path)
    result: dict[str, Any] = {
        "status": "LOCAL_VERIFIED",
        "projectNo": project,
        "proof": str(path),
        "proofWrite": write_status,
        "files": len(proof["files"]),
        "archive": None,
    }
    if archive:
        result["archive"] = ws.archive_verified_case(
            layout,
            project,
            verification=proof,
            manifest_sha256=proof["manifestSha256"],
            package_sha256=proof["packageSha256"],
            verified_at=proof["verifiedAt"],
        )
        result["status"] = "COMPLETED"
    else:
        ws.upsert_case(
            layout,
            project,
            state="LOCAL_VERIFIED_PENDING_ARCHIVE",
            local={"status": "LOCAL_VERIFIED"},
            errorSummary=None,
        )
    return result
