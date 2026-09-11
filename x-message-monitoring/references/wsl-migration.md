# WSL 唯一固定会话与运行接口

## Linux 接替授权（步骤 WSL-2026-09-11.5，当前优先）

用户已批准一次 Linux 固定会话接替：保留原业务账本、每小时计划、Luna/max、飞书目标及规则，Windows 旧聊天归档保留。通过正常产品工具创建并初始化唯一 Linux 固定会话，随后绑定暂停的 heartbeat；不伪造 MCP 调用元数据或修改 Codex 数据库。旧宿主的同 ID 转移、禁止新会话和 CUA-only 限制不再适用于这次已授权接替。迁移后仍只有一个活动固定会话及一个扫描计划；维护任务不代替它扫描。

实际归属和当前准备/激活状态读取业务仓 `/root/workspaces/X-monitor/LINUX_FIXED_SESSION.md` 与受限迁移回执；不得从标题推定已上线。源仓 `/root/workspaces/custom-skills`，受管发布须已提交、与实时远端一致、按发布器安装并在新进程验证发现。安装不能代替业务验收。

## 固定接口

- 工作目录 `/root/workspaces/X-monitor`；代码不可变发布 `/srv/x-monitor/current`；正式账本始终 `/var/lib/x-monitor/x-monitor.sqlite3`，不存在时停止，不能建立空库代替迁移。中枢只读解析器使用同一 data-dir。
- 驱动 `scripts/desktop_monitor_driver.js` 3.0.31；标准输入客户端 `scripts/desktop_stdin_client.js` 1.2.0；Python `/srv/x-monitor/current/.venv/bin/python`，固定控制入口同发布的 `scripts/fixed_session_entry.py`。均从同一发布原样加载并核对版本与指纹。
- 用户现已选择 Linux 日常 Chrome 现有 Default 个人资料；当前配置 `/etc/x-monitor/browser.json` 的后端为 `chrome_extension`，browser=chrome、user_data_dir=/root/.config/google-chrome、profile_directory=Default。配置选择器 `scripts/linux_browser_config.js` 1.0.0 在 acquire 前只读加载并冻结；不启动浏览器、不读取个人资料内容。使用原扩展来源 `desktop_chrome_extension`，不能冒称原生 Playwright 来源。
- 旧 `playwright_linux` 专用配置及 `/var/lib/x-monitor-browser/profile` 保留停用。当前不调用其 launch，不复制 Cookie，不将 Playwright 指向日常 Chrome 目录，不因扩展故障自动恢复备用后端。原生启动器明确拒绝扩展配置和日常目录；两种后端都不新增独立扫描者。
- 固定后端在 acquire 前读取并加载；轮内不切换。保留本页15秒、单次40秒、回复八导航、20分钟租约和双流独立提交。原驱动所有严格身份、UTC、正文、引用和停止条件保持；页面适配只转换 API/timeout。只允许原驱动两个已审 DOM reader；AX 刷新文本不输出，超时未知关闭自有运行时，不重放失败页。

## 静态准备与浏览器控制

Chrome 扩展在唯一固定任务的 CUA 宿主运行；按当前工具文档选择已有 Chrome，在同一宿主保留 `xMonDriver`、`xMonStdinFactory`、`xMonStdin`、`xMonBrowser` 和本轮自有 `xMonTab`。驱动和客户端从同一Linux发布原样加载，公开Node标准库注入保持原契约；之前在独立Node宿主完成的selfTest不代替当前CUA宿主验证。每次工具仍只调用一个固定浏览器采集方法或一次sendKept，不写现场循环合并多个页面。

首次或宿主重置后，在实际 owning Node 宿主执行客户端无参数 selfTest，61项 exact_match=true，才可 acquire；独立命令行 fixture 不是这项证据。模块来自同一 `/srv/x-monitor/current`，公开 Node 的 spawn/Buffer/TextDecoder/计时器注入遵循客户端原工厂签名。

先验证本次自有空白标签创建、操作、关闭，以及实际Profile Path对应既有Default；随后独立验证X登录和新标签复用。Chrome的Google身份不等于X登录证明；需要认证时人工完成，不读取密码/Cookie。只关闭创建回执证明归属的标签，不关闭整个日常浏览器或用户旧标签。旧专用人工登录窗口已结束，不再要求用户登录旧专用profile。

扩展控制恢复可单独维护，但实例、扩展连接和页面控制分别验收。`start_managed_browser.py --browser chrome` 只证明 Linux 实例元数据，不能当作扩展成功；不得回退 Windows 或在失败轮切浏览器。

若工具日志证明产品会话浏览器路由缺失，可通过正常产品入口打开精确固定任务并回读路由登记；不伪造路由元数据或修改Codex内部数据库。只有环境实际变更后才做一次新的恢复验收。超时没有标签ID时不能猜归属或宣称已关闭，保持未确认状态并停止扫描。

## 配对导入全生命周期门禁

生产 driver 必须从同发布 `scripts/linux_guarded_runtime.js` 1.0.0 的 `launch({spawn,createDriver,createClient})` 返回对象绑定为 xMonDriver/xMonStdin。原样源码可提前读取；工厂回调同步实例化，不提前保留裸实例、不启动浏览器或执行业务。先取得官方 paired-import 共享锁才创建驱动/客户端，锁连续覆盖准备、CUA动作、空闲间隔、两阶段finish、标签/在途及kept清理；不能只包一次启动检查或每个Python动作。固定CLI也在打开正式库前持同一共享锁到close，五项sendKept保持。

持锁子进程不扫描、不接收正文/lease、不打开SQLite。它在 `/var/lib/cc-connect-operations/migration/x-driver-active.json` 写入无业务内容的运行标记；EOF/异常/坏帧留下标记，中枢导入/回滚必须拒绝，不能自动过期、删除或重开。只有真实两阶段finish、自有标签关闭、在途零、clearKept均已确认，才调用 runtime.close({twoPhaseFinished:true,ownedTabsClosed:true,inflightZero:true,keptCleared:true})；不得猜这些布尔值。运行时包装器另拒绝进行中关闭，正常握手才删除本轮原样标记并等待释放；关闭后driver/client全部失效。异常锁丢失立即停止，新运行前由主控核验静止，不用TTL当收口。

具体候选选择与已验收范围见业务仓 `LINUX_STARTUP_GUARD.md` 和总台账。未完成迁库时，仅可按主控明确授权独立做无生产driver的空白连接/纯合成selfTest；不能借诊断绕过生产门禁。双方current须由主控切换到含守卫和未收口拒绝的候选，代码测试通过不等于正式owner已持锁。

## 每轮业务与清理

acquire 前完整读取当前快速路径和上下文契约的业务部分；Windows路径替换为上述Linux发布入口，当前扩展仍在CUA宿主以真实desktop_chrome_extension来源运行。五项sendKept、内存载荷、语义筛选、fingerprint、草稿/冻结各一次、预检顺序与两阶段finish全部保持。

lease仍只在functions与当前CUA宿主内存中传递，acquire同次保存原回执，后续控制从该对象取值；不输出凭证。浏览前证明两侧本轮值相等。公开事实不进入文件、临时正文、浏览器存储或普通日志。共同故障停两流，局部失败保留另一流处理；真实unknown不重试。当前扩展失败登记browser=chrome，采集遥测使用实际来源和驱动版本。

两阶段finish、自有标签关闭、无在途子进程后，clearKept再清理lease与其他动态事实；静态工厂可复用。不关闭日常Chrome整体实例或其他profile/标签。只有真实完整成功且机器允许才输出DONT_NOTIFY，手动不计四轮摘要。

## 切换验收

旧 Windows X heartbeat 精确暂停、无额外旧扫描入口、双锁空闲、无待收口/在途提交后，制作并完整迁入一致备份。中枢另一个任务复用既有密钥/原连接，保留 X 幂等并绑定 Linux 新 owner，仅开 X 路由。未知投递保留不重发。旧数据不删。

迁移备份维护按业务仓已评审新方法：只读连接也可能建立WAL/SHM，不能因此删除旁车或绕过原immutable health。用户已允许原生mode=rw/query_only连接正常关闭时由SQLite自行归并，但须保全真实WAL及全部业务行；不初始化Store、不显式checkpoint。文件化脚本与两端合成验证后，由原Windows协调者在新停机窗口一次执行双库备份；全表逻辑、原health及成对回执均通过才迁入。旧部分快照禁用，busy/超时/并发/单库失败不自动重试。

回读新 owner 主机、模型、heartbeat 暂停、旧任务停止、数据库完整性/记录数/水位/状态及路由证明后，才授权唯一固定会话真实宿主自检和人工双流验收。首轮成功后两次全新复扫，全部通过才启用每小时 heartbeat。传输接受与送达分别记录；无新合格通知时不制造消息，送达保持待验证。Linux 写入后回退先停写对账，不能直接恢复旧快照。
