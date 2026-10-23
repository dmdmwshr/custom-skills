"""Business behavior and rollback tests for the optional live PDF book."""
import copy
import json
import tempfile
import unittest
import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader
import compilation_pdf as book
import file_manager as fm
import word_pdf


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

    def test_date_only_and_same_day_revision_preserves_registered_previous_pdf(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);old=copy.deepcopy(current['compilation_pdf'])
        self.assertRegex(Path(old['path']).name,r'，\d{8}修订）\.pdf$')
        current['documents'][0]['title']='Same day renamed'
        updated=self.prepare(current,'same-day')
        self.assertEqual(old['path'],updated['compilation_pdf_candidate']['record']['path'])
        fm.apply(self.root,updated);saved=fm.load(self.root/fm.RECORDS[0]);transaction=self.root/saved['transaction']
        self.assertNotEqual(old['sha256'],saved['compilation_pdf']['sha256'])
        self.assertEqual(old['sha256'],fm.digest(transaction/'before'/old['path']))
        self.assertEqual(old['sha256'],fm.digest(transaction/'replaced'/old['path']))
        self.assertTrue(fm.check(self.root,saved,self.fixed)['pdf_verified'])

    def test_unknown_date_named_pdf_collision_is_not_overwritten(self):
        candidate=self.prepare(self.manifest(self.document()),'initial')
        target=self.root/candidate['compilation_pdf_candidate']['record']['path'];target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'user owned file')
        before=fm.scan(self.root,fm.MANAGED)
        with self.assertRaisesRegex(ValueError,'不是已登记'):fm.apply(self.root,candidate)
        self.assertEqual(b'user owned file',target.read_bytes());self.assertEqual(before,fm.scan(self.root,fm.MANAGED))

    def test_toc_uses_title_once_metadata_once_date_only_and_precise_links(self):
        doc=self.document(title='较长的规范标题'*12);doc['number']='测试〔2026〕1号';doc['short_title']='用户确认短名'
        result=self.prepare(self.manifest(doc),'toc-style');record=result['compilation_pdf_candidate']['record']
        reader=PdfReader(result['compilation_pdf_candidate']['source']);text=reader.pages[0].extract_text()
        self.assertEqual(1,text.count('用户确认短名'));self.assertEqual(1,text.count('测试〔2026〕1号'))
        self.assertEqual(1,text.count('[支队]'));self.assertNotRegex(text,r'\d{2}:\d{2}:\d{2}')
        self.assertIn('........',text);self.assertEqual(1,len(reader.pages[0]['/Annots']))
        link=reader.pages[0]['/Annots'][0].get_object();self.assertEqual(74,float(link['/Rect'][0]));self.assertEqual(record['toc_pages'],reader.get_destination_page_number(reader.outline[0]))

    def test_word_cleanup_preserves_text_tables_seals_and_layout_properties(self):
        source=self.base/'original.docx';target=self.base/'clean.docx'
        xml=b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body><w:p><w:r><w:rPr><w:highlight w:val="yellow"/><w:shadow/><w:color w:val="FF0000"/></w:rPr><w:t>Rule text</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:tcPr><w:shd w:fill="DEE0E3"/><w:tcW w:w="1000"/></w:tcPr><w:p><w:r><w:t>Normative table value 42</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr><a:effectLst><a:outerShdw/></a:effectLst></w:body></w:document>'''
        with zipfile.ZipFile(source,'w') as z:z.writestr('word/document.xml',xml);z.writestr('word/media/seal.png',b'original seal pixels')
        original_hash=fm.digest(source);counts=word_pdf.normalize(source,target)
        with zipfile.ZipFile(target) as z:
            result=z.read('word/document.xml');self.assertIn(b'Rule text',result);self.assertIn(b'Normative table value 42',result)
            self.assertIn(b'FF0000',result);self.assertIn(b'w:val="none"',result);self.assertIn(b'FFFFFF',result)
            self.assertNotIn(b'outerShdw',result);self.assertIn(b'w:pgSz w:w="11906" w:h="16838"',result)
            self.assertEqual(b'original seal pixels',z.read('word/media/seal.png'))
        self.assertEqual(original_hash,fm.digest(source));self.assertEqual(1,counts['highlight']);self.assertEqual(1,counts['drawing_effect'])

    def test_conversion_cache_reuse_and_corruption_detection(self):
        source=self.root/'source.docx';source.write_bytes(b'word original');member={'source':source.name,'sha256':fm.digest(source)}
        identity={'source_sha256':member['sha256'],'profile':word_pdf.PROFILE,'converter':{'test':'converter'}}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();folder=self.root/fm.MGMT/'PDF制作资料/转换缓存'/key;folder.mkdir(parents=True)
        (folder/'body.pdf').write_bytes(b'cached result');fm.save(folder/'conversion.json',{'identity':identity,'files':{'body.pdf':fm.digest(folder/'body.pdf')}})
        with patch.object(word_pdf,'converter_identity',return_value={'test':'converter'}),patch.object(word_pdf.subprocess,'run',side_effect=AssertionError('should reuse')):
            self.assertEqual(folder/'body.pdf',word_pdf.convert(self.root,member,self.base/'second-build',Path('unused')))
            (folder/'body.pdf').write_bytes(b'changed cached result')
            with self.assertRaisesRegex(ValueError,'缓存校验失败'):word_pdf.convert(self.root,member,self.base/'third-build',Path('unused'))

    def test_word_pdf_background_cleanup_keeps_text_borders_and_red_content(self):
        original=self.base/'word-export.pdf';clean=self.base/'white.pdf'
        canvas=Canvas(str(original),pagesize=(595.276,841.89))
        canvas.setFillColorRGB(1,1,0);canvas.rect(50,650,300,30,stroke=0,fill=1)
        canvas.setFillColorRGB(.85,.85,.85);canvas.rect(50,550,300,30,stroke=1,fill=1)
        canvas.setFillColorRGB(1,0,0);canvas.rect(50,750,300,2,stroke=0,fill=1)
        canvas.setFillColorRGB(0,0,0);canvas.drawString(60,660,'Full rule text remains');canvas.save()
        self.assertEqual(2,word_pdf.clean_pdf_backgrounds(original,clean))
        self.assertEqual(PdfReader(original).pages[0].extract_text(),PdfReader(clean).pages[0].extract_text())
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(str(clean)) as pdf:
            bitmap=pdf[0].render(scale=1);im=bitmap.to_pil()
            self.assertEqual((255,255,255),im.getpixel((55,170))[:3])
            red_pixels=sum(r>200 and g<25 and b<25 for r,g,b in im.convert('RGB').get_flattened_data())
            self.assertGreater(red_pixels,200);bitmap.close()

    def test_same_day_failure_recovers_exact_old_pdf(self):
        fm.apply(self.root,self.prepare(self.manifest(self.document()),'initial'))
        current=fm.load(self.root/fm.RECORDS[0]);old=copy.deepcopy(current['compilation_pdf']);current['documents'][0]['title']='Same day failing update'
        candidate=self.prepare(current,'failed-same-day')
        with patch.object(fm,'check',side_effect=ValueError('injected failure')):
            with self.assertRaisesRegex(RuntimeError,'执行失败'):fm.apply(self.root,candidate)
        failed=next(p for p in (self.root/fm.MGMT/'事务').iterdir() if fm.load(p/'transaction.json')['phase']=='failed')
        fm.recover(self.root,failed.relative_to(self.root).as_posix());restored=fm.load(self.root/fm.RECORDS[0])
        self.assertEqual(old,restored['compilation_pdf']);self.assertEqual(old['sha256'],fm.digest(self.root/old['path']))

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
