---
name: video-download
description: 兼容桩：独立下载公开视频/音频/字幕的旧入口，已并入 video-intake。新任务请使用 video-intake 的 download/extract 子命令。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 独立视频下载（兼容桩）

下载、续传、时长与音视频轨核验、SHA-256 回执、来源/哈希去重和提取均由 `video-intake`（`scripts/video_intake.py download|extract`）提供，本入口不再有独立逻辑。yt-dlp 的安装位置不变（`C:/Users/12070/.local/share/codex-tools/video-download/2026.8.19/.venv/Scripts/yt-dlp.exe`），Slack 机器人配置里的该路径继续有效。需要笔记时使用 video-intake 的 `note`。
