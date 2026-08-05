# ComfyUI 模型管理

## 目标目录

默认扫描 `C:\Users\12070\Documents\ComfyUI\models` 以及 ComfyUI 配置中的 `extra_model_paths.yaml`。常见目录与用途：

| 目录 | 用途 |
| --- | --- |
| `checkpoints` / `diffusion_models` / `unet` | 主模型或扩散模型 |
| `text_encoders` / `clip` / `clip_vision` | 文本、视觉编码器 |
| `vae` / `vae_approx` | VAE 编解码 |
| `loras` / `controlnet` / `model_patches` | 风格、动作和结构控制 |
| `sam` / `detection` / `insightface` | 分割、检测、人脸/身份 |
| `upscale_models` / `frame_interpolation` / `optical_flow` | 放大、插帧、光流 |
| `audio_encoders` / `audio` | 音频条件和音频生成 |
| `CogVideo` / `liveportrait` / `3d` 相关目录 | 专用视频、人像和 3D 组件 |

实际目录名以本机节点的模型加载器为准；不要仅按文件名猜测目录。

## 模型登记字段

建议在工作区保存 `models/catalog.json` 或项目 `manifest.json`：

```json
{
  "file": "wan2.1_14B_SCAIL_2_fp16.safetensors",
  "category": "diffusion_models",
  "relative_path": "diffusion_models/wan2.1_14B_SCAIL_2_fp16.safetensors",
  "size_bytes": 0,
  "sha256": null,
  "source_url": null,
  "license": "待核验",
  "precision": "fp16",
  "comfyui_workflows": [],
  "notes": "记录显存、量化和已验证节点版本"
}
```

大模型默认先登记大小、来源和路径；在模型被锁定用于交付或发现疑似重复时再计算 SHA-256，避免无意义地长时间占用磁盘。

## 缺失依赖报告

对每个工作流输出：缺失文件、期望目录、加载节点、可接受的等价变体、来源/许可证、估计显存和是否需要自定义节点。严禁静默把 `fp16` 换成 `fp8` 或换成不同训练版本；如果必须替换，创建新版本并记录画质/速度变化。

## 下载与清理

下载前先搜索同名、同哈希或同模型卡；写入临时目录后再移动到精确类别目录。不要在项目目录复制模型，不要把模型、密钥或缓存纳入 Git。删除模型前检查工作流登记、ComfyUI 日志和项目 manifest；默认移动到可恢复的备份目录，并记录原因。
