# 两流上下文、引用与额度契约（驱动 3.0.14）

驱动 3.0.14 仅在已授权维护的 DiagnosticCycle 显式调用 diagnoseQuote(...,{includeMismatchExcerpt:true}) 时，从本次已观察文字返回首个差异处的预览最多 48 字符、来源最多 64 字符。它是有界公开事实工具上下文，禁止写普通运行日志、正文文件或账本；普通 quoteBatch 和默认诊断仍无正文。身份/正文严格失败不变，不追加 UI 读取或失败重试，诊断不可冻结。

当前任务使用用户指定的 Luna/max；周期完整收口后按快速路径进行同任务原生上下文重整，只保留轻量结果。驱动 3.0.13 的 quote_match_evidence 仅为已有文字的无正文差异诊断，失败不放宽、不补读。历史传递流程仅在维护需要时查阅 legacy-fact-transfer.md，当前标准输入直传优先。

3.0.12 无链接引用的定位点来自当前唯一引用卡的几何与命中测试，排除媒体、链接、按钮等交互目标，只用于受支持的 Tab.click([x,y])；不进入引用事实或指纹。点击后的作者、状态 ID、UTC、完整正文、预览一致性继续由原校验确定，位置可信不等于引用事实已验证。

3.0.11 只增加无正文引用/分页失败定位和不能冻结的维护诊断周期，事实与额度契约不变。诊断不替代主帖、回复或引用成功证据，不通过排序消除搜索歧义；实际目标等待失败后仍停止该流。

3.0.10 的 packFactsGzip 使用已核验公开 node:zlib 的 gzipSync，在内存压缩原字典及 diffFacts 草稿差异，具体步骤见 fast-path-runbook 首节。context-plan、collect-stream、analysis-plan 及 scan-analysis.collected 接受 XMonitorFactTransferV1 后先校验大小、压缩完整性与原校验码，再还原原流对象；不会改变原 V2/V3、身份、水位、引用或冻结语义，也不添加正文文件、进程或浏览器桥接。差异不允许改写草稿中已核验的核心事实及直接父对象。原生流对象及旧字典仍兼容。

3.0.8 允许已验证唯一直接父子关系后的父帖全文补读：仅当截断父帖控件需移位时，从该父帖已知永久链接读取其自身正文，身份、UTC、标志和正文前缀必须一致，引用事实保持独立；原父子链不变，不选择新父对象。每次最多两个永久链接，permalinkBatch 的 pending_parent 继续同批至 done=true；contextBatch 的 pending_status_ids 只续未完成 ID，不重复处理已返回 items 中的 ID。每页 15 秒、每次工具 40 秒；未完整时不能冻结，失败之后不重试。

本页定义新分流周期：主帖和回复统一只推送 Codex 额度重置相关的新内容。旧 ResetAnalysisV1/V2、XReplyContextV1 与 Frozen V1–V4 保留历史原义，不重新分类已发送、已抑制或投递未知记录。AI 功能发布、其他产品额度重置等无关内容仍入账、去重和推进水位。

## 一次冻结前的步骤

1. 主帖按快速路径完成 Latest 搜索与必要全文核验。回复直接 `page(tab,cycle,"search")`，准确查询 `from:<account> filter:replies`，核验 Latest、登录账号、目标作者，读取至 ID 与 UTC instant 同时命中的水位，不访问 with_replies。
2. 完整冻结回复候选 ID 序列在同一 CUA 宿主经固定标准输入客户端送 observation-fingerprint，直接使用本轮机器回执的 fingerprint，再调用 permalinkBatch，每次最多两条。唯一最小主会话区域的完整顶层链中，直接对象与回复必须相邻；对象可为主帖或别人的回复。外层主帖、回复、对象及上文均允许带引用卡，内嵌引用节点不能充当父或子。推荐、重复、身份/时间冲突、父子不相邻或正文不完整仍失败；明确广告也不能替代中间缺失成员。
3. 作者自己的截断标记只由固定驱动点击该卡唯一展开控件，在同一十五秒预算内重读。身份、UTC、预览前缀及完整性必须一致；外层作者正文与引用文字分别保存，不能取第一段或拼接。
4. `draftStream(cycle,"main"|"reply")` 产生草稿；按快速路径客户端把宿主原内存对象 `{lease,payload:草稿}` 直接提交给统一 context-plan。它只读验证、去重，返回 XMonitorContextPlanV1 的 stream、new_status_ids、context_items、proof_only_count、max_ancestors=3、max_quotes=5。旧 reply-context-plan 仍为回复兼容别名。调用 `applyContextPlan(cycle,机器原样计划)`；历史锚点只核对核心事实，不补读、不分析。
5. 新回复的直接对象和当前正文不足时，`contextBatch(tab,cycle,[至多两个新回复ID])` 每个 ID 沿可信父子边补一层，最多三层。足够时 `resolveContext(cycle,id,"sufficient")`；可信不可取得为 unavailable，三层用尽仍不足为 depth_limit。上文为 XReplyContextV2。不要把结构冲突或超时改成语义不足。
6. 两流均执行 `quoteBatch(tab,cycle,"main"|"reply")` 至 ok=true、done=true；每次只补一个来源，最多涉及来源拥有帖和引用原帖两个永久链接，同一十五秒共享预算，工具上限四十秒。每个当前消息/直接对象/上文只补其直接一层引用，最多五个引用归属项；不追踪引用中的引用。后续补上文若增加引用，冻结前再完成 quoteBatch。同轮相同规范来源复用核验结果，依旧校验当前引用归属、预览、身份和时间。
7. 引用卡不提供链接时，只点击当前唯一外层对象内已核验的引用块，读取实际跳转地址，不猜状态 ID。来源正文截断时只展开已核验原帖自己的唯一控件。明确删除/不可用按可见证据记录；没有这种正面证据的缺卡、超时、截断和身份冲突仍失败。
8. 补读结束后每流仅一次 `rawStream(cycle,stream)`。后续不得修改事实或重建 collectedAt，原对象依次 collect-stream → analysis-plan → scan-analysis。XQuoteContextV1 与 XReplyContextV2 均参与采集指纹、分析绑定和 FrozenXMessageV5 完整性校验。

## 引用事实

XQuoteContextV1 为 `{schema_version:"XQuoteContextV1",quotes:[...]}`。每项记录 quoted_by_status_id、quoted_by_permalink、resolution、observed_permalink。verified 项携带 status：引用原帖自己的 status_id、author_handle、created_at、permanent_url、original_text、status_kind、is_media_only、is_quote、is_retweet、is_promoted、text_complete。引用作者与外层作者分开，链接、时间、完整性均须真实核验。

unavailable 项只携带 unavailable_reason=quote_deleted/quote_unavailable；observed_permalink 只有实际可确认时才填写，否则为 null。不伪造正文或时间，不把无法访问的帖子列入采用的证据 ID。引用不可访问时，当前可信正文已足够可以独立判断相关；否则抑制为无法判断。结构错误不能使用 unavailable 降级。

## 模型提交的分析对象

两流的新项必需 chinese_translation、chinese_summary、full_analysis、reset_analysis。回复另需 ai_relevance；通知候选必需 reply_parent_chinese_translation。采用更早上文的通知候选需 reply_context_chinese_translations，采用引用的通知候选需 quote_context_chinese_translations，均为 `{已核验状态ID:中文翻译}`，键不得来自未核验来源。模型不能覆盖可见事实。

ResetAnalysisV3 的完整无时间示例：

```json
{
  "schema_version": "ResetAnalysisV3",
  "subject_product": "codex",
  "related": true,
  "reasoning": "当前消息结合独立标注的可信来源，明确讨论 Codex 额度重置进展。",
  "evidence_status_ids": ["当前消息ID", "直接对象ID", "采用的引用ID"],
  "estimate_precision": "no_time",
  "confidence": "high"
}
```

示例 ID 仅表示字段位置，实际为机器计划中的十进制 ID。主帖证据至少含自己；回复至少含自己与直接对象；采用的上文和引用全部列入，最多十项且不重复。引用证据必须同时包含至少一个可见引用它的外层来源，不能凭单独引用推断作者承诺。subject_product 只允许 codex/other/unknown；related=true 必须为 codex，仅讨论 Grok 或其他产品重置属于无关。

- **相关无时间**：上述共同五键加 estimate_precision=no_time、confidence=low/medium/high，完全省略时间表达、范围和时间锚。进展、延期、规则、“尚未重置”“不会统一重置”等明确否认也可相关。
- **相关可推算时间**：estimate_precision=exact/range_only，加 time_expression、possible_range_beijing、time_anchor_status_id、time_zone_basis；exact 另需 most_likely_beijing，range_only 省略它。时间锚必须是采用的已核验来源，确实包含所解释的时间表达；依据该帖发布时间及真实时区线索换算北京时间，不把外层“谢谢”当时间来源，不改账号时区配置。
- **无关**：共同五键中 related=false，不带时间、置信度或未评估原因。AI 功能发布可以 ai_related=true，同时额度无关。明确不是 Codex 的产品归 other。
- **无法判断**：共同五键中 related=null，另加 unassessed_reason：insufficient_context、context_depth_limit、context_unavailable、media_only_not_inspected、parent_media_only_not_inspected、quote_unavailable、quote_media_only_not_inspected。原因必须与实际采集事实相符；主帖不使用父对象或上文深度原因。

AiRelevanceV1 只适用于回复，true/false 带 reasoning，null 带 unassessed_reason。额度相关必然 AI 相关，反向不成立。当前消息纯媒体时 reset 为 null；回复 AI 也为 null，翻译、摘要及完整分析均包含“未读取媒体内容”。图文中可信作者正文可读时不属于纯媒体；通知不能依赖未读取的引用媒体或父对象媒体。

账本创建投递前执行 codex_reset_only_v2 两流筛选，按流、策略及原因登记 notify/unrelated/unassessed。无关与无法判断冻结为已处理/已抑制并推进可信水位；后续不重分类、不补发。通知分列当前消息、直接对象和采用的上文/引用的原文、翻译、北京时间、规范链接，标明引用归属；无时间依据明确写“未提供可推算时间”。公开帖子不能证明本账户已经重置。

## 异常与完成

- 未终止的流发生采集/上下文/翻译分析失败，用 stream-failure（account、stream、stage、failure_code）；collect/scan 已登记失败则不覆盖。主帖与回复独立提交。
- 已终止流又有其他阶段失败，或投递/回执/标签关闭/收口失败，用 cycle-failure 追加。stream 为 main/reply/both/cycle；stage 采用 browser、identity、search、navigation、surface、target、members、parent、context、watermark、collect、analysis、translation、scan、ledger、publish、receipt、cleanup、finish、budget。仅用脱敏稳定错误码，不写异常原文、正文或凭据。
- 全轮保留首错并汇总全部阶段、完成部分、最近成功和投递证据。故障按首次、类别/范围实质变化、满二十四小时一次、完整恢复一次去重；已认领或未知提醒不改写、不重发。
- 双流成功及本轮投递确定才报告恢复。sync-receipts 仅认证 GET，不因登记/传输接受制造送达证据；下一原 heartbeat 识别上轮缺失完成回执，不建重试任务。
- 新策略生效后独立重新累计四轮完整、零可推送的 scheduled 周期，摘要分别列主帖和回复无关/无法判断数量。manual_validation 不计数；可推送内容、失败和本轮投递未知清零。旧非 AI 数量及旧摘要状态保留原义。

## 发布与验收

依赖锁检查、Python 编译和全量 unittest、实际 node --test tests/test_desktop_driver.cjs 与 Skill 检查必须通过。包含新引用/两流筛选、迁移保留历史、两次复扫零重复、4/8/12 轮摘要和现有消费者纯解析；离线测试不访问 X、不发送飞书。

业务提交后等生产锁空闲，备份原库并显式 migrate-ledger，回读 codex_reset_all_streams_quote_context_v1 和历史行未改写。发布 Skill 后仅在原固定任务完成一轮真实双流及两次连续复扫；保持原自动化和发送路由。

有本轮新通知时按授权在本轮自有飞书网页标签只读核验唯一原目标的时间和内容，不要求先取得 transport_accepted；无法唯一确认目标则停止，不探索其他聊天、不通过 UI 发送或重发、不将人工观察写成机器送达。缺乏可见证据如实报告。
