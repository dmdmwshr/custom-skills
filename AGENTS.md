# 自建 Skill 源仓维护

本仓库为 `dmdmwshr/custom-skills` 的受管源码。先遵循执行主机全局规则和当前用户授权；不编辑已安装副本或插件缓存。

- 先辨认技能是跨平台流程、平台专用工具，还是依赖某个业务项目。仅安装任务实际需要且依赖已核对的技能，不因源仓存在就全量安装。
- 通用流程保留一份。路径、进程、编码、服务、浏览器、凭据入口和发布方式在技能内部按实际目标主机分支，长说明放被入口直接引用的 references。不复制一套 linux-/windows- 同义技能。
- 平台专用技能保留清晰范围，例如 Windows Task Scheduler、CC Switch 或 LocalVault；不强行改成 Linux 默认能力。业务专用技能的运行依赖未验收时只声明源码/安装状态，不能宣称业务可用。
- Linux/WSL 与 Windows 使用各自的工作树、`CODEX_HOME`、环境和安装记录。只读引用挂载资料不授权写宿主机或迁移服务；不要把某台机器的路径和版本写成所有平台的事实。
- WSL 受管发布按 `/root/Documents/work/host-baseline/skills-management.md` 和 `scripts/publish_wsl_skills.py --skill <name>` 执行；Windows 按目标机现行 CC Switch 流程。两侧分别验收，不用一侧成功替代另一侧。
- 用户要求更新自建技能时，默认在同一任务内完成审查测试、精确暂存、逻辑提交、推送既有远端分支、更新本机已安装受管副本和新进程发现验证，不以源码已改为收尾。用户明确限制提交、推送或安装时从其要求；新技能首次安装以任务目标为准。
- 先辨明改动归属，保留无关脏文件和暂存内容；只提交本次已审查路径。不强推、不自动 stash 用户文件。推送失败先回读远端，结果未知不盲重发；安装必须锚定已确认的远端提交。远端新增提交后重新比较和整合，不用旧 origin/main 冒充实时状态。
- 自建技能 frontmatter 保留 `metadata.x-custom-skill: true` 和 `metadata.x-source-repo: dmdmwshr/custom-skills`。修改前检查差异、保留用户已有改动，发布记录注明源提交、dirty 状态和哈希。
- 变更先验证格式、引用和对应行为；只有在实际平台运行后才称该平台已验收。当前工具未暴露不等于版本不支持；可核对文档化接口及当前 schema，不猜内部 RPC、不手改应用状态。
