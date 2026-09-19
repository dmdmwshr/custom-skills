# WSL 唯一固定会话与运行接口

## 当前目录与恢复边界（步骤 WSL-2026-09-20.5）

本机公共布局以 `/etc/codex-dev/paths.json` 为准，X正式源码根当前为 `/srv/workspaces/X-monitor`。原Desktop owner归入既有X-monitor业务项目，实际cwd须与受审Desktop目录精确一致；中枢notifications控制目录是独立出站职责，不因项目归属将owner迁入该目录。修复后用两次普通续接的默认pwd及产品环境证明持久生效，显式workdir或单次启动覆盖不算验收。保留原owner/heartbeat/模型，旧目录链接不重建，私有登记只经中枢受控事务更新。

新鲜状态只读业务仓的 `PROJECT_HANDOFF.md` 与 `LINUX_FIXED_SESSION.md`；已接受的运行版本/指纹以实际current及其manifest为准。本页历史迁移授权不允许再次创建接替者、导入账本或恢复旧后端。源码与离线维护归开发任务，真实浏览器与周期归原owner，cc仅direct_feishu出站且入站silent_drop。

用户已批准官方Playwright CLI接入现有隐藏Chrome，当前接口以业务仓 `HIDDEN_CHROME_CLI.md` 为准，覆盖旧native选择。原owner已实测CLI隐藏绑定、X登录、自有页关闭、release及临时工件清理；正式代码/配置已受控切换，但这些不代替当前CUA61、三人工轮及四真实周期。实时阶段只认唯一handoff，不以技能安装启用后端或heartbeat。用户授权直接接管时，先核对原维护任务已静止，开发可串行完成共享修复/发布，不反复微派发；原owner身份和业务内存仍不迁移或伪造。

## 历史：一次 Linux 接替授权（2026-09-12）

用户已批准一次 Linux 固定会话接替：保留原业务账本、每小时计划、Luna/max、飞书目标及规则，Windows 旧聊天归档保留。通过正常产品工具创建并初始化唯一 Linux 固定会话，随后绑定暂停的 heartbeat；不伪造 MCP 调用元数据或修改 Codex 数据库。旧宿主的同 ID 转移、禁止新会话和 CUA-only 限制不再适用于这次已授权接替。迁移后仍只有一个活动固定会话及一个扫描计划；维护任务不代替它扫描。

历史归属读取当时受限迁移回执；当前业务仓和custom-skills源仓按公共路径配置解析，不沿用旧HOME猜测。受管发布须已提交、与实时远端一致、按发布器安装并在新进程验证发现；安装不能代替业务验收。

## 主动维护与恢复推进

用户要求修复监控和推送时，开发任务负责诊断、源码/配置修复、受管 Skill 发布和已有任务协调；真实浏览器与采集仍由唯一固定 owner 验收。其他任务处于规划模式不撤销本任务已有用户授权；共享应用操作先同步精确对象与影响、排除并行修改，再按本任务实际权限实施。不要反复请求已授权的正常步骤，也不要新建替代 owner。

“失败后停止”针对失败页面、结果未知的动作和依赖它的生产操作，不代表整个维修任务结束。控制阻断期间继续可独立完成的代码回归、插件安装状态与直接错误核验、原飞书路由/权限/配置准备。记录准确缺口、负责者和下一项可执行动作；对账或修复带来可核实的新状态后，由同一 owner 按当前工具文档做有界复验。没有新证据不重复同一未知动作，也不以改 Skill 文字绕过产品策略。

旧 PID、未迁库、候选未切换等只代表回执时间点。重新进入维护先回读本机当前状态；已成功导入的正式库不能因旧文档再次迁入。正式运行放行仍依赖现有配对守卫、真实控制与 selfTest、路由和业务预检，不新增重复授权层。

## 固定接口

- 开发任务按公共配置定位X源码；原owner的持久cwd与中枢受审Desktop登记分别回读，不依赖旧目录链接。代码不可变发布 `/srv/x-monitor/current`；正式账本始终 `/var/lib/x-monitor/x-monitor.sqlite3`，不存在时停止，不能建立空库代替迁移。中枢只读解析器使用同一 data-dir。
- 驱动 `scripts/desktop_monitor_driver.js` 3.0.33、CLI adapter1.0.2；标准输入客户端 `scripts/desktop_stdin_client.js` 1.2.0；Python `/srv/x-monitor/current/.venv/bin/python`，固定控制入口同发布的 `scripts/fixed_session_entry.py`。均从同一发布原样加载并核对版本与指纹。driver VM注入公开node:perf_hooks.performance，CLI要求monotonic_v1；这是预算时钟，UTC事实仍用Date。同CUA加载已验，完整业务仍待验收。
- 本次选择 `/etc/x-monitor/browser.json` 的 `hidden_chrome_cli` 后端，六字段及精确路径按业务仓说明校验：隐藏Chrome `/var/lib/codex-browser-automation/chrome/Default`、UID1000、目录归属、主进程/PID/start_ticks、沙箱、显式代理和官方Playwright扩展/session。允许其他独立Chrome共存；不使用GPT的extensionInstanceId冒充CLI身份。选择器在acquire前冻结，来源固定为已有 `desktop_chrome_playwright_linux`；CLI版本/隐藏绑定仅入运行回执，不改历史账本。
- 旧 `playwright_linux` 专用配置及 `/var/lib/x-monitor-browser/profile` 保留停用。当前不调用其 launch，不复制 Cookie，不将 Playwright 指向日常 Chrome 目录，不因扩展故障自动恢复备用后端。原生启动器明确拒绝扩展配置和日常目录；两种后端都不新增独立扫描者。
- 固定后端在acquire前加载，轮内不切换。官方CLI仅为受管浏览器子进程，不新增扫描者或扩大旧MCP白名单；模型不直接构造CLI argv/eval/CDP。保留15秒、单次40秒、回复八导航、20分钟租约与双流独立提交；身份、UTC、正文、引用、水位不放宽。两个已审DOM reader以原函数身份映射固定ID，禁止自选脚本/路径/hash，AX刷新文本不输出。真正未知停止后续浏览器操作，不为清理再发关闭、不重放失败页。

## 三种身份不可互换

Chrome 的 Google 资料身份 `dmdmwshr@gmail.com` 只用于选定已授权的 Default；本机已用可见账号切换按钮及个人资料链接核验的 **X 登录 handle 为 `dmdmws`**，即 `createCycle.authenticatedAccount`。被监控作者则来自本轮 acquire/health 的账号（当前 `thsottiaux`），即 `createCycle.account`。不要将 `dmdmwshr`、Google 邮箱或被监控作者填进 authenticatedAccount；Windows 快速路径中原有 `dmdmws` 并未因选择 Linux Google 资料而改名。初始化 cycle 前在纯内存核对三者用途，保持严格身份校验；真正的可见登录身份变化需要新证据，不猜值绕过。

heartbeat 为 PAUSED 只停止定时调度，不撤销主控已经明确放行的人工双流/合格新通知。依据唯一 handoff 顶部的最新授权及实际回执判断阶段，不能把历史初始化“禁止扫描”重新当作当前停止指令；已收口的失败轮另起新 lease/cycle，不重开原轮。

## 静态准备与浏览器控制

唯一固定任务的实际CUA宿主保留事实、业务lease、五sendKept及回执。通过公开Node createRequire加载current的hidden_chrome_cli_adapter.js，连接受管客户端并绑定真实任务/session/资料身份；不得把内部shared client当adapter。CUA不暴露CODEX_THREAD_ID时不改env或绕过被拒绝的模块导入；仅使用已受审root进程证明分支，按当前业务接口读取本kernel证明的task_id字段，检查完整UUID，不手抄。证明独立绑定原owner工具回执、boot/进程链与仍打开的非秘密见证FD，错身份/重建/过期/损坏拒绝，不自动续期；参数不是身份证明。真实同CUA连接和正常清理已验，长期续期仍须单独验收。

公开标准库注入保持原契约；版本在工厂核对，launch返回立即保存到预先声明的顶层runtime变量，再生成摘要。隐藏adapter只暴露固定方法与自有不透明handle，不提供裸tab、任意evaluate、CDP或存储；没有identity()方法，公开元数据用state()/receipt()，缺字段不猜值。独立Node控制验收/selfTest不能代替当前CUA61；每次工具只调用一个固定浏览器方法或一次sendKept，不现场循环合并多页。固定浏览器方法按快速路径直接nodeRepl.write(await ...)输出受审回执，不再手工摘取字段而遗漏同次probe/readiness/差异元数据；这是V7已证实的诊断信息缺失，不为补报重读失败页。

每个固定方法由ManagedHiddenCLI.v1的withOperation取得短租约，固定primitive串行执行后共享客户端按真实回执结算，调用方不能自报known/unknown。只从当前CUA公开Node加载已接受固定模块，不加载内部浏览器实现或跨宿主传凭证。总40秒前35秒含准备/工作、末5秒留结算，单页15秒、整轮20分钟；超时不证明进程已取消，真实在途保持不允许清理/重开。短租约不跨研究、长思考或下一消息。

业务结果与控制结果分开：正常返回的空DOM、未找到唯一目标或业务校验失败，不等于浏览器动作结果未知；可以业务失败且正常known结算，但不能据此放宽目标核验。只有真实超时、连接/动作结果或结算未知才走unknown。人工接管/其他客户端明确拒绝时不调用CUA、不循环申请；在途未返回时不提前known结算。未知、过期、关闭失败保留真实现场，不重end、不begin、不自动resume/restart或清状态；只经受支持且有新证据的独立对账恢复。关闭后的lease客户端、adapter及runtime均不复用。

准备新 runtime 前，先确认当前直接工具表中的真实 CUA 入口，并按其首调用约束取得文档；`functions.ALL_TOOLS` 没有该入口不等于未暴露。普通 `node_repl` 的 `js` 或 `nodeRepl` 不是 CUA 身份证明，不能在那里先 launch 再跨宿主使用浏览器。既有 runtime 持有期间若入口确实消失，保留真实持有态、停止依赖操作并按原上下文收口；不通过 reset、再次 launch 或重复 selfTest 寻找入口。已有效的同宿主静态实例继续复用。

业务标签只用 `xMonHidden.newTab()` / `closeTab(handle)`，每次经短租约。连接后必须赋值 `xMonDriver = xMonHidden.driver`，acquire前纯内存检查它等于adapter.driver且不等于runtime.driver；后者只用于静态工厂/时钟核验。V5遗漏绑定后，底层driver对只有id的handle调用不存在的goto，零CLI页面操作即返回navigation_execution_failed，不能误诊为网站/时钟故障。实际发布改变时先用公开fs.realpath解析current，再require实际发布下的adapter，防止长期CommonJS别名缓存仍装旧版；不删缓存/reset。具体模板由业务仓HIDDEN_CHROME_CLI.md维护，不复制整轮runner。专项飞书只读遵守用户本次指定入口，可按明确授权使用现有可见浏览器，但不因此改X绑定。原生入口另按其文档精确browserId/sessionName；visible仅IAB可用。参数在创建前明确拒绝与真实创建未知分开，不能猜标签ID。

CLI标签创建为空白页，cycle由adapter.driver.createCycle生成后，ownTab和lastUrl保持原始null，导航计数不手改，直接逐次调用固定page(handle,cycle,"main"/"search")。adapter将外部handle映射为内部标签，原driver自行绑定并执行首次导航；快速路径的原生预导航及手动设置ownTab/lastUrl步骤不适用。真实调用已证明外部handle赋给ownTab会在页面primitive之前返回已知round_tab_mismatch，不是浏览器unknown。按原规则关闭失败轮，下一轮使用全新cycle；不通过改失败对象、移除归属检查或再次调用失败页修复。

首次或宿主重置后，在实际 owning Node 宿主执行客户端无参数 selfTest，61项 exact_match=true，才可 acquire；独立命令行 fixture 不是这项证据。模块来自同一 `/srv/x-monitor/current`，公开 Node 的 spawn/Buffer/TextDecoder/计时器注入遵循客户端原工厂签名。

先验证本次自有空白标签创建、操作、关闭，以及实际Profile Path对应既有Default；随后独立验证X登录和新标签复用。Chrome的Google身份不等于X登录证明；需要认证时人工完成，不读取密码/Cookie。只关闭创建回执证明归属的标签，不关闭整个日常浏览器或用户旧标签。旧专用人工登录窗口已结束，不再要求用户登录旧专用profile。

扩展控制恢复可单独维护，但实例、扩展连接和页面控制分别验收。隐藏后端使用固定元数据检查器，不调用旧日常 `start_managed_browser.py` 或旧Playwright启动器；元数据通过不能当扩展可控。不得回退Windows或在失败轮切浏览器。

若工具日志证明产品会话浏览器路由缺失，可通过正常产品入口打开精确固定任务并回读路由登记；不伪造路由元数据或修改Codex内部数据库。只有环境实际变更后才做一次新的恢复验收。超时没有标签ID时不能猜归属或宣称已关闭，保持未确认状态并停止扫描。

用户报告侧栏 Codex 已能打开页面，是新连接线索；核对其实际浏览器/profile/任务与本次官方工具回执，不把侧栏成功直接等同于固定 owner 成功。用户明确提供的标签 mention 可按当前工具首调用规则定位授权标签；不猜 ID、不遍历用户历史标签找故障残留，不关闭用户提供的标签。正式采集继续创建并清理自己的标签。

插件 installed/enabled、设置中重新安装结果、扩展连接与 request-header policy 初始化分别记录。请求头策略失败发生在导航前时，不归因为 X 退出登录，不重装已完好的插件试错，不关闭策略检查。按 `codex-local-state-diagnostics` 的浏览器恢复参考定位直接错误；仅产品支持且有具体证据的修复进入执行。

任务预览与旧回执的插件版本/PID 不构成当前运行门禁；以本机当前实例、产品生成配置和已验收发布为准。启动器选择变量在子进程缺失，不能替代实际启动链与运行环境检查。`getState` 成功但只有 IAB 时记为 Chrome 扩展未枚举，不记为错误版本或未登录；继续核对当前 native host 与产品登记，由唯一 owner 执行已授权的扩展界面修复。无新连接状态不反复枚举；修复后仍须真实控制、当前宿主61项及正式预检，不因纠正诊断直接放行采集。

Linux socket 不可见或侧栏报 `Native transport disconnected` 时，按诊断 skill 的实例章节核对两进程实际挂载命名空间及恢复服务 `PrivateTmp`；不能只看协调 shell 的 `/tmp`。短命服务遗留的已删除私有临时目录属于启动环境故障，扩展开关和重装不能修复该目录。由共享桌面负责人修复启动入口、唯一 owner 执行已授权的正常浏览器恢复；保留既有 Default、用户标签与正式账本，恢复后仍按上述完整验收。

## 配对导入全生命周期门禁

生产driver/client必须由同发布scripts/linux_guarded_runtime.js创建；隐藏adapter再包装runtime，不能传伪造runtime或锁前裸实例。含同宿主控制的发布另传createControl并只用runtime.control；精确模板见业务仓LINUX_STARTUP_GUARD.md与HIDDEN_CHROME_CLI.md。候选须经原受管升级通道接受。整轮配对共享锁连续覆盖准备、浏览器动作、空闲间隔、两阶段finish、自有页/在途/kept清理，不在短租约之间释放。固定业务CLI打开正式库前至close亦持共享锁，五sendKept保持。

持锁子进程不扫描、不接收正文/lease、不打开SQLite。它在 `/var/lib/cc-connect-operations/migration/x-driver-active.json` 写入无业务内容的运行标记；EOF/异常/坏帧留下标记，中枢导入/回滚必须拒绝，不能自动过期、删除或重开。只有真实两阶段finish、自有标签关闭、在途零、clearKept均已确认，才调用 runtime.close({twoPhaseFinished:true,ownedTabsClosed:true,inflightZero:true,keptCleared:true})；不得猜这些布尔值。运行时包装器另拒绝进行中关闭，正常握手才删除本轮原样标记并等待释放；关闭后driver/client全部失效。异常锁丢失立即停止，新运行前由主控核验静止，不用TTL当收口。

具体发布选择与已验收范围见业务仓 `LINUX_STARTUP_GUARD.md` 和唯一交接。preparationOnly关闭只适用于从未调用cycle/页面等业务方法的静态准备，不能用于已进行的页面诊断。已接受guard1.0.4/adapter1.0.3另有显式launch mode=diagnostic，原owner已实际验证同CUA61、一次目标诊断已知失败、自有页关闭、health水位不变、release/工件清理、control.clear及diagnosticOnly关闭/marker撤销。诊断模式禁止acquire/sendKept/冻结/提交，不能为了关闭而取得业务lease、调用clearKept或伪造finish；首个失败停止，未知或在途仍不能正常关闭。参数及限额仅按业务仓当前模板，不在Skill复制另一套runner；原owner身份/同CUA/短租约和整轮共享守卫保持。页面地址匹配及目标时间链接计数为零，不能据此断言目标删除、登录失效或控制未知，也不能据诊断计入三人工/四定时验收。候选版本、新增诊断与实际业务分别留证。

两个源文件都是 IIFE 表达式，执行 driver 源码直接返回 API **对象**；执行 stdin 源码返回带 `createFixedStdinClient` 的对象，不能把结果再次当函数调用。锁前仅读取原样文本或编译 `vm.Script`；`createDriver` 回调内才执行 driver Script 并直接返回对象，`createClient` 回调内执行 client Script 后调用其 `createFixedStdinClient({spawn})`。只绑定 runtime 返回的包装实例，版本/指纹在持锁后核验；精确公开 Node 注入与模板见业务仓 `LINUX_STARTUP_GUARD.md` 的“原样源码的确切加载形态”，不得从变量名推断接口或复用锁前的裸 driver。

CUA 的 Node 全局不保证包含计时器。按该模板显式 `import('node:timers')`，将模块的 `setTimeout`、`clearTimeout` 分别注入 guard 和 stdin 两个VM上下文；Buffer、TextDecoder、URL、spawn也从对应公开Node模块取得并在launch前验证。给客户端注入不代表守卫已注入。守卫1.0.1增加spawn前缺计时器拒绝，实际current/version/hash须经正常发布回读；旧1.0.0仍可能在缺计时器时留下初始化失败标记。任一launch已经失败且产生标记时，不因本地改正变量再次launch，由共享marker负责人独立核验、原owner正式收口与idle后恢复，随后才另起新runtime。

执行前纯守卫拒绝、真实driver执行异常、浏览器未知与结果返回后的摘要错误分别处理。只有独立核对准确工具回执和调用路径、证明拒绝在adapter.run/短租约/计数之前，并由原owner新鲜核对runtime held/diagnostic_safe/preparation_safe均true、active/inflight为0、adapter无unknown和control无业务尝试/uncertain，才可沿既有diagnosticOnly分支正常关闭现存自有页及实例；不只凭错误码放行，不重试被拒绝的诊断、不删marker或重启。真实执行异常/未知仍停止。真实返回已存同CUA后才出现ReferenceError时，仅纯内存回读原结果及当前state，不把它重新归类为执行未知或重放操作；summary变量先声明，优先块内const，避免拼写错误遮蔽真实close回执。新增固定方法选项须有真实driver+guard+adapter的隔离跨层准入/拒绝/正常关闭回归，分层测试各自通过不能替代；仍不代替原owner实测。

运行回执一旦发送路径/哈希即保留原文件；发现漏报只读 health 等事实时，写独立更正或收口回执说明字段变化、原哈希与新证据，不覆盖已发送文件。当前状态索引可更新，但不能用索引的新时间刷新原控制/selfTest成功或隐藏失败。

V8已验证的预租约拒绝不能套用正常关闭：即使control在spawn前拒绝，旧guard也可能已经记录业务尝试。先保留真实摘要、检查runtime/control/adapter实际状态，不仅凭process_started=false推断preparation_safe。原分支无法正常收口时保持标记，由已获授权的开发维护者按中枢独立受审入口处理；不能由owner杀守卫或伪造finish。本次无acquire/lease/业务执行且adapter已closed的窄事故已实测“两阶段维护退役→原owner同CUA撤销证明→独立保字节/inode归档”，不推广到业务已执行或unknown。证据须绑定本启动实例/进程/发布/原标记，信号intent后不重试；具体入口与一次性pin仅放项目交接。撤销后的control.state可能被guard明确拒绝，旧已clear状态只能标为pre_retirement_audit；空页证明读实际ownedTabIds数组，不用缺字段默认零。错误抄录的hash保留原件、独立重算并明确关联，不静默改原审计。

正式账本的常规诊断使用原 fixed health；需要扩展只读核验时沿用官方 startup_guard，并复用原 `SQLiteStore(read_only=True)` 已通过静止签名检查的连接，不自行用裸 `mode=ro` 打开 WAL 库。只读连接也可能在退出后留下空 WAL/SHM，不能据此放宽原 health 或把它误报为浏览器故障。已经出现且独立确认无连接、owner idle、heartbeat 暂停及无运行 marker 时，可按明确维护授权使用业务仓已审计 `close_empty_wal_reader.py` 的空 WAL 正常关闭方法；它拒绝非空 WAL/活动锁/待收口，原生 rw/query_only 正常 close 后比对全表摘要并要求原 health 通过，无可写 Store 初始化、显式 checkpoint、手删旁车或业务行写入。此入口不是定时 health 的自动回退或重试，候选维护不切换生产 current。

## 每轮业务与清理

acquire前完整读取当前快速路径和上下文契约的业务部分；Windows路径替换为上述Linux发布入口，CLI来源为desktop_chrome_playwright_linux。五sendKept、内存载荷、语义筛选、fingerprint、草稿/冻结各一次、预检顺序和两阶段finish保持。permalinkBurst第三参数只能取本轮xMonStdin.kept(xMonLease,"observation-fingerprint").result.response.fingerprint，不是流名或空串；永久链接完成前不能用另一stdin业务动作覆盖它。V7两次错误参数在导航前被拒绝且未设置流失败，不等于浏览器unknown；有cycle.failures.reply的真实失败仍禁止续批，不能以参数纠正恢复失败流。

当前直接CUA工具无法嵌入functions，不能假定存在跨宿主opaque传递，也不把lease写临时文件。已批准的新同宿主分支使用 `desktop_control_client.js`，只封装原固定Python的health/acquire/预检/失败/对账/finish等有限小型控制；五项业务sendKept与Python入口不变。候选未正式接受时不得先用旧functions acquire等待搬运凭证；旧轮只按其已有原lease收口，不能迁入新实例。

新分支在当前CUA闭包执行唯一acquire并在返回前保存原回执，默认只输出元数据；用 `runtime.control.leaseReceipt()` 在同一VM初始化xMonLease，后续小型控制由闭包注入原lease，不接受调用者覆盖。这取代Linux旧快速路径“functions与CUA两侧保存/比对”的步骤；相等证明在同一VM核对xMonLease与该原回执，functions不再持有新轮凭证。首次health/acquire之前完成实际浏览器控制、登录和固定控制模块准备；每次仍只执行一个固定请求，预检明确成功后才生成draft/raw。`runKept('login-state')`是本轮lease内登录状态登记，不是只读登录探针，acquire前不能调用，也不为它提前acquire。每次先保存实际摘要，检查ok/code/process_started/outcome_unknown/retained_result_available，仅存在本次保留结果时再kept(action)；spawn前拒绝可能根本没有kept，后续control_result_missing不把已知拒绝变成执行unknown。guard1.0.6的事前准入已接受，实际新运行防护仍需原owner验收，不改旧对象。未知不重试，finish仅一次并由原Python完成内部两阶段。

正式事实对象与lease保持同轮内存，不从文件恢复。用户另已允许官方CLI临时快照/日志，仅本任务私有目录，非正式事实来源，不入Git/Skill/业务备份。共同故障停两流，局部失败保留另一流；真实unknown不重试，失败登记browser=chrome和实际来源/版本。

临时工件必须在/srv/work/tasks下本task/session专属0700目录，文件0600。仅umask077不足以抵消公共父目录default ACL，受管入口在新目录剥离继承ACL后再创建子目录，不改共享父目录。正常收口且无在途后，adapter1.0.1 close先确认release并撤销实例，再以同客户端audit的精确清单一次cleanup，保留不含正文回执。release成功后清理失败仍是浏览器known/closed，artifactCleanup=unverified，不重试close、不计完整验收。崩溃残留需新鲜归属/进程审计；24小时只标记，不按过期盲删或恢复旧事实。

两阶段finish及原health终态/双锁空闲/finish_pending=0核验后，关闭自有标签、确认无在途，先clearKept、await adapter.close及工件清理，再按真实证据clear控制闭包，最后清动态事实并正常close guard。health方法属于control；finishPendingZero直接核对本轮health.last_heartbeat.finish_pending，不从锁空闲推导。曾acquire的正式周期control.clear还要求terminalHealth:true及既有五项静止证明，须来自本轮实际finish/health/清理；不能照搬省略该项的preparationOnly范例或猜值。五sendKept/clearKept使用leaseReceipt().lease字符串，不传整个回执。release已知而工件失败可按其他真实收口证据释放guard，但须报告未清理、不能计完整验收。无有效lease的未知acquire只能经独立终态对账收口，不能以TTL代替。静态工厂可复用，closed实例不可复用；日常浏览器及其他标签不动。只有完整成功且机器允许才DONT_NOTIFY，人工不计四轮摘要。

## 当前隐藏后端切换验收

开发构建不可变X候选；原中枢在原owner已知收口、heartbeat PAUSED、无marker/在途的新鲜静止窗口备份并精确切换。升级前维护备份不能伪填业务验收完成；沿已审 `pre_upgrade_maintenance` 目的绑定完整候选与plan，保留原路由、代次、epoch和配对关系。browser.json六字段独立精确事务保留before/after/hash/权限。失败仅回退本次代码与配置，不覆盖新增业务数据。

新发布由原owner完成实际CUA61、正式health和原路由预检，再做一轮双流及两次全新复扫；局部失败保留成功流，真实收口后另起新轮，不重开失败流。只读核验原唯一已发送维护通知的目标、时间及内容，不新增验收键或再次发送。三轮与飞书网页核验均通过才由原owner通过产品工具恢复同一个整点heartbeat，再验四个连续真实定时周期；失败暂停修复，人工轮不计数。旧日常未知标签保持不动，仅清理明确自有且已知可关闭的临时页。

## 历史：首次Linux迁移验收

旧 Windows X heartbeat 精确暂停、无额外旧扫描入口、双锁空闲、无待收口/在途提交后，制作并完整迁入一致备份。中枢另一个任务复用既有密钥/原连接，保留 X 幂等并绑定 Linux 新 owner，仅开 X 路由。未知投递保留不重发。旧数据不删。

迁移备份维护按业务仓已评审新方法：只读连接也可能建立WAL/SHM，不能因此删除旁车或绕过原immutable health。用户已允许原生mode=rw/query_only连接正常关闭时由SQLite自行归并，但须保全真实WAL及全部业务行；不初始化Store、不显式checkpoint。文件化脚本与两端合成验证后，由原Windows协调者在新停机窗口一次执行双库备份；全表逻辑、原health及成对回执均通过才迁入。旧部分快照禁用，busy/超时/并发/单库失败不自动重试。

回读新 owner 主机、模型、heartbeat 暂停、旧任务停止、数据库完整性/记录数/水位/状态及路由证明后，才授权唯一固定会话真实宿主自检和人工双流验收。首轮成功后两次全新复扫，全部通过才启用每小时 heartbeat，随后验收四个真实定时周期；四周期不能倒置为首次启用的前提，手动轮次不充数。传输接受与送达分别记录；无新合格通知时不制造消息，送达保持待验证。Linux 写入后回退先停写对账，不能直接恢复旧快照。

飞书投递修复复用中枢既有 `direct_feishu` 连接、私有目标和幂等关系。中枢负责凭据/权限/发布状态及精确 X 路由激活，X 负责新事实和投递回执；浏览器故障不阻断中枢只读准备，但不因此提前派发 X。平台通用操作按已安装的feishu-operations规范，X唯一owner和本次外发授权仍优先；只有API与受限配置不能提供必需信息时，才让原owner核验已授权的精确管理页面，不扩展为其他聊天或应用操作。

用户专项授权的X通知可由原owner在本次自有飞书标签只读核对唯一原目标、时间和内容，不要求先有transport_accepted，不要求用户手动确认收件。目标不唯一则停止，不通过UI发送/重发，不读Cookie/令牌或无关历史。网页已登录、机器平台消息回读、网页可见与用户本人已读分别记录；观察结果不伪写机器送达。维护通知不计X采集/水位/周期；过期验收是否可替换严格按本次项目授权和受审维护入口，不把条件替换推广到普通业务或历史未知消息。


投递解析故障先区分中枢注册、解析子进程、平台发送和可见回执。`pre_send_source_resolver_failed` 本身不证明权限错误或已经发送；用原key/route/代次仅GET对账，并核对实际服务namespace的文件系统与发布入口，不能因root账号就假设可写。修复后的X `resolve-delivery` 使用专用只读快照，保持原成功schema/正文哈希和5秒中枢子进程上限；空闲库原静止签名、有WAL原生只读事务，缺库/缺迁移标记继续拒绝。resolver不得初始化/恢复业务行或要求扩大中枢X目录写权限。Linux读者与writer关闭在现有DB inode上协调，保持原health/关闭期限；遗留旁车仍是失败门禁，不能自动维护、删除或重发dead-letter。实际是否已部署须回读current，不能把候选或Skill更新当上线证据。
