"""Real transport-shape orchestration against an entirely in-memory controller."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from dataclasses import FrozenInstanceError, replace
from itertools import pairwise
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    import favorites_document as document
    import favorites_operations as operations
    import favorites_remote as remote

spec = importlib.util.spec_from_file_location("favorites_remote_real_client", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
FIXTURE = json.loads((ROOT / "contracts/favorites-transfer-v1.json").read_bytes())
RUNTIME = FIXTURE["fixtures"]["begin"]["request"]["runtime"]
OPERATION = FIXTURE["fixtures"]["begin"]["request"]["operation_id"]
TRANSACTION = FIXTURE["fixtures"]["begin"]["response"]["transaction_id"]
LIMITS = remote.Limits(1048576, 8192, 256, 16384, 2097152, 1, 120, 600)
PRIVATE = "fixture-private-token"


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class HttpFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(PRIVATE)


class MemoryAPI:
    server = "https://controller.invalid"

    def __init__(self, clock):
        self.clock, self.calls, self.events, self.responses = clock, [], [], {}
        self.post_effects, self.get_effects, self.mutators, self.raw_mutators = {}, {}, {}, {}
        self.begin = self.preview = self.committed = None
        self.upload = bytearray()

    def fixture(self, op):
        return copy.deepcopy(FIXTURE["fixtures"][op]["response"])

    def data(self, params):
        op = params["op"]
        response = self.fixture(op)
        if op == "begin":
            self.begin = params
            response["operation_id"] = params["operation_id"]
        elif op == "append":
            self.assert_append(params)
            response.update(next_seq=params["seq"] + 1, received_bytes=len(self.upload))
        elif op == "preview":
            assert len(self.upload) == self.begin["size_bytes"]
            assert hashlib.sha256(self.upload).hexdigest() == self.begin["document_sha256"]
            response.update(base_revision=self.begin["expected_revision"], binding_revision=self.begin["binding_revision"])
            self.preview = copy.deepcopy(response)
        elif op == "commit":
            assert "saved" in self.events
            response.update(operation_id=params["operation_id"], scope=self.begin["scope"],
                            document_sha256=self.begin["document_sha256"], plan_digest=params["plan_digest"],
                            counts=self.preview["counts"])
            response["receipt"] = {key: copy.deepcopy(response[key]) for key in remote.protocol.RECEIPT}
            self.committed = copy.deepcopy(response)
        elif op == "status":
            if self.committed:
                response["operation_id"] = params["operation_id"]
                response["receipt"] = {key: copy.deepcopy(self.committed[key]) for key in remote.protocol.RECEIPT}
                response["per_store"] = copy.deepcopy(self.committed["per_store"])
            else:
                response = copy.deepcopy(FIXTURE["outcome_fixtures"]["unknown"])
                response["operation_id"] = params["operation_id"]
        elif op == "abort":
            response["operation_id"] = self.begin["operation_id"] if self.begin else OPERATION
        response["runtime"] = params["runtime"]
        return response

    def assert_append(self, params):
        assert params["offset"] == len(self.upload)
        raw = base64.b64decode(params["data_base64"], validate=True)
        assert hashlib.sha256(raw).hexdigest() == params["chunk_sha256"]
        self.upload.extend(raw)

    def api(self, path, payload=None, timeout=None):
        # This is the exact Client.api tuple shape and request JSON serializer.
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if payload is not None:
            raw = json.dumps(payload, ensure_ascii=False).encode()
            sent = json.loads(raw)
            assert sent["action"] == "favorites_transfer" and len(raw) <= 16384
            params = sent["params"]
            remote.protocol.validate_request(params)
            op = params["op"]
            request_id = f"{len(self.responses) + 1:032x}"
            self.calls.append({"method": "POST", "op": op, "at": self.clock(), "timeout": timeout, "payload": raw})
            self.events.append(op)
            data = self.data(params)
            if op in self.mutators:
                data = self.mutators[op](data)
            self.responses[request_id] = (op, {"id": request_id, "status": "ok", "data": data})
            effect = self.post_effects.get(op)
            if effect:
                effect(self, params)
            return 202, {"id": request_id, "status": "queued"}
        request_id = query["id"][0]
        op, response = self.responses[request_id]
        self.calls.append({"method": "GET", "op": op, "at": self.clock(), "timeout": timeout})
        effects = self.get_effects.get(op, [])
        effect = effects.pop(0) if effects else None
        if effect == "pending":
            return 202, {"status": "pending"}
        if isinstance(effect, Exception):
            raise effect
        if callable(effect):
            return effect(self, response)
        return 200, copy.deepcopy(response)

    def posts(self):
        return [x["op"] for x in self.calls if x["method"] == "POST"]

    def api_raw(self, path, payload=None, timeout=None):
        assert payload is None
        status, body = self.api(path, payload, timeout)
        request_id = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["id"][0]
        op = self.responses[request_id][0]
        raw = remote._json(body)
        if op in self.raw_mutators:
            raw = self.raw_mutators[op](raw)
        return status, raw


class RemoteFavoritesTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.api = MemoryAPI(self.clock)
        self.saved = []

    def save(self, record):
        self.saved.append(remote.validate_recovery_record(record))
        self.api.events.append("saved")
        return True

    def client(self, **overrides):
        kwargs = {"runtime": RUNTIME, "limits": LIMITS, "receipt_retention_seconds": 86400,
                  "scope_validator": document.validate_scope_document, "save_context": self.save,
                  "timeout": 45, "monotonic": self.clock, "sleep": self.clock.sleep, "uuid_factory": lambda: OPERATION}
        kwargs.update(overrides)
        return remote.RemoteFavorites(self.api, "device-1", **kwargs)

    def snapshot(self, client):
        return client.snapshot(copy.deepcopy(FIXTURE["scope"]))

    def error(self, code, callback, *args, **kwargs):
        with self.assertRaises(remote.RemoteError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_happy_replace_one_deadline_pacing_and_context_before_commit(self):
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertTrue(result.verified)
        self.assertEqual(result.outcome, "verified")
        self.assertEqual(self.api.posts(), ["snapshot", "begin", "append", "preview", "commit"])
        self.assertEqual(self.api.events[-2:], ["saved", "commit"])
        self.assertEqual(len(self.saved), 1)
        self.assertNotIn("document_base64", json.dumps(self.saved))
        posts = [x["at"] for x in self.api.calls if x["method"] == "POST"]
        self.assertTrue(all(b - a >= 1.5 for a, b in pairwise(posts)))
        self.assertTrue(all(b["at"] - a["at"] >= 0.5 for a, b in pairwise(self.api.calls)))
        self.assertTrue(all(0 < x["timeout"] <= min(10, 145 - x["at"]) for x in self.api.calls))
        self.error("already_started", client.replace, snapshot, snapshot.document_bytes)

    def assert_preview_safe(self):
        self.assertEqual(self.saved, [])
        self.assertNotIn("saved", self.api.events)
        self.assertNotIn("commit", self.api.posts())
        self.assertNotIn("status", self.api.posts())

    def test_preview_ready_retains_immutable_evidence_and_aborts_without_save(self):
        save = mock.Mock(side_effect=AssertionError("preview must not save"))
        client = self.client(save_context=save, limits=replace(LIMITS, max_chunk_bytes=128))
        snapshot = self.snapshot(client)
        result = client.preview(snapshot, snapshot.document_bytes)
        self.assertIsInstance(result, remote.PreviewOutcome)
        self.assertEqual((result.ready, result.reason, result.outcome, result.cleanup),
                         (True, "ready", "not_applied", "aborted"))
        self.assertEqual(result.data["counts"], self.api.preview["counts"])
        changed = result.data
        changed["counts"]["mediaFavorites"] = 999
        self.assertEqual(result.data["counts"], self.api.preview["counts"])
        with self.assertRaises(FrozenInstanceError):
            result.cleanup = "changed"
        self.assertEqual(bytes(self.api.upload), snapshot.document_bytes)
        self.assertEqual(self.api.posts(), ["snapshot", "begin", *["append"] * 6, "preview", "abort"])
        posts = [x["at"] for x in self.api.calls if x["method"] == "POST"]
        self.assertTrue(all(b - a >= 1.5 for a, b in pairwise(posts)))
        self.assertTrue(all(0 < x["timeout"] <= min(10, 145 - x["at"]) for x in self.api.calls))
        self.error("already_started", client.preview, snapshot, snapshot.document_bytes)
        self.error("already_started", client.replace, snapshot, snapshot.document_bytes)
        save.assert_not_called()
        self.assert_preview_safe()

    def test_blocked_preview_returns_validated_diagnostics_without_commit(self):
        diagnostic = {"collection": "mediaFavorites", "entry_index": 0, "reason": "missing"}
        self.api.mutators["preview"] = lambda data: data | {
            "ready": False, "counts": data["counts"] | {"unresolved": 1}, "unresolved": [diagnostic]}
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.preview(snapshot, snapshot.document_bytes)
        self.assertEqual((result.ready, result.reason, result.outcome, result.cleanup),
                         (False, "blocked", "not_applied", "aborted"))
        self.assertEqual(result.data["unresolved"], [diagnostic])
        self.assertEqual(result.data["counts"]["unresolved"], 1)
        self.assert_preview_safe()

    def test_preview_abort_loss_or_wrong_operation_does_not_discard_valid_preview(self):
        def lost(_api, _params):
            raise OSError(PRIVATE)
        for failure in ("enqueue", "receipt", "wrong_operation"):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == "enqueue":
                    self.api.post_effects["abort"] = lost
                elif failure == "receipt":
                    self.api.get_effects["abort"] = ["pending"] * 100
                else:
                    self.api.mutators["abort"] = lambda data: data | {"operation_id": remote.PLACEHOLDER_TRANSACTION}
                client = self.client()
                snapshot = self.snapshot(client)
                result = client.preview(snapshot, snapshot.document_bytes)
                self.assertEqual((result.ready, result.outcome, result.cleanup), (True, "not_applied", "unconfirmed"))
                self.assertEqual(self.api.posts().count("abort"), 1)
                self.assertLessEqual(self.clock(), 145)
                self.assert_preview_safe()

    def test_ready_and_blocked_preview_validate_context_and_schema_before_exposure(self):
        bad = {"runtime": "other-runtime", "transaction_id": remote.PLACEHOLDER_TRANSACTION,
               "base_revision": "changed", "binding_revision": "changed", "version": True,
               "counts": {"channelLists": 2, "channelMemberships": 0, "mediaFavorites": True, "unresolved": 0},
               "unresolved": [{"reason": "missing"}]}
        for ready in (True, False):
            for field, value in bad.items():
                with self.subTest(ready=ready, field=field):
                    self.setUp()
                    self.api.mutators["preview"] = lambda data: data | {"ready": ready, field: value}
                    client = self.client()
                    snapshot = self.snapshot(client)
                    result = client.preview(snapshot, snapshot.document_bytes)
                    self.assertEqual((result.ready, result.data, result.reason, result.cleanup),
                                     (None, None, "invalid_response", "aborted"))
                    self.assert_preview_safe()

    def test_preview_size_and_remaining_deadline_refuse_before_begin(self):
        for case in ("oversized", "large_budget", "short_budget", "expired"):
            with self.subTest(case=case):
                self.setUp()
                client = self.client(timeout=15 if case == "short_budget" else 45)
                snapshot = self.snapshot(client)
                raw = snapshot.document_bytes
                if case in ("oversized", "large_budget"):
                    raw += b" " * (1048576 + int(case == "oversized") - len(raw))
                elif case == "expired":
                    self.clock.now += 44
                code = "invalid_document" if case == "oversized" else "deadline_insufficient"
                self.error(code, client.preview, snapshot, raw)
                self.assertEqual(self.api.posts(), ["snapshot"])
                self.assert_preview_safe()

    def test_preview_budget_reserves_abort_but_does_not_require_commit_admission(self):
        client = self.client(timeout=17, receipt_retention_seconds=1)
        snapshot = self.snapshot(client)
        result = client.preview(snapshot, snapshot.document_bytes)
        self.assertEqual((result.ready, result.cleanup), (True, "aborted"))
        self.assertLess(self.clock(), 117)
        self.assert_preview_safe()
        self.setUp()
        client = self.client(timeout=17)
        snapshot = self.snapshot(client)
        self.error("deadline_insufficient", client.replace, snapshot, snapshot.document_bytes)
        self.assertEqual(self.api.posts(), ["snapshot"])

    def test_preview_uncertain_staging_and_late_receipt_never_commit(self):
        def lost(_api, _params):
            raise OSError(PRIVATE)
        def late(_api, response):
            self.clock.now = 140
            return 200, response
        for phase in ("begin", "append", "preview", "late"):
            with self.subTest(phase=phase):
                self.setUp()
                if phase == "late":
                    self.api.get_effects["preview"] = [late]
                else:
                    self.api.post_effects[phase] = lost
                client = self.client()
                snapshot = self.snapshot(client)
                result = client.preview(snapshot, snapshot.document_bytes)
                self.assertIsNone(result.ready)
                self.assertIsNone(result.data)
                self.assertEqual(result.outcome, "not_applied")
                self.assertEqual(result.cleanup, "unconfirmed" if phase == "begin" else "aborted")
                self.assertEqual(self.api.posts().count(phase if phase != "late" else "preview"), 1)
                self.assertLessEqual(self.clock(), 145)
                self.assert_preview_safe()

    def test_large_document_refused_before_begin_and_lower_chunk_limit_honored(self):
        client = self.client()
        snapshot = self.snapshot(client)
        raw = snapshot.document_bytes + b" " * (1048576 - len(snapshot.document_bytes))
        self.error("deadline_insufficient", client.replace, snapshot, raw)
        self.assertEqual(self.api.posts(), ["snapshot"])
        other = self.client(limits=replace(LIMITS, max_chunk_bytes=128), timeout=60)
        result = other.replace(snapshot, snapshot.document_bytes)
        self.assertTrue(result.verified)
        self.assertEqual(self.api.posts().count("append"), 6)
        self.assertEqual(bytes(self.api.upload), snapshot.document_bytes)

    def test_three_hundred_second_large_transfer_retains_all_bytes_and_http_budget(self):
        client = self.client(timeout=300)
        snapshot = self.snapshot(client)
        raw = snapshot.document_bytes + b" " * (1048576 - len(snapshot.document_bytes))
        result = client.replace(snapshot, raw)
        self.assertTrue(result.verified)
        self.assertEqual(self.api.posts().count("append"), 128)
        self.assertEqual(bytes(self.api.upload), raw)
        for request in self.api.calls:
            active = [x for x in self.api.calls if request["at"] - 60 < x["at"] <= request["at"]]
            self.assertLessEqual(len(active), 120)
        self.assertLess(self.clock(), 400)

    def test_get_backpressure_pending_and_transport_loss_never_repeat_post(self):
        self.api.get_effects["snapshot"] = [HttpFailure(429), OSError(PRIVATE), "pending"]
        client = self.client(timeout=60)
        snapshot = self.snapshot(client)
        self.assertIsNotNone(snapshot.document_bytes)
        self.assertEqual(self.api.posts(), ["snapshot"])
        gets = [x for x in self.api.calls if x["method"] == "GET"]
        self.assertEqual(len(gets), 4)
        self.assertGreaterEqual(gets[1]["at"] - gets[0]["at"], 3)
        self.assertGreaterEqual(gets[2]["at"] - gets[1]["at"], 6)

    def test_begin_enqueue_loss_never_replays_or_commits(self):
        def lost(_api, _params):
            raise OSError(PRIVATE)
        self.api.post_effects["begin"] = lost
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual(result.outcome, "not_applied")
        self.assertEqual(result.cleanup, "unconfirmed")
        self.assertIsNone(result.request_id)
        self.assertEqual(self.api.posts(), ["snapshot", "begin"])
        self.assertEqual(self.saved, [])

    def test_lost_commit_enqueue_ack_reconciles_once_without_commit_replay(self):
        def lost(_api, _params):
            raise OSError(PRIVATE)
        self.api.post_effects["commit"] = lost
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertTrue(result.verified)
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts().count("status"), 1)
        self.assertNotIn("abort", self.api.posts())

    def test_lost_commit_receipt_reserves_status_and_unknown_is_not_success(self):
        self.api.get_effects["commit"] = ["pending"] * 100
        self.api.mutators["status"] = lambda _data: copy.deepcopy(FIXTURE["outcome_fixtures"]["unknown"])
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertFalse(result.verified)
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts().count("status"), 1)
        self.assertLessEqual(self.clock(), 145)
        self.assertNotIn("abort", self.api.posts())

    def test_preview_conflict_or_rejection_aborts_stage_without_commit(self):
        self.api.mutators["preview"] = lambda data: data | {"base_revision": "concurrent-change"}
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual((result.outcome, result.cleanup), ("not_applied", "aborted"))
        self.assertNotIn("commit", self.api.posts())
        self.assertEqual(self.saved, [])
        self.setUp()
        def rejected(_api, response):
            return 200, {"id": response["id"], "status": "rejected", "data": {
                "version": 1, "op": "preview", "runtime": RUNTIME, "error": "revision_conflict"}}
        self.api.get_effects["preview"] = [rejected]
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual(result.reason, "player_rejected")
        self.assertNotIn("commit", self.api.posts())

    def test_failed_or_late_durable_callback_never_sends_commit(self):
        def failure(_record):
            raise OSError(PRIVATE)
        def late(_record):
            self.clock.now += 40
            return True
        for callback in (lambda _: False, failure, late):
            self.setUp()
            client = self.client(save_context=callback)
            snapshot = self.snapshot(client)
            result = client.replace(snapshot, snapshot.document_bytes)
            self.assertEqual((result.outcome, result.reason), ("not_applied", "context_not_saved"))
            self.assertNotIn("commit", self.api.posts())

    def test_late_commit_receipt_is_unknown_until_fresh_restart_reconciliation(self):
        def late(_api, response):
            self.clock.now = 146
            return 200, response
        self.api.get_effects["commit"] = [late]
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual((result.outcome, result.verified), ("unknown", False))
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertNotIn("status", self.api.posts())  # No new POST after whole deadline.
        fresh = self.client()
        restored = fresh.reconcile(self.saved[0])
        self.assertTrue(restored.verified)
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts()[-1], "status")

    def test_wrong_runtime_scope_request_id_and_bad_receipt_do_not_report_success(self):
        self.api.mutators["preview"] = lambda data: data | {"runtime": "other-runtime"}
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual(result.outcome, "not_applied")
        self.assertNotIn("commit", self.api.posts())
        self.setUp()
        self.api.get_effects["commit"] = [lambda _api, response: (200, response | {"id": "f" * 32})]
        def unbound(data):
            data["receipt"]["document_sha256"] = "0" * 64
            return data
        self.api.mutators["status"] = unbound
        client = self.client()
        snapshot = self.snapshot(client)
        result = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual((result.outcome, result.verified), ("unknown", False))
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts().count("status"), 1)

    def test_explicit_partial_result_is_terminal_and_never_success(self):
        self.api.mutators["commit"] = lambda _data: copy.deepcopy(FIXTURE["outcome_fixtures"]["partial"])
        client = self.client()
        snapshot = self.snapshot(client)
        outcome = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual((outcome.outcome, outcome.verified), ("partial", False))
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertNotIn("abort", self.api.posts())
        self.assertNotIn("status", self.api.posts())

    def test_explicit_unknown_commit_receipt_attempts_only_one_status(self):
        partial = copy.deepcopy(FIXTURE["outcome_fixtures"]["partial"])
        partial["outcome"] = "unknown"
        partial["per_store"] = []
        self.api.mutators["commit"] = lambda _data: partial
        client = self.client()
        snapshot = self.snapshot(client)
        self.assertTrue(client.replace(snapshot, snapshot.document_bytes).verified)
        self.assertEqual(self.api.posts().count("status"), 1)
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertNotIn("abort", self.api.posts())

    def test_raw_advertised_response_and_metadata_limits_cannot_lose_padding(self):
        self.api.raw_mutators["snapshot"] = lambda raw: raw + b" " * 4096
        client = self.client(limits=replace(LIMITS, max_snapshot_response_bytes=4096))
        self.error("invalid_response", self.snapshot, client)
        self.assertEqual(self.api.posts(), ["snapshot"])
        self.setUp()
        self.api.raw_mutators["preview"] = lambda raw: raw[:-2] + b" " * (128 * 1024) + raw[-2:]
        client = self.client()
        snapshot = self.snapshot(client)
        outcome = client.replace(snapshot, snapshot.document_bytes)
        self.assertEqual(outcome.outcome, "not_applied")
        self.assertNotIn("commit", self.api.posts())

    def test_restart_requires_fresh_same_scope_binding_and_target(self):
        client = self.client()
        snapshot = self.snapshot(client)
        self.assertTrue(client.replace(snapshot, snapshot.document_bytes).verified)
        original = copy.deepcopy(self.saved[0])
        for mutate in (lambda data: data | {"binding_revision": "changed-binding"},
                       lambda data: data | {"scope": data["scope"] | {"sourceBindingId": "00000000-0000-4000-8000-000000000099"}}):
            self.api.mutators["snapshot"] = mutate
            fresh = self.client(runtime="restarted-runtime")
            before = len(self.api.posts())
            outcome = fresh.reconcile(original)
            self.assertEqual((outcome.outcome, outcome.verified), ("unknown", False))
            self.assertEqual(self.api.posts()[before:], ["snapshot"])
        self.api.mutators.clear()
        fresh = self.client(runtime="restarted-runtime")
        self.api.server = "https://wrong-controller.invalid"
        before = len(self.api.calls)
        self.assertFalse(fresh.reconcile(original).verified)
        self.assertEqual(len(self.api.calls), before)
        self.assertEqual(self.saved[0], original)

    def test_snapshot_target_binding_invalid_settings_and_clock_are_checked(self):
        for override in ({"receipt_retention_seconds": None}, {"runtime": "bad runtime"},
                         {"limits": replace(LIMITS, max_chunk_bytes=9000)}, {"scope_validator": None},
                         {"save_context": None}, {"timeout": 301}):
            self.error("invalid_configuration", self.client, **override)
        client = self.client()
        snapshot = self.snapshot(client)
        foreign = remote.Snapshot(("wrong", "device-1", RUNTIME), snapshot._result)
        self.error("invalid_snapshot", client.replace, foreign, snapshot.document_bytes)
        self.error("invalid_snapshot", client.preview, foreign, snapshot.document_bytes)
        self.assertEqual(self.api.posts(), ["snapshot"])
        stalled = self.client(sleep=lambda _: None)
        self.error("clock_invalid", stalled.snapshot, FIXTURE["scope"])
        changed = self.client()
        before = len(self.api.calls)
        self.api.server = "https://different.invalid"
        self.error("invalid_configuration", changed.snapshot, FIXTURE["scope"])
        self.assertEqual(len(self.api.calls), before)

    def test_context_store_roundtrip_restores_actual_receipt_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            store = operations.ContextStore(directory, server=self.api.server, device="device-1",
                                             validate_context=remote.validate_recovery_record)
            def save(record):
                store.save(record)
                self.api.events.append("saved")
                return True
            client = self.client(save_context=save)
            snapshot = self.snapshot(client)
            self.assertTrue(client.replace(snapshot, snapshot.document_bytes).verified)
            saved = store.load(OPERATION)
            self.assertIsInstance(remote.restore_context(saved), remote.protocol.ReceiptContext)
            self.assertNotIn("document_base64", json.dumps(saved))
            fresh = self.client()
            self.assertTrue(fresh.reconcile(saved).verified)
            self.assertEqual(self.api.posts().count("commit"), 1)
            self.assertEqual(self.api.posts()[-1], "status")
            changed = copy.deepcopy(saved)
            changed["commit_request"]["plan_digest"] = "0" * 64
            self.error("invalid_context", remote.restore_context, changed)
            other_runtime = self.client(runtime="new-runtime")
            before = len(self.api.posts())
            self.assertTrue(other_runtime.reconcile(saved).verified)
            self.assertEqual(self.api.posts()[before:], ["snapshot", "status"])
            self.assertEqual(self.api.posts().count("commit"), 1)
            wrong_target = operations.ContextStore(directory, server=self.api.server, device="another-device",
                                                    validate_context=remote.validate_recovery_record)
            calls = len(self.api.calls)
            with self.assertRaises(operations.OperationStoreError):
                wrong_target.load(OPERATION)
            self.assertEqual(len(self.api.calls), calls)

    def test_actual_client_api_serializer_and_receipt_boundary(self):
        class Response:
            def __init__(inner, status, body):
                inner.status = status
                inner.stream = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
                inner.size = len(inner.stream.getvalue())
            def __enter__(inner):
                return inner
            def __exit__(inner, *_):
                return False
            @property
            def length(inner):
                return inner.size - inner.stream.tell()
            def read1(inner, size):
                return inner.stream.read(size)
        observed = []
        def open_request(request, timeout):
            observed.append(request)
            parsed = urllib.parse.urlsplit(request.full_url)
            payload = json.loads(request.data) if request.data is not None else None
            method = self.api.api if payload is not None else self.api.api_raw
            status, body = method(parsed.path + "?" + parsed.query, payload, timeout)
            return Response(status, body)
        for operation in ("replace", "preview"):
            with self.subTest(operation=operation):
                self.setUp()
                observed.clear()
                real = ott.Client.__new__(ott.Client)
                real.server, real.timeout, real.token = self.api.server, 45, PRIVATE
                real.opener = mock.Mock(open=open_request)
                client = self.client()
                client.client = real
                with mock.patch.object(ott.time, "monotonic", self.clock):
                    snapshot = self.snapshot(client)
                    result = getattr(client, operation)(snapshot, snapshot.document_bytes)
                if operation == "replace":
                    self.assertTrue(result.verified)
                else:
                    self.assertTrue(result.ready)
                    self.assertEqual(result.cleanup, "aborted")
                    self.assert_preview_safe()
                self.assertTrue(all(x.get_method() in ("GET", "POST") for x in observed))
                self.assertEqual(sum(x.get_method() == "POST" for x in observed), 5)
                for request in observed:
                    if request.data:
                        self.assertEqual(request.data, json.dumps(json.loads(request.data), ensure_ascii=False).encode())
                self.assertNotIn(PRIVATE, json.dumps(result.summary()))


if __name__ == "__main__":
    unittest.main()
