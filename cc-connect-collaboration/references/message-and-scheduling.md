# 消息与定时协作

## 先选择会话模式

| `session_mode` | 入站与会话 | 定时边界 |
| --- | --- | --- |
| `cc_connect_fixed_session`（仅新建路由默认） | 已核验的飞书入站进入同一 cc-connect 固定会话。 | 可在明确授权后使用受控 Timer。 |
| `desktop_owner_outbound_only` | 飞书入站在平台边界 `silent_drop`，不进入 Hook、Bridge、CLI、账本或任何 Codex 任务。 | 仅目标 Desktop fixed task 的 Desktop heartbeat 可周期运行。 |

模式必须由路由明确登记，不能由项目归属、会话标题或界面显示推断。既有路由缺少模式时进入 `mode_unknown` 并失败关闭；新建路由才可使用默认双向模式。A 模式中 cc-connect 仅保留 `direct_feishu` 出站；它不得创建、唤醒、恢复或写入 Desktop 会话。

## 先区分消息类型

| 场景 | 正确路径 |
| --- | --- |
| 业务代码自然产生的自动通知 | 业务轻量消费者提交无正文意图，cc-connect `direct_feishu` 直接调用私有 `/send`。 |
| 用户从飞书发起或回复的协作 | 仅 `cc_connect_fixed_session` 的既有移动路由进入该“项目 + 职责”的固定桌面会话。 |
| 需要固定会话在未来继续上下文任务 | `cc_connect_fixed_session` 使用 cc-connect Timer；`desktop_owner_outbound_only` 使用目标 Desktop heartbeat。 |

这三种路径不得以同一幂等键双发，也不得把自动通知改造成桌面轮询、Timer 或外部 CLI 恢复线程。

## 自动直接投递

投递前确认路由活动、来源和事件已登记、业务项目与职责匹配、绑定代次已确认、数据通知与交易授权目标隔离、内容哈希匹配且未过期。中枢每轮最多原子领取一条，正文仅从业务受控只读来源临时进入内存。

发送前明确失败最多有限尝试三次；一旦私有 `/send` 开始，超时、非 200 或结果不明均记为 `delivery_unverified` 并禁止重发。投递意图登记结果未知时只允许认证 GET 对账；无论对账结果为 `present`、`absent` 或 `unknown`，均进入不确定状态且不得再次 POST 或自动重发。200 或 `transport_accepted` 只代表传输层接受，最终飞书真实可见性由用户确认。

每轮把“业务正常运行”“传输接受”和“用户确认真实可见”分别记录，不能相互替代。业务层 `NO_REPLY` 表示正常但无需用户可见内容；最终平台结果必须为原生 `DONT_NOTIFY`，不得附带正文、健康提醒、补跑或补发。

自动投递不会在 Codex 桌面任务里生成静默记录。`cc_connect_fixed_session` 的用户回复仍进入既有固定会话；`desktop_owner_outbound_only` 的任何飞书入站均 `silent_drop`，不能为追求桌面记录而新建第三个会话。

消息事实必须来自业务仓规范记录。中枢不从标题、网页摘要或聊天上下文推断数值，也不负责把未验证数据升级为通知。

宏观快速决策卡应由业务仓冻结为一屏中文正文，通常包含标题/报告期/发布类型、一句结论、3—5 个核心指标、同口径前期对比、条件性影响、限制、中文来源和业务入口。不得出现底层序列代码、`Level`、英文单位、内部相对路径或“第 N 版”等实现信息；没有发布前市场预期时不得判断“超预期”。中枢只原样传输，不做业务改写。

## 人工消息与定时

人工消息必须显式指定路由和目标，不依赖列表顺序。长文本从标准输入传入；发送后只记录脱敏结果和幂等键。重绑、发送、创建 Timer 或修改计划任务分别需要当前用户对该动作的明确授权。

| 目标 | 使用方式 | 要点 |
| --- | --- | --- |
| `desktop_owner_outbound_only` 唤醒唯一 Desktop 固定任务继续既有上下文 | 桌面端该任务的 heartbeat | 只在用户明确要求时创建或修改。 |
| `cc_connect_fixed_session` 精确时点触发会话提示 | cc-connect Timer | 与自动业务通知无关；必须通过唯一写入者门禁。 |
| 保持移动桥接或通知 worker 生命周期 | Windows 计划任务/服务 | 与消息正文无关；按任务生命周期管理。 |

同一路由的 Desktop heartbeat 与 cc-connect cron 强制互斥。`desktop_owner_outbound_only` 需要周期执行时，只保留唯一目标 Desktop fixed task 的 Desktop heartbeat；cc-connect Timer、cron、Bridge scheduler 和会话唤醒均不得触发同一业务结果。发现两个触发源、来源不明或写入冲突时，记为 `timer_conflict` 并失败关闭：不扫描、不发送、不补跑，也不新建计时器试错。

依赖已登录 Chrome、浏览器扩展、可见桌面 UI 或同一桌面运行环境的能力，只能由该目标 Desktop heartbeat 执行。cc-connect CLI、cron、Bridge 和替代浏览器运行器不得模拟、复用或绕过这些能力。

固定线程的 active writer、空响应或运行包不兼容会阻止 Timer/会话注入，但不自动否定独立的 `direct_feishu` 传输。Bridge 与 Management API 不作为业务自动通知出口。

## 本机私有边界

运行时 ACL、能力令牌、路由映射、会话键和凭据不得进入 Git、命令参数、环境变量、日志、回复或业务数据库。AUTO-01 控制网关、直接分发 worker 和私有飞书适配器必须在同一宿主机运行。
