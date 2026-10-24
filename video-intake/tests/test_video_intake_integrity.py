"""video-intake 完整性回归（进程内、合成文件 + 本地 HTTP，不依赖 yt-dlp/ffmpeg，WSL 可跑）。

对应 CAPABILITY-MERGE 阶段 4 的四组缺陷：F1 已登记成品被改动、F2 completed 回执成品缺失、
F3 笔记/附件覆盖与 source_id 误匹配、F4 提交结果未知与明确 FAILED。
运行：python -I -m unittest discover -s video-intake/tests -v
"""
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parent.parent / "scripts" / "video_intake.py"
spec = importlib.util.spec_from_file_location("video_intake_core", CORE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TASK = "11111111-1111-4111-8111-111111111111"
URL = "https://www.douyin.com/video/12345"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def probe_ok(cmd):
    return subprocess.CompletedProcess(cmd, 0, json.dumps(
        {"format": {"duration": "3"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}).encode(), b"")


class DownloadIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vi-int-")
        self.root = Path(self.tmp.name)
        self.out, self.work = self.root / "downloads", self.root / "work"
        self.out.mkdir()
        self.media = self.out / "fixture [12345].mp4"
        self.media.write_bytes(b"original source")
        self.old = sha(b"original source")
        m.Index(self.work).append("downloaded", source_key="douyin:12345", source_id="12345", platform="douyin",
                                  source_url=URL, file_path=str(self.media), sha256=self.old)
        self.receipt = self.out / "receipt-douyin-12345.json"
        m.write_json(self.receipt, {"success": True, "status": "completed", "file_path": str(self.media), "sha256": self.old,
                                    "bytes": self.media.stat().st_size})

    def tearDown(self):
        self.tmp.cleanup()

    def run_download(self, runner):
        args = m.build_parser().parse_args(["download", "--url", URL, "--out-dir", str(self.out), "--work-root", str(self.work)])
        meta = {"id": "12345", "title": "fixture", "duration": 3, "webpage_url": URL}
        with patch.object(m, "find_tool", return_value="engine"), patch.object(m, "fetch_metadata", return_value=meta), \
                patch.object(m, "run_tool", side_effect=runner):
            return m.cmd_download(args, {})

    def test_changed_indexed_media_is_not_reminted(self):
        self.media.write_bytes(b"manually changed")
        calls = []

        def runner(cmd, timeout):
            calls.append(cmd)
            return probe_ok(cmd) if "-show_entries" in cmd else subprocess.CompletedProcess(cmd, 0, (str(self.media) + "\n").encode(), b"")

        before = self.receipt.read_bytes()
        with self.assertRaises(m.IntakeError) as ctx:
            self.run_download(runner)
        self.assertEqual(ctx.exception.category, "failed")
        self.assertEqual(calls, [], "摘要不符时不得再启动下载引擎")
        self.assertEqual(self.media.read_bytes(), b"manually changed")
        self.assertEqual(self.receipt.read_bytes(), before)
        self.assertIn("integrity_mismatch", [e["event"] for e in m.Index(self.work).entries()])

    def test_preexisting_unwritten_file_gets_no_success_receipt(self):
        # 索引没有该来源；--no-overwrites 返回一个本次没写过的既有文件
        m.Index(self.work).path.unlink()
        stray = self.out / "stray [12345].mp4"
        stray.write_bytes(b"unknown provenance")

        def runner(cmd, timeout):
            return probe_ok(cmd) if "-show_entries" in cmd else subprocess.CompletedProcess(cmd, 0, (str(stray) + "\n").encode(), b"")

        with self.assertRaises(m.IntakeError):
            self.run_download(runner)
        self.assertEqual(stray.read_bytes(), b"unknown provenance")
        self.assertEqual(json.loads(self.receipt.read_text("utf-8"))["sha256"], self.old)

    def test_fresh_download_archives_a_different_old_receipt(self):
        m.Index(self.work).path.unlink()
        self.media.unlink()
        self.receipt.write_text(json.dumps({"status": "completed", "file_path": "old", "sha256": "0" * 64}), encoding="utf-8")
        fresh = self.out / "fixture [12345].mp4"

        def runner(cmd, timeout):
            if "-show_entries" in cmd:
                return probe_ok(cmd)
            fresh.write_bytes(b"new bytes")
            return subprocess.CompletedProcess(cmd, 0, (str(fresh) + "\n").encode(), b"")

        result = self.run_download(runner)
        self.assertTrue(result["success"])
        archived = list(self.out.glob("receipt-douyin-12345.prev-*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text("utf-8"))["file_path"], "old")

    def test_download_timeout_keeps_unknown_lock_until_explicit_clear(self):
        self.media.unlink()
        m.Index(self.work).path.unlink()

        def timeout_runner(cmd, timeout):
            raise m.UnknownOutcome("外部工具超时")

        with self.assertRaises(m.UnknownOutcome):
            self.run_download(timeout_runner)
        lock = self.work / "locks" / "douyin_12345.lock"
        self.assertTrue(lock.is_file() and lock.read_text("utf-8").startswith("unknown"))
        with self.assertRaises(m.UnknownOutcome):  # 盲目重跑被拒
            self.run_download(timeout_runner)
        args = m.build_parser().parse_args(["download", "--url", URL, "--out-dir", str(self.out), "--work-root", str(self.work),
                                            "--clear-unknown-lock"])
        fresh = self.out / "fixture [12345].mp4"

        def runner(cmd, timeout):
            if "-show_entries" in cmd:
                return probe_ok(cmd)
            fresh.write_bytes(b"resumed")
            return subprocess.CompletedProcess(cmd, 0, (str(fresh) + "\n").encode(), b"")

        meta = {"id": "12345", "title": "fixture", "duration": 3, "webpage_url": URL}
        with patch.object(m, "find_tool", return_value="engine"), patch.object(m, "fetch_metadata", return_value=meta), \
                patch.object(m, "run_tool", side_effect=runner):
            self.assertTrue(m.cmd_download(args, {})["success"])
        self.assertFalse(lock.exists())


class ResumeVerification(unittest.TestCase):
    def test_completed_receipt_with_missing_or_changed_media_fails(self):
        with tempfile.TemporaryDirectory(prefix="vi-res-") as td:
            root = Path(td)
            media = root / "a.mp4"
            media.write_bytes(b"abc")
            receipt = root / "r.json"
            m.write_json(receipt, {"status": "completed", "file_path": str(media), "sha256": sha(b"abc"), "bytes": 3})
            parse = lambda: m.build_parser().parse_args(["resume", "--receipt", str(receipt)])
            self.assertTrue(m.cmd_resume(parse(), {})["success"])
            media.write_bytes(b"abd")
            changed = m.cmd_resume(parse(), {})
            self.assertFalse(changed["success"]); self.assertFalse(changed["download"]["sha256_match"])
            media.unlink()
            missing = m.cmd_resume(parse(), {})
            self.assertFalse(missing["success"]); self.assertFalse(missing["download"]["file_exists"])
            self.assertFalse(media.exists())  # 不自动重下
            self.assertEqual(json.loads(receipt.read_text("utf-8"))["sha256"], sha(b"abc"))


def note_config(root, backend_dir):
    return {"supported_platforms": ["bilibili"], "backend_base_url": "http://127.0.0.1:1", "backend_result_dir": str(backend_dir),
            "default_request": {"provider_id": "test", "model_name": "model-a", "quality": "medium", "screenshot": False, "link": True,
                                "format": [], "style": "", "video_understanding": False},
            "poll_interval_seconds": 0, "timeout_seconds": 2, "note_roots": {"bilibili": str(root / "notes")},
            "attachment_roots": {"bilibili": str(root / "attach")},
            "frontmatter_defaults": {"type": "t", "status": "draft", "area_link": "", "tags": [], "related_links": []}}


class FakeBackend:
    posts = 0
    status_state = "SUCCESS"
    body = "generated body"

    def __init__(self, base):
        pass

    def call(self, path, body=None, timeout=120):
        if path == "/api/sys_health":
            return {"code": 0}
        if path == "/api/generate_note":
            type(self).posts += 1
            return {"code": 0, "data": {"task_id": TASK}}
        return {"code": 0, "data": {"status": type(self).status_state, "result": {
            "audio_meta": {"video_id": "12345", "title": "Generated"}, "markdown": type(self).body,
            "transcript": {"full_text": "t"}}}}


class NoteProtection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vi-notes-")
        self.root = Path(self.tmp.name)
        (self.root / "notes").mkdir()
        (self.root / "backend").mkdir()
        self.template = self.root / "template.md"
        self.template.write_text("---\nskill: bilinote-video-note\nsource_platform: \"{{source_platform}}\"\nsource_id: \"{{source_id}}\"\n---\n# {{title}}\n{{body_markdown}}\n", encoding="utf-8")
        self.config = note_config(self.root, self.root / "backend")
        FakeBackend.posts, FakeBackend.status_state, FakeBackend.body = 0, "SUCCESS", "generated body"

    def tearDown(self):
        self.tmp.cleanup()

    def run_note(self, *extra, config=None):
        args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                            "--template", str(self.template), *extra])
        with patch.object(m, "Backend", FakeBackend):
            return m.cmd_note(args, config or self.config)

    def test_source_id_prefix_does_not_match_other_source(self):
        other = self.root / "notes" / "other.md"
        other.write_text("---\nsource_id: 123456\n---\nbody mentions source_id: 12345\n", encoding="utf-8")
        self.assertEqual(m.existing_notes(self.root / "notes", "12345"), [])
        self.assertEqual(m.existing_notes(self.root / "notes", "123456"), [other])

    def test_manual_note_is_kept_and_new_result_goes_to_candidate(self):
        manual = self.root / "notes" / "manual.md"
        original = "---\nskill: manual\nsource_id: 12345\n---\nUSER EDITED BODY\n"
        manual.write_text(original, encoding="utf-8")
        result = self.run_note()
        self.assertTrue(result["success"], result)
        self.assertEqual(manual.read_text("utf-8"), original)
        note = Path(result["note_path"])
        self.assertNotEqual(note, manual)
        self.assertIn("generated body", note.read_text("utf-8"))

    def test_manual_note_on_canonical_path_forces_candidate(self):
        canonical = self.root / "notes" / "12345 - Generated.md"
        original = "---\nsource_id: 12345\n---\nHAND WRITTEN\n"
        canonical.write_text(original, encoding="utf-8")
        result = self.run_note()
        self.assertTrue(result["success"], result)
        self.assertEqual(canonical.read_text("utf-8"), original)
        self.assertIn("候选", Path(result["note_path"]).name)
        self.assertIn("generated body", Path(result["note_path"]).read_text("utf-8"))

    def test_same_request_reuses_without_new_model_call_and_edits_survive(self):
        first = self.run_note()
        self.assertEqual(FakeBackend.posts, 1)
        note = Path(first["note_path"])
        note.write_text(note.read_text("utf-8") + "\nUSER ADDED\n", encoding="utf-8")
        again = self.run_note()
        self.assertTrue(again["success"]); self.assertEqual(FakeBackend.posts, 1)
        self.assertEqual(again["note_path"], first["note_path"])
        self.assertIn("USER ADDED", note.read_text("utf-8"))
        self.assertEqual(len(list((self.root / "notes").glob("*.md"))), 1)

    def test_real_difference_archives_old_attachments_and_protects_modified_note(self):
        first = self.run_note()
        note = Path(first["note_path"]); attach = Path(first["attachments_dir"])
        edited = note.read_text("utf-8") + "\nUSER ADDED\n"
        note.write_text(edited, encoding="utf-8")
        FakeBackend.body = "second body"
        second = self.run_note("--model-name", "model-b")
        self.assertEqual(FakeBackend.posts, 2)
        self.assertEqual(note.read_text("utf-8"), edited)  # 已修改的旧笔记不动
        self.assertIn("候选", Path(second["note_path"]).name)
        self.assertIn("second body", (attach / "markdown-source.md").read_text("utf-8"))
        archived = list((attach / "history").rglob("markdown-source.md"))
        self.assertEqual([p.read_text("utf-8") for p in archived], ["generated body"])

    def test_unmodified_generated_note_is_updated_in_place_with_archive(self):
        first = self.run_note()
        note = Path(first["note_path"])
        FakeBackend.body = "second body"
        second = self.run_note("--model-name", "model-b")
        self.assertEqual(second["note_path"], first["note_path"])
        self.assertIn("second body", note.read_text("utf-8"))
        archived = list((Path(first["attachments_dir"]) / "history").rglob("previous-note.md"))
        self.assertEqual(len(archived), 1); self.assertIn("generated body", archived[0].read_text("utf-8"))
        self.assertEqual(len(list((self.root / "notes").glob("*.md"))), 1)


class LostResponseHandler(BaseHTTPRequestHandler):
    accepted = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).accepted.append(True)
        self.close_connection = True
        self.connection.shutdown(2)
        self.connection.close()


class SubmitOutcome(unittest.TestCase):
    def test_accepted_post_with_lost_response_is_unknown_but_get_is_failed(self):
        LostResponseHandler.accepted = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), LostResponseHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            backend = m.Backend(f"http://127.0.0.1:{server.server_port}")
            with self.assertRaises(m.UnknownOutcome):
                backend.call("/api/generate_note", {"x": 1}, timeout=2)
            self.assertTrue(LostResponseHandler.accepted)
            with self.assertRaises(m.IntakeError) as ctx:  # 只读 GET 失败仍是可重试的明确失败
                backend.call("/api/sys_health", timeout=2)
            self.assertNotIsInstance(ctx.exception, m.UnknownOutcome)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_connection_refused_post_is_definite_failure(self):
        sock = __import__("socket").socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        with self.assertRaises(m.IntakeError) as ctx:
            m.Backend(f"http://127.0.0.1:{port}").call("/api/generate_note", {"x": 1}, timeout=2)
        self.assertNotIsInstance(ctx.exception, m.UnknownOutcome)

    def test_note_marks_unknown_progress_and_never_resubmits(self):
        class Lost(FakeBackend):
            def call(self, path, body=None, timeout=120):
                if path == "/api/generate_note":
                    raise m.UnknownOutcome("lost")
                return super().call(path, body, timeout)

        with tempfile.TemporaryDirectory(prefix="vi-lost-") as td:
            root = Path(td); (root / "backend").mkdir()
            template = root / "t.md"; template.write_text("---\nsource_id: \"{{source_id}}\"\n---\n{{body_markdown}}", encoding="utf-8")
            progress = root / "p.json"
            args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVx", "--platform", "bilibili",
                                                "--template", str(template), "--progress-receipt", str(progress)])
            with patch.object(m, "Backend", Lost):
                result = m.cmd_note(args, note_config(root, root / "backend"))
            self.assertFalse(result["success"]); self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(json.loads(progress.read_text("utf-8"))["status"], "unknown")
            self.assertFalse((root / "notes").exists() and any((root / "notes").rglob("*.md")))

    def test_explicit_failed_returns_promptly_with_task_id(self):
        with tempfile.TemporaryDirectory(prefix="vi-failed-") as td:
            root = Path(td); (root / "backend").mkdir()
            template = root / "t.md"; template.write_text("x", encoding="utf-8")
            progress = root / "p.json"
            config = note_config(root, root / "backend"); config["timeout_seconds"] = 600
            FakeBackend.status_state = "FAILED"
            args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVx", "--platform", "bilibili",
                                                "--template", str(template), "--progress-receipt", str(progress)])
            try:
                with patch.object(m, "Backend", FakeBackend):
                    result = m.cmd_note(args, config)
            finally:
                FakeBackend.status_state = "SUCCESS"
            self.assertFalse(result["success"]); self.assertEqual(result["outcome"], "failed"); self.assertEqual(result["task_id"], TASK)
            self.assertEqual(json.loads(progress.read_text("utf-8"))["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
