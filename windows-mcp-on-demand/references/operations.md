# 操作与部署

## 固定入口

- 运行时：D:/Program_Files/Windows-MCP-Runtime/0.8.7-skill-v1/.venv，官方0.8.7、Python3.14.3。旧环境保留；该版本有鉴权和工具裁剪，无需升级0.8.8的输入接管行为。
- 服务：127.0.0.1:18765/mcp，stateless HTTP，令牌仅Windows受限文件注入；无外部监听、遥测、默认用户配置或常驻MCP依赖。
- 凭据：D:/12070/AppData/WindowsMCP/credentials/auth.key，仅12070和SYSTEM可访问。脚本不输出值。截图在同根artifacts，运行标识在runtime，安装XML恢复点在receipts。
- 唯一源：/srv/workspaces/02-software/02-25-custom-skills；受管安装只走02-25发布器、Windows CC Switch精确-Skill发布以及既有Claude链接同步器。不要编辑安装副本。

## Windows

```powershell
$py='D:\Program_Files\Windows-MCP-Runtime\0.8.7-skill-v1\.venv\Scripts\python.exe'
$cli='C:\Users\12070\.codex\skills\windows-mcp-on-demand\scripts\client.py'
& $py -B -X utf8 $cli status
& $py -B -X utf8 $cli start
& $py -B -X utf8 $cli tools
& $py -B -X utf8 $cli snapshot
& $py -B -X utf8 $cli screenshot
& $py -B -X utf8 $cli call Type --arguments-file 'D:\authorized-work\type.json'
& $py -B -X utf8 $cli stop
```

先用tools返回的当前schema准备参数；例子路径是占位，不能直接执行。读取截图用文件工具；返回Windows路径不能直接当成WSL路径，WSL可在/mnt/d按对应相对路径读取文件。

## WSL Claude / Codex

```bash
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py status
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py start
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py tools
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py screenshot
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py call Type --arguments-file /path/to/authorized-arguments.json
python3 -B ~/.codex/skills/windows-mcp-on-demand/scripts/wsl_client.py stop
```

包装通过SSH标准输入传JSON，令牌不离开Windows，不另建隧道或Claude MCP登记。只读/已知未执行的失败可修正后重试；输入或启动结果未知时用status和原现场对账，不盲重放。

## 专用任务清单与生命周期

project_key=codex-env-win，project_code=CODEXENV，id=LIVE-01。
唯一任务：\DevProjects\CODEXENV\LIVE\DEV-CODEXENV-LIVE-01-WindowsMCP。
当前交互用户12070，Limited，Interactive，无触发器；IgnoreNew，30分钟上限，无失败重试、唤醒或错过补跑，电池可运行。任务存在但不会自动启动。
动作：运行时.venv/TaskScripts/pythonw.exe，参数 -B "<已安装Skill>/scripts/server.py"；工作目录为运行时根。TaskScripts来自该Python版本的GUI venv启动器，须核PE subsystem=2、摘要及独立探针的解释器/子进程，不能因名字pythonw判无窗口。

安装/精确更新：powershell.exe -NoProfile -File <已安装Skill>/scripts/setup.ps1 -Action Install；只读Show；精确卸载Remove。更改前保存已有XML，发现活动目标延期，不自动停止或覆盖。脚本只针对清单精确身份；未知同名任务拒绝。服务停止用客户端stop，不按进程名称杀进程。

## 用户本人录入令牌

任务和受限目录安装验证后再请用户操作，助手停下等待：
1. 用户在Windows本机打开PowerShell。
2. 运行 `powershell.exe -NoProfile -File "C:\Users\12070\.codex\skills\windows-mcp-on-demand\scripts\Set-Token.ps1"`。
3. 输入自己准备的至少32字符、无空白令牌，两次一致。输入隐藏；不要发到聊天或放在命令参数中。
4. 看到保存成功后只回复“已录入”。脚本不会启动服务；助手只核文件存在及权限，不打印内容，再继续已授权验证。

脚本拒绝覆盖现有令牌。后续轮换须明确范围并保留受限恢复副本，不用删除旧文件绕过。

## 独立真实验收

录入后先核交互会话、当前用户界面占用和主工具控制者。本机先完成start、tools（五项排除及未认证被拒）、Screenshot文件、Snapshot元素、自建记事本中文输入回读、不保存关闭、stop及端口/自有进程无残留；然后WSL经SSH同一客户端重复。短暂前台只针对自建窗口，前后核鼠标/焦点并恢复。不因后台服务运行而宣称后台键鼠隔离。运行标识、实际会话、原始图片/文字及时间写仓外回执。

缺令牌、版本/任务契约不符、端口被未知进程占用时不启动；未知运行不重放。start不ready先status，stop不完整保留PID证据，不扩大杀进程。未通过的层分别记录，不改模型、网络、其他工具，也不重启桌面或现有app-server。
