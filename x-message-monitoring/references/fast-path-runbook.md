# X 消息监控固定快速路径

此文件只固定执行编排，不改变 `heartbeat-and-delivery.md` 的事实、投递或失败关闭规则。机器回执、SQLite 水位和项目契约始终优先。

## 一次初始化，普通轮次直接执行

- 普通 heartbeat 启动时唯一必须完整读取的支持文件就是本快速路径；不要例行读取 `heartbeat-and-delivery.md`、项目 `README.md` 或固定入口帮助。
- 固定会话首次运行、上下文丢失、项目或 Skill 文件指纹变化、机器 schema 变化时，才完整读取项目 `AGENTS.md`、`README.md`、固定入口帮助与 `heartbeat-and-delivery.md`，并刷新当前上下文中的规则指纹和 schema 认知。
- 普通轮次不枚举 CLI、不搜索浏览器模块、不试探 `agent`、`@oai/browser` 或版本化内部模块；直接使用当前 Computer Use 工具文档给出的 `cua.createBrowserTab` / 已返回标签句柄。
- 全轮只维护两个稳定概念对象：本地阶段对象 `xMonCycle` 与受控浏览器对象 `globalThis.xMonBrowserCycle`。禁止 `xCycle17*`、`probe2Final*` 等编号式临时对象。
- `xMonCycle.lease` 只可由本轮一次 `heartbeat-acquire` 回执赋值；不得手输、覆盖、从旧轮复制或申请第二个 lease。

## 契约裁决触发

普通轮次不得为“保险起见”预读完整契约。仅在下列任一条件成立时，快速路径才明确要求暂停当前编排并完整读取一次 `heartbeat-and-delivery.md`：

1. 固定会话首次运行，或上下文丢失后无法证明当前规则和状态机仍完整。
2. 项目 `AGENTS.md`、本 Skill、两份参考或自动化提示的文件指纹与当前上下文记录不一致。
3. `health` 或后续机器回执出现未知 schema、未知字段语义，或已知 schema 发生变化。
4. 机器回执给出的浏览器备用原因、回复证据、直接父帖、投递状态或 finish 结果无法由本快速路径唯一裁决。
5. 本快速路径与当前项目规则或机器回执表面冲突，需要按更严格规则确定唯一动作。

契约裁决只用于选择更严格的既有动作，不授权重试、换 selector、换浏览器、修改已拒载荷或申请第二个 lease。普通无新增、已知失败码、候选数量增加、常规 AI 分类或额度分析本身都不是契约裁决触发条件。

## 固定本地入口包装

所有正文载荷必须由对象序列化后走 UTF-8 标准输入，不能手写或拼接 JSON 字符串：

```powershell
[Console]::InputEncoding  = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$xMonRoot = 'C:\Users\12070\Desktop\项目开发\X监控'
$xMonPython = Join-Path $xMonRoot '.venv\Scripts\python.exe'
$xMonEntry = Join-Path $xMonRoot 'scripts\fixed_session_entry.py'

function Invoke-XMonRead([string]$Action) {
    $raw = & $xMonPython $xMonEntry $Action --format json
    if ($LASTEXITCODE -ne 0) { throw "x_monitor_${Action}_failed" }
    $raw | ConvertFrom-Json -Depth 100
}

function Invoke-XMonInput([string]$Action, [object]$Envelope) {
    $raw = $Envelope | ConvertTo-Json -Depth 100 -Compress |
        & $xMonPython $xMonEntry $Action --input - --format json
    if ($LASTEXITCODE -ne 0) { throw "x_monitor_${Action}_failed" }
    $raw | ConvertFrom-Json -Depth 100
}
```

固定调用形状为：

```powershell
$xMonHealth  = Invoke-XMonRead 'health'
$xMonAcquire = Invoke-XMonRead 'heartbeat-acquire'
$xMonCycle = [ordered]@{
    phase = 'LEASED'
    lease = $xMonAcquire.lease
    accountIndex = 0
    terminalFailure = $null
}

Invoke-XMonInput 'heartbeat-renew' @{ lease = $xMonCycle.lease }
Invoke-XMonInput 'publish-pending' @{ lease = $xMonCycle.lease }
Invoke-XMonInput 'collect' @{ lease = $xMonCycle.lease; timeline = $xMonTimelineV2 }
Invoke-XMonInput 'scan' @{ lease = $xMonCycle.lease; scan = $xMonScanV3 }
Invoke-XMonInput 'heartbeat-finish' @{ lease = $xMonCycle.lease }
```

备用授权和最终浏览器失败只允许使用固定入口的既有严格包络；不要自行补字段、生成 schema 或重复调用。

## 固定状态机

```text
INIT → HEALTHY → LEASED → ROUTE_READY → BROWSER_SELECTED
→ MAIN_COMPLETE → REPLY_MODE_SELECTED → REPLIES_COMPLETE
→ COLLECTED → ANALYZED → SCANNED → FINISHING → COMPLETED
```

任一阶段失败后只允许：

```text
FAILED_PENDING_FINISH → FINISHED_FAILED
```

失败后立即停止当前阶段，关闭本轮标签，并以同一 lease 调用一次 `heartbeat-finish`。不返回上一阶段、不换 selector、不修改已拒载荷、不申请新 lease。

## 浏览器固定入口

1. 通过当前 Computer Use 文档规定的入口，直接把动态账号主页传给 `cua.createBrowserTab("chrome", url, { sessionName: "🌐 X监控" })`，并把返回句柄保存到 `globalThis.xMonBrowserCycle.tab`。首次浏览器调用只做这一件事。
2. Chrome 明确为 `browser_not_running` 时，才按项目脚本启动一次、等待 8 秒并再调用一次同一入口。其他三种合法不可用结果先取得机器 Edge 授权，再以同一方式创建唯一 Edge 标签。
3. 不使用旧 `agent.browsers`、不导入 `@oai/browser`、不导入版本化内部 browser-client 模块、不从标签列表重新绑定刚创建的标签。
4. `tab.playwright` 只表示当前受控标签的 DOM 操作门面，应优先用于窄范围结构提取；禁止的是独立 Playwright 浏览器、CLI、Python/Node 包、调试端口和额外进程。
5. 无论成功或失败，只关闭 `globalThis.xMonBrowserCycle.tab`；若句柄从未成功创建，不枚举或关闭任何现有标签。

## 页面阶段与唯一分支

### 统一条件等待收口

`MAIN_PROBE`、`REPLY_SEARCH_PROBE` 与 `PERMALINK_PROBE` 的 locator readiness 都必须使用同一规则：在当前 Computer Use 调用内条件等待最多 5 秒，并在同一调用内 `catch` deadline；不得让原始 Playwright timeout 逃逸到调用结果。

deadline 后只允许做一次无等待、无循环的页面包络核验。该核验只判断当前规范 URL、预期标题、唯一主列、登录门、验证码/风控门、显式错误面、可信空态及本阶段必需卡片是否存在；核验自身异常也必须被同一调用捕获。不得返回原始异常、错误栈、selector、HTML、正文或页面异常原文，也不得 reload、滚动、换浏览器、换 selector 或再次等待。

页面包络不可信时只返回阶段稳定子原因 `main_surface_untrusted`、`reply_search_surface_untrusted` 或 `permalink_surface_untrusted`；明确登录失效或风控时仍映射到项目既有 `login_unavailable` 或 `risk_challenge`，不能伪装成普通结构异常。页面包络可信但必需卡片仍未出现时，主页返回 `main_surface_not_ready`，Latest 搜索返回 `reply_search_surface_not_ready`，永久链接返回 `permalink_surface_not_ready`；若主页或 Latest 搜索已有动态可信水位且当前可信页面未能包含该双字段锚，则分别返回 `main_watermark_unreached` 或 `reply_watermark_unreached`。这些子原因都进入既有失败关闭与同轮 finish，不得降级为 generic `structure_ambiguous` 后继续探索。

### MAIN_PROBE

- 一次调用完成导航、最多 5 秒的主列就绪核验和一次同步 DOM 提取，只返回契约所需的可见事实与数量。
- 主列或首批必需卡片的条件等待必须使用统一收口；deadline 后不得直接抛出 Playwright timeout，也不得为了判断页面是否可用而追加第二次页面读取。
- 不做无条件固定等待；水位未到时才执行下一次有界分页探针。
- 每次分页必须增加至少一个唯一状态；无进展、重复、错序、超过 200 或到达第 12 次仍未命中双字段水位时失败关闭。

### REPLY_GATE_PROBE

- 导航 `/with_replies` 后一次取得页面可信条件、目标作者卡数量和最小局部分组事实。
- 初次目标卡精确为 0 时，同地址重载并重探最多一次；仍为 0，整条回复流立即切到永久链接模式。
- 非零页遇到第一张未被主帖精确排除、无可见回复标记且无法成立旧唯一局部分组的卡时，立即丢弃全部旧模式中间事实并整流切换；不继续分类剩余旧卡。
- 其他结构歧义、水位未到、风控或验证失败不切 Edge，也不切第二套页面策略。

### REPLY_SEARCH_PROBE

- 一次进入 Latest 搜索，按有界分页累计唯一候选直到动态回复水位。
- 首个 `article` 的 locator readiness 必须在同一调用内捕获 5 秒 deadline 并执行统一页面包络核验；不得把原始 timeout 上抛为 generic `structure_ambiguous`。deadline 收口分支不得 reload、滚动或切换浏览器。
- 每次读取必须严格按 `stable status link → placeholder filter → bounded same-viewport re-read → strict candidate validation → pagination` 执行。先从目标作者的规范 `/status/<id>` 链接取得稳定身份；没有稳定状态 ID/规范链接且呈加载骨架的 `article` 只是瞬态占位节点，忽略且不计进度，不能因此报结构异常。
- 一旦节点已经有稳定状态 ID，它就是正式候选：作者、规范 UTC 和状态类型必须完整。字段暂缺时只允许在**同一次 Computer Use 调用、同一视口**内做一次有界补读；仍缺失立即失败，不能把它降级为排除项或非 AI 回复。
- `isMediaOnly=true` 是有效候选，不是页面异常：`visibleText` 必须精确使用项目保留标记 `[Media-only post; no visible text.]`，后续按 `ai_related=null` 处理。转推只以状态卡自己的专用 social-context 标记判断，禁止在整张卡片正文中搜索“reposted/转帖/转发”等词来推断。
- 补读不得 reload、不得再次滚动、不得切浏览器；禁止先固定 `waitForTimeout` 再读取。只对身份稳定但字段暂缺的候选等待最多 5 秒，并在当前调用内重读一次。
- 每次分页必须增加唯一候选；重复、错序、无进展、超过 200 或第 12 次仍未到水位时失败关闭。
- 冻结最终候选顺序后再进入永久链接核验，不在核验期间改变搜索集合。

### PERMALINK_PROBE

- 每个动态候选恰好一次 Computer Use 调用：在同一调用内导航永久链接、最多 5 秒核验唯一最小主 `status_permalink` 会话容器、提取有序顶层 ID 链及父/目标事实。
- 不拆成“导航一次、睡眠一次、读取一次”，不跨候选循环，不读取整页平铺卡片。
- 容器定位只使用一个预批准 selector：目标卡最近的 `[data-testid="primaryColumn"] section[role="region"][aria-labelledby]` 语义容器。先证明 `[data-testid="primaryColumn"]` 唯一，并证明目标规范状态只对应一张非嵌套顶层 `article[data-testid="tweet"]`；只沿目标卡祖先链取得这个最小容器，绝不把 `primaryColumn`、更外层祖先或其他 section 当候选。禁止用 `targetCell.parentElement.children` 猜父，禁止从 `primaryColumn.querySelectorAll('article')` 的整页平铺结果挑父，也不得失败后换 selector。
- 可复制的 locator readiness 形状（仍在同一 `tab.playwright` 单候选调用内）：只以动态 `targetStatusId` 构造 `targetTimeLink = 'article[data-testid="tweet"] a[href$="/status/' + targetStatusId + '"] time'`，以状态路径精确结尾避免 ID 前缀误命中，不拼接作者、不要求当前视口可见。先 `await tab.playwright.locator('[data-testid="primaryColumn"] ' + targetTimeLink).last().waitFor({state:'attached', timeout:5000})`；再令 `sectionCandidates = tab.playwright.locator('[data-testid="primaryColumn"] section[role="region"][aria-labelledby]:has(' + targetTimeLink + ')')`，`conversationLocator = sectionCandidates.last()`。同一 statusId time-link 的匹配 section 只能是包含该 DOM 链接的嵌套祖先，故 `last()` 是预批准的最小 section；接着 `await conversationLocator.waitFor({state:'attached', timeout:5000})`，再令 `topLevelTweetLocator = conversationLocator.locator('article[data-testid="tweet"]:not(article[data-testid="tweet"] article[data-testid="tweet"])')`，并 `await topLevelTweetLocator.nth(1).waitFor({state:'attached', timeout:5000})`。第二张非嵌套 tweet 只作成员 readiness；三次等待都不读取正文、不把卡指定为父帖，随后仅一次同步 `evaluate` 验证唯一 closest 容器和完整非嵌套顶层链。
- 禁止在 `evaluate` 内用 `while`、`setTimeout`、`requestAnimationFrame` 或任何轮询等待页面；也不得固定 sleep、滚动、重载、读取正文或延长等待。全部 locator 等待必须置于同一 `try`；deadline 只进入一次 `catch`，在其中调用一次无等待、无循环的页面包络核验并返回它的单个稳定 envelope。若 envelope 可信，目标 time-link deadline 返回 `permalink_target_not_ready`，section 或第二张 tweet deadline 返回 `permalink_members_not_ready`；若 envelope 不可信，仍返回既有 `permalink_surface_untrusted` 或 `permalink_surface_not_ready`。readiness 绝不构成作者、永久链接、UTC 或正文事实；成功后只做上述一次同步提取，只有该提取所得链长度仍不满足 2～200 时才使用 `permalink_conversation_chain_size_untrusted`。
- 同步提取仍只读 `conversation` 的直接顶层 `article[data-testid="tweet"]`，按 DOM 顺序形成完整局部链；嵌套的推荐、额外 timeline/region 或其他分区不得成为链成员，推荐卡、其他回复分支或其他分区不得成为父目标链成员。链身份与父目标详情必须分两层解析：`parseStableIdentity` 只为每个顶层成员取得其自身、且不在 quoteTweet 内的唯一规范 time-link，据此产生状态 ID、作者、可解析 UTC 和规范永久链接；链中非父目标成员不要求正文或媒体事实，禁止因祖先/后续成员正文为空而返回 null。任一成员缺稳定身份使用 `permalink_chain_member_identity_missing`；ID 重复使用 `permalink_chain_duplicate`，不得静默去重。
- 稳定身份链必须为 2～200、ID 全唯一，目标恰好一次且索引至少为 1，`parentIndex = targetIndex - 1` 的槽位精确命中父/目标；随后只对这两张卡执行 `parseTargetAndParentDetails`。目标必须与 Latest 冻结观察的状态 ID、作者、规范 UTC、规范永久链接、可见正文或媒体标记、回复/转推/引用标志逐项一致；任何不一致使用 `permalink_final_observed_mismatch`。父/目标均须为顶层、非引用、非推广，父作者等于回复对象且父 UTC/数值 ID 早于目标；父目标卡自身的身份或详情不可解析时使用 `permalink_target_or_parent_detail_invalid`。宽链携带完整 ID 链和显式索引；精确二卡链也可携带索引。任一不可信条件均失败关闭。
- 父帖有可见正文时一并保留其原文和规范永久链接供后续 AI 判断；不得用引用卡、祖先卡或媒体占位代替父正文。父帖稳定身份可信但正文缺失或仅媒体时，不伪造父正文、不把它当作全链身份失败：对应回复只能进入 `ai_related=null` 的已处理/已抑制路径，不能创建普通投递。第一处无法证明唯一容器、父目标相邻或父目标稳定身份时，立即终止整条回复流；不尝试第二套 selector。
- 探针失败只返回一个稳定脱敏子原因，不回传 selector、HTML、截图、正文或浏览器异常原文：`permalink_primary_column_untrusted`、`permalink_target_not_ready`、`permalink_members_not_ready`、`permalink_target_status_not_unique`、`permalink_target_top_level_untrusted`、`permalink_conversation_container_missing`、`permalink_conversation_chain_size_untrusted`、`permalink_chain_member_identity_missing`、`permalink_chain_duplicate`、`permalink_target_or_parent_detail_invalid`、`permalink_final_observed_mismatch`、`permalink_target_not_unique_top_level`、`permalink_adjacent_parent_untrusted`、`permalink_conversation_container_ambiguous`、`permalink_conversation_partition_untrusted`、`permalink_parent_or_target_card_untrusted`、`permalink_parent_author_mismatch` 或 `permalink_parent_time_order_untrusted`。
- 每核验 5 个候选后续租一次；续租应与下一候选的同一工具批次编排，不额外探索页面。

### BUILD_AND_CLOSE

- 在内存中一次性构造观察列表、raw V2、可见事实指纹和严格数组形状；锚点只参与验证，不重复翻译。
- 在调用 `collect` 前关闭本轮标签。页面工具只回传阶段、数量和成功/失败，不额外打印正文。
- `collect` 每账号最多一次；成功后只分析严格晚于水位的新状态。无新增时直接构造两个 `{complete:true,items:[]}`。
- 一个账号只批量分析一次、`scan` 一次；契约拒绝后不得修改输入再试。

## 精确停止表

| 条件 | 唯一动作 | 最大次数 |
| --- | --- | ---: |
| Chrome 首次 `browser_not_running` | 启动、等待 8 秒、重取句柄 | 启动 1；重取 1 |
| Chrome 扩展/实例/登录不可用 | 机器授权后单次 Edge 路径 | 授权 1 |
| Chrome 结构歧义、水位未到、风控或 V2 拒绝 | 失败收口 | 重试 0 |
| Edge 首次 `browser_not_running` | 启动、等待 8 秒、重取句柄 | 启动 1；重取 1 |
| `/with_replies` 初次 0 卡 | 同地址重载并重探 | 重载 1 |
| Latest 无稳定状态链接的骨架卡 | 忽略且不计进度 | 0 次外部重试 |
| Latest 有稳定 ID 但字段暂缺 | 同调用、同视口有界补读 | 1 次 |
| Latest `isMediaOnly=true` | 使用固定标记保留为正式候选 | 不失败 |
| Latest 重复、错序、无进展、超限或水位未到 | 回复流失败 | 替代查询 0 |
| MAIN/Latest/permalink readiness 达到 5 秒 deadline | 同调用 catch，并只做一次页面包络核验后失败关闭 | 包络核验 1；外部重试 0 |
| 单条 permalink 结构不可信 | 第一处即停止 | 每候选 1 调用 |
| `collect` 拒绝/不可解析 | 不改载荷，进入收口 | 每账号 1 |
| `scan` 拒绝/不可解析 | 不改分析，进入收口 | 每账号 1 |
| `heartbeat-finish` 未知/不可解析 | 不重发、不二次 finish | 每周期 1 |

## 调用预算

设冻结账号数为 `A`，账号 `i` 的永久链接候选数为 `Nᵢ`，主页和搜索分页各最多 12：

- 机器入口：`health=1`、`heartbeat-acquire=1`、预检 `publish-pending=1`、`collect≤A`、`scan≤A`、`heartbeat-finish=1`；续租为 `2 + 2A + Σceil(Nᵢ/5)`。
- 单账号机器入口上限为 `10 + ceil(N/5)`。
- 浏览器：Chrome 句柄最多 2；合法 Edge 路径最多再 2；新建标签 1；主页探针最多 12；回复门探针最多 2；搜索探针最多 12；permalink 恰好 `N`；关闭标签 1。
- 永久链接模式的常见路径应约为 `N + 6` 次浏览器调用；执行预算耗尽属于流程编排异常，立即关闭本轮标签并收口，不能扩大重试。

## 最终输出

- `heartbeat-finish` 三项完成条件成立时精确返回 `DONT_NOTIFY`，包括第四轮已经通过飞书发送健康证明的情况。
- `REPORT`、失败回执、缺失或不可解析回执必须输出一句脱敏中文故障；绝不能因为任务调用方要求静默或历史任务曾使用 `NO_REPLY` 而吞掉失败。
