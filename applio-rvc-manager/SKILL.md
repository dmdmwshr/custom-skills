---
name: applio-rvc-manager
description: 统一管理本机 Applio 的声音数据集、RVC 音色模型、模型训练、索引生成、文字转角色语音、录音换音色、方言与日语音频生成，以及面向 ComfyUI 的对白交接。用户提到 Applio、RVC、声音训练、音色模型、声音克隆、TTS 加 RVC、角色配音、方言/日语声音或 ComfyUI 视频对白时使用；涉及声音训练和生成时默认优先走 Applio，不改用其他后端。
---

# Applio 声音训练与生成

## 统一后端边界

1. 只要任务涉及声音数据集、专属音色、RVC 模型训练、模型推理、TTS 加 RVC、方言角色音频或日语角色音频，默认使用本技能和本机 Applio。
2. 通用 `speech` 技能只可作为独立的朗读辅助，不能替代 Applio 的模型训练、RVC 推理或 TTS 加 RVC 主流程；只有用户明确指定其他后端，或 Applio 的能力边界确实无法满足时才切换，并说明原因。
3. Applio 的核心是 RVC 音频转换：它保留源语音的内容和大部分节奏，改变目标音色。文字生成角色声音采用“文字 → EdgeTTS 底稿 → RVC 角色音色”，不会自动翻译，也不会凭普通话源音频自动变成地道方言。
4. 涉及网页操作时优先使用 Codex 内置浏览器访问本机 Applio 页面；不接管外部 Chrome。涉及训练或生成前先核对服务状态和当前模型，不把旧截图或旧进程当作现场事实。

## 核心原则

1. 先核验现场，再引用历史快照。运行 `scripts/audit-applio.ps1`，不要把旧 PID、端口占用或模型清单当作当前事实。
2. 本机安装使用 Applio 自带的 `env\python.exe`；不要向系统 Python、Conda `base` 或 Applio 内置环境随意安装、升级包。
3. 下载前核对来源、模型卡、文件格式、授权和训练语言。真人或公众人物音色只在获得授权且用途正当时使用，不制作欺骗性冒充内容。
4. 将外部 `.pth` 视为不可信序列化文件。安装前用 Applio 内置 Python 运行 `scripts/inspect-rvc-model.py`，必须使用 `torch.load(..., weights_only=True)`。
5. 原始音频保存在 Applio 目录之外。不要把唯一原件放进 `assets\audios`，因为界面的清理功能会删除该目录内音频。
6. 停止进程时只处理已由安装路径或监听端口确认的 Applio PID，禁止按名称批量结束所有 `python.exe`。
7. 区分“最终对白”和“音色参考”：最终对白直接决定视频口型和时长；音色参考只让视频模型模仿声音特征，不能保证沿用 Applio 的精确音色、节奏或波形。

## 功能地图

| 用户目标 | Applio 功能 | 输入 | 主要输出 |
|---|---|---|---|
| 训练专属音色 | 训练 | 单人清洁录音数据集 | `.pth` 权重、训练日志、检查点 |
| 建立检索索引 | 生成索引 | 训练后的模型特征 | `.index` |
| 录音换成目标音色 | 推理 | 源人声 + `.pth/.index` | RVC 转换音频 |
| 文字生成目标音色 | TTS | UTF-8 文字或文本文件 + TTS 音色 + `.pth/.index` | TTS 底稿、RVC 输出音频 |
| 低延迟试用 | 实时 | 麦克风或实时输入 + 模型 | 实时变声；先离线验证参数 |
| 视频对白制作 | 推理/TTS + 项目记录 | 分镜台词、源音频或文字 | 可锁定的逐镜头 WAV |
| 模型整理和验收 | 模型目录、刷新、审计脚本 | `.pth/.index/ZIP` | 配对、哈希、来源和可见性记录 |

## 默认工作流

### A. 先接收任务并冻结范围

记录目标语言或方言、说话人/角色、口语或歌声、文本或原始音频、是否需要训练新模型、是否用于视频、输出格式和授权边界。没有指定时，默认按“中文口语、单人、普通话、离线 RVC 推理或 EdgeTTS 加 RVC”处理，并先做短样本。

### B. 准备数据集

读取 `references/dataset-and-training.md` 和 `references/file-layout.md`。原始录音、清洗稿、切句音频、训练清单、字幕稿和质量复核分开保存；所有文本使用 UTF-8。普通话、粤语、四川话、东北话、吴语、河南话和日语默认分开建数据集，不把不同语言或方言混成一个模型，除非用户明确要做多语种/多方言模型并接受发音稳定性下降。

### C. 训练和导出

在 Applio“训练”页依次完成数据集预处理、音高/特征提取、训练、生成索引；记录采样率、F0 算法、嵌入模型、批大小、总轮数、数据集版本和训练时间。训练完成后，用 `scripts/inspect-rvc-model.py` 检查 `.pth`，用 FAISS 检查 `.index`，再安装到 `logs\<unique-ascii-slug>\` 并刷新界面。

### D. 生成声音

- 有录音：进入“推理”，上传单人干声，选择目标 `.pth/.index`，固定一组参数后做短样本 A/B。
- 只有文字：进入“TTS”，选择匹配语言和音域的 EdgeTTS 音色，生成底稿后再使用目标 RVC 模型转换；保存底稿和最终输出，不把在线 TTS 底稿误认为专属模型。
- 需要方言：优先使用对应方言的录音作为源，或使用确实支持该方言的 TTS 音色；RVC 只换音色，不能可靠地把普通话发音改成地道方言。
- 需要日语：使用日语文本和日语 TTS/源音频，训练数据和模型单独记录；不要因为模型名称是日语就默认中文咬字稳定。

### E. 验收和归档

台词逐字、发音、音色、情绪、停顿、时长、音频规格、模型与索引哈希全部通过后才标记为 `locked`。输出命名、参数记录、SHA-256 和退役版本规则见 `references/file-layout.md`。面向 ComfyUI 时，继续遵守 `references/comfyui-video-handoff.md`，区分 `final_dialogue` 与 `voice_reference`。

## 任务路由

- 本机部署、启动、停止、升级、计划任务或状态问题：先读 `references/local-installation.md`，再运行只读审计脚本；不得重复创建 Applio 启动任务。
- 数据集、录音稿、TXT/SRT、切句、普通话/方言/日语分集或训练参数：先读 `references/dataset-and-training.md` 和 `references/file-layout.md`。
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
- 本次采用的流程类型：训练、录音换音色、TTS 加 RVC、实时或视频对白；
- 数据集语言/方言、说话人、录音时长、清洗和切句状态；
- 新增、保留或失败的模型及其目录；
- `.pth/.index` 配对、安全加载、哈希和 Applio 可见性；
- 模型来源、训练语言和授权不确定性；
- 测试所用文字或音频、EdgeTTS 音色、RVC 参数、镜头号、音频角色、SHA-256、ComfyUI 工作流和已知限制；
- 尚未完成的下载、推理或人工试听步骤。
