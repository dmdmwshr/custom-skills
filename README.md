# custom-skills

Personal Codex skills maintained by dmdmwshr for Linux/WSL and Windows.

This repository is the source of truth for self-built skills. Windows installation is managed through CC Switch; this WSL host uses the native publisher in `scripts/`. Official and third-party skills remain with their own installed managers.

## Included skills

分类以 platforms.json 为准（“WSL 受管”指 managed_wsl_order，其余为 Windows 专用，由 CC Switch 安装）。

- **跨主机/协作入口（WSL 受管）**：windows-from-wsl、claude-session-ops、native-gui-control、windows-mcp-on-demand（native-gui-control 的活动底层之一）、linux-computer-control、playwright-browser-control（浏览器默认是官方 ego-browser；本项为显式指定的备用入口）
- **Codex 管理（跨平台，WSL 受管）**：codex-project-task-handoff、codex-project-manager、codex-archive-retrospective、codex-local-state-diagnostics、personal-memory-on-demand、markitdown-local、feishu-operations
- **业务运维（WSL 受管）**：research-data-ops（macro-strategy + 已退役 OKXnew 只读参考）、server-traffic-sync
- **路由前门（Windows）**：cloud-docs（金山 / WPS 365 / 飞书）、notion-workflows、cloudflare-platform、github-flow、security-review。其后端 Skill 的 CC 条目按设计保持“未启用”，只作文件读取，不是废弃
- **视频**：video-intake（唯一核心）；video-download、bilinote-video-note 为兼容桩，仍被机器人路径和提示引用
- **文档与资料（Windows）**：wps-office（内置 CLI 28 命令与编辑需求评估；Windows 原生运行/会员权益分层验收）、drawio-local（官方 draw.io Desktop CLI）、docling-local、mineru-local、zerox-local、obsidian-notes、summarize-link-note、wechat-mp-tools、wx-cli、pyweixin-rpa、xfrhtx-cli
- **本机环境（Windows）**：cc-switch、flclash-proxy-toggle、browser-tamer、localvault-credentials、serena-local、windows-task-scheduler、wsl-backup-audit、wsl-system-lifecycle、comfyui-on-demand、comfyui-production-manager、applio-rvc-manager、meifu-resumable-download
- **消防业务（Windows）**：xf-expert-review、xf-information-file-manager、xf-product-case-filler、xf-product-case-registry、xf-product-standards、xf-report-filler

## Windows management notes

- The source of truth is this WSL repository (`/srv/workspaces/02-software/02-25-custom-skills`). The older Windows checkout at `C:\Users\12070\.cc-switch\skills\自建skills` is not a development tree (it carries unrelated uncommitted differences and the Slack agent's legacy compat path); Windows releases use a clean release checkout with the CC Switch sync script.
- Installed copies under `C:\Users\12070\.cc-switch\skills\<skill-name>` are cc-switch sync output. Do not edit those copies directly.
- Add or update self-built skills in this repository first, then commit and push to `dmdmwshr/custom-skills`.
- After pushing, sync or refresh through cc-switch so the installed root is regenerated from the repository source.
- Each self-built skill should carry `x-custom-skill: true` and `x-source-repo: dmdmwshr/custom-skills` metadata in `SKILL.md`.
- Do not restore or run the old `skill-updater` workflow.

## WSL 原生发布

WSL 源仓按本机公共路径配置定位，与 Windows 工作树分离。技能修改默认在同一任务完成源文件验证、精确提交及推送，再用 `python3 scripts/publish_wsl_skills.py` 预演、`--apply` 安装、`--verify` 回读，并运行 `python3 scripts/verify_wsl_skills.py` 验证新进程发现；`--skill <name>` 可重复指定以限定本次对象。受管名单以 platforms.json 为准；安装目录为当前用户活动 CODEX_HOME 下的 skills，版本与哈希记录归本机 host-baseline/skill-releases。Windows 的 CC Switch 管理方式保持其本机职责，WSL 不依赖其数据库、进程或安装副本。

## 双系统适配

维护规则见仓库根 `AGENTS.md`。通用流程共用入口，平台路径、工具及发布方式按目标主机分支；Windows 专用技能与业务依赖技能不自动安装到 WSL。源仓兼容性审查、安装发现和实际业务验收分别记录。两侧使用独立工作树，不共享 Codex 活动状态。

正式安装门禁：源仓 main 清洁、上游 origin/main、HEAD 等于实时远端提交、发布文件全部被跟踪、安装副本匹配既有记录。用户明确限制提交或安装时保留已完成阶段，不绕过门禁。每次更新任务负责闭环，不创建额外定时同步。

## 平台发现与按需加载

platforms.json 是受管平台清单；scripts/skill_platforms.py 提供只读执行主机预检。原生发布器拒绝 Windows 专用技能，不自动安装源仓全部技能。受管范围以清单的 managed_wsl_order 为准，包含独立的 feishu-operations、windows-from-wsl 与 claude-session-ops；后两项在 WSL 执行，分别依赖 Windows 宿主机通道和 Claude 桌面会话工具。声明支持不代表业务实机验收。公共路径读取 /etc/codex-dev/paths.json（若目标机存在）或实际环境；不会从 Linux 共享用户认证到 Windows。
