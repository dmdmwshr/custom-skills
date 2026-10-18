"""Offline boundary tests. No real app or upstream requests."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import wechat_mp as helper


def auth(active=1, cooldown=0):
    return {'logged_in': active > 0, 'login_state': {'status': 'success', 'qrcode': 'SECRET_QR'},
            'token_preview': 'SECRET_TOKEN', 'account_info': {'vid': 'SECRET_VID'},
            'pool_summary': {'active': active, 'cooldown': cooldown, 'invalid': 0, 'banned': 0, 'total': 1}}


class FakeAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Boundaries(unittest.TestCase):
    def test_auth_output_does_not_expose_secrets(self):
        self.assertNotIn('SECRET', json.dumps(helper.auth_summary(auth())))

    def test_doctor_filters_settings_and_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / 'app.exe'
            exe.write_bytes(b'fixture')
            install = root / 'installation.json'
            install.write_text(json.dumps({'exe': str(exe), 'exe_sha256': helper.sha256(exe)}))
            api = FakeAPI([{'api_key': 'SECRET'}, {'accounts': [{'nickname': 'demo', 'cookie': 'SECRET'}]},
                           {'subscriptions': []}, auth()])
            result = helper.doctor(api, install)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertTrue(result['installation']['recorded_hash_matches'])
            self.assertFalse(result['remote_articles_requested'])

    def test_cooldown_prevents_page_request(self):
        api = FakeAPI([auth(0, 1)])
        result, code = helper.page(api, 'publisher==', 20, 5)
        self.assertEqual(code, 2)
        self.assertFalse(result['upstream_requested'])
        self.assertEqual(api.calls, ['/api/auth/status'])

    def test_empty_after_cooldown_is_not_end(self):
        api = FakeAPI([auth(), {'articles': [], 'can_msg_continue': 0}, auth(0, 1)])
        result, code = helper.page(api, 'publisher==', 20, 5)
        self.assertEqual(code, 2)
        self.assertEqual(result['state'], 'blocked_or_error')
        self.assertFalse(result['listing_complete'])
        self.assertEqual(len(api.calls), 3)

    def test_healthy_empty_does_not_claim_coverage(self):
        result, code = helper.page(FakeAPI([auth(), {'articles': []}, auth()]), 'id', 0, 5)
        self.assertEqual(result['state'], 'empty_unconfirmed')
        self.assertEqual(code, 2)
        self.assertFalse(result['listing_complete'])

    def test_response_flag_does_not_override_nonempty_page(self):
        result, code = helper.page(FakeAPI([auth(), {'articles': [{'id': '1'}], 'can_msg_continue': 0}, auth()]), 'id==', 0, 5)
        self.assertEqual(code, 0)
        self.assertFalse(result['listing_complete'])

    def test_unknown_page_is_not_retried(self):
        api = FakeAPI([auth(), RuntimeError('timeout'), auth()])
        result, code = helper.page(api, 'id', 0, 5)
        self.assertEqual(code, 2)
        self.assertEqual(result['state'], 'request_unknown')
        self.assertEqual(len(api.calls), 3)

    def test_completed_with_failures_is_partial(self):
        raw = {'status': 'completed', 'total': 2, 'completed': 1, 'failed': 1,
               'results': [{'success': True}, {'success': False, 'error': 'unknown page'}]}
        result = helper.task(FakeAPI([raw]), 'batch_1')
        self.assertTrue(result['terminal'])
        self.assertTrue(result['counts_match_results'])
        self.assertFalse(result['all_requested_downloaded'])

    def test_existing_receipt_prevents_any_request(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'existing.json'
            receipt.write_text('original')
            argv = ['wechat_mp', 'page', '--fakeid', 'id', '--begin', '0', '--out', str(receipt)]
            with patch.object(sys, 'argv', argv), patch.object(helper, 'API') as api, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(helper.main(), 2)
                api.assert_not_called()
            self.assertEqual(receipt.read_text(), 'original')

    def test_file_checks_detect_missing_remote_and_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'image.png').write_bytes(b'image')
            (root / 'body.txt').write_text('content')
            (root / 'body.html').write_text('<img src="image.png" data-src="https://unused.invalid/a"><img src="missing.png"><img src="https://example.invalid/a"><img src="../escape.png"><img srcset="a.png 2x">', encoding='utf-8')
            result = helper.verify(root)
            self.assertEqual(len(result['missing_images']), 1)
            self.assertEqual(len(result['remote_images']), 1)
            self.assertEqual(len(result['outside_references']), 1)
            self.assertEqual(len(result['uninspected_srcsets']), 1)
            self.assertEqual(result['html_count'], 1)
            self.assertFalse(result['local_image_references_ok'])
            self.assertFalse(result['all_articles_complete'])

    def test_non_loopback_and_credential_urls_rejected(self):
        for url in ['http://example.com', 'http://user:pass@localhost:5200', 'http://127.0.0.1:5200/other']:
            with self.assertRaises(ValueError):
                helper.API(url)

    def test_generated_source_html_is_preserved_but_not_counted_as_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'article.html').write_text('<p>Offline body</p>')
            (root / 'article_raw.html').write_text('<img src="https://example.invalid/source.png">')
            (root / 'metadata.json').write_text(json.dumps({'title': 'article'}))
            result = helper.verify(root)
            self.assertEqual(result['html_count'], 2)
            self.assertEqual(result['readable_html_count'], 1)
            self.assertEqual(result['source_html_excluded_from_offline_check'], ['article_raw.html'])
            self.assertTrue(result['local_image_references_ok'])
            self.assertEqual(len(result['files']), 3)


if __name__ == '__main__':
    unittest.main()
