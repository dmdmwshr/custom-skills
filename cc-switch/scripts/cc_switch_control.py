#!/usr/bin/env python3
"""CC Switch 的 CLI 控制助手：状态/兼容检查、检查更新、生成可审阅清单、按范围更新、备份与回读。

只用 Python 标准库。真正的更新由社区 CLI（SaladDay/cc-switch-cli）完成，本脚本只做：
  版本与数据库结构门禁 -> 备份 -> 逐项调用原生 CLI -> 回读（启用开关、content_hash、客户端目录）。
自建源 dmdmwshr/custom-skills 不走本脚本更新，沿 sync-custom-skills.ps1。

退出码：0 成功；1 失败/部分失败；2 参数错误；3 门禁拒绝（版本/结构不兼容、需协调升级、范围不允许）；4 结果未知。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPAT_FILE = HERE / "cc-switch-compat.json"
CUSTOM_OWNER, CUSTOM_REPO = "dmdmwshr", "custom-skills"
DEFAULT_WIN_HOME = Path("C:/Users/12070")
STABLE_CLI = "C:/Users/12070/.local/share/codex-tools/cc-switch-cli/current/cc-switch-cli.exe"
DESKTOP_EXE = "C:/Users/12070/AppData/Local/Programs/CC Switch/cc-switch.exe"
CLIENTS = ("claude", "codex", "gemini", "opencode", "hermes", "grokbuild", "mcode", "agy", "kimi")
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_GATE, EXIT_UNKNOWN = 0, 1, 2, 3, 4


class Gate(Exception):
    """版本/结构/范围门禁拒绝，未执行任何写入。"""

    def __init__(self, verdict, message, **extra):
        super().__init__(message)
        self.verdict, self.extra = verdict, extra


# ---------------------------------------------------------------- 基础
def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def emit(value) -> None:
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sys.stdout.flush()


def default_home() -> Path:
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    if DEFAULT_WIN_HOME.exists():
        return DEFAULT_WIN_HOME
    wsl = Path("/mnt/c/Users/12070")
    return wsl if wsl.exists() else Path.home()


def host_path(value) -> str:
    """WSL 里把 C:/... 形式的 Windows 路径映射到 /mnt/c/...；Windows 与其他形式原样返回。"""
    text = str(value)
    match = re.match(r"^([A-Za-z]):[/\\](.*)$", text)
    if match and os.name != "nt":
        return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"
    return text


def load_compat() -> dict:
    return json.loads(COMPAT_FILE.read_text(encoding="utf-8"))


class ReparseError(Exception):
    """目录树里出现符号链接/目录联接/重解析点：不计算摘要、不允许写入穿越。"""

    def __init__(self, paths):
        super().__init__("reparse point: " + ", ".join(paths[:5]))
        self.paths = paths


def is_reparse(path) -> bool:
    """符号链接、Windows 目录联接(junction)及其他重解析点。不跟随链接，只看 lstat。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.path.islink(path):
        return True
    if getattr(st, "st_file_attributes", 0) & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def find_reparse(root: Path) -> list[str]:
    """根及其所有子项里的重解析点（相对路径，根本身记为 "."）。只用 lstat，不跟随。"""
    found = []
    if is_reparse(root):
        return ["."]

    def walk(current: str) -> None:
        with os.scandir(current) as it:
            for entry in it:
                if is_reparse(entry.path):
                    found.append(os.path.relpath(entry.path, root).replace(os.sep, "/"))
                elif entry.is_dir(follow_symlinks=False):
                    walk(entry.path)

    if Path(root).is_dir():
        walk(str(root))
    return found


def same_path(left, right) -> bool:
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def inside(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(os.path.realpath(parent))
        return True
    except ValueError:
        return False


def dir_hash(root: Path) -> str:
    """CC Switch 上游 compute_dir_hash：递归所有非点开头文件/目录，按路径组件字节序排序，
    逐文件 feed 相对路径(/)+NUL+原始字节+NUL。已与本机 50 个自建安装副本的库内摘要逐项核对。
    树里有重解析点时拒绝（ReparseError），不跟随链接。"""
    bad = find_reparse(Path(root))
    if bad:
        raise ReparseError(bad)
    files = []

    def walk(current: Path) -> None:
        for entry in os.scandir(current):
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                walk(Path(entry.path))
            else:
                files.append(Path(entry.path))

    walk(Path(root))
    files.sort(key=lambda p: [part.encode("utf-8") for part in p.relative_to(root).parts])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def safe_hash(folder: Path):
    """(摘要或 None, 问题或 None)。问题：missing / reparse_point。"""
    if not os.path.lexists(folder):
        return None, "missing"
    try:
        return (dir_hash(folder) if Path(folder).is_dir() else None), (None if Path(folder).is_dir() else "missing")
    except ReparseError as exc:
        return None, "reparse_point:" + ",".join(exc.paths[:3])


def connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def backup_db(db: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = connect_ro(db)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def skill_rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("select * from skills order by id")]


def enabled_map(row: dict) -> dict:
    return {k.removeprefix("enabled_"): int(v or 0) for k, v in row.items() if k.startswith("enabled_")}


# ---------------------------------------------------------------- 环境探测
class Env:
    def __init__(self, args):
        live_home = default_home()
        self.config_dir = Path(args.config_dir) if args.config_dir else live_home / ".cc-switch"
        self.isolated = not same_path(self.config_dir, live_home / ".cc-switch")
        live_roots = [live_home / rel for rel in (".cc-switch", ".codex", ".claude", ".gemini", ".agents", ".config/opencode", ".hermes", ".openclaw", ".pi", ".kimi", ".mcode")]
        if self.isolated and any(inside(self.config_dir, root) or inside(root, self.config_dir) for root in live_roots):
            raise Gate("isolated_root_overlaps_production", "隔离配置目录与真实客户端/CC状态重叠；未执行CLI")
        self.db = Path(args.db) if args.db else self.config_dir / "cc-switch.db"
        self.ssot = Path(args.ssot_root) if args.ssot_root else self.config_dir / "skills"
        if not same_path(self.db, self.config_dir / "cc-switch.db") or not same_path(self.ssot, self.config_dir / "skills"):
            raise Gate("cli_state_path_mismatch", "--db 和 --ssot-root 必须与 CLI 配置目录下的 cc-switch.db / skills 一致；未执行 CLI")
        self.backup_root = Path(args.backup_root) if args.backup_root else self.config_dir / "skill-backups"
        self.cli = host_path(args.cli or os.environ.get("CC_SWITCH_CLI") or STABLE_CLI)
        self.desktop = host_path(args.desktop_exe or DESKTOP_EXE)
        self.home = self.config_dir / "isolated-home" if self.isolated else live_home
        home = self.home
        self.client_dirs = {
            "claude": home / ".claude" / "skills", "codex": home / ".codex" / "skills",
            "gemini": home / ".gemini" / "skills", "opencode": home / ".config" / "opencode" / "skills",
            "grokbuild": home / ".grok" / "skills", "agy": home / ".gemini" / "config" / "skills",
        }
        self.client_blocked = {}
        settings = self.config_dir / "settings.json"
        try:
            self.settings = json.loads(settings.read_text(encoding="utf-8-sig")) if settings.is_file() else {}
        except (OSError, ValueError):
            raise Gate("settings_unreadable", "无法读取 CLI 设置；不能判断其实际目录")
        if self.settings.get("skillStorageLocation", "cc_switch") != "cc_switch":
            raise Gate("storage_location_unsupported", "当前仅核验 cc_switch Skill 存储位置，其他位置不调用 CLI 写入")
        self.settings_keys = {"claude": "claudeConfigDir", "codex": "codexConfigDir", "gemini": "geminiConfigDir",
                              "opencode": "opencodeConfigDir", "hermes": "hermesConfigDir",
                              "openclaw": "openclawConfigDir", "pi": "piConfigDir"}
        if not self.isolated:
            for client, key in self.settings_keys.items():
                value = self.settings.get(key)
                if value:
                    path = Path(host_path(value))
                    if not path.is_absolute():
                        raise Gate("relative_client_override_unverified", "客户端覆盖目录不是已核绝对路径：" + key)
                    self.client_dirs[client] = path / "skills"
            # Native CLI gives these environment variables precedence over settings.
            for client, key in (("codex", "CODEX_HOME"), ("claude", "CLAUDE_CONFIG_DIR")):
                if os.environ.get(key): self.client_dirs[client] = Path(os.environ[key]) / "skills"
        else:
            for client in self.settings_keys:
                self.client_dirs.setdefault(client, home / client / "skills")
        for item in args.client_dir or []:
            name, separator, value = item.partition("=")
            if not separator or not value: raise Gate("client_override_invalid", "--client-dir 应为 CLIENT=PATH")
            path = Path(value)
            if self.isolated:
                if not inside(path, self.config_dir):
                    raise Gate("isolated_client_outside_root", "隔离客户端必须位于 --config-dir 内；未执行 CLI")
            elif name not in self.client_dirs or not same_path(path, self.client_dirs[name]):
                raise Gate("client_override_mismatch", "生产 --client-dir 只能确认 CLI 已配置的实际目录，不能另指校验对象")
            if path.name != "skills":
                raise Gate("client_skill_dir_invalid", "--client-dir 必须是客户端配置根下的 skills 目录，以匹配原生CLI的固定拼接")
            self.client_dirs[name] = path
        if self.isolated:
            for path in self.client_dirs.values():
                if not inside(path, self.config_dir) or inside(path, live_home / ".codex") or inside(path, live_home / ".cc-switch"):
                    raise Gate("isolated_client_outside_root", "隔离客户端目录越界；未执行 CLI")
        if not self.isolated and os.environ.get("CC_SWITCH_TEST_HOME") and not same_path(os.environ["CC_SWITCH_TEST_HOME"], live_home):
            raise Gate("production_test_home_override", "生产环境检测到另一测试home，不能确定CLI写入对象；未执行CLI")
        self.allow_desktop_running = args.allow_desktop_running
        self.compat = load_compat()

    def cli_env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["CC_SWITCH_CONFIG_DIR"] = str(self.config_dir)
        if self.isolated:
            env.update(CC_SWITCH_CONFIG_DIR=str(self.config_dir), CC_SWITCH_TEST_HOME=str(self.home),
                       HOME=str(self.home), USERPROFILE=str(self.home),
                       APPDATA=str(self.home / "AppData/Roaming"), LOCALAPPDATA=str(self.home / "AppData/Local"),
                       CODEX_HOME=str(self.client_dirs["codex"].parent),
                       CLAUDE_CONFIG_DIR=str(self.client_dirs["claude"].parent),
                       GEMINI_CLI_HOME=str(self.client_dirs["gemini"].parent),
                       OPENCODE_CONFIG_DIR=str(self.client_dirs["opencode"].parent))
        return env

    def prepare_cli(self):
        # Only write commands call this, after database/settings backup. Doctor/version never write.
        if not self.isolated: return
        data = dict(self.settings)
        for client, key in self.settings_keys.items():
            data[key] = str(self.client_dirs[client].parent)
        data["skillStorageLocation"] = "cc_switch"
        data["skillSyncMethod"] = "copy"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "settings.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def cli_command(self, *args) -> list:
        cli = str(self.cli)
        return ([sys.executable, cli] if cli.lower().endswith(".py") else [cli]) + list(args)


def pe_version(path: str) -> str | None:
    """读取 Windows 可执行文件的产品版本，不运行它（桌面程序没有安全的 --version）。"""
    if not Path(path).is_file():
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data)
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(data, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None

        class Fixed(ctypes.Structure):
            _fields_ = [("sig", wintypes.DWORD), ("sv", wintypes.DWORD), ("fvms", wintypes.DWORD), ("fvls", wintypes.DWORD),
                        ("pvms", wintypes.DWORD), ("pvls", wintypes.DWORD)]
        info = ctypes.cast(ptr, ctypes.POINTER(Fixed)).contents
        return f"{info.pvms >> 16}.{info.pvms & 0xFFFF}.{info.pvls >> 16}"
    powershell = shutil.which("powershell.exe")
    if powershell:
        windows_path = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True).stdout.strip() or path
        out = subprocess.run([powershell, "-NoProfile", "-Command", f"(Get-Item -LiteralPath '{windows_path}').VersionInfo.ProductVersion"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        match = re.search(r"\d+\.\d+\.\d+", out)
        return match.group(0) if match else None
    return None


def desktop_running() -> bool | None:
    exe = "tasklist" if os.name == "nt" else "tasklist.exe"
    if not shutil.which(exe):
        return None
    try:
        out = subprocess.run([exe, "/FI", "IMAGENAME eq cc-switch.exe"], capture_output=True, timeout=20).stdout  # 本地代码页输出，按字节匹配
        return b"cc-switch.exe" in out.lower()
    except (OSError, subprocess.SubprocessError):
        return None


def cli_version(env: Env) -> tuple[str | None, str | None]:
    if not (env.cli.lower().endswith(".py") or Path(env.cli).is_file()):
        return None, "cli_missing"
    try:
        out = subprocess.run(env.cli_command("--version"), capture_output=True, text=True, timeout=30, env=env.cli_env(), encoding="utf-8")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"cli_not_runnable:{exc.__class__.__name__}"
    match = re.search(r"(\d+\.\d+\.\d+)", out.stdout)
    return (match.group(1), None) if match else (None, "cli_version_unparsed")


def inspect_db(env: Env) -> dict:
    if not env.db.is_file():
        return {"exists": False}
    conn = connect_ro(env.db)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table' order by name")]
        info = {"exists": True, "path": str(env.db), "user_version": version, "tables": tables}
        if "skills" in tables:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(skills)")]
            rows = skill_rows(conn)
            info["skills_columns"] = columns
            info["skills_total"] = len(rows)
            info["skills_custom"] = sum(1 for r in rows if r["repo_owner"] == CUSTOM_OWNER and r["repo_name"] == CUSTOM_REPO)
            info["enabled_by_client"] = {c.removeprefix("enabled_"): sum(1 for r in rows if int(r.get(c) or 0)) for c in columns if c.startswith("enabled_")}
            info["missing_hash"] = sum(1 for r in rows if not r.get("content_hash"))
        for table, key in (("skill_repos", "repos"), ("mcp_servers", "mcp_servers")):
            if table in tables:
                info[key] = conn.execute(f"select count(*) from {table}").fetchone()[0]
        return info
    finally:
        conn.close()


def assess(env: Env) -> dict:
    """版本/结构兼容评估。不猜测：版本表里没有的组合一律返回 unknown。"""
    db = inspect_db(env)
    cli_ver, cli_err = cli_version(env)
    desktop_ver = pe_version(env.desktop) if not env.isolated else None
    compat = env.compat
    cli_schema = compat["cli"].get(cli_ver) if cli_ver else None
    desktop_schema = compat["desktop"].get(desktop_ver) if desktop_ver else None
    reasons, verdict = [], "ready"
    if not db.get("exists") or "skills" not in db.get("tables", []):
        verdict = "db_unreadable"; reasons.append("数据库不存在或没有 skills 表")
    elif cli_err:
        verdict = "cli_missing" if cli_err == "cli_missing" else "cli_unusable"; reasons.append(cli_err)
    elif cli_schema is None:
        verdict = "cli_version_unknown"; reasons.append(f"CLI {cli_ver} 不在已核验版本表，不猜测其数据库结构版本")
    elif cli_schema != db["user_version"]:
        verdict = "needs_coordinated_upgrade"
        reasons.append(f"CLI {cli_ver} 使用结构 {cli_schema}，当前库为 {db['user_version']}：CLI 会迁移或拒绝，不绕过；需先协调升级 Desktop 与 CLI")
    elif desktop_ver and desktop_schema is None and not env.isolated:
        verdict = "desktop_version_unknown"; reasons.append(f"Desktop {desktop_ver} 不在已核验版本表")
    elif desktop_schema is not None and desktop_schema < db["user_version"]:
        verdict = "desktop_too_old_for_db"; reasons.append("Desktop 的结构版本低于当前库")
    running = None if env.isolated else desktop_running()
    return {"verdict": verdict, "reasons": reasons, "desktop": {"path": env.desktop, "version": desktop_ver, "known_schema": desktop_schema,
            "running": running}, "cli": {"path": env.cli, "version": cli_ver, "known_schema": cli_schema, "error": cli_err},
            "db": db, "isolated": env.isolated}


def require_ready(env: Env, write: bool) -> dict:
    report = assess(env)
    if report["verdict"] != "ready":
        raise Gate(report["verdict"], "; ".join(report["reasons"]), report=report)
    if write and report["desktop"]["running"] and not env.allow_desktop_running:
        raise Gate("desktop_running", "CC Switch 桌面正在运行；写库前须由用户决定关闭，或明确加 --allow-desktop-running", report=report)
    return report


# ---------------------------------------------------------------- 命令
def cmd_doctor(env: Env, args) -> int:
    report = assess(env)
    rows = []
    if env.db.is_file() and "skills" in report["db"].get("tables", []):
        conn = connect_ro(env.db)
        rows = skill_rows(conn)
        conn.close()
    custom = [r for r in rows if r["repo_owner"] == CUSTOM_OWNER and r["repo_name"] == CUSTOM_REPO]
    status = {"match": 0, "mismatch": 0, "missing_dir": 0, "no_db_hash": 0}
    mismatched = []
    for row in custom:
        digest, problem = safe_hash(env.ssot / row["directory"])
        if problem == "missing":
            status["missing_dir"] += 1
        elif problem:
            status.setdefault("reparse", 0); status["reparse"] += 1; mismatched.append(row["directory"])
        elif not row.get("content_hash"):
            status["no_db_hash"] += 1; mismatched.append(row["directory"])
        elif digest == row["content_hash"]:
            status["match"] += 1
        else:
            status["mismatch"] += 1; mismatched.append(row["directory"])
    report["custom_hash_status"] = {**status, "attention": mismatched[:20]}
    report["notes"] = ["doctor 只读：直接以只读方式读 SQLite，不运行 Desktop、不迁库；CLI 只调用 --version",
                       "CLI 的 skills 子命令（list/check-updates/update）启动时都会跑 AppState 启动恢复（规范化共享片段/插件元数据，已观察到小幅 provider/settings 元数据规范化），"
                       "不是纯只读；check-updates 另会回填缺失的 content_hash。本助手对这些调用一律先备份并记录为“升级维护范围”"]
    report["clients"] = {"mapped": {k: str(v) for k, v in env.client_dirs.items()}, "blocked": env.client_blocked,
                         "unmapped_enabled": [c for c, n in report["db"].get("enabled_by_client", {}).items() if n and c not in env.client_dirs]}
    emit(report)
    return EXIT_OK if report["verdict"] == "ready" else EXIT_GATE


def cmd_hash(env: Env, args) -> int:
    try:
        emit({"path": args.path, "sha256": dir_hash(Path(args.path))})
    except ReparseError as exc:
        emit({"path": args.path, "error": "reparse_point", "paths": exc.paths[:20]})
        return EXIT_FAIL
    return EXIT_OK


def run_cli(env: Env, *cli_args, timeout=360):
    env.prepare_cli()
    try:
        done = subprocess.run(env.cli_command(*cli_args), capture_output=True, timeout=timeout, env=env.cli_env())
    except subprocess.TimeoutExpired:
        return None, "", f"timeout after {timeout}s"
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def parse_table_directories(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        if any(separator in line for separator in ("│", "┆", "|")):
            cells = [c.strip() for c in re.split(r"[│┆|]", line) if c.strip()]
            if len(cells) >= 2 and cells[0] not in ("Directory", "Name") and not set(cells[0]) <= set("─═┌┐└┘├┤┬┴┼+-="):
                rows.append((cells[0], cells[1]))
    return rows


def classify_check_failures(stderr: str) -> list[dict]:
    """把 CLI `Update check incomplete:` 之后的每行失败分类。
    download_failed：网络/下载问题（上游可能仍在，稍后可重试）；
    upstream_directory_missing：上游仓库已没有该目录（来源被移除/改名，需人工判断，不是“可更新”）；
    unclassified：其余，按失败处理。行格式 `<owner/repo>[:<路径>]: <原因>`（真实输出见 PRODUCTION_check_updates.stderr.txt）。"""
    failures = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("error:", "update check incomplete")):
            continue
        spec, _, reason = line.partition(": ")
        repo, _, path = spec.partition(":")
        directory = re.split(r"[\\/]", path)[-1] if path else None
        if re.search(r"Remote Skill directory .* is not found|not found in", reason):
            kind = "upstream_directory_missing"
            match = re.search(r"directory '([^']+)'", reason)
            directory = match.group(1) if match else directory
        elif re.search(r"下载失败|读取下载内容失败|Download failed|Failed to read download|error sending request|error decoding", reason):
            kind = "download_failed"
        else:
            kind = "unclassified"
        failures.append({"repo": repo, "directory": directory, "kind": kind, "detail": reason[:300]})
    return failures


def identity_of(row: dict) -> dict:
    keys = ("id", "repo_owner", "repo_name", "repo_branch", "directory", "readme_url", "installed_at", "updated_at", "content_hash")
    return {k: row.get(k) for k in keys}


def tree_problems(env: Env, row: dict) -> tuple[str | None, list[str]]:
    """安装副本（SSOT）的摘要与问题：缺失/联接/越界。返回 (摘要或None, 问题列表)。"""
    directory = row["directory"]
    if not directory or directory in (".", "..") or re.search(r"[\\/]", directory):
        return None, ["directory_unsafe"]
    if is_reparse(env.ssot):
        return None, ["ssot_root_reparse_point"]
    folder = env.ssot / directory
    digest, problem = safe_hash(folder)
    problems = []
    if problem == "missing":
        problems.append("installed_copy_missing")
    elif problem:
        problems.append(problem)
    elif not inside(folder, env.ssot):
        problems.append("installed_copy_outside_ssot"); digest = None
    return digest, problems


def managed_client_link(env: Env, row: dict, target: Path) -> bool:
    return is_reparse(target) and same_path(target, env.ssot / row["directory"])


def client_targets_problems(env: Env, row: dict) -> list[str]:
    """SSOT 不允许链接；客户端只允许精确指向对应 SSOT 的受管入口。"""
    problems = []
    for client, flag in enabled_map(row).items():
        if not flag: continue
        if client not in CLIENTS:
            problems.append(f"unsupported_enabled_client:{client}")
            continue
        base = env.client_dirs.get(client)
        if base is None: continue
        target = base / row["directory"]
        if is_reparse(base):
            problems.append(f"client_root_reparse_point:{client}")
        elif is_reparse(target):
            if not managed_client_link(env, row, target): problems.append(f"client_reparse_point:{client}")
        elif target.is_dir():
            if not inside(target, base): problems.append(f"client_outside_root:{client}")
            elif find_reparse(target): problems.append(f"client_nested_reparse_point:{client}")
    return problems


def build_plan(env: Env, updates, report, failures=()) -> dict:
    conn = connect_ro(env.db)
    rows = skill_rows(conn)
    conn.close()
    items = []

    def item_for(directory, name):
        matches = [r for r in rows if r["directory"].lower() == directory.lower()]
        item = {"directory": directory, "name": name, "selected": False, "route": "cli-update", "problems": []}
        if len(matches) != 1:
            item["problems"].append("ambiguous_directory" if matches else "not_in_database")
        if matches:
            row = matches[0]
            item.update(id=row["id"], repo=f"{row['repo_owner']}/{row['repo_name']}" if row["repo_owner"] else None,
                        enabled=enabled_map(row), db_hash=row.get("content_hash"))
            if len(matches) == 1:
                item["identity"] = identity_of(row)  # 冻结的来源身份：执行时必须与库内当前一致
            digest, problems = tree_problems(env, row)
            item["installed_hash"] = digest
            item["problems"].extend(problems)
            item["problems"].extend(client_targets_problems(env, row))
            if row["repo_owner"] == CUSTOM_OWNER and row["repo_name"] == CUSTOM_REPO:
                item["route"] = "custom-sync"
                item["problems"].append("custom_repo_use_sync_script")
            if digest and row.get("content_hash") and digest != row["content_hash"]:
                item["problems"].append("local_modified")
        return item

    for directory, name in updates:
        items.append(item_for(directory, name))
    listed = {i["directory"].lower() for i in items}
    for failure in failures:  # 失败的来源只阻塞依赖它的条目，且单独列出供审阅
        for row in rows:
            repo = f"{row['repo_owner']}/{row['repo_name']}"
            if repo != failure["repo"] or row["directory"].lower() in listed:
                continue
            if failure["kind"] == "upstream_directory_missing" and row["directory"].lower() != (failure["directory"] or "").lower():
                continue
            item = item_for(row["directory"], row.get("name") or row["directory"])
            item["route"] = "unchecked"
            item["problems"].append("check_" + failure["kind"])
            items.append(item); listed.add(row["directory"].lower())
    return {"created": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "cli_version": report["cli"]["version"],
            "db_user_version": report["db"]["user_version"], "complete": not failures, "check_failures": list(failures),
            "how_to_use": "把要更新的条目 selected 改为 true（problems 非空的条目不会被执行；route=unchecked 的条目是检查失败/上游缺失，不能更新），再运行 update --plan 本文件 --confirm。"
                          "执行时会重读库内来源身份，与清单冻结的不一致则整条拒绝",
            "items": items}


def cmd_check_updates(env: Env, args) -> int:
    report = require_ready(env, write=True)  # CLI 检查会回填 hash 且启动时跑恢复，按写操作门禁
    stamp = now_stamp()
    backup = env.backup_root / f"{stamp}-cli-check-updates"
    backup_db(env.db, backup / "cc-switch.db")
    if (env.config_dir / "settings.json").is_file():
        shutil.copyfile(env.config_dir / "settings.json", backup / "settings.json")
    code, out, err = run_cli(env, "skills", "check-updates")
    result = {"scope": "upgrade-maintenance", "backup": str(backup), "exit": code, "stderr": err.strip()[:2000]}
    if code is None:
        result.update(outcome="unknown", complete=False); emit(result); return EXIT_UNKNOWN
    updates = parse_table_directories(out)
    failures = classify_check_failures(err) if code != 0 else []
    if code != 0 and not failures:
        failures = [{"repo": None, "directory": None, "kind": "unclassified", "detail": err.strip()[:300]}]
    outcome = "complete" if code == 0 else "partial"
    result.update(outcome=outcome, complete=code == 0, updates=len(updates), cli_output=out.strip()[:4000], check_failures=failures)
    plan = build_plan(env, updates, report, failures)
    plan_path = Path(args.plan_out) if args.plan_out else backup / "updates-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    result["plan"] = str(plan_path)
    result["summary"] = {"cli_update": sum(1 for i in plan["items"] if i["route"] == "cli-update" and not i["problems"]),
                         "custom_sync": sum(1 for i in plan["items"] if i["route"] == "custom-sync"),
                         "unchecked": sum(1 for i in plan["items"] if i["route"] == "unchecked"),
                         "blocked": sum(1 for i in plan["items"] if i["problems"] and i["route"] not in ("custom-sync", "unchecked")),
                         "download_failed": sum(1 for f in failures if f["kind"] == "download_failed"),
                         "upstream_directory_missing": sum(1 for f in failures if f["kind"] == "upstream_directory_missing")}
    if code != 0:
        result["note"] = "检查未完整完成：不能据此认为“全部已是最新”；表中项是已确认可更新的，失败来源下的条目状态未知（见 check_failures）"
    emit(result)
    return EXIT_OK if code == 0 else EXIT_FAIL


def client_check(env: Env, row: dict, folder_hash: str | None) -> dict:
    """每个已启用客户端必须落到 ok / missing / hash_mismatch / reparse_point / unmapped / blocked_override 之一。
    unmapped：没有已核验的目录映射（不猜）；它与 blocked_override 都不算成功。"""
    out = {}
    for client, flag in enabled_map(row).items():
        if not flag:
            continue
        base = env.client_dirs.get(client)
        if client not in CLIENTS:
            out[client] = "unsupported"
            continue
        if base is None:
            out[client] = "blocked_override" if client in env.client_blocked else "unmapped"
            continue
        target = base / row["directory"]
        if is_reparse(base):
            out[client] = "reparse_point"
        elif managed_client_link(env, row, target):
            out[client] = "ok" if folder_hash else "hash_mismatch"
        elif is_reparse(target):
            out[client] = "reparse_point"
        elif not target.is_dir():
            out[client] = "missing"
        else:
            try:
                out[client] = "ok" if folder_hash and dir_hash(target) == folder_hash else "hash_mismatch"
            except ReparseError:
                out[client] = "reparse_point"
    return out


def clients_ok(clients: dict) -> bool:
    return all(v == "ok" for v in clients.values())


def cmd_verify(env: Env, args) -> int:
    conn = connect_ro(env.db)
    rows = skill_rows(conn)
    conn.close()
    results = []
    if args.skills is not None:
        wanted = list(dict.fromkeys(s.strip() for s in args.skills.split(",") if s.strip()))
        if not wanted:
            raise SystemExit("--skills 不能为空")
        targets = []
        for name in wanted:  # 每个显式选中项都必须被找到并校验，缺失/歧义是明确的失败
            matches = [r for r in rows if r["directory"].lower() == name.lower()]
            if len(matches) != 1:
                results.append({"directory": name, "installed": False, "db_hash_match": False, "clients": {},
                                "problems": ["ambiguous_directory" if matches else "not_in_database"]})
            else:
                targets.append(matches[0])
    else:
        targets = [r for r in rows if r["repo_owner"] == CUSTOM_OWNER and r["repo_name"] == CUSTOM_REPO]
    for row in targets:
        digest, problems = tree_problems(env, row)
        clients = client_check(env, row, digest)
        results.append({"directory": row["directory"], "installed": digest is not None,
                        "db_hash_match": bool(digest and digest == row.get("content_hash")), "clients": clients,
                        "problems": problems + [f"client_{c}_{v}" for c, v in clients.items() if v != "ok"]})
    bad = [r for r in results if not r["installed"] or not r["db_hash_match"] or r["problems"] or not clients_ok(r["clients"])]
    emit({"checked": len(results), "requested": len(wanted) if args.skills is not None else None, "problems": len(bad),
          "verdict": "verified" if results and not bad else ("nothing_checked" if not results else "problems"),
          "details": bad[:50] if not args.all_details else results})
    return EXIT_OK if results and not bad else EXIT_FAIL


def cmd_update(env: Env, args) -> int:
    if bool(args.plan) == bool(args.skills):
        raise SystemExit("必须且只能给 --plan 或 --skills 之一")
    conn = connect_ro(env.db)
    rows = skill_rows(conn)
    conn.close()
    selected, plan_items, plan = [], {}, None
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        for item in plan["items"]:
            plan_items[item["directory"].lower()] = item
            if item.get("selected"):
                selected.append(item["directory"])
    else:
        selected = [s.strip() for s in args.skills.split(",") if s.strip()]
    if not selected:
        raise Gate("nothing_selected", "没有选中任何条目；未执行")
    refused, todo = [], []
    for directory in selected:
        matches = [r for r in rows if r["directory"].lower() == directory.lower()]
        problems = []
        if len(matches) != 1:
            problems.append("ambiguous_directory" if matches else "not_in_database")
        else:
            row = matches[0]
            if row["repo_owner"] == CUSTOM_OWNER and row["repo_name"] == CUSTOM_REPO:
                problems.append("custom_repo_use_sync_script")
            if not row.get("repo_owner"):
                problems.append("not_repository_backed")
            current, tree = tree_problems(env, row)
            problems.extend(tree)
            problems.extend(client_targets_problems(env, row))
            if current and row.get("content_hash") and current != row["content_hash"]:
                problems.append("local_modified")
            planned = plan_items.get(directory.lower())
            if plan is not None:
                frozen = (planned or {}).get("identity")
                if not frozen:
                    problems.append("plan_identity_missing")
                else:
                    changed = sorted(k for k, v in identity_of(row).items() if frozen.get(k) != v)
                    if changed:  # 来源 id/仓库/分支/目录/readme/时间戳/摘要任一变化：旧清单作废，不对新来源执行
                        problems.append("source_identity_drift:" + ",".join(changed))
                    if planned.get("enabled") is not None and planned["enabled"] != enabled_map(row):
                        problems.append("enabled_changed_since_plan")
                if planned and planned.get("installed_hash") and current and planned["installed_hash"] != current:
                    problems.append("drifted_since_plan")
                if planned and planned.get("problems"):
                    problems.extend(p for p in planned["problems"] if p not in problems)
        (refused if problems else todo).append({"directory": directory, "problems": problems} if problems else (directory, matches[0]))
    if refused:
        raise Gate("scope_refused", "范围内有条目不允许经 CLI 更新；整批未执行", refused=refused)
    report = require_ready(env, write=True)
    if plan is not None and (plan.get("cli_version") != report["cli"]["version"] or plan.get("db_user_version") != report["db"]["user_version"]):
        raise Gate("plan_version_changed", "清单生成后 CLI 版本或库结构版本已变化；请重新 check-updates", report=report)
    if not args.confirm:
        emit({"dry_run": True, "would_update": [d for d, _ in todo], "cli_version": report["cli"]["version"],
              "note": "加 --confirm 才会备份并调用 CLI；逐项调用 `skills update <目录>`，不使用 --all"})
        return EXIT_OK
    backup = env.backup_root / f"{now_stamp()}-cli-update"
    backup_db(env.db, backup / "cc-switch.db")
    if (env.config_dir / "settings.json").is_file():
        shutil.copyfile(env.config_dir / "settings.json", backup / "settings.json")
    for directory, row in todo:
        shutil.copytree(env.ssot / row["directory"], backup / "installed-before" / row["directory"], symlinks=True)
    results, exit_code = [], EXIT_OK
    for directory, before in todo:
        entry = {"directory": directory, "enabled_before": enabled_map(before), "hash_before": before.get("content_hash")}
        code, out, err = run_cli(env, "skills", "update", before["directory"], timeout=args.timeout)
        entry.update(exit=code, stdout=out.strip()[:2000], stderr=err.strip()[:2000])
        if code is None:
            entry["status"] = "unknown"; results.append(entry); exit_code = EXIT_UNKNOWN
            break  # 超时：结果未知，保留现场，不重放也不继续下一项
        conn = connect_ro(env.db)
        after = next((r for r in skill_rows(conn) if r["id"] == before["id"]), None)
        conn.close()
        digest, tree = tree_problems(env, before)
        entry["enabled_after"] = enabled_map(after) if after else None
        entry["hash_after"] = after.get("content_hash") if after else None
        entry["hash_changed"] = entry["hash_after"] != entry["hash_before"]
        entry["installed_hash_matches_db"] = bool(after and digest and digest == after.get("content_hash"))
        entry["clients"] = client_check(env, after, digest) if after else {}
        if tree:
            entry["problems"] = tree
        if after is None or entry["enabled_after"] != entry["enabled_before"]:
            entry["status"] = "drift"  # 启用开关被改动：停止整批，不自动修回
            results.append(entry); exit_code = EXIT_FAIL
            break
        if code != 0:
            entry["status"] = "failed"; exit_code = EXIT_FAIL
        elif not entry["installed_hash_matches_db"]:
            entry["status"] = "unverified"; exit_code = EXIT_FAIL
        elif not clients_ok(entry["clients"]):
            entry["status"] = "partial"  # SSOT 与库已更新，但有已启用客户端未能证明同步（缺失/不一致/未映射/被覆盖），不算成功
            entry["unverified_clients"] = {c: v for c, v in entry["clients"].items() if v != "ok"}
            exit_code = EXIT_FAIL
        else:
            entry["status"] = "updated"
        results.append(entry)
    summary = {"scope": "upgrade-maintenance", "backup": str(backup), "cli_version": report["cli"]["version"], "results": results,
               "not_attempted": [d for d, _ in todo if d not in {r["directory"] for r in results}]}
    (backup / "update-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(summary)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db"); p.add_argument("--cli", help="覆盖稳定 CLI 路径（测试/隔离用；也可设环境变量 CC_SWITCH_CLI）")
    p.add_argument("--config-dir", help="隔离配置目录：同时作为 CLI 的 CC_SWITCH_CONFIG_DIR，默认库/安装根/备份根都在其下")
    p.add_argument("--ssot-root"); p.add_argument("--backup-root"); p.add_argument("--desktop-exe")
    p.add_argument("--client-dir", action="append", metavar="CLIENT=PATH")
    p.add_argument("--allow-desktop-running", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="只读：版本、结构、开关、自建摘要状态与兼容结论")
    h = sub.add_parser("hash", help="按上游算法计算目录 content_hash"); h.add_argument("path")
    c = sub.add_parser("check-updates", help="调用 CLI 检查更新并生成可审阅清单（先备份；会回填缺失 hash）")
    c.add_argument("--plan-out")
    u = sub.add_parser("update", help="按清单或明确列表逐项更新；默认 dry-run，需 --confirm")
    u.add_argument("--plan"); u.add_argument("--skills", help="逗号分隔目录名"); u.add_argument("--confirm", action="store_true")
    u.add_argument("--timeout", type=int, default=360)
    v = sub.add_parser("verify", help="回读：安装副本摘要、库内摘要、各客户端目录")
    v.add_argument("--skills", help="逗号分隔；每一项都必须存在并通过校验"); v.add_argument("--all-details", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        env = Env(args)
        return {"doctor": cmd_doctor, "hash": cmd_hash, "check-updates": cmd_check_updates, "update": cmd_update,
                "verify": cmd_verify}[args.command](env, args)
    except Gate as gate:
        emit({"ok": False, "verdict": gate.verdict, "message": str(gate), **{k: v for k, v in gate.extra.items()}})
        return EXIT_GATE


if __name__ == "__main__":
    sys.exit(main())
