"""Receipt random is explicit, source-bound, and separate from safe R policy."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_receipt_random', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'contracts/random-receipt-v2.json').read_text())


class ReceiptRandomTest(unittest.TestCase):
    def setUp(self):
        self.controls = {
            'version': 1, 'player': {'version': 'test', 'platform': 'webos', 'runtime': 'native-1'},
            'lifecycle': [], 'input': [], 'playback': [],
            'catalog_random': {'version': 2, 'scope': 'receipt', 'request_field': 'receipt_random'},
            'categories': {'version': 1, 'operations': ['list', 'select'], 'maxPageSize': 200},
        }
        self.first = {'id': 'first', 'number': 5, 'name': 'РЕН ТВ HD'}
        self.selected = {'id': 'beyond-first-page', 'number': 978, 'name': 'РЕН ТВ UHD'}
        self.calls, self.overrides = [], {}
        self.client = ott.Client.__new__(ott.Client)
        self.client.config = {'players': {'l': 'dev_1', 'lg2': 'dev_2'}}
        self.client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        self.client.server, self.client.timeout, self.client.on_receipt = 'https://controller.invalid', 45, None
        self.client.call = mock.Mock(side_effect=self.call)

    def call(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params)))
        if action in self.overrides:
            value = self.overrides[action]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(device, params)
            return copy.deepcopy(value)
        if action == 'capabilities':
            return copy.deepcopy(self.controls)
        if action == 'categories':
            return {'version': 1, 'runtime': 'native-1', 'op': 'list', 'catalog': 'categories-' + device,
                    'provider': 'm3u', 'selected': 'favorites', 'offset': 0, 'total': 2,
                    'has_more': False, 'loading': False,
                    'categories': [{'id': 'all', 'name': 'All', 'count': 2000},
                                   {'id': 'favorites', 'name': 'Favorites', 'count': 900}]}
        if action == 'channels':
            return {'version': 1, 'runtime': 'native-1', 'catalog': 'channels-' + device,
                    'category': 'favorites', 'offset': 0, 'total': 900,
                    'has_more': True, 'loading': False, 'channels': [copy.deepcopy(self.first)]}
        if action == 'play_catalog':
            return {'dispatched': True, 'channel': copy.deepcopy(self.selected)}
        raise AssertionError((device, action, params))

    def run_cli(self, words, target='l', json_output=True, receipt_logging=False):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=self.client), \
                mock.patch.object(ott, 'read_json', return_value=self.client.config), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--receipt'] if receipt_logging else []) +
                            (['-j'] if json_output else []) + [target, *words])
        self.assertEqual(self.client.timeout, 45)
        return code, out.getvalue(), err.getvalue()

    def test_explicit_receipt_samples_full_current_category_once(self):
        with mock.patch.object(ott.secrets, 'choice') as choice:
            code, out, err = self.run_cli(['random', '--receipt'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'dispatched': True, 'channel': self.selected})
        self.assertEqual(self.calls, [
            ('dev_1', 'capabilities', {}),
            ('dev_1', 'categories', {'op': 'list', 'runtime': 'native-1', 'offset': 0, 'limit': 200}),
            ('dev_1', 'channels', {'runtime': 'native-1', 'catalog': 'categories-dev_1',
                                 'category': 'favorites', 'offset': 0, 'limit': 1}),
            ('dev_1', 'play_catalog', {'catalog': 'channels-dev_1',
                                     'receipt_random': {'version': 2, 'scope': 'receipt'}}),
        ])
        choice.assert_not_called()

    def test_shared_contract_request_success_and_terminal_errors(self):
        self.controls.update(copy.deepcopy(CONTRACT['capability']))
        self.overrides['channels'] = dict(self.call('dev_1', 'channels', {}),
                                         catalog=CONTRACT['request']['params']['catalog'])
        self.overrides['play_catalog'] = copy.deepcopy(CONTRACT['result'])
        self.calls.clear()
        code, out, err = self.run_cli(['random', '--receipt'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), CONTRACT['result'])
        self.assertEqual(self.calls[-1][1:], (CONTRACT['request']['action'], CONTRACT['request']['params']))
        for key, error in (('rejected_result', ott.PlayerRejected), ('unsupported_result', ott.PlayerUnsupported)):
            with self.subTest(key=key):
                self.overrides['play_catalog'] = error('untrusted player details', copy.deepcopy(CONTRACT[key]))
                self.calls.clear()
                code, out, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertIn('not repeated', err)
                self.assertNotIn('untrusted player details', out + err)
                self.assertEqual(sum(row[1] == 'play_catalog' for row in self.calls), 1)

    def test_search_binds_channel_receipt_not_random_selector(self):
        code, out, err = self.run_cli(['R', '--receipt', 'реН', 'ТВ'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['channel'], self.selected)
        self.assertEqual(self.calls[-2][2]['search'], 'реН ТВ')
        self.assertEqual(set(self.calls[-1][2]), {'catalog', 'receipt_random'})

    def test_global_receipt_logging_flag_does_not_select_receipt_random(self):
        self.controls['random'] = {'version': 1}
        code, _, err = self.run_cli(['R'], receipt_logging=True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.calls[-1][2], {'catalog': 'categories-dev_1', 'random': {}})
        self.calls.clear()
        code, _, err = self.run_cli(['random', '--receipt'], receipt_logging=True)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.calls[-1][2], {'catalog': 'channels-dev_1',
                                           'receipt_random': {'version': 2, 'scope': 'receipt'}})

    def test_parser_keeps_legacy_policy_and_rejects_mixed_options(self):
        self.assertEqual(ott.parse_command(['R']), ('command', {'command': 'random_channel'}))
        self.assertEqual(ott.parse_command(['random', '1', '1000']),
                         ('command', {'command': 'random_channel', 'random_range': [1, 1000]}))
        self.assertEqual(ott.parse_command(['RR']), ('command', {'command': 'random_channel', 'random_mode': 'adult'}))
        for words in (['RR', '--receipt'], ['random', '1', '1000', '--receipt'],
                      ['random', '--receipt', '1', '1000'],
                      ['random', '--receipt', '--range', '1', '1000'],
                      ['random', '--receipt', '--receipt'], ['random', '--receipt', '--mode', 'adult'],
                      ['random', '--receipt', ' '], ['random', '--receipt', 'x\ny'],
                      ['random', '--receipt', 'é' * 513]):
            with self.subTest(words=words):
                self.calls.clear()
                code, _, _ = self.run_cli(words)
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, [])

    def test_missing_marker_or_category_list_never_mutates(self):
        original = copy.deepcopy(self.controls)
        for change in ('catalog_random', 'categories', 'list'):
            with self.subTest(change=change):
                self.controls = copy.deepcopy(original)
                if change == 'list':
                    self.controls['categories']['operations'] = ['select']
                else:
                    del self.controls[change]
                self.calls.clear()
                code, _, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertIn('not supported', err)
                self.assertEqual([row[1] for row in self.calls], ['capabilities'])

    def test_strict_marker_validation_does_not_leak_values(self):
        for marker in (None, True, {}, {'version': True, 'scope': 'receipt', 'request_field': 'receipt_random'},
                       {'version': 2.0, 'scope': 'receipt', 'request_field': 'receipt_random'},
                       {'version': 2, 'scope': 'secret', 'request_field': 'receipt_random'},
                       {'version': 2, 'scope': 'receipt', 'request_field': 'random'},
                       dict(self.controls['catalog_random'], secret='private-token')):
            with self.subTest(marker=marker):
                self.controls['catalog_random'] = marker
                self.calls.clear()
                code, out, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertIn('invalid capabilities', err)
                self.assertNotIn('secret', out + err)
                self.assertNotIn('private-token', out + err)
                self.assertEqual([row[1] for row in self.calls], ['capabilities'])

    def test_new_marker_never_enables_legacy_random_or_provider_random(self):
        for words in (['R'], ['RR'], ['random', '1', '1000'], ['provider', 'm3u', 'R']):
            with self.subTest(words=words):
                self.calls.clear()
                if words[0] == 'provider':
                    self.overrides['providers'] = {'providers': [{'id': 'm3u', 'index': 0, 'name': 'M3U'}]}
                code, _, err = self.run_cli(words)
                self.assertEqual(code, 1)
                self.assertIn('Safe random selection is not supported', err)
                self.assertFalse(any(row[1] in ('play_catalog', 'provider', 'command') for row in self.calls))

    def test_empty_or_loading_pages_never_play(self):
        for action, changes in (('categories', {'loading': True}), ('channels', {'loading': True}),
                                ('channels', {'total': 0, 'has_more': False, 'channels': []})):
            with self.subTest(action=action, changes=changes):
                self.overrides.clear()
                self.overrides[action] = dict(self.call('dev_1', action, {}), **changes)
                self.calls.clear()
                code, _, _ = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertNotIn('play_catalog', [row[1] for row in self.calls])

    def test_changed_channel_receipt_is_not_refreshed_or_replayed(self):
        self.overrides['channels'] = ott.PlayerRejected('private-token', {
            'version': 1, 'runtime': 'native-1', 'op': 'channels', 'state': 'error', 'error': 'catalog_changed'})
        code, out, err = self.run_cli(['random', '--receipt'])
        self.assertEqual(code, 1)
        self.assertNotIn('private-token', out + err)
        self.assertEqual([row[1] for row in self.calls], ['capabilities', 'categories', 'channels'])

    def test_invalid_channel_page_fails_before_mutation(self):
        page = self.call('dev_1', 'channels', {})
        for changes in ({'runtime': 'other'}, {'category': 'all'}, {'offset': 1},
                        {'catalog': 'invalid token'}, {'loading': 1}, {'private': 'token'}):
            with self.subTest(changes=changes):
                self.overrides['channels'] = dict(page, **changes)
                self.calls.clear()
                code, out, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertNotIn('play_catalog', [row[1] for row in self.calls])
                self.assertNotIn('invalid token', out + err)

    def test_rejection_unsupported_and_old_controller_never_fall_back(self):
        for error in (ott.PlayerRejected('private-token', {'error': 'private-token'}),
                      ott.PlayerUnsupported('private-token', {'error': 'private-token'}),
                      ott.HTTPError(400), ott.HTTPError(404), ott.HTTPError(405)):
            with self.subTest(error=type(error).__name__):
                self.overrides['play_catalog'] = error
                self.calls.clear()
                code, out, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertIn('not repeated', err)
                self.assertNotIn('private-token', out + err)
                self.assertEqual([row[1] for row in self.calls], ['capabilities', 'categories', 'channels', 'play_catalog'])

    def test_malformed_or_out_of_search_ack_is_uncertain_without_retry(self):
        good = {'dispatched': True, 'channel': self.selected}
        for reply in ({'dispatched': True}, dict(good, private='private-token'),
                      dict(good, dispatched=1), dict(good, channel=dict(self.selected, number=0)),
                      dict(good, channel=dict(self.selected, number=True)),
                      dict(good, channel=dict(self.selected, number=ott.MAX_CATEGORY_TOTAL + 1)),
                      dict(good, channel=dict(self.selected, name='unrelated')),
                      dict(good, channel=dict(self.selected, id='x' * 2049)),
                      dict(good, channel=dict(self.selected, name='РЕН\x1bTV')),
                      dict(good, channel=dict(self.selected, private='private-token')),
                      dict(good, channel=dict(self.first, number=6)),
                      dict(good, channel=dict(self.first, id='different-id'))):
            with self.subTest(reply=reply):
                self.calls.clear()
                self.overrides['play_catalog'] = reply
                code, out, err = self.run_cli(['random', '--receipt', 'РЕН'])
                self.assertEqual(code, 1)
                self.assertIn('Playback may have started', err)
                self.assertNotIn('private-token', out + err)
                self.assertEqual(sum(row[1] == 'play_catalog' for row in self.calls), 1)

    def test_single_item_receipt_requires_exact_identity_and_accepts_long_ids(self):
        for identity in ('x' * 2048, 'é' * 1024):
            with self.subTest(identity_length=len(identity)):
                self.first = dict(self.first, id=identity)
                self.overrides.pop('channels', None)
                self.overrides['channels'] = dict(self.call('dev_1', 'channels', {}), total=1, has_more=False)
                self.selected = dict(self.first)
                code, out, err = self.run_cli(['random', '--receipt'])
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(json.loads(out)['channel']['id'], identity)
                self.selected = dict(self.first, id='other')
                code, _, err = self.run_cli(['random', '--receipt'])
                self.assertEqual(code, 1)
                self.assertIn('not repeated', err)

    def test_shared_deadline_expiry_before_play_sends_no_mutation(self):
        now = [0]
        self.overrides['channels'] = dict(self.call('dev_1', 'channels', {}))
        page = self.overrides['channels']
        self.overrides['channels'] = lambda *_: (now.__setitem__(0, 46), copy.deepcopy(page))[1]
        self.calls.clear()
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            code, _, _ = self.run_cli(['random', '--receipt'])
        self.assertEqual(code, 1)
        self.assertNotIn('play_catalog', [row[1] for row in self.calls])

    def test_late_or_lost_mutation_ack_is_not_repeated(self):
        now = [0]
        self.overrides['play_catalog'] = lambda *_: (now.__setitem__(0, 46), {'dispatched': True, 'channel': self.selected})[1]
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: now[0]):
            code, _, err = self.run_cli(['random', '--receipt'])
        self.assertEqual(code, 1)
        self.assertIn('not repeated', err)
        self.assertEqual(sum(row[1] == 'play_catalog' for row in self.calls), 1)
        self.calls.clear()
        self.overrides['play_catalog'] = ott.RequestError('request_pending_timeout', 'receipt', 'Outcome unknown; do not repeat blindly')
        code, _, err = self.run_cli(['random', '--receipt'])
        self.assertEqual(code, 1)
        self.assertIn('Outcome unknown', err)
        self.assertEqual(sum(row[1] == 'play_catalog' for row in self.calls), 1)

    def test_groups_keep_independent_receipts_and_run_concurrently(self):
        barrier = threading.Barrier(2)
        self.overrides['play_catalog'] = lambda device, params: (
            barrier.wait(timeout=5), {'dispatched': True, 'channel': dict(self.selected, id=device)})[1]
        code, out, err = self.run_cli(['random', '--receipt'], target='l,lg2')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['succeeded'], 2)
        for device in ('dev_1', 'dev_2'):
            calls = [row for row in self.calls if row[0] == device]
            self.assertEqual([row[1] for row in calls], ['capabilities', 'categories', 'channels', 'play_catalog'])
            self.assertEqual(calls[-1][2], {'catalog': 'channels-' + device,
                                           'receipt_random': {'version': 2, 'scope': 'receipt'}})

    def test_help_is_negotiated_and_plain_output_names_selected_item(self):
        help_out = io.StringIO()
        with contextlib.redirect_stdout(help_out):
            ott.print_player_help({'capabilities': self.controls}, 'l')
        self.assertIn('random --receipt [SEARCH]', help_out.getvalue())
        self.assertIn('no R filtering or weighting', help_out.getvalue())
        self.assertNotIn(' R / random ', help_out.getvalue())
        code, out, err = self.run_cli(['random', '--receipt'], json_output=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('Random channel requested: 978: РЕН ТВ UHD', out)


if __name__ == '__main__':
    unittest.main()
