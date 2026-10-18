import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import browser_cli as cli
import execution_state as lifecycle
import reference_recovery as recovery


ERROR = '### Error\nError: Ref e47 not found in the current page snapshot. Try capturing new snapshot.\n'
OWNER = '11111111-1111-1111-1111-111111111111'


class RecoveryTests(unittest.TestCase):
    def test_exact_pre_input_error_only(self):
        self.assertIsNotNone(recovery.rejected_reference('click', ['e47'], ERROR, '0.1.22'))
        for command, args, output, version in [
            ('fill', ['e47'], ERROR, '0.1.22'), ('click', ['e48'], ERROR, '0.1.22'),
            ('click', ['e47'], ERROR + 'later timeout', '0.1.22'),
            ('click', ['e47'], ERROR, '0.1.23'), ('click', [None], ERROR, '0.1.22')]:
            self.assertIsNone(recovery.rejected_reference(command, args, output, version))

    def fixture(self, directory, state='readback_required'):
        root = Path(directory)
        receipt = lifecycle.fresh({'schema':'BrowserGroupIdentityV1', 'computer_name':'test',
            'environment':'Windows', 'project_name':'test', 'session_name':'case', 'browser_name':'chrome', 'title':'test'},
            root, root / 'profile', OWNER)
        receipt.update(state=state, group_id=7, connection_id=1, owned_tab_ids=[11],
                       browser_epoch={'pid':1, 'start':'test'})
        receipt['last_failure'] = recovery.rejected_reference('click', ['e47'], ERROR, '0.1.22')
        path = lifecycle.receipt_path(root, 'chrome', 'case')
        lifecycle.save(path, receipt)
        return root, path, receipt

    def invoke(self, root, command, snapshot_code=0, epoch=None, members=None):
        args = SimpleNamespace(browser='chrome', session='case', conversation=OWNER, command=command,
                               arguments=[], timeout=2)
        calls = []
        def run(argv, *_):
            calls.append(argv[0])
            if argv[0] == 'snapshot': return snapshot_code, '### Snapshot\npage.yml'
            if argv[0] == 'click': return 1, ERROR
            schema = 'NativeBrowserInventoryV1' if 'captureNativeBrowserInventory' in argv[-1] else 'BrowserGroupIdentityV1'
            return 0, '### Result\n' + json.dumps({'schema':schema, 'group_id':7,
                         'connection_id':1, 'native_tab_ids':members or [11]})
        with contextlib.ExitStack() as stack:
            for target, value in [(cli.Path, root)]:
                stack.enter_context(patch.object(target, 'cwd', return_value=value))
            stack.enter_context(patch.object(cli, 'profile_path', return_value=root / 'profile'))
            stack.enter_context(patch.object(cli, 'prepare', side_effect=lambda s,r,b,n,c,a,w,p: ([c,*a],{},[])))
            stack.enter_context(patch.object(cli, 'run_cli', side_effect=run))
            stack.enter_context(patch.object(cli, 'saved_groups', return_value={}))
            for name, value in [('register',None),('registry',{}),('classify',{}),('complete_verified_own_history',[]),
                                ('browser_epoch', epoch or {'pid':1,'start':'test'})]:
                stack.enter_context(patch.object(cli.browser_audit, name, return_value=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = cli.managed_main(args, root, {'expected_cli_version':'0.1.22'})
        return result, calls

    def test_recovery_reads_fresh_state_and_never_replays_click(self):
        with tempfile.TemporaryDirectory() as folder:
            root, path, _ = self.fixture(folder)
            code, calls = self.invoke(root, 'recover-reference')
            current = lifecycle.read(path)
            self.assertEqual(code, 0)
            self.assertIn('snapshot', calls)
            self.assertNotIn('click', calls)
            self.assertEqual(current['state'], 'active')
            self.assertTrue(Path(current['last_failure']['baseline_receipt']).is_file())

    def test_unknown_readback_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as folder:
            root, path, _ = self.fixture(folder, 'unknown')
            self.invoke(root, 'readback')
            self.assertEqual(lifecycle.read(path)['state'], 'unknown')
            with self.assertRaisesRegex(ValueError, 'requires readback'):
                self.invoke(root, 'click')

    def test_failed_snapshot_or_changed_identity_does_not_unlock(self):
        for options in [{'snapshot_code':1}, {'epoch':{'pid':2}}, {'members':[11,12]}]:
            with tempfile.TemporaryDirectory() as folder:
                root, path, _ = self.fixture(folder)
                with self.assertRaises(ValueError): self.invoke(root, 'recover-reference', **options)
                self.assertNotEqual(lifecycle.read(path)['state'], 'active')

    def test_unclassified_or_already_reconciled_failure_cannot_recover(self):
        for failure in [{'classification':'timeout_result_unknown'},
                        {**recovery.rejected_reference('click',['e47'],ERROR,'0.1.22'), 'reconciled_at':'earlier'}]:
            with tempfile.TemporaryDirectory() as folder:
                root, path, receipt = self.fixture(folder)
                receipt['last_failure'] = failure
                lifecycle.save(path, receipt)
                with self.assertRaises(ValueError): self.invoke(root, 'recover-reference')

    def test_legacy_proof_binds_original_owner_project_and_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, receipt = self.fixture(folder, 'unknown')
            receipt.pop('last_failure')
            record = {'type':'event_msg','ordinal':9,'timestamp':lifecycle.now(), 'payload':{
                'type':'item_completed','thread_id':OWNER,'item':{'type':'CommandExecution','id':'original',
                'command':['python -X utf8 "C:/skill/browser_cli.py" --session case click e47'],
                'cwd':root.as_uri(), 'status':'failed','exit_code':1,'aggregated_output':ERROR}}}
            meta = {'type':'session_meta','payload':{'id':OWNER,'cwd':str(root)}}
            log = root / 'original.jsonl'
            # URI normalization is Windows-specific, matching the managed launcher.
            if __import__('os').name != 'nt': self.skipTest('Windows legacy path contract')
            for change in [None, 'owner', 'output', 'later']:
                rows = [meta, json.loads(json.dumps(record))]
                if change == 'owner': rows[1]['payload']['thread_id'] = 'foreign'
                if change == 'output': rows[1]['payload']['item']['aggregated_output'] += 'unknown'
                if change == 'later':
                    later = json.loads(json.dumps(record)); later['ordinal'] = 10
                    later['payload']['item']['aggregated_output'] = 'timeout'
                    rows.append(later)
                log.write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
                if change:
                    with self.assertRaises(ValueError): recovery.recovery_proof([str(log),'9'],receipt,'0.1.22')
                else:
                    result = recovery.recovery_proof([str(log),'9'],receipt,'0.1.22')
                    self.assertEqual(result['item_id'],'original')


if __name__ == '__main__': unittest.main()
