---
name: mineru-local
description: 仅在 Zerox 转 Markdown 效果不佳或用户明确指定时，使用 D:/Program_Files/MinerU-Docker 的 Docker MinerU 4 处理中文扫描件、长篇文档和密集表格；默认 standard 档位。不得使用已退役的本地 pipeline 安装。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# MinerU Docker 文档解析

执行平台：Windows，实际解析在本机 Docker 的 Linux 容器内完成。先核对目标主机、Docker、显卡和固定入口；本技能不代表 Linux/WSL 执行端已交付。2026-10-14 已完成本机 MinerU 4.0.12 正式切换，三份自制中文 PDF 与 CSV 的实际转换验收。

## 路由与固定入口

1. 文档转 Markdown 仍优先 zerox-local；用户指定 MinerU，或 Zerox 结果不佳时使用本技能。
2. 根目录：D:/Program_Files/MinerU-Docker。
3. 一次性转换：run-mineru-docker.cmd 或 run-mineru-docker.ps1。中文和空格路径加引号。
4. 常驻 API：start-api.cmd，端口 18000；可选网页界面沿用 start-gradio.cmd，端口 7860，此入口同时启用 API。
5. stop-all.cmd 只停止本配置服务并保留容器。网页界面和独立 OpenAI 兼容服务不是默认启动项，本轮没有完成其界面交互验收。
6. 不恢复 D:/Program_Files/MinerU 的旧本地 pipeline 安装。

## 正式版本与验收范围

- 固定镜像：mineru:4.0.12-20261014；MinerU 4.0.12、vLLM 0.21.0+cu129、PyTorch 2.11.0+cu129。
- 显卡：NVIDIA GeForce RTX 5080；真实显卡运算及 vLLM 底层组件加载已通过。
- standard：中文文字层 PDF、扫描 PDF、合并表格 PDF 均通过；已核对编号、中文内容、单位数值、三列合并表头和单页结构。
- flash：原生 CSV 经中文和空格路径的命令入口、正式 API 转换通过。
- 未进行长文档吞吐量、所有格式、basic/advanced 档位或训练能力的全面验收。

普通 vLLM 0.21.0 包曾在实际解析时缺少 CUDA 13 库，现用官方 CUDA 12.9 包解决。包版本检查和普通模块导入不能替代底层组件及真实解析测试。原失败任务、原 3.1.4 容器已保留，不能为让状态好看而清除或重放。

## 一次性转换

~~~powershell
D:\Program_Files\MinerU-Docker\run-mineru-docker.cmd -Path "输入文件或目录" -Output "输出目录"
~~~

默认使用 standard、自动识别文字、解析全部页、输出 Markdown。包装脚本调用 mineru-kit parse；不要换成默认页数不同的文档库命令入口。

- -Tier：flash、basic、standard、advanced；本机 PDF 默认用已验收的 standard，原生 CSV 可用 flash。
- -Format：markdown、middle_json、zip。
- -Pages：PDF 可指定从 1 开始的范围，如 1-5,8；默认 all。原生文件整本解析，不传页码参数；包装脚本已正确处理默认全篇情形。
- -Method：默认 auto，按需要选 txt 或 ocr。
- 已有固定镜像时直接复用；只有镜像缺失或明确指定 -Rebuild 才构建。-NoBuild 禁止自动构建，不能与 -Rebuild 同时使用。
- 旧 -Backend hybrid-auto-engine / vlm-auto-engine 分别提示迁移到 standard / advanced，不保证与旧模式质量完全等效。旧 pipeline 和 -Lang 明确报错；4.x 自动识别语言。

输入按只读方式挂载。为本次结果使用清晰、独立的输出目录，保留原文件和已有产物；失败先核对具体错误及已生成结果，不盲目重复整批转换。

## API 与输出兼容

当前健康检查：http://127.0.0.1:18000/v1/health。成功状态为 ok，版本应为 4.0.12。

新版使用官方 /v1/tasks 提交和查询任务，/v1/tasks/{task_id}/result 下载结果。提交结果未知时先查原任务；已失败的业务任务不能未经授权重新提交。根路径 /health、/file_parse 等旧接口不可当作新版兼容入口，不自行加桥接服务。

已实际回读 Markdown、middle_json、structured_content 和 ZIP。结构化文档按实际 pages 等字段读取，不能沿用旧 pdf_info、_backend 字段假设。完整迁移说明见[官方 4.0 文档](https://github.com/opendatalab/MinerU/blob/mineru-4.0.12-released/docs/zh/reference/migration_4.md)。

## 资源与排障

1. 先核对实际容器、固定镜像和 /v1/health，再区分上传、排队、模型加载、解析及结果下载。健康成功不等于模型推理成功。
2. 本机 API 并发为 1，模型按需加载；第一次解析可能包含加载、即时编译和预热。不要仅凭界面进度停留认定失败。
3. standard 解析前核对共享显卡占用，避免常驻 API 与临时 CLI 同时加载两套模型。资源不足先回读任务和占用，不擅自关闭 Ollama、ComfyUI 或其他用户服务。
4. 已验证基础镜像、官方引擎文件和重建说明在根目录及 releases/4.0.12；以根目录 README 和本次维护记录为准。恢复按配置差异和精确原容器执行，保全切换后新结果，不全局清理 Docker 或覆盖旧数据。

## 其他替补

需要本地扫描识别、表格结构，或现有服务不可用、结果不佳时可读 docling-local；简单文字提取可读 markitdown-local。既有业务默认路线保持，用户明确指定时优先。
