import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('ott_ha', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class HomeAssistantCliTest(unittest.TestCase):
    def status(self):
        return {'version': 1, 'bridge': {'enabled': True, 'state': 'connected', 'events_received': 3},
                'players': [{'device_id': 'tv:living_room', 'active': True, 'pending': 1}],
                'delivery': {'queued': 2, 'polled': 1, 'skipped': 0, 'dropped': 0}}

    def run_cli(self, words, response=None, error=None):
        client = mock.Mock()
        client.api.return_value = (200, self.status() if response is None else response)
        client.api.side_effect = error
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client

    def test_read_status_and_machine_output(self):
        for words in [['ha', 'status'], ['HA', 'STATUS'], ['ha', 'status', '-j'], ['--json', 'ha', 'status']]:
            code, out, err, client = self.run_cli(words)
            self.assertEqual((code, err), (0, ''))
            client.api.assert_called_once_with('/api/home-assistant/status')
            client.call.assert_not_called()
            if '-j' in words or '--json' in words:
                self.assertEqual(json.loads(out), self.status())
            else:
                self.assertIn('Home Assistant: connected', out)
                self.assertIn('tv:living_room: active', out)
                self.assertIn('does not confirm', out)

    def test_disabled_and_scoped_responses(self):
        data = {'version': 1, 'bridge': {'enabled': False, 'state': 'disabled'}, 'players': []}
        code, out, err, _ = self.run_cli(['ha', 'status', '-j'], data)
        self.assertEqual((code, err, json.loads(out)), (0, '', data))

    def test_no_arbitrary_fields_printed(self):
        data = self.status()
        for obj in [data, data['bridge'], data['players'][0], data['delivery']]:
            obj['token'] = 'secret-never-print'
        code, out, err, _ = self.run_cli(['ha', 'status', '-j'], data)
        self.assertEqual(code, 0)
        self.assertNotIn('secret-never-print', out + err)
        for path, value in [('state', 'secret-never-print'), ('last_error_code', 'secret-never-print')]:
            broken = copy.deepcopy(data)
            broken['bridge'][path] = value
            code, out, err, _ = self.run_cli(['ha', 'status'], broken)
            self.assertEqual((code, out), (1, ''))
            self.assertNotIn('secret-never-print', err)

    def test_invalid_usage_does_not_send_and_old_server_is_clear(self):
        for words in [['ha'], ['ha', 'status', 'extra'], ['ha', 'token', 'secret-never-print']]:
            code, out, err, client = self.run_cli(words)
            self.assertEqual((code, out), (1, ''))
            client.api.assert_not_called()
            self.assertNotIn('secret-never-print', err)
        code, out, err, _ = self.run_cli(['ha', 'status'], error=ott.HTTPError(404))
        self.assertEqual((code, out), (1, ''))
        self.assertIn('update the controller', err)


if __name__ == '__main__':
    unittest.main()
