"""Run upstream 0.8.7 in the task's own process; refuse missing credentials."""
import json
import os
from pathlib import Path
import sys
import uuid
from common import ROOT, STATE, EXCLUDED, token
def main():
    secret = token()  # Do this before importing Windows-only upstream dependencies.
    # A caller's environment must not enable tools, OAuth, remote binding or debug.
    for key in list(os.environ):
        if key.upper().startswith("WINDOWS_MCP_"):
            del os.environ[key]
    os.environ.update(WINDOWS_MCP_AUTH_KEY=secret, WINDOWS_MCP_WATCHDOG="off",
        ANONYMIZED_TELEMETRY="false", NO_COLOR="1")
    from importlib.metadata import version
    if version("windows-mcp") != "0.8.7":
        raise RuntimeError("upstream_version_mismatch")
    STATE.mkdir(parents=True, exist_ok=True)
    # Redirect all library diagnostics away from stdout/stderr; never log secrets.
    null = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = null
    sys.stderr = null
    marker = {"pid": os.getpid(), "created_at": __import__("psutil").Process().create_time(), "run_id": uuid.uuid4().hex,
              "server": str(Path(__file__).resolve()), "version": "0.8.7"}
    temporary = STATE / ("server-" + marker["run_id"] + ".tmp")
    temporary.write_text(json.dumps(marker), encoding="utf-8")
    os.replace(temporary, STATE / "server.json")
    from windows_mcp.__main__ import main as upstream
    upstream(args=["serve", "--config", str(ROOT / "references/server.toml"),
        "--transport", "streamable-http", "--host", "127.0.0.1", "--port", "18765",
        "--stateless-http", "--exclude-tools", ",".join(sorted(EXCLUDED))],
        standalone_mode=False)
if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        # Category only: exception text and tracebacks may contain credentials.
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / ("failure-" + uuid.uuid4().hex + ".json")).write_text(
            json.dumps({"error": "server_failed", "category": type(exc).__name__}), encoding="utf-8")
        raise SystemExit(1)
