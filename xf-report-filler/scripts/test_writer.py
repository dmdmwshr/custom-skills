import types
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import writer
import reader
import batch_generate


class Tables:
    Count = 1

    def __call__(self, _):
        return types.SimpleNamespace(Range=types.SimpleNamespace(Cells=[]))


class WriterLayoutTests(unittest.TestCase):
    def fixture(self, fits_with_smaller_margins):
        trailing = types.SimpleNamespace(Text='\r', Font=types.SimpleNamespace(Size=12),
            ParagraphFormat=types.SimpleNamespace(), Information=lambda _: False)
        doc = types.SimpleNamespace(Content=types.SimpleNamespace(Text='all original business text'),
            Tables=Tables(), Paragraphs=types.SimpleNamespace(Last=types.SimpleNamespace(Range=trailing)),
            PageSetup=types.SimpleNamespace(TopMargin=40, BottomMargin=40), Repaginate=lambda: None)
        doc.ComputeStatistics = lambda _: 1 if fits_with_smaller_margins and doc.PageSetup.TopMargin < 40 else 2
        return doc, trailing

    def test_compacting_a_short_archive_keeps_text_and_bounds_margin_changes(self):
        doc, trailing = self.fixture(True)
        writer.compact_archive_layout(doc)
        self.assertEqual(doc.Content.Text, 'all original business text')
        self.assertEqual(trailing.Font.Size, 1)
        self.assertGreaterEqual(doc.PageSetup.TopMargin, 28.35)
        self.assertGreaterEqual(doc.PageSetup.BottomMargin, 28.35)
        self.assertEqual(doc.ComputeStatistics(2), 1)

    def test_long_archive_keeps_multiple_pages_and_restores_original_margins(self):
        doc, _ = self.fixture(False)
        writer.compact_archive_layout(doc)
        self.assertEqual((doc.PageSetup.TopMargin, doc.PageSetup.BottomMargin), (40, 40))
        self.assertEqual(doc.ComputeStatistics(2), 2)

    def tail_doc(self, pages_after_keep):
        class Para:
            KeepWithNext = False

        cells = [types.SimpleNamespace(RowIndex=row, Range=types.SimpleNamespace(ParagraphFormat=Para())) for row in (1, 12, 13, 14, 15, 16, 17) for _ in range(2)]
        doc = types.SimpleNamespace(
            Tables=lambda _: types.SimpleNamespace(Range=types.SimpleNamespace(Cells=cells)),
            Repaginate=lambda: None)
        doc.ComputeStatistics = lambda _: pages_after_keep if any(c.Range.ParagraphFormat.KeepWithNext for c in cells) else 2
        return doc, cells

    def test_tail_rows_are_kept_with_the_result_rows_without_adding_a_page(self):
        doc, cells = self.tail_doc(2)
        self.assertTrue(writer.keep_archive_tail_together(doc, 3))
        kept = sorted({c.RowIndex for c in cells if c.Range.ParagraphFormat.KeepWithNext})
        self.assertEqual(kept, [13, 14, 15, 16, 17][-5:])  # 3 item rows + 合计 + 得分
        self.assertFalse(any(c.Range.ParagraphFormat.KeepWithNext for c in cells if c.RowIndex <= 12))

    def test_tail_keep_is_reverted_when_it_would_add_a_page(self):
        doc, cells = self.tail_doc(3)
        self.assertFalse(writer.keep_archive_tail_together(doc, 3))
        self.assertFalse(any(c.Range.ParagraphFormat.KeepWithNext for c in cells))

    def test_a_generation_failure_is_reported_and_the_document_is_closed(self):
        doc = Mock()
        doc.Tables.side_effect = ValueError('missing expected table')
        word = Mock()
        word.Documents.Open.return_value = doc
        with self.assertRaisesRegex(RuntimeError, '保留已生成文件及失败断点'):
            writer.process_single_record(word, 'template.doc', 'output.doc', None, {}, {})
        doc.Close.assert_called_once_with(False)
        doc.SaveAs.assert_not_called()

    def test_word_returning_no_document_reports_the_real_cause(self):
        word = Mock()
        word.Documents.Open.return_value = None  # non-interactive (SSH/service) session
        with self.assertRaisesRegex(RuntimeError, '未能打开模板.*交互桌面会话'):
            writer.process_single_record(word, 'template.doc', 'output.doc', None, {}, {})

    def test_legacy_reader_preserves_original_and_existing_same_name_docx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, neighbor = root / '业务原件.doc', root / '业务原件.docx'
            source.write_bytes(b'original-doc')
            neighbor.write_bytes(b'protected-human-docx')
            word, doc = Mock(), Mock()
            def open_copy(path, **kwargs):
                self.assertNotEqual(Path(path), source)
                self.assertTrue(kwargs['ReadOnly'])
                self.assertEqual(Path(path).read_bytes(), b'original-doc')
                Path(path).write_bytes(b'converter-metadata')
                return doc
            word.Documents.Open.side_effect = open_copy
            doc.SaveAs2.side_effect = lambda path, **kwargs: Path(path).write_bytes(b'converted-docx')
            with patch('win32com.client.DispatchEx', return_value=word):
                result = Path(reader.convert_doc_to_docx(source))
            self.assertNotEqual(result, neighbor)
            self.assertEqual(result.read_bytes(), b'converted-docx')
            self.assertEqual(source.read_bytes(), b'original-doc')
            self.assertEqual(neighbor.read_bytes(), b'protected-human-docx')
            doc.Close.assert_called_once_with(False)
            word.Quit.assert_called_once()

    def test_legacy_batch_generation_does_not_suppress_errors(self):
        doc, word = Mock(), Mock()
        word.Documents.Open.return_value = doc
        doc.Tables.side_effect = ValueError('missing expected table')
        with self.assertRaisesRegex(RuntimeError, '旧版批量生成失败'):
            batch_generate.process_single_record(word, 'template.doc', 'output.doc', None, {}, {})
        doc.Close.assert_called_once_with(False)


if __name__ == '__main__':
    unittest.main()
