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
6. 变更数与明细：`begin`（STARTED/RESUME/ALREADY_FINISHED；已收尾的历史运行也按证据重算，只读，不回显旧记录里已存的数，也不改写）、`status`（顶层与 `stats`）、`finish`（含 NOT_FINISHED）出口的 `changed` 与 `breakdown` 同一口径，都是证据重算的“变化的案卷/任务个数”（只有自报、没有任何凭证时不计，并以 `unverifiedReportedChanges` 标出、不算安静）；检查点自报的合计只在 `reportedChanged`，`progress` 内部状态也只带 `reportedChanged`。`breakdown` 各分项取各自凭证：采集 = 本运行 `source/r53-new-results.jsonl` 里 `NEW_PACKAGE_ATTACHED` 的案卷；归档 = 案卷水位记录里 `completedAt` 落在本运行内的案卷；云写入按（意图、表、记录、动作）计数——回执 `$NIGHT_RUN/sync/*/exec*/exec-*.json` 给出运行和记录，唯一写入账本给出每个意图及其 `final`，按逐意图证据判定，不看整批报告的 `status`（一批可以既有已核实的写入，又有被挡/失败/未知的条目）：该记录的意图在账本里 `final=OK` 才算成功；回执写着 WRITTEN 而账本是 READBACK_FAILED/VIOLATION/UNKNOWN、没有意图或没有终态的单列 `cloudUnknown`；被挡、未改变、已存在等没有写入的条目单列 `heldOrNotWritten`，不算成功也不算未知；没有账本时新建只认记录自身 `CREATED_VERIFIED`（创建时已回读），更新要整批回读通过（报告 OK），同一回执复制到别处或同一记录重复列出只算一次，不同意图写同一记录算两次；字段数取回执 `writtenFields`（真正改变的格），新建行只计行数；观察队列取当日历史（同一任务已按项目编号计过案卷的不重复）。已收尾的运行如发现统计口径有误，可 `nightly finish --refresh-stats --date <日期>` 按证据重算：只改 `changed`/`breakdown`，旧值留在 `run.json` 的 `statsRevisions`，状态、收尾时间和检查点不变。回执目录由 `begin --evidence-dir` 记入当天运行（缺省取环境变量 `NIGHT_RUN`，且须属于同一运行日期），未记录时云写入数不统计并明示。覆盖范围写在 `coverage`，不预设期望数。
7. 监督进展 `nightly progress [--runner-alive yes|no]`（只读）：`lastProgressAt` 取本运行检查点、回执目录里新写的回执/说明、云写入账本终态的最近时间；不含派发进程存活和会话自身事件（后者只作 `lastActivityAt` 展示）。`thresholdMinutes` 为最近 5 次成功（ENDED）执行的进展间隔 p95，夹在 30–60 分钟，样本不足 5 次取 30，当前执行的静默不进入计算。超过阈值只置 `needsInspection=true`，不结束、不重派、不新增状态；同日已确认失败的运行用既有 `begin`（RESUME）和 `claude-exec --resume` 续做，不按日历补跑。

## 同日手工运行（额外一轮，2026-11-19）

默认行为不变：`nightly begin` 对已 `FINISHED` 的当天只回 `ALREADY_FINISHED`。要在同一业务日再做一轮，用 `--manual-run <编号>`（1–12 位字母数字；`--date` 也可直接写 `YYYY-MM-DD@编号`）：`begin --manual-run m1 [--date 日期] --evidence-dir <独立证据目录>` 在 `运行记录/夜间运行/手工/<日>@<编号>/` 另开 `run.json`（带 `lane`：编号、业务日、对应常规运行及其收尾时间），`checkpoint/bind/bind-end/progress/status/finish/coverage/observe` 与 `feishu-mirror` 的写前写后快照用同一个 `--manual-run`（或运行键）落到这一次，当天常规运行的 `run.json`、`checkpoints.jsonl` 一字节不动。
- 开始前拦截：当天没有常规运行或还没 `FINISHED`；另有手工运行没收尾；任何运行有未记终态的绑定执行（`open_executions` 现在两种运行都列，`date` 是运行键，可直接给 `bind-end --date`）；更早的运行留有未知写入。只是没收尾的历史旧运行不拦，仍由 `earlierUnfinished`/`carryIssues` 列出。`begin`（常规与手工）在资料根 `运行记录/夜间运行/.begin.lock` 的内核文件锁内完成检查并写启动记录，进程退出自动释放（等待最多 20 秒，不手工清锁）：同时请求不同编号只有一个 `STARTED`，另一个被拒；已 `FINISHED` 的运行只读，不取锁。同编号已结束再触发返回 `ALREADY_FINISHED`（不写）；进行中则 `RESUME`，但该运行键还有未记终态的绑定执行时拒绝，先 `bind-end`/对账再续办；启动中途退出留下的无 `run.json` 空目录由下一次启动接续。`bind`、`bind-end` 和写前/写后快照登记读改写同一 `run.json`，也在同一把锁里做；同一手工运行同一时刻只有一个未记终态的执行可绑定（同会话同回执幂等；别的回执被拒，先对账并 `bind-end` 再续办绑定，常规夜的续办绑定保持原行为）。锁只在同一宿主的进程之间互斥，Windows 与 WSL 之间不互斥，所以开轮、续办、绑定都走同一个已发布 Windows CLI（夜间 `ncli` → `source/cli.sh`），不另造跨系统协调器；`sync.sh` 的写前/写后镜像带 `--manual-run`，登记进手工运行。手工 `begin` 的响应带 `earlierUnfinished`（较早未收尾的运行，沿常规 `begin` 的结构，旧文件字节不动）。
- 共享与分开：共享写入账本、`来源覆盖核对.jsonl`（手工做的覆盖核对带 `lane`，同样计入每周周期；手工运行本身不要求重做当日已验的每周核对，也不因它到期而拦收尾）、观察队列（手工的观察事件带 `lane`，当天常规的观察计数、`refresh-stats` 重算不受影响）和原始资料；云写入统计按各自的证据目录与起止时间，常规历史统计不被手工写入污染。
- 结转：手工运行收尾时的云端未知照常带进下一夜（`originDate` 是运行键）；下一次常规 `begin`/`finish` 的“上一次收尾”在常规与手工运行之间按运行键顺序取最后一个。
- 项目入口：`env.sh` 第 4 个参数给手工编号，`source/cli.sh` 对 `nightly`、`feishu-mirror` 自动补 `--manual-run` 和业务日；不给就是原来的常规路径。

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

只含 `source-facts.json` 的活动工作区目录（源事实层在案卷归档后追加产生）不算工作区，不会盖住已归档代际；案卷是否完成按已归档代际的核验判定。仍被 `scan-plan` 列出的才是真待办。

`PKG_HOLD_PACKAGE_UNKNOWN` 是原包状态不明的保护，夜间不处理、不放行。带人工保护或锁的案卷（批次保护、待整理标记）照原规则被工具挡住，记 `skipped` 并写明原因，不清锁、不人工放行。

## 观察队列与 7 天到期

- 来源里未检查、检查中、抽样待结果、复查待结果、来源缺件的任务用 `nightly observe --items <文件> --entry <入口>` 登记（`items` 为 `[{rwid,state,unitName?,brigade?,projectNo?,inspectionDate?,productEvidence?,awaitingProduct?}]`），每 7 天到期一次；等待多久都不降频。
- 队列里任何等待状态（NOT_INSPECTED、IN_PROGRESS、SAMPLE_PENDING、RECHECK_PENDING、SOURCE_GAP）转为 `ENTER_SCOPE`/`OUT_OF_SCOPE`，都要求项目编号、RWID、单位、大队四项齐全且与队列已有记录一致，检查日期落定，并有产品证据（`productEvidence: true`）；缺项留在队列并在 `history` 记 `closeBlocked`，身份冲突记 `IDENTITY_CONFLICT` 且不覆盖原值。每次观察向 `history` 追加一条（ADDED/KEPT/ENTER_SCOPE/OUT_OF_SCOPE/IDENTITY_CONFLICT）。任务只按 RWID 关联：同项目编号或同单位名但 RWID 不同的是另一项任务，不并入。
- 已有检查日期但详情尚无产品行的任务（未能确认是产品案）带 `awaitingProduct: true` 登记，继续留在队列按 7 天复看；详情出现产品行后不带该标记再登记一次即 `ENTER_SCOPE`。
- 只有来源给出检查日期（真实初查日）且不早于 2026-01-01 才 `ENTER_SCOPE`，随后按正常详情确认进入总账；早于边界记 `OUT_OF_SCOPE`；没有检查日期的只观察，不建案、不下载。
- 已入总账案卷的到期重取由 `source scan-plan` 的包决策给出，同为 7 天；文书叶集合未证完整时不自动打包，关键事实可由本案主要材料证实时，现有有效材料可以归档（大队错传、缺件按来源问题登记到唯一异常表，原包整份保留，材料完整性的实际缺口如实保留，不把材料齐全改成真）；关键事实存疑时保留原空值或合同允许的待核实值，可信事实仍先本地后飞书。

## 各阶段对应入口

| 阶段 | 入口 | 记 `done` 的条件 |
| --- | --- | --- |
| `discover` | 来源页面两类入口的可见列表 → `source scan-plan`、`nightly observe`、`nightly due`；到期时加做全范围核对并 `nightly coverage` | 分页总数与观察时间已记录；到期的核对已登记 |
| `acquire` | 详情截图与打包（叶集合证明完整才打包）→ `source` 接件 | 原包摘要与截图已入业务根 |
| `organize`/`validate` | 清点、拆分、字段校验、本地核验归档 | 指纹未变且已有完成证据的案卷记 `skipped` |
| `sync` | 依次 `feishu-source-build`（新案主行）→ `feishu-child-build`（检查与产品子行）→ `feishu-calibrate-plan`（按已核验本地清单补三表监督事实空格；云端与证据不同的只列差异，带 `--resolutions` 校准裁决文件时才按逐格裁决处理；创建时一次写入的名称类字段默认不改写；合同登记 `evidenceCorrection` 的产品名称与两表“数据来源”只能在校准入口带本地已核验证据和精确旧值更正：`--correct-facts`、`--max-corrections N`（超限即停，防批量误改），检查记录里“检查形式”“单位类别”的勾选框由 `record_forms` 解析成 `--form-evidence` 证据，详情页与电子记录勾选互相矛盾时统一以监督系统详情为准，两侧证据留在 `sourcePriority` 逐案记录，详情无值不猜、不写；手动交件没有结构化详情时可用 `--visual-evidence`（VisualDetailEvidenceV1：逐图人工视觉确认的单位类别，带截图路径与摘要，只补云端空格、不更正已有值、与详情冲突时同样以详情为准，没有详情值才补空）；`feishu-child-build` 对已有子行的案卷也会按稳定键只补缺失的产品行，已有行不动）→ `feishu-chain-plan`（同产品链补空，经 `feishu-field-plan-build`）→ `feishu-label-initial-build`（标签初值）→ `record_quantities.py scan`（受管文档环境）→ `feishu-quantity-plan` → `feishu-field-plan-build` → `feishu-period`（月度统计行与年度行，26 日结前一统计月）；每份说明用 `feishu-source-sync --execute` 写入。新案主行写入后子行的父记录才存在：`main` 固定跑两遍（先建行，再补来源已见而云端空白的基础字段，`toCreate` 与 `toFill` 都为 0 才算完），其后 `child`→`calibrate`→`chain`→`label`→`qty`→`period` 按序各一遍；`main` 第一遍新建了行、而后续规划报告里仍有因父行未建而跳过的子行或数量时，整串在同一晚幂等再跑一遍补上，没有这类跳过就不重跑。合同与路由取业务根 `运行记录/字段合同/`，用法见该目录“说明.md” | 每份说明读回一致；规划器退出 4 表示有案被保护挡住，其余照写；UNKNOWN 记 `unknown` 并只封锁该目标 |

| `mirror` | `feishu-mirror`；写入前后各一次 `--tag pre`/`--tag post --run-key <本次执行标识，缺省取当晚运行里最近绑定的执行>`（独立、不覆盖，同键复用时带回第一次读取时冻结的检查结果，不洗掉当时的问题；读不清的行使该次读取不成为之后的比较基线；写后以本次写前为基线，写前以最近确认的镜像为基线，路径与摘要记入 `run.json` 的 `mirrorSnapshots`） | 写前、写后快照与变更记录已写；变更按写入账本区分助手写入与人工修改 |
| `reconcile` | `feishu-recount --out`（含 15 项逐队逐月对账与缺项定位）；0 差异后 `feishu-period --stamp --execute` 登记回算更新时间 | 只接受退出 0 或 4，且结果可解析、四张表都读到（否则 `RECOUNT_INCOMPLETE`，不得记 done）；退出 4 记 `failed` 并保留差异文件。`productAttributionPending`（产品层待确认案卷，保留原 `productAttributionGaps`）是单独一轴，不是运行未知，也不因标签归属视为已解释。数量只对同一含义、同一单位相加；没有单位的数值只记项数，`physicalQuantity.status` 为 `NO_UNIT_BASIS` 时没有实物合计，不是 0 |
| `report` | 汇总本轮变更、缺项、未知与到期 | 无变化时安静结束 |

主行基础字段补空与其他入口同一份现行字段合同、同一摘要（2026-11-19）：`sync.sh main` 的 `--contract-v9` 取现行字段合同，创建仍用专用来源创建合同；旧合同没登记的只读公式列（统计缺项、检查方式类别等）会在补空后自然重算，被误判为“非目标字段变化”而封锁整批。现行合同里已登记为只读公式/派生的列自然重算不算违规；人工值被改、合同没登记的字段变化、目标字段读回不等于目标值仍然拦下。

整理前的初查日期与产品判定分开（2026-11-19）：`main` 先 `record_dates.py scan` 再带 `--record-dates` 建说明，只有原包确认没有电子监督检查记录才用受理日期后备，记录存在但没核实的保持待核实（见[来源字段同步](source-sync.md)“电子记录日期”）；`autoplan` 对含不合格行的案卷以 `product-judgement-visual` 单独留“待目视候选”，不再报笼统的 `record-parse`；已写入云端的后备日期在本地核验归档后由校准 `--correct-facts` 按清单日与精确旧值更正。

汇报口径见 SKILL.md “汇报口径：数量与编号分开”：数量写“N 案”，编号只作定位，每案（一案也）用列表序号，例：“发现 1 案需更正。第 1 案 新吴大队（项目编号尾号 117）”；尾号可能混淆就给完整项目编号，大队和单位继续辅助定位；每晚汇报与夜间任务书同用。

单位类别来源优先级（用户 2026-11-16 裁决，不再就此询问）：详情与初查记录勾选冲突时以监督系统详情为准并在校准报告 `sourcePriority` 逐案记录，不再列为待选差异；检查形式在“其他形式消防监督检查适用”模板下取被勾选项的来源原文（合同登记的选项才写）。

## 写入结果未知（UNKNOWN）的处理

- 官方命令行没有返回可解析的服务端回执时，该写入记 UNKNOWN 并封锁目标，错误说明里附退出码与脱敏截断后的输出头部，用来判断是网络层失败还是别的原因。
- 先用 `feishu-source-sync --reconcile` 只读对账：云端已是目标值为 APPLIED，账本据此解除；仍是旧值为 OLD_VALUE_OBSERVED，只是观察，不能证明请求不会迟到，目标继续封锁，解除需要用户亲自签署 UserRiskAcceptanceV1，助手不得自署。
- 对账后用正常入口重新规划，只写剩余差异，不重放原请求；被封锁目标在规划里列为拒绝。
- 夜间入口 `sync.sh` 写入阶段：任一说明新出现非 0 退出（新 UNKNOWN/无回执、权限或鉴权拒绝、配额、合同或身份违例等）立即停止，后续说明不再尝试，先核对回执与账本，不重放；该说明的目标由账本封锁，下一晚规划阶段起被精确列为拒绝，其余安全目标照常写。已封锁的旧目标在规划/规格生成阶段按目标精确剔除（记录自身键、同案稳定键的未决创建、未获接受的人工放行三类都在规划阶段判定，与写入闸门一致），不让一个被封锁的案连累整份说明；规划退出 4 只表示有案被挡。

## 账本与回执的分层记账

- 写入证据分三层：已核实成功（回执 OK 且账本该意图 `final=OK`，或已由对账 `APPLIED` 闭合）、已知未写（零外部调用被拒、`NOT_ATTEMPTED`、`UNCHANGED`、对账 `NOT_APPLIED_PROVEN`）、未知（回执或账本缺一边、终态非 OK）。三层分别计数，不互相冲抵；只有第一层计入变更，已知未写只闭合“零动作”，不是变更也不是未知。
- 汇总的边界：回执与账本意图只按**同一规格**对应（回执 `exec-<名>.json` 对该批 `logs*/<名>` 日志目录；单规格批次对 `logs-<名>`），相邻规格的意图不能替它作证；账本既读目标文件也读 `pending/` 暂存区；该夜的云写入和归档只算到该夜自己的收尾时间，收尾以后的手工归档、手工同步属后续工作，不并入；对账可以晚于该夜，它闭合的是该夜原来的未知（原创建、原更新按原夜计一次），对账本身不再计变更。收尾仍有云端未知时，`cloud-unknown` 事项带 `items`（原业务日 `originDate`、回执**完整路径**`receiptPath`——不同夜、不同目录可有同名 `exec-fill-01.json`——以及运行、意图 ID、稳定键），下一晚只复核这些具体项。未决跨夜持续：中间正常收尾的夜晚不会让它消失，每次 `begin`/`finish` 只重读结转项（不扫全量历史）；后到的证据（同规格回执的 `.err` 零调用、账本终态或对账）只移除对应那一项，历史原始记录字节不动；旧版记录携证不足时按其原证据目录和同一口径只读重算，不猜、不解锁。结转项的身份是（回执完整路径、原业务日、运行、表、稳定键、意图）：同一回执里不同稳定键各是一项，同一规格同一对象只留一项，持久列表不截断（展示可缩略，不影响后续动作）；同类事项已存在时，又出现的**不同对象**的未知照常算新增并通知，同一对象重复出现保持安静；同一业务日的结转项共用一次账本读取。读不出回执后的原创建经账本证明（bound 或对账 `APPLIED` 的唯一行）按原意图计一次创建行；读不出回执后的原更新同样按原意图计一次操作，但必须有字段级证明：账本里原意图记录了每个目标字段（含旧值和目标值），且写入进程自己的终态为 OK（它读回了每个目标字段），或对账读回为 `APPLIED`（每个意图字段都读到且等于目标值）；原来就等于目标值的格不算改变。字段说不清的更新（对账没读到某字段、意图里没有字段表）不计操作也不计字段，计入 `updatesWithoutFieldProof`。同一意图在可读回执里已计过的不重复，同一格（表、稳定键、字段）只算一次，同一记录的不同意图算不同操作；回执写 WRITTEN 而写入终态不是 OK、之后逐意图对账为已应用的，同样按原夜计一次。已收尾的夜用 `nightly finish --refresh-stats --date <日期>` 重算，原值留在 `statsRevisions`。
- 回执文件为空或不是 JSON（写入进程异常退出、或报错后没有输出）不再被忽略：按同目录证据归类——同目录 `.err` 写明“未发起任何外部调用”且账本没有该目录的意图为 `REFUSED_NO_CALL`（已知未写）；该目录的意图都已由终态或对账 `APPLIED/NOT_APPLIED_PROVEN` 闭合为 `RESOLVED_BY_LEDGER`；其余记为未知（`UNREADABLE_RECEIPT`），计入 `cloudUnknown`，并随 `finish` 作为 `cloud-unknown` 事项带到下一轮。`breakdown` 另给 `cloudReceiptsUnreadable`、`cloudRefusedNoCall`、`cloudResolvedByLedger`、`ledgerCrossCheck`。
- 账本追加：写意图前账本写不进（重试后仍失败）＝一个请求都没发，零外写、不留未决；服务已答复之后的 `bound/final/reconciled` 写不进账本时先落到账根 `pending/` 暂存区（计入状态、下次加锁时原样并回），绝不因此再发请求；两处都写不进才报错，并留下 `report.json`（`ABORTED`）。
- 原写入的回执与账本缺终态时，只有同一行、同一原始意图、原生请求与回执日志逐字节固定、唯一匹配且服务端历史时间吻合时，才可经 `--reconcile` 的 `requestTerminalProof`（`service-receipt-log-pair`）闭合；不重发、不新建行，重跑为零写入。

## 异常登记（唯一异常表）

- 异常表的写入沿同一底线：发送后传输未知（无服务端答复）保持未决，静默时间过去、回读仍无该行或仍是旧值都**不能**转为“未写入”（`STILL_UNKNOWN`）；只有原调用留下的服务端明确结构化拒绝（`"ok": false` 带 API 错误码）才可证明未写，或回读到唯一行且含全部写入值才证明已写（`APPLIED_PROVEN`）。同一次出现新的未知就停止后续提交；已锁定的键在规划时精确隔离，其余安全目标在下一次继续。已登记的行与既有服务端拒绝记录不重复创建、不改旧账。

- 来源或材料异常（大队错传、材料缺项）和受影响的关键事实只登记在案卷 Base 现有的“数据核对”表，不另建表、不另建案卷台账。`feishu-exception-register --registry <异常登记结构-*.json> --registry-sha … --entries <清单> --base-token … --profile … --business-root …`：缺省只出计划，`--execute` 才写；夜间用 `sync.sh exceptions <清单>`。
- 清单 `ExceptionEntriesV1`：`entries[{projectNo, type(来源包混放|来源材料缺项|关键事实待核实), affected(否|是|待核实), fields(受影响字段，无则“无”；affected 为是时必填), status(已登记保留|待核实|已解除), content(≤120字), suggestion(≤80字), source(来源详情|底册/系统|多源)}]`。
- 稳定键 `X-<项目编号>-<类型>`（写在“核对编号”）：一个对象一条当前记录，重跑无变化零写入，内容变化原行就地更新并刷新“记录时间”，旧值留在 `运行记录/异常登记账` 与飞书修订史；不以 `X-` 开头的旧核对行（C0001…）不读不写。内容含路径、文件名、证件/手机号码、证件字样或选项不合法的条目写前拒绝；同键重复行、被账本封锁的键只报告不写；写入结果未知的键封锁，读回核实前不重放。
- 来源事实取监督系统：错传附件不阻断本案按现有有效材料收口；关键事实真被影响时才把“关键信息是否受影响”记为是/待核实，主表对应格保持空白或合同允许的待核实状态，不强填。

## 触发、接管与终态视图

- `run.json` 的 `schedule` 记应触发时刻（每晚 02:00）、实际开始和迟起分钟数；定时触发迟起超过 30 分钟时 `delayCause` 初始为 `UNPROVEN`，只有 `nightly delay-cause --date <日期> --detail <原因> --ref <证据位置>` 带证据才记为 `PROVEN`（旧原因留在 `history`）；人工发起的运行没有预期时刻（`manualStart`）。旧记录没有该字段时视图按日期和首次开始推导并标 `derived`。
- `nightly timeline [--date <起始日期>]` 和 `status` 的 `view` 给出每次运行的触发、执行、接管和终态：第二次及以后的执行是接管，写明前一次的终态与说明；存储状态仍是 `RUNNING` 但所有绑定执行都已结束的运行显示为 `INTERRUPTED_UNFINISHED`（已结束的中断，不是仍在运行），有未结束执行的显示 `ACTIVE`，没有绑定执行的显示 `RUNNING_UNBOUND`。视图只读，不改写历史记录。

