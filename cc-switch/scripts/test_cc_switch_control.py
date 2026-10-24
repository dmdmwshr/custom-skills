"""cc_switch_control.py 行为测试：全部在临时影子目录 + 合成数据库 + 模拟 CLI 里进行，不触碰真实库、配置、桌面或全局 CLI。
运行：python -I -B -m unittest discover -s cc-switch/scripts -p "test_cc_switch_control.py" -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cc_switch_control as ctl  # noqa: E402

CONTROL, FAKE = HERE / "cc_switch_control.py", HERE / "fake_cc_switch_cli.py"
COLUMNS = "id,name,description,directory,repo_owner,repo_name,repo_branch,readme_url,enabled_claude,enabled_codex,enabled_gemini,enabled_opencode,installed_at,content_hash,updated_at,enabled_hermes,enabled_grokbuild,enabled_mcode,enabled_agy,enabled_kimi"


def run(env_dir, *args, expect=None):
    p = subprocess.run([sys.executable, "-I", "-B", str(CONTROL), "--config-dir", str(env_dir), "--cli", str(FAKE),
                        "--client-dir", f"codex={env_dir / 'client-codex' / 'skills'}", "--client-dir", f"gemini={env_dir / 'client-gemini' / 'skills'}", *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        data = None
    if expect is not None:
        assert p.returncode == expect, (p.returncode, p.stdout, p.stderr)
    return p.returncode, data


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccctl-"); self.dir = Path(self.tmp.name)
        self.skills = self.dir / "skills"; self.skills.mkdir()
        self.codex = self.dir / "client-codex" / "skills"; self.codex.mkdir(parents=True)
        self.gemini = self.dir / "client-gemini" / "skills"; self.gemini.mkdir(parents=True)
        self.db = sqlite3.connect(self.dir / "cc-switch.db")
        self.db.execute(f"create table skills({COLUMNS})")
        self.db.execute("create table skill_repos(owner,name,branch,enabled)")
        self.db.execute("PRAGMA user_version=22")
        # 第三方两个（含一个全关的“前门后端”）、自建一个、同名目录两条（歧义）
        self.add("alpha", "openai", "skills", enabled=(0, 1, 0, 0), files={"SKILL.md": "alpha v1", "a/b.txt": "b"})
        self.add("beta", "anthropics", "skills", enabled=(0, 1, 1, 0), files={"SKILL.md": "beta v1"})
        self.add("backend-only", "cloudflare", "skills", enabled=(0, 0, 0, 0), files={"SKILL.md": "backend v1"})
        self.add("mine", "dmdmwshr", "custom-skills", enabled=(0, 1, 0, 0), files={"SKILL.md": "mine v1"})
        self.add("dup", "x", "one", enabled=(0, 1, 0, 0), files={"SKILL.md": "dup1"}, sid="x/one:dup")
        self.db.execute(f"insert into skills({COLUMNS}) select 'y/two:dup',name,description,directory,'y','two',repo_branch,readme_url,enabled_claude,enabled_codex,enabled_gemini,enabled_opencode,installed_at,content_hash,updated_at,enabled_hermes,enabled_grokbuild,enabled_mcode,enabled_agy,enabled_kimi from skills where id='x/one:dup'")
        self.db.commit()
        self.fake({"version": "5.11.3", "updates": []})

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def add(self, name, owner, repo, enabled, files, sid=None):
        folder = self.skills / name
        for rel, data in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True); (folder / rel).write_text(data, encoding="utf-8")
        self.db.execute(f"insert into skills({COLUMNS}) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid or f"{owner}/{repo}:{name}", name, "", name, owner, repo, "main", "", *enabled, 1, ctl.dir_hash(folder), 1, 0, 0, 0, 0, 0))
        if enabled[1]:  # Codex 客户端目录里放一份同内容副本
            import shutil
            shutil.copytree(folder, self.codex / name, dirs_exist_ok=True)
        if enabled[2]:
            import shutil
            shutil.copytree(folder, self.gemini / name, dirs_exist_ok=True)

    def fake(self, value):
        (self.dir / "fake-cli.json").write_text(json.dumps(value), encoding="utf-8")

    def log(self):
        path = self.dir / "fake-cli.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def row(self, directory, sid=None):
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.row_factory = sqlite3.Row
        r = db.execute("select * from skills where directory=? and id like ?", (directory, sid or "%")).fetchone(); db.close()
        return dict(r)


class HashTests(unittest.TestCase):
    def test_native_table_separator_is_parsed(self):
        table = "Directory ┆ Name\nbrand-guidelines ┆ Brand Guidelines\n"
        self.assertEqual(ctl.parse_table_directories(table), [("brand-guidelines", "Brand Guidelines")])

    def test_golden_vector_ordering_hidden_and_unicode(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for rel, data in (("a/b.txt", "b"), ("a-b/c.txt", "c"), ("中文.md", "文"), (".hidden", "h"), ("sub/.dot/x", "x"), ("SKILL.md", "s")):
                (root / rel).parent.mkdir(parents=True, exist_ok=True); (root / rel).write_bytes(data.encode("utf-8"))
            # 组件字节序：SKILL.md < a/b.txt < a-b/c.txt < 中文.md；点开头文件/目录整体忽略
            import hashlib
            expected = hashlib.sha256()
            for rel, data in (("SKILL.md", "s"), ("a/b.txt", "b"), ("a-b/c.txt", "c"), ("中文.md", "文")):
                expected.update(rel.encode() + b"\0" + data.encode() + b"\0")
            self.assertEqual(ctl.dir_hash(root), expected.hexdigest())


class Control(Fixture):
    def test_legitimate_client_link_to_exact_ssot_is_accepted(self):
        import shutil
        target = self.codex / "alpha"
        shutil.rmtree(target)
        make_link(target, self.skills / "alpha")
        code, data = run(self.dir, "verify", "--skills", "alpha", expect=0)
        code, data = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=0)
        self.assertEqual(data["results"][0]["clients"]["codex"], "ok")

    def test_unknown_enabled_column_cannot_be_silently_ignored(self):
        self.db.execute("alter table skills add column enabled_futureclient integer default 0")
        self.db.execute("update skills set enabled_futureclient=1 where directory='alpha'")
        self.db.commit()
        before = (self.skills / "alpha/SKILL.md").read_bytes()
        code, data = run(self.dir, "verify", "--skills", "alpha", expect=1)
        code, data = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertIn("unsupported_enabled_client", json.dumps(data))
        self.assertEqual((self.skills / "alpha/SKILL.md").read_bytes(), before)
        self.assertFalse(any(line.startswith("skills update") for line in self.log()))

    def test_state_overrides_cannot_separate_backup_from_cli_target(self):
        other = self.dir / "other-state"
        for args in (("--db", str(other / "cc-switch.db")), ("--ssot-root", str(other / "skills")),
                     ("--client-dir", "codex=" + str(self.dir.parent / "outside-client"))):
            code, data = run(self.dir, *args, "update", "--skills", "alpha", "--confirm", expect=3)
            self.assertIn(data["verdict"], ("cli_state_path_mismatch", "isolated_client_outside_root"))
        self.assertEqual(self.log(), [])
        self.assertFalse((self.dir / "skill-backups").exists())

    def test_isolated_cli_environment_and_settings_are_bound_to_test_root(self):
        settings = {"theme": "dark", "codexConfigDir": "C:/unrelated-user-root"}
        (self.dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        args = ctl.build_parser().parse_args(["--config-dir", str(self.dir), "--cli", str(FAKE),
                                            "--client-dir", "codex=" + str(self.codex), "check-updates"])
        env = ctl.Env(args)
        child = env.cli_env()
        self.assertTrue(ctl.inside(Path(child["CC_SWITCH_TEST_HOME"]), self.dir))
        self.assertEqual(Path(child["CODEX_HOME"]) / "skills", self.codex)
        self.assertEqual(Path(child["CC_SWITCH_CONFIG_DIR"]), self.dir)
        code, data = run(self.dir, "check-updates", expect=0)
        actual = json.loads((self.dir / "settings.json").read_text(encoding="utf-8"))
        for key in env.settings_keys.values(): self.assertTrue(ctl.inside(Path(actual[key]), self.dir), key)
        self.assertEqual(actual["theme"], "dark")
        self.assertEqual(json.loads((Path(data["backup"]) / "settings.json").read_text(encoding="utf-8")), settings)

    def test_doctor_ready_and_reports_state(self):
        code, d = run(self.dir, "doctor", expect=0)
        self.assertEqual(d["verdict"], "ready"); self.assertEqual(d["db"]["user_version"], 22)
        self.assertEqual(d["db"]["skills_custom"], 1); self.assertEqual(d["custom_hash_status"]["match"], 1)
        self.assertEqual(d["db"]["enabled_by_client"]["codex"], 5)
        self.assertEqual(self.log(), ["--version"])  # doctor 只调用 --version

    def test_schema_mismatch_unknown_version_and_missing_cli_are_refused_without_skills_commands(self):
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute("PRAGMA user_version=21"); db.commit(); db.close()
        code, d = run(self.dir, "doctor", expect=3); self.assertEqual(d["verdict"], "needs_coordinated_upgrade")
        code, d = run(self.dir, "check-updates", expect=3); self.assertEqual(d["verdict"], "needs_coordinated_upgrade")
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertTrue(all(line == "--version" for line in self.log()), self.log())
        self.assertFalse((self.dir / "skill-backups").exists())  # 门禁拒绝时不写任何备份
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute("PRAGMA user_version=22"); db.commit(); db.close()
        self.fake({"version": "9.9.9"})
        code, d = run(self.dir, "doctor", expect=3); self.assertEqual(d["verdict"], "cli_version_unknown")
        code, p = subprocess.run([sys.executable, "-I", "-B", str(CONTROL), "--config-dir", str(self.dir), "--cli", str(self.dir / "nope.exe"), "doctor"],
                                 capture_output=True, text=True, encoding="utf-8").returncode, None
        self.assertEqual(code, 3)

    def test_check_updates_builds_reviewable_plan_and_blocks_unsafe_items(self):
        (self.skills / "beta" / "SKILL.md").write_text("hand edited", encoding="utf-8")  # 人工改动安装副本
        self.fake({"version": "5.11.3", "updates": [["alpha", "Alpha"], ["beta", "Beta"], ["mine", "Mine"], ["dup", "Dup"]]})
        code, d = run(self.dir, "check-updates", expect=0)
        plan = json.loads(Path(d["plan"]).read_text(encoding="utf-8"))
        items = {i["directory"]: i for i in plan["items"]}
        self.assertEqual(items["alpha"]["problems"], []); self.assertFalse(items["alpha"]["selected"])
        self.assertIn("local_modified", items["beta"]["problems"])
        self.assertEqual(items["mine"]["route"], "custom-sync"); self.assertIn("custom_repo_use_sync_script", items["mine"]["problems"])
        self.assertIn("ambiguous_directory", items["dup"]["problems"])
        self.assertTrue((Path(d["backup"]) / "cc-switch.db").is_file())
        self.assertEqual({k: d["summary"][k] for k in ("cli_update", "custom_sync", "blocked")}, {"cli_update": 1, "custom_sync": 1, "blocked": 2})

    def test_update_scope_dry_run_confirm_and_readback(self):
        code, d = run(self.dir, "update", "--skills", "alpha", expect=0)
        self.assertTrue(d["dry_run"]); self.assertFalse(any("update" in l for l in self.log()))
        before_beta = self.row("beta")
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=0)
        res = d["results"][0]
        self.assertEqual(res["status"], "updated"); self.assertTrue(res["hash_changed"]); self.assertTrue(res["installed_hash_matches_db"])
        self.assertEqual(res["enabled_after"], res["enabled_before"])
        self.assertEqual([l for l in self.log() if l.startswith("skills update")], ["skills update alpha"])  # 精确单项，没有 --all
        self.assertEqual(self.row("beta"), before_beta)  # 范围外不动
        self.assertTrue((Path(d["backup"]) / "installed-before" / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha v1")
        self.assertTrue((Path(d["backup"]) / "cc-switch.db").is_file())
        self.assertEqual(res["clients"].get("codex"), "ok")

    def test_client_directory_not_synced_is_partial_not_success(self):
        self.fake({"version": "5.11.3", "behavior": {"alpha": "no_client_sync"}})
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=1)
        self.assertEqual(d["results"][0]["status"], "partial"); self.assertEqual(d["results"][0]["clients"]["codex"], "hash_mismatch")

    def test_refusals_are_whole_batch_and_make_no_calls(self):
        for skills, problem in (("alpha,mine", "custom_repo_use_sync_script"), ("alpha,dup", "ambiguous_directory"), ("alpha,ghost", "not_in_database")):
            code, d = run(self.dir, "update", "--skills", skills, "--confirm", expect=3)
            self.assertEqual(d["verdict"], "scope_refused"); self.assertIn(problem, json.dumps(d["refused"]))
        (self.skills / "alpha" / "SKILL.md").write_text("hand edited", encoding="utf-8")
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertIn("local_modified", json.dumps(d["refused"]))
        self.assertFalse(any(l.startswith("skills update") for l in self.log()))
        self.assertFalse((self.dir / "skill-backups").exists())

    def test_plan_selection_and_drift_since_plan(self):
        self.fake({"version": "5.11.3", "updates": [["alpha", "Alpha"], ["beta", "Beta"]]})
        code, d = run(self.dir, "check-updates", expect=0)
        plan_path = Path(d["plan"]); plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["items"][0]["selected"] = True; plan_path.write_text(json.dumps(plan), encoding="utf-8")
        # 计划之后安装副本被改动（并发漂移）-> 拒绝
        folder = self.skills / "alpha"; (folder / "SKILL.md").write_text("changed after plan", encoding="utf-8")
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute("update skills set content_hash=? where directory='alpha'", (ctl.dir_hash(folder),)); db.commit(); db.close()
        code, d = run(self.dir, "update", "--plan", plan_path, "--confirm", expect=3)
        self.assertIn("drifted_since_plan", json.dumps(d["refused"]))

    def test_disabled_backend_stays_disabled_and_drift_stops_batch(self):
        self.fake({"version": "5.11.3", "behavior": {"alpha": "drift"}})
        code, d = run(self.dir, "update", "--skills", "alpha,beta", "--confirm", expect=1)
        self.assertEqual(d["results"][0]["status"], "drift"); self.assertEqual(d["not_attempted"], ["beta"])
        self.assertEqual(self.row("backend-only")["enabled_codex"], 0)

    def test_failed_item_is_recorded_not_retried_and_next_item_continues(self):
        self.fake({"version": "5.11.3", "behavior": {"alpha": "fail"}})
        code, d = run(self.dir, "update", "--skills", "alpha,beta", "--confirm", expect=1)
        self.assertEqual([r["status"] for r in d["results"]], ["failed", "updated"])
        self.assertEqual(self.log().count("skills update alpha"), 1)

    def test_stale_hash_after_update_is_unverified(self):
        self.fake({"version": "5.11.3", "behavior": {"alpha": "stale_hash"}})
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=1)
        self.assertEqual(d["results"][0]["status"], "unverified")

    def test_timeout_is_unknown_and_stops(self):
        self.fake({"version": "5.11.3", "behavior": {"alpha": "timeout"}})
        code, d = run(self.dir, "update", "--skills", "alpha,beta", "--confirm", "--timeout", 1, expect=4)
        self.assertEqual(d["results"][0]["status"], "unknown"); self.assertEqual(d["not_attempted"], ["beta"])

    def test_verify_reads_back_clients(self):
        code, d = run(self.dir, "verify", "--skills", "alpha,beta", expect=0)
        self.assertEqual(d["problems"], 0)
        (self.codex / "alpha" / "SKILL.md").write_text("tampered", encoding="utf-8")
        code, d = run(self.dir, "verify", "--skills", "alpha", expect=1)
        self.assertEqual(d["details"][0]["clients"]["codex"], "hash_mismatch")

    def test_desktop_running_requires_explicit_override(self):
        env = ctl.Env(ctl.build_parser().parse_args(["--config-dir", str(self.dir), "--cli", str(FAKE), "--desktop-exe", str(self.dir / "no.exe"), "doctor"]))
        env.isolated = False  # 让门禁走桌面检测分支（CLI 仍通过环境变量指向影子目录）
        os.environ["CC_SWITCH_CONFIG_DIR"] = str(self.dir)
        self.addCleanup(os.environ.pop, "CC_SWITCH_CONFIG_DIR", None)
        original = ctl.desktop_running
        ctl.desktop_running = lambda: True
        try:
            with self.assertRaises(ctl.Gate) as ctx:
                ctl.require_ready(env, write=True)
            self.assertEqual(ctx.exception.verdict, "desktop_running")
            env.allow_desktop_running = True
            self.assertEqual(ctl.require_ready(env, write=True)["verdict"], "ready")
        finally:
            ctl.desktop_running = original


def make_link(link: Path, target: Path) -> str:
    """Windows 上建真实目录联接(junction，评审复现的场景)；其他平台建符号链接。返回实际类型。"""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        assert ctl.is_reparse(link) and not os.path.islink(link) or os.path.islink(link)
        return "junction"
    os.symlink(target, link, target_is_directory=True)
    return "symlink"


REAL_CHECK_STDERR = (
    "Error: Update check incomplete:\n"
    "anthropics/skills: 下载失败: error sending request for url (https://github.com/anthropics/skills/archive/refs/heads/main.zip) (Download failed: error sending request for url (https://github.com/anthropics/skills/archive/refs/heads/main.zip))\n"
    "cloudflare/skills: 读取下载内容失败: error decoding response body (Failed to read download bytes: error decoding response body)\n"
    "openai/skills:skills\\.curated\\doc: Remote Skill directory 'doc' is not found in openai/skills\n"
)


class Hardening(Fixture):
    """评审 R1–R4 的独立失败样例：每条都先于修复会得到错误的成功。"""

    def outside(self, content="OUTSIDE"):
        out = self.dir / "outside"; out.mkdir(); (out / "SKILL.md").write_text(content, encoding="utf-8")
        return out

    def update_calls(self):
        return [l for l in self.log() if l.startswith("skills update")]

    # R1
    def test_installed_copy_as_link_is_refused_and_target_untouched(self):
        out = self.outside()
        import shutil
        shutil.rmtree(self.skills / "alpha"); make_link(self.skills / "alpha", out)
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute("update skills set content_hash=? where directory='alpha'", (ctl.dir_hash(out),)); db.commit(); db.close()
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertIn("reparse_point", json.dumps(d["refused"])); self.assertEqual(self.update_calls(), [])
        self.assertEqual((out / "SKILL.md").read_text(encoding="utf-8"), "OUTSIDE")
        code, d = run(self.dir, "verify", "--skills", "alpha", expect=1)
        self.assertIn("reparse_point", json.dumps(d["details"]))

    def test_child_link_inside_installed_tree_is_refused(self):
        out = self.outside()
        make_link(self.skills / "alpha" / "a" / "escape", out)
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertIn("reparse_point", json.dumps(d["refused"])); self.assertEqual(self.update_calls(), [])
        self.assertEqual(os.listdir(out), ["SKILL.md"])
        with self.assertRaises(ctl.ReparseError):
            ctl.dir_hash(self.skills / "alpha")

    def test_client_directory_link_is_refused_before_cli_can_write_through(self):
        out = self.outside()
        import shutil
        shutil.rmtree(self.codex / "alpha"); make_link(self.codex / "alpha", out)
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=3)
        self.assertIn("client_reparse_point:codex", json.dumps(d["refused"])); self.assertEqual(self.update_calls(), [])
        self.assertEqual(os.listdir(out), ["SKILL.md"])

    def test_plan_marks_link_items_blocked(self):
        out = self.outside()
        make_link(self.skills / "alpha" / "a" / "escape", out)
        self.fake({"version": "5.11.3", "updates": [["alpha", "Alpha"]]})
        code, d = run(self.dir, "check-updates", expect=0)
        item = json.loads(Path(d["plan"]).read_text(encoding="utf-8"))["items"][0]
        self.assertTrue(any(p.startswith("reparse_point") for p in item["problems"]), item)

    # R2
    def plan_for(self, *names):
        self.fake({"version": "5.11.3", "updates": [[n, n] for n in names]})
        code, d = run(self.dir, "check-updates", expect=0)
        path = Path(d["plan"]); plan = json.loads(path.read_text(encoding="utf-8"))
        for item in plan["items"]:
            item["selected"] = True
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def test_source_identity_change_with_same_bytes_rejects_old_plan(self):
        plan = self.plan_for("alpha")
        db = sqlite3.connect(self.dir / "cc-switch.db")
        db.execute("update skills set id='another-org/other-source:alpha', repo_owner='another-org', repo_name='other-source' where directory='alpha'")
        db.commit(); db.close()
        code, d = run(self.dir, "update", "--plan", plan, "--confirm", expect=3)
        self.assertIn("source_identity_drift", json.dumps(d["refused"]))
        for field in ("id", "repo_owner", "repo_name"):
            self.assertIn(field, json.dumps(d["refused"]))
        self.assertEqual(self.update_calls(), []); self.assertEqual(list((self.dir / "skill-backups").glob("*-cli-update")), [])

    def test_other_identity_fields_and_switch_changes_reject_plan(self):
        for column, value in (("repo_branch", "dev"), ("readme_url", "https://example.invalid/x"), ("updated_at", 99), ("installed_at", 7), ("enabled_gemini", 1)):
            with self.subTest(column=column):
                plan = self.plan_for("alpha")
                db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute(f"update skills set {column}=? where directory='alpha'", (value,)); db.commit(); db.close()
                code, d = run(self.dir, "update", "--plan", plan, "--confirm", expect=3)
                self.assertTrue("source_identity_drift" in json.dumps(d["refused"]) or "enabled_changed_since_plan" in json.dumps(d["refused"]), d)
                self.assertEqual(self.update_calls(), [])
                db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute("update skills set repo_branch='main', readme_url='', updated_at=1, installed_at=1, enabled_gemini=0 where directory='alpha'"); db.commit(); db.close()

    def test_plan_without_frozen_identity_is_refused(self):
        plan = self.plan_for("alpha")
        data = json.loads(plan.read_text(encoding="utf-8")); [i.pop("identity") for i in data["items"]]; plan.write_text(json.dumps(data), encoding="utf-8")
        code, d = run(self.dir, "update", "--plan", plan, "--confirm", expect=3)
        self.assertIn("plan_identity_missing", json.dumps(d["refused"]))

    def test_unchanged_plan_still_executes(self):
        plan = self.plan_for("alpha")
        code, d = run(self.dir, "update", "--plan", plan, "--confirm", expect=0)
        self.assertEqual(d["results"][0]["status"], "updated")

    # R3
    def test_verify_requires_every_selected_item(self):
        code, d = run(self.dir, "verify", "--skills", "ghost", expect=1)
        self.assertEqual((d["checked"], d["problems"], d["verdict"]), (1, 1, "problems")); self.assertIn("not_in_database", json.dumps(d))
        code, d = run(self.dir, "verify", "--skills", "alpha,ghost", expect=1)
        self.assertEqual((d["checked"], d["problems"]), (2, 1))
        code, d = run(self.dir, "verify", "--skills", "dup", expect=1); self.assertIn("ambiguous_directory", json.dumps(d))
        code, d = run(self.dir, "verify", "--skills", "alpha", expect=0); self.assertEqual(d["verdict"], "verified")

    # R4
    def enable(self, directory, column):
        db = sqlite3.connect(self.dir / "cc-switch.db"); db.execute(f"update skills set {column}=1 where directory=?", (directory,)); db.commit(); db.close()

    def test_enabled_but_unmapped_client_is_partial_not_updated(self):
        self.enable("alpha", "enabled_mcode")
        code, d = run(self.dir, "update", "--skills", "alpha", "--confirm", expect=1)
        res = d["results"][0]
        self.assertEqual(res["status"], "partial"); self.assertEqual(res["clients"]["mcode"], "unmapped"); self.assertEqual(res["clients"]["codex"], "ok")
        self.assertEqual(res["unverified_clients"], {"mcode": "unmapped"})
        self.assertEqual(res["enabled_after"], res["enabled_before"])  # 不靠关开关来消除问题
        self.assertEqual(self.row("alpha")["enabled_mcode"], 1)
        code, d = run(self.dir, "verify", "--skills", "alpha", expect=1); self.assertIn("client_mcode_unmapped", json.dumps(d))

    def test_explicit_mapping_for_extra_client_is_verified(self):
        self.enable("alpha", "enabled_mcode")
        mcode = self.dir / "client-mcode" / "skills"; mcode.mkdir(parents=True)
        import shutil
        shutil.copytree(self.skills / "alpha", mcode / "alpha")
        base = [sys.executable, "-I", "-B", str(CONTROL), "--config-dir", str(self.dir), "--cli", str(FAKE), "--client-dir", f"codex={self.codex}",
                "--client-dir", f"mcode={mcode}"]
        done = subprocess.run([*base, "update", "--skills", "alpha", "--confirm"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stdout); self.assertEqual(json.loads(done.stdout)["results"][0]["clients"], {"codex": "ok", "mcode": "ok"})

    def test_stale_mapped_client_dir_is_not_success(self):
        self.enable("alpha", "enabled_mcode")
        mcode = self.dir / "client-mcode" / "skills"; mcode.mkdir(parents=True)  # 目录存在但没有这个 skill / 内容陈旧
        base = [sys.executable, "-I", "-B", str(CONTROL), "--config-dir", str(self.dir), "--cli", str(FAKE), "--client-dir", f"codex={self.codex}", "--client-dir", f"mcode={mcode}"]
        (self.dir / "fake-cli.json").write_text(json.dumps({"version": "5.11.3", "behavior": {"alpha": "no_mcode_sync"}}), encoding="utf-8")
        done = subprocess.run([*base, "update", "--skills", "alpha", "--confirm"], capture_output=True, text=True, encoding="utf-8")
        res = json.loads(done.stdout)["results"][0]
        self.assertEqual(done.returncode, 1); self.assertEqual(res["status"], "partial"); self.assertEqual(res["clients"]["mcode"], "missing")

    # check-updates partial
    def test_real_check_failures_are_partial_classified_and_block_only_dependents(self):
        self.db.execute(f"insert into skills({COLUMNS}) values('openai/skills:doc','doc','','doc','openai','skills','main','',0,0,1,0,1,'h',1,0,0,0,0,0)"); self.db.commit()
        self.fake({"version": "5.11.3", "updates": [], "check_failures": REAL_CHECK_STDERR.splitlines()[1:]})
        code, d = run(self.dir, "check-updates", expect=1)
        self.assertEqual((d["outcome"], d["complete"]), ("partial", False)); self.assertIn("不能据此认为", d["note"])
        kinds = {(f["repo"], f["directory"], f["kind"]) for f in d["check_failures"]}
        self.assertEqual(kinds, {("anthropics/skills", None, "download_failed"), ("cloudflare/skills", None, "download_failed"), ("openai/skills", "doc", "upstream_directory_missing")})
        self.assertEqual(d["summary"]["download_failed"], 2); self.assertEqual(d["summary"]["upstream_directory_missing"], 1); self.assertEqual(d["summary"]["cli_update"], 0)
        plan = json.loads(Path(d["plan"]).read_text(encoding="utf-8")); self.assertFalse(plan["complete"])
        items = {i["directory"]: i for i in plan["items"]}
        self.assertEqual(set(items), {"beta", "backend-only", "doc"})  # alpha（同属 openai/skills 但目录仍在）不受牵连
        self.assertIn("check_download_failed", items["beta"]["problems"]); self.assertIn("check_upstream_directory_missing", items["doc"]["problems"])
        self.assertTrue(all(i["route"] == "unchecked" and not i["selected"] for i in items.values()))
        self.assertEqual(self.row("doc")["enabled_gemini"], 1)  # 不删除、不停用旧目录
        for i in items.values():
            i["selected"] = True
        path = Path(d["plan"]); path.write_text(json.dumps(plan), encoding="utf-8")
        code, d = run(self.dir, "update", "--plan", path, "--confirm", expect=3); self.assertEqual(self.update_calls(), [])

    def test_unparsable_failure_is_never_reported_as_up_to_date(self):
        self.fake({"version": "5.11.3", "updates": [], "check_failures": ["something odd"]})
        code, d = run(self.dir, "check-updates", expect=1)
        self.assertEqual((d["outcome"], d["complete"], d["updates"]), ("partial", False, 0)); self.assertEqual(d["check_failures"][0]["kind"], "unclassified")

    def test_complete_check_with_updates_and_with_none(self):
        self.fake({"version": "5.11.3", "updates": []})
        code, d = run(self.dir, "check-updates", expect=0); self.assertEqual((d["outcome"], d["complete"], d["updates"]), ("complete", True, 0))

    def test_classifier_on_real_evidence_file_when_present(self):
        tail = "工作台管理报告/能力整合续验与MCP评估-20261024/prune-v3/cc-switch-upgrade/PRODUCTION_check_updates.stderr.txt"
        for root in ("/mnt/d/12070/Documents", "D:/12070/Documents"):
            try:
                real = Path(root) / tail
                if real.is_file():
                    break
            except OSError:
                continue
        else:
            self.skipTest("真实证据文件不在本机")
        got = ctl.classify_check_failures(real.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(sorted(f["kind"] for f in got), ["download_failed", "download_failed", "upstream_directory_missing"])


if __name__ == "__main__":
    unittest.main()
