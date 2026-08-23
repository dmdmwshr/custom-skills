# 工作目录、本地水位与 V2 映射

`CaseImportManifestV2.schema.json` 是上传字段的唯一契约。本文件规定真实业务数据如何在可切换工作根中落盘，以及来源证据如何与 V2 manifest 隔离。

## 工作根解析与目录

工作根按以下顺序解析：

1. 本次命令显式 `--work-root`；
2. `%LOCALAPPDATA%\xf-product-case-registry\workspace.toml` 的工作根；
3. 均缺失时报错并提示运行 `workspace configure`。

配置文件只保存工作根和浏览器下载目录，不保存任何账号、密码、Cookie 或令牌。Skill 不内置任何业务目录；首次使用时把用户当前指定的目录写入本机配置。目录以后可以切换，但只影响新批次。活动批次记录创建时解析出的绝对工作根，跨根续跑必须拒绝，旧目录不得静默迁移、合并或覆盖。

工作根使用以下结构：

- `原始案卷/案卷目录截图/<批次>`：筛选条件、列表页截图及人工核查证据；
- `原始案卷/待处理案卷/<项目编号>`：来源详情截图、唯一的原始 ZIP 和原始案卷文件；下载目录只作临时接收，绑定并复核成功后不保留同一 ZIP 副本；
- `原始案卷/已处理案卷/<项目编号>/<时间>-<包哈希>`：仅 VERIFIED 后归档的不可覆盖原始证据代际；
- `工作区/采集批次/<批次>`：浏览器分页清单、稳定性轮次和断点；
- `工作区/采集批次/<批次>/验收样本/<项目编号>`：真实单案下载验收的隔离截图、来源证据和 ZIP；不属于正式待处理案卷；
- `工作区/<项目编号>`：解压、清点、OCR、拆分、`normalized/`、`case-data.json`、manifest 和 V6 上传状态；
- `工作区/核验记录/<项目编号>-<时间>-<清单哈希>.json`：不可覆盖的最终核验摘要；
- `工作区/历史工作区/<项目编号>-<时间>-<包哈希>`：VERIFIED 后的项目工作产物；
- `案卷水位记录.json`：`CaseWaterlineV1` 唯一机器事实源；
- `案卷水位记录表.xlsx`：从 JSON 生成的人工查看投影。

真实案卷、截图、OCR、清单、水位和状态只能进入工作根，不得写入 Skill 源仓库、安装副本或消防产品案卷信息登记系统代码仓库。登记系统认证仍固定在 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml`，不进入工作根。

## BrowserCaptureV1

每个采集批次保存一个封闭的 `BrowserCaptureV1`，至少表达：

- 格式版本、批次 ID、绝对工作根和上海时区创建/更新时间；
- 年度起止、管辖单位、可选执法大队和精确文书类型；
- 页面实时文书总数、每页条数、报告页数、各扫描轮次、去重 RWID 数及两者差额；
- 每条文书记录的 RWID、案卷名称、文书名称、创建时间、来源页行、处理顺序和 `INITIAL/RECHECK/ANOMALY` 标记；同一 RWID 的多条文书保存在 `sourceAppearances`，不得因去重丢失页面总数；
- 同一 RWID 下案卷名称省略或附带已知主体类型后缀时，保留每条原始名称并按规范名分组，不覆盖原文，也不单独形成阻塞冲突；
- 当前断点、重扫次数、稳定/清单变化中/失败状态及错误摘要。
- 同一案卷名称超过两条检查记录时写入 `anomalies`，其中 `blocking=false`；该告警不进入 `conflicts`，不阻止详情、下载、整理或上传。
- 每案点击下载前的 ZIP/临时文件基线指纹；后续案卷必须重新快照，不能复用批次初始基线。
- `scope=all` 表示正式完整清单；`scope=acceptance` 必须同时标记 `listContract=SAMPLE_ONLY`、`updatesGlobalWaterline=false`，只用于单案下载链路验收。

连续两轮完整文书记录集合一致才能标记稳定，最多三轮。`sourceDocumentCount` 必须等于页面报告总数，`uniqueRwidCount` 只表示需要进入详情的导航键数量，两者不得混用。页面数量是运行时事实，不用历史截图中的 514 等数字填充。

## SourceEvidenceV1

每个来源记录保存一个封闭的 `SourceEvidenceV1`，至少表达：

- 格式版本、RWID、项目编号、去除 `runId` 和会话参数的来源路径；
- 首次发现、最后发现和详情采集时间；
- 单位、大队、地址、日期、检查情况、承办人、产品和文书目录等可确认字段；
- 可重叠业务标签及其证据来源；
- 详情截图的工作根相对路径与 SHA-256；
- 原始建议文件名、规范 ZIP 相对路径、包 SHA-256、大小、逐案下载基线指纹、候选属性和采集状态。

RWID 只用于来源去重和追溯，不进入 V2 manifest。Cookie、令牌、完整 URL、原始 HTML、浏览器配置和登录凭据禁止进入该格式。

## CaseWaterlineV1 与 Excel

`CaseWaterlineV1` 按详情页读取的项目编号维护一条全局水位，至少包含单位、大队、来源 RWID、初查/复查/次数异常标签、包哈希、来源进度、本地整理进度、上传进度、飞牛核验、完成时间和错误摘要。列表阶段不得按文书指纹直接跳过案卷；每个不同 RWID 先进入详情，项目编号相同才合并为一个案卷。只有水位已完成且上传、飞牛均为 `VERIFIED` 时才跳过该项目的重复下载。状态主线为：

`已发现 → 详情已采集 → 案卷包就绪 → 待整理 → 待上传 → 上传中 → 已上传待飞牛核验 → 已完成`

异常状态为“需人工处理”或“采集失败”，只暂停对应案卷。状态更新应先原子写 JSON；`ledger export` 再生成 `案卷水位记录表.xlsx`。Excel 不是事实源，用户占用文件导致导出失败时不得回滚已成功的 JSON，关闭 Excel 后可重新生成。

Excel 至少展示项目编号、单位、大队、标签、来源进度、本地整理、上传、飞牛核验、完成时间和错误摘要，并能回读验证行数、项目编号和关键状态与 JSON 一致。

旧 V4/V5 `upload-state.json`、仍指向代码仓库的旧 inventory 或历史目录不能导入为当前进度。已有遗留案卷首次仅登记“历史案卷待重新清点”，不移动、不删除；完成新 inventory 和 V6 状态后才能进入正常水位。

## 本地证据到 manifest

- `packageSha256` 来自原始 ZIP 或目录清点；同一包重试时保持不变。
- `case`、`initialInspection`、可选 `recheckInspection` 和产品字段只使用详情页、PDF 正文或本机 OCR 中相互一致的可确认事实。
- `files` 只列将上传的规范 PDF，包含稳定 `clientRef`、上传相对路径、SHA-256、PDF MIME 类型和页数。
- `documentSlots` 使用现有 51 个固定槽位；`otherAttachments` 只接收明确不属于固定槽位的独立 PDF。
- 供 `compose` 使用的 `case-data.json` 允许 `files[]` 额外使用 `sourceRelativePath` 指向 inventory 或 split 的真实 PDF；`relativePath` 是上传路径。`compose` 重新校验 PDF、页数与哈希，并删除本地辅助字段后生成正式 manifest。
- 浏览器截图、筛选截图、来源路径、RWID、来源 HTML、OCR 原文、人工笔记、水位和错误记录均不得进入 manifest 或上传状态。

没有可靠证据时省略可选字段；只有 Schema 明确允许未知值的字段才填 `UNKNOWN`。运行时若示例与服务端校验不同，以当前生产 Schema 和服务端校验为准：检查级槽位只填 `inspectionRef`，产品级槽位只填 `productRef`，通报槽位只填 `notificationTarget`，案卷级槽位不填归属字段。

## 文件、槽位与版本

- 初查阶段固定为 `INITIAL_CHECK`，复查固定为 `RECHECK`；现场判定、抽样送检或复检是业务方式，不是检查阶段。
- 产品 `clientRef` 在清单中稳定。`repairSiteId` 只有证据明确时填写；非空 UUID 必须同时 `maintenance: "YES"`。
- 产品为 `UNQUALIFIED` 时按 Schema 提供检查方式和非空问题描述；只有抽样送检才能填写复检申请或结果。
- 普通槽位的 `ELECTRONIC` 和 `SCANNED` 各最多一个；现场照片只能有一个 `SCANNED`。截图、原 ZIP 和组合件不能直接作为正式版本。
- 每个 `files` 条目必须恰好被一个槽位版本或 `OTHER_ATTACHMENT` 引用。一个来源对应多个槽位时生成独立规范 PDF、独立 `fileRef` 和独立上传路径。
- 拆分目标只能位于项目工作区的 `normalized/`，不得覆盖原件或已有文件；空白页不生成规范 PDF。
