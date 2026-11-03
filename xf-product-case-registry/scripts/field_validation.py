"""Read-only validation of extracted case fields (merged from the retired xf-product-case-filler).

Rows are identified by the stable (project number, inspection phase) pair. The validator never
mutates its input, never renames or deletes images, and never writes a workbook. Manual decisions
listed in ``manual_fields`` are protected: they are reported, not overridden or flagged as errors.
"""

from __future__ import annotations

import copy
import json
import re
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
    "qualified": {"合格", "不合格"},
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


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _manual(case: dict[str, Any]) -> set[str]:
    value = case.get("manual_fields")
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def validate_cases(
    cases: Iterable[dict[str, Any]], image_dir: Path | None = None
) -> dict[str, Any]:
    """Return a report; ``cases`` is deep-copied so callers' objects are never modified."""
    rows = copy.deepcopy(list(cases))
    errors: list[str] = []
    warnings: list[str] = []
    unknown: list[dict[str, str]] = []
    protected: list[dict[str, str]] = []
    seen: set[tuple[str, str, int]] = set()
    by_project: dict[str, list[dict[str, Any]]] = {}

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
        for field in TRI_STATE_FIELDS:
            if not text(case.get(field)):
                unknown.append({"projectNo": project, "phase": phase, "field": field})

        date_value = text(case.get("inspection_date"))
        if date_value and not (FULL_DATE.match(date_value) or CN_DATE.match(date_value)):
            if MONTH.match(date_value):
                warnings.append(f"{label} inspection_date 仍是月份，不能当作完整日期：{date_value}")
            else:
                warnings.append(f"{label} inspection_date 不是可识别完整日期：{date_value}")
        if not date_value:
            unknown.append({"projectNo": project, "phase": phase, "field": "inspection_date"})
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
                errors.append(f"{project}: 只有初查不合格才允许生成复查行")
            elif (
                text(recheck.get("inspection_date"))
                and text(recheck.get("inspection_date")) == text(initial.get("inspection_date"))
                and "inspection_date" not in _manual(recheck)
                and not text(recheck.get("inspection_date_source"))
            ):
                errors.append(f"{project}: 复查日期与初查日期相同且无独立来源，疑似复制初查日期")
        failed_recheck = recheck is not None and text(recheck.get("qualified")) == "不合格"
        for case in group:
            if text(case.get("case_type")) == "行案" and not failed_recheck:
                if "case_type" in _manual(case):
                    warnings.append(
                        f"{project}/{phase_of(case)}: 行案为人工裁决，保留；系统规则不会据此改值"
                    )
                else:
                    errors.append(
                        f"{project}/{phase_of(case)}: 只有复查不合格项目才允许 case_type 写 行案"
                    )

    return {
        "status": "ok" if not errors else "errors_found",
        "caseCount": len(rows),
        "errors": errors,
        "warnings": warnings,
        "unknownFields": unknown,
        "protectedManualFields": protected,
        "inputModified": False,
    }
