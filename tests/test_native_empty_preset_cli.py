"""Loading a preset into a truly empty native M3U store must bootstrap safely."""
import json
import unittest

import test_presets_cli as presets

ott = presets.ott


def home_config(numbers=(1, 2), active=1):
    source = presets.config()
    home = source['presets'].pop('local')
    prototype = home['m3u'][0]
    home['m3u'] = [dict(prototype, number=n, name='Home ' + str(n)) for n in numbers]
    home['active_profile'] = active
    source['presets']['Home'] = home
    return source


class EmptyNativePlayer(presets.FakePlayer):
    """Native empty slots stay inactive until an explicit profile selection."""
    def __init__(self, faults=None, after_save=None):
        super().__init__(active=None, faults=faults)
        for row in self.slots.values():
            row.update(name='', history_hours=0, playlist_configured=False)
        self.after_save = after_save

    def call(self, device, action, params):
        saved_before = len(self.saved)
        response = super().call(device, action, params)
        if action == 'profile_settings' and len(self.saved) > saved_before:
            if self.after_save is not None:
                response = self.after_save(self, params, response)
        return response


def load(player, source=None):
    status, output, errors, _, _ = presets.run(
        preset_config=home_config() if source is None else source,
        player=player, words=['tv', 'load', 'Home'])
    return status, json.loads(output), errors


def profile_calls(player):
    return [(action, params['number']) for _, action, params in player.calls
            if action in ('profile_settings', 'profile')]


class EmptyNativePresetTests(unittest.TestCase):
    def test_empty_native_saves_first_assigned_slot_and_selects_desired(self):
        # Input order differs from validated slot order; slot 1 is not assigned.
        player = EmptyNativePlayer()
        status, result, errors = load(player, home_config((11, 4), active=11))
        self.assertEqual((status, result['status'], errors), (0, 'loaded', ''))
        self.assertEqual(profile_calls(player), [
            ('profile_settings', 4), ('profile_settings', 11), ('profile', 11)])
        self.assertEqual((result['active_profile'], player.active), (11, 11))
        self.assertEqual(player.slots[1]['playlist_configured'], False)
        self.assertEqual(player.counts['profiles'], 2)
        self.assertIn('verify_profiles', result['completed'])

    def test_empty_native_first_slot_is_saved_once_when_it_is_desired(self):
        player = EmptyNativePlayer()
        status, result, errors = load(player)
        self.assertEqual((status, result['status'], errors), (0, 'loaded', ''))
        self.assertEqual(profile_calls(player), [
            ('profile_settings', 1), ('profile_settings', 2), ('profile', 1)])
        self.assertEqual(result['completed'].count('save_profile_1'), 1)

    def test_empty_native_single_nonfirst_slot_is_configured_before_selection(self):
        player = EmptyNativePlayer()
        status, result, errors = load(player, home_config((7,), active=7))
        self.assertEqual((status, result['status'], errors), (0, 'loaded', ''))
        self.assertEqual(profile_calls(player), [('profile_settings', 7), ('profile', 7)])
        self.assertEqual(player.active, 7)

    def test_unselected_nonempty_store_stops_before_any_m3u_write(self):
        for change in ({'playlist_configured': True}, {'name': 'Saved label'},
                       {'history_hours': 24}, {'history_hours': None}, {'vportal_configured': True}):
            with self.subTest(change=change):
                player = EmptyNativePlayer()
                player.slots[7].update(change)
                status, result, errors = load(player)
                self.assertEqual((status, result['status'], result['stage'], errors),
                                 (1, 'failed', 'wait_m3u', ''))
                self.assertEqual(profile_calls(player), [])
                self.assertEqual(player.active, None)

    def test_multiple_active_or_malformed_initial_list_is_rejected(self):
        def bad_list(mutate):
            def response(player, params):
                value = {'provider': 'm3u', 'profiles': [player.metadata(n) for n in range(1, 16)]}
                mutate(value)
                return value
            return response
        mutations = [
            lambda data: (data['profiles'][0].update(active=True), data['profiles'][1].update(active=True)),
            lambda data: data['profiles'].pop(),
            lambda data: data['profiles'][1].update(number=1),
            lambda data: data['profiles'][0].update(active=0),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                player = EmptyNativePlayer(faults={('profiles', 1): bad_list(mutate)})
                status, result, _ = load(player)
                self.assertEqual((status, result['stage']), (1, 'wait_m3u'))
                self.assertEqual(profile_calls(player), [])

    def test_first_save_wrong_activation_is_not_replayed_or_followed_by_selection(self):
        def wrong_ack(player, params, response):
            response['profile']['active'] = True
            return response
        player = EmptyNativePlayer(after_save=wrong_ack)
        status, result, errors = load(player)
        self.assertEqual((status, result['stage'], errors), (1, 'save_profile_1', ''))
        self.assertEqual(profile_calls(player), [('profile_settings', 1)])
        self.assertNotIn('save_profile_1', result['completed'])

    def test_first_save_lost_ack_is_not_replayed(self):
        def lost_ack(player, params, response):
            raise ott.Error('Acknowledgement lost')
        player = EmptyNativePlayer(after_save=lost_ack)
        status, result, _ = load(player)
        self.assertEqual((status, result['stage']), (1, 'save_profile_1'))
        self.assertEqual(profile_calls(player), [('profile_settings', 1)])
        self.assertIsNone(player.active)
        self.assertEqual(player.slots[1]['playlist_configured'], True)
        self.assertNotIn('save_profile_1', result['completed'])

    def test_final_readback_requires_exactly_one_desired_active_profile(self):
        def final_response(kind):
            def response(player, params):
                rows = [player.metadata(n) for n in range(1, 16)]
                if kind == 'none':
                    for row in rows:
                        row['active'] = False
                elif kind == 'multiple':
                    rows[1]['active'] = True
                else:
                    rows[0]['active'] = False
                    rows[1]['active'] = True
                return {'provider': 'm3u', 'profiles': rows}
            return response
        for kind in ('none', 'multiple', 'wrong'):
            with self.subTest(kind=kind):
                player = EmptyNativePlayer(faults={('profiles', 2): final_response(kind)})
                status, result, _ = load(player)
                self.assertEqual((status, result['stage']), (1, 'verify_profiles'))
                self.assertEqual(player.calls[-1][1], 'profiles')
                self.assertNotIn('verify_profiles', result['completed'])

    def test_configured_clients_retain_existing_request_order(self):
        cases = [(2, [('profile_settings', 1), ('profile', 1), ('profile_settings', 2)]),
                 (1, [('profile_settings', 2), ('profile_settings', 1), ('profile', 1)]),
                 (3, [('profile_settings', 1), ('profile_settings', 2), ('profile', 1)])]
        for active, expected in cases:
            with self.subTest(active=active):
                player = presets.FakePlayer(active=active)
                player.slots[active]['playlist_configured'] = True
                status, result, errors = load(player)
                self.assertEqual((status, result['status'], errors), (0, 'loaded', ''))
                self.assertEqual(profile_calls(player), expected)


if __name__ == '__main__':
    unittest.main()
