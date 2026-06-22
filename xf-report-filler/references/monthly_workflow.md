# 月度产品与联网监测成绩登记工作流总览

本文是兼容入口和总览，不再承载全部细则。处理月度任务时，先读 `monthly/00_workflow_router.md`，再按目标文件或数据源加载对应对象文档。

## 工作流主线

1. 只读审计：检查通报目录、成绩月份巡查目录、数据源、模板版本和已生成文件。
2. 目录整理：按“两层月份模型”整理 `X月通报` 根层和 `上月巡查` 子目录。
3. 根层 pending：25 号前生成或刷新当月两张根层表，文件名前加 `【待补】`，表内待确认单元格红底。
4. 巡查成绩生成：从产品底册、联网基础信息截图、联网统计表生成 G01-G05 成绩材料。
5. 根层 final-audit：25 号最终数据确认后，用工作计划和产品案卷数据核对根层表，不擅自覆盖大队上报数据。
6. 模板同步：用户更新外部模板后，显式同步 skill 快照并更新哈希。

## 渐进加载入口

- 流程路由：`monthly/00_workflow_router.md`
- 目录模型：`monthly/01_directory_model.md`
- 模板策略：`monthly/02_template_strategy.md`
- 统一审计：`monthly/validation_and_audit.md`
- 机器配置：`../resources/monthly_workflow.json`

## 文件对象索引

根层当月表：

- `R01`：`monthly/output_R01_office_record.md`
- `R02`：`monthly/output_R02_product_stats.md`
成绩月份成品：

- `G01`：`monthly/output_G01_product_archives.md`
- `G02`：`monthly/output_G02_product_summary.md`
- `G03`：`monthly/output_G03_personal_stats.md`
- `G04`：`monthly/output_G04_case_scores.md`
- `G05`：`monthly/output_G05_monthly_report.md`
- `G06`：`monthly/output_G06_legacy_score_office.md`

数据源对象：

- 产品巡查底册：`monthly/source_product_register.md`
- 联网基础信息截图：`monthly/source_monitor_base_info.md`
- 联网统计表：`monthly/source_monitor_stats.md`
- 产品科技工作计划：`monthly/source_work_plan.md`
- 产品案卷数据：`monthly/source_product_case_data.md`

## 维护规则

- 新增、删除或改名目标文件时，同步更新 `resources/monthly_workflow.json`、对应对象文档和测试。
- 业务成品文件名不加序号；`Txx/Rxx/Gxx` 只用于配置、审计报告和测试定位。
- 规则变更必须落到具体文件对象或数据源对象文档，避免重新堆回本总览。
