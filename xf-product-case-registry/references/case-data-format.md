# case-data.json 格式

`case-data.json` 只保存语义结果；`compose` 会从 `inventory.json` 与 `split-index.json` 补齐包信息、文件哈希、页数和本地上传映射。

先运行 `source-analysis`。它只给出来源类别与字段组优先级，不会也不得生成最终业务字段值。

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
          "caseInspectionRef": "case-inspection:initial:2026-05-19",
          "stage": "INITIAL_CHECK",
          "method": "ONSITE",
          "inspectionDate": "2026-05-19",
          "inspectionResult": "UNQUALIFIED",
          "problemDescription": "跌落试验后破裂"
        },
        {
          "caseInspectionRef": "case-inspection:recheck:2026-05-27",
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

## 父检查引用

`caseInspectionRef` 是 Manifest V1 可选的案卷级检查事件分组键。`compose` 对每条产品检查都输出该字段：

1. 输入存在非空 `caseInspectionRef` 时原样保留。
2. 未显式指定且有 `inspectionDate` 时，以 `stage + inspectionDate` 分组；同一次检查的多个产品共享稳定引用，例如 `case-inspection:initial:2026-05-19`。
3. 未显式指定且无日期时，以每个产品内同一 `stage` 的序位分组；各产品的第一个复查记录共享 `case-inspection:recheck:ordinal-1`，第二个共享 `case-inspection:recheck:ordinal-2`。

为保持向后兼容，`validate` 接受旧 Manifest 缺少该可选字段；但字段存在时必须以小写前缀和冒号开头，且同一引用下的 `stage` 与 `inspectionDate` 必须完全一致。

`compose` 自动生成：

- 案卷引用：`case:<项目编号>`；
- 产品引用：`product:<序号>`；
- 检查记录引用：`inspection:<产品序号>:<阶段小写>`；同一阶段有多条方式记录时依次追加 `:2`、`:3`；
- 父检查引用：每条检查的 `caseInspectionRef`，用于把同一次案卷级检查下的多个产品分组；
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

### 同一文书的电子版、扫描件与附件

`fileLinks` 已显式填写 `relationRole` 时，`compose` 原样保留，绝不按文件名改写。每个逻辑文书可链接多个物理文件：

- 监督系统电子版：`PRIMARY`；
- 同文书扫描签字版：`SOURCE_COPY`，必须保留；
- 重复扫描副本：`DUPLICATE_COPY`；
- 与正文配套的图片、清单等：`SUPPORTING_ATTACHMENT`；
- 没有电子版时，扫描签字版可以为 `PRIMARY`。

电子版优先用于印刷字段抽取，扫描签字版优先用于签章、手写更正和归档。二者的字段提取不同，不得自动覆盖：已选正式值可保留 `fieldEvidence`，另一候选值写入 `reviewItems`；不要把与正式实体值不一致的候选也写为 `fieldEvidence`。

## 待核对项 `reviewItems`

`reviewItems` 是可选数组。每项必须有稳定且全清单唯一的 `clientRef`、已知 `entityRef`、可落库的 `fieldPath`、`issueType` 和 `message`。`compose` 会为缺少 `clientRef` 的项按其语义内容补 `review:<hash>`；已提供的标识原样保留，但重复或与案卷、产品、检查、资料要求、文件、文书的 `clientRef` 冲突会校验失败。旧版 `currentValue`、`incomingValue` 仍兼容；推荐用 `candidates` 为每个候选提供稳定 `candidateRef`、值、可信等级和物理来源。`VALUE_CONFLICT` 填写 `candidates` 时必须至少两个、候选标识和值均不重复，并包含正式实体值。支持：`VALUE_CONFLICT`、`LOW_CONFIDENCE`、`EXTRACTION_FAILED`、`DATA_ANOMALY`、`DUPLICATE_CANDIDATE`。

```json
{
  "reviewItems": [
    {
      "clientRef": "review:product-1-model-spec-conflict",
      "entityRef": "product:1",
      "fieldPath": "modelSpec",
      "issueType": "VALUE_CONFLICT",
      "message": "监督系统截图 original/产品信息截图.png 第1页与扫描签字件 original/监督检查记录扫描件.pdf 第1页型号不一致，未自动覆盖人工确认值。",
      "currentValue": "YB-1000",
      "incomingValue": "YB-1000D",
      "candidates": [
        {
          "candidateRef": "candidate:product-1-model-current",
          "value": "YB-1000",
          "trustLevel": "MANUAL",
          "sources": [
            {
              "kind": "MANUAL",
              "evidence": "人工确认的现有产品型号"
            }
          ]
        },
        {
          "candidateRef": "candidate:product-1-model-scan",
          "value": "YB-1000D",
          "trustLevel": "OCR_ONLY",
          "sources": [
            {
              "kind": "SIGNED_SCAN_OCR",
              "relativePath": "original/监督检查记录扫描件.pdf",
              "page": 1,
              "value": "YB-1000D",
              "evidence": "扫描签字件产品型号栏 OCR 结果"
            }
          ]
        }
      ]
    }
  ]
}
```

`candidates.sources` 复用字段证据来源结构：`kind`、`fileRef`、`page`、`value`、`evidence`。在 `case-data.json` 可用 `relativePath` 代替 `fileRef`，`compose` 会转换；页码必须关联存在的文件，来源 `value` 若填写必须与候选值一致。不要把 OCR 无法读取写成 `ABSENT`；使用 `EXTRACTION_FAILED` 或 `LOW_CONFIDENCE`，并保留来源页码。`reviewItems` 本身没有 `candidates` 时，`message` 必须写明各候选的来源文件和页码。

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
