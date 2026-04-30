---
name: bilinote-video-note
description: 当用户要求把 B 站或抖音视频提取为知识笔记、写入 Obsidian、保留视频来源区分、降低 token 消耗、明确要求使用 BiliNote 固定脚本，或只贴出 bilibili / douyin 视频链接希望直接处理时使用。默认调用本机 BiliNote 后端生成笔记，再用固定 PowerShell 脚本落库到 Obsidian。
---

# BiliNote Video Note

这个 skill 负责把 `bilibili` / `douyin` 视频通过本机 BiliNote 后端提取成 Markdown，并按固定结构写入 Obsidian 笔记系统。

## 适用范围

- 用户提供 `http/https` 视频链接，并要求：
  - 提取视频知识
  - 转成 Markdown 笔记
  - 写入 Obsidian
  - 区分视频来源与附件
  - 尽量少消耗 token
- 用户只贴出 `bilibili` / `douyin` 链接，未补充太多说明，但意图明显是“直接提取并入库”
- 默认只处理：
  - `bilibili`
  - `douyin`

## 不适用范围

- `youtube`
- `kuaishou`
- `local`
- 需要浏览器自动化才能完成的站点流程
- 用户明确要求手工重写整篇内容，而不是复用 BiliNote 输出

遇到不支持的平台时，直接停止本 skill，并返回明确的平台错误。

## 本机约定

先读取 `references/local-config.json`，确认：

- BiliNote 后端地址
- 默认 provider/model
- 正文笔记目录
- 附件目录
- Graphiti 记录组

当前固定约定：

- BiliNote 后端：`http://127.0.0.1:8483`
- 默认模型：`openai / gpt-5.4`
- 正文与附件分离存放
- `8001` 不参与本 skill

## 标准流程

1. 优先运行固定脚本：

```powershell
pwsh -ExecutionPolicy Bypass -File "C:\Users\12070\.cc-switch\skills\自建skills\bilinote-video-note\scripts\save-video-note.ps1" -VideoUrl "<URL>"
```

如果只需要最短入口，优先运行：

```powershell
pwsh -ExecutionPolicy Bypass -File "C:\Users\12070\.cc-switch\skills\自建skills\bilinote-video-note\scripts\bn.ps1" "<URL>"
```

2. 如用户显式指定平台或模型，再追加参数：

```powershell
-Platform "bilibili|douyin" -ProviderId "<provider_id>" -ModelName "<model_name>"
```

3. 固定脚本会完成以下动作：
   - 识别平台
   - 调用 `POST /api/generate_note`
   - 轮询 `GET /api/task_status/{task_id}`
   - 提取 `markdown`、`transcript`、`audio_meta`
   - 生成正文笔记
   - 生成附件目录与原始导出文件
   - 按 `source_id` 做去重更新

4. 脚本输出结构化 JSON，至少包含：
   - `success`
   - `platform`
   - `source_id`
   - `task_id`
   - `note_path`
   - `attachments_dir`
   - `backend_result_path`
   - `message`

5. 如果用户后续要继续整理、增补或引用笔记内容，再读取生成后的 `.md`，不要重新让模型从视频 URL 手工总结。

## 落库规则

- 正文笔记目录按平台分开：
  - `...BiliNote视频笔记\bilibili`
  - `...BiliNote视频笔记\douyin`
- 附件目录按平台和 `source_id` 分开：
  - `...bilinote-video-note\bilibili\<source_id>`
  - `...bilinote-video-note\douyin\<source_id>`
- 一视频一笔记，一视频一附件目录。
- 正文只保留高价值内容；转写、任务 JSON、BiliNote 原始 Markdown 放附件目录。

## 注意事项

- 默认不要手工拼接长摘要，优先复用 BiliNote 已生成的 `result.markdown`。
- 默认不要把 transcript 全文塞进正文。
- 默认不要修改 `references/local-config.json` 里的端口和根目录，除非用户明确要求。
- 如果脚本返回已有 `note_path`，优先更新原笔记，不重复新建。
- 抖音来源在当前本机上优先使用分享文案或 `v.douyin.com` 短链；裸 `https://www.douyin.com/video/<id>` 直链存在不稳定情况。
- 如需把 skill 管理规则写入长期记忆，按现有 Graphiti 治理写入 `skill-management` 与 `workflow-content-ingest`，不要把视频正文写进 Graphiti。
