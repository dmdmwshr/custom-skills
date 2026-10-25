"""Verify host environment routing without changing the host registry."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

PATH = Path(__file__).resolve().parents[1] / "scripts/video_intake.py"
spec = importlib.util.spec_from_file_location("video_environment_unit", PATH)
v = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v
spec.loader.exec_module(v)

class ModelEnvironmentTests(unittest.TestCase):
    def test_process_environment_reload_and_explicit_model_priority(self):
        environment = {"AI_MODEL_STRONG":"first-strong", "AI_MODEL_LIGHT":"first-light"}
        with patch.object(v, "os", SimpleNamespace(name="posix", environ=environment, path=v.os.path)):
            config = {"default_request":{"model_role":"strong", "model_name":"legacy"}}
            self.assertEqual(v.resolve_model(config, None), "first-strong")
            environment["AI_MODEL_STRONG"] = "next-strong"
            self.assertEqual(v.resolve_model(config, None), "next-strong")
            self.assertEqual(v.resolve_model(config, "explicit-model"), "explicit-model")

    def test_partial_and_invalid_environment_are_rejected(self):
        for environment in ({"AI_MODEL_STRONG":"s"}, {"AI_MODEL_STRONG":"s", "AI_MODEL_LIGHT":"light"}):
            with self.subTest(environment=environment), patch.object(v, "os", SimpleNamespace(name="posix", environ=environment, path=v.os.path)):
                with self.assertRaises(v.IntakeError):
                    v.resolve_model({"default_request":{"model_role":"strong"}}, None)

if __name__ == "__main__":
    unittest.main()
