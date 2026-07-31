---
name: xf-product-case-registry
description: 清点、哈希、识别、OCR、拆分、规范命名消防产品案卷 PDF、监督系统截图或 ZIP，分析电子版/扫描件来源优先级，为每个逻辑文书选择电子版和扫描件各最多一份规范 PDF，生成可追溯的 CaseImportManifestV1，并通过消防产品案卷信息登记系统的幂等接口导入及同步文书版本。用于用户提到 PDF 案卷包、产品信息截图、组合扫描件拆分、案卷文件重命名、字段证据/冲突核对、项目包导入 product-cases.meifu.zzxhlyj.top，或样例案卷 32002207C202600033 时；截图转 Excel 仍使用 xf-product-case-filler。
---

# 消防产品案卷提取与导入

把原案卷包视为只读证据，所有文本、OCR、拆分文件、manifest 和上传状态均写入独立工作目录。不得覆盖、重命名或删除原 PDF/ZIP。

## 固定边界

- PDF/ZIP 清点、拆分命名、系统导入使用本 skill。
- 截图抽取并填写 Excel 使用 `xf-product-case-filler`，不要混用。
- 原件缺失、OCR 失败或无法确认时写 `UNKNOWN`/`missingItems`，绝不推断为“无”。
- 人工确认值不得由自动结果覆盖；服务端冲突必须进入待核对。
- `png`、`jpg`、`jpeg` 一律按单页图片清点并调用 Zerox；OCR 结果只作为证据，不能凭 OCR 结果猜测业务值。
- 网售情况属于单个产品，只能填写 `products[].onlineSale=YES|NO|UNKNOWN`；不得在案卷层填写或把一个网售产品扩散到同案其他产品。
- 每个逻辑文书的正式版本只允许 `ELECTRONIC`、`SCANNED` 各一份，且只能引用规范化 PDF；截图、原始组合件和重复副本仅保留为 `fileLinks`/字段证据，不能成为正式版本。
- 案卷类型按固定优先级归一：具有页码和直接刑事表述的证据才为 `CRIMINAL`；否则任一产品存在整改复查不合格（`RECHECK` + `UNQUALIFIED`）即为 `ADMINISTRATIVE`；其余一律为 `UNKNOWN` 并创建 `caseType` 待核对项。自动路径绝不把未确认案件写为 `NONE`；处罚、罚字、处罚文书或通报本身不足以推定刑案或行案。
- 文件名只作提示；正文标题、文号、检验类别和关联对象决定文书分类。
- `检验报告.pdf` 只有正文明确“型式试验/型式检验”时才归为型式检验报告，不能误归为本案抽样送检报告。
- 检查阶段只允许初查、整改复查；现场判定和抽样送检是检查方法。复检只作为抽样送检记录下的单次子流程，不能写成检查阶段或与整改复查混用。
- 未经用户明确要求，不运行浏览器自动化。

## 环境

在本 skill 目录使用固定 Python 3.12 环境：

```powershell
uv sync --python 3.12
uv run python scripts/registry_cli.py --help
```

脚本只在当前 Zerox 子进程中把 `D:\Program_Files\poppler\Library\bin` 放到 `PATH` 前端，不修改系统或全局 PATH。

## 标准流程

### 1. 清点与文本层提取

工作目录必须位于原案卷目录之外：

```powershell
uv run python scripts/registry_cli.py inventory `
  "<案卷目录或ZIP>" --work-dir "<独立工作目录>"
```

检查：

- `inventory.json`：本地路径、页数、文本层和待 OCR 页；
- `source-directory-manifest.json`：可上传的去本机路径清单；
- `text/`：逐页文本层结果。

### 2. 仅 OCR 扫描页

执行前先核对 `zerox-local` 当前模型端点。若端点会把页面内容发送到外部模型服务，真实案卷、执法文书或其他敏感材料必须先取得用户对该第三方数据传输的明确授权；不得把“本机启动命令”误称为“内容仅在本机处理”。未获授权时停止 OCR，保留 `needsOcrPages` 和中间清单，继续处理已有可靠文本层的内容，且不得把未识别写成“无”。检查端点时不得输出 `.env.local` 中的密钥。

```powershell
uv run python scripts/registry_cli.py ocr `
  --work-dir "<工作目录>" --concurrency 1
```

默认调用本机 `zerox-local`。失败结果保留在 `ocr-index.json`；先修复失败，不得把未提取内容写成缺失文件。只有 Zerox 结构明显不佳时才按 `zerox-local` 规则回退 MinerU。

### 3. 分析来源优先级，不直接定值

```powershell
uv run python scripts/registry_cli.py source-analysis --work-dir "<工作目录>"
```

检查 `source-analysis.json`。图片需由 OCR 命中“检查产品信息、产品名称、规格型号、标称生产者、产品所在部位、检查基数/数量、市场准入检查情况、产品质量现场检查情况”等表头识别为监督系统截图；文件名只作辅助，不能单独定类。它只输出来源类别和字段组优先级，不生成最终业务值。

- 人工确认值永远最高，自动抽取只能补空值或追加相同值证据。
- 产品名称/型号、标称生产者、位置、检查基数/数量、市场准入和质量状态优先监督系统截图或电子文本。
- `problemDescription` 优先消防产品监督检查记录；有明确手写更正时保留扫描件证据并转人工核对。
- 扫描签字版用于签章、手写更正和归档；低质量 OCR 不得覆盖电子版。
- 电子版与扫描件出现不同值时，已选正式值可写 `fieldEvidence`；另一候选优先写入 `case-data.json.reviewItems.candidates`（候选值、可信等级、来源文件和页码），不自动择一。旧 `currentValue/incomingValue` 仍兼容；没有 `candidates` 时，`message` 必须写明来源文件与页码。

### 4. 形成语义数据与拆分计划

读取：

- `references/case-data-format.md`
- `references/document-classification.md`

逐页核对标题、文号、日期、产品、阶段、对象和案卷类型，编写：

- `case-data.json`：案卷、产品、阶段检查、文书、证据和缺失项；`CRIMINAL` 必须提供含文件、页码和直接刑事表述的字段证据，`UNKNOWN` 必须建立 `caseType` 待核对项。`ADMINISTRATIVE` 的规则证据由 `compose` 生成，其 `value.inspectionRef` 引用整改复查不合格记录；
- `split-plan.json`：组合 PDF 的经确认页码范围；每项必须写与 `case-data.documents[].clientRef` 完全一致的稳定 `documentRef`，并写 `documentVersionKind=ELECTRONIC|SCANNED|UNKNOWN`；无法可靠判断时明确写 `UNKNOWN`，不得猜测。

每条产品检查的 `caseInspectionRef` 可由人工显式指定；未指定时 `compose` 会按阶段和检查日期生成共享父检查引用，无日期时按同阶段同序位生成。不要把不同阶段或不同日期的记录填写为同一引用。

不要仅凭 `p1` 或文件名假定一页就是一份文书。

### 5. 生成规范化 PDF

```powershell
uv run python scripts/registry_cli.py split `
  --work-dir "<工作目录>" --plan "<split-plan.json>"
```

文件名固定为 `项目编号_阶段_文书类型_文号或日期_电子版或扫描件_序号.pdf`。`UNKNOWN` 使用“版本待核对”，但不得进入 `documents[].versions`。脚本只写 `normalized/`，并在 `split-index.json` 保存 `documentRef`、`documentVersionKind`、原文件及页码映射；缺少或无法匹配 `documentRef` 时立即失败。

### 6. 组装并本地校验 manifest

```powershell
uv run python scripts/registry_cli.py compose `
  --work-dir "<工作目录>" --case-data "<case-data.json>"

uv run python scripts/registry_cli.py validate `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json"
```

必须先消除结构错误、悬空引用和哈希错误。低可信字段可以保留，但要带 `OCR_ONLY` 证据并创建相应 `missingItems` 或待核对信息。

`documents[].versions` 只保存每种类型选出的最佳规范 PDF；同一 `documentRef + kind` 只有一个候选时由 `compose` 自动选入，存在多个候选时必须在 `case-data` 显式选定一个，否则不猜测。`compose` 会把该 `documentRef` 的所有候选原文件与页码写入 `fileLinks`，未选候选强制创建 `DUPLICATE_CANDIDATE`；`UNKNOWN` 强制创建 `LOW_CONFIDENCE`。同一 `stage + documentType + documentNo + issueDate` 出现两条逻辑文书会校验失败，必须先合并来源。

### 7. 接口上传

先执行不联网预演：

```powershell
uv run python scripts/registry_cli.py upload `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json" `
  --api-base "https://product-cases.meifu.zzxhlyj.top" `
  --dry-run
```

确认后正式上传并终结：

```powershell
uv run python scripts/registry_cli.py upload `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json" `
  --api-base "https://product-cases.meifu.zzxhlyj.top" `
  --finalize
```

上传状态写入工作目录的 `upload-state.json`，失败后使用同一命令续传。幂等键由原包哈希确定；重复执行不得增加案卷、产品、阶段检查或文件数量。

### 8. 独立同步文书版本

导入任务可能因相同包哈希已 `FINALIZED` 而直接返回旧结果。无论是否发生幂等重放，导入后都运行：

```powershell
uv run python scripts/registry_cli.py sync-document-versions `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json" `
  --api-base "https://product-cases.meifu.zzxhlyj.top" `
  --dry-run

uv run python scripts/registry_cli.py sync-document-versions `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json" `
  --api-base "https://product-cases.meifu.zzxhlyj.top"
```

命令按项目编号唯一定位案卷，再按阶段、文书类型、文号和日期唯一匹配文书；无匹配或多匹配时写入 `document-version-sync-state.json` 并停止猜测。相同 SHA-256 直接跳过；不同内容以服务器当前文书版本号执行乐观锁 PUT，同一命令可安全重跑。

## 完成条件

- 原件哈希与清点时一致；
- 每个规范化 PDF 都能回溯原文件及页码；
- manifest 本地校验通过；
- 服务端 `/api/ready` 正常；
- `validate` 和 `finalize` 均成功；
- 返回的新增、冲突、缺失、跳过明细已保存；
- 前端待核对项与无法确认内容一致。
- `document-version-sync-state.json` 为 `DONE`，或所有 `NEEDS_REVIEW` 项已人工处理。
