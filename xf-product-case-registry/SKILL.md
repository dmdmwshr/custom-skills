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
- 认证、四步导入、核验和归档：`references/api-workflow.md`

不要使用 `CaseImportManifestV1`、V1 接口、来源审计字段、审核项或 `ReviewIssue`，也不要添加第五步“版本同步”。浏览器来源证据只保留在工作根，不进入 V2 manifest。

## 默认流程

1. 运行 `workspace doctor` 解析并核验工作根。首次使用或切换目录时运行 `workspace configure`；配置只保存工作根和下载目录，不保存认证信息。
2. 提示用户在受控浏览器中手工登录来源系统。先读取 `browser-acquisition.md` 的“执行模块与固定顺序”，按可见文字、角色和限定容器操作，不依赖坐标；当前右侧浏览器在批次内固定为 1600×1400。默认筛选上海时间本年、全部管辖单位、全部 8 个大队和“消防产品监督检查记录”。日期输入后必须关闭并重开日期控件，回读组件实际值和两端月份；只看到输入框文字不算生效。同名控件必须先缩小到业务容器；来源网页回传较慢时，单次动作只做一个控件和一个最小状态核对，不能用全页快照或串行逐项查询代替。
3. 先读取页面实时总数、每页条数和总页数，并用 `source tail-cursor` 以“最后一页最后一条”为首个游标。远距离翻页优先使用结果区右下角页码输入框；等待当前页码、预期行数、顶部细进度条和加载遮罩均稳定后，再连续两次回读相同首末 RWID。来源总数是文书记录水位，必须完整保留；RWID 只用于详情导航，详情项目编号才是案卷唯一身份。同一 RWID 或案卷可出现初查、复查记录，不能把文书字段差异误判成案卷冲突。完整年度批次仍须连续两轮全部文书记录一致，最多重扫三轮；单案演示仅使用 `--acceptance-sample`，不得冒充全局水位。
4. 列表中只能点击“关联项目／案卷名称”进入详情；法律文书名称是文书或打印入口，不能当作案卷入口。每个不同 RWID 都必须先打开详情读取项目编号，不能凭列表指纹跳过。按尾页开始的处理顺序，同一案卷名称第 1 条检查记录标“初查”、第 2 条标“复查”、第 3 条及以后标非阻塞的“检查记录次数异常”；异常继续进入详情、下载、提取和上传，只有项目编号或关键身份字段冲突才暂停该案。详情页同时出现项目编号和文书目录后保存完整截图；正式 `source add-page` 必须带列表截图，`source add-detail` 必须带去除 `runId` 后的详情来源地址。项目编号已在 `CaseWaterlineV1` 中完成且上传和飞牛均为 `VERIFIED` 时才跳过下载；否则按项目编号合并重复 RWID，并为每案建立独立下载基线，再执行“打包 → 全选 → 核对叶子文书 → 开始打包”。点击后使用 `source await-download --download-baseline <基线> --attach` 等待来源系统异步生成；`.crdownload`、变化中的文件或多候选均不得绑定。若内置浏览器已完成来源响应却将 Blob 下载标记为 0 字节取消，按 `browser-acquisition.md` 的“Blob 本地恢复”使用 `scripts/browser_blob_receiver.mjs` 原子接收后再走同一校验；若 `Network.getResponseBody` 因原生消息帧上限无法交付大包，记录 `IAB_BLOB_DELIVERY_UNAVAILABLE` 并保持“详情已采集、案卷包待接收”，不得改用页面脚本分块、读取会话或重复打包。只有相对本案基线唯一新增、完成且大小稳定的 ZIP 才会按项目编号和 SHA-256 规范命名、移入工作根，且仅在哈希一致后删除下载目录中的该临时副本。
5. 用 `source begin/add-page/add-detail/snapshot-downloads/await-download/attach-package/finalize` 持久化 `BrowserCaptureV1`、`SourceEvidenceV1` 和 `CaseWaterlineV1`。JSON 是机器事实源，`ledger export` 只把它投影为 `案卷水位记录表.xlsx`。
6. 对已接收完整 ZIP 的独立案卷运行 `inventory → ocr/split → compose → validate → upload --dry-run`。浏览器仍一次只操作一个来源案卷，但不同项目编号的本地整理、信息提取和已授权上传核验可与后续浏览器下载并行流水；同一项目编号只能由一个执行单元处理。只把可确认字段写入 `case-data.json`，只把已分类的规范 PDF 放入 51 个槽位或 `OTHER_ATTACHMENT`；证据不足时保留待确认，不猜测。
7. 当前请求已经明确授权具体案卷和生产写入时，运行 `upload --finalize`，随后 `verify`；否则停在本地清单并一次性说明对象、影响和验证方式。
8. 只有 V6 上传状态和飞牛落盘证据均达到 `VERIFIED`，且无冲突、跳过项或 `created=false` 时才归档原始证据、工作区和核验摘要。飞牛离线或未落盘保持“已上传待飞牛核验”，单案异常不阻塞其他案卷。

真实浏览器发布验收使用 `source begin --acceptance-sample`。筛选 JSON 必须同时写入实时总数、样本数和 `SINGLE_CASE_DOWNLOAD_PROOF`；该批次标为 `SAMPLE_ONLY`。列表截图按批次保存在 `原始案卷/案卷目录截图`，详情和 ZIP 只进入对应采集批次内的 `验收样本` 目录；这些证据均不进入正式待处理案卷或全局水位，也不能触发整理、上传和归档。

## 本地安全门禁

- 工作目录使用 `references/case-data-format.md` 规定的 `原始案卷/案卷目录截图`、`待处理案卷`、`已处理案卷`、`工作区/采集批次`、项目工作区、`核验记录` 和 `历史工作区`。每个原始 ZIP 只在工作根保留一份，不覆盖、不改内容；确认工作根副本哈希一致后删除下载目录中的同一临时副本。规范化结果只写项目工作区。
- ZIP 解压必须拒绝路径穿越、加密条目、重复路径、大小写或 Unicode 重名、异常压缩比、压缩炸弹和超出资源上限的包；不得递归自动解压内层 ZIP。异常只标记当前案卷“需人工处理”。
- 下载基线必须逐案刷新并绑定当前批次、RWID 和项目编号；成功绑定 ZIP 后写入不可覆盖的消费回执。基线不能跨案卷或跨批次复用，相同案卷和相同 ZIP 只允许幂等恢复。
- `attach-package` 只接受工作配置中浏览器下载目录本身，或该目录的直接子文件；其他目录、嵌套路径、同名外部文件均拒绝。
- 优先读取 PDF 电子文本层；扫描页只在本机使用 MinerU。外部 Zerox 必须另获第三方传输授权，默认禁用。
- 文件名只能辅助定位。依据正文、页码、文号、日期和明确关联决定槽位。每个上传文件必须恰好被一个槽位版本或其他附件引用；一个来源对应多个槽位时生成独立规范 PDF 和独立 `fileRef`。
- 浏览器截图、来源 HTML、RWID、来源路径、OCR 原文和人工笔记不得进入 manifest 或上传状态。来源路径去除 `runId` 及其他会话参数。
- 旧 V4/V5 状态不得自动续传或冒充新水位。当前遗留案卷首次只登记为“历史案卷待重新清点”，不移动、不删除原件；重新清点后才进入 V6 上传流程。

## 认证与写入

- 登记系统认证只使用 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml`；先运行 `init-auth-config` 创建空模板，由用户本人填写。不得在聊天、命令行、日志、manifest 或状态文件中展示凭据、Cookie 或 CSRF 令牌。
- CLI 登录后必须回读会话并核对身份、认证方式、CSRF、首次改密状态和大队范围。全 8 大队批量上传必须使用 ADMIN；BRIGADE 账户只能上传与 manifest `brigadeCode` 一致的本大队案卷。
- `validate` 与 `upload --dry-run` 不写网站；正式写入必须显式使用 `upload --finalize`。续传只允许 V6 状态与服务端 CREATED、UPLOADING 或 MANIFEST_RECEIVED 任务完全对账。
- `verify` 默认低流量核对目录 SHA-256、飞牛状态和落盘时间；只有显式 `--deep-content-verify` 才取回正文。网络异常、飞牛离线或等待超时只报告未完成，不伪报哈希不一致。

## 参考资料

- `references/browser-acquisition.md`：来源网页语义操作、人工登录、稳定清单、详情证据和下载交接。
- `references/case-data-format.md`：工作根、目录、BrowserCaptureV1、SourceEvidenceV1、CaseWaterlineV1 及 manifest 映射。
- `references/api-workflow.md`：V2 认证、四步导入、V6 续传、飞牛核验和 VERIFIED 归档。
- `references/document-classification.md`：51 个槽位、版本选择和多槽位复制。
- `references/admin-upload-config.example.toml`：仅含空值的登记系统认证示例。
