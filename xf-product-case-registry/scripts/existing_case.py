"""Verify and archive an existing formal case without inventing an upload job."""

from __future__ import annotations

from pathlib import Path

if __package__:
    from . import ledger_reconcile
    from . import ledger_views as views
    from . import workspace_state as ws
else:
    import ledger_reconcile
    import ledger_views as views
    import workspace_state as ws


def collected_package(api, layout, work: Path, project: str) -> str:
    inventory = api.read_json(work / "inventory.json")
    if inventory.get("inventoryVersion") != 3 or not inventory.get("files"):
        raise api.RegistryError("归档需要已校验的完整材料清单")
    raw = ws._validated_workspace_path(
        Path(inventory["sourceInput"]),
        layout.pending_case_dir(project),
        "当前原始材料",
        must_exist=True,
    )
    if inventory.get("containerKind") == "ARCHIVE" and raw.is_file():
        digest = api.file_sha256(raw)
    elif inventory.get("containerKind") == "DIRECTORY" and raw.is_dir():
        ws._assert_safe_tree(raw, layout.root, "当前原始材料目录")
        files = [
            {"relativePath": p.relative_to(raw).as_posix(), "sha256": api.file_sha256(p)}
            for p in raw.rglob("*")
            if p.is_file()
        ]
        digest = api.directory_hash(files)
    else:
        raise api.RegistryError("原始材料种类与清单不一致")
    if digest != inventory.get("packageSha256"):
        raise api.RegistryError("原始材料自采集后已变化，暂不归档")
    return digest


def verify(api, args) -> dict:
    layout = api.resolve_source_layout(args)
    path = Path(args.manifest).resolve()
    manifest = api.read_json(path)
    errors = api.validate_manifest(manifest)
    if errors:
        raise api.RegistryError("\n".join(errors))
    project = manifest["case"]["projectNo"]
    record = ws.load_waterline(layout)["cases"].get(project)
    if not record:
        raise api.RegistryError("既有案卷尚未进入本地总账")
    work, _manifest, state, issues = views.case_inputs(layout, project, record)
    if state:
        raise api.RegistryError("已有上传断点，请使用 verify 续核原任务")
    if issues or path != (work / "manifest.json").resolve():
        raise api.RegistryError("清单必须对应总账的当前活动或明确归档工作区")
    digest = api.file_sha256(path)
    api_base, origin = api.origin_of(args.api_base)
    with api.httpx.Client(
        timeout=api.httpx.Timeout(args.timeout, read=max(args.timeout, 300.0)),
        follow_redirects=False,
    ) as client:
        identity, headers = api.authenticate_client(
            client,
            api_base,
            origin,
            manifest,
            api.secure_auth_config_path(Path(args.auth_config)),
        )

        def observe():
            result = ledger_reconcile.reconcile(
                api, client, api_base, origin, layout, [project], identity, apply=True
            )["cases"][0]
            if not result.get("applied") or not result.get("systemRegistered"):
                raise api.RegistryError("服务器未确认对应正式案卷，保留原记录")
            if "UNRESOLVED_CONFLICTS" in result["differences"]:
                raise api.RegistryError("正式案卷仍有未解决的文件冲突")
            return ws.load_waterline(layout)["cases"][project]

        observe()
        verification = api.verify_with_poll(
            client,
            api_base,
            manifest,
            headers,
            True,
            min(max(float(args.timeout), 1.0), 60.0),
            recall_wait_seconds=args.recall_wait_seconds,
            receipt_manifest_path=path,
        )
        if verification is None:
            raise api.RegistryError("飞牛落盘仍在进行，保留正文续核回执")
        record = observe()
        if api.file_sha256(path) != digest or (work / "upload-state.json").exists():
            raise api.RegistryError("核验期间本地案卷发生变化，未归档")
        row = views.describe(layout, project, record)
        if not row["stages"]["bodyVerified"]:
            raise api.RegistryError("正文回执与最新服务器文件身份或内容代际不一致")
        result = {
            "projectNo": project,
            "status": "VERIFIED",
            "verification": verification,
            "serverImportWrites": [],
            "uploadStateCreated": False,
        }
        if not args.archive:
            return result
        if (
            not all(
                row["stages"][key]
                for key in (
                    "sourceRegistered",
                    "materialsCollected",
                    "systemRegistered",
                    "bodyVerified",
                )
            )
            or row["sourceChanged"]
        ):
            raise api.RegistryError("正文已核验，但来源材料或变化仍未收口，暂不归档")
        if row["stages"]["archived"]:
            return {**result, "archiveReused": True, "archive": record["archive"]}
        if work != layout.work_case_dir(project):
            raise api.RegistryError("历史归档证据不完整，不能移动或覆盖历史目录")
        package_digest = collected_package(api, layout, work, project)
        evidence = {
            **verification,
            "status": "VERIFIED",
            "mode": "EXISTING_FORMAL_CASE",
            "origin": origin,
            "contentReceiptSha256": api.file_sha256(work / "content-verification.json"),
            "originalManifestPackageSha256": manifest["packageSha256"],
            "collectedPackageSha256": package_digest,
        }
        archived = ws.archive_verified_case(
            layout,
            project,
            upload_status="VERIFIED",
            verification=evidence,
            manifest_sha256=digest,
            package_sha256=package_digest,
        )
        return {**result, "archiveReused": False, "archive": archived}
