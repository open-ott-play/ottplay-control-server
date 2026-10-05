"""Behaviour migrated from VL's playlist, EPG and cache regression tests."""
import contextlib
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

spec = importlib.util.spec_from_file_location("playlist_search_tests",
    Path(__file__).resolve().parents[1] / "cli" / "playlist_search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)
history = search.history
NOW = 1800000000
URL = "http://192.0.2.10:9999/playlist.m3u8"
REQUEST = {"version": 1, "query": "great journey", "playlist_url": URL, "epg_url": "https://epg.example/v1"}
PLAYLIST = '''#EXTM3U
#EXTINF:-1 tvg-name="Travel, World" group-title="Docs, Travel",World Travel HD
/channel/one.m3u8
#EXTINF:-1 group-title="News",News HD
/channel/two.m3u8
#EXTINF:-1 group-title="Docs",Travel World SD
channel/three.m3u8
'''
ENTRY = {"name": "Channel", "url": "http://192.0.2.10:9999/channel/one.m3u8?token=private",
         "group": "TV", "archive_hours": 144, "search": "channel", "epg_shift": 0,
         "tvg_id": "one", "tvg_name": "Channel"}


def row(start, end, title="Great Journey"):
    return {"name": title, "time": NOW + start, "time_to": NOW + end}


class PlaylistTests(unittest.TestCase):
    def fetch(self, text, query):
        return [entry for entry in search.parse_playlist(text, URL) if history.matches(entry["search"], query)]

    def test_quoted_attribute_commas_relative_links_and_all_word_search(self):
        matches = self.fetch("\ufeff" + PLAYLIST, "WORLD travel")
        self.assertEqual([e["name"] for e in matches], ["World Travel HD", "Travel World SD"])
        self.assertEqual(matches[0]["group"], "Docs, Travel")
        self.assertEqual(matches[1]["url"], "http://192.0.2.10:9999/channel/three.m3u8")
        self.assertEqual(len(self.fetch(PLAYLIST, "travel HD")), 1)
        self.assertEqual(self.fetch(PLAYLIST, "missing"), [])

    def test_unicode_normalization_tvg_alias_and_group_exclusion(self):
        text = '#EXTM3U\n#EXTINF:-1 tvg-name="Орёл и Решка",Travel Show\n/channel/one.m3u8\n'
        self.assertEqual(len(self.fetch(text, "орел решка")), 1)
        self.assertEqual(self.fetch(PLAYLIST, "Docs"), [])

    def test_fractional_retention_and_shift_are_preserved_and_bounded(self):
        def entry(rec):
            return self.fetch('#EXTM3U\n#EXTINF:-1 tvg-id="one" tvg-rec="' + rec +
                              '" tvg-shift="2.5",Travel\n/one.m3u8\n', "Travel")[0]
        self.assertEqual((entry("7")["tvg_id"], entry("7")["archive_hours"], entry("7")["epg_shift"]),
                         ("one", 144, 9000))
        self.assertEqual(entry("0.125")["archive_hours"], 3)
        for value in ("NaN", "Infinity", "-1", "bad"):
            self.assertEqual(entry(value)["archive_hours"], 0)

    def test_title_commas_terminal_controls_and_extgrp(self):
        text = '#EXTM3U\n#EXTINF:-1,Title, part two\x1b[0m\n#EXTGRP:TV\x1b[31m\n/channel/one.m3u8\n'
        entry = search.parse_playlist(text, URL)[0]
        self.assertIn("Title, part two", entry["name"])
        self.assertNotIn("\x1b", entry["name"] + entry["group"])

    def test_invalid_documents_or_external_urls_do_not_become_targets(self):
        for text in ("<html>Login</html>", "#EXTM3U\n", "#EXTM3U\n/channel/no-title.m3u8\n"):
            with self.subTest(text=text), self.assertRaises(search.SearchError):
                self.fetch(text, "test")
        for url in ("file:///private.txt", "rtsp://camera/live", "https://other.invalid/private-token",
                    "http://user:password@192.0.2.10:9999/one.m3u8", "http://192.0.2.10:9999/one#fragment",
                    "http://192.0.2.10:0/one", "http://192.0.2.10:9999/one\x7f"):
            with self.subTest(url=url), self.assertRaises(search.SearchError) as error:
                self.fetch("#EXTM3U\n#EXTINF:-1,Test\n" + url + "\n", "Test")
            self.assertNotIn(url, str(error.exception))

    def test_foreign_acestream_and_malformed_entries_do_not_poison_playlist(self):
        for url in ("http://192.0.2.10:6878/ace/private", "file:///private.txt", "rtsp://camera/live",
                    "http://192.0.2.10:bad/one", "http://[invalid"):
            text = PLAYLIST + "#EXTINF:-1,Unsupported\n" + url + "\n/channel/orphan.m3u8\n"
            self.assertEqual(len(self.fetch(text, "travel")), 2)
            self.assertEqual(self.fetch(text, "Unsupported"), [])

    def test_archive_url_replaces_timestamps_and_retains_authorization(self):
        entry = dict(ENTRY, url=ENTRY["url"] + "&utc=old&lutc=old&blank=")
        query = parse_qs(urlsplit(search.archive_url(entry, NOW - 100, NOW)).query, keep_blank_values=True)
        self.assertEqual(query, {"token": ["private"], "blank": [""], "utc": [str(NOW - 100)], "lutc": [str(NOW)]})

    def test_invalid_requests_fail_before_network(self):
        invalid = [{}, dict(REQUEST, version=True), dict(REQUEST, query="---!!!"), dict(REQUEST, query="я" * 513),
                   dict(REQUEST, refresh=1), dict(REQUEST, cache_seconds=True), dict(REQUEST, cache_seconds=float("inf")),
                   dict(REQUEST, cache_seconds=-1), dict(REQUEST, extra=True), dict(REQUEST, playlist_url="file:///private"),
                   dict(REQUEST, epg_url="https://epg.example/?private"), dict(REQUEST, cache_dir="\0"),
                   dict(REQUEST, epg_url=False), dict(REQUEST, epg_url=0), dict(REQUEST, epg_url=None)]
        with patch.object(search, "load_playlist") as load:
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(search.SearchError):
                    search.resolve(request)
            load.assert_not_called()


class CacheAndSelectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.request = dict(REQUEST, cache_dir=str(self.root))
        self.clock = self.stack.enter_context(patch.object(search.time, "time", return_value=NOW))
        self.cache = search.Cache(self.request)

    def guides(self, rows, entry=ENTRY, until=None):
        self.playlist = self.stack.enter_context(patch.object(search, "load_playlist", return_value=[entry]))
        def get(base, channels, *args, **kwargs):
            return [(channels[0], rows)], NOW + 7200 if until is None else until
        guides = self.stack.enter_context(patch.object(history, "schedules", side_effect=get))
        probe = self.stack.enter_context(patch.object(history, "archive_available", return_value=True))
        return guides, probe

    def resolve(self, query="great journey", **kwargs):
        return search.resolve(dict(self.request, query=query, **kwargs))

    def test_channel_title_precedes_all_epg_and_resolver_writes_no_stdout(self):
        guides, probe = self.guides([row(-100, 100)])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.resolve("channel")
        self.assertEqual((result["kind"], len(result["matches"])), ("playlist", 1))
        self.assertEqual(output.getvalue(), "")
        guides.assert_not_called()
        probe.assert_not_called()

    def test_current_programme_on_nonarchive_channel_precedes_archives(self):
        _, probe = self.guides([row(-100, 100)], dict(ENTRY, archive_hours=0))
        result = self.resolve()
        self.assertEqual(result["kind"], "live")
        self.assertEqual(result["matches"][0]["programme_start"], NOW - 100)
        probe.assert_not_called()

    def test_earliest_available_programme_in_each_chain_and_no_parallel_probes(self):
        _, probe = self.guides([row(-600, -500), row(-500, -400), row(-400, -300),
                                row(-300, -200, "News"), row(-200, -100)])
        probe.side_effect = [False, True, True]
        result = self.resolve()
        self.assertEqual([e["programme_start"] for e in result["matches"]], [NOW - 500, NOW - 200])
        self.assertIn("2 consecutive programmes", result["matches"][0]["details"])
        self.assertEqual([call.args[1] for call in probe.call_args_list], [NOW - 600, NOW - 500, NOW - 200])
        self.assertTrue(all(call.kwargs == {"require_hls": True, "allow_redirects": True} for call in probe.call_args_list))

    def test_nonmatching_gaps_overlaps_and_retention_break_chains(self):
        rows = [row(-900, -800), row(-800, -700, "News"), row(-700, -600), row(-590, -500), row(-510, -400)]
        self.assertEqual([len(c) for c in history.archive_chains(rows, "journey", NOW, 144)], [1, 1, 1, 1])
        self.assertEqual(history.archive_chains(rows, "journey", NOW, 0), [])
        self.assertEqual(history.archive_chains([row(-10801, -10700)], "journey", NOW, 3), [])

    def test_live_start_during_slow_archive_probe_supersedes_archive(self):
        _, probe = self.guides([row(-200, -100), row(10, 40)])
        def slow(*args, **kwargs):
            self.clock.return_value = NOW + 11
            return True
        probe.side_effect = slow
        self.assertEqual(self.resolve()["kind"], "live")

    def test_same_query_cache_avoids_epg_and_probes_but_refresh_bypasses(self):
        guides, probe = self.guides([row(-200, -100)])
        first = self.resolve()
        self.clock.return_value = NOW + 20
        second = self.resolve("GREAT, Journey")
        self.assertEqual(first["matches"][0]["programme_start"], second["matches"][0]["programme_start"])
        self.assertIn("lutc=" + str(NOW + 20), second["matches"][0]["url"])
        self.assertEqual((guides.call_count, probe.call_count), (1, 1))
        self.resolve(refresh=True)
        self.assertEqual((guides.call_count, probe.call_count), (2, 2))

    def test_query_expires_at_future_matching_start_or_current_end(self):
        guides, probe = self.guides([row(-200, -100), row(10, 40)])
        self.assertEqual(self.resolve()["kind"], "archive")
        self.clock.return_value = NOW + 11
        self.assertEqual(self.resolve()["kind"], "live")
        self.clock.return_value = NOW + 41
        self.assertEqual(self.resolve()["kind"], "archive")
        self.assertEqual(guides.call_count, 3)

    def test_cached_archive_cannot_outlive_retention(self):
        guides, probe = self.guides([row(-144 * 3600 + 10, -144 * 3600 + 100)])
        self.assertEqual(self.resolve()["kind"], "archive")
        self.clock.return_value = NOW + 11
        self.assertEqual(self.resolve()["kind"], "none")
        self.assertEqual((guides.call_count, probe.call_count), (2, 1))

    def test_probe_cache_shared_across_queries_without_sliding_age(self):
        _, probe = self.guides([row(-1000, -800)])
        self.resolve("great")
        self.clock.return_value = NOW + 3600
        self.resolve("journey")
        self.assertEqual(probe.call_count, 1)
        key = [search.channel_key(ENTRY), NOW - 1000]
        self.clock.return_value = NOW + 604799
        self.assertIs(self.cache.read("playlist-archive", key), True)
        self.clock.return_value += 1
        self.assertIsNone(self.cache.read("playlist-archive", key))

    def test_archive_cache_survives_only_proxy_timestamp_query_changes(self):
        old = dict(ENTRY, url="http://192.0.2.10:9999/channel/one/index.m3u8?q=1800000000000&token=private")
        new = dict(old, url=old["url"].replace("1800000000000", "1800086400000"))
        _, probe = self.guides([row(-1000, -800)], old)
        self.resolve()
        self.clock.return_value = NOW + 86400
        self.playlist.return_value = [new]
        result = self.resolve()
        self.assertEqual(probe.call_count, 1)
        self.assertIn("q=1800086400000", result["matches"][0]["url"])
        for token in ("other", ""):
            self.assertNotEqual(search.channel_key(old), search.channel_key(dict(old, url=old["url"].replace("private", token))))
        self.assertNotEqual(search.channel_key(old), search.channel_key(dict(old, url=old["url"].replace("1800000000000", "hd"))))

    def test_query_cannot_extend_underlying_epg_age(self):
        guides, _ = self.guides([row(-1000, -800)], until=NOW + 5)
        self.resolve()
        self.clock.return_value = NOW + 6
        self.resolve()
        self.assertEqual(guides.call_count, 2)

    def test_long_initial_scan_still_produces_a_reusable_cache(self):
        guides, probe = self.guides([row(-1000, -800)])
        def slow(*args, **kwargs):
            self.clock.return_value = NOW + 360
            return True
        probe.side_effect = slow
        first = self.resolve()
        self.assertEqual(self.resolve(), first)
        self.assertEqual((guides.call_count, probe.call_count), (1, 1))

    def test_archive_expiring_during_probe_is_not_returned(self):
        _, probe = self.guides([row(-144 * 3600 + 10, -144 * 3600 + 100)])
        def slow(*args, **kwargs):
            self.clock.return_value = NOW + 11
            return True
        probe.side_effect = slow
        self.assertEqual(self.resolve()["kind"], "none")

    def test_unavailable_archive_does_not_create_negative_result_cache(self):
        guides, probe = self.guides([row(-200, -100)])
        probe.return_value = False
        self.assertEqual(self.resolve()["kind"], "none")
        probe.return_value = True
        self.assertEqual(self.resolve()["kind"], "archive")
        self.assertEqual((guides.call_count, probe.call_count), (2, 2))

    def test_bad_cached_choice_is_recomputed_without_trusting_saved_urls(self):
        guides, _ = self.guides([row(-200, -100)])
        self.resolve()
        path = next(self.root.glob("playlist-query-*.json"))
        saved = json.loads(path.read_text())
        self.assertNotIn("private", json.dumps(saved))
        saved["data"]["choices"][0]["index"] = 999
        path.write_text(json.dumps(saved))
        self.resolve()
        self.assertEqual(guides.call_count, 2)

    def test_weekly_playlist_ttl_and_private_permissions(self):
        text = PLAYLIST.encode()
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(text)
        with patch.object(history, "same_origin_opener", return_value=opener):
            first = search.load_playlist(self.request, self.cache)
            self.clock.return_value = NOW + 604799
            self.assertEqual(search.load_playlist(self.request, self.cache), first)
            self.assertEqual(opener.open.call_count, 1)
            self.clock.return_value += 1
            search.load_playlist(self.request, self.cache)
            search.load_playlist(self.request, search.Cache(dict(self.request, refresh=True)))
            self.assertEqual(opener.open.call_count, 3)
        path = history.cache_path("playlist", URL, self.root)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private", path.name)

    def test_cache_bounds_zero_disable_corruption_and_future_dates(self):
        cache = search.Cache(dict(self.request, cache_seconds=604800, playlist_cache_seconds=604801))
        self.assertEqual((cache.guide_ttl, cache.playlist_ttl, cache.archive_ttl), (7200, 604800, 604800))
        cache = search.Cache(dict(self.request, cache_seconds=0))
        for kind in ("mapping", "guide", "playlist-query", "playlist", "playlist-archive"):
            cache.write(kind, "key", True)
            self.assertIsNone(cache.read(kind, "key"))
        self.assertEqual(list(self.root.iterdir()), [])
        self.cache.write("playlist", "key", True)
        self.clock.return_value = NOW - 1
        self.assertIsNone(self.cache.read("playlist", "key"))
        history.cache_path("playlist", "key", self.root).write_text("broken")
        self.assertIsNone(self.cache.read("playlist", "key"))

    def test_download_error_and_size_limit_are_safe(self):
        opener = Mock()
        opener.open.side_effect = OSError("private-secret")
        with patch.object(history, "same_origin_opener", return_value=opener):
            with self.assertRaises(search.SearchError) as error:
                search.load_playlist(self.request, self.cache)
            self.assertNotIn("private", str(error.exception))
            opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(b"x" * (8 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(search.SearchError, "8 MiB"):
                search.load_playlist(self.request, self.cache)

    def test_unwritable_cache_does_not_prevent_resolution(self):
        bad_directory = self.root / "file"
        bad_directory.write_text("not a directory")
        self.guides([row(-200, -100)])
        self.assertEqual(self.resolve(cache_dir=str(bad_directory))["kind"], "archive")


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.clock = self.stack.enter_context(patch.object(history.time, "time", return_value=NOW))
        self.cache = search.Cache(dict(REQUEST, cache_dir=str(self.root)))
        self.channels = [{"id": str(i), "name": "Channel " + str(i), "tvgId": "one", "tvgName": "",
                          "shift": 9000, "archiveHours": 144, "index": i} for i in range(201)]

    def envelope(self, **fields):
        return dict(version=1, source="epg-one", generation="g1", stale=False, **fields)

    def response(self, base, path, payload=None, **kwargs):
        if path == "/match":
            return self.envelope(mappings={row["id"]: {"channelId": "one", "shift": 7200}
                                          for row in payload["channels"]})
        self.assertEqual(parse_qs(urlsplit(path).query),
                         {"channelId": ["one"], "shift": ["7200"], "hours": ["168"], "generation": ["g1"]})
        return self.envelope(rows=[row(-1000, -800)])

    def schedules(self, **kwargs):
        return history.schedules(REQUEST["epg_url"], self.channels, cache=self.cache, attempts=3, **kwargs)

    def test_metadata_batches_deduplicate_schedules_and_preserve_playlist_shift(self):
        with patch.object(history, "epg_request", side_effect=self.response) as fetch:
            guide, until = self.schedules()
            self.assertEqual(fetch.call_count, 4)
            self.assertEqual(len(guide), 201)
            self.assertEqual(guide[0][1][0]["time"], NOW - 1000 + 9000)
            self.assertEqual(until, NOW + 7200)
            payloads = [call.args[2] for call in fetch.call_args_list if len(call.args) == 3]
            self.assertEqual([len(p["channels"]) for p in payloads], [100, 100, 1])
            self.assertTrue(all(set(c) == {"id", "name", "tvgId", "tvgName"} for p in payloads for c in p["channels"]))

    def test_mapping_and_schedule_cache_expire_without_sliding_and_refresh_bypasses(self):
        with patch.object(history, "epg_request", side_effect=self.response) as fetch:
            self.schedules()
            self.clock.return_value = NOW + 7199
            _, until = self.schedules()
            self.assertEqual((fetch.call_count, until), (4, NOW + 7200))
            self.clock.return_value += 1
            self.schedules()
            self.assertEqual(fetch.call_count, 8)
            self.schedules(refresh=True)
            self.assertEqual(fetch.call_count, 12)

    def test_generation_conflict_rematches_once_and_repeated_conflict_fails(self):
        self.channels = self.channels[:1]
        responses = [self.response("", "/match", {"channels": self.channels}), history.GenerationChanged("changed"),
                     self.response("", "/match", {"channels": self.channels}), self.envelope(rows=[row(-1000, -800)])]
        with patch.object(history, "epg_request", side_effect=responses) as fetch:
            self.assertEqual(len(self.schedules()[0]), 1)
            self.assertEqual(fetch.call_count, 4)
        self.cache.refresh = True
        responses[-1] = history.GenerationChanged("changed")
        with patch.object(history, "epg_request", side_effect=responses), self.assertRaises(history.GenerationChanged):
            self.schedules()

    def test_zero_ttl_loads_fresh_without_cache_files(self):
        self.cache = search.Cache(dict(REQUEST, cache_dir=str(self.root), cache_seconds=0))
        with patch.object(history, "epg_request", side_effect=self.response) as fetch:
            self.schedules(ttl=0)
            self.schedules(ttl=0)
            self.assertEqual(fetch.call_count, 8)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_bad_rows_or_mappings_fail_instead_of_scanning_partial_archives(self):
        self.channels = self.channels[:1]
        for invalid in (self.envelope(mappings={"0": {"channelId": "one", "shift": 1}}),
                        self.envelope(mappings=[])):
            with self.subTest(invalid=invalid), patch.object(history, "epg_request", return_value=invalid), \
                    self.assertRaises(history.SearchError):
                self.schedules(refresh=True)
        for invalid in ([{}], [{"name": "Show", "time": 100, "time_to": 10}], [None]):
            with self.subTest(invalid=invalid), self.assertRaises(history.SearchError):
                history.clean_rows(invalid)

    def test_rows_are_sorted_and_deduplicated_without_removing_nonmatches(self):
        rows = [row(-200, -100), row(-400, -300), row(-300, -200, "News"), row(-400, -300)]
        self.assertEqual([r["name"] for r in history.clean_rows(rows)], ["Great Journey", "News", "Great Journey"])


class TransportTests(unittest.TestCase):
    def test_epg_retries_transient_errors_only_without_leaking_urls(self):
        envelope = {"version": 1, "source": "epg-one", "generation": "g1", "stale": False}
        opener = Mock()
        opener.open.side_effect = [OSError("private"), io.BytesIO(json.dumps(envelope).encode())]
        with patch.object(history, "build_opener", return_value=opener), patch.object(history.time, "sleep"):
            self.assertEqual(history.epg_request("https://epg.example", "/match", {}, attempts=3), envelope)
        opener.open.side_effect = HTTPError("private", 403, "denied", {}, io.BytesIO())
        opener.open.reset_mock()
        with patch.object(history, "build_opener", return_value=opener), self.assertRaises(search.SearchError) as error:
            history.epg_request("https://epg.example", "/match", {}, attempts=3)
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_stale_and_invalid_epg_envelopes_are_rejected(self):
        for value in ({"version": 1, "source": "epg-one", "generation": "g1", "stale": True},
                      {"version": 1, "source": "other", "generation": "g1", "stale": False}, [], None):
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps(value).encode())
            with self.subTest(value=value), patch.object(history, "build_opener", return_value=opener), \
                    self.assertRaises(history.SearchError):
                history.epg_request("https://epg.example/private", "/match", {})

    def test_local_probe_requires_hls_and_real_media_and_rejects_live_fallback(self):
        ts = bytes([0x47]) + bytes(187)
        ts *= 3
        cases = [(ts, False), (b"<html>login</html>", False), (b"#EXTM3U\n", False),
                 (b"#EXTM3U\n#EXTINF:4,\nhttps://foreign.example/private.ts\n", False),
                 (b"#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2020-01-01T00:00:00Z\n#EXTINF:4,\nseg.ts\n", False),
                 (b"#EXTM3U\n#EXTINF:4,\nseg.ts\n", True)]
        for manifest, expected in cases:
            def response(raw, url):
                stream = io.BytesIO(raw)
                stream.geturl = lambda: url
                return stream
            opener = Mock()
            opener.open.side_effect = [response(manifest, ENTRY["url"]), response(ts, ENTRY["url"])]
            with self.subTest(manifest=manifest[:40]), patch.object(history, "same_origin_opener", return_value=opener):
                self.assertEqual(history.archive_available(ENTRY["url"], NOW, require_hls=True, allow_redirects=True), expected)

    def test_same_origin_redirects_use_final_relative_base_and_foreign_redirects_are_not_fetched(self):
        ts = (bytes([0x47]) + bytes(187)) * 3
        requested = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                if self.path in ("/master", "/cross"):
                    self.send_response(302)
                    self.send_header("Location", "/real/master.m3u8" if self.path == "/master" else
                                     "http://localhost:%s/private" % self.server.server_port)
                    self.end_headers()
                    return
                data = {"/real/master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvariant.m3u8\n",
                        "/real/variant.m3u8": b"#EXTM3U\n#EXTINF:4,\nseg.ts\n", "/real/seg.ts": ts}.get(self.path, b"")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:%s" % server.server_port
            self.assertTrue(history.archive_available(base + "/master", NOW, require_hls=True, allow_redirects=True))
            self.assertEqual(requested, ["/master", "/real/master.m3u8", "/real/variant.m3u8", "/real/seg.ts"])
            requested.clear()
            self.assertFalse(history.archive_available(base + "/cross", NOW, require_hls=True, allow_redirects=True))
            self.assertEqual(requested, ["/cross"])
            self.assertFalse(history.archive_available(base + "/master", NOW))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
