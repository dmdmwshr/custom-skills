"""Synthetic directory names only; no business documents or personal records."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from plan_mail_directory import make_plan


MEMBERS = [
    "1.1、甲项目-需求论证（专家甲）",
    "1.2、甲项目-技术论证（专家乙）",
    "0.1.3、甲项目-规格论证（专家甲）（已发送）",
]


def selection(**changes):
    data = {
        "review_id": "PS-TEST",
        "project_name": "甲项目",
        "stage": "初验",
        "experts": ["专家甲", "专家乙"],
        "project_members": list(MEMBERS),
        "source_name": "甲项目初验（某科室自行开展，暂时没有扫描件）",
        "current_name": None,
        "remarks": [],
    }
    data.update(changes)
    return data


class NamingTests(unittest.TestCase):
    def test_sent_item_reserves_sequence_and_note_survives(self):
        result = make_plan(selection(), MEMBERS)
        self.assertEqual(result["review_number"], 4)
        self.assertEqual(result["target_name"], "1.4、甲项目-初验（专家甲、专家乙）（某科室自行开展，暂时没有扫描件）")
        self.assertFalse(result["preserved_sent_mark"])

    def test_existing_review_reuses_number_and_is_idempotent(self):
        first = make_plan(selection(), MEMBERS)
        name = first["target_name"]
        second = make_plan(selection(current_name=name), MEMBERS + [name])
        self.assertEqual(second["target_name"], name)
        self.assertFalse(second["rename_needed"])

    def test_repeat_without_current_name_refuses_duplicate(self):
        name = make_plan(selection(), MEMBERS)["target_name"]
        with self.assertRaisesRegex(ValueError, "已经存在"):
            make_plan(selection(), MEMBERS + [name])

    def test_new_project_uses_max_not_gap(self):
        result = make_plan(selection(project_name="新年度甲项目", project_members=[]), MEMBERS + ["6.2、乙项目-技术（专家丙）"])
        self.assertEqual((result["project_number"], result["review_number"]), (7, 1))

    def test_expert_spaces_deduplicate_in_labor_order(self):
        result = make_plan(selection(experts=["专家 乙", "专家甲", "专家　乙"]), MEMBERS)
        self.assertIn("（专家乙、专家甲）", result["target_name"])
        self.assertEqual(result["expert_count"], 2)

    def test_missing_experts_refused(self):
        with self.assertRaisesRegex(ValueError, "专家名单"):
            make_plan(selection(experts=[]), MEMBERS)

    def test_missing_member_refused(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            make_plan(selection(project_members=["1.9、已被移动（专家甲）"]), MEMBERS)

    def test_cross_family_members_refused(self):
        other = "2.1、同名另一年度项目（专家甲）"
        with self.assertRaisesRegex(ValueError, "不同项目"):
            make_plan(selection(project_members=MEMBERS + [other]), MEMBERS + [other])

    def test_duplicate_sent_sequence_refused(self):
        with self.assertRaisesRegex(ValueError, "重复评审序号"):
            make_plan(selection(), MEMBERS + ["1.3、甲项目-别名（专家甲）"])

    def test_nested_note_and_project_phase_preserved(self):
        data = selection(project_name="甲项目（二期）", stage="验收论证（初验）", source_name="甲项目（二期）初验（备注（补材料））")
        first = make_plan(data, MEMBERS)
        second = make_plan({**data, "current_name": first["target_name"]}, MEMBERS + [first["target_name"]])
        self.assertEqual(first["target_name"], second["target_name"])
        self.assertEqual(second["preserved_remarks"], ["备注（补材料）"])

    def test_sent_mark_and_sort_prefix_remain(self):
        current = "0.1.4、甲项目-初验（专家甲、专家乙）（保留说明）（已发送）"
        result = make_plan(selection(current_name=current, source_name=current), MEMBERS + [current])
        self.assertEqual(result["target_name"], current)
        self.assertTrue(result["preserved_sent_mark"])

    def test_invalid_or_long_component_refused(self):
        for name in ["甲/项目", "甲项目.", "甲" * 260]:
            with self.subTest(name=name[:10]), self.assertRaises(ValueError):
                make_plan(selection(project_name=name), MEMBERS)

    def test_cli_is_read_only_and_bom_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mail = root / "mail"
            mail.mkdir()
            for name in MEMBERS:
                (mail / name).mkdir()
            (mail / MEMBERS[0] / "keep.txt").write_text("unchanged", encoding="utf-8")
            request = root / "selection.json"
            request.write_text(json.dumps(selection(), ensure_ascii=False), encoding="utf-8-sig")
            before = {str(p.relative_to(mail)): p.read_bytes() if p.is_file() else None for p in mail.rglob("*")}
            process = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).with_name("plan_mail_directory.py")), "--mail-root", str(mail), "--selection", str(request)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(json.loads(process.stdout)["read_only"])
            after = {str(p.relative_to(mail)): p.read_bytes() if p.is_file() else None for p in mail.rglob("*")}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
