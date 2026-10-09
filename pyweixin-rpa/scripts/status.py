"""Use the single installed runtime for a read-only status check."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(r"D:\Program_Files\pyweixin")
    interpreter = root / ".venv" / "Scripts" / "python.exe"
    doctor = root / "doctor.py"
    if not interpreter.is_file() or not doctor.is_file():
        print(json.dumps({"ok": False, "error": "固定 pyweixin 环境或状态入口不存在"}, ensure_ascii=False))
        return 2
    result = subprocess.run(
        [str(interpreter), "-B", "-X", "utf8", str(doctor)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "error": "工具没有返回有效状态", "exit_code": result.returncode}
    print(json.dumps(payload, ensure_ascii=False))
    return result.returncode


if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
