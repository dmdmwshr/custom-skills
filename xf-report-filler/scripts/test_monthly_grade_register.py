import copy
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_grade_register as mgr


class MonthlyGradeRegisterMonthTests(unittest.TestCase):
    def test_resolve_score_month_prefers_filename(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\5月\2026年5月通报.doc",
            default_year=2026,
        )
        self.assertEqual(info["source"], "filename")
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 5))
        self.assertEqual(info["score_month_key"], "2026-05")

    def test_resolve_score_month_from_folder(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\5月\通报.doc",
            default_year=2026,
        )
        self.assertEqual(info["source"], "folder")
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 5))

    def test_resolve_score_month_falls_back_to_runtime_date(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\通报.doc",
            fallback_date=date(2026, 5, 21),
        )
        self.assertEqual(info["source"], "runtime")
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 5))

    def test_resolve_score_month_cross_year(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\1月\2026年1月通报.doc",
            default_year=2026,
        )
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 1))
        self.assertEqual(info["score_month_key"], "2026-01")

    def test_build_generated_history_record_uses_score_month(self):
        record = mgr.build_generated_history_record(
            2026,
            5,
            r"C:\workspace\5月\2026年5月通报.doc",
            "（五）联网监测",
            [],
            {},
        )
        self.assertEqual(record["month_key"], "2026-05")
        self.assertEqual((record["year"], record["month"]), (2026, 5))

    def test_migrate_monitor_history_records_keeps_existing_months(self):
        history = {
            "version": 1,
            "records": [
                {
                    "year": 2026,
                    "month": 3,
                    "month_key": "2026-03",
                    "report_file": r"C:\workspace\3月\2026年3月通报.doc",
                    "selected_cases": [],
                    "source": "scanned_existing",
                },
                {
                    "year": 2026,
                    "month": 4,
                    "month_key": "2026-04",
                    "report_file": r"C:\workspace\4月\2026年4月通报.doc",
                    "selected_cases": [],
                    "source": "scanned_existing",
                },
                {
                    "year": 2026,
                    "month": 5,
                    "month_key": "2026-05",
                    "report_file": r"C:\workspace\5月\2026年5月通报.doc",
                    "selected_cases": [],
                    "source": "generated",
                },
            ],
        }
        migrated, actions = mgr.migrate_monitor_history_records(history)
        self.assertEqual([item["month_key"] for item in migrated["records"]], ["2026-03", "2026-04", "2026-05"])
        self.assertEqual(actions, [])

    def test_migrate_monitor_history_records_raises_on_conflict(self):
        history = {
            "version": 1,
            "records": [
                {
                    "year": 2026,
                    "month": 5,
                    "month_key": "2026-05",
                    "report_file": r"C:\workspace\5月\2026年5月通报.doc",
                    "selected_cases": [],
                    "source": "generated",
                },
                {
                    "year": 2026,
                    "month": 5,
                    "month_key": "2026-05-copy",
                    "report_file": r"C:\workspace\5月\另一份2026年5月通报.doc",
                    "selected_cases": [],
                    "source": "generated",
                },
            ],
        }
        with self.assertRaises(mgr.HistoryMonthConflict):
            mgr.migrate_monitor_history_records(copy.deepcopy(history))

    def test_find_base_info_prefers_root_normal_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            network = base / "2026年5月联网监测基础信息考评明细表"
            network.mkdir()
            root_file = base / "2026年5月基础信息考评截图（不发）.xls"
            nested_file = network / "5月基础信息考评截图.xls"
            root_file.write_text("root", encoding="utf-8")
            nested_file.write_text("nested", encoding="utf-8")
            self.assertEqual(mgr.find_base_info(base, network), root_file)

    def test_find_base_info_keeps_pending_prefix_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            network = base / "2026年5月联网监测基础信息考评明细表"
            network.mkdir()
            root_file = base / "【待补】5月基础信息考评截图（不发）.xls"
            root_file.write_text("root", encoding="utf-8")
            self.assertEqual(mgr.find_base_info(base, network), root_file)

    def test_find_base_info_falls_back_to_network_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            network = base / "2026年5月联网监测基础信息考评明细表"
            network.mkdir()
            nested_file = network / "5月基础信息考评截图.xls"
            nested_file.write_text("nested", encoding="utf-8")
            self.assertEqual(mgr.find_base_info(base, network), nested_file)

    def test_parse_monitor_note_rejects_pseudo_contact(self):
        person, issues = mgr.parse_monitor_note("3202U01557  消防机构联系人、cad、pdf")
        self.assertEqual(person, "")
        self.assertIn("联系人", issues)

    def test_blank_product_template_item_is_skipped(self):
        self.assertTrue(mgr.is_blank_product_template_item("（）", "扣分："))
        self.assertTrue(mgr.is_blank_product_template_item(mgr.clean_error_line("3、（）"), "扣分："))
        self.assertFalse(mgr.is_blank_product_template_item("责令限期改正通知书填写不规范", "扣分：3.2 0.1分"))

    def test_validate_person_matches_allows_missing_monitor_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "个人执法统计表模板.xlsx"
            workbook = Workbook()
            ws = workbook.active
            ws.cell(6, 1).value = "宜兴大队"
            ws.cell(6, 2).value = "张三"
            workbook.save(template)

            mgr.validate_person_matches(
                template,
                [],
                [
                    {
                        "short": "宜兴",
                        "大队": "宜兴大队",
                        "联系人": "",
                        "单位": "测试单位",
                        "note": "3202U01557  消防机构联系人、cad、pdf",
                    }
                ],
            )

    def test_read_monitor_scores_recomputes_avg_over_10(self):
        class FakeCell:
            def __init__(self, value):
                self.value = value

        class FakeSheet:
            title = "Sheet1"
            max_row = 3

            def __init__(self):
                self.values = {(3, 1): "惠山"}
                for col in range(2, 12):
                    self.values[(3, col)] = 9.7
                self.values[(3, 12)] = 97.2

            def cell(self, row, col):
                return FakeCell(self.values.get((row, col)))

        class FakeWorkbook:
            active = FakeSheet()

        with patch.object(mgr, "load_xls_as_workbook", return_value=FakeWorkbook()):
            scores = mgr.read_monitor_scores(Path("联网监测统计表.xls"))

        self.assertEqual(scores["惠山"]["avg"], 9.7)
        self.assertEqual(mgr.read_monitor_scores.last_warnings[0]["raw_avg"], 97.2)


if __name__ == "__main__":
    unittest.main()
