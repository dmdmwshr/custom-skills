# Windows Codex 诊断平台适配

仅在实际执行系统匹配时读取。

- 命令入口：目标机 `Get-Command -All`、文件版本及实际运行路径
- Codex 状态：回读实际 `CODEX_HOME`，通常 `%USERPROFILE%\.codex`
- 进程：目标机进程可执行文件、PID、父子关系及启动时间
- 服务与日志：目标机实际服务或计划任务；不执行 WSL 的 systemctl 替代它
- 路径：盘符、UNC、扩展路径、重解析点和权限
- 代理：WinINET、WinHTTP、应用代理和 TUN 分层

Windows 使用其实际 PowerShell、应用进程和 CC Switch 发布入口。不可用时报告平台能力缺口，不能执行 WSL 的同名工具代替。当前分支没有在本次任务中实机验收。
