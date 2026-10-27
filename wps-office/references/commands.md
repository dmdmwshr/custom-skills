# 28 项原生命令与证据层级

适用本机 WPS12.1.0.28802 / CLI1.1.0；帮助来自证据根 `evidence/help/<command>.txt`。本表依据帮助与本轮合成材料证据，不是官方外部API保证。冻结索引 27 份响应实际覆盖 26 种命令（pdf2word 两份），cad2pdf 另有 finish 回执，caj2pdf 未测。历史原生调用不等于新封装实跑或质量验收；独立验收以本任务正式回执为准。

本轮实际产物核验与视觉查看由Windows主代理执行；仓外规格准备者只读回执、帮助与结构化核验，未操作GUI、未自行声称视检。列内“本地”仅为帮助措辞，不代表断网实测或隐私保证；未明示项也不能推断为离线。

|命令|能力/输出|重要参数与差异|本轮证据与限制|云端/登录边界|
|---|---|---|---|---|
|word2pdf|Word/WPS→PDF|--range 页范围；--pdf-options显式值替代默认；--image-quality|2页、7关键数字完整；主代理已看分页|本机帮助未明示云转换；不作离线保证|
|excel2pdf|Excel/ET→PDF|--range是工作表序号，可逗号；--pdf-options|提取表导出1页，金额显示两位小数；OCR表已导出视检|未明示云转换|
|ppt2pdf|PowerPoint/DPS→PDF|--range幻灯片；--output-type slides/handouts/notes-pages/outline|两页回转内容保留；OCR演示已导出视检|未明示云转换|
|pdf2word|PDF→DOCX/DOC/RTF|--engine auto/ai/basic；扫描用--scanned true且必须--range|普通回转2页、4×4表格；扫描OCR关键数字保留但字体空格标点变化|scanned=true明确在线OCR；ai-fix=true为在线修复；普通engine帮助称本地引擎|
|pdf2excel|PDF→XLSX/XLS/ET/CSV|--multi-sheet；CSV单表；engine与扫描参数互斥|basic提取4列表格；金额是数值，尾零显示与文本比较分开|scanned=true明确在线OCR，扫描分支本轮未单独测|
|pdf2ppt|PDF→PPTX/PPT/DPS|--engine；--scanned及--range|basic得到2页、可编辑文字形状和原生表格；不推广全部PDF|scanned=true明确在线OCR，扫描分支本轮未单独测|
|pdf2txt|PDF→TXT|--range单页或单跨度；批次分号|已有文字、7数字和第2页结束标记；不保原排版表结构|未明示云转换|
|pdf2md|PDF→Markdown及资源|--engine；无scanned/ai-fix选项|正文＋extracted.assets/1.png；表为HTML table，标点有转义|engine帮助称本地；无网络实测保证|
|pdf2photo|PDF→图片|输出必须目录；--suffix jpg/jpeg/png；range/color/image-quality|帮助称逐页；实际2页→1张1122×3174长图，必须按产物回读|未明示云转换|
|pdf2imgpdf|PDF→图像型PDF|--range；--color color/blackwhite|2页图像型PDF成功；不等同安全脱敏或不可OCR|未明示云转换|
|pdfcompress|PDF压缩|--press-quality low/medium/high/best；细粒度ppi/quality/optimizer覆盖预设|high压缩1,796,974→187,355字节，约89.57%；2页7数字保留，图像有损|未明示云转换|
|pdfsplit|PDF拆分|--range必填；单输入逗号多输出，批次分号|2页拆为2个1页PDF；completed.output为目录|未明示云转换|
|pdfmerge|PDF合并|--output文件必填；range分号逐输入，禁止逗号；--add-odd-page|按输入顺序合回2页，7数字与第2页标记保留|未明示云转换|
|pdfwatermark|PDF文字/图片水印|--text与--image二选一；range/opacity/angle/alignment|本轮自加文字水印产出并查看联系表，原文数字保留|未明示云转换|
|pdfremovewatermark|PDF去水印|--fail-on-signature；--fail-on-no-watermark|本轮自加水印移除成功并看联系表；不声称任意水印均可去除|未明示云转换|
|pdfencrypt|PDF加密与权限|open-pwd/edit-pwd至少一个；allow/print-permission；源密码另给|已加密；错密码拒绝，正确密码打开2页；仅随机样本密码|帮助不要求特定在线权益；仍非断网证明|
|pdfinfo|PDF元数据|只支持password/json；没有output/timeout|回读page_count=2及is_scan_document=false；后者是引擎启发式|帮助明确本地PDF引擎，无后端转换；需登录、不需VIP|
|word2photo|Word→图片|--long-image；--dpi 72-600；range可逗号；默认png|2张分页图；completed.output是自动生成的子目录|未明示云转换|
|excel2photo|Excel→图片|按sheet；--long-image；dpi；range可逗号|1张工作表图；completed.output是子目录|未明示云转换|
|ppt2photo|PPT→图片|按slide；--long-image；dpi；range可逗号|2张幻灯片图；completed.output是子目录|未明示云转换|
|photo2pdf|图片→PDF|merge默认true且output必填；merge=false可逐图；overwrite默认false|命令成功，PDFium视觉良好/pdfplumber可开1页；pypdf报Boolean解析错，兼容性部分通过|未明示云转换|
|photo2word|图片→Word|suffix docx/doc/wps/rtf；merge默认false|OCR成功；7关键数字保留，破折号→一，字体/空格/缩放/换行改变；建意仍错|帮助明确cloud-based OCR；本次仅获授权合成图片|
|photo2excel|图片→Excel|suffix xlsx/xls/et；merge默认false|OCR成功并回导PDF视检，表格可见、7数字全；原排版/符号非无损|帮助明确cloud-based OCR；本次仅获授权合成图片|
|photo2ppt|图片→PPT|suffix pptx/ppt/dps；merge默认false|OCR成功并回导PDF视检，表格可见、7数字全；非AI错字校对|帮助明确cloud-based OCR；本次仅获授权合成图片|
|txt2pdf|UTF-8文本→PDF|A4默认；自定义width/height须同时给|成功产出文本PDF；非复杂排版能力验证|未明示云转换|
|pdf2cad|PDF→CAD ZIP|range必填；suffix dwg/dwt/dxf；multi-sheet|有成功回执，ZIP含DXF与相对images/JPG；回转链缺彩色图，内容验证未通过|帮助未明示云/本地，不能从后缀推断|
|cad2pdf|DWG/DWT/DXF→PDF|width/height配对；color；multi-sheet默认false|有成功回执；DXF回转PDF缺彩色图，改cwd重转仍缺图，内容验证未通过|帮助未明示云/本地|
|caj2pdf|CAJ→PDF|output；timeout默认300|仅帮助存在；没有可信合成CAJ输入，未执行、不记成功|帮助未明示云/本地|

## 共用约束与输出

多数转换帮助要求已登录并具有相应VIP权益，输入超过200MB会拒绝；不能凭本样本成功推断完整会员/AI权益或所有尺寸均通过。pdfinfo明确不需VIP；pdfencrypt帮助称不需特定在线权益，均按真实返回报告。

一般转换输出已存在时自动改名；photo2pdf另有显式overwrite参数，保持默认不覆盖。图片与拆分返回目录、单文件、自动子目录等形态不同。以JSONL中completed.output为实际产物根；pdfinfo则返回file/page_count/is_scan_document，没有output和status，不能用通用转换判断强行判失败。Markdown资源、CAD ZIP相对图片和全部拆分页均须交付。

range不是一个通用语法：PDF转换通常单页/单跨度；pdfsplit支持逗号多输出；pdfmerge仅分号逐输入；Office的表/演示/图片导出支持其帮助指定的逗号范围。批次密码/range分号与输入顺序、数量保持对应，不能交给shell解释。

## 质量限制和待补测

- 文字层Word↔PDF、压缩的2页/7关键数字/4×4表格已核；压缩图像有损，不能当成字节级无损。
- OCR保留关键数字，但破折号、表头空格、标点、字体、缩放和换行存在差异；“建意”未纠正，OCR不等同AI校对。
- CAD ZIP及相对图片必须成套保留。已在DXF同目录重跑新输出仍缺图，CAD链内容验收未过，不能建议只改cwd即可修复。
- 图片转PDF保留pypdf解析失败及PDFium视觉良好的分层结论，不改文件掩盖差异。
- CAJ缺可信输入未测。所有命令的完整选项组合、batch、超大文件、全部账号和长期行为未全覆盖。
- 原生编辑、保存重开、COM运行、AI校对、窗口隔离及权益面板均未覆盖。遵守当前用户对界面和实际运行的授权或暂停，不按历史样本擅自恢复。

## 非CLI能力的发现入口

桌面写作排版、表格公式图表、演示设计、文档比对/校对/翻译、图片辅助与AI写作/纠错/阅读可由产品目录发现，但本轮不据此发明CLI参数或宣称已验收。个人云空间、同步、历史、分享沿cloud-docs/kdocs；企业组织/应用API沿独立WPS365授权。

公开资料仅沿主代理已核目录作为来源：WPS产品PDF页 https://www.wps.cn/product/kingsoftpdf/ ，会员能力目录 https://vip.wps.cn/privilege_page/ ，会员协议 https://www.wps.cn/privacy/vipserve ，企业开发说明 https://open.wps.cn/documents/app-integration-dev/wps365/server/introduce 。这些链接不是wpscli正式API或自动化许可声明；目前仅以已安装内置程序和真实帮助为接口依据。

## 封装的逐命令选项

唯一可执行目录内嵌在 `scripts/wps_cli.py`；选项名去掉原生 `--` 后放入 options。output/json/密码由结构化字段生成。下面的值和帮助摘要取自冻结的原生帮助，未知选项拒绝。通用调用、数值/范围/互斥和新目录要求见 [运行契约](runtime.md)。

### word2pdf

`--output`（path）；`--range`（range）；`--pdf-options`（opt[,opt...]）；`--image-quality`（low|medium|high）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`ceabcbcbd21b78ebc84b518c1d9218349c884fa31c42b504b8dd5f83cc41405c`。

### excel2pdf

`--output`（path）；`--range`（range）；`--pdf-options`（opt[,opt...]）；`--image-quality`（low|medium|high）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`7d74477f7c8feddcbab0bf6695d25c4275fd2391b1bd38139115523fa6b177e4`。

### ppt2pdf

`--output`（path）；`--range`（range）；`--output-type`（type）；`--handout-layout`（n）；`--handout-order`（order）；`--image-quality`（low|medium|high）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`d96f0ae649f51f1c965a308b8fe5b3726e5bd1d29ddc22a31adfc604d750dfaa`。

### pdf2word

`--output`（path）；`--suffix`（docx|doc|rtf）；`--range`（range）；`--image-quality`（low|medium|high|best）；`--engine`（auto|ai|basic）；`--scanned`（true|false）；`--ai-fix`（true|false）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`25baea3216cdf6b2b3c31a64df6ee20b45ff0e339b1a91ccd1b73dcdbfeff1aa`。

### pdf2excel

`--output`（path）；`--suffix`（xlsx|xls|et|csv）；`--range`（range）；`--image-quality`（low|medium|high|best）；`--engine`（auto|ai|basic）；`--scanned`（true|false）；`--ai-fix`（true|false）；`--multi-sheet`（true|false）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`9d47900a6b0b99919429ea00b2bbe6efa1746ce29060d066d2d975cb3d6bb06a`。

### pdf2ppt

`--output`（path）；`--suffix`（pptx|ppt|dps）；`--range`（range）；`--image-quality`（low|medium|high|best）；`--engine`（auto|ai|basic）；`--scanned`（true|false）；`--ai-fix`（true|false）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`efd30a8d30f0c4d67480fb7c79d22109f8ede445c6a1141b9e3319a3700f920b`。

### pdf2txt

`--output`（path）；`--range`（range）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`461c95d0ef5a13c2970cf127b1e39c953ea09f3a2ac343b5a97785b96eb24bc0`。

### pdf2md

`--output`（path）；`--range`（range）；`--image-quality`（low|medium|high|best）；`--engine`（auto|ai|basic）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`ffffa368ea09d8405efbd5b133c6fbb8b4047ccf959a8b897e3b84d53e77faf0`。

### pdf2photo

`--output`（dir）；`--suffix`（jpg|jpeg|png）；`--range`（range）；`--image-quality`（low|medium|high|best）；`--color`（color|grayscale|blackwhite）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`a72749e0933034262ae10e445bbb27f2b049d96f4992725209d39cd1eed38792`。

### pdf2imgpdf

`--output`（path）；`--range`（range）；`--color`（color|blackwhite）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`9b3335513dd6a7931b53651c2c6369772e89956d6a7e5c17cd75e315ec56d643`。

### pdfcompress

`--output`（path）；`--press-quality`（low|medium|high|best）；`--color-img-quality`（0-100）；`--color-img-dest-ppi`（9-2400）；`--color-img-threshold-ppi`（9-2400）；`--gray-img-dest-ppi`（9-2400）；`--gray-img-threshold-ppi`（9-2400）；`--mono-img-dest-ppi`（9-2400）；`--mono-img-threshold-ppi`（9-2400）；`--optimizer-settings`（opt[,opt...]）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`f8a478ff963549cd3b843d79a1c7f10805c6aec12815cfb173b9e66cfce9b1ca`。

### pdfsplit

`--output`（dir）；`--range`（ranges）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`e4357addd12aa06eef5eaa4fb22ee8899fd7d113176b0a17e8a55248fb1fa76d`。

### pdfmerge

`--output`（path）；`--range`（ranges）；`--password`（pwds）；`--add-odd-page`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`5a37de3734a4d24585b3a88cd8776c5d7036f28b911f7f6d32f4a46d9a229557`。

### pdfwatermark

`--output`（path）；`--text`（text）；`--image`（path）；`--range`（range）；`--opacity`（0-100）；`--rotate-angle`（deg）；`--font-size`（pt）；`--text-color`（#RRGGBB）；`--lines`（n）；`--font-name`（name）；`--font-bold`（true|false）；`--scale`（factor）；`--h-align`（left|center|right）；`--v-align`（top|center|bottom）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`28996f1c08089e706b289e5ed0269e30cab1e0dbd636eff27fc20691931f0904`。

### pdfremovewatermark

`--output`（path）；`--fail-on-signature`（true|false）；`--fail-on-no-watermark`（true|false）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`f8136fbde297c5a43477bbd014004db3f0c7a3bc9b17896fc970cb9c904d7dbb`。

### pdfencrypt

`--output`（path）；`--open-pwd`（pwd）；`--edit-pwd`（pwd）；`--allow-modify`（true|false）；`--allow-copy`（true|false）；`--allow-annotation`（true|false）；`--print-permission`（full|degraded|none）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`5bd85ac735104da91159d748ae9dc8e51b27f7822578ac99b83210a555788c1b`。

### pdfinfo

`--password`（pwd）；`--json`。

帮助 LF 摘要：`a13b93259fca68cf9903e2c11f6b0ad909e64909b745267c980954df877ac6b7`。

### word2photo

`--output`（path）；`--suffix`（png|jpg|jpeg|bmp|tif）；`--range`（range）；`--long-image`（true|false）；`--dpi`（number）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`c8ee36128ef41972834bd08523bdb81ce9ae450ace99d788c72c9be096d0c256`。

### excel2photo

`--output`（path）；`--suffix`（png|jpg|jpeg|bmp|tif）；`--range`（range）；`--long-image`（true|false）；`--dpi`（number）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`ab09a6bc19e36510102bfa63afdeb4a056ea07b790ac4425eb25be8987f314dd`。

### ppt2photo

`--output`（path）；`--suffix`（png|jpg|jpeg|bmp|tif）；`--range`（range）；`--long-image`（true|false）；`--dpi`（number）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`688d0f421544f3775387937d6c31e50ccff0cf77a5afa759d1257dea15131786`。

### photo2pdf

`--output`（path）；`--merge`（true|false）；`--page-width`（mm）；`--page-height`（mm）；`--page-margin`（mm）；`--force-cover`（true|false）；`--overwrite`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`6f1e587fd7ecc21c2123cab15919e6e95aa2ab44df66c59ad955a5b47d8c6597`。

### photo2word

`--output`（path）；`--suffix`（docx|doc|wps|rtf）；`--merge`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`f6dcd8fe04c413da484fad04944fc194be57e0768eeed0f04306ef3afcb9530a`。

### photo2excel

`--output`（path）；`--suffix`（xlsx|xls|et）；`--merge`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`74dfb3c41e690c247753842e1be30a2a041ea490f910b7d1a694c1c2fe894943`。

### photo2ppt

`--output`（path）；`--suffix`（pptx|ppt|dps）；`--merge`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`7815be86cd125bc358d8c62e33459fdc0f1ef948975e14b49f86050f12979101`。

### txt2pdf

`--output`（path）；`--page-width`（mm）；`--page-height`（mm）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`783e45d4f483eb8d92e25ba3267534f1d7bc8514f1603f5c0b83ca8c6b2dbd02`。

### pdf2cad

`--output`（path）；`--range`（range）；`--suffix`（dwg|dwt|dxf）；`--multi-sheet`（true|false）；`--password`（pwd）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`f27a15ac84aad1691f514c2af3c4d899f502846a46c20ae09a4db43e834b1aaf`。

### cad2pdf

`--output`（path）；`--color`（color|blackwhite）；`--page-width`（mm）；`--page-height`（mm）；`--multi-sheet`（true|false）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`65982a6fc706d8e7000f3b7c00793d495d16a090da044166e1b479e38af36810`。

### caj2pdf

`--output`（path）；`--timeout`（seconds）；`--json`。

帮助 LF 摘要：`a7745cd7963e20e7dae59760758a49d628110cd0ffcb2aef1ed0e614a5b5f15e`。


## 本批新封装未覆盖

只完成静态/离线验证时，所有新封装真实原生转换、COM、窗口隔离、保存重开和 AI 均保持未测。自动改名、多输入、错误与超时的封装测试使用合成进程，不能推广为原生行为已验证。帮助中的“自动改名”历史上未实际观测；默认不覆盖由全新独占输出范围保证。
