import json
import sqlite3
from unittest.mock import patch

from test_switcher import CodexHomeFixture, db_connection


def line(kind, payload):
    return (json.dumps({'timestamp': '2026-10-18T01:00:00Z', 'type': kind,
                        'payload': payload}, ensure_ascii=False) + '\n').encode()


def history(header, model='deepseek-flash', matched=True):
    text = '保留推理备注；不执行历史任务'
    replay = line('response_item', {'type': 'reasoning', 'id': 'reason-1',
        'content': [{'type': 'reasoning_text', 'text': text}],
        'summary': [], 'encrypted_content': 'foreign-' + 'x' * 600})
    display = line('event_msg', {'type': 'item_completed', 'thread_id': 'a',
        'turn_id': 'turn-1', 'item': {'type': 'Reasoning', 'id': 'reason-1',
        'raw_content': [text if matched else 'different'], 'summary': []},
        'started_at_ms': 1, 'completed_at_ms': 2})
    message = line('response_item', {'type': 'message', 'role': 'user',
        'content': [{'type': 'input_text', 'text': '问题'},
                    {'type': 'input_image', 'image_url': 'local-reference'}]})
    tool = line('response_item', {'type': 'function_call', 'call_id': 'call-1',
                                'name': 'example', 'arguments': '{}'})
    return header + line('turn_context', {'model': model}) + message + replay + display + tool


class CompatibilityTests(CodexHomeFixture):
    def setUp(self):
        super().setUp()
        self.path = self.home / 'sessions/one.jsonl'
        self.source = history(self.header)
        self.path.write_bytes(self.source)

    def test_gpt_switch_repairs_replay_and_preserves_all_other_lines(self):
        result = self.app.switch('openai', 'gpt-6.1-sol')
        after = self.path.read_bytes().splitlines(keepends=True)
        before = self.source.splitlines(keepends=True)
        self.assertEqual(result['compatibility']['records'], 1)
        self.assertEqual(result['compatibility']['threads'], 1)
        self.assertEqual([len(x) for x in after], [len(x) for x in before])
        self.assertEqual(after[1:3], before[1:3])
        self.assertEqual(after[4:], before[4:])
        self.assertEqual(json.loads(after[3])['type'], 'event_msg')
        self.assertEqual(json.loads(after[3])['payload']['item'],
                         json.loads(after[4])['payload']['item'])
        self.assertEqual(self.app.store.content(result['backup_id'], 'sessions/one.jsonl'), self.source)

    def test_gpt_reasoning_and_deepseek_destination_are_not_transformed(self):
        self.path.write_bytes(history(self.header, model='gpt-6.1-sol'))
        before = self.path.read_bytes().split(b'\n', 1)[1]
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], before)
        self.assertEqual(result['compatibility']['records'], 0)
        self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], before)

    def test_unmatched_display_is_skipped_without_changing_its_file_or_route(self):
        self.path.write_bytes(history(self.header, matched=False))
        before = self.path.read_bytes()
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result['compatibility']['skipped'][0]['id'], 'a')
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select model_provider from threads where id='a'").fetchone()[0], 'deepseek')

    def test_manual_repair_keeps_original_id_model_and_is_idempotent(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        self.path.write_bytes(history(self.path.read_bytes().splitlines(keepends=True)[0]))
        before = (self.home / 'config.toml').read_bytes()
        result = self.app.repair_compatibility('a')
        self.assertEqual(result['compatibility']['records'], 1)
        self.assertEqual((self.home / 'config.toml').read_bytes(), before)
        self.assertEqual(self.app.repair_compatibility('a')['compatibility']['records'], 0)
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select model from threads where id='a'").fetchone()[0], 'gpt-6.1-sol')

    def test_manual_repair_rejects_deepseek_configuration(self):
        with self.assertRaisesRegex(ValueError, 'GPT'):
            self.app.repair_compatibility('a')
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_database_failure_rolls_back_repaired_body_and_header(self):
        with db_connection(self.home / 'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_restore_undoes_repair_but_preserves_appended_messages(self):
        result = self.app.switch('openai', 'gpt-6.1-sol')
        extra = line('event_msg', {'type': 'user_message', 'message': '新增消息'})
        with self.path.open('ab') as stream:
            stream.write(extra)
        self.app.restore(result['backup_id'])
        restored = self.path.read_bytes().split(b'\n', 1)[1]
        self.assertEqual(restored, self.source.split(b'\n', 1)[1] + extra)

    def test_archived_session_is_repaired_without_unarchiving(self):
        folder = self.home / 'archived_sessions'
        folder.mkdir()
        header = json.dumps({'type': 'session_meta', 'payload': {
            'id': 'b', 'model_provider': 'deepseek', 'padding': 'x' * 80}}).encode() + b'\n'
        archived = folder / 'two.jsonl'
        archived.write_bytes(history(header))
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(result['compatibility']['threads'], 2)
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select archived from threads where id='b'").fetchone()[0], 1)

    def test_interrupted_write_recovers_original_reasoning_from_backup(self):
        from switcher.compatibility import replace_ranges
        actual = replace_ranges
        def interrupt(path, changes):
            actual(path, changes)
            raise SystemExit('simulated power loss')
        # A non-Exception simulates a process that cannot finish its rollback.
        with patch('switcher.core.replace_ranges', side_effect=interrupt):
            with self.assertRaises(SystemExit):
                self.app.switch('openai', 'gpt-6.1-sol')
        journal = json.loads(self.app.pending.read_text())
        self.app.restore(journal['backup_id'])
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1],
                         self.source.split(b'\n', 1)[1])
        self.assertFalse(self.app.pending.exists())

    def test_unsupported_archive_does_not_block_supported_active_session(self):
        folder = self.home / 'archived_sessions'
        folder.mkdir()
        header = json.dumps({'type': 'session_meta', 'payload': {
            'id': 'b', 'model_provider': 'deepseek', 'padding': 'x' * 80}}).encode() + b'\n'
        archived = folder / 'unknown.jsonl'
        original = history(header, matched=False)
        archived.write_bytes(original)
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(result['migrated'], 1)
        self.assertEqual(result['compatibility']['records'], 1)
        self.assertEqual(result['compatibility']['skipped'][0]['id'], 'b')
        self.assertEqual(archived.read_bytes(), original)
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select model_provider from threads where id='b'").fetchone()[0], 'custom')

    def test_compacted_foreign_reasoning_is_reported_and_left_untouched(self):
        original = self.source + line('compacted', {'replacement_history': [
            {'type': 'reasoning', 'content': [{'type': 'reasoning_text', 'text': '压缩推理'}]}]})
        self.path.write_bytes(original)
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(result['compatibility']['records'], 0)
        self.assertIn('压缩历史', result['compatibility']['skipped'][0]['reason'])
        self.assertEqual(self.path.read_bytes(), original)

    def test_manual_unknown_format_fails_before_backup(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        self.path.write_bytes(history(self.header, matched=False))
        count = len(self.app.store.list())
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '自动修复'):
            self.app.repair_compatibility('a')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.app.store.list()), count)

    def test_missing_local_files_are_reported_instead_of_claiming_inspection(self):
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(result['compatibility']['scanned'], 1)
        self.assertEqual(result['compatibility']['unavailable'], 1)
        self.assertIn('未检查格式', result['message'])

    def test_second_switch_is_idempotent(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        before = self.path.read_bytes()
        result = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result['compatibility']['records'], 0)

    def test_restore_does_not_overwrite_an_edited_repair_span(self):
        result = self.app.switch('openai', 'gpt-6.1-sol')
        value = self.path.read_bytes().replace('保留推理备注'.encode(), '修改推理备注'.encode(), 1)
        self.path.write_bytes(value)
        with self.assertRaisesRegex(ValueError, '已被其他操作修改'):
            self.app.restore(result['backup_id'])
        self.assertEqual(self.path.read_bytes(), value)

    def test_recovery_can_be_retried_after_another_database_failure(self):
        from switcher.compatibility import replace_ranges
        actual = replace_ranges
        def interrupt(path, changes):
            actual(path, changes)
            raise SystemExit('simulated power loss')
        with patch('switcher.core.replace_ranges', side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.switch('openai', 'gpt-6.1-sol')
        journal = json.loads(self.app.pending.read_text())
        with db_connection(self.home / 'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        with self.assertRaises(sqlite3.IntegrityError):self.app.restore(journal['backup_id'])
        self.assertEqual(json.loads(self.app.pending.read_text())['compatibility_backup_id'], journal['backup_id'])
        with db_connection(self.home / 'state_5.sqlite') as db:
            db.execute('drop trigger reject_update')
        self.app.restore(journal['backup_id'])
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])

    def test_running_or_unknown_desktop_blocks_manual_repairs(self):
        self.app.require_closed = True
        for state in ('running', 'unknown'):
            with patch('switcher.core.desktop_state', return_value=state):
                with self.assertRaises(ValueError):self.app.repair_compatibility('a')
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_reopened_desktop_after_backup_blocks_all_writes(self):
        self.app.require_closed = True
        with patch('switcher.core.desktop_state', side_effect=['stopped', 'unknown']):
            with self.assertRaises(ValueError):self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_switching_back_to_deepseek_restores_original_reasoning(self):
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertNotEqual(self.path.read_bytes(), self.source)
        self.assertIn(repaired['backup_id'], self.app.repair_backups())
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1],
                         self.source.split(b'\n', 1)[1])
        self.assertEqual(result['compatibility_restore']['records'], 1)
        self.assertEqual(self.app.repair_backups(), [])

    def test_restore_on_switch_preserves_messages_appended_after_repair(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        extra = line('event_msg', {'type': 'user_message', 'message': '新增消息'})
        with self.path.open('ab') as stream:
            stream.write(extra)
        self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1],
                         self.source.split(b'\n', 1)[1] + extra)

    def test_restore_on_switch_skips_an_edited_span_and_keeps_going(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        value = self.path.read_bytes().replace('保留推理备注'.encode(), '修改推理备注'.encode(), 1)
        self.path.write_bytes(value)
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['compatibility_restore']['records'], 0)
        self.assertEqual(result['compatibility_restore']['skipped'][0]['id'], 'a')
        self.assertEqual(self.path.read_bytes(), value)

    def test_repeated_gpt_deepseek_roundtrip_is_byte_stable(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        self.app.switch('deepseek', 'deepseek-flash')
        once = self.path.read_bytes().split(b'\n', 1)[1]
        self.app.switch('openai', 'gpt-6.1-sol')
        self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], once)
        self.assertEqual(once, self.source.split(b'\n', 1)[1])

    def test_cleanup_keeps_repair_backup_until_reasoning_is_restored(self):
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        self.app.save_settings(3, 3650, True)
        for _ in range(5):
            self.app.backup()
        ids = {m['id'] for m in self.app.store.list()}
        self.assertIn(repaired['backup_id'], ids)
        self.app.switch('deepseek', 'deepseek-flash')
        self.app._cleanup()
        ids = {m['id'] for m in self.app.store.list()}
        self.assertNotIn(repaired['backup_id'], ids)

    def test_archive_movement_keeps_backup_protected_and_restores_by_id(self):
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        destination = self.home / 'archived_sessions' / self.path.name
        destination.parent.mkdir()
        self.path.rename(destination)
        with db_connection(self.home / 'state_5.sqlite') as db:
            db.execute('update threads set rollout_path=?, archived=1 where id=?', (str(destination), 'a'))
        self.app.save_settings(3, 3650, True)
        for _ in range(5):self.app.backup()
        self.assertIn(repaired['backup_id'], {m['id'] for m in self.app.store.list()})
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['compatibility_restore']['records'], 1)
        self.assertEqual(destination.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select archived from threads where id='a'").fetchone()[0], 1)

    def test_missing_repaired_file_keeps_original_backup_protected(self):
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        self.path.unlink()
        self.assertIn(repaired['backup_id'], self.app.repair_backups())
        _, stats = self.app.compatibility_restore_all()
        self.assertEqual(stats['skipped'][0]['id'], 'a')

    def test_multiple_incremental_repairs_are_all_restored(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        addition = history(self.header).split(b'\n', 1)[1]
        addition = addition.replace(b'reason-1', b'reason-2').replace(b'turn-1', b'turn-2').replace(b'call-1', b'call-2')
        with self.path.open('ab') as stream:stream.write(addition)
        second = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(second['compatibility']['records'], 1)
        preview = self.app.status()['deepseek_restore']
        self.assertEqual((preview['threads'], preview['records']), (1, 2))
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['compatibility_restore']['records'], 2)
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1] + addition)
        self.assertEqual(self.app.repair_backups(), [])

    def test_interrupted_deepseek_switch_restores_gpt_body_and_keeps_appends(self):
        from switcher.compatibility import replace_ranges
        self.app.switch('openai', 'gpt-6.1-sol')
        before = self.path.read_bytes()
        config = (self.home / 'config.toml').read_bytes()
        def interrupt(path, changes):
            replace_ranges(path, changes)
            raise SystemExit('power loss while restoring originals')
        with patch('switcher.core.replace_ranges', side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.switch('deepseek', 'deepseek-flash')
        extra = line('event_msg', {'type': 'user_message', 'message': '恢复前新增'})
        with self.path.open('ab') as stream:stream.write(extra)
        journal = json.loads(self.app.pending.read_text())
        self.app.restore(journal['backup_id'])
        self.assertEqual(self.path.read_bytes(), before + extra)
        self.assertEqual((self.home / 'config.toml').read_bytes(), config)
        self.assertFalse(self.app.pending.exists())

    def test_successful_reverse_operation_can_itself_be_restored(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        before = self.path.read_bytes()
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.app.restore(result['backup_id'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_historical_spans_are_not_restored_twice(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        self.app.switch('deepseek', 'deepseek-flash')
        self.app.switch('openai', 'gpt-6.1-sol')
        _, preview = self.app.compatibility_restore_all()
        self.assertEqual((preview['threads'], preview['records']), (1, 1))

    def test_manual_backup_restore_finds_archived_file_by_id(self):
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        destination = self.home / 'archived_sessions' / self.path.name
        destination.parent.mkdir()
        self.path.rename(destination)
        self.app.restore(repaired['backup_id'])
        self.assertEqual(destination.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])

    def test_ambiguous_moved_file_is_not_chosen_and_backup_stays_protected(self):
        repair = self.app.switch('openai', 'gpt-6.1-sol')['backup_id']
        folder = self.home / 'archived_sessions'
        folder.mkdir()
        body = self.path.read_bytes()
        self.path.unlink()
        (folder / 'first.jsonl').write_bytes(body)
        (folder / 'second.jsonl').write_bytes(body)
        plans, preview = self.app.compatibility_restore_all()
        self.assertEqual(plans, {})
        self.assertIn('多个候选', preview['skipped'][0]['reason'])
        self.assertIn(repair, self.app.repair_backups())

    def test_reused_old_path_does_not_restore_into_a_different_session(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        destination = self.home / 'archived_sessions' / self.path.name
        destination.parent.mkdir()
        self.path.rename(destination)
        other = self.header.replace(b'"a"', b'"other"') + self.messages
        self.path.write_bytes(other)
        _, preview = self.app.compatibility_restore_all()
        self.assertEqual(preview['records'], 1)
        self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(self.path.read_bytes(), other)
        self.assertEqual(destination.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])

    def test_reverse_recovery_can_be_retried_if_database_rejects_it(self):
        from switcher.compatibility import replace_ranges
        self.app.switch('openai', 'gpt-6.1-sol')
        before = self.path.read_bytes()
        def interrupt(path, changes):
            replace_ranges(path, changes)
            raise SystemExit('power loss')
        with patch('switcher.core.replace_ranges', side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.switch('deepseek', 'deepseek-flash')
        journal = json.loads(self.app.pending.read_text())
        with db_connection(self.home / 'state_5.sqlite') as db:
            db.execute("create trigger reject_update before update on threads begin select raise(ABORT,'reject'); end")
        with self.assertRaises(sqlite3.IntegrityError):self.app.restore(journal['backup_id'])
        self.assertTrue(self.app.pending.exists())
        with db_connection(self.home / 'state_5.sqlite') as db:db.execute('drop trigger reject_update')
        self.app.restore(journal['backup_id'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.app.pending.exists())

    def test_conflict_in_any_batch_preserves_the_entire_file(self):
        self.app.switch('openai', 'gpt-6.1-sol')
        addition = history(self.header).split(b'\n', 1)[1].replace(b'reason-1', b'reason-2').replace(b'turn-1', b'turn-2')
        with self.path.open('ab') as stream:stream.write(addition)
        self.app.switch('openai', 'gpt-6.1-sol')
        value = self.path.read_bytes().replace('保留推理备注'.encode(), '修改推理备注'.encode(), 1)
        self.path.write_bytes(value)
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['compatibility_restore']['records'], 0)
        self.assertEqual(len(result['compatibility_restore']['skipped']), 1)
        self.assertEqual(self.path.read_bytes(), value)

    def test_legacy_120_repair_manifest_still_restores(self):
        repair = self.app.switch('openai', 'gpt-6.1-sol')['backup_id']
        manifest = self.app.store.read(repair)
        manifest.pop('file_changes')
        (self.app.store.root / repair / 'manifest.json').write_text(json.dumps(manifest))
        self.app.restore(repair)
        self.assertEqual(self.path.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])

    def test_duplicate_session_id_files_are_counted_once_and_both_restored(self):
        second = self.home / 'sessions/another-copy.jsonl'
        second.write_bytes(self.source)
        repaired = self.app.switch('openai', 'gpt-6.1-sol')
        self.assertEqual(repaired['compatibility']['threads'], 1)
        self.assertEqual(repaired['compatibility']['records'], 2)
        destination = self.home / 'archived_sessions' / self.path.name
        destination.parent.mkdir()
        self.path.rename(destination)
        result = self.app.switch('deepseek', 'deepseek-flash')
        self.assertEqual(result['compatibility_restore']['threads'], 1)
        self.assertEqual(result['compatibility_restore']['records'], 2)
        for path in (second, destination):
            self.assertEqual(path.read_bytes().split(b'\n', 1)[1], self.source.split(b'\n', 1)[1])

    def test_reverse_crash_after_config_write_recovers_consistent_gpt_state(self):
        import tomllib
        from switcher.backups import atomic_write
        self.app.switch('openai', 'gpt-6.1-sol')
        before = self.path.read_bytes()
        def interrupt(path, value):
            atomic_write(path, value)
            if path == self.app.config:raise SystemExit('power loss before database commit')
        with patch('switcher.core.atomic_write', side_effect=interrupt):
            with self.assertRaises(SystemExit):self.app.switch('deepseek', 'deepseek-flash')
        journal = json.loads(self.app.pending.read_text())
        self.app.restore(journal['backup_id'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(tomllib.loads((self.home / 'config.toml').read_text())['model_provider'], 'openai')
        with db_connection(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute("select model_provider from threads where id='a'").fetchone()[0], 'openai')
