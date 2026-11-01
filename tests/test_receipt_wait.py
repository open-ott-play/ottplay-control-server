"""Negotiated receipt waits remove polling latency without replaying commands."""
import contextlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit


spec = importlib.util.spec_from_file_location('ott_receipt_wait', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
REQUEST_ID = 'a' * 32
MISSING = object()


class ReceiptWaitTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.now, self.sleeps, self.posts, self.gets = 0.0, [], [], []
        self.client = object.__new__(ott.Client)
        self.client.timeout = 3
        self.client.on_receipt = None
        self.client.api = Mock(side_effect=self.api)
        self.advertisement = '1000'
        self.read = lambda query, timeout: (200, {'id': REQUEST_ID, 'status': 'ok', 'data': {'volume': 35}})
        self.stack.enter_context(patch.object(ott.time, 'monotonic', side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(ott.time, 'sleep', side_effect=self.sleep))

    def sleep(self, duration):
        self.assertGreater(duration, 0)
        self.sleeps.append(duration)
        self.now += duration

    def api(self, path, payload=None, timeout=None):
        query = parse_qs(urlsplit(path).query)
        self.assertEqual(query['device_id'], ['fixture-tv'])
        if payload is not None:
            self.posts.append(payload)
            queued = {'id': REQUEST_ID, 'status': 'queued'}
            if self.advertisement is not MISSING:
                queued['receipt_wait_ms'] = self.advertisement
            return 202, queued
        self.gets.append((query, timeout, self.now))
        self.assertLessEqual(len(self.gets), 100, 'receipt polling spun without advancing its deadline')
        self.assertEqual(query['id'], [REQUEST_ID])
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, self.client.timeout - self.now + 1e-9)
        return self.read(query, timeout)

    def call(self):
        return self.client.call_receipt('fixture-tv', 'command', {'command': 'set_volume', 'volume_step': 5})

    def test_negotiated_wait_starts_immediately_and_preserves_receipt(self):
        for advertisement in ('1000', 1000, '1', 1):
            with self.subTest(advertisement=advertisement):
                self.now, self.posts, self.gets, self.sleeps = 0, [], [], []
                self.advertisement = advertisement
                result = self.call()
                self.assertEqual(result, {'request_id': REQUEST_ID, 'status': 'ok', 'data': {'volume': 35}})
                self.assertEqual(len(self.posts), 1)
                self.assertEqual(len(self.gets), 1)
                self.assertEqual(self.gets[0][0]['wait_ms'], [str(advertisement)])
                self.assertEqual(self.gets[0][2], 0)
                self.assertEqual(self.sleeps, [])

    def test_absent_or_invalid_negotiation_retains_legacy_polling(self):
        for advertisement in (MISSING, None, True, False, 0, -1, 1001, 1.0, '', '01', '+1', '1.0',
                              '1001', ' 1000', '1000 ', [], {}, '١'):
            with self.subTest(advertisement=advertisement):
                self.now, self.posts, self.gets, self.sleeps = 0, [], [], []
                self.advertisement = advertisement
                self.call()
                self.assertEqual(self.sleeps, [0.8])
                self.assertNotIn('wait_ms', self.gets[0][0])
                self.assertEqual(len(self.posts), 1)

    def test_wait_duration_is_bounded_by_the_remaining_call_budget(self):
        self.client.timeout = 0.2
        self.call()
        query, timeout, started = self.gets[0]
        self.assertEqual(started, 0)
        self.assertLessEqual(int(query['wait_ms'][0]), int(timeout * 1000))
        self.assertGreater(int(query['wait_ms'][0]), 0)
        self.assertLessEqual(timeout, 0.2)

    def test_network_retry_reuses_the_receipt_without_replaying_mutation(self):
        def read(query, timeout):
            if len(self.gets) == 1:
                raise ott.TransportError('fixture unavailable', 'dns')
            return 200, {'id': REQUEST_ID, 'status': 'ok', 'data': {}}
        self.read = read
        self.call()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.gets), 2)
        self.assertTrue(self.sleeps, 'failed long polls must not spin in a tight retry loop')
        self.assertEqual(self.gets[0][0], self.gets[1][0])

    def test_fast_pending_responses_are_paced_and_obey_the_deadline(self):
        self.client.timeout = 1
        self.read = lambda query, timeout: (202, {'status': 'pending'})
        with self.assertRaises(ott.RequestError) as failure:
            self.call()
        self.assertEqual(failure.exception.reason, 'request_pending_timeout')
        self.assertEqual(failure.exception.request_id, REQUEST_ID)
        self.assertEqual(len(self.posts), 1)
        self.assertLessEqual(len(self.gets), 20)
        self.assertTrue(self.sleeps)
        self.assertLessEqual(self.now, self.client.timeout + 1e-9)

    def test_server_waits_consume_budget_without_an_extra_fixed_poll_delay(self):
        self.client.timeout = 2.2
        def read(query, timeout):
            self.now += int(query.get('wait_ms', ['0'])[0]) / 1000
            return 202, {'status': 'pending'}
        self.read = read
        with self.assertRaises(ott.RequestError) as failure:
            self.call()
        self.assertEqual(failure.exception.reason, 'request_pending_timeout')
        self.assertEqual(len(self.posts), 1)
        self.assertLessEqual(len(self.gets), 4)
        self.assertLess(sum(self.sleeps), 0.3)
        self.assertLessEqual(self.now, self.client.timeout + 1e-9)

    def test_failed_receipt_reads_never_fall_back_to_resubmitting(self):
        for code, reason in ((400, 'controller_http_error'), (403, 'unauthorized'),
                             (404, 'receipt_missing'), (429, 'rate_limited')):
            with self.subTest(code=code):
                self.now, self.posts, self.gets = 0, [], []
                self.read = Mock(side_effect=ott.HTTPError(code))
                with self.assertRaises(ott.RequestError) as failure:
                    self.call()
                self.assertEqual(failure.exception.reason, reason)
                self.assertEqual(failure.exception.request_id, REQUEST_ID)
                self.assertEqual((len(self.posts), len(self.gets)), (1, 1))

    def test_long_poll_keeps_result_identity_and_player_rejection_checks(self):
        for result, expected in (({'id': 'b' * 32, 'status': 'ok', 'data': {}}, ott.RequestError),
                                 ({'id': REQUEST_ID, 'status': 'rejected', 'data': {'error': 'not allowed'}},
                                  ott.PlayerRejected)):
            with self.subTest(result=result):
                self.now, self.posts, self.gets = 0, [], []
                self.read = lambda query, timeout, result=result: (200, result)
                with self.assertRaises(expected):
                    self.call()
                self.assertEqual((len(self.posts), len(self.gets)), (1, 1))


if __name__ == '__main__':
    unittest.main()
