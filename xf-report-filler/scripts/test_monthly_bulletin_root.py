import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_bulletin_root as root


class MonthlyBulletinRootTests(unittest.TestCase):
    def test_previous_month_window_uses_26_to_25(self):
        self.assertEqual(root.previous_month_window(2026, 6), (date(2026, 5, 26), date(2026, 6, 25)))
        self.assertEqual(root.previous_month_window(2026, 1), (date(2025, 12, 26), date(2026, 1, 25)))

    def test_pending_root_names_use_prefix(self):
        paths = root.root_file_paths(Path(r"C:\work\6月通报"), 2026, 6)
        self.assertEqual(paths["office_record"]["pending"].name, "【待补】2026年6月科室月考核情况记录表.xlsx")
        self.assertEqual(paths["product_stats"]["pending"].name, "【待补】2026年6月消防产品监督统计表.xls")
        self.assertEqual(paths["work_report"]["pending"].name, "【待补】2026年6月重点工作完成情况上报表（应急通信与消防科技）.xls")

    def test_staff_count_and_pending_value(self):
        self.assertEqual(root.normalize_brigade("江阴大队12人"), "江阴大队")
        self.assertEqual(root.parse_staff_count("江阴大队12人"), 12)
        self.assertEqual(root.parse_staff_count("经开大队6人"), 6)
        self.assertEqual(root.product_stats_pending_value(8), "（8）")

    def test_work_report_pending_cells_exclude_tech_baseline_column(self):
        cells = set(root.work_report_pending_cells())
        self.assertNotIn("J3", cells)
        self.assertIn("K3", cells)
        self.assertIn("O10", cells)
        self.assertIn("R10", cells)
        self.assertEqual(len(cells), 40)

    def test_timeliness_text_matches_may_style(self):
        text = root.product_timeliness_text(["6月底前完成1起消防产品行政处罚案件"], required_count=12, actual_count=7)
        self.assertEqual(
            text,
            "应完成但还未完成的：\n1、6月底前完成1起消防产品行政处罚案件；\n2、应完成12起案件实际完成7起。",
        )
        self.assertEqual(root.product_timeliness_text([]), "\\")

    def test_audit_product_stats_blocks_mapped_column_mismatch(self):
        stats = {}
        cases = {}
        for brigade in root.BRIGADE_ORDER:
            stats[brigade] = {
                "row": 1,
                "columns": {
                    "B": {"value": "1次（8）", "count": 1},
                    "D": {"value": "0份", "count": 0},
                    "E": {"value": "0份", "count": 0},
                    "F": {"value": "0次", "count": 0},
                    "G": {"value": "0份", "count": 0},
                },
            }
            cases[brigade] = {
                "unique_projects": 1,
                "unqualified_rows": 0,
                "sample_rows": 0,
                "micro_rows": 0,
                "administrative_case_year": 0,
            }
        stats["江阴大队"]["columns"]["B"] = {"value": "6次（12）", "count": 6}
        cases["江阴大队"]["unique_projects"] = 11
        result = root.audit_product_stats(stats, cases)
        blockers = result["blockers"]
        self.assertTrue(result["warnings"])
        self.assertTrue(any(item["message"] == "消防产品监督统计表与案卷数据不一致" and item["brigade"] == "江阴大队" for item in blockers))

    def test_mark_pending_office_marks_product_timeliness_row_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "office.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "1"
            ws.cell(2, 4).value = "梁溪大队"
            ws.cell(2, 11).value = "经开大队"
            ws.cell(15, 3).value = "产品工作实效"
            wb.save(path)

            changed = root.mark_pending_office(
                path,
                timeliness_by_brigade={
                    "梁溪大队": "应完成但还未完成的：\n1、应完成8起案件实际完成7起。",
                    "经开大队": "\\",
                },
            )
            wb2 = load_workbook(path)
            ws2 = wb2["1"]

            self.assertIn("应完成8起案件实际完成7起", ws2.cell(15, 4).value)
            self.assertEqual(ws2.cell(15, 11).value, "\\")
            self.assertEqual(ws2.cell(15, 4).fill.fgColor.rgb, "FFFF0000")
            self.assertEqual(ws2.cell(15, 4).font.color.rgb, "FF000000")
            self.assertEqual(len(changed), 2)


if __name__ == "__main__":
    unittest.main()
