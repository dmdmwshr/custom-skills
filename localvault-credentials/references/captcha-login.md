# 授权普通图片验证码登录（Ego 主入口）

执行平台：Windows。本流程把已验证的“本地凭据 → 刷新验证码 → 本地离线 OCR → 填写提交 → 刷新后核验”固化为现有 `localvault-credentials` 的一条可复用路径，业务 Skill 只引用本契约，不复制代码、不另建 Skill、服务或计划任务。

## 适用与前提

- 仅限用户已明确授权的“普通图片验证码”（4 位数字图片）。短信/邮箱验证码、MFA、滑块/行为验证、反自动化检测都不在范围内，仍由用户完成，不绕过。
- 目标站点、账号、LocalVault 密钥名由用户明确指定；密钥必须已用 `Save` 保存（PSCredential）。
- 浏览器只用调用方**已准备好**的 Ego TaskSpace 与 Page（精确 `--space`、`--page`）：运行器不 claim 他人空间、不新建空间/页面、不关闭页面、不 `finish()`、不注销。TaskSpace 归属为 `user`/`inactive`/`unassigned` 时直接停止（`ego_space_not_agent_owned`）。
- 同一时刻只能有一个运行器操作同一任务；同一个 Ego 页面不能由另一个控制者同时操作。

## 组件

| 文件 | 作用 |
| --- | --- |
| `scripts/captcha-login.mjs` | 子命令 `init` / `status` / `login` / `verify` / `reconcile`；Node ≥ 20，经 `Manage-LocalVaultCredential.ps1 -Action Invoke` 从 stdin 接收 `{username,password}` |
| `scripts/captcha-ocr.py` | 离线 RapidOCR worker：stdin 收一张图，只输出一行 JSON；不写文件、不联网 |
| `scripts/test-captcha-login.mjs` | 合成回归（假 Ego、假/真实离线 OCR），测试数据只写 `CAPTCHA_TEST_DIR` 指定的仓外目录 |

受管发现入口（Codex）：`C:\Users\12070\.codex\skills\localvault-credentials`；不要使用旧的 `…\.cc-switch\skills\自建skills\…` 遗留检出。

固定运行时：Node `C:\Users\12070\nodejs\node.exe`；Windows Python `C:/Users/12070/AppData/Local/Programs/Python/Python312/python.exe`（`rapidocr_onnxruntime 1.4.4` + `onnxruntime 1.21.0`，随包 ONNX，纯 CPU，线程数 1）；Ego `C:/Users/12070/.local/share/ego/active_version_dir/ego-browser.exe`。三者可用 `--python`、`--ego-bin` 覆盖，不安装新依赖。

## 识别规则（L1）

- 只接受 ASCII 四位数字且置信度 ≥ 0.8（`captcha-ocr.py` 与运行器各判一次）。
- 低置信或格式不符：刷新验证码重识别，**不提交**；识别轮次有界（`--max-ocr-rounds`，默认 8，上限 20）；连续两次刷新无变化即停（`captcha_refresh_failed`）。
- 识别码、图片、base64 只在内存中；日志只记耗时、分数、提交次数和分类原因。

## 持久计数与重试（L2）

状态根固定为 `%LOCALAPPDATA%\LocalVaultCredentials\captcha-login-state\<taskId>\`（`state.json` + 排他锁 `lock`）。`CAPTCHA_LOGIN_STATE_ROOT` 仅测试可用（须同时设 `CAPTCHA_LOGIN_ALLOW_TEST_ROOT=1`），生产调用不得改根，输出中的 `stateRootKind` 应为 `default`。同一许可下还有测试专用的 `CAPTCHA_LOGIN_TEST_PAUSE_BEFORE_RECONCILE_LOCK_MS`（用于确定性复现对账竞态），生产调用不得设置。

1. **绑定**：任务 ID 绑定固定的目标 URL、期望账号显示名、业务入口标记、密钥名和提交上限；同任务换任何一项返回 `binding_mismatch`，不会重置计数。
2. **初始化必须显式**：`init` 要求 `--historical-used`（历史已用次数，无默认 0）以及不可变证据文件 `--evidence-ref` + `--evidence-sha256`；已存在状态不覆盖（`state_exists`），缺失状态不静默重建（`state_missing`），篡改/损坏返回 `state_corrupt`。
3. **先记账再点击**：确认 OCR 通过后，先原子写入（临时文件 + fsync + rename）“提交意图”，计数 +1 并置待对账，再点击提交。实际提交次数与 OCR 刷新轮次分开计；当前任务最多 5 次实际提交，每次运行另有上限 `--run-submission-cap`（默认 3）。
4. **只重试本次提交产生的明确验证码错误**：点击前先记录页面已有提示（已有提示最多等约 4 秒消失），点击后只统计**新增**的提示；仅新增的验证码类提示才刷新重试。点击前就存在且未变化的“验证码错误”不是本次反馈，没有新增反馈即为未知。新增账号/密码类（`credential_error`）、限制类（`access_restricted`）立即停止；新增提示夹杂其他内容、或无法归类，同样按未知处理，不重试。
5. **明确失败持久阻断**：`credential_error`、`access_restricted`，以及点击后刷新核验失败（`account_mismatch`、`not_logged_in`、`verify_failed`），都会把任务置为“阻断”（`blocked`）。阻断写在状态文件里，新进程的 `login` 返回 `blocked_reconcile_required` 且不触碰浏览器；只有现场核对后附证据执行 `reconcile --resolution blocked-reviewed` 才能解除，计数不退款、不复位。
6. **未知保持待对账**：点击后超时、脚本异常、结果无法分类、进程崩溃，均保留“待对账”和已记的计数，**不自动再点**；`login` 此时返回 `pending_reconcile_required`，且不触碰浏览器。仅当脚本明确声明点击尚未发生（点击前失败）才自动释放这次计数。
7. **锁**：并发的第二个运行器返回 `locked`；崩溃遗留的锁永不自动清除。`reconcile` 之间由 `reconcile.lock` 互斥（同样不自动清除），在其保护内先确认持有进程已不存在、再清理旧锁、取得排他锁后才读取最新状态；活跃持有者或被替换的锁不会被删除。

对账（需现场核实结果后再执行，附证据文件）：

```powershell
node captcha-login.mjs reconcile --task-id <taskId> --resolution submitted      --evidence-ref <证据> --evidence-sha256 <sha256>   # 确认已提交：保留计数
node captcha-login.mjs reconcile --task-id <taskId> --resolution not-submitted  --evidence-ref <证据> --evidence-sha256 <sha256>   # 确认未提交：计数 -1
node captcha-login.mjs reconcile --task-id <taskId> --resolution blocked-reviewed --evidence-ref <证据> --evidence-sha256 <sha256>  # 明确失败/核验失败后，现场核对并获授权后解除阻断：计数不变
node captcha-login.mjs reconcile --task-id <taskId> --resolution clear-stale-lock                                                   # 仅清理已退出持有者的锁（无待对账时）
```

## 成功核验（L4）

点击后 URL 离开登录页不算成功。运行器随后对**同一页面刷新**，在有界轮询内同时核对：

- URL 解析后源（协议、主机、端口）与目标完全一致、不含 userinfo（`http://目标@evil.invalid/…` 不通过）且不是登录路径；登录页本身要求路径与 `#/login` 哈希精确匹配，不用字符串前缀。
- 页面无密码框。
- **账号控件**：`--account-selector` 指定的 CSS 选择器必须恰好命中**一个可见**元素（隐藏元素不计入），且该元素规范化（合并空白、去首尾空白）后的文本与期望账号显示名**完全相等**。命中 0 个（`missing`）、多个（`ambiguous`）、文本不等（`mismatch`）都不通过；页面其他位置出现同名文字（如“历史经办 钱奡”）不能代替当前账号控件。
- **业务入口控件**：`--business-selector` 同理，必须恰好命中一个可见元素且文本与业务入口标记完全相等。
- 不使用整页文本，选择器缺失时不回退（见下一节）。

## 必填：账号与业务入口的控件定位

`--account-selector`、`--business-selector` 是**必填**的非秘密 CSS 选择器，纳入任务绑定（同任务换选择器会 `binding_mismatch`）。本技能不内置、不猜测任何真实站点的选择器，也不会在缺失时回退到整页文本：`init`、`login`、`verify` 缺任一项都直接 `usage_error`（fail closed），`body`、`html`、`*` 这类整页泛选择器会被拒绝（`bad_selector`）。

一般规则：对尚未提供选择器的新目标站点，选择器由来源 Windows 负责人在已授权的短测试窗口里读取当前页面后提供，并写进调用契约；在提供并核对之前，该站点只能用合成页面验证完整契约，真实成功核验记为未覆盖。选择器应指向只显示账号名（或菜单项名）的那个元素，而不是包含问候语或其他文字的容器——后者会因文本不完全相等而判失败。

当前案卷监督系统任务（已实际提供并验证的事实）：账号控件选择器 `.avue-header .top-bar__right .el-dropdown-link`，业务入口控件选择器 `.avue-header .el-tabs__nav #tab-306`。二者来自对实际当前页面控件的新读取，并在真实登录后的刷新核验中各命中唯一可见元素、规范化文本分别与“钱奡”“产品监督”完全相等，随后原 `verify` 刷新后再次通过（来源：`WINDOWS_TEST_RESULT_REAL_A17.json`，sha256 `ad7a5a43404d459c900fb547da440b34e4f9b1967d01635e739da79a07c8b8c1`）。这只证明该账号和当时的站点版本：`#tab-306` 这类标识跨账号或站点版本可能变化，换账号、换版本或页面改版后必须重新读取并确认唯一可见元素及其准确文本，不得猜测或沿用；未重新核对前按未覆盖处理。

账号控件核验不通过（缺失、不唯一或文本不等）但业务入口控件通过 → `account_mismatch`（失败）；回到登录表单 → `not_logged_in`。已在业务页时 `login` 只做核验（`already_logged_in_verified`），不提交。

子进程（Ego 脚本、OCR worker）只有正常退出（退出码 0、未超时）且结构完整的输出才被采信；超时、非零退出、缺结构一律视为没有结果，且不回显其原始 stdout/stderr。提交已发出时这意味着“结果未知，保持待对账”，不会把打印出的 `ok:true` 当成功。

**Ego 标记通道（stdout 与 stderr）。** 真实 Ego 把脚本里 `console.log` 的输出写到 **stderr**，所以运行器同时读取 Ego 子进程的 stdout 和 stderr，只采信其中以 `@@R@@`（结果）或 `@@STAGE@@`（阶段）开头的整行：

- 标记必须在行首；`[log] @@R@@…` 这类夹在行中的文本、其他噪声行、不是 JSON 对象或缺少布尔 `ok` 的标记行都不算结果。同一路里取最后一条合法 `@@R@@` 行。
- 两路都有合法结果时必须逐字段一致才采信；不一致视为没有结果。只有一路有结果时采信该路。
- 有界读取：只保留标记行，单行和累计各上限约 4 MB；超长的单行整行丢弃（包括它之后的残段），不截断解析。stderr 其余内容不保存、不回显。
- 仍然只在退出码 0、未超时时采信结果。`@@STAGE@@click-begin` 出现在任一路都表示点击阶段已开始：此后若超时、非零退出或没有结果行，一律是“结果未知并保持待对账”，不退还计数、不自动重放；只有脚本明确声明 `clickInvoked:false`（或进程根本没能启动）才释放点击前的计数。
- OCR worker 的协议不变：只读它的 stdout JSON，stderr 不采信。

`verify` 子命令只刷新并核验，不提交、不改计数。

## 调用示例

先在**单独终端**用 pwsh 执行；凭据只经 LocalVault `Invoke` 的 stdin 一次性注入，调用参数里没有任何密码或验证码。

```powershell
$skill = 'C:\Users\12070\.codex\skills\localvault-credentials'
$node  = 'C:\Users\12070\nodejs\node.exe'
$run   = "$skill\scripts\captcha-login.mjs"

# 1) 一次性初始化（显式历史已用 + 证据）
& $node $run init --task-id <taskId> --target-url '<登录页 URL，含 /login>' --expected-account '<账号显示名>' `
  --business-marker '<业务入口文字>' --account-selector '<账号控件 CSS 选择器，占位>' --business-selector '<业务入口控件 CSS 选择器，占位>' `
  --secret-name '<LocalVault 密钥名>' --max-submissions 5 `
  --historical-used <已用次数> --evidence-ref '<成功证据文件>' --evidence-sha256 '<其 sha256>'

# 2) 登录（凭据由 Invoke 经 stdin 注入）
& "$skill\scripts\Manage-LocalVaultCredential.ps1" -Action Invoke -Name '<LocalVault 密钥名>' `
  -CommandPath $node -WorkingDirectory $skill -CommandArgumentList @(
    $run, 'login', '--task-id', '<taskId>', '--target-url', '<登录页 URL>', '--expected-account', '<账号显示名>',
    '--business-marker', '<业务入口文字>', '--account-selector', '<账号控件 CSS 选择器，占位>', '--business-selector', '<业务入口控件 CSS 选择器，占位>',
    '--secret-name', '<LocalVault 密钥名>', '--max-submissions', '5',
    '--space', '<Ego TaskSpace 编号>', '--page', 'p1')

# 3) 查看计数/待对账（只读）
& $node $run status --task-id <taskId>
```

当前案卷监督系统任务的固定取值（由来源协调者调用）：任务 ID `fire-supervision-qianao-login-20261106`，目标 `http://180.101.229.94:11888/#/login`，密钥 `fire-supervision-qianao-js`，账号显示名 `钱奡`，业务入口 `产品监督`，上限 5；账号控件与业务入口控件的选择器见上一节“当前案卷监督系统任务”，是实际读取并验证过的当前事实，跨账号或站点版本须重新读取，不得猜测沿用。

当前状态（实测事实，来源 `WINDOWS_TEST_RESULT_REAL_A17.json`，sha256 `ad7a5a43404d459c900fb547da440b34e4f9b1967d01635e739da79a07c8b8c1`）：本候选已在普通（非提升）Windows 会话中，经现有 `Manage-LocalVaultCredential.ps1 -Action Invoke`、产品默认 Ego 入口、原 UNC 路径下的 runner 和本地 CPU RapidOCR 实测通过。该次实测使用的唯一临时真实账状态已按历史已用 2 初始化并使用：本次新增提交 1 次，最终已用 3/5、剩余 2，`pending` 与 `blocked` 均为空。历史起点 2 的初始化证据 `D:\ws\05-temp\20261106_飞书与案卷续办\自动登录实测证据摘要.json`（协议冻结 sha256 `669cf28f31ace856c87ecb199bc5ae8512c0d25ef5296be8635a638576fbe92f`）只记录那一次临时账的起点，**不能再作为当前正常续账的固定起点 2**。同批的零预算测试账（2/2）只是已有会话上提交 0 次的独立测试，不是正常账，也不能代替或覆盖真实账。

正常生产状态根（见上文状态根）截至本次实测尚不存在，也没有发布到受管安装位置；上方 `init` 示例是通用模板，不表示它已在正常根执行。后续仅在受管发布、安装和新进程发现回读完成后，由父方串行处理：先回读临时账最终实数，若仍为已用 3/max 5，且正常账确认不存在，才用正常原入口按已用 3/max 5 一次初始化；若正常账已存在或出现新的未知结果，先对账保留，不得覆盖，也不得退回固定 2。临时账成功不等于正常账已生效。

运行器本身不创建、不接管他人的 Ego 空间；本任务的空间与页面由来源协调者交接，本链路对其没有控制权，调用时仍须由来源协调者给出当前可用的 `--space` 与 `--page`。

## 输出与结果码

每次运行输出若干白名单 JSON 行，最后一行为 `{"result": …, "exit": …}`。

| result | 退出码 | 含义与下一步 |
| --- | --- | --- |
| `login_verified` / `already_logged_in_verified` | 0 | 刷新后核验通过 |
| `credential_error` / `access_restricted` / `account_mismatch` / `not_logged_in` / `verify_failed` | 10 | 停止并**持久阻断**，先人工核查原因；下一次 `login` 返回 `blocked_reconcile_required` |
| `blocked_reconcile_required` | 20 | 上一次提交后出现明确失败，未经证据对账不能再提交 |
| `submission_budget_exhausted` / `run_submission_cap_reached` / `ocr_rounds_exhausted` / `captcha_refresh_failed` / `ocr_unavailable` / `ocr_failed` | 10 | 未再提交；按原因处理（依赖不可用时如实报 not-covered，不降级冒充通过） |
| `ego_unavailable` / `ego_space_not_agent_owned` / `ego_page_missing` / `unexpected_page` / `fill_failed` / `ego_error` | 10 | 浏览器对象不符合调用约定；修正对象后再来，运行器不会自行 claim 或新建 |
| `submit_result_unknown` / `pending_reconcile_required` | 20 | 先现场核实是否已提交，再 `reconcile`；不盲重放 |
| `usage_error` / `binding_mismatch` / `state_missing` / `state_exists` / `state_corrupt` / `locked` | 30–32 | 参数、状态或锁问题，计数未变 |

## 兼容与边界

- `Save` / `Info` / `Invoke`、`Invoke-LocalVaultWebLogin.ps1` 映射式登录、`playwright-browser-control` 的 `vault_login.cjs` 以及显式指定的临时 Playwright 手动路径保持原行为；本流程是新增的 Ego 自动路径，不改变它们。
- 不读取浏览器 Cookie、密码库或会话存储；不在日志、状态、快照中保存凭据、验证码或图片；不输出含凭据字段的页面快照。
- 本机实测历史：真实登录一次成功（OCR 183 ms，识别到提交 556 ms，累计提交 2/5，刷新后账号与“产品监督”均可见）。该证据来自旧一次性脚本，不代表本运行器；它保留为历史，不作为本运行器的验收。
- 本运行器的真实站点集成（当前最后验收事实）：已在本机普通（非提升）Windows 会话、产品默认 Ego 入口（不带 `--ego-bin`）下，经现有 `Manage-LocalVaultCredential.ps1 -Action Invoke` 由标准输入注入凭据，运行原 UNC 候选 runner 并使用本地 CPU RapidOCR，完成一次真实登录。OCR 置信度 0.9991、识别耗时 569 ms，识别到提交启动 695 ms；本次新增提交 1 次，该临时真实账的计数由历史 2 变为 3/5（剩余 2，`pending` 与 `blocked` 均为空）。提交后刷新核验时，账号“钱奡”与业务入口“产品监督”各唯一可见，随后原 `verify` 刷新后再次核验通过。另用独立的零预算测试账（2/2）在已有登录会话上走一次 `Invoke`：提交 0 次，真实账状态文件哈希不变。
- 上述验收的边界：这是一次临时真实账的集成验证，不等于正常生产账已生效；发布、安装、新进程发现以及正常状态根的最终 `used` 续账仍须另行串行处理。真实错误文案没有人为制造，对应失败分类由真实 Ego 的合成回归（6/6）覆盖，不由这次登录证明。本机实测不推广到其他站点或其他平台；早先 not-covered 的结论已被这次真实结果取代，旧失败日志、错误归因更正和 unknown 记录仍原样保留，不作改写。
