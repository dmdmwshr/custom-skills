"""Annual source coverage derived from existing captures and the one case ledger.

Document searches are supporting evidence, never a substitute for the source's
annual case/task list. Unresolved RWIDs stay in BrowserCaptureV1, not fake cases.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

if __package__:
    from . import workspace_state as ws
else:
    import workspace_state as ws


ANNUAL_MODE = "ANNUAL_CASE_BASELINE"
TASK_CATEGORIES = (
    "日常监督任务",
    "开业前检查任务",
    "消防装备抽查任务",
    "社会单位产品抽查任务",
    "举报投诉核查任务",
    "火灾事故调查核查任务",
)
NOTICE_TYPES = ("责令限期改正通知书", "不合格产品通知书", "不合格通知书")


def execution_year() -> int:
    return datetime.now(ZoneInfo("Asia/Shanghai")).year


def formal_captures(layout: Any) -> list[dict[str, Any]]:
    captures = []
    if not layout.capture_batches.exists():
        return captures
    for directory in sorted(layout.capture_batches.iterdir()):
        if not directory.is_dir() or not (directory / "browser-capture.json").exists():
            continue
        # Malformed formal evidence must not silently disappear from coverage.
        value = ws._read_progress_json(
            directory / "browser-capture.json", layout.root, "来源覆盖批次"
        )
        if value.get("scope") != "all":
            continue
        _, _, capture = ws._formal_capture_batch(layout, directory.name)
        captures.append(capture)
    return captures


def capture_year(capture: dict[str, Any]) -> int | None:
    filters = capture.get("filters") or {}
    year = filters.get("year")
    if type(year) is int:
        return year
    return None  # A cross-year activity window is not a source annual query.


def identity_bindings(captures: list[dict], ledger: dict) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = defaultdict(set)
    for project, record in ledger.get("cases", {}).items():
        source = record.get("source") or {}
        if source.get("projectIdentitySource") != "DETAIL":
            continue
        for rwid in [
            source.get("rwid"),
            *(source.get("observationsByRwid") or {}),
            *(source.get("annualIdentitiesByRwid") or {}),
        ]:
            if rwid:
                bindings[rwid].add(project)
    for capture in captures:
        for rwid, row in capture.get("records", {}).items():
            detail = row.get("detail") or {}
            project = detail.get("projectNo")
            fields = detail.get("fields") or {}
            field_project = fields.get("projectNo") or fields.get("项目编号")
            if (
                isinstance(project, str)
                and ws.PROJECT_NO.fullmatch(project)
                and project == field_project
            ):
                bindings[rwid].add(project)
    return dict(bindings)


def candidate_reasons(row: dict[str, Any], filters: dict | None = None) -> list[str]:
    """Document hints prioritize reading; they never supply a qualification result."""
    reasons = set()
    if (filters or {}).get("selectionMode") == "ANNUAL_RECTIFICATION_NOTICE":
        reasons.add("RECTIFICATION_NOTICE")
    for appearance in row.get("sourceAppearances", []):
        title = str(appearance.get("documentName") or appearance.get("文书名称") or "")
        if any(name in title for name in NOTICE_TYPES):
            reasons.add("NOTICE_CANDIDATE")
        elif "结果通知" in title or "检验报告" in title or "检查记录" in title:
            reasons.add("RESULT_REQUIRES_REVIEW")
    return sorted(reasons)


def observed_at(capture: dict[str, Any]) -> str | None:
    # Local detail/index processing cannot refresh when the source list was read.
    timestamps = [
        str(page["observedAt"])
        for round_value in capture.get("rounds", {}).values()
        for page in round_value.get("pages", {}).values()
        if page.get("observedAt")
    ]
    return max(timestamps) if timestamps else None


def query_evidence_issue(layout: Any, capture: dict) -> str | None:
    evidence = capture.get("queryEvidence") or {}
    try:
        path = layout.root / evidence["relativePath"]
        ws._validated_workspace_path(path, layout.root, "年度筛选证据", must_exist=True)
        if evidence["sha256"] != "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest():
            return "QUERY_EVIDENCE_HASH_MISMATCH"
        query = ws._read_progress_json(path, layout.root, "年度查询回读")
        url_category = parse_qs(urlsplit(query.get("url", "")).fragment.partition("?")[2]).get(
            "name", [None]
        )[0]
        category = query.get("queryCategory") or url_category or query.get("category")
        if category != capture["filters"].get("taskCategory"):
            return "QUERY_CATEGORY_MISMATCH"
    except (OSError, KeyError, ValueError, ws.WorkspaceStateError):
        return "QUERY_EVIDENCE_UNAVAILABLE"
    return None


def candidate_priority(reasons: list[str]) -> int:
    if {"NOTICE_CANDIDATE", "RECTIFICATION_NOTICE"} & set(reasons):
        return 1
    return 2 if reasons else 3


def unavailable_categories(layout: Any, baseline_id: str | None, year: int) -> dict:
    if not baseline_id:
        return {}
    result = {}
    directory = layout.capture_batches / baseline_id
    for index, category in enumerate(TASK_CATEGORIES):
        path = directory / f"availability-{index}.json"
        if not path.is_file():
            continue
        value = ws._read_progress_json(path, layout.root, "来源入口可用性")
        if (
            value.get("schemaVersion") != "SourceCategoryAvailabilityV1"
            or value.get("baselineId") != baseline_id
            or value.get("sourceYear") != year
            or value.get("category") != category
            or value.get("status") != "UNAVAILABLE"
        ):
            raise ws.WorkspaceStateError("来源入口可用性证据身份不一致")
        evidence = value.get("screenshot") or {}
        image = layout.root / evidence.get("relativePath", "")
        ws._validated_workspace_path(image, layout.root, "来源不可用截图", must_exist=True)
        if evidence.get("sha256") != "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest():
            raise ws.WorkspaceStateError("来源不可用截图摘要不一致")
        result[category] = {k: value[k] for k in ("status", "message", "observedAt")}
    return result


def annual_coverage(
    layout: Any,
    year: int | None = None,
    *,
    details: bool = False,
    captures: list[dict] | None = None,
    ledger: dict | None = None,
) -> dict[str, Any]:
    year = execution_year() if year is None else year
    if type(year) is not int or not 2000 <= year <= 9999:
        raise ws.WorkspaceStateError("来源年度必须为有效四位年份")
    captures = formal_captures(layout) if captures is None else captures
    ledger = ws.load_waterline(layout) if ledger is None else ledger
    annual = [c for c in captures if capture_year(c) == year]
    baselines = [c for c in annual if c.get("filters", {}).get("selectionMode") == ANNUAL_MODE]
    # A newer unfinished attempt must not be hidden by an older successful one.
    baseline = max(baselines, key=lambda c: (c.get("createdAt", ""), c["batchId"]), default=None)
    baseline_id = baseline.get("filters", {}).get("baselineId") if baseline else None
    group = {}
    for capture in sorted(baselines, key=lambda c: (c.get("createdAt", ""), c["batchId"])):
        if capture.get("filters", {}).get("baselineId") == baseline_id:
            group[capture["filters"].get("taskCategory")] = capture
    evidence_issues = {category: query_evidence_issue(layout, c) for category, c in group.items()}
    valid_group = {category: c for category, c in group.items() if not evidence_issues[category]}
    membership_group = dict(valid_group)
    for category, current in valid_group.items():
        if current.get("records") or current.get("stableRounds", 0) >= 2:
            continue
        previous = [
            c
            for c in baselines
            if c["filters"].get("baselineId") == baseline_id
            and c["filters"].get("taskCategory") == category
            and c.get("stableRounds", 0) >= 2
            and not query_evidence_issue(layout, c)
        ]
        if previous:
            membership_group[category] = max(
                previous, key=lambda c: (c.get("createdAt", ""), c["batchId"])
            )
    missing_categories = sorted(set(TASK_CATEGORIES) - valid_group.keys())
    unavailable = {
        k: v
        for k, v in unavailable_categories(layout, baseline_id, year).items()
        if k in missing_categories
    }
    bindings = identity_bindings(captures, ledger)
    historical_rows: dict[str, dict] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    documents = set()
    if __package__:
        from .source_intake import document_fingerprint
    else:
        from source_intake import document_fingerprint
    for capture in annual:
        is_baseline = capture.get("filters", {}).get("selectionMode") == ANNUAL_MODE
        for rwid, row in capture.get("records", {}).items():
            historical_rows[rwid] = row
            if not is_baseline:
                documents.update(
                    (rwid, document_fingerprint(a)) for a in row.get("sourceAppearances", [])
                )
                reasons[rwid].update(candidate_reasons(row, capture.get("filters")))
    rows = (
        {
            rwid: row
            for capture in membership_group.values()
            for rwid, row in capture.get("records", {}).items()
        }
        if baseline
        else historical_rows
    )
    stable_captures = [
        c
        for c in group.values()
        if c.get("stableRounds", 0) >= 2
        and c.get("listResult") == "STABLE"
        and not c.get("conflicts")
        and not c.get("listDrift")
    ]
    stable = bool(baseline and not missing_categories and len(stable_captures) == len(group))
    evidence_valid = bool(group) and not any(evidence_issues.values())
    accepted_stable = [c for c in stable_captures if c in valid_group.values()]
    identities = []
    projects = set()
    unresolved = conflicts = 0
    for rwid, row in sorted(rows.items()):
        matches = bindings.get(rwid, set()).copy()
        # A new visible project number that disagrees with the trusted binding
        # requires investigation; an unconfirmed list number alone is not enough.
        listed = row.get("projectNo")
        conflict = len(matches) > 1 or bool(matches and listed and listed not in matches)
        project = next(iter(matches)) if len(matches) == 1 and not conflict else None
        if conflict:
            conflicts += 1
        elif not project:
            unresolved += 1
        if project:
            projects.add(project)
        identities.append(
            {
                "rwid": rwid,
                "projectNo": project,
                "status": "IDENTITY_CONFLICT"
                if conflict
                else "LINKED"
                if project
                else "UNRESOLVED",
                "candidateReasons": sorted(reasons[rwid]),
                "candidatePriority": candidate_priority(sorted(reasons[rwid])),
            }
        )
    missing = projects - ledger.get("cases", {}).keys()
    reconciled = stable and evidence_valid and not unresolved and not conflicts and not missing
    status = (
        "RECONCILED"
        if reconciled
        else "UNVERIFIED"
        if not baseline
        else "SOURCE_UNAVAILABLE"
        if unavailable
        else "QUERY_EVIDENCE_INVALID"
        if not evidence_valid
        else "CATEGORIES_PENDING"
        if missing_categories
        else "LIST_UNSTABLE"
        if not stable
        else "IDENTITY_PENDING"
    )
    result = {
        "schemaVersion": "SourceAnnualCoverageV1",
        "sourceYear": year,
        "status": status,
        "complete": bool(reconciled),
        "membershipBasis": "ANNUAL_CASE_LIST" if baseline else "HISTORICAL_DOCUMENTS_ONLY",
        "baselineBatchId": baseline["batchId"] if baseline else None,
        "baselineId": baseline_id,
        "baselineBatchIds": {category: c["batchId"] for category, c in group.items()},
        "membershipBatchIds": {category: c["batchId"] for category, c in membership_group.items()},
        "missingTaskCategories": missing_categories,
        "unavailableTaskCategories": unavailable,
        "sourceObservedAt": min((observed_at(c) or "" for c in valid_group.values()), default=None)
        or None,
        "query": baseline.get("filters") if baseline else None,
        "sourceListCount": sum(c.get("sourceListCount", 0) for c in group.values())
        if stable and evidence_valid
        else None,
        "observedSourceListCount": sum(c.get("sourceListCount", 0) for c in accepted_stable),
        "invalidTaskCategories": {k: v for k, v in evidence_issues.items() if v},
        "taskCategories": {
            category: {
                "stableRounds": c.get("stableRounds", 0),
                "sourceListCount": c.get("sourceListCount"),
                "sourceObservedAt": observed_at(c),
                "evidenceIssue": evidence_issues[category],
                "accepted": not evidence_issues[category],
                "query": c.get("filters"),
            }
            for category, c in group.items()
        },
        "sourceDocumentCount": len(documents),
        "sourceDocumentCountScope": "KNOWN_SAVED_ANNUAL_DOCUMENTS",
        "sourceIdentityCount": len(rows),
        "linkedSourceIdentities": len(rows) - unresolved - conflicts,
        "unresolvedSourceIdentities": unresolved,
        "conflictingSourceIdentities": conflicts,
        "uniqueProjectCount": len(projects),
        "indexedProjectCount": len(projects) - len(missing),
        "projectsMissingLedger": len(missing),
        "historicalSourceIdentityCount": len(historical_rows),
        "historicalIdentitiesOutsideBaseline": len(historical_rows.keys() - rows.keys())
        if baseline
        else None,
        "projectNos": sorted(projects),
        "candidateProjectNos": sorted(
            {i["projectNo"] for i in identities if i["projectNo"] and i["candidatePriority"] == 1}
        ),
        "candidateSourceIdentities": sum(i["candidatePriority"] == 1 for i in identities),
        "resultReviewSourceIdentities": sum(i["candidatePriority"] == 2 for i in identities),
        "note": "已按年度案卷清单逐项关联；不代表全部已迁移"
        if reconciled
        else "存在来源未开放入口；已核实部分继续关联，年度总水位尚未核实"
        if unavailable
        else "年度总水位尚未对齐；历史文书和本地案卷数不能替代来源年度总数",
    }
    if details:
        result["identities"] = identities
        result["missingLedgerProjectNos"] = sorted(missing)
    return result


def summary(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in coverage.items()
        if k not in {"projectNos", "candidateProjectNos", "identities", "missingLedgerProjectNos"}
    }
