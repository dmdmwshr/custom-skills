---
name: xf-product-case-filler
description: 从消防产品案卷截图或截图 OCR 结果中抽取案件信息，直接更新“产品案卷数据.xlsx”对应大队工作表，并按检查日期、单位名称原地重命名已处理截图。用于用户提到产品案卷、消防产品检查案卷截图、案卷清单、产品监督检查记录表、月度任务表填报、按项目编号去重更新或截图归档命名时。
---

# 消防产品案卷填表

这个 skill 用于把各大队产品案卷截图整理成 Excel 数据。它不负责浏览器自动化，默认先用本机 `zerox-local` 把截图转成 Markdown，或在小批量场景由 Codex 直接查看图片抽取字段，再用本 skill 的脚本做校验、写表和截图原地改名。

## 标准流程

1. 确认工作目录结构：
   - 工作簿：`产品案卷数据.xlsx`
   - 截图目录：`产品检查案卷汇总\<大队名>`
   - 工作表：与大队名一致，例如 `江阴大队`
2. 按数字重复命名分组截图，例如 `1.png` 与 `11.png` 属于同一案。不要仅按文件名判断截图类型，要根据标题和内容识别 `案卷清单`、`检查记录表` 或 `其他附件`。
3. 抽取字段并生成 JSON。优先用 `zerox-local`：
   ```powershell
   D:\Program_Files\zerox\bin\zerox-local.cmd --input "<截图路径>" --output "<输出目录>"
   ```
   小批量、OCR 结果明显不佳或用户直接要求时，可以由 Codex 查看图片并手工整理同一 JSON。
4. 读取 `references/field-rules.md`，按字段规则整理 JSON。关键点：
   - `立卷人` 取主承办人。
   - `检查人` 取审批人。
   - 多个消防产品用数组表示，写入 Excel 时脚本会换行。
   - 下拉列只能使用表内允许值；不能确定时留空。
5. 校验抽取 JSON：
   ```powershell
   python scripts/validate_extractions.py "<extractions.json>" --image-dir "<截图目录>"
   ```
6. 写入工作簿并原地重命名截图：
   ```powershell
   python scripts/fill_workbook.py --workbook "<产品案卷数据.xlsx>" --brigade "<大队名>" --image-dir "<截图目录>" --extractions "<extractions.json>"
   ```

## JSON 格式

JSON 可以是案件数组，也可以是包含 `cases` 数组的对象。每个案件对象固定使用这些字段：

```json
{
  "project_no": "32002221C202600002",
  "unit_name": "江阴市长之橙酒店管理有限公司",
  "unit_address": "无锡市江阴市澄江街道梅园社区应天河路199号",
  "case_handler": "缪晓清",
  "inspector": "周晓昌",
  "inspection_date": "2026-01-09",
  "products": ["过滤式消防自救呼吸器(TZL30A)", "过滤式消防自救呼吸器(TZL 30A)"],
  "station_or_team": "否",
  "method": "抽样送检",
  "qualified": "不合格",
  "case_type": "否",
  "online_sale": "",
  "source_files": ["1.png", "11.png"],
  "file_roles": {
    "1.png": "检查记录表",
    "11.png": "案卷清单"
  },
  "missing_fields": ["online_sale"],
  "notes": ""
}
```

## 写表规则

- 脚本按 `--brigade` 匹配工作表，按 C 列 `项目编号` 去重。项目编号已存在时更新同一行，不新增重复行。
- 新案件写入第一个 B-M 全空的行；如果模板行已满，脚本复制末行样式追加新行并延续序号。
- 只写 B-M 列，不改表头、列宽、工作表顺序和无关单元格。
- G/I/J/K/L/M 下拉列只写入允许值。非法值会被留空并标红。
- 没有数据的目标单元格留空并设置浅红底色 `FFFFC7CE`；后续补入数据时清除该红底。
- 写入前自动备份工作簿，备份命名为 `产品案卷数据_自动填充备份_yyyyMMdd_HHmmss.xlsx`。

## 截图命名

- 已成功写入的案件截图在原目录原地改名，不删除图片。
- 文件名格式：
  - `YYYY-MM-DD_单位名称_案卷清单.png`
  - `YYYY-MM-DD_单位名称_检查记录表.png`
  - `YYYY-MM-DD_单位名称_其他附件.png`
- 日期优先取检查记录表中的监督检查时间，也就是 JSON 的 `inspection_date`。缺少完整日期时使用 `未知日期`。
- 单位名称会自动清理 Windows 文件名非法字符；同名冲突时追加 `_2`、`_3`。

## 资源

- `references/field-rules.md`：字段映射、下拉值、截图分类和缺失处理规则。
- `scripts/validate_extractions.py`：校验抽取 JSON。
- `scripts/fill_workbook.py`：备份、写入 Excel、标红缺失单元格、原地重命名截图。
