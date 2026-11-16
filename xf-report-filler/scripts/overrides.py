"""Month-scoped explicit overrides for generated deliverables (never for sources).

An overrides file is a small JSON the user's request authorises for ONE month. It lives in the
project's execution-record folder (see references/monthly/monthly_overrides.md), is passed with
``--overrides`` and never changes a source workbook, register or template. The mechanism is generic;
the real corrections of a month are data in that file, not code in this skill.

Schema (``xf-report-overrides/1``)::

    {"schema": "xf-report-overrides/1", "year": 2026, "month": 11,
     "authorization": "who/where decided this and the scope",
     "unit_name_fixes": [{"brigade": "梁溪大队", "sheet": "梁溪", "row": 1, "original": "...",
                           "corrected": "...", "context": "...", "judgement": "..."}],
     "deduction_remaps": [{"brigade": "宜兴大队", "match": "送达回执名称不准确", "from_index": 11,
                           "to_index": 12, "value": 0.2, "authorization": "..."}]}

Every entry must match exactly (brigade + sheet + row + original text; brigade + description text +
from_index + value). An entry that no longer matches the source is an error, not silently skipped.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

SCHEMA = "xf-report-overrides/1"


class OverrideError(ValueError):
    """The overrides file is malformed, for another month, or no longer matches the sources."""


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def load(path, year=None, month=None):
    path = Path(path)
    if not path.is_file():
        raise OverrideError(f"覆盖文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise OverrideError(f"覆盖文件不是合法 JSON：{path}（{error}）") from error
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise OverrideError(f"覆盖文件 schema 必须是 {SCHEMA}")
    if year is not None and data.get("year") != year or month is not None and data.get("month") != month:
        raise OverrideError(f"覆盖文件属于 {data.get('year')}-{data.get('month')}，与本次 {year}-{month} 不符，不能跨月使用")
    if not str(data.get("authorization") or "").strip():
        raise OverrideError("覆盖文件缺少 authorization（授权依据）")
    for item in data.get("unit_name_fixes", []):
        for key in ("brigade", "sheet", "row", "original", "corrected", "context", "judgement"):
            if item.get(key) in (None, ""):
                raise OverrideError(f"unit_name_fixes 条目缺少 {key}：{item}")
    for item in data.get("deduction_remaps", []):
        for key in ("brigade", "match", "from_index", "to_index", "value", "authorization"):
            if item.get(key) in (None, ""):
                raise OverrideError(f"deduction_remaps 条目缺少 {key}：{item}")
    data["_path"] = str(path)
    data["_sha256"] = _sha256(path)
    return data


def apply(overrides, monitor_details=None, product_records=None):
    """Return (monitor_details, product_records, report); inputs are copied, never mutated.

    Raises OverrideError when any entry does not match exactly once.
    """
    details = copy.deepcopy(monitor_details) if monitor_details is not None else None
    records = copy.deepcopy(product_records) if product_records is not None else None
    applied, unused = [], []
    for item in overrides.get("unit_name_fixes", []):
        matches = [
            d for d in (details or [])
            if d.get("大队") == item["brigade"] and d.get("sheet") == item["sheet"]
            and d.get("row") == item["row"] and d.get("单位") == item["original"]
        ]
        if len(matches) != 1:
            unused.append({"kind": "unit_name_fix", "entry": item, "matches": len(matches)})
            continue
        matches[0]["单位_原值"] = matches[0]["单位"]
        matches[0]["单位"] = item["corrected"]
        applied.append({"kind": "unit_name_fix", "brigade": item["brigade"], "sheet": item["sheet"], "row": item["row"],
                        "original": item["original"], "corrected": item["corrected"]})
    for item in overrides.get("deduction_remaps", []):
        matches = [
            (r, d) for r in (records or []) if r.get("大队") == item["brigade"]
            for d in r.get("deductions", [])
            if item["match"] in d.get("description", "") and d.get("general_index") == item["from_index"]
            and abs(float(d.get("value", 0)) - float(item["value"])) < 1e-9
        ]
        if len(matches) != 1:
            unused.append({"kind": "deduction_remap", "entry": item, "matches": len(matches)})
            continue
        deduction = matches[0][1]
        deduction["source_general_index"] = deduction["general_index"]
        deduction["general_index"] = item["to_index"]
        applied.append({"kind": "deduction_remap", "brigade": item["brigade"], "description": deduction["description"],
                        "from_index": item["from_index"], "to_index": item["to_index"], "value": item["value"]})
    report = {"path": overrides.get("_path"), "sha256": overrides.get("_sha256"), "authorization": overrides["authorization"],
              "applied": applied, "unused": unused}
    if unused:
        raise OverrideError("覆盖条目未命中源数据（源文件可能已变），不静默跳过：" + json.dumps(unused, ensure_ascii=False))
    return details, records, report
