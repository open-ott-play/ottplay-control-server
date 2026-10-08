"""Exercise preset sequencing through the real CLI and controller HTTP client."""
import contextlib
import copy
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
    'ott_presets_transport', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

DEVICE = 'dev_preset_transport_fixture'
ADMIN_TOKEN = 'dummy-admin-token'
PRESET = {
    'm3u': [
        {'number': number, 'name': f'Bundle {number}', 'history_hours': 144,
         'playlist': f'https://playlist.fixture.invalid/{number}.m3u?key=dummy-playlist-{number}',
         'vportal': f'portal::[key:dummy-vportal-{number}]https://portal.fixture.invalid/api/v1/'}
        for number in (1, 2)
    ],
    'plex': {'server': 'https://plex.fixture.invalid:32400', 'token': 'dummy-plex-token'},
    'active_profile': 1,
}
ECHO = 'sensitive-echo dummy-plex-token dummy-playlist-1 dummy-vportal-1'


class PresetController:
    """A queued controller and delayed player; each POST executes at most once."""
    def __init__(self, failure=None):
        self.failure = failure
        self.provider = 'm3u'
        self.active_profile = 2
        self.slots = {
            number: {'name': f'Original {number}', 'history_hours': 24,
                     'playlist': '', 'vportal': ''}
            for number in range(1, 16)
        }
        self.plex_attempts = 0
        self.plex_writes = []
        self.profile_writes = []
        self.profile_selections = []
        self.profile_queries = 0
        self.reloads = 0
        self.posts = []
        self.gets = []
        self.receipts = {}
        self.outstanding = None
        self.protocol_errors = []
        self.lock = threading.Lock()

    def row(self, number):
        slot = self.slots[number]
        return {'number': number, 'name': slot['name'],
                'history_hours': slot['history_hours'],
                'active': self.active_profile == number,
                'playlist_configured': bool(slot['playlist']),
                'vportal_configured': bool(slot['vportal']), 'private': ECHO}

    @staticmethod
    def rejected(message):
        return {'status': 'rejected', 'data': {'error': message, 'private': ECHO}}

    def execute(self, request):
        action, params = request['action'], request['params']
        if action == 'provider':
            self.provider = params['query']
            data = {'provider': self.provider, 'dispatched': True}
        elif action == 'status':
            # Mounting/settings must not depend on catalog or playback readiness.
            data = {'provider': self.provider, 'ready': False, 'channels': 0,
                    'uuid': DEVICE, 'volume': 25}
        elif action == 'provider_settings':
            if self.provider != 'plex' or params['provider'] != 'plex':
                self.protocol_errors.append('Plex settings sent to another provider')
                return self.rejected('Unexpected provider')
            self.plex_attempts += 1
            if self.plex_attempts == 1:
                return self.rejected('Plex settings are unavailable on this player.')
            self.plex_writes.append(copy.deepcopy(params['settings']))
            data = {'provider': 'plex', 'saved': True,
                    'fields': list(params['settings']), **params['settings']}
        elif action == 'profiles':
            self.profile_queries += 1
            if self.provider != 'm3u' or self.profile_queries == 1:
                return self.rejected('Select the M3U provider before managing profiles.')
            data = {'provider': 'm3u', 'profiles': [self.row(n) for n in range(1, 16)]}
        elif action == 'profile_settings':
            number = params['number']
            if self.provider != 'm3u':
                self.protocol_errors.append('M3U settings sent to another provider')
                return self.rejected('Unexpected provider')
            if self.failure == 'rejected-second-slot' and number == 2:
                return self.rejected('Could not save profile: ' + ECHO)
            settings = copy.deepcopy(params['settings'])
            self.profile_writes.append((number, settings))
            self.slots[number].update(settings)
            reloaded = self.active_profile == number
            self.reloads += int(reloaded)
            data = {'provider': 'm3u', 'saved': True, 'fields': list(settings),
                    'reloaded': reloaded, 'profile': self.row(number)}
        elif action == 'profile':
            number = params['number']
            self.profile_selections.append(number)
            self.reloads += int(self.active_profile != number)
            self.active_profile = number
            data = {'provider': 'm3u', 'dispatched': True, 'profile': self.row(number)}
        else:
            self.protocol_errors.append('Unexpected action: ' + action)
            return self.rejected('Unexpected action')
        return {'status': 'ok', 'data': {**data, 'private': ECHO}}

    @contextlib.contextmanager
    def serve(self):
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def valid_request(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                valid = (parsed.path == '/api/requests'
                         and query.get('device_id') == [DEVICE]
                         and self.headers.get('Authorization') == 'Bearer ' + ADMIN_TOKEN)
                if not valid:
                    fixture.protocol_errors.append('Unexpected route, target or authorization')
                    self.reply(400, {})
                    return None
                return query

            def do_POST(self):
                if self.valid_request() is None:
                    return
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with fixture.lock:
                    if fixture.outstanding is not None:
                        fixture.protocol_errors.append('Another POST arrived before its predecessor settled')
                        self.reply(409, {})
                        return
                    request_id = f'{len(fixture.posts) + 1:032x}'
                    result = fixture.execute(request)
                    fixture.posts.append((request_id, request))
                    fixture.receipts[request_id] = {'request': request, 'result': result, 'reads': 0}
                    fixture.outstanding = request_id
                self.reply(202, {'id': request_id})

            def do_GET(self):
                query = self.valid_request()
                if query is None:
                    return
                request_id = query.get('id', [''])[0]
                with fixture.lock:
                    receipt = fixture.receipts.get(request_id)
                    if receipt is None:
                        fixture.protocol_errors.append('Unknown receipt ID')
                        self.reply(404, {})
                        return
                    receipt['reads'] += 1
                    reads, request = receipt['reads'], receipt['request']
                    if reads == 1:
                        code, body = 202, {'status': 'pending'}
                    elif (fixture.failure == 'missing-write-receipt'
                          and request['action'] == 'profile_settings'):
                        # The write happened, but the caller cannot establish its outcome.
                        code, body = 404, {'error': ECHO}
                    elif (reads == 2 and request['action'] == 'provider_settings'
                          and receipt['result']['status'] == 'ok'):
                        code, body = 503, {'error': ECHO}
                    else:
                        code, body = 200, dict(receipt['result'], id=request_id)
                        fixture.outstanding = None
                    fixture.gets.append((request_id, code))
                self.reply(code, body)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        listener = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        listener.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            server.server_close()
            listener.join(1)


class PresetTransportTest(unittest.TestCase):
    def run_load(self, fixture, json_output=False):
        output, errors = io.StringIO(), io.StringIO()
        real_sleep = time.sleep
        with tempfile.TemporaryDirectory() as directory, fixture.serve() as address:
            credentials = Path(directory) / 'server.json'
            credentials.write_text(json.dumps({'admin_token': ADMIN_TOKEN, 'devices': [{'id': DEVICE}]}))
            config = Path(directory) / 'cli.json'
            config.write_text(json.dumps({'server': address, 'server_config': str(credentials),
                                         'players': {'tv': DEVICE}, 'presets': {'local': PRESET}}))
            credentials.chmod(0o600)
            config.chmod(0o600)
            before = config.read_bytes()
            args = ['--config', str(config), '--timeout', '2']
            if json_output:
                args.append('--json')
            args += ['tv', 'load', 'LOCAL']
            # Retain real HTTP, threads and deadlines; shorten only deliberate polling pauses.
            with mock.patch.object(ott.time, 'sleep', side_effect=lambda seconds: real_sleep(min(seconds, 0.001))), \
                    mock.patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = ott.main(args)
            self.assertEqual(config.read_bytes(), before)
        self.assertEqual(fixture.protocol_errors, [])
        self.assertNotIn('sensitive-echo', output.getvalue() + errors.getvalue())
        for secret in [ADMIN_TOKEN, 'dummy-plex-token', 'dummy-playlist-1', 'dummy-playlist-2',
                       'dummy-vportal-1', 'dummy-vportal-2']:
            self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        return status, output.getvalue(), errors.getvalue()

    def test_mount_delays_and_receipt_retries_keep_one_request_in_flight(self):
        for json_output in (False, True):
            with self.subTest(json_output=json_output):
                fixture = PresetController()
                status, output, errors = self.run_load(fixture, json_output)
                self.assertEqual((status, errors), (0, ''))
                if json_output:
                    receipt = json.loads(output)
                    self.assertEqual((receipt['status'], receipt['provider'], receipt['active_profile']),
                                     ('loaded', 'm3u', 1))
                    self.assertIn('verify_profiles', receipt['completed'])
                self.assertEqual(fixture.plex_attempts, 2)
                self.assertEqual(fixture.plex_writes, [PRESET['plex']])
                self.assertEqual([number for number, _ in fixture.profile_writes], [1, 2])
                self.assertEqual(fixture.profile_selections, [1])
                self.assertEqual((fixture.active_profile, fixture.reloads), (1, 1))
                for expected in PRESET['m3u']:
                    self.assertEqual(fixture.slots[expected['number']],
                                     {key: value for key, value in expected.items() if key != 'number'})
                mutations = [(request['action'], request['params'].get('number'))
                             for _, request in fixture.posts
                             if request['action'] in ('profile_settings', 'profile')]
                self.assertEqual(mutations, [('profile_settings', 1), ('profile', 1), ('profile_settings', 2)])
                successful_plex_id = next(request_id for request_id, request in fixture.posts
                                          if request['action'] == 'provider_settings'
                                          and fixture.receipts[request_id]['result']['status'] == 'ok')
                self.assertEqual([code for request_id, code in fixture.gets if request_id == successful_plex_id],
                                 [202, 503, 200])
                self.assertTrue(all(receipt['reads'] >= 2 for receipt in fixture.receipts.values()))
                self.assertIsNone(fixture.outstanding)

    def test_uncertain_queued_write_stops_without_post_replay_or_next_step(self):
        fixture = PresetController('missing-write-receipt')
        status, _, errors = self.run_load(fixture)
        self.assertEqual(status, 1)
        self.assertTrue(errors)
        self.assertEqual(fixture.plex_writes, [PRESET['plex']])
        self.assertEqual([number for number, _ in fixture.profile_writes], [1])
        self.assertEqual(fixture.profile_selections, [])
        self.assertEqual(fixture.active_profile, 2)
        request_id, last = fixture.posts[-1]
        self.assertEqual((last['action'], last['params']['number']), ('profile_settings', 1))
        self.assertEqual([code for item, code in fixture.gets if item == request_id], [202, 404])
        self.assertEqual(fixture.outstanding, request_id)

    def test_later_player_rejection_preserves_confirmed_progress_without_rollback(self):
        fixture = PresetController('rejected-second-slot')
        status, output, errors = self.run_load(fixture, json_output=True)
        self.assertEqual(status, 1)
        self.assertEqual(errors, '')
        receipt = json.loads(output)
        self.assertEqual((receipt['status'], receipt['stage']), ('failed', 'save_profile_2'))
        self.assertIn('save_plex', receipt['completed'])
        self.assertIn('save_profile_1', receipt['completed'])
        self.assertIn('select_profile_1', receipt['completed'])
        self.assertNotIn('save_profile_2', receipt['completed'])
        self.assertNotIn('verify_profiles', receipt['completed'])
        self.assertEqual(fixture.plex_writes, [PRESET['plex']])
        self.assertEqual([number for number, _ in fixture.profile_writes], [1])
        self.assertEqual(fixture.profile_selections, [1])
        self.assertEqual(fixture.active_profile, 1)
        last = fixture.posts[-1][1]
        self.assertEqual((last['action'], last['params']['number']), ('profile_settings', 2))
        self.assertEqual(fixture.slots[2]['name'], 'Original 2')
        self.assertIsNone(fixture.outstanding)


if __name__ == '__main__':
    unittest.main()
