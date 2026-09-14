# Linux/WSL Codex 诊断平台适配

仅在实际执行系统匹配时读取。

- 命令入口：`command -v` / `type -a`，`readlink` 或 `realpath`，目标程序 `--version`
- Codex 状态：回读实际 `CODEX_HOME`；本机为 `${CODEX_HOME:-$HOME/.codex}`
- 进程：按目标 PID 查询 `/proc/<pid>/exe`、cwd 或不含 args 的 `ps` 字段
- 服务与日志：项目实际 systemd unit；按 unit 和时间窗口读 journal
- 路径：POSIX 路径、大小写、符号链接、权限和挂载边界
- 代理：按已知键读取是否配置、是否带凭据，输出脱敏结论；区分环境代理与应用代理

本机维护配置存在时，CLI 用 `/usr/local/bin/codex`，实体在公共工具目录，活动 CODEX_HOME 仍来自进程环境；其他主机先 command -v，不硬套本机路径。服务采用本机 systemd/实际启动器，不能操作宿主机计划任务。
