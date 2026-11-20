import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_online_sheet as mos

RULE = mos.rules()
BRIGADES = RULE["brigades"]
ITEMS = RULE["items"]
MULTI = "1、甲公司存在问题\n2、乙公司存在问题"
BACKSLASH = "\\"


def make_local(path, extra_rows=0, values=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "1"
    ws["A1"] = "月考核情况记录表"
    for i, name in enumerate(BRIGADES + ["空港大队"]):
        ws.cell(row=2, column=4 + i, value=name)
    ws["C3"] = "融合通信群出警报送"
    for r, item in zip((14, 15, 16), ITEMS):
        ws.cell(row=r, column=3, value=item)
    ws["C17"] = "联网工作实效"
    for i, name in enumerate(BRIGADES):
        ws.cell(row=14, column=4 + i, value=f"{name}案卷\n{MULTI}")
        ws.cell(row=15, column=4 + i, value=BACKSLASH if i == 0 else f"{name}实效")
        ws.cell(row=16, column=4 + i, value=f"{name}联网")
    ws["E15"].fill = PatternFill("solid", fgColor="FFFF0000")
    ws["F14"] = None  # 源空值
    for key, value in (values or {}).items():
        ws[key] = value
    wb.save(path)


def online_cells(local_values, header_row=3, first_col=5, label_col=2, row_offset=3, sheet_other=True):
    """合成在线表：大队列和项目行刻意与本地不同位置；含他人维护的内容。"""
    cells = {mos.coord(1, 1): {"v": "26年通信科技科月考核", "f": None}}
    for i, name in enumerate(BRIGADES + ["空港大队"]):
        cells[mos.coord(header_row, first_col + i)] = {"v": name, "f": None}
    for item, row in zip(ITEMS, (14 + row_offset, 15 + row_offset, 16 + row_offset)):
        cells[mos.coord(row, label_col)] = {"v": item, "f": None}
    cells[mos.coord(header_row + 1, label_col)] = {"v": "融合通信群出警报送", "f": None}
    cells[mos.coord(header_row + 1, first_col)] = {"v": "他人维护的内容", "f": None}
    for (brigade, item), value in local_values.items():
        row = 14 + row_offset + ITEMS.index(item)
        cells[mos.coord(row, first_col + BRIGADES.index(brigade))] = {"v": value, "f": None}
    return cells


def snapshot(cells, title="26年通信科技科月考核情况记录表", sheet="11月情况", sheets=None):
    snap = mos.blank_snapshot(
        {"url": "https://example.invalid/x", "title": title, "reader": "test", "captured_at": "t"},
        sheets or [{"name": "空表模板", "id": 1}, {"name": sheet, "id": 7}, {"name": "12月情况", "id": 8}],
        sheet,
        7,
    )
    snap["cells"] = cells
    return snap


class FakeOnline:
    """同时充当 reader 与 writer；hook 可在每次读之前模拟他人修改。"""

    def __init__(self, snap):
        self.snap = snap
        self.writes = []
        self.reads = 0
        self.before_read = None
        self.fail_write = False
        self.silent_drop = False

    def read(self, sheet_name):
        self.reads += 1
        if self.before_read:
            self.before_read(self, self.reads)
        return copy.deepcopy(self.snap)

    def write(self, worksheet_id, cells):
        self.writes.append((worksheet_id, list(cells)))
        if self.silent_drop:
            return
        for key, text in cells:
            self.snap["cells"][key] = {"v": text, "f": None}
        if self.fail_write:
            raise RuntimeError("timeout")


class OnlineSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "【待补】2026年11月科室月考核情况记录表.xlsx"
        make_local(self.src)
        self.local = mos.read_local(self.src, RULE)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, cells=None, **kwargs):
        snap = snapshot(cells if cells is not None else online_cells({}), **kwargs)
        return mos.build_plan(self.local, snap, 2026, 11, RULE), snap

    def test_local_reads_24_cells_literally(self):
        self.assertEqual(len(self.local["cells"]), 24)
        self.assertEqual(self.local["cells"][("梁溪大队", "产品工作实效")]["value"], BACKSLASH)
        self.assertTrue(self.local["cells"][("梁溪大队", "产品案卷核查")]["value"].count("\n") >= 2)
        self.assertIsNone(self.local["cells"][("惠山大队", "产品案卷核查")]["value"])
        self.assertTrue(self.local["cells"][("锡山大队", "产品工作实效")]["pending_fill"])
        self.assertFalse(self.local["cells"][("梁溪大队", "产品工作实效")]["pending_fill"])
        self.assertNotIn("空港大队", {k[0] for k in self.local["cells"]})

    def test_plan_maps_by_text_not_coordinates(self):
        plan, _ = self.plan()
        self.assertEqual(plan["status"], "ok")
        first = next(i for i in plan["items"] if i["brigade"] == "梁溪大队" and i["item"] == "产品案卷核查")
        self.assertEqual(first["local_cell"], "D14")
        self.assertEqual(first["online_cell"], "E17")
        self.assertEqual(plan["summary"]["write"], 23)
        self.assertEqual(plan["summary"]["skip_source_blank"], 1)

    def test_plan_blocks_wrong_year_title_and_missing_month_sheet(self):
        plan, _ = self.plan(title="25年通信科技科月考核情况记录表")
        self.assertEqual({b["code"] for b in plan["blockers"]}, {"title_year_mismatch"})
        plan, _ = self.plan(sheet="10月情况")
        self.assertIn("sheet_not_unique", {b["code"] for b in plan["blockers"]})
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(all(i["action"] == "blocked" or i["action"] != "write" for i in plan["items"]))

    def test_plan_blocks_duplicate_rows_and_duplicate_brigades(self):
        cells = online_cells({})
        cells["B30"] = {"v": "产品工作实效", "f": None}
        plan, _ = self.plan(cells)
        self.assertIn("item_ambiguous", {b["code"] for b in plan["blockers"]})
        cells = online_cells({})
        cells["Z3"] = {"v": "梁溪大队", "f": None}
        plan, _ = self.plan(cells)
        self.assertIn("brigade_duplicate", {b["code"] for b in plan["blockers"]})
        plan, _ = self.plan({"A1": {"v": "空", "f": None}})
        self.assertIn("header_missing", {b["code"] for b in plan["blockers"]})

    def test_plan_two_header_rows_is_ambiguous(self):
        cells = online_cells({})
        for i, name in enumerate(BRIGADES):
            cells[mos.coord(40, 5 + i)] = {"v": name, "f": None}
        plan, _ = self.plan(cells)
        self.assertIn("header_ambiguous", {b["code"] for b in plan["blockers"]})

    def test_source_blank_never_clears_and_formula_like_source_blocks(self):
        values = {("惠山大队", "产品案卷核查"): "旧值"}
        plan, _ = self.plan(online_cells(values))
        blank = next(i for i in plan["items"] if i["brigade"] == "惠山大队" and i["item"] == "产品案卷核查")
        self.assertEqual(blank["action"], "skip_source_blank")
        make_local(self.src, values={"D16": "=A1+B1"})
        # 本地公式直接停止，不当文本写
        with self.assertRaises(mos.SyncError) as ctx:
            mos.read_local(self.src, RULE)
        self.assertEqual(ctx.exception.code, "local_formula")
        make_local(self.src, values={"D16": " =1"})
        local = mos.read_local(self.src, RULE)
        local["cells"][("梁溪大队", "联网系统核查")]["value"] = "=cmd"
        plan = mos.build_plan(local, snapshot(online_cells({})), 2026, 11, RULE)
        self.assertEqual(plan["status"], "blocked")

    def test_protected_and_formula_cells_are_skipped(self):
        cells = online_cells({})
        cells["F17"] = {"v": None, "f": "=A1"}
        snap = snapshot(cells)
        snap["protected"] = ["E18"]
        plan = mos.build_plan(self.local, snap, 2026, 11, RULE)
        actions = {i["online_cell"]: i["action"] for i in plan["items"]}
        self.assertEqual(actions["F17"], "skip_online_formula")
        self.assertEqual(actions["E18"], "skip_protected")

    def full_values(self):
        return {k: v["value"] for k, v in self.local["cells"].items() if v["value"] is not None}

    def test_plan_is_idempotent_when_online_matches(self):
        plan, _ = self.plan(online_cells(self.full_values()))
        self.assertEqual(plan["summary"].get("write", 0), 0)
        self.assertEqual(plan["summary"]["no_change"], 23)

    def apply(self, fake, plan, evidence=None):
        return mos.apply_plan(plan, fake, fake, evidence or self.dir / "ev", 2026, 11, RULE)

    def test_apply_writes_only_differences_and_verifies(self):
        values = self.full_values()
        values[("锡山大队", "联网系统核查")] = "他人先改过的旧文字"
        del values[("江阴大队", "产品案卷核查")]
        cells = online_cells(values)
        plan, snap = self.plan(cells)
        fake = FakeOnline(snap)
        result = self.apply(fake, plan)
        self.assertEqual(result["status"], "done")
        self.assertEqual({e["online_cell"] for e in result["verified"]}, {"F19", "J17"})
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(sorted(k for k, _ in fake.writes[0][1]), ["F19", "J17"])
        self.assertEqual(fake.snap["cells"]["E18"]["v"], snap["cells"]["E18"]["v"])
        self.assertEqual(fake.snap["cells"]["E4"]["v"], "他人维护的内容")
        recovery = json.loads((self.dir / "ev" / "recovery.json").read_text(encoding="utf-8"))
        self.assertEqual(recovery["F19"], "他人先改过的旧文字")
        self.assertIsNone(recovery["J17"])
        # 幂等：再 plan/apply 不再写
        plan2 = mos.build_plan(self.local, fake.read("11月情况"), 2026, 11, RULE)
        self.assertEqual(plan2["summary"].get("write", 0), 0)
        fake.writes.clear()
        self.apply(fake, plan2, self.dir / "ev2")
        self.assertEqual(fake.writes, [])

    def test_apply_skips_concurrent_edit_between_plan_and_apply(self):
        plan, snap = self.plan(online_cells({("梁溪大队", "产品案卷核查"): "旧"}))
        fake = FakeOnline(snap)

        def human_edit(f, n):
            f.snap["cells"]["E17"] = {"v": "同事刚改的内容", "f": None}

        def later_edit(f, n):
            if n == 3:
                f.snap["cells"]["H30"] = {"v": "写入期间别处新增", "f": None}

        fake.before_read = lambda f, n: (human_edit(f, n), later_edit(f, n))
        result = self.apply(fake, plan)
        self.assertEqual([c["online_cell"] for c in result["conflicts"]], ["E17"])
        self.assertEqual(fake.snap["cells"]["E17"]["v"], "同事刚改的内容")
        self.assertNotIn("E17", {k for _, cells in fake.writes for k, _ in cells})
        self.assertIn("H30", {c["cell"] for c in result["outside_target_changes"]})

    def test_apply_aborts_when_layout_changed(self):
        plan, snap = self.plan()
        fake = FakeOnline(snap)

        def shift(f, n):
            old = {k: v for k, v in f.snap["cells"].items() if k.startswith("B") and v["v"] in ITEMS}
            for k, v in old.items():
                f.snap["cells"].pop(k)
                f.snap["cells"]["B" + str(int(k[1:]) + 1)] = v

        fake.before_read = shift
        with self.assertRaises(mos.SyncError) as ctx:
            self.apply(fake, plan)
        self.assertEqual(ctx.exception.code, "binding_or_mapping_drift")
        self.assertEqual(fake.writes, [])

    def test_unknown_write_is_not_replayed_and_reconciles(self):
        plan, snap = self.plan()
        fake = FakeOnline(snap)
        fake.fail_write = True
        result = self.apply(fake, plan)
        self.assertEqual(result["status"], "needs_reconcile")
        self.assertIn("timeout", result["write_error"])
        with self.assertRaises(mos.SyncError) as ctx:
            self.apply(fake, plan)
        self.assertEqual(ctx.exception.code, "unknown_prior_write")
        self.assertEqual(len(fake.writes), 1)
        report = mos.reconcile(plan, fake, self.dir / "ev")
        self.assertTrue(report["all_in_place"])
        state = json.loads((self.dir / "ev" / "apply-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "done")

    def test_silent_drop_is_reported_as_mismatch(self):
        plan, snap = self.plan()
        fake = FakeOnline(snap)
        fake.silent_drop = True
        result = self.apply(fake, plan)
        self.assertEqual(result["status"], "needs_reconcile")
        self.assertEqual(len(result["mismatch"]), 23)

    def test_blocked_plan_refuses_apply(self):
        plan, snap = self.plan(title="25年表")
        with self.assertRaises(mos.SyncError) as ctx:
            self.apply(FakeOnline(snap), plan)
        self.assertEqual(ctx.exception.code, "plan_blocked")

    def test_cli_adapter_payloads_and_snapshot(self):
        calls = []
        sheets = {"sheetsInfo": [{"sheetName": "11月情况", "sheetId": 7, "rowTo": 20, "colTo": 14}, {"sheetName": "10月情况", "sheetId": 6, "rowTo": 1, "colTo": 1}]}
        rng = {"detail": {"rangeData": [{"rowFrom": 1, "colFrom": 3, "originalCellValue": "梁溪大队", "cellText": "梁溪大队"}]}}

        def runner(argv, payload):
            calls.append((argv[1:], payload))
            if argv[1:3] == ["auth", "status"]:
                return 0, '{"authenticated": true}', ""
            if argv[2] == "get-sheets-info":
                return 0, json.dumps({"data": sheets}), ""
            if argv[2] == "get-file-info":
                return 0, json.dumps({"data": {"fname": "26年通信科技科月考核情况记录表"}}), ""
            if argv[2] == "get-range-data":
                return 0, json.dumps(rng), ""
            return 0, "{}", ""

        cli = mos.CliSheet("kdocs-cli", "https://example.invalid/l/x", runner)
        self.assertTrue(cli.authenticated())
        snap = cli.read("11月情况")
        self.assertEqual(snap["worksheet_id"], 7)
        self.assertEqual(snap["cells"]["D2"]["v"], "梁溪大队")
        rd = next(p for a, p in calls if a[1] == "get-range-data")
        self.assertEqual(rd["range"], {"rowFrom": 0, "rowTo": 20, "colFrom": 0, "colTo": 14})
        cli.write(7, [("E17", "文字\n第二行"), ("D15", "\\")])
        argv, payload = calls[-1]
        self.assertEqual(argv[:2], ["sheet", "range-data-batch-update"])
        op = payload["range_data"][0]
        self.assertEqual((op["row_from"], op["col_from"], op["row_to"], op["col_to"]), (16, 4, 16, 4))
        self.assertEqual(payload["range_data"][1]["formula"], "\\")
        self.assertEqual(payload["worksheet_id"], 7)

    def test_cli_unauthenticated_and_failure(self):
        cli = mos.CliSheet("kdocs-cli", "u", lambda argv, p: (0, '{"authenticated": false, "token": ""}', ""))
        self.assertFalse(cli.authenticated())
        bad = mos.CliSheet("kdocs-cli", "u", lambda argv, p: (1, "", "403"))
        with self.assertRaises(mos.SyncError) as ctx:
            bad.call("sheet", "get-sheets-info", {"url": "u"})
        self.assertEqual(ctx.exception.code, "cli_failed")

    def test_main_plan_with_snapshot_file_leaves_source_untouched(self):
        before = mos.file_sha256(self.src)
        snap_path = self.dir / "snap.json"
        mos.write_json(snap_path, snapshot(online_cells({})))
        out = self.dir / "out"
        code = mos.main(["plan", "--source", str(self.src), "--year", "2026", "--month", "11", "--out", str(out), "--snapshot", str(snap_path)])
        self.assertEqual(code, 0)
        self.assertEqual(mos.file_sha256(self.src), before)
        self.assertEqual(json.loads((out / "plan.json").read_text(encoding="utf-8"))["status"], "ok")
        code = mos.main(["apply", "--source", str(self.src), "--year", "2026", "--month", "11", "--out", str(out), "--snapshot", str(snap_path)])
        self.assertEqual(code, 2)  # 浏览器/快照入口不经脚本写
        self.assertFalse((out / "apply-state.json").exists())


class ReworkGuardTests(unittest.TestCase):
    """a2 返工：年月/文档/白名单/对账/并发的收紧。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "【待补】2026年11月科室月考核情况记录表.xlsx"
        make_local(self.src)
        self.local = mos.read_local(self.src, RULE)
        self.snap = snapshot(online_cells({}))
        self.plan = mos.build_plan(self.local, self.snap, 2026, 11, RULE)

    def tearDown(self):
        self.tmp.cleanup()

    def run_apply(self, plan=None, snap=None, name="ev", year=2026, month=11, sheet_name=None):
        fake = FakeOnline(copy.deepcopy(snap or self.snap))
        return fake, lambda: mos.apply_plan(copy.deepcopy(plan or self.plan), fake, fake, self.dir / name, year, month, RULE, sheet_name)

    def assert_rejected(self, **kwargs):
        fake, call = self.run_apply(**kwargs)
        with self.assertRaises(mos.SyncError) as ctx:
            call()
        self.assertEqual(fake.writes, [])
        return ctx.exception

    def test_period_is_bound_end_to_end(self):
        dec = snapshot(online_cells({}), sheet="12月情况", sheets=[{"name": "12月情况", "id": 8}])
        plan = mos.build_plan(self.local, dec, 2026, 12, RULE)
        self.assertIn("period_mismatch", {b["code"] for b in plan["blockers"]})
        self.assertEqual(plan["status"], "blocked")
        other = mos.build_plan(self.local, self.snap, 2025, 11, RULE)
        self.assertIn("period_mismatch", {b["code"] for b in other["blockers"]})
        unknown = mos.read_local(self.src, RULE)
        unknown["period"] = None
        plan = mos.build_plan(unknown, self.snap, 2026, 11, RULE)
        self.assertIn("local_period_unknown", {b["code"] for b in plan["blockers"]})

    def test_explicit_source_period_is_recorded_but_cannot_override_args(self):
        renamed = self.dir / "月考核表.xlsx"
        renamed.write_bytes(self.src.read_bytes())
        self.assertIsNone(mos.read_local(renamed, RULE)["period"])
        explicit = mos.read_local(renamed, RULE, period=(2026, 11))
        self.assertEqual(mos.build_plan(explicit, self.snap, 2026, 11, RULE)["status"], "ok")
        self.assertEqual(mos.build_plan(explicit, self.snap, 2026, 12, RULE)["status"], "blocked")

    def test_sheet_name_cannot_redirect_month(self):
        plan = mos.build_plan(self.local, snapshot(online_cells({}), sheet="12月情况", sheets=[{"name": "12月情况", "id": 8}]), 2026, 11, RULE, sheet_name="12月情况")
        self.assertIn("sheet_name_override_conflict", {b["code"] for b in plan["blockers"]})
        err = self.assert_rejected(sheet_name="12月情况")
        self.assertEqual(err.code, "sheet_name_override_conflict")

    def test_plan_and_arguments_must_agree(self):
        for mutate in (lambda p: p.update(month=10), lambda p: p.update(year=2025)):
            plan = copy.deepcopy(self.plan)
            mutate(plan)
            self.assertEqual(self.assert_rejected(plan=plan).code, "plan_invalid")
        self.assertEqual(self.assert_rejected(month=12).code, "plan_invalid")

    def test_document_and_worksheet_binding(self):
        for label, mutate in (
            ("url", lambda s: s["identity"].update(url="https://example.invalid/other")),
            ("title", lambda s: s["identity"].update(title="26年别的表")),
            ("sheet-id", lambda s: s.update(worksheet_id=999)),
        ):
            snap = copy.deepcopy(self.snap)
            mutate(snap)
            self.assertEqual(self.assert_rejected(snap=snap, name="ev-" + label).code, "binding_or_mapping_drift", label)
        plan = copy.deepcopy(self.plan)
        plan["online"]["file_id"] = "file_a"
        plan["fingerprint"] = mos.plan_fingerprint(plan)
        snap = copy.deepcopy(self.snap)
        snap["identity"]["file_id"] = "file_b"
        self.assertEqual(self.assert_rejected(plan=plan, snap=snap, name="ev-fid").code, "binding_or_mapping_drift")

    def test_url_query_and_fragment_do_not_change_identity(self):
        snap = copy.deepcopy(self.snap)
        snap["identity"]["url"] = "https://EXAMPLE.invalid/x/?from=share#top"
        fake, call = self.run_apply(snap=snap)
        self.assertEqual(call()["status"], "done")

    def test_whitelist_is_recomputed_not_trusted(self):
        plan = copy.deepcopy(self.plan)
        plan["items"][0]["online_cell"] = "A100"
        self.assertEqual(self.assert_rejected(plan=plan).code, "plan_invalid")
        # 指纹被同步改过也不行：在线坐标必须等于按语义重算的坐标
        plan["fingerprint"] = mos.plan_fingerprint(plan)
        err = self.assert_rejected(plan=plan, name="ev2")
        self.assertIn("online_cell_not_semantic", err.detail + "") if err.code == "binding_or_mapping_drift" else self.assertEqual(err.code, "plan_invalid")
        short = copy.deepcopy(self.plan)
        short["items"].pop()
        self.assertEqual(self.assert_rejected(plan=short, name="ev3").code, "plan_invalid")
        dup = copy.deepcopy(self.plan)
        dup["items"][1]["online_cell"] = dup["items"][0]["online_cell"]
        self.assertEqual(self.assert_rejected(plan=dup, name="ev4").code, "plan_invalid")

    def test_plan_values_must_match_source_and_source_must_be_unchanged(self):
        plan = copy.deepcopy(self.plan)
        item = next(i for i in plan["items"] if i["action"] == "write")
        item["local_value"] = "被改过的值"
        plan["fingerprint"] = mos.plan_fingerprint(plan)
        err = self.assert_rejected(plan=plan)
        self.assertEqual(err.code, "plan_invalid")
        self.assertIn("source_mismatch", err.detail)
        make_local(self.src, values={"D16": "源后来被改了"})
        err = self.assert_rejected(name="ev2")
        self.assertIn("source_changed", err.detail)

    def test_formula_like_prefixes_are_blocked(self):
        for text in ("=1+1", "+1", "-1", "@x"):
            local = copy.deepcopy(self.local)
            local["cells"][("梁溪大队", "联网系统核查")]["value"] = text
            plan = mos.build_plan(local, self.snap, 2026, 11, RULE)
            self.assertIn("value_looks_like_formula", {b["code"] for b in plan["blockers"]}, text)
            self.assertEqual(plan["status"], "blocked")

    def test_recheck_just_before_write_skips_late_edit(self):
        fake = FakeOnline(copy.deepcopy(self.snap))
        fake.before_read = lambda f, n: f.snap["cells"].__setitem__("E18", {"v": "写前最后一刻被人改了", "f": None}) if n == 2 else None
        result = mos.apply_plan(copy.deepcopy(self.plan), fake, fake, self.dir / "ev", 2026, 11, RULE)
        self.assertEqual([c["online_cell"] for c in result["conflicts"]], ["E18"])
        self.assertNotIn("E18", {k for _, cells in fake.writes for k, _ in cells})
        self.assertEqual(fake.snap["cells"]["E18"]["v"], "写前最后一刻被人改了")

    def unknown_state(self, name="ev"):
        fake = FakeOnline(copy.deepcopy(self.snap))
        fake.fail_write = True
        mos.apply_plan(copy.deepcopy(self.plan), fake, fake, self.dir / name, 2026, 11, RULE)
        return fake, self.dir / name

    def test_reconcile_binds_to_original_state(self):
        fake, ev = self.unknown_state()
        state_before = (ev / "apply-state.json").read_bytes()
        # 空 write 清单 / 无指纹的旧式状态 / 错计划：都不能解除
        empty = copy.deepcopy(self.plan)
        for i in empty["items"]:
            i["action"] = "no_change"
        empty["fingerprint"] = mos.plan_fingerprint(empty)
        with self.assertRaises(mos.SyncError) as ctx:
            mos.reconcile(empty, fake, ev)
        self.assertEqual(ctx.exception.code, "reconcile_plan_mismatch")
        other = copy.deepcopy(self.plan)
        other["items"][0]["local_value"] = "别的"
        with self.assertRaises(mos.SyncError):
            mos.reconcile(other, fake, ev)
        self.assertEqual((ev / "apply-state.json").read_bytes(), state_before)
        # 对账到位才解除
        self.assertTrue(mos.reconcile(self.plan, fake, ev)["state_cleared"])
        self.assertEqual(mos.read_json(ev / "apply-state.json")["status"], "done")

    def test_reconcile_partial_keeps_state_and_wrong_document_is_refused(self):
        fake, ev = self.unknown_state()
        fake.snap["cells"]["E17"] = {"v": "没写成功的旧值", "f": None}
        state_before = (ev / "apply-state.json").read_bytes()
        report = mos.reconcile(self.plan, fake, ev)
        self.assertFalse(report["all_in_place"])
        self.assertEqual((ev / "apply-state.json").read_bytes(), state_before)
        other_doc = copy.deepcopy(fake)
        other_doc.snap["identity"]["url"] = "https://example.invalid/another"
        with self.assertRaises(mos.SyncError) as ctx:
            mos.reconcile(self.plan, other_doc, ev)
        self.assertEqual(ctx.exception.code, "reconcile_document_mismatch")
        self.assertEqual((ev / "apply-state.json").read_bytes(), state_before)

    def test_reconcile_without_or_with_corrupt_state(self):
        with self.assertRaises(mos.SyncError) as ctx:
            mos.reconcile(self.plan, FakeOnline(self.snap), self.dir / "none")
        self.assertEqual(ctx.exception.code, "no_apply_state")
        ev = self.dir / "bad"
        ev.mkdir()
        (ev / "apply-state.json").write_text("{坏的", encoding="utf-8")
        with self.assertRaises(mos.SyncError) as ctx:
            mos.reconcile(self.plan, FakeOnline(self.snap), ev)
        self.assertEqual(ctx.exception.code, "state_corrupt")
        fake = FakeOnline(copy.deepcopy(self.snap))
        with self.assertRaises(mos.SyncError) as ctx:
            mos.apply_plan(copy.deepcopy(self.plan), fake, fake, ev, 2026, 11, RULE)
        self.assertEqual(ctx.exception.code, "state_corrupt")
        self.assertEqual(fake.writes, [])
        self.assertEqual((ev / "apply-state.json").read_text(encoding="utf-8"), "{坏的")

    def test_main_requires_matching_source_period(self):
        renamed = self.dir / "月考核表.xlsx"
        renamed.write_bytes(self.src.read_bytes())
        snap_path = self.dir / "snap.json"
        mos.write_json(snap_path, self.snap)
        args = ["plan", "--source", str(renamed), "--year", "2026", "--month", "11", "--out", str(self.dir / "o1"), "--snapshot", str(snap_path)]
        self.assertEqual(mos.main(args), 2)  # 年月未知：阻断
        self.assertEqual(mos.main(args + ["--source-period", "2026-11"]), 0)
        self.assertEqual(mos.main(args[:6] + ["12"] + args[7:] + ["--source-period", "2026-11"]), 2)  # 声明与参数不符


if __name__ == "__main__":
    unittest.main()
