---
name: summarize-link-note
description: 当用户提供普通网页、文章 URL，并明确要求提取为笔记、转为 Markdown、保存为 `.md` 或先转成笔记草稿时使用。默认调用本机 `summarize` CLI 以总结模式生成 Markdown 笔记，固定输出到 `D:\12070\Documents\workspaces\summarize-output`。PDF 不使用本 skill，因为 PDF 已有其他专用转换流程。
---

# Summarize Link Note

这个 skill 负责把普通网页链接整理成 Markdown 笔记草稿，默认走总结模式。

## 适用范围

- 用户提供 `http/https` 链接，并要求：
  - 提取为笔记
  - 转为 Markdown
  - 保存为 `.md`
  - 先转成笔记草稿再继续整理
- 默认处理普通网页、文章、文档页、知识库页面。

## 不适用范围

- PDF 链接或本地 PDF 文件
- 已明确是视频页面、视频链接或以视频转写为主的任务
- 需要浏览器扩展或浏览器自动化才能完成的场景

遇到 PDF 时直接停止本 skill，改走现有 PDF 专用流程，不要用 `summarize` 兜底。

## 本机约定

先读取 `references/local-config.json`，确认本机路径与输出目录。

当前默认：

- CLI：`summarize`
- 配置：`C:\Users\12070\.summarize\config.json`
- 输出目录：`D:\12070\Documents\workspaces\summarize-output`
- 默认模式：总结模式，输出 Markdown 笔记
- 当前 `summarize` 已配置 `FIRECRAWL_API_KEY`；处理普通网页时，会优先用 Firecrawl 抽取网页内容，再交给模型总结
- 浏览器扩展/插件 ID 不参与本 skill 的 CLI 配置，也不是必填项

## 标准流程

1. 先判断输入是不是 PDF：
   - URL 路径以 `.pdf` 结尾
   - 明确是 PDF 下载链接
   - 用户说明来源是 PDF

2. 如果是 PDF，停止并说明：
   - 这个 skill 不处理 PDF
   - PDF 走现有专用转换项目

3. 如果是普通网页，优先运行：

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\12070\.cc-switch\skills\自建skills\summarize-link-note\scripts\save-url-to-md.ps1" -InputUrl "<URL>"
```

如果用户明确给了文件名，再加：

```powershell
-OutputName "<文件名>"
```

4. 脚本会固定调用：

```powershell
summarize "<URL>" --format md --markdown-mode readability --length long --plain --stream off --metrics off
```

在当前本机配置下，这条命令会自动优先使用 Firecrawl 抽取网页内容，再输出总结后的 Markdown，并把结果写到固定输出目录。

5. 生成后，用 UTF-8 读取输出 `.md`，再继续后续整理、重写或入库动作。

6. 如果用户明确要求“写成 Obsidian 笔记”或“整理进库”，再配合：
   - `obsidian-notes`
   - `obsidian-markdown`

## 输出规则

- 默认不要把网页原始 HTML 直接塞进笔记。
- 默认保留 `summarize` 产出的总结版 Markdown 作为笔记草稿。
- 文件默认输出到 `D:\12070\Documents\workspaces\summarize-output`。
- 如果用户没有指定文件名，允许脚本自动生成安全文件名。
- 如果用户明确要“全文提取”“原文 Markdown”“不做总结”，再改用 `--extract --format md`。

## 注意事项

- `summarize` 已在本机配置 SiliconFlow 兼容端点与默认模型；不要在无必要时改写 `C:\Users\12070\.summarize\config.json`。
- `summarize` 也已在本机配置 `FIRECRAWL_API_KEY`；不要因为看到浏览器扩展 ID 就误以为还需要额外安装扩展才能使用本 skill。
- 该 skill 当前默认目标是“生成总结版 Markdown 笔记”。
- 用户只说“提取为笔记”“转成笔记”时，默认使用总结模式，不再默认输出原文 Markdown。
- 只有用户明确要“全文提取”“原文 Markdown”时，才切回提取流程。
