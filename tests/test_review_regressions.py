import json
from unittest.mock import patch
from test_switcher import CodexHomeFixture, db_connection
from switcher.core import Switcher


class ReviewRegressions(CodexHomeFixture):
    def test_blocked_duplicate_preserves_every_copy_and_route(self):
        self.app.switch('openai','gpt-6.1-sol')
        path=self.home/'sessions/one.jsonl'
        before=path.read_bytes()
        (self.home/'sessions/broken-a.jsonl').write_text('{broken\n')
        result=self.app.switch('deepseek','deepseek-flash')
        self.assertEqual(path.read_bytes(),before)
        with db_connection(self.app.db) as db:
            self.assertEqual(db.execute("select model_provider from threads where id='a'").fetchone()[0],'openai')
        self.assertTrue(any(row['id']=='a' for row in result['attention']))

    def test_profile_blocks_route_repair_before_backup(self):
        self.app.config.write_text('profile="work"\n'+self.app.config.read_text()+'\n[profiles.work]\nmodel_provider="openai"\nmodel="gpt-6.1-sol"\n')
        with self.assertRaisesRegex(ValueError,'profile'):self.app.repair()
        self.assertEqual(self.app.store.list(),[])

    def test_corrupt_pending_can_restore_selected_backup_and_is_retained(self):
        backup=self.app.backup()['backup_id']
        raw=b'{broken\n'
        self.app.pending.write_bytes(raw)
        result=self.app.restore(backup)
        self.assertEqual(result['restored'],backup)
        self.assertFalse(self.app.pending.exists())
        self.assertTrue(any(p.read_bytes()==raw for p in (self.app.data/'journal-recovery').iterdir()))

    def test_key_save_rechecks_client_after_backup(self):
        app=Switcher(self.home,require_closed=True,keychain=self.keychain)
        before=app.config.read_bytes();original=app.snapshot;state=['stopped']
        def snapshot(*args,**kwargs):
            result=original(*args,**kwargs);state[0]='running';return result
        with patch('switcher.core.desktop_state',side_effect=lambda:state[0]),patch.object(app,'snapshot',side_effect=snapshot):
            with self.assertRaises(ValueError):app.save_key('sk-'+'a'*32)
        self.assertEqual(app.config.read_bytes(),before)
        self.assertIsNone(self.keychain.value)

    def test_unrelated_nonobject_header_is_skipped(self):
        path=self.home/'sessions/bad.jsonl';path.write_text('[]\n')
        result=self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual(result['migrated'],2)
        self.assertEqual(path.read_text(),'[]\n')

    def test_catalog_models_and_efforts_are_usable(self):
        (self.home/'models_cache.json').write_text(json.dumps({'models':[{'slug':'gpt-6.1-terra','supported_reasoning_levels':[{'effort':'high'},{'effort':'max'}]}]}))
        self.app.switch('openai','gpt-6.1-terra','max')
        options=self.app.runtime_status()['model_options']['openai']
        self.assertTrue(any(m['slug']=='gpt-6.1-terra' and 'max' in m['efforts'] for m in options))

    def test_blocked_duplicate_preserves_repaired_body_on_reverse_switch(self):
        from test_compatibility import history
        path=self.home/'sessions/one.jsonl';path.write_bytes(history(self.header))
        self.app.switch('openai','gpt-6.1-sol')
        before=path.read_bytes()
        (self.home/'sessions/broken-a.jsonl').write_text('{broken\n')
        result=self.app.switch('deepseek','deepseek-flash')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(result['compatibility']['records'],0)

    def test_corrupt_pending_shape_is_reported_and_recoverable(self):
        backup=self.app.backup()['backup_id']
        self.app.pending.write_text('[]')
        self.assertTrue(self.app.status()['pending_error'])
        self.app.restore(backup)
        self.assertFalse(self.app.pending.exists())

    def test_corrupt_model_catalog_falls_back_to_known_choices(self):
        (self.home/'models_cache.json').write_text('{broken')
        options=self.app.runtime_status()['model_options']['openai']
        self.assertTrue(any(m['slug']=='gpt-6.1-terra' for m in options))

    def test_model_efforts_reject_unsupported_value_before_backup(self):
        with self.assertRaises(ValueError):self.app.switch('openai','gpt-6-luna','ultra')
        self.assertEqual(self.app.store.list(),[])
