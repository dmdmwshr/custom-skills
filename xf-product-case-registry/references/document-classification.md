# 文书分类与拆分规则

## 判定优先级

1. 正文标题；
2. 文号；
3. 正文中的检查/检验类别；
4. 被检查单位、产品、阶段和送达对象；
5. 文件名，仅作为最后提示。

同一逻辑文书可以关联多份物理来源。独立文书、组合扫描件、文本版和重复页都保留 `FileAsset/fileLinks`；通过文号、标题、日期和对象归并到同一个 `CaseDocument`。正式归档另由 `versions` 选择电子版和扫描件各最多一份规范 PDF。

## 来源优先级与多版本关系

先运行 `source-analysis`，它只分析来源，不直接填写业务字段。人工确认值始终优先，任何自动来源都不能覆盖。

| 字段组 | 自动来源优先级 | 使用限制 |
| --- | --- | --- |
| 产品名称/型号、标称生产者、位置、检查基数/数量、市场准入、质量状态 | 监督系统截图 → 监督系统电子 PDF → 监督检查记录电子 PDF → 扫描件 OCR → 文件名提示 | 截图或电子文本与人工值冲突时创建待核对项。 |
| 问题描述 | 消防产品监督检查记录 → 监督系统截图/电子 PDF → 扫描件 OCR → 文件名提示 | 监督检查记录用于问题细节；手写更正只能作为人工核对线索。 |
| 签章、签字、手写更正 | 扫描签字件 → 电子版 | 扫描件必须保留；低质量 OCR 不得自动改写电子字段。 |

不同来源的候选值不一致时，只为已选正式实体值保留 `fieldEvidence`；另一候选优先以 `reviewItems.candidates`（值、可信等级、来源文件/页码）保存，旧 `currentValue/incomingValue` 仍兼容。不得依赖文件名自动择一，也不要把与实体值不一致的候选写为 `fieldEvidence`。

`documents[].versions` 只允许 `ELECTRONIC`、`SCANNED` 各一份，必须引用带对应 `documentVersionKind` 的规范化 PDF。`fileLinks` 保留所有原始来源和页码映射；已有最佳版本之外的来源创建 `DUPLICATE_CANDIDATE`。无法可靠判断类型时不猜，拆分项标为 `UNKNOWN`，逻辑文书 `versions` 留空并创建 `LOW_CONFIDENCE`。图片、清单和截图可作来源或另建逻辑附件，但不能成为正式版本。

## 阶段

- `INITIAL_CHECK`：大队首次监督检查。
- `RECHECK`：大队再次检查整改结果。

“现场判定/抽样送检”属于检查方法，不能代替阶段。同一初查或复查可以有多条方法记录，例如先现场判定，因当事人对判定结论有异议后再抽样送法定检验机构进行监督检验。

“复检”不是检查阶段，也不是整改复查。它只发生在当事人对首次抽样检验结果有异议、提出书面申请并获受理后；使用备用样品，且申请以一次为限。复检信息写入对应 `method=SAMPLING` 检查记录的 `reinspection*` 字段，并用 `REINSPECTION_APPLICATION`、`REINSPECTION_ACCEPTANCE`、`REINSPECTION_REPORT` 等逻辑文书提供证据。

## 常用类型

| 类型代码 | 判断要点 |
| --- | --- |
| `PRODUCT_INSPECTION_RECORD` | 消防产品监督检查记录，按正文检查形式关联初查/复查 |
| `ONSITE_UNQUALIFIED_NOTICE` | 现场检查判定不合格通知书 |
| `RECTIFICATION_ORDER` | 责令限期改正通知书 |
| `SERVICE_RECEIPT` | 送达回证；按被送达文书和文号逐份关联 |
| `CASE_NOTIFICATION_LETTER` | 涉嫌违法生产/销售消防产品通报函 |
| `TYPE_TEST_REPORT` | 正文明确型式试验或型式检验 |
| `SAMPLING_TEST_REPORT` | 本案抽样送检形成的检验报告 |
| `REINSPECTION_REPORT` | 检验机构复检报告 |
| `ONSITE_PHOTO` | 现场照片，阶段需由内容或其他证据确认 |
| `APPROVAL_FORM` | 各类内部审批表，需记录其审批事项 |
| `VOLUME_NOTE` | 卷内备考表 |

类型代码允许按系统字典扩展，但不得用笼统“其他”掩盖可确定的正文类型。

## 拆分

- 每页先提取标题、文号、日期、对象；相邻页只有这些字段连续一致时才合并。
- 组合 PDF 中发现独立文书副本时，拆分副本并关联同一逻辑文书。
- 不因已有独立文书就丢弃组合件中的重复页。
- OCR 失败的页保留在原组合件中，创建核对项；不要猜测拆分边界。

`split-plan.json` 示例：

```json
{
  "projectNo": "32002207C202600033",
  "items": [
    {
      "documentRef": "document:service-receipt-0776",
      "sourceRelativePath": "送达回证.pdf",
      "pageStart": 1,
      "pageEnd": 1,
      "stage": "INITIAL_CHECK",
      "documentType": "SERVICE_RECEIPT",
      "documentLabel": "送达回证",
      "documentNoOrDate": "锡锡消送证字〔2026〕第0776号",
      "documentVersionKind": "SCANNED",
      "sequence": 1
    }
  ]
}
```

`documentRef` 与 `documentVersionKind` 均必填。`documentRef` 必须完全等于目标 `case-data.documents[].clientRef`，同一逻辑文书的电子版、扫描件、组合件副本和 `UNKNOWN` 候选均填写同一个值；找不到唯一目标文书时停止，不新造近似引用。`documentVersionKind` 只允许 `ELECTRONIC`、`SCANNED`、`UNKNOWN`。前两者生成带“电子版/扫描件”的规范名并可进入 `versions`；`UNKNOWN` 生成“版本待核对”规范名，只保留为候选和来源证据。

`compose` 按 `documentRef` 汇总全部规范化候选。每种 kind 只有一个候选时自动选用；多个同 kind 候选必须显式指定最佳版本，其余候选的原文件和页码仍自动写入 `fileLinks` 并生成 `DUPLICATE_CANDIDATE`。同一逻辑身份不得拆成两条 `documents`。

案卷级文书在 `case-data.documents[]` 中可不填 `stage`，其拆分项使用 `stage=CASE`；检查级文书必须让拆分项阶段与文书的 `INITIAL_CHECK` 或 `RECHECK` 完全一致。`CASE` 只用于规范文件名和候选归属，不写入服务端检查阶段枚举。
