# 手动下载交件与来源缺件口径

用户手动下载的案卷与自动采集共用唯一项目总账和后续整理、本地核验、归档流程。不能要求手动件重新自动下载，也不能倒填下载前基线、RWID、页面筛选或浏览器采集记录。

## 逐案接件

1. 限定用户指定目录，先列 ZIP、截图、大小、SHA-256 与图片原始尺寸；不按文件名或下载先后猜对应。
2. 查看原图，确认项目编号、单位、大队、初查/复查产品结果和文书目录可读；像素尺寸只是辅助，不能代替实际逐图辨认。保留完整、未经压缩的原始截图。模糊、裁切丢失关键字段或配对有歧义的单案暂停，其他已核清案可继续。
3. ZIP 内选一份可靠电子身份文书，按单位、文号及截图目录核对，保存条目名、条目摘要和正文对应值。单位名有差异时必须有同案文号、地点等明确证据并记录裁决；无法确认同案时不接收。
4. 形成 `ManualCaseIntakeV1` 核查单，以 `source accept-manual --review-json <核查单> --work-root <唯一业务根>` 接收。核查单包含 `projectNo`、`fields.projectNo/unitName/brigadeCode`、ZIP/PNG 的绝对输入路径与完整摘要；`screenshotReview` 记录 `readable=true`、项目/单位/大队、`dimensions` 和已逐图读清的 `readableFields`（projectNo、unitName、brigade、productResults、documentDirectory）；`documentMatch` 记录包内 PDF 的 `entryName/sha256/unitName/documentNo`，名称差异另存 `identityDifferenceResolution`。
5. 新案原件进入既有待处理目录，采用项目号和内容摘要命名，生成 `manual-intake.json`；保留下载目录的手动输入。接收模式明确为 MANUAL_PROVIDED，不伪造 SourceEvidenceV1 的 RWID 记录。后续 inventory、compose、`local-verify` 沿用统一入口，全部在本地完成，不上传服务器。
6. 已归档或已有不同来源包的项目只追加到 `原始案卷/手动交件/<项目>/<接件标识>`，状态 PENDING_COMPARISON；不替换原始包、不重置旧任务和归档。随后执行 `source resolve-manual --project <项目> [--intake-id <接件标识>] --work-root <业务根>`：按包内逐文件摘要与总账摘要仍一致的既有原始 ZIP（历史 V2 与本地完成案均适用）比对。相同或不含新增/变化文件的材料只标记 RESOLVED_IDENTICAL / RESOLVED_NO_NEW_MATERIAL，不重复处理；有新增或变化文件时进入 NEW_GENERATION_STARTED：新包和截图复制进待处理目录，总账回到 PENDING_ORGANIZATION，旧代际原件、旧工作区、旧归档、历史完成记录、人工字段和失败/未知状态全部保留（`source.previousGenerations` 记录被替代的包）。之后按常规 inventory → compose → `local-verify` 完成并归档为新代际，再运行一次 `resolve-manual` 得到 RESOLVED_COMPLETED。找不到可对账的既有 ZIP（BASELINE_MISSING）、原案仍在处理（BLOCKED_ACTIVE_CASE）或待处理目录被占用（BLOCKED_PENDING_OCCUPIED）时保持 PENDING_COMPARISON 并说明原因。整个入口可重复运行、可中断续接，不手改水位。`manualIntakes` 是唯一总账中的证据引用，不是第二份业务水位。

## 来源缺件与处理完成分别表达

下载包中缺少、错放、空白或对应不清的材料属于来源大队/监督系统材料问题，保留 `source.materialGaps` 和证据；不自动认定本地整理漏件，不为此反复下载、补造或整案重传。

默认缺件仍阻止归档。当前用户明确要求按现有有效材料收口时，可执行 `source acknowledge-gaps --project <项目> --reason <当前用户口径> --work-root <业务根>`。该动作只绑定当前缺口清单的摘要，记录来源责任和用户口径，不清除缺件、不修改业务字段、不跳过本地核验。缺口列表变化后原确认自动失效。

现有有效文书全部通过本地核验（摘要、页数、拆分链）、无冲突且原件保全后，可以归档现有材料；核验回执同时保留缺口清单和确认口径。总账仍显示 `materialsCollected=false`、`complete=false`，以 `availableMaterialWorkflowComplete=true` 和来源问题保留说明表示本地现有材料处理已收口。来源变化、文件身份变化及未完成核验仍阻止归档。没有用户明确口径时不得调用该入口。
