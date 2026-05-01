---
name: obsidian-notes
description: 用于在本机 Obsidian 仓库中查询、读取和写入保留笔记。仅处理 `30_Knowledge (知识)` 与 `80_Attachments (附件)` 下的正文、双链、附件和导航。
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# Obsidian Notes

这个 skill 只负责精简后的 Obsidian 笔记系统。

## 适用范围

- 仅在用户明确需要查看、搜索、整理或写入保留的 Obsidian 笔记时使用。
- 当前只应面向两个保留目录：
  - `30_Knowledge (知识)`
  - `80_Attachments (附件)`
- 不要把 Obsidian 当作主记忆系统；长期偏好、经验、规则、部署事件和历史决策优先走 Graphiti。

## 默认笔记仓库

- `D:\12070\Documents\workspaces\Obsidian仓库\日常工作记录`

## 读取规则

1. 优先按相对路径读取单篇笔记，不要先全库扫描。
2. 直接使用 UTF-8 读取保留目录下的文件，不再依赖旧的 `00_System (系统规范)` 或 `memory_cli.py`。
3. 如果必须用 PowerShell 回退读取，先强制 UTF-8：
   - `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`
   - `$OutputEncoding = [Console]::OutputEncoding`
   - `Get-Content -Encoding UTF8 ...`

## 写入规则

1. 只修改用户明确要求的保留笔记或当前任务直接涉及的保留笔记。
2. 保持 frontmatter 简洁，优先保留：`type`、`status`、`tags`、`related`、`source_attachments` 与必要的业务关系字段。
3. 不再为 AI 检索补充旧 memory sidecar、schema 治理或 AI 专用冗余字段。
4. 写入后检查路径、wikilink、附件引用和 Markdown 结构是否正确。

## 配合 skills

1. `obsidian-cli`：笔记查找、批量读写、属性操作
2. `obsidian-markdown`：Obsidian Markdown、wikilink、embed 结构
3. `json-canvas`：仅 `.canvas`
4. `obsidian-bases`：仅 `.base`
5. `defuddle`：仅在把网页内容整理进保留笔记时使用
