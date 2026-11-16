# 扫描件识别候选（scan-candidates）

只读本地命令，不写飞书、不改清单、不上云、不调模型：对初查记录 PDF 产出 `RecognitionCandidatesV1`，每个字段带页码、表格行列和 bbox（原 PDF 页面坐标，pt，已映射回旋转/倾斜前）。

```
document_tools.py exec -- env PYTHONPATH=<case-registry 环境 site-packages> python -B scripts/registry_cli.py scan-candidates \
    --pdf <文件.pdf> [--pdf …] | --project <项目编号> --cache-dir <缓存目录> --work-dir <中间图目录> [--out <结果.json>]
```

需要受管文档环境（numpy、Pillow、pdfplumber、pypdf、pdftoppm、tesseract chi_sim）；`registry_cli` 本身要 `jsonschema`，所以在文档环境里追加 case-registry 环境的 site-packages。

## 选路与规则（不依据已知答案调参）
- 每页文字层字符数 ≥20：`text-layer`，只给编号、日期和产品表单元格（带引用），不 OCR；产品行拆分与校验仍走现有电子记录解析。
- 否则 `scan`：页方向按“锚词得分”取 0/270/90/180；按表格外框估计倾斜；横竖线切单元格；表头列语义按字符集合覆盖度（整页读数＋单元格裁剪重读）。
- stage1 整页 OCR(psm4)，stage2 单元格/行裁剪重读(psm6 chi_sim+eng)：**两阶段规范化后一致才标 `accepted-candidate`，否则 `uncertain` 并保留两种读数，不择一**。表头未识别、无表格线、手写或质量不足的字段保持缺项/uncertain 并写原因。
- 扫描件结果一律是候选，须人工确认，不当已核验值，不覆盖监督事实或人工值。

## 缓存与异常
缓存文件 `<文件 sha256 前 16 位>-<解析器版本>.json`：同摘要同版本命中缓存；版本变化不用旧缓存且旧文件保留；失败(`unknown`)不写缓存，新版本失败时指向旧版本缓存。损坏/加密 → `blocked`（`file-damaged` / `password-required`），不索要或猜测密码。

## 实测边界（2026-11-16）
盲测结果与适用面见统一校准任务目录 `verification/a10/recog-port` 与交付报告：手写填写的扫描记录基本只有编号/日期可用，产品表字段多为缺项；wrong-accepted 在全部留出集合计为 0。接入夜间正常入口前须在更大盲测集再确认。
