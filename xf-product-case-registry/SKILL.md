---
name: xf-product-case-registry
description: 采集真实初查日期不早于 2026-01-01 的消防产品监督案卷（先不合格后其他；范围按初查日期而非项目编号年份），维护项目编号唯一总账；来源只读可见页面，多入口候选经详情确认，手动交件、清点、识别、拆分、文书属性与本地引用、字段校验、本地核验与归档全部在本地完成，不上传服务器或飞牛；飞书承载案卷/文书元数据、属性与快速检索，原件、扫描正文、详情原图和本地路径不上云。复用原任务与未变材料。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 消防产品案卷采集、本地整理与统一水位

执行平台为 Windows（源码与测试也可在 WSL 运行，平台声明以 platforms.json 为准）。先核对实际主机、原生依赖和本机配置。来源网页使用用户已登录的受控浏览器；Skill 负责来源证据、本地整理、字段校验、本地核验与归档。**案卷文件只保存在本地业务根，不上传服务器或飞牛；旧登记系统和飞牛已废除，统计数据在飞书维护。** 浏览器证据、OCR 原文和本地采集账本不外传。

## 采集范围与来源读取规则（2026-11-06 起）

范围：默认只采**消防产品监督案卷**，且只采**真实初查日期不早于 2026-01-01**（含以后年份）的案卷；项目编号年份、任务创建时间、受理日期、下载时间都不决定范围，旧号段（如 C2025）案卷的初查在 2026 年即在范围内，C2026 案卷的初查在 2025 年即在范围外。先不合格，已知缺口准确收口后再补其他产品案卷；独立行政处罚案卷不属于默认采集、登记范围，产品监督案卷内合法关联的处罚文书保留。法律文书“责令限期改正通知书”、任务检查结果、抽样单/验字文书等入口只产生**候选线索**，必须经详情页的初查/最终产品结论确认。

**范围证据（2026-11-11 实测 UI_VERIFIED）**：来源“查询统计 → 检查任务查询”下各任务页（日常监督、开业前检查、社会单位产品抽查、举报投诉核查、火灾事故调查核查；装备抽查不是产品案卷）都有“检查日期”区间筛选；该日期等于初查日（对已有电子检查记录日期的 228 案逐案相等）。取清单时：创建时间区间放宽（页面默认只有近三个月）、检查日期起始 2026-01-01、管辖范围“全部管辖单位(含派出所)”，保存各类型的页面总数和逐行内容（`InitialDateScopeListingV1`），再用 `ledger scope-reconcile` 与总账按单位和数量对账；列表不显示项目编号/RWID，范围外候选与新增来源任务都必须逐案打开详情闭合四身份并取得带日期的来源记录后才能处置。受理日期只是后备，离边界 31 天内不能单独证明范围（可能偏后近一个月）。初查日期缺失或冲突的案卷保留为待核对象，不按猜测剔除。
来源页面读取只用**可见 DOM**，不读取框架内部数据模型、store 或网络缓存，不用坐标；每个脚本只做一个页面状态动作，之后重新观察。列表不显示 RWID/项目编号，因此逐案打开后由详情 URL 与详情字段闭合 **项目号、RWID、单位、大队**：预期值必须在打开前由台账或列表登记，实测值必须齐全且逐项相等，缺项或冲突只允许只读定位，不得开面板、选叶、打包、下载或归档。正常年度入口（`collect_list/collect_identities/locate_known_case/open_case/open_identity`）按可见候选工作：唯一单位候选 → 一次打开 → 详情路由 RWID 与项目号/单位/大队收敛，RWID 在进入详情前保持 pending，歧义或缺证即阻断。活动模块已删除旧的内部状态读取器及其环境开关，设置任何环境变量都不会恢复；历史做法只作为旧补丁与基线中的只读证据。打包前须证明本案叶集合完整（详情目录与面板声明数一致、无折叠/未渲染节点、叶 ID 可读）、根归属明确（[罚]、[监]、未知根及孤叶不得继承本案）、选中集合与本案叶精确相等；不能证明时转人工，不凭常量放行。真实页面选择器、日期框、分页和“全选”按钮行为须在受控页面实测后才写成已验证。

所有案卷按项目编号共用 CaseWaterlineV1。不合格案卷只是筛选子集；来源已登记、材料已采集、本地已核验、归档已完成分别表达，不能互相代替。先读 [统一水位与增量规则](references/unified-waterline.md)。

## 已退役入口

`upload`、`upload-batch`、`verify`、`verify-existing`、`supplement`、`supplement-batch`、`ledger reconcile`、`init-auth-config` 已退役：在读取任何认证、联网、写计时或改总账之前直接返回说明并以非零退出，改用 `local-verify`。旧的上传状态、`content-verification.json`、历史 VERIFIED 回执继续只读，并在总账中标为历史证据等级，不升级也不改写。xf-product-case-filler 已并入本 Skill（字段校验见 `fields validate`）。

## 按任务读取

| 阶段 | 入口 |
| --- | --- |
| 总账、来源年度、子集、指定批次、未完成断点 | [统一水位](references/unified-waterline.md)，ledger status、source coverage、source scan-plan |
| 年度完整任务清单、身份缺口、来源不可用 | [年度基线](references/annual-baseline.md)，annual_browser.py |
| 浏览器筛选、两轮列表、详情、下载 | [浏览器采集](references/browser-acquisition.md)，[查询方法](references/query-recipes.md)；已知年度不合格案用 capture_source_case.py、package_source_case.py |
| 明确要求历史年度通知书口径 | [年度执行](references/annual-unqualified.md)；保留原批次口径，不替代日常扫描 |
| 清点、识别、拆分、字段和案卷清单 | [本地格式](references/case-data-format.md)、[文书分类](references/document-classification.md)、[字段规则](references/field-rules.md)、[清单 Schema](references/CaseImportManifestV2.schema.json) |
| 用户手动下载 ZIP、提供详情截图，或明确按来源现有材料收口 | [手动交件](references/manual-intake.md)，source accept-manual、source resolve-manual、source acknowledge-gaps |
| 本地核验与归档 | `local-verify`（见下），凭证格式见本地格式的 LocalVerificationV1 |
| 已归档案卷字段/分类修订（同一 ZIP） | [本地扩展入口](references/manual-intake.md#本地扩展入口)，source amend-archived、source abandon-amendment |
| 按真实初查日期对账范围、剔除范围外案卷、恢复 | `ledger scope-reconcile --listing`（只读）；`ledger scope-exclude --plan`（默认只校验，`--apply` 才把案卷字节原样移到 `范围外保留/<planId>/` 并出总账）；`ledger scope-restore` 原样恢复；`ledger lifecycle --from-date` 按初查日期而非号段；云端同步剔除用 `feishu-scope-retire --plan`（绑定全字段快照，先子后主，落账读回，缺省只核对不删） |
| 云端初查日期与本案核验清单日不同、需有据更正 | `feishu-date-correct-build`（计划逐项带旧值门禁，只读云）→ `feishu-source-sync --execute`；详见 [来源字段同步](references/source-sync.md) 末节 |
| 自动来源字段需按证据批量维护(如统计归类)、UNKNOWN 更新意图需严格技术恢复 | `feishu-field-plan-build`(证据摘要+旧值门禁，只读云)；`feishu-source-sync --reconcile` 的 `technicalRecovery`(无 humanRelease)；详见 [来源字段同步](references/source-sync.md) 末节 |
| 来源列有但不在 ZIP 的文书（真实可见预览补采） | source attach-supplement，随后 split 引用 `补采材料/<文件>` |
| 已归档案卷的复查产品→初查产品对应(旧式归档/不产生新代际) | [本地扩展入口](references/manual-intake.md#已归档案卷的复查产品对应复核productlinkreviewv1)，source link-products |
| 一案已有多段详情截图（含覆盖索引）要成为永久 D 引用 | [浏览器采集](references/browser-acquisition.md)，source attach-detail-segments（首段须为已登记截图，同案/顺序/重叠/末端全证明才绑定，同索引重入 0 写） |
| 已存详情要生成飞书基础字段（项目号、单位、地址、管辖→八队路由、有证据的初查日）说明 | [来源字段同步](references/source-sync.md)，feishu-source-build（只读云）→ feishu-source-sync；退出码 4 表示有被挡实体(PARTIAL)，不是整体完成 |
| 五类业务标签初值、证据确定标签、用户授权代录（含维修企业库）、写入账根迁移 | [来源字段同步](references/source-sync.md) 末节“字段归属与四种写入依据”：`feishu-label-initial-build`、`feishu-label-evidence-build`、`feishu-delegated-build`、`feishu-ledger-migrate` |
| 大队维护字段读回本地、月度统计独立对账、企业/网售清单主动登记、产品数量补空 | [来源字段同步](references/source-sync.md) 末节：`feishu-mirror`、`feishu-recount`（有差异退出 4）、`feishu-register enterprises|online-sales`、`record_quantities.py scan` → `feishu-quantity-plan` |
| 按单位名称找案卷而系统原文带空格或全角括号 | `feishu-case-find --name <名称或项目编号>`（只读云）：忽略空格和全半角比较，结果给系统原文；规范化后同名的并列列出不合并。名称只用于定位，身份仍核项目编号、RWID、单位、大队四项。见 [来源字段同步](references/source-sync.md) 末节 |
| 每夜定时或人工触发的整轮运行、断点续办、未检查任务观察队列 | [夜间运行](references/nightly-run.md)，`nightly begin/bind/checkpoint/coverage/finish/status/observe/due/executions`；每晚两类入口发现、每 7 天全范围核对、执行绑定 |
| PNG/JPEG 原图需要进入规范成果 | `normalize-images --work-dir <工作区> --relative-path <图片>` |
| Word(.docx/.doc) 原件需要进入规范成果 | 操作者先用本机 Office 引擎转 PDF 并逐页目视核对，再 `normalize-office --work-dir <工作区> --relative-path <Word 原件> --converted-pdf <工作区外的 PDF> --converter-name <名称> --converter-version <版本> --rendering-check <核对说明>` |
| 来源 PDF 无法结构解析（inventory 标记 `unparseablePdf`，如重复 /Info） | 用 PDFium 等引擎能打开且逐页目视核对后，同样走 `normalize-office`（来源格式 PDF_UNPARSEABLE）；未绑定的此类成员 compose 拒绝，仍可解析的 PDF 不得走该路径 |
| JPEG 在结束标记后另有不小于主图的第二幅图（normalize-images 拒绝） | 两幅图都目视核对后，用外部渲染的多页 PDF 走 `normalize-office`（来源格式 JPEG_APPENDED_IMAGE）；能被 normalize-images 忠实转换的 JPEG 不得走该路径 |
| 分类证据文件丢失 | source rebind-evidence（用本案真实手动交件新绑定，不改旧摘要） |
| 分类证据文件被后续采集追加改写（摘要变） | source rebind-retained-observation（凭保留的早期观察与变更轨迹新绑定，不改旧摘要） |

## 每轮执行

1. 运行 workspace doctor，回读总账及活动待办。实际读写使用其 workRoot；历史水位与回执的路径不代表当前主操作盘。首次接入先 ledger index-history 查看本地历史索引差异，当前维护授权内带 --apply 合并；不重新打开全部历史来源页。工作根由本次参数或本机配置解析，缺失时才配置，不迁移旧材料。用户要求在已有双向同步两端切换主操作入口时，按[同步资料主操作入口](references/mirror-access.md)核对和切换。
2. 用 source coverage 和 source scan-plan 判断来源覆盖。年度逐项关联以可见候选 + 详情闭合身份（RWID 进入详情后才取得，之前为 pending）（见上节与 [浏览器采集](references/browser-acquisition.md)）。覆盖未证明完整时只报已覆盖入口，不宣称全量；历史文书标识数不能代替年度案卷数。来源不可用、类别缺失和身份缺口分别保留。年度基线完成后默认扫描执行日向前回推三个自然月创建的全部相关文书，支持跨年。所有筛选先在页面提交回读再保存证据，不使用计划值冒充实测值。
3. 稳定清单先按 RWID 和历史项目身份对账。同一项目跨列表、跨批次合并；近期文书关联往年案时仍进入该案断点。指纹未变且已有有效完成证据（本地核验或历史 VERIFIED），跳过详情、打包、识别和整理。超出窗口的已完成案且无变化线索不处理；未完成案保留，优先其中的不合格案。
4. 仅新案、文书变化或未完成案进入详情。按“清单可见三元组 → RWID → 详情项目编号及单位”闭合身份；页码、行号、文件名和 staging 不能决定归属。初查任一产品有效结果不合格即入子集；抽样复检用最终结果；整改复查只决定案卷类型，统计年份用初查日期。合格案只登记最小索引，分类未知时补必要证据，不能以全文关键词或通知书存在替代判定。
5. 需要完整材料的案卷逐案下载。详情身份连续两次一致，只选择当前案卷的可下载叶子，须先通过上节的身份四项、根边界和完整叶证明，并核对选中集合与完整本案叶精确相等，建立本案基线，再点击一次打包；“全选”仅在页面实测证明其范围等于本案叶集合后使用。以 source await-download --attach 的唯一完整稳定 ZIP 回执确认收件（来源页面在原包之后新增真实文书、需要新一代包时，加 `--refresh-reason SPECIAL_ISSUE|USER_REQUEST|…`：新包作为新一代接入，旧代原件与登记保留，须有当前已验证原包）；每案最多两次打包，重试及 Edge 流恢复严格按浏览器分支。原始包不可覆盖，工作根副本哈希通过后才清理同一下载临时副本。
6. inventory → ocr/split（图片先 `normalize-images`，外加补采先 `source attach-supplement`）→ 文书属性与本地引用（见 [文书分类](references/document-classification.md)）→ 字段整理 → compose → validate。先复用原文件摘要、电子文本和确认过的 OCR；仅处理新增或变化内容。默认逐文件 OCR，ocr --batch 仍需显式选择且关键语义一致。识别路由：有文字层的 PDF 先用本地 PDF 解析；扫描件先评估 `wps-office` 的实际命令、权益和数据去向，本次对该材料有明确云处理授权时可用已验 WPS 桌面入口，其在线 OCR 产物须独立核对；WPS 不可用或能力不足（命令不覆盖、权益不够、产物为空或不可用）、材料限本地处理、无该授权或需复杂表格时，直接用本地路径（registry `ocr` 子命令即本机 MinerU 4，官方任务接口、standard、自动、全部页，沿原任务保存和恢复断点，不启动替代容器），无需先上云试失败。`ocr` 子命令只调用 MinerU，不会自动调用 WPS；WPS 产物如何接入见本地格式的识别规则。Zerox 仅限本次已明确云端授权的专用解析，不再默认首选。字段裁决留在 field-resolution.json，保留全部原件。抽取结果先用 `fields validate <extractions.json> [--image-dir]` 只读校验（稳定键＝项目编号＋初查/复查，缺值留空并列为未知，复查日期必须有自身来源，人工裁决字段受保护，不改输入、不写表、不改名或删除图片），规则见[字段规则](references/field-rules.md)。
7. `local-verify --project <项目编号> --check-only` 先在内存中核验（不写任何文件）；通过后 `local-verify --project <项目编号>` 重新核对清单、每份 PDF 摘要与页数、拆分链、字段裁决、原始 ZIP/材料摘要和来源映射，写 LocalVerificationV1 并归档；`--no-archive` 只写凭证。已完成案每次都深度复核归档 PDF、原始 ZIP、清单、凭证、回执和 changePending：全部有效才返回 ALREADY_COMPLETED（不产生新代际、不改任何字节）；任一损坏、缺失或变化返回 NEEDS_REVIEW（退出码 2），保留全部证据并在总账视图显示 LOCAL_COMPLETION_UNTRUSTED，来源跳过也不再认定完成。来源材料缺口默认阻止归档，用户明确按现有材料收口时按手动交件说明确认后再归档。
8. 归档后核对实际路径、凭证与回执摘要、唯一水位和另一端同步。再次执行窗口扫描，已完成且无来源变化的案卷应零重复下载、识别、整理。日常只报变化、待办和异常；全量总账通过只读查询取得。

## 身份、授权和恢复

- 同一项目只有一个写执行单元；输入限本案待处理目录和工作区，不用兄弟项目或 staging 材料。样本批次 --acceptance-sample 为 SAMPLE_ONLY，不更新正式水位、不归档。最近已结案单案验收另读 [单案验收](references/playwright-single-case.md)。
- 不读取或保存浏览器密码、Cookie、令牌和配置。来源掉线先恢复已有受控标签；当前会话已授权账号自动登录时，沿用 LocalVault 标准输入入口自行登录、识别普通图形验证码并回读，不再要求用户手工登录。只有缺少必要登录授权或遇工具强制人工接管时才请用户处理。本地流程不需要任何业务系统账号。
- 只读查询不触发 OCR、归档或更新水位。当前用户明确授权相关 Skill、系统修复和发布时，可在该范围完成实现、验证、发布及回读；不得把历史交接限制或岗位名称当作新的审批要求。
- 归档中断或目标已存在时，回滚移动、回执和总账，拒绝覆盖，原件与工作区保持原位；未知结果先回读再处理，不盲目重放。
- 已归档案卷在同一 ZIP 内的字段或分类需要修正时，只用 `source amend-archived --project <编号> --reason <原因>`：父代原件、历史工作区、清单、凭证和回执保持原字节，旧完成在总账标为 SUPERSEDED_BY_AMENDMENT，新代际重新核验后才推进唯一水位；父代变化、清单无实质差异、缺少含 amendmentId 的字段裁决均拒绝归档。未完成修订用 `source abandon-amendment` 恢复父代完成，副本移入保留目录而不删除。
- 补采文件（如 VISIBLE_SOURCE_PREVIEW）是独立来源类型，不属于 ZIP 成员：用 `source attach-supplement` 绑定文件与其 `SourceDocumentRecoveryV1` 回执，缺失、篡改、串案、来源记录标识不符、路径越界或与 ZIP 成员重复均拒绝；绑定不改变原始包摘要，也不降低缺件检查。
- 本地业务实体可以包含水上大队（`SHUISHANG`，须同时填 `brigadeName: 水上大队` 且本案真实来源证据的“消防管辖”记载一致）；它不进入飞书八队统计归属或权限，其他值仍拒绝。
- 来源明确缺件时保留 materialGaps，材料采集不能标为齐全；默认不归档。当前用户明确要求按已有有效材料收口时，按手动交件说明绑定来源问题口径，本地核验通过后归档现有材料，缺件事实与来源责任继续保留。
- 来源原地修改旧文书且没有近期文书活动，窗口扫描不能保证发现；收到明确线索时按项目定点核对。来源变化未处理完时保留 changePending，不能用本地处理时间或一次列表刷新消除此标记。
- Skill 修改只在受管源仓库完成，经测试、独立提交推送、cc-switch 同步预检与发布、安装副本及新进程发现核验。隔离测试通过、真实发布完成、真实采集验收完成分别报告，不互相冒充。
