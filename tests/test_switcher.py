import datetime as dt
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import contextlib

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))
from switcher.core import Switcher, rewrite_config
from switcher.backups import BackupStore


@contextlib.contextmanager
def db_connection(path):
    con=sqlite3.connect(path)
    try:
        with con:yield con
    finally:con.close()


class CodexHomeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / 'codex'
        self.home.mkdir()
        (self.home/'sessions').mkdir()
        self.header=json.dumps({'type':'session_meta','payload':{'id':'a','model_provider':'deepseek','base_instructions':{'text':'保留原始说明'}}},ensure_ascii=True).encode()+b'\n'
        self.messages=b'{"type":"message","text":"original messages"}\n'
        (self.home/'sessions'/'one.jsonl').write_bytes(self.header+self.messages)
        self.original = (self.home/'sessions'/'one.jsonl').read_bytes()
        (self.home/'config.toml').write_text('model_provider="deepseek"\nmodel="deepseek-flash"\nnotify=["keep-me"]\n[model_providers.deepseek]\nname="DeepSeek"\nbase_url="https://api.deepseek.com/"\nwire_api="responses"\nexperimental_bearer_token="test-secret"\n[model_providers.custom]\nname="deepseek"\nbase_url="https://api.deepseek.com/"\n[features]\nmulti_agent=true\n')
        (self.home/'auth.json').write_text('{"auth_mode":"chatgpt","tokens":{"test":"unchanged"}}')
        (self.home/'models.json').write_text(json.dumps({'models':[{'slug':'deepseek-flash'},{'slug':'deepseek-v4-pro'}]}))
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute('create table threads(id text primary key, model_provider text, model text, reasoning_effort text, archived integer, title text, rollout_path text)')
            db.executemany('insert into threads values(?,?,?,?,?,?,?)',[
                ('a','deepseek','deepseek-flash','high',0,'C学习',str(self.home/'sessions'/'one.jsonl')),
                ('b','custom','deepseek-flash','high',1,'归档',str(self.home/'sessions'/'one.jsonl'))])
        self.app = Switcher(self.home, require_closed=False)

    def tearDown(self):
        self.tmp.cleanup()


class SwitchTests(CodexHomeFixture):
    def rows(self):
        with db_connection(self.home/'state_5.sqlite') as db:
            return db.execute('select id,model_provider,model,archived,title from threads order by id').fetchall()

    def test_roundtrip_preserves_ids_messages_archives_and_auth(self):
        before_auth=(self.home/'auth.json').read_bytes()
        self.app.switch('openai','gpt-6.1-sol','high')
        self.assertEqual(self.rows(),[('a','openai','gpt-6.1-sol',0,'C学习'),('b','openai','gpt-6.1-sol',1,'归档')])
        self.app.switch('deepseek','deepseek-flash','high')
        self.assertEqual({r[1] for r in self.rows()},{'ds_api'})
        after=(self.home/'sessions'/'one.jsonl').read_bytes()
        self.assertEqual(after.split(b'\n',1)[1],self.messages)
        self.assertEqual(len(after),len(self.original))
        self.assertEqual((self.home/'auth.json').read_bytes(),before_auth)
        import tomllib
        config=tomllib.loads((self.home/'config.toml').read_text())
        self.assertEqual(config['notify'],['keep-me'])
        self.assertEqual(config['model_provider'],'ds_api')
        self.assertFalse(config['model_providers']['ds_api']['requires_openai_auth'])

    def test_restore_keeps_new_threads_and_new_messages(self):
        result=self.app.switch('openai','gpt-6.1-sol','high')
        (self.home/'sessions'/'one.jsonl').write_bytes(self.original+b'new message\n')
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute("insert into threads values('new','openai','gpt-6.1-sol','high',0,'new','')")
        self.app.restore(result['backup_id'])
        self.assertEqual(len(self.rows()),3)
        self.assertEqual(self.rows()[0][1],'ds_api')
        self.assertEqual(self.rows()[-1][1],'openai')
        self.assertTrue((self.home/'sessions'/'one.jsonl').read_bytes().endswith(b'new message\n'))

    def test_invalid_pair_cannot_change_state_or_create_backup(self):
        before=(self.home/'config.toml').read_bytes()
        with self.assertRaises(ValueError):self.app.switch('deepseek','gpt-6.1-sol','high')
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(self.app.store.list(),[])

    def test_root_key_rewrite_does_not_alter_nested_settings(self):
        text='model="old"\n# user note\n[projects.x]\nmodel="nested"\n'
        result=rewrite_config(text,{'model':'new'})
        self.assertIn('model="nested"',result)
        self.assertIn('# user note',result)
        import tomllib
        self.assertEqual(tomllib.loads(result)['model'],'new')

    def test_corrupt_snapshot_fails_before_restore(self):
        result=self.app.switch('openai','gpt-6.1-sol','high')
        manifest=self.app.store.read(result['backup_id'])
        digest=manifest['files']['config.toml']
        self.app.store.blob_path(digest).write_bytes(b'bad gzip')
        before=self.rows()
        with self.assertRaises((ValueError,OSError,EOFError)):self.app.restore(result['backup_id'])
        self.assertEqual(self.rows(),before)

    def test_status_includes_all_providers_and_archived(self):
        status=self.app.status()
        self.assertEqual(len(status['threads']),2)
        self.assertEqual(sum(t['archived'] for t in status['threads']),1)
        self.assertNotIn('test-secret',json.dumps(status))

    def test_scan_metadata_cannot_revert_provider_and_offsets_stay_valid(self):
        self.app.switch('openai','gpt-6.1-sol','high')
        after=(self.home/'sessions'/'one.jsonl').read_bytes()
        first,rest=after.split(b'\n',1)
        self.assertEqual(json.loads(first)['payload']['model_provider'],'openai')
        self.assertEqual(len(first)+1,len(self.header))
        self.assertEqual(json.loads(first)['payload']['base_instructions']['text'],'保留原始说明')
        self.assertEqual(rest,self.messages)

    def test_restore_also_restores_header_without_losing_appended_messages(self):
        result=self.app.switch('openai','gpt-6.1-sol','high')
        path=self.home/'sessions'/'one.jsonl'
        with path.open('ab') as f:f.write(b'new user message\n')
        self.app.restore(result['backup_id'])
        header,rest=path.read_bytes().split(b'\n',1)
        self.assertEqual(json.loads(header)['payload']['model_provider'],'ds_api')
        self.assertEqual(rest,self.messages+b'new user message\n')

    def test_short_provider_id_can_switch_a_compact_gpt_header_without_offset_growth(self):
        path=self.home/'sessions'/'one.jsonl'
        header=b'{"type":"session_meta","payload":{"id":"a","model_provider":"openai"}}\n'
        path.write_bytes(header+self.messages)
        self.app.switch('deepseek','deepseek-flash','high')
        after=path.read_bytes()
        self.assertEqual(len(after),len(header)+len(self.messages))
        self.assertEqual(json.loads(after.split(b'\n',1)[0])['payload']['model_provider'],'ds_api')

    def test_compatible_transcript_excludes_reasoning_and_tool_payloads(self):
        with db_connection(self.home/'thread_history_1.sqlite') as db:
            db.execute('create table thread_items(thread_id text,rollout_ordinal integer,item_type text,item_json text)')
            items=[{'type':'userMessage','content':[{'type':'text','text':'解释数组'}]},
                   {'type':'reasoning','content':[{'text':'PRIVATE_REASONING'}]},
                   {'type':'commandExecution','output':'PRIVATE_TOOL_DATA'},
                   {'type':'agentMessage','text':'数组按下标访问。'}]
            db.executemany('insert into thread_items values(?,?,?,?)',
                           [('a',i,item['type'],json.dumps(item)) for i,item in enumerate(items)])
        transcript=self.app.transcript('a')
        self.assertIn('解释数组',transcript)
        self.assertIn('数组按下标访问。',transcript)
        self.assertNotIn('PRIVATE_REASONING',transcript)
        self.assertNotIn('PRIVATE_TOOL_DATA',transcript)

    def test_database_failure_rolls_back_headers_and_config(self):
        before=self.config_bytes=(self.home/'config.toml').read_bytes()
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'test rejection'); end")
        with self.assertRaises(sqlite3.IntegrityError):self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual((self.home/'sessions'/'one.jsonl').read_bytes(),self.original)
        self.assertTrue(self.app.pending.exists())

    def test_unrelated_custom_provider_is_not_migrated(self):
        path=self.home/'config.toml'
        text=path.read_text().replace('[model_providers.custom]\nname="deepseek"\nbase_url="https://api.deepseek.com/"','[model_providers.custom]\nname="Other"\nbase_url="https://other.example/"')
        path.write_text(text)
        self.app.switch('openai','gpt-6.1-sol')
        self.assertEqual(self.rows()[1][1],'custom')

    def test_replacing_deepseek_key_updates_the_active_alias_without_touching_chatgpt(self):
        self.app.switch('deepseek','deepseek-flash')
        auth=(self.home/'auth.json').read_bytes()
        self.app.save_key('replacement-test-key')
        import tomllib
        config=tomllib.loads((self.home/'config.toml').read_text())
        self.assertEqual(config['model_providers']['ds_api']['experimental_bearer_token'],'replacement-test-key')
        self.assertEqual((self.home/'auth.json').read_bytes(),auth)

    def test_interrupted_recovery_keeps_original_restore_target(self):
        result=self.app.switch('openai','gpt-6.1-sol')
        original=result['backup_id']
        self.app.pending.write_text(json.dumps({'backup_id':original}))
        with db_connection(self.home/'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'test rejection'); end")
        with self.assertRaises(sqlite3.IntegrityError):self.app.restore(original)
        self.assertEqual(json.loads(self.app.pending.read_text())['backup_id'],original)

    def test_atomic_publication_syncs_the_parent_directory(self):
        import os, stat
        from unittest.mock import patch
        from switcher.backups import atomic_write
        seen=[]
        actual=os.fsync
        def sync(fd):
            seen.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return actual(fd)
        with patch('os.fsync',side_effect=sync):atomic_write(self.root/'durable.txt',b'kept')
        self.assertTrue(any(seen),'directory publication must be synced before returning')


class KeyInputTests(CodexHomeFixture):
    """Accepted input must cover what people really paste, and errors must say why."""

    def saved_key(self):
        import tomllib
        config=tomllib.loads((self.home/'config.toml').read_text())
        return config['model_providers']['deepseek']['experimental_bearer_token']

    def test_pasted_key_with_padding_and_newline_is_cleaned(self):
        result=self.app.save_key('  sk-test-0123456789abcdef \n')
        self.assertEqual(self.saved_key(),'sk-test-0123456789abcdef')
        self.assertIn('已保存',result['message'])

    def test_inner_space_bearer_prefix_and_quotes_are_cleaned_not_rejected(self):
        result=self.app.save_key('Bearer "sk-01 23 45 67"')
        self.assertEqual(self.saved_key(),'sk-01234567')
        self.assertIn('已自动移除',result['message'])

    def test_label_full_width_punctuation_and_quotes_around_key_are_cleaned(self):
        result=self.app.save_key('DeepSeek API Key： “sk－0123456789abcdef”')
        self.assertEqual(self.saved_key(),'sk-0123456789abcdef')
        self.assertIn('已自动移除',result['message'])

    def test_multiline_paste_keeps_the_key_token(self):
        result=self.app.save_key('名称：deepseek\n密钥：sk-0123456789abcdef\n')
        self.assertEqual(self.saved_key(),'sk-0123456789abcdef')
        self.assertIn('已自动移除',result['message'])

    def test_short_input_reports_length_instead_of_a_blank_character_list(self):
        with self.assertRaises(ValueError) as caught:self.app.save_key('sk-12')
        message=str(caught.exception)
        self.assertIn('只收到 5 个字符',message)
        self.assertNotIn('（）',message)

    def test_empty_input_explains_itself(self):
        with self.assertRaises(ValueError) as caught:self.app.save_key('  \n ')
        self.assertIn('空的',str(caught.exception))

    def test_non_ascii_key_is_reported_with_the_offending_codes(self):
        with self.assertRaises(ValueError) as caught:self.app.save_key('sk-密钥abcdef')
        message=str(caught.exception)
        self.assertIn('不允许的字符',message)
        self.assertIn('U+5BC6',message)

    def test_overlong_input_is_reported(self):
        with self.assertRaises(ValueError) as caught:self.app.save_key('sk-'+'a'*400)
        self.assertIn('远超正常密钥长度',str(caught.exception))

    def test_non_string_payload_is_rejected(self):
        with self.assertRaises(ValueError):self.app.save_key(None)

    def test_non_sk_prefix_is_saved_with_a_reminder(self):
        result=self.app.save_key('my-relay-key-123456')
        self.assertEqual(self.saved_key(),'my-relay-key-123456')
        self.assertIn('不以 sk- 开头',result['message'])


class ProcessProbeTests(CodexHomeFixture):
    """An unreadable process list must never turn the whole tool into one opaque error."""

    def test_status_reports_unknown_instead_of_failing(self):
        from unittest.mock import patch
        with patch('switcher.core._process_lines',return_value=None):
            status=self.app.status()
        self.assertEqual(status['desktop_check'],'unknown')
        self.assertFalse(status['desktop_running'])

    def test_status_reports_stopped_when_list_is_readable(self):
        from unittest.mock import patch
        with patch('switcher.core._process_lines',return_value=['/usr/sbin/cfprefsd','/bin/zsh']):
            self.assertEqual(self.app.status()['desktop_check'],'stopped')

    def test_running_chatgpt_is_detected(self):
        from unittest.mock import patch
        with patch('switcher.core._process_lines',return_value=['/Applications/ChatGPT.app/Contents/MacOS/ChatGPT']):
            self.assertEqual(self.app.status()['desktop_check'],'running')

    def test_key_save_is_blocked_with_a_clear_message_when_the_probe_is_unavailable(self):
        from unittest.mock import patch
        closed=Switcher(self.home, require_closed=True)
        with patch('switcher.core._process_lines',return_value=None):
            with self.assertRaises(ValueError) as caught:closed.save_key('sk-test-0123456789')
        self.assertIn('无法确认',str(caught.exception))

    def test_key_save_is_blocked_while_chatgpt_is_running(self):
        from unittest.mock import patch
        closed=Switcher(self.home, require_closed=True)
        with patch('switcher.core._process_lines',return_value=['/Applications/ChatGPT.app/Contents/MacOS/ChatGPT']):
            with self.assertRaises(ValueError) as caught:closed.save_key('sk-test-0123456789')
        self.assertIn('⌘Q',str(caught.exception))


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.tmp.name)
        self.store=BackupStore(self.root/'backups')

    def tearDown(self):self.tmp.cleanup()

    def add(self,i):
        return self.store.create({'config.toml':f'config {i}'.encode()},[],created_at=f'2025-01-{i+1:02d}T00:00:00+00:00')

    def test_expired_snapshots_keep_newest_three(self):
        ids=[self.add(i)['id'] for i in range(7)]
        removed=self.store.prune(keep=10,days=30)
        self.assertEqual(len(removed),4)
        self.assertEqual({s['id'] for s in self.store.list()},set(ids[-3:]))

    def test_prune_never_removes_unowned_directory_or_symlink(self):
        other=self.store.root/'manual-backup';other.mkdir();(other/'important').write_text('keep')
        outside=self.root/'outside';outside.mkdir();(outside/'important').write_text('keep')
        (self.store.root/'2020-link').symlink_to(outside,target_is_directory=True)
        for i in range(5):self.add(i)
        self.store.prune(keep=3,days=1)
        self.assertTrue((other/'important').exists())
        self.assertTrue((outside/'important').exists())
        self.assertTrue((self.store.root/'2020-link').is_symlink())

    def test_shared_blob_survives_when_one_snapshot_remains(self):
        for i in range(5):self.store.create({'config.toml':b'same'},[],created_at=f'2025-01-{i+1:02d}T00:00:00+00:00')
        self.store.prune(keep=3,days=1)
        self.assertEqual(self.store.content(self.store.list()[0]['id'],'config.toml'),b'same')
        self.assertEqual(len(list(self.store.blobs.glob('*.gz'))),1)

    def test_reject_traversal_and_bad_retention_values(self):
        with self.assertRaises(ValueError):self.store.read('../outside')
        with self.assertRaises(ValueError):self.store.prune(keep=0,days=30)


if __name__=='__main__':unittest.main()
