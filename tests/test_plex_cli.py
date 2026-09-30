import contextlib
import getpass
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import warnings

spec = importlib.util.spec_from_file_location('ott_plex', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

SERVER = 'http://nas.example:32400'
TOKEN = 'test-only-plex-secret'


class PlexParsingTest(unittest.TestCase):
    def test_atomic_setup_and_individual_updates(self):
        for words, settings in [
            (['plex', 'setup', SERVER, TOKEN], {'server': SERVER, 'token': TOKEN}),
            (['PlEx', 'SeRvEr', SERVER + '/'], {'server': SERVER}),
            (['plex', 'token', TOKEN], {'token': TOKEN}),
            (['plex', 'setup', 'https://[::1]:32400/plex/', TOKEN], {'server': 'https://[::1]:32400/plex', 'token': TOKEN}),
        ]:
            with self.subTest(words=words):
                self.assertEqual(ott.parse_command(words), ('provider_settings', {'provider': 'plex', 'settings': settings}))
        self.assertEqual(ott.parse_command(['provider', 'plex']), ('provider', {'query': 'plex'}))
        self.assertEqual(ott.parse_command(['play', 'plex']), ('play', {'query': 'plex'}))

    def test_missing_token_uses_hidden_prompt(self):
        for words, settings in [(['plex', 'setup', SERVER], {'server': SERVER, 'token': TOKEN}),
                                (['plex', 'token'], {'token': TOKEN})]:
            with mock.patch.object(ott.getpass, 'getpass', return_value=TOKEN) as prompt:
                self.assertEqual(ott.parse_command(words)[1]['settings'], settings)
                prompt.assert_called_once_with('Plex token: ')

    def test_no_echo_fallback_or_prompt_for_invalid_address(self):
        def cannot_hide(*args):
            warnings.warn('Cannot hide input', getpass.GetPassWarning)
            self.fail('Echoing fallback must not continue')
        for error in [EOFError(), OSError(), getpass.GetPassWarning()]:
            with mock.patch.object(ott.getpass, 'getpass', side_effect=error), self.assertRaisesRegex(ott.Error, 'Hidden token input'):
                ott.parse_command(['plex', 'token'])
        with mock.patch.object(ott.getpass, 'getpass', side_effect=cannot_hide), self.assertRaisesRegex(ott.Error, 'Hidden token input'):
            ott.parse_command(['plex', 'token'])
        with mock.patch.object(ott.getpass, 'getpass') as prompt, self.assertRaises(ott.Error):
            ott.parse_command(['plex', 'setup', 'not-a-url'])
        prompt.assert_not_called()

    def test_token_file_and_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token'
            path.write_text(TOKEN + '\n')
            for words in [['plex', 'token-file', str(path)], ['plex', 'setup', SERVER, '--token-file', str(path)]]:
                params = ott.parse_command(words)[1]
                self.assertEqual(params['settings']['token'], TOKEN)
                self.assertEqual(params['provider'], 'plex')
            path.write_text('x' * 1024 + '\n')
            self.assertEqual(ott.parse_command(['plex', 'token-file', str(path)])[1]['settings']['token'], 'x' * 1024)
            settings = {'server': SERVER, 'token': TOKEN}
            path.write_text(json.dumps({'provider': 'plex', 'settings': settings}))
            self.assertEqual(ott.parse_command(['provider-config', str(path)]),
                             ('provider_settings', {'provider': 'plex', 'settings': settings}))
            for raw in ['{"provider":"plex","settings":{"token":"first","token":"second"}}',
                        '{"provider":"plex","provider":"m3u","settings":{"token":"secret"}}',
                        '{"provider":"plex","settings":{}}', '{"provider":"plex","settings":{"password":"secret"}}',
                        '{"provider":"plex","settings":{"token":"\\ud800"}}', ' ' * 16385]:
                path.write_text(raw)
                with self.subTest(raw=raw[:80]), self.assertRaises(ott.Error):
                    ott.parse_command(['provider-config', str(path)])
            for raw in [b'\xff', b'a' * 4097, b'two\ntokens', b'']:
                path.write_bytes(raw)
                with self.subTest(raw=raw[:20]), self.assertRaises(ott.Error):
                    ott.parse_command(['plex', 'token-file', str(path)])
            with self.assertRaises(ott.Error):
                ott.parse_command(['plex', 'token-file', str(path.with_name('missing'))])

    def test_invalid_syntax_does_not_prompt(self):
        for words in [[], ['setup'], ['setup', SERVER, '--token-file'], ['setup', SERVER, TOKEN, 'extra'],
                      ['setup', SERVER, '--wrong', 'file'], ['server'], ['server', SERVER, 'extra'],
                      ['token', TOKEN, 'extra'], ['token-file'], ['token-file', 'a', 'b'], ['unknown']]:
            with self.subTest(words=words), mock.patch.object(ott.getpass, 'getpass') as prompt, self.assertRaises(ott.Error):
                ott.parse_command(['plex'] + words)
            prompt.assert_not_called()

    def test_invalid_settings_are_rejected_without_echoing_values(self):
        values = [None, {}, [], {'extra': TOKEN}, {'token': None}, {'server': 42},
                  {'token': ''}, {'token': ' '}, {'token': 'a b'}, {'token': 'a\nb'},
                  {'token': 'a\x00b'}, {'token': 'a\x7fb'}, {'token': '\ud800'},
                  {'token': 'x' * 1025}, {'token': '😀' * 513}]
        for url in ['ftp://nas.example', 'http://u:' + TOKEN + '@nas.example',
                    SERVER + '?token=' + TOKEN, SERVER + '#' + TOKEN, SERVER + '/../media',
                    SERVER + '/a/../b', 'http://nas.example:0', 'http://nas.example:65536',
                    'http://[bad]', 'http://nas.example/with space', 'http://nas.example/%2e%2e',
                    'http://nas.example\\evil', 'http://nas.example/' + 'x' * 8192]:
            values.append({'server': url})
        for value in values:
            with self.subTest(value=repr(value)[:80]), self.assertRaises(ott.Error) as caught:
                ott.validate_plex_settings(value)
            self.assertNotIn(TOKEN, str(caught.exception))
        for token in ['x' * 1024, '😀' * 512]:
            self.assertEqual(ott.validate_plex_settings({'token': token}), {'token': token})


class PlexExecutionTest(unittest.TestCase):
    def run_command(self, response, words=None, json_output=False):
        client = mock.Mock()
        client.device.return_value = 'dev_tv'
        client.call.side_effect = [response]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = ott.main((['--json'] if json_output else []) + ['tv'] +
                              (words if words is not None else ['plex', 'setup', SERVER, TOKEN]))
        return status, output.getvalue(), errors.getvalue(), client.call.call_args_list

    def test_single_atomic_request_and_only_safe_ack_fields(self):
        ack = {'provider': 'plex', 'saved': True, 'fields': ['server', 'token']}
        for json_output in [False, True]:
            status, output, errors, calls = self.run_command(dict(ack, token=TOKEN, server=SERVER, private=TOKEN), json_output=json_output)
            self.assertEqual((status, errors), (0, ''))
            if json_output:
                self.assertEqual(json.loads(output), ack)
            else:
                self.assertEqual(output, 'Plex settings saved: server, token.\n')
            self.assertNotIn(TOKEN, output + errors)
            self.assertNotIn(SERVER, output + errors)
            self.assertEqual(calls, [mock.call('dev_tv', 'provider_settings',
                {'provider': 'plex', 'settings': {'server': SERVER, 'token': TOKEN}})])

    def test_wrong_ack_never_claims_success_or_retries(self):
        for response in [None, [], {}, {'provider': 'plex', 'saved': 1, 'fields': ['server', 'token']},
                         {'provider': 'm3u', 'saved': True, 'fields': ['server', 'token']},
                         {'provider': 'plex', 'saved': True, 'fields': ['server']},
                         {'provider': 'plex', 'saved': True, 'fields': ['token', 'token']},
                         {'provider': 'plex', 'saved': True, 'fields': ['server', TOKEN]},
                         {'provider': 'plex', 'saved': True, 'fields': [{'token': TOKEN}]}]:
            with self.subTest(response=response):
                status, output, errors, calls = self.run_command(response, json_output=True)
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertIn('may have executed; do not repeat', errors)
                self.assertNotIn(TOKEN, errors)

    def test_rejections_redact_player_errors_and_transport_failure_never_retries(self):
        for error in [ott.PlayerRejected(TOKEN, {'token': TOKEN}), ott.PlayerUnsupported(TOKEN),
                      ott.Error('No acknowledgement; do not repeat blindly')]:
            with self.subTest(error=error):
                status, output, errors, calls = self.run_command(error)
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertNotIn(TOKEN, errors)
                self.assertNotIn('Plex settings saved', errors)

    def test_prompt_cancel_sends_nothing(self):
        with mock.patch.object(ott.getpass, 'getpass', side_effect=KeyboardInterrupt):
            status, output, errors, calls = self.run_command({}, words=['plex', 'setup', SERVER])
        self.assertEqual((status, output, errors, calls), (130, '', '', []))

    def test_invalid_settings_send_nothing(self):
        status, output, errors, calls = self.run_command({}, words=['plex', 'server', SERVER + '?token=' + TOKEN])
        self.assertEqual((status, output, calls), (1, '', []))
        self.assertNotIn(TOKEN, errors)


if __name__ == '__main__':
    unittest.main()
