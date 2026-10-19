# 2026-10-18 路径迁移收尾交接

状态：源码已提交，受管发布待执行。线程 ID：`01a14f63-00e3-7d12-97f0-b5f9e6904eff`。
本仓 `/srv/workspaces/02-software/02-25-custom-skills`；WSL 用户 zhangzhen，CODEX_HOME `/home/zhangzhen/.codex`。

## 授权与边界

仅提交六仓本次迁移路径差异；只有 custom-skills 可推送既有 dmdmwshr/custom-skills main。Skill 安装只走 WSL 发布器与 Windows CC Switch，随后运行指定的 Claude Skill 链接同步。不改业务逻辑、安装副本、并发 workbench/slack-workbench 项目及配置，不修改 Codex config.toml、不重启相关服务。失败或未知结果停止，保留现场。

## 提交

路径提交标题均为 `chore: 迁移到新目录规范路径`；本交接单独提交。

| 仓库 | 路径提交 | 状态 |
| --- | --- | --- |
| 02-23-itpg-platform | `2e45ff080f86a2052e3b028934f46598481f8070` | 本地提交；工作树干净；未推送 |
| 02-27-macro-strategy | `619363780544c18c2b636eba96560459bf5b32dd` | 本地提交；工作树干净；未推送 |
| 03-02-claude-codex-collab | `4bd11dbdca722b1d0e90a6686665915b96def321` | 本地提交；工作树干净；未推送 |
| 03-03-codex-host | `983be602ba92dfe1261fb59dee10a03090389fa0` | 本地提交；工作树干净；未推送 |
| 03-08-server-traffic-sync | `a0974ebe24474b515802625d3cf2ddfbcb62c2ce` | 本地提交；工作树干净；未推送 |
| 02-25-custom-skills | `d6d1249f2c3957266b63d261ac7f09eaa5da94c6` | 21 文件、29 处路径替换；尚未推送或安装 |

初始 41 个文件均逐仓 diff 审查，并确认备份等于原 HEAD、迁移结果可由原前缀规则重现。信息化仓三处历史 legacy 引用被错误放入新源码根，已按备份恢复。未迁移历史数据。

custom-skills 原 17 文件及交接已精确备份；从 62961da 快进同步远端 20 个既有提交到 75ecfbe 后重应用迁移规则，新增发现四个路径文件，合计 21 文件。续接时 21 文件哈希未变，实时远端仍为 `75ecfbe0b035a4afa7102092f0fe25a54ac039bb`，无分歧。

## 已验证

- 六仓 UTF-8、相关 Python 语法/JSON、diff --check 通过；六仓路径提交的精确暂存检查通过。
- 10 个 Skill quick_validate 通过；55 项本地引用存在，包含从源仓根调用的两个发布器脚本。
- personal-memory-on-demand/scripts 下 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_memory_config`：5 项通过。
- comfyui-production-manager/scripts 下 `PYTHONDONTWRITEBYTECODE=1 python3 test_delegate_model_downloads.py`：9 项离线测试通过，未启动下载或远程传输。
- 421 个跟踪文本文件旧根检索零命中，不包括 Git 历史。
- WSL 两项既有安装（7/23 文件）与各自历史发布记录一致。
- Windows 主机 DESKTOP-K65KC17、用户 12070，源仓 main 干净；CC Switch 自建源 main/enabled=1、user_version=20。十项源/安装/Codex 共 101 文件哈希一致。
- 本次获准重跑的 Windows 只读预检已通过：在内存中形成的候选脚本与 WSL 审查版本哈希一致、PowerShell ParseInput 无错误，7 个实际目标目录存在。使用缩短后的 windows-path-syntax-resumed.ps1；不写 Windows 源码或安装副本。

## 历史断点

原 Windows 只读预检的本地包装器 stderr UTF-8 解码失败（UnicodeDecodeError）；该旧调用仍 UNKNOWN。Claude 已修复包装器并保存 run_windows.py.before-claude-fix；用户明确允许重跑一次。新的独立只读回执已通过，未以新回执改写旧结果。缺少 PROJECT_HANDOFF.md 不算失败，已按用户要求新建。

## 发布目标与剩余步骤

1. 提交本交接后，仅推送 custom-skills main，并实时 ls-remote 回读发布提交。
2. WSL 精确发布 personal-memory-on-demand、playwright-browser-control，执行预演、--apply、--verify、新进程 skills/list。
3. Windows 独立源仓同步已确认提交，CC Switch 精确选择：applio-rvc-manager, bilinote-video-note, comfyui-production-manager, obsidian-notes, personal-memory-on-demand, playwright-browser-control, pyweixin-rpa, summarize-link-note, wsl-backup-audit, xf-information-file-manager。先 -WhatIf，再真实同步；保留自动备份、登记、源/安装/Codex 哈希与独立进程发现证据。
4. 运行 `/srv/work/claude-codex/sync-claude-skills.sh`，核验 Claude 链接；两端安装副本旧根搜索排除 Git 历史。
5. 更新本交接并单独提交/推送最终说明；Skill 载荷未再变化时复用既有安装证据，注明安装提交和仅文档提交的区别。最终六仓状态及实时远端以仓外 final-verification.json 回读为准。

此刻安装、Claude 同步、发布后哈希/发现和最终六仓清洁验收尚未完成。仅更新已有 Skill，不触发软件首次安装或新顶层项目盘点。

## 证据与恢复

回执根：`/srv/work/tasks/2026-10-18-workspace-migration/closeout-01a14f63`。原迁移 outputs、backups/files 保留。

migration-audit.json、reviewed-files.json 和各仓 patch 保存初始审查；repository-commits.json 保存五仓提交；custom-before-integration/ 与 custom-upstream-before-path-fix/ 保存精确恢复副本；custom-path-changes.json 保存 21 文件哈希；custom-commit.json 保存本仓路径提交。验证见 skill-validation.json、offline-test-0/1.json、prepublish-verification.json、remote-resumed-preflight.json 与 windows-path-syntax-resumed.result.json。

历史失败见 command-failure.json、stopped-state.json、handoff-before-publish.md。回退只处理本任务精确差异，保留后续修改与未知状态，不覆盖活动数据。
