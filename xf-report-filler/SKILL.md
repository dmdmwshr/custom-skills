---
name: "xf-report-filler"
description: "根据消防产品底册、联网监测源表和月度模板，整理通报目录并生成产品/联网成绩登记材料；也保留旧版消防产品档案 `.doc` 批量填报能力。"
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# xf-report-filler

本 skill 现在分两条工作流：

1. **月度产品与联网监测成绩登记**：整理 `X月通报` 目录、校验模板版本、生成上月巡查成绩材料、处理当月根层三张通报表待补/定稿。
2. **旧版消防产品档案 `.doc` 批量填报**：从旧 `.doc` 或结构化 JSON 生成《消防产品专项监督抽查卷评查记录表》。

月度流程是当前优先维护对象。详细规则见 `references/monthly_workflow.md`；机器可读配置见 `resources/monthly_workflow.json`。

## 月度流程顺序

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

当月 25 号前，根层三张表走待补模式：

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

## 模板版本原则

- 外部模板事实源固定在 `resources/monthly_workflow.json` 的绝对路径：`E:\文件夹\1、工作\2、产品，科技，联网检测\1、产品监督\模板文件`。
- skill 内 `resources/monthly_templates` 只是快照，用于哈希校验和兜底审计。
- 外部模板存在且与 skill 快照不一致时，正式生成采用外部模板，并输出 warning。
- 外部模板缺失时，正式生成阻塞；审计和 dry-run 可以报告缺失并在允许时用 skill 快照兜底。
- 用户修改过 `模板文件\X月通报` 后，运行同步工具把外部模板快照同步回 skill：

```powershell
python scripts/sync_monthly_templates.py --dry-run
python scripts/sync_monthly_templates.py --apply
```

## 关键月度规则

- 两层月份模型：`6月通报` 是通报月份目录，根层放 6 月当月表；`6月通报\5月巡查` 是 5 月成绩月份巡查资料区。
- 业务文件命名统一为 `YYYY年M月文件名称.扩展名`，不加连接号，不额外加“工作检查”。
- `产品巡查底册（不发）` 和 `基础信息考评截图（不发）` 是数据源，不标 `【待补】`。
- 根层三张表在当月 25 号最终数据确认前必须加 `【待补】` 文件名前缀；表格内不写 `【待补】`，有数据先填，未最终确认的单元格用红色底色标记。
- 根层 `科室月考核情况记录表` 的 `产品案卷核查`、`联网系统核查` 可先从上月巡查成绩源填入；`产品工作实效` 等 25 号数据确认后定稿。
- 产品统计核对窗口固定为上月 26 日到当月 25 日。
- 根层 `消防产品监督统计表` 先于 `科室月考核情况记录表` 处理；B 列大队上报检查次数与括号要求数用于判断 `产品工作实效` 是否达标。
- `产品工作实效` 的工作计划任务按 1 月至当月累计读取，只登记黄色单元格里的红色富文本；黄色单元格中的黑字视为已完成。
- `产品案卷数据.xlsx` 统计时排除复查；B 列核对非复查项目编号去重数，C 列核对非复查且不合格项目编号去重数。统计表与案卷数据不一致只输出差异 warning，不自动覆盖也不中断生成。
- 产品底册中的空模板项如 `3、（）` + 空 `扣分：` 跳过，不阻塞。
- 产品底册括号内、`ps` 后的字段级细节只用于定位，不写入产品档案、科室表、月通报或摘要正文。
- 个人统计表人员不匹配时，不直接中断可填写部分；目标表对应大队 `AA` 列标记待核对，并在摘要列出无法填写产品分数的姓名和案卷。
- 联网备注中的“消防机构联系人”“联系人”等伪联系人只作为问题标签 warning，不写入个人执法统计表。
- 联网公开描述统一口径：CAD 类问题写“缺点位图”，PDF 类问题写“缺火灾防控图”，不在通报或科室表中写 CAD/PDF。

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

只修改源仓库 `C:\Users\12070\.cc-switch\skills\自建skills\xf-report-filler`。完成后提交并推送，再验证安装副本 `C:\Users\12070\.cc-switch\skills\xf-report-filler` 是否同步。
