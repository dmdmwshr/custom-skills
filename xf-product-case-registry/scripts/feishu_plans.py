"""Planners for the two non-source write bases (2026-11-12 mechanism): label default and user-delegated entry.

Both only read the cloud and emit ordinary V2 specs; the write itself stays with ``feishu-source-sync``
(formal ledger, old-value guard, intent before write, read-back). Field ownership is decided by
``field_policy`` from the contract class, so nothing here can reach a supervision fact.

- ``build_label_initial_specs``: every ``business-label`` cell that is empty or still holds the retired
  placeholder "待核实" becomes "否"; explicit 是/否 is left alone. The previous values are saved first.
- ``build_delegated_specs``: values the user asked the assistant to record (enterprise list, online-sale
  list, a one-off migration). Records are matched by stable key; a current value that differs from what the
  plan expected is reported as a conflict and not written.

Standard library only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from . import feishu_source_sync as sync
    from . import field_policy
else:
    import feishu_source_sync as sync
    import field_policy

DELEGATED_PLAN_SCHEMA = "FeishuDelegatedPlanV1"
LABEL_REPORT_SCHEMA = "FeishuLabelInitialBuildReportV1"
DELEGATED_REPORT_SCHEMA = "FeishuDelegatedBuildReportV1"


def _contract(contract_path: Path, contract_sha: str, section: str | None, target: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = sync.load_contract(contract_path, contract_sha, section)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    return contract, sync.field_index(contract)


def _ledger(ledger_dir: Path | None, isolated: bool) -> sync.Ledger:
    sync._require(ledger_dir is not None, "生成说明必须沿唯一正式账(--ledger-dir)：缺账会跳过历史封锁，拒绝")
    ledger = sync.Ledger(ledger_dir, isolated_test=isolated)
    ledger.validate_root()
    return ledger


def _emit(*, prefix: str, table_id: str, updates: list[dict[str, Any]], batch_size: int, target: dict[str, str], contract_path: Path,
          contract_sha: str, section: str | None, out_dir: Path, log_root: Path, kind: str, table_name: str) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = []
    for number, start in enumerate(range(0, len(updates), batch_size), start=1):
        chunk = updates[start:start + batch_size]
        name = f"{prefix}-{table_id}-{number:03d}"
        contract_ref: dict[str, Any] = {"path": str(contract_path), "sha256": contract_sha}
        if section:
            contract_ref["section"] = section
        spec = {"mode": "execute", "target": dict(target), "limits": {"maxRecords": batch_size}, "logDir": str(log_root / name),
                "schemaVersion": sync.SPEC_SCHEMA, "contract": contract_ref,
                "allowlist": {table_id: {"records": [c["recordId"] for c in chunk], "fields": sorted({f for c in chunk for f in c["fields"]})}},
                "updates": chunk}
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        specs.append({"name": name, "path": str(path), "sha256": sync.file_sha(path), "records": len(chunk), "kind": kind, "table": table_name})
    return specs


def _plain(field_type: str, cell: Any) -> Any:
    """The stored cell as the plain value a spec carries (select -> its single option)."""
    if sync.cell_is_empty(cell):
        return None
    try:
        value = sync.norm_cell(field_type, cell)
    except sync.Ambiguous:
        return cell
    if field_type == "datetime":
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if field_type == "number" and float(value).is_integer():
        return int(value)
    return value


def build_label_initial_specs(*, contract_path: Path, contract_sha: str, table_name: str, target: dict[str, str], transport: Any,
                              out_dir: Path, log_root: Path, ledger_dir: Path | None, source_ref: str, section: str | None = None,
                              batch_size: int = sync.HARD_CAP_RECORDS, only_keys: frozenset[str] | None = None,
                              isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Specs that set every unidentified label of one table to the rule default "否"."""
    sync._require(1 <= batch_size <= sync.HARD_CAP_RECORDS, f"batch_size 必须是 1..{sync.HARD_CAP_RECORDS}")
    sync._require(bool(source_ref.strip()), "需要 source_ref（规则依据）")
    contract, index = _contract(contract_path, contract_sha, section, target)
    sync._require(table_name in contract["tables"] and table_name in sync.STABLE_KEYS, f"表不在合同或没有稳定键：{table_name}")
    table_id = contract["tables"][table_name]["tableId"]
    key_field = sync.STABLE_KEYS[table_name]
    labels = {fid: meta for fid, meta in index[table_id].items() if meta["class"] == field_policy.CLASS_LABEL}
    sync._require(bool(labels), f"合同里该表没有 {field_policy.CLASS_LABEL} 字段：{table_name}")
    for meta in labels.values():
        sync._require(meta["type"] == "select" and field_policy.LABEL_DEFAULT in (meta["options"] or []), f"标签字段必须是含“否”的单选：{meta['name']}")
    ledger = _ledger(ledger_dir, isolated_test_ledger)
    rows = transport.list_rows(target["baseToken"], table_id, [key_field, *labels])
    observed_at = sync.now()
    updates: list[dict[str, Any]] = []
    previous: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    counts = {"rows": len(rows), "explicit": 0, "empty": 0, "placeholder": 0}
    for record_id, row in sorted(rows.items()):
        key = row.get(key_field)
        if not isinstance(key, str) or not key or (only_keys is not None and key not in only_keys):
            if not isinstance(key, str) or not key:
                held.append({"recordId": record_id, "problem": "没有稳定键，不写"})
            continue
        fields: dict[str, Any] = {}
        for fid, meta in labels.items():
            old = _plain("select", row.get(fid))
            if old in field_policy.LABEL_VALUES:
                counts["explicit"] += 1
                continue
            if old not in field_policy.LABEL_REPLACEABLE:
                held.append({"recordId": record_id, "stableKey": key, "field": meta["name"], "current": old, "problem": "不是空白/待核实/是/否，保留原值"})
                continue
            counts["empty" if old is None else "placeholder"] += 1
            previous.append({"recordId": record_id, "stableKey": key, "fieldId": fid, "field": meta["name"], "old": old})
            fields[fid] = {"new": field_policy.LABEL_DEFAULT, "expectedOld": old,
                           "basis": {"kind": field_policy.BASIS_LABEL_INITIAL, "sourceRef": source_ref, "observedAt": observed_at}}
        if not fields:
            continue
        if ledger.state(sync.Ledger.target_key(target["baseToken"], table_id, record_id))["state"] == "BLOCKED":
            held.append({"recordId": record_id, "stableKey": key, "problem": "该目标账本仍未决(BLOCKED)，先对账"})
            continue
        updates.append({"tableId": table_id, "recordId": record_id, "stableKey": {"fieldId": key_field, "value": key}, "fields": fields})
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_path = out_dir / "previous-values.json"
    previous_path.write_text(json.dumps({"table": table_name, "tableId": table_id, "readAt": observed_at, "values": previous},
                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    specs = _emit(prefix="li", table_id=table_id, updates=updates, batch_size=batch_size, target=target, contract_path=contract_path,
                  contract_sha=contract_sha, section=section, out_dir=out_dir, log_root=log_root, kind="label-initial", table_name=table_name)
    report = {"schema": LABEL_REPORT_SCHEMA, "builtAt": observed_at, "externalCalls": "read-only fresh read", "target": dict(target),
              "table": table_name, "labels": sorted(m["name"] for m in labels.values()),
              "counts": {**counts, "recordsToWrite": len(updates), "held": len(held)},
              "previousValues": {"path": str(previous_path), "sha256": sync.file_sha(previous_path)},
              "specs": specs, "held": held, "status": "OK" if not held else "PARTIAL"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def build_delegated_specs(*, plan_path: Path, plan_sha: str, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any,
                          out_dir: Path, log_root: Path, ledger_dir: Path | None, batch_size: int = sync.HARD_CAP_RECORDS,
                          isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Specs for values the user asked to have recorded.

    Plan (``FeishuDelegatedPlanV1``): ``{authorizedBy:"user", userQuote, sourceRef, section?, table,
    entries:[{stableKey, recordId?, fields:{fieldId|name: {new, expectedOld?}}}]}``. Without ``expectedOld``
    the value read now is the guard between this build and the write; with it, a different current value
    is a conflict that is reported and left untouched.
    """
    sync._require(1 <= batch_size <= sync.HARD_CAP_RECORDS, f"batch_size 必须是 1..{sync.HARD_CAP_RECORDS}")
    sync._require(sync.file_sha(plan_path) == plan_sha, "代录计划摘要与固定值不一致")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sync._require(isinstance(plan, dict) and plan.get("schemaVersion") == DELEGATED_PLAN_SCHEMA, "代录计划版本不支持")
    sync._require(plan.get("authorizedBy") == "user" and sync._text(plan.get("userQuote")) and sync._text(plan.get("sourceRef")),
                  "代录计划需要 authorizedBy=user、用户原话 userQuote 与 sourceRef")
    entries = plan.get("entries")
    sync._require(isinstance(entries, list) and entries, "代录计划没有条目")
    section = plan.get("section") or None
    table_name = sync._text(plan.get("table"))
    contract, index = _contract(contract_path, contract_sha, section, target)
    sync._require(table_name in contract["tables"] and table_name in sync.STABLE_KEYS, f"表不在合同或没有稳定键：{table_name}")
    table_id = contract["tables"][table_name]["tableId"]
    key_field = sync.STABLE_KEYS[table_name]
    by_name = {meta["name"]: fid for fid, meta in index[table_id].items()}
    ledger = _ledger(ledger_dir, isolated_test_ledger)
    wanted: set[str] = set()
    for entry in entries:
        for ref in (entry.get("fields") or {}) if isinstance(entry, dict) else {}:
            wanted.add(by_name.get(ref, ref))
    known = [fid for fid in sorted(wanted) if fid in index[table_id]]
    rows = transport.list_rows(target["baseToken"], table_id, [key_field, *known])
    by_key: dict[str, list[str]] = {}
    for record_id, row in rows.items():
        if isinstance(row.get(key_field), str) and row[key_field]:
            by_key.setdefault(row[key_field], []).append(record_id)
    observed_at = sync.now()
    updates: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unchanged = 0
    seen: set[str] = set()
    for entry in entries:
        key = sync._text(entry.get("stableKey")) if isinstance(entry, dict) else ""
        problems: list[str] = []
        matches = by_key.get(key, [])
        if not key or key in seen:
            problems.append("稳定键为空或重复")
        elif len(matches) != 1:
            problems.append(f"稳定键在云端匹配 {len(matches)} 条（需恰好 1 条）")
        elif entry.get("recordId") not in (None, matches[0]):
            problems.append("recordId 与稳定键匹配到的记录不一致")
        seen.add(key)
        fields_in = entry.get("fields") if isinstance(entry.get("fields"), dict) and entry.get("fields") else None
        if fields_in is None:
            problems.append("fields 为空")
        if problems:
            refused.append({"stableKey": key, "problems": problems})
            continue
        record_id, row = matches[0], rows[matches[0]]
        writes: dict[str, Any] = {}
        entry_conflicts: list[dict[str, Any]] = []
        for ref, item in fields_in.items():
            fid = by_name.get(ref, ref)
            meta = index[table_id].get(fid)
            if meta is None or not isinstance(item, dict) or "new" not in item:
                problems.append(f"字段不在合同或缺 new：{ref}")
                continue
            basis = {"kind": field_policy.BASIS_USER, "authorizedBy": "user", "userQuote": plan["userQuote"],
                     "sourceRef": f"{plan['sourceRef']} | 计划={plan_path.name}", "observedAt": observed_at}
            current = _plain(meta["type"], row.get(fid))
            if current == item["new"]:
                unchanged += 1
                continue
            if "expectedOld" in item and item["expectedOld"] != current:
                entry_conflicts.append({"stableKey": key, "recordId": record_id, "field": meta["name"], "planned": item["new"],
                                        "expectedOld": item["expectedOld"], "current": current, "action": "REPORT_ONLY"})
                continue
            candidate = {"new": item["new"], "expectedOld": current, "basis": basis}
            problem = field_policy.write_problem(meta, basis, candidate)
            if problem or meta["type"] not in sync.SCALAR_TYPES or fid == key_field:
                problems.append(problem or f"字段类型或稳定键不可写：{meta['name']}")
                continue
            if meta["type"] == "select" and meta["options"] is not None and item["new"] not in meta["options"]:
                problems.append(f"选项不在合同中：{meta['name']}={item['new']!r}")
                continue
            writes[fid] = candidate
        if writes and ledger.state(sync.Ledger.target_key(target["baseToken"], table_id, record_id))["state"] == "BLOCKED":
            problems.append("该目标账本仍未决(BLOCKED)，先对账")
        conflicts.extend(entry_conflicts)
        if problems:
            refused.append({"stableKey": key, "recordId": record_id, "problems": problems})
        elif writes:
            updates.append({"tableId": table_id, "recordId": record_id, "stableKey": {"fieldId": key_field, "value": key}, "fields": writes})
    specs = _emit(prefix="ud", table_id=table_id, updates=updates, batch_size=batch_size, target=target, contract_path=contract_path,
                  contract_sha=contract_sha, section=section, out_dir=out_dir, log_root=log_root, kind="user-delegated", table_name=table_name)
    report = {"schema": DELEGATED_REPORT_SCHEMA, "builtAt": observed_at, "externalCalls": "read-only fresh read", "target": dict(target),
              "plan": {"path": str(plan_path), "sha256": plan_sha}, "table": table_name,
              "counts": {"entries": len(entries), "recordsToWrite": len(updates), "alreadyEqualFields": unchanged,
                         "conflicts": len(conflicts), "refused": len(refused)},
              "specs": specs, "conflicts": conflicts, "refused": refused, "status": "OK" if not refused and not conflicts else "PARTIAL"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


EVIDENCE_PLAN_SCHEMA = "FeishuLabelEvidencePlanV1"
EVIDENCE_REPORT_SCHEMA = "FeishuLabelEvidenceBuildV1"
_NEVER = object()


def last_assistant_value(ledger: sync.Ledger, key: str, field_id: str) -> Any:
    """The value this tool last wrote into the field and saw confirmed, or ``_NEVER``.

    A cell that still holds exactly this value has not been changed by anyone since; any other
    explicit value was put there by a person and stays.
    """
    intents: dict[str, Any] = {}
    last: Any = _NEVER
    for event in ledger.events(key):
        if event["event"] == "intent":
            intents[event["intentId"]] = event
            continue
        intent = intents.get(event.get("intentId"))
        if intent is None or field_id not in intent["fields"]:
            continue
        confirmed = (event["event"] == "final" and event.get("status") in ("OK", "UNCHANGED")) or \
                    (event["event"] == "reconciled" and event.get("outcome") == "APPLIED")
        if confirmed:
            wire = intent["fields"][field_id].get("wire")
            last = wire[0] if isinstance(wire, list) and len(wire) == 1 else wire
    return last


def build_label_evidence_specs(*, plan_path: Path, plan_sha: str, contract_path: Path, contract_sha: str, target: dict[str, str],
                               transport: Any, out_dir: Path, log_root: Path, ledger_dir: Path | None,
                               batch_size: int = sync.HARD_CAP_RECORDS, isolated_test_ledger: bool = False) -> dict[str, Any]:
    """Specs that set a label from verified local evidence without ever overriding a brigade's own value.

    Plan (``FeishuLabelEvidencePlanV1``): ``{sourceRef, section?, table, entries:[{stableKey,
    fields:{fieldId|name: {new: 是|否, evidenceRef, evidenceSha256}}}]}``. Empty and "待核实" cells are
    filled; an explicit different value is replaced only when the ledger shows the assistant wrote it
    last; everything else is listed as a difference for the brigade and left alone.
    """
    sync._require(1 <= batch_size <= sync.HARD_CAP_RECORDS, f"batch_size 必须是 1..{sync.HARD_CAP_RECORDS}")
    sync._require(sync.file_sha(plan_path) == plan_sha, "证据计划摘要与固定值不一致")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sync._require(isinstance(plan, dict) and plan.get("schemaVersion") == EVIDENCE_PLAN_SCHEMA, "证据计划版本不支持")
    entries = plan.get("entries")
    sync._require(sync._text(plan.get("sourceRef")) and isinstance(entries, list) and entries, "证据计划需要 sourceRef 与条目")
    section = plan.get("section") or None
    table_name = sync._text(plan.get("table"))
    contract, index = _contract(contract_path, contract_sha, section, target)
    sync._require(table_name in contract["tables"] and table_name in sync.STABLE_KEYS, f"表不在合同或没有稳定键：{table_name}")
    table_id = contract["tables"][table_name]["tableId"]
    key_field = sync.STABLE_KEYS[table_name]
    by_name = {meta["name"]: fid for fid, meta in index[table_id].items()}
    labels = [fid for fid, meta in index[table_id].items() if meta["class"] == field_policy.CLASS_LABEL]
    ledger = _ledger(ledger_dir, isolated_test_ledger)
    rows = transport.list_rows(target["baseToken"], table_id, [key_field, *labels])
    by_key: dict[str, list[str]] = {}
    for record_id, row in rows.items():
        if isinstance(row.get(key_field), str) and row[key_field]:
            by_key.setdefault(row[key_field], []).append(record_id)
    observed_at = sync.now()
    updates: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    unchanged = 0
    seen: set[str] = set()
    for entry in entries:
        key = sync._text(entry.get("stableKey")) if isinstance(entry, dict) else ""
        matches = by_key.get(key, [])
        fields_in = entry.get("fields") if isinstance(entry, dict) and isinstance(entry.get("fields"), dict) else None
        problems: list[str] = []
        if not key or key in seen:
            problems.append("稳定键为空或重复")
        elif len(matches) != 1:
            problems.append(f"稳定键在云端匹配 {len(matches)} 条（需恰好 1 条）")
        if not fields_in:
            problems.append("fields 为空")
        seen.add(key)
        if problems:
            refused.append({"stableKey": key, "problems": problems})
            continue
        record_id, row = matches[0], rows[matches[0]]
        ledger_key = sync.Ledger.target_key(target["baseToken"], table_id, record_id)
        writes: dict[str, Any] = {}
        for ref, item in fields_in.items():
            fid = by_name.get(ref, ref)
            meta = index[table_id].get(fid)
            if meta is None or not isinstance(item, dict):
                problems.append(f"字段不在合同：{ref}")
                continue
            basis = {"kind": field_policy.BASIS_LABEL_EVIDENCE, "sourceRef": f"{plan['sourceRef']} | {sync._text(item.get('evidenceRef'))}",
                     "evidenceSha256": item.get("evidenceSha256"), "observedAt": observed_at}
            current = _plain(meta["type"], row.get(fid))
            candidate = {"new": item.get("new"), "expectedOld": current, "basis": basis}
            problem = field_policy.write_problem(meta, basis, candidate) or \
                (None if sync._text(item.get("evidenceRef")) else f"缺 evidenceRef：{meta['name']}")
            if problem is None and meta["options"] is not None and item["new"] not in meta["options"]:
                problem = f"选项不在合同中：{meta['name']}={item['new']!r}"
            if problem:
                problems.append(problem)
            elif current == item["new"]:
                unchanged += 1
            elif current in field_policy.LABEL_REPLACEABLE or last_assistant_value(ledger, ledger_key, fid) == current:
                writes[fid] = candidate
            else:
                differences.append({"stableKey": key, "recordId": record_id, "field": meta["name"], "current": current,
                                    "evidence": item["new"], "evidenceRef": item["evidenceRef"], "action": "REPORT_ONLY"})
        if writes and ledger.state(ledger_key)["state"] == "BLOCKED":
            problems.append("该目标账本仍未决(BLOCKED)，先对账")
        if problems:
            refused.append({"stableKey": key, "recordId": record_id, "problems": problems})
        elif writes:
            updates.append({"tableId": table_id, "recordId": record_id, "stableKey": {"fieldId": key_field, "value": key}, "fields": writes})
    specs = _emit(prefix="le", table_id=table_id, updates=updates, batch_size=batch_size, target=target, contract_path=contract_path,
                  contract_sha=contract_sha, section=section, out_dir=out_dir, log_root=log_root, kind="label-evidence", table_name=table_name)
    report = {"schema": EVIDENCE_REPORT_SCHEMA, "builtAt": observed_at, "externalCalls": "read-only fresh read", "target": dict(target),
              "plan": {"path": str(plan_path), "sha256": plan_sha}, "table": table_name,
              "counts": {"entries": len(entries), "recordsToWrite": len(updates), "alreadyEqualFields": unchanged,
                         "differences": len(differences), "refused": len(refused)},
              "specs": specs, "differences": differences, "refused": refused, "status": "OK" if not refused else "PARTIAL"}
    (out_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report
