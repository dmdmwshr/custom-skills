# 数据源：飞书执法监督人员名录（快照内）

## 文件用途

R02 `消防产品监督统计表` B 列括号里的“要求数”（实际开展执法的人员数，例如“（8人）”）的唯一来源，也是 R01 `产品工作实效` 里“应完成 N 起案件”的人数。它取代工作计划里的“江阴大队12人”文字、旧模板和历史成果里推导出的人数。2026-11-17 起，人数源从本地人员总表 xlsx 切换为飞书案卷库的“执法监督人员名录”快照；旧 xlsx 只在显式 `--roster` 指定时作兼容读取。

## 依赖的数据源

- 飞书统计快照目录里的 `staff_roster.ndjson` 与 `staff_roster.manifest.json`。由 `feishu_snapshot_export.py` 与案卷四表一起只读导出，配置见 `monthly_workflow.json` → `data_sources.case_snapshot.export`（`tables.staff_roster`、`roster_fields`）。
- 快照只含 人员ID、大队、有效起日、有效止日；**不含姓名**（导出时按字段投影）。
- 快照目录由 `--case-data`（`monthly_bulletin_root.py` / `monthly_workflow_audit.py`）、`--snapshot`（`monthly_run.py`、`staff_roster.py`）、环境变量 `XF_FEISHU_SNAPSHOT_DIR` 或本机配置文件指定。
- 脚本：`staff_roster.py`（`read_feishu_roster` 读取与校验，只读；`load_roster` 在显式 xlsx 与快照之间选择）。

## 数据源解析

- 统计月 M 的人数 = 该月 25 日有效的名录人数：`有效起日 ≤ 25 日 ≤ 有效止日`，止日为空表示长期有效。通报年月 Y/M 的统计月就是 `Y-M`（统计窗口上月 26 日至本月 25 日）。
- 按大队分组计数；`轨交大队` 等别称归一为 `轨道交通大队`，不在月度统计表八个大队内，只报告不写入。
- 导出行数必须等于清单记录数且无未取完分页，否则按无法读取处理。
- 2026 年初始名册（由原人员总表迁入，全年有效）：江阴12、宜兴12、梁溪8、锡山11、惠山11、滨湖8、新吴7、经开6，合计 75；轨道交通大队不纳入名录；水上大队名册待补，不补 0。

## 生成规则

- R02 B 列只刷新括号内的数字，保留单元格已有的“人”和括号外的大队人工值；空白格写“（N人）”。每次改动都在输出的 `changed` 里带 `previous` 旧值，旧值与名录不同必须显式可见。
- 外部 T09 模板、skill 内置快照和当月成果的 B 列与名录一致时保持字节不变；不一致只在审计里报告（`staff_headcount_differs`），不静默改模板。
- 历史月份只读：`monthly_workflow_audit.py --history-r02 <旧统计表>` 列出旧表 B 列与**当月**名录的差异，差异不等于历史错误。

## 不写入内容

- 不修改名录；不用名录推算其他列；不把轨道交通大队加成统计表新行。
- 不回退到工作计划、旧模板、历史成果或旧 xlsx 里的人数。

## Warning/Blocker

- 快照里没有名录文件、清单与行数不符、JSON 损坏：blocker（`staff_roster_unreadable`），不写 B 列，不兜底。
- 人员ID 重复或为空：blocker（`staff_roster_duplicate_ids`/`staff_roster_blank_id`）。
- 八个大队中某队在该统计月 25 日没有有效人员：该大队 blocker（`staff_roster_no_names`），整体不写，不写 0。
- 某行缺大队或有效期止早于起：warning（`staff_roster_row_invalid`），按无效不计。
- 名录大队不在八队之内：信息（`roster_outside_table`），只报告。
- 显式旧 xlsx 沿用原规则：缺分组、重名、标签数字与姓名数不一致等分别报 blocker/warning。

## 验收点

- `staff_roster.py --year 2026 --month 7 --snapshot <快照目录>` 输出八队人数与名录一致、`outside_table` 为空或仅轨交。
- R02 待补表 B 列括号数 = 名录人数，且不出现“（8人）（8）”重复括号。
- 报告里记录名录快照路径、SHA256 和每个 B 列改动的前后值。
