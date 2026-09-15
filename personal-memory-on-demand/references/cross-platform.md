# 跨平台连接与来源

服务入口可为内网 HTTP 或预先配置的公网 HTTPS。本轮默认 Windows 网页 5175，API 位于同源 `/api`；不要求登录。此配置能力不代表公网已开通。

## 配置解析

1. 服务地址：显式 `--api-url` > `MEMORY_API_URL` > 本机 `client.json` 的 `api_url` > 平台默认值。Windows/普通 Linux 默认为本机 5175；WSL 通过 Windows 互操作查询 `vEthernet (WSL)` 地址。失败就报告；不把独立 Wi-Fi 默认网关当作 Windows。
2. 配置文件：显式 `--config` > `MEMORY_CLIENT_CONFIG` > Windows 的 `%LOCALAPPDATA%/PersonalMemorySystemV1/config/client.json`，或 Linux/WSL 的 `${XDG_CONFIG_HOME:-~/.config}/personal-memory/client.json`。
3. 当前设备：`MEMORY_SOURCE_ID` > 配置的 `source_id`。只有 Windows 可默认 `windows-local`；其他设备缺少持久编号时不能默认操作 Windows。`platform` 与实际执行平台不符时停止。
4. 项目根：`MEMORY_PROJECT_ROOT` > 配置 `project_root` > `CODEX_PROJECTS_ROOT` > 受管 `/etc/codex-dev/paths.json` > 平台默认用户项目目录。归档根：`MEMORY_ARCHIVE_ROOT` > 配置 `archive_root` > 当前进程的 `CODEX_HOME/archived_sessions`。只处理归档目录，不读取活跃 `sessions`。这些目录也由同配置的采集端解析；远端指令不能任意改变采集范围。
5. 可配置 `instance_id` 校验服务身份。地址变化后仍须匹配原服务；不会自动上传到另一套记忆库。内网不继承系统代理。显式 `use_proxy: true` 可为 HTTPS 使用已有代理，仍保持标准证书校验。

`connection-info` 只显示非敏感连接与目录配置，不访问图谱或修改队列。查询不需要本机项目源码、Windows 专用解释器或数据库权限。

## 采集端和回读

Linux/WSL 由记忆系统项目提供轻量采集包和用户服务 `personal-memory-source.service`，使用已有受管工具环境及独立 uv 虚拟环境；模型提取和图谱写入留在 Windows。服务启动及每 6 小时同步，在线时接收网页指令，断网保留待传文件和原批次编号。首次历史归档最多 5 个合规新内容任务，来源与图谱回读验收后继续全量。

完成后登记针对调用端的来源编号。按设备查询的软件属性包含该设备的版本和路径；全部来源视图保留各设备记录。批次已接收、模型处理中、图谱已写入和过滤未形成事实是不同状态。

## 模型配置与设备资产

`model-settings` 查询的是所有来源共用的记忆服务端配置，不随 `--source` 改成采集设备上的模型。设备的 `inventory_collectors.skipped.models=not_configured` 表示未配置可选的模型资产盘点，不是记忆处理失败。已配置盘点但连接失败仍保留在 `failed`，不能据此判断服务端主模型、备用模型或向量模型不可用。

采集端确实安装了自己的 Ollama 并需要登记其模型时，在该设备的 `client.json` 中设置 `model_inventory_url`，例如 `http://127.0.0.1:11434`。仅接受本机回环地址；这是设备资产采集配置，不改变服务端的记忆处理模型。未配置、连接失败时均保留原有资产记录，只有完整成功的盘点才使该来源缺席的旧记录失效。

## 发布与平台验收

- Windows：在源仓完成提交推送后运行 `cc-switch/scripts/sync-custom-skills.ps1 -Skill personal-memory-on-demand -WhatIf`，通过后去掉 `-WhatIf`；核对源、安装副本、Codex 入口及数据库登记。
- WSL：从自己的干净源工作树运行 `scripts/publish_wsl_skills.py --skill personal-memory-on-demand`，再 `--apply`、`--verify` 和 `scripts/verify_wsl_skills.py --skill personal-memory-on-demand`；使用当前用户的 CODEX_HOME。
- 用离线测试覆盖 Windows、WSL 与普通 Linux 的配置优先级和来源边界；实际内网查询、完成后登记和后台恢复只在完成对应主机回读后称为实机通过。
