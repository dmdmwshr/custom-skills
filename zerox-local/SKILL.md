---
name: zerox-local
description: 默认优先使用本机安装在 D:\Program_Files\zerox 的 Zerox，把 PDF、Office 文档、文本、HTML、表格和常见图片转换为 Markdown。适用于需要优先走 Zerox 做文档转 Markdown、检查其模型端点状态、运行转换并读取输出结果的场景。
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# Zerox Local

本 skill 面向本机已经安装好的 Zerox。

这是当前默认首选的文档转 Markdown 路线。

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

4. 只有在 Zerox 结果结构明显不佳时，再考虑回退到 `mineru-local` 的 Docker 高精度备份方案。

## 故障判断

- 如果 `zerox-check` 的 `modelsStatus=200` 但 `chatStatus!=200`，先检查当前是否用了错误端点；对反重力 Gemini，必须走 `/antigravity/v1beta/models/...:generateContent`，不能走 `/v1/chat/completions`。
- 如果报 `Could not find soffice binary`，检查 `D:\Program_Files\LibreOffice\program\soffice.exe` 是否存在，以及 `.env.local` 中的 `ZEROX_SOFFICE_BIN`。
- 如果报 `pdfinfo` / `pdftoppm` 找不到，检查 `D:\Program_Files\poppler\Library\bin` 是否存在，以及 `.env.local` 中的 `ZEROX_POPPLER_BIN`。

## 注意事项

- `.env.local` 里包含真实端点和密钥，除非用户明确要求，不要改写或外泄。
- 该本地封装已经把输出 Markdown 文件名改成输入文件名，优先读取该重命名后的文件。
- 当前默认模型配置为 `gemini-3-flash`；如果端点恢复后仍要切模型，只修改 `.env.local` 的 `ZEROX_MODEL`。
- 对短表单、验收模板、流程附件和常见制度文档，默认先相信 Zerox 结果，再决定是否需要备用方案复核。
