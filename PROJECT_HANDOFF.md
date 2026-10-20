## 2026-10-20 用户直接指令：Codex 项目管理 Skill 与前台规则

- 用户要求：前台操作优先少用但不另行询问；将项目改名、路径迁移、项目登记与会话重建整理为 Skill，并给 WSL Codex、Claude 安装。该请求不触发真实项目迁移或会话重建。
- 新增 codex-project-manager，保留一份跨平台流程；4 份按需参考分别覆盖桌面名称、目录/登记迁移、会话/绑定和历史证据。声明平台与 WSL 受管名单已补齐。桌面和独立 app-server 项目身份、归属和执行目录分别核验。
- 只读名称核验器在 Windows 通过 14 项检查及 27 项真实改名回执复核；WSL 完成初版 10 项检查，最终修订测试与安装发现按本轮发布回执验收。独立行为试读 4 场景通过，并修正错误标记漏检和混合远程项目输入边界。
- Windows 全局规则与 WSL 共享规则明确：已授权任务后台不可用时可短暂前台操作，无需再问；保留用户本次明确不抢焦点/禁止前台的限制。WSL Codex 链接与 Claude 导入均回读到同一规则源；未启动模型或重启服务。
- 源仓先核对并整合远端 26b3645，保留既有 MarkItDown WSL 变更；本轮只发布 codex-project-manager，不顺带升级其他 Skill。Windows 使用 CC Switch 精确发布，WSL 使用现有受管发布器，Claude 沿既有链接机制。
- 最终源提交、实时远端、Windows/WSL/Claude 安装与新进程发现、恢复点及未验收层级统一记录于 Windows 仓外 D:/12070/Documents/工作台管理报告/Codex项目管理Skill-20261020/completion.json。未出现成功回执前不把计划发布写成已完成；无需为记录提交号改写同一载荷。

## 2026-10-19 用户直接指令：Claude 独立能力补齐中的 MarkItDown WSL 适配

- 本次只改 markitdown-local/SKILL.md 与 platforms.json；原 convert.py 不变。Windows 原路径保留，WSL 使用协作项目独立文档环境的 MarkItDown 0.1.8。受管平台增加 WSL，不宣称其他 Linux 已安装。
- 已用虚构 Word、Excel、PPT、PDF 四格式实跑同一 convert.py：全部提取中文成功、源文件 SHA256 不变；已有输出被拒绝覆盖且内容不变。测试证据在 /srv/work/tasks/2026-10-19-claude-capability-upgrade/markitdown-publication/acceptance/receipt.json。
- 源仓先从已核对干净的 4bd9ab8 快进到远端 2d1f46f，保留其中其他 Skill 的 5 个提交；本轮仅提交上述两文件及本节，精确发布 markitdown-local。发布提交、远端回读、安装/发现证据与恢复点由同任务目录 publication.json 记录，未有成功回执前不视为完成。
- Windows 安装副本本轮不变；WSL Claude 通过既有同步脚本链接受管 Codex 副本。源码、安装、发现和模型调用分开验收；Claude 实际调用按主任务 19:00 安排执行。

# 2026-10-18 路径迁移收尾交接

状态：六仓路径提交及双端受管安装验收完成。更新时间：2026-10-18T17:35:39-07:00。
线程 ID：`01a14f63-00e3-7d12-97f0-b5f9e6904eff`。
本仓 `/srv/workspaces/02-software/02-25-custom-skills`；WSL 用户 zhangzhen，CODEX_HOME `/home/zhangzhen/.codex`。

## 授权与执行范围

仅提交六仓本次迁移路径差异；只有 custom-skills 推送既有 dmdmwshr/custom-skills main。Skill 安装通过 WSL 发布器与 Windows CC Switch，未直接编辑安装副本；未改业务逻辑、Codex config.toml、并发 workbench/slack-workbench 项目及其配置，未重启相关服务。Claude 同步仅运行指定脚本管理 Skill 链接。原历史备份与未知回执保留。

## 提交结果

六仓路径提交标题均为 `chore: 迁移到新目录规范路径`；交接记录单独提交。

| 仓库 | 本任务路径提交 | 远端处理 |
| --- | --- | --- |
| 02-23-itpg-platform | `2e45ff080f86a2052e3b028934f46598481f8070` | 未推送 |
| 02-27-macro-strategy | `619363780544c18c2b636eba96560459bf5b32dd` | 未推送 |
| 03-02-claude-codex-collab | `4bd11dbdca722b1d0e90a6686665915b96def321` | 未推送 |
| 03-03-codex-host | `983be602ba92dfe1261fb59dee10a03090389fa0` | 未推送 |
| 03-08-server-traffic-sync | `a0974ebe24474b515802625d3cf2ddfbcb62c2ce` | 未推送 |
| 02-25-custom-skills | `d6d1249f2c3957266b63d261ac7f09eaa5da94c6` | 已推送既有 main |

custom-skills 初次发布提交为 `29aad6dcb121405bf7ec7b0258ee3fa36903bd2b`（单独交接提交），git ls-remote 已确认。本文为最终验收说明的独立逻辑提交，不改变 Skill 载荷；提交后 WSL 发布登记对齐最终源提交，Windows 同步最终说明并核对载荷不变。最终 HEAD、实时远端、最后安装源提交与发布目录见仓外 final-verification.json，避免将本文的自引用提交号写成不可能稳定的值。

六仓在本文提交前均已回读干净。协作仓另有并发任务的后继提交 `5426336ba5942bcba5f52172d544c95f04f32425`，直接继承本任务 `4bd11db`；该并发提交已保留，未由本任务推送。最终六仓状态再次记入 final-verification.json。

## 审查与修正

初始 41 个文件逐仓 diff 审查；逐文件确认备份等于原 HEAD、迁移结果可由原前缀替换规则重现。信息化仓三处历史 legacy 引用被错误带入新源码根，已按备份恢复，未迁移历史数据。

custom-skills 原 17 文件及交接精确备份后，从 62961da 快进同步远端 20 个既有提交至 75ecfbe，再重应用迁移规则。最终只修改 21 文件、29 处路径，涉及十项 Skill：applio-rvc-manager, bilinote-video-note, comfyui-production-manager, obsidian-notes, personal-memory-on-demand, playwright-browser-control, pyweixin-rpa, summarize-link-note, wsl-backup-audit, xf-information-file-manager。未 stash/reset/clean，未丢弃未知修改。

## 验收证据

- 六仓 UTF-8、相关 Python 语法/JSON、diff --check 和精确暂存检查通过。
- 十项 quick_validate、55 项本地引用通过；421 个跟踪文本文件旧根检索零命中，不含 Git 历史。
- personal-memory-on-demand/scripts：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_memory_config`，5 项通过。
- comfyui-production-manager/scripts：`PYTHONDONTWRITEBYTECODE=1 python3 test_delegate_model_downloads.py`，9 项离线测试通过，无真实下载或远程传输。
- Windows 重跑只读预检：候选脚本哈希与 WSL 审查版本一致，PowerShell ParseInput 无错误，7 个目标目录存在。
- WSL 精确安装 personal-memory-on-demand（7 文件）与 playwright-browser-control（26 文件）；受管预演、apply、verify 通过，33 文件的源/安装/发布记录一致。新进程各发现唯一 enabled 入口，errors=[]，model_turn_started=false。首次恢复点 `/srv/work/host-baseline/skill-releases/20261019T002951195197Z`；最终提交锚点回执另见 final-verification.json。
- Windows 主机 DESKTOP-K65KC17、用户 12070。源仓干净并与确认发布提交对齐；CC Switch 对上述十项执行 -WhatIf 与真实同步，十份物理 Codex 副本同步成功。源/CC Switch/Codex 三方共 101 文件 SHA-256 一致，来源登记为 dmdmwshr/custom-skills/main，enabled_codex=1；各客户端启用开关和既有 content_hash 与数据库备份相同。独立 Codex 进程发现十项唯一 enabled 入口，errors=[]，未启动模型回合。
- Windows 自动备份：`C:\Users\12070\.cc-switch\skill-backups\20261019-083121-009-custom-skills-before-sync`；含安装旧副本及 SQLite 在线备份，未删除。
- `/srv/work/claude-codex/sync-claude-skills.sh` 成功；13 个 Claude Skill 链接逐一回读，精确指向 Codex 安装入口，目标 SKILL.md 存在，保留未动 0。
- WSL Codex skills、Windows CC Switch skills 与 Windows Codex skills 三个根运行 grep -rIlF：旧根零命中、stderr 空；排除 .git、__pycache__、pyc。grep 退出码 1 表示正常零匹配。模式涵盖迁移旧短名和 Windows 旧根的反斜杠、JSON 转义、正斜杠及挂载路径形式；不篡改 Git 历史。

## 历史断点与剩余项

原只读预检在本地 stderr 解码时发生 UnicodeDecodeError，该旧调用仍 UNKNOWN，回执没有被新成功结果覆盖。Claude 修复字节采集包装器并保留原版；用户明确授权重跑一次。本任务改用较短的只读请求，产生独立成功回执。缺少交接文件不算失败，已按用户要求新建。

无剩余功能、安装或迁移验收项。最后交接提交后的 Git、源锚点及安装回读以 final-verification.json 为最终事实；本次不验收或启动这些 Skill 的业务任务。仅更新已有 Skill，不触发软件首次安装或新顶层项目盘点。

## 回执与恢复点

统一回执根：`/srv/work/tasks/2026-10-18-workspace-migration/closeout-01a14f63`。原迁移 outputs、backups/files 保留。

- migration-audit.json、reviewed-files.json、各仓 patch、repository-commits.json：初始审查及五仓本地提交。
- custom-before-integration/、custom-upstream-before-path-fix/、custom-path-changes.json、custom-commit.json：精确备份及 21 文件路径提交。
- skill-validation.json、offline-test-0/1.json、prepublish-verification.json：格式、离线测试与引用。
- publication-push.json、wsl-publication.json、windows-source-sync.result.json、windows-manager-dry-run.result.json、windows-manager-apply.result.json：首次发布与自动备份。
- windows-hash-verification.result.json、windows-discovery.result.json、claude-skill-sync.json、old-root-scan.json：哈希、登记、发现、链接及旧根检索。
- final-verification.json 及 final-* 回执：最终交接提交后的源锚点、实时远端、六仓清洁与载荷一致性。
- command-failure.json、stopped-state.json、handoff-before-publish.md：历史失败与原未知边界，未覆盖。

回退只处理本任务精确差异，先核对现有内容是否仍属于本次版本，保留后续用户修改、并发提交和未知状态；不以旧备份覆盖活动数据。
