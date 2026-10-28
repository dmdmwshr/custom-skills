#!/usr/bin/env python3
"""匿名合成数据测试：python3 test_preflight_bitable.py"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preflight_bitable as P  # noqa: E402

REF = os.path.join(HERE, "..", "references")


def rd(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def wr(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


SPEC = json.loads(rd(os.path.join(REF, "preflight-spec.example.json")))
DATA = json.loads(rd(os.path.join(REF, "preflight-data.example.json")))


def codes(issues):
    return sorted({i["code"] for i in issues})


class Check(unittest.TestCase):
    def test_example_is_clean(self):
        self.assertEqual(P.check(SPEC, DATA), [])

    def test_duplicate_and_missing_pk(self):
        d = copy.deepcopy(DATA)
        d["案卷"].append(dict(d["案卷"][0]))
        d["案卷"].append({"案卷号": "", "所属单位": "甲单位"})
        c = codes(P.check(SPEC, d))
        self.assertIn("pk_duplicate", c)
        self.assertIn("pk_missing", c)

    def test_numeric_or_padded_key_rejected(self):
        d = copy.deepcopy(DATA)
        d["案卷"][0]["案卷号"] = 12345
        d["案卷"][1]["案卷号"] = " A-0002"
        self.assertEqual(codes(P.check(SPEC, d)).count("pk_not_text"), 1)
        self.assertGreaterEqual(sum(i["code"] == "pk_not_text" for i in P.check(SPEC, d)), 2)

    def test_orphans(self):
        d = copy.deepcopy(DATA)
        d["检查"].append({"检查键": "Z|初查", "案卷号": "Z-9", "阶段": "初查", "检查日期": "2026-01-01"})
        d["案卷"][0]["所属单位"] = "不存在单位"
        iss = P.check(SPEC, d)
        self.assertEqual(sum(i["code"] == "orphan" for i in iss), 2)

    def test_unknown_must_not_be_no(self):
        d = copy.deepcopy(DATA)
        d["案卷"][0]["是否网售"] = None  # 缺失不能留空
        d["案卷"][1]["分类依据"] = None  # 否无依据
        c = codes(P.check(SPEC, d))
        self.assertIn("tri_blank", c)
        self.assertIn("tri_no_evidence", c)
        d = copy.deepcopy(DATA)
        d["案卷"][0]["是否网售"] = "未知"
        self.assertIn("tri_invalid", codes(P.check(SPEC, d)))

    def test_date_anomalies(self):
        d = copy.deepcopy(DATA)
        d["案卷"][0]["初查日期"] = "2026-02-30"
        d["案卷"][1]["初查日期"] = "2025-12-31"
        d["检查"][0]["检查日期"] = "昨天"
        c = codes(P.check(SPEC, d))
        self.assertIn("date_invalid", c)
        self.assertIn("date_out_of_range", c)
        d = copy.deepcopy(DATA)
        d["案卷"][0]["初查日期"] = "2026/03/05 00:00:00"  # 导出常见写法应可接受
        self.assertEqual(P.check(SPEC, d), [])

    def test_wrapped_value(self):
        d = copy.deepcopy(DATA)
        d["案卷"][0]["产品数"] = {"value": 2, "source": None}
        self.assertIn("wrapped_value", codes(P.check(SPEC, d)))

    def test_multi_granularity_children(self):
        d = copy.deepcopy(DATA)
        d["检查"] = [r for r in d["检查"] if r["案卷号"] != "A-0002"]
        iss = P.check(SPEC, d)
        self.assertEqual([i["key"] for i in iss if i["code"] == "parent_without_children"], ["A-0002"])
        # 一案多检、一检多子记录的正常结构不报错
        d = copy.deepcopy(DATA)
        d["检查"].append({"检查键": "A-0001|复查2", "案卷号": "A-0001", "阶段": "复查", "检查日期": "2026-04-01"})
        self.assertEqual(P.check(SPEC, d), [])

    def test_count_and_number(self):
        d = copy.deepcopy(DATA)
        d["单位"].append({"单位名称": "丙单位"})
        d["案卷"][0]["产品数"] = "两个"
        c = codes(P.check(SPEC, d))
        self.assertIn("count_mismatch", c)
        self.assertIn("number_invalid", c)


class Diff(unittest.TestCase):
    def export(self):
        e = copy.deepcopy(DATA)
        e["案卷"][0]["初查日期"] = "2026/03/05"  # 云端日期写法不同，应视为相同
        e["检查"][0]["案卷号"] = ["A-0001"]  # 关联列导出成列表
        e["检查"][2]["案卷号"] = "A-0002"
        return e

    def test_equivalent_export_passes(self):
        stats, iss = P.diff(SPEC, DATA, self.export())
        self.assertEqual(iss, [])
        self.assertEqual(stats["案卷"]["missing_in_export"], 0)

    def test_date_blanked_missing_extra_shell(self):
        e = self.export()
        e["案卷"][0]["初查日期"] = None  # 导入清空日期
        e["案卷"].pop()  # 少一条
        e["检查"].append({"检查键": "多余", "案卷号": "A-0001", "阶段": "初查", "检查日期": None})
        e["单位"].append({"单位名称": None})  # 小表导入遗留空壳
        stats, iss = P.diff(SPEC, DATA, e)
        c = codes(iss)
        for k in ("value_mismatch", "missing_in_export", "extra_in_export", "empty_shell_rows"):
            self.assertIn(k, c)
        self.assertEqual(stats["案卷"]["date_blanked"], 1)
        self.assertEqual(stats["单位"]["empty_shell_rows"], 1)

    def test_relation_count_mismatch(self):
        e = self.export()
        e["检查"][1]["案卷号"] = ["A-0001", "A-0002"]
        _, iss = P.diff(SPEC, DATA, e)
        self.assertEqual([i["column"] for i in iss if i["code"] == "value_mismatch"], ["案卷号"])


class ReadOnlyIdempotent(unittest.TestCase):
    def test_cli_does_not_modify_inputs_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as td:
            sp, dp = os.path.join(td, "s.json"), os.path.join(td, "d.json")
            wr(sp, SPEC)
            wr(dp, DATA)
            digest = lambda: [hashlib.sha256(rd(p, "rb")).hexdigest() for p in (sp, dp)]
            h0 = digest()
            run = lambda: subprocess.run([sys.executable, "-I", os.path.join(HERE, "preflight_bitable.py"), "check", sp, dp],
                                         capture_output=True, text=True)
            a, b = run(), run()
            self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout))
            self.assertEqual(a.returncode, 0)
            self.assertEqual(h0, digest())
            self.assertEqual(sorted(os.listdir(td)), ["d.json", "s.json"])
            # 坏输入退出码 2
            bad = subprocess.run([sys.executable, "-I", os.path.join(HERE, "preflight_bitable.py"), "check", sp, os.path.join(td, "none.json")],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
