# 当前周期步骤（受管 Playwright）

具体函数签名、加载代码及状态机以已接受业务仓 CYCLE_WORKFLOW.md 为准；先核对 current，不对未安装版本调用新接口。
在普通 JavaScript 执行器中使用受管 runtime、Playwright adapter 和 cycle_workflow；执行器本身不是浏览器控制工具。每次显式推进一个浏览器方法或业务 stdin 请求，单页和单方法仍有有限超时，但长期补采不以整轮总耗时截断。当前已安装 workflow 会在活跃步骤间按需续租；模型分析分批处理，批次间调用 `keepAlive()`，不要静默超过租约回收窗口。短租约用于回收失活进程，不是限制正常任务总时长；过期与 unknown 不自动重试。

系统长期未运行时，沿各流原水位补齐，不把固定天数、少量页数或小条数上限当成成功条件。时间基线诊断与正式采集的资源上限必须一致，主帖/回复分别计数；容量与调用签名以已安装项目实现为准。到资源上限而未到水位时报告覆盖不足，不能推进水位或截断正文。中断后重读未提交区间，已入账去重仍保留，不从旧日志恢复正文。用户要求历史起点时，先取得真实边界证据；普通 bootstrap 只取最新项，不等于历史初始化。

## 顺序

1. 先按 [身份与加载](wsl-migration.md) 核对实际任务、项目登记及唯一执行租约；健康连接不重启，版本/进程未变则复用静态模块。当前进程首次或客户端改变时 selfTest，61 项 exact_match=true；独立 Node 通过不替代此项。
2. workflow 取得 health、唯一 heartbeat-acquire，保留原 lease 和账号/水位；正式定时用 scheduled，授权人工用 manual_validation。publish-pending 预检通过后才生成草稿，再 sync-receipts。
3. 受管 connectPlaywright/reuseTab 按当地唯一契约接回本项目登记连接/原生组/页面，driver 必须使用 adapter.driver，不是底层 runtime.driver。Google 资料、X 登录、被监控作者是三个字段；预期 X 登录读取项目部署绑定，被监控作者来自本轮账号。电脑环境已知正常重建时先用当地受管恢复入口重新建立并验证页面归属；不拿旧句柄当当前页面。
4. 主帖 Latest 查询 from:<account> -filter:replies -filter:retweets。page 按 done 续未完成页及全文；context-plan 后仅新项补 quoteBatch。
5. 回复 Latest 查询 from:<account> filter:replies，不访问 with_replies。完整搜索序列经 observation-fingerprint 后，permalinkBurst 使用同次机器指纹续未完成批次；直接父子关系必须可信。context-plan 后按需要补上文（至多三层）。resolveContext(sufficient)只结算回复上文，不代替引用核验；自身、直接父文及已采用上文的直接引用都须经 quoteBatch 补齐（每项至多五个来源）。
6. 两流 collect/rawStream 前分别确认 quoteBatch 返回 done=true；没有待引用项时它是无浏览器读取的纯完成检查。之后按 [判断契约](reply-reset-contract.md) 处理 contentReview；模型提交 equivalent/different/uncertain 及具体依据。不同/不确定不能冒充同义，也不统一自动放行。引用未补齐的本地拒绝不能算模型额度漏判。
7. 已安装历史关联迁移时，两流分别 collect → archive，之后统一 correlation-plan → 分页 history-query → 模型 correlation-commit；函数及信封字段读项目 HISTORY_CORRELATION.md。完整观察持久化后可以推进采集水位，内容待定单独复评，不反复扫描已完整区间。未启用迁移的旧部署才用 analysis-plan/scan-analysis，不能两条路径各发一次。sendKept 保留机器原载荷，模型不得重建 collectedAt、水位或哈希；未提交观察不能从日志恢复。
8. heartbeat-finish 完成两阶段终态，health 核对无锁/finish_pending=0；park、clearKept、control.clear、guard.close 由编排依据实际回执执行，清空动态事实并释放进程租约。正常轮不物理断连、不开新页。

workflow 尚未安装或接口不符时按当前 handoff 维护接入，不自己写替代 runner 或把候选当正式入口。

## 历史补缺与事件关联

普通关联窗口由项目配置；当前项目采用前后七天，不限制停机补采。historyWindow(initial) 固定首次回看起止，不重置水位；incremental 沿原水位分段保存，所有双流范围完整才 historySettle。按 completed_streams 只续未完成流；已有完整观察可以带原指纹/观察时间复用覆盖，不能冒充新鲜页面。网站搜索日期条件可能不可靠，以实际发布时间边界验证，不能将数量上限或返回空页自动当作完成。

计划固定证据版本与指纹，分页全部读完后提交，继续至剩余项完成或有依据地 deferred。回复/引用及未结束事件可跨普通窗口；无新证据/规则变化复用已有判断。先按语义区分事件，再分别判断相关性、时间确定性、可信度，不能靠同词或时间接近拼成重置承诺。旧通知映射事件基线，不因新模板重发；实质变更才增加通知版本。已证明过期的遗漏通过 history-summary 合并；日期未知不是已过期。无合格遗漏时不制造验收消息。

增量复扫除通知数外，也检查历史版本数和待分析批次。已验证的空引用列表与省略空字段属于序列化差异，不应触发整窗复评；真实正文、关系、引用变化仍保留新版本。发生异常重排时先核对具体版本差异，不清队列或改旧判断；修复后经固定版本计划正常收口，并实测无新证据时不再重排。复用判断须核对当前计划全部采用证据的指纹，纯构造函数显式传当前计划，避免持久执行器闭包误持旧批次；提交后先读本次回执，再推进下一步。

## 分析与失败

字段构造使用 [无 I/O helper](../scripts/analysis_fields.cjs) 的 aiRelevance/validateAnalyses，避免把 ai_related 写为 related。语义由模型决定；纯媒体记 media_only_not_inspected，不猜图像内容。
可选祖先或引用所属页的已知只读超时，已验证项目版本会保留局部unavailable/unresolved，继续归档已核验根帖和直接父帖。缺口只限制依赖它的判断，不宣称帖子已删除；真正控制unknown仍由适配器单独锁存。修复前遇到整批失败不能手清driver状态；正常收尾、安装修复后重新观察尚未提交段，已经持久的区段按原指纹复用。
主帖/回复独立，单流失败不回滚成功流。未终结流经 stream-failure 收口；已被 collect/scan 终结的不重复登记。后续投递/清理问题用 cycle-failure 保留首错；共同身份/路由失败停止依赖动作。
sendKept 返回 ok 只证明传输，实际业务结果读取本次 kept。outcome_unknown=true 不重投、不补读、不清未知。代码在发送前拒绝的纯字段或本地清理参数错误，可凭仍有效的原对象与真实回执就地更正；不把所有异常升级为unknown，不重复已完成的浏览器或业务动作。维护preparationOnly的clear/close同样需要实际收口证据，不能无参数调用或手填成功。
只有 finish 明确 heartbeat_complete=true、outcome=completed、notification_decision=DONT_NOTIFY 才静默；partial_failed 或缺失终态简短报告实际失败。平台存在不冒充用户已读。
普通轮只保留最终周期 ID、两流结果、投递证据等级和未解决码，不携带正文/lease/长诊断到下一轮。

## 清水位后重新部署

用户明确要求清理水位时，先核对实际清理范围并保留新鲜配对备份；仅清水位默认保留历史事实、已发送去重和未知回执。首次七天窗口必须按本次重置代次区分，不能复用旧初始化的完成游标；窗口完成再建立正常增量水位。新会话、cc-connect所有者、共享Playwright登记与原heartbeat目标须一致，保持单执行者。程序切换窗口暂停调度；受管版本已安装、身份绑定一致且无在途未知后，可由原定时任务串行续完初始化与验收，未完成项如实记录，不把初始化进度当永久暂停理由。

用户可见事件类型、流名和状态使用中文，内部协议枚举保持不变；不要将 x_reply_detected、main/reply、partial_failed 等原始值直接当作显示标签。诊断确需代码时放在中文解释之后。重部署不等于已验收，待复评项也不等于采集失败，分别给出证据。


历史留存由业务项目政策决定，不把关联窗当无条件删除线。本项目已授权七天：正常finish的原写入者执行清理，回读history_retention；到期且已完成、无依赖的观察/计划/窗口才删除。保留水位及发送去重、未提交计划、待复评、未解决冲突、未结束事件、未结算补采和被保留来源的跨窗直接关系。冻结通知/回执及Codex任务历史不属于该清理接口。清理失败仅回滚清理、记录问题，下轮重试，不撤销已知业务完成或重复发送，也不另开常驻清理者。
