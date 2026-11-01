"""Compact guide searches preserve the complete candidate universe and deadline."""
import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'cli' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


search = module('compact_epg_test', 'epg_search.py')
history = module('compact_history_test', 'programme_search.py')
NOW = 1800000000
SETTINGS = {'url': 'https://guide.example/epg/v1', 'source': 'epg-one'}


class Error(Exception):
    pass


class EpgHTTPError(Error):
    def __init__(self, status, epg_code=None):
        self.status, self.epg_code = status, epg_code


def channel(number=1):
    return {'id': str(number), 'number': number, 'name': 'Channel ' + str(number),
            'tvgId': str(number), 'tvgName': '', 'shift': 0, 'archiveHours': 144}


def snapshot(count=1):
    return {'catalog': 'receipt', 'channels': [channel(number + 1) for number in range(count)],
            'archive': {'version': 1, 'revision': 'provider'}}


def row(ident='1', start=-100, end=100, title='Три кота'):
    return {'id': ident, 'start': NOW + start, 'end': NOW + end, 'title': title}


def response(batch, *, current=None, archive_current=None, archives=None, as_of=NOW):
    return {'version': 1, 'source': 'epg-one', 'generation': 'generation', 'fetchedAt': (NOW - 100) * 1000,
            'stale': False, 'asOf': as_of, 'validUntil': as_of + 60, 'checked': len(batch), 'total': len(batch),
            'complete': True, 'textProfile': search.TEXT_PROFILE,
            'current': current or [], 'archiveCurrent': archive_current or [], 'archives': archives or []}


class CompactSearchTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(patch.object(search.time, 'time', return_value=NOW))
        self.monotonic = self.stack.enter_context(patch.object(search.time, 'monotonic', return_value=10))
        self.history = SimpleNamespace(clean_rows=history.clean_rows, matches=history.matches,
                                       SearchError=history.SearchError,
                                       search_archives=Mock(return_value=({'programs': []}, {})))
        self.stack.enter_context(patch.object(search, '_history_module', return_value=self.history))
        self.epg = Mock()
        self.epg.search.side_effect = lambda batch, query, generation=None: response(batch)
        self.host = SimpleNamespace(Error=Error, EpgHTTPError=EpgHTTPError,
                                    EpgClient=Mock(return_value=self.epg), expired_epg_catalog=Mock())
        self.client = Mock()

    def run_search(self, data=None, query='три кота', **kwargs):
        return search.run(self.host, self.client, 'tv', SETTINGS, data or snapshot(), query, 55, **kwargs)

    def test_all_batches_are_validated_before_current_takes_global_priority(self):
        def answer(batch, query, generation=None):
            live = [row(batch[0]['id'])] if batch[0]['id'] == '257' else []
            return response(batch, current=live)
        self.epg.search.side_effect = answer
        result, targets = self.run_search(snapshot(300))
        self.assertEqual([len(call.args[0]) for call in self.epg.search.call_args_list], [256, 44])
        self.assertEqual([call.kwargs['generation'] for call in self.epg.search.call_args_list], [None, 'generation'])
        self.assertEqual((result['checked'], result['total'], result['partial']), (300, 300, False))
        self.assertEqual(result['programs'][0]['number'], 257)
        self.assertEqual(targets, {257: {'catalog': 'receipt', 'id': '257'}})
        self.history.search_archives.assert_not_called()
        self.client.call.assert_not_called()

    def test_later_invalid_batch_never_reaches_archive_or_player_probe(self):
        def answer(batch, query, generation=None):
            value = response(batch, current=[row('1')] if batch[0]['id'] == '1' else [])
            if batch[0]['id'] == '257':
                value['generation'] = 'replacement'
            return value
        self.epg.search.side_effect = answer
        with self.assertRaises(Error):
            self.run_search(snapshot(300))
        self.history.search_archives.assert_not_called()
        self.client.call.assert_not_called()

    def test_metadata_and_candidates_fail_closed(self):
        invalid = [
            {'complete': False}, {'stale': True}, {'version': True}, {'checked': True}, {'total': 0},
            {'source': 'other'}, {'generation': ''}, {'generation': 'x' * 81}, {'fetchedAt': NOW - 1},
            {'fetchedAt': (NOW - 7200) * 1000}, {'asOf': NOW - 61}, {'asOf': float(NOW)},
            {'validUntil': NOW}, {'validUntil': NOW + 7201}, {'textProfile': 'unrecognised'},
            {'current': [row('foreign')]}, {'current': [row(title='Other')]},
            {'current': [row(end=0)]}, {'current': [dict(row(), start=True)]},
            {'current': [row(title='\ud800')]}, {'current': [row(title='Три кота' + 'x' * 16384)]},
            {'current': [row(), row()]},
            {'archiveCurrent': [row(title='Три\x00 кота')]},
            {'archiveCurrent': [row(title='Три  кота')]},
            {'archiveCurrent': [row(), row()]},
            {'archives': [{'id': '1', 'rows': []}]},
            {'archives': [{'id': '1', 'rows': [row(end=1)]}]},
            {'archives': [{'id': '1', 'rows': [row(start=-144 * 3600 - 1, end=-100)]}]},
            {'archives': [{'id': '1', 'rows': [row(start=-300, end=-200), row(start=-100, end=-1)]}]},
        ]
        for change in invalid:
            with self.subTest(change=change):
                self.epg.search.side_effect = lambda batch, query, generation=None: dict(response(batch), **change)
                with self.assertRaises(Error):
                    self.run_search()
        self.history.search_archives.assert_not_called()
        self.client.call.assert_not_called()

    def test_archive_preparation_preserves_overlap_full_chain_and_options(self):
        live = [row(start=-90, title='Кота — три'), row(start=-80, title='Три: кота')]
        chains = [{'id': '1', 'rows': [row(start=-400, end=-300), row(start=-300, end=-200)]},
                  {'id': '1', 'rows': [row(start=-400, end=-250)]}]
        self.epg.search.side_effect = lambda batch, query, generation=None: response(
            batch, archive_current=live, archives=chains)
        progress = Mock()
        self.run_search(fast=True, refresh=True, progress=progress)
        call = self.history.search_archives.call_args
        self.assertEqual(call.args, (self.client, 'tv', SETTINGS, snapshot(), 'три кота', True))
        self.assertTrue(call.kwargs['fast'])
        self.assertEqual(call.kwargs['deadline'], 55)
        self.assertIs(call.kwargs['progress'], progress)
        prepared = call.kwargs['prepared']
        self.assertEqual(len(prepared['current']), 2)
        self.assertEqual([len(rows) for _, rows in prepared['chains']], [2, 1])
        self.assertEqual(prepared['chains'][0][1], [
            {'time': NOW - 400, 'time_to': NOW - 300, 'name': 'Три кота'},
            {'time': NOW - 300, 'time_to': NOW - 200, 'name': 'Три кота'}])
        # Reuse the exact legacy validator, without any archive network request.
        self.assertEqual(history._prepared_candidates(prepared, snapshot()['channels'], 'три кота')[2], NOW + 60)

    def test_only_verified_matching_timeout_splits_complete_input(self):
        def answer(batch, query, generation=None):
            if len(batch) > 128:
                raise EpgHTTPError(504, 'EPG_TIMEOUT')
            return response(batch)
        self.epg.search.side_effect = answer
        self.run_search(snapshot(256))
        self.assertEqual([len(call.args[0]) for call in self.epg.search.call_args_list], [256, 128, 128])
        successful = [item['id'] for call in self.epg.search.call_args_list[1:] for item in call.args[0]]
        self.assertEqual(successful, [str(number) for number in range(1, 257)])
        for code in (None, 'EPG_RELAY'):
            self.epg.search.reset_mock()
            self.epg.search.side_effect = EpgHTTPError(504, code)
            with self.assertRaises(EpgHTTPError):
                self.run_search(snapshot(256))
            self.assertEqual(self.epg.search.call_count, 1)

    def test_matching_timeout_split_budget_is_global(self):
        self.epg.search.side_effect = EpgHTTPError(504, 'EPG_TIMEOUT')
        with self.assertRaises(EpgHTTPError):
            self.run_search(snapshot(256))
        self.assertEqual([len(call.args[0]) for call in self.epg.search.call_args_list], [256, 128, 64, 32, 16])
        self.history.search_archives.assert_not_called()

    def test_total_deadline_is_not_reset_between_batches(self):
        def answer(batch, query, generation=None):
            self.monotonic.return_value = 56
            return response(batch)
        self.epg.search.side_effect = answer
        with self.assertRaisesRegex(Error, 'timed out'):
            self.run_search(snapshot(300))
        self.assertEqual(self.epg.search.call_count, 1)
        self.history.search_archives.assert_not_called()

    def test_global_budget_includes_every_candidate_type_and_batch(self):
        def answer(batch, query, generation=None):
            items = [row(item['id']) for item in batch]
            return response(batch, current=items, archive_current=items)
        self.epg.search.side_effect = answer
        with patch.object(search, 'MAX_ROWS', 550), self.assertRaisesRegex(Error, 'limit'):
            self.run_search(snapshot(300))
        self.assertEqual(self.epg.search.call_count, 2)
        self.history.search_archives.assert_not_called()
        with patch.object(search, 'MAX_BYTES', 1), self.assertRaisesRegex(Error, 'limit'):
            self.run_search()

    def test_late_current_reuses_then_refreshes_same_generation(self):
        def answer(batch, query, generation=None):
            now = int(self.clock.return_value)
            item = dict(row(title='Кота — три'), start=now - 1, end=now + 100)
            return response(batch, archive_current=[item], as_of=now)
        self.epg.search.side_effect = answer
        self.run_search(generation='generation')
        refresh = self.history.search_archives.call_args.kwargs['refresh_current']
        self.assertEqual(refresh()[0][1]['time'], NOW - 1)
        self.assertEqual(self.epg.search.call_count, 1)
        self.clock.return_value = NOW + 61
        self.assertEqual(refresh()[0][1]['time'], NOW + 60)
        self.assertEqual(self.epg.search.call_count, 2)
        self.assertEqual(self.epg.search.call_args.kwargs['generation'], 'generation')

    def test_expired_window_restarts_whole_catalogue_once(self):
        def answer(batch, query, generation=None):
            if self.epg.search.call_count == 1:
                return dict(response(batch, as_of=NOW - 2), validUntil=NOW - 1)
            return response(batch)
        self.epg.search.side_effect = answer
        self.run_search()
        self.assertEqual(self.epg.search.call_count, 2)
        self.assertEqual(self.epg.search.call_args.kwargs['generation'], 'generation')
        self.epg.search.side_effect = lambda batch, query, generation=None: dict(
            response(batch, as_of=NOW - 2), validUntil=NOW - 1)
        self.epg.search.reset_mock()
        with self.assertRaisesRegex(Error, 'window changed'):
            self.run_search()
        self.assertEqual(self.epg.search.call_count, 2)

    def test_only_public_metadata_is_transmitted(self):
        data = snapshot()
        data['channels'][0]['url'] = 'https://private.example/secret'
        self.run_search(data)
        sent = self.epg.search.call_args.args[0][0]
        self.assertEqual(set(sent), {'id', 'tvgId', 'tvgName', 'name', 'shift', 'archiveHours'})
        self.host.EpgClient.assert_called_once_with(SETTINGS, 45, operation='search')

    def test_literal_matching_retains_accents_spacing_and_dotless_i(self):
        self.assertEqual(search._literal_key('Straße ẞ ςσΣ'), search._literal_key('STRASSE SS ΣΣΣ'))
        for left, right in [('ı', 'i'), ('ё', 'е'), ('a  b', 'a b')]:
            self.assertNotEqual(search._literal_key(left), search._literal_key(right))

    def test_empty_catalogue_does_not_contact_service(self):
        result, targets = self.run_search(snapshot(0))
        self.assertEqual(result['programs'], [])
        self.assertEqual(targets, {})
        self.host.EpgClient.assert_not_called()

    def test_archive_errors_are_not_promoted_to_guide_availability(self):
        self.history.search_archives.side_effect = history.ArchiveSourceError('Archive media unavailable')
        with self.assertRaisesRegex(Error, 'Archive media unavailable') as caught:
            self.run_search()
        self.assertNotIsInstance(caught.exception, EpgHTTPError)


class GuideAvailabilityTest(unittest.TestCase):
    def test_only_safe_guide_failures_receive_availability_type(self):
        for status in (400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                opener = Mock()
                opener.open.side_effect = HTTPError('https://guide.example', status, 'private error', {}, io.BytesIO())
                with patch.object(history, 'build_opener', return_value=opener), self.assertRaises(history.SearchError) as caught:
                    history.epg_request('https://guide.example', '/programs')
                expected = status in (429, 500, 502, 503, 504)
                self.assertEqual(isinstance(caught.exception, history.EpgAvailabilityError), expected)
                if expected:
                    self.assertEqual(caught.exception.status, status)
                    self.assertFalse(caught.exception.transport)
                self.assertNotIn('private error', str(caught.exception))

    def test_transport_failure_is_typed_but_invalid_data_is_not(self):
        opener = Mock()
        opener.open.side_effect = OSError('private address')
        with patch.object(history, 'build_opener', return_value=opener), self.assertRaises(history.EpgAvailabilityError) as caught:
            history.epg_request('https://guide.example', '/programs')
        self.assertTrue(caught.exception.transport)
        self.assertIsNone(caught.exception.status)
        opener.open.side_effect = None
        for raw in (b'not json', b'{"version":1,"source":"epg-one","generation":"x","stale":true}'):
            opener.open.return_value = io.BytesIO(raw)
            with patch.object(history, 'build_opener', return_value=opener), self.assertRaises(history.SearchError) as caught:
                history.epg_request('https://guide.example', '/programs')
            self.assertNotIsInstance(caught.exception, history.EpgAvailabilityError)


if __name__ == '__main__':
    unittest.main()
