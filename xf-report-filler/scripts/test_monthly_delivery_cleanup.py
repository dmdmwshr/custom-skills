import tempfile
import unittest
from pathlib import Path
from monthly_delivery_cleanup import clean_delivery, digest


class DeliveryCleanupTests(unittest.TestCase):
    def run_cleanup(self, root, hashes=(), apply=False):
        return clean_delivery(root, 2026, 9, 2026, 8, hashes, {}, apply)

    def test_photo_merge_hash_check_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root/'8月巡查'/'YYYY年（X-1）月错误照片留档'
            old.mkdir(parents=True)
            photo = old/'照片.png'
            photo.write_bytes(b'actual-photo')
            target = root/'8月巡查'/'2026年8月错误照片留档'/'照片.png'
            actions, blockers = self.run_cleanup(root)
            self.assertFalse(blockers)
            self.assertTrue(photo.exists())
            self.assertFalse(target.exists())
            self.run_cleanup(root, apply=True)
            self.assertEqual(target.read_bytes(), b'actual-photo')
            self.assertFalse(old.exists())
            self.assertEqual(self.run_cleanup(root, apply=True), ([], []))

    def test_duplicate_and_template_deleted_modified_template_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root/'8月巡查'/'_模板副本归档'
            archive.mkdir(parents=True)
            blank = archive/'YYYY年（X-1）月通报.doc'
            blank.write_bytes(b'blank')
            filled = archive/'YYYY年（X-1）月底册.docx'
            filled.write_bytes(b'filled')
            target = archive.parent/'2026年8月底册.docx'
            target.write_bytes(b'filled')
            _, blockers = self.run_cleanup(root, [digest(blank)], True)
            self.assertFalse(blockers)
            self.assertFalse(archive.exists())
            self.assertEqual(target.read_bytes(), b'filled')

    def test_conflict_prevents_all_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root/'YYYY年（X-1）月通报.doc'
            old.write_bytes(b'changed')
            target = root/'2026年8月通报.doc'
            target.write_bytes(b'existing')
            _, blockers = self.run_cleanup(root, apply=True)
            self.assertTrue(blockers)
            self.assertEqual(old.read_bytes(), b'changed')
            self.assertEqual(target.read_bytes(), b'existing')

    def test_process_and_business_files_not_blindly_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ['_生成核验', '2026年8月错误照片留档']:
                (root/name).mkdir()
                (root/name/'keep.txt').write_text('keep',encoding='utf-8')
            self.assertEqual(self.run_cleanup(root, apply=True), ([], []))
            self.assertEqual(len(list(root.rglob('keep.txt'))),2)

    def test_root_placeholder_uses_bulletin_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'X月统计.xls').write_bytes(b'filled')
            _, blockers=clean_delivery(root,2026,9,2026,8,(),{'X月统计.xls':'{year}年{month}月统计.xls'},True)
            self.assertFalse(blockers)
            self.assertEqual((root/'2026年9月统计.xls').read_bytes(),b'filled')

    def test_two_placeholder_sources_conflicting_at_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'_模板副本归档').mkdir()
            (root/'YYYY年（X-1）月通报.doc').write_bytes(b'one')
            (root/'_模板副本归档'/'YYYY年（X-1）月通报.doc').write_bytes(b'two')
            _, blockers=self.run_cleanup(root,apply=True)
            self.assertTrue(blockers)
            self.assertEqual(len(list(root.rglob('*.doc'))),2)
