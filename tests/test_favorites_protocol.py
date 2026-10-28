"""Inactive wire peer: synthetic fixtures and in-memory bytes, never RPC/config."""

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
    spec = importlib.util.spec_from_file_location("favorites_protocol_tested", ROOT / "cli/favorites_protocol.py")
    protocol = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = protocol
    spec.loader.exec_module(protocol)
FIXTURE = json.loads((ROOT / "contracts/favorites-transfer-v1.json").read_bytes())
CURRENT_PACKET = json.loads((ROOT / "contracts/favorites-current-plex-v1.json").read_bytes())
REQUEST_ID = "a" * 32
OTHER_UUID = "00000000-0000-4000-8000-000000000009"
PRIVATE = "fixture-private-token"


def fixture(op):
    if op in ("bindingcatalog", "bind"):
        return {"request": copy.deepcopy(CURRENT_PACKET[op + "_request"]),
                "response": copy.deepcopy(CURRENT_PACKET[op + "_response"])}
    return copy.deepcopy(FIXTURE["fixtures"][op])


def scope_validator(document):
    # Real codec syntax, without adding a bundle wrapper to the wire byte cap.
    # The host still owns provider/PMS/grant admission before production use.
    protocol.document_codec._bounded(document)
    protocol.document_codec._scope_document(document, None)
    return copy.deepcopy(document)


def result(op, data=None, **kwargs):
    pair = fixture(op)
    return protocol.validate_result(pair["request"], pair["response"] if data is None else data,
                                    request_id=REQUEST_ID, response_id=REQUEST_ID, status="ok",
                                    scope_validator=scope_validator, **kwargs)


def context():
    return protocol.retain_context(result("begin"), result("preview"))


class FavoritesProtocolTests(unittest.TestCase):
    def error(self, callback, *args, code=None, **kwargs):
        with self.assertRaises(protocol.ProtocolError) as caught:
            callback(*args, **kwargs)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_all_seven_fixtures_validate_without_promoting_verified_claims(self):
        self.assertEqual(protocol.ERRORS, frozenset(FIXTURE["error_allowlist"]))
        for op in protocol.TRANSFER_OPS:
            with self.subTest(op=op):
                pair = fixture(op)
                self.assertEqual(protocol.validate_request(pair["request"]), pair["request"])
                response = result(op)
                self.assertEqual(response.data, pair["response"])
                self.assertFalse(response.verified)
                response.data.clear()
                response.request.clear()
                self.assertEqual(response.data, pair["response"])
                if op == "snapshot":
                    self.assertEqual(hashlib.sha256(response.raw_document).hexdigest(), FIXTURE["document_sha256"])
                else:
                    self.assertIsNone(response.raw_document)

    def test_extra_fields_legacy_discriminator_and_session_mode_refused(self):
        for op in protocol.OPS:
            params = fixture(op)["request"]
            self.error(protocol.validate_request, params | {"private": PRIVATE})
            params["operation"] = params.pop("op")
            self.error(protocol.validate_request, params)
        for mode in (None, True, "session-only", "", PRIVATE):
            self.error(protocol.validate_request, fixture("begin")["request"] | {"persistence": mode})

    def test_frozen_current_plex_capability_and_binding_fixtures_are_copies(self):
        packet = (ROOT / "contracts/favorites-current-plex-v1.json").read_bytes()
        self.assertEqual(hashlib.sha256(packet).hexdigest(),
                         "86b9ed0fa8412bb7f1805811bf92ec6e21492c7667ad4ba38d020106e29f57e5")
        source = CURRENT_PACKET["capability"]["favorites_transfer"]
        checked = protocol.validate_capability(source)
        self.assertEqual(checked, source)
        checked["codecs"][0]["coverage"].clear()
        checked["limits"].clear()
        self.assertEqual(protocol.validate_capability(source), source)
        for op in protocol.BINDING_OPS:
            pair = fixture(op)
            self.assertEqual(protocol.validate_request(pair["request"]), pair["request"])
            response = result(op)
            self.assertEqual(response.data, pair["response"])
            self.assertFalse(response.verified)  # Pairing syntax is not a commit receipt.
            response.data.clear()
            self.assertEqual(response.data, pair["response"])

    def test_capability_all_eighteen_limit_bounds_and_actual_ttl_relationship(self):
        source = CURRENT_PACKET["capability"]["favorites_transfer"]
        self.assertEqual(len(protocol.CAPABILITY_LIMITS), 18)
        self.assertEqual(source["limits"], protocol.CAPABILITY_LIMITS)
        for name, ceiling in protocol.CAPABILITY_LIMITS.items():
            for invalid in (0, -1, True, 1.0, "1", ceiling + 1):
                with self.subTest(limit=name, value=invalid):
                    self.error(protocol.validate_capability, source | {"limits": source["limits"] | {name: invalid}})
            self.error(protocol.validate_capability, source | {"limits": {k: v for k, v in source["limits"].items() if k != name}})
        self.error(protocol.validate_capability, source | {"limits": source["limits"] | {"future": 1}})
        self.error(protocol.validate_capability, source | {"limits": source["limits"] | {"absolute_ttl_seconds": 119}})
        smaller = source | {"limits": {name: 1 for name in protocol.CAPABILITY_LIMITS}, "receipt_retention_seconds": 1}
        self.assertEqual(protocol.validate_capability(smaller), smaller)
        # Independent ceilings intersect; a lower chunk count need not support
        # every payload below the independent maximum document byte ceiling.
        smaller = source | {"limits": source["limits"] | {"max_chunks": 1}}
        self.assertEqual(protocol.validate_capability(smaller), smaller)

    def test_future_codec_persistence_capability_fields_and_raw_excess_fail_closed(self):
        source = CURRENT_PACKET["capability"]["favorites_transfer"]
        for change in ({"version": True}, {"version": 2}, {"persistence": ["session-only"]},
                       {"persistence": ["persistent", "session-only"]}, {"receipt_retention_origin": "completion"},
                       {"receipt_retention_seconds": 0}, {"receipt_retention_seconds": True},
                       {"receipt_retention_seconds": 86401}, {"future": PRIVATE}, {"codecs": []},
                       {"codecs": source["codecs"] * 2}):
            self.error(protocol.validate_capability, source | change)
        for change in ({"provider": "m3u"}, {"codecVersion": True}, {"codecVersion": 2},
                       {"locatorKinds": ["channel"]}, {"targetScope": "all-sources"},
                       {"coverage": ["mediaFavorites"]}, {"future": PRIVATE}):
            self.error(protocol.validate_capability, source | {"codecs": [source["codecs"][0] | change]})
        raw = protocol._encoded(source)
        self.assertEqual(protocol.validate_capability(raw), source)
        self.error(protocol.validate_capability, raw + b" " * protocol.MAX_METADATA_BYTES, code="envelope_too_large")
        self.error(protocol.validate_capability, raw.replace(b'"version":1', b'"version":1,"version":1'))

    def test_binding_catalog_current_target_scope_uniqueness_and_compatible_references(self):
        data = fixture("bindingcatalog")["response"]
        second = copy.deepcopy(data["bindings"][0])
        second["scope"]["sourceBindingId"] = OTHER_UUID
        # Multiple public identities may point to one physical current target;
        # runtime selection must require explicit disambiguation above this peer.
        self.assertEqual(result("bindingcatalog", data | {"bindings": [*data["bindings"], second]}).data["bindings"][-1], second)
        self.assertEqual(result("bindingcatalog", data | {"bindings": []}).data["bindings"], [])
        rows = []
        for number in range(256):
            row = copy.deepcopy(second)
            row["scope"]["sourceBindingId"] = f"00000000-0000-4000-8000-{number:012x}"
            rows.append(row)
        self.assertEqual(len(result("bindingcatalog", data | {"bindings": rows}).data["bindings"]), 256)
        self.error(result, "bindingcatalog", data | {"bindings": rows + [second]})
        for change in ({"targets": []}, {"targets": data["targets"] * 2},
                       {"bindings": data["bindings"] * 2}, {"binding_revision": ""}, {"private": PRIVATE}):
            self.error(result, "bindingcatalog", data | change)
        for change in ({"target_id": "not-in-catalog"}, {"codecVersion": 2}, {"codecVersion": True},
                       {"scope": second["scope"] | {"provider": "m3u"}}, {"owner": PRIVATE}):
            self.error(result, "bindingcatalog", data | {"bindings": [second | change]})
        for change in ({"provider": "offline"}, {"codecVersion": True}, {"codecVersion": 2},
                       {"serverId": "https://private.invalid/token"}, {"serverId": ""},
                       {"display": "x\u200by"}, {"display": "é" * 129}, {"grant": PRIVATE}):
            self.error(result, "bindingcatalog", data | {"targets": [data["targets"][0] | change]})

    def test_bind_exact_runtime_scope_target_revision_codec_and_no_guessed_creation(self):
        params = fixture("bind")["request"]
        data = fixture("bind")["response"]
        for change in ({"codecVersion": True}, {"codecVersion": 2}, {"scope": params["scope"] | {"provider": "m3u"}},
                       {"expected_binding_revision": ""}, {"create": True}, {"selector": PRIVATE},
                       {"scope": params["scope"] | {"sourceBindingId": params["scope"]["profileBindingId"]}}):
            self.error(protocol.validate_request, params | change)
        for target in ("", "x" * 257, "é" * 129, "two words", "x\x00y", "x\x85y", "x\u200by", "\ud800"):
            self.error(protocol.validate_request, params | {"target_id": target})
        self.assertEqual(protocol.validate_request(params | {"target_id": "é" * 128})["target_id"], "é" * 128)
        for change in ({"scope": params["scope"] | {"sourceBindingId": OTHER_UUID}},
                       {"scope": params["scope"] | {"profileBindingId": OTHER_UUID}},
                       {"runtime": "different"}, {"target_id": "other-target"}, {"binding_revision": "3"},
                       {"paired": False}, {"paired": 1}, {"codecVersion": 1}, {"private": PRIVATE}):
            self.error(result, "bind", data | change)

    def test_catalog_cannot_project_conflicting_global_registry_uuid_roles(self):
        data = fixture("bindingcatalog")["response"]
        original = data["bindings"][0]
        scope = original["scope"]
        impossible = [
            [original, original | {"scope": scope | {"profileBindingId": OTHER_UUID}}],
            [original, original | {"scope": scope | {
                "profileBindingId": scope["sourceBindingId"], "sourceBindingId": OTHER_UUID}}],
            [original | {"scope": scope | {"sourceBindingId": scope["profileBindingId"]}}],
        ]
        for rows in impossible:
            with self.subTest(rows=rows):
                self.error(result, "bindingcatalog", data | {"bindings": rows})
                self.error(result, "bindingcatalog", data | {"bindings": list(reversed(rows))})

    def test_binding_raw_metadata_errors_and_outer_request_identity(self):
        for op in protocol.BINDING_OPS:
            pair = fixture(op)
            for status in ("ok", "rejected", "unsupported"):
                data = pair["response"] if status == "ok" else {
                    "version": 1, "runtime": pair["request"]["runtime"], "op": op, "error": "binding_changed"}
                raw = protocol._encoded(data)
                padded = raw[:-1] + b" " * (protocol.MAX_METADATA_BYTES - len(raw)) + b"}"
                envelope = b'{"id":"' + REQUEST_ID.encode() + b'","status":"' + status.encode() + b'","data":' + padded + b'}'
                checked = protocol.validate_response(pair["request"], envelope, request_id=REQUEST_ID)
                self.assertEqual(checked.data, data)
                self.assertFalse(checked.verified)
                oversized = envelope[:-2] + b" " + envelope[-2:]
                self.error(protocol.validate_response, pair["request"], oversized, request_id=REQUEST_ID, code="envelope_too_large")
                self.error(protocol.validate_response, pair["request"], envelope, request_id="b" * 32)
            args = {"request_id": REQUEST_ID, "response_id": REQUEST_ID, "status": "rejected"}
            error = {"version": 1, "runtime": pair["request"]["runtime"], "op": op, "error": "policy_denied"}
            for change in ({"error": PRIVATE}, {"runtime": "wrong"}, {"target_id": PRIVATE}):
                self.error(protocol.validate_result, pair["request"], error | change, **args)

    def test_advertised_metadata_limit_counts_original_data_padding(self):
        for op in ("bindingcatalog", "preview", "snapshot"):
            pair = fixture(op)
            statuses = ("rejected", "unsupported") if op == "snapshot" else ("ok", "rejected", "unsupported")
            for status in statuses:
                data = pair["response"] if status == "ok" else {
                    "version": 1, "runtime": pair["request"]["runtime"], "op": op, "error": "policy_denied"}
                raw = protocol._encoded(data)
                limit = len(raw) + 17
                padded = raw[:-1] + b" " * 17 + raw[-1:]
                envelope = (b'{"id":"' + REQUEST_ID.encode() + b'","status":"' + status.encode()
                            + b'","data":' + padded + b'}')
                options = {"request_id": REQUEST_ID, "max_metadata_bytes": limit}
                with self.subTest(op=op, status=status):
                    self.assertEqual(protocol.validate_response(pair["request"], envelope, **options).data, data)
                    self.error(protocol.validate_response, pair["request"], envelope,
                               **(options | {"max_metadata_bytes": limit - 1}), code="envelope_too_large")
                    self.error(protocol.validate_result, pair["request"], padded, request_id=REQUEST_ID,
                               response_id=REQUEST_ID, status=status, max_metadata_bytes=limit - 1,
                               code="envelope_too_large")
        for invalid in (0, True, 1.5, None, protocol.MAX_METADATA_BYTES + 1):
            self.error(result, "preview", max_metadata_bytes=invalid)
        # Successful snapshot payloads use their separate 2 MiB boundary.
        self.assertIsNotNone(result("snapshot", max_metadata_bytes=1).raw_document)

    def test_uuid_runtime_revision_digest_and_numeric_boundaries(self):
        params = fixture("begin")["request"]
        changes = [{"operation_id": params["operation_id"].upper()}, {"operation_id": OTHER_UUID.replace("4000", "5000")},
                   {"operation_id": OTHER_UUID + "\n"}, {"runtime": "Runtime"}, {"runtime": "x" * 65},
                   {"document_sha256": "A" * 64}, {"document_sha256": "a" * 64 + "\n"},
                   {"expected_revision": ""}, {"expected_revision": "я" * 129}, {"binding_revision": PRIVATE + "\n"},
                   {"size_bytes": True}, {"size_bytes": 1048577}, {"size_bytes": 0}, {"chunk_count": True},
                   {"chunk_count": 257}, {"chunk_count": 0}, {"chunk_count": 1, "size_bytes": 8193},
                   {"chunk_count": 2, "size_bytes": 1}]
        for change in changes:
            with self.subTest(change=change):
                self.error(protocol.validate_request, params | change)
        self.assertEqual(protocol.validate_request(params | {"expected_revision": "я" * 128})["expected_revision"], "я" * 128)
        self.assertEqual(protocol.validate_request(params | {"size_bytes": 1048576, "chunk_count": 128})["chunk_count"], 128)
        for key in ("profileBindingId", "sourceBindingId"):
            self.error(protocol.validate_request, params | {"scope": params["scope"] | {key: "bad"}})

    def test_idempotent_begin_progress_and_expiry_validation(self):
        data = fixture("begin")["response"]
        for change in ({"next_seq": True}, {"next_seq": 2}, {"received_bytes": 651}, {"received_bytes": 1},
                       {"idle_expires_at": "2026-10-27T23:11:00Z"}, {"absolute_expires_at": "2026-99-27T23:10:00Z"},
                       {"idle_expires_at": "2026-10-27T23:02:00.000000002Z",
                        "absolute_expires_at": "2026-10-27T23:02:00.000000001Z"}):
            self.error(result, "begin", data | change)
        self.assertEqual(result("begin", data | {"next_seq": 1, "received_bytes": 650}).data["received_bytes"], 650)

    def test_append_accepts_raw_utf8_fragment_and_exact_duplicate_ack_only(self):
        params = fixture("append")["request"]
        encoded = protocol.transfer.encode_bytes(b"\x82\xac")
        params.update(data_base64=encoded.data_base64, chunk_sha256=encoded.sha256, offset=5, seq=2)
        self.assertEqual(protocol.validate_request(params), params)
        data = fixture("append")["response"] | {"next_seq": 3, "received_bytes": 7}
        args = {"request_id": REQUEST_ID, "response_id": REQUEST_ID, "status": "ok"}
        for _ in range(2):
            self.assertEqual(protocol.validate_result(params, data, **args).data, data)
        for change in ({"next_seq": 4}, {"received_bytes": 8}, {"next_seq": True}):
            self.error(protocol.validate_result, params, data | change, **args)
        for change in ({"seq": 256}, {"offset": 1048575}, {"data_base64": encoded.data_base64 + "\n"},
                       {"data_base64": "Zh=="}, {"chunk_sha256": "0" * 64}):
            self.error(protocol.validate_request, params | change)
        too_big = protocol.transfer.encode_bytes(b"x" * 8193)
        self.error(protocol.validate_request, params | {"data_base64": too_big.data_base64, "chunk_sha256": too_big.sha256})

    def test_request_budget_includes_real_client_idempotency_and_queued_fields(self):
        params = fixture("append")["request"]
        chunk = protocol.transfer.encode_bytes(b"x" * 8192)
        params.update(data_base64=chunk.data_base64, chunk_sha256=chunk.sha256)
        key = "k" * 80
        self.assertEqual(protocol.validate_request(params, idempotency_key=key), params)
        envelopes = protocol.transfer.request_envelopes("favorites_transfer", params, idempotency_key=key)
        bound = max(len(envelopes.client), len(envelopes.queued_upper_bound))
        self.assertGreater(bound, len(protocol._encoded(params)))
        with mock.patch.object(protocol.transfer, "MAX_RPC_BYTES", bound - 1):
            self.error(protocol.validate_request, params, idempotency_key=key, code="envelope_too_large")

    def test_minimal_errors_require_status_allowlist_and_outer_request_correlation(self):
        pair = FIXTURE["error_fixture"]
        args = {"request_id": REQUEST_ID, "response_id": REQUEST_ID, "status": "rejected"}
        for status in ("rejected", "unsupported"):
            for error in protocol.ERRORS:
                self.assertFalse(protocol.validate_result(pair["request"], pair["response"] | {"error": error},
                                 **(args | {"status": status})).verified)
        for changed in (pair["response"] | {"error": PRIVATE}, pair["response"] | {"transaction_id": OTHER_UUID},
                        pair["response"] | {"version": True}, pair["response"] | {"runtime": "other"}):
            self.error(protocol.validate_result, pair["request"], changed, **args)
        for change in ({"status": "ok"}, {"response_id": "b" * 32}, {"request_id": "bad", "response_id": "bad"}):
            self.error(protocol.validate_result, pair["request"], pair["response"], **(args | change))

    def test_complete_raw_response_strict_json_and_outer_byte_boundary(self):
        pair = fixture("abort")
        envelope = {"id": REQUEST_ID, "status": "ok", "data": pair["response"]}
        raw = protocol._encoded(envelope)
        self.assertEqual(protocol.validate_response(pair["request"], raw, request_id=REQUEST_ID).data, pair["response"])
        for bad in (raw.replace(b'"version":1', b'"version":1,"version":1'),
                    raw.replace(b'"version":1', b'"version":NaN'), raw.replace(b'"version":1', b'"version":1e9999'),
                    raw.replace(b'"runtime":"synthetic-runtime-v1"', b'"runtime":"\\ud800"'),
                    b" " * protocol.MAX_RESPONSE_BYTES + raw):
            self.error(protocol.validate_response, pair["request"], bad, request_id=REQUEST_ID)
        for change in ({"extra": PRIVATE}, {"id": "b" * 32}):
            self.error(protocol.validate_response, pair["request"], envelope | change, request_id=REQUEST_ID)

    def test_non_snapshot_raw_metadata_has_its_own_byte_ceiling(self):
        pair = fixture("abort")
        raw = protocol._encoded(pair["response"])
        args = {"request_id": REQUEST_ID, "response_id": REQUEST_ID, "status": "ok"}
        accepted = raw + b" " * (protocol.MAX_METADATA_BYTES - len(raw))
        self.assertEqual(protocol.validate_result(pair["request"], accepted, **args).data, pair["response"])
        self.error(protocol.validate_result, pair["request"], accepted + b" ", code="envelope_too_large", **args)

    def test_full_response_preserves_data_padding_and_caps_snapshot_errors(self):
        for op in ("abort", "snapshot"):
            pair = fixture(op)
            data = pair["response"] if op == "abort" else {
                "version": 1, "op": op, "runtime": pair["request"]["runtime"], "error": "busy"}
            status = "ok" if op == "abort" else "rejected"
            raw = protocol._encoded(data)
            padded = raw[:-1] + b" " * protocol.MAX_METADATA_BYTES + b"}"
            envelope = b'{"id":"' + REQUEST_ID.encode() + b'","status":"' + status.encode() + b'","data":' + padded + b'}'
            self.error(protocol.validate_response, pair["request"], envelope, request_id=REQUEST_ID, code="envelope_too_large")

    def test_snapshot_original_bytes_hash_scope_schema_and_explicit_codec(self):
        pair = fixture("snapshot")
        raw = result("snapshot").raw_document
        spaced = b" \n" + raw + b"\n"
        encoded = protocol.transfer.encode_document(spaced)
        data = pair["response"] | {"document_base64": encoded.data_base64, "document_sha256": encoded.sha256,
                                    "size_bytes": encoded.size_bytes}
        self.assertEqual(result("snapshot", data).raw_document, spaced)
        for change in ({"size_bytes": True}, {"size_bytes": encoded.size_bytes - 1},
                       {"document_sha256": "0" * 64}, {"document_base64": encoded.data_base64 + "\n"}):
            self.error(result, "snapshot", data | change)
        docs = [protocol._encoded(json.loads(raw) | {"scope": FIXTURE["scope"] | {"sourceBindingId": OTHER_UUID}}),
                protocol._encoded(json.loads(raw) | {"extra": PRIVATE}),
                raw.replace(b'"schema":1', b'"schema":1,"schema":1')]
        for bad in docs:
            encoded = protocol.transfer.encode_bytes(bad)
            self.error(result, "snapshot", pair["response"] | {"document_base64": encoded.data_base64,
                       "document_sha256": encoded.sha256, "size_bytes": encoded.size_bytes})
        args = {"request_id": REQUEST_ID, "response_id": REQUEST_ID, "status": "ok"}
        self.error(protocol.validate_result, pair["request"], pair["response"], code="unresolved_variant", **args)
        def fail(_):
            raise RuntimeError(PRIVATE)
        for validator in (fail, lambda _: {"scope": FIXTURE["scope"]}):
            self.error(protocol.validate_result, pair["request"], pair["response"], scope_validator=validator, **args)

    def test_exact_one_mib_snapshot_uses_original_scope_budget_without_wrapper(self):
        pair = fixture("snapshot")
        raw = result("snapshot").raw_document
        raw += b" " * (protocol.transfer.MAX_DOCUMENT_BYTES - len(raw))
        encoded = protocol.transfer.encode_document(raw)
        data = pair["response"] | {"document_base64": encoded.data_base64, "document_sha256": encoded.sha256,
                                    "size_bytes": encoded.size_bytes}
        self.assertEqual(result("snapshot", data).raw_document, raw)
        self.error(result, "snapshot", data | {"size_bytes": encoded.size_bytes + 1})

    def test_preview_counts_stores_and_typed_unresolved_diagnostics(self):
        data = fixture("preview")["response"]
        for change in ({"ready": 1}, {"counts": data["counts"] | {"channelLists": True}},
                       {"counts": data["counts"] | {"unresolved": 2}}, {"counts": data["counts"] | {"channelLists": 1000001}},
                       {"stores": []}, {"stores": data["stores"] * 2}, {"coverage": {}},
                       {"unresolved": [{"message": PRIVATE}]}):
            self.error(result, "preview", data | change)
        self.assertFalse(result("preview", data | {"ready": False}).verified)
        self.error(protocol.retain_context, result("begin"), result("preview", data | {"ready": False}), code="context_mismatch")
        typed = data | {"counts": data["counts"] | {"channelMemberships": 1, "unresolved": 2},
                        "unresolved": copy.deepcopy(FIXTURE["preview_unresolved_example"])}
        self.assertEqual(result("preview", typed).data, typed)
        for rows in ([typed["unresolved"][0]] * 129,
                     [typed["unresolved"][1] | {"list_index": 0}],
                     [typed["unresolved"][0] | {"entry_index": True}],
                     [typed["unresolved"][0] | {"entry_index": 1000001}]):
            self.error(result, "preview", typed | {"unresolved": rows})

    def test_receipt_verification_requires_complete_retained_context(self):
        retained = context()
        for op in ("commit", "status"):
            self.assertFalse(result(op).verified)
            self.assertTrue(result(op, context=retained).verified)
        for key, value in (("scope", FIXTURE["scope"] | {"sourceBindingId": OTHER_UUID}), ("document_sha256", "0" * 64),
                           ("counts", fixture("commit")["response"]["counts"] | {"mediaFavorites": 2})):
            data = fixture("commit")["response"] | {key: value}
            data["receipt"][key] = value
            self.assertFalse(result("commit", data).verified)
            self.error(result, "commit", data, context=retained, code="context_mismatch")
        data = fixture("commit")["response"]
        data["per_store"][1]["id"] = "other-store"
        self.error(result, "commit", data, context=retained, code="context_mismatch")
        data = fixture("status")["response"]
        data["receipt"]["document_sha256"] = "0" * 64
        self.error(result, "status", data, context=retained, code="context_mismatch")

    def test_context_cannot_cross_operation_stage_runtime_revision_or_plan(self):
        for key, value in (("transaction_id", OTHER_UUID), ("operation_id", OTHER_UUID), ("runtime", "other"),
                           ("expected_revision", "new"), ("plan_digest", "0" * 64)):
            pair = fixture("commit")
            pair["request"][key] = value
            if key in pair["response"]:
                pair["response"][key] = value
            if key in pair["response"]["receipt"]:
                pair["response"]["receipt"][key] = value
            self.error(protocol.validate_result, pair["request"], pair["response"], request_id=REQUEST_ID,
                       response_id=REQUEST_ID, status="ok", context=context(), code="context_mismatch")
        for change in ({"binding_revision": "new"}, {"base_revision": "new"}):
            self.error(protocol.retain_context, result("begin"), result("preview", fixture("preview")["response"] | change),
                       code="context_mismatch")

    def test_partial_unknown_and_not_applied_never_become_verified(self):
        partial = FIXTURE["outcome_fixtures"]["partial"]
        self.assertFalse(result("commit", partial, context=context()).verified)
        self.assertFalse(result("status", FIXTURE["outcome_fixtures"]["unknown"], context=context()).verified)
        for outcome in ("verified", "unknown"):
            self.error(result, "commit", partial | {"per_store": [{"id": "one", "outcome": outcome}]})
        self.assertFalse(result("commit", partial | {"outcome": "not_applied", "per_store": []}).verified)
        data = fixture("commit")["response"]
        for stores in ([], [{"id": "one", "outcome": "unknown"}], data["per_store"] * 2):
            self.error(result, "commit", data | {"per_store": stores})
        self.error(result, "commit", data | {"receipt": {"private": PRIVATE}})

    def test_nonverified_context_refuses_foreign_store_ids(self):
        for outcome in ("partial", "unknown", "not_applied"):
            data = copy.deepcopy(FIXTURE["outcome_fixtures"]["partial"])
            data["outcome"] = outcome
            data["per_store"] = [{"id": "foreign-store", "outcome": outcome}]
            self.assertFalse(result("commit", data).verified)
            self.error(result, "commit", data, context=context(), code="context_mismatch")

    def test_restart_context_is_immutable_status_only_and_requires_same_binding(self):
        retained = context()
        pair = fixture("snapshot")
        pair["request"]["runtime"] = pair["response"]["runtime"] = "restarted-runtime"
        current = protocol.validate_result(pair["request"], pair["response"], request_id=REQUEST_ID,
                                           response_id=REQUEST_ID, status="ok", scope_validator=scope_validator)
        derived = protocol.status_context(retained, current)
        self.assertEqual((derived._begin, derived._begin_result, derived._preview),
                         (retained._begin, retained._begin_result, retained._preview))
        self.assertIsNone(retained._status_runtime)
        status = fixture("status")
        status["request"]["runtime"] = status["response"]["runtime"] = "restarted-runtime"
        checked = protocol.validate_result(status["request"], status["response"], request_id=REQUEST_ID,
                                           response_id=REQUEST_ID, status="ok", context=derived)
        self.assertTrue(checked.verified)
        for state, data in FIXTURE["status_fixtures"].items():
            with self.subTest(restarted_stage=state):
                self.error(protocol.validate_result, status["request"], data | {"runtime": "restarted-runtime"},
                           request_id=REQUEST_ID, response_id=REQUEST_ID, status="ok", context=derived,
                           code="context_mismatch")
        for runtime in ("restarted-runtime", fixture("commit")["request"]["runtime"]):
            commit = fixture("commit")
            commit["request"]["runtime"] = commit["response"]["runtime"] = runtime
            self.error(protocol.validate_result, commit["request"], commit["response"], request_id=REQUEST_ID,
                       response_id=REQUEST_ID, status="ok", context=derived, code="context_mismatch")
        pair["response"]["binding_revision"] = "different"
        wrong = protocol.validate_result(pair["request"], pair["response"], request_id=REQUEST_ID,
                                         response_id=REQUEST_ID, status="ok", scope_validator=scope_validator)
        self.error(protocol.status_context, retained, wrong, code="context_mismatch")

    def test_frozen_status_variants_and_context_progress_bindings(self):
        for state, data in FIXTURE["status_fixtures"].items():
            with self.subTest(state=state):
                self.assertEqual(result("status", data, context=context()).data["state"], state)
                self.assertFalse(result("status", data, context=context()).verified)
                self.error(result, "status", data | {"transaction_id": OTHER_UUID}, context=context(), code="context_mismatch")
        ready = FIXTURE["status_fixtures"]["ready"]
        for change in ({"ready": 1}, {"next_seq": True}, {"received_bytes": 1048577},
                       {"absolute_expires_at": "not-a-time"}, {"receipt": {}}, {"next_seq": 0, "received_bytes": 0}):
            self.error(result, "status", ready | change)
        self.error(result, "status", ready | {"received_bytes": 649}, context=context(), code="context_mismatch")
        expired = FIXTURE["status_fixtures"]["expired"]
        self.error(result, "status", expired | {"outcome": "unknown"})
        self.error(result, "status", expired | {"state": "made-up"})


if __name__ == "__main__":
    unittest.main()
