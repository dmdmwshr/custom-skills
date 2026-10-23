"""WSL transport to the same Windows client; no remote credential copying."""
import argparse,json,pathlib,subprocess,sys
CLIENT='C:/Users/12070/.codex/skills/windows-mcp-on-demand/scripts/client.py'
PYTHON='D:/Program_Files/Windows-MCP-Runtime/0.8.7-skill-v1/.venv/Scripts/python.exe'
def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['status','start','stop','tools','snapshot','screenshot','call'])
    p.add_argument('tool',nargs='?')
    p.add_argument('--arguments-file')
    p.add_argument('--timeout',type=int,default=15,choices=range(1,31))
    a=p.parse_args()
    if a.tool and (not a.tool.isascii() or not a.tool.isalnum()):p.error('Invalid tool name')
    data=None
    if a.arguments_file:
        value=json.loads(pathlib.Path(a.arguments_file).read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict):p.error('Arguments must be an object')
        data=json.dumps(value,ensure_ascii=False).encode('utf-8')
    command=f'{PYTHON} -B -X utf8 {CLIENT} {a.command}'
    if a.tool:command+=' '+a.tool
    command+=f' --timeout {a.timeout}'
    if data is not None:command+=' --arguments-stdin'
    try:
        result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','windows-host-codex',command],input=data,capture_output=True,timeout=120)
    except subprocess.TimeoutExpired:
        print('{"error":"ssh_timeout_result_unknown_do_not_replay"}')
        return 1
    sys.stdout.buffer.write(result.stdout)
    if result.returncode and not result.stdout:print('{"error":"ssh_failed_inspect_before_retry"}')
    return result.returncode
if __name__=='__main__':raise SystemExit(main())
