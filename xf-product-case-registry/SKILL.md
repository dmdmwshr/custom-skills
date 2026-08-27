---
name: xf-product-case-registry
description: 通过用户已登录的消防监督管理网页采集消防产品案卷，维护本地案卷水位，安全整理 PDF/ZIP、51 个 V2 文书槽位，并完成消防产品案卷信息登记系统四步导入和飞牛落盘核验。用户提到案卷下载、法律文书查询、打包、工作目录、案卷水位、V2 案卷包、固定槽位或生产导入时使用；既有“产品案卷数据.xlsx”的截图/OCR 填报仍改用 xf-product-case-filler。
---

# 消防产品案卷采集、整理与 V2 导入

只适配当前消防产品监督来源网页和消防产品案卷信息登记系统 V2。飞牛是正文长期存储库；Meifu 只在飞牛断线或浏览下载需要时临时接替，不能称为备份。

## 三层边界

- **业务系统**：来源网页只负责查询和下载；登记系统只使用现有 V2 接口。不得因执行本 Skill 改网站代码、数据库、接口或部署。
- **Skill**：只保存程序、规则、测试、Schema 和空值示例。不得提交真实案卷、截图、清单、水位、上传状态、账号、Cookie 或令牌。
- **工作目录**：保存真实业务数据和运行状态。工作根可切换；解析顺序为本次 `--work-root`、`%LOCALAPPDATA%\xf-product-case-registry\workspace.toml`、缺失时报错。活动批次不得跨工作根续跑，也不得静默迁移旧目录。

浏览器来源系统必须由用户手工登录。检测到未登录或会话过期时，立即暂停并提示用户登录；不得读取、导出或保存密码、Cookie、令牌或浏览器配置。登记系统的 API 认证仍只从本机认证配置读取，二者不可混用。

## 先读契约

- 浏览器查询、详情采集、下载和断点：`references/browser-acquisition.md`
- 工作根、三种本地格式和 V2 映射：`references/case-data-format.md`
- 槽位分类：`references/document-classification.md`
- V2 Schema 与示例：`references/CaseImportManifestV2.schema.json`、`references/CaseImportManifestV2.example.json`
- 认证、四步导入、缺失文件补录、核验和归档：`references/api-workflow.md`

不要使用 `CaseImportManifestV1`、V1 接口、来源审计字段、审核项或 `ReviewIssue`，也不要添加第五步“版本同步”。浏览器来源证据只保留在工作根，不进入 V2 manifest。

## 默认流程

1. 运行 `workspace doctor` 解析并核验工作根。首次使用或切换目录时运行 `workspace configure`；配置只保存工作根和下载目录，不保存认证信息。
2. 提示用户在受控浏览器中手工登录来源系统。先读取 `browser-acquisition.md` 的“执行模块与固定顺序”，按可见文字、角色和限定容器操作，不依赖坐标；浏览器窗口最大化并使用屏幕实际可用视口，不得强制创建高于屏幕而导致底部裁切的网页可视区。默认在日期控件左侧只点击一次“本年”，筛选上海时间当年 1 月 1 日至 12 月 31 日、全部管辖单位、全部 8 个大队和“消防产品监督检查记录”；随后关闭并重开日期控件，回读实际两端值和一月/十二月，只看到快捷项高亮或输入框文字不算生效。同名控件必须先缩小到业务容器；来源网页回传较慢时，单次动作只做一个控件和一次批量最小状态核对，状态满足就立即继续。普通页面动作最多等待 60 秒；60 秒时仍有顶部细进度条或加载遮罩才延长同一次等待至总计 120 秒，等待期间不得重复点击。
3. 先读取页面实时总数、每页条数和总页数，并用 `source tail-cursor` 以“最后一页最后一条”为首个游标。远距离翻页优先使用结果区右下角页码输入框；等待当前页码、预期行数、顶部细进度条和加载遮罩均稳定后，再连续两次回读相同首末 RWID。来源总数是文书记录水位，必须完整保留；RWID 只用于详情导航，详情项目编号才是案卷唯一身份。同一 RWID 或案卷可出现初查、复查记录，不能把文书字段差异误判成案卷冲突。页码、行号和“row-1”都只是原扫描时的位置证据，不是后续点击身份；每次列表重载先回读实时总数，再以稳定清单中的“案卷名称＋文书名称/编号＋创建时间”在主结果表重新定位目标，必要时搜索相邻页。完整年度批次仍须连续两轮全部文书记录一致，最多重扫三轮；单案演示仅使用 `--acceptance-sample`，不得冒充全局水位。
4. 列表中只能点击“关联项目／案卷名称”进入详情；法律文书名称是文书或打印入口，不能当作案卷入口。点击前可见三元组必须与 `sourceAppearances` 完全一致；进入详情后回读地址中的 RWID，并核对 `清单 RWID = 详情 URL RWID`、`详情项目编号 = 记录项目编号 = 本案目录名`、`清单案卷名称 = 详情单位名称`（只允许已知主体类型后缀规范化）。任一不一致记录 `CASE_IDENTITY_CHAIN_MISMATCH`，只暂停当前案卷，禁止截图、ZIP、工作区或上传数据改绑。每个不同 RWID 都必须先打开详情读取项目编号，不能凭列表指纹跳过。按尾页开始的处理顺序，同一案卷名称第 1 条检查记录标“初查”、第 2 条标“复查”、第 3 条及以后标非阻塞的“检查记录次数异常”；异常继续进入详情、下载、提取和上传，只有项目编号或关键身份字段冲突才暂停该案。详情页同时出现项目编号和文书目录后保存完整截图；正式 `source add-page` 必须带列表截图，`source add-detail` 必须带去除 `runId` 后且仍含当前 RWID 的详情来源地址。项目编号已在 `CaseWaterlineV1` 中完成且上传和飞牛均为 `VERIFIED` 时才跳过下载；否则按项目编号合并重复 RWID，并为每案建立独立下载基线，再执行“打包 → 全选 → 核对叶子文书 → 开始打包”。点击后使用 `source await-download --download-baseline <基线> --attach` 等待来源系统异步生成；`.crdownload`、变化中的文件或多候选均不得绑定。若内置浏览器已完成来源响应却将 Blob 下载标记为 0 字节取消，按 `browser-acquisition.md` 的“内置浏览器 Blob 本地恢复”使用 `scripts/browser_blob_receiver.mjs` 原子接收后再走同一校验；若 `Network.getResponseBody` 因原生消息帧上限无法交付大包，记录 `IAB_BLOB_DELIVERY_UNAVAILABLE` 并保持“详情已采集、案卷包待接收”，不得改用页面脚本分块、读取会话或重复打包。若外部 Edge 已确认来源接口在完整响应前固定取消大包，允许对**尚未点击过的下一案卷**按 `browser-acquisition.md` 的“外部 Edge 响应流接收”在点击前启用精确响应拦截，以 `Fetch.takeResponseBodyAsStream` 和顺序 `IO.read` 直接接收到配置下载目录；不得检查或保存请求头、Cookie、令牌、浏览器配置，不得注入页面脚本，也不得把该恢复方式追加到已达两次上限的案卷。流必须与当前 RWID、项目编号、本轮基线、唯一下载接口、HTTP 200、ZIP MIME 和精确 `Content-Length` 同时闭合，写入中断或长度不符时清除半成品并记录 `EDGE_CDP_STREAM_UNAVAILABLE`。只有相对本案基线唯一新增、完成且大小稳定的 ZIP 才会按项目编号和 SHA-256 规范命名、移入工作根，且仅在哈希一致后删除下载目录中的该临时副本。
5. 用 `source begin/add-page/add-detail/snapshot-downloads/await-download/attach-package/finalize` 持久化 `BrowserCaptureV1`、`SourceEvidenceV1` 和 `CaseWaterlineV1`。JSON 是机器事实源；进度询问先运行只读 `ledger status`，按项目编号区分文书记录、正式详情截图、已接收 ZIP、已创建任务、文件已传、finalize 成功和飞牛 VERIFIED，不能用目录总数、截图数或 `upload-state.json` 存在数互相代替。`ledger export` 只把 JSON 投影为 `案卷水位记录表.xlsx`。
6. 对已接收完整 ZIP 的独立案卷运行 `inventory → ocr/split → compose → validate → upload --dry-run`。浏览器仍一次只操作一个来源案卷，但不同项目编号的本地整理、信息提取和已授权上传核验可与后续浏览器下载并行流水；同一项目编号只能由一个执行单元处理。执行单元只能读取 `原始案卷/待处理案卷/<同一项目编号>` 和 `工作区/<同一项目编号>`，开始前核对 `source-evidence.projectNo`、ZIP 文件名/完整哈希、`case-data.json` 和 manifest 项目编号一致；不得把兄弟项目目录或批次 `staging` 当作业务输入。字段发生差异时按 `document-classification.md` 的裁决顺序合并：案卷、检查和产品字段以详情页结构化采集值为准，完整详情截图只作回查证据；同一业务文书的手写或扫描版本与正文完整的电子版本不一致时，以电子版本字段为准。该优先级只决定 `case-data.json` 的字段值，不改写、丢弃或跨案卷改绑原始文书；原始版本仍按槽位完整保留。每个实际采用过优先级的案卷必须在项目工作区保存 `field-resolution.json`，逐字段记录候选值、所选来源和裁决原因；仍无权威来源或项目编号身份链不一致时继续人工处理。只把已分类的规范 PDF 放入 51 个槽位或 `OTHER_ATTACHMENT`，证据不足时不猜测。
7. 当前请求已经明确授权具体案卷和生产写入时，新案卷单案运行 `upload --finalize`；两个及以上新案卷固定运行 `upload-batch --project <项目编号> ... --finalize`。已有唯一案卷不得重跑完整导入：只有服务器 `CaseImportStateV1` 确认本地清单中的槽位版本缺失、同路径存在未解决历史槽位冲突且原始 V6 状态为 `FINALIZED_WITH_CONFLICTS/created=true` 时，才按 `supplement --plan` 或 `supplement-batch --plan` 生成只读实时计划；计划稳定后用 `supplement --finalize` 或 `supplement-batch --finalize` 走 `SUPPLEMENT_EXISTING/MISSING_ONLY`。补录只能使用服务器快照返回的 `ownerKey` 和 `baseSnapshotDigest`，不得覆盖已有不同哈希文件。批量命令严格按显式项目清单在一个认证会话中依次续传、终结和核验，不能为每案重新登录；单案失败只记录该案并继续后续项目，最后从 JSON 重建 Excel 水位表。
8. 只有 V6 上传状态和飞牛落盘证据均达到 `VERIFIED` 时才归档原始证据、工作区和核验摘要。完整导入还必须无冲突、跳过项或 `created=false`；补录的 `created=false` 是既有案卷的正常语义，但必须同时满足无冲突、无替换、计数闭合、完整清单核验通过且对应历史冲突已解决。飞牛离线或未落盘保持“已上传待飞牛核验”，单案异常不阻塞其他案卷。

真实浏览器发布验收使用 `source begin --acceptance-sample`。筛选 JSON 必须同时写入实时总数、样本数和 `SINGLE_CASE_DOWNLOAD_PROOF`；该批次标为 `SAMPLE_ONLY`。列表截图按批次保存在 `原始案卷/案卷目录截图`，详情和 ZIP 只进入对应采集批次内的 `验收样本` 目录；这些证据均不进入正式待处理案卷或全局水位，也不能触发整理、上传和归档。

## 本地安全门禁

- 工作目录使用 `references/case-data-format.md` 规定的 `原始案卷/案卷目录截图`、`待处理案卷`、`已处理案卷`、`工作区/采集批次`、项目工作区、`核验记录` 和 `历史工作区`。每个原始 ZIP 只在工作根保留一份，不覆盖、不改内容；确认工作根副本哈希一致后删除下载目录中的同一临时副本。规范化结果只写项目工作区。
- 批次 `staging` 只保存尚未由 `source add-page/add-detail` 接收的临时 JSON、PNG 和异常摘要；固定命名为 `详情_<RWID>.json/.png`、`下载交付异常_<RWID>.json`。正式证据按哈希复制到项目目录并写入 SourceEvidence 后，后续整理不得继续读取 `staging`；可在批次完成且逐项哈希对账后清理临时副本。
- 案卷身份链固定为 `稳定清单可见三元组 → RWID → 详情项目编号/单位名称 → 项目目录 → 下载基线 → ZIP 哈希 → 项目工作区/manifest`。页码、行号、浏览器建议文件名和 staging 文件名均不得单独决定归属。
- ZIP 解压必须拒绝路径穿越、加密条目、重复路径、大小写或 Unicode 重名、异常压缩比、压缩炸弹和超出资源上限的包；不得递归自动解压内层 ZIP。异常只标记当前案卷“需人工处理”。
- 下载基线必须逐案刷新并绑定当前批次、RWID 和项目编号；成功绑定 ZIP 后写入不可覆盖的消费回执。基线不能跨案卷或跨批次复用，相同案卷和相同 ZIP 只允许幂等恢复。
- `attach-package` 只接受工作配置中浏览器下载目录本身，或该目录的直接子文件；其他目录、嵌套路径、同名外部文件均拒绝。
- 优先读取 PDF 电子文本层；扫描页只在本机使用 MinerU。外部 Zerox 必须另获第三方传输授权，默认禁用。
- 文件名只能辅助定位。依据正文、页码、文号、日期和明确关联决定槽位。每个上传文件必须恰好被一个槽位版本或其他附件引用；一个来源对应多个槽位时生成独立规范 PDF 和独立 `fileRef`。
- 浏览器截图、来源 HTML、RWID、来源路径、OCR 原文和人工笔记不得进入 manifest 或上传状态。来源路径去除 `runId` 及其他会话参数。
- 旧 V4/V5 状态不得自动续传或冒充新水位。当前遗留案卷首次只登记为“历史案卷待重新清点”，不移动、不删除原件；重新清点后才进入 V6 上传流程。
- 项目工作区根级控制文件使用固定名称：`inventory.json`、`ocr-result.json`、`split-plan.json`、`split-index.json`、`case-data.json`、`manifest.json`、`upload-map.json` 和 `upload-state.json`。正式缺失文件补录另固定使用 `supplement-manifest.json`、`supplement-upload-map.json` 和 `supplement-state.json`，只在实时 `--plan` 通过且即将创建补录任务时生成；不得手工编辑或把它们当作第二套完整案卷事实源。允许保留 CLI/OCR 生成的结构化诊断文件及其专用输出子目录。不得为一次异常在兄弟目录散落临时包装脚本、第二份完整 manifest 或手工改名状态。需要保留旧失败状态时，只能在系统项目明确允许重建后按 `upload-state.failed-before-recreate-<UTC>.json` 留一份只读证据。

## 认证与写入

- 登记系统认证只使用 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml`；先运行 `init-auth-config` 创建空模板，由用户本人填写。不得在聊天、命令行、日志、manifest 或状态文件中展示凭据、Cookie 或 CSRF 令牌。
- CLI 登录后必须回读会话并核对身份、认证方式、CSRF、首次改密状态和大队范围。全 8 大队批量上传或补录必须使用 ADMIN；BRIGADE 账户只能处理与 manifest `brigadeCode` 一致的本大队案卷。多案正式写入只用 `upload-batch` 或 `supplement-batch` 共用一次会话；不得用循环逐案调用单案命令造成重复登录。普通查询和控制请求的起始间隔固定不少于 1.05 秒，PDF 正文上传使用服务器独立额度。HTTP 429 只在收到数值型 `Retry-After` 且单次及累计等待都不超过 60 秒时自动加入短随机抖动后有限重试；更长等待、缺少有效响应头或流式 PDF 上传 429 必须保留断点并停止，流式正文只能由下一次命令重新打开后续传，不在同一文件句柄上自动重放。
- `validate`、`upload --dry-run` 和 `supplement --dry-run` 不访问网站；`supplement --plan` 只登录并读取实时同步快照，不创建任务或写案卷。完整导入正式写入必须显式使用 `upload --finalize` 或 `upload-batch --finalize`；补录必须显式使用 `supplement --finalize` 或 `supplement-batch --finalize`。完整导入创建任务提交相同的 `Idempotency-Key`/`idempotencyKey`、`packageSha256` 和详情项目编号 `projectNo`。补录创建任务提交稳定幂等键、`projectNo`、`mode=SUPPLEMENT_EXISTING` 和服务器 `baseSnapshotDigest`，不复用完整案卷的 `packageSha256`；补录清单固定为 `CaseFileSupplementManifestV1/MISSING_ONLY`。两类任务续传都以 GET 返回的 `receivedFiles` 核对相对路径、SHA-256 和大小，只上传服务端缺失引用；服务端投影回退、缺失或不一致必须停在本案断点。
- `verify` 默认低流量核对目录 SHA-256、飞牛状态和落盘时间；只有显式 `--deep-content-verify` 才取回正文。网络异常、飞牛离线或等待超时只报告未完成，不伪报哈希不一致。
- 上传故障先按证据分层：本地 `validate/dry-run` 失败属于数据或 Skill 门禁；DNS/TLS/连接中断属于网络；大 PDF 或低速链路下，API 可能在 multipart 请求体 180 秒总时限处主动返回 408，而客户端仍在发送正文、未及时读取响应。此时先由系统项目用同一时间窗核查 API/代理日志、`receivedFiles` 和临时文件：若确认 API 主动 408、该文件未登记且无临时文件，属于本次慢传输未完成，不得写成文件缺失或 Nginx 超时；停止当前请求后继续沿用同一任务，先回读服务端水位，只重新打开并补传未登记文件，不新建任务、不自动循环重试。401/403 属于认证或权限；429 按已发布限流契约判断；健康与就绪正常、前 1 至 3 步成功而 finalize 稳定 5xx 或服务端任务 FAILED，优先判为登记系统问题。系统问题只把脱敏的阶段、状态码、项目编号和复现范围反馈给对应登记系统项目任务，不发送凭据、任务 ID、PDF/ZIP 或完整响应；本会话继续安全的本地整理，暂停新的 finalize，且不得直接修改系统仓库或绕过失败任务。失败导入任务只能使用登记系统提供的受限清理/废弃/安全重建流程，且必须确认没有正式 Case、没有 finalize；不得要求通用案卷删除接口、手工删库或删除已有正式案卷。

## 参考资料

- `references/browser-acquisition.md`：来源网页语义操作、人工登录、稳定清单、详情证据和下载交接。
- `references/case-data-format.md`：工作根、目录、BrowserCaptureV1、SourceEvidenceV1、CaseWaterlineV1 及 manifest 映射。
- `references/api-workflow.md`：V2 认证、四步导入、缺失文件补录、续传、飞牛核验和 VERIFIED 归档。
- `references/document-classification.md`：51 个槽位、版本选择和多槽位复制。
- `references/admin-upload-config.example.toml`：仅含空值的登记系统认证示例。
