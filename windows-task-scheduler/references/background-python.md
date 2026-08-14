# 后台 Python 无窗口启动规范

## 环境和启动器

1. 项目继续遵循自己的 Python 环境事实源。普通 `uv` 项目使用项目 `.venv`，不得依赖系统 `PATH` 偶然命中 `python`。
2. 不得仅凭文件名认定 `.venv\Scripts\pythonw.exe` 是无控制台启动器；`uv` 在 Windows 生成的 `python.exe` 与 `pythonw.exe` 可能是同一控制台重定向器。
3. 后台任务使用项目准备并验证的 `.venv\TaskScripts\pythonw.exe`。推荐从当前项目所用 Python 的 `Lib\venv\scripts\nt\pythonw.exe` 复制，但必须先确认来源属于项目当前解释器。
4. 固定动作解释器的绝对路径、脚本绝对路径和工作目录，不依赖登录 shell 或系统 `PATH`。

## 强制验证

1. 读取 PE 头，确认启动器子系统为 `Windows GUI`，不能只是名称为 `pythonw.exe`。
2. 计算 SHA-256，确认 `.venv\TaskScripts\pythonw.exe` 不等于 `.venv\Scripts\python.exe` 或 `.venv\Scripts\pythonw.exe` 的控制台重定向器。
3. Python 参数首部默认使用 `-B`，避免高频任务生成 `__pycache__` 和 `.pyc`。
4. `TaskHidden=true` 只隐藏任务条目，不证明进程不会闪窗。
5. 不用 `powershell.exe`、`cmd.exe` 或 `wscript.exe + VBS` 套壳隐藏控制台；任务停止时这类套壳容易遗留子进程。

## 注册脚本与日志

- PowerShell 5.1 注册脚本在含中文时保存为 UTF-8 BOM，并在执行前做语法检查。
- 失败日志设置大小上限和轮换数量，不在日志或任务参数中记录密钥、口令、令牌或带签名链接。
- 缓存治理优先从启动参数禁止生成；清理脚本只使用项目和缓存目录的精确白名单，排除 `.git`、`.venv`、数据库、日志、运行态、私有配置和重解析点。

## 生命周期验收

1. 回读任务动作路径、参数、工作目录、运行账户和设置。
2. 启动任务，确认没有新增控制台窗口宿主，并按任务语义检查进程、端口、健康接口或产出。
3. 停止任务，确认对应进程链退出且没有遗留子进程。
4. 再次启动并复核业务健康；只有完整生命周期通过时，才报告“无窗口后台运行”已验证。
