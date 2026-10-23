---
name: research-data-ops
description: 研究数据运维统一入口，三个互不接管的域：macro-strategy 宏观与黄金研究的数据采集、来源覆盖、分页恢复、只读 API 与受限发布（运行中，WSL）；以及已退役 OKXnew 的采集与历史回测运维——仅作只读审计/归档参考，不恢复服务。用于 macro-strategy 数据缺口、采集诊断、部署与覆盖报告，或用户明确要求审计旧 OKXnew 采集/回测证据；不用于账户、真实交易、通知或下单。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 研究数据运维（三域，互不接管）

先确认目标域与实际主机（本入口流程均在 WSL/Linux 验证），再**只读取该域模块**：

| 域 | 状态 | 读取 | 运行根/资料 |
| --- | --- | --- | --- |
| macro-strategy 宏观与黄金研究 | **运行中**（02-27，WSL，独立项目） | [references/macro-strategy.md](references/macro-strategy.md) | 项目配置与 systemd 回读所定的独立运行根、缓存、检查点；一个业务写者 |
| OKXnew 数据采集（含金十优先宏观来源、现货/永续行情） | **已退役，只读封存** | [references/okxnew-data/module.md](references/okxnew-data/module.md) | 历史契约 `/var/lib/okxnew`（现不视为存在） |
| OKXnew 官方历史数据回测/研究验证 | **已退役，只读封存** | [references/okxnew-backtest/module.md](references/okxnew-backtest/module.md) | 历史冻结计划、job、runner 契约 |

## 域边界（不得互相接管）

1. **macro-strategy 与 OKXnew 独立**：旧 OKXnew 的代码、运行库、检查点、服务、交易认证、Cookie 不能成为 macro-strategy 的恢复入口，反之亦然（02-27 `AGENTS.md`：不导入或链接 OKXnew 代码/数据/检查点/运行根/服务/旧控制台）。
2. **采集 ≠ 回测**：OKXnew 数据模块只出当前数据证据与转交摘要；回测 runner、冻结计划、分段由回测模块的门禁处理，数据模块不创建替代 job、不启动第二个 runner。
3. **暂停/恢复各域独立**：已明确的暂停、`migration_not_activated` 失败关闭、锁租约、检查点、Retry-After、next_due 保持各域原语义；不因合并而统一启停或统一恢复。
4. **OKXnew 已退役**：证据见两个 OKX 模块顶部横幅；任何“恢复运行/重建项目/起采集/起回测”都需要用户新的明确指令，且先核对项目与运行根是否存在；路径缺失本身不是授权也不是退役证据。
5. 全部域：**不做真实交易、不读账户/仓位/委托、不读密钥/Cookie/浏览器存储、不外发通知**；已授权的非交易采集凭据只由既有受管服务载入，不入输出。

## 共用约定

- 平台预检：OKX 两模块各带 `host-platform.md`；macro 模块自身说明主机/用户/项目根核对。`scripts/skill_platforms.py --skill research-data-ops` 可只读回读系统与配置，不证明业务服务可用。
- 唯一源码为 `dmdmwshr/custom-skills`；安装走当前主机受管发布器，不直接编辑安装目录，不联动修改 Windows。
- 自动只读巡检不修改源码、计划或服务；用户明确要求修复/部署时才在各模块规定的受控流程内实施。
- 旧入口名 `macro-strategy-data-operations`、`okxnew-data-operations`、`okxnew-backtest-operations` 已并入本入口；各模块正文保持原契约，仅有相对链接/称呼的限定修改（见各模块首行注释）。
