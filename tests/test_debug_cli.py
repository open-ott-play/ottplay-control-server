"""Rich diagnostics remain bounded, read-only, and separate from effect evidence."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_workbench_cli import Client, Clock, RUNTIME, SECRET, capabilities, load, ott, wb

verify = load("debug_report_verify", "report_verify.py")


def debug_snapshot():
    return {"version": 1, "runtime": RUNTIME, "capturedAt": 1000000, "platform": "capacitor-android",
            "metrics": {"uptimeMs": 20, "loopDelayMs": 1.5, "visible": True, "controlActive": False,
                        "controlPendingRequests": 0, "controlPendingResponses": 1, "controlConsecutiveFailures": 2},
            "media": [{"lane": "main", "generation": 2, "handleId": 3,
                       "metrics": {"readyState": 4, "volume": 0.5, "paused": False, "totalFrames": 10}}],
            "events": [{"sequence": 2, "elapsedMs": 0.25, "code": "started"}], "eventsDropped": 1,
            "native": {"state": "available", "data": {"version": 1, "platform": "android", "appVersion": "1.2.3",
                       "osVersion": "10", "webviewVersion": None, "metrics": {"pssBytes": 120, "foreground": True}}}}


class DebugClient(Client):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities["debug"] = {"version": 1}
        self.debug = debug_snapshot()

    def call_receipt(self, target, action, params):
        result = super().call_receipt(target, action, params)
        if action == "inspect" and params["section"] == "debug" and self.inspect_envelope is None:
            result["data"]["data"] = copy.deepcopy(self.debug)
        return result


class DebugCLITest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = DebugClient(self.clock)

    def run_debug(self, words):
        out = io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(out):
            code = wb.run(ott, self.client, "web", words, 20, True)
        self.assertNotIn(SECRET, out.getvalue())
        return code, json.loads(out.getvalue())

    def test_aliases_are_read_only_and_keep_existing_inspection_capability(self):
        for alias in ("debug", "dbg", "DBG", "DeBuG"):
            self.client.calls.clear()
            code, result = self.run_debug([alias])
            self.assertEqual((code, result["command"]), (0, "debug"))
            self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])
            self.assertEqual(self.client.calls[-1][2], {"version": 1, "runtime": RUNTIME, "section": "debug"})
            self.assertEqual(result["observations"][0]["data"], self.client.debug)
        self.assertEqual(ott.parse_command(["play", "debug"]), ("play", {"query": "debug"}))
        self.assertEqual(ott.capabilities_metadata(self.client.capabilities)["inspect"], capabilities()["inspect"])

    def test_capability_is_strict_and_absence_never_probes(self):
        del self.client.capabilities["debug"]
        code, result = self.run_debug(["debug"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "unsupported"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities"])
        for bad in (None, {}, {"version": True}, {"version": 1.0}, {"version": 2}, {"version": 1, "section": "debug"}):
            with self.subTest(bad=bad), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(capabilities(), debug=bad))

    def test_malformed_debug_is_not_exposed(self):
        mutations = [
            lambda d: d.update(runtime="other-page"), lambda d: d.update(version=True),
            lambda d: d.update(capturedAt=1.0), lambda d: d.update(extra=SECRET),
            lambda d: d["metrics"].update(visible=1), lambda d: d["metrics"].update(uptimeMs=True),
            lambda d: d["metrics"].update(url=SECRET), lambda d: d["metrics"].update(loopDelayMs=float("nan")),
            lambda d: d["media"][0]["metrics"].update(volume=1.1),
            lambda d: d["media"][0].update(generation=True), lambda d: d.update(media=d["media"] * 2),
            lambda d: d.update(events=d["events"] * 2), lambda d: d.update(events=d["events"] * 33),
            lambda d: d["events"][0].update(sequence=0), lambda d: d["events"][0].update(elapsedMs=-1),
            lambda d: d["events"][0].update(code=SECRET), lambda d: d.update(eventsDropped=2 ** 53),
            lambda d: d["native"].update(state="timeout"), lambda d: d["native"].update(data=None),
            lambda d: d["native"]["data"].update(appVersion=SECRET + "/private"),
            lambda d: d["native"]["data"]["metrics"].update(pssBytes=float("inf")),
            lambda d: d.update(padding="x" * 16384),
        ]
        for mutate in mutations:
            self.client.debug = debug_snapshot()
            mutate(self.client.debug)
            code, result = self.run_debug(["debug"])
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])

    def test_unavailable_native_and_missing_metrics_remain_unavailable(self):
        for state in ("unsupported", "unavailable", "timeout", "invalid"):
            self.client.debug.update(native={"state": state, "data": None}, metrics={}, media=[])
            code, result = self.run_debug(["debug"])
            self.assertEqual(code, 0)
            self.assertEqual(result["observations"][0]["data"]["native"], {"state": state, "data": None})
            self.assertEqual(result["observations"][0]["data"]["metrics"], {})

    def test_negative_runtime_binding_and_old_controller_never_retry(self):
        for failure, reason in ((ott.HTTPError(400), "unsupported_controller"),
                                (ott.PlayerUnsupported("unsupported", {"version": 1, "runtime": "other", "section": "debug", "error": "unsupported"}), "invalid_response")):
            self.client.calls.clear()
            self.client.inspect_failure = failure
            code, result = self.run_debug(["debug"])
            self.assertEqual((code, result["observations"][0]["reason"]), (3, reason))
            self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])

    def test_deadline_and_invalid_arguments_never_fall_back_to_play(self):
        self.client.inspect_failure = ott.TransportError("timeout")
        self.client.consume_web_timeout = True
        code, result = self.run_debug(["debug"])
        self.assertEqual(code, 3)
        self.assertEqual(self.clock.value, 1020)
        self.assertEqual(len(self.client.calls), 2)
        self.client.calls.clear()
        for words in (["debug", "enable"], ["debug", "--lane", "native"]):
            code, result = self.run_debug(words)
            self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))
        self.assertEqual(self.client.calls, [])

    def test_bundle_keeps_two_files_and_verifies_debug_without_promoting_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "bundle"
            code, result = self.run_debug(["bundle", "--out", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(result["evaluator"], "workbench-v3")
            self.assertEqual(set(p.name for p in path.iterdir()), {"manifest.json", "result.json"})
            report = json.loads((path / "result.json").read_text())
            self.assertEqual(verify.evaluate_report(report, ott, wb)["verdict"], "observed")
            with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(ott, "Client", side_effect=AssertionError("offline")):
                self.assertEqual(verify.main(["verify", str(path), "-j"], ott, wb), 0)
            self.assertEqual(json.loads(out.getvalue())["evaluation_policy"], "workbench-v3")
            self.assertEqual(report["debug_observations"][0]["data"], self.client.debug)
            self.assertFalse(report["physical_display_verified"])
            report["debug_observations"][0]["data"]["metrics"]["url"] = SECRET
            with self.assertRaises(wb.InvalidData):
                verify.evaluate_report(report, ott, wb)

    def test_bundle_debug_does_not_promote_unknown_snapshot(self):
        self.client.snapshots = [{}]
        self.client.config["native_devices"] = {}
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_debug(["bundle", "--out", str(Path(directory).resolve() / "bundle")])
        self.assertEqual((code, result["verdict"]), (3, "unknown"))
        self.assertEqual(result["debug_observations"][0]["status"], "observed")
        self.assertEqual(verify.evaluate_report(result, ott, wb)["verdict"], "unknown")

    def test_bundle_preserves_native_budget_and_one_total_deadline(self):
        self.client.failure = ott.TransportError("timeout")
        self.client.consume_web_timeout = True
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_debug(["bundle", "--out", str(Path(directory).resolve() / "bundle")])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][1]["status"], "observed")
        self.assertEqual(result["debug_observations"][0]["status"], "unknown")
        self.assertEqual(self.clock.value, 1020)
        self.assertEqual(self.client.timeout, 20)
        verify.evaluate_report(result, ott, wb)

    def test_bundle_old_player_records_unsupported_debug_without_inspect_probe(self):
        del self.client.capabilities["debug"]
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_debug(["bundle", "--out", str(Path(directory).resolve() / "bundle")])
        self.assertEqual(code, 0)
        self.assertEqual(result["debug_observations"][0]["reason"], "unsupported")
        self.assertEqual([call[2]["section"] for call in self.client.calls if call[1] == "inspect"], ["snapshot"])
        verify.evaluate_report(result, ott, wb)

    def test_new_evaluator_rejects_unvalidated_or_reused_debug_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.run_debug(["bundle", "--out", str(Path(directory).resolve() / "bundle")])
        for mutate in (lambda r: r.update(evaluator="workbench-v2"),
                       lambda r: r["debug_observations"][0].update(runtime="other-page"),
                       lambda r: r["debug_observations"][0]["requests"][0].update(request_id=r["observations"][0]["requests"][0]["request_id"]),
                       lambda r: r.update(debug_observations=[])):
            invalid = copy.deepcopy(result)
            mutate(invalid)
            with self.assertRaises(verify.InvalidReport):
                verify.evaluate_report(invalid, ott, wb)

    def test_native_only_bundle_does_not_probe_web(self):
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_debug(["bundle", "--lane", "native", "--out", str(Path(directory).resolve() / "bundle")])
        self.assertEqual(code, 0)
        self.assertEqual(result["debug_observations"], [])
        self.assertTrue(all(call[0] == "native" for call in self.client.calls))
        verify.evaluate_report(result, ott, wb)

    def test_main_dispatches_debug_aliases(self):
        for alias in ("debug", "dbg", "DBG"):
            out = io.StringIO()
            with mock.patch.object(ott, "Client", return_value=self.client), mock.patch.object(ott, "read_json", return_value={}), contextlib.redirect_stdout(out):
                code = ott.main(["-j", "test-player", alias])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["command"], "debug")


def server_snapshot():
    return {"version": 1, "sampledAt": 1000000, "consistent": False,
            "process": dict.fromkeys("uptimeMs goroutines heapAllocBytes heapSysBytes".split(), 1),
            "control": dict.fromkeys("devices queues pending queueBytes resultEntries resultBytes commandTtlMs maxPendingPerDevice".split(), 1),
            "diagnostics": dict(configured=True, **dict.fromkeys("runtimes sessions repairs eventBytes reservedStops controlSlotsUsed controlSlotsCapacity eventSlotsUsed eventSlotsCapacity".split(), 1))}


class ServerDebugCLITest(unittest.TestCase):
    def test_server_debug_uses_one_admin_read_without_resolving_player(self):
        client = mock.Mock()
        client.api.return_value = (200, server_snapshot())
        with mock.patch.object(ott, "Client", return_value=client), mock.patch.object(ott, "read_json", return_value={}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ott.main(["server", "debug", "-j"]), 0)
        self.assertEqual(json.loads(out.getvalue()), server_snapshot())
        client.api.assert_called_once_with("/api/debug")
        client.device.assert_not_called()
        client.call_receipt.assert_not_called()

    def test_server_metadata_is_strict(self):
        for mutation in (lambda d: d.update(version=True), lambda d: d.update(consistent=True),
                         lambda d: d["process"].update(goroutines=True), lambda d: d["control"].update(devices=-1),
                         lambda d: d["diagnostics"].update(configured=1), lambda d: d.update(private=SECRET),
                         lambda d: d["diagnostics"].update(controlSlotsUsed=2)):
            value = server_snapshot()
            mutation(value)
            with self.assertRaises(ott.Error):
                ott.server_debug_metadata(value)

    def test_old_controller_is_reported_without_retry(self):
        client = mock.Mock()
        client.api.side_effect = ott.HTTPError(404)
        with self.assertRaisesRegex(ott.Error, "does not support"):
            ott.management(client, "unused", ["server", "debug"])
        client.api.assert_called_once_with("/api/debug")


if __name__ == "__main__":
    unittest.main()
