# 命令与 JSON 接口

通过 `scripts/run.ps1 -ToolArguments @('命令', '参数', '--json')` 调用。`run.ps1` 保留工具退出码和 JSON，不改变工具状态。以下只给关键模式，其他参数用 `<命令> --help` 查看。

| 请求 | 参数数组中的命令 |
| --- | --- |
| 检查就绪 | `status --json` |
| 群聊列表 | `sessions --type group --json` |
| 私聊列表 | `sessions --type user --json` |
| 查会话 | `sessions <名称片段> --json` |
| 查通讯录 | `contacts <姓名或标识> --json` |
| 按单位精确查人 | `contacts <姓名> --exact --unit <单位片段> --json` |
| 查单位及职位 | `contacts --unit <单位片段> --position <职位片段> --json` |
| 读会话 | `history <session_key> -n 50 --json` |
| 本地检索 | `search <关键词> --chat <session_key> --since YYYY-MM-DD --until YYYY-MM-DD --json` |
| 会话统计 | `stats <session_key> --json` |
| 列出文件 | `attachments <session_key> --json` |
| 列出媒体 | `attachments <session_key> --media --json` |
| 复制已有附件 | `extract <attachment_id> -o <目标文件或已有目录> --json` |
| 指定附件下载 | `download <attachment_id> --max-mb 200 --timeout 30 --json` |
| 会话归档 | `export <session_key> --with-files --format md -o <新目录> --json` |

`history`、`search`、`attachments` 可用 `--sender`、`--since`、`--until`、`--type group|user|all`、`--kind`、`--order asc|desc`。每页默认 50 条，上限 1000，下一页传回 `--cursor`；筛选条件和排序必须相同。查询默认降序，导出默认升序。只有明确需要时使用 `--include-nonnormal` 查看非正常状态占位条目。

通讯录返回 `id`、`name`、`mobile`、`mobile_state`、`affiliations`。`mobile_state` 为 `available`、`masked`、`missing`，分别表示可读、已隐藏、未填写；可读不等于号码仍有效。每条单位归属包含 `department_id`、`department_path`、`unit_path`、`position_name`、`path_source` 和 `matches_filters`。仅用匹配归属解释单位和职位，保留其他归属。`path_source=organization_tree` 来自完整组织树，`profile_cache` 来自人员详情，`partial_tree` 为不完整层级。职位缺失为 `null`，不猜测。

`contacts` 的 `query` 默认姓名片段匹配，`--exact` 按全名匹配，人员编号始终精确匹配；`--unit` 匹配层级中的单位或部门编号。每页 `-n 50`，上限 1000；下一页使用 `--offset <next_offset>`，保持其余筛选。`total` 是当前缓存的匹配总数，`has_more` 为后续页标记。分页不是固定快照，期间更新的通讯录由调用方按人员编号去重。同名结果不自动归并或任选第一位。

个人会话 `session_key` 为 `USER:…`，群聊为 `GROUP:…`。这两个标识直接使用返回值，不按对方发言的 `sender_id` 拼造会话。同一会话中的自己发言可通过 `sent_by_self` 区分。

成功：返回码 0，外层 `schema_version`、`ok=true`、`source`、`data`。失败：返回码 2，`ok=false` 与 `error.code/message`。用户中断返回码 130。`--help` 和 `--version` 为普通文本输出，不按 JSON 解析。

消息查询的 `data.items` 保留 `message_id`、`session_key`、`conversation_name`、`sender_id`、`sender_name`、`time`、`timestamp_ms`、`kind`、`status_code`、`text` 等。`count` 是本页数量；用 `has_more` 和 `next_cursor` 判断后续页，不能只看 `count` 推断总量。

导出格式为 `md`、`jsonl` 或 `csv`。清单 `manifest.json` 包含查询条件、消息数量、文件映射、完成状态及哈希。`files_missing_locally` 大于零表示缺失附件，不能将 `state=complete` 解释为附件均已下载。CSV 有防公式执行的文本前缀，需要严格原文时选 JSONL。

附件 `attachment_id` 由原消息标识和 original/thumbnail 角色组成；直接使用列表返回值。`resource_id` 是客户端文件资源标识，不是下载命令参数。`local_status` 表示本地可用性，`verification` 说明关联依据，`source_digest_algorithm=null` 表示原字段未证实为标准文件校验算法。文件实际 SHA-256 单独存于 `sha256`。

`source.local_only` 描述消息数据来自本地数据库；附件操作是否联网看 `data.network_used`。不要把两者混为“所有命令都离线”。
