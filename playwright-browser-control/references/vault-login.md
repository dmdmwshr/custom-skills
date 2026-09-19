# 已授权的本机凭据登录

只用于用户明确指定的网站、账号和登录动作。凭据源是 LocalVault，不是浏览器密码库；复用现有扩展连接和命名会话，不新建无头浏览器或桥接服务。

1. 先在当前网页确认准确网址、登录输入框、提交按钮及登录后可见导航。已登录时只回读，不重复提交。
2. 如有验证码，先按用户授权和当前工具规则完成必要的人机验证。可以处理的普通图片验证码先刷新、仅截该元素并确定当前值；若工具要求人工接管或强制停止，遵从工具限制。不把旧验证码用于新页面。
3. 创建仓库外的请求 JSON。它只含非秘密绑定：`browser`、`session`、`origin`、`loginUrlPrefix`、`username`、`usernamePlaceholder`、`passwordPlaceholder`、`submitText`、`successText`，有验证码时另给 `captchaPlaceholder` 和当前 `captchaValue`。不得加入 password、Cookie 或 token；请求文件不用作长期事实。
4. 在连接所用的同一工作目录，用 `localvault-credentials` 的 `Manage-LocalVaultCredential.ps1 -Action Invoke`，将精确凭据名称传给 `scripts/vault_login.cjs`。`CommandPath` 使用本机固定 Node，`CommandArgumentList` 依次为该脚本路径和请求 JSON 路径，`WorkingDirectory` 必须匹配命名会话。凭据只经标准输入进入本地进程。
5. 运行器核对账号、来源、固定 CLI 版本；调用已安装官方 CLI 的进程内入口，避免把密码放进操作系统命令行、环境变量或文件。它在一次 `run-code` 中填入、仅提交一次、验证登录后导航；失败且仍在登录页时清空密码。禁止同时开启 CLI 会话记录、Playwright 跟踪、DEBUG 日志或外部进程转储。
6. 只返回 `LOGIN_VERIFIED`、`ALREADY_LOGGED_IN` 或未确认状态。`submitted=true` 仅表示已尝试提交，不能单独证明服务器接受；超时/结果未知先回读网址和导航，不自动再次提交。`ALREADY_LOGGED_IN` 仅证明已有会话可用，不证明密码重新认证过。

## 隐私回读

- 禁止把密码交给普通 CLI `fill`、`type` 参数，或把含密码的 JavaScript 放进文件。
- 禁止在密码已填写但尚未完成提交时执行会生成快照的命令。输入框为 password 类型并不能保证快照无明文。
- 若历史快照误含明文，在本机凭据进程中只对本次精确产物替换为 `[REDACTED]`，回读零命中；不要在终端输出命中正文。
- 登录截图、请求文件及运行产物不进入 Git。保存密码不等于验证登录成功。

已验收：Windows、官方 CLI 0.1.21、Edge 扩展 0.4.0。网页菜单和指定账号均需现场核对；不承诺其他网站或浏览器版本通用。
