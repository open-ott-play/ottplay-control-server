import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_plex_library', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / 'contracts/plex-library-v1.json').read_text())
SECRET = 'private-server-token-must-not-appear'


def caps():
    return {'version': 1, 'player': {'version': '1.1.54', 'platform': 'webos', 'runtime': 'page-123'},
            'lifecycle': [], 'input': [], 'playback': [],
            'plex_library': {'version': 1, 'operations': ['list', 'preview', 'play'], 'max_items': 500}}


def result(op='play', shuffled=True):
    value = copy.deepcopy(CONTRACT[op + '_result'])
    if op != 'list':
        value['shuffled'] = shuffled
    return value


def capped_result(op='play', count=500):
    value = result(op)
    value['truncated'] = True
    value['queue']['ids'] = [str(i + 1) for i in range(count)]
    if op == 'preview':
        value['queue']['titles'] = ['Item ' + str(i + 1) for i in range(count)]
    return value


class PlexLibraryTest(unittest.TestCase):
    def run_cli(self, words, responses, json_output=False):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--json'] if json_output else []) + ['tv'] + words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client.call.call_args_list

    def test_contract(self):
        self.assertEqual(set(CONTRACT['operations']), ott.PLEX_LIBRARY_OPERATIONS)
        self.assertEqual(set(CONTRACT['errors']), set(ott.PLEX_LIBRARY_ERRORS))
        self.assertEqual(CONTRACT['max_items'], ott.MAX_PLEX_QUEUE_ITEMS)
        for op in CONTRACT['operations']:
            params = CONTRACT[op + '_request']['params']
            self.assertEqual(ott.plex_library_metadata(result(op), 'page-123', params), result(op))
        supported = dict(caps(), **CONTRACT['truncation_capability'])
        self.assertEqual(ott.capabilities_metadata(supported)['plex_library_truncation'], {'version': 1})
        params = CONTRACT['truncation_request']['params']
        self.assertEqual(ott.plex_library_metadata(capped_result(), 'page-123', params), capped_result())

    def test_exact_aliases_options_and_explicit_library_id(self):
        cases = [
            (['PlEx', 'ShUfFlE', 'Три', 'кота'], {'op': 'play', 'query': 'Три кота', 'shuffle': True}),
            (['plex', 'library', 'Три кота'], {'op': 'play', 'query': 'Три кота', 'shuffle': False}),
            (['plex', 'library', '--shuffle', '--preview', 'id:16'], {'op': 'preview', 'library_id': '16', 'shuffle': True}),
            (['plex', 'library', '--preview', 'ID:16'], {'op': 'preview', 'library_id': '16', 'shuffle': False}),
            (['plex', 'libraries'], {'op': 'list'}),
            (['plex', 'library', '--', '--a-title'], {'op': 'play', 'query': '--a-title', 'shuffle': False}),
        ]
        for words, params in cases:
            with self.subTest(words=words):
                self.assertEqual(ott.parse_command(words), ('plex_library', params))
                data = result(params['op'], params.get('shuffle', False))
                code, out, err, calls = self.run_cli(words, [caps(), data], True)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(json.loads(out), data)
                self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                        mock.call('dev_tv', 'plex_library', dict(params, runtime='page-123'))])

    def test_invalid_syntax_and_byte_bounds_dispatch_nothing(self):
        for suffix in [[], ['--shuffle'], ['--preview'], [''], ['   '], ['--unknown', 'A'],
                       ['--shuffle', '--shuffle', 'A'], ['--preview', '--preview', 'A'],
                       ['id:0'], ['id:01'], ['id:-1'], ['id:16', 'extra'], ['id:' + '1' * 21],
                       ['x\nname'], ['x\x7f'], ['\ud800'], ['я' * 128 + 'x']]:
            code, out, err, calls = self.run_cli(['plex', 'library'] + suffix, [])
            self.assertEqual((code, out, calls), (1, '', []), suffix)
        for words in [['plex', 'libraries', 'A'], ['plex', 'libraries', '--shuffle']]:
            self.assertEqual(self.run_cli(words, [])[3], [])
        self.assertEqual(ott.parse_command(['plex', 'library', 'я' * 128])[1]['query'], 'я' * 128)
        self.assertEqual(ott.parse_command(['plex', 'library', 'id:' + '1' * 20])[1]['library_id'], '1' * 20)

    def test_truncation_is_negotiated_without_changing_legacy_or_list_requests(self):
        supported = caps()
        supported['plex_library_truncation'] = {'version': 1}
        for op, words in [('play', ['plex', 'shuffle', 'A']),
                          ('preview', ['plex', 'library', '--preview', '--shuffle', 'A'])]:
            with self.subTest(op=op):
                data = capped_result(op)
                code, out, err, calls = self.run_cli(words, [supported, data], True)
                self.assertEqual((code, err, json.loads(out)), (0, '', data))
                self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                        mock.call('dev_tv', 'plex_library', {
                                            'op': op, 'query': 'A', 'shuffle': True,
                                            'runtime': 'page-123', 'allow_truncate': True})])
                # A complete smaller result is still valid for an opted-in request.
                self.assertEqual(self.run_cli(words, [supported, result(op)])[0], 0)
                code, _, _, legacy_calls = self.run_cli(words, [caps(), result(op)])
                self.assertEqual(code, 0)
                self.assertNotIn('allow_truncate', legacy_calls[-1].args[2])
        code, _, _, calls = self.run_cli(['plex', 'libraries'], [supported, result('list')])
        self.assertEqual(code, 0)
        self.assertEqual(calls[-1], mock.call('dev_tv', 'plex_library', {'op': 'list', 'runtime': 'page-123'}))

    def test_capped_queue_warns_without_claiming_playback_or_polluting_json(self):
        supported = caps()
        supported['plex_library_truncation'] = {'version': 1}
        for op, words in [('play', ['plex', 'shuffle', 'A']),
                          ('preview', ['plex', 'library', '--preview', '--shuffle', 'A'])]:
            code, out, err, calls = self.run_cli(words, [supported, capped_result(op)])
            self.assertEqual((code, err, len(calls)), (0, '', 2))
            self.assertTrue(out.startswith('Warning: The Plex library exceeds the 500-item queue limit; using 500 items.\n'))
            if op == 'play':
                self.assertIn('playback has not been confirmed', out)
            data = capped_result(op)
            del data['truncated']
            code, out, err, _ = self.run_cli(words, [supported, data])
            self.assertEqual((code, err), (0, ''))
            self.assertNotIn('Warning:', out)

    def test_truncation_capability_is_strict_and_requires_the_500_item_library(self):
        for extension in [None, True, 1, {}, {'version': True}, {'version': 2},
                          {'version': 1, 'token': SECRET}]:
            value = caps()
            value['plex_library_truncation'] = extension
            code, out, err, calls = self.run_cli(['plex', 'shuffle', 'A'], [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
            self.assertNotIn(SECRET, err)
        for maximum in [None, 499]:
            value = caps()
            value['plex_library_truncation'] = {'version': 1}
            if maximum is None:
                del value['plex_library']
            else:
                value['plex_library']['max_items'] = maximum
            self.assertEqual(len(self.run_cli(['plex', 'shuffle', 'A'], [value])[3]), 1)
        legacy = caps()
        legacy['plex_library']['max_items'] = 3
        self.assertEqual(self.run_cli(['plex', 'shuffle', 'A'], [legacy, result()])[0], 0)

    def test_truncated_result_requires_opt_in_exact_limit_and_true_marker(self):
        supported = caps()
        supported['plex_library_truncation'] = {'version': 1}
        for marker in [False, 0, 1, None, 'true', {}, []]:
            data = capped_result()
            data['truncated'] = marker
            code, out, _, calls = self.run_cli(['plex', 'shuffle', 'A'], [supported, data])
            self.assertEqual((code, out, len(calls)), (1, '', 2), marker)
        for count in [0, 1, 499, 501]:
            code, out, _, calls = self.run_cli(['plex', 'shuffle', 'A'], [supported, capped_result(count=count)])
            self.assertEqual((code, out, len(calls)), (1, '', 2), count)
        self.assertEqual(self.run_cli(['plex', 'shuffle', 'A'], [caps(), capped_result()])[0], 1)
        data = result('list'); data['truncated'] = True
        self.assertEqual(self.run_cli(['plex', 'libraries'], [supported, data])[0], 1)
        data = dict(CONTRACT['error_result'], truncated=True)
        self.assertEqual(self.run_cli(['plex', 'shuffle', 'A'], [supported, data])[0], 1)

    def test_opted_in_errors_are_not_retried_or_mistaken_for_capped_success(self):
        supported = caps()
        supported['plex_library_truncation'] = {'version': 1}
        for response in [dict(CONTRACT['error_result'], error='incomplete_library'),
                         ott.HTTPError(400), ott.TransportError('Timeout; do not repeat')]:
            code, out, _, calls = self.run_cli(['plex', 'shuffle', 'A'], [supported, response])
            self.assertEqual((code, out, len(calls)), (1, '', 2))

    def test_existing_commands_and_reserved_channel_names_keep_their_meaning(self):
        self.assertEqual(ott.parse_command(['plex', 'play', '16']), ('plex_queue', {'op': 'play', 'ids': ['16']}))
        self.assertEqual(ott.parse_command(['plex', 'preview', '16']), ('plex_queue', {'op': 'preview', 'ids': ['16']}))
        with mock.patch.object(ott.secrets, 'SystemRandom') as random:
            self.assertEqual(ott.parse_command(['plex', 'play', '--shuffle', '16'])[0], 'plex_queue')
            random.return_value.shuffle.assert_called_once()
        self.assertEqual(ott.parse_command(['plex', 'server', 'http://nas.example:32400'])[0], 'provider_settings')
        self.assertEqual(ott.parse_command(['play', 'plex', 'shuffle']), ('play', {'query': 'plex shuffle'}))

    def test_missing_unsupported_or_malformed_capability_never_dispatches_library(self):
        variants = [None, {}, {'version': True, 'operations': ['play'], 'max_items': 500},
                    {'version': 1, 'operations': ['play'], 'max_items': True},
                    {'version': 1, 'operations': ['play'], 'max_items': 501},
                    {'version': 1, 'operations': ['play', 'play'], 'max_items': 500},
                    {'version': 1, 'operations': ['wrong'], 'max_items': 500},
                    {'version': 1, 'operations': [None], 'max_items': 500},
                    {'version': 1, 'operations': [], 'max_items': 500},
                    {'version': 1, 'operations': ['preview'], 'max_items': 500},
                    {'version': 1, 'operations': ['play'], 'max_items': 500, 'token': SECRET}]
        for variant in variants:
            value = caps()
            if variant is None:
                value.pop('plex_library')
            else:
                value['plex_library'] = variant
            code, out, err, calls = self.run_cli(['plex', 'shuffle', 'A'], [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1), variant)
            self.assertNotIn(SECRET, err)
        value = caps(); value.pop('plex_library')
        self.assertIn('updated player and controller', self.run_cli(['plex', 'shuffle', 'A'], [value])[2])

    def test_strict_result_schema_runtime_selector_shuffle_and_nested_queue(self):
        mutations = [
            lambda x: x.update(version=True), lambda x: x.update(runtime='another-page'),
            lambda x: x.update(op='preview'), lambda x: x.update(state='playing'),
            lambda x: x.update(shuffled=1), lambda x: x.update(shuffled=False),
            lambda x: x.update(extra=SECRET), lambda x: x['library'].update(id='17'),
            lambda x: x['library'].update(title='я' * 81), lambda x: x['library'].update(title='bad\nname'),
            lambda x: x['library'].update(type='artist'), lambda x: x['library'].update(url=SECRET),
            lambda x: x['queue'].update(runtime='another-page'), lambda x: x['queue'].update(index=True),
            lambda x: x['queue'].update(index=1), lambda x: x['queue'].update(ids=[]),
            lambda x: x['queue'].update(state='error'), lambda x: x.update(queue=result('preview')['queue']),
        ]
        for mutate in mutations:
            data = result(); mutate(data)
            code, out, err, calls = self.run_cli(['plex', 'shuffle', 'id:16'], [caps(), data])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
        value = caps(); value['plex_library']['max_items'] = 2
        self.assertEqual(self.run_cli(['plex', 'shuffle', 'A'], [value, result()])[0], 1)
        data = result('preview'); data['queue'] = result()['queue']
        self.assertEqual(self.run_cli(['plex', 'library', '--preview', '--shuffle', 'A'], [caps(), data])[0], 1)

    def test_list_is_complete_bounded_distinct_and_safe(self):
        for rows in [None, [{}], [dict(id='16', title='A', type='track')],
                     [dict(id=16, title='A', type='show')], [dict(id='16', title='', type='show')],
                     [dict(id='16', title='A', type='show')] * 2,
                     [dict(id=str(i + 1), title='A', type='movie') for i in range(1001)]]:
            data = result('list'); data['libraries'] = rows
            self.assertEqual(self.run_cli(['plex', 'libraries'], [caps(), data])[0], 1)
        data = result('list')
        data['libraries'] = [dict(id=str(i + 1), title='<' * 160, type='movie') for i in range(1000)]
        self.assertEqual(self.run_cli(['plex', 'libraries'], [caps(), data], True)[0], 0)
        data['libraries'] = []
        self.assertIn('No movie or show libraries', self.run_cli(['plex', 'libraries'], [caps(), data])[1])

    def test_errors_redacted_bound_and_never_retried(self):
        for problem in set(ott.PLEX_LIBRARY_ERRORS) | ott.PLEX_QUEUE_ERRORS:
            data = dict(CONTRACT['error_result'], error=problem)
            for response in [data, ott.PlayerRejected(SECRET, data)]:
                code, out, err, calls = self.run_cli(['plex', 'shuffle', 'A'], [caps(), response])
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn(ott.PLEX_LIBRARY_ERRORS.get(problem, problem), err)
                self.assertNotIn(SECRET, err)
        for data in [dict(CONTRACT['error_result'], error=SECRET),
                     dict(CONTRACT['error_result'], runtime='wrong'),
                     dict(CONTRACT['error_result'], library=SECRET)]:
            code, out, err, calls = self.run_cli(['plex', 'shuffle', 'A'], [caps(), ott.PlayerRejected(SECRET, data)])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
            self.assertNotIn('Several Plex libraries match', err)
        for error in [ott.PlayerUnsupported(SECRET), ott.HTTPError(400), ott.HTTPError(404),
                      ott.TransportError('Timeout; do not repeat')]:
            code, out, err, calls = self.run_cli(['plex', 'shuffle', 'A'], [caps(), error])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)

    def test_one_total_deadline_is_not_reset(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        params = {'op': 'play', 'query': 'A', 'shuffle': True}
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 46]), \
                self.assertRaisesRegex(ott.Error, 'no library command'):
            ott.plex_library_command(client, 'dev_tv', params, 45)
        self.assertEqual((client.call.call_count, client.timeout), (1, 45))
        client.reset_mock(); client.call.side_effect = [caps(), result()]
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 2, 46]), \
                self.assertRaisesRegex(ott.Error, 'Acceptance is uncertain'):
            ott.plex_library_command(client, 'dev_tv', params, 45)
        self.assertEqual((client.call.call_count, client.timeout), (2, 45))

    def test_human_output_and_status_make_library_feature_discoverable(self):
        code, out, err, _ = self.run_cli(['plex', 'shuffle', 'A'], [caps(), result()])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('shuffled once', out)
        self.assertIn('playback has not been confirmed', out)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_status({'capabilities': ott.capabilities_metadata(caps())}, 'l')
        self.assertIn('ott l plex shuffle', out.getvalue())
        self.assertIn('ott l plex libraries', out.getvalue())
        self.assertNotIn('No controls', out.getvalue())

    def test_request_receipt_retains_library_action_without_private_params(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 45
        request_id = 'a' * 32
        client.api = mock.Mock(return_value=(202, {'id': request_id}))
        client._wait_receipt = mock.Mock(return_value={'request_id': request_id, 'status': 'ok', 'data': result()})
        client.on_receipt = ott.print_request_receipt
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            client.call_receipt('private-device', 'plex_library', {'query': SECRET})
        self.assertEqual(json.loads(err.getvalue()), {'action': 'plex_library', 'request_id': request_id, 'status': 'ok'})
        self.assertNotIn(SECRET, err.getvalue())
        client.api.assert_called_once()


if __name__ == '__main__':
    unittest.main()
