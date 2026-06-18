import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TARGET_DIR = SKILL_DIR / "resources" / "monthly_templates"
DEFAULT_TEMPLATE_DIR = Path(r"E:\文件夹\1、工作\2、产品，科技，联网检测\1、产品监督\模板文件")
SKELETON_DIR_NAME = "X月通报"
WORK_REPORT_SKELETON = "X月重点工作完成情况上报表（应急通信与消防科技）.xls"
WORK_REPORT_NUMBERED = "10_重点工作完成情况上报表（应急通信与消防科技）模板.xls"

TEMPLATES = [
    {
        "key": "product_register",
        "file": "01_产品巡查底册模板.docx",
        "description": "产品巡查底册模板",
        "used_in_monthly_register": False,
        "reserved": False,
    },
    {
        "key": "product_archive_detail",
        "file": "02_消防产品档案质量明细表模板.doc",
        "description": "消防产品档案质量明细表模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "product_summary",
        "file": "03_产品监督成绩总表模板.xlsx",
        "description": "产品监督成绩总表模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "personal_stats",
        "file": "04_个人执法统计表模板.xlsx",
        "description": "个人执法统计表模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "office_record",
        "file": "05_科室月考核情况记录表模板.xlsx",
        "description": "科室月考核情况记录表模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "case_scores",
        "file": "06_消防执法质量个案成绩模板.xls",
        "description": "消防监督管理系统消防执法质量个案成绩模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "monthly_report",
        "file": "07_月度通报模板.doc",
        "description": "月度通报模板",
        "used_in_monthly_register": True,
        "reserved": False,
    },
    {
        "key": "monthly_product_supervision_stats_blank",
        "file": "08_每月消防产品监督统计表空表模板.xls",
        "description": "每月消防产品监督统计表空表模板",
        "used_in_monthly_register": False,
        "reserved": True,
    },
    {
        "key": "monthly_product_supervision_stats_work_check",
        "file": "09_消防产品监督统计表工作检查模板.xls",
        "description": "消防产品监督统计表工作检查模板",
        "used_in_monthly_register": False,
        "reserved": True,
    },
    {
        "key": "emergency_comm_fire_tech_work_report",
        "file": "10_重点工作完成情况上报表（应急通信与消防科技）模板.xls",
        "description": "重点工作完成情况上报表（应急通信与消防科技）模板",
        "used_in_monthly_register": False,
        "reserved": True,
    },
]

EXCLUDED = [
    {
        "file": "90_消防产品档案质量明细表样例数据.doc",
        "reason": "样例数据文件，不作为空白模板参与月度生成。",
    }
]


def resolve_source_dir(template_dir):
    template_dir = Path(template_dir)
    numbered_dir = template_dir / "_编号模板库"
    if numbered_dir.exists():
        return numbered_dir
    return template_dir


def source_file_for(template_dir, source_dir, filename):
    template_dir = Path(template_dir)
    if filename == WORK_REPORT_NUMBERED:
        skeleton = template_dir / SKELETON_DIR_NAME / WORK_REPORT_SKELETON
        if skeleton.exists():
            return skeleton
    return Path(source_dir) / filename


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def utc_now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(args):
    source_dir = resolve_source_dir(args.template_dir)
    actions = []
    blockers = []
    manifest_items = []

    for item in TEMPLATES:
        source = source_file_for(args.template_dir, source_dir, item["file"])
        target = TARGET_DIR / item["file"]
        if not source.exists():
            blockers.append({"message": "模板源文件不存在", "path": str(source)})
            continue
        source_hash = sha256_file(source)
        action = {
            "kind": "copy_template",
            "status": "planned" if args.dry_run else "done",
            "src": str(source),
            "dst": str(target),
            "sha256": source_hash,
        }
        if args.apply:
            TARGET_DIR.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha256_file(target) == source_hash:
                action["status"] = "skip_same_hash"
            else:
                shutil.copy2(source, target)
        actions.append(action)

        manifest_item = dict(item)
        manifest_item["original_name"] = item["file"]
        manifest_item["source_path"] = str(source)
        manifest_item["sha256"] = source_hash
        manifest_items.append(manifest_item)

    old_reserved = TARGET_DIR / "08_每月消防产品监督统计表模板.xls"
    new_reserved = TARGET_DIR / "08_每月消防产品监督统计表空表模板.xls"
    if args.apply and old_reserved.exists() and new_reserved.exists():
        old_hash = sha256_file(old_reserved)
        old_reserved.unlink()
        actions.append(
            {
                "kind": "remove_old_reserved_template_name",
                "status": "done",
                "path": str(old_reserved),
                "sha256": old_hash,
                "replacement": str(new_reserved),
            }
        )

    manifest = {
        "version": 2,
        "source_directory": str(source_dir),
        "generated_at": utc_now_text(),
        "templates": manifest_items,
        "excluded_source_files": EXCLUDED,
    }
    manifest_path = TARGET_DIR / "manifest.json"
    if args.apply and not blockers:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        actions.append({"kind": "write_manifest", "status": "done", "path": str(manifest_path)})
    else:
        actions.append({"kind": "write_manifest", "status": "planned", "path": str(manifest_path)})

    return {"mode": "apply" if args.apply else "dry-run", "actions": actions, "blockers": blockers, "ok": not blockers}


def main():
    parser = argparse.ArgumentParser(description="从外部模板目录同步月度登记模板到 skill 资源目录。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
