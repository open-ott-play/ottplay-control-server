"""Negotiated multi-view cleanup across lost admission capacity; fake RPC only."""
import copy
import contextlib
import io
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

from test_multiview_cli import API, Client, Error, FIXTURE, HTTPError, PlayerRejected, mv


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_FIXTURE = json.loads((ROOT / "contracts" / "multiview-recovery-v1.json").read_text(encoding="utf-8"))
PROBE = RECOVERY_FIXTURE["probe"]["params"]
RUNTIME = RECOVERY_FIXTURE["capabilities"]["player"]["runtime"]
RECOVERY = RECOVERY_FIXTURE["recovery_markers"]["all"]


class RecoveryClient(Client):
    def __init__(self):
        super().__init__()
        self.recovery = copy.deepcopy(RECOVERY)

    def call(self, device, action, params):
        value = super().call(device, action, params)
        if action == "capabilities" and self.recovery is not None:
            value["multiview_recovery"] = copy.deepcopy(self.recovery)
        return value


def wire(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class RecoveryContractTest(unittest.TestCase):
    def test_frozen_cross_language_fixture_positive_and_negative_shapes(self):
        for name, request in RECOVERY_FIXTURE["requests"].items():
            with self.subTest(name=name):
                self.assertTrue(mv.request_valid(request["params"]))
                for key, status in (("results", "ok"), ("rejections", "rejected")):
                    response = RECOVERY_FIXTURE[key][name]
                    self.assertEqual(mv.metadata(response, request["params"], status), response)
        for marker in RECOVERY_FIXTURE["recovery_markers"].values():
            self.assertEqual(mv.recovery_capability(marker, RUNTIME), marker)
        for row in RECOVERY_FIXTURE["invalid_requests"]:
            with self.subTest(name=row["name"]):
                self.assertFalse(mv.request_valid(row["request"]["params"]))
        for row in RECOVERY_FIXTURE["invalid_capabilities"]:
            with self.subTest(name=row["name"]), self.assertRaises(Error):
                mv.capabilities_receipt(API, wire({"id": "1" * 32, "status": "ok", "data": row["data"]}), "1" * 32)

    def test_closed_marker_has_independent_runtime_and_operation_gates(self):
        for operations in (["status"], ["stop", "status"], ["off", "status"],
                           ["off", "stop", "status"]):
            marker = dict(RECOVERY, operations=operations,
                          off_modes=["close_only"] if "off" in operations else [])
            before = copy.deepcopy(marker)
            result = mv.recovery_capability(marker, RUNTIME)
            self.assertEqual(result, marker)
            result["operations"].clear()
            self.assertEqual(marker, before)
        invalid = [None, {}, dict(RECOVERY, extra=1), dict(RECOVERY, version=True),
                   dict(RECOVERY, version=1.0), dict(RECOVERY, version=2),
                   dict(RECOVERY, runtime="other-runtime"), dict(RECOVERY, runtime=None),
                   dict(RECOVERY, operations=[]), dict(RECOVERY, operations=["stop"]),
                   dict(RECOVERY, operations=["status", "status"]),
                   dict(RECOVERY, operations=["status", "layout"]),
                   dict(RECOVERY, operations=["status"], off_modes=["close_only"]),
                   dict(RECOVERY, off_modes=[]), dict(RECOVERY, off_modes=["promote_active"]),
                   dict(RECOVERY, off_modes=["close_only", "close_only"])]
        for value in invalid:
            with self.subTest(marker=value), self.assertRaises(mv.InvalidResponse):
                mv.recovery_capability(value, RUNTIME)
        with self.assertRaises(mv.InvalidResponse):
            mv.recovery_capability(dict(RECOVERY, runtime="x" * 65), "x" * 65)

    def test_legacy_marker_and_modes_remain_closed(self):
        self.assertEqual(mv.parse(["off", "close_only"]), {"op": "off", "mode": "close_only"})
        self.assertEqual(mv.parse(["off"]), {"op": "off", "mode": "promote_active"})
        for marker in (dict(FIXTURE["capability"]["multiview"], recovery_version=1),
                       dict(FIXTURE["capability"]["multiview"], off_modes=["close_only"])):
            with self.assertRaises(mv.InvalidResponse):
                mv.capability(marker)
        self.assertEqual(mv.capability(FIXTURE["zero_capacity_capability"]["multiview"]),
                         FIXTURE["zero_capacity_capability"]["multiview"])

    def test_recovery_flag_is_exactly_typed_and_bound_only_to_cleanup(self):
        for name in ("stop", "off"):
            request = copy.deepcopy(FIXTURE["requests"][name]["params"])
            request["recovery_version"] = 1
            if name == "off":
                request["mode"] = "close_only"
            self.assertTrue(mv.request_valid(request))
            response = {"version": 1, "runtime": RUNTIME, "op": name,
                        "binding": {k: v for k, v in request.items() if k not in ("version", "runtime", "op")},
                        "accepted": True, "outcome": "applied"}
            self.assertEqual(mv.metadata(response, request), response)
            for flag in (True, 1.0, "1", 0, 2, None):
                with self.subTest(name=name, flag=flag):
                    self.assertFalse(mv.request_valid(dict(request, recovery_version=flag)))
                    bad = copy.deepcopy(response)
                    bad["binding"]["recovery_version"] = flag
                    with self.assertRaises(mv.InvalidResponse):
                        mv.metadata(bad, request)
            bad = copy.deepcopy(response)
            del bad["binding"]["recovery_version"]
            with self.assertRaises(mv.InvalidResponse):
                mv.metadata(bad, request)
            for reason in ("runtime_changed", "scene_changed", "slot_changed", "source_changed", "unsupported"):
                rejection = {k: response[k] for k in ("version", "runtime", "op", "binding")}
                rejection["reason"] = reason
                status = "unsupported" if reason == "unsupported" else "rejected"
                self.assertEqual(mv.metadata(rejection, request, status), rejection)
        for name, fixture in FIXTURE["requests"].items():
            request = copy.deepcopy(fixture["params"])
            if request["op"] == "stop":
                continue
            with self.subTest(name=name):
                self.assertFalse(mv.request_valid(dict(request, recovery_version=1)))
        request = copy.deepcopy(FIXTURE["requests"]["off"]["params"])
        self.assertFalse(mv.request_valid(dict(request, mode="close_only")))

    def test_raw_receipts_reject_lexical_flag_variants_and_broken_binding(self):
        request = dict(FIXTURE["requests"]["stop"]["params"], recovery_version=1)
        response = copy.deepcopy(FIXTURE["results"]["stop"])
        response["binding"]["recovery_version"] = 1
        identity = "1" * 32
        raw = wire({"id": identity, "status": "ok", "data": response})
        self.assertEqual(mv.receipt(API, raw, request, identity)["data"], response)
        for replacement in (b'1.0', b'1e0', b'true', b'"1"', b'-0', b'null'):
            with self.subTest(replacement=replacement), self.assertRaises(Error):
                mv.receipt(API, raw.replace(b'"recovery_version":1', b'"recovery_version":' + replacement), request, identity)
        duplicate = raw.replace(b'"recovery_version":1', b'"recovery_version":1,"recovery_version":1')
        with self.assertRaises(Error):
            mv.receipt(API, duplicate, request, identity)

    def test_recovery_capability_receipt_requires_valid_legacy_marker_and_runtime(self):
        data = {"version": 1, "player": {"version": "test", "runtime": RUNTIME},
                "multiview": copy.deepcopy(FIXTURE["zero_capacity_capability"]["multiview"]),
                "multiview_recovery": copy.deepcopy(RECOVERY)}
        envelope = {"id": "1" * 32, "status": "ok", "data": data}
        self.assertEqual(mv.capabilities_receipt(API, wire(envelope), envelope["id"]), envelope)
        for edit in (lambda d: d.pop("multiview"),
                     lambda d: d.update(multiview={}),
                     lambda d: d["player"].update(runtime="other"),
                     lambda d: d["multiview_recovery"].update(version=2),
                     lambda d: d["multiview_recovery"].update(extra="PRIVATE")):
            bad = copy.deepcopy(envelope)
            edit(bad["data"])
            with self.assertRaises(Error) as raised:
                mv.capabilities_receipt(API, wire(bad), envelope["id"])
            self.assertNotIn("PRIVATE", str(raised.exception))


class RecoveryRunTest(unittest.TestCase):
    def setUp(self):
        self.client = RecoveryClient()

    def execute(self, words):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                result = mv.run(API, self.client, "device", words, 45, True)
        finally:
            self.assertEqual(self.client.timeout, 45)
        self.assertEqual(result, 0)
        return json.loads(out.getvalue())

    def ops(self):
        return [(action, params) for _, action, params, _ in self.client.calls]

    def retained_nine(self, capacity):
        snapshot = self.client.state["snapshot"]
        snapshot["layout"] = "grid9"
        row = snapshot["slots"][0]
        snapshot["slots"] = [dict(row, slot_id=i, slot_generation=f"generation-{i}",
                                  source_revision=f"source-{i}") for i in reversed(range(9))]
        if capacity == 0:
            self.client.marker = copy.deepcopy(FIXTURE["zero_capacity_capability"]["multiview"])
        else:
            self.client.marker.update(max_slots=capacity, layouts=["single", "perimeter"])

    def test_frozen_paused_retained_slot_uses_exact_shared_cleanup_request(self):
        for words, name in ((["window", "9", "stop"], "stop_slot8"), (["off", "close_only"], "off_close_only")):
            self.setUp()
            self.client.marker = copy.deepcopy(RECOVERY_FIXTURE["capabilities"]["multiview"])
            self.client.state = copy.deepcopy(RECOVERY_FIXTURE["results"]["status"])
            self.execute(words)
            self.assertEqual(self.ops(), [("capabilities", PROBE),
                                         ("multiview", RECOVERY_FIXTURE["requests"]["status"]["params"]),
                                         ("multiview", RECOVERY_FIXTURE["requests"][name]["params"])])
            self.assertEqual(self.client.state["snapshot"]["slots"][8]["state"], "paused")

    def test_zero_or_shrunk_admission_capacity_does_not_bound_retained_stop(self):
        for capacity in (0, 1):
            self.setUp()
            self.retained_nine(capacity)
            self.execute(["window", "9", "stop"])
            self.assertEqual(self.ops()[0], ("capabilities", PROBE))
            self.assertEqual(len(self.ops()), 3)
            request = self.ops()[-1][1]
            self.assertEqual(request, {"version": 1, "runtime": RUNTIME, "op": "stop", "slot_id": 8,
                                      "slot_generation": "generation-8", "source_revision": "source-8",
                                      "expected": self.client.state["snapshot"]["context"], "recovery_version": 1})
            for words in (["focus", "9"], ["window", "9", "play", "ticket"], ["layout", "grid9"],
                          ["sources", "9"], ["audio", "9"]):
                self.client.calls.clear()
                with self.subTest(capacity=capacity, words=words), self.assertRaises(Error):
                    self.execute(words)
                self.assertEqual(len(self.ops()), 1)

    def test_close_only_never_silently_promotes_or_restores(self):
        self.retained_nine(0)
        self.execute(["off", "close_only"])
        request = self.ops()[-1][1]
        self.assertEqual(request, {"version": 1, "runtime": RUNTIME, "op": "off", "mode": "close_only",
                                  "expected": self.client.state["snapshot"]["context"], "recovery_version": 1})
        for words in (["off"], ["off", "restore_previous"]):
            self.client.calls.clear()
            with self.assertRaises(Error):
                self.execute(words)
            self.assertEqual(len(self.ops()), 1)
        self.setUp()
        for words in (["off"], ["off", "restore_previous"]):
            self.client.calls.clear()
            self.execute(words)
            self.assertNotIn("recovery_version", self.ops()[-1][1])

    def test_missing_or_status_only_recovery_retains_legacy_gates(self):
        for marker in (None, dict(RECOVERY, operations=["status"], off_modes=[])):
            for capacity in (0, 9):
                self.setUp()
                self.client.recovery = marker
                if capacity == 0:
                    self.retained_nine(0)
                for words in (["off", "close_only"], ["window", "1", "stop"]):
                    self.client.calls.clear()
                    if capacity > 0 and words[0] == "window":
                        self.execute(words)
                        self.assertNotIn("recovery_version", self.ops()[-1][1])
                    else:
                        with self.assertRaises(Error):
                            self.execute(words)
                        self.assertEqual(len(self.ops()), 1)
        self.setUp()
        self.client.recovery = dict(RECOVERY, operations=["status", "stop"], off_modes=[])
        with self.assertRaises(Error):
            self.execute(["off", "close_only"])
        self.assertEqual(len(self.ops()), 1)

    def test_invalid_marker_or_runtime_never_sends_status_or_falls_back(self):
        for marker in ({}, dict(RECOVERY, runtime="stale"), dict(RECOVERY, version=2),
                       dict(RECOVERY, operations=["status", "stop", "stop"])):
            self.setUp()
            self.client.recovery = marker
            with self.assertRaises(Error):
                self.execute(["window", "1", "stop"])
            self.assertEqual(self.ops(), [("capabilities", PROBE)])
        self.setUp()
        self.client.marker = None
        with self.assertRaises(Error):
            self.execute(["off", "close_only"])
        self.assertEqual(len(self.ops()), 1)

    def test_missing_retained_slot_fails_after_fresh_status_without_mutation(self):
        self.client.marker = copy.deepcopy(FIXTURE["zero_capacity_capability"]["multiview"])
        self.client.state = copy.deepcopy(FIXTURE["empty_status"])
        with self.assertRaisesRegex(Error, "not in the current layout"):
            self.execute(["window", "9", "stop"])
        self.assertEqual(len(self.ops()), 2)

    def test_fresh_status_runtime_and_full_cas_are_not_refreshed_on_rejection(self):
        for field in ("runtime", "scene", "layout_revision", "focus_revision", "audio_revision",
                      "slot_generation", "source_revision"):
            self.setUp()
            def transform(value, request):
                if request.get("op") == "status" and field == "runtime":
                    value["runtime"] = "other-runtime"
                elif request.get("op") == "stop":
                    common = {key: value[key] for key in ("version", "runtime", "op", "binding")}
                    reason = {"scene": "scene_changed", "layout_revision": "layout_changed",
                              "focus_revision": "focus_changed", "audio_revision": "audio_changed",
                              "slot_generation": "slot_changed", "source_revision": "source_changed"}[field]
                    raise PlayerRejected(dict(common, reason=reason))
                return value
            self.client.transform = transform
            with self.subTest(field=field), self.assertRaises(Error):
                self.execute(["window", "1", "stop"])
            self.assertEqual(len(self.ops()), 2 if field == "runtime" else 3)
            if field != "runtime":
                request = self.ops()[-1][1]
                self.assertEqual(request["expected"], self.client.state["snapshot"]["context"])
                self.assertEqual(request["slot_generation"], self.client.state["snapshot"]["slots"][0]["slot_generation"])
                self.assertEqual(request["source_revision"], self.client.state["snapshot"]["slots"][0]["source_revision"])

    def test_lost_or_invalid_cleanup_receipt_never_reexecutes(self):
        for words in (["window", "1", "stop"], ["off", "close_only"]):
            for lost in (False, True):
                self.setUp()
                def transform(value, request):
                    if request.get("recovery_version") == 1:
                        if lost:
                            raise Error("Receipt unavailable; request may have executed")
                        value["binding"].pop("recovery_version")
                    return value
                self.client.transform = transform
                with self.subTest(words=words, lost=lost), self.assertRaises(Error):
                    self.execute(words)
                self.assertEqual(len(self.ops()), 3)
                self.assertEqual(sum("recovery_version" in params for _, params in self.ops()), 1)

    def test_only_explicit_http_400_opt_in_rejection_falls_back_once(self):
        self.client.recovery = None
        def before(client, action, request):
            if action == "capabilities" and request == PROBE:
                exc = HTTPError("old controller")
                exc.code = 400
                raise exc
        self.client.before = before
        self.execute(["window", "1", "stop"])
        self.assertEqual(self.ops()[:2], [("capabilities", PROBE), ("capabilities", {"multiview": True})])
        self.assertEqual(len(self.ops()), 4)
        self.assertNotIn("recovery_version", self.ops()[-1][1])
        for status in (400, 401, 403, 404, 429, 500, 503):
            self.setUp()
            def fail(client, action, request):
                exc = HTTPError("PRIVATE")
                exc.code = status
                raise exc
            self.client.before = fail
            with self.assertRaises(Error) as raised:
                self.execute(["off", "close_only"])
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertEqual(len(self.ops()), 2 if status == 400 else 1)
        for error in (Error("timeout"), mv.InvalidResponse("bad metadata"), PlayerRejected({"private": "secret"})):
            self.setUp()
            def fail(client, action, request):
                raise error
            self.client.before = fail
            with self.assertRaises(Error):
                self.execute(["window", "1", "stop"])
            self.assertEqual(len(self.ops()), 1)

    def test_recovery_marker_leaked_after_legacy_fallback_is_not_authority(self):
        def before(client, action, request):
            if action == "capabilities" and request == PROBE:
                exc = HTTPError("old controller")
                exc.code = 400
                raise exc
        self.client.before = before
        with self.assertRaises(Error):
            self.execute(["window", "1", "stop"])
        self.assertEqual(self.ops(), [("capabilities", PROBE), ("capabilities", {"multiview": True})])

    def test_legacy_fallback_shares_deadline_and_cancellation_before_any_status(self):
        for cancel in (False, True):
            self.setUp()
            self.client.group_cancelled = threading.Event()
            clock = [0.0]
            def before(client, action, request):
                clock[0] = 46
                if cancel:
                    client.group_cancelled.set()
                exc = HTTPError("old controller")
                exc.code = 400
                raise exc
            self.client.before = before
            with mock.patch.object(mv.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaises(KeyboardInterrupt if cancel else Error):
                    self.execute(["window", "1", "stop"])
            self.assertEqual(len(self.ops()), 1)

    def test_recovery_shared_deadline_and_cancel_prevent_mutation_after_status(self):
        for cancel in (False, True):
            self.setUp()
            self.retained_nine(0)
            self.client.group_cancelled = threading.Event()
            clock = [0.0]
            def before(client, action, request):
                if action == "capabilities":
                    clock[0] = 4
                elif request.get("op") == "status":
                    clock[0] = 46
                    if cancel:
                        client.group_cancelled.set()
            self.client.before = before
            with mock.patch.object(mv.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaises(KeyboardInterrupt if cancel else Error):
                    self.execute(["window", "9", "stop"])
            self.assertEqual(len(self.ops()), 2)
            self.assertEqual([row[3] for row in self.client.calls], [45, 41])


if __name__ == "__main__":
    unittest.main()
