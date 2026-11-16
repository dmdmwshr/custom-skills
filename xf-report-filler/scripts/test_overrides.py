import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import overrides as ov


def base(**extra):
    data = {"schema": ov.SCHEMA, "year": 2026, "month": 11, "authorization": "用户 2026-11-16 确认",
            "unit_name_fixes": [{"brigade": "梁溪大队", "sheet": "梁溪", "row": 3, "original": "缺锡市东方医院",
                                 "corrected": "无锡市东方医院", "context": "单位名开头", "judgement": "无锡错字"}],
            "deduction_remaps": [{"brigade": "宜兴大队", "match": "送达回执名称不准确", "from_index": 11, "to_index": 12,
                                  "value": 0.2, "authorization": "用户 2026-11-16"}]}
    data.update(extra)
    return data


def details():
    return [{"大队": "梁溪大队", "sheet": "梁溪", "row": 3, "单位": "缺锡市东方医院", "note": "缺维保记录"},
            {"大队": "梁溪大队", "sheet": "梁溪", "row": 4, "单位": "缺锡市无关单位", "note": ""}]


def records():
    return [{"大队": "宜兴大队", "deductions": [{"description": "送达回执名称不准确", "general_index": 11, "value": 0.2}]}]


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "o.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return self.path

    def test_exact_entries_apply_without_mutating_inputs_or_other_rows(self):
        loaded = ov.load(self.write(base()), 2026, 11)
        src_details, src_records = details(), records()
        new_details, new_records, report = ov.apply(loaded, src_details, src_records)
        self.assertEqual(new_details[0]["单位"], "无锡市东方医院")
        self.assertEqual(new_details[0]["单位_原值"], "缺锡市东方医院")
        self.assertEqual(new_details[0]["note"], "缺维保记录")  # description text is never rewritten
        self.assertEqual(new_details[1]["单位"], "缺锡市无关单位")  # no global replacement
        self.assertEqual(new_records[0]["deductions"][0]["general_index"], 12)
        self.assertEqual(new_records[0]["deductions"][0]["source_general_index"], 11)
        self.assertEqual(new_records[0]["deductions"][0]["value"], 0.2)
        self.assertEqual(src_details[0]["单位"], "缺锡市东方医院")
        self.assertEqual(src_records[0]["deductions"][0]["general_index"], 11)
        self.assertEqual(len(report["applied"]), 2)
        self.assertEqual(len(report["sha256"]), 64)

    def test_entry_that_no_longer_matches_the_source_stops(self):
        loaded = ov.load(self.write(base()), 2026, 11)
        changed = details()
        changed[0]["单位"] = "无锡市东方医院"
        with self.assertRaisesRegex(ov.OverrideError, "未命中"):
            ov.apply(loaded, changed, records())

    def test_value_mismatch_does_not_remap(self):
        loaded = ov.load(self.write(base()), 2026, 11)
        changed = records()
        changed[0]["deductions"][0]["value"] = 0.5
        with self.assertRaisesRegex(ov.OverrideError, "未命中"):
            ov.apply(loaded, details(), changed)

    def test_other_month_missing_authorization_and_bad_schema_are_rejected(self):
        with self.assertRaisesRegex(ov.OverrideError, "不能跨月"):
            ov.load(self.write(base()), 2026, 12)
        with self.assertRaisesRegex(ov.OverrideError, "authorization"):
            ov.load(self.write(base(authorization="")), 2026, 11)
        with self.assertRaisesRegex(ov.OverrideError, "schema"):
            ov.load(self.write(base(schema="x")), 2026, 11)
        bad = base()
        del bad["unit_name_fixes"][0]["judgement"]
        with self.assertRaisesRegex(ov.OverrideError, "judgement"):
            ov.load(self.write(bad), 2026, 11)
        with self.assertRaisesRegex(ov.OverrideError, "不存在"):
            ov.load(Path(self.tmp.name) / "none.json")


if __name__ == "__main__":
    unittest.main()
