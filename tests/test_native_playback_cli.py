"""Native playback diagnostics preserve event units, unknowns and runtime binding."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from test_debug_cli import DebugClient
from test_workbench_cli import Client, Clock, RUNTIME, SECRET, capabilities, ott, wb


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "contracts" / "native-playback-v1.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
NATIVE_RUNTIME = FIXTURE["runtime"]
PRIVATE_URL = "https://private.invalid/stream?token=" + SECRET


def sample(name="displayed_intervals"):
    return copy.deepcopy(next(row["expected_data"] for row in FIXTURE["cases"] if row["name"] == name))


def envelope(data=None, runtime=NATIVE_RUNTIME):
    return {"version": 1, "runtime": runtime, "section": "native_playback",
            "data": sample() if data is None else copy.deepcopy(data)}


class NativeClient(Client):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities["player"]["runtime"] = NATIVE_RUNTIME
        self.capabilities["native_playback_diagnostics"] = {"version": 1}
        self.sample = sample()
        self.barrier = None
        self.children = []
        self.on_receipt = None

    def call_receipt(self, target, action, params):
        if action == "inspect" and self.barrier is not None:
            self.barrier.wait(timeout=5)
        result = super().call_receipt(target, action, params)
        if action == "inspect" and params["section"] == "native_playback" and self.inspect_envelope is None:
            result["data"] = envelope(self.sample, self.capabilities["player"]["runtime"])
        return result

    def for_group(self, cancelled):
        child = NativeClient(Clock())
        child.group_cancelled = cancelled
        child.barrier = self.barrier
        self.children.append(child)
        return child


class NativePlaybackCLITest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = NativeClient(self.clock)

    def execute(self, words=None, json_output=True, client=None, timeout=20):
        output = io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(output):
            code = wb.run(ott, client or self.client, "web", words or ["debug", "--native-playback"], timeout, json_output)
        text = output.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PRIVATE_URL, text)
        return code, json.loads(text) if json_output else text

    def test_shared_synthetic_contract_is_pinned_and_preserves_all_eight_samples(self):
        self.assertEqual(hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
                         "7e19387eb2e96bf80457a7e9457b272d26c13caf771d457eaa42926ecb68dbbe")
        self.assertTrue(FIXTURE["synthetic"])
        self.assertEqual(len(FIXTURE["cases"]), 8)
        for row in FIXTURE["cases"]:
            with self.subTest(name=row["name"]):
                self.client.sample = copy.deepcopy(row["expected_data"])
                self.client.calls.clear()
                code, result = self.execute()
                self.assertEqual((code, result["verdict"]), (0, "observed"))
                self.assertTrue(result["read_only"])
                self.assertFalse(result["physical_display_verified"])
                self.assertEqual(result["evaluator"], "workbench-v2")
                observed = result["observations"][0]
                self.assertEqual(observed["data"], row["expected_data"])
                self.assertEqual((observed["lane"], observed["runtime"]), ("web", NATIVE_RUNTIME))
                self.assertNotIn("runtime", observed["data"])
                self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])
                self.assertEqual(self.client.calls[-1][2],
                                 {"version": 1, "runtime": NATIVE_RUNTIME, "section": "native_playback"})

    def test_capability_alone_enables_the_new_section_without_changing_legacy_arrays(self):
        del self.client.capabilities["inspect"]
        code, result = self.execute()
        self.assertEqual(code, 0)
        self.assertNotIn("debug", result["observations"][0]["capabilities"])
        caps = capabilities()
        caps.update(native_playback_diagnostics={"version": 1}, debug={"version": 1},
                    debug_versions=[1, 2], debug_attempts={"version": 3})
        parsed = ott.capabilities_metadata(caps)
        self.assertEqual(parsed["inspect"], capabilities()["inspect"])
        self.assertEqual(parsed["debug_versions"], [1, 2])
        self.assertEqual(parsed["debug_attempts"], {"version": 3})

    def test_absent_unknown_or_malformed_marker_never_sends_inspect(self):
        for marker in ("absent", None, {}, True, 1, {"version": True}, {"version": 1.0},
                       {"version": 0}, {"version": 2}, {"version": 1, "url": PRIVATE_URL}):
            with self.subTest(marker=marker):
                self.client = NativeClient(self.clock)
                self.client.capabilities["player"]["runtime"] = RUNTIME
                if marker == "absent":
                    self.client.capabilities.pop("native_playback_diagnostics")
                else:
                    self.client.capabilities["native_playback_diagnostics"] = marker
                code, result = self.execute()
                self.assertEqual((code, result["verdict"]), (3, "unknown"))
                self.assertNotIn("data", result["observations"][0])
                self.assertEqual([call[1] for call in self.client.calls], ["capabilities"])

    def test_runtime_extension_requires_exact_marker_and_preserves_platform_rules(self):
        for runtime in ("native_1", "Native-A", "native.1", "n" * 96):
            with self.subTest(runtime=runtime):
                self.client.capabilities["player"]["runtime"] = runtime
                code, result = self.execute()
                self.assertEqual(code, 0)
                self.assertEqual(result["observations"][0]["runtime"], runtime)
                self.assertEqual(self.client.calls[-1][2]["runtime"], runtime)
                for marker in (None, {"version": 2}, {"version": True}, {"version": 1.0}):
                    bad = copy.deepcopy(self.client.capabilities)
                    if marker is None:
                        bad.pop("native_playback_diagnostics")
                    else:
                        bad["native_playback_diagnostics"] = marker
                    with self.assertRaises(ott.Error):
                        ott.capabilities_metadata(bad)
        for runtime in ("", "n" * 97, PRIVATE_URL, "device name", "native/1", "native:1", "нативный"):
            bad = copy.deepcopy(self.client.capabilities)
            bad["player"]["runtime"] = runtime
            with self.subTest(runtime=runtime), self.assertRaises(ott.Error):
                ott.capabilities_metadata(bad)
            with self.assertRaises(wb.InvalidData):
                wb.native_playback_metadata(envelope(runtime=runtime), runtime)
        for platform in ("webos", "webos-native", "native", "native-webos", "native_webos"):
            good = copy.deepcopy(self.client.capabilities)
            good["player"]["runtime"] = NATIVE_RUNTIME
            good["player"]["platform"] = platform
            self.assertEqual(ott.capabilities_metadata(good)["player"]["platform"], platform)
        for platform in ("WEBOS", PRIVATE_URL, "p" * 33):
            bad = copy.deepcopy(good)
            bad["player"]["platform"] = platform
            with self.assertRaises(ott.Error):
                ott.capabilities_metadata(bad)

    def test_incompatible_flags_fail_before_any_rpc_and_native_lane_remains_separate(self):
        for options in (["--engine"], ["--provider"], ["--lane", "native"], ["--lane", "auto"], ["--out", "/unused"]):
            with self.subTest(options=options):
                code, result = self.execute(["debug", "--native-playback", *options])
                self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))
                self.assertEqual(self.client.calls, [])
        code, result = self.execute(["doctor", "--lane", "native"])
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["lane"], "native")
        self.assertEqual([call[1:3] for call in self.client.calls], [("maintenance", {"operation": "health"})])

    def test_default_debug_never_fetches_native_playback_even_when_advertised(self):
        client = DebugClient(self.clock)
        client.capabilities["native_playback_diagnostics"] = {"version": 1}
        code, result = self.execute(["debug"], client=client)
        self.assertEqual(code, 0)
        self.assertEqual(result["observations"][0]["data"], client.debug)
        self.assertEqual([call[1] for call in client.calls], ["capabilities", "inspect"])
        self.assertEqual(client.calls[-1][2]["section"], "debug")

    def test_alias_and_local_help_use_web_inspect_without_native_agent_binding(self):
        self.client.config["native_devices"] = {}
        for command in ("debug", "dbg", "DBG"):
            code, result = self.execute([command, "--native-playback"])
            self.assertEqual((code, result["command"]), (0, "debug"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            wb.arguments(["debug", "--help"], False)
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--native-playback", output.getvalue())
        self.assertIn("--lane {web}", output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(self.client.capabilities)}, "l")
        self.assertIn("ott l debug --native-playback", output.getvalue())

    def test_plain_text_distinguishes_event_reports_frames_and_missing_measurements(self):
        code, text = self.execute(json_output=False)
        self.assertEqual(code, 0)
        for expected in ("Phase: playing; native load epoch: 7", "Video submitted: 4 units",
                         "Audio submitted: 9 units", "Video position callbacks: 3 events",
                         "Displayed callbacks: 3 reports", "Displayed interval sum: 17 frames",
                         "Video submission age: 120 ms", "Displayed callback age: 20 ms",
                         "Video demuxed: NOT OBSERVED", "Audio presented: NOT OBSERVED",
                         "Pending command: none reported", "Current error: none reported"):
            self.assertIn(expected, text)
        self.assertNotIn("\"counters\"", text)
        self.assertIn("physical display and audio remain unverified", text)
        self.client.sample = sample("displayed_zero")
        _, text = self.execute(json_output=False)
        self.assertIn("Displayed interval sum: 0 frames", text)
        self.client.sample["counters"]["video_displayed_frames"] = None
        _, text = self.execute(json_output=False)
        self.assertIn("Displayed callbacks: 1 reports", text)
        self.assertIn("Displayed interval sum: NOT OBSERVED", text)
        self.assertIn("Displayed callback age: 40 ms", text)
        self.client.sample = sample("pending")
        _, text = self.execute(json_output=False)
        self.assertIn("Pending command: resolving_source", text)
        self.assertIn("Pending command age: 250 ms", text)
        self.client.sample = sample("load_timeout")
        _, text = self.execute(json_output=False)
        self.assertIn("Current error: load_timeout", text)

    def test_fixed_schema_rejects_unknown_fields_private_strings_and_missing_fields(self):
        mutations = [lambda d: d.update(url=PRIVATE_URL), lambda d: d.update(runtime=NATIVE_RUNTIME),
                     lambda d: d.update(scope=PRIVATE_URL), lambda d: d.update(phase=PRIVATE_URL),
                     lambda d: d.update(version=True), lambda d: d.update(version=1.0),
                     lambda d: d.update(version=2), lambda d: d.pop("pending_command"),
                     lambda d: d.update(counters=None), lambda d: d["counters"].update(url=PRIVATE_URL),
                     lambda d: d["ages_ms"].update(url=PRIVATE_URL), lambda d: d["counters"].pop("video_submitted_units"),
                     lambda d: d["ages_ms"].pop("audio_submission"),
                     lambda d: d.update(pending_command={"stage": "admitted", "age_ms": 0, "url": PRIVATE_URL}),
                     lambda d: d.update(pending_command={"stage": "admitted"}),
                     lambda d: d.update(pending_command={"stage": PRIVATE_URL, "age_ms": 0}),
                     lambda d: d.update(pending_command={"stage": "waiting_playback", "age_ms": 0}),
                     lambda d: d.update(last_error={"code": "load_timeout", "message": PRIVATE_URL}),
                     lambda d: d.update(phase="error", last_error={"code": PRIVATE_URL})]
        for mutate in mutations:
            self.client.sample = sample()
            mutate(self.client.sample)
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])

    def test_numeric_fields_reject_boolean_fractional_negative_nonfinite_and_overflow(self):
        paths = [("native_epoch",)]
        paths += [("counters", key) for key in wb.NATIVE_PLAYBACK_COUNTERS if key not in wb.NATIVE_PLAYBACK_UNMEASURED]
        paths += [("ages_ms", key) for key in wb.NATIVE_PLAYBACK_AGES]
        paths += [("pending_command", "age_ms")]
        for path in paths:
            for value in (True, False, -1, 0.5, 1.0, "1", float("nan"), float("inf"), 2 ** 53):
                with self.subTest(path=path, value=value):
                    self.client.sample = sample()
                    self.client.sample["pending_command"] = {"stage": "admitted", "age_ms": None}
                    target = self.client.sample if len(path) == 1 else self.client.sample[path[0]]
                    target[path[-1]] = value
                    code, result = self.execute()
                    self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
                    self.assertNotIn("data", result["observations"][0])

    def test_epoch_and_safe_integer_boundaries_and_strict_unmeasured_counters(self):
        for epoch in (1, 4294967295):
            data = sample()
            data["native_epoch"] = epoch
            for key in wb.NATIVE_PLAYBACK_COUNTERS:
                if key not in wb.NATIVE_PLAYBACK_UNMEASURED:
                    data["counters"][key] = 9007199254740991
            data["ages_ms"] = dict.fromkeys(wb.NATIVE_PLAYBACK_AGES, 9007199254740991)
            data["pending_command"] = {"stage": "waiting_catalog", "age_ms": 9007199254740991}
            self.assertEqual(wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME), data)
        for epoch in (0, 4294967296):
            data["native_epoch"] = epoch
            with self.assertRaises(wb.InvalidData):
                wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME)
        for key in wb.NATIVE_PLAYBACK_UNMEASURED:
            for value in (0, 1):
                data = sample()
                data["counters"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(wb.InvalidData):
                    wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME)

    def test_null_epoch_cannot_retain_old_event_evidence_or_out_of_phase_error(self):
        for section in ("counters", "ages_ms"):
            for key in sample("idle")[section]:
                data = sample("idle")
                data[section][key] = 0
                with self.subTest(section=section, key=key), self.assertRaises(wb.InvalidData):
                    wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME)
        for phase in wb.NATIVE_PLAYBACK_PHASES:
            data = sample("load_timeout")
            data["phase"] = phase
            if phase == "error":
                self.assertEqual(wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME), data)
            else:
                with self.subTest(phase=phase), self.assertRaises(wb.InvalidData):
                    wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME)
        for stage in ("admitted", "waiting_catalog", "resolving_source"):
            data = sample("pending")
            data["pending_command"] = {"stage": stage, "age_ms": None}
            self.assertEqual(wb.native_playback_metadata(envelope(data), NATIVE_RUNTIME), data)

    def test_all_four_bound_error_envelopes_remain_unknown_without_retries(self):
        for row in FIXTURE["error_envelopes"]:
            with self.subTest(error=row["data"]["error"]):
                failure = ott.PlayerUnsupported if row["status"] == "unsupported" else ott.PlayerRejected
                self.client.inspect_failure = failure(PRIVATE_URL, copy.deepcopy(row["data"]))
                self.client.calls.clear()
                code, result = self.execute()
                self.assertEqual((code, result["observations"][0]["reason"]), (3, row["data"]["error"]))
                self.assertNotIn("data", result["observations"][0])
                self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])

    def test_unbound_or_malformed_outer_response_is_never_attributed_to_requested_runtime(self):
        for field, value in (("runtime", "another_runtime"), ("section", "debug"), ("version", True),
                             ("version", 2), ("url", PRIVATE_URL)):
            self.client.inspect_envelope = envelope()
            self.client.inspect_envelope[field] = value
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
            self.assertNotIn("data", result["observations"][0])
        self.client.inspect_envelope = None
        for raw in (None, {"version": 1, "runtime": "another_runtime", "section": "native_playback", "error": "runtime_mismatch"},
                    {"version": 1, "runtime": NATIVE_RUNTIME, "section": "native_playback", "error": PRIVATE_URL}):
            self.client.inspect_failure = ott.PlayerUnsupported(PRIVATE_URL, raw)
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))

    def test_negative_error_codes_must_match_transport_status(self):
        for failure, error in ((ott.PlayerRejected, "unsupported"), (ott.PlayerUnsupported, "unavailable"),
                               (ott.PlayerUnsupported, "invalid_request"), (ott.PlayerUnsupported, "runtime_mismatch")):
            with self.subTest(failure=failure.__name__, error=error):
                raw = {"version": 1, "runtime": NATIVE_RUNTIME, "section": "native_playback", "error": error}
                self.client.inspect_failure = failure(PRIVATE_URL, raw)
                code, result = self.execute()
                self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
                self.assertNotIn("data", result["observations"][0])
        self.client.inspect_failure = None
        for error in ("unsupported", "unavailable", "invalid_request", "runtime_mismatch"):
            self.client.inspect_envelope = {"version": 1, "runtime": NATIVE_RUNTIME, "section": "native_playback", "error": error}
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))

    def test_native_inner_envelope_limit_is_utf8_bytes_and_does_not_reduce_other_sections(self):
        raw = envelope({})
        base = len(json.dumps(raw, separators=(",", ":")).encode())
        # Replacing {} with a quoted string has the same JSON overhead.
        raw["data"] = "x" * (4096 - base)
        self.assertEqual(wb.inspection_data(raw, NATIVE_RUNTIME, "native_playback"), raw["data"])
        raw["data"] += "x"
        with self.assertRaises(wb.InvalidData):
            wb.inspection_data(raw, NATIVE_RUNTIME, "native_playback")
        raw["data"] = "é" * (4096 - base)
        with self.assertRaises(wb.InvalidData):
            wb.inspection_data(raw, NATIVE_RUNTIME, "native_playback")
        raw["section"] = "debug"
        self.assertEqual(wb.inspection_data(raw, NATIVE_RUNTIME, "debug"), raw["data"])

    def test_transport_failure_or_shared_deadline_never_retries_or_reports_success(self):
        for failure, reason in ((ott.HTTPError(400), "unsupported_controller"),
                                (ott.RequestError("request_pending_timeout", "receipt", PRIVATE_URL, request_id="a" * 32),
                                 "request_pending_timeout")):
            self.client.inspect_failure = failure
            self.client.calls.clear()
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, reason))
            self.assertEqual([call[1] for call in self.client.calls], ["capabilities", "inspect"])
            self.assertEqual(self.client.timeout, 20)
        self.client.inspect_failure = None
        original = self.client.call_receipt

        def consume(target, action, params):
            receipt = original(target, action, params)
            self.clock.sleep(2)
            return receipt

        self.client.calls.clear()
        with mock.patch.object(self.client, "call_receipt", side_effect=consume):
            code, result = self.execute(timeout=2)
        self.assertEqual((code, result["observations"][0]["reason"]), (3, "deadline_exceeded"))
        self.assertEqual([call[1] for call in self.client.calls], ["capabilities"])

    def test_group_uses_real_route_in_parallel_and_keeps_json_isolated(self):
        self.client.barrier = threading.Barrier(2)
        args = SimpleNamespace(words=["t1,t2", "debug", "--native-playback"], json=True, timeout=20, config="/unused")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.player_group_command(self.client, self.client.config, [("t1", "web"), ("t2", "web2")], args, [])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result["succeeded"], result["failed"]), (0, 2, 0))
        self.assertEqual([row["player"] for row in result["results"]], ["t1", "t2"])
        for row in result["results"]:
            self.assertEqual(row["data"]["verdict"], "observed")
            self.assertEqual(row["data"]["observations"][0]["data"], sample())
        for child in self.client.children:
            self.assertEqual([call[1] for call in child.calls], ["capabilities", "inspect"])
        self.assertEqual(errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
