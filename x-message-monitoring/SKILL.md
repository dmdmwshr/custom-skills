---
name: x-message-monitoring
description: 在唯一 Desktop 固定会话中安全执行 X 消息监控 heartbeat，包括受控浏览器采集、SQLite 可信水位、中文分析、回复父帖核验、幂等投递与失败关闭。用户要求运行、诊断或维护本机 X 消息监控、固定会话 heartbeat、Chrome/Edge 降级、X 水位、回复通知或飞书投递意图时使用；不要将其用于普通网页浏览、创建第二个 Codex 任务或 cc-connect 会话路由。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# X 消息监控

仅在已登记的唯一 Desktop 固定会话内运行本项目的 heartbeat。此 Skill 规定执行边界；业务事实、当前账号、水位、提醒计数和送达状态始终以当前项目的机器回执与 SQLite 账本为准。

## 先确认边界

1. 固定会话首次运行、上下文压缩后、项目规则指纹变化或机器 schema 变化时，完整读取项目 `AGENTS.md`、当前 `README.md` 和固定入口帮助；普通整点轮次不重复探索这些接口，直接从 `health` 开始。只使用项目的 `fixed_session_entry.py` 与其固定数据目录。
2. 固定会话是唯一写入者：不得新建、唤醒、转移、恢复或并行使用第二个 Codex 任务；`cc-connect` 仅作已核验一对一直送，入站保持 `silent_drop`。
3. 不创建或修改 cc-connect cron、Windows 计划任务、飞书应用、独立发送者或浏览器配置。不得读取、复制、导出、填写或记录密码、Cookie、令牌、浏览器存储、验证码、HTML、截图或 HAR。
4. 只操作本轮新建的 X 标签，完成后关闭该标签；绝不读取、导航、复用或关闭用户原有标签。禁止用 Playwright CLI、Python/Node Playwright、调试端口或独立进程启动、连接或控制浏览器；当前受控 Chrome/Edge 扩展标签自身提供的 `tab.playwright` DOM 操作门面是允许且优先的页面接口，不属于独立 Playwright。禁止内置浏览器、无头浏览器和第三种浏览器。

## heartbeat 主流程

先读取 [heartbeat 与投递契约](references/heartbeat-and-delivery.md) 和 [固定快速路径](references/fast-path-runbook.md)。先按快速路径建立唯一状态对象、命令包装器、页面阶段、分支和调用预算；不得临场改名、重组步骤、试探旧浏览器 API，或在契约拒绝后修改载荷重试。每轮以本轮机器回执为唯一状态源：

1. `health`：只读确认账本、冻结账号集合、动态 SQLite 可信水位与运行条件；健康不足或锁异常时失败关闭。
2. `heartbeat-acquire`：取得 20 分钟本轮 lease。取得失败、会话正忙或状态不完整时不扫描、不补跑、不静默。
3. `heartbeat-renew` 与 `publish-pending` 预检：使用 lease 的 UTF-8 标准输入；预检仅证明当前直送路由，不能代替最终回执。
4. 浏览器优先走 `dmdmwshr` Chrome。仅按契约允许的单次 Chrome/Edge 降级路径采集，绝不因页面、水位或 V2 验证问题改用 Edge。
5. `collect`：将完整 `XCollectedTimelineV2` 仅经 UTF-8 标准输入交给固定入口；水位按解析后的 UTC instant 比较，不使用硬编码账号、状态 ID、正文或排序。
6. 受控分析：逐条完成中文翻译、北京时间换算、仅限额度问题的 `reset_analysis`，以及独立的 `AiRelevanceV1`，再构造严格的 `XMonitorScanInputV3`。外部回复必须把回复与已验证的直接父帖合并判断广义 AI 话题；主帖和外部回复都按动态可信水位后的发布时间从旧到新处理。
7. `scan` 与 `heartbeat-finish`：由账本创建或抑制投递意图；最后只由 `heartbeat-finish` 的机器两阶段投递/完成回执收口。未知投递永不盲目重发。

## 回复与通知

- 主帖保持项目既有通知资格。所有可信外部回复仍须入账、去重并推进回复水位。
- 仅 `ai_related=true` 且证据为 `latest_search_unique_adjacent_parent` 或 `latest_search_permalink_unique_parent` 的外部回复可产生普通投递意图；`reset_analysis.related` 不是回复通知门槛。
- `AiRelevanceV1` 独立表达 `ai_related=true`、`ai_related=false` 或 `ai_related=null`/未评估。额度重置 `reset_analysis.related=true` 必须意味着 `ai_related=true`，反之不成立。
- 这类回复必须有唯一、直接、顶层父帖的作者、可见原文、中文翻译与规范永久链接；父帖为媒体占位、引用帖、祖先帖、缺失或不一致时失败关闭，不猜测替代。通知还必须把父帖原文、父帖中文翻译、父帖链接与额度区块一并呈现；额度为 false 时写明“与 Codex 额度重置：无直接关联”。
- `ai_related=false` 和 `ai_related=null`/未评估的回复只作已处理/已抑制登记，不创建普通投递；后续不重新翻译、总结或分析同一内容。不得自动重分类既有历史冻结事件。`visible_reply_marker` 永远不能支撑相关回复投递。

## 结束与汇报

- 只有完整 `heartbeat-finish` 机器回执明确给出 `notification_decision=DONT_NOTIFY`，才能静默结束；锁、采集、分析、扫描、投递或回执的任一不确定性都必须脱敏报告并失败关闭。
- 双浏览器最终失败也必须由同轮 `browser-failure` 后的 `heartbeat-finish` 收口；既有首次、持续 24 小时与恢复提醒仅由 SQLite 幂等意图决定。
- 连续零可通知周期的健康摘要以机器回执的 `notifiable_count=0` 为准，而不是“没有任何新状态”。被忽略的非 AI/无法判断回复不会重置计数，且必须由机器摘要报告；每连续第 4 轮由账本生成唯一 `x_heartbeat_no_new_summary` 并交给 `heartbeat-finish` 的第二阶段直送。无论本轮是否发送该飞书摘要，完整成功的 Codex 结果都保持 `DONT_NOTIFY`；不得自行计数、补发或另行输出摘要。
- 对用户只给出简短中文结论、机器判定和需要人工处理的项；不展示正文、凭据、Cookie、完整 JSON、内部 ID 或命令行参数。
