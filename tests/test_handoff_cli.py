import contextlib
import io
import unittest
from unittest import mock
from test_presets_cli import ott


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.client.device.side_effect = lambda x: x
        self.handoff = {'version': 1, 'provider': 'm3u', 'source_id': 'opaque-source', 'item_id':'channel-v1:1234abcd.5678abcd', 'kind':'live'}

    def test_transfers_only_stable_reference_without_stopping_source(self):
        self.client.call.side_effect = [{'handoff': self.handoff}, {'dispatched':True,'kind':'live'}]
        result = ott.handoff_command(self.client,'one','two')
        self.assertFalse(result['source_stopped'])
        self.assertEqual(self.client.call.call_args_list, [mock.call('one','playback_handoff_export',{}),
            mock.call('two','playback_handoff_import',{'handoff':self.handoff})])

    def test_rejects_url_field_invalid_position_and_same_device_before_import(self):
        bad_rows = [dict(self.handoff,url='https://private.example'), dict(self.handoff,kind='vod'),
                    dict(self.handoff,kind='archive',archive_start=-1), dict(self.handoff,version=True),
                    dict(self.handoff,item_id='https://private.example/stream?token=secret'),
                    dict(self.handoff,item_id='channel-v1:1234ABCD.5678abcd')]
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

    def test_rejects_unconfirmed_target_import_without_retry(self):
        for response in [
                {'dispatched': False, 'kind': 'live'},
                {'dispatched': True, 'kind': 'archive'},
                {'dispatched': True, 'kind': 'live', 'extra': 'unexpected'}]:
            with self.subTest(response=response):
                self.client.call.reset_mock()
                self.client.call.side_effect = [{'handoff': self.handoff}, response]
                with self.assertRaises(ott.Error):
                    ott.handoff_command(self.client, 'one', 'two')
                self.assertEqual(self.client.call.call_count, 2)

    def run_main(self, responses):
        self.client.reset_mock()
        self.client.call.side_effect = responses
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott, 'Client', return_value=self.client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = ott.main(['--json', 'handoff', 'one', 'two'])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(self.client.device.call_args_list, [mock.call('one'), mock.call('two')])
        self.assertNotIn('private.example', output.getvalue() + errors.getvalue())
        self.assertNotIn('private-provider-token', output.getvalue() + errors.getvalue())
        return errors.getvalue()

    def test_main_rejects_invalid_export_before_import_without_printing_secrets(self):
        invalid = dict(self.handoff, url='https://private.example/?token=private-provider-token')
        error = self.run_main([{'handoff': invalid}])
        self.assertEqual(error, 'Error: The source player returned an invalid handoff\n')
        self.assertEqual(self.client.call.call_args_list, [mock.call('one', 'playback_handoff_export', {})])

    def test_main_rejects_provider_response_without_retry_or_printing_secrets(self):
        for exception in (ott.PlayerRejected, ott.PlayerUnsupported):
            for target_rejected in (False, True):
                with self.subTest(exception=exception.__name__, target_rejected=target_rejected):
                    failure = exception('https://private.example/?token=private-provider-token',
                                        {'error': 'private-provider-token'})
                    responses = ([{'handoff': self.handoff}] if target_rejected else []) + [failure]
                    error = self.run_main(responses)
                    self.assertEqual(error, 'Error: Handoff is unavailable or rejected. Both players require '
                                     'the same configured source and a unique matching channel\n')
                    expected = [mock.call('one', 'playback_handoff_export', {})]
                    if target_rejected:
                        expected.append(mock.call('two', 'playback_handoff_import', {'handoff': self.handoff}))
                    self.assertEqual(self.client.call.call_args_list, expected)

    def test_main_rejects_unconfirmed_import_without_retry_or_printing_secrets(self):
        for response in [
                {'dispatched': False, 'kind': 'live'},
                {'dispatched': True, 'kind': 'archive'},
                {'dispatched': True, 'kind': 'live', 'extra': 'private-provider-token'}]:
            with self.subTest(response=response):
                error = self.run_main([{'handoff': self.handoff}, response])
                self.assertEqual(error, 'Error: Target handoff was not confirmed; inspect playback before retrying\n')
                self.assertEqual(self.client.call.call_args_list, [
                    mock.call('one', 'playback_handoff_export', {}),
                    mock.call('two', 'playback_handoff_import', {'handoff': self.handoff})])
