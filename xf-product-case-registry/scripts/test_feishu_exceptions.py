"""a33: exception register — idempotent create, in-place update, legacy rows untouched, unsafe content refused, unknown write blocks its key."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import scripts.feishu_exceptions as ex
from scripts.feishu_period import SHANGHAI

BASE = "XOZ1bfapIasXW1ssaeEcovPinLc"
TABLE = "tblEXC"
NAMES = ("核对编号", "项目编号", "类型", "处理状态", "关键信息是否受影响", "字段", "内容", "建议", "来源", "记录时间")
IDS = {n: f"fld{i:02d}" for i, n in enumerate(NAMES)}
REGISTRY = {"tableId": TABLE, "ids": IDS, "version": "t", "options": {
    "类型": ("来源包混放", "来源材料缺项", "关键事实待核实"), "关键信息是否受影响": ("否", "是", "待核实"), "来源": ("来源详情", "底册/系统", "多源")}}
NOW = datetime(2026, 11, 18, 11, 20, tzinfo=SHANGHAI)
SELECTS = ("类型", "处理状态", "关键信息是否受影响", "来源")


class Fake:
    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}
        self.calls: list[tuple] = []
        self.fail_create = False
        self.reject_text_as_select = True
        self.n = 0

    def _store(self, fields):
        return {k: v for k, v in fields.items()}

    def list_rows(self, base, table, field_ids=None):
        assert table == TABLE
        return {rid: dict(row) for rid, row in self.rows.items()}

    def create_record(self, base, table, fields):
        self.calls.append(("create", fields))
        assert isinstance(fields.get(IDS["处理状态"]), str), "处理状态是文本列"
        if self.fail_create:
            raise RuntimeError("transport broke")
        self.n += 1
        rid = f"recEXC{self.n:04d}"
        row = {IDS[n]: None for n in NAMES}
        for fid, v in fields.items():
            row[fid] = v if not isinstance(v, str) or fid != IDS["记录时间"] else v.replace(" ", "T") + ".000+08:00"
        self.rows[rid] = row
        return {"ok": True, "data": {"record": {"record_id": rid}}}

    def upsert(self, base, table, record_id, fields):
        self.calls.append(("upsert", record_id, fields))
        row = self.rows[record_id]
        for fid, v in fields.items():
            row[fid] = v if fid != IDS["记录时间"] else v.replace(" ", "T") + ".000+08:00"
        return {"ok": True, "data": {"record": {"record_id": record_id}}}


def legacy(fake):
    row = {IDS[n]: None for n in NAMES}
    row[IDS["核对编号"]] = "C0001"
    row[IDS["项目编号"]] = "32002201C202600006"
    row[IDS["内容"]] = "旧核对记录"
    fake.rows["recLEGACY1"] = row


def entry(**over):
    base = {"projectNo": "32002221C202600090", "type": "来源包混放", "affected": "否", "fields": "无", "status": "已登记保留",
            "content": "原包含与本案无关的附件(大队上传错误)，整份原包保留；本案按监督系统事实收口", "suggestion": "无需再找大队补传", "source": "来源详情"}
    base.update(over)
    return base


def go(tmp_path, fake, entries, execute=True, now=NOW):
    return ex.run(registry=REGISTRY, entries=entries, base=BASE, transport=fake, business_root=tmp_path, execute=execute, now=now)


def test_a_new_entry_is_created_once_read_back_and_a_rerun_writes_nothing_and_legacy_rows_are_left_alone(tmp_path):
    fake = Fake()
    legacy(fake)
    first = go(tmp_path, fake, [entry(), entry(projectNo="32002221C202600094", content="同类：与本案无关的他单位通知书副本，整份原包保留")])
    assert first["status"] == "OK" and len(first["apply"]["written"]) == 2 and first["apply"]["readbackProblems"] == []
    keys = sorted(r[IDS["核对编号"]] for r in fake.rows.values())
    assert keys == ["C0001", "X-32002221C202600090-来源包混放", "X-32002221C202600094-来源包混放"]
    created = next(r for r in fake.rows.values() if r[IDS["核对编号"]] == "X-32002221C202600090-来源包混放")
    assert created[IDS["类型"]] == ["来源包混放"] and created[IDS["关键信息是否受影响"]] == ["否"] and created[IDS["记录时间"]].startswith("2026-11-18T11:20")
    before = [c for c in fake.calls]
    again = go(tmp_path, fake, [entry(), entry(projectNo="32002221C202600094", content="同类：与本案无关的他单位通知书副本，整份原包保留")], now=datetime(2026, 11, 18, 23, 0, tzinfo=SHANGHAI))
    assert again["status"] == "OK" and again["plan"]["unchanged"] and not again["plan"]["creates"] and not again["plan"]["updates"] and fake.calls == before
    assert fake.rows["recLEGACY1"][IDS["内容"]] == "旧核对记录"


def test_a_changed_entry_updates_the_same_row_and_moves_the_record_time_only_then(tmp_path):
    fake = Fake()
    go(tmp_path, fake, [entry()])
    later = datetime(2026, 11, 19, 8, 5, tzinfo=SHANGHAI)
    out = go(tmp_path, fake, [entry(status="已解除", content="大队已补传正确材料")], now=later)
    assert out["status"] == "OK" and [u["key"] for u in out["plan"]["updates"]] == ["X-32002221C202600090-来源包混放"]
    assert len(fake.rows) == 1
    row = next(iter(fake.rows.values()))
    assert row[IDS["处理状态"]] == "已解除" and row[IDS["内容"]] == "大队已补传正确材料" and row[IDS["记录时间"]].startswith("2026-11-19T08:05")
    events = [json.loads(line) for p in sorted((tmp_path / "运行记录" / ex.LEDGER_DIR).glob("*.jsonl")) for line in p.read_text(encoding="utf-8").splitlines()]
    update_intent = [e for e in events if e["event"] == "intent" and e["op"] == "update"][0]
    assert update_intent["old"]["内容"].startswith("原包含") and update_intent["old"]["处理状态"] == "已登记保留"      # 旧值留在写入账


@pytest.mark.parametrize("bad", [
    {"content": "附件存放在 D:\\案卷\\x"}, {"content": "见 1.pdf 第二页"}, {"content": "当事人身份证号 410422199003112882"}, {"suggestion": "电话 13915211599"},
    {"projectNo": "bad"}, {"type": "随便"}, {"affected": "可能"}, {"status": "完成"}, {"source": "网上"}, {"content": ""},
    {"affected": "是", "fields": "无"}, {"content": "长" * 121}])
def test_an_unsafe_or_invalid_entry_is_refused_before_any_request(tmp_path, bad):
    fake = Fake()
    out = go(tmp_path, fake, [entry(**bad)])
    assert out["status"] == "ATTENTION" and out["plan"]["invalid"] and not fake.calls and not fake.rows


def test_an_unknown_create_blocks_its_key_and_is_never_replayed(tmp_path):
    fake = Fake()
    fake.fail_create = True
    first = go(tmp_path, fake, [entry()])
    assert first["status"] == "UNKNOWN" and len(fake.calls) == 1
    fake.fail_create = False
    second = go(tmp_path, fake, [entry()])
    assert second["status"] == "ATTENTION" and second["plan"]["held"][0]["key"] == "X-32002221C202600090-来源包混放" and len(fake.calls) == 1 and not fake.rows


def test_duplicate_keys_in_the_table_or_the_list_are_reported_and_not_written(tmp_path):
    fake = Fake()
    go(tmp_path, fake, [entry()])
    dup = dict(next(iter(fake.rows.values())))
    fake.rows["recDUP"] = dup
    calls = len(fake.calls)
    out = go(tmp_path, fake, [entry(content="改了")])
    assert out["status"] == "ATTENTION" and list(out["plan"]["duplicates"]) == ["X-32002221C202600090-来源包混放"] and len(fake.calls) == calls
    out2 = go(tmp_path, Fake(), [entry(), entry()])
    assert out2["plan"]["invalid"][0]["problems"] == ["同一清单内重复的稳定键"]


def test_an_update_is_not_applied_when_the_live_row_changed_since_planning(tmp_path):
    fake = Fake()
    go(tmp_path, fake, [entry()])
    planned = ex.plan(ex._named(fake.list_rows(BASE, TABLE), IDS), [entry(status="待核实")], REGISTRY, NOW, {})
    next(iter(fake.rows.values()))[IDS["处理状态"]] = "已解除"      # 他人在计划后改了要写的那一格
    result = ex.apply(planned, transport=fake, base=BASE, registry=REGISTRY, journal=ex.Journal(tmp_path / "j"))
    assert result["written"] == [] and "旧值与计划不符" in result["skipped"][0]["reason"]


def _unknown_create(tmp_path, fake, error):
    class Broken(Fake):
        def create_record(self, base, table, fields):
            self.calls.append(("create", fields))
            raise RuntimeError(error)
    broken = Broken()
    broken.rows = fake.rows
    out = go(tmp_path, broken, [entry()])
    assert out["status"] == "UNKNOWN"
    journal = ex.Journal(tmp_path / "运行记录" / ex.LEDGER_DIR)
    return next(iter(journal.state()["intents"]))


def test_reconcile_proves_a_structured_service_rejection_only_after_a_minute_and_only_with_no_row(tmp_path):
    fake = Fake()
    rejection = 'UNKNOWN: 官方 CLI 输出不是 JSON（rc=1 stdout=\'\' stderr=\'{ "ok": false, "identity": "user", "error": { "type": "api", "code": 800010407, "message": "The cell value'
    intent = _unknown_create(tmp_path, fake, rejection)
    early = ex.reconcile(registry=REGISTRY, base=BASE, transport=fake, business_root=tmp_path, intent_id=intent, now=datetime.now(SHANGHAI))
    assert early["status"] == "TOO_EARLY"
    later = ex.reconcile(registry=REGISTRY, base=BASE, transport=fake, business_root=tmp_path, intent_id=intent, now=datetime.now(SHANGHAI) + timedelta(seconds=120))
    assert later["status"] == "NOT_APPLIED_PROVEN" and later["evidence"]["basis"] == "服务端结构化拒绝"
    assert ex.reconcile(registry=REGISTRY, base=BASE, transport=fake, business_root=tmp_path, intent_id=intent)["status"] == "ALREADY_RECONCILED"
    again = go(tmp_path, fake, [entry()])                      # 解封后正常入口重新写，只写一次
    assert again["status"] == "OK" and len(again["apply"]["written"]) == 1


def test_reconcile_without_a_structured_rejection_needs_the_quiet_period_and_a_row_that_exists_is_applied_proven(tmp_path):
    fake = Fake()
    intent = _unknown_create(tmp_path, fake, "timeout")
    far = datetime.now(SHANGHAI).replace(year=2027)
    assert ex.reconcile(registry=REGISTRY, base=BASE, transport=fake, business_root=tmp_path, intent_id=intent, now=datetime.now(SHANGHAI).replace(microsecond=0))["status"] == "TOO_EARLY"
    # the write actually landed
    fake.create_record(BASE, TABLE, {IDS["核对编号"]: "X-32002221C202600090-来源包混放", IDS["处理状态"]: "已登记保留", IDS["内容"]: entry()["content"], IDS["字段"]: "无",
                                     IDS["建议"]: entry()["suggestion"], IDS["项目编号"]: "32002221C202600090", IDS["类型"]: ["来源包混放"],
                                     IDS["关键信息是否受影响"]: ["否"], IDS["来源"]: ["来源详情"]})
    proven = ex.reconcile(registry=REGISTRY, base=BASE, transport=fake, business_root=tmp_path, intent_id=intent, now=far)
    assert proven["status"] == "APPLIED_PROVEN"
