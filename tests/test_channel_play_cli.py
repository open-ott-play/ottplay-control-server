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

    def rejected(self, matches=None):
        return ott.PlayerRejected('Several channels match. Use a channel number.',
                                  {'matches': self.matches if matches is None else matches})

    def acknowledgement(self, row=None):
        return {'dispatched': True, 'channel': copy.deepcopy(row or self.matches[0])}

    def run_play(self, words, replies, json_output=False):
        client = mock.Mock()
        client.device.return_value = 'dev_iphone'
        client.call.side_effect = replies
        stdout, stderr = io.StringIO(), io.StringIO()
        args = (['--json'] if json_output else []) + ['iphone'] + words
        with mock.patch.object(ott, 'Client', return_value=client), mock.patch.object(ott, 'read_json', return_value={}):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = ott.main(args)
        return status, stdout.getvalue(), stderr.getvalue(), client.call.call_args_list

    def test_original_cyrillic_example_plays_first_catalogue_match_once(self):
        for words in [['РЕН'], ['рЕн'], ['play', ' РЕН ']]:
            with self.subTest(words=words):
                status, output, errors, calls = self.run_play(words, [self.rejected(), self.acknowledgement()])
                self.assertEqual(status, 0)
                self.assertEqual(output, 'Channel switch requested: 706: РЕН ТВ\n')
                self.assertIn('Multiple channels match; requesting the first in catalogue order: 706: РЕН ТВ', errors)
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[-1], mock.call('dev_iphone', 'play', {'query': '706'}))

    def test_successful_exact_unique_and_numeric_queries_remain_one_request(self):
        for words in [['РЕН', 'ТВ'], ['unique'], ['706']]:
            with self.subTest(words=words):
                reply = self.acknowledgement()
                status, output, errors, calls = self.run_play(words, [reply], json_output=True)
                self.assertEqual((status, errors), (0, ''))
                self.assertEqual(json.loads(output), reply)
                self.assertEqual(calls, [mock.call('dev_iphone', 'play', {'query': ' '.join(words)})])

    def test_fallback_json_is_unchanged_and_warning_stays_on_stderr(self):
        reply = dict(self.acknowledgement(), extra='preserved')
        status, output, errors, calls = self.run_play(['РЕН'], [self.rejected(), reply], json_output=True)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), reply)
        self.assertIn('Multiple channels match', errors)
        self.assertEqual(len(calls), 2)

    def test_unicode_casefold_duplicate_exact_names_and_numeric_ids(self):
        rows = [{'id': 15, 'number': 2, 'name': 'Straße'}, {'id': 16, 'number': 10, 'name': 'STRASSE'}]
        reply = self.acknowledgement(rows[0])
        reply['channel']['id'] = '15'
        status, _, errors, calls = self.run_play(['STRASSE'], [self.rejected(rows), reply])
        self.assertEqual(status, 0, errors)
        self.assertEqual(calls[-1], mock.call('dev_iphone', 'play', {'query': '2'}))

    def test_numeric_empty_no_match_and_single_match_rejections_do_not_dispatch(self):
        for query in ['706', ' 706 ', '+706', '-706', '7.06', '7e2', '７０６', '']:
            with self.subTest(query=query):
                status, output, _, calls = self.run_play(['play', query], [self.rejected()])
                self.assertEqual((status, output, len(calls)), (1, '', 1))
        for matches in [[], self.matches[:1], None, {}, 'invalid']:
            with self.subTest(matches=matches):
                rejection = ott.PlayerRejected('Rejected', {'matches': matches})
                status, output, _, calls = self.run_play(['РЕН'], [rejection])
                self.assertEqual((status, output, len(calls)), (1, '', 1))

    def test_invalid_match_metadata_order_and_identity_never_dispatch(self):
        bad_rows = []
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
            [self.matches[0], dict(self.matches[1], name='РЕН')],
        ])
        for matches in bad_rows:
            with self.subTest(matches=matches):
                status, output, errors, calls = self.run_play(['РЕН'], [self.rejected(matches)])
                self.assertEqual((status, output, len(calls)), (1, '', 1))
                self.assertIn('invalid ambiguous channel list', errors)
                self.assertNotIn('requesting the first', errors)

    def test_transport_unsupported_and_unstructured_errors_never_dispatch(self):
        for error in [ott.TransportError('lost reply'), ott.HTTPError(503),
                      ott.Error('Unsupported request'), ott.Error('The request may have executed'),
                      ott.Error('Several channels match. Use a channel number.')]:
            with self.subTest(error=error):
                status, output, _, calls = self.run_play(['РЕН'], [error])
                self.assertEqual((status, output, len(calls)), (1, '', 1))

    def test_second_dispatch_failure_never_repeats(self):
        for error in [ott.TransportError('lost reply; do not repeat blindly'), self.rejected()]:
            with self.subTest(error=error):
                status, output, _, calls = self.run_play(['РЕН'], [self.rejected(), error])
                self.assertEqual((status, output, len(calls)), (1, '', 2))

    def test_mismatching_dispatch_acknowledgement_never_claims_success(self):
        replies = [None, [], {}, {'dispatched': False, 'channel': self.matches[0]},
                   {'dispatched': 1, 'channel': self.matches[0]}]
        for field, values in {'number': [True, 706.0, '706', 1001],
                              'id': [None, True, 'different'], 'name': [None, 'Changed']}.items():
            for value in values:
                reply = self.acknowledgement()
                reply['channel'][field] = value
                replies.append(reply)
        for reply in replies:
            with self.subTest(reply=reply):
                status, output, errors, calls = self.run_play(['РЕН'], [self.rejected(), reply], json_output=True)
                self.assertEqual((status, output, len(calls)), (1, '', 2))
                self.assertIn('did not confirm', errors)
                self.assertIn('may have executed; do not repeat', errors)
                self.assertNotIn('Channel switch requested:', errors)


class RejectionContractTest(unittest.TestCase):
    def call(self, status, data):
        client = object.__new__(ott.Client)
        client.timeout = 5
        client.api = mock.Mock(side_effect=[(202, {'id': 'a' * 32}), (200, {'status': status, 'data': data})])
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
