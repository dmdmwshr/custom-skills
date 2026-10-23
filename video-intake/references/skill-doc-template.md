---
type: knowledge
status: active
area:
- '[[领域 - AI工具链]]'
tags:
- skill
- video
- bilinote
- codex
related:
- '[[领域 - AI工具链]]'
- '[[skill管理与更新（skill-updater）]]'
---
# BiliNote视频知识提取Skill（bilinote-video-note）

## 结论

这是一个固定调用本机 BiliNote 后端的自建 skill，用于把 `bilibili` / `douyin` 视频提取成 Markdown，并写入 Obsidian 的正文笔记和附件目录，减少主模型手工处理长文本的 token 消耗。

## 固定入口

- skill 根目录：`C:\Users\12070\.cc-switch\skills\自建skills\bilinote-video-note`
- 固定脚本：`scripts\save-video-note.ps1`
- BiliNote 后端：`http://127.0.0.1:8483`

## 默认配置

- 平台：`bilibili`、`douyin`
- 默认模型：`openai / gpt-5.4`
- 正文与附件分离
- 一视频一笔记，一视频一附件目录

## 典型调用

```powershell
pwsh -ExecutionPolicy Bypass -File "C:\Users\12070\.cc-switch\skills\自建skills\bilinote-video-note\scripts\save-video-note.ps1" -VideoUrl "<视频链接>"
```

## 落库规则

- 正文笔记：`30_Knowledge (知识)\01_领域\AI工具链\BiliNote视频笔记\<platform>`
- 附件目录：`80_Attachments (附件)\04_来源与摘录\bilinote-video-note\<platform>\<source_id>`
- 视频正文不写入 Graphiti；Graphiti 只记录 skill 管理规则与工作流约定
