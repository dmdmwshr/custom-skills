"""监督事实三表校准：本地已核证据(代表监督系统) 对 飞书当前值，产出字段计划与未决差异(2026-11-13 统一校准)。

存量校准、每晚维护和有人问起某案时的单案核对都走这一处，不另写一次性修表脚本。它只读本地与云端，
产出 ``FeishuFieldPlanV1`` 字段计划(交 ``feishu-field-plan-build`` 生成写入说明，再由 ``feishu-source-sync``
带旧值门禁、账本和回读执行)和一份差异清单。规则：

- 监督事实的权威是监督系统，本案已核验清单及总账分类、可信后续观察、已保存可信详情是它的本地稳定证据；
  历史可信证据不因时间或工具版本失效，校准不回监督系统重采。
- 只补空值，或把未决占位(待核实/待结果/观察期内未开展等)推进到证据给出的结论；云端已有明确值而证据给出
  另一个明确值时不改写任何一方，列为“待核对”差异并保留双方原值。证据自身互相矛盾时同样只列差异。
- 初查结果只有一条规则(``feishu_source_sync.unified_initial_result``)：主行、初查检查行和可由案卷结论唯一确定的
  产品行同源。
- 复查：清单有复查才有复查日期和结果；初查合格且证实没有复查才是“无需复查”；初查不合格而未见复查按生命周期
  规则给“观察期内未开展/到期确认未开展/已开展待结果/待核实”；初查未决不写复查结果。不造复查行，不按最后一行覆盖。
- 两处“日期来源”和检查/产品两表的“数据来源”统一为“监督系统”(监督事实由本地已核验清单代表)；旧来源标签写入本次报告(履历)后退出日常界面。
- 历史上未获用户接受的人工放行目标、范围外保留和没有已核验清单的案卷整案不动，逐案列出原因。
- 真实 0 只在产品全部有明确结果时写(带 explicitNegative)；缺值不写 0、不写“否”。
- 待核对差异的收口只走“校准裁决”文件(``--resolutions``)：逐条写明表、稳定键、字段、当时的云端值与清单值、采用哪一方
  和原包文书证据(路径+摘要)。只有云端值、清单值都与裁决时完全相同、证据文件摘要未变才生效；采用清单值的经字段计划
  带旧值门禁写入，采用云端值的不写、记入报告 resolved。任何一方变了裁决自动失效，重新列为差异。
- 创建时一次写入的字段(产品名称、两表数据来源)在合同里登记了 ``evidenceCorrection`` 才允许被校准更正；日常同步仍不更新它们。
  名称裁决采用清单值即按证据逐格更正(同一产品只是写法不同，或行序与记录不一致都一样)，采用云端值则不写。
- 更正模式(``correct_facts``)：云端已有明确值而可信清单证据给出另一个明确值时，不再只列差异，而是带证据和精确旧值门禁更正
  (管理员误改、早期写错都走这一条)；已有裁决优先；清单值疑似提取错误(空、同一段文字重复两遍)只列差异；单次更正条数超过
  上限整批不写、全部列出，防止坏清单批量覆盖。人工字段永不在内(执行器按合同类别拒绝)。
- 问题描述：云端文字整段包含在清单文字里(清单多了来源详情与文书出处前缀)视为同一内容，不列差异。
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

if __package__:
    from . import initial_scope
    from . import feishu_source_sync as sync
    from . import ledger_views as _views
    from . import workspace_state as _ws
else:  # script-style import (scripts/ on sys.path)
    import feishu_source_sync as sync
    import initial_scope
    import ledger_views as _views
    import workspace_state as _ws

REPORT_SCHEMA = "FeishuCalibrationReportV1"
RESOLUTION_SCHEMA = "CalibrationResolutionsV1"
SYSTEM = "监督系统"
MAIN_F = {"key": sync.BASIC_FIELDS["key"], "team": sync.BASIC_FIELDS["team"], "date": sync.BASIC_FIELDS["date"],
          "result": sync.LIFECYCLE_FIELDS["result"], "date_source": sync.LIFECYCLE_FIELDS["date_source"],
          "recheck_date": "fld2gf8Lsz", "recheck_result": "fld5vhdy1b", "unit": "fldoF1a3IQ", "form": "fld6ZUenHM"}
UNIT_OPTIONS = ("重点单位", "其他单位", "一般单位", "九小场所")
TERMINAL = ("合格", "不合格")
PENDING_INITIAL = ("待核实", "待结果")
# 复查结果：会随时间和新证据推进的状态；其余(合格/不合格/无需复查)是结论
RECHECK_OPEN = ("待核实", "观察期内未开展", "已开展待结果", "到期确认未开展")
RECHECK_STATE = {"观察期内未开展": "观察期内未开展", "到期确认未开展": "到期确认未开展", "已开展复查待结果": "已开展待结果", "待核实": "待核实"}
KEEP = "保留双方原值，交核对"


def _norm(value: Any) -> str:
    """比较用：全半角归一、去掉全部空白。只用于判断两段文字是否同一内容，不改写任何一方。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", sync._text(value)))


def _plain(field_type: str, cell: Any) -> Any:
    """云端单元格的可读原值：日期给 YYYY-MM-DD，数字给数，单选给选项名；空为 None；读不清原样返回。"""
    if sync.cell_is_empty(cell):
        return None
    try:
        value = sync.norm_cell(field_type, cell)
    except sync.Ambiguous:
        return cell
    if field_type == "datetime":
        return value.strftime("%Y-%m-%d") if (value.hour, value.minute) == (0, 0) else value.strftime("%Y-%m-%d %H:%M")
    if field_type == "number":
        return int(value) if float(value).is_integer() else value
    return value


class _Plan:
    """一次校准的累积：字段计划条目、未决差异、整案不动的原因。"""

    def __init__(self, root: Path, index: dict[str, dict[str, dict[str, Any]]], tables: dict[str, str], *, correct: bool = False, limit: int = 25) -> None:
        self.root, self.index, self.tables = root, index, tables
        self.correct, self.limit = correct, limit
        self.staged: list[dict[str, Any]] = []
        self.held = 0
        self.entries: dict[tuple[str, str], dict[str, Any]] = {}
        self.diffs: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.resolved: list[dict[str, Any]] = []
        self.resolutions: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._sha: dict[str, str | None] = {}

    def load_resolutions(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        sync._require(data.get("schema") == RESOLUTION_SCHEMA, f"校准裁决文件 schema 不是 {RESOLUTION_SCHEMA}")
        for item in data.get("entries", []):
            sync._require(item.get("adopt") in ("local", "cloud") and item.get("evidence") and item.get("basis"), f"校准裁决条目不完整：{item.get('key')}")
            slot = (item["table"], item["key"], item["field"])
            sync._require(slot not in self.resolutions, f"校准裁决重复：{slot}")
            self.resolutions[slot] = item

    def resolve(self, project: str, table: str, key: str, field_id: str, cloud: Any, local: Any) -> tuple[str, list[dict[str, str] | None], str] | None:
        """这一格有没有仍然有效的裁决：返回 (采用方, 证据, 依据)。云端值或清单值已不是裁决时的值即失效。"""
        name = self.index[self.tables[table]][field_id]["name"]
        item = self.resolutions.get((table, key, name))
        if item is None or item.get("projectNo") != project or _norm(item.get("cloud")) != _norm(cloud) or _norm(item.get("local")) != _norm(local):
            return None
        evidence = [self.evidence(e["path"], e.get("sha256")) for e in item["evidence"]]
        if item["adopt"] == "cloud":
            if not all(evidence):
                return None
            self.resolved.append({"projectNo": project, "table": table, "key": key, "field": name, "cloud": cloud, "local": local, "adopt": "cloud", "basis": item["basis"]})
        return item["adopt"], evidence, f"校准裁决采用清单值：{item['basis']}"

    def evidence(self, relative: str, recorded: str | None = None) -> dict[str, str] | None:
        relative = relative.replace("\\", "/")
        if relative not in self._sha:
            try:
                self._sha[relative] = sync.file_sha(self.root / relative)
            except OSError:
                self._sha[relative] = None
        actual = self._sha[relative]
        if actual is None or (recorded and recorded.removeprefix("sha256:") != actual):
            return None
        return {"path": relative, "sha256": actual}

    def value(self, table: str, row: dict[str, Any], field_id: str) -> Any:
        return _plain(self.index[self.tables[table]][field_id]["type"], row.get(field_id))

    def diff(self, project: str, table: str, key: str, field_id: str | None, cloud: Any, local: Any, kind: str, action: str = KEEP) -> None:
        name = self.index[self.tables[table]][field_id]["name"] if field_id else "行"
        self.diffs.append({"projectNo": project, "table": table, "key": key, "field": name, "cloud": cloud, "local": local, "kind": kind, "action": action})

    @staticmethod
    def malformed(text: Any) -> bool:
        """清单值疑似提取错误：空，或同一段文字重复两遍(曾见产品名称被重复提取)。"""
        compact = _norm(text)
        return not compact or (len(compact) % 2 == 0 and len(compact) >= 4 and compact[: len(compact) // 2] == compact[len(compact) // 2:])

    def try_correct(self, project: str, table: str, record_id: str, key_field: str, key: str, field_id: str, cloud: Any, local: Any, kind: str,
                    how: str, evidence: list[dict[str, str] | None], observed_at: str, *, negative: bool = False) -> bool:
        """云端有明确值而可信证据给出另一个明确值：更正模式下排入待更正(返回 True，调用方不再列差异)，否则返回 False 由调用方列差异。"""
        if not self.correct or cloud is None or local is None:
            return False
        if isinstance(local, str) and self.malformed(local):
            self.diff(project, table, key, field_id, cloud, local, kind + "；清单值疑似提取错误", "不更正；交核对")
            return True
        self.staged.append({"project": project, "table": table, "record_id": record_id, "key_field": key_field, "key": key, "field_id": field_id,
                            "old": cloud, "new": local, "evidence": evidence, "basis": how, "observed_at": observed_at, "negative": negative, "kind": kind})
        return True

    def flush_corrections(self) -> None:
        """更正条数不超过上限才写；超过则整批不写、逐条列出(坏清单批量覆盖的断路器)。"""
        if len(self.staged) > self.limit:
            for item in self.staged:
                self.diff(item["project"], item["table"], item["key"], item["field_id"], item["old"], item["new"], item["kind"],
                          f"本次待更正 {len(self.staged)} 处，超过单次上限 {self.limit}，整批不写；核对后用校准裁决或调高上限")
            self.held = len(self.staged)
        else:
            for item in self.staged:
                self.write(item["project"], item["table"], item["record_id"], item["key_field"], item["key"], item["field_id"], item["old"], item["new"],
                           item["evidence"], "更正已有值(可信证据+精确旧值)：" + item["basis"], item["observed_at"], negative=item["negative"], correction=True)
        self.staged = []

    def write(self, project: str, table: str, record_id: str, key_field: str, key: str, field_id: str, old: Any, new: Any,
              evidence: list[dict[str, str] | None], basis: str, observed_at: str, *, negative: bool = False, correction: bool = False) -> None:
        name = self.index[self.tables[table]][field_id]["name"]
        files = [e for e in evidence if e]
        if not files or len(files) != len(evidence):
            self.diffs.append({"projectNo": project, "table": table, "key": key, "field": name, "cloud": old, "local": new,
                               "kind": "证据文件不可读或摘要已变", "action": "不写；先核对本地证据"})
            return
        entry = self.entries.setdefault((table, record_id), {"table": table, "recordId": record_id, "projectNo": project,
                                                             "stableKey": {"fieldId": key_field, "value": key}, "fields": {}})
        item: dict[str, Any] = {"new": new, "expectedOld": old, "evidence": files, "basisRef": basis, "observedAt": observed_at}
        if negative:
            item["explicitNegative"] = True
        if correction:
            item["correction"] = True
        entry["fields"][field_id] = item
        self.writes.append({"projectNo": project, "table": table, "key": key, "recordId": record_id, "field": name, "fieldId": field_id, "old": old, "new": new, "basis": basis,
                            **({"correction": True} if correction else {})})


def _recheck_state(layout: Any, project: str, record: dict[str, Any], today: date | None) -> tuple[str | None, str]:
    """初查不合格而清单没有复查时的复查状态：与本地视图同一评估(可信详情/后续观察、通知书日期、关联链)。"""
    try:
        lifecycle = _views.describe(layout, project, record, today=today)["lifecycle"]
    except Exception as error:  # noqa: BLE001 - 评估不了就不写，列差异
        return None, f"复查状态无法评估：{type(error).__name__}"
    recheck = lifecycle["recheck"]
    state = recheck["state"]
    reason = "；".join(sorted({sync._text(c.get("reason")) for c in lifecycle["chains"] if c.get("reason")}))[:300]
    note = f"生命周期评估 state={state} noticeDate={recheck.get('noticeDate')} dueDate={recheck.get('dueDate')} observedOn={recheck.get('observedOn')} | {reason}"
    return RECHECK_STATE.get(state), note


def build_calibration_plan(*, waterline_path: Path, contract_path: Path, contract_sha: str, target: dict[str, str], transport: Any, out_dir: Path,
                           ledger_dir: Path | None, scope_from: str = "2026-01-01", only_projects: frozenset[str] | None = None,
                           today: date | None = None, isolated_test_ledger: bool = False, resolutions_path: Path | None = None,
                           correct_facts: bool = False, max_corrections: int = 25, form_evidence_path: Path | None = None, visual_evidence_path: Path | None = None) -> dict[str, Any]:
    """读本地已核证据与云端三表当前值，写出字段计划(calibrate-plan.json)和校准报告(calibrate-report.json)。只读云端。"""
    sync._require(ledger_dir is not None, "校准必须沿唯一写入账本(--ledger-dir)：缺账会跳过历史封锁与放行核查，拒绝")
    ledger = sync.Ledger(ledger_dir, isolated_test=isolated_test_ledger)
    ledger.validate_root()
    contract = sync.load_contract(contract_path, contract_sha)
    sync._require(target.get("baseToken") == contract["baseId"], "目标 Base 与合同不一致")
    index = sync.field_index(contract)
    tables = {name: contract["tables"][name]["tableId"] for name in ("案卷主表", "检查明细", "产品明细")}
    case_table, inspect_table, product_table = tables["案卷主表"], tables["检查明细"], tables["产品明细"]
    I, P, M = sync.INSPECT_F, sync.PRODUCT_F, MAIN_F
    root = waterline_path.parent
    waterline = json.loads(waterline_path.read_text(encoding="utf-8")).get("cases", {})
    excluded = initial_scope.excluded_projects_in_root(root)
    base = target["baseToken"]
    main_rows = transport.list_rows(base, case_table, list(M.values()))
    inspect_rows = transport.list_rows(base, inspect_table, [I[k] for k in ("key", "stage", "date", "month", "team", "result", "count", "bad", "method", "date_source", "round", "source")])
    product_rows = transport.list_rows(base, product_table, [P[k] for k in ("key", "seq", "name", "model", "producer", "location", "method", "result", "problem", "source")])
    by_project: dict[str, list[str]] = {}
    for record_id, row in main_rows.items():
        by_project.setdefault(sync._cell_text(row.get(M["key"])), []).append(record_id)
    inspect_by_key: dict[str, list[str]] = {}
    for record_id, row in inspect_rows.items():
        inspect_by_key.setdefault(sync._cell_text(row.get(I["key"])), []).append(record_id)
    product_by_key: dict[str, list[str]] = {}
    for record_id, row in product_rows.items():
        product_by_key.setdefault(sync._cell_text(row.get(P["key"])), []).append(record_id)
    plan = _Plan(root, index, tables, correct=correct_facts, limit=max_corrections)
    form_evidence: dict[str, Any] = {}
    if form_evidence_path is not None:
        loaded = json.loads(form_evidence_path.read_text(encoding="utf-8"))
        sync._require(loaded.get("schemaVersion") == "RecordFormEvidenceV1", "检查形式证据版本不支持")
        form_evidence = loaded.get("cases") or {}
    visual_evidence: dict[str, Any] = {}
    if visual_evidence_path is not None:
        loaded = json.loads(visual_evidence_path.read_text(encoding="utf-8"))
        sync._require(loaded.get("schemaVersion") == "VisualDetailEvidenceV1", "视觉确认证据版本不支持")
        visual_evidence = loaded.get("cases") or {}
    if resolutions_path is not None:
        plan.load_resolutions(resolutions_path)
    layout = None
    counts = {"cases": 0, "casesCompared": 0, "inspectionsCompared": 0, "productsCompared": 0, "problemExcerpts": 0}
    for project in sorted(p for p in by_project if p):
        if only_projects is not None and project not in only_projects:
            continue
        counts["cases"] += 1
        ids = by_project[project]
        if len(ids) != 1:
            plan.skipped.append({"projectNo": project, "reason": f"云端主行不唯一：{ids}"})
            continue
        record_id, row = ids[0], main_rows[ids[0]]
        release = sync._pending_human_release(ledger, base, case_table, record_id, project)
        if release:
            plan.skipped.append({"projectNo": project, "recordId": record_id, "reason": f"历史上存在未获用户风险接受的 human_release({release})：整案保持保护"})
            continue
        if project in excluded:
            plan.skipped.append({"projectNo": project, "recordId": record_id, "reason": "案卷在范围外保留"})
            continue
        record = waterline.get(project)
        found, why = sync._verified_manifest(root, project, record)
        if found is None:
            plan.skipped.append({"projectNo": project, "recordId": record_id, "reason": why or "没有可用的已核验清单"})
            continue
        manifest, relative, recorded, verified_at = found
        manifest_ev = plan.evidence(relative, recorded)
        facts = [sync._child_inspection(project, key, name, tag, manifest.get(key), scope_from) for key, name, tag in sync.CHILD_STAGES
                 if key == "initialInspection" or manifest.get(key) is not None]
        if facts[0][0] is None:
            plan.skipped.append({"projectNo": project, "recordId": record_id, "reason": facts[0][1] or "清单没有可用的初查"})
            continue
        counts["casesCompared"] += 1
        initial = facts[0][0]
        recheck = facts[1][0] if len(facts) > 1 else None
        if len(facts) > 1 and recheck is None:
            plan.diff(project, "案卷主表", project, None, None, facts[1][1], "清单复查记录不可用", "复查相关字段不写；先核对清单")
        unified = sync.unified_initial_result(root, project, record, manifest_found=found)
        ref = f"{relative} | 本地核验清单 manifestSha256={recorded} | projectNo={project}"

        def put(table: str, rid: str, key_field: str, key: str, field_id: str, old: Any, new: Any, how: str, *, extra: list[dict[str, str] | None] | None = None,
                negative: bool = False, project: str = project, ref: str = ref, manifest_ev: Any = manifest_ev, verified_at: str = verified_at) -> None:
            plan.write(project, table, rid, key_field, key, field_id, old, new, [manifest_ev, *(extra or [])], f"{ref} | {how}", verified_at, negative=negative)

        def corr(table: str, rid: str, key_field: str, key: str, field_id: str, cloud: Any, local: Any, kind: str, how: str, *,
                 extra: list[dict[str, str] | None] | None = None, negative: bool = False, project: str = project, ref: str = ref,
                 manifest_ev: Any = manifest_ev, verified_at: str = verified_at) -> bool:
            return plan.try_correct(project, table, rid, key_field, key, field_id, cloud, local, kind, f"{ref} | {how}", [manifest_ev, *(extra or [])], verified_at, negative=negative)

        unified_ev: list[dict[str, str] | None] = []
        if unified["terminal"] and unified["source"] != "manifest":
            unified_ev = [plan.evidence(sync._text(unified["ref"]).split("#", 1)[0].split(" | ", 1)[0])]
        unified_how = f"案卷级统一初查结论({unified['source']})：{unified['ref']}" if unified["terminal"] else ""

        # ---------------- 主行
        cloud_date, cloud_result = plan.value("案卷主表", row, M["date"]), plan.value("案卷主表", row, M["result"])
        cloud_source = plan.value("案卷主表", row, M["date_source"])
        date_agrees = cloud_date == initial["day"]
        if not date_agrees:
            if corr("案卷主表", record_id, M["key"], project, M["date"], cloud_date, initial["day"], "初查日期与清单不同", f"初查日期以清单 {initial['day']} 为准"):
                if cloud_source != SYSTEM and not corr("案卷主表", record_id, M["key"], project, M["date_source"], cloud_source, SYSTEM, "初查日期来源", "日期已按清单更正，来源统一为监督系统"):
                    put("案卷主表", record_id, M["key"], project, M["date_source"], cloud_source, SYSTEM, "日期已按清单更正，来源统一为监督系统")
            else:
                plan.diff(project, "案卷主表", project, M["date"], cloud_date, initial["day"], "初查日期与清单不同")
        elif cloud_source != SYSTEM:
            put("案卷主表", record_id, M["key"], project, M["date_source"], cloud_source, SYSTEM,
                f"初查日期 {initial['day']} 与清单一致；监督系统事实由本地已核验清单代表，旧来源标签 {cloud_source!r} 记入校准履历")
        if unified["conflict"]:
            plan.diff(project, "案卷主表", project, M["result"], cloud_result, {"案卷侧": unified["caseLabel"], "清单逐项": unified["manifestLabel"]}, "初查结果本地证据互相冲突")
        elif unified["terminal"]:
            if cloud_result in (None, *PENDING_INITIAL):
                put("案卷主表", record_id, M["key"], project, M["result"], cloud_result, unified["label"], unified_how, extra=unified_ev)
            elif cloud_result != unified["label"]:
                if not corr("案卷主表", record_id, M["key"], project, M["result"], cloud_result, unified["label"], "初查结果云端与本地证据不同", unified_how, extra=unified_ev):
                    plan.diff(project, "案卷主表", project, M["result"], cloud_result, unified["label"], "初查结果云端与本地证据不同")
        elif cloud_result is None:
            put("案卷主表", record_id, M["key"], project, M["result"], None, unified["label"], "尚无终局证据：登记为未决状态，不是结论")
        elif cloud_result in TERMINAL:
            plan.diff(project, "案卷主表", project, M["result"], cloud_result, unified["label"], "云端有初查结论而本地无终局证据", "保留云端值(历史可信)，本地待补证据")
        initial_label = unified["label"] if unified["terminal"] else None

        # ---------------- 单位类别(已保存的可信来源详情)与检查形式(初查电子记录的勾选框)：补空；云端已有不同值时走更正/差异
        detail = sync.saved_detail_evidence(root, project, record)
        unit_local = sync._text((detail or {}).get("fields", {}).get("单位类别")) if detail else ""
        visual = visual_evidence.get(project) or {}
        visual_used = False
        if not unit_local and sync._text(visual.get("unitCategory")):
            unit_local, visual_used = sync._text(visual["unitCategory"]), True   # 手动交件没有结构化详情：改用逐图人工视觉确认(只补空)
        if unit_local:
            unit_cloud = plan.value("案卷主表", row, M["unit"])
            if visual_used:
                shot = visual.get("screenshot") or {}
                unit_ev = plan.evidence(sync._text(shot.get("path")), sync._text(shot.get("sha256")))
            else:
                unit_ev = plan.evidence(detail["path"], detail["sha256"])
            key_unit = (form_evidence.get(project) or {}).get("keyUnit")
            if unit_ev is None:
                plan.diff(project, "案卷主表", project, M["unit"], unit_cloud, unit_local, "单位类别证据文件缺失或摘要不符", "不写；重新核证据")
            elif key_unit is not None and unit_local in UNIT_OPTIONS and (unit_local == "重点单位") != key_unit:
                plan.diff(project, "案卷主表", project, M["unit"], unit_cloud, {"来源详情": unit_local, "初查电子记录勾选": "重点单位" if key_unit else "非重点单位"},
                          "单位类别：来源详情与初查电子记录勾选互相矛盾", "不写不更正，保留双方；交核对")
            elif unit_local not in UNIT_OPTIONS:
                plan.diff(project, "案卷主表", project, M["unit"], unit_cloud, unit_local, "来源详情的单位类别不在合同选项里", "不写；交核对")
            elif unit_cloud is None:
                plan.write(project, "案卷主表", record_id, M["key"], project, M["unit"], None, unit_local, [manifest_ev, unit_ev],
                           (f"{shot.get('path')} | 详情截图逐图人工视觉确认(单位类别={unit_local}) | readAt={sync._text(visual.get('readAt'))} | projectNo={project}" if visual_used else
                            f"{detail['path']}#fields.单位类别 | 已保存来源详情 | observedAt={detail['observedAt']} | projectNo={project}"), verified_at)
            elif visual_used:
                if unit_cloud != unit_local:
                    plan.diff(project, "案卷主表", project, M["unit"], unit_cloud, unit_local, "单位类别与详情截图视觉确认不同", "不写不更正；视觉确认只补空，交核对")
            elif unit_cloud != unit_local:
                if not corr("案卷主表", record_id, M["key"], project, M["unit"], unit_cloud, unit_local, "单位类别与来源详情不同",
                            f"{detail['path']}#fields.单位类别 | 已保存来源详情 | observedAt={detail['observedAt']}", extra=[unit_ev]):
                    plan.diff(project, "案卷主表", project, M["unit"], unit_cloud, unit_local, "单位类别与来源详情不同")
        form_case = form_evidence.get(project)
        if form_case and form_case.get("inspectionForm"):
            form_local, form_cloud = form_case["inspectionForm"], plan.value("案卷主表", row, M["form"])
            same_manifest = sync._text((form_case.get("manifest") or {}).get("sha256")) == (recorded or "").removeprefix("sha256:")
            record_ev = plan.evidence(sync._text((form_case.get("record") or {}).get("path")), sync._text((form_case.get("record") or {}).get("sha256"))) if same_manifest else None
            how = f"初查电子监督检查记录第 1 页勾选框(Wingdings2 U+F052)：{form_case['record']['path']}"
            if record_ev is None:
                plan.diff(project, "案卷主表", project, M["form"], form_cloud, form_local, "检查形式证据与现行清单或记录摘要不符", "不写；重新扫描证据")
            elif form_cloud is None:
                put("案卷主表", record_id, M["key"], project, M["form"], None, form_local, how, extra=[record_ev])
            elif form_cloud != form_local:
                if not corr("案卷主表", record_id, M["key"], project, M["form"], form_cloud, form_local, "检查形式与记录勾选不同", how, extra=[record_ev]):
                    plan.diff(project, "案卷主表", project, M["form"], form_cloud, form_local, "检查形式与记录勾选不同")

        # ---------------- 复查(主行两项)
        cloud_rdate, cloud_rresult = plan.value("案卷主表", row, M["recheck_date"]), plan.value("案卷主表", row, M["recheck_result"])
        stage_keys = {key for key in inspect_by_key if key.startswith(project + "|")}
        known_keys = {f["key"] for f, _ in facts if f is not None}
        extra_keys = sorted(stage_keys - known_keys)
        for key in extra_keys:
            plan.diff(project, "检查明细", key, None, "云端有", None, "云端检查行不在本地清单", "保留云端行(历史)，不据此推断")
        cloud_has_recheck = any("|复查" in key for key in stage_keys)
        if recheck is not None:
            results = [p["result"] for p in recheck["products"]]
            target_result = "不合格" if "不合格" in results else ("合格" if all(r == "合格" for r in results) else "已开展待结果")
            how = f"清单复查检查日 {recheck['day']}，复查产品逐项结果汇总"
            if any("|复查" in key for key in extra_keys):
                plan.diff(project, "案卷主表", project, M["recheck_result"], cloud_rresult, target_result, "云端另有清单之外的复查行(多轮)", "主行复查项不自动改写")
            else:
                if cloud_rdate is None:
                    put("案卷主表", record_id, M["key"], project, M["recheck_date"], None, recheck["day"], how)
                elif cloud_rdate != recheck["day"]:
                    if not corr("案卷主表", record_id, M["key"], project, M["recheck_date"], cloud_rdate, recheck["day"], "复查日期与清单不同", how):
                        plan.diff(project, "案卷主表", project, M["recheck_date"], cloud_rdate, recheck["day"], "复查日期与清单不同")
                if cloud_rresult in (None, *RECHECK_OPEN):
                    if cloud_rresult != target_result:
                        put("案卷主表", record_id, M["key"], project, M["recheck_result"], cloud_rresult, target_result, how)
                elif cloud_rresult != target_result:
                    if not corr("案卷主表", record_id, M["key"], project, M["recheck_result"], cloud_rresult, target_result, "复查结果云端与清单不同", how):
                        plan.diff(project, "案卷主表", project, M["recheck_result"], cloud_rresult, target_result, "复查结果云端与清单不同")
        elif len(facts) > 1:
            pass   # 清单有复查但读不出：上面已列差异，复查字段不写
        elif cloud_has_recheck or cloud_rdate is not None or cloud_rresult in TERMINAL:
            if cloud_rresult != "无需复查" or cloud_has_recheck or cloud_rdate is not None:
                plan.diff(project, "案卷主表", project, M["recheck_result"], {"复查日期": cloud_rdate, "复查结果": cloud_rresult, "云端复查行": cloud_has_recheck}, None,
                          "云端有复查而本地清单无复查记录", "保留云端值(历史可信)，本地待补证据")
        elif initial_label == "合格":
            stages = {*((record.get("source") or {}).get("projectInspectionStages") or []), *((record.get("source") or {}).get("sourceInspectionStages") or [])}
            if "RECHECK" in stages:
                plan.diff(project, "案卷主表", project, M["recheck_result"], cloud_rresult, "来源登记过复查阶段", "初查合格但来源出现复查阶段", "不写无需复查；交核对")
            elif cloud_rresult != "无需复查":
                put("案卷主表", record_id, M["key"], project, M["recheck_result"], cloud_rresult, "无需复查",
                    f"初查合格({unified_how or '清单逐项'})，清单与来源阶段登记均无复查", extra=unified_ev)
        elif initial_label == "不合格":
            if layout is None:
                layout = _ws.BusinessLayout.from_root(root)
            state, note = _recheck_state(layout, project, record, today)
            if state is None:
                plan.diff(project, "案卷主表", project, M["recheck_result"], cloud_rresult, note, "初查不合格、复查状态待定", "不写；交核对")
            elif cloud_rresult != state:
                put("案卷主表", record_id, M["key"], project, M["recheck_result"], cloud_rresult, state, f"初查不合格、清单无复查记录；{note}", extra=unified_ev)
        # 初查未决：复查结果不写

        # ---------------- 检查行与产品行
        cloud_team = plan.value("案卷主表", row, M["team"])
        manifest_team = sync.BRIGADE_NAMES.get(sync._text((manifest.get("case") or {}).get("brigadeCode")))
        for inspection in (f for f, _ in facts if f is not None):
            is_initial = inspection["tag"] == "initial"
            rids = inspect_by_key.get(inspection["key"], [])
            product_rids = {key: rid for key, rid in product_by_key.items() if key.startswith(inspection["key"] + "|")}
            if not rids and not product_rids:
                plan.diff(project, "检查明细", inspection["key"], None, None, "清单有该次检查", "云端缺检查行", "由子行创建(feishu-child-build)补建")
                continue
            if len(rids) != 1:
                plan.diff(project, "检查明细", inspection["key"], None, rids, None, "检查键重复或只有产品行", "不写；交核对")
                continue
            counts["inspectionsCompared"] += 1
            rid, irow = rids[0], inspect_rows[rids[0]]
            products = [dict(p, result=r) for p, r in zip(inspection["products"], sync.product_results(inspection["products"], unified, is_initial))]
            stage_path = inspection["stageKey"]

            def iput(field: str, old: Any, new: Any, how: str, *, extra: Any = None, negative: bool = False, rid: str = rid, key: str = inspection["key"]) -> None:
                put("检查明细", rid, I["key"], key, I[field], old, new, how, extra=extra, negative=negative)

            def ival(field: str, irow: dict[str, Any] = irow) -> Any:
                return plan.value("检查明细", irow, I[field])

            def icorr(field: str, old: Any, new: Any, kind: str, how: str, *, extra: Any = None, negative: bool = False, rid: str = rid, key: str = inspection["key"]) -> bool:
                return corr("检查明细", rid, I["key"], key, I[field], old, new, kind, how, extra=extra, negative=negative)

            if ival("date") != inspection["day"]:
                if icorr("date", ival("date"), inspection["day"], "检查日期与清单不同", f"{stage_path}.inspectionDate 以清单为准"):
                    if ival("date_source") != SYSTEM and not icorr("date_source", ival("date_source"), SYSTEM, "检查日期来源", "日期已按清单更正，来源统一为监督系统"):
                        iput("date_source", ival("date_source"), SYSTEM, "日期已按清单更正，来源统一为监督系统")
                    if ival("month") != inspection["day"][:7] and not icorr("month", ival("month"), inspection["day"][:7], "检查月份", "日期已按清单更正，月份随之更正"):
                        iput("month", ival("month"), inspection["day"][:7], "日期已按清单更正，月份随之更正")
                else:
                    plan.diff(project, "检查明细", inspection["key"], I["date"], ival("date"), inspection["day"], "检查日期与清单不同")
            else:
                if ival("date_source") != SYSTEM:
                    iput("date_source", ival("date_source"), SYSTEM, f"{stage_path}.inspectionDate 与云端一致；旧来源标签 {ival('date_source')!r} 记入校准履历")
                if ival("month") != inspection["day"][:7]:
                    iput("month", ival("month"), inspection["day"][:7], f"由清单检查日 {inspection['day']} 取年月")
            if ival("source") != SYSTEM:
                iput("source", ival("source"), SYSTEM, f"{stage_path} 来源=本案已核验清单(监督系统事实)；旧标签 {ival('source')!r} 记入校准履历")
            result, why = sync._inspection_result(products, unified, is_initial)
            cloud_iresult = ival("result")
            if result is None:
                plan.diff(project, "检查明细", inspection["key"], I["result"], cloud_iresult, why, "初查结果本地证据互相冲突")
            elif result in TERMINAL:
                if cloud_iresult in (None, "待核实"):
                    iput("result", cloud_iresult, result, why, extra=unified_ev if is_initial else None)
                elif cloud_iresult != result:
                    if not icorr("result", cloud_iresult, result, "检查结果云端与本地证据不同", why, extra=unified_ev if is_initial else None):
                        plan.diff(project, "检查明细", inspection["key"], I["result"], cloud_iresult, result, "检查结果云端与本地证据不同")
            elif cloud_iresult is None:
                iput("result", None, "待核实", f"{why}：登记为未决状态，不是结论")
            same_count = len(product_rids) == len(products)
            if ival("count") is None:
                iput("count", None, len(products), f"{stage_path}.products 清单产品条数")
            elif ival("count") != len(products):
                if not icorr("count", ival("count"), len(products), "产品数与清单不同", f"{stage_path}.products 清单产品条数"):
                    plan.diff(project, "检查明细", inspection["key"], I["count"], ival("count"), len(products), "产品数与清单不同")
            local_keys = [p["key"] for p in products]
            all_present = all(len(product_by_key.get(k, [])) == 1 for k in local_keys)
            if not same_count:
                if all_present:
                    for extra_key in sorted(k for k in product_rids if k not in local_keys):
                        plan.diff(project, "产品明细", extra_key, None, "云端有", None, "云端多余产品行(清单没有这一产品，已核证据)",
                                  "该行不是清单中的产品；删除记录不可逆，保留并等用户选择；清单内产品行照常逐项校准")
                else:
                    plan.diff(project, "产品明细", inspection["key"], None, sorted(product_rids), local_keys, "云端产品行数与清单不同", "产品行不逐项校准；保留云端行")
            bad_known = all(p["result"] in TERMINAL for p in products)
            if bad_known and same_count and ival("count") in (None, len(products)):
                bad = sum(1 for p in products if p["result"] == "不合格")
                cloud_bad = ival("bad")
                if cloud_bad is None or (cloud_bad != bad and cloud_iresult in (None, "待核实")):
                    iput("bad", cloud_bad, bad, f"{stage_path}.products[*].result 产品均有明确结果，按不合格产品计数", extra=unified_ev if is_initial else None, negative=(bad == 0))
                elif cloud_bad != bad:
                    if not icorr("bad", cloud_bad, bad, "不合格产品数与清单不同", f"{stage_path}.products[*].result 产品均有明确结果，按不合格产品计数", extra=unified_ev if is_initial else None, negative=(bad == 0)):
                        plan.diff(project, "检查明细", inspection["key"], I["bad"], cloud_bad, bad, "不合格产品数与清单不同")
            methods = {p["method"] for p in products if p["method"]}
            if methods and all(p["method"] for p in products):
                label = "抽样送检、现场判定" if len(methods) == 2 else next(iter(methods))
                if ival("method") is None:
                    iput("method", None, label, f"{stage_path}.products[*].method 按产品检查方式汇总")
                elif ival("method") != label:
                    ruling = plan.resolve(project, "检查明细", inspection["key"], I["method"], ival("method"), label)
                    if ruling is None:
                        if not icorr("method", ival("method"), label, "检查方式与清单不同", f"{stage_path}.products[*].method 按产品检查方式汇总"):
                            plan.diff(project, "检查明细", inspection["key"], I["method"], ival("method"), label, "检查方式与清单不同")
                    elif ruling[0] == "local":
                        iput("method", ival("method"), label, ruling[2], extra=ruling[1])
            if ival("team") is None and manifest_team is not None and manifest_team == cloud_team:
                iput("team", None, manifest_team, "清单管辖与云端主行所属大队一致")
            elif ival("team") is not None and cloud_team is not None and ival("team") != cloud_team:
                plan.diff(project, "检查明细", inspection["key"], I["team"], ival("team"), cloud_team, "检查行大队与主行所属大队不同")
            if not is_initial and ival("round") is None and not any("|复查" in key for key in extra_keys):
                iput("round", None, 1, "本案清单内唯一一次复查，序次 1")
            if not all_present:
                continue
            for product in products:
                prids = product_by_key.get(product["key"], [])
                if len(prids) != 1:
                    plan.diff(project, "产品明细", product["key"], None, prids, _text_or_none(product["raw"].get("name")), "产品键缺失或重复", "不写；交核对")
                    continue
                prid, prow = prids[0], product_rows[prids[0]]
                raw, path = product["raw"], f"{stage_path}.products[{product['seq'] - 1}]"

                def pval(field: str, prow: dict[str, Any] = prow) -> Any:
                    return plan.value("产品明细", prow, P[field])

                def pput(field: str, old: Any, new: Any, how: str, *, extra: Any = None, prid: str = prid, key: str = product["key"]) -> None:
                    put("产品明细", prid, P["key"], key, P[field], old, new, how, extra=extra)

                def pcorr(field: str, old: Any, new: Any, kind: str, how: str, *, extra: Any = None, prid: str = prid, key: str = product["key"]) -> bool:
                    return corr("产品明细", prid, P["key"], key, P[field], old, new, kind, how, extra=extra)

                def ruled(field: str, cloud: Any, local: Any, kind: str, action: str = KEEP, key: str = product["key"]) -> bool:
                    """云端与清单不同的一格：有有效裁决就按裁决办(采用清单值则写)，否则列差异。返回是否已有裁决。"""
                    ruling = plan.resolve(project, "产品明细", key, P[field], cloud, local)
                    if ruling is None:
                        if not pcorr(field, cloud, local, kind, f"{path}.{field} 清单值"):
                            plan.diff(project, "产品明细", key, P[field], cloud, local, kind, action)
                        return False
                    if ruling[0] == "local":
                        pput(field, cloud, local, ruling[2], extra=ruling[1])
                    return True

                if _norm(pval("name")) != _norm(raw.get("name")):
                    local_name = sync._text(raw.get("name"))
                    ruling = plan.resolve(project, "产品明细", product["key"], P["name"], pval("name"), local_name)
                    if ruling is None:
                        if pcorr("name", pval("name"), local_name, "产品名称与清单不同", f"{path}.name 清单产品名称"):
                            pass   # 已排入更正：与其余事实字段同一证据，整行一并校准
                        else:
                            plan.diff(project, "产品明细", product["key"], P["name"], pval("name"), local_name, "同序号产品名称与清单不同(可能行序错位)", "该产品行不逐项校准；" + KEEP)
                            continue
                    elif ruling[0] == "local":
                        pput("name", pval("name"), local_name, ruling[2], extra=ruling[1])   # 合同登记 evidenceCorrection：按裁决证据逐格更正
                counts["productsCompared"] += 1
                if pval("source") != SYSTEM:
                    pput("source", pval("source"), SYSTEM, f"{path} 来源=本案已核验清单(监督系统事实)；旧标签 {pval('source')!r} 记入校准履历")

                cloud_presult = pval("result")
                if product["result"] in TERMINAL:
                    from_case = product["result"] != sync.PRODUCT_RESULT.get(raw.get("result"))
                    if cloud_presult in (None, "待核实"):
                        pput("result", cloud_presult, product["result"], f"{path}.result" + (f"；清单未给终局，由{unified_how}唯一确定" if from_case else " 清单产品结果"),
                             extra=unified_ev if from_case else None)
                    elif cloud_presult != product["result"]:
                        ruled("result", cloud_presult, product["result"], "产品结果云端与本地证据不同")
                elif cloud_presult is None and product["result"]:
                    pput("result", None, product["result"], f"{path}.result 清单未给终局：登记为未决状态，不是结论")
                if product["method"]:
                    if pval("method") is None:
                        pput("method", None, product["method"], f"{path}.method 清单检查方式")
                    elif pval("method") != product["method"]:
                        ruled("method", pval("method"), product["method"], "产品检查方式与清单不同")
                for field, raw_name, label in (("model", "modelSpec", "规格型号"), ("producer", "nominalProducer", "标称生产者"), ("location", "location", "所在部位"),
                                               ("problem", "problemDescription", "问题描述")):
                    local = sync._text(raw.get(raw_name))
                    if not local:
                        continue
                    if pval(field) is None:
                        pput(field, None, local, f"{path}.{raw_name} 清单{label}")
                    elif _norm(pval(field)) != _norm(local):
                        if field == "problem" and _norm(pval(field)) in _norm(local):
                            counts["problemExcerpts"] += 1   # 云端是清单文字里的正文摘录：同一内容
                            continue
                        ruled(field, pval(field), local, f"{label}与清单不同")
    plan.flush_corrections()
    # ---------------- 输出
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = list(plan.entries.values())
    plan_file = None
    if entries:
        path = out_dir / "calibrate-plan.json"
        path.write_text(json.dumps({"schemaVersion": sync.FIELD_PLAN_SCHEMA, "createdAt": sync.now(), "purpose": "监督事实校准(本地已核证据 → 飞书)", "entries": entries},
                                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        plan_file = {"path": str(path), "sha256": sync.file_sha(path), "entries": len(entries)}
    by_field: dict[str, int] = {}
    for item in plan.writes:
        by_field[f"{item['table']}.{item['field']}"] = by_field.get(f"{item['table']}.{item['field']}", 0) + 1
    by_kind: dict[str, int] = {}
    for item in plan.diffs:
        by_kind[f"{item['table']}.{item['field']}：{item['kind']}"] = by_kind.get(f"{item['table']}.{item['field']}：{item['kind']}", 0) + 1
    report = {"schema": REPORT_SCHEMA, "builtAt": sync.now(), "externalCalls": "read-only fresh scan", "target": dict(target),
              "inputs": {"contract": {"path": str(contract_path), "sha256": contract_sha}, "waterline": {"path": str(waterline_path), "sha256": sync.file_sha(waterline_path)},
                         "resolutions": {"path": str(resolutions_path), "sha256": sync.file_sha(resolutions_path)} if resolutions_path else None},
              "fresh": {"cloudCaseRows": len(main_rows), "cloudInspectionRows": len(inspect_rows), "cloudProductRows": len(product_rows)},
              "counts": {**counts, "plannedRecords": len(entries), "plannedFields": len(plan.writes), "differences": len(plan.diffs), "resolvedKeepCloud": len(plan.resolved), "corrections": sum(1 for w in plan.writes if w.get("correction")), "correctionsHeld": plan.held,
                         "casesLeftAlone": len(plan.skipped)},
              "plannedByField": dict(sorted(by_field.items())), "differencesByKind": dict(sorted(by_kind.items())),
              "plan": plan_file, "writes": plan.writes, "differences": plan.diffs, "resolved": plan.resolved, "casesLeftAlone": plan.skipped,
              "status": "OK" if not entries else "PLANNED"}
    (out_dir / "calibrate-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def _text_or_none(value: Any) -> str | None:
    return sync._text(value) or None
