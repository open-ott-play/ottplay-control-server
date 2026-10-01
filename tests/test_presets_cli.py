import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_presets', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

TOKEN = 'test-only-private-plex-token'
PLAYLIST = 'https://playlist.example/list?key=test-only-private-playlist-key'
PORTAL = 'portal::[key:test-only-private-portal-key]https://portal.example/api/v1/'
SERVER = 'http://nas.example:32400'


def config():
    return {'presets': {'local': {
        'm3u': [{'number': number, 'name': 'Profile ' + str(number), 'playlist': PLAYLIST,
                 'history_hours': 144, 'vportal': PORTAL} for number in (1, 2)],
        'plex': {'server': SERVER, 'token': TOKEN}, 'active_profile': 1,
    }}}


class FakePlayer:
    def __init__(self, active=2, faults=None):
        self.timeout = 45
        self.active = active
        self.provider = 'm3u'
        self.faults = faults or {}
        self.calls = []
        self.counts = {}
        self.saved = []
        self.slots = {n: {'number': n, 'name': 'Old ' + str(n), 'history_hours': None,
                         'playlist_configured': False, 'vportal_configured': False} for n in range(1, 16)}

    def device(self, name):
        return 'dev_tv'

    def metadata(self, number):
        return dict(self.slots[number], active=number == self.active, private=TOKEN)

    def call(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params)))
        self.counts[action] = self.counts.get(action, 0) + 1
        key = (action, self.counts[action])
        if key in self.faults:
            fault = self.faults[key]
            if isinstance(fault, BaseException):
                raise fault
            return fault(self, params) if callable(fault) else copy.deepcopy(fault)
        if action == 'provider':
            self.provider = params['query']
            return {'provider': self.provider, 'dispatched': True, 'private': TOKEN}
        if action == 'status':
            return {'provider': self.provider, 'ready': False, 'channels': 0, 'private': TOKEN}
        if action == 'provider_settings':
            self.saved.append(('plex', copy.deepcopy(params)))
            return {'provider': 'plex', 'saved': True, 'fields': ['token', 'server'], 'private': TOKEN}
        if action == 'profiles':
            return {'provider': 'm3u', 'profiles': [self.metadata(n) for n in range(1, 16)], 'private': TOKEN}
        if action == 'profile_settings':
            n = params['number']
            settings = params['settings']
            self.saved.append((n, copy.deepcopy(settings)))
            self.slots[n].update(name=settings['name'], history_hours=settings['history_hours'],
                                 playlist_configured=bool(settings['playlist']), vportal_configured=bool(settings['vportal']))
            return {'provider': 'm3u', 'saved': True, 'profile': self.metadata(n), 'private': TOKEN}
        if action == 'profile':
            self.active = params['number']
            return {'provider': 'm3u', 'dispatched': True, 'profile': self.metadata(self.active), 'private': TOKEN}
        raise AssertionError('Unexpected action: ' + action)


def run(preset_config=None, player=None, words=None, json_output=True, timeout=45):
    player = player or FakePlayer()
    output, errors = io.StringIO(), io.StringIO()
    with mock.patch.object(ott, 'read_json', return_value=config() if preset_config is None else preset_config), \
            mock.patch.object(ott, 'Client', return_value=player) as constructor, \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        status = ott.main(['--timeout', str(timeout)] + (['--json'] if json_output else []) +
                          (words if words is not None else ['tv', 'load', 'local']))
    return status, output.getvalue(), errors.getvalue(), player, constructor


class PresetValidationTests(unittest.TestCase):
    def assert_no_secrets(self, text):
        for secret in (TOKEN, PLAYLIST, PORTAL, SERVER, 'private-playlist-key', 'private-portal-key'):
            self.assertNotIn(secret, text)

    def test_names_are_local_case_insensitive_and_do_not_construct_client(self):
        source = config()
        source['presets']['Home'] = {'not': 'validated until selected'}
        status, out, err, player, constructor = run(source, words=['PrEsEtS'])
        self.assertEqual((status, json.loads(out), err), (0, {'presets': ['Home', 'local']}, ''))
        constructor.assert_not_called()
        self.assertEqual(player.calls, [])
        status, out, err, _, _ = run(source, words=['presets'], json_output=False)
        self.assertEqual((status, out, err), (0, 'Home\nlocal\n', ''))
        self.assertEqual(ott.validate_preset(source, 'LoCaL')[0], 'local')

    def test_load_is_reserved_and_play_escape_is_unchanged(self):
        self.assertEqual(ott.parse_command(['LoAd', 'LOCAL']), ('load', {'preset': 'LOCAL'}))
        self.assertEqual(ott.parse_command(['play', 'load']), ('play', {'query': 'load'}))
        for words in (['load'], ['load', 'local', 'extra'], ['load', '../local'], ['load', TOKEN + '\n']):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(words)

    def test_invalid_bundle_never_constructs_client_or_prints_values(self):
        mutations = [
            lambda p: p.update(extra=TOKEN), lambda p: p.update(plex=None),
            lambda p: p['plex'].pop('token'), lambda p: p['plex'].update(extra=TOKEN),
            lambda p: p['plex'].update(token=''), lambda p: p['plex'].update(server='http://user:' + TOKEN + '@nas.example'),
            lambda p: p.update(m3u=[]), lambda p: p.update(m3u={}),
            lambda p: p['m3u'][1].update(number=1), lambda p: p['m3u'][1].update(number=True),
            lambda p: p['m3u'][1].update(number=0), lambda p: p['m3u'][1].update(number=16),
            lambda p: p['m3u'][1].pop('vportal'), lambda p: p['m3u'][1].update(extra=TOKEN),
            lambda p: p['m3u'][1].update(history_hours=True), lambda p: p['m3u'][1].update(history_hours=8761),
            lambda p: p['m3u'][1].update(name='bad\x00name'), lambda p: p['m3u'][1].update(name='\ud800'),
            lambda p: p['m3u'][1].update(name='x' * 257), lambda p: p.update(active_profile=True),
            lambda p: p.update(active_profile=3), lambda p: p.update(active_profile=1.0),
        ]
        for url in ['ftp://playlist.example/list', 'https://u:p@playlist.example/list',
                    'https://playlist.example/a b', 'https://playlist.example/a\\b',
                    'https://playlist.example:65536/list', 'https://[bad]/list', ' https://playlist.example/list',
                    'https://bad%zz/list', 'https://bad%20host/list', 'https://bad%2fhost/list',
                    'https://bad%ffhost/list', 'https://bad^host/list', 'http://[v1.name]/list',
                    'http://[fe80::1%25eth0]/list']:
            mutations.append(lambda p, url=url: p['m3u'][1].update(playlist=url))
        for link in ['https://portal.example/api/', 'portal::[key:]https://portal.example/',
                     'portal::[key:x]https://user:password@portal.example/',
                     'portal::[key:x]https://portal.example/#fragment',
                     'portal::[key:x]https://portal.example/a b',
                     'portal::[key:' + '😀' * 513 + ']https://portal.example/']:
            mutations.append(lambda p, link=link: p['m3u'][1].update(vportal=link))
        for mutate in mutations:
            source = config(); mutate(source['presets']['local'])
            with self.subTest(mutation=mutate):
                status, out, err, player, constructor = run(source)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(out)['stage'], 'validate')
                self.assertEqual(json.loads(out)['completed'], [])
                constructor.assert_not_called()
                self.assertEqual(player.calls, [])
                self.assert_no_secrets(out + err)

    def test_request_limit_is_checked_for_later_slot_before_first_write(self):
        source = config()
        source['presets']['local']['m3u'][1].update(
            playlist='https://a/' + 'x' * (8192 - len('https://a/')),
            vportal='portal::[key:x]https://a/' + 'x' * (8192 - len('portal::[key:x]https://a/')))
        status, out, err, player, constructor = run(source)
        self.assertEqual(status, 1)
        self.assertIn('16 KiB', json.loads(out)['error'])
        self.assertEqual(player.calls, [])
        constructor.assert_not_called()

    def test_empty_vportal_and_encoded_brackets_are_supported(self):
        source = config()
        source['presets']['local']['m3u'][0]['vportal'] = ''
        source['presets']['local']['m3u'][1]['vportal'] = 'PORTAL::%5Bkey:opaque%2Fkey%5DHTTPS://portal.example/api/?a=b'
        self.assertEqual(run(source)[0], 0)

    def test_numeric_hosts_are_validated_before_any_provider_change(self):
        for host in ('999.999.999.999', 'host.123', 'host.0xff', '09', '127.1', '0x7f000001'):
            for target in ('playlist', 'vportal', 'plex'):
                source = config()
                url = 'http://' + host + '/media'
                if target == 'plex':
                    source['presets']['local']['plex']['server'] = url
                else:
                    source['presets']['local']['m3u'][1][target] = ('portal::[key:test-only-key]' if target == 'vportal' else '') + url
                with self.subTest(host=host, target=target):
                    status, out, err, player, constructor = run(source)
                    self.assertEqual((status, json.loads(out)['stage'], player.calls), (1, 'validate', []))
                    constructor.assert_not_called()
        for url in ('http://127.0.0.1:8090', 'https://192.168.1.25/', 'https://[::1]:32400', 'https://host123.example/list'):
            with self.subTest(url=url):
                self.assertTrue(ott.preset_url(url))

    def test_bad_root_duplicate_names_and_duplicate_json_fields_fail_cleanly(self):
        for source in [[], 1, {'presets': []}, {'presets': {'bad name': {}}},
                       {'presets': {'Local': {}, 'LOCAL': {}}}]:
            with self.subTest(source=source):
                status, out, err, player, constructor = run(source)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(out)['stage'], 'validate')
                constructor.assert_not_called()
                self.assertEqual(player.calls, [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"presets":{"local":{},"local":{"token":"' + TOKEN + '"}}}')
            with self.assertRaises(ott.Error) as raised:
                ott.read_json(path)
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_management_alias_named_load_is_not_intercepted(self):
        for verb in ('alias', 'add', 'approve'):
            with mock.patch.object(ott, 'management', return_value=True) as manage:
                status, _, _, _, _ = run(words=[verb, 'load', 'device-or-code'])
            self.assertEqual(status, 0)
            manage.assert_called_once()


class PresetExecutionTests(unittest.TestCase):
    assert_no_secrets = PresetValidationTests.assert_no_secrets

    def test_optimized_order_and_credential_editor_does_not_wait_for_ready(self):
        for active, sequence in [(2, [('profile_settings', 1), ('profile', 1), ('profile_settings', 2)]),
                                 (1, [('profile_settings', 2), ('profile_settings', 1), ('profile', 1)]),
                                 (3, [('profile_settings', 1), ('profile_settings', 2), ('profile', 1)])]:
            with self.subTest(active=active):
                status, out, err, player, _ = run(player=FakePlayer(active))
                self.assertEqual((status, err), (0, ''))
                receipt = json.loads(out)
                self.assertEqual((receipt['status'], receipt['active_profile'], receipt['provider']), ('loaded', 1, 'm3u'))
                self.assertEqual([(a, p['number']) for _, a, p in player.calls if a in ('profile', 'profile_settings')], sequence)
                self.assertEqual(player.counts['status'], 1)
                self.assertEqual(player.counts['provider_settings'], 1)
                self.assertEqual(player.counts['profiles'], 2)
                self.assertEqual(len(player.saved), 3)
                self.assert_no_secrets(out + err)

    def test_only_explicit_before_write_mount_errors_are_retried(self):
        for message in ('Select this provider before changing its settings.', 'Plex settings are unavailable on this player.'):
            player = FakePlayer(faults={
                ('provider_settings', 1): ott.PlayerRejected(TOKEN, {'error': message, 'private': TOKEN}),
                ('profiles', 1): ott.PlayerRejected(TOKEN, {'error': 'Select the M3U provider before managing profiles.'}),
            })
            with mock.patch.object(ott.time, 'sleep'):
                status, out, err, _, _ = run(player=player)
            self.assertEqual(status, 0)
            self.assertEqual(player.counts['provider_settings'], 2)
            self.assertEqual(player.counts['profiles'], 3)
            self.assertEqual(len([entry for entry in player.saved if entry[0] == 'plex']), 1)
            self.assert_no_secrets(out + err)

    def test_transport_unsupported_and_other_rejection_are_never_replayed(self):
        errors = [ott.Error(TOKEN), ott.TransportError(TOKEN), ott.PlayerUnsupported(TOKEN),
                  ott.PlayerRejected(TOKEN, {'error': TOKEN}),
                  ott.PlayerRejected(TOKEN, {'error': 'Plex settings are unavailable on this player. '})]
        for error in errors:
            player = FakePlayer(faults={('provider_settings', 1): error})
            status, out, err, _, _ = run(player=player)
            receipt = json.loads(out)
            self.assertEqual((status, receipt['stage'], receipt['completed']), (1, 'save_plex', ['select_plex', 'wait_plex']))
            self.assertEqual(len(player.calls), 3)
            self.assert_no_secrets(out + err)

    def test_wrong_ack_and_null_ack_stop_without_repeating_mutation(self):
        failures = [
            (('provider', 1), None, 'select_plex', 1),
            (('provider', 1), {'provider': 'plex', 'dispatched': 1}, 'select_plex', 1),
            (('provider', 1), {'provider': 'm3u', 'dispatched': True}, 'select_plex', 1),
            (('provider_settings', 1), None, 'save_plex', 3),
            (('provider_settings', 1), {'provider': 'plex', 'saved': True, 'fields': ['token']}, 'save_plex', 3),
            (('profile_settings', 1), lambda player, params: {'provider': 'm3u', 'saved': True,
                 'profile': dict(player.metadata(1), name='Profile 1', history_hours=144,
                                 playlist_configured=True, vportal_configured=True, active=True)}, 'save_profile_1', 6),
        ]
        for key, response, stage, count in failures:
            with self.subTest(stage=stage, key=key):
                player = FakePlayer(faults={key: response})
                status, out, err, _, _ = run(player=player)
                self.assertEqual((status, json.loads(out)['stage'], len(player.calls)), (1, stage, count))
                self.assert_no_secrets(out + err)

    def test_partial_stop_and_keyboard_interrupt_retain_confirmed_steps(self):
        for failure, expected in [(ott.PlayerRejected(TOKEN, {'error': TOKEN}), 1), (KeyboardInterrupt(), 130)]:
            player = FakePlayer(faults={('profile_settings', 2): failure})
            status, out, err, _, _ = run(player=player)
            receipt = json.loads(out)
            self.assertEqual((status, receipt['stage']), (expected, 'save_profile_2'))
            self.assertIn('save_profile_1', receipt['completed'])
            self.assertIn('select_profile_1', receipt['completed'])
            self.assertNotIn('save_profile_2', receipt['completed'])
            self.assertEqual(player.counts['profiles'], 1)
            self.assert_no_secrets(out + err)

    def test_deadline_limits_mount_polling_and_late_ack_stops_next_step(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        player = FakePlayer(faults={('status', n): {'provider': 'm3u'} for n in range(1, 20)})
        player.timeout = 1
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]), mock.patch.object(ott.time, 'sleep', side_effect=sleep):
            status, out, err, _, _ = run(player=player, timeout=1)
        self.assertEqual((status, json.loads(out)['stage']), (1, 'wait_plex'))
        self.assertNotIn('provider_settings', player.counts)
        self.assertLessEqual(player.counts['status'], 4)
        self.assertEqual(player.timeout, 1)
        now[0] = 0
        def late_ack(player, params):
            now[0] = 2
            return {'provider': 'plex', 'dispatched': True}
        player = FakePlayer(faults={('provider', 1): late_ack}); player.timeout = 1
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            status, out, _, _, _ = run(player=player, timeout=1)
        self.assertEqual((status, json.loads(out)['completed'], len(player.calls)), (1, [], 1))

    def test_final_readback_detects_drift_without_exposing_raw_profile_data(self):
        def drift(player, params):
            rows = [player.metadata(n) for n in range(1, 16)]
            rows[1]['history_hours'] = 12
            return {'provider': 'm3u', 'profiles': rows, 'private': TOKEN}
        status, out, err, player, _ = run(player=FakePlayer(faults={('profiles', 2): drift}))
        self.assertEqual((status, json.loads(out)['stage']), (1, 'verify_profiles'))
        self.assertEqual(player.counts['profiles'], 2)
        self.assert_no_secrets(out + err)


if __name__ == '__main__':
    unittest.main()
