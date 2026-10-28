#!/usr/bin/env python3
"""多维表格导入前预检与导出对账（标准库，只读，不调用任何飞书接口）。

  preflight_bitable.py check SPEC.json DATA.json     # 导入前检查本地标准数据
  preflight_bitable.py diff  SPEC.json LOCAL.json EXPORT.json   # 云端导出对账

SPEC: {"tables": {表名: {"primary_key": 列, "columns": {列: text|date|tri|number|relation|select},
       "relations": [{"column": 列, "target": 表, "required": false, "min_children": 0}],
       "tri_values": ["是","否","待核实"], "tri_evidence": {三态列: 依据列},
       "date_range": {日期列: ["2026-01-01","2026-12-31"]}, "expected_count": N}}}
DATA: {表名: [行对象, ...]}（日期用 ISO 文本 YYYY-MM-DD 或 null）。
退出码：0 无错误，1 有错误，2 输入不可读。输出 JSON 报告到标准输出。
"""
import datetime as dt
import json
import re
import sys

DEFAULT_TRI = ["是", "否", "待核实"]
ISO = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?$")


def parse_date(v):
    """返回 date；无法解析返回 None。接受 ISO、yyyy/mm/dd（可带时间）和 date/datetime。"""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        m = ISO.match(v.strip())
        if m:
            try:
                return dt.date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
    return None


def blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "") or v == []


def wrapped(v):
    return isinstance(v, dict) and ("value" in v or "source" in v)


def rel_keys(v):
    """关联值可能是单个键、列表或逗号/顿号分隔文本，统一成键集合。"""
    if blank(v):
        return set()
    if isinstance(v, (list, tuple)):
        out = set()
        for x in v:
            out |= rel_keys(x)
        return out
    if isinstance(v, dict):
        v = v.get("text") or v.get("value") or ""
    return {s.strip() for s in re.split(r"[,，、;；\n]", str(v)) if s.strip()}


def check(spec, data):
    issues = []

    def add(level, code, table, key, column, detail):
        issues.append({"level": level, "code": code, "table": table, "key": key, "column": column, "detail": detail})

    tables = spec["tables"]
    pks = {}
    for t, ts in tables.items():
        rows = data.get(t, [])
        pk = ts["primary_key"]
        seen = {}
        for i, r in enumerate(rows):
            k = r.get(pk)
            if blank(k):
                add("error", "pk_missing", t, f"#row{i + 1}", pk, "主键为空")
                continue
            if not isinstance(k, str) or k != k.strip():
                add("error", "pk_not_text", t, k, pk, f"主键必须是无首尾空白的文本，实际 {type(k).__name__}:{k!r}")
            if k in seen:
                add("error", "pk_duplicate", t, k, pk, f"重复，行 {seen[k]} 与 {i + 1}")
            seen.setdefault(k, i + 1)
        pks[t] = set(seen)
        if "expected_count" in ts and len(rows) != ts["expected_count"]:
            add("error", "count_mismatch", t, None, None, f"期望 {ts['expected_count']} 行，实际 {len(rows)}")
    for t, ts in tables.items():
        rows = data.get(t, [])
        cols = ts.get("columns", {})
        pk = ts["primary_key"]
        tri_ok = ts.get("tri_values", DEFAULT_TRI)
        for r in rows:
            k = r.get(pk)
            for c, v in r.items():
                if wrapped(v):
                    add("error", "wrapped_value", t, k, c, "未解包的 {value/source} 对象")
            for c, ty in cols.items():
                v = r.get(c)
                if ty == "tri":
                    if blank(v):
                        add("error", "tri_blank", t, k, c, "缺失必须写“待核实”，不能留空")
                    elif v not in tri_ok:
                        add("error", "tri_invalid", t, k, c, f"取值 {v!r} 不在 {tri_ok}")
                    elif v == "否":
                        ev = ts.get("tri_evidence", {}).get(c)
                        if ev and blank(r.get(ev)):
                            add("error", "tri_no_evidence", t, k, c, f"“否”缺少依据列 {ev}")
                elif ty == "date" and not blank(v):
                    d = parse_date(v)
                    if d is None:
                        add("error", "date_invalid", t, k, c, f"无法解析为日期: {v!r}")
                    else:
                        rng = ts.get("date_range", {}).get(c)
                        if rng and not (rng[0] <= d.isoformat() <= rng[1]):
                            add("error", "date_out_of_range", t, k, c, f"{d} 不在 {rng}")
                elif ty == "number" and not blank(v):
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        add("error", "number_invalid", t, k, c, f"不是数字: {v!r}")
        for rel in ts.get("relations", []):
            c, tgt = rel["column"], rel["target"]
            children = {}
            for r in rows:
                ks = rel_keys(r.get(c))
                if not ks and rel.get("required"):
                    add("error", "relation_missing", t, r.get(pk), c, "必填关联为空")
                for x in ks:
                    children.setdefault(x, 0)
                    children[x] += 1
                    if x not in pks.get(tgt, set()):
                        add("error", "orphan", t, r.get(pk), c, f"{x!r} 在 {tgt} 中不存在")
            mc = rel.get("min_children", 0)
            if mc:
                for pkey in pks.get(tgt, set()):
                    if children.get(pkey, 0) < mc:
                        add("warn", "parent_without_children", tgt, pkey, c, f"{t} 中少于 {mc} 条子记录")
    return issues


def norm(v, ty):
    if blank(v):
        return None
    if ty == "date":
        d = parse_date(v)
        return d.isoformat() if d else f"?{v}"
    if ty == "number":
        try:
            return float(v)
        except (TypeError, ValueError):
            return f"?{v}"
    if ty == "relation":
        return tuple(sorted(rel_keys(v)))
    return str(v).strip()


def diff(spec, local, export, max_samples=5):
    out = {}
    issues = []
    for t, ts in spec["tables"].items():
        pk, cols = ts["primary_key"], ts.get("columns", {})
        L = {r[pk]: r for r in local.get(t, []) if not blank(r.get(pk))}
        E = {}
        shells = 0
        for r in export.get(t, []):
            if blank(r.get(pk)):
                shells += 1 if all(blank(v) for v in r.values()) else 0
                if not all(blank(v) for v in r.values()):
                    issues.append({"level": "error", "code": "export_pk_missing", "table": t, "key": None, "column": pk,
                                   "detail": "导出里有非空记录缺主键"})
                continue
            if r[pk] in E:
                issues.append({"level": "error", "code": "export_pk_duplicate", "table": t, "key": r[pk], "column": pk, "detail": "导出主键重复"})
            E[r[pk]] = r
        miss, extra = sorted(set(L) - set(E)), sorted(set(E) - set(L))
        stat = {"local": len(L), "export": len(E), "missing_in_export": len(miss), "extra_in_export": len(extra), "empty_shell_rows": shells,
                "column_mismatch": {}, "date_blanked": 0}
        for k in miss[:max_samples]:
            issues.append({"level": "error", "code": "missing_in_export", "table": t, "key": k, "column": None, "detail": "云端缺记录"})
        for k in extra[:max_samples]:
            issues.append({"level": "error", "code": "extra_in_export", "table": t, "key": k, "column": None, "detail": "云端多出记录"})
        if shells:
            issues.append({"level": "error", "code": "empty_shell_rows", "table": t, "key": None, "column": None,
                           "detail": f"导出含 {shells} 行全空记录（导入遗留空壳），需清理后再验收"})
        for k in sorted(set(L) & set(E)):
            for c, ty in cols.items():
                if c == pk or c not in E[k] and c not in L[k]:
                    continue
                a, b = norm(L[k].get(c), ty), norm(E[k].get(c), ty)
                if a != b:
                    stat["column_mismatch"][c] = stat["column_mismatch"].get(c, 0) + 1
                    if ty == "date" and a is not None and b is None:
                        stat["date_blanked"] += 1
                    if stat["column_mismatch"][c] <= max_samples:
                        issues.append({"level": "error", "code": "value_mismatch", "table": t, "key": k, "column": c,
                                       "detail": f"本地={a!r} 云端={b!r}"})
        out[t] = stat
    return out, issues


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    if len(argv) < 4 or argv[1] not in ("check", "diff"):
        print(__doc__)
        return 2
    try:
        spec = load(argv[2])
        if argv[1] == "check":
            issues = check(spec, load(argv[3]))
            report = {"mode": "check", "errors": sum(i["level"] == "error" for i in issues),
                      "warnings": sum(i["level"] == "warn" for i in issues), "issues": issues}
        else:
            stats, issues = diff(spec, load(argv[3]), load(argv[4]))
            issues = issues + []
            report = {"mode": "diff", "errors": sum(i["level"] == "error" for i in issues), "tables": stats, "issues": issues}
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(json.dumps({"fatal": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
