# Graphiti 官方功能摘要

## 定位

Graphiti 是面向 AI agent 的时序知识图谱框架。它把输入组织为：

- episodes：原始输入片段，是事实来源。
- nodes：实体节点及其摘要。
- facts/edges：实体之间的关系，带时间元数据和失效信息。

它适合动态数据场景，例如用户交互、持续变化的业务数据和外部信息。相比简单摘要型记忆，Graphiti 更强调关系、来源和时间变化。

## MCP Server 能力

Graphiti MCP Server 通过 MCP 暴露知识图谱能力，核心能力包括：

- Episode management：添加、读取、删除 text/message/json episodes。
- Entity management：搜索和管理实体节点与关系。
- Search：对节点摘要和事实关系做语义/混合搜索。
- Group management：用 `group_id` 隔离不同知识域。
- Graph maintenance：清理图谱、重建索引。
- Database support：支持 FalkorDB 和 Neo4j 等后端。
- Provider support：支持 OpenAI、Anthropic、Gemini、Groq、Azure OpenAI 等 LLM provider，以及 OpenAI、Voyage、Sentence Transformers、Gemini 等 embedding provider。
- HTTP transport：HTTP MCP endpoint，当前本机使用 `/mcp`。
- Queue-based processing：episode 摄取异步入队，受并发限制控制。

## 本机实际工具名

当前本机 Graphiti MCP 工具清单以 `scripts/list-graphiti-tools.ps1` 输出为准，已确认包括：

- `add_memory`
- `search_nodes`
- `search_memory_facts`
- `delete_entity_edge`
- `delete_episode`
- `get_entity_edge`
- `get_episodes`
- `clear_graph`
- `get_status`

## 部署模式

官方文档提供：

- 默认 FalkorDB Docker 组合。
- Neo4j Docker Compose 组合。
- stdio transport，适用于仅支持 stdio 的 MCP client。
- HTTP transport，适用于支持 HTTP MCP 的 client。

本机采用 Neo4j + Docker HTTP 版部署，固定通过 `http://127.0.0.1:8010/mcp` 访问。

## Telemetry

Graphiti core 包含匿名 telemetry，官方说明不收集 episodes、nodes、edges 内容。可通过 `GRAPHITI_TELEMETRY_ENABLED=false` 禁用。

## 官方链接

- Graphiti repo: https://github.com/getzep/graphiti
- Graphiti MCP Server README: https://github.com/getzep/graphiti/blob/main/mcp_server/README.md
