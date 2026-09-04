---
name: browser-tamer
description: 在 Windows 上诊断和管理 Browser Tamer 的安装、HTTP/HTTPS 默认处理器、浏览器配置文件、域名路由、配置文件选择器与外部链接未命中问题。用于系统外部链接路由；Codex 浏览器扩展连接或浏览器进程启动应先按本 skill 的分层边界另行诊断。
---

# Browser Tamer

通过可审计的本地命令管理 Browser Tamer。先只读检查，再备份、预演、修改和回读验证；只有脚本不支持当前版本或配置结构时才使用桌面界面。

## 硬规则

1. 先运行 `status`，确认可执行文件版本、配置路径以及 `HTTP`/`HTTPS` 当前处理器。
2. 修改前运行 `list` 确认浏览器名和配置文件名，使用精确名称，不猜测 `Profile 1/2/...`。
3. 新增或删除规则前先使用 `-WhatIf`；真实修改必须创建带时间戳的配置备份。
4. 只直接修改 Browser Tamer v6 的 YAML 配置。若版本低于 6、配置不是 UTF-8、结构不符合 Browser Tamer 生成格式，停止并改用对应版本官方界面。
5. 不直接修改 Windows `UserChoice` 注册表来设置默认浏览器。需要变更时打开“设置 → 应用 → 默认应用”，由用户确认。
6. 不把调用 `bt.exe <URL>` 当作只读测试：它会实际打开浏览器。只有用户明确允许打开测试链接时才执行。
7. 浏览器内部链接、内嵌 WebView 和显式 `microsoft-edge:` 链接可能绕过系统默认处理器；不要声称全部链接都能拦截。
8. Browser Tamer 6.0.2 的 `browser set default` 官方实现存在不调用实际 `set_default` 的源码缺口。此版本不要依赖该命令改变默认目标；使用界面或在用户明确授权后安全修改配置。

## 四层快速诊断（短路）

Browser Tamer 只负责把外部 `http:`/`https:` URL 路由到已登记的“浏览器 + 配置文件”。快速诊断固定按以下四层执行，并在第一处失败或无法确认时停止：

1. **OS URL / Browser Tamer 路由**：只读核对 `HTTP`、`HTTPS` 的 `UserChoice` 是否均为 `BrowserTamerHTM`，再核对 v6 配置存在且 `validate`、`list` 成功。任一失败返回稳定结论 `route_not_ready`，后续三层标记 `not_run`。
2. **浏览器配置选择器（profile chooser）**：使用 `list -AsJson` 返回的精确浏览器名和配置文件名，核对目标配置是否唯一、启动参数是否明确指定既有用户数据目录和配置文件标识。名称不唯一、参数缺失或实际弹出浏览器选择器返回 `profile_selector_not_ready`，后续两层标记 `not_run`；仅凭头像、显示名称或规则命中不得推断通过。
3. **浏览器进程**：核对配置中的浏览器可执行文件和目标进程是否存在，并在可读时核对其实际用户数据目录/配置文件参数。未运行返回 `browser_process_not_running`，参数无法核对返回 `browser_process_unverified`，后续扩展层标记 `not_run`。
4. **Codex 扩展连接**：仅用目标浏览器配置的扩展握手或 `agent.browsers.get(...)` 的直接结果判断。连接成功返回 `codex_extension_connected`，未安装、未启用或握手失败返回 `codex_extension_unavailable`；没有直接结果返回 `codex_extension_unverified`。

每轮输出至少包含 `conclusion` 和 `stop_after`；`conclusion` 只能取上述稳定结论之一或 `ok`，`stop_after` 只能取 `route`、`profile_selector`、`browser_process`、`codex_extension`、`none`。前层 `ok` 只表示该层的证据成立，不证明任何后层可用；`not_run` 也不得被解释为通过。不得用修改域名规则掩盖进程、配置选择器或扩展故障，也不得用浏览器窗口可见推断扩展已连接。

## 工具优先级

1. `scripts/browser-tamer-config.ps1`：状态、列表、校验、备份、规则新增和删除。
2. Browser Tamer 官方 CLI：只读列举/查询，或在用户明确授权后处理 URL、打开 picker、重新发现浏览器。
3. Browser Tamer 内置 Pipeline debugger：需要真实规则命中证据时使用。
4. 桌面视觉控制：仅在脚本和配置方式不适用时使用。

不要使用浏览器自动化管理 Browser Tamer。

## 快速开始

在本 skill 目录运行：

```powershell
& .\scripts\browser-tamer-config.ps1 status -AsJson
& .\scripts\browser-tamer-config.ps1 list -AsJson
```

把域名路由到指定配置文件：

```powershell
& .\scripts\browser-tamer-config.ps1 add-rule `
  -Browser "Google Chrome" `
  -Profile "Seth (4Seth)" `
  -Value @("openai.com", "chatgpt.com") `
  -Scope domain `
  -WhatIf

& .\scripts\browser-tamer-config.ps1 add-rule `
  -Browser "Google Chrome" `
  -Profile "Seth (4Seth)" `
  -Value @("openai.com", "chatgpt.com") `
  -Scope domain
```

删除指定规则：

```powershell
& .\scripts\browser-tamer-config.ps1 remove-rule `
  -Browser "Google Chrome" `
  -Profile "Seth (4Seth)" `
  -Value "openai.com" `
  -WhatIf
```

默认使用 `domain` 作用域，避免只因路径或查询参数包含相同文字而误命中。需要兼容原有“URL 任意位置”规则时显式使用 `-Scope any`。

## 标准工作流

### 检查状态

1. 运行 `status -AsJson`。
2. 核对 `version`、`config_path`、`http_handler`、`https_handler`。
3. 只有二者均为 `BrowserTamerHTM` 时，普通外部 `http:`/`https:` 链接才会先进入 Browser Tamer。
4. 运行 `list -AsJson`，确认目标浏览器、配置文件和现有规则。

### 修改规则

1. 使用精确的 `-Browser` 和 `-Profile`。
2. 先带 `-WhatIf` 预演。
3. 再执行真实修改。脚本会：
   - 严格按 UTF-8 读取；
   - 核对目标唯一；
   - 跳过重复规则；
   - 在配置目录的 `backups` 子目录创建备份；
   - 写入前检查原文件哈希，避免覆盖并发修改；
   - 使用同目录临时文件替换；
   - 重新解析并回读目标规则。
4. 再运行 `list -AsJson` 核验。
5. 用户要求真实命中测试时，优先用 Browser Tamer 的 Pipeline debugger；需要实际打开链接时先说明副作用。

### 排查未命中

依次核对：

1. `HTTP` 和 `HTTPS` 是否都关联到 `BrowserTamerHTM`。
2. 链接是否从外部应用交给系统默认处理器，而不是在现有浏览器/WebView 内部打开。
3. 是否是 `microsoft-edge:` 等显式协议。
4. 规则位置、作用域、正则开关是否符合预期。
5. 是否有多个规则同时命中并触发 picker。
6. Pipeline 变换（短链展开、Office 365 Safe Links、替换、Lua 脚本）是否改变了用于匹配的 URL。

### 排查配置文件选择器

路由本应打开指定配置文件却出现浏览器用户选择界面时：

1. 用 `list -AsJson` 回读规则实际绑定的浏览器名和配置文件名，不能只看头像或显示名猜测。
2. 核验该 Browser Tamer 配置项对应的真实配置目录标识，例如 Chromium 浏览器的 `Default`、`Profile 1`；显示名称与目录标识不是同一概念。
3. 直接启动浏览器进行对照验证时，必须同时使用该浏览器的既有用户数据目录和精确 `--profile-directory`。不要复制配置目录，也不要读取 Cookie、密码或浏览器存储。
4. 浏览器自身“启动时显示配置文件选择器”是全局交互偏好。除非用户明确要求，不要为了自动化关闭它；自动化应精确指定配置文件，避免依赖该全局开关。
5. 不用 `bt.exe <URL>` 充当无副作用的浏览器唤醒器。它会打开真实 URL/标签；只想恢复浏览器进程或扩展连接时，使用不带 URL 的受限浏览器启动方式，再单独核验连接。

### 排查 Codex 浏览器插件

把故障至少区分为“浏览器未运行”“扩展不可用”“登录态不可用”和“页面结构异常”。Browser Tamer 能帮助确认目标配置文件是否正确，但不能修复或证明扩展实时连接：

浏览器窗口或进程可见，与桌面连接仍报告 `browser_not_running` 并不矛盾：该状态也可能表示当前连接没有拿到目标配置文件的扩展句柄。先核对精确配置和扩展握手，不要仅凭窗口存在断言插件版本损坏，也不要在没有可复现实验证据时归咎于用户操作。

- `browser_not_running`：先以精确配置启动浏览器，再重试一次连接。
- `extension_unavailable`：核验目标配置中的扩展是否启用并与桌面端连接；不要反复改域名规则。
- `login_unavailable`：交给对应站点登录流程处理；不得读取、复制或导出登录资料。
- 页面结构异常：属于目标网页采集问题，不应改 Browser Tamer，也不应仅因此切换浏览器配置。

自动化如果需要“只启动正确配置、但不打开 URL”，不得调用 Browser Tamer；使用受限启动器，并禁止 URL、任意参数透传、远程调试和会话恢复参数。Browser Tamer 仍只负责用户或外部应用真正打开链接时的路由。

## 官方 CLI 边界

Browser Tamer 6.0.2 包含以下入口：

```powershell
bt.exe
bt.exe <URL>
bt.exe pick <URL>
bt.exe discover
bt.exe browser list
bt.exe browser get default
bt.exe browser set default <browser-name>.<profile-name>
```

CLI 没有新增、删除或编辑规则的命令。Windows 版本会把终端输出重新绑定到 `CONOUT$`，部分自动化宿主无法捕获 `browser list/get default` 的标准输出，因此机器可读场景优先解析配置文件。

## 版本与配置

- v6 Windows 默认配置：`%APPDATA%\Browser Tamer\config.yml`
- v6 配置会在后台自动保存和重新加载。
- v5 及更早版本使用过 `config.ini`、LocalAppData 或便携模式，不能套用 v6 YAML 修改逻辑。

需要核对版本依据、YAML 字段或 CLI 源码边界时，读取 [references/official-6.0.2.md](references/official-6.0.2.md)。

## 输出约定

1. 先报告检测到的版本、配置路径和默认处理器状态。
2. 变更后报告目标 `浏览器 → 配置文件`、新增/删除的规则、备份路径和回读结果。
3. 若只完成配置但 Browser Tamer 尚未成为默认处理器，明确说明规则已保存但外部链接尚不会经过它。
4. 若命中范围存在绕过场景，明确说明来源应用或协议限制。
5. 若问题实际位于浏览器进程或 Codex 扩展层，明确报告 Browser Tamer 路由是否正常，并把“路由正常”与“浏览器/扩展可用”分开下结论。
