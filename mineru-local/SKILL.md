---
name: mineru-local
description: 仅在 Zerox 转 Markdown 效果不佳，或者用户明确要求时，使用 D:\Program_Files\MinerU-Docker 的 Docker 高精度 MinerU 作为备份方案。适用于长篇预算标准、正文夹大量表格、需要 hybrid-auto-engine 兜底的场景。不要再使用已退役的 D:\Program_Files\MinerU 本地 pipeline 安装。
---

# MinerU Local

当前这不是默认主流程，而是 Zerox 之后的备份方案。

## 适用范围

- Docker 备份目录：`D:\Program_Files\MinerU-Docker`
- 固定包装脚本：
  - `D:\Program_Files\MinerU-Docker\run-mineru-docker.cmd`
  - `D:\Program_Files\MinerU-Docker\run-mineru-docker.ps1`
  - `D:\Program_Files\MinerU-Docker\start-api.cmd`
  - `D:\Program_Files\MinerU-Docker\start-gradio.cmd`
  - `D:\Program_Files\MinerU-Docker\stop-all.cmd`
- 固定服务端口：
  - API：`18000`
  - Gradio：`7860`

## 使用规则

1. 默认不要主动使用本技能；文档转 Markdown 优先走 `zerox-local`。
2. 默认使用 `hybrid-auto-engine`，并开启表格识别。
3. 不再使用 `D:\Program_Files\MinerU` 本地安装，也不要再走 `pipeline` 本地路线。
4. 在 Windows 下遇到中文路径、空格路径时，优先用 `run-mineru-docker.ps1` 或 `run-mineru-docker.cmd`。

## 原因

- Zerox 在短表单、验收模板、流程附件上通常更稳，应作为默认主流程。
- Docker 版 MinerU 更适合长篇预算标准和正文夹大量表格的 PDF，作为高精度备份更合理。
- 当前 Docker 方案已和宿主机本地安装解耦，不需要保留旧的本地 pipeline 配置。

## 当前备份结论

- Docker 镜像：`mineru:latest`
- 已验证显卡：`NVIDIA GeForce RTX 5080`
- 已验证 `hybrid-auto-engine` 可成功输出 Markdown
- 当前健康检查：`http://127.0.0.1:18000/health`

## 关于后端

- `hybrid-auto-engine`：默认推荐，高精度备份方案。
- `vlm-auto-engine`：可选，仅在用户明确要求时测试。
- `pipeline`：已退役，不再作为当前环境的推荐路径。

## 推荐命令

```powershell
D:\Program_Files\MinerU-Docker\run-mineru-docker.cmd -Path "输入文件或目录" -Output "输出目录"
```

启动常驻 API：

```powershell
D:\Program_Files\MinerU-Docker\start-api.cmd
```

## 排查顺序

1. 先确认 `zerox-local` 是否已经满足需求，不要直接回退到 MinerU。
2. 再看 Docker API 是否健康：
   - `Invoke-WebRequest http://127.0.0.1:18000/health`
3. 再用 `run-mineru-docker` 运行。

## 不要做的事

- 不要再恢复或重装 `D:\Program_Files\MinerU` 本地 pipeline 安装作为默认方案。
- 不要把本技能当成文档转 Markdown 的第一选择。
- 不要在没有必要时同时启用 Zerox 和 MinerU 两条批处理链路。
