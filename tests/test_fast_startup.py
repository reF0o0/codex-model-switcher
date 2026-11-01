import contextlib
import importlib.util
import io
import json
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

from test_switcher import CodexHomeFixture
from switcher.permissions import check_permissions


class FastStartupChecks(CodexHomeFixture):
    def cli(self, command, payload=None):
        spec = importlib.util.spec_from_file_location('startup_cli', Path(__file__).parents[1]/'src/cli.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        output = io.StringIO()
        with patch.object(sys, 'argv', ['cli.py', '--home', str(self.home), command]), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(payload or {}))), \
                patch('switcher.core.desktop_state', return_value='stopped'), contextlib.redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())['result']

    def test_startup_never_scans_history_prunes_or_reads_keychain(self):
        with patch('switcher.core.Switcher.cleanup', side_effect=AssertionError('startup cleanup')), \
                patch('switcher.core.Switcher.session_index', side_effect=AssertionError('history scan')), \
                patch('switcher.core.Switcher.compatibility_restore_all', side_effect=AssertionError('body scan')), \
                patch('switcher.keychain.Keychain.get', side_effect=AssertionError('keychain lookup')):
            status = self.cli('startup')
        self.assertEqual(len(status['threads']), 2)
        self.assertIsNone(status['deepseek_restore'])
        self.assertFalse(status['history_checked'])

    def test_quick_permission_probe_does_not_open_nested_history(self):
        target = self.home/'sessions/one.jsonl'
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == target:raise PermissionError(13, 'synthetic denial', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            self.assertTrue(check_permissions(self.home, full=False)['ready'])
            self.assertFalse(check_permissions(self.home)['ready'])
            with self.assertRaises(ValueError):
                self.cli('switch', {'provider':'openai','model':'gpt-6.1-sol'})
        self.assertEqual(self.app.store.list(), [])

    def test_default_only_switch_preserves_history_database_and_login(self):
        before = {name:(self.home/name).read_bytes() for name in ['state_5.sqlite','sessions/one.jsonl','auth.json']}
        with patch.object(self.app, 'session_index', side_effect=AssertionError('history scan')):
            result = self.app.switch('openai','gpt-6.1-sol',migrate=False)
        self.assertEqual(result['migrated'], 0)
        self.assertEqual(tomllib.loads(self.app.config.read_text())['model'], 'gpt-6.1-sol')
        for name, content in before.items():self.assertEqual((self.home/name).read_bytes(), content)
        manifest = self.app.store.read(result['backup_id'])
        self.assertEqual(manifest['routes'], [])
        self.assertNotIn('state_5.sqlite', manifest['files'])
        self.assertNotIn('sessions/one.jsonl', manifest['files'])
        self.app.restore(result['backup_id'])
        restored=tomllib.loads(self.app.config.read_text())
        self.assertEqual(restored['model'], 'deepseek-flash')
        self.assertIn(restored['model_provider'],restored['model_providers'])
        for name, content in before.items():self.assertEqual((self.home/name).read_bytes(), content)

    def test_preview_is_read_only_and_distinguishes_modes(self):
        before = self.app.config.read_bytes()
        normal = self.app.preview_switch('openai','gpt-6.1-sol')
        default = self.app.preview_switch('openai','gpt-6.1-sol',migrate=False)
        self.assertEqual(normal['candidate_threads'], 2)
        self.assertEqual(default['candidate_threads'], 0)
        self.assertEqual(default['current_model'], 'deepseek-flash')
        self.assertEqual(default['model'], 'gpt-6.1-sol')
        self.assertEqual(self.app.config.read_bytes(), before)
        self.assertEqual(self.app.store.list(), [])

    def test_default_only_still_obeys_client_and_profile_guards(self):
        self.app.config.write_text('profile="work"\n'+self.app.config.read_text()+'\n[profiles.work]\nmodel="gpt-6.1-sol"\n')
        with self.assertRaisesRegex(ValueError, 'profile'):
            self.app.switch('openai','gpt-6.1-sol',migrate=False)
        self.assertEqual(self.app.store.list(), [])

    def test_non_boolean_migration_mode_cannot_change_files(self):
        before = self.app.config.read_bytes()
        with self.assertRaises(ValueError):self.app.switch('openai','gpt-6.1-sol',migrate='false')
        self.assertEqual(self.app.config.read_bytes(), before)

    def test_default_only_still_rechecks_client_after_backup(self):
        from switcher.core import Switcher
        app=Switcher(self.home,require_closed=True,keychain=self.keychain)
        before=app.config.read_bytes()
        original=app.snapshot_configuration
        state=['stopped']
        def snapshot(*args,**kwargs):
            result=original(*args,**kwargs);state[0]='running';return result
        with patch('switcher.core.desktop_state',side_effect=lambda:state[0]),patch.object(app,'snapshot_configuration',side_effect=snapshot):
            with self.assertRaises(ValueError):app.switch('openai','gpt-6.1-sol',migrate=False)
        self.assertEqual(app.config.read_bytes(),before)

    def test_config_backups_do_not_displace_database_recovery_points(self):
        full=self.app.backup()['backup_id']
        for _ in range(5):self.app.snapshot_configuration()
        self.app.save_settings(3,30)
        self.app.cleanup()
        self.assertIn(full,[row['id'] for row in self.app.store.list()])
