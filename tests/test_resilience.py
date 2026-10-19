import json

from test_switcher import CodexHomeFixture, db_connection
from switcher.core import Switcher


class ResilienceTests(CodexHomeFixture):
    def test_switch_skips_an_unrelated_malformed_session_header(self):
        bad = self.home/'sessions'/'bad.jsonl'
        damaged = b'{broken\n'
        bad.write_bytes(damaged)

        result = self.app.switch('openai','gpt-6.1-sol')

        self.assertEqual(result['migrated'],2)
        self.assertEqual(bad.read_bytes(),damaged)
        self.assertTrue(any(row.get('file')=='bad.jsonl'
                            for row in result['compatibility']['skipped']))

    def test_switch_does_not_update_a_route_whose_session_header_is_broken(self):
        path = self.home/'sessions'/'broken-thread.jsonl'
        path.write_bytes(b'{broken\n')
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute('insert into threads values(?,?,?,?,?,?,?)',
                       ('broken','deepseek','deepseek-flash','high',0,'坏会话',str(path)))

        result = self.app.switch('openai','gpt-6.1-sol')
        with db_connection(self.home/'state_5.sqlite') as db:
            rows = {row[0]:row[1:] for row in db.execute('select id,model_provider,model from threads')}

        self.assertEqual(rows['broken'][0],'deepseek')
        self.assertEqual(rows['a'][0],'openai')
        self.assertEqual(result['migrated'],2)
        self.assertEqual(path.read_bytes(),b'{broken\n')
        self.assertTrue(any(row.get('id')=='broken' for row in result['attention']))

    def test_session_index_skips_malformed_files_and_reports_them(self):
        bad = self.home/'sessions'/'bad.jsonl'
        bad.write_bytes(b'{broken\n')
        skipped = []

        index = self.app.session_index(skipped=skipped)

        self.assertIn('a',index)
        self.assertEqual(index['a'][0].resolve(),(self.home/'sessions'/'one.jsonl').resolve())
        self.assertTrue(any(row.get('file')=='bad.jsonl' for row in skipped))

    def test_status_survives_corrupt_tool_state_files(self):
        names = ['attention-ignores.json','links.json','bridge-state.json',
                 'last-switch.json','settings.json']

        for name in names:
            with self.subTest(name=name):
                path = self.app.data/name
                path.write_text('{broken\n')
                status = self.app.status()

                self.assertTrue(any(name in warning for warning in status['warnings']))
                self.assertEqual(status['settings'],
                                 {'keep':10,'days':30,'auto_cleanup':True})
                self.assertEqual(status['ignored_attention'],[])

    def test_bad_backup_manifest_is_reported_without_breaking_status(self):
        backup = self.app.backup()['backup_id']
        manifest = self.app.store.root/backup/'manifest.json'
        data = json.loads(manifest.read_text())
        data['files'] = []
        manifest.write_text(json.dumps(data))
        errors = []

        status = self.app.status()
        entries = self.app.store.list(errors=errors)

        self.assertEqual(entries,[])
        self.assertEqual(errors,[backup])
        self.assertTrue(any('备份' in warning for warning in status['warnings']))

    def test_corrupt_pending_state_does_not_hide_the_window_but_blocks_writes(self):
        (self.app.pending).write_text('{broken\n')

        status = self.app.status()

        self.assertTrue(any('pending.json' in warning for warning in status['warnings']))
        with self.assertRaises(ValueError):
            self.app.switch('openai','gpt-6.1-sol')

    def test_recovery_status_reports_corrupt_database_and_valid_backup(self):
        backup = self.app.backup()['backup_id']
        self.app.db.write_bytes(b'not a sqlite database\n'*100)

        state = Switcher.recovery_status(self.home)

        self.assertEqual(state['database_state']['state_5.sqlite'],'invalid')
        self.assertEqual([entry['id'] for entry in state['database_backups']],[backup])

    def test_recover_database_replaces_only_the_corrupt_database_and_keeps_sessions(self):
        backup = self.app.backup()['backup_id']
        new_session = self.home/'sessions'/'new.jsonl'
        new_session.write_bytes(self.header+b'new message\n')
        corrupt = b'not a sqlite database\n'*100
        self.app.db.write_bytes(corrupt)

        result = self.app.recover_database(backup)

        self.assertEqual(result['databases'],['state_5.sqlite'])
        self.assertEqual(len(self.app.status()['threads']),2)
        self.assertEqual(new_session.read_bytes(),self.header+b'new message\n')
        saved = list((self.app.data/'database-recovery').glob('*/state_5.sqlite'))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0].read_bytes(),corrupt)

    def test_recover_database_rejects_a_corrupt_backup_without_touching_current_db(self):
        backup = self.app.backup()['backup_id']
        manifest = self.app.store.read(backup)
        self.app.store.blob_path(manifest['files']['state_5.sqlite']).write_bytes(b'bad gzip')
        self.app.db.write_bytes(b'not a sqlite database\n'*100)
        before = self.app.db.read_bytes()

        with self.assertRaises((ValueError,OSError,EOFError)):
            self.app.recover_database(backup)

        self.assertEqual(self.app.db.read_bytes(),before)

