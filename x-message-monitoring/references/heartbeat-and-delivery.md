# 分流 heartbeat 与投递边界

## 不变量

- SQLite 是正式账本；每轮 health、acquire 冻结账号和两个水位，不使用聊天历史或硬编码 ID。
- 主帖与回复独立事务。每条流必须完整验证至自身 ID + UTC instant 可信锚；只回滚失败流。共同预检不通过，两流都不开工。运行中路由/身份故障停止后续工作，不撤销已经合法提交的结果。
- 单个 20 分钟 lease，唯一 Desktop 固定会话和原 heartbeat；不建第二任务、cc-connect cron、Windows 任务或发送者。cc-connect outbound-only；入站 silent_drop。
- 新入口 collect-stream / scan-stream 不接受假空 counterpart。旧双流 collect / scan 保留兼容，但不能混入分流周期绕过新门禁。
- 历史记录不回填为新周期证据。已有 V2/V3 冻结内容、哈希、已抑制决定、未知投递永久保留，不重分类、不重发。

## 固定驱动与浏览器

driver 1.1.0 为业务项目固定源。主帖固定核验非回复/非转发 Latest 搜索，不把主页会话展示重排成时间线。正常轮次复用 CUA 会话中的普通 lexical 绑定；版本变化才加载，不使用 globalThis、内部 API、独立控制进程或 Playwright CLI。允许受控扩展标签的 tab.playwright 和 dom_cua。

Chrome dmdmwshr 首选，仅三种实测失败 browser_not_running / extension_unavailable / login_unavailable 可在机器授权后降级 Edge。首次 Chrome 未运行只启动/重取一次；Edge 也只允许一次启动/连接；规定启动等待 8 秒保留。页面探测没有额外固定睡眠：15 秒页预算、两永久链接/调用、40 秒调用上限。结构、水位、父帖歧义不得换浏览器。

只操作本轮创建的标签。不得读取、导出、填写密码、Cookie、令牌、存储、验证码；不保存 HTML、HAR、截图或采集正文文件，不碰既有标签。唯一直接父帖从主会话最小容器的完整相邻链取得；不能从整页平铺卡挑父。头像不算媒体；有可见文字的图文父帖允许，纯媒体不虚构原文。独立回复对象先核对后派生，不能先赋值再当验证。

## 分析与普通通知

模型仅处理 analysis-plan 返回的新内容。程序负责 raw V2 → enriched V3 字段与上下文转换和可见事实指纹。历史/水位证明项在内存使用明确“仅核验”占位，机器保证它们不能生成新事件；不回写原分析。缺少任一真正新条目的分析，scan-analysis 拒绝。

主帖资格不变。回复和可信直接父帖合并判定广义 AI：模型、训练推理、AI 编程、智能体、AI 产品/API、算力、安全治理等；普通科技不自动算 AI。AiRelevanceV1 为 true/false/null；reset_analysis 独立回答 Codex 额度重置。额度相关必然 AI 相关，反向不成立。

AI 相关回复需要 latest_search_unique_adjacent_parent 或 latest_search_permalink_unique_parent 证据及父作者、父原文、父中文翻译、父链接；通知同时给回复原文/翻译/北京时间/链接及额度说明。无关额度明确“与 Codex 额度重置无直接关系”。AI 候选缺唯一直接父结构或父原文不能发送；仅媒体且无法判断保持 null、抑制入账，不编造。非 AI 和未评估回复仍去重、抑制并推进可信水位。

相对时间以源发布时间和作者时区线索推算北京时间 UTC+8，先翻译再解释依据、最可能时间、范围和置信度；证据不足不给精确时刻。长帖保留关键原文、完整中文概述与链接，完整分析仅保存在事件账本。

## 投递与完成

- 唯一原 direct_feishu 通道；scan 只创建幂等意图。开始时和 finish 内有限 GET 对账，不能无限轮询。
- 已登记、transport_accepted、可见送达已核验是不同证据等级。状态接口没有可信可见凭据时，哪怕返回 delivered 字样也不能升格；保持未核实。
- 旧 intent_registered 对账只追加可靠证据，绝不重新提交。历史未知单列；本轮关联意图未知则 REPORT、重置健康连续计数。
- heartbeat-finish 是唯一完成/释放入口；分流协议必须两阶段。XMonitorHeartbeatFinalizeV2：全部双流成功且最终投递处理确定才 completed；部分扫描成功为 partial_failed；无可信完整流/共同预检故障为 failed_closed。只有 heartbeat_complete=true、completed、notification_decision=DONT_NOTIFY 可静默。
- 首次、持续 24 小时、恢复提醒由 SQLite 去重，并显示受影响流；登录提醒不再叠加通用提醒。中枢不可用时保留安全意图，恢复合并一次，不补刷失败历史。
- 每 4 次完整且零可推送的**定时**周期生成唯一 x_heartbeat_no_new_summary。非 AI 回复不打断；手动不累计；失败、部分成功、锁冲突、本轮投递未知或任何新可推送内容清零。未知摘要不重发，下一组用新单调序号。只有最终机器允许时 Codex 输出 DONT_NOTIFY。

## 验证

必须运行实际业务驱动的合成 DOM/模拟标签测试、临时数据库分流事务/回执/四轮测试，而不只检查 Skill 包含某句话。离线不得访问 X 或发送飞书。保持运行态暂停直到原固定任务完成真实验收；主帖通过但回复失败，可按已授权方案明确以“回复降级”恢复同一 heartbeat。
