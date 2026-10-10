import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import browser_cli as cli


class BrowserLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'chrome.token').write_text('chrome-test-secret', encoding='utf-8')
        (self.root / 'edge.token').write_text('edge-test-secret', encoding='utf-8')
        self.settings = {'node_executable': 'C:/space dir/node.exe', 'cli_entry': 'C:/中文/cli.js',
                         'browsers': {'chrome': {'channel': 'chrome', 'profile_directory': 'Default', 'token_file': 'chrome.token'},
                                      'edge': {'channel': 'msedge', 'profile_directory': 'Profile 1', 'token_file': 'edge.token'}}}

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, browser='chrome', session='case-1', command='connect', args=None):
        return cli.prepare(self.settings, self.root, browser, session, command, args or [], self.root)

    def test_browser_credentials_and_channels_do_not_cross(self):
        for browser, channel, secret in [('chrome', 'chrome', 'chrome-test-secret'), ('edge', 'msedge', 'edge-test-secret')]:
            argv, env, _ = self.prepare(browser)
            self.assertEqual(argv[-1], '--extension=' + channel)
            self.assertEqual(env['PLAYWRIGHT_MCP_EXTENSION_TOKEN'], secret)
            self.assertIn('-s=' + browser + '-case-1', argv)
            self.assertFalse(any(secret in a for a in argv))

    def test_environment_is_local_and_conflicting_overrides_removed(self):
        with patch.dict(os.environ, {'PLAYWRIGHT_MCP_EXTENSION_TOKEN': 'old', 'PLAYWRIGHT_MCP_CDP_ENDPOINT': 'bad', 'DEBUG': '*'}):
            _, env, _ = self.prepare()
            self.assertEqual(os.environ['PLAYWRIGHT_MCP_EXTENSION_TOKEN'], 'old')
            self.assertNotIn('PLAYWRIGHT_MCP_CDP_ENDPOINT', env)
            self.assertNotIn('DEBUG', env)

    def test_arguments_preserve_spaces_and_chinese_without_shell(self):
        argv, _, _ = self.prepare(command='fill', args=['e4', '中文 text & $value'])
        self.assertEqual(argv[-2:], ['e4', '中文 text & $value'])

    def test_session_and_connection_overrides_rejected(self):
        for name in ['../escape', 'two names', '', 'x' * 49]:
            with self.assertRaises(ValueError):
                self.prepare(session=name)
        for argument in ['-s=other', '--session=other', '--cdp=http://bad', '--config=bad']:
            with self.assertRaises(ValueError):
                self.prepare(command='snapshot', args=[argument])

    def test_dangerous_browser_wide_cleanup_rejected(self):
        for command in ['close-all', 'kill-all', 'delete-data', 'close', 'open']:
            with self.assertRaises(ValueError):
                self.prepare(command=command)
        argv, _, _ = self.prepare(command='disconnect')
        self.assertEqual(argv[-1], 'detach')

    def test_redacts_both_tokens_and_connection_urls(self):
        text = 'chrome-test-secret edge-test-secret https://local/?token=unknown_secret)'
        clean = cli.redact(text, ['chrome-test-secret', 'edge-test-secret'])
        self.assertNotIn('secret', clean)
        self.assertTrue(clean.endswith('[REDACTED])'))

    def test_group_receipt_requires_actual_structured_native_result(self):
        value = {'schema': 'BrowserGroupIdentityV1', 'naming_verified': True, 'group_id': 17}
        output = '### Result\r\n' + json.dumps(value) + '\r\n### Ran Playwright code\r\n...'
        self.assertEqual(cli.group_result(output), value)
        for output in ['### Error\nTimeout', '### Result\n{"schema":"other"}']:
            with self.assertRaises(ValueError):
                cli.group_result(output)

    def configure_seth(self):
        executable = self.root / 'pwsh.exe'
        executable.touch()
        self.settings['vault_pwsh_executable'] = str(executable)
        self.settings['profiles'] = {'chrome': {'seth': {
            'profile_directory': 'Profile 2', 'secret_name': 'extension-seth',
            'credential_username': 'Seth'}}}

    def test_named_profile_keeps_default_and_uses_only_bound_vault_credential(self):
        self.configure_seth()
        selected = cli.select_profile(self.settings, 'chrome', 'seth')
        self.assertEqual(self.settings['browsers']['chrome']['profile_directory'], 'Default')
        with patch.object(cli.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='seth-extension-secret', stderr='')) as run:
            argv, env, secrets = cli.prepare(selected, self.root, 'chrome', 'case-seth',
                                             'connect', [], self.root)
        self.assertEqual(env['PLAYWRIGHT_MCP_PROFILE_DIR_NAME'], 'Profile 2')
        self.assertEqual(env['PLAYWRIGHT_MCP_EXTENSION_TOKEN'], 'seth-extension-secret')
        self.assertFalse(any('seth-extension-secret' in a for a in argv + run.call_args.args[0]))
        self.assertIn('chrome-test-secret', secrets)
        self.assertNotIn('secret', cli.redact('seth-extension-secret chrome-test-secret', secrets))

    def test_unknown_or_cross_browser_profile_cannot_fall_back_to_default(self):
        self.configure_seth()
        for browser, name in [('chrome', 'missing'), ('edge', 'seth'), ('chrome', '../seth')]:
            with self.assertRaises(ValueError):
                cli.select_profile(self.settings, browser, name)
        self.settings['profiles']['chrome']['seth']['channel'] = 'msedge'
        with self.assertRaises(ValueError):
            cli.select_profile(self.settings, 'chrome', 'seth')

    def test_vault_failure_is_redacted_and_never_launches_browser(self):
        self.configure_seth()
        selected = cli.select_profile(self.settings, 'chrome', 'seth')
        with patch.object(cli.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='unexpected-secret', stderr='unexpected-secret')) as run:
            with self.assertRaises(ValueError) as error:
                cli.prepare(selected, self.root, 'chrome', 'case-seth', 'connect', [], self.root)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('unexpected-secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
