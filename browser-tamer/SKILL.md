---
name: browser-tamer
description: 在 Windows 上诊断和管理 Browser Tamer 的安装、HTTP/HTTPS 默认处理器、浏览器配置文件、域名路由、v6 config.yml 与外部链接未命中问题。优先使用官方 CLI、脚本和配置文件，只有任务需要时才使用桌面视觉控制。
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
