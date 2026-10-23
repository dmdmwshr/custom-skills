import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

SOURCE=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SOURCE))
import common
import client

class Safety(unittest.TestCase):
    def test_missing_token(self):
        with tempfile.TemporaryDirectory() as d, patch.object(common,"TOKEN",Path(d)/"missing"):
            with self.assertRaisesRegex(RuntimeError,"token_missing"):
                common.token()
    def test_excluded_before_auth_or_network(self):
        args=Mock(command="call",tool="PowerShell")
        with patch.object(client,"token") as read:
            with self.assertRaisesRegex(client.Failure,"excluded_tool"):
                client.run(args)
            read.assert_not_called()
    def test_image_externalized_and_secret_redacted(self):
        secret="sample-secret-for-unit-test-not-a-real-key"
        with tempfile.TemporaryDirectory() as d:
            result=client.sanitize({"content":[{"type":"image","data":base64.b64encode(b"image-test-bytes").decode(),"mimeType":"image/png"},{"type":"text","text":secret}],"authorization":secret},Path(d),secret)
            encoded=json.dumps(result)
            self.assertNotIn(secret,encoded)
            self.assertNotIn(base64.b64encode(b"image-test-bytes").decode(),encoded)
            image=result["content"][0]
            self.assertEqual(Path(image["path"]).read_bytes(),b"image-test-bytes")
            self.assertEqual(image["mimeType"],"image/png")
            self.assertEqual(result["content"][1]["text"],"[redacted]")
    def test_transport_unknown_no_retry(self):
        h=client.HTTP("fake-unit-key",timeout=2)
        h.opener=Mock()
        h.opener.open.side_effect=urllib.error.URLError("no response fake-unit-key")
        with self.assertRaisesRegex(client.Failure,"unknown_no_retry") as caught:
            h.request({"id":1,"method":"tools/call"})
        self.assertNotIn("fake-unit-key",str(caught.exception))
        self.assertEqual(h.opener.open.call_count,1)
        self.assertEqual(h.opener.open.call_args.kwargs["timeout"],2)
    def test_auth_required_before_tool_list(self):
        h=client.HTTP("fake-unit-key")
        h.request=Mock(return_value={"result":{"tools":[]}})
        with self.assertRaisesRegex(client.Failure,"authentication_not_enforced"):
            h.tools()
        self.assertEqual(h.request.call_count,1)
    def test_exact_allowed_tool_set(self):
        h=client.HTTP("fake-unit-key")
        h.request=Mock(side_effect=[{"http_error":401},{"result":{}},{},
            {"result":{"tools":[{"name":x} for x in sorted(client.EXPECTED)]}}])
        self.assertEqual({v["name"] for v in h.tools()},client.EXPECTED)
    def test_excluded_server_set_refused(self):
        h=client.HTTP("fake-unit-key")
        h.request=Mock(side_effect=[{"http_error":401},{"result":{}},{},
            {"result":{"tools":[{"name":"PowerShell"}]}}])
        with self.assertRaisesRegex(client.Failure,"tool_set_mismatch"):
            h.tools()
    def test_task_limited_interactive_no_trigger(self):
        info={"task":{"actions":[{"Execute":str(client.PYTHONW),"Arguments":'-B "'+str(client.ROOT/"scripts/server.py")+'"',"WorkingDirectory":str(client.PYTHONW.parents[2])}],
            "logon":"Interactive","level":"Limited","triggers":0,"userSid":"S-1-5-21-3995939195-1261990851-1351888376-1001","description":"[DEV_TASK_V1][project=codex-env-win][id=LIVE-01][category=LIVE]"}}
        client.valid_task(info)
        for field,value in [("logon","ServiceAccount"),("level","Highest"),("triggers",1),("userSid","other"),("description","unknown")]:
            invalid=json.loads(json.dumps(info))
            invalid["task"][field]=value
            with self.assertRaisesRegex(client.Failure,"contract_mismatch"):
                client.valid_task(invalid)
    def test_stop_never_kills_unknown_process(self):
        args=Mock(command="stop")
        with patch.object(client,"token",return_value="fake-unit-key"), patch.object(client,"inspect",return_value={"task":{},"listeners":[{}],"processes":[{}]}), patch.object(client,"valid_task"), patch.object(client,"owned",side_effect=client.Failure("unknown")), patch.object(client,"ps") as shell:
            with self.assertRaises(client.Failure):
                client.run(args)
            shell.assert_not_called()
if __name__=="__main__":
    unittest.main(verbosity=2)

