---
name: x-message-monitoring
description: 在唯一 Desktop 固定会话执行或维护本机 X 消息监控。固定浏览器驱动、Chrome/Edge 容错、主帖与回复独立入账、只分析新内容、SQLite 水位、飞书投递对账及四轮健康证明。用于 X 监控 heartbeat、诊断和优化；不用于普通浏览或新建会话。
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
5. `analysis-plan` 仅返回机器验证后的新内容；批量翻译中文、换算北京时间、判断 AI 相关性与独立额度重置问题。`scan-analysis` 自动组装 V3。不要重写映射代码，不重新分析已发送、抑制或未知的历史记录。
6. 仅 AI 相关且直接父帖可信的回复推送，包含父帖原文、中文翻译和链接。主帖规则不变。未知送达不重发；登记、传输接受、可见送达分别报告。
7. 唯一完成依据是 `heartbeat-finish`：完整成功且机器允许才输出精确 `DONT_NOTIFY`。部分完成或未知回执报告简短中文故障。连续四次完整、零可推送的**定时运行**由 SQLite 生成飞书健康证明；手动验收不充数。
8. 保留现有固定任务、自动化、工作目录和模型。不得新建、唤醒、转移、恢复或并行使用第二个 Codex 任务；cc-connect 只做 `direct_feishu`，入站 `silent_drop`，不创建 cc-connect cron、Windows 计划任务、飞书入口或第三种浏览器。
