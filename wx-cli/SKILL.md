---
name: wx-cli
description: 用于通过本机已安装的 wx-cli 查询微信本地数据、导出聊天记录、按会话归档文件，并把同一会话的聊天文档与文件本体整理到固定目录；当用户提到 wx-cli、微信聊天导出、群聊归档、会话文件整理、微信本地搜索、wx init、sessions、history、search、stats、attachments 或 extract 时使用。
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# wx-cli

本 skill 面向本机 Windows 环境下的 `wx-cli` 使用场景，默认优先处理“按会话导出并归档”的工作流。

## 适用范围

- 初始化 `wx-cli` 并验证是否能读取本机微信数据。
- 查询最近会话、聊天记录、搜索结果、统计信息。
- 把某个联系人或群聊导出为 Markdown。
- 把某个会话中的文件消息整理到与聊天记录相同的会话目录。
- 处理 `wx.exe` 的高级命令，如 `history`、`search`、`stats`、`attachments`、`extract`。

## 默认工作流

当用户表达“导出某个聊天 / 群聊并把文件一起整理好”时，优先运行：

```powershell
python -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\wx-cli\scripts\export_session.py" --chat "<会话名>"
```

可选时间范围：

```powershell
python -X utf8 "C:\Users\12070\.cc-switch\skills\自建skills\wx-cli\scripts\export_session.py" --chat "<会话名>" --since 2026-05-01 --until 2026-05-31
```

输出根目录固定为：

```text
D:\Program_Files\wx-cli\output
```

会话目录固定为：

```text
<清洗后的显示名>__<username或@chatroom>
```

目录内固定包含：

- `chat.md`
- `files\`
- `manifest.json`

文件命名固定为：

```text
YYYY-MM-DD_HHmmss__发送者__原文件名.ext
```

重复导出时：

- `chat.md` 覆盖更新
- 文件本体不覆盖
- 同名同内容跳过
- 同名不同内容追加 `__v2`、`__v3`

## 高级模式

当用户不是要整包归档，而是要直接查数据时，直接调用本机：

```text
D:\Program_Files\wx-cli\wx.exe
```

常用命令：

```powershell
wx sessions
wx history "<会话名>" -n 200
wx search "<关键词>"
wx stats "<会话名>" --json
wx attachments "<会话名>"
wx extract <attachment_id> -o <输出文件>
```

## 硬规则

1. 默认使用 `D:\Program_Files\wx-cli\wx.exe`，不要切换到其他副本。
2. 默认输出到 `D:\Program_Files\wx-cli\output`，不要把会话内容散落到多个根目录。
3. 会话导出时，聊天文档、文件本体和清单必须放在同一会话目录。
4. 只把直接 `[文件] ...` 消息识别为文件导出对象；普通链接和引用消息不能伪装成文件。
5. 本 skill 当前仅支持 Windows 本机环境，不做跨平台兼容处理。
6. 如果用户要求新建或修改 skill 本身，只改 `C:\Users\12070\.cc-switch\skills\自建skills\wx-cli` 源目录，不直接改安装副本。
