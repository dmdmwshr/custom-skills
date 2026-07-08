# 模板策略

## 数据源解析

模板事实源来自 `resources/monthly_workflow.json` 中的绝对路径：

- `template_sources.external_root`
- `template_sources.bulletin_skeleton`
- `template_sources.score_skeleton`

skill 内 `resources/monthly_templates` 保存两类内容：外部模板快照用于哈希校验和兜底审计，产品档案与成绩总表等内置底稿用于生成成品。

## 生成规则

- 正式整理目录时使用外部模板；产品档案和产品成绩总表使用 skill 内置底稿，不要求用户放到外部模板目录。
- `模板文件\X月通报` 是唯一标准模板文件夹；根层模板命名为 `X月...`，包含科室月考核情况记录表、消防产品监督统计表和消防产品工作动态。
- `X月通报\（X-1）月巡查` 只放用户实际维护的巡查材料模板和三个标准文件夹，文件名使用 `YYYY年（X-1）月...`。
- 外部模板和 skill 快照哈希不一致时，采用外部模板并 warning。
- 用户确认同步后，`sync_monthly_templates.py --apply` 才把外部模板复制到 skill 快照并更新 manifest；内置底稿不从外部目录同步。
- 重点工作完成情况上报表已退役，T10 不再作为月度模板参与同步。
- `X月消防产品监督统计表空表.xls` 已退役，不再作为必备模板。

## 不写入内容

- 生成脚本不自动覆盖用户修改过的外部模板。
- skill 快照不是最高事实源，不能反向覆盖电脑里的模板文件。
- 联网监测明细和基础信息考评截图由人工维护，不作为模板自动生成或覆盖。

## Warning/Blocker

- 外部模板存在但快照缺失或哈希不同：warning，采用外部模板。
- 外部维护的标准模板缺失：blocker。
- 缺少内置生成底稿：blocker，应修复 skill 快照，不要求用户补到外部模板目录。
- dry-run/audit 可列出缺失项，但不猜测模板内容。

## 验收点

- `template_resolver.py` 输出外部路径、快照路径、哈希和采用路径。
- `sync_monthly_templates.py --dry-run` 只列差异，不写文件。
- `manifest.json` 与 skill 快照文件保持一致。
