"""Pure, bounded Torrent documents. Sources are private data, never display text."""
import copy
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

MAX_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_BYTES + 1024 * 1024 + 1024
MAX_SAFE = 9007199254740991
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PLAYABLE = frozenset("mp4 m4v mkv webm mov avi ts m2ts mpg mpeg ogv".split())
TRIM = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


class DocumentError(ValueError):
    def __init__(self, code="invalid_document"):
        self.code = code
        super().__init__("Torrent catalog: " + code)


def require(condition, code="invalid_document"):
    if not condition:
        raise DocumentError(code)


def shape(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def integer(value, low=0, high=MAX_SAFE):
    require(type(value) is int and low <= value <= high)
    return value


def text(value, units, byte_limit, *, trimmed=False):
    require(type(value) is str and 0 < len(value) <= units)
    try:
        require(len(value.encode("utf-8")) <= byte_limit and len(value.encode("utf-16-le")) // 2 <= units)
    except UnicodeError:
        raise DocumentError() from None
    require(not any(unicodedata.category(c) == "Cc" for c in value))
    require(not trimmed or value.strip(TRIM) == value)
    return value


def label(value):
    return text(value, 256, 1024, trimmed=True)


def identifier(value):
    require(type(value) is str and ID.fullmatch(value) is not None)
    return value


def source(value):
    text(value, 8192, 8192)
    require(not any(c.isspace() for c in value))
    if value.lower().startswith("magnet:?"):
        return value
    try:
        uri = urlsplit(value)
        require(uri.scheme in ("http", "https") and bool(uri.hostname)
                and uri.username is None and uri.password is None
                and uri.path.lower().endswith(".torrent"))
        _ = uri.port
    except ValueError:
        raise DocumentError() from None
    return value


def files(value):
    require(type(value) is list and len(value) <= 4096)
    seen, result = set(), []
    for row in value:
        shape(row, ("index", "name", "length"))
        index = integer(row["index"], 0, 999999999)
        require(index not in seen)
        seen.add(index)
        name = text(row["name"], 4096, 16384)
        require(name.rsplit(".", 1)[-1].lower() in PLAYABLE and "." in name)
        result.append({"index": index, "name": name,
                       "length": integer(row["length"])})
    return result


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DocumentError() from None


def catalog(value, *, allow_null=True):
    if value is None:
        require(allow_null)
        return None
    shape(value, ("schema", "id", "name", "entries"))
    integer(value["schema"], 1, 1)
    require(type(value["entries"]) is list and len(value["entries"]) <= 256)
    entries, seen, count = [], set(), 0
    for row in value["entries"]:
        shape(row, ("id", "title", "category", "source", "files"))
        key = identifier(row["id"])
        require(key not in seen)
        seen.add(key)
        selected = files(row["files"])
        count += len(selected)
        require(count <= 10000)
        entries.append({"id": key, "title": label(row["title"]), "category": label(row["category"]),
                        "source": source(row["source"]), "files": selected})
    result = {"schema": 1, "id": identifier(value["id"]), "name": label(value["name"]), "entries": entries}
    require(len(canonical(result)) <= MAX_BYTES, "document_too_large")
    return result


def metadata(value):
    shape(value, ("schema", "source", "files"))
    integer(value["schema"], 1, 1)
    result = {"schema": 1, "source": source(value["source"]), "files": files(value["files"])}
    require(len(canonical(result)) <= MAX_BYTES, "document_too_large")
    return result


def decode(raw, maximum=MAX_BYTES):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, "document_too_large")
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
            require(depth <= 8)
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0)

    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result

    def finite(value):
        number = float(value)
        require(math.isfinite(number))
        return number

    def invalid(_):
        raise DocumentError()

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=finite, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise DocumentError() from None


def merge(existing, incoming, mode):
    old, new = catalog(existing), catalog(incoming, allow_null=False)
    require(mode in ("merge", "replace"))
    if mode == "replace" or old is None:
        return new
    require(old["id"] == new["id"], "catalog_mismatch")
    result = copy.deepcopy(old)
    entries = {row["id"]: row for row in old["entries"]}
    for row in new["entries"]:
        if row["id"] in entries:
            require(entries[row["id"]] == row, "entry_conflict")
        else:
            result["entries"].append(copy.deepcopy(row))
    return catalog(result, allow_null=False)


def bundle(value, favorites_codec):
    shape(value, ("version", "kind", "catalog", "favorites"))
    integer(value["version"], 1, 1)
    require(value["kind"] == "ottplay-torrent-bundle")
    selected = catalog(value["catalog"], allow_null=False)
    favorite = favorites_codec.validate_document(value["favorites"])
    for entry in favorite["entries"]:
        locator = entry["locator"].get("channelId", "")
        match = re.fullmatch(r"torrent:([A-Za-z0-9_-]{1,64}):([A-Za-z0-9_-]{1,64}):(0|[1-9][0-9]{0,8})", locator)
        require(entry["origin"]["provider"] == "torrent" and entry["locator"].get("kind") == "channel"
                and match is not None and match[1] == selected["id"])
    return {"version": 1, "kind": "ottplay-torrent-bundle", "catalog": selected, "favorites": favorite}


def safe_display(value):
    """Do not expose URL/path-like imported labels, including format-character camouflage."""
    clean = "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()
    if ("://" in clean or clean.lower().startswith(("magnet:", "ottplay-"))
            or clean.startswith(("/", "\\", "~/", "~\\")) or re.match(r"^[A-Za-z]:[/\\]", clean)):
        return "[private label]"
    return clean
