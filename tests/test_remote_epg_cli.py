import contextlib
import http.server
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('epg_ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

NOW = 1790737200
SETTINGS = {'url': 'https://epg.example/epg/v1', 'source': 'epg-one'}


def catalog():
    return {'catalog': 'owned-catalog-1', 'channels': [
        {'id': 'ren', 'number': 1, 'name': 'РЕН ТВ', 'tvgId': '18', 'tvgName': 'Рен', 'shift': 0},
        {'id': 'cats', 'number': 7, 'name': 'СТС', 'tvgId': '36', 'tvgName': '', 'shift': 3600}]}


def guide():
    return {'version': 1, 'source': 'epg-one', 'generation': 'snapshot-1', 'fetchedAt': NOW * 1000 - 1000,
            'asOf': NOW, 'stale': False, 'checked': 2, 'total': 2,
            'programs': [{'id': 'cats', 'start': NOW - 60, 'end': NOW + 60, 'title': 'Три кота'}]}


class RemoteEpgCliTest(unittest.TestCase):
    def run_command(self, words, snapshot=None, response=None, playback=None, settings=SETTINGS, json_output=False):
        client = mock.Mock()
        client.timeout = 45
        client.device.return_value = 'dev_tv'
        client.call.side_effect = [catalog() if snapshot is None else snapshot,
                                   {'dispatched': True, 'channel': {'id': 'cats', 'number': 7, 'name': 'СТС'}}
                                   if playback is None else playback]
        epg = mock.Mock()
        epg.current.side_effect = [guide() if response is None else response]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value={'epg': settings}), \
                mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'EpgClient', return_value=epg), \
                mock.patch.object(ott.time, 'time', return_value=NOW), \
                mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]) as choice, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = ott.main((['--json'] if json_output else []) + ['tv'] + words)
        self.program_choice = choice
        return result, output.getvalue(), errors.getvalue(), client, epg

    def test_configured_query_uses_only_metadata_and_guarded_playback(self):
        snapshot = catalog()
        snapshot['channels'][0].update(stream_url='private-stream', password='private-password')
        snapshot['secret'] = 'private-catalog'
        result, output, errors, client, epg = self.run_command(['p', 'ТРИ', 'КОТА'], snapshot=snapshot)
        self.assertEqual(result, 0)
        self.assertEqual(output, 'СТС — Три кота\n')
        self.assertIn('Channel switch requested: 7: СТС', errors)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {}),
                         mock.call('dev_tv', 'play_catalog', {'catalog': 'owned-catalog-1', 'id': 'cats'})])
        expected = [{key: value for key, value in row.items() if key != 'number'} for row in catalog()['channels']]
        epg.current.assert_called_once_with(expected, 'ТРИ КОТА')
        self.assertNotIn('private', json.dumps(epg.current.call_args.args) + output + errors)
        self.program_choice.assert_not_called()

    def test_multiple_programmes_keep_full_list_and_use_random_rows_own_catalogue_id(self):
        response = guide()
        response['programs'].insert(0, {'id': 'ren', 'start': NOW - 30, 'end': NOW + 30,
                                        'title': 'Три кота: первая серия'})
        expected_programmes = [
            {'channel': 'РЕН ТВ', 'number': 1, 'title': 'Три кота: первая серия',
             'start': NOW - 30, 'end': NOW + 30},
            {'channel': 'СТС', 'number': 7, 'title': 'Три кота', 'start': NOW - 60, 'end': NOW + 60},
        ]
        for json_output in [False, True]:
            with self.subTest(json_output=json_output):
                result, output, errors, client, epg = self.run_command(
                    ['p', 'КОТА'], response=response, json_output=json_output)
                self.assertEqual(result, 0, errors)
                if json_output:
                    data = json.loads(output)
                    self.assertEqual(data['programs'], expected_programmes)
                    self.assertEqual(data['playback']['channel'], {'id': 'cats', 'number': 7, 'name': 'СТС'})
                else:
                    self.assertEqual(output, 'РЕН ТВ — Три кота: первая серия\nСТС — Три кота\n')
                self.assertIn('Channel switch requested: 7: СТС', errors)
                self.assertIn('Randomly selected channel: 7: СТС', errors)
                self.program_choice.assert_called_once_with(expected_programmes)
                self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {}),
                    mock.call('dev_tv', 'play_catalog', {'catalog': 'owned-catalog-1', 'id': 'cats'})])
                self.assertEqual(epg.current.call_count, 1)

    def test_random_selection_rejects_first_rows_ack_and_keeps_all_results_without_retry(self):
        response = guide()
        response['programs'].insert(0, {'id': 'ren', 'start': NOW - 30, 'end': NOW + 30, 'title': 'Три кота'})
        incorrect_ack = {'dispatched': True, 'channel': {'id': 'ren', 'number': 1, 'name': 'РЕН ТВ'}}
        result, output, errors, client, _ = self.run_command(
            ['p', 'кота'], response=response, playback=incorrect_ack, json_output=True)
        self.assertEqual(result, 1)
        data = json.loads(output)
        self.assertEqual([row['number'] for row in data['programs']], [1, 7])
        self.assertIn('did not confirm', data['playback']['error'])
        self.assertIn('do not repeat', errors)
        self.assertNotIn('Channel switch requested', errors)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {}),
            mock.call('dev_tv', 'play_catalog', {'catalog': 'owned-catalog-1', 'id': 'cats'})])

    def test_plain_and_list_queries_never_play(self):
        for words, query in [(['p'], ''), (['p', '  '], ''), (['P', '--list', 'Три кота'], 'Три кота')]:
            with self.subTest(words=words):
                result, output, errors, client, epg = self.run_command(words, json_output=True)
                self.assertEqual(result, 0)
                self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {})])
                self.assertEqual(epg.current.call_args.args[1], query)
                data = json.loads(output)
                self.assertEqual(data['programs'][0]['number'], 7)
                self.assertFalse(data['partial'])
                self.assertNotIn('playback', data)
                self.assertEqual(errors, '')
                self.program_choice.assert_not_called()

    def test_multiple_programme_list_query_does_not_choose_or_play(self):
        response = guide()
        response['programs'].insert(0, {'id': 'ren', 'start': NOW - 30, 'end': NOW + 30, 'title': 'Три кота'})
        result, output, errors, client, _ = self.run_command(['p', '--list', 'кота'], response=response)
        self.assertEqual((result, errors), (0, ''))
        self.assertEqual(output, 'РЕН ТВ — Три кота\nСТС — Три кота\n')
        self.program_choice.assert_not_called()
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {})])

    def test_multibyte_id_boundary_survives_catalog_service_and_playback(self):
        value = '界' * 512
        snapshot, response = catalog(), guide()
        snapshot['channels'][1]['id'] = value
        response['programs'][0]['id'] = value
        playback = {'dispatched': True, 'channel': {'id': value, 'number': 7, 'name': 'СТС'}}
        result, _, _, client, epg = self.run_command(['p', 'cats'], snapshot=snapshot, response=response, playback=playback)
        self.assertEqual(result, 0)
        self.assertEqual(epg.current.call_args.args[0][1]['id'], value)
        self.assertEqual(client.call.call_args.args[2], {'catalog': 'owned-catalog-1', 'id': value})

    def test_empty_catalog_skips_service_and_playback(self):
        result, output, _, client, epg = self.run_command(['p', 'cats'], snapshot={'catalog': 'c', 'channels': []}, json_output=True)
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)['programs'], [])
        self.assertEqual(client.call.call_count, 1)
        epg.current.assert_not_called()

    def test_no_current_matches_on_old_player_requires_archive_capability(self):
        response = guide()
        response['programs'] = []
        result, output, errors, client, _ = self.run_command(['p', 'missing'], response=response)
        self.assertEqual(result, 1)
        self.assertEqual(output, '')
        self.assertIn('Update this player to support archive search', errors)
        self.assertEqual(client.call.call_count, 1)

    def test_service_errors_never_fall_back_to_player_scan(self):
        result, output, errors, client, epg = self.run_command(['p', 'cats'], response=ott.Error('EPG service unavailable'))
        self.assertEqual(result, 1)
        self.assertEqual(output, '')
        self.assertIn('EPG service unavailable', errors)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {})])
        self.assertEqual(epg.current.call_count, 1)

    def test_invalid_catalogs_never_call_service(self):
        cases = [{}, {'catalog': '', 'channels': []}, {'catalog': 'x' * 129, 'channels': []}]
        for key, value in [('id', ''), ('id', '😀' * 513), ('number', True), ('number', 0), ('shift', 1.5),
                           ('shift', 86401), ('name', 'x' * 513), ('tvgId', None)]:
            item = catalog()
            item['channels'][0][key] = value
            cases.append(item)
        duplicate = catalog()
        duplicate['channels'][1]['id'] = 'ren'
        cases.append(duplicate)
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                result, output, errors, client, epg = self.run_command(['p', 'cats'], snapshot=snapshot)
                self.assertEqual(result, 1)
                self.assertEqual(output, '')
                self.assertIn('invalid EPG catalogue', errors)
                self.assertEqual(client.call.call_count, 1)
                epg.current.assert_not_called()

    def test_invalid_stale_partial_or_foreign_results_never_play(self):
        cases = [{}, [], {'error': {'code': 'EPG_NOT_READY'}}]
        for key, value in [('version', True), ('source', 'other'), ('generation', ''), ('fetchedAt', None),
                           ('asOf', NOW - 61), ('asOf', float('nan')), ('asOf', 10 ** 400),
                           ('stale', True), ('stale', 0), ('checked', 1), ('total', True), ('programs', {})]:
            response = guide()
            response[key] = value
            cases.append(response)
        for key, value in [('id', 'missing'), ('title', None), ('title', ''), ('title', '\ud800'),
                           ('title', '\udfff'), ('title', 'x' * 16385), ('title', '😀' * 8192 + 'x'), ('start', NOW + 1),
                           ('end', NOW), ('end', float('inf')), ('start', 10 ** 400)]:
            response = guide()
            response['programs'][0][key] = value
            cases.append(response)
        duplicate = guide()
        duplicate['programs'] *= 2
        cases.append(duplicate)
        reordered = guide()
        reordered['programs'].append({'id': 'ren', 'start': NOW - 30, 'end': NOW + 30, 'title': 'Other'})
        cases.append(reordered)
        for response in cases:
            with self.subTest(response=response):
                result, output, errors, client, epg = self.run_command(['p', 'cats'], response=response)
                self.assertEqual(result, 1)
                self.assertEqual(output, '')
                self.assertIn('Error:', errors)
                self.assertEqual(client.call.call_count, 1)
                self.assertEqual(epg.current.call_count, 1)
                self.program_choice.assert_not_called()

    def test_invalid_later_programme_never_reaches_random_choice_or_dispatch(self):
        response = guide()
        response['programs'].insert(0, {'id': 'ren', 'start': NOW - 30, 'end': NOW + 30, 'title': 'Три кота'})
        response['programs'][1]['title'] = '\ud800'
        result, output, _, client, _ = self.run_command(['p', 'кота'], response=response)
        self.assertEqual((result, output), (1, ''))
        self.program_choice.assert_not_called()
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'epg_catalog', {})])

    def test_title_utf16_boundary_allows_complete_valid_unicode(self):
        for title in ['x' * 16384, '😀' * 8192]:
            with self.subTest(title_length=len(title)):
                response = guide()
                response['programs'][0]['title'] = title
                result, output, _, client, _ = self.run_command(['p', 'cats'], response=response, json_output=True)
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(output)['programs'][0]['title'], title)
                self.assertEqual(client.call.call_count, 2)
                self.assertEqual(client.call.call_args.args[1], 'play_catalog')

    def test_catalog_rejection_keeps_results_without_number_fallback(self):
        result, output, errors, client, _ = self.run_command(['p', 'cats'], playback=ott.Error('Catalogue changed'), json_output=True)
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output)['playback']['error'], 'Catalogue changed')
        self.assertIn('Catalogue changed', errors)
        self.assertEqual(client.call.call_count, 2)
        self.assertEqual(client.call.call_args.args[1], 'play_catalog')

    def test_playback_receipt_must_match_id_number_and_name(self):
        for field, value in [('id', 'other'), ('number', 1), ('name', 'Other')]:
            playback = {'dispatched': True, 'channel': {'id': 'cats', 'number': 7, 'name': 'СТС'}}
            playback['channel'][field] = value
            result, _, errors, client, _ = self.run_command(['p', 'cats'], playback=playback)
            self.assertEqual(result, 1)
            self.assertIn('do not repeat', errors)
            self.assertEqual(client.call.call_count, 2)


class EpgTransportTest(unittest.TestCase):
    def serve(self, handler):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_public_http_never_sends_credentials_and_refuses_redirects(self):
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                seen.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                if self.path == '/redirect/current':
                    self.send_response(307)
                    self.send_header('Location', '/epg/v1/current')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = json.dumps(guide()).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = self.serve(Handler)
        base = f'http://127.0.0.1:{server.server_port}'
        client = ott.EpgClient({'url': base + '/epg/v1', 'source': 'epg-one'}, 5)
        self.assertEqual(client.current([], 'ТРИ КОТА'), guide())
        self.assertEqual(seen[0][0], '/epg/v1/current')
        self.assertNotIn('Authorization', seen[0][1])
        self.assertNotIn('Cookie', seen[0][1])
        self.assertEqual(seen[0][2], {'version': 1, 'source': 'epg-one', 'channels': [], 'search': 'ТРИ КОТА'})
        client = ott.EpgClient({'url': base + '/redirect', 'source': 'epg-one'}, 5)
        with self.assertRaisesRegex(ott.Error, 'HTTP 307'):
            client.current([], '')
        self.assertEqual(len(seen), 2)

    def test_invalid_configuration_is_rejected_before_io(self):
        for settings in [None, {}, dict(SETTINGS, token='secret'), dict(SETTINGS, source='other')]:
            with self.subTest(settings=settings), self.assertRaises(ott.Error):
                ott.EpgClient(settings, 5)
        for url in ['file:///epg', 'https://user:secret@epg.example', 'https://epg.example?x=y',
                    'https://epg.example#fragment', 'https://epg.example?', 'https://epg.example#',
                    'https://epg.example:invalid', 'https://epg.example:70000', 'https://epg.example:0',
                    'https://epg.example\n', 'https://epg.example\\other']:
            with self.subTest(url=url), self.assertRaises(ott.Error):
                ott.EpgClient(dict(SETTINGS, url=url), 5)

    def test_trickling_response_obeys_wall_deadline_without_reposting(self):
        requests = []
        stopped = threading.Event()
        self.addCleanup(stopped.set)
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                requests.append(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps(guide()).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                for byte in body:
                    if stopped.wait(0.05): return
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError): return
        server = self.serve(Handler)
        client = ott.EpgClient({'url': f'http://127.0.0.1:{server.server_port}', 'source': 'epg-one'}, 0.3)
        started = time.monotonic()
        with self.assertRaisesRegex(ott.Error, 'timed out'):
            client.current([], '')
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(len(requests), 1)
        for thread in threading.enumerate():
            if thread.name == 'ottplay-epg-http':
                thread.join(0.5)
                self.assertFalse(thread.is_alive())

    def test_oversized_body_is_rejected_before_transport(self):
        client = ott.EpgClient(SETTINGS, 5)
        with mock.patch.object(client.opener, 'open') as opening:
            with self.assertRaisesRegex(ott.Error, 'request size limit'):
                client.current([{'name': 'x' * (512 * 1024)}], '')
            opening.assert_not_called()

    def test_invalid_incomplete_and_oversized_responses_fail(self):
        for body, extra_length in [(b'not-json', 0), (b'{}', 10), (b'x' * (2 * 1024 * 1024 + 1), 0)]:
            with self.subTest(length=len(body), extra_length=extra_length):
                class Response:
                    status = 200
                    def __init__(self):
                        self.stream = io.BytesIO(body)
                        self.length = len(body) + extra_length
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def read1(self, count):
                        value = self.stream.read(count)
                        self.length -= len(value)
                        return value
                client = ott.EpgClient(SETTINGS, 5)
                with mock.patch.object(client.opener, 'open', return_value=Response()) as opening:
                    with self.assertRaises(ott.Error):
                        client.current([], '')
                    opening.assert_called_once()


if __name__ == '__main__':
    unittest.main()
