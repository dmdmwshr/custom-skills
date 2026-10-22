"""Isolated state-machine checks; no browser, credentials, or CLI is started."""
import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import browser_cli as cli


class ReadOnlyRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.profile = self.root / 'profile'
        self.owner = '00000000-0000-4000-8000-000000000001'
        self.args = SimpleNamespace(command='snapshot', arguments=[], browser='chrome',
            session='read-retry', conversation=self.owner, project='fixture', timeout=2)
        self.path = cli.lifecycle.receipt_path(self.work, 'chrome', 'read-retry')
        self.receipt = cli.lifecycle.fresh({'schema':'BrowserGroupIdentityV1',
            'computer_name':'fixture', 'environment':'Windows', 'project_name':'fixture',
            'session_name':'read-retry', 'browser_name':'chrome', 'title':'fixture'},
            self.work, self.profile, self.owner)
        self.receipt.update(state='active', group_id=7, connection_id='fixture-connection',
                            owned_tab_ids=[11,12], browser_epoch='fixture-epoch')
        self.native = {'group_id':7, 'connection_id':'fixture-connection', 'native_tab_ids':[11,12]}
        self.settings = {'expected_cli_version':'0.1.22'}
        self.outcomes = []
        self.metadata_values = []
        self.calls = []
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        cli.lifecycle.save(self.path, self.receipt)

    def read(self):
        return cli.lifecycle.read(self.path)

    def fake_cli(self, argv, env, timeout, secrets):
        command = argv[0]
        self.calls.append(command)
        if command == 'run-code':
            value = self.metadata_values.pop(0) if self.metadata_values else self.native
            if isinstance(value, Exception):
                raise value
            return 0, json.dumps(value)
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def call(self, command=None, arguments=None, epochs=None):
        self.args.command = command or self.args.command
        self.args.arguments = [] if arguments is None else arguments
        epoch = dict(side_effect=epochs) if epochs else dict(return_value='fixture-epoch')
        with (patch.object(cli.Path, 'cwd', return_value=self.work),
              patch.object(cli, 'profile_path', return_value=self.profile),
              patch.object(cli, 'group_code', return_value='fixture metadata'),
              patch.object(cli, 'group_result', side_effect=json.loads),
              patch.object(cli, 'prepare', side_effect=lambda settings,root,browser,session,command,args,cwd,physical:([command,*args],{},[])),
              patch.object(cli, 'run_cli', side_effect=self.fake_cli),
              patch.object(cli.browser_audit, 'register'),
              patch.object(cli.browser_audit, 'browser_epoch', **epoch),
              contextlib.redirect_stdout(io.StringIO())):
            return cli.managed_main(self.args, self.root, self.settings)

    def test_snapshot_failure_then_fresh_identity_retry_succeeds(self):
        self.outcomes = [(1,'fixture read error'),(0,'fresh snapshot')]
        self.assertEqual(self.call(),1)
        failed = self.read()
        self.assertEqual(failed['state'],'active')
        self.assertNotIn('last_failure',failed)
        self.assertEqual(failed['last_read_failure']['classification'],'readonly_snapshot_failed')
        self.assertEqual(self.call(),0)
        self.assertEqual(self.read()['state'],'active')
        self.assertEqual(self.read()['last_read_failure'],failed['last_read_failure'])
        self.assertEqual(self.calls,['run-code','snapshot','run-code']*2)

    def test_active_readback_failure_then_retry_succeeds(self):
        self.outcomes = [(1,'fixture read error'),(0,'fresh snapshot')]
        self.assertEqual(self.call('readback'),1)
        self.assertEqual(self.read()['state'],'active')
        self.assertEqual(self.call('readback'),0)
        self.assertEqual(self.read()['state'],'active')

    def test_existing_unknown_or_readback_required_never_unlocks(self):
        original = {'classification':'original_business_unknown','at':'original-time'}
        for state in ['unknown','readback_required']:
            for code in [0,1]:
                with self.subTest(state=state,code=code):
                    self.receipt.update(state=state,last_failure=copy.deepcopy(original))
                    self.save()
                    self.outcomes = [(code,'fixture read result')]
                    if code:
                        with self.assertRaises(ValueError):self.call('readback')
                    else:
                        self.assertEqual(self.call('readback'),0)
                    self.assertEqual(self.read()['state'],state)
                    self.assertEqual(self.read()['last_failure'],original)

    def test_resolved_historical_failure_does_not_block_read_retry(self):
        original = {'classification':'rejected_reference','resolution':'fresh_snapshot_no_replay',
                    'reconciled_at':'old-proof-time'}
        self.receipt['last_failure'] = original.copy()
        self.save()
        self.outcomes = [(1,'fixture read error')]
        self.assertEqual(self.call(),1)
        self.assertEqual(self.read()['state'],'active')
        self.assertEqual(self.read()['last_failure'],original)

    def test_unresolved_business_failure_cannot_use_read_exemption(self):
        self.receipt['last_failure'] = {'classification':'original_business_unknown'}
        self.save()
        with self.assertRaises(ValueError):self.call()
        self.assertEqual(self.read()['state'],'unknown')
        self.assertNotIn('snapshot',self.calls)

    def test_write_failure_still_marks_unknown(self):
        self.outcomes = [(1,'fixture input error')]
        self.assertEqual(self.call('fill',['e1','value']),1)
        self.assertEqual(self.read()['state'],'unknown')
        self.assertEqual(self.read()['last_failure']['classification'],'unclassified_result_unknown')

    def test_changed_group_connection_or_members_rejects_failed_read_retry(self):
        for change in [{'group_id':8},{'connection_id':'other'},{'native_tab_ids':[11]}]:
            with self.subTest(change=change):
                self.save()
                self.metadata_values = [self.native,{**self.native,**change}]
                self.outcomes = [(1,'fixture read error')]
                with self.assertRaises(ValueError):self.call()
                self.assertEqual(self.read()['state'],'unknown')
                self.assertNotIn('last_read_failure',self.read())

    def test_changed_browser_epoch_does_not_preserve_active(self):
        self.outcomes = [(1,'fixture read error')]
        with self.assertRaises(ValueError):self.call(epochs=['fixture-epoch','other-epoch'])
        self.assertEqual(self.read()['state'],'unknown')

    def test_snapshot_timeout_preserves_active_only_after_identity_recheck(self):
        self.outcomes = [subprocess.TimeoutExpired(['fixture'],2)]
        with self.assertRaises(subprocess.TimeoutExpired) as result:self.call()
        self.assertTrue(result.exception.read_only_state_preserved)
        self.assertEqual(self.read()['state'],'active')
        self.assertEqual(self.read()['last_read_failure']['classification'],'readonly_snapshot_timeout')
        self.assertEqual(self.calls,['run-code','snapshot','run-code'])

    def test_unknown_read_timeout_preserves_original_business_failure(self):
        original = {'classification':'original_business_unknown','at':'original-time'}
        self.receipt.update(state='unknown',last_failure=original.copy())
        self.save()
        self.outcomes = [subprocess.TimeoutExpired(['fixture'],2)]
        with self.assertRaises(subprocess.TimeoutExpired):self.call('readback')
        self.assertEqual(self.read()['state'],'unknown')
        self.assertEqual(self.read()['last_failure'],original)

    def test_snapshot_with_extra_arguments_has_no_read_exemption(self):
        self.outcomes = [(1,'fixture read error')]
        self.assertEqual(self.call('snapshot',['--unexpected']),1)
        self.assertEqual(self.read()['state'],'unknown')

    def test_metadata_failure_keeps_original_unknown_protection(self):
        self.metadata_values = [self.native,ValueError('fixture identity unavailable')]
        self.outcomes = [(1,'fixture read error')]
        with self.assertRaises(ValueError):self.call()
        self.assertEqual(self.read()['state'],'unknown')
        self.assertNotIn('last_read_failure',self.read())


if __name__ == '__main__':
    unittest.main()
