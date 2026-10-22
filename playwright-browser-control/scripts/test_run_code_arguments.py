from contextlib import ExitStack, nullcontext, redirect_stderr
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import browser_cli as cli
import execution_state as lifecycle


class RunCodeArgumentTests(unittest.TestCase):
    def test_single_function_and_official_file_forms_pass_unchanged(self):
        cases = [['async page => ({text: "quoted value"})'],
                 ['--filename', 'C:/with spaces/中文 capture.js'],
                 ['--filename=C:/with spaces/中文 capture.js']]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                original=list(arguments)
                cli.validate_run_code_arguments('run-code',arguments)
                self.assertEqual(arguments,original)

    def test_split_inline_missing_file_and_extra_arguments_are_rejected(self):
        cases = [[], ['async (page)', '=>', '{', 'copy', 'editor', '}'],
                 ['--filename'], ['--filename', ''], ['--filename='],
                 ['--filename','capture.js','extra'], ['--other','capture.js']]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError,'before browser preflight'):
                    cli.validate_run_code_arguments('run-code',arguments)

    def test_invalid_arguments_cannot_reach_runtime_credentials_browser_or_receipt(self):
        with ExitStack() as stack:
            for target,name in [(cli,'runtime_root'),(cli,'read_settings'),(cli,'doctor'),
                                (cli,'prepare'),(cli,'run_cli'),(cli,'vault_credential'),
                                (lifecycle,'read'),(lifecycle,'save'),(lifecycle,'locked'),
                                (cli.subprocess,'run')]:
                stack.enter_context(mock.patch.object(target,name,side_effect=AssertionError(name+' called')))
            stack.enter_context(mock.patch.object(sys,'argv',['browser_cli.py','--session','fixture',
                                                              'run-code','one','two','three','four','five','six']))
            errors=io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(cli.main(),1)
            self.assertIn('No receipt was changed.',errors.getvalue())

    def test_valid_file_arguments_reach_existing_entry_checks(self):
        with mock.patch.object(sys,'argv',['browser_cli.py','--session','fixture','run-code','--filename','capture.js']), \
             mock.patch.object(cli,'runtime_root',side_effect=ValueError('existing entry checks reached')) as runtime, \
             redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(cli.main(),1)
        runtime.assert_called_once()
        self.assertIn('existing entry checks reached',errors.getvalue())

    def test_valid_file_form_cannot_unlock_old_unknown_or_runtime_failures(self):
        owner='11111111-2222-4333-8444-555555555555'
        for failure in ('unclassified_result_unknown','timeout_result_unknown','permission_denied'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);profile=root/'profile'
                receipt=lifecycle.fresh({'browser_name':'chrome','session_name':'fixture','title':'test'},root,profile,owner)
                receipt.update(state='unknown',last_failure={'classification':failure,'at':'original'})
                original=copy.deepcopy(receipt)
                arguments=['--filename','capture.js'];cli.validate_run_code_arguments('run-code',arguments)
                args=type('Args',(),{'browser':'chrome','session':'fixture','conversation':owner,
                                     'command':'run-code','arguments':arguments,'project':None,'timeout':1})()
                settings={'browsers':{'chrome':{'profile_path':str(profile)}}}
                with mock.patch.object(Path,'cwd',return_value=root), \
                     mock.patch.object(lifecycle,'locked',return_value=nullcontext()), \
                     mock.patch.object(lifecycle,'read',return_value=receipt), \
                     mock.patch.object(cli,'prepare',side_effect=AssertionError('browser called')), \
                     mock.patch.object(lifecycle,'save',side_effect=AssertionError('receipt changed')):
                    with self.assertRaisesRegex(ValueError,'requires readback'):
                        cli.managed_main(args,root,settings)
                self.assertEqual(receipt,original)

    def test_other_commands_are_not_reinterpreted(self):
        cli.validate_run_code_arguments('snapshot',['existing','arguments'])


if __name__=='__main__':unittest.main()
