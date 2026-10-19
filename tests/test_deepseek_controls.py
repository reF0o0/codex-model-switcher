import json
import sqlite3
import tomllib
from unittest.mock import patch

from test_switcher import CodexHomeFixture, db_connection
from test_compatibility import history, line


class SearchControlTests(CodexHomeFixture):
    def configure(self, provider='deepseek', model='deepseek-v4-pro', mode=None):
        from switcher.core import rewrite_config
        text=(self.home/'config.toml').read_text()
        (self.home/'config.toml').write_text(rewrite_config(text,{
            'model_provider':provider,'model':model,'web_search':mode}))
        catalog=json.loads((self.home/'models.json').read_text())
        for item in catalog['models']:
            if item['slug']=='deepseek-v4-pro':item.update(supports_search_tool=True,web_search_tool_type='text')
        (self.home/'models.json').write_text(json.dumps(catalog))

    def config(self):
        return tomllib.loads((self.home/'config.toml').read_text())

    def test_toggle_backs_up_without_changing_routes_messages_or_auth(self):
        self.configure()
        routes=self.app.routes()
        auth=(self.home/'auth.json').read_bytes()
        before=(self.home/'config.toml').read_bytes()
        result=self.app.set_web_search(True)
        self.assertEqual(self.config()['web_search'],'live')
        self.assertEqual(tomllib.loads(self.app.store.content(result['backup_id'],'config.toml').decode()),
                         tomllib.loads(before.replace(b'test-secret',b'<stored-in-keychain>').decode()))
        self.assertEqual(self.app.routes(),routes)
        self.assertEqual((self.home/'sessions/one.jsonl').read_bytes(),self.original)
        self.assertEqual((self.home/'auth.json').read_bytes(),auth)
        self.app.set_web_search(False)
        self.assertEqual(self.config()['web_search'],'disabled')

    def test_flash_cannot_enable_but_can_disable_existing_live_config(self):
        before=(self.home/'config.toml').read_bytes()
        with self.assertRaisesRegex(ValueError,'Pro'):
            self.app.set_web_search(True)
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(self.app.store.list(),[])
        self.configure(model='deepseek-flash',mode='live')
        self.app.set_web_search(False)
        self.assertEqual(self.config()['web_search'],'disabled')

    def test_gpt_toggle_rejected_without_mutation(self):
        self.configure(provider='openai',model='gpt-6.1-sol',mode='live')
        before=(self.home/'config.toml').read_bytes()
        with self.assertRaisesRegex(ValueError,'DeepSeek'):
            self.app.set_web_search(False)
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(self.app.store.list(),[])

    def test_non_boolean_toggle_rejected(self):
        for value in ['false',1,None]:
            with self.assertRaises(ValueError):self.app.set_web_search(value)
        self.assertEqual(self.app.store.list(),[])

    def test_repeated_toggle_is_noop(self):
        self.configure(mode='live')
        self.app.set_web_search(True)
        self.assertEqual(self.app.store.list(),[])

    def test_provider_preferences_survive_roundtrip_and_flash_visit(self):
        self.configure(provider='openai',model='gpt-6.1-sol',mode='cached')
        self.app.switch('deepseek','deepseek-v4-pro')
        self.assertEqual(self.config()['web_search'],'disabled')
        self.app.set_web_search(True)
        self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual(self.config()['web_search'],'cached')
        self.app.switch('deepseek','deepseek-flash')
        self.assertEqual(self.config()['web_search'],'disabled')
        self.app.switch('deepseek','deepseek-v4-pro')
        self.assertEqual(self.config()['web_search'],'live')

    def test_gpt_implicit_default_is_restored_as_missing_key(self):
        self.configure(provider='openai',model='gpt-6.1-sol')
        self.app.switch('deepseek','deepseek-v4-pro')
        self.app.set_web_search(True)
        self.app.switch('openai','gpt-6.1-sol')
        self.assertNotIn('web_search',self.config())

    def test_restore_recovers_search_mode_and_saved_preference(self):
        self.configure(mode='disabled')
        result=self.app.set_web_search(True)
        self.app.restore(result['backup_id'])
        self.assertEqual(self.config()['web_search'],'disabled')
        self.app.switch('openai','gpt-6.1-sol')
        self.app.switch('deepseek','deepseek-v4-pro')
        self.assertEqual(self.config()['web_search'],'disabled')

    def test_interrupted_toggle_restores_config_and_preference(self):
        from switcher.backups import atomic_write
        self.configure(mode='disabled')
        def interrupt(path,content):
            atomic_write(path,content)
            if path==self.app.search_file:raise SystemExit('simulated exit')
        with patch('switcher.core.atomic_write',side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.set_web_search(True)
        self.app.restore(json.loads(self.app.pending.read_text())['backup_id'])
        self.assertEqual(self.config()['web_search'],'disabled')
        self.app.switch('openai','gpt-6.1-sol')
        self.app.switch('deepseek','deepseek-v4-pro')
        self.assertEqual(self.config()['web_search'],'disabled')

    def test_database_failure_rolls_back_search_config_and_state(self):
        self.configure(mode='disabled')
        before=(self.home/'config.toml').read_bytes()
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        with self.assertRaises(sqlite3.IntegrityError):self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(self.app.search_preferences(),{})

    def test_toggle_checks_closed_state_again_after_backup(self):
        self.configure()
        self.app.require_closed=True
        with patch('switcher.core.desktop_state',side_effect=['stopped','running']):
            with self.assertRaises(ValueError):self.app.set_web_search(True)
        self.assertNotIn('web_search',self.config())

    def test_lightweight_status_has_no_backup_scan_or_keychain_read(self):
        self.configure(mode='live')
        with (patch.object(self.app.store,'list',side_effect=AssertionError('heavy scan')),
             patch.object(self.keychain,'get',side_effect=AssertionError('keychain')),
             patch('switcher.core.desktop_state',return_value='running')):
            status=self.app.runtime_status()
        self.assertEqual(status['provider'],'deepseek')
        self.assertEqual(status['web_search']['state'],'on')
        self.assertTrue(status['web_search']['requires_restart'])
        self.assertNotIn('test-secret',json.dumps(status))

    def test_status_tracks_external_changes_and_unsupported_flash(self):
        self.configure(mode='live')
        self.assertEqual(self.app.runtime_status()['web_search']['state'],'on')
        self.configure(model='deepseek-flash',mode='live')
        status=self.app.runtime_status()
        self.assertEqual(status['web_search']['state'],'unsupported')
        self.assertFalse(status['web_search']['can_enable'])
        self.assertTrue(status['web_search']['can_disable'])
        self.configure(provider='openai',model='gpt-6.1-sol')
        self.assertEqual(self.app.runtime_status()['provider'],'openai')
        self.assertEqual(self.app.runtime_status()['web_search']['state'],'inactive')

    def test_active_profile_cannot_be_silently_overridden_by_toggle(self):
        self.configure()
        with (self.home/'config.toml').open('a') as stream:
            stream.write('\n[profiles.selected]\nweb_search="live"\n')
        from switcher.core import rewrite_config
        path=self.home/'config.toml'
        path.write_text(rewrite_config(path.read_text(),{'profile':'selected'}))
        before=path.read_bytes()
        self.assertEqual(self.app.runtime_status()['web_search']['state'],'profile')
        with self.assertRaisesRegex(ValueError,'profile'):self.app.set_web_search(False)
        self.assertEqual(path.read_bytes(),before)

    def test_alias_provider_is_reported_as_deepseek(self):
        self.app.switch('deepseek','deepseek-v4-pro')
        self.assertEqual(self.app.runtime_status()['provider'],'deepseek')

    def test_enable_fixes_disabled_catalog_and_restore_keeps_other_catalog_edits(self):
        self.configure(mode='live')
        catalog=json.loads((self.home/'models.json').read_text())
        pro=next(m for m in catalog['models'] if m['slug']=='deepseek-v4-pro')
        pro['supports_search_tool']=False
        (self.home/'models.json').write_text(json.dumps(catalog))
        self.assertEqual(self.app.runtime_status()['web_search']['state'],'catalog')
        result=self.app.set_web_search(True)
        self.assertEqual(self.app.runtime_status()['web_search']['state'],'on')
        catalog=json.loads((self.home/'models.json').read_text())
        catalog['models'][0]['description']='new description after backup'
        (self.home/'models.json').write_text(json.dumps(catalog))
        self.app.restore(result['backup_id'])
        restored=json.loads((self.home/'models.json').read_text())
        self.assertFalse(next(m for m in restored['models'] if m['slug']=='deepseek-v4-pro')['supports_search_tool'])
        self.assertEqual(restored['models'][0]['description'],'new description after backup')

    def test_unreadable_catalog_does_not_claim_search_on(self):
        self.configure(mode='live')
        (self.home/'models.json').write_text('invalid json')
        status=self.app.runtime_status()['web_search']
        self.assertEqual(status['state'],'catalog-error')
        self.assertFalse(status['can_enable'])
        with self.assertRaises(ValueError):self.app.set_web_search(True)
        self.assertEqual(self.app.store.list(),[])

    def test_interrupted_catalog_update_can_be_undone(self):
        from switcher.backups import atomic_write
        self.configure(mode='disabled')
        original={'models':[{'slug':'deepseek-flash'},{'slug':'deepseek-v4-pro','supports_search_tool':False}]}
        (self.home/'models.json').write_text(json.dumps(original))
        def interrupt(path,content):
            atomic_write(path,content)
            if path.resolve()==(self.home/'models.json').resolve():raise SystemExit('simulated catalog exit')
        with patch('switcher.core.atomic_write',side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.set_web_search(True)
        self.app.restore(json.loads(self.app.pending.read_text())['backup_id'])
        self.assertEqual(json.loads((self.home/'models.json').read_text()),original)
        self.assertEqual(self.config()['web_search'],'disabled')

    def test_profile_status_uses_effective_provider(self):
        self.configure(provider='openai',model='gpt-6.1-sol')
        from switcher.core import rewrite_config
        path=self.home/'config.toml'
        path.write_text(rewrite_config(path.read_text(),{'profile':'ds'})+
                        '\n[profiles.ds]\nmodel_provider="deepseek"\nmodel="deepseek-v4-pro"\nweb_search="live"\n')
        self.assertEqual(self.app.status()['provider'],'deepseek')
        self.assertEqual(self.app.status()['model'],'deepseek-v4-pro')

    def test_corrupt_preferences_stop_before_any_write(self):
        self.configure()
        self.app.search_file.write_text('[]')
        before=(self.home/'config.toml').read_bytes()
        with self.assertRaises(ValueError):self.app.set_web_search(True)
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(self.app.store.list(),[])

    def test_catalog_recovery_does_not_use_restored_gpt_custom_catalog_path(self):
        self.configure(provider='openai',model='gpt-6.1-sol',mode='cached')
        from switcher.core import rewrite_config
        custom=self.root/'gpt-custom-models.json'
        custom.write_text('{"models":[]}')
        path=self.home/'config.toml'
        path.write_text(rewrite_config(path.read_text(),{'model_catalog_json':str(custom)}))
        catalog=json.loads((self.home/'models.json').read_text())
        next(m for m in catalog['models'] if m['slug']=='deepseek-v4-pro')['supports_search_tool']=False
        (self.home/'models.json').write_text(json.dumps(catalog))
        self.app.search_file.write_text('{"deepseek":"live"}')
        result=self.app.switch('deepseek','deepseek-v4-pro')
        self.app.restore(result['backup_id'])
        self.assertEqual(self.config()['model_catalog_json'],str(custom))
        self.assertEqual(custom.read_text(),'{"models":[]}')
        self.assertFalse(next(m for m in json.loads((self.home/'models.json').read_text())['models']
                              if m['slug']=='deepseek-v4-pro')['supports_search_tool'])


class DeepSeekRepairTests(CodexHomeFixture):
    def setUp(self):
        super().setUp()
        self.path=self.home/'sessions/one.jsonl'
        self.source=history(self.header)
        self.path.write_bytes(self.source)

    def prepare(self):
        self.app.switch('openai','gpt-6.1-sol')
        from switcher.core import rewrite_config
        path=self.home/'config.toml'
        path.write_text(rewrite_config(path.read_text(),{
            'model_provider':'ds_api','model':'deepseek-flash','model_reasoning_effort':'low'}))

    def test_manual_deepseek_repair_restores_body_and_routes_keeps_model(self):
        self.prepare()
        extra=line('event_msg',{'type':'user_message','message':'later'})
        with self.path.open('ab') as stream:stream.write(extra)
        config=(self.home/'config.toml').read_bytes()
        result=self.app.repair_deepseek()
        self.assertEqual(result['compatibility']['records'],1)
        self.assertEqual(self.path.read_bytes().split(b'\n',1)[1],self.source.split(b'\n',1)[1]+extra)
        self.assertEqual((self.home/'config.toml').read_bytes(),config)
        self.assertEqual({(r['model_provider'],r['model'],r['reasoning_effort']) for r in self.app.routes()},
                         {('ds_api','deepseek-flash','low')})
        count=len(self.app.store.list())
        self.app.repair_deepseek()
        self.assertEqual(len(self.app.store.list()),count)

    def test_gpt_configuration_rejected(self):
        self.app.switch('openai','gpt-6.1-sol')
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'DeepSeek'):self.app.repair_deepseek()
        self.assertEqual(self.path.read_bytes(),before)

    def test_conflict_preserves_body_and_its_gpt_route(self):
        self.prepare()
        self.path.write_bytes(self.path.read_bytes().replace('保留推理备注'.encode(),'修改推理备注'.encode(),1))
        before=self.path.read_bytes()
        result=self.app.repair_deepseek()
        self.assertEqual(result['compatibility']['skipped'][0]['id'],'a')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.app.routes()[0]['model_provider'],'openai')

    def test_single_id_does_not_change_other_routes(self):
        self.prepare()
        self.app.repair_deepseek('a')
        self.assertEqual(self.app.routes()[1]['model_provider'],'openai')
        with self.assertRaises(ValueError):self.app.repair_deepseek('missing')

    def test_failure_rolls_back_and_recovery_keeps_appends(self):
        self.prepare()
        before=self.path.read_bytes()
        from switcher.compatibility import replace_ranges
        def interrupt(path,changes):
            replace_ranges(path,changes)
            raise SystemExit('simulated exit')
        with patch('switcher.core.replace_ranges',side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.repair_deepseek()
        extra=line('event_msg',{'type':'user_message','message':'after exit'})
        with self.path.open('ab') as stream:stream.write(extra)
        self.app.restore(json.loads(self.app.pending.read_text())['backup_id'])
        self.assertEqual(self.path.read_bytes(),before+extra)

    def test_untracked_duplicate_copy_blocks_entire_session_and_protects_backup(self):
        self.prepare()
        copy=self.path.with_name('new-copy.jsonl')
        before=self.path.read_bytes()
        copy.write_bytes(before)
        result=self.app.repair_deepseek()
        self.assertEqual(result['compatibility']['skipped'][0]['id'],'a')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(copy.read_bytes(),before)
        self.assertEqual(self.app.routes()[0]['model_provider'],'openai')
        # Even if the tracked file is restored outside the tool, the unknown copy
        # must keep the original backup protected from pruning.
        from switcher.compatibility import restore_changes,replace_ranges
        manifest=self.app.repair_manifests()[0]
        spans=manifest['compatibility']['sessions/one.jsonl']
        original=self.app.store.content(manifest['id'],'sessions/one.jsonl')
        replace_ranges(self.path,restore_changes(self.path,original,spans))
        self.assertIn(manifest['id'],self.app.repair_backups())
