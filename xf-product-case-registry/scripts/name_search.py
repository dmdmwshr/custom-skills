"""Find cases by name without being defeated by spaces or full-width forms (2026-11-12).

The supervision system records some unit names with spaces or mixed-width brackets
("无锡 蓝盾物业管理有限公司", "锡山区鼎力台球室（个体工商户 ）"). The stored name stays exactly as the
system shows it, locally and in Feishu. This module only builds a comparison key next to it, so a
search typed without the space still finds the case.

Two different stored names may share one key. They are listed side by side and never merged:
identity is the project number and RWID, a name only helps to locate candidates.

Standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

if __package__:
    from . import feishu_source_sync as sync
else:
    import feishu_source_sync as sync

SCHEMA = "CaseNameSearchReportV1"
CASE_TABLE = "案卷主表"
PROJECT_NO = re.compile(r"\d{8}[A-Z]\d{9}")


def search_key(value: Any) -> str:
    """Comparison key: full-width forms folded (NFKC), every kind of whitespace removed, case ignored."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def same_name(left: Any, right: Any) -> bool:
    """True when two names differ at most in whitespace or character width. Empty never matches."""
    key = search_key(left)
    return bool(key) and key == search_key(right)


def find(rows: list[dict[str, Any]], query: str) -> dict[str, Any]:
    """Match ``query`` against rows of {项目编号, 单位名称, ...}; returns every candidate with its stored text."""
    wanted = search_key(query)
    if not wanted:
        raise ValueError("检索词为空")
    matches: list[dict[str, Any]] = []
    for row in rows:
        name, project = row.get("单位名称"), row.get("项目编号")
        key = search_key(name)
        if isinstance(project, str) and wanted == search_key(project):
            how = "项目编号相同"
        elif isinstance(name, str) and name == query:
            how = "名称逐字相同"
        elif key and key == wanted:
            how = "忽略空格和全半角后相同"
        elif key and wanted in key:
            how = "忽略空格和全半角后包含"
        else:
            continue
        matches.append({**row, "match": how})
    order = ["项目编号相同", "名称逐字相同", "忽略空格和全半角后相同", "忽略空格和全半角后包含"]
    matches.sort(key=lambda m: (order.index(m["match"]), str(m.get("项目编号") or "")))
    by_key: dict[str, set[str]] = {}
    for match in matches:
        if isinstance(match.get("单位名称"), str):
            by_key.setdefault(search_key(match["单位名称"]), set()).add(match["单位名称"])
    return {"query": query, "queryKey": wanted, "count": len(matches), "matches": matches,
            "sameKeyDifferentText": [sorted(texts) for _, texts in sorted(by_key.items()) if len(texts) > 1]}


def find_cases(*, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any, query: str) -> dict[str, Any]:
    """Read 案卷主表 once (read-only) and search it. Names in the result are the stored text, untouched."""
    contract = sync.load_contract(contract_path, contract_sha)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    table_id = contract["tables"][CASE_TABLE]["tableId"]
    by_name = {meta["name"]: fid for fid, meta in sync.field_index(contract)[table_id].items()}
    missing = [name for name in ("项目编号", "单位名称") if name not in by_name]
    sync._require(not missing, f"合同里 {CASE_TABLE} 缺少字段：{'、'.join(missing)}")
    wanted = [name for name in ("项目编号", "单位名称", "所属大队", "初查日期") if name in by_name]
    rows = []
    for record_id, row in sorted(transport.list_rows(target["baseToken"], table_id, [by_name[n] for n in wanted]).items()):
        item: dict[str, Any] = {"recordId": record_id}
        for name in wanted:
            cell = row.get(by_name[name])
            item[name] = cell[0] if isinstance(cell, list) and len(cell) == 1 else cell
        rows.append(item)
    return {"schema": SCHEMA, "readAt": sync.now(), "externalCalls": "read-only", "rows": len(rows), **find(rows, query)}
