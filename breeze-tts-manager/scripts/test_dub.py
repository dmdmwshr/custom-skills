import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import breeze_tts as bt
import dub


def make_project(tmp: Path, cues=None, voices=None) -> Path:
    base = dub.dub_dir(tmp)
    dub.save_json(base / "cues.json", {"cues": cues or [
        {"id": "C1", "shot": "S1", "character": "甲", "text": "你听见了吗？灯塔又亮了。", "direction": "轻声", "pace": "normal"},
        {"id": "C2", "shot": "S1", "character": "乙", "text": "快跑！", "pace": "fast"}]})
    dub.save_json(base / "voices.json", {"voices": voices or {
        "甲": {"instruction": "温柔的年轻女声", "reference": None, "locked": False},
        "乙": {"instruction": "年轻男声", "reference": None, "locked": False}}})
    return tmp


def fake_runner(statuses=None):
    calls = []

    def runner(base, workflow, run_dir, *, purpose, timeout_s, node_commit, meta, text=""):
        calls.append((run_dir.name, meta["mode"], meta["seed"]))
        status = (statuses or {}).get(len(calls), "succeeded_pending_listening")
        run_dir.mkdir(parents=True, exist_ok=False)
        audio = run_dir / "out.flac"
        audio.write_bytes(b"fake-%d" % len(calls))
        receipt = {"status": status, "outputs": [{"file": "out.flac", "sha256": bt.sha256_file(audio)}], "elapsed_s": 1.0}
        (run_dir / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        if status != "succeeded_pending_listening":
            raise bt.BreezeError(status)
        return receipt

    runner.calls = calls
    return runner


class TextScoringTests(unittest.TestCase):
    def test_spoken_chars_drops_tags_punctuation_and_emoji(self):
        self.assertEqual(dub.spoken_chars("[叹气]海雾，漫过(laugh)堤岸。😮"), "海雾漫过堤岸")

    def test_cer(self):
        self.assertEqual(dub.cer("交出春秋蝉，给你个痛快。", "交出春秋蝉，给你个痛快"), 0.0)
        self.assertGreater(dub.cer("交出春秋蝉", "消除春秋尘"), 0.3)
        self.assertEqual(dub.cer("", ""), 0.0)

    def test_flags(self):
        cue = {"pace": "slow"}
        take = {"peak_db": -12.0, "mean_db": -26.6, "trailing_silence": 0.5, "cps": 5.3, "cer": 0.3}
        flags = dub.flags_for(take, cue)
        self.assertIn("偏轻(峰值)", flags)
        self.assertIn("偏轻(均值)", flags)
        self.assertIn("尾部静音长", flags)
        self.assertIn("比期望slow更快", flags)
        self.assertTrue(any(f.startswith("转写差异") for f in flags))
        self.assertEqual(dub.flags_for({"peak_db": -3.0, "mean_db": -20.0, "cps": 4.0, "cer": 0.0}, {"pace": "normal"}), [])


class PlanTests(unittest.TestCase):
    def test_mode_selection(self):
        design = {"instruction": "x", "reference": None}
        locked = {"instruction": "x", "reference": {"path": "voices/甲/ref.flac", "text": "t"}}
        self.assertEqual(dub.choose_mode(design, {"direction": "轻声"}), "design")
        self.assertEqual(dub.choose_mode(locked, {"direction": "轻声"}), "direction")
        self.assertEqual(dub.choose_mode(locked, {}), "clone")

    def test_take_dir_keeps_failed_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = dub.take_dir(base, "C1", 11)
            first.mkdir(parents=True)
            self.assertEqual(dub.take_dir(base, "C1", 11).name, "s11-r2")

    def test_expand_cues_validation(self):
        voices = {"甲": {}}
        cues = [{"id": "C1", "character": "甲", "text": "好"}, {"id": "C2", "character": "丙", "text": "好"}]
        with self.assertRaises(dub.DubError):
            dub.expand_cues(cues, voices, None)
        with self.assertRaises(dub.DubError):
            dub.expand_cues(cues, voices, ["C9"])
        self.assertEqual([c["id"] for c in dub.expand_cues(cues, voices, ["C1"])], ["C1"])


class BatchTests(unittest.TestCase):
    def run_batch(self, project, runner, cues, voices, seeds=(11, 23)):
        return dub.run_batch(project, cues=cues, voices=voices, seeds=list(seeds), base_url="http://127.0.0.1:8188",
                             purpose="evaluation", ffmpeg=r"Z:\none\ffmpeg.exe", timeout_s=5, node_commit="x",
                             model="hybrid", runner=runner, uploader=lambda b, p: "breeze_refs/" + p.name)

    def test_batch_runs_every_seed_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            cues = dub.load_json(dub.dub_dir(project) / "cues.json")["cues"]
            voices = dub.load_json(dub.dub_dir(project) / "voices.json")["voices"]
            runner = fake_runner()
            summary = self.run_batch(project, runner, cues, voices)
            self.assertEqual(summary["succeeded"], 4)
            self.assertEqual([c[1] for c in runner.calls], ["design"] * 4)
            index = dub.load_json(dub.dub_dir(project) / "takes.json")
            self.assertEqual(len(index["takes"]), 4)
            again = fake_runner()
            summary = self.run_batch(project, again, cues, voices)
            self.assertEqual((summary["succeeded"], summary["skipped"]), (0, 4))
            self.assertEqual(again.calls, [])

    def test_failure_continues_but_unknown_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            cues = dub.load_json(dub.dub_dir(project) / "cues.json")["cues"]
            voices = dub.load_json(dub.dub_dir(project) / "voices.json")["voices"]
            summary = self.run_batch(project, fake_runner({1: "failed"}), cues, voices)
            self.assertEqual((summary["failed"], summary["succeeded"]), (1, 3))
            with tempfile.TemporaryDirectory() as tmp2:
                project2 = make_project(Path(tmp2))
                summary = self.run_batch(project2, fake_runner({2: "result_unknown"}), cues, voices)
                self.assertEqual(summary["stopped_unknown"], {"cue": "C1", "take": "s23"})
                self.assertEqual(summary["succeeded"], 1)

    def test_locked_voice_uses_reference_and_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            base = dub.dub_dir(project)
            ref = base / "voices" / "甲" / "ref.flac"
            ref.parent.mkdir(parents=True)
            ref.write_bytes(b"ref")
            voices = {"甲": {"instruction": "温柔", "locked": True,
                             "reference": {"path": "voices/甲/ref.flac", "text": "参考文本"}}}
            cues = [{"id": "C1", "character": "甲", "text": "好", "direction": "轻声"}]
            runner = fake_runner()
            self.run_batch(project, runner, cues, voices, seeds=(11,))
            self.assertEqual(runner.calls[0][1], "direction")


class SelectionTests(unittest.TestCase):
    def prepared(self, tmp):
        project = make_project(Path(tmp))
        cues = dub.load_json(dub.dub_dir(project) / "cues.json")["cues"]
        voices = dub.load_json(dub.dub_dir(project) / "voices.json")["voices"]
        BatchTests().run_batch(project, fake_runner(), cues, voices, seeds=(11,))
        return project

    def test_select_requires_successful_take(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.prepared(tmp)
            with self.assertRaises(dub.DubError):
                dub.select_take(project, "C1", "s99", "", "用户试听")
            with self.assertRaises(dub.DubError):
                dub.select_take(project, "C1", "s11", "", " ")
            sel = dub.select_take(project, "C1", "s11", "听后选定", "用户试听")
            self.assertEqual(sel["by"], "用户试听")

    def test_lock_voice_needs_matching_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.prepared(tmp)
            with self.assertRaises(dub.DubError):  # 没转写
                dub.lock_voice(project, "甲", "C1", "s11", None, "用户试听")
            index_path = dub.dub_dir(project) / "takes.json"
            index = dub.load_json(index_path)
            for t in index["takes"]:
                if t["cue"] == "C1":
                    t["asr"], t["cer"] = "你听见了吗灯塔又亮了😮", 0.5
            dub.save_json(index_path, index)
            with self.assertRaises(dub.DubError):  # 差异过大必须手工给文本
                dub.lock_voice(project, "甲", "C1", "s11", None, "用户试听")
            ref = dub.lock_voice(project, "甲", "C1", "s11", "你听见了吗？灯塔又亮了。", "用户试听")
            voices = dub.load_json(dub.dub_dir(project) / "voices.json")["voices"]
            self.assertTrue(voices["甲"]["locked"])
            self.assertTrue((dub.dub_dir(project) / ref["path"]).is_file())
            self.assertEqual(ref["locked_by"], "用户试听")

    def test_ingest_checks_hash_and_calls_media_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.prepared(tmp)
            dub.select_take(project, "C1", "s11", "", "用户试听")
            with mock.patch.object(bt, "ingest_run", return_value={"audio_id": "a1", "audio": {"path": "p"}}) as m:
                result = dub.ingest_selected(project, Path("mc"), project / "audio", "final_dialogue")
            self.assertEqual(result[0]["audio_id"], "a1")
            self.assertEqual(m.call_args.kwargs["speaker"], "甲")
            self.assertEqual(m.call_args.kwargs["shot_id"], "S1")
            sel = dub.load_json(dub.dub_dir(project) / "selection.json")
            Path(sel["C1"]["audio"]).write_bytes(b"tampered")
            with self.assertRaises(dub.DubError):
                dub.ingest_selected(project, Path("mc"), project / "audio", "final_dialogue")

    def test_review_lists_flags_and_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.prepared(tmp)
            dub.select_take(project, "C1", "s11", "", "用户试听")
            cues = {c["id"]: c for c in dub.load_json(dub.dub_dir(project) / "cues.json")["cues"]}
            text = dub.write_review(project, cues).read_text(encoding="utf-8")
            self.assertIn("## C1（甲）", text)
            self.assertIn("s11 ✔", text)


class CliGateTests(unittest.TestCase):
    def test_batch_rejects_commercial_before_any_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            code = dub.main(["--project", tmp, "batch", "--purpose", "commercial", "--yes"])
            self.assertEqual(code, 2)
            self.assertFalse((dub.dub_dir(Path(tmp)) / "takes.json").exists())

    def test_init_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = dub.dub_dir(Path(tmp))
            dub.save_json(base / "cues.json", {"cues": []})
            dub.main(["--project", tmp, "init"])
            self.assertEqual(dub.load_json(base / "cues.json"), {"cues": []})
            self.assertTrue((base / "voices.json").is_file())


if __name__ == "__main__":
    unittest.main()
