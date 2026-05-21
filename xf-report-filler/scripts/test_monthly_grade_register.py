import copy
import sys
import unittest
from datetime import date
from pathlib import Path


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
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 4))
        self.assertEqual(info["score_month_key"], "2026-04")

    def test_resolve_score_month_from_folder(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\5月\通报.doc",
            default_year=2026,
        )
        self.assertEqual(info["source"], "folder")
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 4))

    def test_resolve_score_month_falls_back_to_runtime_date(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\通报.doc",
            fallback_date=date(2026, 5, 21),
        )
        self.assertEqual(info["source"], "runtime")
        self.assertEqual((info["score_year"], info["score_month"]), (2026, 4))

    def test_resolve_score_month_cross_year(self):
        info = mgr.resolve_score_month(
            r"C:\workspace\1月\2026年1月通报.doc",
            default_year=2026,
        )
        self.assertEqual((info["score_year"], info["score_month"]), (2025, 12))
        self.assertEqual(info["score_month_key"], "2025-12")

    def test_build_generated_history_record_uses_score_month(self):
        record = mgr.build_generated_history_record(
            2026,
            5,
            r"C:\workspace\5月\2026年5月通报.doc",
            "（五）联网监测",
            [],
            {},
        )
        self.assertEqual(record["month_key"], "2026-04")
        self.assertEqual((record["year"], record["month"]), (2026, 4))

    def test_migrate_monitor_history_records_shifts_existing_months(self):
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
        self.assertEqual([item["month_key"] for item in migrated["records"]], ["2026-02", "2026-03", "2026-04"])
        self.assertEqual(len(actions), 3)

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


if __name__ == "__main__":
    unittest.main()
