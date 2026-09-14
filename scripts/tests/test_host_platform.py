from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host_platform import detect_platform, host_paths, require_skill_platform
import publish_wsl_skills as publisher


class PlatformTests(unittest.TestCase):
    def test_kernel_identifies_wsl_not_subject_or_environment_name(self):
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu'}):
            self.assertEqual(detect_platform('Windows', '11'), 'windows')
            self.assertEqual(detect_platform('Linux', '6.8.0-generic'), 'linux')
        self.assertEqual(detect_platform('Linux', '6.6.87.2-microsoft-standard-WSL2'), 'wsl')
        self.assertEqual(detect_platform('Darwin', '24'), 'unsupported')

    def test_backup_of_wsl_is_a_windows_executor(self):
        manifest = publisher.PLATFORM_MANIFEST
        self.assertEqual(require_skill_platform('wsl-backup-audit', manifest, 'windows')['execution_platforms'], ['windows'])
        with self.assertRaisesRegex(ValueError, 'skill_platform_unsupported'):
            require_skill_platform('wsl-backup-audit', manifest, 'wsl')

    def test_all_source_entries_have_platform_declarations(self):
        names = {p.parent.name for p in publisher.SOURCE.glob('*/SKILL.md')}
        self.assertEqual(names, set(publisher.PLATFORM_MANIFEST['skills']))

    def test_wrong_platform_rejected_before_git_or_installation(self):
        with patch.object(sys, 'argv', ['publish', '--apply']), patch.object(publisher, 'detect_platform', return_value='windows'), patch.object(publisher.subprocess, 'check_output') as external:
            with self.assertRaisesRegex(ValueError, 'native_linux_publisher_required'):
                publisher.main()
            external.assert_not_called()

    def test_windows_skill_rejected_before_git_or_installation(self):
        with patch.object(sys, 'argv', ['publish', '--apply', '--skill', 'wsl-backup-audit']), patch.object(publisher, 'detect_platform', return_value='wsl'), patch.object(publisher.subprocess, 'check_output') as external:
            with self.assertRaisesRegex(ValueError, 'skill_platform_unsupported'):
                publisher.main()
            external.assert_not_called()

    def test_public_work_root_and_private_codex_home_are_independent(self):
        with tempfile.TemporaryDirectory(prefix='host-paths.', dir='/tmp') as directory:
            root = Path(directory)
            cfg = root / 'paths.json'
            cfg.write_text(json.dumps({'project_root': str(root/'projects'), 'work_root': str(root/'shared-work')}))
            with patch.dict(os.environ, {'HOME': str(root/'user'), 'CODEX_HOME': str(root/'private-state')}, clear=True):
                install, records = publisher.runtime_paths(cfg)
            self.assertEqual(install, root/'private-state/skills')
            self.assertEqual(records, root/'shared-work/host-baseline/skill-releases')

    def test_relative_public_path_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='host-paths.', dir='/tmp') as directory:
            with patch.dict(os.environ, {'HOME': directory, 'CODEX_WORK_ROOT': 'relative'}, clear=True):
                with self.assertRaisesRegex(ValueError, 'host_path_must_be_absolute'):
                    host_paths(Path(directory)/'absent')


if __name__ == '__main__':
    unittest.main()
