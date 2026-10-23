# 模式 B：隔离调试与本地网页测试

用于本地开发应用、静态页、Electron 的功能/视觉调试与回归，**不需要也不得使用真实登录资料**。受管真实资料（登录态、业务站点）一律走模式 A（SKILL.md 的主流程），两种模式的登记、会话、收尾互不借用。

## 何时用

| 任务 | 入口 |
| --- | --- |
| 用终端命令快速开页、快照、点击、截图、录屏、网络检查 | `scripts/isolated_cli.py` 包装 playwright-cli（命令表见 `upstream/playwright-cli.md`、`upstream/playwright-cli-commands.md`、`upstream/playwright-cli-workflows.md`） |
| 写 Python Playwright 脚本测试本机 Web 应用，需要先起前后端服务 | `scripts/webapp/with_server.py` + `examples/`（说明见 `upstream/webapp-testing.md`；脚本依赖 `pip install playwright`，未安装时先用上一行的 CLI） |
| 持久 Node 会话反复调试同一页面/Electron（句柄不重建，功能+视觉 QA） | `upstream/playwright-interactive.md`：Codex 需启用 `js_repl`（默认关闭，且要求 danger-full-access）；Claude 使用 `node_repl` 的 `js` 工具实现同一工作流 |

## 隔离硬规则

1. 只用本次新建的内存临时浏览器；包装器拒绝 `attach`、`--extension`、`--profile`、`--persistent`、`--user-data-dir`、`--cdp`、`close-all`、`kill-all`、`delete-data`、`list`、自带 `-s/--session`，因为这些会触达受管资料或他人会话。
2. 会话名固定加 `iso-` 前缀并登记在 `PBC_ISOLATED_ROOT`（默认系统临时目录下 `pbc-isolated`）；同名不得重复 `open`；`open` 默认附加 10 分钟空闲回收。
3. 结束时必须 `isolated_cli.py finish <会话名>`（close + delete-data，仅限已登记会话）；用 with_server 起的服务由它按进程树停止。下一次任务使用新会话名与新工作目录。
4. 不登录真实账号、不导入 Cookie/凭据、不访问业务站点；需要时改走模式 A。公开页或本地页通过只证明页面行为，不证明业务登录或权限。
5. 截图/trace/视频落在会话工作目录或仓库约定的 `output/playwright/`，不入 Git。
6. 引擎来源：默认 `playwright-cli`（已装的全局命令，不用 npx 临时拉包）；浏览器通道默认 Chrome，缺失时 `--browser msedge`。需要安装浏览器属于软件安装，按当前授权与主机规则执行并登记。
7. 模式 B 的失败按原样保留，不用“再开一个新会话”掩盖；未知点击/提交结果先 `snapshot` 回读再决定。

## 命令示例（Windows/WSL 相同）

```text
python scripts/isolated_cli.py run demo -- open http://127.0.0.1:5173 --browser chrome
python scripts/isolated_cli.py run demo -- snapshot
python scripts/isolated_cli.py run demo -- click e3
python scripts/isolated_cli.py run demo -- screenshot --filename=page.png
python scripts/isolated_cli.py finish demo
python scripts/webapp/with_server.py --server "npm run dev" --port 5173 -- python your_test.py
```

## 许可与来源

`upstream/` 与 `scripts/webapp/`、`examples/` 来自 OpenAI curated `playwright`、`playwright-interactive` 与 Anthropic `webapp-testing`（均 Apache-2.0，原文在 `licenses/`）。与上游的差异：①不再随附 bash 包装器 `playwright_cli.sh`；②`with_server.py` 修复仅停 shell 而残留服务的缺陷（见 `licenses/NOTICE-webapp-testing.txt`）。其余上游正文原样保留，仅在文件头加适用边界说明。
