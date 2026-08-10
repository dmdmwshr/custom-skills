# 本机 Applio 安装基线

本文件是 2026-08-10 的部署快照，不替代现场审计。每次操作前运行 `scripts/audit-applio.ps1`。本机 UI 操作优先使用 Codex 内置浏览器，不接管外部浏览器。

## 已确认安装

| 项目 | 当前值 |
|---|---|
| 版本 | 3.6.4 |
| 安装根目录 | `D:\Program_Files\Applio` |
| 本地说明 | `D:\Program_Files\Applio\LOCAL_INSTALL_INFO.md` |
| 启动入口 | `D:\Program_Files\Applio\run-applio.bat` |
| 内置 Python | `D:\Program_Files\Applio\env\python.exe`，3.12.13 |
| PyTorch | 2.11.0+cu128 |
| GPU | NVIDIA GeForce RTX 5080 |
| 默认地址 | `http://127.0.0.1:6969` |
| 内置 TTS 中间件 | `edge-tts 7.2.8`，需要联网 |
| TTS 音色清单 | `D:\Program_Files\Applio\rvc\lib\tools\tts_voices.json` |
| 模型根目录 | `D:\Program_Files\Applio\logs` |
| 模型原包目录 | `D:\Program_Files\Applio\model_packages\<日期>` |
| 音频工作目录 | `D:\Program_Files\Applio\assets\audios` |
| 内置模型与预测器 | `D:\Program_Files\Applio\rvc\models` |

## 开机启动任务

Applio 已登记为本机开发任务，不要重复创建同功能任务：

| 项目 | 当前值 |
|---|---|
| 任务路径 | `\DevProjects\APPLIO\WEB\` |
| 任务名称 | `DEV-APPLIO-WEB-01-Start-Applio` |
| 触发方式 | 当前交互用户登录时启动后台 Web 服务 |
| 访问边界 | 仅监听本机 `127.0.0.1:6969` |
| 浏览器行为 | 登录时不自动弹出；按需打开本机地址 |
| 管理目录 | `C:\Users\12070\Desktop\临时任务\Applio启动任务管理` |

需要启动、停止或复核时，优先使用该目录下的精确管理脚本；先查看，再执行变更。不要按名称批量结束 Python 进程。

启动脚本执行 `env\python.exe app.py --open`，不使用系统 Python 或 Conda。默认端口被占用时，先确认占用者；确需改端口可从安装根目录执行：

```powershell
& ".\env\python.exe" ".\app.py" --open --port 6970
```

## 安装包与升级

- 官方仓库：`https://github.com/IAHispano/Applio`
- 已安装包：`ApplioV3.6.4.zip`
- 已验证 SHA-256：`beedb7dbe54bcc0ccafacbd59fa4ed85af1839c591a84f82b93b90e5109dd9f1`
- 本地离线包：`D:\Program_Files\Applio\.install-temp\ApplioV3.6.4.zip`
- 离线包约 4.6 GiB。删除前先确认新版可用且不再需要离线恢复。

升级时备份：

1. `logs`
2. `assets\presets`
3. `rvc\models\embedders\embedders_custom`
4. `assets\audios` 中确需保留的文件
5. `LOCAL_INSTALL_INFO.md` 和模型清单

不要直接覆盖或单独升级内置 `env`。

## 进程边界

- 监听 6969 的进程通常是内置 `env\python.exe`。
- PID 会变化，不在脚本或文档中固定 PID。
- 停止前同时核对可执行路径、命令行和端口。
- 不要使用 `Stop-Process -Name python`。

## 音频数据边界

Applio 的“Clear Outputs”会删除 `assets\audios` 内的音频。原始视频、原始人声、分离后干声和最终成片应保存在独立项目目录；Applio 目录只放可重建的工作副本与输出。

## 内置 TTS 边界

- TTS 页调用 Microsoft EdgeTTS 在线服务，不在本机加载独立的 TTS 权重。
- 本机不需要另下 TTS 模型；需要网络可访问 EdgeTTS 服务。
- 当前代码流程是“文字或 UTF-8 TXT → EdgeTTS 底稿 → RVC 角色音色”，会分别生成 TTS 底稿和 RVC 输出。
- 要输出角色音色，仍需选择已安装的 RVC `.pth`，并建议配套 `.index`。
- 2026-07-31 现场查询返回 322 个 EdgeTTS 音色，其中 14 个为 `zh-*` 中文音色；服务清单会变化，使用前可重新查询。
- 完全离线、可训练或高可控 TTS 需要另选专用项目及其模型，不属于 Applio 内置 TTS。

## 当前能力核对

当前安装已核对到“训练”“推理”“TTS”“实时”等功能页；训练页包含数据集预处理、特征提取、训练和生成索引入口，TTS 页包含 EdgeTTS 音色、文字输入和 RVC 模型/索引选择。功能入口已确认不等于某个新模型已经训练完成；每次具体任务仍需核对模型、索引和一次短样本输出。
