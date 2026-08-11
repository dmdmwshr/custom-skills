# Windows 任务清单

| 字段 | 内容 |
| --- | --- |
| project_key | `meifu-resumable-download` |
| project_code | `MEIFU` |
| 稳定编号 | `AUTO-01` |
| TaskPath | `\DevProjects\MEIFU\AUTO\` |
| TaskName | `DEV-MEIFU-AUTO-01-DownloadQueue` |
| 用途 | 用 Windows 后台单工作进程处理已批准的通用 Meifu 下载队列；Codex 只入队、启动和读取状态。 |
| 动作入口 | 固定 Python 3.12 的受管 GUI 启动器（配套 `pyvenv.cfg` 指向已核验的基础标准库）执行 `meifu_download_queue.py scheduled-run`。 |
| 工作目录 | 本 skill 的 `scripts` 目录。 |
| 运行时数据 | `%LOCALAPPDATA%\MeifuDownloadQueue\queue.json`、控制文件与脱敏日志；最终文件和下载器断点状态仍在用户指定输出路径。 |
| 触发器 | 当前用户登录后，以及每 30 分钟。 |
| 运行账户与权限 | 当前交互用户、`Interactive`、`Limited`。 |
| 并发策略 | `IgnoreNew`；队列锁额外保证单工作者、单个 Meifu 分块。 |
| 时限与重试 | 365 天执行时限；临时网络故障以 15 分钟起始、最长 6 小时的指数退避自动续传。 |
| 电源与网络 | 允许电池运行且不停机；要求网络可用、错过触发后可运行。 |
| 安全边界 | 仅无签名 HTTPS 公共直链可持久入队；签名链接只允许单文件 stdin 模式。拒绝代理跳板，不读取凭据、不执行下载文件。 |
| 缓存治理 | 正常完成时下载器清理该任务的 Meifu 远端目录与本地临时目录；暂停/断网保留断点。未完成远端缓存继续受 72 小时无活动和低于 8GiB 可用空间时的既有清理器治理。 |
| 生命周期脚本 | `scripts/install-meifu-download-queue-task.ps1`、`scripts/show-meifu-download-queue-task.ps1`、`scripts/uninstall-meifu-download-queue-task.ps1`。 |

安装、更新、启停或删除任务前，先用查看脚本回读本机实例和此清单。安装脚本只操作上述精确身份；卸载脚本默认 dry-run，且不删除队列、断点、最终文件或 Meifu 缓存。
