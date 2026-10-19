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
                    "artifact_sha256": qa._sha256(next(path for path in [self.root / 'result.docx', self.root / 'result.xlsx'] if path.exists())),
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

    def test_inspection_cannot_accept_another_version_of_the_artifact(self):
        artifact, receipt = self.root / 'result.docx', self.root / 'result.visual-qa.json'
        self._baseline(artifact=artifact, artifact_type='docx', receipt=receipt)
        artifact.write_bytes(b'version-that-was-reviewed')
        inspection = self._inspection(checked_units=['page-1'], total_units=1)
        artifact.write_bytes(b'version-that-was-never-reviewed')
        result = qa.finalize_receipt(receipt_text=str(receipt), inspection_text=str(inspection))
        self.assertEqual(result['outcome'], 'blocked')
        self.assertTrue(any('哈希不同' in text for text in result['blockers']))

    def test_new_inspection_without_an_artifact_hash_remains_pending(self):
        artifact, receipt = self.root / 'result.docx', self.root / 'result.visual-qa.json'
        self._baseline(artifact=artifact, artifact_type='docx', receipt=receipt)
        artifact.write_bytes(b'output')
        inspection = self._inspection(checked_units=['page-1'], total_units=1)
        payload = json.loads(inspection.read_text(encoding='utf-8'))
        payload.pop('artifact_sha256')
        inspection.write_text(json.dumps(payload), encoding='utf-8')
        result = qa.finalize_receipt(receipt_text=str(receipt), inspection_text=str(inspection))
        self.assertEqual(result['outcome'], 'qa_pending')

    def test_rule_release_after_acceptance_preserves_adopted_rule_evidence(self):
        artifact, receipt = self.root / 'result.docx', self.root / 'result.visual-qa.json'
        rule = self.root / 'rules.json'
        rule.write_text('{"version":1}', encoding='utf-8')
        baseline = qa.create_baseline(artifact_text=str(artifact), artifact_type='docx',
            source_texts=[str(self.source)], rule_source_texts=[str(rule)], receipt_text=str(receipt))
        artifact.write_bytes(b'output')
        result = qa.finalize_receipt(receipt_text=str(receipt),
            inspection_text=str(self._inspection(checked_units=['page-1'], total_units=1)))
        self.assertEqual(result['outcome'], 'passed')
        rule.write_text('{"version":2}', encoding='utf-8')
        self.assertEqual(qa.verify_receipt(receipt_text=str(receipt))['outcome'], 'passed')
        frozen = Path(baseline['rule_baseline'][0]['snapshot']['path'])
        self.assertEqual(frozen.read_text(encoding='utf-8'), '{"version":1}')
        frozen.write_text('damaged-evidence', encoding='utf-8')
        self.assertEqual(qa.verify_receipt(receipt_text=str(receipt))['outcome'], 'blocked')

    def test_rule_changes_during_generation_are_blocked(self):
        artifact, receipt = self.root / 'result.docx', self.root / 'result.visual-qa.json'
        rule = self.root / 'rules.json'
        rule.write_text('{}', encoding='utf-8')
        qa.create_baseline(artifact_text=str(artifact), artifact_type='docx', source_texts=[str(self.source)],
            rule_source_texts=[str(rule)], receipt_text=str(receipt))
        artifact.write_bytes(b'output')
        rule.write_text('{"different":true}', encoding='utf-8')
        result = qa.finalize_receipt(receipt_text=str(receipt),
            inspection_text=str(self._inspection(checked_units=['page-1'], total_units=1)))
        self.assertEqual(result['outcome'], 'blocked')
        self.assertFalse(result['sources_unchanged'])

    def test_business_workbook_cannot_be_frozen_as_a_rule(self):
        workbook = self.root / 'business.xlsx'
        workbook.write_bytes(b'business-data')
        with self.assertRaises(qa.VisualQaError):
            qa.create_baseline(artifact_text=str(self.root / 'result.docx'), artifact_type='docx',
                source_texts=[str(self.source)], rule_source_texts=[str(workbook)],
                receipt_text=str(self.root / 'result.visual-qa.json'))

    def test_changed_accepted_workbook_uses_new_receipt_and_preserves_prior_result(self):
        artifact = self.root / 'result.xlsx'
        original_receipt = self.root / 'first.visual-qa.json'
        self._baseline(artifact=artifact, artifact_type='xlsx', receipt=original_receipt)
        artifact.write_bytes(b'workbook-version-one')
        qa.finalize_receipt(receipt_text=str(original_receipt),
            inspection_text=str(self._inspection(checked_units=['sheet:登记表','print-page:登记表:1'], total_units=2)))
        previous_evidence = original_receipt.read_bytes()
        artifact.write_bytes(b'workbook-version-two')
        self.assertEqual(qa.verify_receipt(receipt_text=str(original_receipt))['outcome'], 'blocked')
        current_receipt = self.root / 'second.visual-qa.json'
        self._baseline(artifact=artifact, artifact_type='xlsx', receipt=current_receipt)
        result = qa.finalize_receipt(receipt_text=str(current_receipt),
            inspection_text=str(self._inspection(checked_units=['sheet:登记表','print-page:登记表:1'], total_units=2)))
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(original_receipt.read_bytes(), previous_evidence)


if __name__ == "__main__":
    unittest.main()
