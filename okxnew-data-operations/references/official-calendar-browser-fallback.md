# 官方宏观日历浏览器备用采集

仅当 OKXnew 的普通 HTTP 官方日历路径失败、陈旧，或到达每日刷新窗口时读取本文件。先逐源回读状态；健康且 24 小时内成功的来源不得重复补采。

## 固定入口与导入路由

| 来源 | 固定官方入口 | `route_id` | 导入行为 |
|---|---|---|---|
| BLS 主日历 | `https://www.bls.gov/schedule/news_release/bls.ics` | 不带 `route_id`，沿用 BLS ICS 封套 | 晋升 BLS 主日历 |
| BLS 就业页 | `https://www.bls.gov/schedule/news_release/empsit.htm` | `bls_employment_html` | 只与 ICS 核对 |
| BLS 年度页 | `https://www.bls.gov/schedule/<当前年>/home.htm` | `bls_annual_html` | 只与 ICS 核对 |
| BEA | `https://apps.bea.gov/API/signup/release_dates.json` | `bea_release_dates_json` | 晋升 BEA 日历 |
| Census | `https://www.census.gov/economic-indicators/calendar-listview.html` | `census_schedule_html` | 晋升 Census 日历 |
| Federal Reserve | `https://www.federalreserve.gov/newsevents/<年>-<英文小写月份>.htm` | `fed_monthly_html` | 晋升联储日历 |
| DOL | `https://oui.doleta.gov/unemploy/claims_arch.asp` | `dol_claims_html` | 晋升 DOL 日历 |

BEA 官方 JSON 是主路径而非降级来源；它公开、无需密钥并比 HTML 更稳定。Federal Reserve 月历只补当前月及常驻任务报告为失败/陈旧的月份，不枚举猜测 URL。所有最终 URL 必须与请求 URL 完全相同。

## 获取步骤

1. 调用浏览器前完整读取并遵循当前可用的 `control-in-app-browser` skill。没有该能力时返回 `browser_unavailable`。
2. 只打开上表对应的官方页面。确认最终 URL 未重定向、页面标题或正文标记与来源一致；不要读取 Cookie、浏览器存储、账户或其他标签页。
3. 页面正常渲染时，从当前标签页读取原始 `document.documentElement.outerHTML`。JSON、ICS 或页面受浏览器下载处理影响时，先读取该标签页的 `cdp` 能力说明，再在同源页面上下文执行固定 URL 的 `GET`；使用 `credentials: "omit"` 或 BLS 同源 ICS 所需的 `credentials: "same-origin"`，并设置 `cache: "no-store"`。
4. 只在响应状态为 200、最终 URL 未变化、媒体类型与路由一致、正文包含固定页面标记时继续。BLS ICS 必须为 `text/calendar`、以 `BEGIN:VCALENDAR` 开始并至少包含一个 `BEGIN:VEVENT`。
5. 在浏览器控制会话内对 UTF-8 原文计算 SHA-256。正文不得打印到聊天、终端参数、日志或临时文件。
6. HTML/JSON 路由构造以下封套；BLS ICS 沿用旧封套并省略 `route_id`：

```json
{
  "route_id": "上表固定路由",
  "capture_method": "browser_runtime_fetch_v1",
  "requested_url": "固定官方 URL",
  "final_url": "与请求完全相同的 URL",
  "response_status": 200,
  "content_type": "原始媒体类型",
  "captured_at": "带时区的 ISO 8601 时间",
  "raw_content_sha256": "64 位小写十六进制",
  "body": "原始正文"
}
```

7. 使用浏览器控制会话中的受控子进程能力启动固定解释器和模块，`windowsHide=true`、`shell=false`，并把封套作为 UTF-8 标准输入传入：
   - 解释器：`C:\Users\12070\Desktop\项目开发\OKXnew\.venv\Scripts\python.exe`
   - 参数：`-B -m okxnew.data.browser_capture`
   - 工作目录：`C:\Users\12070\Desktop\项目开发\OKXnew`
8. 只读取脱敏回执：路由、来源、看到数、插入数、当前数、对齐数、未对齐数、采集时间和正文哈希。退出码非零时标记 `import_rejected`，不得打印正文或换域名绕过。
9. BLS HTML 回执必须为 `corroboration_only=true`。存在未对齐事件时标记 `corroboration_mismatch`，不把它解释为 ICS 已更新。
10. 从本地 API 回读 `/api/v1/data/macro/status`、`/api/v1/data/macro/calendar` 和 `/api/v1/data/monitoring`。确认对应来源状态、事件数、哈希、最近成功和失败计数与回执一致。
11. 按 Browser skill 清理研究、错误和中间标签页，不保留页面作为交付物。

## 安全停止条件

- 页面出现 CAPTCHA、登录、下载权限或安全拦截时停止；不要绕过。
- 最终 URL、媒体类型、状态、哈希、页面标记或解析事件不一致时停止。
- 项目虚拟环境、导入模块或本地 API 不存在时停止并报告工程缺口。
- 回读状态与导入回执不一致时停止，不声称成功。
- CME 和 OPEC 不在路由白名单；不得借此流程采集其日历或正文。
