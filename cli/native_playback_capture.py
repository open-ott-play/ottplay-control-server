"""Bounded native playback capture controls; never replay a diagnostic mutation."""
import argparse
import copy
import json
import re
import time


CAPABILITY = {"version": 1, "operations": ["start", "status", "read", "stop"],
              "default_duration_s": 30, "min_duration_s": 5, "max_duration_s": 60,
              "sample_interval_ms": 1000, "max_records": 64}
ACTION = "native_playback_diagnostics"
MAX_BYTES = 65536
RUNTIME = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
CAPTURE_ID = re.compile(r"cap_[0-9a-f]{32}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
ERRORS = frozenset(("unsupported", "invalid_request", "runtime_mismatch", "no_active_playback",
                    "capture_not_found", "policy_denied", "capture_busy"))
STATES = ("idle", "capturing", "complete", "stopped", "session_changed", "expired")
TERMINAL_STATES = ("stopped", "complete", "session_changed", "expired")
RECORD_INTEGERS = {
    "t_ms": (0, 60000, False),
    "frames": (0, 4294967295, False),
    "video_queue_bytes": (0, 4294967295, False),
    "audio_queue_bytes": (0, 4294967295, False),
    "callback_count": (0, 4294967295, False),
    "fps_milli": (0, 4294967295, False),
    "frame_age_ms": (0, 60000, True),
    "playable_buffer_ms": (0, 60000, True),
    "callback_error_age_ms": (0, 60000, True),
    "fed_video_units": (0, 9007199254740991, False),
    "fed_audio_units": (0, 9007199254740991, False),
    "network_rx_bytes": (0, 9007199254740991, False),
    "fed_pts_skew_ms": (-600000, 600000, True),
    "callback_error_code": (-2147483648, 2147483647, True),
    "http_status": (100, 599, True),
    "video_width": (0, 65535, False),
    "video_height": (0, 65535, False),
}
RECORD_ENUMS = {"stage": ("loading", "playing", "bound", "streaming"),
                "load_state": ("pending", "complete", "failed"),
                "feed_state": ("none", "accepting", "full", "refused", "holding", "empty")}


class InvalidData(ValueError):
    pass


class Failure(Exception):
    def __init__(self, reason, *, unknown_outcome=False, confirmed=None, rejected=False):
        self.reason = reason
        self.unknown_outcome = unknown_outcome
        self.confirmed = confirmed
        self.rejected = rejected


def check(value):
    if not value:
        raise InvalidData()


def integer(value, minimum, maximum):
    check(type(value) is int and minimum <= value <= maximum)
    return value


def exact(value, keys):
    check(type(value) is dict and set(value) == set(keys))
    return value


def token(value, pattern):
    check(isinstance(value, str) and pattern.fullmatch(value) is not None)
    return value


def capability(marker):
    exact(marker, CAPABILITY)
    for key, expected in CAPABILITY.items():
        if key == "operations":
            check(type(marker[key]) is list and marker[key] == expected)
        else:
            integer(marker[key], expected, expected)
    return copy.deepcopy(CAPABILITY)


def decode(raw):
    check(isinstance(raw, (bytes, str)))
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    check(len(raw) <= MAX_BYTES)

    def pairs(rows):
        result = {}
        for key, value in rows:
            check(key not in result)
            result[key] = value
        return result

    def constant(_value):
        raise InvalidData()

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def decode_response(api, raw):
    try:
        return decode(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise api.Error("The player returned invalid native playback capture JSON") from None


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("invalid_arguments")


def parse(words, json_output=False):
    parser = Parser(prog="ott PLAYER debug capture", allow_abbrev=False)
    parser.add_argument("operation", choices=CAPABILITY["operations"])
    parser.add_argument("--duration", type=int, help="capture duration in seconds (5–60; default 30)")
    parser.add_argument("-j", "--json", action="store_true", default=json_output)
    args = parser.parse_args(words)
    if args.operation == "start":
        args.duration = 30 if args.duration is None else args.duration
        if not 5 <= args.duration <= 60:
            raise Failure("invalid_arguments")
    elif args.duration is not None:
        raise Failure("invalid_arguments")
    return args


def request(params):
    check(type(params) is dict)
    integer(params.get("version"), 1, 1)
    token(params.get("runtime"), RUNTIME)
    operation = params.get("operation")
    check(isinstance(operation, str) and operation in CAPABILITY["operations"])
    keys = {"version", "runtime", "operation"}
    if operation == "start":
        keys.add("duration_s")
        integer(params.get("duration_s"), 5, 60)
    else:
        keys.add("capture_id")
        if operation != "status" or params.get("capture_id") is not None:
            token(params.get("capture_id"), CAPTURE_ID)
    exact(params, keys)
    return dict(params)


def record(value):
    exact(value, set(RECORD_INTEGERS) | set(RECORD_ENUMS))
    for key, (minimum, maximum, nullable) in RECORD_INTEGERS.items():
        if nullable and value[key] is None:
            continue
        integer(value[key], minimum, maximum)
    for key, values in RECORD_ENUMS.items():
        check(isinstance(value[key], str) and value[key] in values)
    return dict(value)


def metadata(data, params, status="ok"):
    request(params)
    check(type(data) is dict)
    integer(data.get("version"), 1, 1)
    check(data.get("operation") == params["operation"])
    common = {"version", "operation"}
    if status in ("rejected", "unsupported"):
        exact(data, common | {"error"})
        check(isinstance(data["error"], str) and data["error"] in ERRORS)
        check((status == "unsupported") == (data["error"] == "unsupported"))
        return dict(data)
    check(status == "ok")
    common |= {"state", "capture_id"}
    operation = params["operation"]
    if operation == "start":
        exact(data, common)
        check(data["state"] == "capturing")
        token(data["capture_id"], CAPTURE_ID)
        return dict(data)
    exact(data, common | {"elapsed_ms", "records", "dropped"})
    check(isinstance(data["state"], str) and data["state"] in STATES)
    integer(data["elapsed_ms"], 0, 60000)
    integer(data["dropped"], 0, 4294967295)
    if data["state"] == "idle":
        check(operation == "status" and params["capture_id"] is None and data["capture_id"] is None)
        check(data["elapsed_ms"] == 0 and data["dropped"] == 0)
        integer(data["records"], 0, 0)
        return dict(data)
    token(data["capture_id"], CAPTURE_ID)
    if params["capture_id"] is not None:
        check(data["capture_id"] == params["capture_id"])
    if operation == "stop":
        check(data["state"] in TERMINAL_STATES)
    if operation == "read":
        check(type(data["records"]) is list and len(data["records"]) <= 64)
        previous_time = 0
        for row in data["records"]:
            record(row)
            check(previous_time <= row["t_ms"] <= data["elapsed_ms"])
            previous_time = row["t_ms"]
    else:
        integer(data["records"], 0, 64)
    return copy.deepcopy(data)


def receipt(api, raw, params, request_id):
    try:
        result = decode(raw)
        exact(result, {"id", "status", "data"})
        token(request_id, REQUEST_ID)
        check(result["id"] == request_id)
        metadata(result["data"], params, result["status"])
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise api.Error("The player returned an invalid native playback capture receipt") from None


class Capture:
    def __init__(self, api, client, device, timeout):
        self.api, self.client, self.device = api, client, device
        self.deadline = time.monotonic() + timeout
        self.requests = []

    def rpc(self, action, params):
        cancelled = getattr(self.client, "group_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("deadline_exceeded")
        mutation = action == ACTION and params["operation"] in ("start", "stop")
        row = {"action": action, "state": "sent"}
        if action == ACTION:
            row["operation"] = params["operation"]
        self.requests.append(row)
        previous = self.client.timeout
        self.client.timeout = remaining
        try:
            response = self.client.call_receipt(self.device, action, params)
            check(type(response) is dict and response.get("status") == "ok")
            token(response.get("request_id"), REQUEST_ID)
            row["request_id"] = response["request_id"]
            if action == ACTION:
                data = metadata(response.get("data"), params)
            else:
                try:
                    data = self.api.capabilities_metadata(response.get("data"))
                except self.api.Error:
                    raise InvalidData() from None
            row["state"] = "confirmed"
            if time.monotonic() >= self.deadline:
                row["state"] = "confirmed_late"
                raise Failure("deadline_exceeded", confirmed=data if action == ACTION else None)
            return data
        except (self.api.PlayerRejected, self.api.PlayerUnsupported) as exc:
            try:
                check(action == ACTION)
                status = "unsupported" if isinstance(exc, self.api.PlayerUnsupported) else "rejected"
                data = metadata(exc.data, params, status)
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                row.update(state="unconfirmed", reason="invalid_response")
                raise Failure("invalid_response", unknown_outcome=mutation) from None
            identity = getattr(exc, "request_id", None)
            if isinstance(identity, str) and REQUEST_ID.fullmatch(identity):
                row["request_id"] = identity
            row.update(state=status, reason=data["error"])
            raise Failure(data["error"], rejected=True) from None
        except self.api.RequestError as exc:
            detail = self.api.request_error_metadata(exc)
            row.update(state="unconfirmed", reason=detail["reason"], request=detail)
            if detail["request_id"] is not None:
                row["request_id"] = detail["request_id"]
            raise Failure(detail["reason"], unknown_outcome=mutation) from None
        except self.api.HTTPError as exc:
            reason = ("unsupported_controller" if exc.code in (400, 404, 405, 422)
                      else "unauthorized" if exc.code in (401, 403)
                      else "rate_limited" if exc.code == 429 else "controller_http_error")
            row.update(state="unconfirmed", reason=reason)
            raise Failure(reason, unknown_outcome=mutation and exc.code >= 500) from None
        except Failure:
            raise
        except (KeyboardInterrupt, SystemExit):
            row["state"] = "unconfirmed"
            raise
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            row.update(state="unconfirmed", reason="invalid_response")
            raise Failure("invalid_response", unknown_outcome=mutation) from None
        except self.api.Error:
            row.update(state="unconfirmed", reason="controller_unavailable")
            raise Failure("controller_unavailable", unknown_outcome=mutation) from None
        finally:
            self.client.timeout = previous

    def call(self, operation, **values):
        params = request(dict(values, version=1, runtime=self.runtime, operation=operation))
        return self.rpc(ACTION, params)

    def setup(self):
        caps = self.rpc("capabilities", {"native_playback_capture": True})
        if "native_playback_capture" not in caps:
            raise Failure("unsupported")
        capability(caps["native_playback_capture"])
        self.runtime = caps["player"]["runtime"]


def run(api, client, device, words, timeout, json_output=False):
    operation = next((word for word in words if word in CAPABILITY["operations"]), None)
    json_output = json_output or any(word in ("-j", "--json") for word in words)
    result = {"version": 1, "command": "debug capture", "operation": operation, "device_id": device,
              "evaluator": "native-playback-capture-v1", "read_only": operation not in ("start", "stop"),
              "physical_display_verified": False, "unknown_outcome": False, "requests": []}
    capture = None
    code = 0
    try:
        args = parse(words, json_output)
        json_output = args.json
        result.update(operation=args.operation, read_only=args.operation not in ("start", "stop"))
        capture = Capture(api, client, device, timeout)
        result["requests"] = capture.requests
        capture.setup()
        result["runtime"] = capture.runtime
        if args.operation == "start":
            data = capture.call("start", duration_s=args.duration)
        else:
            data = capture.call("status", capture_id=None)
            if args.operation in ("read", "stop"):
                if data["capture_id"] is None:
                    result["no_capture"] = True
                else:
                    discovered = data
                    data = capture.call(args.operation, capture_id=discovered["capture_id"])
                    if discovered["state"] in TERMINAL_STATES:
                        count = len(data["records"]) if args.operation == "read" else data["records"]
                        if ((data["state"], data["elapsed_ms"], data["dropped"], count)
                                != (discovered["state"], discovered["elapsed_ms"], discovered["dropped"], discovered["records"])):
                            capture.requests[-1].update(state="unconfirmed", reason="invalid_response")
                            raise Failure("invalid_response", unknown_outcome=args.operation == "stop")
        result.update(verdict="observed", data=data)
    except Failure as exc:
        code = 1 if exc.reason == "invalid_arguments" or exc.rejected else 3
        result.update(verdict="error" if code == 1 else "unknown", reason=exc.reason,
                      unknown_outcome=exc.unknown_outcome)
        if exc.confirmed is not None:
            result["data"] = exc.confirmed
    except KeyboardInterrupt:
        code = 130
        mutation = capture is not None and any(row.get("operation") in ("start", "stop")
                                              and row["state"] == "unconfirmed" for row in capture.requests)
        result.update(verdict="unknown", reason="interrupted", unknown_outcome=mutation)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        code = 3
        result.update(verdict="unknown", reason="invalid_response")
    if json_output:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print("Native playback capture " + (result["operation"] or "command") + ": " + result["verdict"])
        if "reason" in result:
            print("Reason: " + result["reason"])
        if result.get("no_capture"):
            print("No capture is retained; no " + result["operation"] + " command was sent.")
        if "data" in result:
            print(json.dumps(result["data"], ensure_ascii=True, allow_nan=False, indent=2))
        if result["unknown_outcome"]:
            print("The capture change may have executed. Read debug capture status before another change. The request was not repeated.")
        print("Runtime-reported diagnostics; physical display and audio remain unverified.")
    return code
