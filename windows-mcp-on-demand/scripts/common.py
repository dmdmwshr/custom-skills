"""Fixed deployment contract; no credentials in argv or reports."""
from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(r"D:\Program_Files\Windows-MCP-Runtime\0.8.7-skill-v1")
PYTHON = RUNTIME / ".venv/Scripts/python.exe"
PYTHONW = RUNTIME / ".venv/TaskScripts/pythonw.exe"
DATA = Path(r"D:\12070\AppData\WindowsMCP")
TOKEN = DATA / "credentials/auth.key"
STATE = DATA / "runtime"
OUTPUT = DATA / "artifacts"
TASK_PATH = "\\DevProjects\\CODEXENV\\LIVE\\"
TASK_NAME = "DEV-CODEXENV-LIVE-01-WindowsMCP"
TASK = TASK_PATH + TASK_NAME
URL = "http://127.0.0.1:18765/mcp"
PORT = 18765
EXCLUDED = frozenset({"PowerShell", "FileSystem", "Registry", "Process", "Scrape"})
EXPECTED = frozenset({"App", "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor",
    "Clipboard", "Notification", "Snapshot", "Screenshot", "DisplayInventory", "MultiSelect", "MultiEdit"})
def token():
    if not TOKEN.is_file():
        raise RuntimeError("token_missing_user_input_required")
    value = TOKEN.read_text(encoding="utf-8-sig").strip()
    if len(value) < 32 or any(ch.isspace() for ch in value):
        raise RuntimeError("token_invalid_user_input_required")
    return value
