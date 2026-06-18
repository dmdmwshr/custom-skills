import argparse
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_file_organizer as organizer


def write_fake_templates(template_dir):
    library = template_dir / "_编号模板库"
    library.mkdir(parents=True)
    for name in organizer.NUMBERED_TEMPLATE_NAMES:
        (library / name).write_bytes(f"template:{name}".encode("utf-8"))


class MonthlyFileOrganizerTests(unittest.TestCase):
    def test_bulletin_dry_run_plans_june_root_files_and_wrong_office_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            score_dir = bulletin_dir / "5月巡查"
            score_dir.mkdir(parents=True)
            write_fake_templates(template_dir)
            wrong_office = score_dir / "2026年5月科室月考核情况记录表.xlsx"
            wrong_office.write_bytes(b"wrong")

            result = organizer.run(
                argparse.Namespace(
                    dry_run=True,
                    apply=False,
                    bulletin_dir=str(bulletin_dir),
                    bulletin_year=2026,
                    bulletin_month=6,
                    score_year=2026,
                    score_month=5,
                    template_dir=str(template_dir),
                )
            )

            self.assertTrue(result["ok"], result["blockers"])
            root_targets = {
                Path(action["dst"]).name
                for action in result["actions"]
                if action["kind"] == "copy_bulletin_root_file"
            }
            self.assertEqual(
                root_targets,
                {
                    "2026年6月科室月考核情况记录表.xlsx",
                    "2026年6月消防产品监督统计表.xls",
                    "2026年6月重点工作完成情况上报表（应急通信与消防科技）.xls",
                },
            )
            self.assertTrue(
                any(
                    action["kind"] == "delete_wrong_score_office_record"
                    and Path(action["path"]) == wrong_office
                    for action in result["actions"]
                )
            )

    def test_bulletin_dry_run_skips_unprefixed_when_pending_root_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            (bulletin_dir / "5月巡查").mkdir(parents=True)
            write_fake_templates(template_dir)
            (bulletin_dir / "【待补】2026年6月消防产品监督统计表.xls").write_bytes(b"pending")

            result = organizer.run(
                argparse.Namespace(
                    dry_run=True,
                    apply=False,
                    bulletin_dir=str(bulletin_dir),
                    bulletin_year=2026,
                    bulletin_month=6,
                    score_year=2026,
                    score_month=5,
                    template_dir=str(template_dir),
                )
            )

            self.assertTrue(result["ok"], result["blockers"])
            self.assertTrue(
                any(
                    action["kind"] == "copy_bulletin_root_file"
                    and action["status"] == "skip_pending_target_exists"
                    and action["pending"].endswith("【待补】2026年6月消防产品监督统计表.xls")
                    for action in result["actions"]
                )
            )


if __name__ == "__main__":
    unittest.main()
