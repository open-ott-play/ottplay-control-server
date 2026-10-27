"""Actual RPC failures retain safe provenance without replaying requests."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_group_request_diagnostics', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / 'contracts/local-media-v1.json').read_text())
SESSION_CONTRACT = json.loads((ROOT / 'contracts/local-media-session-v1.json').read_text())
PRIVATE = 'https://private.example/?token=never-project-this'


class GroupRequestDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.posts, self.requests, self.failures, self.reads = [], {}, {}, {}
        self.client = object.__new__(ott.Client)
        self.client.config = {'players': {f't{i}': f'dev_{i}' for i in range(1, 5)}}
        self.client.credentials = {'devices': []}
        self.client.timeout, self.client.on_receipt = 30, None
        self.client.api = mock.Mock(side_effect=self.api)
        self.clock = mock.patch.object(ott.time, 'monotonic', side_effect=lambda: self.now)
        self.sleep = mock.patch.object(ott.time, 'sleep', side_effect=self.advance)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def advance(self, duration):
        self.now += duration

    def api(self, path, payload=None, timeout=None):
        query = ott.urllib.parse.parse_qs(ott.urllib.parse.urlsplit(path).query)
        device = query['device_id'][0]
        if payload is not None:
            self.posts.append((device, payload['action'], payload['params']))
            if self.failures.get((device, payload['action'])) == 'enqueue_dns':
                raise ott.TransportError(PRIVATE, 'dns')
            request_id = f'{len(self.requests) + 1:032x}'
            self.requests[request_id] = (device, payload['action'], payload['params'])
            return 202, {'id': request_id}
        request_id = query['id'][0]
        target, action, params = self.requests[request_id]
        self.assertEqual(target, device)
        mode = self.failures.get((device, action))
        count = self.reads.get(request_id, 0)
        self.reads[request_id] = count + 1
        if mode in ('dns_then_pending', 'dns_then_http', 'dns_then_deadline') and count == 0:
            raise ott.TransportError(PRIVATE, 'dns')
        if mode in ('pending', 'dns_then_pending'):
            return 202, {'status': 'pending'}
        if mode in ('deadline', 'dns_then_deadline', 'dns_then_http'):
            self.advance(timeout)
            if mode == 'dns_then_http':
                raise ott.HTTPError(503)
            raise ott.TransportError(PRIVATE, 'deadline')
        if mode in ('receipt_http400', 'receipt_missing'):
            raise ott.HTTPError(400 if mode == 'receipt_http400' else 404)
        if mode == 'invalid_response':
            return 200, {'id': PRIVATE, 'status': 'ok', 'data': {}}
        runtime = device.replace('_', '-')
        if action == 'capabilities':
            data = {'version': 1, 'player': {'version': 'test', 'platform': 'tauri', 'runtime': runtime},
                    'lifecycle': [], 'input': [], 'playback': [], 'local_media': CONTRACT['capability'],
                    'local_media_progressive': {'version': 1}, 'local_media_session': {'version': 1}}
        else:
            self.assertEqual(action, 'local_media')
            self.assertEqual(params, {'op': 'shuffle', 'source': 'Movies', 'runtime': runtime, 'progressive': True, 'session': True})
            data = dict(SESSION_CONTRACT['shuffle_result'], runtime=runtime)
        return 200, {'id': request_id, 'status': 'ok', 'data': data}

    def run_cli(self, selector='t1,t2,t3,t4'):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=self.client.config), \
                mock.patch.object(ott, 'Client', return_value=self.client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-t', '5', '-j', selector, 'local', 'shuffle', 'Movies'])
        self.assertEqual(self.client.timeout, 30)
        self.assertIsNone(self.client.on_receipt)
        return code, out.getvalue(), err.getvalue()

    def test_group_retains_actual_rpc_without_receipt_flag_and_continues_once(self):
        self.failures = {('dev_1', 'capabilities'): 'pending',
                         ('dev_2', 'local_media'): 'deadline',
                         ('dev_3', 'capabilities'): 'enqueue_dns'}
        code, out, err = self.run_cli()
        result = json.loads(out)
        self.assertEqual((code, err, result['action'], result['succeeded'], result['failed']),
                         (1, '', 'local_media', 1, 3))
        rows = result['results']
        self.assertEqual(rows[0]['request'], {'action': 'capabilities', 'request_id': f'{1:032x}',
                                            'stage': 'receipt', 'reason': 'request_pending_timeout'})
        self.assertEqual(rows[1]['request'], {'action': 'local_media', 'request_id': f'{3:032x}',
                                            'stage': 'receipt', 'reason': 'controller_unavailable',
                                            'transport_category': 'deadline'})
        self.assertEqual(rows[2]['request'], {'action': 'capabilities', 'request_id': None,
                                            'stage': 'enqueue', 'reason': 'controller_unavailable',
                                            'transport_category': 'dns'})
        self.assertNotIn('request', rows[3])
        self.assertTrue(rows[3]['ok'])
        self.assertEqual([(device, action) for device, action, _ in self.posts],
                         [('dev_1', 'capabilities'), ('dev_2', 'capabilities'), ('dev_2', 'local_media'),
                          ('dev_3', 'capabilities'), ('dev_4', 'capabilities'), ('dev_4', 'local_media')])
        self.assertTrue(all(params == SESSION_CONTRACT['probe_request']['params']
                            for _, action, params in self.posts if action == 'capabilities'))
        self.assertNotIn(PRIVATE, out + err)
        self.assertAlmostEqual(self.now, 11.6)

    def test_pending_and_http_recovery_clear_previous_transport_category(self):
        for mode, reason, category in (('dns_then_pending', 'request_pending_timeout', None),
                                       ('dns_then_http', 'controller_http_error', None),
                                       ('dns_then_deadline', 'controller_unavailable', 'deadline')):
            with self.subTest(mode=mode):
                self.failures[('dev_1', 'capabilities')] = mode
                code, out, err = self.run_cli('t1,t2')
                rows = json.loads(out)['results']
                self.assertEqual((code, err, rows[0]['request']['reason'], rows[1]['ok']), (1, '', reason, True))
                self.assertEqual(rows[0]['request'].get('transport_category'), category)
                self.assertNotIn('request', rows[1])

    def test_terminal_receipt_failures_keep_id_and_do_not_retry_caps_or_send_local(self):
        for mode, reason in (('receipt_http400', 'controller_http_error'),
                             ('receipt_missing', 'receipt_missing'), ('invalid_response', 'invalid_response')):
            with self.subTest(mode=mode):
                self.failures[('dev_1', 'capabilities')] = mode
                first_id = f'{len(self.requests) + 1:032x}'
                before = len(self.posts)
                code, out, err = self.run_cli('t1,t2')
                first, second = json.loads(out)['results']
                self.assertEqual((code, err, second['ok']), (1, '', True))
                self.assertEqual(first['request'], {'action': 'capabilities', 'request_id': first_id,
                                                   'stage': 'receipt', 'reason': reason})
                self.assertEqual([(d, a) for d, a, _ in self.posts[before:]],
                                 [('dev_1', 'capabilities'), ('dev_2', 'capabilities'), ('dev_2', 'local_media')])

    def test_single_player_json_failure_behavior_is_unchanged(self):
        self.failures[('dev_1', 'capabilities')] = 'pending'
        code, out, err = self.run_cli('t1')
        self.assertEqual((code, out), (1, ''))
        self.assertEqual(err, 'Error: No player response was received before the deadline. '
                         'This does not establish that the player is offline. The request may still execute '
                         'before its TTL expires; do not repeat the change blindly.\n')

    def test_diagnostic_projection_rejects_untrusted_fields(self):
        error = ott.RequestError(PRIVATE, [PRIVATE], 'unchanged text', PRIVATE, transport_category=PRIVATE)
        error.action = PRIVATE
        self.assertEqual(ott.request_error_metadata(error), {'action': 'unknown', 'request_id': None,
                                                           'stage': 'unknown', 'reason': 'unknown'})
        error.reason = 'controller_unavailable'
        self.assertEqual(ott.request_error_metadata(error)['transport_category'], 'unknown')
        self.assertNotIn(PRIVATE, json.dumps(ott.request_error_metadata(error)))


class TransportCategoryTest(unittest.TestCase):
    def client(self):
        client = object.__new__(ott.Client)
        client.server, client.token, client.timeout = 'https://controller.example', 'synthetic', 5
        client.opener = mock.Mock()
        return client

    def test_typed_network_categories_never_parse_private_messages(self):
        cases = [(ott.socket.gaierror(-2, PRIVATE), 'dns'),
                 (ott.ssl.SSLError(PRIVATE), 'tls'), (ConnectionResetError(PRIVATE), 'socket'),
                 (TimeoutError(PRIVATE), 'socket'), (ott.http.client.BadStatusLine(PRIVATE), 'protocol'),
                 (ott.urllib.error.URLError(PRIVATE), 'unknown')]
        for raw, category in cases:
            for error in (raw, ott.urllib.error.URLError(raw)):
                with self.subTest(error=type(error).__name__, category=category):
                    client = self.client()
                    client.opener.open.side_effect = error
                    with mock.patch.object(ott.time, 'monotonic', return_value=0):
                        with self.assertRaises(ott.TransportError) as caught:
                            client._api('/api/requests', None, 5, 5, threading.Event())
                    self.assertEqual(caught.exception.category, category)
                    self.assertEqual(str(caught.exception), 'Server unavailable; check the address and connection')

    def test_explicit_deadline_and_truncated_body_have_separate_categories(self):
        client = self.client()
        with mock.patch.object(ott.time, 'monotonic', return_value=5):
            with self.assertRaises(ott.TransportError) as caught:
                client._api('/api/requests', None, 5, 5, threading.Event())
        self.assertEqual(caught.exception.category, 'deadline')
        client.opener.open.assert_not_called()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read1.return_value = b''
        response.length = 10
        client.opener.open.return_value = response
        with mock.patch.object(ott.time, 'monotonic', return_value=0):
            with self.assertRaises(ott.TransportError) as caught:
                client._api('/api/requests', None, 5, 5, threading.Event())
        self.assertEqual(caught.exception.category, 'truncated')

    def test_caller_wall_deadline_category_is_explicit(self):
        client = self.client()
        with mock.patch.object(ott.threading, 'Thread'), mock.patch.object(ott.queue, 'Queue') as outcome:
            outcome.return_value.get.side_effect = ott.queue.Empty
            with self.assertRaises(ott.TransportError) as caught:
                client.api('/api/requests')
        self.assertEqual(caught.exception.category, 'deadline')
        client.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
