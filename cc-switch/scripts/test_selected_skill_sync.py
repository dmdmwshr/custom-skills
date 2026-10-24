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
        source, install, codex, gemini = [root / name for name in ("source", "installed", "codex", "gemini")]
        for path in (source, install, codex, gemini):
            path.mkdir()
        names = ("fixture-one", "fixture-two", "fixture-new")
        for name in names:
            for parent in (source, install, codex, gemini):
                if name == "fixture-new" and parent != source:
                    continue
                (parent / name).mkdir()
                value = "new" if parent == source else "old"
                (parent / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {value}\n---\n{value}\n", encoding="utf-8")
                if parent == source and name != "fixture-two":  # 排序/隐藏文件边界：a/b 与 a-b/c、中文名、点开头文件
                    for relative, data in (("a/b.txt", "b"), ("a-b/c.txt", "c"), ("中文.md", "文"), (".hidden", "h")):
                        target = parent / name / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(data, encoding="utf-8")
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
                enabled_hermes INTEGER,enabled_grokbuild INTEGER,content_hash TEXT,
                enabled_mcode INTEGER NOT NULL DEFAULT 0,enabled_agy INTEGER NOT NULL DEFAULT 0,
                enabled_kimi INTEGER NOT NULL DEFAULT 0)""")
            for name in names[:2]:
                db.execute("INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"dmdmwshr/custom-skills:{name}", name, "old", name, "dmdmwshr", "custom-skills", "main", "", 0, int(name == "fixture-one"), 1, 0, 1, 1, int(name == "fixture-two"), 0, "preserved-hash", int(name == "fixture-two"), int(name == "fixture-two"), int(name == "fixture-two")))
        (root / "settings.json").write_text(json.dumps({"skillSyncMethod": "copy"}), encoding="utf-8")
        command = [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-File", str(script),
            "-SourceRoot", str(source), "-InstallRoot", str(install), "-CodexSkillsRoot", str(codex), "-GeminiSkillsRoot", str(gemini),
            "-DatabasePath", str(database), "-PythonPath", sys.executable, "-SkipRemotePull", "-Skill", "fixture-one"]
        for dry in (True, False):
            result = subprocess.run(command + (["-WhatIf"] if dry else []), capture_output=True, text=True, encoding="utf-8", timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        with closing(sqlite3.connect(database)) as db, db:
            assert db.execute("SELECT description,updated_at,content_hash FROM skills WHERE name='fixture-two'").fetchone() == ("old", 1, "preserved-hash")
            assert db.execute("SELECT description,content_hash FROM skills WHERE name='fixture-one'").fetchone() == ("new", "preserved-hash")
            switches = "enabled_claude,enabled_codex,enabled_gemini,enabled_opencode,enabled_hermes,enabled_grokbuild,enabled_mcode,enabled_agy,enabled_kimi"
            assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-one'").fetchone() == (0, 1, 1, 0, 0, 0, 0, 0, 0)
            assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-two'").fetchone() == (0, 0, 1, 0, 1, 0, 1, 1, 1)
        for parent in (install, codex, gemini):
            assert (parent / "fixture-one/SKILL.md").read_bytes() == (source / "fixture-one/SKILL.md").read_bytes()
            assert "description: old" in (parent / "fixture-two/SKILL.md").read_text(encoding="utf-8")
        new_command = command[:-1] + ["fixture-new"]
        result = subprocess.run(new_command + ["-WhatIf"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT count(*) FROM skills WHERE name='fixture-new'").fetchone() == (0,)
        assert not (install / "fixture-new").exists() and not (codex / "fixture-new").exists()
        result = subprocess.run(new_command, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        with closing(sqlite3.connect(database)) as db:
            assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-new'").fetchone() == (0, 1, 0, 0, 0, 0, 0, 0, 0)
            assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-one'").fetchone() == (0, 1, 1, 0, 0, 0, 0, 0, 0)
            assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-two'").fetchone() == (0, 0, 1, 0, 1, 0, 1, 1, 1)
        for parent in (install, codex):
            assert (parent / "fixture-new/SKILL.md").read_bytes() == (source / "fixture-new/SKILL.md").read_bytes()
            assert "description: old" in (parent / "fixture-two/SKILL.md").read_text(encoding="utf-8")
        # 文档里的多技能写法：-Command 才能传 -Skill 列表（-File 会把逗号列表当成一个字符串）
        multi = subprocess.run([command[0], "-NoProfile", "-Command",
            f"& '{script}' -SourceRoot '{source}' -InstallRoot '{install}' -CodexSkillsRoot '{codex}' -GeminiSkillsRoot '{gemini}' -DatabasePath '{database}' "
            f"-PythonPath '{sys.executable}' -SkipRemotePull -Skill 'fixture-one','fixture-new' -WhatIf"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        assert multi.returncode == 0 and "Dry-run" in multi.stdout, multi.stdout + multi.stderr
        missing_root = subprocess.run([command[0], "-NoProfile", "-File", str(script), "-WhatIf"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        assert missing_root.returncode != 0  # -SourceRoot 必填，不会默认落到旧检出
        # content_hash：未核验结构（user_version=0）保持旧行为（上面已断言保留）；核验结构 21 与 22 都按上游算法写入
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cc_switch_control import dir_hash
        for schema in (21, 22):
            with closing(sqlite3.connect(database)) as db, db:
                db.execute(f"PRAGMA user_version={schema}")
                db.execute("UPDATE skills SET content_hash='preserved-hash' WHERE name IN ('fixture-one','fixture-new')")
            for run in (command, new_command):
                result = subprocess.run(run, capture_output=True, text=True, encoding="utf-8", timeout=60)
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                assert "content_hash_written=1" in result.stdout, (schema, result.stdout)
            with closing(sqlite3.connect(database)) as db:
                for name in ("fixture-one", "fixture-new"):
                    stored = db.execute("SELECT content_hash FROM skills WHERE name=?", (name,)).fetchone()[0]
                    assert stored == dir_hash(install / name), (schema, name)
                    assert stored == dir_hash(codex / name), (schema, name)
                    if name == "fixture-one": assert stored == dir_hash(gemini / name), (schema, name)
                assert db.execute("SELECT content_hash FROM skills WHERE name='fixture-two'").fetchone() == ("preserved-hash",)
                # 9 个 enabled_* 开关（含 schema 22 新增的 kimi）及未知既有开关不被改动
                assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-one'").fetchone() == (0, 1, 1, 0, 0, 0, 0, 0, 0), schema
                assert db.execute(f"SELECT {switches} FROM skills WHERE name='fixture-two'").fetchone() == (0, 0, 1, 0, 1, 0, 1, 1, 1), schema
        # 未核验的结构版本（23）：不写入 hash，且明确提示
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("PRAGMA user_version=23")
            db.execute("UPDATE skills SET content_hash='preserved-hash' WHERE name='fixture-one'")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0 and "content_hash_written=0" in result.stdout and "content_hash_not_written schema=23" in result.stdout, result.stdout
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT content_hash FROM skills WHERE name='fixture-one'").fetchone() == ("preserved-hash",)
        # No isolated invocation may silently inherit the real Gemini path.
        no_gemini = command.copy(); index = no_gemini.index("-GeminiSkillsRoot"); del no_gemini[index:index+2]
        before = (install / "fixture-one/SKILL.md").read_bytes()
        result = subprocess.run(no_gemini, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode != 0 and "explicit -GeminiSkillsRoot" in result.stderr + result.stdout
        assert (install / "fixture-one/SKILL.md").read_bytes() == before
        print("Scoped publication passed: new registrations enable only Codex; existing switches/hash and unrelated files preserved; dry-run unchanged.")


if __name__ == "__main__":
    main()
