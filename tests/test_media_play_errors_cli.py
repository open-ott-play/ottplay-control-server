"""Catalog playback refusals retain safe causes without relaxing read receipts."""
import copy
import unittest
from unittest import mock

from test_categories_cli import capabilities, category_page, channel_page, ott


class MediaPlayErrorsTest(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'fixture-ren-uhd', 'number': 115, 'name': 'РЕН ТВ UHD'}
        self.client = mock.Mock(timeout=45)
        self.progress = mock.Mock()
        clock = mock.patch.object(ott.time, 'monotonic', return_value=0)
        clock.start()
        self.addCleanup(clock.stop)

    def search(self, failure, rows=None, query=None, controls=None):
        self.client.call.reset_mock()
        self.client.call.side_effect = [controls or capabilities(), category_page(),
                                      channel_page(rows if rows is not None else [self.row]), failure]
        return ott.play_channel(self.client, 'fixture-lg', {'query': query or self.row['name']}, 45,
                                progress=self.progress)

    def assert_single_bound_play(self):
        self.assertEqual([call.args[1] for call in self.client.call.call_args_list],
                         ['capabilities', 'categories', 'channels', 'play_catalog'])
        self.assertEqual(self.client.call.call_args,
                         mock.call('fixture-lg', 'play_catalog',
                                   {'catalog': 'category-one', 'id': self.row['id']}))
        self.assertEqual(self.client.timeout, 45)
        self.progress.assert_not_called()

    def test_native_play_codes_have_distinct_safe_diagnostics_and_no_replay(self):
        phrases = {
            'invalid_request': 'could not accept the catalog playback request',
            'catalog_changed': 'playback catalog receipt is no longer current',
            'catalog_incomplete': 'playback catalog is incomplete',
            'unavailable': 'playback catalog or selected media is unavailable',
        }
        errors = set()
        for reason, phrase in phrases.items():
            with self.subTest(reason=reason):
                result, error = self.search(ott.PlayerRejected('private-native-message', {'error': reason}))
                self.assertIsInstance(error, ott.Error)
                self.assertNotIsInstance(error, ott.MediaCatalogChanged)
                self.assertIn(phrase, str(error))
                self.assertIn('The request was not repeated.', str(error))
                self.assertNotIn('private-native-message', str(error))
                self.assertEqual(result['channels'], [self.row])
                self.assertEqual(result['playback'], {'error': str(error)})
                errors.add(str(error))
                self.assert_single_bound_play()
        self.assertEqual(len(errors), len(phrases))

    def test_unknown_and_richer_play_errors_stay_generic_and_private(self):
        secret = 'https://private.example/stream?token=fixture-secret'
        rejected = [None, [], {}, {'error': None}, {'error': True}, {'error': ['catalog_changed']},
                    {'error': secret}, {'error': 'catalog_changed\n' + secret},
                    {'error': 'catalog_changed', 'private': secret},
                    {'error': 'restricted'}, {'error': 'runtime_mismatch'},
                    {'version': 1, 'runtime': None, 'op': 'play_catalog', 'state': 'error',
                     'error': 'catalog_changed'}]
        for payload in rejected:
            with self.subTest(payload=payload):
                result, error = self.search(ott.PlayerRejected(secret, payload))
                self.assertEqual(str(error), ott.MEDIA_PLAY_REJECTED + ' The request was not repeated.')
                self.assertNotIn(secret, str(result))
                self.assertNotIn('may have changed', str(error))
                self.assertNotIn('catalog_changed', str(error))
                self.assert_single_bound_play()

    def test_valid_read_error_keeps_its_runtime_bound_stale_signal(self):
        params = {'runtime': 'page-one', 'catalog': 'source-one', 'category': 'all', 'offset': 0, 'limit': 1}
        data = {'version': 1, 'runtime': 'page-one', 'op': 'channels', 'state': 'error',
                'error': 'catalog_changed'}
        self.client.call.side_effect = ott.PlayerRejected('private response', data)
        with self.assertRaises(ott.MediaCatalogChanged):
            ott.media_request(self.client, 'fixture-lg', 'channels', params, 45)
        self.client.call.assert_called_once_with('fixture-lg', 'channels', params)

    def test_bare_or_mismatched_read_errors_cannot_enable_stale_retry(self):
        params = {'runtime': 'page-one', 'catalog': 'source-one', 'category': 'all', 'offset': 0, 'limit': 1}
        data = {'version': 1, 'runtime': 'page-one', 'op': 'channels', 'state': 'error',
                'error': 'catalog_changed'}
        for payload in ({'error': 'catalog_changed'}, dict(data, runtime='other'),
                        dict(data, op='play_catalog'), dict(data, version=True),
                        dict(data, private='fixture-secret')):
            with self.subTest(payload=payload):
                self.client.call.reset_mock()
                self.client.call.side_effect = ott.PlayerRejected('fixture-secret', payload)
                with self.assertRaises(ott.Error) as caught:
                    ott.media_request(self.client, 'fixture-lg', 'channels', params, 45)
                self.assertNotIsInstance(caught.exception, ott.MediaCatalogChanged)
                self.assertNotIn('fixture-secret', str(caught.exception))
                self.client.call.assert_called_once_with('fixture-lg', 'channels', params)

    def test_unknown_request_outcome_is_not_reclassified_as_rejection(self):
        failure = ott.RequestError('request_pending_timeout', 'receipt',
                                   'The request may still execute; do not repeat blindly.', 'a' * 32)
        result, error = self.search(failure)
        self.assertIs(error, failure)
        self.assertEqual(error.reason, 'request_pending_timeout')
        self.assertEqual(error.request_id, 'a' * 32)
        self.assertNotIn('was rejected', result['playback']['error'])
        self.assert_single_bound_play()

    def test_exact_play_success_keeps_the_same_bound_target(self):
        result, error = self.search({'dispatched': True, 'channel': copy.deepcopy(self.row)})
        self.assertIsNone(error)
        self.assertEqual(result['playback'], {'dispatched': True, 'channel': self.row})
        self.assert_single_bound_play()

    def test_duplicate_exact_names_without_random_support_choose_first_catalog_row(self):
        controls = capabilities()
        controls.pop('random')
        rows = [dict(self.row, id='fixture-ren-978', number=978, name='РЕН ТВ HD'),
                dict(self.row, id='fixture-ren-1', number=1, name='РЕН ТВ HD')]
        self.row = rows[0]
        result, error = self.search({'dispatched': True, 'channel': self.row}, rows=rows, controls=controls)
        self.assertIsNone(error)
        self.assertEqual(result['channels'], rows)
        self.assertEqual(result['playback']['channel'], rows[0])
        self.assert_single_bound_play()

    def test_normalized_exact_name_precedes_partials_in_catalog_order(self):
        for query, names, selected in [
                ('STRASSE', ['Straße HD', 'Straße', 'STRASSE'], 1),
                ('CAFÉ', ['Cafe\u0301 HD', 'Cafe\u0301', 'CAFÉ UHD'], 1)]:
            with self.subTest(query=query):
                rows = [{'id': f'fixture-{i}', 'number': number, 'name': name}
                        for i, (number, name) in enumerate(zip([978, 115, 1], names))]
                self.row = rows[selected]
                controls = capabilities()
                controls.pop('random')
                result, error = self.search({'dispatched': True, 'channel': self.row}, rows=rows,
                                            query=query, controls=controls)
                self.assertIsNone(error)
                self.assertEqual(result['playback']['channel'], self.row)
                self.assert_single_bound_play()

    def test_duplicate_exact_failure_or_unknown_outcome_never_tries_another_match(self):
        rows = [dict(self.row, id='fixture-first', number=978), dict(self.row, number=1)]
        self.row = rows[0]
        pending = ott.RequestError('request_pending_timeout', 'receipt', 'Outcome unknown', 'b' * 32)
        for reply in (ott.PlayerRejected('private-native-message', {'error': 'catalog_changed'}),
                      pending, {'dispatched': True, 'channel': rows[1]}):
            with self.subTest(reply=reply):
                result, error = self.search(reply, rows=rows)
                self.assertIsInstance(error, ott.Error)
                self.assertNotIsInstance(error, ott.MediaCatalogChanged)
                self.assertNotIn('private-native-message', str(result))
                if reply is pending:
                    self.assertIs(error, pending)
                self.assert_single_bound_play()

    def test_partial_matches_still_require_safe_random_even_for_one_match(self):
        controls = capabilities()
        controls.pop('random')
        rows = [self.row, dict(self.row, id='fixture-second', number=978)]
        for matches in (rows[:1], rows):
            with self.subTest(matches=len(matches)):
                self.client.call.reset_mock()
                self.client.call.side_effect = [controls, category_page(), channel_page(matches), controls]
                result, error = ott.play_channel(self.client, 'fixture-lg', {'query': 'РЕН'}, 45)
                self.assertEqual(result['channels'], matches)
                self.assertIn('Safe random selection is not supported', str(error))
                self.assertIn('no playback was requested', str(error))
                self.assertEqual([call.args[1] for call in self.client.call.call_args_list],
                                 ['capabilities', 'categories', 'channels', 'capabilities'])


if __name__ == '__main__':
    unittest.main()
