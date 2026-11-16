import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import staff_roster as roster

BRIGADES = ["梁溪大队", "锡山大队", "惠山大队", "滨湖大队", "新吴大队", "江阴大队", "宜兴大队", "经开大队"]


def build(path, groups, title="执法人员总表"):
    """groups: [(label, [names...])]; label cell is merged over the group's rows like the real roster."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.cell(1, 1, title)
    sheet.merge_cells("A1:B1")
    sheet.cell(2, 1, "大队")
    sheet.cell(2, 2, "姓名")
    row = 3
    for label, names in groups:
        start = row
        for name in names or [None]:
            sheet.cell(row, 2, name)
            sheet.cell(row, 3, "未上报" if row % 2 else None)  # column C must never count as a headcount
            row += 1
        sheet.cell(start, 1, label)
        if row - 1 > start:
            sheet.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
    book.save(path)


def clean_groups(sizes=None):
    sizes = sizes or {name: 3 for name in BRIGADES}
    return [(f"{name}{size}人", [f"{name[:2]}{i}" for i in range(size)]) for name, size in sizes.items()]


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "roster.xlsx"

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_are_actual_names_and_column_c_is_ignored(self):
        build(self.path, clean_groups({**{n: 3 for n in BRIGADES}, "江阴大队": 12}))
        result = roster.read_roster(self.path)
        self.assertEqual(result["counts"]["江阴大队"], 12)
        self.assertEqual(result["counts"]["梁溪大队"], 3)
        self.assertEqual((result["blockers"], result["warnings"]), ([], []))
        self.assertTrue(result["stable_during_read"])

    def test_label_number_mismatch_is_a_warning_and_name_count_wins(self):
        groups = clean_groups()
        groups[0] = ("梁溪大队9人", groups[0][1])  # label 9, names 3
        build(self.path, groups)
        result = roster.read_roster(self.path)
        self.assertEqual(result["counts"]["梁溪大队"], 3)
        self.assertEqual([w["code"] for w in result["warnings"]], ["staff_label_mismatch"])
        self.assertEqual(result["blockers"], [])

    def test_duplicate_names_block_only_that_brigade_and_no_value_is_returned(self):
        groups = clean_groups()
        groups[1] = (groups[1][0], ["张三", "张三", "李四"])
        build(self.path, groups)
        result = roster.read_roster(self.path)
        self.assertEqual([b["code"] for b in result["blockers"]], ["staff_roster_duplicate_names"])
        self.assertNotIn("锡山大队", result["counts"])
        self.assertIn("梁溪大队", result["counts"])

    def test_missing_brigade_and_empty_group_are_blockers_not_zero(self):
        groups = [g for g in clean_groups() if not g[0].startswith("经开")]
        groups[0] = (groups[0][0], [])
        build(self.path, groups)
        result = roster.read_roster(self.path)
        codes = sorted(b["code"] for b in result["blockers"])
        self.assertEqual(codes, ["staff_roster_brigade_missing", "staff_roster_no_names"])
        self.assertNotIn("经开大队", result["counts"])
        self.assertNotIn("梁溪大队", result["counts"])

    def test_group_outside_the_table_is_reported_but_never_counted(self):
        build(self.path, clean_groups() + [("轨道交通大队5人", ["甲", "乙", "丙", "丁", "戊"])])
        result = roster.read_roster(self.path)
        self.assertEqual(result["outside_table"], {"轨道交通大队": 5})
        self.assertNotIn("轨道交通大队", result["counts"])
        self.assertEqual(result["info"][0]["code"], "roster_outside_table")
        self.assertEqual(result["blockers"], [])

    def test_repeated_group_is_a_blocker(self):
        build(self.path, clean_groups() + [("梁溪大队2人", ["甲", "乙"])])
        result = roster.read_roster(self.path)
        self.assertIn("staff_roster_brigade_repeated", [b["code"] for b in result["blockers"]])
        self.assertNotIn("梁溪大队", result["counts"])

    def test_unreadable_or_missing_file_raises_instead_of_falling_back(self):
        with self.assertRaisesRegex(roster.RosterError, "不存在"):
            roster.read_roster(Path(self.tmp.name) / "none.xlsx")
        self.path.write_bytes(b"not a workbook")
        with self.assertRaisesRegex(roster.RosterError, "无法读取"):
            roster.read_roster(self.path)

    def test_wrong_sheet_name_raises(self):
        build(self.path, clean_groups())
        with self.assertRaisesRegex(roster.RosterError, "缺少工作表"):
            roster.read_roster(self.path, sheet="名册")

    def test_path_template_fills_year_and_maps_drive_on_posix(self):
        text = "D:\\知识库\\2、{yy}年大队\\{year}大队执法人员总表.xlsx"
        path = roster.resolve_path(text, 2026)
        if roster.os.name == "nt":
            self.assertTrue(str(path).endswith("2026大队执法人员总表.xlsx"))
        else:
            self.assertEqual(str(path), "/mnt/d/知识库/2、26年大队/2026大队执法人员总表.xlsx")

    def test_real_roster_when_available(self):
        real = roster.resolve_path(
            "D:\\工作备份\\移动硬盘\\1、工作\\9、知识库（物料，模板，依据文件，教学文件，名单清单）\\2、产品监督\\2、{yy}年大队执法人员收集\\执法人员信息\\{year}大队执法人员总表.xlsx", 2026)
        if not real.is_file():
            self.skipTest("2026 roster not present on this host")
        result = roster.read_roster(real)
        if result["sha256"] != "9D14C1A8A50FFE3119C8902CE3AB5F2316EC2DA3BACF6273AB1C7DB7096C8535":
            self.skipTest("roster was edited after the 2026-11-16 baseline")
        self.assertEqual(result["counts"], {"江阴大队": 12, "宜兴大队": 12, "梁溪大队": 8, "锡山大队": 11,
                                             "惠山大队": 11, "滨湖大队": 8, "新吴大队": 7, "经开大队": 6})
        self.assertEqual(sum(result["counts"].values()), 75)
        self.assertEqual(result["outside_table"], {"轨道交通大队": 5})


if __name__ == "__main__":
    unittest.main()
