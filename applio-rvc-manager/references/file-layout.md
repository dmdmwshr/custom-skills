# Applio 项目文件与命名规范

## 1. 项目事实源

Applio 的安装目录是运行目录，不是长期素材仓库。训练原件、文本稿、模型记录和最终音频放在独立项目目录；Applio 只保留可重建的工作副本和已安装模型。

默认项目根目录：

```text
D:\12070\Documents\workspaces\Applio-Voice-Projects\<project_slug>\
```

如果项目已经属于 ComfyUI 视频，沿用该项目的 `audio\` 目录，并遵守 `references/comfyui-video-handoff.md`；不要为同一镜头建立第二套事实源。

## 2. 标准目录

```text
<project_slug>\
├── project.json                 # 项目元数据，UTF-8
├── script\                      # 训练稿、录音稿、台词版本
│   └── <locale>\
├── dataset\
│   ├── raw\                     # 原始录音，只读归档
│   ├── clean\                   # 清洗后的派生音频
│   ├── segments\                # Applio 训练用单句音频
│   ├── manifests\               # segments.tsv、哈希和统计
│   └── review\                  # 待复核或拒绝文件
├── training\
│   ├── configs\                 # 训练参数和数据集版本
│   ├── logs\                    # 训练摘要，不替代 Applio 临时日志
│   └── checkpoints\             # 可恢复检查点，按版本归档
├── models\
│   └── <model_slug>\             # 模型来源、哈希和安装记录
├── generation\
│   ├── source\                  # RVC 输入的干声
│   ├── tts\                     # EdgeTTS 底稿
│   ├── rvc\                     # 候选转换结果
│   ├── locked\                  # 通过验收的无损母版
│   └── records\                 # 参数、哈希、试听结论
└── exports\                     # 交付副本，不作为母版
```

目录可以按任务缩减，但 `raw`、`segments`、`models`、`locked` 的语义不能混用。

## 3. 命名规则

### 技术路径

- `project_slug`、`model_slug`、目录名、`.index` 文件名使用小写 ASCII、数字和短横线或下划线，不使用空格、中文、括号和全角符号。
- 示例：`sheep-cmn-speech-v001`、`genshin-paimon-jpn-v002`。
- `.pth` 可以使用中文显示名，便于 Applio 下拉框识别；其所在目录和 `.index` 保持 ASCII。
- 同一个模型的 `.pth` 和 `.index` 必须位于同一 ASCII 目录；不要把索引放在中文路径下。

### 音频和文本

```text
seg_0001_cmn_calm_v001.wav
seg_0002_yue_question_v001.wav
tts_001_jpn_neutral_v001.wav
rvc_001_paimon_jpn_dialogue_v001.wav
locked_001_paimon_jpn_dialogue_v001.wav
```

字段建议按“序号_语言或方言_情绪或用途_版本”组织；角色中文名、台词和详细备注放在清单，不依赖文件名承载全部信息。

训练稿：`<locale>_<purpose>_dataset_v001.txt` 和同名 `.srt`。生成稿、修改稿和已录音稿必须增加版本号，不要覆盖旧稿。

## 4. 文件格式和编码

| 文件 | 规范 |
|---|---|
| 原始/训练音频 | PCM WAV，优先单声道；母版建议 48 kHz、24 bit |
| Applio 训练采样率 | 一个模型内统一使用 32/40/48 kHz 之一，当前可先用 40 kHz 试跑 |
| 最终对白 | 无损 WAV；保留自然句首句尾静音，不以 MP3 作为母版 |
| 训练文本 | UTF-8，每行一句；不混入序号和说明 |
| 字幕文件 | UTF-8 SRT，时间码 `HH:MM:SS,mmm` |
| 清单 | UTF-8 TSV，字段见 `dataset-and-training.md` |
| 参数记录 | UTF-8 JSON，保存模型、索引、源音频、TTS 音色、RVC 参数和输出哈希 |
| 哈希 | SHA-256，文件名建议为 `sha256.txt` 或写入对应 JSON |

PowerShell 读写中文文件时显式指定 UTF-8；不得用系统 ANSI/OEM 编码作为事实源。

## 5. 模型安装和记录

原始下载包放在：

```text
D:\Program_Files\Applio\model_packages\<YYYY-MM-DD>\
```

安装后的运行副本放在：

```text
D:\Program_Files\Applio\logs\<model_slug>\
├── <中文显示名>.pth
└── <model_slug>.index
```

项目 `models\<model_slug>\` 至少保存：

- `model-manifest.json`：来源、下载日期、训练语言/方言、RVC 版本、用途、授权状态、建议音域和安装路径；
- `sha256.txt`：`.pth`、`.index` 和原始包的哈希；
- `qa.md` 或等价记录：短样本、参数、听感、已知限制和是否 `accepted`。

没有模型卡、训练语言、试听或许可说明的模型只能隔离测试，不能直接作为正式交付模型。

## 6. 版本和状态

所有候选输出都使用版本号；锁定文件不原位覆盖。记录状态建议使用：

- `draft`：正在制作；
- `candidate`：已生成，待试听；
- `accepted`：通过音频和文件验收；
- `locked`：已交给视频或其他下游使用；
- `retired`：被新版本替代，但保留以便追溯。

台词、源音频、模型、索引、TTS 音色、Pitch、F0、Embedder、Index Rate、Protect、Clean Audio 和输出规格任一项变化，都要增加输出版本并重新记录哈希。

## 7. 清理边界

- 不删除 `dataset\raw`、正式模型记录、`locked` 母版或唯一的模型包。
- 可以清理 Applio `assets\audios` 中已经复制到项目且可重建的临时文件。
- ComfyUI `input`/`output` 和 Applio `assets\audios` 只作中转；长期文件回收到项目事实源。
- 清理或迁移前先列出精确路径、文件大小、哈希和恢复位置，不按模糊前缀批量删除。
