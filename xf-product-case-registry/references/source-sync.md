# 来源字段同步到飞书（feishu-source-sync，v3）

把 0104 监督系统来源事实，按 **fieldId + record_id 双白名单** 增量写入飞书案卷库。只走官方 `lark-cli base +record-get / +record-upsert`，沿用已有授权的 profile，不读取或保存凭据。

## 三种模式

| 模式 | spec.mode + 命令行 | 行为 |
| --- | --- | --- |
| plan | `plan`（缺省） | 只做本地校验，输出将发送的线上值（wire）和账本状态；外部调用 0，不写账本 |
| execute | `execute` **且** `--execute` | 账本检查与目标锁 → 严格身份回读 → 稳定键/旧值核对 → **意图先落账** → 逐条 upsert → 写后回读与保护断言 |
| reconcile | `reconcile` **且** `--reconcile` | 只读云回读，把早先未决写入与账本意图对账；永不写飞书 |

没有“全量”模式；allowlist 为空、缺项或无法确认即拒绝，不回落到任何全量数据。`execute` 与 `reconcile` 必须显式给出 `--ledger-dir`（没有默认账本，避免写到别处绕过封锁）；`plan` 可省略，只读。

```
python scripts/registry_cli.py feishu-source-sync --spec spec.json [--execute | --reconcile] --ledger-dir DIR   # 正常入口
python scripts/feishu_source_sync.py --spec spec.json [--execute | --reconcile] --ledger-dir DIR            # 同代码的独立入口
```

退出码：0 成功；2 在任何外部调用之前被拒绝；3 失败或结果未知（已保留回读证据，未重试，目标已封锁）。

## spec（FeishuSourceSyncSpecV2）

必填：`contract{path,sha256}`、`target{baseToken,profile,identity}`（Base 必须等于合同 baseId）、`allowlist{tableId:{records[],fields[]}}`、`updates[]`、`limits.maxRecords`（1..20）、`logDir`（execute/reconcile 必填，且必须是**不存在或为空**的目录，旧结果不会被覆盖）。

`updates[]` 每项：`tableId`、`recordId`、`stableKey{fieldId,value}`、`fields{fieldId:{new?, basis, expectedOld?}}`。

### 来源值状态（`basis.valueState`）

| valueState | 含义 | 行为 |
| --- | --- | --- |
| `observed` | 已观察到的来源事实 | 必须带 `new`，按字段类型校验后写入 |
| `not-provided` | 来源页面未提供该值 | 不得带 `new`；**跳过**，不覆盖已有值，报告中记 skippedFields |
| `unknown` | 来源状态未知 | 同上，跳过 |

`basis` 其余必填：`kind: "source-observed"`、`sourceRef`、`observedAt`。来源页面上的字面“待核实”等是合法来源取值，按 `observed` 写入，程序不做关键词禁用。“否 / 0”这类否定值需要 `basis.explicitNegative: true`，表示来源明确否定；缺失值绝不补写成否或 0。本地处理状态不能冒充监督源状态或承办人。

### 字段类型与线上值

按保护合同里字段的真实类型序列化，并按同一口径比较旧值与新值：

| 合同类型 | spec 里的 `new` | 发给官方 CLI 的值 | 读回比较 |
| --- | --- | --- | --- |
| text | 非空字符串 | `"x"` | 字符串相等 |
| select | 单个非空选项字符串 | `["x"]`（官方 help 要求数组） | 单元素数组与字符串等价 |
| number | 有限数字（不接受布尔/字符串） | `12.5` | 数值相等 |
| datetime | `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`，按 Asia/Shanghai | `"YYYY-MM-DD HH:MM"` | 云端 ISO 串转 +08:00 后相等 |

其他类型（link、公式、附件等）、列表值、含糊形态一律拒绝。select 的选项是否存在于云端字段定义不在本版校验范围内。

## 允许与永久禁止

- 只允许合同中 `class = source-sync-whitelist` 且为上述标量类型的字段，并且必须出现在显式 fieldId 白名单里。
- 字段名、未知字段、新增字段、`derived-readonly`、`admin-only`、`legacy-hidden-protected`、`structural-relation`、link 字段一律拒绝。新增的人工字段在合同更新前同样默认不写。
- 以下 fieldId 无论合同如何分类都永久禁写：案卷 `fld4mVYy5Y` `fldvHLpSKI` `fld5E2njAU` `fldrKL9rDE` `fldW2kJC0V` `fldoFEnhmd`；检查 `fld4atEDRT` `fldYkXBvEt`；产品 `fldwsnqeJ4` `fld1Plox8P` `fldVZE4Yc9`（网售标记案卷）。
- 稳定键（项目编号 `fld1lHXHpk` / 检查键 `fld5ujrPv4` / 产品键 `fldqlwHjUW`）只用于核对目标，不作为更新目标。
- 本版不创建、不删除记录，不改 schema、角色权限，不触维修库。

## 记录身份与完整回读

回读只认服务回执：必须有 `record_id_list`、`field_id_list`，行数与行宽一致，ID 不重复、不含未请求的 ID、不缺请求的 ID。任何缺失或异常都在写入前终止；程序**不会**按请求顺序补 ID。结果按 fieldId 建索引，与响应顺序无关。

稳定键字段和**每个请求写入的目标字段**必须出现在写前回读里（否则旧值不可比，未写入即拒绝），也必须出现在写后回读里并与目标值相等；缺失或无法解读都不能通过，也不会被报成 OK。

## 未知结果保护（账本）

账本是追加式 JSONL，按精确目标（base + table + record）一个文件，位置由必填的 `--ledger-dir` 决定（execute/reconcile 无默认），**与 `logDir` 无关**；P18 全程固定同一目录。

- 每个目标先写 `intent`（含稳定键、各字段旧值与线上值、runId、操作者、logDir），再发请求，最后写 `final`。
- `final` 为 OK / UNCHANGED / NOT_ATTEMPTED 才算清除；FAILED / UNKNOWN / VIOLATION / READBACK_FAILED，或有意图无终态（进程中断），都会**封锁该目标**——不论 spec、值、logDir 是否相同，execute 一律在外部调用之前拒绝。
- 每个目标有独占锁文件；并发运行拿不到锁即拒绝，遗留锁不自动清除（人工核实后删除）。
- **对账入口**：`mode: reconcile` + `--reconcile`，用同一 spec 指明目标，只读回读并与账本意图比较。先核业务身份：账本意图里的稳定键必须同时等于 spec 里的稳定键和当前回读的稳定键，否则 `IDENTITY_MISMATCH` / `IDENTITY_UNREADABLE`，继续封锁（含糊、缺失、变化都不猜配，原意图与当前值一并记录）。身份一致后：
  - `APPLIED`：稳定键一致且每个意图字段都已是目标值，才清除封锁，不重放；
  - `OLD_VALUE_OBSERVED`：读到的是旧值。这只说明观察时刻的值，**不能证明超时请求没有执行或不会迟到**，所以**不解除封锁**，只追加观察记录；之后若再对账读到 `APPLIED` 才清除，同时不会有第二次写入；
  - `DIVERGED` / `UNREADABLE`：继续封锁。
- **显式人工放行**（唯一的非 APPLIED 解除方式）：在 reconcile spec 里加 `humanRelease{reason, operator, acknowledgesLateWriteRisk:true, evidence[]}`。`evidence` 至少一条，`kind` 只能是 request-terminal-proof / service-side-record / cli-process-log / human-decision-record，`ref` 必填；旧值相等不是依据，会被拒绝。账本追加 `human_release` 事件，保留原因、操作者、依据和原意图，不读云。
- 失败/未知的原始 `report.json`、`before.json`、`requests.jsonl` 永不被覆盖。

## 写后断言

写后全量回读：目标字段等于目标值；同一记录的其他非派生字段（含人工字段与关系）与写前一致；违反即 FAILED 并封锁。写失败或超时立即停止，剩余记录标 `NOT_ATTEMPTED`，不重试。

`logDir`：`before.json`、`requests.jsonl`、`responses.jsonl`、`after.json`、`report.json`（字段级来源依据、稳定键、实际目标、新旧值、线上值与每条回执、ledgerDir、runId、操作者）。

## 验证边界与未覆盖

`scripts/test_feishu_source_sync.py` 让**真实 `LarkCliTransport`** 跑在假服务之后；假服务严格校验 CellValue 线上形状（select 必须是数组）且从不纠错，因此格式缺陷不会被掩盖。这是离线机械测试，**不是 P18 真实云结果**。

未覆盖：真实云对 select/datetime 写入的读回形态；select 选项存在性；账本被换路径或跨主机/跨用户运行时的互斥（账本是每主机每用户的；P18 应固定同一主机和同一 `--ledger-dir`）；迟到写入只能靠“封锁到 APPLIED 或有依据的人工放行”应对，程序无法证明请求已被服务端终止；最终人工字段合同（新产品人工 flag 的 fieldId 尚未给出，合同里一旦标为 human 即默认拒写）；没有来源明确否定的真实样本，“否/0”机制只做合成验证。合同变化须另版冻结并重新计算摘要。

## V3 create（FeishuSourceSyncSpecV3，仅创建、不更新）

- 入口仍是 `registry_cli.py feishu-source-sync --spec`；`schemaVersion=FeishuSourceSyncSpecV3` 走 create 分支，V2 更新/对账不变。合同用固定 SHA 的 V3 复合文件（完整 v9 `baseContract` + `extension.createFieldsCandidate`）。
- 只允许合同候选中 `proposedCreateClass ∈ {create-once-identity, create-once-source-scalar, create-once-routing, create-once-parent-link}` 且 `candidateState` 不以 `blocked` 开头的字段；人工/派生/反向关系/未知字段一律拒绝；子行（检查/产品）在合同仍 blocked 时整份 spec 拒绝。
- 每个案卷创建必须有冻结路由（来源管辖原文→所属大队选项+归属大队底册记录，同队）；路由无匹配只阻该实体，不按单位名/项目号前缀猜。
- 顺序：案卷→检查→产品；子行只设置子端父关联（`create-once-parent-link`），不同时写反向关系。
- 每个创建：fresh 全页稳定键扫描 0 匹配才创建（1 匹配=ALREADY_PRESENT，零新增；>1=停止）；写前先落创建意图（账本键由 base/table/稳定键派生，同一账本目录）；回执 record_id 缺失=UNKNOWN；写后按返回 recordId 回读键/全部字段/父链并再扫键唯一。
- UNKNOWN：同稳定键封锁；对账 0 匹配仍封锁（不证明迟到不会发生）；1 匹配需 `requestTerminalProof` 才 APPLIED；>1 阻断；人工放行沿既有 `humanRelease`。
- 创建不是更新授权：`feishu_source_sync` 对已有行不改、不重挂、不重编号。


## V3 因果门禁(r19b)

- `requestTerminalProof` 只接受 `kind=service-receipt-log` 引用：logDir 必须等于原意图的 logDir，runId/intentId 与原意图一致，requests/responses 摘要钉住，原请求(表/稳定键/全部字段载荷)与意图相等，服务回执须恰好一条、成功、其唯一合法 id 等于当前唯一匹配(两条同 id 也不算)。布尔值、自由文本、不存在/不相关/不匹配的引用一律不清除；无回执的超时同样不能清除。
- `humanRelease` 必须带精确 `intentId`，只放行该意图。
- 既有同键记录(ALREADY_PRESENT)只是候选：身份/路由/父链字段须与当前意图一致才记 `ALREADY_PRESENT_VERIFIED`(不声称由本次载荷创建)；仅来源标量字段不同记 `EXISTING_NEEDS_V2_UPDATE`(走 V2 更新契约)；身份字段不符记 `EXISTING_DIVERGED`：只挡该精确目标及其依赖子层(子层记 BLOCKED_ENTITY，拿不到该 id)，不写账本封锁、不停止独立对象；报告状态 `PARTIAL` 并列 `blockedEntities`。
- 创建回执无唯一合法 record_id 记 UNKNOWN 并封锁，不回读 None。


## 基础字段说明的正常生成入口（feishu-source-build）

```
python scripts/registry_cli.py feishu-source-build --capture 采集批次.json --capture-sha … --waterline 案卷水位记录.json \
  --contract-v3 V3复合合同.json --contract-v3-sha … --contract-v9 v9合同.json --contract-v9-sha … \
  --routes 八队精确路由.json --routes-sha … --base-token … --profile … --out-dir 说明目录 --log-root 日志根
```

- 只读云：fresh 全页读取案卷稳定键与所用字段、八队底册；不写云、不写账本。输入摘要与固定值不符即拒绝。
- 权威来源是已存的采集详情（`collectionDetail`）。每个值都绑到同一案：项目编号、URL 中的 RWID、列表单位与大队必须与详情一致，错案、错 RWID、缺字段只阻该实体；字段依据 `sourceRef` 写明采集批次文件名与摘要、字段原文位置、URL、项目编号、取得时点。
- 创建说明（V3）：产品监督案(`C` 号)、fresh 云端没有该项目号者；范围按真实初查日期（>= 2026-01-01）而非号段年份：旧号段案只有在总账已有该案时才生成，没有总账又没有“检查日期”来源证据的旧号段案进 `blockedEntities`（不静默丢弃也不新建）；已被 `scope-exclude` 剔除的案卷不再建行。字段限项目编号、单位名称、单位地址(来源有值才带)、所属大队/归属大队(由 fresh 底册核过的精确管辖路由)、初查日期(仅当总账分类带路径+摘要+时点证据；没有就省略；电子监督检查记录日期优先，其缺失/不可用/无法确认时按 2026-11-09 用户授权以详情“受理日期”后备，内部保取值位置、原值、摘要与后备原因，对外来源统一为“监督系统”，证书/报告/复查日期不得顶替)。路由合同外的管辖只阻该案，不猜队。
- 初查结果与初查日期来源（2026-11-09 r36）：build 会按已核分类证据补 `初查结果`（合格/不合格/待结果/待核实，缺证据一律待核实）与 `初查日期来源`（监督系统），仅限最新合同暴露且选项含该值的字段；已是终局值的行不覆盖，待结果/待核实且来源已有终局值时带 `expectedOld` 升级。报告列出 `initialDateAcceptanceFallbacks`、`initialDateConflicts`、`lifecycleFieldsSkipped`。
- V2 更新（`FeishuSourceSyncSpecV2`）的单选值（r37）：字段在最新合同中列出选项时，规划与执行都要求新值是其中之一，合同外的值在任何 transport 调用及业务/账写入之前拒绝（零读零写）；合同未列选项的旧合同无法据以校验，保持原行为。`expectedOld` 旧值防覆盖、错表字段、合同摘要与人工/派生/NEVER 拒写不变。
- 补空说明（V2）：云端已有该案且某字段为空、来源有值时，生成 `expectedOld: null` 的更新；云端非空且与来源不同的只列入 `differing`，不覆盖。已相等不生成。
- 每份说明最多 20 条(引擎硬上限)，各自新 `logDir`。`expectedOld: null` 表示执行时该格必须仍为空，否则整份拒绝(写前，零写)。
- 退出码：0 全部可同步；4 PARTIAL(有被挡实体或路由问题，其余说明已生成)；2 拒绝；3 云读失败。`feishu-source-sync` 同样以 4 报告 PARTIAL，不能把 exit 0 当整体完成。

## 人工放行防误用（正常入口检查；不证明真实授权）

- `humanRelease` 不是实施者的工具：助手（含原实施者）不得构造、冒签或调用；通用实施授权、助手签名、`acknowledgesLateWriteRisk: true`、重复 ZERO_MATCH、稳定键当前存在，都不替代用户对该精确 UNKNOWN 请求迟到写风险的明确接受。
- （防误用，非授权证明）正常入口（`feishu-source-sync` / `feishu_source_sync.py`）在 spec 带 `humanRelease` 时必须同时给 `--user-risk-acceptance <UserRiskAcceptanceV1.json>`：`{"schemaVersion":"UserRiskAcceptanceV1","acceptances":[{"acceptedBy":"user","intentId":…,"userQuote":"用户原话","sourceRef":…,"acceptedAt":…}]}`，且 `intentId` 与放行意图精确相同；缺失即在任何外部调用前拒绝。
- `feishu-source-build --ledger-dir …`：账本里出现过未获用户接受的 `human_release` 的目标保留未决，既不生成创建也不生成补空说明，并列入 `blockedEntities`（构建退出码 4）；历史事件不删、不改。用户接受后以 `--user-accepted-releases` 传入。
- 未决 UNKNOWN 的稳定键封锁不因 spec/字段/顺序变化、新 intent 或进程重启而绕过；重复零匹配仍 BLOCKED。
- 补空说明（`expectedOld: null`）是一次性的：已执行后原样重跑会在写前拒绝(`REFUSED_BEFORE_WRITE`，零写、账本不封锁)，因为该格已非空；正确的幂等入口是重新运行 `feishu-source-build`，相等的字段不再生成说明。创建说明原样重跑则是 `ALREADY_PRESENT_VERIFIED`，零写。

## r27 补充：账根、必须带账、局部阻断、精确意图

- **账根只能是既有唯一账**：`feishu-source-sync` 的 execute/reconcile 和 `feishu-source-build` 在账根不存在、或缺 `targets/` 时直接拒绝，**不会**新建账根（错根会读出“空账”而绕过封锁）。隔离测试须显式 `Ledger.initialize()`；正式运行沿原 P18 账，不迁移、不另建。
- **生成说明必须带账**：`feishu-source-build --ledger-dir` 必填，缺账不能默默生成已保留目标的说明。
- **普通未决也局部阻断**：账里该项目创建键(`create:<项目号>`)或其记录更新键仍是 BLOCKED(UNKNOWN/PENDING 等)时，该项目既不生成创建也不生成补空，列入 `blockedEntities`(PARTIAL，退出码 4)，独立项目继续；晚到的行出现、字段/证据/顺序变化、新 intent 都不改变这一点。V2 execute 同样拒绝同案稳定键创建仍未决的补空。
- **更新对账的放行只对精确意图**：V2 `humanRelease` 同样必须带 `intentId`，对账只放行与该目标当前未决意图完全相同者，其余目标保持封锁(`RELEASE_NOT_FOR_THIS_INTENT`)。
- **UserRiskAcceptanceV1 只是材料结构校验**：只检查必填字段和 intentId 匹配，**不能证明真实的人类授权**；真实例外仍须可核的用户原话出处。自动编排不得自造、传入或调用任何 humanRelease。

## r27 补充：唯一正式账根身份（不只是目录结构）

- 正常入口（CLI 与函数默认）只认唯一正式 P18 账根 `P18_LEDGER_ROOT`（`D:/ws/05-temp/20261104_飞书大队权限/P18运行账本/实施`，Windows 与 WSL 写法规范化后相同）：路径身份必须相同、目录已有历史事件；校验只读，不写。结构合法但另建的“空账”（哪怕含 `targets/`、`locks/`）不是正式账，`feishu-source-sync`（execute/reconcile）与 `feishu-source-build` 都在任何云调用、账本/日志/输出目录写入之前拒绝。
- 隔离测试账仅在代码内部以 `isolated_test_ledger=True` 构造、根里有 `Ledger.initialize()` 写下的标记、且不是正式根时可用；该开关不在任何命令行选项里，`initialize()` 对正式根永远拒绝。
- 变更 `P18_LEDGER_ROOT` 是源码变更(须评审)，不是运行时参数。
- `UserRiskAcceptanceV1` 的校验仍只是材料结构及精确意图检查，不证明真实人类授权；本任务未获此项人类风险接受授权。


## 范围剔除（feishu-scope-retire，2026-11-11）

`feishu-source-sync` 永远不删记录。案卷被 `ledger scope-exclude` 移出活动集合后，云端对应行由独立入口 `registry_cli.py feishu-scope-retire --plan 计划.json [--execute --ledger-dir 唯一P18账 --log-dir 新目录]` 删除：

- 计划（`FeishuScopeRetirePlanV1`）列出 baseToken、三张表的 tableId 与稳定键/父子关联字段 ID，并绑定一份**全字段快照**（案卷行、检查行、产品行的 record_id 与全部字段值，是恢复来源；摘要不符即拒绝）。
- 删除前 fresh 全页读：每个案卷行的稳定键必须等于计划项目编号，其云端检查行、产品行必须与快照逐 ID 相等（多一行或少一行都拒绝，任何检查键/产品键不属于该案也拒绝），没有子行被两个案共用；任何一项不符，零删除。
- 缺省只核对（`PLAN_ONLY`）；`--execute` 才删，且必须带唯一正式 P18 账：每个目标先落 `intent`，按产品→检查→案卷顺序各一次请求，立即全页读回目标已不在才记 `final OK`；读不回或超时记 UNKNOWN 并封锁该目标，不重放；全部完成后再做终读核对行数（各表 = 删前 − 计划数）。
- 恢复：按快照用 `+record-upsert`（不带 record_id）依次重建案卷行→检查行（父关联=新案卷行）→产品行（父关联=新检查行），公式/查找/汇总字段由云端重算，人工字段按快照原值回填；record_id 会变，旧 ID 留在快照里。

## 初查日期口径下的新增能力（2026-11-11）

- **本案已核验清单日**：`feishu-source-build` 在总账没有可证的页面检查日时，先取本案最近一次本地核验通过、清单摘要与总账完成记录相符的 `manifest.json` 里的 `initialInspection.inspectionDate`（本案监督检查记录文书所载监督检查时间）作为初查日，依据写清单路径与摘要；仍找不到才退回受理日期后备。云端非空且不同的值仍只报告、不覆盖。
- **有据日期更正** `feishu-date-correct-build --plan 计划.json --plan-sha …`（只读云）：计划逐项给出 `projectNo / recordId / expectedOld / newDate`（可选成对的 `sourceOld/sourceNew` 同步更正“初查日期来源”）。生成前逐项核对：新日等于本案已核验清单日、不早于范围起点、fresh 云端键唯一命中该 recordId 且当前值正是 expectedOld；任一不符整条拒绝（含人工改动）。输出 V2 说明（allowlist 只含日期/来源字段），执行仍由 `feishu-source-sync --execute` 走 P18 账、写前旧值核对与写后读回。
- **水上大队等不在八队名录的管辖**：路由文件可加 `{"sourceJurisdictionExact": …, "adminOnly": true, "sourceOptionExact": "大队来源选项原文"}`。创建时不写所属大队/归属大队（留空，仅管理员可见，与既有水上案同一做法）；“大队来源”仅在最新合同允许创建且选项存在时才写，合同把它列为只读时留空，不为此改合同。八队路由的“所属大队+归属大队同队”规则不放宽。


## 未决写入的严格技术恢复与字段计划（2026-11-12）

- **UNKNOWN 更新意图的技术恢复**：`feishu-source-sync --reconcile` 的 V2 reconcile spec 可带 `"technicalRecovery": {"kind": "history-quiet-readback", "intentId": "<精确意图>", "minQuietSeconds": ≥3600}`（不得与 `humanRelease` 同用）。只有同时满足才在账本追加 `reconciled / NOT_APPLIED_PROVEN` 并解封：该意图恰有一条 `UNKNOWN` 终态（进程已结束并记账；无终态的 PENDING 不可恢复）；身份核对后的读回仍是全部旧值；服务端 `+record-history-list` 在意图时间之后没有涉及该意图字段（或字段不明）的修订；距终态已过静默期。证据不足记 `RECOVERY_REFUSED` 并保持封锁。原 `UNKNOWN` 终态与历史对账事件原样保留，不伪造 humanRelease，不改账本旧事件。恢复后的更正仍走普通 `execute`（旧值门禁、写后读回）。
- **无回执日志的未决创建**：V3 `reconcile` 的 `requestTerminalProof` 可用 `{"kind": "record-history-creation", "intentId": …}`：唯一稳定键匹配、字段与意图逐项相等，且该行**首个服务端修订**创建于原意图时间窗内（意图前 5 秒～终态后 120 秒），窗内无同键的其他创建意图；证明由工具读服务端历史得出，不接受自报时间。通过记 `APPLIED`。
- **字段计划** `feishu-field-plan-build --plan FeishuFieldPlanV1 …`（只读云）：计划逐条给出 `table/recordId/stableKey` 与每个字段的 `new/expectedOld/basisRef/observedAt/evidence[{path,sha256}]`。生成前重算证据文件摘要、核对合同类别/类型/选项、fresh 稳定键与旧值；`--ledger-dir` 时账本仍未决的目标、以及历史上有未获用户接受的 `human_release` 的目标（更新目标或其创建目标）整条拒绝。输出带旧值门禁的 V2 说明，执行仍由 `feishu-source-sync --execute`。`feishu-date-correct-build` 同样接受 `--ledger-dir` 做 human_release 门禁。

## v41 补充：精确逆操作、执行器级放行闸门、检查/产品子行

- **执行器级 human_release 闸门**：账本里该案的更新键或创建键上出现过未获用户风险接受的 `human_release`，`feishu-source-sync` 在任何外部调用之前拒绝对该案记录的新业务赋值(`HUMAN_RELEASE_UNACCEPTED`)；
  不再只靠构建器拦截，手写 spec 也一样。V3 创建时，父案有这类历史则其下子行记 `BLOCKED_ENTITY`，不新增子行(子行会改动父行反向关联)。
- **精确逆操作 `restore`**：只用于撤销本助手早先写入的误写值。字段项写 `{new:null, expectedOld:<误写值>, restore:{intentId}, basis}`；执行器核对账本里存在该精确意图
  (同目标、同稳定键、写前旧值为空、线上值等于 expectedOld、唯一 OK 终态、其后没有任何别的写入意图)，云端当前值仍等于 expectedOld，才发 `null` 清空并读回为空；其余字段与关系写前写后不变。
  原误写事件永久保留，另记修复意图/终态；不构造 humanRelease。`feishu-field-plan-build` 的条目带 `restore` 时需 `--ledger-dir`。
- **创建时一次写入的字段**：合同里 `syncWrite` 以 `create-once` 开头的字段只能随新行创建写入，V2 更新说明一律拒绝。
- **子行创建说明 `feishu-child-build`**：由本案已核验清单(总账完成记录摘要相符)为“云端还没有任何子行”的案卷生成检查明细与产品明细的 V3 创建说明(同案同份，先检查后产品，子行只设置子端父关联)。
  稳定键 `项目编号|阶段(|序号)` 由本地按清单位置生成；来源清单未提供原生检查/产品标识，原生标识留空，并写入每个字段的 `sourceRef`。
  检查结果按产品逐项汇总(任一不合格→不合格；全部合格→合格；其余待核实；产品都没有结果时才取带原件摘要的总账分类)，缺失值省略不兜底；
  初查日须等于云端主行初查日、大队须与清单管辖一致，否则只保留该案并列出原因。复查产品的“同产品”关系按 `initialProductRef` 是否对应初查产品逐条记入报告(`recheckRelations`)，不写入云端。

