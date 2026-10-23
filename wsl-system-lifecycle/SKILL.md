---
name: wsl-system-lifecycle
description: 在 Windows 宿主机管理 WSL 的开机、关机和资源释放；支持仅关闭 Ubuntu 并保留 Docker，以及 Ubuntu 与 Docker 全部关闭。关机防自动唤醒，开机恢复关机前自启动设置；备份巡检仍使用 wsl-backup-audit。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# WSL 开关机

执行平台为 Windows。本机维护项目是 `D:\ws\03-env\03-06-wsl-maint`，发行版注册名为 `Ubuntu-24.04`。读取该项目 `AGENTS.md`、`PROJECT_HANDOFF.md` 和当前状态后执行用户已明确要求的启停。仅查询或初始化时保持只读。

## 两种关机

- 用户说“关 WSL”“关 Ubuntu”“释放 Ubuntu 资源”时，默认 **只关 Ubuntu，保留 Docker**，使用 `管理WSL.ps1 -Action StopUbuntu`。只屏蔽 Ubuntu 保活、USB 无线自动接管、自动备份与中断恢复四项入口；Docker 的进程、容器、登录启动项及两项相关任务保持。
- 用户明确说“WSL 和 Docker 全关”“全部关闭”时，使用 `-Action StopAll`。此外正常停止当前运行容器、Docker Desktop，屏蔽 Docker 登录启动项、MinerU 和个人记忆系统的 Docker 自启动入口，最后释放整个 WSL 虚拟机。
- 仅关 Ubuntu **不能调用 `wsl --shutdown`**，该命令会连带关闭 Docker 的 WSL 后端。Docker 保留时 `vmmemWSL` 仍存在是正常现象，不能报告全部 WSL 内存已释放。

执行前用 `-Action Status` 只读检查；备份正在运行或有未处理恢复记录时保留断点，先说明准确冲突，不能强制杀掉备份。当前维护入口会使用与备份相同的互斥锁，并在屏蔽唤醒源后复查。其他运行发行版不属于已建恢复方案，不将其悄悄纳入全关范围。

`管理WSL.ps1` 为固定入口，使用项目现有 Python 环境；任务权限不足时经 Windows 标准管理员确认窗口运行，不降低任务权限或创建替代任务。该入口及说明在维护项目中，Skill 不保存第二套实现。

## 开机与恢复自启动

明确开机时使用 `管理WSL.ps1 -Action Start`；事先可用 `-Action PlanStart` 只读查看恢复清单。恢复以 `runtime\system-lifecycle\state.json` 保存的首次关机基线为准：

1. 核对任务身份与定义未改变，保留原来就停用的任务、无线暂停和备份暂停。
2. 恢复本次关闭的 Ubuntu 保活及无线入口，启动 Ubuntu；无线未被原先暂停时等待 USB 无线就绪，保留 `wifi_only` 与严格代理。
3. 最近一次是全部关机时，恢复原 Docker 启动配置、原先运行的容器及其健康状态。容器使用 `unless-stopped` 时，光启动 Docker 不会恢复被手动停止的容器，必须按原 ID 恢复。仅关 Ubuntu 后开机时不操作 Docker。
4. 启动核验后恢复原自动备份设置；不立即执行一份新备份，不恢复原本停用的采集、扫描或其他业务。

首次停机快照不能被重复关机覆盖。两种模式之间切换时，保留原基线；从 Ubuntu 单独停机扩大到全关，另存 Docker 当时的快照。失败保留阶段、回执与原始证据，先回读再续办，不把进程启动或任务启用当成恢复成功。

## 验收与边界

- `Status`、`PlanStart` 只读取 Windows 任务、注册表和运行清单；不进入发行版、不读取 `\\wsl$`、不启动 Docker。
- 仅关 Ubuntu后：Ubuntu 为停止，四项自动唤醒入口已关闭；Docker 原运行状态及容器健康保持。全部关机后：两个发行版均停止、Docker 自动唤醒入口关闭、`vmmemWSL` 消失。
- 开机分别报告任务恢复、Ubuntu 运行、无线就绪、Docker 容器健康和 Linux 失败单元；远控画面、输入与全部业务另行实测。用户要求保持关机时，使用隔离测试与只读恢复计划验证代码，真实开机列为待验收。
- 这些设置阻止既有自动唤醒入口；用户手动打开发行版、访问 WSL 文件入口或其他新工具仍可能启动 WSL。不擅自卸载发行版、停止 WSL 系统服务、修改网络出口或定时安排。
- 原始快照、XML 和运行回执留在项目 `runtime` 或指定私密恢复目录。只读巡检不会解除这里的停机状态；备份健康仍由 `$wsl-backup-audit` 的原入口判定。
