import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_pairing', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class PairingCliTest(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.client.device.return_value = 'dev_tv'
        self.client.config = {'players': {'l': 'dev_tv'}}
        self.row = {'id': 'a' * 32, 'device_id': 'dev_tv', 'code': 'ABCD2345',
                    'address': 'https://control.example/ott-control', 'expires_in': 120}

    def run_management(self, words, json_output=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = ott.management(self.client, '/unused', words, json_output=json_output)
        self.assertTrue(result)
        return output.getvalue()

    def test_approval_matches_device_and_screen_code(self):
        other = dict(self.row, device_id='dev_other', id='b' * 32)
        self.client.api.side_effect = [(200, {'pairings': [other, self.row]}), (200, {'status': 'approved'})]
        output = self.run_management(['approve', 'l', 'abcd2345'])
        self.assertIn('Pairing approved for l', output)
        self.assertEqual(self.client.api.call_args_list, [mock.call('/api/pairings'),
                         mock.call('/api/pairings/approve', {'id': 'a' * 32, 'code': 'ABCD2345'})])

    def test_wrong_code_or_device_never_approves(self):
        for rows in [[], [dict(self.row, code='WRONG123')], [dict(self.row, device_id='dev_other')],
                     [self.row, dict(self.row, id='b' * 32)]]:
            with self.subTest(rows=rows):
                self.client.api.reset_mock()
                self.client.api.return_value = (200, {'pairings': rows})
                with self.assertRaisesRegex(ott.Error, 'No unique pending'):
                    self.run_management(['approve', 'l', 'ABCD2345'])
                self.client.api.assert_called_once_with('/api/pairings')

    def test_lost_approval_response_is_never_replayed(self):
        self.client.api.side_effect = [(200, {'pairings': [self.row]}), ott.TransportError('lost response')]
        with self.assertRaisesRegex(ott.Error, 'may have succeeded'):
            self.run_management(['approve', 'l', 'ABCD2345'])
        self.assertEqual(self.client.api.call_count, 2)

    def test_invalid_code_is_rejected_before_network(self):
        for code in ['', '123', 'ABCD-2345', 'ABCD2345\n']:
            with self.subTest(code=code), self.assertRaises(ott.Error):
                self.run_management(['approve', 'l', code])
        self.client.api.assert_not_called()

    def test_pending_lists_alias_and_bound_controller(self):
        self.client.api.return_value = (200, {'pairings': [self.row]})
        output = self.run_management(['pending'])
        self.assertIn('l  code=ABCD2345', output)
        self.assertIn(self.row['address'], output)
        self.assertNotIn('a' * 32, output)

    def test_discovery_and_pending_json_remain_one_document(self):
        for words, data in [(['discover'], {'version': 1, 'servers': []}),
                            (['pending'], {'pairings': [self.row]})]:
            with self.subTest(words=words):
                self.client.api.return_value = (200, data)
                output = self.run_management(words, json_output=True)
                self.assertEqual(json.loads(output), data)


if __name__ == '__main__':
    unittest.main()
