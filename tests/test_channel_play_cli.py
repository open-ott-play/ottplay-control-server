import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('ott_channel_play', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)


class ChannelPlayTest(unittest.TestCase):
    def setUp(self):
        self.matches = [
            {'id': 'ren', 'number': 706, 'name': 'РЕН ТВ'},
            {'id': 'ren-hd', 'number': 1001, 'name': 'РЕН ТВ HD'},
        ]

    def acknowledgement(self, row=None):
        return {'dispatched': True, 'channel': copy.deepcopy(row or self.matches[-1])}

    def run_play(self, words, replies, json_output=False):
        client = mock.Mock()
        client.device.return_value = 'dev_iphone'
        client.call.side_effect = replies
        stdout, stderr = io.StringIO(), io.StringIO()
        args = (['--json'] if json_output else []) + ['iphone'] + words
        with mock.patch.object(ott, 'Client', return_value=client), \
                mock.patch.object(ott, 'read_json', return_value={}), \
                mock.patch.object(ott.secrets, 'choice', side_effect=lambda rows: rows[-1]) as choice, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = ott.main(args)
        self.choice = choice
        return status, stdout.getvalue(), stderr.getvalue(), client.call.call_args_list

    def test_text_lists_every_match_and_plays_random_nonfirst_channel_once(self):
        for words in [['РЕН'], ['рЕн'], ['play', ' РЕН '], ['РЕН', 'ТВ']]:
            with self.subTest(words=words):
                status, output, errors, calls = self.run_play(
                    words, [{'channels': self.matches}, self.acknowledgement()])
                self.assertEqual(status, 0, errors)
                self.assertEqual(output, '706: РЕН ТВ\n1001: РЕН ТВ HD\n')
                self.assertIn('Randomly selected channel: 1001: РЕН ТВ HD', errors)
                self.assertIn('Channel switch requested: 1001: РЕН ТВ HD', errors)
                query = ' '.join(words[1:] if words[0] == 'play' else words).strip()
                self.assertEqual(calls, [mock.call('dev_iphone', 'channels', {'search': query}),
                                        mock.call('dev_iphone', 'play', {'query': '1001'})])
                self.choice.assert_called_once_with(self.matches)

    def test_single_match_plays_without_random_draw(self):
        row = self.matches[0]
        status, output, errors, calls = self.run_play(
            ['РЕН'], [{'channels': [row]}, self.acknowledgement(row)])
        self.assertEqual((status, output), (0, '706: РЕН ТВ\n'))
        self.assertIn('Channel switch requested: 706', errors)
        self.assertNotIn('Randomly selected', errors)
        self.assertEqual(calls[-1], mock.call('dev_iphone', 'play', {'query': '706'}))
        self.choice.assert_not_called()

    def test_numeric_query_retains_one_request_and_original_response(self):
        reply = self.acknowledgement()
        status, output, errors, calls = self.run_play(['1001'], [reply], json_output=True)
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output), reply)
        self.assertEqual(calls, [mock.call('dev_iphone', 'play', {'query': '1001'})])
        self.choice.assert_not_called()

    def test_json_contains_full_list_and_only_safe_playback_fields(self):
        rows = [dict(row, url='private') for row in self.matches]
        reply = dict(self.acknowledgement(), extra='private')
        reply['channel']['url'] = 'private'
        status, output, errors, calls = self.run_play(['РЕН'], [{'channels': rows}, reply], json_output=True)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), {'channels': self.matches, 'playback': self.acknowledgement()})
        self.assertNotIn('private', output + errors)
        self.assertEqual(len(calls), 2)

    def test_long_match_list_is_not_truncated_and_terminal_controls_are_sanitized(self):
        rows = [{'id': str(n), 'number': n, 'name': f'Zee {n}\x1b[31m'} for n in range(1, 301)]
        status, output, errors, calls = self.run_play(['zee'], [{'channels': rows}, self.acknowledgement(rows[-1])])
        self.assertEqual(status, 0, errors)
        self.assertEqual(len(output.splitlines()), 300)
        self.assertIn('1: Zee 1 [31m', output)
        self.assertIn('300: Zee 300 [31m', output)
        self.assertNotIn('\x1b', output + errors)
        self.assertEqual(calls[-1], mock.call('dev_iphone', 'play', {'query': '300'}))

    def test_unicode_casefold_duplicate_exact_names_and_numeric_ids(self):
        rows = [{'id': 15, 'number': 2, 'name': 'Straße'}, {'id': 16, 'number': 10, 'name': 'STRASSE'}]
        reply = self.acknowledgement(rows[-1])
        reply['channel']['id'] = '16'
        status, output, errors, calls = self.run_play(['STRASSE'], [{'channels': rows}, reply])
        self.assertEqual(status, 0, errors)
        self.assertEqual(output, '2: Straße\n10: STRASSE\n')
        self.assertEqual(calls[-1], mock.call('dev_iphone', 'play', {'query': '10'}))

    def test_numeric_and_empty_rejections_do_not_search_or_retry(self):
        for query in ['706', ' 706 ', '+706', '-706', '7.06', '7e2', '７０６', '']:
            with self.subTest(query=query):
                status, output, _, calls = self.run_play(['play', query], [ott.PlayerRejected('Rejected', {})])
                self.assertEqual((status, output), (1, ''))
                self.assertEqual(calls, [mock.call('dev_iphone', 'play', {'query': query})])
                self.choice.assert_not_called()

    def test_no_matches_returns_empty_list_without_playback(self):
        status, output, errors, calls = self.run_play(['missing'], [{'channels': []}], json_output=True)
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)['channels'], [])
        self.assertIn('No channels match', errors)
        self.assertEqual(calls, [mock.call('dev_iphone', 'channels', {'search': 'missing'})])
        self.choice.assert_not_called()

    def test_invalid_match_metadata_order_and_identity_never_dispatch(self):
        bad_rows = [None, {}, 'invalid']
        for number in [None, 0, -1, True, 706.0, '706', 9007199254740992]:
            rows = copy.deepcopy(self.matches)
            rows[0]['number'] = number
            bad_rows.append(rows)
        for field, values in {'id': [None, '', True, 1.5, [], {}, '\ud800', 'x' * 513],
                              'name': [None, '', ' ', True, [], {}, '\ud800', 'РЕН' * 6000, 'unrelated']}.items():
            for value in values:
                rows = copy.deepcopy(self.matches)
                rows[1][field] = value
                bad_rows.append(rows)
        bad_rows.extend([
            list(reversed(self.matches)),
            [self.matches[0], dict(self.matches[1], number=706)],
            [self.matches[0], dict(self.matches[1], id='ren')],
            [dict(self.matches[0], id=1), dict(self.matches[1], id='1')],
            [self.matches[0], None],
        ])
        for matches in bad_rows:
            with self.subTest(matches=repr(matches)[:150]):
                status, output, errors, calls = self.run_play(['РЕН'], [{'channels': matches}])
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertIn('invalid channel list', errors)
                self.choice.assert_not_called()

    def test_transport_unsupported_and_unstructured_search_errors_never_dispatch(self):
        for error in [ott.TransportError('lost reply'), ott.HTTPError(503),
                      ott.PlayerUnsupported('Unsupported request'), ott.PlayerRejected('Rejected', {}),
                      ott.Error('The request may have executed')]:
            with self.subTest(error=error):
                status, output, _, calls = self.run_play(['РЕН'], [error])
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.choice.assert_not_called()

    def test_dispatch_failure_keeps_full_list_and_never_repeats(self):
        for json_output in [False, True]:
            for error in [ott.TransportError('lost reply; do not repeat blindly'), ott.PlayerRejected('Rejected', {})]:
                with self.subTest(json_output=json_output, error=error):
                    status, output, errors, calls = self.run_play(
                        ['РЕН'], [{'channels': self.matches}, error], json_output=json_output)
                    self.assertEqual((status, len(calls)), (1, 2))
                    if json_output:
                        self.assertEqual(json.loads(output), {'channels': self.matches, 'playback': {'error': str(error)}})
                    else:
                        self.assertEqual(output, '706: РЕН ТВ\n1001: РЕН ТВ HD\n')
                    self.assertNotIn('Channel switch requested', errors)

    def test_mismatching_dispatch_acknowledgement_keeps_matches_without_claiming_success(self):
        replies = [None, [], {}, {'dispatched': False, 'channel': self.matches[-1]},
                   {'dispatched': 1, 'channel': self.matches[-1]}]
        for field, values in {'number': [True, 1001.0, '1001', 706],
                              'id': [None, True, 'different'], 'name': [None, 'Changed']}.items():
            for value in values:
                reply = self.acknowledgement()
                reply['channel'][field] = value
                replies.append(reply)
        for reply in replies:
            with self.subTest(reply=reply):
                status, output, errors, calls = self.run_play(
                    ['РЕН'], [{'channels': self.matches}, reply], json_output=True)
                self.assertEqual((status, len(calls)), (1, 2))
                self.assertEqual(json.loads(output)['channels'], self.matches)
                self.assertIn('did not confirm', errors)
                self.assertIn('may have executed; do not repeat', errors)
                self.assertNotIn('Channel switch requested:', errors)


class RejectionContractTest(unittest.TestCase):
    def call(self, status, data):
        client = object.__new__(ott.Client)
        client.timeout = 5
        client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), (200, {'id': 'a' * 32, 'status': status, 'data': data})])
        with mock.patch.object(ott.time, 'sleep'):
            return client.call('device', 'play', {'query': 'РЕН'})

    def test_only_explicit_rejection_exposes_structured_metadata(self):
        data = {'error': 'Several channels match', 'matches': [
            {'id': 'ren', 'number': 706, 'name': 'РЕН ТВ'},
            {'id': 'ren-hd', 'number': 1001, 'name': 'РЕН ТВ HD'}]}
        with self.assertRaises(ott.PlayerRejected) as caught:
            self.call('rejected', data)
        self.assertIs(caught.exception.data, data)
        self.assertIn('706: РЕН ТВ', str(caught.exception))
        for status in ['unsupported', 'pending', 'unknown']:
            with self.subTest(status=status), self.assertRaises(ott.Error) as caught:
                self.call(status, data)
            self.assertNotIsInstance(caught.exception, ott.PlayerRejected)

    def test_malformed_match_list_does_not_become_structured_rejection(self):
        for matches in [{}, None, [None], ['bad']]:
            with self.subTest(matches=matches), self.assertRaises(ott.Error) as caught:
                self.call('rejected', {'error': 'Rejected', 'matches': matches})
            self.assertNotIsInstance(caught.exception, ott.PlayerRejected)


if __name__ == '__main__':
    unittest.main()
