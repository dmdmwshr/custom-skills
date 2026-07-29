# Browser Tamer 6.0.2 官方实现依据

核对日期：2026-07-29

核对版本：官方 Git 标签 `6.0.2`

提交：`93229bb8fbf9d4017a4ecdbac605236e1eac90e3`

## 官方入口

- 项目主页：<https://www.aloneguid.uk/projects/bt/>
- 官方仓库：<https://github.com/aloneguid/bt>
- 6.0.2 标签：<https://github.com/aloneguid/bt/tree/6.0.2>
- 6.0.2 发布说明：<https://github.com/aloneguid/bt/blob/6.0.2/docs/release-notes.md>

## 已确认结论

### CLI

`bt/bt.cpp` 的参数分发支持：

- 无参数：打开配置界面；
- `<URL>`：按正常规则处理并实际打开；
- `pick <URL>`：强制打开 picker；
- `discover`：重新发现浏览器并保存；
- `browser ...`：进入终端命令。

源码：<https://github.com/aloneguid/bt/blob/6.0.2/bt/bt.cpp>

`bt/cmdline.cpp` 只实现：

- `browser list`
- `browser get default`
- `browser set default <browser-name>.<profile-name>`

没有规则增删改查命令。

源码：<https://github.com/aloneguid/bt/blob/6.0.2/bt/cmdline.cpp>

Windows CLI 构造函数调用 `AttachConsole`/`AllocConsole`，随后把标准流重新打开到 `CONOUT$`。因此某些代理或重定向宿主无法捕获 CLI 文本，不能把“输出为空”直接解释为命令不存在。

本机 6.0.2 构建还出现过 PE `FileVersion`/`ProductVersion` 仍显示 `5.7.0`、但运行窗口标题显示 `6.0.2` 的情况。版本判断不要只依赖文件元数据；同时检查窗口标题、v6 YAML 配置和安装来源。

### `browser set default` 的 6.0.2 限制

6.0.2 的 `exec_set_default` 能查找到浏览器和配置文件，但成功分支只调用 `g_config.serialize()`，没有调用已经存在的 `browser::set_default(...)`。因此此版本不要依赖该命令完成自动变更。

相关源码：

- <https://github.com/aloneguid/bt/blob/6.0.2/bt/cmdline.cpp>
- <https://github.com/aloneguid/bt/blob/6.0.2/bt/app/browser.cpp>

### v6 配置位置与自动保存

6.0.0 发布说明明确：

- Windows 配置迁移到 Roaming AppData；
- 格式从 `config.ini` 改为 `config.yml`；
- v6 与旧配置不向后兼容；
- 配置在后台自动保存和恢复。

实际路径由 `CONFIG_NAME = "Browser Tamer"` 和通用配置类组成：

```text
%APPDATA%\Browser Tamer\config.yml
```

相关源码：

- <https://github.com/aloneguid/bt/blob/6.0.2/bt/globals.h>
- <https://github.com/aloneguid/bt/blob/6.0.2/bt/globals.cpp>
- 配置类依赖提交：<https://github.com/aloneguid/grey/blob/ec1fbd40e5055ff67e237b1382ce89d355c14844/grey/common/config.hpp>
- Windows Roaming 路径实现：<https://github.com/aloneguid/grey/blob/ec1fbd40e5055ff67e237b1382ce89d355c14844/grey/common/fss.cpp>

配置类每约 1 秒比较文件修改时间：外部文件较新时重新读取；内存状态变化时重新序列化。因此直接修改应使用备份、哈希并发检查、同目录临时文件替换和写后回读。

### 规则 YAML

`bt/state.cpp` 确认规则字段：

```yaml
rules:
  -
    loc: url
    scope: domain
    value: openai.com
```

字段含义：

- `value`：匹配值；
- `loc`：匹配来源，如 `url`；
- `scope`：`any`、`domain` 或 `path`；省略时为 `any`；
- `is_regex`：为 `true` 时启用正则；
- `app_mode`：为 `true` 时使用应用模式。

规则序列位于目标浏览器的目标配置文件对象下。源码：
<https://github.com/aloneguid/bt/blob/6.0.2/bt/state.cpp>

## 自动化选择

1. 只读状态和规则列表：直接读取 v6 YAML。
2. 规则新增/删除：备份后修改 v6 YAML，并回读验证。
3. 规则命中解释：使用 Pipeline debugger。
4. 实际打开测试：仅在用户同意后调用 `bt.exe <URL>`。
5. Windows 默认处理器：只读检查 `HTTP`/`HTTPS` 的 `UserChoice`；变更交给 Windows 设置界面。
6. 版本低于 6：停止使用本 skill 的 YAML 写入脚本，转入对应版本界面或官方文档。
