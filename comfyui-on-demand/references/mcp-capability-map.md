# ComfyUI MCP 到按需 Skill 的迁移边界

原映射按 comfyui-mcp@0.37.0 编写；2026-10-22 本机已核安装版本为 0.52.205，旧版本号不能证明当前全量能力已经迁移。下表是既有按需接口的静态分工，保留原0.37.0历史依据；0.52.205 的高级控制面、远程/云端与编排功能未宣称等价。当前本机8188健康检查连接被拒绝，本轮未启动服务，不能据静态映射称服务可用。具体版本与覆盖证据见03-05维护的[工具按需运行入口](D:/ws/03-env/03-05-codex-env-win/docs/Codex配置建议.md)及本轮仓外WPS_COMFY_ROUTE_EVIDENCE.json。第三方完整控制平面不适合为每个 Codex 任务常驻启动。

| 原功能组 | 按需 Skill 处理 | 边界 |
| --- | --- | --- |
| 系统状态、GPU、显存 | health、status | 只读，不启动服务。 |
| 节点、模型、模板查询 | nodes、models、templates | 只读；模型目录治理仍由生产管理 Skill 审计。 |
| 工作流解析与节点检查 | workflow-check、preflight | 不隐式改写工作流或替换模型。 |
| 排队、队列、历史与本地输出一致性 | submit、queue、history、reconcile | reconcile 只读；按 prompt ID 对照 queue、窄 history 和用户给定输出路径。提交须用户明确授权和 --yes。 |
| 中断、取消、释放显存 | cancel-running、cancel-pending、free-vram | 会影响运行任务，须用户明确授权和 --yes。 |
| 高级图片/视频/音频一键生成 | 不直接复制 | 必须先在项目中确定工作流、模型和输入，再提交 API 工作流。 |
| 自动下载/删除模型、安装节点、改搜索路径 | 不复制 | 走既有模型审计、下载路由和明确授权流程。 |
| Agent Panel、远程隧道、云 Pod、内置 LLM 编排 | 不复制 | 与本机按需调用无关，避免额外进程、凭据和常驻网络服务。 |
| 启动、停止、重启 ComfyUI | 不自动化 | 仅在用户当前明确要求时，经运行前检查另行处理。 |

ComfyUI 官方自托管服务提供 /system_stats、/object_info、/models、/workflow_templates、/prompt、/queue、/history、/interrupt 与 /free 等接口；本 Skill 仅使用其中满足上述边界的本机接口。
