import copy,json,tempfile,unittest,zipfile
from pathlib import Path
import fitz
import library as lib

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'library'
        self.data={'schema_version':1,'as_of':'2030-06-01','catalogs':[{'id':'a','folder':'01_A','title':'甲目录'},{'id':'b','folder':'02_B','title':'乙目录'}],'products':[],'standards':{}}
    def tearDown(self):self.tmp.cleanup()
    def standard(self,code,status='现行',date='2029-01-01',pdf=True):
        s=dict(standard=code,title='样例产品',status=status,implemented=date,checked_at='2030-06-01',official_url='https://std.samr.gov.cn/example',verified=True)
        if pdf:
            p=self.base/(code.replace('/','_')+'.pdf');doc=fitz.open();doc.new_page().insert_text((72,72),code);doc.save(p);doc.close();s['source_file']=str(p)
        self.data['standards'][code]=s
        return s
    def product(self,catalog,codes):
        self.data['products'].append(dict(id=str(len(self.data['products'])),catalog=catalog,category='产品分类',name='产品',standard_ids=codes,review_state='已匹配'))
    def test_future_coexists_and_idempotent(self):
        self.standard('XF 100-2029');self.standard('GB 100-2030','即将实施','2031-01-01')
        for c in ['a','b']:self.product(c,list(self.data['standards']))
        self.assertEqual(lib.build(self.root,self.data)['copies'],4)
        loaded=json.loads((self.root/lib.META).read_text('utf-8'))
        before={str(p.relative_to(self.root)):lib.digest(p) for p in self.root.rglob('*') if p.is_file()}
        self.assertFalse(lib.build(self.root,loaded)['errors'])
        self.assertEqual(before,{str(p.relative_to(self.root)):lib.digest(p) for p in self.root.rglob('*') if p.is_file()})
        self.assertTrue(any('03_现行行标' in x['path'] for x in loaded['placements']))
        self.assertTrue(any('02_待生效国标' in x['path'] for x in loaded['placements']))
    def test_partial_replacement_keeps_current_and_download_restriction(self):
        self.standard('GB 200-2020')['replacement_note']='部分被新标准代替；官网仍现行'
        self.standard('GB 201-2030',pdf=False)['download_note']='版权限制，仅在线阅读'
        self.product('a',['GB 200-2020','GB 201-2030'])
        r=lib.build(self.root,self.data);self.assertEqual(r['copies'],1)
        html=(self.root/'01_A/产品与标准目录.html').read_text('utf-8')
        self.assertIn('版权限制',html);self.assertIn('部分被新标准代替',html)
    def test_verified_obsolete_moves_to_history(self):
        self.standard('GB 200-2020');self.product('a',['GB 200-2020']);lib.build(self.root,self.data)
        old=self.root/self.data['placements'][0]['path']
        self.data['standards']['GB 200-2020']['status']='废止'
        lib.build(self.root,self.data)
        self.assertFalse(old.exists());self.assertIn('99_历史标准',self.data['placements'][0]['path'])
    def test_unknown_status_and_conflicting_dates_rejected(self):
        s=self.standard('GB 300-2030','即将实施','2029-01-01');self.product('a',['GB 300-2030'])
        with self.assertRaises(ValueError):lib.build(self.root,self.data)
        s.update(status='现行',verified=False)
        with self.assertRaises(ValueError):lib.build(self.root,self.data)
    def test_hash_conflict_no_overwrite_and_readonly_audit(self):
        self.standard('GB/T 400-2020');self.product('a',['GB/T 400-2020']);lib.build(self.root,self.data)
        p=self.root/self.data['placements'][0]['path'];self.assertIn('GB／T',p.name)
        before={str(f):f.stat().st_mtime_ns for f in self.root.rglob('*') if f.is_file()}
        lib.audit(self.root,True);self.assertEqual(before,{str(f):f.stat().st_mtime_ns for f in self.root.rglob('*') if f.is_file()})
        p.write_bytes(b'changed');self.assertTrue(lib.audit(self.root)['errors'])
        with self.assertRaises(ValueError):lib.build(self.root,self.data)
        self.assertEqual(p.read_bytes(),b'changed')
    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):lib.safe(self.root,'../outside')
    def test_future_reference_keeps_status_and_missing_count(self):
        self.standard('GB 501-2030','即将实施','2031-01-01')
        self.standard('GB 502-2030','即将实施','2031-01-01',pdf=False)
        self.product('a',[]);self.data['products'][0]['reference_ids']=['GB 501-2030','GB 502-2030']
        result=lib.build(self.root,self.data)
        self.assertFalse(result['errors']);self.assertEqual(result['copies'],1)
        self.assertIn('02_待生效国标',self.data['placements'][0]['path'])
        self.assertIn('已列编号但未取得PDF：1',(self.root/'目录导航.html').read_text('utf-8'))

    def test_zip_extract_and_relocate_are_independent_of_original(self):
        self.standard('GB/T 401-2029');self.product('a',['GB/T 401-2029'])
        lib.build(self.root,self.data)
        source=self.root/'01_A/目录 原件.html'
        source.write_text('<a href="产品与标准目录.html">目录</a>',encoding='utf-8')
        archive=self.base/'资料包.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for p in self.root.rglob('*'):
                if p.is_file():z.write(p,p.relative_to(self.root))
        relocated=self.base/'解压 后 空格'
        with zipfile.ZipFile(archive) as z:z.extractall(relocated)
        self.root.rename(self.base/'原位置已不存在')
        self.assertFalse(lib.audit(relocated,True)['errors'])
        self.assertTrue(lib.relocation_check(relocated)['relocation_verified'])

    def test_absolute_links_in_source_html_and_metadata_are_rejected(self):
        self.standard('GB 401-2029');self.product('a',['GB 401-2029'])
        lib.build(self.root,self.data)
        extra=self.root/'01_A/原件.html'
        for url in ['file:///C:/foo.pdf','C:/foo.pdf','/foo.pdf','http://localhost:8080/foo.pdf','//server/share','../%2e%2e/outside.pdf']:
            extra.write_text('<a href="'+url+'">坏链接</a>',encoding='utf-8')
            self.assertTrue(lib.audit(self.root,True)['errors'],url)
        extra.write_text('<base href="https://example.com/">',encoding='utf-8')
        self.assertTrue(lib.audit(self.root,True)['errors'])
        extra.write_text('<a href="https://std.samr.gov.cn/">官网</a>',encoding='utf-8')
        self.assertFalse(lib.audit(self.root,True)['errors'])
        data=json.loads((self.root/lib.META).read_text('utf-8'))
        data['standards']['GB 401-2029']['source_file']=str(self.root/data['placements'][0]['path'])
        lib.atomic_json(self.root/lib.META,data)
        self.assertTrue(lib.audit(self.root,True)['errors'])

    def test_coverage_counts_shared_versions_multivolume_and_reference_separately(self):
        for code in ['GB 601-2029','GB 602-2029','GB 603-2029']:self.standard(code)
        self.standard('GB 601-2030','即将实施','2031-01-01',pdf=False)
        self.product('a',['GB 601-2029','GB 601-2029','GB 601-2030'])
        self.product('a',['GB 601-2029','GB 602-2029'])
        self.data['products'][0]['reference_ids']=['GB 603-2029']
        result=lib.build(self.root,self.data);c=result['catalogs'][0]
        self.assertEqual(c['products'],2)
        self.assertEqual(c['current_product'],dict(versions=2,pdfs=2,missing_codes=[]))
        self.assertEqual(c['current_associations'],3)
        self.assertEqual(c['current_standards_shared_across_products'],1)
        self.assertEqual(c['current_standards_per_product'],{1:1,2:1})
        self.assertEqual(c['current_pdf_coverage']['complete'],2)
        self.assertEqual(c['future_product']['missing_codes'],['GB 601-2030'])
        self.assertEqual(c['reference']['pdfs'],1)

    def test_coverage_needs_valid_copy_in_product_catalog_and_category(self):
        self.standard('GB 701-2029');self.standard('GB 702-2029')
        for cat in ['a','b']:self.product(cat,['GB 701-2029'])
        self.product('a',['GB 701-2029','GB 702-2029'])
        self.data['products'][-1]['category']='另一分类'
        lib.build(self.root,self.data)
        for j in self.data['placements']:
            if j['catalog']=='a' and j['standard']=='GB 701-2029':
                (self.root/j['path']).write_bytes(b'broken')
        result=lib.audit(self.root,True);a,b=result['catalogs']
        self.assertTrue(result['errors'])
        self.assertEqual(a['current_pdf_coverage']['no_pdf'],1)
        self.assertEqual(a['current_pdf_coverage']['partial'],1)
        self.assertEqual(a['current_product']['pdfs'],1)
        self.assertEqual(b['current_pdf_coverage']['complete'],1)

    def test_pending_applicability_and_no_standard_are_not_complete(self):
        self.standard('GB 801-2029')
        self.product('a',['GB 801-2029'])
        self.data['products'][-1]['review_state']='仅列试验方法，专门产品标准待核实'
        self.product('a',[])
        self.data['products'][-1]['review_state']='本次检索未查到适用国标或行标'
        self.data['products'][-1]['reference_ids']=['GB 801-2029']
        result=lib.build(self.root,self.data);c=result['catalogs'][0]
        self.assertEqual(c['current_pdf_coverage']['complete'],0)
        self.assertEqual(c['current_pdf_coverage']['pending_applicability'],1)
        self.assertEqual(c['current_pdf_coverage']['no_current_standard'],1)
        self.assertEqual(c['no_standard_found_products'],1)

    def test_resume_link_is_available_only_for_missing_file(self):
        s=self.standard('GB 901-2029',pdf=False)
        s['resume_urls']=['https://example.com/standard/901']
        self.product('a',['GB 901-2029'])
        self.assertFalse(lib.build(self.root,self.data)['errors'])
        text=(self.root/'01_A/产品与标准目录.html').read_text('utf-8')
        self.assertIn('https://example.com/standard/901',text)

if __name__=='__main__':unittest.main()
