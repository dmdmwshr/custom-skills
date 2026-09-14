---
name: personal-memory-on-demand
description: 从 Windows、WSL 或 Linux 按需查询配置的 Graphiti 个人事实记忆服务；支持按来源设备查询电脑、软件、模型、Skill、项目与历史事实。完成持久软件安装或新建顶层可运行项目后，盘点调用端所在设备并回读登记。其他手动扫描、重试和重建须有当前明确授权；自包含任务不使用。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 个人事实记忆按需管理

同一份技能支持 Windows、WSL 和普通 Linux，脚本只依赖 Python 标准库。执行平台按当前进程识别；管理对象由持久来源编号决定。Windows 集中处理记忆，WSL/Linux 用户采集端负责自己的盘点与归档筛选。平台配置和首次接入见 [跨平台连接与来源](references/cross-platform.md)。

通过可配置的统一网页入口查询个人事实记忆。Graphiti/Neo4j 是唯一业务事实源；SQLite 只保存调度元数据，经过筛选的远端用户输入快照由服务端单独保留。

## 基本边界

- 服务地址依次使用 `--api-url`、`MEMORY_API_URL`、本机采集配置、平台默认值。内网直连，HTTPS 校验证书；连接失败不自动切换服务，不读取或输出凭据。
- 查询默认可查看全部来源；用 `--source self` 或设备编号限定来源。管理操作默认当前执行端的持久来源，禁止在 WSL 安装软件后代替 Windows 盘点；未配置来源时报告待接入。
- 每次实际查询前先检查 API 就绪与健康状态。若服务、Graphiti/Neo4j 或固定向量模型不可用，如实报告，不擅自重启服务、切换模型或伪造结果。
- 普通查询绝不触发写入。手动盘点、归档扫描或归档重建只在用户当前明确授权时执行；唯一例外是符合“软件与项目完成后登记”条件时，可以按全局常驻授权执行一次确定性环境盘点。
- Windows 计划任务可按既定授权范围自动盘点和扫描；计划任务授权本身不能扩展为交互式手动写入，交互式例外仅限本 Skill 明确列出的“完成后登记”。
- 不提供无来源的直接写入入口，不读取活跃会话，不把助手回答、系统提示、工具结果或终端输出当成用户事实。

## 读取顺序与状态分层

查询或解释运行状态时，按以下顺序完成只读核验：

1. 先读取 readiness；就绪后再读取 health。服务、Graphiti/Neo4j 或固定向量模型不可用时，停止本次查询并报告实际状态。
2. 将 `/api/health` 中的 Graphiti/Neo4j 可达性与统计，和业务图谱投影 `/api/knowledge-graph/v3` 分开理解；两者的实体、关系或来源数量可以不同，不能互相替代。
3. 将 SQLite 操作层单独核验。队列、重试、水位、租约和模型调用记录属于运行状态，不是 Graphiti 业务事实；使用 `automation-status`、`archive-status` 或对应只读接口回读。
4. “最近模型调用”可能只是历史成功或失败审计，不能单独证明当前任务失败、成功或正在运行。必须结合当前队列状态、租约、任务时间戳及必要的水位/重试状态；证据不足时只报告“无法确认”。

涉及实体类型、关系类型、命名空间或分组筛选时，先读取 `ontology` 和 `groups`，确认实际标签、字段、关系方向与分组语义，再查询实体或关系。不得猜测 schema 未返回的标签，也不得假定某个字段适用于全部关系；只使用当前 schema/API 明确支持的字段和筛选参数。

执行盘点、扫描、重建或自动化相关 CLI 前，先用当前安装版本的帮助、受支持命令清单或诊断入口核对实际入口。遇到 `invalid choice`、命令消失或版本漂移时，停止并报告入口不匹配，不盲目重试、重建自动化或套用旧命令。

`dry-run` 这个名称本身不是系统级只读证明。只有确认实现不会修改队列、水位、租约、任务、文件或数据库时，才能将其称为只读；否则使用 GET 状态查询，并把它标为模拟、范围有限或未验证，不得用它替代只读回读。

以上规则只约束状态读取和结果解释，不扩大“软件与项目完成后登记”的窄范围常驻授权。

## 查询记忆

先执行 readiness 或 health，再根据目标调用脚本。需要历史状态时使用 `--at` 传入 ISO 8601 时间；需要隔离范围时使用 `--namespace personal|work|creative`。

| 用户目标 | 命令 |
| --- | --- |
| 搜索记忆 | `python scripts/memory_api.py search --query "关键词"` |
| 按历史时点搜索 | `python scripts/memory_api.py search --query "关键词" --at "2026-08-01T23:59:59+08:00"` |
| 查看全局概览 | `python scripts/memory_api.py overview` |
| 列出实体 | `python scripts/memory_api.py list-entities --query "关键词"` |
| 查看实体、真实关系、时间线与来源 | `python scripts/memory_api.py entity-context --entity-id "实体 ID"` |
| 列出项目 | `python scripts/memory_api.py list-projects` |
| 获取项目情况、决策与修改历史 | `python scripts/memory_api.py project-context --entity-id "项目实体 ID"` |
| 查看分组与本体 | `python scripts/memory_api.py groups` 或 `python scripts/memory_api.py ontology` |
| 查看自动任务、队列和失败重试 | `python scripts/memory_api.py automation-status` |
| 查看最近盘点记录 | `python scripts/memory_api.py inventory-status` |
| 查看归档扫描状态 | `python scripts/memory_api.py archive-status` |
| 查看模型配置 | `python scripts/memory_api.py model-settings` |
| 查看设备列表与连接配置 | `python scripts/memory_api.py sources` 或 `connection-info` |
| 查看本设备同步进度 | `python scripts/memory_api.py source-status --source self` |

查询结果必须区分当前事实与历史事实，并保留“用户原话、系统盘点、项目文档、Git 事件”等来源类型。用户问项目实现细节时，只回答事实库中已有的高层项目上下文；缺少事实就明确说明，不猜测函数或调用关系。

## 软件与项目完成后登记

全局规则提供一个窄范围常驻授权：当 Codex 在当前任务中实际完成下列任一结果，并已通过基本验收时，可以不再询问，执行一次确定性环境盘点：

- 在本机安装以后可独立使用、会持久保留的应用、服务、命令行工具或运行时。
- 创建新的顶层、可运行软件项目目录或代码仓库。

现有项目的依赖安装或更新、已有软件升级、临时虚拟环境或容器、仅下载或解压、构建产物、示例项目和未落地方案均不触发。

按以下顺序完成登记：

1. 先完成主任务验收，确认软件可启动或可读取版本，或者项目已达到约定的构建、测试或运行标准。
2. 检查记忆 API 就绪与健康状态；不可用时不擅自启动或重启服务，不切换模型，直接将记忆状态标记为“待补录”。
3. 每个主任务最多执行一次 `python scripts/memory_api.py inventory-scan --source self`，不得因未识别而反复全机盘点。该指令发给当前执行设备的采集端；离线时保留待执行状态。
4. 查询 `source-status --source self`，区分指令已接收、采集中、待写入和已写入；随后按软件准确名称、版本或路径执行 `list-entities --source self` 回读。项目使用 `list-projects --source self`，必要时查看 `project-context`。只收到指令或批次不能称为已登记。
5. 最终分别报告主任务结果与记忆状态。记忆状态只使用“已登记”“已更新”或“待补录”；没有回读到对应记录时，不得声称记忆登记完成。

“完成后登记”只保存确定性环境与项目事实，不等同于备份项目文件、源代码、安装包或运行数据。服务不可用、盘点失败或未识别目标时，保留主任务的真实完成状态并标记“待补录”；不得改用归档扫描、模型抽取、活跃会话读取或直接数据库写入作为兜底。

## 手动录入

除“软件与项目完成后登记”的单次确定性盘点外，以下操作只有用户当前明确授权时执行：

| 用户明确请求 | 命令 |
| --- | --- |
| 对当前电脑环境与登记资产执行确定性盘点 | `python scripts/memory_api.py inventory-scan` |
| 扫描一批已归档用户消息 | `python scripts/memory_api.py archive-scan --limit 10` |
| 扫描全部已归档用户消息 | `python scripts/memory_api.py archive-scan --all` |
| 清除并用 Luna 最高思考重建归档用户消息事实 | `python scripts/memory_api.py archive-rebuild --confirm-rebuild` |

盘点不调用生成模型。归档扫描只处理新原子消息哈希；每个事实必须能回到用户原文，并通过类型、关系方向、时间和敏感信息校验。失败任务由系统按退避策略处理，脚本本身不绕过队列补写。

归档重建只在用户当前明确授权后执行，并明确 `--source self` 或具体来源。它保留确定性盘点和其他设备证据；共享归档只移除所选来源的关联。该来源的重建任务使用 Luna 最高思考，不降级本地模型，不改变其他来源的增量策略。命令携带 `--confirm-rebuild`；当前用户已授权时直接附带该参数，不重复询问。失败重试使用 `archive-retry --source self`。

远端采集指令可以指定 `--request-id` 保存编号。响应丢失时先用 `command-status --source <编号> --request-id <编号>` 查回执，不能盲目重复请求。采集端的分片上传、整批确认和恢复由受管后台服务完成，Skill 不发送原始归档大文件。

## 验证与维护

- 修改适配器后运行 `python scripts/test_memory_api.py`。测试使用模拟接口，不读取或写入真实记忆。
- 不直接编辑 Graphiti、Neo4j 或操作数据库。
- 修改先更新同一份源仓、测试、精确提交并推送。Windows 使用 CC Switch 精确发布，WSL 使用受管发布器；分别核对安装哈希和新 Codex 进程发现。普通 Linux 的离线兼容性与 WSL 实机结果分别记录。

## 资源

- `scripts/memory_api.py`：跨平台查询、来源状态、采集指令与归档管理适配器。
- `scripts/memory_config.py`：服务地址、执行平台、当前来源与目录配置解析。
- `references/cross-platform.md`：配置优先级、平台依赖与发布验收。
- `scripts/test_memory_api.py`：接口映射与授权边界的离线测试。
