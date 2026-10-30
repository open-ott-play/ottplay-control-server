"""Bounded playback reports; retained reports never establish current playback."""
import calendar
import copy
import datetime
import json
import math
import re
import time
import unicodedata
import urllib.parse


MAX_SAFE = 9007199254740991
MAX_BYTES = 256 * 1024
STATES = frozenset("unknown idle loading playing paused buffering ended error standby".split())
KINDS = frozenset("none live archive vod unknown".split())
FIELDS = frozenset("state kind title provider media_id channel_number position duration volume".split())
LABEL = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
RUNTIME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z")


class InvalidReport(ValueError):
    """Only fixed, public errors cross the CLI boundary."""


def require(value):
    if not value:
        raise InvalidReport("Invalid playback report")


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields))


def integer(value, minimum=0, maximum=MAX_SAFE):
    require(type(value) is int and minimum <= value <= maximum)


def text(value, maximum):
    require(type(value) is str and 0 < len(value) <= maximum)
    try:
        require(len(value.encode("utf-8")) <= maximum)
    except UnicodeError:
        raise InvalidReport("Invalid playback report") from None
    require(value == value.strip() and not any(unicodedata.category(char) == "Cc" for char in value))


def private_label(value):
    # Classify the visible label, not an obscured spelling of a private path or
    # URL. The same predicate protects JSON projection and terminal rendering.
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C")).strip()
    return ("://" in value or value.startswith(("/", "\\", "~/", "~\\"))
            or re.match(r"^[A-Za-z]:[/\\]", value) is not None)


def public_playback(value):
    result = copy.deepcopy(value)
    for key in ("title", "provider"):
        if result[key] is not None and private_label(result[key]):
            result[key] = None
    return result


def timestamp(value):
    require(type(value) is str and len(value) <= 35)
    match = STAMP.fullmatch(value)
    require(match is not None)
    try:
        date = datetime.datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S")
        offset = match[3]
        seconds = 0
        if offset != "Z":
            hours, minutes = int(offset[1:3]), int(offset[4:6])
            require(hours < 24 and minutes < 60)
            seconds = (hours * 60 + minutes) * 60 * (1 if offset[0] == "+" else -1)
        return (calendar.timegm(date.timetuple()) - seconds) * 1000000000 + int((match[2] or "").ljust(9, "0"))
    except (ValueError, OverflowError):
        raise InvalidReport("Invalid playback report") from None


def validate_playback(value, standby):
    exact(value, FIELDS)
    require(type(standby) is bool)
    require(type(value["state"]) is str and value["state"] in STATES)
    require(type(value["kind"]) is str and value["kind"] in KINDS)
    require((value["state"] == "standby") == standby)
    for key, maximum in (("title", 256), ("provider", 64)):
        if value[key] is not None:
            text(value[key], maximum)
    if value["media_id"] is not None:
        require(type(value["media_id"]) is str and LABEL.fullmatch(value["media_id"]) is not None)
    if value["channel_number"] is not None:
        integer(value["channel_number"], 1, 2147483647)
    for key in ("position", "duration", "volume"):
        number = value[key]
        if number is not None:
            require(type(number) in (int, float) and 0 <= number <= (100 if key == "volume" else MAX_SAFE))
            require(math.isfinite(number))
    if value["state"] in ("idle", "ended", "standby"):
        require(all(value[key] is None for key in ("title", "media_id", "channel_number")))
    return public_playback(value)


def live_status(data):
    """An invalid additive report does not discard valid legacy status fields."""
    result = dict(data)
    result.pop("playback_error", None)
    if "playback" not in result:
        return result
    try:
        player = result.get("player")
        require(type(player) is dict and type(player.get("runtime")) is str
                and RUNTIME.fullmatch(player["runtime"]) is not None)
        result["playback"] = validate_playback(result["playback"], player.get("standby"))
    except InvalidReport:
        result.pop("playback", None)
        result["playback_error"] = "Playback details were not valid; current content is not reported."
    return result


def description(playback):
    state = playback["state"]
    if state == "standby":
        return "Nothing (standby)"
    if state in ("idle", "ended"):
        return "Nothing (" + state + ")"
    title = display_text(playback["title"]) if playback["title"] is not None else "Content not reported"
    return title + " (" + state + ")"


def display_text(value):
    if private_label(value):
        return "Content not reported"
    return " ".join("".join(char if not unicodedata.category(char).startswith("C") else " "
                            for char in value).split()) or "Content not reported"


def parse(words):
    verb = words[0].casefold() if words else ""
    if verb == "state" and len(words) == 1:
        return {"limit": 0}
    if verb == "history":
        if len(words) == 1:
            return {"limit": 20}
        if (len(words) == 3 and words[1] == "--limit" and len(words[2]) <= 2
                and re.fullmatch(r"[1-9][0-9]?", words[2]) and 1 <= int(words[2]) <= 64):
            return {"limit": int(words[2])}
    raise InvalidReport("Use state or history [--limit 1..64] to read retained controller reports")


def player_metadata(value):
    exact(value, ("version", "platform", "standby"))
    text(value["version"], 64)
    text(value["platform"], 32)
    require(not private_label(value["version"]) and not private_label(value["platform"]))
    require(type(value["standby"]) is bool)


def report_row(value, event=False):
    common = {"runtime", "sequence", "received_at", "player", "playback"}
    exact(value, common | ({"event"} if event else {"age_ms", "stale", "restored"}))
    require(type(value["runtime"]) is str and RUNTIME.fullmatch(value["runtime"]) is not None)
    integer(value["sequence"], 1)
    timestamp(value["received_at"])
    player_metadata(value["player"])
    validate_playback(value["playback"], value["player"]["standby"])
    if event:
        require(type(value["event"]) is str and value["event"] in ("connected", "reconnected", "changed"))
    else:
        integer(value["age_ms"])
        require(type(value["stale"]) is bool and type(value["restored"]) is bool)
        require(not value["restored"] or value["stale"])


def decode(raw):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    def constant(_):
        raise InvalidReport("Invalid playback report")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidReport("Invalid playback report") from None


def validate_response(value, device, limit):
    exact(value, ("version", "controller_epoch", "device_id", "server_time", "storage", "stale_after_ms",
                  "retention_seconds", "connection", "ambiguous", "current", "latest", "runtimes", "history",
                  "history_truncated"))
    require(type(value["version"]) is int and value["version"] == 1)
    require(type(value["controller_epoch"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["controller_epoch"]))
    require(value["device_id"] == device and type(value["device_id"]) is str)
    server_time = timestamp(value["server_time"])
    require(value["storage"] in ("memory", "persistent"))
    require(type(value["stale_after_ms"]) is int and value["stale_after_ms"] == 45000)
    require(type(value["retention_seconds"]) is int and value["retention_seconds"] == 86400)
    exact(value["connection"], ("state", "last_seen_at"))
    require(value["connection"]["state"] in ("online", "offline", "unknown"))
    seen = value["connection"]["last_seen_at"]
    expected_connection = "unknown"
    if seen is not None:
        since_seen = server_time - timestamp(seen)
        expected_connection = "online" if 0 <= since_seen < 45000 * 1000000 else "offline"
    require(value["connection"]["state"] == expected_connection)
    require(type(value["ambiguous"]) is bool and type(value["history_truncated"]) is bool)
    rows, history = value["runtimes"], value["history"]
    require(type(rows) is list and len(rows) <= 4)
    require(type(history) is list and len(history) <= limit)
    for row in rows:
        report_row(row)
        age = server_time - timestamp(row["received_at"])
        require(row["age_ms"] == max(0, age // 1000000))
        require(row["stale"] == (row["restored"] or age < 0 or row["age_ms"] >= 45000))
    for event in history:
        report_row(event, event=True)
    require(len({row["runtime"] for row in rows}) == len(rows))
    require(rows == sorted(rows, key=lambda row: (-timestamp(row["received_at"]), row["runtime"])))
    require(all(timestamp(first["received_at"]) >= timestamp(second["received_at"])
                for first, second in zip(history, history[1:])))
    for key in ("current", "latest"):
        if value[key] is not None:
            report_row(value[key])
            require(value[key] in rows)
    fresh = [row for row in rows if not row["stale"]]
    require(value["ambiguous"] == (len(fresh) > 1))
    require(value["current"] == (fresh[0] if len(fresh) == 1 else None))
    require(value["latest"] == (rows[0] if rows else None))
    result = copy.deepcopy(value)
    for row in [result["current"], result["latest"], *result["runtimes"], *result["history"]]:
        if row is not None:
            row["playback"] = public_playback(row["playback"])
    return result


def read(api, client, device, params, timeout):
    deadline = time.monotonic() + timeout
    path = "/api/player-state?" + urllib.parse.urlencode({"device_id": device, "limit": params["limit"]})
    try:
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        status, raw = client.api_raw(path, timeout=remaining)
        require(time.monotonic() < deadline and status == 200)
        result = validate_response(decode(raw), device, params["limit"])
        require(time.monotonic() < deadline)
        return result
    except api.HTTPError as error:
        if error.code == 404:
            raise api.Error("Retained playback reports are unavailable; update the controller to support state/history") from None
        if error.code in (401, 403):
            raise api.Error("Retained playback reports require status permission for this player") from None
        raise api.Error("The controller could not return retained playback reports") from None
    except (api.Error, InvalidReport, TypeError, KeyError, OverflowError, RecursionError):
        raise api.Error("Retained playback reports could not be read or validated. No player request was sent") from None


def print_report(value, name, history=False):
    safe_name = " ".join(char if char.isprintable() else " " for char in name)[:160]
    print("Player: " + safe_name)
    print("Source: controller-retained reports (" + value["storage"] + "); not a live player read")
    print("Connection: " + value["connection"]["state"] + " (controller observation)")
    if value["ambiguous"]:
        print("Current report: ambiguous; multiple runtimes have fresh reports")
    latest = value["latest"]
    if latest is None:
        print("Last reported: Not reported")
    else:
        freshness = "stale" if latest["stale"] else "fresh report"
        if latest["restored"]:
            freshness += ", restored"
        print("Last reported: " + description(latest["playback"]))
        print(f"Received: {latest['received_at']} ({latest['age_ms'] / 1000:g}s ago; {freshness})")
    if history:
        print("History:")
        for event in value["history"]:
            print("  " + event["received_at"] + "  " + event["event"] + "  " + description(event["playback"]))
        if not value["history"]:
            print("  No retained events")
        if value["history_truncated"]:
            print("  Older retained events omitted; increase --limit up to 64")
    print("Reports do not prove physical screen or sound output.")
