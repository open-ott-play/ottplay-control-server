import importlib.util
import json
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

if __name__ == '__main__':unittest.main()
