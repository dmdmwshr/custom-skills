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
  "missingItems": []
}
```

`compose` 自动生成：

- 案卷引用：`case:<项目编号>`；
- 产品引用：`product:<序号>`；
- 阶段引用：`inspection:<产品序号>:<阶段小写>`；
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
- 阶段：`INITIAL_CHECK`、`RECHECK`、`SAMPLING_INSPECTION`、`LAB_REINSPECTION`
- 方法：`ONSITE`、`SAMPLING`、`UNKNOWN`、`NOT_APPLICABLE`
- 结果：`QUALIFIED`、`UNQUALIFIED`、`PENDING`、`UNKNOWN`
- 证据可信度：`DETERMINISTIC`、`CORROBORATED`、`OCR_ONLY`、`MANUAL`
- 文件关系：`PRIMARY`、`SOURCE_COPY`、`DUPLICATE_COPY`、`SUPPORTING_ATTACHMENT`

不要把 `inspection.stage=LAB_REINSPECTION` 与大队整改后的 `RECHECK` 混用。
