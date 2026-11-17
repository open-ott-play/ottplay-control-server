"""Exact-name multi-view shortcuts use fresh existing tickets, with no live IO."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("multiview_names_tests", ROOT / "cli/multiview.py")
mv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mv)
FIXTURE = json.loads((ROOT / "contracts/multiview-v1.json").read_text())


class Error(Exception):
    pass


class PlayerRejected(Error):
    def __init__(self, data):
        super().__init__("PRIVATE transport detail")
        self.data = data


class PlayerUnsupported(PlayerRejected):
    pass


class HTTPError(Error):
    code = 400


API = SimpleNamespace(Error=Error, PlayerRejected=PlayerRejected, PlayerUnsupported=PlayerUnsupported,
                      HTTPError=HTTPError, capabilities_metadata=copy.deepcopy)


def source(identity="news", label="News HD", provider="m3u", profile="home", kind="live"):
    return dict(source_id=identity, label=label, provider=provider, profile=profile, kind=kind)


class Client:
    def __init__(self, pages=None):
        self.timeout, self.calls = 45, []
        self.pages = pages or [[source()]]
        self.marker = copy.deepcopy(FIXTURE["capability"]["multiview"])
        self.snapshot = copy.deepcopy(FIXTURE["results"]["status_bound"]["snapshot"])
        self.snapshot.update(layout="grid4", satellites=0)
        template = self.snapshot["slots"][0]
        self.snapshot["slots"] = [dict(copy.deepcopy(template), slot_id=i,
                                       slot_generation="generation-" + str(i), source_revision="source-" + str(i))
                                  for i in range(4)]
        self.status_calls, self.source_calls = 0, 0
        self.serial, self.tickets, self.played = 0, {}, []
        self.before = self.transform = None
        self.play_error = None
        self.old_controller = False

    def call(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params)))
        if self.before:
            self.before(self, action, params)
        if action == "capabilities":
            if self.old_controller and "multiview_recovery_v1" in params:
                raise HTTPError("Old admission")
            return {"version": 1, "player": {"version": "old-test", "runtime": "runtime-1",
                                               "platform": "webos-native"},
                    "multiview": copy.deepcopy(self.marker)}
        if action != "multiview":
            raise AssertionError("Named selection must not change the global provider/profile")
        op = params["op"]
        binding = {key: copy.deepcopy(value) for key, value in params.items()
                   if key not in ("version", "runtime", "op")}
        value = dict(version=1, runtime="runtime-1", op=op, binding=binding)
        if op == "status":
            self.status_calls += 1
            value["snapshot"] = copy.deepcopy(self.snapshot)
        elif op == "sources":
            self.source_calls += 1
            index = int(params.get("cursor", "page-0").split("-")[-1])
            rows = []
            for row in self.pages[index]:
                self.serial += 1
                ticket = "ticket-" + str(self.serial)
                rows.append(dict(copy.deepcopy(row), ticket=ticket))
                self.tickets[ticket] = copy.deepcopy(row)
                # The real native source receipt ring is bounded to 256.
                while len(self.tickets) > 256:
                    self.tickets.pop(next(iter(self.tickets)))
            value.update(items=rows, complete=index == len(self.pages) - 1,
                         cursor=None if index == len(self.pages) - 1 else "page-" + str(index + 1))
        elif op == "play":
            self.played.append(copy.deepcopy(params))
            if self.play_error:
                raise self.play_error
            if params["source_ticket"] not in self.tickets:
                raise PlayerRejected(dict(value, reason="source_expired"))
            value.update(accepted=True, outcome="requested")
        else:
            raise AssertionError("Unexpected operation: " + op)
        return self.transform(value, params) if self.transform else value


class NamedParseTest(unittest.TestCase):
    def test_short_long_and_optional_disambiguators(self):
        for prefix in (["2", "local"], ["window", "2", "LOCAL"]):
            self.assertEqual(mv.parse([*prefix, "Cartoon one.mp4", "--profile", "p.1", "--source", "s:2"]),
                             {"op": "play_name", "slot_id": 1, "provider": "local", "name": "Cartoon one.mp4",
                              "profile": "p.1", "source_id": "s:2"})
        self.assertEqual(mv.parse(["1", "m3u", "News", "HD"])["name"], "News HD")
        self.assertEqual(mv.parse(["window", "1", "play", "Ticket_A"]),
                         {"op": "play", "slot_id": 0, "source_ticket": "Ticket_A"})

    def test_invalid_named_input_is_rejected_offline(self):
        for words in (["0", "m3u", "News"], ["01", "m3u", "News"], ["1", "local"],
                      ["1", "local", ""], ["1", "local", "   "], ["1", "local", "/private/file.mp4"],
                      ["1", "m3u", "News", "--profile"], ["1", "m3u", "News", "--bad", "x"],
                      ["1", "m3u", "News", "--profile", "p", "--profile", "q"],
                      ["1", "local", "Film", "--source", "https://private.invalid"],
                      ["window", "1", "m3u", "token=PRIVATE"], ["1", "m3u", "News\nHD"]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                mv.parse(words)


class NamedRunTest(unittest.TestCase):
    def execute(self, client, words, machine=True):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mv.run(API, client, "device-1", words, 45, machine)
        finally:
            self.assertEqual(client.timeout, 45)
        return code, json.loads(out.getvalue()) if machine else out.getvalue()

    def test_every_grid4_window_uses_its_fresh_bound_ticket(self):
        for number in range(1, 5):
            client = Client()
            code, value = self.execute(client, [str(number), "m3u", "News HD", "--profile", "home"])
            self.assertEqual((code, value["op"]), (0, "play"))
            operations = [p["op"] for _, action, p in client.calls if action == "multiview"]
            self.assertEqual(operations, ["status", "sources", "sources", "status", "play"])
            request = client.played[0]
            self.assertEqual((request["slot_id"], request["slot_generation"], request["source_revision"]),
                             (number - 1, "generation-" + str(number - 1), "source-" + str(number - 1)))
            self.assertEqual(request["source_ticket"], "ticket-2")
            choices = [p for _, _, p in client.calls if p.get("op") == "sources"]
            self.assertTrue(all(p["provider"] == "m3u" and p["profile"] == "home" for p in choices))

    def test_local_accepts_native_and_legacy_ids_without_guessing_global_provider(self):
        for provider in ("local", "offline"):
            client = Client([[source("file-1", "Film.mp4", provider, "saved.2", "file"),
                              source("plex-1", "Film.mp4", "plex", "plex.1", "vod")]])
            self.execute(client, ["window", "3", "local", "Film.mp4"])
            self.assertEqual(len(client.played), 1)
            self.assertEqual(client.tickets[client.played[0]["source_ticket"]]["provider"], provider)
            self.assertTrue(all("provider" not in p for _, _, p in client.calls if p.get("op") == "sources"))

    def test_exact_case_unicode_and_complete_label_are_required(self):
        for query in ("News", "news HD", "NEWS HD", "Cafe\u0301"):
            client = Client([[source(label="News HD"), source("cafe", "Café")]])
            with self.subTest(query=query), self.assertRaisesRegex(Error, "No exact"):
                self.execute(client, ["1", "m3u", query])
            self.assertEqual(client.played, [])

    def test_duplicate_labels_list_stable_profile_and_source_choices_without_play(self):
        client = Client([[source("a", profile="p1")], [source("b", profile="p2")]])
        with self.assertRaises(Error) as error:
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertIn("--profile p1 --source a", str(error.exception))
        self.assertIn("--profile p2 --source b", str(error.exception))
        self.assertNotIn("ticket-", str(error.exception))
        self.assertEqual(client.played, [])
        self.assertEqual(client.source_calls, 2)

    def test_source_id_disambiguates_same_profile_only_after_complete_search(self):
        client = Client([[source("a")], [source("b")]])
        self.execute(client, ["1", "m3u", "News HD", "--source", "b"])
        self.assertEqual(client.source_calls, 3)
        self.assertEqual(client.tickets[client.played[0]["source_ticket"]]["source_id"], "b")

    def test_full_scan_refreshes_early_ticket_after_native_ring_eviction(self):
        pages = [[source("selected")]]
        pages += [[source("other-" + str(page) + "-" + str(index), "News HD extra " + str(index))
                   for index in range(128)] for page in range(3)]
        client = Client(pages)
        self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(client.source_calls, 5)
        self.assertNotIn("ticket-1", client.tickets)
        self.assertEqual(client.played[0]["source_ticket"], "ticket-386")

    def test_changed_selected_page_is_not_replayed_or_played(self):
        client = Client()
        def transform(value, request):
            if request["op"] == "sources" and client.source_calls == 2:
                value["items"][0]["profile"] = "changed-profile"
            return value
        client.transform = transform
        with self.assertRaisesRegex(Error, "choices changed"):
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(client.played, [])
        self.assertEqual(client.source_calls, 2)

    def test_refreshed_opaque_cursor_can_rotate_without_changing_the_page(self):
        client = Client([[source("selected"), source("same-page", "News HD extra")],
                         [source("other", "News HD extra 2")]])
        def transform(value, request):
            if request["op"] == "sources" and client.source_calls == 3:
                value["cursor"] = "fresh-opaque-cursor"
                value["items"].reverse()
            return value
        client.transform = transform
        self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(client.source_calls, 3)
        self.assertEqual(client.played[0]["source_ticket"], "ticket-4")
        self.assertEqual(client.tickets[client.played[0]["source_ticket"]]["source_id"], "selected")

    def test_refreshed_page_completion_change_still_rejects(self):
        client = Client([[source("selected")], [source("other", "News HD extra")]])
        def transform(value, request):
            if request["op"] == "sources" and client.source_calls == 3:
                value.update(complete=True, cursor=None)
            return value
        client.transform = transform
        with self.assertRaisesRegex(Error, "choices changed"):
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(client.played, [])

    def test_scene_window_runtime_races_abort_before_play(self):
        for field in ("scene", "focus_revision", "audio_revision", "layout_revision", "slot_generation", "source_revision", "runtime"):
            client = Client()
            def transform(value, request):
                if request["op"] == "status" and client.status_calls == 2:
                    if field in mv.CONTEXT:
                        value["snapshot"]["context"][field] = "new-value"
                    elif field == "runtime":
                        value["runtime"] = "runtime-2"
                    else:
                        value["snapshot"]["slots"][0][field] = "new-value"
                return value
            client.transform = transform
            with self.subTest(field=field), self.assertRaises(Error):
                self.execute(client, ["1", "m3u", "News HD"])
            self.assertEqual(client.played, [])

    def test_cross_page_duplicate_ids_tickets_and_cursors_are_rejected(self):
        for broken in ("source_id", "ticket", "cursor"):
            client = Client([[source("a")], [source("b")], [source("c")]])
            def transform(value, request):
                if request["op"] == "sources" and client.source_calls == 2:
                    if broken == "source_id":
                        value["items"][0]["source_id"] = "a"
                    elif broken == "ticket":
                        value["items"][0]["ticket"] = "ticket-1"
                    else:
                        value["cursor"] = "page-1"
                return value
            client.transform = transform
            with self.subTest(broken=broken), self.assertRaisesRegex(Error, "repeat"):
                self.execute(client, ["1", "m3u", "News HD"])
            self.assertEqual(client.played, [])

    def test_limits_and_missing_discovery_support_never_play_partial_population(self):
        for limit in ("MAX_NAMED_PAGES", "MAX_NAMED_ITEMS"):
            client = Client([[source("a")], [source("b")], [source("c")]])
            with mock.patch.object(mv, limit, 2), self.subTest(limit=limit), self.assertRaisesRegex(Error, "limit"):
                self.execute(client, ["1", "m3u", "News HD"])
            self.assertEqual(client.played, [])
        client = Client()
        client.marker["operations"].remove("sources")
        with self.assertRaisesRegex(Error, "source discovery"):
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(len(client.calls), 1)

    def test_provider_profile_filter_violation_is_not_used(self):
        for row, options in ((source(provider="plex"), []), (source(profile="other"), ["--profile", "home"])):
            client = Client([[row]])
            with self.assertRaisesRegex(Error, "filter"):
                self.execute(client, ["1", "m3u", "News HD", *options])
            self.assertEqual(client.played, [])

    def test_read_deadline_and_unconfirmed_mutation_never_replay_play(self):
        client, clock = Client(), [0]
        def before(c, action, request):
            if action == "multiview" and request["op"] == "status" and c.status_calls == 1:
                clock[0] = 46
        client.before = before
        with mock.patch.object(mv.time, "monotonic", side_effect=lambda: clock[0]), self.assertRaisesRegex(Error, "deadline"):
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(client.played, [])
        client = Client()
        client.play_error = Error("Unknown transport outcome")
        with self.assertRaisesRegex(Error, "Unknown transport outcome"):
            self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual(len(client.played), 1)

    def test_older_controller_read_probe_fallback_keeps_existing_protocol(self):
        client = Client()
        client.old_controller = True
        self.execute(client, ["1", "m3u", "News HD"])
        self.assertEqual([p for _, action, p in client.calls if action == "capabilities"],
                         [{"multiview": True, "multiview_recovery_v1": True}, {"multiview": True}])
        self.assertEqual(len(client.played), 1)
        self.assertTrue(all(p["op"] in mv.OPERATIONS for _, action, p in client.calls if action == "multiview"))


if __name__ == "__main__":
    unittest.main()
