---
name: windows-mcp-on-demand
description: 按需使用 Windows-MCP 截图、读取界面元素并操作已授权的 Windows 窗口；Windows 本机调用或 WSL 经 SSH 复用同一客户端。用于明确指定 Windows-MCP 或原生主工具能力不足时；网页内容流程继续用浏览器 Skill。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Windows-MCP 按需控制

本机 Windows 12070 专用。Windows 使用 scripts/client.py；WSL 使用 scripts/wsl_client.py，经已配置的 windows-host-codex 调用相同 Windows 客户端。无需常驻 MCP 注册或重启应用。

开始前读 [操作与部署](references/operations.md)，核当前实际安装、任务、令牌就绪和目标窗口控制者。用户未录入令牌时停在本人录入步骤，不生成、代填或输出秘密。

按已授权范围调用 status → start → tools，确认鉴权、回环监听及工具集合。Snapshot 读取新鲜元素，Screenshot 输出本地文件路径、摘要和媒体类型；不要把图片编码回传。call 的参数来自 UTF-8 JSON 文件。任何运行或界面未知结果先回读，不能自动重放输入。

原生主用仍是 Cua Driver 精确窗口后台操作。Windows-MCP 补充只读观察或必要前台操作；不能把截图成功解释为后台隔离。先核显示布局和唯一自建/授权窗口，能用副屏则用，前台操作后恢复鼠标和焦点。结束时关闭精确自建未保存测试窗口、stop 并回读端口和自有进程退出；保留用户窗口和其他控制者。

PowerShell、FileSystem、Registry、Process、Scrape 在服务端排除且客户端拒绝。Notification/Clipboard/App 等其余工具仍受任务授权限制；发现工具不产生发送、登录或操作其他应用的授权。

2026-10-23已实测Windows本机及WSL经SSH同一客户端的鉴权、15工具、截图、界面读取、中文记事本、不保存关闭和启停收尾；鼠标/焦点最终恢复，原用户窗口保留。此结论仅覆盖本次场景，不代表后台键鼠隔离或任意应用通过。App的近似标题与缓存窗口风险、原失败及独立读证范围见操作文档；后续以03-05唯一交接及回报为准。
