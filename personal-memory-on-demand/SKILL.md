---
name: personal-memory-on-demand
description: 按需查询本机个人事实资产记忆系统的记忆、项目、实体与代码图谱，并仅在用户明确要求时执行结构化盘点或已归档 Codex 会话扫描录入。用户提到个人记忆、项目历史、实体详情、记忆盘点或归档录入时使用。
---

# 个人记忆按需管理

通过本机个人事实资产记忆系统 API 按需工作，不启动 personal_memory 或 code_graph MCP。规范账本仍是事实源；Graphiti/Neo4j 是投影，codebase-memory 仍由原系统独立管理。

## 基本边界

- 固定访问本机 127.0.0.1:8788；不连接远程地址，不读取或输出凭据。
- 每次实际调用前，先检查 API 就绪状态和健康状态；若服务不可用、Neo4j 不可达或本地向量模型未就绪，如实报告，不重启服务、不切换模型、不伪造成功结果。
- 普通查询绝不触发写入。只在用户当前明确要求“盘点”或“扫描已归档 Codex 会话并录入”时，才运行相应的写入命令。
- 不提供“记住一句话”或无来源事实的直接写入入口；不扫描当前或未归档的会话。

## 读取记忆

先执行 readiness 或 health，再根据用户目标运行脚本。脚本会把预检结果一并输出。

| 用户目标 | 命令 |
| --- | --- |
| 搜索记忆 | python scripts/memory_api.py search --query "关键词" |
| 查看全局概览 | python scripts/memory_api.py overview |
| 列出实体 | python scripts/memory_api.py list-entities --query "关键词" |
| 查看实体、来源、关系与历史 | python scripts/memory_api.py entity-context --entity-id "实体 ID" |
| 列出项目 | python scripts/memory_api.py list-projects |
| 获取项目上下文 | python scripts/memory_api.py project-context --entity-id "项目实体 ID" |
| 查看最近盘点记录 | python scripts/memory_api.py inventory-status |
| 查看归档扫描状态 | python scripts/memory_api.py archive-status |
| 查询代码图谱状态 | python scripts/memory_api.py code-graph-status |
| 检索代码图谱 | python scripts/memory_api.py code-graph-search --project-entity-id "项目实体 ID" --query-type "架构" |

代码图谱尚未建立索引时，必须明确返回“尚未建图”；不得调用模型猜测代码结构。codebase-memory 的独立状态不因本 Skill 而改变。

## 录入记忆

只有在用户当前明确授权时执行以下两类操作，并在结果后报告 API 返回的运行状态：

| 用户明确请求 | 命令 |
| --- | --- |
| 对已登记资产进行结构化盘点 | python scripts/memory_api.py inventory-scan |
| 扫描一批已归档 Codex 会话并录入 | python scripts/memory_api.py archive-scan --limit 10 |
| 扫描全部已归档 Codex 会话 | python scripts/memory_api.py archive-scan --all |

默认会请求既有系统刷新嵌入并同步图投影；仅当用户明确要求跳过时，追加 --no-embeddings 或 --no-graph。若预检或 API 返回失败，停止并报告，不重试或补写。

## 验证与维护

- 修改脚本后运行 python scripts/test_memory_api.py；该测试使用模拟接口，不读取或写入真实记忆数据。
- 不直接编辑个人记忆系统的数据库、Graphiti、Neo4j 或 codebase-memory 配置。
- 本 Skill 的源目录由 CC Switch 管理；修改后按受管同步流程发布到 Codex。

## 资源

- scripts/memory_api.py：固定本机 API 的按需读取与受限写入适配器。
- scripts/test_memory_api.py：覆盖全部接口映射的离线模拟测试。
