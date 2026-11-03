"""Local-only brigade entities (e.g. the water brigade) that are not among the eight cloud brigades.

The eight-brigade list stays the only set allowed for Feishu reporting scopes and member rights.
A case jurisdiction that the real source states (``消防管辖``) as a local entity may still be
organised and archived locally. Anything the source evidence does not state stays rejected.
"""

from __future__ import annotations

import json
from typing import Any

if __package__:
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import source_intake as source
    import workspace_state as ws

# code -> the exact name a real source jurisdiction must contain
LOCAL_ONLY_BRIGADES = {"SHUISHANG": "水上大队"}
JURISDICTION_FIELD = "消防管辖"


def _known_rwids(state: dict[str, Any]) -> set[str]:
    found = {str(key) for key in (state.get("annualIdentitiesByRwid") or {})}
    if state.get("rwid"):
        found.add(str(state["rwid"]))
    return found


def _classification_texts(layout: Any, project: str, state: dict[str, Any]) -> list[str]:
    """Jurisdiction texts of a SourceEvidenceV1 that is this project's own, at its bound digest."""
    classification = state.get("classification") or {}
    raw = classification.get("evidencePath")
    if not raw:
        return []
    path = layout.root / str(raw).replace("\\", "/")
    if not path.is_file() or source._sha256(path) != classification.get("evidenceSha256"):
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return []
    if not isinstance(value, dict) or value.get("projectNo") != project:
        return []
    if "schemaVersion" in value and value["schemaVersion"] != "SourceEvidenceV1":
        return []  # an explicit but wrong version is not accepted as this evidence
    rwids = _known_rwids(state)
    texts: list[str] = []
    for rwid, item in (value.get("records") or {}).items():
        fields = (item or {}).get("fields") or {}
        identities = {item.get("projectNo"), fields.get("项目编号")} - {None}
        if str(rwid) not in rwids or identities != {project}:
            continue  # foreign record / no project identity: never counts for this case
        text = fields.get(JURISDICTION_FIELD)
        if isinstance(text, str):
            texts.append(text)
    return texts


def _manual_texts(layout: Any, project: str, state: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for binding in (state.get("manualIntakes") or {}).values():
        raw = binding.get("evidencePath")
        path = layout.root / str(raw).replace("\\", "/") if raw else None
        if not path or not path.is_file() or source._sha256(path) != binding.get("evidenceSha256"):
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        fields = receipt.get("fields") if isinstance(receipt, dict) else None
        if (
            not isinstance(fields, dict)
            or receipt.get("schemaVersion") != "ManualCaseIntakeReceiptV1"
            or receipt.get("projectNo") != project
            or fields.get("projectNo") != project
        ):
            continue
        text = fields.get(JURISDICTION_FIELD)
        if isinstance(text, str):
            texts.append(text)
    return texts


def _jurisdiction_texts(layout: Any, project: str, record: dict[str, Any]) -> list[str]:
    state = record.get("source") or {}
    return _classification_texts(layout, project, state) + _manual_texts(layout, project, state)


def check_case(layout: Any, project: str, case: dict[str, Any]) -> None:
    """Reject a local-only brigade code unless this case's real source evidence states it."""
    code = case.get("brigadeCode")
    if code not in LOCAL_ONLY_BRIGADES:
        return
    name = LOCAL_ONLY_BRIGADES[code]
    record = (ws.load_waterline(layout).get("cases") or {}).get(project)
    if not record:
        raise ws.WorkspaceStateError("总账没有该项目，无法核对本地大队来源证据")
    if not any(name in text for text in _jurisdiction_texts(layout, project, record)):
        raise ws.WorkspaceStateError(
            f"案卷标为{name}，但已绑定的真实来源证据（须为本案项目身份的分类证据或手动交件回执）没有对应的“消防管辖”记载，拒绝"
        )
