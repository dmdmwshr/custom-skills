# 数据源：飞书统计快照

## 文件用途

R01、R02 的案卷类统计来源。快照是飞书多维表的官方只读导出（NDJSON），只做统计核对；案卷原件、ZIP、拆分 PDF 和截图保留在本地，不进入云端，也不由本 Skill 读取或上传。

## 依赖的数据源和模板

- 目录来源（不内置任何导出路径）：`--case-data <目录>` > 环境变量 `XF_FEISHU_SNAPSHOT_DIR` > 本机配置文件 `~/.xf-report-filler/feishu-snapshot.json`（`{"snapshot_dir": "<目录>"}`，位置可由 `XF_FEISHU_SNAPSHOT_CONFIG` 改）。三者都没有时 pending/final-audit 与审计输出 blocker“未配置飞书统计快照目录”并说明设置方法，不猜测目录。
- 来源与新鲜度：快照目录可带 `snapshot.info.json`（`source` 导出来源、`exportedAt` 导出时间、`completeness` 完整性说明、`refreshedBy`、`note`），会随覆盖摘要输出；缺少时明确标记“未声明”。任何一次导出都只是当时的只读副本，不是长期事实。
- 刷新：需要新数据时由使用者用 feishu-bitable 的只读导出重新生成四张表到新目录（分页取全，`has_more` 必须为 false），写好 `snapshot.info.json`，再把上述任一入口指向新目录；旧目录保留，不原地覆盖。不新增服务、第二份台账或云端附件。
- 必需文件：`cases.ndjson`（案卷，主键项目编号）、`checks.ndjson`（检查，主键检查键＝项目编号|阶段）、`products.ndjson`（产品行，主键产品键）、`monthly.ndjson`（月表，主键月份大队，含外部月报快照 `月报_*`）；每个文件可带 `<表>.manifest.json`。
- 下游文件：R02、R01；脚本 `scripts/feishu_case_source.py`，由 `monthly_bulletin_root.py` 的 pending 和 final-audit 实际入口调用。

## 数据源解析

- 统计窗口固定为上月 26 日至当月 25 日（含两端，跨年时 12 月 26 日起）；以检查日期判定，不以飞书的“统计月份/通报月份”字段代替，二者不同只记 warning。
- 复查（阶段＝复查）不计入初查类统计，单列 `rechecks_excluded`。
- 粒度分开：`initial_checks`（检查次数）、`unique_projects`（项目数）、`product_rows`（产品行）、`unqualified_*`（不合格检查/项目/产品行）。多产品检查只算一次检查。
- 微型消防站检查次数取检查记录上的“案卷是否微型消防站”：`是` 为已确认，`否` 不计，`待核实` 等进入 `micro_checks_unknown`。
- 检查日期缺失的初查记录进入 `date_unknown_initial_checks`，大队缺失记录和案卷大队缺失单列缺口；一律不计 0。
- 检查结果为空/不是“合格”“不合格”的检查记入 `result_unknown_checks`（项目 `result_unknown_projects`，产品行 `product_result_unknown_rows`）并输出 `check_result_unknown` 缺口；`unqualified_*` 因此只是已知下限，未知结果绝不当作“合格”或 0。
- 覆盖：`cases_without_checks`（无任何检查记录的案卷）、`cases_without_dated_initial`（无带日期初查的案卷）按大队和合计输出；`unresolved_initial_cases` 为该大队无法落入窗口判断的案卷数（含大队未知的案卷）。这些案卷可能属于本窗口，因此已确认数只是**下限**，绝不当作上限，也不把缺失阶段/大队/结果默认成“否”或 0。
- 文书份数不在案卷快照内，`documentCountsAvailable=false`。

## 生成规则

- 先校验再统计：缺文件、清单记录数不符、`has_more`、主键重复或为空、表角色不符、检查未关联案卷、产品未关联检查、产品键与检查键不一致，均为 `SnapshotError`，直接 blocker，不猜测补齐。
- 产品数与关联产品行数不一致、检查日期/大队/案卷大队缺失、通报月份字段与窗口不符，均输出 `case_coverage_gap` warning（含 `gap_type` 和检查键/项目编号）。
- 输出同时带每个文件的行数、清单版本和 SHA-256 以便追溯。

## 不写入内容

- 不写飞书、不写本地快照、不改任何统计表或模板。
- 不用月表的“案卷_*”“R02_*”派生列代替本 Skill 的窗口统计；它们是飞书侧参考。
- 不把上报值（`月报_*` 或统计表单元格）改成明细推导值。

## Warning/Blocker

- 快照结构错误：blocker。
- 覆盖缺口（日期、大队、标记未知，产品数不符）：warning，列出具体记录。
- 统计表上报值与快照 `月报_*` 不同：warning，以用户上报为准。

## 验收点

- 跨年窗口 12 月 26 日至 1 月 25 日及两端边界日。
- 复查被排除；多产品只算一次检查；检查、项目、产品行分别计数。
- 缺日期、缺大队、未知标记单列，不计 0。
- B/F 比较：上报值低于已确认数是可信下限冲突（`product_stats_lower_bound_conflict`）；覆盖不足（`unresolved_initial_cases>0` 或标记未知）时不低于下限的上报值一律是“覆盖不足待核”（`product_stats_coverage_pending`），不报不一致；只有覆盖完整（或带明确未知数的历史 Excel）才用上限判 `product_stats_mismatch`。10 月 8 个大队的人工上报值因此保持原值、仅待核。
- 文书份数列只给人工核对，不被代理列替代。
