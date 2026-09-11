# WSL 唯一固定会话与运行接口

## Linux 接替授权（步骤 WSL-2026-09-11.3，当前优先）

用户已批准一次 Linux 固定会话接替：保留原业务账本、每小时计划、Luna/max、飞书目标及规则，Windows 旧聊天归档保留。通过正常产品工具创建并初始化唯一 Linux 固定会话，随后绑定暂停的 heartbeat；不伪造 MCP 调用元数据或修改 Codex 数据库。旧宿主的同 ID 转移、禁止新会话和 CUA-only 限制不再适用于这次已授权接替。迁移后仍只有一个活动固定会话及一个扫描计划；维护任务不代替它扫描。

实际归属和当前准备/激活状态读取业务仓 `/root/workspaces/X-monitor/LINUX_FIXED_SESSION.md` 与受限迁移回执；不得从标题推定已上线。源仓 `/root/workspaces/custom-skills`，受管发布须已提交、与实时远端一致、按发布器安装并在新进程验证发现。安装不能代替业务验收。

## 固定接口

- 工作目录 `/root/workspaces/X-monitor`；代码不可变发布 `/srv/x-monitor/current`；正式账本始终 `/var/lib/x-monitor/x-monitor.sqlite3`，不存在时停止，不能建立空库代替迁移。中枢只读解析器使用同一 data-dir。
- 驱动 `scripts/desktop_monitor_driver.js` 3.0.31；标准输入客户端 `scripts/desktop_stdin_client.js` 1.2.0；Python `/srv/x-monitor/current/.venv/bin/python`，固定控制入口同发布的 `scripts/fixed_session_entry.py`。均从同一发布原样加载并核对版本与指纹。
- Linux 浏览器适配 `scripts/linux_browser_runtime.js` 1.0.0，后端 `playwright_linux`，真实来源标记 `desktop_chrome_playwright_linux`，不得冒称扩展。配置 `/etc/x-monitor/browser.json`，专用持久配置 `/var/lib/x-monitor-browser/profile`，锁 `/var/lib/x-monitor-browser/owner.lock`。仅 owning Node 宿主调用已有 Linux Playwright 组件；无独立扫描进程、CDP listener 或新定时器。已批准 root Chrome 运行配置沿用本机浏览器基线。
- 固定后端在 acquire 前读取并加载；轮内不切换。保留本页15秒、单次40秒、回复八导航、20分钟租约和双流独立提交。原驱动所有严格身份、UTC、正文、引用和停止条件保持；页面适配只转换 API/timeout。只允许原驱动两个已审 DOM reader；AX 刷新文本不输出，超时未知关闭自有运行时，不重放失败页。

## 静态准备与浏览器控制

Linux 原生组件在执行器正常提供的持久 `node_repl` 中使用公开 Node 标准库加载。在同一宿主保留 `xMonDriver`、`xMonStdinFactory`、`xMonStdin`、`xMonBrowser`、本轮自有 `xMonTab`。驱动和客户端仍为原样工厂，不转写函数。使用 `node:module` 的 createRequire 加载固定发布的 linux_browser_runtime.js；用该模块 `launch()` 获得 runtime，再 `newTab()`。适配层返回与原驱动兼容的 tab，所有固定浏览器方法调用签名保持。每次工具仍只调用一个固定浏览器采集方法或一次 sendKept；不写现场循环合并多个页面。

首次或宿主重置后，在实际 owning Node 宿主执行客户端无参数 selfTest，61项 exact_match=true，才可 acquire；独立命令行 fixture 不是这项证据。模块来自同一 `/srv/x-monitor/current`，公开 Node 的 spawn/Buffer/TextDecoder/计时器注入遵循客户端原工厂签名。

专用 profile 需要人工登录时，只能在 `launch({manualLogin:true})` 后 `showLogin()` 打开本机 GNOME 窗口。`loginStatus()` 只回传已登录导航是否可见，不读取账号凭据或 Cookie；不复制日常 Chrome 登录资料。人工登录后 `close()` 释放 profile 锁，正式 owning 宿主再 `launch()`。并发占用返回 browser_profile_busy，不能强抢锁或关闭日常 Chrome。

扩展控制恢复可单独维护，但实例、扩展连接和页面控制分别验收。`start_managed_browser.py --browser chrome` 只证明 Linux 实例元数据，不能当作扩展成功；不得回退 Windows 或在失败轮切浏览器。

## 每轮业务与清理

acquire 前完整读取当前快速路径和上下文契约的业务部分；其中 Windows 路径、CUA 宿主和扩展 source 在本 Linux 分支分别使用上述发布路径、原生 Node 宿主和真实 Linux source。五项 sendKept、内存载荷、语义筛选、fingerprint、草稿/冻结各一次、预检顺序与两阶段 finish 全部保持。

lease 仍只在 functions 与 owning Node 内存中传递，acquire 同次保存原回执，后续控制从该对象取值；不输出凭证。浏览前证明两侧本轮值相等。公开事实不进入文件、临时正文、浏览器存储或普通日志。共同故障停两流，局部失败保留另一流处理；真实 unknown 不重试。失败可用固定入口 browser-failure 的 browser=linux-playwright，采集遥测使用真实来源和驱动版本。

两阶段 finish、关闭自有 tab/runtime、无在途子进程后，clearKept 再清理 lease 与其他动态事实；静态工厂可复用。自有 runtime 关闭释放专用 profile 锁；不能触碰用户其他 profile/标签。只有真实完整成功且机器允许才输出 DONT_NOTIFY，手动不计四轮摘要。

## 切换验收

旧 Windows X heartbeat 精确暂停、无额外旧扫描入口、双锁空闲、无待收口/在途提交后，制作并完整迁入一致备份。中枢另一个任务复用既有密钥/原连接，保留 X 幂等并绑定 Linux 新 owner，仅开 X 路由。未知投递保留不重发。旧数据不删。

回读新 owner 主机、模型、heartbeat 暂停、旧任务停止、数据库完整性/记录数/水位/状态及路由证明后，才授权唯一固定会话真实宿主自检和人工双流验收。首轮成功后两次全新复扫，全部通过才启用每小时 heartbeat。传输接受与送达分别记录；无新合格通知时不制造消息，送达保持待验证。Linux 写入后回退先停写对账，不能直接恢复旧快照。
