---
name: video-intake
description: 公开视频的下载、音频/字幕提取、BiliNote 知识笔记（B站、抖音写入 Obsidian）、任务状态与断点恢复，以及来源/哈希去重索引。用户要保存视频、提取音频字幕、把 B站或抖音视频转成笔记、核对或恢复视频处理任务时使用；普通网页、PDF、Office 不使用本 Skill。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 视频收存与笔记（video-intake）

执行平台：Windows（yt-dlp、ffmpeg、BiliNote 后端均在 Windows）。核心 CLI 为仓库内唯一实现 `scripts/video_intake.py`（仅 Python 标准库）；WSL 经 SSH 调用同一 Windows 副本，不另装一份。旧入口 `video-download`、`bilinote-video-note` 只是兼容桩，不再有独立逻辑。

## 子命令（`python scripts/video_intake.py <子命令>`）

| 子命令 | 用途 | 要点 |
| --- | --- | --- |
| `download --url U --out-dir D [--work-root W]` | 单条公开视频下载 | `--no-playlist`；沿 `.part` 续传；核验存在/非空/时长/音视频轨；SHA-256；写回执并追加索引；同 URL 或同来源已有核验通过的成品则离线复用；不同来源同内容标记 `duplicate_of` 但保留两份 |
| `download --adopt F --url U --source-id ID` | 登记已用其他方式（如机器人浏览器回退）下载的文件 | 同样核验并生成可供 `note` 使用的回执 |
| `extract --receipt R --audio` / `--subs --url U` | 提取音频或字幕 | 回执摘要不符则拒绝；不覆盖已有输出 |
| `note --video-url U [--platform --provider-id --model-name --verified-download-receipt --progress-receipt --resume-progress-receipt]` | 经本机 BiliNote 生成笔记并写入 Obsidian | 参数、JSON 输出与旧 `save-video-note.ps1` 一致；失败 exit 1 |
| `status [--receipt --progress-receipt --source-key --work-root --query-backend]` | 只读状态 | 不改任何文件 |
| `resume --progress-receipt OLD --new-progress-receipt NEW --receipt R --url U` | 恢复笔记；对下载回执则只回读 | 原失败回执与缓存哈希保留，恢复基线已存在则拒绝 |
| `index list|check --work-root W [--source-key K|--sha256 H]` | 追加式索引查询 | `video-intake-index.jsonl` 仅追加 |

退出码：0 成功；1 明确失败；2 参数错误；3 结果未知（超时、遗留锁）。未知结果先查原回执、`.part`、索引与后端状态，不重放；陈旧锁 `locks/*.lock` 不自动清除。

## 工具与配置

yt-dlp / ffmpeg / ffprobe 优先级：命令行参数 > 环境变量 `VIDEO_INTAKE_YT_DLP|FFMPEG|FFPROBE` > `references/local-config.json` 的 `tools` > PATH。配置文件也含 BiliNote 后端（`127.0.0.1:8483`）、结果目录、Obsidian 正文/附件根、默认模型角色（读中央 `ai-models.json` 的 `strong`，显式 `--model-name` 优先）。测试或迁移用 `--config` 或 `VIDEO_INTAKE_CONFIG` 指向临时配置。不要修改端口和归档根，除非用户明确要求。

## 边界

- 笔记平台只支持 `bilibili`、`douyin`；已核验媒体上传入口目前只支持抖音，且只发往本机回环的 BiliNote；回执的来源、编号、大小、SHA-256 必须与文件一致。
- 站点登录资料、Cookie、令牌不入参数、日志、回执或源码；抖音解析不兼容时的受管浏览器回退属于调用方（01-08 / 03-05 机器人），完成后用 `download --adopt` 登记。
- 一视频一笔记、一视频一附件目录；按 `source_id` 去重更新；转写全文和任务 JSON 放附件，不入正文；视频正文不写入个人事实记忆。
- 真实原视频、笔记、Obsidian、业务台账、失败/未知回执只读保留；验证只用合成媒体、本地站点和 BiliNote 桩（见 `tests/`）。
- 日常调用方：01-08 视频链接收存与 03-05 `slack-12070-agent`（旧路径 `…/bilinote-video-note/scripts/save-video-note.ps1` 兼容入口保持参数与退出行为）。
- 官方依据：[yt-dlp](https://github.com/yt-dlp/yt-dlp)。
