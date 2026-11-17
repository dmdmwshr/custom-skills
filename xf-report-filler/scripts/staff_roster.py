"""Read the annual enforcement-staff roster (大队执法人员总表) as the only source of R02 column B headcounts.

Layout: Sheet1, column A holds a merged group label such as ``江阴大队12人``, column B holds the
staff names of that group (one per row), column C is the unreported flag and later columns are
notes. The count used for the report is the number of distinct non-empty names under a label; the
number in the label is only cross-checked, and column C is never a headcount.

Since 2026-11-17 the default source is the Feishu roster snapshot (``staff_roster.ndjson``, exported beside the case snapshot):
people with their stable id, brigade and validity dates, counted as valid on the 25th of the statistics month. The spreadsheet
reader stays for an explicitly given file only.

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


BRIGADE_ORDER = ["梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队"]
DEFAULT_LABEL_PATTERN = r"^(.+?大队)\s*(\d+)\s*人$"
BRIGADE_ALIASES = {"轨交大队": "轨道交通大队"}
ROSTER_SNAPSHOT_FILE = "staff_roster.ndjson"
ROSTER_SNAPSHOT_MANIFEST = "staff_roster.manifest.json"
ROSTER_FIELDS = {"id": "人员ID", "brigade": "大队", "start": "有效起日", "end": "有效止日"}


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
        from openpyxl import load_workbook  # lazy: the snapshot reader and monthly_run plan must work without openpyxl

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


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _day(value):
    value = _first(value)
    return str(value)[:10] if value not in (None, "") else None


def read_feishu_roster(snapshot_dir, as_of, required=BRIGADE_ORDER):
    """Count the people valid on ``as_of`` (a ``date``) per brigade from the Feishu roster snapshot of ``snapshot_dir``.

    Same result shape as ``read_roster`` so every consumer keeps working. A brigade with no valid person is a blocker (no value is
    written); rows of brigades outside ``required`` are reported only. Names are not part of the snapshot.
    """
    root = Path(snapshot_dir)
    path = root / ROSTER_SNAPSHOT_FILE
    if not path.is_file():
        raise RosterError(f"飞书统计快照里没有执法人员名录：{path}（重新导出快照；导出脚本会一并导出名录）")
    sha = _sha256(path)
    manifest_path = root / ROSTER_SNAPSHOT_MANIFEST
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as error:
                raise RosterError(f"{path.name} 第 {number} 行不是合法 JSON") from error
            if not isinstance(value, dict):
                raise RosterError(f"{path.name} 第 {number} 行不是对象")
            rows.append(value)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("records_count") != len(rows) or manifest.get("has_more"):
            raise RosterError(f"{path.name} 行数 {len(rows)} 与清单 {manifest.get('records_count')} 不一致或仍有未导出分页")
    day = as_of.isoformat()
    ids = [_first(row.get(ROSTER_FIELDS["id"])) for row in rows]
    blockers, warnings, info = [], [], []
    duplicated = sorted({i for i in ids if i and ids.count(i) > 1})
    if duplicated:
        blockers.append({"code": "staff_roster_duplicate_ids", "message": f"名录里人员ID重复：{duplicated[:5]}，无法确定人数"})
    if any(not i for i in ids):
        blockers.append({"code": "staff_roster_blank_id", "message": "名录里有没有人员ID的行，无法确定人数"})
    by_brigade, outside = {}, {}
    for row in rows:
        brigade = BRIGADE_ALIASES.get(_first(row.get(ROSTER_FIELDS["brigade"])), _first(row.get(ROSTER_FIELDS["brigade"])))
        start, end = _day(row.get(ROSTER_FIELDS["start"])), _day(row.get(ROSTER_FIELDS["end"]))
        if not brigade or (start and end and start > end):
            warnings.append({"code": "staff_roster_row_invalid", "message": f"名录有一行缺大队或有效期止早于起（{_first(row.get(ROSTER_FIELDS['id']))}），按无效不计"})
            continue
        if (start or "0000-00-00") <= day <= (end or "9999-12-31"):
            by_brigade[brigade] = by_brigade.get(brigade, 0) + 1
    groups = {}
    for brigade, count in by_brigade.items():
        if brigade not in required:
            outside[brigade] = count
            continue
        groups[brigade] = {"count": count, "label": f"飞书名录{count}人", "label_count": count, "start_row": None, "name_rows": [], "duplicated_names": []}
    for brigade in required:
        if brigade not in groups:
            blockers.append({"code": "staff_roster_no_names", "brigade": brigade, "message": f"{brigade} 在 {day} 没有有效人员（名录没有该队人员或有效期不覆盖）"})
    if outside:
        info.append({"code": "roster_outside_table", "brigades": outside, "message": "以下大队不在月度统计表八个大队行内，仅报告，不扩行、不写入 B 列"})
    counts = {b: g["count"] for b, g in groups.items()}
    return {"source": str(path), "sha256": sha.upper(), "sheet": f"飞书名录快照（{day} 有效）", "stable_during_read": sha == _sha256(path),
            "groups": groups, "counts": counts, "outside_table": outside, "blockers": blockers, "warnings": warnings, "info": info, "as_of": day}


def load_roster(roster_path, snapshot_dir, year, month, entry, required=BRIGADE_ORDER):
    """The roster of one statistics month: an explicitly given spreadsheet, otherwise the Feishu roster snapshot (valid on the 25th)."""
    if roster_path:
        return read_roster(Path(roster_path), entry.get("sheet", "Sheet1"), entry.get("group_label_pattern", DEFAULT_LABEL_PATTERN), required)
    if not snapshot_dir:
        raise RosterError("没有指定飞书统计快照目录，也没有显式的人员总表；人数源是快照里的执法人员名录")
    from datetime import date
    return read_feishu_roster(snapshot_dir, date(int(year), int(month), 25), required)


def main():
    parser = argparse.ArgumentParser(description="只读校验大队执法人员总表，输出各大队人数（以姓名数为准）。")
    parser.add_argument("--roster", help="显式人员总表路径（旧 xlsx）；缺省读飞书统计快照里的执法人员名录")
    parser.add_argument("--snapshot", help="飞书统计快照目录；缺省按配置和环境变量")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True, help="统计月份：人数取该统计月 25 日有效的名录")
    args = parser.parse_args()
    import monthly_workflow as workflow  # lazy: keeps this module importable without the workflow config

    config = workflow.load_config()["data_sources"]["staff_roster"]
    snapshot = args.snapshot or workflow.case_source_path(workflow.load_config())
    try:
        result = load_roster(args.roster, snapshot, args.year, args.month, config)
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
