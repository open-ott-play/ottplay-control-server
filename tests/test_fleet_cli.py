"""Finite fleet observation and evidence semantics."""
from concurrent.futures import Future
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load("fleet_fixtures", ROOT / "tests/test_workbench_cli.py")
fleet = load("fleet_tests", ROOT / "cli/fleet.py")
ott, wb = fixtures.ott, fixtures.wb
PRIVATE = "https://secret.example/private-token"


class ImmediatePool:
    def __init__(self, **kwargs):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, **kwargs):
        pass


class Client:
    device = ott.Client.device

    def __init__(self, clock):
        self.clock, self.timeout = clock, 45
        self.config = {"players": {"a": "first", "alias": "first", "b": "second"}}
        self.credentials = {"devices": [{"id": "first"}, {"id": "second"}]}
        self.calls, self.api_calls, self.error = [], [], {}
        self.api_error, self.standby, self.background = None, None, None
        self.modify = lambda data: None

    def api(self, path, payload=None, timeout=None):
        self.api_calls.append((path, payload, timeout))
        if self.api_error:
            raise self.api_error
        return 200, {"devices": [{"id": row["id"], "pending": 0, "last_seen": None} for row in self.credentials["devices"]]}

    def call_receipt(self, device, action, params):
        self.calls.append((device, action, copy.deepcopy(params), self.timeout))
        if device in self.error:
            raise self.error[device]
        if action == "capabilities":
            data = fixtures.capabilities()
            if self.standby is not None:
                data["player"]["standby"] = self.standby
            if self.background is not None:
                data["player"]["background"] = self.background
        elif action == "inspect":
            value = fixtures.snapshot(10 + self.clock.value - 1000, int(self.clock.value * 1000))
            self.modify(value)
            data = {"version": 1, "runtime": fixtures.RUNTIME, "section": "snapshot", "data": value}
        else:
            raise AssertionError("Unexpected RPC")
        return {"request_id": format(len(self.calls), "032x"), "status": "ok", "data": data}


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.clock = fixtures.Clock()
        self.client = Client(self.clock)

    def run_watch(self, selector="a", extra=(), duration=10):
        output = io.StringIO()
        with mock.patch.object(fleet, "time", self.clock), mock.patch.object(wb, "time", self.clock), \
                mock.patch.object(fleet, "workbench_module", return_value=wb), \
                mock.patch.object(fleet, "ThreadPoolExecutor", ImmediatePool), contextlib.redirect_stdout(output):
            code = fleet.run(ott, self.client, selector, ["--duration", str(duration), "--interval", "5", *extra], 45, True)
        return code, json.loads(output.getvalue())

    def test_deduplicates_aliases_and_reports_progress(self):
        code, result = self.run_watch("a,alias,b")
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"], {"targets": 2, "samples": 4, "observed": 4, "unknown": 0,
                                             "progress_pass": 2, "progress_fail": 0, "progress_unknown": 2})
        self.assertEqual(result["planned_samples"], 4)
        self.assertEqual(len(result["controller"]), 2)
        self.assertEqual(self.client.timeout, 45)
        self.assertEqual(len(self.client.calls), 8)
        self.assertTrue(all(row[1] in ("capabilities", "inspect") for row in self.client.calls))
        self.assertTrue(all(row[0] == "/api/devices" and row[1] is None for row in self.client.api_calls))
        self.assertEqual(result["players"][0]["aliases"], ["a", "alias"])
        self.assertIsNone(result["players"][0]["samples"][0]["inventory"]["last_seen"])
        self.assertFalse(result["physical_display_verified"])

    def test_frozen_fails_but_paused_hidden_and_standby_do_not(self):
        for mode, expected, reason in (("frozen", 2, "media_did_not_progress"),
                                       ("paused", 0, "media_not_continuously_playing"),
                                       ("hidden", 0, "document_hidden"),
                                       ("visibility", 0, "visibility_unavailable"),
                                       ("css", 0, "video_not_visible"),
                                       ("standby", 0, "standby_reported")):
            with self.subTest(mode=mode):
                self.setUp()
                def change(data):
                    lane = data["media"]["lanes"][0]
                    lane["position"] = 10
                    if mode == "paused":
                        lane["phase"] = data["media"]["phase"] = "paused"
                        lane["video"]["paused"] = True
                    if mode in ("hidden", "visibility"):
                        data["ui"]["documentVisibility"] = "hidden" if mode == "hidden" else "unknown"
                    if mode == "css":
                        lane["video"]["cssVisible"] = False
                self.client.modify = change
                self.client.standby = mode == "standby"
                self.client.background = {"state": "suspended", "pending": 0}
                code, result = self.run_watch()
                sample = result["players"][0]["samples"][-1]
                self.assertEqual(code, expected)
                self.assertEqual(sample["progress"]["reason"], reason)
                self.assertEqual(sample["standby"], mode == "standby")
                self.assertEqual(sample["activity_source"], "capabilities_before_snapshot_not_atomic")

    def test_uncertain_clock_or_identity_never_proves_a_stall(self):
        for mode in ("generation", "backward", "seek"):
            with self.subTest(mode=mode):
                self.setUp()
                def change(data):
                    if self.clock.value > 1000:
                        if mode == "generation":
                            data["media"]["generation"] += 1
                        elif mode == "backward":
                            data["capturedAt"] -= 10000
                        else:
                            data["media"]["lanes"][0]["position"] += 500
                self.client.modify = change
                code, result = self.run_watch()
                self.assertEqual(code, 0)
                self.assertEqual(result["players"][0]["samples"][-1]["progress"]["verdict"], "unknown")

    def test_unresponsive_peer_preserves_other_player_and_exact_cause(self):
        self.client.error["first"] = ott.RequestError("request_pending_timeout", "receipt", PRIVATE, "a" * 32)
        code, result = self.run_watch("a,b")
        self.assertEqual(code, 3)
        first, second = result["players"]
        self.assertEqual(first["samples"][0]["reason"], "request_pending_timeout")
        self.assertEqual(first["samples"][0]["requests"][0]["request_id"], "a" * 32)
        self.assertEqual(second["summary"]["observed"], 2)
        self.assertEqual(second["summary"]["progress_pass"], 1)
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.assertNotIn("offline", json.dumps(result))

    def test_controller_failure_is_distinct_from_responsive_player(self):
        self.client.api_error = ott.TransportError(PRIVATE)
        code, result = self.run_watch()
        self.assertEqual(code, 3)
        self.assertEqual(result["controller"][0]["reason"], "controller_unavailable")
        self.assertEqual(result["players"][0]["summary"]["observed"], 2)
        self.assertNotIn("inventory", result["players"][0]["samples"][0])
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_limits_and_bad_target_fail_before_any_request(self):
        for selector, extra in (("a,,b", ()), ("missing", ()), ("a", ("--duration", "nan")),
                                ("a", ("--duration", "300", "--interval", "1")),
                                ("a", ("--workers", "5")), ("a", ("--request-timeout", "0"))):
            with self.subTest(selector=selector, extra=extra):
                code, result = self.run_watch(selector, extra)
                self.assertEqual(code, 1)
                self.assertEqual(result["status"], "error")
                self.assertEqual(self.client.calls, [])
                self.assertEqual(self.client.api_calls, [])

    def test_read_only_boundary(self):
        stopped = threading.Event()
        reader = fleet.ReadOnlyClient(ott, self.client, "first", stopped)
        for action, params in (("play", {}), ("restart", {}), ("perf", {}),
                               ("inspect", {"version": 1, "runtime": fixtures.RUNTIME, "section": "operation"})):
            with self.subTest(action=action), self.assertRaises(ott.Error):
                reader.call_receipt("first", action, params)
        with self.assertRaises(ott.Error):
            reader.call_receipt("second", "capabilities", {})
        stopped.set()
        with self.assertRaises(ott.Error):
            reader.call_receipt("first", "capabilities", {})
        self.assertEqual(self.client.calls, [])

    def test_inventory_validation_and_projection(self):
        row = {"id": "first", "last_seen": "2026-10-25T10:00:00Z", "pending": 0, "secret": PRIVATE}
        result = fleet.inventory_metadata({"devices": [row, row], "secret": PRIVATE}, self.client.config)
        self.assertEqual(len(result["devices"]), 1)
        self.assertEqual(result["stale_aliases"], [{"alias": "b", "id": "second"}])
        self.assertNotIn(PRIVATE, json.dumps(result))
        for bad in (dict(row, pending=True), dict(row, last_seen="yesterday"), dict(row, last_seen="2026-10-25")):
            with self.assertRaises(fleet.Failure):
                fleet.inventory_metadata({"devices": [bad]}, self.client.config)
        with self.assertRaises(fleet.Failure):
            fleet.inventory_metadata({"devices": [row, dict(row, pending=1)]}, self.client.config)

    def test_devices_json_and_main_watch_routing(self):
        output = io.StringIO()
        with mock.patch.object(ott, "fleet_module", return_value=fleet), contextlib.redirect_stdout(output):
            ott.management(self.client, "unused", ["devices"], True)
        self.assertEqual(len(json.loads(output.getvalue())["devices"]), 2)
        with mock.patch.object(ott, "read_json", return_value=self.client.config), \
                mock.patch.object(ott, "Client", return_value=self.client), \
                mock.patch.object(ott, "fleet_module", return_value=fleet), mock.patch.object(fleet, "run", return_value=0) as run:
            self.assertEqual(ott.main(["-j", "a,b", "watch", "--duration", "10"]), 0)
        self.assertEqual(run.call_args.args[2:5], ("a,b", ["--duration", "10"], 45))

    def test_capabilities_activity_projection_is_optional_and_strict(self):
        caps = fixtures.capabilities()
        self.assertNotIn("standby", ott.capabilities_metadata(caps)["player"])
        caps["player"].update(standby=True, background={"state": "suspended", "pending": None}, secret=PRIVATE)
        result = ott.capabilities_metadata(caps)
        self.assertTrue(result["player"]["standby"])
        self.assertIsNone(result["player"]["background"]["pending"])
        self.assertNotIn(PRIVATE, json.dumps(result))
        for field, bad in (("standby", 1), ("background", {"state": "running", "pending": True})):
            invalid = copy.deepcopy(caps)
            invalid["player"][field] = bad
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(invalid)

    def test_interrupt_retains_finished_and_marks_pending_peers(self):
        class PendingPool(ImmediatePool):
            def submit(self, function, *args):
                return Future() if args[3] == "second" else super().submit(function, *args)
        def interrupted(futures, **kwargs):
            yield next(iter(futures))
            raise KeyboardInterrupt()
        output = io.StringIO()
        with mock.patch.object(fleet, "time", self.clock), mock.patch.object(wb, "time", self.clock), \
                mock.patch.object(fleet, "ThreadPoolExecutor", PendingPool), \
                mock.patch.object(fleet, "as_completed", interrupted), contextlib.redirect_stdout(output):
            result = fleet.collect(ott, self.client, [("a", "first"), ("b", "second")],
                                   fleet.arguments(["--json"]), wb, 45)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["summary"]["samples"], 2)
        self.assertEqual(result["summary"]["observed"], 1)
        self.assertEqual(result["players"][1]["samples"][0]["reason"], "interrupted")

    def test_timed_out_round_keeps_every_target_visible(self):
        class PendingPool(ImmediatePool):
            def submit(self, *args):
                return Future()
        with mock.patch.object(fleet, "ThreadPoolExecutor", PendingPool), \
                mock.patch.object(fleet, "as_completed", side_effect=fleet.TimeoutError()):
            result = fleet.collect(ott, self.client, [("a", "first"), ("b", "second")],
                                   fleet.arguments(["--json"]), wb, 45)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"]["unknown"], 2)
        self.assertTrue(all(player["samples"][0]["reason"] == "deadline_exceeded" for player in result["players"]))

    def test_slow_peer_does_not_take_the_other_peers_budget(self):
        original = Client.call_receipt
        def slow(client, device, action, params):
            if device == "first":
                client.clock.sleep(client.timeout)
                raise ott.RequestError("request_pending_timeout", "receipt", PRIVATE)
            return original(client, device, action, params)
        with mock.patch.object(Client, "call_receipt", slow):
            code, result = self.run_watch("a,b", ["--workers", "1"])
        self.assertEqual(code, 3)
        self.assertEqual(result["players"][1]["summary"]["observed"], 2)
        self.assertLessEqual(result["elapsed_seconds"], 10)
        self.assertEqual(self.client.timeout, 45)

    def test_hidden_to_visible_pair_remains_unknown_and_keeps_build_identity(self):
        self.client.modify = lambda data: data["ui"].update(documentVisibility="hidden" if self.clock.value == 1000 else "visible")
        code, result = self.run_watch()
        sample = result["players"][0]["samples"][-1]
        self.assertEqual(code, 0)
        self.assertEqual(sample["progress"]["reason"], "document_hidden")
        self.assertEqual(sample["build"], fixtures.snapshot()["build"])
        self.assertEqual(sample["player"]["platform"], fixtures.capabilities()["player"]["platform"])

    def test_recovery_does_not_bridge_missing_observations(self):
        original = Client.call_receipt
        def gap(client, device, action, params):
            if client.clock.value == 1005:
                raise ott.RequestError("request_pending_timeout", "receipt", PRIVATE)
            return original(client, device, action, params)
        with mock.patch.object(Client, "call_receipt", gap):
            code, result = self.run_watch(duration=15)
        samples = result["players"][0]["samples"]
        self.assertEqual(code, 3)
        self.assertEqual([row["status"] for row in samples], ["observed", "unknown", "observed"])
        self.assertEqual(samples[-1]["progress"]["verdict"], "unknown")

    def test_finite_real_worker_collection_keeps_timeouts_private(self):
        # Exercise the real executor too: the fixture transport has no I/O.
        args = fleet.arguments(["--duration", "2", "--interval", "1", "--json"])
        result = fleet.collect(ott, self.client, [("a", "first"), ("b", "second")], args, wb, 3)
        self.assertEqual(result["summary"]["observed"], 4)
        self.assertEqual(self.client.timeout, 45)
        self.assertTrue(all(0 < call[3] <= 3 for call in self.client.calls))
