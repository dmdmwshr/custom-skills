# BLS 浏览器备用采集

仅当 OKXnew 的普通 HTTP BLS 日历路径失败、陈旧，或到达每日刷新窗口时读取本文件。

## 固定入口

- 官方发布页：`https://www.bls.gov/schedule/news_release/empsit.htm`
- 官方年度页：`https://www.bls.gov/schedule/<当前年>/home.htm`
- 规范 ICS：`https://www.bls.gov/schedule/news_release/bls.ics`

发布页和年度页用于确认浏览器网络路径及页面语义。当前规范导入只接受 ICS；HTML 独立解析器完成前，不得把浏览器翻译后的 DOM 文本写入规范日历。

## 获取步骤

1. 调用浏览器前先完整读取并遵循当前可用的 `control-in-app-browser` skill。没有该能力时返回 `browser_unavailable`。
2. 在选定浏览器中打开官方发布页，确认最终域名仍为 `www.bls.gov`，页面标题和发布表格可见。
3. 读取该标签页的 `cdp` 能力，启用网络观测。不要直接导航到 `.ics`；Edge 可能把日历文件报告为 `ERR_BLOCKED_BY_CLIENT`。
4. 通过当前 BLS 页面上下文的 `Runtime.evaluate` 执行同源 `fetch`，参数固定为：
   - URL 必须逐字等于规范 ICS 地址；
   - `method: "GET"`；
   - `credentials: "same-origin"`；
   - `cache: "no-store"`。
5. 只在响应同时满足以下条件时继续：状态 200、最终 URL 未重定向、媒体类型为 `text/calendar`、正文以 `BEGIN:VCALENDAR` 开始、至少一个 `BEGIN:VEVENT`。
6. 在浏览器控制会话中对 UTF-8 正文计算 SHA-256，并构造以下封套；不要把正文打印到聊天或命令行：

```json
{
  "capture_method": "browser_runtime_fetch_v1",
  "requested_url": "https://www.bls.gov/schedule/news_release/bls.ics",
  "final_url": "https://www.bls.gov/schedule/news_release/bls.ics",
  "response_status": 200,
  "content_type": "text/calendar",
  "captured_at": "带时区的 ISO 8601 时间",
  "raw_content_sha256": "64 位小写十六进制",
  "body": "原始 ICS 正文"
}
```

7. 使用浏览器控制会话中的 `node:child_process.spawn` 直接启动以下固定解释器和模块，`windowsHide=true`、`shell=false`，并把封套作为 UTF-8 标准输入传入：
   - 解释器：`C:\Users\12070\Desktop\项目开发\OKXnew\.venv\Scripts\python.exe`
   - 参数：`-B -m okxnew.data.browser_capture`
   - 工作目录：`C:\Users\12070\Desktop\项目开发\OKXnew`
8. 只读取导入器返回的脱敏回执：看到数、插入数、当前数、采集时间和正文哈希。退出码非零时标记 `import_rejected`，不要打印或保存正文，也不要重复绕过校验。
9. 从本地 API 回读 `/api/v1/data/macro/calendar` 和 `/api/v1/data/monitoring`。确认 BLS 日历为健康、事件数非零、哈希等于本次正文、连续失败归零。
10. 结束浏览器工作时按 Browser skill 要求清理研究和错误标签页，不保留 BLS 页面作为交付物。

## 安全停止条件

- 页面出现 CAPTCHA、登录、下载权限或安全拦截时停止；不要绕过。
- 最终域名、媒体类型、状态、哈希或正文结构不一致时停止。
- 项目虚拟环境或导入模块不存在时停止并报告工程缺口。
- 本地 API 回读与导入回执不一致时停止，不声称成功。
