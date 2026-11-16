---
name: breeze-tts-manager
description: 用本机 ComfyUI 的 Breeze TTS 2 为 AI 视频、短片和有声内容批量配声：按台词清单和角色声线库一次生成多条中文候选，自动做转写与响度、语速预检，由用户试听选定并锁定角色声线，再接入媒体中心配音接收流程。用户提到 Breeze、配音、角色配声、批量生成台词语音、音色设计、锁定角色声线、多角色对白时使用；权重仅限研究和非商用，商用先停下确认授权。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Breeze TTS 2 本机配声

执行平台：Windows。WSL 里的 AI 通过 `powershell.exe` 调用同一份脚本，不在 Linux 直接跑 ComfyUI。先核对本机 ComfyUI、节点和权重是否仍在原位；安装、发现和业务验收分别报告，以实测回执为准。

2026-11-15 用户在五句中文对比里试听后认为 Breeze 优于 Qwen3-TTS，因此中文配声默认先用本技能；这是用户对那五句的听感判断，不是通用结论。

## 许可闸门（先看这条）

权重与自托管生成物受 *BreezeBlue Research and Non-Commercial License* 约束，**禁止商用**；节点代码是 Apache-2.0。收费作品、广告、对外商业发布或客户交付要用的声音：停止使用本入口并说明原因，改用 Qwen3-TTS（Apache-2.0）等可商用方案，或由用户另向 BreezeBlue/RESONIA, INC. 取得书面授权。所有生成命令必须带 `--purpose evaluation|noncommercial`，其他值会在写任何文件前被拒绝。克隆真人声音还需要声音所有者授权，不制作冒充内容。

## AI 配声工作流（日常用这个）

目录在 `<项目>\audio\dubbing\`，入口 `scripts\dub.py`，所有命令都带 `--project <项目根目录>`。

1. **准备清单**（AI 从剧本或分镜整理，让用户过目）：`dub.py init` 生成示例，然后写：
   - `cues.json`：每句 `id`、`shot`、`character`、`text`、可选 `direction`（怎么说）和 `pace`（`slow|normal|fast`，用于预检）。台词里可插 `[笑]` `[叹气]` `[咳嗽]` `[清嗓子]`。
   - `voices.json`：每个角色一段 `instruction`（音色描述：年龄、性别、质感、语速、气质，写具体）。同一角色所有台词共用它。
2. **确认后端**：`comfyui-on-demand` 的 `health` 看 8188。未运行时按用户当前请求用媒体中心 `Start-Comfy.ps1` 启动，只收尾自己启动的进程；显卡空闲、内存余量沿媒体中心规则，Win10 虚拟机在用时不动。
3. **批量生成候选**（默认每句 3 条，种子 11、23、37；已成功的候选自动跳过，可断点续跑）：

   ```powershell
   python scripts\dub.py --project <项目> batch --takes 3 --purpose noncommercial --yes
   python scripts\dub.py --project <项目> batch --cue S005-02 --seeds 41,53 --purpose noncommercial --yes   # 只补某句
   ```

   模型加载后留在 ComfyUI 里，后续每句约 6～14 秒。提交结果不明确（`result_unknown`）时整批停止，不重放，先按 prompt_id 对账。
4. **机器预检**：`dub.py check` 用本机 SenseVoice 转写并算字错率，连同响度、尾部静音、每秒字数写入 `REVIEW.md`。标记只用于排除明显问题：偏轻、尾部静音长、比期望 `pace` 更快或更慢、转写差异。**转写和标记不能代替试听。**
5. **用户试听**：把 `REVIEW.md` 和对应音频路径交给用户。AI 不替用户选声；用户说选哪条，才执行：

   ```powershell
   python scripts\dub.py --project <项目> select --cue S005-02 --take s23 --decided-by 用户试听
   ```

6. **锁定角色声线**（强烈建议，保证同一角色前后一致）：用户认可某条后，把它固定为该角色参考，之后同角色的台词自动走 clone/direction：

   ```powershell
   python scripts\dub.py --project <项目> lock-voice --voice 方源 --cue S005-02 --take s23 --decided-by 用户试听
   ```

   参考音频需要逐字转写：转写与台词一致时自动采用，否则用 `--ref-text` 给出实际听到的文字。锁定后可对该角色其余台词再跑 `batch --cue …`。
7. **接收到配音流程**：`dub.py ingest` 把所有已选定的候选交给媒体中心 `ingest_dialogue`，生成 48 kHz 单声道 24bit WAV 候选，清单带 `engine=breeze-tts-2`、许可、`commercial_use_allowed=false`、台词编号、take 和决定人；接收前核对音频哈希与选定时一致。要用媒体中心的 Python（`media-center\.venv`）运行这一步。

   ```powershell
   & D:\ws\04-content\04-01-ai-imaging\comfy-workspace\media-center\.venv\Scripts\python.exe scripts\dub.py --project <项目> ingest
   ```

   `dub.py status` 随时汇总进度。接收后的音频仍是候选，口型对齐前由用户最终确认。

### 提升成功率的经验（来自 2026-11-13～15 实测）

- 多采样再挑：同一句不同种子的语速、情绪、读音差别明显，3 条起步，重要台词 5～6 条。
- 速度指令不稳：写“语速偏慢”的哽咽句可能读得很快，写“急促”的短喊句可能读得很慢。给 `cues.json` 写 `pace`，让预检把明显反的挑出来，再多采几条；不要靠后期拉伸时长，媒体中心规则也不允许硬拉伸。
- 音量偏低的候选会被标“偏轻”，混音时加增益；接收流程不会自动归一化。
- 生僻专名（如“春秋蝉”）容易读错，转写差异大时优先试听，必要时换同音通用字写在台词里并记录。
- 想要情绪细节用 `direction`；想要稳定音色用 `lock-voice`；笑声、叹气用事件标签而不是写“哈哈”。
- 几个角色在同一场景里对话，也可用 `breeze_tts.py run --mode multi` 一次出，但逐句批量更便于选和重做。

## 单条工具（调试、一次性出一句）

`scripts\breeze_tts.py` 提供 `build`（只生成 API 工作流）、`run`（提交并取回，写回执）、`ingest`（接收一条成功运行）、`info`。模式 design（台词+音色描述）、clone（参考音频+逐字转写）、direction（clone 加表演指令）、multi（`名字:台词` 脚本，最多 8 人，只认 ASCII 冒号，脚本会自动把行首全角冒号改掉）。ComfyUI 的 API 工作流必须写全必填输入，否则返回 400，回执记 `rejected`。

## 本机固定事实

- 节点：`C:\Users\12070\Documents\ComfyUI\custom_nodes\ComfyUI-Breeze-TTS-2`，固定提交 `53d899bfaa1ea37badc58f901900c068a9755116`（v1.4.7）。ComfyUI 用 `--base-directory C:\Users\12070\Documents\ComfyUI`，默认 8188。
- 权重：`C:\Users\12070\Documents\ComfyUI\models\breezetts2\drbaph_Breeze-TTS-2-comfyui\`，`Breeze-TTS-2-int8-hybrid.safetensors` 加 `audio_tokenizer/`，已登记 `models\catalog.json`（SHA-256、许可状态）。
- 依赖：只新增 `accelerate`；torch 2.8.0+cu129、transformers 5.5.4 不动。注意力用 sdpa，不装 flash-attn。显存相对基线约增加 4.7～4.9 GiB。
- 禁止联网下载：工作流里 `download_if_missing=false`、说话人 `auto_transcribe_if_empty=false`。转写预检用本机 FunASR（`D:\Program_Files\FunASR-runtimes\1.4.16`）和 SenseVoiceSmall（`D:\aimodels\modelscope\iic`），CPU 离线运行，路径可用参数覆盖。
- 输出为 24 kHz 单声道 FLAC，运行目录含 `receipt.json`（prompt_id、工作流哈希、节点提交、输出哈希、`approved=false`）。

## 与其他配音方案的选择

| 需求 | 选用 |
| --- | --- |
| 非商用的中文角色配声、情绪细节、笑声叹气标签、多角色对白 | Breeze TTS 2（本技能，用户听感偏好） |
| 商用或需要可审计许可的镜头 | 媒体中心 Qwen3-TTS（`Voice-Local.ps1`，Apache-2.0）；其启动器受 GPU 利用率闸门限制，需要时在用户授权的窗口里显式放宽 |
| 已训练好的角色音色转换 | `applio-rvc-manager` |
| 草稿占位 | Windows 系统声音 |

## 失败与回退

- 节点载入失败先看 ComfyUI 日志与节点仓库 issue：已知 #14（ComfyUI ≥0.44 的 Dynamic VRAM 路径）、#12（低显存 codec 设备放置）；当前 ComfyUI 0.39.0 不受 #14 影响。
- 显存紧张：保持 hybrid + eager，不开 `cuda_graphs`。
- 不用 TTS-Audio-Suite、ComfyUI-Manager 一键装或无固定提交的更新；升级节点要重新审查 `install.py`、依赖和许可，并更新 `catalog.json`。
- 回退：删除节点目录和 `models\breezetts2\`，必要时 `uv pip uninstall accelerate`（确认无其他节点依赖），还原 `catalog.json` 备份；基线在工作区 `reports\breeze-tts-2-20261112\`。

## 实测范围

2026-11-13～15 在 RTX 5080 上：design、clone、direction、multi、单条接收，以及批量流水线（批量生成、转写预检、选定、锁定声线后用参考生成、接收）都跑通；批量试跑记录在工作区 `reports\breeze-tts-2-20261112\dub-trial\`，对比样本在 `compare\`，冒烟记录在 `SMOKE-TEST.md`。试跑中的选定和锁定是脚本演练，`decided-by` 写的是“测试脚本”。

未覆盖，不视为已验收：长时间稳定性、一次几十句的大批量、8 人对白、真人声音克隆、bf16 与 `cuda_graphs`、锁定参考后是否一定比多采样更一致（需用户试听判断）。
