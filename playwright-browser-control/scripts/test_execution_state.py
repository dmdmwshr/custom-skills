import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import execution_state as lifecycle
import browser_cli as cli
from group_identity import identity


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temp.name)
        self.owner = '12345678-1234-1234-1234-123456789012'
        self.group = identity('project', 'session', 'chrome')

    def tearDown(self):
        self.temp.cleanup()

    def test_next_execution_changes_physical_session_with_same_conversation_project(self):
        a = lifecycle.fresh(self.group, self.cwd, self.cwd, self.owner)
        b = lifecycle.fresh(self.group, self.cwd, self.cwd, self.owner)
        self.assertNotEqual(a['execution_id'], b['execution_id'])
        self.assertNotEqual(a['cli_session'], b['cli_session'])
        self.assertEqual(a['conversation_id'], b['conversation_id'])
        self.assertEqual(a['project_root'], b['project_root'])
        self.assertLessEqual(len(a['cli_session']), 48)

    def test_cross_conversation_project_profile_and_legacy_reuse_rejected(self):
        a = lifecycle.fresh(self.group, self.cwd, self.cwd, self.owner)
        for cwd, profile, owner in [(self.cwd/'other', self.cwd, self.owner),
                                    (self.cwd, self.cwd/'other', self.owner),
                                    (self.cwd, self.cwd, 'different')]:
            with self.assertRaises(ValueError): lifecycle.check_owner(a, cwd, profile, owner)
        with self.assertRaises(ValueError): lifecycle.check_owner(self.group, self.cwd, self.cwd, self.owner)

    def test_history_is_separate_and_never_replaces_active_pointer(self):
        path = lifecycle.receipt_path(self.cwd, 'chrome', 'session')
        a = lifecycle.fresh(self.group, self.cwd, self.cwd, self.owner)
        b = lifecycle.fresh(self.group, self.cwd, self.cwd, self.owner)
        lifecycle.save(path, b); lifecycle.archive(path, a)
        self.assertEqual(lifecycle.read(path)['execution_id'], b['execution_id'])
        history = path.parent.parent/'history'/path.stem/(a['execution_id']+'.json')
        self.assertEqual(lifecycle.read(history)['execution_id'], a['execution_id'])

    def test_real_conversation_required_and_session_path_cannot_escape(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError): lifecycle.conversation()
        for value in ['../escape','two spaces','']:
            with self.assertRaises(ValueError): lifecycle.receipt_path(self.cwd,'chrome',value)

    def test_unavailable_saved_store_never_means_clean(self):
        with self.assertRaises(ValueError): cli.saved_groups(self.cwd)

    def test_release_receipt_is_scoped_to_the_exact_physical_session(self):
        self.assertEqual(cli.release_status(0,'detached','chrome-own'),'detached')
        self.assertEqual(cli.release_status(1,"Browser 'chrome-own' is not attached.",'chrome-own'),'already_not_attached')
        self.assertEqual(cli.release_status(1,"Browser 'chrome-other' is not attached.",'chrome-own'),'unverified')


if __name__ == '__main__': unittest.main()
