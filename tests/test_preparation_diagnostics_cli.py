"""Preparation observations are opt-in, bounded and never initiate media work."""
import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from test_debug_cli import DebugClient
from test_workbench_cli import Client, Clock, RUNTIME, SECRET, ott, wb


ACTION = "playback_preparation_diagnostics"
PRIVATE = "https://private.invalid/stream?token=" + SECRET
PHASES = "idle resolving inspecting awaiting_quality admitting admitted failed cancelled".split()
FAILURES = "resolve_failed inspect_failed worker_unavailable admission_failed quality_save_failed".split()
CAUSES = "unknown timeout transport http_401 http_403 http_404 http_429 http_server hls_invalid_header hls_invalid_utf8 media_open_refused media_read_failed media_container_unavailable media_stream_probe_failed media_no_video inspection_stopped inspection_cancelled inspection_unavailable resource_limit track_identity codec_unsupported".split()


def sample():
    return {"version": 1, "runtime": RUNTIME, "phase": "failed", "attempt_generation": 5,
            "last_failure": "inspect_failed", "cause": "timeout",
            "retry": {"pending": True, "failures": 2, "retry_after_ms": 750}}


class PreparationClient(Client):
    def __init__(self, clock):
        super().__init__(clock, bound=False)
        self.capabilities[ACTION] = {"version": 1}
        self.value = sample()
        self.read_error = None
        self.before = None

    def call_receipt(self, target, action, params):
        if self.before:
            self.before(action)
        if action != ACTION:
            return super().call_receipt(target, action, params)
        self.calls.append((target, action, copy.deepcopy(params), self.timeout))
        if self.read_error:
            raise self.read_error
        return {"request_id": "a" * 32, "status": "ok", "data": copy.deepcopy(self.value)}


class PreparationDiagnosticsCLITest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = PreparationClient(self.clock)

    def execute(self, words=None, json_output=True, client=None):
        client = client or self.client
        previous = client.timeout
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(wb, "time", self.clock), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wb.run(ott, client, "web", words or ["debug", "--preparation"], 20, json_output)
        text = out.getvalue()
        self.assertNotIn(SECRET, text + err.getvalue())
        self.assertNotIn(PRIVATE, text + err.getvalue())
        self.assertEqual(client.timeout, previous)
        return code, json.loads(text) if json_output else text

    def test_fresh_capabilities_then_single_runtime_bound_passive_action(self):
        code, result = self.execute()
        self.assertEqual((code, result["verdict"]), (0, "observed"))
        self.assertTrue(result["read_only"])
        self.assertFalse(result["physical_display_verified"])
        self.assertEqual(result["observations"][0]["data"], sample())
        self.assertEqual([c[1:3] for c in self.client.calls], [
            ("capabilities", {}), (ACTION, {"version": 1, "runtime": RUNTIME})])

    def test_synthetic_wire_fixture_matches_closed_consumer_contract(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "contracts" /
                              "playback-preparation-diagnostics-v1.json").read_text())
        self.assertTrue(fixture["synthetic"])
        self.assertEqual(set(fixture["phases"]), ott.PREPARATION_PHASES)
        self.assertEqual(set(fixture["failures"]), ott.PREPARATION_FAILURES)
        self.assertEqual(set(fixture["causes"]), ott.PREPARATION_CAUSES)
        self.assertEqual(set(fixture["errors"]), ott.PREPARATION_ERRORS)
        self.assertEqual(ott.preparation_metadata(fixture["example"], fixture["request"]), fixture["example"])
        self.assertEqual(fixture["limits"]["receipt_bytes"], ott.MAX_PREPARATION_BYTES)

    def test_capability_is_independent_of_inspect_and_existing_native_markers(self):
        self.client.capabilities.pop("inspect")
        self.client.capabilities["player"]["runtime"] = "Native_One.1"
        self.client.value["runtime"] = "Native_One.1"
        self.assertEqual(self.execute()[0], 0)

    def test_no_marker_or_invalid_marker_never_dispatches_diagnostics(self):
        for marker in (None, {}, True, 1, {"version": True}, {"version": 1.0},
                       {"version": 2}, {"version": 1, "private": PRIVATE}):
            with self.subTest(marker=marker):
                self.client = PreparationClient(self.clock)
                if marker is None:
                    del self.client.capabilities[ACTION]
                else:
                    self.client.capabilities[ACTION] = marker
                code, result = self.execute()
                self.assertEqual((code, result["verdict"]), (3, "unknown"))
                self.assertEqual([c[1] for c in self.client.calls], ["capabilities"])
                self.assertNotIn("data", result["observations"][0])

    def test_default_debug_is_unchanged_and_does_not_read_preparation(self):
        client = DebugClient(self.clock)
        client.capabilities[ACTION] = {"version": 1}
        self.assertEqual(self.execute(["debug"], client=client)[0], 0)
        self.assertEqual([c[1] for c in client.calls], ["capabilities", "inspect"])

    def test_conflicting_modes_and_out_fail_before_any_request(self):
        for flags in (["--engine"], ["--provider"], ["--native-playback"], ["--local-source"],
                      ["--lane", "native"], ["--lane", "auto"], ["--out", "/unused"]):
            with self.subTest(flags=flags):
                code, result = self.execute(["debug", "--preparation", *flags])
                self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))
                self.assertEqual(self.client.calls, [])

    def test_unknown_observations_and_all_closed_codes_remain_distinct(self):
        for phase in PHASES:
            self.client.value.update(phase=phase, attempt_generation=None, last_failure=None, cause=None,
                                     retry={"pending": False, "failures": None, "retry_after_ms": None})
            self.assertEqual(self.execute()[1]["observations"][0]["data"], self.client.value)
        for failure in FAILURES:
            for cause in CAUSES:
                self.client.value.update(phase="failed", last_failure=failure, cause=cause)
                self.assertEqual(self.execute()[0], 0)

    def test_integer_boundaries_and_zero_delay_are_observed_not_unknown(self):
        self.client.value.update(attempt_generation=9007199254740991,
                                 retry={"pending": True, "failures": 4294967295, "retry_after_ms": 30000})
        self.assertEqual(self.execute()[0], 0)
        self.client.value.update(attempt_generation=0, retry={"pending": True, "failures": 0, "retry_after_ms": 0})
        self.assertEqual(self.execute()[0], 0)

    def test_unknown_fields_wrong_types_bounds_and_private_strings_are_suppressed(self):
        mutations = [lambda d: d.update(extra=PRIVATE), lambda d: d.pop("cause"),
                     lambda d: d.update(runtime="another-runtime"), lambda d: d.update(runtime=PRIVATE),
                     lambda d: d.update(phase=PRIVATE), lambda d: d.update(cause=PRIVATE),
                     lambda d: d.update(last_failure="new_failure"), lambda d: d.update(version=True),
                     lambda d: d.update(version=1.0), lambda d: d.update(version=2)]
        for value in (-1, True, 1.0, 9007199254740992):
            mutations.append(lambda d, value=value: d.update(attempt_generation=value))
        for key, values in (("failures", (-1, True, 1.0, 4294967296, None)),
                            ("retry_after_ms", (-1, True, 1.0, 30001, None)),
                            ("pending", (None, 1, "true"))):
            for value in values:
                mutations.append(lambda d, key=key, value=value: d["retry"].update({key: value}))
        mutations.extend([lambda d: d["retry"].update(pending=False),
                          lambda d: d["retry"].update(extra=PRIVATE), lambda d: d.update(retry=None)])
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.client.value = sample()
                mutate(self.client.value)
                code, result = self.execute()
                self.assertEqual((code, result["observations"][0]["reason"]), (3, "invalid_response"))
                self.assertNotIn("data", result["observations"][0])

    def test_shared_deadline_never_dispatches_after_capability_budget_exhausted(self):
        self.client.before = lambda action: self.clock.sleep(21) if action == "capabilities" else None
        self.assertEqual(self.execute()[0], 3)
        self.assertEqual([c[1] for c in self.client.calls], ["capabilities"])
        self.client = PreparationClient(self.clock)
        self.client.before = lambda action: self.clock.sleep(7) if action == "capabilities" else None
        self.assertEqual(self.execute()[0], 0)
        self.assertEqual([c[3] for c in self.client.calls], [20, 13])

    def test_rejections_are_closed_bound_and_never_retried(self):
        for reason in ("invalid_request", "runtime_mismatch", "unavailable", "unsupported", "policy_denied"):
            self.client = PreparationClient(self.clock)
            self.client.read_error = ott.PlayerRejected(SECRET, {"version": 1, "runtime": RUNTIME, "error": reason})
            code, result = self.execute()
            self.assertEqual((code, result["observations"][0]["reason"]), (3, reason))
            self.assertEqual([c[1] for c in self.client.calls], ["capabilities", ACTION])
        self.client.read_error = ott.PlayerRejected(SECRET, {"version": 1, "runtime": "new-runtime", "error": "runtime_mismatch"})
        self.assertEqual(self.execute()[1]["observations"][0]["reason"], "runtime_mismatch")
        for data in ({"error": PRIVATE}, {"version": 1, "runtime": RUNTIME, "error": "policy_denied", "extra": PRIVATE},
                     {"version": 1, "runtime": "new-runtime", "error": "policy_denied"}):
            self.client.read_error = ott.PlayerRejected(SECRET, data)
            self.assertEqual(self.execute()[1]["observations"][0]["reason"], "invalid_response")

    def test_human_output_labels_preparation_and_retry_without_claiming_display(self):
        code, text = self.execute(json_output=False)
        self.assertEqual(code, 0)
        for word in ("failed", "inspect_failed", "timeout", "750"):
            self.assertIn(word, text)
        self.assertNotIn("playback confirmed", text.lower())


class PreparationRawReceiptTest(unittest.TestCase):
    def call(self, raw, params=None):
        client = ott.Client.__new__(ott.Client)
        client.timeout = 20
        client.api = mock.Mock(return_value=(202, {"id": "a" * 32}))
        client.api_raw = mock.Mock(return_value=(200, raw))
        self.client = client
        with mock.patch.object(ott.time, "sleep"):
            return client.call_receipt("web", ACTION, params or {"version": 1, "runtime": RUNTIME})

    def wire(self, status="ok", data=None):
        return json.dumps({"id": "a" * 32, "status": status, "data": sample() if data is None else data},
                          separators=(",", ":")).encode()

    def test_byte_limit_counts_full_raw_receipt_including_whitespace(self):
        raw = self.wire()
        padded = raw + b" " * (4096 - len(raw))
        self.assertEqual(self.call(padded)["data"], sample())
        with self.assertRaises(ott.RequestError) as caught:
            self.call(padded + b" ")
        self.assertEqual((caught.exception.reason, caught.exception.request_id), ("invalid_response", "a" * 32))
        self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))

    def test_duplicates_nonfinite_extra_keys_and_wrong_receipt_id_are_rejected_before_compaction(self):
        raw = self.wire()
        variants = [raw.replace(b'"status":"ok"', b'"status":"ok","status":"ok"'),
                    raw.replace(b'"pending":true', b'"pending":true,"pending":true'),
                    raw.replace(b'"attempt_generation":5', b'"attempt_generation":NaN'),
                    raw.replace(b'"attempt_generation":5', b'"attempt_generation":5.0'),
                    raw.replace(b'"id":"' + b'a' * 32, b'"id":"' + b'b' * 32),
                    raw[:-1] + b',"url":"private"}',
                    self.wire(data={**sample(), "url": PRIVATE}),
                    self.wire(status="unsupported", data={"version": 1, "runtime": RUNTIME, "error": "unsupported"})]
        for index, value in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ott.RequestError) as caught:
                self.call(value)
            self.assertEqual(caught.exception.request_id, "a" * 32)
            self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))

    def test_closed_rejected_receipt_preserves_its_identity_without_a_second_post(self):
        for reason in ("policy_denied", "runtime_mismatch", "unsupported"):
            data = {"version": 1, "runtime": "new-runtime" if reason == "runtime_mismatch" else RUNTIME, "error": reason}
            with self.subTest(reason=reason), self.assertRaises(ott.PlayerRejected) as caught:
                self.call(self.wire("rejected", data))
            self.assertEqual((caught.exception.request_id, caught.exception.data), ("a" * 32, data))
            self.assertEqual((self.client.api.call_count, self.client.api_raw.call_count), (1, 1))

    def test_invalid_request_is_rejected_before_enqueue(self):
        for params in ({"version": True, "runtime": RUNTIME}, {"version": 1, "runtime": PRIVATE},
                       {"version": 1, "runtime": RUNTIME, "probe": True}):
            with self.subTest(params=params), self.assertRaises(ott.Error):
                self.call(self.wire(), params)
            self.client.api.assert_not_called()
            self.client.api_raw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
