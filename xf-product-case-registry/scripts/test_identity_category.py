"""Unit category from a trusted annual SourceIdentityV1 of the same case (lifecycle fallback)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

import pytest

from scripts import ledger_views as views
from scripts import workspace_state as ws
from scripts.test_ledger_views import A, add_case, layout, write  # noqa: F401

DATE = _dt.date(2099, 2, 20)


def prepared(layout):  # noqa: F811
    add_case(layout, A, archived=True)
    record = ws.load_waterline(layout)["cases"][A]
    work = Path(record["archive"]["workspacePath"])
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    manifest["case"].update(
        {
            "unitName": "单位",
            "unitAddress": "地址",
            "brigadeCode": "XISHAN",
            "inspectionForm": "ROUTINE",
        }
    )
    write(work / "manifest.json", manifest)


def identity(layout, *, project=A, rwid=A, category="重点单位", tamper=False, name="a"):  # noqa: F811
    folder = layout.root / "工作区" / "采集批次" / "batch-1" / "identities" / A
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / f"{rwid}-{name}.png"
    image.write_bytes(b"\x89PNG fixture " + name.encode())
    digest = "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()
    if tamper:
        image.write_bytes(b"changed")
    fields = {"项目编号": project, "单位类别": category}
    value = {
        "schemaVersion": "SourceIdentityV1",
        "projectNo": project,
        "rwid": rwid,
        "observedAt": "2099-02-01T10:00:00+08:00",
        "fields": fields,
        "screenshot": {"relativePath": image.relative_to(layout.root).as_posix(), "sha256": digest},
    }
    write(folder / f"{rwid}-{name}.json", value)


def causes(layout):  # noqa: F811
    record = ws.load_waterline(layout)["cases"][A]
    life = views.describe(layout, A, record, today=DATE)["lifecycle"]
    return life["localCauses"] + life["sourceCauses"]


def test_trusted_identity_category_closes_the_missing_unit_category_gap(layout):  # noqa: F811
    prepared(layout)
    assert "MISSING_unitCategory" in causes(layout)
    identity(layout)
    assert "MISSING_unitCategory" not in causes(layout)


@pytest.mark.parametrize(
    "kwargs",
    [{"project": "99999999T209900002"}, {"rwid": "unbound"}, {"category": " "}, {"tamper": True}],
    ids=["foreign-project", "unbound-rwid", "empty-category", "screenshot-digest-changed"],
)
def test_untrusted_identity_never_supplies_the_category(layout, kwargs):  # noqa: F811
    prepared(layout)
    identity(layout, **kwargs)
    assert "MISSING_unitCategory" in causes(layout)
