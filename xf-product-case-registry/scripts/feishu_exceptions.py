"""Exception register: one current row per (project, exception type) in the case Base's existing ``数据核对`` table.

The user's ruling (2026-11-18): source facts come from the supervision system itself; a brigade's upload error (mixed package, missing
material) does not block a case; only when a key fact (project number, unit, inspection date, result, brigade) is really affected is that
recorded, and everything is recorded in ONE exception table that can be looked up. This module is that table's only writer.

- Reuses the table that already holds data-check findings (``C0001``…); those legacy rows are never read as ours and never written. Ours
  carry the stable key ``X-<项目编号>-<类型>`` in ``核对编号``, so a rerun finds its own row and writes nothing when nothing changed.
- Fields are the minimum: key, project, type, status, key-fact-affected, affected fields, short note, follow-up, source, record time.
  A cell never carries package content, personal identity, a screenshot or a file path (``lint``): such an entry is refused before any write.
- One current row per object. A changed entry updates that row in place (record time moves); the earlier values stay in the append-only write
  journal (``运行记录/异常登记账``) and in the table's own revision history. No parallel case ledger, no second table.
- Every write is read back. A write whose outcome is unknown blocks its key until it is read back by hand; nothing is replayed.

Standard library only (the transport is the registry's official-CLI transport).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:   # package import (tests) or script directory (installed copy)
    from .feishu_period import Ledger as Journal, _receipt_record_id
except ImportError:  # pragma: no cover - script layout
    from feishu_period import Ledger as Journal, _receipt_record_id

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA = "FeishuExceptionRegisterV1"
ENTRIES_SCHEMA = "ExceptionEntriesV1"
REGISTRY_SCHEMA = "ExceptionRegistryV1"
LEDGER_DIR = "异常登记账"
KEY_PREFIX = "X-"
PROJECT_NO = re.compile(r"^\d{8}C\d{9}$")
STATUSES = ("已登记保留", "待核实", "已解除")
MAX_CONTENT, MAX_SUGGESTION = 120, 80
MAX_UNKNOWN_PER_RUN = 1      # 出现新的未知后，同一次里依赖它的后续提交停止；已锁定的键在规划时精确隔离，其余安全目标仍在下一次继续
# 写前拒绝的内容：路径、文件名、证件/手机号等个人身份串、证件字样
FORBIDDEN = (
    (re.compile(r"[A-Za-z]:[\\/]|/mnt/|\\\\|/home/|/srv/"), "文件路径"),
    (re.compile(r"\.(pdf|png|jpe?g|zip|docx?|xlsx?|json)\b", re.I), "文件名"),
    (re.compile(r"(?<!\d)\d{15,18}[0-9Xx]?(?!\d)|(?<!\d)1[3-9]\d{9}(?!\d)"), "证件或手机号码"),
    (re.compile(r"身份证|护照号|银行卡"), "个人身份字样"),
)
COMPARED = ("处理状态", "关键信息是否受影响", "字段", "内容", "建议", "来源")
READ_FIELDS = ("核对编号", "项目编号", "类型", *COMPARED, "记录时间")


class ExceptionError(Exception):
    """Refused before any request (bad registry, entries, or an unsafe cell)."""


class ExceptionRuntimeError(Exception):
    """A write whose outcome is unknown."""


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_registry(path: Path, sha: str, base: str) -> dict[str, Any]:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ExceptionError("异常登记结构文件摘要与 --registry-sha 不符")
    registry = json.loads(data.decode("utf-8"))
    if registry.get("schemaVersion") != REGISTRY_SCHEMA:
        raise ExceptionError("异常登记结构版本不支持")
    if registry.get("baseId") != base:
        raise ExceptionError("目标 Base 与异常登记结构不一致")
    ids = {f["name"]: f["fieldId"] for f in registry["fields"]}
    missing = [n for n in READ_FIELDS if n not in ids]
    if missing:
        raise ExceptionError(f"异常登记结构缺少字段：{'、'.join(missing)}")
    options = {f["name"]: tuple(f["options"]) for f in registry["fields"] if f.get("options")}
    return {"tableId": registry["table"]["tableId"], "ids": ids, "options": options, "version": registry.get("version")}


def load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schemaVersion") != ENTRIES_SCHEMA or not isinstance(data.get("entries"), list):
        raise ExceptionError(f"异常清单需要 schemaVersion={ENTRIES_SCHEMA} 与 entries 数组")
    return data["entries"]


def entry_key(entry: dict[str, Any]) -> str:
    return f"{KEY_PREFIX}{entry.get('projectNo')}-{entry.get('type')}"


def lint(text: str) -> str | None:
    for pattern, label in FORBIDDEN:
        if pattern.search(text or ""):
            return label
    return None


def validate(entry: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(entry, dict):
        return ["条目不是对象"]
    if not PROJECT_NO.match(str(entry.get("projectNo") or "")):
        problems.append("项目编号格式不对")
    if entry.get("type") not in registry["options"].get("类型", ()):
        problems.append("异常事项不在登记选项内")
    if entry.get("affected") not in registry["options"].get("关键信息是否受影响", ()):
        problems.append("关键信息是否受影响必须是 否/是/待核实")
    if entry.get("status") not in STATUSES:
        problems.append("处理状态必须是 " + "/".join(STATUSES))
    if entry.get("source") not in registry["options"].get("来源", ()):
        problems.append("来源不在登记选项内")
    content, suggestion, fields = str(entry.get("content") or ""), str(entry.get("suggestion") or ""), str(entry.get("fields") or "")
    if not content.strip():
        problems.append("简要说明不能为空")
    if len(content) > MAX_CONTENT or len(suggestion) > MAX_SUGGESTION:
        problems.append(f"简要说明不超过{MAX_CONTENT}字、后续处理不超过{MAX_SUGGESTION}字")
    if entry.get("affected") == "是" and fields.strip() in ("", "无"):
        problems.append("关键信息受影响时必须写明受影响字段")
    for label, text in (("简要说明", content), ("后续处理", suggestion), ("字段", fields)):
        hit = lint(text)
        if hit:
            problems.append(f"{label}含{hit}，不得写入异常表")
    return problems


def _single(cell: Any) -> Any:
    if isinstance(cell, list):
        return cell[0] if cell else None
    return cell


def _norm(name: str, cell: Any) -> Any:
    value = _single(cell)
    if value == "" or value is None:
        return None
    if name == "记录时间" and isinstance(value, str):
        return value.replace("T", " ")[:16]
    return value


def _named(raw: dict[str, dict[str, Any]], ids: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {rid: {n: _norm(n, row.get(ids[n])) for n in READ_FIELDS} for rid, row in raw.items()}


def _desired(entry: dict[str, Any]) -> dict[str, Any]:
    return {"核对编号": entry_key(entry), "项目编号": entry["projectNo"], "类型": entry["type"], "处理状态": entry["status"],
            "关键信息是否受影响": entry["affected"], "字段": (entry.get("fields") or "无").strip() or "无", "内容": entry["content"].strip(),
            "建议": (entry.get("suggestion") or "").strip() or None, "来源": entry["source"]}


SELECT_FIELDS = ("类型", "关键信息是否受影响", "来源")      # “处理状态”在表里是文本列，不是单选


def _wire(name: str, value: Any) -> Any:
    if name in SELECT_FIELDS:
        return [value]
    if name == "记录时间":
        return f"{value}:00" if len(str(value)) == 16 else value
    return value


def plan(rows: dict[str, dict[str, Any]], entries: list[dict[str, Any]], registry: dict[str, Any], now: datetime, blocked: dict[str, Any]) -> dict[str, Any]:
    """creates / updates / unchanged from live rows. Legacy rows (key not starting with X-) are never looked at as ours."""
    ours: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for rid, row in rows.items():
        key = row.get("核对编号")
        if isinstance(key, str) and key.startswith(KEY_PREFIX):
            ours.setdefault(key, []).append((rid, row))
    stamp = now.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    unchanged: list[str] = []
    invalid: list[dict[str, Any]] = []
    duplicates: dict[str, list[str]] = {}
    held: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        problems = validate(entry, registry)
        if problems:
            invalid.append({"key": entry_key(entry) if isinstance(entry, dict) else None, "problems": problems})
            continue
        key = entry_key(entry)
        if key in seen:
            invalid.append({"key": key, "problems": ["同一清单内重复的稳定键"]})
            continue
        seen.add(key)
        if key in blocked:
            held.append({"key": key, "reason": "写入账里该键仍有未决写入", "open": blocked[key]})
            continue
        found = ours.get(key, [])
        want = _desired(entry)
        if len(found) > 1:
            duplicates[key] = [rid for rid, _ in found]
        elif not found:
            creates.append({"key": key, "fields": {**want, "记录时间": stamp}})
        else:
            rid, row = found[0]
            changed = {n: want[n] for n in COMPARED if _norm(n, want[n]) != row.get(n)}
            if changed:
                updates.append({"key": key, "recordId": rid, "fields": {**changed, "记录时间": stamp}, "old": {n: row.get(n) for n in (*changed, "记录时间")}})
            else:
                unchanged.append(key)
    orphans = sorted(k for k in ours if k not in seen)
    ok = not (invalid or duplicates or held)
    return {"creates": creates, "updates": updates, "unchanged": unchanged, "invalid": invalid, "duplicates": duplicates, "held": held,
            "keptNotInList": orphans, "status": "OK" if ok else "ATTENTION"}


def apply(planned: dict[str, Any], *, transport: Any, base: str, registry: dict[str, Any], journal: Journal, run_id: str | None = None) -> dict[str, Any]:
    """Write the plan. Before each write the live row is read again (a create only while the key has no row; an update only while the row still
    holds exactly the planned old values); an intent line goes first; an unknown outcome blocks that key and never replays."""
    ids, table = registry["ids"], registry["tableId"]
    run_id = run_id or uuid.uuid4().hex
    operator = {"pid": os.getpid(), "runId": run_id}
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    def wire(fields: dict[str, Any]) -> dict[str, Any]:
        return {ids[n]: _wire(n, v) for n, v in fields.items() if v is not None}

    def live_rows() -> dict[str, dict[str, Any]]:
        return _named(transport.list_rows(base, table, [ids[n] for n in READ_FIELDS]), ids)

    for item in planned["creates"]:
        key = item["key"]
        if len(unknown) >= MAX_UNKNOWN_PER_RUN:
            skipped.append({"key": key, "reason": "本次已有多个未知结果，其余留到下次"})
            continue
        if any(r.get("核对编号") == key for r in live_rows().values()):
            skipped.append({"key": key, "reason": "写前新鲜读取发现该键已有行，不再创建"})
            continue
        intent_id = uuid.uuid4().hex
        journal.add({"event": "intent", "intentId": intent_id, "runId": run_id, "operator": operator, "op": "create", "key": key, "fields": item["fields"]})
        try:
            body = transport.create_record(base, table, wire(item["fields"]))
        except Exception as error:   # any failure leaves the outcome open
            journal.add({"event": "unknown", "intentId": intent_id, "op": "create", "key": key, "error": str(error)[:300]})
            unknown.append({"key": key, "intentId": intent_id})
            continue
        record_id = _receipt_record_id(body)
        if record_id is None:
            journal.add({"event": "unknown", "intentId": intent_id, "op": "create", "key": key, "error": "创建回执缺少 record_id，效果未知"})
            unknown.append({"key": key, "intentId": intent_id})
            continue
        journal.add({"event": "result", "intentId": intent_id, "op": "create", "key": key, "recordId": record_id})
        written.append({"op": "create", "key": key, "recordId": record_id, "fields": item["fields"], "intentId": intent_id})
    for item in planned["updates"]:
        key, record_id = item["key"], item["recordId"]
        if len(unknown) >= MAX_UNKNOWN_PER_RUN:
            skipped.append({"key": key, "reason": "本次已有多个未知结果，其余留到下次"})
            continue
        live = live_rows().get(record_id)
        if live is None or any(live.get(n) != old for n, old in item["old"].items()):
            skipped.append({"key": key, "reason": "写前新鲜读取的旧值与计划不符，不覆盖", "live": live})
            continue
        intent_id = uuid.uuid4().hex
        journal.add({"event": "intent", "intentId": intent_id, "runId": run_id, "operator": operator, "op": "update", "key": key, "recordId": record_id,
                     "fields": item["fields"], "old": item["old"]})
        try:
            transport.upsert(base, table, record_id, wire(item["fields"]))
        except Exception as error:
            journal.add({"event": "unknown", "intentId": intent_id, "op": "update", "key": key, "recordId": record_id, "error": str(error)[:300]})
            unknown.append({"key": key, "intentId": intent_id})
            continue
        journal.add({"event": "result", "intentId": intent_id, "op": "update", "key": key, "recordId": record_id})
        written.append({"op": "update", "key": key, "recordId": record_id, "fields": item["fields"], "intentId": intent_id})
    problems = verify(live_rows() if written else {}, written)
    by_key = {w["key"]: w["intentId"] for w in written}
    for problem in problems:
        problem["intentId"] = by_key.get(problem["key"])
    if written:
        journal.add({"event": "readback", "runId": run_id, "written": len(written), "problems": problems})
    status = "UNKNOWN" if unknown else ("READBACK_PROBLEM" if problems else "OK")
    return {"written": written, "skipped": skipped, "unknown": unknown, "readbackProblems": problems, "status": status}


def verify(rows: dict[str, dict[str, Any]], written: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each written key exists exactly once, with the written values (record time compared to the minute)."""
    problems: list[dict[str, Any]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows.values():
        if isinstance(row.get("核对编号"), str):
            by_key.setdefault(row["核对编号"], []).append(row)
    for item in written:
        found = by_key.get(item["key"], [])
        if len(found) != 1:
            problems.append({"key": item["key"], "problem": f"读回该键有 {len(found)} 行"})
            continue
        wrong = {n: {"wrote": v, "read": found[0].get(n)} for n, v in item["fields"].items() if _norm(n, v) != found[0].get(n)}
        if wrong:
            problems.append({"key": item["key"], "problem": "读回值与写入不符", "fields": wrong})
    return problems


REJECTION = re.compile(r'"ok":\s*false.*?"code":\s*\d{5,}', re.S)


def reconcile(*, registry: dict[str, Any], base: str, transport: Any, business_root: Path, intent_id: str, now: datetime | None = None,
              min_quiet_seconds: int = 3600, clock: Any = None) -> dict[str, Any]:
    """Read-only technical recovery of one unknown create/update intent; appends a ``reconciled`` event to the write journal, never writes the table.

    APPLIED_PROVEN: the key has exactly one row and it holds every value the intent wrote. NOT_APPLIED_PROVEN: the key has no row and either
    the recorded error is the service's own structured rejection (``"ok": false`` with an API error code: the request was refused,
    nothing accepted) and the read is at least a minute after the intent. A transport error after sending, however long ago, with no row
    on read-back stays STILL_UNKNOWN: absence is not proof that the request was never accepted."""
    now = (now or _now()).astimezone(SHANGHAI)
    journal = Journal(Path(business_root) / "运行记录" / LEDGER_DIR, clock)
    state = journal.state()
    item = state["intents"].get(intent_id)
    if item is None:
        raise ExceptionError("账里没有这个意图")
    if item["reconciled"]:
        return {"status": "ALREADY_RECONCILED", "intentId": intent_id}
    if item["status"] not in ("UNKNOWN", "PENDING"):
        return {"status": "NOT_BLOCKED", "intentId": intent_id}
    age = (now - datetime.fromisoformat(item["intentAt"])).total_seconds()
    rejected = bool(REJECTION.search(str(item.get("error") or "")))
    rows = _named(transport.list_rows(base, registry["tableId"], [registry["ids"][n] for n in READ_FIELDS]), registry["ids"])
    found = [(rid, row) for rid, row in rows.items() if row.get("核对编号") == item["key"]]
    if len(found) > 1:
        return {"status": "DUPLICATE_ROWS", "intentId": intent_id, "records": [rid for rid, _ in found]}
    if found:
        rid, row = found[0]
        wanted = {n: v for n, v in (item["fields"] or {}).items() if n != "记录时间"}
        if all(_norm(n, v) == row.get(n) for n, v in wanted.items()):
            status, evidence = "APPLIED_PROVEN", {"recordId": rid}
        else:
            return {"status": "ROW_DIFFERS", "intentId": intent_id, "recordId": rid}
    elif item["op"] == "update":
        return {"status": "UPDATE_NEEDS_MANUAL", "intentId": intent_id}
    elif rejected and age >= 60:
        status, evidence = "NOT_APPLIED_PROVEN", {"basis": "服务端结构化拒绝", "ageSeconds": round(age)}
    elif rejected:
        return {"status": "TOO_EARLY", "intentId": intent_id, "ageSeconds": round(age), "needSeconds": 60}
    else:
        # 发送后传输未知：时间过去、回读仍无该行，只能说明“当前看不到”，不能证明请求没有被服务端接受；保持未决。
        # 只有原调用留下的服务端明确结构化拒绝（或回读到唯一行且含全部写入值）才能闭合。
        return {"status": "STILL_UNKNOWN", "intentId": intent_id, "ageSeconds": round(age),
                "reason": "没有服务端明确拒绝；无行与静默时间都不能证明未写入，保留未决"}
    journal.add({"event": "reconciled", "intentId": intent_id, "op": item["op"], "key": item["key"], "status": status, "evidence": evidence})
    return {"status": status, "intentId": intent_id, "evidence": evidence}


def run(*, registry: dict[str, Any], entries: list[dict[str, Any]], base: str, transport: Any, business_root: Path, execute: bool,
        now: datetime | None = None, clock: Any = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(SHANGHAI)
    journal = Journal(Path(business_root) / "运行记录" / LEDGER_DIR, clock)
    state = journal.state()
    if state["corrupt"]:
        raise ExceptionRuntimeError("异常登记账有无法解析的行，先人工核对，不写：" + "、".join(state["corrupt"][:5]))
    rows = _named(transport.list_rows(base, registry["tableId"], [registry["ids"][n] for n in READ_FIELDS]), registry["ids"])
    planned = plan(rows, entries, registry, now, journal.blocked(state))
    report: dict[str, Any] = {"schema": SCHEMA, "registryVersion": registry["version"], "plan": planned, "executed": False}
    if execute:
        result = apply(planned, transport=transport, base=base, registry=registry, journal=journal)
        report.update(executed=True, apply=result, status=result["status"] if result["status"] != "OK" else planned["status"])
    else:
        report["status"] = planned["status"]
    return report
