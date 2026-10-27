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


def caps(native=True):
    value = {'version': 1, 'player': {'version': 'test', 'platform': 'tauri', 'runtime': 'page-123'},
             'lifecycle': [], 'input': [], 'playback': [], 'local_media': copy.deepcopy(CONTRACT['capability'])}
    value['local_media']['absolute_paths'] = native
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
            self.assertEqual(calls, [mock.call('dev_t1', 'capabilities', {}),
                                    mock.call('dev_t1', 'local_media', CONTRACT[op + '_request']['params'])])

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
