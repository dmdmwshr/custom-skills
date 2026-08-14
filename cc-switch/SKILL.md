---
name: cc-switch
description: 通过终端管理本机 CC Switch 的自建及已登记 skills 仓库源、提交推送、备份、安装更新、SQLite 登记、启停、分支和排障。维护 CC Switch 受管 skill 时使用；只有用户明确选择非受管来源时才改用 skill-installer。
---

# CC Switch

本 skill 集中管理本机 CC Switch 的自建 skills 仓库源、源仓库内容、安装副本和 SQLite 登记。日常安装与更新默认走终端脚本；桌面应用只用于用户明确要求的只读检查或终端链路故障后的人工排查，不作为常规点击入口。

当前用户在当前请求中明确指定 skill、仓库和新建、修改、同步、启停或精确删除动作时，视为已授权该范围及必要的验证步骤；常规可恢复操作不重复确认。只有删除范围不清、仓库级删除或可能造成重大且难以恢复的损失时，才一次性确认精确对象、影响和备份位置。

## 适用范围

- 查询 C:\Users\12070\.cc-switch\cc-switch.db 中的 skill_repos 与关联 skills 记录。
- 管理自建源 dmdmwshr/custom-skills 的提交、推送、自动安装、自动更新、备份和安装副本核验。
- 添加、启用、停用、改分支或删除其他 skills 仓库源。
- 管理自建 skill 的新增、修改、删除和多客户端启用状态。
- 排查源仓库、安装副本、数据库登记、同步脚本和 CC Switch 桌面应用状态。

## 硬规则

1. 先做只读状态检查，再执行任何变更。
2. 自建 skill 的安装与更新默认只运行 scripts/sync-custom-skills.ps1；不通过可视化应用点击完成常规同步。终端链路失败时报告具体原因并停止，不要静默改走 GUI。
3. 自建 skill 内容只在 C:\Users\12070\.cc-switch\skills\自建skills 源仓库中修改；C:\Users\12070\.cc-switch\skills 下的安装副本只由同步脚本更新。
4. 同步前必须确认源仓库已审查、测试、提交并推送，且本地分支为 main、工作区干净、HEAD 与 origin/main 一致；不满足时拒绝安装或更新。
5. 直接写 SQLite 前必须先创建可恢复备份。同步脚本会先做 SQLite 在线备份，再更新安装副本和 skills 登记。
6. C:\Users\12070\.local\bin\cc-switch.cmd 是失效旧 CLI shim，指向不存在的 D:\Program_Files\CC-Switch-CLI\current\cc-switch.exe；不得把它当作可用入口，也不得为绕过门禁重新使用它。
7. Windows PowerShell 中不得用 Copy-Item -LiteralPath "<src>\*" 复制通配符；必须枚举子项并以 LiteralPath 逐项复制。
8. 源目录、安装副本和备份目标中的重解析点（符号链接、联接等可能跳出边界的路径）一律拒绝处理。
9. 不知道 CC Switch 的 skills.content_hash 算法时，不手工计算或覆盖 content_hash；同步脚本只更新来源、目录、元数据、时间和登记状态，保留已有 hash，新记录留空。
10. 同步脚本只添加或更新源仓库当前列出的 skill，不因源目录缺失自动删除安装副本或数据库记录；删除必须按单个 skill 明确核对、备份和执行。
11. 不为 cc-switch 的零散子功能新建独立 skill；相关管理功能集中维护在本 skill 及其 scripts 目录中。
12. CC Switch 桌面应用、随附脚本和数据库结构可能随版本升级变化；不得把当前表、字段、目录、启用开关、迁移方式或 content_hash 规则当成永久契约。发现版本或结构变化时先停在只读检查，确认兼容性并备份后再写入。

## 版本升级与数据库结构兼容性备注

CC Switch 可能自动或手动升级。升级可能改变 cc-switch.db 的表、字段、索引、约束、SQLite user_version、settings 配置、安装目录、客户端开关和 content_hash 算法，也可能改变应用对仓库源和安装副本的同步方式。当前脚本只针对已核验的本机结构，不代表未来版本永久兼容。

每次发现 CC Switch 版本变化、数据库更新时间异常、同步报错或应用完成迁移时，按以下顺序处理：

1. 先停止写入，读取应用版本、SQLite PRAGMA user_version、sqlite_master，以及 skill_repos 和 skills 的 PRAGMA table_info；记录实际表名、字段、类型、默认值和约束。
2. 对照 scripts/skill-repos.py、scripts/sync-custom-skills.ps1 的预期字段做只读兼容性判断；缺少字段、字段类型或约束异常、出现新必填字段、hash 规则不明时，标记为结构不兼容并停止，不猜测、不强行迁移。
3. CC Switch 升级前和任何直接写库前都保留 SQLite 备份。应用已经把数据库迁移到新结构后，不要未经确认直接用旧备份覆盖新数据库；恢复前先确认版本和迁移方向可逆。
4. 适配新版本后先跑 dry-run，再核对安装副本、仓库源、skills 登记和启用状态；不要因为旧版本的 content_hash 或表结构看起来相似，就声称新版本已兼容。

## 自建 skill 生命周期

- 修改已安装的自建 skill：只改源仓库，保持目录名与 frontmatter 的 name 一致；完成审查和测试后提交、推送，再运行终端同步脚本；最后核对源/安装副本、数据库来源和启用状态。
- 新建自建 skill：先在源仓库创建并验证 SKILL.md、agents/openai.yaml 和必要脚本，提交并推送；用户要求安装时直接运行终端同步脚本，不要求安装时只完成源仓库交付。
- 删除自建 skill：先查询 skills 表确认 repo_owner=dmdmwshr、repo_name=custom-skills 和影响范围；备份安装副本与数据库；再对源目录、安装副本和对应登记做精确删除。单删 skill 不删除整个 dmdmwshr/custom-skills 仓库源。
- 修改仓库源、分支、启用状态或删除仓库源：先用 scripts/skill-repos.py 做 dry-run，再在用户授权后带 --yes 执行；删除前列出关联 skills，默认只删 skill_repos 记录。

## 终端优先同步流程

1. 在源仓库完成检查、测试、提交和推送。不要把未提交文件或未推送提交交给同步脚本。
2. 先运行 dry-run，只检查源仓库、远端对齐、skill 数量、安装目标和备份位置，不写数据库、不复制文件：
   ~~~powershell
   & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -File "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\sync-custom-skills.ps1" -WhatIf
   ~~~
3. dry-run 通过后运行同一脚本完成同步。默认会 fetch --prune、pull --ff-only、创建带时间戳的备份、逐项复制源 skill、更新 SQLite 登记并保留已有 content_hash：
   ~~~powershell
   & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -File "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\sync-custom-skills.ps1"
   ~~~
4. 只在已经单独确认远端引用有效、且当前确实需要离线运行时使用 -SkipRemotePull；该参数不会跳过本地干净和 HEAD 对齐检查。
5. 同步后用固定 Python 查看仓库源和关联 skills，并比较关键文件或全目录 SHA-256；发现不一致时保留备份并停止继续覆盖：
   ~~~powershell
   $py = "C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe"
   & $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" show --owner dmdmwshr --name custom-skills
   git -C "C:\Users\12070\.cc-switch\skills\自建skills" status --short --branch
   ~~~

## 备份与恢复边界

每次真实同步都会创建 C:\Users\12070\.cc-switch\skill-backups\<时间>-custom-skills-before-sync，至少包括：

- cc-switch.db：SQLite 在线备份。
- installed-copy：同步前已有安装副本的完整副本。
- previous-live：替换前安装副本的可恢复移动副本。
- staging：本次同步前准备的源 skill 暂存副本。

脚本只操作源仓库列出的精确 skill 目录，不删除其他安装内容。同步中途出现异常时，先报告错误和备份位置，不删除备份、不强行重试；恢复必须根据备份目录逐项核对后执行。

## 数据库约定

- 自建主源必须在 skill_repos 中登记为 owner=dmdmwshr、name=custom-skills、branch=main、enabled=1；不存在或停用时，同步脚本拒绝写入。
- 同步脚本按 owner/repository:name 登记 skills，更新名称、描述、目录、来源、分支、说明链接和时间。
- 已存在 skill 的多客户端启用开关保持不变；新登记的 skill 默认启用当前四个主客户端，较新的额外客户端保持数据库默认关闭。
- content_hash 只读保留，不把文件 SHA-256 冒充 CC Switch 内部 hash。

## 默认流程

1. 使用固定 Windows PowerShell、固定 Python 3.12 和 Git 做只读预检。
2. 自建 skill 先在源仓库审查、测试、提交和推送，再用 sync-custom-skills.ps1 dry-run。
3. dry-run 通过后运行真实同步，读取输出的备份位置和数量。
4. 用 skill-repos.py show、源/安装副本比较和 git status 做回读核验。
5. 只有用户明确要求排查 CC Switch 桌面应用时，才检查 C:\Users\12070\AppData\Local\Programs\CC Switch\cc-switch.exe 的文件或进程状态；不因常规同步主动打开、点击或刷新应用。

## 脚本用法

同步脚本位于本 skill 的 scripts/sync-custom-skills.ps1，仓库源登记脚本位于 scripts/skill-repos.py。推荐使用固定解释器：

~~~powershell
$ps = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$py = "C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe"
$sync = "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\sync-custom-skills.ps1"

& $ps -NoProfile -File $sync -WhatIf
& $ps -NoProfile -File $sync
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" list
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" show --owner dmdmwshr --name custom-skills
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" add --owner owner --name repo --branch main --enabled 1 --dry-run
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" enable --owner owner --name repo --enabled 0 --dry-run
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" set-branch --owner owner --name repo --branch main --dry-run
& $py -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\cc-switch\scripts\skill-repos.py" remove --owner owner --name repo --dry-run
~~~

任何真实删除都必须先 dry-run，并且不得拿官方源或自建主源做测试性删除。
