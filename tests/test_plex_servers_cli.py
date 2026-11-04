"""Managed Plex selection through the real CLI/receipt code, without network IO."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_plex_servers", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
FIXTURES = json.loads((ROOT / "testdata/plex_servers_v1.json").read_text(encoding="utf-8"))
CLIENT_TYPE = ott.Client
REQUEST_ID = FIXTURES["expected_request_id"]
CAP_ID = "c" * 32
CONTEXT = "ctx-0123456789abcdef0123456789abcdef"
SECRET = "private-token-must-not-appear"


def caps():
    return {"version": 1, "player": {"version": "fixture", "platform": "native", "runtime": "native-qa-1"},
            "lifecycle": [], "input": [], "playback": [],
            "plex_servers": {"version": 1, "operations": ["list", "select"], "max_servers": 1}}


def fixture(name):
    return next(case for case in FIXTURES["reply_cases"] if case["id"] == name)


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Transport:
    """Only replace HTTP; retain Client.call, raw decoding, polling and callbacks."""
    def __init__(self, clock, response="list-one"):
        self.clock = clock
        self.controls = caps()
        self.response = fixture(response)["response_json"].encode()
        self.posts = []
        self.gets = []
        self.receipts = []
        self.post_error = None
        self.poll_error = None
        self.pending = 0
        self.cap_delay = 0
        self.reply_delay = 0
        self.client = CLIENT_TYPE.__new__(CLIENT_TYPE)
        self.client.timeout = 45
        self.client.config = {"players": {"tv": "dev_tv", "t1": "dev_t1", "t2": "dev_t2"}}
        self.client.credentials = {"devices": []}
        self.client.api = self.api
        self.client.on_receipt = self.receipts.append

    def api(self, path, payload=None, timeout=None, *, raw=False):
        if payload is not None:
            self.posts.append((copy.deepcopy(payload), timeout))
            if payload["action"] == "capabilities":
                return 200, {"id": CAP_ID}
            if self.post_error:
                raise self.post_error
            return 200, {"id": REQUEST_ID, "receipt_wait_ms": 1000}
        self.gets.append((path, timeout, raw))
        if CAP_ID in path:
            self.clock.now += self.cap_delay
            return 200, {"id": CAP_ID, "status": "ok", "data": self.controls}
        if self.poll_error:
            raise self.poll_error
        if self.pending:
            self.pending -= 1
            self.clock.now += 0.2
            return 202, b'{"status":"pending"}' if raw else {"status": "pending"}
        self.clock.now += self.reply_delay
        return 200, self.response if raw else ott.decode_response_json(self.response)


class PlexServersTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.clock_patch = mock.patch.object(ott.time, "monotonic", self.clock.monotonic)
        self.sleep_patch = mock.patch.object(ott.time, "sleep", self.clock.sleep)
        self.clock_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.sleep_patch.stop)

    def run_cli(self, transport, words, *, player="tv", json_output=True, group=False):
        output, errors = io.StringIO(), io.StringIO()
        config = transport.client.config
        if group:
            config["groups"] = {"twins": ["t1", "t2"]}
        with mock.patch.object(ott, "Client", return_value=transport.client), \
                mock.patch.object(ott, "read_json", return_value=config), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main((["--json"] if json_output else []) + [player, *words])
        self.assertEqual(transport.client.timeout, 45)
        return code, output.getvalue(), errors.getvalue()

    def test_shared_capabilities(self):
        for case in FIXTURES["capability_cases"]:
            with self.subTest(case=case["id"]):
                try:
                    marker = ott.plex_servers_decode(case["marker_json"]) if case["marker_json"] is not None else None
                    ott.plex_servers_capability(marker)
                    valid = True
                except ott.Error:
                    valid = False
                self.assertEqual(valid, case["valid"])

    def test_shared_raw_replies(self):
        for case in FIXTURES["reply_cases"]:
            with self.subTest(case=case["id"]):
                try:
                    ott.plex_servers_receipt(case["response_json"].encode(), case["request_params"], REQUEST_ID)
                    valid = True
                except ott.Error:
                    valid = False
                self.assertEqual(valid, case["valid"])

    def test_shared_request_grammar_and_decoder(self):
        # Raw ingress size is the controller's responsibility; reuse its vectors
        # to check the existing strict decoder plus this action's param grammar.
        for case in FIXTURES["request_cases"]:
            with self.subTest(case=case["id"]):
                try:
                    body = ott.plex_servers_decode(case["request_json"], 16384)
                    if not isinstance(body, dict) or set(body) != {"action", "params"} or body["action"] != "plex_servers":
                        raise ott.Error("Invalid request envelope")
                    ott.plex_servers_request(body["params"])
                    valid = True
                except ott.Error:
                    valid = False
                self.assertEqual(valid, case["valid"])

    def test_parser_reserves_only_servers_and_preserves_legacy_routes(self):
        self.assertEqual(ott.parse_command(["PlEx", "SeRvErS"]), ("plex_servers", {"version": 1, "op": "list"}))
        action, params = ott.parse_command(["plex", "servers", "SELECT", "PMS-1", "--context", CONTEXT])
        self.assertEqual(action, "plex_servers")
        self.assertEqual(params, {"version": 1, "op": "select", "server_id": "PMS-1", "context": CONTEXT})
        self.assertEqual(ott.parse_command(["plex", "server", "http://fixture.invalid:32400"])[0], "provider_settings")
        self.assertEqual(ott.parse_command(["plex", "libraries"])[0], "plex_library")
        self.assertEqual(ott.parse_command(["plex", "play", "16"])[0], "plex_queue")
        self.assertEqual(ott.parse_command(["profile", "1"])[0], "profile")
        for suffix in (["list"], ["select"], ["select", "PMS-1"], ["select", "PMS-1", CONTEXT],
                       ["select", "PMS-1", "--context", CONTEXT, "extra"],
                       ["select", "bad/id", "--context", CONTEXT], ["select", "PMS-1", "--context", ""],
                       ["select", "PMS-1", "--context", "private/context"], ["server", "http://fixture.invalid"]):
            transport = Transport(self.clock)
            code, out, _ = self.run_cli(transport, ["plex", "servers", *suffix])
            self.assertEqual((code, out, transport.posts), (1, "", []), suffix)

    def test_actual_cli_lists_then_selects_once_with_fresh_capabilities(self):
        for name, words in (("list-one", ["plex", "servers"]),
                            ("select-applied", ["plex", "servers", "select", "PMS-1", "--context", CONTEXT])):
            with self.subTest(name=name):
                transport = Transport(self.clock, name)
                code, out, err = self.run_cli(transport, words)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), json.loads(fixture(name)["response_json"])["data"])
                self.assertEqual([p[0]["action"] for p in transport.posts], ["capabilities", "plex_servers"])
                self.assertEqual(transport.posts[-1][0]["params"], fixture(name)["request_params"])
                self.assertLess(transport.posts[-1][1], transport.posts[0][1])
                self.assertTrue(transport.gets[-1][2], "Final response must retain original bytes")

    def test_no_capability_or_invalid_capability_never_sends_action(self):
        for case in FIXTURES["capability_cases"]:
            if case["valid"]:
                continue
            transport = Transport(self.clock)
            raw = case["marker_json"]
            if raw is None:
                del transport.controls["plex_servers"]
            else:
                try:
                    transport.controls["plex_servers"] = ott.decode_response_json(raw)
                except ValueError:
                    continue  # Already exercised before projection above.
            code, out, err = self.run_cli(transport, ["plex", "servers"])
            self.assertEqual((code, out, len(transport.posts)), (1, "", 1), case["id"])
            self.assertIn("No server command was sent", err)
            self.assertNotIn(SECRET, err)
        transport = Transport(self.clock)
        transport.controls["native_playback_diagnostics"] = {"version": 1}
        transport.controls["player"]["runtime"] = "Native.Mixed"
        self.assertEqual(self.run_cli(transport, ["plex", "servers"])[0], 1)
        self.assertEqual(len(transport.posts), 1)

    def test_one_budget_no_action_after_capability_deadline(self):
        transport = Transport(self.clock)
        transport.cap_delay = 45
        code, out, _ = self.run_cli(transport, ["plex", "servers"])
        self.assertEqual((code, out, len(transport.posts)), (1, "", 1))

    def test_multi_target_context_rejected_before_any_requests(self):
        for player, group in (("t1,t2", False), ("twins", True)):
            transport = Transport(self.clock, "select-applied")
            code, out, err = self.run_cli(transport, ["plex", "servers", "select", "PMS-1", "--context", CONTEXT],
                                          player=player, group=group)
            self.assertEqual((code, out, transport.posts, transport.gets), (1, "", [], []))
            self.assertIn("exactly one player", err)

    def test_group_list_gets_independent_capabilities_and_runtime(self):
        transport = Transport(self.clock)
        children = []

        def for_group(cancelled):
            child = Transport(self.clock)
            runtime = "receiver-" + str(len(children) + 1)
            child.controls["player"]["runtime"] = runtime
            reply = json.loads(child.response)
            reply["data"]["runtime"] = runtime
            child.response = json.dumps(reply).encode()
            child.client.group_cancelled = cancelled
            children.append(child)
            return child.client

        transport.client.for_group = for_group
        code, out, err = self.run_cli(transport, ["plex", "servers"], player="t1,t2")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(transport.posts, [])
        self.assertEqual(len(children), 2)
        for child in children:
            self.assertEqual([p[0]["action"] for p in child.posts], ["capabilities", "plex_servers"])
            self.assertEqual(child.posts[-1][0]["params"], {
                "version": 1, "op": "list", "runtime": child.controls["player"]["runtime"]})
        self.assertIsNot(children[0].posts[-1][0]["params"], children[1].posts[-1][0]["params"])

    def test_pending_receipt_polls_do_not_resubmit(self):
        transport = Transport(self.clock, "select-applied")
        transport.pending = 3
        code, _, err = self.run_cli(transport, ["plex", "servers", "select", "PMS-1", "--context", CONTEXT])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(transport.posts), 2)
        self.assertEqual(len(transport.gets), 5)

    def test_lost_post_or_receipt_stays_uncertain_without_replay(self):
        for failure in ("post", "receipt", "late", "pending"):
            transport = Transport(self.clock, "select-applied")
            if failure == "post":
                transport.post_error = ott.TransportError("fixture timeout", "deadline")
            elif failure == "receipt":
                transport.poll_error = ott.HTTPError(404)
            elif failure == "late":
                transport.reply_delay = 45
            else:
                transport.pending = 1000
            code, out, err = self.run_cli(transport, ["plex", "servers", "select", "PMS-1", "--context", CONTEXT])
            self.assertEqual((code, out, len(transport.posts)), (1, "", 2), failure)
            self.assertIn("may", err, failure)
            self.assertNotIn("activated temporarily", err)
            self.assertNotIn("no server command was sent", err)

    def test_raw_negative_receipts_reach_transport_validation(self):
        names = ("whole-reply-bytes-65537", "duplicate-outer-data", "duplicate-runtime", "escaped-duplicate-row-name",
                 "outer-extra-field", "different-request-id", "error-with-ok-status", "ready-with-rejected-status")
        for name in names:
            transport = Transport(self.clock, name)
            code, out, err = self.run_cli(transport, ["plex", "servers"])
            self.assertEqual((code, out, len(transport.posts)), (1, "", 2), name)
            self.assertIn("invalid", err)
        transport = Transport(self.clock, "whole-reply-bytes-65536")
        self.assertEqual(self.run_cli(transport, ["plex", "servers"])[0], 0)

    def test_known_errors_are_closed_and_never_replayed(self):
        for problem in ott.PLEX_SERVERS_ERRORS:
            transport = Transport(self.clock, "select-error-" + problem)
            code, out, err = self.run_cli(transport, ["plex", "servers", "select", "PMS-1", "--context", CONTEXT])
            self.assertEqual((code, out, len(transport.posts)), (1, "", 2), problem)
            self.assertIn("not repeated", err)
            self.assertNotIn("activated temporarily", err)
            if problem == "expired":
                self.assertIn("request or selection context expired before admission", err)
        transport = Transport(self.clock)
        transport.response = json.dumps({"id": REQUEST_ID, "status": "rejected", "data": {"error": SECRET}}).encode()
        _, _, err = self.run_cli(transport, ["plex", "servers"])
        self.assertNotIn(SECRET, err)

    def test_direct_receipt_callbacks_keep_action_and_request_id(self):
        transport = Transport(self.clock, "select-applied")
        ott.plex_servers_command(transport.client, "dev_tv", fixture("select-applied")["request_params"], 45)
        self.assertEqual(transport.receipts, [
            {"action": "capabilities", "request_id": CAP_ID, "status": "ok"},
            {"action": "plex_servers", "request_id": REQUEST_ID, "status": "ok"}])
        transport = Transport(self.clock)
        transport.response = b"{}"
        with self.assertRaises(ott.RequestError) as raised:
            ott.plex_servers_command(transport.client, "dev_tv", {"version": 1, "op": "list"}, 45)
        self.assertEqual(raised.exception.action, "plex_servers")
        self.assertEqual(raised.exception.request_id, REQUEST_ID)

    def test_help_requires_valid_marker_and_output_is_truthful(self):
        controls = caps()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_help({"capabilities": controls}, "tv")
        self.assertIn("plex servers select SERVER_ID --context CONTEXT", out.getvalue())
        del controls["plex_servers"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_help({"capabilities": controls}, "tv")
        self.assertNotIn("plex servers", out.getvalue())
        controls["plex_servers"] = {"version": 1, "operations": ["list"], "max_servers": 1}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(ott.Error):
            ott.print_player_help({"capabilities": controls}, "tv")
        self.assertNotIn("plex servers", out.getvalue())
        for name, expected in (("list-empty", "No eligible retained"), ("select-applied", "profile not saved and playback not confirmed")):
            transport = Transport(self.clock, name)
            words = ["plex", "servers"] if name == "list-empty" else ["plex", "servers", "select", "PMS-1", "--context", CONTEXT]
            code, out, err = self.run_cli(transport, words, json_output=False)
            self.assertEqual((code, err), (0, ""))
            self.assertIn(expected, out)


if __name__ == "__main__":
    unittest.main()
