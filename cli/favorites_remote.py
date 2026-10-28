"""Favorites transport: one deadline, paced HTTP, no POST replay.

The host supplies verified runtime/limits, scope codec and durable storage
bound to controller/device. No discovery, CLI wiring or filesystem I/O here.
"""
import copy
import hashlib
import json
import math
import queue
import re
import threading
import time
import urllib.parse
import uuid
from collections import deque
from dataclasses import dataclass, field

import favorites_protocol as protocol
import favorites_transfer as transfer

ACTION = "favorites_transfer"
POST_INTERVAL, HTTP_INTERVAL, POLL_INTERVAL = 1.5, 0.5, 1.5
RECONCILE_RESERVE = 10.0
MAX_RECOVERY_BYTES = 128 * 1024
PLACEHOLDER_TRANSACTION = "00000000-0000-4000-8000-000000000000"
RECOVERY_FIELDS = frozenset(("version", "operation_id", "runtime", "begin_request", "begin_response",
                             "begin_request_id", "preview_request", "preview_response", "preview_request_id",
                             "commit_request"))
ERROR_CODES = frozenset(("invalid_configuration", "invalid_snapshot", "invalid_document", "invalid_context",
                         "deadline_insufficient", "deadline", "clock_invalid", "already_started", "invalid_response",
                         "transport", "http_error", "pending_timeout", "player_rejected", "player_unsupported",
                         "not_ready", "context_not_saved"))


class RemoteError(ValueError):
    """Fixed diagnostics; never expose exception text, URLs, payloads or stage IDs."""
    def __init__(self, code, *, request_id=None, phase=None, http_status=None):
        self.code = code if code in ERROR_CODES else "invalid_response"
        self.request_id, self.phase, self.http_status = request_id, phase, http_status
        super().__init__("Favorites transfer: " + self.code.replace("_", " ") + ".")


@dataclass(frozen=True)
class Limits:
    """All fields come from an explicitly validated adapter advertisement."""
    max_document_bytes: int
    max_chunk_bytes: int
    max_chunks: int
    max_envelope_bytes: int
    max_snapshot_response_bytes: int
    max_active_stages: int
    idle_ttl_seconds: int
    absolute_ttl_seconds: int

    def validate(self):
        maxima = (1048576, 8192, 256, 16384, 2097152, 1, 120, 600)
        values = tuple(getattr(self, key) for key in self.__dataclass_fields__)
        if (any(type(v) is not int or not 1 <= v <= m for v, m in zip(values, maxima, strict=True))
                or self.idle_ttl_seconds > self.absolute_ttl_seconds):
            raise RemoteError("invalid_configuration")
        return self


@dataclass(frozen=True)
class Snapshot:
    _target: tuple = field(repr=False)
    _result: protocol.ValidatedResult = field(repr=False)

    @property
    def data(self):
        return self._result.data

    @property
    def document_bytes(self):
        return self._result.raw_document


@dataclass(frozen=True)
class BindingCatalog:
    _target: tuple = field(repr=False)
    _result: protocol.ValidatedResult = field(repr=False)

    @property
    def data(self):
        return self._result.data


@dataclass(frozen=True)
class Outcome:
    operation_id: str | None
    outcome: str
    verified: bool
    reason: str
    request_id: str | None = None
    cleanup: str = "not_needed"
    result: protocol.ValidatedResult | None = field(default=None, repr=False)

    def summary(self):
        return {**({"operation_id": self.operation_id} if self.operation_id is not None else {}),
                "outcome": self.outcome, "verified": self.verified,
                "reason": self.reason, "request_id": self.request_id, "cleanup": self.cleanup}


@dataclass(frozen=True)
class PreviewOutcome:
    """Validated dry-run evidence, never a committed or persisted receipt."""
    operation_id: str | None
    reason: str
    request_id: str | None = None
    cleanup: str = "unconfirmed"
    _result: protocol.ValidatedResult | None = field(default=None, repr=False)
    outcome: str = field(default="not_applied", init=False)

    @property
    def data(self):
        return self._result.data if self._result is not None else None

    @property
    def ready(self):
        return self.data["ready"] if self._result is not None else None

    def summary(self):
        return {**({"operation_id": self.operation_id} if self.operation_id is not None else {}),
                "outcome": self.outcome, "ready": self.ready, "reason": self.reason,
                "request_id": self.request_id, "cleanup": self.cleanup}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _restore(record):
    try:
        if (type(record) is not dict or set(record) != RECOVERY_FIELDS or type(record["version"]) is not int
                or record["version"] != 1 or len(_json(record)) > MAX_RECOVERY_BYTES):
            raise RemoteError("invalid_context")
        saved = json.loads(_json(record))
        begin = protocol.validate_result(saved["begin_request"], saved["begin_response"],
                                         request_id=saved["begin_request_id"], response_id=saved["begin_request_id"], status="ok")
        preview = protocol.validate_result(saved["preview_request"], saved["preview_response"],
                                           request_id=saved["preview_request_id"], response_id=saved["preview_request_id"], status="ok")
        context = protocol.retain_context(begin, preview)
        expected = {"op": "commit", "runtime": begin.request["runtime"],
                    "transaction_id": begin.data["transaction_id"], "operation_id": begin.request["operation_id"],
                    "expected_revision": begin.request["expected_revision"], "plan_digest": preview.data["plan_digest"]}
        if (saved["commit_request"] != expected or saved["runtime"] != expected["runtime"]
                or saved["operation_id"] != expected["operation_id"]):
            raise RemoteError("invalid_context")
        protocol.validate_request(expected)
        return saved, context
    except (KeyError, TypeError, ValueError, RecursionError):
        raise RemoteError("invalid_context") from None


def validate_recovery_record(record):
    """Storage validator: no document/base64/URL or filesystem access."""
    return _restore(record)[0]


def restore_context(record):
    """Restore receipt bindings after CLI restart; never authorize commit replay."""
    return _restore(record)[1]


def recovery_record(begin, preview, commit_request):
    return validate_recovery_record({
        "version": 1, "operation_id": begin.request["operation_id"], "runtime": begin.request["runtime"],
        "begin_request": begin.request, "begin_response": begin.data, "begin_request_id": begin.request_id,
        "preview_request": preview.request, "preview_response": preview.data, "preview_request_id": preview.request_id,
        "commit_request": commit_request,
    })


class _RequestTransport:
    """Shared bounded HTTP mechanics, without granting mutation support."""
    def __init__(self, client, device, *, runtime, envelope_limit, response_limit, scope_validator,
                 timeout, monotonic, sleep, max_metadata_bytes, deadline, allowed_ops=None):
        if (not callable(getattr(client, "api", None)) or not callable(getattr(client, "api_raw", None)) or type(device) is not str
                or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", device) is None
                or type(runtime) is not str or protocol.RUNTIME.fullmatch(runtime) is None
                or not callable(monotonic) or not callable(sleep) or type(timeout) not in (int, float)
                or not math.isfinite(timeout) or not 0 < timeout <= 300
                or type(max_metadata_bytes) is not int or not 1 <= max_metadata_bytes <= protocol.MAX_METADATA_BYTES
                or type(envelope_limit) is not int or not 1 <= envelope_limit <= 16384
                or type(response_limit) is not int or not 1 <= response_limit <= protocol.MAX_RESPONSE_BYTES
                or deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline))):
            raise RemoteError("invalid_configuration")
        server = getattr(client, "server", None)
        if type(server) is not str or not server or len(server) > 2048:
            raise RemoteError("invalid_configuration")
        self.client, self.device, self.runtime = client, device, runtime
        self._envelope_limit, self._response_limit = envelope_limit, response_limit
        self._allowed_ops = allowed_ops
        self.max_metadata_bytes = max_metadata_bytes
        self.scope_validator = scope_validator
        self.clock, self.sleep = monotonic, sleep
        self._clock_last = None
        started = self._now()
        self._deadline = min(started + timeout, deadline) if deadline is not None else started + timeout
        if self._deadline <= started:
            raise RemoteError("deadline")
        self._target = (hashlib.sha256(server.encode("utf-8")).hexdigest(), device, runtime)
        self._query = "/api/requests?" + urllib.parse.urlencode({"device_id": device})
        self._last_post = self._last_http = None
        self._http_times = deque()

    def _now(self):
        value = self.clock()
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or self._clock_last is not None and value < self._clock_last):
            raise RemoteError("clock_invalid")
        self._clock_last = float(value)
        return float(value)

    def _wait(self, target, deadline):
        while True:
            now = self._now()
            if now >= deadline or target >= deadline:
                raise RemoteError("deadline")
            if now >= target:
                return
            self.sleep(min(5, target - now))
            if self._now() <= now:
                raise RemoteError("clock_invalid")

    def _slot(self, *, post, deadline, not_before=0):
        now = self._now()
        while self._http_times and self._http_times[0] <= now - 60:
            self._http_times.popleft()
        start = max(now, not_before)
        if self._last_http is not None:
            start = max(start, self._last_http + HTTP_INTERVAL)
        if post and self._last_post is not None:
            start = max(start, self._last_post + POST_INTERVAL)
        # Conservatively enforce the lower controller240/device120 HTTP budget.
        # Shared traffic is unknown and may still cause server backpressure.
        if len(self._http_times) >= 120:
            start = max(start, self._http_times[0] + 60)
        self._wait(start, deadline)

    def _api(self, path, payload, *, deadline, not_before=0):
        server = getattr(self.client, "server", None)
        if (type(server) is not str or hashlib.sha256(server.encode("utf-8")).hexdigest() != self._target[0]):
            raise RemoteError("invalid_configuration")
        self._slot(post=payload is not None, deadline=deadline, not_before=not_before)
        started = self._now()
        self._last_http = started
        if payload is not None:
            self._last_post = started
        self._http_times.append(started)
        try:
            call = self.client.api if payload is not None else self.client.api_raw
            response = call(path, payload, timeout=min(10, deadline - started))
        except Exception as error:  # noqa: BLE001 - transport text may expose credentials
            code = getattr(error, "code", None)
            if type(code) is int and 100 <= code <= 599:
                raise RemoteError("http_error", http_status=code) from None
            category = getattr(error, "category", None)
            if isinstance(error, OSError) or category in ("deadline", "dns", "tls", "socket", "protocol", "truncated", "unknown"):
                raise RemoteError("transport") from None
            raise RemoteError("invalid_response") from None
        if self._now() >= deadline:
            raise RemoteError("deadline")
        if type(response) is not tuple or len(response) != 2 or type(response[0]) is not int:
            raise RemoteError("invalid_response")
        return response

    def _params(self, params):
        try:
            params = protocol.validate_request(params)
            if self._allowed_ops is not None and params["op"] not in self._allowed_ops:
                raise RemoteError("invalid_configuration")
            envelopes = transfer.request_envelopes(ACTION, params)
            if max(len(envelopes.client), len(envelopes.queued_upper_bound)) > self._envelope_limit:
                raise RemoteError("invalid_configuration")
            return params
        except (protocol.ProtocolError, transfer.TransferError):
            raise RemoteError("invalid_response") from None

    def _rpc(self, params, *, deadline, context=None):
        params = self._params(params)
        request_id, phase = None, "enqueue"
        try:
            status, queued = self._api(self._query, {"action": ACTION, "params": copy.deepcopy(params)}, deadline=deadline)
            if (status != 202 or type(queued) is not dict or set(queued) != {"id", "status"}
                    or queued["status"] != "queued" or type(queued["id"]) is not str
                    or protocol.REQUEST_ID.fullmatch(queued["id"]) is None):
                raise RemoteError("invalid_response")
            request_id, phase = queued["id"], "receipt"
            path = self._query + "&id=" + request_id
            not_before, delay, last_error = 0, POLL_INTERVAL, None
            while self._now() < deadline:
                try:
                    status, body = self._api(path, None, deadline=deadline, not_before=not_before)
                except RemoteError as error:
                    if error.code == "transport" or error.code == "http_error" and error.http_status in (429, 502, 503, 504):
                        last_error = error.code
                        delay = min(8, max(2, delay * 2))
                        not_before = self._now() + delay
                        continue
                    raise
                if type(body) is not bytes or len(body) > self._response_limit:
                    raise RemoteError("invalid_response")
                if status == 202:
                    try:
                        pending = transfer.validate_document_bytes(body, max_bytes=min(self.max_metadata_bytes,
                                                                                       self._response_limit))
                    except transfer.TransferError:
                        raise RemoteError("invalid_response") from None
                    if pending != {"status": "pending"}:
                        raise RemoteError("invalid_response")
                    last_error, delay = None, POLL_INTERVAL
                    not_before = self._now() + delay
                    continue
                if status != 200:
                    raise RemoteError("invalid_response")
                try:
                    result = protocol.validate_response(params, body, request_id=request_id, context=context,
                                                        scope_validator=self.scope_validator,
                                                        max_metadata_bytes=self.max_metadata_bytes)
                except (protocol.ProtocolError, TypeError, ValueError, RecursionError):
                    raise RemoteError("invalid_response") from None
                if result.status != "ok":
                    raise RemoteError("player_rejected" if result.status == "rejected" else "player_unsupported")
                return result
            raise RemoteError(last_error or "pending_timeout")
        except RemoteError as error:
            raise RemoteError(error.code, request_id=request_id, phase=phase, http_status=error.http_status) from None

    def _observe_unverified(self, record):
        saved, context = _restore(record)
        operation = saved["operation_id"]
        try:
            response = self._rpc({"op": "status", "runtime": self.runtime, "operation_id": operation},
                deadline=self._deadline, context=context if saved["runtime"] == self.runtime else None)
            # Across a restart there is no authority to derive a new receipt
            # context without the snapshot. Check all retained identities but
            # never promote even a matching claimed receipt to verified.
            data, begin, preview = response.data, saved["begin_request"], saved["preview_response"]
            if ((saved["runtime"] != self.runtime and data.get("state") != "committed")
                    or ("scope" in data and data["scope"] != begin["scope"])
                    or ("transaction_id" in data and data["transaction_id"] != saved["begin_response"]["transaction_id"])
                    or not {row["id"] for row in data.get("per_store", [])} <= {row["id"] for row in preview["stores"]}):
                raise RemoteError("invalid_response", request_id=response.request_id)
            receipt = data.get("receipt")
            if receipt is not None and (any(receipt[key] != value for key, value in {
                    "operation_id": operation, "scope": begin["scope"], "document_sha256": begin["document_sha256"],
                    "plan_digest": preview["plan_digest"], "counts": preview["counts"]}.items())
                    or {row["id"] for row in data["per_store"]} != {row["id"] for row in preview["stores"]}):
                raise RemoteError("invalid_response", request_id=response.request_id)
            return Outcome(operation, "unknown", False, "incomplete_evidence", response.request_id)
        except RemoteError as error:
            return Outcome(operation, "unknown", False, error.code, error.request_id)


class StatusReader(_RequestTransport):
    """Unqualified read-only observation, never a capability advertisement.

    The host must load context from the exact controller/device private store
    and validate fresh generic player metadata first. Protocol ceilings bound
    this one read; they do not assert supported transfer limits or retention.
    """
    def __init__(self, client, device, *, runtime, timeout=45, monotonic=time.monotonic,
                 sleep=time.sleep, deadline=None, envelope_limit=16384,
                 response_limit=protocol.MAX_RESPONSE_BYTES, max_metadata_bytes=protocol.MAX_METADATA_BYTES):
        super().__init__(client, device, runtime=runtime, envelope_limit=envelope_limit,
            response_limit=response_limit, scope_validator=None, timeout=timeout,
            monotonic=monotonic, sleep=sleep, max_metadata_bytes=max_metadata_bytes,
            deadline=deadline, allowed_ops=frozenset(("status",)))

    def observe(self, record):
        return self._observe_unverified(record)


class RemoteFavorites(_RequestTransport):
    def __init__(self, client, device, *, runtime, limits, receipt_retention_seconds, scope_validator,
                 save_context, timeout=45, monotonic=time.monotonic, sleep=time.sleep, uuid_factory=uuid.uuid4,
                 max_metadata_bytes=protocol.MAX_METADATA_BYTES, deadline=None,
                 admission_ttl_ms=10000, max_budget_ms=300000):
        if (type(limits) is not Limits or type(receipt_retention_seconds) is not int
                or not 1 <= receipt_retention_seconds <= 86400 or not callable(scope_validator)
                or not callable(save_context) or not callable(uuid_factory)
                or type(admission_ttl_ms) is not int or not 1 <= admission_ttl_ms <= 10000
                or type(max_budget_ms) is not int or not 1 <= max_budget_ms <= 300000):
            raise RemoteError("invalid_configuration")
        self.limits = limits.validate()
        super().__init__(client, device, runtime=runtime, envelope_limit=limits.max_envelope_bytes,
            response_limit=limits.max_snapshot_response_bytes, scope_validator=scope_validator,
            timeout=timeout, monotonic=monotonic, sleep=sleep, max_metadata_bytes=max_metadata_bytes, deadline=deadline)
        self.receipt_retention_seconds = receipt_retention_seconds
        self.admission_ttl_ms, self.max_budget_ms = admission_ttl_ms, max_budget_ms
        self.save_context, self.uuid_factory = save_context, uuid_factory
        self._staging_started = False

    def snapshot(self, scope):
        """Read a complete snapshot; do not change provider, favorites or UI."""
        response = self._rpc({"op": "snapshot", "runtime": self.runtime, "scope": scope}, deadline=self._deadline)
        if response.data["size_bytes"] > self.limits.max_document_bytes:
            raise RemoteError("invalid_response", request_id=response.request_id, phase="receipt")
        return Snapshot(self._target, response)

    def catalog(self):
        """Read the current target projection under this adapter's deadline."""
        result = self._rpc({"op": "bindingcatalog", "runtime": self.runtime}, deadline=self._deadline)
        return BindingCatalog(self._target, result)

    def provision(self, catalog, mode, target_id, *, scope=None, prior_scope=None):
        """Submit one explicit pairing change, retaining time for a catalog read.

        This is not a transfer receipt and never retries an uncertain write.
        Only the player can generate UUIDs or map the public target to grants.
        """
        if type(catalog) is not BindingCatalog or catalog._target != self._target:
            raise RemoteError("invalid_configuration")
        try:
            checked = protocol.validate_result(catalog._result.request, catalog.data,
                request_id=catalog._result.request_id, response_id=catalog._result.request_id,
                status="ok", max_metadata_bytes=self.max_metadata_bytes)
            if (checked.request != {"op": "bindingcatalog", "runtime": self.runtime}
                    or len([row for row in checked.data["targets"] if row["target_id"] == target_id]) != 1):
                raise RemoteError("invalid_configuration")
            params = {"op": "provision", "runtime": self.runtime, "mode": mode,
                      "target_id": target_id, "codecVersion": 1,
                      "expected_binding_revision": checked.data["binding_revision"]}
            if scope is not None:
                params["scope"] = copy.deepcopy(scope)
            if prior_scope is not None:
                params["prior_scope"] = copy.deepcopy(prior_scope)
            return self._rpc(params, deadline=self._deadline - 4)
        except protocol.ProtocolError:
            raise RemoteError("invalid_response") from None

    def confirm_binding(self, catalog, scope, target_id):
        """Confirm an existing exact pairing only; no bootstrap or rebinding."""
        if type(catalog) is not BindingCatalog or catalog._target != self._target:
            raise RemoteError("invalid_configuration")
        try:
            checked = protocol.validate_result(catalog._result.request, catalog.data,
                request_id=catalog._result.request_id, response_id=catalog._result.request_id, status="ok",
                max_metadata_bytes=self.max_metadata_bytes)
            if checked.request != {"op": "bindingcatalog", "runtime": self.runtime}:
                raise RemoteError("invalid_configuration")
            matches = [row for row in checked.data["bindings"] if row["scope"] == scope and row["target_id"] == target_id]
            if len(matches) != 1:
                raise RemoteError("invalid_configuration")
            return self._rpc({"op": "bind", "runtime": self.runtime, "scope": copy.deepcopy(scope),
                              "target_id": target_id, "codecVersion": matches[0]["codecVersion"],
                              "expected_binding_revision": checked.data["binding_revision"]}, deadline=self._deadline)
        except protocol.ProtocolError:
            raise RemoteError("invalid_response") from None

    def _snapshot(self, snapshot):
        if type(snapshot) is not Snapshot or snapshot._target != self._target:
            raise RemoteError("invalid_snapshot")
        try:
            response = protocol.validate_result(snapshot._result.request, snapshot.data,
                                                request_id=snapshot._result.request_id, response_id=snapshot._result.request_id,
                                                status="ok", scope_validator=self.scope_validator)
            if response.request["op"] != "snapshot" or response.request["runtime"] != self.runtime:
                raise RemoteError("invalid_snapshot")
            return response.data
        except (protocol.ProtocolError, ValueError):
            raise RemoteError("invalid_snapshot") from None

    def _preflight(self, snapshot, raw, *, commit_planned=True):
        baseline = self._snapshot(snapshot)
        try:
            document = transfer.validate_document_bytes(raw, max_bytes=self.limits.max_document_bytes)
            checked = self.scope_validator(copy.deepcopy(document))
            if type(document) is not dict or checked != document or document.get("scope") != baseline["scope"]:
                raise RemoteError("invalid_document")
            operation = str(self.uuid_factory())
            if protocol.UUID.fullmatch(operation) is None:
                raise RemoteError("invalid_configuration")
            def params(chunk, transaction=PLACEHOLDER_TRANSACTION):
                return {"op": "append", "runtime": self.runtime, "transaction_id": transaction,
                        "seq": chunk.seq, "offset": chunk.offset, "chunk_sha256": chunk.payload.sha256,
                        "data_base64": chunk.payload.data_base64}
            packed = transfer.pack_document(raw, lambda chunk: transfer.request_envelopes(ACTION, params(chunk)),
                                            chunk_bytes=self.limits.max_chunk_bytes, max_bytes=self.limits.max_document_bytes,
                                            rpc_bytes=self.limits.max_envelope_bytes, max_chunks=self.limits.max_chunks)
            begin = self._params({"op": "begin", "runtime": self.runtime, "operation_id": operation,
                                  "scope": baseline["scope"], "expected_revision": baseline["revision"],
                                  "binding_revision": baseline["binding_revision"], "document_sha256": packed.sha256,
                                  "size_bytes": packed.size_bytes, "chunk_count": len(packed.chunks), "persistence": "persistent"})
            for op in ("preview", "abort"):
                self._params({"op": op, "runtime": self.runtime, "transaction_id": PLACEHOLDER_TRANSACTION})
            if commit_planned:
                self._params({"op": "commit", "runtime": self.runtime, "transaction_id": PLACEHOLDER_TRANSACTION,
                              "operation_id": operation, "expected_revision": baseline["revision"], "plan_digest": "0" * 64})
                self._params({"op": "status", "runtime": self.runtime, "operation_id": operation})
            controls = 6 if commit_planned else 4  # Includes one player-minted prepare grant.
            commands = len(packed.chunks) + controls
            budget = transfer.estimate_budget(len(packed.chunks), control_commands=controls,
                                              controller_requests=commands * 3, device_requests=commands * 3,
                                              final_receipt_reserve=RECONCILE_RESERVE)
            transfer.require_budget(budget, deadline=self._deadline, now=self._now())
            if (budget.minimum_seconds > self.limits.absolute_ttl_seconds
                    or self.limits.idle_ttl_seconds < POST_INTERVAL + HTTP_INTERVAL
                    or budget.minimum_seconds * 1000 > self.max_budget_ms
                    or self.admission_ttl_ms <= (POST_INTERVAL + HTTP_INTERVAL) * 1000):
                raise RemoteError("deadline_insufficient")
            return begin, packed, params
        except transfer.TransferError as error:
            raise RemoteError("deadline_insufficient" if error.code == "deadline_insufficient" else "invalid_document") from None
        except RemoteError:
            raise
        except Exception:  # noqa: BLE001 - codec/generator failures may expose private data
            raise RemoteError("invalid_document") from None

    def _cutoff(self, commands):
        return self._deadline - RECONCILE_RESERVE - (commands - 1) * POST_INTERVAL

    def _save(self, record, deadline):
        """Late callback completion may save context but can never send commit."""
        remaining = deadline - self._now()
        if remaining <= 0:
            raise RemoteError("context_not_saved")
        completed = queue.Queue(maxsize=1)
        def persist():
            try:
                completed.put(self.save_context(copy.deepcopy(record)) is True)
            except Exception:  # noqa: BLE001 - private storage errors are not public metadata
                completed.put(False)
        threading.Thread(target=persist, name="ott-favorites-context", daemon=True).start()
        while True:
            remaining = deadline - self._now()
            if remaining <= 0:
                raise RemoteError("context_not_saved")
            try:
                saved = completed.get(timeout=min(5, remaining))
                break
            except queue.Empty:
                continue
        if not saved or self._now() >= deadline:
            raise RemoteError("context_not_saved")

    def _abort(self, transaction, operation):
        if transaction is None:
            return "unconfirmed"
        try:
            result = self._rpc({"op": "abort", "runtime": self.runtime, "transaction_id": transaction}, deadline=self._deadline)
            return "aborted" if result.data["operation_id"] == operation else "unconfirmed"
        except RemoteError:
            return "unconfirmed"

    def _outcome(self, result, operation):
        data = result.data
        if result.verified:
            return Outcome(operation, "verified", True, "verified_receipt", result.request_id, result=result)
        outcome = data.get("outcome", "unknown")
        if outcome not in ("not_applied", "partial", "unknown"):
            outcome = "unknown"
        return Outcome(operation, outcome, False, "observed_" + outcome, result.request_id, result=result)

    def reconcile(self, record):
        """Read status only; load record from the same target-bound private store.

        A player restart requires a fresh complete snapshot of the original
        scope and unchanged binding revision. Original commit evidence stays
        immutable and cannot authorize mutations under the new runtime.
        """
        saved, context = _restore(record)
        try:
            if saved["runtime"] != self.runtime:
                try:
                    snapshot = self.snapshot(saved["begin_request"]["scope"])
                    self._snapshot(snapshot)  # Same controller/device/current-runtime ownership.
                    context = protocol.status_context(context, snapshot._result)
                except (RemoteError, protocol.ProtocolError):
                    return self._observe_unverified(saved)
            result = self._rpc({"op": "status", "runtime": self.runtime, "operation_id": saved["operation_id"]},
                               deadline=self._deadline, context=context)
            return self._outcome(result, saved["operation_id"])
        except RemoteError as error:
            return Outcome(saved["operation_id"], "unknown", False, error.code, error.request_id)
        except protocol.ProtocolError:
            return Outcome(saved["operation_id"], "unknown", False, "invalid_context")

    def _stage(self, snapshot, document, *, commit_planned):
        """Share staging/validation; callers alone choose abort or durable commit."""
        if self._staging_started:
            raise RemoteError("already_started")
        begin_params, packed, append_params = self._preflight(snapshot, document, commit_planned=commit_planned)
        operation, transaction = None, None
        self._staging_started = True
        remaining_commit = int(commit_planned)
        try:
            prepare_started = self._now()
            budget_ms = min(self.max_budget_ms, int((self._deadline - prepare_started) * 1000))
            self._deadline = min(self._deadline, prepare_started + budget_ms / 1000)
            prepared = self._rpc({"op": "prepare", "runtime": self.runtime,
                **{key: begin_params[key] for key in ("scope", "expected_revision", "binding_revision",
                                                      "document_sha256", "size_bytes")},
                "budget_ms": budget_ms}, deadline=self._cutoff(len(packed.chunks) + 3 + remaining_commit))
            if prepared.data["admission_ttl_ms"] > self.admission_ttl_ms:
                raise RemoteError("invalid_response", request_id=prepared.request_id, phase="receipt")
            operation = prepared.data["operation_id"]
            begin_params["operation_id"] = operation
            # Budget and grant lifetimes never restart on a later HTTP request.
            begin_deadline = min(self._cutoff(len(packed.chunks) + 2 + remaining_commit),
                                 prepare_started + prepared.data["admission_ttl_ms"] / 1000)
            begin = self._rpc(begin_params, deadline=begin_deadline)
            transaction = begin.data["transaction_id"]
            if begin.data["next_seq"] != 0 or begin.data["received_bytes"] != 0:
                raise RemoteError("invalid_response", request_id=begin.request_id, phase="receipt")
            for index, packed_chunk in enumerate(packed.chunks):
                self._rpc(append_params(packed_chunk.chunk, transaction),
                          deadline=self._cutoff(len(packed.chunks) - index + 1 + remaining_commit))
            preview = self._rpc({"op": "preview", "runtime": self.runtime, "transaction_id": transaction},
                                deadline=self._cutoff(1 + remaining_commit))
            if (preview.data["base_revision"] != begin_params["expected_revision"]
                    or preview.data["binding_revision"] != begin_params["binding_revision"]):
                raise RemoteError("invalid_response", request_id=preview.request_id, phase="receipt")
            return begin, preview
        except RemoteError as error:
            return Outcome(operation, "not_applied", False, error.code, error.request_id, self._abort(transaction, operation))

    def preview(self, snapshot, document):
        """Stage and inspect one known scope, then abort; never save or commit.

        A valid blocked preview retains diagnostics with ready=False. Invalid
        or uncertain preview evidence has ready=None. Cleanup is independent:
        an unconfirmed abort never becomes a claimed completed cleanup.
        """
        staged = self._stage(snapshot, document, commit_planned=False)
        if type(staged) is Outcome:
            return PreviewOutcome(staged.operation_id, staged.reason, staged.request_id, staged.cleanup)
        begin, preview = staged
        operation = begin.request["operation_id"]
        cleanup = self._abort(begin.data["transaction_id"], operation)
        return PreviewOutcome(operation, "ready" if preview.data["ready"] else "blocked",
                              preview.request_id, cleanup, preview)

    def replace(self, snapshot, document):
        """Replace exactly this known scope; merging/planning belongs above."""
        staged = self._stage(snapshot, document, commit_planned=True)
        if type(staged) is Outcome:
            return staged
        begin, preview = staged
        begin_params = begin.request
        operation, transaction = begin_params["operation_id"], begin.data["transaction_id"]
        try:
            if preview.data["ready"] is not True:
                raise RemoteError("not_ready", request_id=preview.request_id, phase="receipt")
            context = protocol.retain_context(begin, preview)
            commit = {"op": "commit", "runtime": self.runtime, "transaction_id": transaction,
                      "operation_id": operation, "expected_revision": begin_params["expected_revision"],
                      "plan_digest": preview.data["plan_digest"]}
            record = recovery_record(begin, preview, commit)
            self._save(record, self._cutoff(1))
        except protocol.ProtocolError:
            return Outcome(operation, "not_applied", False, "invalid_response", cleanup=self._abort(transaction, operation))
        except RemoteError as error:
            return Outcome(operation, "not_applied", False, error.code, error.request_id, self._abort(transaction, operation))
        try:
            result = self._rpc(commit, deadline=self._cutoff(1), context=context)
            if result.data.get("outcome") == "unknown":
                return self.reconcile(record)
            return self._outcome(result, operation)
        except RemoteError:
            # The attempt may still execute: only status can establish its
            # outcome. Never abort, resend commit or fabricate not_applied.
            return self.reconcile(record)
