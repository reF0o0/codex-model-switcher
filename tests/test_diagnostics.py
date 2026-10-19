import json
import subprocess
import sys
import errno
from pathlib import Path
from unittest.mock import patch

from test_switcher import CodexHomeFixture, db_connection


class DiagnosticTests(CodexHomeFixture):
    def test_unreadable_unrelated_file_does_not_spoil_target_diagnosis(self):
        from switcher.diagnostics import diagnose_thread
        other = self.home/'sessions/unrelated.jsonl'
        other.write_text(json.dumps({'type':'session_meta','payload':{'id':'other'}})+'\n')
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == other:raise PermissionError(errno.EACCES, 'denied', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['state'], 'local_checked')
        self.assertNotEqual(result['category'], 'permission')

    def test_unreadable_target_still_reports_permission_error(self):
        from switcher.diagnostics import diagnose_thread
        target = self.home/'sessions/one.jsonl'
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == target:raise PermissionError(errno.EACCES, 'denied', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['category'], 'permission')

    def test_archived_target_with_same_id_name_still_reports_denied_access(self):
        from switcher.diagnostics import diagnose_thread
        base = self.home/'archived_sessions'; base.mkdir()
        target = base/'rollout-a.jsonl'
        (self.home/'sessions/one.jsonl').rename(target)
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == target:raise PermissionError(errno.EACCES, 'denied', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['category'], 'permission')

    def turns(self, rows):
        with db_connection(self.home/'thread_history_1.sqlite') as db:
            db.execute('create table thread_turns(thread_id text, rollout_ordinal integer, status text, error_json text)')
            db.executemany('insert into thread_turns values(?,?,?,?)',
                           [('a', n, status, json.dumps(error)) for n, (status, error) in enumerate(rows)])

    def test_history_error_is_classified_without_exposing_raw_error(self):
        from switcher.diagnostics import diagnose_thread
        self.turns([('failed', {'message': "HTTP 400 Invalid 'input[269].content': array too long sk-private-key"})])
        result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['category'], 'history_format')
        self.assertEqual(result['state'], 'needs_continuation')
        self.assertNotIn('sk-private-key', json.dumps(result))

    def test_recent_success_supersedes_old_quota_failure(self):
        from switcher.diagnostics import diagnose_thread
        self.turns([('failed', {'message': 'insufficient_quota 429'}), ('completed', None)])
        result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['category'], 'no_recent_failure')
        self.assertNotEqual(result['state'], 'verified')

    def test_error_categories_and_unknown_are_distinct(self):
        from switcher.diagnostics import classify_error
        for error, category in [('429 insufficient_quota', 'quota'), ('rate_limit_exceeded', 'rate_limit'),
                                ('connection reset by peer', 'network'), ('Permission denied', 'permission'),
                                ('invalid_api_key HTTP 401', 'authentication'), ('odd failure', 'unknown')]:
            self.assertEqual(classify_error(error), category)

    def test_diagnosis_does_not_create_tool_directory_or_require_write_permission(self):
        import shutil
        shutil.rmtree(self.app.data)
        config = self.home/'config.toml'
        before = config.read_bytes()
        config.chmod(0o400)
        try:
            proc = subprocess.run([sys.executable, str(__import__('pathlib').Path(__file__).parents[1]/'src/cli.py'),
                                   '--home', str(self.home), 'diagnose'], input='{"id":"a"}',
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertTrue(json.loads(proc.stdout)['ok'])
            self.assertFalse(self.app.data.exists())
            self.assertEqual(config.read_bytes(), before)
        finally:config.chmod(0o600)

    def test_missing_rollout_is_unchecked_instead_of_passed(self):
        from switcher.diagnostics import diagnose_thread
        (self.home/'sessions/one.jsonl').unlink()
        result = diagnose_thread(self.home, 'a')
        self.assertEqual(result['state'], 'unchecked')
        self.assertIn('未找到', result['format_summary'])

    def test_switch_lists_unavailable_thread_ids(self):
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual([r['id'] for r in result['attention']], ['b'])
        self.assertEqual(result['attention'][0]['state'], 'unchecked')

    def test_archived_rollout_is_found_when_database_path_is_stale(self):
        from switcher.diagnostics import diagnose_thread
        archived = self.home/'archived_sessions'
        archived.mkdir()
        (self.home/'sessions/one.jsonl').rename(archived/'moved.jsonl')
        self.assertEqual(diagnose_thread(self.home, 'a')['state'], 'local_checked')

    def test_unknown_reasoning_is_not_reported_as_checked(self):
        from switcher.diagnostics import diagnose_thread
        from test_compatibility import history
        (self.home/'sessions/one.jsonl').write_bytes(history(self.header, matched=False))
        self.assertEqual(diagnose_thread(self.home, 'a')['state'], 'needs_continuation')

    def test_attention_report_survives_restart_and_includes_skipped_and_unchecked(self):
        from switcher.core import Switcher
        from test_compatibility import history
        (self.home/'sessions/one.jsonl').write_bytes(history(self.header, matched=False))
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual({r['id'] for r in result['attention']}, {'a','b'})
        restarted = Switcher(self.home, require_closed=False, keychain=self.keychain)
        self.assertEqual(restarted.status()['last_switch']['skipped'], 1)
        self.assertEqual(restarted.status()['last_switch']['unavailable'], 1)

    def test_deepseek_switch_also_lists_missing_local_files(self):
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['switch_summary']['unavailable'], 1)
        self.assertEqual([r['id'] for r in result['attention']], ['b'])

    def test_duplicate_files_produce_one_attention_row_per_conversation(self):
        from test_compatibility import history
        content = history(self.header, matched=False)
        (self.home/'sessions/one.jsonl').write_bytes(content)
        (self.home/'sessions/duplicate.jsonl').write_bytes(content)
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual([r['id'] for r in result['attention']].count('a'), 1)

    def test_attention_ignore_survives_restart_and_can_be_cleared(self):
        from switcher.core import Switcher
        self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual([r['id'] for r in self.app.status()['last_switch']['attention']], ['b'])
        self.app.ignore_attention('b')
        restarted=Switcher(self.home,require_closed=False,keychain=self.keychain)
        status=restarted.status()
        self.assertEqual(status['ignored_attention'],['b'])
        self.assertEqual([r['id'] for r in status['last_switch']['attention']], ['b'])
        restarted.clear_attention_ignores()
        self.assertEqual(restarted.status()['ignored_attention'],[])

    def test_attention_ignore_requires_a_conversation_id(self):
        for value in ('',None,42):
            with self.assertRaises(ValueError):self.app.ignore_attention(value)
        self.assertFalse((self.app.data/'attention-ignores.json').exists())

    def test_cli_ignore_and_restore_attention(self):
        from pathlib import Path as FilePath
        cli=FilePath(__file__).parents[1]/'src/cli.py'
        for command,payload,expected in [('ignore-attention',{'id':'b'},['b']),
                                         ('clear-attention-ignores',{},[])]:
            proc=subprocess.run([sys.executable,'-B',str(cli),'--home',str(self.home),command],
                                input=json.dumps(payload),text=True,capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stdout)
            self.assertEqual(json.loads(proc.stdout)['result']['ignored_attention'],expected)
