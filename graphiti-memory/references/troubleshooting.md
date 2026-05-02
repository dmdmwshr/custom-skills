# Graphiti 排故流程

## 0. 基础命令约定

PowerShell 先设置 UTF-8：

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
```

优先从本 skill 运行：

```powershell
.\scripts\graphiti-status.ps1
.\scripts\list-graphiti-tools.ps1
.\scripts\check-graphiti-health.ps1
```

## 1. 服务层

检查：

- `Invoke-RestMethod http://127.0.0.1:8010/health`
- Docker 容器是否存在并 healthy。
- 端口 `8010`、`7474`、`7687` 是否监听。

常见问题：

- `/health` 失败：容器未启动、端口占用、Docker Desktop 异常。
- `/health` 成功但记忆不可用：继续检查 MCP、依赖和摄取层。

## 2. MCP 层

检查：

- 使用 `scripts/list-graphiti-tools.ps1` 列工具。
- 确认 endpoint 使用 `http://127.0.0.1:8010/mcp`。
- Streamable HTTP 需要先 `initialize`，再带 `mcp-session-id` 调 `tools/list`。
- `list-graphiti-tools.ps1` 只是 MCP 工具清单对照脚本；日常记忆读写仍优先使用 `scripts/graphiti_cli.py`。

常见问题：

- `406 Not Acceptable`：缺少 `Accept: application/json, text/event-stream`。
- `404 Not Found`：endpoint 路径或尾斜杠处理不一致；使用脚本里固定地址。
- Windows PowerShell 5.1 直接用 `curl.exe --data $JsonString` 发送 JSON 时可能丢失引号，导致 JSON-RPC `Parse error`；脚本应通过 UTF-8 临时文件和 `--data-binary "@file"` 发送请求。
- 工具名不匹配：以当前 `tools/list` 输出为准，不凭旧文档猜测。

## 3. 依赖层

检查：

- Relay `/models` 是否包含当前 LLM model。
- Ollama `/v1/models` 是否包含 `nomic-embed-text:latest`。
- Ollama `/v1/embeddings` 是否能返回 embedding。
- Neo4j HTTP/Bolt 是否可连接。

常见问题：

- relay 429 或超时：降低并发，检查 `SEMAPHORE_LIMIT`。
- embedding 失败：确认 Ollama 服务和模型存在。
- Neo4j auth 失败：只核对 `.env` 与容器环境，不泄露密码。

## 4. 摄取层

检查：

- `add_memory` 是否返回 queued。
- 用 `get_episodes` 轮询对应 group。
- 用 `search_nodes` 或 `search_memory_facts` 验证可检索。

常见问题：

- queued 但查不到：等待异步处理；再查日志和 LLM/embedding 依赖。
- 同一 group 并发写入慢：Graphiti 会按 group 顺序处理以避免竞态。
- JSON 写入失败：`episode_body` 必须是 JSON 字符串，不是 Python/PowerShell 对象。

## 5. 存储层

检查：

- Neo4j 容器健康。
- Neo4j `Episodic` 节点数量是否随 smoke test 增加。
- Neo4j volume 是否存在。

常见问题：

- 宿主机脚本报 `Cannot resolve address neo4j:7687`：使用了 Docker 内部 hostname；从宿主机应使用 `127.0.0.1:7687`，或在容器内执行。
- 数据缺失：先确认是否查错 `group_id`。

## 6. 日志层

检查：

```powershell
docker logs --since 30m graphiti-official-graphiti-mcp-1
docker logs --since 30m graphiti-official-neo4j-1
```

关注：

- `ERROR`
- `Traceback`
- `429`
- `timeout`
- `Cannot resolve address`
- `authentication`
- `embedding`

## 7. 维护护栏

- 修改配置前先备份原文件。
- 重启容器前说明影响。
- 删除 episode、edge 或 clear graph 前必须用户确认。
- `.env` 和日志中出现的 key/password 不写入笔记或最终回复。
