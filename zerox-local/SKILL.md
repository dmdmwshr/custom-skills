---
name: zerox-local
description: 使用本机 Zerox 封装调用远端模型，把已获本次云端处理授权的文档转换为 Markdown；本机启动不等于离线处理。仅作授权模型解析专用，文字层轻量提取及本地限制按现有专用工具路由。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Zerox Local

执行平台：Windows。先核对本机依赖及用户配置；WSL/Linux 不直接套用此入口，远程载荷须单独核验。安装、发现和业务验收按目标平台分别报告，以对应记录及当前实测为准。

本 skill 面向本机已经安装好的 Zerox。

本机仅负责启动和格式预处理，当前 Sub2API/Gemini 模型在远端，不能称离线。此入口仅用于本次数据范围已获云端处理授权的模型解析；普通文字层读取以官方PDF本地工具为主，轻量正文检索可用MarkItDown，OCR和本地限制沿当前已验专用路由，不再默认所有转Markdown都走Zerox。

默认安装位置与配置：

- 项目目录：`D:\Program_Files\zerox`
- 主执行脚本：`D:\Program_Files\zerox\bin\zerox-local.cmd`
- 端点检查脚本：`D:\Program_Files\zerox\bin\zerox-check.cmd`
- 模型配置文件：`D:\Program_Files\zerox\.env.local`

当前本地封装特点：

- 优先走官方 `Node` 版 Zerox。
- 当前默认走 `Sub2API Antigravity Gemini` 原生端点：`/antigravity/v1beta/models/...:generateContent`。
- 同时保留了 `OpenAI provider` 的自定义 `baseURL` 支持，便于后续切回 OpenAI 兼容反代。
- 本机通过 `LibreOffice + Poppler` 处理多文件类型，不依赖浏览器自动化。
- 输出 Markdown 默认放到 `D:\Program_Files\zerox\output\...`。

支持的常见输入：

- PDF
- `doc/docx/odt/rtf/txt/html/xml`
- `xls/xlsx/csv/tsv`
- `ppt/pptx/odp`
- `png/jpg/jpeg/heic`

## 标准流程

先核当前任务的数据去向授权；原文、原图或含原值回填成品不能因local/本机命名自动送远端。已有明确授权直接沿其范围执行；缺少云处理授权时使用符合任务的本地入口或保留该依赖，不拿端点健康代替材料授权。

1. 如果用户提到最近转换失败、模型不可用、账号池异常，先运行：

```powershell
D:\Program_Files\zerox\bin\zerox-check.cmd
```

2. 执行转换：

```powershell
D:\Program_Files\zerox\bin\zerox-local.cmd --input "<绝对路径或URL>" --output "<输出目录>"
```

常用参数：

- `--pages 1,2,3` 只转换指定页
- `--concurrency 4` 控制并发
- `--maintain-format true` 保持跨页格式一致，质量优先时保持默认开启

3. 成功后读取输出目录中的 `.md` 文件并继续后续处理。

4. 输出结构或关键字段不佳时先保留原结果，再按当前任务的数据限制及现有Skill边界选择Docling/MinerU等专用路径；不因回退重复未确认的云端调用，不以转换成功代替质量核验。

## 故障判断

- 如果 `zerox-check` 的 `modelsStatus=200` 但 `chatStatus!=200`，先检查当前是否用了错误端点；对反重力 Gemini，必须走 `/antigravity/v1beta/models/...:generateContent`，不能走 `/v1/chat/completions`。
- 如果报 `Could not find soffice binary`，检查 `D:\Program_Files\LibreOffice\program\soffice.exe` 是否存在，以及 `.env.local` 中的 `ZEROX_SOFFICE_BIN`。
- 如果报 `pdfinfo` / `pdftoppm` 找不到，检查 `D:\Program_Files\poppler\Library\bin` 是否存在，以及 `.env.local` 中的 `ZEROX_POPPLER_BIN`。

## 注意事项

- `.env.local` 里包含真实端点和密钥，除非用户明确要求，不要改写或外泄。
- 该本地封装已经把输出 Markdown 文件名改成输入文件名，优先读取该重命名后的文件。
- 当前默认模型配置为 `gemini-3-flash`；如果端点恢复后仍要切模型，只修改 `.env.local` 的 `ZEROX_MODEL`。
- 文字、关键数字、单位、页码及表格行列对应分别抽核；端点正常和转换退出成功不证明内容准确，保留原件和失败/部分结果。

## 补充替补入口

复杂扫描/表格或本地处理限制按 `docling-local`、`mineru-local` 现有专用边界；`markitdown-local` 仅作简单正文/检索提取。业务模板与用户明确指定入口优先，但不扩大数据外传授权。各入口按对应主机、真实依赖和已验范围使用，不把本轮未调用的工具写成通过。
