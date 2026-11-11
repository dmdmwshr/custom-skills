"""Read-only views over the one project-number keyed case waterline."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__:
    from . import case_lifecycle
    from . import initial_scope
    from . import local_validity
    from . import product_links
    from . import source_coverage as coverage_api
    from . import source_facts
    from . import workspace_state as ws
else:
    import case_lifecycle
    import initial_scope
    import local_validity
    import product_links
    import source_coverage as coverage_api
    import source_facts
    import workspace_state as ws



def _shanghai_zone() -> Any:
    try:
        return ZoneInfo("Asia/Shanghai")
    except Exception:  # tzdata missing on a minimal host: Shanghai has no DST, fixed +08:00 is the same rule
        from datetime import timezone, timedelta

        return timezone(timedelta(hours=8), name="Asia/Shanghai")


def _instant(value: Any) -> datetime | None:
    """A zone-aware instant for an observation time; a naive time is read as Shanghai, an unparsable one is None."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_shanghai_zone())


def observation_day(value: Any) -> str | None:
    """The Shanghai business day of an observation time (``2026-11-09T15:39:25-08:00`` is 2026-11-10), not a text prefix."""

    parsed = _instant(value)
    if parsed is not None:
        return parsed.astimezone(_shanghai_zone()).date().isoformat()
    text = str(value or "")[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


# 业务视图 18 列的显式宽度(字符数)。顺序与导出表头一致；新增/删除表头列时必须同步，回归测试会校验两者等长。
LEDGER_COLUMN_WIDTHS = [
    24, 34, 12, 15, 10, 15, 15, 20, 20, 16, 15, 13, 16, 27, 22, 48, 27, 28, 24, 30, 30, 34,
]

_UNQUALIFIED_CELL = re.compile(r"^(?:[1-9][0-9]*项)?不合格(?:[(（][^)）]*[)）])?$")
_BASIS_LABEL = {
    "QUALITY": "产品质量不合格",
    "MARKET_ACCESS": "市场准入不合格",
    "QUALITY_AND_MARKET_ACCESS": "产品质量与市场准入均不合格",
}
_METHOD_LABEL = {"SAMPLING": "抽样送检", "ONSITE": "现场检查"}
_RESULT_LABEL = {"QUALIFIED": "合格", "UNQUALIFIED": "不合格", "PENDING": "结果待定"}


def _cell_outcome(text: str) -> str:
    text = text.strip()
    if _UNQUALIFIED_CELL.fullmatch(text):
        return "UNQUALIFIED"
    if text.startswith("未发现不合格现象"):
        return "NO_FINDING"
    if text == "未检查":
        return "NOT_CHECKED"
    return "OTHER"


def _group_outcome(cells: list[str]) -> str | None:
    outcomes = {_cell_outcome(cell) for cell in cells}
    if not cells:
        return None
    if "UNQUALIFIED" in outcomes:
        return "UNQUALIFIED"
    if outcomes == {"NO_FINDING"}:
        return "NO_FINDING"
    if outcomes <= {"NO_FINDING", "NOT_CHECKED"}:
        return "NOT_CHECKED"
    return "OTHER"


def source_product_summary(evidence: dict[str, Any], project: str) -> dict[str, Any] | None:
    """Product quality and market-access cells exactly as the source detail page showed them.

    Kept apart from every local record or classification: a market-access finding is never
    reported as a quality finding, and neither is derived from document titles or task codes.
    """
    field_sets = []
    if isinstance(evidence.get("fields"), dict) and evidence.get("projectNo") == project:
        field_sets.append(evidence["fields"])
    for item in (evidence.get("records") or {}).values():
        if isinstance(item, dict) and item.get("projectNo") == project and isinstance(item.get("fields"), dict):
            field_sets.append(item["fields"])
    products = [
        p for fields in field_sets for p in (fields.get("检查产品信息") or []) if isinstance(p, dict)
    ]
    if not products:
        return None

    def cells(key: str) -> list[str]:
        values = [str(p.get(key, "")).strip() for p in products]
        return list(dict.fromkeys(v for v in values if v))

    quality, market = cells("产品质量现场检查情况"), cells("市场准入检查情况")
    quality_outcome, market_outcome = _group_outcome(quality), _group_outcome(market)
    if quality_outcome == "UNQUALIFIED" and market_outcome == "UNQUALIFIED":
        basis = "QUALITY_AND_MARKET_ACCESS"
    elif quality_outcome == "UNQUALIFIED":
        basis = "QUALITY"
    elif market_outcome == "UNQUALIFIED":
        basis = "MARKET_ACCESS"
    else:
        basis = None
    return {
        "qualityCells": quality,
        "marketAccessCells": market,
        "qualityOutcome": quality_outcome,
        "marketAccessOutcome": market_outcome,
        "unqualifiedBasis": basis,
    }


def _archived_manifest(layout: Any, project: str, record: dict[str, Any]) -> dict[str, Any]:
    """Manifest of the ledger's explicit archived generation, even when a stub active workspace directory exists."""
    candidate = (record.get("archive") or {}).get("workspacePath")
    if not candidate:
        return {}
    try:
        path = layout.resolve_recorded_path(candidate)
        suffix = path.name.removeprefix(project + "-")
        if path.parent != layout.root / "工作区" / "历史工作区" or not path.name.startswith(project + "-") or not ws.ARCHIVE_GENERATION.fullmatch(suffix):
            return {}
        manifest = _read(path / "manifest.json", layout.root)
    except (ws.WorkspaceStateError, OSError, ValueError):
        return {}
    return manifest if manifest.get("case", {}).get("projectNo") == project else {}


def _identity_unit_category(
    layout: Any,
    project: str,
    source: dict[str, Any],
    lifecycle_evidence: Any,
    manifest: dict[str, Any] | None,
) -> str | None:
    """Unit category from the newest trusted annual SourceIdentityV1 of this very case.

    Only used when neither the saved detail nor the manifest carries a category. The identity file must
    name this project, one RWID the ledger itself binds to the case, and its saved screenshot must still
    match the recorded digest; the newest observation that shows a non-empty category wins.
    """
    detail = (lifecycle_evidence[0] if lifecycle_evidence else None) or {}
    if str(detail.get("单位类别") or "").strip() or str(((manifest or {}).get("case") or {}).get("unitCategory") or "").strip():
        return None
    known = _known_rwids(source)
    best: tuple[str, str] | None = None
    batches = layout.root / "工作区" / "采集批次"
    try:
        paths = sorted(batches.glob(f"*/identities/{project}/*.json"))
    except OSError:
        return None
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            fields = value["fields"]
            shot = value["screenshot"]
            observed = str(value["observedAt"])
            category = fields.get("单位类别")
            if (
                value.get("schemaVersion") != "SourceIdentityV1"
                or value.get("projectNo") != project
                or str(value.get("rwid")) not in known
                or fields.get("项目编号") not in (None, project)
                or not isinstance(category, str)
                or not category.strip()
            ):
                continue
            image = layout.resolve_recorded_path(layout.root / str(shot["relativePath"]).replace("\\", "/"))
            if "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest() != shot.get("sha256"):
                continue
        except (OSError, KeyError, TypeError, ValueError, ws.WorkspaceStateError):
            continue
        moment = _instant(observed)
        if moment is None:
            continue
        if best is None or moment > (_instant(best[0]) or moment):
            best = (observed, category.strip())
    return best[1] if best else None


def _known_rwids(source: dict[str, Any]) -> set[str]:
    """RWIDs this ledger record itself binds to the case (current, observed, annual identities, pending observation)."""
    known = {str(source["rwid"])} if source.get("rwid") else set()
    for key in ("observationsByRwid", "pendingObservationsByRwid", "annualIdentitiesByRwid"):
        known |= {str(k) for k in (source.get(key) or {})}
    return known


def _product_root_problem(fields: dict[str, Any], project: str) -> str | None:
    """A saved detail must show a [产] product root, and a case number in any [产] root must be this case's."""
    roots = [
        str((item.get("标题") or item.get("title") or "") if isinstance(item, dict) else item)
        for item in (fields.get("文书目录") or [])
    ]
    product_roots = [t for t in roots if t.lstrip().startswith("[产]")]
    if not product_roots:
        return "NO_PRODUCT_ROOT"
    numbers = {n for t in product_roots for n in re.findall(r"\d{8}C\d{9}", t)}
    return None if numbers <= {project} else "FOREIGN_PRODUCT_ROOT"


def _detail_problems(item: Any, key: str, project: str, source: dict[str, Any], known: set[str]) -> list[str]:
    """Why a saved detail record cannot be consumed as this case's observation (empty list = trusted).

    Three layers are bound separately, and a record's own digest is never enough by itself:
    (1) content: ``businessFingerprint`` must equal the fingerprint recomputed from ``fields``;
    (2) registration: the ledger must have registered *this* content for *this* RWID -- the current RWID through
        ``detailBusinessFingerprint`` (required), any other bound RWID through ``observationsByRwid[rwid].fingerprint``
        equal to the record's fingerprint;
    (3) time: ``capturedAt`` must be a time the ledger registered for this RWID (``source.capturedAt`` for the current RWID,
        ``observationsByRwid[rwid].observedAt``, ``classification.observedAt``), so an edited timestamp cannot manufacture a
        later valid observation.
    The detail must also name this project number, belong to an RWID the ledger binds to the case, and show this case's
    product root. A legitimate later observation passes because intake registers its content and time together.
    """
    if not isinstance(item, dict) or not isinstance(item.get("fields"), dict) or not item["fields"]:
        return ["NO_FIELDS"]
    fields, problems = item["fields"], []
    if item.get("projectNo") != project or str(fields.get("项目编号") or "").strip().upper() != project:
        problems.append("PROJECT_MISMATCH")
    if key not in known:
        problems.append("RWID_NOT_BOUND_TO_CASE")
    if __package__:
        from . import source_intake
    else:
        import source_intake
    recorded = item.get("businessFingerprint")
    if not recorded:
        problems.append("NO_DIGEST")
    elif source_intake._business_detail_fingerprint(fields) != recorded:
        problems.append("DIGEST_MISMATCH")
    current = key == str(source.get("rwid") or "")
    observation = (source.get("observationsByRwid") or {}).get(key) or {}
    if current:
        ledger_fp = source.get("detailBusinessFingerprint")
        if not ledger_fp:
            problems.append("LEDGER_BINDING_MISSING")
        elif recorded and ledger_fp != recorded:
            problems.append("LEDGER_DIGEST_MISMATCH")
    elif observation.get("fingerprint") != source_intake._fingerprint(item):
        problems.append("OBSERVATION_NOT_REGISTERED")
    registered_times = {observation.get("observedAt"), (source.get("classification") or {}).get("observedAt")}
    if current:
        registered_times.add(source.get("capturedAt"))
    if not item.get("capturedAt") or item["capturedAt"] not in registered_times:
        problems.append("OBSERVED_AT_UNREGISTERED")
    if (root := _product_root_problem(fields, project)):
        problems.append(root)
    return problems


def _saved_detail(layout: Any, project: str, source: dict[str, Any]) -> tuple[tuple[dict[str, Any], str | None, str | None] | None, list[str]]:
    """Latest *trusted* saved source detail of this exact project, and why other saved details were refused.

    Used when the classification-bound evidence was rewritten later. A saved record is consumed only through
    ``_detail_problems`` (verifiable own digest, ledger-bound RWID, same case fields, this case's product root); records
    that fail are never used as a fallback, and their reasons are returned so the assessment keeps the case open.
    """
    candidates = [layout.pending_case_dir(project) / "source-evidence.json"]
    completed = layout.completed_case_dir(project)
    if completed.is_dir():
        candidates += sorted(completed.glob("*/source-evidence.json"), reverse=True)
    known, wanted, refused = _known_rwids(source), source.get("rwid"), []
    for path in candidates:
        try:
            evidence = _read(path, layout.root)
        except (ws.WorkspaceStateError, OSError, ValueError):
            continue
        if not evidence:
            continue  # no such saved file
        if evidence.get("projectNo") != project:
            refused.append("EVIDENCE_PROJECT_MISMATCH")
            continue
        records = evidence.get("records") or {}
        for key in ([wanted] if wanted in records else []) + [k for k in records if k != wanted]:
            problems = _detail_problems(records[key], str(key), project, source, known)
            if problems:
                refused += [f"{key}:{p}" for p in problems]
                continue
            item = records[key]
            captured = item.get("capturedAt")
            return (item["fields"], observation_day(captured), captured), []
    return None, refused


def _evidence_fields(evidence: dict[str, Any], project: str) -> tuple[dict[str, Any], str | None, str | None] | None:
    """Saved detail fields (and the observation day) of this project from a SourceEvidenceV1 / identity document."""
    candidates = [(evidence.get("fields"), evidence.get("capturedAt"))] if evidence.get("projectNo") == project else []
    for item in (evidence.get("records") or {}).values():
        if isinstance(item, dict) and item.get("projectNo") == project:
            candidates.append((item.get("fields"), item.get("capturedAt")))
    for fields, captured in candidates:
        if isinstance(fields, dict) and fields:
            return fields, observation_day(captured), captured
    return None


def record_initial_fact(manifest: dict[str, Any]) -> str:
    """The local inspection record's own initial facts; not a source conclusion."""
    products = (manifest.get("initialInspection") or {}).get("products") or []
    facts = []
    for product in products:
        if not isinstance(product, dict):
            continue
        result = effective_result(product)
        if product.get("method") is None and result == "UNKNOWN":
            continue  # nothing registered locally; do not print a placeholder as if it were a fact
        facts.append(f"{_METHOD_LABEL.get(product.get('method'), '方式未登记')}·{_RESULT_LABEL.get(result, '未登记')}")
    return "；".join(dict.fromkeys(facts))


def ledger_header_height(headers, widths):
    """表头按中文约 2 个字符宽、每行约 16 磅估算所需高度，至少 32 磅，使长表头换行后完整可见。"""
    import math

    lines = max(
        (math.ceil(sum(2 if ord(ch) > 127 else 1 for ch in str(h)) * 1.15 / max(w - 1, 1)) for h, w in zip(headers, widths)),
        default=1,
    )
    return max(32, 16 * lines + 8)

def recent_window(today: date | None = None) -> dict[str, str]:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    month_index = today.year * 12 + today.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    start = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    return {"startDate": start.isoformat(), "endDate": today.isoformat()}


def scan_filters(today: date | None = None) -> dict[str, Any]:
    return {
        "selectionMode": "RECENT_DOCUMENT_ACTIVITY",
        **recent_window(today),
        "dateFieldLabel": "创建日期",
        "documentType": "ALL",
        "jurisdiction": "全部管辖单位(含派出所)",
        "brigadeScope": "ALL",
        "timezone": "Asia/Shanghai",
    }


def effective_result(product: dict[str, Any]) -> str:
    if product.get("method") == "SAMPLING" and product.get("reinspectionApplied") == "YES":
        result = product.get("reinspectionResult")
        return result if result in {"QUALIFIED", "UNQUALIFIED"} else "PENDING"
    result = product.get("result")
    return result if result in {"QUALIFIED", "UNQUALIFIED", "PENDING"} else "UNKNOWN"


def qualification(manifest: dict[str, Any]) -> dict[str, Any]:
    initial = manifest.get("initialInspection") or {}
    products = initial.get("products") or []
    outcomes = [effective_result(p) for p in products if isinstance(p, dict)]
    result = (
        "UNQUALIFIED"
        if "UNQUALIFIED" in outcomes
        else "QUALIFIED"
        if outcomes and all(r == "QUALIFIED" for r in outcomes)
        else "UNKNOWN"
    )
    raw_date = initial.get("inspectionDate")
    try:
        inspection_date = date.fromisoformat(raw_date[:10]).isoformat() if raw_date else None
    except (ValueError, TypeError):
        inspection_date = None
    return {
        "initialResult": result,
        "initialInspectionDate": inspection_date,
        "statisticsYear": int(inspection_date[:4]) if inspection_date else None,
        "reinspectionPending": "PENDING" in outcomes,
    }


def snapshot_qualification(snapshot: dict[str, Any]) -> dict[str, Any]:
    """CaseImportState stores business values with their provenance, not flat fields."""
    initial = next(
        (item for item in snapshot.get("inspections", []) if item.get("stage") == "INITIAL_CHECK"),
        {},
    )

    def values(entity: dict[str, Any]) -> dict[str, Any]:
        return {
            key: field.get("value")
            for key, field in (entity.get("fields") or {}).items()
            if isinstance(field, dict)
        }

    return qualification(
        {
            "initialInspection": {
                **values(initial),
                "products": [values(product) for product in initial.get("products", [])],
            }
        }
    )


def source_qualification(fields: dict[str, Any], stages: list[str]) -> dict[str, Any] | None:
    if isinstance(fields.get("initialInspection"), dict):
        return qualification(fields)
    products = fields.get("检查产品信息")
    certificate = r"(?:认证证书编号[：:])?Z?[0-9]{16}"
    # The displayed stage, method, result icon and quality text must agree.
    # SFHG alone is not a result: observed initial/recheck rows use it differently.
    if isinstance(products, list):
        onsite_failures = [
            p
            for p in products
            if isinstance(p, dict)
            and isinstance(p.get("页面记录"), dict)
            and p["页面记录"].get("SFCPFC") == "0"
            and p.get("检查结果") in ("", "不合格")
            and p.get("检查结果标记") == ["el-icon-error"]
            and any(
                re.fullmatch(
                    r"(?:[1-9][0-9]*项)?不合格"
                    + (
                        rf"(?:\({certificate}\)|（{certificate}）)?"
                        if key == "市场准入检查情况" else ""
                    ),
                    str(p.get(key, "")),
                )
                for key in ("产品质量现场检查情况", "市场准入检查情况")
            )
        ]
        if onsite_failures:
            dates = {
                p["页面记录"].get("JCRQ", "")[:10]
                for p in onsite_failures
                if isinstance(p["页面记录"].get("JCRQ"), str)
            }
            return qualification(
                {
                    "initialInspection": {
                        "inspectionDate": next(iter(dates)) if len(dates) == 1 else None,
                        "products": [{"method": "ONSITE", "result": "UNQUALIFIED"}],
                    }
                }
            )
    # The source UI's exact initial-only, closed onsite checks are structured
    # evidence. Tags or a document title containing "合格" alone are not.
    if stages != ["INITIAL"] or fields.get("检查情况") != "已结案[合格]":
        return None
    if (
        not isinstance(products, list)
        or not products
        or not all(
            isinstance(p, dict)
            and p.get("产品质量现场检查情况") == "未发现不合格现象"
            and p.get("市场准入检查情况") == "未发现不合格现象"
            and p.get("检查结果") in (None, "", "合格")
            for p in products
        )
    ):
        return None
    return qualification(
        {
            "initialInspection": {
                "inspectionDate": fields.get("初查日期") or fields.get("实际检查日期"),
                "products": [{"method": "ONSITE", "result": "QUALIFIED"} for _ in products],
            }
        }
    )


def classify_saved_details(layout: Any, *, apply: bool = False) -> list[dict[str, Any]]:
    changes = []
    for project, record in ws.load_waterline(layout)["cases"].items():
        if record.get("source", {}).get("projectIdentitySource") != "DETAIL":
            continue
        _, manifest, _, _ = case_inputs(layout, project, record)
        if manifest or record.get("source", {}).get("changePending"):
            continue
        path = layout.pending_case_dir(project) / "source-evidence.json"
        evidence = _read(path, layout.root)
        if evidence.get("projectNo") != project:
            continue
        candidates = [
            source_qualification(item.get("fields", {}), item.get("projectInspectionStages", []))
            for item in evidence.get("records", {}).values()
            if item.get("projectNo") == project
        ]
        if (
            not candidates
            or any(c is None or c["initialResult"] == "UNKNOWN" for c in candidates)
            or any(c != candidates[0] for c in candidates)
        ):
            continue
        classification = {
            **candidates[0],
            "evidencePath": str(path.relative_to(layout.root)),
            "evidenceSha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "observedAt": evidence.get("updatedAt"),
        }
        if record.get("source", {}).get("classification") == classification:
            continue
        if apply:
            ws.upsert_case(
                layout,
                project,
                source={
                    "classification": classification,
                    "indexOnly": classification["initialResult"] == "QUALIFIED",
                },
            )
        changes.append(
            {
                "projectNo": project,
                "initialResult": classification["initialResult"],
                "statisticsYear": classification["statisticsYear"],
                "applied": apply,
            }
        )
    return changes


def _read(path: Path, root: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return ws._read_progress_json(path, root, "总账引用证据")


def case_inputs(layout: Any, project: str, record: dict[str, Any]) -> tuple[Path, dict, dict, list]:
    """Only follow the exact active directory or the ledger's explicit archived generation."""
    work = layout.work_case_dir(project)
    issues: list[str] = []
    if not work.is_dir():
        archive = record.get("archive") or {}
        candidate = archive.get("workspacePath")
        if candidate:
            try:
                candidate = layout.resolve_recorded_path(candidate)
            except ws.WorkspaceStateError:
                return work, {}, {}, ["ARCHIVE_REFERENCE_INVALID"]
            suffix = candidate.name.removeprefix(project + "-")
            if (
                candidate.parent != layout.root / "工作区" / "历史工作区"
                or not candidate.name.startswith(project + "-")
                or not ws.ARCHIVE_GENERATION.fullmatch(suffix)
            ):
                return work, {}, {}, ["ARCHIVE_REFERENCE_INVALID"]
            work = candidate
    try:
        manifest = _read(work / "manifest.json", layout.root)
        state = _read(work / "upload-state.json", layout.root)
        if manifest and manifest.get("case", {}).get("projectNo") != project:
            raise ws.WorkspaceStateError("清单项目身份不符")
        if state and (state.get("stateVersion") != 6 or state.get("projectNo") != project):
            raise ws.WorkspaceStateError("上传断点项目身份不符")
        if (
            manifest
            and state
            and state.get("manifestSha256")
            != ("sha256:" + hashlib.sha256((work / "manifest.json").read_bytes()).hexdigest())
        ):
            raise ws.WorkspaceStateError("清单与断点摘要不符")
        return work, manifest, state, issues
    except (ws.WorkspaceStateError, OSError, ValueError):
        return work, {}, {}, ["LOCAL_EVIDENCE_UNTRUSTED"]


def _content_verified(work: Path, layout: Any, manifest: dict, state: dict) -> bool:
    proof = _read(work / "content-verification.json", layout.root)
    if (
        not proof
        or proof.get("schemaVersion") != "ContentVerificationV1"
        or not proof.get("completedAt")
    ):
        return False
    if (
        proof.get("projectNo") != manifest.get("case", {}).get("projectNo")
        or proof.get("manifestSha256") != state.get("manifestSha256")
        or proof.get("caseId") != state.get("caseId")
        or proof.get("origin") != state.get("origin")
    ):
        return False
    files = manifest.get("files") or []
    observed = proof.get("files") or {}
    return (
        bool(files)
        and len(observed) == len(files)
        and all(
            isinstance(observed.get(f["clientRef"]), dict)
            and observed[f["clientRef"]].get("sha256") == f.get("sha256")
            and observed[f["clientRef"]].get("verifiedAt")
            and observed[f["clientRef"]].get("fileId")
            and observed[f["clientRef"]].get("verifiedAgainstManifestSha256")
            and type(observed[f["clientRef"]].get("contentGeneration")) is int
            and observed[f["clientRef"]]["contentGeneration"] >= 1
            for f in files
        )
    )


def _content_evidence(
    work: Path, layout: Any, manifest: dict, state: dict, record: dict
) -> tuple[Path, dict, dict]:
    """Allow a pinned attachment-only verification projection, preserving the import."""
    bundle = record.get("contentVerificationBundle")
    if not bundle:
        return work, manifest, state
    if (
        not isinstance(bundle, dict)
        or bundle.get("schemaVersion") != "CaseContentVerificationBundleV1"
        or state.get("stateVersion") != 6
        or state.get("status") != "VERIFIED"
        or bundle.get("baseManifestSha256") != state.get("manifestSha256")
        or bundle.get("projectNo") != manifest.get("case", {}).get("projectNo")
        or bundle.get("caseId") != state.get("caseId")
        or bundle.get("origin") != state.get("origin")
    ):
        raise ws.WorkspaceStateError("追加附件核验凭证与原案绑定不符")
    path = layout.resolve_recorded_path(bundle["manifestPath"])
    proof_path = layout.resolve_recorded_path(bundle["contentReceiptPath"])
    if (
        path.name != "manifest.json"
        or path.parent == work
        or not path.is_relative_to(work)
        or proof_path != path.with_name("content-verification.json")
    ):
        raise ws.WorkspaceStateError("追加附件核验凭证必须位于原案子目录")
    expanded = _read(path, layout.root)
    proof = _read(proof_path, layout.root)
    for artifact, field in (
        (path, "manifestSha256"),
        (proof_path, "contentReceiptSha256"),
    ):
        if "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest() != bundle.get(field):
            raise ws.WorkspaceStateError("追加附件核验凭证摘要不符")
    exclude = {"files", "otherAttachments"}
    if {k: v for k, v in expanded.items() if k not in exclude} != {
        k: v for k, v in manifest.items() if k not in exclude
    }:
        raise ws.WorkspaceStateError("追加附件不能改变原案字段、检查或文书槽位")
    original_files = {f["clientRef"]: f for f in manifest["files"]}
    expanded_files = {f["clientRef"]: f for f in expanded["files"]}
    added = set(expanded_files) - set(original_files)
    if (
        not added
        or len(expanded_files) != len(expanded["files"])
        or any(expanded_files.get(ref) != item for ref, item in original_files.items())
    ):
        raise ws.WorkspaceStateError("追加核验清单必须完整保留原文件且只增加附件")
    original_attachments = manifest.get("otherAttachments", [])
    expanded_attachments = expanded.get("otherAttachments", [])
    additional = expanded_attachments[len(original_attachments):]
    if (
        expanded_attachments[:len(original_attachments)] != original_attachments
        or len(additional) != len(added)
        or {a.get("fileRef") for a in additional} != added
        or any(a.get("slotCode") != "OTHER_ATTACHMENT" for a in additional)
        or any(not isinstance(a.get("title"), str) or not a["title"].strip() for a in additional)
    ):
        raise ws.WorkspaceStateError("追加文件缺少唯一其他附件归属")
    observed = record.get("systemObservation") or {}
    if (
        observed.get("exists") is not True
        or observed.get("caseId") != state.get("caseId")
        or observed.get("origin") != state.get("origin")
        or not isinstance(observed.get("fileSnapshot"), dict)
    ):
        raise ws.WorkspaceStateError("追加核验凭证缺少当前服务器文件观察")
    verified_files = proof.get("files") or {}
    identities = [item.get("fileId") for item in verified_files.values()]
    if len(identities) != len(set(identities)):
        raise ws.WorkspaceStateError("追加核验凭证文件身份不唯一")
    return path.parent, expanded, {**state, "manifestSha256": bundle["manifestSha256"]}


def _local_completion_valid(
    layout: Any,
    project: str,
    record: dict[str, Any],
    work: Path,
    manifest: dict[str, Any],
    archive: dict[str, Any],
    issues: list[str],
) -> bool:
    """A local completion counts only while receipt, proof and manifest still bind each other."""
    completion = record.get("completion") or {}
    if (
        completion.get("mode") != "LOCAL_V1"
        or completion.get("status") != "LOCAL_VERIFIED"
        or record.get("state") != "COMPLETED"
        or not archive.get("verificationRecord")
        or not manifest
    ):
        return False
    reasons = local_validity.validate_local_completion(layout, project, record)
    if reasons:
        issues.append(local_validity.UNTRUSTED_ISSUE)
        issues.extend(f"本地完成需复核：{reason}" for reason in reasons)
        return False
    return True


def source_facts_layer(work: Path) -> dict[str, Any]:
    """Read-only view of the stored source layer; never writes, never infers a station value."""
    path = work / source_facts.STORE_FILE
    if not path.is_file():
        return {"stored": False, "observationCount": 0, "stationActivity": source_facts.station_activity(None)}
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        observations = store["sourceFacts"]["observations"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"stored": False, "error": "SOURCE_FACTS_UNREADABLE", "observationCount": 0,
                "stationActivity": source_facts.station_activity(None)}
    return {
        "stored": True,
        "observationCount": len(observations),
        "stationActivity": source_facts.station_activity(store),
    }


def source_intake_brigade(fields: Any) -> str | None:
    if __package__:
        from . import source_intake
    else:
        import source_intake
    return source_intake.detail_brigade(fields)


def source_intake_manual_baseline(layout: Any, project: str, source: dict[str, Any]) -> dict[str, Any] | None:
    if __package__:
        from . import source_intake
    else:
        import source_intake
    return source_intake.manual_intake_baseline(layout, project, source)


def _bound_detail_brigade(layout: Any, entry: dict[str, Any]) -> str | None:
    """Fire jurisdiction of the bound detail the follow-up was registered against (None when it cannot be read)."""

    if __package__:
        from . import source_intake
    else:
        import source_intake
    try:
        _, state = source_intake._load_capture(layout, str(entry["batchId"]))
        record = (state.get("records") or {})[str(entry["rwid"])]
        return source_intake.detail_brigade((record.get("collectionDetail") or {}).get("fields"))
    except (KeyError, TypeError, ValueError, OSError, source_intake.SourceIntakeError):
        return None


def _followup_observations(
    layout: Any, project: str, source: dict[str, Any], baseline_brigades: list[str | None] | None = None
) -> tuple[list[tuple[datetime, dict[str, Any], str | None, str]], list[str]]:
    """Trusted follow-up observations of a package-bound case (``source_intake.add_followup_observation``), newest first.

    A follow-up is consumed only when the ledger registered it AND its immutable evidence file still matches: the file's
    digest equals the ledger entry, it is the same case/RWID/observation id/time/fingerprint, its fields recompute to the
    stored digests, it shows this case's product root, and every saved detail screenshot is still on disk with its digest.
    Its fire jurisdiction (the case's fourth identity) must also be shown and equal to the jurisdiction already established
    for the case: the bound detail in its batch, the still-trusted classification evidence and the ledger. Anything else
    is reported by reason and ignored (never promoted to a fact).
    """

    entries = source.get("followupObservations")
    if not isinstance(entries, dict) or not entries:
        return [], []
    if __package__:
        from . import source_intake
    else:
        import source_intake
    known = _known_rwids(source)
    trusted: list[tuple[datetime, dict[str, Any], str | None, str]] = []
    refused: list[str] = []
    for observation_id, entry in entries.items():
        def refuse(reason: str, observation_id: str = observation_id) -> None:
            refused.append(f"{observation_id}:{reason}")

        try:
            path = layout.root / str(entry["evidencePath"]).replace("\\", "/")  # 台账可能存 Windows 分隔符，Linux 读 D 根时同样要能解析
            evidence = _read(path, layout.root)
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, KeyError, ValueError, ws.WorkspaceStateError):
            refuse("EVIDENCE_UNREADABLE")
            continue
        fields = evidence.get("fields")
        observed = _instant(entry.get("observedAt"))
        if digest != entry.get("evidenceSha256"):
            refuse("EVIDENCE_DIGEST_MISMATCH")
        elif evidence.get("schemaVersion") != source_intake.FOLLOWUP_OBSERVATION_VERSION or evidence.get("observationId") != observation_id:
            refuse("EVIDENCE_IDENTITY_MISMATCH")
        elif evidence.get("projectNo") != project or str(evidence.get("rwid")) not in known or str(entry.get("rwid")) != str(evidence.get("rwid")):
            refuse("CASE_OR_RWID_NOT_BOUND")
        elif not isinstance(fields, dict) or str(fields.get("项目编号") or "").strip().upper() != project:
            refuse("PROJECT_MISMATCH")
        elif source_intake._fingerprint(fields) != evidence.get("fingerprint") or source_intake._business_detail_fingerprint(fields) != evidence.get("businessFingerprint"):
            refuse("DIGEST_MISMATCH")
        elif observed is None or entry.get("observedAt") != evidence.get("observedAt") or _instant(evidence.get("observedAtShanghai")) != observed:
            refuse("OBSERVED_AT_UNREGISTERED")
        elif _product_root_problem(fields, project):
            refuse("PRODUCT_ROOT")
        elif (
            source.get("packageSha256")
            and (evidence.get("priorBoundDetail") or {}).get("packageSha256")
            and str(source["packageSha256"]).removeprefix("sha256:") != str(evidence["priorBoundDetail"]["packageSha256"]).removeprefix("sha256:")
        ):
            refuse("PACKAGE_MISMATCH")
        elif (integrity_problem := source_intake.followup_integrity_problem(fields, project)):
            refuse(integrity_problem)
        elif (brigade_problem := source_intake.followup_brigade_problem(
            fields, [*(baseline_brigades or []), _bound_detail_brigade(layout, entry)]
        )):
            refuse(brigade_problem)
        else:
            shots = evidence.get("screenshots") or []
            try:
                shots_ok = bool(shots) and all(
                    (layout.root / str(item["relativePath"]).replace("\\", "/")).is_file()
                    and "sha256:" + hashlib.sha256((layout.root / str(item["relativePath"]).replace("\\", "/")).read_bytes()).hexdigest() == item.get("sha256")
                    for item in shots
                )
            except (OSError, KeyError):
                shots_ok = False
            coverage = evidence.get("coverage")
            coverage_ok = True
            if coverage is not None:
                # 带覆盖索引的观察：索引副本须仍在且摘要不变，且其分段恰是已保存的全部截图
                try:
                    index_ref = coverage["index"]
                    index_file = layout.root / str(index_ref["relativePath"]).replace("\\", "/")
                    coverage_ok = (
                        index_file.is_file()
                        and "sha256:" + hashlib.sha256(index_file.read_bytes()).hexdigest() == index_ref.get("sha256")
                        and index_ref.get("sha256") == coverage.get("indexSha256")
                        and sorted(item["sha256"] for item in coverage["segments"]) == sorted(item.get("sha256") for item in shots)
                    )
                except (OSError, KeyError, TypeError):
                    coverage_ok = False
            if not shots_ok:
                refuse("SCREENSHOT_MISSING_OR_CHANGED")
            elif not coverage_ok:
                refuse("COVERAGE_INDEX_MISSING_OR_CHANGED")
            else:
                trusted.append((observed, fields, observation_day(evidence["observedAt"]), str(evidence["observedAt"])))
    trusted.sort(key=lambda item: item[0], reverse=True)
    return trusted, refused


def describe(layout: Any, project: str, record: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    work, manifest, state, issues = case_inputs(layout, project, record)
    source = record.get("source") or {}
    archive = record.get("archive") or {}
    summary = state.get("finalizeSummary") or {}
    registered = bool(
        state.get("caseId")
        and state.get("status") in {"FINALIZED_UNVERIFIED", "FINALIZED_WITH_CONFLICTS", "VERIFIED"}
        and summary.get("created") is True
    )
    binding = record.get("systemObservation") or {}
    if binding.get("observedAt") and state.get("finalizedAt"):
        try:
            if datetime.fromisoformat(binding["observedAt"]) < datetime.fromisoformat(
                state["finalizedAt"]
            ):
                # An earlier absent/old snapshot cannot negate a later server finalize receipt.
                binding = {}
        except (ValueError, TypeError):
            pass  # Unknown observation ordering remains conservative.
    registered = registered or bool(
        state.get("caseId")
        and state.get("status") == "VERIFIED"
        and state.get("verification", {}).get("caseId") == state["caseId"]
    )
    registered = registered or bool(binding.get("exists") is True and binding.get("caseId"))
    if binding.get("exists") is False and binding.get("observedAt"):
        registered = False
    try:
        verification_binding = state
        # Existing formal cases can have body proof without a local upload job.
        # Use observed server identity; never manufacture a replacement V6 state.
        if (
            not state
            and manifest
            and registered
            and binding.get("snapshotDigest")
            and binding.get("fileSnapshot")
        ):
            verification_binding = {
                "caseId": binding["caseId"],
                "origin": binding.get("origin"),
                "manifestSha256": "sha256:"
                + hashlib.sha256((work / "manifest.json").read_bytes()).hexdigest(),
            }
        verification_work, verification_manifest, verification_binding = _content_evidence(
            work, layout, manifest, verification_binding, record
        )
        body_verified = (
            _content_verified(
                verification_work, layout, verification_manifest, verification_binding
            ) if manifest else False
        )
        proof = _read(verification_work / "content-verification.json", layout.root)
    except (ws.WorkspaceStateError, OSError, KeyError, TypeError, AttributeError):
        body_verified = False
        issues.append("CONTENT_RECEIPT_UNTRUSTED")
    system_changed = bool(state.get("caseId") and binding.get("exists") is False)
    if system_changed:
        body_verified = False
    if body_verified and isinstance(binding.get("fileSnapshot"), dict):
        observed = binding["fileSnapshot"]
        verified = {item["fileId"]: item for item in proof["files"].values()}
        body_current = set(observed) == set(verified) and all(
            item.get("sha256") == verified[file_id].get("sha256")
            and item.get("contentGeneration") == verified[file_id].get("contentGeneration")
            and str(item.get("sizeBytes")) == str(verified[file_id].get("sizeBytes"))
            for file_id, item in observed.items()
        )
        if not body_current:
            body_verified = False
            system_changed = True
            issues.append("SYSTEM_CONTENT_CHANGED_SINCE_VERIFICATION")
    archived = bool(
        archive.get("workspacePath")
        and archive.get("verificationRecord")
        and work.is_dir()
        and (state.get("status") == "VERIFIED" or (not state and body_verified))
    )
    if archived:
        try:
            receipt = _read(
                layout.resolve_recorded_path(archive["verificationRecord"]), layout.root
            )
            receipt_binding = receipt.get("verification") or {}
            if state:
                archive_binding_valid = receipt.get("manifestSha256") == state.get("manifestSha256")
            else:
                archive_binding_valid = bool(
                    body_verified
                    and receipt.get("manifestSha256") == verification_binding.get("manifestSha256")
                    and receipt_binding.get("mode") == "EXISTING_FORMAL_CASE"
                    and receipt_binding.get("status") == "VERIFIED"
                    and receipt_binding.get("caseId") == binding.get("caseId")
                    and receipt_binding.get("origin") == binding.get("origin")
                    and receipt_binding.get("contentReceiptSha256")
                    == (
                        "sha256:"
                        + hashlib.sha256(
                            (work / "content-verification.json").read_bytes()
                        ).hexdigest()
                    )
                )
            archived = bool(
                receipt.get("recordVersion") == 1
                and receipt.get("projectNo") == project
                and archive_binding_valid
                and bool(receipt.get("archivedWorkspace"))
                and layout.resolve_recorded_path(receipt["archivedWorkspace"]) == work
            )
        except (OSError, ws.WorkspaceStateError):
            archived = False
            issues.append("ARCHIVE_RECEIPT_UNTRUSTED")
    local_complete = _local_completion_valid(
        layout, project, record, work, manifest, archive, issues
    )
    archived = archived or local_complete
    # Historical completion is retained, but never upgraded to deep-content proof.
    historical_complete = record.get("state") == "COMPLETED" and archived and not local_complete
    projection = state.get("filesProjection") or []
    projected = {f.get("clientRef") for f in projection if isinstance(f, dict)} - {None}
    received = set(state.get("uploadedFileRefs") or []) & projected
    material_gaps = source.get("materialGaps") or []
    if __package__:
        from .manual_intake import gaps_acknowledged
    else:
        from manual_intake import gaps_acknowledged
    retained_source_issues = gaps_acknowledged(source)
    if material_gaps:
        issues.append("SOURCE_MATERIALS_INCOMPLETE")
    delivery = source.get("downloadDelivery") or {}
    # 下载到包即“已收到”，独立于接件通过(PACKAGE_READY)、材料齐全与文书/云同步；
    # 拒收（INVALID/guard）仍记已收到但不等于已采纳。
    package_received = bool(
        delivery.get("received")
        or source.get("status") in {"PACKAGE_READY", "PACKAGE_RECEIVED", "PACKAGE_READY_TO_ATTACH"}
        or (delivery.get("status") in {"READY", "INVALID"} and delivery.get("zipCandidates"))
        or manifest.get("packageSha256")
    )
    stages = {
        "sourceRegistered": bool(
            source.get("projectIdentitySource") in {"DETAIL", "MANUAL_DETAIL"} or source.get("rwid")
        ),
        "packageReceived": package_received,
        "materialsCollected": not material_gaps
        and (
            source.get("status") in {"PACKAGE_READY", "PACKAGE_RECEIVED"}
            or bool(manifest.get("packageSha256") and (state or local_complete))
        ),
        "systemRegistered": registered,
        "bodyVerified": body_verified,
        "localVerified": local_complete,
        "archived": archived,
    }
    verified_chain = (registered and body_verified) or local_complete
    classification = qualification(manifest)
    if (
        binding.get("exists")
        and binding.get("snapshotDigest")
        and isinstance(binding.get("qualification"), dict)
    ):
        classification = {k: binding["qualification"].get(k) for k in classification}
    source_summary = None
    lifecycle_evidence: tuple[dict[str, Any], str | None, str | None] | None = None
    if source.get("classification"):
        candidate = source["classification"]
        try:
            evidence_path = layout.root / candidate["evidencePath"]
            evidence = _read(evidence_path, layout.root)
            if evidence.get("projectNo") == project and candidate["evidenceSha256"] == (
                "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            ):
                source_summary = source_product_summary(evidence, project)
                lifecycle_evidence = _evidence_fields(evidence, project)
                # A local record that is still sampling/pending, or has no final product result,
                # must not hide an independently valid source conclusion bound to its evidence
                # digest. The local record's own date and reinspection state are kept.
                if classification["initialResult"] == "UNKNOWN":
                    merged = {k: candidate.get(k) for k in classification}
                    if classification["initialInspectionDate"]:
                        merged["initialInspectionDate"] = classification["initialInspectionDate"]
                        merged["statisticsYear"] = classification["statisticsYear"]
                    merged["reinspectionPending"] = bool(
                        classification["reinspectionPending"] or merged["reinspectionPending"]
                    )
                    classification = merged
        except (OSError, KeyError, ws.WorkspaceStateError):
            issues.append("CLASSIFICATION_EVIDENCE_UNTRUSTED")
    if source.get("changePending") and source.get("pendingClassification"):
        candidate = source["pendingClassification"]
        try:
            evidence_path = layout.root / candidate["evidencePath"]
            evidence = _read(evidence_path, layout.root)
            if evidence.get("projectNo") != project or candidate["evidenceSha256"] != (
                "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            ):
                raise ws.WorkspaceStateError("来源变化分类证据不一致")
            classification = {k: candidate.get(k) for k in classification}
        except (OSError, KeyError, ws.WorkspaceStateError):
            issues.append("PENDING_CLASSIFICATION_EVIDENCE_UNTRUSTED")
    source_documents = {
        (rwid, fingerprint)
        for observations in (
            source.get("observationsByRwid"),
            source.get("pendingObservationsByRwid"),
        )
        for rwid, obs in (observations or {}).items()
        for fingerprint in obs.get("documentFingerprints", [])
    }
    lifecycle_classification = {**classification, "projectInspectionStages": source.get("projectInspectionStages"),
                                "sourceInspectionStages": source.get("sourceInspectionStages")}
    refused_details: list[str] = []
    if lifecycle_evidence is None:
        lifecycle_evidence, refused_details = _saved_detail(layout, project, source)
        if lifecycle_evidence and lifecycle_classification["initialResult"] == "UNKNOWN":
            derived = source_qualification(
                lifecycle_evidence[0], list(source.get("projectInspectionStages") or source.get("sourceInspectionStages") or [])
            )
            if derived:
                lifecycle_classification.update({k: derived[k] for k in ("initialResult", "initialInspectionDate", "reinspectionPending")})
    # A newer, trusted same-case observation saved next to the bound material beats the older evidence the ledger's
    # classification still points to (which stays valid and untouched); it never replaces or re-verifies that material.
    base_brigade = [source_intake_brigade(lifecycle_evidence[0]) if lifecycle_evidence else None]
    if base_brigade == [None]:
        base_brigade = [record.get("brigadeName")]
    if base_brigade == [None]:
        manual = source_intake_manual_baseline(layout, project, source)
        if manual is not None:
            base_brigade = [manual["brigade"]]
    followups, refused_followups = _followup_observations(layout, project, source, base_brigade)
    issues.extend(f"FOLLOWUP_OBSERVATION_UNTRUSTED:{reason}" for reason in refused_followups)
    if followups:
        base_instant = _instant(lifecycle_evidence[2]) if lifecycle_evidence else None
        newest = followups[0]
        if base_instant is None or newest[0] > base_instant:
            lifecycle_evidence = (newest[1], newest[2], newest[3])
            derived = source_qualification(
                newest[1], list(source.get("projectInspectionStages") or source.get("sourceInspectionStages") or [])
            )
            if derived:
                lifecycle_classification.update({k: derived[k] for k in ("initialResult", "initialInspectionDate", "reinspectionPending")})
    lifecycle_manifest = manifest or _archived_manifest(layout, project, record)
    # Reviewed recheck->initial product links stored next to the archived generation (never rewrite the archive).
    lifecycle_manifest = product_links.apply(layout, project, record, lifecycle_manifest)
    lifecycle = case_lifecycle.assess_case(
        project=project,
        evidence_fields=lifecycle_evidence[0] if lifecycle_evidence else None,
        manifest=lifecycle_manifest or None,
        ledger_identity={
            "unitName": record.get("unitName"),
            "unitAddress": source.get("address"),
            "brigade": record.get("brigadeCode"),
            "unitCategory": _identity_unit_category(layout, project, source, lifecycle_evidence, lifecycle_manifest),
        },
        classification=lifecycle_classification,
        observed_on=(lifecycle_evidence[1] if lifecycle_evidence else None),
        has_package=package_received,
        new_evidence=bool(source.get("changePending")),
        today=today,
        refused_evidence=None if lifecycle_evidence else refused_details,
    )
    return {
        "projectNo": project,
        "unitName": record.get("unitName"),
        **classification,
        "lifecycle": lifecycle,
        "sourceProduct": source_summary,
        "recordInitialFact": record_initial_fact(manifest),
        "stages": stages,
        "complete": bool(
            stages["sourceRegistered"]
            and stages["materialsCollected"]
            and stages["archived"]
            and verified_chain
            and not source.get("changePending")
            and not system_changed
        ),
        "availableMaterialWorkflowComplete": bool(archived and verified_chain
            and not source.get("changePending") and not system_changed),
        "completionMode": "LOCAL_V1"
        if local_complete
        else "LEGACY_V2_NAS"
        if historical_complete
        else None,
        "sourceIssuesRetained": retained_source_issues,
        "historicalComplete": historical_complete,
        "activePending": system_changed
        or bool(material_gaps and not (retained_source_issues and archived))
        or bool(source.get("changePending"))
        or (
            not historical_complete
            and not local_complete
            and not (
                source.get("indexOnly") is True and classification["initialResult"] == "QUALIFIED"
            )
        ),
        "evidenceLevel": "LOCAL_VERIFIED"
        if local_complete
        else "DEEP_CONTENT"
        if body_verified
        else "LEGACY_UNSPECIFIED"
        if historical_complete
        else "PENDING",
        "sourceChanged": bool(source.get("changePending")),
        "systemChanged": system_changed,
        "sourceObservedAt": source.get("lastObservedAt") or source.get("capturedAt"),
        "sourceDocumentDate": source.get("latestDocumentCreatedAt"),
        "sourceDocumentCount": len(source_documents),
        "workflowUpdatedAt": record.get("workflowUpdatedAt"),
        "files": {"expected": len(projected), "received": len(received)}
        if state
        else {
            "expected": len(manifest.get("files") or []),
            "received": len(binding.get("fileSnapshot") or {}) if registered else 0,
        },
        "waiting": record.get("nasVerification", {}).get("status"),
        "issues": issues,
        "sourceFacts": source_facts_layer(work),
    }


def ledger_view(
    layout: Any, *, view: str = "all", batch_id: str | None = None, source_year: int | None = None
) -> dict[str, Any]:
    ledger = ws.load_waterline(layout)
    records = ledger["cases"]
    if source_year is not None and batch_id:
        raise ws.WorkspaceStateError("来源年度和指定批次必须分别查询")
    annual = coverage_api.annual_coverage(layout, source_year, ledger=ledger)
    if source_year is not None:
        records = {p: r for p, r in records.items() if p in annual["projectNos"]}
    if batch_id:
        # Validate the batch exists and is formal; its RWIDs are not project identities.
        ws._formal_capture_batch(layout, batch_id)
        records = {
            p: r
            for p, r in records.items()
            if r.get("source", {}).get("batchId") == batch_id
            or batch_id in r.get("source", {}).get("batchIds", [])
        }
    rows = [describe(layout, p, r) for p, r in sorted(records.items())]
    for row in rows:
        row["unqualifiedCandidate"] = row["projectNo"] in annual["candidateProjectNos"]
        row["sourceAnnualYear"] = annual["sourceYear"]
        row["sourceAnnualMember"] = row["projectNo"] in annual["projectNos"]
    known = len(rows)
    if view == "unqualified":
        rows = [r for r in rows if r["initialResult"] == "UNQUALIFIED"]
    elif view == "unknown":
        rows = [r for r in rows if r["initialResult"] == "UNKNOWN"]
    elif view == "unfinished":
        rows = [r for r in rows if not r["complete"]]
    elif view != "all":
        raise ws.WorkspaceStateError("总账视图无效")
    stage_names = (
        "sourceRegistered",
        "materialsCollected",
        "systemRegistered",
        "bodyVerified",
        "localVerified",
        "archived",
    )
    return {
        "schemaVersion": "CaseLedgerViewV1",
        "scope": "SOURCE_YEAR" if source_year is not None else "KNOWN_CASES",
        "sourceYear": source_year,
        "view": view,
        "batchId": batch_id,
        "generatedAt": ws._utc_now(),
        "identityKey": "projectNo",
        "coverage": {
            "knownCases": known,
            "historicalSourceComplete": False,
            "note": "仅已知案卷索引；未证明来源系统全部历史覆盖",
        },
        "sourceCoverage": coverage_api.summary(annual),
        "counts": {
            "cases": len(rows),
            **{key: sum(r["stages"][key] for r in rows) for key in stage_names},
            "knownSourceDocuments": sum(r["sourceDocumentCount"] for r in rows),
            "complete": sum(r["complete"] for r in rows),
            "historicalComplete": sum(r["historicalComplete"] for r in rows),
            "activePending": sum(r["activePending"] for r in rows),
            "legacyEvidenceIncomplete": sum(
                r["historicalComplete"] and not r["stages"]["bodyVerified"] for r in rows
            ),
            "expectedFiles": sum(r["files"]["expected"] for r in rows),
            "receivedFiles": sum(r["files"]["received"] for r in rows),
        },
        "qualification": dict(Counter(r["initialResult"] for r in rows)),
        "cases": rows,
    }


_PRODUCT_CASE = re.compile(r"^\d{8}C\d{9}$")


def lifecycle_view(
    layout: Any,
    *,
    from_year: int | None = None,
    from_date: date | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Read-only classification of every known in-scope product case by what it still needs.

    Scope is the real initial inspection date (``initial_scope.SCOPE_FROM`` = 2026-01-01, later years included); the
    project-number year decides nothing (``from_year`` is accepted only for old callers and is ignored). A case whose
    initial date is proven earlier is listed under ``outOfScope`` and not classified; an unknown or boundary-fallback
    date keeps the case in the view and is reported under ``scopeUnproven`` so it is verified, not guessed away.

    REUSE_DIRECT: stable (identity complete, initial result final, recheck branch terminal, no change evidence)
    or waiting for a known due day. LOCAL_SUPPLEMENT: the gap can be closed from materials already held locally.
    NEEDS_SOURCE: a concrete cause justifies a source revisit. Counts are processed/undecided facts of this
    ledger, not a statistic of the whole source year.
    """

    scope_from = from_date or initial_scope.SCOPE_FROM
    ledger = ws.load_waterline(layout)
    rows = []
    out_of_scope = []
    scope_unproven = []
    for project, record in sorted(ledger["cases"].items()):
        if not _PRODUCT_CASE.match(project):
            continue
        row = describe(layout, project, record, today=today)
        life = row["lifecycle"]
        initial = life["required"].get("initialDate") or {}
        verdict = initial_scope.date_basis_proves_scope(
            initial.get("value") if initial.get("state") == "present" else None,
            fallback=bool(initial.get("fallback")),
            scope_from=scope_from,
        )
        if verdict == "OUT":
            out_of_scope.append({"projectNo": project, "unitName": row["unitName"], "initialDate": initial.get("value")})
            continue
        if verdict == "UNPROVEN":
            scope_unproven.append(
                {
                    "projectNo": project,
                    "unitName": row["unitName"],
                    "initialDate": initial.get("value"),
                    "fallback": bool(initial.get("fallback")),
                }
            )
        rows.append(
            {
                "projectNo": project,
                "unitName": row["unitName"],
                "state": record.get("state"),
                "category": life["category"],
                "initialResult": life["initialResult"],
                "recheckState": life["recheck"]["state"],
                "administrativeClass": life["administrativeClass"],
                "sourceCauses": life["sourceCauses"],
                "localCauses": life["localCauses"],
                "nextCheckOn": life["nextCheckOn"],
                "packageReceived": row["stages"]["packageReceived"],
                "archived": row["stages"]["archived"],
                "missingRequired": sorted(k for k, v in life["required"].items() if v["state"] in {"missing", "conflict"}),
                "evidenceRefused": life.get("evidenceRefused", []),
                "pendingRequired": sorted(k for k, v in life["required"].items() if v["state"] == "pending"),
            }
        )
    causes = Counter(c for r in rows for c in r["sourceCauses"] + r["localCauses"])
    return {
        "schemaVersion": "CaseLifecycleViewV1",
        "generatedAt": ws._utc_now(),
        "scopeFromDate": scope_from.isoformat(),
        "identityKey": "projectNo",
        "note": "仅已知总账里初查日期不早于范围起始日的产品案；项目编号年份不决定范围；总账成员数不是年度分母",
        "counts": {
            "cases": len(rows),
            "byCategory": dict(Counter(r["category"] for r in rows)),
            "byRecheckState": dict(Counter(r["recheckState"] for r in rows)),
            "byAdministrativeClass": dict(Counter(r["administrativeClass"] for r in rows)),
            "byCause": dict(sorted(causes.items())),
            "archived": sum(r["archived"] for r in rows),
            "packageReceived": sum(r["packageReceived"] for r in rows),
            # 复查产品未经 initialProductRef 对应到同一初查产品：这些链保持待核实，可由本地文书核对补引用后再收口(不触发重采)
            "recheckLinkUnproven": sum("RECHECK_PRODUCT_LINK_UNPROVEN" in r["localCauses"] for r in rows),
            "outOfScope": len(out_of_scope),
            "scopeUnproven": len(scope_unproven),
        },
        "outOfScope": out_of_scope,
        "scopeUnproven": scope_unproven,
        "cases": rows,
    }


def render_ledger_report(value: dict[str, Any], *, include_all: bool = False) -> str:
    counts = value["counts"]
    coverage = value.get("sourceCoverage") or {}
    source_total = coverage.get("sourceListCount")
    source_total_label = source_total if source_total is not None else "未核实"
    lines = [
        "# 案卷总水位",
        "",
        "所有视图共用项目编号唯一总账；历史来源覆盖未知。",
        "本报告只读本地证据，不等同于本次重新访问服务器核验。",
        f"来源 {coverage.get('sourceYear', '本')} 年度："
        + ("年度身份已对齐" if coverage.get("complete") else "尚未对齐")
        + f"；年度列表总数 {source_total_label}；"
        + f"已保存来源标识 {coverage.get('sourceIdentityCount', 0)}，"
        + f"待关联 {coverage.get('unresolvedSourceIdentities', 0)}，"
        + f"身份冲突 {coverage.get('conflictingSourceIdentities', 0)}。",
        f"已关联来源标识 {coverage.get('linkedSourceIdentities', 0)}，"
        + f"去重项目 {coverage.get('uniqueProjectCount', 0)}；"
        + f"已知年度文书 {coverage.get('sourceDocumentCount', 0)} 份。"
        + "这些数量分别计数，不用文书或来源标识代替案卷总数。",
        "",
        f"本视图 {counts['cases']} 案；材料已采集 {counts['materialsCollected']}；"
        f"本地核验 {counts['localVerified']}；历史系统登记 {counts['systemRegistered']}；"
        f"历史正文核验 {counts['bodyVerified']}；"
        f"归档 {counts['archived']}；全部完成 {counts['complete']}。",
        f"已知来源文书 {counts['knownSourceDocuments']} 份；"
        f"来源已登记 {counts['sourceRegistered']} 案；"
        f"活动待办 {counts['activePending']} 案。历史完成 {counts['historicalComplete']} 案中，"
        f"{counts['legacyEvidenceIncomplete']} 案缺少新式逐份正文回执，"
        "保留原完成记录，不默认重做。",
        "",
        "|项目编号|初查结果|初查日期|来源登记|材料采集|历史系统登记|历史正文核验|本地核验|归档|当前处理|",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    report_rows = (
        value["cases"]
        if include_all
        else [row for row in value["cases"] if row["activePending"] or row["issues"]]
    )
    if not include_all:
        lines.insert(-2, "以下仅列活动待办或明确异常；历史明细见总账，--all-cases 可显式展开。")
        lines.insert(-2, "")
    for row in report_rows:
        s = row["stages"]
        flags = [
            "是" if s[key] else "未确认"
            for key in (
                "sourceRegistered",
                "materialsCollected",
                "systemRegistered",
                "bodyVerified",
                "localVerified",
                "archived",
            )
        ]
        lines.append(
            "|"
            + "|".join(
                [
                    row["projectNo"],
                    {"UNQUALIFIED": "不合格", "QUALIFIED": "合格", "UNKNOWN": "待确认"}.get(
                        row["initialResult"], "待确认"
                    ),
                    row["initialInspectionDate"] or "未确认",
                    *flags,
                    "有新增文书"
                    if row["sourceChanged"]
                    else "待处理"
                    if row["activePending"]
                    else "历史证据复用"
                    if row["historicalComplete"]
                    else "本地完成"
                    if row["completionMode"] == "LOCAL_V1"
                    else "仅建索引",
                ]
            )
            + "|"
        )
    return "\n".join(lines) + "\n"


def _excel_evidence_note(row: dict[str, Any]) -> str:
    notes = [
        "历史完成，缺少新版正文回执，默认复用"
        if row["evidenceLevel"] == "LEGACY_UNSPECIFIED"
        else "本地核验凭证已核对（不含服务端或飞牛）"
        if row["evidenceLevel"] == "LOCAL_VERIFIED"
        else "逐份正文回执已核对"
        if row["evidenceLevel"] == "DEEP_CONTENT"
        else "待处理"
    ]
    if "SOURCE_MATERIALS_INCOMPLETE" in row["issues"]:
        notes.append("来源大队缺件事实保留，现有材料已收口归档"
                     if row.get("sourceIssuesRetained") and row["stages"]["archived"]
                     else "来源材料缺件，采集未完成，不能归档")
    if row["sourceChanged"]:
        notes.append("来源新增或变化尚未处理")
    if row["systemChanged"]:
        notes.append("系统当前内容与原核验证据不同")
    if any(
        issue not in {"SOURCE_MATERIALS_INCOMPLETE", "SYSTEM_CONTENT_CHANGED_SINCE_VERIFICATION"}
        for issue in row["issues"]
    ):
        notes.append("存在待核对的证据异常")
    return "；".join(notes)


def _basis_columns(row: dict[str, Any]) -> list[str]:
    """Quality, market access and the confirmed-unqualified basis stay in separate columns."""
    summary = row.get("sourceProduct")
    if summary is None:
        basis = "未取得来源产品检查原文" if row["initialResult"] == "UNQUALIFIED" else ""
        return [basis, "", "", row.get("recordInitialFact") or ""]
    basis = _BASIS_LABEL.get(summary["unqualifiedBasis"], "") if row["initialResult"] == "UNQUALIFIED" else ""
    if row["initialResult"] == "UNQUALIFIED" and not basis:
        basis = "来源产品原文未见不合格(依据见证据说明)"
    return [
        basis,
        "；".join(summary["qualityCells"]),
        "；".join(summary["marketAccessCells"]),
        row.get("recordInitialFact") or "",
    ]


def add_ledger_sheets(workbook: Any, layout: Any) -> None:
    """Export four views of one evidence snapshot; the workbook is never a second ledger."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    snapshot = ledger_view(layout)
    all_rows = snapshot["cases"]
    groups = [
        ("所有案卷", all_rows),
        ("不合格案卷", [r for r in all_rows if r["initialResult"] == "UNQUALIFIED"]),
        ("待确认案卷", [r for r in all_rows if r["initialResult"] == "UNKNOWN"]),
        ("未完成案卷", [r for r in all_rows if not r["complete"]]),
    ]
    headers = [
        "项目编号",
        "单位名称",
        "初查结果",
        "初查日期",
        "统计年份",
        "来源已登记",
        "材料已采集",
        "历史系统已登记",
        "历史正文已核验",
        "本地已核验",
        "归档已完成",
        "活动待办",
        "来源有变化",
        "最近来源核对",
        "本地处理时间",
        "证据说明",
        "来源年度清单",
        "文书提示不合格候选",
        "不合格依据",
        "来源产品质量现场检查原文",
        "来源市场准入检查原文",
        "本地检查记录初查事实",
    ]
    stage_names = (
        "sourceRegistered",
        "materialsCollected",
        "systemRegistered",
        "bodyVerified",
        "localVerified",
        "archived",
    )
    for index, (name, rows) in enumerate(groups):
        sheet = workbook.create_sheet(name, index)
        sheet.append(headers)
        for row in rows:
            sheet.append(
                [
                    row["projectNo"],
                    row["unitName"] or "",
                    {"QUALIFIED": "合格", "UNQUALIFIED": "不合格", "UNKNOWN": "待确认"}.get(
                        row["initialResult"], "待确认"
                    ),
                    row["initialInspectionDate"] or "",
                    row["statisticsYear"],
                    *("是" if row["stages"][key] else "未确认" for key in stage_names),
                    "是" if row["activePending"] else "否",
                    "是" if row["sourceChanged"] else "否",
                    row["sourceObservedAt"] or "",
                    row["workflowUpdatedAt"] or "",
                    _excel_evidence_note(row),
                    f"{row['sourceAnnualYear']} 年已核实范围"
                    if row["sourceAnnualMember"]
                    else "未纳入当前年度已核实范围",
                    "是，仅提示需核实结果" if row["unqualifiedCandidate"] else "否",
                    *_basis_columns(row),
                ]
            )
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for cells in sheet.iter_rows():
            for cell in cells:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.font = Font(
                    name="Microsoft YaHei",
                    size=10,
                    bold=cell.row == 1,
                    color="FFFFFF" if cell.row == 1 else "1F1F1F",
                )
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if cell.row == 1 or cell.row % 2 == 0:
                    cell.fill = PatternFill(
                        "solid", fgColor="17365D" if cell.row == 1 else "EAF2F8"
                    )
        # 每个表头列都显式给宽度(曾 18 列只给 17 项，最后一列回落默认窄宽度)；表头高度按最长表头所需行数计算，
        # 避免 LibreOffice 等导出路径中长表头被挤压或相邻表头连排。
        for column, width in enumerate(LEDGER_COLUMN_WIDTHS, 1):
            sheet.column_dimensions[get_column_letter(column)].width = width
        sheet.row_dimensions[1].height = ledger_header_height(headers, LEDGER_COLUMN_WIDTHS)
    workbook.active = 0
    info = workbook["使用说明"]
    info.append(
        ["覆盖范围", "仅已知案卷；不代表来源系统全部历史。四种视图按项目编号共用同一总账。"]
    )
    info.append(["历史证据", "未完成视图包含证据不足的历史完成案；活动待办为否时不默认重新采集。"])
    coverage = snapshot["sourceCoverage"]
    sheet = workbook.create_sheet("年度来源覆盖")
    status = (
        "年度身份已逐项对齐"
        if coverage.get("complete")
        else "有来源入口不可用，尚未全部核实"
        if coverage.get("unavailableTaskCategories")
        else "年度身份尚未全部核实"
    )
    for label, key in [
        ("来源年度", "sourceYear"),
        ("覆盖状态", "status"),
        ("年度来源列表总数", "sourceListCount"),
        ("已核实类别列表条数", "observedSourceListCount"),
        ("已知年度文书数", "sourceDocumentCount"),
        ("来源标识数", "sourceIdentityCount"),
        ("已关联来源标识", "linkedSourceIdentities"),
        ("待关联来源标识", "unresolvedSourceIdentities"),
        ("身份冲突", "conflictingSourceIdentities"),
        ("去重项目数", "uniqueProjectCount"),
        ("已进总账项目数", "indexedProjectCount"),
        ("来源清单核对时间", "sourceObservedAt"),
        ("覆盖说明", "note"),
    ]:
        value = status if key == "status" else coverage.get(key)
        sheet.append([label, value if value is not None else "未核实"])
    sheet.append([])
    sheet.append(["任务类别", "已核实列表条数", "核对状态", "来源核对时间"])
    for category, item in coverage.get("taskCategories", {}).items():
        accepted = item.get("accepted") and item.get("stableRounds", 0) >= 2
        sheet.append(
            [category, item.get("sourceListCount") if accepted else "未核实",
             "两轮稳定" if accepted else "尚未核实", item.get("sourceObservedAt")]
        )
    sheet.append([])
    sheet.append(["全部已知案卷进度", "所有案卷", "其中不合格"])
    unqualified = [r for r in all_rows if r["initialResult"] == "UNQUALIFIED"]
    metrics = [
        ("去重案卷", lambda r: True),
        ("来源已登记", lambda r: r["stages"]["sourceRegistered"]),
        ("材料已采集", lambda r: r["stages"]["materialsCollected"]),
        ("历史系统已登记", lambda r: r["stages"]["systemRegistered"]),
        ("历史正文已核验", lambda r: r["stages"]["bodyVerified"]),
        ("本地已核验", lambda r: r["stages"]["localVerified"]),
        ("归档累计", lambda r: r["stages"]["archived"]),
        ("全部完成（含核验证据）", lambda r: r["complete"]),
        ("活动待办", lambda r: r["activePending"]),
    ]
    for label, include in metrics:
        sheet.append(
            [label, sum(include(r) for r in all_rows), sum(include(r) for r in unqualified)]
        )
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 80
    sheet.column_dimensions["C"].width = 22
    sheet.column_dimensions["D"].width = 30
    for cells in sheet:
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(wrap_text=True, vertical="center")


PACKAGE_WINDOW_MONTHS_DEFAULT = 2
PACKAGE_REFRESH_DAYS_DEFAULT = 7
_OPEN_STATUSES = ("处理中", "已检查")


def rolling_window(months: int, today: date | None = None) -> dict[str, str]:
    """Asia/Shanghai 闭区间：今日减 months 个自然月（同日不存在取该月末），起止均含。"""
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    month_index = today.year * 12 + today.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    start = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    return {"startDate": start.isoformat(), "endDate": today.isoformat()}


def _status_class(text: Any) -> str | None:
    """只把已见证据的取值归类：已结案=CLOSED；处理中/已检查=OPEN；其余一律未知（None）。"""
    value = str(text or "").strip()
    if value.startswith("已结案"):
        return "CLOSED"
    if value.startswith(_OPEN_STATUSES):
        return "OPEN"
    return None


def package_decision(
    facts: dict[str, Any],
    *,
    today: date | None = None,
    months: int = PACKAGE_WINDOW_MONTHS_DEFAULT,
    refresh_days: int = PACKAGE_REFRESH_DAYS_DEFAULT,
) -> dict[str, Any]:
    """纯函数：只读决定“复用/首次获取/周期更新候选/仅显式更新/暂缓”及原因码，不产生任何副作用。

    默认任何已验证有效包都复用；状态/日期未知不妨碍复用；complete 不盖过明确合法的近期更新；
    材料缺失从不是重下理由；已结案与合格案不周期更新。
    """
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    window = rolling_window(months, today)
    has_package = bool(facts.get("hasPackage"))
    result = facts.get("initialResult") or "UNKNOWN"
    status = facts.get("sourceStatus")
    out: dict[str, Any] = {"window": window, "refreshDays": refresh_days, "reasons": [],
                            "localComplete": bool(facts.get("localComplete"))}

    def done(code: str, action: str, *reasons: str) -> dict[str, Any]:
        out.update(code=code, action=action)
        out["reasons"].extend(reasons)
        return out

    if has_package and facts.get("packageIssue") in {"UNKNOWN", "AMBIGUOUS_CHAIN"}:
        return done("PKG_HOLD_PACKAGE_UNKNOWN", "HOLD", facts["packageIssue"])
    if has_package and not facts.get("packageValid", True):
        return done("PKG_BAD_LOCAL", "EXPLICIT_ONLY", facts.get("packageIssue") or "PACKAGE_UNREADABLE")
    if result != "UNQUALIFIED":
        # 用户口径：2026消防产品案卷整包先全部获取(合格/未知也取)，已有有效包复用；不合格案优先靠排序，不靠准入。
        reason = "RESULT_QUALIFIED" if result == "QUALIFIED" else "RESULT_UNKNOWN"
        if has_package:
            return done("PKG_REUSE_EXISTING", "REUSE", reason)
        return done("PKG_ACQUIRE_FIRST", "ACQUIRE_FIRST", reason)
    # 已确认不合格
    if status == "CLOSED":
        return done("PKG_REUSE_CLOSED" if has_package else "PKG_ACQUIRE_FIRST",
                    "REUSE" if has_package else "ACQUIRE_FIRST")
    if not has_package:
        return done("PKG_ACQUIRE_FIRST", "ACQUIRE_FIRST", *(["STATUS_UNKNOWN"] if status is None else []))
    if status != "OPEN":
        return done("PKG_REUSE_EXISTING", "REUSE", "STATUS_UNKNOWN")
    check_date = facts.get("checkDate")
    if check_date is None:
        return done("PKG_REUSE_EXISTING", "REUSE", facts.get("dateIssue") or "DATE_UNKNOWN")
    if check_date > today:
        return done("PKG_REUSE_EXISTING", "REUSE", "DATE_IN_FUTURE")
    if check_date < date.fromisoformat(window["startDate"]):
        return done("PKG_ACTIVE_OLDER_MANUAL", "EXPLICIT_ONLY", "OLDER_THAN_WINDOW")
    life = facts.get("lifecycle")
    if life is not None:  # 2026-11-09：稳定案默认本地复用，只有具体原因才更新；不设统一过期天数
        if life.get("stable"):
            return done("PKG_REUSE_STABLE", "REUSE", "STABLE_NO_CAUSE")
        causes = list(life.get("sourceCauses") or [])
        if not causes:
            if life.get("nextCheckOn"):
                out["nextEligibleAt"] = life["nextCheckOn"]
            return done("PKG_REUSE_EXISTING", "REUSE", "NO_CAUSE_YET")
        out["causes"] = causes
    attached = facts.get("attachedAt")
    last = None
    if attached:
        try:
            last = datetime.fromisoformat(str(attached)).astimezone(ZoneInfo("Asia/Shanghai")).date()
        except ValueError:
            last = None
    if last is not None and (today - last).days < refresh_days:
        out["nextEligibleAt"] = date.fromordinal(last.toordinal() + refresh_days).isoformat()
        return done("PKG_REUSE_EXISTING", "REUSE", "WITHIN_REFRESH_INTERVAL")
    return done("PKG_REFRESH_DUE_RECENT_UNQUALIFIED", "REFRESH_CANDIDATE", *out.get("causes", []))


def _package_capture_index(layout: Any) -> dict[str, dict[str, Any]]:
    """一次扫描正式 capture：每案的源状态观察（已结案单调优先）与已登记包。"""
    index: dict[str, dict[str, Any]] = {}
    for state in coverage_api.formal_captures(layout):
        stamp = str(state.get("updatedAt") or state.get("createdAt") or "")
        for key, record in (state.get("records") or {}).items():
            project = record.get("projectNo") or (record.get("detail") or {}).get("projectNo")
            if not project:
                continue
            entry = index.setdefault(project, {"status": None, "statusAt": "", "packages": {}})
            detail = (record.get("detail") or {}).get("fields") or {}
            klass = _status_class((record.get("fields") or {}).get("taskStatus")) or _status_class(
                detail.get("检查情况")
            )
            if klass == "CLOSED" or (klass and entry["status"] != "CLOSED" and stamp >= entry["statusAt"]):
                entry["status"], entry["statusAt"] = klass, stamp
            package = record.get("package")
            if package and package.get("sha256"):
                entry["packages"][package["sha256"]] = package
                # 捕获RWID随包摘要保留，供归档定位核对水位RWID（同摘要多RWID冲突视为UNKNOWN）
                rwids = entry.setdefault("rwids", {}).setdefault(package["sha256"], set())
                rwids.add(str(key))
    return index


def _package_head(
    layout: Any, project: str, packages: dict[str, dict[str, Any]], rwids: dict[str, set] | None = None
) -> tuple[dict[str, Any] | None, str]:
    """返回（当前代包, 状态）。状态 OK/NO_PACKAGE/AMBIGUOUS_CHAIN/FILE_MISSING/SHA_MISMATCH/UNKNOWN。

    多包仅在合法代链上取链头；其余按无法识别处理。FILE_MISSING 常见于已归档移动的旧包，
    只说明登记路径下已无文件，不是“坏包”结论，也绝不触发自动下载。"""
    if not packages:
        return None, "NO_PACKAGE"
    superseded = {p.get("supersedes") for p in packages.values() if p.get("supersedes")}
    heads = [p for sha, p in packages.items() if sha not in superseded]
    if len(heads) != 1:
        return None, "AMBIGUOUS_CHAIN"
    head = heads[0]
    if __package__:
        from . import capture_source_case as capture
    else:
        import capture_source_case as capture
    captured = (rwids or {}).get(head.get("sha256")) or set()
    if len(captured) > 1:
        return head, "UNKNOWN"  # 同一包摘要被多个RWID登记：身份不明，不能统计为可复用
    located = capture.locate_package(layout, project, head, next(iter(captured)) if captured else None)
    if located["state"] != "OK":
        return head, located["state"]
    return head, "OK"


def _check_date(row: dict[str, Any]) -> tuple[date | None, str | None]:
    value = row.get("initialInspectionDate")
    if not value:
        return None, "DATE_UNKNOWN"
    try:
        return date.fromisoformat(str(value)[:10]), None
    except ValueError:
        return None, "DATE_CONFLICT"


def package_facts(layout: Any, index: dict[str, Any], project: str, row: dict[str, Any]) -> dict[str, Any]:
    entry = index.get(project) or {"status": None, "packages": {}}
    head, package_state = _package_head(layout, project, entry["packages"], entry.get("rwids"))
    check_date, issue = _check_date(row)
    return {
        "hasPackage": bool(entry["packages"]),
        "packageValid": package_state in {"OK", "NO_PACKAGE"},
        "packageIssue": package_state,
        "attachedAt": (head or {}).get("attachedAt"),
        "sourceStatus": entry["status"],
        "initialResult": row.get("initialResult"),
        "checkDate": check_date,
        "dateIssue": issue,
        "localComplete": bool(row.get("complete")),
        "lifecycle": row.get("lifecycle"),
    }


def package_decision_for_project(
    layout: Any,
    project: str,
    *,
    today: date | None = None,
    months: int = PACKAGE_WINDOW_MONTHS_DEFAULT,
    refresh_days: int = PACKAGE_REFRESH_DAYS_DEFAULT,
) -> dict[str, Any]:
    ledger = ws.load_waterline(layout)
    record = (ledger.get("cases") or {}).get(project)
    if not record:
        raise ws.WorkspaceStateError("总账没有该项目")
    row = describe(layout, project, record)
    facts = package_facts(layout, _package_capture_index(layout), project, row)
    return {"projectNo": project, **package_decision(facts, today=today, months=months, refresh_days=refresh_days)}



def scan_plan(
    layout: Any,
    batch_id: str | None = None,
    *,
    mode: str = "auto",
    year: int | None = None,
    limit: int = 10,
    package_months: int = PACKAGE_WINDOW_MONTHS_DEFAULT,
    package_refresh_days: int = PACKAGE_REFRESH_DAYS_DEFAULT,
    today: date | None = None,
) -> dict[str, Any]:
    ledger = ws.load_waterline(layout)
    annual = coverage_api.annual_coverage(layout, year, details=True, ledger=ledger)
    if mode not in {"auto", "annual-baseline", "recent"}:
        raise ws.WorkspaceStateError("扫描模式无效")
    if limit < 1 or limit > 10000:
        raise ws.WorkspaceStateError("计划条数必须在 1 至 10000 之间")
    selected_mode = (
        ("recent" if annual["complete"] else "annual-baseline") if mode == "auto" else mode
    )
    batch_filters = None
    if batch_id:
        _, _, saved_batch = ws._formal_capture_batch(layout, batch_id)
        batch_filters = saved_batch.get("filters")
        if mode == "auto":
            selected_mode = (
                "annual-baseline"
                if batch_filters.get("selectionMode") == coverage_api.ANNUAL_MODE
                else "recent"
            )
    pending = []
    for project, record in ledger["cases"].items():
        row = describe(layout, project, record)
        if row["activePending"]:
            pending.append(
                {
                    "projectNo": project,
                    "initialResult": row["initialResult"],
                    "unqualifiedCandidate": project in annual["candidateProjectNos"],
                    "changeEvidence": list(
                        record.get("source", {}).get("changeEvidenceByFingerprint", {}).values()
                    )
                    if row["sourceChanged"]
                    else [],
                    "action": "LOCAL_VERIFY"
                    if row["stages"]["systemRegistered"] or row["systemChanged"]
                    else "COLLECT"
                    if row["initialResult"] == "UNQUALIFIED"
                    else "CLASSIFY"
                    if row["initialResult"] == "UNKNOWN"
                    else "INDEX_ONLY",
                }
            )
    pending.sort(
        key=lambda x: (
            x["initialResult"] != "UNQUALIFIED",
            not x["unqualifiedCandidate"],
            x["projectNo"],
        )
    )
    actions = []
    if batch_id:
        if __package__:
            from .source_intake import document_fingerprint
        else:
            from source_intake import document_fingerprint
        _, _, capture = ws._formal_capture_batch(layout, batch_id)
        if capture.get("stableRounds", 0) < 2:
            raise ws.WorkspaceStateError("窗口清单尚未连续两轮稳定")
        by_rwid = {}
        for project, record in ledger["cases"].items():
            source = record.get("source") or {}
            for rwid in [source.get("rwid"), *(source.get("observationsByRwid") or {})]:
                if rwid:
                    by_rwid.setdefault(rwid, []).append(project)
        for rwid, observation in capture.get("records", {}).items():
            projects = set(by_rwid.get(rwid, []))
            project = next(iter(projects)) if len(projects) == 1 else None
            record = ledger["cases"].get(project, {})
            source = record.get("source") or {}
            previous = source.get("observationsByRwid", {}).get(rwid, {})
            incoming = {document_fingerprint(a) for a in observation.get("sourceAppearances", [])}
            same = bool(incoming and incoming <= set(previous.get("documentFingerprints") or []))
            row = describe(layout, project, record) if project else None
            unchanged_complete = bool(
                row
                and same
                and not source.get("changePending")
                and not row["systemChanged"]
                and (
                    row["historicalComplete"]
                    or row["completionMode"] == "LOCAL_V1"
                    or (source.get("indexOnly") is True and row["initialResult"] == "QUALIFIED")
                )
            )
            actions.append(
                {
                    "rwid": rwid,
                    "projectNo": project,
                    "action": "IDENTITY_CONFLICT"
                    if len(projects) > 1
                    else "SKIP_UNCHANGED"
                    if unchanged_complete
                    else "RESUME"
                    if same
                    else "READ_DETAIL",
                    "sourceChanged": bool(previous and not same),
                }
            )
    if selected_mode == "annual-baseline":
        actions = [
            {
                **item,
                "action": "IDENTITY_CONFLICT"
                if item["status"] == "IDENTITY_CONFLICT"
                else "READ_IDENTITY"
                if not item["projectNo"]
                else "REUSE_IDENTITY",
            }
            for item in annual["identities"]
        ]
        actions.sort(
            key=lambda item: (
                item["action"] == "REUSE_IDENTITY",
                item["candidatePriority"],
                item["rwid"],
            )
        )
    filters = (
        scan_filters()
        if selected_mode == "recent"
        else {
            "selectionMode": coverage_api.ANNUAL_MODE,
            "year": annual["sourceYear"],
            "dateShortcut": "本年",
            "sourceListKind": "CASE_TASK",
            "taskStatus": "ALL",
            "lawEnforcementUnit": "ALL",
            "jurisdiction": "全部管辖单位(含派出所)",
            "brigadeScope": "ALL",
            "timezone": "Asia/Shanghai",
        }
    )
    package_index = _package_capture_index(layout)
    package_decisions = []
    for project, record in ledger["cases"].items():
        row = describe(layout, project, record)
        decision = package_decision(
            package_facts(layout, package_index, project, row),
            today=today, months=package_months, refresh_days=package_refresh_days,
        )
        package_decisions.append({"projectNo": project, **decision})
    package_decisions.sort(key=lambda d: (d["action"] != "REFRESH_CANDIDATE", d["projectNo"]))
    return {
        "schemaVersion": "SourceScanPlanV2",
        "readOnly": True,
        "mode": selected_mode,
        "readyToScan": selected_mode == "annual-baseline" or annual["complete"],
        "readyToResumeBatch": bool(batch_id),
        "sourceCoverage": coverage_api.summary(annual),
        "filters": batch_filters or filters,
        "requiredPageReadback": [
            "startDate",
            "endDate",
            "dateFieldLabel",
            "queryRoute",
            "queryEvidencePath",
        ],
        "requiredTaskCategories": list(coverage_api.TASK_CATEGORIES)
        if selected_mode == "annual-baseline"
        else [],
        "pendingOutsideWindowRetained": True,
        "pendingCount": len(pending),
        "pending": pending[:limit],
        "packagePolicy": {
            "windowMonths": package_months,
            "refreshDays": package_refresh_days,
            "dateField": "来源检查日期(initialInspectionDate)",
            "window": package_decisions[0]["window"] if package_decisions else rolling_window(package_months, today),
            "readOnly": True,
        },
        "packageDecisionCounts": dict(Counter(d["code"] for d in package_decisions)),
        "packageDecisions": [d for d in package_decisions if d["action"] in {"REFRESH_CANDIDATE"}][:limit],
        "sourceActionCount": len(actions),
        "sourceActionCounts": dict(Counter(a["action"] for a in actions)),
        "sourceActions": actions[:limit],
    }


def index_history(layout: Any, *, apply: bool = False) -> dict[str, Any]:
    """Reuse formal, locally saved lists. No browser, package read, or OCR."""
    if __package__:
        from .source_intake import document_fingerprint
    else:
        from source_intake import document_fingerprint
    results = []
    captures = coverage_api.formal_captures(layout)
    ledger = ws.load_waterline(layout)
    bindings = coverage_api.identity_bindings(captures, ledger)
    missing_identity = set()
    conflicts = set()
    rejected_batches = []
    for capture in captures:
        batch_id = capture["batchId"]
        annual = capture.get("filters", {}).get("selectionMode") == coverage_api.ANNUAL_MODE
        if annual and coverage_api.query_evidence_issue(layout, capture):
            rejected_batches.append(batch_id)
            continue
        for rwid, item in capture.get("records", {}).items():
            detail = item.get("detail") or {}
            projects = bindings.get(rwid, set())
            if len(projects) > 1 or (
                projects and item.get("projectNo") and item["projectNo"] not in projects
            ):
                conflicts.add(rwid)
                continue
            project = next(iter(projects)) if projects else None
            fields = detail.get("fields") or {}
            if not project:
                missing_identity.add(rwid)
                continue
            observation = {
                "fingerprint": item.get("sourceRecordFingerprint"),
                "documentFingerprints": sorted(
                    {document_fingerprint(a) for a in item.get("sourceAppearances", [])}
                    if not annual
                    else set()
                ),
                "observedAt": coverage_api.observed_at(capture),
            }
            record = ledger["cases"].get(project, {})
            prior = record.get("source", {}).get("observationsByRwid", {}).get(rwid, {})
            new_docs = set(observation["documentFingerprints"]) - set(
                prior.get("documentFingerprints", [])
            )
            prior_batches = set(record.get("source", {}).get("batchIds", []))
            if project in ledger["cases"] and not new_docs and batch_id in prior_batches:
                continue
            updates = {
                "observationsByRwid": {rwid: observation} if not annual else {},
                "batchIds": sorted(prior_batches | {batch_id}),
            }
            if not record:
                updates.update(projectIdentitySource="DETAIL", rwid=rwid, status="DISCOVERED")
            if apply:
                ledger["cases"][project] = ws.upsert_case(
                    layout,
                    project,
                    source=updates,
                    **({"unitName": fields.get("unitName")} if not record else {}),
                )
            else:
                # Simulate the same merge so report/apply agree across repeated batches.
                from copy import deepcopy

                simulated = deepcopy(record)
                ws._deep_merge(simulated, {"source": updates})
                simulated["source"].setdefault("observationsByRwid", {}).setdefault(rwid, {})[
                    "documentFingerprints"
                ] = sorted(
                    set(prior.get("documentFingerprints", []))
                    | set(observation["documentFingerprints"])
                )
                ledger["cases"][project] = simulated
            results.append(
                {"projectNo": project, "newKnownDocuments": len(new_docs), "applied": apply}
            )
    return {
        "readOnly": not apply,
        "historicalSourceComplete": False,
        "sourceIdentitiesWithoutProject": len(missing_identity),
        "sourceIdentityConflicts": sorted(conflicts),
        "rejectedAnnualBatches": rejected_batches,
        "changes": results,
        "classificationChanges": classify_saved_details(layout, apply=apply),
    }
