"""Exercise release gates against isolated real Git repositories and remotes."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish_wsl_skills import files, require_published_source, require_tracked_tree, runtime_paths


class RuntimePathTests(unittest.TestCase):
    def test_current_home_is_used_when_codex_home_is_unset(self):
        with tempfile.TemporaryDirectory(prefix='skill-home.', dir='/tmp') as directory:
            with patch.dict(os.environ, {'HOME': directory}, clear=True):
                install, records = runtime_paths(Path(directory) / 'absent-config.json')
            self.assertEqual(install, Path(directory) / '.codex/skills')
            self.assertEqual(records, Path(directory) / 'Documents/work/host-baseline/skill-releases')

    def test_explicit_state_and_legacy_alias_resolve_to_one_installation(self):
        with tempfile.TemporaryDirectory(prefix='skill-home.', dir='/tmp') as directory:
            root = Path(directory)
            (root / 'active').mkdir()
            (root / 'legacy').symlink_to(root / 'active', target_is_directory=True)
            with patch.dict(os.environ, {'HOME': directory, 'CODEX_HOME': str(root / 'legacy')}, clear=True):
                install, records = runtime_paths(Path(directory) / 'absent-config.json')
            self.assertEqual(install, root / 'active/skills')
            self.assertEqual(records, root / 'Documents/work/host-baseline/skill-releases')


class PublishedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skill-release-test.', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / 'remote.git'
        self.repo = root / 'source'
        self.repo.mkdir()
        self.run_git(root, 'init', '--bare', str(self.remote))
        self.run_git(self.repo, 'init', '-b', 'main')
        self.run_git(self.repo, 'config', 'user.name', 'Skill Test')
        self.run_git(self.repo, 'config', 'user.email', 'skill-test@example.invalid')
        skill = self.repo / 'example'
        skill.mkdir()
        (skill / 'SKILL.md').write_text('# Test\n', encoding='utf-8')
        (self.repo / '.gitignore').write_text('ignored.txt\n', encoding='utf-8')
        self.run_git(self.repo, 'add', 'example', '.gitignore')
        self.run_git(self.repo, 'commit', '-m', 'initial')
        self.run_git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        self.run_git(self.repo, 'push', '-u', 'origin', 'main')

    def run_git(self, cwd, *args):
        return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8').strip()

    def test_published_clean_tree_is_accepted(self):
        self.assertEqual(require_published_source(self.repo), self.run_git(self.repo, 'rev-parse', 'HEAD'))
        require_tracked_tree(self.repo, 'example', files(self.repo / 'example'))

    def test_modified_source_is_rejected(self):
        (self.repo / 'example/SKILL.md').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'source_dirty'):
            require_published_source(self.repo)

    def test_untracked_source_is_rejected(self):
        (self.repo / 'unknown.txt').write_text('unknown', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'source_dirty'):
            require_published_source(self.repo)

    def test_unpushed_commit_is_rejected(self):
        self.run_git(self.repo, 'commit', '--allow-empty', '-m', 'not pushed')
        with self.assertRaisesRegex(ValueError, 'live_remote'):
            require_published_source(self.repo)

    def test_live_remote_ahead_is_rejected_despite_stale_tracking_ref(self):
        other = Path(self.temp.name) / 'other'
        self.run_git(Path(self.temp.name), 'clone', '-b', 'main', str(self.remote), str(other))
        self.run_git(other, '-c', 'user.name=Other', '-c', 'user.email=other@example.invalid',
                     'commit', '--allow-empty', '-m', 'remote advanced')
        self.run_git(other, 'push', 'origin', 'main')
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.run_git(self.repo, 'rev-parse', 'origin/main'))
        with self.assertRaisesRegex(ValueError, 'live_remote'):
            require_published_source(self.repo)

    def test_ignored_payload_is_rejected(self):
        (self.repo / 'example/ignored.txt').write_text('not versioned', encoding='utf-8')
        require_published_source(self.repo)
        with self.assertRaisesRegex(ValueError, 'untracked_or_ignored'):
            require_tracked_tree(self.repo, 'example', files(self.repo / 'example'))

    def test_symlink_payload_is_rejected(self):
        (self.repo / 'example/link').symlink_to(self.repo / '.gitignore')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            files(self.repo / 'example')


if __name__ == '__main__':
    unittest.main()
