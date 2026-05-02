# 本机 Graphiti 部署现状

## 当前入口

- Health: `http://127.0.0.1:8010/health`
- MCP: `http://127.0.0.1:8010/mcp`
- Neo4j Browser: `http://127.0.0.1:7474`
- Neo4j Bolt: `127.0.0.1:7687`

## 路径

- Project: `D:\Program_Files\graphiti`
- MCP server: `D:\Program_Files\graphiti\mcp_server`
- Docker compose: `D:\Program_Files\graphiti\mcp_server\docker\docker-compose-neo4j.yml`
- Env file: `D:\Program_Files\graphiti\mcp_server\.env`
- Runtime config: `D:\Program_Files\graphiti\mcp_server\config\config-docker-neo4j.yaml`
- Healthcheck script: `D:\Program_Files\graphiti\mcp_server\scripts\graphiti_healthcheck.py`

## Docker containers

- `graphiti-official-graphiti-mcp-1`
  - image: `zepai/knowledge-graph-mcp:standalone`
  - host port: `8010 -> 8000`
  - config source: `D:\Program_Files\graphiti\mcp_server\config\config-docker-neo4j.yaml`
- `graphiti-official-neo4j-1`
  - image: `neo4j:5.26.0`
  - host ports: `7474 -> 7474`, `7687 -> 7687`
  - volumes: `graphiti-official_neo4j_data`, `graphiti-official_neo4j_logs`

## 模型和依赖

- LLM provider: `openai`
- LLM model: `deepseek-v4-flash`
- LLM endpoint: `${OPENAI_API_URL}`，当前指向 DeepSeek OpenAI-compatible endpoint
- Embedder provider: `openai`
- Embedder model: `nomic-embed-text:latest`
- Embedder endpoint: `${OLLAMA_OPENAI_API_URL}`
- Database provider: `neo4j`
- Default group: `main`
- Semaphore limit: from `.env`

## 已验证状态

端到端健康检查应覆盖：

- 配置层：LLM、embedder、relay 地址。
- 服务层：`/health`、`get_status`。
- 依赖层：Relay `/models`、Ollama `/v1/models`、Ollama `/v1/embeddings`。
- 摄取层：`add_memory -> get_episodes`。
- 存储层：Neo4j `Episodic` count 增量。
- 日志层：Graphiti MCP 近期无摄取错误。

## 已知风险

- 宿主机直接使用 Docker 内配置运行 Python 时，`bolt://neo4j:7687` 可能无法解析；`neo4j` 主机名只在 Docker 网络内有效。
- `/health` 只表示服务进程健康，不代表 LLM relay、Ollama embedding、Neo4j 摄取链路都正常。
- `add_memory` 异步入队，不能把返回 queued 视为摄取完成。
- 本机 Graphiti 源码目录可能存在本地改动；维护前必须先查看 `git status`。
- 不要把 `.env` 中的 API key、Neo4j 密码等秘密写入笔记或回复。
- 健康检查脚本应以 runtime YAML + `.env` 为事实源动态读取 LLM 和 embedding 模型，不应硬编码旧模型名。
