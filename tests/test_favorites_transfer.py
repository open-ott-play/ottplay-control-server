"""Byte and admission contracts only; synthetic envelopes are not a wire API."""

import hashlib
import importlib.util
import io
import json
import re
import sys
import threading
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_favorites_transfer_tested", ROOT / "cli/favorites_transfer.py")
transfer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transfer
SPEC.loader.exec_module(transfer)
CLI_SPEC = importlib.util.spec_from_file_location("ott_favorites_transfer_client", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(ott)
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


class FixtureResponse:
    def __init__(self, status, body):
        self.status = status
        self.stream = io.BytesIO(encode_json(body))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def read1(self, limit):
        return self.stream.read(limit)


class FavoritesTransferRequestIntegrationTests(TransferAssertions, unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.posts, self.http_timeouts = [], []
        self.pending = False
        self.client = object.__new__(ott.Client)
        self.client.server = 'https://fixture.invalid'
        self.client.token = 'synthetic-test-token'
        self.client.timeout = 30
        self.client.on_receipt = None
        self.client.opener = mock.Mock()
        self.client.opener.open.side_effect = self.open_fixture
        # Exercise call_receipt and the real _api JSON serializer synchronously.
        # The in-memory opener replaces all networking; no credentials/config
        # are read, and this does not retest the separate worker-thread wrapper.
        self.client.api = self.api_fixture
        clock = mock.patch.object(ott.time, 'monotonic', side_effect=lambda: self.now)
        sleeper = mock.patch.object(ott.time, 'sleep', side_effect=self.advance)
        clock.start()
        sleeper.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleeper.stop)

    def advance(self, seconds):
        self.now += seconds

    def api_fixture(self, path, payload=None, timeout=None):
        budget = min(10, self.client.timeout if timeout is None else timeout)
        self.http_timeouts.append(budget)
        return self.client._api(path, payload, budget, self.now + budget, threading.Event())

    def open_fixture(self, request, timeout):
        if request.get_method() == 'POST':
            self.posts.append((self.now, request.data))
            return FixtureResponse(202, {'id': f'{len(self.posts):032x}'})
        if self.pending:
            return FixtureResponse(202, {'status': 'pending'})
        return FixtureResponse(200, {'id': f'{len(self.posts):032x}', 'status': 'ok', 'data': {}})

    def test_envelope_matches_actual_client_bytes_with_optional_operation_key(self):
        params = {'provider': 'xtream', 'settings': {'title': 'Коты🙂<&>\u2028\u2029',
                                                   'z': 1e-5, 'a': -0.0, 'integer': 9007199254740991}}
        for key in (None, 'a' * 16, 'Z_' * 40):
            planned = transfer.request_envelopes('provider_settings', params, idempotency_key=key)
            self.client.call_receipt('fixture-device', 'provider_settings', params, idempotency_key=key)
            self.assertEqual(planned.client, self.posts[-1][1])
            expected_order = ['action', 'params'] + (['idempotency_key'] if key is not None else [])
            self.assertEqual(list(json.loads(planned.client)), expected_order)
            self.assertIn(b'": ', planned.client)
            self.assertIn('🙂'.encode(), planned.client)
            self.assertIn(b'1e-05', planned.queued_upper_bound)
            self.assertIn(b'-0.0', planned.queued_upper_bound)
            self.assertNotIn(b'idempotency_key', planned.queued_upper_bound)
            self.assertEqual(list(json.loads(planned.queued_upper_bound)), ['action', 'expires_at', 'id', 'params'])
            self.assertEqual(json.loads(planned.queued_upper_bound)['params'], params)
        self.assertEqual(len(self.posts), 3)

    def test_go_rawmessage_escaping_and_number_spelling_have_fixed_fixture(self):
        params = {'z': '<&>\u2028\u2029🙂', 'a': {'second': 1e-5, 'first': -0.0}}
        result = transfer.request_envelopes('provider_settings', params)
        expected = (b'{"action":"provider_settings","expires_at":-' + b'9' * 31
                    + b',"id":"' + b'f' * 32
                    + b'","params":{"z":"\\u003c\\u0026\\u003e\\u2028\\u2029'
                    + '🙂'.encode() + b'","a":{"second":1e-05,"first":-0.0}}}')
        self.assertEqual(result.queued_upper_bound, expected)
        self.assertLess(len(result.client), len(result.queued_upper_bound))
        # This is the existing Go source contract, not an invented favorites RPC.
        requests = (ROOT / 'internal/control/requests.go').read_text()
        decoding = (ROOT / 'internal/control/command.go').read_text()
        self.assertIn('func decodeObject(body []byte) (map[string]json.RawMessage, error)', decoding)
        self.assertIn('delete(m, "idempotency_key")', requests)
        self.assertIn('var random [16]byte', requests)
        self.assertIn('id := hex.EncodeToString(random[:])', requests)
        queue = requests.split('canonical, _ := json.Marshal(m)', 1)[1].split('s.mu.Lock()', 1)[0]
        self.assertEqual(re.findall(r'm\["([^"]+)"\], _ = json.Marshal', queue), ['id', 'expires_at'])
        self.assertIn('json.Marshal(float64(expires.UnixNano()) / 1e9)', queue)
        self.assertIn('data, _ := json.Marshal(m)', queue)
        self.assertIn('const MaxBodyBytes = 16 * 1024', (ROOT / 'internal/control/server.go').read_text())

    def test_current_client_spaces_and_go_escape_expansion_can_each_reject_packing(self):
        raw = b'{}'

        def client_limited(_):
            return transfer.request_envelopes('provider_settings', {'settings': {'title': 'x' * 300}}, idempotency_key='k' * 80)

        pair = client_limited(None)
        self.assertGreater(len(pair.client), len(pair.queued_upper_bound))
        self.assert_error('envelope_too_large', transfer.pack_document, raw, client_limited, rpc_bytes=len(pair.client) - 1)

        def queue_limited(_):
            return transfer.request_envelopes('provider_settings', {'settings': {'title': '<&>' * 2800}})

        pair = queue_limited(None)
        self.assertLess(len(pair.client), transfer.MAX_RPC_BYTES)
        self.assertGreater(len(pair.queued_upper_bound), transfer.MAX_RPC_BYTES)
        self.assert_error('envelope_too_large', transfer.pack_document, raw, queue_limited)

    def test_full_raw_chunk_packs_existing_envelope_without_network(self):
        raw = b'"' + b'x' * 8190 + b'"'

        def builder(chunk):
            # Synthetic settings value exercises the existing RPC envelope;
            # it is not a proposed favorites operation or a submitted command.
            return transfer.request_envelopes('provider_settings', {'provider': 'xtream', 'settings': {
                'payload': chunk.payload.data_base64, 'sha256': chunk.payload.sha256}}, idempotency_key='k' * 80)

        packed = transfer.pack_document(raw, builder)
        self.assertEqual(len(packed.chunks), 1)
        self.assertEqual(self.posts, [])
        self.client.opener.open.assert_not_called()
        self.assertLessEqual(len(packed.chunks[0].envelopes.client), 16384)
        self.assertLessEqual(len(packed.chunks[0].envelopes.queued_upper_bound), 16384)

    def test_invalid_envelope_values_are_fixed_errors(self):
        for action, params, key in ((None, {}, None), ('status', [], None), ('status', {}, 'short'),
                                    ('status', {}, True), ('status', {}, 'k' * 81),
                                    ('status', {'number': float('nan')}, None), ('status', {1: 'a', '1': 'b'}, None),
                                    ('status', {'unicode': '\ud800'}, None)):
            self.assert_error('invalid_envelope', transfer.request_envelopes, action, params, idempotency_key=key)
        self.assertEqual(self.posts, [])

    def test_paced_real_client_calls_share_absolute_deadline_and_do_not_catch_up(self):
        deadline, reserve, previous = 120, 5, None
        starts, timeouts = [], []
        for index in range(3):
            window = transfer.dispatch_window(deadline=deadline, now=self.now, previous_start=previous,
                                              remaining_commands=3 - index, final_receipt_reserve=reserve)
            self.advance(window.wait_seconds)
            # Any wait uses the same deadline and is followed by a fresh check.
            window = transfer.dispatch_window(deadline=deadline, now=self.now, previous_start=previous,
                                              remaining_commands=3 - index, final_receipt_reserve=reserve)
            previous = self.now
            starts.append(previous)
            timeouts.append(window.request_timeout)
            self.client.timeout = window.request_timeout
            self.client.call_receipt('fixture-device', 'status', {})
            if index == 1:
                self.advance(4)  # unrelated work does not authorize a catch-up burst
        self.assertEqual([round(value, 2) for value in starts], [100, 101.5, 106.3])
        self.assertEqual(len(self.posts), 3)
        self.assertTrue(all(later - earlier >= 1.5 for earlier, later in pairwise(starts)))
        self.assertLess(timeouts[-1], timeouts[0])
        self.assertLess(self.now, deadline - reserve)

    def test_pending_request_consumes_only_remaining_timeout_and_is_never_replayed(self):
        self.pending = True
        deadline, reserve = 110, 3
        window = transfer.dispatch_window(deadline=deadline, now=self.now, final_receipt_reserve=reserve)
        self.client.timeout = window.request_timeout
        with self.assertRaises(ott.RequestError) as error:
            self.client.call_receipt('fixture-device', 'status', {})
        self.assertEqual(error.exception.reason, 'request_pending_timeout')
        self.assertEqual(error.exception.action, 'status')
        self.assertEqual(error.exception.request_id, '0' * 31 + '1')
        self.assertEqual(len(self.posts), 1)
        self.assertAlmostEqual(self.now, deadline - reserve)
        self.assert_error('deadline_exhausted', transfer.dispatch_window,
                          deadline=deadline, now=self.now, previous_start=100, final_receipt_reserve=reserve)
        self.assertEqual(len(self.posts), 1)

    def test_backpressure_and_delayed_wakeup_cannot_reset_deadline(self):
        planned = transfer.dispatch_window(deadline=110, now=100, previous_start=100,
                                           not_before=104, final_receipt_reserve=5)
        self.assertEqual((planned.start_at, planned.wait_seconds, planned.request_timeout), (104, 4, 1))
        self.assert_error('deadline_exhausted', transfer.dispatch_window, deadline=110, now=105,
                          previous_start=100, not_before=104, final_receipt_reserve=5)
        for changes in ({'previous_start': 101}, {'command_interval': 1.4}, {'remaining_commands': True},
                        {'remaining_commands': 0}, {'final_receipt_reserve': 0}, {'not_before': float('nan')}):
            args = {'deadline': 120, 'now': 100, 'final_receipt_reserve': 5, **changes}
            self.assert_error('invalid_budget', transfer.dispatch_window, **args)
        self.assertEqual(self.posts, [])


if __name__ == '__main__':
    unittest.main()
