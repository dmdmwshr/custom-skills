# 业务消费者接入契约

cc-connect 只负责路由、会话、通道、投递账本和脱敏回执。业务项目拥有业务事实、不可变待发送箱、消息内容、发送条件和授权边界。中枢不得直写业务数据库，也不得从聊天上下文、网页摘要或标题补全通知事实。

## ControlRouteV2 静态登记与运行读模型

在既有路由字段上补充下列静态、可审计字段；只记录脱敏别名和业务职责。此登记可纳入版本控制，但绝不包含运行绑定状态或代次。

| 字段 | 要求 |
| --- | --- |
| `role` | 明确的项目职责，例如 `data_operations`；用户显示名称另行固定，一个路由只对应一个项目加职责。 |
| `allowed_event_types` | 显式枚举允许投递的事件类型；未登记类型失败关闭。 |
| `registered_sources` | 已登记业务来源的稳定别名；未登记来源不得提交投递意图。 |

运行中的绑定状态、`binding_generation`、会话标识、平台标识、通道凭据、Bridge 令牌、运行时 ACL 文件、能力令牌、私有路由映射和消息正文只可存在于中枢运行账本或受限私有运行边界，不写入 Git、业务仓、公开静态登记、Skill、命令参数、环境变量、日志、回执或业务数据库。控制网关仅通过路由查询接口返回必要的脱敏运行结论。

## 版本化本机接口

中枢控制网关默认仅监听回环地址。业务项目只能通过下列版本化接口访问；Bridge、UDS、会话映射和平台能力只允许中枢私有运行适配器使用。

| 接口 | 行为 |
| --- | --- |
| `GET /v1/routes/{route_key}` | 从中枢运行账本返回脱敏路由状态、`role`、当前 `binding_generation`、`delivery_mode` 与健康结论。 |
| `POST /v1/delivery-intents` | 接收路由别名、通知引用、事件类型、来源别名、幂等键、内容哈希与过期时间；拒绝消息正文、任意 URL 或未登记来源。 |
| `GET /v1/deliveries/{idempotency_key}` | 返回该意图创建时的 `delivery_mode`、会话提示、会话发布、移动通道和总体状态。 |
| `POST /v1/routes/{route_key}/acknowledgements` | 接收业务项目对当前 `binding_generation` 的确认。 |

### `delivery_mode` 公开枚举

路由与投递读模型只能返回下列三个稳定值，禁止暴露 `native` 等中枢内部实现名：

| 值 | 精确语义 |
| --- | --- |
| `session_agent` | 业务自动通知的唯一默认模式：Timer 将不透明任务注入固定会话，由路由专属 hook 解析、一次性领取并只输出最终通知，cc-connect 通过原 ReplyCtx 回发。 |
| `direct_feishu` | 当前用户明确授权时的人工原生直发回退；业务 worker、自动任务和失败重试不得选择该模式，也不得与 `session_agent` 双发同一幂等键。 |
| `disabled` | 能力、配置、绑定、工作目录、职责 hook 或健康验证未通过；不创建 Timer、不直接发送，只保留业务待发送项。 |

每条投递回执固定保存其创建时的 `delivery_mode`；路由读模型返回当前活动模式。业务消费者只允许为自动流程登记并接受 `session_agent`，读到其他模式时必须失败关闭。

### DeliveryIntentV1 的固定请求形状

`POST /v1/delivery-intents` 的 JSON 必须且只能包含下列七个字段：`route_key`、`source`、`event_type`、`notification_ref`、`idempotency_key`、`content_hash`、`expires_at`。`notification_ref` 必须是业务项目定义的受限不透明对象 ID，不能是 URL，也不能承载标题、摘要、正文、深链或其他业务文本；`content_hash` 为投递时临时解析出的完整业务内容的 SHA-256。

- `Authorization: Bearer <本机私有能力令牌>` 证明来源能力。令牌只由同宿主受限运行边界直接提供，不能写入 Git、业务配置、日志、任务参数、环境变量、业务数据库或回复。
- `GET /v1/deliveries/{idempotency_key}` 还必须带 `X-Route-Key` 和 `X-CC-Source`，两者与已登记路由/来源一致。
- 确认请求正文只能是 `{ "binding_generation": <整数> }`，并带 `X-CC-Source` 与同一能力令牌。既有活动路由只可完成一次初始业务确认；后继代次只能在中枢已记录桌面响应核验后确认。
- 路由读模型至少返回 `state`、`role`、`delivery_mode`、`binding_generation`、`acknowledged_generation`、健康结论及脱敏队列计数；投递读模型分别返回创建时的 `delivery_mode`、`mobile_status`、`desktop_status`、总体状态和错误类别。

对投递意图按 `(route_key, idempotency_key)` 去重。持久投递账本只能保留哈希、状态、时间、错误类别和不透明关联值；内容仅可在一次投递时从业务项目的受控只读来源暂时读取。未知送达状态不得自动重发。

## 接入与回读流程

1. 业务项目先读取自身规则与唯一计划，确认事件事实、发送条件、去重和当前授权；中枢不代为判断。
2. 业务项目查询目标路由，确认 `role` 对应的职责、允许事件类型、健康结论与已确认的绑定代次；运行状态不是 `active` 时，只保留业务待发送项。
3. 业务项目提交不含正文的投递意图。默认由中枢通过 `/timer/add` 向目标固定会话注入不透明任务；该会话的独立 prompt hook 以 Luna low 读取受控引用，只输出最终通知，cc-connect 通过同一 ReplyCtx 自动回发原飞书会话。会话不得调用 `POST /send`；中枢直接 `POST /send` 仅在当前用户明确授权的人工回退中允许，正文只存在于受控读取过程的内存，不得双发同一幂等键。
4. 业务项目通过投递查询接口回读结果，并只追加自己的送达回执，绝不改写原始待发送对象。
5. 路由代次变化后，业务项目必须读取新 `binding_generation` 并确认；确认前中枢不得将自动投递指向候选代次。

## OKXnew 首期示例

OKXnew 首期使用两个彼此隔离的显示角色：“OKXnew 授权助手（非交易授权）”负责非交易范围的人工授权协作；“OKXnew 消息推送”负责已确认宏观发布或修订等高重要级通知。两者必须分别拥有控制目录、路由和私有会话映射，不能互相代投；任何角色都不得承接真实交易授权。

“OKXnew 消息推送”只登记“已确认宏观发布或修订”的高重要级事件。其不可变 `NotificationEnvelopeV1` 保持原状，并通过关联的投递意图、绑定确认和送达回执接入中枢。

- 数据采集在形成合格宏观信封后异步提交投递意图；桥接不可用不得阻断宏观采集、策略或模拟执行。
- 中枢通过 `/timer/add` 向“OKXnew 消息推送”固定会话注入不透明任务；该会话使用 Luna low 读取登记信封引用，只输出最终通知，由同一 ReplyCtx 自动回发原飞书会话。会话不得调用 `POST /send`；原生直发仅是显式人工回退。
- 中枢账本分别记录 `session_prompt_accepted`、`session_prompt_unverified`、`session_publish_unverified`、明确失败和总体未知送达；同一任务不得创建第二个 Timer 或备用直发。
- `session_prompt_accepted` 只表示 Timer 已接受；只有固定会话解析内容并原子领取一次性发布权后，才能进入 `session_publish_unverified`。该状态仍不等于飞书已送达或已读；未知结果不得自动重发。
- 不登记交易、授权、执行、撤单、账户或仓位相关事件。`simulation` 与 `okx_demo` 以外的交易模式不属于本契约范围。

## 失败关闭边界

- 路由、来源、事件类型、幂等键、内容哈希、过期时间或业务确认缺失时，拒绝投递意图并保留业务待发送项。
- Bridge 协议探测或兼容性检查失败时，只停止依赖 Bridge 的换代与绑定变更。自动通知另行要求回环网关、UDS Timer、私有目标映射、路由工作目录、职责 hook 和固定 Agent 配置全部通过脱敏验证；任何一项失败都停止对应投递，不得改写会话文件、私有配置或业务事实。
- 任何桥接恢复、路由重绑、实际发送或定时任务创建均需符合当前用户授权和业务仓授权边界；健康查询本身不授权这些变更。
