# 手动下载交件与来源缺件口径

用户手动下载的案卷与自动采集共用唯一项目总账和后续整理、本地核验、归档流程。不能要求手动件重新自动下载，也不能倒填下载前基线、RWID、页面筛选或浏览器采集记录。

## 逐案接件

1. 限定用户指定目录，先列 ZIP、截图、大小、SHA-256 与图片原始尺寸；不按文件名或下载先后猜对应。
2. 查看原图，确认项目编号、单位、大队、初查/复查产品结果和文书目录可读；像素尺寸只是辅助，不能代替实际逐图辨认。保留完整、未经压缩的原始截图。模糊、裁切丢失关键字段或配对有歧义的单案暂停，其他已核清案可继续。
3. ZIP 内选一份可靠电子身份文书，按单位、文号及截图目录核对，保存条目名、条目摘要和正文对应值。单位名有差异时必须有同案文号、地点等明确证据并记录裁决；无法确认同案时不接收。
4. 形成 `ManualCaseIntakeV1` 核查单，以 `source accept-manual --review-json <核查单> --work-root <唯一业务根>` 接收。核查单包含 `projectNo`、`fields.projectNo/unitName/brigadeCode`、ZIP/PNG 的绝对输入路径与完整摘要；`screenshotReview` 记录 `readable=true`、项目/单位/大队、`dimensions` 和已逐图读清的 `readableFields`（projectNo、unitName、brigade、productResults、documentDirectory）；`documentMatch` 记录包内 PDF 的 `entryName/sha256/unitName/documentNo`，名称差异另存 `identityDifferenceResolution`。
5. 新案原件进入既有待处理目录，采用项目号和内容摘要命名，生成 `manual-intake.json`；保留下载目录的手动输入。接收模式明确为 MANUAL_PROVIDED，不伪造 SourceEvidenceV1 的 RWID 记录。后续 inventory、compose、`local-verify` 沿用统一入口，全部在本地完成，不上传服务器。
6. 已归档或已有不同来源包的项目只追加到 `原始案卷/手动交件/<项目>/<接件标识>`，状态 PENDING_COMPARISON；不替换原始包、不重置旧任务和归档。随后执行 `source resolve-manual --project <项目> [--intake-id <接件标识>] --work-root <业务根>`：按包内逐文件摘要与总账摘要仍一致的既有原始 ZIP（历史 V2 与本地完成案均适用）比对。相同或不含新增/变化文件的材料只标记 RESOLVED_IDENTICAL / RESOLVED_NO_NEW_MATERIAL，不重复处理；有新增或变化文件时进入 NEW_GENERATION_STARTED：新包和截图复制进待处理目录，总账回到 PENDING_ORGANIZATION，旧代际原件、旧工作区、旧归档、历史完成记录、人工字段和失败/未知状态全部保留（`source.previousGenerations` 记录被替代的包）。之后按常规 inventory → compose → `local-verify` 完成并归档为新代际，再运行一次 `resolve-manual` 得到 RESOLVED_COMPLETED。找不到可对账的既有 ZIP（BASELINE_MISSING）、原案仍在处理（BLOCKED_ACTIVE_CASE）或待处理目录被占用（BLOCKED_PENDING_OCCUPIED）时保持 PENDING_COMPARISON 并说明原因。整个入口可重复运行、可中断续接，不手改水位。`manualIntakes` 是唯一总账中的证据引用，不是第二份业务水位。

## 来源缺件与处理完成分别表达

下载包中缺少、错放、空白或对应不清的材料属于来源大队/监督系统材料问题，保留 `source.materialGaps` 和证据；不自动认定本地整理漏件，不为此反复下载、补造或整案重传。

默认缺件仍阻止归档。当前用户明确要求按现有有效材料收口时，可执行 `source acknowledge-gaps --project <项目> --reason <当前用户口径> --work-root <业务根>`。该动作只绑定当前缺口清单的摘要，记录来源责任和用户口径，不清除缺件、不修改业务字段、不跳过本地核验。缺口列表变化后原确认自动失效。

现有有效文书全部通过本地核验（摘要、页数、拆分链）、无冲突且原件保全后，可以归档现有材料；核验回执同时保留缺口清单和确认口径。总账仍显示 `materialsCollected=false`、`complete=false`，以 `availableMaterialWorkflowComplete=true` 和来源问题保留说明表示本地现有材料处理已收口。来源变化、文件身份变化及未完成核验仍阻止归档。没有用户明确口径时不得调用该入口。

## 本地扩展入口

以下入口都只改本地总账引用和待处理/工作区目录，不上传、不联网、不手改水位；每个入口都可重复运行并以原回执对账。

### 已归档案卷的同 ZIP 修订

`source resolve-manual` 对字节相同的 ZIP 只会标记已收口，不能修订字段或分类。需要修订时：

1. `source amend-archived --project <项目> --reason <原因> --work-root <业务根>`。要求总账为可信的 LOCAL_V1 完成（深度复核通过）且当前归档是最新代际；待处理目录和活动工作区必须不存在。入口把父代归档原件**复制**到新的待处理目录，在 `工作区/<项目>/amendment-seed/` 放入父代 manifest、case-data、split-plan、field-resolution 的只读种子，并固定父代代际、清单/包摘要、原始目录/历史工作区的整树摘要和回执摘要。总账 `amendment.status=OPEN`，旧 `completion.status` 变为 `SUPERSEDED_BY_AMENDMENT`（旧完成保留在 `amendment.parent.completion` 与 `history.completions`），`local.status=AMENDING`。
2. 同参数重复运行返回 `REUSED`；不同原因在修订未结前被拒绝。
3. 按常规 `inventory <待处理 ZIP> → 编辑 case-data/split-plan → split → compose`；工作区必须有 `field-resolution.json` 并写入 `amendmentId`、`unresolvedFields` 为空，表示已重新核对字段裁决、源页和原始证据。不合格产品的详细问题描述、跨行姓名和位置等字段必须有真实来源，缺失保持省略或 UNKNOWN，不用占位通过 validate。
4. `local-verify`：重新核对清单、每份 PDF 摘要/页数、拆分链、原始 ZIP 摘要，并要求父代整树摘要与固定时一致、最新完成代际仍是该父代、包摘要与父代相同、清单相对父代有实质差异（含检查日期/阶段等检查自身字段、案件字段、产品、文件、槽位和附件；凭证 `amendment.changes` 列出 added/removed/changed；仅生成时间不同视为无差异而拒绝）。成功后新代际以“时间-清单摘要前 12 位”命名归档，`history.completions` 追加一条（含 `amendment.parentGeneration`），`amendment.status=COMPLETED`。任何失败都回滚，父代全文件摘要不变。
5. `source abandon-amendment --project <项目> --reason <原因>`：父代未变化时，把待处理副本移入 `原始案卷/已放弃修订/<项目>/<修订标识>`、工作区移入 `工作区/历史工作区/<项目>-amendment-abandoned-<修订标识>`（均不删除），恢复父代完成记录；重复运行返回 `REUSED`。

### 已归档案卷的复查产品对应复核（ProductLinkReviewV1）

复查产品只经 `initialProductRef` 对应初查产品；名称、位置、数量不能代替。LOCAL_V1 案卷仍走 `source amend-archived`；旧式归档、不能或不必产生新代际的已完成案卷，用 `source link-products --project <项目> --links-file <绝对路径> --work-root <业务根>`：

- 清单 JSON：`reviewer` 与 `links[]`，每条含 `recheckRef`、`initialRef`（必须是本案归档清单里的复查/初查产品）、`basis`、`statement`（至少 8 字，写明所依文书与具体事实）、`evidence[]`（`file` 为归档工作区 `source/` 内的相对文件名，`pages` 可选）。
- `basis=IDENTICAL_ATTRIBUTES`：名称、型号、标称生产者逐项相同，且本案文书点名该产品；`EXPLICIT_STATEMENT`：本案复查文书明确写明复查/整改/更换/仍未整改的就是该初查产品。仅位置或名称相近不构成依据。
- 只对 `COMPLETED` 案卷生效；写入账本 `productLinks`，绑定归档代际与归档清单的规范摘要，不改归档清单、原件、回执和凭证。清单已有 `initialProductRef` 的产品不覆盖；已登记到另一初查产品的不静默改写。
- 生命周期视图读取时才把已核对的对应叠加到清单副本上；归档代际或清单变化后复核自动失效，需要重新登记。

### 外加补采文件（不属于 ZIP 成员）

来源系统列出但 ZIP 缺少、随后从自身可见预览取得的文书（回执 `SourceDocumentRecoveryV1`）用 `source attach-supplement --project <项目> --file <绝对路径> --receipt <回执绝对路径> --work-root <业务根>` 绑定：

- 回执 `projectNo`、`rwid`（须属于本案已记录的来源标识）、`sha256`、页数/字节数、`detailScreenshot.sha256`（须是本案已保存的原始详情截图）全部核对；文件内容若已是原 ZIP 成员、文件名含路径字符、回执缺失或摘要被改均拒绝；
- 文件和回执按摘要原字节复制到 `待处理案卷/<项目>/补采材料/`，总账 `source.supplements[<摘要前 12 位>]` 保存摘要、来源类型 `VISIBLE_SOURCE_PREVIEW`、来源文书标识和回执摘要；同一文书绑定不同摘要或同一文件绑定不同回执一律拒绝；
- inventory 仍只清点 ZIP，原始包摘要不变。split 用 `sourceRelativePath: "补采材料/<文件名>"` 引用补采 PDF，拆分记录标 `sourceKinds=["EXTERNAL_SUPPLEMENT"]`；补采文件不能直接进入 compose，必须先 split 成规范 PDF，页到槽位的映射随拆分链保留；
- split、compose、local-verify 每次重新核对补采文件与回执摘要，归档后补采材料随原件目录一起保存，深度复核逐份核对其摘要和回执。已完成案卷须先开启修订才能补采；未确认的来源缺件仍阻止归档。

### 分类证据文件丢失时的新绑定

总账 `source.classification.evidenceSha256` 指向的证据文件已丢失（或摘要不符）时，既有归档会在重定位阶段拒绝。若本案存在已逐图核清的真实手动交件（同一 ZIP 摘要、截图摘要仍匹配），可用 `source rebind-evidence --project <项目> --intake-id <接件标识> --reason <原因> --work-root <业务根>`：旧路径与旧摘要原样保留，状态记为 MISSING/DIGEST_MISMATCH，新增 `classification.evidenceReplacement`（指向手动交件回执及其摘要）。入口不重建旧 JSON、不改旧摘要、不改分类结果；原证据完整时拒绝。新绑定前还核对：回执 `schemaVersion=ManualCaseIntakeReceiptV1` 且 `fields.projectNo`、`projectNo` 均为本案；截图必须能完整解码为 PNG（含像素数据和尺寸，纯文本伪图、截断或 IDAT 损坏一律拒绝），且尺寸与回执记录一致。之后归档按替代证据重定位，旧摘要缺失作为事实保留。

### 图片转 PDF 与水上大队来源的核验边界

- `normalize-images` 及 local-verify 对 PNG 校验：CRC、IDAT 能被 zlib 完整解压且无多余数据、扫描行数与长度等于“高×(行宽+1)”、滤波字节合法、尺寸/色彩类型/位深一致；pHYs 横纵密度不等、JPEG(JFIF) 横纵密度不等、CMYK/YCCK JPEG、隔行 PNG、tRNS 透明表均拒绝（不支持，不做重绘或猜测）。JPEG 在生成（normalize-images）与校验（compose/local-verify 重新核对）两侧共用同一检查：逐扫描完整读取霍夫曼熵编码（不做 IDCT），要求霍夫曼表/量化表已定义、MCU 数与帧尺寸及采样因子一致、重启标记顺序与段数一致、位数不足（截断）或多余（≥2 字节）、无效编码、系数越界均拒绝，且每个分量都被扫描覆盖。只支持 8 位顺序（baseline/extended sequential）霍夫曼 JPEG；渐进式、无损、算术、分层 JPEG 因本地无法完整验证而明确拒绝。该检查为纯 Python 实现，不依赖 Pillow 或系统解码器，因此各平台行为一致；它证明熵数据可完整解码，不替代渲染目视核对。
- 本地水上大队（SHUISHANG/“水上大队”）要求来源管辖证据：分类证据须带本案 `projectNo`，若有 `schemaVersion` 必须是 SourceEvidenceV1 或年度身份采集的 SourceIdentityV1；SourceEvidenceV1 的记录须绑定本案已记录的来源标识，SourceIdentityV1 的 `rwid` 须是本案已记录的来源标识、`fields.消防管辖` 为依据；记录和字段中的项目编号若出现必须等于本案；手动交件回执须是 ManualCaseIntakeReceiptV1 且 `fields.projectNo` 为本案。外项目、无身份、版本错误一律不作依据。云端八大队口径不变。
