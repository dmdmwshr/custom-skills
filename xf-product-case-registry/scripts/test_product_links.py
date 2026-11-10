"""Reviewed recheck->initial product links beside an archived generation (ProductLinkReviewV1)."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from scripts import ledger_views as views
from scripts import product_links
from scripts import workspace_state as ws
from scripts.test_ledger_views import A, add_case, layout, write  # noqa: F401

DATE = _dt.date(2099, 2, 20)


def archived_with_recheck(layout, project=A):  # noqa: F811
    add_case(layout, project, archived=True)
    record = ws.load_waterline(layout)["cases"][project]
    work = Path(record["archive"]["workspacePath"])
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    manifest["case"].update(
        {
            "unitName": "单位",
            "unitAddress": "地址",
            "unitCategory": "重点单位",
            "brigadeCode": "XISHAN",
            "inspectionForm": "ROUTINE",
        }
    )
    manifest["initialInspection"]["products"] = [
        {"clientRef": "p-i1", "result": "UNQUALIFIED", "method": "ONSITE"}
    ]
    manifest["recheckInspection"] = {
        "inspectionDate": "2098-12-30",
        "products": [{"clientRef": "p-r1", "result": "QUALIFIED", "method": "ONSITE"}],
    }
    write(work / "manifest.json", manifest)
    (work / "source").mkdir(exist_ok=True)
    (work / "source" / "判定不合格通知书.pdf").write_bytes(b"%PDF-1.4 fixture")
    return work


def life(layout, project=A):  # noqa: F811
    return views.describe(layout, project, ws.load_waterline(layout)["cases"][project], today=DATE)[
        "lifecycle"
    ]


def links_file(tmp_path, **override):
    link = {
        "recheckRef": "p-r1",
        "initialRef": "p-i1",
        "basis": "IDENTICAL_ATTRIBUTES",
        "statement": "判定不合格通知书点名同型号；复查记录同型号同生产者同部位",
        "evidence": [{"file": "判定不合格通知书.pdf", "pages": "1"}],
    }
    link.update(override)
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps({"reviewer": "审核人", "links": [link]}, ensure_ascii=False), encoding="utf-8"
    )
    return str(path)


def test_review_closes_only_the_reviewed_chain_and_leaves_the_archive_untouched(layout, tmp_path):  # noqa: F811
    work = archived_with_recheck(layout)
    before = (work / "manifest.json").read_bytes()
    assert "RECHECK_PRODUCT_LINK_UNPROVEN" in life(layout)["localCauses"]
    result = product_links.attach(layout, A, links_file(tmp_path))
    assert result["status"] == "ATTACHED" and result["links"] == 1
    after = life(layout)
    assert (
        "RECHECK_PRODUCT_LINK_UNPROVEN" not in after["localCauses"]
        and after["recheck"]["linkUnproven"] == 0
    )
    assert (work / "manifest.json").read_bytes() == before  # archived manifest is never rewritten
    stored = ws.load_waterline(layout)["cases"][A]["productLinks"]
    assert stored["links"][0]["evidence"][0]["sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    "override",
    [
        {"recheckRef": "p-x"},
        {"initialRef": "p-x"},
        {"basis": "SAME_NAME"},
        {"statement": "同名"},
        {"evidence": []},
        {"evidence": [{"file": "../manifest.json"}]},
        {"evidence": [{"file": "不存在.pdf"}]},
    ],
)
def test_attach_rejects_unknown_refs_weak_basis_and_unbound_evidence(layout, tmp_path, override):  # noqa: F811
    archived_with_recheck(layout)
    with pytest.raises(ws.WorkspaceStateError):
        product_links.attach(layout, A, links_file(tmp_path, **override))
    assert "productLinks" not in ws.load_waterline(layout)["cases"][A]


def test_review_becomes_inert_when_the_manifest_changes_and_never_overrides_existing_refs(
    layout, tmp_path  # noqa: F811
):  # noqa: F811
    work = archived_with_recheck(layout)
    product_links.attach(layout, A, links_file(tmp_path))
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    manifest["initialInspection"]["inspectionDate"] = "2098-12-11"
    write(work / "manifest.json", manifest)
    assert (
        "RECHECK_PRODUCT_LINK_UNPROVEN" in life(layout)["localCauses"]
    )  # stale review is not reused
    manifest["recheckInspection"]["products"][0]["initialProductRef"] = "p-i1"
    write(work / "manifest.json", manifest)
    with pytest.raises(ws.WorkspaceStateError):
        product_links.attach(layout, A, links_file(tmp_path))


def test_conflicting_second_review_is_refused(layout, tmp_path):  # noqa: F811
    work = archived_with_recheck(layout)
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    manifest["initialInspection"]["products"].append(
        {"clientRef": "p-i2", "result": "UNQUALIFIED", "method": "ONSITE"}
    )
    write(work / "manifest.json", manifest)
    product_links.attach(layout, A, links_file(tmp_path))
    with pytest.raises(ws.WorkspaceStateError):
        product_links.attach(layout, A, links_file(tmp_path, initialRef="p-i2"))
    assert ws.load_waterline(layout)["cases"][A]["productLinks"]["links"][0]["initialRef"] == "p-i1"


def test_unarchived_case_cannot_be_reviewed(layout, tmp_path):  # noqa: F811  # noqa: F811
    add_case(layout, A, archived=False)
    with pytest.raises(ws.WorkspaceStateError):
        product_links.attach(layout, A, links_file(tmp_path))
