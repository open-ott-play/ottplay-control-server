"""Synthetic frozen wire and fake-HTTP checks; no files or player state changed."""

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    spec = importlib.util.spec_from_file_location("global_recovery_test", ROOT / "cli/global_favorites_recovery.py")
    recovery = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = recovery
    spec.loader.exec_module(recovery)
FIX = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.fixtures.json").read_bytes())
POS = FIX["positive"]
RUNTIME = POS["inspect_request"]["runtime"]
OPERATION = POS["inspect_request"]["operation_id"]
RID = "a" * 32


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def checked(request, data, **kwargs):
    return recovery.validate_result(request, data, request_id=RID, response_id=RID, status="ok", **kwargs)


def reseal(request):
    values = [request["runtime"], "global", request["operation_id"], request["operation_runtime"],
              request["original_entry_sha256"], request["expected_ledger_revision"], request["expected_store_revision"],
              request["expected_context_revision"], request["backend_proof"], request["reason"]]
    request["acknowledgement_id"] = hashlib.sha256(raw(["ottplay-favorites-global-recovery-ack-v1", *values])).hexdigest()
    request["plan_sha256"] = hashlib.sha256(raw(["ottplay-favorites-global-recovery-plan-v1", *values,
                                               request["acknowledgement_id"], request["after_ledger_sha256"]])).hexdigest()
    return request


class ProtocolTests(unittest.TestCase):
    def error(self, function, *args, **kwargs):
        with self.assertRaises(recovery.ProtocolError) as caught:
            function(*args, **kwargs)
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_frozen_shapes_and_digest_vectors(self):
        pairs = (("page_request", "page_response"), ("inspect_request", "inspect_response"),
                 ("acknowledge_request", "acknowledge_response"), ("page_request", "empty_page_response"),
                 ("inspect_request", "missing_operation_response"), ("page_request", "first_page"),
                 ("last_page_request", "last_page"))
        schema = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.schema.json").read_bytes())["$defs"]
        for request, response in pairs:
            with self.subTest(response=response):
                result = checked(POS[request], POS[response])
                self.assertEqual(result.data, POS[response])
                self.assertFalse(result.verified)
                validated = recovery.validate_response(POS[request], raw({"id": RID, "status": "ok", "data": POS[response]}), request_id=RID)
                self.assertEqual(validated.data, result.data)
        for definition, key in (("inspect_page_request", "page_request"), ("inspect_operation_request", "inspect_request"),
                                ("acknowledge_request", "acknowledge_request"), ("inspect_page_response", "page_response"),
                                ("inspect_operation_response", "inspect_response"), ("acknowledge_response", "acknowledge_response")):
            self.assertEqual(set(schema[definition]["required"]), set(POS[key]))
        self.assertEqual(recovery.recovery_ack_request(POS["inspect_response"]), POS["acknowledge_request"])
        self.assertEqual(hashlib.sha256(raw(FIX["ack_tuple"])).hexdigest(), POS["acknowledge_request"]["acknowledgement_id"])
        self.assertEqual(hashlib.sha256(raw(FIX["plan_tuple"])).hexdigest(), POS["acknowledge_request"]["plan_sha256"])
        self.assertEqual(hashlib.sha256(FIX["private_after_utf8"].encode()).hexdigest(), POS["acknowledge_request"]["after_ledger_sha256"])

    def test_exact_marker_and_literal_global_no_scope_or_keys(self):
        self.assertEqual(recovery.validate_recovery_capability({"version": 1}), {"version": 1})
        for value in (None, {"version": True}, {"version": 2}, {"version": 1, "key": "private"}):
            self.error(recovery.validate_recovery_capability, value)
        for name in ("page_request", "inspect_request", "acknowledge_request"):
            for change in ({"subject": "plex"}, {"scope": {}}, {"key": "fixture-secret"}, {"op": "clear"}, {"runtime": "X"}):
                self.error(recovery.validate_request, {**POS[name], **change})
        for value in (1, False, "true", None):
            self.error(recovery.validate_request, {**POS["acknowledge_request"], "accept_unknown": value})
        self.error(recovery.validate_request, {**POS["page_request"], "operation_id": OPERATION})

    def test_strict_original_json_and_limits(self):
        request = raw(POS["inspect_request"])
        for bad in (request.replace(b'"inspect"', b'"inspect","op":"inspect"'),
                    request.replace(RUNTIME.encode(), b'\\ud800'),
                    request.replace(RUNTIME.encode(), b'bad\\n'),
                    request[:-1] + b',"extra":1e400}', b'\xef\xbb\xbf' + request,
                    request[:-1] + b',"extra":"\xff"}', request + b' ' * 16384):
            self.error(recovery.validate_request, bad)
        payload = raw(POS["inspect_response"])
        padded = b'{"id":"' + RID.encode() + b'","status":"ok","data":{' + b' ' * 20 + payload[1:] + b'}'
        value = recovery.validate_response(POS["inspect_request"], padded, request_id=RID, max_metadata_bytes=len(payload) + 20)
        self.assertFalse(value.verified)
        self.error(recovery.validate_response, POS["inspect_request"], padded, request_id=RID, max_metadata_bytes=len(payload) + 19)
        for value in (True, 0, 131073):
            self.error(recovery.validate_response, POS["inspect_request"], padded, request_id=RID, max_metadata_bytes=value)
        bad = raw({"id": RID, "status": "ok", "data": POS["inspect_response"]}).replace(b'"version":1', b'"version":1,"version":1')
        self.error(recovery.validate_response, POS["inspect_request"], bad, request_id=RID)

    def test_errors_exact_bounded_and_never_verified(self):
        for error in recovery.ERRORS:
            data = {"version": 1, "runtime": RUNTIME, "op": "inspect", "subject": "global", "error": error}
            result = recovery.validate_result(POS["inspect_request"], data, request_id=RID, response_id=RID, status="rejected")
            self.assertFalse(result.verified)
        data["error"] = "fixture-secret://token"
        self.error(recovery.validate_result, POS["inspect_request"], data, request_id=RID, response_id=RID, status="rejected")
        data["error"] = "busy"
        encoded = raw(data)
        envelope = b'{"data":{' + b' ' * 30 + encoded[1:] + b',"id":"' + RID.encode() + b'","status":"rejected"}'
        self.error(recovery.validate_response, POS["inspect_request"], envelope, request_id=RID, max_metadata_bytes=len(encoded))
        self.error(checked, POS["inspect_request"], data)

    def test_correlation_and_no_page_authority(self):
        for key, value in (("operation_id", "22222222-2222-4222-8222-222222222222"), ("runtime", "fresh"),
                           ("subject", "scoped"), ("op", "acknowledge"), ("version", True)):
            self.error(checked, POS["inspect_request"], {**POS["inspect_response"], key: value})
        self.error(recovery.validate_result, POS["inspect_request"], POS["inspect_response"], request_id=RID,
                   response_id="b" * 32, status="ok")
        self.error(recovery.recovery_ack_request, POS["page_response"])
        self.error(recovery.recovery_ack_request, POS["missing_operation_response"])
        self.error(recovery.recovery_ack_request, POS["later_runtime_observation"])

    def test_eligibility_and_absent_physical_values(self):
        empty = POS["empty_page_response"]
        self.assertEqual(checked(POS["page_request"], empty).data["ledger_revision"], recovery.EMPTY_LEDGER_SHA256)
        for change in ({"backend_proof": None}, {"record": None}, {"acknowledgement_id": None},
                       {"after_ledger_sha256": None}, {"plan_sha256": None}, {"acknowledge_allowed": 1},
                       {"ledger_revision": recovery.EMPTY_LEDGER_SHA256}):
            self.error(checked, POS["inspect_request"], {**POS["inspect_response"], **change})
        no_backend = {**POS["inspect_response"], "backend_proof": None, "acknowledge_allowed": False,
                      "acknowledgement_id": None, "after_ledger_sha256": None, "plan_sha256": None}
        checked(POS["inspect_request"], no_backend)
        self.error(recovery.recovery_ack_request, no_backend)
        request = reseal({**POS["acknowledge_request"], "expected_store_revision": recovery.EMPTY_LEDGER_SHA256})
        recovery.validate_request(request)
        for before, after in ((request["expected_ledger_revision"], recovery.EMPTY_LEDGER_SHA256),
                              (recovery.EMPTY_LEDGER_SHA256, request["after_ledger_sha256"]),
                              (request["expected_ledger_revision"], request["expected_ledger_revision"])):
            invalid = reseal({**request, "expected_ledger_revision": before, "after_ledger_sha256": after})
            self.error(recovery.validate_request, invalid)
            inspect = {**POS["inspect_response"], "ledger_revision": before, "store_revision": request["expected_store_revision"],
                       "after_ledger_sha256": after, "acknowledgement_id": invalid["acknowledgement_id"], "plan_sha256": invalid["plan_sha256"]}
            self.error(checked, POS["inspect_request"], inspect)

    def test_every_digest_component_is_bound(self):
        request = POS["acknowledge_request"]
        for key in ("original_entry_sha256", "expected_ledger_revision", "expected_store_revision", "expected_context_revision",
                    "backend_proof", "acknowledgement_id", "after_ledger_sha256", "plan_sha256"):
            self.error(recovery.validate_request, {**request, key: "f" * 64})
        for key, value in (("runtime", "fresh"), ("operation_runtime", "different"),
                           ("operation_id", "22222222-2222-4222-8222-222222222222"), ("reason", "fixture-secret")):
            self.error(recovery.validate_request, {**request, key: value})

    def test_historical_audit_new_runtime_and_outcome_immutability(self):
        observed = POS["later_runtime_observation"]
        req = {**POS["inspect_request"], "runtime": observed["runtime"]}
        checked(req, observed)
        for key in ("runtime", "ledger_revision_before", "store_revision", "context_revision", "backend_proof"):
            data = copy.deepcopy(observed)
            data["record"]["acknowledgement"][key] = "fresh" if key == "runtime" else "f" * 64
            self.error(checked, req, data)
        data = copy.deepcopy(observed)
        audit = data["record"]["acknowledgement"]
        invalid = reseal({**POS["acknowledge_request"], "expected_ledger_revision": recovery.EMPTY_LEDGER_SHA256})
        audit.update(ledger_revision_before=recovery.EMPTY_LEDGER_SHA256, acknowledgement_id=invalid["acknowledgement_id"])
        self.error(checked, req, data)
        for state, outcome in (("pending", "verified"), ("partial", "unknown"), ("unknown", "partial")):
            data = copy.deepcopy(POS["inspect_response"])
            data["record"].update(original_state=state, original_outcome=outcome)
            self.error(checked, POS["inspect_request"], data)

    def test_ack_exact_after_audit_and_original_record(self):
        checked(POS["acknowledge_request"], POS["acknowledge_response"], context=POS["inspect_response"])
        for key in ("ledger_revision", "store_revision", "context_revision", "acknowledgement_id", "plan_sha256"):
            self.error(checked, POS["acknowledge_request"], {**POS["acknowledge_response"], key: "f" * 64})
        self.error(checked, POS["acknowledge_request"], {**POS["acknowledge_response"], "acknowledged": 1})
        changed = copy.deepcopy(POS["acknowledge_response"])
        changed["record"].update(original_state="partial", original_outcome="partial")
        self.error(checked, POS["acknowledge_request"], changed, context=POS["inspect_response"])
        changed = copy.deepcopy(POS["acknowledge_response"])
        changed["record"]["acknowledgement"] = None
        self.error(checked, POS["acknowledge_request"], changed)

    def test_page_counts_boundaries_sorting_and_revision_cursor(self):
        for total in (True, -1, 33):
            self.error(checked, POS["page_request"], {**POS["page_response"], "total": total})
        for rows in ([], POS["first_page"]["records"] * 2, list(reversed(POS["first_page"]["records"]))):
            self.error(checked, POS["page_request"], {**POS["first_page"], "records": rows})
        self.error(checked, POS["page_request"], {**POS["first_page"], "next_cursor": None})
        for key in recovery.REVISIONS:
            self.error(checked, POS["last_page_request"], {**POS["last_page"], key: "f" * 64})
        cursor = {**POS["last_page_request"]["cursor"], "after_operation_id": "ffffffff-ffff-4fff-8fff-ffffffffffff"}
        self.error(checked, {**POS["last_page_request"], "cursor": cursor}, POS["last_page"])
        data = copy.deepcopy(POS["first_page"])
        data["next_cursor"]["after_operation_id"] = OPERATION
        self.error(checked, POS["page_request"], data)


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, duration):
        self.now += duration


class API:
    server = "https://synthetic.invalid"
    def __init__(self, clock):
        self.clock = clock
        self.calls, self.requests = [], {}
        self.pages = [copy.deepcopy(POS["page_response"])]
        self.inspection = copy.deepcopy(POS["inspect_response"])
        self.ack_mode = "success"
        self.acked = False
        self.after = copy.deepcopy(POS["later_runtime_observation"])
        self.after["runtime"] = RUNTIME
        self.pending_once = False
        self.raw_padding = False
    def api(self, path, payload, *, timeout):
        self.calls.append((self.clock(), "POST", copy.deepcopy(payload), timeout))
        self.requests[f"{len(self.requests) + 1:032x}"] = copy.deepcopy(payload["params"])
        identifier = next(reversed(self.requests))
        if payload["params"]["op"] == "acknowledge":
            self.acked = self.ack_mode not in ("lost_no_write", "busy")
            if self.ack_mode.startswith("lost"):
                raise OSError("fixture-secret://token")
        return 202, {"id": identifier, "status": "queued"}
    def api_raw(self, path, payload, *, timeout):
        self.calls.append((self.clock(), "GET", None, timeout))
        identifier = path.rsplit("id=", 1)[1]
        request = self.requests[identifier]
        if self.pending_once:
            self.pending_once = False
            return 202, b'{"status":"pending"}'
        status = "ok"
        if request["op"] == "acknowledge":
            if self.ack_mode == "pending":
                return 202, b'{"status":"pending"}'
            if self.ack_mode == "busy":
                status = "rejected"
                data = {"version": 1, "runtime": RUNTIME, "op": "acknowledge", "subject": "global", "error": "busy"}
            else:
                data = copy.deepcopy(POS["acknowledge_response"])
                if self.ack_mode == "malformed":
                    data["plan_sha256"] = "f" * 64
        elif "cursor" in request:
            data = copy.deepcopy(self.pages[1 if request["cursor"] else 0])
        else:
            data = copy.deepcopy(self.after if self.acked else self.inspection)
        result = raw({"id": identifier, "status": status, "data": data})
        if self.raw_padding:
            result = result.replace(b'"data":{', b'"data":{' + b' ' * 131072)
        return 200, result
    def ops(self):
        return [call[2]["params"]["op"] for call in self.calls if call[1] == "POST"]


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.api = API(self.clock)
        self.peer = recovery.RecoveryPeer(self.api, "fixture-device", runtime=RUNTIME,
                                          monotonic=self.clock, sleep=self.clock.sleep)
    def ack(self, save=lambda *_: True):
        return self.peer.acknowledge(POS["inspect_response"], POS["inspect_response"]["plan_sha256"], save)
    def test_complete_single_two_empty_and_32_record_lists(self):
        self.assertEqual(self.peer.list()["records"], POS["page_response"]["records"])
        self.api.pages = [copy.deepcopy(POS["first_page"]), copy.deepcopy(POS["last_page"])]
        result = self.peer.list()
        self.assertEqual((result["status"], result["total"], len(result["records"])), ("complete", 17, 17))
        first, last = copy.deepcopy(self.api.pages)
        first["total"] = last["total"] = 32
        last["records"] = [{**last["records"][0], "operation_id": f"{i:08x}-1111-4111-8111-111111111111"} for i in range(17, 33)]
        self.api.pages = [first, last]
        self.assertEqual(len(self.peer.list()["records"]), 32)
        self.api.pages = [copy.deepcopy(POS["empty_page_response"])]
        self.assertEqual(self.peer.list()["records"], [])
        self.assertTrue(all(call[2]["action"] == recovery.ACTION for call in self.api.calls if call[1] == "POST"))
    def test_incomplete_or_drifting_list_never_returns_partial(self):
        self.api.pages = [copy.deepcopy(POS["first_page"]), copy.deepcopy(POS["last_page"])]
        self.api.pages[1]["total"] = 18
        with self.assertRaises(recovery.transport.RemoteError):
            self.peer.list()
        self.assertEqual(self.api.ops(), ["inspect", "inspect"])
    def test_list_drift_each_physical_context_component(self):
        for key in recovery.REVISIONS:
            with self.subTest(key=key):
                self.setUp()
                self.api.pages = [copy.deepcopy(POS["first_page"]), copy.deepcopy(POS["last_page"])]
                self.api.pages[1][key] = "f" * 64
                with self.assertRaises(recovery.transport.RemoteError):
                    self.peer.list()
                self.assertEqual(self.api.ops(), ["inspect", "inspect"])
    def test_inspection_is_global_and_side_effect_free(self):
        self.assertEqual(self.peer.inspect(OPERATION), POS["inspect_response"])
        self.assertEqual(self.api.ops(), ["inspect"])
        self.assertFalse(self.api.acked)
    def test_confirmation_and_runtime_required_before_save_or_post(self):
        save = mock.Mock(return_value=True)
        for confirmation in (None, True, "wrong"):
            with self.assertRaises(recovery.transport.RemoteError):
                self.peer.acknowledge(POS["inspect_response"], confirmation, save)
        foreign = copy.deepcopy(POS["inspect_response"])
        foreign["runtime"] = "fresh"
        with self.assertRaises(recovery.transport.RemoteError):
            self.peer.acknowledge(foreign, foreign["plan_sha256"], save)
        save.assert_not_called()
        self.assertEqual(self.api.calls, [])
    def test_save_precedes_post_and_callback_cannot_mutate_retained_intent(self):
        saved = []
        def save(inspection, request):
            self.assertEqual(self.api.calls, [])
            saved.append(copy.deepcopy(request))
            request["operation_id"] = "fixture-secret"
            inspection["record"]["original_outcome"] = "verified"
            return True
        result = self.ack(save)
        self.assertEqual(result["status"], "acknowledgement_confirmed")
        self.assertEqual(result["original_outcome"], "unknown")
        self.assertFalse(result["replay_allowed"])
        self.assertEqual(saved[0], POS["acknowledge_request"])
        self.assertEqual(self.api.ops(), ["acknowledge"])
        self.assertEqual(self.api.calls[0][2]["params"], saved[0])
        self.assertEqual(self.ack()["reason"], "already_started")
        self.assertEqual(self.api.ops(), ["acknowledge"])
    def test_already_retained_identical_intent_can_follow_new_explicit_confirmation(self):
        self.assertEqual(self.ack(lambda *_: False)["status"], "acknowledgement_confirmed")
        self.assertEqual(self.api.ops(), ["acknowledge"])
    def test_save_failure_nonbool_and_deadline_refuse_post(self):
        for value in (None, 1, "true"):
            self.assertEqual(self.ack(lambda *_, result=value: result)["reason"], "context_not_saved")
        def fail(*_):
            raise OSError("fixture-secret")
        self.assertEqual(self.ack(fail)["reason"], "context_not_saved")
        def late(*_):
            self.clock.now = 36
            return True
        self.assertEqual(self.ack(late)["status"], "blocked")
        self.assertEqual(self.api.calls, [])
    def test_short_deadline_before_save(self):
        self.peer = recovery.RecoveryPeer(self.api, "fixture-device", runtime=RUNTIME, timeout=11,
                                          monotonic=self.clock, sleep=self.clock.sleep)
        save = mock.Mock(return_value=True)
        self.assertEqual(self.ack(save)["reason"], "deadline_insufficient")
        save.assert_not_called()
        self.assertEqual(self.api.calls, [])
    def test_lost_post_reply_observes_exact_audit_once_no_ack_retry(self):
        self.api.ack_mode = "lost_written"
        result = self.ack()
        self.assertEqual(result["status"], "acknowledgement_observed")
        self.assertEqual(self.api.ops(), ["acknowledge", "inspect"])
        self.assertFalse(result["replay_allowed"])
    def test_lost_no_write_stays_unknown_without_retry(self):
        self.api.ack_mode = "lost_no_write"
        self.assertEqual(self.ack()["status"], "unknown")
        self.assertEqual(self.api.ops(), ["acknowledge", "inspect"])
    def test_invalid_ack_and_pending_deadline_read_back_exact_audit(self):
        for mode in ("malformed", "pending"):
            with self.subTest(mode=mode):
                self.setUp()
                self.api.ack_mode = mode
                self.assertEqual(self.ack()["status"], "acknowledgement_observed")
                self.assertEqual(self.api.ops(), ["acknowledge", "inspect"])
                self.assertLess(self.clock.now, 45)
    def test_other_valid_audit_does_not_confirm_saved_intent(self):
        request = reseal({**POS["acknowledge_request"], "runtime": "earlier"})
        self.api.after["record"]["acknowledgement"] = recovery.recovery_audit(request)
        self.api.ack_mode = "lost_written"
        self.assertEqual(self.ack()["status"], "unknown")
        self.assertEqual(self.api.ops(), ["acknowledge", "inspect"])
    def test_fresh_runtime_can_observe_but_not_reuse_old_plan(self):
        self.peer = recovery.RecoveryPeer(self.api, "fixture-device", runtime="fresh", monotonic=self.clock, sleep=self.clock.sleep)
        self.api.acked = True
        self.api.after["runtime"] = "fresh"
        self.assertEqual(self.peer.inspect(OPERATION)["record"]["acknowledgement"]["runtime"], RUNTIME)
        with self.assertRaises(recovery.transport.RemoteError):
            self.ack()
        self.assertEqual(self.api.ops(), ["inspect"])
    def test_http_pacing_raw_get_and_bounded_error_redaction(self):
        self.api.pending_once = True
        self.peer.inspect(OPERATION)
        self.peer.list()
        for previous, current in zip(self.api.calls, self.api.calls[1:]):
            self.assertGreaterEqual(current[0] - previous[0], recovery.transport.HTTP_INTERVAL)
        posts = [call for call in self.api.calls if call[1] == "POST"]
        self.assertGreaterEqual(posts[1][0] - posts[0][0], recovery.transport.POST_INTERVAL)
        self.api.raw_padding = True
        with self.assertRaises(recovery.transport.RemoteError) as caught:
            self.peer.inspect(OPERATION)
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertEqual(caught.exception.code, "invalid_response")
    def test_server_drift_before_ack_no_post_and_unknown_is_honest(self):
        def save(*_):
            self.api.server = "https://different.invalid"
            return True
        self.assertEqual(self.ack(save)["status"], "unknown")
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
