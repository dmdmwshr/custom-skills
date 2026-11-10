"""Production consumer of the confirmed case lifecycle rules (2026-11-09 user instruction).

Turns the data the normal workflow already holds -- the saved source detail (SourceEvidenceV1 record
fields), the waterline classification and, when present, the local manifest -- into one assessment:
required-field state, initial date/result, per product chain recheck state, 是否行政处罚案卷, stability
and the concrete causes that justify a source revisit. The pure rules live in ``field_validation``; this
module only gathers facts. It never reads the network and never writes anything.

Blank values are classified, never treated as source gaps by default: 人工字段空白 (filled by the brigade),
非适用空值 (recheck branch already terminal) and 内部项 are separate from a genuinely missing source value.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

if __package__:
    from . import field_validation as fv
else:  # script-style import (scripts/ on sys.path)
    import field_validation as fv

SHANGHAI = ZoneInfo("Asia/Shanghai")

REQUIRED_FIELDS = (
    "projectNo",
    "unitName",
    "brigade",
    "initialDate",
    "unitCategory",
    "unitAddress",
    "inspectionForm",
    "initialResult",
)
# Brigade-filled fields: blank is waiting for the brigade, never a source gap and never "否".
MANUAL_FIELDS = ("是否加油加气站", "是否微型消防站", "是否网售", "是否维修后灭火器")
# Process fields kept for history/dependencies; never required and never a reason to re-collect.
PROCESS_FIELDS = ("统计归类", "数据层级")

CLASS_REUSE = "REUSE_DIRECT"
CLASS_LOCAL = "LOCAL_SUPPLEMENT"
CLASS_SOURCE = "NEEDS_SOURCE"
CLASSES = (CLASS_REUSE, CLASS_LOCAL, CLASS_SOURCE)

_RESULT_FROM_ENUM = {"QUALIFIED": "合格", "UNQUALIFIED": "不合格"}
_NOTICE_SLOTS = ("RECTIFICATION_ORDER", "ONSITE_UNQUALIFIED_NOTICE")
_ERROR_ICON, _OK_ICON = "el-icon-error", "el-icon-success"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def today_shanghai() -> date:
    return datetime.now(SHANGHAI).date()


def initial_result_label(classification: dict[str, Any] | None) -> str:
    """Cloud/外显 初查结果: 合格 / 不合格 / 待结果 (report or recheck result still coming) / 待核实 (no evidence)."""
    cls = classification or {}
    result = cls.get("initialResult")
    if result in _RESULT_FROM_ENUM:
        return _RESULT_FROM_ENUM[result]
    return "待结果" if cls.get("reinspectionPending") else "待核实"


def _row_stage(row: dict[str, Any]) -> str | None:
    record = row.get("页面记录") if isinstance(row, dict) else None
    flag = record.get("SFCPFC") if isinstance(record, dict) else None
    return {"0": "INITIAL", "1": "RECHECK"}.get(str(flag)) if flag is not None else None


def _row_result(row: dict[str, Any]) -> str | None:
    """合格/不合格 only when the page shows an unambiguous result; anything else is unknown (None)."""
    marks = row.get("检查结果标记") or []
    text = _text(row.get("检查结果"))
    quality, market = _text(row.get("产品质量现场检查情况")), _text(row.get("市场准入检查情况"))
    if text == "不合格" or (_ERROR_ICON in marks and re.search(r"不合格", quality + market)):
        return "不合格"
    if text == "合格" or (quality.startswith("未发现不合格") and market.startswith("未发现不合格") and _OK_ICON in marks):
        return "合格"
    return None


def _iso(value: Any) -> str | None:
    parsed = fv.parse_calendar_date(_text(value)[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", _text(value)) else value)
    return parsed.isoformat() if parsed else None


_UNQUALIFIED_CELL = re.compile(r"^(?:[1-9][0-9]*项)?不合格")


def evidence_initial_result(fields: dict[str, Any] | None) -> str | None:
    """合格 / 不合格 / 待结果 straight from the saved detail's product rows; None when the page does not settle it.

    The case status (已结案/处理中) is deliberately not an input. Recheck rows (SFCPFC=1) never decide the initial result.
    """
    rows = [r for r in ((fields or {}).get("检查产品信息") or []) if isinstance(r, dict) and _row_stage(r) != "RECHECK"]
    if not rows:
        return None
    cells = [_text(r.get(k)) for r in rows for k in ("产品质量现场检查情况", "市场准入检查情况")]
    if any(_UNQUALIFIED_CELL.match(c) for c in cells):
        return "不合格"
    if any(c == "抽样送检" for c in cells):
        return "待结果"
    if all(c.startswith("未发现不合格现象") for c in cells) and "[合格]" in _text((fields or {}).get("检查情况")):
        return "合格"
    return None


def _initial_label(classification: dict[str, Any] | None, manifest: dict[str, Any] | None, fields: dict[str, Any] | None = None) -> str:
    if classification and classification.get("initialResult") in _RESULT_FROM_ENUM:
        return initial_result_label(classification)
    from_page = evidence_initial_result(fields)
    if from_page:
        return from_page
    if classification:
        return initial_result_label(classification)
    results = [_manifest_result(p) for p in _manifest_products(manifest)[0]]
    if "不合格" in results:
        return "不合格"
    if results and all(r == "合格" for r in results):
        return "合格"
    pending = any(p.get("method") == "SAMPLING" and p.get("reinspectionApplied") == "YES" for p in _manifest_products(manifest)[0])
    return "待结果" if pending else "待核实"


def notice_date_from_manifest(manifest: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """责令限期改正通知书下发日期 from a manifest slot documentDate; unknown stays None (no countdown)."""
    for code in _NOTICE_SLOTS:
        for slot in (manifest or {}).get("documentSlots") or []:
            if isinstance(slot, dict) and slot.get("slotCode") == code and _iso(slot.get("documentDate")):
                return _iso(slot["documentDate"]), code
    return None, None


def _notice_blank_in_original(manifest: dict[str, Any] | None) -> bool:
    """A reviewed notice slot exists but its original carries no date: local materials are exhausted for this fact."""
    slots = [x for x in (manifest or {}).get("documentSlots") or [] if isinstance(x, dict) and x.get("slotCode") in _NOTICE_SLOTS]
    return bool(slots) and not any(_iso(x.get("documentDate")) for x in slots)


def _manifest_products(manifest: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    manifest = manifest or {}
    initial = [p for p in (manifest.get("initialInspection") or {}).get("products") or [] if isinstance(p, dict)]
    recheck_block = manifest.get("recheckInspection")
    recheck = [p for p in (recheck_block or {}).get("products") or [] if isinstance(p, dict)]
    return initial, recheck, isinstance(recheck_block, dict)


def _manifest_result(product: dict[str, Any]) -> str | None:
    if product.get("method") == "SAMPLING" and product.get("reinspectionApplied") == "YES":
        value = product.get("reinspectionResult")
    else:
        value = product.get("result")
    return _RESULT_FROM_ENUM.get(value)


def _chains_and_state(
    *,
    evidence_fields: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    classification: dict[str, Any] | None,
    observed_on: str | None,
    new_evidence: bool,
    initial_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per product chains with each chain's recheck state, plus the case level recheck summary."""
    notice, notice_slot = notice_date_from_manifest(manifest)
    m_initial, m_recheck, has_recheck_block = _manifest_products(manifest)
    rows = [r for r in ((evidence_fields or {}).get("检查产品信息") or []) if isinstance(r, dict)]
    e_initial = [r for r in rows if _row_stage(r) == "INITIAL"]
    e_recheck = [r for r in rows if _row_stage(r) == "RECHECK"]
    if m_initial:
        initials = [{"clientRef": p.get("clientRef"), "result": _manifest_result(p)} for p in m_initial]
        rechecks = [
            {"clientRef": p.get("clientRef"), "initialProductRef": p.get("initialProductRef"), "result": _manifest_result(p)}
            for p in m_recheck
        ]
    else:
        initials = [{"clientRef": f"row:{i}", "result": _row_result(r)} for i, r in enumerate(e_initial)]
        # Evidence rows carry no cross-stage reference, so recheck rows stay unlinked (never joined by name/position).
        rechecks = [{"clientRef": f"recheck-row:{i}", "initialProductRef": None, "result": _row_result(r)} for i, r in enumerate(e_recheck)]
    if (not initials or all(p["result"] is None for p in initials)) and initial_label in fv.INITIAL_RESULTS:
        initials = [{"clientRef": "case", "result": initial_label}]
    started: bool | None = True if (rechecks or has_recheck_block) else None
    stages = {*((classification or {}).get("projectInspectionStages") or []), *((classification or {}).get("sourceInspectionStages") or [])}
    if "RECHECK" in stages:
        started = True
    # A completed observation of the saved detail with no recheck row/stage is evidence of "not started" at that day.
    observed_not_started = bool(observed_on and evidence_fields is not None and started is None)
    if observed_not_started:
        started = False
    chains = fv.product_chains(
        [{"clientRef": p["clientRef"], "result": p["result"]} for p in initials],
        rechecks,
    )
    initial_pending = initial_label == "待结果" or bool((classification or {}).get("reinspectionPending"))
    # Recheck products that carry no resolvable initialProductRef (old manifests, page rows) cannot be joined to a specific
    # initial product, so no count of valid recheck results closes any chain. Every unqualified initial product that has
    # no linked recheck, and every unlinked recheck, stays open as LINK_UNPROVEN until a same-product link is recorded;
    # chains that do have proven links are judged normally.
    has_unlinked = any(not c["linked"] for c in chains)
    detailed: list[dict[str, Any]] = []
    for chain in chains:
        if not chain["linked"]:
            detailed.append({"linked": False, "recheckRef": chain.get("recheckRef"), "initial_result": None, "recheck_state": "待核实",
                             "recheck_result": None, "reasonCode": "LINK_UNPROVEN",
                             "reason": "复查产品未经 initialProductRef 对应到同一初查产品，不按数量/名称/位置推断"})
            continue
        initial_result = chain.get("initialResult")
        recheck_result = next((r for r in chain.get("recheckResults") or [] if r in fv.INITIAL_RESULTS), None)
        if has_unlinked and initial_result == "不合格" and not chain.get("recheckRefs"):
            detailed.append({"linked": True, "initialRef": chain["initialRef"], "initial_result": initial_result, "recheck_state": "待核实",
                             "recheck_result": None, "reasonCode": "LINK_UNPROVEN",
                             "reason": "本不合格初查产品没有经 initialProductRef 对应的复查，未关联复查不能代替"})
            continue
        status = fv.recheck_status(
            initial_result if initial_result in fv.INITIAL_RESULTS else ("待结果" if initial_pending else ""),
            notice_date=notice,
            recheck_started=started if not chain.get("recheckRefs") else True,
            recheck_result=recheck_result,
            observed_on=observed_on,
            observed_not_started=observed_not_started,
        )
        detailed.append(
            {
                "linked": True,
                "initialRef": chain["initialRef"],
                "initial_result": initial_result if initial_result in fv.INITIAL_RESULTS else None,
                "recheck_state": status["state"],
                "recheck_result": recheck_result,
                "dueDate": status.get("dueDate"),
                "reason": status["reason"],
            }
        )
    if not detailed:  # no product facts at all: the branch is simply undetermined
        status = fv.recheck_status("", notice_date=notice)
        detailed.append({"linked": True, "initialRef": None, "initial_result": None, "recheck_state": status["state"], "recheck_result": None, "reason": status["reason"]})
    def overall_of(rows: list[dict[str, Any]]) -> str | None:
        states = [c["recheck_state"] for c in rows]
        if not states:
            return None
        if all(s in fv.RECHECK_TERMINAL_STATES for s in states):
            return "到期确认未开展" if "到期确认未开展" in states else ("复查有有效结果" if "复查有有效结果" in states else "初查合格无需复查")
        order = ("初查结果待定", "已开展复查待结果", "待核实", "观察期内未开展")
        return next((s for s in order if s in states), states[0])

    overall = overall_of(detailed)
    unlinked_chains = [c for c in detailed if c.get("reasonCode") == "LINK_UNPROVEN"]
    due = sorted(c["dueDate"] for c in detailed if c.get("dueDate"))
    summary = {
        "state": overall,
        "noticeDate": notice,
        "noticeSlot": notice_slot,
        "dueDate": due[0] if due else None,
        "observedOn": observed_on,
        "closed": overall in fv.RECHECK_TERMINAL_STATES,
        "newEvidence": new_evidence,
        # chains whose same-product link is unproven only block themselves; the others keep their own state
        "linkUnproven": len(unlinked_chains),
        "otherState": overall_of([c for c in detailed if c.get("reasonCode") != "LINK_UNPROVEN"]),
    }
    return detailed, summary


def _required_state(
    *,
    project: str,
    evidence_fields: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    classification: dict[str, Any] | None,
    initial_date: dict[str, Any],
    result_label: str,
    ledger_identity: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    fields, case = evidence_fields or {}, (manifest or {}).get("case") or {}
    ledger = ledger_identity or {}

    def present(value: Any, **extra: Any) -> dict[str, Any]:
        return {"state": "present", "value": value, **extra}

    def missing(reason: str) -> dict[str, Any]:
        return {"state": "missing", "reason": reason}

    unit = _text(fields.get("单位名称")) or _text(case.get("unitName")) or _text(ledger.get("unitName"))
    address = _text(fields.get("单位地址")) or _text(case.get("unitAddress")) or _text(ledger.get("unitAddress"))
    brigade = _text(fields.get("消防管辖")) or _text(case.get("brigadeName") or case.get("brigadeCode")) or _text(ledger.get("brigade"))
    category = _text(fields.get("单位类别")) or _text(case.get("unitCategory"))
    form = _text(case.get("inspectionForm"))
    out: dict[str, dict[str, Any]] = {"projectNo": present(project) if fv.PROJECT_NO.fullmatch(_text(project)) else missing("项目编号格式无效")}
    out["unitName"] = missing("单位名称缺失") if not unit else (
        {"state": "conflict", "reason": "单位名称与单位地址串栏"} if fv.unit_name_conflicts_address(unit, address) else present(unit)
    )
    out["unitAddress"] = present(address) if address else missing("单位地址缺失")
    out["brigade"] = present(brigade) if brigade else missing("所属大队缺失(详情消防管辖/同案文书)")
    out["unitCategory"] = present(category) if category else missing("单位类别缺失")
    if form and form != "UNKNOWN":
        out["inspectionForm"] = present(form)
    else:
        out["inspectionForm"] = {"state": "pending", "label": "待核实", "reason": "检查形式来自系统查询界面/任务入口，未证映射只保原值待核实"}
    out["initialDate"] = (
        present(initial_date["value"], externalSource=initial_date["externalSource"], internalSource=initial_date["internalSource"], fallback=initial_date["fallback"])
        if initial_date.get("value")
        else missing("初查日期缺失(电子监督检查记录与受理日期均无)")
    )
    if result_label in fv.INITIAL_RESULTS:
        out["initialResult"] = present(result_label)
    else:
        out["initialResult"] = {"state": "pending", "label": result_label, "reason": "初查结果尚未得出，进入有因更新" if result_label == "待结果" else "缺少初查结果证据"}
    return out


def assess_case(
    *,
    project: str,
    evidence_fields: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    observed_on: str | None = None,
    has_package: bool = False,
    new_evidence: bool = False,
    today: date | None = None,
    ledger_identity: dict[str, Any] | None = None,
    refused_evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Assess one case from existing normal-workflow data. Pure; ``today`` only decides whether a due day has arrived."""
    today = today or today_shanghai()
    fields = evidence_fields or {}
    electronic = next(
        (
            _iso((r.get("页面记录") or {}).get("JCRQ"))
            for r in fields.get("检查产品信息") or []
            if isinstance(r, dict) and _row_stage(r) == "INITIAL" and _iso((r.get("页面记录") or {}).get("JCRQ"))
        ),
        None,
    ) or _iso((manifest or {}).get("initialInspection", {}).get("inspectionDate")) or _iso((classification or {}).get("initialInspectionDate"))
    initial_date = fv.resolve_initial_date(electronic, fields.get("受理日期"))
    label = _initial_label(classification, manifest, evidence_fields)
    chains, recheck = _chains_and_state(
        evidence_fields=evidence_fields, manifest=manifest, classification=classification, observed_on=observed_on, new_evidence=new_evidence, initial_label=label
    )
    required = _required_state(
        project=project, evidence_fields=evidence_fields, manifest=manifest, classification=classification, initial_date=initial_date, result_label=label,
        ledger_identity=ledger_identity,
    )
    admin = fv.administrative_class(chains)
    identity_complete = all(required[name]["state"] == "present" for name in ("projectNo", "unitName", "brigade", "unitAddress", "initialDate", "unitCategory"))
    stable = fv.is_stable_case(
        identity_complete=identity_complete, initial_result=label, recheck_state=recheck["state"], has_new_evidence=new_evidence
    )
    causes: list[str] = []
    local: list[str] = []
    for name, entry in required.items():
        if entry["state"] in {"missing", "conflict"}:
            (local if has_package or manifest else causes).append(f"MISSING_{name}")
        elif entry["state"] == "pending" and name == "initialResult":
            # 文书目录里的“检验报告”可能是型式试验报告而不是本次抽样检验报告(实读 5 案均为型式试验)，故标题不能改判为本地可补
            causes.append("INITIAL_RESULT_PENDING" if label == "待结果" else "INITIAL_RESULT_UNPROVEN")
    state = recheck["state"]
    if recheck.get("linkUnproven"):
        # An unproven same-product link is a local gap (record initialProductRef from the case materials); it blocks only
        # those chains, so every other chain is judged on its own state below.
        local.append("RECHECK_PRODUCT_LINK_UNPROVEN")
        state = recheck.get("otherState") or ""
    if refused_evidence and evidence_fields is None:
        causes.append("SAVED_DETAIL_UNTRUSTED")  # saved details exist but none is verifiably this case's observation
    if state == "待核实":
        if recheck["noticeDate"]:
            causes.append("RECHECK_UNPROVEN")
        elif _notice_blank_in_original(manifest):
            causes.append("NOTICE_DATE_BLANK_IN_ORIGINAL")  # 原件空白：本地已无从补，回源查系统文书创建时间
        else:
            (local if has_package or manifest else causes).append("NOTICE_DATE_UNKNOWN")
    elif state == "已开展复查待结果":
        causes.append("RECHECK_RESULT_PENDING")
    elif state == "观察期内未开展":
        due = fv.parse_calendar_date(recheck["dueDate"])
        if due is not None and today >= due:
            causes.append("RECHECK_DUE_REOBSERVE")
    elif state == "初查结果待定" and not {"INITIAL_RESULT_PENDING", "INITIAL_RESULT_UNPROVEN"} & set(causes):
        causes.append("INITIAL_RESULT_PENDING")
    if new_evidence:
        causes.append("NEW_EVIDENCE")
    if stable:
        category = CLASS_REUSE
    elif causes:
        category = CLASS_SOURCE
    elif local:
        category = CLASS_LOCAL
    elif state == "观察期内未开展":
        category = CLASS_REUSE  # nothing to do before the due day; nextCheck says when
    else:
        category = CLASS_LOCAL
    return {
        "projectNo": project,
        "required": required,
        "initialDate": initial_date,
        "initialResult": label,
        "recheck": recheck,
        "chains": chains,
        "administrativeClass": admin,
        "stable": stable,
        "needsFollowUp": not stable and bool(causes or local),
        "category": category,
        "sourceCauses": causes,
        "localCauses": local,
        "evidenceRefused": list(refused_evidence or [])[:8],
        "nextCheckOn": recheck["dueDate"] if state == "观察期内未开展" else None,
        "manualFieldsIgnored": list(MANUAL_FIELDS),
        "processFieldsIgnored": list(PROCESS_FIELDS),
    }
