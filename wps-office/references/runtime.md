# Windows 运行与输出契约

## 真实入口与边界

- 仅 Python 标准库，执行端必须为 Windows；`catalog` 是可跨主机只读的静态目录。
- 当前历史证据入口：`C:/Users/12070/AppData/Local/Kingsoft/WPS Office/12.1.0.28802/clitool/wpscli.exe`，CLI 1.1.0。路径只说明该次安装；实际优先显式 `wpscliPath`，否则核当前 PATH 或唯一安装候选，不按版本号选最大者。
- `probe` 仅执行 `--version` 和指定白名单命令的 `--help`。帮助以 CRLF→LF 规范化后的 SHA-256 与内嵌目录逐一比较，版本/帮助漂移时拒绝执行。文件路径、大小、摘要、版本和封装自身摘要记录在回执；这不证明会员权益或转换质量。
- 安装/登录/反馈/账号行为没有执行透传入口；不运行 `wpscli install`，不改 WPS 内嵌 Skill、配置或插件。
- 真实账号、输入内容、转换、COM、窗口和 AI 的验证各自需要当前授权。接口调查不创建/附着产品实例，也不据空闲状态恢复用户暂停。

## 三种动作

在本 Skill 目录执行（Python 入口沿目标机已安装事实选择）：

```text
python -I -B scripts/wps_cli.py catalog
python -I -B scripts/wps_cli.py probe < 无秘密的probe请求.json
python -I -B scripts/wps_cli.py run < 本次请求.json
```

`probe` 请求仅接受 `wpscliPath` 和 `commands`（白名单命令数组，省略时查 28 项）。密码请求不要写入文件：在本机调用进程内构造 UTF-8 JSON，经标准输入传递。PowerShell 7 中同时核 `$OutputEncoding` 与 `[Console]::OutputEncoding` 为 UTF-8；PowerShell 5.1 的默认 ASCII 管道可能把中文变成 `?`，路径中的这种损坏会在启动前拒绝。普通文字中的合法问号与有损转码无法普遍区分，应在调用端核对原 UTF-8。输出为 ASCII 转义 JSON，不依赖控制台中文代码页。

示例（文件及其父目录由调用方按真实授权准备；`new-run`、`receipt-run` 必须尚不存在）：

```json
{
  "schemaVersion": 1,
  "runId": "explicit-user-task-a1",
  "command": "pdf2word",
  "inputs": ["D:/task/input/sample.pdf"],
  "outputKind": "file",
  "output": "D:/task/new-run/result.docx",
  "options": {"engine": "basic", "range": "1-2"},
  "cloudProcessingAuthorized": false,
  "evidenceDir": "D:/task/receipt-run",
  "wpscliPath": "C:/verified/clitool/wpscli.exe"
}
```

## 参数与范围

- `schemaVersion=1`；`runId` 为 1–101 字符的 ASCII 字母数字及 `_.-`，首字符必须字母数字；`inputs` 为 1–128 个具体盘符绝对文件路径。拒绝未知顶层字段、未知命令/选项、通配、URL、UNC、设备路径、ADS、保留名、遍历、末尾点/空格、换行及以 `-` 开头的值。先解析真实路径和输入摘要，保护输入及水印图片。
- `options` 使用命令帮助原生长选项名去掉 `--`（如 `image-quality`），只允许 `catalog` 中该命令的选项；布尔值必须 JSON true/false，不能字符串。`--json`、输出和密码由封装构造，不作为通用透传选项。
- `password` 是源密码；单输入用字符串，批次用按输入顺序的数组；`openPassword`、`editPassword` 为加密密码。Office 转图的批次密码未提供分号语义，拒绝；不向无密码参数的命令添加密码。输入路径/密码的分号有原生分隔歧义，当前拒绝；普通文字、水印文字及非分隔路径中的分号完整保留为一个 argv 元素。
- `outputKind=file|directory|none` 必填。pdfinfo 为 none，禁止 output/outputScope。合并类输出文件；pdfsplit、pdf2photo、非长图 Office 转图、非合并 photo2pdf 及非合并多输入转换输出目录。长图 Office 转图只接单输入。文件扩展名须与 suffix 或该命令默认输出相符，PDF→CAD 的文件是 ZIP。
- `outputScope` 可省略：文件模式取 output 父目录，目录模式取 output 本身。首版仅支持文件直接位于该范围内或目录等于该范围；范围父目录须存在，范围本身必须不存在。意图落盘后排他创建，不静默改目录或覆盖；photo2pdf 固定 `overwrite=false`。目录模式拒绝重复输入基名/无扩展名名称碰撞。
- `evidenceDir` 同样须为尚不存在且父目录已存在的独立目录，与输出、原件、主机状态根互不混用。输出或证据范围不得包含输入。真实路径解析后再次比较，实际产物和资源不得逃逸或经过符号链接/目录联接进入范围外。
- `options.timeout` 为每输入 1–3600 秒（pdfinfo 无原生 timeout）；父上限默认 `min(86400, 每输入秒数×输入数+15)`，可用 `parentTimeoutSeconds` 在 1–86400 秒内明确指定。超时只终止本次 CLI 句柄，底层产品效果仍可能未知。
- Windows 使用 argv 数组、`shell=False`、`stdin=DEVNULL`、`CREATE_NO_WINDOW`，固定 cwd 为本次证据目录；不经 Git Bash/PowerShell 二次解释参数。无额外控制台窗口不等于 WPS 自身绝无界面，仍须遵守用户暂停。

## 云授权与密码

`photo2word/photo2excel/photo2ppt` 和 PDF→Word/Excel/PPT 的 scanned/ai-fix 分支要求同时提供：`cloudProcessingAuthorized:true`、非空 `cloudAuthorizationBasis`、`authorizedInputHashes:{原请求输入路径:实际SHA256}`。摘要集合须精确匹配当前输入，校验在任何子进程启动前完成。`engine=auto/ai` 的帮助称本地引擎，不据名称推断云或断网隐私保证。其他未标云命令也没有离线承诺。

密码通过本机进程标准输入临时进入封装，但原生 CLI 只接受命令行密码；同用户进程、管理员、命令行审计或 EDR 可能看到其 argv。封装不能消除产品接口这层可见性。封装不保存原请求或完整 argv，新记录不生成含密请求摘要或密码摘要。`requestIdentity` 仅对非秘密字段及固定的秘密存在/形状标记求摘要；任意深度的密码字段子树均不参与计算，不能先按密码值替换其他字段再求摘要。密码字段存在性按固定顺序记录。

原生事件载荷、stderr 中的密码值及 JSON 转义值去敏，子进程异常不得直接字符串化。封装自身的键、状态、身份、文件摘要及已知公开路径属于结构化元数据，不按密码字符替换；原生事件的已知字段名和合法类型/状态枚举也保留。合法短密码如 `a`、`0` 不得破坏 JSON 或回读。公开元数据偶然含有相同字符不表示密码回显；非秘密字段仍应按原用途提供，不能把其他秘密塞入公开字段。

## 持久意图与恢复

固定状态根为当前 Windows 用户 `%LOCALAPPDATA%/dmdmwshr/wps-office/runs`，不由请求 evidenceDir 决定，也没有 JSON stateRoot/任意程序旁路。每个 runId 排他建立目录与 intent，文件 fsync 后才启动版本/帮助或命令；随后记录 launched、result。该机制只防封装误重放，不代替正式任务单、角色或资源准入。

新 intent/launched/result/receipt 的 `schemaVersion=2`，请求输入仍为版本1。同号且非秘密身份相同只回读既有结果，`replayed=false`、`existingRun=true`；只换密码（包括数组元素和 open/edit 密码）也不执行、不验证新密码。同号非秘密字段、密码存在/形状变化（包括换 evidenceDir、options 文本）明确冲突，先于任何子进程。需要纠正密码时先核原运行实际效果，再按新任务授权决定后续，不以新号盲重试。

旧版本记录只作保守回读：返回 unknown、`legacyRecord=true` 和有限状态提示，不比较旧含密摘要、不返回旧正文或 `requestDigest`、不执行或改写旧文件。旧记录已含的敏感摘要仍原样保留，须沿原证据保护。目录已存在但 intent/结果不完整时同样返回 unknown。进程超时、非零、部分失败、缺产物、输入变化及无法绑定事件，均保留已知子项并返回 unknown。不能删除目录或重放业务来恢复；先按原回执核实际结果。

## 输出封套与产物

- `parse_capture` 接受原生 JSONL 或含 stdout 的历史采集封套，忽略空行、保留行号和顺序；未知事件及非 JSON 行不静默通过。逐输入按 started→completed 绑定并核输入基名；合并类按组，pdfinfo 按 file 精确路径。
- pdfinfo completed 含 file/page_count/is_scan_document，无 status/output；该启发式标志不表示已 OCR。转换须 status=success，并逐个核 actual output，不能靠 progress=100、退出零或期望文件名推断。
- 产物记录实际路径、大小、SHA-256；目录按真实文件收集。图片记录实际文件数和可识别的有限头部尺寸，未识别则标未核实，不从页数推导张数或宣称像素/渲染通过。Markdown 按相对链接、图片及 HTML img 引用收集资源，URL 解码后再核范围，拒绝外链、绝对路径、缺失或逃逸，不执行和联网；未锚定邻居单列 unboundFiles。
- CAD ZIP 只盘点条目、大小和 CRC，拒绝绝对/遍历/链接条目；不解包、不补图、不改引用。所有质量均为 `contentQuality=not-assessed`，结构/摘要不证明内容通过。
- `loadedConfiguration` 回读实际 stateRoot/stateDir/evidenceDir/outputScope/dataTargets。result 先保存在固定状态目录，再在自建证据目录写 receipt.json；无法保存证据副本也保留 unknown。

## 验证层级与离线自测

历史索引 27 份响应实际覆盖 26 种命令，pdf2word 有两条；cad2pdf 另有 finish 回执，caj2pdf 未测。历史 CAD 回转缺图为内容失败；OCR 字符、字体、空格、标点、缩放/换行有变化；photo2pdf 的 pypdf 解析失败与 PDFium 视觉正常并存；pdf2photo 样本是长图。不得把这些历史原生调用当作新封装真实运行。

本批只验证源码、离线响应及自建 Python 子进程。新封装实际转换、多输入原生事件、自动改名、错误/超时形态、加密源、图片水印、Windows 特定路径/参数行为，COM 运行、保存重开、窗口隔离、AI 校对及会员权益均须按后续授权独验；当前不声称通过。

离线测试：设置 `WPS_TEST_ROOT` 为已授权仓外测试根、`WPS_FIXTURE_INDEX` 指向冻结的 27 份响应索引，运行 `python -I -B tests/test_wps_cli.py`。缺索引明确跳过真实响应覆盖；不调用原生 WPS。测试用 unittest.mock 在 Python 内替换路径/主机/观测边界和自建进程，不向生产 JSON 暴露任意执行或改状态根的开关；自建失败/未知材料保留，独立 Windows 测试材料不修改。正式发布和新进程发现仍由原维护者在源接受后按 CC Switch 精确完成，不安装到 WSL。
