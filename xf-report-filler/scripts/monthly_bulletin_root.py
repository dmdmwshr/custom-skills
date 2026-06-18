import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import xlrd
from openpyxl import load_workbook
from openpyxl.styles import Font

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


DEFAULT_WORK_ROOT = Path(r"E:\文件夹\1、工作\2、产品，科技，联网检测")
DEFAULT_TEMPLATE_DIR = DEFAULT_WORK_ROOT / "1、产品监督" / "模板文件"
DEFAULT_WORK_PLAN = DEFAULT_WORK_ROOT / "2026年-产品、科技工作计划.xls"
DEFAULT_CASE_DATA = DEFAULT_WORK_ROOT / "1、产品监督" / "26年" / "26年产品案卷信息收集" / "产品案卷数据.xlsx"

PENDING_PREFIX = "【待补】"
RED_RGB = "FFFF0000"
BLACK_RGB = "FF000000"

ROOT_FILES = [
    {
        "key": "office_record",
        "skeleton": "X月科室月考核情况记录表.xlsx",
        "target": "{year}年{month}月科室月考核情况记录表.xlsx",
    },
    {
        "key": "product_stats",
        "skeleton": "X月消防产品监督统计表.xls",
        "target": "{year}年{month}月消防产品监督统计表.xls",
    },
    {
        "key": "work_report",
        "skeleton": "X月重点工作完成情况上报表（应急通信与消防科技）.xls",
        "target": "{year}年{month}月重点工作完成情况上报表（应急通信与消防科技）.xls",
        "numbered": "10_重点工作完成情况上报表（应急通信与消防科技）模板.xls",
    },
]

BRIGADE_ORDER = ["梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队"]
BRIGADE_ALIASES = {"轨交大队": "轨道交通大队"}
MONTH_HEADERS = {
    1: "一月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "十一月",
    12: "十二月",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def is_under(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    return path == root or root in path.parents


def add_blocker(blockers, message, **extra):
    item = {"message": message}
    item.update({key: str(value) for key, value in extra.items()})
    blockers.append(item)


def normalize_brigade(name):
    text = str(name or "").strip()
    text = re.sub(r"\d+人$", "", text)
    return BRIGADE_ALIASES.get(text, text)


def parse_staff_count(name):
    match = re.search(r"(\d+)人", str(name or ""))
    return int(match.group(1)) if match else None


def previous_month_window(year, month):
    start_year, start_month = (year - 1, 12) if month == 1 else (year, month - 1)
    return date(start_year, start_month, 26), date(year, month, 25)


def root_file_name(item, year, month, pending=False):
    name = item["target"].format(year=year, month=month)
    return f"{PENDING_PREFIX}{name}" if pending else name


def root_file_paths(bulletin_dir, year, month):
    bulletin_dir = Path(bulletin_dir)
    return {
        item["key"]: {
            "normal": bulletin_dir / root_file_name(item, year, month, pending=False),
            "pending": bulletin_dir / root_file_name(item, year, month, pending=True),
        }
        for item in ROOT_FILES
    }


def product_stats_pending_value(staff_count):
    return f"{PENDING_PREFIX}（{staff_count}）"


def work_report_pending_cells():
    return [f"K{row}" for row in range(3, 11)] + [f"{col}{row}" for row in range(3, 11) for col in ["O", "P", "Q", "R"]]


def skeleton_path(template_dir, item):
    return Path(template_dir) / "X月通报" / item["skeleton"]


def numbered_template_path(template_dir, item):
    if not item.get("numbered"):
        return None
    return Path(template_dir) / "_编号模板库" / item["numbered"]


def read_staff_counts(work_plan):
    book = xlrd.open_workbook(str(work_plan), formatting_info=True)
    sheet = book.sheet_by_name("任务总表")
    counts = {}
    for row in range(1, sheet.nrows):
        brigade = normalize_brigade(sheet.cell_value(row, 0))
        staff = parse_staff_count(sheet.cell_value(row, 0))
        if brigade and staff:
            counts[brigade] = staff
    return counts


def _xlrd_rgb(book, index):
    return book.colour_map.get(index)


def _is_yellow(rgb):
    return rgb in {(255, 255, 0), (255, 255, 153)}


def parse_numbered_tasks(text):
    raw = str(text or "").strip()
    if not raw:
        return []
    tasks = []
    for line in raw.splitlines():
        line = line.strip()
        line = re.sub(r"^\d+[、.．]\s*", "", line)
        if line:
            tasks.append(line)
    return tasks


def extract_red_text_map(work_plan, sheet_name, cells, blockers):
    if not cells:
        return {}
    try:
        import win32com.client
    except Exception as exc:
        add_blocker(blockers, "无法读取工作计划富文本红字", error=exc)
        return {}

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    result = {}
    try:
        workbook = excel.Workbooks.Open(str(Path(work_plan).resolve()), ReadOnly=True)
        try:
            sheet = workbook.Worksheets(sheet_name)
            for cell_ref in cells:
                cell = sheet.Range(cell_ref)
                text = str(cell.Value or "")
                red_chars = []
                for index, char in enumerate(text, start=1):
                    try:
                        if cell.Characters(index, 1).Font.Color == 255:
                            red_chars.append(char)
                    except Exception:
                        red_chars = []
                        break
                red_text = "".join(red_chars).strip()
                if red_text:
                    result[cell_ref] = red_text
        finally:
            workbook.Close(SaveChanges=False)
    finally:
        excel.Quit()
    return result


def read_work_plan_month_tasks(work_plan, month, blockers=None):
    blockers = blockers if blockers is not None else []
    book = xlrd.open_workbook(str(work_plan), formatting_info=True)
    sheet = book.sheet_by_name("任务总表")
    header = MONTH_HEADERS[month]
    month_col = None
    for col in range(sheet.ncols):
        if sheet.cell_value(0, col) == header:
            month_col = col
            break
    if month_col is None:
        add_blocker(blockers, "工作计划缺少月份列", month=month, header=header)
        return {}

    candidates = {}
    for row in range(1, sheet.nrows):
        brigade = normalize_brigade(sheet.cell_value(row, 0))
        if not brigade:
            continue
        xf = book.xf_list[sheet.cell_xf_index(row, month_col)]
        rgb = _xlrd_rgb(book, xf.background.pattern_colour_index)
        if not _is_yellow(rgb):
            candidates[brigade] = []
            continue
        cell_ref = f"{xlrd.formula.colname(month_col)}{row + 1}"
        candidates[brigade] = {"cell": cell_ref, "text": str(sheet.cell_value(row, month_col) or "")}

    red_map = extract_red_text_map(work_plan, "任务总表", [value["cell"] for value in candidates.values() if isinstance(value, dict)], blockers)
    tasks = {}
    for brigade, value in candidates.items():
        if isinstance(value, list):
            tasks[brigade] = []
            continue
        text = red_map.get(value["cell"]) or value["text"]
        tasks[brigade] = parse_numbered_tasks(text)
    return tasks


def read_case_counts(case_data, year, month):
    start, end = previous_month_window(year, month)
    workbook = load_workbook(case_data, read_only=True, data_only=True)
    result = {}
    for sheet in workbook.worksheets:
        brigade = normalize_brigade(sheet.title)
        if brigade not in BRIGADE_ORDER:
            continue
        rows = 0
        unique_projects = set()
        sample_rows = 0
        onsite_rows = 0
        micro_rows = 0
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if not any(values):
                continue
            raw_date = values[8] if len(values) > 8 else None
            check_date = normalize_date(raw_date)
            if not check_date or not (start <= check_date <= end):
                continue
            rows += 1
            if len(values) > 2 and values[2]:
                unique_projects.add(str(values[2]).strip())
            if len(values) > 10 and values[10] and "微型" in str(values[10]):
                micro_rows += 1
            if len(values) > 11 and values[11] and "抽样" in str(values[11]):
                sample_rows += 1
            if len(values) > 11 and values[11] and "现场" in str(values[11]):
                onsite_rows += 1
        result[brigade] = {
            "rows": rows,
            "unique_projects": len(unique_projects),
            "sample_rows": sample_rows,
            "onsite_rows": onsite_rows,
            "micro_rows": micro_rows,
        }
    return result


def normalize_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def parse_stat_count(value):
    match = re.search(r"(\d+)\s*次", str(value or ""))
    return int(match.group(1)) if match else None


def read_product_stats(path):
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    stats = {}
    for row in range(sheet.nrows):
        brigade = normalize_brigade(sheet.cell_value(row, 0))
        if brigade in BRIGADE_ORDER:
            stats[brigade] = {"row": row + 1, "value": sheet.cell_value(row, 1), "count": parse_stat_count(sheet.cell_value(row, 1))}
    return stats


def product_timeliness_text(tasks, required_count=None, actual_count=None):
    issues = list(tasks or [])
    if required_count is not None and actual_count is not None and actual_count < required_count:
        issues.append(f"应完成{required_count}起案件实际完成{actual_count}起")
    if not issues:
        return "\\"
    lines = ["应完成但还未完成的："]
    lines.extend(f"{index}、{item}；" for index, item in enumerate(issues, start=1))
    lines[-1] = lines[-1].rstrip("；") + "。"
    return "\n".join(lines)


def audit_product_stats(stats, case_counts):
    blockers = []
    for brigade in BRIGADE_ORDER:
        if brigade not in stats:
            add_blocker(blockers, "消防产品监督统计表缺少大队行", brigade=brigade)
            continue
        actual = stats[brigade]["count"]
        expected = case_counts.get(brigade, {}).get("unique_projects")
        if actual is None:
            add_blocker(blockers, "消防产品监督统计表未填写完成次数", brigade=brigade, value=stats[brigade]["value"])
        elif expected is not None and actual != expected:
            add_blocker(blockers, "消防产品监督统计表与案卷数据不一致", brigade=brigade, stat_count=actual, case_count=expected)
    return blockers


def pending_copy_roots(bulletin_dir, template_dir, year, month, actions, blockers, apply):
    bulletin_dir = Path(bulletin_dir)
    template_dir = Path(template_dir)
    bulletin_dir.mkdir(parents=True, exist_ok=True) if apply else None
    for item in ROOT_FILES:
        source = skeleton_path(template_dir, item)
        target = bulletin_dir / root_file_name(item, year, month, pending=True)
        normal = bulletin_dir / root_file_name(item, year, month, pending=False)
        if not source.exists():
            add_blocker(blockers, "通报骨架文件不存在", path=source)
            continue
        actions.append(
            {
                "kind": "copy_pending_root_file",
                "status": "planned" if not apply else "done",
                "src": str(source),
                "dst": str(target),
                "sha256": sha256_file(source),
            }
        )
        if apply:
            shutil.copy2(source, target)
        if normal.exists():
            actions.append({"kind": "remove_unprefixed_root_file", "status": "planned" if not apply else "done", "path": str(normal)})
            if apply:
                normal.unlink()


def sync_work_report_skeleton(template_dir, actions, blockers, apply):
    item = next(entry for entry in ROOT_FILES if entry["key"] == "work_report")
    source = skeleton_path(template_dir, item)
    target = numbered_template_path(template_dir, item)
    if not source.exists():
        add_blocker(blockers, "重点工作骨架文件不存在，无法同步编号模板", path=source)
        return
    if target is None:
        return
    if target.exists() and sha256_file(source) == sha256_file(target):
        actions.append({"kind": "sync_work_report_numbered_template", "status": "skip_same_hash", "src": str(source), "dst": str(target)})
        return
    actions.append({"kind": "sync_work_report_numbered_template", "status": "planned" if not apply else "done", "src": str(source), "dst": str(target)})
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def mark_pending_office(path):
    workbook = load_workbook(path)
    changed = []
    red_font = Font(color=RED_RGB)
    for sheet in workbook.worksheets:
        header = {}
        for col in range(1, sheet.max_column + 1):
            value = sheet.cell(2, col).value
            if value:
                header[normalize_brigade(value)] = col
        target_row = None
        for row in range(1, sheet.max_row + 1):
            if sheet.cell(row, 3).value == "产品工作实效":
                target_row = row
                break
        if target_row is None:
            continue
        for brigade in BRIGADE_ORDER:
            col = header.get(brigade)
            if not col:
                continue
            cell = sheet.cell(target_row, col)
            cell.value = PENDING_PREFIX
            cell.font = red_font
            changed.append({"sheet": sheet.title, "cell": cell.coordinate, "value": cell.value})
    workbook.save(path)
    return changed


def excel_com():
    import win32com.client

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    return excel


def mark_pending_product_stats(path, staff_counts):
    excel = excel_com()
    changed = []
    try:
        workbook = excel.Workbooks.Open(str(Path(path).resolve()))
        try:
            sheet = workbook.Worksheets(1)
            used_rows = sheet.UsedRange.Rows.Count
            for row in range(1, used_rows + 1):
                brigade = normalize_brigade(sheet.Cells(row, 1).Value)
                if brigade not in staff_counts:
                    continue
                cell = sheet.Cells(row, 2)
                cell.Value = product_stats_pending_value(staff_counts[brigade])
                cell.Font.Color = 255
                changed.append({"sheet": sheet.Name, "cell": f"B{row}", "value": str(cell.Value)})
            workbook.Save()
        finally:
            workbook.Close(SaveChanges=True)
    finally:
        excel.Quit()
    return changed


def mark_pending_work_report(path):
    excel = excel_com()
    changed = []
    try:
        workbook = excel.Workbooks.Open(str(Path(path).resolve()))
        try:
            sheet = workbook.Worksheets(1)
            for row in range(3, 11):
                sheet.Cells(row, 10).Value = "已完成"
                sheet.Cells(row, 10).Font.Color = 0
                for col in [11, 15, 16, 17, 18]:
                    cell = sheet.Cells(row, col)
                    cell.Value = PENDING_PREFIX
                    cell.Font.Color = 255
                    changed.append({"sheet": sheet.Name, "row": row, "col": col, "value": str(cell.Value)})
            workbook.Save()
        finally:
            workbook.Close(SaveChanges=True)
    finally:
        excel.Quit()
    return changed


def mark_pending_cells(bulletin_dir, year, month, staff_counts, actions, blockers, apply):
    paths = root_file_paths(bulletin_dir, year, month)
    planned = [
        {"kind": "mark_pending_office_cells", "path": str(paths["office_record"]["pending"]), "cells": "产品工作实效行 D:K"},
        {"kind": "mark_pending_product_stats_cells", "path": str(paths["product_stats"]["pending"]), "cells": "B列大队行"},
        {"kind": "mark_pending_work_report_cells", "path": str(paths["work_report"]["pending"]), "cells": "K3:K10,O3:R10"},
    ]
    if not apply:
        actions.extend({**item, "status": "planned"} for item in planned)
        return
    for key in paths:
        if not paths[key]["pending"].exists():
            add_blocker(blockers, "待补根层文件不存在，无法标红", path=paths[key]["pending"])
            return
    actions.append({"kind": "mark_pending_office_cells", "status": "done", "path": str(paths["office_record"]["pending"]), "changed": mark_pending_office(paths["office_record"]["pending"])})
    actions.append({"kind": "mark_pending_product_stats_cells", "status": "done", "path": str(paths["product_stats"]["pending"]), "changed": mark_pending_product_stats(paths["product_stats"]["pending"], staff_counts)})
    actions.append({"kind": "mark_pending_work_report_cells", "status": "done", "path": str(paths["work_report"]["pending"]), "changed": mark_pending_work_report(paths["work_report"]["pending"])})


def remove_pending_prefixes(bulletin_dir, year, month, actions, blockers, apply):
    paths = root_file_paths(bulletin_dir, year, month)
    for key, item in paths.items():
        pending = item["pending"]
        normal = item["normal"]
        if not pending.exists():
            continue
        if normal.exists():
            add_blocker(blockers, "定稿文件已存在，无法去掉待补前缀", pending=pending, normal=normal)
            continue
        actions.append({"kind": "remove_pending_prefix", "status": "planned" if not apply else "done", "src": str(pending), "dst": str(normal)})
        if apply:
            pending.rename(normal)


def run(args):
    actions = []
    blockers = []
    apply = args.apply
    bulletin_dir = Path(args.bulletin_dir)
    template_dir = Path(args.template_dir)
    work_plan = Path(args.work_plan)
    case_data = Path(args.case_data)

    for required in [template_dir / "X月通报", work_plan]:
        if not required.exists():
            add_blocker(blockers, "必要路径不存在", path=required)

    staff_counts = read_staff_counts(work_plan) if work_plan.exists() else {}
    sync_work_report_skeleton(template_dir, actions, blockers, apply)

    if args.mode == "pending":
        pending_copy_roots(bulletin_dir, template_dir, args.year, args.month, actions, blockers, apply)
        if not blockers:
            mark_pending_cells(bulletin_dir, args.year, args.month, staff_counts, actions, blockers, apply)
    else:
        paths = root_file_paths(bulletin_dir, args.year, args.month)
        stats_path = paths["product_stats"]["pending"] if paths["product_stats"]["pending"].exists() else paths["product_stats"]["normal"]
        if not stats_path.exists():
            add_blocker(blockers, "消防产品监督统计表不存在，无法核对", path=stats_path)
        if not case_data.exists():
            add_blocker(blockers, "产品案卷数据不存在，无法核对", path=case_data)
        if not blockers:
            blockers.extend(audit_product_stats(read_product_stats(stats_path), read_case_counts(case_data, args.year, args.month)))
        if not blockers:
            remove_pending_prefixes(bulletin_dir, args.year, args.month, actions, blockers, apply)

    return {
        "mode": args.mode,
        "apply": apply,
        "bulletin_dir": str(bulletin_dir),
        "year": args.year,
        "month": args.month,
        "case_window": [str(item) for item in previous_month_window(args.year, args.month)],
        "staff_counts": staff_counts,
        "actions": actions,
        "blockers": blockers,
        "ok": not blockers,
    }


def main():
    parser = argparse.ArgumentParser(description="处理通报月份根层三张当月表的待补和25号核对。")
    parser.add_argument("--bulletin-dir", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--work-plan", default=str(DEFAULT_WORK_PLAN))
    parser.add_argument("--case-data", default=str(DEFAULT_CASE_DATA))
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument("--mode", choices=["pending", "final-audit"], required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
