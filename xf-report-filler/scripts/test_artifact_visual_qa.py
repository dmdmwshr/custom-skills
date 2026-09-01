from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import artifact_visual_qa as qa


class ArtifactVisualQaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.source.write_bytes(b"source-v1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _inspection(self, *, checked_units: list[str], total_units: int) -> Path:
        path = self.root / "inspection.json"
        path.write_text(
            json.dumps(
                {
                    "renderer": "fixture-renderer",
                    "renderer_version": "1.0",
                    "total_units": total_units,
                    "checked_units": checked_units,
                    "checks": {"layout": "passed", "optional_objects": "not_applicable"},
                    "warnings": [],
                    "blockers": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def _baseline(self, *, artifact: Path, artifact_type: str, receipt: Path) -> None:
        qa.create_baseline(
            artifact_text=str(artifact),
            artifact_type=artifact_type,
            source_texts=[str(self.source)],
            receipt_text=str(receipt),
        )

    def test_docx_passes_and_verify_detects_no_drift(self) -> None:
        artifact = self.root / "result.docx"
        receipt = self.root / "result.visual-qa.json"
        self._baseline(artifact=artifact, artifact_type="docx", receipt=receipt)
        artifact.write_bytes(b"result-v1")
        result = qa.finalize_receipt(
            receipt_text=str(receipt),
            inspection_text=str(self._inspection(checked_units=["page-1", "page-2"], total_units=2)),
        )
        self.assertEqual(result["outcome"], "passed")
        self.assertTrue(result["sources_unchanged"])
        self.assertEqual(qa.verify_receipt(receipt_text=str(receipt))["outcome"], "passed")

    def test_source_change_is_blocked(self) -> None:
        artifact = self.root / "result.docx"
        receipt = self.root / "result.visual-qa.json"
        self._baseline(artifact=artifact, artifact_type="docx", receipt=receipt)
        artifact.write_bytes(b"result-v1")
        self.source.write_bytes(b"source-v2")
        result = qa.finalize_receipt(
            receipt_text=str(receipt),
            inspection_text=str(self._inspection(checked_units=["page-1"], total_units=1)),
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertFalse(result["sources_unchanged"])

    def test_incomplete_word_page_list_stays_pending(self) -> None:
        artifact = self.root / "result.docx"
        receipt = self.root / "result.visual-qa.json"
        self._baseline(artifact=artifact, artifact_type="docx", receipt=receipt)
        artifact.write_bytes(b"result-v1")
        result = qa.finalize_receipt(
            receipt_text=str(receipt),
            inspection_text=str(self._inspection(checked_units=["page-1", "page-3"], total_units=2)),
        )
        self.assertEqual(result["outcome"], "qa_pending")
        self.assertTrue(any("page-1 到 page-N" in reason for reason in result["pending_reasons"]))

    def test_excel_sheet_and_print_page_units_pass(self) -> None:
        artifact = self.root / "result.xlsx"
        receipt = self.root / "result.visual-qa.json"
        self._baseline(artifact=artifact, artifact_type="xlsx", receipt=receipt)
        artifact.write_bytes(b"result-v1")
        result = qa.finalize_receipt(
            receipt_text=str(receipt),
            inspection_text=str(
                self._inspection(
                    checked_units=["sheet:登记表", "print-page:登记表:1"],
                    total_units=2,
                )
            ),
        )
        self.assertEqual(result["outcome"], "passed")

    def test_baseline_refuses_overwrite_and_source_alias(self) -> None:
        artifact = self.root / "result.docx"
        receipt = self.root / "result.visual-qa.json"
        self._baseline(artifact=artifact, artifact_type="docx", receipt=receipt)
        with self.assertRaises(qa.VisualQaError):
            self._baseline(artifact=artifact, artifact_type="docx", receipt=receipt)
        with self.assertRaises(qa.VisualQaError):
            qa.create_baseline(
                artifact_text=str(self.source),
                artifact_type="docx",
                source_texts=[str(self.source)],
                receipt_text=str(self.root / "alias.json"),
            )


if __name__ == "__main__":
    unittest.main()
