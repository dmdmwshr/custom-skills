"""测试用模拟 CC Switch CLI（只用于 test_cc_switch_control.py，不是真实 CLI）。

行为由 $CC_SWITCH_CONFIG_DIR/fake-cli.json 控制；每次调用追加到 fake-cli.log。
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_switch_control import dir_hash  # noqa: E402

config_dir = Path(os.environ["CC_SWITCH_CONFIG_DIR"])
plan = json.loads((config_dir / "fake-cli.json").read_text(encoding="utf-8"))
args = sys.argv[1:]
with open(config_dir / "fake-cli.log", "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")

if args == ["--version"]:
    print(f"cc-switch {plan.get('version', '5.11.3')}")
    sys.exit(0)
if args[:2] == ["skills", "check-updates"]:
    rows = plan.get("updates", [])
    if rows:
        print("┌───────────┬──────┐\n│ Directory │ Name │\n├───────────┼──────┤")
        for directory, name in rows:
            print(f"│ {directory} │ {name} │")
        print("└───────────┴──────┘")
    if plan.get("check_failures"):
        sys.stderr.write("Error: Update check incomplete:\n" + "\n".join(plan["check_failures"]) + "\n")
        sys.exit(1)
    sys.exit(0)
if args[:2] == ["skills", "update"] and len(args) == 3 and args[2] != "--all":
    directory = args[2]
    behavior = plan.get("behavior", {}).get(directory, "ok")
    db = sqlite3.connect(config_dir / "cc-switch.db")
    if behavior == "fail":
        sys.stderr.write("network error")
        sys.exit(1)
    if behavior == "timeout":
        time.sleep(30)
        sys.exit(0)
    folder = config_dir / "skills" / directory
    (folder / "SKILL.md").write_text((folder / "SKILL.md").read_text(encoding="utf-8") + "\nupstream update\n", encoding="utf-8")
    if behavior != "stale_hash":
        db.execute("update skills set content_hash=? where directory=?", (dir_hash(folder), directory))
    if behavior == "drift":
        db.execute("update skills set enabled_codex=0 where directory=?", (directory,))
    db.commit()
    import shutil
    for app in ("codex", "gemini", "mcode"):  # 真实 CLI 会把更新后的 SSOT 同步到已启用的客户端目录
        client = config_dir / f"client-{app}" / "skills" / directory
        enabled = db.execute(f"select enabled_{app} from skills where directory=?", (directory,)).fetchone()
        if behavior != "no_client_sync" and not (behavior == "no_mcode_sync" and app == "mcode") and enabled and enabled[0] and client.parent.is_dir():
            if client.resolve() != folder.resolve(): shutil.copytree(folder, client, dirs_exist_ok=True)
    print(f"updated {directory}")
    sys.exit(0)
sys.stderr.write("unexpected invocation: " + " ".join(args))
sys.exit(2)
