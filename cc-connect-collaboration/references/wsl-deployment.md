# WSL 原生部署

仅当实际目标是 WSL 时采用本节；Windows 专属路径、CC Switch 发布和任务计划示例不作为 WSL 默认入口。

- 源码位于 `/root/workspaces/cc-connect-operations`。本机 `CODEX_HOME=/root/.codex`；Windows 与 WSL 状态隔离，不复制认证、数据库、索引或会话历史。
- 复用已有 `codex-cc-connect.service` 与原生 cc-connect；网关使用 `cc-connect-operations.service`，只监听 `127.0.0.1:8765`。两个服务不创建扫描定时器，不调用 Windows 可执行文件。
- 配置分属 `/etc/codex-cc-connect` 与 `/etc/cc-connect-operations`；运行数据分属 `/var/lib/codex-cc-connect` 与 `/var/lib/cc-connect-operations`；版本化网关发布位于 `/srv/cc-connect-operations/releases`。私有文件权限 0600、目录 0700，日志只含脱敏状态。
- `--staging` 使用独立 staging 账本，强制无令牌、无来源解析、无派发。其新 epoch 不能冒充生产账本。生产入口必须先存在一致迁入的账本，不自动建空生产库。
- Linux 直送仍由私有子进程接收 UTF-8 stdin，经独立绑定与项目校验后访问受限 UDS。200 且合法 `status=ok` 才记传输接受；发送开始后异常全部未知且不重发。X 直送只用 A 模式门禁；新扫描仍必须有唯一 Desktop 所有者和 heartbeat 证明。
- 切换前精确停止旧桥接并暂停旧 X 自动化，确认业务锁空闲，取得受限一致性备份与回读。缺失停机、备份或受支持会话迁移入口时保留关闭派发的准备态。不能通过更改数据库制造已迁移证明。
- 安装与更新使用 WSL 源仓的 `scripts/publish_wsl_skills.py`，先 dry-run，校验 Skill 结构后 `--apply`、`--verify`；记录提交、源修改状态和文件哈希，再由新 Codex 会话验证发现与加载。不编辑安装副本，不要求 Windows CC Switch 才能发布 WSL 更新。
