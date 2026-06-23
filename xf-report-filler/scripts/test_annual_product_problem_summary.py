import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import annual_product_problem_summary as annual


def write_register(path, brigade="宜兴大队", error="卷内应有的某文书缺失（缺少审批表）", yellow=False, deduction=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph(f"大队：{brigade}")
    doc.add_paragraph("题名：测试案卷")
    doc.add_paragraph("编号：WX-001")
    doc.add_paragraph("立卷人：张三")
    doc.add_paragraph("错误")
    issue = doc.add_paragraph()
    run = issue.add_run(f"1、{error}")
    if yellow:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    if deduction:
        doc.add_paragraph("扣分：3.13 a 0.5分")
    doc.save(path)
    return path


def write_no_brigade_register(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph("题名：测试案卷")
    doc.add_paragraph("1、卷内应有的某文书缺失")
    doc.save(path)
    return path


class AnnualProductProblemSummaryTests(unittest.TestCase):
    def setUp(self):
        self.config = annual.load_config()

    def test_infers_month_from_score_folder_and_prefers_modified_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = write_register(root / "4月通报" / "3月巡查" / "0、（3月不发）产品巡查底册.docx")
            modified_path = write_register(root / "4月通报" / "3月巡查" / "0、（3月不发）（修改版本）产品巡查底册.docx")
            old_path.touch()
            modified_path.touch()

            review = annual.build_review(root, 2026, self.config)

            self.assertTrue(review["ok"])
            self.assertEqual(len(review["selected_sources"]), 1)
            self.assertEqual(Path(review["selected_sources"][0]["path"]).name, modified_path.name)
            self.assertEqual(review["selected_sources"][0]["month"], 3)
            self.assertEqual(len(review["skipped_sources"]), 1)
            self.assertEqual(Path(review["skipped_sources"][0]["path"]).name, old_path.name)

    def test_yellow_problem_is_included_and_public_text_strips_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_register(
                root / "3月通报" / "2月巡查" / "2、修改版本（2月不发）产品巡查底册.docx",
                error="复查现场照片不齐全（缺少门头远景）",
                yellow=True,
            )

            review = annual.build_review(root, 2026, self.config)

            self.assertTrue(review["ok"])
            self.assertEqual(len(review["entries"]), 1)
            entry = review["entries"][0]
            self.assertTrue(entry["yellow"])
            self.assertEqual(entry["public_description"], "复查现场照片不齐全")
            self.assertIn("缺少门头远景", entry["raw_error"])
            self.assertTrue(entry["review_required"])
            self.assertTrue(any(item.get("type") == "yellow_problem" for item in review["warnings"]))

    def test_simple_early_register_without_deduction_is_low_confidence_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_register(
                root / "3月通报" / "2月巡查" / "2月产品巡查底册.docx",
                error="消防产品监督检查记录填写不规范",
                deduction=False,
            )

            review = annual.build_review(root, 2026, self.config)

            self.assertTrue(review["ok"])
            self.assertEqual(len(review["entries"]), 1)
            self.assertTrue(review["entries"][0]["review_required"])
            self.assertIn("未匹配到扣分行", "；".join(review["entries"][0]["review_notes"]))

    def test_register_without_brigade_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_no_brigade_register(root / "3月通报" / "2月巡查" / "2月产品巡查底册.docx")

            review = annual.build_review(root, 2026, self.config)

            self.assertFalse(review["ok"])
            self.assertTrue(any("未识别到“大队：”分块" in item["message"] for item in review["blockers"]))

    def test_no_registers_is_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = annual.build_review(Path(tmp), 2026, self.config)

            self.assertFalse(review["ok"])
            self.assertTrue(any("未找到可采用的产品巡查底册" in item["message"] for item in review["blockers"]))

    def test_writes_docx_with_toc_headings_page_break_and_yellow_highlight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_register(
                root / "3月通报" / "2月巡查" / "2月产品巡查底册.docx",
                brigade="梁溪大队",
                error="复查现场照片不齐全（缺少门头远景）",
                yellow=True,
            )
            write_register(
                root / "4月通报" / "3月巡查" / "3月产品巡查底册.docx",
                brigade="锡山大队",
                error="缺授权委托书（缺授权委托书）",
            )
            review = annual.build_review(root, 2026, self.config)
            output = root / "2026年产品监督底册问题汇总.docx"

            annual.write_annual_doc(review["entries"], 2026, output, self.config, update_toc=False)

            self.assertTrue(output.exists())
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn('TOC \\o "1-2"', xml)
            self.assertIn('<w:br w:type="page"', xml)
            self.assertIn("<w:highlight", xml)
            self.assertIn("梁溪大队", xml)
            self.assertIn("锡山大队", xml)
            self.assertIn("复查现场照片不齐全", xml)
            self.assertNotIn("缺少门头远景", xml)

    def test_review_json_can_be_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_register(root / "3月通报" / "2月巡查" / "2月产品巡查底册.docx")
            review = annual.build_review(root, 2026, self.config)
            review_path = root / "review.json"

            annual.write_review_json(review_path, review)

            loaded = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["entries"][0]["raw_error"], "卷内应有的某文书缺失（缺少审批表）")


if __name__ == "__main__":
    unittest.main()
