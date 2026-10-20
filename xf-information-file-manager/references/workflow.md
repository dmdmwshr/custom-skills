# 台账和命令

基础归档只使用Python标准库；实时PDF启用后使用应用提供的Python、pypdf、pdfplumber、reportlab及Pillow，Word副本由本机既有LibreOffice转换，不向系统Python安装依赖。所有文本 UTF-8，用户阅读的 TXT 使用 UTF-8 BOM。

`scripts/file_manager.py --root <业务根目录> inventory` 只读输出材料数量。

`preview --manifest <候选台账>` 只读校验并输出预计变化；`apply --manifest <候选台账>` 执行；`verify` 核验正式台账与固定目录快照；`recover --transaction <事务目录>` 恢复失败事务。

## 候选台账接口（schema_version=1）

顶层包含 `documents`、`issues`（中文待核实事项字符串列表）、`changes`（本次人工核实的简要变更说明，一项一行）、`coverage_sources`（本次全量覆盖原始材料列表，可为空但不能删除已覆盖材料）。路径均相对业务根目录，禁止越界及符号链接。记录中不得写入密钥。

`changes` 直接用于面向读者的 TXT，参考“第一部分——某文件——已更新为试行版，见某号文”。只写主要新增、替代、作废或排序变化，避免路径、校验值、内部编号和长篇审核过程；详细依据保存在文书 evidence，缺口放 issues。未提供 changes 时，脚本根据实际入编差异生成简要说明。TXT 后附按三部分排列的最新主文件目录，不展开附件、电脑文件树，也不伪造书籍页码。

每个 document 包含：

- `id`：稳定唯一编号，与汇编可变序号无关。
- `title`、`issuer`、`level`、`topic`、`business`、`date`（YYYY-MM-DD或空）、`number`（无文号为空）。
- `short_title`：用户明确要求的可选短名，仅控制档案名、汇编名及当前目录显示，仍保留原层级和主题标签；不再自动追加文号。正式全称继续保存在 `title`，文号、稳定 id 和文件内容校验值不变，排序仍按正式全称计算。后续候选台账必须保留该字段，避免下一次更新恢复长名。
- `archive_category`：1—5；`section`：1—3，表示汇编三部分。
- `status`：现行、历史留存、征求意见稿、已作废、已替代、待核实、临时材料之一；`included`：是否进入实时汇编。
- `replaced_by`：新文书 id 列表；`evidence`：明确的正文或用户更新要求依据。
- `members`：成员列表，每项 `source` 为现存文件相对路径、`sha256` 为原件内容校验值、`role` 为 main 或 attachment、`name` 为附件规范名（主文忽略）、`in_compilation` 指该格式是否进入实时汇编。`sources` 可列所有重复来源。

有多个附件或文件格式时，同一文书只排一个汇编目录序号。不同内容且同名的版本自动加短校验值后缀；保留每项的 `archive_path` 与 `compilation_path` 输出。脚本不负责从正文自动猜测这些业务字段；代理负责阅读并填写，脚本负责确定性校验、排序、操作和记录。

`status` 为“已作废”或“已替代”时，脚本自动给档案文件或文书文件夹及主文件添加 `【作废】` 前缀，必须有明确 evidence；“已替代”还必须填写有效 replaced_by。前缀不进入 title、canonical_name，不影响正常文件的排序。归档状态不能靠文件名推断。仅取消收录设置 included=false，不更改有效状态；旧版被替代须保留旧文书记录，并另建或关联新文书，不把旧成员从台账删除。

用户明确迁移位置后，可在候选台账增加 `protected_relocations` 对象，键为旧目录相对前缀，值为新目录相对前缀。脚本只更新保护记录，不移动任何专项文件；每个历史文件必须在映射后的保护范围内存在且校验值一致，映射重叠或碰撞拒绝执行。成功后映射留在事务记录，正式台账移除此临时输入字段并采用新保护快照。当前保护范围包含汇编根目录下除实时版之外的专项，以及根目录所有“9、”开头的独立业务目录。

`coverage_sources` 每项为 `path` 与 `sha256`；每个原始材料校验值必须出现在新台账中。候选台账还必须保留正式台账已有的每个文书 id 和历史成员校验值。同一文件归入不同文书允许，但不应仅因文件改名创建新文书。

## 状态与异常

preview/apply 拒绝：受管根目录丢失、路径越界、重解析点、校验值变化、重复文书 id、缺少主文、无效分类、汇编未选择主文、替代目标不存在、未保留旧档案内容、目标路径碰撞。缺元数据可保留空值，但应写入 issues。

事务位于 `归档管理/事务/<版本号>`。prepare 阶段生成完整目标文件并校验；backup 阶段复制全部受管目录和管理记录；apply 阶段先替换档案目录再替换汇编材料目录；最后写台账、TXT和成功回执。失败不生成成功版本，并保留事务、备份和阶段。恢复只处理该事务记录的受管目录与管理记录，固定汇编和流程梳理始终只读。

正式台账是下一次候选台账的起点，成员 source 会更新为稳定档案路径，保留原始 sources；不要继续依赖已移动的输入路径。预览本身不写文件；需要留存时可将标准输出保存至业务管理目录。

## 文件名缩短与历史事务存储

跨 Windows 与 NAS 同步时，中文名的 UTF-8 字节长度可能超过对端单个名称的限制。用户已要求缩短名称时，在候选文书设置 `short_title`，不要把正式全称、文号或旧来源直接覆盖成简称。

为防止新事务备份重新生成旧长名，候选台账可带 `snapshot_relocations`：旧逻辑相对路径到短名存储相对路径的对象。同时明确目录及其内主文件的映射；只允许在同一受管目录内改名，目标必须与新布局的原件校验值一致，不得碰撞、不可逆或仍超过 255 UTF-8 字节。apply 的 before 与 replaced 使用短名物理存储，事务的 before 逻辑索引、管理记录原件和历史校验值保留；recover 按映射恢复原始逻辑布局。映射保存在事务记录，不留在成功提交的正式台账。

已完成历史事务中的长名副本使用 `migrate-storage --transaction <事务相对路径> --relocations <映射JSON>` 整理。此命令只改 before/replaced 的存储名称，先校验全量内容和管理记录、备份原 transaction.json，再记录映射和迁移回执；不重放业务、不改历史版本状态和原逻辑路径。失败事务禁止整理。完成后按 `scan_snapshot` 对逻辑清单和原哈希核验；原始 transaction.json 备份与迁移回执保留在原事务中。


## 实时PDF接口与事务

正式台账仍为schema_version=1。compilation_pdf_required=true表示本项目已经启用PDF维护，后续候选不得取消。旧台账不含此字段时基础归档照常兼容。

每件included=true的文书必须有book_selection.reviewed=true及parts数组。每个part以member_sha256绑定本件已收入实时版的成员，首个part必须为实时选定的唯一main；独立制度、指南或指标表可作为后续part，不能只合并通知。part包含page_count、keep_ranges（1起始闭区间列表）、excluded_ranges（ranges及reason）、evidence；保留与排除页段必须完整覆盖转换PDF且不重叠。可选crops以页码字符串为键，值为[左、下、右、上]归一化坐标，仅裁派生页。excluded_members逐项保留未采用成员的校验值及原因，不从档案删除任何成员。

命令：`--root <D盘业务根> prepare-book --manifest <候选台账> --workdir <本批临时目录>`；可用--soffice显式指定现有转换入口。workdir必须在业务根之外。生成失败写build-failure.json，保留转换日志和中间材料；不修改正式台账。成功将compilation_pdf_candidate写入候选台账，包含候选文件绝对路径和构建记录。每次authoring和视觉检查遵循当前官方PDF技能；本命令的结构核验不代替视觉验收。

prepare-book先重排候选布局，核对成员、页段与源文件校验值后构建。PDF优先合并原页面，Word转换只读取短名副本，采用独立file:///配置目录；混合正文/表单页采用局部高分辨率派生裁图，防止裁后仍带隐藏表单文本。缓存绑定原件和转换PDF双校验值，异常不盲覆盖。

compilation_pdf正式记录包含schema_version、profile、revision、path、sha256、input_fingerprint、document_count、page_count、toc_pages及documents（每件id、section、sequence、首个PDF页、连续正文起始页、页数、原件及转换校验值、选页依据）。输入指纹覆盖文书顺序、显示名称、文号日期、参考状态、所选正文内容和页段；不包含无关附件或运行目录。仅附件变化时prepare-book返回reused，apply沿用原PDF并说明。新正文、名称、排序、收录或选取变化但未prepare时，preview/apply拒绝提交，不能沿用旧PDF。

apply把候选PDF与材料一并暂存校验，before保存登记旧PDF；写入阶段旧PDF移至replaced，新PDF原子改名进入实时根部，再核验并写台账、目录、说明及成功回执。只处理记录中的精确PDF路径；目标新出现未知文件时拒绝覆盖。事务记录book_before/book_after，失败recover检查新改动与备份完整性后保留失败现场，并恢复材料、PDF和管理记录。已成功提交事务不能用recover覆盖后续业务。

verify兼容旧账；启用后必须核对PDF文件校验值、输入指纹、29件或当前入编件数、顺序、起始页、可点击目录、书签和A4页面。模板变更时更新脚本PROFILE并重建，不将历史模板输出冒充当前版。验收和同步以当前实际成果为准，正常10秒以上延迟不作失败，D盘提交后先等15秒再检查E盘PDF及记录。

### 制作资料、日粒度名称与同日修订

新合成本命名为`网络安全和信息化工作文件汇编（无附件，YYYYMMDD修订）.pdf`，目录首页仅显示年月日；revision仍为北京时间完整时间。同日多次更新可采用相同正式路径，transition只允许校验值与正式登记完全一致的上一版占用目标；apply仍先备份并将上一版移入replaced，再原子发布，新出现的未知文件一律拒绝覆盖，recover兼容新旧PDF路径相同。旧带时分秒正式记录可读，不批量改名历史。

Word清理模块word_pdf.py保留原件及派生docx的文字、媒体和分节，仅统一白底、关闭高亮、阴影等装饰。转换器、清理profile、原件SHA-256共同确定缓存身份；旧doc采用原格式直接导出后清理派生PDF底纹，避免docx中转改变分页；docx在副本中清理再导出。成功缓存位于`归档管理/PDF制作资料/转换缓存/<身份指纹>`，保留original、body.pdf、conversion.json和转换日志，docx另存清理后的body.docx。正式PDF每个part可带conversion_assets及conversion_receipt_sha256；verify复核回执、文件指纹及rendered_sha256。缓存异常保留现场并停止，不以同名新文件覆盖。缓存是制作资料，不是业务台账；选页、收录及目录仍由唯一文件台账管理。
