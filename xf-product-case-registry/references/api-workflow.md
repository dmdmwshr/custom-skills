# V2 四步导入接口

飞牛是案卷正文的唯一长期存储库。Meifu 仅作为飞牛断线时的临时接替或下载中转；默认核验只读取目录和飞牛落盘证据，不自动取回正文。只有显式 `--deep-content-verify` 才调用取回接口并下载校验，不代表建立备份副本。

本流程只适用于现有网站 V2。所有业务请求位于 `/api/v2`；V1 已退休，不提供兼容或迁移路径。

## 前置条件

- 本地已依据 `CaseImportManifestV2.schema.json` 校验 manifest。
- 所有 `files` 都是规范 PDF，均有 SHA-256，且每个文件都已关联一个正式槽位版本或一个 `OTHER_ATTACHMENT`。
- 原始证据、OCR 原文、未确认字段和人工核对笔记留在本地，不随请求上传。
- 案卷原件固定保存于 `E:\文件夹\1、工作\2、产品科技联网\1、产品监督\案卷汇总\原始案卷/<项目编号>`；本次清单、规范 PDF 和上传状态只位于同一工作根的 `工作区/<项目编号>`，不得写入系统代码仓库。
- 用户已直接授权当前案卷包写入目标网站。
- 先用 `init-auth-config` 在绝对路径 `%LOCALAPPDATA%\xf-product-case-registry\admin-upload-config.toml` 创建空模板并收紧 ACL，再由用户本人填写。该稳定本地目录不属于 skill 源仓库或安装副本；路径含重解析点时 CLI 拒绝使用。不要把真实账号或密码传入命令行。

## 认证前置流程

1. CLI 用 `tomllib` 读取本地 `[auth]` 下的 `username` 与 `password`，通过同源 `POST /api/auth/login` 登录；失败时只报告 HTTP 状态，不回显响应正文或凭据。
2. CLI 保持同一个 `httpx.Client` 的 Cookie 容器，并立即请求 `GET /api/auth/session`；必须收到安全会话 Cookie。登录响应与会话响应的完整身份、`authMethod=SESSION`、用户内与顶层 CSRF 令牌必须一致，`mustChangePassword` 必须显式为 `false`。
3. `ADMIN` 必须没有任何大队绑定；`BRIGADE` 的平铺及嵌套大队 ID/编号必须一致，且 `brigadeCode` 与 manifest 完全一致。任一条件不满足立即停止，不自动改密或纠正身份。
4. CSRF 不进入客户端全局请求头。登录 POST 单独携带同源 `Origin`；认证后的每个业务写请求逐次携带目标站同源 `Origin`、`X-Product-Case-Client: web-v2` 和当前 `X-CSRF-Token`。GET 只携带 Cookie。密码、Cookie、CSRF 均不落盘、不写日志、不进入摘要。

## 固定顺序

1. `POST /api/v2/import-jobs`：使用包哈希和导入元数据创建或取得幂等导入任务。
2. `POST /api/v2/import-jobs/{id}/files`：逐个 multipart 上传清单中已关联的规范 PDF。文件哈希、引用或类型不一致时停止。
3. `PUT /api/v2/import-jobs/{id}/manifest`：提交 `CaseImportManifestV2`。不得提交 V1 清单、审核项、来源证据或未关联文件。
4. `POST /api/v2/import-jobs/{id}/finalize`：由服务端执行 Schema 与语义校验，并以事务方式写入案卷、检查、产品、文件和槽位版本。

## 停止条件

- 第 1 至 3 步返回冲突、哈希不一致、引用不存在、槽位不匹配或 PDF 不合规时，停止；不要创建替代任务绕过错误。
- 第 4 步失败时保留本地工作目录和服务端返回结果，修正本地清单后再由用户决定是否重试。
- 若服务端拒绝清单但任务仍处于可对账状态，修正仅涉及清单元数据且包哈希、文件投影、项目、大队和身份不变时，可重试同一任务；CLI 会先 GET 导入任务并逐项对账，服务端缺少可确认字段时停止，不猜测或重建任务，也不需要用户手删状态文件。
- 不得调用已退休的旧接口、旧 `validate` 接口或旧的独立版本同步接口。V2 的四步流程已经覆盖正式槽位版本写入。

## 本地安全门禁与核验

- `upload --dry-run` 只做本地 Schema、业务归属、PDF、页数和哈希校验，不建立网络连接。
- 正式写入必须显式增加 `--finalize`；脚本在写入前检查服务就绪状态和项目编号不存在。
- `upload-state.json` 采用封闭 V6，保存规范化文件投影（含本地实测页数和字节数）、不可变清单绑定摘要、进度、目标和不可逆身份摘要；不保存用户 ID、大队 ID或完整服务端响应。续传前必须 GET 导入任务对账；CREATED、UPLOADING、MANIFEST_RECEIVED 且绑定完全一致时会幂等重传全部文件，旧 V5、FAILED/404 或字段不足时拒绝续传。
- 服务端终结成功后，CLI 以服务端 finalizedAt 和白名单 resultSummary 写入“待核验”状态，再有限轮询目录中的 SHA-256、飞牛状态和 nasVerifiedAt。约 60 秒仍未满足时保留 `FINALIZED_UNVERIFIED`，提示稍后运行 `verify`；若摘要存在冲突、跳过项或 `created=false` 则保留 `FINALIZED_WITH_CONFLICTS`，只提示人工修正，不进入飞牛核验或 VERIFIED，也不会自动重建/重终结任务。
- `verify` 默认不下载 34 份正文，只核对目录 SHA-256、`remoteState=AVAILABLE` 和 `nasVerifiedAt` 落盘证据；若证据缺失或仍在处理中，报告“飞牛落盘核验中”，可稍后只读续验。显式增加 `--deep-content-verify` 后，遇结构化 `RECALL_REQUIRED` 才会携带当前认证会话的同源、客户端和 CSRF 写请求头调用 `POST /api/v2/files/{id}/recall`，随后只用会话 Cookie 轮询 `GET /api/v2/file-recalls/{recallId}`。`READY` 后重新下载；`PENDING`/`PROCESSING` 等待，`OFFLINE`/`FAILED`/超时停止并保留服务端任务，不把网络故障写成哈希不一致。
