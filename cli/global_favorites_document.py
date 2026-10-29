"""Pure portable global favorites and deterministic merge previews.

Public origins/locators are data, never receiver bindings or authority. Missing
or unresolved sources remain representable; only the host may resolve/play them.
This module accesses no filesystem, configuration, network, player or store.
"""

import json
import re

import favorites_transfer as transfer

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 1000
MAX_DOCUMENTS = 64
# Valid documents need only four nested containers. Check before JSON parsing.
MAX_JSON_DEPTH = 8
JS_TRIM = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
PROVIDER = re.compile(r"[a-z0-9][a-z0-9_.-]*(?:/[a-z0-9][a-z0-9_.-]*)*\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
PMS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
RATING_KEY = re.compile(r"[1-9][0-9]{0,19}\Z")
VPORTAL_ID_FIELDS = frozenset(("id", "fid", "stream_id", "itemId", "media_id", "episode_id"))


class DocumentError(ValueError):
    """Fixed diagnostics without an untrusted value or locator."""

    def __init__(self, code="invalid_document"):
        self.code = code
        super().__init__("Global favorites document: " + code)


def _require(condition, code="invalid_document"):
    if not condition:
        raise DocumentError(code)


def _shape(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _text(value, maximum):
    _require(type(value) is str and 0 < len(value) <= maximum)
    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeError:
        raise DocumentError() from None
    _require(units <= maximum and value.strip(JS_TRIM) == value
             and not any(ord(char) < 32 or ord(char) == 127 for char in value)
             and "://" not in value)
    return value


def _pattern(value, pattern):
    _require(type(value) is str and pattern.fullmatch(value) is not None)
    return value


def _origin(value):
    _shape(value, ("provider", "profile", "subprofile", "sourceAlias"))
    _require(type(value["provider"]) is str and 1 <= len(value["provider"]) <= 64)
    return {"profile": _text(value["profile"], 256),
            "provider": _pattern(value["provider"], PROVIDER),
            "sourceAlias": _text(value["sourceAlias"], 256),
            "subprofile": None if value["subprofile"] is None else _text(value["subprofile"], 256)}


def _locator(provider, value):
    if provider == "plex":
        _shape(value, ("kind", "serverId", "ratingKey"))
        _require(value["kind"] == "plex-media")
        return {"kind": "plex-media", "ratingKey": _pattern(value["ratingKey"], RATING_KEY),
                "serverId": _pattern(value["serverId"], PMS)}
    if provider == "local":
        _shape(value, ("kind", "rootAlias", "relativePath"))
        _require(value["kind"] == "local-media")
        path = _text(value["relativePath"], 8192)
        _require(not path.startswith("/") and not re.match(r"[A-Za-z]:", path) and "\\" not in path
                 and all(segment not in ("", ".", "..") for segment in path.split("/")))
        return {"kind": "local-media", "relativePath": path, "rootAlias": _text(value["rootAlias"], 256)}
    if (provider == "vportal" or provider == "m3u" and type(value) is dict
            and set(value) == {"kind", "idField", "id"} and value["kind"] == "vportal-media"):
        _shape(value, ("kind", "idField", "id"))
        _require(value["kind"] == "vportal-media" and type(value["idField"]) is str
                 and value["idField"] in VPORTAL_ID_FIELDS)
        return {"id": _pattern(value["id"], IDENTIFIER), "idField": value["idField"], "kind": "vportal-media"}
    if type(value) is dict and value.get("kind") == "channel-name":
        _shape(value, ("kind", "name", "group"))
        return {"group": None if value["group"] is None else _text(value["group"], 256),
                "kind": "channel-name", "name": _text(value["name"], 1024)}
    _shape(value, ("kind", "channelId"))
    _require(value["kind"] == "channel")
    identifier = _pattern(value["channelId"], IDENTIFIER)
    _require(re.fullmatch(r"channel:[0-9]+", identifier) is None)
    return {"channelId": identifier, "kind": "channel"}


def _entry(value):
    _shape(value, ("title", "origin", "locator"))
    origin = _origin(value["origin"])
    return {"locator": _locator(origin["provider"], value["locator"]),
            "origin": origin, "title": _text(value["title"], 1024)}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _identity(entry):
    return _json({"locator": entry["locator"], "origin": entry["origin"]})


def identity(entry):
    """Canonical origin+locator text; no title, case folding or normalization."""
    return _identity(_entry(entry))


def validate_document(value):
    """Return a fresh normalized document, preserving entry order exactly."""
    _shape(value, ("version", "entries"))
    _require(type(value["version"]) in (int, float) and value["version"] == 1 and type(value["entries"]) is list)
    _require(len(value["entries"]) <= MAX_ENTRIES, "document_too_large")
    entries, seen = [], set()
    size = len(b'{"entries":[],"version":1}')
    for value_entry in value["entries"]:
        entry = _entry(value_entry)
        size += len(_json(entry).encode("utf-8")) + bool(entries)
        _require(size <= MAX_BYTES, "document_too_large")
        key = _identity(entry)
        _require(key not in seen, "duplicate_entry")
        seen.add(key)
        entries.append(entry)
    return {"entries": entries, "version": 1}


def canonical_bytes(value):
    """Compact fixed-key-order UTF-8 without BOM, newline or normalization."""
    return _json(validate_document(value)).encode("utf-8")


def required_locator_kinds(value):
    """Return sorted unique kinds; this makes no provider/mapping assertion."""
    return sorted({entry["locator"]["kind"] for entry in validate_document(value)["entries"]})


def supports(value, advertised_kinds):
    """Check the V3 explicit kind-list contract, not trusted host capability.

    Unknown advertised kind strings are tolerated for forward compatibility.
    They do not make an unknown document kind valid or grant mapping authority.
    """
    try:
        required = required_locator_kinds(value)
    except DocumentError:
        return False
    if type(advertised_kinds) is not list or len(advertised_kinds) > 32:
        return False
    seen = set()
    for kind in advertised_kinds:
        # This list is an opaque advertisement, not stored portable text. Count
        # JS string units exactly; document strings require valid scalars above.
        if (type(kind) is not str or len(kind) > 64
                or len(kind.encode("utf-16-le", "surrogatepass")) // 2 > 64
                or kind in seen):
            return False
        seen.add(kind)
    return all(kind in seen for kind in required)


def load_document(raw):
    """Parse bounded original JSON and validate the portable document.

    Raw input and canonical output are each limited to one MiB. Keep original
    bytes separately when a transport digest authenticates those bytes.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "document_too_large")
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
            _require(depth <= MAX_JSON_DEPTH, "invalid_json")
        elif byte in (93, 125):
            depth -= 1
    try:
        parsed = transfer.validate_document_bytes(raw)
    except transfer.TransferError:
        raise DocumentError("invalid_json") from None
    return validate_document(parsed)


def plan_documents(current, incoming, mode):
    """Preview a union without interpreting receiver availability.

    Merge retains destination order/titles. Replace uses incoming order and
    permits deliberate changes from the destination. Conflicting titles among
    selected inputs retain the first row only as a blocked preview. Diagnostics
    contain indices, not payloads; at most one title conflict per selected
    identity. A plan with can_commit=False must not be applied. Runtime mapping
    is not evaluated here, even when can_commit=True.

    All incoming documents together are bounded to 1000 entries and one MiB of
    canonical bytes before merging. The existing destination has its own limits.
    """
    _require(type(mode) is str and mode in ("merge", "replace"), "invalid_mode")
    _require(type(incoming) is list and 1 <= len(incoming) <= MAX_DOCUMENTS, "invalid_documents")
    original = validate_document(current if current is not None else {"version": 1, "entries": []})
    documents, count, size = [], 0, 0
    for value in incoming:
        _shape(value, ("version", "entries"))
        _require(type(value["entries"]) is list)
        count += len(value["entries"])
        _require(count <= MAX_ENTRIES, "document_too_large")
        document = validate_document(value)
        size += len(_json(document).encode("utf-8"))
        _require(size <= MAX_BYTES, "document_too_large")
        documents.append(document)
    selected = list(original["entries"]) if mode == "merge" else []
    positions = {_identity(row): index for index, row in enumerate(selected)}
    conflicts, duplicates = {}, 0
    for document_index, document in enumerate(documents):
        for entry_index, row in enumerate(document["entries"]):
            key = _identity(row)
            if key not in positions:
                _require(len(selected) < MAX_ENTRIES, "document_too_large")
                positions[key] = len(selected)
                selected.append(row)
            elif selected[positions[key]]["title"] == row["title"]:
                duplicates += 1
            elif key not in conflicts:
                conflicts[key] = {"existing_index": positions[key], "incoming_document": document_index,
                                  "incoming_index": entry_index, "reason": "title_mismatch"}
    result = validate_document({"version": 1, "entries": selected})
    before = {_identity(row): row for row in original["entries"]}
    after = {_identity(row): row for row in result["entries"]}
    retained = before.keys() & after.keys()
    old_positions = {_identity(row): index for index, row in enumerate(original["entries"])}
    new_positions = {_identity(row): index for index, row in enumerate(result["entries"])}
    summary = {"before": len(before), "after": len(after), "added": len(after.keys() - before.keys()),
               "removed": len(before.keys() - after.keys()), "retained": len(retained),
               "moved": sum(old_positions[key] != new_positions[key] for key in retained),
               "skipped_duplicates": duplicates,
               "title_changes": sum(before[key]["title"] != after[key]["title"] for key in retained),
               "title_conflicts": len(conflicts)}
    return {"document": result, "summary": summary,
            "title_conflicts": list(conflicts.values()), "can_commit": not conflicts}
