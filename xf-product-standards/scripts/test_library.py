import copy,json,tempfile,unittest
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

if __name__=='__main__':unittest.main()
