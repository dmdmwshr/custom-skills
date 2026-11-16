import argparse
import json
import re
import sys
from pathlib import Path

import xlrd
from openpyxl import load_workbook

import feishu_case_source as snapshot_source
import monthly_grade_register as grade_register
import monthly_workflow as workflow
import staff_roster
import template_resolver


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def first_existing(paths):
    for path in paths:
        path = Path(path)
        if path.exists():
            return path
    return None


def find_product_register(score_dir, year, month):
    score_dir = Path(score_dir)
    candidates = sorted(score_dir.glob(f"*{month}月*产品巡查底册*.docx")) + sorted(score_dir.glob("*产品巡查底册*.docx"))
    return candidates[0] if candidates else None


def find_network_dir(score_dir, year, month):
    return workflow.find_monitor_directory(score_dir, year, month)


def find_base_info(score_dir, network_dir, year, month):
    score_dir = Path(score_dir)
    candidates = [
        score_dir / f"{year}年{month}月基础信息考评截图（不发）.xls",
        score_dir / f"{month}月基础信息考评截图（不发）.xls",
        score_dir / f"【待补】{month}月基础信息考评截图（不发）.xls",
    ]
    if network_dir:
        candidates.append(Path(network_dir) / f"{month}月基础信息考评截图.xls")
    found = first_existing(candidates)
    if found:
        return found
    nested = sorted(score_dir.glob("**/*基础信息考评截图*.xls"))
    return nested[0] if nested else None


def build_grade_outputs(score_dir, year, month, config):
    score_dir = Path(score_dir)
    outputs = {}
    for item in workflow.grade_outputs(config):
        if item.get("requires_flag"):
            continue
        outputs[item["key"]] = str(score_dir / item["target"].format(year=year, month=month))
    return outputs


def existing_status(path):
    path = Path(path)
    return {"path": str(path), "exists": path.exists(), "is_dir": path.is_dir() if path.exists() else False}


def scan_pending_text(path, marker="【待补】"):
    path = Path(path)
    if not path.exists() or path.is_dir():
        return []
    hits = []
    if path.suffix.lower() == ".xlsx":
        workbook = load_workbook(path, data_only=False, read_only=True)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if marker in str(cell.value or ""):
                        hits.append({"path": str(path), "sheet": sheet.title, "cell": cell.coordinate, "value": str(cell.value)})
    elif path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(str(path))
        for sheet in book.sheets():
            for row in range(sheet.nrows):
                for col in range(sheet.ncols):
                    value = sheet.cell_value(row, col)
                    if marker in str(value or ""):
                        hits.append({"path": str(path), "sheet": sheet.name, "row": row + 1, "column": col + 1, "value": str(value)})
    return hits


def adopted_template_path(template_result, key):
    for status in template_result["statuses"]:
        if status["key"] == key and status.get("adopted_path"):
            return Path(status["adopted_path"])
    return None


STAFF_BRACKET = re.compile(r"[（(]\s*(\d+)\s*人?\s*[）)]")
STAFF_BRIGADES = ["梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队"]


def read_stats_headcounts(path):
    """Column-B bracket numbers of an R02 statistics workbook: {brigade: N or None}; read-only."""
    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    result = {}
    for row in range(sheet.nrows):
        name = str(sheet.cell_value(row, 0) or "").strip()
        name = re.sub(r"\d+人$", "", name)
        if name in STAFF_BRIGADES:
            match = STAFF_BRACKET.search(str(sheet.cell_value(row, 1) or ""))
            result[name] = int(match.group(1)) if match else None
    return result


def staff_roster_consistency(args, config, template_result, bulletin_dir):
    """Compare the roster's name counts with the R02 template, the skill snapshot, this month's R02 and optional history files.

    Differences are only reported (never edited). History files are read only and are compared with the *current*
    roster, so a difference there is not an error of that month.
    """
    entry = config["data_sources"]["staff_roster"]
    roster_path = Path(args.roster) if getattr(args, "roster", None) else staff_roster.resolve_path(entry["path"], args.bulletin_year)
    try:
        roster = staff_roster.read_roster(roster_path, entry.get("sheet", "Sheet1"), entry.get("group_label_pattern", staff_roster.DEFAULT_LABEL_PATTERN))
    except staff_roster.RosterError as error:
        return {"ok": False, "error": str(error)}, [{"code": "staff_roster_unreadable", "message": str(error), "path": str(roster_path)}], []
    blockers = [{"code": item["code"], "message": item["message"], "brigade": item.get("brigade")} for item in roster["blockers"]]
    warnings = [{"type": item["code"], **item} for item in roster["warnings"] + roster["info"]]
    targets = {}
    t09 = next((st for st in template_result["statuses"] if st["id"] == "T09"), None)
    if t09 and t09["external_exists"]:
        targets["external_template_T09"] = t09["external_path"]
    if t09 and t09["snapshot_exists"]:
        targets["skill_snapshot_T09"] = t09["snapshot_path"]
    for pending in (True, False):
        candidate = Path(bulletin_dir) / workflow.root_file_name(next(i for i in workflow.bulletin_root_files(config) if i["key"] == "product_stats"), args.bulletin_year, args.bulletin_month, pending=pending, config=config)
        if candidate.exists():
            targets["this_month_R02"] = str(candidate)
            break
    for index, extra in enumerate(getattr(args, "history_r02", None) or [], 1):
        targets[f"history_{index}:{Path(extra).name}"] = str(extra)
    differences = []
    for label, path in targets.items():
        try:
            found = read_stats_headcounts(path)
        except Exception as error:  # an unreadable comparison file must not hide the rest of the audit
            warnings.append({"type": "staff_headcount_unreadable", "message": f"{label} 无法读取，未比对：{error}", "path": path})
            continue
        for brigade in STAFF_BRIGADES:
            expected = roster["counts"].get(brigade)
            if expected is None or found.get(brigade) == expected:
                continue
            differences.append({"target": label, "path": path, "brigade": brigade, "roster": expected, "found": found.get(brigade),
                                "historical_read_only": label.startswith("history_")})
    for item in differences:
        if not item["historical_read_only"]:
            warnings.append({"type": "staff_headcount_differs", "message": f"{item['target']} 的 {item['brigade']} B 列人数 {item['found']} 与人员总表 {item['roster']} 不一致（只报告，不改）", **item})
    return {"ok": not blockers, "source": roster["source"], "sha256": roster["sha256"], "counts": roster["counts"],
            "outside_table": roster["outside_table"], "targets": targets, "differences": differences}, blockers, warnings


def run(args):
    config = workflow.load_config()
    bulletin_dir = Path(args.bulletin_dir)
    score_dir = bulletin_dir / workflow.score_dir_name(args.score_month, config)
    template_result = template_resolver.resolve_templates(
        template_dir=args.template_dir,
        include_reserved=True,
        allow_snapshot_fallback=True,
        config=config,
    )

    product_register = find_product_register(score_dir, args.score_year, args.score_month)
    source_blockers = []
    try:
        network_dir = find_network_dir(score_dir, args.score_year, args.score_month)
    except ValueError as exc:
        network_dir = None
        source_blockers.append({'message': str(exc), 'path': str(score_dir)})
    network_stats = Path(network_dir) / "联网监测统计表.xls" if network_dir else None
    base_info = find_base_info(score_dir, network_dir, args.score_year, args.score_month)
    work_plan = Path(args.work_plan) if args.work_plan else workflow.data_source_path("work_plan", config)
    case_data = Path(args.case_data) if args.case_data else workflow.case_source_path(config)
    case_unconfigured = case_data is None
    if case_unconfigured:
        case_data = Path("(未配置飞书统计快照)")

    root_files = {}
    pending_text_hits = []
    for item in workflow.bulletin_root_map(config):
        normal = bulletin_dir / workflow.root_file_name(item, args.bulletin_year, args.bulletin_month, pending=False, config=config)
        pending = bulletin_dir / workflow.root_file_name(item, args.bulletin_year, args.bulletin_month, pending=True, config=config)
        root_files[item["key"]] = {
            "id": item["id"],
            "normal": existing_status(normal),
            "pending": existing_status(pending),
        }
        for path in [normal, pending]:
            if path.exists():
                pending_text_hits.extend(scan_pending_text(path))

    grade_outputs = {
        key: existing_status(path)
        for key, path in build_grade_outputs(score_dir, args.score_year, args.score_month, config).items()
    }

    blockers = source_blockers
    warnings = list(template_result["warnings"])
    warnings.extend(
        {
            "message": "外部模板与 skill 快照不一致，生成会采用外部模板。",
            "template_id": status["id"],
            "file": status["file"],
            "external_path": status["external_path"],
            "snapshot_path": status["snapshot_path"],
        }
        for status in template_result["statuses"]
        if status["external_exists"] and status["snapshot_exists"] and not status["match"]
    )
    for label, path in [
        ("通报月份目录", bulletin_dir),
        ("成绩月份巡查目录", score_dir),
        ("产品巡查底册", product_register),
        ("联网监测明细目录", network_dir),
        ("联网监测统计表", network_stats),
        ("基础信息考评截图", base_info),
        ("工作计划", work_plan),
        ("飞书统计快照（或历史案卷数据）", case_data),
    ]:
        if path is case_data and case_unconfigured:
            blockers.append({"message": workflow.case_source_unconfigured_message(config)})
        elif not path or not Path(path).exists():
            blockers.append({"message": f"{label}不存在", "path": str(path) if path else ""})

    case_source_status = None
    if Path(case_data).exists() and snapshot_source.is_snapshot_source(case_data):
        try:
            loaded = snapshot_source.load_snapshot(case_data)
            case_source_status = {"ok": True, "files": loaded["files"]}
        except snapshot_source.SnapshotError as exc:
            case_source_status = {"ok": False, "error": str(exc)}
            blockers.append({"message": "飞书统计快照结构校验失败", "path": str(case_data), "error": str(exc)})

    for hit in pending_text_hits:
        warnings.append({"message": "根层表仍包含单元格文本【待补】，应改为红底标记并保留/预填业务值。", **hit})

    product_detail_leaks = []
    person_match_issues = []
    if product_register and Path(product_register).exists():
        try:
            product_records = grade_register.parse_product_register(product_register)
            product_detail_leaks = grade_register.product_detail_leak_issues_for_records(product_records)
            if network_stats and Path(network_stats).exists() and base_info and Path(base_info).exists():
                personal_template = adopted_template_path(template_result, "personal_stats")
                if personal_template and personal_template.exists():
                    monitor_scores = grade_register.read_monitor_scores(network_stats)
                    monitor_details = grade_register.read_monitor_details(base_info, monitor_scores)
                    person_match_issues = grade_register.collect_person_match_issues(personal_template, product_records, monitor_details)
        except Exception as exc:
            warnings.append({"message": "审计产品/个人统计风险时失败", "error": str(exc)})
    warnings.extend(product_detail_leaks)
    warnings.extend(person_match_issues)

    staff_report, staff_blockers, staff_warnings = staff_roster_consistency(args, config, template_result, bulletin_dir)
    # Roster problems are reported here as warnings; the root stage itself refuses to write column B without a clean roster.
    warnings.extend({"type": "staff_roster_issue", **item} for item in staff_blockers)
    warnings.extend(staff_warnings)

    all_blockers = blockers + template_result["blockers"]
    return {
        "ok": not all_blockers,
        "bulletin": {
            "dir": str(bulletin_dir),
            "year": args.bulletin_year,
            "month": args.bulletin_month,
            "root_files": root_files,
            "pending_text_hits": pending_text_hits,
        },
        "score": {
            "dir": str(score_dir),
            "year": args.score_year,
            "month": args.score_month,
            "input_files": {
                "product_register": str(product_register) if product_register else None,
                "network_dir": str(network_dir) if network_dir else None,
                "network_stats": str(network_stats) if network_stats else None,
                "base_info": str(base_info) if base_info else None,
            },
            "outputs": grade_outputs,
        },
        "staff_roster": staff_report,
        "data_sources": {
            "work_plan": existing_status(work_plan),
            "case_data": existing_status(case_data),
            "case_snapshot": case_source_status,
        },
        "templates": {
            "policy": "external_template_source_is_authoritative; skill_snapshot_is_for_hash_verification",
            "statuses": template_result["statuses"],
        },
        "warnings": warnings,
        "blockers": all_blockers,
    }


def main():
    parser = argparse.ArgumentParser(
        description="只读审计月度通报目录、数据源、模板版本和目标成品状态。",
        epilog="参考：references/monthly/00_workflow_router.md；审计规则见 references/monthly/validation_and_audit.md。",
    )
    parser.add_argument("--bulletin-dir", required=True)
    parser.add_argument("--bulletin-year", type=int, required=True)
    parser.add_argument("--bulletin-month", type=int, required=True)
    parser.add_argument("--score-year", type=int, required=True)
    parser.add_argument("--score-month", type=int, required=True)
    parser.add_argument("--template-dir", default=str(workflow.template_root()))
    parser.add_argument("--work-plan")
    parser.add_argument("--roster", help="大队执法人员总表；缺省按配置和通报年份推导")
    parser.add_argument("--history-r02", action="append", help="只读：历史月份的消防产品监督统计表，列出其 B 列人数与当前名册的差异（可重复）")
    parser.add_argument("--case-data", help="飞书统计快照目录（默认）或历史 产品案卷数据.xlsx")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
