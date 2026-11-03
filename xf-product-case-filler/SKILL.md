---
name: xf-product-case-filler
description: 已并入 xf-product-case-registry（兼容转向说明）：案卷截图/OCR 字段抽取校验改用 registry 的 `fields validate` 与 references/field-rules.md；本 Skill 不再写 Excel、不重命名或删除截图，保留仅为精确卸载收口。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 消防产品案卷填表（已并入统一案卷入口）

本 Skill 已退役，不再是任何活动流程的依赖。请改用 **xf-product-case-registry**：

| 原用途 | 现入口 |
| --- | --- |
| 抽取 JSON 校验 | `registry_cli.py fields validate <extractions.json> [--image-dir <目录>]`（只读，不改输入） |
| 字段规则、枚举、行案判定、缺值与三态 | xf-product-case-registry `references/field-rules.md` |
| 项目编号＋初查/复查去重、多产品对应、复查日期来源、人工裁决保护 | 同上，由 `fields validate` 执行 |
| 本地整理、核验与归档 | `inventory`/`ocr`/`split`/`compose`/`validate` 后 `local-verify` |

不再提供：写入 `产品案卷数据.xlsx`、批量写表、同步工作表模板、截图原地改名，以及“写表成功后删除旧截图”的行为（原图一律保留）。统计数据在飞书维护，案卷文件只在本地保存。需要旧脚本的历史行为时，从源仓库基线提交 7cee8270205648adf82e15c4409da5286e99490c 读取，不要从本目录恢复。

卸载本 Skill 由 Codex 按受管流程核定单项入口后执行；在此之前保留本说明作为兼容转向。
