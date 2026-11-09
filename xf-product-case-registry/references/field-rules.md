# 案卷字段抽取与校验规则

原 xf-product-case-filler 规则已并入并退役卸载。本规则只约束抽取结果的**只读校验**（`fields validate`）与人工整理口径；不写 Excel、不改名或删除任何截图。统计数据由飞书维护，Excel 导出不是默认流程。

## 稳定键与阶段

- 一行 = 一个（项目编号 `project_no`，检查阶段 `inspection_phase`，轮次 `inspection_round`）。阶段只有 `初查`、`复查`，缺省按 `初查`，轮次缺省为 1（只在校验时局部缺省，不回写输入）；多次复查用不重复的 `inspection_round`，以最后一轮结果判定行案。
- 同一项目编号最多两行；存在 `复查` 行必须有 `初查` 行且初查 `qualified=不合格`（人工裁决 `manual_fields` 含 `qualified` 的除外）。
- 项目编号格式 `\d{8}[A-Z]\d{9}`，不用截图名、行号或飞书记录号代替项目身份。

## JSON 字段

| 字段 | JSON key | 规则 |
| --- | --- | --- |
| 单位名称 | `unit_name` | 项目详情或检查记录表中的被检查单位。 |
| 项目编号 | `project_no` | 必填。 |
| 初查/复查 | `inspection_phase` | `初查`、`复查`。 |
| 单位地址 | `unit_address` | 单位地址。 |
| 标称生产者 | `nominal_producers` | 与 `products` 同顺序；数量不一致只告警，不猜对应关系。 |
| 立卷人 | `case_handler` | 主承办人，不取协办人。 |
| 检查人 | `inspector` | 最终审批人；明显不是人名时留空并写入 `missing_fields`。 |
| 检查时间 | `inspection_date` | 完整日期 `YYYY-MM-DD`。**复查行日期必须取自复查自己的来源**：`inspection_date_source` 指向 `source_files` 中的文件；与初查日期相同且无独立来源视为复制，报错；没有来源只告警；没有日期则保持留空（未知），不推测。 |
| 消防产品 | `products` | 产品名与规格型号合并为一个字符串，多个产品为数组。 |
| 微型消防站或快速处置队 | `station_or_team` | `微型消防站`、`快速处置队`、`否`；无法判断留空。 |
| 现场判定或抽样送检 | `method` | `现场判定`、`抽样送检`；无法判断留空。 |
| 是否合格 | `qualified` | `合格`、`不合格`；结果未出时留空并写 `missing_fields`。 |
| 行案/刑案 | `case_type` | 仅同一项目复查且复查不合格时才是 `行案`；明确刑事线索才是 `刑案`；否则 `否`。`[罚]`、罚字、处罚文书只作备注，不参与自动判定。 |
| 网售 | `online_sale` | 明确体现才写 `是`/`否`，否则留空（未知）。 |
| 来源文件 | `source_files`/`file_roles` | 角色 `案卷清单`、`检查记录表`、`其他附件`；`--image-dir` 时核对文件存在。 |

## 三态与缺值

- `station_or_team`、`method`、`qualified`、`online_sale`、`inspection_date` 为空表示**未知**，校验报告的 `unknownFields` 逐项列出（项目、阶段、字段），不改成 `否` 或 0。
- 缺值不推测、不用相邻行或旧值补；需要补充时回到原始文书。

## 人工裁决保护

- `manual_fields` 列出由人工裁决的字段，`manual_note` 写依据。报告 `protectedManualFields` 逐项列出。
- 系统规则与人工值冲突时（如人工认定的 `行案`）只给 warning 并保留人工值，不自动改写，也不报错阻断。
- 字段来源冲突的候选、权威来源和理由仍记录在 `field-resolution.json`；`unresolvedFields` 非空时 `local-verify` 拒绝形成凭证。

## 不继承的旧行为

- 不写或覆盖 Excel 工作簿；不备份/排序/复制模板行。
- 不原地重命名截图，不删除“旧命名重复图”。原图保留，命名与归档由 registry 的项目编号目录和摘要命名负责。
- 校验不修改传入对象；不再有“复查日期一律留空”的固定规则，改为必须有自身来源。

## 校验输出

`fields validate` 输出 JSON：`status`、`caseCount`、`errors`、`warnings`、`unknownFields`、`protectedManualFields`、`inputModified=false`；存在 errors 时以非零退出。

## 必有字段、复查收口与有因更新（2026-11-09 用户直接指令）

监督系统是案卷、检查和产品事实源；本地底册、原包、页面字段与截图是它的持久中转，先复用有效本地材料，仅对缺失、待结果、目录/字段变化或明确新线索定点核源（先本地、先比字段和目录、再更新受影响的包图，旧代际保留）。实现入口：`field_validation.py` 的 `resolve_initial_date`、`review_due_date`、`recheck_status`、`product_chains`、`administrative_class`、`is_stable_case`；清单可选 `initialProductRef` 见 `case-data-format.md`。

- **必有字段**：项目编号、单位名称（不得与单位地址串栏，`fields validate` 拒绝名称等于/包含地址）、所属大队（详情“消防管辖”，缺失时用同案文书或有证据的唯一映射，不按名称猜）、单位类别、单位地址、初查日期、检查形式、初查结果。必有不等于首次必须已有终局值：未定值须有原因与后续动作。
- **初查日期**：同案电子监督检查记录优先；缺失/不可用/无法确认时才用详情“受理日期”后备。内部保取值位置、原值、摘要和后备原因，外显来源统一“监督系统”；有效电子记录与受理日不同以前者为准并保差异；证书日、报告日、复查日不冒初查日。
- **检查形式**：来自系统查询界面/任务入口的真实字段，与产品检查方式（现场判定/抽样送检）分开。已证映射才用，未证只保原值并标待核实，不凑“其他”。
- **初查结果**：按同产品同轮次应采用的最终结果；尚未出结果写“待结果”，仅缺证写“待核实”，不得假填合格/不合格。抽样送检未出报告时进入有因更新，报告到达后一次增量。
- **复查**：状态为 初查结果待定 / 初查合格无需复查 / 观察期内未开展 / 已开展复查待结果 / 复查有有效结果 / 到期确认未开展 / 待核实。责令限期改正通知书下发后“一个月”是内部更新收口规则（非法定期限），按上海时区自然月对应日，无对应日取月末。缺下发日期不倒计时；“到期确认未开展”需下发日期、不早于到期日的有效观察证明仍未开展且无相反线索；缺复查文书不等于未开展，已开展待结果不被改写。终态（无需复查、有结果、到期确认未开展）下复查日期/结果的空值是非适用，不再触发重新下载或截图；晚到的新报告/文书/纠错证据保旧代际后重开。
- **是否行政处罚案卷**（业务归类，不是处罚决定）：按同产品链——初查最终不合格且对应整改复查有效结果仍不合格为“是”；所有相关链已终结且无“是”为“否”；有结果待出、观察期内未开展或产品对应未知且无已证“是”为“待核实”。跨阶段对应只经 `initialProductRef`，名称、数组位置、数量不能连接；抽样复检与整改复查严格区分。旧的行键（项目/阶段/轮次）、clientRef、record_id 与父链不变。
- **人工字段**：是否加油加气站、是否微型消防站、是否网售、是否维修后灭火器由大队在飞书填报；来源同步 NEVER/deny（含 fldtBHoCzK），保旧值与空白，空白不变“否”、不触发回源或下载。统计归类、数据层级等过程字段保历史与依赖，不作业务必有，不触发重采。
- **稳定复用**：身份基础字段齐、初查结果已明确、适用复查分支已终结且无相关变化线索时默认复用本地记录（`is_stable_case`），不设统一过期天数；材料齐全/混包、本地整理、归档状态、飞书同步分轴，人工待填与非适用空值不降低来源业务完整度。

### 生命周期规则的实际入口（r36，源仓 `scripts/case_lifecycle.py`）

规则不只是纯函数；下列正常入口都会调用它们，数据来自已存来源详情、总账分类、清单和归档代际，不联网、不写文件：

- **`ledger status/report` 的逐案 `lifecycle`**（`ledger_views.describe`）：必有字段状态（present / pending / missing / conflict）、初查日期与来源、初查结果、复查状态、是否行政处罚案卷、稳定、`sourceCauses`（具体需核源的原因）与 `localCauses`（可由本地材料补齐）。分类证据摘要与后来重写的详情不符时，只读取**可核**的同案已存详情，三层分别绑定：①内容——记录自带 `businessFingerprint` 必须等于其 `fields` 重算值（自指纹自洽只是必要条件，不足以信任）；②总账登记——当前 RWID 须有总账 `detailBusinessFingerprint` 且与之相等（缺失即拒绝），其他已绑定 RWID 须有 `observationsByRwid[rwid].fingerprint` 等于该记录的整条指纹；③观察时点——`capturedAt` 必须是总账为该 RWID 登记过的时点（`source.capturedAt`、`observationsByRwid[rwid].observedAt`、`classification.observedAt`），改写 `capturedAt` 不能制造到期后的有效观察。另需 RWID 为总账为本案绑定、详情项目编号与对象归属本案、文书目录有且仅有本案 `[产]` 产品根；任一不满足即拒绝（原因进入 `evidenceRefused`），案不得稳定并带来源原因 `SAVED_DETAIL_UNTRUSTED`。合法的后续新观察（intake 同时登记内容与时点）和同案多 RWID（各有登记）照常通过。
- **初查结果的页面依据**（不依赖已结案/处理中状态）：复查行不参与；任一现场质量/市场准入单元为“[N项]不合格”→不合格；单元为“抽样送检”→待结果；全部“未发现不合格现象”且检查情况含“[合格]”→合格；其余保待核实。文书目录里的“检验报告”标题不能当作抽样检验报告（实读 5 案均为型式试验报告），不据此改判为本地可补。
- **`ledger lifecycle`**（只读）：2026+ 产品案分为 `REUSE_DIRECT`（稳定，或观察期内未到期）/ `LOCAL_SUPPLEMENT`（缺项可由本地材料补）/ `NEEDS_SOURCE`（有具体原因才核源），并列出各原因计数与“复查产品未经 initialProductRef 对应”的本地缺口数 `recheckLinkUnproven`。总账成员数不是年度分母。
- **复查对应**（r37 修订）：只有经 `initialProductRef` 对应到同一初查产品的复查才关闭那一条链。复查产品没有可解析的 `initialProductRef`（旧清单、页面行、引用不存在）时不按名称/位置/数量连接，**任何有效复查结果数都不能关闭任何链**：未对应的复查、以及没有对应复查的不合格初查产品，各自保持待核实（`reasonCode=LINK_UNPROVEN`），案级记 `RECHECK_PRODUCT_LINK_UNPROVEN`（本地原因，补引用即可，不触发重采）；已有证据链的其他产品按自己的状态判断（`recheck.otherState`），未知关联只阻本链。
- **更新决策**：`package_decision` 对稳定案返回 `PKG_REUSE_STABLE`；无具体原因返回 `PKG_REUSE_EXISTING(NO_CAUSE_YET)` 并带 `nextEligibleAt`；有原因（待结果、复查待结果、到期复核、新证据）才 `PKG_REFRESH_DUE_RECENT_UNQUALIFIED`，仍受间隔节奏限制。近期增量队列对来源文书指纹未变、已收包的案：稳定以 `STABLE_CASE_LOCAL_REUSE`、无来源原因（仅本地缺口）以 `NO_SOURCE_CAUSE_LOCAL_REUSE` 跳过详情，不论是否已归档。 同一已登记观察（总账 `lastObservedAt` 与 batch 相同）再次进入队列时不再调用底层 `upsert_case`/持久保存（零写），真正新的观察时点、新文书、缺包与来源待处理仍进入原因队列。
- **`fields validate`**：接受 `qualified=待结果/待核实`（列入 `pendingResultFields`，其他值仍拒绝）；人工字段空白列入 `manualBlankFields`、`not_applicable_fields` 声明的空值列入 `nonApplicableFields`，二者不算来源缺项；输出 `lifecycle`（复查状态与行政处罚归类，单产品阶段才可连接，否则待核实）。
- **飞书来源同步**：`feishu-source-build` 在电子初查日缺失/不可证时用详情受理日期后备（内部留痕、差异保留）；合同暴露 `初查结果`/`初查日期来源` 且选项含目标值时，创建带“合格/不合格/待结果/待核实”与“监督系统”，补空；云端为待结果/待核实而来源已有终局值时以 `expectedOld` 做有因更新，已是终局值不覆盖；合同没有该字段时整项跳过并列入 `lifecycleFieldsSkipped`。创建路径以最新基础合同为准：`syncWrite` 为 DENIED 的字段（检查结果 fld1RPp2yY、产品检查结果 fldJUpLw89 等）不能被旧扩展候选重开；选项不在最新合同内只阻该实体；规划与执行必须同一合同摘要。
