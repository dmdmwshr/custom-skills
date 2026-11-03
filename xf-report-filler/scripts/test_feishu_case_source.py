import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import feishu_case_source as source
import monthly_bulletin_root as root
import monthly_workflow as workflow


def write(directory, name, rows, **manifest):
    (directory / f"{name}.ndjson").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    if manifest is not None:
        meta = {"records_count": len(rows), "has_more": False, "rev": 1}
        meta.update(manifest)
        (directory / f"{name}.manifest.json").write_text(json.dumps(meta), encoding="utf-8")


class Builder:
    """Synthetic Feishu snapshot: cases, checks, products and monthly tables."""

    def __init__(self):
        self.cases, self.checks, self.products, self.monthly = [], [], [], []
        self.seq = 0

    def case(self, project, brigade="梁溪大队"):
        record = {"record_id": f"case-{project}", "项目编号": project, "所属大队": brigade, "初查结果": ["合格"]}
        self.cases.append(record)
        return record

    def check(self, project, stage, day, *, result="合格", micro="否", method="抽样送检",
              brigade="梁溪大队", products=1, month_field=None, failed_products=0):
        case = next(c for c in self.cases if c["项目编号"] == project)
        key = f"{project}|{stage}"
        record = {
            "record_id": f"check-{key}",
            "检查键": key,
            "阶段": [stage],
            "项目编号": [{"id": case["record_id"]}],
            "检查日期": f"{day}T00:00:00.000+08:00" if day else None,
            "检查结果": [result],
            "检查方式": [method],
            "大队": [brigade] if brigade else [],
            "所属大队(案卷)": brigade,
            "案卷是否微型消防站": micro,
            "产品数": products,
            "通报月份": month_field,
        }
        self.checks.append(record)
        for number in range(1, products + 1):
            self.products.append(
                {
                    "record_id": f"prod-{key}-{number}",
                    "产品键": f"{key}|{number}",
                    "检查键": [{"id": record["record_id"]}],
                    "检查结果": ["不合格" if number <= failed_products else "合格"],
                }
            )
        return record

    def month(self, label, brigade, **fields):
        self.monthly.append({"record_id": f"m-{label}-{brigade}", "月份大队": f"{label}|{brigade}",
                             "月份": label, "大队": [brigade], **fields})

    def save(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        write(directory, "cases", self.cases)
        write(directory, "checks", self.checks)
        write(directory, "products", self.products)
        write(directory, "monthly", self.monthly)
        return directory


def standard(builder=None):
    b = builder or Builder()
    for project in ("32002201C202600001", "32002201C202600002", "32002201C202600003",
                    "32002201C202600004", "32002201C202600005"):
        b.case(project)
    # Window for the January report: 2025-12-26 .. 2026-01-25 (crosses the year boundary).
    b.check("32002201C202600001", "初查", "2025-12-26", result="不合格", micro="是", products=3,
            failed_products=2)
    b.check("32002201C202600002", "初查", "2026-01-25", micro="待核实")
    b.check("32002201C202600003", "初查", "2025-12-25")  # one day before the window
    b.check("32002201C202600004", "初查", "2026-01-26")  # one day after the window
    b.check("32002201C202600001", "复查", "2026-01-10")  # recheck: excluded from first-inspection counts
    b.check("32002201C202600005", "初查", None)  # unknown date: neither counted nor zeroed
    return b


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def counts(self, builder, year=2026, month=1):
        builder.save(self.dir)
        snapshot = source.load_snapshot(self.dir)
        return source.case_counts(snapshot, year, month)

    def test_cross_year_window_excludes_rechecks_and_keeps_unknowns_separate(self):
        result = self.counts(standard())
        item = result["counts"]["梁溪大队"]
        self.assertEqual(result["window"], ["2025-12-26", "2026-01-25"])
        self.assertEqual(item["initial_checks"], 2)  # boundary days 12-26 and 01-25 are inclusive
        self.assertEqual(item["unique_projects"], 2)
        self.assertEqual(item["rechecks_excluded"], 1)
        self.assertEqual(item["date_unknown_initial_checks"], 1)  # not folded into zero or count
        self.assertEqual((item["micro_checks_confirmed"], item["micro_checks_unknown"]), (1, 1))
        self.assertEqual(item["unqualified_checks"], 1)
        self.assertTrue(
            any(g["type"] == "check_date_unknown" and g["checkKey"].endswith("00005|初查")
                for g in result["gaps"])
        )

    def test_check_product_and_project_grains_are_distinct(self):
        item = self.counts(standard())["counts"]["梁溪大队"]
        self.assertEqual(item["initial_checks"], 2)  # checks
        self.assertEqual(item["product_rows"], 4)  # product rows across both checks (3 + 1)
        self.assertEqual(item["unqualified_product_rows"], 2)
        self.assertEqual(item["unqualified_projects"], 1)
        self.assertNotIn("documents", item)  # document counts are never derived

    def test_multi_product_check_counts_once(self):
        b = Builder()
        b.case("32002201C202600001")
        b.check("32002201C202600001", "初查", "2026-03-01", products=5)
        item = self.counts(b, 2026, 3)["counts"]["梁溪大队"]
        self.assertEqual((item["initial_checks"], item["product_rows"]), (1, 5))

    def test_unknown_brigade_is_a_gap_and_not_attributed(self):
        b = Builder()
        b.case("32002201C202600001", brigade=None)
        b.check("32002201C202600001", "初查", "2026-03-01", brigade=None)
        result = self.counts(b, 2026, 3)
        self.assertTrue(any(g["type"] == "brigade_unknown" for g in result["gaps"]))
        self.assertTrue(any(g["type"] == "case_brigade_unknown" for g in result["gaps"]))
        self.assertEqual(sum(v["initial_checks"] for v in result["counts"].values()), 0)

    def test_declared_product_count_mismatch_is_reported(self):
        b = Builder()
        b.case("32002201C202600001")
        b.check("32002201C202600001", "初查", "2026-03-01", products=2)
        b.products.pop()
        result = self.counts(b, 2026, 3)
        self.assertTrue(any(g["type"] == "product_rows_mismatch" for g in result["gaps"]))

    def test_structural_errors_stop_instead_of_guessing(self):
        b = standard()
        b.save(self.dir)
        (self.dir / "checks.manifest.json").write_text(
            json.dumps({"records_count": 99, "has_more": False}), encoding="utf-8"
        )
        with self.assertRaisesRegex(source.SnapshotError, "与清单记录数"):
            source.load_snapshot(self.dir)
        write(self.dir, "checks", b.checks + [b.checks[0]])
        with self.assertRaisesRegex(source.SnapshotError, "主键 检查键 重复"):
            source.load_snapshot(self.dir)
        write(self.dir, "checks", b.checks)
        write(self.dir, "cases", b.products)  # wrong table in the cases slot
        with self.assertRaisesRegex(source.SnapshotError, "不是预期的cases表"):
            source.load_snapshot(self.dir)
        write(self.dir, "cases", b.cases)
        b.checks[0]["项目编号"] = [{"id": "missing"}]
        write(self.dir, "checks", b.checks)
        with self.assertRaisesRegex(source.SnapshotError, "未关联到案卷"):
            source.load_snapshot(self.dir)

    def test_missing_file_and_has_more_are_rejected(self):
        standard().save(self.dir)
        (self.dir / "monthly.ndjson").unlink()
        with self.assertRaisesRegex(source.SnapshotError, "缺少快照文件：monthly.ndjson"):
            source.load_snapshot(self.dir)

    def test_reported_values_are_read_only_references(self):
        b = standard()
        b.month("2026-01", "梁溪大队", **{"月报_检查次数": 3, "月报_责令限期改正通知书份数": 1,
                                         "月报_网售检查总次数": 2, "月报_网售不合格次数": 1})
        b.month("待核实", "梁溪大队", **{"月报_检查次数": 99})
        b.save(self.dir)
        reported = source.reported_values(source.load_snapshot(self.dir), 2026, 1)
        self.assertEqual(reported["梁溪大队"]["B"], [3])
        self.assertEqual(reported["梁溪大队"]["I"], [2, 1])
        self.assertEqual(set(reported), {"梁溪大队"})  # the undated '待核实' row is never used

    def test_real_snapshot_loads_when_available(self):
        real = Path("/srv/work/tasks/20261103-feishu-master/cloud-export")
        if not (real / "cases.ndjson").is_file():
            self.skipTest("frozen task snapshot not present on this host")
        snapshot = source.load_snapshot(real)
        result = source.case_counts(snapshot, 2026, 6)
        self.assertEqual(result["window"], ["2026-05-26", "2026-06-25"])
        gaps = {g["type"] for g in result["gaps"]}
        self.assertIn("check_date_unknown", gaps)  # the frozen export still lacks 22 dates
        self.assertEqual(sum(g["type"] == "check_date_unknown" for g in result["gaps"]), 22)
        self.assertEqual(sum(g["type"] == "brigade_unknown" for g in result["gaps"]), 2)


class AuditTests(unittest.TestCase):
    def stats(self, **columns):
        def row(**values):
            base = {c: {"value": "", "count": None} for c in "BCDEFGHIJK"}
            for column, count in values.items():
                base[column] = {"value": f"{count}次", "count": count}
            return {"row": 1, "columns": base}

        return {b: row(**columns.get(b, {})) for b in root.BRIGADE_ORDER}

    def counts(self, **fields):
        base = {"initial_checks": 2, "date_unknown_initial_checks": 1,
                "micro_checks_confirmed": 1, "micro_checks_unknown": 1,
                "unresolved_initial_cases": 4}
        base.update(fields)
        return {b: dict(base) for b in root.BRIGADE_ORDER}

    def types(self, result, kind):
        return [w for w in result["warnings"] if w.get("type") == kind]

    def test_incomplete_coverage_never_creates_an_upper_bound(self):
        stats = self.stats(**{b: {"B": 11, "F": 5} for b in root.BRIGADE_ORDER})
        result = root.audit_product_stats(stats, self.counts())
        self.assertFalse(self.types(result, "product_stats_mismatch"))
        pending = self.types(result, "product_stats_coverage_pending")
        self.assertEqual(len(pending), 16)
        self.assertEqual(pending[0]["unresolved_cases"], 4)

    def test_trustworthy_lower_bound_conflict_is_separate_and_unfilled_reported(self):
        stats = self.stats(梁溪大队={"B": 1, "F": 0})
        result = root.audit_product_stats(stats, self.counts())
        conflicts = [w for w in self.types(result, "product_stats_lower_bound_conflict")
                     if w["brigade"] == "梁溪大队"]
        self.assertEqual({w["column"] for w in conflicts}, {"B", "F"})
        self.assertEqual(conflicts[0]["case_count"], 2)
        self.assertFalse(self.types(result, "product_stats_mismatch"))
        unfilled = [w for w in self.types(result, "product_stats_unfilled") if w["brigade"] == "锡山大队"]
        self.assertEqual({w["column"] for w in unfilled}, {"B", "F"})

    def test_complete_coverage_keeps_the_upper_bound_mismatch(self):
        stats = self.stats(梁溪大队={"B": 3, "F": 1})
        counts = self.counts(unresolved_initial_cases=0, date_unknown_initial_checks=0,
                             micro_checks_unknown=0)
        result = root.audit_product_stats(stats, counts)
        mismatch = [w for w in self.types(result, "product_stats_mismatch") if w["brigade"] == "梁溪大队"]
        self.assertEqual([(w["column"], w["case_upper_bound"]) for w in mismatch], [("B", 2)])
        self.assertFalse(self.types(result, "product_stats_coverage_pending"))

    def test_document_columns_are_never_compared_to_case_proxies(self):
        stats = self.stats(**{b: {"C": 7, "D": 7, "E": 7, "G": 7} for b in root.BRIGADE_ORDER})
        counts = self.counts(unqualified_projects=0, unqualified_product_rows=0, sampling_checks=0)
        result = root.audit_product_stats(stats, counts)
        compared = {w.get("column") for w in result["warnings"]
                    if w.get("type", "").startswith("product_stats_") and "case_field" in w}
        self.assertFalse(compared & set("CDEG"))
        manual = {w["column"] for w in result["warnings"] if w.get("message") == "消防产品监督统计表列需人工核对"}
        self.assertEqual(manual, set("CDEGHIJK"))

    def test_snapshot_difference_is_a_warning_and_user_value_is_never_changed(self):
        stats = self.stats(梁溪大队={"B": 3})
        before = json.dumps(stats, sort_keys=True)
        result = root.audit_product_stats(stats, self.counts(), {"梁溪大队": {"B": [5], "I": [4, 1]}})
        differs = self.types(result, "reported_snapshot_differs")
        self.assertEqual([(w["column"], w["table_value"], w["snapshot_value"]) for w in differs],
                         [("B", 3, 5)])
        self.assertEqual(json.dumps(stats, sort_keys=True), before)


class EntryTests(unittest.TestCase):
    """The real final-audit and pending entry points read the snapshot, not a workbook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.snapshot = standard()
        self.snapshot.month("2026-01", "梁溪大队", **{"月报_检查次数": 2})
        self.snapshot.save(self.base / "snap")
        self.bulletin = self.base / "1月通报"
        self.bulletin.mkdir()
        name = root.root_file_name(next(i for i in root.ROOT_FILES if i["key"] == "product_stats"),
                                   2026, 1, pending=False)
        (self.bulletin / name).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def fake_stats(self):
        return TestStats.stats(**{"梁溪大队": {"B": 2, "F": 1}})

    def args(self, mode, case_data):
        return SimpleNamespace(mode=mode, apply=False, bulletin_dir=str(self.bulletin), year=2026, month=1,
                               work_plan=str(self.base / "plan.xls"), case_data=str(case_data),
                               template_dir=str(self.base / "tpl"), score_dir=None, score_month=1)

    def run_final(self, case_data):
        (self.base / "plan.xls").write_bytes(b"x")
        (self.base / "tpl").mkdir(exist_ok=True)
        with patch.object(root.workflow, "bulletin_skeleton_dir", return_value=self.base / "tpl"), \
                patch.object(root, "read_staff_counts", return_value={}), \
                patch.object(root, "read_product_stats", return_value=self.fake_stats()):
            return root.run(self.args("final-audit", case_data))

    def test_final_audit_reads_snapshot_and_reports_coverage(self):
        result = self.run_final(self.base / "snap")
        self.assertTrue(result["ok"], result["blockers"])
        kinds = {w.get("type") for w in result["warnings"]}
        self.assertIn("case_source_coverage", kinds)
        self.assertIn("case_coverage_gap", kinds)
        gap_types = {w.get("gap_type") for w in result["warnings"] if w.get("type") == "case_coverage_gap"}
        self.assertIn("check_date_unknown", gap_types)
        self.assertIn("product_stats_coverage_pending", kinds)
        self.assertEqual(result["case_window"], ["2025-12-26", "2026-01-25"])

    def test_unconfigured_snapshot_stops_with_a_clear_message(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("XF_FEISHU_SNAPSHOT")}
        env["HOME"] = str(self.base)
        (self.base / "plan.xls").write_bytes(b"x")
        (self.base / "tpl").mkdir(exist_ok=True)
        args = self.args("final-audit", "")
        args.case_data = None
        with patch.dict(os.environ, env, clear=True), \
                patch.object(root.workflow, "bulletin_skeleton_dir", return_value=self.base / "tpl"), \
                patch.object(root, "read_staff_counts", return_value={}), \
                patch.object(root, "read_product_stats", return_value=self.fake_stats()):
            result = root.run(args)
        self.assertFalse(result["ok"])
        self.assertTrue(any("未配置飞书统计快照目录" in b["message"] for b in result["blockers"]))

    def test_final_audit_blocks_on_broken_snapshot_instead_of_guessing(self):
        (self.base / "snap" / "checks.ndjson").unlink()
        result = self.run_final(self.base / "snap")
        self.assertFalse(result["ok"])
        self.assertTrue(any("飞书统计快照不可用" in b["message"] for b in result["blockers"]))

    def test_pending_mode_reads_snapshot_without_a_workbook(self):
        warnings, blockers, actions = [], [], []
        (self.base / "plan.xls").write_bytes(b"x")
        with patch.object(root, "read_work_plan_month_tasks", return_value={}), \
                patch.object(root, "read_product_stats", return_value=self.fake_stats()):
            root.mark_pending_cells(self.bulletin, 2026, 1, {}, self.base / "plan.xls",
                                    self.base / "snap", None, 1, actions, blockers, warnings, False)
        self.assertEqual(blockers, [])
        self.assertTrue(any(w.get("type") == "case_source_coverage" for w in warnings))
        self.assertTrue(any(a["status"] == "planned" for a in actions))

    def test_legacy_excel_path_still_dispatches_to_the_workbook_reader(self):
        workbook = self.base / "产品案卷数据.xlsx"
        workbook.write_bytes(b"x")
        with patch.object(root, "read_case_counts_excel", return_value={"梁溪大队": {}}) as legacy:
            result = root.read_case_source(workbook, 2026, 1)
        legacy.assert_called_once()
        self.assertEqual(result["coverage"]["kind"], "legacy_excel")


TestStats = AuditTests()

if __name__ == "__main__":
    unittest.main()


class FrozenSnapshotCoverageTests(unittest.TestCase):
    """Counts of the independent 26th-25th oracle stay unchanged; coverage never becomes a bound."""

    REAL = Path("/srv/work/tasks/20261103-feishu-master/cloud-export")

    def setUp(self):
        if not (self.REAL / "cases.ndjson").is_file():
            self.skipTest("frozen task snapshot not present on this host")
        self.snapshot = source.load_snapshot(self.REAL)

    def totals(self, month, field):
        counts = source.case_counts(self.snapshot, 2026, month)["counts"]
        return sum(item[field] for item in counts.values())

    def test_oracle_counts_are_unchanged(self):
        self.assertEqual([self.totals(m, "initial_checks") for m in (1, 6, 10)], [23, 95, 0])
        self.assertEqual([self.totals(m, "product_rows") for m in (1, 6, 10)], [33, 99, 0])
        self.assertEqual([self.totals(m, "rechecks_excluded") for m in (1, 6, 10)], [1, 8, 1])

    def test_case_coverage_is_reported_and_manual_october_values_are_pending_only(self):
        result = source.case_counts(self.snapshot, 2026, 10)
        self.assertEqual(result["coverage"]["cases_without_checks"], 305)
        self.assertEqual(result["coverage"]["cases_without_dated_initial"], 308)
        reported = source.reported_values(self.snapshot, 2026, 10)
        stats = {}
        for brigade, columns in reported.items():
            stats[brigade] = {"columns": {
                c: {"value": str(v[0]), "count": v[0]} for c, v in columns.items() if len(v) == 1
            }}
        audit = root.audit_product_stats(
            {b: stats.get(b, {"columns": {}}) for b in root.BRIGADE_ORDER}, result["counts"]
        )
        kinds = {w.get("type") for w in audit["warnings"]}
        self.assertNotIn("product_stats_mismatch", kinds)
        self.assertNotIn("product_stats_lower_bound_conflict", kinds)
        self.assertIn("product_stats_coverage_pending", kinds)
        self.assertEqual(stats["梁溪大队"]["columns"]["B"]["count"], 11)  # manual value is kept


class UnknownResultTests(unittest.TestCase):
    def counts(self, builder):
        return source.case_counts(
            source.load_snapshot(self._save(builder)), 2026, 3)

    def _save(self, builder):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        target = Path(self.tmp.name) / "snap"
        builder.save(target)
        return target

    def test_empty_result_is_explicit_unknown_not_a_certain_zero(self):
        b = Builder()
        b.case("32002201C202600001")
        b.case("32002201C202600002")
        known = b.check("32002201C202600001", "初查", "2026-03-01", result="不合格", failed_products=1)
        unknown = b.check("32002201C202600002", "初查", "2026-03-02")
        unknown["检查结果"] = []
        for row in b.products:
            if row["产品键"].startswith("32002201C202600002"):
                row["检查结果"] = []
        self.assertTrue(known)
        result = self.counts(b)
        item = result["counts"]["梁溪大队"]
        self.assertEqual(item["initial_checks"], 2)  # the unknown-result check still counts once
        self.assertEqual(item["unqualified_checks"], 1)  # known lower bound only
        self.assertEqual(item["result_unknown_checks"], 1)
        self.assertEqual(item["result_unknown_projects"], 1)
        self.assertEqual(item["product_result_unknown_rows"], 1)
        self.assertEqual([g["type"] for g in result["gaps"]], ["check_result_unknown"])

    def test_single_unknown_result_never_looks_like_zero_unqualified(self):
        b = Builder()
        b.case("32002201C202600003")
        check = b.check("32002201C202600003", "初查", "2026-03-05")
        check["检查结果"] = []
        item = self.counts(b)["counts"]["梁溪大队"]
        self.assertEqual((item["initial_checks"], item["unqualified_checks"]), (1, 0))
        self.assertEqual(item["result_unknown_checks"], 1)


class ProvenanceAndConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "snap"

    def tearDown(self):
        self.tmp.cleanup()

    def test_provenance_is_declared_or_explicitly_undeclared(self):
        standard().save(self.dir)
        snapshot = source.load_snapshot(self.dir)
        self.assertFalse(snapshot["provenance"]["declared"])
        (self.dir / "snapshot.info.json").write_text(
            json.dumps({"source": "飞书多维表只读导出", "exportedAt": "2026-11-03T08:00:00+08:00",
                        "completeness": "全部分页，has_more=false"}, ensure_ascii=False),
            encoding="utf-8")
        info = source.load_snapshot(self.dir)["provenance"]
        self.assertTrue(info["declared"])
        self.assertEqual(info["exportedAt"], "2026-11-03T08:00:00+08:00")
        summary = source.coverage_summary(source.load_snapshot(self.dir), [])
        self.assertEqual(summary["provenance"]["source"], "飞书多维表只读导出")

    def test_default_config_has_no_baked_in_path_and_unconfigured_is_clear(self):
        config = workflow.load_config()
        self.assertIsNone(config["data_sources"]["case_snapshot"]["path"])
        self.assertNotIn("/srv/work/tasks", json.dumps(config, ensure_ascii=False))
        env = {k: v for k, v in os.environ.items() if not k.startswith("XF_FEISHU_SNAPSHOT")}
        env["HOME"] = self.tmp.name
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(workflow.case_source_path(config))
            self.assertIn("未配置飞书统计快照目录", workflow.case_source_unconfigured_message(config))
            local = Path(self.tmp.name) / ".xf-report-filler"
            local.mkdir()
            (local / "feishu-snapshot.json").write_text(
                json.dumps({"snapshot_dir": str(self.dir)}), encoding="utf-8")
            self.assertEqual(workflow.case_source_path(config), self.dir)
        with patch.dict(os.environ, {**env, "XF_FEISHU_SNAPSHOT_DIR": "/tmp/explicit"}, clear=True):
            self.assertEqual(workflow.case_source_path(config), Path("/tmp/explicit"))
