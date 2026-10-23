---
name: security-review
description: 仅在用户明确要求安全审查时使用的三个流程：语言/框架安全最佳实践审查（best-practices，python、js/ts、go）、基于仓库的威胁建模（threat-model）、基于 git 历史的安全所有权与 bus factor 分析（ownership-map）。普通代码审查、调试、架构总结、功能开发不触发；不自动扫描。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# 安全审查（三合一，显式触发）

| 用户明确要求 | 模块 | 要点 |
| --- | --- | --- |
| 安全最佳实践审查/报告/安全默认写法（python、JavaScript/TypeScript、go） | [best-practices](modules/best-practices/WORKFLOW.md) | 语言框架参考在 `modules/best-practices/references/`，只读与目标栈相关的文件；不支持的语言如实说明 |
| 对某代码库或路径做威胁建模、枚举信任边界与滥用路径 | [threat-model](modules/threat-model/WORKFLOW.md) | 仓库依据驱动，产出简洁 Markdown 威胁模型 |
| 基于 git 历史的安全所有权拓扑、bus factor、敏感代码孤儿分析 | [ownership-map](modules/ownership-map/WORKFLOW.md) | 脚本在 `modules/ownership-map/scripts/`（`run_ownership_map.py`、`query_ownership.py`、`community_maintainers.py`）；只读 git 历史，导出 CSV/JSON 供查询 |

## 规则

1. **不因普通任务自动做安全扫描**；只有用户明确点名安全审查/威胁建模/所有权分析才进入对应模块。
2. 产物（报告、威胁模型、CSV/JSON、图数据）写入用户指定位置或任务工作目录，默认不提交进被审查仓库，不外发、不导入外部图数据库或服务，除非用户当次要求。
3. 发现真实密钥或敏感数据时只报告位置与类型，不在输出中复述值。
4. 发现与修复分开：用户只要求审查就只给发现和建议；要求修复时按普通开发流程实施并测试。
5. `ownership-map` 脚本需要 networkx：用隔离环境运行，如 `uv run --no-project --with networkx python modules/ownership-map/scripts/run_ownership_map.py --repo <仓库> --out <输出目录>`；不在被审查仓库里新装依赖。

## 来源与许可

模块来自 OpenAI curated 的 `security-best-practices`、`security-threat-model`、`security-ownership-map`（Apache-2.0，见 `licenses/`）。正文、references、scripts 原样保留；仅各 `WORKFLOW.md` 文件头说明相对路径基准，`ownership-map` 中的 `skills/skills/security-ownership-map/` 路径改为 `modules/ownership-map/`。
