# 固定会话代发契约

固定会话代发是默认通知路径：业务项目提交不含正文的投递意图，中枢通过 `/timer/add` 注入不透明任务，固定会话以 Luna low 按职责 hook 读取受控引用，只输出最终通知，由 cc-connect 同一 ReplyCtx 自动回发原飞书会话。

## 固定职责

- 业务项目拥有事实、不可变待发送箱、发送条件、幂等键和授权边界。
- 中枢只负责路由、会话提示、绑定代次、投递账本和脱敏回执；不补全事实、不持久化正文。
- 固定会话只做轻量格式化、引用读取和最终回复，不做交易、账户、授权、下单、撤单或事实升级；会话不得调用 `/send`。
- 每条路由必须使用自己的控制目录和 prompt hook；不得跨路由复用会话、hook 或幂等键。
- 每条路由必须在自己的 `work_dir/.codex/hooks.json` 中定义受审 Hook，仅使用 SessionStart 的 `startup`、`resume`、`clear`、`compact` 事件和 `additionalContext`；自动链只允许 `session_agent`，固定会话不得调用 `/send`。
- 启用前必须通过项目身份和 `.codex/hooks.json` exact hash 两层信任校验，并在设置→Hooks或 CLI `/hooks` 审核；Hook 修改后必须重新审核，禁止 bypass。

## 最小提示

提示只能包含固定任务类型、通知引用、幂等键、内容哈希、过期时间、随机 `publish_nonce`、发布动作和禁止事项。不得包含私有令牌、会话键、账号信息、任意 URL 或未登记正文；原始 nonce 只进入 cc-connect 私有 Timer 提示和会话标准输入，中枢账本只能保存其 SHA-256。

固定会话收到提示后应：

1. 校验路由职责、绑定代次和业务引用仍有效；
2. 从固定回环只读接口读取内容、核对引用/幂等键/哈希/过期时间，并只在内存生成最终文本；
3. 立即向中枢原子 claim `publish_nonce`；领取失败时空输出并停止；
4. 领取成功后只输出已生成的最终通知，由同一 ReplyCtx 自动回发原飞书会话；
5. 不附加解释、工具过程、第二次发送或凭据。

`connectivity_probe` 只能验证路由、Hook、Timer 和 ReplyCtx 的连通性，不得伪造、推断或发布宏观事实。探针若需要实际飞书外发，必须有当轮用户明确授权；飞书端最终可见性必须由用户确认。

## 状态与幂等

至少分别记录 `session_prompt_accepted`、`session_prompt_unverified`、nonce 是否 `claimed`、`session_publish_unverified` 和明确失败。`session_prompt_accepted` 只表示 Timer 被接受；Timer `fired` 但没有 claim 不得记为已发布。claim 表示会话已完成内容生成并开始最终输出，仍不等于飞书送达；没有可信平台回执时总体保持 `delivery_unverified`。

同一 `(route_key, idempotency_key)` 只能产生一次会话提示和一次发布尝试。`/timer/add` POST 一旦开始，任何超时、非 200 或回执异常都视为可能已经创建，不得再次添加 Timer；重复执行只能有一个 nonce claim 成功。未知送达不得自动重发。

## 人工回退

中枢直接调用 cc-connect `POST /send` 不是默认路径，只能在当前用户明确授权的人工回退中使用。会话代发与人工直发不得同时执行同一幂等键；固定会话不可用时默认保留待发送项，不自动切换直发。

## 失败关闭

固定会话、工作目录、prompt hook、绑定代次、Agent 模型/推理强度、业务引用或哈希校验失败时停止发布并保留业务待发送项。Luna/low 由固定 cc-connect 项目配置，Timer API 不携带或覆盖模型参数。任何交易、账户、授权、执行、撤单、仓位或真实外发扩权请求均失败关闭。
