# 固定快速路径（分流协议 1 / 驱动 3.0.10 / 标准输入客户端 1.0.0）

## 2026-09-09：同入口标准输入直传（当前优先，用户已批准）

公开事实、完整观察序列、机器指纹、草稿、上下文计划、最终 raw 和分析都保留在同一 CUA 宿主内存。五个指定动作直接向原固定 Python 入口的一次性 stdin 提交，不再经模型转抄到 functions。下方旧 packFacts/packFactsGzip/diffFacts、reply_base/reply_delta 和 write_stdin 正文搬运只作历史兼容，不是新周期默认步骤；小型控制动作仍可使用原工具 stdin。浏览器、固定任务、二十分钟上限及发送路由不变。

1. 当前 CUA 已初始化后，通过公开 `node:fs`、`node:vm` 原样读取业务 `scripts/desktop_stdin_client.js`。用公开 `node:buffer.Buffer`、`node:util.TextDecoder`、`node:timers.setTimeout/clearTimeout` 注入该纯工厂；打印 version 与 sourceFingerprint()，必须与本地源读回一致。用公开 `node:child_process.spawn` 创建客户端。只复用普通 let 绑定，禁止内部模块、其他 Node REPL 或自行转写客户端。该有限 Python 子进程不控制浏览器。
2. 首次启用、源码变更或控制会话重置后，在 acquire 前（当前 CUA 已可用时）执行客户端 `selfTest()`。它只运行固定 `scripts/desktop_stdin_fixture.py` 和预置合成数据，不访问账本/X/飞书。必须 ok=true、response.exact_match=true、61 项；只输出字节、哈希、耗时。模块可导入不算通过，失败时停止，不自行换进程通道。新 CUA 的第一条调用仍遵守工具的单一浏览器入口规则，不能为初始化客户端读取用户旧标签。
3. 本轮 acquire/预检通过后，把机器原样 lease 只交给 CUA 宿主的 xMonLease；不放入驱动 cycle、页面求值或普通日志。每轮新建 cycle、清空旧计划/指纹/分析，不从历史恢复 lease。主帖与回复继续独立提交。
4. 搜索冻结后，`send("observation-fingerprint",{lease:xMonLease,payload:完整冻结ID数组})`，先检查包装回执 ok，再将 response.fingerprint 直接给同轮 permalinkBatch。ID 数组和返回值均留在此宿主，不经模型重建。失败不沿用先前哈希。
5. 草稿只生成一次，直接 `send("context-plan",{lease:xMonLease,payload:xMonReplyDraft})`。成功后将完整 response 交给 applyContextPlan，模型只按需读取其中 context_items 的新项事实。必要上文和一层引用完成后，一次 rawStream 得到最终 xMonRaw；不得用草稿冒充最终事实或重新读取历史项。
6. `send("collect-stream",{lease:xMonLease,payload:xMonRaw})`；成功后 `send("analysis-plan",同一对象)`；只读 response.analysis_items 为这些新 ID 补齐分析。分析对象直接保存在此 CUA 宿主，最后 `send("scan-analysis",{lease:xMonLease,payload:{collected:xMonRaw,analyses:xMonAnalyses}})`，原入口机械转换 V3 并执行账本筛选。空 new_status_ids 对应空 analyses。所有步骤仍以原业务回执为准。
7. 客户端内部先等待精确 `XMonitorInputReadyV1`、chunks 和 echo_disabled=true，再把 Array.from 分块帧一次写入同一 stdin；并不打开终端。只有固定五项动作，shell=false、windowsHide=true、路径和 cwd 固定，最多一个在途子进程、30 秒结果期限。禁止参数正文、正文文件、常驻服务、网络桥接和第二个扫描者。
8. 包装回执的 input_frame_bytes/stdout_bytes/elapsed_ms 只描述本次传输，不等于整轮或页面耗时；ok=true 后仍须检查 response 的业务结果。ok=false 且 outcome_unknown=true 表示输入已交给程序而最终结果未知，不重试该动作；同 lease 用只读健康核验是否已提交，已提交流使用 cycle-failure 追加，其他按原失败入口收口。不要输出 stderr、异常原文或在捕获错误后静默。
9. 发生观察指纹不一致时，在清理前只报告：本轮 ID 数量/唯一数、raw 序列与冻结序列逐项相等性、原机器 fingerprint、回复/排除上下文指纹的去重值，以及包装回执字节/耗时。不输出正文或完整 ID 列表，不修改序列/哈希再提交，不从已清理历史恢复。最终仍由 heartbeat-finish 给出两阶段结果，同 token 收口后才清理 lease 和事实。

客户端加载的固定形态（变量首次使用才 let，已有则赋值）：

```javascript
let xMonStdinSource = await (await import('node:fs/promises')).readFile('C:/Users/12070/Desktop/项目开发/X监控/scripts/desktop_stdin_client.js','utf8');
let xMonStdinFactory = (await import('node:vm')).runInNewContext(xMonStdinSource,{
  Buffer:(await import('node:buffer')).Buffer, TextDecoder:(await import('node:util')).TextDecoder,
  setTimeout:(await import('node:timers')).setTimeout, clearTimeout:(await import('node:timers')).clearTimeout
});
let xMonStdin = xMonStdinFactory.createFixedStdinClient({spawn:(await import('node:child_process')).spawn});
nodeRepl.write({version:xMonStdinFactory.version,source_fingerprint:xMonStdinFactory.sourceFingerprint()});
```

上例不执行 selfTest 或业务动作；读回指纹一致后再单独执行 selfTest。不要重放已运行但赋值失败的动作。自检通过不代表生产扫描完成，正式验收仍须在原固定任务完成一轮双流和两次复扫。

## 历史兼容：公开事实的跨工具内存传递（3.0.10）

CUA 与 functions 不共享变量，这是已知接口边界，不是停止合法传递的理由。以下对象都是当前可验证公开帖子事实，允许进入模型工具上下文。开始 acquire 前读懂并准备本节；不等到二十分钟末尾才设计传递方式，不另建通道、正文文件或进程桥接。

预先在当前 CUA 通过公开 Node 标准库 `node:zlib` 取得 `gzipSync`，绑定已声明的 `xMonGzipSync`；原样工厂以 `vm.runInNewContext(source,{URL,TextEncoder})` 实例化。两项均为纯内存计算，不执行浏览器动作、进程或文件写入。该环境已实测 gzipSync 可用，不能改用未提供的 CompressionStream。工厂和压缩函数可跨轮复用；每次新 acquire 后必须重新 createCycle，并确认 mainDone/searchDone 为 false、mainPages/searchPages 为 0。

1. 草稿仍只调用一次 `draftStream`，保留已声明的 `xMonReplyDraft`（主帖变量同理）。CUA 用原固定工厂 `xMonDriver.packFactsGzip(xMonReplyDraft,xMonGzipSync)` 导出 `XMonitorFactTransferV1` 的 `gzip_base64` 对象，工具回传该对象一次。functions 用 `store("reply_base", <刚返回的完整对象>)` 原样保存；不手工展开重复字段、改正文或推测引用。packFacts 的旧字典仍兼容小载荷；实际回复积压使用已准备好的 gzip 压缩，不能再次转抄约四万字符的旧字典。
2. 立即经原 `--input-framing chunks`、InputReady/echo_disabled 与 write_stdin 提交 `{lease,payload:load("reply_base")}` 到 context-plan。固定入口限制解压大小，核对长度、压缩完整性、唯一 JSON 键和规范 JSON 校验码，再还原原 XCollectedStreamV1；仍走原严格水位、身份与事实检查。此校验码只检测传递差错，采集与冻结原有哈希继续生效。
3. functions 可直接解析并保存固定入口返回的结构化计划。模型读取其中 context_items 判断必要上下文；传给 CUA applyContextPlan 的控制对象只需保留计划中的 schema_version、account、stream、draft_fingerprint、max_ancestors、max_quotes、new_status_ids，不重复传 context_items 正文。所有字段都取本次真实计划，不自己推断 new_status_ids。
4. 按既有流程补必要上文/引用。最后仅一次 `rawStream` 得到 xMonReplyRaw；CUA 导出 `xMonDriver.packFactsGzip(xMonDriver.diffFacts(xMonReplyDraft,xMonReplyRaw),xMonGzipSync)`，functions 原样保存 reply_delta。后续同一内存 collected 对象为 `{schema_version:"XMonitorFactTransferV1",representation:"delta",base:load("reply_base"),patch:load("reply_delta")}`。collect-stream、analysis-plan 的 payload 直接用它；scan-analysis 的 collected 也用它，不重新转抄整条时间线。已核验正文、身份、直接父对象和观察序列在差异中不可变；只有新上文/引用、采集时间和耗时可补充。
5. context-plan/analysis-plan 的可读新项事实是分析来源；相关/无关/无法判断按实际内容填写。批量分析可用 functions 的数组与公共字段机械组装严格对象，减少重复键名；不能以模板猜测结论或给已知项重新分析。空 new_status_ids 直接组装空 analyses，同一 functions 调用内可顺序完成标准入口调用，每一步仍检查成功回执。
6. 只传递元数据时回报条数、压缩前后字符数、校验码与阶段耗时；正文只进授权工具上下文/无回显 stdin，不进普通日志。任何校验或原验证失败都报告，不把传输包装当作成功采集。
7. 当前 lease 必须保留到同轮 heartbeat-finish 成功或明确失败回执之后才清理。时间到期、发布异常或浏览器关闭后也先用同 token 完成既有失败收口；不能先清空 lease 再声称无法 finish，不从历史取回 token、不用 renew 绕过整轮二十分钟上限。

3.0.8 的父帖全文规则优先于下文旧父帖滚动步骤：父子关系已核验且截断父帖控件需要移位时，驱动直接读该父帖规范永久链接，以身份、UTC、标志和前缀绑定完整正文及独立引用事实。permalinkBatch 每次最多两个永久链接；pending_parent=true 是正常未完成进度，继续同一批次直到 done=true。contextBatch 返回 pending_status_ids 时只在下次续这些 ID，已返回 items 中的 ID 不重复调用；每页仍限 15 秒、整次工具限 40 秒，不在失败后补读或重试。

这是操作清单，不是让模型重写采集器的伪代码。真实扫描仅在唯一固定任务完成；初始化和验收仍留在该任务。

## 入口与状态

- 命令工作目录：`C:\Users\12070\Desktop\项目开发\cc-connect-operations\projects\x-monitor\notifications`。
- 业务目录：`C:\Users\12070\Desktop\项目开发\X监控`；解释器 `.venv\Scripts\python.exe`；入口 `scripts\fixed_session_entry.py`；数据仅在业务 `data`。
- 全部命令通过 UTF-8 对象序列化后标准输入，禁止正文/凭据出现在进程参数、普通日志或临时脚本中。公开帖的结构化工具返回值是授权事实载荷，允许进入模型上下文与当前 functions 内存供验证/分析，不能把它误当凭据或禁止的普通日志。PowerShell 输入输出均设 `[Text.UTF8Encoding]::new($false)`；不要手拼 JSON。
- CUA 状态使用普通 `let` 明确绑定驱动、标签、cycle、原始事实、客户端、lease、计划和本轮分析；各变量第一次使用必须声明，已经存在时只赋值，不能遗漏初始化或重复声明。本轮 lease 来自一次 acquire；不能复制旧值。工具会话重置时不接续旧浏览器句柄，登记失败并 finish。
- 页面调用结果可直接用 `nodeRepl.write(await xMonDriver.page(...))` 输出，或赋给事先已声明的变量。给未声明变量赋值发生 ReferenceError 时，右侧 await 可能已经完成浏览器动作；不能因此说操作未执行或重放 page。先看本轮纯计数/已有回执，不能确认则失败收口，不通过第二次调用取得一个新结果。

## 按表执行，失败不探索

| 顺序 | 固定操作 | 收口条件 |
|---|---|---|
| 1 | `health` → `heartbeat-acquire --stream-protocol independent` | 无有效锁才继续；定时默认 scheduled，手动显式 `--trigger-kind manual_validation`。账号来自 acquire，水位来自本轮 health。 |
| 2 | `publish-pending` 预检；`sync-receipts` 对账一次 | 都用 `{lease}`。预检不通过，两流不开工。历史未知单列，不永久阻塞新扫描。 |
| 3 | 创建本轮 Chrome 标签，加载/复用驱动 | 登录控件与 X 账号必须为 `@dmdmws`，不从配置或存储猜测。 |
| 4 | 主帖 `page` → `draftStream(main)` → `context-plan` → `applyContextPlan` → `quoteBatch(main)` → 一次 `rawStream(main)` → collect/analysis-plan/scan-analysis → `publish-pending` | 仅新主帖补引用、只推送 Codex 额度重置相关；局部失败登记 stream-failure，仍可继续 reply。 |
| 5 | 回复 `page(search)` → `permalinkBatch` → `draftStream(reply)` → `context-plan` → `applyContextPlan` → 必要的 `contextBatch` 与 `quoteBatch(reply)` → 一次 `rawStream(reply)` → collect/analysis-plan/scan-analysis → `publish-pending` | 仅新回复补上文/引用、只推送 Codex 额度重置相关；失败保留旧水位和已提交主帖。 |
| 6 | 关闭本轮标签；同一 lease 调用 `heartbeat-finish` | 机器两阶段处理最终投递、回执并释放锁。缺失/不可解析必须报告，不能 catch 后静默。 |

20 分钟为整轮上限，不无限续租。每流最多 200 条，须到 ID 与 UTC instant 同时匹配的水位；`Z`、`.000Z` 等价。命中水位后立即停止该流的卡片读取，不携带更旧卡片。下一页滚到本次已观察边界，并在同一 15 秒预算内等待至少一张新状态；不能仅滚动少量像素就把仍显示旧卡片误当时间线结束。超过条数/时限不重置、不跳过历史。各流内部旧到新，跨流按提交完成顺序登记。

## 浏览器：固定源加载，不生成新驱动

1. 第一次 CUA 调用仅执行 `let xMonTab = await cua.createBrowserTab("chrome", url, {sessionName:"🌐 X监控"})`；url 为 `https://x.com/search?q=` 加 `encodeURIComponent("from:"+account+" -filter:replies -filter:retweets")` 加 `&f=live`。读取工具返回的文档和初始状态。驱动核验准确查询和 Latest 选项；不要读取或排序主页卡片来替代主帖搜索。
2. 驱动未加载或版本变化时，在**当前 CUA JavaScript 会话**用公开 Node 标准库 `node:fs` 读取唯一业务 `scripts/desktop_monitor_driver.js` 的 UTF-8 原文，再用 `node:vm` 的 `vm.runInNewContext(source,{URL,TextEncoder})` 原样实例化无浏览器副作用的工厂，绑定普通 `let xMonDriver`；变量已存在时只赋值，不重复声明。不要手工转写整段源码。驱动采集前必须核对 `version` 和 `sourceFingerprint()` 与本地源文件计算值逐项一致；不等则停止，不修改指纹返回值或水位字段来掩盖差异。该文件读取只用于加载固定源码，不读取浏览器数据。浏览器动作仍只由当前 CUA 的受支持标签句柄执行；不能导入内部模块、执行独立 Playwright、使用 `globalThis`、另建控制进程/桥接或重写 selector。其他 Node REPL 与 CUA 不共享变量，不能作为浏览器替代通道。新轮次只创建 cycle，不重复加载源码。
3. `xMonCycle = xMonDriver.createCycle({account, authenticatedAccount:"dmdmws", source:"desktop_chrome_extension", fallbackReason:null, watermarks, sourceTimezone})`。参数来自本轮配置。设 `xMonCycle.ownTab=xMonTab`、`xMonCycle.lastUrl=url`，避免再次导航创建时的同一地址。
4. 工具调用上限 40 秒；page 一次一页，permalinkBatch 一次最多两个永久链接，页面总探测预算 15 秒。驱动等待账号头部文本和目标卡片脱离骨架态后抽取一次；仅出现时间链接不等于正文就绪。就绪立即继续，不固定等待、不增加重试循环。`tab.playwright` 是允许的受控扩展 DOM 门面，独立 Playwright 不允许。
5. 首次 Chrome 明确 `browser_not_running`：仅一次项目 `start_managed_browser.ps1 -Browser chrome`，无 URL，等待规定 8 秒，重试一次创建标签。工具仅报告通用 Browser is not available、未区分未运行/无扩展时，先用该脚本 `-Browser chrome -CheckOnly` 只读检查：`browser_not_running` 才进入上述一次启动；`browser_running` 表示正确根目录实例存在，工具仍不可用按 `extension_unavailable` 处理，不重启；`process_probe_unavailable` 失败关闭，不猜未运行。脚本将带空格的 User Data 作为完整参数，只认可正确根目录主进程，旧 User 目录进程不计。其他失败不重启 Chrome，不清理任何浏览器目录或既有进程。
6. Chrome 最终仅 `browser_not_running`、`extension_unavailable`、`login_unavailable` 可调用 `chrome-fallback-authorize`，输入 `{lease,account,reason}`。机器明确允许后才用 Edge；按项目规定只启动一次、等待 8 秒、创建一次新 Edge 标签。source 改为 `desktop_edge_extension`，fallbackReason 保留 Chrome 原因。已提交/采集后不跨浏览器混合事实。
7. Edge 失败调用 `browser-failure`：`{lease,account,browser:"edge",reason:<Chrome原因>,state:<稳定状态>}`；状态为 `extension_disconnected` / `login_required` / `risk_challenge`。只有 Chrome 时 browser 为 chrome。账号不符走严格 login-state，账号必须来自实际观察。随后 finish；不改登录态，不触碰用户原有标签。

## 驱动调用

每次加载前，在业务源码目录用 Node 的 fs 读取固定驱动文件，并用 vm.runInNewContext 仅实例化无浏览器副作用的原样工厂，打印 `{version,source_fingerprint:driver.sourceFingerprint()}` 作为预期值。这是离线源码核对，不建立浏览器或控制进程。CUA 原样加载后打印相同元数据并比较算法、长度、摘要；通过后才调用驱动采集。FNV 指纹仅检测意外转写变化，不是安全签名或页面证据。3.0.10 保留搜索或详情初次快照没有目标时的共享十五秒预算、一次 AX 刷新及就绪等待，并增加两流新项引用补读；失败之后不导航或采集重试，也不固定延长等待。

展开和引用点击使用驱动刚核验的实际时间链接属性定位，避免规范化后的账号大小写或链接查询串造成零匹配；该属性仅留在本轮驱动内存，事实仍使用规范永久链接。源卡和控件均须唯一，来源身份、展开后的完整正文仍由固定驱动核验，不能现场改选择器或按位置点选。

回复展开控件处于可视区域边缘时，固定驱动按已观察几何用当前 Tab.scroll 居中一次，重新核验同一父/目标与正文前缀；点击后等待自身展开控件消失再重读，全部共用原十五秒预算。位置未解决、身份变化或就绪失败仍关闭该流；不追加固定睡眠，不使用未定义的滚入视口方法，也不失败后补点。

每个 ok=false 直接使用返回 failure_code 和 stage，不现场改代码修载荷。导航、主列、目标、成员、父帖、水位分开记录。当前 Unified CUA 使用 `Tab.scroll([x,y],"down",pages)`，坐标及页数由已读取的主列几何计算；导航/滚动后 `getAXState({emit:false})` 刷新观察，再读取 DOM。不要调用另一套旧接口的 `tab.dom_cua`。驱动已固化这些步骤，不在轮次中探索 API。

1. `await xMonDriver.page(xMonTab,xMonCycle,"main")`；仅 ok=true,done=false 时下一次 `page(...,"main",true)`。`action=main_permalink_details` 表示发现已冻结，后续同一 page 调用自动核验最多两条主帖全文，不再滚动搜索。驱动只对作者自己的可见“显示更多”补读规范原帖；必要时点击已核验目标唯一展开控件并在同一 15 秒预算内重读。身份、UTC、引用/媒体标志与预览前缀必须一致；仍截断不能分析或入账。done=true 后按上下文契约执行 draftStream(main) → 只读 context-plan → applyContextPlan → quoteBatch(main) 至 done=true，再一次 rawStream(main) 并原样提交。
2. 新回复直接进入下一项 Latest 搜索，不访问 with_replies，不调用旧回复主页前置步骤。
3. `page(...,"search")`，未到水位才 `page(...,"search",true)`。搜索冻结后在同一 CUA 宿主把完整 ID 序列直接交给客户端 observation-fingerprint，输入 `{lease:xMonLease,payload:xMonCycle.search.map(i=>i.statusId)}`，检查成功后直接取 response.fingerprint；每轮重新取得，不转抄、不复用旧轮值。
4. `await xMonDriver.permalinkBatch(xMonTab,xMonCycle,fingerprint)`，每次最多两条，仅 ok=true,done=false 继续下一批。完整唯一主会话链、相邻父帖和独立回复对象由驱动核验。文本加图片父帖可读，头像不是帖子媒体；纯媒体不编造文字。
5. 两流开始前完整读取 [上下文、引用与额度契约](reply-reset-contract.md)。回复执行 draftStream(reply) → 只读 context-plan → applyContextPlan，仅新项必要时 contextBatch（至多三层，够用即停）；quoteBatch(reply) 补直接一层引用至 done=true，每条最多五个来源归属，同轮复用。最后一次 rawStream(reply)，原样 collect-stream。reply-context-plan 保留兼容；不更改冻结事实、不保存正文文件。

驱动以独立 User-Name 加 tweetText 或真实帖子媒体的嵌入链接块识别引用；其文字不属于外层作者正文。顶层回复、直接对象和上文允许含引用，内嵌引用不能冒充直接对象。引用卡不含链接时只点击该外层对象内唯一已核验引用块，核对实际跳转；不猜 ID、不递归引用中的引用。明确删除/不可用记录事实；结构、身份、截断和超时仍失败关闭。

## 本地载荷：程序机械组装

### 历史兼容：工具间固定交接（当前事实优先使用上节直传）

1. `nodeRepl.write(xMonRaw)` 返回本流结构化公开事实。不是 HTML，也不写文件；模型此时只搬运事实，不翻译历史状态。将该原样对象与本轮 lease 放入 `functions.store("x-monitor-current", {lease,payload:<该对象>})`，只放一个账号的一条流。
2. 准备好内存对象后，以 `exec_command` 的 `tty:true` 启动同一项目解释器和固定入口：`--input-framing chunks collect-stream --input -`；命令只有固定路径/动作，不含正文。先等到 `XMonitorInputReadyV1` 且 `echo_disabled=true`；没有这条回执不发送数据。不要使用默认无 TTY 管道，它会立即关闭 stdin。
3. 在 functions 中以 `JSON.stringify(load("x-monitor-current"))` 得到帧内容，用 `Array.from` 按每 800 个字符分块；发送格式为第一行 `XMONITOR-JSON-CHUNKS/1`，随后每块一行、行首加 `+`，最后独立一行 `.`。通过原进程的 `write_stdin(session_id,chars)` 输入，一次发完。入口在读取前关闭输入回显，180 秒超时自动退出；输入正文不会回显到终端输出。
4. 同一方式调用 `analysis-plan`，直接复用同一个 functions 内存对象。它返回机器验证后的 analysis_items；只分析这些新项。随后 `scan-analysis` 输入对象为 `{lease,payload:{collected:load("x-monitor-current").payload,analyses:<新项分析>}}`，不重复手工转写 raw。
5. 每次入口读取一帧后就退出。只认最终业务回执；InputReady 不代表 collect 或 scan 成功。切换流/finish 后将 `x-monitor-current` 清空，不落盘，不另建常驻程序，不使用独立 Playwright 或内部浏览器接口。

此通路使用现成终端工具的标准输入，不是新增网络桥接。正文禁入命令参数/日志的约束不禁止上述受控事实工具输出与 stdin；不得再以“没有无正文通路”为由跳过已通过浏览器验证的主帖。

- `collect-stream`：`{lease,payload:xMonRaw}`。xMonRaw 为 XCollectedStreamV1：仅本流的 V2 可见事实和驱动耗时；不存在另一条流，不伪造空流。
- `analysis-plan`：同一 `{lease,payload:xMonRaw}`。只分析 analysis_items，ID 恰好对应 new_status_ids；空列表直接提交空 analyses。不要翻译锚点或历史事件。
- `scan-analysis`：`{lease,payload:{collected:xMonRaw,analyses:{<新状态ID>:<分析对象>}}}`。程序转换 raw 字段、父帖上下文、索引及指纹，形成严格 XMonitorScanInputV3 并调用 scan-stream；模型不生成映射代码。
- 分析对象必需 chinese_translation、chinese_summary、full_analysis、reset_analysis。两流新项统一 ResetAnalysisV3，subject_product=codex/other/unknown，相关/无关/无法判断及 evidence_status_ids 按上下文契约提交。只有 Codex 额度重置相关内容通知；回复 AI 判断只作辅助，候选需父翻译，采用上文/引用需对应翻译，不覆盖可见事实。
- 两流共同支持 exact/range_only/no_time。时间锚指向确实包含时间表达的采用来源；引用原文独立标注作者、翻译、发布时间和链接，不变成外层作者承诺。相关但无时间明确“未提供可推算时间”。仅讨论 Grok 等产品的重置不通知；公开帖子不是本账户已经重置的证据。
- isMediaOnly=true 时不猜媒体内容：两流 ResetAnalysisV3 的 related=null、unassessed_reason=media_only_not_inspected，并携带其余共同字段。翻译、摘要、完整分析都包含“未读取媒体内容”；回复 AI 也为 null。引用不可读取按契约判断其余可信内容，不能用语义不足掩盖结构失败。
- AI 字段 true/false 携带 reasoning，null 携带 unassessed_reason。额度相关必然 AI 相关，AI 相关不一定额度相关。新回复无关/无法判断分别抑制并推进可信水位，不重分类历史。
- 局部失败 `stream-failure`：`{lease,payload:{account,stream:"main"|"reply",stage,failure_code}}`。浏览器已选择时，附 `browser:xMonCycle.source,driver_version:xMonDriver.version,timings:<本流有界耗时>`，机器核验其与本轮浏览器授权一致；记录这些诊断不代表采集成功。若 collect/scan 已登记失败，不二次覆盖。
- 后续投递/回执/标签关闭/收口故障用 `cycle-failure` 追加。同一轮汇总所有受影响阶段并保留首错；不要因流已提交或已经失败而丢掉后续异常。首发/实质变化/满二十四小时一次/完整恢复一次由账本去重，每轮异常仍在原任务报告。

## 最终回执

XMonitorHeartbeatFinalizeV2.streams 是逐账号逐流证据；scan_complete 仅表示双流扫描，不能代替投递结果。

- heartbeat_complete=true、outcome=completed 且 notification_decision=DONT_NOTIFY：精确输出 `DONT_NOTIFY`。
- outcome=partial_failed：中文说明“主帖正常，回复降级”或反向，附失败阶段，不声称全部恢复。
- REPORT、锁冲突、finish 缺失/不可解析：简短中文故障。历史冻结未知单列，本轮未知不能静默。
- 手动验收必须给出本轮主帖/回复结果、零重复计数与送达证据等级；不能返回历史 `NO_REPLY`。失败轮次即使飞书提醒被 24 小时抑制，Codex 仍报告故障。
- 四轮摘要从新策略生效后重新累计完整、零可推送的定时运行，主帖/回复无关与无法判断分别计数，手动不计。登记/传输接受不代表可见送达，未知不重发。缺可见性证据时明确“送达未验证”。
