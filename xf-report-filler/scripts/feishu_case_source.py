"""Read-only monthly case statistics from official Feishu table exports (NDJSON snapshots).

The snapshot is the only statistical case source: cases (project), checks (project + phase),
products (product rows) and monthly (external report values + Feishu derived helpers). Nothing
is written back and no attachment is read. Unknown dates, brigades or flags are listed as
coverage gaps and never counted as zero. Document counts (notices, sampling sheets, filing
approval forms) are not derivable from case rows and are reported as unavailable.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

SNAPSHOT_FILES = {
    "cases": "cases.ndjson",
    "checks": "checks.ndjson",
    "products": "products.ndjson",
    "monthly": "monthly.ndjson",
}
# (primary key column, a column that identifies the table's role)
TABLE_ROLES = {
    "cases": ("项目编号", "初查结果"),
    "checks": ("检查键", "阶段"),
    "products": ("产品键", "检查键"),
    "monthly": ("月份大队", "月份"),
}
BRIGADES = ("梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队")
ENV_SNAPSHOT_DIR = "XF_FEISHU_SNAPSHOT_DIR"
INFO_FILE = "snapshot.info.json"
INFO_FIELDS = ("source", "exportedAt", "completeness", "refreshedBy", "note")
# R02 columns B..K and the Feishu 月报_ fields that carry the brigade-reported values.
REPORTED_FIELDS = {
    "B": ("月报_检查次数",),
    "C": ("月报_责令限期改正通知书份数",),
    "D": ("月报_现场判定不合格通知书份数",),
    "E": ("月报_抽样单份数",),
    "F": ("月报_微型消防站检查次数",),
    "G": ("月报_立案审批表份数",),
    "H": ("月报_相关企业检查次数", "月报_处置力量配备检查次数"),
    "I": ("月报_网售检查总次数", "月报_网售不合格次数"),
    "J": ("月报_维修后灭火器检查总次数", "月报_维修后灭火器不合格次数"),
    "K": ("月报_加油加气站检查总次数", "月报_加油加气站不合格次数"),
}


class SnapshotError(RuntimeError):
    """The snapshot is structurally unusable; callers must stop instead of guessing."""


def default_snapshot_dir(configured: str | os.PathLike[str] | None = None) -> Path | None:
    value = os.environ.get(ENV_SNAPSHOT_DIR) or configured
    return Path(value) if value else None


def is_snapshot_source(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    return candidate.is_dir() or candidate.suffix.lower() not in {".xlsx", ".xls", ".xlsm"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as error:
                raise SnapshotError(f"{path.name} 第 {number} 行不是合法 JSON") from error
            if not isinstance(value, dict):
                raise SnapshotError(f"{path.name} 第 {number} 行不是对象")
            rows.append(value)
    return rows


def load_snapshot(directory: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and structurally validate the four statistical tables; raises SnapshotError."""
    root = Path(directory)
    if not root.is_dir():
        raise SnapshotError(f"飞书统计快照目录不存在：{root}")
    errors: list[str] = []
    tables: dict[str, list[dict[str, Any]]] = {}
    files: dict[str, Any] = {}
    for role, name in SNAPSHOT_FILES.items():
        path = root / name
        if not path.is_file():
            errors.append(f"缺少快照文件：{name}")
            continue
        rows = _rows(path)
        tables[role] = rows
        info: dict[str, Any] = {"file": name, "rows": len(rows), "sha256": _sha256(path)}
        manifest_path = root / f"{role}.manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            info["manifestRecords"] = manifest.get("records_count")
            info["rev"] = manifest.get("rev")
            if manifest.get("records_count") != len(rows):
                errors.append(
                    f"{name} 行数 {len(rows)} 与清单记录数 {manifest.get('records_count')} 不一致"
                )
            if manifest.get("has_more"):
                errors.append(f"{name} 清单显示仍有未导出分页（has_more）")
        files[role] = info
        key, marker = TABLE_ROLES[role]
        if rows and (key not in rows[0] or marker not in rows[0]):
            errors.append(f"{name} 不是预期的{role}表（缺少 {key}/{marker} 列）")
            continue
        keys = [row.get(key) for row in rows]
        if any(not isinstance(item, str) or not item for item in keys):
            errors.append(f"{name} 存在空主键 {key}")
        duplicates = sorted({item for item in keys if keys.count(item) > 1})
        if duplicates:
            errors.append(f"{name} 主键 {key} 重复：{duplicates[:5]}")
    if errors:
        raise SnapshotError("；".join(errors))

    cases = {row["record_id"]: row for row in tables["cases"]}
    checks = {row["record_id"]: row for row in tables["checks"]}
    relation_errors: list[str] = []
    for check in tables["checks"]:
        link = check.get("项目编号")
        case = cases.get(link[0].get("id")) if isinstance(link, list) and link else None
        if case is None:
            relation_errors.append(f"检查 {check['检查键']} 未关联到案卷")
        elif check["检查键"].split("|", 1)[0] != case["项目编号"]:
            relation_errors.append(f"检查 {check['检查键']} 的项目编号与关联案卷不一致")
    for product in tables["products"]:
        link = product.get("检查键")
        check = checks.get(link[0].get("id")) if isinstance(link, list) and link else None
        if check is None:
            relation_errors.append(f"产品 {product['产品键']} 未关联到检查")
        elif product["产品键"].rsplit("|", 1)[0] != check["检查键"]:
            relation_errors.append(f"产品 {product['产品键']} 的检查键与关联检查不一致")
    if relation_errors:
        raise SnapshotError("关系校验失败：" + "；".join(relation_errors[:8]))
    return {
        "directory": str(root),
        "tables": tables,
        "files": files,
        "cases": cases,
        "checks": checks,
        "provenance": _provenance(root),
    }


def _provenance(root: Path) -> dict[str, Any]:
    """Optional snapshot.info.json: where the export came from, when and how complete it is."""
    path = root / INFO_FILE
    if not path.is_file():
        return {
            "declared": False,
            "note": f"快照目录没有 {INFO_FILE}：导出来源、导出时间和完整性未声明，只能按文件清单与摘要追溯",
        }
    try:
        info = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise SnapshotError(f"{INFO_FILE} 不是合法 JSON") from error
    if not isinstance(info, dict):
        raise SnapshotError(f"{INFO_FILE} 必须是对象")
    return {"declared": True, **{k: info[k] for k in INFO_FIELDS if k in info}}


def _day(value: Any) -> date | None:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _known_result(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(item in {"合格", "不合格"} for item in values)


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _brigade(check: dict[str, Any], case: dict[str, Any] | None) -> str | None:
    for candidate in (_first(check.get("大队")), check.get("所属大队(案卷)"), (case or {}).get("所属大队")):
        if isinstance(candidate, str) and candidate in BRIGADES:
            return candidate
    return None


def _window(year: int, month: int) -> tuple[date, date]:
    start_year, start_month = (year - 1, 12) if month == 1 else (year, month - 1)
    return date(start_year, start_month, 26), date(year, month, 25)


def case_counts(snapshot: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    """Per-brigade counts for the 26th–25th reporting window plus an explicit coverage report."""
    start, end = _window(year, month)
    cases = snapshot["cases"]
    by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in snapshot["tables"]["products"]:
        link = _first(product.get("检查键"))
        if isinstance(link, dict):
            by_check[link["id"]].append(product)

    counts: dict[str, dict[str, Any]] = {
        name: {
            "initial_checks": 0,
            "unique_projects": 0,
            "unqualified_checks": 0,
            "unqualified_projects": 0,
            "rechecks_excluded": 0,
            "product_rows": 0,
            "unqualified_product_rows": 0,
            "micro_checks_confirmed": 0,
            "micro_checks_unknown": 0,
            "result_unknown_checks": 0,
            "result_unknown_projects": 0,
            "product_result_unknown_rows": 0,
            "_result_unknown_projects": set(),
            "sampling_checks": 0,
            "onsite_checks": 0,
            "date_unknown_initial_checks": 0,
            "cases_without_checks": 0,
            "cases_without_dated_initial": 0,
            "_projects": set(),
            "_unqualified_projects": set(),
        }
        for name in BRIGADES
    }
    gaps: list[dict[str, Any]] = []
    for check in snapshot["tables"]["checks"]:
        link = _first(check.get("项目编号"))
        case = cases.get(link["id"]) if isinstance(link, dict) else None
        key = check["检查键"]
        stage = _first(check.get("阶段"))
        brigade = _brigade(check, case)
        checked = _day(check.get("检查日期"))
        if stage not in {"初查", "复查"}:
            gaps.append({"type": "stage_unknown", "checkKey": key, "stage": stage})
            continue
        if brigade is None:
            gaps.append({"type": "brigade_unknown", "checkKey": key, "stage": stage})
        if checked is None:
            gaps.append({"type": "check_date_unknown", "checkKey": key, "stage": stage,
                         "brigade": brigade})
            if stage == "初查" and brigade:
                counts[brigade]["date_unknown_initial_checks"] += 1
            continue
        if not start <= checked <= end:
            continue
        if stage == "复查":
            if brigade:
                counts[brigade]["rechecks_excluded"] += 1
            continue
        if brigade is None:
            continue
        item = counts[brigade]
        project = key.split("|", 1)[0]
        item["initial_checks"] += 1
        item["_projects"].add(project)
        failed = "不合格" in (check.get("检查结果") or [])
        if failed:
            item["unqualified_checks"] += 1
            item["_unqualified_projects"].add(project)
        if not _known_result(check.get("检查结果")):
            # Missing outcome is unknown, never "no": unqualified_* is a lower bound only.
            item["result_unknown_checks"] += 1
            item["_result_unknown_projects"].add(project)
            gaps.append({"type": "check_result_unknown", "checkKey": key, "brigade": brigade})
        products = by_check.get(check["record_id"], [])
        item["product_result_unknown_rows"] += sum(
            1 for row in products if not _known_result(row.get("检查结果"))
        )
        item["product_rows"] += len(products)
        item["unqualified_product_rows"] += sum(
            1 for row in products if "不合格" in (row.get("检查结果") or [])
        )
        declared = check.get("产品数")
        if isinstance(declared, int) and declared != len(products):
            gaps.append({"type": "product_rows_mismatch", "checkKey": key,
                         "declared": declared, "linked": len(products)})
        micro = check.get("案卷是否微型消防站")
        if micro == "是":
            item["micro_checks_confirmed"] += 1
        elif micro != "否":
            item["micro_checks_unknown"] += 1
        methods = check.get("检查方式") or []
        item["sampling_checks"] += "抽样送检" in methods
        item["onsite_checks"] += "现场判定" in methods
        reported_month = check.get("通报月份")
        if reported_month and reported_month != f"{year:04d}-{month:02d}":
            gaps.append({"type": "report_month_field_differs", "checkKey": key,
                         "field": reported_month, "windowMonth": f"{year:04d}-{month:02d}"})
    # Coverage: a case with no dated initial check might still belong to this window (its
    # check/date/brigade is simply not in the snapshot). Such cases make the confirmed counts a
    # lower bound only; they never turn into "0" or an upper bound.
    dated_initial: set[str] = set()
    has_check: set[str] = set()
    for check in snapshot["tables"]["checks"]:
        link = _first(check.get("项目编号"))
        if not isinstance(link, dict):
            continue
        has_check.add(link["id"])
        if _first(check.get("阶段")) == "初查" and _day(check.get("检查日期")) is not None:
            dated_initial.add(link["id"])
    unattributed = 0
    for record_id, case in cases.items():
        if record_id in dated_initial:
            continue
        brigade = case.get("所属大队") if case.get("所属大队") in BRIGADES else None
        if brigade is None:
            unattributed += 1
            continue
        counts[brigade]["cases_without_dated_initial"] += 1
        if record_id not in has_check:
            counts[brigade]["cases_without_checks"] += 1
    for item in counts.values():
        item["unattributed_unresolved_cases"] = unattributed
        item["unresolved_initial_cases"] = item["cases_without_dated_initial"] + unattributed
    totals = {
        "cases": len(cases),
        "cases_without_checks": sum(i["cases_without_checks"] for i in counts.values())
        + sum(1 for r, c in cases.items() if r not in has_check and c.get("所属大队") not in BRIGADES),
        "cases_without_dated_initial": sum(i["cases_without_dated_initial"] for i in counts.values())
        + unattributed,
    }
    for item in counts.values():
        item["unique_projects"] = len(item.pop("_projects"))
        item["unqualified_projects"] = len(item.pop("_unqualified_projects"))
        item["result_unknown_projects"] = len(item.pop("_result_unknown_projects"))
    for case in snapshot["tables"]["cases"]:
        if case.get("所属大队") not in BRIGADES:
            gaps.append({"type": "case_brigade_unknown", "projectNo": case["项目编号"]})
    return {
        "counts": counts,
        "gaps": gaps,
        "window": [str(start), str(end)],
        "coverage": totals,
    }


def reported_values(snapshot: dict[str, Any], year: int, month: int) -> dict[str, dict[str, Any]]:
    """Brigade-reported (外部月报) values from the monthly table; read only, never derived."""
    label = f"{year:04d}-{month:02d}"
    result: dict[str, dict[str, Any]] = {}
    for row in snapshot["tables"]["monthly"]:
        brigade = _first(row.get("大队"))
        if row.get("月份") != label or brigade not in BRIGADES:
            continue
        result[brigade] = {
            column: [row.get(field) for field in fields] for column, fields in REPORTED_FIELDS.items()
        }
    return result


def coverage_summary(
    snapshot: dict[str, Any], gaps: list[dict[str, Any]], totals: dict[str, int] | None = None
) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    for gap in gaps:
        by_type[gap["type"]] += 1
    return {
        "kind": "feishu_snapshot",
        "directory": snapshot["directory"],
        "files": snapshot["files"],
        "gapCounts": dict(sorted(by_type.items())),
        "caseCoverage": totals or {},
        "provenance": snapshot.get("provenance", {"declared": False}),
        "documentCountsAvailable": False,
        "note": "文书份数（责令限期改正、现场判定不合格、抽样单、立案审批表）不在案卷快照内，需人工核对；"
        "日期/大队/标记未知的记录单独列出，不按 0 处理。",
    }
