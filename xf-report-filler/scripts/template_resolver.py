import argparse
import json
from pathlib import Path

import monthly_workflow as workflow


def resolve_templates(template_dir=None, include_reserved=True, allow_snapshot_fallback=False, config=None):
    config = config or workflow.load_config()
    statuses = workflow.template_statuses(
        config=config,
        template_dir=template_dir,
        allow_snapshot_fallback=allow_snapshot_fallback,
    )
    templates = {}
    warnings = []
    blockers = []
    for status in statuses:
        if not include_reserved and not status["used_in_monthly_register"]:
            continue
        if status["adopted_path"]:
            templates[status["key"]] = Path(status["adopted_path"])
        for message in status["warnings"]:
            warnings.append(
                {
                    "template_id": status["id"],
                    "key": status["key"],
                    "file": status["file"],
                    "message": message,
                    "external_path": status["external_path"],
                    "snapshot_path": status["snapshot_path"],
                }
            )
        for message in status["blockers"]:
            blockers.append(
                {
                    "template_id": status["id"],
                    "key": status["key"],
                    "file": status["file"],
                    "message": message,
                    "external_path": status["external_path"],
                    "snapshot_path": status["snapshot_path"],
                }
            )
    return {
        "templates": templates,
        "statuses": statuses,
        "warnings": warnings,
        "blockers": blockers,
    }


def main():
    parser = argparse.ArgumentParser(description="校验外部模板事实源与 skill 内模板快照。")
    parser.add_argument("--template-dir", help="外部模板根目录；默认读取 monthly_workflow.json 中的绝对路径。")
    parser.add_argument("--allow-snapshot-fallback", action="store_true", help="外部模板缺失时允许使用 skill 快照，仅用于审计或 dry-run。")
    parser.add_argument("--used-only", action="store_true", help="只校验月度生成实际使用的模板。")
    args = parser.parse_args()
    result = resolve_templates(
        template_dir=args.template_dir,
        include_reserved=not args.used_only,
        allow_snapshot_fallback=args.allow_snapshot_fallback,
    )
    printable = dict(result)
    printable["templates"] = {key: str(path) for key, path in result["templates"].items()}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
