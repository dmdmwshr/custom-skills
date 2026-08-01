from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

import scripts.registry_cli as registry_cli
from scripts.registry_cli import (
    RegistryError,
    build_entities,
    compose_command,
    file_sha256,
    inventory_command,
    ocr_command,
    read_json,
    resolve_document_stage_bindings,
    safe_extract_zip,
    source_analysis_command,
    split_command,
    sync_document_versions_command,
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
    write_blank_pdf(source / "组合件.pdf", pages=4)
    (source / "案卷截图.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    work = tmp_path / "work"

    inventory_command(argparse.Namespace(input=str(source), work_dir=str(work)))
    inventory = read_json(work / "inventory.json")
    assert inventory["containerKind"] == "DIRECTORY"
    assert len(inventory["files"]) == 2
    pdf_record = next(item for item in inventory["files"] if item["mimeType"] == "application/pdf")
    assert pdf_record["pageCount"] == 4
    assert [page["needsOcr"] for page in pdf_record["pages"]] == [
        True,
        True,
        True,
        True,
    ]

    plan = {
        "projectNo": "32002207C202600033",
        "items": [
            {
                "documentRef": "document:service-receipt-1",
                "sourceRelativePath": "组合件.pdf",
                "pageStart": 1,
                "pageEnd": 1,
                "stage": "INITIAL_CHECK",
                "documentType": "SERVICE_RECEIPT",
                "documentLabel": "送达回证",
                "documentNoOrDate": "〔2026〕第0001号",
                "documentVersionKind": "SCANNED",
                "sequence": 1,
            },
            {
                "documentRef": "document:type-test-report-1",
                "sourceRelativePath": "组合件.pdf",
                "pageStart": 2,
                "pageEnd": 2,
                "stage": "CASE",
                "documentType": "TYPE_TEST_REPORT",
                "documentLabel": "型式检验报告",
                "documentNoOrDate": "ZB2018M3262",
                "documentVersionKind": "UNKNOWN",
                "sequence": 2,
            },
            {
                "documentRef": "document:service-receipt-1",
                "sourceRelativePath": "组合件.pdf",
                "pageStart": 3,
                "pageEnd": 3,
                "stage": "INITIAL_CHECK",
                "documentType": "SERVICE_RECEIPT",
                "documentLabel": "送达回证",
                "documentNoOrDate": "〔2026〕第0001号",
                "documentVersionKind": "SCANNED",
                "sequence": 3,
            },
            {
                "documentRef": "document:service-receipt-1",
                "sourceRelativePath": "组合件.pdf",
                "pageStart": 4,
                "pageEnd": 4,
                "stage": "INITIAL_CHECK",
                "documentType": "SERVICE_RECEIPT",
                "documentLabel": "送达回证",
                "documentNoOrDate": "〔2026〕第0001号",
                "documentVersionKind": "ELECTRONIC",
                "sequence": 4,
            },
        ],
    }
    plan_path = work / "split-plan.json"
    missing_document_ref_plan = json.loads(json.dumps(plan))
    missing_document_ref_plan["items"][0].pop("documentRef")
    missing_document_ref_path = work / "split-plan-missing-document-ref.json"
    missing_document_ref_path.write_text(
        json.dumps(missing_document_ref_plan, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="必须填写.*documentRef"):
        split_command(argparse.Namespace(work_dir=str(work), plan=str(missing_document_ref_path)))
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    split_command(argparse.Namespace(work_dir=str(work), plan=str(plan_path)))
    split_index = read_json(work / "split-index.json")
    assert split_index["splitIndexVersion"] == 2
    assert len(split_index["items"]) == 4
    assert Path(split_index["items"][0]["absolutePath"]).is_file()
    assert "_扫描件_" in split_index["items"][0]["relativePath"]
    assert split_index["items"][0]["documentVersionKind"] == "SCANNED"
    assert "_版本待核对_" in split_index["items"][1]["relativePath"]
    assert split_index["items"][1]["documentVersionKind"] == "UNKNOWN"
    assert split_index["items"][2]["documentRef"] == "document:service-receipt-1"
    assert "_电子版_" in split_index["items"][3]["relativePath"]
    assert split_index["items"][3]["documentVersionKind"] == "ELECTRONIC"

    case_data = {
        "case": {
            "projectNo": "32002207C202600033",
            "brigadeCode": "XISHAN",
            "unitName": "测试单位",
            "inspectionForm": "ROUTINE",
            "caseType": "UNKNOWN",
        },
        "products": [
            {
                "sequence": 1,
                "name": "直流水枪",
                "modelSpec": "QZ3.5/7.5",
                "onlineSale": "NO",
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
                "onlineSale": "YES",
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
                "onlineSale": "UNKNOWN",
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
        "documents": [
            {
                "clientRef": "document:service-receipt-1",
                "documentType": "SERVICE_RECEIPT",
                "documentNo": "〔2026〕第0001号",
                "issueDate": "2026-05-19",
                "associationScope": "INSPECTION",
                "stage": "INITIAL_CHECK",
                "caseInspectionRefs": ["case-inspection:initial:2026-05-19"],
                "productRefs": ["product:1"],
                "inspectionRefs": ["inspection:1:initial_check"],
                "stageEvidence": {
                    "method": "DOCUMENT_LINK",
                    "relatedInspectionRef": "inspection:1:initial_check",
                },
                "versions": [
                    {
                        "relativePath": split_index["items"][0]["relativePath"],
                        "kind": "SCANNED",
                    }
                ],
                "fileLinks": [],
            },
            {
                "clientRef": "document:type-test-report-1",
                "documentType": "TYPE_TEST_REPORT",
                "documentNo": "ZB2018M3262",
                "issueDate": "2026-05-19",
                "associationScope": "PRODUCT",
                "productRefs": ["product:1"],
                "inspectionRefs": [],
                "versions": [],
                "fileLinks": [
                    {
                        "relativePath": "original/组合件.pdf",
                        "relationRole": "SOURCE_COPY",
                        "pageStart": 2,
                        "pageEnd": 2,
                    }
                ],
            },
            {
                "clientRef": "document:onsite-photo-unknown",
                "documentType": "ONSITE_PHOTO",
                "productRefs": [],
                "inspectionRefs": [],
                "versions": [],
                "fileLinks": [
                    {
                        "relativePath": "original/案卷截图.png",
                        "relationRole": "SOURCE_COPY",
                    }
                ],
                "stageEvidence": {
                    "method": "UNKNOWN",
                    "sources": [
                        {
                            "kind": "FILENAME_HINT",
                            "relativePath": "original/案卷截图.png",
                            "evidence": "文件名包含现场照片。",
                        }
                    ],
                },
            },
        ],
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
        "clientRef 重复：" in error for error in validate_manifest(invalid_review, upload_map)
    )
    entity_ref_collision = json.loads(json.dumps(manifest))
    entity_ref_collision["reviewItems"][0]["clientRef"] = "product:1"
    assert "clientRef 重复：product:1" in validate_manifest(entity_ref_collision, upload_map)
    duplicate_candidate = json.loads(json.dumps(manifest))
    duplicate_candidate["reviewItems"][0]["candidates"][1]["candidateRef"] = duplicate_candidate[
        "reviewItems"
    ][0]["candidates"][0]["candidateRef"]
    assert any(
        "候选标识重复" in error for error in validate_manifest(duplicate_candidate, upload_map)
    )
    legacy_value_conflict = json.loads(json.dumps(manifest))
    legacy_value_conflict["reviewItems"][0].pop("candidates")
    assert validate_manifest(legacy_value_conflict, upload_map) == []
    assert len(manifest["files"]) == 7
    assert all("documentVersionKind" not in item for item in manifest["files"])
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
    assert "onlineSale" not in manifest["case"]
    assert [product["onlineSale"] for product in manifest["products"]] == [
        "NO",
        "YES",
        "UNKNOWN",
    ]
    assert {version["kind"] for version in manifest["documents"][0]["versions"]} == {
        "ELECTRONIC",
        "SCANNED",
    }
    assert manifest["documents"][0]["caseInspectionRefs"] == ["case-inspection:initial:2026-05-19"]
    assert "associationScope" not in manifest["documents"][0]
    assert "stageEvidence" not in manifest["documents"][0]
    assert manifest["documents"][0]["fileLinks"][0]["fileRef"].startswith("file:orig:")
    assert {
        (link.get("pageStart"), link.get("pageEnd"))
        for link in manifest["documents"][0]["fileLinks"]
    } == {(1, 1), (3, 3), (4, 4)}
    assert any(
        item["entityRef"] == "document:service-receipt-1"
        and item["fieldPath"] == "versions"
        and item["issueType"] == "DUPLICATE_CANDIDATE"
        for item in manifest["reviewItems"]
    )
    assert any(
        item["entityRef"] == "document:type-test-report-1"
        and item["fieldPath"] == "versions"
        and item["issueType"] == "LOW_CONFIDENCE"
        for item in manifest["reviewItems"]
    )
    assert manifest["documents"][1]["versions"] == []
    assert "stage" not in manifest["documents"][1]
    assert "caseInspectionRefs" not in manifest["documents"][1]
    assert manifest["documents"][2]["documentType"] == "ONSITE_PHOTO"
    assert "stage" not in manifest["documents"][2]
    assert "caseInspectionRefs" not in manifest["documents"][2]
    assert manifest["documents"][2]["inspectionRefs"] == []
    assert any(
        item["entityRef"] == "document:onsite-photo-unknown"
        and item["fieldPath"] == "stage"
        and item["issueType"] == "LOW_CONFIDENCE"
        for item in manifest["reviewItems"]
    )
    resolved_case_data = read_json(work / "case-data.resolved.json")
    unknown_photo = next(
        item
        for item in resolved_case_data["documents"]
        if item["clientRef"] == "document:onsite-photo-unknown"
    )
    assert unknown_photo["associationScope"] == "INSPECTION"
    assert unknown_photo["caseInspectionRefs"] == []
    bindings = read_json(work / "document-stage-bindings.json")["bindings"]
    assert (
        next(item for item in bindings if item["documentRef"] == "document:service-receipt-1")[
            "status"
        ]
        == "RESOLVED"
    )
    assert (
        next(item for item in bindings if item["documentRef"] == "document:onsite-photo-unknown")[
            "status"
        ]
        == "NEEDS_REVIEW"
    )
    assert any(
        item["entityRef"] == "case:32002207C202600033" and item["fieldPath"] == "caseType"
        for item in manifest["missingItems"]
    )

    screenshot_file_ref = next(
        item["clientRef"]
        for item in manifest["files"]
        if item["relativePath"] == "original/案卷截图.png"
    )

    legacy_case_online_sale = json.loads(json.dumps(manifest))
    legacy_case_online_sale["case"]["onlineSale"] = "YES"
    assert any(
        "case.onlineSale 已停用" in error
        for error in validate_manifest(legacy_case_online_sale, upload_map)
    )

    invalid_online_sale_review = json.loads(json.dumps(manifest))
    invalid_online_sale_review["reviewItems"].append(
        {
            "clientRef": "review:case-online-sale",
            "entityRef": "case:32002207C202600033",
            "fieldPath": "onlineSale",
            "issueType": "DATA_ANOMALY",
            "message": "旧版案卷级网售字段待迁移。",
        }
    )
    assert "onlineSale 待核对项只能关联 product 实体" in validate_manifest(
        invalid_online_sale_review, upload_map
    )

    invalid_online_sale_evidence = json.loads(json.dumps(manifest))
    invalid_online_sale_evidence["fieldEvidence"].append(
        {
            "entityRef": "case:32002207C202600033",
            "fieldPath": "onlineSale",
            "value": "YES",
            "trustLevel": "MANUAL",
            "sources": [{"fileRef": screenshot_file_ref}],
        }
    )
    assert "onlineSale 字段证据只能关联 product 实体" in validate_manifest(
        invalid_online_sale_evidence, upload_map
    )

    invalid_online_sale_missing = json.loads(json.dumps(manifest))
    invalid_online_sale_missing["missingItems"].append(
        {
            "entityRef": "case:32002207C202600033",
            "fieldPath": "onlineSale",
            "reason": "旧版案卷级网售缺失项。",
        }
    )
    assert "onlineSale 缺失项只能关联 product 实体" in validate_manifest(
        invalid_online_sale_missing, upload_map
    )

    duplicate_kind = json.loads(json.dumps(manifest))
    duplicate_kind["documents"][0]["versions"].append(
        dict(duplicate_kind["documents"][0]["versions"][0])
    )
    assert any(
        "同一 kind 只能有一个正式版本" in error
        for error in validate_manifest(duplicate_kind, upload_map)
    )

    duplicate_identity = json.loads(json.dumps(manifest))
    duplicate_document = dict(duplicate_identity["documents"][0])
    duplicate_document["clientRef"] = "document:service-receipt-duplicate"
    duplicate_document["versions"] = []
    duplicate_identity["documents"].append(duplicate_document)
    assert any(
        "逻辑文书身份重复" in error for error in validate_manifest(duplicate_identity, upload_map)
    )

    screenshot_as_version = json.loads(json.dumps(manifest))
    screenshot_as_version["documents"][0]["versions"][0]["fileRef"] = screenshot_file_ref
    assert any(
        "只能引用 NORMALIZED_FILE" in error
        for error in validate_manifest(screenshot_as_version, upload_map)
    )

    duplicate_source = json.loads(json.dumps(manifest))
    duplicate_source["reviewItems"] = [
        item
        for item in duplicate_source["reviewItems"]
        if not (
            item.get("entityRef") == "document:service-receipt-1"
            and item.get("fieldPath") == "versions"
            and item.get("issueType") == "DUPLICATE_CANDIDATE"
        )
    ]
    duplicate_source["documents"][0]["fileLinks"].append(
        {"fileRef": screenshot_file_ref, "relationRole": "DUPLICATE_COPY"}
    )
    assert any(
        "必须创建 DUPLICATE_CANDIDATE 待核对项" in error
        for error in validate_manifest(duplicate_source, upload_map)
    )
    duplicate_source["reviewItems"].append(
        {
            "clientRef": "review:document-source-duplicate",
            "entityRef": "document:service-receipt-1",
            "fieldPath": "versions",
            "issueType": "DUPLICATE_CANDIDATE",
            "message": "存在未选为正式版本的原始来源，待人工核对。",
        }
    )
    assert validate_manifest(duplicate_source, upload_map) == []

    legacy_manifest = json.loads(json.dumps(manifest))
    for product in legacy_manifest["products"]:
        for inspection in product["inspections"]:
            inspection.pop("caseInspectionRef")
    assert any(
        "未知 caseInspectionRef" in error
        for error in validate_manifest(legacy_manifest, upload_map)
    )

    inconsistent_group = json.loads(json.dumps(manifest))
    inconsistent_group["products"][2]["inspections"][0]["inspectionDate"] = "2026-05-20"
    assert any(
        "案卷检查分组 case-inspection:initial:2026-05-19 的阶段或检查日期不一致" in error
        for error in validate_manifest(inconsistent_group, upload_map)
    )

    mismatched_document_stage = json.loads(json.dumps(manifest))
    mismatched_document_stage["documents"][0]["stage"] = "RECHECK"
    assert any(
        "与父检查 case-inspection:initial:2026-05-19 的阶段不一致" in error
        for error in validate_manifest(mismatched_document_stage, upload_map)
    )

    wrong_inspection_entity = json.loads(json.dumps(manifest))
    wrong_inspection_entity["documents"][0]["inspectionRefs"] = ["product:1"]
    assert any(
        "inspectionRefs 只能引用产品检查实体" in error
        for error in validate_manifest(wrong_inspection_entity, upload_map)
    )

    missing_product_owner = json.loads(json.dumps(manifest))
    missing_product_owner["documents"][0]["productRefs"] = []
    assert any(
        "productRefs 必须包含 product:1" in error
        for error in validate_manifest(missing_product_owner, upload_map)
    )

    multiple_parent_documents = json.loads(json.dumps(manifest))
    multiple_parent_documents["documents"][0]["caseInspectionRefs"] = [
        "case-inspection:initial:2026-05-19",
        "external:initial-2026-05-19",
    ]
    assert any(
        "必须唯一关联一个 caseInspectionRef" in error
        for error in validate_manifest(multiple_parent_documents, upload_map)
    )

    product_outside_parent = json.loads(json.dumps(manifest))
    product_outside_parent["documents"][0]["productRefs"].append("product:2")
    assert any(
        "product:2 不属于父检查 case-inspection:initial:2026-05-19" in error
        for error in validate_manifest(product_outside_parent, upload_map)
    )

    orphan_inspection_document = json.loads(json.dumps(manifest))
    orphan_inspection_document["products"][0]["inspections"][0].pop("caseInspectionRef")
    assert any(
        "产品检查 inspection:1:initial_check 缺少 caseInspectionRef" in error
        for error in validate_manifest(orphan_inspection_document, upload_map)
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
        item["entityRef"] == "case:32002207C202600033" and item["fieldPath"] == "caseType"
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
    sampling_reinspection["products"][0]["inspections"][0]["reinspectionStatus"] = "COMPLETED"
    assert validate_manifest(sampling_reinspection, upload_map) == []

    inconsistent_reinspection = json.loads(json.dumps(sampling_reinspection))
    inconsistent_reinspection["products"][0]["inspections"][0]["reinspectionStatus"] = "NOT_APPLIED"
    assert any(
        "复检状态为未申请时不能包含复检详情" in error
        for error in validate_manifest(inconsistent_reinspection, upload_map)
    )

    legacy_stage = json.loads(json.dumps(manifest))
    legacy_stage["products"][0]["inspections"][0]["stage"] = "LAB_REINSPECTION"
    assert any(".stage 不合法" in error for error in validate_manifest(legacy_stage, upload_map))

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
        if item["entityRef"] == "case:32002207C202600033" and item["fieldPath"] == "caseType"
    )
    assert administrative_evidence["sources"][0]["kind"] == "RULE"
    assert administrative_evidence["sources"][0]["value"]["inspectionRef"] == "inspection:1:recheck"
    assert validate_manifest(administrative_manifest, upload_map) == []

    missing_rule_evidence = json.loads(json.dumps(administrative_manifest))
    missing_rule_evidence["fieldEvidence"] = [
        item
        for item in missing_rule_evidence["fieldEvidence"]
        if not (item["entityRef"] == "case:32002207C202600033" and item["fieldPath"] == "caseType")
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
    case_data_path.write_text(json.dumps(criminal_case_data, ensure_ascii=False), encoding="utf-8")
    compose_command(argparse.Namespace(work_dir=str(work), case_data=str(case_data_path)))
    criminal_manifest = read_json(work / "manifest.json")
    assert criminal_manifest["case"]["caseType"] == "CRIMINAL"
    assert validate_manifest(criminal_manifest, upload_map) == []

    indirect_criminal = json.loads(json.dumps(criminal_manifest))
    criminal_evidence = next(
        item
        for item in indirect_criminal["fieldEvidence"]
        if item["entityRef"] == "case:32002207C202600033"
        and item["fieldPath"] == "caseType"
        and item["value"] == "CRIMINAL"
    )
    criminal_evidence["sources"][0]["evidence"] = "行政处罚决定书。"
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
        analysis["fieldGroupPriorities"]["problemDescription"][0] == "SUPERVISION_RECORD_PDF_TEXT"
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
    assert (
        first_product["inspections"][0]["caseInspectionRef"]
        == (second_product["inspections"][0]["caseInspectionRef"])
    )
    assert first_product["inspections"][1]["caseInspectionRef"] == (
        "case-inspection:recheck:ordinal-2"
    )
    assert (
        first_product["inspections"][1]["caseInspectionRef"]
        == (second_product["inspections"][1]["caseInspectionRef"])
    )


def test_document_stage_binding_uses_body_then_related_document_and_never_filename() -> None:
    case_data = {
        "case": {"projectNo": "32002207C202600033"},
        "products": [
            {
                "sequence": 1,
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    },
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-27",
                        "inspectionResult": "QUALIFIED",
                    },
                ],
            },
            {
                "sequence": 2,
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    },
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-27",
                        "inspectionResult": "QUALIFIED",
                    },
                ],
            },
        ],
        "fieldEvidence": [
            {
                "entityRef": "document:manual-stage-wins",
                "fieldPath": "stage",
                "value": "INITIAL_CHECK",
                "trustLevel": "MANUAL",
                "sources": [{"kind": "MANUAL", "evidence": "人工核对父检查为初查。"}],
            },
            {
                "entityRef": "document:record-initial",
                "fieldPath": "stage",
                "value": "INITIAL_CHECK",
                "trustLevel": "CORROBORATED",
                "sources": [
                    {
                        "kind": "PDF_TEXT",
                        "relativePath": "original/初查记录电子版.pdf",
                        "page": 1,
                        "evidence": "消防产品监督检查记录，正文明确为初查。",
                    }
                ],
            },
        ],
    }
    build_entities(case_data)
    documents = [
        {
            "clientRef": "document:record-initial",
            "associationScope": "INSPECTION",
            "documentType": "PRODUCT_INSPECTION_RECORD",
            "productRefs": [],
            "inspectionRefs": [],
        },
        {
            "clientRef": "document:photo-recheck",
            "documentType": "ONSITE_PHOTO",
            "issueDate": "2026-05-28",
            "productRefs": [],
            "inspectionRefs": [],
            "stageEvidence": {
                "method": "BODY_TEXT",
                "sources": [
                    {
                        "kind": "SIGNED_SCAN_OCR",
                        "relativePath": "original/现场照片扫描件.pdf",
                        "page": 1,
                        "evidence": "整改复查现场照片，拍摄于2026年5月27日。",
                    }
                ],
            },
        },
        {
            "clientRef": "document:receipt-initial",
            "associationScope": "INSPECTION",
            "documentType": "SERVICE_RECEIPT",
            "issueDate": "2026-05-25",
            "productRefs": [],
            "inspectionRefs": [],
            "stageEvidence": {
                "method": "DOCUMENT_LINK",
                "relatedDocumentRef": "document:record-initial",
            },
        },
        {
            "clientRef": "document:photo-filename-only",
            "documentType": "ONSITE_PHOTO",
            "issueDate": "2026-05-20",
            "productRefs": [],
            "inspectionRefs": [],
            "stageEvidence": {
                "method": "UNKNOWN",
                "sources": [
                    {
                        "kind": "FILENAME_HINT",
                        "relativePath": "original/复查现场照片.pdf",
                        "evidence": "文件名写有复查。",
                    }
                ],
            },
        },
        {
            "clientRef": "document:type-report",
            "documentType": "TYPE_TEST_REPORT",
            "stage": "RECHECK",
            "caseInspectionRefs": ["case-inspection:recheck:2026-05-27"],
            "productRefs": ["product:1"],
            "inspectionRefs": ["inspection:1:recheck"],
        },
        {
            "clientRef": "document:ccc-certificate",
            "documentType": "CCC_CERTIFICATE",
            "stage": "RECHECK",
            "caseInspectionRefs": ["case-inspection:recheck:2026-05-27"],
            "productRefs": ["product:1"],
            "inspectionRefs": ["inspection:1:recheck"],
        },
        {
            "clientRef": "document:technical-appraisal",
            "documentType": "TECHNICAL_APPRAISAL_CERTIFICATE",
            "stage": "RECHECK",
            "caseInspectionRefs": ["case-inspection:recheck:2026-05-27"],
            "productRefs": ["product:1"],
            "inspectionRefs": ["inspection:1:recheck"],
        },
        {
            "clientRef": "document:manual-stage-wins",
            "associationScope": "INSPECTION",
            "documentType": "ONSITE_UNQUALIFIED_NOTICE",
            "stage": "INITIAL_CHECK",
            "caseInspectionRefs": ["case-inspection:initial:2026-05-19"],
            "productRefs": ["product:1"],
            "inspectionRefs": ["inspection:1:initial_check"],
            "stageEvidence": {
                "method": "BODY_TEXT",
                "sources": [
                    {
                        "kind": "SIGNED_SCAN_OCR",
                        "relativePath": "original/冲突扫描件.pdf",
                        "page": 1,
                        "evidence": "OCR 识别为整改复查通知书。",
                    }
                ],
            },
        },
    ]
    review_items: list[dict[str, object]] = []
    bindings = resolve_document_stage_bindings(case_data, documents, review_items)
    by_ref = {document["clientRef"]: document for document in documents}
    binding_by_ref = {binding["documentRef"]: binding for binding in bindings}

    assert by_ref["document:record-initial"]["stage"] == "INITIAL_CHECK"
    assert by_ref["document:record-initial"]["caseInspectionRefs"] == [
        "case-inspection:initial:2026-05-19"
    ]
    assert by_ref["document:photo-recheck"]["stage"] == "RECHECK"
    assert by_ref["document:photo-recheck"]["caseInspectionRefs"] == [
        "case-inspection:recheck:2026-05-27"
    ]
    assert by_ref["document:receipt-initial"]["stage"] == "INITIAL_CHECK"
    assert binding_by_ref["document:receipt-initial"]["resolutionMethod"] == "DOCUMENT_LINK"
    assert binding_by_ref["document:photo-filename-only"]["status"] == "NEEDS_REVIEW"
    assert "stage" not in by_ref["document:photo-filename-only"]
    assert by_ref["document:photo-filename-only"]["caseInspectionRefs"] == []
    assert any(
        item["entityRef"] == "document:photo-filename-only"
        and item["fieldPath"] == "stage"
        and item["issueType"] == "LOW_CONFIDENCE"
        for item in review_items
    )
    for document_ref in (
        "document:type-report",
        "document:ccc-certificate",
        "document:technical-appraisal",
    ):
        assert "stage" not in by_ref[document_ref]
        assert by_ref[document_ref]["caseInspectionRefs"] == []
        assert by_ref[document_ref]["inspectionRefs"] == []
        assert any(
            item["entityRef"] == document_ref and item["issueType"] == "DATA_ANOMALY"
            for item in review_items
        )
    assert by_ref["document:manual-stage-wins"]["stage"] == "INITIAL_CHECK"
    stage_conflict = next(
        item
        for item in review_items
        if item["entityRef"] == "document:manual-stage-wins"
        and item["issueType"] == "VALUE_CONFLICT"
    )
    assert stage_conflict["currentValue"] == "INITIAL_CHECK"
    assert stage_conflict["incomingValue"] == "RECHECK"


def test_document_stage_binding_date_order_is_last_and_ambiguous_stays_unknown() -> None:
    case_data = {
        "case": {"projectNo": "32002207C202600033"},
        "products": [
            {
                "sequence": 1,
                "inspections": [
                    {
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    },
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-27",
                        "inspectionResult": "QUALIFIED",
                    },
                ],
            }
        ],
        "fieldEvidence": [],
    }
    build_entities(case_data)
    ordered_documents = [
        {
            "clientRef": "document:order-early",
            "associationScope": "INSPECTION",
            "documentType": "RECTIFICATION_ORDER",
            "issueDate": "2026-05-20",
            "productRefs": [],
            "inspectionRefs": [],
        },
        {
            "clientRef": "document:order-later",
            "associationScope": "INSPECTION",
            "documentType": "RECTIFICATION_ORDER",
            "issueDate": "2026-05-28",
            "productRefs": [],
            "inspectionRefs": [],
        },
    ]
    reviews: list[dict[str, object]] = []
    bindings = resolve_document_stage_bindings(case_data, ordered_documents, reviews)
    assert [document["stage"] for document in ordered_documents] == ["INITIAL_CHECK", "RECHECK"]
    assert {binding["resolutionMethod"] for binding in bindings} == {"DATE_ORDER"}
    assert reviews == []

    ambiguous_case_data = json.loads(json.dumps(case_data))
    ambiguous_document = [
        {
            "clientRef": "document:record-one-of-two",
            "associationScope": "INSPECTION",
            "documentType": "PRODUCT_INSPECTION_RECORD",
            "issueDate": "2026-05-19",
            "productRefs": [],
            "inspectionRefs": [],
        }
    ]
    ambiguous_reviews: list[dict[str, object]] = []
    ambiguous_binding = resolve_document_stage_bindings(
        ambiguous_case_data, ambiguous_document, ambiguous_reviews
    )[0]
    assert ambiguous_binding["status"] == "NEEDS_REVIEW"
    assert "stage" not in ambiguous_document[0]
    assert ambiguous_reviews[0]["fieldPath"] == "stage"

    mismatched_case_data = {
        "case": {"projectNo": "32002207C202600035"},
        "products": [
            {
                "sequence": 1,
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
                "inspections": [
                    {
                        "stage": "RECHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-27",
                        "inspectionResult": "QUALIFIED",
                    }
                ],
            },
        ],
        "fieldEvidence": [],
    }
    build_entities(mismatched_case_data)
    mismatched_documents = [
        {
            "clientRef": "document:mismatch-early",
            "associationScope": "INSPECTION",
            "documentType": "RECTIFICATION_ORDER",
            "issueDate": "2026-05-20",
            "productRefs": ["product:2"],
            "inspectionRefs": [],
        },
        {
            "clientRef": "document:mismatch-later",
            "associationScope": "INSPECTION",
            "documentType": "RECTIFICATION_ORDER",
            "issueDate": "2026-05-28",
            "productRefs": ["product:1"],
            "inspectionRefs": [],
        },
    ]
    mismatch_reviews: list[dict[str, object]] = []
    mismatch_bindings = resolve_document_stage_bindings(
        mismatched_case_data, mismatched_documents, mismatch_reviews
    )
    assert {binding["status"] for binding in mismatch_bindings} == {"NEEDS_REVIEW"}
    assert all("stage" not in document for document in mismatched_documents)
    assert len(mismatch_reviews) == 2


def test_upload_keeps_create_files_manifest_validate_finalize_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_pdf = tmp_path / "原件.pdf"
    write_blank_pdf(source_pdf, pages=1)
    manifest = {
        "schemaVersion": "CaseImportManifestV1",
        "source": {
            "sourceType": "LOCAL_SKILL",
            "packageName": "四步上传测试",
            "containerKind": "DIRECTORY",
            "packageSha256": "sha256:" + "b" * 64,
            "packageHashMethod": "SORTED_RELATIVE_PATH_AND_FILE_SHA256",
            "extractedAt": "2026-08-01T00:00:00Z",
            "extractor": {"name": "xf-product-case-registry", "version": "0.6.0"},
        },
        "case": {
            "clientRef": "case:32002207C202600034",
            "projectNo": "32002207C202600034",
            "brigadeCode": "XISHAN",
            "unitName": "四步上传测试单位",
            "caseType": "UNKNOWN",
        },
        "products": [
            {
                "clientRef": "product:1",
                "sequence": 1,
                "name": "直流水枪",
                "onlineSale": "UNKNOWN",
                "inspections": [
                    {
                        "clientRef": "inspection:1:initial_check",
                        "caseInspectionRef": "case-inspection:initial:2026-05-19",
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    }
                ],
            }
        ],
        "documentRequirements": [],
        "files": [
            {
                "clientRef": "file:original",
                "storageKind": "ORIGINAL_FILE",
                "relativePath": "original/原件.pdf",
                "sha256": file_sha256(source_pdf),
                "mimeType": "application/pdf",
                "pageCount": 1,
            }
        ],
        "documents": [],
        "fieldEvidence": [],
        "missingItems": [
            {
                "entityRef": "case:32002207C202600034",
                "fieldPath": "caseType",
                "reason": "待人工核对。",
            }
        ],
        "reviewItems": [],
    }
    manifest_path = tmp_path / "manifest.json"
    upload_map_path = tmp_path / "upload-map.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    upload_map_path.write_text(
        json.dumps({"files": {"file:original": str(source_pdf)}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert validate_manifest(manifest, {"file:original": str(source_pdf)}) == []

    calls: list[tuple[str, str]] = []
    replay_finalized = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal replay_finalized
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/import-jobs":
            assert request.headers["Idempotency-Key"] == "xfpcr-v1-" + "b" * 64
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "status": "FINALIZED" if replay_finalized else "CREATED",
                        **({"resultSummary": {"created": 1}} if replay_finalized else {}),
                    }
                },
            )
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"fileRef": "file:original"})
        if request.method == "PUT" and request.url.path.endswith("/manifest"):
            return httpx.Response(200, json={"status": "MANIFEST_ACCEPTED"})
        if request.url.path.endswith("/validate"):
            return httpx.Response(200, json={"status": "VALIDATED"})
        if request.url.path.endswith("/finalize"):
            return httpx.Response(200, json={"status": "FINALIZED"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: object) -> httpx.Client:
        return real_client(
            transport=transport,
            timeout=kwargs.get("timeout"),
            follow_redirects=False,
            headers=kwargs.get("headers"),
        )

    monkeypatch.setattr(registry_cli.httpx, "Client", client_factory)
    args = argparse.Namespace(
        manifest=str(manifest_path),
        upload_map=str(upload_map_path),
        api_base="https://example.invalid",
        timeout=10.0,
        dry_run=False,
        finalize=True,
    )
    upload_command(args)
    assert calls == [
        ("GET", "/api/ready"),
        ("POST", "/api/v1/import-jobs"),
        ("POST", "/api/v1/import-jobs/11111111-1111-4111-8111-111111111111/files"),
        ("PUT", "/api/v1/import-jobs/11111111-1111-4111-8111-111111111111/manifest"),
        ("POST", "/api/v1/import-jobs/11111111-1111-4111-8111-111111111111/validate"),
        ("POST", "/api/v1/import-jobs/11111111-1111-4111-8111-111111111111/finalize"),
    ]
    state = read_json(tmp_path / "upload-state.json")
    assert state["finalized"] is True
    assert state["uploadedFileRefs"] == ["file:original"]

    replay_finalized = True
    calls.clear()
    upload_command(args)
    assert calls == [
        ("GET", "/api/ready"),
        ("POST", "/api/v1/import-jobs"),
    ]


def test_sync_document_versions_skips_same_hash_and_rejects_non_unique_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_electronic = tmp_path / "原始电子版.pdf"
    original_scanned = tmp_path / "原始扫描件.pdf"
    normalized_electronic = tmp_path / "规范电子版.pdf"
    normalized_scanned = tmp_path / "规范扫描件.pdf"
    for path in (
        original_electronic,
        original_scanned,
        normalized_electronic,
        normalized_scanned,
    ):
        write_blank_pdf(path, pages=1)
    original_electronic_sha = file_sha256(original_electronic)
    original_scanned_sha = file_sha256(original_scanned)
    normalized_electronic_sha = file_sha256(normalized_electronic)
    normalized_scanned_sha = file_sha256(normalized_scanned)
    manifest = {
        "schemaVersion": "CaseImportManifestV1",
        "source": {
            "sourceType": "LOCAL_SKILL",
            "packageName": "同步测试",
            "containerKind": "DIRECTORY",
            "packageSha256": "sha256:" + "a" * 64,
            "packageHashMethod": "SORTED_RELATIVE_PATH_AND_FILE_SHA256",
            "extractedAt": "2026-07-31T00:00:00Z",
            "extractor": {"name": "xf-product-case-registry", "version": "0.6.0"},
        },
        "case": {
            "clientRef": "case:32002207C202600033",
            "projectNo": "32002207C202600033",
            "brigadeCode": "XISHAN",
            "unitName": "测试单位",
            "caseType": "UNKNOWN",
        },
        "products": [
            {
                "clientRef": "product:1",
                "sequence": 1,
                "name": "直流水枪",
                "onlineSale": "YES",
                "inspections": [
                    {
                        "clientRef": "inspection:1:initial_check",
                        "caseInspectionRef": "case-inspection:initial:2026-05-19",
                        "stage": "INITIAL_CHECK",
                        "method": "ONSITE",
                        "inspectionDate": "2026-05-19",
                        "inspectionResult": "UNQUALIFIED",
                    }
                ],
            }
        ],
        "files": [
            {
                "clientRef": "file:original-electronic",
                "storageKind": "ORIGINAL_FILE",
                "relativePath": "original/原始电子版.pdf",
                "sha256": original_electronic_sha,
                "mimeType": "application/pdf",
                "pageCount": 1,
            },
            {
                "clientRef": "file:original-scanned",
                "storageKind": "ORIGINAL_FILE",
                "relativePath": "original/原始扫描件.pdf",
                "sha256": original_scanned_sha,
                "mimeType": "application/pdf",
                "pageCount": 1,
            },
            {
                "clientRef": "file:normalized-electronic",
                "storageKind": "NORMALIZED_FILE",
                "relativePath": "normalized/规范电子版.pdf",
                "sha256": normalized_electronic_sha,
                "mimeType": "application/pdf",
                "pageCount": 1,
                "sourceFileRef": "file:original-electronic",
                "sourcePageStart": 1,
                "sourcePageEnd": 1,
            },
            {
                "clientRef": "file:normalized-scanned",
                "storageKind": "NORMALIZED_FILE",
                "relativePath": "normalized/规范扫描件.pdf",
                "sha256": normalized_scanned_sha,
                "mimeType": "application/pdf",
                "pageCount": 1,
                "sourceFileRef": "file:original-scanned",
                "sourcePageStart": 1,
                "sourcePageEnd": 1,
            },
        ],
        "documents": [
            {
                "clientRef": "document:record-1",
                "documentType": "PRODUCT_INSPECTION_RECORD",
                "documentNo": "〔2026〕第0036号",
                "issueDate": "2026-05-19",
                "stage": "INITIAL_CHECK",
                "caseInspectionRefs": ["case-inspection:initial:2026-05-19"],
                "productRefs": ["product:1"],
                "inspectionRefs": ["inspection:1:initial_check"],
                "versions": [
                    {"fileRef": "file:normalized-electronic", "kind": "ELECTRONIC"},
                    {"fileRef": "file:normalized-scanned", "kind": "SCANNED"},
                ],
                "fileLinks": [
                    {
                        "fileRef": "file:original-electronic",
                        "relationRole": "PRIMARY",
                        "pageStart": 1,
                        "pageEnd": 1,
                    },
                    {
                        "fileRef": "file:original-scanned",
                        "relationRole": "SOURCE_COPY",
                        "pageStart": 1,
                        "pageEnd": 1,
                    },
                ],
            }
        ],
        "documentRequirements": [],
        "fieldEvidence": [
            {
                "entityRef": "document:record-1",
                "fieldPath": "stage",
                "value": "INITIAL_CHECK",
                "trustLevel": "DETERMINISTIC",
                "sources": [
                    {
                        "kind": "RULE",
                        "value": {
                            "caseInspectionRef": "case-inspection:initial:2026-05-19",
                            "inspectionRefs": ["inspection:1:initial_check"],
                            "method": "DOCUMENT_LINK",
                        },
                        "evidence": "按关联检查记录唯一绑定初查。",
                    }
                ],
            }
        ],
        "missingItems": [
            {
                "entityRef": "case:32002207C202600033",
                "fieldPath": "caseType",
                "reason": "待人工核对。",
            }
        ],
        "reviewItems": [],
    }
    manifest_path = tmp_path / "manifest.json"
    upload_map_path = tmp_path / "upload-map.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    upload_map_path.write_text(
        json.dumps(
            {
                "files": {
                    "file:original-electronic": str(original_electronic),
                    "file:original-scanned": str(original_scanned),
                    "file:normalized-electronic": str(normalized_electronic),
                    "file:normalized-scanned": str(normalized_scanned),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert (
        validate_manifest(
            manifest,
            {
                "file:original-electronic": str(original_electronic),
                "file:original-scanned": str(original_scanned),
                "file:normalized-electronic": str(normalized_electronic),
                "file:normalized-scanned": str(normalized_scanned),
            },
        )
        == []
    )
    sync_args = argparse.Namespace(
        manifest=str(manifest_path),
        upload_map=str(upload_map_path),
        api_base="https://example.invalid",
        timeout=10.0,
        dry_run=False,
    )
    with pytest.raises(RegistryError, match="必须先完成 upload validate/finalize"):
        sync_document_versions_command(sync_args)
    (tmp_path / "upload-state.json").write_text(
        json.dumps(
            {
                "stateVersion": 1,
                "manifestSha256": file_sha256(manifest_path),
                "packageSha256": manifest["source"]["packageSha256"],
                "jobId": "99999999-9999-4999-8999-999999999999",
                "finalized": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    server_state = {
        "uploadedKinds": set(),
        "caseMode": "unique",
        "documentMode": "unique",
        "jobStatus": "NEEDS_REVIEW",
        "jobPackageSha256": manifest["source"]["packageSha256"],
        "jobProjectNo": manifest["case"]["projectNo"],
        "serverVersion": 3,
        "failKind": "SCANNED",
        "failRefreshKind": None,
        "putAttempts": [],
        "serverHashes": {
            "ELECTRONIC": normalized_electronic_sha,
            "SCANNED": normalized_scanned_sha,
        },
        "incomingHashes": {
            "ELECTRONIC": normalized_electronic_sha,
            "SCANNED": normalized_scanned_sha,
        },
    }

    def server_document() -> dict[str, object]:
        return {
            "id": "11111111-1111-4111-8111-111111111111",
            "documentType": "PRODUCT_INSPECTION_RECORD",
            "documentNo": "〔2026〕第0036号",
            "issueDate": "2026-05-19T00:00:00.000Z",
            "stage": "INITIAL_CHECK",
            "version": server_state["serverVersion"],
            "deletedAt": None,
            "versions": [
                {
                    "kind": kind,
                    "deletedAt": None,
                    "fileAsset": {"sha256": server_state["serverHashes"][kind]},
                }
                for kind in ("ELECTRONIC", "SCANNED")
                if kind in server_state["uploadedKinds"]
            ],
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/import-jobs/99999999-9999-4999-8999-999999999999":
            return httpx.Response(
                200,
                json={
                    "id": "99999999-9999-4999-8999-999999999999",
                    "status": server_state["jobStatus"],
                    "packageSha256": server_state["jobPackageSha256"],
                    "resultSummary": {"projectNo": server_state["jobProjectNo"]},
                },
            )
        if request.url.path == "/api/v1/cases":
            cases = [
                {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "projectNo": "32002207C202600033",
                    "deletedAt": None,
                }
            ]
            if server_state["caseMode"] == "ambiguous":
                cases.append(
                    {
                        "id": "33333333-3333-4333-8333-333333333333",
                        "projectNo": "32002207C202600033",
                        "deletedAt": None,
                    }
                )
            return httpx.Response(200, json={"data": cases, "meta": {"total": len(cases)}})
        if request.url.path.endswith("/documents"):
            documents = [server_document()]
            if server_state["documentMode"] == "none":
                documents = []
            elif server_state["documentMode"] == "ambiguous":
                duplicate = dict(server_document())
                duplicate["id"] = "44444444-4444-4444-8444-444444444444"
                documents.append(duplicate)
            return httpx.Response(200, json=documents)
        if request.method == "PUT" and "/versions/" in request.url.path:
            kind = request.url.path.rsplit("/", maxsplit=1)[-1]
            marker = b'name="expectedDocumentVersion"'
            assert marker in request.content
            expected_version = int(
                request.content.split(marker, maxsplit=1)[1]
                .split(b"\r\n\r\n", maxsplit=1)[1]
                .split(b"\r\n", maxsplit=1)[0]
            )
            server_state["putAttempts"].append((kind, expected_version))
            if server_state["failKind"] == kind:
                return httpx.Response(500, json={"message": "fixture upload failure"})
            assert expected_version == server_state["serverVersion"]
            server_state["uploadedKinds"].add(kind)
            server_state["serverHashes"][kind] = server_state["incomingHashes"][kind]
            server_state["serverVersion"] += 1
            return httpx.Response(200, json=server_document())
        if request.url.path.startswith("/api/v1/documents/"):
            if server_state["failRefreshKind"] is not None:
                server_state["failRefreshKind"] = None
                return httpx.Response(503, json={"message": "fixture refresh failure"})
            return httpx.Response(200, json=server_document())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: object) -> httpx.Client:
        return real_client(
            transport=transport,
            timeout=kwargs.get("timeout"),
            follow_redirects=False,
            headers=kwargs.get("headers"),
        )

    monkeypatch.setattr(registry_cli.httpx, "Client", client_factory)
    args = sync_args
    with pytest.raises(RegistryError, match="SCANNED 版本同步失败"):
        sync_document_versions_command(args)
    failed_state = read_json(tmp_path / "document-version-sync-state.json")
    assert failed_state["status"] == "NEEDS_REVIEW"
    assert [item["status"] for item in failed_state["results"]] == [
        "UPLOADED",
        "FAILED",
    ]
    assert failed_state["results"][1]["failedPhase"] == "UPLOAD"
    assert failed_state["results"][1]["remoteWriteMayHaveSucceeded"] is False
    assert server_state["putAttempts"] == [("ELECTRONIC", 3), ("SCANNED", 4)]

    server_state["failKind"] = None
    sync_document_versions_command(args)
    assert server_state["putAttempts"] == [
        ("ELECTRONIC", 3),
        ("SCANNED", 4),
        ("SCANNED", 4),
    ]
    sync_state = read_json(tmp_path / "document-version-sync-state.json")
    assert sync_state["status"] == "DONE"
    assert sync_state["results"][0]["status"] == "UNCHANGED"
    assert sync_state["results"][1]["status"] == "UPLOADED"
    assert sync_state["results"][1]["expectedDocumentVersion"] == 4
    assert sync_state["results"][1]["serverDocumentVersion"] == 5

    sync_document_versions_command(args)
    assert server_state["putAttempts"] == [
        ("ELECTRONIC", 3),
        ("SCANNED", 4),
        ("SCANNED", 4),
    ]
    assert {
        item["status"]
        for item in read_json(tmp_path / "document-version-sync-state.json")["results"]
    } == {"UNCHANGED"}

    replacement_electronic = tmp_path / "规范电子版-替换.pdf"
    write_blank_pdf(replacement_electronic, pages=2)
    replacement_electronic_sha = file_sha256(replacement_electronic)
    electronic_file = next(
        item for item in manifest["files"] if item["clientRef"] == "file:normalized-electronic"
    )
    electronic_file["sha256"] = replacement_electronic_sha
    electronic_file["pageCount"] = 2
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    upload_map_document = read_json(upload_map_path)
    upload_map_document["files"]["file:normalized-electronic"] = str(replacement_electronic)
    upload_map_path.write_text(
        json.dumps(upload_map_document, ensure_ascii=False), encoding="utf-8"
    )
    server_state["incomingHashes"]["ELECTRONIC"] = replacement_electronic_sha
    server_state["failRefreshKind"] = "ELECTRONIC"
    with pytest.raises(RegistryError, match="REFRESH"):
        sync_document_versions_command(args)
    refresh_failed_state = read_json(tmp_path / "document-version-sync-state.json")
    assert refresh_failed_state["status"] == "NEEDS_REVIEW"
    assert refresh_failed_state["results"][0]["status"] == "FAILED"
    assert refresh_failed_state["results"][0]["failedPhase"] == "REFRESH"
    assert refresh_failed_state["results"][0]["remoteWriteMayHaveSucceeded"] is True
    assert server_state["putAttempts"][-1] == ("ELECTRONIC", 5)

    sync_document_versions_command(args)
    assert len(server_state["putAttempts"]) == 4
    assert {
        item["status"]
        for item in read_json(tmp_path / "document-version-sync-state.json")["results"]
    } == {"UNCHANGED"}

    server_state["caseMode"] = "ambiguous"
    with pytest.raises(RegistryError, match="匹配到 2 个活动案卷"):
        sync_document_versions_command(args)
    assert read_json(tmp_path / "document-version-sync-state.json")["status"] == "NEEDS_REVIEW"
    assert len(server_state["putAttempts"]) == 4

    server_state["caseMode"] = "unique"
    server_state["documentMode"] = "none"
    with pytest.raises(RegistryError, match="匹配到 0 项"):
        sync_document_versions_command(args)
    assert read_json(tmp_path / "document-version-sync-state.json")["status"] == "NEEDS_REVIEW"
    assert len(server_state["putAttempts"]) == 4

    server_state["documentMode"] = "ambiguous"
    with pytest.raises(RegistryError, match="匹配到 2 项"):
        sync_document_versions_command(args)
    assert read_json(tmp_path / "document-version-sync-state.json")["status"] == "NEEDS_REVIEW"
    assert len(server_state["putAttempts"]) == 4

    server_state["documentMode"] = "unique"
    server_state["jobStatus"] = "VALIDATED"
    with pytest.raises(RegistryError, match="服务端导入任务必须已终结"):
        sync_document_versions_command(args)
    assert len(server_state["putAttempts"]) == 4
    server_state["jobStatus"] = "NEEDS_REVIEW"
    server_state["jobPackageSha256"] = "sha256:" + "b" * 64
    with pytest.raises(RegistryError, match="包哈希与当前 manifest 不一致"):
        sync_document_versions_command(args)
    assert len(server_state["putAttempts"]) == 4
    server_state["jobPackageSha256"] = manifest["source"]["packageSha256"]
    server_state["jobProjectNo"] = "32002207C202699999"
    with pytest.raises(RegistryError, match="项目编号与当前 manifest 不一致"):
        sync_document_versions_command(args)
    assert len(server_state["putAttempts"]) == 4
    server_state["jobProjectNo"] = manifest["case"]["projectNo"]
    duplicate_local_manifest = json.loads(json.dumps(manifest))
    duplicate_local_document = json.loads(json.dumps(duplicate_local_manifest["documents"][0]))
    duplicate_local_document["clientRef"] = "document:record-duplicate"
    duplicate_local_manifest["documents"].append(duplicate_local_document)
    manifest_path.write_text(
        json.dumps(duplicate_local_manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="本地清单存在重复逻辑文书身份"):
        sync_document_versions_command(args)
    duplicate_state = read_json(tmp_path / "document-version-sync-state.json")
    assert duplicate_state["status"] == "NEEDS_REVIEW"
    assert duplicate_state["results"][0]["status"] == "DUPLICATE_IDENTITY"
    assert len(server_state["putAttempts"]) == 4
