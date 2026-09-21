# 回执、投递与完成

- 原 direct_feishu 通道与原目标不变；scan 只创建幂等意图。同步只沿受控认证 GET，不从 UI 重发。
- 已登记、transport_accepted、平台原消息存在、网页可见及用户已读是不同证据。XMonitorPlatformReceiptV1 按原意图/应用/目标/内容/时间核对，仅追加证明，不改写旧周期或手写 delivered。
- 真实 unknown 不重发、不新建键；历史积压单列，不为了 health 变绿重放。本轮通知未定则报告，不把 notifiable=0 当作没有系统提醒。
- heartbeat-finish 为完成依据。XMonitorHeartbeatFinalizeV2 双流及本轮投递确定才 completed；部分成功 partial_failed，其余 failed_closed。独立已提交流不回滚。
- 故障按首次、实质变化、持续满24小时、完整恢复去重；每个失败周期仍在原任务报告。
- 业务保留每四个完整零可推送 scheduled 周期的唯一健康摘要；manual_validation 不计。失败/部分/本轮未知/新可推送会打断统计。这是产品功能，不是每次维修必须再等四轮的门槛。
- 正常关闭释放本轮锁和事实，保留受管 MCP 页面。最终返回 DONT_NOTIFY 需机器 finish 明确允许；人工验收报告实际结果与证据等级。
