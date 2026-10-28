"""Bounded byte transport helpers, without a favorites schema or RPC client.

Digests always cover original bytes, never reserialized JSON. Chunk boundaries
may split UTF-8 code points; validate JSON only after complete reassembly.

The caller supplies a pure envelope builder. Its client bytes are the actual
serialized request, including idempotency metadata. Its queued_upper_bound must
include maximum-width placeholders for server-generated IDs/timestamps and all
other queued metadata. This module checks both sizes; it cannot prove that an
external builder correctly bounds a future server envelope.

Budget estimates are conservative, traffic-free lower bounds plus an explicit
receipt reserve. They do not reserve shared capacity or guarantee completion.
The caller owns one absolute deadline, pacing, backpressure and reconciliation.
Nothing here stages data, sends requests, sleeps or mutates player/config state.
"""

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_CHUNK_BYTES = 8 * 1024
MAX_RPC_BYTES = 16 * 1024
MAX_CHUNKS = 256
MIN_COMMAND_INTERVAL = 1.5
CONTROLLER_REQUESTS_PER_MINUTE = 240
DEVICE_REQUESTS_PER_MINUTE = 120

_MESSAGES = {
    "invalid_limits": "Invalid favorites transfer limits.",
    "invalid_bytes": "Invalid bounded favorites transfer bytes.",
    "invalid_json": "Favorites transfer data is not unambiguous finite UTF-8 JSON.",
    "invalid_encoding": "Invalid canonical favorites transfer encoding.",
    "size_mismatch": "Favorites transfer byte count does not match.",
    "digest_mismatch": "Favorites transfer digest does not match.",
    "invalid_envelope": "Could not build valid favorites transfer envelopes.",
    "envelope_too_large": "Favorites transfer envelope exceeds the request limit; use smaller chunks.",
    "too_many_chunks": "Favorites transfer exceeds the chunk count limit.",
    "invalid_budget": "Invalid favorites transfer deadline or request budget.",
    "deadline_insufficient": "Favorites transfer cannot fit the remaining deadline; nothing was staged.",
    "deadline_exhausted": "Favorites transfer cannot continue within the remaining deadline; reconcile any uncertain request before another change.",
}


class TransferError(Exception):
    """Fixed public text with a non-secret machine-readable code."""

    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True)
class EncodedBytes:
    size_bytes: int
    sha256: str
    data_base64: str = field(repr=False)


@dataclass(frozen=True)
class Chunk:
    seq: int
    offset: int
    payload: EncodedBytes


@dataclass(frozen=True)
class EnvelopeBytes:
    client: bytes = field(repr=False)
    queued_upper_bound: bytes = field(repr=False)


@dataclass(frozen=True)
class PackedChunk:
    chunk: Chunk
    envelopes: EnvelopeBytes


@dataclass(frozen=True)
class TransferPlan:
    size_bytes: int
    sha256: str
    chunks: tuple[PackedChunk, ...] = field(repr=False)


@dataclass(frozen=True)
class Budget:
    commands: int
    controller_requests: int
    device_requests: int
    paced_seconds: float
    http_seconds: float
    final_receipt_reserve: float
    minimum_seconds: float


@dataclass(frozen=True)
class DispatchWindow:
    start_at: float
    wait_seconds: float
    request_timeout: float


def _limit(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise TransferError("invalid_limits")
    return value


def _raw_bytes(raw, maximum):
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        raise TransferError("invalid_bytes")
    return raw


def validate_document_bytes(raw, *, max_bytes=MAX_DOCUMENT_BYTES):
    """Return parsed JSON; validate no document shape and retain input unchanged."""
    _raw_bytes(raw, _limit(max_bytes, MAX_DOCUMENT_BYTES))

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    def finite_number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    def reject_constant(_):
        raise ValueError

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=reject_constant, parse_float=finite_number)
        # Reject escaped unpaired surrogates as well as invalid raw UTF-8. This
        # serialization is validation only; it never supplies a digest/payload.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise TransferError("invalid_json") from None


def encode_bytes(raw, *, max_bytes=MAX_DOCUMENT_BYTES):
    """Encode original bytes, including arbitrary non-UTF-8 chunk fragments."""
    _raw_bytes(raw, _limit(max_bytes, MAX_DOCUMENT_BYTES))
    return EncodedBytes(len(raw), hashlib.sha256(raw).hexdigest(), base64.b64encode(raw).decode("ascii"))


def encode_document(raw, *, max_bytes=MAX_DOCUMENT_BYTES):
    """Validate strict JSON and encode the unchanged original document bytes."""
    validate_document_bytes(raw, max_bytes=max_bytes)
    return encode_bytes(raw, max_bytes=max_bytes)


def decode_bytes(data_base64, *, size_bytes, sha256, max_bytes=MAX_DOCUMENT_BYTES):
    """Strictly decode bytes and match both advertised byte count and digest."""
    maximum = _limit(max_bytes, MAX_DOCUMENT_BYTES)
    if (type(size_bytes) is not int or not 1 <= size_bytes <= maximum
            or not isinstance(sha256, str) or re.fullmatch(r"[a-f0-9]{64}", sha256) is None
            or not isinstance(data_base64, str) or not data_base64 or len(data_base64) > 4 * ((maximum + 2) // 3)):
        raise TransferError("invalid_encoding")
    try:
        raw = base64.b64decode(data_base64, validate=True)
        if base64.b64encode(raw).decode("ascii") != data_base64:
            raise ValueError
    except (ValueError, binascii.Error, UnicodeError):
        raise TransferError("invalid_encoding") from None
    if len(raw) != size_bytes:
        raise TransferError("size_mismatch")
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise TransferError("digest_mismatch")
    return raw


def decode_document(data_base64, *, size_bytes, sha256, max_bytes=MAX_DOCUMENT_BYTES):
    """Verify complete original bytes and strict JSON; return the original bytes."""
    raw = decode_bytes(data_base64, size_bytes=size_bytes, sha256=sha256, max_bytes=max_bytes)
    validate_document_bytes(raw, max_bytes=max_bytes)
    return raw


def request_envelopes(action, params, *, idempotency_key=None):
    """Build the existing /api/requests envelopes, without admitting an action.

    Client.call_receipt inserts action, params, then optional idempotency_key;
    Client._api uses default JSON separators and ensure_ascii=False. The Go
    requests handler removes the key and stores a sorted map of RawMessages,
    adding a 32-hex ID and float64 expires_at. Unlike legacy command enqueue it
    does not add ts. RawMessage compaction preserves parameter order and number
    spelling, while Go's default JSON encoder escapes HTML and line separators.

    The numeric placeholder is deliberately 32 bytes: a conservative upper
    bound on Go JSON's finite float64 encoding, not a future expiry timestamp.
    Neither this formatter nor pack_document validates an RPC action/schema.
    """
    if (not isinstance(action, str) or not action or not isinstance(params, dict)
            or (idempotency_key is not None and (not isinstance(idempotency_key, str)
                or re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", idempotency_key) is None))):
        raise TransferError("invalid_envelope")
    payload = {"action": action, "params": params}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    try:
        client = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        validate_document_bytes(client)

        def go_raw(value):
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                                       ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
                raw = raw.replace(character, escaped)
            return raw.encode("utf-8")

        queued = (b'{"action":' + go_raw(action) + b',"expires_at":-' + b'9' * 31
                  + b',"id":"' + b'f' * 32 + b'","params":' + go_raw(params) + b'}')
        validate_document_bytes(queued)
    except (TypeError, ValueError, UnicodeError, RecursionError, TransferError):
        raise TransferError("invalid_envelope") from None
    return EnvelopeBytes(client, queued)


def pack_document(raw, build_envelopes, *, chunk_bytes=MAX_CHUNK_BYTES,
                  rpc_bytes=MAX_RPC_BYTES, max_bytes=MAX_DOCUMENT_BYTES, max_chunks=MAX_CHUNKS):
    """Pack all fixed raw chunks or refuse; the pure builder sends nothing.

    build_envelopes(Chunk) -> EnvelopeBytes. To lower an advertised limit, pass
    the lower value explicitly. Larger-than-local limits are rejected. Oversize
    envelopes do not cause adaptive packing or any implicit request retry.
    """
    validate_document_bytes(raw, max_bytes=max_bytes)
    chunk_bytes = _limit(chunk_bytes, MAX_CHUNK_BYTES)
    rpc_bytes = _limit(rpc_bytes, MAX_RPC_BYTES)
    max_chunks = _limit(max_chunks, MAX_CHUNKS)
    if not callable(build_envelopes):
        raise TransferError("invalid_envelope")
    if (len(raw) + chunk_bytes - 1) // chunk_bytes > max_chunks:
        raise TransferError("too_many_chunks")
    chunks = []
    for seq, offset in enumerate(range(0, len(raw), chunk_bytes)):
        chunk = Chunk(seq, offset, encode_bytes(raw[offset:offset + chunk_bytes], max_bytes=chunk_bytes))
        try:
            envelopes = build_envelopes(chunk)
        except Exception:  # noqa: BLE001 - builder failures may contain credentials or payloads
            raise TransferError("invalid_envelope") from None
        if not isinstance(envelopes, EnvelopeBytes):
            raise TransferError("invalid_envelope")
        for envelope in (envelopes.client, envelopes.queued_upper_bound):
            if type(envelope) is not bytes:
                raise TransferError("invalid_envelope")
            if len(envelope) > rpc_bytes:
                raise TransferError("envelope_too_large")
            try:
                validate_document_bytes(envelope, max_bytes=rpc_bytes)
            except TransferError:
                raise TransferError("invalid_envelope") from None
        chunks.append(PackedChunk(chunk, envelopes))
    return TransferPlan(len(raw), hashlib.sha256(raw).hexdigest(), tuple(chunks))


def _count(value, *, minimum=0, maximum=1_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise TransferError("invalid_budget")
    return value


def _seconds(value, *, minimum=0):
    if type(value) not in (float, int):
        raise TransferError("invalid_budget")
    try:
        result = float(value)
    except OverflowError:
        raise TransferError("invalid_budget") from None
    if not math.isfinite(result) or result < minimum:
        raise TransferError("invalid_budget")
    return result


def estimate_budget(actual_chunk_count, *, control_commands, controller_requests,
                    device_requests, final_receipt_reserve, command_interval=MIN_COMMAND_INTERVAL,
                    controller_limit=CONTROLLER_REQUESTS_PER_MINUTE, device_limit=DEVICE_REQUESTS_PER_MINUTE):
    """Estimate from packed count and caller-accounted POST/GET/poll totals.

    Count all control commands and all expected requests on each credential,
    including polling; unrelated traffic remains unknown. One interval per
    command conservatively includes initial admission. Request-rate time uses
    sustained limits, not an assumption that a fresh burst budget is available.
    Additional latency/backpressure may exceed this necessary preflight bound.
    """
    count = _count(actual_chunk_count, minimum=1, maximum=MAX_CHUNKS)
    commands = count + _count(control_commands, minimum=1, maximum=MAX_CHUNKS)
    controller_requests = _count(controller_requests, minimum=commands)
    device_requests = _count(device_requests, minimum=commands)
    interval = _seconds(command_interval, minimum=MIN_COMMAND_INTERVAL)
    reserve = _seconds(final_receipt_reserve)
    if reserve <= 0:
        raise TransferError("invalid_budget")
    controller_limit = _limit(controller_limit, CONTROLLER_REQUESTS_PER_MINUTE)
    device_limit = _limit(device_limit, DEVICE_REQUESTS_PER_MINUTE)
    paced = commands * interval
    http = max(controller_requests * 60 / controller_limit, device_requests * 60 / device_limit)
    minimum = max(paced, http) + reserve
    if not math.isfinite(minimum):
        raise TransferError("invalid_budget")
    return Budget(commands, controller_requests, device_requests, paced, http, reserve, minimum)


def require_budget(budget, *, deadline, now):
    """Check the existing absolute deadline; never create/reset a chunk timeout."""
    if not isinstance(budget, Budget):
        raise TransferError("invalid_budget")
    deadline, now = _seconds(deadline), _seconds(now)
    minimum = _seconds(budget.minimum_seconds)
    if minimum <= 0:
        raise TransferError("invalid_budget")
    remaining = deadline - now
    if remaining < minimum:
        raise TransferError("deadline_insufficient")
    return remaining


def dispatch_window(*, deadline, now, final_receipt_reserve, remaining_commands=1,
                    previous_start=None, not_before=None, command_interval=MIN_COMMAND_INTERVAL):
    """Return a pure pacing/timeout decision against one existing deadline.

    previous_start is the actual preceding POST start, even when its outcome is
    unknown; do not pass an old nominal schedule. not_before may include server
    backpressure. After waiting, recompute with a fresh now before dispatch; a
    stale window must not become a fresh timeout. This function neither sleeps
    nor sends/replays requests. Uncertain outcomes still require reconciliation.

    The current call's timeout preserves spacing for the remaining commands and
    an explicit end reserve for final receipt/reconciliation. These are lower
    bounds, not reserved capacity or promises about network/player latency.
    """
    deadline, now = _seconds(deadline), _seconds(now)
    reserve = _seconds(final_receipt_reserve)
    interval = _seconds(command_interval, minimum=MIN_COMMAND_INTERVAL)
    remaining = _count(remaining_commands, minimum=1, maximum=2 * MAX_CHUNKS)
    if reserve <= 0:
        raise TransferError("invalid_budget")
    start = now
    if previous_start is not None:
        previous_start = _seconds(previous_start)
        if previous_start > now:
            raise TransferError("invalid_budget")
        start = max(start, previous_start + interval)
    if not_before is not None:
        start = max(start, _seconds(not_before))
    timeout = deadline - start - reserve - (remaining - 1) * interval
    if not math.isfinite(timeout) or timeout <= 0:
        raise TransferError("deadline_exhausted")
    return DispatchWindow(start, start - now, timeout)
