# ComfyUI 模型管理

## 本机模型位置与落盘规则

ComfyUI Desktop 的运行进程以
`C:\Users\12070\AppData\Roaming\Comfy Desktop\shared_model_paths.yaml`
为准，当前已配置两个模型根目录：

| 优先级 | 模型根目录 | 使用规则 |
| --- | --- | --- |
| 默认 | `D:\Comfy-Desktop\ComfyUI-Shared\models` | D 盘可用空间不少于 200 GiB 时，所有新模型优先落在这里。 |
| 备用 | `C:\Users\12070\Documents\ComfyUI\models` | D 盘低于 200 GiB，或 D 盘不足以容纳“模型 + 一个传输块 + 本地预留”时自动使用。 |

不要手改 Desktop 生成的 YAML，也不要将模型复制到项目目录。下载临时文件放在所选模型根目录同级的 `.model-download-staging/`，只有校验完成后才原子移动到 `checkpoints`、`diffusion_models`、`text_encoders`、`vae`、`loras`、`controlnet` 等精确类别目录。

```powershell
# 输出当前 Desktop 配置、两个根目录的可用空间和本次默认落盘选择。
python scripts/model_paths.py
```

## 盘点与模板缺口

先做两份只读报告：已安装模型清单，以及模板中声明的模型依赖。后者优先解析官方模板 `MarkdownNote` 中的模型直链和类别，不能可靠识别的项目标记为 `unresolved`，不得自动下载。

```powershell
python scripts/audit_models.py --root "D:\Comfy-Desktop\ComfyUI-Shared\models" --root "C:\Users\12070\Documents\ComfyUI\models" --output "<工作区>\models\model_inventory_current.json"

python scripts/audit_model_dependencies.py --workflow-root "<工作区>\workflows\模板库" --output "<工作区>\models\template_dependency_report.json"
```

依赖状态含义：

- `installed`：两个模型根目录中有唯一同名文件。
- `duplicate_installed`：同名文件存在多个位置，需要后续哈希审计，不能直接删除。
- `missing`：模板给出了唯一类别和 HTTPS 来源，可作为下载候选。
- `unresolved`：类别、文件名或来源不明确，必须先人工确认节点文档或模型卡。

模型名称相同不等于模型等价；禁止将 fp16/fp8、不同版本、不同训练集或不同 LoRA 静默替换。

## 下载路由与 Meifu 委托

ComfyUI 不再维护自己的远端缓存、SFTP 传输器或后台下载任务。先按以下规则选择传输方式，不把所有模型下载都送入 Meifu：

1. 来源可直连：无论文件大小，走本机直连下载，不经过 CN2 或 Meifu。
2. 来源必须经代理但文件小于 2 GiB：走 CN2 直连下载，不使用 Meifu。
3. 只有来源必须经代理且文件大于等于 2 GiB：委托通用 Meifu 队列。
4. 代理需求或大小不明：只做来源与大小探测，确认前不写入 Meifu 队列。

本机直连表示直接向来源站下载；CN2 直连表示本机经既有 CN2 网络链路直接向来源站下载。两者都不经过 Meifu。Meifu 委托链路的职责严格如下：

```text
ComfyUI：已批准候选、模型类别、模型根目录、下载后登记
通用 Meifu 下载器：HTTPS 分块缓存、SFTP 续传、哈希、原子落盘、单工作者与缓存回收
Windows 计划任务：在 Codex 之外运行通用队列，并在登录/网络恢复后唤醒它
```

只使用 `download_candidates` 中唯一类别、单一无签名 HTTPS 来源和安全文件名都明确的条目。带签名参数、需要登录或需要接受许可证的链接不能写入持久队列；先由用户完成授权并提供可安全持久化的来源，绝不把令牌写入聊天、命令行、工作区或 Git。

```powershell
# 仅对“必须经代理且大于等于 2 GiB”的候选做本地准备：按指定模型根目录逐项写入通用队列和 ComfyUI 委托清单；不联网、不启动传输。
python scripts/delegate_model_downloads.py prepare --dependency-report "<工作区>\models\template_dependency_report.json" --model-root "D:\Comfy-Desktop\ComfyUI-Shared\models"

# 仅在当前用户已明确批准、候选满足 Meifu 门槛且通用后台任务已单独安装后，才请求 Windows 后台任务开始队列。
python scripts/delegate_model_downloads.py prepare --dependency-report "<工作区>\models\template_dependency_report.json" --model-root "D:\Comfy-Desktop\ComfyUI-Shared\models" --start

# 只读查看模型委托和通用队列；不会连接 Meifu 或启动下载。
python scripts/delegate_model_downloads.py status

# 文件完成落盘后，计算最终 SHA-256 并更新 models\catalog.json；不会执行或导入模型。
python scripts/delegate_model_downloads.py reconcile
```

`--model-root` 是精确的存储根目录；省略时，脚本按 Comfy Desktop 的共享路径和空间规则选择根目录。只有已确认符合 Meifu 门槛的候选，其类别与文件名才会传给通用下载器作为安全相对目标，因此文件不能逃出该模型根目录。通用队列始终一次只处理一个文件和一个 Meifu 分块，并为同一输出路径设置锁，避免重复传输和并发写入。

`generic_meifu_model_downloads.json` 也只能由 `delegate_model_downloads.py` 写入，不能由多个任务手工合并。`prepare` 与 `reconcile` 通过同一短时写入租约串行化；提交前核对清单版本号并原子替换。若发现另一个任务正在写入、租约异常或版本已变，脚本会拒绝而非覆盖；应稍后重试或先运行只读 `status`。旧清单会在下一次受保护写入时自动补齐版本元数据。即使两个任务同时看到同一候选，通用队列的输出路径锁也会把第二次请求转换为“已入队/已占用”记录，而不会再次传输。

通用缓存根是 `/root/.cache/meifu-downloads`。完整校验与原子落盘后会清理自己的临时目录；断网、断电或临时上游错误保留本地状态和当前远端块，并以 15 分钟起始、最长 6 小时的退避自动续传。无活动超过 72 小时，或 Meifu 可用空间低于 8 GiB 时，通用缓存清理器会按最旧任务精确回收。它不会删除 ComfyUI 模型目录或其他缓存根。

唯一传输后台任务由 `meifu-resumable-download` 管理：

```text
\DevProjects\MEIFU\AUTO\DEV-MEIFU-AUTO-01-DownloadQueue
```

该任务在当前用户登录时和每 30 分钟唤醒一次，使用 GUI Python 启动器和 `IgnoreNew` 并发策略。Codex 的入队、启动和查询均为短操作；实际传输不在 Codex 的命令进程中运行。安装、更新、启停或卸载该任务必须获得当前用户明确授权。

原来的 `stage_model_download.py`、`model_download_queue.py` 和 ComfyUI 旧任务已退役：前两者只保留旧状态审计，传输型命令会安全退出；旧任务的停用或迁移不是本 skill 同步的一部分，必须单独授权后才执行。

## 登记、清理与删除

成功下载后，下载器会更新工作区 `models/catalog.json`，记录文件名、类别、模型根目录、相对路径、大小、SHA-256、脱敏来源、许可证核验状态、适用工作流、传输方式和验证时间。历史盘点和下载目录不纳入 Git。

删除模型前检查 `catalog.json`、项目 `manifest.json`、工作流依赖报告和 ComfyUI 日志。默认只提出清理建议；需要删除时，先移动到精确的可恢复位置，再验证没有引用，绝不按目录通配符批量删除。
