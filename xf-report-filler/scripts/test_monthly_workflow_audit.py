import argparse
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_workflow as workflow
import monthly_workflow_audit as audit


def write_fake_template_tree(template_dir):
    config = workflow.load_config()
    skeleton = workflow.bulletin_skeleton_dir(config=config, template_dir=template_dir)
    skeleton.mkdir(parents=True)
    for item in workflow.templates(config):
        path = workflow.external_template_path(item, config=config, template_dir=template_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"template:{item['file']}".encode("utf-8"))
    for item in workflow.bulletin_root_files(config):
        (skeleton / item["skeleton_file"]).write_bytes(f"root:{item['skeleton_file']}".encode("utf-8"))


class MonthlyWorkflowAuditTests(unittest.TestCase):
    def test_audit_reports_paths_without_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "7月通报"
            score_dir = bulletin_dir / "6月巡查"
            network_dir = score_dir / "2026年6月联网监测基础信息考评明细表"
            network_dir.mkdir(parents=True)
            write_fake_template_tree(template_dir)
            (score_dir / "2026年6月产品巡查底册（不发）.docx").write_bytes(b"product")
            (score_dir / "2026年6月基础信息考评截图（不发）.xls").write_bytes(b"base")
            (network_dir / "联网监测统计表.xls").write_bytes(b"stats")
            work_plan = base / "2026年-产品、科技工作计划.xls"
            case_data = base / "产品案卷数据.xlsx"
            work_plan.write_bytes(b"work")
            case_data.write_bytes(b"case")

            result = audit.run(
                argparse.Namespace(
                    bulletin_dir=str(bulletin_dir),
                    bulletin_year=2026,
                    bulletin_month=7,
                    score_year=2026,
                    score_month=6,
                    template_dir=str(template_dir),
                    work_plan=str(work_plan),
                    case_data=str(case_data),
                )
            )

        self.assertTrue(result["ok"], result["blockers"])
        self.assertEqual(result["score"]["input_files"]["product_register"].split("\\")[-1], "2026年6月产品巡查底册（不发）.docx")
        self.assertIn("monthly_report", result["score"]["outputs"])


if __name__ == "__main__":
    unittest.main()
