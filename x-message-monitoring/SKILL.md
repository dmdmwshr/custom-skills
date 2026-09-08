---
name: x-message-monitoring
description: 在唯一 Desktop 固定会话执行或维护本机 X 消息监控。固定驱动、直接回复搜索、可信父帖与上文、Codex 额度重置回复筛选、独立入账、分阶段异常汇报、飞书对账及四轮健康摘要。用于 heartbeat、诊断和优化；不用于普通浏览或新建会话。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# X 消息监控

运行只在已登记的唯一 Desktop 固定会话；维护和离线测试可在开发任务完成，不代替固定会话进行真实扫描。

1. 完整读取 [固定快速路径](references/fast-path-runbook.md)，按固定入口执行。普通轮次不再展开历史、搜索模块、生成浏览器采集代码或试探 API。规则/驱动版本变化或未知契约才读取 [完整边界](references/heartbeat-and-delivery.md)。
2. 浏览器代码唯一来源为业务项目 `scripts/desktop_monitor_driver.js`。在受支持的 CUA 会话中用普通 `let` 绑定加载原样工厂一次；版本未变、会话未重置即复用。不得使用 `globalThis`、内部模块、独立进程或自建浏览器桥接。
3. 流程固定：健康 → 锁与预检 → 主帖采集/分析/提交/登记 → 回复采集/分析/提交/登记 → `heartbeat-finish`。主帖和回复各自事务；回复失败不回滚已提交主帖。共同身份/路由故障停止后续处理。
4. Chrome 优先；仅实例、扩展、登录不可用三类实测失败，经机器授权才用 Edge。结构、水位、父帖歧义不得换浏览器。只操作本轮新标签，不触碰原有标签，不读取密码、Cookie、令牌、浏览器存储或验证码，不保存 HTML、截图、HAR。
5. 新回复直接 Latest 搜索和永久链接核验；`reply-context-plan` 只读识别新项，按 [回复上下文与额度契约](references/reply-reset-contract.md) 必要时补读最多三层，然后一次冻结、collect-stream、analysis-plan、scan-analysis。程序组装 V3，不重写映射代码，不重新分析历史记录。
6. 仅与 Codex 额度重置直接相关且对象可信的新回复推送；包括进展、延期、规则和明确否认，相关但无时间可发送并写明未提供可推算时间。通知包含回复/直接对象的原文、中文翻译、发布时间、链接和采用的上文；AI 判断只作辅助。主帖资格不变，未知送达不重发。
7. 唯一完成依据是 `heartbeat-finish`：完整成功且机器允许才输出精确 `DONT_NOTIFY`。部分完成或未知回执报告简短中文故障。连续四次完整、零可推送的**定时运行**由 SQLite 生成飞书健康证明；手动验收不充数。
8. 保留现有固定任务、自动化、工作目录和模型。不得新建、唤醒、转移、恢复或并行使用第二个 Codex 任务；cc-connect 只做 `direct_feishu`，入站 `silent_drop`，不创建 cc-connect cron、Windows 计划任务、飞书入口或第三种浏览器。
9. 每轮汇总浏览器到收口的全部失败阶段。首次、实质变化、持续满二十四小时一次、完整恢复一次由账本去重；每个失败周期仍在原任务报告。已经提交的流发生后续投递/回执/标签关闭错误，使用 cycle-failure 追加，不覆盖成功扫描或盲目重发。

维护任务遇到浏览器启动错配置、扩展控制失败、工厂转写差异，或用户明确要求验证飞书网页登录复用时，调用 `codex-local-state-diagnostics` 的浏览器控制恢复流程。普通 heartbeat 仍只走本技能固定快速路径，不因这条维护入口增加浏览器、任务、重试、登录或发送权限；不要把控制失败误报为用户没有登录。
