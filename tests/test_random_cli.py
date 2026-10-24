"""Random selection stays inside the player's source-bound policy boundary."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_random', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class RandomSelectionTest(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'ren', 'number': 23, 'name': 'РЕН ТВ'}
        self.controls = {'version': 1, 'player': {'version': 'test', 'platform': 'tauri', 'runtime': 'page-123'},
                         'lifecycle': [], 'input': [], 'playback': [], 'random': {'version': 1},
                         'categories': {'version': 1, 'operations': ['list', 'select'], 'maxPageSize': 200}}
        self.categories = {'version': 1, 'runtime': 'page-123', 'op': 'list', 'catalog': 'source-123',
                           'provider': 'm3u', 'selected': 'favorites', 'offset': 0, 'total': 2,
                           'has_more': False, 'loading': False,
                           'categories': [{'id': 'all', 'name': 'All', 'count': 200000},
                                          {'id': 'favorites', 'name': 'Favorites', 'count': 1}]}
        self.replies = {}
        self.calls = []
        self.client = mock.Mock(timeout=45)
        self.client.device.return_value = 'tv'
        self.client.call.side_effect = self.call

    def call(self, device, action, params):
        self.calls.append((action, copy.deepcopy(params)))
        if action in self.replies:
            reply = self.replies[action]
            if isinstance(reply, Exception):
                raise reply
            return copy.deepcopy(reply)
        if action == 'capabilities':
            return copy.deepcopy(self.controls)
        if action == 'categories':
            return copy.deepcopy(self.categories)
        if action == 'play_catalog':
            return {'dispatched': True, 'channel': self.row}
        if action == 'epg_catalog':
            return {'catalog': 'epg-123', 'channels': [self.row]}
        if action == 'providers':
            return {'providers': [{'id': 'm3u', 'index': 0, 'name': 'M3U'}]}
        if action == 'provider':
            return {'dispatched': True, 'provider': 'm3u'}
        if action == 'status':
            return {'provider': 'm3u', 'ready': True, 'player': {'standby': False}}
        raise AssertionError((action, params))

    def run_cli(self, words):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=self.client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['-j', 't1', *words])
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_aliases_share_all_catalog_and_one_player_selection(self):
        for word in ('R', 'r', 'random', 'RANDOM'):
            with self.subTest(word=word):
                self.calls.clear()
                code, data, err = self.run_cli([word])
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(data['channel'], self.row)
                self.assertEqual([action for action, _ in self.calls], ['capabilities', 'categories', 'play_catalog'])
                self.assertEqual(self.calls[-1][1], {'catalog': 'source-123', 'random': {}})

    def test_inclusive_range_and_internal_mode_are_forwarded_without_sampling(self):
        for words, selector in [(['random', '1', '1000'], {'range': [1, 1000]}),
                                (['RR'], {'mode': 'adult'}), (['rr', '1', '23'], {'mode': 'adult', 'range': [1, 23]})]:
            with self.subTest(words=words), mock.patch.object(ott.secrets, 'choice') as choice:
                code, _, err = self.run_cli(words)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(self.calls[-1][1]['random'], selector)
                choice.assert_not_called()
        self.assertNotIn('RR', ott.HELP)
        help_output = io.StringIO()
        with contextlib.redirect_stdout(help_output):
            ott.print_player_help({'capabilities': self.controls}, 't1')
        self.assertIn(' R / random ', help_output.getvalue())
        self.assertNotIn(' RR', help_output.getvalue())

    def test_invalid_ranges_never_contact_player(self):
        for bounds in (['0', '1'], ['2', '1'], ['1.5', '2'], ['1', '9007199254740992'], ['1']):
            with self.subTest(bounds=bounds):
                self.calls.clear()
                code, _, _ = self.run_cli(['R', *bounds])
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])

    def test_missing_capability_fails_before_any_mutation_in_both_paths(self):
        del self.controls['random']
        for words in (['R'], ['provider', 'm3u', 'R']):
            with self.subTest(words=words):
                self.calls.clear()
                code, _, err = self.run_cli(words)
                self.assertEqual(code, 1)
                self.assertIn('not supported', err)
                self.assertFalse(any(action in ('provider', 'play_catalog', 'command') for action, _ in self.calls))

    def test_provider_random_uses_all_receipt_without_downloading_channels(self):
        code, data, err = self.run_cli(['provider', 'm3u', 'R'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(data['playback']['channel'], self.row)
        self.assertEqual([action for action, _ in self.calls].count('play_catalog'), 1)
        self.assertNotIn('channels', [action for action, _ in self.calls])

    def test_changed_or_loading_catalog_never_dispatches(self):
        for key, value in (('runtime', 'another-page'), ('loading', True)):
            with self.subTest(key=key):
                before = self.categories[key]
                self.categories[key] = value
                self.calls.clear()
                code, _, _ = self.run_cli(['R'])
                self.assertEqual(code, 1)
                self.assertNotIn('play_catalog', [action for action, _ in self.calls])
                self.categories[key] = before

    def test_legacy_epg_receipt_and_no_unsafe_fallback_after_rejection(self):
        del self.controls['categories']
        self.replies['play_catalog'] = ott.PlayerRejected('private provider URL', {})
        code, _, err = self.run_cli(['R'])
        self.assertEqual(code, 1)
        self.assertIn('not repeated', err)
        self.assertNotIn('private', err)
        self.assertEqual(self.calls[-1], ('play_catalog', {'catalog': 'epg-123', 'random': {}}))
        self.assertEqual(len(self.calls), 3)

    def test_acknowledgement_cannot_leak_private_fields_or_escape_range(self):
        for reply in ({'dispatched': True, 'channel': dict(self.row, number=24)},
                      {'dispatched': True, 'channel': dict(self.row, url='private')},
                      {'dispatched': True, 'channel': self.row, 'token': 'private'}):
            with self.subTest(reply=reply):
                self.replies['play_catalog'] = reply
                code, _, err = self.run_cli(['R', '23', '23'])
                self.assertEqual(code, 1)
                self.assertNotIn('private', err)

    def test_fixed_empty_selection_rejection_is_clear_without_echoing_extra_data(self):
        self.replies['play_catalog'] = ott.PlayerRejected('private', {
            'error': 'No eligible channels are available, or playback is restricted by the player.', 'token': 'private'})
        code, _, err = self.run_cli(['RR'])
        self.assertEqual(code, 1)
        self.assertIn('No eligible channels', err)
        self.assertNotIn('private', err)
        self.assertEqual([action for action, _ in self.calls].count('play_catalog'), 1)


if __name__ == '__main__':
    unittest.main()
