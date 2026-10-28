"""Pure protocol validation for the favorites-transfer host orchestrator.

No capability registration, RPC, clock, persistence, or retry lives here.
Current verified Plex negotiation is syntax-only; other codecs fail closed.
Parsing an outcome named ``verified`` is not receipt verification: only a
matching retained begin/preview context sets ValidatedResult.verified. Even
that confirms the correlated player's readback claim, not fsync/atomicity or
provider/grant ownership. The host orchestrator enforces its admission gates.
"""

import base64
import datetime
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field

import favorites_document as document_codec
import favorites_transfer as transfer

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 128 * 1024
MAX_COUNT = 1000000
TRANSFER_OPS = frozenset(("snapshot", "begin", "append", "preview", "commit", "status", "abort"))
BINDING_OPS = frozenset(("bindingcatalog", "bind", "provision"))
ADMISSION_OPS = frozenset(("prepare",))
PROVISION_MODES = frozenset(("create", "import", "pair", "replace-current"))
OPS = TRANSFER_OPS | BINDING_OPS | ADMISSION_OPS
CAPABILITY_LIMITS = {
    "max_document_bytes": 1048576, "max_chunk_bytes": 8192, "max_chunks": 256,
    "max_envelope_bytes": 16384, "max_snapshot_response_bytes": 2097152,
    "max_active_stages": 1, "idle_ttl_seconds": 120, "absolute_ttl_seconds": 600,
    "max_plan_bytes": 65536, "max_metadata_bytes": 131072, "max_json_depth": 20,
    "max_json_nodes": 32768, "max_channel_lists": 256, "max_entries_per_list": 10000,
    "max_media_favorites": 1000, "max_receipt_entries": 32, "max_receipt_ledger_bytes": 65536,
    "max_retained_admissions_per_adapter_instance": 16, "max_unstarted_drafts": 1,
}
ERRORS = frozenset((
    "invalid_request", "envelope_too_large", "runtime_changed", "busy", "expired",
    "operation_conflict", "transaction_not_found", "out_of_order", "chunk_conflict",
    "incomplete", "hash_mismatch", "revision_conflict", "binding_changed", "policy_denied",
    "unsupported_persistence", "codec_error", "not_ready", "reconciliation_required",
    "storage_unavailable", "limits_exceeded",
))
OUTCOMES = frozenset(("verified", "not_applied", "partial", "unknown"))
COMMON = frozenset(("version", "runtime", "op"))
RECEIPT = frozenset(("operation_id", "scope", "new_revision", "document_sha256",
                     "plan_digest", "counts", "outcome", "persistence"))
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
RUNTIME = re.compile(r"[a-z0-9-]{1,64}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
PROVISION_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
PROVISION_REVISION = re.compile(r"(?:0|[1-9][0-9]{0,15})\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})\Z")


class ProtocolError(ValueError):
    """Fixed, non-secret error; never render rejected input or upstream text."""

    def __init__(self, code="invalid_protocol"):
        self.code = code
        super().__init__({
            "invalid_protocol": "Invalid favorites transfer protocol metadata.",
            "envelope_too_large": "Favorites transfer envelope exceeds its byte limit.",
            "unresolved_variant": "This favorites transfer response variant is not supported yet.",
            "context_mismatch": "Favorites receipt does not match the retained operation context.",
        }[code])


@dataclass(frozen=True)
class ValidatedResult:
    """Copies on access prevent callers from modifying retained wire evidence."""

    _request: bytes = field(repr=False)
    _data: bytes = field(repr=False)
    status: str
    request_id: str
    verified: bool = False
    raw_document: bytes | None = field(default=None, repr=False)

    @property
    def request(self):
        return json.loads(self._request)

    @property
    def data(self):
        return json.loads(self._data)


@dataclass(frozen=True)
class ReceiptContext:
    """Retained, validated begin and ready-preview evidence; not a wire object."""

    _begin: bytes = field(repr=False)
    _begin_result: bytes = field(repr=False)
    _preview: bytes = field(repr=False)
    _status_runtime: str | None = field(default=None, repr=False)


def _require(condition, code="invalid_protocol"):
    if not condition:
        raise ProtocolError(code)


def _shape(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _integer(value, low=0, high=transfer.MAX_DOCUMENT_BYTES):
    _require(type(value) is int and low <= value <= high)


def _pattern(value, pattern):
    _require(type(value) is str and pattern.fullmatch(value) is not None)


def _text(value):
    _require(type(value) is str and bool(value) and len(value) <= 256
             and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    try:
        _require(len(value.encode("utf-8")) <= 256)
    except UnicodeError:
        raise ProtocolError() from None


def _scope(value):
    _shape(value, ("profileBindingId", "sourceBindingId", "provider"))
    _pattern(value["profileBindingId"], UUID)
    _pattern(value["sourceBindingId"], UUID)
    _require(type(value["provider"]) is str and value["provider"] in document_codec.PROVIDERS)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _object(value, limit):
    """Bound raw bytes and reject duplicate keys/nonfinite/unpaired Unicode."""
    def pairs(rows):
        out = {}
        for key, item in rows:
            _require(key not in out)
            out[key] = item
        return out

    def finite(raw):
        result = float(raw)
        _require(math.isfinite(result))
        return result

    def constant(_):
        raise ProtocolError()

    try:
        if type(value) is bytes:
            raw = value
        else:
            _require(type(value) is dict)
            # Bound traversal before serializing already-parsed, untrusted data.
            pending, nodes = [(value, 0)], 0
            while pending:
                item, depth = pending.pop()
                nodes += 1
                _require(depth <= 24 and nodes <= limit)
                if type(item) is dict:
                    _require(len(item) <= 128 and all(type(k) is str and len(k) <= limit for k in item))
                    pending.extend((x, depth + 1) for pair in item.items() for x in pair)
                elif type(item) is list:
                    _require(len(item) <= limit)
                    pending.extend((x, depth + 1) for x in item)
                else:
                    _require(item is None or type(item) in (str, int, float, bool))
                    _require(type(item) is not str or len(item) <= limit)
            raw = _encoded(value)
        _require(0 < len(raw) <= limit, "envelope_too_large")
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=constant, parse_float=finite)
        _require(type(result) is dict)
        _encoded(result)  # Validate escaped surrogate pairs without changing original bytes.
        return result, raw
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError() from None


def validate_capability(value):
    """Validate/copy the inner favorites_transfer advertisement, not ownership.

    The frozen first adapter supports persistent current-verified Plex only.
    Independent byte/item ceilings intersect; smaller advertisements are not
    promises that every individually bounded collection can be admitted.
    """
    value, _ = _object(value, MAX_METADATA_BYTES)
    _shape(value, ("version", "persistence", "codecs", "limits", "admission",
                   "receipt_retention_seconds", "receipt_retention_origin", "receipt_retention_policy"))
    _integer(value["version"], 1, 1)
    _require(value["persistence"] == ["persistent"]
             and value["receipt_retention_origin"] == "logical-reservation-plan"
             and value["receipt_retention_policy"] == "up-to-count-and-byte-bounded")
    _integer(value["receipt_retention_seconds"], 1, 86400)
    codecs = value["codecs"]
    _require(type(codecs) is list and len(codecs) == 1)
    codec = codecs[0]
    _shape(codec, ("provider", "codecVersion", "locatorKinds", "targetScope", "coverage"))
    _integer(codec["codecVersion"], 1, 1)
    _require(codec["provider"] == "plex" and codec["locatorKinds"] == ["plex-media"]
             and codec["targetScope"] == "current-verified-source"
             and codec["coverage"] == ["channelLists", "mediaFavorites"])
    _shape(value["limits"], CAPABILITY_LIMITS)
    for name, ceiling in CAPABILITY_LIMITS.items():
        _integer(value["limits"][name], 1, ceiling)
    _require(value["limits"]["idle_ttl_seconds"] <= value["limits"]["absolute_ttl_seconds"])
    admission = value["admission"]
    _shape(admission, ("op", "max_unconsumed_grants", "admission_ttl_ms", "max_budget_ms", "operation_id"))
    _require(admission["op"] == "prepare" and admission["operation_id"] == "player-generated-uuidv4")
    _integer(admission["max_unconsumed_grants"], 1, 1)
    _integer(admission["admission_ttl_ms"], 1, 10000)
    _integer(admission["max_budget_ms"], 1, 300000)
    return value


def _binding_text(value, *, target=False):
    _text(value)
    _require(not any(unicodedata.category(char).startswith("C") or target and char.isspace() for char in value))


def _plex_scope(value):
    _scope(value)
    _require(value["provider"] == "plex" and value["profileBindingId"] != value["sourceBindingId"])


def _provision_revision(value):
    _pattern(value, PROVISION_REVISION)
    _require(int(value) <= 9007199254740991)


def _binding_catalog(data):
    _shape(data, COMMON | {"binding_revision", "targets", "bindings"})
    _text(data["binding_revision"])
    targets = data["targets"]
    _require(type(targets) is list and len(targets) == 1)
    target = targets[0]
    _shape(target, ("target_id", "display", "provider", "codecVersion", "serverId"))
    _binding_text(target["target_id"], target=True)
    _binding_text(target["display"])
    _require(target["provider"] == "plex")
    _integer(target["codecVersion"], 1, 1)
    _pattern(target["serverId"], re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z"))
    bindings, seen = data["bindings"], set()
    profiles, sources = set(), set()
    _require(type(bindings) is list and len(bindings) <= 256)
    for binding in bindings:
        _shape(binding, ("scope", "target_id", "codecVersion"))
        _plex_scope(binding["scope"])
        _binding_text(binding["target_id"], target=True)
        _integer(binding["codecVersion"], 1, 1)
        _require(binding["target_id"] == target["target_id"]
                 and binding["scope"]["provider"] == target["provider"]
                 and binding["codecVersion"] == target["codecVersion"])
        identity = tuple(binding["scope"][key] for key in ("provider", "profileBindingId", "sourceBindingId"))
        _require(identity not in seen)
        seen.add(identity)
        profile, source = binding["scope"]["profileBindingId"], binding["scope"]["sourceBindingId"]
        _require(source not in sources)
        profiles.add(profile)
        sources.add(source)
    # Registry row IDs are globally unique. One source has one parent, and a
    # source row cannot simultaneously be the parent profile of another row.
    _require(profiles.isdisjoint(sources))


def _chunk(params):
    encoded = params["data_base64"]
    _require(type(encoded) is str and 0 < len(encoded) <= 4 * ((transfer.MAX_CHUNK_BYTES + 2) // 3))
    try:
        raw = base64.b64decode(encoded, validate=True)
        return transfer.decode_bytes(encoded, size_bytes=len(raw), sha256=params["chunk_sha256"],
                                     max_bytes=transfer.MAX_CHUNK_BYTES)
    except (ValueError, UnicodeError, transfer.TransferError):
        raise ProtocolError() from None


def validate_request(params, *, idempotency_key=None):
    """Validate params and both real-client/conservative queued envelope sizes.

    UUID syntax cannot prove entropy. The stage owner must allocate transaction
    bearer IDs with a cryptographically secure UUID-v4 generator.
    """
    params, _ = _object(params, transfer.MAX_RPC_BYTES)
    op = params.get("op")
    _require(type(op) is str and op in OPS)
    _pattern(params.get("runtime"), RUNTIME)
    fields = {"op", "runtime"}
    if op == "bindingcatalog":
        pass
    elif op == "bind":
        fields |= {"scope", "target_id", "expected_binding_revision", "codecVersion"}
    elif op == "provision":
        mode = params.get("mode")
        _require(type(mode) is str and mode in PROVISION_MODES)
        fields |= {"mode", "target_id", "expected_binding_revision", "codecVersion"}
        if mode != "create":
            fields.add("scope")
        if mode == "replace-current":
            fields.add("prior_scope")
    elif op == "snapshot":
        fields |= {"scope"}
    elif op == "prepare":
        fields |= {"scope", "expected_revision", "binding_revision", "document_sha256", "size_bytes", "budget_ms"}
    elif op == "begin":
        fields |= {"operation_id", "scope", "expected_revision", "binding_revision",
                   "document_sha256", "size_bytes", "chunk_count", "persistence"}
    elif op == "append":
        fields |= {"transaction_id", "seq", "offset", "chunk_sha256", "data_base64"}
    elif op in ("preview", "abort"):
        fields |= {"transaction_id"}
    elif op == "commit":
        fields |= {"transaction_id", "operation_id", "expected_revision", "plan_digest"}
    else:
        fields |= {"operation_id"}
    _shape(params, fields)
    for key in ("operation_id", "transaction_id"):
        if key in params:
            _pattern(params[key], UUID)
    if "scope" in params:
        _scope(params["scope"])
    for key in ("expected_revision", "binding_revision", "expected_binding_revision"):
        if key in params:
            _text(params[key])
    for key in ("document_sha256", "chunk_sha256", "plan_digest"):
        if key in params:
            _pattern(params[key], DIGEST)
    if op in ("bind", "provision"):
        if "scope" in params:
            _plex_scope(params["scope"])
        if "prior_scope" in params:
            _plex_scope(params["prior_scope"])
            _require(len({params[scope][key] for scope in ("scope", "prior_scope")
                          for key in ("profileBindingId", "sourceBindingId")}) == 4)
        if op == "provision":
            _pattern(params["target_id"], PROVISION_TARGET)
            _provision_revision(params["expected_binding_revision"])
        else:
            _binding_text(params["target_id"], target=True)
        _integer(params["codecVersion"], 1, 1)
    elif op == "prepare":
        _plex_scope(params["scope"])
        _integer(params["size_bytes"], 1)
        _integer(params["budget_ms"], 1, 300000)
    elif op == "begin":
        _require(params["persistence"] == "persistent")
        _integer(params["size_bytes"], 1)
        _integer(params["chunk_count"], 1, transfer.MAX_CHUNKS)
        _require(params["chunk_count"] <= params["size_bytes"]
                 <= params["chunk_count"] * transfer.MAX_CHUNK_BYTES)
    elif op == "append":
        _integer(params["seq"], 0, transfer.MAX_CHUNKS - 1)
        _integer(params["offset"])
        raw = _chunk(params)
        _require(params["offset"] + len(raw) <= transfer.MAX_DOCUMENT_BYTES)
    try:
        envelopes = transfer.request_envelopes("favorites_transfer", params, idempotency_key=idempotency_key)
        _require(max(len(envelopes.client), len(envelopes.queued_upper_bound)) <= transfer.MAX_RPC_BYTES,
                 "envelope_too_large")
    except transfer.TransferError:
        raise ProtocolError() from None
    return params


def _counts(value):
    _shape(value, ("channelLists", "channelMemberships", "mediaFavorites", "unresolved"))
    for count in value.values():
        _integer(count, high=MAX_COUNT)
    _require(value["unresolved"] <= value["channelMemberships"] + value["mediaFavorites"])


def _diagnostics(rows, counts):
    _require(type(rows) is list and len(rows) <= min(128, counts["unresolved"]))
    for row in rows:
        _require(type(row) is dict and row.get("collection") in ("channelLists", "mediaFavorites"))
        _shape(row, {"collection", "entry_index", "reason"}
               | ({"list_index"} if row["collection"] == "channelLists" else set()))
        _integer(row["entry_index"], high=MAX_COUNT)
        if "list_index" in row:
            _integer(row["list_index"], high=MAX_COUNT)
        _require(row["reason"] in ("missing", "ambiguous", "legacy-unportable", "codec-unavailable"))


def _stores(value, *, preview=False, outcome=None):
    _require(type(value) is list and (1 if preview else 0) <= len(value) <= 64)
    seen, outcomes = set(), []
    for row in value:
        _shape(row, ("id", "expected_revision") if preview else ("id", "outcome"))
        _text(row["id"])
        _require(row["id"] not in seen)
        seen.add(row["id"])
        if preview:
            _text(row["expected_revision"])
        else:
            _require(type(row["outcome"]) is str and row["outcome"] in OUTCOMES)
            outcomes.append(row["outcome"])
    if outcome == "verified":
        _require(bool(outcomes) and all(x == "verified" for x in outcomes))
    elif outcome == "not_applied":
        _require(all(x == "not_applied" for x in outcomes))
    elif outcome == "partial":
        _require("unknown" not in outcomes and ("partial" in outcomes
                 or "verified" in outcomes and "not_applied" in outcomes))
    return seen


def _time(value):
    _pattern(value, TIMESTAMP)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Go uses RFC3339Nano; Python datetime alone truncates the final three
        # fractional digits and could accept idle > absolute within 1 us.
        fraction = re.search(r"\.([0-9]{1,9})", value)
        nanoseconds = int(fraction[1].ljust(9, "0")) if fraction else 0
        return parsed.replace(microsecond=0), nanoseconds
    except ValueError:
        raise ProtocolError() from None


def _progress(data, max_seq=transfer.MAX_CHUNKS, max_bytes=transfer.MAX_DOCUMENT_BYTES, *, ready=False):
    _integer(data["next_seq"], 1 if ready else 0, max_seq)
    _integer(data["received_bytes"], 1 if ready else 0, max_bytes)
    _require(data["next_seq"] <= data["received_bytes"] <= data["next_seq"] * transfer.MAX_CHUNK_BYTES)
    _require(_time(data["idle_expires_at"]) <= _time(data["absolute_expires_at"]))


def _receipt(value):
    _shape(value, RECEIPT)
    _pattern(value["operation_id"], UUID)
    _scope(value["scope"])
    _text(value["new_revision"])
    _pattern(value["document_sha256"], DIGEST)
    _pattern(value["plan_digest"], DIGEST)
    _counts(value["counts"])
    _require(value["outcome"] == "verified" and value["persistence"] == "persistent")


def _context_parts(context):
    _require(type(context) is ReceiptContext, "context_mismatch")
    if context._status_runtime is not None:
        _pattern(context._status_runtime, RUNTIME)
    begin = validate_request(context._begin)
    begin_result, _ = _object(context._begin_result, MAX_RESPONSE_BYTES)
    preview, _ = _object(context._preview, MAX_RESPONSE_BYTES)
    _require(begin["op"] == "begin", "context_mismatch")
    _validate_data(begin, begin_result, "ok", None, None)
    params = {"op": "preview", "runtime": begin["runtime"], "transaction_id": begin_result["transaction_id"]}
    _validate_data(params, preview, "ok", None, None)
    _require(preview["ready"] is True and preview["base_revision"] == begin["expected_revision"]
             and preview["binding_revision"] == begin["binding_revision"], "context_mismatch")
    return begin, begin_result, preview


def retain_context(begin, preview):
    """Retain correlated successful begin + ready-preview responses for receipts."""
    _require(type(begin) is ValidatedResult and type(preview) is ValidatedResult
             and begin.status == "ok" and preview.status == "ok", "context_mismatch")
    _require(begin.request["op"] == "begin" and preview.request["op"] == "preview"
             and preview.request["runtime"] == begin.request["runtime"]
             and preview.request["transaction_id"] == begin.data.get("transaction_id"), "context_mismatch")
    context = ReceiptContext(begin._request, begin._data, preview._data)
    _context_parts(context)
    return context


def status_context(context, current_snapshot):
    """Derive status-only authority after a proven same-binding player restart.

    The caller obtains this snapshot on the same authenticated target as the
    retained context's private store. Original begin/preview bytes are retained;
    the derived context can never validate a commit response.
    """
    begin, _, _ = _context_parts(context)
    _require(type(current_snapshot) is ValidatedResult and current_snapshot.status == "ok"
             and current_snapshot.request["op"] == "snapshot"
             and current_snapshot.request["scope"] == begin["scope"]
             and current_snapshot.data["scope"] == begin["scope"]
             and current_snapshot.data["binding_revision"] == begin["binding_revision"]
             and current_snapshot.raw_document is not None, "context_mismatch")
    runtime = current_snapshot.request["runtime"]
    _pattern(runtime, RUNTIME)
    return ReceiptContext(context._begin, context._begin_result, context._preview, runtime)


def _bind_context(params, data, receipt, context):
    begin, accepted, preview = _context_parts(context)
    if context._status_runtime is not None:
        _require(params["op"] == "status", "context_mismatch")
        if context._status_runtime != begin["runtime"]:
            # RAM stage identity cannot establish anything after a restart.
            # Only reconciled persisted commit outcomes have cross-runtime
            # authority; stale aborted/expired must not become not_applied.
            _require(data.get("state") == "committed", "context_mismatch")
    expected = ((params["runtime"], context._status_runtime or begin["runtime"]),
                (params.get("operation_id"), begin["operation_id"]))
    for actual, wanted in expected:
        _require(actual == wanted, "context_mismatch")
    if "transaction_id" in params:
        _require(params["transaction_id"] == accepted["transaction_id"], "context_mismatch")
    if "transaction_id" in data:
        _require(data["transaction_id"] == accepted["transaction_id"], "context_mismatch")
    if params["op"] == "commit":
        _require(params["expected_revision"] == begin["expected_revision"]
                 and params["plan_digest"] == preview["plan_digest"], "context_mismatch")
    if "scope" in data:
        _require(data["scope"] == begin["scope"], "context_mismatch")
    if "per_store" in data:
        _require({x["id"] for x in data["per_store"]} <= {x["id"] for x in preview["stores"]},
                 "context_mismatch")
    if "next_seq" in data:
        _require(data["next_seq"] <= begin["chunk_count"] and data["received_bytes"] <= begin["size_bytes"],
                 "context_mismatch")
    if data.get("state") == "ready":
        _require(data["next_seq"] == begin["chunk_count"] and data["received_bytes"] == begin["size_bytes"]
                 and data["plan_digest"] == preview["plan_digest"]
                 and data["base_revision"] == begin["expected_revision"]
                 and data["binding_revision"] == begin["binding_revision"], "context_mismatch")
    if receipt is not None:
        _require(receipt["operation_id"] == begin["operation_id"]
                 and receipt["scope"] == begin["scope"]
                 and receipt["document_sha256"] == begin["document_sha256"]
                 and receipt["plan_digest"] == preview["plan_digest"]
                 and receipt["counts"] == preview["counts"]
                 and {x["id"] for x in data["per_store"]} == {x["id"] for x in preview["stores"]},
                 "context_mismatch")


def _validate_data(params, data, status, context, scope_validator):
    _require(type(data.get("version")) is int and data["version"] == 1
             and data.get("runtime") == params["runtime"] and data.get("op") == params["op"])
    if "error" in data:
        _shape(data, COMMON | {"error"})
        _require(status in ("rejected", "unsupported") and type(data["error"]) is str
                 and (data["error"] in ERRORS or params["op"] == "provision" and data["error"] == "outcome_unknown"))
        return False, None
    _require(status == "ok")
    op, raw, receipt = params["op"], None, None
    for key in ("transaction_id", "operation_id"):
        if key in params:
            _require(data.get(key) == params[key])
    if op == "bindingcatalog":
        _binding_catalog(data)
    elif op == "prepare":
        _shape(data, set(params) | {"version", "operation_id", "admission_ttl_ms"})
        _require(all(type(data[key]) is type(value) and data[key] == value for key, value in params.items()))
        _pattern(data["operation_id"], UUID)
        _integer(data["admission_ttl_ms"], 1, min(10000, params["budget_ms"]))
        # A runtime-owned grant is neither persistent evidence nor a commit
        # receipt. Its clock/deadline and single consumption live in the host.
    elif op == "bind":
        _shape(data, COMMON | {"binding_revision", "scope", "target_id", "paired"})
        _text(data["binding_revision"])
        _plex_scope(data["scope"])
        _binding_text(data["target_id"], target=True)
        # This packet confirms an existing pairing only, without bootstrap or
        # rebind. A changed revision cannot confirm the requested operation.
        _require(data["paired"] is True and data["scope"] == params["scope"]
                 and data["target_id"] == params["target_id"]
                 and data["binding_revision"] == params["expected_binding_revision"])
    elif op == "provision":
        _shape(data, COMMON | {"mode", "paired", "changed", "binding_revision", "target_id", "scope",
                               "persistence", "outcome"})
        _provision_revision(data["binding_revision"])
        _pattern(data["target_id"], PROVISION_TARGET)
        _plex_scope(data["scope"])
        _require(data["mode"] == params["mode"] and data["target_id"] == params["target_id"]
                 and data["paired"] is True and type(data["changed"]) is bool
                 and data["persistence"] == "persistent" and data["outcome"] == "verified"
                 and data["changed"] == (data["binding_revision"] != params["expected_binding_revision"]))
        if params["mode"] != "create":
            _require(data["scope"] == params["scope"])
        # This is only a correlated registry acknowledgement. In create mode
        # the player chooses UUIDs; syntax cannot prove their entropy. Never
        # turn binding outcome="verified" into a transfer commit receipt.
    elif op == "snapshot":
        _shape(data, COMMON | {"scope", "revision", "binding_revision", "document_sha256", "size_bytes", "document_base64"})
        _require(data["scope"] == params["scope"])
        _text(data["revision"])
        _text(data["binding_revision"])
        try:
            raw = transfer.decode_document(data["document_base64"], size_bytes=data["size_bytes"],
                                           sha256=data["document_sha256"])
            document = transfer.validate_document_bytes(raw)
            _require(type(document) is dict and document.get("scope") == params["scope"])
            # The scope's 1 MiB wire bound excludes the local bundle wrapper.
            # Production needs the real provider-aware scope codec, not a
            # transport-only shape check or a guessed channel locator rule.
            _require(callable(scope_validator), "unresolved_variant")
            try:
                validated = scope_validator(json.loads(raw))
            except Exception:  # noqa: BLE001 - codec text can contain private data
                raise ProtocolError() from None
            _require(type(validated) is dict and validated == document)
            _require(document["scope"] == params["scope"])
        except (transfer.TransferError, document_codec.DocumentError):
            raise ProtocolError() from None
    elif op == "begin":
        _shape(data, COMMON | {"operation_id", "transaction_id", "next_seq", "received_bytes",
                               "idle_expires_at", "absolute_expires_at"})
        _pattern(data["transaction_id"], UUID)
        _progress(data, params["chunk_count"], params["size_bytes"])
    elif op == "append":
        _shape(data, COMMON | {"transaction_id", "next_seq", "received_bytes"})
        _integer(data["next_seq"], 1, transfer.MAX_CHUNKS)
        _integer(data["received_bytes"], 1)
        _require(data["next_seq"] == params["seq"] + 1
                 and data["received_bytes"] == params["offset"] + len(_chunk(params)))
    elif op == "preview":
        _shape(data, COMMON | {"transaction_id", "plan_digest", "base_revision", "binding_revision",
                               "coverage", "counts", "unresolved", "ready", "persistence", "stores"})
        _pattern(data["plan_digest"], DIGEST)
        _text(data["base_revision"])
        _text(data["binding_revision"])
        _require(data["coverage"] == document_codec.COVERAGE and type(data["coverage"]) is dict)
        _counts(data["counts"])
        _diagnostics(data["unresolved"], data["counts"])
        _require(type(data["ready"]) is bool and data["persistence"] == "persistent")
        _stores(data["stores"], preview=True)
    elif op == "status" and data.get("state") in ("receiving", "ready", "expired", "aborted"):
        base = COMMON | {"operation_id", "transaction_id", "persistence", "state"}
        _pattern(data.get("transaction_id"), UUID)
        _require(data.get("persistence") == "persistent")
        if data["state"] in ("receiving", "ready"):
            fields = base | {"next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at"}
            _shape(data, fields | ({"ready", "plan_digest", "base_revision", "binding_revision"}
                                   if data["state"] == "ready" else set()))
            _progress(data, ready=data["state"] == "ready")
            if data["state"] == "ready":
                _require(data["ready"] is True)
                _pattern(data["plan_digest"], DIGEST)
                _text(data["base_revision"])
                _text(data["binding_revision"])
        else:
            _shape(data, base | {"scope", "outcome", "per_store"})
            _scope(data["scope"])
            _require(data["outcome"] == "not_applied" and data["per_store"] == [])
        if context is not None:
            _bind_context(params, data, None, context)
    elif op in ("commit", "status"):
        _require(type(data.get("outcome")) is str and data["outcome"] in OUTCOMES)
        _require(data.get("persistence") == "persistent")
        if op == "commit":
            base = COMMON | {"transaction_id", "operation_id", "scope", "outcome", "persistence", "per_store"}
            _shape(data, base | (RECEIPT | {"receipt"} if data["outcome"] == "verified" else set()))
            _scope(data["scope"])
            if data["outcome"] == "verified":
                receipt = {k: data[k] for k in RECEIPT}
                _receipt(receipt)
                _receipt(data["receipt"])
                _require(data["receipt"] == receipt)
                _require(receipt["plan_digest"] == params["plan_digest"])
        else:
            _require(data.get("state") == "committed", "unresolved_variant")
            _shape(data, COMMON | {"operation_id", "state", "outcome", "persistence", "per_store"}
                   | ({"receipt"} if data["outcome"] == "verified" else set()))
            if data["outcome"] == "verified":
                receipt = data["receipt"]
                _receipt(receipt)
                _require(receipt["operation_id"] == params["operation_id"])
        _stores(data["per_store"], outcome=data["outcome"])
        if context is not None:
            _bind_context(params, data, receipt, context)
    else:
        _shape(data, COMMON | {"transaction_id", "operation_id", "state", "outcome", "persistence", "per_store"})
        _pattern(data["operation_id"], UUID)
        _require(data["state"] == "aborted" and data["outcome"] == "not_applied"
                 and data["persistence"] == "persistent" and data["per_store"] == [])
    return receipt is not None and context is not None, raw


def validate_result(params, data, *, request_id, response_id, status, context=None, scope_validator=None,
                    max_metadata_bytes=MAX_METADATA_BYTES):
    """Correlate parsed data; use validate_response when original envelope bytes exist.

    Snapshot additionally requires scope_validator(document) to validate the
    actual document schema/provider codec and return its unchanged value. No
    identity, pairing or grant authority is inferred from syntax validation.
    """
    _integer(max_metadata_bytes, 1, MAX_METADATA_BYTES)
    params = validate_request(params)
    _pattern(request_id, REQUEST_ID)
    _require(response_id == request_id and type(status) is str and status in ("ok", "rejected", "unsupported"))
    data, raw = _object(data, MAX_RESPONSE_BYTES)
    if params["op"] != "snapshot" or status != "ok":
        _require(len(raw) <= max_metadata_bytes, "envelope_too_large")
    # Include outer receipt fields in the bound, rather than budgeting data only.
    overhead = len(_encoded({"id": response_id, "status": status, "data": None})) - 4
    _require(len(raw) + overhead <= MAX_RESPONSE_BYTES, "envelope_too_large")
    verified, document = _validate_data(params, data, status, context, scope_validator)
    return ValidatedResult(_encoded(params), _encoded(data), status, request_id, verified, document)


def validate_response(params, response, *, request_id, context=None, scope_validator=None,
                      max_metadata_bytes=MAX_METADATA_BYTES):
    """Validate the complete authenticated {id,status,data} response, without I/O."""
    _integer(max_metadata_bytes, 1, MAX_METADATA_BYTES)
    response, raw = _object(response, MAX_RESPONSE_BYTES)
    _shape(response, ("id", "status", "data"))
    # Keep the original data token (including interior padding) for its own
    # byte cap. Parsing/re-encoding the envelope would silently erase padding.
    text, decoder = raw.decode("utf-8"), json.JSONDecoder()
    position = text.index("{") + 1
    while True:
        while text[position].isspace() or text[position] == ",":
            position += 1
        key, position = decoder.raw_decode(text, position)
        while text[position].isspace():
            position += 1
        position += 1  # The already-validated colon.
        while text[position].isspace():
            position += 1
        start = position
        _, position = decoder.raw_decode(text, position)
        if key == "data":
            data = text[start:position].encode("utf-8")
            break
    return validate_result(params, data, request_id=request_id, response_id=response["id"],
                           status=response["status"], context=context, scope_validator=scope_validator,
                           max_metadata_bytes=max_metadata_bytes)
