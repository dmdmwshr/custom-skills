---
name: personal-memory-on-demand
description: 按需查询本机 Graphiti 个人事实记忆系统中的电脑配置、软件、模型、Skill、项目、数字资产、工作条线、创作知识、实体关系与历史时点，并仅在用户当前明确要求时手动触发结构化盘点或已归档 Codex 用户消息扫描。相关任务需要既有本机事实、项目历史或长期决策时使用；自包含任务不使用。
---

# 个人事实记忆按需管理

通过固定本机 API 查询个人事实记忆系统。Graphiti/Neo4j 是唯一业务事实源；操作数据库只保存哈希、水位、队列、重试、本体缓存和模型调用记录。

## 基本边界

- 固定访问 `127.0.0.1:8788`，不连接远程地址，不读取或输出凭据。
- 每次实际查询前先检查 API 就绪与健康状态。若服务、Graphiti/Neo4j 或固定向量模型不可用，如实报告，不擅自重启服务、切换模型或伪造结果。
- 普通查询绝不触发写入。只有用户在当前消息明确要求“盘点”“扫描已归档 Codex 会话并录入”或“只重建归档事实”时，才可手动执行对应操作。
- Windows 计划任务可按既定授权范围自动盘点和扫描；该自动授权不能扩展为交互式手动写入授权。
- 不提供无来源的直接写入入口，不读取活跃会话，不把助手回答、系统提示、工具结果或终端输出当成用户事实。

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

查询结果必须区分当前事实与历史事实，并保留“用户原话、系统盘点、项目文档、Git 事件”等来源类型。用户问项目实现细节时，只回答事实库中已有的高层项目上下文；缺少事实就明确说明，不猜测函数或调用关系。

## 手动录入

只有用户当前明确授权时执行：

| 用户明确请求 | 命令 |
| --- | --- |
| 对当前电脑环境与登记资产执行确定性盘点 | `python scripts/memory_api.py inventory-scan` |
| 扫描一批已归档用户消息 | `python scripts/memory_api.py archive-scan --limit 10` |
| 扫描全部已归档用户消息 | `python scripts/memory_api.py archive-scan --all` |
| 清除并用 Luna 最高思考重建归档用户消息事实 | `python scripts/memory_api.py archive-rebuild --confirm-rebuild` |

盘点不调用生成模型。归档扫描只处理新原子消息哈希；每个事实必须能回到用户原文，并通过类型、关系方向、时间和敏感信息校验。失败任务由系统按退避策略处理，脚本本身不绕过队列补写。

归档重建只在用户当前明确授权后执行。它保留确定性环境、软件、模型、Skill、项目、资产和工作线事实，只清理 `codex_archive` 来源及其归档水位；重建批次强制使用 Luna 最高思考，失败进入重试，不降级到本地模型。命令必须携带 `--confirm-rebuild`，缺少确认时脚本拒绝请求。

## 验证与维护

- 修改适配器后运行 `python scripts/test_memory_api.py`。测试使用模拟接口，不读取或写入真实记忆。
- 不直接编辑 Graphiti、Neo4j 或操作数据库。
- 本 Skill 由 CC Switch 管理；修改时先更新源仓库、测试、提交并推送，再同步 Codex 与 Agent 安装副本，最后核对三份哈希一致。

## 资源

- `scripts/memory_api.py`：固定回环 API 的只读查询、两类增量扫描与受确认保护的归档事实重建适配器。
- `scripts/test_memory_api.py`：接口映射与授权边界的离线测试。
