"""Wire compatibility and explicit opt-in for bounded HLS diagnostics."""
import copy
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout

import test_performance_cli as legacy
from test_performance_cli import CONTRACT, ROOT, SID, PerfClient, perf, session, schema_minimal
from test_workbench_cli import RUNTIME, ott

V2 = json.loads((ROOT / "contracts/perf-capture-v2.json").read_text())


class HLSClient(PerfClient):
    def __init__(self, clock):
        super().__init__(clock)
        self.capabilities.update(V2["capability"])
        self.capture_version = 1
        self.old_controller = False

    def call_receipt(self, target, action, params):
        if action == "perf_capture" and self.old_controller and params["version"] == 2:
            self.calls.append((target, action, copy.deepcopy(params), self.timeout))
            raise ott.HTTPError(400)
        receipt = super().call_receipt(target, action, params)
        if action == "perf_capture":
            if params["op"] == "start":
                self.capture_version = params["version"]
            receipt["data"]["version"] = params["version"]
            if params["version"] == 2 and receipt["data"]["session"] is not None:
                receipt["data"]["session"]["capture_version"] = self.capture_version
        return receipt


def sample_hls(kind, seq=1):
    # The new families contain no references: build from their exact shared schema.
    value = schema_minimal(V2["$defs"][kind + "_event"])
    value.update(seq=seq, at_ms=0, lane="main", source_id="s1")
    return value


def reply_hls(kinds, capture_version=2):
    events = [sample_hls(kind, i + 1) for i, kind in enumerate(kinds)]
    return {"version": 2, "op": "read", "runtime": RUNTIME,
            "session": dict(session(events), capture_version=capture_version),
            "events": events, "next_after_seq": len(events), "more": False, "lost_before": 0}


def read_request():
    return {"version": 2, "op": "read", "runtime": RUNTIME, "session_id": SID, "after_seq": 0}


class PerformanceHLSTest(unittest.TestCase):
    run_perf = legacy.PerformanceTest.run_perf

    def setUp(self):
        legacy.PerformanceTest.setUp(self)
        self.client = HLSClient(self.clock)

    def test_v1_schema_bytes_and_capability_are_unchanged(self):
        raw = (ROOT / "contracts/perf-capture-v1.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "e80ee9749d2b8594d3e008c80a873dcdc2201abfadbafd19a376e5a3467838d0")
        self.assertEqual(CONTRACT["capability"]["perf_capture"], V2["capability"]["perf_capture"])
        self.assertEqual(V2["capability"]["perf_capture_hls"], perf.HLS_CAPABILITY)
        self.assertEqual(V2["limits"], CONTRACT["limits"])

    def test_detail_marker_is_strict_and_requires_base_capability(self):
        self.assertEqual(ott.capabilities_metadata(self.client.capabilities)["perf_capture_hls"], {"version": 2})
        for value in [None, [], {}, {"version": True}, {"version": 1}, {"version": 2, "private": 0}]:
            with self.subTest(value=value), self.assertRaises(ott.Error):
                ott.capabilities_metadata(dict(self.client.capabilities, perf_capture_hls=value))
        del self.client.capabilities["perf_capture"]
        with self.assertRaises(ott.Error):
            ott.capabilities_metadata(self.client.capabilities)

    def test_only_explicit_start_enables_details(self):
        code, result = self.run_perf(["start", "--session", SID])
        self.assertEqual(code, 0, result)
        self.assertEqual(self.client.calls[-1][2]["version"], 1)
        self.assertNotIn("capture_version", result["data"]["session"])
        self.setUp()
        code, result = self.run_perf(["start", "--session", SID, "--hls-details"])
        self.assertEqual(code, 0, result)
        self.assertEqual(self.client.calls[-1][2]["version"], 2)
        self.assertEqual(result["data"]["session"]["capture_version"], 2)
        self.assertEqual((result["version"], result["evaluator"]), (2, "performance-v2"))

    def test_missing_detail_capability_never_dispatches_capture(self):
        del self.client.capabilities["perf_capture_hls"]
        code, result = self.run_perf(["start", "--hls-details"])
        self.assertEqual(code, 3)
        self.assertEqual(result["reason"], "unsupported")
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities"])
        self.assertIsNone(self.client.current)

    def test_old_controller_rejects_once_without_downgrade(self):
        self.client.old_controller = True
        code, result = self.run_perf(["start", "--hls-details"])
        self.assertEqual(code, 3)
        self.assertEqual(result["reason"], "unsupported_controller")
        self.assertEqual([row[1] for row in self.client.calls], ["capabilities", "perf_capture"])
        self.assertEqual(self.client.calls[-1][2]["version"], 2)
        self.assertIsNone(self.client.current)
        output = io.StringIO()
        with redirect_stdout(output):
            perf.brief(result)
        self.assertIn("update the controller", output.getvalue())

    def test_readers_auto_v2_and_preserve_original_capture_version(self):
        for version in (1, 2):
            self.setUp()
            self.run_perf(["start", "--session", SID] + (["--hls-details"] if version == 2 else []))
            for words in (["status"], ["read", SID], ["stop", SID], ["export", SID, "--out", "/memory-hls"]):
                code, result = self.run_perf(words)
                self.assertEqual(code, 0, result)
                self.assertEqual(self.client.calls[-1][2]["version"], 2)
                if "data" in result:
                    self.assertEqual(result["data"]["session"]["capture_version"], version)
                else:
                    self.assertEqual(result["capture"]["version"], 2)
                    self.assertEqual(result["capture"]["first_session"]["capture_version"], version)

    def test_v1_session_cannot_contain_v2_events_even_in_v2_envelope(self):
        for kind in perf.HLS_EVENT_DATA:
            value = reply_hls([kind])
            self.assertEqual(perf.metadata(value, read_request()), value)
            value["session"]["capture_version"] = 1
            with self.assertRaises(perf.Failure):
                perf.metadata(value, read_request())

    def test_start_cannot_silently_downgrade_and_negative_echoes_version(self):
        request = dict(read_request(), op="start")
        del request["after_seq"]
        value = {"version": 2, "op": "start", "runtime": RUNTIME, "session": dict(session(), capture_version=1)}
        with self.assertRaises(perf.Failure):
            perf.metadata(value, request)
        negative = {"version": 2, "op": "start", "runtime": RUNTIME, "error": "unsupported"}
        self.assertEqual(perf.metadata(negative, request, rejected=True), negative)
        negative["version"] = 1
        with self.assertRaises(perf.Failure):
            perf.metadata(negative, request, rejected=True)

    def test_holes_and_truncated_ranges_are_preserved(self):
        value = reply_hls(["hls_buffer"])
        data = value["events"][0]["data"]
        data["position_seconds"] = 10.1
        data["buffered"] = {"range_count": 3, "truncated_count": 1, "ranges": [
            {"index": 1, "start_seconds": 0, "end_seconds": 10},
            {"index": 2, "start_seconds": 10.2, "end_seconds": 20}]}
        self.assertEqual(perf.metadata(value, read_request())["events"][0]["data"], data)
        data["buffered"]["truncated_count"] = 0
        with self.assertRaises(perf.Failure):
            perf.metadata(value, read_request())

    def test_rich_events_reject_private_invalid_or_missing_identity(self):
        mutations = [
            ("hls_buffer", lambda e: e["data"].update(track="other")),
            ("hls_buffer", lambda e: e.update(source_id=None)),
            ("hls_fragment", lambda e: e["data"].update(start_seconds=20, end_seconds=10)),
            ("hls_fragment", lambda e: e["data"].update(elementary_streams=[{"track": "audio", "start_pts_seconds": 0, "end_pts_seconds": 1}] * 2)),
            ("hls_fragment", lambda e: e["data"].update(elementary_streams=[{"track": "audio", "start_pts_seconds": 10, "end_pts_seconds": 1}])),
            ("hls_config", lambda e: e["data"].update(max_buffer_size_bytes=2.5)),
            ("hls_config", lambda e: e["data"].update(max_buffer_hole_seconds=float("inf"))),
            ("hls_live", lambda e: e["data"].update(latency_seconds="private")),
            ("hls_live", lambda e: e["data"].update(url="https://private.invalid")),
        ]
        for kind, mutation in mutations:
            with self.subTest(kind=kind):
                value = reply_hls([kind])
                mutation(value["events"][0])
                with self.assertRaises(perf.Failure):
                    perf.metadata(value, read_request())

    def test_signed_live_latency_and_observed_bitrates_are_preserved(self):
        value = reply_hls(["hls_live"])
        data = value["events"][0]["data"]
        data.update(latency_seconds=-0.75, effective_buffer_length_seconds=2.4,
                    current_level={"index": 0, "bitrate": 200000000, "max_bitrate": 210000000},
                    load_level={"index": -1, "bitrate": None, "max_bitrate": None})
        self.assertEqual(perf.metadata(value, read_request())["events"][0]["data"], data)
        data["load_level"]["bitrate"] = 5000000
        with self.assertRaises(perf.Failure):
            perf.metadata(value, read_request())

    def test_export_detects_capture_version_change_between_pages(self):
        self.run_perf(["start", "--session", SID, "--hls-details"])
        original = self.client.call_receipt
        def changed(target, action, params):
            result = original(target, action, params)
            if action == "perf_capture" and params["op"] == "read":
                result["data"]["session"]["capture_version"] = 1
            return result
        self.client.call_receipt = changed
        code, result = self.run_perf(["export", SID, "--out", "/memory-changed"])
        self.assertEqual(code, 3)
        self.assertIn("session_changed", result["capture"]["partial_reasons"])

    def test_hls_flag_is_start_only(self):
        for words in (["status", "--hls-details"], ["trial", "notifications", "--out", "/not-created", "--hls-details"]):
            with self.assertRaises(perf.Failure):
                perf.arguments(words)

    def test_notification_trial_keeps_v1_even_with_hls_capability(self):
        code, result = self.run_perf(["trial", "notifications", "--seconds-per-phase", "10", "--out", "/memory-trial"], 180)
        self.assertEqual(code, 0, result)
        self.assertTrue(all(row[2]["version"] == 1 for row in self.client.calls if row[1] == "perf_capture"))

    def test_actual_player_corpus_covers_all_hls_families(self):
        corpus = json.loads((ROOT / "tests/fixtures/perf_capture_producer_v2.json").read_text())
        kinds, operations = set(), set()
        for row in corpus:
            response = row["response"]
            result = perf.metadata(response["data"], row["params"], rejected=response["status"] != "ok")
            operations.add(row["params"]["op"])
            kinds.update(event["kind"] for event in result.get("events", []))
        self.assertTrue({"start", "read", "stop"}.issubset(operations))
        self.assertTrue(set(perf.HLS_EVENT_DATA).issubset(kinds))


if __name__ == "__main__":
    unittest.main()
