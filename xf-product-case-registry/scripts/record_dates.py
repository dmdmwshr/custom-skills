"""包内电子《消防产品监督检查记录》的初查日期：整理之前从来源原包读出并核对，给创建/补空用。

初查日期、产品合格判定、文书分类是三件分开的事：产品判定（含不合格行）与文书归类留给整理与目视核对，这里只回答
“这个案的原包里有没有电子检查记录，它写的监督检查时间是哪天”，且每个答案都带可复核的来源（包摘要、成员摘要）。

四种状态，缺一不可混用：
- ``PRESENT_VERIFIED``：恰有一份标准命名的电子记录，文字层读出合法的监督检查时间，且记录里的单位名称与总账本案一致 → 取该日。
- ``ABSENT_CONFIRMED``：整包成员（至少一个）**每一个都明确属于已认可的其他文书**（通知书、送达回证、审批表、营业执照、照片等，见
  ``KNOWN_OTHER``），且没有任何成员的名字不明、含检查记录字样或是合并件 → 才允许用详情受理日期作后备。不凭“不像标准题名”推出没有电子记录。
- ``PRESENT_UNVERIFIED``：记录存在但读不出、日期缺失/非法、单位对不上、有多份；或包里有名字不明/混合/泛名成员（“扫描件”“初查”
  “通知书与监督检查记录”之类）；或空包、CRC 错、加密、打不开 → 不填关键字段、不用受理日期顶替，保留待核实并带原因，等目视或来源核对。
  读取格式问题只影响这一案；共享工具（pdfplumber）缺失仍明确报错（``ToolMissing``）。
- ``UNKNOWN``：没有本案的核查证据（包不在、没扫描、包已变化）→ 同样不顶替。

``scan`` 只读本地原包（受管文档环境，需要 pdfplumber）；同一份包摘要已有结果时直接复用。构建时用 ``state_for`` 逐案重核包与成员摘要，
证据文件本身带摘要，被改动即拒绝。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
import zlib
from datetime import date
from pathlib import Path
from typing import Any, Callable

EVIDENCE_SCHEMA = "ElectronicRecordDateEvidenceV1"
PARSER = "record-date-text-v2"      # v1 把泛名/合并名成员误确认为“没有电子记录”：旧证据不再复用，也不能用于构建
VERIFIED, ABSENT, UNVERIFIED, UNKNOWN = "PRESENT_VERIFIED", "ABSENT_CONFIRMED", "PRESENT_UNVERIFIED", "UNKNOWN"
ELECTRONIC_NAME = re.compile(r"^消防产品监督检查记录\(编号：〔\d{4}〕第\d+号\)\.pdf$")   # 与整理程序同一标准文件名
# 已认可的其他文书（成员名去掉扩展名、序号前缀、重复后缀后的整体匹配；合并件按 及/和/与/、 拆开后每一段都要匹配）。
# 名字里没有出现在这里的一律不是“已认可”：泛名（扫描件、初查、产品监督）、含检查记录字样的名字、不清楚的合并件都保持未知。
_NUMBERED = r"锡?[\u4e00-\u9fa5]{0,2}消(?:送证|限|产函|产判|产验|即|函)字(?:〔\d{4}〕第?\d+号)?"
KNOWN_OTHER = tuple(re.compile(f"^(?:{p})$") for p in (
    r"卷内备考表", r"营业执照(?:复印件)?", r"(?:现场)?照片(?:（[^）]*）)?", r"(?:\d+、)?[\u4e00-\u9fa5]{0,8}照片", r"图纸\d*", r"(?:\d+、)?检验报告(?:书)?", r"认证证书", r"购买(?:记录|凭证)", r"授权委托书",
    r"送达回证", r"(?:责令限期改正)?通知书", r"责令限期改正通知书", r"责令", r"通报函", r"(?:现场)?(?:检查)?判定不合格通知书", r"不合格通知书",
    r"消防产品现场检查判定不合格通知书", r"呈请[\u4e00-\u9fa5]{0,16}审批表", r"审批表", r"证据(?:保全|保留)决定书", r"证据(?:保全)?清单", r"清单", r"先行登记保存清单",
    r"行政处罚告知笔录(?:（单位）)?", r"呈请行政处罚审批表", r"结案报告", r"消防产品质量监督抽查抽样单(?:（\d+）|\(\d+\))?", _NUMBERED))
COMPONENT_SPLIT = re.compile(r"[及和与、，,+]")
DATE_RX = re.compile(r"监督检查时间：\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
UNIT_RX = re.compile(r"名\s*称\s*：(.*?)(?=[]\s*(?:非)?消防安全重点单位)", re.S)
DOC_NO_RX = re.compile(r"编号：\s*〔(\d{4})〕第\s*(\d+)\s*号")


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _member_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").replace("(", "（").replace(")", "）"))


def parse_record_text(text: str, unit_name: str) -> tuple[dict[str, Any] | None, str | None]:
    """(日期、文号、记录单位) 或 (None, 原因)。日期必须是真实日历日；单位须与总账本案一致。"""
    flat = text.replace("\n", " ")
    found = DATE_RX.search(flat)
    if not found:
        return None, "记录里读不到“监督检查时间”"
    try:
        day = date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError:
        return None, "记录里的监督检查时间不是合法日期"
    unit = UNIT_RX.search(text)
    if not unit or not _norm(unit.group(1)):
        return None, "记录里读不到单位名称"
    if not unit_name:
        return None, "总账没有本案单位名称，无法核对记录身份"
    if _norm(unit.group(1)) != _norm(unit_name):
        return None, "记录里的单位名称与总账本案不一致"
    number = DOC_NO_RX.search(flat)
    return {"date": day.isoformat(), "docNo": f"〔{number.group(1)}〕第{int(number.group(2)):04d}号" if number else None, "recordUnit": _norm(unit.group(1))}, None


class ToolMissing(RuntimeError):
    """共享工具（如 pdfplumber）缺失：不是某一案的问题，明确报出，不降成案卷状态。"""


def read_first_page(data: bytes) -> str:
    """电子记录第 1 页文字层（受管文档环境的 pdfplumber）。"""
    try:
        import pdfplumber
    except ImportError as error:
        raise ToolMissing("读取电子检查记录需要 pdfplumber：请在受管文档环境中运行本步骤") from error
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return pdf.pages[0].extract_text() or ""


def known_other(name: str) -> bool:
    """成员名整体属于已认可的其他文书（合并件的每一段都认可）；否则 False。"""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name.rsplit("/", 1)[-1].strip())
    stem = re.sub(r"[（(]\d+[）)]$|[_-]\d+$", "", stem)
    stem = re.sub(r"^\d+、", "", stem)
    parts = [re.sub(r"^\d+、", "", x.strip()) for x in COMPONENT_SPLIT.split(stem)] if COMPONENT_SPLIT.search(stem) else [stem]
    parts = [x for x in parts if x] or [""]
    return all(any(rx.match(part) for rx in KNOWN_OTHER) for part in parts)


def classify_package(zip_path: Path, unit_name: str, *, reader: Callable[[bytes], str] = read_first_page) -> dict[str, Any]:
    """一个原包的电子记录状态与来源；只读包，不解压到磁盘。读取格式问题只影响这一案（UNVERIFIED），``ToolMissing`` 向上报出。"""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as error:
        return {"state": UNVERIFIED, "reason": f"原包打不开：{type(error).__name__}"}
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        names = [_member_name(i) for i in infos]
        base = {"memberCount": len(names)}
        if not infos:
            return {**base, "state": UNVERIFIED, "reason": "空包：没有任何成员，不能当作没有电子记录"}
        encrypted = [n for n, i in zip(names, infos) if i.flag_bits & 0x1]
        if encrypted:
            return {**base, "state": UNVERIFIED, "reason": "包内成员加密，读不了", "members": sorted(encrypted)}
        try:
            bad = archive.testzip()
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError, ValueError) as error:
            return {**base, "state": UNVERIFIED, "reason": f"包损坏，无法逐件校验：{type(error).__name__}"}
        if bad is not None:
            return {**base, "state": UNVERIFIED, "reason": "包内成员 CRC 校验不通过（损坏）", "members": [bad]}
        electronic = [(n, i) for n, i in zip(names, infos) if ELECTRONIC_NAME.match(n.rsplit("/", 1)[-1])]
        others = [n for n, _ in zip(names, infos) if not ELECTRONIC_NAME.match(n.rsplit("/", 1)[-1])]
        unknown = sorted(n for n in others if not known_other(n))
        if len(electronic) > 1:
            return {**base, "state": UNVERIFIED, "reason": "包内有多份标准命名的电子检查记录", "members": sorted(n for n, _ in electronic)}
        if not electronic:
            if unknown:
                return {**base, "state": UNVERIFIED, "reason": "没有标准命名的电子记录，但有名字不明/泛名/合并或含检查记录字样的成员，不能确认没有电子记录，须目视确认", "members": unknown}
            return {**base, "state": ABSENT, "reason": "全包成员都明确属于已认可的其他文书（通知书、送达回证、审批表、照片等），没有电子检查记录", "members": sorted(others)}
        name, info = electronic[0]
        try:
            data = archive.read(info)
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError, ValueError, zlib.error) as error:
            return {**base, "state": UNVERIFIED, "reason": f"电子记录读不出：{type(error).__name__}", "members": [name]}
        member = {"member": name, "memberSha256": hashlib.sha256(data).hexdigest(), "memberBytes": len(data)}
        try:
            text = reader(data)
        except ToolMissing:
            raise
        except Exception as error:      # 损坏的 PDF、读不出文字层：存在但读不出
            return {**base, **member, "state": UNVERIFIED, "reason": f"电子记录读不出：{type(error).__name__}"}
        parsed, why = parse_record_text(text, unit_name)
        if parsed is None:
            return {**base, **member, "state": UNVERIFIED, "reason": why}
        extra = {"otherUnrecognizedMembers": unknown} if unknown else {}
        return {**base, **member, **extra, "state": VERIFIED, "date": parsed["date"], "docNo": parsed["docNo"], "recordUnit": parsed["recordUnit"]}


def _package_of(case: Any) -> tuple[str, str]:
    source = case.get("source") if isinstance(case, dict) else None
    source = source if isinstance(source, dict) else {}
    return str(source.get("packageRelativePath") or ""), str(source.get("packageSha256") or "").removeprefix("sha256:")


def scan(business_root: Path, out_path: Path, observed_at: str, *, reader: Callable[[bytes], str] = read_first_page,
         only: set[str] | None = None, reuse_path: Path | None = None) -> dict[str, Any]:
    waterline = json.loads((business_root / "案卷水位记录.json").read_text(encoding="utf-8-sig")).get("cases", {})
    reuse: dict[tuple[str, str], dict[str, Any]] = {}
    if reuse_path is not None and reuse_path.is_file():
        old = json.loads(reuse_path.read_text(encoding="utf-8"))
        if old.get("schemaVersion") == EVIDENCE_SCHEMA and old.get("parser") == PARSER:
            for project, entry in (old.get("cases") or {}).items():
                if isinstance(entry, dict) and entry.get("packageSha256") and entry.get("state") in {VERIFIED, ABSENT}:   # 读不出/未核实的不复用：读法改进后自动重读
                    reuse[(project, entry["packageSha256"])] = entry
    cases: dict[str, Any] = {}
    for project, case in sorted(waterline.items()):
        if only is not None and project not in only:
            continue
        relative, recorded = _package_of(case)
        if not relative or (case.get("state") == "COMPLETED" and only is None):
            continue      # 没有原包的案、已归档的案（清单里有日期）不在整理前核查范围
        path = business_root / relative
        if not path.is_file():
            cases[project] = {"state": UNKNOWN, "reason": "总账登记的原包文件不存在", "packageRelativePath": relative}
            continue
        try:
            actual = file_sha(path)
        except OSError as error:
            cases[project] = {"state": UNKNOWN, "reason": f"原包读不出：{type(error).__name__}", "packageRelativePath": relative}
            continue
        if recorded and recorded != actual:
            cases[project] = {"state": UNKNOWN, "reason": "原包实际摘要与总账登记不符", "packageRelativePath": relative, "packageSha256": actual}
            continue
        previous = reuse.get((project, actual))
        if previous is not None:
            entry = {**previous, "reused": True}
        else:
            try:
                entry = classify_package(path, str(case.get("unitName") or ""), reader=reader)
            except ToolMissing:
                raise      # 共享工具缺失不是某一案的问题：整次扫描明确失败，不写任何案的状态
            except Exception as error:      # 其余读取问题只影响这一案，后续案照常
                entry = {"state": UNVERIFIED, "reason": f"这一案的原包读取出错：{type(error).__name__}"}
        cases[project] = {**entry, "packageRelativePath": relative, "packageSha256": actual}
    counts: dict[str, int] = {}
    for entry in cases.values():
        counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    body = {"schemaVersion": EVIDENCE_SCHEMA, "parser": PARSER, "observedAt": observed_at, "businessRoot": str(business_root),
            "counts": {"cases": len(cases), "reused": sum(1 for e in cases.values() if e.get("reused")), "byState": counts}, "cases": cases}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"path": str(out_path), "sha256": file_sha(out_path), "counts": body["counts"]}


def load_evidence(path: Path, expected_sha: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("电子记录日期证据文件不存在")
    if file_sha(path) != expected_sha:
        raise ValueError("电子记录日期证据摘要与固定值不一致；证据变化须重扫并另行冻结")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != EVIDENCE_SCHEMA or not isinstance(value.get("cases"), dict):
        raise ValueError("电子记录日期证据格式无效")
    if value.get("parser") != PARSER:      # 旧读法的确认（尤其是误把泛名成员判成“没有电子记录”）不得再用于构建
        raise ValueError(f"电子记录日期证据由旧读法 {value.get('parser')} 生成，须用 {PARSER} 重扫")
    return value


def state_for(evidence: dict[str, Any] | None, business_root: Path, project: str, waterline_case: Any) -> dict[str, Any]:
    """构建时对一个案的状态逐项重核：包文件与成员此刻的摘要必须仍等于证据里记的，否则按 UNKNOWN（须重扫）。"""
    entry = ((evidence or {}).get("cases") or {}).get(project)
    if not isinstance(entry, dict) or entry.get("state") not in {VERIFIED, ABSENT, UNVERIFIED}:
        return {"state": UNKNOWN, "reason": entry.get("reason") if isinstance(entry, dict) and entry.get("reason") else "没有本案电子记录核查证据"}
    if entry["state"] == UNVERIFIED:
        return {"state": UNVERIFIED, "reason": entry.get("reason") or "电子记录存在但未核实"}
    relative, recorded = _package_of(waterline_case)
    package = business_root / (relative or "")
    if not relative or relative != entry.get("packageRelativePath") or not package.is_file():
        return {"state": UNKNOWN, "reason": "证据对应的原包与总账当前原包不符或不存在，须重扫"}
    actual = file_sha(package)
    if actual != entry.get("packageSha256") or (recorded and recorded != actual):
        return {"state": UNKNOWN, "reason": "原包摘要已变化，须重扫"}
    if entry["state"] == ABSENT:
        return {"state": ABSENT, "reason": entry.get("reason") or "", "packageSha256": actual}
    try:
        with zipfile.ZipFile(package) as archive:
            matches = [i for i in archive.infolist() if _member_name(i) == entry.get("member")]
            ok = len(matches) == 1 and hashlib.sha256(archive.read(matches[0])).hexdigest() == entry.get("memberSha256")
    except (OSError, zipfile.BadZipFile):
        ok = False
    if not ok or not isinstance(entry.get("date"), str):
        return {"state": UNKNOWN, "reason": "电子记录成员摘要与证据不符，须重扫"}
    return {"state": VERIFIED, "date": entry["date"], "member": entry["member"], "memberSha256": entry["memberSha256"], "packageSha256": actual,
            "docNo": entry.get("docNo"), "observedAt": (evidence or {}).get("observedAt", "")}


def main(argv: list[str] | None = None) -> None:
    """独立入口（受管文档环境运行）：record_dates.py scan --business-root 根 --out 文件 [--reuse 上一份] [--only-project 项目编号]。"""
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
