import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from docx import Document
from docx.shared import RGBColor

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SKILL_TEMPLATE_DIR = SKILL_DIR / "resources" / "monthly_templates"

DEFAULT_WORK_ROOT = Path(r"E:\文件夹\1、工作\2、产品，科技，联网检测\1、产品监督")
DEFAULT_MAY_DIR = DEFAULT_WORK_ROOT / "26年" / "5月通报"
DEFAULT_JUNE_MONTH_DIR = DEFAULT_WORK_ROOT / "26年" / "6月通报" / "5月巡查"
DEFAULT_TEMPLATE_DIR = DEFAULT_WORK_ROOT / "模板文件"

PENDING_PREFIX = "【待补】"

MAY_ROOT_RENAMES = {
    "5月科室月考核情况记录表.xlsx": "2026年5月工作检查-科室月考核情况记录表.xlsx",
    "5月消防产品监督统计表.xls": "2026年5月工作检查-消防产品监督统计表.xls",
    "5月重点工作完成情况上报表（应急通信与消防科技）.xls": "2026年5月工作检查-重点工作完成情况上报表（应急通信与消防科技）.xls",
}

TEMPLATE_RENAMES = {
    "（模板）产品巡查底册.docx": "01_产品巡查底册模板.docx",
    "（模板）消防产品档案质量明细表.doc": "02_消防产品档案质量明细表模板.doc",
    "（模板）产品监督成绩总表.xlsx": "03_产品监督成绩总表模板.xlsx",
    "（模板）个人执法统计表202601.xlsx": "04_个人执法统计表模板.xlsx",
    "（模板）科室月考核情况记录表.xlsx": "05_科室月考核情况记录表模板.xlsx",
    "(模板-成绩汇总)消防监督管理系统消防执法质量（个案成绩）.xls": "06_消防执法质量个案成绩模板.xls",
    "(样例)xxxx年x月通报.doc": "07_月度通报模板.doc",
    "（空表）每月消防产品监督统计表.xls": "08_每月消防产品监督统计表空表模板.xls",
    "(模板)X月消防产品监督统计表.xls": "09_消防产品监督统计表工作检查模板.xls",
    "（样例数据）消防产品档案质量明细表.doc": "90_消防产品档案质量明细表样例数据.doc",
}

JUNE_TEMPLATE_COPY_NAMES = set(TEMPLATE_RENAMES) | {
    "（模板）产品监督成绩总表.xlsx",
    "（模板）个人执法统计表202601.xlsx",
    "（模板）科室月考核情况记录表.xlsx",
    "（模板）消防产品档案质量明细表.doc",
    "（空表）每月消防产品监督统计表.xls",
    "（样例数据）消防产品档案质量明细表.doc",
}

PRODUCT_REGISTER_PARAGRAPHS = [67, 68, 109, 110]


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
    item.update({k: str(v) for k, v in extra.items()})
    blockers.append(item)


def move_or_rename(src, dst, actions, blockers, apply, root, kind):
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        return
    if not is_under(src, root) or not is_under(dst, root):
        add_blocker(blockers, "路径越界，已拒绝移动", src=src, dst=dst, root=root)
        return
    if dst.exists():
        if sha256_file(src) == sha256_file(dst):
            actions.append({"kind": kind, "status": "skip_same_hash_target_exists", "src": str(src), "dst": str(dst)})
            return
        add_blocker(blockers, "目标已存在且内容不同", src=src, dst=dst)
        return
    actions.append({"kind": kind, "status": "planned" if not apply else "done", "src": str(src), "dst": str(dst)})
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def collect_known_template_hashes(template_dir):
    hashes = {}
    for directory in [Path(template_dir), SKILL_TEMPLATE_DIR]:
        if not directory.exists():
            continue
        for file_path in directory.glob("*"):
            if file_path.is_file() and not file_path.name.startswith("~$"):
                hashes.setdefault(sha256_file(file_path), []).append(str(file_path))
    return hashes


def archive_office_locks(base_dir, actions, blockers, apply):
    archive_dir = Path(base_dir) / "_Office临时锁文件归档"
    for lock_file in Path(base_dir).glob("~$*"):
        move_or_rename(lock_file, archive_dir / lock_file.name, actions, blockers, apply, base_dir, "archive_office_lock")


def organize_may_dir(may_dir, actions, blockers, apply):
    may_dir = Path(may_dir)
    for old_name, new_name in MAY_ROOT_RENAMES.items():
        move_or_rename(may_dir / old_name, may_dir / new_name, actions, blockers, apply, may_dir, "may_root_rename")

    product_dir = may_dir / "4月巡查" / "2026年4月消防产品监督成绩"
    if product_dir.exists():
        for file_path in product_dir.glob("*（5月产品监督档案）.doc"):
            move_or_rename(
                file_path,
                file_path.with_name(file_path.name.replace("（5月产品监督档案）", "（4月产品监督档案）")),
                actions,
                blockers,
                apply,
                may_dir,
                "may_product_archive_month_fix",
            )
    archive_office_locks(may_dir / "4月巡查", actions, blockers, apply)


def organize_template_dir(template_dir, actions, blockers, apply):
    template_dir = Path(template_dir)
    for old_name, new_name in TEMPLATE_RENAMES.items():
        move_or_rename(template_dir / old_name, template_dir / new_name, actions, blockers, apply, template_dir, "template_rename")
    archive_office_locks(template_dir, actions, blockers, apply)


def archive_june_template_copies(june_month_dir, template_dir, actions, blockers, apply):
    june_month_dir = Path(june_month_dir)
    archive_dir = june_month_dir / "_模板副本归档"
    known_hashes = collect_known_template_hashes(template_dir)
    for name in sorted(JUNE_TEMPLATE_COPY_NAMES):
        file_path = june_month_dir / name
        if not file_path.exists() or not file_path.is_file():
            continue
        digest = sha256_file(file_path)
        if digest not in known_hashes:
            add_blocker(blockers, "疑似模板副本哈希不在模板库中，未归档", path=file_path, sha256=digest)
            continue
        move_or_rename(file_path, archive_dir / file_path.name, actions, blockers, apply, june_month_dir, "archive_june_template_copy")


def mark_docx_paragraphs(path, paragraphs):
    document = Document(str(path))
    changed = []
    for paragraph_no in paragraphs:
        if paragraph_no < 1 or paragraph_no > len(document.paragraphs):
            continue
        paragraph = document.paragraphs[paragraph_no - 1]
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(255, 0, 0)
        changed.append({"paragraph": paragraph_no, "text": paragraph.text})
    document.save(str(path))
    return changed


def mark_excel_cells(path, cells):
    import win32com.client

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    marked = []
    try:
        workbook = excel.Workbooks.Open(str(Path(path).resolve()))
        try:
            for cell_spec in cells:
                sheet = workbook.Worksheets(cell_spec["sheet"])
                cell = sheet.Cells(cell_spec["row"], cell_spec["col"])
                cell.Font.Color = 255
                marked.append(
                    {
                        "path": str(path),
                        "sheet": cell_spec["sheet"],
                        "row": cell_spec["row"],
                        "col": cell_spec["col"],
                        "value": str(cell.Value),
                    }
                )
            workbook.Save()
        finally:
            workbook.Close(SaveChanges=True)
    finally:
        excel.Quit()
    return marked


def organize_june_dir(june_month_dir, actions, blockers, apply, template_dir):
    june_month_dir = Path(june_month_dir)
    archive_june_template_copies(june_month_dir, template_dir, actions, blockers, apply)

    old_product = june_month_dir / "（5月）产品巡查底册.docx"
    product = june_month_dir / f"{PENDING_PREFIX}（5月）产品巡查底册（不发）.docx"
    move_or_rename(old_product, product, actions, blockers, apply, june_month_dir, "june_product_register_pending_rename")

    network_dir = june_month_dir / "2026年5月联网监测基础信息考评明细表"
    old_base_info = network_dir / "5月基础信息考评截图.xls"
    base_info = june_month_dir / f"{PENDING_PREFIX}5月基础信息考评截图（不发）.xls"
    move_or_rename(old_base_info, base_info, actions, blockers, apply, june_month_dir, "june_base_info_move_pending")

    if apply and not blockers:
        if product.exists():
            marked = mark_docx_paragraphs(product, PRODUCT_REGISTER_PARAGRAPHS)
            actions.append({"kind": "mark_docx_missing", "status": "done", "path": str(product), "marked": marked})
        else:
            add_blocker(blockers, "产品巡查底册不存在，无法标红", path=product)

        excel_marks = []
        if base_info.exists():
            excel_marks.extend(mark_excel_cells(base_info, [{"sheet": "宜兴", "row": 4, "col": 6}]))
        else:
            add_blocker(blockers, "基础信息考评截图不存在，无法标红", path=base_info)
        network_stats = network_dir / "联网监测统计表.xls"
        if network_stats.exists():
            excel_marks.extend(mark_excel_cells(network_stats, [{"sheet": "Sheet1", "row": 7, "col": 12}]))
        else:
            add_blocker(blockers, "联网监测统计表不存在，无法标红", path=network_stats)
        if excel_marks:
            actions.append({"kind": "mark_excel_missing", "status": "done", "marked": excel_marks})
    else:
        actions.append(
            {
                "kind": "mark_missing",
                "status": "planned",
                "docx": str(product),
                "docx_paragraphs": PRODUCT_REGISTER_PARAGRAPHS,
                "excel_cells": [
                    {"path": str(base_info), "sheet": "宜兴", "row": 4, "col": 6},
                    {"path": str(network_dir / "联网监测统计表.xls"), "sheet": "Sheet1", "row": 7, "col": 12},
                ],
            }
        )


def run(args):
    actions = []
    blockers = []
    apply = args.apply

    organize_may_dir(args.may_dir, actions, blockers, apply)
    organize_template_dir(args.template_dir, actions, blockers, apply)
    organize_june_dir(args.june_month_dir, actions, blockers, apply, args.template_dir)

    return {
        "mode": "apply" if apply else "dry-run",
        "actions": actions,
        "blockers": blockers,
        "ok": not blockers,
    }


def main():
    parser = argparse.ArgumentParser(description="整理月度产品成绩登记目录并标注缺漏。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只预览动作，不修改文件")
    mode.add_argument("--apply", action="store_true", help="执行重命名、移动和缺漏标红")
    parser.add_argument("--may-dir", default=str(DEFAULT_MAY_DIR))
    parser.add_argument("--june-month-dir", default=str(DEFAULT_JUNE_MONTH_DIR))
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
