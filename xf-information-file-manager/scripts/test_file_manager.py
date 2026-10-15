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

    def test_readable_report_distinguishes_current_changes(self):
        d = self.document(); m = self.manifest(d); m['issues'] = ['D1：日期待核实']
        fm.apply(self.root, m)
        previous = fm.load(self.root / fm.RECORDS[0])
        issues = (self.root / fm.RECORDS[2]).read_text(encoding='utf-8-sig')
        self.assertIn('管理办法', issues); self.assertNotIn('D1：', issues)
        m = copy.deepcopy(previous)
        m['documents'][0].update(included=False, status='已替代', replaced_by=['D2'])
        m['documents'].append(self.document('D2', '新管理办法', 'new'))
        result = fm.apply(self.root, m)
        content = (self.root / result['report']).read_text(encoding='utf-8-sig')
        self.assertIn('第一部分——新增——[支队][管理] 新管理办法', content)
        self.assertIn('第一部分——[支队][管理] 管理办法', content)
        self.assertIn('已由[支队][管理] 新管理办法', content)
        self.assertIn('最新文件目录\n\n第一部分  消防内部文件', content)
        self.assertNotIn(fm.LIVE, content)
        self.assertNotIn('.doc', content)
        self.assertNotIn('日期待核实', content)
        toc = content.split('最新文件目录', 1)[1]
        self.assertEqual(1, len([line for line in toc.splitlines() if line.startswith('1、')]))

    def test_void_single_file_and_cancellation_are_different(self):
        d = self.document('OLD', '废止办法', 'old', included=False)
        d['status'] = '已作废'
        e = self.document('KEEP', '取消收录但未废止', 'keep', included=False)
        fm.apply(self.root, self.manifest(d, e))
        saved = fm.load(self.root / fm.RECORDS[0])
        old, keep = saved['documents']
        self.assertTrue(Path(old['members'][0]['archive_path']).name.startswith('【作废】'))
        self.assertFalse(Path(keep['members'][0]['archive_path']).name.startswith('【作废】'))
        self.assertEqual(d['members'][0]['sha256'], fm.digest(self.root / old['members'][0]['archive_path']))
        self.assertNotIn('【作废】', old['title'])
        self.assertFalse(fm.apply(self.root, saved)['changed'])

    def test_replaced_folder_marked_and_attachments_preserved(self):
        d = self.document('OLD', '旧办法', 'old', included=False)
        d.update(status='已替代', replaced_by=['NEW'])
        attachment = self.root / '附件1.docx'; attachment.write_bytes(b'keep attachment')
        d['members'].append(dict(source=attachment.name, sha256=fm.digest(attachment), role='attachment',
                                 name=attachment.name, in_compilation=False))
        new = self.document('NEW', '新办法', 'new')
        fm.apply(self.root, self.manifest(d, new))
        saved = fm.load(self.root / fm.RECORDS[0]); old, latest = saved['documents']
        a, b = [Path(m['archive_path']) for m in old['members']]
        self.assertTrue(a.parent.name.startswith('【作废】'))
        self.assertTrue(a.name.startswith('【作废】'))
        self.assertEqual(a.parent, b.parent)
        self.assertEqual('附件1.docx', b.name)
        self.assertEqual(b'keep attachment', (self.root / b).read_bytes())
        self.assertFalse(Path(latest['members'][0]['archive_path']).name.startswith('【作废】'))
        self.assertIsNone(old['members'][0]['compilation_path'])

    def test_void_mark_needs_evidence_and_replacement_target(self):
        d = self.document(included=False); d.update(status='已作废', evidence='')
        with self.assertRaisesRegex(ValueError, '明确依据'): fm.make_plan(self.root, self.manifest(d))
        d.update(status='已替代', evidence='用户明确说明')
        with self.assertRaisesRegex(ValueError, '关联新版本'): fm.make_plan(self.root, self.manifest(d))

    def test_protected_relocation_preserves_hashes_and_tracks_new_specials(self):
        fm.apply(self.root, self.manifest(self.document()))
        m = fm.load(self.root / fm.RECORDS[0])
        old_prefix = fm.COLLECTION + '/固定专项'; new_prefix = '9、专项/原固定专项'
        (self.root / new_prefix).parent.mkdir()
        (self.root / old_prefix).rename(self.root / new_prefix)
        new_file = self.root / '9、专项/另一个专项/资料.txt'
        new_file.parent.mkdir(); new_file.write_bytes(b'new special')
        m['protected_relocations'] = {old_prefix: new_prefix}
        fm.apply(self.root, m)
        saved = fm.load(self.root / fm.RECORDS[0])
        self.assertNotIn('protected_relocations', saved)
        self.assertIn(new_prefix + '/原件.txt', saved['protected_files'])
        self.assertIn(new_file.relative_to(self.root).as_posix(), saved['protected_files'])
        transaction = fm.load(self.root / saved['transaction'] / 'transaction.json')
        self.assertEqual({old_prefix: new_prefix}, transaction['protected_relocations'])
        self.assertFalse(fm.apply(self.root, saved)['changed'])

    def test_protected_relocation_does_not_accept_changed_or_lost_files(self):
        fm.apply(self.root, self.manifest(self.document()))
        m = fm.load(self.root / fm.RECORDS[0])
        old_prefix = fm.COLLECTION + '/固定专项'; new_prefix = '9、专项/原固定专项'
        (self.root / new_prefix).parent.mkdir()
        (self.root / old_prefix).rename(self.root / new_prefix)
        (self.root / new_prefix / '原件.txt').write_bytes(b'changed bytes')
        m['protected_relocations'] = {old_prefix: new_prefix}
        with self.assertRaisesRegex(ValueError, '内容变化'): fm.apply(self.root, m)
        self.assertEqual(b'changed bytes', (self.root / new_prefix / '原件.txt').read_bytes())

    def short_name_candidate(self, current):
        candidate = copy.deepcopy(current)
        candidate['documents'][0]['short_title'] = '国家局19号文'
        planned, _ = fm.layout(self.root, candidate)
        mappings = {}
        for old_doc, new_doc in zip(current['documents'], planned['documents']):
            for old, new in zip(old_doc['members'], new_doc['members']):
                for key in ('archive_path', 'compilation_path'):
                    if old.get(key) and old[key] != new[key]:
                        mappings[old[key]] = new[key]
                        if Path(old[key]).parent != Path(new[key]).parent:
                            mappings[Path(old[key]).parent.as_posix()] = Path(new[key]).parent.as_posix()
        candidate['snapshot_relocations'] = mappings
        return candidate

    def long_document(self):
        d = self.document(title='关于印发' + '综合性消防救援信息化建设管理' * 6 + '的通知')
        d['number'] = '消防〔2026〕19号'
        other = self.root / '另一个格式.pdf'; other.write_bytes(b'pdf alternative')
        d['members'].append(dict(source=other.name, sha256=fm.digest(other), role='main',
                                 name='', in_compilation=False))
        return d

    def test_short_title_preserves_official_metadata_and_future_names(self):
        d = self.long_document()
        fm.apply(self.root, self.manifest(d))
        current = fm.load(self.root / fm.RECORDS[0])
        candidate = self.short_name_candidate(current)
        result = fm.apply(self.root, candidate)
        saved = fm.load(self.root / fm.RECORDS[0]); latest = saved['documents'][0]
        self.assertEqual(d['title'], latest['title'])
        self.assertEqual(d['number'], latest['number'])
        self.assertEqual(d['id'], latest['id'])
        self.assertEqual('[支队][管理] 国家局19号文', latest['canonical_name'])
        self.assertNotIn('snapshot_relocations', saved)
        transaction = self.root / saved['transaction']
        record = fm.load(transaction / 'transaction.json')
        for part in ('before', 'replaced'):
            self.assertEqual(record['before'], fm.scan_snapshot(transaction / part, record['snapshot_relocations']))
            self.assertTrue(all(len(p.name.encode('utf-8')) <= 255 for p in (transaction / part).rglob('*')))
        self.assertTrue(fm.check(self.root, saved, self.frozen)['verified'])
        self.assertFalse(fm.apply(self.root, saved)['changed'])
        self.assertIn('国家局19号文', (self.root / result['report']).read_text(encoding='utf-8-sig'))

    def test_short_name_failure_recovers_logical_original_snapshot(self):
        fm.apply(self.root, self.manifest(self.long_document()))
        before = fm.scan(self.root, fm.MANAGED)
        records = fm.record_snapshot(self.root)
        candidate = self.short_name_candidate(fm.load(self.root / fm.RECORDS[0]))
        with patch.object(fm, 'txt', side_effect=OSError('injected record write failure')):
            with self.assertRaisesRegex(RuntimeError, '执行失败'):
                fm.apply(self.root, candidate)
        failed = [p for p in (self.root / fm.MGMT / '事务').iterdir()
                  if fm.load(p / 'transaction.json')['phase'] == 'failed'][0]
        self.assertTrue(fm.recover(self.root, failed.relative_to(self.root).as_posix())['recovered'])
        self.assertEqual(before, fm.scan(self.root, fm.MANAGED))
        self.assertEqual(records, fm.record_snapshot(self.root))
        self.assertTrue(fm.check(self.root, fm.load(self.root / fm.RECORDS[0]), self.frozen)['verified'])

    def test_committed_storage_migration_preserves_original_record_and_bytes(self):
        fm.apply(self.root, self.manifest(self.long_document()))
        current = fm.load(self.root / fm.RECORDS[0])
        current['documents'][0]['evidence'] += '；新增核对说明'
        fm.apply(self.root, current)
        current = fm.load(self.root / fm.RECORDS[0])
        rel = current['transaction']; transaction = self.root / rel
        old_record_hash = fm.digest(transaction / 'transaction.json')
        old_record = fm.load(transaction / 'transaction.json')
        mappings = self.short_name_candidate(current)['snapshot_relocations']
        result = fm.migrate_storage(self.root, rel, mappings)
        record = fm.load(transaction / 'transaction.json')
        self.assertTrue(result['changed'])
        self.assertEqual('committed', record['phase'])
        self.assertEqual(old_record['before'], record['before'])
        migration = fm.load(transaction / record['storage_migrations'][0])
        self.assertEqual(old_record_hash, fm.digest(transaction / migration['original_record']))
        for part in ('before', 'replaced'):
            self.assertEqual(old_record['before'], fm.scan_snapshot(transaction / part, mappings))
            self.assertTrue(all(len(p.name.encode('utf-8')) <= 255 for p in (transaction / part).rglob('*')))
        self.assertFalse(fm.migrate_storage(self.root, rel, mappings)['changed'])

    def test_snapshot_mapping_rejects_collisions_and_unrelated_targets(self):
        fm.apply(self.root, self.manifest(self.long_document()))
        current = fm.load(self.root / fm.RECORDS[0])
        candidate = self.short_name_candidate(current)
        paths = list(candidate['snapshot_relocations'])
        candidate['snapshot_relocations'][paths[0]] = '9、专项/不应移动.doc'
        with self.assertRaisesRegex(ValueError, '受管目录'):
            fm.make_plan(self.root, candidate)
        candidate = self.short_name_candidate(current)
        candidate['snapshot_relocations'][paths[0]] += '.other'
        with self.assertRaisesRegex(ValueError, '内容相同的新布局'):
            fm.make_plan(self.root, candidate)


if __name__ == '__main__': unittest.main()
