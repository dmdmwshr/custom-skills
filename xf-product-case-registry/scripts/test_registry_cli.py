from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

import scripts.registry_cli as registry_cli
from scripts.registry_cli import (
    RegistryError,
    build_entities,
    compose_command,
    inventory_command,
    ocr_command,
    read_json,
    safe_extract_zip,
    source_analysis_command,
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
                "modelSpec": "QZ3.5/7.5",
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    }
                ],
            },
            {
                "sequence": 2,
                "name": "消防水带",
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                        "caseInspectionRef": "external:initial-2026-05-19",
                    }
                ],
            },
            {
                "sequence": 3,
                "name": "消防接口",
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    }
                ],
            },
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
        "reviewItems": [
            {
                "entityRef": "product:1",
                "fieldPath": "modelSpec",
                "issueType": "VALUE_CONFLICT",
                "message": "电子版与扫描件型号不一致，保留现有值待核对。",
                "currentValue": "QZ3.5/7.5",
                "incomingValue": "QZ3.5/7.5A",
                "candidates": [
                    {
                        "candidateRef": "candidate:product-1-model-spec-current",
                        "value": "QZ3.5/7.5",
                        "trustLevel": "MANUAL",
                        "sources": [
                            {
                                "kind": "MANUAL",
                                "value": "QZ3.5/7.5",
                                "evidence": "人工确认的现有产品型号",
                            }
                        ],
                    },
                    {
                        "candidateRef": "candidate:product-1-model-spec-scan",
                        "value": "QZ3.5/7.5A",
                        "trustLevel": "OCR_ONLY",
                        "sources": [
                            {
                                "kind": "SIGNED_SCAN_OCR",
                                "relativePath": "original/组合件.pdf",
                                "page": 1,
                                "value": "QZ3.5/7.5A",
                                "evidence": "扫描件产品型号栏 OCR 结果",
                            }
                        ],
                    },
                ],
            }
        ],
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
    assert manifest["reviewItems"][0]["clientRef"].startswith("review:")
    assert manifest["reviewItems"][0]["entityRef"] == "product:1"
    assert manifest["reviewItems"][0]["currentValue"] == "QZ3.5/7.5"
    assert manifest["reviewItems"][0]["candidates"][1]["sources"][0]["fileRef"].startswith(
        "file:orig:"
    )
    first_review_ref = manifest["reviewItems"][0]["clientRef"]
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    assert read_json(work / "manifest.json")["reviewItems"][0]["clientRef"] == first_review_ref
    invalid_review = json.loads(json.dumps(manifest))
    invalid_review["reviewItems"].append(dict(invalid_review["reviewItems"][0]))
    assert any(
        "clientRef 重复：" in error
        for error in validate_manifest(invalid_review, upload_map)
    )
    entity_ref_collision = json.loads(json.dumps(manifest))
    entity_ref_collision["reviewItems"][0]["clientRef"] = "product:1"
    assert "clientRef 重复：product:1" in validate_manifest(entity_ref_collision, upload_map)
    duplicate_candidate = json.loads(json.dumps(manifest))
    duplicate_candidate["reviewItems"][0]["candidates"][1]["candidateRef"] = (
        duplicate_candidate["reviewItems"][0]["candidates"][0]["candidateRef"]
    )
    assert any(
        "候选标识重复" in error for error in validate_manifest(duplicate_candidate, upload_map)
    )
    legacy_value_conflict = json.loads(json.dumps(manifest))
    legacy_value_conflict["reviewItems"][0].pop("candidates")
    assert validate_manifest(legacy_value_conflict, upload_map) == []
    assert len(manifest["files"]) == 4
    assert manifest["products"][0]["clientRef"] == "product:1"
    assert (
        manifest["products"][0]["inspections"][0]["caseInspectionRef"]
        == "case-inspection:initial:2026-05-19"
    )
    assert (
        manifest["products"][2]["inspections"][0]["caseInspectionRef"]
        == manifest["products"][0]["inspections"][0]["caseInspectionRef"]
    )
    assert (
        manifest["products"][1]["inspections"][0]["caseInspectionRef"]
        == "external:initial-2026-05-19"
    )
    assert manifest["case"]["caseType"] == "UNKNOWN"
    assert any(
        item["entityRef"] == "case:32002207C202600033"
        and item["fieldPath"] == "caseType"
        for item in manifest["missingItems"]
    )

    legacy_manifest = json.loads(json.dumps(manifest))
    for product in legacy_manifest["products"]:
        for inspection in product["inspections"]:
            inspection.pop("caseInspectionRef")
    assert validate_manifest(legacy_manifest, upload_map) == []

    inconsistent_group = json.loads(json.dumps(manifest))
    inconsistent_group["products"][2]["inspections"][0]["inspectionDate"] = "2026-05-20"
    assert any(
        "案卷检查分组 case-inspection:initial:2026-05-19 的阶段或检查日期不一致"
        in error
        for error in validate_manifest(inconsistent_group, upload_map)
    )

    indirect_criminal_case_data = json.loads(json.dumps(case_data))
    indirect_criminal_case_data["case"]["caseType"] = "CRIMINAL"
    indirect_criminal_case_data["fieldEvidence"].append(
        {
            "entityRef": "case:32002207C202600033",
            "fieldPath": "caseType",
            "value": "CRIMINAL",
            "trustLevel": "CORROBORATED",
            "sources": [
                {
                    "kind": "PDF_TEXT",
                    "relativePath": "original/组合件.pdf",
                    "page": 1,
                    "evidence": "行政处罚决定书和通报函。",
                }
            ],
        }
    )
    case_data_path.write_text(
        json.dumps(indirect_criminal_case_data, ensure_ascii=False), encoding="utf-8"
    )
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    indirect_criminal_manifest = read_json(work / "manifest.json")
    assert indirect_criminal_manifest["case"]["caseType"] == "UNKNOWN"
    assert any(
        item["entityRef"] == "case:32002207C202600033"
        and item["fieldPath"] == "caseType"
        for item in indirect_criminal_manifest["missingItems"]
    )

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

    administrative_case_data = json.loads(json.dumps(case_data))
    administrative_case_data["products"][0]["inspections"].append(
        {
            "stage": "RECHECK",
            "method": "ONSITE",
            "inspectionDate": "2026-05-27",
            "inspectionResult": "UNQUALIFIED",
        }
    )
    case_data_path.write_text(
        json.dumps(administrative_case_data, ensure_ascii=False), encoding="utf-8"
    )
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    administrative_manifest = read_json(work / "manifest.json")
    assert administrative_manifest["case"]["caseType"] == "ADMINISTRATIVE"
    administrative_evidence = next(
        item
        for item in administrative_manifest["fieldEvidence"]
        if item["entityRef"] == "case:32002207C202600033"
        and item["fieldPath"] == "caseType"
    )
    assert administrative_evidence["sources"][0]["kind"] == "RULE"
    assert (
        administrative_evidence["sources"][0]["value"]["inspectionRef"]
        == "inspection:1:recheck"
    )
    assert validate_manifest(administrative_manifest, upload_map) == []

    missing_rule_evidence = json.loads(json.dumps(administrative_manifest))
    missing_rule_evidence["fieldEvidence"] = [
        item
        for item in missing_rule_evidence["fieldEvidence"]
        if not (
            item["entityRef"] == "case:32002207C202600033"
            and item["fieldPath"] == "caseType"
        )
    ]
    assert any(
        "行案必须具有引用整改复查不合格记录" in error
        for error in validate_manifest(missing_rule_evidence, upload_map)
    )

    criminal_case_data = json.loads(json.dumps(administrative_case_data))
    criminal_case_data["case"]["caseType"] = "CRIMINAL"
    criminal_case_data["fieldEvidence"].append(
        {
            "entityRef": "case:32002207C202600033",
            "fieldPath": "caseType",
            "value": "CRIMINAL",
            "trustLevel": "CORROBORATED",
            "sources": [
                {
                    "kind": "PDF_TEXT",
                    "relativePath": "original/组合件.pdf",
                    "page": 1,
                    "evidence": "正文明确载明本案已移送公安机关。",
                }
            ],
        }
    )
    case_data_path.write_text(
        json.dumps(criminal_case_data, ensure_ascii=False), encoding="utf-8"
    )
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    criminal_manifest = read_json(work / "manifest.json")
    assert criminal_manifest["case"]["caseType"] == "CRIMINAL"
    assert validate_manifest(criminal_manifest, upload_map) == []

    indirect_criminal = json.loads(json.dumps(criminal_manifest))
    indirect_criminal["fieldEvidence"][-1]["sources"][0]["evidence"] = "行政处罚决定书。"
    assert any(
        "刑案必须具有含页码和直接刑事表述" in error
        for error in validate_manifest(indirect_criminal, upload_map)
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


def test_inventory_ocr_and_source_analysis_handles_images_and_source_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "32002207C202600033"
    source.mkdir()
    (source / "20260731-001.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    write_blank_pdf(source / "消防产品监督检查记录.pdf", pages=1)
    write_blank_pdf(source / "现场签字扫描件.pdf", pages=1)
    work = tmp_path / "work"
    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    inventory = read_json(work / "inventory.json")

    screenshot = next(
        item for item in inventory["files"] if item["relativePath"] == "20260731-001.png"
    )
    assert screenshot["pageCount"] == 1
    assert screenshot["pages"] == [
        {
            "page": 1,
            "textPath": screenshot["pages"][0]["textPath"],
            "textChars": 0,
            "needsOcr": True,
            "inputKind": "IMAGE",
        }
    ]
    record = next(
        item for item in inventory["files"] if item["relativePath"] == "消防产品监督检查记录.pdf"
    )
    Path(record["pages"][0]["textPath"]).write_text(
        "消防产品监督检查记录\n产品信息", encoding="utf-8"
    )
    record["pages"][0]["textChars"] = 100
    record["pages"][0]["needsOcr"] = False
    (work / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False), encoding="utf-8"
    )

    zerox = tmp_path / "zerox.cmd"
    zerox.write_text("fixture", encoding="utf-8")
    poppler = tmp_path / "poppler"
    poppler.mkdir()
    calls: list[dict[str, object]] = []

    def fake_zerox_page(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown = output_dir / "page.md"
        source_name = Path(str(kwargs["source"])).name
        markdown.write_text(
            (
                "检查产品信息 产品名称 规格型号 标称生产者 产品所在部位 "
                "检查基数 检查数量 市场准入检查情况 产品质量现场检查情况"
                if source_name.endswith(".png")
                else "扫描签字件"
            ),
            encoding="utf-8",
        )
        return {"status": "SUCCESS", "page": kwargs["page_number"], "markdownPath": str(markdown)}

    monkeypatch.setattr(registry_cli, "run_zerox_page", fake_zerox_page)
    ocr_command(
        argparse.Namespace(
            work_dir=str(work),
            zerox=str(zerox),
            poppler=str(poppler),
            concurrency=1,
            timeout=10,
            continue_on_error=False,
        )
    )
    assert any(call["input_kind"] == "IMAGE" for call in calls)

    source_analysis_command(argparse.Namespace(work_dir=str(work)))
    analysis = read_json(work / "source-analysis.json")
    classifications = {item["relativePath"]: item["sourceKind"] for item in analysis["sources"]}
    assert classifications["20260731-001.png"] == "SUPERVISION_SCREENSHOT"
    assert classifications["消防产品监督检查记录.pdf"] == "SUPERVISION_RECORD_PDF_TEXT"
    assert classifications["现场签字扫描件.pdf"] == "SIGNED_SCAN_OCR"
    assert analysis["doesNotInferBusinessValues"] is True
    assert (
        analysis["fieldGroupPriorities"]["problemDescription"][0]
        == "SUPERVISION_RECORD_PDF_TEXT"
    )


def test_undated_case_inspections_share_stage_ordinal_refs() -> None:
    case_data = {
        "case": {"projectNo": "32002207C202600033"},
        "products": [
            {
                "sequence": 1,
                "inspections": [
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionResult": "UNQUALIFIED",
                    },
                    {
                        "stage": "RECHECK",
                        "method": "SAMPLING",
                        "inspectionResult": "PENDING",
                    },
                ],
            },
            {
                "sequence": 2,
                "inspections": [
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionResult": "UNQUALIFIED",
                    },
                    {
                        "stage": "RECHECK",
                        "method": "SAMPLING",
                        "inspectionResult": "PENDING",
                    },
                ],
            },
        ],
    }

    build_entities(case_data)
    first_product, second_product = case_data["products"]
    assert first_product["inspections"][0]["caseInspectionRef"] == (
        "case-inspection:recheck:ordinal-1"
    )
    assert first_product["inspections"][0]["caseInspectionRef"] == (
        second_product["inspections"][0]["caseInspectionRef"]
    )
    assert first_product["inspections"][1]["caseInspectionRef"] == (
        "case-inspection:recheck:ordinal-2"
    )
    assert first_product["inspections"][1]["caseInspectionRef"] == (
        second_product["inspections"][1]["caseInspectionRef"]
    )
