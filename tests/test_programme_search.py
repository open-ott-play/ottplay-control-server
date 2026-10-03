import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "cli" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


search = load("programme_search", "programme_search.py")
ott = load("programme_ott", "ott.py")
NOW = 1800000000
CHANNEL = {"id": "cats", "name": "Channel", "number": 1, "tvgId": "cats", "tvgName": "", "shift": 0, "archiveHours": 144}
SNAPSHOT = {"catalog": "receipt", "channels": [CHANNEL], "archive": {"version": 1, "revision": "stable-source"}}
SETTINGS = {"url": "https://epg.example/epg/v1", "source": "epg-one"}


def row(start, end, name="Орёл и решка"):
    return {"time": NOW + start, "time_to": NOW + end, "name": name}


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(search, "CACHE", Path(directory)))
        self.clock = self.stack.enter_context(patch.object(search.time, "time", return_value=NOW))
        self.client = Mock(config={"server": "https://control.example"})
        self.client.call.side_effect = lambda device, action, params: (
            copy.deepcopy(SNAPSHOT) if action == "epg_catalog" else
            {"resolved": True, "url": "https://proxy.example/private/channel.m3u8"})
        self.guides = self.stack.enter_context(patch.object(search, "schedules", return_value=(
            [(CHANNEL, [row(-600, -500), row(-500, -400), row(-400, -300)])], NOW + 7200)))
        self.probe = self.stack.enter_context(patch.object(search, "archive_available", return_value=True))

    def run_search(self, query="орел и решка", snapshot=SNAPSHOT, refresh=False, **kwargs):
        return search.search_archives(self.client, "tv", SETTINGS, snapshot, query, refresh, **kwargs)

    def test_expired_receipt_is_refreshed_before_one_read_only_retry(self):
        expired = ott.PlayerRejected('expired', {
            'error': 'Channels or provider changed. Retry the EPG query before playing.'})
        renewed = copy.deepcopy(SNAPSHOT)
        renewed['catalog'] = 'renewed-receipt'
        self.client.call.side_effect = [expired, renewed,
                                       {'resolved': True, 'url': 'https://proxy.example/archive.m3u8'}, renewed]
        data, targets = self.run_search(catalog_expired=ott.expired_epg_catalog)
        self.assertEqual(len(data['programs']), 1)
        calls = self.client.call.call_args_list
        self.assertEqual([call.args[1] for call in calls],
                         ['resolve_archive', 'epg_catalog', 'resolve_archive', 'epg_catalog'])
        self.assertEqual(calls[2].args[2]['catalog'], 'renewed-receipt')
        self.assertEqual(targets[(1, NOW - 600)]['catalog'], 'renewed-receipt')

    def test_receipt_retry_rejects_changed_catalogues_and_never_repeats_twice(self):
        expired = ott.PlayerRejected('expired', {
            'error': 'Channels or provider changed. Retry the EPG query before playing.'})
        for changed in ('channels', 'revision', 'expires-again'):
            renewed = copy.deepcopy(SNAPSHOT)
            renewed['catalog'] = 'renewed-receipt'
            if changed == 'channels':
                renewed['channels'][0]['name'] = 'Changed'
            elif changed == 'revision':
                renewed['archive']['revision'] = 'changed-source'
            self.client.call.reset_mock()
            self.client.call.side_effect = [expired, renewed, expired]
            with self.subTest(changed=changed), self.assertRaises(
                    ott.PlayerRejected if changed == 'expires-again' else search.SearchError):
                self.run_search(catalog_expired=ott.expired_epg_catalog)
            self.assertEqual(sum(call.args[1] == 'resolve_archive' for call in self.client.call.call_args_list),
                             2 if changed == 'expires-again' else 1)
        self.probe.assert_not_called()

    def test_unrelated_rejections_and_uncertain_transport_are_not_retried(self):
        message = 'Channels or provider changed. Retry the EPG query before playing.'
        for error in (ott.PlayerRejected('locked', {'error': 'Unlock this channel'}),
                      ott.TransportError(message), ott.PlayerUnsupported(message)):
            self.client.call.reset_mock()
            self.client.call.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_search(catalog_expired=ott.expired_epg_catalog)
            self.client.call.assert_called_once()
        self.probe.assert_not_called()

    def test_earliest_adjacent_programme_is_the_only_candidate_in_its_chain(self):
        data, targets = self.run_search()
        self.assertEqual(len(data["programs"]), 1)
        selected = data["programs"][0]
        self.assertEqual((selected["start"], selected["consecutive"]), (NOW - 600, 3))
        self.assertEqual(targets[(1, NOW - 600)]["start"], NOW - 600)
        self.assertEqual(self.client.call.call_args_list[0].args[1], "resolve_archive")
        self.assertNotIn("private", json.dumps(data))
        self.assertTrue(all("private" not in p.read_text() for p in search.CACHE.glob("*.json")))

    def test_unavailable_first_programme_advances_only_within_the_same_chain(self):
        self.probe.side_effect = [False, True]
        data, _ = self.run_search()
        self.assertEqual((data["programs"][0]["start"], data["programs"][0]["consecutive"]), (NOW - 500, 2))

    def test_nonmatching_programmes_gaps_and_overlaps_break_chains(self):
        rows = [row(-900, -800), row(-800, -700, "News"), row(-700, -600),
                row(-590, -500), row(-510, -400)]
        chains = search.archive_chains(search.clean_rows(rows), "орел решка", NOW, 144)
        self.assertEqual([len(chain) for chain in chains], [1, 1, 1, 1])

    def test_retention_cutoff_and_channels_without_archive_are_excluded(self):
        rows = [row(-144 * 3600 - 1, -144 * 3600 + 100), row(-100, -50)]
        self.assertEqual(len(search.archive_chains(rows, "решка", NOW, 144)), 1)
        self.assertEqual(search.archive_chains(rows, "решка", NOW, 0), [])
        self.assertEqual(search.archive_chains([row(-7201, -7100)], "решка", NOW, 2), [])

    def test_live_programme_is_preferred_without_any_archive_probe(self):
        self.guides.return_value = ([(CHANNEL, [row(-600, -500), row(-20, 100)])], NOW + 7200)
        data, targets = self.run_search()
        self.assertEqual(data["programs"][0]["mode"], "live")
        self.assertEqual(targets, {1: {"catalog": "receipt", "id": "cats"}})
        self.probe.assert_not_called()

    def test_newly_started_live_programme_supersedes_archive_after_slow_probe(self):
        self.guides.return_value = ([(CHANNEL, [row(-600, -500), row(10, 100)])], NOW + 7200)
        def slow(*args):
            self.clock.return_value = NOW + 20
            return True
        self.probe.side_effect = slow
        data, _ = self.run_search()
        self.assertEqual(data["programs"][0]["mode"], "live")

    def test_query_cache_and_weekly_availability_cache_reuse_without_sliding(self):
        self.run_search()
        self.clock.return_value = NOW + 3600
        self.run_search()
        self.assertEqual(self.guides.call_count, 1)
        self.clock.return_value = NOW + 86400
        self.run_search("решка")
        self.assertEqual(self.guides.call_count, 2)
        self.assertEqual(self.probe.call_count, 1)
        self.run_search(refresh=True)
        self.assertEqual(self.probe.call_count, 2)

    def test_future_match_boundary_invalidates_cached_archive(self):
        self.guides.return_value = ([(CHANNEL, [row(-600, -500), row(10, 100)])], NOW + 7200)
        self.run_search()
        self.clock.return_value = NOW + 20
        data, _ = self.run_search()
        self.assertEqual(data["programs"][0]["mode"], "live")

    def test_changed_catalogue_aborts_without_playback(self):
        self.client.call.side_effect = lambda device, action, params: (
            dict(SNAPSHOT, archive={"version": 1, "revision": "other"}) if action == "epg_catalog" else
            {"resolved": True, "url": "https://proxy.example/channel.m3u8"})
        with self.assertRaisesRegex(search.SearchError, "catalogue changed"):
            self.run_search()
        self.assertTrue(all(c.args[1] != "play_archive_catalog" for c in self.client.call.call_args_list))

    def test_old_client_cannot_claim_archives_are_absent(self):
        with self.assertRaisesRegex(search.SearchError, "Update this player"):
            self.run_search(snapshot={"catalog": "old", "channels": [CHANNEL]})
        self.guides.assert_not_called()

    def test_malformed_query_cache_is_rebuilt_without_playback(self):
        expected, _ = self.run_search()
        cache = next(search.CACHE.glob("query-*.json"))
        valid = expected["programs"][0]
        for programs in ([None], [{}], [dict(valid, id="missing")],
                         [dict(valid, number="1")], [dict(valid, archive_hours=1000)],
                         [dict(valid, title=None)], [dict(valid, consecutive=0)]):
            with self.subTest(programs=programs):
                envelope = json.loads(cache.read_text())
                envelope["data"]["programs"] = programs
                cache.write_text(json.dumps(envelope))
                self.guides.reset_mock()
                data, _ = self.run_search()
                self.assertEqual(data["programs"], expected["programs"])
                self.guides.assert_called_once()


class LaunchTest(unittest.TestCase):
    def test_no_channel_match_falls_back_to_epg_and_dispatches_archive_once(self):
        program = {"id": "cats", "channel": "Channel", "number": 1, "title": "Cats", "mode": "archive",
                   "start": NOW - 600, "end": NOW - 300, "consecutive": 3}
        target = {"catalog": "fresh", "id": "cats", "title": "Cats", "start": NOW - 600, "end": NOW - 300}
        client = Mock()
        client.device.return_value = "tv"
        client.call.side_effect = [{"channels": []}, {"dispatched": True,
            "channel": {"number": 1, "name": "Channel", "id": "cats"}, "start": NOW - 600, "end": NOW - 300}]
        with patch.object(ott, "read_json", return_value={"epg": SETTINGS}), patch.object(ott, "Client", return_value=client), \
             patch.object(ott.time, "time", return_value=NOW), \
             patch.object(ott, "server_programs", return_value=({"programs": [program]}, {(1, NOW - 600): target})) as epg, \
             contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ott.main(["tv", "cats"]), 0)
        self.assertEqual(client.call.call_args_list[-1].args, ("tv", "play_archive_catalog", target))
        self.assertEqual(client.call.call_count, 2)
        self.assertEqual(epg.call_args.args[3], "cats")
        self.assertIn("3 consecutive programmes", output.getvalue())

    def test_multiple_archive_chains_on_same_channel_remain_random_choices(self):
        rows = [{"number": 1, "channel": "Same", "title": "Cats", "mode": "archive", "start": NOW - 600, "end": NOW - 500},
                {"number": 1, "channel": "Same", "title": "Cats", "mode": "archive", "start": NOW - 400, "end": NOW - 300}]
        with patch.object(ott.time, "time", return_value=NOW), patch.object(ott.secrets, "choice", return_value=rows[1]):
            self.assertEqual(ott.select_programme(rows), rows[1])


class HistoryTransportTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(search, "CACHE", Path(directory)))
        self.clock = self.stack.enter_context(patch.object(search.time, "time", return_value=NOW))

    def test_guide_cache_expires_without_sliding_and_applies_player_shift_once(self):
        channel = dict(CHANNEL, shift=900)
        def response(base, path, payload=None):
            if path == "/match":
                self.assertEqual(set(payload["channels"][0]), {"id", "name", "tvgId", "tvgName"})
                return {"generation": "g1", "mappings": {"cats": {"channelId": "guide", "shift": 3600}}}
            self.assertIn("shift=3600", path)
            self.assertIn("hours=168", path)
            return {"generation": "g1", "rows": [row(-600, -500)]}
        with patch.object(search, "epg_request", side_effect=response) as request:
            guide, until = search.schedules(SETTINGS["url"], [channel])
            self.assertEqual(guide[0][1][0]["time"], NOW + 300)
            self.assertEqual(until, NOW + 7200)
            self.clock.return_value = NOW + 3600
            self.assertEqual(search.schedules(SETTINGS["url"], [channel])[1], until)
            self.assertEqual(request.call_count, 2)
            self.clock.return_value = NOW + 7201
            search.schedules(SETTINGS["url"], [channel])
            self.assertEqual(request.call_count, 4)

    def test_generation_change_restarts_mapping_before_history_is_accepted(self):
        responses = [
            {"generation": "g1", "mappings": {"cats": {"channelId": "guide", "shift": 0}}},
            search.GenerationChanged("Changed"),
            {"generation": "g2", "mappings": {"cats": {"channelId": "guide", "shift": 0}}},
            {"generation": "g2", "rows": [row(-600, -500)]},
        ]
        with patch.object(search, "epg_request", side_effect=responses) as request:
            guide, _ = search.schedules(SETTINGS["url"], [CHANNEL])
        self.assertEqual(guide[0][1], [row(-600, -500)])
        self.assertIn("generation=g2", request.call_args.args[1])

    def test_malformed_mapping_and_guide_cache_refetch_from_the_server(self):
        def response(base, path, payload=None):
            if path == "/match":
                return {"generation": "g1", "mappings": {"cats": {"channelId": "guide", "shift": 0}}}
            return {"generation": "g1", "rows": [row(-600, -500)]}
        with patch.object(search, "epg_request", side_effect=response) as request:
            search.schedules(SETTINGS["url"], [CHANNEL])
            for kind, invalid in (("mapping", {"until": "later"}),
                                  ("mapping", {"until": NOW + 100, "mappings": []}),
                                  ("mapping", {"until": NOW + 100, "generation": "g1", "mappings": {"cats": 3}}),
                                  ("guide", {"until": NOW + 100}),
                                  ("guide", {"until": NOW + 100, "rows": [None]})):
                with self.subTest(kind=kind, data=invalid):
                    cache = next(search.CACHE.glob(kind + "-*.json"))
                    envelope = json.loads(cache.read_text())
                    envelope["data"] = invalid
                    cache.write_text(json.dumps(envelope))
                    request.reset_mock()
                    guides, _ = search.schedules(SETTINGS["url"], [CHANNEL])
                    self.assertEqual(guides[0][1], [row(-600, -500)])
                    request.assert_called_once()


class MediaProbeTest(unittest.TestCase):
    def probe(self, documents):
        self.seen = []
        def open_request(request, **kwargs):
            self.seen.append(request.full_url)
            return io.BytesIO(documents[request.full_url])
        opener = Mock()
        opener.open.side_effect = open_request
        with patch.object(search, "build_opener", return_value=opener):
            return search.archive_available("https://proxy.example/master.m3u8", NOW - 600)

    def test_follows_hls_to_real_media_and_rejects_empty_playlist(self):
        documents = {
            "https://proxy.example/master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nmedia/index.m3u8\n",
            "https://proxy.example/media/index.m3u8": b"#EXTM3U\n#EXTINF:10,\n1.ts\n",
            "https://proxy.example/media/1.ts": (b"G" + b"x" * 187) * 3,
        }
        self.assertTrue(self.probe(documents))
        self.assertEqual(len(self.seen), 3)
        documents["https://proxy.example/media/1.ts"] = b"<html>Unavailable</html>"
        self.assertFalse(self.probe(documents))
        documents["https://proxy.example/media/index.m3u8"] = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n"
        self.assertFalse(self.probe(documents))

    def test_rejects_cross_origin_segments_and_live_fallback_timestamps(self):
        documents = {"https://proxy.example/master.m3u8": b"#EXTM3U\n#EXTINF:10,\nhttps://other.example/1.ts\n"}
        self.assertFalse(self.probe(documents))
        self.assertEqual(len(self.seen), 1)
        documents["https://proxy.example/master.m3u8"] = (
            b"#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2020-01-01T00:00:00Z\n#EXTINF:10,\n1.ts\n")
        self.assertFalse(self.probe(documents))
        self.assertEqual(len(self.seen), 1)


if __name__ == "__main__":
    unittest.main()
