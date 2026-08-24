# V2 四步导入、核验与归档

飞牛是案卷正文的唯一长期存储库。Meifu 只在飞牛断线时临时接替写入或在需要浏览/下载时中转；飞牛恢复后由系统修复临时接替任务，不把 Meifu 描述为备份。

本流程只适用于现有登记系统 V2，所有业务请求位于 `/api/v2`。来源网页的人工登录和浏览器采集是独立上游，不向 V2 接口传递来源 Cookie、RWID、截图、URL 或 HTML。

## 前置条件

- 已用 `workspace doctor` 解析工作根；本次 manifest、规范 PDF 和上传状态只在该工作根的 `工作区/<项目编号>`，原始 ZIP 和截图只在 `原始案卷/待处理案卷/<项目编号>`。
- 已依据 `CaseImportManifestV2.schema.json` 校验 manifest。每个 `files` 条目都是有 SHA-256 的规范 PDF，并恰好关联一个正式槽位版本或 `OTHER_ATTACHMENT`。
- 浏览器证据、OCR 原文、未确认字段、水位和人工笔记留在工作根，不随请求上传。
- 用户已直接授权当前案卷写入目标网站。全 8 大队批量上传使用 ADMIN；BRIGADE 账户只能处理与 manifest `brigadeCode` 完全一致的大队。
- `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml` 已由用户本人填写；它与浏览器手工登录完全独立，不在 Skill 或工作根中保存副本。

## 登记系统认证

1. CLI 从本机认证配置读取用户名和密码，通过同源 `POST /api/auth/login` 登录；失败只报告状态，不回显响应正文或凭据。
2. 使用同一客户端 Cookie 容器立即请求 `GET /api/auth/session`。登录响应与会话响应的身份、`authMethod=SESSION`、内外层 CSRF 必须一致，`mustChangePassword` 必须为 `false`。
3. ADMIN 不得绑定大队；BRIGADE 的平铺和嵌套大队 ID/编号必须一致，且与 manifest 大队编号相同。任一条件不满足立即停止。
4. Cookie、密码和 CSRF 只留在当前进程内存。业务写请求逐次携带同源 `Origin`、`X-Product-Case-Client: web-v2` 和当前 CSRF；GET 只使用会话 Cookie。
5. 两个及以上案卷使用 `upload-batch`：先离线预检全部显式项目编号，再登录和读取会话各一次、就绪检查一次，在同一客户端 Cookie 容器中依次处理。跨大队批量必须是 ADMIN；同一大队的 BRIGADE 批量仍逐案核对 `brigadeCode`。不得用外层循环逐案启动 `upload`。

## 固定四步

1. `POST /api/v2/import-jobs`：请求头 `Idempotency-Key` 与正文 `idempotencyKey` 使用同一稳定值，正文同时提交 `packageSha256` 和详情项目编号 `projectNo`，按三者创建、取得幂等任务或安全重建符合门禁的 FAILED 任务。
2. `POST /api/v2/import-jobs/{id}/files`：逐个流式上传已关联的规范 PDF。
3. `PUT /api/v2/import-jobs/{id}/manifest`：提交 `CaseImportManifestV2`。
4. `POST /api/v2/import-jobs/{id}/finalize`：由服务端执行 Schema 与语义校验并事务写入。

不得访问 `/api/v1`，不得提交 V1 清单、来源证据、审核项、未关联文件，也不得增加独立“版本同步”步骤。

## 本地门禁与续传

- `validate` 和 `upload --dry-run` 只做本地 Schema、归属、PDF、页数和哈希校验，不读取认证配置，不建立网络连接。
- 正式写入必须显式 `upload --finalize`；写入前核对服务就绪、项目编号和当前授权范围。
- `upload-state.json` 使用封闭 V6，保存文件投影、不可变清单绑定、任务进度、目标、大队编号和不可逆身份摘要，不保存用户/大队 ID、密码、Cookie、CSRF 或完整服务端响应。
- 只有服务端任务为 CREATED、UPLOADING 或 MANIFEST_RECEIVED，且包哈希、`projectNo`、文件投影、清单绑定、大队、目标和身份摘要完全一致时，才能续传。服务端 GET 的正式水位字段为 `receivedFiles`，逐项核对上传相对路径、SHA-256 和大小；manifest 已提交后还可用其中的 `clientRef` 辅助对账，服务端若返回 MIME 再核对 MIME。服务端精确多出的已接收文件可以推进本地 `uploadedFileRefs`，本地声称已传但服务端缺失、服务端出现额外路径或任一属性不一致时立即停止。兼容旧服务端时可读取 `files`/`uploadedFiles`，但生产契约以 `receivedFiles` 为准；完全未返回投影时，只信任本地在成功上传响应后原子写入的引用。
- CREATED/UPLOADING 只上传 `uploadedFileRefs` 之外的文件，全部引用齐全后才提交 manifest。MANIFEST_RECEIVED 表示服务端已经接受完整文件图和清单：若服务端返回文件投影则必须完整等于本地投影；旧服务端没有投影时以该状态及项目编号对账为依据，直接 finalize，不重复上传 PDF 或 manifest。
- 服务端 FAILED/404、字段不足或对账失败时停止，不新建替代任务绕过错误。只有登记系统提供 ADMIN 受限的“废弃/清理失败导入任务”能力，并证明任务未生成正式 Case、未 finalize、包哈希和授权范围匹配后，才可保留旧状态证据并安全重建；不得调用通用案卷删除、手工删库或删除已存在的正式案卷。
- 旧 V4/V5 状态不得自动续传，也不能直接转换为 V6；将案卷登记为“历史案卷待重新清点”，保留原件，重新 inventory、compose 和 validate。
- 第 1 至 3 步出现冲突、哈希不一致、引用不存在、槽位错误或 PDF 不合规时停止。第 4 步失败时保留本地状态，修正后仅在同一任务仍可完整对账时重试。

## 上传故障分类与反馈

先保留原断点，再按失败发生的位置分类；不能用笼统的“上传失败”替代诊断：

- 本地 `validate` 或 `upload --dry-run` 失败：属于案卷数据、文件映射或 Skill 门禁；不访问网络，修正本地输入或 Skill 规则后重新验证。
- DNS、TLS、连接建立失败、连接中断或超时：属于网络传输问题；只报告传输阶段和可重试性，不写成服务端业务错误。
- 401、403、首次改密或大队范围不匹配：属于认证或权限问题；保持断点并请用户处理权限，不降级或绕过。
- 429：属于登记系统认证限流或客户端未复用会话。读取数值型 `Retry-After`，明确报告等待秒数并停止本轮登录；多案改用一次登录的 `upload-batch`，不得并发登录、长时间自动睡眠或无界重试。
- 大 PDF 上传仍在发送或等待响应时返回 408/代理超时，且本地文件、清单和哈希均已验证：先按登记系统链路问题反馈，请系统项目核查反向代理请求体/读取超时、API multipart 流式接收、临时文件清理和同一任务服务日志。保留成功文件引用；同一任务恢复时只补缺失文件，不整案重传。
- `/api/health`、`/api/ready` 正常，认证和第 1 至 3 步成功，但第 4 步 finalize 对多个独立任务稳定返回 5xx 或服务端任务进入 FAILED：优先归类为登记系统服务端问题。暂停新的 finalize，但可继续其他案卷的 inventory、OCR/拆分、compose、validate 和 dry-run。
- finalize 成功而飞牛不是 AVAILABLE、缺少 `nasVerifiedAt` 或目录哈希未就绪：属于远端存储核验未完成；不得归档，也不得称为网络或 finalize 失败。

登记系统问题应反馈给对应系统项目任务，由该任务检查服务日志、数据库任务元数据、代码、测试和部署。本 Skill 执行会话不得跨边界修改系统仓库、接口或部署。反馈内容仅限脱敏的项目编号、失败步骤、HTTP 状态、发生时间范围、前置成功里程碑和是否可重复；不得发送用户名、密码、Cookie、CSRF、任务 ID、PDF/ZIP、完整响应或浏览器配置。系统修复前不得清除 V6 状态、另建任务或重试真实 FAILED 任务；修复后先由系统任务说明旧任务的安全处置方式，再以一个验证通过的小案卷作正式 canary，成功完成 verify 后才恢复批量 finalize。

## 飞牛核验

- finalize 成功后保存服务端 `finalizedAt` 和白名单摘要，并进入“已上传待飞牛核验”。有限轮询目录 SHA-256、`remoteState=AVAILABLE` 和 `nasVerifiedAt`；约 60 秒未满足时保持 `FINALIZED_UNVERIFIED`，稍后可单独运行 `verify`。
- finalize 摘要包含冲突、跳过项或 `created=false` 时进入 `FINALIZED_WITH_CONFLICTS`，标记“需人工处理”；不得自动核验、重新终结或称为完成。
- `verify` 默认低流量核对目录 SHA-256、飞牛状态和落盘时间，不下载全部正文。证据缺失或仍处理中时保持“已上传待飞牛核验”。
- 只有显式 `--deep-content-verify` 才允许正文核验。遇结构化 `RECALL_REQUIRED` 后可用当前登记系统会话发起或复用 recall，轮询到 READY 再下载；PENDING/PROCESSING 继续等待，OFFLINE/FAILED/超时停止。网络异常不能写成哈希不一致。
- 只有文件计数、目录 SHA-256、飞牛 AVAILABLE 和 `nasVerifiedAt` 全部满足，且无冲突、跳过或 `created=false`，才能把 V6 上传状态标记为 `VERIFIED`。本地水位先进入“已核验、归档待处理”，归档成功后才是“已完成”。

## VERIFIED 后自动归档

自动归档必须是 VERIFIED 状态提交的一部分，并保持单案幂等：

1. 把 `原始案卷/待处理案卷/<项目编号>` 移入 `原始案卷/已处理案卷/<项目编号>/<时间>-<包哈希>`；每次完成形成不可覆盖代际，同案后来发生变化可生成新代际。
2. 把活动项目工作区移入 `工作区/历史工作区/<项目编号>-<时间>-<包哈希>`；保留 manifest、V6 状态和整理证据。
3. 在 `工作区/核验记录/<项目编号>-<时间>-<清单哈希>.json` 写入不可覆盖的最终摘要。
4. 原子更新 `CaseWaterlineV1`，再由 JSON 重建 `案卷水位记录表.xlsx`。Excel 被占用时保留 JSON 成功状态并报告可稍后导出。

飞牛离线、核验等待、网络失败、冲突、跳过、`created=false`、旧状态或任何人工处理项均不得触发归档。归档失败不撤销服务端 VERIFIED，但水位必须明确记录“已核验、归档待处理”，再次运行只重试未完成的本地归档步骤。
