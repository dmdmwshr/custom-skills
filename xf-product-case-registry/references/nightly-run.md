# 夜间运行、检查点与观察队列

定时任务与人工触发共用同一入口，记录只写业务根 `运行记录/`，命令本身不联网、不调用模型。

## 一轮的顺序

1. `nightly begin [--trigger scheduled|manual]`：按上海日期开或续当天运行。返回 `STARTED`、`RESUME`（给出下一阶段、可直接续的步骤、须先对账的未知写入、额度暂停点）或 `ALREADY_FINISHED`（不写任何文件，重复触发到此为止）；同时列出错过的日期、更早未完成的运行、上次完成日期 `lastFinishedDate`、每周全范围核对是否到期 `weeklyCoverage` 和尚未记终态的执行 `openExecutions`。
2. 执行者随即 `nightly bind --session-id <会话> --receipt <回执路径> --runner-pid <进程号>`，把这一次执行登记进当天 `run.json` 的 `executions`。监督者只凭这里登记的会话和回执判断上一回合是否结束，不看其他项目的回执。
3. 按阶段执行：`discover` 来源发现 → `acquire` 详情与打包 → `organize` 清点识别整理 → `validate` 字段校验与本地核验归档 → `sync` 飞书写入 → `mirror` 大队字段镜像与快照 → `reconcile` 统计对账 → `report`。
4. 每阶段、每案、每入口用 `nightly checkpoint --stage <阶段> --status <状态> [--case <项目编号>] [--entry <入口>] [--changed N] [--detail …] [--ref 回执路径] [--issue]` 记录：`started`、`done`、`skipped`、`failed`、`unknown`、`quota`、`reconciled`。检查点身份是（阶段，案卷，入口），没有入口的旧记录照常读取；`--changed` 只存为自报的 `reportedChanged`，不是变更数。
   - **unknown 防覆盖**：效果未知的步骤只能回读对账后记 `reconciled`，不能改记 `done`/`failed`/`skipped`/`started`，也不能用 `quota` 把它的最新状态换掉（额度暂停记在别的项上）。写阶段（`acquire`、`sync`）的 `reconciled` 必须带 `--ref`（原回执）和 `--detail`（回读到的实际效果）。非写阶段已证实的来源问题可记 `skipped --issue --detail 原因` 作为显式保留事项，但不能清除云写入未知。
   - **整阶段收口**：某阶段下仍有未到终态的案卷或入口时，整阶段 `done` 被拒；有 `failed` 的子项时也被拒（要么重试成功，要么对已证实的来源问题记 `skipped --issue --detail`，要么整阶段记 `failed` 并随 `finish` 带出）；`discover` 须十个入口（五类任务 × 创建时间/检查日期）都到终态。
5. `nightly finish`：仍有未知写入、额度暂停或未完阶段时返回 `NOT_FINISHED`，不得当作完成；每周全范围核对已到期而当天没有做过时同样返回 `NOT_FINISHED`（`coverageDue`）。有失败、显式保留事项或缺入口时仍收尾为 `FINISHED`，但 `finishedWithIssues=true`、`quiet=false`，`issues` 逐项列出，下一晚 `begin` 的 `carryIssues` 带出；失败历史留在 `checkpoints.jsonl`，当前视图取各项最新状态。只有没有变化、也没有新增或加重问题时 `quiet=true`，不发任何提醒；当天核对做了但没能证明完整时可以结束，`quiet=false` 并带出原因，次晚继续到期。
6. 变更数与明细：`finish`/`status` 的 `changed` 是证据重算的“变化的案卷/任务个数”（只有自报、没有任何凭证时不计，并以 `unverifiedReportedChanges` 标出、不算安静）。`breakdown` 各分项取各自凭证：采集 = 本运行 `source/r53-new-results.jsonl` 里 `NEW_PACKAGE_ATTACHED` 的案卷；归档 = 案卷水位记录里 `completedAt` 落在本运行内的案卷；云写入按（意图、表、记录、动作）计数——回执 `$NIGHT_RUN/sync/*/exec*/exec-*.json` 给出运行和记录，唯一写入账本给出每个意图及其 `final`，回执 OK 且账本 `final=OK` 才算成功，回执写着 WRITTEN 而账本是 READBACK_FAILED/UNKNOWN 或没有终态的单列 `cloudUnknown`，同一回执复制到别处或同一记录重复列出只算一次，不同意图写同一记录算两次；字段数取回执 `writtenFields`（真正改变的格），新建行只计行数；观察队列取当日历史。回执目录由 `begin --evidence-dir` 记入当天运行（缺省取环境变量 `NIGHT_RUN`，且须属于同一运行日期），未记录时云写入数不统计并明示。覆盖范围写在 `coverage`，不预设期望数。
7. 监督进展 `nightly progress [--runner-alive yes|no]`（只读）：`lastProgressAt` 取本运行检查点、回执目录里新写的回执/说明、云写入账本终态的最近时间；不含派发进程存活和会话自身事件（后者只作 `lastActivityAt` 展示）。`thresholdMinutes` 为最近 5 次成功（ENDED）执行的进展间隔 p95，夹在 30–60 分钟，样本不足 5 次取 30，当前执行的静默不进入计算。超过阈值只置 `needsInspection=true`，不结束、不重派、不新增状态；同日已确认失败的运行用既有 `begin`（RESUME）和 `claude-exec --resume` 续做，不按日历补跑。

## 来源发现的两类入口与每周核对

- 每晚增量读两类入口，五个任务列表各读一遍：按**创建时间**取上次完成日前 3 天至今新建的任务；按**检查日期**取同一时段内发生检查的任务（创建时间放开）。后者覆盖旧任务后来才初查、补录日期和长期进行中的任务。两类结果合并后对照本地在册案卷和观察队列。
- 每 7 天做一次全范围核对：按检查日期读 2026-01-01 至当天的全部分页，逐行对照本地。结果用 `nightly coverage --report <文件>` 登记到 `运行记录/夜间运行/来源覆盖核对.jsonl`，一次一行，只追加。
- 报告格式：`{dateField:"检查日期", scopeFrom, scopeTo, entries:{<入口名>:{complete, rows, total}}, unregistered:[{rwid?, unitName, disposition: OBSERVED|REGISTERED|OUT_OF_SCOPE}], unavailable?:[…], rawRef?}`。
- 记为 `COMPLETE` 的条件：日期字段确为检查日期；范围起点不晚于 2026-01-01、终点不早于运行日前一天；五个入口都读完且行数等于页面总数；每条未在册的行都有处置。任一不满足记 `INCOMPLETE` 并写明原因。
- 到期与补做：距上次 `COMPLETE` 满 7 天即到期，从未做过也算到期。漏跑的夜晚不单独补，下一次运行仍到期；`INCOMPLETE` 不重置周期。页面的检查日期筛选不可用时如实记 `INCOMPLETE` 和限制，不用创建时间顶替。
- `nightly status` 给出 `weeklyCoverage`：上次完整日期、下次到期日、今天是否做过及结果。

## 执行绑定

- `nightly bind` 写入会话、回执路径、进程号和开始时间；同一会话同一回执重复登记返回 `ALREADY_BOUND`。
- `nightly bind-end --date <日期> --receipt <回执路径> --state ENDED|ENDED_ERROR|LOST [--detail …]` 记终态。`LOST` 用于进程已不在而回执没有写完的情况，须先人工对账。
- `nightly executions` 列出所有未记终态的执行。存在这样的执行且无法证明已结束时，不派发新回合。

## 中断、额度和未知写入

- 写阶段（`acquire`、`sync`）停在 `started` 即视为未知写入：先回读来源页面/下载目录或飞书与写入账本，记 `reconciled` 后才能重新 `started`；不重放。
- 其他阶段停在 `started` 可直接续做，已 `done` 的案卷不重做。
- 模型或接口额度不足记 `quota` 并停止本轮，留下精确阶段与案卷；不换通道、不绕预算，下次触发从该点继续。
- 错过的夜晚不补跑空轮：下一次运行处理全部到期事项（`nightly due`、到期的每周核对，以及 `source scan-plan` 的下列三类）。

## 在册案卷的到期维护

`source scan-plan --limit <足够大>` 给出在册案卷里还要做的事，夜间按下表处理；案卷是哪一批进来的不影响是否处理。

| 输出 | 含义 | 处理 |
| --- | --- | --- |
| `packageDecisions`（`REFRESH_CANDIDATE`） | 已有原包、满 7 天需回看 | 重新读详情，叶集合证明完整才重取 |
| `packageAcquireFirst`（`PKG_ACQUIRE_FIRST`） | 在册但还没有原包 | 按新案同样的详情核对和打包规则取包 |
| `pending` 的 `COLLECT`、`CLASSIFY`、`LOCAL_VERIFY` | 待取材料、待确认初查结果、待本地核验归档 | 分别走采集、详情确认、核验归档 |

`PKG_HOLD_PACKAGE_UNKNOWN` 是原包状态不明的保护，夜间不处理、不放行。带人工保护或锁的案卷（批次保护、待整理标记）照原规则被工具挡住，记 `skipped` 并写明原因，不清锁、不人工放行。

## 观察队列与 7 天到期

- 来源里未检查、检查中、抽样待结果、复查待结果、来源缺件的任务用 `nightly observe --items <文件> --entry <入口>` 登记（`items` 为 `[{rwid,state,unitName?,brigade?,projectNo?,inspectionDate?,productEvidence?,awaitingProduct?}]`），每 7 天到期一次；等待多久都不降频。
- 队列里任何等待状态（NOT_INSPECTED、IN_PROGRESS、SAMPLE_PENDING、RECHECK_PENDING、SOURCE_GAP）转为 `ENTER_SCOPE`/`OUT_OF_SCOPE`，都要求项目编号、RWID、单位、大队四项齐全且与队列已有记录一致，检查日期落定，并有产品证据（`productEvidence: true`）；缺项留在队列并在 `history` 记 `closeBlocked`，身份冲突记 `IDENTITY_CONFLICT` 且不覆盖原值。每次观察向 `history` 追加一条（ADDED/KEPT/ENTER_SCOPE/OUT_OF_SCOPE/IDENTITY_CONFLICT）。任务只按 RWID 关联：同项目编号或同单位名但 RWID 不同的是另一项任务，不并入。
- 已有检查日期但详情尚无产品行的任务（未能确认是产品案）带 `awaitingProduct: true` 登记，继续留在队列按 7 天复看；详情出现产品行后不带该标记再登记一次即 `ENTER_SCOPE`。
- 只有来源给出检查日期（真实初查日）且不早于 2026-01-01 才 `ENTER_SCOPE`，随后按正常详情确认进入总账；早于边界记 `OUT_OF_SCOPE`；没有检查日期的只观察，不建案、不下载。
- 已入总账案卷的到期重取由 `source scan-plan` 的包决策给出，同为 7 天；文书叶集合未证完整时不自动打包，材料未齐不标归档完成，可信事实仍先本地后飞书。

## 各阶段对应入口

| 阶段 | 入口 | 记 `done` 的条件 |
| --- | --- | --- |
| `discover` | 来源页面两类入口的可见列表 → `source scan-plan`、`nightly observe`、`nightly due`；到期时加做全范围核对并 `nightly coverage` | 分页总数与观察时间已记录；到期的核对已登记 |
| `acquire` | 详情截图与打包（叶集合证明完整才打包）→ `source` 接件 | 原包摘要与截图已入业务根 |
| `organize`/`validate` | 清点、拆分、字段校验、本地核验归档 | 指纹未变且已有完成证据的案卷记 `skipped` |
| `sync` | 依次 `feishu-source-build`（新案主行）→ `feishu-child-build`（检查与产品子行）→ `feishu-calibrate-plan`（按已核验本地清单补三表监督事实空格；云端与证据不同的只列差异，带 `--resolutions` 校准裁决文件时才按逐格裁决处理；创建时一次写入的名称类字段默认不改写；合同登记 `evidenceCorrection` 的产品名称与两表“数据来源”只能在校准入口带本地已核验证据和精确旧值更正：`--correct-facts`、`--max-corrections N`（超限即停，防批量误改），检查记录里“检查形式”“单位类别”的勾选框由 `record_forms` 解析成 `--form-evidence` 证据，详情页与电子记录勾选互相矛盾时不写、只列差异；手动交件没有结构化详情时可用 `--visual-evidence`（VisualDetailEvidenceV1：逐图人工视觉确认的单位类别，带截图路径与摘要，只补云端空格、不更正已有值、与记录勾选矛盾时同样只列差异）；`feishu-child-build` 对已有子行的案卷也会按稳定键只补缺失的产品行，已有行不动）→ `feishu-label-initial-build`（标签初值）→ `record_quantities.py scan`（受管文档环境）→ `feishu-quantity-plan` → `feishu-field-plan-build`；每份说明用 `feishu-source-sync --execute` 写入。新案主行写入后子行的父记录才存在，所以整串在同一晚跑两遍：第二遍补上第一遍因父行未建而跳过的子行和数量。合同与路由取业务根 `运行记录/字段合同/`，用法见该目录“说明.md” | 每份说明读回一致；规划器退出 4 表示有案被保护挡住，其余照写；UNKNOWN 记 `unknown` 并只封锁该目标 |

单位类别来源优先级：详情与初查记录勾选冲突时以监督系统详情为准并在校准报告 `sourcePriority` 逐案记录，不再列为待选差异；检查形式在“其他形式消防监督检查适用”模板下取被勾选项的来源原文（合同登记的选项才写）。
| `mirror` | `feishu-mirror`；写入前后各一次 `--tag pre`/`--tag post --run-key <本次执行标识，缺省取当晚运行里最近绑定的执行>`（独立、不覆盖，同键复用时带回第一次读取时冻结的检查结果，不洗掉当时的问题；读不清的行使该次读取不成为之后的比较基线；写后以本次写前为基线，写前以最近确认的镜像为基线，路径与摘要记入 `run.json` 的 `mirrorSnapshots`） | 写前、写后快照与变更记录已写；变更按写入账本区分助手写入与人工修改 |
| `reconcile` | `feishu-recount --out` | 只接受退出 0 或 4，且结果可解析、四张表都读到（否则 `RECOUNT_INCOMPLETE`，不得记 done）；退出 4 记 `failed` 并保留差异文件。`productAttributionPending`（产品层待确认案卷，保留原 `productAttributionGaps`）是单独一轴，不是运行未知，也不因标签归属视为已解释。数量只对同一含义、同一单位相加；没有单位的数值只记项数，`physicalQuantity.status` 为 `NO_UNIT_BASIS` 时没有实物合计，不是 0 |
| `report` | 汇总本轮变更、缺项、未知与到期 | 无变化时安静结束 |

## 写入结果未知（UNKNOWN）的处理

- 官方命令行没有返回可解析的服务端回执时，该写入记 UNKNOWN 并封锁目标，错误说明里附退出码与脱敏截断后的输出头部，用来判断是网络层失败还是别的原因。
- 先用 `feishu-source-sync --reconcile` 只读对账：云端已是目标值为 APPLIED，账本据此解除；仍是旧值为 OLD_VALUE_OBSERVED，只是观察，不能证明请求不会迟到，目标继续封锁，解除需要用户亲自签署 UserRiskAcceptanceV1，助手不得自署。
- 对账后用正常入口重新规划，只写剩余差异，不重放原请求；被封锁目标在规划里列为拒绝。
- 夜间入口 `sync.sh` 写入阶段：某个说明未成功只封锁它自己的目标，同一步其余说明继续执行，累计 2 个未成功即停（疑似系统性故障），步骤末尾只要有未成功就退出 1，不静默吞掉；被封锁的目标下一晚在规划里直接列为拒绝，不再尝试。
