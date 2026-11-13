"""0104 source-field sync into the Feishu case Base, through the official ``lark-cli`` (v2).

Only source fields explicitly allow-listed by field ID, on explicitly allow-listed record IDs,
and classified ``source-sync-whitelist`` by a pinned protection contract can be written.
Human, derived, structural, admin and legacy fields, unknown fields and field names are always
refused. Every refusal happens before the first external call. There is no "all records" mode.

Modes (``mode`` in the spec; cloud access also needs the matching command-line flag)
- ``plan``       validate locally and print the exact wire requests and ledger state; never calls
                 Feishu and never writes the ledger.
- ``execute``    ``--execute``: ledger check and per-target locks, full readback with strict record
                 identity, stable-key check, intent recorded before the first write, one upsert per
                 record, full readback and protection assertions.
- ``reconcile``  ``--reconcile``: read-only cloud readback of an unresolved earlier write, compared
                 with the intent recorded in the ledger. It never writes to Feishu. Only a matching
                 stable key plus every intended value present clears the block (APPLIED). An old value
                 observed later is only an observation: it cannot prove the request never ran or will
                 not arrive late, so the target stays blocked until a recorded human release with
                 evidence.

Unknown results are never replayed. A write whose effect is unknown, failed, or not yet verified
blocks every later write to the same exact target (base, table, record) until reconciliation or a
recorded human release; the ledger is append-only and independent of ``logDir``.

V3 (``FeishuSourceSyncSpecV3``) adds a separate, create-only branch for entities that a fresh full-page
key scan proves absent (see "V3 create" below). It never updates, re-parents or renumbers an existing
row and uses the same append-only ledger directory (intent keys are derived from the stable key because
no recordId exists before the create).

Standard library only; the transport is injectable so tests never reach the network.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

if __package__:
    from . import case_lifecycle
    from . import field_policy
    from . import field_validation as fv
    from . import initial_scope
    from . import ledger_views as _views
    from . import workspace_state as _ws
else:
    import case_lifecycle
    import field_policy
    import field_validation as fv
    import initial_scope
    import ledger_views as _views
    import workspace_state as _ws

SPEC_SCHEMA = "FeishuSourceSyncSpecV2"
REPORT_SCHEMA = "FeishuSourceSyncReportV2"
WHITELIST = "source-sync-whitelist"
SCALAR_TYPES = {"text", "number", "select", "datetime"}
DERIVED_CLASSES = {"derived-readonly"}
DERIVED_TYPES = {"formula", "updated_at", "created_at", "lookup"}
FIELD_ID = re.compile(r"^fld[0-9A-Za-z]{6,}$")
RECORD_ID = re.compile(r"^rec[0-9A-Za-z]{6,}$")
TABLE_ID = re.compile(r"^tbl[0-9A-Za-z]{6,}$")
DATETIME_INPUT = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2})(?::00)?)?$")
HARD_CAP_RECORDS = 20
DEFAULT_CLI = "/opt/devtools/nvm/versions/node/v24.21.0/bin/lark-cli"
DEFAULT_LEDGER = Path.home() / ".local" / "state" / "xf-product-case-registry" / "feishu-source-sync"
VALUE_STATES = {"observed", "not-provided", "unknown"}
SOURCE_TZ = timezone(timedelta(hours=8))  # responses carry timezone Asia/Shanghai
CHINA_DST_OFFSET = timedelta(hours=9)  # Asia/Shanghai summer time, 1986-1991
# Manual and relation fields from the v8 contract. They stay denied even if a later contract
# edit mislabels them as source fields. Unknown or new fields are refused by default as well.
# 2026-11-12: a contract that classifies one of them ``business-label`` / ``brigade-maintained`` opens it
# to the two non-source bases of field_policy (label-initial, user-delegated) only; a supervision-fact
# update (source-observed) can still never write it.
ALWAYS_DENIED = {
    "fld4mVYy5Y": "案卷.是否网售(人工)",
    "fldvHLpSKI": "案卷.是否加油加气站(人工)",
    "fld5E2njAU": "案卷.是否微型消防站(人工)",
    "fldrKL9rDE": "案卷.是否核实(人工)",
    "fldW2kJC0V": "案卷.无值说明(人工，原名核实说明)",
    "fldtBHoCzK": "案卷.是否维修后灭火器(人工，2026-11-09 起由大队填报)",
    "fldoFEnhmd": "案卷.涉及网售产品(人工关系)",
    "fld4atEDRT": "检查.是否核实(人工)",
    "fldYkXBvEt": "检查.无值说明(人工，原名核实说明)",
    "fldwsnqeJ4": "产品.是否核实(人工)",
    "fld1Plox8P": "产品.无值说明(人工，原名核实说明)",
    "fldVZE4Yc9": "产品.网售标记案卷(人工关系反向)",
}
# Stable business key per table; it must match before any write.
STABLE_KEYS = {"案卷主表": "fld1lHXHpk", "检查明细": "fld5ujrPv4", "产品明细": "fldqlwHjUW", "维修企业": "fld7d3PedK"}
# 稳定键：来源键，或由管理员维护的内部编号(如企业编号)。
STABLE_KEY_CLASSES = frozenset({WHITELIST, "admin-only"})
WRITABLE_CLASSES = frozenset().union(*field_policy.ALLOWED_CLASSES.values())
OPENED_CLASSES = frozenset({field_policy.CLASS_LABEL, field_policy.CLASS_BRIGADE})


def _denied(field_id: str, meta: dict[str, Any] | None) -> bool:
    """永久禁写名单仍然有效，除非最新合同把该字段明确归为标签/大队维护字段(只对非来源依据开放)。"""
    return field_id in ALWAYS_DENIED and (meta is None or meta.get("class") not in OPENED_CLASSES)
NEGATIVE_LITERALS = {"否", "0", 0, False}
CLEAR_FINALS = {"OK", "UNCHANGED", "NOT_ATTEMPTED"}
# APPLIED: positive evidence that the request took effect. NOT_APPLIED_PROVEN: the strict technical recovery below (the run ended, the
# service's own revision history shows no write after the intent, and a quiet window has passed). A bare old-value readback proves nothing.
CLEAR_RECONCILED = {"APPLIED", "NOT_APPLIED_PROVEN"}
RECOVERY_MIN_QUIET_SECONDS = 3600
HISTORY_CREATE_SLACK_BEFORE, HISTORY_CREATE_SLACK_AFTER = 5, 120
RELEASE_EVIDENCE_KINDS = {"request-terminal-proof", "service-side-record", "cli-process-log", "human-decision-record"}


class SyncError(Exception):
    """Refused before any external call (exit code 2)."""


class RuntimeSyncError(Exception):
    """A failure while talking to Feishu or while verifying (exit code 3)."""


class Ambiguous(Exception):
    """A cell has a shape this version does not interpret; comparisons fail closed."""


class Transport(Protocol):
    def get_records(self, base: str, table: str, record_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    def upsert(self, base: str, table: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]: ...
    # V3 create branch (optional for V2-only transports)
    def find_records(self, base: str, table: str, key_field_id: str, value: str) -> list[str]: ...
    def create_record(self, base: str, table: str, fields: dict[str, Any]) -> dict[str, Any]: ...
    # Service-side revision history (optional: only the technical recovery below needs it)
    def get_record_history(self, base: str, table: str, record_id: str) -> list[dict[str, Any]]: ...


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(value: Any) -> Any:
    """Order-insensitive, display-name-insensitive raw comparison form for a cell."""
    if isinstance(value, list):
        items = [canon(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, dict):
        if "id" in value:
            return {"id": value["id"]}
        return {key: canon(item) for key, item in sorted(value.items())}
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# ---------------------------------------------------------------------------------------------
# Typed cells: one definition of the wire value and of the comparison, per contract field type.
# ---------------------------------------------------------------------------------------------


def wire_value(field_type: str, new: Any) -> Any:
    """Official lark-cli CellValue for a source value; ambiguous input is refused."""
    if field_type == "text":
        if not isinstance(new, str) or not new:
            raise SyncError("text 字段的新值必须是非空字符串")
        return new
    if field_type == "select":
        if not isinstance(new, str) or not new:
            raise SyncError("select 字段的新值必须是单个非空选项字符串(程序序列化为单元素数组)")
        return [new]
    if field_type == "number":
        if isinstance(new, bool) or not isinstance(new, (int, float)) or not math.isfinite(new):
            raise SyncError("number 字段的新值必须是有限数字(不接受布尔/字符串)")
        return new
    if field_type == "datetime":
        match = DATETIME_INPUT.match(new) if isinstance(new, str) else None
        if match is None:
            raise SyncError("datetime 字段的新值必须是 YYYY-MM-DD 或 YYYY-MM-DD HH:MM(按 Asia/Shanghai)")
        text = f"{match.group(1)} {match.group(2) or '00:00'}"
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise SyncError("datetime 字段的新值不是有效日期时间") from error
        return text
    raise SyncError(f"本版不支持写入 {field_type} 类型")


def norm_cell(field_type: str, cell: Any) -> Any:
    """Comparable form of a stored or intended cell; raises Ambiguous for unknown shapes."""
    if cell is None:
        return None
    if field_type == "text":
        if isinstance(cell, str):
            return cell
        raise Ambiguous
    if field_type == "select":
        if isinstance(cell, str):
            return cell
        if isinstance(cell, list) and all(isinstance(item, str) for item in cell):
            if not cell:
                return None
            return cell[0] if len(cell) == 1 else tuple(sorted(cell))
        raise Ambiguous
    if field_type == "number":
        if isinstance(cell, bool) or not isinstance(cell, (int, float)):
            raise Ambiguous
        return float(cell)
    if field_type == "datetime":
        if not isinstance(cell, str):
            raise Ambiguous
        try:
            parsed = datetime.fromisoformat(cell)
        except ValueError as error:
            raise Ambiguous from error
        if parsed.tzinfo is not None:
            # 1986-1991 夏令时期间飞书按 +09:00 返回同一上海墙上时间；墙上时间即业务值。
            if parsed.utcoffset() == CHINA_DST_OFFSET and 1986 <= parsed.year <= 1991:
                return parsed.replace(tzinfo=None)
            parsed = parsed.astimezone(SOURCE_TZ).replace(tzinfo=None)
        return parsed
    raise Ambiguous


def cell_is_empty(cell: Any) -> bool:
    """A stored cell that carries no value (missing, empty text or empty list)."""
    return cell is None or cell == "" or cell == []


def cell_equals(field_type: str, stored: Any, intended_wire: Any) -> bool:
    try:
        return norm_cell(field_type, stored) == norm_cell(field_type, intended_wire if field_type != "datetime" else intended_wire.replace(" ", "T"))
    except Ambiguous:
        return False


# ---------------------------------------------------------------------------------------------
# Contract and spec validation (everything here runs before the first external call)
# ---------------------------------------------------------------------------------------------


def load_contract(path: Path, expected_sha: str, section: str | None = None) -> dict[str, Any]:
    """``section`` 选合同里的一个子库(如 maintenanceBase：维修企业台账)，它自带 baseId 与 tables。"""
    if not path.is_file():
        raise SyncError("保护合同文件不存在")
    if file_sha(path) != expected_sha:
        raise SyncError("保护合同摘要与 spec 固定值不一致；合同变化须另版冻结")
    contract = json.loads(path.read_text(encoding="utf-8"))
    if section is not None:
        contract = contract.get(section) if isinstance(contract, dict) else None
        if not isinstance(contract, dict):
            raise SyncError(f"保护合同没有该子库：{section}")
    if not isinstance(contract.get("tables"), dict) or not contract.get("baseId"):
        raise SyncError("保护合同缺少 baseId 或 tables")
    return contract


def _option_names(options: Any) -> list[str] | None:
    """Select option names from either contract shape ([{name, optionId}] or [name]); None when the contract lists none."""
    if not isinstance(options, list):
        return None
    names = [o.get("name") if isinstance(o, dict) else o for o in options]
    return [n for n in names if isinstance(n, str)]


def field_index(contract: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """tableId -> fieldId -> {name, type, class, table, syncWrite, options, key(tableId/fieldId)}."""
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for table_name, table in contract["tables"].items():
        table_id = table.get("tableId")
        if not isinstance(table_id, str):
            raise SyncError("合同表缺少 tableId")
        index[table_id] = {
            field["fieldId"]: {
                "name": field["name"],
                "type": field["type"],
                "class": field["class"],
                "table": table_name,
                "syncWrite": field.get("syncWrite"),
                "options": _option_names(field.get("options")),
                "key": f"{table_id}/{field['fieldId']}",
            }
            for field in table.get("fields", [])
        }
    return index


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SyncError(message)


def _table_name(index: dict[str, dict[str, dict[str, Any]]], table_id: str) -> str:
    return next(iter(index[table_id].values()))["table"]


def validate_spec(spec: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Every rule that must hold before the first external call. Returns the resolved plan."""
    _require(isinstance(spec, dict) and spec.get("schemaVersion") == SPEC_SCHEMA, "spec 格式无效(需要 FeishuSourceSyncSpecV2)")
    _require(spec.get("mode") in {"plan", "execute", "reconcile"}, "mode 必须是 plan / execute / reconcile")
    target = spec.get("target")
    _require(isinstance(target, dict), "缺少 target")
    base = target.get("baseToken")
    _require(isinstance(base, str) and base == contract["baseId"], "目标 Base 与保护合同不一致")
    _require(
        isinstance(target.get("profile"), str) and target["profile"] and target.get("identity") in {"user", "bot"},
        "必须显式给出 profile 与 identity",
    )
    index = field_index(contract)
    allow = spec.get("allowlist")
    _require(isinstance(allow, dict) and allow, "allowlist 为空；不回落到任何全量数据")
    allowed: dict[str, dict[str, set[str]]] = {}
    for table_id, entry in allow.items():
        _require(TABLE_ID.match(table_id) is not None and table_id in index, f"allowlist 表不在保护合同中：{table_id}")
        _require(isinstance(entry, dict), "allowlist 表项无效")
        records, fields = entry.get("records"), entry.get("fields")
        _require(isinstance(records, list) and records, f"{table_id} 的 record 白名单为空")
        _require(isinstance(fields, list) and fields, f"{table_id} 的 fieldId 白名单为空")
        _require(all(isinstance(r, str) and RECORD_ID.match(r) for r in records), "record_id 格式无效或仍是占位")
        _require(len(set(records)) == len(records), "record 白名单含重复")
        for field_id in fields:
            _require(isinstance(field_id, str) and FIELD_ID.match(field_id) is not None, f"白名单必须是 fieldId，不接受名称：{field_id}")
            meta = index[table_id].get(field_id)
            _require(not _denied(field_id, meta), f"人工/关系字段永久禁写：{ALWAYS_DENIED.get(field_id, field_id)}")
            _require(meta is not None, f"字段不在保护合同中（新增字段默认拒写）：{field_id}")
            _require(meta["class"] in WRITABLE_CLASSES, f"字段不是 {WHITELIST}：{meta['name']}({meta['class']})")
            _require(meta["type"] in SCALAR_TYPES, f"本版只写标量源字段，拒绝 {meta['type']}：{meta['name']}")
            _require(not str(meta.get("syncWrite") or "").lower().startswith("create-once"),
                     f"合同把该字段限定为创建时一次写入(create-once)，不得作为更新目标：{meta['name']}")
        stable = STABLE_KEYS.get(_table_name(index, table_id))
        _require(stable in index[table_id] and index[table_id][stable]["class"] in STABLE_KEY_CLASSES, "稳定键字段未在合同中确认")
        allowed[table_id] = {"records": set(records), "fields": set(fields)}
    updates = spec.get("updates")
    _require(isinstance(updates, list) and updates, "updates 为空")
    limits = spec.get("limits") or {}
    cap = limits.get("maxRecords")
    _require(isinstance(cap, int) and not isinstance(cap, bool) and 1 <= cap <= HARD_CAP_RECORDS, f"limits.maxRecords 必须是 1..{HARD_CAP_RECORDS}")
    _require(len(updates) <= cap, "更新记录数超过 maxRecords")
    seen: set[tuple[str, str]] = set()
    pattern = spec.get("syntheticKeyPattern")
    synthetic = spec.get("syntheticOnly") is True
    _require(not synthetic or (isinstance(pattern, str) and pattern), "syntheticOnly 需要 syntheticKeyPattern")
    plan: list[dict[str, Any]] = []
    for update in updates:
        _require(isinstance(update, dict), "update 无效")
        table_id, record_id = update.get("tableId"), update.get("recordId")
        _require(table_id in allowed and record_id in allowed[table_id]["records"], f"目标记录不在白名单：{table_id}/{record_id}")
        _require((table_id, record_id) not in seen, "同一记录重复更新")
        seen.add((table_id, record_id))
        stable = STABLE_KEYS[_table_name(index, table_id)]
        key = update.get("stableKey")
        _require(
            isinstance(key, dict) and key.get("fieldId") == stable and isinstance(key.get("value"), str) and key["value"],
            f"必须给出稳定键 {stable} 及其值",
        )
        _require(not synthetic or re.search(pattern, key["value"]) is not None, "syntheticOnly：稳定键不符合合成测试模式")
        fields = update.get("fields")
        _require(isinstance(fields, dict) and fields, "update.fields 为空")
        writes: dict[str, dict[str, Any]] = {}
        skipped: dict[str, dict[str, Any]] = {}
        for field_id, item in fields.items():
            _require(FIELD_ID.match(field_id) is not None, f"update 必须用 fieldId：{field_id}")
            _require(not _denied(field_id, index[table_id].get(field_id)), f"人工/关系字段永久禁写：{ALWAYS_DENIED.get(field_id, field_id)}")
            _require(field_id in allowed[table_id]["fields"], f"字段不在白名单：{field_id}")
            _require(field_id != stable, "稳定键字段不得作为更新目标")
            _require(isinstance(item, dict), "每个字段需 {new, basis}")
            basis = item.get("basis")
            if item.get("restore") is not None:   # 精确逆操作：只恢复本助手早先写入的误写值，条件在 execute 中对账本逐项核验
                restore = item["restore"]
                _require(isinstance(restore, dict) and _text(restore.get("intentId")) and "new" in item and item["new"] is None
                         and _text(item.get("expectedOld")), "restore 需要 intentId、new 显式为 null、expectedOld 为被恢复的误写值")
                _require(isinstance(basis, dict) and basis.get("kind") == "source-observed" and basis.get("valueState") == "observed"
                         and _text(basis.get("sourceRef")) and _text(basis.get("observedAt")), "restore 需要 observed 的 basis 与 sourceRef")
                writes[field_id] = {"name": index[table_id][field_id]["name"], "type": index[table_id][field_id]["type"], "new": None, "wire": None,
                                    "basis": basis, "expectedOld": item["expectedOld"], "restore": restore}
                continue
            problem = field_policy.basis_problem(basis)
            _require(problem is None and (basis["kind"] != field_policy.BASIS_SOURCE or basis.get("valueState") in VALUE_STATES),
                     "每个字段需 basis{kind:source-observed, valueState:observed|not-provided|unknown, sourceRef, observedAt}"
                     + (f"；{problem}" if problem else ""))
            meta = index[table_id][field_id]
            problem = field_policy.write_problem(meta, basis, item)   # 一份字段归属规则：依据种类决定可写的字段类别
            _require(problem is None, f"字段归属规则拒绝：{problem}")
            _require(basis["kind"] != field_policy.BASIS_SOURCE or field_id not in ALWAYS_DENIED,
                     f"人工/关系字段永久禁写：{ALWAYS_DENIED.get(field_id, field_id)}")
            if basis.get("valueState", "observed") != "observed":
                _require("new" not in item, "valueState 为 not-provided/unknown 时不得携带 new；缺失只跳过，不覆盖已有值")
                skipped[field_id] = {"name": meta["name"], "valueState": basis["valueState"], "basis": basis}
                continue
            _require("new" in item, "valueState=observed 需要 new")
            value = item["new"]
            if field_policy.needs_explicit_negative(basis) and isinstance(value, (str, int, float, bool)) and value in NEGATIVE_LITERALS:
                _require(basis.get("explicitNegative") is True, "“否/0”只能来自来源明确否定(basis.explicitNegative)")
            if meta["type"] == "select" and meta.get("options") is not None:
                # 最新合同列出的选项是唯一合法取值；合同外的值在任何 transport 调用与业务/账写入前拒绝。
                _require(isinstance(value, str) and value in meta["options"], f"选项不在最新合同中：{meta['name']}={value!r}")
            writes[field_id] = {
                "name": meta["name"], "type": meta["type"], "new": value, "wire": wire_value(meta["type"], value),
                "basis": basis, "expectedOld": item.get("expectedOld", "__absent__"),
            }
        plan.append({"tableId": table_id, "recordId": record_id, "stableKey": key, "writes": writes, "skipped": skipped})
    if spec["mode"] != "plan":
        _require(isinstance(spec.get("logDir"), str) and spec["logDir"], f"{spec['mode']} 需要显式 logDir")
    if spec["mode"] == "reconcile" and spec.get("humanRelease") is not None:
        release = spec["humanRelease"]
        _require(isinstance(release, dict), "humanRelease 格式无效")
        _require(all(isinstance(release.get(k), str) and release[k].strip() for k in ("reason", "operator", "intentId")), "humanRelease 需要 reason、operator 与精确 intentId(只放行该意图)")
        _require(release.get("acknowledgesLateWriteRisk") is True, "humanRelease 需要 acknowledgesLateWriteRisk: true(确认知悉迟到写入风险)")
        evidence = release.get("evidence")
        _require(isinstance(evidence, list) and evidence, "humanRelease 需要至少一条依据 evidence；旧值相等不是依据")
        for item in evidence:
            _require(isinstance(item, dict) and item.get("kind") in RELEASE_EVIDENCE_KINDS, f"evidence.kind 必须是 {sorted(RELEASE_EVIDENCE_KINDS)} 之一")
            _require(isinstance(item.get("ref"), str) and item["ref"].strip(), "evidence.ref 不能为空")
    recovery = spec.get("technicalRecovery")
    if recovery is not None:
        _require(spec["mode"] == "reconcile" and isinstance(recovery, dict), "technicalRecovery 仅限 reconcile 且为对象")
        _require(spec.get("humanRelease") is None, "technicalRecovery 与 humanRelease 不能同时使用")
        _require(recovery.get("kind") == "history-quiet-readback", "technicalRecovery.kind 必须是 history-quiet-readback")
        _require(isinstance(recovery.get("intentId"), str) and recovery["intentId"].strip(), "technicalRecovery 需要精确 intentId(只恢复该意图)")
        _require(isinstance(recovery.get("minQuietSeconds"), int) and not isinstance(recovery["minQuietSeconds"], bool)
                 and recovery["minQuietSeconds"] >= RECOVERY_MIN_QUIET_SECONDS, f"minQuietSeconds 不得小于 {RECOVERY_MIN_QUIET_SECONDS}")
    return {"index": index, "allowed": allowed, "plan": plan}


def build_requests(checked: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "tableId": item["tableId"],
            "recordId": item["recordId"],
            "stableKey": item["stableKey"],
            "wire": {fid: write["wire"] for fid, write in item["writes"].items()},
            "skippedFields": {fid: skip["valueState"] for fid, skip in item["skipped"].items()},
        }
        for item in checked["plan"]
    ]


# ---------------------------------------------------------------------------------------------
# Strict response parsing and the transport
# ---------------------------------------------------------------------------------------------


def parse_get_response(body: Any, requested: list[str]) -> dict[str, dict[str, Any]]:
    """record_id -> {fieldId: cell}. Identity comes only from the service receipt, never from order."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise RuntimeSyncError("回读结构异常：缺少 data")
    ids, field_ids, rows = data.get("record_id_list"), data.get("field_id_list"), data.get("data")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise RuntimeSyncError("回读缺少 record_id_list，不能按请求顺序猜配记录")
    if not isinstance(field_ids, list) or not all(isinstance(f, str) for f in field_ids) or len(set(field_ids)) != len(field_ids):
        raise RuntimeSyncError("回读缺少或重复 field_id_list")
    if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
        raise RuntimeSyncError("回读 data 不是行矩阵")
    if len(set(ids)) != len(ids):
        raise RuntimeSyncError("回读 record_id_list 含重复 ID")
    if len(ids) != len(rows):
        raise RuntimeSyncError("回读 record_id_list 与 data 行数不一致")
    unknown = sorted(set(ids) - set(requested))
    if unknown:
        raise RuntimeSyncError(f"回读含未请求的 record_id：{unknown}")
    missing = sorted(set(requested) - set(ids))
    if missing:
        raise RuntimeSyncError(f"目标记录读不到：{missing}")
    if any(len(row) != len(field_ids) for row in rows):
        raise RuntimeSyncError("回读行宽与 field_id_list 不一致")
    return {rid: dict(zip(field_ids, row, strict=True)) for rid, row in zip(ids, rows, strict=True)}


def _receipt_record_id(body: Any) -> str | None:
    """Exactly one record id from the service receipt; several/none => None (never guessed from order)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    found: list[str] = []
    record = data.get("record")
    if isinstance(record, dict) and isinstance(record.get("record_id"), str):
        found.append(record["record_id"])
    if isinstance(record, dict) and isinstance(record.get("record_id_list"), list):  # 真实 CLI 回执形态：data.record.record_id_list
        found.extend(x for x in record["record_id_list"] if isinstance(x, str))
    if isinstance(data.get("record_id"), str):
        found.append(data["record_id"])
    for key in ("record_id_list", "record_ids"):
        if isinstance(data.get(key), list):
            found.extend(x for x in data[key] if isinstance(x, str))
    records = data.get("records")
    if isinstance(records, list):
        found.extend(r["record_id"] for r in records if isinstance(r, dict) and isinstance(r.get("record_id"), str))
    unique = list(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 and RECORD_ID.match(unique[0]) else None


def parse_list_response(body: Any, key_field_id: str) -> tuple[list[str], list[Any]]:
    """record ids and the key column cells of one ``+record-list`` page; identity only from the service receipt."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise RuntimeSyncError("列表结构异常：缺少 data")
    ids, field_ids, rows = data.get("record_id_list"), data.get("field_id_list"), data.get("data")
    if not isinstance(ids, list) or not isinstance(field_ids, list) or not isinstance(rows, list):
        raise RuntimeSyncError("列表缺少 record_id_list/field_id_list/data")
    if len(ids) != len(rows) or len(set(ids)) != len(ids):
        raise RuntimeSyncError("列表 record_id_list 与 data 不一致或重复")
    if key_field_id not in field_ids:
        raise RuntimeSyncError("列表缺少稳定键列，不能确认唯一性")
    position = field_ids.index(key_field_id)
    if any(not isinstance(row, list) or len(row) != len(field_ids) for row in rows):
        raise RuntimeSyncError("列表行宽与 field_id_list 不一致")
    return ids, [row[position] for row in rows]


class LarkCliTransport:
    """Official lark-cli, existing authenticated profile. No credential is read here."""

    READ_VERBS = frozenset({"+record-get", "+record-list", "+record-history-list"})
    READ_ATTEMPTS = 5

    def __init__(self, cli: str, profile: str, identity: str, runner: Any = None, timeout: int = 120, sleep: Any = None):
        self.cli, self.profile, self.identity = cli, profile, identity
        self.runner = runner or subprocess.run
        self.timeout = timeout
        self.sleep = sleep or time.sleep

    @staticmethod
    def _network_transport_failure(result: Any) -> bool:
        """官方 CLI 的网络层失败：stdout 为空、stderr 是 {error:{type:"network"}}；请求没有得到服务端回执。"""
        try:
            error = json.loads(getattr(result, "stderr", "") or "")["error"]
        except (ValueError, KeyError, TypeError):
            return False
        return isinstance(error, dict) and error.get("type") == "network" and not (getattr(result, "stdout", "") or "").strip()

    def _run(self, args: list[str]) -> dict[str, Any]:
        argv = [self.cli, "base", *args, "--profile", self.profile, "--as", self.identity]
        # 只读调用遇到明确的网络层失败可重试(无副作用)；写调用从不重试，结果未知按 UNKNOWN 处理
        attempts = self.READ_ATTEMPTS if args and args[0] in self.READ_VERBS else 1
        for attempt in range(1, attempts + 1):
            try:
                result = self.runner(argv, capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as error:
                raise RuntimeSyncError("UNKNOWN: 官方 CLI 超时；效果未知，需按 record_id 回读对账") from error
            except OSError as error:
                raise RuntimeSyncError(f"官方 CLI 无法启动：{error.__class__.__name__}") from error
            if attempt < attempts and self._network_transport_failure(result):
                self.sleep(1.5 * attempt)
                continue
            break
        try:
            body = json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeSyncError("UNKNOWN: 官方 CLI 输出不是 JSON") from error
        self.last_body = body  # 供创建回执解析失败时留证（只保存在日志，不用于判断身份）
        if result.returncode != 0 or not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeSyncError("官方 CLI 返回失败")
        return body

    def get_records(self, base: str, table: str, record_ids: list[str]) -> dict[str, dict[str, Any]]:
        args = ["+record-get", "--base-token", base, "--table-id", table, "--format", "json"]
        for record_id in record_ids:
            args += ["--record-id", record_id]
        return parse_get_response(self._run(args), record_ids)

    def upsert(self, base: str, table: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        body = self._run(
            ["+record-upsert", "--base-token", base, "--table-id", table, "--record-id", record_id,
             "--json", json.dumps(fields, ensure_ascii=False)]
        )
        data = body.get("data")
        rid = data.get("record", {}).get("record_id") if isinstance(data, dict) and isinstance(data.get("record"), dict) else None
        if rid is not None and rid != record_id:
            raise RuntimeSyncError("UNKNOWN: 写入回执的 record_id 与请求不一致")
        return body

    def find_records(self, base: str, table: str, key_field_id: str, value: str) -> list[str]:
        """Full-page scan of one key column; exact string match on the client (no server filter trusted)."""
        matches: list[str] = []
        offset, limit = 0, 200
        while True:
            body = self._run(["+record-list", "--base-token", base, "--table-id", table, "--field-id", key_field_id,
                              "--limit", str(limit), "--offset", str(offset), "--format", "json"])
            ids, rows = parse_list_response(body, key_field_id)
            for record_id, cell in zip(ids, rows, strict=True):
                if cell == value:
                    matches.append(record_id)
            if len(ids) < limit:
                return matches
            offset += limit

    def list_rows(self, base: str, table: str, field_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """Full-page read of selected columns: record_id -> {fieldId: cell}. Identity only from the service receipt."""
        result: dict[str, dict[str, Any]] = {}
        offset, limit = 0, 200
        while True:
            args = ["+record-list", "--base-token", base, "--table-id", table]
            for field_id in field_ids or []:
                args += ["--field-id", field_id]
            body = self._run([*args, "--limit", str(limit), "--offset", str(offset), "--format", "json"])
            data = body.get("data") if isinstance(body, dict) else None
            ids, fids, rows = (data or {}).get("record_id_list"), (data or {}).get("field_id_list"), (data or {}).get("data")
            if not isinstance(ids, list) or not isinstance(fids, list) or not isinstance(rows, list) or len(ids) != len(rows):
                raise RuntimeSyncError("列表结构异常：record_id_list/field_id_list/data 不一致")
            if len(set(ids)) != len(ids) or any(i in result for i in ids) or any(not isinstance(r, list) or len(r) != len(fids) for r in rows):
                raise RuntimeSyncError("列表含重复 record_id 或行宽与 field_id_list 不一致")
            for record_id, row in zip(ids, rows, strict=True):
                result[record_id] = dict(zip(fids, row, strict=True))
            if len(ids) < limit:
                return result
            offset += limit

    def get_record_history(self, base: str, table: str, record_id: str) -> list[dict[str, Any]]:
        """Service-side revisions of one record (``+record-history-list``, read-only): [{rev, createTime(epoch s), operator, fieldIds}], newest first."""
        items: list[dict[str, Any]] = []
        max_version: int | None = None
        for _ in range(40):
            args = ["+record-history-list", "--base-token", base, "--table-id", table, "--record-id", record_id, "--page-size", "50"]
            if max_version is not None:
                args += ["--max-version", str(max_version)]
            data = self._run(args).get("data")
            page = data.get("items") if isinstance(data, dict) else None
            if not isinstance(page, list):
                raise RuntimeSyncError("历史结构异常：缺少 data.items")
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get("rev"), int) or not isinstance(item.get("create_time"), (int, float)):
                    raise RuntimeSyncError("历史条目缺少 rev/create_time")
                changes = [c for c in (item.get("field_changes") or []) if isinstance(c, dict) and c.get("field_id")]
                items.append({"rev": item["rev"], "createTime": float(item["create_time"]), "operator": item.get("operator"),
                              "fieldIds": sorted({c["field_id"] for c in changes}),
                              "changes": [{"fieldId": c["field_id"], "before": c.get("before"), "after": c.get("after")} for c in changes]})
            if not data.get("has_more"):
                if len({i["rev"] for i in items}) != len(items):
                    raise RuntimeSyncError("历史含重复 rev")
                return sorted(items, key=lambda i: -i["rev"])
            if not page:
                raise RuntimeSyncError("历史分页异常：has_more 但无条目")
            max_version = min(i["rev"] for i in page) - 1
        raise RuntimeSyncError("历史分页过多，拒绝作为证据")

    def delete_records(self, base: str, table: str, record_ids: list[str]) -> dict[str, Any]:
        """``+record-delete`` for exactly these ids (high-risk write: only ``feishu_scope_retire`` calls it, after its checks)."""
        if not record_ids or len(set(record_ids)) != len(record_ids) or any(not str(r).startswith("rec") for r in record_ids):
            raise RuntimeSyncError("删除请求的 record_id 为空、重复或格式不对，拒绝")
        args = ["+record-delete", "--base-token", base, "--table-id", table]
        for record_id in record_ids:
            args += ["--record-id", record_id]
        return self._run([*args, "--yes"])

    def create_record(self, base: str, table: str, fields: dict[str, Any]) -> dict[str, Any]:
        """``+record-upsert`` without --record-id creates a record (official help); no business-key upsert."""
        body = self._run(["+record-upsert", "--base-token", base, "--table-id", table, "--json", json.dumps(fields, ensure_ascii=False)])
        rid = _receipt_record_id(body)
        if rid is None:
            raise RuntimeSyncError("UNKNOWN: 创建回执缺少 record_id；效果未知，不按顺序猜配，需按稳定键回读对账")
        return {**body, "data": {**(body.get("data") if isinstance(body.get("data"), dict) else {}), "record": {"record_id": rid}}}


# ---------------------------------------------------------------------------------------------
# Append-only ledger: exact-target intents, results and reconciliation, independent of logDir
# ---------------------------------------------------------------------------------------------


# 唯一正式 P18 账根：正常入口(CLI/函数默认)只认这一个身份。结构合法但另建的“空账”不是它，不能绕过它的历史封锁。
# 变更此常量是源码变更(须经 diff 评审)，不是运行时可传入的参数。
# 2026-11-12：正式账迁到业务根固定运行位置(RUNTIME_ROOT)。原账逐字节迁入，目标键与路径无关，逻辑身份和封锁集合不变；
# 旧根留作历史证据，正常入口拒绝再写它。P18_LEDGER_ROOT 保留为同一正式根的旧名。
RUNTIME_ROOT = "/mnt/d/工作备份/移动硬盘/1、工作/2、产品科技联网/1、产品监督/案卷汇总/运行记录"
LEDGER_ROOT = RUNTIME_ROOT + "/飞书写入账本"
P18_LEDGER_ROOT = LEDGER_ROOT
LEGACY_LEDGER_ROOTS = ("/mnt/d/ws/05-temp/20261104_飞书大队权限/P18运行账本/实施",)
LEDGER_IDENTITY = "账本身份.json"
ISOLATED_LEDGER_MARKER = ".isolated-test-ledger"


def canonical_ledger_path(value: Any) -> str:
    """Windows 盘符路径与 WSL /mnt 路径指向同一 D 根时规范成同一字符串，便于比较身份(不访问文件系统)。"""
    text = str(value).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if match:
        text = f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    return os.path.normpath(text).rstrip("/")


class Ledger:
    def __init__(self, root: Path, *, isolated_test: bool = False):
        self.root = Path(root)
        self.isolated_test = isolated_test   # 仅代码内部(测试)可设；CLI/正常入口从不设置

    def validate_root(self) -> None:
        """正常入口只认唯一正式 P18 账根的身份，而不只是“目录结构合法”。

        - 正式根：规范化路径必须等于 P18_LEDGER_ROOT，目录须存在且已有历史(targets/ 下至少一个事件文件)；只读核验，不写。
        - 隔离测试账：仅当代码内部以 isolated_test=True 构造、根里有 initialize() 写下的标记、且不是正式根时才可用；
          CLI 与默认入口不暴露这条路径，另建的空账(哪怕有 targets/)一律拒绝。
        """
        production = canonical_ledger_path(P18_LEDGER_ROOT)
        is_production = canonical_ledger_path(self.root) == production
        if not self.root.is_dir() or not (self.root / "targets").is_dir():
            raise SyncError(f"账根不存在或不是既有账(缺 targets/)：{self.root}；正常入口不会新建账根")
        if self.isolated_test:
            if is_production or not (self.root / ISOLATED_LEDGER_MARKER).is_file():
                raise SyncError("隔离测试账必须是显式 initialize() 过、带标记且不是正式根的目录")
            return
        if canonical_ledger_path(self.root) in {canonical_ledger_path(item) for item in LEGACY_LEDGER_ROOTS}:
            raise SyncError(f"不是唯一正式 P18 账根：{self.root} 是迁移前的旧账根，只留作历史证据；正式账已在 {P18_LEDGER_ROOT}")
        if not is_production:
            raise SyncError(f"不是唯一正式 P18 账根(应为 {P18_LEDGER_ROOT})：{self.root}；另建的空账会绕过正式账的历史封锁，拒绝")
        if not any((self.root / "targets").glob("*.jsonl")):
            raise SyncError("正式账根下没有任何历史事件，疑似被替换为空账，拒绝")

    def initialize(self) -> None:
        """仅隔离测试：显式创建账根结构并写下隔离标记；正常同步入口从不调用，也不会对正式根调用。"""
        if canonical_ledger_path(self.root) == canonical_ledger_path(P18_LEDGER_ROOT):
            raise SyncError("拒绝对正式 P18 账根执行 initialize")
        (self.root / "targets").mkdir(parents=True, exist_ok=True)
        (self.root / "locks").mkdir(parents=True, exist_ok=True)
        (self.root / ISOLATED_LEDGER_MARKER).write_text("isolated test ledger; not the formal P18 ledger\n", encoding="utf-8")

    def unresolved(self) -> dict[str, str]:
        """Every target that is still BLOCKED, with its reason: the blocking set of this ledger."""
        blocked: dict[str, str] = {}
        for path in sorted((self.root / "targets").glob("*.jsonl")):
            state = self.state(path.stem)
            if state["state"] != "CLEAR":
                blocked[path.stem] = state["reason"]
        return blocked

    @staticmethod
    def target_key(base: str, table: str, record: str) -> str:
        return digest({"base": base, "table": table, "record": record})[:32]

    def _events_path(self, key: str) -> Path:
        return self.root / "targets" / f"{key}.jsonl"

    def events(self, key: str) -> list[dict[str, Any]]:
        path = self._events_path(key)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def state(self, key: str) -> dict[str, Any]:
        """CLEAR, or BLOCKED with the unresolved intent and why."""
        events = self.events(key)
        last_intent = None
        status: dict[str, Any] = {"state": "CLEAR"}
        for event in events:
            kind = event["event"]
            if kind == "intent":
                last_intent = event
                status = {"state": "BLOCKED", "reason": "PENDING", "intentId": event["intentId"], "detail": "意图已落账但无终态(进程中断或结果未记录)"}
            elif last_intent is not None and event.get("intentId") == last_intent["intentId"]:
                if kind == "final":
                    status = {"state": "CLEAR"} if event["status"] in CLEAR_FINALS else {
                        "state": "BLOCKED", "reason": event["status"], "intentId": event["intentId"], "detail": event.get("detail", "")}
                elif kind == "reconciled":
                    status = {"state": "CLEAR"} if event["outcome"] in CLEAR_RECONCILED else {
                        "state": "BLOCKED", "reason": event["outcome"], "intentId": event["intentId"], "detail": event.get("detail", "")}
                elif kind == "human_release":
                    status = {"state": "CLEAR", "releasedByHuman": True}
        status["lastIntent"] = last_intent
        return status

    def append(self, key: str, event: dict[str, Any]) -> None:
        path = self._events_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": now(), **event}, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @contextlib.contextmanager
    def locks(self, keys: list[str], run_id: str):
        """Exclusive per-target locks held for the whole run; a leftover lock is never auto-broken."""
        directory = self.root / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        held: list[Path] = []
        try:
            for key in sorted(set(keys)):
                path = directory / f"{key}.lock"
                try:
                    handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError as error:
                    raise SyncError(f"目标正被另一运行占用或遗留锁未清理(人工核实后才能删除)：{path.name}") from error
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(json.dumps({"runId": run_id, "pid": os.getpid(), "host": socket.gethostname(), "at": now()}))
                held.append(path)
            yield
        finally:
            for path in held:
                with contextlib.suppress(OSError):
                    path.unlink()


def operator() -> dict[str, Any]:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - identity is diagnostic only
        user = "unknown"
    return {"user": user, "host": socket.gethostname(), "pid": os.getpid()}


def _write(log_dir: Path, name: str, value: Any) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _append(log_dir: Path, name: str, value: Any) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / name).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _fresh_log_dir(spec: dict[str, Any]) -> Path:
    path = Path(spec["logDir"])
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise SyncError("logDir 已存在且非空；旧结果不得被覆盖，请使用新目录")
    return path


def _target_keys(spec: dict[str, Any], checked: dict[str, Any]) -> dict[tuple[str, str], str]:
    base = spec["target"]["baseToken"]
    return {(i["tableId"], i["recordId"]): Ledger.target_key(base, i["tableId"], i["recordId"]) for i in checked["plan"]}


def _ledger_blocks(ledger: Ledger, keys: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    blocks = []
    for (table, record), key in keys.items():
        status = ledger.state(key)
        if status["state"] == "BLOCKED":
            blocks.append({"tableId": table, "recordId": record, "reason": status["reason"], "intentId": status["intentId"], "detail": status["detail"]})
    return blocks


def _creation_blocks(ledger: Ledger, spec: dict[str, Any], checked: dict[str, Any]) -> list[dict[str, Any]]:
    """同案稳定键的创建意图若仍未决，晚到的行不能转成“补空”来绕过它(创建键与记录键不同)。"""
    base, blocks = spec["target"]["baseToken"], []
    for item in checked["plan"]:
        status = ledger.state(Ledger.target_key(base, item["tableId"], "create:" + item["stableKey"]["value"]))
        if status["state"] == "BLOCKED":
            blocks.append({"tableId": item["tableId"], "recordId": item["recordId"], "stableKey": item["stableKey"]["value"], "kind": "CREATE_UNKNOWN",
                           "reason": status["reason"], "intentId": status["intentId"], "detail": status["detail"]})
    return blocks


def _release_blocks(ledger: Ledger, spec: dict[str, Any], checked: dict[str, Any], accepted: frozenset[str]) -> list[dict[str, Any]]:
    """历史上有未获用户风险接受的 human_release 的目标：保持其保护，任何新的业务赋值都在写前被拒绝(含手写说明)；
    只有对本助手早先写入的精确逆操作(restore)不受此限。"""
    base, blocks = spec["target"]["baseToken"], []
    for item in checked["plan"]:
        if item["writes"] and all(w.get("restore") is not None for w in item["writes"].values()):
            continue
        keys = (Ledger.target_key(base, item["tableId"], item["recordId"]), Ledger.target_key(base, item["tableId"], "create:" + item["stableKey"]["value"]))
        pending = sorted({str(e.get("intentId")) for key in keys for e in ledger.events(key)
                          if e.get("event") == "human_release" and not e.get("userAcceptance") and e.get("intentId") not in accepted})
        if pending:
            blocks.append({"tableId": item["tableId"], "recordId": item["recordId"], "stableKey": item["stableKey"]["value"], "kind": "HUMAN_RELEASE_UNACCEPTED",
                           "reason": "历史 human_release 未获用户风险接受，保护继续，新的业务赋值被拒绝", "intentIds": pending})
    return blocks


def _restore_blocks(ledger: Ledger, spec: dict[str, Any], checked: dict[str, Any]) -> list[dict[str, Any]]:
    """restore 的因果证据：账本里存在该精确意图(同目标、同稳定键、字段旧值为空且新值等于 expectedOld)，其终态 OK，其后没有别的意图。"""
    base, blocks = spec["target"]["baseToken"], []
    for item in checked["plan"]:
        for fid, write in item["writes"].items():
            if write.get("restore") is None:
                continue
            events = ledger.events(Ledger.target_key(base, item["tableId"], item["recordId"]))
            intent_id = write["restore"]["intentId"]
            positions = [n for n, e in enumerate(events) if e.get("event") == "intent" and e.get("intentId") == intent_id]
            problems: list[str] = []
            if len(positions) != 1:
                problems.append("账本没有该精确意图")
            else:
                intent = events[positions[0]]
                field = (intent.get("fields") or {}).get(fid)
                if intent.get("stableKey") != item["stableKey"] or (intent.get("target") or {}).get("record") != item["recordId"]:
                    problems.append("意图的目标/稳定键与计划不一致")
                if field is None or not cell_is_empty(field.get("old")) or not cell_equals(field["type"], field.get("wire"), wire_value(field["type"], write["expectedOld"])):
                    problems.append("意图的旧值不为空或其新值不等于 expectedOld：不是对该误写的逆操作")
                finals = [e for e in events[positions[0] + 1:] if e.get("event") == "final" and e.get("intentId") == intent_id]
                if len(finals) != 1 or finals[0].get("status") != "OK":
                    problems.append("该意图没有唯一的 OK 终态")
                if any(e.get("event") == "intent" for e in events[positions[0] + 1:]):
                    problems.append("该意图之后账本上还有别的写入意图(值可能被后续写者改过)")
            if problems:
                blocks.append({"tableId": item["tableId"], "recordId": item["recordId"], "field": fid, "kind": "RESTORE_NOT_PROVEN", "reasons": problems})
    return blocks


def _all_blocks(ledger: Ledger, spec: dict[str, Any], checked: dict[str, Any], accepted: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    return [*_ledger_blocks(ledger, _target_keys(spec, checked)), *_creation_blocks(ledger, spec, checked),
            *_release_blocks(ledger, spec, checked, accepted), *_restore_blocks(ledger, spec, checked)]



def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_ledger(source: Path, dest: Path | None = None, *, apply: bool = False) -> dict[str, Any]:
    """Move the formal ledger to its fixed runtime root byte for byte; nothing is rewritten, unlocked or emptied.

    Each target file is copied unchanged and its digest compared. A file already at the destination must
    equal the source file or extend it (events appended after the move); anything else is a conflict and
    stops the migration. A leftover lock or an active writer stops it too. Re-running is a verification.
    """
    source, dest = Path(source), Path(dest if dest is not None else LEDGER_ROOT)
    _require(canonical_ledger_path(source) in {canonical_ledger_path(item) for item in LEGACY_LEDGER_ROOTS},
             f"只迁移登记过的旧账根：{source}")
    _require(canonical_ledger_path(dest) == canonical_ledger_path(LEDGER_ROOT) or (dest / ISOLATED_LEDGER_MARKER).is_file(),
             f"目标必须是正式账根 {LEDGER_ROOT}")
    files = sorted((source / "targets").glob("*.jsonl"))
    _require(bool(files), f"旧账根没有历史事件：{source}")
    for root in (source, dest):
        leftover = sorted(p.name for p in (root / "locks").glob("*")) if (root / "locks").is_dir() else []
        _require(not leftover, f"账根仍有锁({len(leftover)} 个，如 {leftover[:3]})：有活动写入或遗留锁，先串行对账，不清锁")
    report: dict[str, Any] = {"source": str(source), "dest": str(dest), "apply": apply, "targets": len(files),
                              "copied": 0, "identical": 0, "extended": 0, "conflicts": [], "extraAtDest": []}
    digest_lines: list[str] = []
    for path in files:
        data = path.read_bytes()
        digest_lines.append(f"{hashlib.sha256(data).hexdigest()}  {path.name}")
        target = dest / "targets" / path.name
        if target.is_file():
            current = target.read_bytes()
            if current == data:
                report["identical"] += 1
            elif current.startswith(data):
                report["extended"] += 1
            else:
                report["conflicts"].append(path.name)
        elif apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".migrating")
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _require(_file_sha(temporary) == hashlib.sha256(data).hexdigest(), f"迁移副本摘要不符：{path.name}")
            os.replace(temporary, target)
            report["copied"] += 1
    names = {p.name for p in files}
    if (dest / "targets").is_dir():
        report["extraAtDest"] = sorted(p.name for p in (dest / "targets").glob("*.jsonl") if p.name not in names)
    report["sourceManifestSha256"] = hashlib.sha256(("\n".join(digest_lines) + "\n").encode()).hexdigest()
    _require(not report["conflicts"], f"目标账根已有与旧账不一致的文件 {report['conflicts'][:5]}：停止，不覆盖")
    if apply:
        (dest / "locks").mkdir(parents=True, exist_ok=True)
        identity = dest / LEDGER_IDENTITY
        if not identity.is_file():
            identity.write_text(json.dumps({
                "schemaVersion": "LedgerIdentityV1", "ledger": "P18 飞书写入账本", "migratedAt": now(),
                "predecessor": canonical_ledger_path(source), "targets": len(files),
                "sourceManifestSha256": report["sourceManifestSha256"],
                "note": "逐字节迁入；目标键=digest(base,table,record)，与路径无关；旧根留作历史证据不再写入",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        missing = [p.name for p in files if not (dest / "targets" / p.name).is_file()]
        _require(not missing, f"迁移后目标缺文件：{missing[:5]}")
        report["blockedSource"] = Ledger(source).unresolved()
        report["blockedDest"] = Ledger(dest).unresolved()
        if not report["extended"] and not report["extraAtDest"]:
            _require(report["blockedSource"] == report["blockedDest"], "迁移后封锁集合与旧账不一致")
    report["status"] = "MIGRATED" if apply else ("VERIFIED" if report["identical"] + report["extended"] == len(files) else "PLAN")
    return report


# ---------------------------------------------------------------------------------------------
# execute / reconcile
# ---------------------------------------------------------------------------------------------


def execute(spec: dict[str, Any], checked: dict[str, Any], transport: Transport, ledger: Ledger, accepted: frozenset[str] = frozenset()) -> dict[str, Any]:
    index, base = checked["index"], spec["target"]["baseToken"]
    keys = _target_keys(spec, checked)
    blocks = _all_blocks(ledger, spec, checked, accepted)
    if blocks:
        raise SyncError("目标存在未对账的早先写入或同案稳定键的未决创建，已拒绝(未发起任何外部调用)；先 reconcile 或经用户接受后人工放行：" + json.dumps(blocks, ensure_ascii=False))
    log_dir = _fresh_log_dir(spec)
    run_id = uuid.uuid4().hex
    with ledger.locks(list(keys.values()), run_id):
        blocks = _all_blocks(ledger, spec, checked, accepted)  # re-check inside the lock: a concurrent run may have just finished
        if blocks:
            raise SyncError("目标在加锁后出现未对账记录，已拒绝：" + json.dumps(blocks, ensure_ascii=False))
        return _execute_locked(spec, checked, transport, ledger, keys, log_dir, run_id, index, base)


def _execute_locked(spec, checked, transport, ledger, keys, log_dir, run_id, index, base) -> dict[str, Any]:
    plan = checked["plan"]
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "mode": "execute", "runId": run_id, "operator": operator(), "startedAt": now(),
        "baseToken": base, "profile": spec["target"]["profile"], "ledgerDir": str(ledger.root),
        "contractSha256": spec["contract"]["sha256"], "records": [], "status": "RUNNING",
    }

    def refuse(reason: str) -> None:
        report["status"] = "REFUSED_BEFORE_WRITE"
        report["reason"] = reason
        _write(log_dir, "report.json", report)
        raise RuntimeSyncError(reason)

    tables: dict[str, list[str]] = {}
    for item in plan:
        tables.setdefault(item["tableId"], []).append(item["recordId"])
    before: dict[str, dict[str, dict[str, Any]]] = {}
    for table_id, ids in tables.items():
        try:
            before[table_id] = transport.get_records(base, table_id, ids)
        except RuntimeSyncError as error:
            refuse(f"写前回读失败，未写入：{error}")
    _write(log_dir, "before.json", before)
    for item in plan:  # stable key and expectedOld checks, still before any write
        row = before[item["tableId"]][item["recordId"]]
        key_field = item["stableKey"]["fieldId"]
        absent = [fid for fid in [key_field, *item["writes"]] if fid not in row]
        if absent:
            refuse(f"写前回读缺少稳定键或目标字段，旧值不可比，未写入：{item['recordId']}/{absent}")
        if not cell_equals(index[item["tableId"]][key_field]["type"], row.get(key_field), item["stableKey"]["value"]):
            refuse(f"稳定键与云端记录不符：{item['recordId']}")
        for fid, write in item["writes"].items():
            expected = write["expectedOld"]
            if expected is None:  # 显式 null：云端此刻必须为空(只补空值，不覆盖任何既有值)
                if not cell_is_empty(row.get(fid)):
                    refuse(f"旧值应为空但云端已有值（同期变化，保留双方）：{item['recordId']}/{fid}")
            elif expected != "__absent__" and not cell_equals(write["type"], row.get(fid), wire_value(write["type"], expected)):
                refuse(f"旧值与 expectedOld 不符（同期变化，保留双方）：{item['recordId']}/{fid}")
        item["changes"] = {fid: w["wire"] for fid, w in item["writes"].items() if not cell_equals(w["type"], row.get(fid), w["wire"])}
    for item in plan:
        row = before[item["tableId"]][item["recordId"]]
        report["records"].append({
            "tableId": item["tableId"], "recordId": item["recordId"], "stableKey": item["stableKey"],
            "fieldDetail": {fid: {"name": w["name"], "type": w["type"], "old": row.get(fid), "new": w["new"], "wire": w["wire"], "basis": w["basis"]} for fid, w in item["writes"].items()},
            "skippedFields": {fid: {"name": s["name"], "valueState": s["valueState"], "basis": s["basis"]} for fid, s in item["skipped"].items()},
            "plannedFields": sorted(item["changes"]), "writtenFields": [],
            "status": "NOT_ATTEMPTED" if item["changes"] else "UNCHANGED",
        })
    intents: dict[tuple[str, str], str] = {}
    for item in plan:  # the intent is on the ledger before the first external write
        if not item["changes"]:
            continue
        intent_id = digest({"base": base, "table": item["tableId"], "record": item["recordId"], "stableKey": item["stableKey"], "wire": item["changes"]})
        intents[(item["tableId"], item["recordId"])] = intent_id
        ledger.append(keys[(item["tableId"], item["recordId"])], {
            "event": "intent", "intentId": intent_id, "runId": run_id, "operator": operator(), "logDir": str(log_dir),
            "target": {"base": base, "table": item["tableId"], "record": item["recordId"]}, "stableKey": item["stableKey"],
            "fields": {fid: {"name": item["writes"][fid]["name"], "type": item["writes"][fid]["type"], "old": before[item["tableId"]][item["recordId"]].get(fid), "wire": wire,
                             "basis": item["writes"][fid]["basis"].get("kind")}
                       for fid, wire in item["changes"].items()},
        })
    failed = False
    for item, entry in zip(plan, report["records"], strict=True):
        if not item["changes"]:
            continue
        _append(log_dir, "requests.jsonl", {"at": now(), "op": "upsert", "tableId": item["tableId"], "recordId": item["recordId"], "fields": item["changes"]})
        try:
            response = transport.upsert(base, item["tableId"], item["recordId"], item["changes"])
            data = response.get("data")
            _append(log_dir, "responses.jsonl", {"at": now(), "recordId": item["recordId"], "ok": bool(response.get("ok", True)), "ignored_fields": data.get("ignored_fields") if isinstance(data, dict) else None})
            entry["status"] = "WRITTEN"
            entry["writtenFields"] = sorted(item["changes"])
        except RuntimeSyncError as error:
            entry["status"] = "UNKNOWN" if str(error).startswith("UNKNOWN") else "FAILED"
            entry["error"] = str(error)
            failed = True
            break  # never continue or retry after a failed/unknown request
    after: dict[str, dict[str, dict[str, Any]]] = {}
    readback_error = None
    try:
        for table_id, ids in tables.items():
            after[table_id] = transport.get_records(base, table_id, ids)
    except RuntimeSyncError as error:
        readback_error = str(error)
    violations: list[str] = []
    if readback_error is None:
        _write(log_dir, "after.json", after)
        violations = _verify(index, plan, report["records"], before, after)
    report["protectionViolations"] = violations
    report["finishedAt"] = now()
    if readback_error is not None:
        report["status"] = "UNKNOWN"
        report["reason"] = f"写后回读失败，效果未核实：{readback_error}"
    else:
        report["status"] = "FAILED" if failed or violations else "OK"
    _write(log_dir, "report.json", report)
    for item, entry in zip(plan, report["records"], strict=True):
        intent_id = intents.get((item["tableId"], item["recordId"]))
        if intent_id is None:
            continue
        if entry["status"] == "NOT_ATTEMPTED":
            final = "NOT_ATTEMPTED"
        elif readback_error is not None and entry["status"] == "WRITTEN":
            final = "READBACK_FAILED"
        elif entry["status"] in {"FAILED", "UNKNOWN"}:
            final = entry["status"]
        elif any(v.startswith(item["recordId"]) for v in violations):
            final = "VIOLATION"
        else:
            final = "OK"
        ledger.append(keys[(item["tableId"], item["recordId"])], {"event": "final", "intentId": intent_id, "runId": run_id, "status": final, "detail": entry.get("error", "")})
    if report["status"] != "OK":
        raise RuntimeSyncError(report.get("reason") or "; ".join(violations) or "写入失败/结果未知，已保留回读证据，未重试，目标已在账本中封锁")
    return report


def _verify(index, plan, records, before, after) -> list[str]:
    violations: list[str] = []
    for item, entry in zip(plan, records, strict=True):
        old_row, new_row = before[item["tableId"]][item["recordId"]], after[item["tableId"]][item["recordId"]]
        meta_by_id = index[item["tableId"]]
        if entry["status"] in {"WRITTEN", "UNCHANGED"}:
            for fid, write in item["writes"].items():  # every requested field needs an explicit, comparable readback
                if fid not in new_row:
                    violations.append(f"{item['recordId']}/{write['name']}：回读缺少该目标字段，无法核对")
                elif not cell_equals(write["type"], new_row[fid], write["wire"]):
                    violations.append(f"{item['recordId']}/{write['name']}：回读值不等于目标值")
        for fid in sorted(set(old_row) | set(new_row)):
            if fid in item["writes"]:
                continue
            meta = meta_by_id.get(fid)
            derived = meta is not None and (meta["class"] in DERIVED_CLASSES or meta["type"] in DERIVED_TYPES)
            if meta is not None and meta["type"] == "checkbox" and old_row.get(fid) in (None, False) and new_row.get(fid) in (None, False):
                continue  # 未勾选复选框：服务端首次写入某行时会把空读数规范成 false，二者同为“未勾选”，不是人工值变化
            if not derived and canon(old_row.get(fid)) != canon(new_row.get(fid)):
                violations.append(f"{item['recordId']}/{meta['name'] if meta else fid}：非目标字段发生变化（保护域/未知字段）")
    return violations


def reconcile(spec: dict[str, Any], checked: dict[str, Any], transport: Transport, ledger: Ledger) -> dict[str, Any]:
    """Read-only against Feishu; appends reconciliation results to the local ledger only.

    Identity first: the stable key stored in the intent must equal both the key in this spec and the
    key read back now, otherwise the record is a different business object and the block stays.
    """
    base, index = spec["target"]["baseToken"], checked["index"]
    keys = _target_keys(spec, checked)
    log_dir = _fresh_log_dir(spec)
    run_id = uuid.uuid4().hex
    release = spec.get("humanRelease")
    for item in checked["plan"]:  # spec/intent identity conflicts are refused before any ledger write
        intent = ledger.state(keys[(item["tableId"], item["recordId"])]).get("lastIntent")
        if intent is not None and intent["stableKey"] != item["stableKey"]:
            raise SyncError(f"对账 spec 的稳定键与账本意图不一致，已拒绝：{item['recordId']}")
    report: dict[str, Any] = {"schema": REPORT_SCHEMA, "mode": "reconcile", "runId": run_id, "operator": operator(), "startedAt": now(),
                              "ledgerDir": str(ledger.root), "externalWrites": 0, "targets": []}
    with ledger.locks(list(keys.values()), run_id):
        for item in checked["plan"]:
            key = keys[(item["tableId"], item["recordId"])]
            status = ledger.state(key)
            entry: dict[str, Any] = {"tableId": item["tableId"], "recordId": item["recordId"], "ledgerState": status["state"]}
            report["targets"].append(entry)
            if status["state"] == "CLEAR":
                entry["outcome"] = "NOTHING_TO_RECONCILE"
                continue
            intent = status["lastIntent"]
            entry["intentId"] = intent["intentId"]
            if release is not None and release["intentId"] != intent["intentId"]:
                entry["outcome"] = "RELEASE_NOT_FOR_THIS_INTENT"   # 放行只对其精确 intentId 有效；其余目标保持封锁，不批量释放
                continue
            if release is not None:
                ledger.append(key, {"event": "human_release", "intentId": intent["intentId"], "runId": run_id, "reason": release["reason"],
                                    "operator": release["operator"], "evidence": release["evidence"], "acknowledgesLateWriteRisk": True,
                                    "priorState": status["reason"], "originalIntent": intent})
                entry["outcome"] = "HUMAN_RELEASED"
                continue
            recovery = spec.get("technicalRecovery")
            if recovery is not None:
                if recovery["intentId"] != intent["intentId"]:
                    entry["outcome"] = "RECOVERY_NOT_FOR_THIS_INTENT"   # 恢复只对其精确 intentId 有效，不批量
                    continue
                outcome, observed, recovery_facts = _technical_recovery(index, item, intent, ledger.events(key), status, transport, base, recovery["minQuietSeconds"])
                ledger.append(key, {"event": "reconciled", "intentId": intent["intentId"], "runId": run_id, "outcome": outcome, "originalIntent": intent,
                                    "observed": observed, "recovery": recovery_facts,
                                    "detail": "严格技术恢复：原运行已结束、服务端历史在意图之后无该字段写入、读回等于旧值且静默期已过" if outcome == "NOT_APPLIED_PROVEN"
                                    else "技术恢复证据不足，保持封锁：" + "；".join(recovery_facts.get("reasons", []))})
                entry["outcome"], entry["observed"], entry["recovery"] = outcome, observed, recovery_facts
                continue
            outcome, observed = _classify(index, item, intent, transport, base)
            ledger.append(key, {"event": "reconciled", "intentId": intent["intentId"], "runId": run_id, "outcome": outcome,
                                "originalIntent": intent, "observed": observed,
                                "detail": "旧值仅是观察，不能证明请求未执行或不会迟到" if outcome == "OLD_VALUE_OBSERVED" else ""})
            entry["outcome"] = outcome
            entry["observed"] = observed
    report["finishedAt"] = now()
    _write(log_dir, "reconcile-report.json", report)
    return report


def _now_epoch() -> float:
    return datetime.now(UTC).timestamp()


def _iso_epoch(text: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _technical_recovery(index, item, intent, events, status, transport, base, min_quiet, now_epoch: float | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Strict, ledger-recorded recovery of an UNKNOWN update intent. It never invents a human release and never edits the old events.

    All of the following must hold, otherwise the target stays BLOCKED (RECOVERY_REFUSED):
    the intent has exactly one ``final`` UNKNOWN event (the run ended and recorded it; PENDING/no final is not eligible);
    the live row has the same stable key and every intent field still equals its old value (identity-checked read, ``OLD_VALUE_OBSERVED``);
    the service's own revision history has no revision at/after the intent time that touches an intent field (or touches unknown fields);
    at least ``min_quiet`` seconds passed since the final event.
    """
    reasons: list[str] = []
    facts: dict[str, Any] = {"kind": "history-quiet-readback", "minQuietSeconds": min_quiet, "reasons": reasons}
    if status.get("reason") not in {"UNKNOWN", "OLD_VALUE_OBSERVED", "RECOVERY_REFUSED"}:
        reasons.append(f"账本状态 {status.get('reason')} 不可技术恢复(只限 UNKNOWN 终态的更新意图)")
    finals = [e for e in events if e.get("event") == "final" and e.get("intentId") == intent["intentId"]]
    if len(finals) != 1 or finals[0].get("status") != "UNKNOWN":
        reasons.append("原意图须恰有一条 UNKNOWN 终态事件(进程已结束并记账)")
    final_epoch = _iso_epoch(finals[0].get("at")) if len(finals) == 1 else None
    intent_epoch = _iso_epoch(intent.get("at"))
    if final_epoch is None or intent_epoch is None:
        reasons.append("意图/终态时间无法解析")
    current = now_epoch if now_epoch is not None else _now_epoch()
    if final_epoch is not None:
        facts["finalAt"], facts["quietSeconds"] = finals[0].get("at"), int(current - final_epoch)
        if current - final_epoch < min_quiet:
            reasons.append(f"静默期不足 {min_quiet} 秒")
    if reasons:
        return "RECOVERY_REFUSED", {}, facts
    outcome, observed = _classify(index, item, intent, transport, base)
    facts["readbackOutcome"] = outcome
    if outcome != "OLD_VALUE_OBSERVED":
        reasons.append(f"读回结果为 {outcome}，不是“身份相同且全部字段仍为旧值”")
        return "RECOVERY_REFUSED", observed, facts
    try:
        history = transport.get_record_history(base, item["tableId"], item["recordId"])
    except (RuntimeSyncError, AttributeError) as error:
        reasons.append(f"服务端历史不可读：{error}")
        return "RECOVERY_REFUSED", observed, facts
    if not history:
        reasons.append("服务端历史为空，不能作为证据")
        return "RECOVERY_REFUSED", observed, facts
    touched = set(intent["fields"])
    after = [h for h in history if h["createTime"] >= intent_epoch - HISTORY_CREATE_SLACK_BEFORE]
    bad = [h for h in after if not h["fieldIds"] or touched & set(h["fieldIds"])]
    facts.update(historyRevisions=len(history), latestRev={"rev": history[0]["rev"], "createTime": history[0]["createTime"]},
                 revisionsAtOrAfterIntent=[{"rev": h["rev"], "createTime": h["createTime"], "fieldIds": h["fieldIds"]} for h in after])
    if bad:
        reasons.append("服务端历史在意图之后出现涉及该字段(或字段不明)的修订，不能证明未生效")
        return "RECOVERY_REFUSED", observed, facts
    return "NOT_APPLIED_PROVEN", observed, facts


def _classify(index, item, intent, transport, base) -> tuple[str, dict[str, Any]]:
    key_field, key_value = intent["stableKey"]["fieldId"], intent["stableKey"]["value"]
    try:
        row = transport.get_records(base, item["tableId"], [item["recordId"]])[item["recordId"]]
    except RuntimeSyncError as error:
        return "UNREADABLE", {"error": str(error)}
    observed: dict[str, Any] = {"stableKey": row.get(key_field, "__absent__"), "fields": {fid: row.get(fid, "__absent__") for fid in intent["fields"]}}
    if key_field not in row:
        return "IDENTITY_UNREADABLE", observed
    if not cell_equals(index[item["tableId"]][key_field]["type"], row[key_field], key_value):
        return "IDENTITY_MISMATCH", observed
    fields = intent["fields"]
    if any(fid not in row for fid in fields):
        return "UNREADABLE", observed
    if all(cell_equals(f["type"], row[fid], f["wire"]) for fid, f in fields.items()):
        return "APPLIED", observed
    if all(cell_equals(f["type"], row[fid], f["old"]) if f["old"] is not None else norm_or_none(f["type"], row[fid]) is None for fid, f in fields.items()):
        return "OLD_VALUE_OBSERVED", observed
    return "DIVERGED", observed


def norm_or_none(field_type: str, cell: Any) -> Any:
    try:
        return norm_cell(field_type, cell)
    except Ambiguous:
        return "__ambiguous__"


# ---------------------------------------------------------------------------------------------
# V3 create: create-only branch, same ledger directory, fail-closed identity/route/parent checks
# ---------------------------------------------------------------------------------------------

SPEC_SCHEMA_V3 = "FeishuSourceSyncSpecV3"
REPORT_SCHEMA_V3 = "FeishuSourceSyncReportV3"
CREATE_CLASSES = {"create-once-identity", "create-once-source-scalar", "create-once-routing", "create-once-parent-link"}
# The latest base contract decides first: an extension candidate may only open a create class on a base field of the matching
# class, and never a field whose base syncWrite is DENIED (e.g. the unconfirmed 检查结果 fields).
CREATE_BASE_CLASS = {
    "create-once-identity": {"source-sync-whitelist"},
    "create-once-source-scalar": {"source-sync-whitelist"},
    "create-once-routing": {"admin-only"},
    "create-once-parent-link": {"structural-relation"},
}
TABLE_RANK = {"案卷主表": 0, "检查明细": 1, "产品明细": 2}
PROJECT_NO = re.compile(r"^\d{8}[A-Z]\d{9}$")
TEAM_TABLE_NAME = "大队底册"


def load_contract_v3(path: Path, expected_sha: str) -> dict[str, Any]:
    """The pinned V3 composite: the complete v9 contract (``baseContract``) plus the create-once extension."""
    if not path.is_file():
        raise SyncError("V3 合同文件不存在")
    if file_sha(path) != expected_sha:
        raise SyncError("V3 合同摘要与 spec 固定值不一致；合同变化须另版冻结")
    value = json.loads(path.read_text(encoding="utf-8"))
    base, extension = value.get("baseContract"), value.get("extension")
    if not isinstance(base, dict) or not isinstance(base.get("tables"), dict) or not base.get("baseId"):
        raise SyncError("V3 合同缺少完整 v9 baseContract")
    if not isinstance(extension, dict) or not isinstance(extension.get("createFieldsCandidate"), list):
        raise SyncError("V3 合同缺少 create-once 扩展")
    return {"base": base, "extension": extension, "sha256": expected_sha}


def _link_wire(value: Any) -> list[dict[str, str]]:
    _require(isinstance(value, str) and RECORD_ID.match(value) is not None, "link 字段的新值必须是 recordId 字符串")
    return [{"id": value}]


def validate_create_spec(spec: Any, v3: dict[str, Any]) -> dict[str, Any]:
    """Structural violations refuse the whole spec; entity-level gaps only block that entity."""
    _require(isinstance(spec, dict) and spec.get("schemaVersion") == SPEC_SCHEMA_V3, "spec 格式无效(需要 FeishuSourceSyncSpecV3)")
    _require(spec.get("mode") in {"plan", "execute", "reconcile"}, "mode 必须是 plan / execute / reconcile")
    contract, extension = v3["base"], v3["extension"]
    target = spec.get("target")
    _require(isinstance(target, dict) and target.get("baseToken") == contract["baseId"], "目标 Base 与保护合同不一致")
    _require(isinstance(target.get("profile"), str) and target["profile"] and target.get("identity") in {"user", "bot"}, "必须显式给出 profile 与 identity")
    index = field_index(contract)
    candidates: dict[str, dict[str, Any]] = {f["fieldId"]: f for f in extension["createFieldsCandidate"]}
    # 冻结合同把两个 admin-only 路由字段同时列入 createPayload DENY 与 create-once-routing 候选；
    # 父定稿明确放行“所属大队+归属大队”的精确队路由首写，故仅 create-once-routing 候选可覆盖通用 DENY 清单；
    # 人工/永久禁写字段（ALWAYS_DENIED）永不被覆盖。冲突字段记入 plan/report 的 contractConflicts。
    routing_ids = {fid for fid, f in candidates.items() if f.get("proposedCreateClass") == "create-once-routing"}
    explicit_deny = {f["fieldId"] for f in extension.get("explicitCreatePayloadDeny", [])}
    contract_conflicts = sorted(explicit_deny & routing_ids)
    denied = ((explicit_deny - routing_ids) | set(ALWAYS_DENIED))
    release = spec.get("humanRelease")
    if release is not None:
        _require(spec["mode"] == "reconcile" and isinstance(release, dict), "humanRelease 仅限 reconcile 且为对象")
        _require(all(isinstance(release.get(k), str) and release[k].strip() for k in ("reason", "operator", "intentId")), "humanRelease 需要 reason、operator 与精确 intentId(只放行该意图)")
        _require(release.get("acknowledgesLateWriteRisk") is True and isinstance(release.get("evidence"), list) and release["evidence"], "humanRelease 需要风险确认与 evidence")
    recovery = spec.get("technicalRecovery")
    if recovery is not None:
        _require(spec["mode"] == "reconcile" and isinstance(recovery, dict), "technicalRecovery 仅限 reconcile 且为对象")
        _require(release is None, "technicalRecovery 与 humanRelease 不能同时使用")
        _require(recovery.get("kind") == "create-quiet-zero-match", "创建对账的 technicalRecovery.kind 必须是 create-quiet-zero-match")
        _require(isinstance(recovery.get("intentId"), str) and recovery["intentId"].strip(), "technicalRecovery 需要精确 intentId(只恢复该意图)")
        _require(isinstance(recovery.get("minQuietSeconds"), int) and not isinstance(recovery["minQuietSeconds"], bool)
                 and recovery["minQuietSeconds"] >= RECOVERY_MIN_QUIET_SECONDS, f"minQuietSeconds 不得小于 {RECOVERY_MIN_QUIET_SECONDS}")
    creates = spec.get("creates")
    _require(isinstance(creates, list) and creates, "creates 为空；不回落到任何全量数据")
    limits = spec.get("limits") or {}
    cap = limits.get("maxRecords")
    _require(isinstance(cap, int) and not isinstance(cap, bool) and 1 <= cap <= HARD_CAP_RECORDS, f"limits.maxRecords 必须是 1..{HARD_CAP_RECORDS}")
    _require(len(creates) <= cap, "创建条数超过 maxRecords")
    routes = spec.get("routes") or []
    _require(isinstance(routes, list), "routes 格式无效")
    route_by_source: dict[str, dict[str, Any]] = {}
    for route in routes:
        if isinstance(route, dict) and route.get("adminOnly") is True:
            # 不在八队名录的来源管辖(水上大队)：不写所属大队/归属大队(留空，仅管理员可见)，只以“大队来源”选项如实标明；与既有水上案同一做法。
            _require(all(isinstance(route.get(k), str) and route[k] for k in ("sourceJurisdictionExact", "sourceOptionExact")) and not any(k in route for k in ("teamOptionExact", "teamRecordId")),
                     "adminOnly route 只需要精确原文管辖与大队来源选项，且不得带队选项/底册记录")
        else:
            _require(isinstance(route, dict) and all(isinstance(route.get(k), str) and route[k] for k in ("sourceJurisdictionExact", "teamOptionExact", "teamTableId", "teamRecordId")), "route 需要精确原文管辖/队选项/底册表/底册记录")
            _require(TABLE_ID.match(route["teamTableId"]) is not None and RECORD_ID.match(route["teamRecordId"]) is not None, "route 的底册表/记录无效")
            _require(len({r["teamTableId"] for r in routes if r.get("adminOnly") is not True}) == 1, "所有 route 必须指向同一大队底册表")
        _require(route["sourceJurisdictionExact"] not in route_by_source, "route 的管辖原文重复")
        route_by_source[route["sourceJurisdictionExact"]] = route
    synthetic = spec.get("syntheticOnly") is True
    pattern = spec.get("syntheticKeyPattern")
    _require(not synthetic or (isinstance(pattern, str) and pattern), "syntheticOnly 需要 syntheticKeyPattern")
    if spec["mode"] != "plan":
        _require(isinstance(spec.get("logDir"), str) and spec["logDir"], f"{spec['mode']} 需要显式 logDir")
    plan: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for create in creates:
        _require(isinstance(create, dict), "create 项无效")
        table_id = create.get("tableId")
        _require(isinstance(table_id, str) and table_id in index, f"create 表不在保护合同中：{table_id}")
        table_name = _table_name(index, table_id)
        _require(table_name in TABLE_RANK, f"本版只创建案卷/检查/产品源行：{table_name}")
        stable_field = STABLE_KEYS[table_name]
        key = create.get("stableKey")
        _require(isinstance(key, dict) and key.get("fieldId") == stable_field and isinstance(key.get("value"), str) and key["value"], f"必须给出稳定键 {stable_field} 及其值")
        _require((table_id, key["value"]) not in seen, "同一稳定键重复创建")
        seen.add((table_id, key["value"]))
        if table_name == "案卷主表":
            _require(PROJECT_NO.fullmatch(key["value"]) is not None or (synthetic and re.search(pattern, key["value"]) is not None), "案卷稳定键必须是项目编号")
        _require(not synthetic or re.search(pattern, key["value"]) is not None, "syntheticOnly：稳定键不符合合成测试模式")
        fields = create.get("fields")
        _require(isinstance(fields, dict) and fields, "create.fields 为空")
        blocked: list[str] = []
        writes: dict[str, dict[str, Any]] = {}
        skipped: dict[str, dict[str, Any]] = {}
        for field_id, item in fields.items():
            _require(FIELD_ID.match(field_id) is not None, f"create 必须用 fieldId：{field_id}")
            _require(field_id not in denied, f"人工/派生/关系/禁写字段不得出现在创建载荷：{field_id}")
            meta, cand = index[table_id].get(field_id), candidates.get(field_id)
            _require(meta is not None and cand is not None and cand.get("tableId") == table_id, f"字段不在 V3 创建候选中(默认拒绝)：{field_id}")
            _require(cand.get("proposedCreateClass") in CREATE_CLASSES, f"字段不是 create-once 类别：{field_id}")
            _require(
                meta["class"] in CREATE_BASE_CLASS[cand["proposedCreateClass"]],
                f"最新保护合同不允许该字段创建(基础合同类别 {meta['class']} 不开放 {cand['proposedCreateClass']}；扩展候选不得重开)：{meta['name']}",
            )
            _require(
                not str(meta.get("syncWrite") or "").upper().startswith("DENIED"),
                f"最新保护合同对该字段的 syncWrite 为 DENIED，扩展候选不得重开：{meta['name']}",
            )
            _require(not str(cand.get("candidateState", "")).startswith("blocked"), f"合同未放行该字段创建(候选仍 blocked)：{meta['name']}")
            _require(isinstance(item, dict), "每个字段需 {new, basis}")
            basis = item.get("basis")
            _require(
                isinstance(basis, dict) and basis.get("kind") == "source-observed" and basis.get("valueState") in VALUE_STATES
                and isinstance(basis.get("sourceRef"), str) and basis["sourceRef"] and isinstance(basis.get("observedAt"), str) and basis["observedAt"],
                "每个字段需 basis{kind:source-observed, valueState, sourceRef, observedAt}",
            )
            if basis["valueState"] != "observed":
                _require("new" not in item, "valueState 为 not-provided/unknown 时不得携带 new")
                skipped[field_id] = {"name": meta["name"], "valueState": basis["valueState"], "basis": basis}
                continue
            _require("new" in item, "valueState=observed 需要 new")
            value = item["new"]
            if isinstance(value, (str, int, float, bool)) and value in NEGATIVE_LITERALS:
                _require(basis.get("explicitNegative") is True, "“否/0”只能来自来源明确否定")
            wire = _link_wire(value) if meta["type"] == "link" else wire_value(meta["type"], value)
            if meta["type"] == "select" and meta.get("options") is not None and value not in meta["options"]:
                blocked.append(f"选项不在最新合同中：{meta['name']}={value}")
            writes[field_id] = {"name": meta["name"], "type": meta["type"], "new": value, "wire": wire, "basis": basis, "class": cand["proposedCreateClass"]}
        _require(stable_field in writes and writes[stable_field]["new"] == key["value"], "稳定键字段必须作为 create-once-identity 写入且等于 stableKey.value")
        select_id, link_id, source_id = "fld2Kdm4i2", "fldlw1KAaR", "fld73J0dOH"
        route_for_case = None
        if table_name == "案卷主表":
            source_name = create.get("routingSource")
            route_for_case = route_by_source.get(source_name) if isinstance(source_name, str) else None
        admin_only = bool(route_for_case and route_for_case.get("adminOnly") is True)
        for field_id, cand in candidates.items():  # required fields missing => only this entity is blocked
            if admin_only and field_id in {select_id, link_id}:
                continue
            if cand.get("tableId") == table_id and str(cand.get("emptySemantics", "")).startswith("required") and field_id not in writes:
                blocked.append(f"缺必需字段 {cand['name']}")
        route_info = None
        if table_name == "案卷主表":
            route = route_for_case
            if route is None:
                blocked.append("路由未匹配：消防管辖原文无冻结映射(不按单位名/项目号前缀猜)")
            elif admin_only:
                _require(select_id not in writes and link_id not in writes, "adminOnly 路由的案卷不得写所属大队/归属大队")
                _require(source_id not in writes or writes[source_id]["new"] == route["sourceOptionExact"], "adminOnly 路由的案卷若写大队来源，必须是冻结的大队来源选项")
                route_info = route
            else:
                _require(select_id in writes and link_id in writes, "案卷创建必须同时给出所属大队与归属大队")
                _require(writes[select_id]["new"] == route["teamOptionExact"] and writes[link_id]["new"] == route["teamRecordId"], "所属大队/归属大队与冻结路由不一致(必须同队)")
                route_info = route
        parent = create.get("parent")
        if table_name != "案卷主表":
            _require(isinstance(parent, dict), "检查/产品创建必须给出 parent")
            _require(isinstance(parent.get("recordId"), str) or isinstance(parent.get("createRef"), str), "parent 需要 recordId 或 createRef")
        plan.append({"tableId": table_id, "tableName": table_name, "stableKey": key, "writes": writes, "skipped": skipped,
                     "blocked": blocked, "route": route_info, "parent": parent, "rank": TABLE_RANK[table_name]})
    plan.sort(key=lambda item: item["rank"])  # case -> check -> product, stable within a rank
    return {"index": index, "plan": plan, "routes": route_by_source, "contractConflicts": contract_conflicts, "contractSha256": v3["sha256"]}


def _create_key(spec: dict[str, Any], item: dict[str, Any]) -> str:
    return Ledger.target_key(spec["target"]["baseToken"], item["tableId"], "create:" + item["stableKey"]["value"])


def _create_blocks(spec: dict[str, Any], checked: dict[str, Any], ledger: Ledger) -> list[dict[str, Any]]:
    blocks = []
    for item in checked["plan"]:
        status = ledger.state(_create_key(spec, item))
        if status["state"] == "BLOCKED":
            blocks.append({"tableId": item["tableId"], "stableKey": item["stableKey"]["value"], "reason": status["reason"], "intentId": status["intentId"], "detail": status["detail"]})
    return blocks


def plan_creates(spec: dict[str, Any], checked: dict[str, Any], ledger: Ledger) -> dict[str, Any]:
    blocks = _create_blocks(spec, checked, ledger)
    return {
        "schema": REPORT_SCHEMA_V3, "mode": "plan", "externalCalls": 0,
        "creates": [{"tableId": i["tableId"], "stableKey": i["stableKey"], "wire": {f: w["wire"] for f, w in i["writes"].items()},
                     "skippedFields": {f: s["valueState"] for f, s in i["skipped"].items()}, "blocked": i["blocked"],
                     "parent": i["parent"], "action": "blocked" if i["blocked"] else "create-if-fresh-scan-proves-absent"} for i in checked["plan"]],
        "ledgerBlocks": blocks, "executable": not blocks and all(not i["blocked"] for i in checked["plan"]),
        "contractSha256": checked["contractSha256"],
        "contractConflicts": checked.get("contractConflicts", []),
        "note": "plan 不读云；创建前仍须 fresh 全页键扫描证明 0 匹配",
    }


def _create_wire(item: dict[str, Any], parent_record: str | None) -> dict[str, Any]:
    wire = {fid: w["wire"] for fid, w in item["writes"].items()}
    if parent_record is not None:
        for fid, w in item["writes"].items():
            if w["class"] == "create-once-parent-link":
                wire[fid] = [{"id": parent_record}]
    return wire


def execute_creates(spec: dict[str, Any], checked: dict[str, Any], transport: Transport, ledger: Ledger) -> dict[str, Any]:
    base, index = spec["target"]["baseToken"], checked["index"]
    if checked.get("contractSha256") != spec["contract"].get("sha256"):
        raise SyncError("规划与执行使用的合同摘要不一致；不得换合同(未发起任何外部调用)")
    blocks = _create_blocks(spec, checked, ledger)
    if blocks:
        raise SyncError("目标存在未对账的早先创建，已拒绝(未发起任何外部调用)；先 reconcile 或人工放行：" + json.dumps(blocks, ensure_ascii=False))
    log_dir = _fresh_log_dir(spec)
    run_id = uuid.uuid4().hex
    keys = [_create_key(spec, i) for i in checked["plan"] if not i["blocked"]]
    report: dict[str, Any] = {"schema": REPORT_SCHEMA_V3, "mode": "execute", "runId": run_id, "operator": operator(), "startedAt": now(),
                              "baseToken": base, "profile": spec["target"]["profile"], "ledgerDir": str(ledger.root),
                              "contractSha256": checked["contractSha256"], "records": [], "status": "RUNNING",
                              "contractConflicts": checked.get("contractConflicts", [])}
    created: dict[str, str] = {}
    stop = False
    with ledger.locks(keys, run_id):
        blocks = _create_blocks(spec, checked, ledger)
        if blocks:
            raise SyncError("目标在加锁后出现未对账记录，已拒绝：" + json.dumps(blocks, ensure_ascii=False))
        for item in checked["plan"]:
            key_field, key_value = item["stableKey"]["fieldId"], item["stableKey"]["value"]
            entry: dict[str, Any] = {"tableId": item["tableId"], "stableKey": item["stableKey"], "status": "NOT_ATTEMPTED"}
            report["records"].append(entry)
            if item["blocked"]:
                entry.update(status="BLOCKED_ENTITY", reasons=item["blocked"])
                continue
            if stop:
                entry["status"] = "NOT_ATTEMPTED_AFTER_STOP"
                continue
            parent_record = None
            if item["parent"] is not None:
                parent = item["parent"]
                parent_record = parent.get("recordId") or created.get(parent.get("createRef", ""))
                if parent_record is None:
                    entry.update(status="BLOCKED_ENTITY", reasons=["父记录未确认(createRef 未成功)"])
                    continue
                pending_release = _pending_human_release(ledger, base, _parent_table(index, item), parent_record, _text((parent.get("stableKey") or {}).get("value")))
                if pending_release:  # 父案历史上有未获用户风险接受的人工放行：保持保护，不在其下新增子行(子行会改动父行的反向关联)
                    entry.update(status="BLOCKED_ENTITY", reasons=[f"父案历史上存在未获用户风险接受的 human_release({pending_release})：保持保护，不新增子行"])
                    continue
                try:  # parent must exist and carry the stated stable key right now
                    parent_row = transport.get_records(base, _parent_table(index, item), [parent_record])[parent_record]
                except RuntimeSyncError as error:
                    entry.update(status="BLOCKED_ENTITY", reasons=[f"父记录读不到：{error}"])
                    continue
                pkey = parent.get("stableKey")
                if isinstance(pkey, dict) and not cell_equals("text", parent_row.get(pkey.get("fieldId")), pkey.get("value", "")):
                    entry.update(status="BLOCKED_ENTITY", reasons=["父记录稳定键与云端不符"])
                    continue
            if item["route"] is not None and item["route"].get("adminOnly") is not True:  # the team base row must exist and be the frozen one
                route = item["route"]
                try:
                    transport.get_records(base, route["teamTableId"], [route["teamRecordId"]])
                except RuntimeSyncError as error:
                    entry.update(status="BLOCKED_ENTITY", reasons=[f"八队底册行读不到：{error}"])
                    continue
            try:
                matches = transport.find_records(base, item["tableId"], key_field, key_value)
            except RuntimeSyncError as error:
                entry.update(status="UNKNOWN_SCAN", error=str(error))
                stop = True
                continue
            if len(matches) > 1:
                entry.update(status="DUPLICATE_KEYS_BLOCK", recordIds=matches)
                stop = True
                continue
            if len(matches) == 1:
                verdict = _classify_existing(index, item, matches[0], parent_record, transport, base)
                entry.update(recordId=matches[0], **verdict["entry"])
                if verdict["usable"]:  # 稳定实体/路由/父链已核实：只作"已存在的同一实体"，不声称本次载荷已创建
                    created[key_value] = matches[0]
                continue
            wire = _create_wire(item, parent_record)
            intent_id = digest({"base": base, "table": item["tableId"], "stableKey": item["stableKey"], "wire": wire})
            ledger_key = _create_key(spec, item)
            ledger.append(ledger_key, {"event": "intent", "kind": "create", "intentId": intent_id, "runId": run_id, "operator": operator(),
                                       "logDir": str(log_dir), "target": {"base": base, "table": item["tableId"]}, "stableKey": item["stableKey"],
                                       "parent": parent_record, "route": item["route"],
                                       "fields": {fid: {"name": item["writes"][fid]["name"], "type": item["writes"][fid]["type"], "old": None, "wire": w} for fid, w in wire.items()}})
            _append(log_dir, "requests.jsonl", {"at": now(), "op": "create", "tableId": item["tableId"], "stableKey": item["stableKey"], "fields": wire})
            try:
                response = transport.create_record(base, item["tableId"], wire)
            except RuntimeSyncError as error:
                status = "UNKNOWN" if str(error).startswith("UNKNOWN") else "FAILED"
                entry.update(status=status, error=str(error))
                _append(log_dir, "responses.jsonl", {"at": now(), "stableKey": key_value, "error": str(error), "rawReceipt": getattr(transport, "last_body", None)})
                ledger.append(ledger_key, {"event": "final", "intentId": intent_id, "runId": run_id, "status": status, "detail": str(error)})
                stop = True
                continue
            record_id = _receipt_record_id(response)
            _append(log_dir, "responses.jsonl", {"at": now(), "stableKey": key_value, "recordId": record_id, "rawReceipt": response})
            if record_id is None:  # 回执没有唯一合法 id：效果未知，不回读 None、不向子层交出未核实 id
                entry.update(status="UNKNOWN", error="UNKNOWN: 创建回执缺少唯一合法 record_id")
                ledger.append(ledger_key, {"event": "final", "intentId": intent_id, "runId": run_id, "status": "UNKNOWN", "detail": entry["error"]})
                stop = True
                continue
            entry.update(status="CREATED", recordId=record_id)
            problems = _verify_created(index, item, record_id, parent_record, transport, base)
            if problems:
                entry.update(status="VIOLATION", violations=problems)
                ledger.append(ledger_key, {"event": "final", "intentId": intent_id, "runId": run_id, "status": "VIOLATION", "detail": "; ".join(problems)})
                stop = True
                continue
            created[key_value] = record_id
            ledger.append(ledger_key, {"event": "bound", "intentId": intent_id, "runId": run_id, "recordId": record_id})
            ledger.append(ledger_key, {"event": "final", "intentId": intent_id, "runId": run_id, "status": "OK", "detail": ""})
            entry["status"] = "CREATED_VERIFIED"
    report["finishedAt"] = now()
    bad = [e for e in report["records"] if e["status"] in {"UNKNOWN", "FAILED", "VIOLATION", "UNKNOWN_SCAN", "DUPLICATE_KEYS_BLOCK"}]
    held = [e for e in report["records"] if e["status"] in {"EXISTING_DIVERGED", "BLOCKED_ENTITY"}]  # 精确目标及其依赖被挡；独立对象照常继续，不是失败、不写账本封锁
    report["blockedEntities"] = [{k: e.get(k) for k in ("tableId", "stableKey", "status", "violations", "reasons")} for e in held]
    report["status"] = "FAILED" if bad else ("PARTIAL" if held else "OK")
    _write(log_dir, "report.json", report)
    if bad:
        raise RuntimeSyncError("创建失败/结果未知/违规，已停止且未重试，目标已在账本中封锁：" + json.dumps([{k: e.get(k) for k in ("stableKey", "status", "error", "violations")} for e in bad], ensure_ascii=False))
    return report


def _classify_existing(index, item, record_id, parent_record, transport, base) -> dict[str, Any]:
    """A unique key match is only a candidate. Identity (key, routing, parent link) must equal the current intent;
    source-scalar differences are legitimate updates for the V2 contract, never silently claimed as this create."""
    try:
        row = transport.get_records(base, item["tableId"], [record_id])[record_id]
    except RuntimeSyncError as error:
        return {"usable": False, "entry": {"status": "EXISTING_DIVERGED", "violations": [f"已存在记录读不到：{error}"]}}
    wire = _create_wire(item, parent_record)
    identity_bad: list[str] = []
    update_fields: list[str] = []
    for fid, intended in wire.items():
        w = item["writes"][fid]
        meta = index[item["tableId"]][fid]
        if fid not in row:
            same = False
        elif meta["type"] == "link":
            same = canon(row[fid]) == canon(intended)
        else:
            same = cell_equals(meta["type"], row[fid], intended)
        if same:
            continue
        (update_fields if w["class"] == "create-once-source-scalar" else identity_bad).append(meta["name"])
    if identity_bad:
        return {"usable": False, "entry": {"status": "EXISTING_DIVERGED", "violations": [f"同键已存在记录的身份/路由/父链字段与当前意图不符：{identity_bad}"], "sourceScalarDiffs": update_fields}}
    if update_fields:  # 实体已核实为同一实体；字段差异只能走 V2 更新契约(五个白名单字段)，不重放创建
        return {"usable": True, "entry": {"status": "EXISTING_NEEDS_V2_UPDATE", "sourceScalarDiffs": update_fields}}
    return {"usable": True, "entry": {"status": "ALREADY_PRESENT_VERIFIED", "verifiedFields": sorted(wire), "note": "既有记录与当前意图一致；未声称由本次载荷创建"}}


def _parent_table(index: dict[str, dict[str, dict[str, Any]]], item: dict[str, Any]) -> str:
    link = next(fid for fid, w in item["writes"].items() if w["class"] == "create-once-parent-link")
    for table_id, fields in index.items():  # the link points at the table of the previous rank
        if _table_name(index, table_id) == ("案卷主表" if item["tableName"] == "检查明细" else "检查明细"):
            return table_id
    raise SyncError(f"无法确定父表：{link}")


def _verify_created(index, item, record_id, parent_record, transport, base) -> list[str]:
    """Read back by the returned recordId (never by response order): key, every written field, parent, duplicates."""
    problems: list[str] = []
    key_field, key_value = item["stableKey"]["fieldId"], item["stableKey"]["value"]
    try:
        row = transport.get_records(base, item["tableId"], [record_id])[record_id]
    except RuntimeSyncError as error:
        return [f"创建后回读失败：{error}"]
    wire = _create_wire(item, parent_record)
    for fid, intended in wire.items():
        meta = index[item["tableId"]][fid]
        if fid not in row:
            problems.append(f"回读缺少字段 {meta['name']}")
        elif meta["type"] == "link":
            if canon(row[fid]) != canon(intended):
                problems.append(f"回读关联不等于目标：{meta['name']}")
        elif not cell_equals(meta["type"], row[fid], intended):
            problems.append(f"回读值不等于目标值：{meta['name']}")
    try:
        matches = transport.find_records(base, item["tableId"], key_field, key_value)
    except RuntimeSyncError as error:
        problems.append(f"创建后键扫描失败：{error}")
    else:
        if matches != [record_id]:
            problems.append(f"创建后稳定键匹配不唯一：{matches}")
    return problems


def reconcile_creates(spec: dict[str, Any], checked: dict[str, Any], transport: Transport, ledger: Ledger) -> dict[str, Any]:
    """Read-only against Feishu. Zero matches never authorize a retry; one match needs request-terminal proof to clear."""
    base, index = spec["target"]["baseToken"], checked["index"]
    log_dir = _fresh_log_dir(spec)
    run_id = uuid.uuid4().hex
    release = spec.get("humanRelease")
    proof = spec.get("requestTerminalProof")
    report: dict[str, Any] = {"schema": REPORT_SCHEMA_V3, "mode": "reconcile", "runId": run_id, "operator": operator(), "startedAt": now(),
                              "ledgerDir": str(ledger.root), "externalWrites": 0, "targets": []}
    keys = [_create_key(spec, i) for i in checked["plan"]]
    with ledger.locks(keys, run_id):
        for item in checked["plan"]:
            key = _create_key(spec, item)
            status = ledger.state(key)
            entry: dict[str, Any] = {"tableId": item["tableId"], "stableKey": item["stableKey"], "ledgerState": status["state"]}
            report["targets"].append(entry)
            if status["state"] == "CLEAR":
                entry["outcome"] = "NOTHING_TO_RECONCILE"
                continue
            intent = status["lastIntent"]
            if intent.get("stableKey") != item["stableKey"]:
                raise SyncError("对账 spec 的稳定键与账本意图不一致，已拒绝")
            entry["intentId"] = intent["intentId"]
            if release is not None and release.get("intentId") == intent["intentId"]:  # 人工放行只作用于其精确 intent，不推广到其他目标
                ledger.append(key, {"event": "human_release", "intentId": intent["intentId"], "runId": run_id, "reason": release["reason"], "operator": release["operator"],
                                    "evidence": release["evidence"], "acknowledgesLateWriteRisk": True, "priorState": status["reason"], "originalIntent": intent})
                entry["outcome"] = "HUMAN_RELEASED"
                continue
            recovery = spec.get("technicalRecovery")
            if recovery is not None:
                if recovery["intentId"] != intent["intentId"]:
                    entry["outcome"] = "RECOVERY_NOT_FOR_THIS_INTENT"   # 恢复只对其精确 intentId 有效，不批量
                    continue
                outcome, observed, recovery_facts = _create_recovery(index, item, intent, ledger.events(key), status, transport, base, recovery["minQuietSeconds"])
                ledger.append(key, {"event": "reconciled", "intentId": intent["intentId"], "runId": run_id, "outcome": outcome, "originalIntent": intent,
                                    "observed": observed, "recovery": recovery_facts,
                                    "detail": "严格技术恢复：创建请求已结束并记 UNKNOWN、静默期已过、全键扫描与等价行扫描均为 0 匹配，证明未生效"
                                    if outcome == "NOT_APPLIED_PROVEN" else "技术恢复证据不足，保持封锁：" + "；".join(recovery_facts.get("reasons", []))})
                entry["outcome"], entry["observed"], entry["recovery"] = outcome, observed, recovery_facts
                continue
            outcome, observed = _classify_create(index, item, intent, transport, base, proof, ledger.events(key))
            ledger.append(key, {"event": "reconciled", "intentId": intent["intentId"], "runId": run_id, "outcome": outcome, "originalIntent": intent,
                                "observed": observed, "terminalProof": observed.get("verifiedProof", []) if outcome == "APPLIED" else [],
                                "detail": "0 匹配不能证明迟到请求不会执行，保持封锁" if outcome == "ZERO_MATCH" else ""})
            entry["outcome"], entry["observed"] = outcome, observed
    report["finishedAt"] = now()
    _write(log_dir, "reconcile-report.json", report)
    return report


def _create_recovery(index, item, intent, events, status, transport, base, min_quiet, now_epoch: float | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Strict, ledger-recorded recovery of an UNKNOWN create intent whose row does not exist. Never a human release; old events are never edited.

    All must hold, otherwise the target stays BLOCKED (RECOVERY_REFUSED): the ledger reason is UNKNOWN with exactly one UNKNOWN ``final`` (the run ended
    and recorded it); at least ``min_quiet`` seconds passed since that final; a fresh full-page stable-key scan has 0 matches; and no row of the table
    carries the very same content (every intent field incl. the parent link) under any other key. A single match is never cleared here.
    """
    reasons: list[str] = []
    facts: dict[str, Any] = {"kind": "create-quiet-zero-match", "minQuietSeconds": min_quiet, "reasons": reasons}
    if status.get("reason") not in {"UNKNOWN", "ZERO_MATCH", "UNREADABLE", "RECOVERY_REFUSED"}:   # 后三者是该 UNKNOWN 意图此前的对账观察；原 UNKNOWN 终态仍须唯一
        reasons.append(f"账本状态 {status.get('reason')} 不可技术恢复(只限 UNKNOWN 终态的创建意图)")
    finals = [e for e in events if e.get("event") == "final" and e.get("intentId") == intent["intentId"]]
    if len(finals) != 1 or finals[0].get("status") != "UNKNOWN":
        reasons.append("原意图须恰有一条 UNKNOWN 终态事件(进程已结束并记账)")
    final_epoch = _iso_epoch(finals[0].get("at")) if len(finals) == 1 else None
    if final_epoch is None:
        reasons.append("终态时间无法解析")
    current = now_epoch if now_epoch is not None else _now_epoch()
    if final_epoch is not None:
        facts["finalAt"], facts["quietSeconds"] = finals[0].get("at"), int(current - final_epoch)
        if current - final_epoch < min_quiet:
            reasons.append(f"静默期不足 {min_quiet} 秒")
    if reasons:
        return "RECOVERY_REFUSED", {}, facts
    outcome, observed = _classify_create(index, item, intent, transport, base, None, events)
    facts["readbackOutcome"] = outcome
    if outcome != "ZERO_MATCH":
        reasons.append(f"全键扫描结果为 {outcome}，不是 0 匹配")
        return "RECOVERY_REFUSED", observed, facts
    key_field = intent["stableKey"]["fieldId"]
    try:
        rows = transport.list_rows(base, item["tableId"], list(intent["fields"]))
    except (RuntimeSyncError, AttributeError) as error:
        reasons.append(f"等价行扫描读取失败：{error}")
        return "RECOVERY_REFUSED", observed, facts
    twins = [rid for rid, row in rows.items() if all(
        fid in row and (canon(row[fid]) == canon(f["wire"]) if f["type"] == "link" else cell_equals(f["type"], row[fid], f["wire"]))
        for fid, f in intent["fields"].items() if fid != key_field)]
    facts["equivalentRowScan"] = {"rows": len(rows), "equivalentUnderOtherKey": twins}
    if twins:
        reasons.append(f"表内已有内容完全相同的行(键不同)：{twins}")
        return "RECOVERY_REFUSED", {**observed, "equivalentRows": twins}, facts
    return "NOT_APPLIED_PROVEN", observed, facts


def _classify_create(index, item, intent, transport, base, proof, events=()) -> tuple[str, dict[str, Any]]:
    key_field, key_value = intent["stableKey"]["fieldId"], intent["stableKey"]["value"]
    try:
        matches = transport.find_records(base, item["tableId"], key_field, key_value)
    except RuntimeSyncError as error:
        return "UNREADABLE", {"error": str(error)}
    observed: dict[str, Any] = {"matches": matches}
    if not matches:
        return "ZERO_MATCH", observed
    if len(matches) > 1:
        return "MULTIPLE_MATCH", observed
    try:
        row = transport.get_records(base, item["tableId"], matches)[matches[0]]
    except RuntimeSyncError as error:
        return "UNREADABLE", {**observed, "error": str(error)}
    for fid, field in intent["fields"].items():
        if fid not in row:
            return "UNREADABLE", observed
        same = canon(row[fid]) == canon(field["wire"]) if field["type"] == "link" else cell_equals(field["type"], row[fid], field["wire"])
        if not same:
            return "DIVERGED", observed
    verified, reasons = _verify_terminal_proof(proof, item, intent, base, matches[0])
    if not verified:
        verified, history_reasons = _verify_history_creation_proof(proof, item, intent, events, transport, base, matches[0])
        reasons = [*reasons, *history_reasons]
    if not verified:
        return "ONE_MATCH_NEEDS_TERMINAL_PROOF", {**observed, "proofRejected": reasons}
    return "APPLIED", {**observed, "verifiedProof": verified}


def _jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verify_history_creation_proof(proof: Any, item, intent, events, transport, base: str, record_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Causal proof from the service's own revision history, for an original run that left no receipt log.

    Accepted only when the intent has exactly one UNKNOWN ``final`` event, no other create intent for this stable key lies in the window,
    and the matched row's FIRST service revision was created inside [intent time - slack, final time + slack]. The unique stable-key match and the
    field-by-field equality with the intent are checked by the caller. A free-text reference is not proof; the history is read here.
    """
    entries = [e for e in (proof if isinstance(proof, list) else []) if isinstance(e, dict) and e.get("kind") == "record-history-creation"]
    if len(entries) != 1 or entries[0].get("intentId") != intent.get("intentId"):
        return [], ["未提供针对该精确 intentId 的 record-history-creation 证明"]
    finals = [e for e in events if e.get("event") == "final" and e.get("intentId") == intent["intentId"]]
    if len(finals) != 1 or finals[0].get("status") != "UNKNOWN":
        return [], ["原意图须恰有一条 UNKNOWN 终态事件"]
    start, end = _iso_epoch(intent.get("at")), _iso_epoch(finals[0].get("at"))
    if start is None or end is None:
        return [], ["意图/终态时间无法解析"]
    start, end = start - HISTORY_CREATE_SLACK_BEFORE, end + HISTORY_CREATE_SLACK_AFTER
    others = [e for e in events if e.get("event") == "intent" and e.get("intentId") != intent["intentId"]
              and (_iso_epoch(e.get("at")) or 0) >= start - 1 and (_iso_epoch(e.get("at")) or 0) <= end]
    if others:
        return [], ["同一稳定键在该窗口内另有创建意图，不能归因"]
    try:
        history = transport.get_record_history(base, item["tableId"], record_id)
    except (RuntimeSyncError, AttributeError) as error:
        return [], [f"服务端历史不可读：{error}"]
    if not history:
        return [], ["服务端历史为空"]
    first = min(history, key=lambda h: h["rev"])
    if not (start <= first["createTime"] <= end):
        return [], ["该行的首个服务端修订不在原创建请求的时间窗口内"]
    return [{"kind": "record-history-creation", "intentId": intent["intentId"], "recordId": record_id, "firstRev": first["rev"],
             "firstRevCreateTime": first["createTime"], "operator": first.get("operator"), "window": [start, end]}], []


def _verify_terminal_proof(proof: Any, item, intent, base: str, record_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Causal proof that the ORIGINAL request reached a service terminal state and produced this very row.

    Accepted only from the original run's own logs: logDir == intent.logDir, report.runId == intent.runId, the logged
    request equals the intent (table, stable key, every field wire), and exactly one logged service receipt (ok, one
    valid id) names the matched record. Hashes of both logs are pinned. A non-empty/bool/free-text reference is no proof.
    """
    if not isinstance(proof, list) or not proof:
        return [], ["未提供 requestTerminalProof"]
    reasons: list[str] = []
    for n, ref in enumerate(proof):
        tag = f"proof[{n}]"
        if not isinstance(ref, dict) or ref.get("kind") != "service-receipt-log":
            reasons.append(f"{tag}：kind 必须是 service-receipt-log")
            continue
        try:
            log_dir = Path(str(ref.get("logDir", "")))
            if str(log_dir) != str(intent.get("logDir")) or not log_dir.is_absolute():
                reasons.append(f"{tag}：logDir 不是原意图的 logDir")
                continue
            files = {name: log_dir / name for name in ("requests.jsonl", "responses.jsonl", "report.json")}
            if not all(f.is_file() for f in files.values()):
                reasons.append(f"{tag}：原日志文件缺失")
                continue
            if ref.get("requestsSha256") != file_sha(files["requests.jsonl"]) or ref.get("responsesSha256") != file_sha(files["responses.jsonl"]):
                reasons.append(f"{tag}：日志摘要与登记不符")
                continue
            report = json.loads(files["report.json"].read_text(encoding="utf-8"))
            if report.get("runId") != intent.get("runId") or ref.get("runId") != intent.get("runId") or ref.get("intentId") != intent.get("intentId"):
                reasons.append(f"{tag}：run/intent 与原意图不一致")
                continue
            if report.get("baseToken") not in (None, base):
                reasons.append(f"{tag}：原运行的 base 不同")
                continue
            wanted = {fid: f["wire"] for fid, f in intent["fields"].items()}
            requests = [r for r in _jsonl(files["requests.jsonl"]) if isinstance(r, dict) and r.get("op") == "create" and r.get("stableKey") == intent["stableKey"]]
            if len(requests) != 1 or requests[0].get("tableId") != item["tableId"] or canon(requests[0].get("fields")) != canon(wanted):
                reasons.append(f"{tag}：原请求(表/稳定键/字段载荷)与意图不一致或不唯一")
                continue
            key_value = intent["stableKey"]["value"]
            responses = [r for r in _jsonl(files["responses.jsonl"]) if isinstance(r, dict) and r.get("stableKey") == key_value]
            receipts = [r for r in responses if isinstance(r.get("rawReceipt"), dict) and r["rawReceipt"].get("ok") is True]
            ids = {_receipt_record_id(r["rawReceipt"]) for r in receipts}
            if len(responses) != 1 or len(receipts) != 1 or len(ids) != 1 or None in ids or ids != {record_id}:  # 恰好一条成功回执；两条同 id 也不算唯一证明
                reasons.append(f"{tag}：服务回执须恰好一条且成功，其 record_id 等于当前唯一匹配")
                continue
        except (OSError, ValueError, KeyError, TypeError) as error:
            reasons.append(f"{tag}：证明无法核验：{error}")
            continue
        return [{"kind": "service-receipt-log", "logDir": str(log_dir), "runId": intent["runId"], "intentId": intent["intentId"],
                 "requestsSha256": ref["requestsSha256"], "responsesSha256": ref["responsesSha256"], "recordId": record_id}], []
    return [], reasons


# ---------------------------------------------------------------------------------------------
# 权威已存详情 -> 带字段依据的基础字段说明(V3 创建说明 + V2 补空说明)的正常生成入口
# ---------------------------------------------------------------------------------------------

BASIC_FIELDS = {"key": "fld1lHXHpk", "unit": "fld2Lo4dpD", "addr": "fld71ckPl9", "date": "fld5azNPuE", "team": "fld2Kdm4i2", "link": "fldlw1KAaR"}
# 2026-11-09 必有字段：初查结果（含待结果/待核实）与外显初查日期来源。字段与选项以最新合同为准，合同没有则整项跳过并在报告列出。
LIFECYCLE_FIELDS = {"result": "fld77TOx5G", "date_source": "fld4upZMpZ"}
SOURCE_TEAM_FIELD = "fld73J0dOH"  # 大队来源
PENDING_LABELS = {"待结果", "待核实"}
BUILD_SCHEMA = "FeishuBasicFieldBuildReportV1"
RWID_IN_URL = re.compile(r"[?&#]RWID=(\d+)")
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


PRODUCT_ROOT = re.compile(r"\[产\]\S*?-(\d{8}C\d{9})-")


def _tree_titles(tree: Any) -> list[str]:
    titles: list[str] = []
    for node in tree if isinstance(tree, list) else []:
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):
                titles.append(node["title"])
            titles.extend(_tree_titles(node.get("children")))
    return titles


def load_user_release_acceptances(path: Path) -> list[dict[str, Any]]:
    """UserRiskAcceptanceV1 的材料结构校验：只检查必填字段与 intentId 匹配，**不能证明真实的人类授权**。

    真实例外仍须可核的用户原话(userQuote 的出处 sourceRef)；助手签名、通用实施授权、ack=true 都不是依据。
    本函数不是权限系统，任何自动编排都不得自造或传入这类记录。"""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SyncError(f"无法读取用户风险接受记录：{error.__class__.__name__}") from error
    items = document.get("acceptances") if isinstance(document, dict) and document.get("schemaVersion") == "UserRiskAcceptanceV1" else None
    if not isinstance(items, list) or not items:
        raise SyncError("用户风险接受记录需要 schemaVersion=UserRiskAcceptanceV1 与 acceptances 列表")
    for item in items:
        if not (isinstance(item, dict) and item.get("acceptedBy") == "user" and all(isinstance(item.get(k), str) and item[k].strip() for k in ("intentId", "userQuote", "sourceRef", "acceptedAt"))):
            raise SyncError("每条风险接受需要 acceptedBy=user、intentId、用户原话 userQuote、sourceRef 与 acceptedAt")
    return items


def require_user_acceptance_for_release(spec: Any, acceptance_path: Path | None) -> None:
    """正常入口不接受助手自署的 humanRelease：spec 带 humanRelease 时必须同时给出用户对该精确意图的风险接受记录(仅结构校验，见 load_user_release_acceptances)。"""
    release = spec.get("humanRelease") if isinstance(spec, dict) else None
    if release is None:
        return
    if acceptance_path is None:
        raise SyncError("humanRelease 只能由用户接受迟到写风险后使用：必须附 --user-risk-acceptance(UserRiskAcceptanceV1，含用户原话)；助手不得自署")
    intents = {a["intentId"] for a in load_user_release_acceptances(acceptance_path)}
    if release.get("intentId") not in intents:
        raise SyncError("用户风险接受记录没有覆盖该精确 intentId，拒绝")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _record_basis(ref: str, observed_at: str) -> dict[str, Any]:
    return {"kind": "source-observed", "valueState": "observed", "sourceRef": ref, "observedAt": observed_at}


def _followup_detail(root: Path, project: str, waterline_case: Any, listed_org: str) -> dict[str, Any] | None:
    """手动交件案卷没有整包详情时，退而用其最新可信后续观察(同一套消费者校验，且大队须等于清单大队)作为已存详情。"""
    source = waterline_case.get("source") if isinstance(waterline_case, dict) else None
    if not isinstance(source, dict) or not source.get("followupObservations") or not listed_org:
        return None
    try:
        layout = _ws.BusinessLayout.from_root(root)
        trusted, _ = _views._followup_observations(layout, project, source, [listed_org])
        if not trusted:
            return None
        _, fields, _, observed_text = trusted[0]
        entry = next(item for item in source["followupObservations"].values() if item.get("observedAt") == observed_text)
        stored = json.loads((root / str(entry["evidencePath"]).replace("\\", "/")).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - 读不到/校验不过就不采纳
        return None
    return {"fields": fields, "sourceUrl": stored.get("sourceUrl"), "capturedAt": observed_text, "projectNo": project,
            "_refBase": f"{entry['evidencePath']} | 后续观察 {entry['observationId']} | evidenceSha256={entry['evidenceSha256']}"}


def _entity_evidence(rwid: str, record: Any, capture_name: str, capture_sha: str,
                     fallback_detail: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Read one stored detail; every value is tied to the same case (project no / RWID / URL / unit / jurisdiction)."""
    problems: list[str] = []
    if not isinstance(record, dict) or not (isinstance(record.get("collectionDetail"), dict) or fallback_detail):
        return None, ["缺少已存详情(collectionDetail)"]
    detail = record["collectionDetail"] if isinstance(record.get("collectionDetail"), dict) else fallback_detail
    listed = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    fields = detail.get("fields") if isinstance(detail.get("fields"), dict) else {}
    project = _text(record.get("projectNo"))
    url = _text(detail.get("sourceUrl"))
    captured = _text(detail.get("capturedAt"))
    if PROJECT_NO.fullmatch(project) is None:
        problems.append("项目编号格式无效")
    if _text(fields.get("项目编号")) != project or _text(detail.get("projectNo")) != project:
        problems.append("详情项目编号与登记项目编号不一致")
    found = RWID_IN_URL.findall(url)
    if str(record.get("rwid")) != rwid or found != [rwid]:
        problems.append("详情 URL 的 RWID 与记录 RWID 不一致")
    if _text(listed.get("rwid")) not in {"", rwid}:
        problems.append("列表 RWID 与记录 RWID 不一致")
    unit, jurisdiction, address = _text(fields.get("单位名称")), _text(fields.get("消防管辖")), _text(fields.get("单位地址"))
    if not unit:
        problems.append("详情缺少单位名称")
    elif " ".join(_text(listed.get("unitName")).split()) not in {"", " ".join(unit.split())}:
        problems.append("详情单位与列表单位不一致")
    if not jurisdiction:
        problems.append("详情缺少消防管辖")
    elif _text(listed.get("sourceOrganization")) not in {"", jurisdiction}:
        problems.append("详情消防管辖与列表大队不一致")
    if not captured:
        problems.append("缺少取得时点")
    roots = {m for title in _tree_titles(fields.get("文书目录")) for m in PRODUCT_ROOT.findall(title)}
    if roots != {project}:  # 类别只由同案四身份 + 本案唯一 [产] 根证明；C 号、单位名、结果合否或入口名都不是依据
        problems.append(f"文书目录没有唯一的本案 [产] 产品根(实际={sorted(roots)})，不放行")
    if problems:
        return None, problems

    def ref(field: str) -> str:
        if detail.get("_refBase"):
            return f"{detail['_refBase']}#fields.{field} | url={url} | projectNo={project}"
        return f"{capture_name}#records.{rwid}.collectionDetail.fields.{field} | batchSha256={capture_sha} | url={url} | projectNo={project}"

    return {"projectNo": project, "rwid": rwid, "unit": unit, "address": address, "jurisdiction": jurisdiction,
            "acceptance": _text(fields.get("受理日期")), "capturedAt": captured, "ref": ref, "url": url}, []


def _initial_date(waterline_case: Any, root: Path, project: str, rwid: str) -> tuple[tuple[str, str, str] | None, str | None]:
    """The actual initial-inspection day, proven from the stored original; otherwise (None, reason).

    Registry claim -> original file must exist inside the business root with the recorded SHA, belong to the same case
    (project no / RWID / URL), and its product rows marked as not-a-recheck (SFCPFC=0) must show exactly that one day
    (JCRQ). A recheck row, the acceptance date, a different day or a missing original never passes.
    """
    source = waterline_case.get("source") if isinstance(waterline_case, dict) else None
    cls = (source or {}).get("classification") if isinstance(source, dict) else None
    if not isinstance(cls, dict) or cls.get("initialInspectionDate") is None:
        return None, None  # 没有登记初查日：不是错误，只是省略
    claimed = cls["initialInspectionDate"]
    if not isinstance(claimed, str) or ISO_DAY.match(claimed) is None:
        return None, "登记的初查日格式无效"
    path_text, recorded_sha, observed = _text(cls.get("evidencePath")), _text(cls.get("evidenceSha256")), _text(cls.get("observedAt"))
    if not path_text or not recorded_sha or not observed:
        return None, "缺少原件路径/摘要/取得时点"
    relative = Path(path_text.replace("\\", "/"))
    try:
        original = (root / relative).resolve()
        original.relative_to(root.resolve())
    except (ValueError, OSError):
        return None, "原件路径越出业务根"
    if relative.is_absolute() or not original.is_file():
        return None, "原件不存在"
    actual_sha = "sha256:" + file_sha(original)
    if actual_sha != recorded_sha:
        return None, "原件实际摘要与登记不符"
    try:
        document = json.loads(original.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "原件无法读取为 JSON"
    detail: Any = None
    if isinstance(document, dict) and document.get("schemaVersion") == "SourceIdentityV1":
        detail = document
    elif isinstance(document, dict) and isinstance(document.get("records"), dict):
        detail = document["records"].get(rwid)
    if not isinstance(detail, dict) or not isinstance(detail.get("fields"), dict):
        return None, "原件里没有本案详情"
    fields = detail["fields"]
    url = _text(detail.get("sourceUrl"))
    same_case = _text(detail.get("projectNo")) == project and _text(fields.get("项目编号")) == project
    if "rwid" in detail:
        same_case = same_case and str(detail["rwid"]) == rwid
    if not same_case or RWID_IN_URL.findall(url) != [rwid]:
        return None, "原件不是同一案(项目编号/RWID/URL)"
    rows = fields.get("检查产品信息")
    initial_days: set[str] = set()
    positions: list[int] = []
    for position, row in enumerate(rows if isinstance(rows, list) else []):
        record = row.get("页面记录") if isinstance(row, dict) else None
        if isinstance(record, dict) and record.get("SFCPFC") == "0":  # 0 = 非复查(初查)；复查行永不作初查日依据
            day = _text(record.get("JCRQ"))[:10]
            if ISO_DAY.match(day):
                initial_days.add(day)
                positions.append(position)
    if not initial_days:
        return None, "原件没有可读的初查(SFCPFC=0)检查日"
    if initial_days != {claimed}:
        return None, f"原件初查日{sorted(initial_days)}与登记{claimed}不一致"
    where = f"检查产品信息[{','.join(map(str, positions))}].页面记录.JCRQ(SFCPFC=0)={claimed}"
    return (claimed, f"{path_text} | {actual_sha} | {where} | projectNo={project} | RWID={rwid} | url={url}", observed), None


def _proven_classification(waterline_case: Any, root: Path) -> dict[str, Any] | None:
    """The waterline classification, only when its stored original exists inside the root with the recorded SHA."""
    source = waterline_case.get("source") if isinstance(waterline_case, dict) else None
    cls = (source or {}).get("classification") if isinstance(source, dict) else None
    if not isinstance(cls, dict):
        return None
    path_text, recorded = _text(cls.get("evidencePath")), _text(cls.get("evidenceSha256"))
    if not path_text or not recorded:
        return None
    relative = Path(path_text.replace("\\", "/"))
    try:
        original = (root / relative).resolve()
        original.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    if relative.is_absolute() or not original.is_file() or "sha256:" + file_sha(original) != recorded:
        return None
    return cls


def _followup_initial_result(root: Path, project: str, waterline_case: Any, classification: dict[str, Any] | None,
                             jurisdiction: str | None) -> dict[str, str] | None:
    """初查结果只在总账分类证据不能证明时，才取已归档案卷的最新可信后续观察(source_intake.add_followup_observation)。

    观察须经消费者同样的校验(证据摘要、同案、[产]根、目录覆盖、截图、同一大队)，且比分类证据更新；只采纳页面产品行明确给出的
    合格/不合格，其余不改写。返回外显标签与可回溯依据(证据文件、摘要、观察时点)。
    """
    source = waterline_case.get("source") if isinstance(waterline_case, dict) else None
    if not isinstance(source, dict) or not source.get("followupObservations"):
        return None
    if isinstance(classification, dict) and classification.get("initialResult") in case_lifecycle._RESULT_FROM_ENUM:
        return None
    try:
        layout = _ws.BusinessLayout.from_root(root)
        baseline = [jurisdiction]
        if jurisdiction is None:  # 与本地视图同一顺序取大队基线：已保存详情 → 总账大队 → 人工接件基线
            saved = _views._saved_detail(layout, project, source)[0]
            baseline = [_views.source_intake_brigade(saved[0]) if saved else None]
            if baseline == [None]:
                baseline = [waterline_case.get("brigadeName")]
            if baseline == [None]:
                manual = _views.source_intake_manual_baseline(layout, project, source)
                baseline = [manual["brigade"]] if manual is not None else [None]
        trusted, _ = _views._followup_observations(layout, project, source, baseline)
    except Exception:  # noqa: BLE001 - 无法读取/校验就不采纳，不猜
        return None
    if not trusted:
        return None
    _, fields, _, observed_text = trusted[0]
    label = case_lifecycle.evidence_initial_result(fields)
    if label not in {"合格", "不合格"}:  # 待结果/待核实互转没有信息增量，不制造差异
        return None
    entry = next((item for item in source["followupObservations"].values() if item.get("observedAt") == observed_text), None)
    if not entry:
        return None
    ref = (f"{entry['evidencePath']}#fields.检查产品信息 | 后续观察 {entry['observationId']} | "
           f"evidenceSha256={entry['evidenceSha256']} | projectNo={project}")
    return {"label": label, "ref": ref, "observedAt": observed_text}


def _detail_initial_result(root: Path, project: str, waterline_case: Any, classification: dict[str, Any] | None) -> dict[str, str] | None:
    """初查结果在总账分类证据和后续观察都不能证明时，取该案自己保存的、可信的来源详情页(与本地视图同一读取与校验)。

    只采纳页面产品行按严格规则明确给出的合格/不合格；复查行不参与；待结果/待核实互转没有信息增量，不制造差异。
    返回外显标签与可回溯依据(已保存详情文件、其摘要、观察时点)。
    """
    source = waterline_case.get("source") if isinstance(waterline_case, dict) else None
    if not isinstance(source, dict):
        return None
    if isinstance(classification, dict) and classification.get("initialResult") in case_lifecycle._RESULT_FROM_ENUM:
        return None
    try:
        layout = _ws.BusinessLayout.from_root(root)
        found, _ = _views._saved_detail(layout, project, source)
    except Exception:  # noqa: BLE001 - 无法读取/校验就不采纳，不猜
        return None
    if not found:
        return None
    fields, _, captured = found
    label = case_lifecycle.evidence_initial_result(fields)
    if label not in {"合格", "不合格"}:
        return None
    candidates = [layout.pending_case_dir(project) / "source-evidence.json"]
    completed = layout.completed_case_dir(project)
    if completed.is_dir():
        candidates += sorted(completed.glob("*/source-evidence.json"), reverse=True)
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
            relative = path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        records = [doc] if doc.get("projectNo") == project and doc.get("capturedAt") == captured else []
        records += [r for r in (doc.get("records") or {}).values() if isinstance(r, dict) and r.get("projectNo") == project and r.get("capturedAt") == captured]
        if any(r.get("fields") == fields for r in records):
            ref = f"{relative.as_posix()}#fields.检查产品信息 | 已保存来源详情 | evidenceSha256=sha256:{file_sha(path)} | projectNo={project}"
            return {"label": label, "ref": ref, "observedAt": captured}
    return None


def unified_initial_result(root: Path, project: str, waterline_case: Any, *, classification: dict[str, Any] | None = None,
                           jurisdiction: str | None = None, manifest_found: Any = None) -> dict[str, Any]:
    """案卷级初查结果的唯一规则：主行、初查检查行和可由案卷结论确定的产品行都用它，不再各算各的。

    来源只有本地已核证据(代表监督系统)：总账分类终局(原件摘要相符) → 最新可信后续观察 → 本案已保存的可信详情 → 本案已核验清单的
    产品逐项结果(任一不合格→不合格；全部合格→合格)。案卷侧终局与清单逐项终局不一致时不取任何一方(conflict)，双方原值都保留，交核对。
    都没有终局时给待结果(分类登记了抽样待出)或待核实；这两个未决标签之间互转没有信息增量，调用方不据此改写已有未决值。
    """
    classification = classification if classification is not None else _proven_classification(waterline_case, root)
    found = manifest_found if manifest_found is not None else _verified_manifest(root, project, waterline_case)[0]
    manifest_label = manifest_ref = manifest_at = None
    if found is not None:
        manifest, relative, recorded, verified_at = found
        products = case_lifecycle._manifest_products(manifest)[0]
        results = [case_lifecycle._manifest_result(p) for p in products]
        if "不合格" in results:
            manifest_label = "不合格"
        elif results and all(r == "合格" for r in results):
            manifest_label = "合格"
        manifest_ref = f"{relative}#initialInspection.products[*].result | 本地核验清单 manifestSha256={recorded} | projectNo={project}"
        manifest_at = verified_at
    case_label = case_source = case_ref = case_at = None
    if isinstance(classification, dict) and classification.get("initialResult") in case_lifecycle._RESULT_FROM_ENUM:
        case_label, case_source = case_lifecycle._RESULT_FROM_ENUM[classification["initialResult"]], "classification"
        case_ref = (f"{_text(classification.get('evidencePath')).replace(chr(92), '/')}#classification.initialResult | 总账分类证据 "
                    f"evidenceSha256={_text(classification.get('evidenceSha256'))} | projectNo={project}")
    else:
        for source_name, getter in (("followup", lambda: _followup_initial_result(root, project, waterline_case, classification, jurisdiction)),
                                    ("detail", lambda: _detail_initial_result(root, project, waterline_case, classification))):
            extra = getter()
            if extra:
                case_label, case_source, case_ref, case_at = extra["label"], source_name, extra["ref"], extra["observedAt"]
                break
    out: dict[str, Any] = {"manifestLabel": manifest_label, "caseLabel": case_label, "conflict": None}
    if case_label and manifest_label and case_label != manifest_label:
        out |= {"label": None, "terminal": False, "source": None, "ref": None, "observedAt": None,
                "conflict": f"案卷侧证据({case_source})为{case_label}，清单产品逐项汇总为{manifest_label}"}
    elif case_label:
        out |= {"label": case_label, "terminal": True, "source": case_source, "ref": case_ref, "observedAt": case_at}
    elif manifest_label:
        out |= {"label": manifest_label, "terminal": True, "source": "manifest", "ref": manifest_ref, "observedAt": manifest_at}
    else:
        out |= {"label": case_lifecycle.initial_result_label(classification), "terminal": False, "source": None, "ref": None, "observedAt": None}
    return out


def _lifecycle_values(index_case: dict[str, dict[str, Any]], candidates: set[str], classification: dict[str, Any] | None,
                      resolved: dict[str, Any], *, create: bool, result_override: str | None = None) -> tuple[dict[str, str], list[str]]:
    """New-cell values for 初查结果 / 初查日期来源, limited to what the latest contract exposes and its option lists."""
    values: dict[str, str] = {}
    skipped: list[str] = []
    wanted = {LIFECYCLE_FIELDS["result"]: result_override or case_lifecycle.initial_result_label(classification)}
    if resolved.get("value"):
        wanted[LIFECYCLE_FIELDS["date_source"]] = resolved["externalSource"]
    for field_id, value in wanted.items():
        meta = index_case.get(field_id)
        usable = meta is not None and (field_id in candidates if create else meta["class"] == WHITELIST)
        if not usable or (meta.get("options") is not None and value not in meta["options"]):
            skipped.append(f"{field_id}={value}")
            continue
        values[field_id] = value
    return values, skipped


def build_basic_specs(*, capture_path: Path, capture_sha: str, waterline_path: Path, v3_path: Path, v3_sha: str,
                      v9_path: Path, v9_sha: str, routes_path: Path, routes_sha: str, target: dict[str, str],
                      transport: Any, out_dir: Path, log_root: Path, batch_size: int = HARD_CAP_RECORDS,
                      ledger_dir: Path | None = None, user_accepted_releases: Path | None = None,
                      isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Fresh cloud first (keys, schema fields, team base), then specs only for what the stored details prove.

    Creates: 2026 cases absent from the fresh key scan. Fills: existing rows whose address / initial date is empty while the stored
    source value is present (explicit ``expectedOld: null``). Anything non-empty and different is reported, never overwritten.
    """
    _require(1 <= batch_size <= HARD_CAP_RECORDS, f"batch_size 必须是 1..{HARD_CAP_RECORDS}")
    _require(ledger_dir is not None, "生成说明必须沿唯一 P18 账(--ledger-dir)：缺账会跳过历史封锁与放行核查，拒绝")
    ledger = Ledger(ledger_dir, isolated_test=isolated_test_ledger)
    ledger.validate_root()
    _require(file_sha(capture_path) == capture_sha, "采集批次摘要与固定值不一致")
    _require(file_sha(routes_path) == routes_sha, "八队路由文件摘要与固定值不一致")
    v3 = load_contract_v3(v3_path, v3_sha)
    contract = load_contract(v9_path, v9_sha)
    case_table = v3["base"]["tables"]["案卷主表"]["tableId"]
    _require(target.get("baseToken") == v3["base"]["baseId"] == contract["baseId"], "目标 Base 与合同不一致")
    index = field_index(v3["base"])
    for field_id in BASIC_FIELDS.values():
        _require(field_id in index[case_table], f"合同缺少案卷字段 {field_id}")
    candidate_ids = {c["fieldId"] for c in v3["extension"]["createFieldsCandidate"]}
    lifecycle_skipped: list[dict[str, Any]] = []
    date_fallbacks: list[dict[str, Any]] = []
    date_conflicts: list[dict[str, Any]] = []
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    waterline = json.loads(waterline_path.read_text(encoding="utf-8")).get("cases", {})
    routes_doc = json.loads(routes_path.read_text(encoding="utf-8"))
    _require(routes_doc.get("baseId") == target["baseToken"], "路由文件 Base 与目标不一致")
    # 1) fresh cloud: team base + case rows
    team_table = {r["teamTableId"] if "teamTableId" in r else None for r in routes_doc.get("routes", []) if r.get("adminOnly") is not True}
    _require(len(team_table) == 1 and None not in team_table, "路由必须指向同一大队底册")
    (team_table_id,) = team_table
    teams = transport.list_rows(target["baseToken"], team_table_id, None)
    live_routes: dict[str, dict[str, Any]] = {}
    route_problems: list[dict[str, Any]] = []
    for route in routes_doc["routes"]:
        if route.get("adminOnly") is True:
            option = route.get("sourceOptionExact")
            if isinstance(option, str) and option:
                live_routes[route["sourceJurisdictionExact"]] = {"sourceJurisdictionExact": route["sourceJurisdictionExact"], "adminOnly": True, "sourceOptionExact": option}
            else:
                route_problems.append({"source": route["sourceJurisdictionExact"], "reason": "adminOnly 路由缺少大队来源选项原文"})
            continue
        record_id = route.get("savedTeamRecordId")
        row = teams.get(record_id)
        names = {v for v in (row or {}).values() if isinstance(v, str)} | {x for v in (row or {}).values() if isinstance(v, list) for x in v if isinstance(x, str)}
        if row is None or route["teamOptionExact"] not in names:
            route_problems.append({"source": route["sourceJurisdictionExact"], "reason": "fresh 大队底册读不到该行或名称不符"})
            continue
        live_routes[route["sourceJurisdictionExact"]] = {"sourceJurisdictionExact": route["sourceJurisdictionExact"], "teamOptionExact": route["teamOptionExact"],
                                                         "teamTableId": team_table_id, "teamRecordId": record_id}
    read_fields = list(BASIC_FIELDS.values()) + [f for f in LIFECYCLE_FIELDS.values() if f in index[case_table]]
    cloud = transport.list_rows(target["baseToken"], case_table, read_fields)
    by_key: dict[str, list[str]] = {}
    for record_id, row in cloud.items():
        by_key.setdefault(_text(row.get(BASIC_FIELDS["key"])), []).append(record_id)
    duplicate = {k: v for k, v in by_key.items() if k and len(v) > 1}
    _require(not duplicate, "fresh 云端稳定键重复，停止：" + json.dumps(duplicate, ensure_ascii=False))
    # 1b) 账本里出现过 human_release 的目标：除非用户亲自接受该精确意图的迟到写风险，否则保留未决、不生成任何创建/补空说明
    accepted_intents: set[str] = set()
    if user_accepted_releases is not None:
        accepted_intents = {a["intentId"] for a in load_user_release_acceptances(user_accepted_releases)}
    excluded_by_scope = initial_scope.excluded_projects_in_root(waterline_path.parent)
    # 2) per-entity evidence
    creates: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    differing: list[dict[str, Any]] = []
    dates_omitted: list[dict[str, Any]] = []
    already_equal = 0
    for rwid, record in sorted((capture.get("records") or {}).items(), key=lambda kv: str((kv[1] or {}).get("projectNo"))):
        project = _text((record or {}).get("projectNo"))
        year_match = re.match(r"^\d{8}C(\d{4})\d{5}$", project)
        if year_match is None or project in excluded_by_scope:
            continue  # 只消防产品监督案(C)；已按初查日期剔除的案卷不再建行
        if int(year_match.group(1)) < 2026 and waterline.get(project) is None:
            # 范围按真实初查日期(>=2026-01-01)而不是项目编号年份：旧号段案可能初查在 2026，但没有“检查日期”来源证据就不新建
            blocked.append({"projectNo": project, "rwid": rwid, "reasons": ["项目编号年份早于2026，总账没有该案：先取得“检查日期”来源清单与详情证据证明初查日期不早于2026-01-01，再按新案准入"]})
            continue
        fallback = None
        if isinstance(record, dict) and not isinstance(record.get("collectionDetail"), dict):
            fallback = _followup_detail(waterline_path.parent, project, waterline.get(project), _text((record.get("fields") or {}).get("sourceOrganization")))
        evidence, problems = _entity_evidence(rwid, record, capture_path.name, capture_sha, fallback)
        if evidence is None:
            blocked.append({"projectNo": project, "rwid": rwid, "reasons": problems})
            continue
        key = Ledger.target_key(target["baseToken"], case_table, "create:" + project)
        pending = [e for e in ledger.events(key) if e.get("event") == "human_release" and e.get("intentId") not in accepted_intents]
        if pending:
            blocked.append({"projectNo": project, "rwid": rwid, "reasons": ["历史上出现过未获用户风险接受的人工放行(human_release)；保留原 UNKNOWN 的迟到写风险，不再生成创建/补空说明",
                                                                       {"intentIds": sorted({e.get("intentId") for e in pending})}]})
            continue
        ids = by_key.get(project, [])
        # 普通未决(UNKNOWN/PENDING 等)按目标局部阻断：先看同案稳定键的创建意图，再看该记录的更新意图；晚到的行不能改走补空来绕过
        unresolved = [("create", ledger.state(key))]
        if ids:
            unresolved.append(("update", ledger.state(Ledger.target_key(target["baseToken"], case_table, ids[0]))))
        held = [(kind, st) for kind, st in unresolved if st["state"] == "BLOCKED"]
        if held:
            blocked.append({"projectNo": project, "rwid": rwid, "reasons": [f"{kind} 意图未决({st['reason']})：{st.get('detail', '')}；按目标局部阻断，独立项目继续" for kind, st in held],
                            "intentIds": [st["intentId"] for _, st in held]})
            continue
        date, date_reason = _initial_date(waterline.get(project), waterline_path.parent, project, rwid)
        if date is None:  # 总账没有可证的页面检查日时，取本案已核验清单里的初查日(本案监督检查记录文书所载)，仍优于受理日期后备
            manifest_day, _why = _manifest_initial_date(waterline_path.parent, project, waterline.get(project))
            if manifest_day is not None:
                date, date_reason = manifest_day, None
        if date_reason:
            dates_omitted.append({"projectNo": project, "rwid": rwid, "reason": date_reason})
        resolved = fv.resolve_initial_date(date[0] if date else None, evidence["acceptance"])
        if date is None and resolved["value"]:  # 电子监督检查记录缺失/不可用/无法确认：受理日期后备，内部留痕，外显统一监督系统
            date = (resolved["value"], f"{evidence['ref']('受理日期')} | 后备原因：{date_reason or '未登记可证的电子初查日'} | 受理日期原值={evidence['acceptance']}", evidence["capturedAt"])
            date_fallbacks.append({"projectNo": project, "rwid": rwid, "value": resolved["value"], "reason": date_reason or "未登记可证的电子初查日"})
        elif date is not None and resolved.get("conflict"):
            date_conflicts.append({"projectNo": project, "rwid": rwid, "electronic": resolved["value"], "acceptance": resolved["acceptanceDate"], "handling": "以电子监督检查记录为准，保留差异"})
        classification = _proven_classification(waterline.get(project), waterline_path.parent)
        unified = unified_initial_result(waterline_path.parent, project, waterline.get(project), classification=classification or {}, jurisdiction=evidence["jurisdiction"])
        followup = ({"label": unified["label"], "ref": unified["ref"], "observedAt": unified["observedAt"]}
                    if unified["terminal"] and unified["source"] != "classification" else None)   # 与子行、校准同一规则；冲突时不取任何一方
        basis_ref = evidence["ref"]("检查产品信息") + (" | 总账分类证据已核" if classification else " | 缺可核的分类证据，初查结果按待核实")
        result_ref, result_at = (followup["ref"], followup["observedAt"]) if followup else (basis_ref, None)
        if not ids:
            route = live_routes.get(evidence["jurisdiction"])
            if route is None:
                blocked.append({"projectNo": project, "rwid": rwid, "reasons": [f"路由未匹配(保留本地，不猜队)：{evidence['jurisdiction']}"]})
                continue
            observed = evidence["capturedAt"]
            fields: dict[str, Any] = {
                BASIC_FIELDS["key"]: {"new": project, "basis": _record_basis(evidence["ref"]("项目编号"), observed)},
                BASIC_FIELDS["unit"]: {"new": evidence["unit"], "basis": _record_basis(evidence["ref"]("单位名称"), observed)},
            }
            route_basis = _record_basis(evidence["ref"]("消防管辖") + f" | route={routes_path.name} sha256={routes_sha}", observed)
            if route.get("adminOnly") is True:
                source_meta = index[case_table].get(SOURCE_TEAM_FIELD)
                # 大队来源只在最新合同允许创建且选项存在时才写；合同把它列为只读时留空(所属大队/归属大队本来就为空，仅管理员可见)，不为此改合同。
                if (source_meta is not None and source_meta["class"] in CREATE_BASE_CLASS["create-once-source-scalar"]
                        and route["sourceOptionExact"] in (source_meta.get("options") or [])):
                    fields[SOURCE_TEAM_FIELD] = {"new": route["sourceOptionExact"], "basis": route_basis}
            else:
                fields[BASIC_FIELDS["team"]] = {"new": route["teamOptionExact"], "basis": route_basis}
                fields[BASIC_FIELDS["link"]] = {"new": route["teamRecordId"], "basis": route_basis}
            if evidence["address"]:
                fields[BASIC_FIELDS["addr"]] = {"new": evidence["address"], "basis": _record_basis(evidence["ref"]("单位地址"), observed)}
            if date:
                fields[BASIC_FIELDS["date"]] = {"new": date[0], "basis": _record_basis(date[1], date[2])}
            values, skipped = _lifecycle_values(index[case_table], candidate_ids, classification, fv.resolve_initial_date(date[0] if date else None, ""), create=True,
                                                result_override=followup["label"] if followup else None)
            for fid, value in values.items():
                if fid == LIFECYCLE_FIELDS["result"] and followup:
                    fields[fid] = {"new": value, "basis": _record_basis(result_ref, result_at)}
                    continue
                fields[fid] = {"new": value, "basis": _record_basis(date[1] if fid == LIFECYCLE_FIELDS["date_source"] and date else basis_ref, date[2] if fid == LIFECYCLE_FIELDS["date_source"] and date else observed)}
            if skipped:
                lifecycle_skipped.append({"projectNo": project, "fields": skipped, "stage": "create"})
            creates.append({"tableId": case_table, "stableKey": {"fieldId": BASIC_FIELDS["key"], "value": project}, "routingSource": evidence["jurisdiction"], "fields": fields})
            continue
        row = cloud[ids[0]]
        update_fields: dict[str, Any] = {}
        for name, fid, new in (("单位名称", BASIC_FIELDS["unit"], evidence["unit"]), ("单位地址", BASIC_FIELDS["addr"], evidence["address"]), ("初查日期", BASIC_FIELDS["date"], date[0] if date else "")):
            stored = row.get(fid)
            ftype = index[case_table][fid]["type"]
            if not new:
                continue
            if not cell_is_empty(stored) and cell_equals(ftype, stored, new):
                already_equal += 1
            elif cell_is_empty(stored):
                basis = _record_basis(date[1], date[2]) if fid == BASIC_FIELDS["date"] else _record_basis(evidence["ref"](name), evidence["capturedAt"])
                update_fields[fid] = {"new": new, "expectedOld": None, "basis": basis}
            else:
                differing.append({"projectNo": project, "field": name, "cloud": stored, "source": new, "handling": "云端非空且不同：不覆盖，报告交人工/来源裁决"})
        values, skipped = _lifecycle_values(index[case_table], candidate_ids, classification, fv.resolve_initial_date(date[0] if date else None, ""), create=False,
                                            result_override=followup["label"] if followup else None)
        for fid, value in values.items():
            stored, ftype = row.get(fid), index[case_table][fid]["type"]
            ref = date[1] if fid == LIFECYCLE_FIELDS["date_source"] and date else basis_ref
            at = date[2] if fid == LIFECYCLE_FIELDS["date_source"] and date else evidence["capturedAt"]
            if fid == LIFECYCLE_FIELDS["result"] and followup:
                ref, at = result_ref, result_at
            if cell_is_empty(stored):
                update_fields[fid] = {"new": value, "expectedOld": None, "basis": _record_basis(ref, at)}
            elif cell_equals(ftype, stored, value):
                already_equal += 1
            elif fid == LIFECYCLE_FIELDS["result"] and any(cell_equals(ftype, stored, label) for label in PENDING_LABELS) and value not in PENDING_LABELS:
                old_label = next(label for label in sorted(PENDING_LABELS) if cell_equals(ftype, stored, label))
                update_fields[fid] = {"new": value, "expectedOld": old_label, "basis": _record_basis(ref, at)}   # 有因更新：待结果/待核实 -> 终局值
            else:
                differing.append({"projectNo": project, "field": index[case_table][fid]["name"], "cloud": stored, "source": value, "handling": "云端非空且不同：不覆盖，报告交人工/来源裁决"})
        if skipped:
            lifecycle_skipped.append({"projectNo": project, "fields": skipped, "stage": "fill"})
        if update_fields:
            fills.append({"tableId": case_table, "recordId": ids[0], "stableKey": {"fieldId": BASIC_FIELDS["key"], "value": project}, "fields": update_fields})
    # 3) specs (≤ batch_size per run, fresh logDir per spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []

    def emit(kind: str, items: list[dict[str, Any]]) -> None:
        for number, start in enumerate(range(0, len(items), batch_size), start=1):
            chunk = items[start:start + batch_size]
            name = f"{kind}-{number:02d}"
            spec: dict[str, Any] = {"mode": "execute", "target": dict(target), "limits": {"maxRecords": batch_size}, "logDir": str(log_root / name)}
            if kind == "create":
                spec |= {"schemaVersion": SPEC_SCHEMA_V3, "contract": {"path": str(v3_path), "sha256": v3_sha},
                         "routes": [live_routes[x] for x in sorted({c["routingSource"] for c in chunk})],
                         "creates": chunk}
            else:
                spec |= {"schemaVersion": SPEC_SCHEMA, "contract": {"path": str(v9_path), "sha256": v9_sha},
                         "allowlist": {case_table: {"records": [c["recordId"] for c in chunk], "fields": sorted({f for c in chunk for f in c["fields"]})}},
                         "updates": chunk}
            path = out_dir / f"{name}.json"
            path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            specs.append({"name": name, "path": str(path), "sha256": file_sha(path), "records": len(chunk), "kind": kind})

    emit("create", creates)
    emit("fill", fills)
    report = {"schema": BUILD_SCHEMA, "builtAt": now(), "externalCalls": "read-only fresh scan", "target": dict(target),
              "inputs": {"capture": {"path": str(capture_path), "sha256": capture_sha}, "routes": {"path": str(routes_path), "sha256": routes_sha},
                         "contractV3": {"path": str(v3_path), "sha256": v3_sha}, "contractV9": {"path": str(v9_path), "sha256": v9_sha},
                         "waterline": {"path": str(waterline_path), "sha256": file_sha(waterline_path)}},
              "fresh": {"cloudCaseRows": len(cloud), "cloudDistinctKeys": len([k for k in by_key if k]), "teamRows": len(teams), "routeProblems": route_problems},
              "counts": {"toCreate": len(creates), "toFill": len(fills), "alreadyEqualFields": already_equal, "differingNotOverwritten": len(differing), "blockedEntities": len(blocked)},
              "createdWithInitialDate": sum(1 for c in creates if BASIC_FIELDS["date"] in c["fields"]),
              "createdWithoutAddress": sum(1 for c in creates if BASIC_FIELDS["addr"] not in c["fields"]),
              "specs": specs, "blockedEntities": blocked, "differing": differing, "initialDatesOmitted": dates_omitted,
              "initialDateAcceptanceFallbacks": date_fallbacks, "initialDateConflicts": date_conflicts, "lifecycleFieldsSkipped": lifecycle_skipped,
              "status": "PARTIAL" if blocked or route_problems else "OK"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


DATE_CORRECTION_SCHEMA = "FeishuDateCorrectionPlanV1"


def _manifest_initial_date(root: Path, project: str, waterline_case: Any) -> tuple[tuple[str, str, str] | None, str | None]:
    """本案最近一次本地核验通过的清单里的初查日；只认哈希与总账完成记录相符的清单。返回 ((日, 依据, 核验时点), None) 或 (None, 原因)。

    清单日来自本案自己的监督检查记录文书(local-verify 已核对清单、文件与原包)，不是受理日、创建日或下载日。
    """
    found, why = _verified_manifest(root, project, waterline_case)
    if found is None:
        return None, why
    manifest, relative, recorded, verified_at = found
    day = _text(((manifest.get("initialInspection") or {}).get("inspectionDate")))
    if ISO_DAY.match(day) is None:
        return None, "清单没有有效的初查日"
    ref = f"{relative}#initialInspection.inspectionDate | 本地核验清单 manifestSha256={recorded} | projectNo={project}"
    return (day, ref, verified_at), None


def _pending_human_release(ledger: Ledger | None, base: str, table_id: str, record_id: str, project: str, accepted: frozenset[str] = frozenset()) -> list[str]:
    """Intent ids with a human_release the user never accepted, on this record's update target or its create target.

    A target with such a history keeps its historical late-write risk: the maintenance builders refuse it instead of writing around the gate.
    """
    if ledger is None:
        return []
    found: list[str] = []
    for key in (Ledger.target_key(base, table_id, record_id), Ledger.target_key(base, table_id, "create:" + project)):
        found += [str(e.get("intentId")) for e in ledger.events(key) if e.get("event") == "human_release" and e.get("intentId") not in accepted]
    return sorted(set(found))


def build_date_correction_specs(*, plan_path: Path, plan_sha: str, waterline_path: Path, v3_path: Path, v3_sha: str, v9_path: Path,
                                v9_sha: str, target: dict[str, str], transport: Any, out_dir: Path, log_root: Path,
                                batch_size: int = HARD_CAP_RECORDS, scope_from: str = "2026-01-01", ledger_dir: Path | None = None,
                                isolated_test_ledger: bool = False) -> dict[str, Any]:
    """云端初查日期有据更正说明（V2 spec，旧值门禁）。

    计划逐项给出 projectNo、云端 recordId、此刻应有的旧值 expectedOld 与新日。生成前核对：新日等于本案已核验清单里的初查日、
    不早于范围起点；fresh 云端该键唯一且 recordId 相符、当前值正是 expectedOld；任一不符的条目整条拒绝并列入报告，不写其他条目以外的任何字段。
    执行仍由 feishu-source-sync 的 P18 账、写前旧值核对与写后读回完成；写前云端值已变（含人工改动）会被执行器拒绝。
    """
    _require(1 <= batch_size <= HARD_CAP_RECORDS, f"batch_size 必须是 1..{HARD_CAP_RECORDS}")
    _require(file_sha(plan_path) == plan_sha, "更正计划摘要与固定值不一致")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    _require(isinstance(plan, dict) and plan.get("schemaVersion") == DATE_CORRECTION_SCHEMA, "更正计划版本不支持")
    entries = plan.get("entries")
    _require(isinstance(entries, list) and entries and len({e.get("projectNo") for e in entries}) == len(entries), "更正计划没有条目或含重复项目")
    v3 = load_contract_v3(v3_path, v3_sha)
    contract = load_contract(v9_path, v9_sha)
    case_table = v3["base"]["tables"]["案卷主表"]["tableId"]
    _require(target.get("baseToken") == v3["base"]["baseId"] == contract["baseId"], "目标 Base 与合同不一致")
    index = field_index(v3["base"])
    date_field = BASIC_FIELDS["date"]
    ftype = index[case_table][date_field]["type"]
    waterline = json.loads(waterline_path.read_text(encoding="utf-8")).get("cases", {})
    root = waterline_path.parent
    excluded = initial_scope.excluded_projects_in_root(root)
    source_field = LIFECYCLE_FIELDS["date_source"]
    source_meta = index[case_table].get(source_field)
    cloud = transport.list_rows(target["baseToken"], case_table, [BASIC_FIELDS["key"], date_field] + ([source_field] if source_meta else []))
    by_key: dict[str, list[str]] = {}
    for record_id, row in cloud.items():
        by_key.setdefault(_text(row.get(BASIC_FIELDS["key"])), []).append(record_id)
    date_ledger = Ledger(ledger_dir, isolated_test=isolated_test_ledger) if ledger_dir is not None else None
    if date_ledger is not None:
        date_ledger.validate_root()
    updates: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for entry in entries:
        project = _text(entry.get("projectNo"))
        problems: list[str] = []
        proven, reason = _manifest_initial_date(root, project, waterline.get(project))
        new_day, old_day, record_id = _text(entry.get("newDate")), _text(entry.get("expectedOld")), _text(entry.get("recordId"))
        if project in excluded:
            problems.append("案卷已移出活动集合")
        pending_release = _pending_human_release(date_ledger, target["baseToken"], case_table, record_id, project)
        if pending_release:
            problems.append(f"历史上存在未获用户风险接受的 human_release({pending_release})：保持阻断，不生成说明")
        if proven is None:
            problems.append(f"无已核验的本案清单日期：{reason}")
        elif proven[0] != new_day:
            problems.append(f"清单初查日 {proven[0]} 与计划新日 {new_day} 不一致")
        if ISO_DAY.match(new_day) is None or new_day < scope_from:
            problems.append("新日格式无效或早于范围起点")
        if ISO_DAY.match(old_day) is None or old_day == new_day:
            problems.append("旧值格式无效或与新日相同")
        ids = by_key.get(project, [])
        if ids != [record_id]:
            problems.append(f"fresh 云端稳定键未唯一命中计划 recordId：{ids}")
        else:
            stored = cloud[record_id].get(date_field)
            if cell_is_empty(stored) or not cell_equals(ftype, stored, old_day):
                problems.append(f"云端当前值 {stored!r} 不是计划旧值 {old_day}（同期变化或人工改动，保留）")
        source_old, source_new = _text(entry.get("sourceOld")), _text(entry.get("sourceNew"))
        if bool(source_old) != bool(source_new):
            problems.append("sourceOld 与 sourceNew 必须同时给出")
        elif source_new:  # 日期来源随依据更正(如 底册 -> 监督系统)：同样带旧值门禁，且新值必须是合同选项
            options = (source_meta or {}).get("options")
            if source_meta is None or (options is not None and source_new not in options):
                problems.append("合同没有该初查日期来源选项")
            elif ids == [record_id] and not cell_equals(source_meta["type"], cloud[record_id].get(source_field), wire_value(source_meta["type"], source_old)):
                problems.append(f"云端初查日期来源当前值 {cloud[record_id].get(source_field)!r} 不是计划旧值 {source_old}")
        if problems:
            refused.append({"projectNo": project, "recordId": record_id, "problems": problems})
            continue
        ref = proven[1] + f" | 来源任务列表检查日期对照={_text(plan.get('listingSha256'))} | 计划={plan_path.name}"
        fields = {date_field: {"new": new_day, "expectedOld": old_day, "basis": _record_basis(ref, proven[2])}}
        if source_new:
            fields[source_field] = {"new": source_new, "expectedOld": source_old, "basis": _record_basis(ref, proven[2])}
        updates.append({"tableId": case_table, "recordId": record_id, "stableKey": {"fieldId": BASIC_FIELDS["key"], "value": project}, "fields": fields})
    out_dir.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []
    for number, start in enumerate(range(0, len(updates), batch_size), start=1):
        chunk = updates[start:start + batch_size]
        name = f"datefix-{number:02d}"
        spec = {"mode": "execute", "target": dict(target), "limits": {"maxRecords": batch_size}, "logDir": str(log_root / name),
                "schemaVersion": SPEC_SCHEMA, "contract": {"path": str(v9_path), "sha256": v9_sha},
                "allowlist": {case_table: {"records": [c["recordId"] for c in chunk], "fields": sorted({f for c in chunk for f in c["fields"]})}}, "updates": chunk}
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        specs.append({"name": name, "path": str(path), "sha256": file_sha(path), "records": len(chunk), "kind": "datefix"})
    report = {"schema": "FeishuDateCorrectionBuildReportV1", "builtAt": now(), "externalCalls": "read-only fresh scan", "target": dict(target),
              "plan": {"path": str(plan_path), "sha256": plan_sha}, "counts": {"entries": len(entries), "toCorrect": len(updates), "refused": len(refused)},
              "specs": specs, "refused": refused, "status": "OK" if not refused else "PARTIAL"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


FIELD_PLAN_SCHEMA = "FeishuFieldPlanV1"


def _evidence_ref(root: Path, item: Any) -> tuple[str | None, str | None]:
    """One evidence file of a plan field: exists, sha256 equals the declared one. Returns (text for sourceRef, problem)."""
    if not isinstance(item, dict) or not _text(item.get("path")) or not re.fullmatch(r"(sha256:)?[0-9a-f]{64}", _text(item.get("sha256"))):
        return None, "evidence 需要 path 与 sha256"
    path = Path(_text(item["path"]))
    path = path if path.is_absolute() else root / path
    try:
        actual = file_sha(path)
    except OSError:
        return None, f"证据文件不可读：{item.get('path')}"
    if actual != _text(item["sha256"]).removeprefix("sha256:"):
        return None, f"证据文件摘要与计划不符：{item.get('path')}"
    return f"{item['path']}@sha256={actual}", None


def build_field_plan_specs(*, plan_path: Path, plan_sha: str, waterline_path: Path, contract_path: Path, contract_sha: str, target: dict[str, str],
                           transport: Any, out_dir: Path, log_root: Path, ledger_dir: Path | None = None,
                           batch_size: int = HARD_CAP_RECORDS, isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Evidence-checked V2 specs from a per-record field plan (maintenance of automatic source fields such as 统计归类).

    Every plan field carries the new value, the exact old value expected now, and evidence files whose digests are re-computed here.
    Refused, never partially written: a field the contract does not allow (class/type/option), an unreadable or changed evidence file,
    a stable key that does not match the fresh cloud row, a current value that is not the planned old value (including human edits),
    or a target whose ledger is still blocked. Execution stays with ``feishu-source-sync`` (P18 ledger, old-value guard, read-back).
    """
    _require(1 <= batch_size <= HARD_CAP_RECORDS, f"batch_size 必须是 1..{HARD_CAP_RECORDS}")
    _require(file_sha(plan_path) == plan_sha, "字段计划摘要与固定值不一致")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    _require(isinstance(plan, dict) and plan.get("schemaVersion") == FIELD_PLAN_SCHEMA, "字段计划版本不支持")
    entries = plan.get("entries")
    _require(isinstance(entries, list) and entries, "字段计划没有条目")
    contract = load_contract(contract_path, contract_sha)
    _require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    index = field_index(contract)
    table_ids = {name: t["tableId"] for name, t in contract["tables"].items()}
    root = waterline_path.parent
    ledger = Ledger(ledger_dir, isolated_test=isolated_test_ledger) if ledger_dir is not None else None
    if ledger is not None:
        ledger.validate_root()
    seen: set[tuple[str, str]] = set()
    pending: dict[str, list[dict[str, Any]]] = {}
    refused: list[dict[str, Any]] = []
    unchanged = 0
    for entry in entries:
        problems: list[str] = []
        table_name, record_id = _text(entry.get("table")), _text(entry.get("recordId"))
        table_id = table_ids.get(table_name)
        stable = entry.get("stableKey") if isinstance(entry.get("stableKey"), dict) else {}
        if table_id is None or STABLE_KEYS.get(table_name) is None:
            problems.append("表不在合同或没有稳定键")
        elif stable.get("fieldId") != STABLE_KEYS[table_name] or not _text(stable.get("value")):
            problems.append("稳定键字段/值无效")
        if RECORD_ID.match(record_id) is None or (table_name, record_id) in seen:
            problems.append("recordId 无效或重复")
        seen.add((table_name, record_id))
        fields_in = entry.get("fields") if isinstance(entry.get("fields"), dict) and entry.get("fields") else None
        if fields_in is None:
            problems.append("fields 为空")
        planned: dict[str, dict[str, Any]] = {}
        for fid, item in (fields_in or {}).items():
            meta = index.get(table_id or "", {}).get(fid)
            if not isinstance(item, dict) or meta is None or fid in ALWAYS_DENIED or meta["class"] != WHITELIST or meta["type"] not in SCALAR_TYPES or fid == STABLE_KEYS.get(table_name):
                problems.append(f"字段 {fid} 合同不允许写入（类别/类型/永久禁写）")
                continue
            if "new" not in item or "expectedOld" not in item:
                problems.append(f"字段 {meta['name']} 缺 new 或 expectedOld")
                continue
            if isinstance(item.get("restore"), dict):   # 精确逆操作条目：new 必须为 null，expectedOld 为被恢复的误写值，因果证据由执行器对账本核验
                if item["new"] is not None or not _text(item["expectedOld"]) or not _text(item["restore"].get("intentId")) or ledger is None:
                    problems.append(f"字段 {meta['name']} restore 需要 new=null、expectedOld=误写值、restore.intentId，且构建必须带 --ledger-dir")
                    continue
            elif meta["type"] == "select" and meta["options"] is not None and item["new"] not in meta["options"]:
                problems.append(f"选项不在合同中：{meta['name']}={item['new']!r}")
                continue
            refs = []
            for ev in item.get("evidence") if isinstance(item.get("evidence"), list) and item.get("evidence") else [None]:
                ref, why = _evidence_ref(root, ev)
                if why:
                    problems.append(f"字段 {meta['name']}：{why}")
                else:
                    refs.append(ref)
            if not _text(item.get("basisRef")) or not _text(item.get("observedAt")):
                problems.append(f"字段 {meta['name']} 缺 basisRef/observedAt")
            planned[fid] = {"meta": meta, "item": item, "refs": refs}
        if ledger is not None and table_id is not None and RECORD_ID.match(record_id) and ledger.state(Ledger.target_key(target["baseToken"], table_id, record_id))["state"] == "BLOCKED":
            problems.append("该目标账本仍未决(BLOCKED)，先对账")
        if table_id is not None and RECORD_ID.match(record_id):
            release = _pending_human_release(ledger, target["baseToken"], table_id, record_id, _text(entry.get("projectNo")))
            restore_only = all(isinstance(f, dict) and isinstance(f.get("restore"), dict) for f in (fields_in or {}).values())
            if release and not restore_only:
                problems.append(f"历史上存在未获用户风险接受的 human_release({release})：保持阻断，不生成说明")
        if problems:
            refused.append({"projectNo": _text(entry.get("projectNo")), "table": table_name, "recordId": record_id, "problems": problems})
            continue
        pending.setdefault(table_name, []).append({"entry": entry, "planned": planned, "tableId": table_id})
    updates: dict[str, list[dict[str, Any]]] = {}
    for table_name, items in pending.items():
        ids = [i["entry"]["recordId"] for i in items]
        rows: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 50):
            chunk_ids = ids[start:start + 50]
            try:
                rows.update(transport.get_records(target["baseToken"], items[0]["tableId"], chunk_ids))
            except RuntimeSyncError:   # 块内有读不到的记录：逐条读，读不到的整条拒绝，其余照常
                for record_id in chunk_ids:
                    with contextlib.suppress(RuntimeSyncError):
                        rows.update(transport.get_records(target["baseToken"], items[0]["tableId"], [record_id]))
        for it in items:
            entry, record_id = it["entry"], it["entry"]["recordId"]
            row, problems = rows.get(record_id), []
            if row is None:
                problems.append("fresh 读取没有该记录")
            else:
                key_field = STABLE_KEYS[table_name]
                if not cell_equals(index[it["tableId"]][key_field]["type"], row.get(key_field, "__absent__"), entry["stableKey"]["value"]):
                    problems.append("fresh 稳定键与计划不一致")
            writes: dict[str, Any] = {}
            same = 0
            for fid, pf in it["planned"].items():
                if row is None or problems:
                    break
                meta, item = pf["meta"], pf["item"]
                stored = row.get(fid, "__absent__")
                restore = item.get("restore") if isinstance(item.get("restore"), dict) else None
                if restore is not None:
                    if stored != "__absent__" and cell_is_empty(stored):
                        same += 1   # 已是空值：不写
                        continue
                    if stored == "__absent__" or not cell_equals(meta["type"], stored, wire_value(meta["type"], item["expectedOld"])):
                        problems.append(f"{meta['name']} 当前 {stored!r} 不等于被恢复的误写值 {item['expectedOld']!r}（可能已被他人改动，保留）")
                        break
                    writes[fid] = {"new": None, "expectedOld": item["expectedOld"], "restore": {"intentId": restore["intentId"]},
                                   "basis": _record_basis(f"{_text(item['basisRef'])} | 证据 " + "; ".join(pf["refs"]) + f" | 计划={plan_path.name}", _text(item["observedAt"]))}
                    continue
                if stored != "__absent__" and not cell_is_empty(stored) and cell_equals(meta["type"], stored, wire_value(meta["type"], item["new"])):
                    same += 1   # 已是目标值：不写
                    continue
                old = item["expectedOld"]
                if old is None:
                    if stored == "__absent__" or not cell_is_empty(stored):
                        problems.append(f"{meta['name']} 当前 {stored!r} 不是计划旧值(空)")
                        break
                elif stored == "__absent__" or cell_is_empty(stored) or not cell_equals(meta["type"], stored, wire_value(meta["type"], old)):
                    problems.append(f"{meta['name']} 当前 {stored!r} 不是计划旧值 {old!r}（同期变化或人工改动，保留）")
                    break
                basis = _record_basis(f"{_text(item['basisRef'])} | 证据 " + "; ".join(pf["refs"]) + f" | 计划={plan_path.name}", _text(item["observedAt"]))
                if item.get("explicitNegative") is True:   # 证据明确给出的 0/否(如产品均有结果且无不合格)；缺值不走这里
                    basis["explicitNegative"] = True
                writes[fid] = {"new": item["new"], "expectedOld": old, "basis": basis}
            if problems:
                refused.append({"projectNo": _text(entry.get("projectNo")), "table": table_name, "recordId": record_id, "problems": problems})
            elif writes:
                updates.setdefault(table_name, []).append({"tableId": it["tableId"], "recordId": record_id, "stableKey": entry["stableKey"], "fields": writes})
            else:
                unchanged += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []
    for table_name, table_updates in updates.items():
        for number, start in enumerate(range(0, len(table_updates), batch_size), start=1):
            chunk = table_updates[start:start + batch_size]
            name = f"fp-{table_ids[table_name]}-{number:02d}"
            spec = {"mode": "execute", "target": dict(target), "limits": {"maxRecords": batch_size}, "logDir": str(log_root / name),
                    "schemaVersion": SPEC_SCHEMA, "contract": {"path": str(contract_path), "sha256": contract_sha},
                    "allowlist": {chunk[0]["tableId"]: {"records": [c["recordId"] for c in chunk], "fields": sorted({f for c in chunk for f in c["fields"]})}}, "updates": chunk}
            path = out_dir / f"{name}.json"
            path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            specs.append({"name": name, "path": str(path), "sha256": file_sha(path), "records": len(chunk), "kind": "fieldplan", "table": table_name})
    report = {"schema": "FeishuFieldPlanBuildReportV1", "builtAt": now(), "externalCalls": "read-only fresh read", "target": dict(target),
              "plan": {"path": str(plan_path), "sha256": plan_sha}, "counts": {"entries": len(entries), "toWrite": sum(len(v) for v in updates.values()), "alreadyEqual": unchanged, "refused": len(refused)},
              "specs": specs, "refused": refused, "status": "OK" if not refused else "PARTIAL"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


# ---------------------------------------------------------------------------------------------
# 检查明细/产品明细：由本案已核验清单生成的 V3 子行创建说明
# ---------------------------------------------------------------------------------------------

CHILD_BUILD_SCHEMA = "FeishuChildDetailBuildReportV1"
INSPECT_F = {"key": "fld5ujrPv4", "case": "fld1kH9BIS", "stage": "fld7vkbzoW", "date": "fld70b2uKG", "month": "fld2QLZcf9", "team": "fldNs0IQEj",
             "result": "fld1RPp2yY", "count": "fld1jdSi33", "bad": "fld3LlgGvX", "method": "fld2TGEbpj", "source": "fld5l8RUfn",
             "date_source": "fld3eCB2bR", "round": "fldf4Udwp1", "in_scope": "fld2zAtOpF"}
PRODUCT_F = {"key": "fldqlwHjUW", "inspection": "fldD2pT8R7", "seq": "fld53DA0lX", "name": "fld6QPrwZD", "model": "fld1ah1DAy",
             "producer": "fld1JERqCE", "location": "fld4leGJ9P", "method": "fld4gYH0cP", "result": "fldJUpLw89", "access": "fld532RUHS",
             "problem": "fld2GmMG7w", "source": "fldLCZvMp4", "cert": "fldAN4wz5l"}
BRIGADE_NAMES = {"LIANGXI": "梁溪大队", "BINHU": "滨湖大队", "XINWU": "新吴大队", "XISHAN": "锡山大队", "HUISHAN": "惠山大队", "JIANGYIN": "江阴大队",
                 "YIXING": "宜兴大队", "JINGKAI": "经开大队"}
PRODUCT_METHOD = {"ONSITE": "现场判定", "SAMPLING": "抽样送检"}
PRODUCT_RESULT = {"QUALIFIED": "合格", "UNQUALIFIED": "不合格", "PENDING": "待核实"}
CLASSIFICATION_RESULT = {"QUALIFIED": "合格", "UNQUALIFIED": "不合格"}
CHILD_STAGES = (("initialInspection", "初查", "initial"), ("recheckInspection", "复查", "recheck"))
NATIVE_ID_NOTE = "来源清单未提供原生检查/产品标识，原生标识留空；稳定键由本地按清单位置生成，不冒充来源原生标识"


def _cell_text(cell: Any) -> str:
    if isinstance(cell, list):
        return _text(cell[0]) if cell else ""
    return _text(cell)


def _verified_manifest(root: Path, project: str, waterline_case: Any) -> tuple[tuple[dict[str, Any], str, str, str] | None, str | None]:
    """本案最近一次本地核验通过的清单：只认哈希与总账完成记录相符且是本案的清单。返回 ((清单, 相对路径, 总账摘要, 核验时点), None) 或 (None, 原因)。"""
    if not isinstance(waterline_case, dict) or waterline_case.get("state") != "COMPLETED" or (waterline_case.get("local") or {}).get("status") != "ARCHIVED":
        return None, "案卷未完成本地核验归档"
    completions = ((waterline_case.get("history") or {}).get("completions")) or []
    if not completions:
        return None, "总账没有完成记录"
    last = completions[-1]
    generation, recorded = _text(last.get("generation")), _text(last.get("manifestSha256"))
    relative = Path("工作区") / "历史工作区" / f"{project}-{generation}" / "manifest.json"
    path = root / relative
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None, "清单路径越出业务根"
    if not generation or not path.is_file():
        return None, "清单文件不存在"
    if "sha256:" + file_sha(path) != recorded:
        return None, "清单摘要与总账完成记录不符"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "清单无法读取"
    if not isinstance(manifest, dict) or (manifest.get("case") or {}).get("projectNo") != project:
        return None, "清单不是本案"
    return (manifest, relative.as_posix(), recorded, _text(last.get("verifiedAt")) or now()), None


def _child_inspection(project: str, stage_key: str, stage_name: str, stage_tag: str, inspection: Any, scope_from: str) -> tuple[dict[str, Any] | None, str | None]:
    """清单里一次检查的事实：日、产品逐项(来源值+映射结果)。缺日期/产品/产品名称整次检查保留(不猜)。"""
    if not isinstance(inspection, dict):
        return None, f"{stage_name}检查缺失或格式无效"
    day = _text(inspection.get("inspectionDate"))
    if ISO_DAY.match(day) is None:
        return None, f"{stage_name}检查日期无效或缺失"
    if day < scope_from:
        return None, f"{stage_name}检查日 {day} 早于范围起点 {scope_from}"
    raw_products = inspection.get("products")
    if not isinstance(raw_products, list) or not raw_products or not all(isinstance(p, dict) and _text(p.get("name")) for p in raw_products):
        return None, f"{stage_name}检查没有产品或产品名称缺失"
    products = []
    for number, raw in enumerate(raw_products, start=1):
        method, result = raw.get("method"), raw.get("result")
        if (method is not None and method not in PRODUCT_METHOD) or (result is not None and result not in PRODUCT_RESULT):
            return None, f"{stage_name}产品 {number} 的方式/结果取值不在已知映射内：{method!r}/{result!r}"
        products.append({"seq": number, "key": f"{project}|{stage_name}|{number}", "clientRef": _text(raw.get("clientRef")), "raw": raw,
                         "method": PRODUCT_METHOD.get(method), "result": PRODUCT_RESULT.get(result), "initialProductRef": _text(raw.get("initialProductRef"))})
    return {"stageKey": stage_key, "stage": stage_name, "tag": stage_tag, "day": day, "key": f"{project}|{stage_name}", "products": products}, None


def _inspection_result(products: list[dict[str, Any]], unified: dict[str, Any] | None, initial: bool) -> tuple[str | None, str]:
    """检查级结果。复查：任一产品明确不合格→不合格；全部明确合格→合格；其余→待核实。
    初查：与主行同一规则(unified_initial_result)；证据冲突时不给值(None)，由校准列为待核对；没有终局时为待核实。"""
    if initial and unified is not None:
        if unified["conflict"]:
            return None, unified["conflict"]
        if unified["terminal"]:
            return unified["label"], f"案卷级统一初查结论({unified['source']})"
        return "待核实", "初查结果尚无终局证据"
    results = [p["result"] for p in products]
    if "不合格" in results:
        return "不合格", "产品逐项结果含不合格"
    if all(r == "合格" for r in results):
        return "合格", "产品逐项结果全部合格"
    return "待核实", "产品结果含待结果/缺项"


def product_results(products: list[dict[str, Any]], unified: dict[str, Any] | None, initial: bool) -> list[str | None]:
    """产品逐项结果：清单给了终局就用清单；初查产品清单未给终局时，只有案卷结论能唯一确定才补——
    案卷初查合格即每项合格；案卷初查不合格且只有一个产品即该项不合格；多产品不合格不推断到具体产品。"""
    out = []
    for product in products:
        result = product["result"] if product["result"] in ("合格", "不合格") else None
        if result is None and initial and unified is not None and unified["terminal"]:
            if unified["label"] == "合格" or len(products) == 1:
                result = unified["label"]
        out.append(result or product["result"])
    return out


def build_child_specs(*, waterline_path: Path, v3_path: Path, v3_sha: str, target: dict[str, str], transport: Any, out_dir: Path, log_root: Path,
                      ledger_dir: Path | None, batch_size: int = HARD_CAP_RECORDS, scope_from: str = "2026-01-01",
                      only_projects: frozenset[str] | None = None, isolated_test_ledger: bool = False) -> dict[str, Any]:
    """由本案已核验清单生成 检查明细/产品明细 的 V3 创建说明(先检查后产品，同一案的子行放在同一份说明里)。

    按检查键增量补行：清单里有而云端没有的那次检查(连同它的产品行)才创建，已有同键子行一律不动，所以初查早已在云端、
    复查后来才归档的案卷也能补上复查行；云端已有该次检查但产品行不全的不自动补(列出，交核对)。每个案卷要同时满足：总账完成且清单摘要相符、初查日等于云端主行初查日、
    大队与清单管辖一致、主行无未获用户接受的历史 human_release、不在范围外保留；否则只保留该案并列出原因。
    稳定键 `项目编号|阶段(|序号)` 由本地按清单位置生成，来源未提供原生检查/产品标识，原生标识留空并写入每个字段的 sourceRef。
    """
    _require(1 <= batch_size <= HARD_CAP_RECORDS, f"batch_size 必须是 1..{HARD_CAP_RECORDS}")
    _require(ledger_dir is not None, "生成说明必须沿唯一 P18 账(--ledger-dir)：缺账会跳过历史封锁与放行核查，拒绝")
    ledger = Ledger(ledger_dir, isolated_test=isolated_test_ledger)
    ledger.validate_root()
    v3 = load_contract_v3(v3_path, v3_sha)
    _require(target.get("baseToken") == v3["base"]["baseId"], "目标 Base 与合同不一致")
    tables = v3["base"]["tables"]
    case_table, inspect_table, product_table = (tables[name]["tableId"] for name in ("案卷主表", "检查明细", "产品明细"))
    index = field_index(v3["base"])
    candidates = {c["fieldId"] for c in v3["extension"]["createFieldsCandidate"] if not str(c.get("candidateState", "")).startswith("blocked")}
    needed = [*INSPECT_F.values(), *PRODUCT_F.values()]
    missing = [fid for fid in needed if fid not in candidates]
    _require(not missing, f"合同没有放行这些子行字段的创建：{missing}")
    root = waterline_path.parent
    waterline = json.loads(waterline_path.read_text(encoding="utf-8")).get("cases", {})
    excluded = initial_scope.excluded_projects_in_root(root)
    base = target["baseToken"]
    # fresh cloud: 主行(键/大队/初查日/初查结果)、已有子行键
    main_cols = [BASIC_FIELDS["key"], BASIC_FIELDS["team"], BASIC_FIELDS["date"], LIFECYCLE_FIELDS["result"]]
    main_rows = transport.list_rows(base, case_table, main_cols)
    inspect_rows = transport.list_rows(base, inspect_table, [INSPECT_F["key"]])
    product_rows = transport.list_rows(base, product_table, [PRODUCT_F["key"]])
    by_project: dict[str, list[str]] = {}
    for record_id, row in main_rows.items():
        by_project.setdefault(_cell_text(row.get(BASIC_FIELDS["key"])), []).append(record_id)
    inspect_keys = {_cell_text(r.get(INSPECT_F["key"])): rid for rid, r in inspect_rows.items()}
    product_keys = {_cell_text(r.get(PRODUCT_F["key"])) for r in product_rows.values()}
    date_type = index[case_table][BASIC_FIELDS["date"]]["type"]
    blocked: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    already = 0
    for project in sorted(p for p in by_project if p):
        if only_projects is not None and project not in only_projects:
            continue
        ids = by_project[project]
        reasons: list[str] = []
        if len(ids) != 1:
            blocked.append({"projectNo": project, "reasons": [f"云端主行不唯一：{ids}"]})
            continue
        record_id, row = ids[0], main_rows[ids[0]]
        release = _pending_human_release(ledger, base, case_table, record_id, project)
        if release:
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": [f"历史上存在未获用户风险接受的 human_release({release})：保持保护，不新增子行"]})
            continue
        if project in excluded:
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": ["案卷在范围外保留"]})
            continue
        found, why = _verified_manifest(root, project, waterline.get(project))
        if found is None:
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": [why or "没有可用的已核验清单"]})
            continue
        manifest, relative, recorded, verified_at = found
        facts = [_child_inspection(project, key, name, tag, manifest.get(key), scope_from) for key, name, tag in CHILD_STAGES if key == "initialInspection" or manifest.get(key) is not None]
        inspections = [f for f, _ in facts if f is not None]
        reasons += [w for f, w in facts if f is None and w]
        if not inspections or inspections[0]["stageKey"] != "initialInspection":
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": reasons or ["清单没有可用的初查"]})
            continue
        initial = inspections[0]
        wanted, unpaired = [], []
        for inspection in inspections:
            has_products = any(k.startswith(inspection["key"] + "|") for k in product_keys)
            if inspection["key"] not in inspect_keys and not has_products:
                wanted.append(inspection["key"])
            elif inspection["key"] not in inspect_keys or not has_products:
                unpaired.append(f"{inspection['key']} 云端检查行与产品行不成套(只有其一)：不自动补，交核对")
        if not wanted and not unpaired:
            already += 1   # 清单里的检查云端都已有：不动(清单另有读不出的检查时由校准对账列出)
            continue
        if unpaired:
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": unpaired + reasons})
            continue
        stored_date = row.get(BASIC_FIELDS["date"])
        if cell_is_empty(stored_date) or not cell_equals(date_type, stored_date, initial["day"]):
            reasons.append(f"云端主行初查日 {stored_date!r} 不等于清单初查日 {initial['day']}")
        code = _text((manifest.get("case") or {}).get("brigadeCode"))
        cloud_team = _cell_text(row.get(BASIC_FIELDS["team"]))
        team = BRIGADE_NAMES.get(code)
        if team is None and not cloud_team:
            team = None   # 水上等不在八队名录的管辖：主行也不写所属大队，子行同样留空
        elif team is None or team != cloud_team:
            reasons.append(f"清单管辖 {code!r} 与云端所属大队 {cloud_team!r} 不一致")
        if len(inspections) > 1 and inspections[1]["day"] < initial["day"]:
            reasons.append("复查日早于初查日")
        if reasons:
            blocked.append({"projectNo": project, "recordId": record_id, "reasons": reasons})
            continue
        cases.append({"project": project, "recordId": record_id, "manifest": manifest, "relative": relative, "recorded": recorded, "verifiedAt": verified_at,
                      "unified": unified_initial_result(root, project, waterline.get(project), jurisdiction=None, manifest_found=found),
                      "inspections": inspections, "wanted": set(wanted), "team": team, "cloudResult": _cell_text(row.get(LIFECYCLE_FIELDS["result"]))})
    # 生成创建项
    creates_by_case: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    source_counts: dict[str, int] = {}
    product_total = inspection_total = 0
    for case in cases:
        project, manifest, relative, recorded = case["project"], case["manifest"], case["relative"], case["recorded"]
        items: list[dict[str, Any]] = []
        case_info: dict[str, Any] = {"projectNo": project, "inspections": [], "products": 0}

        def basis(path: str, how: str, *, negative: bool = False) -> dict[str, Any]:
            ref = (f"{relative}#{path} | 本地核验清单 manifestSha256={recorded} | projectNo={project} | {how} | {NATIVE_ID_NOTE}")
            value = _record_basis(ref, case["verifiedAt"])
            if negative:
                value["explicitNegative"] = True
            return value

        for number, inspection in enumerate(case["inspections"]):
            if inspection["key"] not in case["wanted"]:
                continue
            is_initial = inspection["tag"] == "initial"
            products = [dict(p, result=r) for p, r in zip(inspection["products"], product_results(inspection["products"], case["unified"], is_initial))]
            stage_path = inspection["stageKey"]
            result, why = _inspection_result(products, case["unified"], is_initial)
            if is_initial and (result is None or (case["cloudResult"] in CLASSIFICATION_RESULT.values() and result != case["cloudResult"])):
                notes.append({"projectNo": project, "kind": "初查结果证据冲突或与主行不同(双方原值保留，交核对)", "cloud": case["cloudResult"], "children": result, "why": why})
            methods = {p["method"] for p in products if p["method"]}
            all_methods = all(p["method"] for p in products)
            bad_known = all(p["result"] in ("合格", "不合格") for p in products)   # 有待结果/缺项时不断言不合格产品数
            fields: dict[str, Any] = {
                INSPECT_F["key"]: {"new": inspection["key"], "basis": basis(stage_path, f"检查键={inspection['key']}")},
                INSPECT_F["case"]: {"new": case["recordId"], "basis": basis(stage_path, "父案卷为云端主行(项目编号精确匹配)")},
                INSPECT_F["stage"]: {"new": inspection["stage"], "basis": basis(f"{stage_path}.stage", "清单检查阶段")},
                INSPECT_F["date"]: {"new": inspection["day"], "basis": basis(f"{stage_path}.inspectionDate", "清单检查日")},
                INSPECT_F["month"]: {"new": inspection["day"][:7], "basis": basis(f"{stage_path}.inspectionDate", "由清单检查日取年月")},
                INSPECT_F["count"]: {"new": len(products), "basis": basis(f"{stage_path}.products", "清单产品条数")},
                INSPECT_F["source"]: {"new": "来源详情", "basis": basis(stage_path, "来源=本案已核验清单(来源详情)")},
                INSPECT_F["date_source"]: {"new": "监督系统",
                                           "basis": basis(f"{stage_path}.inspectionDate", "检查日是监督系统事实，由本案已核验清单(本地稳定证据)代表；清单文件与摘要见本依据")},
                INSPECT_F["in_scope"]: {"new": "是", "basis": basis(f"{stage_path}.inspectionDate", f"检查日 {inspection['day']} 在范围起点 {scope_from} 及以后")},
            }
            if result is not None:
                fields[INSPECT_F["result"]] = {"new": result, "basis": basis(f"{stage_path}.products[*].result", why)}
            if case["team"] is not None:
                fields[INSPECT_F["team"]] = {"new": case["team"], "basis": basis("case.brigadeCode", f"清单管辖 {_text(manifest['case'].get('brigadeCode'))} 与云端主行所属大队一致")}
            if methods and all_methods:
                label = "抽样送检、现场判定" if len(methods) == 2 else next(iter(methods))
                fields[INSPECT_F["method"]] = {"new": label, "basis": basis(f"{stage_path}.products[*].method", "按产品检查方式汇总")}
            if bad_known:
                bad = sum(1 for p in products if p["result"] == "不合格")
                fields[INSPECT_F["bad"]] = {"new": bad, "basis": basis(f"{stage_path}.products[*].result", "产品均有明确结果，按不合格产品计数", negative=(bad == 0))}
            if inspection["tag"] == "recheck":
                fields[INSPECT_F["round"]] = {"new": 1, "basis": basis(stage_path, "本案第 1 次复查")}
            for fid in fields:
                source_counts[fid] = source_counts.get(fid, 0) + 1
            items.append({"tableId": inspect_table, "stableKey": {"fieldId": INSPECT_F["key"], "value": inspection["key"]}, "fields": fields,
                          "parent": {"recordId": case["recordId"], "stableKey": {"fieldId": BASIC_FIELDS["key"], "value": project}}})
            info = {"key": inspection["key"], "date": inspection["day"], "products": len(products), "result": result}
            for product in products:
                raw, path = product["raw"], f"{stage_path}.products[{product['seq'] - 1}]"
                pfields: dict[str, Any] = {
                    PRODUCT_F["key"]: {"new": product["key"], "basis": basis(path, f"产品键={product['key']}(按清单产品顺序编号；清单本地引用 {product['clientRef'] or '无'})")},
                    PRODUCT_F["inspection"]: {"new": "recPLACEHOLDER1", "basis": basis(path, f"父检查为同案同阶段 {inspection['key']}")},
                    PRODUCT_F["seq"]: {"new": product["seq"], "basis": basis(path, "清单产品顺序")},
                    PRODUCT_F["name"]: {"new": _text(raw.get("name")), "basis": basis(f"{path}.name", "清单产品名称")},
                    PRODUCT_F["source"]: {"new": "来源详情", "basis": basis(path, "来源=本案已核验清单(来源详情)")},
                }
                optional = (("model", "modelSpec", "规格型号"), ("producer", "nominalProducer", "标称生产者"), ("location", "location", "所在部位"), ("problem", "problemDescription", "问题描述"))
                for key_name, raw_name, label in optional:
                    if _text(raw.get(raw_name)):
                        pfields[PRODUCT_F[key_name]] = {"new": _text(raw[raw_name]), "basis": basis(f"{path}.{raw_name}", f"清单{label}")}
                if product["method"]:
                    pfields[PRODUCT_F["method"]] = {"new": product["method"], "basis": basis(f"{path}.method", "清单检查方式")}
                if product["result"]:
                    pfields[PRODUCT_F["result"]] = {"new": product["result"], "basis": basis(f"{path}.result", "清单产品结果")}
                cert = "；".join(x for x in (_text(raw.get("certificateNumberRaw")), _text(raw.get("qualityCheckRaw")), _text(raw.get("marketAccessRaw"))) if x)
                if cert:
                    pfields[PRODUCT_F["cert"]] = {"new": cert, "basis": basis(path, "清单证书编号/核查原文原样保留")}
                for fid in pfields:
                    source_counts[fid] = source_counts.get(fid, 0) + 1
                items.append({"tableId": product_table, "stableKey": {"fieldId": PRODUCT_F["key"], "value": product["key"]}, "fields": pfields,
                              "parent": {"createRef": inspection["key"], "stableKey": {"fieldId": INSPECT_F["key"], "value": inspection["key"]}}})
                case_info["products"] += 1
                product_total += 1
                if inspection["tag"] == "recheck":
                    ref = product["initialProductRef"]
                    initial_refs = {p["clientRef"]: p["key"] for p in case["inspections"][0]["products"] if p["clientRef"]}
                    case_info.setdefault("recheckRelations", []).append(
                        {"recheckProduct": product["key"], "initialProductRef": ref or None,
                         "relation": "proven-by-initialProductRef" if ref and ref in initial_refs else "unproven-no-matching-initialProductRef",
                         "initialProduct": initial_refs.get(ref)})
            case_info["inspections"].append(info)
            inspection_total += 1
        creates_by_case.append((project, items, case_info))
    # 分批：同一案的子行不拆开
    out_dir.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    batch_cases: list[str] = []

    def flush() -> None:
        if not batch:
            return
        name = f"child-{len(specs) + 1:03d}"
        spec = {"schemaVersion": SPEC_SCHEMA_V3, "mode": "execute", "contract": {"path": str(v3_path), "sha256": v3_sha}, "target": dict(target),
                "limits": {"maxRecords": batch_size}, "logDir": str(log_root / name), "creates": list(batch)}
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        specs.append({"name": name, "path": str(path), "sha256": file_sha(path), "records": len(batch), "cases": list(batch_cases), "kind": "child-create"})
        batch.clear()
        batch_cases.clear()

    for project, items, _info in creates_by_case:
        if len(items) > batch_size:
            blocked.append({"projectNo": project, "reasons": [f"该案子行 {len(items)} 条超过单份说明上限 {batch_size}"]})
            continue
        if len(batch) + len(items) > batch_size:
            flush()
        batch.extend(items)
        batch_cases.append(project)
    flush()
    built = {c for s in specs for c in s["cases"]}
    report = {"schema": CHILD_BUILD_SCHEMA, "builtAt": now(), "externalCalls": "read-only fresh scan", "target": dict(target),
              "inputs": {"contractV3": {"path": str(v3_path), "sha256": v3_sha}, "waterline": {"path": str(waterline_path), "sha256": file_sha(waterline_path)}},
              "fresh": {"cloudCaseRows": len(main_rows), "cloudInspectionRows": len(inspect_rows), "cloudProductRows": len(product_rows), "casesAlreadyWithChildren": already},
              "counts": {"cases": len(built), "inspections": sum(len(i["inspections"]) for p, _, i in creates_by_case if p in built),
                         "products": sum(i["products"] for p, _, i in creates_by_case if p in built), "specs": len(specs), "blocked": len(blocked)},
              "nativeIdentity": NATIVE_ID_NOTE, "fieldWrites": source_counts, "notes": notes,
              "cases": [info for p, _, info in creates_by_case if p in built], "specs": specs, "blockedEntities": blocked,
              "status": "PARTIAL" if blocked else "OK"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def run(spec_path: Path, *, execute_flag: bool = False, reconcile_flag: bool = False, cli: str = DEFAULT_CLI,
        transport: Transport | None = None, ledger_dir: Path | None = None, user_risk_acceptance: Path | None = None,
        isolated_test_ledger: bool = False) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    _require(isinstance(spec, dict) and isinstance(spec.get("contract"), dict), "spec 缺少 contract")
    require_user_acceptance_for_release(spec, user_risk_acceptance)
    if spec.get("schemaVersion") == SPEC_SCHEMA_V3:
        v3 = load_contract_v3(Path(spec["contract"].get("path", "")), str(spec["contract"].get("sha256", "")))
        checked = validate_create_spec(spec, v3)
        _require(spec["mode"] == "plan" or ledger_dir is not None, f"{spec['mode']} 必须显式给出 --ledger-dir")
        ledger = Ledger(ledger_dir or DEFAULT_LEDGER, isolated_test=isolated_test_ledger)
        if spec["mode"] == "plan":
            return plan_creates(spec, checked, ledger)
        flag, name = (execute_flag, "--execute") if spec["mode"] == "execute" else (reconcile_flag, "--reconcile")
        _require(flag, f"{spec['mode']} 模式必须同时带 {name}")
        ledger.validate_root()
        use = transport or LarkCliTransport(cli, spec["target"]["profile"], spec["target"]["identity"])
        return execute_creates(spec, checked, use, ledger) if spec["mode"] == "execute" else reconcile_creates(spec, checked, use, ledger)
    contract = load_contract(Path(spec["contract"].get("path", "")), str(spec["contract"].get("sha256", "")), spec["contract"].get("section"))
    checked = validate_spec(spec, contract)
    _require(spec["mode"] == "plan" or ledger_dir is not None, f"{spec['mode']} 必须显式给出 --ledger-dir(无默认账本，避免写到别处绕过封锁)")
    ledger = Ledger(ledger_dir or DEFAULT_LEDGER, isolated_test=isolated_test_ledger)
    if spec["mode"] == "plan":
        blocks = _all_blocks(ledger, spec, checked)
        return {"schema": REPORT_SCHEMA, "mode": "plan", "externalCalls": 0, "requests": build_requests(checked),
                "denylist": sorted(ALWAYS_DENIED), "contractSha256": spec["contract"]["sha256"],
                "ledgerDir": str(ledger.root), "ledgerBlocks": blocks, "executable": not blocks}
    flag, name = (execute_flag, "--execute") if spec["mode"] == "execute" else (reconcile_flag, "--reconcile")
    _require(flag, f"{spec['mode']} 模式必须同时带 {name}")
    ledger.validate_root()
    use = transport or LarkCliTransport(cli, spec["target"]["profile"], spec["target"]["identity"])
    if spec["mode"] == "execute":
        return execute(spec, checked, use, ledger)
    return reconcile(spec, checked, use, ledger)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0104 来源字段白名单增量同步（官方 lark-cli）")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--execute", action="store_true", help="真实云写；缺省只计划")
    parser.add_argument("--reconcile", action="store_true", help="只读云回读，对账早先未决写入")
    parser.add_argument("--cli", default=DEFAULT_CLI)
    parser.add_argument("--ledger-dir", help="追加式账本目录；execute/reconcile 必填；按精确目标建档，与 logDir 无关")
    parser.add_argument("--user-risk-acceptance", help="UserRiskAcceptanceV1：用户对精确意图接受迟到写风险的记录；spec 带 humanRelease 时必填，助手不得自署")
    args = parser.parse_args(argv)
    try:
        result = run(Path(args.spec), execute_flag=args.execute, reconcile_flag=args.reconcile, cli=args.cli, ledger_dir=Path(args.ledger_dir) if args.ledger_dir else None,
                     user_risk_acceptance=Path(args.user_risk_acceptance) if args.user_risk_acceptance else None)
    except SyncError as error:
        print(json.dumps({"status": "REFUSED", "externalCalls": 0, "reason": str(error)}, ensure_ascii=False))
        return 2
    except RuntimeSyncError as error:
        print(json.dumps({"status": "FAILED_OR_UNKNOWN", "reason": str(error)}, ensure_ascii=False))
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 4 if result.get("status") == "PARTIAL" else 0  # PARTIAL 有被挡实体：不得以 exit 0 当作整体完成


if __name__ == "__main__":
    sys.exit(main())
