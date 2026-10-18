# Windows 运行入口与本地接口

以下为 2026-10-18 对原上游 v2.2.1 Windows Lite 便携版的实机核对。后续版本先读实际安装记录和接口，不能把本页当成永久契约。

## 入口

- 上游：[x554960766/wechatAutoRSSTool](https://github.com/x554960766/wechatAutoRSSTool)。升级按该仓的正式发布核对版本、文件身份和回退点；不要仅凭更新提示改换项目。
- 本机安装记录：`D:/Program_Files/wechatAutoRSSTool/INSTALLATION.json`，含实际 `exe`、版本、校验值和数据根目录。先读记录再核对文件与进程。
- 本地 API 默认 `http://127.0.0.1:5200`；用端口所属进程确认是该应用。辅助脚本只允许环回地址且不跟随重定向。
- 本机便携版运行数据根：`%APPDATA%/WeChat MP Tools`；正文通常在其 `data/articles_full/<公众号>/<文章>`。Linux/源码运行布局可能不同。
- v2.2.1 下载器使用加载时确定的 `OUTPUT_DIR`；仅改 `download_dir` 设置未必改变写入位置。以完成回执的 `results[].path` 和磁盘文件为准，不为迁移目录擅改源码。
- `doctor` 仅输出登录和池状态摘要、收藏公众号、持续任务开关及安装身份，不读取账号池文件。既有自动任务先核对是否冲突，不自动修改。

## API 对照

请求体为 UTF-8 JSON。认证由原应用管理，不导出或拼接微信令牌。

| 用途 | 方法和路径 | 输入/返回及注意事项 |
| --- | --- | --- |
| 登录状态 | `GET /api/auth/status` | 仅保留 `logged_in`、`login_state.status`、`pool_summary` 数字；原响应含敏感信息，禁止整段打印 |
| 二维码 | `POST /api/auth/qrcode`，体 `{}` | `qrcode_url` 或 `qrcode.qrcodebase64`；仅需登录时调用，图片落地供扫码，不回传 Base64 |
| 已收藏公众号 | `GET /api/accounts` | `accounts` 数组，含 `fakeid`、`nickname`，可含 `bookId` |
| 查公众号 | `POST /api/accounts/search` | `{"keyword":"文章链接、名称或 biz:标识"}`；会访问上游，限已授权获取任务 |
| 收藏公众号 | `POST /api/accounts` | `{"fakeid":"...","nickname":"..."}`；本地登记，不等于 RSS 订阅 |
| 文章一页 | `GET /api/articles/list/<URL编码fakeid>?begin=0&count=5` | `articles`、`begin`、`count`、估算 `total`、`can_msg_continue`；HTTP 200 也可能含 `error` 或受限空页 |
| 提交下载 | `POST /api/articles/download` | `{"account_name":"公众号名","articles":[{"title":"...","link":"...","id":"..."}]}` → `task_id`；应用内部可能重试，外层不自动重发 |
| 任务状态 | `GET /api/articles/download-status/<task_id>` | `status/total/completed/failed/results`；结果含 `title/success/path/error`，可能无原 URL，保留提交顺序和原始清单 |
| 停止任务 | `POST /api/articles/download-cancel/<task_id>` | 仅用户要求或已授权范围内确需止损时调用并回读，不是健康检查 |
| 设置 | `GET /api/settings` | 只输出所需非敏感字段，避免云服务密钥进入回执 |
| 持续订阅 | `GET /api/accounts/rss-subscriptions` | `subscriptions`；只读核对本次新增项 |

不要用 `GET /api/articles/history` 作严格只读诊断：该版本会从磁盘补同步历史。确需持久下载历史时，只读解析 `data/download_history.json` 并仅输出目标文章字段。应用重启后内存任务可能不可查询，这不授权重新提交。

## 下载回执示例

先核对现有链接、活动任务和目录。v2.2.1 将标题清理并截到 60 字符，可能碰撞；已有同名目录或同批标题冲突需保全并解决，不能直接重下覆盖。无冲突记录可继续，冲突项单独保留。

将请求写为新 `download-request.json`，再执行一次提交。响应未知时保留 `submission_unknown`，从应用当前任务/持久记录回读，不删回执重来。不同批次串行执行，避免该版本以秒为单位的任务编号碰撞。

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$ErrorActionPreference = 'Stop'
$bodyPath = '<本次 download-request.json 的绝对路径>'
$receipt = '<本次尚不存在的 submission.json 的绝对路径>'
$body = [IO.File]::ReadAllText($bodyPath, [Text.Encoding]::UTF8)
$bodyHash = (Get-FileHash -LiteralPath $bodyPath -Algorithm SHA256).Hash
$entry = [ordered]@{ state='submission_unknown'; request_file=$bodyPath; request_sha256=$bodyHash; at=(Get-Date -Format o) }
$stream = [IO.File]::Open($receipt, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
try { $writer.Write(($entry | ConvertTo-Json)); $writer.Flush() } finally { $writer.Dispose() }
# 本地检查和回执落盘全部成功后，仅发起以下一次请求。
$result = Invoke-RestMethod -Uri 'http://127.0.0.1:5200/api/articles/download' -Method Post -MaximumRedirection 0 -TimeoutSec 30 -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
if (-not $result.task_id) { throw '提交结果未知，保留回执并回读应用，不重发。' }
$entry.state = 'submitted'
$entry.task_id = $result.task_id
$entry | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
```

二维码可在原应用显示；通过接口取得时先识别数据图片，只呈现解码后的图片文件。生成二维码会改变登录过程，不能用于只读检查。

## 覆盖与成果证据

- 每页独立回执，记录位置、真实返回数、稳定 ID/URL、首末日期和账号池前后状态。辅助脚本拒绝覆盖回执，也不将健康空页自动判为完整。
- 根据最新清单和链接去重；URL 中 `__biz/mid/idx/sn` 等文章标识可用于归一，不把标题当唯一 ID。
- 列表范围、正文下载和媒体完整性是不同维度。已知文章全成功但历史列表受限也不能称“所有文章”。
- `verify` 输出文件哈希、HTML/TXT 数量、图片引用缺失、远程图片和越界引用，不联网补图或改源文件。原工具保留的 `标题_raw.html`，仅在元数据标题和对应阅读版都吻合时列为原始证据，仍计算哈希，但不纳入离线阅读版图片判定；报告单列这类文件。它仅检查阅读版的 `img src/data-src`；含 `srcset` 时明确标待查。视频、CSS 背景、外链附件及正文质量需要按需抽查。核验报告存到核验目录之外。
- 对成功、文字补取、失败、未覆盖分别计数。错误解释区分上游明确错误、工具推断和浏览器观察，不把一次个案概括成固定行为。
