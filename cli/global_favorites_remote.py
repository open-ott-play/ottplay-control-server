"""Bounded global Favorites transport; no command/capability registration.

Uses the existing paced HTTP transport with a separate strict wire codec.
Every import first reads a complete immutable export, plans locally, and binds
the player's preview to that plan. Commit is sent at most once, only after
private recovery evidence is saved. A lost commit response leads to status,
never another write. These guarantees require the matching player facade.
"""

import copy
import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field

import favorites_operations as operations
import favorites_remote as transport
import favorites_transfer as transfer
import global_favorites_document as document
import global_favorites_protocol as protocol

ACTION = "favorites_global"
RESERVE = transport.RECONCILE_RESERVE
RemoteError = transport.RemoteError
Outcome = transport.Outcome


def validate_recovery_record(value):
    """Validate a private wrapper without granting write/replay authority."""
    try:
        if (type(value) is not dict or set(value) != {"version", "operation_id", "context"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError
        context = protocol.restore_context(value["context"])
        checked = protocol.export_context(context)
        if (checked["preview"] is None or not checked["preview"]["data"]["ready"]
                or checked["begin"]["request"]["operation_id"] != value["operation_id"]
                or checked != value["context"]):
            raise ValueError
        return copy.deepcopy(value)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise RemoteError("invalid_context") from None


def restore_context(value):
    return protocol.restore_context(validate_recovery_record(value)["context"])


class ContextStore(operations.ContextStore):
    """Use ONLY a separate global directory; old scoped files are not read."""

    _protocol = protocol
    _restore_context = staticmethod(restore_context)

    def __init__(self, directory, *, server, device):
        super().__init__(directory, server=server, device=device, validate_context=validate_recovery_record)


@dataclass(frozen=True)
class Snapshot:
    _target: tuple = field(repr=False)
    description: protocol.ValidatedResult = field(repr=False)
    raw_document: bytes = field(repr=False)

    @property
    def document(self):
        return document.load_document(self.raw_document)


@dataclass(frozen=True)
class Preview:
    operation_id: str
    ready: bool
    cleanup: str
    data: dict = field(repr=False)


class RemoteGlobalFavorites(transport._RequestTransport):
    _protocol = protocol
    _action = ACTION
    _save = transport.RemoteFavorites._save

    def __init__(self, client, device, *, runtime, save_context=None, timeout=45,
                 monotonic=time.monotonic, sleep=time.sleep, deadline=None, uuid_factory=uuid.uuid4):
        super().__init__(client, device, runtime=runtime, envelope_limit=16384,
                         response_limit=protocol.MAX_RESPONSE_BYTES, scope_validator=None,
                         timeout=timeout, monotonic=monotonic, sleep=sleep,
                         max_metadata_bytes=protocol.MAX_METADATA_BYTES, deadline=deadline,
                         allowed_ops=frozenset(("describe", "export", "begin", "append", "preview",
                                                "commit", "status", "abort")))
        self.save_context = save_context
        self.uuid_factory = uuid_factory
        self._description = None
        self._started = False

    def describe(self, *, deadline=None):
        cutoff = self._deadline if deadline is None else min(self._deadline, deadline)
        result = self._rpc({"op": "describe", "runtime": self.runtime}, deadline=cutoff)
        self._description = result
        limits = result.data["limits"]
        self._envelope_limit = limits["max_envelope_bytes"]
        self.max_metadata_bytes = limits["max_metadata_bytes"]
        return result

    def export(self):
        description = self.describe()
        descriptor = description.data["export"]
        if descriptor is None:
            raise RemoteError("not_ready")
        limits = description.data["limits"]
        pages, offset = [], 0
        # The validated size bounds both iterations and accumulation even if a
        # malicious peer sends a one-byte page instead of the requested size.
        while offset < descriptor["size_bytes"]:
            result = self._rpc({"op": "export", "runtime": self.runtime,
                                "snapshot_id": descriptor["snapshot_id"], "offset": offset,
                                "max_bytes": limits["max_export_bytes"]}, deadline=self._deadline)
            data = result.data
            if (data["revision"] != description.data["revision"]
                    or data["document_sha256"] != descriptor["document_sha256"]
                    or data["size_bytes"] != descriptor["size_bytes"]):
                raise RemoteError("invalid_response")
            pages.append(result)
            offset = data["next_offset"]
        try:
            exported = protocol.assemble_export(description, pages)
            raw = document.canonical_bytes(exported.document)
            if (len(raw) != descriptor["size_bytes"]
                    or hashlib.sha256(raw).hexdigest() != descriptor["document_sha256"]):
                raise ValueError
            return Snapshot(self._target, description, raw)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise RemoteError("invalid_response") from None

    def _preflight(self, snapshot, incoming, mode, commit, *, refresh=True):
        if self._started:
            raise RemoteError("already_started")
        if (type(snapshot) is not Snapshot or snapshot._target != self._target
                or snapshot.description is not self._description):
            raise RemoteError("invalid_snapshot")
        info = snapshot.description.data
        if not info["write_ready"] or (commit and not callable(self.save_context)):
            raise RemoteError("not_ready")
        try:
            incoming = document.validate_document(incoming)
            raw = document.canonical_bytes(incoming)
            limits = info["limits"]
            plan = document.plan_documents(snapshot.document, [incoming], mode)
            if (not plan["can_commit"] or len(raw) > limits["max_document_bytes"]
                    or len(document.canonical_bytes(plan["document"])) > limits["max_document_bytes"]
                    or len(incoming["entries"]) > limits["max_entries"]
                    or len(plan["document"]["entries"]) > limits["max_entries"]
                    or not document.supports(incoming, info["locator_kinds"])
                    or not document.supports(plan["document"], info["locator_kinds"])):
                raise RemoteError("invalid_document")
            operation = info["admission"]["operation_id"]
            def append(chunk, transaction=transport.PLACEHOLDER_TRANSACTION):
                return {"op": "append", "runtime": self.runtime, "transaction_id": transaction,
                        "seq": chunk.seq, "offset": chunk.offset, "chunk_sha256": chunk.payload.sha256,
                        "data_base64": chunk.payload.data_base64}
            packed = transfer.pack_document(raw, lambda c: transfer.request_envelopes(ACTION, append(c)),
                chunk_bytes=limits["max_append_bytes"], max_bytes=limits["max_document_bytes"],
                max_chunks=limits["max_chunks"], rpc_bytes=limits["max_envelope_bytes"])
            pages = max(1, math.ceil(len(plan["document"]["entries"]) / limits["max_preview_rows"]))
            # Include every remaining preview page, begin, terminal operation,
            # and reconciliation. Three HTTP calls per RPC cover an initial
            # pending poll; delays beyond this floor still use one deadline.
            controls = 1 + int(refresh) + pages + (2 if commit else 1)
            budget = transfer.estimate_budget(len(packed.chunks), control_commands=controls,
                controller_requests=(len(packed.chunks) + controls) * 3,
                device_requests=(len(packed.chunks) + controls) * 3,
                final_receipt_reserve=RESERVE, command_interval=2.0)
            transfer.require_budget(budget, deadline=self._deadline, now=self._now())
            if (budget.minimum_seconds > limits["absolute_ttl_seconds"]
                    or limits["idle_ttl_seconds"] < 2):
                raise RemoteError("deadline_insufficient")
            remaining_ms = int(min(self._deadline - self._now(), limits["absolute_ttl_seconds"]) * 1000)
            begin = self._params({"op": "begin", "runtime": self.runtime, "operation_id": operation,
                "mode": mode, "expected_revision": info["revision"],
                "expected_context_revision": info["context_revision"], "document_sha256": packed.sha256,
                "size_bytes": len(raw), "chunk_count": len(packed.chunks), "budget_ms": remaining_ms,
                "required_kinds": document.required_locator_kinds(incoming)})
            protocol.validate_begin_negotiation(begin, snapshot.description)
            return begin, packed, append, plan
        except transfer.TransferError as error:
            raise RemoteError("deadline_insufficient" if error.code in ("deadline_insufficient", "invalid_budget")
                              else "invalid_document") from None
        except RemoteError:
            raise
        except (document.DocumentError, protocol.ProtocolError, TypeError, ValueError, OverflowError):
            raise RemoteError("invalid_document") from None

    def _abort(self, operation, transaction):
        if transaction is None:
            return "unconfirmed"
        try:
            self._rpc({"op": "abort", "runtime": self.runtime, "operation_id": operation,
                       "transaction_id": transaction}, deadline=self._deadline)
            return "aborted"
        except RemoteError:
            return "unconfirmed"

    def _stage(self, snapshot, incoming, mode, commit):
        self._preflight(snapshot, incoming, mode, commit)
        operation, transaction = None, None
        cutoff = self._deadline - RESERVE - (transport.POST_INTERVAL if commit else 0)
        try:
            refreshed_at = self._now()
            fresh = self.describe(deadline=cutoff)
            previous, current = snapshot.description.data, fresh.data
            if (any(current[key] != previous[key] for key in ("revision", "context_revision"))
                    or current["export"] is None
                    or any(current["export"][key] != previous["export"][key]
                           for key in ("document_sha256", "size_bytes", "entries"))):
                raise RemoteError("invalid_snapshot")
            refreshed = Snapshot(self._target, fresh, snapshot.raw_document)
            begin_request, packed, append, plan = self._preflight(refreshed, incoming, mode, commit, refresh=False)
            self._started = True
            operation = begin_request["operation_id"]
            begin = self._rpc(begin_request, deadline=min(cutoff, refreshed_at + current["admission"]["ttl_ms"] / 1000))
            transaction = begin.data["transaction_id"]
            for chunk in packed.chunks:
                result = self._rpc(append(chunk.chunk, transaction), deadline=cutoff)
                if result.data["operation_id"] != operation or result.data["duplicate"]:
                    raise RemoteError("invalid_response")
            offset, previews, context = 0, [], None
            summary = plan["summary"]
            counts = {key: summary[key] for key in ("before", "after", "added", "removed", "retained", "moved")}
            counts.update(updated=summary["title_changes"], duplicates_skipped=summary["skipped_duplicates"])
            expected_hash = hashlib.sha256(document.canonical_bytes(plan["document"])).hexdigest()
            while True:
                preview = self._rpc({"op": "preview", "runtime": self.runtime, "transaction_id": transaction,
                    "page_offset": offset, "max_entries": current["limits"]["max_preview_rows"]},
                    deadline=cutoff, context=context)
                if not previews:
                    context = protocol.retain_context(begin, preview)
                data = preview.data
                if data["counts"] != counts or data["result_document_sha256"] != expected_hash:
                    raise RemoteError("invalid_response")
                previews.append(preview)
                if data["next_offset"] is None:
                    break
                offset = data["next_offset"]
            rows = protocol.validate_preview_pages(context, previews)
            protocol.validate_metadata_conflicts(rows, incoming, plan["document"])
            return begin, previews[0], context
        except (RemoteError, protocol.ProtocolError) as error:
            reason = error.code if isinstance(error, RemoteError) else "invalid_response"
            return Outcome(operation, "not_applied", False, reason,
                           getattr(error, "request_id", None), self._abort(operation, transaction))

    def preview(self, snapshot, incoming, mode="merge"):
        staged = self._stage(snapshot, incoming, mode, False)
        if isinstance(staged, Outcome):
            return staged
        begin, preview, _ = staged
        operation = begin.request["operation_id"]
        return Preview(operation, preview.data["ready"], self._abort(operation, begin.data["transaction_id"]),
                       preview.data)

    def apply(self, snapshot, incoming, mode="merge"):
        staged = self._stage(snapshot, incoming, mode, True)
        if isinstance(staged, Outcome):
            return staged
        begin, preview, context = staged
        operation, transaction = begin.request["operation_id"], begin.data["transaction_id"]
        try:
            if not preview.data["ready"]:
                raise RemoteError("not_ready")
            record = validate_recovery_record({"version": 1, "operation_id": operation,
                                              "context": protocol.export_context(context)})
            self._save(record, self._deadline - RESERVE)
        except RemoteError as error:
            return Outcome(operation, "not_applied", False, error.code, cleanup=self._abort(operation, transaction))
        try:
            result = self._rpc({"op": "commit", "runtime": self.runtime, "transaction_id": transaction,
                "operation_id": operation, "plan_digest": preview.data["plan_digest"]},
                deadline=self._deadline - RESERVE, context=context)
            if result.verified:
                return Outcome(operation, "verified", True, "verified_receipt", result.request_id, result=result)
        except RemoteError:
            pass
        return self.reconcile(record)

    def reconcile(self, record):
        record = validate_recovery_record(record)
        operation = record["operation_id"]
        try:
            result = self._rpc({"op": "status", "runtime": self.runtime, "operation_id": operation},
                               deadline=self._deadline, context=restore_context(record))
            return Outcome(operation, "verified" if result.verified else result.data["outcome"], result.verified,
                           "verified_receipt" if result.verified else "observed_" + result.data["outcome"],
                           result.request_id, result=result)
        except (RemoteError, protocol.ProtocolError) as error:
            return Outcome(operation, "unknown", False,
                           error.code if isinstance(error, RemoteError) else "invalid_context",
                           getattr(error, "request_id", None))


class StatusReader(RemoteGlobalFavorites):
    """Read retained outcomes with fresh identity, even without a write marker.

    The host must load this target's private context and validate fresh player
    metadata. This reader grants no discovery, staging, abort or commit action.
    """

    def __init__(self, client, device, *, runtime, timeout=45, monotonic=time.monotonic,
                 sleep=time.sleep, deadline=None):
        super().__init__(client, device, runtime=runtime, timeout=timeout,
                         monotonic=monotonic, sleep=sleep, deadline=deadline)
        self._allowed_ops = frozenset(("status",))
