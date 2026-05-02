---
name: cc-switch
description: 用于管理本机 CC Switch 桌面应用相关的 skills 仓库源、同步来源和 SQLite 记录，包括自建 skill 修改同步、新建安装提示、删除清理、仓库源查询、启停和排障；当前以桌面应用和随附 SQLite 脚本为准，不依赖旧 CLI shim。
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# CC Switch

本 skill 用于管理本机 `CC Switch` 桌面应用相关配置，重点是 skills 仓库源的查询、增加、启停、改分支和删除。

## 适用范围

- 查询 `C:\Users\12070\.cc-switch\cc-switch.db` 中的 `skill_repos` 与关联 `skills` 记录。
- 添加新的 skills 仓库源。
- 启用或停用已有仓库源。
- 修改已有仓库源的分支。
- 删除仓库源，并在删除前列出关联 skills。
- 管理自建 skills 的修改同步、新建安装提示、删除清理和多客户端启用状态核对。
- 排查 CC Switch 桌面应用、settings、数据库、同步来源、仓库源和安装副本状态。

## 硬规则

1. 先查询当前状态，再执行任何变更。
2. 当前有效入口是桌面应用 `C:\Users\12070\AppData\Local\Programs\CC Switch\cc-switch.exe`；不要把命令行 shim 当作正常入口。
3. 直接改数据库前必须备份 `C:\Users\12070\.cc-switch\cc-switch.db`。
4. 删除仓库源前必须先 dry-run，并向用户说明会影响哪些 `skills` 记录。
5. 默认只删除 `skill_repos` 仓库源记录，不删除 `skills` 表中的已扫描 skill 记录；只有用户明确要求时才连同关联 skills 一起删除。
6. 不直接修改 `C:\Users\12070\.cc-switch\skills\<skill-name>` 安装副本；自建 skill 内容只改 `C:\Users\12070\.cc-switch\skills\自建skills` 源仓库。
7. 不为 `cc-switch` 的零散子功能新建独立 skill；后续 cc-switch 管理功能集中维护在本 skill 内。
8. `C:\Users\12070\.local\bin\cc-switch.cmd` 是旧 CLI shim，指向已不存在的 `D:\Program_Files\CC-Switch-CLI\current\cc-switch.exe`；不得把它作为可用 `cc-switch` 入口。
9. `settings.json` 中 `skillSyncMethod=auto` 不代表安装副本会在本轮任务内立即刷新；源仓库提交推送后必须显式验证安装副本内容。
10. 不知道 CC Switch 当前 `content_hash` 算法时，不手工改 `skills.content_hash`；优先保持数据库来源、目录和启用状态正确，并说明 hash 可能滞后。

## 自建 skill 生命周期

- 修改已安装的自建 skill：只改 `C:\Users\12070\.cc-switch\skills\自建skills\<skill-name>` 源仓库；保持 `name` 与目录名不变；改动完成后默认自动提交并推送；随后通过 CC Switch 桌面应用同步或刷新，让 `C:\Users\12070\.cc-switch\skills\<skill-name>` 安装副本自动更新；最后验证安装副本内容、`skills` 表的来源/目录/启用状态，并仅在明确知道算法时核对或更新 `content_hash`。
- 新建自建 skill：先在源仓库新建、提交并推送；默认不替用户强行安装，提示用户通过 CC Switch 桌面应用手动安装；安装后再查询 `skills` 表，核对来源、目录、hash 和多客户端启用状态。
- 删除自建 skill：源仓库目录和 CC 安装侧一起处理；先查 `skills` 表确认该 skill 是否来自 `repo_owner=dmdmwshr` 且 `repo_name=custom-skills`，列出影响；通过 CC Switch 桌面应用卸载或删除安装副本；必要时先备份数据库，再清理对应 `skills` 记录。单删某个 skill 时不要删除 `skill_repos` 中的 `dmdmwshr/custom-skills` 仓库源，除非用户明确要求删除整个自建源。

## 安装副本同步实操

1. 源仓库改完后先检查并提交推送：
   ```powershell
   git -C "C:\Users\12070\.cc-switch\skills\自建skills" status --short
   git -C "C:\Users\12070\.cc-switch\skills\自建skills" add <changed-files>
   git -C "C:\Users\12070\.cc-switch\skills\自建skills" commit -m "<message>"
   git -C "C:\Users\12070\.cc-switch\skills\自建skills" push
   ```
2. 等待或刷新 CC Switch 后，必须验证安装副本是否真的更新：
   ```powershell
   Test-Path "C:\Users\12070\.cc-switch\skills\<skill-name>\scripts\<new-script>.py"
   ```
3. 如果自动同步未即时发生，允许在已提交推送后做一次受控手工同步：先完整备份安装目录到 `C:\Users\12070\.cc-switch\skill-backups\<timestamp>-<skill-name>-before-sync`，再验证目标路径位于 `C:\Users\12070\.cc-switch\skills\<skill-name>` 下，最后复制源目录内容。
4. Windows PowerShell 中不要用 `Copy-Item -LiteralPath "<src>\*"` 复制通配符；`-LiteralPath` 会把 `*` 当字面路径。应使用 `Get-ChildItem -LiteralPath <src> | ForEach-Object { Copy-Item -LiteralPath $_.FullName ... }`。
5. 手工同步后做三项验证：关键文件与源仓库字节一致、安装副本的实际命令或 smoke test 通过、`skill-repos.py show --owner dmdmwshr --name custom-skills` 仍显示对应 skill 来源和启用状态正常。
6. 从 skill 目录运行 Python 脚本或 `py_compile` 后，清理 `__pycache__`，不要让缓存目录混入安装副本或源仓库。

## 默认流程

1. 设置 PowerShell UTF-8 输出。
2. 确认桌面应用路径和运行状态：
   ```powershell
   Get-Item "C:\Users\12070\AppData\Local\Programs\CC Switch\cc-switch.exe"
   Get-Process cc-switch -ErrorAction SilentlyContinue
   ```
3. 读取当前仓库源：
   ```powershell
   python scripts/skill-repos.py list
   ```
4. 对新增、删除、停用、改分支等数据库变更先执行 `--dry-run`。
5. 用户确认后，再带 `--yes` 执行真实数据库变更。
6. 对已安装自建 skill 的内容修改，优先走“源仓库提交推送 -> 桌面应用同步/刷新 -> 安装副本验证”；自动同步未即时发生时，按“安装副本同步实操”受控处理。
7. 变更后再次 `list` 或 `show`，并验证安装副本内容、来源记录和多客户端启用状态。

## 脚本用法

在本 skill 目录下运行：

```powershell
python scripts/skill-repos.py list
python scripts/skill-repos.py show --owner dmdmwshr --name custom-skills
python scripts/skill-repos.py add --owner owner --name repo --branch main --enabled 1 --dry-run
python scripts/skill-repos.py add --owner owner --name repo --branch main --enabled 1 --yes
python scripts/skill-repos.py enable --owner owner --name repo --enabled 0 --dry-run
python scripts/skill-repos.py set-branch --owner owner --name repo --branch main --dry-run
python scripts/skill-repos.py remove --owner owner --name repo --dry-run
python scripts/skill-repos.py remove --owner owner --name repo --yes
```

删除测试时，先用 `remove --dry-run` 验证影响范围；不要拿官方源或自建主源做真实删除测试，除非用户明确指定。
