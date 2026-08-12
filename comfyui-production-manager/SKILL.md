---
name: comfyui-production-manager
description: 管理本机 ComfyUI 的工作流、模型与 AI 创作项目。用户要求整理/去重/归档/命名工作流、检查模板缺失模型、确认模型存放位置、规划模型目录、准备图像/视频/音频/3D 创作流程、建立项目资产与分镜台账，或询问 ComfyUI MCP/Agent 配置时使用。模型下载先按“直连/大小/代理”分流：可直连时走本机直连；必须经代理但小于 2 GiB 时走 CN2 直连；只有必须经代理且大于等于 2 GiB 才委托 `meifu-resumable-download` 的 Windows 单工作队列。本 skill 只负责模型类别、来源、许可证、目标目录和下载后的登记。
---

# ComfyUI Production Manager

## 目标

把 ComfyUI 当作可学习、可复用、可追溯的生产系统，而不是散落的 JSON、模型和输出文件。任何动作先判断“来源—范围—输入输出”，再执行只读审计、登记、同步或归档。

默认本机路径：

- ComfyUI：`C:\Users\12070\Documents\ComfyUI`
- 运行工作流：`C:\Users\12070\Documents\ComfyUI\user\default\workflows`
- 工作区：`D:\12070\Documents\workspaces\Comfy-Codex-Workspace`
- 工作区工作流母版：`D:\12070\Documents\workspaces\Comfy-Codex-Workspace\workflows`
- 项目：`D:\12070\Documents\workspaces\Comfy-Codex-Workspace\projects`
- 模型：优先使用 ComfyUI `models/` 或已经配置的共享模型目录，不把大模型复制进项目。

## 三个功能模块

### 1. 工作流管理

1. 先扫描 JSON：以 UTF-8/UTF-8-SIG 读取，检查 JSON 合法性、`nodes`、`links`、节点类型、输入输出和自定义节点类型。
2. 计算原始 SHA-256 与规范化 JSON SHA-256；同时记录来源目录、运行目录、项目/测试/脚本引用。
3. 将官方模板和特色模板统一管理在 `workflows/模板库/`；来源通过 `catalog.json`、`curated_registry.json` 和工作流登记字段表达，不再用“官方库/手动补充库”拆成两套。
4. 使用 `scripts/audit_workflows.py` 生成审计报告；任何删除或移动前先做 dry-run 和引用搜索。
5. 手动下载模板必须先做学习价值审查：比较输入输出、核心节点、控制方法、模型变体、节点是否展开和测试用途。只有没有任何独特学习角度、且已有模板库等价流程的手动副本才允许直接删除；其他模板归入模板库并登记保留理由。
6. 安全归档优先于删除：目标必须是精确路径，归档清单记录原路径、目标路径、哈希、原因和替代项。被测试、脚本或项目引用的文件不能直接删除；用户明确要求清理且审计确认无独特价值时，才删除母版和运行镜像。
7. 工作区是编辑母版，ComfyUI 用户目录是运行镜像；同步后必须回读 SHA-256。不得把用户目录里的临时自动保存文件当作正式版本。

推荐目录和命名见 `references/workflow-organization.md`。文件名应表达“输入 → 核心能力 → 输出”，特色模板可加 `补充_`，但不能用前缀掩盖功能重复，例如：

```text
12_关键帧生视频_六关键帧到视频_Wan2.2_首尾帧补间.json
90_项目_乌萨奇视频替换_原物品锁定_固定背景_SCAIL2-SAM3_UI版.json
```

模板库登记至少包含 `source_type`、`input`、`output`、`learning_value`、`supersedes`、母版路径和运行镜像路径；官方条目保留官方模板 ID，特色条目写入 `模板库/curated_registry.json`。

### 2. 模型管理

1. 先读取运行进程实际使用的 `C:\Users\12070\AppData\Roaming\Comfy Desktop\shared_model_paths.yaml`，不要只扫描 ComfyUI 安装目录。
2. 当前默认根目录是 `D:\Comfy-Desktop\ComfyUI-Shared\models`；D 盘可用空间低于 200 GiB，或不足以容纳“模型 + 传输块 + 预留”时，才回退到 `C:\Users\12070\Documents\ComfyUI\models`。不手改 Desktop 生成的共享路径配置。
3. 先运行 `scripts/audit_models.py` 盘点两个模型根目录，再用 `scripts/audit_model_dependencies.py` 从模板库提取“已安装 / 缺失 / 未解析”的模型依赖与直链。
4. 只把类别、来源和文件名都明确的 `missing` 项作为下载候选；`unresolved`、同名多来源、不同精度或不同训练版本必须先人工确认，不能静默替换。
5. 只允许 `download_candidates` 中类别、文件名和无签名 HTTPS 来源均明确的 `missing` 项进入下载准备；`unresolved`、类别冲突、带签名链接以及不同精度/训练版本必须人工确认，不能静默替换。
6. 对每个已批准候选先确认“是否必须经代理”和文件大小：来源可直连的任意大小走本机直连；必须经代理但小于 2 GiB 的候选走 CN2 直连；两者都不写入 Meifu 队列。代理需求或大小未知时只做探测，不得默认转入 Meifu。
7. 只有必须经代理且大于等于 2 GiB 的候选，才使用 `scripts/delegate_model_downloads.py prepare` 逐项委托给 `meifu-resumable-download`。通过 `--model-root` 配置精确模型根目录，或由共享路径规则选择根目录；通用队列收到的是“存储根目录 + 类别/文件名”的安全相对目标，不能逃出模型根目录。ComfyUI 委托清单和通用队列都采用短时单写入租约、版本校验和原子提交；遇到其他任务正在写入时只重试或查询，不手改 JSON。
8. 仅对进入 Meifu 队列的候选，实际传输才由通用技能的 Windows 后台任务承担：官方 HTTPS → `/root/.cache/meifu-downloads` 分块缓存 → 本机 SFTP 续传 → SHA-256 → 原子落位。Codex 只可准备、请求后台任务启动和查询状态；队列始终只有一个工作者、一个输出文件和一个 Meifu 分块。断网、断电和临时上游错误保留断点并自动退避续传。
9. 文件落盘后运行 `scripts/delegate_model_downloads.py reconcile`，由本 skill 计算最终 SHA-256 并更新 `models/catalog.json`；它不执行、安装或导入模型。下载成功不等于显存可完整加载，显存兼容性单独报告。
10. `stage_model_download.py` 和 `model_download_queue.py` 是只保留状态审计的旧兼容入口，所有可能传输、清理或修改旧队列的命令都会安全退出。不得新建或恢复旧 ComfyUI 下载任务；已有旧任务的停用/迁移属于单独的 Windows 计划任务变更，必须获得当前用户明确授权。
11. 模型移动、覆盖或删除属于外部状态变更。默认只生成清单和建议；已有不同文件时通用下载器拒绝覆盖，删除前先检查工作流、项目 manifest、模型目录登记和 ComfyUI 日志。

详见 `references/model-management.md`。

### 3. 创作管理

每个创作建立独立项目，不把长视频、实验输出和正式成片混在一起：

```text
projects/<project>/
├── brief/                         # 目标、受众、版权与交付规格
├── assets/{raw,references,generated}/
├── bible/{characters,scenes,props,style}/
├── shots/<shot-id>/{inputs,controls,outputs,reviews}/
├── prompts/                       # 结构化提示词和负面约束
├── audio/                         # 台词、音效、音乐和时间轴
├── workflows/                     # 项目 API/UI 工作流及锁定清单
├── deliverables/                  # 通过验收的成片
├── logs/                          # 参数、错误、显存和复盘
└── manifest.json                  # 资产、镜头、模型、版本、哈希
```

创作顺序：文本拆镜 → 角色/场景/道具 Bible → 参考图和版权登记 → 关键帧 → 3–8 秒镜头生成 → 画面/一致性检查 → 音频与口型 → 剪辑转场 → 成片验收。人物、背景、视角、动作、物品、遮挡和声音必须在镜头表中有明确字段；“固定随机种子”不能替代参考图、控制视频、时序蒙版和分段验收。

详见 `references/creative-production.md`。

## MCP / Agent 使用边界

- 先使用只读能力列出 ComfyUI 地址、版本、工作流、节点和模型依赖，再执行生成。
- 工作流 JSON 的结构检查、模型盘点、依赖锁定可以本地完成；MCP 不替代文件母版和审计报告。
- 启动/停止 ComfyUI、安装自定义节点、下载大模型、覆盖运行配置或批量生成前，必须得到当前用户明确授权，并记录变更前后的状态。
- Agent Panel 的云端 Pod 控制与本地 ComfyUI 不是同一层；`Use Local` 只表示使用本机渲染，不代表已配置 MCP 或节点权限。
- 节点缺失、模型缺失或版本不兼容时先停止在诊断阶段，不用随机节点或模型“凑出”结果。

## 标准响应和验证

每次管理任务都要给出：扫描范围、分类依据、保留/归档/删除清单、引用与哈希证据、模型/节点缺口、下一步建议。完成文件变更后至少验证：

1. JSON 仍能解析，节点和链接数量未意外改变；
2. 母版与运行镜像哈希一致；
3. 测试清单、脚本和项目文档引用新路径；
4. 自建工作流的输入、输出、模型和验证状态已登记。

不要为了“整齐”删除模板库条目、模型、输出或项目实验资产。对手动下载模板，只有完成“功能级对照 + 引用搜索 + 替代项确认”后才能按用户要求直接删除；删除对象只包括明确的手动副本，不删除官方/特色模板库中的运行镜像或模型文件。

## 资源

- `references/workflow-organization.md`：目录、命名、去重、归档和镜像规范。
- `references/model-management.md`：模型目录、清单字段、依赖锁定和下载检查。
- `references/creative-production.md`：小说改编、成片重构、段子转视频的项目结构和镜头验收。
- `references/mcp-and-validation.md`：MCP/Agent 边界、节点依赖和运行前检查。
- `scripts/audit_workflows.py`：扫描 JSON、校验结构、识别精确/规范化重复并输出报告。
- `scripts/curate_template_candidates.py`：将待整理模板与统一模板库做功能签名对照，只给出候选建议，不自动删除。
- `scripts/model_paths.py`：读取 Comfy Desktop 共享模型路径，并按 D 盘 200 GiB 阈值选择默认/备用落盘位置。
- `scripts/audit_model_dependencies.py`：从模板 `MarkdownNote` 和模型加载器提取模型依赖、来源与缺口；不下载模型。
- `scripts/delegate_model_downloads.py`：仅可用于“必须经代理且大于等于 2 GiB”的安全候选，将其委托给通用 Meifu 队列；调用方必须先完成路由判断。可直连的候选走本机直连，必须经代理但小于 2 GiB 的候选走 CN2 直连。该脚本支持可配置模型根目录、只读状态和下载后目录登记；本身不传输模型。
- `scripts/stage_model_download.py`、`scripts/model_download_queue.py`：旧直连下载兼容入口；只保留审计能力，不能启动传输。
- `scripts/show-model-download-queue-task.ps1`、`scripts/uninstall-model-download-queue-task.ps1`：旧任务的只读查看与精确卸载入口。旧安装脚本会拒绝创建任务，改由通用 Meifu 技能管理唯一后台队列任务。
