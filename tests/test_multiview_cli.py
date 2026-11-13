"""Multi-view command parsing and source-bound requests, using fake clients only."""
import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("multiview_contract_tests", ROOT / "cli" / "multiview.py")
mv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mv)
FIXTURE = json.loads((ROOT / "contracts" / "multiview-v1.json").read_text(encoding="utf-8"))


class Error(Exception):
    pass


class PlayerRejected(Error):
    def __init__(self, data):
        super().__init__("PRIVATE transport message")
        self.data = data


class PlayerUnsupported(PlayerRejected):
    pass


class HTTPError(Error):
    pass


API = SimpleNamespace(Error=Error, PlayerRejected=PlayerRejected, PlayerUnsupported=PlayerUnsupported,
                      HTTPError=HTTPError, capabilities_metadata=copy.deepcopy)


class Client:
    """An in-memory RPC seam; no socket, HTTP or real player is reachable."""
    def __init__(self):
        self.timeout = 45
        self.calls = []
        self.marker = copy.deepcopy(FIXTURE["capability"]["multiview"])
        self.state = copy.deepcopy(FIXTURE["results"]["status_bound"])
        self.before = self.transform = None

    def call(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params), self.timeout))
        if self.before:
            self.before(self, action, params)
        if action == "capabilities":
            value = {"version": 1, "player": {"version": "test", "runtime": "native-runtime-1",
                                               "platform": "webos-native"}, "multiview": copy.deepcopy(self.marker)}
        elif action == "multiview":
            binding = {key: copy.deepcopy(value) for key, value in params.items()
                       if key not in ("version", "runtime", "op")}
            if params["op"] == "status":
                value = copy.deepcopy(self.state)
            elif params["op"] == "sources":
                value = copy.deepcopy(FIXTURE["results"]["sources"])
            else:
                value = {"accepted": True, "outcome": "requested"}
            value.update(version=1, runtime="native-runtime-1", op=params["op"], binding=binding)
        else:
            raise AssertionError("Unexpected action: " + action)
        return self.transform(value, params) if self.transform else value


class MultiviewParserTest(unittest.TestCase):
    def test_status_help_and_off_are_explicit(self):
        for words in ([], ["status"], ["STATUS"]):
            self.assertEqual(mv.parse(words), {"op": "status"})
        for words in (["--help"], ["-h"]):
            self.assertEqual(mv.parse(words), {"op": "help"})
        self.assertEqual(mv.parse(["off"]), {"op": "off", "mode": "promote_active"})
        for mode in ("promote_active", "restore_previous"):
            self.assertEqual(mv.parse(["off", mode]), {"op": "off", "mode": mode})

    def test_human_slots_convert_once_in_every_slot_operation(self):
        for number in range(1, 10):
            word, wire = str(number), number - 1
            for words, expected in (
                    (["sources", word], {"op": "sources", "slot_id": wire}),
                    (["focus", word], {"op": "focus", "slot_id": wire}),
                    (["audio", word], {"op": "audio", "policy": "slot", "slot_id": wire}),
                    (["window", word, "focus"], {"op": "focus", "slot_id": wire}),
                    (["window", word, "audio"], {"op": "audio", "policy": "slot", "slot_id": wire}),
                    (["window", word, "stop"], {"op": "stop", "slot_id": wire}),
                    (["window", word, "play", "Ticket_A-1"],
                     {"op": "play", "slot_id": wire, "source_ticket": "Ticket_A-1"})):
                with self.subTest(words=words):
                    before = copy.deepcopy(words)
                    self.assertEqual(mv.parse(words), expected)
                    self.assertEqual(words, before)

    def test_audio_policy_has_no_implicit_slot(self):
        self.assertEqual(mv.parse(["audio", "off"]), {"op": "audio", "policy": "muted"})
        self.assertEqual(mv.parse(["audio", "follows-focus"]),
                         {"op": "audio", "policy": "follows_focus"})

    def test_layouts_and_perimeter_satellite_boundaries(self):
        for layout in ("single", "pip", "grid4", "grid9"):
            self.assertEqual(mv.parse(["layout", layout]), {"op": "layout", "layout": layout})
        self.assertEqual(mv.parse(["layout", "perimeter"]),
                         {"op": "layout", "layout": "perimeter", "satellites": 8})
        for number in range(9):
            expected = {"op": "layout", "layout": "perimeter", "satellites": number}
            self.assertEqual(mv.parse(["layout", "perimeter", "--satellites", str(number)]), expected)
            self.assertEqual(mv.parse(["layout", "perimeter", str(number)]), expected)

    def test_sources_use_opaque_profile_and_nonmutating_filters(self):
        words = ["sources", "9", "--profile", "SavedProfile:Home-2", "--provider", "edem/vportal",
                 "--search", "Три кота", "--cursor", "Cursor_Z9"]
        before = list(words)
        self.assertEqual(mv.parse(words), {
            "op": "sources", "slot_id": 8, "profile": "SavedProfile:Home-2",
            "provider": "edem/vportal", "query": "Три кота", "cursor": "Cursor_Z9"})
        self.assertEqual(words, before)
        self.assertEqual(mv.parse(["sources", "1", "--profile", "001"]),
                         {"op": "sources", "slot_id": 0, "profile": "001"})

    def test_invalid_slot_spellings_and_types_are_rejected(self):
        for value in ("0", "10", "-1", "+1", "01", "1.0", "1e0", " 1", "1 ", "", "１", "١",
                      None, False, True, 1, 1.0, [], {}):
            for words in (["sources", value], ["focus", value], ["audio", value],
                          ["window", value, "stop"], ["window", value, "play", "ticket"]):
                with self.subTest(words=words), self.assertRaises(ValueError):
                    mv.parse(words)

    def test_rejects_urls_paths_duplicate_options_and_unbounded_values(self):
        invalid = [
            ["status", "extra"], ["off", "extra"], ["focus"], ["audio"], ["window", "1"],
            ["window", "1", "focus", "2"], ["window", "1", "play"],
            ["window", "1", "stop", "extra"], ["layout", "unknown"],
            ["layout", "grid4", "--satellites", "1"], ["layout", "single", "0"],
            ["layout", "perimeter", "--satellites", "9"], ["layout", "perimeter", "-1"],
            ["sources", "1", "--profile"], ["sources", "1", "--unknown", "x"],
            ["sources", "1", "--provider", "UPPERCASE"], ["sources", "1", "--provider", "a/b/c"],
            ["sources", "1", "--provider", "a" * 65], ["sources", "1", "--profile", "p" * 65],
            ["sources", "1", "--search", "я" * 257], ["sources", "1", "--search", "\ud800"],
            ["sources", "1", "--search", "\x1bsecret"], ["sources", "1", "--search", "\u202esecret"],
        ]
        for value in ("https://private.invalid/stream", "/private/video.mp4", "~/Movies", "C:\\Movies", "\\\\nas\\video"):
            invalid.extend((["window", "1", "play", value], ["sources", "1", "--cursor", value],
                            ["sources", "1", "--search", value]))
        for option in ("--provider", "--profile", "--search", "--cursor"):
            invalid.append(["sources", "1", option, "first", option, "second"])
        invalid.extend((["window", "1", "play", "t" * 257], ["sources", "1", "--cursor", "c" * 257]))
        invalid.extend(["sources", "1", "--search", value] for value in
                       ("token=PRIVATE", "api_key: PRIVATE", "PASSWORD = PRIVATE", "authorization: PRIVATE"))
        for words in invalid:
            with self.subTest(words=words), self.assertRaises(ValueError):
                mv.parse(words)

    def test_maximum_tokens_and_utf8_search_are_accepted_without_normalizing(self):
        ticket = "T" * 256
        self.assertEqual(mv.parse(["window", "1", "play", ticket])["source_ticket"], ticket)
        value = mv.parse(["sources", "1", "--profile", "P" * 64, "--provider", "p" * 64,
                          "--search", "я" * 256, "--cursor", "C" * 256])
        self.assertEqual(value["profile"], "P" * 64)
        self.assertEqual(value["query"], "я" * 256)
        self.assertEqual(value["cursor"], "C" * 256)
        self.assertEqual(mv.parse(["sources", "1", "--search", ""])["query"], "")


class MultiviewProtocolTest(unittest.TestCase):
    def test_shared_fixture_success_rejections_and_capabilities(self):
        self.assertEqual(set(FIXTURE["requests"]), set(FIXTURE["results"]))
        for name, envelope in FIXTURE["requests"].items():
            request = envelope["params"]
            for status, response in (("ok", FIXTURE["results"][name]),
                                     ("rejected", FIXTURE["rejections"][name]),
                                     ("unsupported", dict(FIXTURE["rejections"][name], reason="unsupported"))):
                with self.subTest(name=name, status=status):
                    before = copy.deepcopy((request, response))
                    self.assertEqual(mv.metadata(response, request, status), response)
                    self.assertEqual((request, response), before)
        for field in ("capability", "zero_capacity_capability"):
            marker = FIXTURE[field]["multiview"]
            before = copy.deepcopy(marker)
            self.assertEqual(mv.capability(marker), marker)
            self.assertEqual(marker, before)

    def test_capability_rejects_forged_capacity_enums_and_missing_dependencies(self):
        base = FIXTURE["capability"]["multiview"]
        invalid = [None, {}, dict(base, version=True), dict(base, version=1.0), dict(base, extra=1)]
        for value in (-1, 10, True, 9.0, "9", None):
            invalid.append(dict(base, max_slots=value))
        for field in ("operations", "layouts", "source_kinds", "audio_policies", "off_modes"):
            invalid.extend(dict(base, **{field: value}) for value in (None, [True], [{}], ["unknown"], base[field] * 2))
        invalid.extend((dict(base, max_slots=0), dict(base, max_slots=4), dict(base, operations=["focus"])))
        for field in ("layouts", "source_kinds", "audio_policies", "off_modes"):
            invalid.append(dict(base, **{field: []}))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(mv.InvalidResponse):
                mv.capability(value)

    def test_receipt_never_accepts_a_different_request_or_fresh_context(self):
        request = FIXTURE["requests"]["focus"]["params"]
        for status, base in (("ok", FIXTURE["results"]["focus"]),
                             ("rejected", FIXTURE["rejections"]["focus"])):
            invalid = []
            for key, value in (("runtime", "other-runtime"), ("op", "stop"), ("version", True),
                               ("version", 1.0), ("current", {})):
                invalid.append(dict(base, **{key: value}))
            for key, value in (("slot_id", 1), ("slot_id", False), ("slot_id", 0.0),
                               ("slot_generation", "new-generation"), ("source_revision", "new-source")):
                changed = copy.deepcopy(base)
                changed["binding"][key] = value
                invalid.append(changed)
            changed = copy.deepcopy(base)
            changed["binding"]["expected"]["focus_revision"] = "focus-after-ABA"
            invalid.append(changed)
            for value in invalid:
                with self.subTest(status=status, value=value), self.assertRaises(mv.InvalidResponse):
                    mv.metadata(value, request, status)
        with self.assertRaises(mv.InvalidResponse):
            mv.metadata(dict(FIXTURE["rejections"]["focus"], reason="unsupported"), request, "rejected")
        with self.assertRaises(mv.InvalidResponse):
            mv.metadata(FIXTURE["rejections"]["focus"], request, "unsupported")

    def test_status_snapshot_order_and_owner_invariants(self):
        request = FIXTURE["requests"]["status_bound"]["params"]
        response = copy.deepcopy(FIXTURE["results"]["status_bound"])
        response["snapshot"]["slots"].reverse()
        self.assertEqual(mv.metadata(response, request), response)
        self.assertEqual(mv.metadata(FIXTURE["empty_status"], request), FIXTURE["empty_status"])
        mutations = [
            lambda x: x["slots"].pop(),
            lambda x: x["slots"][1].update(slot_id=0),
            lambda x: x.update(focus_slot=True),
            lambda x: x.update(focus_slot=4),
            lambda x: x.update(audio_policy="slot", audio_slot=None),
            lambda x: x.update(audio_policy="muted", audio_slot=0),
            lambda x: x.update(audio_policy="follows_focus", audio_slot=1),
            lambda x: x.update(satellites=1),
            lambda x: x["slots"][0].update(state="failed", error=None),
            lambda x: x["slots"][0].update(state="starting", source=None),
            lambda x: x["slots"][0].update(state="playing", source=None),
            lambda x: x["slots"][0].update(state="stalled", source=None),
            lambda x: x["slots"][0].update(error="PRIVATE error"),
            lambda x: x["slots"][0]["source"].update(ticket="PRIVATE ticket"),
        ]
        for change in mutations:
            value = copy.deepcopy(FIXTURE["results"]["status_bound"])
            change(value["snapshot"])
            with self.assertRaises(mv.InvalidResponse):
                mv.metadata(value, request)

    def test_paused_status_preserves_intent_and_requires_a_source(self):
        request = FIXTURE["requests"]["status_bound"]["params"]
        response = copy.deepcopy(FIXTURE["paused_status"])
        before = copy.deepcopy(response)
        self.assertEqual(mv.metadata(response, request), response)
        self.assertEqual(response, before)
        row = next(row for row in response["snapshot"]["slots"] if row["state"] == "paused")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            mv.render(response)
        self.assertIn(f"Window {row['slot_id'] + 1}:", output.getvalue())
        self.assertIn("player reports paused", output.getvalue())
        row["source"] = None
        with self.assertRaises(mv.InvalidResponse):
            mv.metadata(response, request)

    def test_sources_page_limits_tokens_and_private_labels(self):
        request = FIXTURE["requests"]["sources"]["params"]
        response = copy.deepcopy(FIXTURE["results"]["sources"])
        base = response["items"][0]
        response["items"] = [dict(base, source_id="source_" + str(n), ticket="ticket_" + str(n)) for n in range(128)]
        self.assertEqual(mv.metadata(response, request), response)
        response["items"].append(dict(base, source_id="source_128", ticket="ticket_128"))
        with self.assertRaises(mv.InvalidResponse):
            mv.metadata(response, request)
        for field, value in (("label", "https://private.invalid"), ("label", "/private/file"),
                             ("label", "password=PRIVATE"), ("label", "\u202ePRIVATE"),
                             ("label", "\ud800"), ("label", " "), ("profile", None),
                             ("provider", "M3U"), ("source_id", "s" * 65), ("ticket", "t" * 257)):
            response = copy.deepcopy(FIXTURE["results"]["sources"])
            response["items"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(mv.InvalidResponse):
                mv.metadata(response, request)
        for changes in ({"complete": False, "cursor": None}, {"complete": True, "cursor": "next"},
                        {"complete": 1}, {"cursor": "https://private.invalid"}):
            with self.assertRaises(mv.InvalidResponse):
                mv.metadata(dict(FIXTURE["results"]["sources"], **changes), request)
        for field in ("source_id", "ticket"):
            response = copy.deepcopy(FIXTURE["results"]["sources"])
            response["items"].append(dict(base, source_id="another", ticket="another"))
            response["items"][1][field] = base[field]
            with self.assertRaises(mv.InvalidResponse):
                mv.metadata(response, request)

    def test_raw_receipt_bounds_duplicates_integer_spellings_and_identity(self):
        request = FIXTURE["requests"]["focus"]["params"]
        identity = "1" * 32
        value = {"id": identity, "status": "ok", "data": FIXTURE["results"]["focus"]}
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.assertEqual(mv.receipt(API, raw, request, identity), value)
        padded = raw + b" " * (mv.MAX_RESPONSE - len(raw))
        self.assertEqual(mv.receipt(API, padded, request, identity), value)
        invalid = [padded + b" ", raw.replace(b'"version":1', b'"version":1.0'),
                   raw.replace(b'"slot_id":0', b'"slot_id":0e0'),
                   raw.replace(b'"slot_id":0', b'"slot_id":-0'),
                   raw.replace(b'"slot_id":0', b'"slot_id":false'),
                   raw.replace(b'"slot_id":0', b'"slot_id":0,"slot_id":0'),
                   raw.replace(b'"focus_revision":"focus-1"', b'"focus_revision":"focus-1","focus_revision":"focus-1"'),
                   raw.replace(b'"runtime":"native-runtime-1"', b'"runtime":"\\ud800"'),
                   raw.replace(b'"version":1', b'"version":NaN'),
                   raw.replace(b'"version":1', b'"version":1e999')]
        for bad in invalid:
            with self.subTest(raw=bad[:80]), self.assertRaises(Error) as raised:
                mv.receipt(API, bad, request, identity)
            self.assertEqual(str(raised.exception), mv.ERROR)
        with self.assertRaises(Error):
            mv.receipt(API, raw, request, "2" * 32)

    def test_unbound_status_still_has_a_valid_runtime_and_no_extra_binding(self):
        request = FIXTURE["requests"]["status"]["params"]
        response = FIXTURE["results"]["status"]
        self.assertEqual(mv.metadata(response, request), response)
        for value in (dict(response, runtime=None), dict(response, runtime="x" * 65),
                      dict(response, binding={"runtime": response["runtime"]})):
            with self.assertRaises(mv.InvalidResponse):
                mv.metadata(value, request)


class MultiviewRunTest(unittest.TestCase):
    def setUp(self):
        self.client = Client()

    def execute(self, words=(), machine=True):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mv.run(API, self.client, "dev-1", list(words), 45, machine)
        finally:
            self.assertEqual(self.client.timeout, 45)
        return code, json.loads(out.getvalue()) if machine else out.getvalue()

    def operations(self):
        return [(action, params) for _, action, params, _ in self.client.calls]

    def test_status_uses_only_probe_and_bound_status(self):
        code, result = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(result, FIXTURE["results"]["status_bound"])
        self.assertEqual(self.operations(), [("capabilities", {"multiview": True}),
                         ("multiview", FIXTURE["requests"]["status_bound"]["params"])])

    def test_each_operation_uses_fresh_context_and_one_request(self):
        for name, words in (
                ("sources", ["sources", "1"]), ("layout", ["layout", "grid4"]),
                ("layout_perimeter", ["layout", "perimeter"]),
                ("play", ["window", "1", "play", "source_ticket_1"]), ("focus", ["focus", "1"]),
                ("audio", ["audio", "1"]), ("audio_follow", ["audio", "follows-focus"]),
                ("audio_muted", ["audio", "off"]), ("stop", ["window", "1", "stop"]),
                ("off", ["off"]), ("off_restore", ["off", "restore_previous"])):
            with self.subTest(name=name):
                self.client = Client()
                self.assertEqual(self.execute(words)[0], 0)
                self.assertEqual(self.operations(), [("capabilities", {"multiview": True}),
                                 ("multiview", FIXTURE["requests"]["status_bound"]["params"]),
                                 ("multiview", FIXTURE["requests"][name]["params"])])

    def test_slot_nine_is_not_decremented_twice_and_array_order_is_irrelevant(self):
        snapshot = self.client.state["snapshot"]
        snapshot["layout"] = "grid9"
        empty = snapshot["slots"][1]
        snapshot["slots"] = [dict(empty, slot_id=i, slot_generation=f"slot-{i}-generation-1",
                                  source_revision=f"slot-{i}-source-1") for i in reversed(range(9))]
        self.execute(["focus", "9"])
        request = self.operations()[-1][1]
        self.assertEqual((request["slot_id"], request["slot_generation"], request["source_revision"]),
                         (8, "slot-8-generation-1", "slot-8-source-1"))

    def test_invalid_input_and_help_never_call_client(self):
        for words in (["focus", "0"], ["window", "10", "stop"], ["sources", "1", "--cursor", "https://private.invalid"]):
            with self.assertRaises(Error):
                self.execute(words)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mv.run(API, self.client, "dev-1", ["--help"], 45, False), 0)
        self.assertIn("Windows are numbered 1 through 9", out.getvalue())
        self.assertEqual(self.client.calls, [])

    def test_zero_capacity_and_unsupported_markers_do_not_send_mutations(self):
        self.client.marker = copy.deepcopy(FIXTURE["zero_capacity_capability"]["multiview"])
        self.client.state = copy.deepcopy(FIXTURE["empty_status"])
        self.assertEqual(self.execute()[0], 0)
        for words in (["focus", "1"], ["layout", "single"], ["off"], ["sources", "1"]):
            self.client.calls.clear()
            with self.assertRaises(Error):
                self.execute(words)
            self.assertEqual(self.operations(), [("capabilities", {"multiview": True})])
        for marker in (None, {}, dict(FIXTURE["capability"]["multiview"], version=2)):
            self.client.marker = marker
            self.client.calls.clear()
            with self.assertRaises(Error):
                self.execute(["focus", "1"])
            self.assertEqual(len(self.client.calls), 1)

    def test_missing_slot_and_capacity_limits_fail_without_dispatch(self):
        with self.assertRaisesRegex(Error, "not in the current layout"):
            self.execute(["focus", "9"])
        self.assertEqual(len(self.client.calls), 2)
        self.client = Client()
        self.client.marker.update(max_slots=4, layouts=["single", "pip", "grid4", "perimeter"])
        for words in (["focus", "9"], ["layout", "grid9"], ["layout", "perimeter", "4"]):
            self.client.calls.clear()
            with self.assertRaises(Error):
                self.execute(words)
            self.assertEqual(len(self.client.calls), 1)

    def test_stale_rejection_is_not_refreshed_or_retried(self):
        def reject(value, request):
            if request.get("op") == "focus":
                raise PlayerRejected({key: value[key] for key in ("version", "runtime", "op", "binding")}
                                     | {"reason": "focus_changed"})
            return value
        self.client.transform = reject
        with self.assertRaisesRegex(Error, "focus_changed"):
            self.execute(["focus", "1"])
        self.assertEqual(len(self.client.calls), 3)

    def test_invalid_or_lost_mutation_ack_never_repeats(self):
        for lost in (False, True):
            self.client = Client()
            def fail(value, request):
                if request.get("op") == "play":
                    if lost:
                        raise Error("Receipt unavailable; the request may have executed")
                    return dict(value, runtime="another-runtime", private="PRIVATE token")
                return value
            self.client.transform = fail
            with self.assertRaises(Error) as raised:
                self.execute(["window", "1", "play", "ticket"])
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertEqual(len(self.client.calls), 3)

    def test_one_deadline_and_cancellation_prevent_late_mutation(self):
        for cancel in (False, True):
            self.client = Client()
            self.client.group_cancelled = threading.Event()
            clock = [0.0]
            def before(client, action, request):
                clock[0] += 4 if action == "capabilities" else 41
                if cancel and request.get("op") == "status":
                    client.group_cancelled.set()
            self.client.before = before
            with mock.patch.object(mv.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaises(KeyboardInterrupt if cancel else Error):
                    self.execute(["focus", "1"])
            self.assertEqual(len(self.client.calls), 2)
            self.assertEqual([row[3] for row in self.client.calls], [45, 41])

    def test_valid_terminal_reply_is_kept_when_deadline_or_cancel_arrives(self):
        self.client.group_cancelled = threading.Event()
        clock = [0.0]
        def before(client, action, request):
            if request.get("op") == "focus":
                clock[0] = 46.0
                client.group_cancelled.set()
        self.client.before = before
        with mock.patch.object(mv.time, "monotonic", side_effect=lambda: clock[0]):
            code, result = self.execute(["focus", "1"])
        self.assertEqual((code, result["outcome"]), (0, "requested"))
        self.assertEqual(len(self.client.calls), 3)

    def test_read_rejection_and_probe_failure_do_not_fallback(self):
        for stage in ("capabilities", "status"):
            self.client = Client()
            def before(client, action, request):
                if action == stage or request.get("op") == stage:
                    raise PlayerUnsupported({"PRIVATE": "https://private.invalid"})
            self.client.before = before
            with self.assertRaises(Error) as raised:
                self.execute(["focus", "1"])
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertNotIn("private.invalid", str(raised.exception))
            self.assertEqual(len(self.client.calls), 1 if stage == "capabilities" else 2)

    def test_plain_render_displays_human_slot_numbers_without_playback_claims(self):
        code, text = self.execute(machine=False)
        self.assertEqual(code, 0)
        self.assertIn("Window 1:", text)
        self.assertNotIn("Window 0:", text)
        self.assertIn("does not establish visible playback", text)


if __name__ == "__main__":
    unittest.main()
