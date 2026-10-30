"""Live playback and retained reports stay distinct, bounded and read-only."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ott_playback_status_tests", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
reports = ott.playback_status_module()
STAMP = "2026-10-29T12:00:00Z"
PLAYER = {"version": "1.1.62", "platform": "browser", "standby": False}
PLAYBACK = {"state": "playing", "kind": "vod", "title": "Example episode", "provider": "plex",
            "media_id": "12345", "channel_number": None, "position": 100.5, "duration": 500, "volume": 50}


def fixture(device="dev_one"):
    row = {"runtime": "page:one", "sequence": 1, "received_at": STAMP, "age_ms": 0,
           "stale": False, "restored": False, "player": copy.deepcopy(PLAYER), "playback": copy.deepcopy(PLAYBACK)}
    return {"version": 1, "controller_epoch": "a" * 32, "device_id": device, "server_time": STAMP,
            "storage": "memory", "stale_after_ms": 45000, "retention_seconds": 86400,
            "connection": {"state": "online", "last_seen_at": STAMP}, "ambiguous": False,
            "current": copy.deepcopy(row), "latest": copy.deepcopy(row), "runtimes": [row],
            "history": [], "history_truncated": True}


def sync_rows(value):
    value["latest"] = copy.deepcopy(value["runtimes"][0]) if value["runtimes"] else None
    fresh = [row for row in value["runtimes"] if not row["stale"]]
    value["current"] = copy.deepcopy(fresh[0]) if len(fresh) == 1 else None
    value["ambiguous"] = len(fresh) > 1
    return value


def event(row, kind="connected"):
    return {"event": kind, **{key: copy.deepcopy(row[key]) for key in
                              ("runtime", "sequence", "received_at", "player", "playback")}}


class PlaybackProtocolTest(unittest.TestCase):
    def test_full_dto_states_nullable_fields_and_independent_copies(self):
        for state in reports.STATES:
            data = dict(PLAYBACK, state=state)
            if state in ("idle", "ended", "standby"):
                data.update(title=None, media_id=None, channel_number=None)
            value = reports.validate_playback(data, state == "standby")
            self.assertEqual(value, data)
            value["title"] = "changed"
            self.assertNotEqual(value, data)
        data = dict.fromkeys(PLAYBACK)
        data.update(state="unknown", kind="unknown")
        self.assertEqual(reports.validate_playback(data, False), data)

    def test_rejects_dto_wrong_types_bounds_unknown_missing_and_inactive_content(self):
        cases = [{**PLAYBACK, "private": "secret"}, {key: value for key, value in PLAYBACK.items() if key != "title"}]
        for key, values in {
            "state": (None, [], "stopped"), "kind": (False, "movie"),
            "title": ("", " padded", "x\n", "\ud800", "é" * 129),
            "provider": ("x" * 65,), "media_id": ("../secret", "http://secret", "a:b"),
            "position": (True, -1, 9007199254740992, float("nan"), float("inf")),
            "duration": ("1", -1), "volume": (False, 101), "channel_number": (False, 0, 1.5, 2147483648),
        }.items():
            cases.extend(dict(PLAYBACK, **{key: value}) for value in values)
        cases.extend(dict(PLAYBACK, state=state) for state in ("standby", "idle", "ended"))
        for data in cases:
            with self.subTest(data=data), self.assertRaises(reports.InvalidReport):
                reports.validate_playback(data, False)
        with self.assertRaises(reports.InvalidReport):
            reports.validate_playback(PLAYBACK, True)
        self.assertEqual(reports.validate_playback(dict(PLAYBACK, title="é" * 128), False)["title"], "é" * 128)

    def test_private_labels_redacted_in_live_and_retained_json_but_punctuation_retained(self):
        for private in ("https://private.invalid/token", "/private/movie", "C:\\private\\movie", "\\server\\movie", "~/movie",
                        "\u200b/private/example", "https:\u200b/\u2060/private.invalid", "\ufeffC:\u200b\\private", "\ue000/private"):
            raw = fixture()
            raw["runtimes"][0]["playback"].update(title=private, provider=private)
            sync_rows(raw)
            actual = reports.validate_response(raw, "dev_one", 0)
            self.assertNotIn(private, json.dumps(actual))
            self.assertIsNone(actual["latest"]["playback"]["title"])
            status = {"player": dict(PLAYER, runtime="page-one"), "playback": dict(PLAYBACK, title=private)}
            self.assertIsNone(reports.live_status(status)["playback"]["title"])
        for title in ("AC/DC: Live", "Film (2026) — Part 2", "Episode/Chapter", "Лучшее кино", "Episode\u200b title"):
            self.assertEqual(reports.validate_playback(dict(PLAYBACK, title=title), False)["title"], title)

    def test_invisible_path_prefix_and_split_url_never_render_or_survive_json(self):
        for private in ("\u200b/private/example", "http\u200bs:\u2060/\u200b/private.invalid", "\u200b ~/private"):
            raw = fixture()
            raw["runtimes"][0]["playback"].update(title=private, provider=private)
            sync_rows(raw)
            raw["history"] = [event(raw["runtimes"][0])]
            public = reports.validate_response(raw, "dev_one", 1)
            for row in (public["current"], public["latest"], *public["runtimes"], *public["history"]):
                self.assertIsNone(row["playback"]["title"])
                self.assertIsNone(row["playback"]["provider"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                reports.print_report(public, "one", history=True)
            self.assertNotIn("private", out.getvalue())
            self.assertNotIn("private", json.dumps(public))
            self.assertEqual(reports.description(dict(PLAYBACK, title=private)), "Content not reported (playing)")

    def test_retained_fresh_stale_restored_backward_clock_and_empty(self):
        self.assertEqual(reports.validate_response(fixture(), "dev_one", 0), fixture())
        for stamp, age, restored in (("2026-10-29T12:00:45Z", 45000, False), (STAMP, 0, True),
                                     ("2026-10-29T11:59:59Z", 0, False)):
            raw = fixture()
            raw["server_time"] = stamp
            raw["runtimes"][0].update(age_ms=age, stale=True, restored=restored)
            if stamp != STAMP:
                raw["connection"]["state"] = "offline"
            sync_rows(raw)
            actual = reports.validate_response(raw, "dev_one", 0)
            self.assertIsNone(actual["current"])
            self.assertTrue(actual["latest"]["stale"])
        raw = fixture()
        raw.update(runtimes=[], current=None, latest=None, history_truncated=False,
                   connection={"state": "unknown", "last_seen_at": None})
        self.assertEqual(reports.validate_response(raw, "dev_one", 0), raw)

    def test_runtimes_and_history_preserve_ambiguity_exact_order_and_limits(self):
        raw = fixture()
        second = copy.deepcopy(raw["runtimes"][0])
        second["runtime"] = "page:two"
        raw["runtimes"].append(second)
        sync_rows(raw)
        raw["history"] = [event(second), event(raw["runtimes"][0])]
        self.assertTrue(reports.validate_response(raw, "dev_one", 2)["ambiguous"])
        bads = []
        wrong = copy.deepcopy(raw)
        wrong["current"] = copy.deepcopy(second)
        bads.append(wrong)
        wrong = copy.deepcopy(raw)
        wrong["runtimes"].reverse()
        wrong["latest"] = wrong["runtimes"][0]
        bads.append(wrong)
        wrong = copy.deepcopy(raw)
        wrong["runtimes"][1]["runtime"] = "page:one"
        bads.append(wrong)
        wrong = copy.deepcopy(raw)
        wrong["runtimes"] *= 3
        bads.append(wrong)
        wrong = copy.deepcopy(raw)
        wrong["history"][1]["received_at"] = "2026-10-29T12:00:01Z"
        bads.append(wrong)
        for wrong in bads:
            with self.assertRaises(reports.InvalidReport):
                reports.validate_response(wrong, "dev_one", 2)
        with self.assertRaises(reports.InvalidReport):
            reports.validate_response(raw, "dev_one", 1)

    def test_rejects_wrong_identity_freshness_timestamp_and_nested_extra_fields(self):
        cases = []
        for key, value in (("device_id", "other"), ("version", True), ("controller_epoch", "A" * 32),
                           ("storage", "elsewhere"), ("stale_after_ms", 45001), ("retention_seconds", True),
                           ("server_time", "2026-02-30T00:00:00Z"), ("server_time", "2026-10-29T12:00:00")):
            cases.append(dict(fixture(), **{key: value}))
        for key, value in (("age_ms", 1), ("stale", True), ("restored", True), ("sequence", True)):
            raw = fixture()
            raw["runtimes"][0][key] = value
            cases.append(sync_rows(raw))
        raw = fixture()
        raw["connection"]["state"] = "offline"
        cases.append(raw)
        raw = fixture()
        raw["runtimes"][0]["player"]["url"] = "private"
        cases.append(sync_rows(raw))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(reports.InvalidReport):
                reports.validate_response(raw, "dev_one", 0)

    def test_nanosecond_freshness_boundary_and_offset_equivalence(self):
        raw = fixture()
        raw["server_time"] = "2026-10-29T12:00:44.999999999Z"
        raw["runtimes"][0]["age_ms"] = 44999
        sync_rows(raw)
        self.assertIsNotNone(reports.validate_response(raw, "dev_one", 0)["current"])
        self.assertEqual(reports.timestamp("2026-10-29T13:00:00+01:00"), reports.timestamp(STAMP))

    def test_wire_duplicate_keys_nonfinite_oversize_and_invalid_utf8_rejected(self):
        for raw in (b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b' ' * (reports.MAX_BYTES + 1)):
            with self.assertRaises(reports.InvalidReport):
                reports.decode(raw)

    def test_player_identity_fields_cannot_carry_private_labels_in_any_retained_row(self):
        for key in ("version", "platform"):
            for private in ("https://private.invalid/token", "/private/info", "C:\\private", "\\private", "~/private",
                            "\u200b/private/example", "https:\u200b/\u2060/private.invalid"):
                for location in ("current", "latest", "runtimes", "history"):
                    raw = fixture()
                    raw["history"] = [event(raw["runtimes"][0])]
                    row = raw[location][0] if location in ("runtimes", "history") else raw[location]
                    row["player"][key] = private
                    with self.subTest(key=key, private=private, location=location), self.assertRaises(reports.InvalidReport):
                        reports.validate_response(raw, "dev_one", 1)


class PlaybackCliTest(unittest.TestCase):
    def client(self):
        client = mock.Mock(timeout=45)
        client.config = {"players": {"one": "dev_one", "two": "dev_two"}, "groups": {"both": ["one", "two"]}}
        client.credentials = {"devices": []}
        client.device.side_effect = lambda name: client.config["players"][name]

        def read(path, timeout=None):
            query = ott.urllib.parse.parse_qs(ott.urllib.parse.urlsplit(path).query)
            self.assertEqual(ott.urllib.parse.urlsplit(path).path, "/api/player-state")
            raw = fixture(query["device_id"][0])
            if query["limit"] != ["0"]:
                raw["history"] = [event(raw["runtimes"][0])]
            return 200, json.dumps(raw).encode()
        client.api_raw.side_effect = read
        return client

    def run_cli(self, words, client=None):
        client = client or self.client()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=client.config), \
                mock.patch.object(ott, "Client", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client

    def test_state_and_history_use_one_get_no_rpc_capabilities_or_retry(self):
        for words, limit in ((["state"], 0), (["history"], 20), (["history", "--limit", "64"], 64)):
            code, out, err, client = self.run_cli(["-j", "one", *words])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["device_id"], "dev_one")
            self.assertEqual(client.api_raw.call_count, 1)
            self.assertEqual(client.api_raw.call_args.args, (f"/api/player-state?device_id=dev_one&limit={limit}",))
            self.assertGreater(client.api_raw.call_args.kwargs["timeout"], 0)
            self.assertLessEqual(client.api_raw.call_args.kwargs["timeout"], 45)
            client.call.assert_not_called()
            client.call_receipt.assert_not_called()
            self.assertEqual(client.timeout, 45)

    def test_bad_grammar_rejected_before_any_target_request(self):
        for words in (["state", "now"], ["state", "--limit", "1"], ["history", "2"], ["history", "--limit"],
                      ["history", "--limit", "0"], ["history", "--limit", "65"], ["history", "--limit", "1.0"],
                      ["history", "--limit", "1", "--limit", "2"]):
            code, _, err, client = self.run_cli(["one,two", *words])
            self.assertEqual(code, 1)
            self.assertIn("Use state or history", err)
            client.api_raw.assert_not_called()
            client.call.assert_not_called()

    def test_group_list_and_saved_group_are_per_device_get_only(self):
        for selector in ("one,two", "both"):
            code, out, err, client = self.run_cli(["-j", selector, "state"])
            self.assertEqual((code, err), (0, ""))
            rows = json.loads(out)["results"]
            self.assertEqual([row["data"]["device_id"] for row in rows], ["dev_one", "dev_two"])
            self.assertTrue(all(row["ok"] for row in rows))
            self.assertEqual(client.api_raw.call_count, 2)
            client.call.assert_not_called()

    def test_retained_errors_are_safe_without_live_fallback(self):
        for error, expected in ((ott.HTTPError(404), "update the controller"), (ott.HTTPError(403), "status permission"),
                                (ott.HTTPError(503), "could not return"), (ott.Error("private-token"), "could not be read")):
            client = self.client()
            client.api_raw.side_effect = error
            code, out, err, client = self.run_cli(["one", "state"], client)
            self.assertEqual((code, out), (1, ""))
            self.assertIn(expected, err)
            self.assertNotIn("private-token", err)
            self.assertEqual(client.api_raw.call_count, 1)
            client.call.assert_not_called()

    def test_retained_text_always_labels_source_age_and_ambiguity_not_live(self):
        raw = fixture()
        raw["runtimes"].append(dict(copy.deepcopy(raw["runtimes"][0]), runtime="page:two"))
        sync_rows(raw)
        raw["history"] = [event(raw["runtimes"][0])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            reports.print_report(raw, "one", history=True)
        text = out.getvalue()
        self.assertIn("Source: controller-retained reports", text)
        self.assertIn("Last reported: Example episode (playing)", text)
        self.assertIn("0s ago; fresh report", text)
        self.assertIn("ambiguous", text)
        self.assertIn("Older retained events omitted", text)
        self.assertNotIn("Now playing:", text)
        self.assertIn("do not prove physical", text)

    def test_live_status_uses_validated_dto_and_existing_two_rpc_budget(self):
        for state in ("playing", "paused", "buffering", "standby"):
            client = self.client()
            playback = dict(PLAYBACK, state=state)
            if state == "standby":
                playback.update(title=None, media_id=None)
            player = dict(PLAYER, runtime="page-one", standby=state == "standby")
            status = {"player": player, "playback": playback, "title": "Wrong selected title"}
            caps = {"version": 1, "player": dict(player), "lifecycle": [], "input": [], "playback": []}
            client.call.side_effect = [status, caps]
            code, out, err, client = self.run_cli(["one"], client)
            self.assertEqual((code, err), (0, ""))
            self.assertIn("Now playing:  " + ("Nothing (standby)" if state == "standby" else "Example episode (" + state + ")"), out)
            self.assertNotIn("Wrong selected title", out)
            self.assertEqual(client.call.call_args_list, [mock.call("dev_one", "status", {}), mock.call("dev_one", "capabilities", {})])
            client.api_raw.assert_not_called()

    def test_bad_live_playback_degrades_only_additive_field(self):
        for data in (dict(PLAYBACK, position=True), dict(PLAYBACK, state="standby"), None, {"title": "secret"}):
            client = self.client()
            player = dict(PLAYER, runtime="page-one")
            status = {"player": player, "volume": 35, "playback": data}
            caps = {"version": 1, "player": dict(player), "lifecycle": [], "input": [], "playback": []}
            client.call.side_effect = [status, caps]
            code, out, err, _ = self.run_cli(["-j", "one"], client)
            value = json.loads(out)
            self.assertEqual((code, err, value["volume"]), (0, "", 35))
            self.assertNotIn("playback", value)
            self.assertIn("playback_error", value)
            self.assertNotIn("secret", out)
            self.assertIsNotNone(value["capabilities"])

    def test_retained_read_deadline_cannot_be_extended_by_response_or_validation(self):
        for ticks, called in (([0, 6], False), ([0, 0, 6], True), ([0, 0, 1, 6], True)):
            client = self.client()
            with mock.patch.object(reports.time, "monotonic", side_effect=ticks), self.assertRaises(ott.Error):
                reports.read(ott, client, "dev_one", {"limit": 0}, 5)
            self.assertEqual(client.api_raw.call_count, int(called))
            client.call.assert_not_called()

    def test_group_bad_report_is_not_retried_and_next_player_is_still_read(self):
        client = self.client()
        client.api_raw.side_effect = [(200, b'{"unexpected":"private-token"}'),
                                      (200, json.dumps(fixture("dev_two")).encode())]
        code, out, err, _ = self.run_cli(["-j", "both", "state"], client)
        result = json.loads(out)
        self.assertEqual((code, err, result["succeeded"], result["failed"]), (1, "", 1, 1))
        self.assertNotIn("private-token", out)
        self.assertEqual(client.api_raw.call_count, 2)
        client.call.assert_not_called()

    def test_private_player_identity_never_reaches_json_or_error_output(self):
        for key in ("version", "platform"):
            for location in ("current", "latest", "runtimes", "history"):
                client = self.client()
                raw = fixture()
                raw["history"] = [event(raw["runtimes"][0])]
                row = raw[location][0] if location in ("runtimes", "history") else raw[location]
                row["player"][key] = "/private/secret-value"
                client.api_raw.return_value = 200, json.dumps(raw).encode()
                client.api_raw.side_effect = None
                code, out, err, _ = self.run_cli(["-j", "one", "history"], client)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("could not be read or validated", err)
                self.assertNotIn("secret-value", err)
                self.assertEqual(client.api_raw.call_count, 1)
                client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
