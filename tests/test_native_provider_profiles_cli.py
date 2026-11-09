"""Numbered native slots preserve the registered provider's actual identity."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_native_profiles', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

# Native producer registry: ottplay-lg rust-modules/data/src/ott/operator_ids.rs.
BRANDS = ('antifriz kb-team itv ottclub shura 1ott bestlist/stalker only4 shara-tv tvteam '
          'all4you bestlist cbilling d/maxtv diamondtv dosug dragon drvao fabryka fox fxml '
          'great ipstream iptv-ott.ru korona moidom newlook ottg ottprime polmedia prost '
          'raduga rd russkoetv shara.club sharavoz shocktv tabox top topiptv tvclub ultifl1x vidok').split()


class NativeProviderProfilesTest(unittest.TestCase):
    def listing(self, provider, active=3):
        return {'provider': provider, 'profiles': [
            {'number': n, 'name': 'Profile ' + str(n), 'active': n == active,
             'history_hours': 0, 'playlist_configured': False}
            for n in range(1, 16)]}

    def run_cli(self, words, reply):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'native'
        client.call.return_value = reply
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-j', 'lg-native', *words])
        return code, out.getvalue(), err.getvalue(), client.call.call_args_list

    def test_every_native_brand_lists_and_selects_without_collapsing_identity(self):
        for provider in ['edem', *BRANDS]:
            with self.subTest(provider=provider):
                listing = self.listing(provider)
                code, out, err, calls = self.run_cli(['profiles'], listing)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(json.loads(out), listing)
                self.assertEqual(calls, [mock.call('native', 'profiles', {})])
                reply = {'provider': provider, 'profile': listing['profiles'][2],
                         'dispatched': True, 'reloaded': True}
                code, out, err, calls = self.run_cli(['profile', '3'], reply)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(json.loads(out), {k: v for k, v in reply.items() if k != 'reloaded'})
                self.assertEqual(calls, [mock.call('native', 'profile', {'number': 3})])

    def test_unconfigured_slots_and_private_fields_are_not_inferred_or_exposed(self):
        data = self.listing('edem', active=0)
        for row in data['profiles']:
            row['name'] = ''
        safe = copy.deepcopy(data)
        data['source'] = 'https://private.example/?key=private-key'
        for row in data['profiles']:
            row.update(source='private-key', key='private-key', list=5, portal='private-key')
        code, out, err, _ = self.run_cli(['profiles'], data)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), safe)
        self.assertNotIn('private', out)

    def test_generic_unknown_alias_and_invalid_slot_metadata_are_rejected(self):
        for provider in ['operator', 'unknown', 'ilook', 'ilooktv', '../edem', 'd//maxtv', ['edem']]:
            with self.subTest(provider=provider):
                with self.assertRaises(ott.Error):
                    ott.profile_metadata(self.listing(provider), 'profiles', {})
        for field, value in [('playlist_configured', True), ('number', True), ('active', 1),
                             ('history_hours', -1)]:
            with self.subTest(field=field):
                data = self.listing('d/maxtv')
                data['profiles'][0][field] = value
                with self.assertRaises(ott.Error):
                    ott.profile_metadata(data, 'profiles', {})
        data = self.listing('edem')
        data['profiles'][0]['active'] = True
        with self.assertRaises(ott.Error):
            ott.profile_metadata(data, 'profiles', {})

    def test_source_brand_does_not_extend_m3u_writes_or_expected_provider(self):
        for provider in ['edem', *BRANDS]:
            with self.subTest(provider=provider):
                listing = self.listing(provider)
                with self.assertRaises(ott.Error):
                    ott.profile_metadata(listing, 'profiles', {}, expected_provider='m3u')
                reply = {'provider': provider, 'profile': listing['profiles'][2], 'saved': True}
                with self.assertRaises(ott.Error):
                    ott.profile_metadata(reply, 'profile_settings', {'number': 3, 'settings': {'name': 'x'}})

    def test_native_help_lists_profiles_and_respects_kiosk(self):
        for provider in ['edem', 'd/maxtv', 'bestlist/stalker', 'shara.club']:
            for locked in [False, True]:
                with self.subTest(provider=provider, locked=locked):
                    controls = {'player': {'platform': 'webos-native'}, 'lifecycle': [], 'input': [], 'playback': []}
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        ott.print_player_help({'provider': provider, 'capabilities': controls,
                                               'kiosk': {'enabled': locked}}, 'lg-native')
                    self.assertIn(' profiles ', out.getvalue())
                    self.assertEqual(' profile N ' in out.getvalue(), not locked)


if __name__ == '__main__':
    unittest.main()
