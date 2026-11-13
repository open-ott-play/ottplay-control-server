"""Explicit multi-view commands with window-bound source selection."""
import copy
import json
import math
import re
import time
import unicodedata


USAGE = ("Use mv [status], mv sources WINDOW [--provider ID] [--profile TOKEN] [--search TEXT] "
         "[--cursor TOKEN], mv layout LAYOUT [--satellites N], mv window N play TICKET, "
         "mv focus N, mv audio N|off|follows-focus, mv window N stop, or mv off [MODE]")
HELP = """ott PLAYER mv [status]
ott PLAYER mv sources WINDOW [--provider ID] [--profile TOKEN] [--search TEXT] [--cursor TOKEN]
ott PLAYER mv layout single|pip|grid4|grid9|perimeter [--satellites N]
ott PLAYER mv window N play TICKET
ott PLAYER mv focus N
ott PLAYER mv audio N|off|follows-focus
ott PLAYER mv window N stop
ott PLAYER mv off [promote_active|restore_previous]

Windows are numbered 1 through 9. Perimeter satellites range from 0 through 8 (default 8).
Profile tokens identify saved sources; they are not profile slot numbers.
Source tickets and cursors are opaque, case-sensitive values returned by the player.
Commands require advertised support and do not switch the global provider or profile.
"""
LAYOUTS = frozenset(("single", "pip", "grid4", "grid9", "perimeter"))
TOKEN = r"[A-Za-z0-9_-]{1,256}"
BINDING = r"[A-Za-z0-9_.:-]{1,64}"
PROVIDER = r"[a-z0-9][a-z0-9_.-]*(?:/[a-z0-9][a-z0-9_.-]*)?"
PRIVATE_QUERY = re.compile(
    r"""([a-z][a-z0-9+.-]*://|\b(https?|magnet|data|file):|(^|[\s"'(])(/\S|~/|[a-z]:[\\/]|\\\\)|\b(token|api[_-]?key|authorization|password|secret)\s*[=:])""",
    re.IGNORECASE | re.ASCII,
)


def token(value):
    return type(value) is str and re.fullmatch(TOKEN, value) is not None


def slot(value):
    """Convert human numbering exactly once; downstream code uses wire IDs."""
    if type(value) is not str or re.fullmatch(r"[1-9]", value) is None:
        raise ValueError(USAGE)
    return int(value) - 1


def search_text(value):
    if type(value) is not str:
        return False
    try:
        return (len(value.encode("utf-8")) <= 512
                and not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)
                and PRIVATE_QUERY.search(value) is None)
    except UnicodeError:
        return False


def provider_id(value):
    return (type(value) is str and 1 <= len(value) <= 64
            and re.fullmatch(PROVIDER, value) is not None)


def parse(words):
    if type(words) not in (list, tuple) or any(type(word) is not str for word in words):
        raise ValueError(USAGE)
    if not words or len(words) == 1 and words[0].casefold() == "status":
        return {"op": "status"}
    if list(words) in (["--help"], ["-h"]):
        return {"op": "help"}
    verb = words[0].casefold()
    if verb == "off" and len(words) <= 2:
        mode = words[1].casefold() if len(words) == 2 else "promote_active"
        if mode not in ("promote_active", "restore_previous"):
            raise ValueError(USAGE)
        return {"op": "off", "mode": mode}
    if verb == "focus" and len(words) == 2:
        return {"op": "focus", "slot_id": slot(words[1])}
    if verb == "audio" and len(words) == 2:
        policy = {"follows-focus": "follows_focus", "off": "muted"}.get(words[1].casefold())
        return ({"op": "audio", "policy": policy} if policy is not None
                else {"op": "audio", "policy": "slot", "slot_id": slot(words[1])})
    if verb == "window" and len(words) >= 3:
        target, operation = slot(words[1]), words[2].casefold()
        if operation in ("focus", "stop") and len(words) == 3:
            return {"op": operation, "slot_id": target}
        if operation == "audio" and len(words) == 3:
            return {"op": "audio", "policy": "slot", "slot_id": target}
        if operation == "play" and len(words) == 4 and token(words[3]):
            return {"op": "play", "slot_id": target, "source_ticket": words[3]}
        raise ValueError(USAGE)
    if verb == "layout" and len(words) >= 2 and words[1].casefold() in LAYOUTS:
        result = {"op": "layout", "layout": words[1].casefold()}
        if len(words) == 2:
            return dict(result, satellites=8) if result["layout"] == "perimeter" else result
        satellites = (words[2] if len(words) == 3 else
                      words[3] if len(words) == 4 and words[2] == "--satellites" else "")
        if result["layout"] == "perimeter" and re.fullmatch(r"[0-8]", satellites) is not None:
            return dict(result, satellites=int(satellites))
        raise ValueError(USAGE)
    if verb == "sources" and len(words) >= 2:
        result = {"op": "sources", "slot_id": slot(words[1])}
        tail = words[2:]
        if len(tail) % 2:
            raise ValueError(USAGE)
        for index in range(0, len(tail), 2):
            option, value = tail[index:index + 2]
            if option not in ("--provider", "--profile", "--search", "--cursor"):
                raise ValueError(USAGE)
            key = option[2:]
            output_key = "query" if key == "search" else key
            if output_key in result:
                raise ValueError(USAGE)
            if ((key == "provider" and not provider_id(value))
                    or (key == "profile" and re.fullmatch(BINDING, value) is None)
                    or (key == "search" and not search_text(value))
                    or (key == "cursor" and not token(value))):
                raise ValueError(USAGE)
            result[output_key] = value
        return result
    raise ValueError(USAGE)


OPERATIONS = frozenset(("status", "sources", "layout", "play", "focus", "audio", "stop", "off"))
AUDIO_POLICIES = frozenset(("follows_focus", "muted", "slot"))
OFF_MODES = frozenset(("promote_active", "restore_previous"))
SOURCE_KINDS = frozenset(("live", "vod", "file"))
SLOT_STATES = frozenset(("empty", "starting", "playing", "stalled", "ended", "failed", "stopped"))
CONTEXT = ("scene", "layout_revision", "focus_revision", "audio_revision")
SLOT_BINDING = ("slot_id", "slot_generation", "source_revision")
REASONS = frozenset(("invalid_request", "unsupported", "runtime_changed", "scene_changed", "layout_changed",
                    "focus_changed", "audio_changed", "slot_changed", "source_changed", "source_unavailable",
                    "source_expired", "policy_restricted", "limit_exceeded", "busy", "expired", "unavailable",
                    "receipt_unavailable"))
MAX_RESPONSE = 256 * 1024
FIXED_CAPACITY = {"off": 0, "single": 1, "pip": 2, "grid4": 4, "grid9": 9}
ERROR = "The player returned invalid multi-view metadata"


class InvalidResponse(ValueError):
    pass


def require(value):
    if not value:
        raise InvalidResponse(ERROR)


def exact(value, names):
    return type(value) is dict and set(value) == set(names)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def enum(value, allowed):
    return type(value) is str and value in allowed


def binding_token(value):
    return type(value) is str and re.fullmatch(BINDING, value) is not None


def context_valid(value):
    return exact(value, CONTEXT) and all(binding_token(value[key]) for key in CONTEXT)


def enum_list(value, allowed):
    return (type(value) is list and all(enum(item, allowed) for item in value)
            and len(set(value)) == len(value))


def capability(value):
    try:
        require(exact(value, ("version", "operations", "layouts", "max_slots", "source_kinds",
                              "audio_policies", "off_modes")))
        require(type(value["version"]) is int and value["version"] == 1
                and integer(value["max_slots"], 0, 9))
        for key, allowed in (("operations", OPERATIONS), ("layouts", LAYOUTS),
                             ("source_kinds", SOURCE_KINDS), ("audio_policies", AUDIO_POLICIES),
                             ("off_modes", OFF_MODES)):
            require(enum_list(value[key], allowed))
        require("status" in value["operations"])
        if value["max_slots"] == 0:
            require(value["operations"] == ["status"]
                    and not any(value[key] for key in ("layouts", "source_kinds", "audio_policies", "off_modes")))
        else:
            require(all(FIXED_CAPACITY.get(layout, 1) <= value["max_slots"] for layout in value["layouts"]))
            for operation, field in (("layout", "layouts"), ("play", "source_kinds"),
                                     ("sources", "source_kinds"), ("audio", "audio_policies"), ("off", "off_modes")):
                require(operation not in value["operations"] or bool(value[field]))
        return copy.deepcopy(value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise InvalidResponse("Invalid multi-view capability") from None


def request_valid(request):
    if (type(request) is not dict or type(request.get("version")) is not int
            or request["version"] != 1 or not enum(request.get("op"), OPERATIONS)):
        return False
    op = request["op"]
    fields = {"version", "op"}
    if op == "status":
        if "runtime" in request:
            fields.add("runtime")
            if not binding_token(request["runtime"]):
                return False
        return set(request) == fields
    fields.update(("runtime", "expected"))
    if not binding_token(request.get("runtime")) or not context_valid(request.get("expected")):
        return False
    if op in ("sources", "play", "focus", "stop") or op == "audio" and request.get("policy") == "slot":
        fields.update(SLOT_BINDING)
        if (not integer(request.get("slot_id"), 0, 8)
                or not binding_token(request.get("slot_generation"))
                or not binding_token(request.get("source_revision"))):
            return False
    if op == "sources":
        for key, valid in (("provider", provider_id), ("profile", binding_token),
                           ("query", search_text), ("cursor", token)):
            if key in request:
                fields.add(key)
                if not valid(request[key]):
                    return False
    elif op == "play":
        fields.add("source_ticket")
        if not token(request.get("source_ticket")):
            return False
    elif op == "layout":
        fields.add("layout")
        if not enum(request.get("layout"), LAYOUTS):
            return False
        if request["layout"] == "perimeter":
            fields.add("satellites")
            if not integer(request.get("satellites"), 0, 8):
                return False
    elif op == "audio":
        fields.add("policy")
        if not enum(request.get("policy"), AUDIO_POLICIES):
            return False
    elif op == "off":
        fields.add("mode")
        if not enum(request.get("mode"), OFF_MODES):
            return False
    return set(request) == fields


def source_valid(value, ticket=False):
    fields = ("source_id", "label", "provider", "profile", "kind") + (("ticket",) if ticket else ())
    return (exact(value, fields) and binding_token(value["source_id"])
            and search_text(value["label"]) and bool(value["label"].strip())
            and provider_id(value["provider"]) and binding_token(value["profile"])
            and enum(value["kind"], SOURCE_KINDS) and (not ticket or token(value["ticket"])))


def snapshot_valid(value):
    if (not exact(value, ("context", "layout", "satellites", "focus_slot", "audio_policy", "audio_slot", "slots"))
            or not context_valid(value["context"]) or not enum(value["layout"], LAYOUTS | {"off"})
            or not integer(value["satellites"], 0, 8) or not enum(value["audio_policy"], AUDIO_POLICIES)
            or type(value["slots"]) is not list or len(value["slots"]) > 9):
        return False
    count = value["satellites"] + 1 if value["layout"] == "perimeter" else FIXED_CAPACITY[value["layout"]]
    if len(value["slots"]) != count or value["layout"] != "perimeter" and value["satellites"] != 0:
        return False
    ids = []
    for row in value["slots"]:
        if (not exact(row, (*SLOT_BINDING, "state", "source", "error"))
                or not integer(row["slot_id"], 0, 8) or not binding_token(row["slot_generation"])
                or not binding_token(row["source_revision"]) or not enum(row["state"], SLOT_STATES)
                or row["source"] is not None and not source_valid(row["source"])
                or row["state"] in ("starting", "playing", "stalled") and row["source"] is None
                or row["error"] is not None and not enum(row["error"], REASONS)
                or row["state"] == "failed" and row["error"] is None):
            return False
        ids.append(row["slot_id"])
    if sorted(ids) != list(range(count)):
        return False
    focus, audio = value["focus_slot"], value["audio_slot"]
    if value["layout"] == "off":
        return focus is None and audio is None and value["audio_policy"] == "muted"
    if not integer(focus, 0, count - 1) or audio is not None and not integer(audio, 0, count - 1):
        return False
    if value["audio_policy"] == "muted":
        return audio is None
    if value["audio_policy"] == "follows_focus":
        return audio is None or audio == focus
    return audio is not None


def strict_values(value):
    if type(value) is dict:
        for key, item in value.items():
            if key in ("version", "slot_id", "satellites", "max_slots", "focus_slot", "audio_slot") and item is not None:
                require(type(item) is int)
            strict_values(key)
            strict_values(item)
    elif type(value) is list:
        for item in value:
            strict_values(item)
    elif type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is float:
        require(math.isfinite(value))


def metadata(raw, request, status="ok"):
    try:
        require(request_valid(request))
        strict_values(raw)
        require(len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= MAX_RESPONSE)
        require(type(raw) is dict and type(raw.get("version")) is int and raw["version"] == 1
                and binding_token(raw.get("runtime")) and raw.get("op") == request["op"])
        require("runtime" not in request or raw["runtime"] == request["runtime"])
        binding = {key: value for key, value in request.items() if key not in ("version", "runtime", "op")}
        require(raw.get("binding") == binding)
        common = ("version", "runtime", "op", "binding")
        if status in ("rejected", "unsupported"):
            require(exact(raw, (*common, "reason")) and enum(raw.get("reason"), REASONS)
                    and (raw["reason"] == "unsupported") == (status == "unsupported"))
        elif status == "ok":
            if request["op"] == "status":
                require(exact(raw, (*common, "snapshot")) and snapshot_valid(raw.get("snapshot")))
            elif request["op"] == "sources":
                require(exact(raw, (*common, "items", "cursor", "complete"))
                        and type(raw["items"]) is list and len(raw["items"]) <= 128
                        and type(raw["complete"]) is bool)
                require((raw["cursor"] is None) if raw["complete"] else token(raw["cursor"]))
                require(all(source_valid(row, ticket=True) for row in raw["items"]))
                require(len({row["source_id"] for row in raw["items"]}) == len(raw["items"])
                        and len({row["ticket"] for row in raw["items"]}) == len(raw["items"]))
            else:
                require(exact(raw, (*common, "accepted", "outcome")) and raw["accepted"] is True
                        and enum(raw["outcome"], ("requested", "applied")))
        else:
            raise InvalidResponse(ERROR)
        return copy.deepcopy(raw)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise InvalidResponse(ERROR) from None


def decode(raw):
    try:
        require(type(raw) is bytes and len(raw) <= MAX_RESPONSE)
        def pairs(values):
            result = {}
            for key, value in values:
                require(key not in result)
                result[key] = value
            return result
        def parse_int(value):
            require(re.fullmatch(r"0|-?[1-9][0-9]*", value) is not None)
            return int(value)
        def invalid_constant(value):
            raise InvalidResponse(ERROR)
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                           parse_int=parse_int, parse_constant=invalid_constant)
        strict_values(value)
        return value
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise InvalidResponse(ERROR) from None


def decode_response(api, raw):
    try:
        return decode(raw)
    except InvalidResponse:
        raise api.Error(ERROR) from None


def envelope(raw, request_id):
    result = decode(raw)
    require(exact(result, ("id", "status", "data")) and result["id"] == request_id
            and type(result["id"]) is str and re.fullmatch(r"[0-9a-f]{32}", result["id"]) is not None
            and enum(result["status"], ("ok", "rejected", "unsupported")) and type(result["data"]) is dict)
    return result


def receipt(api, raw, params, request_id):
    try:
        result = envelope(raw, request_id)
        result["data"] = metadata(result["data"], params, result["status"])
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise api.Error(ERROR) from None


def capabilities_receipt(api, raw, request_id):
    try:
        result = envelope(raw, request_id)
        if result["status"] == "ok" and "multiview" in result["data"]:
            capability(result["data"]["multiview"])
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise api.Error("Invalid multi-view capability receipt") from None


def render(data):
    if data["op"] == "status":
        value = data["snapshot"]
        focus = "none" if value["focus_slot"] is None else str(value["focus_slot"] + 1)
        audio = "none" if value["audio_slot"] is None else str(value["audio_slot"] + 1)
        print(f"Multi-view: {value['layout']}; focus window {focus}; audio {value['audio_policy']} (window {audio}).")
        for row in sorted(value["slots"], key=lambda item: item["slot_id"]):
            label = row["source"]["label"] if row["source"] is not None else "No source"
            error = "; reason " + row["error"] if row["error"] else ""
            print(f"  Window {row['slot_id'] + 1}: {label}; player reports {row['state']}{error}")
        print("Reported state does not establish visible playback.")
    elif data["op"] == "sources":
        for row in data["items"]:
            print(f"{row['ticket']}  {row['label']} ({row['provider']}, profile {row['profile']}, {row['kind']})")
        if not data["items"]:
            print("No source choices in this page.")
        if not data["complete"]:
            print("More sources: --cursor " + data["cursor"])
    else:
        print(f"Multi-view {data['op']}: {data['outcome']}. Visible playback has not been confirmed.")


def run(api, client, device, words, timeout, json_output):
    try:
        command = parse(words)
    except ValueError:
        raise api.Error(USAGE) from None
    if command["op"] == "help":
        print(HELP, end="")
        return 0
    previous_timeout = client.timeout
    deadline = time.monotonic() + timeout
    def remaining():
        cancelled = getattr(client, "group_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt
        value = deadline - time.monotonic()
        if value <= 0:
            raise api.Error("Multi-view deadline expired. No request was repeated")
        client.timeout = min(previous_timeout, value)

    def call(request):
        remaining()
        require(request_valid(request))
        # Reserve transport ID/expiry overhead before sending any request.
        size = len(json.dumps({"action": "multiview", "params": request}, ensure_ascii=True).encode("ascii")) + 256
        if size > 16384:
            raise api.Error("Multi-view request exceeds the size limit; no request was sent")
        status = "ok"
        try:
            raw = client.call(device, "multiview", request)
        except (api.PlayerRejected, api.PlayerUnsupported) as exc:
            raw = exc.data
            status = "unsupported" if isinstance(exc, api.PlayerUnsupported) else "rejected"
        try:
            data = metadata(raw, request, status)
        except InvalidResponse:
            suffix = (" No change was sent." if request["op"] in ("status", "sources") else
                      " A sent change may have executed; no request was repeated.")
            raise api.Error(ERROR + "." + suffix) from None
        if status != "ok":
            raise api.Error("Multi-view request was not accepted: " + data["reason"] + ". No request was repeated")
        return data

    try:
        remaining()
        try:
            raw_caps = client.call(device, "capabilities", {"multiview": True})
            caps = api.capabilities_metadata(raw_caps)
            marker = capability(caps.get("multiview"))
            require(binding_token(caps["player"]["runtime"]))
        except (api.PlayerRejected, api.PlayerUnsupported, api.HTTPError, InvalidResponse, KeyError):
            raise api.Error("Multi-view requires a compatible player and controller. No multi-view request was sent") from None
        if command["op"] not in marker["operations"]:
            raise api.Error("This multi-view operation is not advertised. No multi-view request was sent")
        if "slot_id" in command and command["slot_id"] >= marker["max_slots"]:
            raise api.Error("This window exceeds the advertised multi-view capacity. No multi-view request was sent")
        if command["op"] == "layout":
            count = command["satellites"] + 1 if command["layout"] == "perimeter" else FIXED_CAPACITY[command["layout"]]
            if command["layout"] not in marker["layouts"] or count > marker["max_slots"]:
                raise api.Error("This layout exceeds advertised multi-view support. No multi-view request was sent")
        if command["op"] == "audio" and command["policy"] not in marker["audio_policies"]:
            raise api.Error("This audio policy is not advertised. No multi-view request was sent")
        if command["op"] == "off" and command["mode"] not in marker["off_modes"]:
            raise api.Error("This exit mode is not advertised. No multi-view request was sent")
        runtime = caps["player"]["runtime"]
        data = call({"version": 1, "runtime": runtime, "op": "status"})
        if command["op"] != "status":
            snapshot = data["snapshot"]
            request = dict(command, version=1, runtime=runtime, expected=copy.deepcopy(snapshot["context"]))
            if "slot_id" in command:
                row = next((row for row in snapshot["slots"] if row["slot_id"] == command["slot_id"]), None)
                if row is None:
                    raise api.Error("This window is not in the current layout. No change was sent")
                request.update(slot_generation=row["slot_generation"], source_revision=row["source_revision"])
            data = call(request)
        if json_output:
            print(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2))
        else:
            render(data)
        return 0
    finally:
        client.timeout = previous_timeout
