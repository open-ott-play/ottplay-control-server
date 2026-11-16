"""Ingress metadata stays passive; only exact bounded request IDs establish correlation."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from test_workbench_cli import Clock, capabilities, ott

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("request_audit_tests", ROOT / "cli" / "request_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
FIXTURE = json.loads((ROOT / "contracts" / "request-audit-v1.json").read_text())
NATIVE = json.loads((ROOT / "contracts" / "command-activity-v2.json").read_text())
DEVICE = FIXTURE["example"]["device_id"]
RUNTIME = NATIVE["request"]["runtime"]
IDENTITY = FIXTURE["example"]["entries"][0]["request_id"]
PRIVATE = "https://private.invalid/source?token=secret-audit-test"


def sample(selected=False):
    value = copy.deepcopy(FIXTURE["example"])
    if not selected:
        value["lookup"] = "not_requested"
    return value


class AuditClient(ott.Client):
    def __init__(self, clock=None):
        self.clock = clock or Clock()
        self.timeout = 45
        self.server = "https://controller.invalid"
        self.on_receipt = None
        self.calls, self.children = [], []
        self.config = {"players": {"l": DEVICE, "l2": "second"}, "groups": {"ll": ["l", "l2"]}}
        self.credentials = {"devices": [{"id": DEVICE}, {"id": "second"}]}
        self.caps = capabilities()
        self.caps["player"]["runtime"] = RUNTIME
        self.caps[audit.ACTION] = {"version": 2}
        self.caps["native_playback_diagnostics"] = {"version": 1}
        self.activity = copy.deepcopy(NATIVE["example"])
        self.audit = sample()
        self.before = None
        self.barrier = None

    def call_receipt(self, target, action, params):
        self.calls.append(("rpc", target, action, copy.deepcopy(params), self.timeout))
        if self.before:
            self.before(self, action)
        if action == "capabilities":
            self.assert_passive(params == {})
            value = copy.deepcopy(self.caps)
        else:
            self.assert_passive(action == audit.ACTION and params == {"version": 2, "runtime": RUNTIME})
            value = copy.deepcopy(self.activity)
        return {"request_id": "3" * 32, "status": "ok", "data": value}

    def assert_passive(self, value):
        if not value:
            raise AssertionError("Unexpected player action")

    def api_raw(self, path, payload=None, timeout=None):
        self.calls.append(("get", path, payload, timeout))
        self.assert_passive(path.startswith("/api/request-audit?device_id=") and payload is None)
        if self.before:
            self.before(self, "get")
        if self.barrier:
            self.barrier.wait(timeout=5)
        value = copy.deepcopy(self.audit)
        value["device_id"] = self.target if hasattr(self, "target") else DEVICE
        return 200, json.dumps(value).encode()

    def for_group(self, cancelled):
        child = AuditClient()
        child.group_cancelled = cancelled
        child.barrier = self.barrier
        child.target = DEVICE if not self.children else "second"
        self.children.append(child)
        return child


class RequestAuditCLITest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = AuditClient(self.clock)

    def execute(self, words=(), *, client=None, json_output=True, timeout=20):
        output = io.StringIO()
        client = client or self.client
        previous = client.timeout
        with contextlib.redirect_stdout(output), mock.patch.object(audit, "time", self.clock):
            code = audit.run(ott, client, DEVICE, list(words), timeout, json_output)
        text = output.getvalue()
        self.assertNotIn(PRIVATE, text)
        self.assertEqual(client.timeout, previous)
        return code, json.loads(text) if json_output else text

    def test_fixtures_pin_all_permissions_and_native_enums(self):
        self.assertEqual(audit.PERMISSIONS, frozenset(FIXTURE["permissions"]))
        self.assertEqual(audit.FAMILIES, frozenset(FIXTURE["tracked_action_families"]))
        self.assertEqual(audit.FAMILIES, frozenset(NATIVE["actions"]))
        self.assertEqual(audit.ERRORS, frozenset(NATIVE["errors"]))
        self.assertEqual(audit.audit_metadata(sample(True), DEVICE, IDENTITY), sample(True))
        self.assertEqual(audit.activity_metadata(NATIVE["example"], NATIVE["request"]), NATIVE["example"])
        self.assertEqual(audit.activity_metadata(NATIVE["legacy_example"], NATIVE["request"]), NATIVE["legacy_example"])
        self.assertEqual(FIXTURE["limits"]["max_response_bytes"], audit.MAX_AUDIT_BYTES)
        self.assertEqual(NATIVE["limits"]["receipt_bytes"], audit.MAX_ACTIVITY_BYTES)

    def test_plain_audit_and_id_lookup_are_one_get_and_no_player_rpc(self):
        code, result = self.execute()
        self.assertEqual((code, result["verdict"]), (0, "observed"))
        self.assertEqual(result["activity"], {"status": "not_requested"})
        self.assertTrue(result["read_only"])
        self.assertFalse(result["physical_display_verified"])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][0], "get")
        self.client.calls.clear()
        self.client.audit = sample(True)
        self.assertEqual(self.execute([IDENTITY])[0], 0)
        self.assertIn("&id=" + IDENTITY, self.client.calls[0][1])
        self.assertEqual(len(self.client.calls), 1)

    def test_activity_is_opt_in_caps_then_one_passive_rpc_then_one_get(self):
        code, result = self.execute(["--activity"])
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in self.client.calls], ["rpc", "rpc", "get"])
        self.assertEqual([c[2] for c in self.client.calls[:2]], ["capabilities", audit.ACTION])
        self.assertEqual(result["correlations"], [{"sequence": 1, "request_id": IDENTITY,
            "action": "plex_library", "status": "matched", "initiator": {"credential_role": "admin", "operator_id": None}}])
        self.assertNotIn("runtime", result["controller"]["data"])

    def test_old_player_stays_unknown_without_sending_activity_action(self):
        del self.client.caps[audit.ACTION]
        code, result = self.execute(["--activity"])
        self.assertEqual(code, 3)
        self.assertEqual(result["activity"], {"status": "unknown", "reason": "unsupported"})
        self.assertEqual([c[0] for c in self.client.calls], ["rpc", "get"])
        self.assertEqual(result["correlations"], [])

    def test_missing_request_id_ring_loss_and_action_mismatch_stay_unknown(self):
        for change, reason in ((lambda c: c.activity["entries"][0].update(request_id=None), "request_id_not_reported"),
                               (lambda c: c.audit.update(entries=[]), "not_retained"),
                               (lambda c: c.audit["entries"][0].update(action="play"), "action_mismatch")):
            self.client = AuditClient(self.clock)
            change(self.client)
            code, result = self.execute(["--activity"])
            self.assertEqual((code, result["verdict"], result["correlation_status"]), (3, "unknown", "unknown"))
            item = result["correlations"][0]
            self.assertEqual((item["status"], item["reason"]), ("unknown", reason))
            self.assertNotIn("initiator", item)

    def test_matching_operator_projects_configured_label_without_claiming_app(self):
        self.client.audit["entries"][0]["initiator"] = {"credential_role": "operator", "operator_id": "ha:living-room"}
        _, result = self.execute(["--activity"])
        self.assertEqual(result["correlations"][0]["initiator"], {"credential_role": "operator", "operator_id": "ha:living-room"})
        _, text = self.execute(["--activity"], json_output=False)
        self.assertIn("Shared credentials do not identify a human, application or chat", text)
        self.assertNotIn('"entries"', text)

    def test_closed_audit_schema_invalid_role_scope_id_and_private_fields(self):
        mutations = [lambda v: v.update(url=PRIVATE), lambda v: v.update(device_id="other"),
                     lambda v: v.update(scope="player_execution"), lambda v: v.update(version=True),
                     lambda v: v["entries"][0].update(action=PRIVATE),
                     lambda v: v["entries"][0]["initiator"].update(credential_role="home_assistant"),
                     lambda v: v["entries"][0]["initiator"].update(operator_id="admin"),
                     lambda v: v["entries"][0].update(request_id="A" * 32),
                     lambda v: v["entries"][0].update(age_ms=600001),
                     lambda v: v.update(entries=v["entries"] * 2)]
        for change in mutations:
            value = sample()
            change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                audit.audit_metadata(value, DEVICE)

    def test_lookup_not_retained_and_clock_jump_are_not_fabricated(self):
        self.client.audit.update(lookup="not_retained", entries=[])
        code, result = self.execute([IDENTITY])
        self.assertEqual((code, result["verdict"]), (3, "unknown"))
        self.assertEqual(result["controller"]["data"]["entries"], [])
        value = sample()
        value["sampled_at_ms"] = 0
        value["entries"][0]["age_ms"] = 0
        self.assertEqual(audit.audit_metadata(value, DEVICE), value)
        value["sampled_at_ms"] = value["entries"][0]["accepted_at_ms"] + 1
        self.assertEqual(audit.audit_metadata(value, DEVICE), value)  # independent sub-ms monotonic age stays zero
        value["sampled_at_ms"] = 0
        value["entries"][0]["age_ms"] = 1000
        self.assertEqual(audit.audit_metadata(value, DEVICE), value)  # wall clock can move backwards
        # Admission order remains valid even when later admissions have earlier wall time.
        second = copy.deepcopy(value["entries"][0])
        second["request_id"] = "4" * 32
        second["accepted_at_ms"] += 1
        value["entries"].append(second)
        self.assertEqual(audit.audit_metadata(value, DEVICE), value)

    def test_runtime_mismatch_rejection_is_fenced_and_no_snapshot_is_attributed(self):
        for error in NATIVE["errors"]:
            value = {"version": 2, "runtime": "new-runtime" if error == "runtime_mismatch" else RUNTIME, "error": error}
            self.assertEqual(audit.activity_metadata(value, NATIVE["request"], "rejected"), value)
        value = {"version": 2, "runtime": "foreign", "error": "policy_denied"}
        with self.assertRaises(ValueError):
            audit.activity_metadata(value, NATIVE["request"], "rejected")
        self.client.activity["runtime"] = "foreign"
        _, result = self.execute(["--activity"])
        self.assertEqual(result["activity"]["reason"], "invalid_response")
        self.assertEqual(result["correlations"], [])

    def test_shared_deadline_and_changed_controller_prevent_join_or_extra_rpc(self):
        def expire(client, action):
            if action == "capabilities":
                self.clock.value += 25
        self.client.before = expire
        code, result = self.execute(["--activity"], timeout=20)
        self.assertEqual(code, 3)
        self.assertEqual([c[0] for c in self.client.calls], ["rpc"])
        self.assertEqual(result["controller"]["reason"], "deadline_exceeded")
        self.client = AuditClient(self.clock)
        self.client.before = lambda c, action: setattr(c, "server", "https://other.invalid")
        _, result = self.execute(["--activity"])
        self.assertEqual(result["activity"]["reason"], "controller_changed")
        self.assertEqual(result["correlations"], [])

    def test_raw_decoders_reject_duplicates_nonfinite_floats_oversize_and_unknown_receipt_fields(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.0}', b" " * 32769):
            with self.subTest(raw=raw[:32]), self.assertRaises(ValueError):
                audit.decode(raw, audit.MAX_AUDIT_BYTES)
        receipt = {"id": "a" * 32, "status": "ok", "data": NATIVE["example"]}
        self.assertEqual(audit.activity_receipt(ott, json.dumps(receipt).encode(), NATIVE["request"], "a" * 32), receipt)
        for change in (lambda v: v.update(secret=PRIVATE), lambda v: v.update(id="b" * 32),
                       lambda v: v["data"].update(url=PRIVATE)):
            value = copy.deepcopy(receipt)
            change(value)
            with self.assertRaises(ott.Error):
                audit.activity_receipt(ott, json.dumps(value).encode(), NATIVE["request"], "a" * 32)
        with self.assertRaises(ott.Error):
            audit.activity_receipt(ott, b" " * 8193, NATIVE["request"], "a" * 32)

    def test_invalid_arguments_never_touch_transport(self):
        for words in ([IDENTITY, "--activity"], ["bad"], ["--act"], ["--activity", "--out", PRIVATE]):
            with self.subTest(words=words):
                code, result = self.execute(words)
                self.assertEqual((code, result["reason"]), (1, "invalid_arguments"))
        self.assertEqual(self.client.calls, [])

    def test_real_cli_routes_single_and_parallel_group_without_player_rpc(self):
        self.client.barrier = threading.Barrier(2)
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "read_json", return_value=self.client.config), \
                mock.patch.object(ott, "Client", return_value=self.client), \
                mock.patch.object(ott, "request_audit_module", return_value=audit), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = ott.main(["ll", "audit", "-j"])
        self.assertEqual(code, 0, errors.getvalue() + output.getvalue())
        self.assertEqual(len(self.client.children), 2)
        for child in self.client.children:
            self.assertEqual([c[0] for c in child.calls], ["get"])
        self.assertEqual(json.loads(output.getvalue())["succeeded"], 2)

    def test_native_closed_enums_bounds_sequence_and_capability_gate(self):
        for action in NATIVE["actions"]:
            for outcome in NATIVE["results"]:
                value = copy.deepcopy(NATIVE["example"])
                value["entries"][0].update(action=action, result=outcome, age_ms=audit.MAX_INTEGER,
                                             sequence=audit.MAX_INTEGER)
                self.assertEqual(audit.activity_metadata(value, NATIVE["request"]), value)
        saturated = copy.deepcopy(NATIVE["clamped_example"])
        self.assertEqual(audit.activity_metadata(saturated, NATIVE["request"]), saturated)
        for change in (lambda v: v.update(version=True), lambda v: v.update(scope="execution"),
                       lambda v: v.update(entries=v["entries"] * 9),
                       lambda v: v["entries"][0].update(sequence=0),
                       lambda v: v["entries"][0].update(age_ms=audit.MAX_INTEGER + 1),
                       lambda v: v["entries"][0].update(age_ms=True),
                       lambda v: v["entries"][0].update(action=PRIVATE),
                       lambda v: v["entries"][0].update(result="executed"),
                       lambda v: v["entries"][0].update(request_id="A" * 32)):
            value = copy.deepcopy(NATIVE["example"])
            change(value)
            with self.assertRaises(ValueError):
                audit.activity_metadata(value, NATIVE["request"])
        for marker in ({}, {"version": True}, {"version": 2.0}, {"version": 1}, {"version": 2, "url": PRIVATE}):
            self.client = AuditClient(self.clock)
            self.client.caps[audit.ACTION] = marker
            _, result = self.execute(["--activity"])
            self.assertEqual(result["activity"]["reason"], "invalid_response")
            self.assertEqual([c[0] for c in self.client.calls], ["rpc", "get"])

    def test_player_help_always_lists_audit_but_activity_requires_marker(self):
        for enabled in (False, True):
            caps = capabilities()
            if enabled:
                caps[audit.ACTION] = {"version": 2}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(caps)}, "l")
            self.assertIn("audit [REQUEST_ID] [-j]", output.getvalue())
            self.assertEqual("audit --activity [-j]" in output.getvalue(), enabled)

    def test_controller_unknown_does_not_borrow_role_from_native_or_sequence(self):
        self.client.api_raw = mock.Mock(side_effect=ott.HTTPError(404))
        _, result = self.execute(["--activity"])
        self.assertEqual(result["controller"], {"status": "unknown", "reason": "unsupported_controller"})
        self.assertEqual(result["correlations"], [])
        self.assertNotIn("initiator", result["activity"])
        self.assertEqual(self.client.api_raw.call_count, 1)

    def test_real_receipt_path_validates_raw_bytes_before_returning_and_never_reposts(self):
        class ReceiptClient(ott.Client):
            def __init__(self, raw):
                self.timeout, self.raw = 10, raw
                self.calls, self.receipts = [], []
                self.on_receipt = self.receipts.append
            def api(self, path, payload=None, timeout=None):
                self.calls.append(("post", path, payload))
                return 202, {"id": "a" * 32, "receipt_wait_ms": "1000"}
            def api_raw(self, path, payload=None, timeout=None):
                self.calls.append(("get", path, payload))
                return 200, self.raw
        expected = {"id": "a" * 32, "status": "ok", "data": NATIVE["example"]}
        with mock.patch.object(ott, "request_audit_module", return_value=audit):
            client = ReceiptClient(json.dumps(expected).encode())
            got = client.call_receipt(DEVICE, audit.ACTION, NATIVE["request"])
            self.assertEqual(got, {"request_id": "a" * 32, "status": "ok", "data": NATIVE["example"]})
            self.assertEqual([c[0] for c in client.calls], ["post", "get"])
            self.assertEqual(client.receipts, [{"request_id": "a" * 32, "action": audit.ACTION, "status": "ok"}])
            for raw in (json.dumps(dict(expected, secret=PRIVATE)).encode(),
                        json.dumps(expected).replace('"version": 2', '"version": 2,"version": 2').encode(),
                        json.dumps(expected).replace('"age_ms": 1000', '"age_ms": NaN').encode(), b" " * 8193):
                client = ReceiptClient(raw)
                with self.subTest(raw=raw[:40]), self.assertRaises(ott.RequestError) as caught:
                    client.call_receipt(DEVICE, audit.ACTION, NATIVE["request"])
                self.assertEqual(caught.exception.reason, "invalid_response")
                self.assertEqual(caught.exception.action, audit.ACTION)
                self.assertEqual([c[0] for c in client.calls], ["post", "get"])
            client = ReceiptClient(b"{}")
            with self.assertRaises(ott.Error):
                client.call_receipt(DEVICE, audit.ACTION, dict(NATIVE["request"], url=PRIVATE))
            self.assertEqual(client.calls, [])

    def test_help_is_available_without_client_or_credentials(self):
        output = io.StringIO()
        with mock.patch.object(ott, "Client", side_effect=AssertionError("must not open credentials")), \
                mock.patch.object(ott, "read_json", side_effect=AssertionError("must not open configuration")), \
                mock.patch.object(ott, "request_audit_module", return_value=audit), \
                contextlib.redirect_stdout(output):
            self.assertEqual(ott.main(["l", "audit", "--help"]), 0)
        self.assertIn("controller RPC ingress metadata", output.getvalue())



if __name__ == "__main__":
    unittest.main()
