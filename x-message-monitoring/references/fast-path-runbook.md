# 固定快速路径（分流协议 1 / 驱动 1.2.0）

这是操作清单，不是让模型重写采集器的伪代码。真实扫描仅在唯一固定任务完成；初始化和验收仍留在该任务。

## 入口与状态

- 命令工作目录：`C:\Users\12070\Desktop\项目开发\cc-connect-operations\projects\x-monitor\notifications`。
- 业务目录：`C:\Users\12070\Desktop\项目开发\X监控`；解释器 `.venv\Scripts\python.exe`；入口 `scripts\fixed_session_entry.py`；数据仅在业务 `data`。
- 全部命令通过 UTF-8 对象序列化后标准输入，禁止正文/凭据出现在参数、普通日志或临时脚本中。PowerShell 输入输出均设 `[Text.UTF8Encoding]::new($false)`；不要手拼 JSON。
- CUA 状态只用 `let xMonDriver`、`let xMonTab`、`let xMonCycle`、`let xMonRaw`；各变量第一次使用必须声明，已经存在时只赋值，不能遗漏初始化或重复声明。本轮 lease 来自一次 acquire；不能复制旧值。工具会话重置时不接续旧浏览器句柄，登记失败并 finish。

## 按表执行，失败不探索

| 顺序 | 固定操作 | 收口条件 |
|---|---|---|
| 1 | `health` → `heartbeat-acquire --stream-protocol independent` | 无有效锁才继续；定时默认 scheduled，手动显式 `--trigger-kind manual_validation`。账号来自 acquire，水位来自本轮 health。 |
| 2 | `publish-pending` 预检；`sync-receipts` 对账一次 | 都用 `{lease}`。预检不通过，两流不开工。历史未知单列，不永久阻塞新扫描。 |
| 3 | 创建本轮 Chrome 标签，加载/复用驱动 | 登录控件与 X 账号必须为 `@dmdmws`，不从配置或存储猜测。 |
| 4 | 主帖 `page` → `rawStream(main)` → `collect-stream` → `analysis-plan` → `scan-analysis` → `publish-pending` | 只处理 main；局部失败登记 `stream-failure`，仍可继续 reply。 |
| 5 | 回复 `replyGate` → `page(search)` → `permalinkBatch` → `rawStream(reply)` → 同上三个本地入口 → `publish-pending` | 回复失败保留回复旧水位，主帖提交不回滚；共同身份/路由故障停止后续账号。 |
| 6 | 关闭本轮标签；同一 lease 调用 `heartbeat-finish` | 机器两阶段处理最终投递、回执并释放锁。缺失/不可解析必须报告，不能 catch 后静默。 |

20 分钟为整轮上限，不无限续租。每流最多 200 条、12 次分页，须到 ID 与 UTC instant 同时匹配的水位；`Z`、`.000Z` 等价。超过上限不重置、不跳过历史。各流内部旧到新，跨流按提交完成顺序登记。

## 浏览器：固定源加载，不生成新驱动

1. 第一次 CUA 调用仅执行 `let xMonTab = await cua.createBrowserTab("chrome", url, {sessionName:"🌐 X监控"})`；url 为 `https://x.com/search?q=` 加 `encodeURIComponent("from:"+account+" -filter:replies -filter:retweets")` 加 `&f=live`。读取工具返回的文档和初始状态。驱动核验准确查询和 Latest 选项；不要读取或排序主页卡片来替代主帖搜索。
2. 驱动未加载或版本变化时，完整读取业务 `scripts/desktop_monitor_driver.js` 原文，将**原样工厂表达式**绑定为 `let xMonDriver = <原样表达式>`；变量已存在时只赋值 `xMonDriver = <原样表达式>`，不重复声明。它只接收受支持的标签句柄。不能导入内部模块、执行独立 Playwright、使用 `globalThis`、自行 eval/桥接或重写 selector。新轮次只创建 cycle，不重复生成 2 万多字符代码。
3. `xMonCycle = xMonDriver.createCycle({account, authenticatedAccount:"dmdmws", source:"desktop_chrome_extension", fallbackReason:null, watermarks, sourceTimezone})`。参数来自本轮配置。设 `xMonCycle.ownTab=xMonTab`、`xMonCycle.lastUrl=url`，避免再次导航创建时的同一地址。
4. 工具调用上限 40 秒；page 一次一页，permalinkBatch 一次最多两个永久链接，页面总探测预算 15 秒。驱动等待账号头部文本和目标卡片脱离骨架态后抽取一次；仅出现时间链接不等于正文就绪。就绪立即继续，不固定等待、不增加重试循环。`tab.playwright` 是允许的受控扩展 DOM 门面，独立 Playwright 不允许。
5. 首次 Chrome 明确 `browser_not_running`：仅一次项目 `start_managed_browser.ps1 -Browser chrome`，无 URL，等待规定 8 秒，重试一次创建标签。其他失败不重启 Chrome。
6. Chrome 最终仅 `browser_not_running`、`extension_unavailable`、`login_unavailable` 可调用 `chrome-fallback-authorize`，输入 `{lease,account,reason}`。机器明确允许后才用 Edge；按项目规定只启动一次、等待 8 秒、创建一次新 Edge 标签。source 改为 `desktop_edge_extension`，fallbackReason 保留 Chrome 原因。已提交/采集后不跨浏览器混合事实。
7. Edge 失败调用 `browser-failure`：`{lease,account,browser:"edge",reason:<Chrome原因>,state:<稳定状态>}`；状态为 `extension_disconnected` / `login_required` / `risk_challenge`。只有 Chrome 时 browser 为 chrome。账号不符走严格 login-state，账号必须来自实际观察。随后 finish；不改登录态，不触碰用户原有标签。

## 驱动调用

每个 ok=false 直接使用返回 failure_code 和 stage，不现场改代码修载荷。导航、主列、目标、成员、父帖、水位分开记录。

1. `await xMonDriver.page(xMonTab,xMonCycle,"main")`；仅 ok=true,done=false 时下一次 `page(...,"main",true)`。完成后 `xMonRaw=xMonDriver.rawStream(xMonCycle,"main")`，立即提交 main。
2. `await xMonDriver.replyGate(xMonTab,xMonCycle)`；只有 action=repeat_gate_once 才再调用一次。驱动限定零页或无标记且旧二元组不成立两种整流切换条件，不混合旧/新证据。
3. `page(...,"search")`，未到水位才 `page(...,"search",true)`。搜索冻结后 `observation-fingerprint` 输入 `{lease,payload:xMonCycle.search.map(i=>i.statusId)}`，取机器 fingerprint。
4. `await xMonDriver.permalinkBatch(xMonTab,xMonCycle,fingerprint)`，每次最多两条，仅 ok=true,done=false 继续下一批。完整唯一主会话链、相邻父帖和独立回复对象由驱动核验。文本加图片父帖可读，头像不是帖子媒体；纯媒体不编造文字。
5. `xMonRaw=xMonDriver.rawStream(xMonCycle,"reply")`。保留该对象原样，不重新调用以免改变 collectedAt。仅经受控标准输入传递，不保存采集正文文件。

驱动以独立 `User-Name` 加 `tweetText` 的嵌入链接块识别当前 X 引用结构；其文字不属于外层作者正文。遇到其他多正文形态仍报告明确错误，不现场挑选第一段或改写驱动。

## 本地载荷：程序机械组装

- `collect-stream`：`{lease,payload:xMonRaw}`。xMonRaw 为 XCollectedStreamV1：仅本流的 V2 可见事实和驱动耗时；不存在另一条流，不伪造空流。
- `analysis-plan`：同一 `{lease,payload:xMonRaw}`。只分析 analysis_items，ID 恰好对应 new_status_ids；空列表直接提交空 analyses。不要翻译锚点或历史事件。
- `scan-analysis`：`{lease,payload:{collected:xMonRaw,analyses:{<新状态ID>:<分析对象>}}}`。程序转换 raw 字段、父帖上下文、索引及指纹，形成严格 XMonitorScanInputV3 并调用 scan-stream；模型不生成映射代码。
- 分析对象必需 chinese_translation、chinese_summary、full_analysis、reset_analysis；回复另需 ai_relevance，AI 相关回复另需 reply_parent_chinese_translation。不能覆盖可见事实。
- reset_analysis 不相关仅为 `{related:false}`。相关必须包含 `related:true,time_expression,reasoning,estimate_precision,possible_range_beijing,confidence`；confidence 仅 low/medium/high，estimate_precision 仅 exact/range_only。exact 另需 most_likely_beijing，range_only 必须省略该键。证据不足不给精确时刻；先中文翻译，再时间，再判断，公开帖不是账号额度重置的官方证明。
- `isMediaOnly=true` 时不得猜测图片内容：reset_analysis 必须为 `{related:null,unassessed_reason:"media_only_not_inspected"}`，中文翻译、中文概述与完整分析三项都须明确包含“未读取媒体内容”；若为回复，ai_relevance 也只能为 null 并说明未读取媒体。图文有可信正文时不属于纯媒体。
- 回复 ai_relevance 为 `{schema_version:"AiRelevanceV1",ai_related:true|false,reasoning:<依据>}`；无法判断用 `{schema_version:"AiRelevanceV1",ai_related:null,unassessed_reason:<稳定原因>}`。结合回复和直接父帖，AI 不等于额度相关；非 AI/未评估抑制并推进可信水位，不重分类历史。
- 局部失败 `stream-failure`：`{lease,payload:{account,stream:"main"|"reply",stage,failure_code}}`。浏览器已选择时，附 `browser:xMonCycle.source,driver_version:xMonDriver.version,timings:<本流有界耗时>`，机器核验其与本轮浏览器授权一致；记录这些诊断不代表采集成功。若 collect/scan 已登记失败，不二次覆盖。

## 最终回执

XMonitorHeartbeatFinalizeV2.streams 是逐账号逐流证据；scan_complete 仅表示双流扫描，不能代替投递结果。

- heartbeat_complete=true、outcome=completed 且 notification_decision=DONT_NOTIFY：精确输出 `DONT_NOTIFY`。
- outcome=partial_failed：中文说明“主帖正常，回复降级”或反向，附失败阶段，不声称全部恢复。
- REPORT、锁冲突、finish 缺失/不可解析：简短中文故障。历史冻结未知单列，本轮未知不能静默。
- 四轮摘要只计完整、零可推送的定时运行，手动不计。登记/传输接受不代表可见送达，未知不重发。缺可见性证据时明确“送达未验证”。
