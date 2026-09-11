# 主机选择与发布适配

先确认执行主机、归档所在主机、活动 `CODEX_HOME` 和发布目标。主机不同就分别记录；能读取文件不等于具有目标应用的操作权限。

| 项目 | 本机 Ubuntu/WSL | Windows |
| --- | --- | --- |
| Codex 状态 | `/root/.codex`；其他 Linux 用户回读自己的活动目录 | 回读目标进程实际 `CODEX_HOME`，通常为 `%USERPROFILE%\.codex` |
| 自建源仓 | `/root/workspaces/custom-skills` | 核对本机 CC Switch 登记的 `dmdmwshr/custom-skills` 源工作树 |
| 发布入口 | 本机 `skills-management.md` 和 `scripts/publish_wsl_skills.py --skill <name>` | 本机已安装 `cc-switch` 的受管同步流程 |
| 验收与备份 | `/root/Documents/work/host-baseline/skill-releases`；日常复盘材料放本机 tasks 目录 | 目标机规范指定的任务目录及 CC Switch 发布备份 |

上述绝对路径是已登记的本机约定，不能用于其他 Linux 主机或 Windows。Windows 发布入口缺失时只完成已授权的源码工作并报告缺口，不在 Linux 安装 CC Switch 替代宿主机。

## 归档读取

优先使用当前暴露的任务列表/读取工具，按实际 schema 限定 archived、项目和时间范围。没有上层工具时，可使用本机 Codex 官方 app-server 公共协议；先查该可执行文件版本、帮助和生成的 schema，再进行 initialize、受限 `thread/list`、`thread/read` 或分页读取。不要把别的版本的字段直接传入。

仅为发现验收查询一页元数据，不启动模型 turn，不恢复、创建、改名、归档或重放业务任务。缺少 API 时可读取用户提供的既有脱敏摘要；若没有可用证据，报告读取能力缺口。不直接读取归档 JSONL 或内部数据库来绕过本 Skill 的正文范围。

`thread/list` 返回本机底层会话不等于证明 Desktop 保存项目绑定或 UI 可见性。只有读取结果明确提供的字段才作为证据。父子来源字段缺失时记录未知，不按标题或短前缀合并。持续授权按当前会话范围有效，不因跨轮再次确认。

## 本机 WSL 发布

先读 `/root/Documents/work/host-baseline/skills-management.md`，确认目标已在发布脚本受管名单中。用户要求更新自建技能时，默认先完成验证、精确提交及推送，再更新本机已安装副本；用户明确限制时从其要求。确认源 main 清洁且与实时 origin/main 一致后，仅安装当前授权的技能：

```bash
python3 /root/workspaces/custom-skills/scripts/publish_wsl_skills.py --skill codex-archive-retrospective
python3 /root/workspaces/custom-skills/scripts/publish_wsl_skills.py --skill codex-archive-retrospective --apply
python3 /root/workspaces/custom-skills/scripts/publish_wsl_skills.py --skill codex-archive-retrospective --verify
```

正式安装必须来自已提交、已推送的清洁源树，发布记录保留源提交、远端确认值、`source_dirty=false` 和逐文件哈希；历史 dirty 发布记录不能冒充正式完成。安装副本与既有记录不符时先核对，不能强行覆盖。用 `scripts/verify_wsl_skills.py --skill codex-archive-retrospective` 在新进程验证唯一正确路径、enabled=true 和加载错误；无需为发现验收运行新业务任务。

两侧发布均先完成提交和远端对齐；Windows 的数据库备份与 CC Switch 同步只作用于 Windows 发布。两侧独立工作树通过受控版本同步，不共享活动状态，也不同时编辑同一 Git 工作树。
