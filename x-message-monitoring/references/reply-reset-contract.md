# 回复上下文与额度契约（驱动 2.0.1）

本页只定义新分流周期。旧 with_replies、广义 AI 筛选与 Frozen V1/V2/V3 仅供历史解析，不用于新回复降级。不要重新分类已发送、已抑制或投递未知的记录。

## 一次冻结前的步骤

1. 直接 `page(tab,cycle,"search")`，准确查询 `from:<account> filter:replies`，核验 Latest、登录账号、目标作者，读取至 ID 与解析 UTC 同时命中的回复水位。不要先访问 with_replies。
2. 把完整候选 ID 序列送 `observation-fingerprint`。调用 `permalinkBatch(tab,cycle,fingerprint)`，每次最多两条，逐条核验唯一最小主会话区域和相邻的直接对象；对象可为主帖或别人的回复，不以根帖替代。广告必须有明确 placementTracking 证据才排除；父子间若被广告打断仍失败。引用、推荐、重复、身份/时间矛盾和应有正文不完整均失败。
3. 回复或直接对象有作者自己的截断标记时，驱动只点击该卡唯一展开控件并在同一十五秒预算内重读；最后身份、UTC、预览前缀及完整正文必须一致。后续卡尚未就绪不能伪装成完整链。
4. `let xMonDraft = driver.draftStream(cycle)`（已有变量只赋值）。按快速路径的无回显标准输入提交 `{lease,payload:xMonDraft}` 给 `reply-context-plan`。这是只读核验与去重，不冻结、不写事件或水位。结果为 XMonitorReplyContextPlanV1，返回 new_status_ids、context_items、proof_only_count。
5. 在 CUA 调用 `driver.applyContextPlan(cycle,<机器原样计划>)`，只允许返回的新 ID 补上文。直接对象和回复已足以判断时不再读；不足时 `contextBatch(tab,cycle,[<至多两个新回复ID>])`，每个 ID 每次沿可信父子边补一层。只分析返回的公开文本决定是否继续，足够就 `resolveContext(cycle,id,"sufficient")`。最多补三层。驱动确认上文暂不可取得返回 unavailable；三层用尽仍不足为 depth_limit。不能把结构冲突、缺失成员或读取超时改成语义不足。
6. 所有新项的必要补读结束后，只调用一次 `rawStream(cycle,"reply")`，保留返回对象。此后不得修改上下文、再补读或重新生成 collectedAt。按同一对象依次 collect-stream → analysis-plan → scan-analysis。上文是可见事实的一部分，raw/enriched 指纹和 FrozenXMessageV4 绑定一致。

## 模型提交的分析对象

每个新回复必需 chinese_translation、chinese_summary、full_analysis、reset_analysis、ai_relevance。只有通知候选才必需 reply_parent_chinese_translation；采用更早上文的候选还需 reply_context_chinese_translations，它是 `{<上文状态ID>:<中文翻译>}`，键只能来自该条 reply_context.ancestors。模型不得覆盖采集事实。

ResetAnalysisV2 的共同键为：

```json
{
  "schema_version": "ResetAnalysisV2",
  "related": true,
  "reasoning": "结合当前回复及直接对象说明与 Codex 额度重置的直接关系。",
  "evidence_status_ids": ["当前回复ID", "直接对象ID"],
  "estimate_precision": "no_time",
  "confidence": "high"
}
```

示例中的 ID 是字段位置说明，实际提交必须为机器计划里的十进制真实 ID。evidence_status_ids 必须同时包含当前回复和直接对象，以及所有实际采用的更早上文，最多五项、不重复、不引用未采集来源。

- **相关且未给时间**：共同四键加 estimate_precision=no_time、confidence=low/medium/high。时间、范围和时间锚全部省略。进展、延期、规则解释及“尚未重置”“不会统一重置”等明确否认仍可相关；不要为发送编造范围。
- **相关且可推算时间**：estimate_precision=exact/range_only，加 time_expression、possible_range_beijing、time_anchor_status_id、time_zone_basis；exact 另加 most_likely_beijing，range_only 完全省略它。时间锚必须是 evidence_status_ids 中包含的已采集帖子。把“明天、今晚、六点”等解释绑定到该帖发布时间和真实时区线索；PST/PDT 等歧义明确保留，不改写账号配置。
- **额度无关**：共同四键中 related=false；不带时间/置信度/未评估原因。这可以同时 ai_related=true，例如 AI 产品功能消息但未讨论 Codex 额度重置。
- **无法判断**：共同四键中 related=null，再加 unassessed_reason。只允许 insufficient_context、context_depth_limit、context_unavailable、media_only_not_inspected、parent_media_only_not_inspected。深度/不可取得/媒体原因必须与采集上下文一致，不用来掩盖结构失败。

AiRelevanceV1 保留独立字段。true/false 携带 reasoning，null 携带 unassessed_reason。额度相关必然 AI 相关；AI 相关不决定是否推送。当前回复纯媒体时 reset 和 AI 均为 null，翻译、摘要、完整分析均明确包含“未读取媒体内容”，不虚构媒体原文。父对象只有媒体时无法完整评估额度，省略其中文翻译，不伪造父正文。

额度无关与无法判断均冻结为已处理、已抑制并推进已验证水位，后续不重做分析或补发。通知只来自 related=true、直接对象可读且证据/翻译完整的新回复，包含回复、直接对象的原文、中文翻译、北京时间和规范链接；采用更早上文时单列必要文本和来源。主帖仍用既有 reset_analysis 和通知资格，不切换到 ResetAnalysisV2。

## 异常与完成

- 尚未终止的流发生采集/上下文/翻译分析失败，用 stream-failure（account、stream、stage、failure_code）。collect/scan 已登记失败则不要覆盖。
- 同一流已终止后又有其他阶段失败，或发生投递/回执/标签关闭/收口错误，用 cycle-failure 追加 `{lease,payload:{account,stream,stage,failure_code}}`。stream 为 main/reply/both/cycle；stage 为 browser、identity、search、navigation、surface、target、members、parent、context、watermark、collect、analysis、translation、scan、ledger、publish、receipt、cleanup、finish、budget 之一。错误码采用工具已有的脱敏稳定码，不写异常原文、正文或凭据。
- 全轮保留首要错误并汇总全部影响阶段、已完成部分、最近成功和投递证据。故障变化按类别和影响范围去重，耗时变化不重复提醒。提醒内部保留连续故障起点，变化使用新的兼容投递编号，二十四小时仍只一次；旧未认领提醒可合并，已认领或未知的提醒不改写、不重发。
- 双流成功及本轮投递确定才报告恢复。sync-receipts 只做认证 GET；接口仅有登记/传输接受时不制造送达证据。飞书异常仍在原 Codex 任务报告；下一原 heartbeat 会识别上轮缺少完成回执，不另建重试任务。
- 健康新增额度无关数、无法判断数和具体未评估原因；旧 suppressed_non_ai_reply_count 保持历史原义。四轮摘要仅计完整、零可推送的 scheduled 周期，显示新原因计数；manual_validation 不计数。

## 发布与验收

业务锁检查、编译、全量 unittest、实际 node --test tests/test_desktop_driver.cjs 必须通过。Skill 测试同时运行业务回复策略用例和既有消费者纯解析兼容性，不访问真实 X、不调用发送。业务提交后等原周期结束、锁空闲，备份原库并显式 migrate-ledger，回读新增 reply_reset_context_diagnostics_v1，原数据不重写。

仅原固定任务做一轮真实双流及两次连续复扫，并核验新通知和飞书可见结果；任何缺父对象、结构或送达证据的限制如实报告。记录两条预热回复的页面阶段和批次耗时，以两分钟为目标，不降低核验。
