import copy
import unittest
from unittest import mock
from test_presets_cli import ott


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.client.device.side_effect = lambda x: x
        self.handoff = {'version': 1, 'provider': 'm3u', 'source_id': 'opaque-source', 'item_id':'stable-channel', 'kind':'live'}

    def test_transfers_only_stable_reference_without_stopping_source(self):
        self.client.call.side_effect = [{'handoff': self.handoff}, {'dispatched':True,'kind':'live'}]
        result = ott.handoff_command(self.client,'one','two')
        self.assertFalse(result['source_stopped'])
        self.assertEqual(self.client.call.call_args_list, [mock.call('one','playback_handoff_export',{}),
            mock.call('two','playback_handoff_import',{'handoff':self.handoff})])

    def test_rejects_url_field_invalid_position_and_same_device_before_import(self):
        bad_rows = [dict(self.handoff,url='https://private.example'), dict(self.handoff,kind='vod'),
                    dict(self.handoff,kind='archive',archive_start=-1), dict(self.handoff,version=True)]
        for handoff in bad_rows:
            self.client.call.reset_mock()
            self.client.call.return_value = {'handoff':handoff}
            with self.assertRaises(ott.Error):
                ott.handoff_command(self.client,'one','two')
            self.assertEqual(self.client.call.call_count,1)
        with self.assertRaises(ott.Error):
            ott.handoff_command(self.client,'one','one')

    def test_negative_response_does_not_print_provider_secrets(self):
        self.client.call.side_effect = ott.PlayerRejected('secret-url',{'error':'secret-url'})
        with self.assertRaises(ott.Error) as cm:
            ott.handoff_command(self.client,'one','two')
        self.assertNotIn('secret-url',str(cm.exception))
