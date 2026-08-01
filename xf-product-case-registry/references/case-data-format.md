# case-data.json 格式

格式与工作流说明版本：`0.6.0`。

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
    "caseType": "UNKNOWN"
  },
  "products": [
    {
      "sequence": 1,
      "name": "直流水枪",
      "modelSpec": "QZ3.5/7.5",
      "nominalProducer": "高邮市顺威消防科技有限公司",
      "onlineSale": "UNKNOWN",
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

## 案卷、父检查与受检产品层级

层级固定为：

1. `case`：案卷；
2. `caseInspectionRef`：案卷下某一次初查或复查父检查；
3. `product` 与其 `inspections[]`：该次检查实际涉及的受检产品及检查方式、结果。

检查级文书必须同时输出：

- `stage`：`INITIAL_CHECK` 或 `RECHECK`；
- `caseInspectionRefs`：唯一定位该文书所属的父检查；
- `inspectionRefs`：该父检查下、文书实际涉及的产品检查记录；
- `productRefs`：与上述产品检查对应的产品。

正式 `CaseImportManifestV1` 已支持 `caseInspectionRefs`。已确认归属时，`case-data.json` 与最终 manifest 的 `stage`、唯一父检查引用及产品检查引用必须一致；`inspectionRefs` 只列实际涉及的产品检查，父检查级文书可使用空数组。无法唯一确认父检查时，`case-data` 将父检查引用和产品检查引用置空；正式 manifest 省略 `stage/caseInspectionRefs`、保留 `inspectionRefs=[]`，并创建 `CaseDocument.stage` 待核对项，禁止由文件名补值。

检查归属证据优先级固定为：正文明确出现“复查/整改复查”或初查表述 > 文号及被送达文书、监督检查记录等关联记录 > 同类型检查日期排序（最早一次为初查、后续为复查）。日期排序只能在同一案卷、同类型检查且无冲突时使用。文件名、目录名、页序号和单一日期只能作提示，不能单独作为证据。

检查级文书示例：

```json
{
  "clientRef": "document:inspection-record-initial-0036",
  "documentType": "PRODUCT_INSPECTION_RECORD",
  "documentNo": "〔2026〕第0036号",
  "issueDate": "2026-05-19",
  "stage": "INITIAL_CHECK",
  "caseInspectionRefs": ["case-inspection:initial:2026-05-19"],
  "productRefs": ["product:1"],
  "inspectionRefs": ["inspection:1:initial_check"],
  "classificationEvidence": "正文为首次监督检查记录，文号0036，日期2026-05-19，与父检查及产品检查一致"
}
```

自动归属时可增加本地 `stageEvidence`（`compose` 会消费它，但不会把该辅助对象写入正式 manifest）：

```json
{
  "method": "BODY_TEXT",
  "inspectionDate": "2026-05-19",
  "sources": [
    {
      "kind": "PDF_TEXT",
      "relativePath": "original/消防产品监督检查记录.pdf",
      "page": 1,
      "evidence": "正文标题及检查形式明确为首次监督检查"
    }
  ]
}
```

关联记录使用 `method=DOCUMENT_LINK`，并填写 `relatedInspectionRef`、`relatedDocumentRef` 或 `relatedDocumentNo`；送达回证优先继承被送达文书的父检查，不因送达日期更晚而改判复查。`fieldEvidence` 中 `entityRef=document:<引用>`、`fieldPath=stage` 的正文来源也会参与同一判定。`FILENAME_HINT` 只能保留提示，不能触发阶段写入。

## 产品级网售

`onlineSale` 只属于产品，枚举为 `YES`、`NO`、`UNKNOWN`。同一案卷有四个产品且仅一个来自网售时，只给对应产品写 `YES`；其余产品必须按各自证据写 `NO` 或 `UNKNOWN`。`fieldEvidence`、`missingItems` 和 `reviewItems` 的 `entityRef` 必须是对应 `product:<序号>`，不得指向案卷。

## 文书版本与原始来源

`documents[].versions` 只保存正式归档版本，每个逻辑文书的 `ELECTRONIC`、`SCANNED` 各最多一份；只能引用 `NORMALIZED_FILE` 且 MIME 为 `application/pdf`。每条文书必须有稳定且唯一的 `clientRef`，`split-plan.items[].documentRef` 必须完全指向该值。`fileLinks` 继续保存所有原始来源、组合件和重复副本映射。两者都可以用上传相对路径，`compose` 会转换为 `fileRef`：

```json
{
  "clientRef": "document:type-test-report-zb2018m3262",
  "documentType": "TYPE_TEST_REPORT",
  "documentNo": "ZB2018M3262",
  "productRefs": ["product:1"],
  "caseInspectionRefs": [],
  "inspectionRefs": [],
  "versions": [
    {
      "relativePath": "normalized/32002207C202600033_案卷_型式检验报告_ZB2018M3262_电子版_01.pdf",
      "kind": "ELECTRONIC"
    }
  ],
  "fileLinks": [
    {
      "relativePath": "original/检验报告.pdf",
      "relationRole": "SOURCE_COPY",
      "pageStart": 1,
      "pageEnd": 10
    }
  ],
  "classificationEvidence": "正文首页检验类别明确为型式试验"
}
```

该 `TYPE_TEST_REPORT` 示例是案卷/产品材料，所以不填写 `stage`。`case-data` 可用空 `caseInspectionRefs` 表示不适用，`compose` 在正式 manifest 中省略该可选字段；必需字段 `inspectionRefs` 保持空数组。CCC 证书、技术鉴定资料等执行相同规则，不得仅因材料出现在初查或复查目录中就绑定检查。

`ONSITE_PHOTO` 必须由正文分组标题、照片水印/说明或关联监督检查记录唯一定位初查或复查父检查。照片能明确对应产品时再填写一致的 `inspectionRefs` 与 `productRefs`；只能确定父检查时两者可为空。仅文件名、拍摄日期或日期排序不足时，正式 manifest 省略 `stage/caseInspectionRefs`、保留 `inspectionRefs=[]`，并创建 `CaseDocument.stage` 的 `LOW_CONFIDENCE`。

### 正式双版本与重复来源

`versions` 的元素只允许 `fileRef`、`kind`；`kind` 只允许：

- `ELECTRONIC`：监督系统生成、具有可靠文本层的电子版 PDF；
- `SCANNED`：签字盖章或手写记录的扫描版 PDF。

`fileLinks` 已显式填写 `relationRole` 时，`compose` 原样保留，并按 `documentRef` 自动补上全部规范化候选（不仅是所选版本）的原文件及页码来源。原始来源仍使用 `PRIMARY`、`SOURCE_COPY`、`DUPLICATE_COPY`、`SUPPORTING_ATTACHMENT` 描述物理关系，但这些关系不再代表正式版本。

同一 `documentRef + kind` 只有一个候选时，`compose` 自动选入 `versions`；有多个候选时必须显式选择最佳版本，无法确定则不写该 kind。已有最佳正式版本之外的原始来源保留在 `fileLinks`，并创建 `DUPLICATE_CANDIDATE`。无法可靠判断电子版或扫描件时，拆分计划写 `documentVersionKind=UNKNOWN`，规范文件名使用“版本待核对”，该文件不得进入 `versions`，并创建 `LOW_CONFIDENCE`。截图可用于字段提取和来源证据，但不能进入 `versions`。两条文书若 `stage + documentType + documentNo + issueDate` 完全相同，必须先合并为一条逻辑文书，不能依赖同步顺序覆盖版本。

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
- 产品网售：`YES`、`NO`、`UNKNOWN`
- 正式文书版本：`ELECTRONIC`、`SCANNED`
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
