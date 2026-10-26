"""Offline v3 performance reports preserve unknowns and reject contradictions."""
import copy
import json
import unittest

import test_performance_report as reports
from test_performance_hls_policy_cli import HLSPolicyClient, sample_policy, ROOT


class PerformanceHLSPolicyReportTest(unittest.TestCase):
    setUp = reports.PerformanceReportTest.setUp
    run_perf = reports.PerformanceReportTest.run_perf
    persist = reports.PerformanceReportTest.persist
    invoke = reports.PerformanceReportTest.invoke

    def capture_policy(self, capture_version=3):
        self.client = HLSPolicyClient(self.clock)
        flag = [] if capture_version == 1 else ["--hls-details" if capture_version == 2 else "--hls-policy-details"]
        self.assertEqual(self.run_perf(["start", "--session", reports.SID] + flag)[0], 0)
        if capture_version == 3:
            self.client.events = [sample_policy(kind, index + 1) for index, kind in enumerate(
                ("hls", "hls_fragment", "hls_buffer_policy", "hls_error_action"))]
        self.assertEqual(self.run_perf(["export", reports.SID, "--out", str(self.path)])[0], 0)
        self.records = copy.deepcopy(self.journal.records)
        self.persist()

    def test_actual_producer_reads_export_and_verify_including_partial_evidence(self):
        corpus = json.loads((ROOT / "tests/fixtures/perf_capture_producer_v3.json").read_text())
        rows = [row for row in corpus if row["response"]["status"] == "ok" and row["params"]["op"] == "read"]
        observed_versions, partial = set(), False
        for index, row in enumerate(rows):
            with self.subTest(index=index):
                captured = copy.deepcopy(row["response"]["data"])
                self.client = HLSPolicyClient(self.clock)
                self.client.capabilities["player"]["runtime"] = captured["runtime"]
                original = self.client.call_receipt
                def replay(target, action, params):
                    if action != "perf_capture":
                        return original(target, action, params)
                    self.client.calls.append((target, action, copy.deepcopy(params), self.client.timeout))
                    if params["op"] == "status":
                        data = {"version": 3, "op": "status", "runtime": captured["runtime"],
                                "session": copy.deepcopy(captured["session"])}
                    else:
                        self.assertEqual(params["op"], "read")
                        self.assertEqual(params["after_seq"], 0)
                        data = copy.deepcopy(captured)
                    return {"request_id": "corpus-" + str(len(self.client.calls)), "status": "ok", "data": data}
                self.client.call_receipt = replay
                code, live = self.run_perf(["export", captured["session"]["id"], "--out", str(self.path)])
                self.assertIn(code, (0, 3), live)
                self.records = copy.deepcopy(self.journal.records)
                self.persist()
                checked_code, checked = self.invoke()
                self.assertEqual(checked_code, code, checked)
                self.assertTrue(checked["valid"])
                observed_versions.add(captured["session"]["capture_version"])
                partial = partial or code == 3
        self.assertEqual(observed_versions, {1, 2, 3})
        self.assertTrue(partial)

    def test_offline_v3_export_works_without_network_or_configuration(self):
        self.capture_policy()
        code, result = self.invoke()
        self.assertEqual((code, result["evaluation_policy"], result["event_count"]), (0, "performance-v3", 4))
        self.assertTrue(result["capture_complete"])
        self.assertFalse(result["physical_display_verified"])
        data = self.records["result.json"]["capture"]["events"][2]["data"]
        self.assertEqual(data["cause"], "unknown")

    def test_v3_export_preserves_legacy_capture_version(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.capture_policy(version)
                capture = self.records["result.json"]["capture"]
                self.assertEqual(capture["first_session"]["capture_version"], version)
                code, result = self.invoke()
                self.assertEqual((code, result["evaluation_policy"]), (0, "performance-v3"))

    def test_rehashed_guessed_cause_is_still_invalid(self):
        self.capture_policy()
        self.records["result.json"]["capture"]["events"][2]["data"]["cause"] = "buffer_full_error"
        self.persist(rehash=True)
        code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertFalse(result["valid"])

    def test_legacy_shape_cannot_be_relabelled_as_v3(self):
        self.capture_policy()
        data = self.records["result.json"]["capture"]["events"][0]["data"]
        data["retries"] = data.pop("stats_retry")
        self.persist(rehash=True)
        code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
