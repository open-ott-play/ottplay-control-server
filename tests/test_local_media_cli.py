import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_local_media', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / 'contracts/local-media-v1.json').read_text())


def caps(native=True, progressive=False):
    value = {'version': 1, 'player': {'version': 'test', 'platform': 'tauri', 'runtime': 'page-123'},
             'lifecycle': [], 'input': [], 'playback': [], 'local_media': copy.deepcopy(CONTRACT['capability'])}
    value['local_media']['absolute_paths'] = native
    if progressive:
        value['local_media_progressive'] = copy.deepcopy(CONTRACT['progressive_capability'])
    return value


class LocalMediaTest(unittest.TestCase):
    def run_cli(self, words, responses, json_output=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_t1'
        client.call.side_effect = responses
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['-j'] if json_output else []) + ['t1'] + words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client.call.call_args_list

    def test_shared_contract_and_exact_requests(self):
        self.assertEqual(set(CONTRACT['operations']), ott.LOCAL_MEDIA_OPERATIONS)
        self.assertEqual(set(CONTRACT['errors']), set(ott.LOCAL_MEDIA_ERRORS))
        for op in CONTRACT['operations']:
            words = ['local', op] + ([] if op == 'sources' else ['Movies'])
            data = CONTRACT[op + '_result']
            code, out, err, calls = self.run_cli(words, [caps(), data], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', data))
            probe = {'local_media_progressive': True} if op in ('play', 'shuffle') else {}
            self.assertEqual(calls, [mock.call('dev_t1', 'capabilities', probe),
                                    mock.call('dev_t1', 'local_media', CONTRACT[op + '_request']['params'])])

    def test_progressive_request_needs_both_controller_probe_and_player_marker(self):
        for op in ('play', 'shuffle'):
            partial = dict(CONTRACT['progressive_' + op + '_result'], count=1)
            code, out, err, calls = self.run_cli(['local', op, 'Movies'], [caps(progressive=True), partial], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', partial))
            self.assertEqual(calls, [mock.call('dev_t1', 'capabilities', CONTRACT['progressive_probe_request']['params']),
                                    mock.call('dev_t1', 'local_media', CONTRACT['progressive_' + op + '_request']['params'])])

            # Successful negotiation may still return a complete legacy result.
            complete = CONTRACT[op + '_result']
            code, out, err, _ = self.run_cli(['local', op, 'Movies'], [caps(progressive=True), complete], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', complete))

            # A new player behind an older controller still uses the legacy request.
            code, out, err, calls = self.run_cli(['local', op, 'Movies'],
                                               [ott.HTTPError(400), caps(progressive=True), complete], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', complete))
            self.assertEqual(calls, [mock.call('dev_t1', 'capabilities', {'local_media_progressive': True}),
                                    mock.call('dev_t1', 'capabilities', {}),
                                    mock.call('dev_t1', 'local_media', CONTRACT[op + '_request']['params'])])

    def test_progressive_probe_is_not_used_for_sources_or_connect(self):
        for op in ('sources', 'connect'):
            code, out, err, calls = self.run_cli(['local', op] + (['Movies'] if op == 'connect' else []),
                                               [caps(progressive=True), CONTRACT[op + '_result']], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', CONTRACT[op + '_result']))
            self.assertEqual(calls, [mock.call('dev_t1', 'capabilities', {}),
                                    mock.call('dev_t1', 'local_media', CONTRACT[op + '_request']['params'])])

    def test_progressive_marker_is_strict_and_requires_local_media(self):
        for marker in (None, True, 1, [], {}, {'version': True}, {'version': 2}, {'version': 1, 'extra': True}):
            value = caps(); value['local_media_progressive'] = marker
            code, out, _, calls = self.run_cli(['local', 'shuffle', 'Movies'], [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1), marker)
        value = caps(progressive=True); del value['local_media']
        self.assertEqual(len(self.run_cli(['local', 'play', 'Movies'], [value])[3]), 1)

    def test_scanning_requires_negotiated_true_and_preserves_all_result_guards(self):
        partial = dict(CONTRACT['shuffle_result'], scanning=True)
        for replies in ([caps(), partial], [ott.HTTPError(400), caps(progressive=True), partial]):
            code, out, _, calls = self.run_cli(['local', 'shuffle', 'Movies'], replies)
            self.assertEqual((code, out, len(calls)), (1, '', len(replies)))
        for value in (False, None, 1, 'true', [], {}):
            code, out, _, calls = self.run_cli(['local', 'shuffle', 'Movies'],
                                              [caps(progressive=True), dict(partial, scanning=value)])
            self.assertEqual((code, out, len(calls)), (1, '', 2), value)
        for edit in (lambda x: x.update(count=0), lambda x: x.update(count=True),
                     lambda x: x.update(count=50001), lambda x: x.update(runtime='other-page'),
                     lambda x: x.update(op='play'), lambda x: x.update(accepted=False),
                     lambda x: x.update(playback_confirmed=True), lambda x: x.update(shuffled=False),
                     lambda x: x.update(extra=True), lambda x: x['source'].update(name='Other'),
                     lambda x: x['source'].update(available=False), lambda x: x['source'].update(path='/private/secret')):
            data = copy.deepcopy(partial); edit(data)
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(progressive=True), data])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('/private', err)
        for op in ('sources', 'connect'):
            with self.assertRaises(ott.Error):
                ott.local_media_metadata(dict(CONTRACT[op + '_result'], scanning=True), 'page-123',
                                         {'op': op, 'source': 'Movies', 'progressive': True})
        with self.assertRaises(ott.Error):
            ott.local_media_metadata(dict(CONTRACT['error_result'], scanning=True), 'page-123',
                                     {'op': 'shuffle', 'source': 'Movies', 'progressive': True})
        limited = caps(progressive=True); limited['local_media']['max_items'] = 2
        self.assertEqual(self.run_cli(['local', 'shuffle', 'Movies'], [limited, partial])[0], 1)

    def test_partial_output_does_not_present_count_as_complete_or_playback_as_confirmed(self):
        for op in ('play', 'shuffle'):
            partial = dict(CONTRACT[op + '_result'], scanning=True, count=46)
            code, out, err, _ = self.run_cli(['local', op, 'Movies'], [caps(progressive=True), partial])
            self.assertEqual((code, err), (0, ''))
            self.assertIn('46 items discovered so far; scanning continues.', out)
            self.assertIn('playback has not been confirmed', out)
            self.assertNotIn('shuffled once', out)
            self.assertNotIn('source order', out)

    def test_only_confirmed_probe_enqueue_400_falls_back_and_mutations_never_replay(self):
        failures = (ott.HTTPError(401), ott.HTTPError(404), ott.HTTPError(429), ott.HTTPError(500),
                    ott.TransportError('uncertain probe'),
                    ott.RequestError('controller_http_error', 'receipt', 'HTTP 400 after enqueue'),
                    ott.PlayerRejected('rejected', {}), ott.PlayerUnsupported('unsupported'))
        for failure in failures:
            code, out, _, calls = self.run_cli(['local', 'shuffle', 'Movies'], [failure])
            self.assertEqual((code, out, len(calls)), (1, '', 1), type(failure).__name__)
        code, out, _, calls = self.run_cli(['local', 'shuffle', 'Movies'], [ott.HTTPError(400), ott.HTTPError(400)])
        self.assertEqual((code, out, len(calls)), (1, '', 2))
        for failure in (ott.HTTPError(400), ott.TransportError('uncertain mutation'),
                        ott.PlayerRejected('private detail', CONTRACT['error_result'])):
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(progressive=True), failure])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('private detail', err)

    def test_fallback_and_delayed_capability_reply_share_original_deadline(self):
        client = mock.Mock(timeout=10)
        budgets = []
        replies = iter([ott.HTTPError(400), caps(progressive=True), CONTRACT['shuffle_result']])
        def reply(*args):
            budgets.append(client.timeout)
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value
        client.call.side_effect = reply
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 8, 9, 9.5]):
            ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
        self.assertEqual(budgets, [10, 2, 1])
        self.assertEqual(client.timeout, 10)
        self.assertNotIn('progressive', client.call.call_args.args[2])
        for first in (ott.HTTPError(400), caps(progressive=True)):
            client = mock.Mock(timeout=10)
            client.call.side_effect = [first]
            with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 10]):
                with self.assertRaisesRegex(ott.Error, 'capability discovery exceeded'):
                    ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
            self.assertEqual(client.call.call_count, 1)
            self.assertEqual(client.timeout, 10)

    def test_enqueue_http_400_falls_back_once_through_actual_receipt_client(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 10
        client.api = mock.Mock(side_effect=[ott.HTTPError(400), (202, {'id': 'a' * 32}), (202, {'id': 'b' * 32})])
        client._wait_receipt = mock.Mock(side_effect=[
            {'request_id': 'a' * 32, 'status': 'ok', 'data': caps(progressive=True)},
            {'request_id': 'b' * 32, 'status': 'ok', 'data': CONTRACT['shuffle_result']}])
        client.on_receipt = mock.Mock()
        result = ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
        self.assertEqual(result, CONTRACT['shuffle_result'])
        self.assertEqual([call.args[1] for call in client.api.call_args_list], [
            CONTRACT['progressive_probe_request'], {'action': 'capabilities', 'params': {}}, CONTRACT['shuffle_request']])
        self.assertEqual([call.args[0]['action'] for call in client.on_receipt.call_args_list],
                         ['capabilities', 'capabilities', 'local_media'])

    def test_receipt_http_400_is_not_reinterpreted_as_probe_enqueue_rejection(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 10
        client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), ott.HTTPError(400)])
        client.on_receipt = mock.Mock()
        with mock.patch.object(ott.time, 'sleep'):
            with self.assertRaises(ott.RequestError):
                ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
        self.assertEqual(client.api.call_count, 2)
        self.assertEqual(client.api.call_args_list[0].args[1],
                         {'action': 'capabilities', 'params': {'local_media_progressive': True}})
        self.assertEqual(client.on_receipt.call_count, 1)
        self.assertEqual(client.on_receipt.call_args.args[0]['action'], 'capabilities')

    def test_capability_receipt_callback_cannot_extend_command_deadline(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 10
        client.api = mock.Mock(return_value=(202, {'id': 'a' * 32}))
        client._wait_receipt = mock.Mock(return_value={'request_id': 'a' * 32, 'status': 'ok', 'data': caps(progressive=True)})
        now = [0]
        def callback(receipt):
            self.assertEqual(receipt['action'], 'capabilities')
            now[0] = 11
        client.on_receipt = callback
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            with self.assertRaisesRegex(ott.Error, 'capability discovery exceeded'):
                ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
        self.assertEqual(client.api.call_count, 1)
        self.assertEqual(client.timeout, 10)

    def test_aliases_and_native_paths_are_not_cli_paths(self):
        for name in ('local', 'LOCAL', 'offline', 'OFFLINE'):
            self.assertEqual(ott.parse_command([name]), ('local_media', {'op': 'sources'}))
            self.assertEqual(ott.parse_command([name, 'list']), ('local_media', {'op': 'sources'}))
            for selector in ('Movies', '/Volumes/data/Movies/', r'C:\Movies', 'Три кота'):
                with mock.patch.object(Path, 'resolve', side_effect=AssertionError('must not open CLI paths')):
                    self.assertEqual(ott.parse_command([name, 'shuffle', selector]),
                                     ('local_media', {'op': 'shuffle', 'source': selector}))
        self.assertEqual(ott.parse_command(['provider', 'local']), ('provider', {'query': 'offline'}))
        self.assertEqual(ott.parse_command(['provider', 'offline']), ('provider', {'query': 'offline'}))
        self.assertEqual(ott.parse_command(['local', 'shuffle', '--', '--Movies']),
                         ('local_media', {'op': 'shuffle', 'source': '--Movies'}))

    def test_invalid_syntax_has_no_network_or_search_fallback(self):
        for words in (['local', 'unknown', 'Movies'], ['local', 'sources', 'Movies'],
                      ['local', 'shuffle'], ['offline', 'shuffle', './Movies'],
                      ['local', 'play', 'https://private.example/token'], ['local', 'connect', '../Movies'],
                      ['local', 'shuffle', 'a\nb'], ['local', 'shuffle', '\ud800'],
                      ['local', 'shuffle', 'a' * 513], ['local', 'sources', '--unknown'],
                      ['local', 'shuffle', '--unknown'], ['local', 'play', 'Movies', '--unknown'],
                      ['local', 'sources', '--path'], ['local', 'sources', '--path', './Movies'],
                      ['local', 'sources', '--path', '/Movies', '--path', '/Movies'],
                      ['local', 'sources', '--paths', '--paths']):
            code, out, _, calls = self.run_cli(words, [])
            self.assertEqual((code, out, calls), (1, '', []), words)

    def test_private_paths_are_explicit_and_native_only(self):
        data = CONTRACT['paths_result']
        code, out, err, calls = self.run_cli(['local', 'sources', '--paths'], [caps(), data], True)
        self.assertEqual((code, err, json.loads(out)), (0, '', data))
        self.assertEqual(calls[-1], mock.call('dev_t1', 'local_media', CONTRACT['paths_request']['params']))
        for words in (['local', 'sources', '--paths'], ['local', 'shuffle', '/Volumes/data/Movies/'],
                      ['local', 'sources', '--path', '/Volumes/data/Movies/']):
            code, out, err, calls = self.run_cli(words, [caps(False)])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertIn('does not support native local paths', err)
        code, out, err, _ = self.run_cli(['local', 'sources'], [caps(), data])
        self.assertEqual((code, out), (1, ''))
        self.assertNotIn('/Volumes', err)

    def test_literal_colon_names_round_trip_without_changing_portable_profiles(self):
        for name in ('Kids: Cartoons', 'Drama:2026', 'Фильмы: детям'):
            for op in CONTRACT['operations']:
                data = copy.deepcopy(CONTRACT[op + '_result'])
                if op == 'sources':
                    data['sources'].append(dict(data['sources'][0], id='source-colon', name=name))
                else:
                    data['source']['name'] = name
                words = ['local', op] + ([] if op == 'sources' else [name])
                code, out, err, calls = self.run_cli(words, [caps(), data], True)
                self.assertEqual((code, err, json.loads(out)), (0, '', data))
                self.assertEqual(len(calls), 2)
        self.assertFalse(ott.offline_source_name('Kids: Cartoons', 512))

    def test_source_names_remain_bounded_basename_text(self):
        for name in ('', ' ', '.', '..', 'Kids/Cartoons', 'Kids\\Cartoons', 'Kids\nCartoons',
                     'Kids\x7fCartoons', 'Kids\x85Cartoons', '\ud800', 'Я' * 257):
            data = copy.deepcopy(CONTRACT['sources_result'])
            data['sources'][0]['name'] = name
            code, out, _, _ = self.run_cli(['local', 'sources'], [caps(), data], True)
            self.assertEqual((code, out), (1, ''), repr(name))

    def test_path_lookup_uses_player_canonical_identity_without_revealing_paths_by_default(self):
        params = CONTRACT['lookup_request']['params']
        result = CONTRACT['lookup_result']
        for flags in (['--path', params['path'], '--paths'], ['--paths', '--path', params['path']]):
            with mock.patch.object(Path, 'resolve', side_effect=AssertionError('canonicalize only on player')):
                code, out, err, calls = self.run_cli(['local', 'sources'] + flags, [caps(), result], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', result))
            self.assertEqual(calls[-1], mock.call('dev_t1', 'local_media', params))
        code, out, err, _ = self.run_cli(['local', 'sources', '--path', params['path']], [caps(), result])
        self.assertEqual((code, out), (1, ''))
        self.assertNotIn('/private', err)
        for path in ('/var/media/Movies', r'C:\Movies', '//server/share/Movies'):
            data = dict(result, sources=[])
            code, out, err, calls = self.run_cli(['local', 'sources', '--path', path], [caps(), data], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', data))
            self.assertEqual(calls[-1], mock.call('dev_t1', 'local_media', {'op': 'sources', 'runtime': 'page-123', 'path': path}))

    def test_invalid_capabilities_stop_before_mutation(self):
        for edit in (lambda x: x.update(version=True), lambda x: x.update(absolute_paths=1),
                     lambda x: x.update(max_sources=33), lambda x: x.update(max_items=50001),
                     lambda x: x.update(operations=['shuffle', 'shuffle']),
                     lambda x: x.update(secret='private'), lambda x: x.update(operations=['delete'])):
            value = caps(); edit(value['local_media'])
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertNotIn('private', err)
        value = caps(); del value['local_media']
        self.assertEqual(len(self.run_cli(['local', 'shuffle', 'Movies'], [value])[3]), 1)

    def test_results_are_correlated_and_do_not_claim_playback(self):
        for edit in (lambda x: x.update(runtime='other-page'), lambda x: x.update(op='play'),
                     lambda x: x.update(count=True), lambda x: x.update(count=0),
                     lambda x: x.update(count=50001), lambda x: x.update(shuffled=False),
                     lambda x: x.update(accepted=False), lambda x: x.update(playback_confirmed=True),
                     lambda x: x['source'].update(name='Other folder'),
                     lambda x: x['source'].update(available=False),
                     lambda x: x['source'].update(path='/private/secret')):
            data = copy.deepcopy(CONTRACT['shuffle_result']); edit(data)
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), data])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('/private', err)
        code, out, err, _ = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), CONTRACT['shuffle_result']])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('shuffled once', out)
        self.assertIn('playback has not been confirmed', out)
        for count in (13611, 50000):
            data = dict(CONTRACT['shuffle_result'], count=count)
            code, out, err, _ = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), data], True)
            self.assertEqual((code, err, json.loads(out)), (0, '', data))
        limited = caps(); limited['local_media']['max_items'] = 10000
        code, out, _, _ = self.run_cli(['local', 'shuffle', 'Movies'],
                                     [limited, dict(CONTRACT['shuffle_result'], count=13611)])
        self.assertEqual((code, out), (1, ''))

    def test_negative_reply_and_timeout_are_not_repeated(self):
        for failure in (ott.PlayerRejected('private raw message', CONTRACT['error_result']),
                        ott.PlayerUnsupported('private raw message'), ott.TransportError('Request may have executed')):
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), failure])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('private raw message', err)
        client = mock.Mock(timeout=10)
        client.call.side_effect = [caps(), CONTRACT['shuffle_result']]
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 0, 11]):
            with self.assertRaisesRegex(ott.Error, 'not repeated'):
                ott.local_media_command(client, 'dev_t1', {'op': 'shuffle', 'source': 'Movies'}, 10)
        self.assertEqual(client.call.call_count, 2)
        self.assertEqual(client.timeout, 10)

    def test_empty_source_explains_formats_and_profile_without_retry(self):
        data = dict(CONTRACT['error_result'], error='empty_source')
        for reply in (data, ott.PlayerRejected('private player details', data)):
            with self.subTest(reply=type(reply).__name__):
                code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), reply])
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn('No supported playable items', err)
                self.assertIn('selected source match the active Local profile', err)
                self.assertIn('check file formats and profile selection', err)
                self.assertIn('The request was not repeated', err)
                self.assertNotIn('private player details', err)
                self.assertNotIn('empty directory', err)

        stale = dict(data, runtime='previous-page')
        for reply in (stale, ott.PlayerRejected('private player details', stale)):
            code, out, err, calls = self.run_cli(['local', 'shuffle', 'Movies'], [caps(), reply])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('check file formats and profile selection', err)
            self.assertNotIn('private player details', err)

    def test_connect_can_use_preset_session_without_duplicate_dispatch(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        session = mock.Mock()
        session.call.return_value = CONTRACT['connect_result']
        result = ott.local_media_command(client, 'dev_t1', {'op': 'connect', 'source': 'Movies'}, 45, session=session)
        self.assertEqual(result, CONTRACT['connect_result'])
        client.call.assert_called_once_with('dev_t1', 'capabilities', {})
        session.call.assert_called_once_with('local_media', CONTRACT['connect_request']['params'])

    def test_mutation_refreshes_runtime_even_if_read_context_is_supplied(self):
        stale = caps()
        current = caps()
        current['player']['runtime'] = 'page-456'
        reply = dict(CONTRACT['connect_result'], runtime='page-456')
        client = mock.Mock(timeout=45)
        client.call.side_effect = [current, reply]
        ott.local_media_command(client, 'dev_t1', {'op': 'connect', 'source': 'Movies'}, 45, controls=stale)
        self.assertEqual(client.call.call_args_list,
                         [mock.call('dev_t1', 'capabilities', {}),
                          mock.call('dev_t1', 'local_media', {'op': 'connect', 'source': 'Movies', 'runtime': 'page-456'})])

    def test_progressive_opt_in_cannot_leak_from_supplied_arguments_or_stale_caps(self):
        params = {'op': 'shuffle', 'source': 'Movies', 'progressive': True}
        original = dict(params)
        client = mock.Mock(timeout=45)
        client.call.side_effect = [caps(), CONTRACT['shuffle_result']]
        ott.local_media_command(client, 'dev_t1', params, 45, controls=caps(progressive=True))
        self.assertEqual(params, original)
        self.assertEqual(client.call.call_args_list,
                         [mock.call('dev_t1', 'capabilities', {'local_media_progressive': True}),
                          mock.call('dev_t1', 'local_media', CONTRACT['shuffle_request']['params'])])

    def test_mixed_four_player_fanout_reports_partial_counts_in_plain_and_json(self):
        selector = '/Users/example/v/data/FFFF/'  # The shell expands ~/ before the CLI receives it.
        for json_output in (False, True):
            client = ott.Client.__new__(ott.Client)
            client.config = {'players': {f't{n}': f'dev_{n}' for n in range(1, 5)}}
            client.credentials = {'devices': [{'id': f'dev_{n}'} for n in range(1, 5)]}
            client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
            def reply(device, action, params):
                if action == 'capabilities':
                    if device == 'dev_2' and params:
                        raise ott.HTTPError(400)
                    value = caps(progressive=device != 'dev_3')
                    value['player']['runtime'] = device.replace('_', '-')
                    return value
                value = copy.deepcopy(CONTRACT['shuffle_result'])
                value['runtime'] = params['runtime']
                value['source']['name'] = 'FFFF'
                value['count'] = {'dev_1': 46, 'dev_2': 13611, 'dev_3': 12, 'dev_4': 1}[device]
                if device in ('dev_1', 'dev_4'):
                    value['scanning'] = True
                return value
            client.call = mock.Mock(side_effect=reply)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value=client.config), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ott.main((['-j'] if json_output else []) + ['t1,t2,t3,t4', 'local', 'shuffle', selector])
            self.assertEqual((code, err.getvalue()), (0, ''))
            mutations = [c for c in client.call.call_args_list if c.args[1] == 'local_media']
            self.assertEqual(len(mutations), 4)
            for index, call in enumerate(mutations, 1):
                expected = {'op': 'shuffle', 'source': selector, 'runtime': f'dev-{index}'}
                if index in (1, 4):
                    expected['progressive'] = True
                self.assertEqual(call, mock.call(f'dev_{index}', 'local_media', expected))
            self.assertEqual(client.call.call_count, 9)
            self.assertEqual(client.timeout, 45)
            if json_output:
                data = json.loads(out.getvalue())
                self.assertEqual((data['succeeded'], data['failed']), (4, 0))
                self.assertEqual([row['data'].get('scanning') for row in data['results']], [True, None, None, True])
                self.assertEqual([row['data']['count'] for row in data['results']], [46, 13611, 12, 1])
            else:
                text = out.getvalue()
                self.assertIn('t1: Local source: FFFF; 46 items discovered so far; scanning continues.', text)
                self.assertIn('t4: Local source: FFFF; 1 item discovered so far; scanning continues.', text)
                self.assertIn('t2: Local source: FFFF; 13611 items; shuffled once.', text)
                self.assertIn('t3: Local source: FFFF; 12 items; shuffled once.', text)
                self.assertEqual(text.count('Playback request accepted; playback has not been confirmed.'), 4)
                self.assertFalse(any('shuffled once' in line for line in text.splitlines()
                                     if line.startswith(('t1:', 't4:'))))

    def test_group_source_selection_is_once_per_distinct_player(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 'same': 'dev_1', 't2': 'dev_2'}}
        client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
        def reply(device, action, params):
            if action == 'capabilities':
                value = caps(); value['player']['runtime'] = device.replace('_', '-'); return value
            value = copy.deepcopy(CONTRACT['shuffle_result']); value['runtime'] = params['runtime']; return value
        client.call = mock.Mock(side_effect=reply)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value=client.config), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-j', 't1,same,t2', 'local', 'shuffle', 'Movies'])
        self.assertEqual((code, err.getvalue()), (0, ''))
        self.assertEqual(json.loads(out.getvalue())['succeeded'], 2)
        mutations = [c for c in client.call.call_args_list if c.args[1] == 'local_media']
        self.assertEqual(mutations, [mock.call('dev_1', 'local_media', {'op': 'shuffle', 'source': 'Movies', 'runtime': 'dev-1'}),
                                     mock.call('dev_2', 'local_media', {'op': 'shuffle', 'source': 'Movies', 'runtime': 'dev-2'})])


if __name__ == '__main__':
    unittest.main()
