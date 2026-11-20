import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_online_sheet as mos
import test_monthly_online_sheet as fx


@unittest.skipUnless(shutil.which("node"), "node 不可用")
class EgoOnlineSheetNodeTests(unittest.TestCase):
    def test_node_suite(self):
        done = subprocess.run(
            ["node", "--test", str(SCRIPT_DIR / "test_ego_online_sheet.mjs")],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(done.returncode, 0, done.stdout[-2500:] + done.stderr[-1000:])

    def test_plan_fingerprint_is_identical_in_python_and_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "【待补】2026年11月科室月考核情况记录表.xlsx"
            fx.make_local(src, values={"D14": "含\n换行\n和反斜杠\\的值"})
            local = mos.read_local(src, fx.RULE)
            plan = mos.build_plan(local, fx.snapshot(fx.online_cells({("锡山大队", "联网系统核查"): "旧值"})), 2026, 11, fx.RULE)
            self.assertEqual(plan["status"], "ok")
            plan_path = Path(tmp) / "plan.json"
            mos.write_json(plan_path, plan)
            script = (
                "import { planFingerprint } from './ego_online_sheet.mjs';"
                "import fs from 'node:fs';"
                "const p = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));"
                "console.log(planFingerprint(p));"
            )
            done = subprocess.run(
                ["node", "--input-type=module", "-e", script, str(plan_path)],
                capture_output=True, encoding="utf-8", cwd=str(SCRIPT_DIR),
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), plan["fingerprint"])


class CheckPlanCommandTests(unittest.TestCase):
    def test_check_plan_recomputes_from_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "【待补】2026年11月科室月考核情况记录表.xlsx"
            fx.make_local(src)
            local = mos.read_local(src, fx.RULE)
            snap = fx.snapshot(fx.online_cells({}))
            plan = mos.build_plan(local, snap, 2026, 11, fx.RULE)
            plan_path, snap_path = Path(tmp) / "plan.json", Path(tmp) / "snap.json"
            mos.write_json(plan_path, plan)
            mos.write_json(snap_path, snap)
            base = ["check-plan", "--source", str(src), "--plan", str(plan_path), "--year", "2026"]
            self.assertEqual(mos.main(base + ["--month", "11", "--snapshot", str(snap_path)]), 0)
            self.assertEqual(mos.main(base + ["--month", "12"]), 2)
            plan["items"][0]["online_cell"] = "A100"
            mos.write_json(plan_path, plan)
            self.assertEqual(mos.main(base + ["--month", "11"]), 2)


if __name__ == "__main__":
    unittest.main()
