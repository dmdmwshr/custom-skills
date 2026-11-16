import json
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import feishu_case_source as source
import feishu_snapshot_export as export
from test_feishu_case_source import Builder, standard

CFG = {"profile": "p", "base_token": "BASE", "page_limit": 2000,
       "tables": {"cases": "tblC", "checks": "tblK", "products": "tblP", "monthly": "tblM"}}


class FakeCli:
    """Stands in for lark-cli: serves the rows of a Builder snapshot page by page and records every argv."""

    def __init__(self, builder, break_rev=False, bad_next_offset=False):
        self.rows = {"tblC": builder.cases, "tblK": builder.checks, "tblP": builder.products, "tblM": builder.monthly}
        self.calls, self.break_rev, self.bad_next_offset = [], break_rev, bad_next_offset

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        value = lambda flag: argv[argv.index(flag) + 1]
        table, limit = value("--table-id"), int(value("--limit"))
        offset = int(value("--offset")) if "--offset" in argv else 0
        rows = self.rows[table][offset:offset + limit]
        out = Path(cwd) / value("--output")
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        more = offset + len(rows) < len(self.rows[table])
        rev = 7 + (1 if self.break_rev and offset else 0)
        manifest = {"manifest_version": "v1", "base_token": value("--base-token"), "table_id": table, "rev": rev,
                    "records_count": len(rows), "page_count": 1, "has_more": more}
        if more:
            manifest["next_offset"] = (offset + len(rows)) + (1 if self.bad_next_offset else 0)
        out.with_suffix("").with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.builder = standard()
        self.builder.month("2026-01", "梁溪大队", **{"月报_检查次数": 3})

    def tearDown(self):
        self.tmp.cleanup()

    def run_export(self, cli, out="snap", today=date(2026, 1, 20), page_limit=None):
        return export.run_export(2026, 1, self.base / out, CFG, "lark-cli", runner=cli, contract_dir=self.base / "no-contract",
                                 page_limit=page_limit, today=today)

    def test_multi_page_export_equals_single_page_export_and_states_the_lower_bound(self):
        single = self.run_export(FakeCli(self.builder), "single")
        paged = self.run_export(FakeCli(self.builder), "paged", page_limit=2)
        for role in source.SNAPSHOT_FILES:
            self.assertEqual((self.base / "single" / f"{role}.ndjson").read_bytes(), (self.base / "paged" / f"{role}.ndjson").read_bytes())
        self.assertGreater(paged["info"]["tables"]["checks"]["pages"], 1)
        self.assertEqual(single["info"]["windowStatus"], "before_window_end")
        self.assertIn("下限", single["info"]["completeness"])
        self.assertIs(single["info"]["finalDataConfirmed"], False)
        self.assertNotIn("一天", json.dumps(single["info"], ensure_ascii=False))
        loaded = source.load_snapshot(self.base / "paged")
        self.assertTrue(loaded["provenance"]["declared"])
        self.assertEqual(loaded["provenance"]["windowStatus"], "before_window_end")

    def test_after_window_end_is_stated_but_final_confirmation_stays_false(self):
        result = self.run_export(FakeCli(self.builder), today=date(2026, 1, 26))
        self.assertEqual(result["info"]["windowStatus"], "at_or_after_window_end")
        self.assertIs(result["info"]["finalDataConfirmed"], False)

    def test_only_the_read_verb_is_ever_called(self):
        cli = FakeCli(self.builder)
        self.run_export(cli)
        for argv in cli.calls:
            self.assertEqual([w for w in argv if w in {"base", "+record-list"}], ["base", "+record-list"])
            self.assertFalse([w for w in argv if w.startswith("+") and w != "+record-list"])
        with self.assertRaisesRegex(export.ExportError, "只允许只读"):
            export.assert_read_only(["lark-cli", "base", "+record-create"])
        with self.assertRaisesRegex(export.ExportError, "只允许只读|写入"):
            export.assert_read_only(["lark-cli", "auth", "login"])

    def test_existing_target_is_refused_and_not_touched(self):
        (self.base / "snap").mkdir()
        (self.base / "snap" / "keep.txt").write_text("x")
        with self.assertRaisesRegex(export.ExportError, "已存在"):
            self.run_export(FakeCli(self.builder))
        self.assertEqual((self.base / "snap" / "keep.txt").read_text(), "x")

    def test_revision_change_between_pages_and_broken_offsets_publish_nothing(self):
        with self.assertRaisesRegex(export.ExportError, "被修改"):
            self.run_export(FakeCli(self.builder, break_rev=True), "a", page_limit=2)
        with self.assertRaisesRegex(export.ExportError, "偏移不连续"):
            self.run_export(FakeCli(self.builder, bad_next_offset=True), "b", page_limit=2)
        self.assertFalse((self.base / "a").exists() or (self.base / "b").exists())

    def test_a_failed_publish_removes_the_partial_target(self):
        from unittest.mock import patch

        original = export.shutil.copyfile
        calls = []

        def flaky(src, dst, **kwargs):
            if Path(dst).parent.name == "partial":
                calls.append(dst)
                if len(calls) == 3:
                    raise OSError("disk error")
            return original(src, dst, **kwargs)

        with patch.object(export.shutil, "copyfile", side_effect=flaky):
            with self.assertRaises(OSError):
                self.run_export(FakeCli(self.builder), "partial")
        self.assertFalse((self.base / "partial").exists())

    def test_a_transient_cli_failure_is_retried_but_a_persistent_one_stops(self):
        from unittest.mock import patch

        class Flaky(FakeCli):
            def __init__(self, builder, failures):
                super().__init__(builder)
                self.failures = failures

            def __call__(self, argv, cwd):
                if self.failures:
                    self.failures -= 1
                    return types.SimpleNamespace(returncode=4, stdout="", stderr="EOF")
                return super().__call__(argv, cwd)

        with patch.object(export, "RETRY_PAUSE", 0):
            ok = self.run_export(Flaky(self.builder, 2), "retried")
            self.assertTrue(ok["ok"])
            with self.assertRaisesRegex(export.ExportError, "EOF"):
                self.run_export(Flaky(self.builder, 99), "stuck")
        self.assertFalse((self.base / "stuck").exists())

    def test_cli_failure_and_invalid_snapshot_publish_nothing(self):
        failing = lambda argv, cwd: types.SimpleNamespace(returncode=3, stdout="", stderr="token expired")
        with self.assertRaisesRegex(export.ExportError, "token expired"):
            self.run_export(failing)
        broken = Builder()
        broken.cases, broken.checks, broken.products, broken.monthly = [], [], [], []
        broken.cases.append({"record_id": "c", "项目编号": "X", "初查结果": ["合格"]})
        broken.checks.append({"record_id": "k", "检查键": "X|初查", "阶段": ["初查"], "项目编号": [{"id": "missing"}]})
        with self.assertRaisesRegex(export.ExportError, "结构校验"):
            self.run_export(FakeCli(broken), "bad")
        self.assertFalse((self.base / "bad").exists())

    def test_cli_missing_from_path_is_found_through_a_login_shell(self):
        from unittest.mock import patch

        real = self.base / "lark-cli"
        real.write_text("#!/bin/sh\n")
        with patch.object(export.shutil, "which", return_value=None), patch.object(export.os, "name", "posix"), \
                patch.dict(export.os.environ, {}, clear=False), \
                patch.object(export.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout=f"banner\n{real}\n")):
            export.os.environ.pop("XF_LARK_CLI", None)
            self.assertEqual(export.find_lark_cli(), str(real))

    def test_missing_lark_cli_explains_how_to_run_in_wsl(self):
        with self.assertRaisesRegex(export.ExportError, "WSL"):
            export.find_lark_cli("/nonexistent/lark-cli")


if __name__ == "__main__":
    unittest.main()
