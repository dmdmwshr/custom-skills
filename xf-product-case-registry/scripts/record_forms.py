"""检查形式：从本案初查电子《消防产品监督检查记录》第 1 页的勾选框读“检查形式”，作为校准补空/更正的本地证据。

电子记录里每个选项前是 Wingdings2 字体的方框字符：U+F0A3 未选，U+F052 已选。只有恰好一项被选中且是合同允许的
选项(专项监督抽查/日常检查/举报投诉核查/其他；“复查”不是初查的检查形式)才给值，否则整案缺项，不猜、不读扫描件。
首次采用时用 29 个云端已有检查形式的案卷交叉验证过(全部一致、没有冲突)。

``scan`` 只读本地：逐案取最新一代已核验清单指向的初查电子记录(摘要须与清单一致)，写一份证据文件(含记录与清单摘要)。
同一份记录摘要已有结果时直接复用(``--reuse`` 指向上一份证据)，不重新解析。需要 pdfplumber(受管文档环境自带)。
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any, Callable

try:
    from . import record_quantities as rq
except ImportError:  # script-style import
    import record_quantities as rq

EVIDENCE_SCHEMA = "RecordFormEvidenceV1"
PARSER = "wingdings2-checkbox-v3"
OFF, ON = "", ""
ALLOWED = ("专项监督抽查", "日常检查", "举报投诉核查", "其他")
FORMS = ("专项监督抽查", "举报投诉核查", "复查", "其他", "日常检查")


def read_boxes(path: Path) -> dict[str, bool]:
    """第 1 页每个方框后面的选项文字 -> 是否选中。"""
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("读取勾选框需要 pdfplumber：请在受管文档环境中运行本步骤") from error
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        boxes = [c for c in page.chars if c["text"] in (OFF, ON)]
        words = page.extract_words()
        found: dict[str, bool] = {}
        for box in boxes:
            row = [w for w in words if abs(w["top"] - box["top"]) < 6 and w["x0"] > box["x0"]]
            row.sort(key=lambda w: w["x0"])
            limit = min([b["x0"] for b in boxes if b is not box and b["x0"] > box["x0"] and abs(b["top"] - box["top"]) < 6], default=1e9)
            label = "".join("".join(w["text"].split()) for w in row if w["x0"] < limit)
            if label:
                found[label] = box["text"] == ON
        return found


def decide(boxes: dict[str, bool]) -> tuple[str | None, str | None]:
    checked = [form for form in FORMS for label, on in boxes.items() if on and label.startswith(form)]
    if not any(label.startswith(form) for form in FORMS for label in boxes):
        return None, "记录里没有读到检查形式方框"
    if len(checked) != 1:
        return None, f"检查形式勾选不唯一：{checked or '无'}"
    if checked[0] not in ALLOWED:
        return None, f"勾选项“{checked[0]}”不是初查检查形式的合同选项"
    return checked[0], None


def key_unit(boxes: dict[str, bool]) -> bool | None:
    """“消防安全重点单位 / 非消防安全重点单位”方框：只选重点=True，只选非重点=False；都选、都没选、没读到=None。"""
    key = any(on for label, on in boxes.items() if label.startswith("消防安全重点单位"))
    non = any(on for label, on in boxes.items() if label.startswith("非消防安全重点单位"))
    if key and not non:
        return True
    if non and not key:
        return False
    return None


def extract_case(generation_dir: Path, business_root: Path, boxes_of: Callable[[Path], dict[str, bool]] = read_boxes,
                 reuse: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"generation": generation_dir.name}
    manifest_path = generation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out["manifest"] = {"path": manifest_path.relative_to(business_root).as_posix(), "sha256": rq.file_sha(manifest_path)}
    record, why = rq._initial_record(manifest, generation_dir)
    if record is None:
        return {**out, "hold": why}
    digest = rq.file_sha(record)
    out["record"] = {"path": record.relative_to(business_root).as_posix(), "sha256": digest}
    previous = (reuse or {}).get(digest)
    if previous is not None and previous.get("parser") == PARSER:
        return {**out, **{k: previous[k] for k in ("inspectionForm", "hold", "keyUnit") if k in previous}, "parser": PARSER, "reused": True}
    try:
        boxes = boxes_of(record)
        value, why = decide(boxes)
        unit = key_unit(boxes)
    except RuntimeError:
        raise
    except Exception as error:   # 损坏或加密的 PDF：整案缺项
        return {**out, "hold": f"记录读不出：{type(error).__name__}", "parser": PARSER}
    return {**out, "parser": PARSER, "keyUnit": unit, **({"inspectionForm": value} if value else {"hold": why})}


def scan(business_root: Path, out_path: Path, observed_at: str, boxes_of: Callable[[Path], dict[str, bool]] = read_boxes,
         only: set[str] | None = None, reuse_path: Path | None = None) -> dict[str, Any]:
    history = business_root / "工作区" / "历史工作区"
    latest: dict[str, Path] = {}
    for child in sorted(history.iterdir()):
        match = rq.GENERATION.match(child.name)
        if match and (child / "manifest.json").is_file() and (only is None or match.group(1) in only):
            latest[match.group(1)] = child
    reuse: dict[str, Any] = {}
    if reuse_path is not None and reuse_path.is_file():
        old = json.loads(reuse_path.read_text(encoding="utf-8"))
        for case in (old.get("cases") or {}).values():
            if isinstance(case, dict) and case.get("record", {}).get("sha256"):
                reuse[case["record"]["sha256"]] = case
    cases = {project: extract_case(path, business_root, boxes_of, reuse) for project, path in latest.items()}
    values = collections.Counter(c.get("inspectionForm") for c in cases.values() if "inspectionForm" in c)
    body = {"schemaVersion": EVIDENCE_SCHEMA, "parser": PARSER, "observedAt": observed_at, "businessRoot": str(business_root),
            "counts": {"cases": len(cases), "withForm": sum(values.values()), "held": sum(1 for c in cases.values() if "hold" in c),
                       "reused": sum(1 for c in cases.values() if c.get("reused")), "byForm": dict(values)}, "cases": cases}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"path": str(out_path), "sha256": rq.file_sha(out_path), "counts": body["counts"]}


def main(argv: list[str] | None = None) -> None:
    """独立入口（受管文档环境运行）：record_forms.py scan --business-root 根 --out 文件 [--reuse 上一份证据]。"""
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("action", choices=["scan"])
    parser.add_argument("--business-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--reuse")
    parser.add_argument("--only-project", action="append")
    args = parser.parse_args(argv)
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(scan(Path(args.business_root), Path(args.out), observed, only=set(args.only_project) if args.only_project else None,
                          reuse_path=Path(args.reuse) if args.reuse else None), ensure_ascii=False))


if __name__ == "__main__":
    main()
