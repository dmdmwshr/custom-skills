"""Offline regression tests; synthetic data only, no FlClash changes."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import flclash_state as state


class DiagnosticTests(unittest.TestCase):
    def test_target_rejects_url_port_ip_and_query(self):
        for value in ("https://example.com", "example.com:443", "127.0.0.1", "example.com?a=b", "bad..example.com", "-bad.example.com"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                state.target_hostname(value)
        self.assertEqual(state.target_hostname("EXAMPLE.COM."), "example.com")

    def test_domain_suffix_respects_label_boundary(self):
        rules = [{"type": "DomainSuffix", "payload": "example.com", "proxy": "DIRECT"}]
        self.assertEqual(state.matching_domain_rules(rules, "notexample.com"), [])
        self.assertEqual(state.matching_domain_rules(rules, "site.example.com")[0]["route_category"], "direct")

    def test_dns_redaction_and_comparison_fingerprint(self):
        a = state.address_summary(["203.0.113.2", "203.0.113.1", "203.0.113.1", "secret.invalid"])
        b = state.address_summary(["203.0.113.1", "203.0.113.2"])
        self.assertEqual(a, b)
        self.assertEqual(a["answer_count"], 2)
        self.assertNotIn("203.0.113", json.dumps(a))
        self.assertFalse(a["contains_fake_ip_range"])
        self.assertTrue(state.address_summary(["198.18.1.2", "2001:db8::1"])["contains_fake_ip_range"])
        self.assertIsNone(state.address_summary([])["answer_fingerprint"])

    def test_live_tun_is_not_replaced_by_generated_value(self):
        replies = [({"mode": "rule", "tun": {"enable": True, "auto-route": True, "stack": "gvisor"}}, True), ({"version": "1.10.0"}, True)]
        with patch.object(state, "controller_json", side_effect=replies):
            result = state.runtime_summary("tun:\n  enable: false\n", {"external-controller": "127.0.0.1:9090"})
        self.assertTrue(result["tun"]["enable"])
        self.assertEqual(result["probe_warning"], "noproxy_does_not_bypass_tun")

    def test_controller_addresses_exclude_credentials_and_remote_hosts(self):
        for value in ("https://remote.example:443", "http://token@127.0.0.1:9090", "http://127.0.0.1:9090/path", "http://127.0.0.1:9090?token=x"):
            self.assertIsNone(state.controller_base_url(value))
        self.assertEqual(state.controller_base_url("127.0.0.1:9090"), "http://127.0.0.1:9090")

    def test_target_rule_candidates_do_not_claim_actual_route(self):
        replies = [
            ({"Status": 0, "Answer": [{"type": 1, "data": "203.0.113.6"}]}, True),
            ({"Status": 0}, True),
            ({"rules": [{"type": "DomainSuffix", "payload": "example.com", "proxy": "DIRECT"}]}, True),
            ({"connections": []}, True),
        ]
        with patch.object(state, "controller_json", side_effect=replies):
            result = state.target_diagnostics("secret: synthetic-only", {"external-controller": "127.0.0.1:9090"}, "site.example.com")
        self.assertEqual(result["actual_route_evidence"], "not_observed")
        self.assertTrue(result["domain_rule_matches_are_not_actual_route_proof"])
        self.assertNotIn("synthetic-only", json.dumps(result))
        self.assertNotIn("203.0.113.6", json.dumps(result))

    def test_v099_preferences_keep_override_keys_separate_from_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefs.json"
            path.write_text(json.dumps({"flutter.config": json.dumps({"currentProfileId": 1, "overrideDns": False, "patchClashConfig": {"dns-override-keys": ["nameserver-policy"], "dns": {"nameserver": ["https://secret.invalid/?token=x"]}}})}), encoding="utf-8")
            with patch.object(state, "PREFS_PATH", path):
                result, profile_id = state.parse_preferences()
            self.assertEqual(profile_id, 1)
            self.assertFalse(result["dns_override_enabled"])
            self.assertEqual(result["dns_override_keys"], ["nameserver-policy"])
            self.assertNotIn("secret.invalid", json.dumps(result))

    def test_schema_10_metadata_excludes_profile_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("PRAGMA user_version=10; CREATE TABLE profiles(id INTEGER, label TEXT, url TEXT, overwrite_type TEXT, selected_map TEXT); CREATE TABLE clash_providers(id INTEGER); CREATE TABLE custom_proxies(id INTEGER);")
                db.execute("INSERT INTO profiles VALUES(1,?,?,?,?)", ("Synthetic", "https://private.invalid/?token=secret", "standard", json.dumps({"代理出口": "Meifu"})))
                db.commit()
            with patch.object(state, "DATABASE_PATH", path):
                result = state.database_summary(1)
            self.assertEqual(result["user_version"], 10)
            self.assertIn("clash_providers", result["table_counts"])
            self.assertNotIn("private.invalid", json.dumps(result))


class ControllerTransportTests(unittest.TestCase):
    def test_local_controller_bypasses_proxy_and_rejects_redirect(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leak")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"version":"test"}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                value, ok = state.controller_json(base, "/version", "synthetic-token")
                self.assertTrue(ok)
                self.assertEqual(value["version"], "test")
                self.assertEqual(state.controller_json(base, "/redirect", "synthetic-token"), (None, False))
            self.assertEqual([p for p, _ in seen], ["/version", "/redirect"])
            self.assertTrue(all(auth == "Bearer synthetic-token" for _, auth in seen))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
