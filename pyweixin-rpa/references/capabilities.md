# 安装与上游能力

- 上游：https://github.com/Hello-Mr-Crab/pywechat
- 安装说明：https://github.com/Hello-Mr-Crab/pywechat/blob/main/QuickStart.md
- 本机固定源码提交：8589baa049bb91d3a500602c167f07b2f8397a13。
- 本机发行包：pywechat127 1.9.8；导入模块为 pyweixin。
- 官方完整 API 速查位于 `D:\Program_Files\pyweixin\upstream\Skill\OtherPlatforms\pyweixin-rpa\references\api_reference.md`，只读所需类和方法。

使用固定运行环境 `D:\Program_Files\pyweixin\.venv\Scripts\python.exe`，直接 `from pyweixin import Tools, Navigator, Messages`。各业务类是静态方法集合，不需要实例化。需要参数签名时可用 `inspect.signature` 查看已安装实现。

| 类 | 公开能力 |
| --- | --- |
| Tools | 进程、版本、目录、窗口与界面辅助 |
| Navigator | 连接或打开主界面、聊天文件、聊天记录、设置等内部窗口 |
| Messages | 界面消息读取、历史读取、会话导出，以及已授权的消息发送 |
| Files | 文件发送、本地文件导出；导出会复制文件并跳过未下载对象 |
| Monitor | 单聊天或会话列表新消息监听 |
| Settings | 自动下载文件大小上限、通知、字体等设置 |
| Collections / Moments / Contacts / FriendSettings / Call | 收藏、朋友圈、通讯录、好友设置及通话，按任务另行授权 |

上游 `Navigator.open_weixin` 会恢复、移动/缩放窗口，并可能关闭主界面的附属面板；`Navigator.connect_weixin` 是更轻的只读连接入口。自定义下载部件应尽量采用后者及上游控件定位定义，逐动作核对，结束恢复实际原窗口状态。

上游导出不能证明客户端拉取成功：`Files.save_chatfiles` 和 `Files.export_recent_files` 跳过“未下载”，`Files.export_videos` 只复制已在本地的视频；`Messages.save_media` 会另存图片/视频，部分图片保存是预览截图。补下载必须检查微信原生文件，不能以这些方法的返回值替代下载证据。
