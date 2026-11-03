"""Current-playback track selection with source-bound receipts and no replay."""
import copy
import json
import re
import time
import unicodedata


USAGE = "Use tracks [list], tracks audio ID, or tracks subtitle ID|off; use id:off for a track literally named off"
HELP = """ott PLAYER tracks [list]
ott PLAYER tracks audio ID
ott PLAYER tracks subtitle ID|off

IDs are opaque, case-sensitive handles from the current track list, not indices.
Use id:off to select a subtitle track whose ID is literally off.
Selection affects current playback only. It does not save language preferences.
Requested selection is distinct from independently confirmed backend selection.
"""
ID = r"[A-Za-z0-9_-]{1,64}"
RUNTIME = r"[a-z0-9-]{1,64}"
BINDINGS = ("playback", "source_generation", "catalog")
PRIVATE_LABEL = re.compile(
    r"""([a-z][a-z0-9+.-]*://|\b(https?|magnet|data|file):|(^|[\s"'(])(/\S|~/|[a-z]:[\\/]|\\\\)|\b(token|api[_-]?key|authorization|password|secret)\s*[=:])""",
    re.IGNORECASE | re.ASCII,
)
ERRORS = {
    "invalid_request": "The player rejected the track request",
    "runtime_mismatch": "The player runtime changed",
    "no_playback": "No current playback is available",
    "playback_changed": "Playback or its source changed",
    "catalog_changed": "The track catalog changed or expired; list tracks again",
    "catalog_incomplete": "A complete current track catalog is not available",
    "track_not_found": "The requested track is not in the current catalog",
    "not_selectable": "This track cannot be selected temporarily on the current route",
    "restricted": "Current player policy prevents track selection",
    "unsupported": "The current player or playback route does not support media tracks",
    "busy": "The current playback route is busy",
    "unavailable": "Current track information or selection is unavailable",
}


class InvalidResponse(ValueError):
    pass


def keys(value, expected):
    return isinstance(value, dict) and set(value) == set(expected)


def token(value, pattern=ID):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def parse(words):
    if not words or [word.casefold() for word in words] == ["list"]:
        return {"op": "list"}
    if words in (["--help"], ["-h"]):
        return {"op": "help"}
    if len(words) != 2 or words[0].casefold() not in ("audio", "subtitle", "subtitles"):
        raise ValueError(USAGE)
    kind = "audio" if words[0].casefold() == "audio" else "subtitle"
    value = words[1]
    if kind == "subtitle" and value.casefold() == "off":
        target = {"kind": "subtitle", "mode": "off"}
    else:
        value = value[3:] if value.startswith("id:") else value
        if not token(value):
            raise ValueError(USAGE)
        target = {"kind": kind, "mode": "track", "id": value}
    return {"op": "select", "target": target}


def target_valid(target):
    if keys(target, ("kind", "mode")):
        return target == {"kind": "subtitle", "mode": "off"}
    return (keys(target, ("kind", "mode", "id")) and target["kind"] in ("audio", "subtitle")
            and target["mode"] == "track" and token(target["id"]))


def label_valid(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 128:
        return False
    return (not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)
            and PRIVATE_LABEL.search(value) is None)


def observation_valid(value, kind, rows=None):
    if not keys(value, ("state", "track_id", "age_ms")):
        return False
    state, identity, age = value["state"], value["track_id"], value["age_ms"]
    if state == "not_observed":
        return identity is None and age is None
    if type(age) is not int or not 0 <= age <= 9007199254740991:
        return False
    if state == "off":
        return kind == "subtitle" and identity is None
    return (state == "track" and token(identity)
            and (rows is None or rows.get(identity) == kind))


def _metadata(raw, request, status):
    if (len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65536
            or not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1
            or raw.get("runtime") != request["runtime"] or not token(raw["runtime"], RUNTIME)
            or raw.get("op") != request["op"] or request["op"] not in ("list", "select")):
        raise InvalidResponse()
    common = ("version", "runtime", "op")
    if request["op"] == "select":
        if (not target_valid(request.get("target"))
                or any(not token(request.get(key)) or raw.get(key) != request[key] for key in BINDINGS)
                or raw.get("target") != request["target"]):
            raise InvalidResponse()
    if status in ("rejected", "unsupported"):
        fields = (*common, "state", "error")
        if request["op"] == "select":
            fields += (*BINDINGS, "target")
        if (not keys(raw, fields) or raw["state"] != "error" or raw.get("error") not in ERRORS
                or (raw["error"] == "unsupported") != (status == "unsupported")):
            raise InvalidResponse()
        return copy.deepcopy(raw)
    if status != "ok" or raw.get("scope") != "current_playback" or any(not token(raw.get(key)) for key in BINDINGS):
        raise InvalidResponse()
    if request["op"] == "list":
        if (not keys(raw, (*common, "scope", *BINDINGS, "tracks", "subtitle_off_supported", "observed"))
                or not isinstance(raw["tracks"], list) or len(raw["tracks"]) > 128
                or type(raw["subtitle_off_supported"]) is not bool
                or not keys(raw["observed"], ("audio", "subtitle"))):
            raise InvalidResponse()
        identities, counts = {}, {"audio": 0, "subtitle": 0}
        for row in raw["tracks"]:
            if (not keys(row, ("id", "kind", "label", "language", "codec", "selectable"))
                    or not token(row["id"]) or row["id"] in identities or row["kind"] not in counts
                    or not label_valid(row["label"]) or type(row["selectable"]) is not bool
                    or (row["language"] is not None and not token(row["language"], r"[A-Za-z0-9-]{1,32}"))
                    or (row["codec"] is not None and not token(row["codec"], r"[A-Za-z0-9_.-]{1,32}"))):
                raise InvalidResponse()
            identities[row["id"]] = row["kind"]
            counts[row["kind"]] += 1
        if (any(count > 64 for count in counts.values())
                or any(not observation_valid(raw["observed"][kind], kind, identities) for kind in counts)):
            raise InvalidResponse()
    else:
        if (not keys(raw, (*common, "scope", *BINDINGS, "target", "accepted", "outcome", "observed"))
                or raw["accepted"] is not True or raw["outcome"] not in ("requested", "confirmed")):
            raise InvalidResponse()
        if raw["outcome"] == "requested":
            if raw["observed"] is not None:
                raise InvalidResponse()
        else:
            target, observed = request["target"], raw["observed"]
            if (not observation_valid(observed, target["kind"]) or observed["state"] != target["mode"]
                    or observed["track_id"] != target.get("id")):
                raise InvalidResponse()
    return copy.deepcopy(raw)


def metadata(raw, request, status="ok"):
    try:
        return _metadata(raw, request, status)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise InvalidResponse("The player returned invalid media track metadata") from None


def render(data):
    if data["op"] == "select":
        print("Backend selection confirmed." if data["outcome"] == "confirmed" else
              "Selection requested; backend selection has not been confirmed.")
        return
    for kind in ("audio", "subtitle"):
        observed = data["observed"][kind]
        state = "NOT OBSERVED" if observed["state"] == "not_observed" else observed["track_id"] or "off"
        print(("Audio" if kind == "audio" else "Subtitles") + ": " + state)
    if not data["tracks"]:
        print("No tracks reported for current playback.")
    for row in data["tracks"]:
        suffix = "" if row["selectable"] else " (not selectable)"
        print(f"{row['kind']} {row['id']}: {row['label']}{suffix}")
    if data["subtitle_off_supported"]:
        print("Subtitles can be disabled with tracks subtitle off.")


def run(api, client, device, words, timeout, json_output):
    try:
        command = parse(words)
    except ValueError:
        raise api.Error(USAGE) from None
    if command["op"] == "help":
        print(HELP, end="")
        return 0
    deadline = time.monotonic() + timeout
    previous_timeout = client.timeout

    def remaining():
        cancelled = getattr(client, "group_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt
        value = deadline - time.monotonic()
        if value <= 0:
            raise api.Error("Media tracks deadline expired. A sent request may have executed; no request was repeated")
        client.timeout = value

    def call(request):
        remaining()
        status = "ok"
        try:
            raw = client.call(device, "media_tracks", request)
        except (api.PlayerRejected, api.PlayerUnsupported) as exc:
            raw = exc.data
            status = "unsupported" if isinstance(exc, api.PlayerUnsupported) else "rejected"
        try:
            data = metadata(raw, request, status)
        except InvalidResponse:
            detail = ("No selection was sent" if request["op"] == "list" else
                      "A sent selection may have executed; no request was repeated")
            raise api.Error("The player returned invalid media track metadata. " + detail) from None
        if status != "ok":
            raise api.Error(ERRORS[data["error"]] + ". No request was repeated")
        # Preserve a completed, validated reply even if cancellation or the
        # deadline arrives while processing it. Any next RPC checks remaining().
        return data

    try:
        remaining()
        try:
            caps = api.capabilities_metadata(client.call(device, "capabilities", {}))
        except (api.PlayerUnsupported, api.PlayerRejected):
            raise api.Error("Media tracks require a player advertising media_tracks version 1. No selection was sent") from None
        if "media_tracks" not in caps:
            raise api.Error("Media tracks require a player advertising media_tracks version 1. No selection was sent")
        request = {"op": "list", "runtime": caps["player"]["runtime"]}
        data = call(request)
        if command["op"] == "select":
            target = command["target"]
            if target["mode"] == "off":
                if not data["subtitle_off_supported"]:
                    raise api.Error("Subtitle off is not supported for the current playback. No selection was sent")
            else:
                row = next((row for row in data["tracks"] if row["id"] == target["id"] and row["kind"] == target["kind"]), None)
                if row is None:
                    raise api.Error("The requested track ID is not in the current catalog. No selection was sent")
                if not row["selectable"]:
                    raise api.Error("This track is not selectable temporarily on the current route. No selection was sent")
            request = {"op": "select", "runtime": request["runtime"],
                       **{key: data[key] for key in BINDINGS}, "target": target}
            data = call(request)
        if json_output:
            print(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2))
        else:
            render(data)
        return 0
    finally:
        client.timeout = previous_timeout
