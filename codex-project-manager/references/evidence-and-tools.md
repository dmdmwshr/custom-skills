# 证据与工具入口

以下为 2026-10-20 形成的本机经验与入口定位，使用前核对当前版本、实际路径及授权，不能把历史结果当成当前健康。

## 官方入口

- [Projects and chats](https://learn.chatgpt.com/docs/projects)：本地项目、编辑附件文件夹、主目录和新聊天行为。
- [Codex App Server](https://learn.chatgpt.com/docs/app-server)：公共线程协议和 `skills/list`。调用前核对本机 schema；接口存在、包装支持和桌面已生效分别验收。
- [Developer commands](https://learn.chatgpt.com/docs/developer-commands)：CLI 的 resume/fork 及目录选择语义。

应用管理优先 `list_projects`、`list_threads`、`move_thread_to_project`、`create_thread`、`read_thread`、`wait_threads` 和归档工具等实际暴露能力。工具名可能变化，先发现再使用；不要虚构项目改名或改路径工具。内部数据库不作为日常管理接口。

## Windows 与 WSL 的当前入口

- Windows 工作台：`D:/ws/03-env/03-11-workbench-win/AGENTS.md` 与 `PROJECT_HANDOFF.md`。
- 管理工具源码与迁移维护：`D:/ws/03-env/03-05-codex-env-win`，不在业务目录复制活动工具源码。
- 工作台现有 `scripts/codex_app_rpc.py`：原生 app-server 的元数据查询、受限改名/排序和归档。先读同目录 `codex_app_rpc.md`；当前包装不提供 roots 修改，不因此断言官方 schema 不支持。
- WSL 通过既有 SSH 别名 `windows-host-codex` 调用 Windows 包装时，需确认目标主机和完整线程/项目身份。该通道不是自动取得 Windows 桌面控制权。
- WSL 唯一项目总表当前为 `/srv/workspaces/03-env/03-01-workbench/项目总表.json`，Windows `D:/ws/项目目录索引.md` 为生成结果，不另造并行总表或手改生成索引。

## 经过验证的经验

1. **同根目录可能有不同项目 ID。** 2026-10-20 Windows 桌面 27 项旧名称，经真实编辑项目和应用列表逐项回读后已修正；此前原生协议中的规范名称并未改变桌面显示。不要按 UUID 跨记录域操作，不用重启猜测替代核验。
2. **支持字段不等于已执行。** 2026-10-19 的协议 schema 支持 `project/update` 的 `roots`，但连接旧 socket 失败，实际 0/25 写入。接口能力、连接成功和目标应用生效是不同证据。
3. **配置路径不改变旧会话。** 当时信任路径和自动化提示词已更新，旧线程仍指向失效目录；后来由用户重建的 6 个固定线程目录正确，但原生 projectId 为空。没有用补数据库或再建一遍消除这个差异。
4. **链接清理可造成真实损失。** 历史迁移曾因递归穿透目录联接删除真实文件。清理前核验解析目标、链接身份、活动引用和恢复职责；目录名里有“旧”或“备份”不能证明可删除。
5. **事故恢复不是通用流程。** 曾在应用退出和专门恢复授权下修复内部状态；本 Skill 不复用这条路径作为项目重建手段。

完整回执定位（只按本次需要读取，不加载超大历史）：

- `D:/12070/Documents/工作台管理报告/项目命名核验-20261020/REPORT.md`
- `D:/12070/Documents/迁移备份-20261018/codex-projects/windows-project-update-20261019-094846/执行回执.md`
- `D:/12070/Documents/迁移备份-20261018/codex-sessions-archive/run-20261019-01/收尾回执.md`
- Windows 环境项目 `docs/20261014-开发项目目录迁移.md`

这些路径是本机历史证据，不是其他主机必须存在的依赖。缺少历史报告时使用当前项目状态和公开接口证据继续，不为追溯而扫描所有会话。
