import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import test_player_settings_cli as base

ott, settings = base.ott, base.settings
FIXTURE = json.loads((base.ROOT / 'contracts/player-settings-patch-v1.json').read_text())


def caps():
    result = base.caps()
    result['player_settings_patch'] = copy.deepcopy(FIXTURE['capability'])
    return result


def result(name):
    return copy.deepcopy(FIXTURE['responses'][name])


class SettingsPatchCliTests(unittest.TestCase):
    run_cli = base.PlayerSettingsCliTests.run_cli

    def write(self, responses, values=None, json_output=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            path.write_text(json.dumps(values if values is not None else FIXTURE['requests']['set']['values']))
            path.chmod(0o600)
            return self.run_cli((['-j'] if json_output else []) + ['f', 'settings', 'patch', 'command_server', '--value-file', str(path)], responses)

    def test_get_and_atomic_write_are_redacted_and_bound(self):
        code, out, err, client = self.run_cli(['-j', 'f', 'settings', 'patch', 'command_server'], [caps(), result('get')])
        self.assertEqual((code, err, json.loads(out)), (0, '', result('get')))
        self.assertEqual(client.call.call_args, mock.call('device-f', 'player_settings_patch', FIXTURE['requests']['get']))
        code, out, err, client = self.write([caps(), result('get'), result('set')])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(client.call.call_args, mock.call('device-f', 'player_settings_patch', FIXTURE['requests']['set']))
        self.assertEqual(client.call.call_count, 3)
        self.assertIn('New connectivity is not confirmed', out)
        for value in FIXTURE['requests']['set']['values'].values():
            if isinstance(value, str):
                self.assertNotIn(value, out + err)

    def test_local_action_descriptor_is_visible_and_cannot_write(self):
        data = base.result('get')
        data['descriptor']['access'] = {'read': False, 'write': False, 'reason': 'local_action_required'}
        code, out, err, client = self.run_cli(['f','setting','bufSize','3'], [base.caps(), data])
        self.assertEqual((code, out, client.call.call_count), (1, '', 2))
        self.assertIn('local_action_required', err)

    def test_noop_and_failed_transition_have_honest_status(self):
        code, out, err, _ = self.write([caps(), result('get'), result('unchanged')])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('without reconnecting', out)
        data = result('get'); data['transition'] = {'state': 'rolled_back', 'reason': 'handshake_timeout'}
        code, out, err, _ = self.run_cli(['f', 'settings', 'patch', 'command_server'], [caps(), data])
        self.assertEqual((code, err), (1, '')); self.assertIn('rolled back', out)

    def test_unchanged_values_can_require_a_pending_reconnect(self):
        code, out, err, _ = self.write([caps(), result('get'), result('unchanged_pending')])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('New connectivity is not confirmed', out)
        self.assertNotIn('without reconnecting', out)

    def test_invalid_private_files_are_rejected_before_any_request(self):
        invalid = ['{}', '[]', '{"commandServerEnabled":true}', '{"commandServerToken":"a","commandServerToken":"b"}',
                   '{"localHttpEnabled":1}', '{"commandServerToken":NaN}', json.dumps({'commandServerToken': 'x' * 257}),
                   json.dumps({'commandServerToken': '\ud800'}), ' ' * 16385]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            for raw in invalid:
                path.write_text(raw)
                code, out, err, client = self.run_cli(['f', 'settings', 'patch', 'command_server', '--value-file', str(path)], [])
                self.assertEqual((code, out, client.call.call_count), (1, '', 0))
                self.assertNotIn(raw, err)

    def test_old_capability_and_cross_group_fields_never_write(self):
        code, out, err, client = self.write([base.caps()])
        self.assertEqual((code, out, client.call.call_count), (1, '', 1))
        for key, value in [('max_fields', True), ('groups', ['command_server']), ('version', 2)]:
            data = caps(); data['player_settings_patch'][key] = value
            code, out, err, client = self.write([data])
            self.assertEqual((code, out, client.call.call_count), (1, '', 1))

    def test_secret_leaks_stale_binding_and_false_success_are_rejected(self):
        changes = [lambda d: d.update(runtime='other'), lambda d: d.update(group='local_http'),
                   lambda d: d['settings'][0].update(value='secret-do-not-print'),
                   lambda d: d['settings'][0].update(redacted=False),
                   lambda d: d['settings'].pop(), lambda d: d['settings'].__setitem__(1, d['settings'][0]),
                   lambda d: d['settings'][2].update(value=0), lambda d: d['settings'][2].pop('persisted_value'),
                   lambda d: d['context'].update(token='other'), lambda d: d['context'].update(provider='plex'),
                   lambda d: d.update(application={'state': 'completed'}),
                   lambda d: d.update(transition={'state': 'applied'}), lambda d: d.update(persisted=False)]
        for change in changes:
            data = result('set'); change(data)
            code, out, err, client = self.write([caps(), result('get'), data])
            self.assertEqual((code, out, client.call.call_count), (1, '', 3))
            self.assertNotIn('secret-do-not-print', err)

    def test_transition_matrix_and_snapshot_storage(self):
        request = FIXTURE['requests']['get']
        for state, reasons in FIXTURE['transition_matrix'].items():
            for reason in reasons:
                data = result('get'); data['transition'] = {'state': state}
                if reason is not None:
                    data['transition']['reason'] = reason
                self.assertEqual(settings.patch_metadata(ott, data, request), data)
        for transition in [{'state': 'idle', 'reason': None}, {'state': 'rolled_back', 'reason': 'superseded'}, {'state': 'failed'}]:
            data = result('get'); data['transition'] = transition
            with self.assertRaises(ott.Error): settings.patch_metadata(ott, data, request)
        data = result('get'); data['settings'][2]['storage'] = 'default'; data['settings'][2].pop('persisted_value')
        self.assertEqual(settings.patch_metadata(ott, data, request), data)
        data['settings'][2]['persisted_value'] = 1
        with self.assertRaises(ott.Error): settings.patch_metadata(ott, data, request)

    def test_rejected_write_has_no_retry_and_does_not_echo_remote_error(self):
        data = result('error'); data['error'] = 'secret-do-not-print'
        code, out, err, client = self.write([caps(), result('get'), ott.PlayerRejected('secret-do-not-print', data)])
        self.assertEqual((code, out, client.call.call_count), (1, '', 3))
        self.assertNotIn('secret-do-not-print', err)
        self.assertIn('not repeated', err)

    def test_deadline_is_shared_and_timeout_restored(self):
        with mock.patch.object(settings.time, 'monotonic', side_effect=[0, 0, 1, 46]):
            code, out, err, client = self.write([caps()])
        self.assertEqual((code, out, client.call.call_count), (1, '', 1))
        self.assertIn('timeout', err)

    def test_local_http_partial_write_and_scalar_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'; path.write_text('{"localHttpEnabled":0}')
            response = result('local_http_get')
            response.update(op='set', persisted=True, changed=True, application={'state':'deferred','reason':'after_ack'}, transition={'state':'pending'})
            response['settings'][2].update(value=0, persisted_value=0)
            code, out, err, client = self.run_cli(['f','settings','patch','local_http','--value-file',str(path)], [caps(),result('local_http_get'),response])
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(client.call.call_args, mock.call('device-f','player_settings_patch',FIXTURE['requests']['partial']))
        error = base.result('error'); error.update(reason='atomic_patch_required')
        code, out, err, client = self.run_cli(['f','setting','bufSize','3'],[base.caps(),base.result('get'),ott.PlayerRejected('rejected',error)])
        self.assertEqual(code,1); self.assertIn('settings patch',err)


if __name__ == '__main__': unittest.main()
