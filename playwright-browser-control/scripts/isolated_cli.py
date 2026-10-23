#!/usr/bin/env python3
"""模式 B（隔离调试/测试）的 playwright-cli 包装：跨 Windows/WSL，固定隔离，精确登记与收尾。

只驱动本次新建的内存临时浏览器会话（会话名固定前缀 iso-）。拒绝一切会触达受管真实资料或他人会话的参数/命令：
attach、--extension、--profile、--persistent、--user-data-dir、--cdp、close-all、kill-all、自带 -s/--session 等。
用法：
  isolated_cli.py run SESSION [--workdir DIR] -- <playwright-cli 参数...>
  isolated_cli.py finish SESSION        # close + delete-data（仅限本包装登记过的会话）并移除登记
  isolated_cli.py list                  # 本包装登记的会话（不读取页面内容）
环境变量：PLAYWRIGHT_CLI（可执行文件）、PBC_ISOLATED_ROOT（登记/工作目录根，默认系统临时目录下 pbc-isolated）。
退出码：沿用 playwright-cli；包装层拒绝返回 2；会话未登记返回 3。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SESSION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
FORBIDDEN_FLAGS = ("--extension", "--profile", "--persistent", "--user-data-dir", "--cdp", "--endpoint", "--connect",
                   "--session", "-s")
FORBIDDEN_COMMANDS = {"attach", "detach", "close-all", "kill-all", "delete-data", "install", "install-browser", "list"}


def root() -> Path:
    return Path(os.environ.get("PBC_ISOLATED_ROOT") or Path(tempfile.gettempdir()) / "pbc-isolated")


def registry_path() -> Path:
    return root() / "registry.json"


def load_registry() -> dict:
    path = registry_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_registry(data: dict) -> None:
    root().mkdir(parents=True, exist_ok=True)
    temporary = registry_path().with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, registry_path())


def find_cli() -> list:
    explicit = os.environ.get("PLAYWRIGHT_CLI")
    if explicit and Path(explicit).is_file():
        return [explicit]
    names = ["playwright-cli.cmd", "playwright-cli"] if os.name == "nt" else ["playwright-cli"]
    for name in names:
        found = shutil.which(name)
        if found:
            return [found]
    raise SystemExit("未找到 playwright-cli（可设置 PLAYWRIGHT_CLI）；不使用 npx 临时拉包。")


def full_name(session: str) -> str:
    if not SESSION_RE.match(session):
        print("会话名须为 1–32 位小写字母/数字/连字符。", file=sys.stderr)
        raise SystemExit(2)
    return "iso-" + session


def reject(message: str) -> int:
    print("拒绝：" + message, file=sys.stderr)
    return 2


def check_args(args: list) -> str | None:
    if not args:
        return "缺少 playwright-cli 命令。"
    command = next((a for a in args if not a.startswith("-")), "")
    if command in FORBIDDEN_COMMANDS:
        return f"命令 {command} 不允许在隔离模式使用（收尾用 finish；接管/全局清理属于受管模式）。"
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in FORBIDDEN_FLAGS or arg.startswith("-s="):
            return f"参数 {flag} 会触达受管/持久资料或他人会话。"
        if flag == "--browser" and "=" in arg and arg.split("=", 1)[1] not in ("chrome", "msedge", "firefox", "webkit"):
            return "未知浏览器通道。"
    return None


def cmd_run(argv: list) -> int:
    if len(argv) < 3 or "--" not in argv:
        return reject("用法：run SESSION [--workdir DIR] -- <playwright-cli 参数>")
    split = argv.index("--")
    head, args = argv[:split], argv[split + 1:]
    session = full_name(head[0])
    workdir = None
    if "--workdir" in head:
        workdir = Path(head[head.index("--workdir") + 1])
    problem = check_args(args)
    if problem:
        return reject(problem)
    registry = load_registry()
    command = next((a for a in args if not a.startswith("-")), "")
    if command == "open" and session in registry:
        return reject(f"会话 {session} 已存在；先 finish，下次使用新会话名。")
    if command != "open" and session not in registry:
        return reject(f"会话 {session} 不是本包装创建的；先 run … -- open。")
    work = workdir or (root() / session / "work")
    work.mkdir(parents=True, exist_ok=True)
    if command == "open":
        if not any(a.startswith("--idle-timeout") for a in args):
            args = args + ["--idle-timeout", "600000"]
        registry[session] = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "workdir": str(work), "pid": os.getpid()}
        save_registry(registry)
    result = subprocess.run(find_cli() + [f"-s={session}"] + args, cwd=work)
    if command == "open" and result.returncode != 0:
        registry.pop(session, None)
        save_registry(registry)
    return result.returncode


def cmd_finish(argv: list) -> int:
    if len(argv) != 1:
        return reject("用法：finish SESSION")
    session = full_name(argv[0])
    registry = load_registry()
    if session not in registry:
        print(f"会话 {session} 未登记，不做任何清理。", file=sys.stderr)
        return 3
    work = Path(registry[session]["workdir"])
    cli = find_cli()
    close = subprocess.run(cli + [f"-s={session}", "close"], cwd=work, capture_output=True)
    delete = subprocess.run(cli + [f"-s={session}", "delete-data"], cwd=work, capture_output=True)
    registry.pop(session)
    save_registry(registry)
    print(json.dumps({"session": session, "close_exit": close.returncode, "delete_data_exit": delete.returncode,
                      "workdir_kept": str(work)}, ensure_ascii=False))
    return 0 if close.returncode == 0 else close.returncode


def main(argv: list) -> int:
    if not argv or argv[0] not in ("run", "finish", "list"):
        print(__doc__)
        return 2
    if argv[0] == "run":
        return cmd_run(argv[1:])
    if argv[0] == "finish":
        return cmd_finish(argv[1:])
    print(json.dumps(load_registry(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
