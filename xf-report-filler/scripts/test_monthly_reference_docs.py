import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_workflow as workflow


REQUIRED_OBJECT_HEADINGS = [
    "## 数据源解析",
    "## 生成规则",
    "## 不写入内容",
    "## Warning/Blocker",
    "## 验收点",
]

REQUIRED_MAINTENANCE_HEADINGS = [
    "## 变更分级",
    "## 渐进加载",
    "## 修改边界",
    "## 验证矩阵",
    "## 提交同步",
    "## 停止条件",
]


class MonthlyReferenceDocsTests(unittest.TestCase):
    def setUp(self):
        self.config = workflow.load_config()
        self.docs = workflow.reference_docs(self.config)

    def assert_doc_exists(self, value):
        path = workflow.reference_doc_path(value)
        self.assertTrue(path.exists(), f"reference doc missing: {value}")
        return path

    def test_reference_docs_cover_config_objects(self):
        for key in ["overview", "router", "directory_model", "template_strategy", "validation_and_audit"]:
            with self.subTest(section=key):
                self.assert_doc_exists(self.docs[key])

        for key in self.config["data_sources"]:
            with self.subTest(data_source=key):
                self.assertIn(key, self.docs["data_sources"])
                self.assert_doc_exists(self.docs["data_sources"][key])

        for item in self.config["templates"]:
            with self.subTest(template=item["id"]):
                self.assertIn(item["id"], self.docs["templates"])
                self.assert_doc_exists(self.docs["templates"][item["id"]])

        for item in self.config.get("excluded_templates", []):
            with self.subTest(excluded_template=item["id"]):
                self.assertIn(item["id"], self.docs["templates"])
                self.assert_doc_exists(self.docs["templates"][item["id"]])

        for item in self.config["bulletin_root_files"]:
            with self.subTest(root_file=item["id"]):
                self.assertIn(item["id"], self.docs["bulletin_root_files"])
                self.assert_doc_exists(self.docs["bulletin_root_files"][item["id"]])

        for item in self.config["grade_outputs"]:
            with self.subTest(grade_output=item["id"]):
                self.assertIn(item["id"], self.docs["grade_outputs"])
                self.assert_doc_exists(self.docs["grade_outputs"][item["id"]])

    def test_object_docs_have_required_structure(self):
        monthly_dir = workflow.SKILL_DIR / "references" / "monthly"
        object_docs = sorted(monthly_dir.glob("source_*.md")) + sorted(monthly_dir.glob("output_*.md"))
        self.assertGreaterEqual(len(object_docs), 10)
        for path in object_docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(doc=path.name):
                for heading in REQUIRED_OBJECT_HEADINGS:
                    self.assertIn(heading, text)

    def test_skill_entry_points_to_router_not_long_rule_dump(self):
        skill_text = (workflow.SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/monthly_workflow.md", skill_text)
        self.assertIn("references/monthly/00_workflow_router.md", skill_text)
        self.assertIn("references/skill_maintenance.md", skill_text)
        self.assertIn("渐进加载规则", skill_text)

    def test_skill_maintenance_doc_is_the_self_update_entry(self):
        maintenance_path = workflow.SKILL_DIR / "references" / "skill_maintenance.md"
        self.assertTrue(maintenance_path.exists())
        maintenance_text = maintenance_path.read_text(encoding="utf-8")
        for heading in REQUIRED_MAINTENANCE_HEADINGS:
            with self.subTest(heading=heading):
                self.assertIn(heading, maintenance_text)

        router_text = (workflow.SKILL_DIR / "references" / "monthly" / "00_workflow_router.md").read_text(encoding="utf-8")
        self.assertIn("../skill_maintenance.md", router_text)
        self.assertIn("规则变更或小改动", router_text)


if __name__ == "__main__":
    unittest.main()
