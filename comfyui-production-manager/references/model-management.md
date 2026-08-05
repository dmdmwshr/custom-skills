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

## 美服分块断点下载

默认链路为：

```text
官方 HTTPS 来源 → 美服 /root/.cache/comfyui-models 的 2 GiB 临时块
→ 本机 SFTP reget 续传 → 本地临时合成 → SHA-256 校验 → 正确模型目录
```

美服每个块会在本机校验、写入合成文件并登记进度后立即删除；完整模型成功落盘后会清理该任务的远端缓存目录。因此美服不需要容纳完整模型，可中转超过其剩余空间的大文件。中断时保留未完成的本地状态和当前美服分块，再次执行同一命令即可续传。

使用顺序：

```powershell
# 只显示请求、来源和落盘规则，不联网、不写入。
python scripts/stage_model_download.py --dependency-report "<工作区>\models\template_dependency_report.json" --model "模型文件名.safetensors"

# 只读验证来源是否支持 Range、来源哈希线索、美服空间和最终落盘位置。
python scripts/stage_model_download.py --dependency-report "<工作区>\models\template_dependency_report.json" --model "模型文件名.safetensors" --probe-only

# 用户明确批准后才会下载、传输、校验、登记和清理缓存。
python scripts/stage_model_download.py --dependency-report "<工作区>\models\template_dependency_report.json" --model "模型文件名.safetensors" --execute
```

下载器只接受 HTTPS、受支持的模型扩展名和安全的类别名。优先从 Hugging Face LFS 的 ETag 自动获得 SHA-256；没有可用官方哈希时默认拒绝写入。仅在用户明确接受来源校验风险时，才能传入 `--allow-unverified-source`。

需要登录或同意许可证的 Hugging Face 仓库，先由用户在模型卡完成授权，并在美服以受限权限配置令牌；不得把令牌写入聊天、命令行、工作区、下载报告或 Git。带签名查询参数的 URL 也不得写入可提交的登记文件。

## 登记、清理与删除

成功下载后，下载器会更新工作区 `models/catalog.json`，记录文件名、类别、模型根目录、相对路径、大小、SHA-256、脱敏来源、许可证核验状态、适用工作流、传输方式和验证时间。历史盘点和下载目录不纳入 Git。

删除模型前检查 `catalog.json`、项目 `manifest.json`、工作流依赖报告和 ComfyUI 日志。默认只提出清理建议；需要删除时，先移动到精确的可恢复位置，再验证没有引用，绝不按目录通配符批量删除。
