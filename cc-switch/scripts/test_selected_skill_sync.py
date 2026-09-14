"""Exercise scoped Windows publication entirely inside an isolated fixture."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def main():
    if os.name != "nt":
        print("Windows publisher fixture requires Windows; no host state touched.")
        return
    script = Path(__file__).with_name("sync-custom-skills.ps1")
    with tempfile.TemporaryDirectory(prefix="selected-skill-sync-") as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        source, install, codex = [root / name for name in ("source", "installed", "codex")]
        for path in (source, install, codex):
            path.mkdir()
        names = ("fixture-one", "fixture-two")
        for name in names:
            for parent in (source, install, codex):
                (parent / name).mkdir()
                value = "new" if parent == source else "old"
                (parent / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {value}\n---\n{value}\n", encoding="utf-8")
        def git(*args):
            return subprocess.run(["git", *args], cwd=source, check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-b", "main")
        git("add", ".")
        git("-c", "user.name=dmdmwshr", "-c", "user.email=120701253@qq.com", "commit", "-m", "fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        database = root / "cc-switch.db"
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE skill_repos(owner TEXT,name TEXT,branch TEXT,enabled INTEGER)")
            db.execute("INSERT INTO skill_repos VALUES ('dmdmwshr','custom-skills','main',1)")
            db.execute("""CREATE TABLE skills(id TEXT PRIMARY KEY,name TEXT,description TEXT,directory TEXT,repo_owner TEXT,
                repo_name TEXT,repo_branch TEXT,readme_url TEXT,enabled_claude INTEGER,enabled_codex INTEGER,
                enabled_gemini INTEGER,enabled_opencode INTEGER,installed_at INTEGER,updated_at INTEGER,
                enabled_hermes INTEGER,enabled_grokbuild INTEGER,content_hash TEXT)""")
            for name in names:
                db.execute("INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"dmdmwshr/custom-skills:{name}", name, "old", name, "dmdmwshr", "custom-skills", "main", "", 1, 1, 1, 1, 1, 1, 0, 0, "preserved-hash"))
        (root / "settings.json").write_text(json.dumps({"skillSyncMethod": "copy"}), encoding="utf-8")
        command = [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-File", str(script),
            "-SourceRoot", str(source), "-InstallRoot", str(install), "-CodexSkillsRoot", str(codex),
            "-DatabasePath", str(database), "-PythonPath", sys.executable, "-SkipRemotePull", "-Skill", "fixture-one"]
        for dry in (True, False):
            result = subprocess.run(command + (["-WhatIf"] if dry else []), capture_output=True, text=True, encoding="utf-8", timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        with closing(sqlite3.connect(database)) as db, db:
            assert db.execute("SELECT description,updated_at,content_hash FROM skills WHERE name='fixture-two'").fetchone() == ("old", 1, "preserved-hash")
            assert db.execute("SELECT description,content_hash FROM skills WHERE name='fixture-one'").fetchone() == ("new", "preserved-hash")
        for parent in (install, codex):
            assert (parent / "fixture-one/SKILL.md").read_bytes() == (source / "fixture-one/SKILL.md").read_bytes()
            assert "description: old" in (parent / "fixture-two/SKILL.md").read_text(encoding="utf-8")
        print("Scoped publication passed: selected files/registration/discovery copy updated; unrelated skill unchanged; backups created.")


if __name__ == "__main__":
    main()
