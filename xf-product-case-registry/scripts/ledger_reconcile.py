"""Server facts advance existing local checkpoints; never upload or finalize here."""

from __future__ import annotations

import copy
from typing import Any

if __package__:
    from . import ledger_views as views
    from . import workspace_state as ws
else:
    import ledger_views as views
    import workspace_state as ws


def reconcile(
    api: Any,
    client: Any,
    api_base: str,
    origin: str,
    layout: Any,
    projects: list[str],
    identity: dict,
    *,
    apply: bool = False,
) -> dict:
    results = []
    ledger = ws.load_waterline(layout)
    for project in projects:
        record = ledger["cases"][project]
        work, manifest, state, issues = views.case_inputs(layout, project, record)
        if issues:
            results.append({"projectNo": project, "differences": issues, "applied": False})
            continue
        state_path = work / "upload-state.json"
        before_sha = api.file_sha256(state_path) if state else None
        changed = copy.deepcopy(state)
        try:
            if state:
                api.validate_upload_state(state)
                if state["origin"] != origin:
                    raise api.RegistryError("本地断点与服务器来源不一致")
                api.require_same_state_identity(state, identity)
            response = api.api_request(
                client, "GET", f"{api_base}/api/v2/case-import-state", params={"projectNo": project}
            )
            snapshot = (
                None if response.status_code == 404 else api.response_json(response, "统一案卷对账")
            )
            if snapshot:
                api.validate_case_import_state(snapshot, project)
                if state and snapshot["case"]["brigade"].get("code") != state["brigadeCode"]:
                    raise api.RegistryError("服务器与本地案卷大队不一致")
                if state.get("caseId") and state["caseId"] != snapshot["case"]["id"]:
                    raise api.RegistryError("服务器与本地案卷绑定不一致")
            observation = {
                "exists": bool(snapshot),
                "caseId": snapshot["case"]["id"] if snapshot else None,
                "origin": origin,
                "observedAt": api.utc_now(),
                "snapshotDigest": snapshot["snapshotDigest"] if snapshot else None,
            }
            differences = []
            if bool(record.get("systemObservation", {}).get("exists")) != bool(snapshot):
                differences.append("SYSTEM_REGISTRATION_OBSERVATION")
            if state and state["status"] != "VERIFIED":
                job = api.get_import_job(
                    client,
                    api_base,
                    state["jobId"],
                    state["packageSha256"],
                    project,
                    state["brigadeCode"],
                )
                if job["status"] == "FINALIZED":
                    if not snapshot or job["case"]["id"] != snapshot["case"]["id"]:
                        raise api.RegistryError("已终结任务与案卷快照不一致")
                    # Finalized import caches are disposable. Compare the formal
                    # directory and owners instead of treating cleared receivedFiles as lost bodies.
                    verified = api.verify_with_client(
                        client, api_base, manifest, require_nas_ready=False
                    )
                    if verified["caseId"] != snapshot["case"]["id"] or verified[
                        "filesVerified"
                    ] != len(state["filesProjection"]):
                        raise api.RegistryError("正式文件投影与原导入清单不一致")
                    changed["uploadedFileRefs"] = sorted(
                        f["clientRef"] for f in state["filesProjection"]
                    )
                    summary = api.finalize_summary(job["resultSummary"])
                    clean = (
                        summary.get("created") is True
                        and summary.get("conflictCount") == 0
                        and summary.get("skippedCount") == 0
                    )
                    changed.update(
                        status="FINALIZED_UNVERIFIED" if clean else "FINALIZED_WITH_CONFLICTS",
                        caseId=job["case"]["id"],
                        finalizedAt=job["finalizedAt"],
                        finalizeSummary=summary,
                    )
                else:
                    api.reconcile_uploaded_file_refs(changed, job, state["filesProjection"])
                api.validate_upload_state(changed)
                if changed != state:
                    differences.append("UPLOAD_CHECKPOINT_BEHIND_SERVER")
            if snapshot and (
                snapshot.get("unresolvedConflicts")
                or changed.get("status") == "FINALIZED_WITH_CONFLICTS"
            ):
                differences.append("UNRESOLVED_CONFLICTS")
            if not snapshot and state.get("caseId"):
                differences.append("SYSTEM_CASE_MISSING")
            if apply:
                if state and api.file_sha256(state_path) != before_sha:
                    raise api.RegistryError("本地断点已被其他操作更新，未覆盖")
                if changed != state:
                    api.write_json(state_path, changed)
                updates: dict = {"systemObservation": observation}
                if snapshot:
                    initial = next(
                        (i for i in snapshot["inspections"] if i.get("stage") == "INITIAL_CHECK"),
                        {},
                    )
                    updates["systemObservation"]["qualification"] = views.qualification(
                        {"initialInspection": initial}
                    )
                if changed.get("status") == "FINALIZED_UNVERIFIED":
                    updates.update(
                        state="UPLOADED_AWAITING_NAS",
                        upload={
                            "status": "FINALIZED_UNVERIFIED",
                            "caseId": changed["caseId"],
                            "jobId": changed["jobId"],
                        },
                        nasVerification={"status": "PENDING"},
                        errorSummary=None,
                    )
                ws.upsert_case(layout, project, **updates)
            results.append(
                {
                    "projectNo": project,
                    "systemRegistered": bool(snapshot),
                    "differences": differences,
                    "applied": apply,
                    "uploadStatus": changed.get("status"),
                    "bodyVerification": "NOT_PERFORMED",
                }
            )
        except api.RegistryError as error:
            results.append(
                {
                    "projectNo": project,
                    "differences": ["RECONCILE_BLOCKED"],
                    "error": str(error),
                    "applied": False,
                }
            )
    return {
        "schemaVersion": "CaseReconciliationV1",
        "readOnly": not apply,
        "serverWrites": [],
        "cases": results,
    }
