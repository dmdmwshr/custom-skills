"""Package checks plus execution of real business driver/ledger fixtures."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
BUSINESS=Path(os.environ.get("X_MONITOR_PROJECT_ROOT",r"C:\Users\12070\Desktop\项目开发\X监控"))


class SkillContractTests(unittest.TestCase):
    def test_discovery_references_and_no_unsupported_global_assignment(self):
        skill=(ROOT/"SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: x-message-monitoring",skill)
        self.assertIn("x-custom-skill: true",skill)
        for ref in re.findall(r"\]\((references/[^)]+)\)",skill):
            self.assertTrue((ROOT/ref).is_file())
        documents="\n".join(p.read_text(encoding="utf-8") for p in ROOT.rglob("*.md"))
        self.assertNotRegex(documents,r"globalThis\.\w+\s*=")
        fast=(ROOT/"references/fast-path-runbook.md").read_text(encoding="utf-8")
        for entry in ("collect-stream","context-plan","reply-context-plan","quoteBatch","cycle-failure","analysis-plan","scan-analysis","stream-failure","heartbeat-finish","sync-receipts"):
            self.assertIn(entry,fast)
        self.assertIn("manual_validation",fast)
        self.assertIn("partial_failed",fast)
        self.assertIn("-filter:replies -filter:retweets",fast)
        self.assertIn("media_only_not_inspected",fast)
        for step in ("--input-framing chunks","XMonitorInputReadyV1","echo_disabled=true","write_stdin","Array.from"):
            self.assertIn(step,fast)
        for step in ("packFacts","packFactsGzip","node:zlib","TextEncoder","diffFacts","XMonitorFactTransferV1","reply_base","reply_delta","context_items","heartbeat-finish"):
            self.assertIn(step,fast)
        driver=BUSINESS/"scripts/desktop_monitor_driver.js"
        if driver.is_file():
            version=re.search(r"const version = '([0-9.]+)'",driver.read_text(encoding="utf-8")).group(1)
            self.assertIn("驱动 "+version,fast)
        self.assertIn("15 秒",fast)
        self.assertIn("40 秒",fast)
        contract=(ROOT/"references/reply-reset-contract.md").read_text(encoding="utf-8")
        for field in ("ResetAnalysisV3","XReplyContextV2","XQuoteContextV1","FrozenXMessageV5","subject_product","quote_context_chinese_translations","codex_reset_all_streams_quote_context_v1"):
            self.assertIn(field,contract)
        self.assertIn("$x-message-monitoring",(ROOT/"agents/openai.yaml").read_text(encoding="utf-8"))

    def test_production_driver_behavior_not_just_document_phrases(self):
        node=shutil.which("node")
        target=BUSINESS/"tests/test_desktop_driver.cjs"
        if not node or not target.is_file():
            self.skipTest("business driver or Node is unavailable on this host")
        result=subprocess.run([node,"--test",str(target)],cwd=BUSINESS,capture_output=True,text=True,encoding="utf-8",timeout=30)
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)

    def test_production_stream_transactions_and_receipts(self):
        python=BUSINESS/".venv/Scripts/python.exe"
        if not python.is_file():
            self.skipTest("business test environment unavailable")
        for pattern in ("test_independent_streams.py","test_receipt_sync.py","test_reply_reset_policy.py","test_reset_quote_policy.py","test_fact_transfer.py"):
            result=subprocess.run([str(python),"-m","unittest","discover","-s","tests","-p",pattern,"-q"],
                cwd=BUSINESS,capture_output=True,text=True,encoding="utf-8",timeout=30,env={**os.environ,"PYTHONIOENCODING":"utf-8"})
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)


if __name__=="__main__":
    unittest.main()
