import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
ENTRY = shutil.which("xfrhtx")


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def invoke(options, arguments):
    code = "& " + quoted(ROOT / "scripts" / "run.ps1")
    for key, value in options.items():
        code += " -" + key + " " + quoted(value)
    code += " -ToolArguments @(" + ",".join(quoted(a) for a in arguments) + ")"
    code += "; exit $LASTEXITCODE"
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    return subprocess.run([str(PS), "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)


@unittest.skipUnless(os.name == "nt", "Windows entry resolver")
class RunnerTests(unittest.TestCase):
    @unittest.skipUnless(ENTRY, "Requires installed reader")
    def test_plain_version_passthrough(self):
        result = invoke({"EntryPoint": ENTRY}, ["--version"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout.strip(), r"^xfrhtx \d+\.\d+\.\d+$")

    @unittest.skipUnless(ENTRY, "Requires installed reader")
    def test_json_error_and_exit_code_are_preserved(self):
        result = invoke({"EntryPoint": ENTRY}, ["history", "--json"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid_arguments")

    def test_explicit_empty_install_does_not_use_another_install(self):
        with tempfile.TemporaryDirectory() as folder:
            result = invoke({"InstallRoot": folder}, ["status", "--json"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "tool_unavailable")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_gui_client_is_not_launched(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = Path(folder) / "XFRHTX.exe"
            entry.touch()
            (Path(folder) / "jmtoolkit.dll").touch()
            result = invoke({"EntryPoint": entry}, ["status", "--json"])
            self.assertEqual(result.returncode, 2)
            self.assertIn("GUI client", json.loads(result.stdout)["error"]["message"])


if __name__ == "__main__":
    unittest.main()
