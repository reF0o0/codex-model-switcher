import errno
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))
from switcher.permissions import check_permissions


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name) / 'codex'
        self.home.mkdir()
        (self.home / 'config.toml').write_text('model="gpt-6.1-sol"\n')
        (self.home / 'state_5.sqlite').write_bytes(b'database placeholder')
        folder = self.home / 'sessions' / '2026' / '10'
        folder.mkdir(parents=True)
        (folder / 'one.jsonl').write_bytes(b'private history\n')

    def files(self):
        return {str(p.relative_to(self.home)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.home.rglob('*') if p.is_file()}

    def test_check_leaves_existing_data_and_times_unchanged(self):
        before = self.files()
        report = check_permissions(self.home)
        self.assertTrue(report['ready'])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.home / 'provider-switcher').exists())
        self.assertNotIn('private history', json.dumps(report))

    def test_nested_history_denial_is_reported_with_exact_path(self):
        target = self.home / 'sessions' / '2026' / '10' / 'one.jsonl'
        original = pathlib.Path.open

        def denied(path, *args, **kwargs):
            if path == target:
                raise PermissionError(errno.EACCES, 'test denial', str(path))
            return original(path, *args, **kwargs)

        with mock.patch.object(pathlib.Path, 'open', denied):
            report = check_permissions(self.home)
        self.assertFalse(report['ready'])
        failed = [c for c in report['checks'] if c['status'] == 'blocked']
        self.assertEqual(failed[0]['path'], str(target))

    def test_directory_creation_denial_blocks_even_when_files_are_readable(self):
        with mock.patch('switcher.permissions.tempfile.mkstemp',
                        side_effect=PermissionError(errno.EPERM, 'test denial', str(self.home))):
            report = check_permissions(self.home)
        self.assertFalse(report['ready'])

    def test_rename_denial_is_reported_and_probe_files_are_removed(self):
        before = self.files()
        with mock.patch('switcher.permissions.os.replace',
                        side_effect=PermissionError(errno.EACCES, 'test denial', str(self.home))):
            report = check_permissions(self.home)
        self.assertFalse(report['ready'])
        self.assertEqual(self.files(), before)

    def test_read_only_configuration_stops_cli_before_creating_backup(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses ordinary file permissions')
        target = self.home / 'config.toml'
        target.chmod(0o400)
        self.addCleanup(target.chmod, 0o600)
        cli = pathlib.Path(__file__).parents[1] / 'src' / 'cli.py'
        process = subprocess.run([sys.executable, '-B', str(cli), '--home', str(self.home), 'switch'],
                                 input=json.dumps({'provider': 'openai', 'model': 'gpt-6.1-sol'}),
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 1)
        data = json.loads(process.stdout)
        self.assertFalse(data['ok'])
        self.assertEqual(next(c['path'] for c in data['permissions']['checks']
                              if c['status'] == 'blocked'), str(target))
        self.assertFalse((self.home / 'provider-switcher').exists())

    def test_sqlite_auxiliary_file_denial_blocks_writes(self):
        target = self.home / 'state_5.sqlite-wal'
        target.write_bytes(b'wal')
        original = pathlib.Path.open
        def denied(path, *args, **kwargs):
            if path == target:
                raise PermissionError(errno.EACCES, 'test denial', str(path))
            return original(path, *args, **kwargs)
        with mock.patch.object(pathlib.Path, 'open', denied):
            report = check_permissions(self.home)
        self.assertFalse(report['ready'])
        self.assertTrue(any(c['status'] == 'blocked' and c['path'] == str(target)
                            for c in report['checks']))

    def test_missing_home_is_actionable_and_never_created(self):
        target = self.home / 'missing'
        report = check_permissions(target)
        self.assertFalse(report['ready'])
        self.assertEqual(report['checks'][0]['status'], 'missing')
        self.assertFalse(target.exists())

    def test_symlinked_history_does_not_grant_write_access_to_target(self):
        target = self.home / 'sessions' / 'linked.jsonl'
        target.symlink_to(self.home / 'config.toml')
        report = check_permissions(self.home)
        self.assertFalse(report['ready'])
        self.assertTrue(any(c['status'] == 'blocked' and c['path'] == str(target)
                            for c in report['checks']))

    def test_cli_reports_permissions_before_switcher_initialization(self):
        cli = pathlib.Path(__file__).parents[1] / 'src' / 'cli.py'
        (self.home / 'config.toml').unlink()
        for command in ['permissions', 'startup', 'backup']:
            process = subprocess.run([sys.executable, '-B', str(cli), '--home', str(self.home), command],
                                     capture_output=True, text=True)
            data = json.loads(process.stdout)
            if command == 'permissions':
                self.assertTrue(data['ok'])
                self.assertFalse(data['result']['ready'])
            else:
                self.assertFalse(data['ok'])
                self.assertFalse(data['permissions']['ready'])
            self.assertFalse((self.home / 'provider-switcher').exists())


if __name__ == '__main__':
    unittest.main()
