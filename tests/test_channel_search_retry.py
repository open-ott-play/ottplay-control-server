"""Only explicitly stale read-only discovery may restart a channel search."""
import contextlib
import copy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from test_categories_cli import capabilities, category_page, channel_page, ott


QUERY = 'Три кота'


def stale(operation='channels', **changes):
    return ott.PlayerRejected('private response', dict(
        version=1, runtime='page-one', op=operation, state='error', error='catalog_changed', **changes))


class ChannelSearchRetryTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock(timeout=99)
        self.progress = Mock()
        self.clock = [0]
        self.clock_patch = patch.object(ott.time, 'monotonic', side_effect=lambda: self.clock[0])
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.row = {'id': 'new-video', 'number': 18, 'name': QUERY}
        self.page = channel_page([self.row], catalog='new-query')
        self.ack = {'dispatched': True, 'channel': copy.deepcopy(self.row)}

    def run_search(self, replies, timeout=45):
        self.client.call.side_effect = replies
        return ott.play_channel(self.client, 'tv', {'query': QUERY}, timeout, progress=self.progress)

    def actions(self):
        return [entry.args[1] for entry in self.client.call.call_args_list]

    def test_stale_query_restarts_full_discovery_then_dispatches_once(self):
        result, error = self.run_search([
            capabilities(), category_page(), stale(),
            capabilities(), category_page(catalog='new-source'), self.page, self.ack])
        self.assertIsNone(error)
        self.assertEqual(result['channels'], [self.row])
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'] * 2 + ['play_catalog'])
        self.assertEqual(self.client.call.call_args_list[-2].args[2]['catalog'], 'new-source')
        self.assertEqual(self.client.call.call_args, call('tv', 'play_catalog',
                                                       {'catalog': 'new-query', 'id': 'new-video'}))
        self.progress.assert_called_once_with('Player catalogue changed; restarting channel-name lookup')
        self.assertEqual(self.client.timeout, 99)

    def test_partial_rows_are_discarded_before_fresh_discovery(self):
        caps = capabilities()
        caps['categories']['maxPageSize'] = 1
        first = channel_page([{'id': 'old-video', 'number': 1, 'name': QUERY}], total=2, has_more=True)
        result, error = self.run_search([
            caps, category_page([{'id': 'all', 'name': 'All', 'count': 2}], total=2, has_more=True),
            first, stale(), capabilities(), category_page(), self.page, self.ack])
        self.assertIsNone(error)
        self.assertEqual(result['channels'], [self.row])
        self.assertEqual(self.actions().count('capabilities'), 2)
        self.assertEqual(self.actions().count('play_catalog'), 1)

    def test_stale_category_read_also_restarts_once(self):
        result, error = self.run_search([
            capabilities(), stale('list'), capabilities(), category_page(), self.page, self.ack])
        self.assertIsNone(error)
        self.assertEqual(result['channels'], [self.row])
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'capabilities',
                                          'categories', 'channels', 'play_catalog'])

    def test_repeated_stale_catalogue_stops_without_playback(self):
        with self.assertRaisesRegex(ott.Error, 'No playback was requested'):
            self.run_search([capabilities(), category_page(), stale()] * 2)
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'] * 2)
        self.progress.assert_called_once()
        self.assertEqual(self.client.timeout, 99)

    def test_retry_uses_original_deadline_and_restores_timeout(self):
        replies = iter([capabilities(), category_page(), stale(),
                        capabilities(), category_page(), self.page, self.ack])
        budgets = []
        def respond(*_):
            budgets.append(self.client.timeout)
            self.clock[0] += 1
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.run_search(respond, timeout=10)
        self.assertEqual(budgets, [10, 9, 8, 7, 6, 5, 4])
        self.assertEqual(self.client.timeout, 99)

    def test_expired_deadline_never_restarts_discovery(self):
        replies = iter([capabilities(), category_page()])
        def respond(_device, action, _params):
            if action == 'channels':
                self.clock[0] = 45
                raise stale()
            return next(replies)
        with self.assertRaisesRegex(ott.Error, 'No playback was requested'):
            self.run_search(respond)
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'])
        self.progress.assert_not_called()

    def test_only_verified_catalogue_change_is_retryable(self):
        base = stale().data
        bad = [dict(base, runtime='other'), dict(base, op='play_catalog'),
               dict(base, state='ok'), dict(base, version=True), dict(base, error='runtime_mismatch'),
               dict(base, private='secret'), {'error': 'catalog_changed'}]
        for envelope in bad:
            with self.subTest(envelope=envelope):
                self.client.call.reset_mock()
                with self.assertRaises(ott.Error):
                    self.run_search([capabilities(), category_page(), ott.PlayerRejected('secret', envelope)])
                self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'])
        self.progress.assert_not_called()

    def test_mutation_rejection_is_never_retried(self):
        result, error = self.run_search([
            capabilities(), category_page(), self.page, stale('play_catalog')])
        self.assertIsInstance(error, ott.Error)
        self.assertNotIsInstance(error, ott.MediaCatalogChanged)
        self.assertEqual(result['channels'], [self.row])
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels', 'play_catalog'])
        self.progress.assert_not_called()

    def test_unconfirmed_read_preserves_diagnostics_without_replay_or_mutation_warning(self):
        for stage in ('enqueue', 'receipt'):
            with self.subTest(stage=stage):
                self.client.call.reset_mock()
                failure = ott.RequestError('controller_unavailable', stage, 'Do not repeat blindly',
                                           'a' * 32, transport_category='socket')
                failure.action = 'channels'
                with self.assertRaises(ott.RequestError) as caught:
                    self.run_search([capabilities(), category_page(), failure])
                self.assertIs(caught.exception, failure)
                self.assertIn('No playback was requested', str(failure))
                self.assertIn('channels/' + stage, str(failure))
                self.assertNotIn('blindly', str(failure))
                self.assertEqual(ott.request_error_metadata(failure), {
                    'action': 'channels', 'request_id': 'a' * 32, 'stage': stage,
                    'reason': 'controller_unavailable', 'transport_category': 'socket'})
                self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'])
        self.progress.assert_not_called()

    def test_loading_source_stops_name_lookup_before_querying_unstable_channels(self):
        with self.assertRaisesRegex(ott.Error, 'catalog is still loading; no playback was requested'):
            self.run_search([capabilities(), category_page(loading=True)])
        self.assertEqual(self.actions(), ['capabilities', 'categories'])

    def test_loading_source_preserves_explicit_loaded_id_playback(self):
        self.client.call.side_effect = [capabilities(), category_page(loading=True), self.page, self.ack]
        result, error = ott.play_channel(self.client, 'tv', {'query': 'id:new-video'})
        self.assertIsNone(error)
        self.assertTrue(result['playback']['dispatched'])
        self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels', 'play_catalog'])

    def command(self):
        args = SimpleNamespace(words=['tv', QUERY], timeout=45, json=True, refresh=False, config='/unused')
        return ott.execute_player_command(self.client, {'epg': {
            'url': 'https://guide.example/epg/v1', 'source': 'epg-one'}}, args, [], 'tv',
            progress_stream=io.StringIO())

    def test_implicit_guide_search_and_dispatch_share_the_original_deadline(self):
        now = 1800000000
        program = dict(number=18, channel=QUERY, title=QUERY, start=now - 10, end=now + 10)
        for final_clock in (44, 45):
            with self.subTest(final_clock=final_clock):
                self.clock[0] = 0
                self.client.call.reset_mock()
                self.client.timeout = 45
                budgets = []
                def respond(_device, action, _params):
                    budgets.append(self.client.timeout)
                    if action == 'capabilities':
                        self.clock[0] = 3
                        raise ott.PlayerUnsupported('Legacy player')
                    if action == 'channels':
                        self.clock[0] = 7
                        return {'channels': []}
                    self.assertEqual(action, 'play_catalog')
                    return self.ack
                def guide(_client, _device, _settings, _query, **options):
                    self.assertEqual(options['deadline'], 45)
                    self.assertEqual(self.clock[0], 7)
                    self.clock[0] = final_clock
                    return {'programs': [program]}, {18: {'catalog': 'epg', 'id': 'new-video'}}
                self.client.call.side_effect = respond
                with patch.object(ott, 'server_programs', side_effect=guide) as search, \
                        patch.object(ott.time, 'time', return_value=now), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if final_clock == 44:
                        self.assertEqual(self.command(), 0)
                    else:
                        with self.assertRaisesRegex(ott.Error, 'exceeded --timeout; no playback was requested'):
                            self.command()
                self.assertEqual(budgets, [45, 42, 1] if final_clock == 44 else [45, 42])
                self.assertEqual(self.client.timeout, 45)
                search.assert_called_once()

    def test_guide_entry_honors_supplied_remaining_budget(self):
        self.clock[0] = 7
        self.client.timeout = 45
        self.client.call.return_value = {'catalog': 'epg', 'channels': []}
        with patch.object(ott, 'EpgClient') as factory:
            result, _ = ott.server_programs(self.client, 'tv', {
                'url': 'https://guide.example/epg/v1', 'source': 'epg-one'}, QUERY, deadline=45)
        self.assertEqual(factory.call_args.args[1], 38)
        self.assertEqual(result['programs'], [])
        self.assertEqual(self.client.timeout, 45)

    def test_media_catalogue_no_match_never_falls_back_to_tv_guide(self):
        for provider in ('plex', 'vportal'):
            with self.subTest(provider=provider):
                self.client.call.reset_mock()
                self.client.call.side_effect = [capabilities(), category_page(provider=provider), channel_page([])]
                with patch.object(ott, 'server_programs') as search, \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(ott.Error, 'No channels match'):
                        self.command()
                search.assert_not_called()
                self.assertEqual(self.actions(), ['capabilities', 'categories', 'channels'])


if __name__ == '__main__':
    unittest.main()
