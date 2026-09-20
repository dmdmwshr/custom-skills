import base64
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree
from review_docx import build, locate, source_fields, table_fields


def xml(node):
    return etree.tostring(node) if node is not None else None


class ReviewDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.docx'
        d = Document()
        p = d.add_paragraph('XXX项目技术需求分析报告论证会专家评审意见')
        p.paragraph_format.space_after = Pt(13)
        p.runs[0].font.size = Pt(22)
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        d.add_picture(BytesIO(image))
        t = d.add_table(rows=8, cols=2)
        for i in range(5):
            t.cell(i, 0).text = '某专家' if i < 4 else 'XXX'
            t.cell(i, 1).text = '某单位研究所所长/高工' if i < 4 else 'XXXXXXXXXXXXX'
        t.cell(7, 1).text = '2026年9月XX日'
        d.add_paragraph('2026年9月XX日下午组织召开评审会，形成意见如下：')
        for text in ['1、明确起算条件。', '2、建议核对考核口径。', '3、补充验收依据。']:
            p = d.add_paragraph(text)
            p.paragraph_format.line_spacing = Pt(28)
            p.paragraph_format.first_line_indent = Pt(32)
            p.runs[0].font.name = '方正仿宋_GBK'
            p.runs[0]._r.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '方正仿宋_GBK')
            p.runs[0].font.size = Pt(16)
        d.add_paragraph('用户已确定的评审结论。')
        d.add_paragraph('')
        d.add_paragraph('专家签字：')
        d.save(self.source)

    def test_preserves_package_drawings_styles_paragraphs_and_source(self):
        digest = hashlib.sha256(self.source.read_bytes()).digest()
        result = self.root/'out.docx'
        build(self.source,result)
        before,after = Document(self.source),Document(result)
        self.assertEqual(source_fields(before),source_fields(after))
        self.assertEqual(len(after.tables),1)
        for a,b in zip(before.paragraphs,after.paragraphs):
            self.assertEqual(xml(a._p.pPr),xml(b._p.pPr))
        self.assertEqual(xml(before._element.body.sectPr),xml(after._element.body.sectPr))
        self.assertEqual([xml(x) for x in before._element.xpath('.//w:drawing')],
                         [xml(x) for x in after._element.xpath('.//w:drawing')])
        with ZipFile(self.source) as a, ZipFile(result) as b:
            self.assertEqual(a.namelist(),b.namelist())
            for name in a.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(a.read(name),b.read(name),name)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(),digest)

    def test_content_and_layout_sources_are_separate(self):
        content = source_fields(Document(self.source))
        d = Document()
        d.add_paragraph(content['title'])
        for name,desc in content['experts']:
            d.add_paragraph(name+'\t'+desc)
        d.add_paragraph(content['introduction'])
        for i,text in enumerate(content['opinions'],1):
            d.add_paragraph(f'{i}、{text}')
        d.add_paragraph(content['conclusion'])
        d.add_paragraph('专家签字：')
        source = self.root/'tableless-content.docx'
        d.save(source)
        with self.assertRaises(ValueError):
            build(source,self.root/'invalid.docx')
        result = self.root/'repaired.docx'
        build(source,result,template=self.source)
        self.assertEqual(source_fields(Document(result)),content)
        self.assertEqual(locate(Document(result))[2][0].paragraph_format.line_spacing,Pt(28))

    def test_prefix_is_opt_in_and_idempotent(self):
        first,second = self.root/'one.docx',self.root/'two.docx'
        build(self.source,first,prefix_suggestions=True)
        build(first,second,prefix_suggestions=True)
        lines = source_fields(Document(second))
        self.assertEqual(lines['opinions'],['建议明确起算条件。','建议核对考核口径。','建议补充验收依据。'])
        self.assertEqual(lines['conclusion'],'用户已确定的评审结论。')

    def test_blank_only_highlights_unknown_fields(self):
        result = self.root/'blank.docx'
        build(self.source,result,blank=True)
        d = Document(result)
        fields = source_fields(d)
        self.assertEqual(fields['experts'],[['XXX','XXXXXXXXXXXXX']]*5)
        self.assertEqual(fields['conclusion'],'评审结论：XXX。')
        self.assertEqual(fields['opinions'],['建议XXX']*3)
        for run in d._element.iter(qn('w:r')):
            hi = run.xpath('./w:rPr/w:highlight')
            text = ''.join(run.xpath('./w:t/text()'))
            if hi:
                self.assertEqual(set(text),{'X'})
            if 'X' in text:
                self.assertTrue(hi)

    def test_only_requested_table_dimensions_change(self):
        result = self.root/'sized.docx'
        before = Document(self.source)
        build(self.source,result,name_width_twips=1280,spacer_total_twips=1401)
        after = Document(result)
        t,_,spacers,_ = table_fields(after)
        self.assertEqual(sum(g.w for g in before.tables[0]._tbl.tblGrid.gridCol_lst),
                         sum(g.w for g in t._tbl.tblGrid.gridCol_lst))
        self.assertEqual(t._tbl.tblGrid.gridCol_lst[0].w.twips,1280)
        self.assertEqual(sum(r.height.twips for r in spacers),1401)
        self.assertEqual(xml(locate(before)[1]._p.pPr),xml(locate(after)[1]._p.pPr))

    def test_rejects_existing_output_bad_content_and_extra_body(self):
        with self.assertRaises(ValueError):
            build(self.source,self.source)
        fields = source_fields(Document(self.source))
        for key,value in [('opinions',['建议仅一条']),('experts',[['XXX','XXX']]),('date','2027年9月XX日')]:
            bad = dict(fields);bad[key]=value
            target = self.root/(key+'.docx')
            with self.assertRaises(ValueError):
                build(self.source,target,content=bad)
            self.assertFalse(target.exists())
        d=Document(self.source);d.add_paragraph('不能悄悄丢失的用户备注');d.save(self.root/'extra.docx')
        with self.assertRaises(ValueError):
            build(self.root/'extra.docx',self.root/'dropped.docx')


if __name__ == '__main__':
    unittest.main()
