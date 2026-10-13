---
name: docling-local
description: 使用本机 Docling 离线解析中文扫描 PDF、阅读顺序和表格，输出 Markdown 及保留页码、合并单元格信息的结构化 JSON。作为 Zerox/MinerU 不可用、结果不佳或要求本地处理时的替补；不负责编辑原文档。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Docling 本地解析替补

当前已验收 Windows；固定运行目录为 `C:/Users/12070/.local/share/codex-tools/docling/2.138.0`。其他主机先核对安装，不套用此路径。

默认文档流程仍遵循业务技能。需要离线扫描识别、表格结构，或既有解析工具不可用/效果不佳时使用本技能。MarkItDown 适合仅提取简单文字；Docling 不承诺还原原始排版。

先盘点页数、尺寸和已有输出，选少量关键页试算；复杂跨页表格须检查原页、数字和单位。调用固定解释器与本技能的解析脚本：

```powershell
& "C:/Users/12070/.local/share/codex-tools/docling/2.138.0/.venv/Scripts/python.exe" -X utf8 "$env:USERPROFILE/.codex/skills/docling-local/scripts/convert.py" --input "<PDF绝对路径>" --output "<新输出目录>"
```

可用 `--first-page 2 --last-page 4` 限定页码。脚本只读取本地文件，使用已缓存模型、CPU 四线程和中文 RapidOCR；不调用云模型。缺模型时报告缺项，不自动更换网络或下载。需补模型时依当前授权和官方模型来源单独处理。

成功后读取 `.md` 与 `.json`。Markdown 可能省略页脚并展开合并表头；页码来源、合并行列及页脚以结构化 JSON 和原页交叉核对。空文本、部分成功或数值错位不能标记为完整转换。原文档保持，默认拒绝覆盖同名输出。

官方依据：[Docling](https://github.com/docling-project/docling)、[转换配置](https://docling-project.github.io/docling/usage/advanced_options/)。
