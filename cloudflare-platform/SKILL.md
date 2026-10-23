---
name: cloudflare-platform
description: Cloudflare 平台任务的条件路由入口：选型与产品参考、部署上线、Workers 编写/审查与 wrangler 命令、Durable Objects、Sandbox（stable/next/迁移三选一）、Zero Trust（Cloudflare One）与迁移评估、Email Service、Turnstile、网页性能审计。用户的任务涉及 Cloudflare 的任何产品或部署时使用；根据任务只读取对应后端文件，不一次展开全部。
metadata:
  x-custom-skill: true
  x-source-repo: dmdmwshr/custom-skills
---

# Cloudflare 平台（条件路由）

后端是原维护来源安装的 Skill 文件，**原位保留、不改**（发现已降为本入口之下）。按任务读取下表对应文件；路径是 CC Switch 安装实体 `C:\Users\12070\.cc-switch\skills\<名>\`（稳定，非客户端链接），用文件读取，不要重新开启后端发现。

| 任务 | 读取 | 备注 |
| --- | --- | --- |
| 不确定该用哪个产品 / 产品总览与决策树 | `cloudflare/SKILL.md`，再按产品读 `cloudflare/references/<产品>/` | 来源 cloudflare/skills，覆盖 Workers/Pages/KV/D1/R2/AI/Tunnel/WAF/Terraform 等约 60 个产品目录 |
| 部署/发布/托管项目到 Cloudflare | `cloudflare-deploy/SKILL.md`，配合 `wrangler/SKILL.md` | 来源 OpenAI curated；其 `references/` 与 `cloudflare` 的参考树同构（版本略有差异），产品细节以 `cloudflare/references` 为准，deploy 流程与鉴权章节用 `cloudflare-deploy` |
| 用 wrangler 命令（deploy/dev/secrets/KV/R2/D1/Queues/Workflows…） | `wrangler/SKILL.md`（先读再运行命令） | 单文件较长，按章节检索 |
| 编写或审查 Workers 代码、wrangler.jsonc | `workers-best-practices/SKILL.md` | 检查流式、悬空 Promise、全局状态、密钥、绑定、可观测性 |
| 有状态协调（聊天室、多人、预订）、DO 的 RPC/SQLite/alarms/WebSocket | `durable-objects/SKILL.md` | |
| Sandbox SDK 应用 | 按包版本三选一：稳定包 `sandbox-stable/`；`@cloudflare/sandbox@next`（1.0 预览）`sandbox-next/`；稳定→next 迁移 `sandbox-migrate-to-next/` | 不得混用两个版本的 API；先看项目 `package.json` 里的实际 tag |
| Zero Trust / SASE（Access、Gateway、WARP、Tunnel、WAN、DLP、CASB） | `cloudflare-one/SKILL.md` | 检索优先：以当前官方文档/API schema 为准 |
| 从 Zscaler/Palo Alto/传统 VPN/SWG 迁到 Cloudflare One 的评估与对照 | `cloudflare-one-migrations/SKILL.md` | |
| 邮件发送/路由（Workers binding、REST、Email Routing） | `cloudflare-email-service/SKILL.md` | |
| Turnstile 人机验证端到端接入 | `turnstile-spin/SKILL.md`（含 `references/`、`scripts/`、`tests/`） | 会通过 Cloudflare API 建 widget，属外部写入 |
| 页面性能审计（Core Web Vitals） | `web-perf/SKILL.md` | 需要 Chrome DevTools MCP；本机当前未配置，缺失时只说明限制，不自行安装 |

`agents-sdk` 不在本入口范围，保持独立。

## 规则

1. 部署、改 DNS/规则/路由、创建或轮换 API Token 与密钥、Turnstile 建 widget、迁移切换等**写入**，只在用户当次明确要求且目标账号/Zone/项目明确时执行；先 `npx wrangler whoami` 等只读命令核对当前账户与权限，账户不符就停。
2. 检索优先于记忆：上游已要求以当前官方文档/schema 为准，限额与价格易变，不凭印象回答。
3. 凭据：沿现有可用配置（`wrangler login` 的 OAuth 或 `CLOUDFLARE_API_TOKEN` 环境变量）；不在输出、日志、命令参数、仓库文件中暴露令牌；`.dev.vars`、`wrangler.jsonc` 里的密钥不提交。
4. 沙箱阻断网络调用时，按当前平台权限规则处理，不换通道规避；部署耗时较长，设合适超时，结果未知先查 Cloudflare 侧状态再重试。
5. 一次任务通常只读 1–2 个后端文件；产品参考树体量大（约 2 MB），只读相关产品目录。

## 来源与维护

后端分别来自 `cloudflare/skills`（12 项，物理副本在 `C:\Users\12070\.agents\skills`，与 CC 副本逐文件一致）和 OpenAI curated `cloudflare-deploy`（Apache-2.0）。更新走各自上游/CC Switch；本入口只维护路由表。后端 Skill 的 `description` 触发词已由发现配置降级，若发现仍同时展开多个 Cloudflare 技能，说明降级未生效，应报告而不是改后端文件。
