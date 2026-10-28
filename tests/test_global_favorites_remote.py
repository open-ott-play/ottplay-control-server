"""Global orchestration through real byte envelopes and an in-memory API only."""
import base64
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from itertools import pairwise
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    import favorites_operations as old_operations
    import favorites_protocol as old_protocol
    import global_favorites_document as document
    import global_favorites_remote as remote

OPERATION = "10000000-0000-4000-8000-000000000001"
TRANSACTION = "20000000-0000-4000-8000-000000000002"
SNAPSHOT = "30000000-0000-4000-8000-000000000003"
PRIVATE = "synthetic-private-token"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def row(number=1, *, kind="channel", title=None):
    provider = "plex" if kind == "plex-media" else "m3u"
    locator = ({"kind": kind, "serverId": "fixture-pms", "ratingKey": str(number)}
               if kind == "plex-media" else {"kind": "channel", "channelId": str(number)})
    return {"title": title or f"Favorite {number}",
            "origin": {"provider": provider, "profile": "Fixture", "subprofile": None, "sourceAlias": "Fixture source"},
            "locator": locator}


def doc(*entries):
    return document.validate_document({"version": 1, "entries": list(entries)})


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class MemoryAPI:
    server = "https://controller.invalid"

    def __init__(self, clock):
        self.clock = clock
        self.current = doc()
        self.limits = copy.deepcopy(remote.protocol.LIMITS)
        self.kinds = list(remote.protocol.KINDS)
        self.calls, self.events, self.responses = [], [], {}
        self.mutators, self.post_effects, self.get_effects = {}, {}, {}
        self.error_ops = {}
        self.upload = bytearray()
        self.begin = self.plan = self.preview = self.receipt = None
        self.before_commit = None
        self.export_page_bytes = None

    def data(self, params):
        op = params["op"]
        common = {"version": 1, "runtime": params["runtime"], "op": op, "subject": "global"}
        if op in self.error_ops:
            return {**common, "error": self.error_ops[op]}
        if op == "describe":
            self.export_raw = document.canonical_bytes(self.current)
            data = {"revision": digest(self.export_raw), "context_revision": "2" * 64,
                    "write_ready": True, "write_blocker": None, "export_error": None,
                    "admission": {"operation_id": OPERATION, "ttl_ms": 10000},
                    "export": {"snapshot_id": SNAPSHOT, "document_sha256": digest(self.export_raw),
                               "size_bytes": len(self.export_raw), "entries": len(self.current["entries"]),
                               "expires_at": "2099-01-01T00:00:00Z"},
                    "limits": copy.deepcopy(self.limits), "locator_kinds": list(self.kinds)}
        elif op == "export":
            assert params["snapshot_id"] == SNAPSHOT
            length = min(params["max_bytes"], self.export_page_bytes or params["max_bytes"])
            part = self.export_raw[params["offset"]:params["offset"] + length]
            end = params["offset"] + len(part)
            data = {"snapshot_id": SNAPSHOT, "revision": digest(self.export_raw),
                    "document_sha256": digest(self.export_raw), "size_bytes": len(self.export_raw),
                    "offset": params["offset"], "chunk_sha256": digest(part),
                    "data_base64": base64.b64encode(part).decode(), "next_offset": end,
                    "eof": end == len(self.export_raw)}
        elif op == "begin":
            self.begin = copy.deepcopy(params)
            assert params["expected_revision"] == digest(self.export_raw)
            assert set(params["required_kinds"]) <= set(self.kinds)
            data = {"operation_id": params["operation_id"], "transaction_id": TRANSACTION,
                    "expected_revision": params["expected_revision"], "context_revision": params["expected_context_revision"],
                    "document_sha256": params["document_sha256"], "size_bytes": params["size_bytes"],
                    "chunk_count": params["chunk_count"], "next_seq": 0, "received_bytes": 0,
                    "idle_expires_at": "2099-01-01T00:01:00Z", "absolute_expires_at": "2099-01-01T00:10:00Z",
                    "required_kinds": params["required_kinds"]}
        elif op == "append":
            assert params["transaction_id"] == TRANSACTION and params["offset"] == len(self.upload)
            part = base64.b64decode(params["data_base64"], validate=True)
            assert digest(part) == params["chunk_sha256"]
            self.upload.extend(part)
            data = {"operation_id": self.begin["operation_id"], "transaction_id": TRANSACTION,
                    "seq": params["seq"], "offset": params["offset"], "chunk_sha256": params["chunk_sha256"],
                    "next_seq": params["seq"] + 1, "received_bytes": len(self.upload), "duplicate": False}
        elif op == "preview":
            assert len(self.upload) == self.begin["size_bytes"] and digest(self.upload) == self.begin["document_sha256"]
            incoming = document.load_document(bytes(self.upload))
            assert document.required_locator_kinds(incoming) == self.begin["required_kinds"]
            self.plan = document.plan_documents(self.current, [incoming], self.begin["mode"])
            summary = self.plan["summary"]
            counts = {key: summary[key] for key in ("before", "after", "added", "removed", "retained", "moved")}
            counts.update(updated=summary["title_changes"], duplicates_skipped=summary["skipped_duplicates"])
            start, total = params["page_offset"], summary["after"]
            end = min(total, start + params["max_entries"])
            data = {"operation_id": self.begin["operation_id"], "transaction_id": TRANSACTION,
                    "expected_revision": self.begin["expected_revision"], "context_revision": self.begin["expected_context_revision"],
                    "input_document_sha256": self.begin["document_sha256"],
                    "result_document_sha256": digest(document.canonical_bytes(self.plan["document"])),
                    "plan_digest": "3" * 64, "counts": counts,
                    "resolution_counts": {"resolved": total, "missing": 0, "ambiguous": 0, "unsupported": 0},
                    "ready": True, "blocker": None, "page_offset": start,
                    "rows": [{"entry_index": index, "state": "resolved"} for index in range(start, end)],
                    "next_offset": None if end == total else end, "required_kinds": self.begin["required_kinds"]}
            self.preview = copy.deepcopy(data)
        elif op == "commit":
            assert "saved" in self.events and params["plan_digest"] == "3" * 64
            if self.before_commit:
                self.before_commit()
            after = digest(document.canonical_bytes(self.plan["document"]))
            self.receipt = {"version": 1, "subject": "global", "operation_id": self.begin["operation_id"],
                            "operation_runtime": self.begin["runtime"], "transaction_id": TRANSACTION,
                            "input_document_sha256": self.begin["document_sha256"],
                            "result_document_sha256": self.preview["result_document_sha256"],
                            "expected_revision": self.begin["expected_revision"], "new_revision": after,
                            "context_revision": self.begin["expected_context_revision"], "plan_digest": "3" * 64,
                            "counts": self.preview["counts"], "changed": after != self.begin["expected_revision"],
                            "outcome": "verified", "persistence": "persistent", "required_kinds": self.begin["required_kinds"]}
            self.current = copy.deepcopy(self.plan["document"])
            data = {"operation_id": params["operation_id"], "transaction_id": TRANSACTION,
                    "receipt": copy.deepcopy(self.receipt)}
        elif op == "status":
            if self.receipt:
                data = {"operation_id": params["operation_id"], "operation_runtime": self.receipt["operation_runtime"],
                        "transaction_id": TRANSACTION, "state": "committed", "outcome": "verified",
                        "receipt": copy.deepcopy(self.receipt)}
            else:
                data = {"operation_id": params["operation_id"], "operation_runtime": None, "transaction_id": None,
                        "state": "not_found", "outcome": "unknown", "receipt": None}
        elif op == "abort":
            assert self.receipt is None
            data = {"operation_id": params["operation_id"], "transaction_id": params["transaction_id"],
                    "state": "aborted", "outcome": "not_applied"}
        else:
            raise AssertionError(op)
        result = {**common, **data}
        if op in self.mutators:
            result = self.mutators[op](result, params)
        return result

    def api(self, path, payload=None, timeout=None):
        assert payload is not None
        sent = json.loads(encoded(payload))
        assert set(sent) == {"action", "params"} and sent["action"] == "favorites_global"
        assert len(encoded(sent)) <= 16384
        params = remote.protocol.validate_request(sent["params"])
        op = params["op"]
        request_id = f"{len(self.responses) + 1:032x}"
        self.calls.append({"method": "POST", "op": op, "at": self.clock(), "timeout": timeout, "params": params})
        self.events.append(op)
        data = self.data(params)
        self.responses[request_id] = (op, {"id": request_id, "status": "rejected" if "error" in data else "ok", "data": data})
        if op in self.post_effects:
            self.post_effects[op](self, params)
        return 202, {"id": request_id, "status": "queued"}

    def api_raw(self, path, payload=None, timeout=None):
        assert payload is None
        request_id = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["id"][0]
        op, response = self.responses[request_id]
        self.calls.append({"method": "GET", "op": op, "at": self.clock(), "timeout": timeout})
        effect = self.get_effects.get(op)
        if effect:
            return effect(self, response)
        return 200, encoded(response)

    def posts(self):
        return [call["op"] for call in self.calls if call["method"] == "POST"]


class GlobalFavoritesRemoteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.api = MemoryAPI(self.clock)
        self.saved = []

    def save(self, record):
        self.saved.append(remote.validate_recovery_record(record))
        self.api.events.append("saved")
        return True

    def client(self, **overrides):
        options = {"runtime": "fixture-runtime", "save_context": self.save, "timeout": 45,
                   "monotonic": self.clock, "sleep": self.clock.sleep, "uuid_factory": lambda: OPERATION}
        options.update(overrides)
        return remote.RemoteGlobalFavorites(self.api, "device-1", **options)

    def error(self, code, callback, *args, **kwargs):
        with self.assertRaises(remote.RemoteError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_real_eight_op_flow_and_context_precedes_only_commit(self):
        client = self.client()
        snapshot = client.export()
        result = client.apply(snapshot, doc(row()))
        self.assertTrue(result.verified)
        self.assertEqual(result.outcome, "verified")
        self.assertEqual(self.api.posts(), ["describe", "export", "describe", "begin", "append", "preview", "commit"])
        self.assertEqual(self.api.events[-2:], ["saved", "commit"])
        self.assertEqual(self.api.current, doc(row()))
        self.assertEqual(len(self.saved), 1)
        self.assertNotIn("data_base64", json.dumps(self.saved))
        times = [call["at"] for call in self.api.calls if call["method"] == "POST"]
        self.assertTrue(all(b - a >= 1.5 for a, b in pairwise(times)))
        self.assertTrue(all(b["at"] - a["at"] >= 0.5 for a, b in pairwise(self.api.calls)))
        self.assertTrue(all(0 < call["timeout"] <= 10 for call in self.api.calls))
        self.assertLess(self.clock(), 145)

    def test_complete_export_upload_and_preview_pages_preserve_order(self):
        self.api.current = doc(*(row(n, title=f"Old {n}") for n in range(1, 9)))
        self.api.limits.update(max_export_bytes=256, max_append_bytes=256, max_preview_rows=2)
        original = copy.deepcopy(self.api.current)
        client = self.client(timeout=300)
        snapshot = client.export()
        self.assertEqual(snapshot.document, original)
        self.assertGreater(self.api.posts().count("export"), 1)
        incoming = doc(*(row(n) for n in range(12, 20)))
        result = client.apply(snapshot, incoming, "replace")
        self.assertTrue(result.verified)
        self.assertEqual(bytes(self.api.upload), document.canonical_bytes(incoming))
        self.assertEqual(self.api.current, incoming)
        self.assertGreater(self.api.posts().count("append"), 1)
        self.assertEqual(self.api.posts().count("preview"), 4)
        offsets = [call["params"]["page_offset"] for call in self.api.calls
                   if call["method"] == "POST" and call["op"] == "preview"]
        self.assertEqual(offsets, [0, 2, 4, 6])

    def test_begin_uses_fresh_player_minted_id_never_caller_id(self):
        client = self.client(uuid_factory=lambda: self.fail("caller must not mint admission"))
        snapshot = client.export()
        minted = "40000000-0000-4000-8000-000000000004"
        self.api.mutators["describe"] = lambda data, _: {**data, "admission": {"operation_id": minted, "ttl_ms": 10000}}
        result = client.apply(snapshot, doc(row()))
        self.assertTrue(result.verified)
        self.assertEqual(result.operation_id, minted)
        self.assertEqual(self.api.begin["operation_id"], minted)

    def test_changed_context_after_export_rejects_before_begin(self):
        client = self.client()
        snapshot = client.export()
        self.api.mutators["describe"] = lambda data, _: {**data, "context_revision": "a" * 64}
        result = client.apply(snapshot, doc(row()))
        self.assertEqual((result.outcome, result.reason), ("not_applied", "invalid_snapshot"))
        self.assertNotIn("begin", self.api.posts())
        self.assertEqual(self.saved, [])

    def test_changed_portable_snapshot_after_export_rejects_before_begin(self):
        client = self.client()
        snapshot = client.export()
        self.api.current = doc(row(9))
        result = client.apply(snapshot, doc(row()))
        self.assertEqual(result.reason, "invalid_snapshot")
        self.assertNotIn("begin", self.api.posts())

    def test_kind_lost_on_fresh_describe_rejects_before_begin(self):
        client = self.client()
        snapshot = client.export()
        self.api.kinds.remove("plex-media")
        result = client.apply(snapshot, doc(row(kind="plex-media")))
        self.assertEqual(result.reason, "invalid_document")
        self.assertNotIn("begin", self.api.posts())

    def test_admission_deadline_starts_before_refresh_round_trip(self):
        client = self.client()
        snapshot = client.export()
        def slow_refresh(_, response):
            self.clock.sleep(10)
            return 200, encoded(response)
        self.api.get_effects["describe"] = slow_refresh
        result = client.apply(snapshot, doc(row()))
        self.assertEqual((result.outcome, result.reason), ("not_applied", "deadline"))
        self.assertNotIn("begin", self.api.posts())

    def test_local_expected_hash_mismatch_aborts_without_save_or_commit(self):
        self.api.mutators["preview"] = lambda data, _: {**data, "result_document_sha256": "a" * 64}
        client = self.client()
        result = client.apply(client.export(), doc(row()))
        self.assertEqual((result.outcome, result.cleanup), ("not_applied", "aborted"))
        self.assertEqual(result.reason, "invalid_response")
        self.assertNotIn("commit", self.api.posts())
        self.assertEqual(self.saved, [])
        self.assertEqual(self.api.current, doc())

    def test_local_expected_counts_mismatch_is_rejected_even_when_typed(self):
        self.api.current = doc(row())
        self.api.mutators["preview"] = lambda data, _: {**data, "counts": {**data["counts"], "updated": 1}}
        client = self.client()
        result = client.apply(client.export(), doc(row()))
        self.assertEqual(result.reason, "invalid_response")
        self.assertEqual(self.api.posts()[-1], "abort")
        self.assertNotIn("commit", self.api.posts())
        self.assertEqual(self.saved, [])

    def test_missing_negotiated_kind_refuses_before_begin(self):
        self.api.kinds.remove("plex-media")
        client = self.client()
        snapshot = client.export()
        self.error("invalid_document", client.apply, snapshot, doc(row(kind="plex-media")))
        self.assertEqual(self.api.posts(), ["describe", "export"])
        self.assertEqual(self.saved, [])

    def test_45_second_budget_refuses_large_document_before_begin(self):
        incoming = doc(*(row(n, title="T" * 800) for n in range(1, 201)))
        self.assertGreater(len(document.canonical_bytes(incoming)), 100000)
        client = self.client()
        snapshot = client.export()
        self.error("deadline_insufficient", client.apply, snapshot, incoming)
        self.assertEqual(self.api.posts(), ["describe", "export"])

    def test_expired_export_is_not_partial_success_or_admission(self):
        self.api.current = doc(row(), row(2))
        self.api.limits["max_export_bytes"] = 128
        exports = 0

        def expire_after_first(data, _):
            nonlocal exports
            exports += 1
            if exports == 1:
                self.api.error_ops["export"] = "expired"
            return data

        self.api.mutators["export"] = expire_after_first
        self.error("player_rejected", self.client().export)
        self.assertEqual(self.api.posts(), ["describe", "export", "export"])
        self.assertEqual(self.saved, [])

    def test_export_full_hash_is_checked_after_valid_chunk_hash(self):
        def substitute(data, _):
            part = base64.b64decode(data["data_base64"])
            changed = part.replace(b"Favorite", b"Impostor")
            self.assertEqual(len(changed), len(part))
            return {**data, "data_base64": base64.b64encode(changed).decode(), "chunk_sha256": digest(changed)}

        self.api.current = doc(row())
        self.api.mutators["export"] = substitute
        self.error("invalid_response", self.client().export)
        self.assertNotIn("begin", self.api.posts())

    def test_short_contiguous_export_pages_are_valid_below_maximum(self):
        self.api.current = doc(row(), row(2))
        self.api.limits["max_export_bytes"] = 1024
        self.api.export_page_bytes = 128
        snapshot = self.client(timeout=300).export()
        self.assertEqual(snapshot.document, self.api.current)
        self.assertGreater(self.api.posts().count("export"), 1)

    def test_blocked_preview_discards_without_retaining_or_mutation(self):
        self.api.mutators["preview"] = lambda data, _: {**data, "ready": False, "blocker": "policy_denied"}
        client = self.client()
        result = client.preview(client.export(), doc(row()))
        self.assertIs(result.ready, False)
        self.assertEqual(result.cleanup, "aborted")
        self.assertEqual(self.saved, [])
        self.assertEqual(self.api.current, doc())
        self.assertNotIn("commit", self.api.posts())

    def test_apply_blocked_preview_aborts_before_context_save(self):
        self.api.mutators["preview"] = lambda data, _: {**data, "ready": False, "blocker": "policy_denied"}
        client = self.client()
        result = client.apply(client.export(), doc(row()))
        self.assertEqual((result.outcome, result.reason, result.cleanup), ("not_applied", "not_ready", "aborted"))
        self.assertEqual(self.saved, [])
        self.assertNotIn("commit", self.api.posts())

    def test_lost_commit_enqueue_ack_uses_one_status_never_recommit(self):
        def lost_ack(*_):
            raise OSError(PRIVATE)

        self.api.post_effects["commit"] = lost_ack
        client = self.client()
        result = client.apply(client.export(), doc(row()))
        self.assertTrue(result.verified)
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts().count("status"), 1)
        self.assertNotIn("abort", self.api.posts())
        self.assertEqual(self.api.current, doc(row()))

    def test_lost_commit_receipt_polls_until_reserved_status_budget(self):
        self.api.get_effects["commit"] = lambda *_: (202, encoded({"status": "pending"}))
        client = self.client()
        result = client.apply(client.export(), doc(row()))
        self.assertTrue(result.verified)
        self.assertEqual(self.api.posts().count("commit"), 1)
        self.assertEqual(self.api.posts().count("status"), 1)
        self.assertLess(self.clock(), 145)

    def test_save_failure_discards_with_no_commit_and_no_private_error(self):
        def cannot_save(_):
            raise OSError(PRIVATE)

        client = self.client(save_context=cannot_save)
        result = client.apply(client.export(), doc(row()))
        self.assertEqual((result.outcome, result.reason, result.cleanup),
                         ("not_applied", "context_not_saved", "aborted"))
        self.assertNotIn("commit", self.api.posts())
        self.assertNotIn(PRIVATE, str(result.summary()))

    def test_restart_status_reads_only_and_keeps_original_runtime(self):
        client = self.client()
        self.assertTrue(client.apply(client.export(), doc(row())).verified)
        original = copy.deepcopy(self.saved[0])
        before = len(self.api.calls)
        restarted = self.client(runtime="new-runtime")
        result = restarted.reconcile(original)
        self.assertTrue(result.verified)
        self.assertEqual([call["op"] for call in self.api.calls[before:] if call["method"] == "POST"], ["status"])
        self.assertEqual(result.result.data["operation_runtime"], "fixture-runtime")
        self.assertEqual(original, self.saved[0])

    def test_unknown_status_never_replays_or_discards_a_committed_write(self):
        client = self.client()
        self.assertTrue(client.apply(client.export(), doc(row())).verified)
        self.api.receipt = None
        before = len(self.api.calls)
        result = self.client(runtime="new-runtime").reconcile(self.saved[0])
        self.assertEqual((result.outcome, result.verified), ("unknown", False))
        self.assertEqual([call["op"] for call in self.api.calls[before:] if call["method"] == "POST"], ["status"])
        self.assertEqual(self.api.current, doc(row()))

    def test_status_only_reader_cannot_stage_or_describe(self):
        writer = self.client()
        self.assertTrue(writer.apply(writer.export(), doc(row())).verified)
        before = len(self.api.posts())
        reader = remote.StatusReader(self.api, "device-1", runtime="new-runtime",
                                     monotonic=self.clock, sleep=self.clock.sleep)
        self.assertTrue(reader.reconcile(self.saved[0]).verified)
        self.error("invalid_configuration", reader.describe)
        self.assertEqual(self.api.posts()[before:], ["status"])

    def test_separate_private_context_store_and_existing_store_remain_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            old_dir, global_dir = base / "scoped", base / "global"
            old_dir.mkdir(mode=0o700)
            global_dir.mkdir(mode=0o700)
            legacy = old_operations.ContextStore(old_dir, server=self.api.server, device="device-1",
                                                 validate_context=copy.deepcopy)
            legacy_record = {"version": 1, "operation_id": OPERATION, "legacy": True}
            old_path = Path(legacy.save(legacy_record)["path"])
            old_bytes = old_path.read_bytes()
            store = remote.ContextStore(global_dir, server=self.api.server, device="device-1")

            def save(record):
                store.save(record)
                self.assertEqual(store.load(OPERATION), record)
                self.api.events.append("saved")
                return True

            self.api.before_commit = lambda: self.assertEqual(store.load(OPERATION)["operation_id"], OPERATION)
            client = self.client(save_context=save)
            result = client.apply(client.export(), doc(row()))
            self.assertTrue(result.verified)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            self.assertEqual(legacy.load(OPERATION), legacy_record)
            self.assertIs(old_operations.ContextStore._protocol, old_protocol)
            self.assertIs(remote.ContextStore._protocol, remote.protocol)
            store.mark_completed(OPERATION, result.result)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            before = len(self.api.calls)
            with self.assertRaises(old_operations.OperationStoreError) as caught:
                remote.ContextStore(global_dir, server=self.api.server, device="foreign").load(OPERATION)
            self.assertEqual(caught.exception.code, "wrong_target")
            self.assertEqual(len(self.api.calls), before)
            self.assertEqual(len(list(global_dir.glob("*.json"))), 1)
            if os.name != "nt":
                self.assertEqual(next(global_dir.glob("*.json")).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
