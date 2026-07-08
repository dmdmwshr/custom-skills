import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import monthly_workflow as workflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SKILL_TEMPLATE_DIR = SKILL_DIR / "resources" / "monthly_templates"
CONFIG = workflow.load_config()

DEFAULT_TEMPLATE_DIR = workflow.template_root(CONFIG)
DEFAULT_BULLETIN_DIR = DEFAULT_TEMPLATE_DIR.parent / "26年" / "6月通报"

PENDING_PREFIX = "【待补】"

TEMPLATE_RENAMES = workflow.legacy_template_names(CONFIG)
NUMBERED_TEMPLATE_NAMES = workflow.template_file_names(CONFIG, include_excluded=True)

NUMBERED_TEMPLATE_CANDIDATES = {
    name: [name] for name in NUMBERED_TEMPLATE_NAMES
}
for old_name, new_name in TEMPLATE_RENAMES.items():
    NUMBERED_TEMPLATE_CANDIDATES.setdefault(new_name, [new_name]).append(old_name)

BULLETIN_ROOT_TEMPLATE_MAP = workflow.bulletin_root_map(CONFIG)

TEMPLATE_COPY_NAMES = set(TEMPLATE_RENAMES) | set(NUMBERED_TEMPLATE_NAMES)
DEPRECATED_ROOT_FILE_NAMES = [
    "{year}年{month}月重点工作完成情况上报表（应急通信与消防科技）.xls",
]


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


def copy_if_missing(src, dst, actions, blockers, apply, root, kind):
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        add_blocker(blockers, "源文件不存在，无法复制", src=src, dst=dst)
        return
    if not is_under(src, root) or not is_under(dst, root):
        add_blocker(blockers, "路径越界，已拒绝复制", src=src, dst=dst, root=root)
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
        shutil.copy2(src, dst)


def delete_file_if_exists(path, actions, blockers, apply, root, kind):
    path = Path(path)
    if not path.exists():
        return
    if not path.is_file():
        add_blocker(blockers, "目标不是文件，拒绝删除", path=path)
        return
    if not is_under(path, root):
        add_blocker(blockers, "路径越界，已拒绝删除", path=path, root=root)
        return
    digest = sha256_file(path)
    actions.append({"kind": kind, "status": "planned" if not apply else "done", "path": str(path), "sha256": digest})
    if apply:
        path.unlink()


def ensure_dir(path, actions, apply, kind):
    path = Path(path)
    if path.exists():
        actions.append({"kind": kind, "status": "skip_exists", "path": str(path)})
        return
    actions.append({"kind": kind, "status": "planned" if not apply else "done", "path": str(path)})
    if apply:
        path.mkdir(parents=True, exist_ok=True)


def common_root(*paths):
    resolved = [str(Path(path).resolve()) for path in paths]
    return Path(os.path.commonpath(resolved))


def skeleton_template_dir(template_dir):
    return Path(template_dir) / "X月通报"


def score_template_dir(template_dir):
    return skeleton_template_dir(template_dir) / "上月巡查"


def find_first_existing(paths):
    for path in paths:
        path = Path(path)
        if path.exists():
            return path
    return None


def template_source_for(template_dir, filename):
    template_dir = Path(template_dir)
    skeleton_dir = skeleton_template_dir(template_dir)
    score_dir = score_template_dir(template_dir)
    configured = next((item for item in workflow.templates(CONFIG) if item["file"] == filename), None)
    if configured:
        canonical = workflow.external_template_path(configured, config=CONFIG, template_dir=template_dir)
        if canonical.exists():
            return canonical
    candidates = []
    for candidate_name in NUMBERED_TEMPLATE_CANDIDATES.get(filename, [filename]):
        candidates.extend(
            [
                score_dir / candidate_name,
                skeleton_dir / candidate_name,
                template_dir / candidate_name,
            ]
        )
    if configured:
        candidates.append(workflow.external_template_path(configured, config=CONFIG, template_dir=template_dir))
    else:
        candidates.append(template_dir / filename)
    return find_first_existing(candidates) or candidates[0]


def skeleton_source_for(template_dir, item):
    template_dir = Path(template_dir)
    skeleton = skeleton_template_dir(template_dir) / item["skeleton"]
    if skeleton.exists():
        return skeleton
    return template_source_for(template_dir, item["library"])


def organize_template_model(template_dir, actions, blockers, apply):
    template_dir = Path(template_dir)
    skeleton_dir = skeleton_template_dir(template_dir)
    score_dir = score_template_dir(template_dir)

    ensure_dir(skeleton_dir, actions, apply, "ensure_bulletin_skeleton_dir")
    ensure_dir(score_dir, actions, apply, "ensure_bulletin_skeleton_patrol_dir")
    archive_office_locks(template_dir, actions, blockers, apply)

    for item in workflow.templates(CONFIG):
        filename = item["file"]
        target = workflow.external_template_path(item, config=CONFIG, template_dir=template_dir)
        if target.exists():
            actions.append({"kind": "standard_template_present", "status": "skip_exists", "path": str(target)})
            continue
        source = template_source_for(template_dir, filename)
        if source is None or not source.exists():
            add_blocker(blockers, "标准模板源文件不存在", target=target)
            continue
        copy_if_missing(
            source,
            target,
            actions,
            blockers,
            apply,
            template_dir,
            "copy_template_to_standard_skeleton",
        )

    for item in BULLETIN_ROOT_TEMPLATE_MAP:
        skeleton_target = skeleton_dir / item["skeleton"]
        if skeleton_target.exists():
            actions.append({"kind": "bulletin_skeleton_present", "status": "skip_exists", "path": str(skeleton_target)})
            continue
        source = template_source_for(template_dir, item["library"])
        copy_if_missing(
            source,
            skeleton_target,
            actions,
            blockers,
            apply,
            template_dir,
            "copy_bulletin_skeleton_template",
        )


def normalize_score_source_names(score_dir, score_year, score_month, actions, blockers, apply):
    score_dir = Path(score_dir)
    product_normal_name = f"{score_year}年{score_month}月产品巡查底册（不发）.docx"
    base_info_normal_name = f"{score_year}年{score_month}月基础信息考评截图（不发）.xls"
    network_dir = score_dir / f"{score_year}年{score_month}月联网监测基础信息考评明细表"

    product_target = score_dir / product_normal_name
    product_old_names = [
        f"{PENDING_PREFIX}（{score_month}月）产品巡查底册（不发）.docx",
        f"（{score_month}月）产品巡查底册（不发）.docx",
        f"（{score_month}月）产品巡查底册.docx",
        f"{score_month}月产品巡查底册（不发）.docx",
    ]
    for old_name in product_old_names:
        move_or_rename(score_dir / old_name, product_target, actions, blockers, apply, score_dir, "score_product_register_rename")

    base_info_target = score_dir / base_info_normal_name
    base_info_old_paths = [
        score_dir / f"{PENDING_PREFIX}{score_month}月基础信息考评截图（不发）.xls",
        score_dir / f"{score_month}月基础信息考评截图（不发）.xls",
        network_dir / f"{score_month}月基础信息考评截图.xls",
    ]
    for old_path in base_info_old_paths:
        move_or_rename(old_path, base_info_target, actions, blockers, apply, score_dir, "score_base_info_move")


def instantiate_bulletin_dir(bulletin_dir, bulletin_year, bulletin_month, score_year, score_month, template_dir, actions, blockers, apply):
    bulletin_dir = Path(bulletin_dir)
    template_dir = Path(template_dir)
    operation_root = common_root(bulletin_dir, template_dir)
    score_dir = bulletin_dir / f"{score_month}月巡查"

    ensure_dir(bulletin_dir, actions, apply, "ensure_bulletin_dir")
    ensure_dir(score_dir, actions, apply, "ensure_score_patrol_dir")

    for item in BULLETIN_ROOT_TEMPLATE_MAP:
        source = skeleton_source_for(template_dir, item)
        target_name = item["target"].format(year=bulletin_year, month=bulletin_month)
        pending_target = bulletin_dir / f"{PENDING_PREFIX}{target_name}"
        if pending_target.exists():
            actions.append(
                {
                    "kind": "copy_bulletin_root_file",
                    "status": "skip_pending_target_exists",
                    "src": str(source),
                    "dst": str(bulletin_dir / target_name),
                    "pending": str(pending_target),
                }
            )
            continue
        copy_if_missing(
            source,
            bulletin_dir / target_name,
            actions,
            blockers,
            apply,
            operation_root,
            "copy_bulletin_root_file",
        )

    archive_deprecated_root_files(bulletin_dir, bulletin_year, bulletin_month, actions, blockers, apply)

    wrong_score_office = score_dir / f"{score_year}年{score_month}月科室月考核情况记录表.xlsx"
    delete_file_if_exists(
        wrong_score_office,
        actions,
        blockers,
        apply,
        bulletin_dir,
        "delete_wrong_score_office_record",
    )

    archive_template_copies(score_dir, template_dir, actions, blockers, apply)
    normalize_score_source_names(score_dir, score_year, score_month, actions, blockers, apply)


def collect_known_template_hashes(template_dir):
    hashes = {}
    for directory in [skeleton_template_dir(template_dir), score_template_dir(template_dir), SKILL_TEMPLATE_DIR]:
        if not directory.exists():
            continue
        for file_path in directory.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("~$"):
                hashes.setdefault(sha256_file(file_path), []).append(str(file_path))
    return hashes


def archive_office_locks(base_dir, actions, blockers, apply):
    archive_dir = Path(base_dir) / "_Office临时锁文件归档"
    for lock_file in Path(base_dir).glob("~$*"):
        move_or_rename(lock_file, archive_dir / lock_file.name, actions, blockers, apply, base_dir, "archive_office_lock")


def archive_template_copies(month_dir, template_dir, actions, blockers, apply):
    month_dir = Path(month_dir)
    archive_dir = month_dir / "_模板副本归档"
    known_hashes = collect_known_template_hashes(template_dir)
    for name in sorted(TEMPLATE_COPY_NAMES):
        file_path = month_dir / name
        if not file_path.exists() or not file_path.is_file():
            continue
        digest = sha256_file(file_path)
        move_or_rename(file_path, archive_dir / file_path.name, actions, blockers, apply, month_dir, "archive_template_copy")
        if actions and actions[-1].get("src") == str(file_path):
            actions[-1]["sha256"] = digest
            actions[-1]["hash_match_current_template"] = digest in known_hashes


def archive_deprecated_root_files(bulletin_dir, year, month, actions, blockers, apply):
    bulletin_dir = Path(bulletin_dir)
    archive_dir = bulletin_dir / "_停用文件归档"
    for template in DEPRECATED_ROOT_FILE_NAMES:
        name = template.format(year=year, month=month)
        for candidate in [bulletin_dir / name, bulletin_dir / f"{PENDING_PREFIX}{name}"]:
            move_or_rename(
                candidate,
                archive_dir / candidate.name,
                actions,
                blockers,
                apply,
                bulletin_dir,
                "archive_deprecated_root_file",
            )


def run(args):
    actions = []
    blockers = []
    apply = args.apply

    organize_template_model(args.template_dir, actions, blockers, apply)
    instantiate_bulletin_dir(
        args.bulletin_dir,
        args.bulletin_year,
        args.bulletin_month,
        args.score_year,
        args.score_month,
        args.template_dir,
        actions,
        blockers,
        apply,
    )

    return {
        "mode": "apply" if apply else "dry-run",
        "bulletin": {
            "dir": str(Path(args.bulletin_dir)),
            "year": args.bulletin_year,
            "month": args.bulletin_month,
        },
        "score": {
            "year": args.score_year,
            "month": args.score_month,
            "dir": str(Path(args.bulletin_dir) / f"{args.score_month}月巡查"),
        },
        "template_dir": str(Path(args.template_dir)),
        "actions": actions,
        "blockers": blockers,
        "ok": not blockers,
    }


def main():
    parser = argparse.ArgumentParser(
        description="按通报月份目录模型整理月度产品成绩登记目录。",
        epilog="参考：references/monthly/01_directory_model.md；模板策略见 references/monthly/02_template_strategy.md。",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只预览动作，不修改文件")
    mode.add_argument("--apply", action="store_true", help="执行安全重命名、移动、复制和指定误生成文件删除")
    parser.add_argument("--bulletin-dir", default=str(DEFAULT_BULLETIN_DIR), help="通报月份目录，例如 ...\\26年\\6月通报")
    parser.add_argument("--bulletin-year", type=int, default=2026, help="通报目录年份")
    parser.add_argument("--bulletin-month", type=int, default=6, help="通报目录月份")
    parser.add_argument("--score-year", type=int, default=2026, help="巡查/成绩所属年份")
    parser.add_argument("--score-month", type=int, default=5, help="巡查/成绩所属月份")
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
