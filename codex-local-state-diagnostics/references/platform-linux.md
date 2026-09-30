# Linux/WSL Codex 诊断平台适配

仅在实际执行系统匹配时读取。

- 命令入口：`command -v` / `type -a`，`readlink` 或 `realpath`，目标程序 `--version`
- Codex 状态：回读实际 `CODEX_HOME`；本机为 `${CODEX_HOME:-$HOME/.codex}`
- 进程：按目标 PID 查询 `/proc/<pid>/exe`、cwd 或不含 args 的 `ps` 字段
- 服务与日志：项目实际 systemd unit；按 unit 和时间窗口读 journal
- 路径：POSIX 路径、大小写、符号链接、权限和挂载边界
- 代理：按已知键读取是否配置、是否带凭据，输出脱敏结论；区分环境代理与应用代理

本机维护配置存在时，CLI 用 `/usr/local/bin/codex`，实体在公共工具目录，活动 CODEX_HOME 仍来自进程环境；其他主机先 command -v，不硬套本机路径。服务采用本机 systemd/实际启动器，不能操作宿主机计划任务。

重启后桌面提示重新登录，先区分凭据失效与启动期间认证查询失败：核对状态目录及权限、实际代理环境和同窗连接日志，不读取令牌或把网络超时当作凭据丢失。`network-online.target`、代理进程和监听端口不证明 HTTPS 已可用；图形代理还可能晚于用户级后台启动。已授权修复时，在新后台/桌面启动前加入使用原代理、验证 TLS 的无凭据公开端点探针，有限等待并退避重试；失败不得静默另起内置后台、清凭据或关闭已有离线会话。就绪探针由主机配置维护，不依赖用户手动进入远程桌面。用隔离 systemd 服务验证断网阻止启动及联网自动接续，区分配置加载、隔离恢复、账号状态与整机冷启动验收；不为验证重启活动后台或宿主机。

桌面更新或重启后出现MCP握手失败、产品工具消失，核对当前应用进程及受管socket alias实际目标是否存在、是否属于当前实例；旧alias指向已消失socket是可验证的独立原因，不能据此倒推升级期间更早的通用错误码。已授权修复时按主机codex-runtime.md现行受管helper/launcher恢复别名与自修能力，不硬编码本次socket或手造服务。随后用官方daemon的config/mcpServer/reload重新加载并回读。当前工具未重新暴露时，可按已核实官方schema使用mcpServer/tool/call调用原产品工具，保留真实threadId、工具审批与原参数；不是绕过审批或直连自造调度接口。恢复后回读原对象和持久阶段，未知先对账，不手写automation.toml或Codex数据库；绑定恢复与旧任务真实归档由对应业务/协作技能续接。
