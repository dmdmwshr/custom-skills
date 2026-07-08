import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import monthly_workflow as workflow


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TARGET_DIR = SKILL_DIR / "resources" / "monthly_templates"
DEFAULT_TEMPLATE_DIR = workflow.template_root()

CONFIG = workflow.load_config()
TEMPLATES = workflow.templates(CONFIG)
EXCLUDED = CONFIG.get("excluded_templates", [])
INTERNAL_TEMPLATES = workflow.internal_templates(CONFIG)


def resolve_source_dir(template_dir):
    return Path(template_dir)


def source_file_for(template_dir, source_dir, filename):
    item = next((entry for entry in workflow.templates(CONFIG) if entry["file"] == filename), None)
    if item is None:
        return Path(source_dir) / filename
    return workflow.external_template_path(item, config=CONFIG, template_dir=template_dir)


def sha256_file(path):
    return workflow.sha256_file(path)


def utc_now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_copy_action(actions, src, dst, kind, dry_run, apply):
    src = Path(src)
    dst = Path(dst)
    source_hash = sha256_file(src)
    target_hash = sha256_file(dst) if dst.exists() else None
    action = {
        "kind": kind,
        "status": "skip_same_hash" if target_hash == source_hash else ("planned" if dry_run else "done"),
        "src": str(src),
        "dst": str(dst),
        "sha256": source_hash,
        "target_sha256": target_hash,
        "match": target_hash == source_hash,
    }
    if apply and target_hash != source_hash:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    actions.append(action)
    return source_hash


def run(args):
    source_dir = resolve_source_dir(args.template_dir)
    actions = []
    blockers = []
    manifest_items = []
    config = workflow.load_config()
    expected_snapshot_files = {
        item["file"]
        for item in workflow.templates(config) + workflow.internal_templates(config)
    }

    for item in workflow.templates(config):
        source = workflow.external_template_path(item, config=config, template_dir=args.template_dir)
        target = workflow.snapshot_template_path(item, config=config)
        if not source.exists():
            blockers.append({"message": "模板源文件不存在", "path": str(source), "template_id": item["id"], "key": item["key"]})
            continue

        source_hash = add_copy_action(actions, source, target, "copy_template", args.dry_run, args.apply)

        manifest_item = dict(item)
        manifest_item["original_name"] = item["file"]
        manifest_item["source_path"] = str(source)
        manifest_item["sha256"] = source_hash
        manifest_items.append(manifest_item)

    if TARGET_DIR.exists():
        for stale in sorted(TARGET_DIR.iterdir(), key=lambda path: path.name):
            if not stale.is_file() or stale.name == "manifest.json" or stale.name in expected_snapshot_files:
                continue
            action = {
                "kind": "remove_stale_snapshot_template",
                "status": "planned" if args.dry_run else "done",
                "path": str(stale),
                "sha256": sha256_file(stale),
            }
            if args.apply:
                stale.unlink()
            actions.append(action)

    manifest = {
        "version": 3,
        "source_directory": str(source_dir),
        "generated_at": utc_now_text(),
        "templates": manifest_items,
        "internal_templates": INTERNAL_TEMPLATES,
        "excluded_source_files": EXCLUDED,
        "template_policy": "external_template_source_is_authoritative; skill_snapshot_is_for_hash_verification",
    }
    manifest_path = TARGET_DIR / "manifest.json"
    if args.apply and not blockers:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        actions.append({"kind": "write_manifest", "status": "done", "path": str(manifest_path)})
    else:
        actions.append({"kind": "write_manifest", "status": "planned", "path": str(manifest_path)})

    return {"mode": "apply" if args.apply else "dry-run", "actions": actions, "blockers": blockers, "ok": not blockers}


def main():
    parser = argparse.ArgumentParser(
        description="从外部模板事实源同步月度登记模板到 skill 快照目录。",
        epilog="参考：references/monthly/02_template_strategy.md。",
    )
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
