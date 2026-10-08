import contextlib
import copy
import io
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_android', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

class AndroidCLI(unittest.TestCase):
    def client(self):
        c = mock.Mock()
        c.config = {'native_devices': {'web': 'native'}}
        c.credentials = {'devices': [{'id': 'web'}, {'id': 'native'}]}
        c.device.side_effect = lambda x: x
        return c

    def test_routes_to_native_queue_and_retains_trilogy_order(self):
        c = self.client()
        c.call.return_value = {'operation': 'play', 'dispatched': True, 'loop': True, 'total': 3,
                               'token': 'private', 'items': [{'id': fid, 'title': '\x1b[31mTitle', 'url': 'private'} for fid in [47677, 44819, 47674]]}
        data = ott.android_command(c, 'unused', 'web', 'a1', ['queue', 'play', '47677', '44819', '47674'], True)
        c.call.assert_called_once_with('native', 'vportal_queue', {'operation': 'play', 'ids': [47677, 44819, 47674], 'loop': True})
        self.assertNotIn('private', json.dumps(data))
        self.assertNotIn('\x1b', data['items'][0]['title'])

    def test_invalid_requests_never_send(self):
        for words in [['queue', 'play', '1', '1'], ['queue', 'play', '--once', '1'], ['seek', 'nan'],
                      ['seek', '-1'], ['reboot', 'now'], ['update', 'http://x/a', 'a'*64],
                      ['update', 'https://[bad/a', 'a'*64], ['queue', 'play', '9007199254740992']]:
            c = self.client()
            with self.assertRaises(ott.Error):
                ott.android_command(c, 'unused', 'web', 'a1', words, True)
            c.call.assert_not_called()

    def test_binding_refuses_shared_or_circular_identity(self):
        for native,bindings in [('web', {}), ('native', {'other': 'native'}), ('native', {'native': 'third'}), ('missing', {})]:
            c = self.client();c.config['native_devices'] = bindings
            with self.assertRaises(ott.Error):
                ott.android_command(c, 'unused', 'web', 'a1', ['bind', native], True)
        c = self.client()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'config.json'
            ott.android_command(c, path, 'web', 'a1', ['bind', 'native'], True)
            self.assertEqual(json.loads(path.read_text())['native_devices'], {'web': 'native'})
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_receipts_do_not_claim_completion_and_errors_are_private(self):
        c = self.client();c.call.return_value = {'operation': 'restart_app', 'accepted': True, 'completion': 'inspect_status', 'secret': 'private'}
        data = ott.android_command(c, 'unused', 'web', 'a1', ['restart'], True)
        self.assertEqual(set(data), {'operation', 'accepted', 'completion'})
        for reply in [{}, {'operation': 'restart_app', 'accepted': True, 'completion': 'done'}]:
            c.call.return_value = reply
            with self.assertRaises(ott.Error):ott.android_command(c, 'unused', 'web', 'a1', ['restart'], True)
        c.call.side_effect = ott.PlayerRejected('private', {})
        with self.assertRaisesRegex(ott.Error, 'inspect android status') as err:
            ott.android_command(c, 'unused', 'web', 'a1', ['restart'], True)
        self.assertNotIn('private', str(err.exception))

    def test_existing_series_queue_is_readable_without_relaxing_play_or_log_limits(self):
        rows = [{'id': i+1, 'title': 'Episode', 'url': 'private'} for i in range(1000)]
        health = {'version': 1, 'runtime': 'android-test', 'webview_responsive': True,
                  'player': {'queue': {'index': 0, 'total': len(rows), 'items': rows}}}
        result = ott.android_metadata(health, 'maintenance', {'operation': 'health'})
        self.assertEqual(len(result['player']['queue']['items']), 1000)
        self.assertNotIn('private', json.dumps(result))
        status = {'operation': 'status', 'index': 0, 'total': len(rows), 'items': rows}
        self.assertEqual(ott.android_metadata(status, 'vportal_queue', {'operation': 'status'})['total'], 1000)
        rows.append({'id': 1001, 'title': 'Episode'})
        with self.assertRaises(ott.Error):
            ott.android_metadata(health, 'maintenance', {'operation': 'health'})
        with self.assertRaises(ott.Error):
            ott.android_metadata({'version': 1, 'events': [{'time': 1, 'event': 'test'}]*101},
                                 'maintenance', {'operation': 'logs'})
        c = self.client()
        with self.assertRaises(ott.Error):
            ott.android_command(c, 'unused', 'web', 'a1', ['queue', 'play']+[str(i+1) for i in range(101)], True)
        c.call.assert_not_called()

class NativeEvidenceCLI(unittest.TestCase):
    def test_operation_lookup_only_reads_native_health(self):
        c = AndroidCLI().client()
        key = 'a' * 32
        c.call.return_value = {'version': 1, 'runtime': 'new', 'webview_responsive': True,
            'operations': [{'request_id': key, 'state': 'handler_completed', 'action': 'lifecycle',
                'operation': 'reload_player', 'runtime': 'old', 'updated_at': 123, 'evidence': 'handler_completed', 'url': 'private'}]}
        result = ott.android_command(c, 'unused', 'web', 'a1', ['operation', key], True)
        c.call.assert_called_once_with('native', 'maintenance', {'operation': 'health'})
        self.assertEqual(result['receipt']['runtime'], 'old')
        self.assertFalse(result['effect_observed'])
        self.assertNotIn('private', json.dumps(result))
        c.call.return_value.pop('operations')
        self.assertEqual(ott.android_command(c, 'unused', 'web', 'a1', ['operation', key], True)['state'], 'unknown')

    def test_operation_entrypoint_validates_complete_receipt_and_never_replays(self):
        key = 'a' * 32
        complete = {'request_id': key, 'state': 'handler_completed', 'action': 'lifecycle',
                    'operation': 'reload_player', 'runtime': 'old', 'updated_at': 123, 'evidence': 'handler_completed'}
        cases = [(complete, 0)]
        for field in ('action', 'operation', 'runtime', 'updated_at', 'state', 'evidence'):
            bad = copy.deepcopy(complete);bad.pop(field);cases.append((bad, 1))
            bad = copy.deepcopy(complete);bad[field] = None;cases.append((bad, 1))
        for receipt, expected in cases:
            with self.subTest(receipt=receipt):
                c = AndroidCLI().client();c.device.side_effect = lambda _: 'web'
                c.call.return_value = {'version': 1, 'runtime': 'new', 'webview_responsive': True, 'operations': [receipt]}
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(ott, 'Client', return_value=c), mock.patch.object(ott, 'read_json', return_value=c.config), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = ott.main(['--json', 'a1', 'android', 'operation', key])
                self.assertEqual(code, expected)
                c.call.assert_called_once_with('native', 'maintenance', {'operation': 'health'})
                if expected == 0:
                    result = json.loads(out.getvalue())
                    self.assertEqual(result['receipt'], complete)
                    self.assertEqual(result['state'], 'handler_completed')
                    self.assertFalse(result['effect_observed'])
                    self.assertEqual(err.getvalue(), '')
                else:
                    self.assertEqual(out.getvalue(), '')
                    self.assertIn('Invalid native operation receipt', err.getvalue())

    def test_system_evidence_rejects_invented_physical_success(self):
        raw = {'version': 1, 'runtime': 'native', 'webview_responsive': True,
               'system_evidence': {'physical_display_verified': True}}
        with self.assertRaises(ott.Error):
            ott.android_metadata(raw, 'maintenance', {'operation': 'health'})

if __name__ == '__main__':unittest.main()
