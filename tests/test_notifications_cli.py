"""Durable notification preferences use one fenced request, never playback effects."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_notifications', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
RUNTIME = 'page-123'
SECRET = 'private-token-never-output'


def capabilities(operations=None):
    return {'version': 1, 'player': {'version': '1.1.59', 'platform': 'browser', 'runtime': RUNTIME},
            'lifecycle': [], 'input': [], 'playback': [],
            'notifications': {'version': 1, 'operations': ['get', 'set'] if operations is None else operations}}


def result(operation='get', enabled=True):
    return {'version': 1, 'runtime': RUNTIME, 'operation': operation, 'enabled': enabled, 'persisted': True}


class NotificationsCliTest(unittest.TestCase):
    def client(self, responses):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 'alias': 'dev_1', 't2': 'dev_2'}}
        client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        client.timeout = 45
        client.call = mock.Mock(side_effect=responses)
        return client

    def run_cli(self, words, responses=(), client=None):
        client = client or self.client(responses)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value=client.config), \
                mock.patch.object(ott, 'Client', return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_get_aliases_bind_exact_runtime_and_project_durable_result(self):
        for verb in ['notifications', 'NOTIFICATIONS', 'notification', 'notif', 'notify']:
            for tail in [[], ['status'], ['GET']]:
                code, out, err, client = self.run_cli(['-j', 't1', verb, *tail], [capabilities(), result()])
                self.assertEqual((code, err, json.loads(out)), (0, '', result()))
                self.assertEqual(client.call.call_args_list, [mock.call('dev_1', 'capabilities', {}),
                    mock.call('dev_1', 'notifications', {'version': 1, 'runtime': RUNTIME, 'operation': 'get'})])

    def test_set_on_off_requires_persistence_and_never_wakes(self):
        for value, enabled in [('ON', True), ('off', False)]:
            code, out, err, client = self.run_cli(['t1', 'notifications', value], [capabilities(), result('set', enabled)])
            self.assertEqual((code, err), (0, ''))
            self.assertIn('Notifications: ' + value.lower(), out)
            self.assertIn('saved on player', out)
            self.assertEqual(client.call.call_args_list[-1], mock.call('dev_1', 'notifications', {
                'version': 1, 'runtime': RUNTIME, 'operation': 'set', 'enabled': enabled}))
            self.assertEqual(client.call.call_count, 2)

    def test_invalid_syntax_does_not_send_and_channel_escape_stays(self):
        for tail in [['toggle'], ['set'], ['set', 'false'], ['on', 'off'], ['false'], ['0'], [SECRET]]:
            code, out, err, client = self.run_cli(['t1', 'notifications', *tail])
            self.assertEqual((code, out), (1, ''))
            client.call.assert_not_called()
            self.assertNotIn(SECRET, err)
        self.assertEqual(ott.parse_command(['play', 'notifications']), ('play', {'query': 'notifications'}))

    def test_old_player_or_missing_permission_never_receives_preference(self):
        old = capabilities(); old.pop('notifications')
        for caps in [old, capabilities([]), capabilities(['get'])]:
            code, out, err, client = self.run_cli(['t1', 'notifications', 'off'], [caps])
            self.assertEqual((code, out, client.call.call_count), (1, '', 1))
            self.assertIn('no preference command was sent', err)

    def test_malformed_capability_fails_before_mutation(self):
        for notification in [None, {}, {'version': True, 'operations': ['get']},
                             {'version': 1, 'operations': ['get', 'get']},
                             {'version': 1, 'operations': ['toggle']},
                             {'version': 1, 'operations': ['get'], 'token': SECRET}]:
            caps = capabilities(); caps['notifications'] = notification
            code, out, err, client = self.run_cli(['t1', 'notifications', 'off'], [caps])
            self.assertEqual((code, out, client.call.call_count), (1, '', 1))
            self.assertNotIn(SECRET, err)

    def test_bad_success_never_claims_persistence_or_repeats(self):
        for key, values in {'version': [True, 2], 'runtime': ['other'], 'operation': ['get'],
                            'enabled': [True, None, 0], 'persisted': [False, None, 1], 'token': [SECRET]}.items():
            for value in values:
                bad = result('set', False); bad[key] = value
                code, out, err, client = self.run_cli(['t1', 'notifications', 'off'], [capabilities(), bad])
                self.assertEqual((code, out, client.call.call_count), (1, '', 2))
                self.assertNotIn(SECRET, err)
                self.assertIn('invalid notification', err)

    def test_bound_negative_codes_are_safe_and_unbound_errors_invalid(self):
        for kind in [ott.PlayerRejected, ott.PlayerUnsupported]:
            for error in ott.NOTIFICATIONS_ERRORS:
                data = {'version': 1, 'runtime': RUNTIME, 'operation': 'set', 'error': error}
                code, out, err, client = self.run_cli(['t1', 'notifications', 'off'],
                    [capabilities(), kind(SECRET, data)])
                self.assertEqual((code, out, client.call.call_count), (1, '', 2))
                self.assertIn('not repeated', err)
                self.assertNotIn(SECRET, err)
        for bad in [{'error': SECRET}, {'version': 1, 'runtime': 'old', 'operation': 'set', 'error': 'unsupported'}]:
            code, out, err, client = self.run_cli(['t1', 'notifications', 'off'],
                [capabilities(), ott.PlayerRejected(SECRET, bad)])
            self.assertEqual((code, out, client.call.call_count), (1, '', 2))
            self.assertNotIn(SECRET, err)

    def test_old_controller_is_clear_and_not_retried(self):
        code, out, err, client = self.run_cli(['t1', 'notifications', 'off'], [capabilities(), ott.HTTPError(400)])
        self.assertEqual((code, out, client.call.call_count), (1, '', 2))
        self.assertIn('update the controller', err)

    def test_group_resolves_all_and_deduplicates_before_sending(self):
        client = self.client([capabilities(), result('set', False), capabilities(), result('set', False)])
        events = []
        resolve = client.device
        client.device = lambda name: (events.append(('resolve', name)), resolve(name))[1]
        responses = iter(client.call.side_effect)
        client.call.side_effect = lambda device, action, params: (events.append(('send', device)), next(responses))[1]
        code, out, err, client = self.run_cli(['-j', 'T1,alias,t2', 'notifications', 'off'], client=client)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(events[:3], [('resolve', 'T1'), ('resolve', 'alias'), ('resolve', 't2')])
        self.assertEqual(client.call.call_count, 4)
        data = json.loads(out)
        self.assertEqual((data['succeeded'], data['failed']), (2, 0))
        self.assertEqual([row['player'] for row in data['results']], ['T1', 't2'])

    def test_group_invalid_targets_or_syntax_send_nothing(self):
        for words in [['t1,missing', 'notifications', 'off'], ['t1,t2', 'notifications', 'toggle'],
                      ['t1,', 'notifications', 'off']]:
            code, out, _, client = self.run_cli(words)
            self.assertEqual((code, out), (1, ''))
            client.call.assert_not_called()

    def test_group_partial_failure_does_not_hide_success(self):
        bad = {'version': 1, 'runtime': RUNTIME, 'operation': 'set', 'error': 'storage_unavailable'}
        code, out, err, client = self.run_cli(['-j', 't1,t2', 'notifications', 'off'],
            [capabilities(), ott.PlayerRejected(SECRET, bad), capabilities(), result('set', False)])
        self.assertEqual((code, err, client.call.call_count), (1, '', 4))
        data = json.loads(out)
        self.assertEqual((data['succeeded'], data['failed']), (1, 1))
        self.assertFalse(data['results'][0]['ok']); self.assertTrue(data['results'][1]['ok'])
        self.assertNotIn(SECRET, out)

    def test_status_help_advertises_supported_notification_controls(self):
        data = {'provider': 'm3u', 'player': capabilities()['player'], 'capabilities': capabilities()}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_status(data, 't1')
        self.assertIn('ott t1 notifications on / off', out.getvalue())
        self.assertIn('ott t1,t2,t3,t4 notifications off', out.getvalue())

    def test_public_ha_status_projects_reported_preference_without_secrets(self):
        raw = {'version': 1, 'bridge': {'enabled': True, 'state': 'connected', 'camera_acquired': 2,
                                       'camera_failed': 1, 'camera_skipped': 3, 'private': SECRET},
               'players': [{'device_id': 'dev_1', 'active': False, 'pending': 0,
                            'notifications_enabled': False, 'media_jpeg': True, 'secret': SECRET}]}
        data = ott.home_assistant_metadata(raw)
        self.assertFalse(data['players'][0]['notifications_enabled'])
        self.assertEqual(data['bridge']['camera_acquired'], 2)
        self.assertEqual(data['bridge']['camera_failed'], 1)
        self.assertEqual(data['bridge']['camera_skipped'], 3)
        self.assertNotIn(SECRET, json.dumps(data))
        raw['players'][0]['notifications_enabled'] = 0
        with self.assertRaises(ott.Error):
            ott.home_assistant_metadata(raw)


if __name__ == '__main__':
    unittest.main()
