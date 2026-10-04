import unittest
from unittest.mock import patch

import group_identity as names


class GroupIdentityTests(unittest.TestCase):
    def test_same_computer_has_distinct_windows_and_wsl_titles(self):
        with patch.object(names.socket, 'gethostname', return_value='DESKTOP-TEST'):
            with patch.object(names.platform, 'system', return_value='Windows'):
                windows = names.identity('测试项目', 'audit')
            with patch.object(names.platform, 'system', return_value='Linux'), \
                 patch.object(names.platform, 'release', return_value='6.6-microsoft-standard-WSL2'), \
                 patch.dict(names.os.environ, {'WSL_DISTRO_NAME': 'Ubuntu-24.04'}):
                wsl = names.identity('测试项目')
        self.assertEqual(windows['title'], 'Playwright · DESKTOP-TEST · Windows · 测试项目 · audit')
        self.assertEqual(wsl['title'], 'Playwright · DESKTOP-TEST · WSL/Ubuntu-24.04 · 测试项目')
        self.assertEqual(wsl['computer_name'], windows['computer_name'])
        self.assertNotEqual(wsl['environment'], windows['environment'])

    def test_plain_linux_is_not_mislabeled_wsl_by_inherited_variable(self):
        with patch.object(names.platform, 'system', return_value='Linux'), \
             patch.object(names.platform, 'release', return_value='6.8.0-generic'), \
             patch.dict(names.os.environ, {'WSL_DISTRO_NAME': 'Ubuntu-24.04'}):
            self.assertEqual(names.identity('project')['environment'], 'Linux')

    def test_labels_do_not_inject_fields_or_empty_unknown_computer(self):
        self.assertEqual(names.label(' A·B\nC ', 'project'), 'A B C')
        for value in ['', '\n·', 'x' * 97]:
            with self.assertRaises(ValueError):
                names.label(value, 'computer')


if __name__ == '__main__':
    unittest.main()
