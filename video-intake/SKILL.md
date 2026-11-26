---
name: video-intake
description: 在 Windows 用已安装的 VideoIntake.exe 收存公开视频、提取音频或字幕、生成和导出视频笔记，以及查询、恢复任务或核对成果去重。用于 B站、抖音等视频和本机媒体；普通网页、PDF、Office 走对应工具。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 视频收存与笔记

## 查找本机程序

采集、转写、笔记和进度由同一个 `VideoIntake.exe` 提供。Windows PowerShell 从当前用户的持久安装绑定读取路径，确保老终端也能找到当前应用。

```powershell
$app = [Environment]::GetEnvironmentVariable('VIDEO_INTAKE_EXE', 'User')
if ([string]::IsNullOrWhiteSpace($app)) { $app = $env:VIDEO_INTAKE_EXE }
if ([string]::IsNullOrWhiteSpace($app)) {
    $app = (Get-Command VideoIntake.exe -CommandType Application -ErrorAction SilentlyContinue).Source
}
if ([string]::IsNullOrWhiteSpace($app) -or -not (Test-Path -LiteralPath $app -PathType Leaf)) {
    throw '找不到已安装的 VideoIntake.exe，请检查本机安装路径或 VIDEO_INTAKE_EXE。'
}
& $app --json capabilities
```

实际调用使用 `& $app --json <命令> <参数>`，从任意工作目录运行。日常任务省略 `--runtime-root`，让命令和桌面应用共用任务、成果和设置。隔离测试使用单独的数据目录。同一台 Windows 的 WSL 可通过 Windows 互操作运行该 exe：从 Windows 用户环境读取路径，用 `wslpath -u` 转换程序路径，传给程序的文件路径用 `wslpath -w` 转成 Windows 路径。

## 执行任务

| 命令 | 用途 |
| --- | --- |
| `collect '<公开内容链接>'` | 收存视频或受支持的文章，复用已有成果；仅采集，不访问总结模型 |
| `collect-note '<公开内容链接>' --screenshots` | 收存后生成笔记，附首帧；不需要图片时省略 `--screenshots` |
| `note-file --file '<本机音视频>'` 或 `note-file --artifact '<成果编号>'` | 用本机文件或已采集成果生成笔记预览 |
| `collected list` / `collected get '<成果编号>'` | 查询来源、文件路径、长度和 SHA-256 校验值 |
| `collected import --file '<本机视频>' --source-url '<原链接>'` | 登记本机已保存的视频并保留来源，适合无预览参数的视频号链接 |
| `jobs list` / `jobs get '<任务编号>'` | 查询任务、状态和产物 |
| `jobs resume '<任务编号>'` | 只恢复已确认失败、可恢复的任务 |
| `jobs export '<任务编号>' --out '<目标.zip>'` | 导出已完成笔记的正文和附件，保留 Markdown 相对链接 |
| `extract --file '<本机视频>' --audio --out-dir '<目录>'` | 提取音频；提取站点字幕使用 `extract --subs --url '<链接>' --out-dir '<目录>'` |
| `doctor` / `resolve '<链接>'` | 只读核对运行条件或来源编号 |

`collect`、`collect-note` 和 `note-file` 可加 `--dry-run` 校验输入，测试时不会建任务或访问内容平台、模型接口。笔记使用应用模型设置，用户明确指定型号时用 `--model '<型号>'`。使用应用设置里已授权的登录资料，诊断只回报是否存在。用户要求确定下载目录或旧回执时，可用同一 exe 的 `download --url '<链接>' --out-dir '<目录>' --work-root '<任务目录>'`，参数和收据按既有调用契约核对。

## 核对与交付

- 解析 UTF-8 JSON。新任务命令先看 `success`、`data.status`、`data.id`；退出值 0 表示命令成功，1 明确失败，2 参数有误，3 结果未知。完成时用新进程执行 `jobs get` 核对 `outputs_intact`，并按 `outputs` 或 `collected get` 读取实际文件。只读列表支持 `--limit` 和 `--cursor`，以返回的 `next_cursor` 翻页。
- `UNKNOWN` 或中断后先按任务编号只读核对，保留回执和成果。没有终态或归属证据时保持待核对，避免重新提交、重新下载或重复调用模型。
- 新笔记先保存在应用任务内。需要在 Obsidian 使用正文和附件时，从应用导出 ZIP，按目标仓库已授权的目录和管理规范保存并检查相对链接。原有固定业务、旧回执及原 BiliNote 入口的说明见 [旧版调用与恢复](references/legacy-cli.md)。
