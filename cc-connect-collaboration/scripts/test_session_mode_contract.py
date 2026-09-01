"""离线校验 cc-connect 两种会话模式的不可回退契约。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def document(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class SessionModeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = document("SKILL.md")
        cls.fixed = document("references/fixed-session-contract.md")
        cls.scheduling = document("references/message-and-scheduling.md")
        cls.rollover = document("references/session-rollover-protocol.md")
        cls.desktop_only = document("references/desktop-owner-outbound-only.md")

    def assert_contains(self, text: str, *phrases: str) -> None:
        for phrase in phrases:
            self.assertIn(phrase, text, f"missing contract phrase: {phrase}")

    def test_two_explicit_session_modes(self) -> None:
        for text in (self.skill, self.fixed, self.scheduling, self.desktop_only):
            self.assert_contains(
                text,
                "cc_connect_fixed_session",
                "desktop_owner_outbound_only",
            )

    def test_existing_route_without_mode_fails_closed(self) -> None:
        for text in (self.skill, self.fixed, self.scheduling, self.desktop_only):
            self.assert_contains(text, "既有路由", "mode_unknown")
        self.assert_contains(self.skill, "只有新建路由可默认使用")

    def test_desktop_heartbeat_and_cc_connect_cron_are_mutually_exclusive(self) -> None:
        for text in (self.skill, self.scheduling, self.desktop_only):
            self.assert_contains(text, "Desktop heartbeat", "cc-connect cron", "互斥")
        self.assert_contains(
            self.scheduling,
            "`cc_connect_fixed_session` 使用 cc-connect Timer",
            "`desktop_owner_outbound_only` 使用目标 Desktop heartbeat",
        )

    def test_project_ownership_does_not_replace_work_dir(self) -> None:
        for text in (self.skill, self.fixed, self.desktop_only):
            self.assert_contains(text, "项目归属", "work_dir")
        self.assert_contains(self.desktop_only, "任务内部身份不变", "完整实际 `work_dir`")

    def test_route_registry_records_single_owners(self) -> None:
        self.assert_contains(
            self.fixed,
            "`session_owner`",
            "`automation_owner`",
            "`cc_connect_cron`",
            "`desktop_heartbeat`",
        )
        self.assert_contains(
            self.desktop_only,
            "`session_owner=desktop`",
            "`automation_owner=desktop_heartbeat`",
        )
        self.assert_contains(
            self.fixed,
            "| `cc_connect_fixed_session` | `fixed_session` | `cc_connect` |",
            "| `desktop_owner_outbound_only` | `silent_drop` | `desktop` |",
            "真实 Desktop 任务身份与 heartbeat 目标必须在受保护回读中按内部标识精确比较",
        )

    def test_work_dir_policy_is_split_by_mode(self) -> None:
        self.assert_contains(
            self.fixed,
            "双向模式为中枢控制目录",
            "桌面专属模式为已登记且受审的 Desktop 实际目录",
        )
        self.assert_contains(
            self.desktop_only,
            "不要求一律迁入中枢控制目录",
            "必须与路由登记精确一致",
        )

    def test_desktop_mode_suppresses_inbound_and_reserves_desktop_capabilities(self) -> None:
        for text in (self.skill, self.fixed, self.scheduling, self.desktop_only):
            self.assert_contains(text, "silent_drop")
        self.assert_contains(
            self.desktop_only,
            "已登录 Chrome",
            "浏览器扩展",
            "目标 Desktop fixed task",
        )

    def test_no_reply_uses_native_dont_notify(self) -> None:
        for text in (self.skill, self.scheduling, self.desktop_only):
            self.assert_contains(text, "NO_REPLY", "DONT_NOTIFY")

    def test_unknown_delivery_is_never_resent(self) -> None:
        for text in (self.skill, self.scheduling, self.desktop_only):
            self.assert_contains(text, "未知", "自动重发")

    def test_rollover_is_limited_to_bidirectional_mode(self) -> None:
        for text in (self.skill, self.rollover, self.desktop_only):
            self.assert_contains(text, "desktop_owner_outbound_only")
        self.assert_contains(self.rollover, "不得调用 Bridge", "激活新会话")

    def test_generic_contract_does_not_embed_one_business_monitor(self) -> None:
        combined = "\n".join(
            (
                self.skill,
                self.fixed,
                self.scheduling,
                self.rollover,
                self.desktop_only,
            )
        )
        for forbidden in (
            "X消息监控",
            "Tibo",
            "thsottiaux",
            "dmdmws",
            "每流 200",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
