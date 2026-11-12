# 夜间运行、检查点与观察队列

定时任务与人工触发共用同一入口，记录只写业务根 `运行记录/`，命令本身不联网、不调用模型。

## 一轮的顺序

1. `nightly begin [--trigger scheduled|manual]`：按上海日期开或续当天运行。返回 `STARTED`、`RESUME`（给出下一阶段、可直接续的步骤、须先对账的未知写入、额度暂停点）或 `ALREADY_FINISHED`（不写任何文件，重复触发到此为止）；同时列出错过的日期和更早未完成的运行。
2. 按阶段执行：`discover` 来源发现 → `acquire` 详情与打包 → `organize` 清点识别整理 → `validate` 字段校验与本地核验归档 → `sync` 飞书写入 → `mirror` 大队字段镜像与快照 → `reconcile` 统计对账 → `report`。
3. 每阶段、每案用 `nightly checkpoint --stage <阶段> --status <状态> [--case <项目编号>] [--changed N] [--detail …] [--ref 回执路径]` 记录：`started`、`done`、`skipped`、`failed`、`unknown`、`quota`、`reconciled`。
4. `nightly finish`：仍有未知写入、额度暂停或未完阶段时返回 `NOT_FINISHED`，不得当作完成；全部结束且变更数为 0 时 `quiet=true`，不发任何提醒。

## 中断、额度和未知写入

- 写阶段（`acquire`、`sync`）停在 `started` 即视为未知写入：先回读来源页面/下载目录或飞书与写入账本，记 `reconciled` 后才能重新 `started`；不重放。
- 其他阶段停在 `started` 可直接续做，已 `done` 的案卷不重做。
- 模型或接口额度不足记 `quota` 并停止本轮，留下精确阶段与案卷；不换通道、不绕预算，下次触发从该点继续。
- 错过的夜晚不补跑空轮：下一次运行处理全部到期事项（`nightly due` 与 `source scan-plan` 的 `REFRESH_CANDIDATE`）。

## 观察队列与 7 天到期

- 来源里未检查、检查中、抽样待结果、复查待结果、来源缺件的任务用 `nightly observe --items <文件> --entry <入口>` 登记（`items` 为 `[{rwid,state,unitName?,brigade?,projectNo?,inspectionDate?,awaitingProduct?}]`），每 7 天到期一次；等待多久都不降频。
- 已有检查日期但详情尚无产品行的任务（未能确认是产品案）带 `awaitingProduct: true` 登记，继续留在队列按 7 天复看；详情出现产品行后不带该标记再登记一次即 `ENTER_SCOPE`。
- 只有来源给出检查日期（真实初查日）且不早于 2026-01-01 才 `ENTER_SCOPE`，随后按正常详情确认进入总账；早于边界记 `OUT_OF_SCOPE`；没有检查日期的只观察，不建案、不下载。
- 已入总账案卷的到期重取由 `source scan-plan` 的包决策给出，同为 7 天；文书叶集合未证完整时不自动打包，材料未齐不标归档完成，可信事实仍先本地后飞书。

## 各阶段对应入口

| 阶段 | 入口 | 记 `done` 的条件 |
| --- | --- | --- |
| `discover` | 来源页面可见列表 → `source scan-plan`、`nightly observe`、`nightly due` | 分页总数与观察时间已记录 |
| `acquire` | 详情截图与打包（叶集合证明完整才打包）→ `source` 接件 | 原包摘要与截图已入业务根 |
| `organize`/`validate` | 清点、拆分、字段校验、本地核验归档 | 指纹未变且已有完成证据的案卷记 `skipped` |
| `sync` | 依次 `feishu-source-build`（新案主行）→ `feishu-child-build`（检查与产品子行）→ `feishu-label-initial-build`（标签初值）→ `record_quantities.py scan`（受管文档环境）→ `feishu-quantity-plan` → `feishu-field-plan-build`；每份说明用 `feishu-source-sync --execute` 写入。合同与路由取业务根 `运行记录/字段合同/`，用法见该目录“说明.md” | 每份说明读回一致；规划器退出 4 表示有案被保护挡住，其余照写；UNKNOWN 记 `unknown` 并只封锁该目标 |
| `mirror` | `feishu-mirror` | 镜像、当日快照、变更记录已写 |
| `reconcile` | `feishu-recount --out` | 退出 0；退出 4 记 `failed` 并保留差异文件 |
| `report` | 汇总本轮变更、缺项、未知与到期 | 无变化时安静结束 |
