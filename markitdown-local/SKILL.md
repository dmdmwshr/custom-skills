---
name: markitdown-local
description: 使用本机 MarkItDown 快速提取有文字层的 PDF、DOCX、XLSX、PPTX 为 Markdown，作为简单文本提取或常用解析工具不可用时的替补。不用于扫描 OCR、排版复刻、编辑源文件或识别宏与公式语义。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# MarkItDown 简单文本替补

当前 Windows 固定版本 0.1.8，运行目录 `C:/Users/12070/.local/share/codex-tools/markitdown/0.1.8`。其他主机先核对安装。

用户只需正文或简单表格、需要快速本地提取，或常用文档解析入口不可用时采用。扫描 PDF 改用 Docling 或既有 OCR 流程；需要保留 Word/PPT 排版、Excel 公式/宏时继续对应文档技能。默认业务文档路线不因安装本工具而替换。

```powershell
& "C:/Users/12070/.local/share/codex-tools/markitdown/0.1.8/.venv/Scripts/python.exe" -X utf8 "$env:USERPROFILE/.codex/skills/markitdown-local/scripts/convert.py" --input "<本地文件绝对路径>" --output "<新Markdown绝对路径>"
```

脚本不配置云模型、不启用第三方插件，默认拒绝覆盖输出。检查关键文字、数字和单位；表格文本不证明合并单元格结构保留。空提取结果会返回失败，不生成伪成功文件。源文件保持。

官方依据：[Microsoft MarkItDown](https://github.com/microsoft/markitdown)。
