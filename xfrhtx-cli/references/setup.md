# 独立安装与跨电脑初始化

工具：[xfrhtx-reader](https://github.com/dmdmwshr/xfrhtx-reader)，[发布包](https://github.com/dmdmwshr/xfrhtx-reader/releases)。Skill：`dmdmwshr/custom-skills` 中的 `xfrhtx-cli`。当前要求工具 **0.3.0+**，JSON 结构版本 1.0。

工具和 Skill 分别取得、分别安装。源码、运行环境和业务数据不得放入 Skill 目录。目标为原生 64 位 Windows，保持该电脑融合通信已登录；当前适配 9.0.271.0 特定组件。安装路径、账号和数据目录从目标机实际状态识别，不使用开发电脑的路径。

## 便携版：优先

1. 在目标电脑核对现有入口及版本；已有符合要求且可读取的工具可以继续使用，不重复安装。
2. 取得独立的 `*-windows-x64.zip`，核对配套 SHA-256 并完整解压至用户指定的工具目录。私有仓库使用目标机已有授权，也可直接复制用户提供的工具包；不复制其他电脑的凭据或通讯录。
3. 包含 exe 和运行库，无需 Python、uv 或联网安装依赖。阅读包内 INSTALL.md，先执行 `initialize.ps1 -CheckOnly` 检查文件和客户端，再在已授权初始化时执行 `initialize.ps1`。
4. 调用本 Skill 时指定 `-InstallRoot <便携包目录>`。定位脚本支持包内相对入口，整包移动后按新目录调用。无需预先生成回执也可从显式目录内的便携清单定位 exe；执行前仍核对工具版本。
5. 若需要设为该电脑默认入口，由工具的 `initialize.ps1 -Register` 写本机登记。登记包含在目标机自动解析的当前入口，不随包分发。登记后移动包，应在新位置重新登记，或显式指定 InstallRoot。
6. 通过 Skill 执行 `status --json`，再执行用户指定的单人或小范围通讯录查询，核对单位层级与结果来源。不要为初始化验收批量导出通讯录、发送消息或下载附件。

示例只使用目标目录占位，实际路径由目标机确定：

```powershell
& "<Skill 目录>\scripts\run.ps1" -InstallRoot "<独立工具目录>" -ToolArguments @('status', '--json')
& "<Skill 目录>\scripts\run.ps1" -InstallRoot "<独立工具目录>" -ToolArguments @('contacts', '姓名', '--unit', '单位名称', '--exact', '--json')
```

工具初始化回读 `installed`、`version`、`client_ready`。`-SkipClientCheck` 返回 `client_ready=null`；安装或解压成功不能代替业务就绪。客户端未登录时保留工具安装状态，说明待登录验证，不启动或代为登录。

## 常规安装或源码版

已有 uv 管理习惯的机器可用独立 `*-source.zip` 或工具源码，按其 INSTALL.md 执行 `deployment/initialize.ps1`。该入口属于工具，不复制到 Skill 中。

初始化自动查找 uv，必要时按官方方式安装，再准备 Python 3.12 和独立工具环境。支持 `-CheckOnly`、`-InstallRoot`、`-Offline`、`-UvPath`；离线首次安装需要依赖已存在或缓存齐全。普通安装不依赖源码目录长期存在，默认不改 PATH。使用返回的 entry_point，或由本 Skill 读取当前用户的安装回执定位。

默认回执在目标用户 `%LOCALAPPDATA%\XFRHTXReader\installation.json`；自定义根目录时回执在该目录。绝对定位记录仅由目标机运行时生成，不是可复制的跨电脑配置。

## 单独安装 Skill 与升级

优先使用目标电脑已有 Skill 管理器，仅选择 `xfrhtx-cli`，保留 SKILL.md、agents、scripts、references 等完整目录。Windows CC Switch 受管环境遵循目标机的同步流程；其他客户端使用其原生 Skill 安装方式。新进程检查启用、唯一入口及加载错误，然后验证实际工具调用。

工具升级只更新独立工具，Skill 升级只更新本 Skill。当前读取与已验证客户端组件绑定，组件不匹配时需适配；不硬套旧偏移。保留原数据库、缓存、导出和失败回执。明确区分同机异目录测试与另一台实体电脑的实际验收。