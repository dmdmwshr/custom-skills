# FlClash 配置分层与维护契约

## 适用范围

本参考用于 Windows 桌面版 FlClash 的配置定位、持久化判断和安全维护。界面名称与数据库 schema 可能随版本变化；执行前必须结合当前安装的只读事实复核。

官方依据于 2026-07-31 核对 FlClash 主仓库提交 `7c83185`：

- 项目与桌面界面说明：<https://github.com/chen08209/FlClash>
- 数据路径：`lib/common/path.dart`
- 数据库定义：`lib/database/database.dart`、`profiles.dart`、`rules.dart`、`links.dart`、`groups.dart`
- 生成配置合并：`lib/common/task.dart`
- Profile 覆写界面：`lib/views/profiles/overwrite/overwrite.dart`
- 通用设置中的 external controller：`lib/views/config/general.dart`
- 桌面入口：`lib/main.dart`

这些是实现证据，不把内部文件格式提升为稳定公共 API。

## 一、桌面持久配置与 Mihomo YAML 不是同一概念

### 1. 应用持久设置

官方路径实现把 `shared_preferences.json` 放在应用 support 目录。它承载 FlClash UI 的应用设置和 PatchClashConfig 等状态，例如端口、TUN、日志等级或 external controller 开关。

维护结论：

- 正式修改入口是 FlClash 设置 UI。
- 文件适合只读核对，不适合作为离线配置入口。
- 应用仍在运行时直接改文件可能被内存状态覆盖。
- 文件中可能包含用户环境信息，输出必须按允许字段脱敏。

### 2. 配置档案与覆写数据库

官方当前使用 Drift/SQLite，数据库名为 `database.sqlite`，schemaVersion 当前为 2。官方注册的表包括：

- `profiles`：档案元数据、覆写类型、当前组名、`selected_map` 等；
- `scripts`：脚本覆写；
- `rules`：结构化规则；
- `profile_rule_mapping`：档案与规则的映射、场景和顺序；
- `proxy_groups`：自定义策略组；
- `icon_records`：图标缓存记录。

Profile 覆写类型是：

- `standard`：标准覆写，读取附加规则；
- `script`：脚本覆写；
- `custom`：自定义策略组与规则。

维护结论：

- UI 中“配置档案 → 覆写”是规则/策略组的持久入口。
- `selected_map` 是 FlClash 保存策略组选择的位置之一；API/界面切换后的持久结果应回读。
- 数据库是内部实现。schema、字段与写入副作用可能迁移，不能制作通用写库 CLI。
- 普通配置维护走 UI；数据库默认只读。

### 3. 订阅或文件源

`profiles/*.yaml` 是下载或导入的源 Profile；provider 内容在其子目录缓存。

维护结论：

- 源 YAML 可被订阅更新覆盖。
- 客户端附加规则不一定存在于源 YAML，而可能只存在数据库。
- 发现“源文件没有某规则、生成配置却有”时，应继续检查数据库覆写，不得直接判定异常来源不存在。
- 读取时禁止输出订阅 URL 与代理节点详情。

### 4. 运行时生成配置

官方 `makeRealProfileTask` 会把源 Profile、应用 PatchClashConfig、标准/脚本/自定义覆写、DNS 和系统设置合并，最后生成供核心使用的 `config.yaml`。例如源码会用应用补丁值覆盖 `external-controller`、端口、TUN、模式等字段。

维护结论：

- `config.yaml` 是输出，不是桌面配置事实源。
- 直接编辑可能在下次应用、切换或订阅更新时丢失。
- 只改 `config.yaml` 不能证明 UI/数据库已持久化。
- 它仍是重要的“合并结果”验收层，只允许脱敏读取。

### 5. 核运行状态

FlClashCore 实际加载的配置、当前策略组、连接和规则命中是运行事实。

维护结论：

- 文件已更新不等于核心已加载。
- 不得为了刷新状态自行关闭或重启应用。
- 优先让 FlClash UI 正常应用，然后通过 UI 或 external controller 回读。
- 运行态选择与持久 `selected_map` 都需要时，应分别核对。

## 二、桌面界面映射

官方桌面导航按窗口宽度自适应，部分页面可能收入“更多”。按功能理解：

| 界面 | 主要职责 | 不是 |
|---|---|---|
| 仪表盘 | 运行时间、流量、网络检测、状态概览 | 持久规则编辑器 |
| 代理 | 策略组、节点、延迟、当前选择 | 订阅源编辑器 |
| 配置 | Profile 的新增、更新、切换、详情 | 磁盘 `config.yaml` 文本编辑 |
| 配置 → 覆写 | 标准、脚本、自定义覆写与预览 | 强制重启入口 |
| 请求 | 规则命中/请求观测 | 规则持久事实源 |
| 连接 | 当前连接观测与关闭连接 | 代理应用退出 |
| 工具 | 日志、备份恢复等辅助功能 | 稳定自动化 CLI |
| 应用设置 → 常规 | 端口、external controller 等应用补丁 | Profile 源 YAML |

版本变化时，以当前窗口的可访问文本和官方同版本源码为准。无标签图标不做坐标猜测。

## 三、是否存在 CLI 或可编程配置入口

### Windows CLI

截至上述核对点：

- 官方 README 只公开 Android 的 `START`、`STOP`、`TOGGLE` action。
- Windows/Dart 入口是无参数 `main()`。
- 未发现官方文档化的 Windows 管理 CLI。

因此：

- 不把 `FlClash.exe` 参数、helper service IPC 或偶然可用的内部调用当作稳定 CLI。
- 不新建“结束并重启 FlClash”的伪 CLI 来完成配置维护。
- 需要自动化时，优先选择只读脚本；写操作仍回到 UI 或受控的 external controller。

### external controller

启用后可使用 Mihomo 兼容控制器读取运行状态、策略组和连接，并精确切换策略组。

边界：

- 仅监听本机回环；若当前版本允许非回环，默认不开放。
- 密钥只从受保护本地事实读取并直接交给请求层，不输出到模型、日志或回复。
- API 写操作必须有用户明确的精确目标。
- 它管理核心运行态，不是完整 FlClash Profile/数据库管理 API。
- controller 未启用时，不得通过直接编辑生成文件后重启来“绕过”。

### SQLite

SQLite 可用于：

- `PRAGMA user_version`、`integrity_check`；
- 表/列存在性；
- 脱敏计数；
- 精确目标规则、映射和选择的只读来源确认；
- 在用户明确批准的维护窗口做一致性备份。

SQLite 不应用于：

- 常规日常规则增删；
- 应用运行时的无备份写入；
- 通配批量删除；
- 读取/输出 `profiles.url`；
- 伪装成官方 CLI；
- 以“写完数据库”为完成证据而不经过 UI/核心回读。

## 四、安全决策树

1. 用户只说“查看/核验”：
   - 只读脚本、UI 观察、数据库只读查询；
   - 不点击开关，不调用 PUT/PATCH，不写文件。

2. 用户说“切换某策略组”：
   - 精确确认组与目标；
   - controller 可用则精确 API，否则 UI；
   - 回读当前选择；
   - 保持代理运行态不变。

3. 用户说“修改规则/设置”：
   - 先定位属于常规设置还是 Profile 覆写；
   - UI 修改、预览/保存/应用；
   - 核对数据库、生成配置、核心；
   - 不退出、不重启。

4. UI 无法完成且怀疑数据库异常：
   - 只读查明精确行和关系；
   - 报告恢复方案与并发风险；
   - 等用户明确批准维护窗口；
   - 智能体不得自行暂停或关闭代理来制造维护窗口。

5. 用户说“暂停代理”：
   - 只授权暂停，不授权退出；
   - 操作后至少两项独立证据回读。

6. 用户说“关闭 FlClash”：
   - 这是独立高风险动作；
   - 先确认代理、安全网络状态和系统代理/TUN；
   - 无法确认则拒绝强制关闭；
   - `taskkill`/`Stop-Process` 仍需额外明确授权。

## 五、验收证据

配置变更完成至少需要：

1. 持久层：UI/数据库回读目标值；
2. 合并层：生成 `config.yaml` 的脱敏目标摘要；
3. 运行层：核心/UI/controller 的当前状态；
4. 网络层：与任务相关的公开探针；
5. 安全层：确认未发生未授权暂停、退出、重启或进程终止。

任何一层未验证都应写“未核验”，不能用“已保存”替代完整完成。
