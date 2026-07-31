from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from scripts.registry_cli import (
    RegistryError,
    compose_command,
    inventory_command,
    read_json,
    safe_extract_zip,
    split_command,
    upload_command,
    validate_manifest,
)


def write_blank_pdf(path: Path, pages: int = 2) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def test_inventory_split_compose_and_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "32002207C202600033"
    source.mkdir()
    write_blank_pdf(source / "组合件.pdf")
    (source / "案卷截图.json").write_text('{"kind":"fixture"}\n', encoding="utf-8")
    work = tmp_path / "work"

    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    inventory = read_json(work / "inventory.json")
    assert inventory["containerKind"] == "DIRECTORY"
    assert len(inventory["files"]) == 2
    pdf_record = next(item for item in inventory["files"] if item["mimeType"] == "application/pdf")
    assert pdf_record["pageCount"] == 2
    assert [page["needsOcr"] for page in pdf_record["pages"]] == [True, True]

    plan = {
        "projectNo": "32002207C202600033",
        "items": [
            {
                "sourceRelativePath": "组合件.pdf",
                "pageStart": 1,
                "pageEnd": 1,
                "stage": "INITIAL_CHECK",
                "documentType": "SERVICE_RECEIPT",
                "documentLabel": "送达回证",
                "documentNoOrDate": "〔2026〕第0001号",
                "sequence": 1,
            }
        ],
    }
    plan_path = work / "split-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))
    split_index = read_json(work / "split-index.json")
    assert len(split_index["items"]) == 1
    assert Path(split_index["items"][0]["absolutePath"]).is_file()

    case_data = {
        "case": {
            "projectNo": "32002207C202600033",
            "brigadeCode": "XISHAN",
            "unitName": "测试单位",
            "inspectionForm": "ROUTINE",
            "caseType": "UNKNOWN",
            "onlineSale": "UNKNOWN",
        },
        "products": [
            {
                "sequence": 1,
                "name": "直流水枪",
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    }
                ],
            }
        ],
        "documentRequirements": [],
        "documents": [],
        "fieldEvidence": [
            {
                "entityRef": "case:32002207C202600033",
                "fieldPath": "unitName",
                "value": "测试单位",
                "trustLevel": "MANUAL",
                "sources": [{"kind": "MANUAL", "evidence": "测试夹具"}],
            }
        ],
        "missingItems": [],
    }
    case_data_path = work / "case-data.json"
    case_data_path.write_text(
        json.dumps(case_data, ensure_ascii=False),
        encoding="utf-8",
    )
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    manifest = read_json(work / "manifest.json")
    upload_map = read_json(work / "upload-map.json")["files"]
    assert validate_manifest(manifest, upload_map) == []
    assert len(manifest["files"]) == 4
    assert manifest["products"][0]["clientRef"] == "product:1"

    invalid_reinspection = json.loads(json.dumps(manifest))
    invalid_reinspection["products"][0]["inspections"][0].update(
        {
            "reinspectionStatus": "COMPLETED",
            "reinspectionReportNo": "复检报告001",
            "reinspectionResult": "QUALIFIED",
        }
    )
    assert any(
        "只有抽样送检记录可以包含复检信息" in error
        for error in validate_manifest(invalid_reinspection, upload_map)
    )

    sampling_reinspection = json.loads(json.dumps(invalid_reinspection))
    sampling_reinspection["products"][0]["inspections"][0]["method"] = "SAMPLING"
    sampling_reinspection["products"][0]["inspections"][0][
        "reinspectionStatus"
    ] = "COMPLETED"
    assert validate_manifest(sampling_reinspection, upload_map) == []

    inconsistent_reinspection = json.loads(json.dumps(sampling_reinspection))
    inconsistent_reinspection["products"][0]["inspections"][0][
        "reinspectionStatus"
    ] = "NOT_APPLIED"
    assert any(
        "复检状态为未申请时不能包含复检详情" in error
        for error in validate_manifest(inconsistent_reinspection, upload_map)
    )

    legacy_stage = json.loads(json.dumps(manifest))
    legacy_stage["products"][0]["inspections"][0]["stage"] = "LAB_REINSPECTION"
    assert any(
        ".stage 不合法" in error
        for error in validate_manifest(legacy_stage, upload_map)
    )

    upload_command(
        argparse.Namespace(
            manifest=str(work / "manifest.json"),
            upload_map=str(work / "upload-map.json"),
            api_base="https://example.invalid",
            timeout=10.0,
            dry_run=True,
            finalize=False,
        )
    )


def test_safe_extract_zip_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.pdf", b"%PDF-1.4\n")
    with pytest.raises(RegistryError, match="不安全"):
        safe_extract_zip(archive, tmp_path / "out")
