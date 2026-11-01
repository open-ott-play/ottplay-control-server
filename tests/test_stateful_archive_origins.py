"""Explicit proxy policy must avoid media side effects without inventing availability."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_programme_search import search, ott, CHANNEL, SNAPSHOT, SETTINGS, NOW, row
from test_playlist_search import search as playlist, ENTRY, REQUEST


policy = search.archive_origin_policy()
ORIGINS = ["https://PROXY.example:443/"]
PRIVATE_URL = "https://proxy.example/private/channel.m3u8?token=private"


class OriginPolicyTests(unittest.TestCase):
    def test_canonical_origins_are_exact_and_do_not_resolve_host_aliases(self):
        values = ["HTTP://EXAMPLE.COM:80/", "https://Example.COM.:443",
                  "http://[0:0:0:0:0:0:0:1]:8090/", "https://bücher.example/"]
        self.assertEqual(policy.stateful_archive_origins(values),
                         ["http://example.com", "https://example.com", "http://[::1]:8090",
                          "https://xn--bcher-kva.example"])
        self.assertEqual(policy.stateful_archive_origins([values[0], "http://example.com"]),
                         ["http://example.com"])
        for url in ("http://localhost:8090/a", "http://127.0.0.1:8091/a", "https://127.0.0.1:8090/a"):
            self.assertFalse(search.defer_archive_probe(url, ["http://127.0.0.1:8090"]))
        self.assertTrue(search.defer_archive_probe("https://proxy.example.:443/a?token=private",
                                                  ["https://proxy.example"]))

    def test_invalid_origins_are_rejected_before_player_or_network_io(self):
        invalid = [None, "https://proxy.example", {}, [None], [""], ["file:///tmp"],
                   ["https://user:private@proxy.example"], ["https://proxy.example/path"],
                   ["https://proxy.example//"], ["https://proxy.example?"], ["https://proxy.example#"],
                   ["https://proxy.example:0"], ["https://proxy.example:65536"],
                   ["https://proxy.example:"], ["https://proxy.example.."],
                   ["https://proxy.example\n"], ["https://proxy.example\u00a0"],
                   ["http://[::1%lo0]"], ["http://127.1"], ["https://bad_host"],
                   ["https://proxy.example"] * 33]
        for value in invalid:
            client = Mock(timeout=45)
            with self.subTest(value=value), patch.object(ott, "_server_programs") as execute:
                with self.assertRaises(ott.Error) as error:
                    ott.server_programs(client, "tv", dict(SETTINGS, stateful_archive_origins=value), "cats")
                self.assertNotIn("private", str(error.exception))
                execute.assert_not_called()
                client.call.assert_not_called()
            with patch.object(ott.urllib.request, "build_opener") as opener:
                with self.assertRaises(ott.Error):
                    ott.EpgClient(dict(SETTINGS, stateful_archive_origins=value), 10)
                opener.assert_not_called()

    def test_primary_and_fallback_preserve_independent_policy_lists(self):
        original = dict(SETTINGS, fallback_url="http://127.0.0.1:18792/epg/v1", stateful_archive_origins=ORIGINS)
        primary, fallback = policy.split_settings(original)
        self.assertEqual(primary["stateful_archive_origins"], ["https://proxy.example"])
        self.assertEqual(fallback["stateful_archive_origins"], primary["stateful_archive_origins"])
        primary["stateful_archive_origins"].append("https://other.example")
        self.assertEqual(fallback["stateful_archive_origins"], ["https://proxy.example"])
        self.assertEqual(original["stateful_archive_origins"], ORIGINS)
        with patch.object(ott, "_server_programs", side_effect=[ott.EpgTransportError("offline"), ({}, {})]) as run:
            ott.server_programs(Mock(timeout=45), "tv", original, "cats")
            self.assertEqual([c.args[2]["stateful_archive_origins"] for c in run.call_args_list],
                             [["https://proxy.example"], ["https://proxy.example"]])
            self.assertEqual(run.call_args_list[0].args[-1], run.call_args_list[1].args[-1])

    def test_policy_is_never_sent_to_public_epg_transport(self):
        client = ott.EpgClient(dict(SETTINGS, stateful_archive_origins=ORIGINS), 10)
        with patch.object(client, "_current", return_value={}) as send:
            client.current([], "cats")
            client.search([], "cats", generation="one")
        for call in send.call_args_list:
            payload = json.loads(call.args[0])
            self.assertNotIn("stateful_archive_origins", payload)
            self.assertNotIn("proxy.example", str(payload))


class StatefulArchiveTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(search, "CACHE", self.directory))
        self.stack.enter_context(patch.object(search.time, "time", return_value=NOW))
        self.client = Mock(config={"server": "https://control.example"}, timeout=45)
        self.client.call.side_effect = lambda device, action, params: (
            copy.deepcopy(SNAPSHOT) if action == "epg_catalog" else {"resolved": True, "url": PRIVATE_URL})
        self.guides = self.stack.enter_context(patch.object(search, "schedules", return_value=(
            [(CHANNEL, [row(-600, -500), row(-500, -400)])], NOW + 60)))
        self.settings = dict(SETTINGS, stateful_archive_origins=ORIGINS)

    def run_search(self, **kwargs):
        return search.search_archives(self.client, "tv", self.settings, SNAPSHOT, "орел решка", **kwargs)

    def test_no_media_http_and_no_verified_cache_even_after_repeated_search(self):
        with patch.object(search, "archive_available") as probe, patch.object(search, "build_opener") as http, \
                patch.object(search, "remember", wraps=search.remember) as cache, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            for _ in range(2):
                data, targets = self.run_search()
                self.assertEqual(data["programs"][0]["availability"], "deferred")
                self.assertFalse(data["verified_all"])
                self.assertEqual(targets[(1, NOW - 600)]["catalog"], "receipt")
            probe.assert_not_called()
            http.assert_not_called()
            cache.assert_not_called()
        self.assertIn("deferred to playback", errors.getvalue())
        self.assertNotIn("private", errors.getvalue() + json.dumps(data))
        self.assertEqual([c.args[1] for c in self.client.call.call_args_list],
                         ["resolve_archive", "epg_catalog"] * 2)

    def test_old_query_and_true_availability_caches_cannot_bypass_classification(self):
        identity = [self.client.config["server"], "tv", "stable-source"]
        search.remember("archive", [identity, "cats", NOW - 600], True)
        cached_program = {"id": "cats", "channel": "Channel", "number": 1, "title": "Орёл и решка",
                          "start": NOW - 500, "end": NOW - 400, "mode": "archive", "consecutive": 1,
                          "archive_hours": 144}
        search.remember("query", [SETTINGS["url"], identity, search.text_key("орел решка")],
                        {"programs": [cached_program], "until": NOW + 60})
        with patch.object(search, "archive_available") as probe, contextlib.redirect_stderr(io.StringIO()):
            data, _ = self.run_search()
        self.assertEqual(data["programs"][0]["start"], NOW - 600)
        self.assertEqual(self.client.call.call_args_list[0].args[1], "resolve_archive")
        probe.assert_not_called()
        self.guides.assert_called_once()

    def test_unmatched_origin_still_probes_and_propagates_uncertain_failure(self):
        self.settings["stateful_archive_origins"] = ["https://proxy.example:8443"]
        with patch.object(search, "archive_available", return_value=True) as probe:
            data, _ = self.run_search(fast=True)
        probe.assert_called_once()
        self.assertEqual(data["selection"], "random_first_available")
        self.assertNotIn("availability", data["programs"][0])
        with patch.object(search, "archive_available", side_effect=search.ArchiveTransientError("temporary")):
            with self.assertRaises(search.ArchiveTransientError):
                self.run_search(fast=True)

    def test_compact_candidates_keep_eligibility_label_and_final_receipt_gate(self):
        prepared = {"current": [], "chains": [(CHANNEL, [row(-600, -500)])], "until": NOW + 60}
        progress = Mock()
        with patch.object(search, "archive_available") as probe:
            data, _ = self.run_search(fast=True, prepared=prepared, refresh_current=lambda: [], progress=progress)
        self.assertEqual(data["selection"], "random_first_eligible")
        self.assertIn("deferred to playback", str(progress.call_args_list))
        probe.assert_not_called()
        changed = copy.deepcopy(SNAPSHOT)
        changed["archive"]["revision"] = "changed"
        self.client.call.side_effect = [{"resolved": True, "url": PRIVATE_URL}, changed]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(search.SearchError):
            self.run_search()

    def test_invalid_resolved_url_is_not_made_trusted_by_origin_policy(self):
        self.client.call.side_effect = lambda *_: {"resolved": True, "url": "https://user:private@proxy.example/a"}
        with patch.object(search, "archive_available") as probe, self.assertRaises(search.SearchError):
            self.run_search()
        probe.assert_not_called()


class StatefulPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(playlist.time, "time", return_value=NOW))
        self.stack.enter_context(patch.object(playlist, "load_playlist", return_value=[ENTRY]))
        self.guides = self.stack.enter_context(patch.object(playlist.history, "schedules", side_effect=lambda base, channels, *a, **k:
            ([(channels[0], [row(-600, -500, "Great Journey")])], NOW + 60)))
        self.request = dict(REQUEST, cache_dir=self.directory, stateful_archive_origins=["http://192.0.2.10:9999"])

    def test_playlist_policy_avoids_all_media_and_does_not_cache_deferred_choice(self):
        with patch.object(playlist.history, "archive_available") as probe, \
                patch.object(playlist.Cache, "write") as cache, contextlib.redirect_stderr(io.StringIO()) as errors:
            first = playlist.resolve(self.request)
            second = playlist.resolve(self.request)
        self.assertEqual(first["matches"][0]["availability"], "deferred")
        self.assertEqual(second, first)
        self.assertEqual(self.guides.call_count, 2)
        probe.assert_not_called()
        cache.assert_not_called()
        self.assertIn("deferred to playback", errors.getvalue())
        self.assertNotIn("private", errors.getvalue())

    def test_playlist_old_caches_do_not_hide_stateful_origin(self):
        with patch.object(playlist.history, "archive_available", return_value=True):
            ordinary = dict(self.request)
            ordinary.pop("stateful_archive_origins")
            playlist.resolve(ordinary)
        with patch.object(playlist.history, "archive_available") as probe, contextlib.redirect_stderr(io.StringIO()):
            result = playlist.resolve(self.request)
        self.assertEqual(self.guides.call_count, 2)
        self.assertEqual(result["matches"][0]["availability"], "deferred")
        probe.assert_not_called()

    def test_playlist_invalid_policy_precedes_even_catalogue_fetch(self):
        with patch.object(playlist, "load_playlist") as load, self.assertRaises(playlist.SearchError):
            playlist.resolve(dict(self.request, stateful_archive_origins=["http://proxy.example/private"]))
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
