# 官方宏观浏览器备用采集

仅当 BLS 日历到达每日刷新窗口、失败/陈旧或进入跨年更新窗口，或者其他已登记官方日历/实际值路径失败、陈旧时读取本文件。先逐产品回读状态；除 BLS 年度日历的固定刷新外，健康且 24 小时内成功的产品不得重复补采。

## 固定入口与导入路由

| 来源 | 固定官方入口 | `route_id` | 导入行为 |
|---|---|---|---|
| BLS 就业页 | `https://www.bls.gov/schedule/news_release/empsit.htm` | `bls_employment_html` | 只与当前规范事件核对 |
| BLS 年度页 | `https://www.bls.gov/schedule/<年份>/home.htm` | `bls_annual_html` | 锚定稳定身份后晋升 BLS 日历；只允许未来覆盖尾部新增 |
| BEA | `https://apps.bea.gov/API/signup/release_dates.json` | `bea_release_dates_json` | 晋升 BEA 日历 |
| Census | `https://www.census.gov/economic-indicators/calendar-listview.html` | `census_schedule_html` | 晋升 Census 日历 |
| Federal Reserve | `https://www.federalreserve.gov/newsevents/<年>-<英文小写月份>.htm` | `fed_monthly_html` | 晋升联储日历 |
| DOL | `https://oui.doleta.gov/unemploy/claims_arch.asp` | `dol_claims_html` | 晋升 DOL 日历 |
| Federal Reserve 目标利率 | FOMC 日历中已发布且符合 `https://www.federalreserve.gov/newsevents/pressreleases/monetary<YYYYMMDD>a.htm` 的官方声明 | `fed_policy_statement_html` | 晋升联邦基金目标区间实际值 |
| DOL 周度申领实际值 | `https://oui.doleta.gov/unemploy/wkclaims/report.asp` | `dol_weekly_claims_xml` | 晋升全国初请、续请、参保失业率等实际值 |

BLS 年度页按当前规范日历覆盖尾部选择年份；进入跨年窗口且 BLS 已发布下一年度页面时，同时处理下一年度。不得回退到 BLS 日历 HTTP 直连、同源 ICS 或第三方日历。BEA 官方 JSON 是主路径而非降级来源；它公开、无需密钥并比 HTML 更稳定。Federal Reserve 月历只补当前月及常驻任务报告为失败/陈旧的月份；实际值声明 URL 必须由 FOMC 官方日历发现，不枚举猜测。DOL 实际值固定使用 `POST`，表单只允许全国范围、上一年到当前年和 XML 输出。所有最终 URL 必须与请求 URL 完全相同。

BEA 与 Census 的结构化实际值不在浏览器回退路由中：普通发布页可读不代表字段完整、版本稳定或可替代官方 API。它们由项目已配置的官方 API 短任务维护；若该链不可用，只回报对应产品状态，不读取凭据，也不得抓网页标题或摘要填充实际值。

## 获取步骤

1. 调用浏览器前完整读取并遵循当前可用的 `control-in-app-browser` skill。没有该能力时返回 `browser_unavailable`。
2. 只打开上表对应的官方页面。确认最终 URL 未重定向、页面标题或正文标记与来源一致；不要读取 Cookie、浏览器存储、账户或其他标签页。
3. 不把浏览器翻译、阅读模式或扩展改写后的 DOM 当作原文。BLS 年度页读取固定官方页面的原始 DOM；其他路由先读取该标签页的 `cdp` 能力说明，再从同源页面上下文取得固定 URL 的原始网络响应。日历、声明和 JSON 使用 `GET`，DOL 实际值使用固定 `POST`；使用 `credentials: "omit"` 和 `cache: "no-store"`。
4. DOL 实际值 `POST` 正文固定为 `level=us&strtdate=<上一年>&enddate=<当前年>&filetype=xml&submit=Submit`，媒体类型为 `application/x-www-form-urlencoded`；不得扩大到州级枚举、任意年份或其他输出格式。
5. 只在响应状态为 200、最终 URL 未变化、媒体类型与路由一致、正文包含固定页面标记时继续。BLS 年度页必须包含目标年份和至少一个可解析发布事件；DOL 实际值必须为全国 XML 且至少包含一个已发布的 `weekEnded` 与初请季调值。
6. 在浏览器控制会话内对 UTF-8 原文计算 SHA-256。正文不得打印到聊天、终端参数、日志或临时文件。
7. 日历 HTML/JSON 路由构造以下封套。实际值路由还必须增加 `request_method`，联储为 `GET`、DOL 为 `POST`：

```json
{
  "route_id": "上表固定路由",
  "capture_method": "browser_runtime_fetch_v1",
  "request_method": "仅实际值路由需要，GET 或 POST",
  "requested_url": "固定官方 URL",
  "final_url": "与请求完全相同的 URL",
  "response_status": 200,
  "content_type": "原始媒体类型",
  "captured_at": "带时区的 ISO 8601 时间",
  "raw_content_sha256": "64 位小写十六进制",
  "body": "原始正文"
}
```

8. 使用浏览器控制会话中的受控子进程能力启动固定解释器和模块，`windowsHide=true`、`shell=false`，并把封套作为 UTF-8 标准输入传入：
   - 解释器：`C:\Users\12070\Desktop\项目开发\OKXnew\.venv\Scripts\python.exe`
   - 参数：`-B -m okxnew.data.browser_capture`
   - 工作目录：`C:\Users\12070\Desktop\项目开发\OKXnew`
9. 只读取脱敏回执：路由、来源、事件或观测数、插入数、当前数、对齐数、未对齐数、采集时间和正文哈希。退出码非零时标记 `import_rejected`，不得打印正文或换域名绕过。
10. BLS 就业页回执必须为 `corroboration_only=true`。BLS 年度页回执必须为 `corroboration_only=false`；既有事件须唯一锚定稳定身份并以只追加版本写入，明确晚于覆盖尾部的新事件才可创建新身份。历史未匹配、一对多或歧义标记 `identity_anchor_rejected`，不得猜测或创建重复事件。
11. 从本地 API 回读 `/api/v1/data/macro/status`、`/api/v1/data/macro/calendar`、`/api/v1/data/macro/observations` 和 `/api/v1/data/monitoring`。确认对应来源状态、事件数、观测数、哈希、最近成功和失败计数与回执一致。
12. 按 Browser skill 清理研究、错误和中间标签页，不保留页面作为交付物。

## 安全停止条件

- 页面出现 CAPTCHA、登录、下载权限或安全拦截时停止；不要绕过。
- 最终 URL、媒体类型、状态、哈希、页面标记或解析事件不一致时停止。
- 项目虚拟环境、导入模块或本地 API 不存在时停止并报告工程缺口。
- 回读状态与导入回执不一致时停止，不声称成功。
- CME 和 OPEC 不在路由白名单；不得借此流程采集其日历或正文。
- BEA 与 Census 实际值没有当前浏览器导入路由；不得临时用通用抓取替代官方 API。
