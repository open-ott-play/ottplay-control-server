"""Passive controller ingress metadata and opt-in exact native request correlation."""
import argparse
import copy
import json
import re
import time
import urllib.parse

MAX_INTEGER = 9007199254740991
MAX_AUDIT_BYTES = 32768
MAX_ACTIVITY_BYTES = 8192
RETENTION = {"max_entries_per_device": 32, "max_entries_total": 512, "max_age_ms": 600000}
ACTION = "command_activity_diagnostics"
FAMILIES = frozenset("play play_catalog playback restart provider profile input command local_media plex_queue plex_library lifecycle kiosk".split())
OPERATIONS = {
    "command": "popup_message channel_by_number channel_by_name random_channel change_provider change_provider_settings change_playlist set_volume exit_player",
    "input": "up down left right ok back menu settings channels guide info channel_up channel_down volume_up volume_down mute play_pause audio aspect zoom pip fullscreen",
    "playback": "pause resume previous_channel next_channel step_channel seek seek_relative",
    "lifecycle": "restart_stream reload_player restart_app standby wake exit_app reboot_device",
    "restart": "stream player", "kiosk": "status off on set",
    "plex_queue": "status preview play next previous stop repeat", "plex_library": "list preview play",
    "local_media": "sources play shuffle connect",
}
PERMISSIONS = frozenset((FAMILIES - OPERATIONS.keys()) | {family + "." + op for family, ops in OPERATIONS.items() for op in ops.split()})
ERRORS = frozenset(("invalid_request", "runtime_mismatch", "unavailable", "policy_denied"))
HELP = """ott PLAYER audit [REQUEST_ID] [-j]
ott PLAYER audit --activity [-j]

Read bounded controller RPC ingress metadata without sending player commands.
--activity additionally reads advertised native command activity and joins exact
request IDs. It never identifies the human or application behind shared credentials.
Missing IDs or retained entries mean unknown correlation, not proof of no command.
"""


class InvalidData(ValueError):
    pass


class Failure(Exception):
    def __init__(self, reason):
        self.reason = reason


def check(condition):
    if not condition:
        raise InvalidData()


def exact(value, keys):
    check(type(value) is dict and set(value) == set(keys))
    return value


def token(value, pattern):
    check(isinstance(value, str) and re.fullmatch(pattern, value) is not None)
    return value


def integer(value, maximum=MAX_INTEGER, minimum=0):
    check(type(value) is int and minimum <= value <= maximum)
    return value


def request_id(value):
    return token(value, r"[0-9a-f]{32}")


def runtime(value):
    return token(value, r"[A-Za-z0-9_.-]{1,96}")


def decode(raw, maximum):
    check(isinstance(raw, (bytes, str)))
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    check(len(raw) <= maximum)
    def fields(rows):
        value = {}
        for key, item in rows:
            check(key not in value)
            value[key] = item
        return value
    def reject(_value):
        raise InvalidData()
    # This protocol contains no floats, including integral-valued float spellings.
    return json.loads(raw.decode("utf-8"), object_pairs_hook=fields,
                      parse_float=reject, parse_constant=reject)


def initiator(value):
    exact(value, ("credential_role", "operator_id"))
    role = value["credential_role"]
    if role == "admin":
        check(value["operator_id"] is None)
    else:
        check(role == "operator")
        token(value["operator_id"], r"[A-Za-z0-9_.:-]{1,80}")
    return copy.deepcopy(value)


def audit_metadata(value, device, selected=None):
    exact(value, ("version", "scope", "controller_epoch", "device_id", "sampled_at_ms", "retention", "lookup", "entries"))
    integer(value["version"], 1, 1)
    check(value["scope"] == "controller_rpc_ingress" and isinstance(value["device_id"], str) and value["device_id"] == device)
    request_id(value["controller_epoch"])
    integer(value["sampled_at_ms"])
    exact(value["retention"], RETENTION)
    for key, expected in RETENTION.items():
        integer(value["retention"][key], expected, expected)
    check(value["lookup"] in (("not_requested",) if selected is None else ("found", "not_retained")))
    entries = value["entries"]
    check(type(entries) is list and len(entries) <= 32)
    seen = set()
    for entry in entries:
        exact(entry, ("request_id", "action", "initiator", "accepted_at_ms", "expires_at_ms", "age_ms", "state", "result_status"))
        identity = request_id(entry["request_id"])
        check(identity not in seen)
        seen.add(identity)
        check(isinstance(entry["action"], str) and entry["action"] in PERMISSIONS)
        initiator(entry["initiator"])
        accepted = integer(entry["accepted_at_ms"])
        expires = integer(entry["expires_at_ms"])
        integer(entry["age_ms"], RETENTION["max_age_ms"])
        check(accepted <= expires)
        check(entry["state"] in ("pending", "responded", "expired"))
        if entry["state"] == "responded":
            check(entry["result_status"] in ("ok", "rejected", "unsupported"))
        else:
            check(entry["result_status"] is None)
    if selected is not None:
        request_id(selected)
        check((value["lookup"] == "found" and len(entries) == 1 and entries[0]["request_id"] == selected)
              or (value["lookup"] == "not_retained" and not entries))
    return copy.deepcopy(value)


def activity_request(params):
    exact(params, ("version", "runtime"))
    integer(params["version"], 2, 2)
    runtime(params["runtime"])
    return dict(params)


def activity_metadata(value, params, status="ok"):
    activity_request(params)
    check(type(value) is dict)
    integer(value.get("version"), 2, 2)
    runtime(value.get("runtime"))
    if status == "rejected":
        exact(value, ("version", "runtime", "error"))
        check(isinstance(value["error"], str) and value["error"] in ERRORS)
        check(value["error"] == "runtime_mismatch" or value["runtime"] == params["runtime"])
        return dict(value)
    check(status == "ok")
    exact(value, ("version", "runtime", "scope", "entries"))
    check(value["runtime"] == params["runtime"] and value["scope"] == "remote_ingress")
    check(type(value["entries"]) is list and len(value["entries"]) <= 8)
    for row in value["entries"]:
        exact(row, ("sequence", "action", "age_ms", "result", "request_id"))
        integer(row["sequence"], minimum=1)
        check(isinstance(row["action"], str) and row["action"] in FAMILIES)
        integer(row["age_ms"])
        check(row["result"] in ("pending", "ok", "rejected", "unsupported", "failed"))
        if row["request_id"] is not None:
            request_id(row["request_id"])
    return copy.deepcopy(value)


def activity_receipt(api, raw, params, identity):
    try:
        value = decode(raw, MAX_ACTIVITY_BYTES)
        exact(value, ("id", "status", "data"))
        check(value["id"] == request_id(identity))
        activity_metadata(value["data"], params, value["status"])
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise api.Error("The player returned invalid command activity diagnostics") from None


def activity_decode(api, raw):
    try:
        return decode(raw, MAX_ACTIVITY_BYTES)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise api.Error("The player returned invalid command activity diagnostic JSON") from None


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise Failure("invalid_arguments")


def parse(words, json_output=False):
    if words in (["--help"], ["-h"]):
        return {"help": True}
    parser = Parser(prog="ott PLAYER audit", allow_abbrev=False, add_help=False)
    parser.add_argument("request_id", nargs="?")
    parser.add_argument("--activity", action="store_true")
    parser.add_argument("-j", "--json", action="store_true", default=json_output)
    args = parser.parse_args(words)
    if args.request_id is not None:
        try:
            request_id(args.request_id)
        except ValueError:
            raise Failure("invalid_arguments") from None
    if args.request_id is not None and args.activity:
        raise Failure("invalid_arguments")
    return vars(args)


def reason(api, exc):
    if isinstance(exc, Failure):
        return exc.reason
    if isinstance(exc, (ValueError, TypeError, UnicodeError, RecursionError)):
        return "invalid_response"
    if isinstance(exc, api.RequestError):
        return exc.reason if exc.reason in ("controller_unavailable", "controller_http_error", "request_pending_timeout", "receipt_missing", "unauthorized", "rate_limited", "invalid_response") else "unavailable"
    if isinstance(exc, api.HTTPError):
        return ("unsupported_controller" if exc.code in (400, 404, 405, 422) else
                "unauthorized" if exc.code in (401, 403) else "rate_limited" if exc.code == 429 else "controller_http_error")
    if isinstance(exc, api.TransportError):
        return "controller_unavailable"
    return "unavailable"


class Audit:
    def __init__(self, api, client, device, timeout):
        self.api, self.client, self.device = api, client, device
        self.deadline = time.monotonic() + timeout
        self.server = client.server

    def budget(self):
        cancelled = getattr(self.client, "group_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt
        if self.client.server != self.server:
            raise Failure("controller_changed")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("deadline_exceeded")
        return remaining

    def rpc(self, action, params):
        self.client.timeout = self.budget()
        value = self.client.call_receipt(self.device, action, params)
        self.budget()
        exact(value, ("request_id", "status", "data"))
        request_id(value["request_id"])
        check(value["status"] == "ok")
        return value["data"]

    def activity(self):
        params = None
        try:
            raw = self.rpc("capabilities", {})
            try:
                caps = self.api.capabilities_metadata(raw)
            except self.api.Error:
                raise InvalidData() from None
            if caps.get(ACTION) != {"version": 2}:
                raise Failure("unsupported")
            params = {"version": 2, "runtime": runtime(caps["player"]["runtime"])}
            value = activity_metadata(self.rpc(ACTION, params), params)
            return {"status": "observed", "runtime": value["runtime"], "entries": value["entries"]}
        except (self.api.Error, Failure, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            if params is not None and isinstance(exc, (self.api.PlayerRejected, self.api.PlayerUnsupported)):
                try:
                    value = activity_metadata(exc.data, params, "rejected" if isinstance(exc, self.api.PlayerRejected) else "unsupported")
                    why = value["error"]
                except (ValueError, TypeError):
                    why = "invalid_response"
            else:
                why = reason(self.api, exc)
            return {"status": "unknown", "reason": why}

    def controller(self, selected=None):
        try:
            query = {"device_id": self.device}
            if selected is not None:
                query["id"] = selected
            self.client.timeout = self.budget()
            status, raw = self.client.api_raw("/api/request-audit?" + urllib.parse.urlencode(query), timeout=self.client.timeout)
            self.budget()
            check(status == 200)
            data = audit_metadata(decode(raw, MAX_AUDIT_BYTES), self.device, selected)
            return {"status": "observed", "data": data}
        except (self.api.Error, Failure, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            return {"status": "unknown", "reason": reason(self.api, exc)}


def correlate(controller, activity):
    if controller["status"] != "observed" or activity["status"] != "observed":
        return []
    entries = {row["request_id"]: row for row in controller["data"]["entries"]}
    result = []
    for row in activity["entries"]:
        match = entries.get(row["request_id"])
        out = {key: row[key] for key in ("sequence", "request_id", "action")}
        if row["request_id"] is None:
            out.update(status="unknown", reason="request_id_not_reported")
        elif match is None:
            out.update(status="unknown", reason="not_retained")
        elif match["action"].split(".", 1)[0] != row["action"]:
            out.update(status="unknown", reason="action_mismatch")
        else:
            out.update(status="matched", initiator=copy.deepcopy(match["initiator"]))
        result.append(out)
    return result


def print_summary(result):
    print("Request audit: " + result["verdict"] + " (controller RPC ingress metadata only)")
    controller = result["controller"]
    if controller["status"] == "observed":
        data = controller["data"]
        print("  Retention: up to 32 entries per player / 512 total / 10 minutes; lookup: " + data["lookup"])
        for row in data["entries"]:
            who = row["initiator"]["credential_role"]
            if who == "operator":
                who += ":" + row["initiator"]["operator_id"]
            print("  " + row["request_id"] + "  " + row["action"] + "  " + who + "  " + row["state"] + "  age " + str(row["age_ms"]) + " ms")
    else:
        print("  Controller: unknown (" + controller["reason"] + ")")
    if result["activity"]["status"] == "unknown":
        print("  Native activity: unknown (" + result["activity"]["reason"] + ")")
    for row in result["correlations"]:
        print("  Native sequence " + str(row["sequence"]) + ": " + row["status"] + (" (" + row["reason"] + ")" if "reason" in row else " by exact request ID"))
    print("  Shared credentials do not identify a human, application or chat. Missing evidence stays unknown.")


def run(api, client, device, words, timeout, json_output=False):
    try:
        args = parse(words, json_output)
    except Failure:
        result = {"version": 1, "command": "audit", "read_only": True, "physical_display_verified": False,
                  "verdict": "error", "reason": "invalid_arguments"}
        if json_output or any(word in ("-j", "--json") for word in words):
            print(json.dumps(result, ensure_ascii=True))
        else:
            print("Invalid audit arguments. Use ott PLAYER audit [REQUEST_ID] [-j] or audit --activity [-j].")
        return 1
    if args.get("help"):
        print(HELP, end="")
        return 0
    result = {"version": 1, "command": "audit", "device_id": device, "read_only": True,
              "physical_display_verified": False, "activity": {"status": "not_requested"}}
    old_timeout = client.timeout
    try:
        audit = Audit(api, client, device, timeout)
        if args["activity"]:
            result["activity"] = audit.activity()
        result["controller"] = audit.controller(args["request_id"])
        result["correlations"] = correlate(result["controller"], result["activity"])
        result["correlation_status"] = ("not_requested" if not args["activity"] else
                                        "matched" if result["correlations"] and all(row["status"] == "matched" for row in result["correlations"]) else "unknown")
        lookup_missing = (args["request_id"] is not None and result["controller"]["status"] == "observed"
                          and result["controller"]["data"]["lookup"] == "not_retained")
        result["verdict"] = ("observed" if result["controller"]["status"] == "observed" and not lookup_missing
                             and result["correlation_status"] != "unknown" else "unknown")
    finally:
        client.timeout = old_timeout
    if args["json"]:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print_summary(result)
    return 0 if result["verdict"] == "observed" else 3
