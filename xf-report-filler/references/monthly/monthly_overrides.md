# 本月显式覆盖（成品更正）

用户**只对某一个月**明确授权的成品文字/条款更正放在一个覆盖文件里，用 `--overrides` 传给 `monthly_grade_register.py`、`monthly_bulletin_root.py`（或经 `monthly_run.py` 自动发现）。机制属于 skill；具体某月的更正内容是数据，不写进脚本、配置或规则文档。

## 固定位置（同月续办必须能找到）

`D:\ws\01-business\01-05-monthly-bulletin\执行记录\覆盖\YYYYMM.overrides.json`（`monthly_workflow.json` → `monthly_run`）。`monthly_run.py plan` 会自动列出并检查；同时在该月“执行记录/YYYY年M月.md”里写一行“覆盖文件：路径 + SHA256”。任务临时目录里的副本不是唯一来源。

## 文件格式（`xf-report-overrides/1`）

```json
{"schema": "xf-report-overrides/1", "year": 2026, "month": 11,
 "authorization": "谁、在哪一条用户原话里、授权了什么范围",
 "unit_name_fixes": [{"brigade": "梁溪大队", "sheet": "梁溪", "row": 1,
   "original": "源表单位全称", "corrected": "更正后全称", "context": "上下文摘录", "judgement": "为什么判定是错字"}],
 "deduction_remaps": [{"brigade": "宜兴大队", "match": "底册问题描述中的关键词", "from_index": 11, "to_index": 12,
   "value": 0.2, "authorization": "用户原话/出处"}]}
```

## 规则

- 每条必须精确命中：单位名按 大队+sheet+行+原值；条款按 大队+描述包含的关键词+原条款+分值。命中 0 条或多条都**停止**并列出，不静默跳过（说明源文件变了）。
- 只作用于内存里的成品数据；源表、底册、模板文件不改。覆盖不跨月（年月不符即拒绝），下月没有文件就没有更正。
- 单位名只更正 `单位` 字段；同一行末尾的问题描述（“缺维保记录”等）保持原样，不做全局 `缺锡→无锡`。
- 条款重映射只改落格行次，分值不变，总分不变。
- 输出里的 `month_overrides` 记录文件路径、SHA256、授权依据和每条实际命中；`monitor_unit_name_warnings` 在应用后只列仍未处理的疑似错字。
