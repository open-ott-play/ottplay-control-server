"""Current-playback track commands bind one mutation to a fresh, complete catalog."""
import contextlib
import copy
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_media_tracks_tests", ROOT / "cli" / "ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
PRIVATE = "private-track-fixture-secret"
PRIVATE_URL = "https://private.invalid/stream?token=" + PRIVATE


def media_tracks_module():
    spec = importlib.util.spec_from_file_location("media_tracks_contract_tests", ROOT / "cli" / "media_tracks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def track(identifier, kind="audio", **changes):
    row = {"id": identifier, "kind": kind, "label": "English surround",
           "language": "eng", "codec": "ac3", "selectable": True}
    row.update(changes)
    return row


def catalog(device="dev-1"):
    return {"version": 1, "runtime": "runtime-" + device, "op": "list",
            "scope": "current_playback", "playback": "playback-" + device,
            "source_generation": "source-" + device, "catalog": "catalog-" + device,
            "tracks": [track("Audio_A"), track("Audio_B", label="Russian stereo", language="rus"),
                       track("Sub_A", "subtitle", label="English captions", codec="srt")],
            "subtitle_off_supported": True,
            "observed": {"audio": {"state": "track", "track_id": "Audio_A", "age_ms": 10},
                         "subtitle": {"state": "not_observed", "track_id": None, "age_ms": None}}}


def selected(params, *, confirmed=False):
    target = params["target"]
    observed = None
    if confirmed:
        observed = {"state": target["mode"], "track_id": target.get("id"), "age_ms": 0}
    return {"version": 1, "scope": "current_playback", **copy.deepcopy(params),
            "accepted": True, "outcome": "confirmed" if confirmed else "requested", "observed": observed}


class Client(ott.Client):
    """Use the real alias/group machinery with a deterministic, non-network RPC seam."""

    def __init__(self):
        self.config = {"players": {"t1": "dev-1", "t2": "dev-2"},
                       "groups": {"tt": ["t1", "t2"]}}
        self.credentials = {"devices": [{"id": "dev-1"}, {"id": "dev-2"}]}
        self.server, self.timeout, self.on_receipt = "https://controller.invalid", 45, None
        self.calls, self.lock = [], threading.Lock()
        self.capability_transform = self.list_transform = self.select_transform = self.before = None
        self.confirmed = False

    def call(self, device, action, params):
        with self.lock:
            self.calls.append((device, action, copy.deepcopy(params)))
            number = len(self.calls)
        if self.before:
            self.before(self, device, action, params)
        if action == "capabilities":
            value = {"version": 1,
                     "player": {"version": "test", "platform": "webos-native", "runtime": "runtime-" + device},
                     "lifecycle": [], "input": [], "playback": [], "media_tracks": {"version": 1}}
            transform = self.capability_transform
        elif action == "media_tracks" and params["op"] == "list":
            value, transform = catalog(device), self.list_transform
        elif action == "media_tracks" and params["op"] == "select":
            value, transform = selected(params, confirmed=self.confirmed), self.select_transform
        else:
            raise AssertionError("Track commands must not use another action: " + action)
        status = "ok"
        try:
            return transform(value, params) if transform else value
        except ott.PlayerRejected:
            status = "rejected"
            raise
        except ott.PlayerUnsupported:
            status = "unsupported"
            raise
        finally:
            if self.on_receipt:
                self.on_receipt({"request_id": format(number, "032x"), "action": action, "status": status})


class MediaTracksCLITest(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def execute(self, words=(), *, player="t1", json_output=True, receipt=False):
        output, errors = io.StringIO(), io.StringIO()
        args = (["-j"] if json_output else []) + (["--receipt"] if receipt else [])
        with mock.patch.object(ott, "read_json", return_value=self.client.config), \
                mock.patch.object(ott, "Client", return_value=self.client), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main([*args, player, "tracks", *words])
        raw, error = output.getvalue(), errors.getvalue()
        self.assertNotIn(PRIVATE, raw + error)
        self.assertNotIn(PRIVATE_URL, raw + error)
        self.assertEqual(self.client.timeout, 45)
        return code, json.loads(raw) if json_output and raw.strip() else raw, error

    def operations(self, device="dev-1"):
        return [(action, params) for target, action, params in self.client.calls if target == device]

    def assert_one_selection(self, target, device="dev-1"):
        snapshot = catalog(device)
        expected = {key: snapshot[key] for key in ("runtime", "playback", "source_generation", "catalog")}
        expected.update(op="select", target=target)
        self.assertEqual(self.operations(device), [
            ("capabilities", {}),
            ("media_tracks", {"op": "list", "runtime": snapshot["runtime"]}),
            ("media_tracks", expected)])

    def test_shared_contract_accepts_valid_results_and_rejects_every_negative(self):
        module = media_tracks_module()
        fixture = json.loads((ROOT / "contracts" / "media-tracks-v1.json").read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic"])
        self.assertEqual(fixture["capability"], {"media_tracks": {"version": 1}})
        for row in fixture["valid"]:
            with self.subTest(valid=row["name"]):
                data, request = copy.deepcopy(row["data"]), copy.deepcopy(row["params"])
                self.assertEqual(module.metadata(data, request, row["status"]), row["data"])
                self.assertEqual(data, row["data"])
                self.assertEqual(request, row["params"])
        for row in fixture["invalid_results"]:
            with self.subTest(invalid=row["name"]), self.assertRaises(module.InvalidResponse):
                module.metadata(copy.deepcopy(row["data"]), copy.deepcopy(row["params"]), row["status"])

    def test_track_count_limits_apply_per_kind_and_to_the_complete_catalog(self):
        module = media_tracks_module()
        data = catalog()
        data["tracks"] = [track("a" + str(i)) for i in range(64)] + [
            track("s" + str(i), "subtitle") for i in range(64)]
        data["observed"]["audio"]["track_id"] = "a0"
        request = {"op": "list", "runtime": data["runtime"]}
        self.assertEqual(module.metadata(data, request), data)
        data["tracks"][-1] = track("a64")
        with self.assertRaises(module.InvalidResponse):
            module.metadata(data, request)
        data["tracks"][-1] = track("s63", "subtitle")
        data["tracks"].append(track("s64", "subtitle"))
        with self.assertRaises(module.InvalidResponse):
            module.metadata(data, request)

    def test_bare_and_explicit_list_are_passive_and_keep_unknowns(self):
        for words in ([], ["list"]):
            with self.subTest(words=words):
                self.client = Client()
                code, data, errors = self.execute(words)
                self.assertEqual((code, errors, data), (0, "", catalog()))
                self.assertIsNone(data["observed"]["subtitle"]["track_id"])
                self.assertIsNone(data["observed"]["subtitle"]["age_ms"])
                self.assertEqual(self.operations(), [("capabilities", {}),
                    ("media_tracks", {"op": "list", "runtime": "runtime-dev-1"})])

    def test_audio_and_subtitle_select_exact_ids_with_all_fresh_bindings(self):
        for kind, identifier in (("audio", "Audio_B"), ("subtitle", "Sub_A")):
            with self.subTest(kind=kind):
                self.client = Client()
                code, data, errors = self.execute([kind, identifier])
                self.assertEqual((code, errors), (0, ""))
                target = {"kind": kind, "mode": "track", "id": identifier}
                self.assert_one_selection(target)
                self.assertEqual(data["target"], target)
                self.assertEqual(data["outcome"], "requested")
                self.assertIsNone(data["observed"])

    def test_subtitle_off_is_explicit_and_id_prefix_disambiguates_literal_off(self):
        for words, target in ((["subtitle", "off"], {"kind": "subtitle", "mode": "off"}),
                              (["subtitle", "id:off"], {"kind": "subtitle", "mode": "track", "id": "off"}),
                              (["audio", "off"], {"kind": "audio", "mode": "track", "id": "off"})):
            with self.subTest(words=words):
                self.client = Client()
                self.client.list_transform = lambda value, params, kind=words[0]: {**value, "tracks": [
                    *value["tracks"], track("off", kind)]}
                code, data, errors = self.execute(words)
                self.assertEqual((code, errors), (0, ""))
                self.assert_one_selection(target)
                self.assertEqual(data["target"], target)

    def test_each_selection_reads_the_new_catalog_instead_of_caching_a_lease(self):
        self.assertEqual(self.execute(["audio", "Audio_B"])[0], 0)
        self.client.calls.clear()
        def replace(value, params):
            value.update(playback="another-playback", source_generation="another-source", catalog="another-catalog")
            return value
        self.client.list_transform = replace
        code, _, _ = self.execute(["audio", "Audio_B"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.operations()), 3)
        self.assertEqual({key: self.operations()[-1][1][key] for key in ("playback", "source_generation", "catalog")},
                         {"playback": "another-playback", "source_generation": "another-source", "catalog": "another-catalog"})

    def test_invalid_syntax_sends_no_rpc(self):
        for words in (["list", "extra"], ["audio"], ["subtitle"], ["video", "Audio_A"],
                      ["audio", "Audio_A", "extra"], ["subtitle", "id:"],
                      ["audio", "../../secret"], ["audio", PRIVATE_URL]):
            with self.subTest(words=words):
                self.client = Client()
                self.assertNotEqual(self.execute(words)[0], 0)
                self.assertEqual(self.client.calls, [])

    def test_missing_or_inexact_capability_never_fetches_or_changes_tracks(self):
        for marker in ("absent", None, {}, True, 1, {"version": True}, {"version": 1.0},
                       {"version": 2}, {"version": 1, "token": PRIVATE}):
            with self.subTest(marker=marker):
                self.client = Client()
                def alter(value, params, marker=marker):
                    if marker == "absent":
                        value.pop("media_tracks")
                    else:
                        value["media_tracks"] = marker
                    return value
                self.client.capability_transform = alter
                self.assertNotEqual(self.execute(["audio", "Audio_A"])[0], 0)
                self.assertEqual(self.operations(), [("capabilities", {})])

    def test_ids_are_case_sensitive_not_labels_or_positions_and_match_the_kind(self):
        for words in (["audio", "audio_a"], ["audio", "1"], ["audio", "English"],
                      ["audio", "Sub_A"], ["subtitle", "Audio_A"], ["subtitle", "missing"]):
            with self.subTest(words=words):
                self.client = Client()
                self.assertNotEqual(self.execute(words)[0], 0)
                self.assertEqual([action for action, _ in self.operations()], ["capabilities", "media_tracks"])
                self.assertEqual(self.operations()[-1][1]["op"], "list")

    def test_unselectable_track_and_unsupported_off_never_mutate(self):
        for words in (["audio", "Audio_A"], ["subtitle", "off"]):
            with self.subTest(words=words):
                self.client = Client()
                def restrict(value, params):
                    value["tracks"][0]["selectable"] = False
                    value["subtitle_off_supported"] = False
                    return value
                self.client.list_transform = restrict
                self.assertNotEqual(self.execute(words)[0], 0)
                self.assertEqual(len(self.operations()), 2)

    def test_invalid_or_incoherent_catalog_blocks_selection(self):
        mutations = [lambda d: d.update(runtime="another-runtime"),
                     lambda d: d.update(version=True), lambda d: d.update(op="select"),
                     lambda d: d.update(scope="persistent_profile"),
                     lambda d: d.update(catalog=PRIVATE_URL), lambda d: d.update(token=PRIVATE),
                     lambda d: d["tracks"].append(copy.deepcopy(d["tracks"][0])),
                     lambda d: d["tracks"][0].update(selectable=1),
                     lambda d: d["tracks"][0].update(url=PRIVATE_URL),
                     lambda d: d["observed"]["audio"].update(track_id="Sub_A"),
                     lambda d: d["observed"]["audio"].update(track_id="not-listed"),
                     lambda d: d["observed"]["audio"].update(age_ms=True),
                     lambda d: d["observed"]["subtitle"].update(age_ms=0)]
        for index, change in enumerate(mutations):
            with self.subTest(mutation=index):
                self.client = Client()
                self.client.list_transform = lambda value, params, change=change: (change(value), value)[1]
                self.assertNotEqual(self.execute(["audio", "Audio_B"])[0], 0)
                self.assertEqual(len(self.operations()), 2)

    def test_invalid_list_metadata_reports_no_selection_for_listing_and_preflight(self):
        for words in ([], ["list"], ["audio", "Audio_B"]):
            with self.subTest(words=words):
                self.client = Client()
                self.client.list_transform = lambda value, params: {**value, "token": PRIVATE}
                code, data, errors = self.execute(words)
                self.assertEqual((code, data), (1, ""))
                self.assertIn("invalid media track metadata", errors)
                self.assertIn("No selection was sent", errors)
                self.assertNotIn("may have executed", errors)
                self.assertEqual(self.operations(), [("capabilities", {}),
                    ("media_tracks", {"op": "list", "runtime": "runtime-dev-1"})])

    def test_invalid_selection_metadata_retains_uncertainty_without_replay(self):
        self.client.select_transform = lambda value, params: {**value, "token": PRIVATE}
        code, data, errors = self.execute(["audio", "Audio_B"])
        self.assertEqual((code, data), (1, ""))
        self.assertIn("invalid media track metadata", errors)
        self.assertIn("A sent selection may have executed; no request was repeated", errors)
        self.assertNotIn("No selection was sent", errors)
        self.assert_one_selection({"kind": "audio", "mode": "track", "id": "Audio_B"})

    def test_known_empty_catalog_is_valid_and_does_not_invent_tracks(self):
        def empty(value, params):
            value["tracks"] = []
            value["observed"]["audio"] = {"state": "not_observed", "track_id": None, "age_ms": None}
            return value
        self.client.list_transform = empty
        code, data, errors = self.execute()
        self.assertEqual((code, errors, data["tracks"]), (0, "", []))
        self.assertEqual(data["observed"]["audio"], {"state": "not_observed", "track_id": None, "age_ms": None})

    def test_private_wire_labels_are_rejected_without_display_or_selection(self):
        for label in (PRIVATE_URL, "/private/" + PRIVATE, "token=" + PRIVATE):
            with self.subTest(label=label):
                self.client = Client()
                self.client.list_transform = lambda value, params, label=label: {**value, "tracks": [
                    {**row, "label": label} for row in value["tracks"]]}
                code, data, _ = self.execute(["audio", "Audio_B"])
                self.assertNotEqual(code, 0)
                self.assertEqual(data, "")
                self.assertEqual(len(self.operations()), 2)

    def test_requested_and_confirmed_text_do_not_claim_the_same_evidence(self):
        code, text, errors = self.execute(["audio", "Audio_B"], json_output=False)
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("requested", text.casefold())
        self.assertNotIn("selection confirmed", text.casefold())
        self.client = Client()
        self.client.confirmed = True
        code, text, errors = self.execute(["subtitle", "off"], json_output=False)
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("confirmed", text.casefold())

    def test_forged_select_ack_is_rejected_after_exactly_one_mutation(self):
        mutations = [lambda d: d.update(runtime="old-runtime"), lambda d: d.update(playback="old-playback"),
                     lambda d: d.update(source_generation="old-source"), lambda d: d.update(catalog="old-catalog"),
                     lambda d: d["target"].update(id="Audio_A"), lambda d: d.update(accepted=1),
                     lambda d: d.update(observed={"state": "track", "track_id": "Audio_B", "age_ms": 0}),
                     lambda d: d.update(outcome="confirmed"), lambda d: d.update(token=PRIVATE)]
        for index, change in enumerate(mutations):
            with self.subTest(mutation=index):
                self.client = Client()
                self.client.select_transform = lambda value, params, change=change: (change(value), value)[1]
                self.assertNotEqual(self.execute(["audio", "Audio_B"])[0], 0)
                self.assert_one_selection({"kind": "audio", "mode": "track", "id": "Audio_B"})

    def test_stale_catalog_rejection_does_not_relist_or_replay(self):
        def stale(value, params):
            raise ott.PlayerRejected(PRIVATE_URL, {"version": 1, **params,
                "state": "error", "error": "catalog_changed"})
        self.client.select_transform = stale
        self.assertNotEqual(self.execute(["audio", "Audio_B"])[0], 0)
        self.assert_one_selection({"kind": "audio", "mode": "track", "id": "Audio_B"})

    def test_uncertain_mutation_transport_failure_is_not_repeated(self):
        def timeout(value, params):
            error = ott.RequestError("request_pending_timeout", "wait", "Request may have executed", "f" * 32)
            error.action = "media_tracks"
            raise error
        self.client.select_transform = timeout
        self.assertNotEqual(self.execute(["subtitle", "off"])[0], 0)
        self.assert_one_selection({"kind": "subtitle", "mode": "off"})

    def test_group_cancellation_after_listing_prevents_selection(self):
        cancelled = threading.Event()
        self.client.group_cancelled = cancelled
        def stop(value, params):
            cancelled.set()
            return value
        self.client.list_transform = stop
        self.assertEqual(self.execute(["audio", "Audio_B"])[0], 130)
        self.assertEqual(len(self.operations()), 2)

    def test_terminal_reply_survives_cancellation_after_validation(self):
        for words, confirmed in (([], False), (["audio", "Audio_B"], False), (["subtitle", "off"], True)):
            with self.subTest(words=words, confirmed=confirmed):
                self.client = Client()
                self.client.confirmed = confirmed
                cancelled = threading.Event()
                self.client.group_cancelled = cancelled
                module = media_tracks_module()
                validate = module.metadata
                terminal = "select" if words else "list"
                def finish(raw, request, status="ok", terminal=terminal, validate=validate, cancelled=cancelled):
                    data = validate(raw, request, status)
                    if request["op"] == terminal:
                        cancelled.set()
                    return data
                with mock.patch.object(ott, "media_tracks_module", return_value=module), \
                        mock.patch.object(module, "metadata", side_effect=finish):
                    code, data, errors = self.execute(words, receipt=True)
                self.assertEqual(code, 0)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(data["op"], terminal)
                receipts = [json.loads(line) for line in errors.splitlines()]
                self.assertEqual(receipts[-1]["status"], "ok")
                self.assertEqual(len(receipts), 3 if words else 2)
                if words:
                    self.assertEqual(data["outcome"], "confirmed" if confirmed else "requested")
                    self.assertEqual(data["observed"], {"state": "off", "track_id": None, "age_ms": 0}
                                     if confirmed else None)
                    self.assert_one_selection({"kind": "subtitle", "mode": "off"} if confirmed else
                                              {"kind": "audio", "mode": "track", "id": "Audio_B"})

    def test_terminal_reply_survives_deadline_crossed_during_validation(self):
        for words, confirmed in (([], False), (["audio", "Audio_B"], False), (["subtitle", "off"], True)):
            with self.subTest(words=words, confirmed=confirmed):
                self.client = Client()
                self.client.confirmed = confirmed
                module = media_tracks_module()
                clock = [100.0]
                validate = module.metadata
                terminal = "select" if words else "list"
                def finish(raw, request, status="ok", terminal=terminal, validate=validate, clock=clock):
                    data = validate(raw, request, status)
                    if request["op"] == terminal:
                        clock[0] += 46
                    return data
                with mock.patch.object(ott, "media_tracks_module", return_value=module), \
                        mock.patch.object(module, "time", SimpleNamespace(monotonic=lambda clock=clock: clock[0])), \
                        mock.patch.object(module, "metadata", side_effect=finish):
                    code, data, errors = self.execute(words)
                self.assertEqual((code, errors), (0, ""))
                self.assertEqual(data["op"], terminal)
                self.assertEqual(len(self.operations()), 3 if words else 2)
                if words:
                    self.assertEqual(data["outcome"], "confirmed" if confirmed else "requested")

    def test_deadline_crossed_after_list_still_prevents_selection(self):
        module = media_tracks_module()
        clock = [100.0]
        validate = module.metadata
        def finish(raw, request, status="ok"):
            data = validate(raw, request, status)
            if request["op"] == "list":
                clock[0] += 46
            return data
        with mock.patch.object(ott, "media_tracks_module", return_value=module), \
                mock.patch.object(module, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                mock.patch.object(module, "metadata", side_effect=finish):
            code, data, errors = self.execute(["audio", "Audio_B"])
        self.assertNotEqual(code, 0)
        self.assertEqual(data, "")
        self.assertIn("deadline expired", errors)
        self.assertEqual(self.operations(), [("capabilities", {}),
            ("media_tracks", {"op": "list", "runtime": "runtime-dev-1"})])

    def test_named_and_comma_groups_use_independent_bindings_and_isolate_failure(self):
        for player in ("tt", "t1,t2"):
            with self.subTest(player=player):
                self.client = Client()
                barrier = threading.Barrier(2, timeout=5)
                def meet(value, params, barrier=barrier):
                    barrier.wait()
                    return value
                self.client.list_transform = meet
                def reject_first(value, params):
                    if params["runtime"] == "runtime-dev-1":
                        raise ott.PlayerRejected(PRIVATE_URL, {"version": 1, **params,
                            "state": "error", "error": "catalog_changed"})
                    return value
                self.client.select_transform = reject_first
                code, data, errors = self.execute(["audio", "Audio_B"], player=player)
                self.assertNotEqual(code, 0)
                self.assertEqual(errors, "")
                self.assertEqual((data["succeeded"], data["failed"]), (1, 1))
                self.assertEqual([row["player"] for row in data["results"]], ["t1", "t2"])
                self.assertEqual([row["ok"] for row in data["results"]], [False, True])
                self.assertEqual(data["results"][1]["data"]["runtime"], "runtime-dev-2")
                for device in ("dev-1", "dev-2"):
                    self.assert_one_selection({"kind": "audio", "mode": "track", "id": "Audio_B"}, device)

    def test_receipts_survive_invalid_ack_and_group_tagging(self):
        for player in ("t1", "tt"):
            with self.subTest(player=player):
                self.client = Client()
                self.client.select_transform = lambda value, params: {**value, "accepted": False}
                code, _, errors = self.execute(["audio", "Audio_B"], player=player, receipt=True)
                self.assertNotEqual(code, 0)
                receipts = [json.loads(line) for line in errors.splitlines() if line.startswith("{")]
                devices = ("dev-1", "dev-2") if player == "tt" else ("dev-1",)
                self.assertEqual(len(receipts), len(devices) * 3)
                self.assertEqual(len({row["request_id"] for row in receipts}), len(receipts))
                self.assertEqual(sum(row["action"] == "media_tracks" for row in receipts), len(devices) * 2)
                if player == "tt":
                    self.assertCountEqual([row["player"] for row in receipts], ["t1"] * 3 + ["t2"] * 3)
                for device in devices:
                    self.assert_one_selection({"kind": "audio", "mode": "track", "id": "Audio_B"}, device)


if __name__ == "__main__":
    unittest.main()
