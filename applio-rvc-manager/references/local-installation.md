# 本机 Applio 安装基线

本文件是 2026-07-31 的部署快照，不替代现场审计。每次操作前运行 `scripts/audit-applio.ps1`。

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
| 模型根目录 | `D:\Program_Files\Applio\logs` |
| 模型原包目录 | `D:\Program_Files\Applio\model_packages\<日期>` |
| 音频工作目录 | `D:\Program_Files\Applio\assets\audios` |
| 内置模型与预测器 | `D:\Program_Files\Applio\rvc\models` |

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
