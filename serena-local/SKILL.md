---
name: serena-local
description: 使用本机 Serena 按函数、类和引用关系检索或修改代码，在普通文本搜索不足以定位跨文件符号时作为替补。限定用户指定项目，使用开源语言服务，不代替简单 rg 搜索，不处理个人事实记忆。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Serena 代码符号替补

Windows 固定版本 1.7.0，运行目录 `C:/Users/12070/.local/share/codex-tools/serena/1.7.0`。已验收 Python；TypeScript 等语言需先检查其语言服务可用性，不能以 Python 结果代替。

简单文本或文件名搜索继续用 `rg`。需要了解函数定义、调用引用或完整修改某个函数体时采用 Serena。先确认用户项目及已有改动；缓存和索引只属于该项目，初始化可创建 `.serena` 目录，避免把机器缓存纳入提交。Serena 的项目记忆不是用户 Graphiti 事实库，不承担个人记忆登记。

使用本技能脚本按需启动官方 MCP 子进程，退出后关闭连接，不注册全局常驻服务，不打开浏览器或日志窗口：

```powershell
$py = "C:/Users/12070/.local/share/codex-tools/serena/1.7.0/.venv/Scripts/python.exe"
$entry = "$env:USERPROFILE/.codex/skills/serena-local/scripts/call_tool.py"
& $py -X utf8 $entry --project "<项目绝对路径>" --list
& $py -X utf8 $entry --project "<项目绝对路径>" --describe find_symbol
& $py -X utf8 $entry --project "<项目绝对路径>" --tool find_symbol --arguments-file "<UTF-8参数JSON文件>"
```

先读取目标工具的当前参数说明，再调用，不能猜参数。常见入口为 `find_symbol`、`find_referencing_symbols`、`get_symbols_overview`；定位后按需读取函数体，避免整仓输出。修改使用 `replace_symbol_body` 等实际发现的工具，只限当前授权范围，随后检查差异并跑与变更相称的测试。工具报错或找不到符号时保留结果，回到普通源码检索，不盲目重复修改。

官方依据：[Serena](https://github.com/oraios/serena)、[配置说明](https://oraios.github.io/serena/02-usage/050_configuration.html)。
