"""Fast archive selection preserves complete candidates and bounded probes."""
import contextlib
import copy
import importlib.util
import io
import itertools
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError


spec = importlib.util.spec_from_file_location(
    'archive_fast_search', Path(__file__).resolve().parents[1] / 'cli/programme_search.py')
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)
NOW = 1800000000
CHANNEL = {'id': 'cats', 'name': 'Channel', 'number': 1, 'tvgId': 'cats',
           'tvgName': '', 'shift': 0, 'archiveHours': 144}
SNAPSHOT = {'catalog': 'receipt', 'channels': [CHANNEL],
            'archive': {'version': 1, 'revision': 'source'}}
SETTINGS = {'url': 'https://epg.example/epg/v1', 'source': 'epg-one'}


def row(start, stop, name='Три кота'):
    return {'time': NOW + start, 'time_to': NOW + stop, 'name': name}


class FastSelectionTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(patch.object(search.time, 'time', return_value=NOW))
        self.random = self.stack.enter_context(patch.object(search.secrets, 'SystemRandom'))
        self.random.return_value.shuffle.side_effect = lambda items: None
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(search, 'CACHE', Path(folder)))

    def test_fast_mode_stops_at_first_playable_chain_but_preserves_member_order(self):
        guide = [(CHANNEL, [row(-900, -800), row(-800, -700), row(-700, -600, 'Other'),
                            row(-600, -500), row(-500, -400)])]
        verify = Mock(side_effect=[False, True])
        selected, _ = search.select_from_schedules(guide, 'три кота', verify, fast=True)
        self.assertEqual(len(selected), 1)
        self.assertEqual((selected[0][1]['time'], selected[0][3]), (NOW - 800, 1))
        self.assertEqual([call.args[1]['time'] for call in verify.call_args_list], [NOW - 900, NOW - 800])
        candidates = self.random.return_value.shuffle.call_args.args[0]
        self.assertEqual([[item['time'] for item in chain] for _, chain in candidates],
                         [[NOW - 900, NOW - 800], [NOW - 600, NOW - 500]])

    def test_all_chain_permutations_choose_each_stable_playable_chain_equally(self):
        guide = [(CHANNEL, [row(-1000 + i * 100, -950 + i * 100) for i in range(5)])]
        counts = {NOW - 1000: 0, NOW - 800: 0, NOW - 600: 0}
        for order in itertools.permutations(range(5)):
            self.random.return_value.shuffle.side_effect = lambda items, order=order: items.__setitem__(
                slice(None), [items[i] for i in order])
            selected, _ = search.select_from_schedules(
                guide, 'три кота', lambda channel, item: item['time'] in counts, fast=True)
            counts[selected[0][1]['time']] += 1
        self.assertEqual(set(counts.values()), {40})

    def test_later_invalid_metadata_rejected_before_first_probe(self):
        guide = [(CHANNEL, [row(-900, -800)]),
                 (dict(CHANNEL, id='later', number=2), [{'name': 'Три кота', 'time': True, 'time_to': NOW}])]
        verify = Mock(return_value=True)
        with self.assertRaises(search.SearchError):
            search.select_from_schedules(guide, 'три кота', verify, fast=True)
        verify.assert_not_called()

    def test_overlap_break_and_late_live_priority_are_preserved(self):
        guide = [(CHANNEL, [row(-100, -90), row(-95, -90, 'Other'), row(-90, -80), row(1, 10)])]
        def verify(channel, item):
            self.clock.return_value = NOW + 2
            return True
        selected, _ = search.select_from_schedules(guide, 'три кота', verify, fast=True)
        self.assertEqual(selected[0][2], 'live')
        shuffled = self.random.return_value.shuffle.call_args.args[0]
        self.assertEqual([len(chain) for _, chain in shuffled], [1, 1])

    def test_fast_singleton_never_enters_complete_query_cache(self):
        client = Mock(config={'server': 'https://control.example'}, timeout=45)
        client.call.side_effect = lambda device, action, params: copy.deepcopy(SNAPSHOT) if action == 'epg_catalog' else {
            'resolved': True, 'url': 'https://private.example/token/secret'}
        guide = [(CHANNEL, [row(-900, -800), row(-700, -600)])]
        with patch.object(search, 'schedules', return_value=(guide, NOW + 7200)), \
                patch.object(search, 'archive_available', return_value=True):
            first, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота', fast=True)
            self.random.return_value.shuffle.side_effect = lambda items: items.reverse()
            second, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота', fast=True)
            self.assertNotEqual(first['programs'][0]['start'], second['programs'][0]['start'])
            self.assertEqual((first['selection'], first['verified_all'], first['candidate_chains']),
                             ('random_first_available', False, 2))
            self.assertEqual(list(search.CACHE.glob('query-*.json')), [])
            full, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота')
            fast_after_full, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота', fast=True)
        self.assertEqual(len(full['programs']), 2)
        self.assertEqual(len(fast_after_full['programs']), 1)
        self.assertEqual(fast_after_full['candidate_chains'], 2)
        self.assertEqual(len(list(search.CACHE.glob('query-*.json'))), 1)
        self.assertNotIn('selection', full)

    def test_prepared_candidates_require_full_validation_and_late_recheck(self):
        client = Mock(config={'server': 'https://control.example'}, timeout=45)
        client.call.side_effect = lambda device, action, params: copy.deepcopy(SNAPSHOT) if action == 'epg_catalog' else {
            'resolved': True, 'url': 'https://private.example/token/secret'}
        prepared = {'current': [], 'chains': [(CHANNEL, [row(-900, -800)])], 'until': NOW + 100}
        fresh = Mock(return_value=[(CHANNEL, row(-1, 100))])
        with patch.object(search, 'schedules') as schedules, \
                patch.object(search, 'archive_available', return_value=True):
            result, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота',
                                               fast=True, prepared=prepared, refresh_current=fresh)
        self.assertEqual(result['programs'][0]['mode'], 'live')
        fresh.assert_called_once()
        schedules.assert_not_called()
        for invalid in (dict(prepared, chains=prepared['chains'] + [(dict(CHANNEL, id='foreign'), [row(-600, -500)])]),
                        dict(prepared, chains=[(CHANNEL, [row(-900, -800), row(-700, -600)])])):
            with patch.object(search, 'archive_available') as probe, self.assertRaises(search.SearchError):
                search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота',
                                       fast=True, prepared=invalid, refresh_current=fresh)
            probe.assert_not_called()

    def test_deadline_restores_rpc_timeout_and_does_not_dispatch(self):
        client = Mock(config={'server': 'https://control.example'}, timeout=45)
        now = [1.0]
        prepared = {'current': [], 'chains': [(CHANNEL, [row(-900, -800)])], 'until': NOW + 100}
        def call(device, action, params):
            self.assertEqual(client.timeout, 1)
            now[0] = 3
            return {'resolved': True, 'url': 'https://private.example/secret'}
        client.call.side_effect = call
        with patch.object(search.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(search, 'archive_available') as probe, self.assertRaises(search.SearchTimeout):
            search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота', fast=True, deadline=2,
                                   prepared=prepared, refresh_current=lambda: [])
        self.assertEqual(client.timeout, 45)
        self.assertEqual(client.call.call_count, 1)
        probe.assert_not_called()

    def test_prepared_current_preserves_distinct_overlaps_but_rejects_duplicates(self):
        first, second = row(-50, 50), row(-10, 90)
        pairs = [(CHANNEL, first), (CHANNEL, second)]
        prepared = {'current': pairs, 'chains': [], 'until': NOW + 100}
        current, chains, until = search._prepared_candidates(prepared, [CHANNEL], 'три кота')
        self.assertEqual(current, pairs)
        self.assertEqual(chains, [])
        self.assertEqual(until, NOW + 100)
        with self.assertRaises(search.SearchError):
            search._prepared_candidates(dict(prepared, current=pairs + [pairs[0]]), [CHANNEL], 'три кота')
        client = Mock(config={'server': 'https://control.example'}, timeout=45)
        client.call.return_value = copy.deepcopy(SNAPSHOT)
        with patch.object(search, 'archive_available') as probe:
            result, _ = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота',
                                               fast=True, prepared=prepared, refresh_current=lambda: pairs)
        self.assertEqual([item['start'] for item in result['programs']], [NOW - 50, NOW - 10])
        self.assertEqual({item['mode'] for item in result['programs']}, {'live'})
        probe.assert_not_called()

    def test_prepared_chains_preserve_distinct_same_start_rows(self):
        first, second = row(-50, -30), row(-50, -10)
        pairs = [(CHANNEL, [first]), (CHANNEL, [second])]
        prepared = {'current': [], 'chains': pairs, 'until': NOW + 100}
        _, chains, _ = search._prepared_candidates(prepared, [CHANNEL], 'три кота')
        self.assertEqual(chains, pairs)
        with self.assertRaises(search.SearchError):
            search._prepared_candidates(dict(prepared, chains=pairs + [pairs[0]]), [CHANNEL], 'три кота')
        client = Mock(config={'server': 'https://control.example'}, timeout=45)
        client.call.side_effect = lambda device, action, params: copy.deepcopy(SNAPSHOT) if action == 'epg_catalog' else {
            'resolved': True, 'url': 'https://private.example/token/secret'}
        with patch.object(search, 'archive_available', return_value=True):
            result, targets = search.search_archives(client, 'tv', SETTINGS, SNAPSHOT, 'три кота',
                                                     prepared=prepared, refresh_current=lambda: [])
        self.assertEqual(len(result['programs']), 2)
        for candidate in result['programs']:
            key = (candidate['number'], candidate['start'], candidate['end'], candidate['title'])
            self.assertEqual(targets[key], {'catalog': 'receipt', 'id': CHANNEL['id'],
                                           'start': candidate['start'], 'end': candidate['end'],
                                           'title': candidate['title']})
        self.assertEqual(targets[(1, NOW - 50)]['end'], NOW - 10)

    def test_retention_rechecked_after_slow_current_refresh(self):
        channel = dict(CHANNEL, archiveHours=1)
        candidate = row(-3599, -3590)
        calls = []
        def current(now):
            if calls:
                self.clock.return_value = NOW + 2
            calls.append(now)
            return []
        selected = search._select_chains(current, [(channel, [candidate])], lambda *args: True, fast=True)
        self.assertEqual(selected, [])


class CountingResponse(io.BytesIO):
    def __init__(self, content, chunk=0):
        super().__init__(content)
        self.bytes_read, self.chunk, self.did_close = 0, chunk, False

    def read1(self, count):
        value = super().read(min(count, self.chunk) if self.chunk else count)
        self.bytes_read += len(value)
        return value

    def close(self):
        self.did_close = True
        super().close()


class SmallProbeTest(unittest.TestCase):
    def test_unstamped_media_is_reachable_not_proof_of_an_archive_timeline(self):
        # A provider can serve identical bytes for live and archival media.
        # Neither an absent ENDLIST nor the channel's label establishes mode.
        for stamps, available in ((b'', True),
                                  (b'#EXT-X-PROGRAM-DATE-TIME:2020-01-01T00:00:00Z\n', False)):
            manifest = CountingResponse(b'#EXTM3U\n' + stamps + b'#EXTINF:10,\nsegment.ts\n')
            segment = CountingResponse((b'G' + b'x' * 187) * 3)
            opener = Mock()
            opener.open.side_effect = [manifest, segment]
            with self.subTest(stamps=stamps), patch.object(search, 'build_opener', return_value=opener):
                self.assertEqual(search.archive_available('https://media.example/playlist', NOW, strict=True),
                                 available)
            self.assertEqual(opener.open.call_count, 2 if available else 1)
            self.assertTrue(manifest.did_close)

    def test_ts_and_fragmented_mp4_read_only_required_prefix_with_short_reads(self):
        ts = CountingResponse((b'G' + b'x' * 187) * 10000, chunk=7)
        opener = Mock(); opener.open.return_value = ts
        with patch.object(search, 'build_opener', return_value=opener):
            self.assertTrue(search.archive_available('https://media.example/stream', NOW, strict=True))
        self.assertEqual(ts.bytes_read, 377)
        self.assertTrue(ts.did_close)
        manifest = CountingResponse(b'#EXTM3U\n#EXTINF:10,\nsegment.m4s\n', chunk=3)
        fragment = CountingResponse(b'\x00\x00\x00\x10moof' + b'x' * 10000, chunk=2)
        opener.open.side_effect = [manifest, fragment]
        with patch.object(search, 'build_opener', return_value=opener):
            self.assertTrue(search.archive_available('https://media.example/playlist', NOW, strict=True))
        self.assertEqual(fragment.bytes_read, 16)
        self.assertTrue(manifest.did_close and fragment.did_close)

    def test_strict_probe_distinguishes_missing_from_source_and_transient_errors(self):
        for code, expected in ((404, None), (410, None), (401, search.ArchiveSourceError),
                               (403, search.ArchiveSourceError), (429, search.ArchiveTransientError),
                               (503, search.ArchiveTransientError)):
            opener = Mock(); body = io.BytesIO(b'private secret')
            opener.open.side_effect = HTTPError('private token', code, 'private reason', {}, body)
            with self.subTest(code=code), patch.object(search, 'build_opener', return_value=opener):
                if expected:
                    with self.assertRaises(expected) as failure:
                        search.archive_available('https://media.example/token', NOW, strict=True)
                    self.assertNotIn('private', str(failure.exception))
                else:
                    self.assertFalse(search.archive_available('https://media.example/token', NOW, strict=True))
            self.assertTrue(body.closed)

    def test_probe_deadline_covers_the_entire_trickling_response(self):
        stopped = threading.Event()
        class SlowResponse(CountingResponse):
            def read1(self, count):
                stopped.wait(0.04)
                return super().read1(min(count, 1))
        stream = SlowResponse((b'G' + b'x' * 187) * 3)
        opener = Mock(); opener.open.return_value = stream
        started = time.monotonic()
        try:
            with patch.object(search, 'build_opener', return_value=opener), self.assertRaises(search.SearchTimeout):
                search.archive_available('https://media.example/token', NOW, deadline=started + 0.08, strict=True)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            stopped.set()
        for thread in threading.enumerate():
            if thread.name == 'ottplay-archive-http':
                thread.join(0.2)
        self.assertTrue(stream.did_close)
        self.assertLess(stream.bytes_read, 377)

    def test_numeric_loopback_guide_bypasses_proxy_but_public_and_dns_hosts_do_not(self):
        body = json.dumps({'version': 1, 'source': 'epg-one', 'generation': 'g1', 'stale': False}).encode()
        for base, bypass in (('http://127.0.0.1:9999/epg/v1', True), ('http://[::1]:9999/epg/v1', True),
                             ('https://epg.example/epg/v1', False), ('http://localhost/epg/v1', False)):
            opener = Mock(); opener.open.return_value = io.BytesIO(body)
            with self.subTest(base=base), patch.object(search, 'build_opener', return_value=opener) as build:
                search.epg_request(base, '/match', {})
                handlers = build.call_args.args
                proxies = [handler for handler in handlers if isinstance(handler, search.ProxyHandler)]
                self.assertEqual(bool(proxies), bypass)
                if bypass:
                    self.assertEqual(proxies[0].proxies, {})

    def test_history_timeout_stops_scheduling_and_does_not_wait_for_the_pool(self):
        channels = [dict(CHANNEL, id=str(i), number=i + 1) for i in range(20)]
        mapping = {'generation': 'g1', 'until': NOW + 100,
                   'mappings': {str(i): {'channelId': str(i), 'shift': 0} for i in range(20)}}
        release = threading.Event()
        requests = []
        def request(*args, **kwargs):
            requests.append(args)
            release.wait(0.4)
            return {'generation': 'g1', 'rows': [row(-100, -50)]}
        started = time.monotonic()
        try:
            with patch.object(search.time, 'time', return_value=NOW), \
                    patch.object(search, 'cached', side_effect=lambda kind, *args: mapping if kind == 'mapping' else None), \
                    patch.object(search, 'epg_request', side_effect=request), self.assertRaises(search.SearchTimeout):
                search.schedules(SETTINGS['url'], channels, deadline=started + 0.08)
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertLessEqual(len(requests), 4)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
