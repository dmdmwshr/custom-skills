"""登记清单 Excel 导出（功能模块，不含业务判断）。

输入 ``RegisterReportV1`` JSON：``{schemaVersion, sheets:[{name, columns:[...], rows:[{列名: 值}], note?}]}``，
按给定顺序写成工作簿，首行为表头并冻结，列宽按内容取值。业务模块负责决定写哪些列、哪些表；本模块只负责
写出并保存后重新打开核对（表名、行数、表头与每格文本），任何不符即失败。已有同名文件不覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "RegisterReportV1"
MAX_WIDTH = 60


class ReportError(RuntimeError):
    pass


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float)):
        return value
    return str(value)


def _width(texts: list[str]) -> int:
    return min(MAX_WIDTH, max(8, max((sum(2 if ord(c) > 255 else 1 for c in line) for t in texts for line in (t.splitlines() or [""])), default=8) + 2))


def export_workbook(*, spec_path: Path, out_path: Path) -> dict[str, Any]:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or spec.get("schemaVersion") != SCHEMA or not spec.get("sheets"):
        raise ReportError(f"报表规格版本不支持(需要 {SCHEMA})")
    names = [s.get("name") for s in spec["sheets"]]
    if len(set(names)) != len(names) or any(not n or len(n) > 31 for n in names):
        raise ReportError("工作表名需唯一且不超过 31 字")
    out_path = Path(out_path)
    if out_path.exists():
        raise ReportError(f"已有同名文件，不覆盖：{out_path}")
    book = Workbook()
    book.remove(book.active)
    for sheet in spec["sheets"]:
        columns = list(sheet["columns"])
        if len(set(columns)) != len(columns):
            raise ReportError(f"{sheet['name']} 的列名重复")
        ws = book.create_sheet(sheet["name"])
        ws.append(columns)
        for row in sheet["rows"]:
            unknown = set(row) - set(columns)
            if unknown:
                raise ReportError(f"{sheet['name']} 有未声明的列：{sorted(unknown)}")
            ws.append([_cell(row.get(c)) for c in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for index, column in enumerate(columns, 1):
            letter = get_column_letter(index)
            ws.column_dimensions[letter].width = _width([column] + [str(_cell(r.get(column)) or "") for r in sheet["rows"]])
            for cell in ws[letter][1:]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    book.save(out_path)
    return verify_workbook(spec=spec, path=out_path, load=load_workbook)


def verify_workbook(*, spec: dict[str, Any], path: Path, load: Any = None) -> dict[str, Any]:
    """重新打开已保存文件，与规格逐表逐格比较；返回每表行数。"""
    if load is None:
        from openpyxl import load_workbook as load
    book = load(path)
    if book.sheetnames != [s["name"] for s in spec["sheets"]]:
        raise ReportError(f"工作表与规格不一致：{book.sheetnames}")
    counts: dict[str, int] = {}
    for sheet in spec["sheets"]:
        ws = book[sheet["name"]]
        rows = list(ws.iter_rows(values_only=True))
        if list(rows[0]) != list(sheet["columns"]) or len(rows) != len(sheet["rows"]) + 1:
            raise ReportError(f"{sheet['name']} 表头或行数与规格不一致")
        for got, want in zip(rows[1:], sheet["rows"]):
            expected = [_cell(want.get(c)) for c in sheet["columns"]]
            if [g if g is not None else None for g in got] != expected:
                raise ReportError(f"{sheet['name']} 内容与规格不一致：{got} != {expected}")
        counts[sheet["name"]] = len(sheet["rows"])
    return {"path": str(path), "sheets": counts, "reopened": True}
