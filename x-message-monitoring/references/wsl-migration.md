# WSL 唯一固定会话与运行接口

## Linux 接替授权（步骤 WSL-2026-09-12.9，当前优先）

用户已批准一次 Linux 固定会话接替：保留原业务账本、每小时计划、Luna/max、飞书目标及规则，Windows 旧聊天归档保留。通过正常产品工具创建并初始化唯一 Linux 固定会话，随后绑定暂停的 heartbeat；不伪造 MCP 调用元数据或修改 Codex 数据库。旧宿主的同 ID 转移、禁止新会话和 CUA-only 限制不再适用于这次已授权接替。迁移后仍只有一个活动固定会话及一个扫描计划；维护任务不代替它扫描。

实际归属和当前准备/激活状态读取业务仓 `/root/workspaces/X-monitor/LINUX_FIXED_SESSION.md` 与受限迁移回执；不得从标题推定已上线。源仓 `/root/workspaces/custom-skills`，受管发布须已提交、与实时远端一致、按发布器安装并在新进程验证发现。安装不能代替业务验收。

## 主动维护与恢复推进

用户要求修复监控和推送时，开发任务负责诊断、源码/配置修复、受管 Skill 发布和已有任务协调；真实浏览器与采集仍由唯一固定 owner 验收。其他任务处于规划模式不撤销本任务已有用户授权；共享应用操作先同步精确对象与影响、排除并行修改，再按本任务实际权限实施。不要反复请求已授权的正常步骤，也不要新建替代 owner。

“失败后停止”针对失败页面、结果未知的动作和依赖它的生产操作，不代表整个维修任务结束。控制阻断期间继续可独立完成的代码回归、插件安装状态与直接错误核验、原飞书路由/权限/配置准备。记录准确缺口、负责者和下一项可执行动作；对账或修复带来可核实的新状态后，由同一 owner 按当前工具文档做有界复验。没有新证据不重复同一未知动作，也不以改 Skill 文字绕过产品策略。

旧 PID、未迁库、候选未切换等只代表回执时间点。重新进入维护先回读本机当前状态；已成功导入的正式库不能因旧文档再次迁入。正式运行放行仍依赖现有配对守卫、真实控制与 selfTest、路由和业务预检，不新增重复授权层。

## 固定接口

- 工作目录 `/root/workspaces/X-monitor`；代码不可变发布 `/srv/x-monitor/current`；正式账本始终 `/var/lib/x-monitor/x-monitor.sqlite3`，不存在时停止，不能建立空库代替迁移。中枢只读解析器使用同一 data-dir。
- 驱动 `scripts/desktop_monitor_driver.js` 3.0.31；标准输入客户端 `scripts/desktop_stdin_client.js` 1.2.0；Python `/srv/x-monitor/current/.venv/bin/python`，固定控制入口同发布的 `scripts/fixed_session_entry.py`。均从同一发布原样加载并核对版本与指纹。
- 用户现已选择 Linux 日常 Chrome 现有 Default 个人资料；当前配置 `/etc/x-monitor/browser.json` 的后端为 `chrome_extension`，browser=chrome、user_data_dir=/root/.config/google-chrome、profile_directory=Default。配置选择器 `scripts/linux_browser_config.js` 1.0.0 在 acquire 前只读加载并冻结；不启动浏览器、不读取个人资料内容。使用原扩展来源 `desktop_chrome_extension`，不能冒称原生 Playwright 来源。
- 旧 `playwright_linux` 专用配置及 `/var/lib/x-monitor-browser/profile` 保留停用。当前不调用其 launch，不复制 Cookie，不将 Playwright 指向日常 Chrome 目录，不因扩展故障自动恢复备用后端。原生启动器明确拒绝扩展配置和日常目录；两种后端都不新增独立扫描者。
- 固定后端在 acquire 前读取并加载；轮内不切换。保留本页15秒、单次40秒、回复八导航、20分钟租约和双流独立提交。原驱动所有严格身份、UTC、正文、引用和停止条件保持；页面适配只转换 API/timeout。只允许原驱动两个已审 DOM reader；AX 刷新文本不输出，超时未知关闭自有运行时，不重放失败页。

## 三种身份不可互换

Chrome 的 Google 资料身份 `dmdmwshr@gmail.com` 只用于选定已授权的 Default；本机已用可见账号切换按钮及个人资料链接核验的 **X 登录 handle 为 `dmdmws`**，即 `createCycle.authenticatedAccount`。被监控作者则来自本轮 acquire/health 的账号（当前 `thsottiaux`），即 `createCycle.account`。不要将 `dmdmwshr`、Google 邮箱或被监控作者填进 authenticatedAccount；Windows 快速路径中原有 `dmdmws` 并未因选择 Linux Google 资料而改名。初始化 cycle 前在纯内存核对三者用途，保持严格身份校验；真正的可见登录身份变化需要新证据，不猜值绕过。

heartbeat 为 PAUSED 只停止定时调度，不撤销主控已经明确放行的人工双流/合格新通知。依据唯一 handoff 顶部的最新授权及实际回执判断阶段，不能把历史初始化“禁止扫描”重新当作当前停止指令；已收口的失败轮另起新 lease/cycle，不重开原轮。

## 静态准备与浏览器控制

Chrome 扩展在唯一固定任务的 CUA 宿主运行；按当前工具文档选择已有 Chrome，在同一宿主保留 `xMonDriver`、`xMonStdinFactory`、`xMonStdin`、`xMonBrowser` 和本轮自有 `xMonTab`。驱动和客户端从同一Linux发布原样加载，公开Node标准库注入保持原契约；之前在独立Node宿主完成的selfTest不代替当前CUA宿主验证。每次工具仍只调用一个固定浏览器采集方法或一次sendKept，不写现场循环合并多个页面。

准备新 runtime 前，先确认当前直接工具表中的真实 CUA 入口，并按其首调用约束取得文档；`functions.ALL_TOOLS` 没有该入口不等于未暴露。普通 `node_repl` 的 `js` 或 `nodeRepl` 不是 CUA 身份证明，不能在那里先 launch 再跨宿主使用浏览器。既有 runtime 持有期间若入口确实消失，保留真实持有态、停止依赖操作并按原上下文收口；不通过 reset、再次 launch 或重复 selfTest 寻找入口。已有效的同宿主静态实例继续复用。

首次或宿主重置后，在实际 owning Node 宿主执行客户端无参数 selfTest，61项 exact_match=true，才可 acquire；独立命令行 fixture 不是这项证据。模块来自同一 `/srv/x-monitor/current`，公开 Node 的 spawn/Buffer/TextDecoder/计时器注入遵循客户端原工厂签名。

先验证本次自有空白标签创建、操作、关闭，以及实际Profile Path对应既有Default；随后独立验证X登录和新标签复用。Chrome的Google身份不等于X登录证明；需要认证时人工完成，不读取密码/Cookie。只关闭创建回执证明归属的标签，不关闭整个日常浏览器或用户旧标签。旧专用人工登录窗口已结束，不再要求用户登录旧专用profile。

扩展控制恢复可单独维护，但实例、扩展连接和页面控制分别验收。`start_managed_browser.py --browser chrome` 只证明 Linux 实例元数据，不能当作扩展成功；不得回退 Windows 或在失败轮切浏览器。

若工具日志证明产品会话浏览器路由缺失，可通过正常产品入口打开精确固定任务并回读路由登记；不伪造路由元数据或修改Codex内部数据库。只有环境实际变更后才做一次新的恢复验收。超时没有标签ID时不能猜归属或宣称已关闭，保持未确认状态并停止扫描。

用户报告侧栏 Codex 已能打开页面，是新连接线索；核对其实际浏览器/profile/任务与本次官方工具回执，不把侧栏成功直接等同于固定 owner 成功。用户明确提供的标签 mention 可按当前工具首调用规则定位授权标签；不猜 ID、不遍历用户历史标签找故障残留，不关闭用户提供的标签。正式采集继续创建并清理自己的标签。

插件 installed/enabled、设置中重新安装结果、扩展连接与 request-header policy 初始化分别记录。请求头策略失败发生在导航前时，不归因为 X 退出登录，不重装已完好的插件试错，不关闭策略检查。按 `codex-local-state-diagnostics` 的浏览器恢复参考定位直接错误；仅产品支持且有具体证据的修复进入执行。

任务预览与旧回执的插件版本/PID 不构成当前运行门禁；以本机当前实例、产品生成配置和已验收发布为准。启动器选择变量在子进程缺失，不能替代实际启动链与运行环境检查。`getState` 成功但只有 IAB 时记为 Chrome 扩展未枚举，不记为错误版本或未登录；继续核对当前 native host 与产品登记，由唯一 owner 执行已授权的扩展界面修复。无新连接状态不反复枚举；修复后仍须真实控制、当前宿主61项及正式预检，不因纠正诊断直接放行采集。

Linux socket 不可见或侧栏报 `Native transport disconnected` 时，按诊断 skill 的实例章节核对两进程实际挂载命名空间及恢复服务 `PrivateTmp`；不能只看协调 shell 的 `/tmp`。短命服务遗留的已删除私有临时目录属于启动环境故障，扩展开关和重装不能修复该目录。由共享桌面负责人修复启动入口、唯一 owner 执行已授权的正常浏览器恢复；保留既有 Default、用户标签与正式账本，恢复后仍按上述完整验收。

## 配对导入全生命周期门禁

生产 driver 必须从同发布 `scripts/linux_guarded_runtime.js` 的 `launch({spawn,createDriver,createClient})` 返回对象绑定为 xMonDriver/xMonStdin。原样源码可提前读取；工厂回调同步实例化，不提前保留裸实例、不启动浏览器或执行业务。先取得官方 paired-import 共享锁才创建驱动/客户端，锁连续覆盖准备、CUA动作、空闲间隔、两阶段finish、标签/在途及kept清理；不能只包一次启动检查或每个Python动作。固定CLI也在打开正式库前持同一共享锁到close，五项sendKept保持。

持锁子进程不扫描、不接收正文/lease、不打开SQLite。它在 `/var/lib/cc-connect-operations/migration/x-driver-active.json` 写入无业务内容的运行标记；EOF/异常/坏帧留下标记，中枢导入/回滚必须拒绝，不能自动过期、删除或重开。只有真实两阶段finish、自有标签关闭、在途零、clearKept均已确认，才调用 runtime.close({twoPhaseFinished:true,ownedTabsClosed:true,inflightZero:true,keptCleared:true})；不得猜这些布尔值。运行时包装器另拒绝进行中关闭，正常握手才删除本轮原样标记并等待释放；关闭后driver/client全部失效。异常锁丢失立即停止，新运行前由主控核验静止，不用TTL当收口。

具体候选选择与已验收范围见业务仓 `LINUX_STARTUP_GUARD.md` 和总台账。未完成迁库时，仅可按主控明确授权独立做无生产driver的空白连接/纯合成selfTest；不能借诊断绕过生产门禁。双方current须由主控切换到含守卫和未收口拒绝的候选，代码测试通过不等于正式owner已持锁。

两个源文件都是 IIFE 表达式，执行 driver 源码直接返回 API **对象**；执行 stdin 源码返回带 `createFixedStdinClient` 的对象，不能把结果再次当函数调用。锁前仅读取原样文本或编译 `vm.Script`；`createDriver` 回调内才执行 driver Script 并直接返回对象，`createClient` 回调内执行 client Script 后调用其 `createFixedStdinClient({spawn})`。只绑定 runtime 返回的包装实例，版本/指纹在持锁后核验；精确公开 Node 注入与模板见业务仓 `LINUX_STARTUP_GUARD.md` 的“原样源码的确切加载形态”，不得从变量名推断接口或复用锁前的裸 driver。

CUA 的 Node 全局不保证包含计时器。按该模板显式 `import('node:timers')`，将模块的 `setTimeout`、`clearTimeout` 分别注入 guard 和 stdin 两个VM上下文；Buffer、TextDecoder、URL、spawn也从对应公开Node模块取得并在launch前验证。给客户端注入不代表守卫已注入。守卫1.0.1增加spawn前缺计时器拒绝，实际current/version/hash须经正常发布回读；旧1.0.0仍可能在缺计时器时留下初始化失败标记。任一launch已经失败且产生标记时，不因本地改正变量再次launch，由共享marker负责人独立核验、原owner正式收口与idle后恢复，随后才另起新runtime。

运行回执一旦发送路径/哈希即保留原文件；发现漏报只读 health 等事实时，写独立更正回执说明字段变化、原哈希与新证据，不覆盖已发送文件。当前状态索引可更新，但不能用索引的新时间刷新原控制/selfTest成功或隐藏失败。

正式账本的常规诊断使用原 fixed health；需要扩展只读核验时沿用官方 startup_guard，并复用原 `SQLiteStore(read_only=True)` 已通过静止签名检查的连接，不自行用裸 `mode=ro` 打开 WAL 库。只读连接也可能在退出后留下空 WAL/SHM，不能据此放宽原 health 或把它误报为浏览器故障。已经出现且独立确认无连接、owner idle、heartbeat 暂停及无运行 marker 时，可按明确维护授权使用业务仓已审计 `close_empty_wal_reader.py` 的空 WAL 正常关闭方法；它拒绝非空 WAL/活动锁/待收口，原生 rw/query_only 正常 close 后比对全表摘要并要求原 health 通过，无可写 Store 初始化、显式 checkpoint、手删旁车或业务行写入。此入口不是定时 health 的自动回退或重试，候选维护不切换生产 current。

## 每轮业务与清理

acquire 前完整读取当前快速路径和上下文契约的业务部分；Windows路径替换为上述Linux发布入口，当前扩展仍在CUA宿主以真实desktop_chrome_extension来源运行。五项sendKept、内存载荷、语义筛选、fingerprint、草稿/冻结各一次、预检顺序与两阶段finish全部保持。

lease仍只在functions与当前CUA宿主内存中传递，acquire同次保存原回执，后续控制从该对象取值；不输出凭证。浏览前证明两侧本轮值相等。公开事实不进入文件、临时正文、浏览器存储或普通日志。共同故障停两流，局部失败保留另一流处理；真实unknown不重试。当前扩展失败登记browser=chrome，采集遥测使用实际来源和驱动版本。

两阶段finish、自有标签关闭、无在途子进程后，clearKept再清理lease与其他动态事实；静态工厂可复用。不关闭日常Chrome整体实例或其他profile/标签。只有真实完整成功且机器允许才输出DONT_NOTIFY，手动不计四轮摘要。

## 切换验收

旧 Windows X heartbeat 精确暂停、无额外旧扫描入口、双锁空闲、无待收口/在途提交后，制作并完整迁入一致备份。中枢另一个任务复用既有密钥/原连接，保留 X 幂等并绑定 Linux 新 owner，仅开 X 路由。未知投递保留不重发。旧数据不删。

迁移备份维护按业务仓已评审新方法：只读连接也可能建立WAL/SHM，不能因此删除旁车或绕过原immutable health。用户已允许原生mode=rw/query_only连接正常关闭时由SQLite自行归并，但须保全真实WAL及全部业务行；不初始化Store、不显式checkpoint。文件化脚本与两端合成验证后，由原Windows协调者在新停机窗口一次执行双库备份；全表逻辑、原health及成对回执均通过才迁入。旧部分快照禁用，busy/超时/并发/单库失败不自动重试。

回读新 owner 主机、模型、heartbeat 暂停、旧任务停止、数据库完整性/记录数/水位/状态及路由证明后，才授权唯一固定会话真实宿主自检和人工双流验收。首轮成功后两次全新复扫，全部通过才启用每小时 heartbeat，随后验收四个真实定时周期；四周期不能倒置为首次启用的前提，手动轮次不充数。传输接受与送达分别记录；无新合格通知时不制造消息，送达保持待验证。Linux 写入后回退先停写对账，不能直接恢复旧快照。

飞书投递修复复用中枢既有 `direct_feishu` 连接、私有目标和幂等关系。中枢负责凭据/权限/发布状态及精确 X 路由激活，X 负责新事实和投递回执；浏览器故障不阻断中枢只读准备，但不因此提前派发 X。只有 API 与受限配置不能提供必需信息时，才让唯一 owner 按已授权的精确飞书管理页面核验必要字段或完成配置操作；不读取聊天历史、Cookie 或登录令牌来构造旁路。网页已登录、平台接受发送和确认送达各自验收，未知投递仍只对账、不重发。


投递解析故障先区分中枢注册、解析子进程、平台发送和可见回执。`pre_send_source_resolver_failed` 本身不证明权限错误或已经发送；用原key/route/代次仅GET对账，并核对实际服务namespace的文件系统与发布入口，不能因root账号就假设可写。修复后的X `resolve-delivery` 使用专用只读快照，保持原成功schema/正文哈希和5秒中枢子进程上限；空闲库原静止签名、有WAL原生只读事务，缺库/缺迁移标记继续拒绝。resolver不得初始化/恢复业务行或要求扩大中枢X目录写权限。Linux读者与writer关闭在现有DB inode上协调，保持原health/关闭期限；遗留旁车仍是失败门禁，不能自动维护、删除或重发dead-letter。实际是否已部署须回读current，不能把候选或Skill更新当上线证据。
