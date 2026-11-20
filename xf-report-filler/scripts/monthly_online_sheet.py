"""R01 在线表维护分支：把本地科室月考核记录表中本项目负责的 24 格同步到金山文档在线表。

默认只读（plan / reconcile）；只有 apply 且在线读写通过后才写。只写内容，不改在线行列、合并和格式。
在线表链接、文件 ID 属本机执行输入，从参数或环境变量传入，不写进 Skill。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import host_paths
import monthly_workflow as workflow

SNAPSHOT_SCHEMA = "online-sheet-snapshot/1"
PLAN_SCHEMA = "online-sheet-plan/1"
MAX_ROWS = 200
MAX_COLS = 40
FORMULA_PREFIXES = ("=", "+", "-", "@")  # 官方 CLI/页面可能把这些开头的文本当公式解析：一律阻断，不猜解析行为
PLAN_ACTIONS = {"write", "no_change", "skip_source_blank", "skip_online_formula", "skip_protected"}


class SyncError(Exception):
    """带稳定 code 的业务停止条件。"""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rules(config=None):
    return dict((config or workflow.load_config())["online_sheet_rules"])


def norm_text(value):
    """单元格值标准化：只统一换行；反斜杠、前后空白按字面保留；全空白视为空。"""
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return None if text.strip() == "" else text


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_url(url):
    """文档身份用：协议+主机+路径，去掉查询、片段和末尾斜杠。"""
    if not url:
        return None
    parts = urlsplit(str(url).strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def parse_period(path):
    """从文件名读本地已知的 年/月（如“2026年11月”）；读不到返回 None，不猜。"""
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", Path(str(path).replace("\\", "/")).name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_period_arg(text):
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", str(text).strip())
    if not match:
        raise SyncError("bad_source_period", f"--source-period 需要 YYYY-MM，得到 {text!r}")
    return int(match.group(1)), int(match.group(2))


def resolve_path(text):
    """计划里记录的源路径在当前主机上的实际路径（Windows D:\\… 与 WSL /mnt/d/… 互转）。"""
    return Path(host_paths.wsl_path(text) if os.name != "nt" else host_paths.win_path(text))


def plan_fingerprint(plan):
    """计划指纹：覆盖源哈希、年月、文档/工作表身份和每格动作。Python 与 ego_online_sheet.mjs 同算法。"""
    online = plan["online"]
    parts = [
        "online-sheet-plan-fp/1",
        plan["source"]["sha256"],
        str(plan["year"]),
        str(plan["month"]),
        str(online.get("sheet_name")),
        str(online.get("worksheet_id")),
        online.get("url_key") or "",
    ]
    for item in sorted(plan["items"], key=lambda i: (i["brigade"], i["item"])):
        parts.append(
            "\x1f".join(
                [
                    item["brigade"],
                    item["item"],
                    item["local_cell"],
                    item.get("online_cell") or "",
                    "\x00" if item.get("local_value") is None else item["local_value"],
                    "\x00" if item.get("baseline") is None else item["baseline"],
                    item["action"],
                    "1" if item.get("local_pending_fill") else "0",
                ]
            )
        )
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def coord(row, col):
    return f"{get_column_letter(col)}{row}"


def split_coord(text):
    letters, row = coordinate_from_string(text)
    return int(row), column_index_from_string(letters)


# ---------------------------------------------------------------- 定位（本地与在线共用）


def locate(grid, rule):
    """在 {(row, col): value} 中按文字定位八个大队列和三个项目行；返回 (mapping, blockers)。"""
    blockers = []
    names = rule["brigades"]
    by_row = {}
    for (row, col), value in grid.items():
        text = norm_text(value)
        if text is not None and text.strip() in names:
            by_row.setdefault(row, []).append((col, text.strip()))
    header_rows = [row for row, cells in by_row.items() if {n for _, n in cells} == set(names)]
    if len(header_rows) != 1:
        code = "header_missing" if not header_rows else "header_ambiguous"
        return None, [{"code": code, "detail": f"含全部大队名的行：{sorted(header_rows)}"}]
    header_row = header_rows[0]
    cols = {}
    for col, name in by_row[header_row]:
        if name in cols:
            blockers.append({"code": "brigade_duplicate", "detail": f"{name} 在表头重复出现"})
        cols[name] = col
    if blockers:
        return None, blockers
    first_col = min(cols.values())
    rows = {}
    for item in rule["items"]:
        hits = sorted(
            {
                row
                for (row, col), value in grid.items()
                if row > header_row and col < first_col and (norm_text(value) or "").strip() == item
            }
        )
        if len(hits) != 1:
            code = "item_missing" if not hits else "item_ambiguous"
            blockers.append({"code": code, "detail": f"{item} 命中行 {hits}"})
        else:
            rows[item] = hits[0]
    if blockers:
        return None, blockers
    return {"header_row": header_row, "brigade_cols": cols, "item_rows": rows}, []


# ---------------------------------------------------------------- 本地源


def read_local(path, rule, period=None):
    path = Path(path)
    sha = file_sha256(path)
    wb = load_workbook(path)
    candidates = []
    for ws in wb.worksheets:
        grid = {(c.row, c.column): c.value for row in ws.iter_rows() for c in row if c.value is not None}
        mapping, _ = locate(grid, rule)
        if mapping:
            candidates.append((ws, grid, mapping))
    if len(candidates) != 1:
        raise SyncError("local_sheet_ambiguous" if candidates else "local_sheet_missing", f"本地可用工作表 {len(candidates)} 个")
    ws, grid, mapping = candidates[0]
    cells = {}
    for brigade, col in mapping["brigade_cols"].items():
        for item, row in mapping["item_rows"].items():
            cell = ws.cell(row=row, column=col)
            raw = cell.value
            if isinstance(raw, str) and raw.startswith("="):
                raise SyncError("local_formula", f"{cell.coordinate} 本地为公式，不能按文本同步")
            fill = cell.fill
            red = bool(fill and fill.fill_type == "solid" and str(fill.fgColor.rgb).upper().endswith("FF0000"))
            cells[(brigade, item)] = {
                "cell": cell.coordinate,
                "value": norm_text(raw),
                "pending_fill": red,
            }
    known = tuple(period) if period else parse_period(path)
    return {
        "path": str(path),
        "sha256": sha,
        "sheet": ws.title,
        "mapping": mapping,
        "cells": cells,
        "period": list(known) if known else None,
        "period_source": "explicit" if period else ("filename" if known else None),
    }


# ---------------------------------------------------------------- 在线快照


def snapshot_grid(snapshot):
    grid = {}
    for key, cell in snapshot["cells"].items():
        value = cell.get("f") or cell.get("v")
        if norm_text(value) is not None:
            grid[split_coord(key)] = value
    return grid


def check_identity(snapshot, rule, year, month, sheet_name=None):
    """核对年份文档、工作表存在且唯一；返回 blockers。"""
    blockers = []
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        blockers.append({"code": "snapshot_schema", "detail": str(snapshot.get("schema"))})
    title = (snapshot.get("identity") or {}).get("title") or ""
    markers = [m.format(year=year, yy=str(year)[-2:]) for m in rule["title_year_markers"]]
    if not any(m in title for m in markers):
        blockers.append({"code": "title_year_mismatch", "detail": f"标题 {title!r} 不含 {markers}"})
    if not (isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12):
        blockers.append({"code": "bad_period_arg", "detail": f"{year!r}-{month!r}"})
        return blockers
    want = rule["sheet_name_pattern"].format(month=month)
    if sheet_name and sheet_name != want:
        blockers.append({"code": "sheet_name_override_conflict", "detail": f"--sheet-name {sheet_name!r} 与目标月份页 {want!r} 不一致；明确月份不能被覆盖"})
    names = [s["name"] for s in snapshot.get("sheets", [])]
    if names.count(want) != 1:
        blockers.append({"code": "sheet_not_unique", "detail": f"工作表 {want!r} 出现 {names.count(want)} 次，现有 {names}"})
    elif snapshot.get("sheet_name") != want:
        blockers.append({"code": "snapshot_sheet_mismatch", "detail": f"快照读取的是 {snapshot.get('sheet_name')!r}"})
    return blockers


def blank_snapshot(identity, sheets, sheet_name, worksheet_id):
    return {
        "schema": SNAPSHOT_SCHEMA,
        "identity": dict(identity),
        "sheets": sheets,
        "sheet_name": sheet_name,
        "worksheet_id": worksheet_id,
        "used_range": {"rowTo": 0, "colTo": 0},
        "cells": {},
        "protected": [],
    }


def snapshot_from_range(identity, sheets_info, sheet_name, range_detail, protected=()):
    """把 kdocs-cli 的 get-sheets-info / get-range-data 返回规整成快照（坐标 0 起转 1 起）。"""
    infos = [s for s in sheets_info if s.get("sheetName") == sheet_name]
    sheets = [{"name": s.get("sheetName"), "id": s.get("sheetId")} for s in sheets_info]
    sheet_id = infos[0].get("sheetId") if len(infos) == 1 else None
    snap = blank_snapshot(identity, sheets, sheet_name, sheet_id)
    for item in range_detail:
        row, col = item.get("rowFrom", 0) + 1, item.get("colFrom", 0) + 1
        formula = item.get("fmlaText")
        original = item.get("originalCellValue", item.get("cellText"))
        value = None if original is None else str(original)
        snap["cells"][coord(row, col)] = {"v": value, "f": formula or None}
        snap["used_range"]["rowTo"] = max(snap["used_range"]["rowTo"], row)
        snap["used_range"]["colTo"] = max(snap["used_range"]["colTo"], col)
    snap["protected"] = sorted(protected)
    return snap


# ---------------------------------------------------------------- 差异计划


def build_plan(local, snapshot, year, month, rule=None, sheet_name=None):
    rule = rule or rules()
    blockers = check_identity(snapshot, rule, year, month, sheet_name)
    period = local.get("period")
    if not period:
        blockers.append({"code": "local_period_unknown", "detail": "本地源文件名读不出年月；确认后用 --source-period YYYY-MM 明确声明，不猜"})
    elif tuple(period) != (year, month):
        blockers.append({"code": "period_mismatch", "detail": f"本地源是 {period[0]}年{period[1]}月，执行参数是 {year}年{month}月"})
    mapping, found = locate(snapshot_grid(snapshot), rule)
    blockers += found
    items = []
    protected = set(snapshot.get("protected", []))
    for (brigade, item), src in local["cells"].items():
        entry = {
            "brigade": brigade,
            "item": item,
            "local_cell": src["cell"],
            "local_value": src["value"],
            "local_pending_fill": src["pending_fill"],
        }
        if mapping:
            online = coord(mapping["item_rows"][item], mapping["brigade_cols"][brigade])
            cell = snapshot["cells"].get(online, {})
            current = norm_text(cell.get("v"))
            entry.update(online_cell=online, online_value=current, baseline=current)
            if src["value"] is None:
                entry["action"] = "skip_source_blank"
            elif src["value"].startswith(FORMULA_PREFIXES):
                entry["action"] = "blocked_formula_like"
                blockers.append({"code": "value_looks_like_formula", "detail": f"{brigade}/{item} 以 {src['value'][0]!r} 开头，可能被在线表按公式解析"})
            elif cell.get("f"):
                entry["action"] = "skip_online_formula"
            elif online in protected:
                entry["action"] = "skip_protected"
            elif src["value"] == current:
                entry["action"] = "no_change"
            else:
                entry["action"] = "write"
        else:
            entry["action"] = "blocked"
        items.append(entry)
    if blockers:  # 有阻断时没有任何格可写
        for entry in items:
            if entry["action"] == "write":
                entry["action"] = "blocked"
    counts = {}
    for entry in items:
        counts[entry["action"]] = counts.get(entry["action"], 0) + 1
    identity = dict(snapshot.get("identity") or {})
    plan = {
        "schema": PLAN_SCHEMA,
        "created_at": now_iso(),
        "year": year,
        "month": month,
        "status": "blocked" if blockers else "ok",
        "blockers": blockers,
        "source": {
            "path": local["path"],
            "sha256": local["sha256"],
            "sheet": local["sheet"],
            "period": local.get("period"),
            "period_source": local.get("period_source"),
        },
        "online": {
            "identity": identity,
            "url_key": norm_url(identity.get("url")),
            "file_id": identity.get("file_id"),
            "sheet_name": snapshot.get("sheet_name"),
            "worksheet_id": snapshot.get("worksheet_id"),
            "sheets": snapshot.get("sheets"),
        },
        "mapping": mapping,
        "summary": counts,
        "items": items,
        "not_covered": [
            "除所列单元格外的在线内容（其他人维护的行列、其他工作表）不读取判断、不写入",
            "在线格式默认不改；浏览器分支只对本地待补红底格新增红色，CLI 分支不同步格式",
            "官方 API 没有原子比较并写：写前近时复核只能缩小并发窗口，不能声称绝对消除极短竞争",
        ],
    }
    plan["fingerprint"] = plan_fingerprint(plan) if mapping else None
    return plan


def validate_plan(plan, rule, year, month, fresh=None, check_source=True):
    """严格重算：固定业务 8 队 × 3 项、计划完整唯一、年月/文档/工作表/指纹、源对应；不只信计划里的坐标。"""
    errors = []

    def bad(code, detail=""):
        errors.append({"code": code, "detail": detail})

    if plan.get("schema") != PLAN_SCHEMA:
        bad("plan_schema", str(plan.get("schema")))
    if plan.get("status") != "ok":
        bad("plan_blocked", json.dumps(plan.get("blockers"), ensure_ascii=False))
    if not (isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12):
        bad("bad_period_arg", f"{year!r}-{month!r}")
    if plan.get("year") != year or plan.get("month") != month:
        bad("plan_period_mismatch", f"计划 {plan.get('year')}-{plan.get('month')}，执行参数 {year}-{month}")
    source = plan.get("source") or {}
    if source.get("period") != [year, month]:
        bad("source_period_mismatch", f"计划记录的源年月 {source.get('period')}，执行参数 {year}-{month}")
    online = plan.get("online") or {}
    want_sheet = rule["sheet_name_pattern"].format(month=month)
    if online.get("sheet_name") != want_sheet:
        bad("plan_sheet_mismatch", f"计划工作表 {online.get('sheet_name')!r}，应为 {want_sheet!r}")
    items = plan.get("items")
    if not isinstance(items, list):
        bad("items_missing")
        raise SyncError("plan_invalid", json.dumps(errors, ensure_ascii=False))
    expected_pairs = {(b, i) for b in rule["brigades"] for i in rule["items"]}
    pairs = [(i.get("brigade"), i.get("item")) for i in items]
    if len(items) != len(expected_pairs) or set(pairs) != expected_pairs or len(set(pairs)) != len(pairs):
        bad("items_not_whitelist", f"计划 {len(items)} 项，应恰为 {len(expected_pairs)} 项固定业务白名单")
    for key in ("online_cell", "local_cell"):
        values = [i.get(key) for i in items]
        if len(set(values)) != len(values) or not all(isinstance(v, str) and re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,3}", v) for v in values):
            bad("cells_invalid", f"{key} 缺失、格式不对或重复")
    for entry in items:
        if entry.get("action") not in PLAN_ACTIONS:
            bad("action_invalid", f"{entry.get('brigade')}/{entry.get('item')}: {entry.get('action')}")
        if entry.get("action") == "write":
            value = entry.get("local_value")
            if not isinstance(value, str) or norm_text(value) is None or value.startswith(FORMULA_PREFIXES) or value == entry.get("baseline"):
                bad("write_value_invalid", f"{entry.get('brigade')}/{entry.get('item')}")
    if errors:
        raise SyncError("plan_invalid", json.dumps(errors, ensure_ascii=False))
    if plan.get("fingerprint") != plan_fingerprint(plan):
        bad("fingerprint_mismatch", "计划内容与生成时指纹不符（坐标、值、年月、文档或动作被改过）")
    if fresh is not None:
        mapping, found = locate(snapshot_grid(fresh), rule)
        if found or mapping != plan.get("mapping"):
            bad("mapping_drift", json.dumps(found, ensure_ascii=False) or "在线布局与计划映射不同")
        elif any(e["online_cell"] != coord(mapping["item_rows"][e["item"]], mapping["brigade_cols"][e["brigade"]]) for e in items):
            bad("online_cell_not_semantic", "计划里的在线坐标不等于按表头/项目名重算的坐标")
    if check_source:
        try:
            path = resolve_path(source.get("path"))
            sha = file_sha256(path)
        except OSError as exc:
            raise SyncError("source_unreadable", f"{source.get('path')}: {exc}") from exc
        if sha != source.get("sha256"):
            bad("source_changed", "本地源哈希与计划不同，重新 plan")
        else:
            local = read_local(path, rule, period=source.get("period"))
            for entry in items:
                src = local["cells"].get((entry["brigade"], entry["item"]))
                if not src or src["cell"] != entry["local_cell"] or src["value"] != entry["local_value"] or src["pending_fill"] != bool(entry["local_pending_fill"]):
                    bad("source_mismatch", f"{entry['brigade']}/{entry['item']} 与本地源重读结果不一致")
    if errors:
        raise SyncError("plan_invalid", json.dumps(errors, ensure_ascii=False))


def check_binding(plan, fresh):
    """原 URL/文件 ID、工作表名与 ID、标题必须与写前再读到的一致；返回 blockers。"""
    blockers = []
    online = plan["online"]
    identity = fresh.get("identity") or {}
    if norm_url(identity.get("url")) != online.get("url_key"):
        blockers.append({"code": "document_url_mismatch", "detail": f"计划 {online.get('url_key')}，现读 {norm_url(identity.get('url'))}"})
    if online.get("file_id") and identity.get("file_id") and online["file_id"] != identity["file_id"]:
        blockers.append({"code": "document_id_mismatch", "detail": "文件 ID 不同"})
    if (online.get("identity") or {}).get("title") != identity.get("title"):
        blockers.append({"code": "document_title_mismatch", "detail": f"{(online.get('identity') or {}).get('title')!r} → {identity.get('title')!r}"})
    if fresh.get("sheet_name") != online.get("sheet_name") or fresh.get("worksheet_id") != online.get("worksheet_id"):
        blockers.append({"code": "worksheet_mismatch", "detail": f"计划 {online.get('sheet_name')}#{online.get('worksheet_id')}，现读 {fresh.get('sheet_name')}#{fresh.get('worksheet_id')}"})
    return blockers


# ---------------------------------------------------------------- 读写适配（kdocs-cli）


class CliSheet:
    """官方 kdocs-cli 的最薄封装；runner 可注入以便测试。命令经标准输入传 UTF-8 JSON。"""

    def __init__(self, binary, url, runner=None):
        self.binary = binary
        self.url = url
        self.runner = runner or self._run

    @staticmethod
    def _run(argv, payload):
        done = subprocess.run(
            argv,
            input=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")

    def call(self, service, action, payload=None):
        argv = [self.binary, service, action] + (["-"] if payload is not None else [])
        code, out, err = self.runner(argv, payload)
        if code != 0:
            raise SyncError("cli_failed", f"{service} {action} exit={code} {err.strip()[:200]}")
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise SyncError("cli_unparsable", f"{service} {action}: {exc}") from exc

    def authenticated(self):
        argv = [self.binary, "auth", "status"]
        code, out, _ = self.runner(argv, None)
        try:
            return code == 0 and bool(json.loads(out).get("authenticated"))
        except json.JSONDecodeError:
            return False

    @staticmethod
    def _dig(obj, key):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for value in obj.values():
                found = CliSheet._dig(value, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = CliSheet._dig(value, key)
                if found is not None:
                    return found
        return None

    def file_info(self):
        info = self.call("drive", "get-file-info", {"url": self.url})
        title = next((self._dig(info, k) for k in ("fname", "name", "file_name") if isinstance(self._dig(info, k), str) and self._dig(info, k)), None)
        if not title:
            raise SyncError("title_unreadable", "get-file-info 未返回文件名")
        file_id = self._dig(info, "file_id")
        return title, file_id if isinstance(file_id, str) and file_id else None

    def read(self, sheet_name):
        sheets_info = self._dig(self.call("sheet", "get-sheets-info", {"url": self.url}), "sheetsInfo") or []
        match = [s for s in sheets_info if s.get("sheetName") == sheet_name]
        title, file_id = self.file_info()
        identity = {"url": self.url, "title": title, "reader": "kdocs-cli", "captured_at": now_iso()}
        if file_id:
            identity["file_id"] = file_id
        if len(match) != 1:
            return snapshot_from_range(identity, sheets_info, sheet_name, [])
        info = match[0]
        row_to = min(int(info.get("rowTo", 0)), MAX_ROWS - 1)
        col_to = min(int(info.get("colTo", 0)), MAX_COLS - 1)
        payload = {
            "url": self.url,
            "worksheet_id": info["sheetId"],
            "range": {"rowFrom": 0, "rowTo": row_to, "colFrom": 0, "colTo": col_to},
        }
        detail = self._dig(self.call("sheet", "get-range-data", payload), "rangeData") or []
        return snapshot_from_range(identity, sheets_info, sheet_name, detail)

    def write(self, worksheet_id, cells):
        """cells: [(A1 坐标, 文本)]，每格一条内容操作，不跨格填充。"""
        ops = []
        for key, text in cells:
            row, col = split_coord(key)
            ops.append(
                {
                    "op_type": "cell_operation_type_formula",
                    "row_from": row - 1,
                    "row_to": row - 1,
                    "col_from": col - 1,
                    "col_to": col - 1,
                    "formula": text,
                }
            )
        return self.call("sheet", "range-data-batch-update", {"url": self.url, "worksheet_id": worksheet_id, "range_data": ops})


# ---------------------------------------------------------------- 应用与对账


def write_json(path, data):
    path = Path(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_state(path):
    """状态文件不存在返回 None；存在但读不出/结构不对一律 fail closed，不当作没有状态。"""
    path = Path(path)
    if not path.exists():
        return None
    try:
        state = read_json(path)
    except (OSError, ValueError, UnicodeError) as exc:
        raise SyncError("state_corrupt", f"{path.name} 损坏，保留原文件，不重跑：{exc}") from exc
    if not isinstance(state, dict) or state.get("status") not in {"writing", "needs_reconcile", "done"}:
        raise SyncError("state_corrupt", f"{path.name} 结构不对，保留原文件，不重跑")
    return state


def apply_plan(plan, reader, writer, evidence_dir, year, month, rule=None, sheet_name=None):
    """写前严格重算白名单并绑定文档/工作表/源，再读两次核基线，仅写仍一致的不同格；写后回读。

    官方 API 没有原子“比较并写”：近时复核只能缩小并发窗口。未知结果落状态文件，不重放。"""
    rule = rule or rules()
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    state_path = evidence / "apply-state.json"
    state = read_state(state_path)
    if state is not None and state["status"] != "done":
        raise SyncError("unknown_prior_write", "上次写入未完成或结果未知，先运行 reconcile，不重放")
    if plan.get("status") != "ok":
        raise SyncError("plan_blocked", json.dumps(plan.get("blockers"), ensure_ascii=False))
    validate_plan(plan, rule, year, month)
    want_sheet = plan["online"]["sheet_name"]
    if sheet_name and sheet_name != want_sheet:
        raise SyncError("sheet_name_override_conflict", f"{sheet_name!r} 不是目标月份页 {want_sheet!r}")

    def read_checked():
        fresh = reader.read(want_sheet)
        problems = check_identity(fresh, rule, year, month) + check_binding(plan, fresh)
        try:
            validate_plan(plan, rule, year, month, fresh=fresh, check_source=False)
        except SyncError as exc:
            problems.append({"code": exc.code, "detail": exc.detail})
        if problems:
            raise SyncError("binding_or_mapping_drift", json.dumps(problems, ensure_ascii=False))
        return fresh

    def classify(fresh):
        protected = set(fresh.get("protected", []))
        to_write, conflicts, skipped = [], [], []
        for entry in plan["items"]:
            brief = {"brigade": entry["brigade"], "item": entry["item"], "online_cell": entry["online_cell"]}
            if entry["action"] != "write":
                skipped.append({**brief, "action": entry["action"]})
                continue
            cell = fresh["cells"].get(entry["online_cell"], {})
            now_value = norm_text(cell.get("v"))
            if cell.get("f") or entry["online_cell"] in protected:
                skipped.append({**brief, "action": "skip_protected_or_formula"})
            elif now_value == entry["local_value"]:
                skipped.append({**brief, "action": "already_equal"})
            elif now_value != entry["baseline"]:
                conflicts.append({**brief, "baseline": entry["baseline"], "now": now_value})
            else:
                to_write.append(entry)
        return to_write, conflicts, skipped

    fresh = read_checked()
    to_write, conflicts, skipped = classify(fresh)
    if to_write:  # 写前再读一次，缩小并发窗口（无原子 CAS，不能消除极短竞争）
        recheck = read_checked()
        still, late, _ = classify(recheck)
        keep = {e["online_cell"] for e in still}
        conflicts += [c for c in late if c["online_cell"] in {e["online_cell"] for e in to_write}]
        to_write = [e for e in to_write if e["online_cell"] in keep]
        fresh = recheck

    write_json(evidence / "before.json", fresh)
    write_json(evidence / "recovery.json", {e["online_cell"]: e["baseline"] for e in to_write})
    result = {
        "started_at": now_iso(),
        "plan_fingerprint": plan["fingerprint"],
        "conflicts": conflicts,
        "skipped": skipped,
        "written": [],
        "verified": [],
        "mismatch": [],
        "write_error": None,
        "outside_target_changes": [],
        "recovery": "evidence/recovery.json 记录每个被写单元格写前原值；按格写回原值即恢复，空值格需人工清空",
        "limits": "官方 API 无原子比较并写；写前近时复核不能绝对消除极短并发竞争",
    }
    binding = {
        "plan_fingerprint": plan["fingerprint"],
        "url_key": plan["online"]["url_key"],
        "sheet_name": want_sheet,
        "worksheet_id": plan["online"]["worksheet_id"],
        "source_sha256": plan["source"]["sha256"],
        "year": year,
        "month": month,
    }
    if to_write:
        write_json(state_path, {**binding, "status": "writing", "cells": [e["online_cell"] for e in to_write], "at": now_iso()})
        try:
            writer.write(fresh["worksheet_id"], [(e["online_cell"], e["local_value"]) for e in to_write])
        except Exception as exc:  # noqa: BLE001 结果未知：只回读，不重试
            result["write_error"] = f"{type(exc).__name__}: {exc}"
    after = reader.read(want_sheet)
    write_json(evidence / "after.json", after)
    wrote = {e["online_cell"] for e in to_write}
    for entry in to_write:
        got = norm_text(after["cells"].get(entry["online_cell"], {}).get("v"))
        record = {"brigade": entry["brigade"], "item": entry["item"], "online_cell": entry["online_cell"]}
        if got == entry["local_value"]:
            result["verified"].append(record)
        else:
            result["mismatch"].append({**record, "got": got})
        result["written"].append(record)
    for key in sorted(set(fresh["cells"]) | set(after["cells"])):
        if key in wrote:
            continue
        old, new = norm_text(fresh["cells"].get(key, {}).get("v")), norm_text(after["cells"].get(key, {}).get("v"))
        if old != new:
            result["outside_target_changes"].append({"cell": key, "before": old, "after": new, "note": "非本次写入范围，按并发修改处理"})
    ok = not result["write_error"] and not result["mismatch"]
    result["status"] = "done" if ok else "needs_reconcile"
    result["finished_at"] = now_iso()
    write_json(evidence / "result.json", result)
    if to_write:
        write_json(state_path, {**binding, "status": result["status"], "cells": sorted(wrote), "at": now_iso()})
    return result


def reconcile(plan, reader, evidence_dir):
    """只读对账：必须绑定原 apply-state 的计划指纹、文档/工作表和真实待确认格。

    条件不全（无状态、状态损坏、错计划、错文档、无待确认格）一律报错，原状态不动；
    只有全部待确认格都已到位才把状态标为 done。"""
    evidence = Path(evidence_dir)
    state_path = evidence / "apply-state.json"
    state = read_state(state_path)
    if state is None:
        raise SyncError("no_apply_state", "该目录没有 apply-state.json，没有可对账的写入")
    if state["status"] == "done":
        raise SyncError("nothing_to_reconcile", "状态已是 done")
    rule = rules()
    if plan.get("fingerprint") != plan_fingerprint(plan) or state.get("plan_fingerprint") != plan.get("fingerprint"):
        raise SyncError("reconcile_plan_mismatch", "计划指纹与原写入状态不符，不能用它解除状态")
    cells = state.get("cells")
    by_cell = {i["online_cell"]: i for i in plan["items"] if i.get("action") == "write"}
    if not isinstance(cells, list) or not cells or any(c not in by_cell for c in cells):
        raise SyncError("reconcile_no_pending_cells", "状态里没有可对应到计划 write 项的待确认格")
    online = plan["online"]
    if (state.get("url_key"), state.get("sheet_name"), state.get("worksheet_id")) != (online.get("url_key"), online.get("sheet_name"), online.get("worksheet_id")):
        raise SyncError("reconcile_document_mismatch", "状态与计划绑定的文档/工作表不同")
    snap = reader.read(online["sheet_name"])
    problems = check_binding(plan, snap) + check_identity(snap, rule, plan["year"], plan["month"])
    if problems:
        raise SyncError("reconcile_document_mismatch", json.dumps(problems, ensure_ascii=False))
    report = {"checked_at": now_iso(), "plan_fingerprint": plan["fingerprint"], "cells": []}
    for cell in cells:
        entry = by_cell[cell]
        got = norm_text(snap["cells"].get(cell, {}).get("v"))
        report["cells"].append({"online_cell": cell, "equals_local": got == entry["local_value"], "equals_baseline": got == entry["baseline"]})
    report["all_in_place"] = all(c["equals_local"] for c in report["cells"])
    report["state_cleared"] = report["all_in_place"]
    write_json(evidence / "reconcile.json", report)
    if report["all_in_place"]:
        write_json(state_path, {**state, "status": "done", "at": now_iso(), "via": "reconcile"})
    return report


# ---------------------------------------------------------------- 命令行


def build_reader(args):
    if args.snapshot:
        class FileReader:
            def read(self, sheet_name):
                return read_json(args.snapshot)

        return FileReader(), None
    binary = args.kdocs_cli or os.environ.get("KDOCS_CLI")
    url = args.online_url or os.environ.get("KDOCS_ONLINE_URL")
    if not binary or not url:
        raise SyncError("online_input_missing", "需要 --snapshot，或 --kdocs-cli 与 --online-url（也可用 KDOCS_CLI / KDOCS_ONLINE_URL）")
    cli = CliSheet(binary, url)
    if not cli.authenticated():
        raise SyncError("not_authenticated", "kdocs-cli 未登录；由用户本人登录后重试，或改用浏览器 fallback 产出快照")
    return cli, cli


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-plan", help="只读：按本地源重算并核对 plan.json（浏览器分支交给 Ego 前必跑）")
    check.add_argument("--source", required=True)
    check.add_argument("--plan", required=True)
    check.add_argument("--year", type=int, required=True)
    check.add_argument("--month", type=int, required=True)
    check.add_argument("--snapshot", help="可选：再用这份在线快照核对语义坐标")
    for name in ("plan", "apply", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("--source", required=True, help="本地月考核记录表 xlsx（只读）")
        p.add_argument("--year", type=int, required=True)
        p.add_argument("--month", type=int, required=True)
        p.add_argument("--out", required=True, help="证据目录（任务临时目录）")
        p.add_argument("--snapshot", help="已有在线快照 JSON（浏览器或离线）")
        p.add_argument("--kdocs-cli")
        p.add_argument("--online-url")
        p.add_argument("--sheet-name", help="仅作断言：必须等于“N月情况”，不能用它改投别的月份页")
        p.add_argument("--source-period", help="本地源文件名读不出年月时，由用户明确声明 YYYY-MM；不会据此放过与 --year/--month 不符")
    args = parser.parse_args(argv)
    rule = rules()
    if args.command == "check-plan":
        try:
            plan = read_json(args.plan)
            if Path(args.source).resolve() != resolve_path(plan["source"]["path"]).resolve():
                raise SyncError("source_path_mismatch", "--source 与计划记录的源路径不是同一文件")
            validate_plan(plan, rule, args.year, args.month, fresh=read_json(args.snapshot) if args.snapshot else None)
            print(json.dumps({"status": "ok", "fingerprint": plan["fingerprint"], "items": len(plan["items"])}, ensure_ascii=False))
            return 0
        except SyncError as exc:
            print(json.dumps({"status": "stopped", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
            return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        local = read_local(args.source, rule, parse_period_arg(args.source_period) if args.source_period else None)
        reader, cli = build_reader(args)
        sheet = args.sheet_name or rule["sheet_name_pattern"].format(month=args.month)
        if args.command == "plan":
            snap = reader.read(sheet)
            plan = build_plan(local, snap, args.year, args.month, rule, args.sheet_name)
            write_json(out / "online-before.json", snap)
            write_json(out / "plan.json", plan)
            print(json.dumps({"status": plan["status"], "summary": plan["summary"], "blockers": plan["blockers"]}, ensure_ascii=False))
            return 0 if plan["status"] == "ok" else 2
        plan = read_json(out / "plan.json")
        if args.command == "reconcile":
            report = reconcile(plan, reader, out)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["all_in_place"] else 3
        if cli is None:
            raise SyncError("apply_needs_cli", "apply 只经官方 CLI 写入；浏览器 fallback 按 plan.json 的 write 项执行")
        if file_sha256(args.source) != plan["source"]["sha256"]:
            raise SyncError("source_changed", "本地源与计划时哈希不同，重新 plan")
        if Path(args.source).resolve() != resolve_path(plan["source"]["path"]).resolve():
            raise SyncError("source_path_mismatch", "--source 与计划记录的源路径不是同一文件")
        result = apply_plan(plan, cli, cli, out, args.year, args.month, rule, args.sheet_name)
        print(json.dumps({k: result[k] for k in ("status", "verified", "conflicts", "mismatch", "write_error")}, ensure_ascii=False))
        return 0 if result["status"] == "done" else 3
    except SyncError as exc:
        print(json.dumps({"status": "stopped", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
    sys.exit(main())
