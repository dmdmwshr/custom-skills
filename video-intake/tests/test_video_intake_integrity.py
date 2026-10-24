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


class DownloadFixture:
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


class DownloadIntegrity(DownloadFixture, unittest.TestCase):
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


class NoteFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vi-notes-")
        self.root = Path(self.tmp.name)
        (self.root / "notes").mkdir()
        (self.root / "backend").mkdir()
        self.template = self.root / "template.md"
        self.template.write_text("---\nskill: bilinote-video-note\nsource_platform: \"{{source_platform}}\"\nsource_id: \"{{source_id}}\"\n---\n# {{title}}\n{{body_markdown}}\n{{attachments_links}}\n", encoding="utf-8")
        self.config = note_config(self.root, self.root / "backend")
        FakeBackend.posts, FakeBackend.status_state, FakeBackend.body = 0, "SUCCESS", "generated body"

    def tearDown(self):
        self.tmp.cleanup()

    def run_note(self, *extra, config=None):
        args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                            "--template", str(self.template), *extra])
        with patch.object(m, "Backend", FakeBackend):
            return m.cmd_note(args, config or self.config)


class NoteProtection(NoteFixture, unittest.TestCase):
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
        # 旧笔记受保护 -> 其原附件路径原字节不动，新候选用独立附件束并正确关联
        self.assertEqual((attach / "markdown-source.md").read_text("utf-8"), "generated body")
        bundle = Path(second["attachments_dir"])
        self.assertNotEqual(bundle, attach); self.assertEqual(bundle.parent.parent, attach)
        self.assertIn("second body", (bundle / "markdown-source.md").read_text("utf-8"))
        self.assertIn("candidates", Path(second["note_path"]).read_text("utf-8"))

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

    def test_refused_is_definite_but_timeout_stays_unknown(self):
        import socket
        import urllib.error
        backend = m.Backend("http://127.0.0.1:1")
        with patch.object(m.urllib.request, "urlopen", side_effect=urllib.error.URLError(ConnectionRefusedError(111, "refused"))):
            with self.assertRaises(m.IntakeError) as refused:
                backend.call("/api/generate_note", {"x": 1})
        self.assertNotIsInstance(refused.exception, m.UnknownOutcome)
        with patch.object(m.urllib.request, "urlopen", side_effect=urllib.error.URLError(socket.timeout("timed out"))):
            with self.assertRaises(m.UnknownOutcome):
                backend.call("/api/generate_note", {"x": 1})
        with patch.object(m.urllib.request, "urlopen", side_effect=urllib.error.URLError(socket.gaierror(-2, "no name"))):
            with self.assertRaises(m.IntakeError) as dns:
                backend.call("/api/generate_note", {"x": 1})
        self.assertNotIsInstance(dns.exception, m.UnknownOutcome)

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

class ReworkBoundaries(NoteFixture, unittest.TestCase):
    """阶段 4 返工：R1–R5 的真实边界。继承 NoteProtection 的夹具，不重复其用例。"""

    def test_r2_a_b_a_does_not_reuse_stale_generation(self):
        a = self.run_note("--model-name", "A")
        FakeBackend.body = "body B"
        self.run_note("--model-name", "B")
        self.assertEqual(FakeBackend.posts, 2)
        FakeBackend.body = "body A again"
        again = self.run_note("--model-name", "A")
        self.assertEqual(FakeBackend.posts, 3, "旧记录指向已被替换的路径，不得命中复用")
        text = Path(again["note_path"]).read_text("utf-8")
        self.assertIn("body A again", text)
        # 紧接着的无变化重复请求才复用
        self.run_note("--model-name", "A")
        self.assertEqual(FakeBackend.posts, 3)

    def test_r2_edited_note_reuse_is_marked_modified(self):
        first = self.run_note()
        note = Path(first["note_path"]); note.write_text(note.read_text("utf-8") + "\nEDIT", encoding="utf-8")
        again = self.run_note()
        self.assertEqual(FakeBackend.posts, 1); self.assertIn("modified", again["message"])
        self.assertIn("EDIT", note.read_text("utf-8"))

    def test_r2_tampered_attachment_bundle_blocks_reuse(self):
        first = self.run_note()
        (Path(first["attachments_dir"]) / "transcript.txt").write_text("hand edited", encoding="utf-8")
        self.run_note()
        self.assertEqual(FakeBackend.posts, 2)

    def test_r3_modified_attachment_and_note_keep_original_paths_and_bytes(self):
        first = self.run_note()
        note = Path(first["note_path"]); attach = Path(first["attachments_dir"])
        note.write_text(note.read_text("utf-8") + "\nUSER", encoding="utf-8")
        raw = attach / "markdown-source.md"; raw.write_text("MANUAL ATTACHMENT", encoding="utf-8")
        before = {p: p.read_bytes() for p in (note, raw, attach / "context.json", attach / "transcript.txt")}
        FakeBackend.body = "third"
        third = self.run_note("--model-name", "C")
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertNotEqual(Path(third["attachments_dir"]), attach)
        self.assertIn("third", (Path(third["attachments_dir"]) / "markdown-source.md").read_text("utf-8"))

    def test_r3_unmodified_note_but_edited_attachment_uses_new_bundle(self):
        first = self.run_note()
        attach = Path(first["attachments_dir"])
        (attach / "transcript.txt").write_text("hand edited", encoding="utf-8")
        second = self.run_note("--model-name", "B")
        self.assertEqual((attach / "transcript.txt").read_text("utf-8"), "hand edited")
        self.assertNotEqual(Path(second["attachments_dir"]), attach)

    def test_r4_empty_or_incomplete_ack_is_unknown_and_keeps_progress(self):
        for ack in (None, {"code": 0}, {"code": 0, "data": {}}, {"code": 0, "data": None}, "ok"):
            progress = self.root / "p.json"
            if progress.exists():
                progress.unlink()

            class Ack(FakeBackend):
                def call(self, path, body=None, timeout=120):
                    return ack if path == "/api/generate_note" else super().call(path, body, timeout)

            args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                                "--template", str(self.template), "--progress-receipt", str(progress)])
            with patch.object(m, "Backend", Ack):
                result = m.cmd_note(args, self.config)
            self.assertEqual(result["outcome"], "unknown", ack)
            self.assertEqual(json.loads(progress.read_text("utf-8"))["status"], "unknown")

    def test_r4_explicit_application_rejection_is_failed(self):
        class Reject(FakeBackend):
            def call(self, path, body=None, timeout=120):
                return {"code": 500, "msg": "bad model"} if path == "/api/generate_note" else super().call(path, body, timeout)

        args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                            "--template", str(self.template)])
        with patch.object(m, "Backend", Reject):
            self.assertEqual(m.cmd_note(args, self.config)["outcome"], "failed")

    def test_r5_unsettled_progress_receipt_blocks_second_post(self):
        for status in ("unknown", "submitting", "RUNNING"):
            progress = self.root / f"p-{status}.json"
            progress.write_text(json.dumps({"task_id": None, "status": status, "source_url": "x"}), encoding="utf-8")
            before = progress.read_bytes()
            posts = FakeBackend.posts
            args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                                "--template", str(self.template), "--progress-receipt", str(progress)])
            with patch.object(m, "Backend", FakeBackend):
                result = m.cmd_note(args, self.config)
            self.assertEqual(result["outcome"], "unknown", status)
            self.assertEqual(FakeBackend.posts, posts); self.assertEqual(progress.read_bytes(), before)

    def test_r5_terminal_progress_is_copied_before_reuse_of_path(self):
        progress = self.root / "p.json"
        progress.write_text(json.dumps({"task_id": TASK, "status": "FAILED", "source_url": "x"}), encoding="utf-8")
        args = m.build_parser().parse_args(["note", "--video-url", "https://www.bilibili.com/video/BVtest", "--platform", "bilibili",
                                            "--template", str(self.template), "--progress-receipt", str(progress)])
        with patch.object(m, "Backend", FakeBackend):
            self.assertTrue(m.cmd_note(args, self.config)["success"])
        self.assertEqual(len(list(self.root.glob("p.prev-*.json"))), 1)

    def test_resume_success_keeps_action_resume(self):
        with tempfile.TemporaryDirectory(prefix="vi-act-") as td:
            media = Path(td) / "a.mp4"; media.write_bytes(b"abc")
            receipt = Path(td) / "r.json"
            m.write_json(receipt, {"status": "completed", "file_path": str(media), "sha256": sha(b"abc"), "bytes": 3})
            out = m.cmd_resume(m.build_parser().parse_args(["resume", "--receipt", str(receipt)]), {})
            self.assertEqual(out["action"], "resume")


class SilentAfterProbeRejection(DownloadFixture, unittest.TestCase):
    def test_allow_silent_continues_from_own_fetch_without_redownload(self):
        m.Index(self.work).path.unlink()
        self.media.unlink()
        fresh = self.out / "fixture [12345].mp4"
        downloads = []

        def runner(cmd, timeout):
            if "-show_entries" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"format": {"duration": "3"}, "streams": [{"codec_type": "video"}]}).encode(), b"")
            if not fresh.exists():  # --no-overwrites：已存在则不重写，只回显路径
                fresh.write_bytes(b"silent video bytes"); downloads.append(1)
            return subprocess.CompletedProcess(cmd, 0, (str(fresh) + "\n").encode(), b"")

        with self.assertRaises(m.IntakeError) as ctx:
            self.run_download(runner)
        self.assertIn("音频", str(ctx.exception))
        self.assertTrue(fresh.is_file())  # 文件与断点保留
        args = m.build_parser().parse_args(["download", "--url", URL, "--out-dir", str(self.out), "--work-root", str(self.work), "--allow-silent"])
        meta = {"id": "12345", "title": "fixture", "duration": 3, "webpage_url": URL}
        with patch.object(m, "find_tool", return_value="engine"), patch.object(m, "fetch_metadata", return_value=meta), \
                patch.object(m, "run_tool", side_effect=runner):
            result = m.cmd_download(args, {})
        self.assertTrue(result["success"]); self.assertEqual(result["audio_tracks"], 0); self.assertEqual(len(downloads), 1)

    def test_changed_after_own_fetch_is_not_trusted(self):
        m.Index(self.work).path.unlink()
        self.media.unlink()
        fresh = self.out / "fixture [12345].mp4"

        def runner(cmd, timeout):
            if "-show_entries" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"format": {"duration": "3"}, "streams": [{"codec_type": "video"}]}).encode(), b"")
            if not fresh.exists():
                fresh.write_bytes(b"silent video bytes")
            return subprocess.CompletedProcess(cmd, 0, (str(fresh) + "\n").encode(), b"")

        with self.assertRaises(m.IntakeError):
            self.run_download(runner)
        fresh.write_bytes(b"someone replaced it")
        args = m.build_parser().parse_args(["download", "--url", URL, "--out-dir", str(self.out), "--work-root", str(self.work), "--allow-silent"])
        meta = {"id": "12345", "title": "fixture", "duration": 3, "webpage_url": URL}
        with patch.object(m, "find_tool", return_value="engine"), patch.object(m, "fetch_metadata", return_value=meta), \
                patch.object(m, "run_tool", side_effect=runner):
            with self.assertRaises(m.IntakeError):
                m.cmd_download(args, {})
        self.assertEqual(fresh.read_bytes(), b"someone replaced it")


if __name__ == "__main__":
    unittest.main()
