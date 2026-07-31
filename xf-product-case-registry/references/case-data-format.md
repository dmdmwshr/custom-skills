# case-data.json 格式

`case-data.json` 只保存语义结果；`compose` 会从 `inventory.json` 与 `split-index.json` 补齐包信息、文件哈希、页数和本地上传映射。

## 最小结构

```json
{
  "case": {
    "projectNo": "32002207C202600033",
    "brigadeCode": "XISHAN",
    "unitName": "锡山区东湖塘鑫源生鲜超市",
    "unitAddress": "锡山区东港镇东湖塘宜东苑社区底层商铺51号",
    "inspectionForm": "ROUTINE",
    "caseHandler": "朱大海",
    "inspector": "张慧",
    "caseType": "UNKNOWN",
    "onlineSale": "UNKNOWN"
  },
  "products": [
    {
      "sequence": 1,
      "name": "直流水枪",
      "modelSpec": "QZ3.5/7.5",
      "nominalProducer": "高邮市顺威消防科技有限公司",
      "inspections": [
        {
          "stage": "INITIAL_CHECK",
          "method": "ONSITE",
          "inspectionDate": "2026-05-19",
          "inspectionResult": "UNQUALIFIED",
          "problemDescription": "跌落试验后破裂"
        },
        {
          "stage": "RECHECK",
          "method": "ONSITE",
          "inspectionDate": "2026-05-27",
          "inspectionResult": "QUALIFIED",
          "problemDescription": "已更换合格产品"
        }
      ]
    }
  ],
  "documentRequirements": [],
  "documents": [],
  "fieldEvidence": [],
  "missingItems": [
    {
      "entityRef": "case:32002207C202600033",
      "fieldPath": "caseType",
      "reason": "未识别到明确刑事直接证据，且不存在整改复查不合格记录；待人工核对。"
    }
  ]
}
```

## 案卷类型归一

`case.caseType` 使用服务端既有枚举：`NONE`、`ADMINISTRATIVE`、`CRIMINAL`、`UNKNOWN`。`compose` 不改变 Manifest V1 结构，但会按以下优先级归一其值：

1. 仅当 `fieldEvidence` 中存在 `entityRef=case:<项目编号>`、`fieldPath=caseType`、`value=CRIMINAL` 的直接刑事证据时，写 `CRIMINAL`。该证据必须具有文件引用、页码，以及“刑事案件”“刑案”或“移送公安”等直接表述；`OCR_ONLY`、处罚、罚字、行政处罚文书和通报线索不足以定性。
2. 否则，只要任一产品检查记录同时为 `stage=RECHECK` 与 `inspectionResult=UNQUALIFIED`，写 `ADMINISTRATIVE`。`compose` 自动生成一条 `fieldEvidence`，其 `sources[0]` 为 `kind=RULE`，并在 `value.inspectionRef` 引用该条复查不合格记录。
3. 其他情况一律写 `UNKNOWN`，并自动建立 `entityRef=case:<项目编号>`、`fieldPath=caseType` 的 `missingItems` 待核对项。

自动路径从不把“尚未确认不是行案/刑案”写为 `NONE`。`NONE` 只可由人工基于明确反向证据填写，并须保留非 `OCR_ONLY` 的 `caseType` 字段证据。`CRIMINAL` 的优先级高于整改复查不合格。

`compose` 自动生成：

- 案卷引用：`case:<项目编号>`；
- 产品引用：`product:<序号>`；
- 检查记录引用：`inspection:<产品序号>:<阶段小写>`；同一阶段有多条方式记录时依次追加 `:2`、`:3`；
- 文书和资料要求缺少 `clientRef` 时的顺序引用。

## 文件引用

文书 `fileLinks` 可以用上传相对路径，`compose` 会转换为 `fileRef`：

```json
{
  "documentType": "TYPE_TEST_REPORT",
  "documentNo": "ZB2018M3262",
  "productRefs": ["product:1"],
  "inspectionRefs": [],
  "fileLinks": [
    {
      "relativePath": "original/检验报告.pdf",
      "relationRole": "PRIMARY",
      "pageStart": 1,
      "pageEnd": 10
    }
  ],
  "classificationEvidence": "正文首页检验类别明确为型式试验"
}
```

可用路径：

- 原文件：`original/<原相对路径>`；
- 原 ZIP：`package/<ZIP文件名>`；
- 规范化文件：`normalized/<规范化文件名>`；
- 目录清单：`metadata/source-directory-manifest.json`。

字段证据 `sources` 同样可以使用 `relativePath`，并必须给出页码和简短证据摘要：

```json
{
  "entityRef": "product:1",
  "fieldPath": "nominalProducer",
  "value": "高邮市顺威消防科技有限公司",
  "trustLevel": "CORROBORATED",
  "sources": [
    {
      "kind": "PDF_TEXT",
      "relativePath": "original/消防产品监督检查记录(编号：〔2026〕第0036号).pdf",
      "page": 1,
      "evidence": "产品信息栏"
    }
  ]
}
```

## 枚举

- 大队：`JIANGYIN`、`YIXING`、`LIANGXI`、`XISHAN`、`HUISHAN`、`BINHU`、`XINWU`、`JINGKAI`
- 阶段：`INITIAL_CHECK`、`RECHECK`
- 方法：`ONSITE`、`SAMPLING`、`UNKNOWN`
- 结果：`QUALIFIED`、`UNQUALIFIED`、`PENDING`、`UNKNOWN`
- 复检状态：`NOT_APPLIED`、`APPLIED`、`ACCEPTED`、`REJECTED`、`COMPLETED`、`UNKNOWN`
- 证据可信度：`DETERMINISTIC`、`CORROBORATED`、`OCR_ONLY`、`MANUAL`
- 文件关系：`PRIMARY`、`SOURCE_COPY`、`DUPLICATE_COPY`、`SUPPORTING_ATTACHMENT`

`SAMPLING` 是初查或复查采用的检查方式，不是检查阶段。只有 `method=SAMPLING` 的记录可以填写 `reinspection*` 字段；复检是当事人对首次抽样检验结果有异议后的单次子流程，绝不能与大队整改后的 `RECHECK` 混用。对现场判定结论有异议后首次送检属于监督检验，不直接写成复检。

以下仅演示抽样记录下复检子流程的字段结构，不表示样例案卷存在抽样或复检事实：

```json
{
  "stage": "INITIAL_CHECK",
  "method": "SAMPLING",
  "inspectionDate": "2026-06-01",
  "inspectionResult": "UNQUALIFIED",
  "submittedSampleName": "送检样品名称",
  "reinspectionStatus": "COMPLETED",
  "reinspectionApplicationDate": "2026-06-10",
  "reinspectionAcceptanceDate": "2026-06-11",
  "reinspectionAgency": "法定检验机构",
  "reinspectionReportNo": "复检报告编号",
  "reinspectionReportDate": "2026-06-20",
  "reinspectionResult": "UNQUALIFIED",
  "reinspectionNotes": "使用备用样品完成一次复检"
}
```
