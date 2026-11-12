"""Active registration from lists the user hands over (2026-11-12 mechanism).

Two list kinds, both matched against what Feishu holds now (read-only), each run kept as one batch
under ``<业务根>/运行记录/主动登记/<批次>/`` with the input digest, the match result, what was changed
and what was read back:

- enterprise list (``EnterpriseListV1``): matched to the stable 企业编号 first, by 统一社会信用代码 and
  then by name. A matched enterprise gets its empty cells filled (a different existing value is only
  listed, unless the entry says ``overwrite``); an unmatched one gets the next free number and a new
  row. An invalid credit code, a name that collides with another enterprise, or a duplicate inside the
  list is held for a person: nothing is merged automatically.
- online-sale list (``OnlineSaleListV1``): every entry must match an existing case (项目编号, or a unit
  name that identifies exactly one case). Matched cases get 是否网售=是 through the label-evidence
  planner, which never overrides a brigade's own answer. Unmatched entries stay local in
  ``网售待匹配.json`` and are tried again by later runs; they are never written to Feishu.

Field updates go through the existing planners and ``feishu-source-sync`` (ledger, readback,
UNKNOWN reconciliation). Only the creation of new enterprise rows is done here, one row at a time,
re-reading before each create so that a repeated or interrupted run cannot create a second row.

Standard library only.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

if __package__:
    from . import feishu_plans as plans
    from . import feishu_source_sync as sync
    from . import field_policy
    from .feishu_mirror import _atomic, _load
else:
    import feishu_plans as plans
    import feishu_source_sync as sync
    import field_policy
    from feishu_mirror import _atomic, _load

REGISTER_DIR = "主动登记"
ENTERPRISE_LIST = "EnterpriseListV1"
ONLINE_LIST = "OnlineSaleListV1"
ENTERPRISE_REPORT = "EnterpriseRegisterReportV1"
ONLINE_REPORT = "OnlineSaleRegisterReportV1"
CREATE_REPORT = "EnterpriseCreateReportV1"
SECTION = "maintenanceBase"
ENTERPRISE_TABLE = "维修企业"
NUMBER = re.compile(r"^WXQ-(\d{3,})$")
CODE_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
CODE_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
PENDING_FILE = "网售待匹配.json"


def norm_name(value: Any) -> str:
    """Unit name for matching: width-folded, no whitespace. Nothing else is guessed."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)) if isinstance(value, str) else ""


def norm_code(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).upper() if isinstance(value, str) else ""


def code_valid(code: str) -> bool:
    """GB 32100 unified social credit code: 18 characters with a mod-31 check character."""
    if len(code) != 18 or any(ch not in CODE_ALPHABET for ch in code):
        return False
    total = sum(CODE_ALPHABET.index(ch) * weight for ch, weight in zip(code[:17], CODE_WEIGHTS))
    return CODE_ALPHABET[(31 - total % 31) % 31] == code[17]


def _batch(business_root: Path, kind: str, day: str, input_sha: str) -> Path:
    return Path(business_root) / "运行记录" / REGISTER_DIR / f"{day}-{kind}-{input_sha[:12]}"


def _read_list(path: Path, sha: str, schema: str) -> dict[str, Any]:
    sync._require(sync.file_sha(path) == sha, "清单摘要与固定值不一致")
    value = json.loads(path.read_text(encoding="utf-8"))
    sync._require(isinstance(value, dict) and value.get("schemaVersion") == schema, f"清单版本不支持(需要 {schema})")
    sync._require(sync._text(value.get("sourceRef")) and isinstance(value.get("entries"), list) and value["entries"], "清单需要 sourceRef 与条目")
    return value


# ---------------------------------------------------------------------------------------------
# Enterprise list
# ---------------------------------------------------------------------------------------------


def plan_enterprises(*, list_path: Path, list_sha: str, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any,
                     business_root: Path, day: str) -> dict[str, Any]:
    """Match an enterprise list and write the batch (read-only towards Feishu).

    List: ``{schemaVersion, sourceRef, userQuote, entries:[{企业名称, 统一社会信用代码?, 所属大队?, overwrite?, <大队维护字段>: 值}]}``.
    """
    listing = _read_list(list_path, list_sha, ENTERPRISE_LIST)
    sync._require(sync._text(listing.get("userQuote")), "企业清单需要用户原话 userQuote")
    contract = sync.load_contract(contract_path, contract_sha, SECTION)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    table_id = contract["tables"][ENTERPRISE_TABLE]["tableId"]
    index = sync.field_index(contract)[table_id]
    by_name = {meta["name"]: fid for fid, meta in index.items()}
    key_field = sync.STABLE_KEYS[ENTERPRISE_TABLE]
    brigade_field = by_name["所属大队"]
    editable = {meta["name"]: fid for fid, meta in index.items() if meta["class"] == field_policy.CLASS_BRIGADE and meta["type"] in sync.SCALAR_TYPES}
    rows = transport.list_rows(target["baseToken"], table_id, [key_field, brigade_field, *editable.values()])
    name_fid, code_fid = editable["企业名称"], editable["统一社会信用代码"]
    by_code: dict[str, list[str]] = {}
    by_unit: dict[str, list[str]] = {}
    highest = 0
    for record_id, row in rows.items():
        number = NUMBER.match(row.get(key_field) or "")
        highest = max(highest, int(number.group(1)) if number else 0)
        if norm_code(row.get(code_fid)):
            by_code.setdefault(norm_code(row.get(code_fid)), []).append(record_id)
        by_unit.setdefault(norm_name(row.get(name_fid)), []).append(record_id)

    seen_codes: dict[str, int] = {}
    seen_names: dict[str, int] = {}
    matched, new, held, differences, delegated = [], [], [], [], []
    for position, entry in enumerate(listing["entries"], 1):
        name, code = norm_name(entry.get("企业名称") if isinstance(entry, dict) else None), norm_code(entry.get("统一社会信用代码") if isinstance(entry, dict) else None)
        item = {"position": position, "企业名称": entry.get("企业名称") if isinstance(entry, dict) else None, "统一社会信用代码": code or None}

        def hold(reason: str, **extra: Any) -> None:
            held.append({**item, "reason": reason, **extra})

        if not name:
            hold("缺企业名称")
            continue
        unknown = sorted(k for k in entry if k not in editable and k not in ("所属大队", "overwrite"))
        if unknown:
            hold("清单含非大队维护字段，不代录", fields=unknown)
            continue
        if (code and code in seen_codes) or name in seen_names:
            hold("清单内重复", firstPosition=seen_codes.get(code) or seen_names.get(name))
            continue
        seen_names[name] = position
        if code:
            seen_codes[code] = position
            if not code_valid(code):
                hold("信用代码异常（长度、字符或校验位不符）")
                continue
        code_hits, name_hits = by_code.get(code, []) if code else [], by_unit.get(name, [])
        if len(code_hits) > 1 or len(name_hits) > 1:
            hold("飞书已有多条同代码或同名企业，不自动合并", candidates=sorted(rows[r].get(key_field) for r in {*code_hits, *name_hits}))
            continue
        record_id = None
        if code_hits:
            record_id = code_hits[0]
            if norm_name(rows[record_id].get(name_fid)) != name:
                hold("信用代码相同但名称不同，不自动合并", candidates=[rows[record_id].get(key_field)], feishuName=rows[record_id].get(name_fid))
                continue
        elif name_hits:
            record_id = name_hits[0]
            existing_code = norm_code(rows[record_id].get(code_fid))
            if code and existing_code and existing_code != code:
                hold("同名但信用代码不同，不自动合并", candidates=[rows[record_id].get(key_field)])
                continue
        values = {k: v for k, v in entry.items() if k in editable and v not in (None, "")}
        if record_id is None:
            brigade = entry.get("所属大队")
            if brigade not in (index[brigade_field]["options"] or []):
                hold("新企业缺少有效的所属大队")
                continue
            highest += 1
            new.append({**item, "企业编号": f"WXQ-{highest:03d}", "所属大队": brigade, "values": values})
            continue
        row, stable = rows[record_id], rows[record_id].get(key_field)
        fields: dict[str, Any] = {}
        for field_name, value in values.items():
            meta = index[editable[field_name]]
            current = plans._plain(meta["type"], row.get(editable[field_name]))
            if current == plans._plain(meta["type"], value if meta["type"] != "select" else [value]):
                continue
            if field_name == "企业名称" or (field_name == "统一社会信用代码" and norm_code(current) == code):
                continue                                     # matched on it; a width or spacing variant is not a new value
            if current is None or entry.get("overwrite") is True:
                fields[field_name] = {"new": value, "expectedOld": current}
            else:
                differences.append({"企业编号": stable, "field": field_name, "feishu": current, "list": value})
        matched.append({**item, "企业编号": stable, "recordId": record_id, "fieldsToWrite": sorted(fields)})
        if fields:
            delegated.append({"stableKey": stable, "recordId": record_id, "fields": fields})

    batch = _batch(business_root, "企业清单", day, list_sha)
    result = {"schema": ENTERPRISE_REPORT, "day": day, "input": {"path": str(list_path), "sha256": list_sha, "sourceRef": listing["sourceRef"], "entries": len(listing["entries"])},
              "feishuRowsRead": len(rows), "matched": matched, "new": new, "held": held, "differences": differences, "externalCalls": "read-only"}
    plan_ref = None
    if delegated:
        plan = {"schemaVersion": plans.DELEGATED_PLAN_SCHEMA, "authorizedBy": "user", "userQuote": listing["userQuote"], "sourceRef": listing["sourceRef"],
                "section": SECTION, "table": ENTERPRISE_TABLE, "entries": delegated}
        _atomic(batch / "代录计划.json", plan)
        plan_ref = {"path": str(batch / "代录计划.json"), "sha256": sync.file_sha(batch / "代录计划.json")}
    result["delegatedPlan"] = plan_ref
    result["counts"] = {"matched": len(matched), "new": len(new), "held": len(held), "differences": len(differences), "recordsToUpdate": len(delegated)}
    result["batchDir"] = str(batch)
    _atomic(batch / "匹配结果.json", result)
    return result


def create_enterprises(*, batch_dir: Path, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any,
                       retry_not_found: bool = False) -> dict[str, Any]:
    """Create the batch's new enterprises one at a time; safe to run again after an interruption.

    Before each create the table is read again: a row that already carries the number with the same name
    counts as created; a number or a name/code taken by anything else blocks that entry. A create whose
    effect is unknown stops the run; the next run looks for the row first and never blindly repeats.
    """
    match = _load(Path(batch_dir) / "匹配结果.json")
    sync._require(isinstance(match, dict) and match.get("schema") == ENTERPRISE_REPORT, "批次目录没有企业清单匹配结果")
    contract = sync.load_contract(contract_path, contract_sha, SECTION)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    table_id = contract["tables"][ENTERPRISE_TABLE]["tableId"]
    index = sync.field_index(contract)[table_id]
    by_name = {meta["name"]: fid for fid, meta in index.items()}
    key_field, name_fid, code_fid, brigade_fid = sync.STABLE_KEYS[ENTERPRISE_TABLE], by_name["企业名称"], by_name["统一社会信用代码"], by_name["所属大队"]
    state_path = Path(batch_dir) / "新建回执.json"
    state = _load(state_path) or {"schema": CREATE_REPORT, "entries": {}}
    base = target["baseToken"]

    def find(entry: dict[str, Any]) -> tuple[str | None, str | None]:
        rows = transport.list_rows(base, table_id, [key_field, name_fid, code_fid])
        same = [r for r, row in rows.items() if row.get(key_field) == entry["企业编号"]]
        if same:
            ok = len(same) == 1 and norm_name(rows[same[0]].get(name_fid)) == norm_name(entry["企业名称"])
            return (same[0], None) if ok else (None, "企业编号已被其他记录占用")
        code = entry.get("统一社会信用代码")
        for row in rows.values():
            if norm_name(row.get(name_fid)) == norm_name(entry["企业名称"]) or (code and norm_code(row.get(code_fid)) == code):
                return None, f"已有同名或同代码企业 {row.get(key_field)}，需重新匹配"
        return None, None

    stopped = None
    for entry in match["new"]:
        number = entry["企业编号"]
        record = state["entries"].get(number, {"status": "PENDING"})
        if record["status"] == "CREATED":
            continue
        found, blocked = find(entry)
        if found:
            record = {**record, "status": "CREATED", "recordId": found, "confirmedBy": "readback", "at": sync.now()}
        elif blocked:
            record = {**record, "status": "BLOCKED", "reason": blocked, "at": sync.now()}
        elif record["status"] == "UNKNOWN" and not retry_not_found:
            record = {**record, "status": "UNKNOWN", "readback": "not-found", "at": sync.now()}
            stopped = f"{number} 上次新建结果未知且本次回读未见；确认未建后用 --retry-not-found 续办"
        else:
            payload = {key_field: number, brigade_fid: sync.wire_value("select", entry["所属大队"]), name_fid: entry["企业名称"]}
            for field_name, value in entry["values"].items():
                payload[by_name[field_name]] = sync.wire_value(index[by_name[field_name]]["type"], value)
            state["entries"][number] = {"status": "UNKNOWN", "intentAt": sync.now(), "fields": sorted(index[f]["name"] for f in payload)}
            _atomic(state_path, state)                      # intent on disk before the external write
            try:
                receipt = transport.create_record(base, table_id, payload)
            except sync.RuntimeSyncError as error:
                record = {**state["entries"][number], "error": str(error)[:200]}
                stopped = f"{number} 新建结果未知，已停止；下次运行先回读"
            else:
                record_id = receipt["data"]["record"]["record_id"]
                stored = transport.get_records(base, table_id, [record_id])[record_id]
                wrong = sorted(index[f]["name"] for f, wire in payload.items() if not sync.cell_equals(index[f]["type"], stored.get(f), wire if not isinstance(wire, list) else wire[0]))
                record = {**state["entries"][number], "status": "CREATED" if not wrong else "MISMATCH", "recordId": record_id, "readbackMismatch": wrong, "at": sync.now()}
                if wrong:
                    stopped = f"{number} 已新建但回读不一致：{'、'.join(wrong)}"
        state["entries"][number] = record
        _atomic(state_path, state)
        if stopped:
            break
    statuses = [state["entries"].get(e["企业编号"], {"status": "PENDING"})["status"] for e in match["new"]]
    state.update({"counts": {s: statuses.count(s) for s in sorted(set(statuses))}, "stopped": stopped,
                  "status": "OK" if all(s == "CREATED" for s in statuses) else ("STOPPED" if stopped else "ATTENTION")})
    _atomic(state_path, state)
    return {**state, "batchDir": str(batch_dir)}


# ---------------------------------------------------------------------------------------------
# Online-sale list
# ---------------------------------------------------------------------------------------------


def plan_online_sales(*, list_path: Path, list_sha: str, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any,
                      business_root: Path, day: str) -> dict[str, Any]:
    """Match an online-sale list to existing cases; unmatched entries stay in the local pending file.

    List: ``{schemaVersion, sourceRef, entries:[{项目编号?, 单位名称?, 说明?}]}``. Earlier pending entries are tried again.
    """
    listing = _read_list(list_path, list_sha, ONLINE_LIST)
    contract = sync.load_contract(contract_path, contract_sha)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    table_id = contract["tables"]["案卷主表"]["tableId"]
    by_name = {meta["name"]: fid for fid, meta in sync.field_index(contract)[table_id].items()}
    key_field, unit_fid = sync.STABLE_KEYS["案卷主表"], by_name["单位名称"]
    rows = transport.list_rows(target["baseToken"], table_id, [key_field, unit_fid])
    projects = {row.get(key_field) for row in rows.values() if row.get(key_field)}
    by_unit: dict[str, list[str]] = {}
    for row in rows.values():
        if row.get(key_field):
            by_unit.setdefault(norm_name(row.get(unit_fid)), []).append(row[key_field])

    register_root = Path(business_root) / "运行记录" / REGISTER_DIR
    pending_path = register_root / PENDING_FILE
    pending = _load(pending_path) or {"schema": "OnlineSalePendingV1", "entries": {}}
    candidates: list[tuple[str, dict[str, Any], dict[str, str]]] = []
    for digest, item in pending["entries"].items():
        candidates.append((digest, item["entry"], item["evidence"]))
    evidence = {"evidenceRef": listing["sourceRef"], "evidenceSha256": list_sha}
    for entry in listing["entries"]:
        sync._require(isinstance(entry, dict) and (sync._text(entry.get("项目编号")) or norm_name(entry.get("单位名称"))), "网售条目需要项目编号或单位名称")
        digest = sync.digest(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        if digest not in pending["entries"]:
            candidates.append((digest, entry, evidence))

    matched: dict[str, dict[str, Any]] = {}
    unmatched: dict[str, dict[str, Any]] = {}
    for digest, entry, proof in candidates:
        project, unit = sync._text(entry.get("项目编号")), norm_name(entry.get("单位名称"))
        reason = None
        if project:
            reason = None if project in projects else "项目编号不在案卷主表"
        else:
            hits = by_unit.get(unit, [])
            project, reason = (hits[0], None) if len(hits) == 1 else (None, "单位名称没有对应案卷" if not hits else f"单位名称对应 {len(hits)} 个案卷，需给出项目编号")
        if reason:
            previous = pending["entries"].get(digest, {})
            unmatched[digest] = {"entry": entry, "evidence": proof, "reason": reason, "firstSeen": previous.get("firstSeen", day), "lastTried": day}
        else:
            matched.setdefault(project, {"entries": [], **proof})["entries"].append(entry)

    batch = _batch(business_root, "网售清单", day, list_sha)
    plan_ref = None
    if matched:
        plan = {"schemaVersion": plans.EVIDENCE_PLAN_SCHEMA, "sourceRef": listing["sourceRef"], "table": "案卷主表",
                "entries": [{"stableKey": project, "fields": {"是否网售": {"new": "是", "evidenceRef": info["evidenceRef"], "evidenceSha256": info["evidenceSha256"]}}}
                            for project, info in sorted(matched.items())]}
        _atomic(batch / "证据计划.json", plan)
        plan_ref = {"path": str(batch / "证据计划.json"), "sha256": sync.file_sha(batch / "证据计划.json")}
    result = {"schema": ONLINE_REPORT, "day": day, "input": {"path": str(list_path), "sha256": list_sha, "sourceRef": listing["sourceRef"], "entries": len(listing["entries"])},
              "feishuRowsRead": len(rows), "matched": [{"项目编号": p, "entries": len(i["entries"])} for p, i in sorted(matched.items())],
              "unmatched": [{"entry": u["entry"], "reason": u["reason"], "firstSeen": u["firstSeen"]} for u in unmatched.values()],
              "counts": {"matchedCases": len(matched), "unmatched": len(unmatched), "retriedPending": len(pending["entries"])},
              "evidencePlan": plan_ref, "pendingFile": str(pending_path), "batchDir": str(batch), "externalCalls": "read-only"}
    _atomic(batch / "匹配结果.json", result)
    _atomic(pending_path, {"schema": "OnlineSalePendingV1", "updated": day, "entries": unmatched})
    return result
