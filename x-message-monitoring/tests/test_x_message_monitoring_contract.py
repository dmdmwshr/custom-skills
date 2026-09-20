"""Package checks plus execution of real business driver/ledger fixtures."""
from pathlib import Path
import os
import json
import re
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
BUSINESS=Path(os.environ.get("X_MONITOR_PROJECT_ROOT", str(Path.home()/"Desktop/项目开发/X监控") if os.name == "nt" else str(Path(os.environ.get("CODEX_PROJECTS_ROOT", str(Path.home()/"workspaces")))/"X-monitor")))


class SkillContractTests(unittest.TestCase):
    def test_pure_analysis_fields_against_real_contract_and_isolated_ledger(self):
        node = shutil.which("node")
        python = Path(os.environ.get("X_MONITOR_PYTHON", str(BUSINESS / ".venv/bin/python")))
        if not node or not python.is_file() or not (BUSINESS / "tests/test_reset_quote_policy.py").is_file():
            self.skipTest("business fixture environment unavailable")
        helper = ROOT / "scripts/analysis_fields.cjs"
        unit = subprocess.run([node, "--test", str(ROOT / "tests/test_analysis_fields.cjs")],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(0, unit.returncode, unit.stdout + unit.stderr)
        emitted = subprocess.run([node, "-e", "const h=require(process.argv[1]);console.log(JSON.stringify([h.aiRelevance(true,'AI产品讨论。'),h.aiRelevance(false,'视频制作归属讨论。'),h.aiRelevance(null,'context_unavailable')]))", str(helper)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(0, emitted.returncode, emitted.stderr)
        self.assertEqual(3, len(json.loads(emitted.stdout)))
        program = '''
import json, sys
from x_monitor.contracts import AiRelevanceV1, ContractError
from test_reset_quote_policy import ResetQuotePolicyTests, raw_stream, semantic
values = json.load(sys.stdin)
for value in values:
    assert AiRelevanceV1.from_json(value).as_json() == value
try:
    AiRelevanceV1.from_json({'schema_version':'AiRelevanceV1','related':False,'reasoning':'离线依据。'})
except ContractError as exc:
    assert exc.code == 'unknown_ai_relevance_field'
else:
    raise AssertionError('observed alias unexpectedly accepted')
fixture = ResetQuotePolicyTests()
fixture.setUp()
try:
    fixture.service.store.migrate_desktop_v2(now=fixture.current)
    lease = fixture.begin(manual=True)
    fixture.empty(lease, 'main')
    raw = raw_stream(fixture.current, 'reply')
    item = raw['timeline']['replies']['items'][0]
    item['replyParentVisibleText'] = 'Here is the video I made.'
    item['replyContext']['direct_parent']['original_text'] = item['replyParentVisibleText']
    analysis = semantic('reply', False, product='other')
    analysis['ai_relevance'] = values[1]
    result = fixture.submit(lease, 'reply', raw=raw, analysis=analysis)
    assert result['outcome'] == 'ok'
    assert result['fresh_status_count'] == 1
    assert result['notifiable_status_count'] == 0
    assert fixture.finish_streamed(lease)['heartbeat_complete'] is True
    assert fixture.service.health()['heartbeat_lease']['held'] is False
finally:
    fixture.tearDown()
print('helper -> actual AiRelevanceV1 -> stream assembly -> isolated ledger -> finish passed')
'''
        result = subprocess.run([str(python), "-B", "-c", program], input=emitted.stdout,
                                cwd=BUSINESS, capture_output=True, text=True, timeout=30,
                                env={**os.environ, "PYTHONPATH":os.pathsep.join([str(BUSINESS / "src"), str(BUSINESS / "tests")])})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

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
        legacy=(ROOT/"references/legacy-fact-transfer.md").read_text(encoding="utf-8")
        self.assertIn("legacy-fact-transfer.md",fast)
        for step in ("--input-framing chunks","XMonitorInputReadyV1","echo_disabled=true","write_stdin","Array.from"):
            self.assertIn(step,legacy)
        for step in ("packFacts","packFactsGzip","node:zlib","TextEncoder","diffFacts","XMonitorFactTransferV1","reply_base","reply_delta","context_items","heartbeat-finish"):
            self.assertIn(step,legacy)
        for step in ("desktop_stdin_client.js","node:child_process","selfTest","exact_match=true","outcome_unknown=true","input_frame_bytes","observation-fingerprint"):
            self.assertIn(step,fast)
        driver=BUSINESS/"scripts/desktop_monitor_driver.js"
        if driver.is_file():
            version=re.search(r"const version = '([0-9.]+)'",driver.read_text(encoding="utf-8")).group(1)
            if os.name == "nt":
                self.assertIn("驱动 "+version,fast)
            else:
                # Windows pins are not the Linux runtime contract.
                self.assertIn("references/wsl-migration.md", skill)
                linux = (ROOT/"references/wsl-migration.md").read_text(encoding="utf-8")
                self.assertIn("实际", linux)
                self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertIn("15 秒",fast)
        self.assertIn("40 秒",fast)
        contract=(ROOT/"references/reply-reset-contract.md").read_text(encoding="utf-8")
        for field in ("ResetAnalysisV3","XReplyContextV2","XQuoteContextV1","FrozenXMessageV5","subject_product","quote_context_chinese_translations","codex_reset_all_streams_quote_context_v1"):
            self.assertIn(field,contract)
        self.assertIn("$x-message-monitoring",(ROOT/"agents/openai.yaml").read_text(encoding="utf-8"))

    def test_production_driver_behavior_not_just_document_phrases(self):
        node=shutil.which("node")
        targets=[BUSINESS/"tests/test_desktop_driver.cjs",BUSINESS/"tests/test_desktop_stdin_client.cjs"]
        if not node or any(not target.is_file() for target in targets):
            self.skipTest("business driver or Node is unavailable on this host")
        result=subprocess.run([node,"--test",*[str(target) for target in targets]],cwd=BUSINESS,capture_output=True,text=True,encoding="utf-8",timeout=30)
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)

    def test_production_stream_transactions_and_receipts(self):
        python=Path(os.environ.get("X_MONITOR_PYTHON",str(BUSINESS / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))))
        if not python.is_file():
            self.skipTest("business test environment unavailable")
        for pattern in ("test_independent_streams.py","test_receipt_sync.py","test_reply_reset_policy.py","test_reset_quote_policy.py","test_fact_transfer.py"):
            result=subprocess.run([str(python),"-m","unittest","discover","-s","tests","-p",pattern,"-q"],
                cwd=BUSINESS,capture_output=True,text=True,encoding="utf-8",timeout=30,env={**os.environ,"PYTHONIOENCODING":"utf-8"})
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)


if __name__=="__main__":
    unittest.main()
