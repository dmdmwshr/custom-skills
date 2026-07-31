---
name: xf-product-case-registry
description: 清点、哈希、识别、OCR、拆分、规范命名消防产品案卷 PDF 或 ZIP，生成可追溯的 CaseImportManifestV1，并通过消防产品案卷信息登记系统的幂等接口上传和终结导入。用于用户提到 PDF 案卷包、组合扫描件拆分、案卷文件重命名、字段证据/缺失核对、项目包导入 product-cases.meifu.zzxhlyj.top，或样例案卷 32002207C202600033 时；截图转 Excel 仍使用 xf-product-case-filler。
---

# 消防产品案卷提取与导入

把原案卷包视为只读证据，所有文本、OCR、拆分文件、manifest 和上传状态均写入独立工作目录。不得覆盖、重命名或删除原 PDF/ZIP。

## 固定边界

- PDF/ZIP 清点、拆分命名、系统导入使用本 skill。
- 截图抽取并填写 Excel 使用 `xf-product-case-filler`，不要混用。
- 原件缺失、OCR 失败或无法确认时写 `UNKNOWN`/`missingItems`，绝不推断为“无”。
- 人工确认值不得由自动结果覆盖；服务端冲突必须进入待核对。
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

```powershell
uv run python scripts/registry_cli.py ocr `
  --work-dir "<工作目录>" --concurrency 1
```

默认调用本机 `zerox-local`。失败结果保留在 `ocr-index.json`；先修复失败，不得把未提取内容写成缺失文件。只有 Zerox 结构明显不佳时才按 `zerox-local` 规则回退 MinerU。

### 3. 形成语义数据与拆分计划

读取：

- `references/case-data-format.md`
- `references/document-classification.md`

逐页核对标题、文号、日期、产品、阶段和对象，编写：

- `case-data.json`：案卷、产品、阶段检查、文书、证据和缺失项；
- `split-plan.json`：组合 PDF 的经确认页码范围。

不要仅凭 `p1` 或文件名假定一页就是一份文书。

### 4. 生成规范化 PDF

```powershell
uv run python scripts/registry_cli.py split `
  --work-dir "<工作目录>" --plan "<split-plan.json>"
```

文件名固定为 `项目编号_阶段_文书类型_文号或日期_序号.pdf`。脚本只写 `normalized/`，并在 `split-index.json` 保存原文件及页码映射。

### 5. 组装并本地校验 manifest

```powershell
uv run python scripts/registry_cli.py compose `
  --work-dir "<工作目录>" --case-data "<case-data.json>"

uv run python scripts/registry_cli.py validate `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json"
```

必须先消除结构错误、悬空引用和哈希错误。低可信字段可以保留，但要带 `OCR_ONLY` 证据并创建相应 `missingItems` 或待核对信息。

### 6. 接口上传

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

## 完成条件

- 原件哈希与清点时一致；
- 每个规范化 PDF 都能回溯原文件及页码；
- manifest 本地校验通过；
- 服务端 `/api/ready` 正常；
- `validate` 和 `finalize` 均成功；
- 返回的新增、冲突、缺失、跳过明细已保存；
- 前端待核对项与无法确认内容一致。
