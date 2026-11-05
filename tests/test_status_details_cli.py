"""Explicit status details negotiate before reading, without changing playback."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_status_details', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class StatusDetailsTest(unittest.TestCase):
    def caps(self):
        return {'version': 1, 'player': {'version': '1.2.3', 'platform': 'webos-native',
                                       'runtime': 'runtime-one', 'standby': False},
                'input': [], 'playback': [], 'lifecycle': [], 'playback_failure_versions': [2, 3]}

    def status(self):
        return {'player': dict(self.caps()['player']), 'ready': True, 'volume': 55,
                'playback_failure': {'version': 3, 'play_generation': 18446744073709551615,
                                     'code': 'media_source', 'context': {
                                         'demux_exit': 'hls_transport_open_failed', 'pipeline_error': -7,
                                         'io_failure': {'source_io': {'transport': 'recv_error'}}}}}

    def run_cli(self, responses, words=None, machine=True):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = responses
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott, 'television_module') as television, \
                mock.patch.object(ott, 'device_power_module') as power, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main((['--json'] if machine else []) + ['tv'] + (words or ['status', '--details']))
        television.assert_not_called()
        power.assert_not_called()
        self.assertEqual(client.timeout, 45)
        return code, out.getvalue(), err.getvalue(), client

    def test_exact_status_and_alias_parse_without_changing_defaults(self):
        for verb in ('status', 'st', 'ST'):
            self.assertEqual(ott.parse_command([verb, '--details']),
                             ('status', {'playback_failure_version': 3}))
            self.assertEqual(ott.parse_command([verb]), ('status', {}))
        self.assertEqual(ott.parse_command([]), ('status', {}))

    def test_invalid_arguments_send_no_request(self):
        for words in (['status', '--details', '3'], ['status', '--details', '--details'],
                      ['status', 'details'], ['status', '--other'], ['providers', '--details']):
            with self.subTest(words=words):
                code, _, _, client = self.run_cli([], words)
                self.assertEqual(code, 1)
                client.call.assert_not_called()

    def test_invalid_status_arguments_are_rejected_before_reading_configuration(self):
        for selector in ('tv', 't1,t2'):
            for tail in (['--details', '3'], ['--details', '--details'], ['--unknown']):
                with mock.patch.object(ott, 'read_json') as config, \
                        mock.patch.object(ott, 'Client') as client, \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(ott.main([selector, 'st', *tail]), 1)
                config.assert_not_called()
                client.assert_not_called()

    def test_json_two_requests_preserve_opaque_failure_and_inputs(self):
        for verb in ('status', 'st'):
            caps, status = self.caps(), self.status()
            status['playback_failure']['future_detail'] = {'opaque': ['kept', 18446744073709551615]}
            before = copy.deepcopy((caps, status))
            code, out, err, client = self.run_cli([caps, status], [verb, '--details'])
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {}),
                             mock.call('dev_tv', 'status', {'playback_failure_version': 3})])
            self.assertEqual(json.loads(out), dict(status, capabilities=caps))
            self.assertEqual((caps, status), before)

    def test_missing_or_invalid_marker_stops_after_capabilities(self):
        for marker in (None, [], [3], [3, 2], [2, 3.0], [2, True], 'secret'):
            caps = self.caps()
            if marker is None:
                del caps['playback_failure_versions']
            else:
                caps['playback_failure_versions'] = marker
            code, out, err, client = self.run_cli([caps])
            self.assertEqual((code, out), (1, ''))
            self.assertIn('Detailed status is not supported', err)
            self.assertIn('No status request was sent', err)
            self.assertNotIn('secret', err)
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'capabilities', {})])

    def test_transport_errors_do_not_fallback_or_leak_messages(self):
        for responses, expected in (([ott.Error('https://secret.invalid/token')], 1),
                                    ([self.caps(), ott.Error('https://secret.invalid/token')], 2)):
            code, out, err, client = self.run_cli(responses)
            self.assertEqual((code, out), (1, ''))
            self.assertNotIn('secret.invalid', err)
            self.assertEqual(client.call.call_count, expected)

    def test_explicit_refusal_or_old_controller_has_no_retry(self):
        for error in (ott.PlayerUnsupported('private message'), ott.PlayerRejected('private message', {}),
                      ott.HTTPError(400), ott.HTTPError(404), ott.HTTPError(405)):
            code, out, err, client = self.run_cli([self.caps(), error])
            self.assertEqual((code, out), (1, ''))
            self.assertIn('rejected or unsupported', err)
            self.assertNotIn('private message', err)
            self.assertEqual(client.call.call_count, 2)

    def test_unsupported_capabilities_sends_no_status(self):
        code, out, err, client = self.run_cli([ott.PlayerUnsupported('private message')])
        self.assertEqual((code, out), (1, ''))
        self.assertIn('No status request was sent', err)
        self.assertNotIn('private message', err)
        self.assertEqual(client.call.call_count, 1)

    def test_invalid_status_does_not_trigger_another_capability_probe(self):
        for status in (None, [], 'private status'):
            code, out, err, client = self.run_cli([self.caps(), status])
            self.assertEqual((code, out), (1, ''))
            self.assertIn('invalid status metadata', err)
            self.assertEqual(client.call.call_count, 2)

    def test_identity_changes_or_missing_identity_are_not_coherent(self):
        for field in ('version', 'platform', 'runtime', None):
            status = self.status()
            if field is None:
                status.pop('player')
            else:
                status['player'][field] = 'different'
            code, out, err, client = self.run_cli([self.caps(), status])
            self.assertEqual((code, out), (1, ''))
            self.assertIn('The player changed while reading detailed status', err)
            self.assertEqual(client.call.call_count, 2)

    def test_one_deadline_reduces_second_budget_and_restores_timeout(self):
        clock = [0.0]
        seen = []
        client = mock.Mock(timeout=45)
        def call(device, action, params):
            seen.append((action, client.timeout))
            clock[0] += 4 if action == 'capabilities' else 2
            return self.caps() if action == 'capabilities' else self.status()
        client.call.side_effect = call
        with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: clock[0]):
            result = ott.detailed_status(client, 'dev_tv', 10)
        self.assertEqual(seen, [('capabilities', 10), ('status', 6)])
        self.assertEqual(result['capabilities'], self.caps())
        self.assertEqual(client.timeout, 45)

    def test_deadline_expiry_never_adds_a_request(self):
        for stage in ('before', 'capabilities', 'status'):
            clock = [10.0 if stage == 'before' else 0.0]
            client = mock.Mock(timeout=45)
            def call(device, action, params):
                if stage == action:
                    clock[0] = 10.0
                return self.caps() if action == 'capabilities' else self.status()
            client.call.side_effect = call
            with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: clock[0]), \
                    self.assertRaisesRegex(ott.Error, 'exceeded --timeout'):
                ott.detailed_status(client, 'dev_tv', 10)
            self.assertEqual(client.call.call_count, {'before': 0, 'capabilities': 1, 'status': 2}[stage])
            self.assertEqual(client.timeout, 45)

    def test_plain_details_show_safe_failure_fields(self):
        code, out, err, _ = self.run_cli([self.caps(), self.status()], machine=False)
        self.assertEqual((code, err), (0, ''))
        for expected in ('Playback failure details:', 'media_source', '18446744073709551615',
                         'hls_transport_open_failed', '-7', 'source_io', 'recv_error'):
            self.assertIn(expected, out)
        self.assertIn('ott tv status --details', out)

    def test_plain_details_show_all_known_io_variants_and_null(self):
        for tag, body, expected in (
                ('hls', {'exit': 'hls_short_body', 'outcome': 'deadline'}, 'hls_short_body'),
                ('auto_fallback', {'outcome': 'refused'}, 'refused'),
                ('audio_decode', {'outcome': 'inconclusive'}, 'inconclusive'),
                ('unclassified', {'outcome': 'inconclusive'}, 'inconclusive')):
            status = self.status()
            status['playback_failure']['context']['io_failure'] = {tag: body}
            code, out, _, _ = self.run_cli([self.caps(), status], machine=False)
            self.assertEqual(code, 0)
            self.assertIn(tag, out)
            self.assertIn(expected, out)
        status = self.status(); status['playback_failure'] = None
        code, out, _, _ = self.run_cli([self.caps(), status], machine=False)
        self.assertEqual(code, 0)
        self.assertIn('No retained failure reported', out)

    def test_preplay_failure_is_visible_when_playback_failure_is_null(self):
        status = self.status()
        status.update(playback_failure=None, preplay={'version': 1, 'phase': 'failed',
                      'last_failure': 'inspect_failed', 'attempt_generation': 5,
                      'phase_elapsed_ms': 15002, 'cancel_reason': None})
        before = copy.deepcopy(status)
        code, out, err, client = self.run_cli([self.caps(), status], machine=False)
        self.assertEqual((code, err), (0, ''))
        for expected in ('Playback preparation:', 'failed', 'inspect_failed',
                         'App attempt generation: 5', '15002 ms', 'No retained failure reported'):
            self.assertIn(expected, out)
        self.assertLess(out.index('inspect_failed'), out.index('No retained failure reported'))
        self.assertEqual(status, before)
        self.assertEqual(client.call.call_count, 2)
        code, out, _, _ = self.run_cli([self.caps(), status])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['preplay'], before['preplay'])

    def test_preplay_projection_rejects_private_values_and_invalid_numbers(self):
        for invalid in (
                {'phase': 'https://secret.invalid', 'last_failure': 'SECRET_TOKEN',
                 'attempt_generation': True, 'phase_elapsed_ms': 1 << 80},
                {'phase': [], 'last_failure': {}, 'attempt_generation': -1, 'phase_elapsed_ms': -1},
                {'phase': False, 'last_failure': None, 'attempt_generation': 5.0, 'phase_elapsed_ms': '15002'},
                'https://secret.invalid'):
            status = self.status()
            status.update(playback_failure=None, preplay=invalid)
            code, out, err, client = self.run_cli([self.caps(), status], machine=False)
            self.assertEqual((code, err), (0, ''))
            for forbidden in ('secret.invalid', 'SECRET_TOKEN', 'True', str(1 << 80), '-1', '5.0', '15002 ms'):
                self.assertNotIn(forbidden, out)
            self.assertEqual(client.call.call_count, 2)

    def test_plain_details_never_print_unknown_strings_or_unbounded_numbers(self):
        status = self.status()
        failure = status['playback_failure']
        failure.update(code='SECRET_UNEXPECTED', play_generation=1 << 80, extra='https://secret.invalid')
        failure['context'].update(demux_exit='https://secret.invalid', pipeline_error=True,
                                 io_failure={'source_io': {'transport': 'SECRET_UNEXPECTED'}})
        code, out, err, _ = self.run_cli([self.caps(), status], machine=False)
        self.assertEqual((code, err), (0, ''))
        for forbidden in ('SECRET_UNEXPECTED', 'secret.invalid', str(1 << 80), 'True'):
            self.assertNotIn(forbidden, out)
        self.assertIn('Not reported', out)

    def test_missing_io_cause_is_not_treated_as_explicit_null(self):
        for missing in (True, False):
            status = self.status()
            context = status['playback_failure']['context']
            if missing:
                del context['io_failure']
            else:
                context['io_failure'] = None
            code, out, _, _ = self.run_cli([self.caps(), status], machine=False)
            self.assertEqual(code, 0)
            if missing:
                self.assertNotIn('No classified IO cause reported', out)
                self.assertIn('IO cause:             Not reported', out)
            else:
                self.assertIn('No classified IO cause reported', out)

    def test_details_keep_native_generated_media_id(self):
        status = self.status()
        status['playback'] = {'state': 'playing', 'kind': 'vod', 'title': 'Example movie', 'provider': 'vportal',
                              'media_id': 'x' * 128 + ':0123456789abcdef', 'channel_number': None,
                              'position': 12, 'duration': 120, 'volume': 55}
        code, out, err, _ = self.run_cli([self.caps(), status])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['playback'], status['playback'])

    def test_group_mixed_support_is_independent_without_mutations(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 't2': 'dev_2'}}
        client.credentials = {'devices': [{'id': 'dev_1'}, {'id': 'dev_2'}]}
        client.server, client.timeout, client.on_receipt = 'https://controller.example', 45, None
        def reply(device, action, params):
            if action == 'capabilities':
                caps = self.caps()
                if device == 'dev_2':
                    del caps['playback_failure_versions']
                return caps
            self.assertEqual((device, action, params), ('dev_1', 'status', {'playback_failure_version': 3}))
            return self.status()
        client.call = mock.Mock(side_effect=reply)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value=client.config), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(['--json', 't1,t2', 'st', '--details'])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertEqual((data['succeeded'], data['failed']), (1, 1))
        self.assertEqual([row['player'] for row in data['results']], ['t1', 't2'])
        self.assertEqual(data['results'][0]['data']['playback_failure'], self.status()['playback_failure'])
        self.assertIn('No status request was sent', data['results'][1]['error'])
        self.assertEqual(client.call.call_count, 3)
        self.assertEqual([call for call in client.call.call_args_list if call.args[0] == 'dev_1'],
                         [mock.call('dev_1', 'capabilities', {}),
                          mock.call('dev_1', 'status', {'playback_failure_version': 3})])
        self.assertEqual(client.timeout, 45)


if __name__ == '__main__':
    unittest.main()
