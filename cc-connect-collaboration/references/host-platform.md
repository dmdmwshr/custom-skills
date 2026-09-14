# cc-connect-collaboration：执行系统与路径预检

仅在当前任务匹配本技能时读取本页。先确定实际执行主机和目标主机，再读取匹配系统的后续说明；路径包含 Windows/WSL 或能读取挂载盘，不代表具备该主机的执行能力。

## Linux / WSL

核对实际内核、发行版、id、HOME、CODEX_HOME 和已解析的工具入口。Linux 内核包含 microsoft 时判为 WSL；环境变量只能补充上下文，不可把 Windows 进程伪装为 Linux。当前主机若存在 `/etc/codex-dev/paths.json`，公共项目/资料路径以它及 `CODEX_PROJECTS_ROOT`、`CODEX_WORK_ROOT` 为准；否则按目标机实际目录规范解析，默认回退 HOME/workspaces 和 HOME/Documents/work。程序可公用，认证、会话和用户缓存保持实际用户私有。

维护源仓的 `scripts/skill_platforms.py --skill cc-connect-collaboration` 可只读回读系统和配置；它不证明业务服务、浏览器、凭据或外发能力可用。发布器在任何安装写入前再次拒绝不匹配平台，不能靠增加 frontmatter 字段假定客户端自动过滤。

Linux/WSL 按需继续读取 [wsl-deployment.md](wsl-deployment.md)；不要加载 Windows 的计划任务、CC Switch 或注册表操作分支。

## Windows

核对实际目标进程、PowerShell/解释器、USERPROFILE 和 CODEX_HOME。源仓与安装记录由该机 CC Switch 维护，使用原生路径、编码和服务/计划任务接口。不要读取 Linux 路径配置来替代 Windows 配置，也不要把 WSL 原生发布器当作 Windows 安装器。

Windows 的脚本或业务依赖只有在该主机实际验证后才称可用；本次仅做源分支审查，Windows 安装及实机验收未执行。跨主机请求先核对可用协作入口，没有入口时准确报告，不切换到同名本机项目或私自重建身份。

## 共用不变量

系统分支不增加操作授权。原业务冻结路径、绑定代次、租约、投递和未知结果规则继续有效；旧兼容链接只代表当时迁移阶段，本机已清理的入口不为排障重建。目录变更通过项目受控配置事务，不能为统一目录直接改写签名字符串、历史任务或认证。一次只读匹配的系统分支及当前任务所需资料，不全量加载 refs。
