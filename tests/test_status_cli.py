"""Status discovers current controls without causing player actions or hiding status."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_status', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class StatusCliTest(unittest.TestCase):
    def capabilities(self):
        return {'version': 1, 'player': {'version': '1.1.52-beta.48', 'platform': 'browser', 'runtime': 'page-one'},
                'lifecycle': ['reload_player', 'standby', 'wake'], 'input': ['up', 'ok'], 'playback': []}

    def run_cli(self, words, status, capabilities, machine=False, alias='tv'):
        client = mock.Mock(timeout=45)
        client.device.return_value = 'dev_tv'
        client.call.side_effect = [status, capabilities]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main((['--json'] if machine else []) + [alias] + words)
        expected = [mock.call('dev_tv', 'status', {})]
        if not isinstance(status, ott.Error):
            expected.append(mock.call('dev_tv', 'capabilities', {}))
        self.assertEqual(client.call.call_args_list, expected)
        return code, output.getvalue(), errors.getvalue(), client

    def test_default_and_explicit_status_include_only_advertised_commands(self):
        for words in ([], ['status'], ['ST']):
            caps = self.capabilities()
            status = {'ready': True, 'volume': 100, 'player': caps['player']}
            code, out, err, client = self.run_cli(words, status, caps)
            self.assertEqual((code, err), (0, ''))
            self.assertIn('"ready": true', out)
            self.assertIn('ott tv restart  — reload the player', out)
            self.assertIn('ott tv standby', out)
            self.assertIn('ott tv wake', out)
            self.assertIn('ott tv key KEY  — up, ok', out)
            for unsupported in ('restart stream', 'restart app', 'reboot', 'exit', 'seek', '+N', '-N'):
                self.assertNotIn(unsupported, out)
            self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {}),
                                                         mock.call('dev_tv', 'capabilities', {})])
            self.assertEqual(client.timeout, 45)
            self.assertNotIn('capabilities', status)

    def test_json_retains_status_and_projects_validated_capabilities(self):
        caps = self.capabilities()
        status = {'ready': True, 'volume': 55, 'player': dict(caps['player'])}
        for words in ([], ['status'], ['st']):
            code, out, err, _ = self.run_cli(words, status, dict(caps, private='do-not-print'), True)
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out), dict(status, capabilities=caps))
            self.assertNotIn('do-not-print', out)

    def test_optional_failure_keeps_status_and_never_echoes_raw_error(self):
        failures = [ott.PlayerUnsupported('private-token'), ott.PlayerRejected('private-token', {}),
                    ott.Error('private-token'), ott.TransportError('private-token'), ott.HTTPError(401),
                    {'version': 1, 'player': {'secret': 'private-token'}}]
        for failure in failures:
            for machine in (False, True):
                with self.subTest(failure=type(failure).__name__, machine=machine):
                    code, out, err, client = self.run_cli([], {'ready': True}, failure, machine)
                    self.assertEqual((code, err, len(client.call.call_args_list)), (0, '', 2))
                    self.assertNotIn('private-token', out)
                    self.assertIn('"ready": true', out)
                    self.assertNotIn('ott tv restart', out)
                    if machine:
                        value = json.loads(out)
                        self.assertIsNone(value['capabilities'])
                        self.assertTrue(value['capabilities_error'])
                    self.assertEqual(client.timeout, 45)

    def test_changed_runtime_or_invalid_identity_does_not_show_stale_controls(self):
        caps = self.capabilities()
        for identity in (dict(caps['player'], runtime='another-page'), {}, 'invalid'):
            code, out, err, _ = self.run_cli([], {'ready': True, 'player': identity}, caps, True)
            self.assertEqual((code, err), (0, ''))
            value = json.loads(out)
            self.assertIsNone(value['capabilities'])
            self.assertIn('player changed', value['capabilities_error'])

    def test_no_controls_is_explicit_and_parameterized_commands_are_usable(self):
        caps = self.capabilities()
        caps.update(lifecycle=[], input=[], playback=[])
        code, out, _, _ = self.run_cli([], {'ready': True}, caps)
        self.assertEqual(code, 0)
        self.assertIn('No controls are currently advertised', out)
        caps.update(lifecycle=['restart_stream', 'restart_app', 'exit_app', 'reboot_device'],
                    playback=['pause', 'resume', 'seek', 'previous_channel', 'next_channel', 'step_channel'])
        code, out, _, _ = self.run_cli([], {'ready': True}, caps, alias='Living room')
        for command in ('restart stream', 'restart app', 'exit', 'reboot', 'pause', 'resume', 'seek SECONDS', 'prev', 'next', '+N', '-N'):
            self.assertIn("ott 'Living room' " + command, out)
        self.assertNotIn('previous_channel', out)
        self.assertNotIn('next_channel', out)
        self.assertNotIn('step_channel', out)
        self.assertNotIn("ott 'Living room' restart  — reload", out)

    def test_status_failure_never_submits_capabilities_request(self):
        code, _, _, client = self.run_cli([], ott.Error('offline'), self.capabilities())
        self.assertEqual(code, 1)
        self.assertEqual(client.call.call_args_list, [mock.call('dev_tv', 'status', {})])

    def test_secondary_call_uses_only_remaining_budget_and_restores_timeout(self):
        client = mock.Mock(timeout=45)
        budgets = []
        def reply(*_):
            budgets.append(client.timeout)
            return self.capabilities()
        client.call.side_effect = reply
        with mock.patch.object(ott.time, 'monotonic', side_effect=[12, 13]):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertEqual(budgets, [3])
        self.assertEqual(client.timeout, 45)
        self.assertIsNotNone(result['capabilities'])
        with mock.patch.object(ott.time, 'monotonic', return_value=15):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertEqual(client.call.call_count, 1)
        self.assertIsNone(result['capabilities'])
        with mock.patch.object(ott.time, 'monotonic', side_effect=[14, 16]):
            result = ott.status_capabilities(client, 'dev_tv', {'ready': True}, 15)
        self.assertIsNone(result['capabilities'])
        self.assertIn('--timeout', result['capabilities_error'])


if __name__ == '__main__':
    unittest.main()
