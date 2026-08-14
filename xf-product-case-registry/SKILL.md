---
name: xf-product-case-registry
description: 管理消防产品案卷登记系统 V2 的本地清点、51 个文书槽位清单、PDF、ZIP、截图核验及四步生产接口导入。用户提到 V2 案卷包、固定槽位或生产导入时使用；截图 Excel 台账改用 xf-product-case-filler。
---

# 消防产品案卷 V2 本地整理与导入

只适配当前消防产品案卷信息登记系统 V2。不得改动网站、数据库、接口、部署或 `xf-product-case-filler`。

## 先读的契约

执行 V2 导入前，读取以下生产契约；它们是字段、枚举、槽位编码、认证和示例的唯一事实源：

- `references/CaseImportManifestV2.schema.json`
- `references/CaseImportManifestV2.example.json`
- `references/api-workflow.md`

不要使用或生成 `CaseImportManifestV1`、V1 接口、来源审计字段、审核项或 `ReviewIssue`。`scripts/registry_cli.py` 只实现 V2 的清点、MinerU 辅助识别、拆分、组装、校验、四步导入和只读核验；不存在独立的文书版本同步步骤。

## 不可突破的边界

- 本地业务工作根固定为 `E:\文件夹\1、工作\2、产品科技联网\1、产品监督\案卷汇总`。该目录是后续案卷包、拆分结果和核验记录的唯一业务落点；不得在消防产品案卷信息登记系统代码仓库中创建或保留案卷原件、OCR 结果、清单、上传状态、规范 PDF 或核验产物。
- 固定目录结构为：`原始案卷/<项目编号>` 保存收到的案卷包及原件；`工作区/<项目编号>` 保存清点、OCR、拆分、`normalized/`、清单和上传状态；`工作区/核验记录` 保存已完成导入的核验摘要；`资料模板` 只保存可复用模板。不得用工作区替代原始案卷目录，也不得把原件移动、改名、覆盖或删除。
- 把原 PDF、ZIP、图片和本地提取结果视为本地证据。不得上传、转交或提供给外部审核模型；未确认信息只保留在本地，或在 V2 清单中按契约省略/填写 `UNKNOWN`。
- 默认优先使用 PDF 的电子文本层。扫描页只可用本机 MinerU 处理。外部 Zerox 需要用户对第三方传输作出明确授权；本版本默认禁用，当前任务不得调用。
- 文件名只能辅助定位。依据电子正文、页面内容、文号、日期和明确关联决定槽位；证据不足时不要猜测、不要上传。
- V2 有现有的 51 个固定文书槽位；每个普通槽位最多一个电子版和一个扫描件，现场照片只允许扫描件。其他附件使用独立的 `OTHER_ATTACHMENT`，不占用正式双版本槽位。
- 清单中的每个文件必须被一个 `documentSlots[].versions[].fileRef` 或 `otherAttachments[].fileRef` 引用；不得存在未关联文件。
- 同一来源需要映射到多个逻辑槽位时，为每个槽位复制出独立的规范 PDF，并在 `files` 中使用独立 `clientRef` 和 `relativePath`。不得让一个上传文件同时充当多个逻辑槽位。
- 上传会写入生产网站。当前消息已明确授权具体案卷包、目标网站和正式导入时，视为直接授权且不重复确认；否则只完成本地清单，并一次性展示精确对象、影响和验证方式后等待授权。任何冲突、哈希不一致、槽位不明或未确认内容均停止，不绕过校验。
- 网站认证只使用 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml`，不放在会被 CC Switch 完整替换的 skill 安装目录内。不得把账号、密码、会话 Cookie 或 CSRF 防护令牌写入 manifest、`upload-state.json`、日志、命令行参数或响应摘要。

## 本地认证配置

1. 仓库只提交空值示例 `references/admin-upload-config.example.toml`。真实文件固定为绝对路径 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml`；该目录独立于源仓库和安装副本，CC Switch 同步不会覆盖它。源仓库根和 skill 自身仍永久精确忽略旧位置 `/admin-upload-config.toml`，防止历史文件被误提交。
2. 初次使用先运行 `uv run --python 3.12 python scripts/registry_cli.py init-auth-config`。命令只在文件不存在时创建以下空模板，并把文件访问控制列表（ACL，即谁能读取和修改文件）收紧为当前用户可读写、Windows `SYSTEM` 可完全控制；已有文件不会被覆盖，只会重新收紧权限：
   ~~~toml
   [auth]
   username = ""
   password = ""
   ~~~
   账号和密码只由用户本人填写；不得在聊天、提交、测试夹具或终端输出中展示真实值。
3. `upload` 与 `verify` 通过 Windows `LOCALAPPDATA` 环境变量解析上述真实文件；默认路径和 `--auth-config` 覆盖路径都必须是绝对路径，任一父目录或文件是符号链接、联接等重解析点时拒绝使用。不要把账号或密码改成命令行参数。
4. CLI 先向 `POST /api/auth/login` 提交凭据，再用同一 `httpx` 客户端的 Cookie 容器请求 `GET /api/auth/session` 复核身份。必须收到会话 Cookie；登录与会话中的身份、认证方式、内外层 CSRF 令牌及大队绑定必须完全一致，`mustChangePassword` 必须明确为 `false`，否则停止。
5. `ADMIN` 必须不绑定大队；`BRIGADE` 账户的平铺与嵌套大队 ID/编号必须一致，且只能处理与 manifest `brigadeCode` 完全一致的大队。CSRF 令牌不写入客户端全局请求头；只有业务写请求逐次携带同源 `Origin`、客户端标识和 `X-CSRF-Token`，所有 GET 仅使用会话 Cookie。

## 本地整理流程

1. 将收到的案卷包和原件保存到固定业务工作根的 `原始案卷/<项目编号>`；只在同一工作根的 `工作区/<项目编号>` 创建工作目录，清点文件、页数和 SHA-256。保留原件不改名、不覆盖、不删除。
2. 读取电子文本层；对没有可靠文本层的扫描页，仅在本机调用 MinerU。保留页码与来源映射，无法识别时停在本地。
3. 依据 `references/document-classification.md` 将每一份可确认的规范 PDF 映射到一个 V2 槽位或其他附件；先完成所有文件关联，再编写 manifest。
4. 依据 `references/case-data-format.md` 和生产 Schema 组装 `CaseImportManifestV2`。用 Schema 校验 JSON，并逐项核对：包哈希、文件哈希、初查、可选复查、稳定引用、51 个槽位规则、双版本限制和无未关联文件。
5. 先生成本地清单摘要：项目编号、文件数、各槽位的电子版/扫描件、其他附件、未确认且未上传的内容。当前消息未直接授权具体案卷包和目标网站正式导入时，到此结束并一次性请求授权；已经明确授权时继续执行，不再次询问。

## 命令边界

- 使用 `uv run --python 3.12 python scripts/registry_cli.py inventory ...` 清点原目录或 ZIP；目录包哈希由相对路径和逐文件哈希确定。
- `ocr` 必须用一个或多个 `--relative-path` 明确选择扫描型 PDF/图片，输出必须位于 `工作区/<项目编号>` 内。它通过当前 `PATH` 中已核验的 PowerShell 7 读取 MinerU 的无 BOM UTF-8 包装脚本；不要改写全局 PowerShell、Node 或 PATH，也不要把已有可靠文本层的电子 PDF 批量交给 MinerU。
- `split` 的计划项必须写明原文件、起止页和 `工作区/<项目编号>/normalized/` 下的唯一 PDF 路径；脚本拒绝覆盖已有结果。
- `compose` 使用的本地 `case-data.files[]` 需要同时写 `sourceRelativePath`（清点或拆分结果）与 `relativePath`（上传相对路径）。包哈希只能来自 `inventory.json`。
- `validate` 与 `upload --dry-run` 都不写网站；正式导入必须显式使用 `upload --finalize`。导入完成后用 `verify` 逐项核对案卷、检查、产品、目录版本和下载文件哈希。
- `upload --dry-run` 保持完全离线，不读取认证配置。`upload` 与 `verify` 的原有参数保持兼容，并新增可选 `--auth-config`。
- `upload-state.json` 使用封闭的 V5 字段集：只保存目标、哈希、任务进度、角色、大队编号及不可逆身份摘要，不保存用户 ID、大队 ID、密码、Cookie、CSRF 或完整服务端响应。终结结果只白名单保存案卷 ID、布尔结果和计数摘要，命令最终也只输出摘要。状态文件只可位于 `工作区/<项目编号>`，用于防止误续传和误切换身份，不带 HMAC，也不是授权边界；真正的安全边界始终是服务端会话、CSRF 与大队授权。旧状态或额外字段一律拒绝用 `upload` 续传，可单独运行 `verify` 只读核验。

## V2 四步导入

具体请求顺序和停止条件见 `references/api-workflow.md`：

认证步骤不计入四步业务导入：先登录并回读会话，确认身份、大队范围、无需首次改密，并把 Cookie 与 CSRF 令牌只保留在当前进程内存。

1. 创建导入任务；
2. 流式上传已关联的规范 PDF；
3. 提交 `CaseImportManifestV2`；
4. 终结导入，由服务端进行 Schema 与语义校验并事务写入。

不得访问 `/api/v1`，不得添加第五步“版本同步”，不得将未关联来源文件作为附件兜底上传。导入完成后仅记录服务端返回的结果；不要额外提交本地证据、OCR 原文或未确认项给审核模型。

## 参考资料

- `references/CaseImportManifestV2.schema.json`：生产 JSON Schema、51 个固定槽位编码和版本限制。
- `references/CaseImportManifestV2.example.json`：最小完整示例。
- `references/case-data-format.md`：本地证据到 V2 清单的映射规则。
- `references/document-classification.md`：文件分类、版本选择和多槽位复制规则。
- `references/api-workflow.md`：V2 四步接口流程与停止条件。
- `references/admin-upload-config.example.toml`：仅含空值的本地认证配置示例。
