import tempfile
import unittest
from pathlib import Path

import breeze_tts as bt


class BuildWorkflowTests(unittest.TestCase):
    def test_design_graph_is_offline_and_saves_audio(self):
        graph = bt.build_workflow("design", text="你好。[笑]", instruction="温和的年轻女声")
        load = graph["1"]["inputs"]
        self.assertFalse(load["download_if_missing"])
        self.assertEqual(load["attention"], "sdpa")
        self.assertEqual(graph["2"]["class_type"], "BreezeTTS2VoiceDesign")
        self.assertEqual(graph["2"]["inputs"]["cfg_scale"], 4.0)
        self.assertEqual(graph["3"]["class_type"], "SaveAudio")
        self.assertEqual(graph["3"]["inputs"]["audio"], ["2", 0])

    def test_generation_controls_are_complete(self):
        required = {"max_new_tokens", "temperature", "top_k", "top_p", "repetition_penalty",
                    "depth_temperature", "depth_top_k", "depth_top_p", "seed"}
        graph = bt.build_workflow("design", text="x", instruction="y")
        self.assertTrue(required <= set(graph["2"]["inputs"]))
        graph = bt.build_workflow("multi", text="甲：你好", cast=[{"name": "甲", "instruction": "低沉男声"}])
        multi = next(n for n in graph.values() if n["class_type"] == "BreezeTTS2MultiSpeaker")
        self.assertTrue(required <= set(multi["inputs"]))

    def test_clone_requires_reference_and_transcript(self):
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("clone", text="x", reference_name="a.wav")
        graph = bt.build_workflow("clone", text="x", reference_name="breeze_refs/a.wav", reference_text="转写")
        self.assertEqual(graph["3"]["class_type"], "BreezeTTS2VoiceClone")
        self.assertEqual(graph["3"]["inputs"]["cfg_scale"], 1.0)
        self.assertEqual(graph["2"]["inputs"]["audio"], "breeze_refs/a.wav")

    def test_direction_needs_instruction(self):
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("direction", text="x", reference_name="a.wav", reference_text="t")
        graph = bt.build_workflow("direction", text="x", instruction="慢一点", reference_name="a.wav",
                                  reference_text="t")
        self.assertEqual(graph["3"]["inputs"]["stitch_reference"], "none")

    def test_multi_disables_whisper_autotranscribe(self):
        cast = [{"name": "甲", "instruction": "低沉男声"}, {"name": "乙", "instruction": "清亮女声"}]
        graph = bt.build_workflow("multi", text="甲：你好\n乙：你好", cast=cast)
        speakers = [n for n in graph.values() if n["class_type"] == "BreezeTTS2Speaker"]
        self.assertEqual(len(speakers), 2)
        self.assertTrue(all(not s["inputs"]["auto_transcribe_if_empty"] for s in speakers))
        multi = next(n for n in graph.values() if n["class_type"] == "BreezeTTS2MultiSpeaker")
        self.assertIn("speaker_2", multi["inputs"])

    def test_multi_script_uses_ascii_colon(self):
        self.assertEqual(bt.normalize_script("甲：你好：吗\n乙：[笑]好"), "甲:你好：吗\n乙:[笑]好")
        self.assertEqual(bt.normalize_script('[{"speaker":"甲","text":"a：b"}]'), '[{"speaker":"甲","text":"a：b"}]')
        graph = bt.build_workflow("multi", text="甲：你好", cast=[{"name": "甲", "instruction": "低沉男声"}])
        multi = next(n for n in graph.values() if n["class_type"] == "BreezeTTS2MultiSpeaker")
        self.assertEqual(multi["inputs"]["text"], "甲:你好")

    def test_multi_rejects_incomplete_cast(self):
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("multi", text="甲：你好", cast=[{"name": "甲"}])
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("multi", text="甲：你好",
                              cast=[{"name": "甲", "reference_name": "a.wav"}])

    def test_empty_text_and_unknown_model(self):
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("design", text=" ", instruction="x")
        with self.assertRaises(bt.BreezeError):
            bt.build_workflow("design", text="x", instruction="x", model="nope")


class SafetyTests(unittest.TestCase):
    def test_only_loopback(self):
        self.assertEqual(bt.normalize_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        for bad in ("http://example.com", "http://127.0.0.1:8188/x", "ftp://127.0.0.1", "http://u:p@127.0.0.1"):
            with self.assertRaises(bt.BreezeError):
                bt.normalize_url(bad)

    def test_commercial_purpose_rejected_before_any_write(self):
        graph = bt.build_workflow("design", text="x", instruction="y")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            with self.assertRaises(bt.BreezeError) as ctx:
                bt.run_workflow("http://127.0.0.1:1", graph, run_dir, purpose="commercial", timeout_s=1,
                                node_commit="x", meta={})
            self.assertIn("商用", str(ctx.exception))
            self.assertFalse(run_dir.exists())

    def test_unreachable_comfyui_writes_not_submitted_and_keeps_dir(self):
        graph = bt.build_workflow("design", text="x", instruction="y")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            with self.assertRaises(bt.BreezeError):
                bt.run_workflow("http://127.0.0.1:1", graph, run_dir, purpose="evaluation", timeout_s=1,
                                node_commit="x", meta={})
            self.assertIn("not_submitted", (run_dir / "receipt.json").read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                bt.run_workflow("http://127.0.0.1:1", graph, run_dir, purpose="evaluation", timeout_s=1,
                                node_commit="x", meta={})


if __name__ == "__main__":
    unittest.main()
