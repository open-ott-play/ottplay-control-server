"""Pure favorites bundle codec, not an enabled remote capability.

Scope documents preserve list order, multiplicity and auxiliary shuffleBefore.
Provider resolution and persistence belong to the host. Channels require an
injected proven locator validator; arbitrary existing itemId values are refused.
"""

import copy
import json
import math
import re
from collections import Counter

MAX_BYTES = 1024 * 1024
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
PROVIDERS = frozenset(("m3u", "vportal", "plex", "offline"))
COVERAGE = {"channelLists": "complete-for-scope", "mediaFavorites": "complete-for-scope",
            "remoteAliases": "excluded-preserved", "history": "excluded-preserved"}


class DocumentError(ValueError):
    def __init__(self, code="invalid_document"):
        super().__init__("Favorites document: " + code)
        self.code = code


def _require(condition, code="invalid_document"):
    if not condition:
        raise DocumentError(code)


def _shape(value, required, optional=()):
    _require(type(value) is dict and set(required) <= set(value)
             and set(value) <= set(required) | set(optional))


def _text(value, limit, empty=False):
    _require(type(value) is str and (empty or bool(value)) and len(value) <= limit
             and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise DocumentError() from None


def _uuid(value):
    _require(type(value) is str and UUID.fullmatch(value) is not None)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _bounded(value):
    """Bound traversal before copying even shared in-memory subtrees."""
    remaining, nodes = MAX_BYTES, 0

    def visit(item, depth=0):
        nonlocal remaining, nodes
        nodes += 1
        # Every visited value/key consumes at least one encoded byte. This
        # bound must not reject a dense, otherwise valid <=1 MiB document.
        _require(depth <= 16 and nodes <= MAX_BYTES, "document_too_large")
        if type(item) is dict:
            _require(len(item) <= 64, "document_too_large")
            remaining -= 2 + len(item) + max(len(item) - 1, 0)
            _require(remaining >= 0, "document_too_large")
            for key, child in item.items():
                _require(type(key) is str)
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(item) is list:
            _require(len(item) <= 65536, "document_too_large")
            remaining -= 2 + max(len(item) - 1, 0)
            _require(remaining >= 0, "document_too_large")
            for child in item:
                visit(child, depth + 1)
        else:
            _require(item is None or type(item) in (str, bool, int, float))
            if type(item) is str:
                _require(len(item) <= MAX_BYTES, "document_too_large")
            if type(item) is float:
                _require(math.isfinite(item))
            try:
                remaining -= len(_json(item).encode("utf-8"))
            except (ValueError, UnicodeError):
                raise DocumentError() from None
        _require(remaining >= 0, "document_too_large")

    visit(value)
    _require(len(_json(value).encode("utf-8")) <= MAX_BYTES, "document_too_large")


def _scope_key(document):
    scope = document["scope"]
    return (scope["provider"], scope["profileBindingId"], scope["sourceBindingId"])


def _locator(value, scope, channel_codec):
    _require(type(value) is dict)
    kind = value.get("kind")
    if kind == "channel":
        _shape(value, ("kind", "itemId"))
        _text(value["itemId"], 8192)
        proven = False
        if callable(channel_codec):
            try:
                proven = channel_codec(copy.deepcopy(scope), copy.deepcopy(value)) is True
            except Exception:  # noqa: BLE001 - codec failures can contain private source data
                proven = False
        _require(proven, "unsupported_channel_codec")
    elif kind == "plex-media":
        _shape(value, ("kind", "serverId", "ratingKey"))
        _text(value["serverId"], 256)
        _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value["serverId"]) is not None)
        _require(type(value["ratingKey"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value["ratingKey"]) is not None)
    elif kind == "vportal":
        _shape(value, ("kind", "mediaType", "leafId"))
        _require(value["mediaType"] in ("live", "movie", "episode"))
        _text(value["leafId"], 512)
    elif kind == "local":
        _shape(value, ("kind", "rootBindingId", "relativePath"))
        _uuid(value["rootBindingId"])
        path = value["relativePath"]
        _text(path, 8192)
        _require(not path.startswith("/") and "\\" not in path
                 and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path) is None
                 and all(part not in ("", ".", "..") for part in path.split("/")), "invalid_local_path")
    else:
        raise DocumentError("unsupported_locator")


def _reference(value, scope, channel_codec):
    _require(type(value) is dict)
    state = value.get("state")
    if state == "located":
        _shape(value, ("state", "locator"), ("title",))
        _locator(value["locator"], scope, channel_codec)
    elif state == "unresolved":
        _shape(value, ("state", "reason", "provenance"), ("title",))
        _require(value["reason"] in ("missing", "ambiguous", "legacy-unportable", "codec-unavailable"))
        provenance = value["provenance"]
        _shape(provenance, ("origin",), ("legacyId",))
        _require(provenance["origin"] in ("raw", "canonical"))
        if "legacyId" in provenance:
            _require(type(provenance["legacyId"]) is int and 0 <= provenance["legacyId"] <= 9007199254740991)
    else:
        raise DocumentError()
    if "title" in value:
        _text(value["title"], 512, empty=True)


def _scope_document(value, channel_codec):
    _shape(value, ("schema", "scope", "coverage", "channelLists", "mediaFavorites"))
    _require(type(value["schema"]) is int and value["schema"] == 1)
    scope = value["scope"]
    _shape(scope, ("profileBindingId", "sourceBindingId", "provider"))
    _uuid(scope["profileBindingId"])
    _uuid(scope["sourceBindingId"])
    _require(type(scope["provider"]) is str and scope["provider"] in PROVIDERS)
    _require(type(value["coverage"]) is dict and value["coverage"] == COVERAGE)
    lists = value["channelLists"]
    _shape(lists, ("active", "order", "default", "lists"))
    _require(type(lists["lists"]) is list and type(lists["order"]) is list)
    names = []
    for row in lists["lists"]:
        _shape(row, ("name", "entries"), ("shuffleBefore",))
        _text(row["name"], 256)
        _require(row["name"] not in ("__proto__", "prototype", "constructor") and row["name"] not in names)
        names.append(row["name"])
        for field in ("entries", "shuffleBefore"):
            if field not in row:
                continue
            _require(type(row[field]) is list)
            for reference in row[field]:
                _reference(reference, scope, channel_codec)
    for name in lists["order"]:
        _text(name, 256)
    _require(len(lists["order"]) == len(names) and set(lists["order"]) == set(names))
    _require(lists["active"] is None or type(lists["active"]) is str and lists["active"] in names)
    default = lists["default"]
    _shape(default, ("present",), ("value",))
    _require(type(default["present"]) is bool)
    if default["present"]:
        _require("value" in default and (default["value"] is None or type(default["value"]) is str and default["value"] in names))
    else:
        _require("value" not in default)
    _require(type(value["mediaFavorites"]) is list)
    for reference in value["mediaFavorites"]:
        _reference(reference, scope, channel_codec)


def validate_scope_document(value, *, channel_codec=None):
    """Validate one wire document at its own byte limit, without a bundle wrapper."""
    _bounded(value)
    _scope_document(value, channel_codec)
    return copy.deepcopy(value)


def validate_document(value, *, channel_codec=None):
    """Validate syntax; advertised codecs, PMS identity and target grants still gate use."""
    _bounded(value)
    _shape(value, ("version", "scopes"))
    _require(type(value["version"]) is int and value["version"] == 1 and type(value["scopes"]) is list)
    seen = set()
    for scope in value["scopes"]:
        _scope_document(scope, channel_codec)
        key = _scope_key(scope)
        _require(key not in seen, "duplicate_scope")
        seen.add(key)
    result = copy.deepcopy(value)
    result["scopes"].sort(key=_scope_key)
    return result


def _inventory(bundle):
    """Count distinct identified items plus separate unresolved occurrences.

    Located identity is shared across the two membership stores. Legacy numeric
    provenance is not proven to share their namespace. Opaque records have no
    portable identity: only an identical full record at the same list position
    compares as retained; changed content counts as removal/addition.
    """
    items, memberships = set(), Counter()
    for document in bundle["scopes"]:
        scope = _scope_key(document)
        collections = [("channels", row["name"], row["entries"]) for row in document["channelLists"]["lists"]]
        collections.append(("media", "favorites", document["mediaFavorites"]))
        for domain, name, rows in collections:
            for index, reference in enumerate(rows):
                if reference["state"] == "located":
                    identity = ("located", _json(reference["locator"]))
                elif "legacyId" in reference["provenance"]:
                    identity = ("unresolved", domain, reference["reason"], _json(reference["provenance"]))
                else:
                    # Unidentified entries have only positional occurrence counts,
                    # not a stable cross-device identity. Never dedup by title.
                    identity = ("opaque-occurrence", domain, name, index, _json(reference))
                item = (scope, identity)
                items.add(item)
                memberships[(domain, name, item)] += 1
    return items, memberships


def _counts(before, after):
    retained = sum((before & after).values())
    return {"before": sum(before.values()), "after": sum(after.values()),
            "added": sum(after.values()) - retained, "removed": sum(before.values()) - retained,
            "retained": retained}


def plan_documents(current, incoming, mode, *, channel_codec=None):
    """Union distinct scopes; differing overlap blocks, never resolves by title.

    Merge starts with the saved bundle. Replace starts with incoming scopes only;
    an intentional single-input replacement can differ from the saved document.
    Repeated membership is retained; duplicate whole-scope inputs are skipped.
    """
    _require(mode in ("merge", "replace") and type(incoming) is list and 1 <= len(incoming) <= 64)
    original = validate_document(current if current is not None else {"version": 1, "scopes": []}, channel_codec=channel_codec)
    documents = [validate_document(value, channel_codec=channel_codec) for value in incoming]
    before = {_scope_key(scope): scope for scope in original["scopes"]}
    selected = dict(before) if mode == "merge" else {}
    conflicts, duplicates = set(), 0
    for document in documents:
        for scope in document["scopes"]:
            key = _scope_key(scope)
            if key not in selected:
                selected[key] = scope
            elif _json(selected[key]) == _json(scope):
                duplicates += len(_inventory({"scopes": [scope]})[0])
            else:
                conflicts.add(key)
                # Keep the first selected input for replace; saved scope for merge.
    result = validate_document({"version": 1, "scopes": list(selected.values())}, channel_codec=channel_codec)
    old_items, old_memberships = _inventory(original)
    new_items, new_memberships = _inventory(result)
    counts = _counts(Counter({item: 1 for item in old_items}), Counter({item: 1 for item in new_items}))
    summary = {**counts, "skipped_duplicates": duplicates,
               "memberships": _counts(old_memberships, new_memberships),
               "conflicts": {"resolved": 0, "unresolved": len(conflicts)}}
    return {"document": result, "summary": summary}


def clear_scope(value, *, channel_codec=None):
    """Empty membership, retaining names/active/default and auxiliary shuffle history.

    Existing shuffleBefore can differ from current membership after UI edits;
    preserving it is intentional. It never contributes membership/item counts.
    """
    result = validate_document({"version": 1, "scopes": [value]}, channel_codec=channel_codec)["scopes"][0]
    for row in result["channelLists"]["lists"]:
        row["entries"] = []
    result["mediaFavorites"] = []
    return result
