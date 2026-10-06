"""Exercise aliases through CLI dispatch/output, not just token replacement."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_aliases', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class AliasDispatchTest(unittest.TestCase):
    def test_global_short_and_full_flags_share_config_timeout_and_json_dispatch(self):
        for config_flag in ['-c', '--config']:
            for timeout_flag in ['-t', '--timeout']:
                for json_flag in ['-j', '--json']:
                    with self.subTest(config=config_flag, timeout=timeout_flag, json=json_flag):
                        config = {'selected': 'private-test-config'}
                        client = mock.Mock()
                        client.device.return_value = 'dev_tv'
                        client.call.side_effect = [{'ready': True}, ott.PlayerUnsupported('old client')]
                        output, errors = io.StringIO(), io.StringIO()
                        with mock.patch.object(ott, 'read_json', return_value=config) as read, \
                                mock.patch.object(ott, 'Client', return_value=client) as create, \
                                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                            code = ott.main([config_flag, 'private config.json', timeout_flag, '12.5', json_flag, 'tv', 'st'])
                        self.assertEqual((code, errors.getvalue()), (0, ''))
                        result = json.loads(output.getvalue())
                        self.assertTrue(result['ready'])
                        self.assertIsNone(result['capabilities'])
                        self.assertIn('not reported', result['capabilities_error'])
                        read.assert_called_once_with('private config.json')
                        create.assert_called_once_with(config, 12.5)
                        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {}),
                                                                     mock.call('dev_tv', 'capabilities', {})])

    def run_cli(self, words, responses, json_output=False):
        client = mock.Mock()
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]) as choice, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main((['--json'] if json_output else []) + ['tv'] + words)
        return code, output.getvalue(), errors.getvalue(), client.call.call_args_list, choice.call_args_list

    def test_volume_read_aliases_share_projection_and_format(self):
        for command in ['v', 'V', 'vol', 'VOL', 'volume', 'VoLuMe']:
            for machine in [False, True]:
                with self.subTest(command=command, json=machine):
                    code, out, err, calls, _ = self.run_cli([command], [{'volume': 37.5, 'uuid': 'private', 'provider': 'private'}], machine)
                    self.assertEqual((code, err), (0, ''))
                    self.assertEqual(json.loads(out) if machine else out, {'volume': 37.5} if machine else '37.5%\n')
                    self.assertEqual(calls, [mock.call('dev_tv', 'status', {})])

    def test_volume_absolute_and_relative_dispatch_once_with_same_alias_output(self):
        for command in ['v', 'vol', 'VOLUME']:
            for argument, field, value in [('25', 'volume', 25), ('+5', 'volume_step', 5), ('-5', 'volume_step', -5)]:
                for machine in [False, True]:
                    with self.subTest(command=command, arg=argument, json=machine):
                        code, out, err, calls, _ = self.run_cli([command, argument], [{'volume': 25, 'dispatched': True, 'settings': 'private'}], machine)
                        self.assertEqual((code, err), (0, ''))
                        self.assertEqual(json.loads(out) if machine else out, {'volume': 25, 'dispatched': True} if machine else '25%\n')
                        self.assertEqual(calls, [mock.call('dev_tv', 'command', {'command': 'set_volume', field: value})])

    def test_invalid_or_unsupported_volume_is_rejected_before_json_or_text_output(self):
        for command in ['v', 'vol', 'volume']:
            for machine in [False, True]:
                for value in [None, True, '50', -1, 101, 10**400, float('nan'), float('inf'), {}, []]:
                    for mutate in [False, True]:
                        with self.subTest(command=command, value=value, json=machine, mutate=mutate):
                            code, out, err, calls, _ = self.run_cli([command] + (['+5'] if mutate else []),
                                [{'volume': value, 'dispatched': True, 'secret': 'never-print'}], machine)
                            self.assertEqual((code, out, len(calls)), (1, '', 1))
                            self.assertNotIn('never-print', err)
                for ack in [None, False, 1, 'true']:
                    code, out, err, calls, _ = self.run_cli([command, '+5'], [{'volume': 30, 'dispatched': ack}], machine)
                    self.assertEqual((code, out, len(calls)), (1, '', 1))
                    self.assertIn('do not repeat', err)

    def test_status_and_channel_listing_aliases_do_not_switch(self):
        for command in ['status', 'ST']:
            response = {'ready': True, 'volume': None}
            code, out, err, calls, _ = self.run_cli([command], [response, ott.PlayerUnsupported('old client')], True)
            result = json.loads(out)
            self.assertEqual((code, err, result['ready'], result['volume']), (0, '', True, None))
            self.assertIsNone(result['capabilities'])
            self.assertEqual(calls, [mock.call('dev_tv', 'status', {}), mock.call('dev_tv', 'capabilities', {})])
        for command in ['s', 'S', 'channels', 'CHANNELS']:
            for text in ['РЕН', 'рЕн ТВ']:
                rows = [{'number': 2, 'name': 'РЕН ТВ'}, {'number': 3, 'name': 'РЕН ТВ HD'}]
                code, out, err, calls, _ = self.run_cli([command, text], [{'channels': rows}])
                self.assertEqual((code, err, out), (0, '', '2: РЕН ТВ\n3: РЕН ТВ HD\n'))
                self.assertEqual(calls, [mock.call('dev_tv', 'channels', {'search': text})])

    def test_program_full_names_and_short_list_flag_never_launch_playback(self):
        rows = [{'number': 2, 'channel': 'News', 'title': 'Current'}]
        for command in ['p', 'programs', 'PROGRAMMES']:
            for flag in ['--list', '-l', '--LIST', '-L']:
                code, out, err, calls, choice = self.run_cli([command, flag, 'НОВоСТИ'], [{'programs': rows}], True)
                self.assertEqual((code, err, json.loads(out)), (0, '', {'programs': rows}))
                self.assertEqual(calls, [mock.call('dev_tv', 'programs', {'search': 'НОВоСТИ'})])
                self.assertEqual(choice, [])

    def test_full_program_command_retains_random_one_mutation_and_no_retry(self):
        rows = [{'number': 2, 'channel': 'News', 'title': 'Current'}, {'number': 3, 'channel': 'Films', 'title': 'Current film'}]
        for command in ['p', 'programs', 'programmes']:
            for response in [{'dispatched': True, 'channel': {'number': 3, 'name': 'Films'}}, ott.Error('Lost ACK; do not repeat')]:
                code, _, err, calls, choices = self.run_cli([command, 'Current'], [{'programs': rows}, response], True)
                self.assertEqual(code, 1 if isinstance(response, Exception) else 0)
                self.assertEqual(calls, [mock.call('dev_tv', 'programs', {'search': 'Current'}), mock.call('dev_tv', 'play', {'query': '3'})])
                self.assertEqual(choices, [mock.call(rows)])
                if isinstance(response, Exception): self.assertIn('do not repeat', err)

    def test_short_and_full_cyrillic_channel_names_keep_substring_random_behavior(self):
        rows = [{'id': 'ren', 'number': 2, 'name': 'РЕН ТВ'}, {'id': 'ren-hd', 'number': 3, 'name': 'РЕН ТВ HD'}]
        for words in [['рЕн'], ['РЕН', 'ТВ'], ['play', 'РЕН ТВ']]:
            code, _, err, calls, choices = self.run_cli(words, [{'channels': rows}, {'dispatched': True, 'channel': rows[-1]}])
            self.assertEqual(code, 0, err)
            text = ' '.join(words[1:] if words[0] == 'play' else words)
            self.assertEqual(calls, [mock.call('dev_tv', 'channels', {'search': text}), mock.call('dev_tv', 'play', {'query': '3'})])
            self.assertEqual(choices, [mock.call(rows)])
        row = rows[-1]
        code, _, err, calls, choices = self.run_cli(['РЕН ТВ HD'], [{'channels': [row]}, {'dispatched': True, 'channel': row}])
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[-1], mock.call('dev_tv', 'play', {'query': '3'}))
        self.assertEqual(choices, [])

    def test_reserved_alias_titles_escape_and_unknown_prefixes_are_not_commands(self):
        for title in [*ott.COMMAND_ALIASES, 'restart', 'exit', 'reboot']:
            self.assertEqual(ott.parse_command(['play', title]), ('play', {'query': title}))
        for title in ['volu', 're', 'rest', 'ex', 'pro', 'Нов']:
            self.assertEqual(ott.parse_command([title]), ('play', {'query': title}))
        row = {'id': 'reserved', 'number': 1, 'name': 'Volume'}
        code, _, err, calls, _ = self.run_cli(['play', 'Volume'], [{'channels': [row]}, {'dispatched': True, 'channel': row}])
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [mock.call('dev_tv', 'channels', {'search': 'Volume'}), mock.call('dev_tv', 'play', {'query': '1'})])

    def test_nonnumeric_signed_channel_names_keep_normal_search(self):
        for name in ['+HD', '-Новости']:
            row = {'id': 'signed-title', 'number': 8, 'name': name}
            code, _, err, calls, _ = self.run_cli([name], [{'channels': [row]}, {'dispatched': True, 'channel': row}])
            self.assertEqual((code, err), (0, 'Channel switch requested: 8: ' + name + '\n'))
            self.assertEqual(calls, [mock.call('dev_tv', 'channels', {'search': name}),
                                     mock.call('dev_tv', 'play', {'query': '8'})])

    def test_recognized_malformed_commands_fail_before_requests(self):
        invalid = [['status', 'extra'], ['st', 'extra'], ['providers', 'extra'], ['provs', 'extra'],
                   ['provider'], ['prov'], ['profiles', 'extra'], ['profs', 'extra'], ['profile'], ['prof'],
                   ['playlist'], ['playlist', 'one', 'two'], ['message'], ['msg'], ['exit', 'extra'],
                   ['play'], ['volume', '1', '2'], ['vportal'], ['vportal-random', '--list']]
        for words in invalid:
            with self.subTest(words=words):
                code, out, _, calls, _ = self.run_cli(words, [])
                self.assertEqual((code, out, calls), (1, '', []))

    def test_provider_profile_message_vportal_aliases_keep_canonical_wire_actions(self):
        for words, expected in [
            (['PROV', 'M3U'], ('provider', {'query': 'M3U'})),
            (['PROVS'], ('providers', {})),
            (['PROFS'], ('profiles', {})),
            (['PROF', '3'], ('profile', {'number': 3})),
            (['MESSAGE', 'Привет', 'мир'], ('command', {'command': 'popup_message', 'message': 'Привет мир'})),
            (['VPORTAL', 'Три кота'], ('vportal', {'query': 'Три кота'})),
            (['vportal-random', 'Три кота'], ('vportal_random', {'query': 'Три кота'})),
            (['vportal', '-l', 'Три кота'], ('vportal_search', {'query': 'Три кота'})),
            (['vportal-random', '--list', 'Три кота'], ('vportal_search', {'query': 'Три кота'})),
        ]:
            self.assertEqual(ott.parse_command(words), expected)
        for alias, field, value in [('url', 'playlist', 'https://example/list'), ('playlist', 'playlist', 'https://example/list'),
                                     ('history', 'history_hours', 168), ('history-hours', 'history_hours', 168),
                                     ('history_hours', 'history_hours', 168), ('vp', 'vportal', ''), ('vportal', 'vportal', ''),
                                     ('n', 'name', 'Гостиная'), ('name', 'name', 'Гостиная')]:
            self.assertEqual(ott.parse_command(['prof', '2', alias.upper(), str(value)]),
                             ('profile_settings', {'number': 2, 'settings': {field: value}}))
        for alias in ['history', 'history-hours', 'history_hours']:
            with self.assertRaises(ott.Error): ott.parse_command(['profile', '2', alias, '8761'])


if __name__ == '__main__':
    unittest.main()
