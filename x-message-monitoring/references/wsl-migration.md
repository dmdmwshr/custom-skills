# WSL 身份、受管加载与发布

当前运行状态仅看业务仓 PROJECT_HANDOFF.md；本文不保存易过期的 PID/session/版本快照。源码 /srv/workspaces/X-monitor，运行 /srv/x-monitor/current，数据 /var/lib/x-monitor；公共路径按 /etc/codex-dev/paths.json 核对。
唯一 owner 保持原任务 01a08fbf-89c7-7f81-bd74-d81ce4f2a60c、Luna/max、原两小时 x；中枢只 direct_feishu 出站、入站 silent_drop。

## 正常进程重建

已接受的 hidden_cua_proof_ensure.py 是 root 受管入口。使用实际当前 CUA PID；已有证明沿 refresh 核验活进程/原官方回执/见证 FD，剩余不足四小时才续期。不要在普通轮带 --refresh。
新 PID 没有证明时，原任务运行入口 --print-helper 提供的固定元数据代码一次，保留非秘密见证 FD，取得本次官方 cua_repl/js turn/call 回执；受管入口核验原任务、当前 boot/进程链/FD、无旧执行者/业务守卫/在途/unknown 后登记。不得复制旧 PID 文件、修改 task_id 环境或读取失败正文。保留 worker 时必须证明最后一次 park 与真实 session 对应且之后无活动。
已过期、身份漂移、结果未知或旧执行者仍活动不能当普通重建；先只读核对实际状态。候选未安装时完成受管部署，不调用不存在的 API。具体参数与首次启用状态见项目当前说明。

## 加载与页面

只加载 current realpath 对应的已接受模块。公开 Node fs/vm、child_process、timers、Buffer/TextDecoder、URL、performance 按业务仓 LINUX_STARTUP_GUARD.md 注入；launch 返回立即保存，不能先后处理失败而丢失守卫。
固定 driver IIFE 返回对象，stdin/control IIFE 返回 factory；版本在 factory 核对。配对守卫连续覆盖准备到正常关闭，Python 正式入口另持同一共享锁，不自动删 marker。
hidden_chrome_mcp_adapter.js 直接导出 connect；连接后使用 adapter.driver。一个物理 MCP 连接/真实工作页，逻辑客户端逐轮更换，reuseTab 不按标题或 URL 认领未知页，park 不执行物理关闭。
页面丢失、浏览器重启、连接未知不自动新页或重连；真正人工接管先排空断开，普通查看不等于接管。绿色 Playwright·Hidden 是组名，不是额外页。
元数据通过、MCP 控制、X 登录和业务成功分别记录。正式仅 hidden_chrome_mcp，旧 CLI/native/日常扩展/Python/Windows 启动路径均不调用。

## 开发与发布

模块测试按影响运行；完整整合后一次全回归。driver 分片构建 --check，两个 reader 精确哈希不变时复用原受审包和物理连接，不因文件组织变化重启浏览器。
runtime-files.json 显式白名单，维护工具/测试不入生产。沿 cc-connect-operations/scripts/upgrade_linux_runtime.py 的真实发布契约，用新的静止备份窗口；旧窗口不可重跑，不手动覆盖 current。
正式库仅 production_startup_guard＋SQLiteStore(read_only=True) 正常关闭，不能裸 mode=ro 或为消空 WAL 手删旁车。
暂停原 x 后受控切换，原 owner 一轮新鲜双流及受影响的新进程/单页复用验收通过即恢复同一两小时调度；长期观察不机械要求固定三加四轮。候选、安装、实际业务分别留证。回退本次程序/配置，不回滚新增业务事实。
