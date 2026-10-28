"""Pure bounded validation for the independent favorites_global wire.

A validated receipt is the correlated player's readback claim, not physical
storage qualification. Hosts own authentication, target identity, negotiation,
deadlines, pacing and durable pre-commit context. This module performs no I/O.
"""

import base64
import binascii
import datetime
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

import favorites_transfer as transfer
import global_favorites_document as document_codec

ACTION = "favorites_global"
MAX_METADATA_BYTES = 131072
MAX_RESPONSE_BYTES = 2097152
MAX_DOCUMENT_BYTES = 1048576
ADMISSION_TTL_MS = 10000
OPS = frozenset(("describe", "export", "begin", "append", "preview", "commit", "status", "abort"))
KINDS = ("channel", "channel-name", "local-media", "plex-media", "vportal-media")
LIMITS = {"max_document_bytes": MAX_DOCUMENT_BYTES, "max_entries": 1000,
          "max_append_bytes": 8192, "max_chunks": 256, "max_export_bytes": 65536,
          "max_preview_rows": 64, "max_envelope_bytes": 16384, "max_metadata_bytes": MAX_METADATA_BYTES,
          "max_active_transactions": 1, "idle_ttl_seconds": 120, "absolute_ttl_seconds": 600,
          "max_receipts": 32, "max_receipt_ledger_bytes": 65536}
ERRORS = frozenset(("invalid_request", "envelope_too_large", "unsupported", "runtime_changed", "busy",
                    "expired", "operation_conflict", "transaction_not_found", "snapshot_not_found",
                    "out_of_order", "chunk_conflict", "incomplete", "hash_mismatch", "revision_conflict",
                    "context_changed", "invalid_document", "unportable_export", "not_ready", "policy_denied",
                    "unsupported_persistence", "storage_unavailable", "limits_exceeded", "reconciliation_required"))
WRITE_BLOCKERS = frozenset(("busy", "policy_denied", "unsupported_persistence", "storage_unavailable",
                            "limits_exceeded", "reconciliation_required"))
PREVIEW_BLOCKERS = WRITE_BLOCKERS - {"busy"} | {"revision_conflict", "context_changed", "invalid_document", "metadata_conflict"}
RESOLUTION = ("resolved", "missing", "ambiguous", "unsupported")
COUNT_FIELDS = ("before", "after", "added", "removed", "retained", "updated", "moved", "duplicates_skipped")
COMMON = frozenset(("version", "runtime", "op", "subject"))
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
RUNTIME = re.compile(r"[a-z0-9-]{1,64}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})\Z")
PREVIEW_FIELDS = frozenset(("operation_id", "transaction_id", "expected_revision", "context_revision",
                            "input_document_sha256", "result_document_sha256", "plan_digest", "counts",
                            "resolution_counts", "legacy_effects", "ready", "blocker", "page_offset", "rows", "next_offset", "required_kinds"))
RECEIPT_FIELDS = frozenset(("version", "subject", "operation_id", "operation_runtime", "transaction_id",
                            "input_document_sha256", "result_document_sha256", "expected_revision", "new_revision",
                            "context_revision", "plan_digest", "counts", "changed", "outcome", "persistence", "required_kinds"))


class ProtocolError(ValueError):
    def __init__(self, code="invalid_protocol"):
        self.code = code
        super().__init__({"invalid_protocol": "Invalid global favorites protocol metadata.",
                          "envelope_too_large": "Global favorites metadata exceeds its byte limit.",
                          "context_mismatch": "Global favorites evidence does not match the retained operation.",
                          "incomplete_export": "Global favorites export is incomplete or inconsistent."}[code])


@dataclass(frozen=True)
class ValidatedResult:
    _request: bytes = field(repr=False)
    _data: bytes = field(repr=False)
    status: str
    request_id: str
    verified: bool = False
    chunk: bytes | None = field(default=None, repr=False)

    @property
    def request(self):
        return json.loads(self._request)

    @property
    def data(self):
        return json.loads(self._data)


@dataclass(frozen=True)
class ReceiptContext:
    _record: bytes = field(repr=False)


@dataclass(frozen=True)
class ExportedDocument:
    raw_document: bytes = field(repr=False)
    revision: str
    document_sha256: str

    @property
    def document(self):
        return json.loads(self.raw_document)


def _require(condition, code="invalid_protocol"):
    if not condition:
        raise ProtocolError(code)


def _shape(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _integer(value, low=0, high=1000):
    _require(type(value) is int and low <= value <= high)


def _pattern(value, expression):
    _require(type(value) is str and expression.fullmatch(value) is not None)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _object(value, limit):
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result)
            result[key] = item
        return result

    def finite(value):
        number = float(value)
        _require(math.isfinite(number))
        return number

    def reject(_):
        raise ProtocolError()

    try:
        if type(value) is bytes:
            raw = value
        else:
            _require(type(value) is dict)
            pending, nodes = [(value, 0)], 0
            while pending:
                item, depth = pending.pop()
                nodes += 1
                _require(nodes <= limit and depth <= 16)
                if type(item) is dict:
                    _require(len(item) <= 128 and all(type(k) is str and len(k) <= limit for k in item))
                    pending.extend((x, depth + 1) for pair in item.items() for x in pair)
                elif type(item) is list:
                    _require(len(item) <= 1000)
                    pending.extend((x, depth + 1) for x in item)
                else:
                    _require(item is None or type(item) in (str, int, float, bool))
                    _require(type(item) is not str or len(item) <= limit)
            raw = _encoded(value)
        _require(0 < len(raw) <= limit, "envelope_too_large")
        depth, quoted, escaped = 0, False, False
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                _require(depth <= 16)
            elif byte in (93, 125):
                depth -= 1
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject, parse_float=finite)
        _require(type(result) is dict)
        _encoded(result)
        return result, raw
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError() from None


def _kinds(value):
    _require(type(value) is list and len(value) <= len(KINDS)
             and all(type(kind) is str and kind in KINDS for kind in value))
    _require(value == sorted(set(value)))


def _time(value):
    _pattern(value, TIMESTAMP)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        fraction = re.search(r"\.(\d+)", value)
        nanos = int(fraction.group(1).ljust(9, "0")) if fraction else 0
        return parsed.astimezone(datetime.timezone.utc).replace(microsecond=0), nanos
    except ValueError:
        raise ProtocolError() from None


def _counts(value):
    _shape(value, COUNT_FIELDS)
    for count in value.values():
        _integer(count)
    _require(value["before"] == value["removed"] + value["retained"]
             and value["after"] == value["added"] + value["retained"]
             and value["updated"] <= value["retained"] and value["moved"] <= value["retained"])


def _chunk(encoded, digest, maximum):
    _pattern(digest, DIGEST)
    _require(type(encoded) is str and 4 <= len(encoded) <= 4 * ((maximum + 2) // 3))
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ProtocolError() from None
    _require(0 < len(value) <= maximum and base64.b64encode(value).decode("ascii") == encoded
             and hashlib.sha256(value).hexdigest() == digest)
    return value


def validate_capability(value):
    result, _ = _object(value, MAX_METADATA_BYTES)
    _shape(result, ("version",))
    _integer(result["version"], 1, 1)
    return result


def validate_request(params, *, describe=None):
    value, _ = _object(params, LIMITS["max_envelope_bytes"])
    _require(type(value.get("op")) is str and value["op"] in OPS)
    op = value["op"]
    fields = {"describe": (), "export": ("snapshot_id", "offset", "max_bytes"),
              "begin": ("operation_id", "mode", "expected_revision", "expected_context_revision", "document_sha256",
                        "size_bytes", "chunk_count", "budget_ms", "required_kinds"),
              "append": ("transaction_id", "seq", "offset", "chunk_sha256", "data_base64"),
              "preview": ("transaction_id", "page_offset", "max_entries"),
              "commit": ("transaction_id", "operation_id", "plan_digest"),
              "status": ("operation_id",), "abort": ("transaction_id", "operation_id")}
    _shape(value, {"op", "runtime"} | set(fields[op]))
    _pattern(value["runtime"], RUNTIME)
    for key in ("snapshot_id", "operation_id", "transaction_id"):
        if key in value:
            _pattern(value[key], UUID)
    for key in ("expected_revision", "expected_context_revision", "document_sha256", "plan_digest"):
        if key in value:
            _pattern(value[key], DIGEST)
    if op == "begin":
        _require(value["mode"] in ("merge", "replace"))
        _integer(value["size_bytes"], 1, MAX_DOCUMENT_BYTES)
        _integer(value["chunk_count"], 1, 256)
        _integer(value["budget_ms"], 1, 600000)
        _kinds(value["required_kinds"])
        _require(value["chunk_count"] <= value["size_bytes"] <= value["chunk_count"] * 8192)
    elif op == "append":
        _integer(value["seq"], 0, 255)
        _integer(value["offset"], 0, MAX_DOCUMENT_BYTES - 1)
        chunk = _chunk(value["data_base64"], value["chunk_sha256"], 8192)
        _require(value["offset"] + len(chunk) <= MAX_DOCUMENT_BYTES)
    elif op == "export":
        _integer(value["offset"], 0, MAX_DOCUMENT_BYTES - 1)
        _integer(value["max_bytes"], 1, 65536)
    elif op == "preview":
        _integer(value["page_offset"])
        _integer(value["max_entries"], 1, 64)
    try:
        envelopes = transfer.request_envelopes(ACTION, value)
    except transfer.TransferError:
        raise ProtocolError() from None
    _require(max(len(envelopes.client), len(envelopes.queued_upper_bound)) <= LIMITS["max_envelope_bytes"],
             "envelope_too_large")
    if describe is not None:
        _require(op == "begin", "context_mismatch")
        _bind_describe(value, None, describe)
    return value


def validate_begin_negotiation(params, describe):
    """Require the player's validated grant before sending begin.

    The transport bounds elapsed grant time; the player owns cryptographic ID
    generation, runtime-local expiry and single-use consumption. This pure
    check cannot create, renew or consume an admission.
    """
    _require(describe is not None, "context_mismatch")
    return validate_request(params, describe=describe)


def _limits(value):
    _shape(value, LIMITS)
    for key, maximum in LIMITS.items():
        _integer(value[key], 1, maximum)
    _require(value["idle_ttl_seconds"] <= value["absolute_ttl_seconds"])


def _describe(data):
    _shape(data, COMMON | {"revision", "context_revision", "write_ready", "write_blocker", "admission", "export", "export_error", "limits", "locator_kinds"})
    _pattern(data["revision"], DIGEST)
    _pattern(data["context_revision"], DIGEST)
    _require(type(data["write_ready"]) is bool and
             ((data["write_ready"] and data["write_blocker"] is None) or
              (not data["write_ready"] and type(data["write_blocker"]) is str and data["write_blocker"] in WRITE_BLOCKERS)))
    if data["write_ready"]:
        _shape(data["admission"], ("operation_id", "ttl_ms"))
        _pattern(data["admission"]["operation_id"], UUID)
        _integer(data["admission"]["ttl_ms"], ADMISSION_TTL_MS, ADMISSION_TTL_MS)
    else:
        _require(data["admission"] is None)
    _limits(data["limits"])
    _kinds(data["locator_kinds"])
    descriptor = data["export"]
    if descriptor is None:
        _require(data["export_error"] in ("unportable_export", "limits_exceeded", "storage_unavailable"))
    else:
        _require(data["export_error"] is None)
        _shape(descriptor, ("snapshot_id", "document_sha256", "size_bytes", "entries", "expires_at"))
        _pattern(descriptor["snapshot_id"], UUID)
        _pattern(descriptor["document_sha256"], DIGEST)
        _integer(descriptor["size_bytes"], 1, data["limits"]["max_document_bytes"])
        _integer(descriptor["entries"], 0, data["limits"]["max_entries"])
        _time(descriptor["expires_at"])


def _preview(params, data):
    _shape(data, COMMON | PREVIEW_FIELDS)
    _pattern(data["operation_id"], UUID)
    _require(data["transaction_id"] == params["transaction_id"])
    for key in ("expected_revision", "context_revision", "input_document_sha256", "result_document_sha256", "plan_digest"):
        _pattern(data[key], DIGEST)
    _kinds(data["required_kinds"])
    _counts(data["counts"])
    _shape(data["legacy_effects"], ("linked_removed", "unmapped_retained"))
    for count in data["legacy_effects"].values():
        _integer(count, 0, 100)
    _shape(data["resolution_counts"], RESOLUTION)
    for count in data["resolution_counts"].values():
        _integer(count)
    total = data["counts"]["after"]
    _require(sum(data["resolution_counts"].values()) == total)
    _require(type(data["ready"]) is bool and
             ((data["ready"] and data["blocker"] is None) or
              (not data["ready"] and type(data["blocker"]) is str and data["blocker"] in PREVIEW_BLOCKERS)))
    _integer(data["page_offset"], 0, total)
    _require(data["page_offset"] == params["page_offset"] and type(data["rows"]) is list
             and len(data["rows"]) <= params["max_entries"])
    end = data["page_offset"] + len(data["rows"])
    _require(end <= total and (len(data["rows"]) > 0 or end == total))
    page_counts = dict.fromkeys(RESOLUTION, 0)
    conflict_indices = set()
    for index, row in enumerate(data["rows"], data["page_offset"]):
        _require(type(row) is dict)
        conflict = "metadata_conflict" in row
        _shape(row, ("entry_index", "state", "metadata_conflict") if conflict else ("entry_index", "state"))
        _integer(row["entry_index"], 0, 999)
        _require(row["entry_index"] == index and type(row["state"]) is str and row["state"] in RESOLUTION)
        page_counts[row["state"]] += 1
        if conflict:
            _require(not data["ready"] and data["blocker"] == "metadata_conflict")
            detail = row["metadata_conflict"]
            _shape(detail, ("reason", "incoming_entry_index"))
            _require(detail["reason"] == "title_mismatch")
            _integer(detail["incoming_entry_index"], 0, 999)
            _require(detail["incoming_entry_index"] not in conflict_indices)
            conflict_indices.add(detail["incoming_entry_index"])
    _require(all(page_counts[state] <= data["resolution_counts"][state] for state in RESOLUTION))
    if end == total:
        _require(data["next_offset"] is None)
    else:
        _integer(data["next_offset"], 1, 1000)
        _require(data["next_offset"] == end)


def _receipt(value):
    _shape(value, RECEIPT_FIELDS)
    _integer(value["version"], 1, 1)
    _require(value["subject"] == "global" and value["outcome"] == "verified" and value["persistence"] == "persistent")
    _pattern(value["operation_runtime"], RUNTIME)
    for key in ("operation_id", "transaction_id"):
        _pattern(value[key], UUID)
    for key in ("input_document_sha256", "result_document_sha256", "expected_revision", "new_revision", "context_revision", "plan_digest"):
        _pattern(value[key], DIGEST)
    _counts(value["counts"])
    _kinds(value["required_kinds"])
    _require(type(value["changed"]) is bool and value["changed"] == (value["expected_revision"] != value["new_revision"]))
    if not value["changed"]:
        _require(all(value["counts"][key] == 0 for key in ("added", "removed", "updated", "moved")))


def _data(params, data, status):
    _integer(data.get("version"), 1, 1)
    _require(data.get("runtime") == params["runtime"] and data.get("op") == params["op"] and data.get("subject") == "global")
    if "error" in data:
        _shape(data, COMMON | {"error"})
        _require(status in ("rejected", "unsupported") and type(data["error"]) is str and data["error"] in ERRORS)
        return None, None
    _require(status == "ok")
    op, chunk, receipt = params["op"], None, None
    if op == "describe":
        _describe(data)
    elif op == "export":
        _shape(data, COMMON | {"snapshot_id", "revision", "document_sha256", "size_bytes", "offset", "chunk_sha256", "data_base64", "next_offset", "eof"})
        _require(data["snapshot_id"] == params["snapshot_id"])
        _pattern(data["revision"], DIGEST)
        _pattern(data["document_sha256"], DIGEST)
        _integer(data["size_bytes"], 1, MAX_DOCUMENT_BYTES)
        _integer(data["offset"], 0, data["size_bytes"] - 1)
        _require(data["offset"] == params["offset"])
        chunk = _chunk(data["data_base64"], data["chunk_sha256"], params["max_bytes"])
        _integer(data["next_offset"], 1, data["size_bytes"])
        _require(data["next_offset"] == data["offset"] + len(chunk) and type(data["eof"]) is bool
                 and data["eof"] == (data["next_offset"] == data["size_bytes"]))
    elif op == "begin":
        _shape(data, COMMON | {"operation_id", "transaction_id", "expected_revision", "context_revision", "document_sha256", "size_bytes", "chunk_count", "next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at", "required_kinds"})
        _pattern(data["transaction_id"], UUID)
        for key in ("operation_id", "expected_revision", "document_sha256", "size_bytes", "chunk_count", "required_kinds"):
            _require(data[key] == params[key] and type(data[key]) is type(params[key]))
        _require(data["context_revision"] == params["expected_context_revision"])
        _integer(data["next_seq"], 0, 0)
        _integer(data["received_bytes"], 0, 0)
        _require(_time(data["idle_expires_at"]) <= _time(data["absolute_expires_at"]))
    elif op == "append":
        _shape(data, COMMON | {"operation_id", "transaction_id", "seq", "offset", "chunk_sha256", "next_seq", "received_bytes", "duplicate"})
        _pattern(data["operation_id"], UUID)
        for key in ("transaction_id", "seq", "offset", "chunk_sha256"):
            _require(data[key] == params[key] and type(data[key]) is type(params[key]))
        chunk = _chunk(params["data_base64"], params["chunk_sha256"], 8192)
        _integer(data["next_seq"], 1, 256)
        _integer(data["received_bytes"], 1, MAX_DOCUMENT_BYTES)
        _require(data["next_seq"] == params["seq"] + 1 and data["received_bytes"] == params["offset"] + len(chunk)
                 and type(data["duplicate"]) is bool)
    elif op == "preview":
        _preview(params, data)
    elif op == "commit":
        _shape(data, COMMON | {"operation_id", "transaction_id", "receipt"})
        _require(data["operation_id"] == params["operation_id"] and data["transaction_id"] == params["transaction_id"])
        receipt = data["receipt"]
        _receipt(receipt)
        _require(receipt["operation_id"] == params["operation_id"] and receipt["transaction_id"] == params["transaction_id"]
                 and receipt["operation_runtime"] == params["runtime"] and receipt["plan_digest"] == params["plan_digest"])
    elif op == "status":
        _shape(data, COMMON | {"operation_id", "operation_runtime", "transaction_id", "state", "outcome", "receipt"})
        _require(data["operation_id"] == params["operation_id"] and type(data["state"]) is str)
        state = data["state"]
        if state == "not_found":
            _require(data["operation_runtime"] is None and data["transaction_id"] is None
                     and data["receipt"] is None and data["outcome"] == "unknown")
        else:
            _pattern(data["operation_runtime"], RUNTIME)
            _pattern(data["transaction_id"], UUID)
            if state == "committed":
                receipt = data["receipt"]
                _receipt(receipt)
                _require(data["outcome"] == "verified" and receipt["operation_id"] == params["operation_id"]
                         and receipt["transaction_id"] == data["transaction_id"]
                         and receipt["operation_runtime"] == data["operation_runtime"])
            elif state in ("receiving", "ready", "expired", "aborted"):
                _require(data["receipt"] is None and data["outcome"] == "not_applied"
                         and data["operation_runtime"] == params["runtime"])
            else:
                _require(state in ("applying", "unknown") and data["receipt"] is None and data["outcome"] == "unknown")
    else:
        _shape(data, COMMON | {"operation_id", "transaction_id", "state", "outcome"})
        _require(data["operation_id"] == params["operation_id"] and data["transaction_id"] == params["transaction_id"]
                 and data["state"] == "aborted" and data["outcome"] == "not_applied")
    return receipt, chunk


def _saved(result):
    return {"request": result.request, "request_id": result.request_id, "data": result.data}


def _restore_saved(value):
    _shape(value, ("request", "request_id", "data"))
    return validate_result(value["request"], value["data"], request_id=value["request_id"],
                           response_id=value["request_id"], status="ok")


def _checked(result, op=None):
    _require(type(result) is ValidatedResult and result.status == "ok", "context_mismatch")
    checked = _restore_saved(_saved(result))
    _require(op is None or checked.request["op"] == op, "context_mismatch")
    return checked


def _bind_begin(begin, accepted, params, data):
    _require(params["runtime"] == begin["runtime"], "context_mismatch")
    for item in (params, data):
        if "operation_id" in item:
            _require(item["operation_id"] == begin["operation_id"], "context_mismatch")
        if "transaction_id" in item:
            _require(item["transaction_id"] == accepted["transaction_id"], "context_mismatch")
    if params["op"] == "preview":
        if data["blocker"] == "metadata_conflict":
            _require(begin["mode"] == "merge", "context_mismatch")
        _require(data["expected_revision"] == begin["expected_revision"]
                 and data["context_revision"] == begin["expected_context_revision"]
                 and data["input_document_sha256"] == begin["document_sha256"]
                 and data["required_kinds"] == begin["required_kinds"], "context_mismatch")
    if params["op"] == "append":
        _require(data["next_seq"] <= begin["chunk_count"] and data["received_bytes"] <= begin["size_bytes"],
                 "context_mismatch")


def _context_parts(context):
    _require(type(context) is ReceiptContext, "context_mismatch")
    record, _ = _object(context._record, MAX_METADATA_BYTES)
    _shape(record, ("version", "begin", "preview"))
    _integer(record["version"], 1, 1)
    begin = _restore_saved(record["begin"])
    _require(begin.request["op"] == "begin", "context_mismatch")
    preview = None
    if record["preview"] is not None:
        preview = _restore_saved(record["preview"])
        _require(preview.request["op"] == "preview" and preview.request["page_offset"] == 0, "context_mismatch")
        _bind_begin(begin.request, begin.data, preview.request, preview.data)
    return begin, preview


def retain_context(begin, first_preview=None):
    """Retain a begin alone for staging, then its first ready/blocked preview."""
    begin = _checked(begin, "begin")
    preview = _checked(first_preview, "preview") if first_preview is not None else None
    context = ReceiptContext(_encoded({"version": 1, "begin": _saved(begin),
                                       "preview": _saved(preview) if preview is not None else None}))
    _context_parts(context)
    return context


def export_context(context):
    """Return a copied, bounded record. Only a ready preview permits commit."""
    _context_parts(context)
    return json.loads(context._record)


def restore_context(record):
    """Revalidate private retained data; authentication/target binding is external."""
    value, _ = _object(record, MAX_METADATA_BYTES)
    result = ReceiptContext(_encoded(value))
    _context_parts(result)
    return result


def _immutable_preview(first, data):
    mutable = {"page_offset", "rows", "next_offset"}
    _require({k: v for k, v in first.items() if k not in mutable}
             == {k: v for k, v in data.items() if k not in mutable}, "context_mismatch")


def _bind_context(params, data, receipt, context):
    begin_result, preview_result = _context_parts(context)
    begin, accepted = begin_result.request, begin_result.data
    preview = preview_result.data if preview_result is not None else None
    op = params["op"]
    _require(op in ("append", "preview", "commit", "status", "abort"), "context_mismatch")
    if op != "status":
        _bind_begin(begin, accepted, params, data)
    else:
        _require(params["operation_id"] == begin["operation_id"], "context_mismatch")
        if data["state"] != "not_found":
            _require(data["transaction_id"] == accepted["transaction_id"]
                     and data["operation_runtime"] == begin["runtime"], "context_mismatch")
        if params["runtime"] != begin["runtime"]:
            _require(data["state"] in ("committed", "unknown", "not_found"), "context_mismatch")
    if op == "preview" and preview is not None:
        _immutable_preview(preview, data)
    if op == "commit":
        _require(preview is not None and preview["ready"] is True
                 and params["plan_digest"] == preview["plan_digest"], "context_mismatch")
    if receipt is not None:
        _require(preview is not None and preview["ready"] is True, "context_mismatch")
        expected = {"operation_id": begin["operation_id"], "operation_runtime": begin["runtime"],
                    "transaction_id": accepted["transaction_id"], "input_document_sha256": begin["document_sha256"],
                    "expected_revision": begin["expected_revision"], "context_revision": begin["expected_context_revision"],
                    "required_kinds": begin["required_kinds"], "plan_digest": preview["plan_digest"],
                    "result_document_sha256": preview["result_document_sha256"], "counts": preview["counts"]}
        _require(all(receipt[key] == value for key, value in expected.items()), "context_mismatch")


def _bind_describe(params, data, describe):
    described = _checked(describe, "describe")
    baseline = described.data
    _require(params["runtime"] == described.request["runtime"], "context_mismatch")
    limits = baseline["limits"]
    envelopes = transfer.request_envelopes(ACTION, params)
    _require(max(len(envelopes.client), len(envelopes.queued_upper_bound)) <= limits["max_envelope_bytes"],
             "envelope_too_large")
    op = params["op"]
    if op == "export":
        descriptor = baseline["export"]
        _require(descriptor is not None and params["max_bytes"] <= limits["max_export_bytes"], "context_mismatch")
        _require(data["revision"] == baseline["revision"]
                 and all(data[key] == descriptor[key] for key in ("snapshot_id", "document_sha256", "size_bytes")),
                 "context_mismatch")
    elif op == "begin":
        _require(baseline["write_ready"] and params["operation_id"] == baseline["admission"]["operation_id"]
                 and params["expected_revision"] == baseline["revision"]
                 and params["expected_context_revision"] == baseline["context_revision"]
                 and set(params["required_kinds"]) <= set(baseline["locator_kinds"])
                 and params["size_bytes"] <= limits["max_document_bytes"]
                 and params["chunk_count"] <= limits["max_chunks"]
                 and params["size_bytes"] <= params["chunk_count"] * limits["max_append_bytes"]
                 and params["budget_ms"] <= limits["absolute_ttl_seconds"] * 1000, "context_mismatch")
    elif op == "append":
        _chunk(params["data_base64"], params["chunk_sha256"], limits["max_append_bytes"])
    elif op == "preview":
        _require(params["max_entries"] <= limits["max_preview_rows"] and data["counts"]["after"] <= limits["max_entries"],
                 "context_mismatch")


def _bind_previous(params, data, previous):
    previous = _checked(previous, params["op"])
    old = previous.data
    _require(params["runtime"] == previous.request["runtime"], "context_mismatch")
    if params["op"] == "preview":
        _immutable_preview(old, data)
        _require(old["next_offset"] is not None and params["page_offset"] == old["next_offset"], "context_mismatch")
    elif params["op"] == "export":
        _require(not old["eof"] and params["offset"] == old["next_offset"]
                 and all(data[key] == old[key] for key in ("snapshot_id", "revision", "document_sha256", "size_bytes")),
                 "context_mismatch")
    else:
        raise ProtocolError("context_mismatch")


def validate_result(params, data, *, request_id, response_id, status, context=None, scope_validator=None,
                    max_metadata_bytes=MAX_METADATA_BYTES, describe=None, previous=None):
    """Validate data and exact request correlation; raw bytes retain padding limits."""
    _require(scope_validator is None)  # Global is not a fabricated scoped subject.
    _integer(max_metadata_bytes, 1, MAX_METADATA_BYTES)
    params = validate_request(params)
    _pattern(request_id, REQUEST_ID)
    _require(response_id == request_id and type(status) is str and status in ("ok", "rejected", "unsupported"))
    data, _ = _object(data, max_metadata_bytes)
    receipt, chunk = _data(params, data, status)
    if status == "ok":
        if describe is not None:
            _bind_describe(params, data, describe)
        if context is not None:
            _bind_context(params, data, receipt, context)
        if previous is not None:
            _bind_previous(params, data, previous)
    return ValidatedResult(_encoded(params), _encoded(data), status, request_id,
                           receipt is not None and context is not None, chunk)


def validate_response(params, response, *, request_id, context=None, scope_validator=None,
                      max_metadata_bytes=MAX_METADATA_BYTES, describe=None, previous=None):
    """Validate raw authenticated {id,status,data}; never erase token byte padding."""
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
                           context=context, scope_validator=scope_validator, max_metadata_bytes=max_metadata_bytes,
                           describe=describe, previous=previous)


def assemble_export(describe, pages):
    """Require the complete contiguous snapshot and exact canonical bytes/digest."""
    described = _checked(describe, "describe")
    descriptor = described.data["export"]
    _require(descriptor is not None and type(pages) is list and 1 <= len(pages) <= descriptor["size_bytes"], "incomplete_export")
    parts, offset, previous = [], 0, None
    for page in pages:
        page = _checked(page, "export")
        _require(page.request["offset"] == offset, "incomplete_export")
        page = validate_result(page.request, page._data, request_id=page.request_id, response_id=page.request_id,
                               status="ok", describe=described, previous=previous)
        parts.append(page.chunk)
        offset = page.data["next_offset"]
        previous = page
    _require(offset == descriptor["size_bytes"] and previous.data["eof"], "incomplete_export")
    raw = b"".join(parts)
    _require(hashlib.sha256(raw).hexdigest() == descriptor["document_sha256"], "incomplete_export")
    try:
        document = document_codec.load_document(raw)
        canonical = document_codec.canonical_bytes(document)
    except document_codec.DocumentError:
        raise ProtocolError("incomplete_export") from None
    _require(raw == canonical and len(document["entries"]) == descriptor["entries"]
             and set(document_codec.required_locator_kinds(document)) <= set(described.data["locator_kinds"]), "incomplete_export")
    return ExportedDocument(raw, described.data["revision"], descriptor["document_sha256"])


def validate_preview_pages(context, pages):
    """Confirm complete ordered resolution diagnostics without claiming writes."""
    _, first = _context_parts(context)
    _require(first is not None and type(pages) is list and 1 <= len(pages) <= max(1, first.data["counts"]["after"]),
             "context_mismatch")
    offset, previous, totals, rows = 0, None, dict.fromkeys(RESOLUTION, 0), []
    conflict_indices = set()
    for page in pages:
        page = _checked(page, "preview")
        _require(page.request["page_offset"] == offset, "context_mismatch")
        checked = validate_result(page.request, page._data, request_id=page.request_id, response_id=page.request_id,
                                  status="ok", context=context, previous=previous)
        for row in checked.data["rows"]:
            totals[row["state"]] += 1
            rows.append(row)
            if "metadata_conflict" in row:
                incoming_index = row["metadata_conflict"]["incoming_entry_index"]
                _require(incoming_index not in conflict_indices, "context_mismatch")
                conflict_indices.add(incoming_index)
        offset += len(checked.data["rows"])
        previous = checked
    _require(offset == first.data["counts"]["after"] and previous.data["next_offset"] is None
             and totals == first.data["resolution_counts"], "context_mismatch")
    _require(bool(conflict_indices) == (first.data["blocker"] == "metadata_conflict"), "context_mismatch")
    return rows


def validate_metadata_conflicts(rows, incoming, result_document):
    """Bind bounded conflict indices to the locally verified documents."""
    try:
        source = document_codec.validate_document(incoming)["entries"]
        result = document_codec.validate_document(result_document)["entries"]
        for row in rows:
            if "metadata_conflict" not in row:
                continue
            old = result[row["entry_index"]]
            new = source[row["metadata_conflict"]["incoming_entry_index"]]
            _require(document_codec.identity(old) == document_codec.identity(new)
                     and old["title"] != new["title"], "context_mismatch")
    except (document_codec.DocumentError, IndexError, KeyError, TypeError):
        raise ProtocolError("context_mismatch") from None
