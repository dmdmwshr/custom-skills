"""浏览器路由技能的四层快速诊断契约回归测试。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BrowserTamerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_four_layers_are_ordered_and_explicit(self) -> None:
        section_start = self.skill.index("## 四层快速诊断（短路）")
        section_end = self.skill.find("\n## ", section_start + 1)
        section = self.skill[section_start:] if section_end < 0 else self.skill[section_start:section_end]
        markers = (
            "OS URL / Browser Tamer 路由",
            "浏览器配置选择器（profile chooser）",
            "浏览器进程",
            "Codex 扩展连接",
        )
        positions = [section.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("四层快速诊断（短路）", self.skill)

    def test_short_circuit_and_layer_independence_are_contracts(self) -> None:
        self.assertIn("第一处失败或无法确认时停止", self.skill)
        self.assertIn("后续三层标记 `not_run`", self.skill)
        self.assertIn("前层 `ok` 只表示该层的证据成立，不证明任何后层可用", self.skill)
        self.assertIn("not_run` 也不得被解释为通过", self.skill)

    def test_stable_conclusion_vocabulary_is_present(self) -> None:
        for conclusion in (
            "route_not_ready",
            "profile_selector_not_ready",
            "browser_process_not_running",
            "browser_process_unverified",
            "codex_extension_connected",
            "codex_extension_unavailable",
            "codex_extension_unverified",
        ):
            self.assertIn(conclusion, self.skill)
        for stop_after in ("route", "profile_selector", "browser_process", "codex_extension", "none"):
            self.assertIn(f"`{stop_after}`", self.skill)

    def test_skill_stays_generic(self) -> None:
        self.assertLess(len(self.skill.splitlines()), 500)
        self.assertIn("不得用修改域名规则掩盖", self.skill)
        self.assertIn("不得用浏览器窗口可见推断扩展已连接", self.skill)
        for business_term in ("X消息监控", "Tibo", "thsottiaux", "每流 200"):
            self.assertNotIn(business_term, self.skill)


if __name__ == "__main__":
    unittest.main(verbosity=2)
