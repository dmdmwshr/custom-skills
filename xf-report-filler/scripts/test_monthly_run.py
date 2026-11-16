import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import monthly_run as run


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.bulletin = self.base / "2611_通报"
        (self.bulletin / "10月巡查").mkdir(parents=True)
        self.roster = self.base / "roster.xlsx"
        self.roster.write_bytes(b"x")
        self.snap = self.base / "snap"
        self.snap.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def info(self, **fields):
        data = {"exportedAt": "2026-11-16T10:00:00+08:00", "windowStatus": "before_window_end", "finalDataConfirmed": False}
        data.update(fields)
        (self.snap / "snapshot.info.json").write_text(json.dumps(data), encoding="utf-8")

    def plan(self, today):
        args = SimpleNamespace(bulletin_dir=str(self.bulletin), snapshot=str(self.snap), roster=str(self.roster), overrides=None, today=None)
        return run.plan(2026, 11, args, today=today)

    def test_before_the_25th_only_pending_and_final_is_not_offered(self):
        self.info()
        result = self.plan(date(2026, 11, 16))
        self.assertEqual((result["mode"], result["scoreMonth"], result["window"]), ("pending", 10, ["2026-10-26", "2026-11-25"]))
        self.assertTrue(result["ok"], result["blockers"])
        self.assertEqual(result["finalBlockers"], [])
        self.assertIn("只能待补", result["note"])

    def test_from_the_25th_a_stale_or_unconfirmed_snapshot_blocks_final(self):
        self.info()
        result = self.plan(date(2026, 11, 25))
        self.assertEqual(result["mode"], "final_candidate")
        self.assertEqual(len(result["finalBlockers"]), 2)  # exported before the window end + not confirmed by the user
        self.info(exportedAt="2026-11-25T18:00:00+08:00", finalDataConfirmed=True)
        self.assertEqual(self.plan(date(2026, 11, 26))["finalBlockers"], [])

    def test_missing_snapshot_blocks_but_names_the_export_step_and_does_not_assume_a_lag(self):
        (self.snap / "x").write_text("")
        args = SimpleNamespace(bulletin_dir=str(self.bulletin), snapshot=str(self.base / "none"), roster=str(self.roster), overrides=None, today=None)
        result = run.plan(2026, 11, args, today=date(2026, 11, 16))
        self.assertFalse(result["ok"])
        self.assertEqual(result["next"][0]["step"], "snapshot")
        self.assertNotIn("一天", json.dumps(result, ensure_ascii=False))

    def test_cross_year_score_month_and_window(self):
        self.assertEqual(run.score_month_of(2027, 1), (2026, 12))
        self.assertEqual(run.window_for(2027, 1), (date(2026, 12, 26), date(2027, 1, 25)))

    def test_final_audit_is_refused_before_the_window_end(self):
        self.info()
        args = SimpleNamespace(bulletin_dir=str(self.bulletin), snapshot=str(self.snap), roster=str(self.roster), overrides=None, today=None,
                               stage="root", mode="final-audit", apply=False, force=False)
        original = run.today_shanghai
        run.today_shanghai = lambda: date(2026, 11, 16)
        try:
            with self.assertRaises(SystemExit) as caught:
                run.run_stage(2026, 11, args)
        finally:
            run.today_shanghai = original
        self.assertIn("final-audit 被拒绝", str(caught.exception))

    def test_stage_commands_carry_roster_overrides_and_dry_run_by_default(self):
        overrides = self.base / "o.json"
        overrides.write_text("{}")
        args = SimpleNamespace(bulletin_dir=str(self.bulletin), snapshot=str(self.snap), roster=str(self.roster), overrides=str(overrides),
                               force=True, mode="pending")
        grade = run.stage_command("grade", 2026, 11, args, apply=False)
        self.assertIn("--dry-run", grade)
        self.assertEqual(grade[grade.index("--overrides") + 1], str(overrides))
        self.assertEqual(grade[grade.index("--month") + 1], "10")
        self.assertEqual(grade[grade.index("--bulletin-month") + 1], "11")
        root = run.stage_command("root", 2026, 11, args, apply=True)
        self.assertIn("--apply", root)
        self.assertEqual(root[root.index("--roster") + 1], str(self.roster))
        self.assertEqual(root[root.index("--case-data") + 1], str(self.snap))


def command_value(item, flag):
    command = item.get("command") or item["windowsCommand"]
    return command[command.index(flag) + 1]


class EntryContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.bulletin = self.base / "2611 通报"
        (self.bulletin / "10月巡查").mkdir(parents=True)
        self.roster = self.base / "名册 2026.xlsx"
        self.roster.write_bytes(b"x")
        self.overrides = self.base / "o 1.json"
        self.overrides.write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, snapshot, today=date(2026, 11, 16)):
        args = SimpleNamespace(bulletin_dir=str(self.bulletin), snapshot=str(snapshot), roster=str(self.roster), overrides=str(self.overrides), today=None)
        return run.plan(2026, 11, args, today=today)

    def test_every_step_keeps_the_chosen_snapshot_bulletin_roster_and_overrides_with_spaces(self):
        snap = self.base / "快照 一"
        snap.mkdir()
        result = self.plan(snap)
        steps = [item for item in result["next"] if item["step"] in {"organize-dry-run", "organize-apply", "qa-baseline", "grade-apply", "root-apply"}]
        self.assertEqual(len(steps), 5)
        for item in steps:
            command = item.get("command") or item["windowsCommand"]
            for flag, value in (("--bulletin-dir", self.bulletin), ("--snapshot", snap), ("--roster", self.roster), ("--overrides", self.overrides)):
                self.assertIn(flag, command, item["step"])
                shown = command[command.index(flag) + 1]
                expected = str(value) if item.get("command") else run.host_paths.win_path(str(value))
                self.assertEqual(shown, expected, item["step"])
            self.assertTrue(item["shell"])
        root_step = next(i for i in steps if i["step"] == "root-apply")
        self.assertTrue(root_step["shell"].startswith("& '"))  # PowerShell call form, each argument single-quoted
        quoted = "'" + command_value(root_step, "--roster") + "'"  # a path with a space stays one quoted argument
        self.assertIn(quoted, root_step["shell"])

    def test_missing_snapshot_makes_later_steps_use_the_planned_export_directory(self):
        result = self.plan(self.base / "none")
        planned = result["paths"]["snapshotForNextSteps"]
        self.assertIn("snapshot-20261116", planned)
        first = result["next"][0]
        self.assertEqual(first["step"], "snapshot")
        self.assertTrue(first["shell"])
        self.assertIn("python3", first["command"])
        if os.name != "nt":
            import shlex

            self.assertEqual(shlex.split(first["shell"]), first["command"])
        later = next(i for i in result["next"] if i["step"] == "root-apply")
        command = later.get("command") or later["windowsCommand"]
        self.assertEqual(command[command.index("--snapshot") + 1].replace("\\", "/").split("/")[-1], "snapshot-20261116")

    def test_snapshot_step_runs_python3_in_wsl_with_wsl_paths(self):
        result = self.plan(self.base / "none")
        command = result["next"][0]["command"]
        if os.name == "nt":
            self.assertEqual(command[:4], ["wsl.exe", "-e", "python3", run.host_paths.wsl_path(run.SCRIPTS / "feishu_snapshot_export.py")])
        else:
            self.assertEqual(command[0], "python3")
        self.assertTrue(command[command.index("--out-dir") + 1].startswith("/"))

    def test_path_mapping_between_windows_and_wsl(self):
        hp = run.host_paths
        self.assertEqual(hp.wsl_path("D:\\a b\\c"), "/mnt/d/a b/c")
        self.assertEqual(hp.wsl_path("\\\\wsl.localhost\\Ubuntu-24.04\\srv\\x\\y.py"), "/srv/x/y.py")
        self.assertEqual(hp.win_path("/mnt/d/a b/c"), "D:\\a b\\c")
        self.assertEqual(hp.win_path("/srv/x/y.py", "Ubuntu-24.04"), "\\\\wsl.localhost\\Ubuntu-24.04\\srv\\x\\y.py")
        self.assertEqual(hp.win_path("--year"), "--year")

    def test_default_python_without_openpyxl_can_plan_and_check_the_contract(self):
        import subprocess

        code = (
            "import sys; sys.modules['openpyxl'] = None; sys.path.insert(0, %r)\n"
            "import monthly_run, feishu_snapshot_export as e, monthly_workflow as w\n"
            "cfg = w.load_config()['data_sources']['case_snapshot']['export']\n"
            "print(sorted(e.check_contract(cfg)))\n"
            "print(monthly_run.locate(2026, 11, type('A', (), {})())['roster'])\n"
        ) % str(SCRIPT_DIR)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr[-400:])


class VisualQaFailSafeTests(unittest.TestCase):
    def test_unknown_checks_are_never_accepted_as_a_pass(self):
        import artifact_visual_qa as qa

        inspection = {"renderer": "r", "renderer_version": "1", "total_units": 1, "checked_units": ["page-1"],
                      "checks": {run.CHECKS_WORD[0]: "unknown", run.CHECKS_WORD[1]: "passed"}, "blockers": [], "warnings": []}
        pending, blockers = qa._inspection_result(inspection, "doc")
        self.assertTrue(any("状态无效" in item for item in pending))
        self.assertEqual(blockers, [])

    def test_template_writes_every_check_as_unknown_and_baseline_refuses_to_reuse_a_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_dir = Path(tmp)
            artifact = qa_dir / "a.doc"
            artifact.write_bytes(b"doc")
            (qa_dir / "run-context.json").write_text(json.dumps({"receipts": {"archive-新吴": {"artifact": str(artifact), "type": "doc", "receipt": "x"}}}), encoding="utf-8")
            run.qa_template(SimpleNamespace(qa_dir=str(qa_dir)))
            template = json.loads((qa_dir / "archive-新吴.inspection.template.json").read_text(encoding="utf-8"))
            self.assertEqual(set(template["checks"].values()), {"unknown"})
            self.assertEqual(template["artifact_sha256"], __import__("hashlib").sha256(b"doc").hexdigest())
            with self.assertRaises(SystemExit):
                run.qa_baseline(2026, 11, SimpleNamespace(qa_dir=str(qa_dir), only=None, bulletin_dir=str(qa_dir), snapshot=None, roster=None, overrides=None))


if __name__ == "__main__":
    unittest.main()
