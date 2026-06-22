# 月度工作流渐进加载路由

本文件用于决定本次任务要继续读取哪些对象文档。不要默认加载全部月度规则。

## 按用户任务加载

- 说明整体流程：读取 `01_directory_model.md`、`02_template_strategy.md`。
- 审计真实目录：读取 `01_directory_model.md`、`02_template_strategy.md`、`validation_and_audit.md`。
- 整理 `X月通报` 目录：读取 `01_directory_model.md`、`02_template_strategy.md`。
- 处理根层当月三张表：读取 `output_R01_office_record.md`、`output_R02_product_stats.md`、`output_R03_work_report.md`，并按依赖读取 `source_work_plan.md`、`source_product_case_data.md`、联网源表对象。
- 生成上月巡查成绩：读取 `source_product_register.md`、`source_monitor_base_info.md`、`source_monitor_stats.md`、`output_G01_product_archives.md` 到 `output_G05_monthly_report.md`。
- 仅修某个文件：读取该文件对象、它依赖的数据源对象和 `validation_and_audit.md`。
- 模板同步或模板版本问题：读取 `02_template_strategy.md`。

## 脚本到文档

- `monthly_workflow_audit.py`：目录模型、模板策略、统一校验。
- `monthly_file_organizer.py`：目录模型、模板策略。
- `monthly_bulletin_root.py`：R01-R03、工作计划、产品案卷数据、联网源表。
- `monthly_grade_register.py`：产品底册、联网源表、G01-G06。
- `sync_monthly_templates.py` 与 `template_resolver.py`：模板策略。

## 维护要求

修改业务规则时，先定位到对应对象文档。代码、配置、对象文档和测试必须一起更新。
