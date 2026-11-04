"""Selected native handles require negotiated support and exact acknowledgements."""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

import test_local_browser_cli as browser

ott, FIXTURE = browser.ott, browser.FIXTURE
DISPLAY_NAMES = json.loads((browser.ROOT / 'testdata/local_media_display_names_v1.json').read_text())


def caps(progressive=False):
    value = browser.caps()
    value.update(local_media={'version': 1, 'operations': ['sources', 'connect', 'play', 'shuffle'],
                              'absolute_paths': False, 'max_sources': 32, 'max_items': 50000},
                 local_media_handles=copy.deepcopy(FIXTURE['capabilities']['local_media_handles']),
                 local_media_session={'version': 1})
    if progressive:
        value['local_media_progressive'] = {'version': 1}
    return value


class LocalMediaHandlesTest(unittest.TestCase):
    def run_cli(self, *args, **kwargs):
        return browser.LocalBrowserTest.run_cli(self, *args, **kwargs)

    def test_shared_handle_requests_and_echo_without_legacy_fallback(self):
        for row in FIXTURE['handle_cases']:
            op = row['request']['op']
            probe = {'local_media_handles': True}
            if op != 'connect':
                probe.update(local_media_session=True, local_media_progressive=True)
            code, out, err, calls = self.run_cli(['local', op, '--handle', 'handle-1'], [caps(), row['data']])
            self.assertEqual((code, err, json.loads(out)), (0, '', row['data']))
            self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', probe),
                                    mock.call('dev_tv', 'local_media', row['request'])])

    def test_bad_or_ambiguous_handle_syntax_stops_before_discovery(self):
        for op in ('connect', 'play', 'shuffle'):
            for tail in (['--handle'], ['--handle', ''], ['--handle', '/path'], ['--handle', 'x' * 257],
                         ['--handle', 'h', 'Movies'], ['Movies', '--handle', 'h'],
                         ['--handle', 'h', '--handle', 'h']):
                code, out, _, calls = self.run_cli(['local', op, *tail], [])
                self.assertEqual((code, out, calls), (1, '', []))
        for op in ('sources', 'play'):
            client = mock.Mock(timeout=45)
            params = {'op': op, 'source_handle': 'h', 'source': 'Movies'}
            with self.assertRaises(ott.Error):
                ott.local_media_command(client, 'dev_tv', params, 45)
            client.call.assert_not_called()
        self.assertEqual(ott.parse_command(['local', 'play', '--', '--handle']),
                         ('local_media', {'op': 'play', 'source': '--handle'}))

    def test_selected_display_names_survive_handle_actions_and_source_listing(self):
        for name in DISPLAY_NAMES['valid']:
            selected = browser.case('select_without_playback_or_save')
            selected['data']['source']['name'] = name
            code, out, err, calls = self.run_cli(browser.words(selected['request']), [caps(), selected['data']])
            self.assertEqual((code, err, len(calls)), (0, '', 2))
            source = json.loads(out)['source']
            self.assertEqual(source['name'], name)
            for row in FIXTURE['handle_cases']:
                with self.subTest(name=repr(name), op=row['request']['op']):
                    data = copy.deepcopy(row['data'])
                    data['source']['name'] = name
                    data['source_handle'] = source['handle']
                    request = dict(row['request'], source_handle=source['handle'])
                    code, out, err, calls = self.run_cli(
                        ['local', request['op'], '--handle', source['handle']], [caps(), data])
                    self.assertEqual((code, err, json.loads(out)), (0, '', data))
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[-1], mock.call('dev_tv', 'local_media', request))
                    listing = {'version': 1, 'runtime': request['runtime'], 'op': 'sources',
                               'state': 'ready', 'sources': [data['source']]}
                    code, out, err, calls = self.run_cli(['local', 'sources'], [caps(), listing])
                    self.assertEqual((code, err, json.loads(out)), (0, '', listing))
                    self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                            mock.call('dev_tv', 'local_media', {
                                                'runtime': request['runtime'], 'op': 'sources'})])

    def test_invalid_display_names_reject_handle_receipt_without_retry(self):
        for name in DISPLAY_NAMES['invalid']:
            for row in FIXTURE['handle_cases']:
                with self.subTest(name=repr(name), op=row['request']['op']):
                    data = copy.deepcopy(row['data'])
                    data['source']['name'] = name
                    code, out, err, calls = self.run_cli(
                        ['local', row['request']['op'], '--handle', 'handle-1'], [caps(), data])
                    self.assertEqual((code, out, len(calls)), (1, '', 2))
                    self.assertIn('Invalid local media result', err)
                    self.assertEqual(calls[-1], mock.call('dev_tv', 'local_media', row['request']))

    def test_marker_is_closed_complete_and_requires_base_operations(self):
        base = caps()['local_media_handles']
        markers = [None, {}, True, dict(base, version=True), dict(base, version=2),
                   dict(base, operations=['connect']), dict(base, operations=['connect'] * 3),
                   dict(base, operations=['connect', 'play', {}]), dict(base, extra=True)]
        missing = caps(); del missing['local_media_handles']
        missing_base = caps(); del missing_base['local_media']
        incomplete_base = caps(); incomplete_base['local_media']['operations'] = ['connect', 'play']
        values = [missing, missing_base, incomplete_base] + [dict(caps(), local_media_handles=m) for m in markers]
        for value in values:
            code, out, _, calls = self.run_cli(['local', 'connect', '--handle', 'h'], [value])
            self.assertEqual((code, out, len(calls)), (1, '', 1))
        good = dict(caps(), local_media_handles=dict(base, operations=['shuffle', 'play', 'connect']))
        self.assertEqual(ott.capabilities_metadata(good)['local_media_handles'], good['local_media_handles'])

    def test_handle_echo_and_original_admission_checks_are_strict(self):
        for row in FIXTURE['handle_cases']:
            params, data = row['request'], row['data']
            variants = [dict(data, source_handle=value) for value in ('other', '', None, True)]
            variants += [{key: value for key, value in data.items() if key != 'source_handle'},
                         dict(data, runtime='wrong'), dict(data, playback_confirmed=True),
                         dict(data, source=dict(data['source'], available=False))]
            if params['op'] != 'connect':
                variants += [dict(data, session=False), dict(data, count=0)]
            for invalid in variants:
                code, out, _, calls = self.run_cli(['local', params['op'], '--handle', 'handle-1'], [caps(), invalid])
                self.assertEqual((code, out, len(calls)), (1, '', 2))
            # A legacy source request may not accidentally accept a handle echo.
            legacy = {key: value for key, value in params.items() if key != 'source_handle'}
            legacy['source'] = data['source']['name']
            with self.assertRaises(ott.Error):
                ott.local_media_metadata(data, params['runtime'], legacy)
            legacy_data = {key: value for key, value in data.items() if key != 'source_handle'}
            self.assertEqual(ott.local_media_metadata(legacy_data, params['runtime'], legacy), legacy_data)

    def test_progressive_session_negotiation_is_fresh_and_does_not_modify_arguments(self):
        row = FIXTURE['handle_cases'][2]
        params = {'op': 'shuffle', 'source_handle': 'handle-1', 'progressive': True}
        original = copy.deepcopy(params)
        for progressive in (False, True):
            client = mock.Mock(timeout=45)
            response = dict(row['data'], **({'scanning': True} if progressive else {}))
            client.call.side_effect = [caps(progressive), response]
            result = ott.local_media_command(client, 'dev_tv', params, 45, controls=caps(not progressive))
            request = dict(row['request'], **({'progressive': True} if progressive else {}))
            self.assertEqual(client.call.call_args_list[-1], mock.call('dev_tv', 'local_media', request))
            self.assertEqual((params, result), (original, response))
        unsupported = caps(); del unsupported['local_media_session']
        self.assertEqual(len(self.run_cli(['local', 'shuffle', '--handle', 'h'], [unsupported])[3]), 1)

    def test_errors_keep_five_fields_and_never_replay_after_uncertainty(self):
        params = FIXTURE['handle_cases'][2]['request']
        error = {'version': 1, 'runtime': params['runtime'], 'op': 'shuffle',
                 'state': 'error', 'error': 'source_unavailable'}
        self.assertEqual(ott.local_media_metadata(error, params['runtime'], params), error)
        with self.assertRaises(ott.Error):
            ott.local_media_metadata(dict(error, source_handle='handle-1'), params['runtime'], params)
        failures = (ott.HTTPError(400), ott.PlayerRejected('secret', error),
                    ott.TransportError('uncertain'), ott.RequestError('receipt_missing', 'receipt', 'uncertain'))
        for failure in failures:
            code, out, err, calls = self.run_cli(['local', 'shuffle', '--handle', 'handle-1'], [caps(), failure])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn('secret', err)
            self.assertEqual(len(self.run_cli(['local', 'shuffle', '--handle', 'handle-1'], [failure])[3]), 1)

    def test_one_budget_covers_probe_and_handle_operation(self):
        row = FIXTURE['handle_cases'][0]
        params = {'op': 'connect', 'source_handle': 'handle-1'}
        now, budgets = [0], []
        client = mock.Mock(timeout=45)
        def call(device, action, request):
            budgets.append(client.timeout)
            now[0] += 4
            return caps() if action == 'capabilities' else row['data']
        client.call.side_effect = call
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            self.assertEqual(ott.local_media_command(client, 'dev_tv', params, 10), row['data'])
        self.assertEqual((budgets, client.timeout), ([10, 6], 45))

    def test_multiplayer_handles_never_dispatch(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 't2': 'dev_2'}, 'groups': {'tt': ['t1', 't2']}}
        client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
        client.call = mock.Mock()
        for selector in ('t1,t2', 'tt'):
            for op in ('connect', 'play', 'shuffle'):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(ott, 'Client', return_value=client), \
                        mock.patch.object(ott, 'read_json', return_value=client.config), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = ott.main([selector, 'local', op, '--handle', 'h'])
                self.assertEqual((code, out.getvalue(), client.call.call_count), (1, '', 0))

    def test_help_requires_handle_marker(self):
        old = caps(); del old['local_media_handles']
        for value, present in ((old, False), (caps(), True)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ott.print_player_status({'capabilities': value}, 'TV')
            for op in ('connect', 'play', 'shuffle'):
                self.assertEqual('local ' + op + ' --handle' in out.getvalue(), present)


if __name__ == '__main__':
    unittest.main()
