# 固定会话路由契约

## 公开登记字段

中枢项目的控制登记表只保留以下可审计字段：

| 字段 | 含义 | 禁止内容 |
| --- | --- | --- |
| `cc_connect_project` | 稳定的路由别名 | 会话键、平台用户标识 |
| `state` | `standby_unbound`、`binding_pending`、`awaiting_first_inbound`、`active` 或 `suspended` | “健康”之类无法判定行动的笼统状态 |
| `work_dir` | 中枢项目内的控制目录 | 业务仓的私有运行目录 |
| `target_repository` | 业务规则的事实归属 | 私有数据目录 |
| `session_policy` | 独立持久流或明确的一次性流 | 共享所有业务上下文 |
| `session_hook` | 该路由固定会话可执行的最小提示入口和动作约束 | 任意消息正文、私有会话键、跨路由 hook |
| `.codex/hooks.json` | 该路由独立的受审 Hook 配置 | 共享 Hook、未审命令、绕过配置 |
| `hook_hash` | `.codex/hooks.json` 的 exact 内容哈希 | 模糊版本、未核验副本 |
| `mobile_channel` | 脱敏的既有通道别名，或 `null` | App ID、令牌、用户标识、会话键 |

一个路由对应一个“业务项目 + 职责”组合。业务代码不复制到控制目录；控制目录只放职责边界、固定 prompt hook、协调说明和可公开的验收材料。默认通知 hook 使用 Luna low，通过 `/timer/add` 注入不透明任务并只输出最终通知；授权助手 hook 只处理非交易授权协作，不承担通知发布。

新增绑定的最小控制目录必须包含：`AGENTS.md`（职责和边界）、`SESSION_INITIALIZATION.md`（首次激活说明）、一个与职责匹配的独立 hook 和 `.codex/hooks.json`。Hook 只允许 SessionStart 的 `startup`、`resume`、`clear`、`compact` 事件，并用 `additionalContext` 注入职责说明。通知发布使用 `SESSION_NOTIFICATION_HOOK.md`，开发/非交易授权协作使用 `SESSION_DEVELOPMENT_CONTROL_HOOK.md`；不得把两类提示词互换或共用。已有目录或文件必须复用并逐项核对，不得重复添加。

启用前必须同时满足：项目身份与目标路由一致、`.codex/hooks.json` exact hash 与登记一致，并已在 Codex 设置→Hooks或 CLI `/hooks` 审核通过。任何 Hook 修改后必须重新审核；不得通过环境变量、替代配置或其他方式 bypass 审核。

推荐由中枢 `scripts/scaffold_control_route.py` 生成待审查目录和 `ROUTE_MANIFEST.pending.json`。合并公开登记、完成私有绑定后，把新控制目录加入 Codex 桌面 `cc-connect-operations` 项目的源目录：优先使用产品公开项目编辑能力；没有 API 时可在用户已授权本次接入后使用可见桌面交互完成并回读。任何情况下都不得直接编辑 Codex SQLite、索引或会话文件。

即使多个控制子目录被用户添加为同一个 Codex 桌面项目的源目录，每条活动路由的实际 `work_dir` 也必须精确等于自己的控制目录。控制目录是路由身份；`CC_PROJECT` 只用于核对公开项目别名是否一致，不能据此选择路由、覆盖 `work_dir` 或把根目录旧会话认作活动路由。

## 状态转换

```text
standby_unbound
  └─ 用户批准绑定 ─→ binding_pending
       └─ 固定会话与私有绑定均已精确核验 ─→ awaiting_first_inbound 或 active
            └─ 解绑、暂停或通道失效 ─→ suspended
```

- 只有已经存在并核验过的移动会话才能进入 `active`。
- cc-connect 无离线预建会话命令时，等待真实首条入站；不能把空会话或自发测试消息当作成功。
- `suspended` 保留路由身份和历史归属，不删除会话、通道或业务数据。
- 以中枢根目录创建的旧会话必须由用户显式指定项目与职责后才可做一次性人工初始化；它不能接收自动投递。持续使用时从正确控制目录创建后继候选，并按换代协议核验后激活。

## 私有绑定的安全迁移

1. 先由受控工具创建可恢复备份，并记录迁移前后的整体校验摘要。
2. 只改经结构化对比唯一定位的字段，例如路由名、工作目录和已记录的会话绑定引用。
3. 平台配置、允许名单、凭据和历史引用必须逐项保持不变；无法确认时失败关闭。
4. 迁移后运行脱敏核验，至少确认项目别名、会话实际工作目录精确回到该控制目录、持久策略、通道数量与类型、以及唯一进程链；`CC_PROJECT` 只作为别名一致性证据。
5. 新绑定未通过前不删除旧绑定，不重定向已有定时器，也不发送生产消息。

## 中枢与业务仓的职责

| 工作 | 归属 |
| --- | --- |
| 数据来源、业务判断、去重、版本化、发送条件 | 业务仓 |
| 固定会话、移动通道、桥接进程、投递和脱敏回读 | cc-connect-operations |
| 真实交易、正式外发、凭据和不可逆清理 | 各业务仓的独立授权边界 |
