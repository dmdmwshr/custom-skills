"""video-intake 隔离回归：合成媒体 + 本地 HTTP 站点 + BiliNote 桩 + 旧参数兼容。

不访问外网、不读真实媒体/Obsidian/真实 BiliNote（所有用例显式使用临时 --config）。
需要 yt-dlp/ffmpeg/ffprobe 的用例在缺工具时跳过（WSL 无这些工具；Windows 上全部运行）。
运行：python -I -m unittest discover -s video-intake/tests -v
"""
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "scripts" / "video_intake.py"
SHIM = HERE.parent.parent / "bilinote-video-note" / "scripts" / "save-video-note.ps1"
CONFIG = json.loads((HERE.parent / "references" / "local-config.json").read_text(encoding="utf-8"))
TASK = "11111111-1111-4111-8111-111111111111"
SOURCE = "7693883674231788974"
URL = "https://v.douyin.com/TEST123/"
ENV = {**os.environ, "PYTHONUTF8": "1", "VIDEO_INTAKE_POLL_SECONDS": "0.05"}


def tool(name):
    configured = CONFIG["tools"].get(name)
    if configured and Path(configured).is_file():
        return configured
    return shutil.which(name)


YTDLP, FFMPEG, FFPROBE = tool("yt-dlp"), tool("ffmpeg"), tool("ffprobe")
MEDIA_TOOLS = bool(YTDLP and FFMPEG and FFPROBE)


def run_core(*args, config=None, extra_env=None, timeout=120):
    command = [sys.executable, "-I", str(CORE)]
    if config:
        command += ["--config", str(config)]
    env = {**ENV, **(extra_env or {})}
    proc = subprocess.run(command + [str(a) for a in args], capture_output=True, timeout=timeout, env=env)
    text = proc.stdout.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return proc.returncode, data, proc.stderr.decode("utf-8", "replace")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MediaServer:
    """本地站点：支持 Range；/cut.mp4 可按开关每次只发一半后断开；统计媒体 GET。"""

    def __init__(self, files):
        self.files, self.cut, self.gets, self.slow = files, False, 0, False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.do_GET(head=True)

            def do_GET(self, head=False):
                name = self.path.lstrip("/")
                if name == "slow.mp4":
                    time.sleep(8)
                data = outer.files.get(name.replace("cut", "ok") if name == "cut.mp4" else name)
                if data is None:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
                start = 0
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0] or 0)
                body = data[start:]
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", "video/mp4"); self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(body)))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if head:
                    return
                if not rng:
                    outer.gets += 1
                try:
                    if name == "cut.mp4" and outer.cut:
                        self.wfile.write(body[: len(body) // 2]); self.wfile.flush(); self.close_connection = True; return
                    self.wfile.write(body)
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self, *a):
        self.httpd.shutdown(); self.httpd.server_close()

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"


class BackendStub:
    """BiliNote 桩：health / upload / generate_note / task_status。"""

    def __init__(self, result_dir, health=0, final="SUCCESS"):
        self.result_dir, self.health, self.final = Path(result_dir), health, final
        self.calls, self.payloads, self.uploads = [], [], []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):
                outer.calls.append(self.path)
                if self.path == "/api/sys_health":
                    return self.reply({"code": outer.health, "msg": "bad" if outer.health else "ok"})
                if self.path.startswith("/api/task_status/"):
                    task = self.path.rsplit("/", 1)[1]
                    result = {"markdown": "> 来源链接：x\n\n# 标题\n\n正文内容",
                              "transcript": {"full_text": "转写全文", "segments": [{"start": 0, "end": 1, "text": "转写"}]},
                              "audio_meta": {"title": "合成 视频:标题", "video_id": SOURCE}}
                    return self.reply({"code": 0, "data": {"status": outer.final, "result": result if outer.final == "SUCCESS" else None, "task": task}})
                self.send_response(404); self.end_headers()

            def do_POST(self):
                outer.calls.append(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/api/upload":
                    name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
                    outer.uploads.append(name)
                    return self.reply({"code": 0, "data": {"url": "/uploads/" + name}})
                if self.path == "/api/generate_note":
                    payload = json.loads(body)
                    outer.payloads.append(payload)
                    return self.reply({"code": 0, "data": {"task_id": payload.get("task_id") or TASK}})
                self.send_response(404); self.end_headers()

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self, *a):
        self.httpd.shutdown(); self.httpd.server_close()


def make_config(root, base, **over):
    config = json.loads(json.dumps(CONFIG))
    config.update(backend_base_url=base, backend_result_dir=str(Path(root) / "backend" / "note_results"),
                  note_roots={k: str(Path(root) / "notes" / k) for k in ("bilibili", "douyin")},
                  attachment_roots={k: str(Path(root) / "attach" / k) for k in ("bilibili", "douyin")},
                  model_roles_file=str(Path(root) / "ai-models.json"), timeout_seconds=5, poll_interval_seconds=0.05)
    config.update(over)
    path = Path(root) / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    Path(config["backend_result_dir"]).mkdir(parents=True, exist_ok=True)
    (Path(root) / "ai-models.json").write_text(json.dumps({"strong": "test-model-1", "light": "test-light"}), encoding="utf-8")
    return path


def verified_receipt(root, body=b"original video bytes", url=URL, source=SOURCE):
    media = Path(root) / "videos" / f"sample [{source}].mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(body)
    receipt = {"success": True, "status": "completed", "action": "download", "source_url": url, "source_id": source,
               "file_path": str(media), "bytes": media.stat().st_size, "sha256": sha(media), "title": "样例标题"}
    path = Path(root) / "videos" / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return path, media


class CompatConfigTests(unittest.TestCase):
    def test_bot_compat_config_matches_canonical_keys(self):
        compat = json.loads((HERE.parent.parent / "bilinote-video-note" / "references" / "local-config.json").read_text(encoding="utf-8"))
        for key in ("backend_base_url", "backend_result_dir", "note_roots", "attachment_roots", "supported_platforms", "model_roles_file",
                    "default_request", "frontmatter_defaults", "timeout_seconds", "poll_interval_seconds"):
            self.assertEqual(compat[key], CONFIG[key], key)
        self.assertEqual(compat["superseded_by"], "video-intake")


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vi-note-"); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, stub, *extra, **cfg):
        config = make_config(self.root, stub.base, **cfg)
        return run_core("note", "--video-url", URL, *extra, config=config)

    def test_verified_upload_note_roundtrip_and_dedupe(self):
        receipt, media = verified_receipt(self.root)
        progress = self.root / "progress.json"
        with BackendStub(self.root / "backend" / "note_results") as stub:
            code, out, err = self.note(stub, "--verified-download-receipt", receipt, "--progress-receipt", progress)
            self.assertEqual(code, 0, err + json.dumps(out))
            for key in ("success", "platform", "source_id", "task_id", "note_path", "attachments_dir", "backend_result_path", "message"):
                self.assertIn(key, out)
            self.assertTrue(out["success"]); self.assertEqual(out["source_id"], SOURCE)
            note = Path(out["note_path"]); text = note.read_text(encoding="utf-8")
            self.assertIn(f'source_id: "{SOURCE}"', text); self.assertIn("test-model-1", text); self.assertIn("正文内容", text)
            self.assertNotIn("来源链接：x", text)
            attach = Path(out["attachments_dir"])
            for name in ("markdown-source.md", "transcript.txt", "transcript.json", "task-result.json", "context.json"):
                self.assertTrue((attach / name).is_file(), name)
            self.assertEqual(json.loads(progress.read_text("utf-8"))["status"], "SUCCESS")
            self.assertEqual(len(stub.uploads), 1); self.assertTrue(stub.uploads[0].startswith(f"douyin-{SOURCE}-"))
            self.assertEqual(stub.payloads[0]["platform"], "local")
            self.assertEqual(sha(media), json.loads(receipt.read_text("utf-8"))["sha256"])
            # 再次处理同一来源：更新同一笔记，不产生第二份
            code2, out2, _ = self.note(stub, "--verified-download-receipt", receipt)
            self.assertEqual(code2, 0); self.assertEqual(out2["note_path"], out["note_path"])
            self.assertEqual(len(list(Path(out["note_path"]).parent.glob("*.md"))), 1)

    def test_changed_media_rejected_before_any_backend_submit(self):
        receipt, media = verified_receipt(self.root)
        media.write_bytes(b"tampered")
        with BackendStub(self.root / "backend" / "note_results") as stub:
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt)
            self.assertEqual(code, 1); self.assertFalse(out["success"])
            self.assertEqual(stub.payloads, []); self.assertEqual(stub.uploads, [])

    def test_wrong_source_unfinished_and_non_douyin_rejected(self):
        receipt, _ = verified_receipt(self.root)
        with BackendStub(self.root / "backend" / "note_results") as stub:
            config = make_config(self.root, stub.base)
            code, out, _ = run_core("note", "--video-url", "https://v.douyin.com/OTHER/", "--verified-download-receipt", receipt, config=config)
            self.assertEqual(code, 1)
            data = json.loads(receipt.read_text("utf-8")); data["status"] = "running"; receipt.write_text(json.dumps(data), "utf-8")
            code, out, _ = run_core("note", "--video-url", URL, "--verified-download-receipt", receipt, config=config)
            self.assertEqual(code, 1)
            code, out, _ = run_core("note", "--video-url", "https://www.bilibili.com/video/BV1xx", "--verified-download-receipt", receipt, config=config)
            self.assertEqual(code, 1); self.assertIn("仅支持抖音", out["message"])
            self.assertEqual(stub.payloads, [])

    def test_backend_unhealthy_unsupported_platform_and_nonloopback(self):
        receipt, _ = verified_receipt(self.root)
        with BackendStub(self.root / "backend" / "note_results", health=1) as stub:
            code, out, _ = self.note(stub)
            self.assertEqual(code, 1); self.assertIn("健康检查失败", out["message"]); self.assertEqual(stub.payloads, [])
        with BackendStub(self.root / "backend" / "note_results") as stub:
            config = make_config(self.root, stub.base)
            code, out, _ = run_core("note", "--video-url", "https://example.com/x", config=config)
            self.assertEqual(code, 1); self.assertIn("无法从 URL 识别平台", out["message"])
        sys.path.insert(0, str(CORE.parent))
        import video_intake
        self.assertTrue(video_intake.is_loopback("http://127.0.0.1:8483"))
        self.assertFalse(video_intake.is_loopback("http://192.0.2.1:8483"))
        self.assertFalse(video_intake.is_loopback("ftp://127.0.0.1"))

    def test_timeout_is_reported_without_note_and_keeps_progress(self):
        progress = self.root / "p.json"
        with BackendStub(self.root / "backend" / "note_results", final="RUNNING") as stub:
            code, out, _ = self.note(stub, "--progress-receipt", progress, timeout_seconds=0.3)
            self.assertEqual(code, 1); self.assertIn("等待任务超时", out["message"]); self.assertEqual(out["outcome"], "unknown")
            self.assertEqual(json.loads(progress.read_text("utf-8"))["status"], "RUNNING")
            self.assertFalse((self.root / "notes").exists() and any((self.root / "notes").rglob("*.md")))

    def test_model_precedence_and_invalid_role_file(self):
        with BackendStub(self.root / "backend" / "note_results") as stub:
            code, out, _ = self.note(stub, "--model-name", "explicit-model")
            self.assertEqual(code, 0); self.assertEqual(stub.payloads[-1]["model_name"], "explicit-model")
            (self.root / "ai-models.json").write_text(json.dumps({"strong": "strong"}), "utf-8")
            config = make_config(self.root, stub.base)
            (self.root / "ai-models.json").write_text(json.dumps({"strong": "strong"}), "utf-8")
            code, out, _ = run_core("note", "--video-url", URL, config=config)
            self.assertEqual(code, 1); self.assertIn("缺少有效型号", out["message"])

    # ---- 恢复
    def resume_fixture(self, status="FAILED"):
        receipt, media = verified_receipt(self.root)
        results = self.root / "backend" / "note_results"; results.mkdir(parents=True, exist_ok=True)
        uploads = self.root / "backend" / "uploads"; uploads.mkdir(exist_ok=True)
        uploaded = f"/uploads/douyin-{SOURCE}-{'1' * 32}.mp4"
        (uploads / uploaded.rsplit("/", 1)[1]).write_bytes(media.read_bytes())
        audio = self.root / "backend" / "audio.mp3"; audio.write_bytes(b"audio")
        (results / f"{TASK}.status.json").write_text(json.dumps({"status": status}), "utf-8")
        (results / f"{TASK}_transcript.json").write_text(json.dumps({"full_text": "转写", "segments": [{"text": "a"}]}), "utf-8")
        (results / f"{TASK}_audio.json").write_text(json.dumps({"file_path": str(audio)}), "utf-8")
        old = self.root / "runs" / "old-progress.json"; old.parent.mkdir(exist_ok=True)
        old.write_text(json.dumps({"task_id": TASK, "status": "FAILED", "processing_platform": "local", "source_url": URL,
                                   "uploaded_path": uploaded}), "utf-8")
        return receipt, old, results, uploads

    def test_resume_failed_task_reuses_cache_and_keeps_baseline(self):
        receipt, old, results, uploads = self.resume_fixture()
        new = self.root / "runs2" / "new-progress.json"
        before = sha(old)
        with BackendStub(results) as stub:
            code, out, err = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", new)
            self.assertEqual(code, 0, err + json.dumps(out))
            self.assertEqual(out["task_id"], TASK)
            self.assertEqual(stub.payloads[0]["task_id"], TASK); self.assertEqual(stub.payloads[0]["platform"], "local")
            self.assertEqual(stub.uploads, [])  # 复用原上传，不重新上传
            baseline = new.parent / "resume-baseline"
            manifest = json.loads((baseline / "manifest.json").read_text("utf-8"))
            self.assertGreaterEqual(len(manifest), 4)
            self.assertEqual(sha(old), before)  # 原失败回执保持
            ctx = json.loads((Path(out["attachments_dir"]) / "context.json").read_text("utf-8"))
            self.assertEqual(ctx["resumed_from_progress"], str(old))
            # 基线已存在：再次恢复被拒绝，不重复提交
            n = len(stub.payloads)
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", new)
            self.assertEqual(code, 1); self.assertIn("恢复基线已存在", out["message"]); self.assertEqual(len(stub.payloads), n)

    def test_resume_success_status_only_reads_back(self):
        receipt, old, results, _ = self.resume_fixture(status="SUCCESS")
        with BackendStub(results) as stub:
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old,
                                     "--progress-receipt", self.root / "r3" / "p.json")
            self.assertEqual(code, 0); self.assertEqual(stub.payloads, [])

    def test_resume_guards(self):
        receipt, old, results, uploads = self.resume_fixture()
        with BackendStub(results) as stub:
            cases = []
            # 同一进度路径
            cases.append(("--progress-receipt", old))
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", old)
            self.assertEqual(code, 1); self.assertIn("新的进度回执", out["message"])
            # 无已核验回执
            code, out, _ = self.note(stub, "--resume-progress-receipt", old, "--progress-receipt", self.root / "g1" / "p.json")
            self.assertEqual(code, 1); self.assertIn("已核验视频回执", out["message"])
            # 原上传被改
            up = next(uploads.glob("*.mp4")); up.write_bytes(b"changed")
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", self.root / "g2" / "p.json")
            self.assertEqual(code, 1); self.assertIn("摘要不一致", out["message"])
            up.write_bytes(Path(json.loads(receipt.read_text("utf-8"))["file_path"]).read_bytes())
            # 处理中不重复提交
            (results / f"{TASK}.status.json").write_text(json.dumps({"status": "RUNNING"}), "utf-8")
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", self.root / "g3" / "p.json")
            self.assertEqual(code, 1); self.assertIn("仍在处理", out["message"])
            (results / f"{TASK}.status.json").write_text(json.dumps({"status": "FAILED"}), "utf-8")
            # 缓存缺失
            (results / f"{TASK}_transcript.json").write_text(json.dumps({"full_text": "", "segments": []}), "utf-8")
            code, out, _ = self.note(stub, "--verified-download-receipt", receipt, "--resume-progress-receipt", old, "--progress-receipt", self.root / "g4" / "p.json")
            self.assertEqual(code, 1); self.assertIn("缓存未核实", out["message"])
            self.assertEqual(stub.payloads, []); self.assertEqual(stub.uploads, [])

    def test_status_and_resume_command(self):
        receipt, old, results, _ = self.resume_fixture()
        with BackendStub(results) as stub:
            config = make_config(self.root, stub.base)
            code, out, _ = run_core("status", "--receipt", receipt, "--progress-receipt", old, "--query-backend", config=config)
            self.assertEqual(code, 0); self.assertTrue(out["download"]["sha256_match"]); self.assertEqual(out["note"]["backend_status"], "SUCCESS")
            code, out, err = run_core("resume", "--progress-receipt", old, "--new-progress-receipt", self.root / "r9" / "p.json",
                                      "--receipt", receipt, "--url", URL, config=config)
            self.assertEqual(code, 0, err + json.dumps(out)); self.assertEqual(out["task_id"], TASK)

    def test_status_does_not_modify(self):
        receipt, old, results, _ = self.resume_fixture()
        snapshot = {p: sha(p) for p in (receipt, old)}
        code, out, _ = run_core("status", "--receipt", receipt, "--progress-receipt", old, config=make_config(self.root, "http://127.0.0.1:9"))
        self.assertEqual(code, 0)
        self.assertEqual(snapshot, {p: sha(p) for p in (receipt, old)})


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "旧 PowerShell 兼容入口仅在 Windows 验证")
class ShimTests(unittest.TestCase):
    def test_legacy_script_params_json_and_exit_codes(self):
        with tempfile.TemporaryDirectory(prefix="vi-shim-") as tmp:
            root = Path(tmp)
            receipt, _ = verified_receipt(root)
            with BackendStub(root / "backend" / "note_results") as stub:
                config = make_config(root, stub.base)
                env = {**ENV, "VIDEO_INTAKE_CONFIG": str(config), "VIDEO_INTAKE_PYTHON": sys.executable}
                base = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHIM)]
                ok = subprocess.run(base + ["-VideoUrl", URL, "-VerifiedDownloadReceipt", str(receipt), "-ProgressReceiptPath",
                                            str(root / "pp.json"), "-ModelName", "m-x"], capture_output=True, env=env, timeout=120)
                data = json.loads(ok.stdout.decode("utf-8"))
                self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr); self.assertTrue(data["success"])
                self.assertEqual(stub.payloads[0]["model_name"], "m-x")
                bad = subprocess.run(base + ["-VideoUrl", "https://example.com/z"], capture_output=True, env=env, timeout=120)
                self.assertEqual(bad.returncode, 1); self.assertFalse(json.loads(bad.stdout.decode("utf-8"))["success"])
                missing = subprocess.run(base, capture_output=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
                self.assertNotEqual(missing.returncode, 0)  # 缺必填参数


@unittest.skipUnless(MEDIA_TOOLS, "需要 yt-dlp/ffmpeg/ffprobe（Windows 工具链）")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="vi-media-")
        root = Path(cls.tmp.name)
        cls.sample = root / "sample.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(cls.sample)],
                       check=True, timeout=60)
        cls.silent = root / "silent.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10", "-c:v", "mpeg4",
                        "-an", str(cls.silent)], check=True, timeout=60)
        cls.data = cls.sample.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="vi-run-"); self.root = Path(self.t.name)
        self.config = make_config(self.root, "http://127.0.0.1:9")
        self.out = self.root / "videos"; self.work = self.root / "work"

    def tearDown(self):
        self.t.cleanup()

    def dl(self, server, name, *extra, timeout=120):
        return run_core("download", "--url", server.url(name), "--out-dir", self.out, "--work-root", self.work, *extra,
                        config=self.config, timeout=timeout)

    def test_download_hash_index_dedupe_and_reuse(self):
        with MediaServer({"ok.mp4": self.data, "copy.mp4": self.data, "silent.mp4": self.silent.read_bytes()}) as server:
            code, r1, err = self.dl(server, "ok.mp4")
            self.assertEqual(code, 0, err + json.dumps(r1))
            self.assertTrue(r1["success"] and r1["status"] == "completed" and r1["action"] == "download")
            media = Path(r1["file_path"])
            self.assertTrue(media.is_file()); self.assertEqual(r1["sha256"], sha(media)); self.assertEqual(r1["bytes"], media.stat().st_size)
            self.assertEqual((r1["video_tracks"], r1["audio_tracks"]), (1, 1)); self.assertAlmostEqual(r1["duration"], 3, delta=0.6)
            self.assertEqual(r1["source_key"], "generic:ok"); self.assertFalse(r1["reused"]); self.assertIsNone(r1["duplicate_of"])
            self.assertTrue(Path(r1["receipt_path"]).is_file())
            gets = server.gets
            # 同源再次下载：复用、不再请求媒体、文件不变
            code, r2, _ = self.dl(server, "ok.mp4")
            self.assertEqual(code, 0); self.assertTrue(r2["reused"]); self.assertEqual(r2["sha256"], r1["sha256"]); self.assertEqual(server.gets, gets)
            # 不同来源同内容：标记重复但保留两份
            code, r3, _ = self.dl(server, "copy.mp4")
            self.assertEqual(code, 0); self.assertEqual(r3["duplicate_of"], "generic:ok"); self.assertTrue(Path(r3["file_path"]).is_file())
            self.assertTrue(media.is_file())
            code, chk, _ = run_core("index", "check", "--work-root", self.work, "--sha256", r1["sha256"], config=self.config)
            self.assertTrue(chk["found"] and chk["file_exists"])
            code, lst, _ = run_core("index", "list", "--work-root", self.work, config=self.config)
            events = [e["event"] for e in lst["entries"]]
            self.assertIn("downloaded", events); self.assertIn("reused", events); self.assertIn("started", events)
            # 无音轨来源默认拒绝，显式 --allow-silent 才接受
            code, bad, _ = self.dl(server, "silent.mp4")
            self.assertEqual(code, 1); self.assertIn("音频轨道", bad["message"])
            code, ok, _ = self.dl(server, "silent.mp4", "--allow-silent")
            self.assertEqual(code, 0); self.assertEqual(ok["audio_tracks"], 0)

    def test_interrupted_download_keeps_part_then_resumes(self):
        with MediaServer({"ok.mp4": self.data}) as server:
            server.cut = True
            code, r, _ = self.dl(server, "cut.mp4")
            self.assertEqual(code, 1, json.dumps(r)); self.assertFalse(r["success"])
            parts = list(self.out.glob("*.part"))
            self.assertTrue(parts, "应保留 .part 断点")
            part_size = parts[0].stat().st_size
            self.assertEqual(list(self.out.glob("receipt-*.json")), [])  # 失败不得出完成回执
            code, st, _ = run_core("index", "list", "--work-root", self.work, config=self.config)
            self.assertIn("failed", [e["event"] for e in st["entries"]])
            self.assertFalse((self.work / "locks").exists() and any((self.work / "locks").iterdir()))  # 锁已释放
            server.cut = False
            code, r2, err = self.dl(server, "cut.mp4")
            self.assertEqual(code, 0, err + json.dumps(r2))
            self.assertEqual(r2["sha256"], hashlib.sha256(self.data).hexdigest())  # 续传后与原片逐字节一致
            self.assertGreater(part_size, 0); self.assertEqual(list(self.out.glob("*.part")), [])

    def test_missing_source_fails_and_slow_source_is_unknown(self):
        with MediaServer({}) as server:
            code, r, _ = self.dl(server, "nope.mp4")
            self.assertEqual(code, 1); self.assertFalse(r["success"])
            self.assertEqual(list(self.out.glob("receipt-*.json")), [])
        with MediaServer({"slow.mp4": self.data}) as server:
            # 元数据阶段即超时 -> 未知
            code, r, _ = self.dl(server, "slow.mp4", "--timeout", "1", timeout=60)
            self.assertIn(code, (1, 3)); self.assertFalse(r["success"])

    def test_leftover_lock_blocks_as_unknown(self):
        with MediaServer({"ok.mp4": self.data}) as server:
            (self.work / "locks").mkdir(parents=True)
            (self.work / "locks" / "generic_ok.lock").write_text("999 earlier")
            code, r, _ = self.dl(server, "ok.mp4")
            self.assertEqual(code, 3); self.assertEqual(r["outcome"], "unknown"); self.assertEqual(list(self.out.glob("*.mp4")), [])
            self.assertTrue((self.work / "locks" / "generic_ok.lock").is_file())  # 陈旧锁不自动清除

    def test_extract_audio_and_refuse_overwrite_and_adopt(self):
        with MediaServer({"ok.mp4": self.data}) as server:
            _, r, _ = self.dl(server, "ok.mp4")
        outdir = self.root / "audio"
        code, e, err = run_core("extract", "--receipt", r["receipt_path"], "--audio", "--audio-format", "m4a", "--out-dir", outdir, config=self.config)
        self.assertEqual(code, 0, err + json.dumps(e)); self.assertGreater(e["audio"]["bytes"], 0)
        code, e2, _ = run_core("extract", "--receipt", r["receipt_path"], "--audio", "--audio-format", "m4a", "--out-dir", outdir, config=self.config)
        self.assertEqual(code, 1); self.assertIn("不覆盖", e2["message"])
        # 篡改媒体后回执核验失败
        media = Path(r["file_path"]); media.write_bytes(b"x" * 10)
        code, e3, _ = run_core("extract", "--receipt", r["receipt_path"], "--audio", "--out-dir", outdir, config=self.config)
        self.assertEqual(code, 1); self.assertIn("摘要", e3["message"])
        # 登记外部已下载文件（机器人浏览器回退产物）并生成可被 note 消费的回执
        adopted = self.root / "ext" / f"clip [{SOURCE}].mp4"; adopted.parent.mkdir(); adopted.write_bytes(self.data)
        code, a, err = run_core("download", "--url", URL, "--out-dir", self.root / "ext", "--work-root", self.work, "--adopt", adopted,
                                "--source-id", SOURCE, "--title", "合成", config=self.config)
        self.assertEqual(code, 0, err + json.dumps(a)); self.assertEqual(a["source_key"], f"douyin:{SOURCE}")
        with BackendStub(self.root / "backend" / "note_results") as stub:
            config = make_config(self.root, stub.base)
            code, n, err = run_core("note", "--video-url", URL, "--verified-download-receipt", a["receipt_path"], config=config)
            self.assertEqual(code, 0, err + json.dumps(n))

    def test_status_reports_part_and_hash_state(self):
        with MediaServer({"ok.mp4": self.data}) as server:
            _, r, _ = self.dl(server, "ok.mp4")
        code, st, _ = run_core("status", "--receipt", r["receipt_path"], "--source-key", r["source_key"], "--work-root", self.work, config=self.config)
        self.assertTrue(st["download"]["sha256_match"]); self.assertFalse(st["download"]["part_exists"]); self.assertGreaterEqual(st["index"]["events"], 1)
        resumed = run_core("resume", "--receipt", r["receipt_path"], config=self.config)
        self.assertEqual(resumed[0], 0); self.assertIn("不重复下载", resumed[1]["message"])


if __name__ == "__main__":
    unittest.main()
