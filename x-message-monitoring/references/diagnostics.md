# 按需诊断

先定位实际失败层：模型/字段、采集结构、浏览器控制、进程身份、账本或投递。不要因同一拒绝码不断加补丁或重跑整轮；先检查是否把显示差异错误当成内容变化。

先看已安装 workflow.state().last.recovery、实际提交标记及 adapter 回执，再决定续行：未提交的字段/顺序错误可就地更正；已返回的目标提取失败由 driver 在同页有限刷新重读；仍无法解析的引用记 unresolved 并交给模型评估其余证据，不停止无关消息。not_unique 未提供数量时不猜零个或多个，也不选第一条冒充唯一身份。控制/外发 unknown 只对账，不能以通用 retry 重放。超过单次恢复预算时留待下一正常周期，不反复启动整轮。

quote_context_incomplete 在 collect 发送前发生时，继续 quoteBatch 至 done、复核上下文后再 collect；不是重新发送请求。给用户报告时说明已尝试的恢复、剩余缺口和后续复评，而非仅粘贴拒绝码。诊断和模型结论分别留证。
正文差异使用同轮 contentReview 完整上下文由模型判断。需要代码修复时保留同次比较的差异位置、有限片段/码点和身份元数据；不补读失败页或恢复已清正文。
raw_html_rejected 可对同次 kept payload 调用 diagnosePayloadText 一次；它只读当前内存，不重新 draft/raw、发送或读 DOM。其他 probe/readiness/expander 证据直接保留本次回执，不能凭摘要遗漏再执行。
heartbeat_collection_time_invalid 核对真实 preflight、collectedAt 和时钟；elapsed_ms 为负及 WSL Time jumped backwards 是真实时钟线索。已安装代码仅同请求最多一秒 monotonic 等待 UTC 追上，不能改时间、重投或扩大预算。
新进程 proof 缺失按 [受管登记](wsl-migration.md)，不误报为 X 未登录。已返回的业务拒绝与超时/坏回执的控制 unknown 分开处理。
身份证明回执缺失先核对精确prepare调用、完成状态与分页，不反复登记；固定代码不匹配需修正调用流程，不删除身份校验。环境ready在产品暂停/恢复后报文件身份错误时，检查该自动化配置的实际属主/权限；产品可能按进程umask重写为组可写。只修精确目标并验证下一次产品更新仍保持受管权限，不放宽root读取门禁或手写调度内容。
登记中断的本地pending不等于外发unknown；通过已安装入口加锁只读回查实际grant和控制状态，再决定复用或生成新鲜证明。不要手动删除全局变量来重试。诊断保留终端异常码或受管校验点，不从traceback文件名提取错误、也不把通用身份拒绝直接猜成时钟或登录故障。
真正 unknown 保留原请求/在途/守卫审计，停止依赖动作；已授权维修继续独立源码工作。恢复须基于新证据，不复制旧 grant、clear unknown 或套用已消费窗口。
新增诊断需真实 driver/adapter/共享接口贯通测试；纯 mock 或离线成功不代表实机通过。原 owner 的有界新诊断不计业务周期，正常收尾后再做针对修复的一次真实验证。
diagnoseQuote 专用于含直接引用的来源，不是普通回复/祖先链读取入口。普通回复的 quote_container_missing 不能证明上下文不可读；来源作者应与独立诊断cycle.account相符，但不得因此改业务账号。permalink_target_status_not_unique 可表示零个或多个有效身份，若回执未提供匹配数则不得猜测；引用预览作者与外层作者分开报告。诊断失败后 guard 停止，未执行的第二目标不算已观察；新诊断须有明确范围，不能为绕过停止标记盲重读。
