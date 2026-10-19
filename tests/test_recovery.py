import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tomllib
from unittest.mock import patch

from test_switcher import CodexHomeFixture, db_connection
from switcher.core import Switcher


class RecoveryTests(CodexHomeFixture):
    def setUp(self):
        super().setUp()
        self.backup = self.app.backup()['backup_id']

    def cli(self, command, payload=None):
        spec = importlib.util.spec_from_file_location('recovery_cli', Path(__file__).parents[1]/'src/cli.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        output = io.StringIO()
        with patch.object(sys, 'argv', ['cli.py', '--home', str(self.home), command]), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(payload or {}))), \
                patch('switcher.core.desktop_state', return_value='stopped'), contextlib.redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())['result']

    def test_recovery_list_works_without_config_and_does_not_create_files(self):
        self.app.config.unlink()
        before = {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')}
        result = self.cli('recovery-status')
        self.assertEqual(result['config_state'], 'missing')
        self.assertEqual([b['id'] for b in result['backups']], [self.backup])
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')})

    def test_recovery_list_does_not_initialize_a_missing_backup_directory(self):
        import shutil
        shutil.rmtree(self.app.data)
        self.app.config.unlink()
        result = self.cli('recovery-status')
        self.assertEqual(result['backups'], [])
        self.assertFalse(self.app.data.exists())

    def test_cli_restores_missing_config_and_preserves_new_messages(self):
        self.app.config.unlink()
        path = self.home/'sessions/one.jsonl'
        path.write_bytes(path.read_bytes()+b'new message\n')
        self.keychain.value = 'current-key'
        with patch('switcher.keychain.Keychain.get', return_value='current-key'):
            result = self.cli('restore', {'id': self.backup})
        self.assertTrue(result['config_rebuilt'])
        self.assertTrue(path.read_bytes().endswith(b'new message\n'))
        self.assertEqual(tomllib.loads(self.app.config.read_text())['model'], 'deepseek-flash')
        self.assertNotIn('<stored-in-keychain>', self.app.config.read_text())

    def test_corrupt_config_is_preserved_privately_before_rebuilding(self):
        damaged = b'model="broken"\n[features\napi_key="private-damaged-value"\n'
        self.app.config.write_bytes(damaged)
        result = self.app.restore(self.backup)
        self.assertTrue(result['config_rebuilt'])
        saved = Path(result['saved_config'])
        self.assertEqual(saved.read_bytes(), damaged)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('private-damaged-value', json.dumps(result))
        self.assertNotIn('<stored-in-keychain>', self.app.config.read_text())
        tomllib.loads(self.app.config.read_text())

    def test_non_utf8_config_can_also_be_rebuilt(self):
        self.app.config.write_bytes(b'\xff\xfe damaged')
        result = self.app.restore(self.backup)
        self.assertEqual(Path(result['saved_config']).read_bytes(), b'\xff\xfe damaged')
        tomllib.loads(self.app.config.read_text())

    def test_invalid_backup_does_not_touch_damaged_config(self):
        damaged = b'[broken\n'
        self.app.config.write_bytes(damaged)
        manifest = self.app.store.read(self.backup)
        self.app.store.blob_path(manifest['files']['config.toml']).write_bytes(b'bad gzip')
        with self.assertRaises(ValueError):self.app.restore(self.backup)
        self.assertEqual(self.app.config.read_bytes(), damaged)
        self.assertFalse((self.app.data/'config-recovery').exists())

    def test_missing_config_remains_missing_after_database_failure(self):
        self.app.config.unlink()
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        with self.assertRaises(sqlite3.Error):self.app.restore(self.backup)
        self.assertFalse(self.app.config.exists())
        self.assertEqual(json.loads(self.app.pending.read_text())['backup_id'], self.backup)

    def test_missing_config_does_not_bypass_other_permission_failures(self):
        from switcher.permissions import PermissionCheckError
        self.app.config.unlink()
        self.app.db.chmod(0o400)
        try:
            with self.assertRaises(PermissionCheckError):self.cli('restore', {'id':self.backup})
        finally:self.app.db.chmod(0o600)
        self.assertFalse(self.app.config.exists())

    def test_valid_config_keeps_the_existing_partial_restore_behavior(self):
        self.app.config.write_text(self.app.config.read_text()+'\n[unrelated]\nkeep="later setting"\n')
        result = self.app.restore(self.backup)
        self.assertFalse(result['config_rebuilt'])
        self.assertEqual(tomllib.loads(self.app.config.read_text())['unrelated']['keep'], 'later setting')

    def test_recovery_safety_snapshot_is_not_offered_as_a_restorable_config(self):
        self.app.config.unlink()
        result = self.app.restore(self.backup)
        self.assertTrue(self.app.store.verify(result['backup_id']))
        offered = {b['id'] for b in self.app.status()['backups']}
        self.assertIn(self.backup, offered)
        self.assertNotIn(result['backup_id'], offered)

    def test_failed_recovery_attempts_cannot_evict_the_only_valid_config_backup(self):
        self.app.save_settings(3, 30)
        self.app.config.unlink()
        with db_connection(self.app.db) as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        for _ in range(3):
            with self.assertRaises(sqlite3.Error):self.app.restore(self.backup)
        with db_connection(self.app.db) as db:db.execute('drop trigger reject_update')
        self.app.restore(self.backup)
        self.app.cleanup()
        self.assertTrue(self.app.store.verify(self.backup))
        self.assertIn(self.backup, {b['id'] for b in Switcher.recovery_status(self.home)['backups']})

    def test_recovery_listing_rejects_config_symlinks(self):
        self.app.config.unlink()
        self.app.config.symlink_to(self.home/'auth.json')
        with self.assertRaises(ValueError):self.cli('recovery-status')

    def test_recovery_write_failure_after_config_publication_restores_missing_state(self):
        from switcher import core
        self.app.config.unlink()
        actual = core.atomic_write
        def failure(path, data):
            actual(path, data)
            if path == self.app.config:raise OSError('simulated disk failure after publishing config')
        with patch.object(core, 'atomic_write', side_effect=failure):
            with self.assertRaises(OSError):self.app.restore(self.backup)
        self.assertFalse(self.app.config.exists())
        self.assertTrue(self.app.pending.exists())
        self.app.restore(self.backup)
        tomllib.loads(self.app.config.read_text())
