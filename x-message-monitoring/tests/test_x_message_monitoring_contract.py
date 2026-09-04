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
            "媒体占位、引用帖、祖先帖",
            "ai_reply_parent_structure_required",
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

    def test_permalink_probe_selects_smallest_viable_target_ancestor_container(self) -> None:
        self.assert_contains(
            self.fast_path,
            "目标祖先结构候选算法",
            "逐级枚举直到 `primaryColumn` **之前**的所有祖先元素",
            "不得直接把 `primaryColumn` 当候选",
            "不能因为最近 `section` 过窄、链少于两张就立即失败",
            "固定伪代码",
            "`targetPath = ancestors(targetCard, stopBefore=primaryColumn)`",
            "最近预批准语义 region/partition 为 `null` 或属于 `targetPartitions`",
            "嵌套的推荐、额外 timeline/region 或其他分区，必须排除而不是并入外层链",
            "收集所有可用链后，只选择 DOM 上离目标最近的（最小）一个",
            "无可用候选、最小选择并列或任一保留下来的卡归属/身份不可信均失败关闭",
            "禁止用 `targetCell.parentElement.children` 猜父",
            "禁止从 `primaryColumn.querySelectorAll('article')` 的整页平铺结果挑父",
            '`article[data-testid="tweet"]`',
            "`parentIndex = targetIndex - 1`",
            "宽链携带完整链和显式索引",
            "稳定脱敏子原因",
            "permalink_target_ancestor_candidates_missing",
            "permalink_conversation_chain_size_untrusted",
            "permalink_adjacent_parent_untrusted",
            "permalink_parent_author_mismatch",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
