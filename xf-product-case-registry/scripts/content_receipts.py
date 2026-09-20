"""Body verification receipts, independent from the frozen V6 upload protocol."""

from __future__ import annotations

from typing import Any


def file_identity(remote: dict[str, Any]) -> dict[str, Any]:
    generation = remote.get("contentGeneration")
    size = str(remote.get("sizeBytes", ""))
    if (
        not isinstance(remote.get("id"), str)
        or not isinstance(remote.get("sha256"), str)
        or type(generation) is not int
        or generation < 1
        or not size.isdecimal()
    ):
        raise ValueError("服务器未提供完整文件身份和内容代际，不能保存正文核验回执")
    return {
        "fileId": remote["id"],
        "sha256": remote["sha256"],
        "contentGeneration": generation,
        "sizeBytes": size,
    }


def resume_receipt(
    previous: dict,
    *,
    project_no: str,
    case_id: str,
    origin: str,
    manifest_sha: str,
    identities: dict[str, dict],
) -> dict:
    same_case = (
        previous.get("schemaVersion") == "ContentVerificationV1"
        and previous.get("projectNo") == project_no
        and previous.get("caseId") == case_id
        and previous.get("origin") == origin
    )
    old_files = previous.get("files", {}) if same_case else {}
    reusable = {
        ref: old_files[ref]
        for ref, identity in identities.items()
        if isinstance(old_files.get(ref), dict)
        and old_files[ref].get("verifiedAt")
        and old_files[ref].get("verifiedAgainstManifestSha256")
        and all(old_files[ref].get(key) == value for key, value in identity.items())
    }
    receipt = {
        "schemaVersion": "ContentVerificationV1",
        "projectNo": project_no,
        "caseId": case_id,
        "origin": origin,
        "manifestSha256": manifest_sha,
        "files": reusable,
    }
    if (
        same_case
        and previous.get("manifestSha256") == manifest_sha
        and previous.get("completedAt")
        and len(reusable) == len(identities) == len(old_files)
    ):
        receipt["completedAt"] = previous["completedAt"]
    return receipt


def identities_from_directory(value: Any, wanted: set[str]) -> dict[str, dict]:
    result = {}

    def walk(item):
        if isinstance(item, dict):
            if item.get("id") in wanted and isinstance(item.get("sha256"), str):
                identity = file_identity(item)
                if identity["fileId"] in result and result[identity["fileId"]] != identity:
                    raise ValueError("目录重复文件身份不一致")
                result[identity["fileId"]] = identity
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return result
