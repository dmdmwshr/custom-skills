---
name: native-gui-control
description: 操作 Windows（及指向 Linux）原生图形应用窗口的统一入口：按任务选择 Cua Driver 或按需 Windows-MCP 后端，统一目标窗口、观察-动作-验证、后台/副屏优先、用户输入避让与收尾规则。用户要求操作、读取或自动化真实应用界面（含 WPS、记事本、浏览器工具栏等原生控件）时使用；网页内容用浏览器 Skill，文档内容处理用文档 Skill。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 原生界面控制（统一入口）

先核实目标主机、用户、显示布局、目标窗口及其当前控制者。一个目标一个控制者；用户正在输入或本次明确禁止前台时避让并保留断点，不换通道绕行。两个后端按任务与实测互补，没有固定总排名。

## 后端选择

| 情况 | 用 | 怎么读/调用 |
| --- | --- | --- |
| 能保持原前台、鼠标和输入焦点的动作；按窗口/元素令牌操作；原生菜单路径、窗口几何、用户要求续作 Cua 历史 | **Cua Driver**（CLI 为默认，MCP 仅在已启用时） | 读官方技能包 `C:\Users\12070\.cc-switch\skills\cua-driver\SKILL.md`（CC 以 `local:cua-driver` 登记，版本 0.34.0；Codex 目录有同名副本），再按需读 `WORKFLOW.md`、`RUNTIME.md`、`WINDOWS.md`、`BROWSER.md`；用 `cua-driver --version`、`list-tools`（无需守护进程，当前 59 项）、`describe <工具>`、`doctor` 核二进制与 schema，`status` 报 daemon 未运行属正常的按需状态。注意 `cua-driver skills status` 目前报告“本地技能包未安装”——现有 Skill 不是 `skills install` 的产物，**官方包不编辑**；要刷新只能由维护者在明确范围内运行 `cua-driver skills update`（会向各客户端建链接，属安装动作） |
| Cua 不覆盖或效果不佳的 Windows 动作（如菜单需读取 UI 树后再点、中文输入回读）；需要 UI 树/截图落本地文件 | **Windows-MCP 按需客户端** | 读 [windows-mcp-on-demand](../windows-mcp-on-demand/SKILL.md) 与其 `references/operations.md`；Windows 用 `scripts/client.py`，WSL 用 `scripts/wsl_client.py`（SSH 复用同一客户端）；`status → start → tools → … → stop` |
| Linux/WSL 图形应用 | `linux-computer-control` | 不在本 Skill 范围 |
| 网页内容、标签内流程 | `playwright-browser-control` | 浏览器工具栏/分组菜单等原生控件才回到本 Skill |

两个后端在同一桌面同一时刻只留一个活动控制者；切换后端前先停止/收尾前一个。令牌类前置（Windows-MCP 的令牌只能用户本人隐藏录入）不代填、不读取、不输出。

## 共同流程

1. **精确目标**：先 `list_windows`/等价能力确定唯一窗口句柄；标题近似、缓存窗口、同名窗口不等于目标，不凭标题盲点。
2. **最小读取**：默认**不做全屏/整屏 Snapshot 或 Screenshot**。先给精确窗口或矩形；注意 Windows-MCP 的区域参数只裁剪像素，**不等于窗口隔离，UI 树仍可能含其他窗口正文**。若读到目标外内容，立即按实际范围记录，不把删本地图片或裁图当作已消除读取。
3. **观察 → 单次动作 → 新鲜状态验证**；`effect:"unverifiable"`、成功退出码、截图成功都不是任务成功。部分/取消/未知的输入先回读，不盲重放。
4. **后台/副屏优先**：能用后台路径就不抢前台；需要短暂前台只针对自建窗口，前后记录前台窗口、鼠标位置并在动作后恢复；副屏不隔离鼠标与焦点。用户输入中则停下，等空闲。
5. **菜单不可见不盲点**；弹窗、权限提示、保存密码等属于用户或可信宿主，不得代点来绕过。
6. **只清理自建对象**：关闭自建且未保存的测试窗口、停止自启的服务、回读端口与自有进程；保留用户窗口和其他控制者。

## 已知失败（保留，勿当通用结论外推）

- 本机 Cua 0.34.0 + WPS 表格 12.1.0.28802：后台 `hotkey ctrl+s` 被当作字符 `s` 写入，回执仅 `effect=unverifiable`；保存改用工具栏保存图标并回读文件摘要/重开核值（详见 `windows-from-wsl` 的对应条目）。前台 Ctrl+S 与其他版本未证。
- Windows-MCP：菜单不可见时曾盲点 4 次；用户鼠标活动时继续前台输入属执行失败；保存首击未生效需二次点击；全屏 Snapshot 曾读入他人窗口内容（范围未核实）；近似标题与缓存窗口曾误展开原用户记事本（未输入已恢复）。
- Cua：另存为文件名需多次路由探索；前台恢复与鼠标变化来源未完全核实。

## 边界

PowerShell/FileSystem/Registry/Process/Scrape 在 Windows-MCP 服务端排除且客户端拒绝；其余通知/剪贴板/应用类工具仍受当次授权限制，发现工具不产生发送、登录或操作其他应用的授权。真实 GUI 回归只在用户当次放开且避让其输入时进行；未做的层级如实标未覆盖。
