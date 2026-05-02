# 记忆系统情况清单

- 生成时间：2026-05-02T08:32:11+00:00
- Registry：`D:\Program_Files\graphiti\mcp_server\config\memory-registry.yaml`
- Runtime YAML：`D:\Program_Files\graphiti\mcp_server\config\config-docker-neo4j.yaml`

## 当前运行配置

- Graphiti LLM：`deepseek-v4-flash`（profile: `deepseek-v4-flash`）
- Embedding：`nomic-embed-text:latest`（profile: `ollama-nomic-embed-text`）
- Daemon assistant：`deepseek-v4-flash`
- Reranker：`disabled`，启用：`False`
- 默认 group：`${GRAPHITI_GROUP_ID:main}`

## Groups

- `main` [active]：全局长期记忆。全局用户偏好、长期规则、本机稳定环境事实和跨项目可复用经验。
- `project-graphiti-memory` [active]：Graphiti 记忆系统项目。Graphiti 服务本体、graphiti-memory skill、常驻记忆服务、模型配置和记忆治理相关事实。
- `source-codex` [active]：Codex 会话来源。Codex 会话采集与解析相关的来源治理信息，不用于泛化用户偏好。
- `source-claude` [active]：Claude 会话来源。Claude 会话采集与解析相关的来源治理信息，不用于泛化用户偏好。
- `archive-old-daemon` [archive]：旧 daemon 写入审计。旧常驻服务策略写入内容的审计或迁移暂存组；默认搜索不包括。

## 实体与关系

- 实体类型以 runtime YAML 的 `graphiti.entity_types` 为准：`Preference`、`Requirement`、`Procedure`、`Person`、`Organization`、`Project`、`Environment`、`Device`、`Tool`、`Document`、`Location`、`Event`、`Topic`、`Object`
- 关系类型以 runtime YAML 的 `graphiti.edge_types` / `graphiti.edge_type_map` 为准；未配置时使用 Graphiti 默认关系抽取。

## 使用规则

- 写入前先选择已登记 group；未知 group 默认禁止直写。
- `main` 只保存全局长期事实；项目、领域、来源类事实应进入对应登记 group。
- `archive-*`、`system`、`deprecated` 默认不参与日常搜索。
- 常驻服务写入后端为 skill CLI，MCP 仅用于兼容状态与排故。
