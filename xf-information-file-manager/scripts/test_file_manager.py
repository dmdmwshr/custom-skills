import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import file_manager as fm


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for prefix in fm.MANAGED: (self.root / prefix).mkdir(parents=True)
        frozen = self.root / fm.COLLECTION / '固定专项' / '原件.txt'
        frozen.parent.mkdir(parents=True); frozen.write_text('固定', encoding='utf-8')
        self.frozen = fm.protected(self.root)

    def tearDown(self): self.temp.cleanup()

    def document(self, id='D1', title='管理办法', content='first', included=True):
        source = self.root / ('输入-' + id + '.doc')
        source.write_text(content, encoding='utf-8')
        return dict(id=id, title=title, issuer='测试支队', level='支队', topic='管理',
                    business=fm.BUSINESS[0], date='2026-01-01', number='', archive_category=3,
                    section=1, status='现行', included=included, replaced_by=[], evidence='测试',
                    members=[dict(source=source.name, sha256=fm.digest(source), role='main',
                                  name='', in_compilation=included)])

    def manifest(self, *docs):
        return dict(schema_version=1, documents=list(docs), issues=[], changes=[], coverage_sources=[])

    def test_new_and_repeat_no_new_version(self):
        m = self.manifest(self.document())
        result = fm.apply(self.root, m)
        self.assertTrue(result['changed'])
        self.assertFalse(fm.apply(self.root, m)['changed'])
        saved = fm.load(self.root / fm.RECORDS[0])
        self.assertFalse(fm.apply(self.root, saved)['changed'])
        self.assertEqual(1, len(list((self.root / fm.LIVE / '版本变更说明').glob('*.txt'))))
        self.assertEqual(self.frozen, fm.protected(self.root))

    def test_replace_preserves_history_and_renumbers_attachment(self):
        d = self.document()
        attachment = self.root / '附件1.docx'; attachment.write_bytes(b'attachment')
        d['members'].append(dict(source=attachment.name, sha256=fm.digest(attachment), role='attachment',
                                 name=attachment.name, in_compilation=True))
        fm.apply(self.root, self.manifest(d))
        m = fm.load(self.root / fm.RECORDS[0]); old_hash = d['members'][0]['sha256']
        m['documents'][0].update(included=False, status='已替代', replaced_by=['D2'])
        m['documents'].append(self.document('D2', '新管理办法', 'new'))
        result = fm.apply(self.root, m)
        saved = fm.load(self.root / fm.RECORDS[0])
        self.assertEqual(1, result['included'])
        self.assertIn(old_hash, fm.scan(self.root, fm.ARCHIVES).values())
        self.assertNotIn(old_hash, fm.scan(self.root, [fm.LIVE + '/' + fm.SECTIONS[0]]).values())
        self.assertEqual(1, saved['documents'][1]['sequence'])
        self.assertEqual(self.frozen, fm.protected(self.root))

    def test_attachment_renumber_and_business_date_order(self):
        d = self.document('A', '旧制度'); d['date'] = '2025-01-01'
        f = self.root / '附件.docx'; f.write_bytes(b'a')
        d['members'].append(dict(source=f.name, sha256=fm.digest(f), role='attachment', name=f.name, in_compilation=True))
        e = self.document('B', '新制度', 'new')
        fm.apply(self.root, self.manifest(d, e))
        saved = fm.load(self.root / fm.RECORDS[0]); a = saved['documents'][0]
        self.assertEqual(2, a['sequence'])
        main = Path(a['members'][0]['compilation_path'])
        self.assertEqual(main.stem, main.parent.name)
        self.assertEqual(main.parent, Path(a['members'][1]['compilation_path']).parent)

    def test_same_name_different_bytes_preserved(self):
        d = self.document(); other = self.root / '其他.doc'; other.write_bytes(b'different')
        d['members'].append(dict(source=other.name, sha256=fm.digest(other), role='main', name='', in_compilation=False))
        result = fm.apply(self.root, self.manifest(d))
        self.assertEqual(2, result['archive_files']); self.assertEqual(1, result['compilation_files'])
        self.assertEqual(2, len(set(fm.scan(self.root, fm.ARCHIVES).values())))

    def test_mutated_input_and_omitted_history_rejected(self):
        d = self.document(); m = self.manifest(d)
        (self.root / d['members'][0]['source']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '内容变化'): fm.make_plan(self.root, m)
        d = self.document(); fm.apply(self.root, self.manifest(d))
        with self.assertRaisesRegex(ValueError, '不得删除'): fm.make_plan(self.root, self.manifest())

    def test_outside_path_and_protected_change_rejected(self):
        with self.assertRaises(ValueError): fm.safe(self.root, '../outside')
        fm.apply(self.root, self.manifest(self.document()))
        p = self.root / fm.COLLECTION / '固定专项' / '原件.txt'; p.write_text('modified')
        m = fm.load(self.root / fm.RECORDS[0])
        with self.assertRaisesRegex(ValueError, '固定'): fm.apply(self.root, m)

    def test_unregistered_live_material_cannot_be_lost(self):
        p = self.root / fm.LIVE / fm.SECTIONS[0] / '未入账原件.doc'
        p.write_bytes(b'unregistered')
        with self.assertRaisesRegex(ValueError, '实时版内容'):
            fm.apply(self.root, self.manifest(self.document()))
        self.assertEqual(b'unregistered', p.read_bytes())

    def test_partial_failure_recover(self):
        before = fm.scan(self.root, fm.MANAGED)
        original = Path.rename; count = [0]
        def failing(p, target):
            if '/stage/' in p.as_posix():
                count[0] += 1
                if count[0] == 3: raise OSError('injected rename failure')
            return original(p, target)
        with patch.object(Path, 'rename', failing):
            with self.assertRaisesRegex(RuntimeError, '执行失败'):
                fm.apply(self.root, self.manifest(self.document()))
        transaction = next((self.root / fm.MGMT / '事务').iterdir())
        self.assertTrue(fm.recover(self.root, transaction.relative_to(self.root).as_posix())['recovered'])
        self.assertEqual(before, fm.scan(self.root, fm.MANAGED))
        self.assertEqual(self.frozen, fm.protected(self.root))
        self.assertFalse((self.root / fm.RECORDS[0]).exists())


if __name__ == '__main__': unittest.main()
