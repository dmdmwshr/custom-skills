"""Read the annual enforcement-staff roster (大队执法人员总表) as the only source of R02 column B headcounts.

Layout: Sheet1, column A holds a merged group label such as ``江阴大队12人``, column B holds the
staff names of that group (one per row), column C is the unreported flag and later columns are
notes. The count used for the report is the number of distinct non-empty names under a label; the
number in the label is only cross-checked, and column C is never a headcount.

Nothing is guessed: a missing brigade, duplicated or blank-only names, or an unreadable file is a
blocker for that brigade (no value is written and no old value is substituted); a label whose
number differs from the real name count is a warning and the name count is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

BRIGADE_ORDER = ["梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队"]
DEFAULT_LABEL_PATTERN = r"^(.+?大队)\s*(\d+)\s*人$"
BRIGADE_ALIASES = {"轨交大队": "轨道交通大队"}


class RosterError(RuntimeError):
    """The roster file cannot be read at all."""


def resolve_path(template, year):
    """Fill {year}/{yy} and map a Windows drive path when running under WSL/Linux."""
    text = str(template).format(year=year, yy=str(year)[-2:])
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match and os.name != "nt":
        text = "/mnt/" + match.group(1).lower() + "/" + match.group(2).replace("\\", "/")
    return Path(text)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean(value):
    return re.sub(r"\s+", "", str(value)) if value is not None else ""


def read_roster(path, sheet="Sheet1", label_pattern=DEFAULT_LABEL_PATTERN, required=BRIGADE_ORDER):
    """Return {source, sha256, groups, brigades, blockers, warnings, outside_table}; never raises on content issues."""
    path = Path(path)
    if not path.is_file():
        raise RosterError(f"人员总表不存在：{path}")
    try:
        before = _sha256(path)
        workbook = load_workbook(path, read_only=False, data_only=True)
    except Exception as error:  # unreadable/corrupt workbook
        raise RosterError(f"人员总表无法读取：{path}（{error}）") from error
    try:
        if sheet not in workbook.sheetnames:
            raise RosterError(f"人员总表缺少工作表 {sheet}，现有：{workbook.sheetnames}")
        ws = workbook[sheet]
        pattern = re.compile(label_pattern)
        groups, current = [], None
        for row in range(1, ws.max_row + 1):
            label = _clean(ws.cell(row, 1).value)
            name = _clean(ws.cell(row, 2).value)
            if label:
                match = pattern.match(label)
                if match:
                    current = {"label": label, "brigade": BRIGADE_ALIASES.get(match.group(1), match.group(1)),
                               "label_count": int(match.group(2)), "start_row": row, "names": [], "name_rows": [],
                               "blank_rows": []}
                    groups.append(current)
                else:
                    current = None  # title/header row, not a group
                    continue
            if current is None:
                continue
            if name:
                current["names"].append(name)
                current["name_rows"].append(row)
            elif label or ws.cell(row, 1).value is None:
                current["blank_rows"].append(row)
    finally:
        workbook.close()
    after = _sha256(path)
    blockers, warnings, info = [], [], []
    by_brigade = {}
    for group in groups:
        names = group["names"]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        entry = {"count": len(names), "label": group["label"], "label_count": group["label_count"],
                 "start_row": group["start_row"], "name_rows": [group["name_rows"][0], group["name_rows"][-1]] if names else [],
                 "duplicated_names": duplicated}
        brigade = group["brigade"]
        if brigade in by_brigade:
            blockers.append({"code": "staff_roster_brigade_repeated", "brigade": brigade,
                             "message": f"{brigade} 在人员总表中出现多个分组，无法确定人数"})
            by_brigade[brigade]["conflict"] = True
            continue
        by_brigade[brigade] = entry
        if brigade in required and not names:
            blockers.append({"code": "staff_roster_no_names", "brigade": brigade, "message": f"{brigade} 分组下没有姓名"})
        if brigade in required and duplicated:
            blockers.append({"code": "staff_roster_duplicate_names", "brigade": brigade,
                             "message": f"{brigade} 存在重名/重复行：{duplicated}，请核对是否同一人或同名"})
        if names and group["label_count"] != len(names):
            warnings.append({"code": "staff_label_mismatch", "brigade": brigade, "label_count": group["label_count"],
                             "name_count": len(names),
                             "message": f"{brigade} 分组标签 {group['label_count']} 人与实际姓名 {len(names)} 个不符，按姓名数写入，需人工确认是否漏填"})
    for brigade in required:
        if brigade not in by_brigade:
            blockers.append({"code": "staff_roster_brigade_missing", "brigade": brigade,
                             "message": f"人员总表缺少 {brigade} 分组"})
    outside = {b: v["count"] for b, v in by_brigade.items() if b not in required}
    if outside:
        info.append({"code": "roster_outside_table", "brigades": outside,
                     "message": "以下分组不在月度统计表八个大队行内，仅报告，不扩行、不写入 B 列"})
    counts = {b: v["count"] for b, v in by_brigade.items()
              if b in required and v["count"] and not v["duplicated_names"] and not v.get("conflict")}
    return {"source": str(path), "sha256": before.upper(), "sheet": sheet, "stable_during_read": before == after,
            "groups": by_brigade, "counts": counts, "outside_table": outside,
            "blockers": blockers, "warnings": warnings, "info": info}


def main():
    parser = argparse.ArgumentParser(description="只读校验大队执法人员总表，输出各大队人数（以姓名数为准）。")
    parser.add_argument("--roster", help="人员总表路径；缺省按 monthly_workflow.json 的 staff_roster 配置和 --year 推导")
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    import monthly_workflow as workflow  # lazy: keeps this module importable without the workflow config

    config = workflow.load_config()["data_sources"]["staff_roster"]
    path = Path(args.roster) if args.roster else resolve_path(config["path"], args.year)
    try:
        result = read_roster(path, config.get("sheet", "Sheet1"), config.get("group_label_pattern", DEFAULT_LABEL_PATTERN))
    except RosterError as error:
        print(json.dumps({"ok": False, "blockers": [{"code": "staff_roster_unreadable", "message": str(error)}]}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    result["ok"] = not result["blockers"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blockers"]:
        raise SystemExit(2)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
