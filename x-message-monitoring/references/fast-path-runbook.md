# 当前周期步骤（WSL MCP）

具体函数签名、加载代码及状态机以已接受业务仓 CYCLE_WORKFLOW.md 为准；先核对 current，不对未安装版本调用新接口。
同 CUA 内使用受管 runtime、MCP adapter 和 cycle_workflow。一个方法调用对应一个受审浏览器动作或一次 stdin/control 请求，原单页 15 秒、方法 40 秒（含收尾）、整轮 20 分钟保持。

## 顺序

1. 先按当地部署说明确认环境ready，再按 [身份与加载](wsl-migration.md) 建立当前进程证明；健康连接不重启，版本/进程未变则复用静态模块。当前 CUA 首次或客户端改变时 selfTest，61 项 exact_match=true；独立 Node 通过不替代此项。
2. workflow 取得 health、唯一 heartbeat-acquire，保留原 lease 和账号/水位；正式定时用 scheduled，授权人工用 manual_validation。publish-pending 预检通过后才生成草稿，再 sync-receipts。
3. adapter.connect/reuseTab 接回原物理 session/Page，driver 必须使用 adapter.driver，不是底层 runtime.driver。Google 资料、X 登录、被监控作者是三个字段；预期 X 登录读取项目部署绑定，被监控作者来自本轮账号。电脑环境已知正常重建时先用当地受管恢复入口重新建立并验证页面归属；不拿旧句柄当当前页面。
4. 主帖 Latest 查询 from:<account> -filter:replies -filter:retweets。page 按 done 续未完成页及全文；context-plan 后仅新项补 quoteBatch。
5. 回复 Latest 查询 from:<account> filter:replies，不访问 with_replies。完整搜索序列经 observation-fingerprint 后，permalinkBurst 使用同次机器指纹续未完成批次；直接父子关系必须可信。context-plan 后按需要补上文（至多三层）与直接引用（每项至多五个来源）。
6. rawStream 前按 [判断契约](reply-reset-contract.md) 处理 contentReview；模型提交 equivalent/different/uncertain 及具体依据。不同/不确定不能冒充同义，也不统一自动放行。
7. 各流分别 collect-stream → analysis-plan → 模型仅分析 analysis_items → scan-analysis → publish-pending。五项正文输入全用 sendKept；draft/raw 各只生成一次，后续直接使用 kept 原 payload。模型不得重建 collectedAt、水位或哈希。
8. heartbeat-finish 完成两阶段终态，health 核对无锁/finish_pending=0；park、clearKept、control.clear、guard.close 由编排依据实际回执执行，清空动态事实但保留静态见证 FD。正常轮不物理断连、不开新页。

workflow 尚未安装或接口不符时按当前 handoff 维护接入，不自己写替代 runner 或把候选当正式入口。

## 分析与失败

字段构造使用 [无 I/O helper](../scripts/analysis_fields.cjs) 的 aiRelevance/validateAnalyses，避免把 ai_related 写为 related。语义由模型决定；纯媒体记 media_only_not_inspected，不猜图像内容。
主帖/回复独立，单流失败不回滚成功流。未终结流经 stream-failure 收口；已被 collect/scan 终结的不重复登记。后续投递/清理问题用 cycle-failure 保留首错；共同身份/路由失败停止依赖动作。
sendKept 返回 ok 只证明传输，实际业务结果读取本次 kept。outcome_unknown=true 不重投、不补读、不清未知。代码在发送前拒绝的纯字段或本地清理参数错误，可凭仍有效的原对象与真实回执就地更正；不把所有异常升级为unknown，不重复已完成的浏览器或业务动作。维护preparationOnly的clear/close同样需要实际收口证据，不能无参数调用或手填成功。
只有 finish 明确 heartbeat_complete=true、outcome=completed、notification_decision=DONT_NOTIFY 才静默；partial_failed 或缺失终态简短报告实际失败。平台存在不冒充用户已读。
普通轮只保留最终周期 ID、两流结果、投递证据等级和未解决码，不携带正文/lease/长诊断到下一轮。
