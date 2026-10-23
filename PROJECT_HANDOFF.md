## 2026-10-20 新增 windows-from-wsl、claude-session-ops 两个 Skill

- 源提交：`f14d0d6975731aac85ba1b973e6824b661823cfa`（`f14d0d6`），已推送既有 `origin/main` 并实时回读一致。新增两个 Skill 和最小只读检查脚本，仅同步 README 与 platforms.json；按现场使用仓库根目录布局。
- 验收：格式、UTF-8、引用、脚本实跑与受管管理器验证通过；源文件、安装副本和发布记录哈希一致。新 Codex 进程在源仓及工作台目录均发现唯一 enabled 入口，`errors=[]`，未启动模型回合。
- 发布记录：`/srv/work/host-baseline/skill-releases/20261020T015629912725Z`。
- 完成回执：`/srv/work/tasks/2026-10-19-two-skills-01a15701/outputs/completion.json`。Claude 链接已通过指定同步脚本完成并回读确认，分别指向本机 Codex 受管安装副本。
- 真实业务会话创建、派发、归档以及 Windows 界面和设备操作未执行；本次 Skill 发布验收无未完成项。本轮仅补写交接、单独 docs 提交并推送既有远端，不重跑发布；补录提交号与推送回读记录在同任务目录 `outputs/handoff-closeout.json`。
- 本项目收尾规则（用户本次明确）：以后每个任务结束都必须在 `PROJECT_HANDOFF.md` 写一条，包含提交号和回执路径；当前提交号需避免自引用时，由该回执记录并可回读。

## 2026-10-20 用户直接指令：Codex 项目管理 Skill 与前台规则

- 用户要求：前台操作优先少用但不另行询问；将项目改名、路径迁移、项目登记与会话重建整理为 Skill，并给 WSL Codex、Claude 安装。该请求不触发真实项目迁移或会话重建。
- 新增 codex-project-manager，保留一份跨平台流程；4 份按需参考分别覆盖桌面名称、目录/登记迁移、会话/绑定和历史证据。声明平台与 WSL 受管名单已补齐。桌面和独立 app-server 项目身份、归属和执行目录分别核验。
- 只读名称核验器在 Windows 通过 14 项检查及 27 项真实改名回执复核；WSL 完成初版 10 项检查，最终修订测试与安装发现按本轮发布回执验收。独立行为试读 4 场景通过，并修正错误标记漏检和混合远程项目输入边界。
- Windows 全局规则与 WSL 共享规则明确：已授权任务后台不可用时可短暂前台操作，无需再问；保留用户本次明确不抢焦点/禁止前台的限制。WSL Codex 链接与 Claude 导入均回读到同一规则源；未启动模型或重启服务。
- 源仓先核对并整合远端 26b3645，保留既有 MarkItDown WSL 变更；本轮只发布 codex-project-manager，不顺带升级其他 Skill。Windows 使用 CC Switch 精确发布，WSL 使用现有受管发布器，Claude 沿既有链接机制。
- 用户追加统一 Codex 默认完全控制，随后明确已有会话可由本人手动修改。本 Skill 补充创建/续接/重建及自动化绑定的权限回读，区分只读行为与受限沙盒、全局默认与旧会话覆盖；不直接改内部状态或正在执行回合。首版双端 14 项检查和安装发现已通过，追加仅为流程文档，最终发布仍沿同一精确 Skill。
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

## 2026-10-22 用户直接指令：诊断 Skill 单次被动采样与五类操作卡

- 用户授权实施“多需求协作、Claude 按需使用与长期维护方案”。本源仓只认领 `codex-local-state-diagnostics/SKILL.md` 与新增 `references/passive-maintenance.md`；Windows 采样器实现由 03-05 原项目负责，不复制源码或新增服务。
- 载荷提交 `6075c21b3e886d862ca933aa0ca52932a3fbbe85`，Claude 窄返工提交 `60011414480186603da7eeacae997e749f3aecd5`。新增人工单次 `--passive` 路由与模型断连、看似卡住、工作空间/会话加载、工具慢操作卡；主动诊断及获授权修复分别处理。旧缺失 Skill 引用改指项目规则与 03-02 唯一协作规范，不改默认角色或权限。
- 源格式、UTF-8、引用及原有 8 文件摘要保全通过；真实 Claude 已核两处返工与冻结摘要，源规格通过。工具独验、远端同步、Windows 单 Skill 安装及新进程发现按仓外 `D:/12070/Documents/工作台管理报告/按需协作实施-20261022/skill/completion.json` 分层记录，未取得对应成功回读前不能称完成；本条不替代发布回执。
- 受管同步沿现有 CC Switch，限定这一 Skill，保留全部客户端开关与历史备份。当前 CC Switch 4.0.16/schema 20 的实际登记代码在隔离副本验证通过，新增客户端字段及 content_hash 保持；活跃数据库未用于该测试写入。
- 本次全部恢复点、源规格独验、受管发布备份、逐文件三方摘要和发现结果留仓外；本交接提交号由同一 completion.json 记录以免自引用。没有启动模型业务、修改运行配置或新增定时诊断。

## 2026-10-22 用户直接指令：项目启动登记与浏览器约束（WB-FLOW-20261022/v1）

- 本轮唯一源写域为 `codex-project-manager` 与 `playwright-browser-control`，载荷提交 `1be38703a3613d458c8bab89a3f2025772173200`，仅三文件说明/启动模板修改。准入后由目标聊天核对人工添加的应用身份、目录、分区和实际权限，引用03-01唯一 `PROJECT_REGISTRATION.md` 与 `status/ensure` 完成自身登记并回原交接继续已授权工作；不复制总表或请求实现。
- 浏览器保留Chrome、账号资料、副屏及后台约束；一次未发现/连接失败先核原入口，不切Edge或借Seth替Default、新建运行根掩盖缺口。引用03-05默认离线预检，配置检查、显式运行、业务登录及物理清理分别结论化，不把旧缺失写为当前故障。
- Codex root 已独立核对三文件及权威接口并PASS。03-01登记依最新r2独验通过，r1失败与旧冻结保留；浏览器工具独验和当前运行配置观察也分层留证。源、提交推送、Windows精确两Skill安装、三方哈希、七客户端开关/其他110行保护及一次无模型新进程发现，以仓外 `D:/12070/Documents/工作台管理报告/治理完善实施-20261022/tools-skills/RELEASE_COMPLETION.json` 为最终发布指针；此条不替代尚未取得的发布回读。
- 工具/Skill资源盘点为派生候选，权威仍为既有项目总表和CC Switch；MCP三缺口仅列后续维护候选，不授权安装、启停、卸载或第三Skill修改。未进行浏览器连接/凭据调用、MCP启停、业务运行或自动化变更。Windows发布不证明WSL、Claude安装或旧聊天热加载。
- 本轮精确源基线、独验、安装前数据库只读摘要及发布自动备份均保留；回退先对账后续修改和实际引用，只处理本轮精确载荷，不覆盖活动数据。

## 2026-10-22 用户直接指令：整个Codex工具链核查与修复（TOOLCHAIN-20261022/v1）

- 用户要求覆盖全部Skill/MCP用途、相似项主备与合并删减、正常失败重试和本轮自有UI收尾，并减少重MCP常驻；凭据沿现状，不进行密钥库改造。工作台按所属范围协调，本源负责人只写本轮Skill文案与精确CC开关，不接管业务。
- 本轮自有文案提交 `9fbc17182b339d8727ee7e2b27725f939f66c0aa` 已推送既有origin/main并实时回读一致：browser可选诊断、Chrome主/Edge经核备用、普通读取/未派发的有界恢复与未知写入对账；Comfy同地址只读health瞬时失败最多补读一次，映射补当前0.52.205及03-05现有入口引用。自有B1片段及B2文案由未实施Codex独验PASS，未把外部实现或本轮文案当真实新后端验收。
- 另有用户安排的浏览器参数预检并发修复，主责可见聊天 `01a16182-8841-7cc1-b99b-0582bbfc2d78`。用户已明确“请协调后合并”；其已完成参数预检精确提交 `de34f00e73abca3431780be41de7204150692e1e`（父9fbc171），31自检与15独验通过并释放源/index写权；本负责人继续必要的无参数snapshot/readback失败分类修复，12隔离自测及未实施Codex14独验通过，保留其参数预检AST。双方变更沿原main统一精确同步browser+comfy。此前未知外部差异已固定保存，本轮自有提交排除其run-code段/脚本/测试，原工作副本保留；不得覆盖、stash或假clean绕过发布条件。
- 当前状态：载荷提交 `c320f6d3386bd392c82f4e9c9aeb7e3328115c9b`（父de34f00）已推送并实时回读一致，源main清洁后沿原受管脚本精确同步browser+comfy完成。browser28文件、Comfy5文件在源/CC安装/Codex三方逐文件摘要一致；全部112条登记的7客户端开关不变，非目标110条整行及10个源登记不变。双cwd新CLI分别发现两个目标及官方pdf唯一启用、旧pdf禁用、零加载错，自有进程退出；既有活动聊天热加载、WSL/Claude发布不在本轮验收层。新代码仅使同身份、无未决业务失败的无参数读取错误可正常再读；旧unknown/readback_required、metadata/进程/成员变化和写入失败不解锁，不自动重连或重放。 源代码增量已由未实施Codex14隔离用例独验通过；参数预检31自检/15独验及先前真实Chrome/Edge自有页循环证据复用，未追加真实浏览器或业务测试。同步恢复点为 `C:/Users/12070/.cc-switch/skill-backups/20261022-225208-498-custom-skills-before-sync`；最终发布分层回执为同证据根 `RELEASE_COMPLETION.json`，只补本交接的后续提交不改变载荷，不重复同步。同步继续既有CC受管入口，源/安装/Codex文件树及新进程发现分别验收，Windows成功不代替WSL/Claude。仓外唯一状态和回退指针：`D:/12070/Documents/工作台管理报告/工具链核查修复-20261022/skills/RELEASE_STATUS.json`。
- B3已单独完成：只将 `openai/skills:skills\.curated\pdf` 的enabled_codex改0并新增旧pdf精确路径的Codex禁用项；官方pdf插件启用保留、112行除该字段外不变、其他客户端和安装/符号链接保留。双cwd新CLI旧pdf=false、pdf:pdf=true、零加载错，自有进程退出；未宣称旧活动聊天热加载或内存改善。已有SQLite在线备份、原配置/安装/链接证据；回退仅恢复精确开关及该路径覆盖，不整库/整配置回灌。回执：`PDF_DISABLE_DRY_RUN.json`、`PDF_DISABLE_APPLY.json`、`POST_B3_SKILL_DISCOVERY.json`；独验在同需求rules/review/B3_POST_APPLY_REVIEW.json。
- 全量派生能力清单与相似族矩阵保存在同一skills证据根。系统官方openai-docs/creator/installer已刷新启用发现；缓存旧版及pptx无安全删除证据而保留。Cua为工具自身官方Skill包、kdocs为CC第三方源，未直接编辑安装物；03-05已补现有Cua短生命周期及WPS365既有CLI/账户能力边界入口。

## 2026-10-22 用户直接指令：CLAUDE-ENH-20261022-WINMCP/v1/a1 实施占位

来源：Claude 代用户派发。按需恢复 Windows-MCP；当前唯一 Skill 实施者 skills_readonly，仅写 windows-mcp-on-demand/**、platforms.json 本项及本节。当前实现与必要测试；待根线程独立审查，未提交推送或安装，真实运行等待用户本人录入令牌。其他差异保留。

本轮停止点（已释放源写入占位）：仅保留 windows-mcp-on-demand/scripts/common.py、server.py、client.py 及 references/server.toml 四个实现文件；9项隔离测试通过，未执行真实服务或GUI。第二批包含任务登记/凭据录入脚本及Skill入口、平台声明的组合源写入被自动审批拒绝，原始理由仅为“blocked by policy”，未换通道或拆分重放。未提交、未推送、未发布或安装；未创建计划任务、令牌或服务。源码保持未完成候选，不作为已发现/已可用Skill。仓外文件哈希、测试及Git状态见 /srv/work/tasks/2026-10-22-windows-mcp-skill-a1/implementation-stop.json；真实本机/SSH完整闭环和独立验收均未覆盖。后续仅在解除具体审查阻断后续接，保留已写源码及其他差异。


## 2026-10-23 用户本人直接授权：Windows-MCP v1/a1续接

主责01a163ba-d4d6-71b1-b023-7be9c06769a3；实际写者/root。用户本人授权补齐7项源文件、受管发布并两端安装，令牌由本人录入。本轮仅认领windows-mcp-on-demand/**、platforms.json本项及本交接自身节；TOOLCHAIN占位已释放，其他差异保留。备份/证据 /srv/work/tasks/2026-10-23-windows-mcp-skill-resume。


### Windows-MCP v1/a1：发布与无令牌验证完成，等本人录入

- 7项补齐、共11文件载荷已审查并提交c367cf0；与远端11提交保全合并be44bd2，最终载荷e4df54f4f7e76ed8b837510f1aed6f70c0d7d5d3已推送实时回读一致。空触发器计数实机缺陷已修；PowerShell null/空/单/双计数0/0/1/2、真实XML节点0及未实施Codex窄独验通过。既有9单测/8身份模拟通过；失败和独验范围保留。
- WSL原发布器精确安装并verify；最终回执/srv/work/host-baseline/skill-releases/20261023T005835467157Z/windows-mcp-on-demand.json。新原生Codex进程唯一启用发现；Claude既有同步器仅新增该Skill链接并回读，未执行模型回合，不宣称旧聊天热加载。
- Windows沿原CC管理器精确-Skill发布，发布检出在D:/12070/Documents/claude-codex/claude-enhancement-20261022/windows-mcp-v1-a1-resume/release-checkout（仅冻结发布用途），旧Windows脏检出未改。11文件源/安装/Codex三方摘要一致，112原Skill整行及10仓库源不变；新增Skill仅启用Windows Codex。两次CC恢复点20261023-085438-040、20261023-085914-703。
- Windows专用任务已登记，12070、Limited/Interactive、无触发器、30分钟上限，动作绑定C:/Users/12070/.codex/skills/windows-mcp-on-demand/scripts/server.py；当前物理Codex副本。数据ACL仅12070/SYSTEM；无令牌、无服务启动。本机与WSL SSH同一客户端status均stopped，start在派发前拒绝token_missing_user_input_required，任务运行时间未变、端口/专用进程为空。
- 按用户本人原指令停在本机Set-Token.ps1交互录入；真实HTTP鉴权/15工具/截图/元素/中文输入/启动停止闭环待录入后继续。未代填/读取令牌、未改全局config、未重启现有应用/服务、未操作业务UI。
- 唯一结果报告D:/12070/Documents/claude-codex/claude-enhancement-20261022/REPLY-tools.md；本轮仓外证据windows-mcp-v1-a1-resume/含独验转录、三方摘要、WSL发现、PRETOKEN_VERIFICATION.json、原报告备份，COMPLETION.json记录本交接提交。此文档收尾不改变冻结载荷，不重复发布；写入占位释放，无自动继续。

- 跨主机最终逐字节核对发现Windows发布检出增量pull继承全局core.autocrlf=true，两个文件换行转换；仅该发布检出固定为false，按Git已提交对象原字节恢复并逐对象验证相同后刷新索引，源代码无变更。再次原CC精确发布，恢复点20261023-090939-819；最终11文件在WSL权威源、WSL安装、Windows发布检出、CC安装及Codex安装全部SHA256相同，Windows新进程发现和本机/SSH无令牌验证再通过。最终Windows回执WINDOWS_RELEASE_VERIFICATION-44c21f6.json；此前换行版本、回执及备份保留。此后仅交接文档提交，不重发载荷。


### Windows-MCP v1/a1：本人录入后真实行为验证完成

- 2026-10-23用户本人本机隐藏录入成功；用户先后明确暂停桌面操作，主责沿原授权完成Windows与WSL SSH实际鉴权、15工具、截图、Snapshot中文值、中文Notepad输入、不保存关闭及停止。原始证据D:/12070/AppData/WindowsMCP/receipts/live-20261023-a1；汇总windows-mcp-v1-a1-resume/LIVE_VERIFICATION.json。令牌仅由运行程序使用，未输出、未复制至WSL。
- 自建句柄9969510、7086042、25043136最终均消失；原用户3021364保留且最小化，最终ChatGPT前台/输入焦点与鼠标[1337,1219]恢复。本机和WSL停止回读无监听/自有进程。未宣称后台键鼠隔离。
- 上游App的模糊标题与缓存曾误展开原用户Notepad，未输入已恢复；第一次空白窗消失原因未知。输入/关闭期间有其他桌面操作，精确断言失败的动作未派发。源文档补充完整Snapshot、唯一句柄、当前控件和控制者核验，保留原失败，不改上游运行时或扩大工具权限。
- 本轮仅更新本Skill入口/操作说明及自身交接，无实现代码变更。将沿原管理器精确发布两端并核摘要/新进程发现；最终发布提交与独立读证结论见REPLY-tools.md。未参与实现Codex独立读原始证据及停止现场，范围不是独立重复GUI执行。
- 当前全局config相对本轮原基线另有3个键变化（model_reasoning_effort及两个插件项），来源未归因；本任务未写或回滚，windows_mcp常驻仍false。不能再使用早期“全局配置哈希未变”的当前断言。

- 用户直接指令（本聊天最新AGENTS规则）已覆盖旧固定主备排序；本次最终说明同步为Cua Driver与Windows-MCP按任务/真实效果互补，保持前台、鼠标和输入焦点时优先后台。仅文案调整，无工具、权限或运行时变更。

- 最终发布已完成：载荷/文案a64e64b4f9e2a31f95d83038d16200e66849b5e4；WSL恢复点20261023T022614718626Z、Windows CC恢复点20261023-102659-528。11文件跨端摘要一致，两端新进程唯一启用发现、无加载错误，无模型回合，自有验证进程退出；Windows其余112条Skill和仓库源不变。完整独立读证及最新互补路由窄审均通过，保留限制，回执同REPLY-tools.md。末次服务补证10:12已停止，无监听/自有服务进程；三自建窗口消失，原用户窗口恢复。源文件与index、桌面和服务占用均释放，后续协调方可沿最新提交合并自己的明确范围。当前“等待令牌”仅为保留的历史阶段，已在Skill当前状态修正；本需求交付完成，无自动继续。
