# 案卷字段抽取与校验规则

原 xf-product-case-filler 规则已并入并退役卸载。本规则只约束抽取结果的**只读校验**（`fields validate`）与人工整理口径；不写 Excel、不改名或删除任何截图。统计数据由飞书维护，Excel 导出不是默认流程。

## 稳定键与阶段

- 一行 = 一个（项目编号 `project_no`，检查阶段 `inspection_phase`，轮次 `inspection_round`）。阶段只有 `初查`、`复查`，缺省按 `初查`，轮次缺省为 1（只在校验时局部缺省，不回写输入）；多次复查用不重复的 `inspection_round`，以最后一轮结果判定行案。
- 同一项目编号最多两行；存在 `复查` 行必须有 `初查` 行且初查 `qualified=不合格`（人工裁决 `manual_fields` 含 `qualified` 的除外）。
- 项目编号格式 `\d{8}[A-Z]\d{9}`，不用截图名、行号或飞书记录号代替项目身份。

## JSON 字段

| 字段 | JSON key | 规则 |
| --- | --- | --- |
| 单位名称 | `unit_name` | 项目详情或检查记录表中的被检查单位。 |
| 项目编号 | `project_no` | 必填。 |
| 初查/复查 | `inspection_phase` | `初查`、`复查`。 |
| 单位地址 | `unit_address` | 单位地址。 |
| 标称生产者 | `nominal_producers` | 与 `products` 同顺序；数量不一致只告警，不猜对应关系。 |
| 立卷人 | `case_handler` | 主承办人，不取协办人。 |
| 检查人 | `inspector` | 最终审批人；明显不是人名时留空并写入 `missing_fields`。 |
| 检查时间 | `inspection_date` | 完整日期 `YYYY-MM-DD`。**复查行日期必须取自复查自己的来源**：`inspection_date_source` 指向 `source_files` 中的文件；与初查日期相同且无独立来源视为复制，报错；没有来源只告警；没有日期则保持留空（未知），不推测。 |
| 消防产品 | `products` | 产品名与规格型号合并为一个字符串，多个产品为数组。 |
| 微型消防站或快速处置队 | `station_or_team` | `微型消防站`、`快速处置队`、`否`；无法判断留空。 |
| 现场判定或抽样送检 | `method` | `现场判定`、`抽样送检`；无法判断留空。 |
| 是否合格 | `qualified` | `合格`、`不合格`；结果未出时留空并写 `missing_fields`。 |
| 行案/刑案 | `case_type` | 仅同一项目复查且复查不合格时才是 `行案`；明确刑事线索才是 `刑案`；否则 `否`。`[罚]`、罚字、处罚文书只作备注，不参与自动判定。 |
| 网售 | `online_sale` | 明确体现才写 `是`/`否`，否则留空（未知）。 |
| 来源文件 | `source_files`/`file_roles` | 角色 `案卷清单`、`检查记录表`、`其他附件`；`--image-dir` 时核对文件存在。 |

## 三态与缺值

- `station_or_team`、`method`、`qualified`、`online_sale`、`inspection_date` 为空表示**未知**，校验报告的 `unknownFields` 逐项列出（项目、阶段、字段），不改成 `否` 或 0。
- 缺值不推测、不用相邻行或旧值补；需要补充时回到原始文书。

## 人工裁决保护

- `manual_fields` 列出由人工裁决的字段，`manual_note` 写依据。报告 `protectedManualFields` 逐项列出。
- 系统规则与人工值冲突时（如人工认定的 `行案`）只给 warning 并保留人工值，不自动改写，也不报错阻断。
- 字段来源冲突的候选、权威来源和理由仍记录在 `field-resolution.json`；`unresolvedFields` 非空时 `local-verify` 拒绝形成凭证。

## 不继承的旧行为

- 不写或覆盖 Excel 工作簿；不备份/排序/复制模板行。
- 不原地重命名截图，不删除“旧命名重复图”。原图保留，命名与归档由 registry 的项目编号目录和摘要命名负责。
- 校验不修改传入对象；不再有“复查日期一律留空”的固定规则，改为必须有自身来源。

## 校验输出

`fields validate` 输出 JSON：`status`、`caseCount`、`errors`、`warnings`、`unknownFields`、`protectedManualFields`、`inputModified=false`；存在 errors 时以非零退出。
