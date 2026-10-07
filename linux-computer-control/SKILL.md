---
name: linux-computer-control
description: 控制 Linux/WSL 原生图形应用，或比较当前桌面与隔离隐藏桌面的能力。支持 AT-SPI 控件读取、中文输入、按钮操作及隐藏桌面截图；用于实际 GUI 操作，不用于纯终端维护、Windows 或浏览器控制。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Linux 电脑控制

先区分目标：用户当前 Linux 桌面的已有原生应用用 `linux-desktop` MCP；可在独立桌面启动的应用测试用 `agent-workspace-linux`。浏览器仍按用户的 Playwright 约束和受管入口执行，不以原生点击、截图或键盘操作代替。

## 当前桌面

在实际调用主机核对 OS、UID、GNOME 会话和部署配置。WSL 的当前入口及版本记录位于 `/srv/work/host-baseline/linux-computer-control.md`；其他 Linux 主机先核实对应部署，不复制本机 DISPLAY、用户名或会话文件。

本机入口 `/usr/local/libexec/codex-host/linux-desktop-mcp --check` 只读验证身份；无参数为 MCP stdio。它从调用者 `/run/user/<uid>/codex-gnome-session.json` 取得桌面环境，核对 PID/starttime、所有者、DBus 与 Wayland socket；配置 `/etc/codex-dev/linux-desktop-mcp.json` 选择运行版本。正常会话退出后重新核验，不沿用旧环境或切到 root。

1. 先 `desktop_capabilities`，再按目标应用 `desktop_snapshot(app_name=...)`。从实时结果取 ref，不猜测；避免无目标的全桌面正文枚举。
2. 可编辑控件使用 `desktop_type(ref,text,clear_first=true)`；按钮和勾选使用 `desktop_click(ref)`。每次关键操作后回读控件状态或应用自己的结果。
3. ref 绑定当前 MCP 客户端状态，连续操作保持同一连接。连接更换后重新 snapshot；未知写入先回读，不重放。
4. 只关闭本任务创建的测试窗口。`desktop_release_window` 仅释放工具跟踪，不等于关闭真实窗口。

如果当前聊天没有暴露新 MCP 工具，已注册配置可供新客户端加载；不为此重启全部 app-server/MCP。可使用部署运行环境中的官方 Python `mcp.ClientSession` + `stdio_client(StdioServerParameters(command=入口))`，持续一个客户端完成 `initialize → list_tools → call_tool`。普通 shell 执行该入口时输出是 JSON-RPC，不是人类交互终端。工具返回文本 `Error:` 时即使 `isError=false` 也按失败处理。

### 已验证边界

- 2026-10-07 WSL Ubuntu 26.04 / GNOME Wayland：GTK 原生窗口的语义树、完整中文输入、勾选、按钮、结果回读和关闭已验证。
- upstream 0.2.0 在新 GI 上有 `Accessible.get_text` 同名方法冲突；主机补丁改用 `Atspi.Text.get_text(iface,...)`。`EditableText.insert_text` 的长度传 UTF-8 字节数，避免中文截断。上游源码和主机补丁独立保留，不改插件缓存。
- MCP 当时暴露 10 个工具，没有截图工具。`xdotool canClick/canType=true` 不能证明原生 Wayland 全局鼠标、键盘、拖拽或快捷键可用。优先实测语义动作；控件不暴露可访问性时报告具体缺口，不宣称可控制所有应用。

## 隔离隐藏桌面

优先按 `agent-workspace-linux` 的已安装 Skill 分阶段加载 MCP。若 MCP 因其他有效客户端持有锁而握手失败，不终止该连接或启动第二个 MCP 冒充恢复。已授权目标可通过本机受管 CLI `/usr/local/bin/agent-workspace-linux` 完成；它固定 agent-ws 身份和权限上限，调用前查 status、profile 与权限文件。普通用户按需要 `sudo -n` 执行，不直接绕过 wrapper 使用宽权限二进制。

本机已有 `codex-hidden` 是共享服务。测试另建唯一工作区；不要停止或复用在用工作区。创建临时 systemd 单元时使用 `User=agent-ws`、`PrivateTmp=yes` 及 `JoinsNamespaceOf=agent-workspace-linux.service`，通过固定 CLI `workspace start --foreground --profile codex-hidden --id <本次ID> --ack-hidden-workspace --purpose <目的>` 启动。共享临时目录仅为 X 显示号分配看到已有锁；工作区仍有独立 display/Xauthority 和应用命名空间。若现有服务不存在，先按实际主机配置选择启动方式，不照抄加入不存在的服务。

- `workspace launch` 也须指定 `--profile codex-hidden`，否则文件挂载权限检查会拒绝。不放宽全局权限来绕过；本机默认只挂载登记的 test-data，网络 local_only。
- 从 launch/windows 回读真实 window ID；`screenshot-window` 截图后用图像查看工具观察，再以窗口局部坐标 click-window。
- 中文优先 `paste-window`，只使用该隔离桌面剪贴板。实际结果要从应用或截图回读，不能只凭 ok。
- 完成后关闭自建窗口、`workspace stop --id <本次ID>`、`workspace cleanup --id <本次ID>`，再次 list 核对不存在；原共享服务应仍在运行。

本机已实测独立 :91 工作区的应用启动、窗口截图、中文粘贴、勾选、保存及清理。其 CLI 证据不代表 Codex MCP 握手已恢复，也不代表网络访问或未测应用已通过。
