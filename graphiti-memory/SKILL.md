---
name: graphiti-memory
description: 用于稳定使用和排查本机 Graphiti 自建记忆系统，包括查询历史记忆、写入长期经验/偏好/部署事件、检查 Docker HTTP 版 Graphiti + Neo4j 部署、验证 MCP 工具和端到端摄取链路。
---

# Graphiti Memory

本 skill 负责本机 Graphiti 主记忆系统的日常使用、健康检查和排故。

## 适用范围

- 查询历史记忆、长期偏好、历史决策、经验、部署事件和规则变更。
- 写入需要跨会话保留的事实、偏好、操作经验、修复记录或部署事件。
- 排查 Graphiti MCP、Neo4j、Docker 容器、LLM relay、Ollama embedding 或摄取链路问题。
- 核对本机 Graphiti 部署状态、MCP 工具清单和官方功能边界。

## 固定入口

- Graphiti health: `http://127.0.0.1:8010/health`
- Graphiti MCP endpoint: `http://127.0.0.1:8010/mcp`
- Neo4j Browser: `http://127.0.0.1:7474`
- Graphiti project: `D:\Program_Files\graphiti`
- MCP server dir: `D:\Program_Files\graphiti\mcp_server`
- Docker compose file: `D:\Program_Files\graphiti\mcp_server\docker\docker-compose-neo4j.yml`
- Config file: `D:\Program_Files\graphiti\mcp_server\config\config-docker-neo4j.yaml`

## 硬规则

1. 默认使用当前 Docker HTTP 版 Graphiti 部署，不把其他 memory MCP 当作主记忆入口。
2. 查询前尽量缩小 `group_id`；默认只查 `main`，除非任务明确需要其他 group。
3. 写入时默认 `group_id=main`；`domain-*`、`project-*`、`source-*`、`archive-*` 必须有明确 slug。
4. 不确定分组时写入 `main`，并在回复中说明推荐目标 group。
5. `add_memory` 是异步入队；重要写入后必须用 `get_episodes` 或搜索确认摄取完成。
6. 允许查询和新增记忆；`delete_episode`、`delete_entity_edge`、`clear_graph`、重启容器、修改配置必须先说明影响并得到用户确认。
7. 健康检查允许写入 `healthcheck-smoke-*` 临时 group；不要默认清理，除非用户明确要求。
8. Windows 下处理中文路径和正文时，先设置 UTF-8 输出，再读写或运行脚本。

## 常用工具

Graphiti MCP 当前暴露工具：

- `add_memory`: 添加 text/json/message episode。
- `search_nodes`: 搜索实体节点摘要，可按 `group_ids` 和 `entity_types` 过滤。
- `search_memory_facts`: 搜索实体之间的事实关系。
- `get_entity_edge`: 按 UUID 读取事实边。
- `get_episodes`: 按 group 读取最近 episodes。
- `get_status`: 检查 MCP 服务和数据库连接。
- `delete_episode`、`delete_entity_edge`、`clear_graph`: 破坏性操作，必须先确认。

## 默认流程

1. 判断任务是查询、写入、健康检查还是排故。
2. 查询：优先使用 `search_memory_facts` 和 `search_nodes`，限定 `group_ids=['main']`；需要原始来源时再用 `get_episodes`。
3. 写入：使用清晰 `name`、完整 `episode_body`、明确 `source_description` 和 `group_id`。
4. 写入验证：轮询 `get_episodes` 或搜索新增主题，确认已摄取。
5. 排故：按 `references/troubleshooting.md` 的层级检查，不跳过服务层和依赖层。
6. 需要本机部署事实时，读取 `references/local-deployment.md`。
7. 需要官方功能边界时，读取 `references/graphiti-official-summary.md`。

## 脚本

在本 skill 目录运行：

```powershell
.\scripts\graphiti-status.ps1
.\scripts\list-graphiti-tools.ps1
.\scripts\check-graphiti-health.ps1
```

- `graphiti-status.ps1`：只读检查容器、端口、健康接口和近期错误日志。
- `list-graphiti-tools.ps1`：通过 HTTP MCP session 列出实际工具。
- `check-graphiti-health.ps1`：调用 Graphiti 项目自带端到端健康检查，会写入 `healthcheck-smoke-*` 临时 group。

## 参考资料

- `references/graphiti-official-summary.md`：官方功能、MCP 工具、部署和 telemetry 摘要。
- `references/local-deployment.md`：本机部署现状、路径、容器、端口、模型和风险。
- `references/troubleshooting.md`：排故流程和常见错误。
