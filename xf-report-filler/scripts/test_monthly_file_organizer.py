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
    def test_bulletin_dry_run_plans_root_files_and_preserves_unverified_legacy_office(self):
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
                    action["kind"] == "preserve_unverified_score_office_record"
                    and Path(action["path"]) == wrong_office
                    for action in result["actions"]
                )
            )
            self.assertTrue(deprecated_report.exists())

    def run_fixture(self, base, apply):
        return organizer.run(argparse.Namespace(apply=apply, dry_run=not apply,
            bulletin_dir=str(base / '2610_通报'), bulletin_year=2026, bulletin_month=10,
            score_year=2026, score_month=9, template_dir=str(base / '模板')))

    def test_network_alias_dry_run_plans_both_directory_and_source_moves_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fake_templates(base / '模板')
            score = base / '2610_通报/9月巡查'
            legacy = score / '2026年9月联网检测基础信息考评明细表'
            canonical = score / '2026年9月联网监测基础信息考评明细表'
            legacy.mkdir(parents=True)
            canonical.mkdir()
            (legacy / '联网监测统计表.xls').write_bytes(b'stats')
            (legacy / '9月基础信息考评截图.xls').write_bytes(b'base')
            before = {str(path): path.read_bytes() for path in base.rglob('*') if path.is_file()}
            result = self.run_fixture(base, False)
            self.assertTrue(result['ok'], result['blockers'])
            self.assertTrue(any(a['kind'] == 'normalize_monitor_directory' and a['files'] == 2 for a in result['actions']))
            self.assertTrue(any(a['kind'] == 'score_base_info_move' and Path(a['src']).parent == legacy for a in result['actions']))
            self.assertEqual(before, {str(path): path.read_bytes() for path in base.rglob('*') if path.is_file()})

    def test_network_alias_apply_preserves_all_source_bytes_and_normalizes_the_base_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fake_templates(base / '模板')
            score = base / '2610_通报/9月巡查'
            legacy = score / '2026年9月联网检测基础信息考评明细表'
            canonical = score / '2026年9月联网监测基础信息考评明细表'
            legacy.mkdir(parents=True)
            canonical.mkdir()
            for name, value in [('联网监测统计表.xls', b'stats'), ('滨湖.doc', b'raw-doc'), ('9月基础信息考评截图.xls', b'base')]:
                (legacy / name).write_bytes(value)
            result = self.run_fixture(base, True)
            self.assertTrue(result['ok'], result['blockers'])
            self.assertFalse(legacy.exists())
            self.assertEqual((canonical / '联网监测统计表.xls').read_bytes(), b'stats')
            self.assertEqual((canonical / '滨湖.doc').read_bytes(), b'raw-doc')
            self.assertEqual((score / '2026年9月基础信息考评截图（不发）.xls').read_bytes(), b'base')

    def test_two_populated_network_directories_are_preserved_on_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fake_templates(base / '模板')
            score = base / '2610_通报/9月巡查'
            paths = [score / f'2026年9月联网{word}基础信息考评明细表' for word in ['监测', '检测']]
            for index, path in enumerate(paths):
                path.mkdir(parents=True)
                (path / '联网监测统计表.xls').write_bytes(str(index).encode())
            result = self.run_fixture(base, True)
            self.assertFalse(result['ok'])
            for index, path in enumerate(paths):
                self.assertEqual((path / '联网监测统计表.xls').read_bytes(), str(index).encode())

    def test_network_alias_with_an_office_lock_is_not_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fake_templates(base / '模板')
            legacy = base / '2610_通报/9月巡查/2026年9月联网检测基础信息考评明细表'
            legacy.mkdir(parents=True)
            (legacy / '~$联网监测统计表.xls').write_bytes(b'active-lock')
            result = self.run_fixture(base, True)
            self.assertFalse(result['ok'])
            self.assertTrue((legacy / '~$联网监测统计表.xls').exists())

    def test_only_hash_proven_unprefixed_duplicates_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fake_templates(base / '模板')
            bulletin = base / '2610_通报'
            bulletin.mkdir()
            item = next(x for x in organizer.BULLETIN_ROOT_TEMPLATE_MAP if x['id'] == 'R02')
            blank = organizer.skeleton_source_for(base / '模板', item).read_bytes()
            normal = bulletin / '2026年10月消防产品监督统计表.xls'
            pending = bulletin / ('【待补】' + normal.name)
            normal.write_bytes(blank)
            pending.write_bytes(b'pending-manual-values')
            result = self.run_fixture(base, True)
            self.assertTrue(result['ok'], result['blockers'])
            self.assertFalse(normal.exists())
            self.assertEqual(pending.read_bytes(), b'pending-manual-values')
            normal.write_bytes(b'different-manual-values')
            result = self.run_fixture(base, True)
            self.assertTrue(result['ok'], result['blockers'])
            self.assertEqual(normal.read_bytes(), b'different-manual-values')

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

    def test_bulletin_dry_run_deletes_verified_root_template_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template_dir = base / "模板文件"
            bulletin_dir = base / "26年" / "6月通报"
            (bulletin_dir / organizer.workflow.score_dir_name(5, organizer.CONFIG)).mkdir(parents=True)
            write_fake_templates(template_dir)
            root_template_copy = bulletin_dir / "X月消防产品监督统计表.xls"
            root_template_copy.write_bytes(
                (organizer.workflow.bulletin_skeleton_dir(config=organizer.CONFIG, template_dir=template_dir) / "X月消防产品监督统计表.xls").read_bytes()
            )

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
                    action["kind"] == "delete_verified_template_copy"
                    and Path(action["src"]) == root_template_copy
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
