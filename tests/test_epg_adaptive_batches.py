"""Adaptive EPG reads retain complete, generation-bound catalogue coverage."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'adaptive_epg_ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

NOW = 1790737200
SETTINGS = {'url': 'https://epg.example/epg/v1', 'source': 'epg-one'}


def catalogue(count):
    return {'catalog': 'owned-catalog', 'channels': [
        {'id': str(index), 'number': index + 1, 'name': 'Channel ' + str(index),
         'tvgId': '', 'tvgName': '', 'shift': 0} for index in range(count)]}


def response(rows):
    return {'version': 1, 'source': 'epg-one', 'generation': 'generation-1',
            'fetchedAt': NOW * 1000 - 1000, 'asOf': NOW, 'stale': False,
            'checked': len(rows), 'total': len(rows), 'programs': [
                {'id': row['id'], 'title': 'Три кота', 'start': NOW - 60, 'end': NOW + 60}
                for row in rows]}


class AdaptiveEpgTest(unittest.TestCase):
    def configured(self, count, current, timeout=45):
        client, epg = mock.Mock(timeout=timeout), mock.Mock()
        client.call.return_value = catalogue(count)
        client.device.return_value = 'tv'
        epg.current.side_effect = current
        return client, epg

    def search(self, client, epg):
        with mock.patch.object(ott, 'EpgClient', return_value=epg), \
                mock.patch.object(ott.time, 'time', return_value=NOW):
            return ott.server_programs(client, 'tv', SETTINGS, 'Три кота')

    def assert_read_only(self, client):
        self.assertEqual(client.call.call_args_list, [mock.call('tv', 'epg_catalog', {})])

    def test_matching_timeout_splits_batch_without_losing_or_duplicating_coverage(self):
        successful = []
        def current(rows, query):
            self.assertEqual(query, 'Три кота')
            if len(rows) == 256:
                raise ott.EpgHTTPError(504, 'EPG_TIMEOUT')
            successful.extend(row['id'] for row in rows)
            return response(rows)
        client, epg = self.configured(300, current)
        result, targets = self.search(client, epg)
        self.assertEqual([len(call.args[0]) for call in epg.current.call_args_list], [256, 128, 128, 44])
        self.assertEqual(successful, [str(index) for index in range(300)])
        self.assertEqual([row['number'] for row in result['programs']], list(range(1, 301)))
        self.assertEqual((result['checked'], result['total'], result['partial']), (300, 300, False))
        self.assertEqual(targets, {index + 1: {'catalog': 'owned-catalog', 'id': str(index)}
                                  for index in range(300)})
        self.assert_read_only(client)

    def test_invalid_child_cannot_reach_playback_after_an_accepted_child(self):
        for defect in ('generation', 'foreign_id', 'partial', 'order'):
            with self.subTest(defect=defect):
                def current(rows, query):
                    if len(rows) == 256:
                        raise ott.EpgHTTPError(504, 'EPG_TIMEOUT')
                    value = response(rows)
                    if rows[0]['id'] == '128':
                        if defect == 'generation':
                            value['generation'] = 'generation-2'
                        elif defect == 'foreign_id':
                            value['programs'][0]['id'] = '0'
                        elif defect == 'partial':
                            value['checked'] -= 1
                        else:
                            value['programs'].reverse()
                    return value
                client, epg = self.configured(300, current)
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(ott, 'read_json', return_value={'epg': SETTINGS}), \
                        mock.patch.object(ott, 'Client', return_value=client), \
                        mock.patch.object(ott, 'EpgClient', return_value=epg), \
                        mock.patch.object(ott.time, 'time', return_value=NOW), \
                        mock.patch.object(ott.secrets, 'choice') as choice, \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = ott.main(['--json', 'tv', 'p', 'Три кота'])
                self.assertEqual(status, 1)
                self.assertEqual(output.getvalue(), '')
                self.assertIn('Error:', errors.getvalue())
                self.assertEqual(epg.current.call_count, 3)
                self.assert_read_only(client)
                choice.assert_not_called()

    def test_split_reads_share_original_deadline_and_stop_without_extra_calls(self):
        for timeout, expected_sizes, expected_budgets in ((8, [256], [8]),
                                                        (9, [256, 128], [9, 1])):
            with self.subTest(timeout=timeout):
                clock, budgets = [0], []
                def current(rows, query):
                    budgets.append(epg.timeout)
                    if len(rows) == 256:
                        clock[0] += 8
                        raise ott.EpgHTTPError(504, 'EPG_TIMEOUT')
                    clock[0] += 1
                    return response(rows)
                client, epg = self.configured(300, current, timeout=timeout)
                with mock.patch.object(ott.time, 'monotonic', side_effect=lambda: clock[0]), \
                        self.assertRaisesRegex(ott.Error, 'timed out'):
                    self.search(client, epg)
                self.assertEqual([len(call.args[0]) for call in epg.current.call_args_list], expected_sizes)
                self.assertEqual(budgets, expected_budgets)
                self.assert_read_only(client)

    def test_gateway_untyped_and_other_errors_do_not_split_or_retry(self):
        for failure in (ott.EpgHTTPError(504, 'EPG_RELAY'), ott.EpgHTTPError(504),
                        ott.EpgHTTPError(503, 'EPG_NOT_READY'), ott.EpgHTTPError(500),
                        ott.EpgHTTPError(503, 'EPG_TIMEOUT'), ott.Error('EPG service timed out')):
            with self.subTest(failure=str(failure)):
                client, epg = self.configured(300, failure)
                with self.assertRaises(ott.Error) as raised:
                    self.search(client, epg)
                self.assertIs(raised.exception, failure)
                self.assertEqual(epg.current.call_count, 1)
                self.assert_read_only(client)

    def test_immediate_matching_errors_have_finite_split_and_singleton_limits(self):
        for count, expected_sizes in ((300, [256, 128, 64, 32, 16]), (1, [1])):
            with self.subTest(count=count):
                client, epg = self.configured(count, ott.EpgHTTPError(504, 'EPG_TIMEOUT'))
                with self.assertRaises(ott.EpgHTTPError):
                    self.search(client, epg)
                self.assertEqual([len(call.args[0]) for call in epg.current.call_args_list], expected_sizes)
                self.assert_read_only(client)

    def test_split_limit_is_shared_across_original_batches(self):
        def current(rows, query):
            if len(rows) == 256:
                raise ott.EpgHTTPError(504, 'EPG_TIMEOUT')
            return response(rows)
        client, epg = self.configured(1280, current)
        with self.assertRaises(ott.EpgHTTPError):
            self.search(client, epg)
        self.assertEqual([len(call.args[0]) for call in epg.current.call_args_list],
                         [256, 128, 128] * 4 + [256])
        self.assert_read_only(client)

    def test_ten_thousand_healthy_channels_use_forty_complete_reads(self):
        client, epg = self.configured(10000, lambda rows, query: response(rows))
        result, targets = self.search(client, epg)
        calls = epg.current.call_args_list
        self.assertEqual([len(call.args[0]) for call in calls], [256] * 39 + [16])
        self.assertEqual([row['id'] for call in calls for row in call.args[0]],
                         [str(index) for index in range(10000)])
        self.assertEqual((result['checked'], result['total'], result['partial']), (10000, 10000, False))
        self.assertEqual(len(result['programs']), 10000)
        self.assertEqual(len(targets), 10000)
        self.assert_read_only(client)


if __name__ == '__main__':
    unittest.main()
