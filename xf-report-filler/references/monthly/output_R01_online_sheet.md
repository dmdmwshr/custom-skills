# R01 在线维护分支：金山文档在线月考核表

## 文件用途

R01 的可选分支。本地 `科室月考核情况记录表.xlsx` 仍是事实源；在线版本由多人维护，本项目只同步自己负责的 8 个大队 × 3 个项目共 24 格（产品案卷核查、产品工作实效、联网系统核查），其余单元格属于他人。用户明确要求维护在线表时才使用。

## 数据源解析

- 本地源：R01 生成的月度表，只读，记录 sha256；按“含 8 个大队名的表头行”和“左侧标签列的项目名”定位，不依赖坐标。
- 在线表：链接或文件 ID 是本机执行输入，经 `--online-url` / `KDOCS_ONLINE_URL` 传入，不写进 Skill。工作表固定取“N月情况”（`resources/monthly_workflow.json` 的 `online_sheet_rules`），文档标题须含当年（`26年`/`2026年`）。
- 在线读写通道，按顺序：
  1. 官方 `kdocs-cli`（`--kdocs-cli` 或 `KDOCS_CLI`）。先 `auth status`；`authenticated=false` 时停止，由用户本人 `auth login`。读写用 `sheet get-sheets-info / get-range-data / range-data-batch-update`，参数经标准输入传 UTF-8 JSON。
  2. CLI 不可用或未登录且已有浏览器 fallback 时，用 Ego（先读当次 ego-browser Skill，沿同一任务空间，不新开浏览器）。执行器 `scripts/ego_online_sheet.mjs` 只处理页面里实际存在 `WPSOpenApi.Application`（金山文档 WebOffice JSAPI）的在线表；没有该对象的页面抛 `unsupported_page` 并保留不动。流程：
     1. 用户本人登录后，页面里用 CSS 路径点选目标月份页签（不要用坐标猜），再确认 `ActiveSheet.Name` 是“N月情况”。
     2. round 脚本（UTF-8 `.mjs` 文件，经 `ego-browser nodejs -e "await import('file:///…')"` 运行；Windows 路径写正斜杠）：`freezeAllSheets` 冻结所有工作表（各自 A1:N19 及更大已用区域，封顶 200×40）的值/公式，`readSnapshot` 产出 `online-sheet-snapshot/1`。
     3. `monthly_online_sheet.py plan --snapshot <快照> --source <本地xlsx> --year Y --month M` 生成 `plan.json`，核对映射和 24 格动作；再跑 `check-plan`（按本地源重算白名单、值、年月、指纹）通过才交给 Ego。
     4. `applyPlan(page, planPath, outDir, {year, month, onlyCells?})`（year/month 必填）：先核计划指纹、年月、固定 8 队×3 项白名单、本地源哈希，再核页面文档 URL/标题、活动月份表名与索引，并用页面现读内容重新定位表头行/大队列/项目行（出现重复表头或重复项目行即拒绝，在线坐标必须等于语义重算坐标）。写入时**每格动作前**在页面里再核文档、月份表、本格表头/项目名，以及该格当前值/公式仍等于计划基线，才设置 `Range.Value2`（字面文本，换行与反斜杠原样）并即时回读；基线不同的格记为冲突并跳过，布局变了就中止。只有本地待补红底的格才追加 `Interior.Color = 255`（BGR 红），其他格的填充、字体、行列宽高、合并一律不动。任一格对不上、填充读回不是红、`Save()` 出错、写入出错或中止，状态都是 `needs_reconcile`，不会是 done；状态文件含计划指纹和待确认格。最后冻结全部工作表与写前比较，白名单外的变化逐条报告。
     5. 刷新页面后重新 `readSnapshot` 并再跑一次 `plan`：应全部 `no_change`；`verifyFills` 回读 24 格填充，红底格数应等于本地待补格数，不得出现多余红底。
     6. 状态为 `needs_reconcile` 时只能 `reconcileApply(page, planPath, outDir)`：绑定原状态里的计划指纹、文档/工作表和待确认格；无状态、状态损坏、错计划、错文档或无待确认格都抛错且不改状态，只有全部待确认格值与红底都到位才标 done。状态文件读不出或结构不对一律 fail closed，不换目录冒充重跑。
     7. 首次在新页面使用先用 `onlyCells` 试写 1–2 格（含多行文本与反斜杠格），回读通过再全量。
     已验证的 API 事实：`Range('A1:N19').Value2` 返回二维数组；多格 `Range.Formula` 只返回首格，须逐格读；`UsedRange.Address` 不可用，用 `UsedRange.Rows/Columns.Count`；`Interior.Color` 白底为 16777215。
  3. 明确的权限拒绝（403、企业账号受限、无编辑权）不换入口绕过；不用后台内部接口，不整表上传，不新建文档。

## 生成规则

- 命令（均为显式子命令，不存在默认写入）：
  - `plan --source <本地xlsx> --year Y --month M --out <任务目录>`：只读，输出 `online-before.json`（写前快照）和 `plan.json`（含指纹、源哈希、年月、文档 URL/文件 ID、工作表名与 ID）。本地源年月取自文件名（如“2026年11月”）；读不出时阻断，由用户明确后用 `--source-period YYYY-MM` 声明，但它不能放过与 `--year/--month` 不符。`--sheet-name` 只是断言，必须等于“N月情况”，不能改投别的月份页。
  - `check-plan --source --plan --year --month [--snapshot]`：只读，按本地源重算并严格核对计划（固定 8 队×3 项、坐标唯一、值与源一致、指纹、年月、可选在线语义坐标）。
  - `apply ...`（仅 CLI；浏览器分支由 `ego_online_sheet.mjs` 的 `applyPlan` 执行同口径检查）：先严格重算计划（白名单、年月、指纹、源哈希与值），再读两次在线表：绑定原 URL/文件 ID、标题、工作表名与 ID，映射语义唯一，每个待写格当前值须仍等于 plan 基线，才写；写后立即回读。官方 API 没有原子“比较并写”，近时复核只能缩小并发窗口，不能声称绝对消除极短竞争。只经 CLI。
  - `reconcile ...`：只读，必须绑定原 `apply-state.json` 的计划指纹、文档/工作表和待确认格；无状态、状态损坏、错计划、错文档、无待确认格都报错且不改状态，全部待确认格到位才标 done。
- 动作：`write`（本地与在线不同）、`no_change`、`skip_source_blank`（本地空值不清空在线）、`skip_online_formula`、`skip_protected`、`blocked`。幂等：再次运行时只剩 `no_change`。
- 值按字面写入：反斜杠 `\` 就是反斜杠，多行文本保留换行；本地值以 `=`、`+`、`-`、`@` 开头一律阻断（可能被在线表按公式解析，未验证其解析行为，不猜）；当前业务源无此类值。
- 只写单元格内容，保留在线行列、合并、字体和其他格式。`local_pending_fill` 记录本地红底（待补）语义：CLI 分支不同步；浏览器分支只对本地红底格新增红色填充，不清除任何格的填充。25 日前红底待补、定稿都由用户另行确认，在线表不写“已定稿”。
- 写前快照（all sheets 列表、当前工作表已用区域值/公式）和每格写前值（`recovery.json`）保存到任务目录；恢复就是按格写回写前值。

## 不写入内容

- 他人维护的单元格、其他工作表、空白的本地源格（不清空）。
- 在线格式（浏览器分支新增的本地待补红底除外）、权限、分享设置；不新建在线文档，不发送消息，不设定时任务。
- 不把 CLI 或浏览器 Token、Cookie 写入证据。

## Warning/Blocker

- Blocker（整批停止，不写）：本地源年月未知、与 `--year/--month` 或计划年月不一致；文档标题年份不符；月份工作表不存在或不唯一，或 `--sheet-name` 与目标月份页不符；文档 URL/文件 ID、标题、工作表名/ID 与计划不同；表头行或项目行缺失/重复；任一大队列重复；计划不是固定 8 队×3 项、坐标重复或不等于语义重算坐标、指纹不符、值与本地源重读不一致；写前复核时映射与计划不同；`plan.json` 为 blocked；本地源 sha256 与计划不同；状态文件损坏。
- 单格冲突（写前值与基线不同）：跳过并列入 `conflicts`，不覆盖近期人工改动。
- 写入报错或回读不一致：状态 `needs_reconcile`，禁止重放；先 `reconcile`，成功到位后才恢复为 done。
- 写入窗口内其他格的变化记入 `outside_target_changes`，按并发修改处理，不能证明整表无人改动。

## 验收点

- 24 格全部映射，`plan` 汇总数与 `write/no_change/skip_*` 一致；本地 xlsx 哈希未变。
- 写后回读 24 格与本地一致（空值格除外），刷新后再读仍一致；其他人单元格无本次写入。
- 证据：源哈希、在线身份、映射、前后快照、变更、冲突、未覆盖项、恢复方法。
- 离线测试 `scripts/test_monthly_online_sheet.py` 用合成夹具覆盖错月/重复行、源空值、反斜杠与多行、保护格、幂等、并发冲突、未知写入不重放；真实在线写入另行回读，离线测试不代替。
