"""Bounded global recovery protocol and paced, explicit audit acknowledgement.

No storage, capability discovery or transfer activation occurs here. The host
supplies authenticated target/runtime evidence and private durable intent storage.
An observed audit never verifies or replays the original uncertain operation.
"""

import copy
import hashlib
import json
import queue
import sys
import threading
import time

import favorites_remote as transport
import favorites_transfer as transfer
import global_favorites_protocol as core

ACTION = "favorites_global_recovery"
OPS = frozenset(("inspect", "acknowledge"))
MAX_METADATA_BYTES = core.MAX_METADATA_BYTES
MAX_RESPONSE_BYTES = core.MAX_RESPONSE_BYTES
MAX_ENVELOPE_BYTES = 16384
MAX_RECORDS = 32
PAGE_SIZE = 16
EMPTY_LEDGER_SHA256 = hashlib.sha256(b"null").hexdigest()
REASON = "accept-unknown-no-replay"
ERRORS = frozenset(("invalid_request", "envelope_too_large", "runtime_changed", "expired", "busy",
                    "unsupported_persistence", "revision_conflict", "context_changed", "policy_denied",
                    "storage_unavailable", "limits_exceeded", "outcome_unknown"))
COMMON = frozenset(("version", "runtime", "op", "subject"))
REVISIONS = ("ledger_revision", "store_revision", "context_revision", "backend_proof")
INSPECT_META = frozenset(("ledger_format", *REVISIONS))
RECORD_FIELDS = frozenset(("operation_id", "operation_runtime", "subject", "original_state",
                            "original_outcome", "original_entry_sha256", "acknowledgement"))
AUDIT_FIELDS = frozenset(("acknowledgement_id", "runtime", "ledger_revision_before", "store_revision",
                         "context_revision", "backend_proof", "reason"))
ACK_FIELDS = frozenset(("runtime", "op", "subject", "operation_id", "operation_runtime", "original_entry_sha256",
                       "expected_ledger_revision", "expected_store_revision", "expected_context_revision",
                       "backend_proof", "reason", "acknowledgement_id", "after_ledger_sha256", "plan_sha256",
                       "accept_unknown"))
ProtocolError = core.ProtocolError
ValidatedResult = core.ValidatedResult
_require, _shape, _pattern, _integer = core._require, core._shape, core._pattern, core._integer
_object, _encoded = core._object, core._encoded


def validate_recovery_capability(value):
    return core.validate_capability(value)


def _ack_digest(runtime, operation, original_runtime, entry, ledger, store, context, backend):
    _pattern(runtime, core.RUNTIME)
    _pattern(original_runtime, core.RUNTIME)
    _pattern(operation, core.UUID)
    for digest in (entry, ledger, store, context, backend):
        _pattern(digest, core.DIGEST)
    _require(ledger != EMPTY_LEDGER_SHA256)
    values = [runtime, "global", operation, original_runtime, entry, ledger, store, context, backend, REASON]
    return hashlib.sha256(_encoded(["ottplay-favorites-global-recovery-ack-v1", *values])).hexdigest(), values


def recovery_digests(runtime, operation, original_runtime, entry, ledger, store, context, backend, after):
    """Recompute the fixed ordered tuple, not a reserialization of private data."""
    acknowledgement, values = _ack_digest(runtime, operation, original_runtime, entry, ledger, store, context, backend)
    _pattern(after, core.DIGEST)
    _require(after not in (EMPTY_LEDGER_SHA256, ledger))
    plan = hashlib.sha256(_encoded(["ottplay-favorites-global-recovery-plan-v1", *values, acknowledgement, after])).hexdigest()
    return acknowledgement, plan


def _request_digests(value):
    return recovery_digests(value["runtime"], value["operation_id"], value["operation_runtime"],
                            value["original_entry_sha256"], value["expected_ledger_revision"],
                            value["expected_store_revision"], value["expected_context_revision"],
                            value["backend_proof"], value["after_ledger_sha256"])


def _cursor(value):
    _shape(value, ("after_operation_id", *REVISIONS))
    _pattern(value["after_operation_id"], core.UUID)
    _revisions(value)
    _require(value["ledger_revision"] != EMPTY_LEDGER_SHA256)


def _revisions(value):
    for key in REVISIONS[:-1]:
        _pattern(value[key], core.DIGEST)
    if value["backend_proof"] is not None:
        _pattern(value["backend_proof"], core.DIGEST)


def validate_request(params):
    value, _ = _object(params, MAX_ENVELOPE_BYTES)
    _pattern(value.get("runtime"), core.RUNTIME)
    _require(value.get("subject") == "global" and type(value.get("op")) is str and value["op"] in OPS)
    if value["op"] == "inspect":
        if "operation_id" in value:
            _shape(value, ("runtime", "op", "subject", "operation_id"))
            _pattern(value["operation_id"], core.UUID)
        else:
            _shape(value, ("runtime", "op", "subject", "cursor"))
            if value["cursor"] is not None:
                _cursor(value["cursor"])
    else:
        _shape(value, ACK_FIELDS)
        _require(value["reason"] == REASON and value["accept_unknown"] is True)
        ack, plan = _request_digests(value)
        _require(value["acknowledgement_id"] == ack and value["plan_sha256"] == plan)
    try:
        envelopes = transfer.request_envelopes(ACTION, value)
    except transfer.TransferError:
        raise ProtocolError() from None
    _require(max(len(envelopes.client), len(envelopes.queued_upper_bound)) <= MAX_ENVELOPE_BYTES,
             "envelope_too_large")
    return value


def _record(value):
    _shape(value, RECORD_FIELDS)
    _pattern(value["operation_id"], core.UUID)
    _pattern(value["operation_runtime"], core.RUNTIME)
    _pattern(value["original_entry_sha256"], core.DIGEST)
    _require(value["subject"] == "global" and type(value["original_state"]) is str
             and value["original_state"] in ("pending", "unknown", "partial"))
    _require(value["original_outcome"] == ("partial" if value["original_state"] == "partial" else "unknown"))
    audit = value["acknowledgement"]
    if audit is not None:
        _shape(audit, AUDIT_FIELDS)
        _require(audit["reason"] == REASON)
        acknowledgement, _ = _ack_digest(audit["runtime"], value["operation_id"], value["operation_runtime"],
                                          value["original_entry_sha256"], audit["ledger_revision_before"],
                                          audit["store_revision"], audit["context_revision"], audit["backend_proof"])
        _require(audit["acknowledgement_id"] == acknowledgement)


def _audit(value):
    return {"acknowledgement_id": value["acknowledgement_id"], "runtime": value["runtime"],
            "ledger_revision_before": value["expected_ledger_revision"],
            "store_revision": value["expected_store_revision"], "context_revision": value["expected_context_revision"],
            "backend_proof": value["backend_proof"], "reason": REASON}


def recovery_audit(request):
    value = validate_request(request)
    _require(value["op"] == "acknowledge")
    return _audit(value)


def _page(params, data):
    _shape(data, COMMON | INSPECT_META | {"total", "records", "next_cursor"})
    _integer(data["total"], 0, MAX_RECORDS)
    records = data["records"]
    _require(type(records) is list and len(records) <= PAGE_SIZE)
    for row in records:
        _record(row)
    ids = [row["operation_id"] for row in records]
    _require(ids == sorted(set(ids)))
    cursor = params["cursor"]
    if cursor is None:
        _require(len(records) == min(PAGE_SIZE, data["total"]))
        remains = data["total"] > PAGE_SIZE
    else:
        _require(all(data[key] == cursor[key] for key in REVISIONS), "context_mismatch")
        _require(PAGE_SIZE < data["total"] <= MAX_RECORDS
                 and len(records) == data["total"] - PAGE_SIZE
                 and ids[0] > cursor["after_operation_id"])
        remains = False
    if remains:
        _cursor(data["next_cursor"])
        expected = {"after_operation_id": ids[-1], **{key: data[key] for key in REVISIONS}}
        _require(data["next_cursor"] == expected)
    else:
        _require(data["next_cursor"] is None)
    if data["total"]:
        _require(data["ledger_revision"] != EMPTY_LEDGER_SHA256)


def _inspection(params, data):
    _shape(data, COMMON | INSPECT_META | {"operation_id", "record", "acknowledge_allowed", "acknowledgement_id",
                                        "after_ledger_sha256", "plan_sha256"})
    _require(data["operation_id"] == params["operation_id"] and type(data["acknowledge_allowed"]) is bool)
    record = data["record"]
    if record is not None:
        _record(record)
        _require(record["operation_id"] == params["operation_id"] and data["ledger_revision"] != EMPTY_LEDGER_SHA256)
    if data["acknowledge_allowed"]:
        _require(record is not None and record["acknowledgement"] is None)
        ack, plan = recovery_digests(data["runtime"], data["operation_id"], record["operation_runtime"],
                                    record["original_entry_sha256"], data["ledger_revision"], data["store_revision"],
                                    data["context_revision"], data["backend_proof"], data["after_ledger_sha256"])
        _require(data["acknowledgement_id"] == ack and data["plan_sha256"] == plan)
    else:
        _require(all(data[key] is None for key in ("acknowledgement_id", "after_ledger_sha256", "plan_sha256")))


def _acknowledged(params, data):
    _shape(data, COMMON | {"operation_id", "operation_runtime", "acknowledgement_id", "plan_sha256", "acknowledged",
                          "persistence", "ledger_revision", "store_revision", "context_revision", "record"})
    _require(data["acknowledged"] is True and data["persistence"] == "persistent")
    for key in ("operation_id", "operation_runtime", "acknowledgement_id", "plan_sha256"):
        _require(data[key] == params[key], "context_mismatch")
    _require(data["ledger_revision"] == params["after_ledger_sha256"]
             and data["store_revision"] == params["expected_store_revision"]
             and data["context_revision"] == params["expected_context_revision"], "context_mismatch")
    row = data["record"]
    _record(row)
    _require(all(row[key] == params[key] for key in ("operation_id", "operation_runtime", "original_entry_sha256"))
             and row["acknowledgement"] == _audit(params), "context_mismatch")


def validate_result(params, data, *, request_id, response_id, status, context=None,
                    scope_validator=None, max_metadata_bytes=MAX_METADATA_BYTES):
    _require(scope_validator is None)
    _integer(max_metadata_bytes, 1, MAX_METADATA_BYTES)
    params = validate_request(params)
    _pattern(request_id, core.REQUEST_ID)
    _require(response_id == request_id and type(status) is str and status in ("ok", "rejected", "unsupported"))
    data, raw = _object(data, max_metadata_bytes)
    _integer(data.get("version"), 1, 1)
    _require(data.get("runtime") == params["runtime"] and data.get("op") == params["op"] and data.get("subject") == "global")
    if "error" in data:
        _shape(data, COMMON | {"error"})
        _require(status in ("rejected", "unsupported") and type(data["error"]) is str and data["error"] in ERRORS)
    else:
        _require(status == "ok")
        if params["op"] == "inspect":
            _integer(data.get("ledger_format"), 1, 1)
            # Exact shape is checked below before optional nullable proof use.
            _require(all(key in data for key in REVISIONS))
            _revisions(data)
            if "cursor" in params:
                _page(params, data)
            else:
                _inspection(params, data)
        else:
            _acknowledged(params, data)
        if context is not None:
            original = _checked_inspection(context)
            _require(params == recovery_ack_request(original), "context_mismatch")
            _require(data["record"] == {**original["record"], "acknowledgement": _audit(params)}, "context_mismatch")
    return ValidatedResult(_encoded(params), raw, status, request_id)


def validate_response(params, response, *, request_id, context=None, scope_validator=None,
                      max_metadata_bytes=MAX_METADATA_BYTES):
    """Keep the original data token bytes for both success and error limits."""
    value, raw = _object(response, MAX_RESPONSE_BYTES)
    _shape(value, ("id", "status", "data"))
    text, decoder = raw.decode("utf-8"), json.JSONDecoder()
    position = text.index("{") + 1
    while True:
        while text[position].isspace() or text[position] == ",":
            position += 1
        key, position = decoder.raw_decode(text, position)
        while text[position].isspace():
            position += 1
        position += 1
        while text[position].isspace():
            position += 1
        start = position
        _, position = decoder.raw_decode(text, position)
        if key == "data":
            data = text[start:position].encode("utf-8")
            break
    return validate_result(params, data, request_id=request_id, response_id=value["id"], status=value["status"],
                           context=context, scope_validator=scope_validator, max_metadata_bytes=max_metadata_bytes)


def _checked_inspection(value):
    if type(value) is ValidatedResult:
        _require(value.status == "ok")
        value = value.data
    data, _ = _object(value, MAX_METADATA_BYTES)
    params = {"op": "inspect", "runtime": data.get("runtime"), "subject": "global", "operation_id": data.get("operation_id")}
    return validate_result(params, data, request_id="0" * 32, response_id="0" * 32, status="ok").data


def recovery_ack_request(inspection):
    data = _checked_inspection(inspection)
    _require(data["acknowledge_allowed"] is True, "context_mismatch")
    record = data["record"]
    request = {"runtime": data["runtime"], "op": "acknowledge", "subject": "global", "operation_id": data["operation_id"],
               "operation_runtime": record["operation_runtime"], "original_entry_sha256": record["original_entry_sha256"],
               "expected_ledger_revision": data["ledger_revision"], "expected_store_revision": data["store_revision"],
               "expected_context_revision": data["context_revision"], "backend_proof": data["backend_proof"], "reason": REASON,
               "acknowledgement_id": data["acknowledgement_id"], "after_ledger_sha256": data["after_ledger_sha256"],
               "plan_sha256": data["plan_sha256"], "accept_unknown": True}
    return validate_request(request)


class RecoveryPeer(transport._RequestTransport):
    """Global read/audit operations only; no route discovery or original replay."""
    _protocol = sys.modules[__name__]
    _action = ACTION

    def __init__(self, client, device, *, runtime, timeout=45, monotonic=time.monotonic,
                 sleep=time.sleep, deadline=None):
        super().__init__(client, device, runtime=runtime, envelope_limit=MAX_ENVELOPE_BYTES,
                         response_limit=MAX_RESPONSE_BYTES, scope_validator=None, timeout=timeout,
                         monotonic=monotonic, sleep=sleep, max_metadata_bytes=MAX_METADATA_BYTES,
                         deadline=deadline, allowed_ops=OPS)
        self._attempted = set()
        self._ack_lock = threading.Lock()

    def list(self):
        cursor, first, rows = None, None, []
        # At most two pages for the frozen 32-row / 16-row bound.
        for _ in range(2):
            result = self._rpc({"op": "inspect", "runtime": self.runtime, "subject": "global", "cursor": cursor},
                               deadline=self._deadline)
            data = result.data
            if first is None:
                first = data
            elif any(data[key] != first[key] for key in (*REVISIONS, "total", "ledger_format")):
                raise transport.RemoteError("invalid_response")
            rows.extend(data["records"])
            cursor = data["next_cursor"]
            if cursor is None:
                ids = [row["operation_id"] for row in rows]
                if len(rows) != first["total"] or ids != sorted(set(ids)):
                    raise transport.RemoteError("invalid_response")
                return {"status": "complete", "runtime": self.runtime, "subject": "global",
                        **{key: first[key] for key in (*INSPECT_META, "total")}, "records": rows}
        raise transport.RemoteError("invalid_response")

    def inspect(self, operation_id):
        return self._rpc({"op": "inspect", "runtime": self.runtime, "subject": "global", "operation_id": operation_id},
                         deadline=self._deadline).data

    def _save(self, inspection, request, save_intent, deadline):
        completed = queue.Queue(maxsize=1)
        def retain():
            try:
                result = save_intent(copy.deepcopy(inspection), copy.deepcopy(request))
            except Exception:  # noqa: BLE001 - private storage errors stay private
                result = None
            completed.put(result)
        threading.Thread(target=retain, name="ott-global-recovery-intent", daemon=True).start()
        wall = time.perf_counter() + max(0, deadline - self._now())
        while self._now() < deadline and time.perf_counter() < wall:
            try:
                saved = completed.get(timeout=max(0, min(1, deadline - self._now(), wall - time.perf_counter())))
                return type(saved) is bool
            except queue.Empty:
                continue
        return False

    def acknowledge(self, inspection, confirmation, save_intent):
        try:
            original = _checked_inspection(inspection)
            request = recovery_ack_request(original)
        except ProtocolError:
            raise transport.RemoteError("invalid_context") from None
        if (request["runtime"] != self.runtime or type(confirmation) is not str
                or confirmation != request["plan_sha256"] or not callable(save_intent)):
            raise transport.RemoteError("invalid_context")
        base = {"operation_id": request["operation_id"], "acknowledgement_id": request["acknowledgement_id"],
                "plan_sha256": request["plan_sha256"], "original_outcome": original["record"]["original_outcome"],
                "replay_allowed": False}
        # Prevent concurrent callbacks/POSTs and repeat attempts on this peer.
        # A later explicitly confirmed invocation must freshly inspect again.
        if not self._ack_lock.acquire(blocking=False):
            raise transport.RemoteError("already_started")
        try:
            if request["plan_sha256"] in self._attempted:
                return {**base, "status": "blocked", "reason": "already_started"}
            cutoff = self._deadline - transport.RECONCILE_RESERVE
            if cutoff - self._now() <= transport.POST_INTERVAL + transport.HTTP_INTERVAL:
                return {**base, "status": "blocked", "reason": "deadline_insufficient"}
            if not self._save(original, request, save_intent, cutoff):
                return {**base, "status": "blocked", "reason": "context_not_saved"}
            if cutoff - self._now() <= transport.POST_INTERVAL + transport.HTTP_INTERVAL:
                return {**base, "status": "blocked", "reason": "deadline_insufficient"}
            self._attempted.add(request["plan_sha256"])
            try:
                result = self._rpc(request, deadline=cutoff, context=original)
                return {**base, "status": "acknowledgement_confirmed", "evidence": result.data,
                        "request_id": result.request_id}
            except transport.RemoteError:
                pass
            # One read-only reconciliation; neither failure nor a missing audit
            # authorizes a second acknowledgement or original transfer.
            try:
                observed = self.inspect(request["operation_id"])
                if observed["record"] == {**original["record"], "acknowledgement": _audit(request)}:
                    return {**base, "status": "acknowledgement_observed", "evidence": observed}
            except transport.RemoteError:
                pass
            return {**base, "status": "unknown", "reason": "acknowledgement_unconfirmed"}
        finally:
            self._ack_lock.release()
