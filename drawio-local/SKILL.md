---
name: drawio-local
description: 在本机生成、转换、布局和导出原生可编辑的 .drawio 文件：XML、Mermaid（28 类图）、CSV 导入、ELK 自动布局与正交避障连线、多页读写、形状/图标检索、PNG/SVG/PDF 导出（可内嵌 XML）。用户要 draw.io、流程图、架构图、泳道图、组织结构图、时序图、思维导图或导出图表时使用；仅需对话内示意图时不触发。取代已停用的 @drawio/mcp 本地 MCP，无需任何 MCP 配置。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# drawio-local

基于官方方案（[drawio 官方 Skill](https://github.com/jgraph/drawio-mcp/blob/main/plugins/codex/drawio/skills/drawio/SKILL.md) + [draw.io Desktop](https://github.com/jgraph/drawio-desktop) 命令行），按需调用，**不常驻服务、不依赖 MCP、不起浏览器**。产物先是原生 `.drawio`（可在 draw.io 里继续编辑），再按需导出。

执行平台：Windows。WSL 里的 Claude/Codex 经 `/mnt/c/...` 调用同一份 Windows 程序，不另装。

## 入口与前置

| 项 | 路径 / 版本 |
| --- | --- |
| draw.io Desktop（便携解压，当前用户，无注册表、无文件关联） | `C:\Users\12070\.local\share\codex-tools\drawio-desktop\current\draw.io.exe`（`current` 是指向 `32.6.1\` 的目录联接；升级时解压新版本目录并重指，旧版本目录保留作回退） |
| WSL 调用 | `/mnt/c/Users/12070/.local/share/codex-tools/drawio-desktop/current/draw.io.exe`，传给它的输入/输出路径必须是 Windows 路径（`wslpath -w`） |
| Node（脚本用） | Windows `C:\Users\12070\.local\share\codex-tools\node24\node-v24.21.0-win-x64\node.exe`；WSL 的 `node` 也可（脚本只用 Node 内置模块，Node ≥ 18） |
| 本 Skill 脚本 | 随 Skill 目录：`scripts/drawio_pages.mjs`、`scripts/search_shapes.mjs`、`scripts/repair_drawio.mjs` |
| 参考资料 | `references/xml-essentials.md`、`references/xml-reference.md`、`references/mermaid-reference.md`（取自 @drawio/mcp 1.7.0，Apache-2.0，随 Skill 本地保存，不需联网） |

CLI 一律带 `--disable-gpu`；无头导出/转换不会弹窗、不抢焦点。调用结束进程自行退出，仍应核对没有残留 `draw.io.exe`。找不到程序时先核对上表路径；确实缺失才改为只交付手写 XML 的 `.drawio`，并如实说明无法转换/布局/导出，不用其他浏览器工具代替。

```bash
DRAWIO="/mnt/c/Users/12070/.local/share/codex-tools/drawio-desktop/current/draw.io.exe"
W() { wslpath -w "$1"; }          # WSL 路径 -> Windows 路径
"$DRAWIO" --disable-gpu -x -f <格式> [选项] -o "$(W out)" "$(W in)"
```

## 工作流

1. **作者**：标准类型（流程、时序、类、状态、ER、甘特、思维导图、时间线、C4、饼图等）优先写 **Mermaid**（`.mmd`，语法见 `references/mermaid-reference.md`；首行类型关键字拼错会得到空图），需要精确定位、特定图标库（AWS/Azure/网络/P&ID…）、容器/泳道时写 **XML**（规则见 `references/xml-essentials.md`，层、标签、表格、深色等再读 `xml-reference.md`）；表格数据生成组织结构图等用 **CSV**。标签用用户的语言。
2. **落成原生 `.drawio`**：
   - Mermaid：`-x -f xml -o 图.drawio 图.mmd`，转换后删除 `.mmd`（转换器已自动布局，不要再加 `--layout`）。流程图想要 ELK 效果可在转换结果上再 `--layout verticalFlow|horizontalFlow`。
   - CSV：`-x -f xml -o 图.drawio 数据.csv`（draw.io CSV 导入格式：`# label:`、`# style:`、`# connect:`、`# layout:` 等指令 + 表头 + 数据）。
   - XML：直接写 `图.drawio`，**写完先跑修复遍** `node scripts/repair_drawio.mjs 图.drawio`（剥离图外文字、转义 `<` 和孤立 `&`、修正默认层自指父级；对合法文件无改动）。严禁 XML 注释；每条边必须有子元素 `<mxGeometry relative="1" as="geometry"/>`。
3. **布局（可选，对 XML/转换结果原地执行）**：`-x -f xml --layout <预设> -o 图.drawio 图.drawio`
   - 预设：`verticalFlow`、`horizontalFlow`（分层流程）、`verticalTree`、`horizontalTree`、`radialTree`（树）、`organic`（力导向）；精细控制传 JSON 数组，如 `'[{"layout":"elkLayered","config":{"elk.direction":"RIGHT"}}]'`（算法 `elkLayered/elkTree/elkRadial/elkOrganic/elkStress/elkBox`）。
   - **正交避障连线**（不移动任何节点、只重算连线绕开形状）：本机 32.6.1 实测须写 `--layout orthogonalEdge`；官方文档里的名字 `libavoid` 在这个版本返回 `Unknown layout`。流程/树预设已自带连线，不必再叠加。
4. **导出**：`-x -f png|svg|pdf -e -b 10 -o 图.drawio.png 图.drawio`。`-e` 内嵌 XML，导出文件仍可在 draw.io 里还原编辑；`-a` 多页 PDF，`-p N` 选第 N 页，`-s/--width/--height` 缩放，`-t` 透明 PNG。导出成功后按用户要求保留或删除中间 `.drawio`；双扩展名（`name.drawio.png`）表示含内嵌 XML。**Mermaid 不要直接导出 PNG**（`-e` 会崩），先转 `.drawio` 再导出。
5. **核验**：不要只看退出码。打开导出的 PNG/SVG 看实际内容（中文标签是否完整），或读 XML 核节点数、坐标、边的 source/target；多页文件用 `drawio_pages.mjs list` 核页数。
6. 默认只落文件、不自动打开。用户要求在浏览器里看时才生成 `https://app.diagrams.net/#R…` 链接（官方 Skill 的 Node 一行脚本），浏览器按全局默认用 Ego，不自动启动 Chrome/Edge 等备用；图内容会随链接片段交给 draw.io 网页，涉及保密图表先说明或改用本地文件。

## 多页读写（取代 list_pages / get_page / set_page）

```bash
node scripts/drawio_pages.mjs list  图.drawio                   # 序号、id、名称、约略字节
node scripts/drawio_pages.mjs get   图.drawio 0|页名|页id        # 输出该页 mxGraphModel（压缩页会解压）
node scripts/drawio_pages.mjs set   图.drawio 页名 新页.xml      # 只替换该页，保持原压缩状态；其余页不动
```

只支持 `.drawio`/`.xml`；`set` 的内容必须是单页 `<mxGraphModel>`（不含 `<diagram>`）；写前自动留 `图.drawio.bak-<时间>` 备份，结果写同目录临时文件再重命名。大文件先 `list` 再只 `get` 需要的页。

## 形状 / 图标检索（取代 search_shapes）

```bash
node scripts/search_shapes.mjs "aws lambda" 10
```

约一万个内置模板（云、网络、P&ID、电气、Cisco、Kubernetes、BPMN），结果不足时补充官方图标服务（品牌/通用图标）。返回的 `style` 可直接放进 `mxCell` 的 `style`。只用于行业专用/品牌/图示类图标，流程图、UML、ER、组织图用基础几何形状。首次联网取形状索引（≈4.8 MB，按 ETag 缓存在 `%LOCALAPPDATA%\drawio-skill\`）；`DRAWIO_ICON_SERVICE_URL=off` 关闭图标服务，`DRAWIO_SHAPE_INDEX_URL` 可指向本地索引文件。只发送检索关键词。

## 旧 MCP 功能对照

| 旧 `@drawio/mcp` 1.7.0 | 现在 |
| --- | --- |
| `open_drawio_xml`（含 `postLayout:elk`、`direction`、`routing:libavoid`） | 写 XML + 修复遍 + `--layout verticalFlow/horizontalFlow`；避障用 `--layout orthogonalEdge` |
| `open_drawio_csv` | CLI 直接把 `.csv` 转 `.drawio` |
| `open_drawio_mermaid`（含 ELK） | CLI 把 `.mmd` 转 `.drawio`，可再叠加 ELK 预设 |
| `get_drawio_reference`（xml / mermaid） | `references/` 本地三份文档 |
| `list_pages` / `get_page` / `set_page` | `scripts/drawio_pages.mjs` |
| `search_shapes` | `scripts/search_shapes.mjs` |
| 在浏览器里直接打开、`lightbox`、`dark` 参数 | 不再默认提供；需要时用上面的链接方式，或在 draw.io 里手动设置 |
| 新增 | PNG/SVG/PDF 导出（内嵌 XML、多页 PDF）；ELK/避障改为本机 Desktop 内置引擎，不再从 CDN 下载 |

## 边界

- 底层 `@drawio/mcp` 包（`...\codex-tools\node24\...\node_modules\@drawio\mcp`）可作为只读参考保留，本 Skill 不依赖它；不启动、不重新注册该 MCP。
- 只处理用户指定的目录；不覆盖已有 `.drawio`/导出文件（先确认或另起文件名）；不读取、不上传业务文档；不为导出图表向云端发送内容。
- 版本记录：draw.io Desktop v32.6.1（官方 `draw.io-32.6.1-windows.zip`，sha256 `41291ad0c06469c183b65793e7604dcb83a6d21d8e339a333790cb1d7576a2ed`），脚本和参考文档来源 `@drawio/mcp` 1.7.0 / jgraph/drawio-mcp（Apache-2.0）；归属见 `references/NOTICE.md`。
