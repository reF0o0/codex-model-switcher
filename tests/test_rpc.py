from collections import deque
import unittest

from switcher.rpc import Rpc
from unittest.mock import patch


class RpcTests(unittest.TestCase):
    def test_empty_transport_exposes_a_distinct_receive_timeout(self):
        from switcher.rpc import RpcTimeoutError
        clock = [0.0]
        class EmptySelector:
            def select(self, timeout):clock[0] += timeout; return []
        rpc = Rpc.__new__(Rpc)
        rpc.events = deque(); rpc.buffer = b''; rpc.selector = EmptySelector()
        with patch('time.monotonic', lambda:clock[0]):
            with self.assertRaises(RpcTimeoutError):rpc.receive(45)
        self.assertEqual(clock[0], 45)
    def test_completion_before_rpc_reply_is_preserved_for_bridge(self):
        rpc = Rpc.__new__(Rpc)
        rpc.ident = 0; rpc.events = deque(); rpc.send = lambda data: None
        completion = {'method':'turn/completed', 'params':{'threadId':'a','turn':{'id':'t','status':'completed'}}}
        data = iter([completion, {'id':1,'result':{'turn':{'id':'t','status':'inProgress'}}}])
        rpc._receive = lambda timeout: next(data)
        self.assertEqual(rpc.call('turn/start', {})['turn']['id'], 't')
        self.assertEqual(rpc.receive(), completion)
