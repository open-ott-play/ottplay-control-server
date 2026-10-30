"""Explicit player groups reuse the ordinary command handlers without replay."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_player_groups', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class PlayerGroupsTest(unittest.TestCase):
    def client(self, aliases=None):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': aliases or {f't{i}': f'dev_{i}' for i in range(1, 5)}}
        client.credentials = {'devices': [{'id': value} for value in dict.fromkeys(client.config['players'].values())]}
        client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
        client.call = mock.Mock(side_effect=self.reply)
        return client

    def caps(self, device):
        return {'version': 1, 'player': {'version': 'test', 'platform': 'browser', 'runtime': device.replace('_', '-')},
                'lifecycle': ['standby', 'wake'], 'input': [], 'playback': [], 'random': {'version': 1},
                'categories': {'version': 1, 'operations': ['list', 'select'], 'maxPageSize': 200}}

    def reply(self, device, action, params):
        channel = {'id': device, 'number': 7, 'name': 'Test channel'}
        if action == 'capabilities':
            return self.caps(device)
        if action == 'status':
            return {'provider': 'm3u', 'ready': True, 'volume': 30, 'player': self.caps(device)['player']}
        if action == 'categories':
            return {'version': 1, 'runtime': device.replace('_', '-'), 'op': 'list', 'catalog': 'catalog-' + device,
                    'provider': 'm3u', 'selected': 'all', 'offset': 0, 'total': 1,
                    'has_more': False, 'loading': False,
                    'categories': [{'id': 'all', 'name': 'All', 'count': 100}]}
        if action in ('play', 'play_catalog'):
            return {'dispatched': True, 'channel': channel}
        if action == 'playback':
            return {**params, 'dispatched': True, 'channel': channel}
        if action == 'command':
            return {'dispatched': True, 'volume': params['volume'], 'private': 'never-print'}
        if action == 'lifecycle':
            return {**params, 'accepted': True, 'dispatched': False, 'effect': 'lifecycle-after-ack'}
        if action == 'restart':
            return {**params, 'accepted': True, 'dispatched': params['target'] == 'stream', 'effect': 'reload-after-ack'}
        if action == 'providers':
            return {'providers': [{'id': 'm3u', 'index': 0, 'name': 'M3U', 'active': True}]}
        raise AssertionError((device, action, params))

    def run_cli(self, words, client=None):
        client = client or self.client()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=client.config), \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_random_uses_each_players_catalog_once_for_four_players(self):
        for verb in ('R', 'r', 'random', 'RANDOM'):
            code, out, err, client = self.run_cli(['-j', 't1,t2,t3,t4', verb])
            self.assertEqual((code, err), (0, ''))
            data = json.loads(out)
            self.assertEqual((data['succeeded'], data['failed']), (4, 0))
            self.assertEqual([row['data']['channel']['id'] for row in data['results']], [f'dev_{i}' for i in range(1, 5)])
            self.assertEqual(client.call.call_args_list, [call for i in range(1, 5) for call in (
                mock.call(f'dev_{i}', 'capabilities', {}),
                mock.call(f'dev_{i}', 'categories', {'op': 'list', 'runtime': f'dev-{i}', 'offset': 0, 'limit': 200}),
                mock.call(f'dev_{i}', 'play_catalog', {'catalog': f'catalog-dev_{i}', 'random': {}}))])

    def test_existing_command_families_keep_their_single_player_wire_requests(self):
        for words in (['1'], ['next'], ['prev'], ['+15'], ['-15'], ['v', '42'], ['volume', '42'],
                      ['standby'], ['go'], ['restart'], ['restart', 'stream'], ['caps'], ['providers']):
            with self.subTest(words=words):
                single = self.run_cli(['-j', 't1', *words])
                group = self.run_cli(['-j', 't1,t2', *words])
                self.assertEqual((single[0], group[0], group[2]), (0, 0, ''))
                original = single[3].call.call_args_list
                expected = original + [mock.call('dev_2', *call.args[1:]) for call in original]
                self.assertEqual(group[3].call.call_args_list, expected)
                self.assertEqual(json.loads(group[1])['results'][0]['data'], json.loads(single[1]))
                self.assertNotIn('never-print', group[1])

    def test_bare_group_and_status_show_help_before_each_summary(self):
        for tail in ([], ['status']):
            code, out, err, client = self.run_cli(['t1,t2', *tail])
            self.assertEqual((code, err), (0, ''))
            for name in ('t1', 't2'):
                self.assertLess(out.index(name + ': Available controls'), out.index(name + ': Player summary:'))
            self.assertEqual(client.call.call_count, 4)

    def test_all_names_resolve_before_dispatch_deduplicate_and_reserved_alias_is_not_management(self):
        client = self.client({'t1': 'dev_1', 'same': 'dev_1', 'devices': 'dev_2'})
        events, original = [], client.device
        client.device = lambda name: (events.append(name), original(name))[1]
        client.call.side_effect = lambda *args: (self.assertEqual(events, ['T1', 'same', 'devices']), self.reply(*args))[1]
        code, out, err, client = self.run_cli(['-j', 'T1,same,devices', 'go'], client)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual([row['player'] for row in json.loads(out)['results']], ['T1', 'devices'])
        self.assertEqual(client.call.call_count, 2)
        for selector in ('t1,missing', 't1,', ',t1', ','.join(['t1'] * 65)):
            code, out, _, client = self.run_cli([selector, 'go'])
            self.assertEqual((code, out, client.call.call_count), (1, '', 0))

    def test_per_player_failures_are_isolated_and_never_replayed(self):
        for failure in (ott.PlayerRejected('never-print', {}), ott.TransportError('Request may have executed'),
                        RuntimeError('never-print'), {'accepted': True}):
            client = self.client()
            client.call.side_effect = [failure, self.reply('dev_2', 'lifecycle', {'operation': 'wake'})]
            code, out, err, client = self.run_cli(['-j', 't1,t2', 'go'], client)
            data = json.loads(out)
            self.assertEqual((code, err, data['succeeded'], data['failed'], client.call.call_count), (1, '', 1, 1, 2))
            self.assertEqual([row['exit_code'] for row in data['results']], [1, 0])
            self.assertNotIn('never-print', out + err)

    def test_generic_receipts_are_immediate_tagged_json_and_callback_restored(self):
        client = self.client()
        def call(device, action, params):
            client.on_receipt({'action': action, 'status': 'ok', 'request_id': 'a' * 32})
            return self.reply(device, action, params)
        client.call.side_effect = call
        code, out, err, _ = self.run_cli(['-j', '--receipt', 't1,t2', 'go'], client)
        self.assertEqual(code, 0)
        self.assertEqual([json.loads(line)['player'] for line in err.splitlines()], ['t1', 't2'])
        self.assertEqual(json.loads(out)['succeeded'], 2)
        self.assertIs(client.on_receipt, ott.print_request_receipt)

    def test_keyboard_interrupt_stops_remaining_targets_and_retains_results(self):
        client = self.client()
        client.call.side_effect = [self.reply('dev_1', 'lifecycle', {'operation': 'wake'}), KeyboardInterrupt()]
        code, out, err, client = self.run_cli(['-j', 't1,t2,t3,t4', 'go'], client)
        self.assertEqual((code, err, client.call.call_count), (130, '', 2))
        rows = json.loads(out)['results']
        self.assertEqual([row['exit_code'] for row in rows], [0, 130, 130, 130])
        self.assertTrue(rows[0]['ok'])
        self.assertIn('Not started', rows[-1]['error'])

    def test_plex_hidden_token_is_requested_once_before_capture_and_not_printed(self):
        client = self.client()
        client.call.side_effect = None
        client.call.return_value = {'provider': 'plex', 'saved': True, 'fields': ['token']}
        with mock.patch.object(ott.getpass, 'getpass', return_value='never-print') as prompt:
            code, out, err, client = self.run_cli(['-j', 't1,t2', 'plex', 'token'], client)
        self.assertEqual((code, err, prompt.call_count, client.call.call_count), (0, '', 1, 2))
        self.assertNotIn('never-print', out + err)

    def module_cli(self, words, runner, client=None):
        module = SimpleNamespace(run=runner)
        groups = ott.player_groups_module()
        fake_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        with mock.patch.object(ott, 'player_groups_module', return_value=groups), \
                mock.patch.object(ott.importlib.util, 'spec_from_file_location', return_value=fake_spec), \
                mock.patch.object(ott.importlib.util, 'module_from_spec', return_value=module):
            return self.run_cli(words, client)

    def test_every_optional_module_reuses_its_normal_runner_and_arguments(self):
        for words in (['perf', 'status'], ['settings'], ['setting', 'theme', 'dark'],
                      ['update', '--variant', 'full'], ['doctor'], ['debug', '--engine'],
                      ['inspect'], ['operation', 'a' * 32], ['test', 'list']):
            calls = []
            def run(api, client, device, tail, *options):
                calls.append((device, tail, options))
                print(json.dumps({'device': device, 'ok': True}))
                return 0
            with self.subTest(words=words):
                code, out, err, _ = self.module_cli(['-j', 't1,t2', *words], run)
                self.assertEqual((code, err, json.loads(out)['succeeded']), (0, '', 2))
                self.assertEqual([item[0] for item in calls], ['dev_1', 'dev_2'])
                self.assertEqual(calls[0][1], words[1:] if words[0] == 'perf' else words)

    def test_real_local_workbench_command_accepts_module_json_flag(self):
        code, out, err, client = self.run_cli(['t1,t2', 'test', 'list', '--json'])
        self.assertEqual((code, err, client.call.call_count), (0, '', 0))
        self.assertEqual([row['data']['command'] for row in json.loads(out)['results']], ['test', 'test'])

    def test_module_return_codes_help_and_interrupt_are_preserved(self):
        for codes, expected, count in (([3, 0], 3, 2), ([2, 3], 2, 2), ([1, 2], 1, 2), ([130], 130, 1)):
            values, called = iter(codes), []
            def run(api, client, device, *args):
                called.append(device)
                print(json.dumps({'device': device}))
                return next(values)
            code, out, _, _ = self.module_cli(['-j', 't1,t2', 'doctor'], run)
            self.assertEqual((code, len(called)), (expected, count))
            self.assertEqual(json.loads(out)['results'][0]['exit_code'], codes[0])
        def help_runner(*args):
            print('Usage: safe help')
            raise SystemExit(0)
        code, out, _, _ = self.module_cli(['-j', 't1,t2', 'doctor', '--help'], help_runner)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['results'][0]['output'], 'Usage: safe help')

    def test_output_files_are_siblings_and_existing_second_output_blocks_every_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'screen.png'
            def screenshot(client, device, name, output, timeout):
                with open(output, 'x') as stream:
                    stream.write(device)
                return {'path': output, 'width': 1, 'height': 1, 'source': 'player-view', 'video': 'unknown'}
            with mock.patch.object(ott, 'screenshot_command', side_effect=screenshot) as capture:
                code, out, _, _ = self.run_cli(['-j', 't1,t2', 'shot', '-o', str(path)])
                self.assertEqual((code, capture.call_count), (0, 2))
                self.assertEqual([Path(row['data']['path']).name for row in json.loads(out)['results']], ['screen.t1.png', 'screen.t2.png'])
                (Path(temporary) / 'screen.t1.png').unlink()
                capture.reset_mock()
                code, out, _, client = self.run_cli(['t1,t2', 'screenshot', '--output', str(path)])
                self.assertEqual((code, out, capture.call_count, client.call.call_count), (1, '', 0, 0))

    def test_module_directory_outputs_expand_equals_and_abbreviated_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            for words, flag in ((['bundle'], '--out'), (['test', 'run', 'health'], '--report'),
                                (['perf', 'export', 'a' * 32], '--out'), (['perf', 'trial', 'notifications'], '--out')):
                for option in ([flag, temporary + '/evidence'], [flag + '=' + temporary + '/evidence'],
                               [flag[:3], temporary + '/evidence']):
                    calls = []
                    def run(api, client, device, tail, *args):
                        calls.append(tail)
                        print(json.dumps({'device': device}))
                        return 0
                    code, _, err, _ = self.module_cli(['-j', 't1,t2', *words, *option], run)
                    self.assertEqual((code, err), (0, ''))
                    for index, call in enumerate(calls, 1):
                        argument = call[-1]
                        value = argument.split('=', 1)[1] if argument.startswith('--') and '=' in argument else argument
                        self.assertEqual(Path(value), Path(temporary) / f'evidence.t{index}')

    def test_output_mapping_covers_profile_native_and_checkpoint_but_never_inputs(self):
        cases = [(['prof', 'export', '1', 'movies.json'], 'movies.t1.json'),
                 (['android', 'screenshot', '-o', 'native.png'], 'native.t1.png'),
                 (['load', 'Home', 'apply', '--checkpoint', 'state.json'], 'state.t1.json'),
                 (['load', 'Home', 'resume', '--checkpoint', 'state.json'], 'state.t1.json')]
        for words, expected in cases:
            route = ott.player_command_route(words)
            prepared = ott.parse_command(words) if route is None else None
            result, _, paths = ott.group_output_arguments(words, route, prepared, 't1')
            self.assertEqual(result[-1], expected)
            self.assertEqual(len(paths), 1)
        for words in (['setting', 'token', '--value-file', 'secret'], ['load', 'Home', 'verify', '--checkpoint', 'secret'],
                      ['provider-config', 'secret'], ['profile-config', '1', 'secret'], ['plex', 'token-file', 'secret']):
            result, _, paths = ott.group_output_arguments(words, ott.player_command_route(words), None, 't1')
            self.assertEqual((result, paths), (words, []))

    def test_sanitized_suffix_is_stable_for_reordered_groups_and_resume_subset(self):
        targets = [('room/a', 'dev_1'), ('room?a', 'dev_2'), ('t1', 'dev_3')]
        full = ott.group_output_suffixes(targets)
        self.assertEqual(len(set(full)), 3)
        self.assertEqual(ott.group_output_suffixes(targets[::-1]), full[::-1])
        self.assertEqual(ott.group_output_suffixes(targets[:1]), full[:1])
        self.assertEqual(full[-1], 't1')
        self.assertRegex(full[0], r'^room_a-[a-f0-9]{12}$')

    def test_native_binding_fails_before_config_write_even_with_uppercase_command(self):
        for verb in ('bind', 'BIND', 'Bind'):
            with mock.patch.object(ott, 'write_private') as write:
                code, out, err, client = self.run_cli(['t1,t2', 'android', verb, 't3'])
            self.assertEqual((code, out, client.call.call_count), (1, '', 0))
            self.assertIn('cannot be shared', err)
            write.assert_not_called()

    def test_native_controls_use_resolved_players_without_reentering_global_dispatch(self):
        with mock.patch.object(ott, 'android_command', return_value={'observed': True}) as native:
            code, out, err, client = self.run_cli(['-j', 't1,t2', 'android', 'go'])
        self.assertEqual((code, err, json.loads(out)['succeeded']), (0, '', 2))
        self.assertEqual([(call.args[2], call.args[3], call.args[4]) for call in native.call_args_list],
                         [('dev_1', 't1', ['go']), ('dev_2', 't2', ['go'])])
        self.assertTrue(all(call.args[0] is client for call in native.call_args_list))

    def test_presets_reuse_resolved_connection_for_legacy_load_and_session_plan(self):
        client = self.client()
        client.config['presets'] = {'Home': {
            'm3u': [{'number': 1, 'name': 'Home', 'playlist': 'https://playlist.example/test', 'history_hours': 24}],
            'plex': {'server': 'https://plex.example', 'token': 'never-print'}, 'active_profile': 1}}
        resolutions, original = [], client.device
        client.device = lambda name: (resolutions.append(name), original(name))[1]
        with mock.patch.object(ott, 'load_preset', return_value={'status': 'loaded', 'preset': 'Home', 'active_profile': 1}) as load:
            code, out, err, _ = self.run_cli(['-j', 't1,t2', 'load', 'Home'], client)
        self.assertEqual((code, err, json.loads(out)['succeeded']), (0, '', 2))
        self.assertEqual(resolutions, ['t1', 't2'])
        self.assertEqual([call.args[1] for call in load.call_args_list], ['dev_1', 'dev_2'])
        self.assertTrue(all(call.args[0] is client for call in load.call_args_list))
        resolutions.clear()
        code, out, err, _ = self.run_cli(['-j', 't1,t2', 'load', 'Home', 'plan'], client)
        self.assertEqual((code, err, resolutions, client.call.call_count), (0, '', ['t1', 't2'], 0))
        self.assertEqual([row['data']['device'] for row in json.loads(out)['results']], ['dev_1', 'dev_2'])
        self.assertNotIn('never-print', out)

    def test_relative_output_beginning_with_dash_keeps_option_safe_spelling(self):
        words, _, _ = ott.group_output_arguments(['bundle', '--out', './-report'], 'workbench', None, 't1')
        self.assertEqual(words[:2], ['bundle', '--out'])
        self.assertEqual(Path(words[-1]), Path('-report.t1'))
        self.assertFalse(words[-1].startswith('-'))
        self.assertFalse(Path(words[-1]).is_absolute())
        module_spec = importlib.util.spec_from_file_location('group_workbench_args', Path(ott.__file__).with_name('workbench.py'))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        self.assertEqual(module.arguments(words, False).out, words[-1])

    def test_output_limits_report_incomplete_results_without_replaying_commands(self):
        for output_limit, aggregate_limit, expected in ((20, 1000, [False, False]), (1000, 50, [True, False])):
            calls = []
            def run(api, client, device, *args):
                calls.append(device)
                print(json.dumps({'text': 'x' * 20}))
                return 0
            with mock.patch.object(ott, 'GROUP_OUTPUT_LIMIT', output_limit), \
                    mock.patch.object(ott, 'GROUP_AGGREGATE_LIMIT', aggregate_limit):
                code, out, _, _ = self.module_cli(['-j', 't1,t2', 'doctor'], run)
            self.assertEqual((code, calls), (1, ['dev_1', 'dev_2']))
            rows = json.loads(out)['results']
            self.assertEqual([row['ok'] for row in rows], expected)
            self.assertIn('may have executed', rows[-1]['error'])
            self.assertNotIn('data', rows[-1])


if __name__ == '__main__':
    unittest.main()
