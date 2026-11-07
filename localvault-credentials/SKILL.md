---
name: localvault-credentials
description: 管理本机 LocalVault 中由 SecretManagement 和 SecretStore 保存的凭据，包括新增、更新、元数据检查、精确删除和向当前用户批准的本地自动化程序经标准输入临时注入；也提供用户授权后的普通图片验证码自动登录（Ego 主入口、本地离线 OCR、持久提交计数）。不得输出明文凭据或传给远程、不明程序。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# LocalVault 凭据管理

执行平台：Windows。先核对本机依赖及用户配置；WSL/Linux 不直接套用此入口，远程载荷须单独核验。安装、发现和业务验收按目标平台分别报告，以对应记录及当前实测为准。

使用本技能管理当前 Windows 用户下的 `LocalVault`。密钥库是本机加密保存凭据的 PowerShell 存储，不是浏览器密码管理器，也不会自动连接 Codex 内置浏览器。

## 安全边界

- 只在本机 PowerShell 进程中处理凭据；不要把密码写入技能文件、映射文件、日志、命令参数、环境变量或聊天消息。
- 新增或更新凭据优先使用本机凭据提示框，不要求用户把密码发到对话中。用户已经主动提供并明确要求保存时，可将该值输入不回显的凭据提示，不放入命令参数或普通记忆；保存后只核验账号、类型和非空状态。
- `Info` 只显示密钥名称、类型、密钥库和映射信息；不要用会把密码打印到终端的调用方式。
- 运行时调用只允许传给用户明确批准的本地程序，并通过标准输入发送一次性 JSON；不要把凭据发给远程命令、网络接口或不明程序。
- 不读取、删除或修改浏览器 Cookie、密码库、配置文件或会话存储；LocalVault 与浏览器自动填充保持分离。
- 验证码等挑战默认请用户完成。用户明确授权“普通图片验证码”自动识别后，只走下文“授权普通图片验证码自动登录”的本地离线 OCR 路径；短信/邮箱验证码、MFA、滑块/行为验证等其他挑战仍由用户完成，不绕过，也不降级去读浏览器数据或工具强制限制。
- 真实登录前确认目标网站、账号映射和本地自动化程序均由用户明确指定；一次只运行一个凭据自动化任务。

## 初始化

先在 PowerShell 7 中确认模块；缺失时由用户在本机安装：

```powershell
Install-Module Microsoft.PowerShell.SecretManagement -Scope CurrentUser
Install-Module Microsoft.PowerShell.SecretStore -Scope CurrentUser
```

使用本技能附带的脚本：

```powershell
$skillRoot = 'C:\Users\12070\.codex\skills\localvault-credentials'
```

脚本路径为 `$skillRoot\scripts\Manage-LocalVaultCredential.ps1`。脚本会导入模块，并在 `LocalVault` 尚未注册时注册它；首次访问可能要求输入密钥库解锁密码。

## 保存凭据

为每个平台建立稳定且不含密码的名称，例如 `platform-purpose-account`。用 `Save` 让用户在本机输入账号和密码：

```powershell
& "$skillRoot\scripts\Manage-LocalVaultCredential.ps1" `
  -Action Save `
  -Name 'site-example-user' `
  -Username 'user@example.com'
```

只有用户明确要求覆盖时才加 `-Force`。网站地址、平台、账号与密钥名称的对应关系放在调用项目的普通映射文件中，但映射文件不得包含密码；推荐字段为 `site`、`url`、`username`、`secretName`。

## 查看元数据

用 `Info` 验证密钥是否存在和类型是否正确，不暴露密钥值：

```powershell
& "$skillRoot\scripts\Manage-LocalVaultCredential.ps1" `
  -Action Info `
  -Name 'site-example-user'
```

凭据应显示为 `PSCredential`。如果保存成普通字符串，重新用 `Save` 保存，不要在终端直接打印它。

## 运行时调用

当前消息已明确指定本地自动化程序、目标和调用动作时，视为已批准使用 `Invoke`，无需再次确认。子程序必须从标准输入读取一次性 JSON：

```json
{"username":"...","password":"..."}
```

调用示例：

```powershell
& "$skillRoot\scripts\Manage-LocalVaultCredential.ps1" `
  -Action Invoke `
  -Name 'site-example-user' `
  -CommandPath 'node' `
  -CommandArgumentList 'scripts\local-login-runner.mjs' `
  -WorkingDirectory 'C:\path\to\approved-local-project'
```

脚本不会把密码放到命令行参数或环境变量中，也不会把子程序输出中的密码原样回显。只调用可信的本地程序；不要把 `CommandPath` 指向下载后未经核验的脚本。

用户明确指定临时 Edge/Playwright 手动路径时，可使用独立的临时 Edge/Playwright 上下文：运行时填写账号和密码，由用户手动完成验证码，再由脚本提交并验证页面结果。这个调用链不等于把 LocalVault 接入 Codex 内置浏览器；网页登录的默认浏览器是 Ego，授权普通图片验证码的自动路径见下一节。

用户明确指定已连接的 Chrome/Edge 配置时，优先配合 `playwright-browser-control` 的 `scripts/vault_login.cjs` 复用官方 CLI 扩展会话；按其 `references/vault-login.md` 创建非秘密请求文件，经本技能 `Invoke` 注入凭据。此路径不读取浏览器密码库、不增加调试端口或常驻服务。不要用普通 CLI `fill` 传密码，也不要在填密码后、提交前保存页面快照；密码框的圆点不代表快照已经脱敏。真实登录必须回读业务导航，结果未知不重复提交。

## 授权普通图片验证码自动登录

用户明确授权普通图片验证码（4 位数字图片）自动识别，并指定目标站点、LocalVault 密钥和调用方已准备好的 Ego TaskSpace/Page 后，使用本技能的 `scripts/captcha-login.mjs`：经 `Manage-LocalVaultCredential.ps1 -Action Invoke` 从标准输入注入凭据，在**现有** Ego 页面内刷新验证码，用 `scripts/captcha-ocr.py`（离线 RapidOCR，仅 ASCII 四位数字且置信度不低于 0.8）识别后填写提交，并在刷新页面后核对目标 URL、期望账号和业务入口才算成功。

- 任务 ID 绑定固定的目标/账号/密钥/上限，提交计数先于点击持久化（按“每轮”计：一次重新登录为一轮，每轮连续最多 5 次实际提交，跨命令和重启连续、不重置；完整刷新核验成功后本轮归零，下次过期重新登录重新享有 5 次，累计总数 `total` 另行保留）；点击后结果未知保持待对账，不自动再点；只有本次提交产生的明确验证码错误才刷新重试，账号/密码错误、访问限制和核验失败会持久阻断，需附证据对账后才能再次提交。成功核验要求调用方显式提供账号控件与业务入口控件的选择器（`--account-selector`、`--business-selector`，纳入任务绑定，缺失则失败关闭、不回退整页文本）。
- 凭据、验证码和图片只在内存；不 claim 他人 Ego 空间、不新建/关闭页面、不注销。
- 完整参数、初始化（需显式历史已用次数与证据）、对账、旧账 `migrate` 迁移、结果码和示例见 [references/captcha-login.md](references/captcha-login.md)。依赖（离线 OCR、Ego）不可用时如实报告未覆盖，不降级冒充通过。

## 映射式浏览器登录

已获用户明确同意的本地站点可使用映射式入口。映射文件位于
`$skillRoot\local\accounts.json`，每个站点只能包含 `url`、`username`、
`secretName` 和 `captcha` 四项；严禁加入密码、Cookie、令牌或验证码内容。该文件被
Git 精确忽略，所以修改映射不会形成技能版本更新。

浏览器代码和锁定依赖清单位于技能源码，但实际 Playwright 依赖只安装到当前用户的
本地运行时目录，不会写入技能源码或 CC Switch 安装副本。首次安装或依赖修复时，只运行
初始化入口；它不读取 LocalVault，也不会发起登录：

```powershell
& "$skillRoot\scripts\Initialize-LocalVaultBrowserRuntime.ps1"
```

只有用户明确指定目标站点并要求真实登录时，才使用下面的入口。`Info` 仅显示密钥元数据；
`Save` 通过系统凭据提示框保存；`Login` 才会读取一次性凭据并打开临时浏览器窗口。该临时窗口路径
不含自动识别：验证码、MFA 和邮箱验证码由用户手动完成；已授权普通图片验证码时改走上一节的
Ego 自动路径。

```powershell
& "$skillRoot\scripts\Invoke-LocalVaultWebLogin.ps1" `
  -Action Info `
  -AccountKey 'site-account-key'
```

运行器只从标准输入接收一次性的账号和密码，不读取现有浏览器配置、Cookie、自动填充或
会话存储。它优先使用已安装的 Edge，结束后关闭临时上下文；若没有 Edge 或网页结构变化，
应报告失败，不要降级为读取浏览器数据或绕过验证码。

## 删除凭据

删除必须使用精确名称。当前用户已明确要求删除该名称时视为已授权，可直接加 `-Force`；只有范围不清或批量、难以恢复的删除才最多确认一次：

```powershell
& "$skillRoot\scripts\Manage-LocalVaultCredential.ps1" `
  -Action Remove `
  -Name 'site-example-user'
```

删除前先用 `Info` 核对名称，避免误删其他平台凭据。

## 常见问题

- 在 CMD 中输入 `Import-Module`、`Get-Credential` 等命令会提示不是内部命令；切换到 PowerShell 7。
- `Enter password:` 通常是 LocalVault 的解锁密码；之后的 `Get-Credential` 提示才是网站账号密码。
- `Set-Secret` 报 `Secret is null`，表示凭据变量为空或已被清空；重新执行 `Save`，不要复用已结束的交互会话变量。
- `LocalVault` 只负责本机密钥存储，不会自动填充 Codex 内置浏览器；要自动化浏览器，必须有用户批准的本地运行器。
