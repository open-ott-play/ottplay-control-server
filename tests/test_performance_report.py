"""Portable performance evidence: real collectors, offline replay, hostile files."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_performance_cli as fixtures
from test_performance_hls_cli import HLSClient, sample_hls
from test_report_verify import verify, wb

perf, ott, SID = fixtures.perf, fixtures.ott, fixtures.SID
PATH_MARKER = "private-path-must-not-be-exported"


class PerformanceReportTest(unittest.TestCase):
    run_perf = fixtures.PerformanceTest.run_perf

    def setUp(self):
        fixtures.PerformanceTest.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / PATH_MARKER

    def capture(self, version=1, start=True):
        if version == 2:
            self.client = HLSClient(self.clock)
        if start:
            self.assertEqual(self.run_perf(["start", "--session", SID] +
                                          (["--hls-details"] if version == 2 else []))[0], 0)
        code, result = self.run_perf(["export", SID, "--out", str(self.path)])
        self.records = copy.deepcopy(self.journal.records)
        self.persist()
        return code, result

    def persist(self, rehash=False):
        self.path.mkdir(exist_ok=True)
        if rehash:
            self.records["manifest.json"]["files"] = [
                {"name": name, "bytes": len(perf.encoded(value)),
                 "sha256": hashlib.sha256(perf.encoded(value)).hexdigest()}
                for name, value in self.records.items() if name != "manifest.json"]
        for name, value in self.records.items():
            (self.path / name).write_bytes(perf.encoded(value))

    def invoke(self, json_output=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                mock.patch.object(ott, "Client", side_effect=AssertionError("network forbidden")), \
                mock.patch.object(ott, "read_json", side_effect=AssertionError("config forbidden")):
            code = verify.main(["verify", str(self.path)] + (["--json"] if json_output else []), ott, wb)
        self.assertNotIn(PATH_MARKER, output.getvalue())
        return code, json.loads(output.getvalue()) if json_output else output.getvalue()

    def test_complete_export_is_portable_and_verifies_without_configuration(self):
        code, live = self.capture()
        self.assertEqual(code, 0)
        self.assertEqual(live["output"], str(self.path))
        self.assertNotIn("output", self.records["result.json"])
        self.assertNotIn(PATH_MARKER, json.dumps(self.records))
        code, checked = self.invoke()
        self.assertEqual((code, checked["integrity"], checked["evaluation_policy"]),
                         (0, "verified", "performance-v1"))
        self.assertTrue(checked["capture_complete"])
        self.assertFalse(checked["authenticated"])
        self.assertFalse(checked["physical_display_verified"])
        self.assertEqual(checked["event_count"], 1)
        result = subprocess.run([sys.executable, str(fixtures.ROOT / "cli/ott.py"),
                                 "--config", str(self.path / "missing-config"),
                                 "report", "verify", str(self.path), "--json"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_old_local_output_path_is_ignored_and_never_printed(self):
        self.capture()
        self.records["result.json"]["output"] = str(self.path)
        self.persist(rehash=True)
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.invoke(False)[0], 0)

    @unittest.skipUnless(os.name == "posix", "private export writer requires POSIX")
    def test_real_export_writer_roundtrip_and_private_permissions(self):
        self.run_perf(["start", "--session", SID])
        code, result = self.run_perf(["export", SID, "--out", str(self.path)], memory=False)
        self.assertEqual(code, 0, result)
        self.assertNotIn(PATH_MARKER, (self.path / "result.json").read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.path.iterdir()))
        self.assertEqual(self.invoke()[0], 0)

    def test_v2_hls_events_and_v1_session_over_v2_transport(self):
        for details in (True, False):
            with self.subTest(details=details):
                self.client = HLSClient(self.clock)
                self.run_perf(["start", "--session", SID] + (["--hls-details"] if details else []))
                if details:
                    self.client.events = [sample_hls(kind, index + 1)
                                          for index, kind in enumerate(perf.HLS_EVENT_DATA)]
                code, _ = self.capture(start=False)
                self.assertEqual(code, 0)
                code, checked = self.invoke()
                self.assertEqual((code, checked["evaluation_policy"]), (0, "performance-v2"))

    def test_partial_transport_failure_remains_valid_unknown(self):
        self.run_perf(["start", "--session", SID])
        self.client.fail_page = True
        code, live = self.capture(start=False)
        self.assertEqual(code, 3)
        code, checked = self.invoke()
        self.assertEqual((code, checked["valid"], checked["capture_complete"]), (3, True, False))
        self.assertIn("transport_error", checked["partial_reasons"])
        self.assertIn("missing_tail", checked["partial_reasons"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            perf.brief(live)
        self.assertIn("Capture: partial", output.getvalue())
        self.assertIn("transport_error", output.getvalue())

    def test_dropped_prefix_and_page_limit_recompute_as_partial(self):
        self.run_perf(["start", "--session", SID])
        self.client.events = [fixtures.event(2), fixtures.event(3)]
        self.client.current.update(dropped_events=1, ring_dropped=1)
        code, _ = self.capture(start=False)
        self.assertEqual(code, 3)
        code, checked = self.invoke()
        self.assertEqual(code, 3, checked)
        self.assertEqual(set(checked["partial_reasons"]), {"discarded_prefix", "sequence_gap", "events_dropped"})
        self.run_perf(["start", "--session", SID])
        self.client.events = [fixtures.event(index) for index in range(1, 101)]
        self.client.max_page = 1
        self.capture(start=False)
        self.assertEqual(self.invoke()[0], 3)

    def test_failed_preflight_export_is_verified_as_unavailable(self):
        del self.client.capabilities["perf_capture"]
        code, _ = self.capture(start=False)
        self.assertEqual(code, 3)
        code, checked = self.invoke()
        self.assertEqual((code, checked["reason"]), (3, "capture_unavailable"))

    def test_late_capabilities_and_deadline_before_first_request_are_valid_unknown(self):
        original = self.client.call_receipt
        def response(target, action, params):
            receipt = original(target, action, params)
            if action == "capabilities":
                self.clock.sleep(241)
            return receipt
        self.client.call_receipt = response
        code, live = self.capture(start=False)
        self.assertEqual(code, 3)
        self.assertIn("confirmed_late_result", live)
        self.assertEqual(self.invoke()[0], 3)
        # A fresh directory with only manifest/result is valid when the budget
        # expires before dispatch; there must be no invented request receipt.
        self.path = self.path.parent / "zero-request"
        code, live = self.run_perf(["export", SID, "--out", str(self.path)], timeout=0)
        self.records = copy.deepcopy(self.journal.records)
        self.persist()
        self.assertEqual((code, live["requests"]), (3, []))
        self.assertEqual(self.invoke()[0], 3)

    def test_typed_request_failures_survive_export_without_retry_or_raw_messages(self):
        for reason in (*perf.REQUEST_FAILURE_REASONS, PATH_MARKER):
            with self.subTest(reason=reason):
                self.client = fixtures.PerfClient(self.clock)
                self.run_perf(["start", "--session", SID])
                original, attempts = self.client.call_receipt, []
                def response(target, action, params):
                    if action == "perf_capture" and params["op"] == "read":
                        attempts.append(params)
                        raise ott.RequestError(reason, "response_poll", PATH_MARKER)
                    return original(target, action, params)
                self.client.call_receipt = response
                code, live = self.capture(start=False)
                self.assertEqual((code, len(attempts)), (3, 1))
                expected = reason if reason != PATH_MARKER else "transport_error"
                self.assertIn(expected, live["capture"]["partial_reasons"])
                code, checked = self.invoke()
                self.assertEqual(code, 3, checked)
                self.assertIn(expected, checked["partial_reasons"])

    def test_enqueue_rate_limit_is_distinct_and_verifies_without_retry(self):
        for stage in ("capabilities", "read"):
            with self.subTest(stage=stage):
                self.path = self.path.parent / ("rate-limit-" + stage)
                self.client = fixtures.PerfClient(self.clock)
                self.run_perf(["start", "--session", SID])
                original, attempts = self.client.call_receipt, []
                def response(target, action, params):
                    if action == stage or action == "perf_capture" and params["op"] == stage:
                        attempts.append(params)
                        raise ott.HTTPError(429)
                    return original(target, action, params)
                self.client.call_receipt = response
                code, live = self.capture(start=False)
                self.assertEqual((code, len(attempts)), (3, 1))
                self.assertNotIn("transport_error", json.dumps(live))
                code, checked = self.invoke()
                self.assertEqual(code, 3, checked)
                self.assertIn("rate_limited", checked["partial_reasons"])

    def test_changed_or_regressed_boundary_retains_partial_evidence(self):
        for changed in ("identity", "clock"):
            with self.subTest(changed=changed):
                self.client = fixtures.PerfClient(self.clock)
                self.run_perf(["start", "--session", SID])
                self.clock.sleep(2)
                original = self.client.call_receipt
                def response(target, action, params):
                    receipt = original(target, action, params)
                    if action == "perf_capture" and params["op"] == "read":
                        if changed == "identity":
                            receipt["data"]["session"]["started_at_unix_ms"] += 1
                        else:
                            receipt["data"]["session"]["elapsed_ms"] -= 1000
                    return receipt
                self.client.call_receipt = response
                code, _ = self.capture(start=False)
                self.assertEqual(code, 3)
                code, checked = self.invoke()
                self.assertEqual((code, checked["valid"], checked["capture_complete"]), (3, True, False))

    def test_hash_agreement_cannot_hide_missing_events_or_false_completeness(self):
        self.capture()
        original = copy.deepcopy(self.records)
        mutations = [
            lambda r: r["result.json"]["capture"].update(events=[]),
            lambda r: r["result.json"]["capture"].update(highwater=0),
            lambda r: r["manifest.json"].update(capture_complete=False),
            lambda r: r["result.json"]["capture"]["events"][0].update(at_ms=999999),
            lambda r: r["result.json"]["capture"].update(partial_reasons=["missing_tail"]),
            lambda r: r["result.json"]["capture"]["last_session"].update(started_at_unix_ms=1),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.records = copy.deepcopy(original)
                mutate(self.records)
                self.persist(rehash=True)
                code, checked = self.invoke()
                self.assertEqual((code, checked["valid"], checked["integrity"]), (1, False, "verified"))

    def test_false_partial_page_limit_and_nonmonotonic_events_are_rejected(self):
        self.capture()
        self.records["result.json"]["capture"].update(complete=False, partial_reasons=["page_limit"])
        self.records["manifest.json"]["capture_complete"] = False
        self.persist(rehash=True)
        self.assertEqual(self.invoke()[0], 1)

    def test_digest_covers_all_request_files(self):
        self.capture()
        self.records["request-002.json"]["params"]["after_seq"] = 1
        self.persist()
        self.assertEqual(self.invoke()[1]["reason"], "digest_mismatch")
        self.persist(rehash=True)
        self.assertEqual(self.invoke()[1]["reason"], "request_journal_mismatch")

    def test_manifest_paths_duplicates_and_extra_files_are_rejected(self):
        self.capture()
        original = copy.deepcopy(self.records)
        for name in ("../result.json", "/tmp/result.json", "request-096.json", "manifest.json"):
            self.records = copy.deepcopy(original)
            self.records["manifest.json"]["files"][0]["name"] = name
            self.persist()
            self.assertEqual(self.invoke()[0], 1)
        self.records = original
        self.records["manifest.json"]["files"].append(copy.deepcopy(self.records["manifest.json"]["files"][0]))
        self.persist()
        self.assertEqual(self.invoke()[0], 1)
        self.persist(rehash=True)
        (self.path / "unexpected").write_text("unrelated test file")
        self.assertEqual(self.invoke()[1]["reason"], "unexpected_report_files")

    def test_oversized_or_symlinked_request_file_is_never_accepted(self):
        self.capture()
        target = self.path / "request-000.json"
        target.write_bytes(b" " * (16 * 1024 + 1))
        self.assertEqual(self.invoke()[1]["reason"], "report_size_invalid")
        if hasattr(os, "symlink"):
            target.unlink()
            target.symlink_to(self.path / "result.json")
            self.assertEqual(self.invoke()[0], 1)

    def test_private_or_mutating_request_payload_is_rejected_even_after_rehash(self):
        self.capture()
        original = copy.deepcopy(self.records)
        for changes in ({"op": "start"}, {"url": PATH_MARKER}, {"runtime": "different-runtime"}):
            self.records = copy.deepcopy(original)
            self.records["request-002.json"]["params"].update(changes)
            self.records["result.json"]["requests"][2]["params"].update(changes)
            self.persist(rehash=True)
            self.assertEqual(self.invoke()[0], 1)

    def test_missing_capture_does_not_claim_media_failure(self):
        self.capture()
        self.records["result.json"].pop("capture")
        self.records["result.json"]["reason"] = "interrupted"
        self.records["manifest.json"]["capture_complete"] = False
        self.persist(rehash=True)
        code, checked = self.invoke()
        self.assertEqual((code, checked["verdict"]), (3, "unknown"))

    def test_plain_output_explains_incomplete_persistence(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            perf.brief({"output": "/selected/directory", "evidence_error": "evidence_incomplete_preserve_directory"})
        self.assertIn("Evidence error:", output.getvalue())
        self.assertIn("not confirmed complete", output.getvalue())
        self.assertNotIn("Private evidence:", output.getvalue())

    def test_trial_report_remains_explicitly_unsupported(self):
        self.capture()
        self.records["result.json"]["operation"] = "trial"
        self.persist(rehash=True)
        code, checked = self.invoke()
        self.assertEqual((code, checked["reason"]), (1, "unsupported_report"))


if __name__ == "__main__":
    unittest.main()
