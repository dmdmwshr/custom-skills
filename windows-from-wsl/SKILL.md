---
name: windows-from-wsl
description: Claude 或 Codex 从 WSL 操作 Windows 项目时使用，涵盖挂载盘文件读写、SSH 原生命令、编码与 Git、交互会话启动及 Windows Codex 派发。适用于跨主机实施和排障；不用于纯 WSL 项目或无关远程服务器。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 从 WSL 操作 Windows 项目

执行端是 WSL，目标是已登记的 Windows 宿主机。Claude 仅在 WSL 运行，不在 Windows 部署或登录 Claude。

## 开始检查

- [ ] 读取目标项目的 `AGENTS.md`、`PROJECT_HANDOFF.md`，核对目录、授权、实时差异及当前写入者；挂载可见不扩大业务权限。
- [ ] 分清 WSL 路径 `/mnt/d/ws/...`、Windows 路径 `D:/ws/...` 和 SSH Git Bash 路径 `/d/ws/...`，不能直接互换命令参数。
- [ ] 核对 SSH 目标是 `windows-host-codex`，用 BatchMode 失败即回报；不索取、复制或打印私钥及认证资料。
- [ ] 需要构建、ADB、GPU、Windows 服务或界面时，在 Windows 执行；读取文件可直接走挂载盘。

现行入口与分工见[协作能力与通道](/srv/work/claude-codex/协作能力与通道.md)的“调用通道”“同一项目的协作”；目录与登记见[工作台规则](/srv/workspaces/03-env/03-01-workbench/AGENTS.md)。实际路径和能力与示例不同时按现场核对。

## 文件、Git 与编码

| 对象 | 操作要点 |
| --- | --- |
| Windows 文件 | 经 `/mnt/c`、`/mnt/d` 读写；先核对实体、链接、原编码和换行，再备份本次精确文件 |
| 挂载盘 Git | 每次加 `git -c safe.directory='*' -C '/mnt/d/ws/目标项目' ...`；只对已核实的目标使用，不写全局通配信任 |
| drvfs 写入 | 不依赖 `chmod`/`fchmod` 或写入器的权限复制；对本次目标原地写入并回读，不套用含 chmod 的原子替换助手 |
| 换行 | 仓库常有 CRLF，按原文件和 `.gitattributes` 保持；避免整仓换行变化 |
| 中文脚本 | 默认 UTF-8；优先 `pwsh`。Windows PowerShell 5.1 可能误读无 BOM 中文脚本；确需 5.1 时单独核对其编码兼容性，不整仓加 BOM |

## SSH 原生命令

登录壳是 Git Bash，调用 Windows 程序时先处理壳引用和路径转换：

```bash
ssh -o BatchMode=yes windows-host-codex \
  'MSYS_NO_PATHCONV=1 tasklist.exe /FI "IMAGENAME eq notepad.exe"'
ssh -o BatchMode=yes windows-host-codex \
  'pwsh -NoLogo -NoProfile -NonInteractive -Command '\''[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $PSVersionTable.PSVersion.ToString()'\'''
```

- `/FI`、`/TN` 等斜杠参数容易被 MSYS 转成路径，相关命令显式设 `MSYS_NO_PATHCONV=1`。
- PowerShell 显式设 `[Console]::OutputEncoding` 为 UTF-8；复杂脚本先在精确目标保存并核验，再用 `pwsh -File` 执行，避免多层插值。
- 超时或断连先查原进程、回执和目标效果；不根据解码异常推断命令没执行，不盲目重放。

## 界面、联接与设备

- 先读 `native-gui-control`（统一入口，后端为官方 `cua-driver` 与受管 `windows-mcp-on-demand`），按授权直接复用现成 Windows CLI 或既有 SSH 入口；两者按需互补，依当前任务、后台能力和实测效果选择。不得新建桥接程序，或因某模型额度不足强制转交独立工具操作。
- 穿越目录联接的移动/删除若被 SSH 重定向保护拒绝，先解析真实源、目标、恢复点和当前效果，再在同一授权范围内走一次性用户会话计划任务。该路径不能绕过平台强制限制或扩大删除范围。
- 命令提交成功、计划任务退出码为零不等于界面或文件迁移已验收；未知结果保留任务身份及断点，先对账再收尾。
- 本机 Cua Driver 0.34.0 对 WPS 表格 12.1.0.28802 的后台 `hotkey ctrl+s`（未指定 `delivery_mode`）曾被当作字符 `s` 送入编辑框，文件未写入；回执仅为 `effect=unverifiable`、`route=synthetic_events`、“via PostMessage”，不足以证明保存。该样本保存优先用已验证的工具栏保存图标（UIA 后台点击），再回读文件字节/摘要并关闭重开核对单元格；若出现异常字符，先 `Esc` 撤销并核对原单元格值。内部修饰键原因未证，前台 Ctrl+S 未测，其他 WPS 版本或应用需自行验证，不据此改变其他界面的通用做法。
- 已有交互会话入口优先复用。确需经 SSH 启动 GUI 而无法显示到用户桌面时，按 `windows-task-scheduler` 的当前权限与范围使用一次性用户交互任务；核实登录用户及真实窗口/结果后删除精确自建任务并回读，不用 SYSTEM 或持久定时器代替。常驻自动化及依赖 Codex 产品专属会话、授权的步骤仍沿现行分工；独立 CLI/SSH 不要求先委派。
- `D:\工作备份\移动硬盘` 是业务主资料，E 盘仅备份；不把备份当活动主目录，也不顺带同步资料。
- 设备和模拟器按项目独占；先回读 `adb devices -l`、`emulator -list-avds`，运行中的模拟器再用 `adb -s <序列号> emu avd name` 确认真实 AVD。未核实归属前不启动、停止、重置或接管其他项目设备。

一次性计划任务与联接限制的本机依据见[协作运维手册](/srv/workspaces/03-env/03-02-claude-codex-collab/docs/协作运维手册.md)“断线恢复与相互修复”。

## 派发 Windows Codex

```text
/srv/workspaces/03-env/03-01-workbench/scripts/win-codex \
  'D:/ws/分区/项目' [--write] [--resume <原生threadId>] < 任务书.txt
```

- 方括号是可选参数说明，运行时替换为真实参数；修改加 `--write`，续接保存的原生 threadId，不猜最新线程。
- 默认只读前缀约束行为，普通 Codex 权限仍为 `danger-full-access`、`never`；不把前缀描述成技术沙箱。
- 记录在 `/srv/work/claude-codex/win-codex-runs/`；回读退出码、错误、最终正文和 threadId，缺少结果视为未知。
- 可能随发起进程退出而中断的长任务，优先用工作台 `dispatch.py` 的独立单元入口；保持同一任务只有一个写入者。

## 最小只读检查

在获得目标主机只读检查授权后执行；需要传入已存在的 Windows Git 项目挂载路径：

```bash
bash scripts/check_windows.sh '/mnt/d/ws/分区/项目'
```

脚本检查 WSL、SSH 用户、`pwsh` 版本、目标仓根和本次命令的 `safe.directory`；不修改 Git 配置或启动模型任务。脚本路径相对于本 Skill 目录。

## 交付检查

- [ ] 区分文件修改、命令执行、界面/设备效果和长期运行验收；只报告已回读的层级。
- [ ] 保留原生 threadId、精确差异、验证、未完成项及未知结果；进展写回目标项目原有交接入口。
- [ ] 核实自建计划任务已删除或说明保留原因；不清理其他项目任务、设备和用户窗口。
