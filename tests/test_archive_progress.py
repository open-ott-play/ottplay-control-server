"""Archive progress is optional, credential-free and emitted by the caller."""
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    'archive_progress_search', Path(__file__).resolve().parents[1] / 'cli/programme_search.py')
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)
NOW = 1800000000
CHANNEL = {'id': 'private-channel-id', 'name': 'private-channel-name', 'number': 1,
           'tvgId': 'private-guide-id', 'tvgName': 'private-guide-name', 'shift': 0, 'archiveHours': 144}
SNAPSHOT = {'catalog': 'private-receipt', 'channels': [CHANNEL],
            'archive': {'version': 1, 'revision': 'private-revision'}}
SETTINGS = {'url': 'https://private-guide.example/epg/v1', 'source': 'epg-one'}
ROWS = [{'time': NOW - 600, 'time_to': NOW - 500, 'name': 'private-query'},
        {'time': NOW - 500, 'time_to': NOW - 400, 'name': 'private-query'}]


class ArchiveProgressTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(search, 'CACHE', Path(directory)))
        self.stack.enter_context(patch.object(search.time, 'time', return_value=NOW))
        self.events = []
        self.thread = threading.get_ident()

    def progress(self, stage, completed=None, total=None):
        self.assertEqual(threading.get_ident(), self.thread)
        self.assertNotIn('private', stage)
        self.assertNotIn('https://', stage)
        self.events.append((stage, completed, total))

    def guide_response(self, base, path, payload=None):
        if path == '/match':
            self.assertEqual(self.events[-1][0], 'Matching archive channels')
            return {'generation': 'private-generation', 'mappings': {
                row['id']: {'channelId': row['id'], 'shift': 0} for row in payload['channels']}}
        return {'generation': 'private-generation', 'rows': ROWS}

    def test_mapping_history_and_cache_progress_leave_results_unchanged(self):
        with patch.object(search, 'epg_request', side_effect=self.guide_response) as request:
            expected = search.schedules(SETTINGS['url'], [CHANNEL], progress=self.progress)
            self.assertEqual(request.call_count, 2)
            self.assertIn(('Matching archive channels', 0, 1), self.events)
            self.assertIn(('Matching archive channels', 1, 1), self.events)
            self.assertIn(('Loading programme history', 0, 1), self.events)
            self.assertEqual(self.events[-1], ('Loading programme history', 1, 1))
            self.events.clear()
            self.assertEqual(search.schedules(SETTINGS['url'], [CHANNEL], progress=self.progress), expected)
            self.assertEqual(request.call_count, 2)
            self.assertIn(('Using cached channel mappings', 1, 1), self.events)
            self.assertEqual(self.events[-1], ('Loading programme history', 1, 1))

    def test_history_reports_completed_futures_on_caller_thread_and_retains_channel_order(self):
        release = threading.Event()
        channels = [dict(CHANNEL, id='slow', number=1), dict(CHANNEL, id='fast', number=2)]
        mapping = {'generation': 'g1', 'until': NOW + 7200,
                   'mappings': {row['id']: {'channelId': row['id'], 'shift': 0} for row in channels}}
        def request(base, path, payload=None):
            if 'channelId=slow' in path:
                self.assertTrue(release.wait(2), 'Completed history should be reported without waiting for earlier tasks')
            return {'generation': 'g1', 'rows': ROWS}
        def progress(stage, completed=None, total=None):
            self.progress(stage, completed, total)
            if (stage, completed, total) == ('Loading programme history', 1, 2):
                release.set()
        with patch.object(search, 'cached', side_effect=lambda kind, *args: mapping if kind == 'mapping' else None), \
                patch.object(search, 'epg_request', side_effect=request):
            guide, _ = search.schedules(SETTINGS['url'], channels, progress=progress)
        self.assertEqual([channel['id'] for channel, rows in guide], ['slow', 'fast'])
        self.assertIn(('Loading programme history', 1, 2), self.events)
        self.assertEqual(self.events[-1], ('Loading programme history', 2, 2))

    def test_archive_rpc_media_and_cached_results_emit_safe_stages(self):
        client = Mock(config={'server': 'https://private-control.example'})
        def call(device, action, params):
            if action == 'epg_catalog':
                self.assertEqual(self.events[-1][0], 'Refreshing player catalogue')
                return copy.deepcopy(SNAPSHOT)
            self.assertEqual(self.events[-1][0], 'Resolving archive candidates')
            return {'resolved': True, 'url': 'https://private-media.example/token/secret'}
        client.call.side_effect = call
        def probe(*args):
            self.assertEqual(self.events[-1][0], 'Checking archive media')
            return True
        with patch.object(search, 'schedules', return_value=([(CHANNEL, ROWS)], NOW + 7200)) as schedules, \
                patch.object(search, 'archive_available', side_effect=probe) as probe_call:
            result = search.search_archives(client, 'private-device', SETTINGS, SNAPSHOT, 'private-query', progress=self.progress)
            self.assertIn(('Checking archive availability', 1, None), self.events)
            self.assertEqual(self.events[-1], ('Archive search complete', 1, 1))
            self.assertEqual(len(result[0]['programs']), 1)
            schedules.assert_called_once_with(SETTINGS['url'], SNAPSHOT['channels'], False, progress=self.progress)
            self.events.clear()
            self.assertEqual(search.search_archives(client, 'private-device', SETTINGS, SNAPSHOT, 'private-query', progress=self.progress), result)
            self.assertIn(('Using cached archive results', 1, 1), self.events)
            self.assertEqual(self.events[-1], ('Archive search complete', 1, 1))
            self.assertEqual(probe_call.call_count, 1)
            self.assertEqual(schedules.call_count, 1)
            # A different query key still reuses the independently saved media check.
            self.events.clear()
            search.search_archives(client, 'private-device', SETTINGS, SNAPSHOT, 'query', progress=self.progress)
            self.assertIn(('Using cached archive availability', 0, None), self.events)
            self.assertEqual(probe_call.call_count, 1)

    def test_default_without_callback_is_silent_for_shared_resolver(self):
        def request(base, path, payload=None):
            if path == '/match':
                return {'generation': 'g1', 'mappings': {CHANNEL['id']: {'channelId': 'g1', 'shift': 0}}}
            return {'generation': 'g1', 'rows': ROWS}
        client = Mock(config={'server': 'https://private-control.example'})
        client.call.side_effect = lambda device, action, params: copy.deepcopy(SNAPSHOT) if action == 'epg_catalog' else {
            'resolved': True, 'url': 'https://private-media.example/token/secret'}
        with patch.object(search, 'epg_request', side_effect=request), \
                patch.object(search, 'archive_available', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            data, _ = search.search_archives(client, 'private-device', SETTINGS, SNAPSHOT, 'private-query')
        self.assertEqual(len(data['programs']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')

    def test_failure_keeps_stage_without_false_completion(self):
        with patch.object(search, 'epg_request', side_effect=search.SearchError('private failure')):
            with self.assertRaises(search.SearchError):
                search.schedules(SETTINGS['url'], [CHANNEL], progress=self.progress)
        self.assertEqual(self.events[-1], ('Matching archive channels', 0, 1))
        self.assertFalse(any(event[0] == 'Archive search complete' for event in self.events))


if __name__ == '__main__':
    unittest.main()
