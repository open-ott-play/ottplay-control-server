import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_profiles', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ProfileParsingTest(unittest.TestCase):
    def test_profile_commands_and_original_provider_commands(self):
        for words, action, params in [
            (['PROFILES'], 'profiles', {}),
            (['profile', '1'], 'profile', {'number': 1}),
            (['PROFILE', '15'], 'profile', {'number': 15}),
            (['profile', '3', 'url', 'https://example/list?key=private'], 'profile_settings', {'number': 3, 'settings': {'playlist': 'https://example/list?key=private'}}),
            (['profile', '3', 'history', '8760'], 'profile_settings', {'number': 3, 'settings': {'history_hours': 8760}}),
            (['profile', '3', 'history', '0'], 'profile_settings', {'number': 3, 'settings': {'history_hours': 0}}),
            (['profile', '3', 'VPORTAL', 'https://portal/link'], 'profile_settings', {'number': 3, 'settings': {'vportal': 'https://portal/link'}}),
            (['profile', '3', 'name', 'Living', 'room'], 'profile_settings', {'number': 3, 'settings': {'name': 'Living room'}}),
            (['profile', '3', 'name', 'Гостиная 😀'], 'profile_settings', {'number': 3, 'settings': {'name': 'Гостиная 😀'}}),
            (['provider', 'm3u'], 'provider', {'query': 'm3u'}),
            (['playlist', 'https://example/list'], 'provider_settings', {'provider': 'm3u', 'settings': {'playlist': 'https://example/list'}}),
            (['play', 'profiles'], 'play', {'query': 'profiles'}),
        ]:
            with self.subTest(words=words):
                self.assertEqual(ott.parse_command(words), (action, params))
        for field, key in [('name', 'name'), ('url', 'playlist'), ('vportal', 'vportal')]:
            self.assertEqual(ott.parse_command(['profile', '1', field, '']),
                             ('profile_settings', {'number': 1, 'settings': {key: ''}}))

    def test_invalid_numbers_syntax_and_settings_are_rejected(self):
        for number in ['', '0', '16', '-1', '+1', '01', '1.0', '1e0', '１']:
            with self.subTest(number=number), self.assertRaises(ott.Error):
                ott.parse_command(['profile', number])
        for words in [['profiles', '1'], ['profile'], ['profile', '2', 'url'], ['profile', '2', 'url', 'a', 'b'],
                      ['profile', '2', 'other', 'a'], ['profile-config', '2'], ['profile-config', '2', 'one', 'two']]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)
        for hours in ['', '-1', '8761', 'true', '+1', '01', '1.5', '1e3']:
            with self.subTest(hours=hours), self.assertRaises(ott.Error):
                ott.parse_command(['profile', '1', 'history', hours])
        for settings in [None, [], {}, {'extra': 'private'}, {'history_hours': True}, {'history_hours': None},
                         {'history_hours': 1.0}, {'name': None}, {'playlist': []}, {'vportal': {}},
                         {'name': '\ud800'}, {'name': 'я' * 129}, {'playlist': 'я' * 4097}, {'vportal': 'x' * 8193},
                         {'name': 'Name\x1b'}, {'playlist': 'url\n'}, {'vportal': 'link\x7f'}]:
            with self.subTest(settings=settings), self.assertRaises(ott.Error):
                ott.validate_profile_settings(settings)
        for settings in [{'name': 'я' * 128}, {'playlist': 'я' * 4096}, {'vportal': 'x' * 8192}]:
            ott.validate_profile_settings(settings)

    def test_profile_config_is_one_atomic_object_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            settings = {'name': 'Cinema', 'history_hours': 168, 'playlist': 'https://example/list', 'vportal': ''}
            path.write_text(json.dumps(settings))
            self.assertEqual(ott.parse_command(['profile-config', '2', str(path)]),
                             ('profile_settings', {'number': 2, 'settings': settings}))
            for raw in ['{"name":"one","name":"two"}', '{"settings":{"name":"Cinema"}}',
                        '{"provider":"m3u","settings":{"playlist":"private"}}', '[]', '{}', 'null',
                        '{"history_hours":NaN}', '{"playlist":"\\ud800"}', '{', ' ' * (16 * 1024 + 1)]:
                path.write_text(raw)
                with self.subTest(raw=raw[:50]), self.assertRaises(ott.Error):
                    ott.parse_command(['profile-config', '2', str(path)])
            path.write_bytes(b'{"name":"\xff"}')
            with self.assertRaises(ott.Error):
                ott.parse_command(['profile-config', '2', str(path)])

    def test_combined_request_size_is_checked_before_queueing(self):
        settings = {'playlist': 'x' * 8192, 'vportal': 'y' * 8192}
        with mock.patch.object(ott, 'read_profile_settings', return_value=settings), self.assertRaisesRegex(ott.Error, '16 KiB'):
            ott.parse_command(['profile-config', '1', 'private.json'])

    def test_restart_parsing_has_only_two_targets(self):
        for words, target in [(['restart'], 'player'), (['restart', 'stream'], 'stream'), (['RESTART', 'PLAYER'], 'player')]:
            self.assertEqual(ott.parse_command(words), ('restart', {'target': target}))
        for words in [['restart', 'all'], ['restart', 'player', 'again'], ['restart', '']]:
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)


class ProfileOutputTest(unittest.TestCase):
    def row(self, number=1, active=True, **changes):
        result = {'number': number, 'name': 'Profile ' + str(number), 'active': active,
                  'history_hours': 168, 'playlist_configured': True, 'vportal_configured': False}
        result.update(changes)
        return result

    def listing(self):
        return {'provider': 'm3u', 'profiles': [self.row(number, active=number == 3) for number in range(1, 16)]}

    def run_command(self, words, response, json_output=False):
        client = mock.Mock()
        client.device.return_value = 'dev_tv'
        if isinstance(response, Exception):
            client.call.side_effect = response
        else:
            client.call.return_value = response
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = ott.main((['--json'] if json_output else []) + ['tv'] + words)
        return status, stdout.getvalue(), stderr.getvalue(), client.call.call_args_list

    def test_listing_projects_public_metadata_and_preserves_unknown_history(self):
        data = self.listing()
        data['profiles'][0]['history_hours'] = None
        safe = copy.deepcopy(data)
        data['playlist'] = 'secret-top-level'
        for row in data['profiles']:
            row.update(playlist='https://example?key=secret-key', vportal='secret-portal')
        for json_output in [False, True]:
            status, output, errors, calls = self.run_command(['profiles'], data, json_output)
            self.assertEqual((status, errors), (0, ''))
            self.assertEqual(calls, [mock.call('dev_tv', 'profiles', {})])
            self.assertNotIn('secret', output)
            self.assertNotIn('https://', output)
            if json_output:
                self.assertEqual(json.loads(output), safe)
            else:
                self.assertIn('* 3: Profile 3', output)
                self.assertIn('history: unknown h', output)
                self.assertEqual(len(output.splitlines()), 15)

    def test_vportal_profiles_have_no_tv_playlist_and_preserve_provider(self):
        data = self.listing()
        data['provider'] = 'vportal'
        for row in data['profiles']:
            row.update(history_hours=0, playlist_configured=False, vportal_configured=True)
        status, output, errors, calls = self.run_command(['profiles'], data, True)
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output), data)
        self.assertEqual(calls, [mock.call('dev_tv', 'profiles', {})])

    def test_standalone_vportal_profile_mutations_remain_supported(self):
        row = self.row(2, name='Cinema', history_hours=0,
                       playlist_configured=False, vportal_configured=True)
        for words, action, params, receipt in [
            (['profile', '2'], 'profile', {'number': 2}, {'dispatched': True}),
            (['profile', '2', 'name', 'Cinema'], 'profile_settings',
             {'number': 2, 'settings': {'name': 'Cinema'}}, {'saved': True}),
        ]:
            with self.subTest(action=action):
                data = {'provider': 'vportal', 'profile': row, **receipt}
                status, output, errors, calls = self.run_command(words, data, True)
                self.assertEqual((status, errors), (0, ''))
                self.assertEqual(json.loads(output), data)
                self.assertEqual(calls, [mock.call('dev_tv', action, params)])

    def test_invalid_listing_never_claims_success(self):
        cases = [None, {}, {'provider': 'xtream', 'profiles': self.listing()['profiles']}]
        for field, value in [('number', True), ('number', 1.0), ('name', '\ud800'), ('name', 'я' * 129),
                             ('active', 1), ('history_hours', True), ('history_hours', 1.5), ('history_hours', 8761),
                             ('playlist_configured', 'yes'), ('vportal_configured', None)]:
            data = self.listing()
            data['profiles'][0][field] = value
            cases.append(data)
        for change in ['short', 'long', 'reordered', 'duplicate', 'no-active', 'two-active', 'missing-history']:
            data = self.listing()
            if change == 'short': data['profiles'].pop()
            elif change == 'long': data['profiles'].append(self.row(16, False))
            elif change == 'reordered': data['profiles'].reverse()
            elif change == 'duplicate': data['profiles'][1]['number'] = 1
            elif change == 'no-active': data['profiles'][2]['active'] = False
            elif change == 'two-active': data['profiles'][0]['active'] = True
            else: data['profiles'][0].pop('history_hours')
            cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                status, output, errors, calls = self.run_command(['profiles'], data, True)
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertIn('invalid profile metadata', errors)

    def test_selection_checks_requested_number_active_and_dispatch(self):
        good = {'provider': 'm3u', 'profile': self.row(2), 'dispatched': True, 'url': 'secret'}
        status, output, errors, calls = self.run_command(['profile', '2'], good, True)
        self.assertEqual((status, errors), (0, ''))
        self.assertNotIn('secret', output)
        self.assertEqual(json.loads(output), {k: good[k] for k in ('provider', 'profile', 'dispatched')})
        self.assertEqual(calls, [mock.call('dev_tv', 'profile', {'number': 2})])
        for data in [{**good, 'profile': self.row(1)}, {**good, 'profile': self.row(2, False)},
                     {**good, 'dispatched': 1}, {**good, 'provider': 'other'}]:
            status, output, errors, calls = self.run_command(['profile', '2'], data)
            self.assertEqual((status, output, len(calls)), (1, '', 1))
            self.assertIn('may have executed', errors)

    def test_atomic_settings_ack_matches_requested_values_and_redacts_urls(self):
        settings = {'name': 'Cinema', 'history_hours': 8760, 'playlist': '', 'vportal': 'https://example?key=secret'}
        row = self.row(5, False, name='Cinema', history_hours=8760, playlist_configured=False, vportal_configured=True)
        response = {'provider': 'm3u', 'profile': {**row, 'settings': settings}, 'saved': True, 'settings': settings}
        with mock.patch.object(ott, 'read_profile_settings', return_value=settings):
            status, output, errors, calls = self.run_command(['profile-config', '5', 'private.json'], response, True)
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output), {'provider': 'm3u', 'profile': row, 'saved': True})
        self.assertEqual(calls, [mock.call('dev_tv', 'profile_settings', {'number': 5, 'settings': settings})])
        for field, value in [('number', 6), ('name', 'Other'), ('history_hours', None),
                             ('playlist_configured', True), ('vportal_configured', False)]:
            bad = copy.deepcopy(response)
            bad['profile'][field] = value
            with mock.patch.object(ott, 'read_profile_settings', return_value=settings):
                status, output, errors, calls = self.run_command(['profile-config', '5', 'private.json'], bad)
            self.assertEqual((status, output, len(calls)), (1, '', 1))
            self.assertIn('may have executed', errors)
        for saved in [None, False, 1, 'true']:
            bad = {**response, 'saved': saved}
            with mock.patch.object(ott, 'read_profile_settings', return_value=settings):
                status, output, _, calls = self.run_command(['profile-config', '5', 'private.json'], bad)
            self.assertEqual((status, output, len(calls)), (1, '', 1))

    def test_profile_name_controls_are_not_written_to_terminal(self):
        response = self.listing()
        response['profiles'][0]['name'] = 'Cinema\x1b[2J'
        status, output, _, _ = self.run_command(['profiles'], response)
        self.assertEqual(status, 0)
        self.assertNotIn('\x1b', output)
        status, output, _, calls = self.run_command(['profile', '1', 'name', 'Cinema\x1b[2J'], {})
        self.assertEqual((status, output, len(calls)), (1, '', 0))

    def test_empty_values_acknowledge_cleared_fields(self):
        for field, row in [('name', self.row(name='')), ('url', self.row(playlist_configured=False)),
                           ('vportal', self.row(vportal_configured=False))]:
            with self.subTest(field=field):
                status, _, errors, calls = self.run_command(['profile', '1', field, ''],
                                                           {'provider': 'm3u', 'profile': row, 'saved': True})
                self.assertEqual((status, errors, len(calls)), (0, '', 1))

    def test_invalid_config_never_enqueues_and_errors_do_not_echo_settings(self):
        with mock.patch.object(ott, 'read_profile_settings', return_value={'secret-key': 'secret-value'}):
            status, output, errors, calls = self.run_command(['profile-config', '2', 'private.json'], {})
        self.assertEqual((status, output, len(calls)), (1, '', 0))
        self.assertNotIn('secret-', errors)
        for error in [ott.PlayerRejected('secret-key in URL', {}), ott.PlayerUnsupported('secret-key'),
                      ott.TransportError('Response lost; do not repeat blindly')]:
            status, output, errors, calls = self.run_command(['profile', '2', 'url', 'https://example?key=secret-key'], error)
            self.assertEqual((status, output, len(calls)), (1, '', 1))
            self.assertNotIn('secret-key', errors)

    def test_restart_acknowledgements_are_truthful_and_projected(self):
        for target, response, message in [
            ('stream', {'accepted': True, 'target': 'stream', 'dispatched': True}, 'Stream restart requested.'),
            ('player', {'accepted': True, 'target': 'player', 'dispatched': False, 'effect': 'reload-after-ack'},
             'Player reload accepted; waiting for the acknowledgement to reach the player.'),
        ]:
            for json_output in [False, True]:
                status, output, errors, calls = self.run_command(['restart', target], {**response, 'secret': 'private'}, json_output)
                self.assertEqual((status, errors), (0, ''))
                self.assertEqual(calls, [mock.call('dev_tv', 'restart', {'target': target})])
                self.assertEqual(json.loads(output) if json_output else output.strip(), response if json_output else message)

    def test_restart_malformed_rejected_or_uncertain_never_retries(self):
        for target, data in [('stream', {}), ('stream', {'accepted': True, 'target': 'player', 'dispatched': True}),
                             ('stream', {'accepted': 1, 'target': 'stream', 'dispatched': True}),
                             ('stream', {'accepted': True, 'target': 'stream', 'dispatched': False}),
                             ('player', {'accepted': True, 'target': 'player', 'dispatched': True, 'effect': 'reload-after-ack'}),
                             ('player', {'accepted': True, 'target': 'player', 'dispatched': False}),
                             ('player', ott.PlayerRejected('Unavailable', {})),
                             ('stream', ott.PlayerUnsupported('Unavailable')),
                             ('player', ott.TransportError('Lost acknowledgement; do not repeat blindly'))]:
            with self.subTest(target=target, data=data):
                status, output, _, calls = self.run_command(['restart', target], data, True)
                self.assertEqual((status, output, len(calls)), (1, '', 1))

    def test_restart_unready_stream_is_distinct_from_unsupported_player(self):
        for response in [
            ott.PlayerRejected('private', {'reason': 'no_restartable_stream', 'error': 'private'}),
            ott.PlayerUnsupported('private', {'error': 'There is no owned, restartable stream.'}),
        ]:
            for json_output in [False, True]:
                status, output, errors, calls = self.run_command(['restart', 'stream'], response, json_output)
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertIn('No stream is ready to restart', errors)
                self.assertIn("ott <player> restart", errors)
                self.assertNotIn('private', errors)
                self.assertNotIn('unsupported', errors)
        for words in [['restart', 'stream'], ['restart', 'player']]:
            response = ott.PlayerUnsupported('private', {'reason': 'unknown', 'error': 'private'})
            status, output, errors, calls = self.run_command(words, response, False)
            self.assertEqual((status, output, len(calls)), (1, '', 1))
            self.assertIn('unsupported by this player', errors)
            self.assertNotIn('private', errors)


if __name__ == '__main__':
    unittest.main()
