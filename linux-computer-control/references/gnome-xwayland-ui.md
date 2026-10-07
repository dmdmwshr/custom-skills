# GNOME Wayland 中的 Xwayland 原生应用

适用于用户已授权操作当前 Linux 桌面应用、AT-SPI 只暴露窗口外壳的情况。2026-10-07 在 GNOME 50 的既有 FlClash 和 Codex 窗口验证；其他桌面、原生 Wayland 窗口或输入后端须重新核对能力。

## 目标与观察

- 从会话元数据取得实际 DISPLAY、XAUTHORITY、DBus 和运行用户，并验证进程身份；不照抄示例显示号或认证路径。AT-SPI 应用名可能是 `com.follow.clash` 而非窗口标题。
- 用现有窗口清单和 `xwininfo` 核对标题、真实 XID、位置与尺寸。`import -window <XID>` 可取得这个 Xwayland 窗口的截图；根窗口截图失败不代表单窗口不可观察。
- 后台窗口截图可以有效，但不能证明前台输入会落到它。输入前用 `xdotool getactivewindow getwindowname` 回读；必要时按当前窗口状态通过正常切换快捷键取得前台。坐标由新截图与窗口位置换算，不能把缩放后的预览像素直接当桌面坐标。

## 公开本机输入接口

XTest 的移动或激活即使退出 0，也可能未改变 GNOME 实际指针或前台。可先通过会话总线 introspect `org.gnome.Mutter.RemoteDesktop` 和 `org.gnome.Mutter.ScreenCast`，按实际暴露的签名创建短期本机输入会话：

1. RemoteDesktop `CreateSession`，读取该会话 `SessionId`；在同一个持续的 DBus 客户端中创建 ScreenCast 会话，属性 `remote-desktop-session-id` 绑定该 ID。
2. 从 DisplayConfig `GetCurrentState` 取得当前显示器 connector，以 ScreenCast `RecordMonitor` 取得 stream；RemoteDesktop 会话 `Start` 后等待设备建立，再调用 `NotifyPointerMotionAbsolute(stream,x,y)`。本机实测 Start 后约 1 秒才有可靠效果，立即输入会无效果。
3. 回读位置及前台。此机实测短期会话 Stop 后用 XTest 点击可让 Flutter 正常响应；不要把 D-Bus 调用成功当成按钮已点击。快捷键按下和释放间保留短暂间隔，再确认前台/页面发生预期变化。
4. 完成后 Stop 自建会话，保留原桌面、应用及远控会话。不得为输入方便修改 GNOME RDP `view-only`，也不调用应用未公开 helper IPC、直接写偏好/数据库或另开应用实例。

## 输入与回读

- 先检查输入法状态。Fcitx 激活时，XTest 英文也可能被组合成中文并丢失空格；URL 控件成功不能证明普通编辑器输入可靠。仅对本次输入临时切为直接输入（本机 `fcitx-remote -c`），完成后恢复原状态。
- 不把未验证的 Unicode 键盘注入用于长指令或消息。语义输入不可用时，可在本机 UTF-8 文件保留完整内容，通过已验证的 ASCII 输入提交简短路径与明确范围；这只适用于接收方本来就能读取该文件，且消息已有用户授权。
- 文件末尾换行可能相当于 Enter。先输入不带结尾换行的文本，待界面完成渲染后逐项回读，再执行提交。发现乱码先更正未提交草稿；若已误提交，核对实际状态并纠正，不能重复发送同一未知结果。
- 应用正常 UI 保存后，只读提取配置中的必要非秘密字段核验。FlClash 的 DNS 覆写可使订阅 DNS 被覆盖；核对 UI 覆写开关、最终 dns.enable、TUN strict-route 和真实请求，不能只凭订阅导入成功声称生效。
