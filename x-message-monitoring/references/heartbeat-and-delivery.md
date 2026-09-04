# heartbeat 与投递契约

此契约适用于已绑定的唯一 Desktop 固定会话。它不授权创建任务、定时器、浏览器配置、飞书应用或新的发送者。

## 机器回执优先

- SQLite 是正式账本；当前启用账号、可信主帖/回复水位、失败事件与投递状态必须从本轮 `health`、`heartbeat-acquire` 和后续机器回执取得。不得把历史聊天、固定样例、网页文本或字符串时间比较当作水位事实。
- 水位由状态 ID 与带时区 UTC 时间点共同组成。比较时解析为 UTC instant；同一 instant 的不同 RFC3339 小数精度等价。
- 本轮 lease 有效期为 20 分钟。除 `health` 和 `heartbeat-acquire` 外，所有入口都使用该 lease 的 UTF-8 标准输入包络；失效、忙碌、缺步骤或未知结果均失败关闭。

## 固定顺序

按下面的语义顺序执行，并在每个需续租节点重新取得成功回执：

`health → heartbeat-acquire → heartbeat-renew → publish-pending（预检）→ Chrome/Edge 采集 → collect → 受控分析 → scan → publish（由 heartbeat-finish 的机器两阶段处理）→ heartbeat-finish`

`publish-pending` 的预检不是发送成功，也不是周期完成。`heartbeat-finish` 是唯一完成入口：它处理最终受控投递、聚合冻结账号集合并产生最终通知决定。无论本轮在哪一步失败，都先以同轮可用 lease 执行收口；不能以 `NO_REPLY` 或普通聊天文本代替机器完成回执。

每个账号的主帖阶段、回复阶段、每五个永久链接详情核验之后以及 finish 前均需要续租。不得把 lease 累积延长、换用旧 lease 或在失败后并行补跑。

## 浏览器路径

| 条件 | 唯一允许动作 | 不能做的事 |
| --- | --- | --- |
| Chrome 可用 | 用 `dmdmwshr` Chrome 的扩展新建本轮自己的 X 标签，验证登录后的双流结构，完成后关闭该标签。 | 不触碰原有标签，不导出浏览器状态。 |
| 首次 Chrome 句柄为 `browser_not_running` | 仅一次无 URL 的 `start_managed_browser.ps1 -Browser chrome`，等待 8 秒，仅重取一次 Chrome 句柄。 | 不附加 URL、配置、调试端口或额外启动参数；其他 Chrome 错误不得重启。 |
| Chrome 最终为 `browser_not_running`、`extension_unavailable` 或 `login_unavailable` | 仅先以本轮 lease 调用 `chrome-fallback-authorize`；机器明确授权后才可进入 Edge，一次路径。若 Edge 也为 `browser_not_running`，仅一次无 URL 的 Edge 启动/8 秒/重取。 | 不因结构歧义、水位未到、风控、V2 校验或已采集候选改用 Edge。 |
| Edge 不可用或其他不可备用错误 | 用 `browser-failure` 登记终态，再由 `heartbeat-finish` 失败关闭。 | 不再重试、不开第三种浏览器、不生成平行提醒。 |

Chrome 与 Edge 都只能操作本轮新建的标签。禁止 Playwright、Codex 内置浏览器、无头浏览器、第三种浏览器、专用 profile、密码/验证码处理，以及 Cookie、令牌、存储、HTML、截图和 HAR 的读取或保存。

## 采集、V3 分析和直接父帖

- 扩展只产生完整 `XCollectedTimelineV2` 可见事实；它保持不变。`collect` 只接受 UTF-8 标准输入，不能由参数、文件、网页正文或旁路浏览器结果替代。
- 先到达动态双流可信水位，再按解析 UTC 与状态数值从旧到新交给 `scan`。分析产物必须是 `XMonitorScanInputV3`，不得以 V2 输入降级或绕过新判断；也不得写入目标账号、当前状态 ID、历史正文或固定顺序。
- `reset_analysis` 只回答更窄的 Codex 额度重置问题；`AiRelevanceV1` 独立回答外部回复及其已验证直接父帖是否属于广义 AI 话题。它固定使用 `ai_related=true`、`ai_related=false` 或 `ai_related=null`/未评估三态。`reset_analysis.related=true` 必须同时为 `ai_related=true`，但 AI 相关不等于与额度重置直接相关。
- 评估回复与父帖的合并语境，而不是孤立匹配回复文字。广义 AI 主题包括模型、推理、训练、代理、生成式内容、AI 编程/开发者工具、部署、评测、安全、政策与生态；仅仅没有提到额度重置不能判为非 AI。
- 外部回复的普通投递资格严格为：`ai_related=true`、`latest_search_unique_adjacent_parent` 或 `latest_search_permalink_unique_parent`，以及完整唯一直接父帖结构。不得把 `reset_analysis.related` 当作投递门槛。
- 准备普通投递的 `ai_related=true` 回复，其直接父帖必须是回复对象自身的顶层可见作者正文；投递正文必须单列父作者、父帖可见原文、父帖中文翻译和规范永久链接。通知同时带额度区块；当 `reset_analysis.related=false` 时，该区块明确写为“与 Codex 额度重置：无直接关联”。对这类可投递回复，媒体占位、引用帖、祖先帖、空正文、非相邻父项或任一 raw/enriched 不一致都是 `ai_reply_parent_structure_required` 级别的失败关闭。父帖稳定身份可信但正文缺失或仅媒体、因而无法完成 AI 判断的回复必须保持 `ai_related=null` 并进入已处理/已抑制路径，不能伪造父正文或创建普通投递。
- `ai_related=false` 的非 AI 回复与 `ai_related=null`/未评估回复必须写入已处理/已抑制和不可变分析结论，不创建普通投递、不外送、也不在后续扫描中再次翻译或分析。既有历史事件保持冻结，不能由新规则自动重分类。`visible_reply_marker` 只能用于此类抑制，不能生成 AI 相关回复投递。

## 投递、提醒和静默

- 投递意图经 SQLite 幂等键与受控 cc-connect 一对一直送处理；`transport_accepted`、未知或无回执都不等于送达，且未知状态绝不自动重发。
- 双浏览器失败属于同一连续失败事件：机器账本只在首次创建提醒意图，持续未恢复满 24 小时至多再创建一个，恢复后至多一个恢复提醒。不要自行发送、复制正文或创建第二条失败流。
- 只有 `heartbeat-finish` 机器回执同时证明周期完整、投递确定且 `notification_decision=DONT_NOTIFY` 时，才可静默。其他任何状态都需要脱敏失败结果。
- “每 4 个已完成且零可通知周期”的机器计数以 `notifiable_count=0` 为准，而不是没有新 X 状态。被抑制/已处理的非 AI 或无法判断回复不会重置计数，且机器健康摘要必须报告其忽略数量。第 4、8、12……轮由 SQLite 分配不可复用的摘要序号，生成 `x_heartbeat_no_new_summary`，仅在 `heartbeat-finish` 第二阶段经同一飞书直送；摘要未知不重发，下一组使用新序号。Codex 自动化在完整成功时始终返回 `DONT_NOTIFY`，不得自行推算、补发或另行显示摘要正文。

## 调试与验证

先做只读健康和本地契约验证。业务实现测试只能使用 fixture、mock 或临时 SQLite，不访问真实 X、不使用真实浏览器登录态、也不发送飞书。项目规则和机器回执与本参考冲突时，以更严格的当前项目规则为准。
