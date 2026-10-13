---
name: video-download
description: 独立下载公开视频、音频或字幕到本地，使用固定 yt-dlp 入口。用户只要求下载或保存媒体时使用，不调用 BiliNote 笔记生成、转写或云模型；视频知识提取和写入 Obsidian 仍使用 bilinote-video-note。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 独立视频下载

当前 Windows 已安装 yt-dlp 2026.8.19，入口：`C:/Users/12070/.local/share/codex-tools/video-download/2026.8.19/.venv/Scripts/yt-dlp.exe`。它独立于 BiliNote 服务和转写环境，可单独运行。其他平台先核对实际入口。

只下载不启动 BiliNote、不转写、不总结、不写入 Obsidian。需要视频笔记时使用 `bilinote-video-note`；已下载文件可作为后续处理的素材，但不自动追加处理任务。

1. 核对 URL 和用户要求的媒体类型、输出目录。默认单个视频、不下载播放列表；只在明确要求时扩大到列表或分集批量。
2. 先用 `--simulate --dump-single-json --no-playlist` 查询元数据，仅读取标题、时长、格式和大小估计，避免回传完整媒体直链或站点令牌。B 站 `p=` 参数需保留。
3. 使用实际版本 `--help` 认可的选项下载。例如：

```powershell
$download = "C:/Users/12070/.local/share/codex-tools/video-download/2026.8.19/.venv/Scripts/yt-dlp.exe"
& $download --ignore-config --no-playlist --continue --no-overwrites --windows-filenames --retries 3 --fragment-retries 3 --socket-timeout 30 --paths "<输出目录>" --output "%(title).150B [%(id)s].%(ext)s" --print "after_move:filepath" "<视频URL>"
```

音频使用 `-x --audio-format mp3`；字幕使用 `--skip-download --write-subs --write-auto-subs --sub-langs "zh.*,en"`。合并音视频或转音频需要现有 FFmpeg。优先原站点可直接访问的公开内容；需要登录时仅使用用户授权的既有登录资料或受限 Cookie 文件，不打印 Cookie、不导出整个浏览器资料，不绕过付费或访问限制。

失败后先核对文件和 `.part` 断点，再沿同一 URL、格式和输出路径续传；不删除失败文件后冒充首次成功。完成时核验文件存在、非空、媒体时长与音视频轨道，报告最终文件。`--simulate` 或仅有元数据不等于下载完成。

当前仅验收 B 站公开样例的 6 秒片段下载及本地断点续传；不同站点可用性受上游变化影响，抖音等站点需当次实测。官方依据：[yt-dlp](https://github.com/yt-dlp/yt-dlp)。
