import copy,json,unittest
from pathlib import Path
import test_library as fixtures
import library as lib
import maintenance as m

class MaintenanceTests(unittest.TestCase):
    setUp=fixtures.LibraryTests.setUp
    tearDown=fixtures.LibraryTests.tearDown
    standard=fixtures.LibraryTests.standard
    product=fixtures.LibraryTests.product
    def test_missing_pdf_metadata_is_still_required(self):
        self.standard('GB 101-2029',pdf=False);self.product('a',['GB 101-2029']);lib.build(self.root,self.data)
        data=json.loads((self.root/lib.META).read_text('utf-8'));data['standards']['GB 101-2029'].update(verified=False,checked_at='',official_url='',status='未核实');lib.atomic_json(self.root/lib.META,data)
        self.assertTrue(lib.audit(self.root)['errors'])
        with self.assertRaises(ValueError):lib.build(self.root,data)

    def test_pending_closed_into_history_only_after_valid_pdf(self):
        self.standard('GB 102-2029');self.product('a',['GB 102-2029']);self.data['pending_research']=[dict(kind='缺PDF',standard='GB 102-2029',reason='旧失败断点')]
        lib.build(self.root,self.data)
        self.assertFalse(self.data['pending_research']);self.assertEqual(self.data['research_history'][0]['reason'],'旧失败断点')
        self.data['pending_research']=[dict(kind='缺PDF',standard='GB 102-2029')];lib.atomic_json(self.root/lib.META,self.data)
        self.assertTrue(any('仍挂' in e for e in lib.audit(self.root)['errors']))

    def test_scope_exit_preserves_pdf_and_excludes_current_totals(self):
        self.standard('GB 103-2029');self.product('a',['GB 103-2029']);lib.build(self.root,self.data)
        self.data['products'][0]['certification']=dict(in_scope=False,scope_checked_at='2030-06-01',scope_source='https://example.com/notice',reason='官方公告退出')
        result=lib.build(self.root,self.data);c=result['catalogs'][0]
        self.assertFalse(result['errors']);self.assertEqual(c['products'],0);self.assertEqual(c['source_products'],1);self.assertEqual(c['out_of_scope_products'],1);self.assertEqual(result['copies'],1)
        self.assertIn('已退出CCC范围',self.data['placements'][0]['path']);self.assertEqual(c['current_product']['versions'],0)
        html=(self.root/'01_A/产品与标准目录.html').read_text('utf-8');self.assertIn('历史条目保留',html)

    def test_plan_flags_due_without_inventing_current_status(self):
        self.standard('GB 104-2030','即将实施','2030-07-01',False);self.product('a',['GB 104-2030']);lib.build(self.root,self.data)
        result=m.plan(self.root,self.base/'work','2030-07-02')
        self.assertFalse(result['errors']);self.assertTrue(result['due_status_review'][0]['overdue']);self.assertFalse(result['online_verified']);self.assertEqual(self.data['standards']['GB 104-2030']['status'],'即将实施')

    def test_conversion_deadline_cannot_be_assumed_from_implementation(self):
        self.standard('GB 105-2029');self.product('a',['GB 105-2029']);self.data['products'][0]['certification']=dict(in_scope=True,scope_checked_at='2030-06-01',scope_source='https://example.com/rule',conversion_deadline='2030-07-01')
        with self.assertRaises(ValueError):lib.build(self.root,self.data)

    def test_safe_cleanup_never_deletes_failure_or_unique_copy(self):
        s=self.standard('GB 106-2029');self.product('a',['GB 106-2029']);lib.build(self.root,self.data)
        work=self.base/'work';work.mkdir();items=[]
        for name,active,recovery in [('eligible.pdf',False,False),('resume.pdf',True,False),('baseline.pdf',False,True)]:
            (work/name).write_bytes(Path(s['source_file']).read_bytes() if Path(s['source_file']).is_absolute() else (self.root/s['source_file']).read_bytes())
            items.append(dict(path=name,standard=s['standard'],sha256=s['sha256'],state='archived_verified',active=active,recovery_critical=recovery,resumable=False,archived_at='2030-01-01'))
        mf=work/'manifest.json';mf.write_text(json.dumps(items))
        dry=m.cleanup(self.root,work,mf,on_date='2030-06-01');self.assertEqual(len(dry['candidates']),1);self.assertTrue((work/'eligible.pdf').exists())
        done=m.cleanup(self.root,work,mf,True,'2030-06-01');self.assertEqual(done['deleted'],['eligible.pdf']);self.assertTrue((work/'resume.pdf').exists());self.assertTrue((work/'baseline.pdf').exists())
        self.assertEqual(m.cleanup(self.root,work,mf,True,'2030-06-01')['deleted'],[])

    def test_cleanup_escape_and_formal_overlap_rejected(self):
        self.standard('GB 107-2029');self.product('a',['GB 107-2029']);lib.build(self.root,self.data);work=self.base/'work';work.mkdir();mf=work/'manifest.json';mf.write_text(json.dumps([dict(path='../outside.pdf')]))
        (self.base/'outside.pdf').write_bytes(b'keep');m.cleanup(self.root,work,mf,True,'2030-06-01');self.assertTrue((self.base/'outside.pdf').exists())
        with self.assertRaises(ValueError):m.cleanup(self.root,self.root,mf,True)

    def test_offline_work_volume_returns_instead_of_looping(self):
        from unittest.mock import patch
        class OfflineRoot:
            calls=0
            def resolve(self):return self
            def exists(self):
                self.calls+=1
                if self.calls>3:raise AssertionError('无限循环')
                return False
            @property
            def parent(self):return self
            def __str__(self):return 'offline-volume'
        with patch.object(m,'Path',return_value=OfflineRoot()):result=m.resources('offline')
        self.assertFalse(result['downloads_allowed']);self.assertTrue(result['errors'])

    def test_plan_returns_diagnostic_for_escape_instead_of_crashing(self):
        self.standard('GB 108-2029');self.product('a',['GB 108-2029']);lib.build(self.root,self.data)
        self.data['placements'][0]['path']='../escape.pdf';lib.atomic_json(self.root/lib.META,self.data)
        self.assertTrue(m.plan(self.root,self.base/'work','2030-06-01')['errors'])

    def test_shared_active_and_withdrawn_links_do_not_cross_roles(self):
        self.standard('GB 109-2029');self.product('a',['GB 109-2029']);self.product('a',['GB 109-2029'])
        self.data['products'][1]['certification']=dict(in_scope=False,scope_checked_at='2030-06-01',scope_source='https://example.com/notice')
        result=lib.build(self.root,self.data);self.assertFalse(result['errors']);self.assertEqual(result['copies'],2)
        a,b=self.data['products'];self.assertTrue(set(a['local_files']).isdisjoint(b['local_files']));self.assertEqual(result['catalogs'][0]['current_pdf_coverage']['complete'],1)
        self.data['standards']['GB 109-2029']['status']='废止';result=lib.build(self.root,self.data)
        self.assertFalse(result['errors']);self.assertEqual(len({j['path'] for j in self.data['placements']}),2);self.assertIn('已退出CCC范围',b['local_files'][0])

    def test_scope_link_and_date_must_be_usable(self):
        self.standard('GB 110-2029');self.product('a',['GB 110-2029']);cert=dict(in_scope=False,scope_checked_at='not-a-date',scope_source='javascript:alert(1)');self.data['products'][0]['certification']=cert
        with self.assertRaises(ValueError):lib.build(self.root,self.data)

    def test_cleanup_bad_manifest_item_reports_error(self):
        self.standard('GB 111-2029');self.product('a',['GB 111-2029']);lib.build(self.root,self.data);work=self.base/'work';work.mkdir();mf=work/'manifest.json';mf.write_text('[42]')
        self.assertTrue(m.cleanup(self.root,work,mf)['errors'])

    def test_shared_reference_and_product_roles_have_distinct_links(self):
        self.standard('GB 112-2029');self.product('a',['GB 112-2029']);self.product('a',[]);self.data['products'][-1]['reference_ids']=['GB 112-2029']
        result=lib.build(self.root,self.data);self.assertFalse(result['errors']);self.assertEqual(result['copies'],2)
        a,b=self.data['products'];self.assertTrue(set(a['local_files']).isdisjoint(b['local_files']))

    def test_cleanup_bad_fields_are_preserved_with_receipt(self):
        self.standard('GB 113-2029');self.product('a',['GB 113-2029']);lib.build(self.root,self.data);work=self.base/'work';work.mkdir();(work/'keep.pdf').write_bytes(b'keep');mf=work/'manifest.json';mf.write_text(json.dumps([dict(path='keep.pdf',state='archived_verified',active=False,recovery_critical=False,resumable=False,archived_at=None),dict(path=42)]))
        result=m.cleanup(self.root,work,mf,True,'2030-06-01');self.assertEqual(len(result['preserved']),2);self.assertTrue((work/'keep.pdf').exists())

if __name__=='__main__':unittest.main()
