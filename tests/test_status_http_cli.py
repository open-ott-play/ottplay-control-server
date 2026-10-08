"""Verify combined status reads through the real private-config HTTP client."""
import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit


spec = importlib.util.spec_from_file_location(
    'ott_status_http', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

DEVICE = 'dev_status_http_fixture'
TOKEN = 'synthetic-status-http-admin-token'
PLAYER = {'version': '1.1.52-beta.48', 'platform': 'browser', 'runtime': 'page-one'}
STATUS = {'ready': True, 'volume': 35, 'player': PLAYER}
CAPABILITIES = {'version': 1, 'player': PLAYER, 'lifecycle': ['reload_player'],
                'input': ['up', 'ok'], 'playback': []}


@contextlib.contextmanager
def serve(handler):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.daemon_threads = True
    listener = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    listener.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        listener.join(1)


class StatusHttpCliTest(unittest.TestCase):
    def run_status(self, redirect=None):
        events, sink_requests, timeouts, clients = [], [], [], []
        receipts = {}

        class Sink(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                sink_requests.append((self.command, self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            do_POST = do_GET

        with serve(Sink) as sink_address:
            class Controller(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def reply(self, code, data, location=None):
                    body = json.dumps(data).encode()
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    if location:
                        self.send_header('Location', location)
                    self.end_headers()
                    self.wfile.write(body)

                def validate_request(self, payload=None):
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    events.append((self.command, parsed.path, query,
                                   self.headers.get('Authorization'), payload))
                    if (parsed.path != '/mounted/api/requests' or query.get('device_id') != [DEVICE]
                            or self.headers.get('Authorization') != 'Bearer ' + TOKEN):
                        self.reply(400, {})
                        return None
                    return query

                def do_POST(self):
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.validate_request(payload) is None:
                        return
                    if payload == {'action': 'status', 'params': {}}:
                        request_id, data = 'a' * 32, STATUS
                    elif payload == {'action': 'capabilities', 'params': {}}:
                        if redirect == 'enqueue':
                            self.reply(303, {'error': TOKEN}, sink_address + '/stolen')
                            return
                        request_id, data = 'b' * 32, CAPABILITIES
                    else:
                        self.reply(400, {})
                        return
                    receipts[request_id] = {'reads': 0, 'data': data}
                    self.reply(202, {'id': request_id})

                def do_GET(self):
                    query = self.validate_request()
                    if query is None:
                        return
                    request_id = query.get('id', [''])[0]
                    receipt = receipts.get(request_id)
                    if receipt is None:
                        self.reply(404, {})
                        return
                    receipt['reads'] += 1
                    if request_id == 'b' * 32 and redirect == 'receipt':
                        self.reply(302, {'error': TOKEN}, sink_address + '/stolen')
                    elif receipt['reads'] == 1:
                        self.reply(202, {'status': 'pending'})
                    else:
                        self.reply(200, {'id': request_id, 'status': 'ok', 'data': receipt['data']})

            with tempfile.TemporaryDirectory() as directory, serve(Controller) as address:
                credentials = Path(directory) / 'server.json'
                config = Path(directory) / 'cli.json'
                credentials.write_text(json.dumps({'admin_token': TOKEN, 'devices': [{'id': DEVICE}]}))
                config.write_text(json.dumps({'server': address + '/mounted',
                                              'server_config': str(credentials), 'players': {'tv': DEVICE}}))
                credentials.chmod(0o600)
                config.chmod(0o600)
                before = (config.read_bytes(), credentials.read_bytes())
                output, errors = io.StringIO(), io.StringIO()
                real_sleep, real_api = time.sleep, ott.Client.api
                # Cross a clock tick even on platforms with coarse monotonic
                # resolution, while keeping the fixture's polling accelerated.
                poll_pause = max(0.005, 2 * time.get_clock_info('monotonic').resolution)

                def api(client, path, payload=None, timeout=None):
                    clients.append(client)
                    timeouts.append(timeout)
                    return real_api(client, path, payload, timeout)

                # Preserve real HTTP and monotonic deadlines; only shorten the
                # intentional poll pause. The spy observes, never replaces, IO.
                with mock.patch.object(ott.Client, 'api', new=api), \
                        mock.patch.object(ott.time, 'sleep', side_effect=lambda seconds: real_sleep(min(seconds, poll_pause))), \
                        mock.patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = ott.main(['--config', str(config), '--timeout', '2', '--json', 'tv', 'status'])
                self.assertEqual((config.read_bytes(), credentials.read_bytes()), before)

        self.assertEqual((code, errors.getvalue()), (0, ''))
        self.assertNotIn(TOKEN, output.getvalue() + errors.getvalue())
        self.assertEqual(sink_requests, [], 'No redirected request or administrator token may reach the other origin')
        self.assertTrue(clients)
        self.assertTrue(all(client is clients[0] for client in clients))
        self.assertEqual(clients[0].timeout, 2)
        self.assertTrue(all(event[1] == '/mounted/api/requests' and event[2]['device_id'] == [DEVICE]
                            and event[3] == 'Bearer ' + TOKEN for event in events))
        self.assertEqual([event[4] for event in events if event[0] == 'POST'],
                         [{'action': 'status', 'params': {}}, {'action': 'capabilities', 'params': {}}])
        return json.loads(output.getvalue()), events, timeouts

    def test_status_then_capabilities_poll_exact_ids_with_one_shared_budget(self):
        result, events, budgets = self.run_status()
        self.assertEqual(result, dict(STATUS, capabilities=CAPABILITIES))
        self.assertEqual([(event[0], event[2].get('id')) for event in events],
                         [('POST', None), ('GET', ['a' * 32]), ('GET', ['a' * 32]),
                          ('POST', None), ('GET', ['b' * 32]), ('GET', ['b' * 32])])
        self.assertEqual(budgets[0], 2)
        self.assertEqual(len(budgets), len(events))
        # Adjacent reads can share a monotonic clock tick. The secondary POST
        # must still inherit the spent budget instead of starting a fresh one.
        self.assertTrue(all(0 < later <= earlier for earlier, later in zip(budgets, budgets[1:])))
        self.assertLess(budgets[3], budgets[0])
        self.assertLessEqual(budgets[3], budgets[2])

    def test_secondary_redirects_preserve_status_without_forwarding_credentials(self):
        for phase in ('enqueue', 'receipt'):
            with self.subTest(phase=phase):
                result, events, _ = self.run_status(redirect=phase)
                self.assertEqual({key: result[key] for key in STATUS}, STATUS)
                self.assertIsNone(result['capabilities'])
                self.assertEqual(result['capabilities_error'], 'Available controls could not be read; run status again.')
                expected = [('POST', None), ('GET', ['a' * 32]), ('GET', ['a' * 32]), ('POST', None)]
                if phase == 'receipt':
                    expected.append(('GET', ['b' * 32]))
                self.assertEqual([(event[0], event[2].get('id')) for event in events], expected)


if __name__ == '__main__':
    unittest.main()
