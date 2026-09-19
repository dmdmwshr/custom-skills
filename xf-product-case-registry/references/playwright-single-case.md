# Playwright CLI 登录与单案下载验收

此入口用于用户指定的 Windows Chrome/Edge 单案下载测试，不自动迁移年度批次或已有业务固定驱动。浏览器仍使用官方 CLI 与扩展，凭据由 LocalVault 管理。连接的工作目录建议直接放在业务工作根内，使浏览器产物也留在工作根。

## 登录

按照 `playwright-browser-control/references/vault-login.md`，已有会话只验证导航；用户授权调用精确凭据时用其 `vault_login.cjs`，凭据通过 LocalVault `Invoke` 的标准输入传入。先处理验证码，再在同一次调用内填密码、提交和核验。不要在密码已填时保存快照，不能用密码框圆点判断日志安全。工具强制停止时停止，不反复切换控制通道。

## 最近已结案样本

1. `workspace doctor`、`ledger status` 固定现有工作根、下载目录及正式水位；记录水位文件哈希。只建新的 acceptance 批次。
2. 用户要求最近已办结案卷时，可进入“产品监督 → 查询统计 → 检查任务查询 → 日常监督任务”。记录查询实际日期范围，选择“已结案”和“全部管辖单位(含派出所)”，执法单位不选；点击一次搜索。结果范围仅代表该类任务及该日期筛选，不声称是全部业务类型的绝对最新案卷。
3. 在“结束日期”列点击“降序：最高到最低”，回读降序激活标记、实时总数、当前页和首行。并列日期时取排序后的首个案卷。用单位名称、结束日期、状态定位，进入详情核对 RWID、项目编号、单位名称和已结案状态。检查历史打包次数，达到上限的案卷不重开。
4. 新筛选 JSON 使用 `selectionMode=LATEST_CLOSED_TASK`、`acceptanceMode=SINGLE_CASE_DOWNLOAD_PROOF`、`sampleCount=1`、`liveTotalCount`、实际 `startDate/endDate`、`queryRoute=#/xfjd/cpjd/cxtj/jcwcx/rcjcrw`、`taskStatus=已结案`、`sortField=结束日期`、`sortDirection=descending`、`jurisdiction=全部管辖单位(含派出所)`、`brigadeScope=ALL`。不填未实际选择的本年快捷项、法律文书类型或年度总数。正式采集继续使用原年度契约。
5. `source begin --acceptance-sample` 后，样本分页的 totalCount/totalPages 均为 1，真实列表总数保留在 filters.liveTotalCount；同一可见样本两次回读一致才 `add-page/finalize`。详情身份与截图前后再核对，使用 `add-detail` 接收。

## 原生下载

1. 进入打包模式，点击“全选”。当前页面存在主表和固定列的重复 DOM：只核对可见文书叶子，按其业务值去重，排除“附件”等非文书框，逐项全部已选；记录叶子数量。不要把 DOM 总数等同于文书数量。
2. 点击前创建本案 `source snapshot-downloads` 基线。请求 JSON 只包含 `browser/session/origin/batchId/rwid/projectNo/unitName/expectedLeafCount/baselinePath/checkpointPath`，真实值只存在业务批次目录。checkpointPath 为本批次新的不可覆盖 JSON。
3. 在连接所用同一工作目录运行固定 Node 的 `scripts/playwright_cli_download.cjs <请求JSON>`。它检查精确身份、叶子数量和全选状态、基线案卷绑定与未消费状态，创建排他动作意图记录，再调用现有官方 CLI，仅点击一次“开始打包”。已有 checkpoint 拒绝重放；不能删除或换名来掩盖未知结果。
4. 3 秒内的 download 事件只是提示。`NATIVE_FILE_CHECK_REQUIRED`、`CLICK_OUTCOME_UNKNOWN` 或事件缺失都先执行同一基线的 `source await-download --attach`；不改用 saveAs、二次取包或再次点击。原生 Edge 可能先产生随机名 `.tmp` 再改为 `.zip`，不能把临时文件改名冒充完成。
5. 唯一新 ZIP 必须连续稳定、能完整打开且通过路径/资源检查，工作根复制哈希一致后才允许清理下载临时副本。样本状态须为 ACCEPTANCE_COMPLETE，正式水位哈希保持一致。不得把验收样本继续整理、上传或归档。
6. 保存轻量回执：范围、选择依据、项目编号、实际叶子数、文件数、字节数、SHA-256、仅一次点击、基线消费、正式水位未变。数字现场读取，不写死；不要把一个站点成功外推为所有站点稳定。

2026-09-19 的外部 Edge 实测确认：CLI download 事件缺失时，原生 ZIP 仍可成功落盘，由既有 await-download 正确接收。此结论不适用于尚未验收的其他版本。
