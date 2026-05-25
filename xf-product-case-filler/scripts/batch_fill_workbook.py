#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def extraction_for(extractions_dir: Path, brigade: str) -> Path | None:
    candidates = [
        extractions_dir / f"{brigade}.json",
        extractions_dir / f"{brigade}.extractions.json",
        extractions_dir / f"{brigade}_extractions.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按截图根目录批量调用 fill_workbook.py。")
    parser.add_argument("--workbook", required=True, type=Path, help="产品案卷数据.xlsx")
    parser.add_argument("--cases-root", required=True, type=Path, help="产品检查案卷汇总目录")
    parser.add_argument("--extractions-dir", required=True, type=Path, help="按大队命名的抽取 JSON 目录")
    parser.add_argument("--brigades", nargs="*", help="只处理指定大队；省略时处理 cases-root 下所有有 JSON 的目录")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-rename", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = Path(__file__).with_name("fill_workbook.py")
    brigades = args.brigades or [path.name for path in args.cases_root.iterdir() if path.is_dir()]
    results = []
    failed = False
    for brigade in brigades:
        image_dir = args.cases_root / brigade
        extraction = extraction_for(args.extractions_dir, brigade)
        if not image_dir.exists() or not extraction:
            results.append({"brigade": brigade, "status": "skipped", "reason": "missing image dir or extraction json"})
            continue
        command = [
            sys.executable,
            str(script),
            "--workbook",
            str(args.workbook),
            "--brigade",
            brigade,
            "--image-dir",
            str(image_dir),
            "--extractions",
            str(extraction),
        ]
        if args.dry_run:
            command.append("--dry-run")
        if args.skip_rename:
            command.append("--skip-rename")
        completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        status = "ok" if completed.returncode == 0 else "failed"
        failed = failed or completed.returncode != 0
        results.append({"brigade": brigade, "status": status, "stdout": completed.stdout, "stderr": completed.stderr})
    print(json.dumps({"status": "errors_found" if failed else "ok", "results": results}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
