---
name: bilinote-video-note
description: 兼容桩：B站/抖音视频转 BiliNote 笔记的旧入口，已并入 video-intake。新任务请使用 video-intake；本目录仅保留旧脚本路径 scripts/save-video-note.ps1 与 bn.ps1 供 Slack 机器人等既有调用者使用。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# BiliNote Video Note（兼容桩）

唯一实现已迁入 `video-intake/scripts/video_intake.py`（`note` 子命令）。本目录不再有独立逻辑：

- `scripts/save-video-note.ps1`：旧参数 `-VideoUrl -Platform -ProviderId -ModelName -VerifiedDownloadReceipt -ProgressReceiptPath -ResumeProgressReceiptPath`、JSON 输出键和失败时 exit 1 均保持，内部转发到核心；找不到核心或 Python 时输出失败 JSON 并 exit 1。
- `references/local-config.json`：只读兼容副本——Slack 机器人（03-05 `video_tasks.note_config`）按相对路径读取它；权威配置是 `video-intake/references/local-config.json`，测试断言共享键一致，修改配置时两处同步。
- `scripts/bn.ps1`：旧最短入口，调用同目录的 save-video-note.ps1。
- 核心查找顺序：`VIDEO_INTAKE_CORE`、相邻 `../../video-intake`、`%USERPROFILE%\.codex\skills\video-intake`、`%USERPROFILE%\.cc-switch\skills\video-intake`；Python 查找顺序：`VIDEO_INTAKE_PYTHON`、`py -3`、`python`。

使用说明、边界、恢复与去重规则见 `video-intake/SKILL.md`。旧调用者迁移到新稳定路径前，不要删除本目录。
