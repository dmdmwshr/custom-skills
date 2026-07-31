---
name: applio-rvc-manager
description: 管理本机 Applio 与 RVC 音色模型，并为 ComfyUI 视频准备可复现的对白音频。包括安装、升级、启动、停止、状态核验，获取和验证 .pth/.index/ZIP，中文命名与目录管理，文字转角色语音、现有录音换音色、跨语言转换、按镜头切分与锁定音频、口型和音频驱动工作流交接，以及 GPU、内存、端口、模型不显示、下载失败或界面打不开等故障诊断。用户提到 Applio、RVC、声音转换、角色配音、ComfyUI 视频对白或音色模型时使用。
---

# Applio RVC 管理

## 核心原则

1. 先核验现场，再引用历史快照。运行 `scripts/audit-applio.ps1`，不要把旧 PID、端口占用或模型清单当作当前事实。
2. 本机安装使用 Applio 自带的 `env\python.exe`；不要向系统 Python、Conda `base` 或 Applio 内置环境随意安装、升级包。
3. 区分声音转换和语音合成：RVC 需要源语音并保留其内容、节奏和大部分语气；Applio 内置 TTS 使用联网 EdgeTTS 生成源语音，再接 RVC 形成角色音色。它不会自动翻译。
4. 下载前核对来源、模型卡、文件格式、授权和训练语言。真人或公众人物音色只在获得授权且用途正当时使用，不制作欺骗性冒充内容。
5. 将外部 `.pth` 视为不可信序列化文件。安装前用 Applio 内置 Python 运行 `scripts/inspect-rvc-model.py`，必须使用 `torch.load(..., weights_only=True)`。
6. 原始音频保存在 Applio 目录之外。不要把唯一原件放进 `assets\audios`，因为界面的清理功能会删除该目录内音频。
7. 停止进程时只处理已由安装路径或监听端口确认的 Applio PID，禁止按名称批量结束所有 `python.exe`。
8. 区分“最终对白”和“音色参考”：最终对白直接决定视频口型和时长；音色参考只让视频模型模仿声音特征，不能保证沿用 Applio 的精确音色、节奏或波形。

## 任务路由

- 本机部署、启动、停止、升级或状态问题：先读 `references/local-installation.md`，再运行只读审计脚本。
- 获取、安装、改名、迁移、备份或清理模型：读 `references/model-management.md`。
- 文字转语音、中文转日语角色、说话或唱歌参数、长音频转换：读 `references/inference-guide.md`。
- 为 ComfyUI 制作对白、选择音频驱动工作流、管理镜头音频或排查口型错位：读 `references/comfyui-video-handoff.md`；需要调整 RVC 参数时再读 `references/inference-guide.md`。
- 界面打不开、端口冲突、模型不显示、显存或内存异常、下载失败：读 `references/troubleshooting.md`。

## 标准工作流

### 1. 审计部署

在普通用户 PowerShell 中运行：

```powershell
& "<skill-dir>\scripts\audit-applio.ps1"
```

检查安装根目录、版本、启动入口、内置 Python、Applio 进程、监听端口、GPU、模型配对和磁盘占用。除非用户要求变更，否则只报告结果。

### 2. 获取与安装模型

1. 明确用途：中文口语、跨语言角色音色、唱歌、实时变声或训练。
2. 优先选择带模型卡、训练语言、试听、`.pth + .index` 和清晰许可的来源。
3. 将原始 ZIP 或文件放入 `model_packages\<日期>\`，不要直接下载到 `logs`。
4. 记录来源 URL、下载日期、SHA-256、训练语言、RVC 版本和许可状态。
5. 检查 ZIP 不包含越界路径，只提取需要的 `.pth`、`.index` 和说明文件。
6. 使用安全检查脚本验证 `.pth`；如有 `.index`，同时用 FAISS 验证。
7. 安装到 `logs\<唯一 ASCII slug>\`，模型和索引放在同一目录；Windows FAISS 可能无法读取含中文路径的 `.index`。
8. 将角色中文名放在 `.pth` 显示文件名和模型清单中；`.index` 文件名和完整目录路径保持 ASCII。
9. 在 Applio 中刷新模型列表，确认模型与索引自动配对，再做 10–20 秒短音频测试。

安全检查示例：

```powershell
& "D:\Program_Files\Applio\env\python.exe" `
  "<skill-dir>\scripts\inspect-rvc-model.py" `
  --pth "D:\Program_Files\Applio\logs\示例\example.pth" `
  --index "D:\Program_Files\Applio\logs\示例\example.index"
```

### 3. 制作视频对白

1. 从 `assets/applio-comfyui-audio-record.md` 复制项目记录模板，先冻结分镜号、说话人、台词版本和目标工作流。
2. 有表演录音时走 `干声 → RVC`；只有文字时走 `文字/UTF-8 TXT → EdgeTTS → RVC`。
3. 一次只测试一个镜头和一个说话人；使用 10–20 秒样本确定模型、Pitch、Embedder、Index Rate 和 Protect。
4. 每个说话镜头导出独立 WAV，记录模型与音频 SHA-256。锁定文件不得原位覆盖，修改时增加版本号。
5. 根据 `references/comfyui-video-handoff.md` 将音频标记为 `final_dialogue` 或 `voice_reference`，再选择对应 ComfyUI 工作流。
6. 项目目录保存母版；ComfyUI `input` 只放可重建的暂存副本。视频生成后按镜头核对口型、时长、说话人和音色。

### 4. 转换与验收

1. 使用干净、无背景音乐、无混响的源人声。
2. 先做短样本，固定源音频，对音高、索引比例、辅音保护和嵌入模型进行 A/B 测试。
3. 跨语言模型先验证中文声母、韵母、声调和气声；不能因音色相似就判定可用于整段视频。
4. 长音频按语义停顿切段，段间保留少量上下文，最后统一响度并检查断句。
5. 保留源文件、参数、模型哈希和输出文件，确保结果可复现。

### 5. 变更和升级

1. 升级前备份 `logs`、`assets\presets`、自定义嵌入模型及要保留的音频。
2. 记录当前版本、内置 Python、PyTorch、CUDA、模型清单和启动方式。
3. 优先按官方完整安装包升级，不覆盖式替换 `env`。
4. 升级后验证 GPU、端口、模型扫描、一次短推理和输出路径。
5. 安装包、模型包或旧版本清理属于实质删除；先列出精确路径、大小和恢复方式，再执行。

## 结果报告

报告至少包含：

- 安装版本、根目录、启动方式、地址和运行状态；
- 新增、保留或失败的模型及其目录；
- `.pth/.index` 配对、安全加载、哈希和 Applio 可见性；
- 模型来源、训练语言和授权不确定性；
- 测试所用文字或音频、EdgeTTS 音色、RVC 参数、镜头号、音频角色、SHA-256、ComfyUI 工作流和已知限制；
- 尚未完成的下载、推理或人工试听步骤。
