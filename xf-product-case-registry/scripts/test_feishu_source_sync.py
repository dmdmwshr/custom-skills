"""Offline mechanical tests for feishu_source_sync v2.

The real ``LarkCliTransport`` runs against a fake *service* behind its subprocess runner. The fake
validates the wire shape of every CellValue like the official CLI help describes and never
corrects a wrong shape, so a formatting defect cannot be hidden. Results here are NOT a P18
real-cloud result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.feishu_source_sync as sync

CONTRACT_CANDIDATES = [
    os.environ.get("P18_CONTRACT", ""),
    "D:/ws/05-temp/20261105_案卷汇总_不合格名录与下载核查/review/p18-actual-field-contract-observation-20261106/人工字段与同步保护合同-结构已回读.json",
    "/mnt/d/ws/05-temp/20261105_案卷汇总_不合格名录与下载核查/review/p18-actual-field-contract-observation-20261106/人工字段与同步保护合同-结构已回读.json",
]
CONTRACT = next((Path(c) for c in CONTRACT_CANDIDATES if c and Path(c).is_file()), None)
pytestmark = pytest.mark.skipif(CONTRACT is None, reason="P18 保护合同副本不可达")

_REAL_RELEASE_GATE = sync.require_user_acceptance_for_release


@pytest.fixture(autouse=True)
def _engine_tests_exercise_the_release_mechanics_not_the_user_gate(monkeypatch, request):
    """旧引擎用例验证放行机制本身(精确意图、证据形态)；用户风险接受门禁另由 gate 用例用真实函数验证。"""
    if not any(tag in request.node.name for tag in ("users_own_risk_acceptance", "assistant_signed")):
        monkeypatch.setattr(sync, "require_user_acceptance_for_release", lambda spec, path: None)
    if "ledger_root" not in request.node.name:
        # 引擎用例在“自己的隔离测试账”上运行：这里显式 initialize() 并只通过内部 isolated_test_ledger 开关通行，
        # 真实的 validate_root() 照常执行(不被 mock)；正式根身份规则由 ledger_root 用例直接验证。
        real_run, real_build = sync.run, sync.build_basic_specs

        def run_isolated(spec_path, **kw):
            if kw.get("ledger_dir") is not None and json.loads(Path(spec_path).read_text(encoding="utf-8")).get("mode") != "plan":
                sync.Ledger(kw["ledger_dir"]).initialize()
            return real_run(spec_path, **kw, isolated_test_ledger=True)

        def build_isolated(**kw):
            if kw.get("ledger_dir") is not None:
                sync.Ledger(kw["ledger_dir"]).initialize()
            return real_build(**kw, isolated_test_ledger=True)

        monkeypatch.setattr(sync, "run", run_isolated)
        monkeypatch.setattr(sync, "build_basic_specs", build_isolated)


CASE, CHECK, PRODUCT = "tbl30eWyoeTZqxmU", "tbl6cARXeBhjWunC", "tbl6jr76MIVK1xXi"
KEY, UNIT, HANDLER, RESULT, DATE = "fld1lHXHpk", "fld2Lo4dpD", "fldHTq0ECh", "fld77TOx5G", "fld5azNPuE"
HUMAN_NET, HUMAN_NOTE, HUMAN_CHECKBOX, DERIVED = "fld4mVYy5Y", "fldW2kJC0V", "fldrKL9rDE", "fld3Vvqol2"
CHECK_KEY, CHECK_NUMBER = "fld5ujrPv4", "fld1jdSi33"
BASE = "XOZ1bfapIasXW1ssaeEcovPinLc"
REC, REC2, CREC = "recTEST000001", "recTEST000002", "recTEST000003"
STABLE = "SYNTH-0001"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture()
def meta(contract) -> dict:
    return {t["tableId"]: {f["fieldId"]: f for f in t["fields"]} for t in contract["tables"].values()}


class FakeService:
    """Stands behind the lark-cli runner. Strict about wire shapes, honest about identity."""

    def __init__(self, meta, rows, **behaviour):
        self.meta, self.rows = meta, copy.deepcopy(rows)
        self.b = behaviour
        self.calls: list[list[str]] = []
        self.upserts: list[tuple[str, str, dict]] = []
        self.hooks_at_upsert = None

    def __call__(self, argv, capture_output=True, text=True, timeout=None):
        self.calls.append(argv)
        verb = argv[2]
        opt = lambda name: [argv[i + 1] for i, a in enumerate(argv) if a == name]  # noqa: E731
        table = opt("--table-id")[0]
        if verb == "+record-get":
            return self._get(table, opt("--record-id"))
        if verb == "+record-upsert":
            return self._upsert(table, opt("--record-id")[0], json.loads(opt("--json")[0]))
        if verb == "+record-history-list":
            return self._ok({"items": self.b.get("history", []), "has_more": False})
        return SimpleNamespace(returncode=1, stdout=json.dumps({"ok": False}), stderr="")

    def _ok(self, data):
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": data}, ensure_ascii=False), stderr="")

    def _get(self, table, ids):
        if self.b.get("fail_get_after_write") and self.upserts:
            return SimpleNamespace(returncode=1, stdout=json.dumps({"ok": False}), stderr="")
        shown = [i for i in ids if not (self.b.get("drop_row") == i)]
        if self.b.get("permute"):
            shown = list(reversed(shown))
        if self.b.get("extra_unknown"):
            self.rows[(table, "recUNKNOWN9999")] = {}
            shown = [*shown, "recUNKNOWN9999"]
        if self.b.get("dup_ids") and shown:
            shown = [*shown, shown[0]]
        fields = sorted(set(self.meta[table]) | {fid for i in shown for fid in self.rows.get((table, i), {})})
        fields = [f for f in fields if f not in self.b.get("omit_fields", ())
                  and not (self.upserts and f in self.b.get("omit_fields_after_write", ()))]
        if self.b.get("dup_fields") and fields:
            fields = [*fields, fields[0]]
        data = {"fields": [self.meta[table].get(f, {}).get("name", f) for f in fields], "field_id_list": fields,
                "field_type_list": [self.meta[table].get(f, {}).get("type", "text") for f in fields],
                "data": [[self.rows[(table, i)].get(f) for f in fields] for i in shown],
                "record_id_list": shown, "has_more": False, "timezone": "Asia/Shanghai"}
        if self.b.get("no_ids"):
            del data["record_id_list"]
        if self.b.get("no_field_ids"):
            del data["field_id_list"]
        if self.b.get("ragged") and data["data"]:
            data["data"][0] = data["data"][0][:-1]
        if self.b.get("count_mismatch") and data["record_id_list"]:
            data["record_id_list"] = data["record_id_list"][:-1]
        return self._ok(data)

    def _upsert(self, table, record, fields):
        self.upserts.append((table, record, copy.deepcopy(fields)))
        if self.hooks_at_upsert:
            self.hooks_at_upsert(table, record)
        if self.b.get("timeout_for") == record:
            raise subprocess.TimeoutExpired("lark-cli", 1)
        if self.b.get("fail_for") == record:
            return SimpleNamespace(returncode=1, stdout=json.dumps({"ok": False}), stderr="")
        if self.b.get("crash_for") == record:
            raise KeyboardInterrupt
        for fid, wire in fields.items():
            kind = self.meta[table][fid]["type"]
            if wire is None:   # null 清空单元格(官方 CLI：允许清空时 null 清除)
                if self.b.get("ignore_writes_for") != record:
                    self.rows[(table, record)][fid] = None
                continue
            if kind == "select":
                assert isinstance(wire, list) and wire and all(isinstance(x, str) for x in wire), f"select 需要数组：{wire!r}"
            elif kind == "text":
                assert isinstance(wire, str), "text 需要字符串"
            elif kind == "number":
                assert isinstance(wire, (int, float)) and not isinstance(wire, bool), "number 需要数字"
            elif kind == "datetime":
                assert isinstance(wire, str) and len(wire) == 16 and wire[10] == " ", f"datetime 需要 'YYYY-MM-DD HH:MM'：{wire!r}"
                wire = wire.replace(" ", "T") + ":00.000+08:00"
            else:
                raise AssertionError(f"不应写入 {kind}")
            if self.b.get("ignore_writes_for") != record:
                self.rows[(table, record)][fid] = wire
        if self.b.get("mutate"):
            self.b["mutate"](self.rows[(table, record)])
        if self.b.get("apply_then_timeout_for") == record:
            raise subprocess.TimeoutExpired("lark-cli", 1)
        return self._ok({"record": {"record_id": record}})


def base_rows():
    case = {KEY: STABLE, UNIT: "旧单位", HANDLER: "旧承办人", RESULT: ["合格"], DATE: "2026-01-05T00:00:00.000+08:00",
            HUMAN_NET: ["是"], HUMAN_NOTE: "人工说明", HUMAN_CHECKBOX: True, DERIVED: "0"}
    return {(CASE, REC): dict(case), (CASE, REC2): {**case, KEY: "SYNTH-0002"},
            (CHECK, CREC): {CHECK_KEY: "SYNTH-CHK-1", CHECK_NUMBER: 3, "fld1RPp2yY": ["合格"], "fld4atEDRT": ["是"]}}


def basis(ref="源详情#1", state="observed", **extra):
    return {"kind": "source-observed", "valueState": state, "sourceRef": ref, "observedAt": "2026-11-06T08:00:00Z", **extra}


def make_spec(tmp_path, mode="execute", **override):
    spec = {
        "schemaVersion": sync.SPEC_SCHEMA, "mode": mode,
        "contract": {"path": str(CONTRACT), "sha256": sha(CONTRACT)},
        "target": {"baseToken": BASE, "profile": "p18-test", "identity": "user"},
        "syntheticOnly": True, "syntheticKeyPattern": r"^SYNTH-",
        "limits": {"maxRecords": 5},
        "logDir": str(tmp_path / "log"),
        "allowlist": {CASE: {"records": [REC, REC2], "fields": [UNIT, HANDLER, RESULT, DATE]}},
        "updates": [{
            "tableId": CASE, "recordId": REC, "stableKey": {"fieldId": KEY, "value": STABLE},
            "fields": {UNIT: {"new": "新单位", "basis": basis()}, RESULT: {"new": "不合格", "basis": basis()}},
        }],
    }
    spec.update(override)
    return spec


def write_spec(tmp_path, spec, name="spec.json"):
    path = tmp_path / name
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def service(meta, **behaviour):
    return FakeService(meta, base_rows(), **behaviour)


def run(tmp_path, spec, svc, *, flag="execute", name="spec.json"):
    transport = sync.LarkCliTransport("/x/lark-cli", "p18-test", "user", runner=svc)
    kwargs = {"execute_flag": flag == "execute", "reconcile_flag": flag == "reconcile"}
    return sync.run(write_spec(tmp_path, spec, name), cli="unused", transport=transport, ledger_dir=tmp_path / "ledger", **kwargs)


def refused(tmp_path, spec, meta, match=None, flag="execute"):
    svc = service(meta)
    with pytest.raises(sync.SyncError, match=match):
        run(tmp_path, spec, svc, flag=flag)
    assert svc.calls == [], "拒绝必须发生在任何外部调用之前"


def read_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ledger_events(tmp_path, record=REC):
    key = sync.Ledger.target_key(BASE, CASE, record)
    return sync.Ledger(tmp_path / "ledger").events(key)


# ------------------------------------------------------------------ plan / basic execute


def test_plan_makes_no_external_call_and_writes_no_ledger(tmp_path, meta):
    svc = service(meta)
    result = run(tmp_path, make_spec(tmp_path, "plan"), svc)
    assert result["externalCalls"] == 0 and svc.calls == [] and result["executable"] is True
    assert result["requests"][0]["wire"][RESULT] == ["不合格"]
    assert set(sync.ALWAYS_DENIED) <= set(result["denylist"])
    assert not (tmp_path / "ledger").exists() and not (tmp_path / "log").exists()


def test_execute_requires_explicit_flag(tmp_path, meta):
    svc = service(meta)
    with pytest.raises(sync.SyncError, match="--execute"):
        run(tmp_path, make_spec(tmp_path), svc, flag="none")
    assert svc.calls == []


def test_execute_updates_only_target_fields_with_official_wire_shapes(tmp_path, meta):
    svc = service(meta)
    report = run(tmp_path, make_spec(tmp_path), svc)
    row = svc.rows[(CASE, REC)]
    assert report["status"] == "OK"
    assert row[UNIT] == "新单位" and row[RESULT] == ["不合格"]
    assert row[HANDLER] == "旧承办人" and row[HUMAN_NET] == ["是"] and row[HUMAN_NOTE] == "人工说明" and row[HUMAN_CHECKBOX] is True
    assert svc.rows[(CASE, REC2)] == base_rows()[(CASE, REC2)]
    assert svc.upserts == [(CASE, REC, {UNIT: "新单位", RESULT: ["不合格"]})]      # select 以数组上线
    detail = report["records"][0]["fieldDetail"][RESULT]
    assert detail["old"] == ["合格"] and detail["new"] == "不合格" and detail["wire"] == ["不合格"] and detail["type"] == "select"
    log = Path(tmp_path / "log")
    for name in ("before.json", "after.json", "requests.jsonl", "report.json"):
        assert (log / name).is_file()
    assert [e["event"] for e in ledger_events(tmp_path)] == ["intent", "final"]


def test_fake_service_does_not_correct_a_wrong_select_shape(meta):
    svc = service(meta)
    transport = sync.LarkCliTransport("/x/lark-cli", "p", "user", runner=svc)
    with pytest.raises(AssertionError, match="select 需要数组"):
        transport.upsert(BASE, CASE, REC, {RESULT: "不合格"})


def test_unchanged_value_is_not_written_and_select_form_is_compared_as_value(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {UNIT: {"new": "旧单位", "basis": basis()}, RESULT: {"new": "合格", "basis": basis()}}
    svc = service(meta)
    report = run(tmp_path, spec, svc)
    assert svc.upserts == [] and report["records"][0]["status"] == "UNCHANGED"
    assert ledger_events(tmp_path) == []


def test_datetime_number_and_text_use_their_own_wire_and_comparison(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["allowlist"] = {CASE: {"records": [REC], "fields": [DATE, UNIT]}, CHECK: {"records": [CREC], "fields": [CHECK_NUMBER, "fld1RPp2yY"]}}
    spec["updates"] = [
        {"tableId": CASE, "recordId": REC, "stableKey": {"fieldId": KEY, "value": STABLE},
         "fields": {DATE: {"new": "2026-01-05", "basis": basis()}}},                      # 与云端 00:00 相同 -> 不写
        {"tableId": CHECK, "recordId": CREC, "stableKey": {"fieldId": CHECK_KEY, "value": "SYNTH-CHK-1"},
         "fields": {CHECK_NUMBER: {"new": 4, "basis": basis()}, "fld1RPp2yY": {"new": "不合格", "basis": basis()}}}]
    svc = service(meta)
    run(tmp_path, spec, svc)
    assert svc.upserts == [(CHECK, CREC, {CHECK_NUMBER: 4, "fld1RPp2yY": ["不合格"]})]
    spec2 = copy.deepcopy(spec)
    spec2["logDir"] = str(tmp_path / "log2")
    spec2["updates"] = [spec["updates"][0]]
    spec2["updates"][0]["fields"] = {DATE: {"new": "2026-02-01 08:30", "basis": basis()}}
    svc2 = service(meta)
    run(tmp_path, spec2, svc2, name="spec2.json")
    assert svc2.upserts == [(CASE, REC, {DATE: "2026-02-01 08:30"})]
    assert svc2.rows[(CASE, REC)][DATE] == "2026-02-01T08:30:00.000+08:00"


@pytest.mark.parametrize("field,new,match", [
    (RESULT, ["不合格"], "单个非空选项"),
    (RESULT, "", "单个非空选项"),
    (UNIT, 5, "非空字符串"),
    (UNIT, ["x"], "非空字符串"),
    (DATE, "2026/01/05", "datetime"),
    (DATE, "2026-13-40", "datetime"),
    (DATE, 20260105, "datetime"),
    (CHECK_NUMBER, True, "有限数字"),
    (CHECK_NUMBER, "4", "有限数字"),
    (CHECK_NUMBER, float("nan"), "有限数字"),
])
def test_ambiguous_or_wrong_typed_values_are_refused(tmp_path, meta, field, new, match):
    table, record, key, stable = (CHECK, CREC, CHECK_KEY, "SYNTH-CHK-1") if field == CHECK_NUMBER else (CASE, REC, KEY, STABLE)
    spec = make_spec(tmp_path)
    spec["allowlist"] = {table: {"records": [record], "fields": [field]}}
    spec["updates"] = [{"tableId": table, "recordId": record, "stableKey": {"fieldId": key, "value": stable},
                        "fields": {field: {"new": new, "basis": basis()}}}]
    refused(tmp_path, spec, meta, match)


# ------------------------------------------------------------------ allow / deny rules


@pytest.mark.parametrize("field_id", sorted(sync.ALWAYS_DENIED))
def test_every_human_and_relation_field_is_denied(tmp_path, meta, field_id):
    table = next(t for t, fields in meta.items() if field_id in fields)
    stable = {CASE: KEY, CHECK: CHECK_KEY, PRODUCT: "fldqlwHjUW"}[table]
    spec = make_spec(tmp_path)
    spec["allowlist"] = {table: {"records": [REC], "fields": [field_id]}}
    spec["updates"] = [{"tableId": table, "recordId": REC, "stableKey": {"fieldId": stable, "value": STABLE},
                        "fields": {field_id: {"new": "是", "basis": basis()}}}]
    refused(tmp_path, spec, meta, "永久禁写")


def test_human_field_mixed_into_updates_only(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"][HUMAN_NET] = {"new": "是", "basis": basis()}
    refused(tmp_path, spec, meta, "永久禁写")


def test_field_name_instead_of_id_rejected(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["allowlist"][CASE]["fields"] = ["单位名称"]
    refused(tmp_path, spec, meta, "fieldId")


def test_unknown_field_rejected(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["allowlist"][CASE]["fields"] = ["fldNEWUNKNOWN1"]
    refused(tmp_path, spec, meta, "默认拒写")


def test_derived_admin_legacy_and_structural_fields_rejected(tmp_path, contract, meta):
    for cls in ("derived-readonly", "admin-only", "legacy-hidden-protected", "structural-relation"):
        field = next(f for f in contract["tables"]["案卷主表"]["fields"] if f["class"] == cls and f["fieldId"] not in sync.ALWAYS_DENIED)
        spec = make_spec(tmp_path)
        spec["allowlist"][CASE]["fields"] = [field["fieldId"]]
        spec["updates"][0]["fields"] = {field["fieldId"]: {"new": "x", "basis": basis()}}
        refused(tmp_path, spec, meta, "source-sync-whitelist")


def test_link_whitelist_field_not_written_by_this_version(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["allowlist"] = {CHECK: {"records": [REC], "fields": ["fld1kH9BIS"]}}
    spec["updates"] = [{"tableId": CHECK, "recordId": REC, "stableKey": {"fieldId": CHECK_KEY, "value": STABLE},
                        "fields": {"fld1kH9BIS": {"new": "x", "basis": basis()}}}]
    refused(tmp_path, spec, meta, "标量")


@pytest.mark.parametrize("patch,match", [
    (lambda s: s.pop("allowlist"), "allowlist"),
    (lambda s: s.update(allowlist={}), "allowlist"),
    (lambda s: s["allowlist"][CASE].update(fields=[]), "fieldId 白名单为空"),
    (lambda s: s["allowlist"][CASE].update(records=[]), "record 白名单为空"),
    (lambda s: s["allowlist"][CASE].update(records=["<待填合成record_id>", REC]), "record_id 格式"),
    (lambda s: s["allowlist"][CASE].update(records=[REC, REC]), "重复"),
    (lambda s: s["target"].update(baseToken="OTHERBASETOKEN"), "Base"),
    (lambda s: s["target"].pop("profile"), "profile"),
    (lambda s: s.update(updates=[]), "updates"),
    (lambda s: s.update(updates=s["updates"] * 2), "重复"),
    (lambda s: s["updates"][0].update(recordId="recNOTLISTED001"), "不在白名单"),
    (lambda s: s["updates"][0].pop("stableKey"), "稳定键"),
    (lambda s: s["updates"][0]["stableKey"].update(fieldId=UNIT), "稳定键"),
    (lambda s: s["updates"][0]["stableKey"].update(value="REAL-0001"), "合成"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "x"}}), "basis"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "x", "basis": {"kind": "source-observed", "sourceRef": "a", "observedAt": "b"}}}), "valueState"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "x", "basis": {**basis(), "kind": "local-processing"}}}), "basis"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "x", "basis": basis(state="guess")}}), "valueState"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "", "basis": basis()}}), "非空"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": None, "basis": basis()}}), "非空"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"basis": basis()}}), "需要 new"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "否", "basis": basis()}}), "明确否定"),
    (lambda s: s["updates"][0]["fields"].update({HANDLER: {"new": "0", "basis": basis()}}), "明确否定"),
    (lambda s: s["updates"][0]["fields"].update({"fld9ZZZZZZ": {"new": "x", "basis": basis()}}), "不在白名单"),
    (lambda s: s["limits"].update(maxRecords=21), "maxRecords"),
    (lambda s: s.pop("logDir"), "logDir"),
    (lambda s: s.update(mode="bulk"), "mode"),
    (lambda s: s.update(schemaVersion="FeishuSourceSyncSpecV1"), "格式无效"),
    (lambda s: s.update(syntheticKeyPattern=""), "syntheticKeyPattern"),
])
def test_invalid_specs_are_refused_before_any_external_call(tmp_path, meta, patch, match):
    spec = make_spec(tmp_path)
    patch(spec)
    refused(tmp_path, spec, meta, match)


def test_contract_digest_mismatch_refused(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["contract"]["sha256"] = "0" * 64
    refused(tmp_path, spec, meta, "摘要")


# ------------------------------------------------------------------ source value states


def test_explicit_negative_is_allowed_only_with_source_basis(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {HANDLER: {"new": "否", "basis": basis(explicitNegative=True)}}
    svc = service(meta)
    assert run(tmp_path, spec, svc)["status"] == "OK"
    assert svc.rows[(CASE, REC)][HANDLER] == "否"


def test_literal_pending_verification_text_is_a_legitimate_observed_source_value(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {RESULT: {"new": "待核实", "basis": basis("来源页面原文“待核实”")}}
    svc = service(meta)
    run(tmp_path, spec, svc)
    assert svc.rows[(CASE, REC)][RESULT] == ["待核实"]


@pytest.mark.parametrize("state", ["not-provided", "unknown"])
def test_missing_source_value_is_skipped_and_never_overwrites(tmp_path, meta, state):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {HANDLER: {"basis": basis(state=state)}, UNIT: {"new": "新单位", "basis": basis()}}
    svc = service(meta)
    report = run(tmp_path, spec, svc)
    assert svc.upserts == [(CASE, REC, {UNIT: "新单位"})]
    assert svc.rows[(CASE, REC)][HANDLER] == "旧承办人"
    assert report["records"][0]["skippedFields"][HANDLER]["valueState"] == state


def test_missing_value_state_with_a_value_is_ambiguous_and_refused(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {HANDLER: {"new": "x", "basis": basis(state="not-provided")}}
    refused(tmp_path, spec, meta, "不得携带 new")


def test_all_fields_missing_means_no_write_at_all(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {HANDLER: {"basis": basis(state="not-provided")}}
    svc = service(meta)
    report = run(tmp_path, spec, svc)
    assert svc.upserts == [] and report["records"][0]["status"] == "UNCHANGED"


# ------------------------------------------------------------------ strict record identity


@pytest.mark.parametrize("behaviour,match", [
    ({"no_ids": True}, "record_id_list"),
    ({"drop_row": REC}, "读不到"),
    ({"dup_ids": True}, "重复"),
    ({"extra_unknown": True}, "未请求"),
    ({"count_mismatch": True}, "行数不一致"),
    ({"ragged": True}, "行宽"),
    ({"no_field_ids": True}, "field_id_list"),
    ({"dup_fields": True}, "field_id_list"),
])
def test_identity_or_structure_anomalies_fail_closed_before_any_write(tmp_path, meta, behaviour, match):
    svc = service(meta, **behaviour)
    with pytest.raises(sync.RuntimeSyncError, match=match):
        run(tmp_path, make_spec(tmp_path), svc)
    assert svc.upserts == []
    assert read_report(tmp_path / "log" / "report.json")["status"] == "REFUSED_BEFORE_WRITE"
    assert ledger_events(tmp_path) == []


def test_response_order_never_decides_identity(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"].append({"tableId": CASE, "recordId": REC2, "stableKey": {"fieldId": KEY, "value": "SYNTH-0002"},
                            "fields": {UNIT: {"new": "新2", "basis": basis()}}})
    svc = service(meta, permute=True)
    report = run(tmp_path, spec, svc)
    assert report["status"] == "OK"
    assert svc.rows[(CASE, REC)][UNIT] == "新单位" and svc.rows[(CASE, REC2)][UNIT] == "新2"


def test_get_records_never_invents_ids_from_request_order():
    body = {"ok": True, "data": {"fields": ["项目编号"], "data": [["SYNTH-REVIEW"]]}}
    with pytest.raises(sync.RuntimeSyncError, match="record_id_list"):
        sync.parse_get_response(body, ["recSYNTH00001"])


# ------------------------------------------------------------------ stable key, expectedOld, failures


def test_stable_key_mismatch_refused_before_write(tmp_path, meta):
    svc = service(meta)
    spec = make_spec(tmp_path)
    spec["updates"][0]["stableKey"]["value"] = "SYNTH-9999"
    with pytest.raises(sync.RuntimeSyncError, match="稳定键"):
        run(tmp_path, spec, svc)
    assert svc.upserts == [] and len(svc.calls) == 1 and ledger_events(tmp_path) == []


def test_expected_old_conflict_preserves_both_sides(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"][UNIT]["expectedOld"] = "别人刚改过的值"
    svc = service(meta)
    with pytest.raises(sync.RuntimeSyncError, match="expectedOld"):
        run(tmp_path, spec, svc)
    assert svc.upserts == [] and svc.rows[(CASE, REC)][UNIT] == "旧单位"


def test_expected_old_uses_typed_comparison(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"][RESULT]["expectedOld"] = "合格"      # 云端为 ["合格"]
    assert run(tmp_path, spec, service(meta))["status"] == "OK"


def two_record_spec(tmp_path):
    spec = make_spec(tmp_path)
    spec["updates"].append({"tableId": CASE, "recordId": REC2, "stableKey": {"fieldId": KEY, "value": "SYNTH-0002"},
                            "fields": {UNIT: {"new": "新2", "basis": basis()}}})
    return spec


def test_failed_write_stops_without_retry_and_blocks_the_target(tmp_path, meta):
    svc = service(meta, fail_for=REC)
    with pytest.raises(sync.RuntimeSyncError):
        run(tmp_path, two_record_spec(tmp_path), svc)
    assert [u[1] for u in svc.upserts] == [REC]
    report = read_report(tmp_path / "log" / "report.json")
    assert report["status"] == "FAILED" and [r["status"] for r in report["records"]] == ["FAILED", "NOT_ATTEMPTED"]
    assert [e["event"] for e in ledger_events(tmp_path)] == ["intent", "final"] and ledger_events(tmp_path)[-1]["status"] == "FAILED"
    assert ledger_events(tmp_path, REC2)[-1]["status"] == "NOT_ATTEMPTED"


def test_protection_violation_detected_on_readback_and_blocks(tmp_path, meta):
    def clobber(row):
        row[HUMAN_NOTE] = "被同步冲掉"

    svc = service(meta, mutate=clobber)
    with pytest.raises(sync.RuntimeSyncError, match="人工说明|fldW2kJC0V|核实说明"):
        run(tmp_path, make_spec(tmp_path), svc)
    report = read_report(tmp_path / "log" / "report.json")
    assert report["status"] == "FAILED" and report["protectionViolations"]
    assert ledger_events(tmp_path)[-1]["status"] == "VIOLATION"


def test_unchecked_checkbox_null_to_false_is_not_a_human_value_change(tmp_path, meta):
    """服务端首次写入某行会把空复选框读数规范成 false：二者同为未勾选，不算人工值变化；其余变化仍是违规。"""
    def start_unset(svc):
        svc.rows[(CASE, REC)][HUMAN_CHECKBOX] = None

    def normalize(row):
        if row.get(HUMAN_CHECKBOX) is None:
            row[HUMAN_CHECKBOX] = False

    svc = service(meta, mutate=normalize)
    start_unset(svc)
    assert run(tmp_path, make_spec(tmp_path), svc)["status"] == "OK"
    assert svc.rows[(CASE, REC)][HUMAN_CHECKBOX] is False


@pytest.mark.parametrize("before,after", [(True, False), (None, True), (False, True), (True, None)])
def test_checkbox_real_changes_are_still_violations(tmp_path, meta, before, after):
    def clobber(row):
        row[HUMAN_CHECKBOX] = after

    svc = service(meta, mutate=clobber)
    svc.rows[(CASE, REC)][HUMAN_CHECKBOX] = before
    with pytest.raises(sync.RuntimeSyncError):
        run(tmp_path, make_spec(tmp_path), svc)
    assert ledger_events(tmp_path)[-1]["status"] == "VIOLATION"


def test_ignored_write_is_caught_by_readback(tmp_path, meta):
    svc = service(meta, ignore_writes_for=REC)
    with pytest.raises(sync.RuntimeSyncError, match="回读值不等于目标值"):
        run(tmp_path, make_spec(tmp_path), svc)


# ------------------------------------------------------------------ UNKNOWN protection / ledger


def unknown_state(tmp_path, meta, **behaviour):
    svc = service(meta, **({"timeout_for": REC} | behaviour))
    with pytest.raises(sync.RuntimeSyncError):
        run(tmp_path, make_spec(tmp_path), svc)
    return svc


def test_unknown_result_is_recorded_and_not_replayed_by_the_same_spec(tmp_path, meta):
    svc = unknown_state(tmp_path, meta)
    first = (tmp_path / "log" / "report.json").read_bytes()
    assert read_report(tmp_path / "log" / "report.json")["records"][0]["status"] == "UNKNOWN"
    svc2 = service(meta)
    with pytest.raises(sync.SyncError, match="logDir 已存在|未对账"):
        run(tmp_path, make_spec(tmp_path), svc2)                       # 同 spec 同 logDir
    assert svc2.calls == []
    assert (tmp_path / "log" / "report.json").read_bytes() == first   # 旧 UNKNOWN 报告不被覆盖
    spec = make_spec(tmp_path, logDir=str(tmp_path / "log-other"))
    with pytest.raises(sync.SyncError, match="未对账"):
        run(tmp_path, spec, svc2, name="other.json")                   # 换 logDir 不能绕过
    assert svc2.calls == [] and len(svc.upserts) == 1


def test_different_values_on_the_same_unreconciled_target_are_also_blocked(tmp_path, meta):
    unknown_state(tmp_path, meta)
    spec = make_spec(tmp_path, logDir=str(tmp_path / "log-b"))
    spec["updates"][0]["fields"] = {UNIT: {"new": "另一个值", "basis": basis()}}
    svc = service(meta)
    with pytest.raises(sync.SyncError, match="未对账"):
        run(tmp_path, spec, svc, name="b.json")
    assert svc.calls == []


def test_plan_reports_the_block_without_touching_anything(tmp_path, meta):
    unknown_state(tmp_path, meta)
    before = sorted(p.name for p in (tmp_path / "ledger" / "targets").iterdir())
    result = run(tmp_path, make_spec(tmp_path, "plan"), service(meta))
    assert result["executable"] is False and result["ledgerBlocks"][0]["reason"] == "UNKNOWN"
    assert sorted(p.name for p in (tmp_path / "ledger" / "targets").iterdir()) == before


def test_intent_is_on_the_ledger_before_the_write_is_sent(tmp_path, meta):
    seen = {}
    svc = service(meta)
    svc.hooks_at_upsert = lambda table, record: seen.update(events=[e["event"] for e in ledger_events(tmp_path, record)])
    run(tmp_path, make_spec(tmp_path), svc)
    assert seen["events"] == ["intent"]


def test_interrupted_run_leaves_a_pending_intent_that_blocks(tmp_path, meta):
    svc = service(meta, crash_for=REC)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, make_spec(tmp_path), svc)
    assert [e["event"] for e in ledger_events(tmp_path)] == ["intent"]
    assert not list((tmp_path / "ledger" / "locks").iterdir())          # 锁已释放，意图仍封锁
    svc2 = service(meta)
    with pytest.raises(sync.SyncError, match="PENDING"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-2")), svc2, name="s2.json")
    assert svc2.calls == []


def test_readback_failure_after_write_is_unknown_and_blocks(tmp_path, meta):
    svc = service(meta, fail_get_after_write=True)
    with pytest.raises(sync.RuntimeSyncError, match="效果未核实"):
        run(tmp_path, make_spec(tmp_path), svc)
    assert read_report(tmp_path / "log" / "report.json")["status"] == "UNKNOWN"
    assert ledger_events(tmp_path)[-1]["status"] == "READBACK_FAILED"


def test_concurrent_run_is_refused_by_the_target_lock(tmp_path, meta):
    lock_dir = tmp_path / "ledger" / "locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / f"{sync.Ledger.target_key(BASE, CASE, REC)}.lock").write_text("{}", encoding="utf-8")
    svc = service(meta)
    with pytest.raises(sync.SyncError, match="占用|遗留锁"):
        run(tmp_path, make_spec(tmp_path), svc)
    assert svc.calls == [] and (lock_dir / f"{sync.Ledger.target_key(BASE, CASE, REC)}.lock").exists()


def test_a_lock_held_inside_the_run_is_seen_by_a_second_run(tmp_path, meta):
    outcome = {}

    def second_run(table, record):
        svc2 = service(meta)
        try:
            run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-concurrent")), svc2, name="c.json")
        except sync.SyncError as error:
            outcome["error"] = str(error)
        outcome["calls"] = len(svc2.calls)

    svc = service(meta)
    svc.hooks_at_upsert = second_run
    run(tmp_path, make_spec(tmp_path), svc)
    assert ("未对账" in outcome["error"] or "占用" in outcome["error"]) and outcome["calls"] == 0


def reconcile_spec(tmp_path, name="reconcile", **extra):
    return make_spec(tmp_path, "reconcile", logDir=str(tmp_path / name), **extra)


def test_reconcile_applied_clears_the_block_without_any_write(tmp_path, meta):
    svc = unknown_state(tmp_path, meta, apply_then_timeout_for=REC, timeout_for=None)
    recon = service(meta)
    recon.rows = copy.deepcopy(svc.rows)                                # 云端已生效
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "APPLIED" and recon.upserts == [] and report["externalWrites"] == 0
    assert all(call[2] == "+record-get" for call in recon.calls)
    again = service(meta)
    again.rows = copy.deepcopy(svc.rows)
    result = run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-after")), again, name="again.json")
    assert result["records"][0]["status"] == "UNCHANGED" and again.upserts == []


def test_old_value_observed_after_unknown_never_releases_the_block(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)                                               # 云端仍是旧值
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "OLD_VALUE_OBSERVED" and recon.upserts == []
    again = service(meta)
    with pytest.raises(sync.SyncError, match="OLD_VALUE_OBSERVED"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-retry")), again, name="retry.json")
    assert again.calls == []
    last = ledger_events(tmp_path)[-1]
    assert last["event"] == "reconciled" and last["originalIntent"]["fields"][UNIT]["wire"] == "新单位" and last["observed"]["fields"][UNIT] == "旧单位"


def test_a_late_write_after_an_old_value_observation_is_not_replayed_or_missed(tmp_path, meta):
    svc = unknown_state(tmp_path, meta)
    first = service(meta)
    assert run(tmp_path, reconcile_spec(tmp_path, "r1"), first, flag="reconcile", name="r1.json")["targets"][0]["outcome"] == "OLD_VALUE_OBSERVED"
    late = service(meta)                                                # 迟到的原请求现在才生效
    late.rows[(CASE, REC)][UNIT], late.rows[(CASE, REC)][RESULT] = "新单位", ["不合格"]
    blocked = service(meta)
    with pytest.raises(sync.SyncError, match="OLD_VALUE_OBSERVED"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-b")), blocked, name="b.json")
    assert blocked.calls == []                                          # 未被重放，不会双写
    report = run(tmp_path, reconcile_spec(tmp_path, "r2"), late, flag="reconcile", name="r2.json")
    assert report["targets"][0]["outcome"] == "APPLIED" and late.upserts == [] and len(svc.upserts) == 1
    again = service(meta)
    again.rows = copy.deepcopy(late.rows)
    assert run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-c")), again, name="c.json")["records"][0]["status"] == "UNCHANGED"


def test_a_late_write_mixed_with_a_foreign_change_is_diverged_not_applied(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)
    recon.rows[(CASE, REC)][UNIT] = "新单位"                            # 只到了一半
    assert run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")["targets"][0]["outcome"] == "DIVERGED"


def test_reconcile_requires_the_same_stable_key_business_identity(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)
    recon.rows[(CASE, REC)][KEY] = "SYNTH-OTHER"                       # 同 record_id 现在是另一个业务对象
    recon.rows[(CASE, REC)][UNIT], recon.rows[(CASE, REC)][RESULT] = "新单位", ["不合格"]   # 目标值恰好都相等
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "IDENTITY_MISMATCH" and report["targets"][0]["observed"]["stableKey"] == "SYNTH-OTHER"
    with pytest.raises(sync.SyncError, match="IDENTITY_MISMATCH"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-x")), service(meta), name="x.json")
    last = ledger_events(tmp_path)[-1]
    assert last["outcome"] == "IDENTITY_MISMATCH" and last["originalIntent"]["stableKey"]["value"] == STABLE


def test_reconcile_with_a_different_stable_key_in_the_spec_is_refused_before_any_event(tmp_path, meta):
    unknown_state(tmp_path, meta)
    events = len(ledger_events(tmp_path))
    spec = reconcile_spec(tmp_path)
    spec["updates"][0]["stableKey"]["value"] = "SYNTH-0009"
    recon = service(meta)
    with pytest.raises(sync.SyncError, match="稳定键与账本意图不一致"):
        run(tmp_path, spec, recon, flag="reconcile", name="r.json")
    assert recon.calls == [] and len(ledger_events(tmp_path)) == events


def test_reconcile_with_an_unreadable_stable_key_stays_blocked(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta, omit_fields=(KEY,))
    assert run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")["targets"][0]["outcome"] == "IDENTITY_UNREADABLE"


def test_reconcile_diverged_stays_blocked(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)
    recon.rows[(CASE, REC)][UNIT] = "别人改成的值"
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "DIVERGED"
    svc = service(meta)
    with pytest.raises(sync.SyncError, match="DIVERGED"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-x")), svc, name="x.json")


def test_reconcile_unreadable_stays_blocked_and_needs_a_flag(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta, no_ids=True)
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "UNREADABLE"
    with pytest.raises(sync.SyncError, match="--reconcile"):
        run(tmp_path, reconcile_spec(tmp_path, "r2"), service(meta), flag="none", name="r2.json")


RELEASE = {"reason": "已核对服务端请求记录：原请求已终止且未生效", "operator": "测试人", "intentId": "placeholder-intent", "acknowledgesLateWriteRisk": True,
           "evidence": [{"kind": "service-side-record", "ref": "审计记录#42", "sha256": "ab" * 32}]}


def test_human_release_is_recorded_with_evidence_and_the_original_intent(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)
    release = {**RELEASE, "intentId": ledger_events(tmp_path)[0]["intentId"]}
    report = run(tmp_path, reconcile_spec(tmp_path, humanRelease=release), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "HUMAN_RELEASED" and recon.calls == []
    event = ledger_events(tmp_path)[-1]
    assert event["event"] == "human_release" and event["evidence"] == RELEASE["evidence"] and event["operator"] == "测试人"
    assert event["originalIntent"]["fields"][UNIT]["wire"] == "新单位" and event["priorState"] == "UNKNOWN"
    # 未获用户风险接受的人工放行不解除保护：新的业务赋值在任何外部调用前被拒绝
    after = service(meta)
    with pytest.raises(sync.SyncError, match="HUMAN_RELEASE_UNACCEPTED"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-after")), after, name="a.json")
    assert after.calls == []


def test_update_reconcile_releases_only_the_exact_intent_and_never_a_whole_target_in_bulk(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta)
    wrong = {**RELEASE, "intentId": "0" * 64}
    report = run(tmp_path, reconcile_spec(tmp_path, humanRelease=wrong), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "RELEASE_NOT_FOR_THIS_INTENT"
    assert not [e for e in ledger_events(tmp_path) if e["event"] == "human_release"]
    with pytest.raises(sync.SyncError, match="未对账"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-after")), service(meta), name="a.json")


@pytest.mark.parametrize("patch,match", [
    (lambda r: r.pop("evidence"), "evidence"),
    (lambda r: r.update(evidence=[]), "evidence"),
    (lambda r: r.update(evidence=[{"kind": "old-value-readback", "ref": "云端旧值相等"}]), "evidence.kind"),
    (lambda r: r.update(evidence=[{"kind": "service-side-record", "ref": " "}]), "evidence.ref"),
    (lambda r: r.pop("acknowledgesLateWriteRisk"), "acknowledgesLateWriteRisk"),
    (lambda r: r.update(reason=" "), "reason"),
    (lambda r: r.update(operator=""), "operator"),
    (lambda r: r.pop("intentId"), "精确 intentId"),
])
def test_human_release_without_real_grounds_is_refused(tmp_path, meta, patch, match):
    release = copy.deepcopy(RELEASE)
    patch(release)
    spec = reconcile_spec(tmp_path, humanRelease=release)
    refused(tmp_path, spec, meta, match, flag="reconcile")


def test_reconcile_with_nothing_unresolved_does_nothing(tmp_path, meta):
    recon = service(meta)
    report = run(tmp_path, reconcile_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "NOTHING_TO_RECONCILE" and recon.calls == []


def test_existing_log_dir_is_never_reused(tmp_path, meta):
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "report.json").write_text("旧结果", encoding="utf-8")
    svc = service(meta)
    with pytest.raises(sync.SyncError, match="已存在且非空"):
        run(tmp_path, make_spec(tmp_path), svc)
    assert svc.calls == [] and (tmp_path / "log" / "report.json").read_text(encoding="utf-8") == "旧结果"


# ------------------------------------------------------------------ entry points


def test_main_refuses_with_exit_2_and_zero_calls(tmp_path, capsys):
    spec = make_spec(tmp_path)
    spec["allowlist"] = {}
    path = write_spec(tmp_path, spec)
    assert sync.main(["--spec", str(path), "--execute", "--ledger-dir", str(tmp_path / "l")]) == 2
    assert json.loads(capsys.readouterr().out)["externalCalls"] == 0


def test_main_without_spec_is_usage_error():
    with pytest.raises(SystemExit):
        sync.main([])


def test_registry_cli_subcommand_refuses_without_external_call(tmp_path, capsys):
    import scripts.registry_cli as registry_cli

    spec = make_spec(tmp_path)
    spec["allowlist"] = {}
    path = write_spec(tmp_path, spec)
    assert registry_cli.main(["feishu-source-sync", "--spec", str(path), "--execute", "--ledger-dir", str(tmp_path / "l")]) == 2
    assert "已拒绝" in capsys.readouterr().err


def test_lark_transport_builds_official_cli_argv(meta):
    svc = service(meta)
    transport = sync.LarkCliTransport("/x/lark-cli", "prof", "user", runner=svc)
    assert transport.get_records(BASE, CASE, [REC])[REC][UNIT] == "旧单位"
    transport.upsert(BASE, CASE, REC, {UNIT: "新"})
    assert svc.calls[0][:3] == ["/x/lark-cli", "base", "+record-get"] and "--record-id" in svc.calls[0]
    assert svc.calls[1][2] == "+record-upsert" and svc.calls[1][-4:] == ["--profile", "prof", "--as", "user"]
    assert not any(arg in svc.calls[1] for arg in ("+record-batch-update", "+record-delete", "+record-list"))


def test_upsert_receipt_with_another_record_id_is_unknown(meta):
    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": {"record": {"record_id": "recOTHER00001"}}}), stderr="")

    transport = sync.LarkCliTransport("/x/lark-cli", "p", "user", runner=runner)
    with pytest.raises(sync.RuntimeSyncError, match="UNKNOWN"):
        transport.upsert(BASE, CASE, REC, {UNIT: "x"})


def test_missing_cli_binary_is_a_clean_failure_before_any_write(tmp_path):
    def runner(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    transport = sync.LarkCliTransport("/nonexistent/lark-cli", "prof", "user", runner=runner)
    with pytest.raises(sync.RuntimeSyncError, match="无法启动"):
        transport.get_records("B", CASE, [REC])


def test_target_field_missing_from_the_pre_write_readback_is_refused_before_any_write(tmp_path, meta):
    svc = service(meta, omit_fields=(UNIT,))                            # 服务端两次回读都不返回该字段
    with pytest.raises(sync.RuntimeSyncError, match="缺少稳定键或目标字段"):
        run(tmp_path, make_spec(tmp_path), svc)
    assert svc.upserts == [] and ledger_events(tmp_path) == []


def test_target_field_missing_from_the_post_write_readback_cannot_pass(tmp_path, meta):
    svc = service(meta, omit_fields_after_write=(UNIT,))
    with pytest.raises(sync.RuntimeSyncError, match="回读缺少该目标字段"):
        run(tmp_path, make_spec(tmp_path), svc)
    report = read_report(tmp_path / "log" / "report.json")
    assert report["status"] == "FAILED" and ledger_events(tmp_path)[-1]["status"] == "VIOLATION"


def test_a_service_that_ignores_the_write_for_a_missing_field_does_not_report_ok(tmp_path, meta):
    svc = service(meta, ignore_writes_for=REC, omit_fields_after_write=(UNIT, RESULT))
    with pytest.raises(sync.RuntimeSyncError):
        run(tmp_path, make_spec(tmp_path), svc)
    assert read_report(tmp_path / "log" / "report.json")["status"] != "OK"


@pytest.mark.parametrize("mode", ["execute", "reconcile"])
def test_execute_and_reconcile_need_an_explicit_ledger_dir(tmp_path, meta, mode):
    spec = make_spec(tmp_path, mode)
    transport = sync.LarkCliTransport("/x/lark-cli", "p", "user", runner=service(meta))
    path = write_spec(tmp_path, spec)
    with pytest.raises(sync.SyncError, match="--ledger-dir"):
        sync.run(path, execute_flag=True, reconcile_flag=True, transport=transport)
    assert not (tmp_path / "log").exists()


def test_plan_may_omit_the_ledger_dir_and_reads_only(tmp_path, meta):
    path = write_spec(tmp_path, make_spec(tmp_path, "plan"))
    result = sync.run(path, transport=sync.LarkCliTransport("/x/lark-cli", "p", "user", runner=service(meta)))
    assert result["externalCalls"] == 0


def test_a_newly_added_manual_field_in_the_contract_is_refused_without_any_code_change(tmp_path, contract, meta):
    new_field = {"fieldId": "fldNEWMANUAL1", "name": "新人工标记", "type": "select", "class": "human", "syncWrite": "denied"}
    contract["tables"]["案卷主表"]["fields"].append(new_field)
    path = tmp_path / "contract-with-new-human-field.json"
    path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    spec = make_spec(tmp_path)
    spec["contract"] = {"path": str(path), "sha256": sha(path)}
    spec["allowlist"][CASE]["fields"] = ["fldNEWMANUAL1"]
    spec["updates"][0]["fields"] = {"fldNEWMANUAL1": {"new": "是", "basis": basis()}}
    refused(tmp_path, spec, meta, "source-sync-whitelist")


def test_an_unclassified_new_field_is_refused_by_default(tmp_path, meta):
    spec = make_spec(tmp_path)
    spec["allowlist"][CASE]["fields"] = ["fldBRANDNEW01"]
    spec["updates"][0]["fields"] = {"fldBRANDNEW01": {"new": "是", "basis": basis()}}
    refused(tmp_path, spec, meta, "默认拒写")


def test_registry_cli_exit_code_3_when_an_external_call_failed(tmp_path, capsys):
    import scripts.registry_cli as registry_cli

    path = write_spec(tmp_path, make_spec(tmp_path))
    with pytest.raises(SystemExit) as stop:
        registry_cli.main(["feishu-source-sync", "--spec", str(path), "--execute", "--cli", str(tmp_path / "no-such-lark-cli"),
                           "--ledger-dir", str(tmp_path / "ledger")])
    assert stop.value.code == 3 and "未重试" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# V3 create (offline, in-memory transport; not a P18/real-cloud result)
# ---------------------------------------------------------------------------------------------

TEAM_TABLE, TEAM_NAME = "tbl3pj0AvF9G7GJh", "fldTEAMname1"
SELECT_TEAM, LINK_TEAM = "fld2Kdm4i2", "fldlw1KAaR"
CHECK_PARENT = "fld1kH9BIS"
TEAM_REC, OTHER_TEAM_REC = "recTEAMliangxi1", "recTEAMbinhu001"
SRC = "无锡市梁溪区消防救援局"


def _f(fid, name, kind, klass, sync="NEVER"):
    return {"fieldId": fid, "name": name, "type": kind, "class": klass, "memberRead": False, "memberEdit": False, "syncWrite": sync}


def _cand(table, fid, name, kind, klass, state, empty="omit unknown/missing/unread/unconfirmed"):
    return {"tableId": table, "fieldId": fid, "name": name, "type": kind, "proposedCreateClass": klass, "candidateState": state, "emptySemantics": empty}


def make_v3_contract(tmp_path: Path, *, child_ready: bool = False) -> Path:
    base = {
        "baseId": BASE, "version": "synthetic-v9",
        "tables": {
            "案卷主表": {"tableId": CASE, "fields": [
                _f(KEY, "项目编号", "text", "source-sync-whitelist", "NEVER (stable key)"), _f(UNIT, "单位名称", "text", "source-sync-whitelist", "allowed"),
                _f(SELECT_TEAM, "所属大队", "select", "admin-only"), _f(LINK_TEAM, "归属大队", "link", "admin-only"),
                _f(HUMAN_NET, "是否网售", "select", "human"), _f(DERIVED, "初查不合格数", "formula", "derived-readonly")]},
            "检查明细": {"tableId": CHECK, "fields": [
                _f(CHECK_KEY, "检查键", "text", "source-sync-whitelist", "NEVER (stable key)"), _f(CHECK_PARENT, "项目编号", "link", "structural-relation")]},
            "产品明细": {"tableId": PRODUCT, "fields": [_f("fldqlwHjUW", "产品键", "text", "source-sync-whitelist", "NEVER (stable key)")]},
            "大队底册": {"tableId": TEAM_TABLE, "fields": [_f(TEAM_NAME, "名称", "text", "admin-only")]},
        },
    }
    child = "candidate-test-ready" if child_ready else "blocked-pending-native-child-ref-parent-and-compatible-key"
    ext = {
        "versionCandidate": "synthetic-v3",
        "createFieldsCandidate": [
            _cand(CASE, KEY, "项目编号", "text", "create-once-identity", "candidate-ready", "required; block"),
            _cand(CASE, UNIT, "单位名称", "text", "create-once-source-scalar", "candidate-observed", "required; block"),
            _cand(CASE, SELECT_TEAM, "所属大队", "select", "create-once-routing", "candidate-ready", "required; block"),
            _cand(CASE, LINK_TEAM, "归属大队", "link", "create-once-routing", "candidate-ready", "required; block"),
            _cand(CHECK, CHECK_KEY, "检查键", "text", "create-once-identity", child, "required; block"),
            _cand(CHECK, CHECK_PARENT, "项目编号", "link", "create-once-parent-link", child, "required; block"),
        ],
        "explicitCreatePayloadDeny": [{"fieldId": DERIVED}, {"fieldId": HUMAN_NET}, {"fieldId": SELECT_TEAM}, {"fieldId": LINK_TEAM}],  # 与冻结合同一致：路由字段同时在DENY清单
    }
    path = tmp_path / "v3-contract.json"
    path.write_text(json.dumps({"baseContract": base, "extension": ext}, ensure_ascii=False), encoding="utf-8")
    return path


class MemoryTransport:
    """In-memory service for the V3 branch. Behaviours simulate timeouts, ignored fields and late writes."""

    def __init__(self, **behaviour):
        self.b = behaviour
        self.rows: dict[tuple[str, str], dict] = {(TEAM_TABLE, TEAM_REC): {TEAM_NAME: "梁溪大队"}}
        self.creates: list[tuple[str, dict]] = []
        self.finds = 0
        self.counter = 0
        self.order: list[str] = []

    def get_records(self, base, table, ids):
        for rid in ids:
            if (table, rid) not in self.rows:
                raise sync.RuntimeSyncError(f"目标记录读不到：{[rid]}")
        return {rid: dict(self.rows[(table, rid)]) for rid in ids}

    def find_records(self, base, table, key_field, value):
        self.finds += 1
        if self.b.get("scan_fails"):
            raise sync.RuntimeSyncError("扫描失败")
        return [rid for (t, rid), row in self.rows.items() if t == table and row.get(key_field) == value]

    def get_record_history(self, base, table, record_id):
        if "history" not in self.b:
            raise sync.RuntimeSyncError("无历史")
        return self.b["history"](record_id)

    def _store(self, table, fields):
        self.counter += 1
        rid = f"recNEW{self.counter:07d}"
        row = dict(fields)
        for fid in self.b.get("ignore_fields", ()):
            row.pop(fid, None)
        self.rows[(table, rid)] = row
        return rid

    def create_record(self, base, table, fields):
        self.creates.append((table, copy.deepcopy(fields)))
        if self.b.get("timeout_late"):
            self._store(table, fields)  # the request did take effect, but the caller sees a timeout
            raise sync.RuntimeSyncError("UNKNOWN: 官方 CLI 超时；效果未知，需按 record_id 回读对账")
        if self.b.get("timeout_lost"):
            raise sync.RuntimeSyncError("UNKNOWN: 官方 CLI 超时；效果未知，需按 record_id 回读对账")
        rid = self._store(table, fields)
        if self.b.get("no_receipt_id"):
            self.last_body = {"ok": True, "data": {"created": True, "record": {"record_id_list": [rid]}}}   # 真实回执形态，旧解析器读不到
            raise sync.RuntimeSyncError("UNKNOWN: 创建回执缺少 record_id；效果未知，不按顺序猜配，需按稳定键回读对账")
        return {"ok": True, "data": {"record": {"record_id": rid}}}

    def upsert(self, base, table, record_id, fields):  # V2 path must never be used by V3
        raise AssertionError("V3 不得走更新路径")


def _wire_case(key="32002299C202600001", unit="合成单位", source=SRC, team_option="梁溪大队", team_rec=TEAM_REC, **extra):
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-07T00:00:00Z"}
    fields = {KEY: {"new": key, "basis": basis}, UNIT: {"new": unit, "basis": basis},
              SELECT_TEAM: {"new": team_option, "basis": basis}, LINK_TEAM: {"new": team_rec, "basis": basis}}
    fields.update(extra)
    return {"tableId": CASE, "stableKey": {"fieldId": KEY, "value": key}, "routingSource": source, "fields": fields}


def v3_spec(tmp_path: Path, contract: Path, creates: list, *, mode="execute", log="log1", **over) -> Path:
    spec = {
        "schemaVersion": sync.SPEC_SCHEMA_V3, "mode": mode,
        "contract": {"path": str(contract), "sha256": sha(contract)},
        "target": {"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
        "limits": {"maxRecords": 5}, "logDir": str(tmp_path / log),
        "routes": [{"sourceJurisdictionExact": SRC, "teamOptionExact": "梁溪大队", "teamTableId": TEAM_TABLE, "teamRecordId": TEAM_REC}],
        "creates": creates,
    }
    spec.update(over)
    path = tmp_path / f"spec-{mode}-{log}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _run_v3(spec_path, transport, ledger, *, execute=False, reconcile=False):
    return sync.run(spec_path, execute_flag=execute, reconcile_flag=reconcile, transport=transport, ledger_dir=ledger)


def test_v3_plan_makes_no_call_and_shows_official_wire_shapes(tmp_path):
    contract = make_v3_contract(tmp_path)
    transport = MemoryTransport()
    spec = v3_spec(tmp_path, contract, [_wire_case(), _wire_case(key="32002299C202600002", source="未知管辖")], mode="plan")
    report = _run_v3(spec, transport, tmp_path / "ledger")
    assert report["externalCalls"] == 0 and transport.finds == 0 and not transport.creates
    ok, unmatched = report["creates"]
    assert ok["wire"][SELECT_TEAM] == ["梁溪大队"] and ok["wire"][LINK_TEAM] == [{"id": TEAM_REC}]
    assert unmatched["action"] == "blocked" and "路由未匹配" in unmatched["blocked"][0]
    assert not (tmp_path / "ledger").exists()


def test_v3_creates_a_verified_case_with_the_intent_on_the_ledger_first_and_a_second_run_adds_nothing(tmp_path):
    contract, ledger = make_v3_contract(tmp_path), tmp_path / "ledger"
    transport = MemoryTransport()
    seen = {}
    original = transport.create_record

    def spy(base, table, fields):
        events = [json.loads(line) for p in (ledger / "targets").glob("*.jsonl") for line in p.read_text(encoding="utf-8").splitlines()]
        seen["intent_before_create"] = any(e["event"] == "intent" and e["kind"] == "create" for e in events)
        return original(base, table, fields)

    transport.create_record = spy
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    assert report["status"] == "OK" and report["records"][0]["status"] == "CREATED_VERIFIED" and seen["intent_before_create"]
    assert len(transport.creates) == 1
    key = sync.Ledger.target_key(BASE, CASE, "create:32002299C202600001")
    assert sync.Ledger(ledger).state(key)["state"] == "CLEAR"
    again = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="log2"), transport, ledger, execute=True)
    assert again["records"][0]["status"] == "ALREADY_PRESENT_VERIFIED" and len(transport.creates) == 1     # 幂等：零新增


@pytest.mark.parametrize("field,message", [
    (HUMAN_NET, "不得出现在创建载荷"), (DERIVED, "不得出现在创建载荷"), ("fldUNKNOWN01", "不在 V3 创建候选"),
])
def test_v3_denied_and_unknown_fields_are_refused_before_any_call(tmp_path, field, message):
    contract = make_v3_contract(tmp_path)
    transport = MemoryTransport()
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-07T00:00:00Z"}
    spec = v3_spec(tmp_path, contract, [_wire_case(**{field: {"new": "x", "basis": basis}})])
    with pytest.raises(sync.SyncError, match=message):
        _run_v3(spec, transport, tmp_path / "ledger", execute=True)
    assert transport.finds == 0 and not transport.creates


def test_v3_child_creation_is_refused_while_the_contract_keeps_it_blocked_and_works_when_released(tmp_path):
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-07T00:00:00Z"}
    child = {"tableId": CHECK, "stableKey": {"fieldId": CHECK_KEY, "value": "32002299C202600001|初查"},
             "parent": {"createRef": "32002299C202600001", "stableKey": {"fieldId": KEY, "value": "32002299C202600001"}},
             "fields": {CHECK_KEY: {"new": "32002299C202600001|初查", "basis": basis}, CHECK_PARENT: {"new": "recPLACEHOLDER1", "basis": basis}}}
    blocked_contract = make_v3_contract(tmp_path)
    with pytest.raises(sync.SyncError, match="候选仍 blocked"):
        _run_v3(v3_spec(tmp_path, blocked_contract, [_wire_case(), child]), MemoryTransport(), tmp_path / "l0", execute=True)
    ready = make_v3_contract(tmp_path / "ready" if (tmp_path / "ready").mkdir() is None else tmp_path, child_ready=True)
    transport = MemoryTransport()
    report = _run_v3(v3_spec(tmp_path, ready, [child, _wire_case()]), transport, tmp_path / "l1", execute=True)
    assert [e["status"] for e in report["records"]] == ["CREATED_VERIFIED", "CREATED_VERIFIED"]      # 案卷先于检查
    assert [t for t, _ in transport.creates] == [CASE, CHECK]
    case_rid = report["records"][0]["recordId"]
    assert transport.creates[1][1][CHECK_PARENT] == [{"id": case_rid}]                               # 只设子端父链


def test_v3_route_must_match_the_frozen_pair_and_unmatched_jurisdiction_only_blocks_that_entity(tmp_path):
    contract = make_v3_contract(tmp_path)
    transport = MemoryTransport()
    with pytest.raises(sync.SyncError, match="必须同队"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case(team_rec=OTHER_TEAM_REC)]), transport, tmp_path / "l0", execute=True)
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case(), _wire_case(key="32002299C202600002", source="无锡市新吴区消防救援局")], log="b"),
                     transport, tmp_path / "l1", execute=True)
    assert [e["status"] for e in report["records"]] == ["CREATED_VERIFIED", "BLOCKED_ENTITY"] and len(transport.creates) == 1


def test_v3_duplicate_existing_keys_stop_without_creating(tmp_path):
    contract = make_v3_contract(tmp_path)
    transport = MemoryTransport()
    transport.rows[(CASE, "recDUP0000001")] = {KEY: "32002299C202600001"}
    transport.rows[(CASE, "recDUP0000002")] = {KEY: "32002299C202600001"}
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, tmp_path / "ledger", execute=True)
    assert not transport.creates


def test_v3_unknown_create_blocks_the_target_and_zero_match_never_authorizes_a_retry(tmp_path):
    contract, ledger = make_v3_contract(tmp_path), tmp_path / "ledger"
    transport = MemoryTransport(timeout_lost=True)
    with pytest.raises(sync.RuntimeSyncError, match="创建失败/结果未知"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    with pytest.raises(sync.SyncError, match="未对账的早先创建"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="again"), transport, ledger, execute=True)
    assert len(transport.creates) == 1
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], mode="reconcile", log="rec"), transport, ledger, reconcile=True)
    assert report["targets"][0]["outcome"] == "ZERO_MATCH"
    with pytest.raises(sync.SyncError, match="未对账的早先创建"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="after-zero"), transport, ledger, execute=True)
    assert len(transport.creates) == 1                                                            # 仍封锁，没有换ID/重放


def _proof_for(log_dir: Path, ledger: Path, key_value="32002299C202600001", **override):
    key = sync.Ledger.target_key(BASE, CASE, "create:" + key_value)
    intent = sync.Ledger(ledger).state(key)["lastIntent"]
    ref = {"kind": "service-receipt-log", "logDir": str(log_dir), "runId": intent["runId"], "intentId": intent["intentId"],
           "requestsSha256": sha(log_dir / "requests.jsonl"), "responsesSha256": sha(log_dir / "responses.jsonl")}
    ref.update(override)
    return [ref]


def _late_create(tmp_path, **behaviour):
    contract, ledger = make_v3_contract(tmp_path), tmp_path / "ledger"
    transport = MemoryTransport(**behaviour)
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    return contract, ledger, transport, sync.Ledger.target_key(BASE, CASE, "create:32002299C202600001")


def _reconcile(tmp_path, contract, ledger, transport, log, **extra):
    return _run_v3(v3_spec(tmp_path, contract, [_wire_case()], mode="reconcile", log=log, **extra), transport, ledger, reconcile=True)["targets"][0]


def test_v3_weak_or_free_text_proof_never_clears_a_late_effect(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, timeout_late=True)     # 超时：没有任何服务回执
    for n, weak in enumerate([True, "cli-process-log", [{"kind": "request-terminal-proof", "ref": "cli-process-log-fixture"}], [{}], ["x"], [{"kind": "service-receipt-log"}]]):
        target = _reconcile(tmp_path, contract, ledger, transport, f"w{n}", requestTerminalProof=weak)
        assert target["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF" and sync.Ledger(ledger).state(key)["state"] == "BLOCKED"
    # 真实日志但原请求没有拿到服务回执：同样不能证明已终态
    log_dir = Path(sync.Ledger(ledger).state(key)["lastIntent"]["logDir"])
    target = _reconcile(tmp_path, contract, ledger, transport, "no-receipt", requestTerminalProof=_proof_for(log_dir, ledger))
    assert target["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF" and "回执" in " ".join(target["observed"]["proofRejected"])
    assert len(transport.creates) == 1


def test_v3_service_receipt_log_clears_only_the_exact_original_request(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, no_receipt_id=True)    # 真实形态 record_id_list：旧代码读不到 id
    intent = sync.Ledger(ledger).state(key)["lastIntent"]
    log_dir = Path(intent["logDir"])
    bad = {
        "digest": _proof_for(log_dir, ledger, requestsSha256="0" * 64),
        "intent": _proof_for(log_dir, ledger, intentId="f" * 64),
        "run": _proof_for(log_dir, ledger, runId="other-run"),
        "dir": _proof_for(log_dir, ledger, logDir=str(tmp_path / "forged")),
        "unrelated": _proof_for(log_dir, ledger, logDir=str(tmp_path)),
    }
    for name, proof in bad.items():
        target = _reconcile(tmp_path, contract, ledger, transport, "bad-" + name, requestTerminalProof=proof)
        assert target["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF", name
        assert sync.Ledger(ledger).state(key)["state"] == "BLOCKED"
    rows = [rid for (t, rid) in transport.rows if t == CASE]
    transport.rows[(CASE, "recOTHER00001")] = dict(transport.rows[(CASE, rows[0])])     # 第二条同键行：多匹配
    assert _reconcile(tmp_path, contract, ledger, transport, "multi", requestTerminalProof=_proof_for(log_dir, ledger))["outcome"] == "MULTIPLE_MATCH"
    del transport.rows[(CASE, "recOTHER00001")]
    transport.rows[(CASE, rows[0])][UNIT] = "被他人改过"                                  # 字段差异：DIVERGED
    assert _reconcile(tmp_path, contract, ledger, transport, "diff", requestTerminalProof=_proof_for(log_dir, ledger))["outcome"] == "DIVERGED"
    transport.rows[(CASE, rows[0])][UNIT] = "合成单位"
    good = _reconcile(tmp_path, contract, ledger, transport, "good", requestTerminalProof=_proof_for(log_dir, ledger))
    assert good["outcome"] == "APPLIED" and sync.Ledger(ledger).state(key)["state"] == "CLEAR"
    assert len(transport.creates) == 1
    kinds = [e["event"] for e in sync.Ledger(ledger).events(key)]
    assert kinds.count("intent") == 1 and kinds.count("reconciled") >= 7                      # 历史只追加，不删改


def test_v3_proof_whose_receipt_names_another_record_is_rejected(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, no_receipt_id=True)
    log_dir = Path(sync.Ledger(ledger).state(key)["lastIntent"]["logDir"])
    lines = [json.loads(x) for x in (log_dir / "responses.jsonl").read_text(encoding="utf-8").splitlines()]
    lines[0]["rawReceipt"]["data"]["record"]["record_id_list"] = ["recSOMEONEELSE1"]
    (log_dir / "responses.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    target = _reconcile(tmp_path, contract, ledger, transport, "other-id", requestTerminalProof=_proof_for(log_dir, ledger))
    assert target["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF" and sync.Ledger(ledger).state(key)["state"] == "BLOCKED"


def test_v3_two_identical_receipts_are_not_a_unique_terminal_proof(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, no_receipt_id=True)
    log_dir = Path(sync.Ledger(ledger).state(key)["lastIntent"]["logDir"])
    line = (log_dir / "responses.jsonl").read_text(encoding="utf-8").splitlines()[0]
    (log_dir / "responses.jsonl").write_text(line + "\n" + line + "\n", encoding="utf-8")         # 同 run/request/intent/payload 的两条同 id 成功回执
    target = _reconcile(tmp_path, contract, ledger, transport, "dup", requestTerminalProof=_proof_for(log_dir, ledger))
    assert target["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF" and "恰好一条" in " ".join(target["observed"]["proofRejected"])
    assert sync.Ledger(ledger).state(key)["state"] == "BLOCKED" and len(transport.creates) == 1


def test_v3_human_release_applies_only_to_its_exact_intent(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, timeout_lost=True)
    intent = sync.Ledger(ledger).state(key)["lastIntent"]
    release = {"reason": "人工核实", "operator": "tester", "acknowledgesLateWriteRisk": True, "evidence": [{"kind": "x", "ref": "y"}]}
    with pytest.raises(sync.SyncError, match="intentId"):
        _reconcile(tmp_path, contract, ledger, transport, "no-id", humanRelease=release)
    wrong = _reconcile(tmp_path, contract, ledger, transport, "wrong", humanRelease={**release, "intentId": "0" * 64})
    assert wrong["outcome"] == "ZERO_MATCH" and sync.Ledger(ledger).state(key)["state"] == "BLOCKED"
    ok = _reconcile(tmp_path, contract, ledger, transport, "ok", humanRelease={**release, "intentId": intent["intentId"]})
    assert ok["outcome"] == "HUMAN_RELEASED"


def test_v3_unknown_blocks_every_payload_variant_of_the_same_target_before_any_call(tmp_path):
    contract, ledger = make_v3_contract(tmp_path), tmp_path / "ledger"
    transport = MemoryTransport(timeout_lost=True)
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, ledger, execute=True)
    finds, creates = transport.finds, len(transport.creates)
    with pytest.raises(sync.SyncError, match="未对账的早先创建"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case(unit="换了单位名的另一载荷")], log="variant"), transport, ledger, execute=True)
    assert transport.finds == finds and len(transport.creates) == creates                       # 未发起任何外部调用
    other = _run_v3(v3_spec(tmp_path, contract, [_wire_case(key="32002299C202600009")], log="other"), MemoryTransport(), ledger, execute=True)
    assert other["status"] == "OK"                                                               # 其他目标不受牵连


def test_v3_existing_row_is_verified_not_assumed_to_be_this_create(tmp_path):
    contract = make_v3_contract(tmp_path)
    good = MemoryTransport()
    good.rows[(CASE, "recEXIST000001")] = {KEY: "32002299C202600001", UNIT: "合成单位", SELECT_TEAM: ["梁溪大队"], LINK_TEAM: [{"id": TEAM_REC}]}
    r = _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), good, tmp_path / "l1", execute=True)
    assert r["records"][0]["status"] == "ALREADY_PRESENT_VERIFIED" and not good.creates
    scalar = MemoryTransport()                                                                    # 仅来源标量字段不同：走 V2 更新，不重放创建
    scalar.rows[(CASE, "recEXIST000001")] = {KEY: "32002299C202600001", UNIT: "旧单位名", SELECT_TEAM: ["梁溪大队"], LINK_TEAM: [{"id": TEAM_REC}]}
    r = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="s"), scalar, tmp_path / "l2", execute=True)
    assert r["records"][0]["status"] == "EXISTING_NEEDS_V2_UPDATE" and r["records"][0]["sourceScalarDiffs"] == ["单位名称"] and not scalar.creates
    wrong_route = MemoryTransport()                                                               # 路由不同：不是同一实体，只挡该目标，不创建、不写账本封锁
    wrong_route.rows[(CASE, "recEXIST000001")] = {KEY: "32002299C202600001", UNIT: "合成单位", SELECT_TEAM: ["别的大队"], LINK_TEAM: [{"id": OTHER_TEAM_REC}]}
    r = _run_v3(v3_spec(tmp_path, contract, [_wire_case(), _wire_case(key="32002299C202600002")], log="w"), wrong_route, tmp_path / "l3", execute=True)
    assert [e["status"] for e in r["records"]] == ["EXISTING_DIVERGED", "CREATED_VERIFIED"] and r["status"] == "PARTIAL"   # 独立对象照常继续
    assert [f[KEY] for _, f in wrong_route.creates] == ["32002299C202600002"]
    assert sync.Ledger(tmp_path / "l3").state(sync.Ledger.target_key(BASE, CASE, "create:32002299C202600001"))["state"] == "CLEAR"


def test_v3_unverified_existing_row_id_is_not_handed_to_child_layers(tmp_path):
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-07T00:00:00Z"}
    child = {"tableId": CHECK, "stableKey": {"fieldId": CHECK_KEY, "value": "32002299C202600001|初查"},
             "parent": {"createRef": "32002299C202600001", "stableKey": {"fieldId": KEY, "value": "32002299C202600001"}},
             "fields": {CHECK_KEY: {"new": "32002299C202600001|初查", "basis": basis}, CHECK_PARENT: {"new": "recPLACEHOLDER1", "basis": basis}}}
    contract = make_v3_contract(tmp_path, child_ready=True)
    transport = MemoryTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: "32002299C202600001", UNIT: "合成单位", SELECT_TEAM: ["别的大队"], LINK_TEAM: [{"id": OTHER_TEAM_REC}]}
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case(), child]), transport, tmp_path / "l", execute=True)
    assert [e["status"] for e in report["records"]] == ["EXISTING_DIVERGED", "BLOCKED_ENTITY"] and not transport.creates       # 子层不得拿到未核实id
    assert "父记录未确认" in report["records"][1]["reasons"][0]


def test_v3_readback_violation_and_missing_receipt_id_stay_blocked(tmp_path):
    contract = make_v3_contract(tmp_path)
    ignoring = MemoryTransport(ignore_fields=(UNIT,))
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), ignoring, tmp_path / "l1", execute=True)
    key = sync.Ledger.target_key(BASE, CASE, "create:32002299C202600001")
    assert sync.Ledger(tmp_path / "l1").state(key)["state"] == "BLOCKED"
    no_id = MemoryTransport(no_receipt_id=True)
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case(key="32002299C202600003")], log="x"), no_id, tmp_path / "l2", execute=True)
    assert sync.Ledger(tmp_path / "l2").state(sync.Ledger.target_key(BASE, CASE, "create:32002299C202600003"))["state"] == "BLOCKED"


def test_v3_scan_failure_stops_before_create(tmp_path):
    contract = make_v3_contract(tmp_path)
    transport = MemoryTransport(scan_fails=True)
    with pytest.raises(sync.RuntimeSyncError):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()]), transport, tmp_path / "ledger", execute=True)
    assert not transport.creates


def test_v3_lark_transport_scans_all_pages_and_creates_without_a_record_id():
    pages = {0: ([f"rec{i:08d}" for i in range(200)], [["K%d" % i] for i in range(200)]), 200: (["recLAST00001"], [["WANTED"]])}
    calls = []

    def runner(argv, capture_output=True, text=True, timeout=None):
        calls.append(argv)
        opt = lambda name: argv[argv.index(name) + 1] if name in argv else None  # noqa: E731
        if argv[2] == "+record-list":
            ids, rows = pages[int(opt("--offset"))]
            data = {"record_id_list": ids, "field_id_list": [KEY], "data": rows}
        else:
            data = {"record": {"record_id": "recCREATED0001"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": data}), stderr="")

    transport = sync.LarkCliTransport("lark-cli", "p", "user", runner=runner)
    assert transport.find_records(BASE, CASE, KEY, "WANTED") == ["recLAST00001"]
    assert [c[c.index("--offset") + 1] for c in calls] == ["0", "200"]
    transport.create_record(BASE, CASE, {KEY: "x"})
    assert "--record-id" not in calls[-1] and calls[-1][2] == "+record-upsert"


def test_v3_routing_candidates_override_the_generic_deny_list_but_human_fields_never_do(tmp_path):
    contract = make_v3_contract(tmp_path)
    spec = v3_spec(tmp_path, contract, [_wire_case()], mode="plan")
    report = _run_v3(spec, MemoryTransport(), tmp_path / "ledger")
    assert report["contractConflicts"] == sorted([SELECT_TEAM, LINK_TEAM]) and report["executable"]
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-07T00:00:00Z"}
    with pytest.raises(sync.SyncError, match="不得出现在创建载荷"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case(**{HUMAN_NET: {"new": "是", "basis": basis}})], mode="plan", log="h"), MemoryTransport(), tmp_path / "ledger")


def test_v3_create_receipt_shapes_yield_exactly_one_record_id():
    assert sync._receipt_record_id({"data": {"created": True, "record": {"record_id_list": ["recABC1234567"]}}}) == "recABC1234567"
    assert sync._receipt_record_id({"data": {"record": {"record_id": "recABC1234567"}}}) == "recABC1234567"
    assert sync._receipt_record_id({"data": {"record": {"record_id_list": ["recABC1234567", "recDEF1234567"]}}}) is None   # 多个：不猜
    assert sync._receipt_record_id({"data": {"record": {}}}) is None and sync._receipt_record_id({}) is None
    assert sync._receipt_record_id({"data": {"record": {"record_id_list": []}}}) is None                                 # 0 个
    assert sync._receipt_record_id({"data": {"record": {"record_id": "recABC1234567", "record_id_list": ["recDEF1234567"]}}}) is None   # 冲突
    assert sync._receipt_record_id({"data": {"record": {"record_id_list": [123]}}}) is None and sync._receipt_record_id({"data": {"record": {"record_id_list": "recABC1234567"}}}) is None
    assert sync._receipt_record_id({"data": {"record": {"record_id_list": ["bad id"]}}}) is None                          # 非法形态
    assert sync._receipt_record_id({"data": {"record": {"record_id_list": ["recABC1234567", "recABC1234567"]}}}) == "recABC1234567"   # 同一 id 重复


# ---------------------------------------------------------------------------------------------
# 基础字段说明的正常生成入口(权威已存详情 -> 带字段依据的 V3/V2 说明)；离线内存传输，不是真实云结果
# ---------------------------------------------------------------------------------------------

ADDR, DATE_FIELD = "fld71ckPl9", "fld5azNPuE"
PN_NEW, PN_FILL, PN_OTHER = "32002299C202600001", "32002299C202600002", "32002299C202600003"


def make_basic_contracts(tmp_path: Path) -> tuple[Path, Path]:
    v3 = json.loads(make_v3_contract(tmp_path).read_text(encoding="utf-8"))
    table = v3["baseContract"]["tables"]["案卷主表"]["fields"]
    table[1]["class"] = "source-sync-whitelist"
    table += [_f(ADDR, "单位地址", "text", "source-sync-whitelist", "allowed"), _f(DATE_FIELD, "初查日期", "datetime", "source-sync-whitelist", "allowed")]
    v3["extension"]["createFieldsCandidate"] += [
        _cand(CASE, ADDR, "单位地址", "text", "create-once-source-scalar", "optional-only-if-actual-source-value-observed"),
        _cand(CASE, DATE_FIELD, "初查日期", "datetime", "create-once-source-scalar", "optional-only-if-actual-source-value-observed"),
    ]
    v3_path, v9_path = tmp_path / "v3-basic.json", tmp_path / "v9-basic.json"
    v3_path.write_text(json.dumps(v3, ensure_ascii=False), encoding="utf-8")
    v9_path.write_text(json.dumps(v3["baseContract"], ensure_ascii=False), encoding="utf-8")
    return v3_path, v9_path


def _detail(project, rwid, *, unit="合成单位", address="合成地址1号", jurisdiction=SRC, url_rwid=None, listed_unit=None):
    return {"projectNo": project, "rwid": rwid, "fields": {"rwid": rwid, "unitName": listed_unit or unit, "sourceOrganization": jurisdiction},
            "collectionDetail": {"projectNo": project, "capturedAt": "2026-11-05T10:00:00+08:00",
                                 "sourceUrl": f"http://source.invalid/#/xfjd/projectDetail?runId=1&RWID={url_rwid or rwid}",
                                 "fields": {"项目编号": project, "单位名称": unit, "单位地址": address, "消防管辖": jurisdiction,
                                        "文书目录": [{"title": "[安]非承诺制-32002299A202600001-合成单位", "children": []},
                                                 {"title": f"[产]开业检查-{project}-{unit}", "children": [{"title": "受理登记", "children": []}]}]}}}


def make_basic_inputs(tmp_path: Path, records: dict, waterline: dict | None = None):
    capture, water, routes = tmp_path / "capture.json", tmp_path / "waterline.json", tmp_path / "routes.json"
    capture.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")
    water.write_text(json.dumps({"cases": waterline or {}}, ensure_ascii=False), encoding="utf-8")
    routes.write_text(json.dumps({"baseId": BASE, "routes": [{"sourceJurisdictionExact": SRC, "teamOptionExact": "梁溪大队", "teamTableId": TEAM_TABLE,
                                                              "savedTeamRecordId": TEAM_REC}]}, ensure_ascii=False), encoding="utf-8")
    return capture, water, routes


class BuildTransport(MemoryTransport):
    list_calls = 0

    def list_rows(self, base, table, field_ids=None):
        type(self).list_calls += 1
        rows = {rid: dict(row) for (t, rid), row in self.rows.items() if t == table}
        return {rid: ({f: v for f, v in row.items() if f in field_ids} if field_ids else row) for rid, row in rows.items()}

    def upsert(self, base, table, record_id, fields):
        row = self.rows[(table, record_id)]
        row.update({f: (w if not isinstance(w, str) or len(w) != 16 else w.replace(" ", "T") + ":00.000+08:00") for f, w in fields.items()})
        return {"ok": True, "data": {"record": {"record_id": record_id}}}


RESULT_F, DATE_SRC_F = "fld77TOx5G", "fld4upZMpZ"


def _add_lifecycle_fields(v3: Path, v9: Path) -> None:
    """Expose 初查结果 / 初查日期来源 as the v11 contract does (v3 create candidates + v9 whitelist, with option lists)."""
    fields = [
        {**_f(RESULT_F, "初查结果", "select", "source-sync-whitelist", "allowed(v11)"), "options": [{"name": n} for n in ("合格", "不合格", "待核实", "待结果")]},
        {**_f(DATE_SRC_F, "初查日期来源", "select", "source-sync-whitelist", "allowed(v11)"), "options": [{"name": n} for n in ("登记系统", "来源详情", "监督系统")]},
    ]
    doc = json.loads(v3.read_text(encoding="utf-8"))
    doc["baseContract"]["tables"]["案卷主表"]["fields"] += fields
    doc["extension"]["createFieldsCandidate"] += [_cand(CASE, f["fieldId"], f["name"], "select", "create-once-source-scalar", "candidate-ready", "omit unknown") for f in fields]
    v3.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    v9.write_text(json.dumps(doc["baseContract"], ensure_ascii=False), encoding="utf-8")


def run_build(tmp_path, records, *, waterline=None, transport=None, capture_sha=None, batch=20, ledger_dir=None, lifecycle=False):
    v3, v9 = make_basic_contracts(tmp_path)
    if lifecycle:
        _add_lifecycle_fields(v3, v9)
    capture, water, routes = make_basic_inputs(tmp_path, records, waterline)
    transport = transport or BuildTransport()
    report = sync.build_basic_specs(
        capture_path=capture, capture_sha=capture_sha or sha(capture), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
        routes_path=routes, routes_sha=sha(routes), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
        transport=transport, out_dir=tmp_path / "specs", log_root=tmp_path / "logs", batch_size=batch, ledger_dir=ledger_dir or tmp_path / "build-ledger")
    return report, transport, (v3, v9)


def test_build_makes_a_v3_create_with_per_field_evidence_and_omits_unknown_initial_date(tmp_path):
    transport = BuildTransport()
    report, transport, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, transport=transport)
    assert report["status"] == "OK" and report["counts"]["toCreate"] == 1 and report["createdWithInitialDate"] == 0
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    fields = spec["creates"][0]["fields"]
    assert set(fields) == {KEY, UNIT, ADDR, SELECT_TEAM, LINK_TEAM}                      # 没有初查日期：未知不以受理日代替
    assert fields[SELECT_TEAM]["new"] == "梁溪大队" and fields[LINK_TEAM]["new"] == TEAM_REC
    ref = fields[UNIT]["basis"]["sourceRef"]
    assert "records.1001.collectionDetail.fields.单位名称" in ref and sha(tmp_path / "capture.json") in ref and "RWID=1001" in ref and PN_NEW in ref
    assert not transport.creates                                                           # 生成入口只读云，不创建


def _original(tmp_path, project, rwid, rows, *, name="orig.json", url_rwid=None, proj=None):
    """A stored SourceIdentityV1 original beside the waterline (the business root)."""
    folder = tmp_path / "evidence"
    folder.mkdir(exist_ok=True)
    doc = {"schemaVersion": "SourceIdentityV1", "projectNo": proj or project, "rwid": rwid,
           "sourceUrl": f"http://source.invalid/#/xfjd/projectDetail?RWID={url_rwid or rwid}",
           "fields": {"项目编号": proj or project, "检查产品信息": rows}}
    path = folder / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _row(day, recheck="0"):
    return {"产品名称（规格型号）": "合成产品", "页面记录": {"JCRQ": f"{day} 19:00:00", "SFCPFC": recheck, "SFHG": "0"}}


def _classification(path, day, *, sha=None, rel=None):
    return {"initialInspectionDate": day, "evidencePath": rel or f"evidence\\{path.name}", "evidenceSha256": sha or "sha256:" + sha_of(path),
            "observedAt": "2026-11-05T10:00:00+08:00"}


def sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date_build(tmp_path, project, classification, *, rwid="1001"):
    water = {project: {"source": {"classification": classification}}}
    report, _, _ = run_build(tmp_path, {rwid: _detail(project, rwid)}, waterline=water)
    creates = {c["stableKey"]["value"]: c for c in json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))["creates"]} if report["specs"] else {}
    return report, creates


def test_build_keeps_an_initial_date_only_when_the_stored_original_proves_exactly_that_initial_day(tmp_path):
    original = _original(tmp_path, PN_NEW, "1001", [_row("2026-09-21"), _row("2026-10-30", recheck="1")])
    report, creates = _date_build(tmp_path, PN_NEW, _classification(original, "2026-09-21"))
    field = creates[PN_NEW]["fields"][DATE_FIELD]
    assert field["new"] == "2026-09-21" and report["initialDatesOmitted"] == []
    ref = field["basis"]["sourceRef"]
    assert "JCRQ(SFCPFC=0)=2026-09-21" in ref and "sha256:" + sha_of(original) in ref and "RWID=1001" in ref and PN_NEW in ref


@pytest.mark.parametrize("scenario,reason", [
    ("missing", "原件不存在"),
    ("bad-sha", "摘要"),
    ("other-project", "不是同一案"),
    ("other-rwid-url", "不是同一案"),
    ("wrong-day", "不一致"),
    ("recheck-only", "没有可读的初查"),
    ("recheck-day-claimed", "不一致"),
    ("two-initial-days", "不一致"),
    ("outside-root", "越出业务根"),
])
def test_build_omits_the_date_but_keeps_the_main_row_when_the_basis_is_not_proven(tmp_path, scenario, reason):
    rows = [_row("2026-09-21")]
    kwargs = {}
    if scenario == "other-project":
        kwargs["proj"] = "32002299C209999999"
    if scenario == "other-rwid-url":
        kwargs["url_rwid"] = "9999"
    if scenario == "recheck-only":
        rows = [_row("2026-09-21", recheck="1")]
    if scenario == "recheck-day-claimed":
        rows = [_row("2026-08-01"), _row("2026-09-21", recheck="1")]
    if scenario == "two-initial-days":
        rows = [_row("2026-09-21"), _row("2026-09-22")]
    original = _original(tmp_path, PN_NEW, "1001", rows, **kwargs)
    classification = _classification(original, "2026-09-21")
    if scenario == "missing":
        classification = _classification(original, "2026-09-21", rel="evidence\\nope.json")
    if scenario == "bad-sha":
        classification = _classification(original, "2026-09-21", sha="sha256:" + "0" * 64)
    if scenario == "wrong-day":
        classification = _classification(original, "2026-09-22")
    if scenario == "outside-root":
        classification = _classification(original, "2026-09-21", rel="..\\..\\evidence\\orig.json")
    report, creates = _date_build(tmp_path, PN_NEW, classification)
    assert DATE_FIELD not in creates[PN_NEW]["fields"] and creates[PN_NEW]["fields"][UNIT]["new"] == "合成单位"   # 主行不受影响
    assert report["counts"]["blockedEntities"] == 0 and len(report["initialDatesOmitted"]) == 1
    assert reason in report["initialDatesOmitted"][0]["reason"]


def test_build_without_a_registered_initial_date_and_without_any_acceptance_date_omits_it_silently(tmp_path):
    report, creates = _date_build(tmp_path, PN_NEW, {"initialResult": "QUALIFIED"})
    assert DATE_FIELD not in creates[PN_NEW]["fields"] and report["initialDatesOmitted"] == [] and report["initialDateAcceptanceFallbacks"] == []


@pytest.mark.parametrize("tree,reason", [
    ([{"title": "[处]行政处罚-32002299X202600001-合成单位", "children": []}], "产品根"),
    ([], "产品根"),
    ([{"title": "[产]开业检查-32002299C209999999-合成单位", "children": []}], "产品根"),
    ([{"title": f"[产]开业检查-{PN_NEW}-合成单位", "children": []}, {"title": "[产]开业检查-32002299C209999999-合成单位", "children": []}], "产品根"),
])
def test_build_blocks_a_c_numbered_case_without_its_own_unique_product_root(tmp_path, tree, reason):
    record = _detail(PN_NEW, "1001")
    record["collectionDetail"]["fields"]["文书目录"] = tree
    report, _, _ = run_build(tmp_path, {"1001": record, "1002": _detail(PN_FILL, "1002")})
    assert report["status"] == "PARTIAL" and report["counts"]["blockedEntities"] == 1 and report["counts"]["toCreate"] == 1
    assert reason in json.dumps(report["blockedEntities"], ensure_ascii=False)


def test_build_does_not_gate_on_product_quantity_zero_or_missing(tmp_path):
    record = _detail(PN_NEW, "1001")
    record["collectionDetail"]["fields"]["检查产品信息"] = []
    report, _, _ = run_build(tmp_path, {"1001": record})
    assert report["status"] == "OK" and report["counts"]["toCreate"] == 1


@pytest.mark.parametrize("record,reason", [
    (_detail(PN_NEW, "1001", url_rwid="9999"), "RWID"),
    (_detail(PN_NEW, "1001", listed_unit="另一个单位"), "单位"),
    (_detail(PN_NEW, "1001", jurisdiction="无锡市未知区消防救援局"), "路由未匹配"),
])
def test_build_blocks_only_the_wrong_case_and_never_guesses_a_route(tmp_path, record, reason):
    if reason == "单位":
        record["collectionDetail"]["fields"]["单位名称"] = "合成单位"
    records = {"1001": record, "1002": _detail(PN_FILL, "1002")}
    report, _, _ = run_build(tmp_path, records)
    assert report["status"] == "PARTIAL" and report["counts"]["blockedEntities"] == 1 and report["counts"]["toCreate"] == 1
    assert reason in json.dumps(report["blockedEntities"], ensure_ascii=False)
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    assert [c["stableKey"]["value"] for c in spec["creates"]] == [PN_FILL]


def test_build_refuses_a_capture_whose_digest_is_not_the_pinned_one(tmp_path):
    with pytest.raises(sync.SyncError, match="摘要"):
        run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, capture_sha="0" * 64)


def test_build_skips_non_c_cases_and_blocks_an_old_number_case_that_has_no_scope_evidence(tmp_path):
    # 范围按真实初查日期(>=2026-01-01)而不是项目编号年份：旧号段案无总账、无“检查日期”证据时不新建也不静默丢弃
    records = {"1": _detail("32002299C202500001", "1"), "2": _detail("32002299J202600001", "2"), "3": _detail(PN_NEW, "3")}
    report, _, _ = run_build(tmp_path, records)
    assert report["counts"]["toCreate"] == 1 and report["counts"]["blockedEntities"] == 1
    assert report["blockedEntities"][0]["projectNo"] == "32002299C202500001"
    assert "项目编号年份早于2026" in json.dumps(report["blockedEntities"], ensure_ascii=False)


def test_build_creates_an_old_number_case_that_is_in_the_ledger_because_its_initial_date_is_in_scope(tmp_path):
    old = "32002299C202500002"
    records = {"1": _detail(old, "1"), "3": _detail(PN_NEW, "3")}
    report, _, _ = run_build(tmp_path, records, waterline={old: {"projectNo": old, "state": "COMPLETED"}})
    assert report["counts"]["toCreate"] == 2 and report["counts"]["blockedEntities"] == 0


def test_build_never_recreates_a_case_excluded_by_the_initial_date_scope(tmp_path):
    excluded = "32002299C202600003"
    register = tmp_path / "范围外保留" / "plan-20261111-test" / "exclusion-register.json"
    register.parent.mkdir(parents=True)
    register.write_text(json.dumps({"entries": [{"projectNo": excluded, "status": "EXCLUDED"}]}), encoding="utf-8")
    records = {"1": _detail(excluded, "1"), "3": _detail(PN_NEW, "3")}
    report, _, _ = run_build(tmp_path, records)
    assert report["counts"]["toCreate"] == 1 and report["counts"]["blockedEntities"] == 0
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    assert [c["stableKey"]["value"] for c in spec["creates"]] == [PN_NEW]


def test_build_scopes_by_fresh_cloud_keys_and_fills_only_empty_cells_without_overwriting_values(tmp_path):
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, UNIT: "合成单位", ADDR: "", SELECT_TEAM: ["梁溪大队"]}
    transport.rows[(CASE, "recEXIST000002")] = {KEY: PN_OTHER, UNIT: "合成单位", ADDR: "云端人工改过的地址"}
    transport.rows[(TEAM_TABLE, TEAM_REC)] = {TEAM_NAME: "梁溪大队"}
    records = {"1002": _detail(PN_FILL, "1002", address="来源地址A"), "1003": _detail(PN_OTHER, "1003", address="来源地址B")}
    report, transport, _ = run_build(tmp_path, records, transport=transport)
    assert report["counts"]["toCreate"] == 0 and report["counts"]["toFill"] == 1 and report["counts"]["differingNotOverwritten"] == 1
    fill = next(s for s in report["specs"] if s["kind"] == "fill")
    spec = json.loads(Path(fill["path"]).read_text(encoding="utf-8"))
    update = spec["updates"][0]
    assert update["recordId"] == "recEXIST000001" and update["fields"][ADDR]["new"] == "来源地址A" and update["fields"][ADDR]["expectedOld"] is None
    assert report["differing"][0]["projectNo"] == PN_OTHER


def test_built_create_spec_runs_through_the_normal_path_and_a_rerun_writes_nothing(tmp_path):
    transport = BuildTransport()
    report, transport, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, transport=transport)
    spec_path = Path(report["specs"][0]["path"])
    done = sync.run(spec_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger")
    assert done["status"] == "OK" and len(transport.creates) == 1
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["logDir"] = str(tmp_path / "logs" / "rerun")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    again = sync.run(spec_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger")
    assert again["records"][0]["status"] == "ALREADY_PRESENT_VERIFIED" and len(transport.creates) == 1      # 同输入重跑：业务写调用数不增


def test_built_fill_spec_refuses_when_the_cell_was_filled_meanwhile_and_fills_when_still_empty(tmp_path):
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, UNIT: "合成单位", ADDR: ""}
    report, transport, _ = run_build(tmp_path, {"1002": _detail(PN_FILL, "1002", address="来源地址A")}, transport=transport)
    fill_path = Path(next(s for s in report["specs"] if s["kind"] == "fill")["path"])
    transport.rows[(CASE, "recEXIST000001")][ADDR] = "同期有人填了"
    with pytest.raises(sync.RuntimeSyncError, match="旧值应为空"):
        sync.run(fill_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger")
    assert transport.rows[(CASE, "recEXIST000001")][ADDR] == "同期有人填了"
    transport.rows[(CASE, "recEXIST000001")][ADDR] = ""
    spec = json.loads(fill_path.read_text(encoding="utf-8"))
    spec["logDir"] = str(tmp_path / "logs" / "fill-ok")
    fill_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    done = sync.run(fill_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger2")
    assert done["status"] == "OK" and transport.rows[(CASE, "recEXIST000001")][ADDR] == "来源地址A"


def test_partial_never_exits_zero_on_either_entry(tmp_path, monkeypatch, capsys):
    transport = BuildTransport()
    report, transport, _ = run_build(tmp_path, {"1002": _detail(PN_FILL, "1002")}, transport=transport)
    spec_path = Path(report["specs"][0]["path"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["creates"].append(_wire_case(key=PN_NEW, source="未知管辖"))         # 路由合同外：只阻该实体
    monkeypatch.setattr(sync, "LarkCliTransport", lambda *a, **k: transport)
    import scripts.registry_cli as registry_cli

    spec["logDir"] = str(tmp_path / "logs" / "p1")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert sync.main(["--spec", str(spec_path), "--execute", "--ledger-dir", str(tmp_path / "led1")]) == 4
    spec["logDir"] = str(tmp_path / "logs" / "p2")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        registry_cli.main(["feishu-source-sync", "--spec", str(spec_path), "--execute", "--ledger-dir", str(tmp_path / "led2")])
    assert stop.value.code == 4 and "PARTIAL" in capsys.readouterr().err


@pytest.mark.parametrize("juris,code", [(SRC, None), ("无锡市未知区消防救援局", 4)])
def test_registry_cli_build_command_reports_partial_with_exit_4_and_writes_no_cloud(tmp_path, monkeypatch, capsys, juris, code):
    import scripts.registry_cli as registry_cli

    transport = BuildTransport()
    v3, v9 = make_basic_contracts(tmp_path)
    capture, water, routes = make_basic_inputs(tmp_path, {"1001": _detail(PN_NEW, "1001", jurisdiction=juris), "1002": _detail(PN_FILL, "1002")})
    monkeypatch.setattr(sync, "LarkCliTransport", lambda *a, **k: transport)
    argv = ["feishu-source-build", "--capture", str(capture), "--capture-sha", sha(capture), "--waterline", str(water), "--contract-v3", str(v3),
            "--contract-v3-sha", sha(v3), "--contract-v9", str(v9), "--contract-v9-sha", sha(v9), "--routes", str(routes), "--routes-sha", sha(routes),
            "--base-token", BASE, "--profile", "offline-fake", "--out-dir", str(tmp_path / "o"), "--log-root", str(tmp_path / "l"), "--ledger-dir", str(tmp_path / "bl")]
    if code is None:
        registry_cli.main(argv)
    else:
        with pytest.raises(SystemExit) as stop:
            registry_cli.main(argv)
        assert stop.value.code == code and "PARTIAL" in capsys.readouterr().err
    assert not transport.creates and (tmp_path / "o" / "build-report.json").is_file()


def test_registry_cli_build_command_applies_the_date_and_category_gates_through_real_files(tmp_path, monkeypatch):
    import scripts.registry_cli as registry_cli

    good = _original(tmp_path, PN_NEW, "1001", [_row("2026-09-21")], name="good.json")
    bad = _original(tmp_path, PN_FILL, "1002", [_row("2026-09-22")], name="bad.json")        # 登记日与原件不一致
    non_product = _detail(PN_OTHER, "1003")
    non_product["collectionDetail"]["fields"]["文书目录"] = [{"title": "[监]日常监督抽查-32002299J202600001-合成单位", "children": []}]
    records = {"1001": _detail(PN_NEW, "1001"), "1002": _detail(PN_FILL, "1002"), "1003": non_product}
    water = {PN_NEW: {"source": {"classification": _classification(good, "2026-09-21")}},
             PN_FILL: {"source": {"classification": _classification(bad, "2026-09-21")}}}
    v3, v9 = make_basic_contracts(tmp_path)
    capture, waterline, routes = make_basic_inputs(tmp_path, records, water)
    transport = BuildTransport()
    monkeypatch.setattr(sync, "LarkCliTransport", lambda *a, **k: transport)
    argv = ["feishu-source-build", "--capture", str(capture), "--capture-sha", sha(capture), "--waterline", str(waterline), "--contract-v3", str(v3),
            "--contract-v3-sha", sha(v3), "--contract-v9", str(v9), "--contract-v9-sha", sha(v9), "--routes", str(routes), "--routes-sha", sha(routes),
            "--base-token", BASE, "--profile", "offline-fake", "--out-dir", str(tmp_path / "o"), "--log-root", str(tmp_path / "l"), "--ledger-dir", str(tmp_path / "bl")]
    with pytest.raises(SystemExit) as stop:
        registry_cli.main(argv)
    assert stop.value.code == 4                                      # 非产品 C 案被挡 => PARTIAL，不是 0
    report = json.loads((tmp_path / "o" / "build-report.json").read_text(encoding="utf-8"))
    creates = {c["stableKey"]["value"]: c for c in json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))["creates"]}
    assert set(creates) == {PN_NEW, PN_FILL} and creates[PN_NEW]["fields"][DATE_FIELD]["new"] == "2026-09-21"
    assert DATE_FIELD not in creates[PN_FILL]["fields"] and report["initialDatesOmitted"][0]["projectNo"] == PN_FILL
    assert report["blockedEntities"][0]["projectNo"] == PN_OTHER and not transport.creates


def _acceptance(tmp_path, intent, **over):
    item = {"acceptedBy": "user", "intentId": intent, "userQuote": "我接受该精确请求迟到写入的风险", "sourceRef": "dialog#1", "acceptedAt": "2026-11-08T07:00:00Z", **over}
    path = tmp_path / "accept.json"
    path.write_text(json.dumps({"schemaVersion": "UserRiskAcceptanceV1", "acceptances": [item]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_human_release_without_the_users_own_risk_acceptance_is_refused_before_any_call(tmp_path):
    spec = {"humanRelease": {"reason": "r", "operator": "Claude 实施者", "intentId": "i1", "acknowledgesLateWriteRisk": True, "evidence": [{"kind": "x", "ref": "y"}]}}
    with pytest.raises(sync.SyncError, match="助手不得自署"):
        sync.require_user_acceptance_for_release(spec, None)
    for bad in ({"acceptedBy": "assistant"}, {"userQuote": ""}, {"sourceRef": " "}):
        with pytest.raises(sync.SyncError, match="acceptedBy=user"):
            sync.require_user_acceptance_for_release(spec, _acceptance(tmp_path, "i1", **bad))
    with pytest.raises(sync.SyncError, match="精确 intentId"):
        sync.require_user_acceptance_for_release(spec, _acceptance(tmp_path, "another-intent"))
    sync.require_user_acceptance_for_release(spec, _acceptance(tmp_path, "i1"))        # 用户亲自接受该精确意图才放行
    sync.require_user_acceptance_for_release({"mode": "plan"}, None)                    # 没有 humanRelease 的 spec 不受影响


def test_the_normal_entries_refuse_an_assistant_signed_release_with_zero_cloud_calls(tmp_path, monkeypatch, capsys):
    import scripts.registry_cli as registry_cli

    contract, ledger, transport, key = _late_create(tmp_path, timeout_lost=True)
    intent = sync.Ledger(ledger).state(key)["lastIntent"]
    spec = json.loads(v3_spec(tmp_path, contract, [_wire_case()], mode="reconcile", log="rel").read_text(encoding="utf-8"))
    spec["humanRelease"] = {"reason": "助手自署", "operator": "Claude", "intentId": intent, "acknowledgesLateWriteRisk": True, "evidence": [{"kind": "cli-process-log", "ref": "x"}]}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    calls = []
    monkeypatch.setattr(sync, "LarkCliTransport", lambda *a, **k: calls.append(a) or transport)
    assert sync.main(["--spec", str(path), "--reconcile", "--ledger-dir", str(ledger)]) == 2
    assert registry_cli.main(["feishu-source-sync", "--spec", str(path), "--reconcile", "--ledger-dir", str(ledger)]) != 0
    assert "助手不得自署" in capsys.readouterr().err
    assert not calls and sync.Ledger(ledger).state(key)["state"] == "BLOCKED"          # 账本仍封锁，没有放行事件
    assert not [e for e in sync.Ledger(ledger).events(key) if e["event"] == "human_release"]


def test_repeated_zero_match_and_other_variants_keep_the_unknown_blocked(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, timeout_lost=True)
    for number in range(3):
        report = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], mode="reconcile", log=f"z{number}"), transport, ledger, reconcile=True)
        assert report["targets"][0]["outcome"] == "ZERO_MATCH"
        assert sync.Ledger(ledger).state(key)["state"] == "BLOCKED"
    changed = _wire_case(unit="换了单位名称的同键变体")
    before = len(transport.creates)
    with pytest.raises(sync.SyncError, match="未对账"):
        _run_v3(v3_spec(tmp_path, contract, [changed], log="variant"), transport, ledger, execute=True)
    assert len(transport.creates) == before                                                # 稳定键变体：云写调用 0


def test_build_keeps_a_target_with_an_unaccepted_human_release_undecided_and_never_plans_it(tmp_path):
    transport = BuildTransport()
    ledger = tmp_path / "ledger"
    key = sync.Ledger.target_key(BASE, CASE, "create:" + PN_NEW)
    sync.Ledger(ledger).append(key, {"event": "human_release", "intentId": "iX", "operator": "assistant"})
    v3, v9 = make_basic_contracts(tmp_path)
    capture, water, routes = make_basic_inputs(tmp_path, {"1001": _detail(PN_NEW, "1001"), "1002": _detail(PN_FILL, "1002")})
    args = dict(capture_path=capture, capture_sha=sha(capture), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
                routes_path=routes, routes_sha=sha(routes), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
                transport=transport, log_root=tmp_path / "logs", ledger_dir=ledger)
    report = sync.build_basic_specs(out_dir=tmp_path / "o1", **args)
    assert report["status"] == "PARTIAL" and [b["projectNo"] for b in report["blockedEntities"]] == [PN_NEW] and report["counts"]["toCreate"] == 1
    accepted = _acceptance(tmp_path, "iX")
    report = sync.build_basic_specs(out_dir=tmp_path / "o2", user_accepted_releases=accepted, **args)
    assert report["status"] == "OK" and report["counts"]["toCreate"] == 2


def test_build_year_gate_is_2026_or_later_not_only_2026(tmp_path):
    records = {"1": _detail("32002299C202500001", "1"), "2": _detail("32002299C202600009", "2"), "3": _detail("32002299C202700001", "3")}
    report, _, _ = run_build(tmp_path, records)
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    assert sorted(c["stableKey"]["value"] for c in spec["creates"]) == ["32002299C202600009", "32002299C202700001"]


# ---------------------------------------------------------------------------------------------
# r27 七项最小修正：账根、必须带账、普通 UNKNOWN 局部阻断、创建未决不转补空、更新对账精确 intent
# ---------------------------------------------------------------------------------------------

def _unknown_create_in(ledger_dir, project, intent="iUNK"):
    ledger = sync.Ledger(ledger_dir)
    ledger.initialize()
    key = sync.Ledger.target_key(BASE, CASE, "create:" + project)
    ledger.append(key, {"event": "intent", "kind": "create", "intentId": intent, "stableKey": {"fieldId": KEY, "value": project}, "fields": {}})
    ledger.append(key, {"event": "final", "intentId": intent, "status": "UNKNOWN", "detail": "UNKNOWN: 官方 CLI 输出不是 JSON"})
    return key


def _listing(root):
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size, p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))


def test_ledger_root_that_is_missing_or_wrong_is_refused_and_never_created(tmp_path):
    contract = make_v3_contract(tmp_path)
    for name in ("never-created", "wrong-place"):
        missing = tmp_path / name
        with pytest.raises(sync.SyncError, match="账根"):
            _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log=f"lr-{name}"), MemoryTransport(), missing, execute=True)
        assert not missing.exists()                                                   # 读空之后也不会被悄悄建成另一本账
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(sync.SyncError, match="账根"):
        _run_v3(v3_spec(tmp_path, contract, [_wire_case()], log="lr-empty"), MemoryTransport(), empty_dir, execute=True)
    assert not (empty_dir / "targets").exists() and not (empty_dir / "locks").exists()


def test_ledger_root_that_is_another_initialized_empty_ledger_is_not_the_formal_one(tmp_path):
    """结构合法、已 initialize 的另一本空账，在正常入口(execute/reconcile/build)一律在任何外调与持久写之前被拒绝。"""
    contract = make_v3_contract(tmp_path)
    other = tmp_path / "another-ledger"
    sync.Ledger(other).initialize()
    before = _listing(other)
    transport = MemoryTransport()
    BuildTransport.list_calls = 0
    for mode, kw in (("execute", {"execute": True}), ("reconcile", {"reconcile": True})):
        spec = v3_spec(tmp_path, contract, [_wire_case()], mode=mode, log=f"other-{mode}")
        with pytest.raises(sync.SyncError, match="唯一正式 P18 账根"):
            sync.run(spec, execute_flag=kw.get("execute", False), reconcile_flag=kw.get("reconcile", False), transport=transport, ledger_dir=other)
    assert transport.creates == [] and transport.finds == 0                          # 外调 0、云写 0
    assert _listing(other) == before                                                 # 账内无任何新增/改动(含 locks/)
    assert not (tmp_path / "other-execute").exists() and not (tmp_path / "other-reconcile").exists()   # logDir 也未建
    v3, v9 = make_basic_contracts(tmp_path)
    capture, water, routes = make_basic_inputs(tmp_path, {"1001": _detail(PN_NEW, "1001")})
    with pytest.raises(sync.SyncError, match="唯一正式 P18 账根"):
        sync.build_basic_specs(capture_path=capture, capture_sha=sha(capture), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
                               routes_path=routes, routes_sha=sha(routes), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
                               transport=BuildTransport(), out_dir=tmp_path / "o", log_root=tmp_path / "l", ledger_dir=other)
    assert not (tmp_path / "o").exists() and _listing(other) == before
    assert BuildTransport.list_calls == 0                                            # build 在任何云读取之前就拒绝


def test_ledger_root_identity_is_the_formal_path_in_either_notation_and_is_checked_read_only():
    assert sync.canonical_ledger_path("D:\\ws\\05-temp\\20261104_飞书大队权限\\P18运行账本\\实施") == sync.canonical_ledger_path(sync.P18_LEDGER_ROOT)
    assert sync.canonical_ledger_path("d:/ws/05-temp/20261104_飞书大队权限/P18运行账本/实施/") == sync.canonical_ledger_path(sync.P18_LEDGER_ROOT)
    assert sync.canonical_ledger_path("/mnt/d/ws/05-temp/20261104_飞书大队权限/P18运行账本/实施x") != sync.canonical_ledger_path(sync.P18_LEDGER_ROOT)
    root = Path(sync.P18_LEDGER_ROOT)
    if not root.is_dir():
        pytest.skip("正式 P18 账根不可达")
    before = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
    sync.Ledger(root).validate_root()                                                # 真实 validator，只读
    after = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
    assert before == after
    with pytest.raises(sync.SyncError, match="正式 P18 账根"):
        sync.Ledger(root).initialize()                                               # 对正式根永不 initialize


def test_ledger_root_isolated_test_ledger_needs_marker_the_internal_switch_and_cannot_be_the_formal_root(tmp_path):
    isolated = tmp_path / "isolated"
    sync.Ledger(isolated).initialize()
    sync.Ledger(isolated, isolated_test=True).validate_root()                        # 标记 + 内部开关：可用
    with pytest.raises(sync.SyncError, match="唯一正式 P18 账根"):
        sync.Ledger(isolated).validate_root()                                        # 没有内部开关：同一目录在正常入口不可用
    unmarked = tmp_path / "unmarked"
    (unmarked / "targets").mkdir(parents=True)
    with pytest.raises(sync.SyncError, match="隔离测试账"):
        sync.Ledger(unmarked, isolated_test=True).validate_root()
    root = Path(sync.P18_LEDGER_ROOT)
    if root.is_dir():
        with pytest.raises(sync.SyncError, match="隔离测试账"):
            sync.Ledger(root, isolated_test=True).validate_root()                    # 正式根不能被当作隔离账


def test_ledger_root_normal_cli_entries_never_expose_the_isolated_switch(tmp_path, monkeypatch, capsys):
    import scripts.registry_cli as registry_cli

    other = tmp_path / "another"
    sync.Ledger(other).initialize()
    contract = make_v3_contract(tmp_path)
    spec = v3_spec(tmp_path, contract, [_wire_case()], log="cli-other")
    calls = []
    monkeypatch.setattr(sync, "LarkCliTransport", lambda *a, **k: calls.append(a) or MemoryTransport())
    assert sync.main(["--spec", str(spec), "--execute", "--ledger-dir", str(other)]) == 2
    assert registry_cli.main(["feishu-source-sync", "--spec", str(spec), "--execute", "--ledger-dir", str(other)]) != 0
    assert "唯一正式 P18 账根" in capsys.readouterr().err and not calls and not (tmp_path / "cli-other").exists()
    assert "isolated" not in " ".join(a.option_strings[0] for a in registry_cli.build_parser()._subparsers._group_actions[0].choices["feishu-source-sync"]._actions if a.option_strings)


def test_build_blocks_only_the_target_with_an_unresolved_create_and_lets_independent_cases_continue(tmp_path):
    ledger = tmp_path / "ledger"
    _unknown_create_in(ledger, PN_NEW)
    report, _, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001"), "1002": _detail(PN_FILL, "1002")}, ledger_dir=ledger)
    assert report["status"] == "PARTIAL" and report["counts"]["toCreate"] == 1
    assert [b["projectNo"] for b in report["blockedEntities"]] == [PN_NEW] and "create 意图未决" in json.dumps(report["blockedEntities"], ensure_ascii=False)


def test_a_late_arriving_row_cannot_turn_an_unresolved_create_into_a_fill(tmp_path):
    ledger = tmp_path / "ledger"
    _unknown_create_in(ledger, PN_NEW)
    transport = BuildTransport()
    transport.rows[(CASE, "recLATE0000001")] = {KEY: PN_NEW, UNIT: "合成单位", ADDR: ""}                 # 迟到的创建结果：行出现了，地址为空
    for variation in (_detail(PN_NEW, "1001"), _detail(PN_NEW, "1001", address="另一份来源地址", unit="合成单位")):
        report, _, _ = run_build(tmp_path, {"1001": variation}, transport=transport, ledger_dir=ledger)
        assert report["counts"]["toFill"] == 0 and report["counts"]["toCreate"] == 0 and report["blockedEntities"][0]["projectNo"] == PN_NEW
        assert report["specs"] == []                                                                      # 字段/证据变化也不绕过旧创建未决


def test_an_unresolved_update_blocks_only_its_own_record_in_the_build(tmp_path):
    ledger = sync.Ledger(tmp_path / "ledger")
    ledger.initialize()
    key = sync.Ledger.target_key(BASE, CASE, "recEXIST000001")
    ledger.append(key, {"event": "intent", "intentId": "iU2", "stableKey": {"fieldId": KEY, "value": PN_FILL}, "fields": {}})   # 无终态：进程中断
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, UNIT: "合成单位", ADDR: ""}
    transport.rows[(CASE, "recEXIST000002")] = {KEY: PN_OTHER, UNIT: "合成单位", ADDR: ""}
    records = {"1002": _detail(PN_FILL, "1002", address="地址A"), "1003": _detail(PN_OTHER, "1003", address="地址B")}
    report, _, _ = run_build(tmp_path, records, transport=transport, ledger_dir=tmp_path / "ledger")
    assert [b["projectNo"] for b in report["blockedEntities"]] == [PN_FILL] and report["counts"]["toFill"] == 1 and report["status"] == "PARTIAL"


def test_v2_execute_refuses_a_fill_for_a_stable_key_whose_create_is_still_unresolved(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _unknown_create_in(ledger_dir, PN_FILL)
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, UNIT: "合成单位", ADDR: ""}
    report, transport, _ = run_build(tmp_path, {"1002": _detail(PN_FILL, "1002", address="地址A")}, transport=transport, ledger_dir=tmp_path / "other-ledger")
    fill = Path(next(s for s in report["specs"] if s["kind"] == "fill")["path"])               # 手工拿到 fill 说明(另一本账里没有封锁)
    with pytest.raises(sync.SyncError, match="未决创建"):
        sync.run(fill, execute_flag=True, transport=transport, ledger_dir=ledger_dir)
    assert transport.rows[(CASE, "recEXIST000001")][ADDR] == ""


def test_the_normal_generation_and_execution_flow_never_writes_or_emits_a_human_release(tmp_path):
    transport = BuildTransport()
    report, transport, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, transport=transport)
    spec_path = Path(report["specs"][0]["path"])
    assert "humanRelease" not in json.loads(spec_path.read_text(encoding="utf-8"))
    sync.run(spec_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "build-ledger")
    events = [json.loads(line) for p in (tmp_path / "build-ledger" / "targets").glob("*.jsonl") for line in p.read_text(encoding="utf-8").splitlines()]
    assert events and not [e for e in events if e["event"] in {"human_release", "reconciled"}]


# ---------------------------------------------------------------------------------------------
# R35-D1/D2: the latest base contract (DENIED / options) decides before any extension candidate
# ---------------------------------------------------------------------------------------------

CHECK_RESULT, PRODUCT_RESULT, PRODUCT_KEY_F, PRODUCT_PARENT = "fld1RPp2yY", "fldJUpLw89", "fldqlwHjUW", "fldD2pT8R7"


def make_reopen_contract(tmp_path: Path, *, extension_reopens: bool = True) -> Path:
    path = make_v3_contract(tmp_path, child_ready=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    tables = value["baseContract"]["tables"]
    tables["检查明细"]["fields"].append({**_f(CHECK_RESULT, "检查结果", "select", "source-unconfirmed-readonly", "DENIED-until-0104-confirms"),
                                      "options": [{"name": "合格", "optionId": "o1"}, {"name": "不合格", "optionId": "o2"}, {"name": "待核实", "optionId": "o3"}]})
    tables["产品明细"]["fields"] += [
        _f(PRODUCT_PARENT, "检查键", "link", "structural-relation"),
        {**_f(PRODUCT_RESULT, "检查结果", "select", "source-unconfirmed-readonly", "DENIED-until-0104-confirms"),
         "options": [{"name": "合格", "optionId": "p1"}, {"name": "不合格", "optionId": "p2"}]},
    ]
    cands = value["extension"]["createFieldsCandidate"]
    cands += [_cand(PRODUCT, PRODUCT_KEY_F, "产品键", "text", "create-once-identity", "candidate-ready", "required; block"),
              _cand(PRODUCT, PRODUCT_PARENT, "检查键", "link", "create-once-parent-link", "candidate-ready", "required; block")]
    if extension_reopens:  # an old extension proposes the two unconfirmed result fields as create-once scalars
        cands += [_cand(CHECK, CHECK_RESULT, "检查结果", "select", "create-once-source-scalar", "candidate-ready", "omit unknown"),
                  _cand(PRODUCT, PRODUCT_RESULT, "检查结果", "select", "create-once-source-scalar", "candidate-ready", "omit unknown")]
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _chain(result_table, result_field, value="不合格"):
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-09T00:00:00Z"}
    check_key = "32002299C202600001|初查"
    check_fields = {CHECK_KEY: {"new": check_key, "basis": basis}, CHECK_PARENT: {"new": "recPLACEHOLDER1", "basis": basis}}
    product_fields = {PRODUCT_KEY_F: {"new": check_key + "|p1", "basis": basis}, PRODUCT_PARENT: {"new": "recPLACEHOLDER1", "basis": basis}}
    (check_fields if result_table == CHECK else product_fields)[result_field] = {"new": value, "basis": basis}
    return [
        _wire_case(),
        {"tableId": CHECK, "stableKey": {"fieldId": CHECK_KEY, "value": check_key},
         "parent": {"createRef": "32002299C202600001", "stableKey": {"fieldId": KEY, "value": "32002299C202600001"}}, "fields": check_fields},
        {"tableId": PRODUCT, "stableKey": {"fieldId": PRODUCT_KEY_F, "value": check_key + "|p1"},
         "parent": {"createRef": check_key, "stableKey": {"fieldId": CHECK_KEY, "value": check_key}}, "fields": product_fields},
    ]


@pytest.mark.parametrize("mode", ["plan", "execute"])
@pytest.mark.parametrize("table,field", [(CHECK, CHECK_RESULT), (PRODUCT, PRODUCT_RESULT)])
def test_r35_d1_latest_denied_result_fields_cannot_be_reopened_by_old_extension(tmp_path, mode, table, field):
    contract = make_reopen_contract(tmp_path)
    transport = MemoryTransport()
    spec = v3_spec(tmp_path, contract, _chain(table, field), mode=mode)
    with pytest.raises(sync.SyncError, match="不得重开|不允许该字段创建"):
        _run_v3(spec, transport, tmp_path / "ledger", execute=(mode == "execute"))
    assert not transport.creates and transport.finds == 0


def test_r35_d1_legal_identity_routing_parent_chain_still_creates_once_without_denied_fields(tmp_path):
    contract = make_reopen_contract(tmp_path)
    chain = _chain(CHECK, CHECK_RESULT)
    del chain[1]["fields"][CHECK_RESULT]
    transport = MemoryTransport()
    report = _run_v3(v3_spec(tmp_path, contract, chain), transport, tmp_path / "ledger", execute=True)
    assert [e["status"] for e in report["records"]] == ["CREATED_VERIFIED"] * 3
    assert [t for t, _ in transport.creates] == [CASE, CHECK, PRODUCT]
    assert all(CHECK_RESULT not in f and PRODUCT_RESULT not in f for _, f in transport.creates)
    again = _run_v3(v3_spec(tmp_path, contract, chain, log="again"), transport, tmp_path / "ledger", execute=True)
    assert [e["status"] for e in again["records"]] == ["ALREADY_PRESENT_VERIFIED"] * 3 and len(transport.creates) == 3


def test_r35_d2_field_index_keeps_options_syncwrite_and_composite_key(tmp_path):
    contract = make_reopen_contract(tmp_path)
    value = sync.load_contract_v3(contract, sha(contract))
    meta = sync.field_index(value["base"])[CHECK][CHECK_RESULT]
    assert meta["options"] == ["合格", "不合格", "待核实"] and meta["syncWrite"].startswith("DENIED") and meta["key"] == f"{CHECK}/{CHECK_RESULT}"
    assert value["sha256"] == sha(contract)


def test_r35_d2_plan_reports_the_exact_contract_sha_and_execute_refuses_a_swapped_one(tmp_path):
    contract = make_reopen_contract(tmp_path, extension_reopens=False)
    plan = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], mode="plan"), MemoryTransport(), tmp_path / "ledger")
    assert plan["contractSha256"] == sha(contract)
    value = sync.load_contract_v3(contract, sha(contract))
    checked = sync.validate_create_spec(json.loads(v3_spec(tmp_path, contract, [_wire_case()]).read_text(encoding="utf-8")), value)
    spec = json.loads(v3_spec(tmp_path, contract, [_wire_case()], log="swap").read_text(encoding="utf-8"))
    spec["contract"]["sha256"] = "0" * 64
    transport = MemoryTransport()
    ledger = sync.Ledger(tmp_path / "ledger2", isolated_test=True)
    ledger.initialize()
    with pytest.raises(sync.SyncError, match="合同摘要不一致"):
        sync.execute_creates(spec, checked, transport, ledger)
    assert not transport.creates and transport.finds == 0


def test_r35_d2_unknown_select_option_blocks_only_the_affected_entity(tmp_path):
    contract = make_reopen_contract(tmp_path, extension_reopens=False)
    value = json.loads(contract.read_text(encoding="utf-8"))
    value["extension"]["createFieldsCandidate"].append(_cand(CHECK, CHECK_RESULT, "检查结果", "select", "create-once-source-scalar", "candidate-ready", "omit unknown"))
    contract.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sync.SyncError, match="不得重开|不允许该字段创建"):
        _run_v3(v3_spec(tmp_path, contract, _chain(CHECK, CHECK_RESULT, "不合格")), MemoryTransport(), tmp_path / "ledger", execute=True)
    # an unknown option on an allowed select field blocks just that entity: the routing field accepts only listed options
    base_value = json.loads(contract.read_text(encoding="utf-8"))
    for f in base_value["baseContract"]["tables"]["案卷主表"]["fields"]:
        if f["fieldId"] == SELECT_TEAM:
            f["options"] = [{"name": "新吴大队", "optionId": "x1"}]
    contract.write_text(json.dumps(base_value, ensure_ascii=False), encoding="utf-8")
    report = _run_v3(v3_spec(tmp_path, contract, [_wire_case()], mode="plan", log="opt"), MemoryTransport(), tmp_path / "ledger")
    assert report["creates"][0]["action"] == "blocked" and any("选项不在最新合同中" in b for b in report["creates"][0]["blocked"])


# ---------------------------------------------------------------------------------------------
# R35-D4: the confirmed lifecycle rules reach the normal sync build (date fallback, 初查结果, 来源外显)
# ---------------------------------------------------------------------------------------------

def _detail_with_acceptance(project, rwid, acceptance="2026年11月04日", **kw):
    record = _detail(project, rwid, **kw)
    record["collectionDetail"]["fields"]["受理日期"] = acceptance
    return record


def _first_create(report):
    return json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))["creates"][0]["fields"]


def test_r35_d4_acceptance_date_is_a_recorded_fallback_when_no_electronic_initial_day_is_proven(tmp_path):
    report, _, _ = run_build(tmp_path, {"1001": _detail_with_acceptance(PN_NEW, "1001")})
    field = _first_create(report)[DATE_FIELD]
    assert field["new"] == "2026-11-04" and "受理日期" in field["basis"]["sourceRef"] and "后备原因" in field["basis"]["sourceRef"]
    assert report["initialDateAcceptanceFallbacks"] == [{"projectNo": PN_NEW, "rwid": "1001", "value": "2026-11-04", "reason": "未登记可证的电子初查日"}]


def test_r35_d4_electronic_day_wins_over_a_different_acceptance_day_and_the_difference_is_kept(tmp_path):
    original = _original(tmp_path, PN_NEW, "1001", [_row("2026-11-03")])
    water = {PN_NEW: {"source": {"classification": _classification(original, "2026-11-03")}}}
    report, _, _ = run_build(tmp_path, {"1001": _detail_with_acceptance(PN_NEW, "1001")}, waterline=water)
    assert _first_create(report)[DATE_FIELD]["new"] == "2026-11-03" and report["initialDateAcceptanceFallbacks"] == []
    assert report["initialDateConflicts"] == [{"projectNo": PN_NEW, "rwid": "1001", "electronic": "2026-11-03", "acceptance": "2026-11-04", "handling": "以电子监督检查记录为准，保留差异"}]


@pytest.mark.parametrize("classification_kind,label", [("UNQUALIFIED", "不合格"), ("QUALIFIED", "合格"), ("PENDING", "待结果"), ("NONE", "待核实")])
def test_r35_d4_create_carries_initial_result_and_supervision_system_source_from_the_latest_contract(tmp_path, classification_kind, label):
    original = _original(tmp_path, PN_NEW, "1001", [_row("2026-11-03")])
    base = _classification(original, "2026-11-03")
    cls = {"UNQUALIFIED": {"initialResult": "UNQUALIFIED"}, "QUALIFIED": {"initialResult": "QUALIFIED"},
           "PENDING": {"initialResult": "UNKNOWN", "reinspectionPending": True}, "NONE": {"initialResult": "UNKNOWN"}}[classification_kind]
    water = {PN_NEW: {"source": {"classification": {**base, **cls}}}}
    report, _, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, waterline=water, lifecycle=True)
    fields = _first_create(report)
    assert fields[RESULT_F]["new"] == label and fields[DATE_SRC_F]["new"] == "监督系统"
    assert report["lifecycleFieldsSkipped"] == []


def test_r35_d4_without_proven_classification_the_result_is_pending_verification_never_a_guess(tmp_path):
    report, _, _ = run_build(tmp_path, {"1001": _detail(PN_NEW, "1001")}, waterline={PN_NEW: {"source": {"classification": {"initialResult": "UNQUALIFIED", "evidencePath": "nope.json", "evidenceSha256": "sha256:" + "0" * 64}}}}, lifecycle=True)
    assert _first_create(report)[RESULT_F]["new"] == "待核实"


def test_r35_d4_contract_without_the_fields_skips_them_and_reports_it_instead_of_failing(tmp_path):
    report, _, _ = run_build(tmp_path, {"1001": _detail_with_acceptance(PN_NEW, "1001")})
    fields = _first_create(report)
    assert RESULT_F not in fields and DATE_SRC_F not in fields
    assert report["lifecycleFieldsSkipped"] and report["status"] == "OK"


def test_r35_d4_fill_upgrades_a_pending_result_with_expected_old_and_never_overwrites_a_final_value(tmp_path):
    original = _original(tmp_path, PN_FILL, "1002", [_row("2026-11-03")])
    other = _original(tmp_path, PN_OTHER, "1003", [_row("2026-11-03")], name="other.json")
    water = {PN_FILL: {"source": {"classification": {**_classification(original, "2026-11-03"), "initialResult": "UNQUALIFIED"}}},
             PN_OTHER: {"source": {"classification": {**_classification(other, "2026-11-03"), "initialResult": "UNQUALIFIED"}}}}
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, UNIT: "合成单位", ADDR: "合成地址1号", RESULT_F: ["待核实"], DATE_FIELD: "2026-11-03", DATE_SRC_F: ["监督系统"]}
    transport.rows[(CASE, "recEXIST000002")] = {KEY: PN_OTHER, UNIT: "合成单位", ADDR: "合成地址1号", RESULT_F: ["合格"], DATE_FIELD: "2026-11-03", DATE_SRC_F: ["监督系统"]}
    report, _, _ = run_build(tmp_path, {"1002": _detail(PN_FILL, "1002"), "1003": _detail(PN_OTHER, "1003")}, waterline=water, transport=transport, lifecycle=True)
    fill = json.loads(Path(next(s for s in report["specs"] if s["kind"] == "fill")["path"]).read_text(encoding="utf-8"))
    assert [u["recordId"] for u in fill["updates"]] == ["recEXIST000001"]
    assert fill["updates"][0]["fields"][RESULT_F] == {"new": "不合格", "expectedOld": "待核实", "basis": fill["updates"][0]["fields"][RESULT_F]["basis"]}
    assert [d["projectNo"] for d in report["differing"]] == [PN_OTHER]
    fill_path = Path(next(s for s in report["specs"] if s["kind"] == "fill")["path"])
    done = sync.run(fill_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger-fill")
    assert done["status"] == "OK" and transport.rows[(CASE, "recEXIST000001")][RESULT_F] == ["不合格"]
    assert transport.rows[(CASE, "recEXIST000002")][RESULT_F] == ["合格"]            # 已是终局值的行一字不动
    transport.rows[(CASE, "recEXIST000001")][RESULT_F] = ["合格"]                     # 同期被人改成别的终局值：旧值保护拒绝
    spec = json.loads(fill_path.read_text(encoding="utf-8"))
    spec["logDir"] = str(tmp_path / "logs" / "fill-again")
    fill_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sync.RuntimeSyncError, match="旧值"):
        sync.run(fill_path, execute_flag=True, transport=transport, ledger_dir=tmp_path / "ledger-fill2")
    assert transport.rows[(CASE, "recEXIST000001")][RESULT_F] == ["合格"]


# ---------------------------------------------------------------------------------------------
# R36-D2-C: V2 update values are validated against the latest contract options before any call or write
# ---------------------------------------------------------------------------------------------

EXACT_CONTRACT = Path("/mnt/d/ws/05-temp/20261105_案卷汇总_不合格名录与下载核查/review/r35-field-lifecycle-prep-20261109/review-entry-prep/"
                      "freeze-r35-delivery-20261109/permission-deliverables/字段合同-20261109.json")
CONTRACT_PARAMS = [pytest.param(None, id="p18-contract"),
                   pytest.param(EXACT_CONTRACT, id="c8e537-contract", marks=pytest.mark.skipif(not EXACT_CONTRACT.is_file(), reason="准确字段合同不可达"))]


def _option_spec(tmp_path, contract_path, value, mode, *, expected_old="合格", field_id=RESULT, name="opt"):
    path = contract_path or CONTRACT
    spec = make_spec(tmp_path, mode, contract={"path": str(path), "sha256": sha(path)}, logDir=str(tmp_path / ("log-" + name)))
    spec["allowlist"] = {CASE: {"records": [REC], "fields": [field_id]}}
    item = {"new": value, "basis": basis()}
    if expected_old is not None:
        item["expectedOld"] = expected_old
    spec["updates"] = [{"tableId": CASE, "recordId": REC, "stableKey": {"fieldId": KEY, "value": STABLE}, "fields": {field_id: item}}]
    return spec, path


def _meta_of(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {t["tableId"]: {f["fieldId"]: f for f in t["fields"]} for t in data["tables"].values()}


EXACT_ONLY = CONTRACT_PARAMS[1:]   # 旧 P18 合同没有列出该字段的选项，无法据以校验


@pytest.mark.parametrize("contract_path", EXACT_ONLY)
@pytest.mark.parametrize("mode", ["plan", "execute"])
def test_r36_d2_c_a_select_value_outside_the_latest_contract_options_is_refused_before_any_call_or_write(tmp_path, contract_path, mode):
    spec, path = _option_spec(tmp_path, contract_path, "不存在的选项", mode)
    svc = FakeService(_meta_of(path), base_rows())
    before = copy.deepcopy(svc.rows)
    with pytest.raises(sync.SyncError, match="选项不在最新合同"):
        run(tmp_path, spec, svc, flag="execute" if mode == "execute" else "none")
    assert svc.calls == [] and svc.upserts == [] and svc.rows == before
    assert not any(p.is_file() for p in (tmp_path / "ledger" / "targets").rglob("*")) and not (tmp_path / "log-opt").exists()   # 账无事件、无日志目录


@pytest.mark.parametrize("contract_path", EXACT_ONLY)
def test_r36_d2_c_a_legal_contract_option_plans_and_executes_with_the_old_value_guard(tmp_path, contract_path):
    path = contract_path or CONTRACT
    allowed = _meta_of(path)[CASE][RESULT]["options"]
    names = [o["name"] if isinstance(o, dict) else o for o in allowed]
    assert "不合格" in names and "合格" in names, "夹具假设：初查结果选项含合格/不合格"
    plan_spec, _ = _option_spec(tmp_path, contract_path, "不合格", "plan", name="plan")
    plan = run(tmp_path, plan_spec, FakeService(_meta_of(path), base_rows()), flag="none")
    assert plan["executable"] is True and plan["externalCalls"] == 0 and plan["requests"][0]["wire"][RESULT] == ["不合格"]
    exec_spec, _ = _option_spec(tmp_path, contract_path, "不合格", "execute", name="exec")
    svc = FakeService(_meta_of(path), base_rows())
    report = run(tmp_path, exec_spec, svc)
    assert report["status"] == "OK" and svc.rows[(CASE, REC)][RESULT] == ["不合格"] and len(svc.upserts) == 1
    # 旧值防覆盖仍生效：期望旧值与真实旧值不同时保留双方，不写
    conflict_spec, _ = _option_spec(tmp_path, contract_path, "不合格", "execute", expected_old="不合格", name="conflict")
    svc2 = FakeService(_meta_of(path), base_rows())
    with pytest.raises(sync.RuntimeSyncError, match="旧值"):
        run(tmp_path, conflict_spec, svc2)
    assert svc2.rows[(CASE, REC)][RESULT] == ["合格"] and svc2.upserts == []


@pytest.mark.parametrize("contract_path", CONTRACT_PARAMS)
def test_r36_d2_c_the_original_wrong_table_field_bad_digest_and_option_cases_stay_refused_with_zero_calls(tmp_path, contract_path):
    path = contract_path or CONTRACT
    wrong, _ = _option_spec(tmp_path, contract_path, "合格", "plan", field_id="fld1RPp2yY", name="wrong-field")   # 检查层结果在别表且最新合同 DENIED
    svc = FakeService(_meta_of(path), base_rows())
    with pytest.raises(sync.SyncError):
        run(tmp_path, wrong, svc, flag="none")
    bad, _ = _option_spec(tmp_path, contract_path, "不合格", "execute", name="bad-sha")
    bad["contract"]["sha256"] = "0" * 64
    with pytest.raises(sync.SyncError):
        run(tmp_path, bad, svc)
    assert svc.calls == [] and svc.upserts == []


# ---------------------------------------------------------------------------------------------
# 云端初查日期有据更正（本案已核验清单日 + 云端旧值门禁）；离线内存传输
# ---------------------------------------------------------------------------------------------

def _datefix_setup(tmp_path, *, manifest_day="2026-06-17", cloud_day="2026-06-18", state="COMPLETED", tamper=False, plan_over=None):
    v3, v9 = make_basic_contracts(tmp_path)
    manifest_dir = tmp_path / "工作区" / "历史工作区" / f"{PN_FILL}-G1"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps({"case": {"projectNo": PN_FILL}, "initialInspection": {"inspectionDate": manifest_day}}), encoding="utf-8")
    recorded = "sha256:" + sha(manifest)
    if tamper:
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(manifest_day, "2026-06-30"), encoding="utf-8")
    water = tmp_path / "waterline.json"
    water.write_text(json.dumps({"cases": {PN_FILL: {"state": state, "local": {"status": "ARCHIVED"},
                                                       "history": {"completions": [{"generation": "G1", "manifestSha256": recorded, "verifiedAt": "2026-11-01T10:00:00+08:00"}]}}}}), encoding="utf-8")
    entry = {"projectNo": PN_FILL, "recordId": "recEXIST000001", "expectedOld": cloud_day, "newDate": manifest_day}
    entry.update(plan_over or {})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schemaVersion": sync.DATE_CORRECTION_SCHEMA, "listingSha256": "sha256:abc", "entries": [entry]}), encoding="utf-8")
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, DATE_FIELD: cloud_day}
    return v3, v9, water, plan, transport


def _datefix(tmp_path, **kw):
    v3, v9, water, plan, transport = _datefix_setup(tmp_path, **kw)
    report = sync.build_date_correction_specs(
        plan_path=plan, plan_sha=sha(plan), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
        target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport, out_dir=tmp_path / "specs", log_root=tmp_path / "logs")
    return report, transport


def test_date_correction_builds_a_guarded_update_from_the_cases_own_verified_manifest(tmp_path):
    report, transport = _datefix(tmp_path)
    assert report["status"] == "OK" and report["counts"] == {"entries": 1, "toCorrect": 1, "refused": 0}
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    update = spec["updates"][0]
    item = update["fields"][DATE_FIELD]
    assert item["new"] == "2026-06-17" and item["expectedOld"] == "2026-06-18" and set(update["fields"]) == {DATE_FIELD}
    assert spec["allowlist"][CASE]["fields"] == [DATE_FIELD] and spec["allowlist"][CASE]["records"] == ["recEXIST000001"]
    ref = item["basis"]["sourceRef"]
    assert "initialInspection.inspectionDate" in ref and "manifestSha256=sha256:" in ref and PN_FILL in ref and "sha256:abc" in ref
    assert not transport.creates                                                         # 生成入口只读云


@pytest.mark.parametrize("kw,why", [
    ({"cloud_day": "2026-06-20", "plan_over": {"expectedOld": "2026-06-18"}}, "不是计划旧值"),      # 云端已被改动(含人工)：保留
    ({"tamper": True}, "摘要"),                                                                   # 清单与总账摘要不符
    ({"state": "PENDING_LOCAL_VERIFY"}, "未完成本地核验归档"),
    ({"plan_over": {"newDate": "2026-06-16"}}, "不一致"),                                          # 计划新日不等于清单日
    ({"plan_over": {"recordId": "recOTHER0000001"}}, "未唯一命中"),
    ({"manifest_day": "2025-12-31", "cloud_day": "2026-01-02"}, "早于范围起点"),
])
def test_date_correction_refuses_an_entry_whose_evidence_or_old_value_does_not_hold(tmp_path, kw, why):
    report, _ = _datefix(tmp_path, **kw)
    assert report["status"] == "PARTIAL" and report["counts"]["toCorrect"] == 0 and not report["specs"]
    assert why in json.dumps(report["refused"], ensure_ascii=False)


def test_date_correction_plan_digest_mismatch_is_refused(tmp_path):
    v3, v9, water, plan, transport = _datefix_setup(tmp_path)
    with pytest.raises(sync.SyncError, match="摘要"):
        sync.build_date_correction_specs(plan_path=plan, plan_sha="0" * 64, waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
                                         target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                         out_dir=tmp_path / "specs", log_root=tmp_path / "logs")


# ---------------------------------------------------------------------------------------------
# 水上大队(不在八队名录)：adminOnly 路由——不写所属大队/归属大队，只写大队来源选项，与既有水上案同一做法
# ---------------------------------------------------------------------------------------------

SOURCE_TEAM_F, WATER_SRC, WATER_OPTION = "fld73J0dOH", "无锡市消防救援支队水上大队", "来源详情消防管辖=水上大队(不在8大队名录)"


def _water_contract(tmp_path: Path) -> Path:
    path = make_v3_contract(tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["baseContract"]["tables"]["案卷主表"]["fields"].append(
        {**_f(SOURCE_TEAM_F, "大队来源", "select", "source-sync-whitelist", "allowed"), "options": [{"name": n} for n in ("系统", "底册", WATER_OPTION)]})
    doc["extension"]["createFieldsCandidate"].append(_cand(CASE, SOURCE_TEAM_F, "大队来源", "select", "create-once-source-scalar", "optional-only-if-actual-source-value-observed", "omit unknown"))
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _water_case(key="32002210C202600002", **fields_over):
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-11T00:00:00Z"}
    fields = {KEY: {"new": key, "basis": basis}, UNIT: {"new": "合成水上单位", "basis": basis}, SOURCE_TEAM_F: {"new": WATER_OPTION, "basis": basis}}
    fields.update(fields_over)
    return {"tableId": CASE, "stableKey": {"fieldId": KEY, "value": key}, "routingSource": WATER_SRC, "fields": fields}


def _water_spec(tmp_path, contract, creates, mode="execute", log="w1"):
    routes = [{"sourceJurisdictionExact": SRC, "teamOptionExact": "梁溪大队", "teamTableId": TEAM_TABLE, "teamRecordId": TEAM_REC},
              {"sourceJurisdictionExact": WATER_SRC, "adminOnly": True, "sourceOptionExact": WATER_OPTION}]
    return v3_spec(tmp_path, contract, creates, mode=mode, log=log, routes=routes)


def test_admin_only_route_creates_a_water_case_without_any_team_fields_and_reads_it_back(tmp_path):
    contract, ledger, transport = _water_contract(tmp_path), tmp_path / "ledger", MemoryTransport()
    report = _run_v3(_water_spec(tmp_path, contract, [_water_case()]), transport, ledger, execute=True)
    assert report["status"] == "OK" and report["records"][0]["status"] == "CREATED_VERIFIED"
    (_, wire), = transport.creates
    assert SELECT_TEAM not in wire and LINK_TEAM not in wire and wire[SOURCE_TEAM_F] == [WATER_OPTION]
    again = _run_v3(_water_spec(tmp_path, contract, [_water_case()], log="w2"), transport, ledger, execute=True)
    assert again["records"][0]["status"] == "ALREADY_PRESENT_VERIFIED" and len(transport.creates) == 1


@pytest.mark.parametrize("over,message", [
    ({SELECT_TEAM: {"new": "梁溪大队", "basis": {"kind": "source-observed", "valueState": "observed", "sourceRef": "f", "observedAt": "t"}}}, "不得写所属大队"),
    ({SOURCE_TEAM_F: {"new": "系统", "basis": {"kind": "source-observed", "valueState": "observed", "sourceRef": "f", "observedAt": "t"}}}, "冻结的大队来源选项"),
])
def test_admin_only_route_refuses_team_fields_or_a_different_source_option(tmp_path, over, message):
    contract, transport = _water_contract(tmp_path), MemoryTransport()
    with pytest.raises(sync.SyncError, match=message):
        _run_v3(_water_spec(tmp_path, contract, [_water_case(**over)]), transport, tmp_path / "ledger", execute=True)
    assert not transport.creates


def test_admin_only_route_does_not_relax_the_team_routes(tmp_path):
    contract, transport = _water_contract(tmp_path), MemoryTransport()
    basis = {"kind": "source-observed", "valueState": "observed", "sourceRef": "fixture", "observedAt": "2026-11-11T00:00:00Z"}
    broken = _wire_case()
    del broken["fields"][LINK_TEAM]
    with pytest.raises(sync.SyncError, match="同时给出所属大队与归属大队"):
        _run_v3(_water_spec(tmp_path, contract, [broken], log="w3"), transport, tmp_path / "ledger", execute=True)
    assert not transport.creates


@pytest.mark.parametrize("source_class", ["source-sync-whitelist", "source-unconfirmed-readonly"])
def test_build_routes_a_water_source_case_through_the_admin_only_route_and_leaves_team_cases_alone(tmp_path, source_class):
    v3, v9 = make_basic_contracts(tmp_path)
    for path in (v3, v9):
        doc = json.loads(path.read_text(encoding="utf-8"))
        base = doc["baseContract"] if "baseContract" in doc else doc
        base["tables"]["案卷主表"]["fields"].append({**_f(SOURCE_TEAM_F, "大队来源", "select", source_class, "allowed"), "options": [{"name": WATER_OPTION}, {"name": "系统"}]})
        if "extension" in doc:
            doc["extension"]["createFieldsCandidate"].append(_cand(CASE, SOURCE_TEAM_F, "大队来源", "select", "create-once-source-scalar", "optional-only-if-actual-source-value-observed", "omit unknown"))
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    records = {"2001": _detail("32002210C202600002", "2001", jurisdiction=WATER_SRC), "2002": _detail(PN_NEW, "2002")}
    capture, water, routes = make_basic_inputs(tmp_path, records)
    doc = json.loads(routes.read_text(encoding="utf-8"))
    doc["routes"].append({"sourceJurisdictionExact": WATER_SRC, "adminOnly": True, "sourceOptionExact": WATER_OPTION})
    routes.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    report = sync.build_basic_specs(
        capture_path=capture, capture_sha=sha(capture), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
        routes_path=routes, routes_sha=sha(routes), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
        transport=BuildTransport(), out_dir=tmp_path / "specs", log_root=tmp_path / "logs", ledger_dir=tmp_path / "build-ledger")
    assert report["status"] == "OK" and report["counts"]["toCreate"] == 2
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    by_key = {c["stableKey"]["value"]: c["fields"] for c in spec["creates"]}
    assert SELECT_TEAM not in by_key["32002210C202600002"] and LINK_TEAM not in by_key["32002210C202600002"]
    if source_class == "source-sync-whitelist":
        assert by_key["32002210C202600002"][SOURCE_TEAM_F]["new"] == WATER_OPTION
    else:   # 合同把大队来源列为只读：留空，不为此改合同
        assert SOURCE_TEAM_F not in by_key["32002210C202600002"]
    assert by_key[PN_NEW][SELECT_TEAM]["new"] == "梁溪大队" and SOURCE_TEAM_F not in by_key[PN_NEW]
    assert {r["sourceJurisdictionExact"] for r in spec["routes"]} == {SRC, WATER_SRC}


def test_date_correction_can_move_the_date_source_with_its_own_old_value_guard(tmp_path):
    v3, v9, water, plan, transport = _datefix_setup(tmp_path)
    for path in (v3, v9):
        doc = json.loads(path.read_text(encoding="utf-8"))
        base = doc["baseContract"] if "baseContract" in doc else doc
        base["tables"]["案卷主表"]["fields"].append({**_f(DATE_SRC_F, "初查日期来源", "select", "source-sync-whitelist", "allowed(v11)"), "options": [{"name": n} for n in ("底册", "监督系统")]})
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    transport.rows[(CASE, "recEXIST000001")][DATE_SRC_F] = ["底册"]
    body = json.loads(plan.read_text(encoding="utf-8"))
    body["entries"][0].update({"sourceOld": "底册", "sourceNew": "监督系统"})
    plan.write_text(json.dumps(body), encoding="utf-8")
    args = dict(plan_path=plan, plan_sha=sha(plan), waterline_path=water, v3_path=v3, v3_sha=sha(v3), v9_path=v9, v9_sha=sha(v9),
                target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport, out_dir=tmp_path / "specs", log_root=tmp_path / "logs")
    report = sync.build_date_correction_specs(**args)
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    fields = spec["updates"][0]["fields"]
    assert fields[DATE_SRC_F]["new"] == "监督系统" and fields[DATE_SRC_F]["expectedOld"] == "底册" and sorted(spec["allowlist"][CASE]["fields"]) == sorted([DATE_FIELD, DATE_SRC_F])
    transport.rows[(CASE, "recEXIST000001")][DATE_SRC_F] = ["登记系统"]                    # 来源已被改动：整条拒绝
    refused = sync.build_date_correction_specs(**{**args, "out_dir": tmp_path / "specs2"})
    assert refused["counts"]["toCorrect"] == 0 and "初查日期来源当前值" in json.dumps(refused["refused"], ensure_ascii=False)


def test_build_prefers_the_verified_manifest_initial_day_over_the_acceptance_fallback_and_keeps_the_difference(tmp_path):
    manifest_dir = tmp_path / "工作区" / "历史工作区" / f"{PN_NEW}-G1"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps({"case": {"projectNo": PN_NEW}, "initialInspection": {"inspectionDate": "2026-11-02"}}), encoding="utf-8")
    water = {PN_NEW: {"state": "COMPLETED", "local": {"status": "ARCHIVED"},
                      "history": {"completions": [{"generation": "G1", "manifestSha256": "sha256:" + sha(manifest), "verifiedAt": "2026-11-05T10:00:00+08:00"}]}}}
    report, _, _ = run_build(tmp_path, {"1001": _detail_with_acceptance(PN_NEW, "1001")}, waterline=water)
    field = _first_create(report)[DATE_FIELD]
    assert field["new"] == "2026-11-02" and "initialInspection.inspectionDate" in field["basis"]["sourceRef"] and "manifestSha256" in field["basis"]["sourceRef"]
    assert report["initialDateAcceptanceFallbacks"] == [] and report["initialDateConflicts"][0]["electronic"] == "2026-11-02"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("2026-11-02", "2026-11-03"), encoding="utf-8")      # 清单被改：摘要不符，退回受理日期后备
    again, _, _ = run_build(tmp_path, {"1001": _detail_with_acceptance(PN_NEW, "1001")}, waterline=water)
    assert _first_create(again)[DATE_FIELD]["new"] == "2026-11-04"


# ------------------------------------------------------------------ strict technical recovery of an UNKNOWN update (no human release)


def _later(monkeypatch, seconds=7200):
    real = sync._now_epoch
    monkeypatch.setattr(sync, "_now_epoch", lambda: real() + seconds)


def _hist(created_before_intent=True, field_ids=(), at=None):
    base = sync.now()
    epoch = sync._iso_epoch(base)
    items = [{"rev": 10, "create_time": epoch - 86400, "operator": "u", "field_changes": [{"field_id": HANDLER}]}]
    if not created_before_intent:
        items.insert(0, {"rev": 11, "create_time": at if at is not None else epoch + 1, "operator": "u", "field_changes": [{"field_id": f} for f in field_ids]})
    return items


def recovery_spec(tmp_path, name="recover", intent_id=None, quiet=7200, **extra):
    intent = intent_id or ledger_events(tmp_path)[0]["intentId"]
    return reconcile_spec(tmp_path, name, technicalRecovery={"kind": "history-quiet-readback", "intentId": intent, "minQuietSeconds": quiet}, **extra)


def test_technical_recovery_clears_an_unknown_update_only_with_strict_evidence_and_allows_the_normal_write(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    recon = service(meta, history=_hist())
    report = run(tmp_path, recovery_spec(tmp_path), recon, flag="reconcile", name="r.json")
    target = report["targets"][0]
    assert target["outcome"] == "NOT_APPLIED_PROVEN" and recon.upserts == [] and report["externalWrites"] == 0
    assert target["recovery"]["revisionsAtOrAfterIntent"] == [] and target["recovery"]["quietSeconds"] >= 7200
    events = ledger_events(tmp_path)
    assert [e["event"] for e in events] == ["intent", "final", "reconciled"] and events[1]["status"] == "UNKNOWN"   # 原 UNKNOWN 保留
    assert not any(e["event"] == "human_release" for e in events)
    again = service(meta)
    result = run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-after")), again, name="again.json")
    assert result["records"][0]["status"] == "WRITTEN" and len(again.upserts) == 1


def test_technical_recovery_is_refused_inside_the_quiet_window(tmp_path, meta):
    unknown_state(tmp_path, meta)
    recon = service(meta, history=_hist())
    report = run(tmp_path, recovery_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "RECOVERY_REFUSED" and "静默期" in " ".join(report["targets"][0]["recovery"]["reasons"])
    blocked = service(meta)
    with pytest.raises(sync.SyncError, match="RECOVERY_REFUSED"):
        run(tmp_path, make_spec(tmp_path, logDir=str(tmp_path / "log-retry")), blocked, name="retry.json")
    assert blocked.calls == []


def test_technical_recovery_is_refused_when_history_shows_a_later_revision_on_the_field(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    recon = service(meta, history=_hist(created_before_intent=False, field_ids=[UNIT]))
    report = run(tmp_path, recovery_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "RECOVERY_REFUSED"
    assert ledger_events(tmp_path)[-1]["outcome"] == "RECOVERY_REFUSED"


def test_technical_recovery_is_refused_when_a_later_revision_has_unknown_fields_or_history_is_empty(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    assert run(tmp_path, recovery_spec(tmp_path, "a"), service(meta, history=_hist(created_before_intent=False, field_ids=[])), flag="reconcile",
               name="a.json")["targets"][0]["outcome"] == "RECOVERY_REFUSED"
    assert run(tmp_path, recovery_spec(tmp_path, "b"), service(meta, history=[]), flag="reconcile", name="b.json")["targets"][0]["outcome"] == "RECOVERY_REFUSED"


def test_technical_recovery_tolerates_a_later_revision_of_unrelated_fields(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    recon = service(meta, history=_hist(created_before_intent=False, field_ids=[HANDLER]))
    assert run(tmp_path, recovery_spec(tmp_path), recon, flag="reconcile", name="r.json")["targets"][0]["outcome"] == "NOT_APPLIED_PROVEN"


def test_technical_recovery_is_refused_when_the_live_value_is_not_the_old_value(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    recon = service(meta, history=_hist())
    recon.rows[(CASE, REC)][UNIT] = "新单位"
    report = run(tmp_path, recovery_spec(tmp_path), recon, flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "RECOVERY_REFUSED" and report["targets"][0]["recovery"]["readbackOutcome"] == "DIVERGED"


def test_technical_recovery_needs_an_unknown_final_not_a_pending_intent(tmp_path, meta, monkeypatch):
    ledger = sync.Ledger(tmp_path / "ledger")
    ledger.initialize()
    ledger.append(sync.Ledger.target_key(BASE, CASE, REC), {"event": "intent", "intentId": "i1", "stableKey": {"fieldId": KEY, "value": STABLE},
                                                            "fields": {UNIT: {"name": "u", "old": "旧单位", "type": "text", "wire": "新单位"}}})
    _later(monkeypatch)
    report = run(tmp_path, recovery_spec(tmp_path, intent_id="i1"), service(meta, history=_hist()), flag="reconcile", name="p.json")
    assert report["targets"][0]["outcome"] == "RECOVERY_REFUSED"


def test_technical_recovery_applies_only_to_its_exact_intent_and_never_with_a_human_release(tmp_path, meta, monkeypatch):
    unknown_state(tmp_path, meta)
    _later(monkeypatch)
    report = run(tmp_path, recovery_spec(tmp_path, intent_id="0" * 64), service(meta, history=_hist()), flag="reconcile", name="r.json")
    assert report["targets"][0]["outcome"] == "RECOVERY_NOT_FOR_THIS_INTENT" and ledger_events(tmp_path)[-1]["event"] == "final"
    both = recovery_spec(tmp_path, "both", humanRelease={"reason": "r", "operator": "o", "intentId": "i", "acknowledgesLateWriteRisk": True, "evidence": [{"kind": "x", "ref": "y"}]})
    with pytest.raises(sync.SyncError):
        run(tmp_path, both, service(meta, history=_hist()), flag="reconcile", name="both.json")


@pytest.mark.parametrize("quiet", [0, 60, 3599])
def test_technical_recovery_rejects_a_short_quiet_window_in_the_spec(tmp_path, meta, quiet):
    unknown_state(tmp_path, meta)
    with pytest.raises(sync.SyncError, match="minQuietSeconds"):
        run(tmp_path, recovery_spec(tmp_path, quiet=quiet), service(meta, history=_hist()), flag="reconcile", name="q.json")


# ------------------------------------------------------------------ create reconcile: causal proof from the service's own revision history


def _creation_history(offset=0.5):
    epoch = sync._iso_epoch(sync.now())
    return lambda rid: [{"rev": 5, "createTime": epoch + offset, "operator": "u", "fieldIds": []}]


def _history_proof(ledger, key):
    return [{"kind": "record-history-creation", "intentId": sync.Ledger(ledger).state(key)["lastIntent"]["intentId"]}]


def test_v3_history_creation_proof_clears_a_create_without_a_receipt_log(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, timeout_late=True)
    transport.b["history"] = _creation_history()
    target = _reconcile(tmp_path, contract, ledger, transport, "h1", requestTerminalProof=_history_proof(ledger, key))
    assert target["outcome"] == "APPLIED" and target["observed"]["verifiedProof"][0]["kind"] == "record-history-creation"
    assert sync.Ledger(ledger).state(key)["state"] == "CLEAR" and len(transport.creates) == 1
    events = sync.Ledger(ledger).events(key)
    assert [e["event"] for e in events][-1] == "reconciled" and events[-1]["terminalProof"][0]["kind"] == "record-history-creation"


def test_v3_history_creation_proof_refuses_outside_the_window_or_without_exact_intent_or_history(tmp_path):
    contract, ledger, transport, key = _late_create(tmp_path, timeout_late=True)
    transport.b["history"] = _creation_history(offset=-3600)                         # 该行早于原请求就存在
    assert _reconcile(tmp_path, contract, ledger, transport, "h-early", requestTerminalProof=_history_proof(ledger, key))["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    transport.b["history"] = _creation_history(offset=3600)                          # 远晚于原请求
    assert _reconcile(tmp_path, contract, ledger, transport, "h-late", requestTerminalProof=_history_proof(ledger, key))["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    transport.b["history"] = _creation_history()
    wrong = [{"kind": "record-history-creation", "intentId": "f" * 64}]
    assert _reconcile(tmp_path, contract, ledger, transport, "h-intent", requestTerminalProof=wrong)["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    assert _reconcile(tmp_path, contract, ledger, transport, "h-none")["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    del transport.b["history"]
    assert _reconcile(tmp_path, contract, ledger, transport, "h-unreadable", requestTerminalProof=_history_proof(ledger, key))["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    transport.b["history"] = lambda rid: []
    assert _reconcile(tmp_path, contract, ledger, transport, "h-empty", requestTerminalProof=_history_proof(ledger, key))["outcome"] == "ONE_MATCH_NEEDS_TERMINAL_PROOF"
    assert sync.Ledger(ledger).state(key)["state"] == "BLOCKED"


# ------------------------------------------------------------------ evidence-checked field plan (自动来源字段有据维护)


def _fieldplan_setup(tmp_path, *, cloud=("登记系统",), new="监督系统", old="登记系统", tamper=False, field=DATE_SRC_F, plan_over=None):
    v3, v9 = make_basic_contracts(tmp_path)
    _add_lifecycle_fields(v3, v9)
    evidence = tmp_path / "证据" / "manifest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({"initialInspection": {"inspectionDate": "2026-06-17"}}), encoding="utf-8")
    digest_ = sha(evidence)
    if tamper:
        evidence.write_text("{}", encoding="utf-8")
    water = tmp_path / "waterline.json"
    water.write_text(json.dumps({"cases": {}}), encoding="utf-8")
    entry = {"table": "案卷主表", "projectNo": PN_FILL, "recordId": "recEXIST000001", "stableKey": {"fieldId": KEY, "value": PN_FILL},
             "fields": {field: {"new": new, "expectedOld": old, "basisRef": "本案已核验清单初查日", "observedAt": "2026-11-12T00:00:00+08:00",
                                "evidence": [{"path": "证据/manifest.json", "sha256": digest_}]}}}
    entry.update(plan_over or {})
    plan = tmp_path / "fieldplan.json"
    plan.write_text(json.dumps({"schemaVersion": sync.FIELD_PLAN_SCHEMA, "entries": [entry]}), encoding="utf-8")
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: PN_FILL, field: list(cloud) if isinstance(cloud, tuple) else cloud}
    return v3, v9, water, plan, transport


def _fieldplan(tmp_path, **kw):
    v3, v9, water, plan, transport = _fieldplan_setup(tmp_path, **kw)
    report = sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9),
                                         target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                         out_dir=tmp_path / "specs", log_root=tmp_path / "logs")
    return report, transport


def test_field_plan_builds_a_guarded_update_with_digest_checked_evidence(tmp_path):
    report, transport = _fieldplan(tmp_path)
    assert report["status"] == "OK" and report["counts"] == {"entries": 1, "toWrite": 1, "alreadyEqual": 0, "refused": 0}
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    item = spec["updates"][0]["fields"][DATE_SRC_F]
    assert item["new"] == "监督系统" and item["expectedOld"] == "登记系统" and "@sha256=" in item["basis"]["sourceRef"] and "manifest.json" in item["basis"]["sourceRef"]
    assert spec["allowlist"][CASE] == {"records": ["recEXIST000001"], "fields": [DATE_SRC_F]} and not transport.creates


def test_field_plan_fill_of_an_empty_cell_and_already_equal_cells(tmp_path):
    report, _ = _fieldplan(tmp_path, cloud=None, old=None)
    assert report["status"] == "OK" and report["counts"]["toWrite"] == 1
    again, _ = _fieldplan(tmp_path / "again", cloud=("监督系统",), old="登记系统") if (tmp_path / "again").mkdir() is None else (None, None)
    assert again["status"] == "OK" and again["counts"] == {"entries": 1, "toWrite": 0, "alreadyEqual": 1, "refused": 0} and not again["specs"]


@pytest.mark.parametrize("kw,why", [
    ({"tamper": True}, "摘要"),                                              # 证据文件被改
    ({"cloud": ("底册",)}, "不是计划旧值"),                                   # 云端已变(含人工)
    ({"cloud": None}, "不是计划旧值"),                                         # 计划说有旧值，云端其实是空
    ({"new": "不存在的选项"}, "选项不在合同"),
    ({"field": "fld0000000X"}, "合同不允许"),                                 # 合同外字段默认拒写
    ({"plan_over": {"recordId": "recOTHER0000001"}}, "没有该记录"),
])
def test_field_plan_refuses_what_the_contract_or_evidence_does_not_back(tmp_path, kw, why):
    report, _ = _fieldplan(tmp_path, **kw)
    assert report["status"] == "PARTIAL" and report["counts"]["toWrite"] == 0 and not report["specs"]
    assert why in json.dumps(report["refused"], ensure_ascii=False)


def test_field_plan_refuses_a_human_field_and_a_changed_plan(tmp_path):
    report, _ = _fieldplan(tmp_path, field="fldvHLpSKI")
    assert report["status"] == "PARTIAL" and "合同不允许" in json.dumps(report["refused"], ensure_ascii=False)
    v3, v9, water, plan, transport = _fieldplan_setup(tmp_path / "x") if (tmp_path / "x").mkdir() is None else (None,) * 5
    with pytest.raises(sync.SyncError, match="摘要"):
        sync.build_field_plan_specs(plan_path=plan, plan_sha="0" * 64, waterline_path=water, contract_path=v9, contract_sha=sha(v9),
                                    target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                    out_dir=tmp_path / "specs", log_root=tmp_path / "logs")


def test_maintenance_builders_refuse_a_target_with_an_unaccepted_human_release_history(tmp_path):
    v3, v9, water, plan, transport = _fieldplan_setup(tmp_path)
    ledger_dir = tmp_path / "ledger"
    ledger = sync.Ledger(ledger_dir, isolated_test=True)
    ledger.initialize()
    ledger.append(sync.Ledger.target_key(BASE, CASE, "create:" + PN_FILL), {"event": "human_release", "intentId": "i-old", "operator": "助手(历史)", "reason": "r"})
    target = {"baseToken": BASE, "profile": "offline-fake", "identity": "user"}
    report = sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9), target=target,
                                         transport=transport, out_dir=tmp_path / "specs", log_root=tmp_path / "logs", ledger_dir=ledger_dir, isolated_test_ledger=True)
    assert report["status"] == "PARTIAL" and not report["specs"] and "human_release" in json.dumps(report["refused"], ensure_ascii=False)
    v3d, v9d, waterd, pland, transportd = _datefix_setup(tmp_path / "d") if (tmp_path / "d").mkdir() is None else (None,) * 5
    refused = sync.build_date_correction_specs(plan_path=pland, plan_sha=sha(pland), waterline_path=waterd, v3_path=v3d, v3_sha=sha(v3d), v9_path=v9d, v9_sha=sha(v9d),
                                               target=target, transport=transportd, out_dir=tmp_path / "specs2", log_root=tmp_path / "logs2",
                                               ledger_dir=ledger_dir, isolated_test_ledger=True)
    assert refused["status"] == "PARTIAL" and not refused["specs"] and "human_release" in json.dumps(refused["refused"], ensure_ascii=False)
    ok = sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9), target=target, transport=transport,
                                     out_dir=tmp_path / "specs3", log_root=tmp_path / "logs3", ledger_dir=None)
    assert ok["status"] == "OK" and ok["counts"]["toWrite"] == 1


# ------------------------------------------------------------------ 精确逆操作(restore)与写前人工放行闸门


def _mis_write(tmp_path, meta):
    """早先在一个原本为空的字段写入了误写值：先造空字段，再按正常路径写入，得到该意图的 OK 终态。"""
    svc = service(meta)
    svc.rows[(CASE, REC)][RESULT] = None
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {RESULT: {"new": "不合格", "expectedOld": None, "basis": basis()}}
    run(tmp_path, spec, svc)
    return svc, [e for e in ledger_events(tmp_path) if e["event"] == "intent"][0]["intentId"]


def _restore_spec(tmp_path, intent_id, old="不合格", **override):
    spec = make_spec(tmp_path, logDir=str(tmp_path / "log-restore"))
    spec["updates"][0]["fields"] = {RESULT: {"new": None, "expectedOld": old, "restore": {"intentId": intent_id}, "basis": basis("恢复误写")}}
    spec.update(override)
    return spec


def _release_create_target(tmp_path):
    ledger = sync.Ledger(tmp_path / "ledger")
    ledger.append(sync.Ledger.target_key(BASE, CASE, "create:" + STABLE), {"event": "human_release", "intentId": "1" * 64, "reason": "历史放行"})


def test_restore_clears_only_the_mis_written_field_and_keeps_history(tmp_path, meta):
    svc, intent_id = _mis_write(tmp_path, meta)
    _release_create_target(tmp_path)   # 该案历史上有未获用户接受的人工放行，业务赋值被拒绝，但精确逆操作可恢复
    with pytest.raises(sync.SyncError, match="HUMAN_RELEASE_UNACCEPTED"):
        spec = make_spec(tmp_path, logDir=str(tmp_path / "log-biz"))
        spec["updates"][0]["fields"] = {HANDLER: {"new": "新承办人", "expectedOld": "旧承办人", "basis": basis()}}
        run(tmp_path, spec, svc)
    before_events = len(ledger_events(tmp_path))
    other = {k: v for k, v in svc.rows[(CASE, REC)].items() if k != RESULT}
    report = run(tmp_path, _restore_spec(tmp_path, intent_id), svc, name="restore.json")
    assert report["status"] == "OK"
    assert svc.rows[(CASE, REC)][RESULT] is None and {k: v for k, v in svc.rows[(CASE, REC)].items() if k != RESULT} == other
    assert svc.upserts[-1][2] == {RESULT: None}
    events = ledger_events(tmp_path)
    assert len(events) == before_events + 2 and events[0]["event"] == "intent" and events[-2]["event"] == "intent" and events[-1]["status"] == "OK"


@pytest.mark.parametrize("old,match", [("合格", "不等于被恢复的误写值|旧值与 expectedOld 不符|不是对该误写的逆操作")])
def test_restore_refuses_when_the_value_is_not_the_mis_written_one(tmp_path, meta, old, match):
    svc, intent_id = _mis_write(tmp_path, meta)
    svc.rows[(CASE, REC)][RESULT] = ["合格"]   # 他人此后改过
    with pytest.raises(sync.SyncError, match="RESTORE_NOT_PROVEN|expectedOld"):
        run(tmp_path, _restore_spec(tmp_path, intent_id, old="合格"), svc, name="restore.json")


def test_restore_refuses_unknown_intent_changed_value_and_later_writers(tmp_path, meta):
    svc, intent_id = _mis_write(tmp_path, meta)
    calls = len(svc.calls)
    with pytest.raises(sync.SyncError, match="RESTORE_NOT_PROVEN"):
        run(tmp_path, _restore_spec(tmp_path, "0" * 64), svc, name="r1.json")
    spec = _restore_spec(tmp_path, intent_id, logDir=str(tmp_path / "log-r2"))
    spec["updates"][0]["fields"][RESULT]["expectedOld"] = "合格"
    with pytest.raises(sync.SyncError, match="RESTORE_NOT_PROVEN"):
        run(tmp_path, spec, svc, name="r2.json")
    later = make_spec(tmp_path, logDir=str(tmp_path / "log-later"))
    later["updates"][0]["fields"] = {HANDLER: {"new": "后续写者", "expectedOld": "旧承办人", "basis": basis()}}
    run(tmp_path, later, svc, name="later.json")
    with pytest.raises(sync.SyncError, match="RESTORE_NOT_PROVEN"):
        run(tmp_path, _restore_spec(tmp_path, intent_id, logDir=str(tmp_path / "log-r3")), svc, name="r3.json")
    assert all(call[2] != "+record-upsert" or len(svc.upserts) == 2 for call in svc.calls[calls:])


def test_restore_requires_the_original_old_value_to_have_been_empty(tmp_path, meta):
    svc = service(meta)   # RESULT 原值 ["合格"]：覆盖写入不是“空值被误写”，不能当逆操作
    spec = make_spec(tmp_path)
    spec["updates"][0]["fields"] = {RESULT: {"new": "不合格", "expectedOld": "合格", "basis": basis()}}
    run(tmp_path, spec, svc)
    intent_id = [e for e in ledger_events(tmp_path) if e["event"] == "intent"][0]["intentId"]
    with pytest.raises(sync.SyncError, match="RESTORE_NOT_PROVEN"):
        run(tmp_path, _restore_spec(tmp_path, intent_id), svc, name="restore.json")
    assert svc.rows[(CASE, REC)][RESULT] == ["不合格"]


def _restore_plan_setup(tmp_path, cloud=("监督系统",)):
    v3, v9, water, plan, transport = _fieldplan_setup(tmp_path, cloud=cloud, new=None, old="监督系统",
                                                      plan_over=None)
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["entries"][0]["fields"][DATE_SRC_F]["restore"] = {"intentId": "i-mis"}
    plan.write_text(json.dumps(data), encoding="utf-8")
    return v3, v9, water, plan, transport


def _build_restore(tmp_path, ledger_dir, v9, water, plan, transport):
    return sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9),
                                       target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                       out_dir=tmp_path / "specs", log_root=tmp_path / "logs", ledger_dir=ledger_dir, isolated_test_ledger=True)


def test_field_plan_restore_entry_is_built_even_for_a_target_with_an_unaccepted_release_but_only_with_a_ledger(tmp_path):
    v3, v9, water, plan, transport = _restore_plan_setup(tmp_path)
    ledger_dir = tmp_path / "ledger"
    ledger = sync.Ledger(ledger_dir, isolated_test=True)
    ledger.initialize()
    ledger.append(sync.Ledger.target_key(BASE, CASE, "create:" + PN_FILL), {"event": "human_release", "intentId": "i-old", "operator": "助手(历史)", "reason": "r"})
    report = _build_restore(tmp_path, ledger_dir, v9, water, plan, transport)
    assert report["status"] == "OK" and report["counts"]["toWrite"] == 1
    item = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))["updates"][0]["fields"][DATE_SRC_F]
    assert item["new"] is None and item["expectedOld"] == "监督系统" and item["restore"] == {"intentId": "i-mis"}
    no_ledger = sync.build_field_plan_specs(plan_path=plan, plan_sha=sha(plan), waterline_path=water, contract_path=v9, contract_sha=sha(v9),
                                            target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, transport=transport,
                                            out_dir=tmp_path / "specs2", log_root=tmp_path / "logs2")
    assert no_ledger["status"] == "PARTIAL" and "--ledger-dir" in json.dumps(no_ledger["refused"], ensure_ascii=False)


def test_field_plan_restore_entry_refuses_a_value_changed_by_someone_else_and_skips_an_already_empty_cell(tmp_path):
    v3, v9, water, plan, transport = _restore_plan_setup(tmp_path, cloud=("人工改的值",))
    ledger_dir = tmp_path / "ledger"
    sync.Ledger(ledger_dir, isolated_test=True).initialize()
    changed = _build_restore(tmp_path, ledger_dir, v9, water, plan, transport)
    assert changed["status"] == "PARTIAL" and "不等于被恢复的误写值" in json.dumps(changed["refused"], ensure_ascii=False) and not changed["specs"]
    sub = tmp_path / "empty"
    sub.mkdir()
    v3, v9, water, plan, transport = _restore_plan_setup(sub, cloud=None)
    empty = _build_restore(sub, ledger_dir, v9, water, plan, transport)
    assert empty["status"] == "OK" and empty["counts"]["toWrite"] == 0 and not empty["specs"]


# ------------------------------------------------------------------ 子行(检查明细/产品明细)创建说明

CP = "32002299C202600005"


def make_child_contract(tmp_path: Path) -> Path:
    inspect_f, product_f = sync.INSPECT_F, sync.PRODUCT_F
    teams = [{"name": n} for n in sync.BRIGADE_NAMES.values()]

    def sel(*names):
        return [{"name": n} for n in names]

    case_fields = [_f(KEY, "项目编号", "text", "source-sync-whitelist", "NEVER (stable key)"), {**_f("fld2Kdm4i2", "所属大队", "select", "admin-only"), "options": teams},
                   {**_f(DATE, "初查日期", "datetime", "source-sync-whitelist", "allowed")}, {**_f(RESULT_F, "初查结果", "select", "source-sync-whitelist", "allowed"), "options": sel("合格", "不合格", "待核实")}]
    sw = "create-once(v14)"
    inspect_fields = [
        _f(inspect_f["key"], "检查键", "text", "source-sync-whitelist", "NEVER (stable key)"), _f(inspect_f["case"], "项目编号", "link", "structural-relation"),
        {**_f(inspect_f["stage"], "阶段", "select", "source-sync-whitelist", sw), "options": sel("初查", "复查")}, _f(inspect_f["date"], "检查日期", "datetime", "source-sync-whitelist", sw),
        _f(inspect_f["month"], "统计月份", "text", "source-sync-whitelist", sw), {**_f(inspect_f["team"], "大队", "select", "source-sync-whitelist", sw), "options": teams},
        {**_f(inspect_f["result"], "检查结果", "select", "source-sync-whitelist", sw), "options": sel("合格", "不合格", "待核实")},
        _f(inspect_f["count"], "产品数", "number", "source-sync-whitelist", sw), _f(inspect_f["bad"], "不合格产品数", "number", "source-sync-whitelist", sw),
        {**_f(inspect_f["method"], "检查方式", "select", "source-sync-whitelist", sw), "options": sel("抽样送检", "现场判定", "抽样送检、现场判定")},
        {**_f(inspect_f["source"], "数据来源", "select", "source-sync-whitelist", sw), "options": sel("来源详情")},
        {**_f(inspect_f["date_source"], "日期来源", "select", "source-sync-whitelist", sw), "options": sel("监督系统", "来源复查记录PDF")},
        _f(inspect_f["round"], "检查序次", "number", "source-sync-whitelist", sw),
        {**_f(inspect_f["in_scope"], "计入2026统计", "select", "source-sync-whitelist", sw), "options": sel("是", "否", "待核实")}]
    product_fields = [
        _f(product_f["key"], "产品键", "text", "source-sync-whitelist", "NEVER (stable key)"), _f(product_f["inspection"], "检查键", "link", "structural-relation"),
        _f(product_f["seq"], "序号", "number", "source-sync-whitelist", sw), _f(product_f["name"], "产品名称", "text", "source-sync-whitelist", sw),
        _f(product_f["model"], "规格型号", "text", "source-sync-whitelist", sw), _f(product_f["producer"], "标称生产者", "text", "source-sync-whitelist", sw),
        _f(product_f["location"], "所在部位", "text", "source-sync-whitelist", sw),
        {**_f(product_f["method"], "检查方式", "select", "source-sync-whitelist", sw), "options": sel("抽样送检", "现场判定")},
        {**_f(product_f["result"], "检查结果", "select", "source-sync-whitelist", sw), "options": sel("合格", "不合格", "待核实")},
        {**_f(product_f["access"], "市场准入", "select", "source-sync-whitelist", sw), "options": sel("CCC认证")},
        _f(product_f["problem"], "问题描述", "text", "source-sync-whitelist", sw),
        {**_f(product_f["source"], "数据来源", "select", "source-sync-whitelist", sw), "options": sel("来源详情")}, _f(product_f["cert"], "证书编号/核查原文", "text", "source-sync-whitelist", sw)]
    base = {"baseId": BASE, "version": "synthetic-v14", "tables": {"案卷主表": {"tableId": CASE, "fields": case_fields}, "检查明细": {"tableId": CHECK, "fields": inspect_fields},
                                                                    "产品明细": {"tableId": PRODUCT, "fields": product_fields}}}
    kinds = {"text": "create-once-source-scalar"}
    cands = []
    for table, fields in ((CHECK, inspect_fields), (PRODUCT, product_fields)):
        for f in fields:
            klass = "create-once-parent-link" if f["type"] == "link" else ("create-once-identity" if f["fieldId"] in (inspect_f["key"], product_f["key"]) else kinds["text"])
            cands.append(_cand(table, f["fieldId"], f["name"], f["type"], klass, "candidate-ready-v14",
                               "required; block" if klass != "create-once-source-scalar" else "omit unknown/missing/unread/unconfirmed"))
    path = tmp_path / "child-contract.json"
    path.write_text(json.dumps({"baseContract": base, "extension": {"versionCandidate": "synthetic", "createFieldsCandidate": cands, "explicitCreatePayloadDeny": []}}, ensure_ascii=False), encoding="utf-8")
    return path


def _child_setup(tmp_path, *, initial=None, recheck=None, brigade="LIANGXI", cloud_team="梁溪大队", cloud_day="2026-06-17", state="COMPLETED", tamper=False, classification=None):
    contract = make_child_contract(tmp_path)
    products = initial or [{"clientRef": "p1", "name": "手提式干粉灭火器", "method": "ONSITE", "result": "QUALIFIED", "modelSpec": "MFZ/ABC4", "nominalProducer": "某厂", "location": "大厅"},
                           {"clientRef": "p2", "name": "洒水喷头", "method": "SAMPLING", "result": "UNQUALIFIED", "problemDescription": "标志不符合"}]
    manifest_doc = {"case": {"projectNo": CP, "brigadeCode": brigade}, "initialInspection": {"clientRef": "initial", "stage": "INITIAL_CHECK", "inspectionDate": "2026-06-17", "products": products}}
    if recheck is not None:
        manifest_doc["recheckInspection"] = recheck
    manifest_dir = tmp_path / "工作区" / "历史工作区" / f"{CP}-G1"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps(manifest_doc, ensure_ascii=False), encoding="utf-8")
    recorded = "sha256:" + sha(manifest)
    if tamper:
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("2026-06-17", "2026-06-30"), encoding="utf-8")
    case = {"state": state, "local": {"status": "ARCHIVED"}, "history": {"completions": [{"generation": "G1", "manifestSha256": recorded, "verifiedAt": "2026-11-01T10:00:00+08:00"}]}}
    if classification:
        case["source"] = {"classification": classification}
    water = tmp_path / "waterline.json"
    water.write_text(json.dumps({"cases": {CP: case}}, ensure_ascii=False), encoding="utf-8")
    transport = BuildTransport()
    transport.rows[(CASE, "recEXIST000001")] = {KEY: CP, "fld2Kdm4i2": [cloud_team] if cloud_team else None, DATE: f"{cloud_day}T00:00:00.000+08:00", RESULT_F: ["不合格"]}
    return contract, water, transport


def _child_build(tmp_path, contract, water, transport, **kw):
    ledger = kw.pop("ledger_dir", None) or tmp_path / "child-ledger"
    sync.Ledger(ledger, isolated_test=True).initialize() if not (ledger / "targets").is_dir() else None
    return sync.build_child_specs(waterline_path=water, v3_path=contract, v3_sha=sha(contract), target={"baseToken": BASE, "profile": "offline-fake", "identity": "user"},
                                  transport=transport, out_dir=tmp_path / "specs", log_root=tmp_path / "logs", ledger_dir=ledger, isolated_test_ledger=True, **kw)


def test_child_build_makes_one_spec_per_case_with_checks_first_then_products_and_local_key_notes(tmp_path):
    contract, water, transport = _child_setup(tmp_path)
    report = _child_build(tmp_path, contract, water, transport)
    assert report["status"] == "OK" and report["counts"] == {"cases": 1, "inspections": 1, "products": 2, "specs": 1, "blocked": 0}
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    insp, p1, p2 = spec["creates"]
    f, F = insp["fields"], sync.INSPECT_F
    assert insp["tableId"] == CHECK and insp["stableKey"]["value"] == f"{CP}|初查" and insp["parent"]["recordId"] == "recEXIST000001"
    assert (f[F["stage"]]["new"], f[F["date"]]["new"], f[F["month"]]["new"], f[F["team"]]["new"]) == ("初查", "2026-06-17", "2026-06", "梁溪大队")
    assert (f[F["result"]]["new"], f[F["count"]]["new"], f[F["bad"]]["new"], f[F["method"]]["new"]) == ("不合格", 2, 1, "抽样送检、现场判定")
    assert (f[F["source"]]["new"], f[F["date_source"]]["new"], f[F["in_scope"]]["new"]) == ("来源详情", "监督系统", "是") and F["round"] not in f
    ref = f[F["key"]]["basis"]["sourceRef"]
    assert "manifestSha256=sha256:" in ref and CP in ref and "原生标识留空" in ref
    assert p1["stableKey"]["value"] == f"{CP}|初查|1" and p2["stableKey"]["value"] == f"{CP}|初查|2"
    assert p1["parent"] == {"createRef": f"{CP}|初查", "stableKey": {"fieldId": F["key"], "value": f"{CP}|初查"}} and p1["fields"][sync.PRODUCT_F["inspection"]]["new"].startswith("rec")
    assert p1["fields"][sync.PRODUCT_F["method"]]["new"] == "现场判定" and p2["fields"][sync.PRODUCT_F["method"]]["new"] == "抽样送检"
    assert sync.PRODUCT_F["model"] not in p2["fields"] and p2["fields"][sync.PRODUCT_F["problem"]]["new"] == "标志不符合" and not transport.creates


def test_child_build_zero_bad_count_is_an_explicit_negative_only_when_every_product_has_a_result(tmp_path):
    contract, water, transport = _child_setup(tmp_path, initial=[{"name": "灭火器", "method": "ONSITE", "result": "QUALIFIED"}])
    spec = json.loads(Path(_child_build(tmp_path, contract, water, transport)["specs"][0]["path"]).read_text(encoding="utf-8"))
    bad = spec["creates"][0]["fields"][sync.INSPECT_F["bad"]]
    assert bad["new"] == 0 and bad["basis"]["explicitNegative"] is True and spec["creates"][0]["fields"][sync.INSPECT_F["result"]]["new"] == "合格"
    sub = tmp_path / "pending"
    sub.mkdir()
    contract, water, transport = _child_setup(sub, initial=[{"name": "灭火器", "method": "SAMPLING", "result": "PENDING"}])
    spec = json.loads(Path(_child_build(sub, contract, water, transport)["specs"][0]["path"]).read_text(encoding="utf-8"))
    fields = spec["creates"][0]["fields"]
    assert fields[sync.INSPECT_F["result"]]["new"] == "待核实" and sync.INSPECT_F["bad"] not in fields   # 结果未出：不断言“不合格产品数=0”


def test_child_build_without_any_product_result_uses_the_proven_classification_and_omits_unknown_fields(tmp_path):
    evidence = tmp_path / "证据.json"
    evidence.write_text("{}", encoding="utf-8")
    contract, water, transport = _child_setup(tmp_path, initial=[{"name": "灭火器", "qualityCheckRaw": "市场准入检查不合格，未开展产品质量现场检查。"}],
                                              classification={"initialResult": "UNQUALIFIED", "evidencePath": "证据.json", "evidenceSha256": "sha256:" + sha(evidence)})
    spec = json.loads(Path(_child_build(tmp_path, contract, water, transport)["specs"][0]["path"]).read_text(encoding="utf-8"))
    insp, product = spec["creates"]
    assert insp["fields"][sync.INSPECT_F["result"]]["new"] == "不合格" and sync.INSPECT_F["method"] not in insp["fields"] and sync.INSPECT_F["bad"] not in insp["fields"]
    assert sync.PRODUCT_F["result"] not in product["fields"] and sync.PRODUCT_F["method"] not in product["fields"]
    assert product["fields"][sync.PRODUCT_F["cert"]]["new"].startswith("市场准入检查不合格")


def test_child_build_recheck_gets_round_one_pdf_date_source_and_a_per_product_relation_fact(tmp_path):
    recheck = {"clientRef": "rc", "stage": "RECHECK", "inspectionDate": "2026-07-20",
               "products": [{"clientRef": "r1", "name": "灭火器", "method": "ONSITE", "result": "QUALIFIED", "initialProductRef": "p1"},
                            {"clientRef": "r2", "name": "喷头", "method": "ONSITE", "result": "QUALIFIED"}]}
    contract, water, transport = _child_setup(tmp_path, recheck=recheck)
    report = _child_build(tmp_path, contract, water, transport)
    assert report["counts"]["inspections"] == 2 and report["counts"]["products"] == 4
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    by_key = {c["stableKey"]["value"]: c for c in spec["creates"]}
    rc = by_key[f"{CP}|复查"]["fields"]
    assert rc[sync.INSPECT_F["round"]]["new"] == 1 and rc[sync.INSPECT_F["date_source"]]["new"] == "来源复查记录PDF" and rc[sync.INSPECT_F["date"]]["new"] == "2026-07-20"
    assert f"{CP}|复查|2" in by_key and by_key[f"{CP}|复查|1"]["parent"]["createRef"] == f"{CP}|复查"
    relations = report["cases"][0]["recheckRelations"]
    assert [(r["recheckProduct"], r["relation"]) for r in relations] == [(f"{CP}|复查|1", "proven-by-initialProductRef"), (f"{CP}|复查|2", "unproven-no-matching-initialProductRef")]
    assert relations[0]["initialProduct"] == f"{CP}|初查|1"


@pytest.mark.parametrize("kw,why", [
    ({"tamper": True}, "摘要"), ({"state": "NEW"}, "未完成"), ({"cloud_day": "2026-06-18"}, "不等于清单初查日"),
    ({"brigade": "BINHU"}, "不一致"), ({"brigade": "ZZZ"}, "不一致"),
])
def test_child_build_holds_only_the_case_whose_evidence_does_not_hold(tmp_path, kw, why):
    contract, water, transport = _child_setup(tmp_path, **kw)
    report = _child_build(tmp_path, contract, water, transport)
    assert report["status"] == "PARTIAL" and report["counts"]["cases"] == 0 and not report["specs"]
    assert why in json.dumps(report["blockedEntities"], ensure_ascii=False)


def test_child_build_leaves_cases_with_any_existing_child_and_unaccepted_release_targets_alone(tmp_path):
    contract, water, transport = _child_setup(tmp_path)
    transport.rows[(CHECK, "recCHK00000001")] = {sync.INSPECT_F["key"]: f"{CP}|初查"}
    report = _child_build(tmp_path, contract, water, transport)
    assert report["status"] == "OK" and report["counts"]["cases"] == 0 and report["fresh"]["casesAlreadyWithChildren"] == 1 and not report["specs"]
    sub = tmp_path / "held"
    sub.mkdir()
    contract, water, transport = _child_setup(sub)
    ledger = sub / "child-ledger"
    sync.Ledger(ledger, isolated_test=True).initialize()
    sync.Ledger(ledger, isolated_test=True).append(sync.Ledger.target_key(BASE, CASE, "create:" + CP), {"event": "human_release", "intentId": "i-old", "operator": "助手(历史)", "reason": "r"})
    held = _child_build(sub, contract, water, transport, ledger_dir=ledger)
    assert held["status"] == "PARTIAL" and "human_release" in json.dumps(held["blockedEntities"], ensure_ascii=False) and not held["specs"]


def test_child_specs_validate_and_execute_with_parent_links_and_the_parent_release_gate(tmp_path):
    contract, water, transport = _child_setup(tmp_path)
    report = _child_build(tmp_path, contract, water, transport)
    spec = json.loads(Path(report["specs"][0]["path"]).read_text(encoding="utf-8"))
    spec["mode"] = "plan"
    plan = sync.run(write_spec(tmp_path, spec, "plan.json"), transport=transport, ledger_dir=tmp_path / "ledger")
    assert plan["executable"] is True and len(plan["creates"]) == 3
    ledger = tmp_path / "ledger"
    # 执行器自己的闸门：父案目标上有未获用户接受的 human_release 时，不新增任何子行
    sync.Ledger(ledger, isolated_test=True).initialize()
    sync.Ledger(ledger, isolated_test=True).append(sync.Ledger.target_key(BASE, CASE, "create:" + CP), {"event": "human_release", "intentId": "i-old", "operator": "助手(历史)", "reason": "r"})
    spec["mode"], spec["logDir"] = "execute", str(tmp_path / "exec-log")
    result = sync.run(write_spec(tmp_path, spec, "exec.json"), execute_flag=True, transport=transport, ledger_dir=ledger)
    assert [e["status"] for e in result["records"]] == ["BLOCKED_ENTITY"] * 3 and not transport.creates and result["status"] == "PARTIAL"
    assert "human_release" in result["records"][0]["reasons"][0]


def test_update_spec_refuses_a_create_once_field(tmp_path):
    contract = make_child_contract(tmp_path)
    base = json.loads(contract.read_text(encoding="utf-8"))["baseContract"]
    v9 = tmp_path / "v9.json"
    v9.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    field = sync.INSPECT_F["result"]
    spec = {"schemaVersion": sync.SPEC_SCHEMA, "mode": "plan", "contract": {"path": str(v9), "sha256": sha(v9)},
            "target": {"baseToken": BASE, "profile": "offline-fake", "identity": "user"}, "limits": {"maxRecords": 5},
            "allowlist": {CHECK: {"records": ["recCHK00000001"], "fields": [field]}},
            "updates": [{"tableId": CHECK, "recordId": "recCHK00000001", "stableKey": {"fieldId": sync.INSPECT_F["key"], "value": f"{CP}|初查"},
                         "fields": {field: {"new": "合格", "basis": basis()}}}]}
    with pytest.raises(sync.SyncError, match="create-once"):
        sync.run(write_spec(tmp_path, spec), transport=BuildTransport(), ledger_dir=tmp_path / "ledger")
