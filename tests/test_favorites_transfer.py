"""Byte and admission contracts only; synthetic envelopes are not a wire API."""

import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_favorites_transfer_tested", ROOT / "cli/favorites_transfer.py")
transfer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transfer
SPEC.loader.exec_module(transfer)
PRIVATE = "fixture-private-payload"


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def envelopes(chunk, *, metadata="", idempotency=""):
    # An injected synthetic builder owns final serialization and all overhead.
    data = {"seq": chunk.seq, "offset": chunk.offset, "data": chunk.payload.data_base64,
            "sha256": chunk.payload.sha256, "metadata": metadata}
    client = encode_json({"fixture": data, "idempotency": idempotency})
    queued = encode_json({"fixture": data, "id": "f" * 32, "ts": 9_007_199_254_740_991})
    return transfer.EnvelopeBytes(client, queued)


def encoded_args(encoded):
    return {"size_bytes": encoded.size_bytes, "sha256": encoded.sha256}


class TransferAssertions:
    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(transfer.TransferError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PRIVATE, str(caught.exception))
        return caught.exception


class FavoritesTransferBytesTests(TransferAssertions, unittest.TestCase):
    def test_original_bytes_are_hashed_without_json_normalization(self):
        first = b' { "unicode": "\\u043a", "number":1.0 } \n'
        second = '{"number":1,"unicode":"к"}'.encode()
        self.assertEqual(transfer.validate_document_bytes(first), transfer.validate_document_bytes(second))
        encoded = transfer.encode_document(first)
        self.assertEqual(encoded.sha256, hashlib.sha256(first).hexdigest())
        self.assertNotEqual(encoded.sha256, transfer.encode_document(second).sha256)
        self.assertEqual(transfer.decode_document(encoded.data_base64, **encoded_args(encoded)), first)

    def test_document_boundaries_and_lowered_limits(self):
        raw = b'"' + b'a' * (transfer.MAX_DOCUMENT_BYTES - 2) + b'"'
        value = transfer.encode_document(raw)
        self.assertEqual(value.size_bytes, transfer.MAX_DOCUMENT_BYTES)
        self.assertEqual(transfer.decode_document(value.data_base64, **encoded_args(value)), raw)
        self.assert_error("invalid_bytes", transfer.encode_document, raw + b' ')
        self.assert_error("invalid_bytes", transfer.encode_document, b'"ab"', max_bytes=3)
        self.assertEqual(transfer.encode_document(b'"a"', max_bytes=3).size_bytes, 3)
        for cap in (0, True, 1.5, transfer.MAX_DOCUMENT_BYTES + 1):
            self.assert_error("invalid_limits", transfer.encode_document, b'{}', max_bytes=cap)
        for value in (b'', bytearray(b'{}'), '{}', None):
            self.assert_error("invalid_bytes", transfer.encode_document, value)

    def test_json_is_strict_without_claiming_document_schema(self):
        invalid = [b'{"a":1,"a":2}', b'{"nested":{"a":1,"\\u0061":2}}',
                   b'NaN', b'Infinity', b'-Infinity', b'[1e999]', b'"\xff"', b'"\\ud800"',
                   b'{', b'{} {}', b'\xef\xbb\xbf{}', b' ']
        for raw in invalid:
            self.assert_error("invalid_json", transfer.encode_document, raw)
            encoded = transfer.encode_bytes(raw)
            self.assert_error("invalid_json", transfer.decode_document, encoded.data_base64, **encoded_args(encoded))
        self.assertEqual(transfer.validate_document_bytes(b'[null,true,1.25]'), [None, True, 1.25])
        self.assertEqual(transfer.validate_document_bytes(b'{"a":1,"nested":{"a":2}}'), {"a": 1, "nested": {"a": 2}})

    def test_base64_must_be_canonical_and_bounded_before_decode(self):
        encoded = transfer.encode_bytes(b'a')
        for value in ('YR==', 'YQ===', 'YQ', 'YQ==\n', 'Y Q==', 'YQ==☃', '_w==', '', None):
            self.assert_error("invalid_encoding", transfer.decode_bytes, value, **encoded_args(encoded))
        with mock.patch.object(transfer.base64, "b64decode", side_effect=AssertionError("must reject before allocation")):
            self.assert_error("invalid_encoding", transfer.decode_bytes, 'A' * 13,
                              size_bytes=1, sha256=encoded.sha256, max_bytes=8)
        for size in (True, 0, transfer.MAX_DOCUMENT_BYTES + 1):
            self.assert_error("invalid_encoding", transfer.decode_bytes, encoded.data_base64, size_bytes=size, sha256=encoded.sha256)
        for digest in ('f', encoded.sha256.upper(), None):
            self.assert_error("invalid_encoding", transfer.decode_bytes, encoded.data_base64, size_bytes=1, sha256=digest)

    def test_size_and_digest_match_original_payload(self):
        encoded = transfer.encode_bytes(PRIVATE.encode())
        self.assert_error("size_mismatch", transfer.decode_bytes, encoded.data_base64,
                          size_bytes=encoded.size_bytes + 1, sha256=encoded.sha256)
        self.assert_error("digest_mismatch", transfer.decode_bytes, encoded.data_base64,
                          size_bytes=encoded.size_bytes, sha256='0' * 64)
        self.assertNotIn(PRIVATE, repr(encoded))
        self.assertNotIn(encoded.data_base64, repr(encoded))


class FavoritesTransferPackingTests(TransferAssertions, unittest.TestCase):
    def test_multibyte_splits_are_reassembled_before_json_validation(self):
        raw = '"🙂🙂"'.encode()
        plan = transfer.pack_document(raw, envelopes, chunk_bytes=3)
        self.assertEqual([row.chunk.offset for row in plan.chunks], [0, 3, 6, 9])
        self.assertEqual([row.chunk.seq for row in plan.chunks], [0, 1, 2, 3])
        fragments = [transfer.decode_bytes(row.chunk.payload.data_base64, **encoded_args(row.chunk.payload), max_bytes=3)
                     for row in plan.chunks]
        with self.assertRaises(UnicodeDecodeError):
            fragments[0].decode('utf-8')
        self.assertEqual(b''.join(fragments), raw)
        self.assertEqual(plan.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(transfer.validate_document_bytes(b''.join(fragments)), '🙂🙂')

    def test_actual_chunk_count_and_lower_advertised_bound(self):
        raw = b'"' + b'a' * (transfer.MAX_DOCUMENT_BYTES - 2) + b'"'
        normal = transfer.pack_document(raw, envelopes)
        smaller = transfer.pack_document(raw, envelopes, chunk_bytes=4096)
        self.assertEqual((len(normal.chunks), len(smaller.chunks)), (128, 256))
        self.assertEqual(sum(row.chunk.payload.size_bytes for row in normal.chunks), len(raw))
        builder = mock.Mock(side_effect=envelopes)
        self.assert_error("too_many_chunks", transfer.pack_document, raw, builder, chunk_bytes=4095)
        builder.assert_not_called()
        self.assert_error("too_many_chunks", transfer.pack_document, b'"abcd"', builder, chunk_bytes=2, max_chunks=2)
        for keyword, value in (("chunk_bytes", 8193), ("max_chunks", 257), ("rpc_bytes", 16385)):
            self.assert_error("invalid_limits", transfer.pack_document, b'{}', builder, **{keyword: value})

    def test_actual_utf8_bytes_and_both_envelopes_are_counted(self):
        raw = b'"ab"'
        def builder(chunk):
            return envelopes(chunk, metadata='Ж' * 200)

        packed = transfer.pack_document(raw, builder)
        pair = packed.chunks[0].envelopes
        self.assertGreater(len(pair.client), len(pair.client.decode('utf-8')))
        cap = max(len(pair.client), len(pair.queued_upper_bound))
        self.assertEqual(len(transfer.pack_document(raw, builder, rpc_bytes=cap).chunks), 1)
        self.assert_error("envelope_too_large", transfer.pack_document, raw, builder, rpc_bytes=cap - 1)
        self.assert_error("envelope_too_large", transfer.pack_document, raw,
                          lambda _: transfer.EnvelopeBytes(b'{}', b'"' + b'x' * 100 + b'"'), rpc_bytes=100)
        self.assert_error("envelope_too_large", transfer.pack_document, raw,
                          lambda _: transfer.EnvelopeBytes(b'"' + b'x' * 100 + b'"', b'{}'), rpc_bytes=100)

    def test_idempotency_overhead_is_not_guessed_or_dropped(self):
        raw = b'"' + b'a' * 8190 + b'"'
        def builder(chunk):
            return envelopes(chunk, idempotency='k' * 6000)

        self.assert_error("envelope_too_large", transfer.pack_document, raw, builder)
        # Explicit replanning is allowed; the original failure never emitted a partial plan.
        smaller = transfer.pack_document(raw, builder, chunk_bytes=4096)
        self.assertEqual(len(smaller.chunks), 2)
        self.assertTrue(all(len(row.envelopes.client) <= 16384 for row in smaller.chunks))

    def test_builder_errors_are_fixed_and_no_payload_is_in_repr(self):
        for failure in (RuntimeError(PRIVATE), ValueError(PRIVATE)):
            self.assert_error("invalid_envelope", transfer.pack_document, b'{}', mock.Mock(side_effect=failure))
        for builder in (None, lambda _: {}, lambda _: transfer.EnvelopeBytes('{}', b'{}'),
                        lambda _: transfer.EnvelopeBytes(b'{}', b'{"a":1,"a":2}')):
            self.assert_error("invalid_envelope", transfer.pack_document, b'{}', builder)
        packed = transfer.pack_document(encode_json(PRIVATE), envelopes)
        self.assertNotIn(PRIVATE, repr(packed))
        self.assertNotIn(PRIVATE, repr(packed.chunks))


class FavoritesTransferBudgetTests(TransferAssertions, unittest.TestCase):
    def budget(self, count=128, **changes):
        arguments = {"control_commands": 5, "controller_requests": 2 * (count + 5),
                     "device_requests": 2 * (count + 5), "final_receipt_reserve": 10}
        arguments.update(changes)
        return transfer.estimate_budget(count, **arguments)

    def test_large_plan_refuses_default_deadline_before_staging(self):
        budget = self.budget()
        self.assertEqual((budget.commands, budget.paced_seconds, budget.minimum_seconds), (133, 199.5, 209.5))
        self.assert_error("deadline_insufficient", transfer.require_budget, budget, deadline=145, now=100)
        self.assertEqual(transfer.require_budget(budget, deadline=400, now=100), 300)
        self.assert_error("deadline_insufficient", transfer.require_budget, budget, deadline=400, now=191)
        # The caller keeps the same deadline; no per-chunk timeout is created.
        self.assertEqual(transfer.require_budget(budget, deadline=309.5, now=100), 209.5)

    def test_actual_lowered_chunk_count_and_http_poll_cost_change_budget(self):
        self.assertGreater(self.budget(256).minimum_seconds, 300)
        extra_polls = self.budget(controller_requests=1000, device_requests=600)
        self.assertEqual(extra_polls.http_seconds, 300)
        self.assertEqual(extra_polls.minimum_seconds, 310)
        self.assertGreater(self.budget(device_limit=60).minimum_seconds, self.budget().minimum_seconds)
        self.assertGreater(self.budget(command_interval=2).minimum_seconds, self.budget().minimum_seconds)

    def test_invalid_budget_cannot_weaken_limits_or_remove_receipt_reserve(self):
        for values in ({"control_commands": True}, {"controller_requests": 132}, {"device_requests": 0},
                       {"final_receipt_reserve": 0}, {"final_receipt_reserve": float('nan')},
                       {"command_interval": 1.49}, {"command_interval": float('inf')},
                       {"final_receipt_reserve": 10 ** 400}):
            self.assert_error("invalid_budget", self.budget, **values)
        for count in (0, True, 257):
            self.assert_error("invalid_budget", self.budget, count)
        for values in ({"controller_limit": 241}, {"device_limit": 121}, {"device_limit": True}):
            self.assert_error("invalid_limits", self.budget, **values)
        for deadline, now in ((True, 0), (float('inf'), 0), (100, float('nan')), (10 ** 400, 0)):
            self.assert_error("invalid_budget", transfer.require_budget, self.budget(), deadline=deadline, now=now)
        self.assert_error("invalid_budget", transfer.require_budget, replace(self.budget(), minimum_seconds=0), deadline=400, now=0)


if __name__ == '__main__':
    unittest.main()
