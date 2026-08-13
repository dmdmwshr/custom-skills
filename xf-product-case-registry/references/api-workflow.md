# V2 四步导入接口

本流程只适用于现有网站 V2。所有业务请求位于 `/api/v2`；V1 已退休，不提供兼容或迁移路径。

## 前置条件

- 本地已依据 `CaseImportManifestV2.schema.json` 校验 manifest。
- 所有 `files` 都是规范 PDF，均有 SHA-256，且每个文件都已关联一个正式槽位版本或一个 `OTHER_ATTACHMENT`。
- 原始证据、OCR 原文、未确认字段和人工核对笔记留在本地，不随请求上传。
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
- 不得调用已退休的旧接口、旧 `validate` 接口或旧的独立版本同步接口。V2 的四步流程已经覆盖正式槽位版本写入。

## 本地安全门禁与核验

- `upload --dry-run` 只做本地 Schema、业务归属、PDF、页数和哈希校验，不建立网络连接。
- 正式写入必须显式增加 `--finalize`；脚本在写入前检查服务就绪状态和项目编号不存在。
- `upload-state.json` 采用封闭 V5，只保存进度、不可逆身份摘要、角色和大队编号；不保存用户 ID、大队 ID或完整 `finalize` 响应。终结结果只保留案卷 ID、布尔值和计数白名单，命令只输出核验摘要。它只防止本地误操作，不带 HMAC；服务端授权才是安全边界。身份不同、旧版本或出现额外字段时，`upload` 拒绝续传，可单独运行 `verify`。
- 服务端终结成功后先记录“已写入、待核验”，再读取案卷详情、固定目录和每份文件进行哈希核对。网络中断后重跑只允许续做只读核验，不会再次终结任务。
