---
name: meifu-resumable-download
description: 通过 Meifu 分块缓存稳定下载任意 HTTPS 大文件，并在本机断点续传、校验和原子落盘。用户直接提供下载链接并要求下载大模型、模型、安装包、数据集或其他大文件，或明确表示本机网络不稳定、下载反复中断时使用；也供 ComfyUI 模型管理在完成类别和来源审查后调用。
---

# Meifu 断点下载

## 作用与边界

使用受控链路“来源链接 → Meifu 临时分块 → 本机 SFTP 续传 → 完整文件校验 → 原子落盘”。它不是公开网页代理，也不提供任意人可访问的转发接口。

- 仅处理当前用户直接提供或明确批准的 HTTP(S) 直链；默认仅允许 HTTPS。
- 要求绝对输出路径。也可用 `--storage-root` 加 `--target` 指定“存储根目录 + 安全相对目标”；相对目标不得是绝对路径或含 `..`，最终文件不能逃出指定根目录。
- 模型、安装包、可执行文件和依赖包优先要求官方 SHA-256。没有官方哈希时可计算结果哈希，但不能表述为“已验证来源”。
- 带签名参数或令牌的链接只能经 `--url-stdin` 传入；不能写入命令行、队列、日志、状态文件或 Git。
- 拒绝 ProxyJump、ProxyCommand 和其他跳板；不把 Meifu 暴露为公共下载接口。
- 同一输出路径始终只允许一个传输。输出锁、队列工作者锁、清单写入租约和原子状态文件共同阻止重复启动、并发写入与旧快照覆盖。

## 后台运行方式

公共直链的大文件必须使用 `scripts/meifu_download_queue.py`：

1. `enqueue` 只写入本地持久队列，不连接 Meifu，也不启动传输。
2. `start` 只请求已安装的 Windows 计划任务处理默认队列；它绝不创建 Codex 的后台子进程。
3. Windows 后台任务才运行长传输，严格一次只处理一个文件和一个 Meifu 分块；断网时保留断点并按退避时间自动续传。
4. `status`、`list` 与 `audit` 只读取本地队列、锁、运行时版本和脱敏审计记录，不连接 Meifu。`list` 和 `audit` 默认每次只返回 20 项，避免把大队列完整回显到 Codex；用页码位置或精确条目编号继续查看。

多个任务需要共用队列时，任何调用方都只能通过受管命令写入，不能直接编辑 `queue.json`。每次写入会短暂取得单一写入租约，校验清单版本号后原子替换；租约忙或版本不一致时只会拒绝并提示重新读取，不会覆盖已有条目。工作者在每个文件开始与结束时重新读取清单，因此其他任务可以在下载期间安全追加、取消未运行条目或调整未运行条目的顺序。

所有会改变清单的操作都必须带稳定的 `--requested-by` 标识和可选的 `--request-id`；工具把“条目编号、公开来源链接、最终存放位置、请求方、顺序、状态和变更原因”写入有上限轮换的本地脱敏审计日志。相同“来源链接 + 输出位置 + SHA-256”重复入队会返回既有条目，不会重复下载；同一输出位置出现不同来源或哈希时安全拒绝。`remove`、`move`、`retry` 只能按 `list` 返回的精确条目编号操作；运行中的条目不能删除或调序。

计划任务不再直接引用会被 CC Switch 替换的安装副本。安装/更新脚本会将已核验的两个运行脚本复制到 `%LOCALAPPDATA%\MeifuDownloadQueue\Runtime\<哈希版本>`，记录哈希清单后再让计划任务引用该不可变版本。若运行时被意外删除或哈希不一致，工作者只把当前条目保留为待续传并安全停止，绝不把后续所有条目批量标记为失败。

计划任务只在当前用户明确授权后安装。安装、查看和精确卸载入口见 `docs/WINDOWS_TASK_CATALOG.md`。任务未安装时，`start` 会安全拒绝，不会退回到 Codex 进程树。

单文件 `download_via_meifu.py --execute` 只作为带签名链接或人工值守排障的例外：默认也会脱离调用终端，但不会获得持久队列的周期唤醒。不要在 Codex 当前会话使用 `--foreground` 跑长传输。

## 使用流程

1. 确认链接、输出位置、大小级别和官方 SHA-256。ComfyUI 模型先由 `comfyui-production-manager` 确认类别、版本、许可证和最终目录。
2. 用不带 `--execute` 的单文件命令检查脱敏来源和输出规则；需要验证 Range 支持、远端和本机空间时使用 `--probe-only`。
3. 用户直接批准下载后，对无签名公共直链逐项 `enqueue --requested-by <稳定任务标识>`，再一次 `start` 请求 Windows 后台任务。
4. 只用 `status`、`list`、`audit` 轮询。运行中重复请求会在创建子进程前返回“已有传输正在进行”。需要增删、重排或恢复条目时，先按页 `list` 或按精确条目编号查看，再使用精确条目编号和变更原因。
5. 完成后核对输出大小、SHA-256 和“来源已验证/仅计算哈希”状态；不自动执行、安装或导入下载文件。

常用调用形式：

    python scripts/download_via_meifu.py --url "https://example.invalid/file.bin" --storage-root "D:\aimodels" --target "speech\tts\model.bin"
    python scripts/download_via_meifu.py --url "https://example.invalid/file.bin" --output "D:\Downloads\file.bin" --probe-only
    python scripts/meifu_download_queue.py enqueue --url "https://example.invalid/file.bin" --storage-root "D:\aimodels" --target "speech\tts\model.bin" --sha256 <官方哈希> --requested-by "<稳定任务标识>"
    python scripts/meifu_download_queue.py start
    python scripts/meifu_download_queue.py status
    python scripts/meifu_download_queue.py list --limit 20 --offset 0
    python scripts/meifu_download_queue.py list --id <条目编号>
    python scripts/meifu_download_queue.py audit --limit 20
    python scripts/meifu_download_queue.py move --id <条目编号> --before <条目编号> --reason "用户确认的优先级调整" --requested-by "<稳定任务标识>"
    python scripts/meifu_download_queue.py remove --id <条目编号> --reason "用户取消" --requested-by "<稳定任务标识>"
    python scripts/meifu_download_queue.py retry --id <条目编号> --reason "运行时已修复" --requested-by "<稳定任务标识>"

签名链接不放入命令行：

    <将链接通过标准输入传入> | python scripts/download_via_meifu.py --url-stdin --output "D:\Downloads\file.bin" --execute

## 与 ComfyUI skill 的分工

- `comfyui-production-manager`：判断缺失模型、模型类别、许可证、目标目录、模型清单和显存影响。
- 本 skill：只负责通用传输、续传、缓存和文件完整性。
- ComfyUI 通过 `delegate_model_downloads.py` 把已批准候选写入本队列；不要再运行它的旧直连下载器或旧模型队列。

## Meifu 缓存维护

本 skill 只使用 `/root/.cache/meifu-downloads`。成功完成时立即清理本任务的本地暂存和远端目录；暂停、断网或重启时保留断点。

服务端清理器默认在任务无活动 72 小时后回收残留缓存；可用空间低于 8 GiB 时按最早无活动任务继续回收。旧 ComfyUI 缓存是单独的遗留范围，不由本 skill 新建、迁移或删除。详见 `references/transport-and-cache.md`。

## 资源

- `scripts/download_via_meifu.py`：通用单文件分块传输器；`--status` 只读轮询。
- `scripts/meifu_download_queue.py`：公共直链的持久单工作队列；`start` 只唤醒 Windows 后台任务，`list/audit/remove/move/retry` 提供可审计的共享清单管理。
- `scripts/install-meifu-download-queue-task.ps1`、`show-meifu-download-queue-task.ps1`、`uninstall-meifu-download-queue-task.ps1`：唯一后台队列任务的生命周期入口。
- `scripts/deploy_meifu_cache_gc.ps1`：部署或更新 Meifu 缓存清理器。
- `references/transport-and-cache.md`：传输、缓存、续传和安全边界。
