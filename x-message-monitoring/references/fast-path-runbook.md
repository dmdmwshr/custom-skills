# X 消息监控固定快速路径

此文件只固定执行编排，不改变 `heartbeat-and-delivery.md` 的事实、投递或失败关闭规则。机器回执、SQLite 水位和项目契约始终优先。

## 一次初始化，普通轮次直接执行

- 固定会话首次运行、上下文压缩后、项目文件指纹变化或机器 schema 变化时，才重新读取完整项目规则、入口帮助和两份 Skill 参考。
- 普通轮次不枚举 CLI、不搜索浏览器模块、不试探 `agent`、`@oai/browser` 或版本化内部模块；直接使用当前 Computer Use 工具文档给出的 `cua.createBrowserTab` / 已返回标签句柄。
- 全轮只维护两个稳定概念对象：本地阶段对象 `xMonCycle` 与受控浏览器对象 `globalThis.xMonBrowserCycle`。禁止 `xCycle17*`、`probe2Final*` 等编号式临时对象。
- `xMonCycle.lease` 只可由本轮一次 `heartbeat-acquire` 回执赋值；不得手输、覆盖、从旧轮复制或申请第二个 lease。

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

### MAIN_PROBE

- 一次调用完成导航、最多 5 秒的主列就绪核验和一次同步 DOM 提取，只返回契约所需的可见事实与数量。
- 不做无条件固定等待；水位未到时才执行下一次有界分页探针。
- 每次分页必须增加至少一个唯一状态；无进展、重复、错序、超过 200 或到达第 12 次仍未命中双字段水位时失败关闭。

### REPLY_GATE_PROBE

- 导航 `/with_replies` 后一次取得页面可信条件、目标作者卡数量和最小局部分组事实。
- 初次目标卡精确为 0 时，同地址重载并重探最多一次；仍为 0，整条回复流立即切到永久链接模式。
- 非零页遇到第一张未被主帖精确排除、无可见回复标记且无法成立旧唯一局部分组的卡时，立即丢弃全部旧模式中间事实并整流切换；不继续分类剩余旧卡。
- 其他结构歧义、水位未到、风控或验证失败不切 Edge，也不切第二套页面策略。

### REPLY_SEARCH_PROBE

- 一次进入 Latest 搜索，按有界分页累计唯一候选直到动态回复水位。
- 每次读取必须严格按 `stable status link → placeholder filter → bounded same-viewport re-read → strict candidate validation → pagination` 执行。先从目标作者的规范 `/status/<id>` 链接取得稳定身份；没有稳定状态 ID/规范链接且呈加载骨架的 `article` 只是瞬态占位节点，忽略且不计进度，不能因此报结构异常。
- 一旦节点已经有稳定状态 ID，它就是正式候选：作者、规范 UTC 和状态类型必须完整。字段暂缺时只允许在**同一次 Computer Use 调用、同一视口**内做一次有界补读；仍缺失立即失败，不能把它降级为排除项或非 AI 回复。
- `isMediaOnly=true` 是有效候选，不是页面异常：`visibleText` 必须精确使用项目保留标记 `[Media-only post; no visible text.]`，后续按 `ai_related=null` 处理。转推只以状态卡自己的专用 social-context 标记判断，禁止在整张卡片正文中搜索“reposted/转帖/转发”等词来推断。
- 补读不得 reload、不得再次滚动、不得切浏览器；禁止先固定 `waitForTimeout` 再读取。只对身份稳定但字段暂缺的候选等待最多 5 秒，并在当前调用内重读一次。
- 每次分页必须增加唯一候选；重复、错序、无进展、超过 200 或第 12 次仍未到水位时失败关闭。
- 冻结最终候选顺序后再进入永久链接核验，不在核验期间改变搜索集合。

### PERMALINK_PROBE

- 每个动态候选恰好一次 Computer Use 调用：在同一调用内导航永久链接、最多 5 秒核验唯一最小主 `status_permalink` 会话容器、提取有序顶层 ID 链及父/目标事实。
- 不拆成“导航一次、睡眠一次、读取一次”，不跨候选循环，不读取整页平铺卡片。
- 语义容器只允许一个预批准 selector：目标顶层卡祖先链中的 `[data-testid="primaryColumn"] section[role="region"][aria-labelledby]`。沿目标卡祖先链过滤后必须恰好一个；禁止用 `targetCell.parentElement.children` 猜父，禁止从 `primaryColumn.querySelectorAll('article')` 的整页平铺结果挑父，也禁止临场尝试第二个容器 selector。
- 只在该唯一容器内读取顶层 `article[data-testid="tweet"]`：每张卡最近的同类语义 region 必须仍是该容器，且不得嵌套在另一张 article 内。按 DOM 顺序得到完整 2～200 条 ID 链；ID 必须唯一，推荐、推广、额外 timeline/region 或其他分区成员一律失败。不得把推荐卡、其他回复分支或其他分区混入父目标链。
- 目标状态必须在链中恰好一次且索引至少为 1；`parentIndex = targetIndex - 1`，链对应槽位必须精确命中父/目标。父作者必须等于回复对象，父 UTC/数值 ID 必须早于目标；父与目标都必须顶层、非引用、非推广、规范 ID/作者/UTC/永久链接一致。宽链携带完整链和显式索引；精确二卡链也可携带索引。
- 父帖有可见正文时一并保留其原文和规范永久链接供后续 AI 判断；不得用引用卡、祖先卡或媒体占位代替父正文。第一处无法证明唯一容器、父目标相邻或父帖事实时，立即终止整条回复流；不尝试第二套 selector。
- 探针失败只返回一个稳定脱敏子原因，不回传 selector、HTML、截图、正文或浏览器异常原文：`permalink_primary_column_untrusted`、`permalink_target_status_not_unique`、`permalink_conversation_container_missing`、`permalink_conversation_container_ambiguous`、`permalink_conversation_partition_untrusted`、`permalink_top_level_chain_identity_untrusted`、`permalink_parent_author_mismatch` 或 `permalink_parent_time_order_untrusted`。
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
