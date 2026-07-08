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
    for item in organizer.workflow.templates(organizer.CONFIG):
        path = organizer.workflow.external_template_path(item, config=organizer.CONFIG, template_dir=template_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"template:{item['file']}".encode("utf-8"))
    for item in organizer.workflow.bulletin_root_files(organizer.CONFIG):
        path = organizer.workflow.bulletin_skeleton_dir(config=organizer.CONFIG, template_dir=template_dir) / item["skeleton_file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"root:{item['skeleton_file']}".encode("utf-8"))


class MonthlyFileOrganizerTests(unittest.TestCase):
    def test_bulletin_dry_run_plans_june_root_files_and_wrong_office_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            score_dir = bulletin_dir / organizer.workflow.score_dir_name(5, organizer.CONFIG)
            score_dir.mkdir(parents=True)
            write_fake_templates(template_dir)
            wrong_office = score_dir / "2026年5月科室月考核情况记录表.xlsx"
            wrong_office.write_bytes(b"wrong")
            deprecated_report = bulletin_dir / "【待补】2026年6月重点工作完成情况上报表（应急通信与消防科技）.xls"
            deprecated_report.write_bytes(b"retired")

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
                    "6月消防产品工作动态（无锡） .doc",
                },
            )
            ensured_score_subdirs = {
                Path(action["path"]).name
                for action in result["actions"]
                if action["kind"].startswith("ensure_score_subdir_")
            }
            self.assertEqual(
                ensured_score_subdirs,
                {
                    "2026年5月错误照片留档",
                    "2026年5月联网监测基础信息考评明细表",
                    "2026年5月消防产品监督成绩",
                },
            )
            self.assertTrue(
                any(
                    action["kind"] == "delete_wrong_score_office_record"
                    and Path(action["path"]) == wrong_office
                    for action in result["actions"]
                )
            )
            self.assertTrue(
                any(
                    action["kind"] == "archive_deprecated_root_file"
                    and Path(action["src"]) == deprecated_report
                    and Path(action["dst"]).parent.name == "_停用文件归档"
                    for action in result["actions"]
                )
            )

    def test_bulletin_dry_run_preserves_existing_manual_root_file_with_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            (bulletin_dir / organizer.workflow.score_dir_name(5, organizer.CONFIG)).mkdir(parents=True)
            write_fake_templates(template_dir)
            existing = bulletin_dir / "2026年6月消防产品监督统计表.xls"
            existing.write_bytes(b"manual-result")

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
                    and action["status"] == "skip_existing_manual_file"
                    and Path(action["dst"]) == existing
                    for action in result["actions"]
                )
            )

    def test_bulletin_dry_run_skips_unprefixed_when_pending_root_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            (bulletin_dir / organizer.workflow.score_dir_name(5, organizer.CONFIG)).mkdir(parents=True)
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
