# custom-skills

Personal Codex skills maintained by dmdmwshr for Linux/WSL and Windows.

This repository is the source of truth for self-built skills. Windows installation is managed through CC Switch; this WSL host uses the native publisher in `scripts/`. Official and third-party skills remain with their own installed managers.

## Included skills

- bilinote-video-note
- cc-switch
- drawio-local
- flclash-proxy-toggle
- mineru-local
- obsidian-notes
- summarize-link-note
- xf-report-filler
- zerox-local

## Windows management notes

- This repository at `C:\Users\12070\.cc-switch\skills\自建skills` is the source of truth for self-built skills.
- Installed copies under `C:\Users\12070\.cc-switch\skills\<skill-name>` are cc-switch sync output. Do not edit those copies directly.
- Add or update self-built skills in this repository first, then commit and push to `dmdmwshr/custom-skills`.
- After pushing, sync or refresh through cc-switch so the installed root is regenerated from the repository source.
- Each self-built skill should carry `x-custom-skill: true` and `x-source-repo: dmdmwshr/custom-skills` metadata in `SKILL.md`.
- Do not restore or run the old `skill-updater` workflow.

## WSL 原生发布

WSL 源仓为 `/root/workspaces/custom-skills`，与 Windows 工作树分离。技能修改默认在同一任务完成源文件验证、精确提交及推送，再用 `python3 scripts/publish_wsl_skills.py` 预演、`--apply` 安装、`--verify` 回读，并运行 `python3 scripts/verify_wsl_skills.py` 验证新进程发现；`--skill <name>` 可限定本次对象。当前管理 cc-connect-collaboration、x-message-monitoring、codex-project-task-handoff、codex-archive-retrospective 与 codex-local-state-diagnostics；安装目录为 `/root/.codex/skills`，版本与哈希记录归本机 host-baseline/skill-releases。Windows 的 CC Switch 管理方式保持其本机职责，WSL 不依赖其数据库、进程或安装副本。

## 双系统适配

维护规则见仓库根 `AGENTS.md`。通用流程共用入口，平台路径、工具及发布方式按目标主机分支；Windows 专用技能与业务依赖技能不自动安装到 WSL。源仓兼容性审查、安装发现和实际业务验收分别记录。两侧使用独立工作树，不共享 Codex 活动状态。

正式安装门禁：源仓 main 清洁、上游 origin/main、HEAD 等于实时远端提交、发布文件全部被跟踪、安装副本匹配既有记录。用户明确限制提交或安装时保留已完成阶段，不绕过门禁。每次更新任务负责闭环，不创建额外定时同步。
