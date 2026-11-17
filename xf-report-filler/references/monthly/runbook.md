# 月度通报运行手册（给任何模型的最短路径）

先读 SKILL.md，再按本页做；不要自己拼路径、平台或计划任务参数，全部后续命令由 `plan` 打印的“下一步”给出，每条都是可直接粘贴的一行（`shell`），并已带上本次选定的 `--bulletin-dir/--snapshot/--overrides`（人数来自快照里的执法监督人员名录；只有显式使用旧 xlsx 时才带 `--roster`）（路径含空格也已加引号）。

## 唯一入口（先做这一步）

- **Windows 直接入口（优先，生成 Word/Excel 必须在这里，用户桌面 PowerShell）**，不必先切目录：
  `& 'C:\Users\12070\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\12070\.cc-switch\skills\xf-report-filler\scripts\monthly_run.py' plan --year 2026 --month 11`
  （已安装副本在 Skill 发布后才含本入口；发布前用源仓的 UNC 路径 `\\wsl.localhost\Ubuntu-24.04\srv\workspaces\02-software\02-25-custom-skills\xf-report-filler\scripts\monthly_run.py`。）
- **WSL 备选（只读 plan 和飞书导出，系统自带 python3 即可，不需要 openpyxl）**，在任意目录：
  `python3 /srv/workspaces/02-software/02-25-custom-skills/xf-report-filler/scripts/monthly_run.py plan --year 2026 --month 11`
  WSL 里 plan 会把需要 Word/Excel 的步骤打印成可粘贴到 Windows PowerShell 的命令。
- 飞书快照导出的 `snapshot` 步骤在 WSL 执行（Windows 经 `wsl.exe -e python3 …`），plan 已按当前平台给出准确命令。
- 监督系统统计页核对是**可选**交叉核对，不是使用飞书快照的前提。

## 禁止项
不写飞书；不新增计划任务或常驻服务；不改源表、底册、名册、历史月份；覆盖旧文件前先备份；浏览器默认 Ego；`unknown` 的视觉检查不能自己写成 passed。

## 步骤
1. **看今天能做什么**：运行上面的 plan。输出“模式”：今天早于当月 25 日只能“待补”，不能 final-audit；25 日起才是 `final_candidate`，且仍须第 5 步的确认。看“阻塞/提示/定稿前须”，按提示补齐。
2. **飞书快照（根层两表需要，成绩材料生成不需要，二者互不阻断）**：照 `plan` 打印的 snapshot 命令运行：在 WSL 里是 `python3 scripts/feishu_snapshot_export.py --year 2026 --month 11 --out-dir <新目录>`；Windows 没有 lark-cli，命令形如 `wsl.exe -e python3 /mnt/c/Users/<用户>/.cc-switch/skills/xf-report-filler/scripts/feishu_snapshot_export.py --year 2026 --month 11 --out-dir /mnt/d/...`（经 wsl.exe 调用 WSL 里已授权的官方 CLI，已真实验证）。目录必须新建；授权过期时只报告，不要自己登录。导出日 `snapshot.info.json` 会写明窗口、最新检查日期、是否到 25 日。
3. **整理目录**：`monthly_run.py run --stage organize --year 2026 --month 11`（先 dry-run），确认动作后加 `--apply`。
4. **生成前冻结视觉基线**（必须在生成前、整理后）：`monthly_run.py qa baseline --year 2026 --month 11 --qa-dir <任务目录>/qa-v2 [--only archive-宜兴,...]`。它冻结实际执行目录的脚本、配置、模板、名册、快照、覆盖文件；只重做部分成品时用 `--only`，旧收据保留不覆盖。
5. **生成**：`run --stage grade --apply [--force]`（成绩月份材料），`run --stage root --apply`（根层两表，25 日前是待补）。需要本月更正时先按 `monthly_overrides.md` 放好覆盖文件，plan 会自动带上。在 Windows 非交互会话（SSH）里 Word/Excel 打不开，脚本会拒绝并提示换桌面终端。
6. **看图**：导出 PDF/PNG 逐页看；`qa template` 生成全是 `unknown` 的检查模板，看过页面后改为 passed/not_applicable/failed，另存 `<名称>.inspection.json`，再 `qa finalize`、`qa verify`。
7. **交付**：先报正式文件位置、检查结论；分别写 D 盘成品、E 盘备份、待补项、覆盖/缺口三类。
8. **25 日及以后**：重新导出快照（第 2 步）→ （可选）用 Ego 只读查看监督系统统计页交叉核对最新检查日期（未跑通的备选，不要编造接口，不是必须步骤）→ 用户确认数据已最终汇总（把 `snapshot.info.json` 的 `finalDataConfirmed` 改为 true）→ `run --stage root --mode final-audit`。不假设飞书与监督系统“约 1 天”同步。

## 常见失败 → 下一步
| 现象 | 原因 | 做什么 |
|---|---|---|
| `staff_roster_*` blocker | 名册缺队/重名/打不开 | 按提示改名册或联系用户；不要用旧人数 |
| `staff_label_mismatch` | 标签数与姓名数不同 | 照常按姓名数，但提示用户核对漏填 |
| `snapshot_missing` | 没有导出 | 第 2 步 |
| `overrides_unmatched` | 覆盖条目与源数据不再相同 | 核对源文件是否变了，不要删条目硬过 |
| Word/Excel 返回空/拒绝 | 非交互会话或 Excel 忙 | 在桌面终端重试；脚本已对忙碌做有界重试 |
| `final-audit 被拒绝` | 未到 25 日或快照过旧或用户未确认 | 按“定稿前须”处理 |
