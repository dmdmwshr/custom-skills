"""模式 B 隔离包装与 with_server 的回归：守卫拒绝 + 真实无头浏览器 + 精确收尾。
真实浏览器用例需要 playwright-cli 与 Chrome/Edge，缺失则跳过。不访问外网，不碰受管资料。"""
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRAP = HERE / "isolated_cli.py"
SERVER = HERE / "webapp" / "with_server.py"
CLI = os.environ.get("PLAYWRIGHT_CLI") or shutil.which("playwright-cli.cmd" if os.name == "nt" else "playwright-cli")


def wrap(root, *args, timeout=120):
    env = {**os.environ, "PBC_ISOLATED_ROOT": str(root), "PYTHONUTF8": "1"}
    p = subprocess.run([sys.executable, "-X", "utf8", "-I", str(WRAP), *map(str, args)], capture_output=True, env=env, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_forbidden_commands_and_flags_rejected_before_any_cli_call(self):
        env_cli = {"PLAYWRIGHT_CLI": str(self.root / "must-not-run")}
        for args in (["attach"], ["open", "--extension"], ["open", "--profile", "x"], ["open", "--persistent"], ["close-all"],
                     ["kill-all"], ["open", "--user-data-dir=/x"], ["open", "-s=other"], ["open", "--session", "x"], ["delete-data"],
                     ["list"], ["open", "--cdp", "http://x"], []):
            rc, out, err = wrap(self.root, "run", "demo", "--", *args)
            self.assertEqual(rc, 2, (args, out, err)); self.assertIn("拒绝", err)
        self.assertEqual(wrap(self.root, "run", "Bad_Name", "--", "open")[0], 2)
        self.assertFalse((self.root / "registry.json").exists())

    def test_non_open_on_unregistered_and_finish_unregistered(self):
        rc, _, err = wrap(self.root, "run", "demo", "--", "snapshot")
        self.assertEqual(rc, 2); self.assertIn("不是本包装创建", err)
        rc, _, err = wrap(self.root, "finish", "demo")
        self.assertEqual(rc, 3); self.assertIn("未登记", err)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipUnless(CLI, "需要 playwright-cli")
class RealBrowserTests(unittest.TestCase):
    def test_open_snapshot_screenshot_finish_leaves_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reg"; site = Path(tmp) / "site"; site.mkdir()
            (site / "index.html").write_text("<html><body><h1>iso-ok</h1><button id=b onclick=\"this.textContent='clicked'\">go</button></body></html>", "utf-8")
            handler = lambda *a, **k: Quiet(*a, directory=str(site), **k)
            with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
                try:
                    rc, out, err = wrap(root, "run", "t1", "--", "open", url, "--browser", "chrome", timeout=180)
                    self.assertEqual(rc, 0, out + err)
                    self.assertIn("iso-t1", json.loads(wrap(root, "list")[1]))
                    rc, snap, err = wrap(root, "run", "t1", "--", "snapshot")
                    self.assertEqual(rc, 0, err); self.assertIn("iso-ok", snap)
                    ref = next((l.split("[ref=")[1].split("]")[0] for l in snap.splitlines() if "button" in l and "[ref=" in l), None)
                    self.assertTrue(ref, snap)
                    self.assertEqual(wrap(root, "run", "t1", "--", "click", ref)[0], 0)
                    rc, snap2, _ = wrap(root, "run", "t1", "--", "snapshot")
                    self.assertIn("clicked", snap2)
                    rc, out, err = wrap(root, "run", "t1", "--", "screenshot", "--filename=shot.png")
                    self.assertEqual(rc, 0, out + err)
                    self.assertTrue(any(Path(root / "iso-t1" / "work").rglob("*.png")))
                    rc, out, _ = wrap(root, "run", "t1", "--", "open", url)
                    self.assertEqual(rc, 2)  # 同名会话不得重复 open
                finally:
                    rc, out, err = wrap(root, "finish", "t1")
                self.assertEqual(rc, 0, out + err)
                self.assertEqual(json.loads(wrap(root, "list")[1]), {})
                listing = subprocess.run([CLI, "list"], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
                self.assertNotIn("iso-t1", listing)
                httpd.shutdown()


class WithServerTests(unittest.TestCase):
    def test_server_started_ready_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "i.html").write_text("hello", "utf-8")
            import socket
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
            code = ("import urllib.request;print(urllib.request.urlopen('http://localhost:%d/i.html').read().decode())" % port)
            p = subprocess.run([sys.executable, "-I", str(SERVER), "--server", f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1 --directory "{tmp}"',
                                "--port", str(port), "--timeout", "20", "--", sys.executable, "-I", "-c", code],
                               capture_output=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr); self.assertIn(b"hello", p.stdout)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1)  # 服务已被收尾


if __name__ == "__main__":
    unittest.main()
