# Linux/WSL 与 Windows 运行环境

## 先定主机和入口

诊断前区分操作系统、执行主机、目标应用、项目路径和状态根。WSL 中能访问 `/mnt/c` 只证明挂载文件可读，不证明 Windows 进程、认证、浏览器或 IPC 在 Linux 可用；Linux CLI 与 Windows Desktop 版本也不能互相代表。

| 证据 | Linux/WSL | Windows |
| --- | --- | --- |
| 命令入口 | `command -v` / `type -a`，`readlink` 或 `realpath`，目标程序 `--version` | 目标机 `Get-Command -All`、文件版本及实际运行路径 |
| Codex 状态 | 回读实际 `CODEX_HOME`；本机为 `/root/.codex` | 回读实际 `CODEX_HOME`，通常 `%USERPROFILE%\.codex` |
| 进程 | 按目标 PID 查询 `/proc/<pid>/exe`、cwd 或不含 args 的 `ps` 字段 | 目标机进程可执行文件、PID、父子关系及启动时间 |
| 服务与日志 | 项目实际 systemd unit；按 unit 和时间窗口读 journal | 目标机实际服务或计划任务；不执行 WSL 的 systemctl 替代它 |
| 路径 | POSIX 路径、大小写、符号链接、权限和挂载边界 | 盘符、UNC、扩展路径、重解析点和权限 |
| 代理 | 按已知键读取是否配置、是否带凭据，输出脱敏结论；区分环境代理与应用代理 | WinINET、WinHTTP、应用代理和 TUN 分层 |

本机 WSL 主入口是 `/root/.local/bin/codex`；桌面内置副本只在目标进程证据指向它时检查，不替换主入口或自动更新。标准库诊断可用本机 `python3`；项目脚本使用其已声明虚拟环境。禁止全量输出进程命令行、环境变量、配置、网络连接列表或 journal；只取已定位对象的必要字段。

## 会话接口和版本判断

优先使用当前暴露的官方会话工具。没有上层工具时，先检查目标机 CLI 的 app-server 帮助与生成 schema，确认公共协议实际包含所需方法。支持的 `thread/list`、`thread/read` 等只读方法可用于受限核对；不因工具未注入模型就判定 Linux 不支持或版本过低。

底层方法存在不证明 Desktop 保存项目归属、工作树创建、UI 可见性和跨主机路由均已验收。只有同对象回读能证明结果。文档化 API 调用与手写数据库是不同路径：前者按授权和当前 schema 使用，后者不用于模拟会话管理。归档、改名或恢复使用实际可验证的同一主机接口，超时先对账。

启动独立 app-server 用于只读接口验收时，只管理本次创建的进程，结束时关闭它；不停止 Desktop 正在使用的服务，不启动模型 turn。权限或接口不足时完成可独立验证层并说明缺口，不扫描全部历史来补足证据。

## SQLite、索引和归档

只在症状明确涉及本机状态层时读取精确文件。Python SQLite 使用 URI `mode=ro` 和查询只读约束，先检查表结构，再查询最小元数据；不假定每版字段相同。活跃 WAL 不使用 `immutable=1` 冒充一致视图，不 checkpoint、VACUUM、修库或把主文件复制称为一致备份。只读连接失败即报告该层未验证。

挂载盘上的 Windows 活跃数据库不作为 Linux 本机状态诊断对象。需要检查另一主机时，优先目标机受支持只读接口或已有一致备份；普通技能部署不授权复制会话历史和数据库。

## 浏览器与平台专用参考

当前用户未明确要求浏览器自动化时，不启动浏览器控制、不创建测试标签；先做文件、版本、目标进程或接口诊断。明确请求后才按当前浏览器工具文档验证本次自有标签。

`browser-control-recovery.md` 中 PowerShell、Hidden、Windows X 启动器和 Browser Tamer 只属于 Windows。Linux 使用项目已有原生启动器；没有已验证入口时记录缺口，不安装替代浏览器、不复用 Windows 用户数据、不关闭沙箱来绕过 root 启动限制。浏览器宿主可与 CLI 主机不同，必须按实际浏览器工具返回的宿主证据判断。

本 Skill 的安装发布沿用各主机技能管理器。WSL 用 `/root/workspaces/custom-skills/scripts/publish_wsl_skills.py --skill codex-local-state-diagnostics` 预演后按授权 `--apply`、`--verify`；Windows 用本机已安装的 CC Switch 管理流程。安装成功不等于浏览器、网络或业务任务健康。
