# 两流模型判断与额度契约

模型只处理同轮机器计划中的新项、待定复评项及待复核观察，不从历史正文恢复事实。执行顺序见 [运行步骤](fast-path-runbook.md)，投递见 [回执与完成](heartbeat-and-delivery.md)。

## 事实与内容复核

主帖使用非回复/非转发 Latest 搜索；回复直接 Latest 搜索及永久链接确认唯一相邻父子链。ID、作者、UTC、直接父关系、完整性均可信才进入分析。水位以 ID＋UTC instant 同时匹配，不能跳过未完成项。
直接对象/上文/引用各自保留作者、时间、页面正文及链接；引用文字不混入外层正文，引用作者的承诺不变成外层作者承诺。上下文最多三层，够用即停；每项最多五个直接引用来源，不递归引用。
浏览器展开、导航和已知读取失败的恢复由固定驱动处理；模型不重写 selector。rawStream 前读取 contentReview，同轮两份完整实际观察可按语义接受翻译、换行、链接卡显示差异，不要求逐字相同。核对数值、否定、对象、范围、时限、承诺和引用归属，逐项 resolveContentReview 为 equivalent/different/uncertain 并给具体依据；不批量固定答案、不删除字符凑相同。
页面文字原样保存，译文不冒充独立原文。未知非关键链接跳转路径不升级为正文错误，但也不声称目的地已经核验。
XReplyContextV2、XQuoteContextV1 与 FrozenXMessageV5 的原事实指纹/历史哈希保持。quote_deleted/quote_unavailable 必须有实际证据，不能将超时、身份冲突或缺卡伪装 unavailable。不可访问引用不列入采用证据；其余可信正文足够时可独立判断。

## 模型提交

先判断话题相关性，再判断具体承诺与时间，不能因日期指代模糊就直接抑制整条消息。结合可信直接对话确定是在回应 Codex 额度重置请求、但“周二”等表达可能指发布而非重置时，可提交 related=true、no_time、low/medium，具体说明歧义；不要凭作者身份把其他产品重置强行归为 Codex。可信度针对解读，并非账户已重置概率，不能伪造百分比。不同产品或完全缺乏额度话题证据仍可无关/待定。

引用 resolution=unresolved 表示采集缺口，不是帖子删除，也不是可采用证据。先检查 warning 与已有可信正文；独立正文足够则继续判断，不足则 related=null、unassessed_reason=quote_read_incomplete。禁止将未核验引用 ID 写入 evidence_status_ids。此扩展只在项目已安装支持版本后使用。

每条新项需要 chinese_translation、chinese_summary、full_analysis、reset_analysis。回复另需 ai_relevance；通知候选需要 reply_parent_chinese_translation。采用更早上文/引用的通知候选需要 reply_context_chinese_translations / quote_context_chinese_translations，格式为 {已核验状态ID:中文翻译}。
模型依据只来自本轮可信上下文，不能覆盖事实或发明媒体内容。

ResetAnalysisV3 公共字段：
- schema_version="ResetAnalysisV3"
- subject_product=codex/other/unknown
- related=true/false/null
- reasoning：本轮具体判断依据
- evidence_status_ids：最多十个不重复的已核验ID。主帖至少自己；回复至少自己和直接对象；采用引用时同时列它及引用它的外层来源。

分支：
- 相关且无时间：related=true、subject_product=codex、estimate_precision=no_time、confidence=low/medium/high；完全省略时间表达、范围与锚。进展、延期、规则和明确否认都可相关。
- 相关且可推算：estimate_precision=exact/range_only，加 time_expression、possible_range_beijing、time_anchor_status_id、time_zone_basis；exact 另需 most_likely_beijing。时间锚必须确实含被解释表达，根据该帖发布时间及真实时区线索换算北京时间。
- 无关：related=false，仅公共五键；不带置信度、时间或未评估原因。只讨论 Grok/其他产品重置归 other，AI 功能发布也不等于 Codex 额度重置。
- 无法判断：related=null，增加 unassessed_reason=insufficient_context/context_depth_limit/context_unavailable/media_only_not_inspected/parent_media_only_not_inspected/quote_unavailable/quote_media_only_not_inspected/quote_read_incomplete；与实际事实匹配，主帖不使用父帖/上文深度理由。

AiRelevanceV1 只用于回复，键是 ai_related，**不是 related**：
- true/false：{schema_version:"AiRelevanceV1",ai_related:false,reasoning:"本轮模型依据"}
- null：{schema_version:"AiRelevanceV1",ai_related:null,unassessed_reason:"context_unavailable"}

示例仅表示形态，不是默认判断。额度相关必然 AI 相关，反向不成立。
使用 [纯内存字段构造器](../scripts/analysis_fields.cjs) 的 aiRelevance(判断,依据) 及 validateAnalyses(analyses,stream)；无 I/O，不替模型决定。它在尚未发送前发现拼写问题可就地更正；已经提交后不能据此重投。
纯媒体且未读取：reset related=null，回复 AI 也 null；翻译/摘要/分析注明“未读取媒体内容”。图文中可信作者正文可读不算纯媒体，通知不能依赖未读取的媒体。

## 处理与通知

沿 codex_reset_only_v2（原 codex_reset_all_streams_quote_context_v1 数据兼容）分别入账。新版中，缺上下文或引用读取不全的待定项不写永久抑制记录，所属流水位保留，下一正常周期重新采集该区间；其他已处理消息继续去重，独立消息可以通知。纯媒体未读取仍保持明确披露。旧冻结记录不自动改写；用户明确要求干净初始化时，经备份由项目维护入口清除执行历史和去重，再按指定水位重新采集，不从旧正文重放。
通知分列当前消息、直接对象、采用上文/引用的页面正文、翻译、北京时间、规范链接与归属。无时间写明“未提供可推算时间”；公开帖子不能证明本账户已重置。
语义弹性不覆盖身份、UTC、完整性、防重发或真实未知。真实失败按原流/周期终态收口，不恢复已清事实。
