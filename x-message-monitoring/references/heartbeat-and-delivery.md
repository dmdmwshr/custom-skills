# 回执、投递与完成

- 原 direct_feishu 通道与原目标不变；scan 只创建幂等意图。同步只沿受控认证 GET，不从 UI 重发。
- 已登记、transport_accepted、平台原消息存在、网页可见及用户已读是不同证据。XMonitorPlatformReceiptV1 按原意图/应用/目标/内容/时间核对，仅追加证明，不改写旧周期或手写 delivered。
- 网关保留的 `delivery_unverified / transport_accepted` 与业务账本 `delivered` 可以并存：先读取原键附加的平台证明，不能只看 transport state。对历史指定键，用项目受管只读 `verification_summary(..., only_key=原键)` 判断平台是否已经核实；查询不发送、不补建意图。平台已核实但浏览器失联时报告“平台已核实；网页可见性待验”，不能报发送失败或重发。累计账本统计、当前同步新增数量、本轮业务通知与生命周期告警分别说明；业务通知为0不代表没有恢复/故障消息。
- `delivery_unverified` 是待核验观察，不是永久否决。迟到的合法精确平台证明，可按项目 DELIVERY_VERIFICATION.md 与受管状态转换单调提升当前投递状态；保留旧回执及历史周期，不手改为成功。发布验证须覆盖 registered → unverified → 有效proof的真实中间态，不能只测首次直接收到proof。
- 尝试计数与纯文本限制不能独自否决有审计的明确pre-send恢复或真实卡片证明：先核对受管原键恢复证据和实际平台内容，不放宽未知不重发原则。卡片旧GET可能只返回占位文字，按当前官方接口的 `user_card_content` 读取原结构并完整匹配原消息、目标和冻结内容；无完整内容不当proof，不能将任意近似文本或卡片标题当已送达。具体接口和恢复资格以项目/主机现行文档为准，不另写发送或修库入口。
- 新通知模板的人类可读时间按项目规则显示到分钟，保留时区；来源原文/译文、内部UTC事实、签名与冻结哈希不做全局秒数替换。历史已冻结通知继续按兼容验证路径核对，不重新渲染或为了格式更新重发。
- 真实 unknown 不重发、不新建键；历史积压单列，不为了 health 变绿重放。本轮通知未定则报告，不把 notifiable=0 当作没有系统提醒。
- heartbeat-finish 为完成依据。XMonitorHeartbeatFinalizeV2 双流及本轮投递确定才 completed；部分成功 partial_failed，其余 failed_closed。独立已提交流不回滚。
- 故障按首次、实质变化、持续满24小时、完整恢复去重；每个失败周期仍在原任务报告。
- 业务保留每四个完整零可推送 scheduled 周期的唯一健康摘要；manual_validation 不计。失败/部分/本轮未知/新可推送会打断统计。这是产品功能，不是每次维修必须再等四轮的门槛。
- 正常关闭由 adapter 按当地 ProjectBrowserV2 契约完成真实页面复用/用途留存与显式环境 finish，再释放本轮锁和事实；临时页关闭，protected/unknown 单独报告。adapter 1.2.2/workflow 1.4.1 起，park/released成功只表示控制已释放；清理是否完成读取cleanupPending/cleanupComplete/cleanupUnresolved，clearClose后仍须核对workflow.result().cleanup保留的反馈。protected、unknown、退役页和恢复候选四类未决不能被释放成功掩盖，正常按用途retained不算未决；未决项按当地契约继续对账或准确报告，不直接清状态。最终返回 DONT_NOTIFY 需机器 finish 明确允许；人工验收报告实际结果与证据等级。
