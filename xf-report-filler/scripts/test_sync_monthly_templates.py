import argparse
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_file_organizer as organizer
import sync_monthly_templates as syncer


class SyncMonthlyTemplatesTests(unittest.TestCase):
    def test_sync_prefers_numbered_template_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_dir = Path(tmp) / "模板文件"
            library = template_dir / "_编号模板库"
            library.mkdir(parents=True)
            for item in syncer.TEMPLATES:
                (library / item["file"]).write_bytes(f"template:{item['file']}".encode("utf-8"))
            (library / organizer.NUMBERED_TEMPLATE_NAMES[-1]).write_bytes(b"sample")

            result = syncer.run(
                argparse.Namespace(
                    dry_run=True,
                    apply=False,
                    template_dir=str(template_dir),
                )
            )

            self.assertTrue(result["ok"], result["blockers"])
            self.assertEqual(result["actions"][0]["src"], str(library / syncer.TEMPLATES[0]["file"]))

    def test_work_report_template_prefers_bulletin_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_dir = Path(tmp) / "模板文件"
            library = template_dir / "_编号模板库"
            skeleton_dir = template_dir / "X月通报"
            library.mkdir(parents=True)
            skeleton_dir.mkdir(parents=True)
            for item in syncer.TEMPLATES:
                (library / item["file"]).write_bytes(f"library:{item['file']}".encode("utf-8"))
            skeleton = skeleton_dir / syncer.WORK_REPORT_SKELETON
            skeleton.write_bytes(b"skeleton")

            result = syncer.run(
                argparse.Namespace(
                    dry_run=True,
                    apply=False,
                    template_dir=str(template_dir),
                )
            )

            work_report_action = next(action for action in result["actions"] if action["dst"].endswith(syncer.WORK_REPORT_NUMBERED))
            self.assertEqual(work_report_action["src"], str(skeleton))


if __name__ == "__main__":
    unittest.main()
