# 系统接口流程

工作流说明版本：`0.6.0`。

生产地址：`https://product-cases.meifu.zzxhlyj.top`

系统按既定决策没有账号、令牌或身份隔离。固定请求头 `X-Product-Case-Client: web-v1` 只是降低跨站浏览器滥用，不是凭据。

## 正式顺序

1. `GET /api/ready`
2. `POST /api/v1/import-jobs`
3. `POST /api/v1/import-jobs/{id}/files`，每个文件一个 multipart 请求
4. `PUT /api/v1/import-jobs/{id}/manifest`
5. `POST /api/v1/import-jobs/{id}/validate`
6. 用户确认后 `POST /api/v1/import-jobs/{id}/finalize`
7. 独立执行 `sync-document-versions`，处理相同包哈希已终结但新 manifest 增加正式版本的情况

以上“上传 → validate/finalize → sync-document-versions”是一个完整流程。`sync-document-versions` 不替代导入校验或终结，也不得在 finalize 前执行正式写入。

正式同步时脚本必须读取同一工作目录的 `upload-state.json`，核对 `finalized=true`、存在 `jobId`，并确认 `packageSha256` 与当前 manifest 相同；缺失或不一致立即停止。`--dry-run` 只做预览，不执行 PUT。

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

原 ZIP、原 PDF、组合扫描件、截图和重复副本上传后统一属于只读留档来源及 `fileLinks` 证据历史。前端栏目使用“文书与附件”；服务端逻辑文书只有 `ELECTRONIC`（电子版）和 `SCANNED`（扫描件）两个正式版本槽位，不设置第三种正式版本类别。

## 检查级文书关联

正式 `CaseImportManifestV1` 已支持 `caseInspectionRefs`。已确认归属的检查级文书输出一个父检查引用；`inspectionRefs` 只列该父检查下实际涉及的产品检查，可以在仅确定父检查时为空数组。两组引用必须与文书 `stage` 一致。阶段未知时省略 `stage/caseInspectionRefs`、输出 `inspectionRefs=[]`，并提交 `CaseDocument.stage` 的 `LOW_CONFIDENCE` 待核对项。

`ONSITE_PHOTO` 同样属于检查级文书，不能只凭文件名归入初查或复查。`TYPE_TEST_REPORT`、CCC 证书、技术鉴定资料等案卷/产品材料不输出检查引用。

失败时保留 `upload-state.json`，使用相同 manifest、原包哈希和幂等键续传。不得新建另一案卷来绕过冲突。

## 文书版本独立同步

四步导入任务对相同 `packageSha256` 会返回既有 `FINALIZED` 结果，不能依靠再次 finalize 回填后来新增的 `documents[].versions`。因此必须独立运行：

```powershell
uv run python scripts/registry_cli.py sync-document-versions `
  --manifest "<工作目录>\manifest.json" `
  --upload-map "<工作目录>\upload-map.json" `
  --api-base "https://product-cases.meifu.zzxhlyj.top"
```

命令执行顺序：

1. `GET /api/ready`；
2. `GET /api/v1/cases?projectNo=...`，只接受一个项目编号完全相同的活动案卷；
3. `GET /api/v1/cases/{caseId}/documents`；
4. 按 `stage + documentType + documentNo + issueDate` 完全匹配，必须且只能命中一个活动文书；
5. 相同 kind 的服务器文件 SHA-256 与本地一致时跳过；
6. 不同时调用 `PUT /api/v1/documents/{id}/versions/{kind}`，multipart 包含 `file` 与 `expectedDocumentVersion`；
7. 每次 PUT 后刷新文书，确保第二种版本使用新的乐观锁版本号；
8. 每个跳过、待处理、上传中、PUT 已受理、成功或失败结果都立即原子写入 `document-version-sync-state.json`。

同步前先拒绝本地清单中重复的 `stage + documentType + documentNo + issueDate`，避免两条本地文书连续覆盖同一服务端槽位。无匹配、多匹配或服务器文书版本号无效时不猜测，写入 `document-version-sync-state.json` 并报告待处理。PUT 或随后的刷新失败时写 `FAILED`、失败阶段、错误摘要以及“远端写入是否可能已成功”，保留此前成功项后返回非零；用同一 manifest 重跑时，相同哈希跳过，只补未完成槽位。
