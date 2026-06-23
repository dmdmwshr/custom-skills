---
name: "xf-report-filler"
description: "根据消防产品底册、联网监测源表和月度模板，整理通报目录并生成产品/联网成绩登记材料；按需生成年度产品问题汇总；也保留旧版消防产品档案 `.doc` 批量填报能力。"
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# xf-report-filler

本 skill 分三条工作流：

1. **月度产品与联网监测成绩登记**：整理 `X月通报` 目录、校验模板版本、生成上月巡查成绩材料、处理当月根层两张通报表待补/定稿。
2. **年度产品监督底册问题汇总**：按需扫描本年各月产品巡查底册，兼容 1 月旧版产品监督网上巡查 `.doc` 源，生成年度根目录下的 `YYYY年产品监督底册问题汇总.docx`。
3. **旧版消防产品档案 `.doc` 批量填报**：从旧 `.doc` 或结构化 JSON 生成《消防产品专项监督抽查卷评查记录表》。

月度流程是当前优先维护对象。月度任务先读 `references/monthly_workflow.md`，再按任务加载 `references/monthly/00_workflow_router.md` 指定的文件对象和数据源对象文档。年度汇总任务先读 `references/annual_problem_summary/00_workflow_router.md`。机器配置分别见 `resources/monthly_workflow.json` 和 `resources/annual_problem_summary.json`。

## 渐进加载规则

- 用户问整体流程：读本文件、`references/monthly_workflow.md`、`references/monthly/00_workflow_router.md`、`references/monthly/01_directory_model.md`。
- 用户要整理目录：再读 `01_directory_model.md`、`02_template_strategy.md`。
- 用户要处理当月根层两张表：读 `output_R01_office_record.md`、`output_R02_product_stats.md`，以及它们依赖的数据源对象。
- 用户要生成上月巡查成绩：读 `source_product_register.md`、联网源表对象、`output_G01_product_archives.md` 到 `output_G05_monthly_report.md`。
- 用户要生成年度产品问题汇总：读 `references/annual_problem_summary/00_workflow_router.md`、`source_product_register.md`、`output_annual_problem_summary.md`、`validation_and_audit.md`。
- 用户要生成或修正 Word 公文类格式：读 `references/document_style.md`；年度汇总还要读年度输出对象文档。
- 用户指出某个文件不合规：只加载该目标文件对象、它依赖的数据源对象和 `validation_and_audit.md`，再改配置、脚本和测试。
- 新增或修改规则时，必须落到具体文件对象文档、对应工作流配置和测试里，不只写在聊天记录或总览文档中。

## 月度执行顺序

先只读审计，不要直接覆盖成品：

```powershell
python scripts/monthly_workflow_audit.py `
  --bulletin-dir "E:\文件夹\1、工作\2、产品，科技，联网检测\1、产品监督\26年\6月通报" `
  --bulletin-year 2026 `
  --bulletin-month 6 `
  --score-year 2026 `
  --score-month 5
```

审计确认后整理目录：

```powershell
python scripts/monthly_file_organizer.py `
  --bulletin-dir "<通报月份目录>" `
  --bulletin-year 2026 `
  --bulletin-month 6 `
  --score-year 2026 `
  --score-month 5 `
  --dry-run
```

当月 25 号前，根层两张表走待补模式：

```powershell
python scripts/monthly_bulletin_root.py `
  --bulletin-dir "<通报月份目录>" `
  --year 2026 `
  --month 6 `
  --score-dir "<通报月份目录>\5月巡查" `
  --score-year 2026 `
  --score-month 5 `
  --mode pending `
  --dry-run
```

25 号当天或之后，在用户确认大队填报数据已最终汇总后，才走定稿核对：

```powershell
python scripts/monthly_bulletin_root.py `
  --bulletin-dir "<通报月份目录>" `
  --year 2026 `
  --month 6 `
  --score-dir "<通报月份目录>\5月巡查" `
  --score-year 2026 `
  --score-month 5 `
  --mode final-audit `
  --dry-run
```

成绩月份巡查目录生成产品和联网成绩材料：

```powershell
python scripts/monthly_grade_register.py `
  --month-dir "<通报月份目录>\5月巡查" `
  --year 2026 `
  --month 5 `
  --dry-run
```

按需审计年度产品问题汇总：

```powershell
python scripts/annual_product_problem_summary.py `
  --year-root "E:\文件夹\1、工作\2、产品，科技，联网检测\1、产品监督\26年" `
  --year 2026 `
  --dry-run `
  --review-json "<审计输出.json>"
```

审计无 blocker 后生成年度汇总：

```powershell
python scripts/annual_product_problem_summary.py `
  --year-root "<年度根目录>" `
  --year 2026 `
  --apply
```

用户修改模板后同步 skill 快照：

```powershell
python scripts/sync_monthly_templates.py --dry-run
python scripts/sync_monthly_templates.py --apply
```

## 月度硬规则

- 通报月份目录和成绩月份巡查目录分开：`6月通报` 根层放 6 月当月表，`6月通报\5月巡查` 放 5 月巡查成绩源和成品。
- 外部模板事实源固定在 `resources/monthly_workflow.json` 的绝对路径；skill 内 `resources/monthly_templates` 只是快照。
- `产品巡查底册（不发）` 和 `基础信息考评截图（不发）` 是数据源，不标 `【待补】`。
- 根层两张表在当月 25 号最终数据确认前，文件名前加 `【待补】`；表格内不写 `【待补】`，待确认单元格只用红色底色标记。
- 产品底册黄色高亮问题属于私账：不进入成品，不参与扣分。
- 产品公开问题描述只写底册括号前内容；括号内、`ps` 后、字段级细节只用于内部定位。
- 联网公开描述中 CAD 类写“缺点位图”，PDF 类写“缺火灾防控图”。
- 成绩汇总表显示格式：产品分数固定 1 位小数，联网监测分数固定 2 位小数。

## 年度问题汇总硬规则

- 年度汇总是独立条线，不改变月度 R01/R02/G01-G06 生成逻辑。
- 默认扫描年度根目录下所有 `*产品巡查底册*.docx` 和 `*产品监督网上巡查.doc`；旧版网上巡查源按大队行、案卷信息行、问题行分段解析。
- 同月多源时，新版修改版底册优先，其次新版普通底册，最后旧版网上巡查 `.doc`。
- 年度汇总只生成封面和正文，不生成可见目录页；Word 左侧导航依靠“大队”一级标题和“月份”二级标题跳转。
- 年度汇总按 `references/document_style.md` 的公文格式生成：标题 2 号方正小标宋，正文 3 号方正仿宋，一级标题 3 号方正黑体，二级标题 3 号方正楷体。
- 年度汇总正文按“大队 -> 月份 -> 案卷 -> 问题”组织；同一案卷信息只出现一次，问题列在案卷下方。
- 年度汇总是内部复核材料，正文保留底册括号内细节和旧版网上巡查 `ps` 备注；`ps` 统一写成中文全角括号格式，如 `（ps：备注内容）`。
- 年度汇总正文不放图片、截图、扣分、扣分说明、分值或条款号；这些只写入 review JSON。
- 年度汇总纳入黄色高亮问题，并在输出中继续标黄；黄色只表示需人工复核，不在年度汇总中自动剔除。
- 月度产品登记公开材料仍只写括号前描述；不要把年度汇总的括号/ps 规则套到月度产品档案、通报或科室表。

## 旧版 `.doc` 填报

读取旧 `.doc` 为 JSON：

```powershell
python scripts/reader.py "<旧.doc绝对路径>" "<中间数据.json>"
```

按 `resources/template.json` 的完整结构修改 JSON 后生成：

```powershell
python scripts/writer.py `
  "resources\空表.doc" `
  "<完整数据.json>" `
  "<输出目录>"
```

批量生成时把所有案卷放进一个 JSON 数组，不要循环反复调用 writer。

## 修改本 skill

任何优化、细化、修复本 skill 前，先读 `references/skill_maintenance.md`，按变更类型选择需要加载的对象文档、配置和测试。

只修改源仓库 `C:\Users\12070\.cc-switch\skills\自建skills\xf-report-filler`。完成后提交并推送，再验证安装副本 `C:\Users\12070\.cc-switch\skills\xf-report-filler` 是否同步。
