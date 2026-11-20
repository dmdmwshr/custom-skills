# 月度工作流渐进加载路由

本文件用于决定本次任务要继续读取哪些对象文档。不要默认加载全部月度规则。

## 按用户任务加载

- 让较弱的模型从头到尾跑通一个月：只读 `runbook.md`，其中的命令都来自 `monthly_run.py plan`。
- 用户本月明确授权更正成品文字或扣分条款：读 `monthly_overrides.md`。

- 说明整体流程：读取 `01_directory_model.md`、`02_template_strategy.md`。
- 审计真实目录：读取 `01_directory_model.md`、`02_template_strategy.md`、`validation_and_audit.md`。
- 整理月度通报目录：读取 `01_directory_model.md`、`02_template_strategy.md`。
- 处理根层当月两张表：读取 `output_R01_office_record.md`、`output_R02_product_stats.md`，并按依赖读取 `source_work_plan.md`、`source_feishu_case_snapshot.md`、联网源表对象。
- 把本地月考核表同步到金山文档在线表：读取 `output_R01_online_sheet.md`（可选分支，先读 R01 对象文档）。
- 生成 `（X-1）月巡查` 成绩：读取 `source_product_register.md`、`source_monitor_base_info.md`、`source_monitor_stats.md`、`output_G01_product_archives.md` 到 `output_G05_monthly_report.md`。
- 仅修某个文件：读取该文件对象、它依赖的数据源对象和 `validation_and_audit.md`。
- 模板同步或模板版本问题：读取 `02_template_strategy.md`。
- 规则变更或小改动：先读取 `../skill_maintenance.md`，再读取目标文件对象、相关数据源对象和 `validation_and_audit.md`。

## 脚本到文档

- `monthly_workflow_audit.py`：目录模型、模板策略、统一校验。
- `monthly_file_organizer.py`：目录模型、模板策略。
- `monthly_bulletin_root.py`：R01-R02、工作计划、飞书执法监督人员名录（`staff_roster.py`，随快照导出）、飞书统计快照（`feishu_case_source.py`）、联网源表。
- `monthly_run.py`（统一入口 plan/run/qa）与 `feishu_snapshot_export.py`（只读导出四表）：`runbook.md`、`source_feishu_case_snapshot.md`。
- `overrides.py`（本月显式覆盖）：`monthly_overrides.md`。
- `monthly_online_sheet.py`、`ego_online_sheet.mjs`：R01 在线维护分支（plan/apply/reconcile；浏览器执行器）。
- `monthly_grade_register.py`：产品底册、联网源表、G01-G06。
- `sync_monthly_templates.py` 与 `template_resolver.py`：模板策略。

## 维护要求

修改业务规则时，先定位到对应对象文档。代码、配置、对象文档和测试必须一起更新。
