# 文书分类与拆分规则

## 判定优先级

1. 正文标题；
2. 文号；
3. 正文中的检查/检验类别；
4. 被检查单位、产品、阶段和送达对象；
5. 文件名，仅作为最后提示。

同一逻辑文书可以关联多份物理来源。独立文书、组合扫描件、文本版和重复页都保留 `FileAsset`；通过文号、标题、日期和对象归并到同一个 `CaseDocument`。

## 阶段

- `INITIAL_CHECK`：大队首次监督检查。
- `RECHECK`：大队再次检查整改结果。
- `SAMPLING_INSPECTION`：大队抽样并送检。
- `LAB_REINSPECTION`：检验机构受理的复检申请与报告。

“现场判定/抽样送检”属于方法，不能代替阶段。

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
      "sourceRelativePath": "送达回证.pdf",
      "pageStart": 1,
      "pageEnd": 1,
      "stage": "INITIAL_CHECK",
      "documentType": "SERVICE_RECEIPT",
      "documentLabel": "送达回证",
      "documentNoOrDate": "锡锡消送证字〔2026〕第0776号",
      "sequence": 1
    }
  ]
}
```
