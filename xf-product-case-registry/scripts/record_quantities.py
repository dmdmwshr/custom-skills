"""产品数量：从本案初查电子《消防产品监督检查记录》表格读“检查基数/检查数量”原文，再生成补空用的字段计划。

两步分开，互不替代：
1. ``scan``：只读本地。逐案取最新一代已核验清单指向的初查电子记录（摘要须与清单一致），读表格两列原文，
   与清单产品逐行按序号和名称对上才算数；对不上、没有电子记录、表格读不出的整案列为缺项，不猜、不读扫描件。
   需要 pdfplumber（受管文档环境自带）；没有时明确报错，不退回按文字流猜列。
2. ``build_plan``：对照飞书产品明细 fresh 行，只给仍为空的数量格生成 FeishuFieldPlanV1 条目（expectedOld=null）。
   记录原文没有单位就不写单位；抽样送检产品的抽样数量在抽样单上，这里不拿记录的检查数量顶替；从不补 1。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

EVIDENCE_SCHEMA = "RecordQuantityEvidenceV1"
PLAN_SCHEMA = "FeishuFieldPlanV1"
GENERATION = re.compile(r"^(\d{8}C\d{9})-(\d{8}T\d{6}Z)-[0-9a-f]+$")
QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)(\D*)$")
PRODUCT_TABLE, PRODUCT_KEY = "产品明细", "fldqlwHjUW"
F_TEXT, F_VALUE, F_UNIT, F_MEANING = "fldiWdH6Ut", "fldD8v7LK2", "fld0r0PKqO", "fldf79UPnW"
F_NAME, F_METHOD = "fld6QPrwZD", "fld4gYH0cP"
ONSITE_MEANING, ONSITE_METHOD, SAMPLING_METHOD = "现场检查数量", "现场判定", "抽样送检"


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", text if isinstance(text, str) else "")


def same_name(left: Any, right: Any) -> bool:
    """全半角括号视同，其余逐字。"""
    table = str.maketrans("（）", "()")
    return norm(left).translate(table) == norm(right).translate(table)


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_quantity(text: Any) -> dict[str, Any]:
    """原文照录；纯数字或“数字+单位”才给数值，单位只取原文里写着的。"""
    raw = norm(text)
    match = QUANTITY.match(raw)
    if not raw or match is None:
        return {"text": raw or None, "value": None, "unit": None}
    number = float(match.group(1))
    return {"text": raw, "value": int(number) if number.is_integer() else number, "unit": match.group(2) or None}


def pdf_tables(path: Path) -> list[list[list[str | None]]]:
    try:
        import pdfplumber
    except ImportError as error:  # 明确缺依赖，不换一种读法
        raise RuntimeError("读取监督检查记录表格需要 pdfplumber：请在受管文档环境中运行本步骤") from error
    with pdfplumber.open(str(path)) as pdf:
        return [table for page in pdf.pages for table in page.extract_tables()]


def record_rows(tables: list[list[list[str | None]]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """产品表各行的名称格、检查基数、检查数量；表头不是预期七列时拒绝。"""
    rows: list[dict[str, Any]] = []
    for table in tables:
        header = [norm(cell) for cell in (table[0] if table else [])]
        if not header or "产品名称" not in header[0]:
            continue
        if len(header) < 7 or "基数" not in header[3] or "检查数量" not in header[4]:
            return None, "表头不是预期列：" + "|".join(header)
        for row in table[1:]:
            if not row or norm(row[0]).startswith("备注"):
                continue
            if len(row) < 7 or not norm(row[0]):
                return None, "产品行形状异常"
            rows.append({"nameCell": norm(row[0]), "base": norm(row[3]), "checked": norm(row[4])})
    return (rows, None) if rows else (None, "记录里没有产品表")


def _initial_record(manifest: dict[str, Any], generation_dir: Path) -> tuple[Path | None, str | None]:
    files = {item.get("clientRef"): item for item in manifest.get("files", []) if isinstance(item, dict)}
    ref = next((version.get("fileRef") for slot in manifest.get("documentSlots", []) if slot.get("slotCode") == "INITIAL_INSPECTION_RECORD"
                for version in slot.get("versions", []) if version.get("kind") == "ELECTRONIC"), None)
    entry = files.get(ref)
    if entry is None:
        return None, "清单没有初查电子监督检查记录"
    want = str(entry.get("sha256") or "").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        return None, "清单没有该记录的摘要"
    names = [entry.get("relativePath"), entry.get("sourceRelativePath")]
    candidates = [generation_dir / folder / name for name in names if name for folder in ("normalized", "source", ".")]
    for path in candidates:
        if path.is_file() and file_sha(path) == want:
            return path, None
    for path in sorted(generation_dir.rglob("*.pdf")):   # 文件改过名：按清单摘要找回同一份
        if file_sha(path) == want:
            return path, None
    return None, "找不到与清单摘要一致的初查电子记录"


def extract_case(generation_dir: Path, business_root: Path, tables_of: Callable[[Path], list] = pdf_tables) -> dict[str, Any]:
    out: dict[str, Any] = {"generation": generation_dir.name}
    manifest_path = generation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out["manifest"] = {"path": manifest_path.relative_to(business_root).as_posix(), "sha256": file_sha(manifest_path)}
    products = (manifest.get("initialInspection") or {}).get("products") or []
    record, why = _initial_record(manifest, generation_dir)
    if record is None:
        return {**out, "hold": why}
    out["record"] = {"path": record.relative_to(business_root).as_posix(), "sha256": file_sha(record)}
    try:
        rows, why = record_rows(tables_of(record))
    except RuntimeError:
        raise
    except Exception as error:   # 损坏或加密的 PDF：整案缺项
        return {**out, "hold": f"记录读不出：{type(error).__name__}"}
    if rows is None:
        return {**out, "hold": why}
    if len(rows) != len(products):
        return {**out, "hold": f"记录产品行数 {len(rows)} 与清单产品数 {len(products)} 不一致"}
    items = []
    for number, (row, product) in enumerate(zip(rows, products), start=1):
        name = norm(product.get("name"))
        if not name or not row["nameCell"].translate(str.maketrans("（）", "()")).startswith(name.translate(str.maketrans("（）", "()"))):
            return {**out, "hold": f"第 {number} 行名称与清单不一致"}
        items.append({"seq": number, "name": product.get("name"), "method": product.get("method"),
                      "base": parse_quantity(row["base"]), "checked": parse_quantity(row["checked"])})
    return {**out, "products": items}


def scan(business_root: Path, out_path: Path, observed_at: str, tables_of: Callable[[Path], list] = pdf_tables,
         only: set[str] | None = None) -> dict[str, Any]:
    """每案只读最新一代清单；结果写一份数量证据文件（含每案记录与清单摘要）。"""
    history = business_root / "工作区" / "历史工作区"
    latest: dict[str, Path] = {}
    for child in sorted(history.iterdir()):
        match = GENERATION.match(child.name)
        if match and (child / "manifest.json").is_file() and (only is None or match.group(1) in only):
            latest[match.group(1)] = child   # 目录名含 UTC 时间，排序后最后一个即最新
    cases = {project: extract_case(path, business_root, tables_of) for project, path in latest.items()}
    body = {"schemaVersion": EVIDENCE_SCHEMA, "observedAt": observed_at, "businessRoot": str(business_root),
            "counts": {"cases": len(cases), "held": sum(1 for c in cases.values() if "hold" in c),
                       "products": sum(len(c.get("products", [])) for c in cases.values())}, "cases": cases}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"path": str(out_path), "sha256": file_sha(out_path), "counts": body["counts"]}


def _cell(value: Any) -> Any:
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def build_plan(*, evidence_path: Path, evidence_sha: str, cloud_rows: dict[str, dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    """``cloud_rows``：recordId -> {fieldId: 值} 的产品明细 fresh 行。只为初查行里仍为空的数量格出条目。"""
    if file_sha(evidence_path) != evidence_sha:
        raise ValueError("数量证据摘要与固定值不一致")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schemaVersion") != EVIDENCE_SCHEMA:
        raise ValueError("数量证据版本不支持")
    by_case: dict[str, dict[int, tuple[str, dict[str, Any]]]] = {}
    for record_id, row in cloud_rows.items():
        parts = str(_cell(row.get(PRODUCT_KEY)) or "").split("|")
        if len(parts) == 3 and parts[1] == "初查" and parts[2].isdigit():
            by_case.setdefault(parts[0], {})[int(parts[2])] = (record_id, row)
    entries, missing, filled = [], [], 0
    for project in sorted(by_case):
        rows, case = by_case[project], evidence["cases"].get(project)
        reason = "本地没有该案已核验清单" if case is None else case.get("hold")
        if reason is None and sorted(rows) != [item["seq"] for item in case["products"]]:
            reason = f"飞书初查产品序号 {sorted(rows)} 与记录 {len(case['products'])} 行不对应"
        if reason is None:
            wrong = [item["seq"] for item in case["products"] if not same_name(_cell(rows[item["seq"]][1].get(F_NAME)), item["name"])]
            reason = f"第 {wrong} 行产品名称与飞书不一致" if wrong else None
        if reason is not None:
            missing.extend({"产品键": f"{project}|初查|{seq}", "reason": reason} for seq in sorted(rows))
            continue
        refs = [case["record"], case["manifest"]]
        for item in case["products"]:
            record_id, row = rows[item["seq"]]
            key, checked = f"{project}|初查|{item['seq']}", item["checked"]
            if all(not _empty(row.get(fid)) for fid in (F_TEXT, F_VALUE, F_UNIT, F_MEANING)):
                filled += 1
                continue
            if _cell(row.get(F_METHOD)) != ONSITE_METHOD:
                why = "抽样送检：抽样数量在抽样单上，尚无确定性来源" if _cell(row.get(F_METHOD)) == SAMPLING_METHOD else "检查方式为空，数量含义无法确定"
                missing.append({"产品键": key, "reason": why})
                continue
            if checked["text"] is None:
                missing.append({"产品键": key, "reason": "记录的检查数量格为空"})
                continue
            new = {F_TEXT: checked["text"], F_VALUE: checked["value"], F_UNIT: checked["unit"], F_MEANING: ONSITE_MEANING}
            fields = {fid: {"new": value, "expectedOld": None, "evidence": refs, "basisRef": "初查电子监督检查记录“检查数量”列",
                            "observedAt": evidence["observedAt"]}
                      for fid, value in new.items() if value is not None and _empty(row.get(fid))}
            gaps = [label for fid, label in ((F_VALUE, "数值"), (F_UNIT, "单位")) if new[fid] is None and _empty(row.get(fid))]
            if gaps:
                missing.append({"产品键": key, "reason": "记录原文没有" + "、".join(gaps) + f"（原文：{checked['text']}）"})
            if fields:
                entries.append({"table": PRODUCT_TABLE, "recordId": record_id, "projectNo": project,
                                "stableKey": {"fieldId": PRODUCT_KEY, "value": key}, "fields": fields})
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path, report_path = out_dir / "数量字段计划.json", out_dir / "数量缺项.json"
    plan = None
    if entries:
        plan_path.write_text(json.dumps({"schemaVersion": PLAN_SCHEMA, "source": {"path": str(evidence_path), "sha256": evidence_sha},
                                         "entries": entries}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        plan = {"path": str(plan_path), "sha256": file_sha(plan_path)}
    report = {"schemaVersion": "RecordQuantityPlanReportV1", "evidence": {"path": str(evidence_path), "sha256": evidence_sha},
              "counts": {"initialProducts": sum(len(v) for v in by_case.values()), "toFill": len(entries), "alreadyFilled": filled,
                         "missing": len(missing)}, "plan": plan, "missing": missing}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    """独立入口（受管文档环境运行，只依赖标准库与 pdfplumber）：record_quantities.py scan --business-root 根 --out 文件。"""
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("action", choices=["scan"])
    parser.add_argument("--business-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only-project", action="append")
    args = parser.parse_args(argv)
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(scan(Path(args.business_root), Path(args.out), observed, only=set(args.only_project) if args.only_project else None),
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
