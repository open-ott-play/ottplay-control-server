"""Local indexer control through an owned player; no downloads or mutation replay."""
import copy
import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import unicodedata
from urllib.parse import urlsplit

SOURCE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z", re.ASCII)
REFERENCE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
RUNTIME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z", re.ASCII)
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII)
MAX_SAFE = 9007199254740991
MAX_RESPONSE = 2 * 1024 * 1024
MAX_KEY = 2048
RPC_INTERVAL = 1.5
REASONS = frozenset((
    'companion_access_denied',
    'companion_endpoint_invalid',
    'companion_module_unavailable',
    'companion_not_configured',
    'companion_request_failed',
    'companion_timeout',
    'companion_transport_unavailable',
    'companion_unavailable',
    'expired',
    'file_selection_required',
    'invalid_metadata',
    'invalid_request',
    'invalid_source',
    'invalid_tracker',
    'metadata_limit',
    'policy_restricted',
    'runtime_changed',
    'service_stopped',
    'torznab_api_error',
    'torznab_busy',
    'torznab_cancelled',
    'torznab_http_error',
    'torznab_invalid_category',
    'torznab_invalid_link',
    'torznab_invalid_page',
    'torznab_invalid_query',
    'torznab_invalid_source',
    'torznab_invalid_xml',
    'torznab_local_only',
    'torznab_network_error',
    'torznab_reference_missing',
    'torznab_response_limit',
    'torznab_search_unavailable',
    'torznab_source_changed',
    'torznab_source_limit',
    'torznab_source_missing',
    'torznab_store_busy',
    'torznab_store_limit',
    'torznab_store_unavailable',
    'torznab_timeout',
    'torznab_xml_limit',
    'tracker_limit',
    'unauthorized',
    'unavailable',
    'unsupported',
))
LOCAL_REASONS = frozenset(("invalid_arguments", "invalid_response", "invalid_key_file", "invalid_api_key",
    "missing_api_key", "context_changed", "deadline_exceeded", "request_unconfirmed", "request_failed",
    "category_unavailable", "limit_unavailable", "search_unavailable"))
USAGE = """Use torrent indexer:
  list
  configure ID --label LABEL --endpoint URL (--api-key-file FILE | --api-key-env NAME)
  remove ID
  caps ID
  search ID [QUERY] [--query QUERY] [--category IDS] [--offset N] [--limit N]
  prepare ID RESULT_REF

Search defaults: offset 0, limit 50. Category IDs are comma-separated integers.
prepare returns an opaque ottplay-local-torrent: source for torrent add/inspect.
API keys are read locally; endpoint and key are never printed. Requests are not replayed."""


class IndexerError(ValueError):
    def __init__(self, code, *, rejected=False):
        self.code = code
        self.rejected = rejected
        super().__init__("Torrent indexer: " + code.replace("_", " "))


def require(condition, code="invalid_response"):
    if not condition:
        raise IndexerError(code)


def _pattern(value, pattern, code="invalid_response"):
    require(type(value) is str and pattern.fullmatch(value) is not None, code)
    return value


def _shape(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def _integer(value, low=0, high=MAX_SAFE):
    require(type(value) is int and low <= value <= high)
    return value


def _text(value, maximum, *, empty=False, code="invalid_response"):
    require(type(value) is str and (empty or bool(value.strip())), code)
    try:
        require(len(value.encode("utf-8")) <= maximum, code)
    except UnicodeError:
        raise IndexerError(code) from None
    require(not any(unicodedata.category(char) == "Cc" for char in value), code)
    return value


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise IndexerError("invalid_response") from None


def _endpoint(value):
    _text(value, 4096, code="invalid_arguments")
    require(not any(char.isspace() for char in value) and not any(char in value for char in "\\?#"), "invalid_arguments")
    require(re.search(r"%(?![0-9A-Fa-f]{2})", value) is None, "invalid_arguments")
    try:
        parsed = urlsplit(value)
        require(parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment and not parsed.netloc.endswith(":"), "invalid_arguments")
        require(parsed.port is None or 0 < parsed.port <= 65535, "invalid_arguments")
    except ValueError:
        raise IndexerError("invalid_arguments") from None
    return value


def _number(value, low, high):
    require(type(value) is str and re.fullmatch(r"[0-9]{1,16}", value, re.ASCII) is not None, "invalid_arguments")
    number = int(value)
    require(low <= number <= high, "invalid_arguments")
    return number


def parse(words):
    """Validate syntax without contacting the player or loading a secret."""
    words = list(words)
    if words and words[0].casefold() == "torrent":
        words.pop(0)
    if words and words[0].casefold() == "indexer":
        words.pop(0)
    if not words:
        words = ["list"]
    op, tail = words[0].casefold(), words[1:]
    require(op in ("list", "configure", "remove", "caps", "search", "prepare"), "invalid_arguments")
    if op == "list":
        require(not tail, "invalid_arguments")
        return {"op": op}
    require(tail, "invalid_arguments")
    result = {"op": op, "sourceId": _pattern(tail.pop(0), SOURCE_ID, "invalid_arguments")}
    if op in ("remove", "caps"):
        require(not tail, "invalid_arguments")
        return result
    if op == "prepare":
        require(len(tail) == 1, "invalid_arguments")
        result["resultRef"] = _pattern(tail[0], REFERENCE, "invalid_arguments")
        return result
    if op == "configure":
        flags = {"--label": "label", "--endpoint": "endpoint", "--api-key-file": "key_file",
                 "--api-key-env": "key_env"}
        while tail:
            flag = tail.pop(0)
            require(flag in flags and tail and flags[flag] not in result, "invalid_arguments")
            result[flags[flag]] = tail.pop(0)
        require("label" in result and "endpoint" in result
                and ("key_file" in result) != ("key_env" in result), "invalid_arguments")
        _text(result["label"], 128, code="invalid_arguments")
        _endpoint(result["endpoint"])
        if "key_file" in result:
            _text(result["key_file"], 4096, code="invalid_arguments")
        else:
            _pattern(result["key_env"], ENV_NAME, "invalid_arguments")
        return result
    result.update(query="", categoryIds=[], offset=0, limit=50)
    positional, seen = [], set()
    flags = {"--query", "--category", "--offset", "--limit"}
    while tail:
        word = tail.pop(0)
        if word.startswith("--"):
            require(word in flags and word not in seen and tail, "invalid_arguments")
            seen.add(word)
            value = tail.pop(0)
            if word == "--query":
                result["query"] = value
            elif word == "--category":
                parts = value.split(",")
                require(0 < len(parts) <= 16, "invalid_arguments")
                result["categoryIds"] = [_number(part, 1, MAX_SAFE) for part in parts]
                require(len(set(result["categoryIds"])) == len(parts), "invalid_arguments")
            elif word == "--offset":
                result["offset"] = _number(value, 0, 1000000)
            else:
                result["limit"] = _number(value, 1, 100)
        else:
            positional.append(word)
    require(not positional or "--query" not in seen, "invalid_arguments")
    if positional:
        result["query"] = " ".join(positional)
    _text(result["query"], 512, empty=True, code="invalid_arguments")
    return result


def _key(args):
    if "key_env" in args:
        raw = os.environ.get(args["key_env"])
        require(raw is not None, "missing_api_key")
    else:
        path = Path(args["key_file"]).expanduser()
        try:
            require(not path.is_symlink(), "invalid_key_file")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_KEY + 2, "invalid_key_file")
                data = stream.read(MAX_KEY + 3)
                after = os.fstat(stream.fileno())
                require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "invalid_key_file")
            raw = data.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            raise IndexerError("invalid_key_file") from None
    if "key_file" in args and raw.endswith("\r\n"):
        raw = raw[:-2]
    elif "key_file" in args and raw.endswith("\n"):
        raw = raw[:-1]
    _text(raw, MAX_KEY, empty=True, code="invalid_api_key")
    return raw


def _category_ids(values, maximum=32):
    require(type(values) is list and len(values) <= maximum)
    seen = set()
    for item in values:
        _integer(item, 1)
        require(item not in seen)
        seen.add(item)


def _source(value):
    _shape(value, ("id", "label"))
    _pattern(value["id"], SOURCE_ID)
    _text(value["label"], 128)


def _timestamp(value):
    _text(value, 64)
    require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value) is not None)
    try:
        datetime.datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        raise IndexerError("invalid_response") from None


def validate_response(value, request):
    """Accept only the bounded public DTO bound to this request and runtime."""
    require(type(value) is dict and len(_canonical(value)) <= MAX_RESPONSE)
    require(type(value.get("version")) is int and value["version"] == 1
            and value.get("runtime") == request["runtime"] and value.get("op") == request["op"])
    common = {"version", "runtime", "op"}
    if "error" in value:
        _shape(value, common | {"error", "reason"})
        require(value["error"] == "Torrent indexer request failed." and type(value["reason"]) is str
                and value["reason"] in REASONS)
        raise IndexerError(value["reason"], rejected=True)
    require(type(value.get("schema")) is int and value["schema"] == 1)
    common.add("schema")
    op = request["op"]
    if op == "list":
        _shape(value, common | {"sources"})
        require(type(value["sources"]) is list and len(value["sources"]) <= 8)
        seen = set()
        for row in value["sources"]:
            _source(row)
            require(row["id"] not in seen)
            seen.add(row["id"])
    elif op == "configure":
        _shape(value, common | {"source"})
        _source(value["source"])
        require(value["source"]["id"] == request["sourceId"] and value["source"]["label"] == request["label"])
    elif op == "remove":
        _shape(value, common | {"removed"})
        require(value["removed"] is True)
    else:
        require(value.get("sourceId") == request["sourceId"])
        common.add("sourceId")
        if op == "caps":
            _shape(value, common | {"searchAvailable", "categories", "maxLimit"})
            require(type(value["searchAvailable"]) is bool)
            _integer(value["maxLimit"], 1, 100)
            require(type(value["categories"]) is list and len(value["categories"]) <= 512)
            seen = set()
            for row in value["categories"]:
                _shape(row, ("id", "name", "parentId"))
                identifier = _integer(row["id"], 1)
                require(identifier not in seen)
                seen.add(identifier)
                _text(row["name"], 128)
                if row["parentId"] is not None:
                    require(_integer(row["parentId"], 1) != identifier)
        elif op == "search":
            _shape(value, common | {"items", "nextOffset", "total"})
            require(type(value["items"]) is list and len(value["items"]) <= request["limit"])
            seen = set()
            for row in value["items"]:
                _shape(row, ("resultRef", "title", "size", "seeders", "publishedAt", "categoryIds", "languages"))
                reference = _pattern(row["resultRef"], REFERENCE)
                require(reference not in seen)
                seen.add(reference)
                _text(row["title"], 1024)
                for key in ("size", "seeders"):
                    if row[key] is not None:
                        _integer(row[key])
                if row["publishedAt"] is not None:
                    _timestamp(row["publishedAt"])
                _category_ids(row["categoryIds"])
                require(type(row["languages"]) is list and len(row["languages"]) <= 16)
                for language in row["languages"]:
                    _text(language, 32)
                    require(re.fullmatch(r"[A-Za-z0-9-]+", language, re.ASCII) is not None)
            if value["nextOffset"] is not None:
                _integer(value["nextOffset"], request["offset"] + 1, 1000000)
            if value["total"] is not None:
                _integer(value["total"])
        elif op == "prepare":
            _shape(value, common | {"sourceRef"})
            _pattern(value["sourceRef"], REFERENCE)
        else:
            raise IndexerError("invalid_response")
    return copy.deepcopy(value)


def _safe_text(value, secrets):
    clean = "".join(c for c in value if not unicodedata.category(c).startswith("C")).replace("\u2028", " ").replace("\u2029", " ")
    if (any(secret and secret in clean for secret in secrets)
            or "://" in clean or clean.lower().startswith(("magnet:", "ottplay-"))
            or clean.startswith(("/", "\\", "~/", "~\\")) or re.match(r"^[A-Za-z]:[/\\]", clean)):
        return "[private text]"
    return clean


def _public(value, secrets=()):
    value = copy.deepcopy(value)
    for row in value.get("sources", []):
        row["label"] = _safe_text(row["label"], secrets)
    if value["op"] == "configure":
        value["source"]["label"] = _safe_text(value["source"]["label"], secrets)
    for row in value.get("categories", []):
        row["name"] = _safe_text(row["name"], secrets)
    for row in value.get("items", []):
        row["title"] = _safe_text(row["title"], secrets)
    if value["op"] == "prepare":
        value["source"] = "ottplay-local-torrent:" + value["sourceId"] + "/" + value["sourceRef"]
    return {"status": "ok", **value}


class Session:
    def __init__(self, api, client, device, timeout, *, clock=time.monotonic, sleep=time.sleep):
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 1 <= timeout <= 300,
                "invalid_arguments")
        self.api, self.client, self.device = api, client, device
        self.clock, self.sleep = clock, sleep
        self.deadline, self.last, self.next_rpc = clock() + timeout, None, 0
        self.server, self.runtime, self.attempted = client.server, None, False

    def remaining(self):
        now = self.clock()
        require(math.isfinite(now) and (self.last is None or now >= self.last)
                and self.client.server == self.server, "context_changed")
        self.last = now
        require(now < self.deadline, "deadline_exceeded")
        return self.deadline - now

    def discover(self):
        self.client.timeout = self.remaining()
        raw = self.client.call(self.device, "capabilities", {})
        self.remaining()
        require(type(raw) is dict, "invalid_response")
        marker = raw.get("torrent_indexer")
        require(type(marker) is dict and set(marker) == {"version"}
                and type(marker["version"]) is int and marker["version"] == 1, "unsupported")
        controls = self.api.capabilities_metadata(raw)
        self.runtime = _pattern(controls["player"]["runtime"], RUNTIME)
        self.next_rpc = self.clock() + RPC_INTERVAL

    def rpc(self, args):
        request = {"version": 1, "runtime": self.runtime, **args}
        require(len(_canonical({"action": "torrent_indexer", "params": request})) + 128 <= 16384,
                "invalid_arguments")
        delay = self.next_rpc - self.clock()
        if delay > 0:
            require(self.remaining() > delay, "deadline_exceeded")
            self.sleep(delay)
        self.client.timeout = self.remaining()
        self.next_rpc = self.clock() + RPC_INTERVAL
        self.attempted = True
        try:
            raw = self.client.call(self.device, "torrent_indexer", request)
        except (self.api.PlayerRejected, self.api.PlayerUnsupported) as exc:
            validate_response(exc.data, request)
            raise IndexerError("invalid_response") from None
        except Exception:
            raise IndexerError("request_unconfirmed") from None
        self.remaining()
        return validate_response(raw, request)


def print_result(value):
    if value["status"] != "ok":
        print(value["error"])
        return
    op = value["op"]
    if op == "list":
        print("Torrent indexer sources: " + str(len(value["sources"])) + ".")
        for row in value["sources"]:
            print(row["id"] + "  " + row["label"])
    elif op == "configure":
        print("Torrent indexer configured: " + value["source"]["id"] + "  " + value["source"]["label"] + ".")
    elif op == "remove":
        print("Torrent indexer removed.")
    elif op == "caps":
        print("Torrent indexer: " + value["sourceId"] + ".")
        print("Search: " + ("available" if value["searchAvailable"] else "unavailable")
              + "; maximum page size: " + str(value["maxLimit"]) + ".")
        for row in value["categories"]:
            parent = "" if row["parentId"] is None else " (parent " + str(row["parentId"]) + ")"
            print(str(row["id"]) + "  " + row["name"] + parent)
    elif op == "search":
        print("Torrent indexer: " + value["sourceId"] + "; " + str(len(value["items"])) + " results.")
        for row in value["items"]:
            details = []
            if row["size"] is not None:
                details.append(str(row["size"]) + " bytes")
            if row["seeders"] is not None:
                details.append(str(row["seeders"]) + " seeders")
            details.extend(row["languages"])
            print(row["resultRef"] + "  " + row["title"] + ("  [" + ", ".join(details) + "]" if details else ""))
        if value["nextOffset"] is not None:
            print("Next offset: " + str(value["nextOffset"]) + ".")
        if value["total"] is not None:
            print("Total: " + str(value["total"]) + ".")
    else:
        print(value["source"])
        print("Prepared locally; use this source with torrent add or inspect. Playback has not started.")


def run(api, client, device, words, timeout, json_output, config_path=None):
    """One target with bounded capability discovery; no persistent secret state."""
    previous = client.timeout
    session, args = None, None
    try:
        args = parse(words)
        secrets = ()
        if args["op"] == "configure":
            key = _key(args)
            args = {name: value for name, value in args.items() if name not in ("key_file", "key_env")}
            args["apiKey"] = key
            secrets = (key, args["endpoint"])
        session = Session(api, client, device, timeout)
        session.discover()
        if args["op"] == "search":
            caps = session.rpc({"op": "caps", "sourceId": args["sourceId"]})
            require(not args["query"] or caps["searchAvailable"], "search_unavailable")
            require(args["limit"] <= caps["maxLimit"], "limit_unavailable")
            require(set(args["categoryIds"]) <= {row["id"] for row in caps["categories"]}, "category_unavailable")
        value, code = _public(session.rpc(args), secrets), 0
    except KeyboardInterrupt:
        value, code = {"status": "unknown", "error": "Interrupted; no automatic replay.", "replay_allowed": False}, 130
    except Exception as error:
        reason = error.code if isinstance(error, IndexerError) and error.code in REASONS | LOCAL_REASONS else "request_failed"
        uncertain = (session is not None and session.attempted and args["op"] in ("configure", "remove", "prepare")
                     and not (isinstance(error, IndexerError) and error.rejected))
        value = {"status": "unknown" if uncertain else "failed", "reason": reason,
                 "error": "Torrent indexer: " + reason.replace("_", " ") + ". No automatic replay.",
                 "replay_allowed": False}
        code = 2 if uncertain else 1
    finally:
        client.timeout = previous
    if json_output:
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    else:
        print_result(value)
    return code
