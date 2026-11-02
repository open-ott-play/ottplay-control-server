"""One search can change guide origin, never its deadline or partial results."""
import contextlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    'fallback_integration_ott', Path(__file__).resolve().parents[1] / 'cli/ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)

NOW = 1800000000
SETTINGS = {'url': 'https://guide.example/epg/v1', 'source': 'epg-one',
            'fallback_url': 'http://127.0.0.1:18792/epg/v1'}


def catalogue(prefix='remote', count=300):
    return {'catalog': prefix + '-receipt', 'channels': [
        {'id': prefix + str(index), 'number': index + 1, 'name': prefix + ' channel ' + str(index),
         'tvgId': str(index), 'tvgName': '', 'shift': 0, 'archiveHours': 144}
        for index in range(count)], 'archive': {'version': 1, 'revision': prefix + '-revision'}}


def response(rows, generation='remote-generation', compact=False):
    programs = [{'id': row['id'], 'start': NOW - 100, 'end': NOW + 100, 'title': 'Cats'} for row in rows]
    value = {'version': 1, 'source': 'epg-one', 'generation': generation,
             'fetchedAt': (NOW - 100) * 1000, 'asOf': NOW, 'stale': False,
             'checked': len(rows), 'total': len(rows), 'programs': programs}
    if compact:
        value.pop('programs')
        value.update(complete=True, current=programs, archiveCurrent=[], archives=[], validUntil=NOW + 60,
                     textProfile='archive-nfkc-casefold-words-u17-v1')
    return value


class FallbackIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = [0]
        self.stack.enter_context(patch.object(ott.time, 'time', return_value=NOW))
        self.stack.enter_context(patch.object(ott.time, 'monotonic', side_effect=lambda: self.clock[0]))
        self.client = Mock(timeout=45)
        self.client.call.side_effect = [catalogue(), catalogue('local')]
        self.primary, self.local = Mock(), Mock()
        self.primary.current.side_effect = lambda rows, query: response(rows)
        self.local.current.side_effect = lambda rows, query: response(rows, 'local-generation')
        self.primary.search.side_effect = lambda rows, query, generation=None: response(rows, compact=True)
        self.local.search.side_effect = lambda rows, query, generation=None: response(rows, 'local-generation', True)
        self.factory = self.stack.enter_context(patch.object(ott, 'EpgClient', side_effect=self.create_epg))

    def create_epg(self, settings, timeout, operation='current'):
        self.assertEqual(set(settings), {'url', 'source'})
        epg = self.local if settings['url'].startswith('http://127.') else self.primary
        epg.timeout = min(10, timeout)
        return epg

    def run_search(self, **kwargs):
        return ott.server_programs(self.client, 'tv', SETTINGS, 'Cats', **kwargs)

    def assert_read_only(self, count=2):
        self.assertEqual(self.client.call.call_args_list, [unittest.mock.call('tv', 'epg_catalog', {})] * count)
        self.assertEqual(self.client.timeout, 45)

    def assert_local_only(self, result, targets):
        self.assertEqual(len(result['programs']), 300)
        self.assertEqual((result['checked'], result['total'], result['partial']), (300, 300, False))
        self.assertEqual(set(targets), set(range(1, 301)))
        self.assertTrue(all(row['channel'].startswith('local ') for row in result['programs']))
        self.assertTrue(all(target['catalog'] == 'local-receipt' and target['id'].startswith('local')
                            for target in targets.values()))

    def test_mid_batch_failover_discards_primary_results_and_reads_fresh_catalogue(self):
        def primary(rows, query):
            if rows[0]['id'] == 'remote256':
                raise ott.EpgHTTPError(503, 'EPG_NOT_READY')
            return response(rows)
        self.primary.current.side_effect = primary
        result, targets = self.run_search()
        self.assert_local_only(result, targets)
        self.assertEqual([len(call.args[0]) for call in self.primary.current.call_args_list], [256, 44])
        self.assertEqual([len(call.args[0]) for call in self.local.current.call_args_list], [256, 44])
        self.assert_read_only()

    @patch.object(ott.unicodedata, 'unidata_version', '17.0.0')
    def test_compact_mid_batch_failover_restarts_discovery_and_pins_new_generation(self):
        self.primary.current.side_effect = lambda rows, query: dict(response(rows), searchVersion=1)
        self.local.current.side_effect = lambda rows, query: dict(response(rows, 'local-generation'), searchVersion=1)
        def primary(rows, query, generation=None):
            if rows[0]['id'] == 'remote256':
                raise ott.EpgTransportError('Guide connection unavailable')
            return response(rows, compact=True)
        self.primary.search.side_effect = primary
        result, targets = self.run_search(fast=True, refresh=True)
        self.assert_local_only(result, targets)
        self.assertEqual(self.local.current.call_count, 1)
        self.assertEqual([call.kwargs['generation'] for call in self.primary.search.call_args_list],
                         ['remote-generation'] * 2)
        self.assertEqual([call.kwargs['generation'] for call in self.local.search.call_args_list],
                         ['local-generation'] * 2)
        self.assert_read_only()

    def test_mismatched_unicode_keeps_complete_current_lookup_across_fallback(self):
        for version in ('14.0.0', '16.0.0', '18.0.0'):
            with self.subTest(version=version), patch.object(ott.unicodedata, 'unidata_version', version):
                self.client.call.reset_mock(side_effect=True)
                self.client.call.side_effect = [catalogue(), catalogue('local')]
                self.primary.current.reset_mock()
                self.local.current.reset_mock()
                def primary(rows, query):
                    if rows[0]['id'] == 'remote256':
                        raise ott.EpgHTTPError(503, 'EPG_NOT_READY')
                    return dict(response(rows), searchVersion=1)
                self.primary.current.side_effect = primary
                self.local.current.side_effect = lambda rows, query: dict(
                    response(rows, 'local-generation'), searchVersion=1)
                progress = Mock()
                result, targets = self.run_search(progress=progress)
                self.assert_local_only(result, targets)
                self.assertEqual([len(call.args[0]) for call in self.primary.current.call_args_list], [256, 44])
                self.assertEqual([len(call.args[0]) for call in self.local.current.call_args_list], [256, 44])
                self.primary.search.assert_not_called()
                self.local.search.assert_not_called()
                self.assertEqual([call.args[0] for call in progress.call_args_list
                                  if call.args[0].startswith('Using compatible EPG search')],
                                 ['Using compatible EPG search for Python Unicode ' + version])
                self.assert_read_only()

    def test_mismatched_unicode_keeps_complete_history_and_search_options(self):
        original_spec = ott.importlib.util.spec_from_file_location
        for version in ('14.0.0', '16.0.0'):
            with self.subTest(version=version), patch.object(ott.unicodedata, 'unidata_version', version):
                self.client.call.reset_mock(side_effect=True)
                self.client.call.return_value = catalogue()
                self.primary.current.reset_mock()
                self.primary.current.side_effect = lambda rows, query: dict(
                    response(rows), programs=[], searchVersion=1)
                archived = ({'programs': [{'title': 'Cats', 'mode': 'archive'}]}, {'selected': 'archive'})
                history_search = Mock(return_value=archived)
                def load_spec(name, *args, **kwargs):
                    spec = original_spec(name, *args, **kwargs)
                    if name == 'ott_programme_search':
                        original_execute = spec.loader.exec_module
                        def execute(module):
                            original_execute(module)
                            module.search_archives = history_search
                        spec.loader.exec_module = execute
                    return spec
                with patch.object(ott.importlib.util, 'spec_from_file_location', side_effect=load_spec):
                    result = self.run_search(fast=True, refresh=True)
                self.assertEqual(result, archived)
                self.assertEqual([len(call.args[0]) for call in self.primary.current.call_args_list], [256, 44])
                history_search.assert_called_once_with(
                    self.client, 'tv', {'url': SETTINGS['url'], 'source': 'epg-one'}, catalogue(), 'Cats', True,
                    catalog_expired=ott.expired_epg_catalog, progress=None, fast=True, deadline=45)
                self.primary.search.assert_not_called()
                self.local.search.assert_not_called()
                self.assert_read_only(1)

    def test_fallback_retains_remaining_budget_and_restores_controller_timeout(self):
        catalogue_budgets, guide_budgets = [], []
        def catalog(device, action, params):
            catalogue_budgets.append(self.client.timeout)
            if len(catalogue_budgets) == 2:
                self.clock[0] += 2
            return catalogue('remote' if len(catalogue_budgets) == 1 else 'local', 1)
        def primary(rows, query):
            self.clock[0] = 35
            raise ott.EpgHTTPError(502)
        def local(rows, query):
            guide_budgets.append(self.local.timeout)
            return response(rows, 'local-generation')
        self.client.call.side_effect = catalog
        self.primary.current.side_effect, self.local.current.side_effect = primary, local
        result, targets = self.run_search()
        self.assertEqual(catalogue_budgets, [45, 10])
        self.assertEqual(guide_budgets, [8])
        self.assertEqual(targets, {1: {'catalog': 'local-receipt', 'id': 'local0'}})
        self.assert_read_only()

    def test_no_fallback_after_budget_expires_and_no_second_fallback(self):
        def primary(rows, query):
            self.clock[0] = 45
            raise ott.EpgHTTPError(503)
        self.primary.current.side_effect = primary
        with self.assertRaises(ott.EpgHTTPError):
            self.run_search()
        self.local.current.assert_not_called()
        self.assert_read_only(1)
        self.clock[0] = 0
        self.client.call.reset_mock(side_effect=True)
        self.client.call.side_effect = [catalogue(), catalogue('local')]
        self.primary.current.side_effect = ott.EpgHTTPError(502)
        self.local.current.side_effect = ott.EpgHTTPError(503)
        with self.assertRaises(ott.EpgHTTPError):
            self.run_search()
        self.assertEqual(self.local.current.call_count, 1)
        self.assert_read_only()

    def test_fallback_response_cannot_extend_original_deadline(self):
        def primary(rows, query):
            self.clock[0] = 44
            raise ott.EpgHTTPError(503)
        def local(rows, query):
            self.assertEqual(self.local.timeout, 1)
            self.clock[0] = 45
            return response(rows, 'local-generation')
        self.primary.current.side_effect, self.local.current.side_effect = primary, local
        with self.assertRaisesRegex(ott.Error, 'timed out'):
            self.run_search()
        self.assertEqual(self.local.current.call_count, 1)
        self.assert_read_only()

    def test_controller_and_player_failures_never_activate_guide_fallback(self):
        for failure in (ott.HTTPError(401), ott.HTTPError(503), ott.TransportError('Controller unreachable'),
                        ott.PlayerRejected('Player rejected catalogue', {'error': 'busy'})):
            with self.subTest(failure=type(failure).__name__):
                self.client.call.side_effect = failure
                with self.assertRaises(type(failure)):
                    self.run_search()
        self.primary.current.assert_not_called()
        self.local.current.assert_not_called()

    def test_guide_auth_request_generation_and_untyped_errors_never_fallback(self):
        for failure in [ott.EpgHTTPError(status) for status in (400, 401, 403, 404, 409, 413, 422)] + [
                ott.Error('EPG service timed out'), ott.Error('Invalid EPG JSON')]:
            with self.subTest(failure=str(failure)):
                self.client.call.side_effect = None
                self.client.call.return_value = catalogue()
                self.primary.current.side_effect = failure
                with self.assertRaises(ott.Error):
                    self.run_search()
        self.local.current.assert_not_called()

    def test_malformed_stale_and_changed_generation_fail_without_fallback(self):
        for change in ({'stale': True}, {'checked': 0}, {'version': True}, {'programs': 'invalid'},
                       {'programs': [{'id': 'foreign', 'title': 'Cats', 'start': NOW - 1, 'end': NOW + 1}]}):
            with self.subTest(change=change):
                self.client.call.side_effect = None
                self.client.call.return_value = catalogue()
                self.primary.current.side_effect = lambda rows, query: dict(response(rows), **change)
                with self.assertRaises(ott.Error):
                    self.run_search()
        self.primary.current.side_effect = lambda rows, query: response(
            rows, 'changed' if rows[0]['id'] == 'remote256' else 'remote-generation')
        with self.assertRaisesRegex(ott.Error, 'changed'):
            self.run_search()
        self.local.current.assert_not_called()

    def test_stale_fetch_timestamp_is_rejected_even_when_stale_flag_is_false(self):
        self.primary.current.side_effect = lambda rows, query: dict(response(rows), fetchedAt=(NOW - 7201) * 1000)
        with self.assertRaises(ott.Error):
            self.run_search()
        self.local.current.assert_not_called()

    def test_fallback_requires_explicit_configuration(self):
        self.primary.current.side_effect = ott.EpgHTTPError(503)
        settings = {key: value for key, value in SETTINGS.items() if key != 'fallback_url'}
        with self.assertRaises(ott.EpgHTTPError):
            ott.server_programs(self.client, 'tv', settings, 'Cats')
        self.local.current.assert_not_called()
        self.assert_read_only(1)

    def test_legacy_history_availability_fails_over_but_media_errors_do_not(self):
        original_spec = ott.importlib.util.spec_from_file_location
        for kind in ('http', 'transport', 'media'):
            with self.subTest(kind=kind):
                self.client.call.reset_mock(side_effect=True)
                self.client.call.side_effect = [catalogue(), catalogue('local')]
                self.local.current.reset_mock()
                self.primary.current.side_effect = lambda rows, query: dict(response(rows), programs=[])

                def load_spec(name, *args, **kwargs):
                    spec = original_spec(name, *args, **kwargs)
                    if name == 'ott_programme_search':
                        original_execute = spec.loader.exec_module
                        def execute(module):
                            original_execute(module)
                            if kind == 'http':
                                failure = module.EpgAvailabilityError('Guide unavailable', status=503)
                            elif kind == 'transport':
                                failure = module.EpgAvailabilityError('Guide unavailable', transport=True)
                            else:
                                failure = module.ArchiveTransientError('Media unavailable')
                            module.search_archives = Mock(side_effect=failure)
                        spec.loader.exec_module = execute
                    return spec

                with patch.object(ott.importlib.util, 'spec_from_file_location', side_effect=load_spec):
                    if kind == 'media':
                        with self.assertRaisesRegex(ott.Error, 'Media unavailable'):
                            self.run_search()
                        self.local.current.assert_not_called()
                        self.assert_read_only(1)
                    else:
                        result, targets = self.run_search()
                        self.assert_local_only(result, targets)
                        self.assert_read_only()


if __name__ == '__main__':
    unittest.main()
