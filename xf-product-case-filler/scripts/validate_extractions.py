#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_KEYS = [
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
]

ALLOWED_VALUES = {
    "station_or_team": {"微型消防站", "快速处置队", "否"},
    "method": {"现场判定", "抽样送检"},
    "qualified": {"合格", "不合格"},
    "case_type": {"刑案", "行案", "否"},
    "online_sale": {"是", "否"},
}

ROLE_VALUES = {"案卷清单", "检查记录表", "其他附件"}
FULL_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
CN_DATE_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$")
MONTH_RE = re.compile(r"^\d{1,2}月$")


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    else:
        raise ValueError("抽取文件必须是案件数组，或包含 cases 数组的对象。")
    if not all(isinstance(item, dict) for item in cases):
        raise ValueError("cases 中每一项都必须是对象。")
    return cases


def _check_file_exists(image_dir: Path | None, file_name: str) -> bool:
    path = Path(file_name)
    if not path.is_absolute() and image_dir is not None:
        path = image_dir / file_name
    return path.exists()


def validate_cases(
    cases: list[dict[str, Any]], image_dir: Path | None = None
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_projects: set[str] = set()

    for index, case in enumerate(cases, start=1):
        prefix = f"第 {index} 个案件"
        for key in REQUIRED_KEYS:
            if key not in case:
                errors.append(f"{prefix} 缺少字段：{key}")

        project_no = text(case.get("project_no"))
        if not project_no:
            errors.append(f"{prefix} 缺少项目编号 project_no")
        elif project_no in seen_projects:
            errors.append(f"{prefix} 项目编号重复：{project_no}")
        seen_projects.add(project_no)

        products = case.get("products")
        if not isinstance(products, list) or not all(isinstance(v, str) for v in products):
            errors.append(f"{prefix} products 必须是字符串数组")
        elif not [v for v in products if v.strip()]:
            warnings.append(f"{prefix} products 为空，写表时消防产品会留空标红")

        nominal_producers = case.get("nominal_producers")
        if not isinstance(nominal_producers, list) or not all(isinstance(v, str) for v in nominal_producers):
            errors.append(f"{prefix} nominal_producers 必须是字符串数组")
        elif not [v for v in nominal_producers if v.strip()]:
            warnings.append(f"{prefix} nominal_producers 为空，写表时标称生产者会留空标红")
        elif isinstance(products, list) and len([v for v in products if text(v)]) != len([v for v in nominal_producers if text(v)]):
            warnings.append(f"{prefix} products 与 nominal_producers 数量不一致，请人工核对换行对应关系")

        source_files = case.get("source_files")
        if not isinstance(source_files, list) or not all(isinstance(v, str) for v in source_files):
            errors.append(f"{prefix} source_files 必须是字符串数组")
            source_files = []
        elif not source_files:
            errors.append(f"{prefix} source_files 不能为空")

        file_roles = case.get("file_roles")
        if not isinstance(file_roles, dict):
            errors.append(f"{prefix} file_roles 必须是对象")
            file_roles = {}

        for field, allowed in ALLOWED_VALUES.items():
            value = text(case.get(field))
            if value and value not in allowed:
                errors.append(f"{prefix} {field} 值不在允许范围内：{value}")

        date_value = text(case.get("inspection_date"))
        if date_value and not (
            FULL_DATE_RE.match(date_value)
            or CN_DATE_RE.match(date_value)
            or MONTH_RE.match(date_value)
        ):
            warnings.append(f"{prefix} inspection_date 不是可识别日期或月份：{date_value}")
        if not date_value:
            warnings.append(f"{prefix} inspection_date 为空，截图重命名将使用 未知日期")

        missing_fields = case.get("missing_fields")
        if not isinstance(missing_fields, list) or not all(isinstance(v, str) for v in missing_fields):
            errors.append(f"{prefix} missing_fields 必须是字符串数组")

        if "case_type_review" in case and not isinstance(case.get("case_type_review"), bool):
            errors.append(f"{prefix} case_type_review 必须是布尔值")

        source_set = set(source_files if isinstance(source_files, list) else [])
        for file_name in source_set:
            if image_dir is not None and not _check_file_exists(image_dir, file_name):
                errors.append(f"{prefix} source_files 中的文件不存在：{file_name}")

        for file_name, role in file_roles.items():
            if file_name not in source_set:
                warnings.append(f"{prefix} file_roles 包含未列入 source_files 的文件：{file_name}")
            if role not in ROLE_VALUES:
                errors.append(f"{prefix} file_roles 的角色不合法：{file_name}={role}")

    return {
        "status": "ok" if not errors else "errors_found",
        "case_count": len(cases),
        "errors": errors,
        "warnings": warnings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验消防产品案卷抽取 JSON。")
    parser.add_argument("extractions", type=Path, help="抽取结果 JSON 文件")
    parser.add_argument("--image-dir", type=Path, help="截图目录，用于校验 source_files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cases = load_cases(args.extractions)
        result = validate_cases(cases, args.image_dir)
    except Exception as exc:
        result = {"status": "errors_found", "case_count": 0, "errors": [str(exc)], "warnings": []}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
