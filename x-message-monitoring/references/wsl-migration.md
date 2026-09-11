# WSL 迁移边界

WSL 维护时源码位于 `/root/workspaces/X-monitor`，使用项目 Linux `.venv/bin/python`；固定入口、标准输入客户端和数据目录必须一起核验，不能把 Windows 路径机械当成本机路径。业务持久数据迁入 `/var/lib/x-monitor`，不复制 Windows Python 环境或 Codex 状态。

维护任务可适配源码和运行离线测试，但不能代替唯一 Desktop 固定任务浏览或扫描。原固定任务及 heartbeat 只能经受支持产品入口迁移并回读；没有该能力时停止在准备态，不建第二任务、cron、独立 Playwright 扫描器或浏览器桥接。旧任务暂停、双锁空闲及一致备份须在实际切换时重新证明。

Windows 环境参考只适用于旧宿主。WSL 的 Skill 更新以 `/root/workspaces/custom-skills` 为源，使用仓库 `scripts/publish_wsl_skills.py` 校验发布；来源版本、文件哈希和新会话发现证据保留在本机治理目录。发布 Skill 不代表已迁移浏览器登录态或扫描恢复。


## X 业务发布接口（客户端 1.2.0 / 步骤 WSL-2026-09-11.1）

WSL 运行读取 `/srv/x-monitor/current/scripts/desktop_monitor_driver.js`（驱动 3.0.30）和同目录 `desktop_stdin_client.js`（客户端 1.2.0），固定子进程为 `/srv/x-monitor/current/.venv/bin/python`。客户端与驱动必须从同一发布目录原样加载，先比对指纹；原宿主 selfTest 61 项成功后才可 acquire。Linux 命令行 fixture 的成功不能代替 CUA 宿主自检。

固定控制入口为 `/srv/x-monitor/current/scripts/fixed_session_entry.py`。无论调用 cwd 为业务目录或中枢控制目录，正式账本固定 `/var/lib/x-monitor/x-monitor.sqlite3`。不存在账本时如实失败，不能初始化空库当作迁移成功；不从 cwd/data 或 Windows 活动目录回退。中枢只读解析器也必须显式使用同一 data-dir。

Linux 原生浏览器检查使用 `/srv/x-monitor/current/.venv/bin/python /srv/x-monitor/current/scripts/start_managed_browser.py --browser chrome`，该命令不读取浏览器配置内容或标签。不使用本技能 Windows 历史参考中的 PowerShell 启动器。在原固定任务已明确观察 browser_not_running 后才允许加 `--start` 一次；root 缺少浏览器实例时返回 start_requires_interactive_user，由用户处理交互式启动，不增加禁用沙箱参数。主用 Chrome；Edge 必须实际安装、原生可控且取得原机器备用授权，不能回退到 Windows 浏览器。

快速路径中的业务步骤、五项 sendKept、草稿/冻结各一次、两阶段 finish 及所有页面和整轮预算保持；其中 Windows 绝对路径与客户端 1.1.0 只属于旧宿主，WSL 以上述入口和 1.2.0 为准。正式激活前必须有旧任务已暂停、数据一致备份、原任务受支持转移与回读证明；仅安装发布目录、skill 或本地 selfTest 不构成激活。

## WSL 浏览器检查修订（2026-09-11.2）

启动检查通过父子关系排除 Chrome 子进程；默认目录以 SingletonLock 与主进程打开文件路径元数据交叉核验。没有读取配置、Cookie、正文或标签内容。无法证明目录时失败关闭，不因空参数当作未运行；already_running 只证明实例/数据根，extensionControlVerified 始终为 false，实际控制另行验收。

产品有原任务 handoff 能力不等于当前执行器已提供可调用工具或已连接 Windows 源任务。只能使用执行器正常提供的任务身份和受支持工具；不伪造 MCP 元数据，不改 Codex 数据库，不新建替代固定会话。Chrome 选择返回绑定不等于创建、控制、关闭标签成功，失败须逐项保留未知状态。
