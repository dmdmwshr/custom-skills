"""One-shot, bounded MCP HTTP client. Never replay a tool call."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from common import (ROOT, PYTHONW, STATE, OUTPUT, TASK_PATH, TASK_NAME, TASK, URL,
                    PORT, EXCLUDED, EXPECTED, TOKEN, token)
class Failure(RuntimeError):
    pass
def ps(script):
    if os.name != "nt":
        raise Failure("windows_client_required")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
            "-EncodedCommand", encoded], capture_output=True, timeout=15,
            creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        raise Failure("task_query_or_action_timeout_result_unknown") from None
    if p.returncode:
        raise Failure("task_query_or_action_failed_result_unknown")
    try:
        return json.loads(p.stdout.decode("utf-8-sig").strip() or "null")
    except (ValueError, UnicodeError):
        raise Failure("task_query_invalid_result_unknown") from None
def inspect():
    # Return only public process identity, never process environment or arbitrary commands.
    server = str(ROOT / "scripts/server.py").replace("'", "''")
    return ps("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
        + "$t=Get-ScheduledTask -TaskPath '" + TASK_PATH + "' -TaskName '" + TASK_NAME + "' -ErrorAction SilentlyContinue; "
        + "$ports=@(Get-NetTCPConnection -LocalPort " + str(PORT) + " -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,OwningProcess); "
        + "$p=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -and $_.CommandLine.Contains('" + server + "')} | Select-Object ProcessId,ParentProcessId,ExecutablePath,@{n='created_at';e={([DateTimeOffset]$_.CreationDate).ToUnixTimeMilliseconds()/1000}},CommandLine); "
        + "[ordered]@{task=if($t){@{state=[string]$t.State;actions=@($t.Actions|Select-Object Execute,Arguments,WorkingDirectory);user=$t.Principal.UserId;userSid=([Security.Principal.NTAccount]::new($t.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value;description=$t.Description;logon=[string]$t.Principal.LogonType;level=[string]$t.Principal.RunLevel;triggers=@($t.Triggers | Where-Object {$null -ne $_}).Count}}else{$null};listeners=$ports;processes=$p}|ConvertTo-Json -Depth 6 -Compress")
def valid_task(info):
    task = info.get("task")
    if not task:
        raise Failure("task_missing_run_setup")
    actions = task["actions"]
    expected_args = '-B "' + str(ROOT / "scripts/server.py") + '"'
    if (len(actions) != 1 or actions[0]["Execute"].lower() != str(PYTHONW).lower()
        or actions[0]["Arguments"] != expected_args
        or actions[0]["WorkingDirectory"].lower() != str(PYTHONW.parents[2]).lower()
        or task.get("userSid") != "S-1-5-21-3995939195-1261990851-1351888376-1001"
        or not task.get("description", "").startswith("[DEV_TASK_V1][project=codex-env-win][id=LIVE-01][category=LIVE]")
        or task["logon"] != "Interactive" or task["level"] != "Limited" or task["triggers"] != 0):
        raise Failure("task_contract_mismatch_no_action")
def owned(info):
    try:
        mark = json.loads((STATE / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Failure("server_identity_unknown_no_action") from None
    if (mark.get("server") != str(ROOT / "scripts/server.py") or mark.get("version") != "0.8.7"
        or not isinstance(mark.get("run_id"), str) or len(mark["run_id"]) != 32):
        raise Failure("server_marker_mismatch_no_action")
    proc = [p for p in info["processes"] if p["ProcessId"] == mark["pid"]]
    if len(proc) != 1 or abs(proc[0].get("created_at", 0) - mark.get("created_at", -100)) > 0.1:
        raise Failure("server_identity_unknown_no_action")
    base = r"D:\Program_Files\uv-python\cpython-3.14.3-windows-x86_64-none\pythonw.exe"
    if proc[0]["ExecutablePath"].lower() == base.lower():
        parents = [p for p in info["processes"] if p["ProcessId"] == proc[0].get("ParentProcessId")]
        if len(parents) != 1 or parents[0]["ExecutablePath"].lower() != str(PYTHONW).lower():
            raise Failure("server_parent_unknown_no_action")
    elif proc[0]["ExecutablePath"].lower() != str(PYTHONW).lower():
        raise Failure("server_identity_unknown_no_action")
    for listener in info["listeners"]:
        if listener["OwningProcess"] != mark["pid"] or listener["LocalAddress"] != "127.0.0.1":
            raise Failure("port_owner_unknown_no_action")
    return mark
class HTTP:
    def __init__(self, secret, timeout=15):
        self.secret = secret
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(self, payload, authenticated=True):
        headers = {"Content-Type":"application/json", "Accept":"application/json, text/event-stream",
                   "MCP-Protocol-Version":"2025-03-26"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.secret
        request = urllib.request.Request(URL, json.dumps(payload).encode("utf-8"), headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(20 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            return {"http_error": exc.code}
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Failure("http_transport_failed_result_unknown_no_retry") from None
        if len(raw) > 20 * 1024 * 1024:
            raise Failure("http_result_too_large_unknown_no_retry")
        try:
            text = raw.decode("utf-8")
            if text.lstrip().startswith("data:") or "\ndata:" in text:
                events = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
                values = [json.loads(value) for value in events if value]
                matches = [v for v in values if v.get("id") == payload.get("id")]
                return matches[-1] if matches else {}
            return json.loads(text) if text.strip() else {}
        except (ValueError, UnicodeError, AttributeError):
            raise Failure("http_invalid_response_result_unknown_no_retry") from None
    def rpc(self, method, params, ident=1):
        result = self.request({"jsonrpc":"2.0", "id":ident, "method":method, "params":params})
        if "http_error" in result or "error" in result or "result" not in result:
            raise Failure("mcp_error_result_unknown_no_retry")
        return result["result"]
    def tools(self):
        unauth = self.request({"jsonrpc":"2.0","id":0,"method":"tools/list","params":{}}, False)
        if unauth.get("http_error") not in (401, 403):
            raise Failure("authentication_not_enforced")
        self.rpc("initialize", {"protocolVersion":"2025-03-26","capabilities":{},
            "clientInfo":{"name":"windows-mcp-on-demand","version":"1.0.0"}})
        self.request({"jsonrpc":"2.0","method":"notifications/initialized"})
        tools = self.rpc("tools/list", {}, 2).get("tools", [])
        names = {item["name"] for item in tools}
        if names != EXPECTED or names & EXCLUDED:
            raise Failure("tool_set_mismatch_no_calls")
        return tools
def sanitize(value, output, secret):
    """Recursively externalize every MCP image block before JSON output."""
    if isinstance(value, dict):
        if value.get("type") == "image":
            try:
                raw = base64.b64decode(value["data"], validate=True)
            except (KeyError, ValueError):
                raise Failure("image_invalid_no_payload_output") from None
            output.mkdir(parents=True, exist_ok=True)
            mime = value.get("mimeType", "application/octet-stream")
            ext = {"image/png":".png","image/jpeg":".jpg","image/webp":".webp"}.get(mime, ".bin")
            path = output / (uuid.uuid4().hex + ext)
            with path.open("xb") as stream:
                stream.write(raw)
            return {"type":"image_file", "path":str(path),"sha256":hashlib.sha256(raw).hexdigest(),"mimeType":mime}
        return {str(k).replace(secret, "[redacted]"):sanitize(v, output, secret)
                for k,v in value.items() if k.lower() not in {"authorization", "headers", "data"}}
    if isinstance(value, list):
        return [sanitize(v, output, secret) for v in value]
    if isinstance(value, str):
        # Do not emit embedded data URIs or non-image opaque encodings.
        if "data:image/" in value or len(value) > 200000:
            return "[opaque payload omitted]"
        return value.replace(secret, "[redacted]")
    return value
def arguments(args):
    if args.arguments_file and args.arguments_stdin:
        raise Failure("choose_one_arguments_source")
    try:
        if args.arguments_file:
            value = json.loads(Path(args.arguments_file).read_text(encoding="utf-8-sig"))
        elif args.arguments_stdin:
            value = json.loads(sys.stdin.buffer.read(1024 * 1024).decode("utf-8-sig"))
        else:
            value = {}
    except (OSError, ValueError, UnicodeError):
        raise Failure("arguments_invalid") from None
    if not isinstance(value, dict):
        raise Failure("arguments_must_be_object")
    return value
def run(args):
    if args.command == "call" and args.tool in EXCLUDED:
        raise Failure("excluded_tool_refused")
    info = inspect()
    valid_task(info)
    if args.command == "status":
        if not info["listeners"] and not info["processes"]:
            return {"state":"stopped", "token_present":TOKEN.is_file(), **info}
        owned(info)
        HTTP(token(), args.timeout).tools()
        return {"state":"running_authenticated", **info}
    if args.command == "stop":
        if not info["listeners"] and not info["processes"]:
            return {"state":"already_stopped"}
        owned(info)
        ps("$ErrorActionPreference='Stop';Stop-ScheduledTask -TaskPath '" + TASK_PATH
           + "' -TaskName '" + TASK_NAME + "'; 'null'")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            after = inspect()
            if not after["listeners"] and not after["processes"] and after["task"]["state"] != "Running":
                return {"state":"stopped","listeners":[],"processes":[]}
            time.sleep(.4)
        raise Failure("stop_incomplete_result_unknown_no_kill")
    secret = token()
    if args.command == "start":
        if info["listeners"] or info["processes"]:
            owned(info)
            HTTP(secret, args.timeout).tools()
            return {"state":"already_running_authenticated"}
        ps("$ErrorActionPreference='Stop';Start-ScheduledTask -TaskPath '" + TASK_PATH
           + "' -TaskName '" + TASK_NAME + "'; 'null'")
        # Only read-only readiness probes repeat; task start and tool calls never replay.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            after = inspect()
            if after["listeners"]:
                owned(after)
                HTTP(secret, args.timeout).tools()
                return {"state":"started_authenticated","tools":sorted(EXPECTED)}
            time.sleep(.4)
        raise Failure("start_not_ready_result_unknown_no_retry_use_status")
    owned(info)
    http = HTTP(secret, args.timeout)
    tools = http.tools()
    if args.command == "tools":
        return {"tools":tools}
    name = {"snapshot":"Snapshot","screenshot":"Screenshot"}.get(args.command, args.tool)
    if name not in EXPECTED:
        raise Failure("tool_not_exposed_refused")
    return http.rpc("tools/call", {"name":name, "arguments":arguments(args)}, 3)
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status","start","stop","tools","snapshot","screenshot","call"])
    parser.add_argument("tool", nargs="?")
    parser.add_argument("--arguments-file")
    parser.add_argument("--arguments-stdin", action="store_true")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 30:
        parser.error("timeout must be between 1 and 30 seconds")
    try:
        result = run(args)
        print(json.dumps(sanitize(result, OUTPUT, token() if TOKEN.is_file() else "\x00NO_TOKEN\x00"), ensure_ascii=False))
    except Exception as exc:
        # Never print urllib exceptions, server error bodies, headers or traceback.
        error = str(exc) if isinstance(exc, Failure) or str(exc) in {"token_missing_user_input_required", "token_invalid_user_input_required"} else type(exc).__name__
        print(json.dumps({"error":error,"replayed":False}))
        return 1
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
