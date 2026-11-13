---
name: breeze-tts-manager
description: 管理本机 ComfyUI 中的 Breeze TTS 2（中英文音色设计、音色克隆、情绪指令、多人对白）并把生成的候选音频交给 AI 视频制作链路的配音接收流程。用户提到 Breeze TTS、音色设计、用文字描述生成声音、本地中文配音候选、多角色对白生成时使用；权重仅限研究和非商用，商用先停下确认授权。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Breeze TTS 2 本机配音

执行平台：Windows。先核对本机 ComfyUI、节点和权重是否仍在原位；WSL/Linux 不直接套用此入口。安装、发现和业务验收分别报告，以实测回执为准。

## 它是什么

BreezeBlue 的开放权重文字转语音**模型**（3B，中英双语），经 ComfyUI 节点包 `ComfyUI-Breeze-TTS-2` 调用。四种用法：

| 模式 | 输入 | 用途 |
| --- | --- | --- |
| design | 台词 + 音色描述 | 还没有参考声音时，用文字设计角色音色 |
| clone | 台词 + 参考音频 + 其逐字转写 | 复刻已有授权声音 |
| direction | clone 的输入 + 表演指令 | 保持音色，调整情绪、语速、语气 |
| multi | `名字: 台词` 脚本 + 最多 8 个说话人 | 一次生成多角色对白 |

台词内可插入事件标签：中文 `[笑]` `[咳嗽]` `[清嗓子]` `[叹气]`，英文 `(laugh)` `(cough)` `(clears throat)` `(sigh)`。输出为 24 kHz 单声道。

## 许可闸门（先看这条）

权重与自托管生成物受 *BreezeBlue Research and Non-Commercial License* 约束，**禁止商用**；节点代码本身是 Apache-2.0。用户要把声音用于收费作品、广告、对外商业发布或客户交付时：停止使用本入口，说明原因，改用已有商用许可方案，或由用户另向 BreezeBlue/RESONIA, INC. 取得书面授权。`breeze_tts.py run` 必须带 `--purpose evaluation|noncommercial`，传其他值会在写任何文件前拒绝。克隆真人声音还要有声音所有者授权，不制作冒充内容。

## 本机固定事实

- 节点：`C:\Users\12070\Documents\ComfyUI\custom_nodes\ComfyUI-Breeze-TTS-2`，固定提交 `53d899bfaa1ea37badc58f901900c068a9755116`（v1.4.7）。ComfyUI 实际用 `--base-directory C:\Users\12070\Documents\ComfyUI`，由 `media-center\Start-Comfy.ps1` 按需启动，默认 8188。
- 权重：`C:\Users\12070\Documents\ComfyUI\models\breezetts2\drbaph_Breeze-TTS-2-comfyui\`，默认 `Breeze-TTS-2-int8-hybrid.safetensors` 加 `audio_tokenizer/`。登记在工作区 `models\catalog.json`，含 SHA-256 与许可状态。
- 依赖：只新增 `accelerate`；torch 2.8.0+cu129、transformers 5.5.4 不动。不装 flash-attn、sageattention，注意力用 sdpa。
- 禁止联网下载：工作流中 `download_if_missing=false`，说话人节点 `auto_transcribe_if_empty=false`（避免触发 Whisper 下载）。克隆需要的逐字转写由人提供或另行核对。

## 调用流程

1. **确认用途与许可**：商用则停。确认台词、角色和授权。
2. **确认后端**：用 `comfyui-on-demand` 的 `health` 检查 8188。未运行时由用户当前请求授权后，再用媒体中心 `Start-Comfy.ps1` 启动；本技能不自行启动、不终止进程，只收尾自己启动的那一个。GPU 与内存预算沿媒体中心规则（显卡空闲、提交余量足够），Win10 虚拟机在用时不动。
3. **生成工作流**：

   ```powershell
   python scripts\breeze_tts.py build --mode design --text-file 台词.txt --instruction "沉稳的中年男声，语速偏慢" --out wf.api.json
   python <comfyui-on-demand>\scripts\comfyui_api.py preflight --workflow wf.api.json
   ```

4. **提交并取回**（目录必须全新，失败回执保留）：

   ```powershell
   python scripts\breeze_tts.py run --mode design --text-file 台词.txt --instruction-file 音色.txt `
     --run-dir <项目>\audio\tts_source\<镜头>_<角色>_breeze_v001 --purpose noncommercial --yes
   ```

   克隆或指令模式再加 `--reference 参考.wav --reference-text-file 参考转写.txt`；多人对白用 `--mode multi --cast-json cast.json`。参考音频会被上传到 ComfyUI `input\breeze_refs\`。
5. **回执核对**：`receipt.json` 含 prompt_id、工作流哈希、节点提交、输出哈希、时长和 `approved=false, voice_locked=false`。`status=result_unknown` 时不重放，按 prompt_id 用 `comfyui-on-demand history/reconcile` 对账。
6. **文字核对**：用媒体中心 `scripts/audit_local_voice.py`（FunASR SenseVoice）转写比对；同音字、标点差异保留，转写相同也不等于听感正确。
7. **交给配音接收流程**：成功运行（`succeeded_pending_listening`）用 `breeze_tts.py ingest` 调用媒体中心 `media_center.audio.ingest_dialogue`，生成 48 kHz 单声道 24bit WAV 候选，清单 `metadata` 自动写入 `engine=breeze-tts-2`、许可、`commercial_use_allowed=false`、回执路径与哈希。接收前会核对输出哈希与回执一致。

   ```powershell
   python scripts\breeze_tts.py ingest --run-dir <运行目录> --shot-id S005 --speaker 方源 --out <项目>\audio --role final_dialogue
   ```

   做音色参考用 `--role voice_reference`。重采样与格式统一只由接收流程完成。
8. **试听与选定**：必须由用户听后决定。本技能不批准音色、不锁定声线。

## 与其他配音方案的选择

| 需求 | 选用 |
| --- | --- |
| 固定预设音色、稳定离线、可评估商用边界 | 媒体中心 Qwen3-TTS（`Voice-Local.ps1`，Apache-2.0） |
| 用文字设计全新角色音色、情绪指令、`[笑]` 类事件、多角色一次出 | Breeze TTS 2（仅非商用） |
| 已训练好的角色音色转换 | `applio-rvc-manager` |
| 草稿占位 | Windows 系统声音 |

Breeze 与 Qwen 的中文听感没有官方独立对比，选型以同一批台词的试听为准；不要据英文榜单推断中文质量。

## 失败与回退

- 节点载入失败先看 ComfyUI 日志与节点仓库 issue；已知 #14（ComfyUI ≥0.44 的 Dynamic VRAM 路径）和 #12（低显存 codec 设备放置）。当前 ComfyUI 为 0.39.0，不受 #14 影响。
- 显存紧张：保持 hybrid + eager；不要为速度开 `cuda_graphs`（权重常驻显存、首次捕获数秒）。
- 不用 TTS-Audio-Suite、ComfyUI-Manager 一键装节点或无固定提交的更新；升级节点需重新审查 `install.py`、依赖变化和许可，并更新 `catalog.json`。
- 回退：删除节点目录和 `models\breezetts2\`，必要时 `uv pip uninstall accelerate`（仅当确认无其他节点依赖），还原 `catalog.json` 备份；基线记录在工作区 `reports\breeze-tts-2-20261112\`。

## 实测记录

2026-11-13 在本机 RTX 5080 上，design、clone、direction、multi 四种模式和 `ingest` 接收都跑通：已加载时 2～4 秒的音频约 6～14 秒出结果，首次含载入约 40 秒；相对基线显存增加约 4.7～4.9 GiB，关闭 ComfyUI 后释放。SenseVoice 转写大体一致，但会出现首字、同音字差异，`[笑]` 能被识别为笑声事件。详见工作区 `reports\breeze-tts-2-20261112\SMOKE-TEST.md`。

经验：多人脚本只认 ASCII 冒号（`甲:台词`），`build/run` 会把行首全角冒号自动改掉；API 格式工作流必须写全所有必填输入，否则 ComfyUI 返回 400，回执记 `rejected` 而非结果不明。

未覆盖，不视为已验收：中文听感与 Qwen 的对比、bf16/`cuda_graphs`、长文本与 8 人对白、真人声音克隆、Whisper 转写节点、长时间稳定性。
