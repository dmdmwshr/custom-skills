"""离线守护 x-message-monitoring 的固定会话与投递契约。"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def document(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class XMessageMonitoringContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = document("SKILL.md")
        cls.agent = document("agents/openai.yaml")
        cls.contract = document("references/heartbeat-and-delivery.md")
        cls.fast_path = document("references/fast-path-runbook.md")
        cls.combined = "\n".join((cls.skill, cls.agent, cls.contract, cls.fast_path))

    def assert_contains(self, text: str, *phrases: str) -> None:
        for phrase in phrases:
            self.assertIn(phrase, text, f"missing contract phrase: {phrase}")

    def test_identity_and_implicit_prompt_are_discoverable(self) -> None:
        self.assert_contains(
            self.skill,
            "name: x-message-monitoring",
            "x-custom-skill: true",
            "x-source-repo: dmdmwshr/custom-skills",
        )
        self.assertRegex(
            self.agent,
            r'(?m)^  default_prompt: "\$x-message-monitoring\b',
        )
        self.assert_contains(self.agent, "allow_implicit_invocation: true")

    def test_one_desktop_session_and_no_scheduler_or_browser_escape(self) -> None:
        self.assert_contains(
            self.combined,
            "唯一 Desktop 固定会话",
            "不得新建、唤醒、转移、恢复或并行使用第二个 Codex 任务",
            "cc-connect cron",
            "Windows 计划任务",
            "不触碰原有标签",
            "禁止用 Playwright CLI、Python/Node Playwright",
            "`tab.playwright` DOM 操作门面是允许且优先的页面接口",
            "第三种浏览器",
            "密码、Cookie、令牌",
        )

    def test_heartbeat_is_machine_receipt_driven_and_ordered(self) -> None:
        sequence = (
            "health → heartbeat-acquire → heartbeat-renew → publish-pending（预检）→ "
            "Chrome/Edge 采集 → collect → 受控分析 → scan → publish（由 heartbeat-finish 的机器两阶段处理）→ heartbeat-finish"
        )
        self.assertIn(sequence, self.contract)
        self.assert_contains(
            self.combined,
            "SQLite 是正式账本",
            "UTC instant",
            "20 分钟",
            "`heartbeat-finish` 是唯一完成入口",
            "notification_decision=DONT_NOTIFY",
        )

    def test_chrome_then_single_machine_authorized_edge_fallback(self) -> None:
        self.assert_contains(
            self.contract,
            "dmdmwshr` Chrome",
            "browser_not_running",
            "仅一次无 URL",
            "等待 8 秒",
            "仅重取一次 Chrome 句柄",
            "extension_unavailable",
            "login_unavailable",
            "chrome-fallback-authorize",
            "机器明确授权后才可进入 Edge，一次路径",
            "browser-failure",
            "不因结构歧义、水位未到、风控、V2 校验或已采集候选改用 Edge",
        )

    def test_v3_separates_ai_relevance_from_quota_reset_analysis(self) -> None:
        self.assert_contains(
            self.combined,
            "XCollectedTimelineV2",
            "XMonitorScanInputV3",
            "AiRelevanceV1",
            "ai_related=true",
            "ai_related=false",
            "ai_related=null",
            "`reset_analysis` 只回答更窄的 Codex 额度重置问题",
            "reset_analysis.related=true` 必须同时为 `ai_related=true`",
            "AI 相关不等于与额度重置直接相关",
            "回复与父帖的合并语境",
            "模型、推理、训练、代理、生成式内容、AI 编程/开发者工具、部署、评测、安全、政策与生态",
        )
        self.assertNotIn("XMonitorScanInputV2", self.combined)
        self.assertNotIn("三态额度相关性", self.combined)

    def test_ai_reply_delivery_requires_a_strict_direct_parent(self) -> None:
        self.assert_contains(
            self.combined,
            "外部回复的普通投递资格严格为：`ai_related=true`",
            "不得把 `reset_analysis.related` 当作投递门槛",
            "latest_search_unique_adjacent_parent",
            "latest_search_permalink_unique_parent",
            "父帖可见原文",
            "父帖中文翻译",
            "规范永久链接",
            "与 Codex 额度重置：无直接关联",
            "准备普通投递的 `ai_related=true` 回复",
            "媒体占位、引用帖、祖先帖",
            "ai_reply_parent_structure_required",
            "父帖稳定身份可信但正文缺失或仅媒体",
            "必须保持 `ai_related=null` 并进入已处理/已抑制路径",
            "visible_reply_marker",
            "既有历史事件保持冻结，不能由新规则自动重分类",
        )

    def test_non_ai_and_unassessed_replies_are_suppressed_without_reclassification(self) -> None:
        self.assert_contains(
            self.contract,
            "`ai_related=false` 的非 AI 回复",
            "`ai_related=null`/未评估回复",
            "已处理/已抑制",
            "不创建普通投递、不外送",
            "既有历史事件保持冻结，不能由新规则自动重分类",
        )

    def test_alerts_and_periodic_health_summary_are_ledger_owned(self) -> None:
        self.assert_contains(
            self.contract,
            "首次创建提醒意图",
            "满 24 小时",
            "恢复后至多一个恢复提醒",
            "每 4 个已完成且零可通知周期",
            "x_heartbeat_no_new_summary",
            "摘要未知不重发",
            "不得自行推算、补发或另行显示摘要正文",
            "notifiable_count=0",
            "非 AI 或无法判断回复不会重置计数",
            "Codex 自动化在完整成功时始终返回 `DONT_NOTIFY`",
        )

    def test_no_current_x_account_post_or_permalink_is_embedded(self) -> None:
        for forbidden in ("thsottiaux", "https://x.com/", "x_status_v1:"):
            self.assertNotIn(forbidden, self.combined)
        self.assertNotRegex(self.combined, r"\b\d{15,}\b")
        self.assert_contains(self.combined, "不得写入目标账号、当前状态 ID、历史正文或固定顺序")

    def test_fast_path_eliminates_runtime_api_and_payload_exploration(self) -> None:
        self.assert_contains(
            self.fast_path,
            "普通轮次不枚举 CLI",
            "不试探 `agent`、`@oai/browser`",
            "不使用旧 `agent.browsers`",
            "不导入 `@oai/browser`",
            "不从标签列表重新绑定刚创建的标签",
            "全轮只维护两个稳定概念对象",
            "不得手输、覆盖、从旧轮复制或申请第二个 lease",
        )

    def test_ordinary_heartbeat_only_loads_fast_path_until_contract_adjudication(self) -> None:
        self.assert_contains(
            self.skill,
            "普通 heartbeat 启动时只完整读取 [固定快速路径]",
            "不预读完整投递契约",
            "不要例行加载 [heartbeat 与投递契约]",
            "只有快速路径列出的初始化、上下文/指纹/schema 变化或契约裁决条件命中时才读取",
        )
        self.assertNotIn(
            "先读取 [heartbeat 与投递契约](references/heartbeat-and-delivery.md) 和 [固定快速路径]",
            self.skill,
        )
        self.assert_contains(
            self.fast_path,
            "普通 heartbeat 启动时唯一必须完整读取的支持文件就是本快速路径",
            "固定会话首次运行、上下文丢失、项目或 Skill 文件指纹变化、机器 schema 变化时",
            "普通轮次不得为“保险起见”预读完整契约",
            "机器回执给出的浏览器备用原因、回复证据、直接父帖、投递状态或 finish 结果无法由本快速路径唯一裁决",
            "契约裁决只用于选择更严格的既有动作，不授权重试",
            "常规 AI 分类或额度分析本身都不是契约裁决触发条件",
        )

    def test_fast_path_has_one_way_state_machine_and_strict_retry_limits(self) -> None:
        self.assert_contains(
            self.fast_path,
            "INIT → HEALTHY → LEASED → ROUTE_READY → BROWSER_SELECTED",
            "FAILED_PENDING_FINISH → FINISHED_FAILED",
            "每个动态候选恰好一次 Computer Use 调用",
            "不拆成“导航一次、睡眠一次、读取一次”",
            "`collect` 每账号最多一次",
            "`scan` 一次",
            "不重发、不二次 finish",
            "主页和搜索分页各最多 12",
            "约为 `N + 6` 次浏览器调用",
        )

    def test_fast_path_keeps_controlled_tab_dom_but_forbids_independent_playwright(self) -> None:
        self.assert_contains(
            self.fast_path,
            "`tab.playwright` 只表示当前受控标签的 DOM 操作门面",
            "独立 Playwright 浏览器、CLI、Python/Node 包、调试端口和额外进程",
            "唯一最小主 `status_permalink` 会话容器",
            "推荐卡、其他回复分支或其他分区",
        )

    def test_latest_search_filters_transient_placeholders_without_weakening_candidates(self) -> None:
        self.assert_contains(
            self.fast_path,
            "stable status link → placeholder filter → bounded same-viewport re-read → strict candidate validation → pagination",
            "瞬态占位节点，忽略且不计进度",
            "同一次 Computer Use 调用、同一视口",
            "不能把它降级为排除项或非 AI 回复",
            "`isMediaOnly=true` 是有效候选，不是页面异常",
            "[Media-only post; no visible text.]",
            "专用 social-context 标记判断",
            "禁止先固定 `waitForTimeout` 再读取",
        )

    def test_readiness_deadlines_are_caught_and_mapped_to_stable_surface_reasons(self) -> None:
        self.assert_contains(
            self.fast_path,
            "`MAIN_PROBE`、`REPLY_SEARCH_PROBE` 与 `PERMALINK_PROBE` 的 locator readiness 都必须使用同一规则",
            "条件等待最多 5 秒",
            "在同一调用内 `catch` deadline",
            "不得让原始 Playwright timeout 逃逸到调用结果",
            "deadline 后只允许做一次无等待、无循环的页面包络核验",
            "当前规范 URL、预期标题、唯一主列、登录门、验证码/风控门、显式错误面、可信空态",
            "不得返回原始异常、错误栈、selector、HTML、正文或页面异常原文",
            "也不得 reload、滚动、换浏览器、换 selector 或再次等待",
            "main_surface_not_ready",
            "reply_search_surface_not_ready",
            "permalink_surface_not_ready",
            "main_watermark_unreached",
            "reply_watermark_unreached",
            "不能伪装成普通结构异常",
            "不得降级为 generic `structure_ambiguous` 后继续探索",
            "包络核验 1；外部重试 0",
        )

    def test_permalink_probe_waits_for_the_minimum_semantic_container(self) -> None:
        self.assert_contains(
            self.fast_path,
            '`[data-testid="primaryColumn"] section[role="region"][aria-labelledby]`',
            "只沿目标卡祖先链取得这个最小容器",
            "绝不把 `primaryColumn`、更外层祖先或其他 section 当候选",
            "可复制的 locator readiness 形状",
            "只以动态 `targetStatusId` 构造",
            "`targetTimeLink = 'article[data-testid=\"tweet\"] a[href$=\"/status/' + targetStatusId + '\"] time'`",
            "以状态路径精确结尾避免 ID 前缀误命中",
            "不拼接作者、不要求当前视口可见",
            "section[role=\"region\"][aria-labelledby]:has(",
            "`conversationLocator = sectionCandidates.last()`",
            "`deadlineAt = Date.now() + 5000`",
            "`remaining() = Math.max(1, deadlineAt - Date.now())`",
            "以下全部等待只用 `timeout: remaining()`",
            "不得各自重置为 5 秒",
            "`topLevelTweetLocator = conversationLocator.locator('article[data-testid=\"tweet\"]:not(article[data-testid=\"tweet\"] article[data-testid=\"tweet\"])')`",
            "读取一次 `topLevelCount`",
            "用一个 `Promise.all` 并发等待",
            "各自至少出现一个 `a[href*=\"/status/\"] time` attached",
            "不再是无身份骨架",
            "最终同步提取仍须排除 quoteTweet 并证明每张卡恰有一个自身规范 time-link",
            "任一成员在总截止前未就绪统一进入 `permalink_members_not_ready`",
            "随后仅一次同步 `evaluate` 验证唯一 closest 容器和完整非嵌套顶层链",
            "禁止在 `evaluate` 内用 `while`、`setTimeout`、`requestAnimationFrame` 或任何轮询等待页面",
            "全部 locator 等待必须置于同一 `try`",
            "deadline 只进入一次 `catch`",
            "调用一次无等待、无循环的页面包络核验并返回它的单个稳定 envelope",
            "目标 time-link deadline 返回 `permalink_target_not_ready`",
            "section 或第二张 tweet deadline 返回 `permalink_members_not_ready`",
            "readiness 绝不构成作者、永久链接、UTC 或正文事实",
            "成功后只做上述一次同步提取",
            "只有该提取所得链长度仍不满足 2～200 时才使用 `permalink_conversation_chain_size_untrusted`",
            "禁止用 `targetCell.parentElement.children` 猜父",
            "禁止从 `primaryColumn.querySelectorAll('article')` 的整页平铺结果挑父",
            '`article[data-testid="tweet"]`',
            "`parentIndex = targetIndex - 1`",
            "宽链携带完整 ID 链和显式索引",
            "稳定脱敏子原因",
            "permalink_conversation_container_missing",
            "permalink_target_not_ready",
            "permalink_members_not_ready",
            "permalink_conversation_chain_size_untrusted",
            "permalink_adjacent_parent_untrusted",
            "permalink_parent_author_mismatch",
        )
        self.assertEqual(self.fast_path.count('a[href*=\"/status/\"] time'), 1)
        self.assertNotIn("a[href*=\"/status/' + targetStatusId", self.fast_path)
        self.assertNotIn('a[href^=\"/status/', self.fast_path)
        self.assertEqual(self.fast_path.count("`while`"), 1)
        self.assertEqual(self.fast_path.count("`setTimeout`"), 1)
        self.assertEqual(self.fast_path.count("`requestAnimationFrame`"), 1)
        self.assertEqual(self.fast_path.count("`evaluate`"), 3)
        self.assertEqual(self.fast_path.count("primaryColumn.querySelectorAll('article')"), 1)
        strict_extract = self.fast_path.split("- 同步提取仍只读", 1)[1].split("- 探针失败只返回", 1)[0]
        self.assert_contains(
            strict_extract,
            "链身份与父目标详情必须分两层解析",
            "`parseStableIdentity`",
            "链中非父目标成员不要求正文或媒体事实",
            "禁止因祖先/后续成员正文为空而返回 null",
            "permalink_chain_member_identity_missing",
            "permalink_chain_duplicate",
            "不得静默去重",
            "ID 全唯一",
            "父/目标均须为顶层、非引用、非推广",
            "目标必须与 Latest 冻结观察的状态 ID、作者、规范 UTC、规范永久链接、可见正文或媒体标记、回复/转推/引用标志逐项一致",
            "父作者等于回复对象",
            "父帖有可见正文时一并保留其原文和规范永久链接",
            "`parseTargetDetail`",
            "`parseParentFacts`",
            "状态 ID 顺序不得使用 `BigInt`",
            "`compareDecimalStatusIds(left,right)`",
            "先比较长度、等长再按 ASCII 十进制字符串比较",
            "只有严格 `parent < target` 才通过",
            "`parentReadable`",
            "页面外禁止引用页面内的 `marker` 或其他局部变量",
            "`parentReadable=true` 才把父帖正文与永久链接成对写入候选",
            "false 时完全省略这两个可选键",
            "permalink_target_or_parent_detail_invalid",
            "permalink_final_observed_mismatch",
        )
        self.assertNotIn("`permalink_conversation_chain_identity_untrusted`", self.fast_path)
        self.assertNotIn("BigInt(", strict_extract)


if __name__ == "__main__":
    unittest.main(verbosity=2)
