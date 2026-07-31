# Applio × ComfyUI 视频音频制作记录

将本文件复制到：

`D:\12070\Documents\workspaces\Comfy-Codex-Workspace\projects\<project_slug>\audio\records\audio-production-record.md`

## 项目基线

- 项目名称：
- 项目 slug：
- 台词版本：
- 视频帧率：
- 对白母版规格：
- 目标响度：
- ComfyUI 输入暂存目录：
- 记录日期：

## 角色与模型

| 角色 | 角色 slug | 语言 | RVC 模型 | 模型 SHA-256 | Index | Index SHA-256 | 训练语言/用途 | 授权备注 |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |

## 参数预设

| 预设名 | EdgeTTS 音色/源录音 | TTS Speed | F0 | Pitch | Embedder | Index Rate | Protect | 其他开关 |
|---|---|---:|---|---:|---|---:|---:|---|
|  |  |  |  |  |  |  |  |  |

## 镜头音频

| 镜头 | 说话人 | 逐字台词 | 情绪/语速 | 音频角色 | 预设 | 锁定 WAV | 时长 | SHA-256 | ComfyUI 工作流 | 状态 |
|---|---|---|---|---|---|---|---:|---|---|---|
| `sh010` |  |  |  | `final_dialogue` |  |  |  |  |  | `draft` |

音频角色只使用：

- `final_dialogue`：直接决定口型和时长；
- `voice_reference`：仅供视频模型参考音色；
- `music_or_sfx`：不作为主要口型驱动。

状态只使用：`draft`、`tested`、`locked`、`retired`。

## ComfyUI 暂存与输出

| 镜头 | 锁定音频 | ComfyUI 暂存文件 | LoadAudio 节点文件 | 输出视频 | 验收 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 验收结论

- [ ] 台词逐字正确
- [ ] 人名、数字和外语发音正确
- [ ] 音色跨镜头一致
- [ ] 无削波、电音、吞字或异常呼吸
- [ ] 停顿和时长符合分镜
- [ ] ComfyUI 未引用模板示例音频或旧版本
- [ ] 嘴型起止、重音和闭口音可接受
- [ ] 双人场景没有串嘴
- [ ] 音频、模型、参数和输出哈希已记录

## 返工记录

| 日期 | 镜头 | 问题层 | 原版本 | 新版本 | 修改内容 | 结果 |
|---|---|---|---|---|---|---|
|  |  | Applio / ComfyUI / Mix |  |  |  |  |
