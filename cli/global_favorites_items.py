"""Strict global list/current/play protocol; no CLI or capability registration.

Reads retain the existing portable codec and authenticated request transport.
A complete receiver-bound list is required before one playback dispatch. This
module never changes favorites, mappings, settings or operation receipts.
"""

import json
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import favorites_remote as transport
import favorites_transfer as transfer
import global_favorites_document as document
import global_favorites_protocol as core

ACTION = "favorites_global_items"
OPS = frozenset(("list", "current", "play"))
MAX_METADATA_BYTES = 65536
MAX_RESPONSE_BYTES = core.MAX_RESPONSE_BYTES
MAX_ENVELOPE_BYTES = 16384
MAX_ENTRIES = document.MAX_ENTRIES
MAX_PAGE_ENTRIES = 100
CURSOR = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
COMMON = frozenset(("version", "runtime", "op", "subject"))
REVISIONS = ("revision", "context_revision")
UNAVAILABLE = frozenset(("no_portable_current_item", "standby", "unsupported_provider"))
ERRORS = frozenset(("invalid_request", "envelope_too_large", "unsupported", "runtime_changed",
                    "expired", "cancelled", "policy_denied", "busy", "storage_unavailable",
                    "unsupported_persistence", "limits_exceeded", "invalid_cursor", "revision_conflict",
                    "context_changed", "favorite_not_found", "source_unavailable", "ambiguous_source",
                    "item_unavailable", "ambiguous_item", "unsupported_item", "playback_failed"))
ProtocolError = core.ProtocolError
ValidatedResult = core.ValidatedResult
RemoteError = transport.RemoteError
_require, _shape, _pattern, _integer = core._require, core._shape, core._pattern, core._integer
_object, _encoded = core._object, core._encoded


def validate_capability(value):
    return core.validate_capability(value)


validate_items_capability = validate_capability


def _cursor(value):
    if value is not None:
        _pattern(value, CURSOR)


def _portable(entries):
    try:
        return document.validate_document({"version": 1, "entries": entries})
    except document.DocumentError:
        raise ProtocolError() from None


def validate_request(params):
    value, _ = _object(params, MAX_ENVELOPE_BYTES)
    _pattern(value.get("runtime"), core.RUNTIME)
    _require(value.get("subject") == "global" and type(value.get("op")) is str and value["op"] in OPS)
    fields = {"runtime", "op", "subject"}
    if value["op"] == "list":
        _shape(value, fields | {"cursor", "max_entries"})
        _cursor(value["cursor"])
        _integer(value["max_entries"], 1, MAX_PAGE_ENTRIES)
    elif value["op"] == "current":
        _shape(value, fields)
    else:
        _shape(value, fields | {"id", "expected_revision", "expected_context_revision"})
        _pattern(value["id"], core.UUID)
        for key in ("expected_revision", "expected_context_revision"):
            _pattern(value[key], core.DIGEST)
    try:
        envelopes = transfer.request_envelopes(ACTION, value)
    except transfer.TransferError:
        raise ProtocolError() from None
    _require(max(len(envelopes.client), len(envelopes.queued_upper_bound)) <= MAX_ENVELOPE_BYTES,
             "envelope_too_large")
    return value


def _page(params, data):
    _shape(data, COMMON | set(REVISIONS) | {"cursor", "page_offset", "total", "entries", "next_cursor",
                                          "unresolved_legacy_count"})
    _require(data["cursor"] == params["cursor"], "context_mismatch")
    _integer(data["page_offset"], 0, MAX_ENTRIES)
    _integer(data["total"], 0, MAX_ENTRIES)
    _integer(data["unresolved_legacy_count"], 0, 100)
    rows = data["entries"]
    _require(type(rows) is list and len(rows) <= params["max_entries"])
    ids, entries = set(), []
    for row in rows:
        _shape(row, ("id", "entry"))
        _pattern(row["id"], core.UUID)
        _require(row["id"] not in ids)
        ids.add(row["id"])
        entries.append(row["entry"])
    _portable(entries)
    offset, end = data["page_offset"], data["page_offset"] + len(rows)
    _require(end <= data["total"])
    _require(offset == 0 if params["cursor"] is None else offset > 0)
    if not rows:
        _require(params["cursor"] is None and offset == data["total"] == 0)
    _cursor(data["next_cursor"])
    if end < data["total"]:
        _require(bool(rows) and data["next_cursor"] is not None and data["next_cursor"] != params["cursor"])
    else:
        _require(data["next_cursor"] is None)


def validate_result(params, data, *, request_id, response_id, status, context=None,
                    scope_validator=None, max_metadata_bytes=MAX_METADATA_BYTES):
    _require(context is None and scope_validator is None)
    _integer(max_metadata_bytes, 1, MAX_METADATA_BYTES)
    params = validate_request(params)
    _pattern(request_id, core.REQUEST_ID)
    _require(response_id == request_id and type(status) is str and status in ("ok", "rejected", "unsupported"))
    data, raw = _object(data, max_metadata_bytes)
    _integer(data.get("version"), 1, 1)
    _require(data.get("runtime") == params["runtime"] and data.get("op") == params["op"]
             and data.get("subject") == "global", "context_mismatch")
    if "error" in data:
        _shape(data, COMMON | {"error"})
        _require(status in ("rejected", "unsupported") and type(data["error"]) is str and data["error"] in ERRORS)
    else:
        _require(status == "ok")
        for key in REVISIONS:
            _pattern(data.get(key), core.DIGEST)
        if params["op"] == "list":
            _page(params, data)
        elif params["op"] == "current":
            _shape(data, COMMON | set(REVISIONS) | {"entry", "unavailable_reason"})
            if data["entry"] is None:
                _require(type(data["unavailable_reason"]) is str and data["unavailable_reason"] in UNAVAILABLE)
            else:
                _require(data["unavailable_reason"] is None)
                _portable([data["entry"]])
        else:
            _shape(data, COMMON | set(REVISIONS) | {"id", "dispatched"})
            _require(data["dispatched"] is True and data["id"] == params["id"]
                     and data["revision"] == params["expected_revision"]
                     and data["context_revision"] == params["expected_context_revision"], "context_mismatch")
    return ValidatedResult(_encoded(params), raw, status, request_id)


def validate_response(params, response, *, request_id, context=None, scope_validator=None,
                      max_metadata_bytes=MAX_METADATA_BYTES):
    """Reuse the strict core JSON parser; retain the original data-token bytes."""
    value, raw = _object(response, MAX_RESPONSE_BYTES)
    _shape(value, ("id", "status", "data"))
    # The object is already parsed/validated. Decode positions only to preserve
    # interior whitespace and escaping in the smaller per-data byte budget.
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


@dataclass(frozen=True)
class Listing:
    """Complete list evidence, bound to the issuing peer and authenticated target."""
    _target: tuple = field(repr=False)
    _data: bytes = field(repr=False)

    @property
    def data(self):
        return json.loads(self._data)

    @property
    def document(self):
        return _portable([row["entry"] for row in self.data["entries"]])


class ItemsPeer(transport._RequestTransport):
    _protocol = sys.modules[__name__]
    _action = ACTION

    def __init__(self, client, device, *, runtime, timeout=45, monotonic=time.monotonic,
                 sleep=time.sleep, deadline=None):
        super().__init__(client, device, runtime=runtime, envelope_limit=MAX_ENVELOPE_BYTES,
                         response_limit=MAX_RESPONSE_BYTES, scope_validator=None, timeout=timeout,
                         monotonic=monotonic, sleep=sleep, max_metadata_bytes=MAX_METADATA_BYTES,
                         deadline=deadline, allowed_ops=OPS)
        self._operation_lock = threading.Lock()
        self._listing = None
        self._played = False

    @contextmanager
    def _operation(self):
        if not self._operation_lock.acquire(blocking=False):
            raise RemoteError("already_started")
        try:
            yield
        finally:
            self._operation_lock.release()

    def list(self, *, max_entries=MAX_PAGE_ENTRIES):
        """Return every row from one revision or raise; no partial list escapes."""
        with self._operation():
            self._listing = None
            self._played = False
            cursor, first, rows = None, None, []
            seen_cursors, ids, identities = set(), set(), set()
            empty_size = len(document.canonical_bytes({"version": 1, "entries": []}))
            size = empty_size
            for _ in range(MAX_ENTRIES):
                result = self._rpc({"op": "list", "runtime": self.runtime, "subject": "global",
                                    "cursor": cursor, "max_entries": max_entries}, deadline=self._deadline)
                data = result.data
                if first is None:
                    first = data
                elif any(data[key] != first[key] for key in (*REVISIONS, "total", "unresolved_legacy_count")):
                    raise RemoteError("invalid_response")
                if data["page_offset"] != len(rows):
                    raise RemoteError("invalid_response")
                for row in data["entries"]:
                    identity = document.identity(row["entry"])
                    if row["id"] in ids or identity in identities:
                        raise RemoteError("invalid_response")
                    row_size = len(document.canonical_bytes({"version": 1, "entries": [row["entry"]]})) - empty_size
                    size += row_size + bool(rows)
                    if size > document.MAX_BYTES:
                        raise RemoteError("invalid_response")
                    ids.add(row["id"])
                    identities.add(identity)
                    rows.append(row)
                cursor = data["next_cursor"]
                if cursor is None:
                    if len(rows) != first["total"]:
                        raise RemoteError("invalid_response")
                    complete = {"status": "complete", "runtime": self.runtime, "subject": "global",
                                **{key: first[key] for key in (*REVISIONS, "total", "unresolved_legacy_count")},
                                "entries": rows}
                    self._listing = Listing(self._target, _encoded(complete))
                    return self._listing
                if cursor in seen_cursors:
                    raise RemoteError("invalid_response")
                seen_cursors.add(cursor)
            raise RemoteError("invalid_response")

    def current(self):
        with self._operation():
            return self._rpc({"op": "current", "runtime": self.runtime, "subject": "global"}, deadline=self._deadline)

    def play(self, item_id, listing):
        """Attempt one dispatch from this peer's complete pinned list, never replay."""
        with self._operation():
            if type(listing) is not Listing or listing is not self._listing or listing._target != self._target:
                raise RemoteError("invalid_snapshot")
            if self._played:
                raise RemoteError("already_started")
            data = listing.data
            if type(item_id) is not str or not any(row["id"] == item_id for row in data["entries"]):
                raise RemoteError("invalid_snapshot")
            params = self._params({"op": "play", "runtime": self.runtime, "subject": "global", "id": item_id,
                                   "expected_revision": data["revision"], "expected_context_revision": data["context_revision"]})
            self._played = True
            return self._rpc(params, deadline=self._deadline)
