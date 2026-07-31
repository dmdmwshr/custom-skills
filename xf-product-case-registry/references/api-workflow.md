# 系统接口流程

生产地址：`https://product-cases.meifu.zzxhlyj.top`

系统按既定决策没有账号、令牌或身份隔离。固定请求头 `X-Product-Case-Client: web-v1` 只是降低跨站浏览器滥用，不是凭据。

## 顺序

1. `GET /api/ready`
2. `POST /api/v1/import-jobs`
3. `POST /api/v1/import-jobs/{id}/files`，每个文件一个 multipart 请求
4. `PUT /api/v1/import-jobs/{id}/manifest`
5. `POST /api/v1/import-jobs/{id}/validate`
6. 用户确认后 `POST /api/v1/import-jobs/{id}/finalize`

创建任务需要：

- `Idempotency-Key`：由原包 SHA-256 稳定生成；
- `sourceType=LOCAL_SKILL`；
- 包名、容器类型、包哈希、哈希方法和 extractor 版本。

文件 multipart 字段：

- `file`
- `relativePath`
- `storageKind`
- `sha256`
- PDF 时的 `pageCount`

失败时保留 `upload-state.json`，使用相同 manifest、原包哈希和幂等键续传。不得新建另一案卷来绕过冲突。
