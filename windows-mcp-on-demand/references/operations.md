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

截图像素和Snapshot/UI树的数据范围分别核实。只有当前schema真实支持且已验证的region/窗口限制才可采用；区域截图不证明UI树也仅含目标窗口，也不证明键鼠隔离。优先精确自有目标和所需最小范围；若工具仍返回其他窗口正文，明确记录实际范围，按授权选择已核能力的兼容入口，不把裁图、删除本地图片或撤回报告当作已消除读取/发送。

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


## 2026-10-23 实测结论与窗口定位约束

用户本人完成隐藏令牌录入后，Windows本机和WSL经SSH分别执行了真实启动、工具列举、截图文件、Snapshot中文编辑值、中文记事本输入及不保存关闭。两端最终status均为stopped；服务只监听127.0.0.1，实际会话1、Limited/Interactive/无触发器，停止后无监听和自有进程残留。缺少或错误令牌的直接HTTP请求均被401拒绝；15项实际工具中不含五项排除项，客户端对五项也拒绝。

这是主责Codex实测；未参与实现的Codex独立读取原始回执、截图及停止现场，不能表述为另一操作者重复完整GUI验收。用于通过判定的两张图片仅含自建测试窗；早期截到其他窗口的原失败图片保留，不计入通过证据。令牌不写入回执或WSL。最终鼠标/焦点恢复、原用户Notepad保留并恢复最小化。

实测发现上游0.8.7的App按近似标题匹配，且switch可能沿用上一次Snapshot的窗口缓存。一次空白测试窗在输入前消失，原因未确认；随后模糊匹配误展开原用户Notepad，未向其输入，已恢复。不得把App返回成功当作精确目标证明。 客户端退出码0仅表示成功取得MCP响应；必须再检查结果isError=false和实际界面，isError=true仍属工具失败。本次Application not found即出现退出码0且isError=true，不能仅凭退出码继续输入。

1. 切换前取目标范围内可用的新状态，确认准确标题、唯一窗口和句柄；需要全桌面身份核对时尽量只取必要窗口清单。分别检查截图与UI树范围，不能因region参数或区域窗口清单宣称完整隔离。
2. App动作后重新核对实际句柄、控件和前台，再发送输入。菜单/目标在最新截图或树中不可见、定位未证实、同名、遮挡或句柄改变时不得猜坐标盲点或连续试点；先重新读取状态，或在已核精确自有前台中使用当前文档支持的快捷键/已核兼容工具。用户已暂停时这些替代输入也不得继续。
3. 每次输入前后核当前控制者及用户活动；检测到用户鼠标/键盘活动立即停止输入、保留断点，不连续尝试抢焦点。只有当前授权允许且用户空闲时才重新读取状态后继续；用户明确暂停或禁止前台时等其新的继续指令，不自动恢复或换通道。动作后核真实效果及原前台、鼠标、输入焦点恢复；未恢复如实记录，不覆盖用户正在使用的焦点。本工具用真实前台键鼠，不宣称副屏隔离；Cua等兼容路径仍须核精确窗口与当前能力，按需互补。
4. 不保存关闭须读取当次对话框中的按钮位置，动作后核精确自建句柄消失；不要沿用另一窗口的坐标，或按Notepad进程名称关闭用户窗口。

成功、工具失败、未派发、撤回/取消和输入效果未知分别记录；未知输入先对账，不重复。未恢复焦点、已读取其他窗口正文或已发送的截图不能靠删除本地文件、撤回材料改为通过；原失败和实际曝光范围保留，后续纠正另记。

本次曾因控制者切换而暂停输入/关闭，外部验证脚本还出现过陈旧坐标和PowerShell管道编码断言失败；这些断言失败均在相应输入派发前停止，原失败保留。验证脚本传中文时显式使用UTF-8。报告及证据入口为03-05项目PROJECT_HANDOFF.md与D:/12070/Documents/claude-codex/claude-enhancement-20261022/REPLY-tools.md。
