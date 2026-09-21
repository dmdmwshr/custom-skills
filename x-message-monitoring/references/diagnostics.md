# 按需诊断

先定位实际失败层：模型/字段、采集结构、浏览器控制、进程身份、账本或投递。不要因同一拒绝码不断加补丁或重跑整轮；先检查是否把显示差异错误当成内容变化。
正文差异使用同轮 contentReview 完整上下文由模型判断。需要代码修复时保留同次比较的差异位置、有限片段/码点和身份元数据；不补读失败页或恢复已清正文。
raw_html_rejected 可对同次 kept payload 调用 diagnosePayloadText 一次；它只读当前内存，不重新 draft/raw、发送或读 DOM。其他 probe/readiness/expander 证据直接保留本次回执，不能凭摘要遗漏再执行。
heartbeat_collection_time_invalid 核对真实 preflight、collectedAt 和时钟；elapsed_ms 为负及 WSL Time jumped backwards 是真实时钟线索。已安装代码仅同请求最多一秒 monotonic 等待 UTC 追上，不能改时间、重投或扩大预算。
新进程 proof 缺失按 [受管登记](wsl-migration.md)，不误报为 X 未登录。已返回的业务拒绝与超时/坏回执的控制 unknown 分开处理。
身份证明回执缺失先核对精确prepare调用、完成状态与分页，不反复登记；固定代码不匹配需修正调用流程，不删除身份校验。环境ready在产品暂停/恢复后报文件身份错误时，检查该自动化配置的实际属主/权限；产品可能按进程umask重写为组可写。只修精确目标并验证下一次产品更新仍保持受管权限，不放宽root读取门禁或手写调度内容。
真正 unknown 保留原请求/在途/守卫审计，停止依赖动作；已授权维修继续独立源码工作。恢复须基于新证据，不复制旧 grant、clear unknown 或套用已消费窗口。
新增诊断需真实 driver/adapter/共享接口贯通测试；纯 mock 或离线成功不代表实机通过。原 owner 的有界新诊断不计业务周期，正常收尾后再做针对修复的一次真实验证。
