"""Explicit v3 HLS policy capture and strict backward-compatible readers."""
import copy
import hashlib
import json
import io
from contextlib import redirect_stdout
import unittest

import test_performance_cli as legacy
from test_performance_cli import ROOT, SID, perf, session, schema_minimal
from test_performance_hls_cli import HLSClient
from test_workbench_cli import RUNTIME, ott


V3 = json.loads((ROOT / "contracts/perf-capture-v3.json").read_text())


def sample_policy(kind, seq=1):
    value = schema_minimal(V3["$defs"][kind + "_event"])
    value.update(seq=seq, at_ms=0, lane="main", source_id="s1")
    return value


def reply_policy(kinds, capture_version=3):
    events = [sample_policy(kind, i + 1) for i, kind in enumerate(kinds)]
    return {"version": 3, "op": "read", "runtime": RUNTIME,
            "session": dict(session(events), capture_version=capture_version),
            "events": events, "next_after_seq": len(events), "more": False, "lost_before": 0}


def policy_request():
    return {"version": 3, "op": "read", "runtime": RUNTIME, "session_id": SID, "after_seq": 0}


class HLSPolicyClient(HLSClient):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities["perf_capture_hls_policy"] = {"version": 3}
        self.policy_old_controller = False

    def call_receipt(self, target, action, params):
        if action == "perf_capture" and self.policy_old_controller and params["version"] == 3:
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            raise ott.HTTPError(400)
        receipt = super().call_receipt(target, action, params)
        if action == "perf_capture" and params["version"] == 3 and receipt["data"]["session"] is not None:
            receipt["data"]["session"]["capture_version"] = self.capture_version
        return receipt


class PerformanceHLSPolicyTest(unittest.TestCase):
    run_perf = legacy.PerformanceTest.run_perf

    def setUp(self):
        legacy.PerformanceTest.setUp(self)
        self.client = HLSPolicyClient(self.clock)

    def test_actual_player_corpus_preserves_all_wire_and_capture_versions(self):
        rows = json.loads((ROOT / "tests/fixtures/perf_capture_producer_v3.json").read_text())
        versions, captures, kinds = set(), set(), set()
        for row in rows:
            response = row["response"]
            value = perf.metadata(response["data"], row["params"], rejected=response["status"] != "ok")
            versions.add(row["params"]["version"])
            if value.get("session"):
                captures.add(value["session"].get("capture_version", 1))
            kinds.update(event["kind"] for event in value.get("events", []))
        self.assertEqual(versions, {1, 2, 3})
        self.assertEqual(captures, {1, 2, 3})
        self.assertTrue(set(perf.HLS_POLICY_EVENT_DATA) <= kinds)

    def test_shared_schema_gates_read_events_by_capture_version(self):
        gates = V3["responses"]["read"]["allOf"]
        self.assertEqual(len(gates), 3)
        for version, gate in enumerate(gates, 1):
            self.assertEqual(gate["if"]["properties"]["session"]["properties"]["capture_version"], {"enum": [version]})
            allowed = {item["$ref"] for item in gate["then"]["properties"]["events"]["items"]["oneOf"]}
            if version < 3:
                previous = json.loads((ROOT / ("contracts/perf-capture-v%d.json" % version)).read_text())
                expected = {item["$ref"].replace("/hls_event", "/hls_legacy_event")
                            .replace("/hls_fragment_event", "/hls_fragment_legacy_event")
                            for item in previous["$defs"]["event"]["oneOf"]}
            else:
                expected = {item["$ref"] for item in V3["$defs"]["event"]["oneOf"]
                            if "legacy" not in item["$ref"]}
            self.assertEqual(allowed, expected)
        self.assertEqual(V3["responses"]["start"]["properties"]["session"]["allOf"][1],
                         {"properties": {"capture_version": {"enum": [3]}}})

    def test_legacy_contract_bytes_remain_unchanged(self):
        for version, expected in ((1, "e80ee9749d2b8594d3e008c80a873dcdc2201abfadbafd19a376e5a3467838d0"),
                                  (2, "b1c83b2c86fce859647b68c4240368ace2b2b2865389b84eede58803d38a37ee")):
            raw = (ROOT / ("contracts/perf-capture-v%d.json" % version)).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        self.assertEqual(V3["capability"]["perf_capture_hls_policy"], perf.HLS_POLICY_CAPABILITY)

    def test_exact_v3_shapes_and_legacy_shapes_do_not_cross_capture_versions(self):
        for kind in perf.HLS_POLICY_EVENT_DATA:
            value = reply_policy([kind])
            self.assertEqual(perf.metadata(value, policy_request()), value)
            for version in (1, 2):
                value["session"]["capture_version"] = version
                with self.subTest(kind=kind, version=version), self.assertRaises(perf.Failure):
                    perf.metadata(value, policy_request())
        for version in (1, 2):
            value = reply_policy(["hls_legacy"], version)
            self.assertEqual(perf.metadata(value, policy_request()), value)
        value = reply_policy(["hls_fragment_legacy"], 2)
        self.assertEqual(perf.metadata(value, policy_request()), value)
        value["session"]["capture_version"] = 3
        with self.assertRaises(perf.Failure):
            perf.metadata(value, policy_request())

    def test_unknown_and_availability_changes_are_preserved_without_cause_inference(self):
        for before, after in ((20, 5.005), (20, None), (None, 5.005)):
            value = reply_policy(["hls_buffer_policy"])
            data = value["events"][0]["data"]
            data.update(event="cap_observation_changed", observation="sample",
                        previous_max_max_buffer_length_seconds=before)
            data["current"]["max_max_buffer_length_seconds"] = after
            self.assertEqual(perf.metadata(value, policy_request())["events"][0]["data"], data)
            self.assertEqual(data["cause"], "unknown")
        value = reply_policy(["hls_buffer_policy"])
        data = value["events"][0]["data"]
        data["initial_source"] = "unavailable"
        self.assertEqual(perf.metadata(value, policy_request())["events"][0]["data"], data)

    def test_v3_rejects_unsupported_fields_and_contradictions(self):
        changes = [
            ("hls", lambda d: d.update(retries=1)),
            ("hls", lambda d: d.update(actual_network_retries=1)),
            ("hls", lambda d: d.update(stats_retry=True)),
            ("hls_buffer_policy", lambda d: d.update(cause="buffer_full_error")),
            ("hls_buffer_policy", lambda d: d.update(previous_max_max_buffer_length_seconds=20)),
            ("hls_buffer_policy", lambda d: d.update(event="cap_observation_changed")),
            ("hls_buffer_policy", lambda d: d["current"].update(max_buffer_size_bytes=1.2)),
            ("hls_buffer_policy", lambda d: (d.update(initial_source="unavailable"), d["initial"].update(max_buffer_length_seconds=20))),
            ("hls_fragment", lambda d: d.update(part_gap=1)),
            ("hls_fragment", lambda d: d.update(elementary_streams=[{"track": "video", "start_pts_seconds": 0, "end_pts_seconds": 1}])),
            ("hls_error_action", lambda d: d.update(flags=32)),
            ("hls_error_action", lambda d: d.update(retry_budget_count=-1)),
            ("hls_error_action", lambda d: d.update(action="http_retry")),
            ("hls_error_action", lambda d: d.update(url="private")),
        ]
        for kind, mutate in changes:
            value = reply_policy([kind])
            mutate(value["events"][0]["data"])
            with self.subTest(kind=kind, data=value["events"][0]["data"]), self.assertRaises(perf.Failure):
                perf.metadata(value, policy_request())

    def test_v3_keeps_observed_flags_and_error_action_budget(self):
        value = reply_policy(["hls", "hls_fragment", "hls_error_action"])
        value["events"][0]["data"].update(stats_retry=1, detail="buffer_full_error")
        value["events"][1]["data"].update(fragment_gap=True, part_gap=False, part_independent=None,
            elementary_streams=[{"track": "video", "start_pts_seconds": 0, "end_pts_seconds": 1, "partial": True}])
        value["events"][2]["data"].update(action="retry_request", flags=31, retry_budget_count=2,
                                         resolved=False, detail="buffer_append_no_progress")
        self.assertEqual(perf.metadata(value, policy_request()), value)
        for detail in ("buffer_full_error", "internal_exception", "buffer_append_no_progress", "buffer_add_codec_error"):
            value["events"][0]["data"]["detail"] = detail
            value["events"][2]["data"]["detail"] = detail
            self.assertEqual(perf.metadata(value, policy_request()), value)
            legacy_value = reply_policy(["hls_legacy"], 2)
            legacy_value["events"][0]["data"]["detail"] = detail
            with self.assertRaises(perf.Failure):
                perf.metadata(legacy_value, policy_request())

    def test_new_details_are_explicit_and_start_defaults_stay_unchanged(self):
        for flag, version in ((None, 1), ("--hls-details", 2), ("--hls-policy-details", 3)):
            self.setUp()
            code, result = self.run_perf(["start", "--session", SID] + ([flag] if flag else []))
            self.assertEqual(code, 0, result)
            self.assertEqual(self.client.calls[-1][2]["version"], version)
            self.assertEqual(result["data"]["session"].get("capture_version", 1), version)
            self.assertEqual((result["version"], result["evaluator"]), (version, "performance-v" + str(version)))

    def test_policy_flag_requires_capability_and_cannot_silently_downgrade(self):
        del self.client.capabilities["perf_capture_hls_policy"]
        code, result = self.run_perf(["start", "--hls-policy-details"])
        self.assertEqual((code, result["reason"]), (3, "unsupported"))
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities"])
        self.setUp()
        self.client.policy_old_controller = True
        code, result = self.run_perf(["start", "--hls-policy-details"])
        self.assertEqual((code, result["reason"]), (3, "unsupported_controller"))
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities", "perf_capture"])
        self.assertEqual(self.client.calls[-1][2]["version"], 3)
        self.assertIsNone(self.client.current)

    def test_player_help_only_lists_policy_option_when_advertised(self):
        output = io.StringIO()
        with redirect_stdout(output):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(self.client.capabilities)}, "d1")
        self.assertIn("perf start --hls-policy-details", output.getvalue())
        del self.client.capabilities["perf_capture_hls_policy"]
        output = io.StringIO()
        with redirect_stdout(output):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(self.client.capabilities)}, "d1")
        self.assertNotIn("--hls-policy-details", output.getvalue())

    def test_policy_marker_is_strict_and_requires_base_capability(self):
        self.assertEqual(ott.capabilities_metadata(self.client.capabilities)["perf_capture_hls_policy"], {"version": 3})
        for value in (None, [], {}, {"version": True}, {"version": 2}, {"version": 3, "extra": 0}):
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, perf_capture_hls_policy=value))
        del self.client.capabilities["perf_capture"]
        with self.assertRaises(ott.Error):
            ott.capabilities_metadata(self.client.capabilities)

    def test_readers_preserve_old_capture_version_in_v3_envelope(self):
        for flag, version in ((None, 1), ("--hls-details", 2), ("--hls-policy-details", 3)):
            self.setUp()
            self.assertEqual(self.run_perf(["start", "--session", SID] + ([flag] if flag else []))[0], 0)
            for words in (["status"], ["read", SID], ["stop", SID], ["export", SID, "--out", "/memory-policy"]):
                code, result = self.run_perf(words)
                self.assertEqual(code, 0, result)
                self.assertEqual(self.client.calls[-1][2]["version"], 3)
                if "data" in result:
                    self.assertEqual(result["data"]["session"]["capture_version"], version)
                else:
                    self.assertEqual(result["capture"]["version"], 3)
                    self.assertEqual(result["capture"]["first_session"]["capture_version"], version)

    def test_capture_and_envelope_version_fences(self):
        for requested, captured in ((1, 3), (2, 3), (3, 4)):
            value = {"version": requested, "op": "status", "runtime": RUNTIME,
                     "session": dict(session(), capture_version=captured)}
            params = {"version": requested, "op": "status", "runtime": RUNTIME}
            with self.subTest(requested=requested), self.assertRaises(perf.Failure):
                perf.metadata(value, params)
        for captured in (1, 2):
            value = {"version": 3, "op": "start", "runtime": RUNTIME,
                     "session": dict(session(), capture_version=captured)}
            with self.assertRaises(perf.Failure):
                perf.metadata(value, {"version": 3, "op": "start", "runtime": RUNTIME, "session_id": SID})

    def test_flags_are_mutually_exclusive_and_start_only(self):
        for words in (["start", "--hls-details", "--hls-policy-details"],
                      ["status", "--hls-policy-details"],
                      ["trial", "notifications", "--out", "/unused", "--hls-policy-details"]):
            with self.subTest(words=words), self.assertRaises(perf.Failure):
                perf.arguments(words)

    def test_trial_remains_v1_when_v3_advertised(self):
        code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", "/memory-trial"], 180)
        self.assertEqual(code, 0, result)
        self.assertTrue(all(row[2]["version"] == 1 for row in self.client.calls if row[1] == "perf_capture"))


if __name__ == "__main__":
    unittest.main()
