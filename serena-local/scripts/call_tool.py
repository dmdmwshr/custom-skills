"""Call the official Serena MCP interface in an explicit local project."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


async def run(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    project = args.project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Project must be a directory')
    runtime = Path.home() / '.local/share/codex-tools/serena/1.7.0'
    state = runtime / 'state'
    state.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(SERENA_HOME=str(state), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    parameters = StdioServerParameters(command=str(runtime / '.venv/Scripts/serena.exe'),
        args=['start-mcp-server', '--project', str(project), '--context', 'codex',
              '--mode', 'interactive', '--mode', 'editing', '--add-mode', 'no-memories',
              '--enable-web-dashboard', 'false', '--enable-gui-log-window', 'false',
              '--open-web-dashboard', 'false', '--log-level', 'WARNING'], env=environment)
    with (state / 'last-call.log').open('w', encoding='utf-8') as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                if args.list:
                    print(json.dumps([tool.name for tool in listing.tools], ensure_ascii=False))
                    return
                name = args.describe or args.tool
                tool = next((tool for tool in listing.tools if tool.name == name), None)
                if tool is None:
                    raise ValueError(f'Tool unavailable: {name}')
                if args.describe:
                    print(tool.model_dump_json(exclude_none=True))
                    return
                values = json.loads(args.arguments_file.read_text(encoding='utf-8-sig')) if args.arguments_file else {}
                result = await session.call_tool(name, values)
                print(result.model_dump_json(exclude_none=True))
                if result.isError:
                    raise RuntimeError('Serena reported a tool error; see returned content')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--list', action='store_true')
    action.add_argument('--describe')
    action.add_argument('--tool')
    parser.add_argument('--arguments-file', type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
