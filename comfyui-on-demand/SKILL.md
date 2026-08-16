---
name: comfyui-on-demand
description: 按需调用本机 ComfyUI 的官方 HTTP 接口，检查健康、显存、节点、模型、工作流、队列和历史，并在用户明确要求时提交、取消任务或释放显存。用户提到运行本机 ComfyUI、提交工作流、查看队列、检查节点或显存、取消渲染时使用；普通创作规划、模型下载和 ComfyUI 文件治理使用 comfyui-production-manager。
---

# ComfyUI 按需控制

仅在当前用户明确需要本机 ComfyUI 运行能力时使用。本 Skill 直接访问固定的本机回环地址，不启动 comfyui-mcp、不启动或修复 ComfyUI 服务，也不连接远程地址。

## 基本边界

- 默认地址为 http://127.0.0.1:8188；--url 仅接受 127.0.0.1、localhost 或 ::1。
- 每次实际调用前先运行 health。服务不可用时如实报告，不自动启动、重启、重试或切换端口。
- 普通读取可直接执行；提交、取消和释放显存必须由用户在当前请求明确要求，并附带 --yes。
- 仅提交用户已审阅的 API 格式工作流 JSON。Skill 不根据一句提示词私自选择模型、下载模型或生成隐式工作流。
- 不安装或删除模型/自定义节点，不修改 ComfyUI 设置、工作流母版或输出文件；这些工作交由 comfyui-production-manager 的既有审计流程。
- 不使用全局 comfyui MCP。若需要启动、停止或重启 ComfyUI，先取得当前用户明确授权，并按 comfyui-production-manager 的运行前验证处理。

## 读取与预检

先使用固定 Python 运行 scripts/comfyui_api.py：

| 用户目标 | 操作 |
| --- | --- |
| 确认本机服务、版本、GPU 与显存 | health |
| 查看服务与执行队列概览 | status |
| 查询节点类型 | nodes --query "关键词" |
| 列出模型目录或某类模型 | models 或 models --folder checkpoints |
| 查看工作流模板 | templates |
| 检查 API 工作流 JSON 结构 | workflow-check --workflow "文件.json" |
| 对照当前节点集做运行前检查 | preflight --workflow "文件.json" |
| 查看队列 | queue |
| 查看历史或指定任务 | history 或 history --prompt-id "任务 ID" |

workflow-check 只做本地结构检查；preflight 会额外读取本机服务与节点定义。缺节点、模型、输入或显存风险时停止在诊断阶段，不排队执行。

## 明确授权后的写入动作

只有当前用户清楚要求对应动作时才执行：

| 用户明确请求 | 操作 |
| --- | --- |
| 运行已审阅工作流 | submit --workflow "文件.json" --yes |
| 中断正在运行的渲染 | cancel-running --yes |
| 取消指定待处理任务 | cancel-pending --prompt-id "任务 ID" --yes |
| 卸载缓存模型并释放显存 | free-vram --yes |

提交前始终先运行 workflow-check 和 preflight；完成后回读 history --prompt-id 或 queue。取消和释放显存会影响正在运行的 ComfyUI 工作，必须在结果中说明影响。

## 与生产管理 Skill 的分工

- comfyui-on-demand：短生命周期的本机 API 调用与队列控制。
- comfyui-production-manager：工作流母版、模型依赖、项目资产、下载路由、生产台账和可追溯验证。

迁移映射和未迁移的高影响功能见 references/mcp-capability-map.md。修改脚本后，运行 python scripts/test_comfyui_api.py；测试使用临时模拟服务器，不访问真实 ComfyUI。

## 资源

- scripts/comfyui_api.py：受限本机 ComfyUI HTTP 适配器。
- scripts/test_comfyui_api.py：离线模拟接口测试。
- references/mcp-capability-map.md：原 comfyui-mcp@0.37.0 的功能迁移边界。
