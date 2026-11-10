"""Read-only validation of extracted case fields (merged from the retired xf-product-case-filler).

Rows are identified by the stable (project number, inspection phase) pair. The validator never
mutates its input, never renames or deletes images, and never writes a workbook. Manual decisions
listed in ``manual_fields`` are protected: they are reported, not overridden or flagged as errors.
"""

from __future__ import annotations

import calendar
import copy
import json
import re
from datetime import date
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REQUIRED_KEYS = (
    "project_no",
    "unit_name",
    "unit_address",
    "nominal_producers",
    "case_handler",
    "inspector",
    "inspection_date",
    "products",
    "station_or_team",
    "method",
    "qualified",
    "case_type",
    "online_sale",
    "source_files",
    "file_roles",
    "missing_fields",
    "notes",
)

ALLOWED_VALUES = {
    "inspection_phase": {"初查", "复查"},
    "station_or_team": {"微型消防站", "快速处置队", "否"},
    "method": {"现场判定", "抽样送检"},
    # 待结果：真实等待报告/复查结果；待核实：仅缺证据。两者不是合格/不合格，也不是未知错误。
    "qualified": {"合格", "不合格", "待结果", "待核实"},
    "case_type": {"刑案", "行案", "否"},
    "online_sale": {"是", "否"},
}
# Fields whose blank value means "unknown", never "no": reported, never defaulted.
TRI_STATE_FIELDS = ("online_sale", "qualified", "method", "station_or_team")
ROLE_VALUES = {"案卷清单", "检查记录表", "其他附件"}
PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
FULL_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
CN_DATE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$")
MONTH = re.compile(r"^\d{1,2}月$")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def phase_of(case: dict[str, Any]) -> str:
    return text(case.get("inspection_phase")) or "初查"


def round_of(case: dict[str, Any]) -> int | None:
    """Optional 1-based round within a phase (e.g. a second recheck); absent means round 1."""
    value = case.get("inspection_round", 1)
    return value if type(value) is int and value >= 1 else None


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not all(isinstance(item, dict) for item in cases):
        raise ValueError("抽取文件必须是案件对象数组，或包含 cases 数组的对象")
    return cases


_NAME_PUNCTUATION = re.compile(r"[\s\u3000.,，。、;；:：·\-—_()（）\[\]【】]+")
_FINE_GRAINED_ADDRESS = re.compile(r"\d|[０-９]|[路街巷弄号室楼栋幢座层]")


def unit_name_conflicts_address(unit_name: Any, unit_address: Any) -> bool:
    """True only when the unit-name column really holds the address (column shift), not when a legitimate name embeds place text.

    Conflict: the name equals the address; or the address is specific (house number, road, building, ...) and appears in the
    name at all (an organisation plus its street address in the name column); or the address is only a place name and the
    name ends with it (nothing but punctuation follows). A place-name address that is followed by further name text
    (``廊下村`` -> ``廊下村民委员会``) belongs to a legitimate name that merely contains place words and is not rejected.
    Missing values never conflict here; required-field checks stay with the callers.
    """

    name, address = text(unit_name), text(unit_address)
    if not name or not address:
        return False
    if name == address:
        return True
    if address not in name:
        return False
    if _FINE_GRAINED_ADDRESS.search(address):
        return True
    return not _NAME_PUNCTUATION.sub("", name.rsplit(address, 1)[1])


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _manual(case: dict[str, Any]) -> set[str]:
    value = case.get("manual_fields")
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _not_applicable(case: dict[str, Any]) -> set[str]:
    value = case.get("not_applicable_fields")
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _project_lifecycle(project: str, initial: dict[str, Any] | None, recheck: dict[str, Any] | None) -> dict[str, Any]:
    """Recheck state and 是否行政处罚案卷 from the extraction rows, through the confirmed rules.

    Extraction rows are case level: a chain is only linked when each stage lists exactly one product; otherwise
    products cannot be joined by name or position and the answer stays 待核实.
    """
    initial_result = text((initial or {}).get("qualified"))
    status = recheck_status(
        initial_result,
        notice_date=(initial or {}).get("notice_date"),
        recheck_started=True if recheck is not None else None,
        recheck_result=text((recheck or {}).get("qualified")) or None,
    )
    def count(row: dict[str, Any] | None) -> int:
        products = (row or {}).get("products")
        return sum(1 for item in products if text(item)) if isinstance(products, list) else 0
    linked = count(initial) == 1 and (recheck is None or count(recheck) == 1)
    chain = {
        "linked": linked,
        "initial_result": initial_result if initial_result in INITIAL_RESULTS else None,
        "recheck_state": status["state"],
        "recheck_result": text((recheck or {}).get("qualified")) or None,
    }
    return {
        "projectNo": project,
        "recheckState": status["state"],
        "recheckReason": status["reason"],
        "administrativeClass": administrative_class([chain]),
    }


def validate_cases(
    cases: Iterable[dict[str, Any]], image_dir: Path | None = None
) -> dict[str, Any]:
    """Return a report; ``cases`` is deep-copied so callers' objects are never modified."""
    rows = copy.deepcopy(list(cases))
    errors: list[str] = []
    warnings: list[str] = []
    unknown: list[dict[str, str]] = []
    protected: list[dict[str, str]] = []
    manual_blank: list[dict[str, str]] = []
    not_applicable: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    seen: set[tuple[str, str, int]] = set()
    by_project: dict[str, list[dict[str, Any]]] = {}
    lifecycle: list[dict[str, Any]] = []

    for index, case in enumerate(rows, start=1):
        label = f"第 {index} 个案件"
        for key in REQUIRED_KEYS:
            if key not in case:
                errors.append(f"{label} 缺少字段：{key}")
        project = text(case.get("project_no"))
        phase = phase_of(case)
        manual = _manual(case)
        for field in sorted(manual):
            protected.append({"projectNo": project, "phase": phase, "field": field})
        if manual and not text(case.get("manual_note")):
            warnings.append(f"{label} 含人工裁决字段但缺少 manual_note 说明依据")
        if not project:
            errors.append(f"{label} 缺少项目编号 project_no")
        else:
            if not PROJECT_NO.fullmatch(project):
                errors.append(f"{label} 项目编号格式不合法：{project}")
            number = round_of(case)
            if number is None:
                errors.append(f"{label} inspection_round 必须是不小于 1 的整数")
            elif (project, phase, number) in seen:
                suffix = f"/第{number}轮" if number > 1 else ""
                errors.append(f"{label} 项目编号和初查/复查重复：{project}/{phase}{suffix}")
            else:
                seen.add((project, phase, number))
            by_project.setdefault(project, []).append(case)
        if phase not in ALLOWED_VALUES["inspection_phase"]:
            errors.append(f"{label} inspection_phase 值不在允许范围内：{phase}")

        products, producers = case.get("products"), case.get("nominal_producers")
        if not _string_list(products):
            errors.append(f"{label} products 必须是字符串数组")
        elif not any(text(item) for item in products):
            warnings.append(f"{label} products 为空，保持留空，不推测")
        if not _string_list(producers):
            errors.append(f"{label} nominal_producers 必须是字符串数组")
        elif not any(text(item) for item in producers):
            warnings.append(f"{label} nominal_producers 为空，保持留空，不推测")
        if _string_list(products) and _string_list(producers):
            names = [text(item) for item in products if text(item)]
            makers = [text(item) for item in producers if text(item)]
            if names and makers and len(names) != len(makers):
                warnings.append(
                    f"{label} products 与 nominal_producers 数量不一致，"
                    "产品与生产者对应关系需人工核对"
                )

        unit_name, unit_address = text(case.get("unit_name")), text(case.get("unit_address"))
        if unit_name_conflicts_address(unit_name, unit_address):
            errors.append(f"{label} 单位名称与单位地址疑似串栏：unit_name 不得等于 unit_address，也不得把门牌/道路等具体地址写入名称栏，或以地名地址结尾")

        files = case.get("source_files")
        if not _string_list(files):
            errors.append(f"{label} source_files 必须是字符串数组")
            files = []
        elif not files:
            errors.append(f"{label} source_files 不能为空")
        roles = case.get("file_roles")
        if not isinstance(roles, dict):
            errors.append(f"{label} file_roles 必须是对象")
            roles = {}
        for field, allowed in ALLOWED_VALUES.items():
            value = text(case.get(field))
            if value and value not in allowed:
                errors.append(f"{label} {field} 值不在允许范围内：{value}")
        na = _not_applicable(case)
        for field in TRI_STATE_FIELDS:
            if text(case.get(field)):
                continue
            entry = {"projectNo": project, "phase": phase, "field": field}
            if field in manual:  # 人工空白：等待大队填报，不是来源缺项，不触发补采
                manual_blank.append(entry)
            elif field in na:  # 非适用空值：该分支已终结，不触发重新下载
                not_applicable.append(entry)
            else:
                unknown.append(entry)
        if text(case.get("qualified")) in {"待结果", "待核实"}:
            pending.append({"projectNo": project, "phase": phase, "field": "qualified", "label": text(case.get("qualified"))})

        date_value = text(case.get("inspection_date"))
        if date_value and not (FULL_DATE.match(date_value) or CN_DATE.match(date_value)):
            if MONTH.match(date_value):
                warnings.append(f"{label} inspection_date 仍是月份，不能当作完整日期：{date_value}")
            else:
                warnings.append(f"{label} inspection_date 不是可识别完整日期：{date_value}")
        if not date_value:
            entry = {"projectNo": project, "phase": phase, "field": "inspection_date"}
            if "inspection_date" in manual:
                manual_blank.append(entry)
            elif "inspection_date" in na:
                not_applicable.append(entry)
            else:
                unknown.append(entry)
        elif phase == "复查" and "inspection_date" not in manual:
            source = text(case.get("inspection_date_source"))
            if not source:
                warnings.append(f"{label} 复查日期缺少 inspection_date_source，来源未证明")
            elif source not in files:
                errors.append(f"{label} 复查日期来源不在 source_files 中：{source}")

        if not _string_list(case.get("missing_fields")):
            errors.append(f"{label} missing_fields 必须是字符串数组")
        if "case_type_review" in case and not isinstance(case["case_type_review"], bool):
            errors.append(f"{label} case_type_review 必须是布尔值")
        for name in set(files):
            if image_dir is not None:
                path = Path(name)
                if not path.is_absolute():
                    path = image_dir / name
                if not path.exists():
                    errors.append(f"{label} source_files 中的文件不存在：{name}")
        for name, role in roles.items():
            if name not in files:
                warnings.append(f"{label} file_roles 包含未列入 source_files 的文件：{name}")
            if role not in ROLE_VALUES:
                errors.append(f"{label} file_roles 的角色不合法：{name}={role}")

    for project, group in by_project.items():
        multi_round = any((round_of(c) or 1) > 1 for c in group)
        if len(group) > 2 and not multi_round:
            errors.append(
                f"{project}: 同一项目编号最多只能有 初查/复查 两行，"
                "多轮复查须显式 inspection_round"
            )
        initial = next((c for c in group if phase_of(c) == "初查"), None)
        rechecks = sorted(
            (c for c in group if phase_of(c) == "复查"), key=lambda c: round_of(c) or 1
        )
        recheck = rechecks[-1] if rechecks else None  # latest round decides
        if recheck is not None:
            if initial is None:
                errors.append(f"{project}: 存在复查行但缺少初查行")
            elif text(initial.get("qualified")) != "不合格" and "qualified" not in _manual(initial):
                # A source record may explicitly mark a recheck while the earlier
                # sampling outcome remains pending. Do not manufacture that outcome.
                phase_source = text(recheck.get("inspection_phase_source"))
                if not phase_source or phase_source not in recheck.get("source_files", []):
                    errors.append(f"{project}: 初查结论未证明不合格时，复查须有 source_files 内的 inspection_phase_source")
            elif (
                text(recheck.get("inspection_date"))
                and text(recheck.get("inspection_date")) == text(initial.get("inspection_date"))
                and "inspection_date" not in _manual(recheck)
                and not text(recheck.get("inspection_date_source"))
            ):
                errors.append(f"{project}: 复查日期与初查日期相同且无独立来源，疑似复制初查日期")
        lifecycle.append(_project_lifecycle(project, initial, recheck))
        for case in group:
            case_type = text(case.get("case_type"))
            source = text(case.get("case_type_source"))
            if source and source not in case.get("source_files", []):
                errors.append(f"{project}/{phase_of(case)}: case_type_source 不在 source_files 中")
            if case_type in {"行案", "刑案"} and not source:
                if "case_type" in _manual(case):
                    warnings.append(f"{project}/{phase_of(case)}: 案卷类型为人工裁决，保留并待来源核对")
                else:
                    warnings.append(f"{project}/{phase_of(case)}: 历史案卷类型缺少独立来源，不由复查结果推定或改写")

    return {
        "status": "ok" if not errors else "errors_found",
        "caseCount": len(rows),
        "errors": errors,
        "warnings": warnings,
        "unknownFields": unknown,
        "protectedManualFields": protected,
        "manualBlankFields": manual_blank,
        "nonApplicableFields": not_applicable,
        "pendingResultFields": pending,
        "lifecycle": lifecycle,
        "inputModified": False,
    }


# ---------------------------------------------------------------------------------------------
# 2026-11-09 用户直接指令：初查日期来源、复查自然月收口、同产品链处罚归类、稳定判定。
# 纯函数，不读写文件。一个月是用户指定的内部更新收口规则，不是法定期限；日期均为上海时区日历日。
# ---------------------------------------------------------------------------------------------

INITIAL_DATE_SOURCE_EXTERNAL = "监督系统"
RECHECK_STATES = (
    "初查结果待定",
    "初查合格无需复查",
    "观察期内未开展",
    "已开展复查待结果",
    "复查有有效结果",
    "到期确认未开展",
    "待核实",
)
# 这些状态下复查分支已终结：日期/结果的空值是“非适用”，不是缺件，也不触发重新下载或截图。
RECHECK_TERMINAL_STATES = frozenset({"初查合格无需复查", "复查有有效结果", "到期确认未开展"})
INITIAL_RESULTS = {"合格", "不合格"}
PENDING_RESULTS = {"待结果", "待核实"}


def parse_calendar_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD (or YYYY年M月D日); anything else is unknown, never guessed."""
    raw = text(value)
    if FULL_DATE.match(raw):
        year, month, day = (int(part) for part in raw.split("-"))
    elif CN_DATE.match(raw):
        year, month, day = (int(part) for part in re.findall(r"\d+", raw))
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def review_due_date(notice_date: Any) -> date | None:
    """Natural month after the 责令限期改正通知书 date; the day is clamped to month end."""
    start = parse_calendar_date(notice_date)
    if start is None:
        return None
    month_index = start.year * 12 + start.month  # month after (0-based month + 1)
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def resolve_initial_date(electronic: Any, acceptance: Any) -> dict[str, Any]:
    """Electronic 监督检查记录 date wins; 受理日期 is a recorded fallback, never a silent override."""
    e_text, a_text = text(electronic), text(acceptance)
    e_date, a_date = parse_calendar_date(e_text), parse_calendar_date(a_text)
    if e_date is not None:
        return {
            "value": e_date.isoformat(),
            "internalSource": "电子监督检查记录",
            "externalSource": INITIAL_DATE_SOURCE_EXTERNAL,
            "fallback": False,
            "conflict": a_date is not None and a_date != e_date,
            "acceptanceDate": a_date.isoformat() if a_date else None,
        }
    if a_date is not None:
        return {
            "value": a_date.isoformat(),
            "internalSource": "详情受理日期(后备)",
            "externalSource": INITIAL_DATE_SOURCE_EXTERNAL,
            "fallback": True,
            "conflict": False,
            "fallbackReason": "电子监督检查记录缺失、不可用或日期无法确认",
            "electronicRaw": e_text or None,
        }
    return {"value": None, "internalSource": None, "externalSource": None, "fallback": False, "conflict": False}


def recheck_status(
    initial_result: str,
    *,
    notice_date: Any = None,
    recheck_started: bool | None = None,
    recheck_result: str | None = None,
    observed_on: Any = None,
    observed_not_started: bool = False,
) -> dict[str, Any]:
    """Classify the recheck branch of one product chain.

    ``recheck_started`` is None when unknown. A missing notice date never starts a countdown, and
    a missing recheck document alone never means 未开展。"到期确认未开展" needs a notice date, an
    observation not earlier than the due date that shows no recheck, and no contrary evidence.
    """
    initial = text(initial_result)
    result = text(recheck_result)
    if initial not in INITIAL_RESULTS:
        return {"state": "初查结果待定", "closed": False, "reason": "初查结果未定，按有因更新跟踪"}
    if initial == "合格":
        return {"state": "初查合格无需复查", "closed": True, "reason": "初查合格，复查不适用"}
    if result in INITIAL_RESULTS:
        return {"state": "复查有有效结果", "closed": True, "reason": "已有复查有效结果"}
    if recheck_started is True or result == "待结果":
        return {"state": "已开展复查待结果", "closed": False, "reason": "复查已开展，结果待出，不被一个月规则改写"}
    due = review_due_date(notice_date)
    if due is None:
        return {"state": "待核实", "closed": False, "reason": "缺责令限期改正通知书下发日期，不倒计时"}
    if recheck_started is not False:
        return {"state": "待核实", "closed": False, "reason": "是否开展复查未证明", "dueDate": due.isoformat()}
    seen = parse_calendar_date(observed_on)
    if seen is None or not observed_not_started:
        return {"state": "待核实", "closed": False, "reason": "缺有效来源观察", "dueDate": due.isoformat()}
    if seen < due:
        return {"state": "观察期内未开展", "closed": False, "reason": "观察早于到期日", "dueDate": due.isoformat()}
    return {"state": "到期确认未开展", "closed": True, "reason": "到期后有效观察仍未开展", "dueDate": due.isoformat()}


def product_chains(
    initial_products: Iterable[dict[str, Any]], recheck_products: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Link recheck products to initial products only through an explicit ``initialProductRef``.

    Names, array positions and counts never link products. A recheck product whose reference does
    not resolve to an INITIAL_CHECK product of the same case produces an unlinked chain.
    """
    initials = {p.get("clientRef"): p for p in initial_products if isinstance(p, dict)}
    linked: dict[str, list[dict[str, Any]]] = {}
    chains: list[dict[str, Any]] = []
    for product in recheck_products:
        ref = product.get("initialProductRef") if isinstance(product, dict) else None
        if isinstance(ref, str) and ref in initials:
            linked.setdefault(ref, []).append(product)
        else:
            chains.append({"initialRef": None, "linked": False, "recheckRef": product.get("clientRef")})
    for ref, product in initials.items():
        rechecks = linked.get(ref, [])
        chains.append(
            {
                "initialRef": ref,
                "linked": True,
                "initialResult": product.get("result"),
                "recheckRefs": [p.get("clientRef") for p in rechecks],
                "recheckResults": [p.get("result") for p in rechecks],
            }
        )
    return chains


def administrative_class(chains: Iterable[dict[str, Any]]) -> str:
    """是/否/待核实 for 是否行政处罚案卷 — a business classification, not a penalty decision.

    Each chain: {"initial_result", "recheck_state", "recheck_result"}. 是 needs one proven chain
    whose initial final result and recheck valid result are both 不合格. 否 needs every chain closed
    without 是. Anything open or unlinked stays 待核实 (never defaulted to 否).
    """
    rows = list(chains)
    if not rows:
        return "待核实"
    yes = False
    open_chain = False
    for row in rows:
        if row.get("linked") is False or text(row.get("initial_result")) not in INITIAL_RESULTS:
            open_chain = True
            continue
        if text(row.get("initial_result")) == "不合格" and text(row.get("recheck_result")) == "不合格":
            yes = True
        elif text(row.get("recheck_state")) not in RECHECK_TERMINAL_STATES:
            open_chain = True
    if yes:
        return "是"
    return "待核实" if open_chain else "否"


def is_stable_case(
    *,
    identity_complete: bool,
    initial_result: str,
    recheck_state: str,
    has_new_evidence: bool = False,
) -> bool:
    """Reusable locally without re-downloading: identity fields complete, initial result final,
    recheck branch terminal and no related change evidence. No fixed expiry in days."""
    return bool(
        identity_complete
        and text(initial_result) in INITIAL_RESULTS
        and text(recheck_state) in RECHECK_TERMINAL_STATES
        and not has_new_evidence
    )
