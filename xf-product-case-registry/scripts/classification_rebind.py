"""Rebind a classification whose evidence file was extended by a later, lawful capture.

A case is classified from one source observation and the ledger pins the SHA-256 of the whole
``source-evidence.json``. Later captures of the same RWID rewrite that file: the earlier observation
moves into ``previousObservations`` (kept with its own ``capturedAt``) and the current one replaces
it, so the pinned digest no longer matches although nothing was lost. Archiving then refuses with
"归档后的分类证据摘要不一致".

``rebind`` accepts that only with a full change trail and never edits the recorded digest:

* the file is this project's own evidence and still carries the same RWID and the same ZIP digest
  as the ledger;
* exactly one retained previous observation has the capture time the classification was made at,
  and re-deriving the classification from it reproduces every recorded field;
* the superseded digest and the current observation (including what changed) are written beside the
  classification as ``evidenceReplacement`` facts.

Anything else is refused. A missing evidence file is handled by ``rebind-evidence`` (manual intake).
"""

from __future__ import annotations

import json
from typing import Any

if __package__:
    from . import ledger_views
    from . import source_intake as source
    from . import workspace_state as ws
else:
    import ledger_views
    import source_intake as source
    import workspace_state as ws

STATUS = "SUPERSEDED_OBSERVATION_RETAINED"
_DERIVED = ("initialResult", "initialInspectionDate", "statisticsYear", "reinspectionPending")


def rebind(layout: Any, project: str, reason: str) -> dict[str, Any]:
    project = ws._project_no(project)
    if not isinstance(reason, str) or not reason.strip():
        raise ws.WorkspaceStateError("重新绑定分类证据必须记录明确原因")
    with ws._waterline_lock(layout):
        data = ws.load_waterline(layout)
        case = data.get("cases", {}).get(project)
        if not case:
            raise ws.WorkspaceStateError("总账没有该项目")
        state = case.get("source") or {}
        classification = state.get("classification") or {}
        if not classification.get("evidencePath") or not classification.get("evidenceSha256"):
            raise ws.WorkspaceStateError("该案没有分类证据记录")
        path = layout.root / str(classification["evidencePath"]).replace("\\", "/")
        if not path.is_file():
            raise ws.WorkspaceStateError("分类证据文件缺失，应使用 rebind-evidence 绑定手动交件")
        actual = source._sha256(path)
        if actual == classification["evidenceSha256"]:
            raise ws.WorkspaceStateError("原分类证据文件仍然完整，无需重新绑定")
        evidence = _evidence(path, project)
        rwid = str(state.get("rwid") or "")
        current = (evidence.get("records") or {}).get(rwid)
        if not isinstance(current, dict) or current.get("projectNo") != project:
            raise ws.WorkspaceStateError("证据文件里没有本案当前 RWID 的记录")
        package = (current.get("package") or {}).get("sha256")
        if not package or package != state.get("packageSha256"):
            raise ws.WorkspaceStateError("证据文件当前记录的案卷包与总账登记不是同一个 ZIP")
        retained = _retained(current, classification)
        replacement = {
            "status": STATUS,
            "evidencePath": classification["evidencePath"],
            "evidenceSha256": actual,
            "packageSha256": package,
            "retainedObservation": {
                "capturedAt": retained.get("capturedAt"),
                "fingerprint": retained.get("fingerprint"),
                "projectInspectionStages": retained.get("projectInspectionStages"),
                "tags": retained.get("tags"),
                "screenshotSha256": (retained.get("screenshot") or {}).get("sha256"),
            },
            "currentObservation": {
                "capturedAt": current.get("capturedAt"),
                "fingerprint": current.get("fingerprint"),
                "projectInspectionStages": current.get("projectInspectionStages"),
                "tags": current.get("tags"),
            },
            "reason": reason.strip(),
            "replacedAt": ws._utc_now(),
            "supersededEvidencePath": classification["evidencePath"],
            "supersededEvidenceSha256": classification["evidenceSha256"],
            "supersededEvidenceState": "DIGEST_MISMATCH",
        }
        old = classification.get("evidenceReplacement")
        if old:
            same = ("status", "evidenceSha256", "supersededEvidenceSha256")
            if all(old.get(key) == replacement[key] for key in same):
                return {"status": "REUSED", "projectNo": project}
            raise ws.WorkspaceStateError("该案已绑定其他替代证据，拒绝覆盖")
        ws._deep_merge(case, {"source": {"classification": {"evidenceReplacement": replacement}}})
        ws._save_waterline_unlocked(layout, data)
    return {
        "status": "REBOUND_RETAINED_OBSERVATION",
        "projectNo": project,
        "retainedCapturedAt": replacement["retainedObservation"]["capturedAt"],
        "currentCapturedAt": replacement["currentObservation"]["capturedAt"],
    }


def _evidence(path: Any, project: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ws.WorkspaceStateError("分类证据文件无法读取") from error
    if (
        not isinstance(value, dict)
        or value.get("projectNo") != project
        or value.get("schemaVersion") not in (None, "SourceEvidenceV1")
    ):
        raise ws.WorkspaceStateError("分类证据文件不是本案的来源证据")
    return value


def _retained(current: dict[str, Any], classification: dict[str, Any]) -> dict[str, Any]:
    observed = classification.get("observedAt")
    candidates = [
        item
        for item in current.get("previousObservations") or []
        if isinstance(item, dict) and observed and item.get("capturedAt") == observed
    ]
    if len(candidates) != 1:
        raise ws.WorkspaceStateError(
            "证据文件没有保留与分类取得时点完全一致的那一次早期观察，无法证明变更轨迹"
        )
    retained = candidates[0]
    if retained.get("projectNo") != current.get("projectNo"):
        raise ws.WorkspaceStateError("保留的早期观察不是本案的记录")
    derived = ledger_views.source_qualification(
        retained.get("fields") or {}, retained.get("projectInspectionStages") or []
    )
    if not derived or any(derived.get(key) != classification.get(key) for key in _DERIVED):
        raise ws.WorkspaceStateError("保留的早期观察无法复现登记的分类结果")
    return retained
