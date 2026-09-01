#!/usr/bin/env python3
"""使用临时模拟服务器验证 comfyui_api.py，不访问真实 ComfyUI。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comfyui_api import main


class FakeComfyHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, object]] = []
    submit_response: object = {
        "prompt_id": "submitted-1",
        "number": 3,
        "node_errors": {},
    }

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.__class__.requests.append(("GET", path, None))
        payloads = {
            "/system_stats": {
                "system": {
                    "comfyui_version": "test-1.0",
                    "python_version": "3.12",
                    "os": "Windows",
                },
                "devices": [
                    {
                        "name": "Mock GPU",
                        "type": "cuda",
                        "vram_total": 24000,
                        "vram_free": 12000,
                    }
                ],
            },
            "/queue": {
                "queue_running": [[1, "running-1", {}, {}]],
                "queue_pending": [[2, "pending-1", {}, {}]],
            },
            "/object_info": {
                "CheckpointLoaderSimple": {
                    "display_name": "Checkpoint Loader",
                    "category": "loaders",
                    "input": {"required": {"ckpt_name": ["STRING"]}},
                },
                "KSampler": {
                    "display_name": "KSampler",
                    "category": "sampling",
                    "input": {"required": {"seed": ["INT"]}},
                },
            },
            "/models": ["checkpoints", "loras"],
            "/models/checkpoints": ["base.safetensors", "video.safetensors"],
            "/workflow_templates": {"core": [{"name": "txt2img"}]},
            "/history": {
                "job-1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                }
            },
        }
        if path.startswith("/history/"):
            prompt_id = unquote(path.removeprefix("/history/"))
            history_payloads = {
                "job-1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "completed-1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "missing-output": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "unverified-1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "invalid-probe": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "probe-only-directory": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"9": {}},
                },
                "artifact-only": {},
                "failed-1": {
                    "status": {"status_str": "error", "completed": False},
                    "outputs": {},
                },
            }
            self.send_json(200, {prompt_id: history_payloads.get(prompt_id, {})})
            return
        if path in payloads:
            self.send_json(200, payloads[path])
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        self.__class__.requests.append(("POST", path, body))
        if path == "/prompt":
            self.send_json(200, self.__class__.submit_response)
        elif path in {"/interrupt", "/free", "/queue"}:
            self.send_json(200, {})
        else:
            self.send_json(404, {"error": "not found"})


class ComfyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FakeComfyHandler.requests = []
        FakeComfyHandler.submit_response = {
            "prompt_id": "submitted-1",
            "number": 3,
            "node_errors": {},
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfyHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def invoke(self, *args: str) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = main(["--url", self.url, *args])
        return code, json.loads(output.getvalue())

    def make_workflow(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "workflow.json"
        path.write_text(
            json.dumps(
                {
                    "1": {
                        "class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": "base.safetensors"},
                    },
                    "2": {
                        "class_type": "KSampler",
                        "inputs": {"model": ["1", 0], "seed": 1},
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def make_output(self, name: str, *, probe: dict[str, object] | None = None) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_bytes(b"mock media")
        if probe is not None:
            Path(str(path) + ".probe.json").write_text(
                json.dumps(probe),
                encoding="utf-8",
            )
        return path

    def test_read_operations(self) -> None:
        code, response = self.invoke("health")
        self.assertEqual(code, 0)
        self.assertTrue(response["result"]["ready"])

        code, response = self.invoke("status")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["queue"]["pending_count"], 1)

        code, response = self.invoke("nodes", "--query", "sampler")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["count"], 1)

        code, response = self.invoke("models", "--folder", "checkpoints")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["count"], 2)

        code, response = self.invoke("templates")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["count"], 1)

        code, response = self.invoke("history", "--prompt-id", "job-1")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["items"][0]["prompt_id"], "job-1")

    def test_workflow_preflight_and_submit(self) -> None:
        workflow = self.make_workflow()
        code, response = self.invoke("workflow-check", "--workflow", str(workflow))
        self.assertEqual(code, 0)
        self.assertTrue(response["result"]["valid"])

        code, response = self.invoke("preflight", "--workflow", str(workflow))
        self.assertEqual(code, 0)
        self.assertTrue(response["result"]["ready_to_submit"])

        code, response = self.invoke("submit", "--workflow", str(workflow), "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["response"]["prompt_id"], "submitted-1")
        self.assertIn(("POST", "/prompt", {"prompt": json.loads(workflow.read_text())}), FakeComfyHandler.requests)

    def test_reconcile_multiple_prompt_ids_and_local_outputs(self) -> None:
        FakeComfyHandler.requests = []
        valid_probe = {
            "media_type": "mp4",
            "decodable": True,
            "frame_count": 3,
            "width": 1280,
            "height": 720,
            "fps": 24,
        }
        completed = self.make_output("completed.mp4", probe=valid_probe)
        unverified = self.make_output("unverified.mp4")
        artifact_only = self.make_output("artifact-only.mp4", probe=valid_probe)
        invalid = self.make_output(
            "invalid.mp4",
            probe={"media_type": "mp4", "decodable": False, "frame_count": 0},
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        missing = Path(directory.name) / "missing.mp4"
        pending_missing = Path(directory.name) / "pending.mp4"
        probe_only_directory = Path(directory.name) / "probe-only"
        probe_only_directory.mkdir()
        (probe_only_directory / ".probe.json").write_text(
            json.dumps(valid_probe),
            encoding="utf-8",
        )

        code, response = self.invoke(
            "reconcile",
            "--prompt-id",
            "completed-1",
            "--output-path",
            str(completed),
            "--prompt-id",
            "missing-output",
            "--output-path",
            str(missing),
            "--prompt-id",
            "unverified-1",
            "--output-path",
            str(unverified),
            "--prompt-id",
            "artifact-only",
            "--output-path",
            str(artifact_only),
            "--prompt-id",
            "invalid-probe",
            "--output-path",
            str(invalid),
            "--prompt-id",
            "pending-1",
            "--output-path",
            str(pending_missing),
            "--prompt-id",
            "probe-only-directory",
            "--output-path",
            str(probe_only_directory),
        )
        self.assertEqual(code, 0)
        items = {item["prompt_id"]: item for item in response["result"]["items"]}
        self.assertEqual(items["completed-1"]["status"], "completed")
        self.assertEqual(items["missing-output"]["status"], "race")
        self.assertEqual(items["unverified-1"]["status"], "unverified")
        self.assertEqual(items["artifact-only"]["status"], "race")
        self.assertEqual(items["invalid-probe"]["status"], "race")
        self.assertEqual(items["pending-1"]["status"], "unknown")
        self.assertEqual(items["probe-only-directory"]["status"], "race")
        self.assertEqual(response["result"]["counts"]["completed"], 1)
        self.assertEqual(response["result"]["counts"]["unverified"], 1)
        self.assertEqual(response["result"]["counts"]["race"], 4)
        self.assertEqual(response["result"]["counts"]["unknown"], 1)

        paths = [entry[1] for entry in FakeComfyHandler.requests]
        self.assertEqual(paths.count("/queue"), 1)
        self.assertNotIn("/history", paths)
        for prompt_id in (
            "completed-1",
            "missing-output",
            "unverified-1",
            "artifact-only",
            "invalid-probe",
            "pending-1",
            "probe-only-directory",
        ):
            self.assertIn(f"/history/{prompt_id}", paths)

    def test_submit_requires_prompt_id_and_empty_node_errors(self) -> None:
        workflow = self.make_workflow()
        bad_responses = (
            {"number": 3, "node_errors": {}},
            {"prompt_id": "bad-1", "node_errors": {"2": {"errors": ["bad"]}}},
        )
        for bad_response in bad_responses:
            FakeComfyHandler.submit_response = bad_response
            code, response = self.invoke("submit", "--workflow", str(workflow), "--yes")
            self.assertEqual(code, 2)
            self.assertFalse(response["ok"])
            self.assertIn("不会自动重试", response["error"])

        FakeComfyHandler.submit_response = {
            "result": {
                "response": {"prompt_id": "nested-1", "node_errors": {}},
            },
        }
        code, response = self.invoke("submit", "--workflow", str(workflow), "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["prompt_id"], "nested-1")
        self.assertEqual(response["result"]["node_errors"], {})
        FakeComfyHandler.submit_response = {
            "prompt_id": "submitted-1",
            "number": 3,
            "node_errors": {},
        }

    def test_write_guards_and_loopback_boundary(self) -> None:
        workflow = self.make_workflow()
        code, response = self.invoke("submit", "--workflow", str(workflow))
        self.assertEqual(code, 2)
        self.assertFalse(response["ok"])
        self.assertIn("--yes", response["error"])

        code, response = self.invoke("cancel-running", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(response["result"]["interrupted_running_job"])

        code, response = self.invoke("cancel-pending", "--prompt-id", "pending-1", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(response["result"]["cancelled_pending_prompt_id"], "pending-1")

        code, response = self.invoke("free-vram", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(response["result"]["freed_vram"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = main(["--url", "http://192.168.1.8:8188", "health"])
        response = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertIn("回环地址", response["error"])


if __name__ == "__main__":
    unittest.main()
