import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ott_favorites', ROOT / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
CONTRACT = json.loads((ROOT / 'contracts/favorites-v1.json').read_text())
SECRET = 'private-url-token-must-not-appear'
LEGACY_NOTICE = 'Legacy favorites: this player does not advertise global favorites items.\n'


def caps():
    return {'version': 1, 'player': {'version': '1.1.55', 'platform': 'webos', 'runtime': 'page-123'},
            'lifecycle': [], 'input': [], 'playback': [],
            'favorites': {'version': 1, 'operations': ['list', 'add', 'play', 'remove'], 'max_items': 100}}


def result(op):
    return copy.deepcopy(CONTRACT[op + '_result'])


class FavoritesTest(unittest.TestCase):
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

    def test_shared_contract(self):
        self.assertEqual(set(CONTRACT['operations']), ott.FAVORITES_OPERATIONS)
        self.assertEqual(set(CONTRACT['errors']), set(ott.FAVORITES_ERRORS))
        self.assertEqual(CONTRACT['max_items'], ott.MAX_FAVORITES)
        for op in CONTRACT['operations']:
            params = CONTRACT[op + '_request']['params']
            self.assertEqual(ott.favorites_metadata(result(op), 'page-123', params), result(op))

    def test_current_item_is_captured_on_player_without_cli_snapshot(self):
        for alias in ('fav', 'favs', 'favorite', 'favorites', 'favourite', 'favourites', 'FaVoRiTeS'):
            for words, params in [([alias, 'add'], {'op': 'add'}),
                                  ([alias, 'CURRENT'], {'op': 'add'}),
                                  ([alias, 'add', 'Мой', 'канал'], {'op': 'add', 'name': 'Мой канал'}),
                                  ([alias, 'add', '  Мой канал  '], {'op': 'add', 'name': 'Мой канал'}),
                                  ([alias], {'op': 'list'})]:
                code, out, err, calls = self.run_cli(words, [caps(), result(params['op'])], True)
                self.assertEqual((code, err), (0, LEGACY_NOTICE))
                self.assertEqual(json.loads(out), result(params['op']))
                self.assertEqual(calls, [mock.call('dev_tv', 'capabilities', {}),
                                        mock.call('dev_tv', 'favorites', dict(params, runtime='page-123'))])

    def test_saved_selection_uses_player_id_or_query_and_keeps_reserved_channels(self):
        cases = [(['fav', 'play', 'fav-abc123'], {'op': 'play', 'id': 'fav-abc123'}),
                 (['fav', 'play', 'ID:fav-abc123'], {'op': 'play', 'id': 'fav-abc123'}),
                 (['fav', 'play', 'Винни-Пух'], {'op': 'play', 'query': 'Винни-Пух'}),
                 (['fav', 'Винни-Пух'], {'op': 'play', 'query': 'Винни-Пух'}),
                 (['fav', 'play', 'add'], {'op': 'play', 'query': 'add'}),
                 (['fav', 'rm', 'fav-abc123'], {'op': 'remove', 'id': 'fav-abc123'}),
                 (['fav', 'remove', '  Винни-Пух  '], {'op': 'remove', 'query': 'Винни-Пух'}),
                 (['fav', 'play', '--', '--name'], {'op': 'play', 'query': '--name'})]
        for words, params in cases:
            self.assertEqual(ott.parse_command(words), ('favorites', params))
            code, out, err, calls = self.run_cli(words, [caps(), result(params['op'])], True)
            self.assertEqual((code, err), (0, LEGACY_NOTICE))
            self.assertEqual(calls[-1], mock.call('dev_tv', 'favorites', dict(params, runtime='page-123')))
            self.assertEqual(len(calls), 2)
        self.assertEqual(ott.parse_command(['play', 'favorites']), ('play', {'query': 'favorites'}))

    def test_bad_local_inputs_send_nothing(self):
        for words in [['fav', 'list', 'extra'], ['fav', 'play'], ['fav', 'remove'], ['fav', 'add', ''],
                      ['fav', 'play', ' '], ['fav', 'add', 'bad\x1bname'], ['fav', 'play', '\ud800'],
                      ['fav', 'play', 'id:bad'], ['fav', 'play', 'id:fav-'],
                      ['fav', 'add', 'я' * 81], ['fav', 'play', 'я' * 81]]:
            code, out, err, calls = self.run_cli(words, [])
            self.assertEqual((code, out, calls), (1, '', []), words)
        self.assertEqual(ott.parse_command(['fav', 'add', 'я' * 80])[1]['name'], 'я' * 80)

    def test_old_players_invalid_capabilities_and_unsupported_ops_do_not_mutate(self):
        variants = [None, {}, {'version': True, 'operations': ['add'], 'max_items': 100},
                    {'version': 1, 'operations': ['list'], 'max_items': 100},
                    {'version': 1, 'operations': ['add', 'add'], 'max_items': 100},
                    {'version': 1, 'operations': ['add'], 'max_items': True},
                    {'version': 1, 'operations': ['add'], 'max_items': 101},
                    {'version': 1, 'operations': ['add'], 'max_items': 0},
                    {'version': 1, 'operations': ['add'], 'max_items': 100, 'token': SECRET}]
        for variant in variants:
            controls = caps()
            if variant is None:
                controls.pop('favorites')
            else:
                controls['favorites'] = variant
            code, out, err, calls = self.run_cli(['fav', 'add'], [controls])
            self.assertEqual((code, out, len(calls)), (1, '', 1), variant)
            self.assertNotIn(SECRET, err)
        self.assertEqual(self.run_cli(['fav', 'add'], [ott.PlayerUnsupported(SECRET)])[0], 1)

    def test_strict_results_reject_secret_fields_wrong_profile_types_and_stale_runtime(self):
        changes = [lambda x: x.update(version=True), lambda x: x.update(runtime='old-page'),
                   lambda x: x.update(op='remove'), lambda x: x.update(state='ready'),
                   lambda x: x.update(dispatched=1), lambda x: x.update(dispatched=False),
                   lambda x: x.update(url=SECRET), lambda x: x['entry'].update(ref=SECRET),
                   lambda x: x['entry'].update(id='fav-other'), lambda x: x['entry'].update(name=''),
                   lambda x: x['entry'].update(provider='https://private'),
                   lambda x: x['entry'].update(profile_number=True), lambda x: x['entry'].update(profile_number=16),
                   lambda x: x['entry'].update(kind='url'), lambda x: x['entry'].update(profile_name='bad\nname'),
                   lambda x: x['entry'].update(profile_name='я' * 129), lambda x: x['entry'].update(title='я' * 257)]
        for change in changes:
            response = result('play'); change(response)
            code, out, err, calls = self.run_cli(['fav', 'play', 'fav-abc123'], [caps(), response], True)
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
        for profile_name in ('', '   ', '<' * 256):
            response = result('play')
            response['entry'].update(profile_name=profile_name, profile_number=None, provider='bestlist/stalker')
            self.assertEqual(self.run_cli(['fav', 'play', 'fav-abc123'], [caps(), response])[0], 0)

    def test_list_unique_complete_and_bounded_and_add_remove_receipts(self):
        for entries in [None, [{}], [result('add')['entry']] * 2,
                        [dict(result('add')['entry'], id='fav-' + str(i)) for i in range(101)]]:
            response = result('list'); response['entries'] = entries
            self.assertEqual(self.run_cli(['fav'], [caps(), response])[0], 1)
        response = result('list'); response['entries'] = []
        self.assertIn('No saved favorites', self.run_cli(['fav'], [caps(), response])[1])
        response['entries'] = [dict(result('add')['entry'], id='fav-' + str(i), name='<' * 160,
                                   profile_name='<' * 256, title='<' * 512) for i in range(100)]
        self.assertEqual(self.run_cli(['fav'], [caps(), response])[0], 0)
        controls = caps(); controls['favorites']['max_items'] = 99
        self.assertEqual(self.run_cli(['fav'], [controls, response])[0], 1)
        for value in (False, True):
            response = result('add'); response['created'] = value
            self.assertEqual(self.run_cli(['fav', 'add'], [caps(), response])[0], 0)
        response = result('remove'); response['removed'] = False
        self.assertEqual(self.run_cli(['fav', 'rm', 'fav-abc123'], [caps(), response])[0], 1)

    def test_errors_are_allowlisted_runtime_bound_and_never_retried(self):
        for problem, message in ott.FAVORITES_ERRORS.items():
            response = dict(CONTRACT['error_result'], error=problem)
            for error in (response, ott.PlayerRejected(SECRET, response), ott.PlayerUnsupported(SECRET, response)):
                code, out, err, calls = self.run_cli(['fav', 'play', 'fav-abc123'], [caps(), error])
                self.assertEqual((code, out, len(calls)), (1, '', 2))
                self.assertIn(message, err)
                self.assertNotIn(SECRET, err)
        for response in (dict(CONTRACT['error_result'], error=SECRET),
                         dict(CONTRACT['error_result'], runtime='old-page'),
                         dict(CONTRACT['error_result'], token=SECRET)):
            code, out, err, calls = self.run_cli(['fav', 'play', 'fav-abc123'], [caps(), ott.PlayerRejected(SECRET, response)])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)
        for error in (ott.PlayerUnsupported(SECRET), ott.HTTPError(400), ott.HTTPError(404),
                      ott.TransportError('Timeout; do not repeat')):
            code, out, err, calls = self.run_cli(['fav', 'add'], [caps(), error])
            self.assertEqual((code, out, len(calls)), (1, '', 2))
            self.assertNotIn(SECRET, err)

    def test_total_deadline_is_shared_by_discovery_and_command(self):
        client = mock.Mock(timeout=45)
        client.call.return_value = caps()
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 46]), \
                self.assertRaisesRegex(ott.Error, 'no favorites command'):
            ott.favorites_command(client, 'dev_tv', {'op': 'add'}, 45)
        self.assertEqual((client.call.call_count, client.timeout), (1, 45))
        client.reset_mock(); client.call.side_effect = [caps(), result('add')]
        with mock.patch.object(ott.time, 'monotonic', side_effect=[0, 0, 2, 46]), \
                self.assertRaisesRegex(ott.Error, 'Acceptance is uncertain'):
            ott.favorites_command(client, 'dev_tv', {'op': 'add'}, 45)
        self.assertEqual((client.call.call_count, client.timeout), (2, 45))

    def test_discoverability_and_truthful_playback_message(self):
        code, out, err, _ = self.run_cli(['fav', 'play', 'fav-abc123'], [caps(), result('play')])
        self.assertEqual((code, err), (0, LEGACY_NOTICE))
        self.assertIn('visible playback has not been confirmed', out)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_status({'capabilities': ott.capabilities_metadata(caps())}, 'l')
        for command in ('ott l fav', 'fav add', 'fav play', 'fav remove'):
            self.assertIn(command, out.getvalue())
        self.assertNotIn('No controls', out.getvalue())

    def test_safe_request_receipt(self):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 45
        request_id = 'a' * 32
        client.api = mock.Mock(return_value=(202, {'id': request_id}))
        client._wait_receipt = mock.Mock(return_value={'request_id': request_id, 'status': 'ok', 'data': result('add')})
        client.on_receipt = ott.print_request_receipt
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            client.call_receipt('private-device', 'favorites', {'name': SECRET})
        self.assertEqual(json.loads(err.getvalue()), {'action': 'favorites', 'request_id': request_id, 'status': 'ok'})
        self.assertNotIn(SECRET, err.getvalue())


if __name__ == '__main__':
    unittest.main()
