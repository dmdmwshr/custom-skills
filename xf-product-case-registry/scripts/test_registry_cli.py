from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

import scripts.registry_cli as cli
import scripts.source_intake as source
import scripts.workspace_state as workspace
from scripts.registry_cli import (
    RegistryError,
    compose_command,
    file_sha256,
    inventory_command,
    ocr_command,
    read_json,
    split_command,
    validate_manifest,
)

PROJECT = "32002207C202600033"
ROOT = Path(__file__).resolve().parents[1]


def test_browser_runbook_covers_marked_controls_latency_and_download_gate() -> None:
    runbook = (ROOT / "references" / "browser-acquisition.md").read_text(encoding="utf-8")

    for required in (
        "#tab-306",
        "查询统计",
        "消防产品监督检查记录",
        "SINGLE_CASE_DOWNLOAD_PROOF",
        "打\\s*包",
        "叶子",
        "source snapshot-downloads",
        "source await-download",
        "内置浏览器 Blob 本地恢复",
        "外部 Edge 响应流接收",
        "Network.getResponseBody",
        "Fetch.takeResponseBodyAsStream",
        "EDGE_CDP_STREAM_UNAVAILABLE",
        "native pipe message exceeds frame limit",
        "消息帧超限后控制通道可能短暂积压",
        "browser_blob_receiver.mjs",
        "执行模块与固定顺序",
        "关联项目／案卷名称",
        ".crdownload",
        "尾页优先",
        "evaluateAll",
        "快捷项“本年”",
        "当年 `01-01` 和 `12-31`",
        "窗口最大化",
        "50 条/页",
        "20 条/页",
        "60 秒",
        "120 秒",
        "法律文书查询",
        "键盘确认键",
        "tab.playwright.waitForEvent",
        "page.waitForEvent('download')",
        "下载交付不可用",
        "不得第三次点击",
        "只保留工作根中的原始 ZIP",
        "下载清理结果",
    ):
        assert required in runbook


def pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def manifest(source: Path) -> dict[str, object]:
    return {
        "schemaVersion": "CaseImportManifestV2",
        "packageSha256": "sha256:" + "a" * 64,
        "createdAt": "2026-08-10T00:00:00Z",
        "extractor": {"name": "fixture", "version": "1"},
        "case": {
            "projectNo": PROJECT,
            "brigadeCode": "XISHAN",
            "unitName": "合成单位",
            "inspectionForm": "ROUTINE",
            "notificationTarget": "UNKNOWN",
        },
        "initialInspection": {
            "clientRef": "initial",
            "stage": "INITIAL_CHECK",
            "inspectionDate": "2026-08-01",
            "products": [],
        },
        "files": [
            {
                "clientRef": "file:one",
                "relativePath": "files/one.pdf",
                "sha256": file_sha256(source),
                "mimeType": "application/pdf",
                "pageCount": 1,
            }
        ],
        "documentSlots": [],
        "otherAttachments": [
            {
                "clientRef": "attachment:one",
                "slotCode": "OTHER_ATTACHMENT",
                "title": "附件",
                "fileRef": "file:one",
            }
        ],
    }


def test_explicit_51_slot_map_is_correct() -> None:
    assert len(cli.SLOT_META) == 51
    for code in (
        "ONSITE_UNQUALIFIED_NOTICE",
        "RECTIFICATION_ORDER",
        "EVIDENCE_PRESERVATION_DECISION",
    ):
        assert cli.SLOT_META[code] == ("INSPECTION", "INITIAL_CHECK")
    assert cli.SLOT_META["ILLEGAL_PRODUCT_NOTIFICATION_LETTER"] == (
        "NOTIFICATION_TARGET",
        "INITIAL_CHECK",
    )
    assert cli.SLOT_META["INITIAL_CCC_CERTIFICATE"] == ("PRODUCT", "INITIAL_CHECK")


def test_same_name_model_products_keep_distinct_client_refs(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["initialInspection"]["products"] = [
        {"clientRef": f"product:{letter}", "name": "产品", "modelSpec": "M", "location": letter}
        for letter in ("a", "b")
    ]
    assert validate_manifest(data, {"file:one": str(source)}) == []
    data["initialInspection"]["products"][1]["clientRef"] = "product:a"
    assert any("clientRef 重复" in error for error in validate_manifest(data))


@pytest.mark.parametrize("code", sorted(cli.SLOT_META))
def test_each_slot_accepts_only_its_explicit_owner(code: str, tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    multiplicity, stage = cli.SLOT_META[code]
    if multiplicity == "OTHER":
        assert code == "OTHER_ATTACHMENT"
        return
    data["initialInspection"]["products"] = [{"clientRef": "product:initial", "name": "产品"}]
    data["recheckInspection"] = {
        "clientRef": "recheck",
        "stage": "RECHECK",
        "products": [{"clientRef": "product:recheck", "name": "复查产品"}],
    }
    slot: dict[str, object] = {
        "clientRef": "slot:one",
        "slotCode": code,
        "versions": [
            {"kind": "SCANNED" if code in cli.PHOTO_SLOTS else "ELECTRONIC", "fileRef": "file:one"}
        ],
    }
    if multiplicity == "INSPECTION":
        slot["inspectionRef"] = "initial" if stage == "INITIAL_CHECK" else "recheck"
    if multiplicity == "PRODUCT":
        slot["productRef"] = "product:initial" if stage == "INITIAL_CHECK" else "product:recheck"
    if multiplicity == "NOTIFICATION_TARGET":
        data["case"]["notificationTarget"] = "PRODUCTION"
        slot["notificationTarget"] = "PRODUCTION"
    data["otherAttachments"] = []
    data["documentSlots"] = [slot]
    assert validate_manifest(data, {"file:one": str(source)}) == []
    if multiplicity == "PRODUCT":
        slot["inspectionRef"] = "initial"
        assert any(
            "只填写" in error for error in validate_manifest(data, {"file:one": str(source)})
        )


def test_bom_compose_uses_only_inventory_hash_and_source_relative_path(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf(source_root / "一.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source_root), work_dir=str(work)))
    data = manifest(source_root / "一.pdf")
    data.pop("packageSha256")
    data["files"][0].update({"sourceRelativePath": "一.pdf", "relativePath": "normalized/一.pdf"})
    case_data = tmp_path / "case.json"
    case_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))
    assert (
        read_json(work / "manifest.json")["packageSha256"]
        == read_json(work / "inventory.json")["packageSha256"]
    )
    data["packageSha256"] = "sha256:" + "b" * 64
    case_data.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RegistryError, match="精确一致"):
        compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))


@pytest.mark.parametrize("work_relative", [".", "work", ".."])
def test_inventory_rejects_directory_source_and_work_dir_overlap(
    tmp_path: Path, work_relative: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    work = (
        source
        if work_relative == "."
        else tmp_path
        if work_relative == ".."
        else source / work_relative
    )
    with pytest.raises(RegistryError, match="不得重叠"):
        inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))


def test_compose_rejects_duplicate_source_relative_path_for_multiple_file_refs(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf(source_root / "one.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source_root), work_dir=str(work)))
    data = manifest(source_root / "one.pdf")
    data.pop("packageSha256")
    data["files"] = [
        {
            "clientRef": "file:one",
            "sourceRelativePath": "one.pdf",
            "relativePath": "normalized/one.pdf",
        },
        {
            "clientRef": "file:two",
            "sourceRelativePath": "one.pdf",
            "relativePath": "normalized/two.pdf",
        },
    ]
    data["otherAttachments"] = [
        {"clientRef": "attachment:one", "slotCode": "OTHER_ATTACHMENT", "fileRef": "file:one"},
        {"clientRef": "attachment:two", "slotCode": "OTHER_ATTACHMENT", "fileRef": "file:two"},
    ]
    case_data = tmp_path / "case.json"
    case_data.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RegistryError, match="不得供多个 fileRef 复用"):
        compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data)))


def test_split_preflights_png_collision_and_never_writes_outside_normalized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "a.pdf", 2)
    (source / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    plan = {
        "items": [
            {
                "sourceRelativePath": "a.pdf",
                "pageStart": 1,
                "pageEnd": 1,
                "relativePath": "normalized/a.pdf",
            },
            {
                "sourceRelativePath": "a.pdf",
                "pageStart": 2,
                "pageEnd": 2,
                "relativePath": "normalized/a.pdf",
            },
        ]
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(RegistryError, match="重复"):
        split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))
    assert not (work / "normalized").exists()
    plan["items"] = [
        {
            "sourceRelativePath": "bad.png",
            "pageStart": 1,
            "pageEnd": 1,
            "relativePath": "normalized/no.pdf",
        }
    ]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(RegistryError, match="PDF"):
        split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))


def test_mineru_uses_explicit_sources_and_records_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "one.pdf")
    (source / "two.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    mineru = tmp_path / "run-mineru-docker.ps1"
    powershell = tmp_path / "powershell.exe"
    mineru.write_text("# fixture", encoding="utf-8")
    powershell.write_bytes(b"fixture")
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "MINERU_SCRIPT", mineru)
    monkeypatch.setattr(cli, "SYSTEM_POWERSHELL", powershell)

    from scripts import ocr_runtime
    from scripts.test_ocr_runtime import make_output

    monkeypatch.setattr(
        ocr_runtime,
        "engine_profile",
        lambda _wrapper: {"imageId": "sha256:" + "a" * 64},
    )

    def successful_run(inputs, timeout, poll, destination, attempt_path, profile):
        calls.append(inputs)
        make_output(inputs[0], destination)
        return 0

    monkeypatch.setattr(ocr_runtime, "run_task", successful_run)
    ocr_command(
        argparse.Namespace(
            work_dir=str(work),
            output_dir=str(work / "ocr"),
            relative_path=["one.pdf", "two.png"],
            timeout=10,
        )
    )
    assert len(calls) == 2 and all(len(inputs) == 1 for inputs in calls)
    assert len(read_json(work / "ocr-result.json")["mappings"]) == 2
    monkeypatch.setattr(
        ocr_runtime,
        "run_task",
        lambda *_args, **_kwargs: 0,
    )
    with pytest.raises(RegistryError, match="未完整"):
        ocr_command(
            argparse.Namespace(
                work_dir=str(work),
                output_dir=str(work / "empty-ocr"),
                relative_path=["one.pdf"],
                timeout=10,
            )
        )
    with pytest.raises(RegistryError, match="显式"):
        ocr_command(
            argparse.Namespace(
                work_dir=str(work), output_dir=str(work / "ocr"), relative_path=[], timeout=10
            )
        )


def test_ocr_adopt_existing_uses_recorded_results_without_engine_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import ocr_runtime
    from scripts.test_ocr_runtime import _digest, _zip_result

    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "one.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    inventory = read_json(work / "inventory.json")
    item = ocr_runtime.validate_sources(inventory, ["one.pdf"], None)[0]
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    archive = _zip_result(evidence, "result", item["pages"])
    attempt = evidence / "attempt.json"
    attempt.write_text(
        json.dumps(
            {
                "sha": item["sha256"].split(":", 1)[1],
                "pages": item["pages"],
                "state": "DONE",
                "status": "completed",
                "taskId": "job_cli",
                "options": {"tier": "standard", "ocr_mode": "auto", "output_formats": "zip", "page_range": "all"},
                "resultSha256": _digest(archive).split(":", 1)[1],
            }
        ),
        encoding="utf-8",
    )
    receipts = evidence / "receipts.json"
    receipts.write_text(
        json.dumps(
            {
                "schemaVersion": ocr_runtime.ADOPTION_SCHEMA,
                "entries": [
                    {
                        "engine": "MINERU_V4_TASK",
                        "sourceRelativePath": "one.pdf",
                        "sourceSha256": item["sha256"],
                        "pageCount": item["pages"],
                        "task": {"taskId": "job_cli", "status": "completed"},
                        "attemptRecord": {"path": str(attempt), "sha256": _digest(attempt)},
                        "resultZip": {"path": str(archive), "sha256": _digest(archive)},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("adoption must not start an engine or use the network")

    monkeypatch.setattr(ocr_runtime, "run_task", forbidden)
    monkeypatch.setattr(ocr_runtime, "engine_profile", forbidden)
    ocr_command(
        argparse.Namespace(
            work_dir=str(work),
            output_dir=str(work / "ocr"),
            relative_path=None,
            timeout=10,
            adopt_existing=str(receipts),
        )
    )
    index = read_json(work / "ocr-result.json")
    assert index["status"] == "COMPLETED" and index["pending"] == []
    assert index["mappings"][0]["adoption"]["taskId"] == "job_cli"
    receipts.write_text(receipts.read_text(encoding="utf-8").replace("job_cli", "job_x", 1), encoding="utf-8")
    with pytest.raises(RegistryError, match="不采纳|未被采纳"):
        ocr_command(
            argparse.Namespace(
                work_dir=str(work),
                output_dir=str(work / "ocr2"),
                relative_path=None,
                timeout=10,
                adopt_existing=str(receipts),
            )
        )


def test_schema_semantics_reject_missing_method_bad_date_and_bad_scope(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["initialInspection"]["products"] = [
        {"clientRef": "product:one", "name": "产品", "result": "QUALIFIED"}
    ]
    data["documentSlots"] = [
        {
            "clientRef": "slot:one",
            "slotCode": "INITIAL_CCC_CERTIFICATE",
            "inspectionRef": "initial",
            "versions": [{"kind": "ELECTRONIC", "fileRef": "file:one"}],
            "documentDate": "2026-02-30",
        }
    ]
    data["otherAttachments"] = []
    errors = validate_manifest(data, {"file:one": str(source)})
    assert (
        any("method" in error for error in errors)
        and any("documentDate" in error for error in errors)
        and any("只填写" in error for error in errors)
    )


def test_main_returns_single_line_error_for_business_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_args: argparse.Namespace) -> None:
        raise RegistryError("业务校验失败")

    monkeypatch.setattr(cli, "validate_command", fail)
    status = cli.main(["validate", "--manifest", "unused.json"])
    assert status == 2 and capsys.readouterr().err == "ERROR: 业务校验失败\n"


def test_reference_v2_example_is_readable_and_valid_after_product_owner_fix() -> None:
    example = read_json(ROOT / "references" / "CaseImportManifestV2.example.json")
    assert example["schemaVersion"] == "CaseImportManifestV2"
    assert validate_manifest(example) == []


def test_real_compose_cli_updates_case_waterline_to_pending_local_verify(tmp_path: Path) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    config = workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    layout = workspace.BusinessLayout.from_root(config.work_root)
    pending = layout.pending_case_dir(PROJECT)
    pending.mkdir(parents=True)
    source = pending / "one.pdf"
    pdf(source)
    work = layout.work_case_dir(PROJECT)
    inventory_args = cli.build_parser().parse_args(
        [
            "inventory",
            str(pending),
            "--work-dir",
            str(work),
            "--workspace-config",
            str(config_path),
        ]
    )
    inventory_command(inventory_args)
    record = workspace.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "PENDING_ORGANIZATION"
    assert record["local"]["status"] == "INVENTORIED"

    data = manifest(source)
    data.pop("packageSha256")
    data["files"][0].update({"sourceRelativePath": "one.pdf", "relativePath": "normalized/one.pdf"})
    case_data = work / "case-data.json"
    case_data.write_text(json.dumps(data), encoding="utf-8")
    compose_args = cli.build_parser().parse_args(
        [
            "compose",
            "--work-dir",
            str(work),
            "--case-data",
            str(case_data),
            "--workspace-config",
            str(config_path),
        ]
    )
    compose_command(compose_args)
    record = workspace.load_waterline(layout)["cases"][PROJECT]
    assert record["state"] == "PENDING_LOCAL_VERIFY"
    assert record["local"]["status"] == "READY_FOR_LOCAL_VERIFY"


def test_real_inventory_cli_rejects_originals_outside_pending_case(tmp_path: Path) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    config = workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    layout = workspace.BusinessLayout.from_root(config.work_root)
    source = tmp_path / "external-source"
    source.mkdir()
    pdf(source / "one.pdf")
    work = layout.work_case_dir(PROJECT)
    args = cli.build_parser().parse_args(
        [
            "inventory",
            str(source),
            "--work-dir",
            str(work),
            "--workspace-config",
            str(config_path),
        ]
    )
    with pytest.raises(RegistryError, match="原始案卷/待处理案卷"):
        inventory_command(args)
    assert not (work / "inventory.json").exists()


def test_source_attach_package_parser_accepts_download_baseline_override() -> None:
    args = cli.build_parser().parse_args(
        [
            "source",
            "attach-package",
            "--batch-id",
            "batch-1",
            "--rwid",
            "fixture-rwid",
            "--download",
            "downloads",
            "--download-baseline",
            "baseline.json",
        ]
    )
    assert args.download_baseline == "baseline.json"


def test_source_begin_parser_supports_isolated_acceptance_sample() -> None:
    args = cli.build_parser().parse_args(
        [
            "source",
            "begin",
            "--origin",
            "https://source.example/cases",
            "--acceptance-sample",
        ]
    )
    assert args.acceptance_sample is True


def test_ledger_status_parser_supports_formal_batch_selection() -> None:
    args = cli.build_parser().parse_args(
        [
            "ledger",
            "status",
            "--work-root",
            "C:/fixture-work-root",
            "--batch-id",
            "formal-batch",
        ]
    )
    assert args.batch_id == "formal-batch"
    assert args.func is cli.ledger_status_command


def test_source_tail_cursor_and_await_download_parsers() -> None:
    tail = cli.build_parser().parse_args(
        [
            "source",
            "tail-cursor",
            "--total-count",
            "521",
            "--page-size",
            "20",
            "--page-number",
            "27",
            "--visible-row-count",
            "1",
        ]
    )
    assert tail.total_count == 521
    assert tail.row_number is None

    waiting = cli.build_parser().parse_args(
        [
            "source",
            "await-download",
            "--batch-id",
            "batch-1",
            "--rwid",
            "fixture-rwid",
            "--download-baseline",
            "baseline.json",
            "--timeout-seconds",
            "1800",
            "--attach",
        ]
    )
    assert waiting.download_baseline == "baseline.json"
    assert waiting.timeout_seconds == 1800
    assert waiting.attach is True


def test_source_attach_package_parser_requires_per_case_download_baseline() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "attach-package",
                "--batch-id",
                "batch-1",
                "--rwid",
                "fixture-rwid",
                "--download",
                "download.zip",
            ]
        )


def test_source_cli_requires_page_screenshot_detail_url_and_snapshot_rwid() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "add-page",
                "--batch-id",
                "batch-1",
                "--page-json",
                "page.json",
            ]
        )
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "source",
                "add-detail",
                "--batch-id",
                "batch-1",
                "--rwid",
                "fixture-rwid",
                "--detail-json",
                "detail.json",
                "--screenshot",
                "detail.png",
            ]
        )
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["source", "snapshot-downloads", "--batch-id", "batch-1"])


def test_source_snapshot_downloads_persists_per_case_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "business"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    config_path = tmp_path / "workspace.toml"
    workspace.configure_workspace(
        work_root=root,
        download_dir=downloads,
        config_path=config_path,
    )
    filter_path = tmp_path / "legacy-explicit-filters.json"
    filter_path.write_text(json.dumps(source._default_filters(None, {})), encoding="utf-8")
    begin_args = cli.build_parser().parse_args(
        [
            "source",
            "begin",
            "--batch-id",
            "batch-1",
            "--filter-json",
            str(filter_path),
            "--origin",
            "http://registry-source.example/#/xfjd/list?runId=secret",
            "--workspace-config",
            str(config_path),
        ]
    )
    cli.source_begin_command(begin_args)
    capsys.readouterr()
    layout = workspace.BusinessLayout.from_root(root)
    record = {
        "RWID": "fixture-rwid",
        "项目编号": PROJECT,
        "单位名称": "测试单位",
    }
    for round_no in (1, 2):
        source.add_page(
            layout,
            "batch-1",
            1,
            [record],
            1,
            1,
            round_no=round_no,
        )
        source.finalize_capture(layout, "batch-1")
    screenshot = tmp_path / "detail.png"
    screenshot.write_bytes(b"detail screenshot")
    source.add_detail(
        layout,
        "batch-1",
        "fixture-rwid",
        {"项目编号": PROJECT, "单位名称": "测试单位", "大队代码": "FIXTURE"},
        "http://registry-source.example/#/xfjd/detail?RWID=fixture-rwid",
        screenshot,
    )
    (downloads / "existing.zip").write_bytes(b"baseline fixture")
    snapshot_args = cli.build_parser().parse_args(
        [
            "source",
            "snapshot-downloads",
            "--batch-id",
            "batch-1",
            "--rwid",
            "fixture-rwid",
            "--workspace-config",
            str(config_path),
        ]
    )
    cli.source_snapshot_downloads_command(snapshot_args)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "captured"
    assert output["fileCount"] == 1
    assert output["rwid"] == "fixture-rwid"
    assert output["projectNo"] == PROJECT
    assert Path(output["path"]).is_file()
    persisted = json.loads(Path(output["path"]).read_text(encoding="utf-8"))
    assert persisted["batchId"] == "batch-1"
    assert persisted["rwid"] == "fixture-rwid"
    assert persisted["projectNo"] == PROJECT
    assert persisted["consumedAt"] is None


def test_repair_site_id_contract_is_preserved_and_enforced(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    valid = manifest(source)
    valid["initialInspection"]["products"] = [
        {
            "clientRef": "product:one",
            "name": "产品",
            "maintenance": "YES",
            "repairSiteId": "11111111-1111-4111-8111-111111111111",
        }
    ]
    assert validate_manifest(valid, {"file:one": str(source)}) == []

    invalid_uuid = json.loads(json.dumps(valid))
    invalid_uuid["initialInspection"]["products"][0]["repairSiteId"] = "not-a-uuid"
    assert any("repairSiteId" in error for error in validate_manifest(invalid_uuid))

    invalid_maintenance = json.loads(json.dumps(valid))
    invalid_maintenance["initialInspection"]["products"][0]["maintenance"] = "NO"
    assert any("maintenance" in error for error in validate_manifest(invalid_maintenance))

    no_repair_site = json.loads(json.dumps(valid))
    no_repair_site["initialInspection"]["products"][0]["repairSiteId"] = None
    no_repair_site["initialInspection"]["products"][0]["maintenance"] = "UNKNOWN"
    assert validate_manifest(no_repair_site, {"file:one": str(source)}) == []

    uppercase_uuid = json.loads(json.dumps(valid))
    uppercase_uuid["initialInspection"]["products"][0]["repairSiteId"] = (
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    )
    assert any("repairSiteId" in error for error in validate_manifest(uppercase_uuid))


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", " 产品"),
        ("modelSpec", " "),
        ("nominalProducer", "生产企业 "),
        ("location", "位置" + "x" * 300),
        ("problemDescription", "问题" + "x" * 2000),
    ],
)
def test_product_text_fields_are_trimmed_nonempty_and_bounded(
    tmp_path: Path, field: str, value: str
) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    product = {"clientRef": "product:one", "name": "产品"}
    data["initialInspection"]["products"] = [product]
    product[field] = value
    errors = validate_manifest(data)
    assert any(field in error for error in errors)


def test_one_untitled_attachment_is_allowed_but_multiple_are_not(tmp_path: Path) -> None:
    source = tmp_path / "one.pdf"
    pdf(source)
    data = manifest(source)
    data["otherAttachments"][0].pop("title")
    assert validate_manifest(data, {"file:one": str(source)}) == []
    second = json.loads(json.dumps(data["otherAttachments"][0]))
    second["clientRef"] = "attachment:two"
    data["otherAttachments"].append(second)
    assert any("多个无 title" in error for error in validate_manifest(data))
    titled = manifest(source)
    titled["otherAttachments"][0]["title"] = " 附件"
    assert any("title" in error for error in validate_manifest(titled))
    titled["otherAttachments"][0]["title"] = "x" * 301
    assert any("title" in error for error in validate_manifest(titled))


def test_ocr_answered_entirely_from_cache_leaves_waterline_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pdf(source / "one.pdf")
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    mineru = tmp_path / "run-mineru-docker.ps1"
    powershell = tmp_path / "powershell.exe"
    mineru.write_text("# fixture", encoding="utf-8")
    powershell.write_bytes(b"fixture")
    monkeypatch.setattr(cli, "MINERU_SCRIPT", mineru)
    monkeypatch.setattr(cli, "SYSTEM_POWERSHELL", powershell)

    from scripts import ocr_runtime
    from scripts.test_ocr_runtime import make_output

    monkeypatch.setattr(
        ocr_runtime, "engine_profile", lambda _wrapper: {"imageId": "sha256:" + "a" * 64}
    )
    requests: list[list[str]] = []

    def successful_run(inputs, timeout, poll, destination, attempt_path, profile):
        requests.append(inputs)
        make_output(inputs[0], destination)
        return 0

    monkeypatch.setattr(ocr_runtime, "run_task", successful_run)
    marked: list[str] = []
    monkeypatch.setattr(cli, "update_local_case_waterline", lambda *a, **k: marked.append("hit"))
    args = argparse.Namespace(
        work_dir=str(work), output_dir=str(work / "ocr"), relative_path=["one.pdf"], timeout=10
    )
    ocr_command(args)
    assert len(requests) == 1 and marked == ["hit"]  # a real engine run still records progress
    ocr_command(args)
    assert len(requests) == 1  # zero new engine requests
    assert marked == ["hit"]  # all-cache run does not touch the waterline again


def test_resolve_workspace_local_query_does_not_require_download_dir(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    missing = tmp_path / "no-downloads"

    with pytest.raises(workspace.WorkspaceStateError):
        workspace.resolve_workspace(
            work_root=work, download_dir=missing, config_path=tmp_path / "cfg.toml"
        )
    config, _layout = workspace.resolve_workspace(
        work_root=work,
        download_dir=missing,
        config_path=tmp_path / "cfg.toml",
        require_download_dir=False,
    )

    assert config.download_dir == missing
    assert not missing.exists()


@pytest.mark.parametrize("kind", ["missing", "plain-file", "symlink"])
def test_configured_unusable_download_dir_does_not_block_local_queries(
    tmp_path: Path, kind: str
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    target = tmp_path / "downloads"
    if kind == "plain-file":
        target.write_text("x", encoding="utf-8")
    elif kind == "symlink":
        real = tmp_path / "real-downloads"
        real.mkdir()
        try:
            target.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("环境不支持符号链接")
    config = tmp_path / "workspace.toml"
    config.write_text(
        '[workspace]\nroot = "%s"\ndownload_dir = "%s"\n'
        % (work.as_posix(), target.as_posix()),
        encoding="utf-8",
    )
    before = config.read_bytes()

    kept, _layout = workspace.resolve_workspace(config_path=config, require_download_dir=False)
    assert kept.download_dir == target and config.read_bytes() == before
    if kind != "symlink":
        with pytest.raises(workspace.WorkspaceStateError):
            workspace.resolve_workspace(config_path=config)


def test_source_admit_observed_case_parser_requires_batch_and_detail_file() -> None:
    parsed = cli.build_parser().parse_args(
        ["source", "admit-observed-case", "--batch-id", "batch-1", "--detail-json", "detail.json"]
    )
    assert parsed.batch_id == "batch-1" and parsed.detail_json == "detail.json"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["source", "admit-observed-case", "--batch-id", "batch-1"])


def test_source_attach_detail_segments_parser_requires_batch_and_index_file() -> None:
    args = cli.build_parser().parse_args(
        ["source", "attach-detail-segments", "--batch-id", "batch-1", "--index-json", "index.json"]
    )
    assert args.batch_id == "batch-1" and args.index_json == "index.json"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["source", "attach-detail-segments", "--batch-id", "batch-1"])


def test_feishu_source_build_parser_requires_every_pinned_input() -> None:
    required = ["--capture", "c.json", "--capture-sha", "a", "--waterline", "w.json", "--contract-v3", "v3.json", "--contract-v3-sha", "b",
                "--contract-v9", "v9.json", "--contract-v9-sha", "c", "--routes", "r.json", "--routes-sha", "d",
                "--base-token", "bas", "--profile", "p", "--out-dir", "o", "--log-root", "l", "--ledger-dir", "led"]
    args = cli.build_parser().parse_args(["feishu-source-build", *required])
    assert args.identity == "user" and args.batch_size == 20
    for index in range(0, len(required), 2):
        trimmed = required[:index] + required[index + 2:]
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["feishu-source-build", *trimmed])
