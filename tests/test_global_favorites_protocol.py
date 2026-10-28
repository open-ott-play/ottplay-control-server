import base64
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
    spec = importlib.util.spec_from_file_location("global_protocol_test", ROOT / "cli/global_favorites_protocol.py")
    protocol = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = protocol
    spec.loader.exec_module(protocol)
codec = protocol.document_codec
RID = "a" * 32
RUNTIME = "fixture-runtime"
OPID = "10000000-0000-4000-8000-000000000001"
TXID = "20000000-0000-4000-8000-000000000002"
SNAPID = "30000000-0000-4000-8000-000000000003"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def common(op, **fields):
    return {"version": 1, "runtime": RUNTIME, "op": op, "subject": "global", **fields}


class GlobalFavoritesProtocolTests(unittest.TestCase):
    def setUp(self):
        self.document = {"version": 1, "entries": [
            {"title": "News😀", "origin": {"provider": "m3u", "profile": "TV", "subprofile": None, "sourceAlias": "Home"},
             "locator": {"kind": "channel-name", "name": "News HD", "group": None}},
            {"title": "Film", "origin": {"provider": "plex", "profile": "Cinema", "subprofile": None, "sourceAlias": "PMS"},
             "locator": {"kind": "plex-media", "serverId": "PMS-1", "ratingKey": "42"}}]}
        self.raw = codec.canonical_bytes(self.document)
        self.kinds = codec.required_locator_kinds(self.document)
        self.counts = {"before": 1, "after": 2, "added": 1, "removed": 0, "retained": 1,
                       "updated": 1, "moved": 1, "duplicates_skipped": 0}
        self.requests = {
            "describe": {"op": "describe", "runtime": RUNTIME},
            "export": {"op": "export", "runtime": RUNTIME, "snapshot_id": SNAPID, "offset": 0, "max_bytes": 65536},
            "begin": {"op": "begin", "runtime": RUNTIME, "operation_id": OPID, "mode": "replace",
                      "expected_revision": "1" * 64, "expected_context_revision": "2" * 64,
                      "document_sha256": sha(self.raw), "size_bytes": len(self.raw), "chunk_count": 1,
                      "budget_ms": 45000, "required_kinds": self.kinds},
            "append": {"op": "append", "runtime": RUNTIME, "transaction_id": TXID, "seq": 0, "offset": 0,
                       "chunk_sha256": sha(self.raw), "data_base64": base64.b64encode(self.raw).decode()},
            "preview": {"op": "preview", "runtime": RUNTIME, "transaction_id": TXID, "page_offset": 0, "max_entries": 1},
            "commit": {"op": "commit", "runtime": RUNTIME, "transaction_id": TXID, "operation_id": OPID, "plan_digest": "3" * 64},
            "status": {"op": "status", "runtime": RUNTIME, "operation_id": OPID},
            "abort": {"op": "abort", "runtime": RUNTIME, "transaction_id": TXID, "operation_id": OPID},
        }
        self.receipt = {"version": 1, "subject": "global", "operation_id": OPID, "operation_runtime": RUNTIME,
                        "transaction_id": TXID, "input_document_sha256": sha(self.raw), "result_document_sha256": sha(self.raw),
                        "expected_revision": "1" * 64, "new_revision": "4" * 64, "context_revision": "2" * 64,
                        "plan_digest": "3" * 64, "counts": self.counts, "changed": True, "outcome": "verified",
                        "persistence": "persistent", "required_kinds": self.kinds}
        self.responses = {
            "describe": common("describe", revision="1" * 64, context_revision="2" * 64, write_ready=True,
                               admission={"operation_id": OPID, "ttl_ms": 10000},
                               write_blocker=None, export={"snapshot_id": SNAPID, "document_sha256": sha(self.raw),
                                                         "size_bytes": len(self.raw), "entries": 2,
                                                         "expires_at": "2026-10-28T23:00:00Z"},
                               export_error=None, limits=dict(protocol.LIMITS), locator_kinds=list(protocol.KINDS)),
            "export": common("export", snapshot_id=SNAPID, revision="1" * 64, document_sha256=sha(self.raw),
                             size_bytes=len(self.raw), offset=0, chunk_sha256=sha(self.raw),
                             data_base64=base64.b64encode(self.raw).decode(), next_offset=len(self.raw), eof=True),
            "begin": common("begin", operation_id=OPID, transaction_id=TXID, expected_revision="1" * 64,
                            context_revision="2" * 64, document_sha256=sha(self.raw), size_bytes=len(self.raw), chunk_count=1,
                            next_seq=0, received_bytes=0, idle_expires_at="2026-10-28T22:02:00Z",
                            absolute_expires_at="2026-10-28T22:10:00Z", required_kinds=self.kinds),
            "append": common("append", operation_id=OPID, transaction_id=TXID, seq=0, offset=0,
                             chunk_sha256=sha(self.raw), next_seq=1, received_bytes=len(self.raw), duplicate=False),
            "preview": common("preview", operation_id=OPID, transaction_id=TXID, expected_revision="1" * 64,
                              context_revision="2" * 64, input_document_sha256=sha(self.raw), result_document_sha256=sha(self.raw),
                              plan_digest="3" * 64, counts=self.counts,
                              resolution_counts={"resolved": 1, "missing": 1, "ambiguous": 0, "unsupported": 0},
                              ready=True, blocker=None, page_offset=0, rows=[{"entry_index": 0, "state": "resolved"}],
                              next_offset=1, required_kinds=self.kinds),
            "commit": common("commit", operation_id=OPID, transaction_id=TXID, receipt=self.receipt),
            "status": common("status", operation_id=OPID, operation_runtime=RUNTIME, transaction_id=TXID,
                             state="committed", outcome="verified", receipt=self.receipt),
            "abort": common("abort", operation_id=OPID, transaction_id=TXID, state="aborted", outcome="not_applied"),
        }

    def result(self, op, data=None, params=None, **kwargs):
        return protocol.validate_response(params or self.requests[op],
                                          encoded({"id": RID, "status": "ok", "data": data or self.responses[op]}),
                                          request_id=RID, **kwargs)

    def context(self, preview=None):
        return protocol.retain_context(self.result("begin"), self.result("preview", data=preview))

    def error(self, function, *args, **kwargs):
        with self.assertRaises(protocol.ProtocolError) as caught:
            function(*args, **kwargs)
        self.assertNotIn("fixture-secret", str(caught.exception))
        return caught.exception

    def test_all_eight_requests_and_successful_responses(self):
        for op in self.requests:
            with self.subTest(op=op):
                self.assertEqual(protocol.validate_request(self.requests[op]), self.requests[op])
                result = self.result(op)
                self.assertEqual(result.data, self.responses[op])
                self.assertFalse(result.verified)
                changed = result.data
                changed["subject"] = "fixture-secret"
                self.assertEqual(result.data, self.responses[op])

    def test_schema_shapes_and_count_names_match_frozen_repository_schema(self):
        schema = json.loads((ROOT / "docs/design/global-favorites-v1.schema.json").read_bytes())["$defs"]
        self.assertEqual(set(schema["counts"]["required"]), set(protocol.COUNT_FIELDS))
        self.assertEqual(schema["locator_kinds"]["maxItems"], 5)
        self.assertEqual(schema["locator_kinds"]["items"]["enum"], list(protocol.KINDS))
        for op in self.requests:
            self.assertEqual(set(schema[op + "_request"]["required"]), set(self.requests[op]))
            self.assertEqual(set(schema[op + "_response"]["required"]), set(self.responses[op]))

    def test_capability_and_exact_shapes_fail_closed(self):
        self.assertEqual(protocol.validate_capability({"version": 1}), {"version": 1})
        for value in ({"version": True}, {"version": 2}, {"version": 1, "scope": "global"}, None):
            self.error(protocol.validate_capability, value)
        for op in self.requests:
            value = copy.deepcopy(self.requests[op])
            value["scope"] = {"provider": "plex", "profileBindingId": OPID}
            self.error(protocol.validate_request, value)
            data = copy.deepcopy(self.responses[op])
            data["extra"] = "fixture-secret"
            self.error(self.result, op, data=data)
            data = copy.deepcopy(self.responses[op])
            data["subject"] = "plex"
            self.error(self.result, op, data=data)

    def test_ids_runtime_and_request_integer_boundaries(self):
        for runtime in ("X", "a" * 65, "bad\n", "https://fixture-secret", None):
            self.error(protocol.validate_request, {"op": "describe", "runtime": runtime})
        for invalid in ("A" + OPID[1:], OPID.replace("4000", "3000"), "fixture-secret", "a" * 32):
            value = copy.deepcopy(self.requests["begin"])
            value["operation_id"] = invalid
            self.error(protocol.validate_request, value)
        for key, value in (("size_bytes", True), ("size_bytes", 1048577), ("chunk_count", 0),
                           ("chunk_count", 257), ("budget_ms", 600001), ("budget_ms", 0), ("budget_ms", 1.0)):
            params = copy.deepcopy(self.requests["begin"])
            params[key] = value
            self.error(protocol.validate_request, params)
        for kinds in (["plex-media", "channel-name"], ["channel", "channel"], ["future"], [True]):
            params = copy.deepcopy(self.requests["begin"])
            params["required_kinds"] = kinds
            self.error(protocol.validate_request, params)

    def test_request_and_response_identity_correlation(self):
        for key, value in (("id", "b" * 32), ("status", "accepted")):
            response = {"id": RID, "status": "ok", "data": self.responses["describe"]}
            response[key] = value
            self.error(protocol.validate_response, self.requests["describe"], encoded(response), request_id=RID)
        for op in self.responses:
            for key, value in (("runtime", "other-runtime"), ("op", "describe" if op != "describe" else "status")):
                data = copy.deepcopy(self.responses[op])
                data[key] = value
                self.error(self.result, op, data=data)
        self.error(protocol.validate_response, self.requests["describe"],
                   encoded({"id": RID, "status": "ok", "data": self.responses["describe"]}), request_id="not-an-id")

    def test_negative_responses_are_exact_allowlisted_and_never_verified(self):
        for op in self.requests:
            for status in ("rejected", "unsupported"):
                for code in protocol.ERRORS:
                    result = protocol.validate_response(self.requests[op],
                        encoded({"id": RID, "status": status, "data": common(op, error=code)}), request_id=RID)
                    self.assertFalse(result.verified)
            for error in ("fixture-secret", {"message": "fixture-secret"}, None):
                self.error(protocol.validate_response, self.requests[op],
                           encoded({"id": RID, "status": "rejected", "data": common(op, error=error)}), request_id=RID)
            self.error(self.result, op, data=common(op, error="invalid_request"))

    def test_strict_raw_json_and_original_metadata_padding_limits(self):
        request = self.requests["describe"]
        response = encoded({"id": RID, "status": "ok", "data": self.responses["describe"]})
        for invalid in (response.replace(b'"id":', b'"id":"' + RID.encode() + b'","id":', 1),
                        response.replace(b'"version":1', b'"version":1,"version":1', 1),
                        response.replace(b'"version":1', b'"version":NaN', 1),
                        response.replace(b'"subject":"global"', b'"subject":"\\ud800"'),
                        b'\xef\xbb\xbf' + response, response + b'\xff', b'{"x":' * 17 + b'0' + b'}' * 17):
            self.error(protocol.validate_response, request, invalid, request_id=RID)
        data = encoded(self.responses["describe"])
        padded = b'{"id":"' + RID.encode() + b'","status":"ok","data":{' + b' ' * 13 + data[1:] + b'}'
        limit = len(data) + 13
        self.assertEqual(protocol.validate_response(request, padded, request_id=RID, max_metadata_bytes=limit).data,
                         self.responses["describe"])
        self.error(protocol.validate_response, request, padded, request_id=RID, max_metadata_bytes=limit - 1)
        error = encoded(common("describe", error="unsupported"))
        response = b'{"id":"' + RID.encode() + b'","status":"unsupported","data":{' + b' ' * 100 + error[1:] + b'}'
        self.error(protocol.validate_response, request, response, request_id=RID, max_metadata_bytes=len(error))

    def test_describe_limits_readiness_and_export_pairing(self):
        for name, ceiling in protocol.LIMITS.items():
            for value in (0, ceiling + 1, True):
                data = copy.deepcopy(self.responses["describe"])
                data["limits"][name] = value
                self.error(self.result, "describe", data=data)
        for changes in ({"write_ready": False}, {"write_blocker": "busy"}, {"export": None},
                        {"export_error": "unportable_export"}, {"locator_kinds": ["future"]}):
            self.error(self.result, "describe", data={**self.responses["describe"], **changes})
        data = copy.deepcopy(self.responses["describe"])
        data.update(write_ready=False, write_blocker="policy_denied", admission=None,
                    export=None, export_error="unportable_export")
        self.assertFalse(self.result("describe", data=data).data["write_ready"])

    def test_describe_admission_is_exact_and_required_only_when_ready(self):
        ready = self.responses["describe"]
        for admission in (None, True, [], {}, {"operation_id": OPID}, {"ttl_ms": 10000},
                          {"operation_id": OPID, "ttl_ms": 10000, "extra": "fixture-secret"},
                          {"operation_id": OPID.replace("4000", "3000"), "ttl_ms": 10000}):
            with self.subTest(admission=admission):
                self.error(self.result, "describe", data={**ready, "admission": admission})
        for ttl in (True, False, None, "10000", 10000.0, 0, 9999, 10001):
            with self.subTest(ttl=ttl):
                self.error(self.result, "describe", data={**ready, "admission": {"operation_id": OPID, "ttl_ms": ttl}})
        missing = dict(ready)
        del missing["admission"]
        self.error(self.result, "describe", data=missing)
        for blocker in protocol.WRITE_BLOCKERS:
            blocked = {**ready, "write_ready": False, "write_blocker": blocker, "admission": None}
            self.assertIsNone(self.result("describe", data=blocked).data["admission"])
            self.error(self.result, "describe", data={**blocked, "admission": ready["admission"]})

    def test_begin_negotiation_requires_minted_id_and_complete_context(self):
        described = self.result("describe")
        begin = self.requests["begin"]
        self.assertEqual(protocol.validate_begin_negotiation(begin, described), begin)
        self.assertEqual(protocol.validate_request(begin, describe=described), begin)
        self.error(protocol.validate_begin_negotiation, begin, None)
        self.error(protocol.validate_begin_negotiation, begin, described.data)
        self.error(protocol.validate_begin_negotiation, begin, self.result("begin"))
        self.error(protocol.validate_begin_negotiation, self.requests["status"], described)
        for changes in ({"operation_id": SNAPID}, {"runtime": "fresh-runtime"},
                        {"expected_revision": "f" * 64}, {"expected_context_revision": "f" * 64}):
            with self.subTest(changes=changes):
                self.error(protocol.validate_begin_negotiation, {**begin, **changes}, described)
        for changes in ({"admission": {"operation_id": SNAPID, "ttl_ms": 10000}},
                        {"runtime": "fresh-runtime"}, {"revision": "f" * 64},
                        {"context_revision": "f" * 64}, {"locator_kinds": ["plex-media"]},
                        {"write_ready": False, "write_blocker": "busy", "admission": None}):
            with self.subTest(changes=changes):
                data = {**self.responses["describe"], **changes}
                other = self.result("describe", data=data,
                                    params={**self.requests["describe"], "runtime": data["runtime"]})
                self.error(protocol.validate_begin_negotiation, begin, other)
                self.error(self.result, "begin", describe=other)
        # A matching forged begin ACK cannot legitimize an ID absent from the grant.
        self.error(self.result, "begin", params={**begin, "operation_id": SNAPID},
                   data={**self.responses["begin"], "operation_id": SNAPID}, describe=described)

    def test_begin_negotiation_uses_lower_limits_and_copied_grant(self):
        begin = self.requests["begin"]
        for limits in ({"max_document_bytes": len(self.raw) - 1},
                       {"max_append_bytes": len(self.raw) - 1},
                       {"absolute_ttl_seconds": 44, "idle_ttl_seconds": 44},
                       {"max_envelope_bytes": 1}):
            with self.subTest(limits=limits):
                data = copy.deepcopy(self.responses["describe"])
                data["limits"].update(limits)
                # Export availability is independent of admission; the old snapshot
                # cannot exceed newly advertised limits in this synthetic response.
                data.update(export=None, export_error="unportable_export")
                described = self.result("describe", data=data)
                self.error(protocol.validate_begin_negotiation, begin, described)
        data = copy.deepcopy(self.responses["describe"])
        data["limits"]["max_chunks"] = 1
        described = self.result("describe", data=data)
        self.error(protocol.validate_begin_negotiation, {**begin, "chunk_count": 2}, described)
        data["admission"]["operation_id"] = SNAPID
        outward = described.data
        outward["admission"]["operation_id"] = SNAPID
        normalized = protocol.validate_begin_negotiation(begin, described)
        normalized["operation_id"] = SNAPID
        self.assertEqual(described.data["admission"]["operation_id"], OPID)
        self.assertEqual(begin["operation_id"], OPID)

    def test_begin_echo_timestamps_and_fresh_describe_binding(self):
        described = self.result("describe")
        self.result("begin", describe=described)
        for key, value in (("required_kinds", ["plex-media"]), ("next_seq", 1), ("received_bytes", 1),
                           ("size_bytes", True), ("context_revision", "f" * 64), ("operation_id", SNAPID)):
            self.error(self.result, "begin", data={**self.responses["begin"], key: value})
        data = copy.deepcopy(self.responses["begin"])
        data.update(idle_expires_at="2026-10-28T22:02:00.000000002Z", absolute_expires_at="2026-10-28T22:02:00.000000001Z")
        self.error(self.result, "begin", data=data)
        for change in ({"locator_kinds": ["plex-media"]}, {"revision": "f" * 64},
                       {"context_revision": "f" * 64}, {"write_ready": False, "write_blocker": "busy", "admission": None}):
            other = self.result("describe", data={**self.responses["describe"], **change})
            self.error(self.result, "begin", describe=other)

    def test_canonical_chunk_encoding_hash_and_duplicate_ack(self):
        context = protocol.retain_context(self.result("begin"))
        self.result("append", context=context)
        self.result("append", data={**self.responses["append"], "duplicate": True}, context=context)
        for change in ({"next_seq": 2}, {"received_bytes": len(self.raw) - 1}, {"operation_id": SNAPID},
                       {"chunk_sha256": "e" * 64}, {"duplicate": 1}):
            self.error(self.result, "append", data={**self.responses["append"], **change}, context=context)
        request = {**self.requests["append"], "data_base64": "YQ==", "chunk_sha256": sha(b"a")}
        protocol.validate_request(request)
        for change in ({"data_base64": "YR=="}, {"data_base64": "YQ==\n"}, {"data_base64": ""},
                       {"chunk_sha256": sha(b"b")}, {"offset": 1048576}, {"seq": 256}):
            self.error(protocol.validate_request, {**request, **change})
        huge = b"x" * 8193
        self.error(protocol.validate_request, {**request, "data_base64": base64.b64encode(huge).decode(), "chunk_sha256": sha(huge)})

    def test_retained_context_roundtrip_and_all_receipt_bindings(self):
        context = self.context()
        record = protocol.export_context(context)
        restored = protocol.restore_context(encoded(record))
        self.assertTrue(self.result("commit", context=restored).verified)
        record["begin"]["data"]["transaction_id"] = SNAPID
        self.error(protocol.restore_context, record)
        self.assertTrue(self.result("commit", context=context).verified)
        self.assertFalse(self.result("commit").verified)
        for key, value in (("input_document_sha256", "e" * 64), ("result_document_sha256", "e" * 64),
                           ("expected_revision", "e" * 64), ("context_revision", "e" * 64),
                           ("operation_id", SNAPID), ("transaction_id", SNAPID),
                           ("operation_runtime", "other-runtime"), ("required_kinds", ["plex-media"]),
                           ("plan_digest", "e" * 64)):
            data = copy.deepcopy(self.responses["commit"])
            data["receipt"][key] = value
            self.error(self.result, "commit", data=data, context=context)
        data = copy.deepcopy(self.responses["commit"])
        data["receipt"]["counts"]["moved"] = 0
        self.error(self.result, "commit", data=data, context=context)
        self.assertNotEqual(self.receipt["new_revision"], self.receipt["result_document_sha256"])

    def test_blocked_preview_can_be_inspected_but_cannot_verify_commit(self):
        blocked = {**self.responses["preview"], "ready": False, "blocker": "context_changed"}
        context = self.context(blocked)
        self.assertFalse(protocol.export_context(context)["preview"]["data"]["ready"])
        self.error(self.result, "commit", context=context)
        self.error(self.result, "commit", context=protocol.retain_context(self.result("begin")))
        for key, value in (("transaction_id", SNAPID), ("operation_id", SNAPID), ("expected_revision", "f" * 64),
                           ("context_revision", "f" * 64), ("input_document_sha256", "f" * 64), ("required_kinds", [])):
            self.error(self.context, {**blocked, key: value})

    def second_preview(self, **kwargs):
        params = {**self.requests["preview"], "page_offset": 1}
        data = {**self.responses["preview"], "page_offset": 1, "rows": [{"entry_index": 1, "state": "missing"}], "next_offset": None}
        return self.result("preview", data=data, params=params, **kwargs)

    def test_preview_pages_bind_full_immutable_plan_and_complete_resolution(self):
        first, context = self.result("preview"), self.context()
        second = self.second_preview(context=context, previous=first)
        self.assertEqual(protocol.validate_preview_pages(context, [first, second]),
                         [{"entry_index": 0, "state": "resolved"}, {"entry_index": 1, "state": "missing"}])
        for pages in ([first], [second, first], [first, first], []):
            self.error(protocol.validate_preview_pages, context, pages)
        for key, value in (("plan_digest", "e" * 64), ("result_document_sha256", "e" * 64),
                           ("ready", False), ("required_kinds", [])):
            changed = second.data
            changed[key] = value
            if key == "ready":
                changed["blocker"] = "context_changed"
            self.error(self.result, "preview", data=changed, params=second.request, context=context, previous=first)
        changed = second.data
        changed["resolution_counts"] = {"resolved": 0, "missing": 2, "ambiguous": 0, "unsupported": 0}
        self.error(self.result, "preview", data=changed, params=second.request, context=context)
        changed = second.data
        changed["rows"] = [{"entry_index": 1, "state": "resolved"}]
        contradictory = self.result("preview", data=changed, params=second.request, context=context)
        self.error(protocol.validate_preview_pages, context, [first, contradictory])

    def test_preview_counts_bool_arithmetic_paging_and_empty_result(self):
        for changes in ({"before": True}, {"retained": 2}, {"updated": 2}, {"moved": 2}, {"after": 1001}):
            data = copy.deepcopy(self.responses["preview"])
            data["counts"].update(changes)
            self.error(self.result, "preview", data=data)
        for change in ({"next_offset": 2}, {"next_offset": None}, {"rows": []},
                       {"rows": [{"entry_index": 1, "state": "resolved"}]}, {"ready": 1}):
            self.error(self.result, "preview", data={**self.responses["preview"], **change})
        empty = copy.deepcopy(self.responses["preview"])
        empty.update(counts=dict.fromkeys(protocol.COUNT_FIELDS, 0), resolution_counts=dict.fromkeys(protocol.RESOLUTION, 0),
                     rows=[], next_offset=None)
        self.result("preview", data=empty)

    def test_changed_revision_and_noop_receipt_consistency(self):
        for change in ({"changed": False}, {"new_revision": "1" * 64}, {"changed": 1}):
            data = copy.deepcopy(self.responses["commit"])
            data["receipt"].update(change)
            self.error(self.result, "commit", data=data)
        data = copy.deepcopy(self.responses["commit"])
        data["receipt"].update(changed=False, new_revision="1" * 64,
                               counts={**dict.fromkeys(protocol.COUNT_FIELDS, 0), "before": 2, "after": 2, "retained": 2})
        self.assertFalse(self.result("commit", data=data).verified)

    def test_status_new_runtime_can_verify_original_receipt_but_not_replay_commit(self):
        context = self.context()
        params = {**self.requests["status"], "runtime": "fresh-runtime"}
        data = {**self.responses["status"], "runtime": "fresh-runtime"}
        result = self.result("status", data=data, params=params, context=context)
        self.assertTrue(result.verified)
        self.assertEqual(result.data["receipt"]["operation_runtime"], RUNTIME)
        self.error(self.result, "status", data={**data, "runtime": RUNTIME}, params=params, context=context)
        self.error(self.result, "status", data={**data, "operation_runtime": "unrelated"}, params=params, context=context)
        changed_commit = copy.deepcopy(self.responses["commit"])
        changed_commit["runtime"] = "fresh-runtime"
        changed_commit["receipt"]["operation_runtime"] = "fresh-runtime"
        self.error(self.result, "commit", data=changed_commit,
                   params={**self.requests["commit"], "runtime": "fresh-runtime"}, context=context)

    def test_status_state_outcome_semantics_and_restart_ram_refusal(self):
        context = self.context()
        for state in ("receiving", "ready", "expired", "aborted"):
            data = {**self.responses["status"], "state": state, "outcome": "not_applied", "receipt": None}
            self.assertFalse(self.result("status", data=data, context=context).verified)
            self.error(self.result, "status", data={**data, "runtime": "fresh"},
                       params={**self.requests["status"], "runtime": "fresh"}, context=context)
        for state in ("applying", "unknown"):
            data = {**self.responses["status"], "state": state, "outcome": "unknown", "receipt": None}
            self.assertFalse(self.result("status", data=data, context=context).verified)
        absent = common("status", operation_id=OPID, operation_runtime=None, transaction_id=None,
                        state="not_found", outcome="unknown", receipt=None)
        self.assertFalse(self.result("status", data=absent, context=context).verified)
        self.error(self.result, "status", data={**absent, "outcome": "not_applied"}, context=context)
        self.error(self.result, "status", data={**self.responses["status"], "receipt": None})

    def export_pages(self, described=None, raw=None):
        described = described or self.result("describe")
        raw = raw if raw is not None else self.raw
        # Split within an astral UTF-8 scalar: chunks are arbitrary bytes.
        split = raw.index("😀".encode()) + 1 if "😀".encode() in raw else len(raw) // 2
        pages, offset = [], 0
        for chunk in (raw[:split], raw[split:]):
            params = {**self.requests["export"], "offset": offset}
            data = {**self.responses["export"], "offset": offset, "size_bytes": len(raw), "document_sha256": sha(raw),
                    "data_base64": base64.b64encode(chunk).decode(), "chunk_sha256": sha(chunk),
                    "next_offset": offset + len(chunk), "eof": offset + len(chunk) == len(raw)}
            pages.append(self.result("export", data=data, params=params, describe=described,
                                     previous=pages[-1] if pages else None))
            offset += len(chunk)
        return pages

    def test_export_assembly_binds_bytes_digest_size_revision_and_codec(self):
        described = self.result("describe")
        pages = self.export_pages(described)
        result = protocol.assemble_export(described, pages)
        self.assertEqual(result.raw_document, self.raw)
        self.assertEqual(result.document, self.document)
        result.document["entries"].clear()
        self.assertEqual(result.document, self.document)
        self.assertNotEqual(result.revision, result.document_sha256)
        for selected in ([], [pages[0]], [pages[1], pages[0]], [pages[0], pages[0]]):
            self.error(protocol.assemble_export, described, selected)
        wrong = pages[1].data
        wrong["revision"] = "e" * 64
        self.error(self.result, "export", data=wrong, params=pages[1].request, describe=described)
        changed = copy.deepcopy(self.responses["describe"])
        changed["export"]["entries"] = 1
        self.error(protocol.assemble_export, self.result("describe", data=changed), pages)
        changed["export"]["entries"] = 2
        changed["export"]["document_sha256"] = "e" * 64
        self.error(protocol.assemble_export, self.result("describe", data=changed), pages)

    def test_export_rejects_noncanonical_complete_document_even_when_hash_matches(self):
        raw = encoded(self.document)
        self.assertNotEqual(raw, self.raw)
        changed = copy.deepcopy(self.responses["describe"])
        changed["export"].update(document_sha256=sha(raw), size_bytes=len(raw))
        described = self.result("describe", data=changed)
        self.error(protocol.assemble_export, described, self.export_pages(described, raw))
        changed = copy.deepcopy(self.responses["describe"])
        changed["locator_kinds"] = ["plex-media"]
        described = self.result("describe", data=changed)
        self.error(protocol.assemble_export, described, self.export_pages(described))


if __name__ == "__main__":
    unittest.main()
