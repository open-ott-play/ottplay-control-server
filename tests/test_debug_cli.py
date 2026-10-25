"""Rich diagnostics remain bounded, read-only, and separate from effect evidence."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_workbench_cli import Client, Clock, RUNTIME, SECRET, capabilities, load, ott, wb

verify = load("debug_report_verify", "report_verify.py")
ROOT = Path(__file__).resolve().parents[1]
V3 = json.loads((ROOT / "contracts/runtime-debug-v3.json").read_text())


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
            result["data"]["version"] = params["version"]
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

    def test_debug_help_advertises_only_the_supported_web_lane(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stopped:
            wb.run(ott, self.client, "web", ["debug", "--help"], 20)
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--lane {web}", out.getvalue())
        self.assertNotIn("--lane {auto", out.getvalue())
        self.assertNotIn("--lane {web,native}", out.getvalue())
        self.assertEqual(self.client.calls, [])
        self.assertEqual(wb.arguments(["debug"], False).lane, "web")
        self.assertEqual(wb.arguments(["debug", "--lane", "web"], False).lane, "web")

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

    def test_engine_v2_is_explicit_and_keeps_default_v1(self):
        self.client.capabilities["debug_versions"] = [1, 2]
        code, result = self.run_debug(["debug"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["data"]["version"], 1)
        self.assertNotIn("engine", result["observations"][0]["data"]["media"][0])
        self.client.debug["version"] = 2
        for engine in (None, "hls.js", "shaka", "html5"):
            self.client.debug["media"][0]["engine"] = engine
            code, result = self.run_debug(["debug", "--engine"])
            self.assertEqual(code, 0)
            self.assertEqual(self.client.calls[-1][2]["version"], 2)
            self.assertEqual(result["observations"][0]["data"]["media"][0]["engine"], engine)

    def test_engine_v2_requires_exact_capability_and_never_downgrades(self):
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "unsupported"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities"])
        for versions in (None, [], [1], [2], [1, 2, 2], [True, 2], [1, 2.0], "1,2"):
            with self.subTest(versions=versions), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, debug_versions=versions))
        self.client.capabilities["debug_versions"] = [1, 2]
        self.client.calls.clear()
        self.client.inspect_failure = ott.HTTPError(400)
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "unsupported_controller"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])

    def test_engine_v2_rejects_missing_invalid_or_downgraded_data(self):
        self.client.capabilities["debug_versions"] = [1, 2]
        for mutate in (lambda d: d["media"][0].pop("engine"),
                       lambda d: d["media"][0].update(engine="auto"),
                       lambda d: d["media"][0].update(engine=SECRET),
                       lambda d: d["media"][0].update(engine={}),
                       lambda d: d.update(version=1)):
            self.client.debug = debug_snapshot()
            self.client.debug["version"] = 2
            self.client.debug["media"][0]["engine"] = "hls.js"
            mutate(self.client.debug)
            code, result = self.run_debug(["debug", "--engine"])
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])

    def enable_attempts(self):
        self.client.capabilities.update(copy.deepcopy(V3["capabilities"]))
        self.client.debug = copy.deepcopy(V3["response"]["data"])

    def test_actual_old_parser_ignores_new_marker(self):
        source = (ROOT / V3["legacy_parser"]["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), V3["legacy_parser"]["sha256"])
        namespace = dict(vars(ott))
        exec(compile(source, V3["legacy_parser"]["path"], "exec"), namespace)
        self.enable_attempts()
        legacy = namespace["capabilities_metadata"](self.client.capabilities)
        self.assertEqual(legacy["debug_versions"], [1, 2])
        self.assertNotIn("debug_attempts", legacy)
        modern = ott.capabilities_metadata(self.client.capabilities)
        self.assertEqual(modern.pop("debug_attempts"), {"version": 3})
        self.assertEqual(modern, legacy)
        with self.assertRaises(ott.Error):
            namespace["capabilities_metadata"](dict(self.client.capabilities, debug_versions=[1, 2, 3]))

    def test_engine_negotiates_v3_and_preserves_null_or_real_zero(self):
        self.enable_attempts()
        for empty in (False, True):
            if empty:
                self.client.debug["media"][0].update(attempt=None, lastAttempt=None)
            code, result = self.run_debug(["debug", "--engine"])
            self.assertEqual(code, 0)
            self.assertEqual(self.client.calls[-1][2]["version"], 3)
            self.assertEqual(result["observations"][0]["data"], self.client.debug)
            self.assertEqual(result["observations"][0]["data"]["media"][0]["metrics"]["totalFrames"], 0)
        self.client.debug = debug_snapshot()
        code, result = self.run_debug(["debug"])
        self.assertEqual(code, 0)
        self.assertEqual(self.client.calls[-1][2]["version"], 1)

    def test_attempt_capability_is_strict_and_requires_legacy_base(self):
        self.enable_attempts()
        for value in (None, {}, {"version": True}, {"version": 3.0}, {"version": 2}, {"version": 4}, {"version": 3, "url": SECRET}):
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, debug_attempts=value))
        for field in ("debug", "debug_versions"):
            caps = copy.deepcopy(self.client.capabilities)
            del caps[field]
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(caps)

    def test_v3_attempts_reject_malformed_private_or_downgraded_data(self):
        mutations = [lambda d: d.update(version=2), lambda d: d["media"][0].pop("attempt"),
                     lambda d: d["media"][0].pop("lastAttempt"),
                     lambda d: d["media"][0]["attempt"].update(url=SECRET),
                     lambda d: d["media"][0]["attempt"].pop("attachment"),
                     lambda d: d["media"][0]["lastAttempt"].update(reason=SECRET),
                     lambda d: d["media"][0]["lastAttempt"].update(handleId=None)]
        for field in V3["attempt_fields"]:
            for bad in (True, -1, 0.5, float("inf"), "1", None, 2 ** 53):
                mutations.append(lambda d, field=field, bad=bad: d["media"][0]["attempt"].update({field: bad}))
        for mutate in mutations:
            self.enable_attempts()
            mutate(self.client.debug)
            code, result = self.run_debug(["debug", "--engine"])
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])

    def test_v3_rejection_never_retries_or_silently_downgrades(self):
        self.enable_attempts()
        self.client.inspect_failure = ott.HTTPError(400)
        code, result = self.run_debug(["debug", "--engine"])
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "unsupported_controller"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])
        self.assertEqual(self.client.calls[-1][2]["version"], 3)

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
        for words in (["debug", "enable"], ["debug", "--lane", "native"], ["debug", "--lane", "auto"]):
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
