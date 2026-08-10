# 本地证据到 CaseImportManifestV2 的映射

`CaseImportManifestV2.schema.json` 是唯一字段契约，本文件只说明本地整理原则。不要沿用历史清单结构、来源审计字段或待审核记录。

运行时若生产示例与服务端校验发生差异，以现有网站实际校验规则为准：检查级槽位只填写 `inspectionRef`，产品级槽位只填写 `productRef`，通报槽位只填写 `notificationTarget`，案卷级槽位不填写任何归属字段。

## 顶层结构

- `packageSha256`：原案卷包的 SHA-256；同一包重试时保持不变。
- `case`：项目编号、大队、被检查单位及可确认的案卷字段。
- `initialInspection`：唯一初查，包含稳定的检查引用和受检产品。
- `recheckInspection`：仅在确有复查时填写，结构与初查对应。
- `files`：每份将被上传的规范 PDF；必须包含稳定 `clientRef`、相对路径、SHA-256、PDF MIME 类型和页数。
- `documentSlots`：51 个固定槽位的已确认正式版本。
- `otherAttachments`：不属于固定槽位的独立 PDF 附件。

供本地 `compose` 使用的 `case-data.json` 与最终 manifest 只有一处差别：`files[]` 额外使用 `sourceRelativePath` 指向 `inventory.json` 或 `split-index.json` 中的真实 PDF；`relativePath` 是提交给网站的上传路径。`compose` 会重新读取 PDF、校验页数与哈希，再删除本地辅助字段并生成正式 manifest。`case-data.json` 不得自定义包哈希；若填写，必须与 `inventory.json` 完全一致。

只有 Schema 允许的值才可填写。没有可靠电子文本或本机 MinerU 证据时，省略可选字段；明确允许未知值的字段才填 `UNKNOWN`。不要凭文件名、目录顺序或 OCR 猜测补值。

## 检查和产品

- 初查阶段固定为 `INITIAL_CHECK`，复查阶段固定为 `RECHECK`；不要把现场判定、抽样送检或复检写成检查阶段。
- 产品的 `clientRef` 在整个清单中保持稳定。产品结果、网售、维修、市场准入和问题描述属于对应产品，不扩散到同案其他产品。
- 产品结果为 `UNQUALIFIED` 时，按 Schema 同时提供检查方式和非空问题描述。
- 抽样送检才可填写复检申请或复检结果；其余情形按 Schema 省略。

## 文件、槽位与版本

- `files[].clientRef`、检查引用、产品引用、槽位引用和附件引用均须符合 Schema 的稳定引用规则，并且相互不重复。
- 普通正式槽位的 `versions` 最多两个：`ELECTRONIC` 和 `SCANNED` 各最多一个。现场照片槽位只能有一个 `SCANNED` 版本。
- 电子文本清晰时优先填电子版；扫描签字、手写更正或现场照片才填扫描件。不要把截图、原 ZIP 或原始组合件直接作为正式版本。
- 每一个 `files` 条目必须恰好被正式槽位版本或其他附件引用；没有明确逻辑归属的文件不进入清单。
- 一个来源对应多个槽位时，按每个逻辑槽位生成独立规范 PDF、独立 `fileRef` 和独立相对路径，再分别关联。不得共享同一个 `fileRef`。
- `OTHER_ATTACHMENT` 只用于确实不属于 51 个固定槽位的 PDF；它不设置电子版/扫描件版本槽位。
- 拆分计划的每个目标必须位于工作目录的 `normalized/` 下，且不得重复、覆盖已有文件或指向原件目录。空白页不生成规范 PDF。
