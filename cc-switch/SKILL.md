---
name: cc-switch
description: 通过终端管理本机 CC Switch 的 Skill：版本与数据库结构兼容检查、检查第三方/官方 Skill 更新并生成可审阅清单、按用户范围经社区 CLI 单项或批量更新（先备份、回读启用开关与摘要）、自建 skills 仓库的精确发布（提交推送后同步、content_hash 记录）、仓库源登记、启停与排障。维护 CC Switch 受管 skill 或用户说“更新 skill / 有 N 个可更新”时使用；只有用户明确选择非受管来源时才改用 skill-installer。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# CC Switch

执行平台：Windows（脚本在 Windows 上运行；WSL 里的 Claude/Codex 经 `/mnt/c` 或 `powershell.exe` 调用同一份）。先核对本机版本、数据库结构与用户授权；安装、发现和业务验收分别报告。

CC Switch 管三类东西，更新路径不同，**不要混用**：

| 对象 | 更新方式 |
| --- | --- |
| 自建源 `dmdmwshr/custom-skills`（50 个自建 Skill） | 源码主仓在 WSL；单次任务内“提交→推送→精确发布→发现回读”闭环，走 `scripts/sync-custom-skills.ps1`。**没有后台自动更新**：库里 `skill_repos.enabled=1` 只表示“登记为启用的源”，不会定时拉取；不得为此新建计划任务 |
| 第三方/官方源（openai、anthropics、kdocs、obsidian…） | 社区 CLI `cc-switch-cli skills check-updates / update`，经本 Skill 的 `scripts/cc_switch_control.py` 门禁、备份与回读 |
| 桌面 GUI（用户在界面点“更新”） | 用户自己的操作；GUI 与 CLI 共用同一个库和安装目录，二者不要同时写 |

当前用户在本次请求里明确指定 Skill、范围和动作时，视为已授权该范围及必要的验证；可恢复的常规操作不重复确认。删除范围不清、仓库级删除、可能难以恢复的损失才集中确认一次。

## 硬规则

1. 先只读检查（`doctor`），再写。任何写库前先备份 SQLite。
2. 自建 Skill 内容只在 WSL 源仓 `/srv/workspaces/02-software/02-25-custom-skills` 修改。Windows 只用**干净的发布镜像**（该仓 main 的 Windows 检出，`core.autocrlf=false`）并显式给 `-SourceRoot`；旧的 `C:\Users\12070\.cc-switch\skills\自建skills` 是带大量未提交差异的遗留检出，Slack 机器人还读它的兼容路径，**不得在其中开发、`git pull` 或整体覆盖**。`sync-custom-skills.ps1` 的 `-SourceRoot` 已改为必填。
3. 安装副本（`C:\Users\12070\.cc-switch\skills\<名>`）和客户端目录（如 `C:\Users\12070\.codex\skills`）只由同步脚本或 CLI 更新，不手改。
4. 同步前源仓必须已审查、测试、提交并推送，发布镜像在 main、工作区干净、HEAD 与 origin/main 一致；脚本不满足即拒绝。
5. `content_hash` 是 CC Switch 判断“可更新”的依据。同步脚本现在会按上游算法写入（见下），**仅当数据库结构版本在 `scripts/cc-switch-compat.json` 的 `content_hash_verified_schemas` 内**；其它版本保持“保留旧值、新记录留空”并输出 `content_hash_not_written`。不得用普通文件 SHA-256 冒充，也不得手工 SQL 改 hash。
6. 重解析点（符号链接/联接）在源目录、安装副本、备份目标中一律拒绝；Codex 受管入口只能精确指向对应安装副本。
7. 不为零散功能新建 Skill、常驻服务或计划任务；管理功能集中在本 Skill 与其 scripts。
8. 同步脚本只添加/更新源仓当前列出的 skill，不因源目录缺失删除安装副本或库记录；删除须按单个 skill 核对、备份、执行。
9. 禁止把 CLI 指向正式库去“试一下”：CLI 的任何 `skills` 子命令启动时都会做数据库迁移/恢复，不是只读。只有 `--version`、`--help` 不进入启动状态。

## 版本与结构兼容（先读这里）

Desktop（4.x）和社区 CLI（5.x）分别发版，数据库用 `PRAGMA user_version` 表示结构版本，**两者必须一致**，且每次升级都可能改表、改开关列、改 hash 规则。已核验对照写在 `scripts/cc-switch-compat.json`：Desktop 4.0.18→结构 21、4.0.19→22；CLI 5.11.3→22、5.11.0–5.11.2→20。表里没有的版本一律视为 unknown，不猜测、不拿“最大值”代替。

`doctor` 的结论：

| verdict | 含义 | 处理 |
| --- | --- | --- |
| `ready` | CLI 结构版本 = 库结构版本，Desktop 不低于库 | 可检查/更新 |
| `needs_coordinated_upgrade` | CLI 与库结构版本不同（CLI 会迁移库或拒绝，且迁移后旧 Desktop 可能打不开） | 停止；由用户决定先升级 Desktop（关闭重开）再装对应 CLI，**不绕过版本拒绝** |
| `cli_missing` / `cli_unusable` / `cli_version_unknown` / `desktop_version_unknown` | 缺少或未核验的版本组合 | 停止并报告；不自动下载/升级 |
| `desktop_running`（写命令） | 桌面正在运行 | 须用户决定关闭，或明确加 `--allow-desktop-running` |

CLI 稳定入口（待安装后使用）：`C:\Users\12070\.local\share\codex-tools\cc-switch-cli\current\cc-switch-cli.exe`；测试/隔离用 `--cli` 或环境变量 `CC_SWITCH_CLI` 覆盖，不把临时路径写成默认。

## 工作流 A：检查并更新第三方/官方 Skill（用户说“有 58 个可以更新”）

固定入口（Codex 物理副本；发布后可用，或直接用源码路径）：

~~~powershell
$py  = "C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe"
$ctl = "C:\Users\12070\.codex\skills\cc-switch\scripts\cc_switch_control.py"
& $py -X utf8 $ctl doctor                          # 只读：版本/结构/开关/自建摘要/兼容结论（不运行桌面，不迁库）
& $py -X utf8 $ctl check-updates --plan-out C:\path\updates-plan.json
~~~

1. **doctor**：verdict 不是 `ready` 就停，把 `reasons` 原样告诉用户。
2. **check-updates**：先备份库，再调用 CLI 的 `skills check-updates`（它会回填缺失的 hash，所以不是只读，已备份），生成 `updates-plan.json`。每个条目含目录、来源仓库、各客户端启用开关、库内 hash、安装副本 hash、`problems`：
   - `custom_repo_use_sync_script`：自建源，走工作流 B，不经 CLI；
   - `local_modified`：安装副本与库内 hash 不一致（有人手改过）；
   - `ambiguous_directory` / `not_in_database` / `installed_copy_missing`：不可自动更新。
   清单默认 `selected:false`。把清单摘要（有问题的与可更新的分开）给用户，**让用户选范围**：全部可更新项、指定若干项、或不更新。
3. **update**（先 dry-run，再 `--confirm`）：

~~~powershell
& $py -X utf8 $ctl update --plan C:\path\updates-plan.json            # dry-run，列出将更新的项
& $py -X utf8 $ctl update --plan C:\path\updates-plan.json --confirm  # 执行（清单里 selected=true 的项）
& $py -X utf8 $ctl update --skills alpha,beta --confirm               # 或明确点名
~~~

   - 范围内任何一项有 `problems`、或自上次生成清单后安装副本又变了（`drifted_since_plan`），**整批拒绝且不调用 CLI**；先解决或从范围剔除。
   - 执行前备份库和所选安装副本到 `skill-backups\<时间>-cli-update\`；逐项调用 `cc-switch-cli skills update <目录>`，**不使用 `--all`**（`--all` 会把自建源也从 GitHub 覆盖下来）。
   - 每项回读：各客户端启用开关与更新前一致（被关掉的“前门后端”不会被重新打开）、库内 hash 等于安装副本 hash、各已启用客户端目录内容一致。结果为 `updated` / `failed` / `unverified` / `drift`（开关被改，停止整批）/ `unknown`（超时，停止整批，不重放）。
   - 退出码：0 全成功；1 有失败；3 门禁/范围拒绝；4 结果未知。失败逐项留证，不自动重试；普通已确认失败可在原授权内修正重试，未知结果先对账，不重放。
4. **verify**：`& $py -X utf8 $ctl verify [--skills a,b]` 回读安装副本摘要、库内摘要、客户端目录；不带 `--skills` 时检查全部自建项。

“58 个可更新”这类数字是 GUI 当时的检测；以本流程重新 `check-updates` 的实际清单为准。

## 工作流 B：更新自建 Skill（单次任务内闭环）

1. 在 WSL 源仓改、审查、测试，精确暂存并提交，推送 `origin/main`，用 `git ls-remote origin refs/heads/main` 回读与本地一致。
2. WSL 受管 Skill（`platforms.json` 的 `managed_wsl_order`）用 `python3 scripts/publish_wsl_skills.py --skill <名>` 预演、`--apply`、`verify_wsl_skills.py`；Windows 专用 Skill 不装到 WSL。
3. Windows：更新发布镜像到刚推送的提交，再 dry-run、真实同步（`-Skill` 限定本次对象；每次同步都带 `-SourceRoot`）：

~~~powershell
$mirror = "D:\12070\Documents\claude-codex\claude-enhancement-20261022\windows-mcp-v1-a1-resume\release-checkout"
git -C $mirror fetch origin; git -C $mirror merge --ff-only origin/main      # 镜像须 core.autocrlf=false，工作区干净
$sync = "$mirror\cc-switch\scripts\sync-custom-skills.ps1"
$ps = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
& $ps -NoProfile -Command "& '$sync' -SourceRoot '$mirror' -SkipRemotePull -Skill 'skill-a','skill-b' -WhatIf"
& $ps -NoProfile -Command "& '$sync' -SourceRoot '$mirror' -SkipRemotePull -Skill 'skill-a','skill-b'"
~~~

   （用 `-Command` 才能传多个 `-Skill`；`-File` 会把逗号列表当成一个字符串。）脚本会：备份库与现有安装副本→只复制 Git 已跟踪文件→登记 SQLite（保留各客户端启用开关；新记录仅启用 Codex）→**按上游算法写入 content_hash**（仅已核验结构版本）→按 Auto 规则精确发布到已启用的 Codex / Gemini 发现目录（两者同批回滚）；任一步失败，安装副本和库恢复到同步前。
4. 回读：`& $py -X utf8 $ctl verify --skills skill-a,skill-b`（库内 hash=安装副本 hash=客户端目录）；源/镜像/安装/Codex/Gemini 目录逐文件比较；独立 Codex 进程只读核对发现（不发起模型回合）。GUI 此后不应再把刚发布的自建项显示为“待更新”。
5. 机器人旧路径等外部消费者是否需要同步，按各自项目规则另行处理，不在此脚本里覆盖。

## content_hash 算法（已核验，Desktop 4.0.18 / 4.0.19）

上游 `compute_dir_hash`：递归目录下所有**非点开头**的文件/目录；文件按相对路径**组件字节序**排序（`a/b` 排在 `a-b/c` 之前，不是整串字符串排序）；逐文件向同一个 SHA-256 喂入 `UTF8(相对路径，/ 分隔) + NUL + 原始字节 + NUL`。`cc_switch_control.py hash <目录>` 和同步脚本用同一实现；本机 50 个自建安装副本与库内 hash 逐项吻合。已比对 Desktop 4.0.18 / 4.0.19 的两段完整上游函数，逐字相同；本同步器已核验结构21和22。其他结构在重新核验前保留旧hash、新记录留空并明确提示。

## 备份与恢复

- 自建同步：`C:\Users\12070\.cc-switch\skill-backups\<时间>-custom-skills-before-sync`（cc-switch.db、installed-copy、previous-live、failed-install-new、staging、codex-*）。
- CLI 检查/更新：`...\skill-backups\<时间>-cli-check-updates` 与 `<时间>-cli-update`（cc-switch.db、settings.json（原来存在时）、installed-before\<目录>、update-result.json）。
- 异常时先报告备份位置，不删除备份、不强行重试；恢复前核对库的结构版本方向，**不要用旧结构备份覆盖已迁移的新库**。

## 仓库源与其他操作

`scripts/skill-repos.py list|show|add|enable|set-branch|remove`（默认只读或 `--dry-run`，真实写入带 `--yes`，不对官方源和自建主源做测试性删除）。删除自建 Skill：确认 `repo_owner=dmdmwshr、repo_name=custom-skills`，备份安装副本与库，再精确删除源目录、安装副本和登记，单删不删整个源。桌面应用只在用户明确要求排查时检查文件/进程，不为常规流程打开或点击。

## 升级本身

CC Switch 升级是版本组合问题：`doctor` 先报版本与结构；升级（桌面 4.0.19、CLI 5.11.3 等）需要用户决定关闭重开桌面，由根 Codex/维护者执行并记录恢复点，装好后更新 `cc-switch-compat.json` 的已核验项并重跑本 Skill 的测试，再恢复工作流 A/B。本 Skill 不自动下载、安装或升级 Desktop/CLI。

## 测试

`scripts/test_cc_switch_control.py`（模拟 CLI + 合成库，覆盖版本拒绝、范围、手改/并发漂移、开关保持、失败/超时/未知、回读）；`scripts/test_selected_skill_sync.py`（Windows，真实 PowerShell 同步脚本 + 隔离夹具，覆盖 hash 写入/保留/未知结构）。

## 原生CLI目录绑定与隔离

- 备份、检查和原生CLI必须绑定同一个配置目录；`--db` / `--ssot-root` 只能确认该目录下的 `cc-switch.db` / `skills`，不能另指一套对象。当前仅核验 `skillStorageLocation=cc_switch`。
- 生产客户端目录从正式设置及 `CODEX_HOME` / `CLAUDE_CONFIG_DIR` 读取；生产 `--client-dir` 只能核对既有真实映射，不能另设校验对象。SSOT禁止重解析点；Codex等客户端的精确SSOT受管链接允许，其余根、嵌套或越界链接拒绝。
- `--config-dir` 指定另一套状态时进入隔离测试：目录必须与真实CC/客户端根互不重叠，所有 `--client-dir` 必须在该配置目录内并以 `skills` 为末级（原生CLI固定在配置根追加该目录名）；只在CLI写命令已备份后写入影子settings的正式目录覆盖，并对该子进程设置 `CC_SWITCH_CONFIG_DIR`、`CC_SWITCH_TEST_HOME`、`CODEX_HOME`、`CLAUDE_CONFIG_DIR` 等。只改变HOME/USERPROFILE不足以隔离Windows Known Folder API。
- 所有 `enabled_*` 列动态保留和冻结；未知且启用的客户端明确阻断，不能漏验后报成功。当前实际schema22是9个客户端开关（含kimi），不把未映射/未核验算通过。

自建发布器当前覆盖实际使用的 Codex / Gemini；所选 Skill 还有其他启用客户端时在写入前明确拒绝，需先核验并适配对应发布路径。其余行的开关不改。生产 Gemini 路径从正式设置 geminiConfigDir 或本机默认位置取得；非生产数据库必须显式传 `-GeminiSkillsRoot`，不会继承真实客户端。`-SkipCodexClientSync` 保留历史参数名，代表跳过整个客户端发布阶段，仅用于明确隔离的恢复或元数据测试。客户端阶段的暂存、旧副本、失败副本仍沿用 codex-* 备份目录，内部按客户端分区；任一客户端发布失败会恢复同批全部客户端，再由外层恢复安装副本和库。
