import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_workflow as workflow
import template_resolver


class MonthlyWorkflowConfigTests(unittest.TestCase):
    def test_config_ids_are_unique(self):
        self.assertEqual(workflow.validate_unique_ids(), [])

    def test_config_contains_external_template_absolute_paths(self):
        config = workflow.load_config()
        self.assertTrue(Path(config["template_sources"]["external_root"]).is_absolute())
        self.assertTrue(Path(config["template_sources"]["bulletin_skeleton"]).is_absolute())
        self.assertTrue(Path(config["template_sources"]["score_skeleton"]).is_absolute())

    def test_template_resolver_prefers_external_when_hash_differs(self):
        item = {
            "id": "T99",
            "key": "demo",
            "file": "demo.xls",
            "description": "demo",
            "source": "score_skeleton",
            "used_in_monthly_register": True,
            "reserved": False,
        }
        config = workflow.load_config()
        with self.subTest("external wins"):
            with patch.object(workflow, "templates", return_value=[item]):
                with patch.object(workflow, "score_skeleton_dir") as score_skeleton, patch.object(workflow, "snapshot_template_path") as snapshot:
                    import tempfile

                    with tempfile.TemporaryDirectory() as tmp:
                        base = Path(tmp)
                        external_dir = base / "X月通报" / "上月巡查"
                        external_dir.mkdir(parents=True)
                        external = external_dir / "demo.xls"
                        snap = base / "snapshot" / "demo.xls"
                        snap.parent.mkdir()
                        external.write_bytes(b"external")
                        snap.write_bytes(b"snapshot")
                        score_skeleton.return_value = external_dir
                        snapshot.return_value = snap
                        result = template_resolver.resolve_templates(template_dir=base, include_reserved=False)

        self.assertEqual(result["templates"]["demo"], external)
        self.assertTrue(result["warnings"])
        self.assertFalse(result["blockers"])

    def test_template_resolver_blocks_external_missing_without_fallback(self):
        item = {
            "id": "T99",
            "key": "demo",
            "file": "demo.xls",
            "description": "demo",
            "source": "score_skeleton",
            "used_in_monthly_register": True,
            "reserved": False,
        }
        with patch.object(workflow, "templates", return_value=[item]):
            with patch.object(workflow, "score_skeleton_dir") as score_skeleton, patch.object(workflow, "snapshot_template_path") as snapshot:
                import tempfile

                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    external_dir = base / "X月通报" / "上月巡查"
                    external_dir.mkdir(parents=True)
                    snap = base / "snapshot" / "demo.xls"
                    snap.parent.mkdir()
                    snap.write_bytes(b"snapshot")
                    score_skeleton.return_value = external_dir
                    snapshot.return_value = snap
                    result = template_resolver.resolve_templates(template_dir=base, include_reserved=False)

        self.assertFalse(result["templates"])
        self.assertTrue(result["blockers"])


if __name__ == "__main__":
    unittest.main()
