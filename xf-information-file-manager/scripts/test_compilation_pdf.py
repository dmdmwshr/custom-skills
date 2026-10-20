"""Business behavior and rollback tests for the optional live PDF book."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader
import compilation_pdf as book
import file_manager as fm


class BookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base/'business'; self.root.mkdir()
        for prefix in fm.MANAGED: (self.root/prefix).mkdir(parents=True)
        fixed = self.root/fm.COLLECTION/'5月固定版本'/'book.pdf'
        fixed.parent.mkdir(parents=True); fixed.write_bytes(b'fixed original')
        self.fixed = fm.protected(self.root)

    def tearDown(self): self.temp.cleanup()

    def document(self, id='D1', title='Test rule', page_count=2):
        source=self.root/(id+'.pdf'); canvas=Canvas(str(source),pagesize=(595.276,841.89))
        for i in range(page_count):
            canvas.drawString(60,700,id+' body '+str(i+1));canvas.showPage()
        canvas.save();sha=fm.digest(source)
        return dict(id=id,title=title,issuer='测试支队',level='支队',topic='管理',business=fm.BUSINESS[0],
                    date='2026-01-01',number='',archive_category=3,section=1,status='现行',included=True,
                    replaced_by=[],evidence='test',members=[dict(source=source.name,sha256=sha,role='main',name='',in_compilation=True)],
                    book_selection={'reviewed':True,'parts':[{'member_sha256':sha,'page_count':page_count,
                        'keep_ranges':[[1,page_count]],'excluded_ranges':[],'evidence':'已核对正文，无配套附件'}]})

    def manifest(self,*docs):
        return dict(schema_version=1,documents=list(docs),issues=[],changes=[],coverage_sources=[],compilation_pdf_required=True)

    def prepare(self,manifest,name):
        path=self.base/(name+'.json');fm.save(path,manifest)
        book.prepare(self.root,path,self.base/name,Path('unused'))
        return fm.load(path)

    def test_create_repeat_and_clickable_toc(self):
        candidate=self.prepare(self.manifest(self.document()),'initial')
        result=fm.apply(self.root,candidate)
        self.assertTrue(result['pdf_verified']);self.assertEqual(1,result['pdf_documents'])
        current=fm.load(self.root/fm.RECORDS[0]);record=current['compilation_pdf']
        reader=PdfReader(self.root/record['path'])
        self.assertEqual(3,len(reader.pages));self.assertTrue(reader.pages[0].get('/Annots'))
        self.assertFalse(fm.apply(self.root,current)['changed'])
        self.assertEqual(self.fixed,fm.protected(self.root))

    def test_reorder_update_keeps_old_pdf_in_transaction(self):
        current=self.prepare(self.manifest(self.document()),'initial')
        fm.apply(self.root,current);current=fm.load(self.root/fm.RECORDS[0]);old=current['compilation_pdf']
        current['documents'].append(self.document('D2','New rule'))
        # A later timestamp is deterministic here and prevents same-second name collisions.
        from datetime import datetime
        with patch.object(book,'datetime') as clock:
            clock.now.return_value=datetime(2026,11,1,12,0,0,tzinfo=book.CST)
            candidate=self.prepare(current,'update')
        result=fm.apply(self.root,candidate);saved=fm.load(self.root/fm.RECORDS[0])
        self.assertEqual(2,result['pdf_documents']);self.assertFalse((self.root/old['path']).exists())
        transaction=self.root/saved['transaction']
        self.assertEqual(old['sha256'],fm.digest(transaction/'before'/old['path']))
        self.assertEqual(old['sha256'],fm.digest(transaction/'replaced'/old['path']))

    def test_attachment_only_reuses_body_pdf(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);old=copy.deepcopy(current['compilation_pdf'])
        attachment=self.root/'form.docx';attachment.write_bytes(b'form-only attachment')
        current['documents'][0]['members'].append(dict(source=attachment.name,sha256=fm.digest(attachment),
            role='attachment',name=attachment.name,in_compilation=True))
        result=fm.apply(self.root,current);saved=fm.load(self.root/fm.RECORDS[0])
        self.assertEqual(old,saved['compilation_pdf']);self.assertTrue(result['changed'])
        self.assertIn('正文未变，沿用',(self.root/result['report']).read_text(encoding='utf-8-sig'))

    def test_template_revision_rebuilds_without_archive_changes(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);old=current['compilation_pdf']
        from datetime import datetime
        with patch.object(book,'PROFILE','test-new-template'), patch.object(book,'datetime') as clock:
            clock.now.return_value=datetime(2026,11,3,12,0,0,tzinfo=book.CST)
            candidate=self.prepare(current,'new-template')
            result=fm.apply(self.root,candidate)
            saved=fm.load(self.root/fm.RECORDS[0])
            self.assertTrue(result['changed']);self.assertNotEqual(old['input_fingerprint'],saved['compilation_pdf']['input_fingerprint'])
            self.assertTrue(fm.check(self.root,saved,self.fixed)['pdf_verified'])

    def test_cancellation_and_replacement_need_fresh_pdf(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document('OLD'),self.document('KEEP','Keep rule')),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);current['documents'][0].update(included=False,status='已替代',replaced_by=['NEW'])
        current['documents'].append(self.document('NEW','Replacement'))
        with self.assertRaisesRegex(ValueError,'prepare-book'): fm.apply(self.root,current)
        current['documents'][-1].pop('book_selection')
        with self.assertRaisesRegex(ValueError,'正文选取'): book.fingerprint(fm.layout(self.root,current)[0])
        current=fm.load(self.root/fm.RECORDS[0]);current['documents'][0]['included']=False
        with self.assertRaisesRegex(ValueError,'prepare-book'): fm.apply(self.root,current)

    def test_internal_forms_excluded_core_table_retained(self):
        doc=self.document(page_count=4)
        part=doc['book_selection']['parts'][0]
        part.update(keep_ranges=[[1,2],[4,4]],excluded_ranges=[{'ranges':[[3,3]],'reason':'填报表'}])
        candidate=self.prepare(self.manifest(doc),'selection');fm.apply(self.root,candidate)
        record=fm.load(self.root/fm.RECORDS[0])['compilation_pdf'];reader=PdfReader(self.root/record['path'])
        text='\n'.join(p.extract_text() or '' for p in reader.pages)
        self.assertIn('D1 body 4',text);self.assertNotIn('D1 body 3',text)
        self.assertEqual(3,record['documents'][0]['body_page_count'])

    def test_conversion_failure_does_not_commit(self):
        candidate=self.manifest(self.document())
        path=self.base/'candidate.json';fm.save(path,candidate);before=fm.scan(self.root,fm.MANAGED)
        with patch.object(book,'render_member',side_effect=RuntimeError('conversion failed')):
            with self.assertRaisesRegex(RuntimeError,'conversion failed'):
                book.prepare(self.root,path,self.base/'failure',Path('unused'))
        self.assertFalse((self.root/fm.RECORDS[0]).exists())
        self.assertEqual(before,fm.scan(self.root,fm.MANAGED));self.assertTrue((self.base/'failure/build-failure.json').exists())

    def test_mixed_body_form_page_removes_hidden_form_text(self):
        doc=self.document(page_count=1);source=self.root/doc['members'][0]['source']
        canvas=Canvas(str(source),pagesize=(595.276,841.89))
        canvas.drawString(60,700,'Core body remains');canvas.drawString(60,100,'Excluded form must disappear');canvas.save()
        sha=fm.digest(source);doc['members'][0]['sha256']=sha
        doc['book_selection']['parts'][0].update(member_sha256=sha,crops={'1':[0,0.5,1,1]})
        fm.apply(self.root,self.prepare(self.manifest(doc),'mixed'))
        current=fm.load(self.root/fm.RECORDS[0]);reader=PdfReader(self.root/current['compilation_pdf']['path'])
        self.assertNotIn('Excluded form',reader.pages[-1].extract_text())
        self.assertTrue(list(reader.pages[-1].images))

    def test_pdf_corruption_and_disable_rejected(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);candidate=copy.deepcopy(current);candidate['compilation_pdf_required']=False
        with self.assertRaisesRegex(ValueError,'不得取消'): fm.apply(self.root,candidate)
        (self.root/current['compilation_pdf']['path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'实时PDF'): fm.check(self.root,current,self.fixed)

    def test_failure_after_pdf_publication_recovers_previous_pdf(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);old=current['compilation_pdf'];current['documents'][0]['title']='Renamed rule'
        from datetime import datetime
        with patch.object(book,'datetime') as clock:
            clock.now.return_value=datetime(2026,11,2,12,0,0,tzinfo=book.CST)
            candidate=self.prepare(current,'update')
        with patch.object(fm,'check',side_effect=ValueError('injected check failure')):
            with self.assertRaisesRegex(RuntimeError,'执行失败'): fm.apply(self.root,candidate)
        failed=[p for p in (self.root/fm.MGMT/'事务').iterdir() if fm.load(p/'transaction.json')['phase']=='failed'][0]
        fm.recover(self.root,failed.relative_to(self.root).as_posix())
        restored=fm.load(self.root/fm.RECORDS[0]);self.assertEqual(old,restored['compilation_pdf'])
        self.assertEqual(old['sha256'],fm.digest(self.root/old['path']));self.assertTrue(fm.check(self.root,restored,self.fixed)['pdf_verified'])


if __name__=='__main__': unittest.main()
