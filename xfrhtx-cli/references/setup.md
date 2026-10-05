# 独立安装与跨电脑初始化

工具仓库：`git@github.com:dmdmwshr/xfrhtx-reader.git`，网页 [xfrhtx-reader](https://github.com/dmdmwshr/xfrhtx-reader)。工具发布：[Releases](https://github.com/dmdmwshr/xfrhtx-reader/releases)。Skill 来源：`dmdmwshr/custom-skills` 中的 `xfrhtx-cli`。工具要求 `0.2.0+`，JSON 结构版本 `1.0`。

这两个组件分别取得、分别安装。源码、工具环境和业务数据不得放进 Skill 目录。工具安装根目录可根据目标机器选择，例如当前用户 `%LOCALAPPDATA%\Programs\XFRHTXReader`；不是固定到某个用户名或磁盘。工具仓库当前为私有，需要目标机既有授权访问，也可由用户提供独立工具压缩包，不复制其他电脑的凭据。

## 初始化步骤

1. 确认目标执行端为 64 位 Windows。读取目标机已有的工具安装回执或调用本技能 `scripts/run.ps1 -ToolArguments @('--version')`。存在多个入口时确定唯一目标；不重新安装已符合要求的版本。
2. 在独立工具目录取得已发布版本源码或压缩包，压缩包对照附带 SHA-256 校验值；工具自己的 `source-manifest.json` 还会校验包内源码。不要把聊天数据库、附件或原账号目录当成安装材料。
3. 先读工具包 `INSTALL.md`，执行其 `deployment/initialize.ps1`。此脚本属于工具，不随 Skill 复制。已明确授权初始化时，必要依赖安装属于该范围。

   ```powershell
   & "<独立工具源码目录>\deployment\initialize.ps1"
   ```

   可用 `-CheckOnly` 只检查；`-InstallRoot <目录>` 隔离工具；`-Offline` 禁止安装联网；`-UvPath <已有 uv.exe>` 使用目标机现有入口。缺少依赖时按实际结果处理，不更换代理或放宽证书检查。

4. 初始化用目标机现有 uv，缺失时按官方方式安装 uv，再由 uv 准备 64 位 Python 3.12 和独立工具环境。普通安装不依赖源码目录长期存在。默认不改写 PATH；使用返回的 `entry_point` 即可调用。
5. 读回初始化 JSON 的 `installed`、`version`、`entry_point` 与 `client_ready`。安装完成而客户端未登录时，明确写“工具已安装，消息读取待登录后验证”，不要重启或代为登录客户端。
6. 在该电脑实际登录融合通信后，通过本 Skill 运行 `status --json`；再列一次小范围会话或读取用户指定的少量消息。不要为初始化测试发送消息、批量导出或自动下载附件。

默认回执为当前用户 `%LOCALAPPDATA%\XFRHTXReader\installation.json`。自定义 `InstallRoot` 时回执就在该目录，之后给 `run.ps1` 同一 `-InstallRoot`。回执只能用于定位，仍须执行当前状态检查。

## 单独安装 Skill

优先使用目标电脑现有 Skill 管理器。从 `dmdmwshr/custom-skills` 只选择 `xfrhtx-cli`，保留完整 `SKILL.md`、`agents/`、`scripts/` 和 `references/`，不要复制整个源仓为一个技能。

Windows CC Switch 受管环境遵循该电脑实际配置和受管同步流程；不套用原电脑数据库或绝对路径。没有 CC Switch 的客户端使用其本身支持的 Skill 安装方式，并在新进程核对 `xfrhtx-cli` 已启用、没有重复入口或加载错误。安装 Skill 不等于已经安装工具，也不表示业务数据可读。

## 兼容性与升级

当前工具适配特定的 9.0.271.0 客户端组件。相同软件名称或版本文字不保证二进制组件相同；实际以工具组件核验为准。升级后的组件未适配时，不把旧偏移或其他用户的读取材料硬套到新客户端。

工具升级只执行工具初始化，Skill 升级只更新本 Skill。保留原数据、缓存、导出和失败回执。报告已测试的具体环境，不把本机新路径测试称为另一台物理电脑已验收。
