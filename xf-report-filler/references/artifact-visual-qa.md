# 成品视觉与源文件不变验收

本流程是生成与源数据审计之后的独立完成门。生成脚本的 dry-run 或 review JSON 只证明内容审计，不自动证明视觉验收；视觉结果写入单独的 `visual_qa_receipt`，不能把“文件可打开”当成通过。

执行链固定为：正式生成前运行 `python scripts/artifact_visual_qa.py baseline ...`；完成 Word 全页渲染或 Excel 全工作表/打印页检查后，把轻量 inspection JSON 交给 `finalize`；最后运行 `verify` 只读回读当前成品和采用源哈希。旧收据不得覆盖，每次正式生成使用新的收据路径。

## 源文件基线

1. 在正式生成前，对本次实际采用的外部模板和源文件记录绝对路径、大小、修改时间与 SHA-256；生成结束后对同一组路径重新计算。
2. `sources_unchanged=true` 只表示同一采用源在本次生成前后哈希一致。外部模板与 Skill 快照哈希不同继续按模板策略记为 warning，并采用外部模板；两者不相等不是本门的 blocker。
3. 任一采用源在生成期间发生变化、消失或被替换，结果为 blocker；不得把生成物误列为源文件，也不得为取得一致哈希改写源文件。

## Word 成品

- `.doc` 先通过可验证的 Office/LibreOffice 路径转换为只读渲染副本，`.docx` 直接使用文档渲染工具；转换和渲染都不得覆盖源文件或成品。
- 渲染全部页面并按页码逐页核对，不抽样。固定检查页数、页序、意外空白页、裁切/重叠、页眉页脚、标题层级、编号、字体字号和段落缩进。
- `document_style.md` 规定的标题黑色优先于模板主题色。表格、图片、印章、重复表头和“数据行不跨页”等检查只在实际成品含该对象且模板有对应要求时适用；不为不存在的对象制造 blocker。

## Excel 成品

- `.xls`/`.xlsx` 不套用 Word 的段落、字体层级或“逐页 DOCX”规则。逐工作表核对值、公式、合并单元格、行列、隐藏状态和禁止公开内容；再检查打印区域、缩放、分页、页眉页脚及模板要求的重复标题行。
- 需要对外打印或归档时，渲染全部打印页或使用等价的全页预览；不只看活动工作表或第一个打印页。公式错误、截断列、意外空白打印页或源表被改写均为 blocker。

## `visual_qa_receipt` 最小字段

- `artifact_path`、`artifact_type`、成品 SHA-256；
- `source_baseline`：每个采用源的生成前/后 SHA-256；
- `sources_unchanged`；
- 渲染/预览入口及版本、总页数或工作表/打印页数；
- 已逐项检查的页面或工作表清单、适用/不适用项、warning 和 blocker；
- `outcome=passed|blocked|qa_pending` 与观察时间。

渲染入口不可用、页面/工作表未全部检查、收据字段缺失或结果无法回读时，标记 `qa_pending`，只能报告“成品已生成，视觉验收未完成”，不能宣称最终验收通过。

inspection JSON 至少提供 `renderer`、`renderer_version`、`total_units`、`checked_units`、`checks`、`warnings` 和 `blockers`。Word 的 `checked_units` 必须按 `page-1` 到 `page-N` 连续列出；Excel 使用 `sheet:<名称>` 和需要时的 `print-page:<名称>:<页号>`。所有检查必须为 `passed` 或 `not_applicable`；失败项写 `failed` 或进入 blockers。
