import json
from unittest.mock import patch

from test_switcher import CodexHomeFixture, db_connection


class FakeRpc:
    calls = []
    outcome = 'failed'
    last_status = 'failed'
    fail_method = None

    def __init__(self, home):pass
    def __enter__(self):return self
    def __exit__(self, *args):pass
    def call(self, method, params):
        self.calls.append((method, params))
        if method == self.fail_method:raise ValueError('simulated transport failure sk-do-not-display')
        if method == 'thread/start':return {'thread': {'id': 'linked-1'}}
        if method == 'thread/resume':return {'thread': {'id': params['threadId']}}
        if method == 'thread/turns/list':return {'data': [{'id': 't1', 'status': self.last_status}]}
        if method == 'turn/start':return {'turn': {'id': 't1', 'status': 'inProgress'}}
        return {}
    def receive(self, timeout=45):
        return {'method':'turn/completed', 'params': {'threadId':'linked-1',
                'turn': {'id':'t1', 'status': self.outcome, 'error': {'message':'insufficient_quota'}}}}


class BridgeTests(CodexHomeFixture):
    def setUp(self):
        super().setUp()
        FakeRpc.calls = []; FakeRpc.outcome = 'failed'; FakeRpc.last_status = 'failed'; FakeRpc.fail_method = None
        with db_connection(self.home/'thread_history_1.sqlite') as db:
            db.execute('create table thread_items(thread_id text, item_type text, item_json text, rollout_ordinal integer)')
            db.execute('insert into thread_items values(?,?,?,?)',
                       ('a','userMessage',json.dumps({'content':[{'type':'text','text':'learn Java'}]}),0))

    def test_silent_intervals_do_not_end_validation_before_overall_deadline(self):
        from switcher.rpc import RpcTimeoutError
        clock = [0.0]
        class SlowRpc(FakeRpc):
            def receive(self, timeout=45):
                if clock[0] < 90:
                    clock[0] += timeout
                    raise RpcTimeoutError('等待 Codex 后端超时；原会话保留')
                return {'method':'turn/completed','params':{'threadId':'linked-1',
                        'turn':{'id':'t1','status':'completed'}}}
        with patch('switcher.rpc.Rpc', SlowRpc), patch('time.monotonic', lambda:clock[0]):
            result = self.app.bridge('a')
        self.assertTrue(result['completed'])
        self.assertEqual(clock[0], 90)

    def test_overall_deadline_expires_at_150_seconds_with_network_advice(self):
        from switcher.rpc import RpcTimeoutError
        clock = [0.0]
        class SilentRpc(FakeRpc):
            def receive(self, timeout=45):
                clock[0] += timeout
                raise RpcTimeoutError('等待 Codex 后端超时；原会话保留')
        with patch('switcher.rpc.Rpc', SilentRpc), patch('time.monotonic', lambda:clock[0]):
            result = self.app.bridge('a')
        self.assertFalse(result['completed'])
        self.assertEqual(clock[0], 150)
        self.assertEqual(result['category'], 'network')

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_failed_retry_reuses_thread_and_does_not_reinject_history(self):
        first = self.app.bridge('a')
        self.assertFalse(first['completed'])
        FakeRpc.outcome = 'completed'
        second = self.app.bridge('a')
        self.assertTrue(second['completed'])
        self.assertEqual(first['thread_id'], second['thread_id'])
        self.assertEqual(sum(m == 'thread/start' for m, _ in FakeRpc.calls), 1)
        self.assertEqual(sum(m == 'thread/inject_items' for m, _ in FakeRpc.calls), 1)
        self.assertIn('thread/resume', [m for m, _ in FakeRpc.calls])
        self.assertEqual(len(list((self.app.data/'continuations').glob('*.md'))), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_completed_existing_thread_does_not_spend_another_turn(self):
        FakeRpc.outcome = 'completed'
        self.app.bridge('a')
        FakeRpc.last_status = 'completed'
        result = self.app.bridge('a')
        self.assertTrue(result['completed'])
        self.assertEqual(sum(m == 'turn/start' for m, _ in FakeRpc.calls), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_inflight_existing_turn_is_not_duplicated(self):
        self.app.bridge('a')
        FakeRpc.last_status = 'inProgress'
        result = self.app.bridge('a')
        self.assertFalse(result['completed'])
        self.assertEqual(sum(m == 'turn/start' for m, _ in FakeRpc.calls), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_link_is_saved_before_setup_failure_and_reused(self):
        FakeRpc.fail_method = 'thread/name/set'
        result = self.app.bridge('a')
        self.assertFalse(result['completed'])
        self.assertNotIn('sk-do-not-display', json.dumps(result))
        self.assertEqual(json.loads((self.app.data/'links.json').read_text())['a']['deepseek'], 'linked-1')
        FakeRpc.fail_method = None; FakeRpc.outcome = 'completed'
        self.app.bridge('a')
        self.assertEqual(sum(m == 'thread/start' for m, _ in FakeRpc.calls), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_uncertain_injection_does_not_duplicate_historical_text(self):
        FakeRpc.fail_method = 'thread/inject_items'
        self.app.bridge('a')
        FakeRpc.fail_method = None
        result = self.app.bridge('a')
        self.assertFalse(result['completed'])
        self.assertEqual(sum(m == 'thread/inject_items' for m, _ in FakeRpc.calls), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_legacy_link_is_resumed_instead_of_replaced(self):
        (self.app.data/'links.json').write_text(json.dumps({'a':{'deepseek':'linked-1'}}))
        FakeRpc.outcome = 'completed'
        result = self.app.bridge('a')
        self.assertTrue(result['completed'])
        self.assertFalse(any(m == 'thread/start' for m, _ in FakeRpc.calls))
        self.assertFalse(any(m == 'thread/inject_items' for m, _ in FakeRpc.calls))

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_failed_resume_does_not_create_a_replacement(self):
        self.app.bridge('a')
        FakeRpc.fail_method = 'thread/resume'
        result = self.app.bridge('a')
        self.assertFalse(result['completed'])
        self.assertEqual(sum(m == 'thread/start' for m, _ in FakeRpc.calls), 1)

    @patch('switcher.rpc.Rpc', FakeRpc)
    def test_changed_model_requires_new_verification_on_same_thread(self):
        FakeRpc.outcome = 'completed'
        self.app.bridge('a')
        FakeRpc.last_status = 'completed'
        self.app.switch('deepseek', 'deepseek-v4-pro')
        self.assertEqual(self.app.status()['threads'][0]['continuation_state'], 'unverified')
        result = self.app.bridge('a')
        self.assertTrue(result['completed'])
        self.assertEqual(sum(m == 'thread/start' for m, _ in FakeRpc.calls), 1)
        self.assertEqual(sum(m == 'turn/start' for m, _ in FakeRpc.calls), 2)
