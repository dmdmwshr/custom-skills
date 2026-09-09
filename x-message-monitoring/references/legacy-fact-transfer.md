# 历史传递兼容（维护时按需读取）

新周期使用 fast-path-runbook.md 中的同宿主标准输入客户端。本文件仅保存旧协议解释，不是新周期默认流程；不得自动回退、恢复旧事实或重放未知提交。

## 历史兼容：公开事实的跨工具内存传递（3.0.10）

CUA 与 functions 不共享变量，这是已知接口边界，不是停止合法传递的理由。以下对象都是当前可验证公开帖子事实，允许进入模型工具上下文。开始 acquire 前读懂并准备本节；不等到二十分钟末尾才设计传递方式，不另建通道、正文文件或进程桥接。

预先在当前 CUA 通过公开 Node 标准库 `node:zlib` 取得 `gzipSync`，绑定已声明的 `xMonGzipSync`；原样工厂以 `vm.runInNewContext(source,{URL,TextEncoder})` 实例化。两项均为纯内存计算，不执行浏览器动作、进程或文件写入。该环境已实测 gzipSync 可用，不能改用未提供的 CompressionStream。工厂和压缩函数可跨轮复用；每次新 acquire 后必须重新 createCycle，并确认 mainDone/searchDone 为 false、mainPages/searchPages 为 0。

1. 草稿仍只调用一次 `draftStream`，保留已声明的 `xMonReplyDraft`（主帖变量同理）。CUA 用原固定工厂 `xMonDriver.packFactsGzip(xMonReplyDraft,xMonGzipSync)` 导出 `XMonitorFactTransferV1` 的 `gzip_base64` 对象，工具回传该对象一次。functions 用 `store("reply_base", <刚返回的完整对象>)` 原样保存；不手工展开重复字段、改正文或推测引用。packFacts 的旧字典仍兼容小载荷；实际回复积压使用已准备好的 gzip 压缩，不能再次转抄约四万字符的旧字典。
2. 立即经原 `--input-framing chunks`、InputReady/echo_disabled 与 write_stdin 提交 `{lease,payload:load("reply_base")}` 到 context-plan。固定入口限制解压大小，核对长度、压缩完整性、唯一 JSON 键和规范 JSON 校验码，再还原原 XCollectedStreamV1；仍走原严格水位、身份与事实检查。此校验码只检测传递差错，采集与冻结原有哈希继续生效。
3. functions 可直接解析并保存固定入口返回的结构化计划。模型读取其中 context_items 判断必要上下文；传给 CUA applyContextPlan 的控制对象只需保留计划中的 schema_version、account、stream、draft_fingerprint、max_ancestors、max_quotes、new_status_ids，不重复传 context_items 正文。所有字段都取本次真实计划，不自己推断 new_status_ids。
4. 按既有流程补必要上文/引用。最后仅一次 `rawStream` 得到 xMonReplyRaw；CUA 导出 `xMonDriver.packFactsGzip(xMonDriver.diffFacts(xMonReplyDraft,xMonReplyRaw),xMonGzipSync)`，functions 原样保存 reply_delta。后续同一内存 collected 对象为 `{schema_version:"XMonitorFactTransferV1",representation:"delta",base:load("reply_base"),patch:load("reply_delta")}`。collect-stream、analysis-plan 的 payload 直接用它；scan-analysis 的 collected 也用它，不重新转抄整条时间线。已核验正文、身份、直接父对象和观察序列在差异中不可变；只有新上文/引用、采集时间和耗时可补充。
5. context-plan/analysis-plan 的可读新项事实是分析来源；相关/无关/无法判断按实际内容填写。批量分析可用 functions 的数组与公共字段机械组装严格对象，减少重复键名；不能以模板猜测结论或给已知项重新分析。空 new_status_ids 直接组装空 analyses，同一 functions 调用内可顺序完成标准入口调用，每一步仍检查成功回执。
6. 只传递元数据时回报条数、压缩前后字符数、校验码与阶段耗时；正文只进授权工具上下文/无回显 stdin，不进普通日志。任何校验或原验证失败都报告，不把传输包装当作成功采集。
7. 当前 lease 必须保留到同轮 heartbeat-finish 成功或明确失败回执之后才清理。时间到期、发布异常或浏览器关闭后也先用同 token 完成既有失败收口；不能先清空 lease 再声称无法 finish，不从历史取回 token、不用 renew 绕过整轮二十分钟上限。

### 历史兼容：工具间固定交接（当前事实优先使用上节直传）

1. `nodeRepl.write(xMonRaw)` 返回本流结构化公开事实。不是 HTML，也不写文件；模型此时只搬运事实，不翻译历史状态。将该原样对象与本轮 lease 放入 `functions.store("x-monitor-current", {lease,payload:<该对象>})`，只放一个账号的一条流。
2. 准备好内存对象后，以 `exec_command` 的 `tty:true` 启动同一项目解释器和固定入口：`--input-framing chunks collect-stream --input -`；命令只有固定路径/动作，不含正文。先等到 `XMonitorInputReadyV1` 且 `echo_disabled=true`；没有这条回执不发送数据。不要使用默认无 TTY 管道，它会立即关闭 stdin。
3. 在 functions 中以 `JSON.stringify(load("x-monitor-current"))` 得到帧内容，用 `Array.from` 按每 800 个字符分块；发送格式为第一行 `XMONITOR-JSON-CHUNKS/1`，随后每块一行、行首加 `+`，最后独立一行 `.`。通过原进程的 `write_stdin(session_id,chars)` 输入，一次发完。入口在读取前关闭输入回显，180 秒超时自动退出；输入正文不会回显到终端输出。
4. 同一方式调用 `analysis-plan`，直接复用同一个 functions 内存对象。它返回机器验证后的 analysis_items；只分析这些新项。随后 `scan-analysis` 输入对象为 `{lease,payload:{collected:load("x-monitor-current").payload,analyses:<新项分析>}}`，不重复手工转写 raw。
5. 每次入口读取一帧后就退出。只认最终业务回执；InputReady 不代表 collect 或 scan 成功。切换流/finish 后将 `x-monitor-current` 清空，不落盘，不另建常驻程序，不使用独立 Playwright 或内部浏览器接口。

此通路使用现成终端工具的标准输入，不是新增网络桥接。正文禁入命令参数/日志的约束不禁止上述受控事实工具输出与 stdin；不得再以“没有无正文通路”为由跳过已通过浏览器验证的主帖。
