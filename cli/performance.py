"""Opt-in bounded performance capture and guarded notification experiments."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import time

SAFE = 9007199254740991
MAX_DATA, MAX_EVENT, MAX_EXPORT = 60 * 1024, 2048, 1024 * 1024
CAPABILITY = {"version": 1, "default_duration_seconds": 120, "max_duration_seconds": 600,
              "max_events": 1024, "max_bytes": 524288, "max_read_events": 64}
HLS_CAPABILITY = {"version": 2}
SESSION_ID = r"[a-f0-9]{32}"
RUNTIME = r"[A-Za-z0-9_.:-]{1,128}"
REASONS = (None, "requested", "expired", "disconnected", "page_hidden", "runtime_changed", "unsupported")
ERRORS = ("invalid_request", "runtime_mismatch", "unsupported", "not_found", "busy", "disconnected", "expired")
REQUEST_FAILURE_REASONS = ("controller_unavailable", "controller_http_error", "request_pending_timeout",
                           "receipt_missing", "unauthorized", "rate_limited", "invalid_response")
STAGES = ("received", "queued", "dom_created", "media_fetch_start", "media_received", "image_loaded",
          "shown", "updated", "hidden", "dropped", "coalesced")
TIMINGS = ("fetch_ms", "base64_ms", "validation_ms", "blob_ms", "image_load_decode_ms", "decode_ms", "queue_ms")
HLS_DETAILS = (None, "manifest_load_error", "manifest_load_timeout", "level_load_error", "level_load_timeout",
               "level_empty_error", "frag_load_error", "frag_load_timeout", "frag_parsing_error", "buffer_append_error",
               "buffer_stalled_error", "buffer_nudge_on_stall", "buffer_seek_over_hole", "other")


class Failure(Exception):
    def __init__(self, reason, *, uncertain=False, confirmed=None):
        self.reason, self.uncertain, self.confirmed = reason, uncertain, confirmed
        super().__init__(reason)


def check(value, reason="invalid_response"):
    if not value:
        raise Failure(reason)


def encoded(value):
    return (json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()


def num(low=0, high=SAFE, integer=False):
    def valid(value):
        check(type(value) in ((int,) if integer else (int, float)) and math.isfinite(value) and low <= value <= high)
    return valid


def one(*values):
    def valid(value):
        check(any(type(value) is type(item) and value == item for item in values))
    return valid


def matching(pattern):
    def valid(value):
        check(isinstance(value, str) and re.fullmatch(pattern, value) is not None)
    return valid


def nullable(rule):
    def valid(value):
        if value is not None:
            rule(value)
    return valid


def fields(**rules):
    def valid(value):
        check(isinstance(value, dict) and set(value) == set(rules))
        for key, rule in rules.items():
            rule(value[key])
    return valid


def array(rule, maximum, unique=False):
    def valid(value):
        check(isinstance(value, list) and len(value) <= maximum)
        for item in value:
            rule(item)
        if unique:
            check(len({json.dumps(item, sort_keys=True) for item in value}) == len(value))
    return valid


N, I, B = num(), num(integer=True), one(False, True)
NN, NI, NB = nullable(N), nullable(I), nullable(B)
SIGNED = nullable(num(-SAFE))
ATTEMPT_FIELDS = {key: I for key in ("attachment", "engineStarts", "fatalErrors", "nativeFallbacks", "streamRetries")}
ATTEMPT = fields(**ATTEMPT_FIELDS)
LAST_ATTEMPT = fields(**dict(ATTEMPT_FIELDS, handleId=I, generation=NI, reason=one("ended", "disposed", "suspended")))
OPTIONS = fields(raf=B, long_tasks=B)
SUPPORT = fields(raf=B, long_tasks=B, notification_stages=array(one(*STAGES), len(STAGES), True),
                 notification_timings=array(one(*TIMINGS), len(TIMINGS), True))
SESSION = fields(id=matching(SESSION_ID), state=one("active", "stopped"), reason=one(*REASONS),
                 duration_seconds=num(10, 600, True), sample_interval_ms=one(1000), elapsed_ms=N, remaining_ms=N,
                 started_at_unix_ms=I, clock=one("performance", "wall_fallback"), requested=OPTIONS, supported=SUPPORT,
                 first_seq=num(1, SAFE, True), next_seq=num(1, SAFE, True), event_count=num(0, 1024, True),
                 dropped_events=I, rate_dropped=I, ring_dropped=I, oversized_dropped=I, bytes=num(0, 524288, True))
BUFFER = fields(range_count=nullable(num(0, 65535, True)), truncated_count=nullable(num(0, 65535, True)),
                ranges=array(fields(index=num(0, 65534, True), start_seconds=num(-SAFE), end_seconds=num(-SAFE)), 8))
FRAMES = fields(source=one("quality", "webkit", "unsupported"), total=NI, dropped=NI, corrupted=NI)
EVENT_DATA = {
    "sample": fields(position_seconds=SIGNED, playback_rate=nullable(num(0, 16)), buffer_ahead_seconds=NN, buffered=BUFFER,
                     ready_state=nullable(num(0, 4, True)), network_state=nullable(num(0, 3, True)),
                     paused=NB, seeking=NB, ended=NB, width=nullable(num(0, 65535, True)),
                     height=nullable(num(0, 65535, True)), frames=FRAMES,
                     attempt=nullable(ATTEMPT), last_attempt=nullable(LAST_ATTEMPT)),
    "media": fields(event=one("waiting", "stalled", "playing", "pause", "seeking", "seeked", "error", "ended"),
                    error_code=nullable(num(1, 4, True)), reason=one(None)),
    "hls": fields(event=one("fragment", "fragment_buffered", "playlist", "error"), detail=one(*HLS_DETAILS), level=NI, part_index=NI, fragment_sequence=NI,
                  http_status=nullable(num(100, 599, True)), timing_source=one("performance", "unavailable"),
                  load_start_ms=SIGNED, first_byte_at_ms=SIGNED, load_end_ms=SIGNED,
                  load_duration_ms=NN, first_byte_duration_ms=NN, parse_duration_ms=NN, buffer_duration_ms=NN,
                  bytes=NI, retries=NI, fatal=NB, category=one(None, "network", "media", "other")),
    "abr": fields(from_level=NI, to_level=NI, automatic=NB, bitrate=NN,
                  width=nullable(num(0, 65535, True)), height=nullable(num(0, 65535, True))),
    "recovery": fields(event=one("engine_start", "fatal_error", "native_fallback", "stream_retry"),
                       attempt=nullable(ATTEMPT)),
    "loop": fields(window_ms=N, samples=I, max_delay_ms=NN, raf_callbacks=NI, raf_max_gap_ms=NN,
                   long_tasks=NI, long_tasks_total_ms=NN, long_tasks_max_ms=NN, long_task_entries_dropped=NI),
    "notification": fields(notification_id=nullable(matching(r"n[1-9][0-9]{0,9}")), stage=one(*STAGES),
                           reason=one(None, "disabled", "expired", "duplicate", "invalid", "capacity", "superseded",
                                      "hidden", "stopped", "media_error", "unsupported"), media=one("none", "image"),
                           active_count=num(0, 3, True), image_width=nullable(num(1, 640, True)),
                           image_height=nullable(num(1, 360, True)), image_bytes=nullable(num(1, 163840, True)),
                           **{key: NN for key in TIMINGS}),
    "session": fields(event=one("started", "stopped", "source_changed"), reason=one(*REASONS)),
}


TRACK = one("audio", "video", "audiovideo")
HLS_LEVEL = fields(index=nullable(num(-1, 65535, True)), bitrate=NN, max_bitrate=NN)
HLS_EVENT_DATA = {
    "hls_buffer": fields(track=TRACK, position_seconds=SIGNED, buffered=BUFFER),
    "hls_fragment": fields(event=one("fragment", "fragment_buffered"), level=NI, part_index=NI,
                           fragment_sequence=NI, discontinuity_counter=NI, start_seconds=SIGNED, end_seconds=SIGNED,
                           start_pts_seconds=SIGNED, end_pts_seconds=SIGNED,
                           elementary_streams=array(fields(track=TRACK, start_pts_seconds=SIGNED, end_pts_seconds=SIGNED), 3)),
    "hls_config": fields(max_buffer_length_seconds=NN, max_max_buffer_length_seconds=NN, max_buffer_size_bytes=NI,
                         back_buffer_length_seconds=NN, max_buffer_hole_seconds=NN,
                         high_buffer_watchdog_period_seconds=NN, nudge_offset_seconds=NN, nudge_max_retry=NI,
                         live_sync_duration_seconds=NN, live_sync_duration_count=NN,
                         live_max_latency_duration_seconds=NN, live_max_latency_duration_count=NN),
    "hls_live": fields(position_seconds=SIGNED, target_duration_seconds=NN, edge_seconds=SIGNED,
                       live_sync_position_seconds=SIGNED, latency_seconds=SIGNED,
                       target_latency_seconds=NN, max_latency_seconds=NN, effective_buffer_length_seconds=NN,
                       current_level=HLS_LEVEL, load_level=HLS_LEVEL),
}


def validate_session(value, version=1):
    if version == 2:
        check(isinstance(value, dict) and "capture_version" in value)
        one(1, 2)(value["capture_version"])
        SESSION({key: item for key, item in value.items() if key != "capture_version"})
    else:
        SESSION(value)
    check(value["first_seq"] <= value["next_seq"])
    check((value["event_count"] != 0 or value["first_seq"] == value["next_seq"]) and
          value["event_count"] <= value["next_seq"] - value["first_seq"])
    check(value["dropped_events"] == value["rate_dropped"] + value["ring_dropped"] + value["oversized_dropped"])
    check(value["event_count"] + value["dropped_events"] == value["next_seq"] - 1)
    check(value["remaining_ms"] <= value["duration_seconds"] * 1000)
    check((value["state"] == "active" and value["reason"] is None) or
          (value["state"] == "stopped" and value["reason"] is not None and value["remaining_ms"] == 0))


def validate_buffer(buffer):
    count, ranges = buffer["range_count"], buffer["ranges"]
    if count is None:
        check(buffer["truncated_count"] is None and ranges == [])
    else:
        check(buffer["truncated_count"] == count - len(ranges))
        previous = -1
        for row in ranges:
            check(previous < row["index"] < count and row["start_seconds"] <= row["end_seconds"])
            check(previous < 0 or row["index"] == previous + 1)
            previous = row["index"]


def ordered_pair(value, start, end):
    check(value[start] is None or value[end] is None or value[start] <= value[end])


def validate_event(value, session):
    rules = dict(EVENT_DATA, **HLS_EVENT_DATA) if session.get("capture_version") == 2 else EVENT_DATA
    check(isinstance(value, dict) and isinstance(value.get("kind"), str) and value["kind"] in rules)
    fields(seq=num(1, SAFE, True), at_ms=N, kind=one(*rules), lane=one(None, "main", "pip"), generation=NI,
           handle_id=NI, source_id=nullable(matching(r"s[1-9][0-9]{0,9}")), data=rules[value["kind"]])(value)
    check(len(encoded(value)) - 1 <= MAX_EVENT)
    check(value["seq"] < session["next_seq"] and value["at_ms"] <= session["elapsed_ms"])
    data = value["data"]
    if value["kind"] == "sample":
        frames, buffer = data["frames"], data["buffered"]
        if frames["source"] == "unsupported":
            check(all(frames[key] is None for key in ("total", "dropped", "corrupted")))
        if frames["total"] is not None and frames["dropped"] is not None:
            check(frames["dropped"] <= frames["total"])
        validate_buffer(buffer)
    elif value["kind"] == "notification":
        check(data["stage"] in session["supported"]["notification_stages"])
        check(all(data[key] is None or key in session["supported"]["notification_timings"] for key in TIMINGS))
    elif value["kind"] == "loop":
        if not (session["requested"]["raf"] and session["supported"]["raf"]):
            check(data["raf_callbacks"] is None and data["raf_max_gap_ms"] is None)
        if not (session["requested"]["long_tasks"] and session["supported"]["long_tasks"]):
            check(all(data[key] is None for key in ("long_tasks", "long_tasks_total_ms", "long_tasks_max_ms", "long_task_entries_dropped")))
    elif value["kind"] in HLS_EVENT_DATA:
        check(value["lane"] is not None and value["source_id"] is not None)
        if value["kind"] == "hls_buffer":
            validate_buffer(data["buffered"])
        elif value["kind"] == "hls_fragment":
            ordered_pair(data, "start_seconds", "end_seconds")
            ordered_pair(data, "start_pts_seconds", "end_pts_seconds")
            check(len({row["track"] for row in data["elementary_streams"]}) == len(data["elementary_streams"]))
            for row in data["elementary_streams"]:
                ordered_pair(row, "start_pts_seconds", "end_pts_seconds")
        elif value["kind"] == "hls_live":
            for key in ("current_level", "load_level"):
                level = data[key]
                if level["index"] is None or level["index"] < 0:
                    check(level["bitrate"] is None and level["max_bitrate"] is None)


def metadata(raw, request, rejected=False):
    try:
        check(len(encoded(raw)) - 1 <= MAX_DATA)
        version = request["version"]
        one(1, 2)(version)
        common = {"version": one(version), "op": one(request["op"]), "runtime": one(request["runtime"])}
        def session_rule(value):
            validate_session(value, version)
        if rejected:
            fields(**common, error=one(*ERRORS))(raw)
            return raw
        if request["op"] == "read":
            fields(**common, session=session_rule, events=array(lambda v: None, 64), next_after_seq=I,
                   more=B, lost_before=I)(raw)
        else:
            rule = nullable(session_rule) if request["op"] == "status" and "session_id" not in request else session_rule
            fields(**common, session=rule)(raw)
        session = raw["session"]
        if session is None:
            return raw
        check("session_id" not in request or session["id"] == request["session_id"])
        if request["op"] == "start":
            check(session.get("capture_version", 1) == version)
            check(session["duration_seconds"] == request.get("duration_seconds", 120))
            check(session["requested"] == {"raf": request.get("raf", False), "long_tasks": request.get("long_tasks", True)})
        if request["op"] == "stop":
            check(session["state"] == "stopped")
        if request["op"] == "read":
            check(request["after_seq"] < session["next_seq"]
                  and len(raw["events"]) <= min(request.get("limit", 64), session["event_count"]))
            check(raw["lost_before"] == max(0, session["first_seq"] - request["after_seq"] - 1))
            previous, elapsed = request["after_seq"], -1
            for event in raw["events"]:
                validate_event(event, session)
                check(previous < event["seq"] and event["seq"] >= session["first_seq"] and event["at_ms"] >= elapsed)
                previous, elapsed = event["seq"], event["at_ms"]
            if raw["more"]:
                check(bool(raw["events"]) and raw["next_after_seq"] == previous and previous < session["next_seq"] - 1)
            else:
                check(raw["next_after_seq"] == session["next_seq"] - 1 and raw["next_after_seq"] >= previous)
                if request["after_seq"] < session["first_seq"]:
                    check(len(raw["events"]) == session["event_count"])
        return raw
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise Failure("invalid_response") from None


def peer(name):
    path = Path(__file__).resolve().with_name(name + ".py")
    check(path.is_file(), "missing_cli_module")
    spec = importlib.util.spec_from_file_location("ott_perf_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Journal:
    """Private append-only evidence, with an explicit completion manifest."""
    def __init__(self, directory):
        self.path = Path(directory).expanduser().absolute()
        self.fd, self.total, self.files = None, 0, []
        check(os.name == "posix", "private_output_requires_posix")
        descriptor = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.path.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_fd
            os.mkdir(self.path.name, 0o700, dir_fd=descriptor)
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        finally:
            os.close(descriptor)

    def write(self, name, value):
        check(re.fullmatch(r"[a-z0-9_.-]+", name) is not None, "unsafe_output_name")
        raw = encoded(value)
        check(self.total + len(raw) <= MAX_EXPORT, "export_size_limit")
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)
        self.total += len(raw)
        self.files.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

    def finish(self, result):
        # The destination is useful to the local caller, but is not evidence.
        # Keep usernames and private directory names out of portable reports.
        self.write("result.json", {key: value for key, value in result.items() if key != "output"})
        self.write("manifest.json", {"version": 1, "complete": True, "files": list(self.files),
                                     "capture_complete": result.get("capture", {}).get("complete", False),
                                     "physical_display_verified": False})

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Capture:
    def __init__(self, api, client, device, timeout, journal=None):
        self.api, self.client, self.device, self.journal = api, client, device, journal
        self.deadline = time.monotonic() + timeout
        self.receipts, self.runtime, self.caps = [], None, None
        self.version = 1

    def rpc(self, action, params, validator):
        self.last_attempted = False
        remaining = self.deadline - time.monotonic()
        check(remaining > 0, "deadline_exceeded")
        check(len(self.receipts) < 96, "request_limit")
        item = {"action": action, "params": dict(params), "started_monotonic": time.monotonic(), "state": "sent"}
        index = len(self.receipts)
        if self.journal:
            self.journal.write("request-%03d.json" % index, item)
        self.receipts.append(item)
        previous, self.client.timeout = self.client.timeout, min(remaining, 10)
        mutation = action == "player_settings" and params.get("op") == "set" or action == "perf_capture" and params.get("op") in ("start", "stop")
        try:
            self.last_attempted = True
            receipt = self.client.call_receipt(self.device, action, params)
            check(isinstance(receipt, dict) and receipt.get("status") == "ok"
                  and isinstance(receipt.get("request_id"), str)
                  and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", receipt["request_id"]) is not None)
            result = validator(receipt["data"])
            item.update(state="confirmed", request_id=receipt["request_id"])
            if time.monotonic() >= self.deadline:
                item["state"] = "confirmed_late"
                raise Failure("deadline_exceeded", confirmed=result)
            return result
        except (self.api.PlayerRejected, self.api.PlayerUnsupported) as error:
            if action == "perf_capture":
                reason = metadata(error.data, params, rejected=True)["error"]
            elif action == "player_settings":
                checked = peer("player_settings").metadata(self.api, error.data, params, rejected=True)
                reason = checked["reason"]
            else:
                reason = "unsupported"
            item.update(state="rejected", reason=reason)
            raise Failure(reason) from None
        except self.api.RequestError as error:
            reason = error.reason if error.reason in REQUEST_FAILURE_REASONS else "transport_error"
            item.update(state="unconfirmed", reason=reason)
            raise Failure(reason, uncertain=mutation) from None
        except self.api.HTTPError as error:
            reason = ("unauthorized" if error.code in (401, 403) else "rate_limited" if error.code == 429
                      else "unsupported_controller" if error.code in (400, 404, 405, 422) else "transport_error")
            item.update(state="unconfirmed", reason=reason)
            raise Failure(reason, uncertain=mutation) from None
        except Failure as error:
            if error.confirmed is None:
                item.update(state="unconfirmed")
                error.uncertain = error.uncertain or mutation
            raise
        except (KeyboardInterrupt, SystemExit):
            item.update(state="unconfirmed")
            raise
        except Exception:
            item.update(state="unconfirmed", reason="transport_error")
            raise Failure("transport_error", uncertain=mutation) from None
        finally:
            self.client.timeout = previous
            item["finished_monotonic"] = time.monotonic()

    def setup(self):
        self.caps = self.rpc("capabilities", {}, self.api.capabilities_metadata)
        check(self.caps.get("perf_capture") == CAPABILITY, "unsupported")
        self.runtime = self.caps["player"]["runtime"]

    def call(self, op, **values):
        request = dict(values, version=self.version, op=op, runtime=self.runtime)
        return self.rpc("perf_capture", request, lambda raw: metadata(raw, request))

    def collect(self, session_id):
        first = self.call("status", session_id=session_id)["session"]
        identity = tuple(first[key] for key in ("id", "started_at_unix_ms", "duration_seconds", "clock"))
        highwater, cursor, events, reasons, pages, event_bytes = first["next_seq"] - 1, 0, [], [], 0, 0
        last = first
        try:
            for _ in range(32):
                if cursor >= highwater:
                    break
                data = self.call("read", session_id=session_id, after_seq=cursor, limit=64)
                pages += 1
                previous_elapsed = last["elapsed_ms"]
                last = data["session"]
                check(last["elapsed_ms"] >= previous_elapsed, "session_clock_regressed")
                check(tuple(last[key] for key in ("id", "started_at_unix_ms", "duration_seconds", "clock")) == identity,
                      "session_changed")
                check(last["requested"] == first["requested"] and last["supported"] == first["supported"], "session_changed")
                check(last.get("capture_version", 1) == first.get("capture_version", 1), "session_changed")
                if data["lost_before"]:
                    reasons.append("discarded_prefix")
                for event in data["events"]:
                    if event["seq"] > highwater:
                        continue
                    previous = events[-1]["seq"] if events else 0
                    check(event["seq"] > previous, "nonadvancing_export")
                    check(not events or event["at_ms"] >= events[-1]["at_ms"], "nonmonotonic_export")
                    if event["seq"] != previous + 1:
                        reasons.append("sequence_gap")
                    candidate_bytes = len(encoded(event)) - 1
                    check(len(events) < 1024, "export_event_limit")
                    check(event_bytes + candidate_bytes <= 524288, "export_size_limit")
                    events.append(event)
                    event_bytes += candidate_bytes
                check(data["next_after_seq"] > cursor, "nonadvancing_export")
                cursor = data["next_after_seq"]
            if cursor < highwater:
                reasons.append("page_limit")
        except Failure as error:
            reasons.append(error.reason)
        if first["dropped_events"] or last["dropped_events"]:
            reasons.append("events_dropped")
        if highwater and (not events or events[-1]["seq"] < highwater):
            reasons.append("missing_tail")
        result = {"version": self.version, "runtime": self.runtime, "session_id": session_id,
                  "scope": "frozen_highwater", "highwater": highwater, "pages": pages,
                  "first_session": first, "last_session": last, "events": events,
                  "complete": not reasons, "partial_reasons": sorted(set(reasons))}
        check(len(encoded(result)) <= MAX_EXPORT - 65536, "export_size_limit")
        return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("invalid_arguments")


def arguments(words):
    parser = Parser(prog="ott PLAYER perf", description="Explicit, temporary performance capture; off by default.")
    commands = parser.add_subparsers(dest="operation")
    for operation in ("start", "status", "list", "read", "stop", "export", "trial"):
        command = commands.add_parser(operation)
        command.add_argument("-j", "--json", action="store_true")
        if operation in ("read", "stop", "export"):
            command.add_argument("session_id")
        elif operation in ("start", "status", "list"):
            command.add_argument("--session", dest="session_id")
        if operation == "start":
            command.add_argument("--duration", type=int, default=120)
            command.add_argument("--hls-details", action="store_true", help="collect bounded HLS track buffers, fragment PTS and effective config")
        if operation in ("start", "trial"):
            command.add_argument("--raf", action="store_true")
            command.add_argument("--no-long-tasks", dest="long_tasks", action="store_false", default=True)
        if operation == "read":
            command.add_argument("--after", type=int, default=0)
            command.add_argument("--limit", type=int, default=64)
        if operation in ("export", "trial"):
            command.add_argument("--out", required=True)
        if operation == "trial":
            command.add_argument("scenario", choices=("notifications",))
            command.add_argument("--seconds-per-phase", type=int, default=30)
            command.add_argument("--settle", type=int, default=15)
    args = parser.parse_args(words or ["status"])
    if getattr(args, "session_id", None) is not None:
        check(re.fullmatch(SESSION_ID, args.session_id) is not None, "invalid_arguments")
    if args.operation == "start":
        check(10 <= args.duration <= 600, "invalid_arguments")
    if args.operation == "read":
        check(0 <= args.after <= SAFE and 1 <= args.limit <= 64, "invalid_arguments")
    if args.operation == "trial":
        check(10 <= args.seconds_per_phase <= 60 and 0 <= args.settle <= 15, "invalid_arguments")
    return args


def brief(result):
    if result.get("reason"):
        message = "update the controller; this protocol is not supported" if result["reason"] == "unsupported_controller" else result["reason"].replace("_", " ")
        print("Performance capture: " + message + ". No request was retried.")
    session = result.get("data", {}).get("session")
    if session:
        print("Session " + session["id"] + ": " + session["state"] + "; " + str(session["event_count"]) +
              " retained events, " + str(session["dropped_events"]) + " dropped.")
    elif result.get("operation") in ("status", "list") and not result.get("reason"):
        print("Performance capture is off; no retained session.")
    if result.get("session_id") and not session:
        print("Session: " + result["session_id"])
    capture = result.get("capture")
    if capture is not None:
        print("Capture: " + ("complete" if capture["complete"] else "partial") +
              "; " + str(len(capture["events"])) + " exported events.")
        if capture["partial_reasons"]:
            print("Partial reasons: " + ", ".join(capture["partial_reasons"]))
    if result.get("evidence_error"):
        print("Evidence error: " + result["evidence_error"] + ". The export is not confirmed complete.")
    if result.get("output"):
        print(("Evidence directory (preserve for inspection): " if result.get("evidence_error") else
               "Private evidence: ") + result["output"])
    if "policy_restored" in result:
        if result.get("policy_write_attempted"):
            print("Original notification policy: " + ("confirmed restored" if result["policy_restored"] is True else "not confirmed restored"))
        else:
            print("This trial did not write the notification policy.")
    if result.get("required_timeout"):
        print("Use ott -t " + str(result["required_timeout"]) + " PLAYER perf trial notifications --out NEW_DIRECTORY")


class Trial:
    def __init__(self, capture, args, result):
        self.capture, self.args, self.result = capture, args, result
        self.settings, self.workbench = peer("player_settings"), peer("workbench")
        self.original, self.owned, self.session_id = None, None, secrets.token_hex(16)
        self.started, self.stop_attempted = False, False
        self.transition_latest = 0
        self.deadline = capture.deadline

    def get_policy(self):
        params = {"op": "get", "runtime": self.capture.runtime, "id": "notificationsEnabled"}
        data = self.capture.rpc("player_settings", params,
                                lambda raw: self.settings.metadata(self.capture.api, raw, params))
        descriptor, setting = data["descriptor"], data["setting"]
        check(descriptor["id"] == "notificationsEnabled" and descriptor["type"] == "integer"
              and descriptor["scope"] == "application" and descriptor["sensitive"] is False
              and descriptor["apply"] == "immediate" and descriptor["access"]["read"] is True
              and descriptor["access"]["write"] is True, "unsupported_notification_policy")
        check(setting["storage"] in ("stored", "default") and type(setting.get("value")) is int
              and setting["value"] in (0, 1), "notification_policy_unavailable")
        check(setting.get("effective_value", setting["value"]) == setting["value"], "notification_policy_unavailable")
        return data

    def set_policy(self, previous, value):
        params = {"op": "set", "runtime": self.capture.runtime, "id": "notificationsEnabled",
                  "context": previous["context"]["token"], "revision": previous["setting"]["revision"], "value": value}
        self.result["policy_write_attempted"] = True
        # Clear ownership before sending: an uncertain write must never use a stale
        # revision for restoration, even if the desired value is visible later.
        prior_owned, self.owned = self.owned, None
        prior_restored = self.result.get("policy_restored")
        self.result["policy_restored"] = None
        try:
            data = self.capture.rpc("player_settings", params,
                                    lambda raw: self.settings.metadata(self.capture.api, raw, params, self.original["descriptor"]))
        except BaseException as error:
            if not self.capture.last_attempted:
                self.owned = prior_owned
                self.result["policy_restored"] = prior_restored
            elif isinstance(error, Failure) and error.confirmed is not None:
                self.adopt_policy(error.confirmed, previous)
            raise
        self.adopt_policy(data, previous)
        check(data["application"] == {"state": "completed"} and data["setting"]["value"] == value,
              "notification_policy_not_applied")
        return data

    def adopt_policy(self, data, previous):
        if data["context"] != self.original["context"] or data["setting"]["revision"] == previous["setting"]["revision"]:
            raise Failure("notification_policy_conflict", uncertain=True)
        self.owned = data
        self.result["last_confirmed_policy"] = data

    def wait(self, seconds):
        end = time.monotonic() + seconds
        check(end <= self.capture.deadline, "deadline_exceeded")
        while time.monotonic() < end:
            time.sleep(min(0.5, max(0, end - time.monotonic())))

    def observe_context(self):
        params = {"version": 1, "runtime": self.capture.runtime, "section": "snapshot"}
        data = self.capture.rpc("inspect", params,
                                lambda raw: self.workbench.snapshot_metadata(raw, self.capture.runtime, "snapshot"))
        version = 3 if "debug_attempts" in self.capture.caps else 2
        params = {"version": version, "runtime": self.capture.runtime, "section": "debug"}
        debug = self.capture.rpc("inspect", params,
                                 lambda raw: self.workbench.debug_metadata(raw, self.capture.runtime, version))
        return {"build": data["build"], "ui": data["ui"], "media": data["media"], "engine_observations": debug["media"],
                "standby": None, "standby_evidence": "not_independently_observed"}

    def phase(self, name, policy):
        start = self.capture.call("status", session_id=self.session_id)["session"]
        check(start["state"] == "active", "capture_stopped")
        row = {"phase": name, "notification_policy": policy, "start_session": start,
               "transition_latest_ms": self.transition_latest,
               "steady_from_ms": max(start["elapsed_ms"], self.transition_latest + 15000),
               "context": self.observe_context(), "host_started_monotonic": time.monotonic()}
        self.result["phases"].append(row)
        self.wait(self.args.seconds_per_phase)
        end = self.capture.call("status", session_id=self.session_id)["session"]
        row.update(end_session=end, host_finished_monotonic=time.monotonic())
        check(end["state"] == "active", "capture_stopped")

    def restore(self):
        if self.owned is not None and self.owned["setting"]["value"] != self.original["setting"]["value"]:
            self.result["restore_attempted"] = True
            self.set_policy(self.owned, self.original["setting"]["value"])
        if self.owned is None:
            self.result["policy_restored"] = None if self.result.get("policy_write_attempted") else False
            return
        # Starting a fresh verification retires the prior confirmation. A failed
        # observation must not be retried by cleanup using stale ownership.
        self.result["policy_restored"] = None
        self.result.pop("final_policy", None)
        self.result["storage_representation_changed"] = None
        try:
            current = self.get_policy()
            check(current["context"] == self.original["context"]
                  and current["setting"]["revision"] == self.owned["setting"]["revision"]
                  and current["setting"]["value"] == self.original["setting"]["value"], "notification_policy_superseded")
        except BaseException:
            self.owned = None
            raise
        self.result["policy_restored"] = True
        self.result["final_policy"] = current
        self.result["storage_representation_changed"] = current["setting"]["storage"] != self.original["setting"]["storage"]

    def execute(self):
        required = 3 * self.args.seconds_per_phase + 3 * self.args.settle + 60
        if self.deadline - time.monotonic() < required:
            self.result["required_timeout"] = required + 1
            raise Failure("insufficient_timeout")
        check("player_settings" in self.capture.caps and self.capture.caps.get("debug_versions") == [1, 2]
              and "snapshot" in self.capture.caps.get("inspect", {}).get("sections", []), "unsupported_notification_trial")
        self.original = self.get_policy()
        self.result.update(original_policy=self.original, session_id=self.session_id, phases=[], policy_restored=None,
                           policy_write_attempted=False, restore_attempted=False,
                           storage_representation_changed=None, evidence_level="temporal_correlation_only",
                           unobserved_confounders=["network_contention", "operating_system_scheduling", "physical_display", "standby"])
        self.capture.journal.write("trial-plan.json", {"version": 1, "runtime": self.capture.runtime,
            "session_id": self.session_id, "original_policy": self.original,
            "phases": [self.original["setting"]["value"], 1 - self.original["setting"]["value"], self.original["setting"]["value"]],
            "seconds_per_phase": self.args.seconds_per_phase, "settle_seconds": self.args.settle})
        self.capture.deadline = self.deadline - 15
        try:
            duration = min(600, math.ceil(self.deadline - time.monotonic()))
            started = self.capture.call("start", session_id=self.session_id, duration_seconds=duration,
                                        raf=self.args.raf, long_tasks=self.args.long_tasks)
            check(started["session"]["state"] == "active", "capture_stopped")
            self.started = True
            self.transition_latest = started["session"]["elapsed_ms"]
            self.wait(self.args.settle)
            self.phase("A1", self.original["setting"]["value"])
            self.set_policy(self.original, 1 - self.original["setting"]["value"])
            self.transition_latest = self.capture.call("status", session_id=self.session_id)["session"]["elapsed_ms"]
            self.wait(self.args.settle)
            self.phase("B", 1 - self.original["setting"]["value"])
            self.restore()
            check(self.result["policy_restored"] is True, "notification_restore_uncertain")
            self.transition_latest = self.capture.call("status", session_id=self.session_id)["session"]["elapsed_ms"]
            self.wait(self.args.settle)
            self.phase("A2", self.original["setting"]["value"])
            self.restore()
        finally:
            self.capture.deadline = self.deadline
            try:
                if self.owned is not None and self.result.get("policy_restored") is not True:
                    self.restore()
            except (Exception, KeyboardInterrupt, SystemExit):
                self.result["policy_restored"] = None
                self.result["restore_error"] = "restore_not_confirmed"
            if self.started:
                self.stop_attempted = True
                try:
                    self.result["stop"] = self.capture.call("stop", session_id=self.session_id)
                except (Exception, KeyboardInterrupt, SystemExit):
                    self.result["stop_error"] = "stop_not_confirmed_ttl_applies"


def measured_summary(values):
    values = sorted(value for value in values if value is not None)
    return {"samples": len(values), "maximum": values[-1] if values else None,
            "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None}


def frame_deltas(rows, confounders, seeks=()):
    groups = {}
    for row in rows:
        if row["kind"] != "sample":
            continue
        data = row["data"]
        if row["generation"] is None or row["handle_id"] is None or row["source_id"] is None:
            confounders.add("sample_identity_unavailable")
            continue
        key = (row["lane"], row["generation"], row["handle_id"], row["source_id"], data["frames"]["source"],
               (data["attempt"] or {}).get("attachment"))
        groups.setdefault(key, []).append(row)
    total_delta, dropped_delta, intervals, position_intervals, stalled = 0, 0, 0, 0, 0
    for group in groups.values():
        for before, after in zip(group, group[1:]):
            if any(start <= after["at_ms"] and end >= before["at_ms"] for start, end in seeks):
                confounders.add("seek_crossing_interval")
                continue
            a, b = before["data"], after["data"]
            frame_a, frame_b = a["frames"], b["frames"]
            if all(frame[key] is not None for frame in (frame_a, frame_b) for key in ("total", "dropped")):
                total, dropped = frame_b["total"] - frame_a["total"], frame_b["dropped"] - frame_a["dropped"]
                if total < 0 or dropped < 0 or dropped > total:
                    confounders.add("frame_counter_reset")
                else:
                    total_delta += total
                    dropped_delta += dropped
                    intervals += 1
            if a["playback_rate"] != b["playback_rate"]:
                confounders.add("playback_rate_changed")
            if (a["position_seconds"] is not None and b["position_seconds"] is not None
                    and after["at_ms"] - before["at_ms"] >= 800
                    and all(data["paused"] is False and data["seeking"] is False and data["ended"] is False
                            and data["playback_rate"] is not None and data["playback_rate"] > 0 for data in (a, b))):
                position_intervals += 1
                stalled += b["position_seconds"] == a["position_seconds"]
    return {"frame_delta_intervals": intervals, "total_frame_delta": total_delta if intervals else None,
            "dropped_frame_delta": dropped_delta if intervals else None,
            "dropped_frame_ratio": dropped_delta / total_delta if total_delta else None,
            "position_delta_intervals": position_intervals,
            "stationary_playback_intervals": stalled if position_intervals else None}


def seek_intervals(events, finish):
    opened, intervals = {}, []
    for row in events:
        active = None
        if row["kind"] == "media" and row["data"]["event"] in ("seeking", "seeked"):
            active = row["data"]["event"] == "seeking"
        elif row["kind"] == "sample":
            active = row["data"]["seeking"]
        if active is True:
            opened.setdefault(row["lane"], row["at_ms"])
        elif active is False and row["lane"] in opened:
            intervals.append((opened.pop(row["lane"]), row["at_ms"]))
    intervals.extend((start, finish) for start in opened.values())
    return intervals


def summarize_trial(result):
    """Temporal associations only; resets and missing evidence never imply zero."""
    capture = result.get("capture")
    if not capture:
        return
    events, summaries, builds, engines = capture["events"], [], set(), set()
    confounders = set(capture["partial_reasons"])
    seeks = seek_intervals(events, capture["last_session"]["elapsed_ms"])
    if seeks:
        confounders.add("seek_activity")
    if any(len({row["source_id"] for row in events if row["lane"] == lane and row["source_id"] is not None}) > 1
           for lane in ("main", "pip", None)):
        confounders.add("source_changed")
    if any(row["kind"] in ("recovery", "abr") for row in events):
        confounders.add("engine_recovery_or_abr_change")
    for phase in result.get("phases", []):
        context = phase["context"]
        builds.add(json.dumps(context["build"], sort_keys=True))
        engines.add(json.dumps([(row["lane"], row.get("engine")) for row in context["engine_observations"]], sort_keys=True))
        if context["ui"]["documentVisibility"] != "visible" or context["ui"]["documentFocused"] is not True:
            confounders.add("visibility_or_focus_changed")
        if "end_session" not in phase:
            confounders.add("incomplete_phase")
            continue
        begin, end = phase["start_session"]["elapsed_ms"], phase["end_session"]["elapsed_ms"]
        steady = phase.get("steady_from_ms", begin + 15000)
        raw_rows = [row for row in events if begin <= row["at_ms"] < end]
        rows = [row for row in raw_rows if row["at_ms"] >= steady and
                not any(start <= row["at_ms"] <= stop for start, stop in seeks)]
        if steady > begin:
            confounders.add("phase_transition_excluded")
        loops = [row["data"] for row in rows if row["kind"] == "loop"]
        hls = [row["data"] for row in rows if row["kind"] == "hls"]
        notes = [row for row in rows if row["kind"] == "notification"]
        associated = []
        for note in notes:
            if note["data"]["stage"] != "shown" or len(associated) >= 64:
                continue
            nearby = [row for row in rows if abs(row["at_ms"] - note["at_ms"]) <= 2000]
            associated.append({"notification_id": note["data"]["notification_id"], "at_ms": note["at_ms"],
                "loop_delay_ms": measured_summary([row["data"]["max_delay_ms"] for row in nearby if row["kind"] == "loop"]),
                "hls_errors": sum(row["kind"] == "hls" and row["data"]["event"] == "error" for row in nearby)})
        summaries.append(dict(frame_deltas(rows, confounders, seeks), phase=phase["phase"], policy=phase["notification_policy"],
            elapsed_ms=end-begin, steady_from_ms=steady, excluded_events=len(raw_rows)-len(rows), events=len(rows),
            excluded_transition_ms=max(0, min(end, steady)-begin),
            loop_delay_ms=measured_summary([row["max_delay_ms"] for row in loops]),
            hls_load_ms=measured_summary([row["load_duration_ms"] for row in hls if row["event"] == "fragment"]),
            hls_buffer_ms=measured_summary([row["buffer_duration_ms"] for row in hls if row["event"] == "fragment_buffered"]),
            notification_stages={stage: sum(row["data"]["stage"] == stage for row in notes) for stage in STAGES},
            notification_timings={key: measured_summary([row["data"][key] for row in notes]) for key in TIMINGS},
            banner_associations=associated,
            banner_associations_truncated=max(0, sum(row["data"]["stage"] == "shown" for row in notes)-len(associated))))
    if len(builds) > 1:
        confounders.add("build_changed")
    if len(engines) > 1:
        confounders.add("engine_changed")
    result["analysis"] = {"phases": summaries, "confounders": sorted(confounders),
                          "conclusion": "temporal_associations_only_no_causal_claim",
                          "zero_frame_delta_is_smoothness_evidence": False}


def run(api, client, device, words, timeout, json_output=False):
    result = {"version": 1, "evaluator": "performance-v1", "device_id": device,
              "physical_display_verified": False, "started_at_unix_ms": int(time.time() * 1000)}
    journal, capture, trial, args, status = None, None, None, None, 0
    try:
        args = arguments(words)
        json_output = json_output or args.json
        result.update(operation=args.operation, read_only=args.operation not in ("start", "stop", "trial"))
        if args.operation == "trial":
            required = 3 * args.seconds_per_phase + 3 * args.settle + 60
            if timeout < required:
                result["required_timeout"] = required + 1
                raise Failure("insufficient_timeout")
        if hasattr(args, "out"):
            journal = Journal(args.out)
            result["output"] = str(journal.path)
        capture = Capture(api, client, device, timeout, journal)
        capture.setup()
        if args.operation == "start" and args.hls_details:
            check(capture.caps.get("perf_capture_hls") == HLS_CAPABILITY, "unsupported")
            capture.version = 2
        elif args.operation not in ("start", "trial") and capture.caps.get("perf_capture_hls") == HLS_CAPABILITY:
            capture.version = 2
        if capture.version == 2:
            result.update(version=2, evaluator="performance-v2")
        if args.operation == "start":
            result["session_id"] = args.session_id or secrets.token_hex(16)
            result["data"] = capture.call("start", session_id=result["session_id"], duration_seconds=args.duration,
                                          raf=args.raf, long_tasks=args.long_tasks)
        elif args.operation in ("status", "list"):
            result["data"] = capture.call("status", **({"session_id": args.session_id} if args.session_id else {}))
        elif args.operation == "read":
            result["data"] = capture.call("read", session_id=args.session_id, after_seq=args.after, limit=args.limit)
        elif args.operation == "stop":
            result["data"] = capture.call("stop", session_id=args.session_id)
        elif args.operation == "export":
            result["session_id"] = args.session_id
            result["capture"] = capture.collect(args.session_id)
            status = 0 if result["capture"]["complete"] else 3
        else:
            trial = Trial(capture, args, result)
            trial.execute()
            result["capture"] = capture.collect(trial.session_id)
            summarize_trial(result)
            status = 0 if result["capture"]["complete"] and result.get("policy_restored") is True else 3
    except Failure as error:
        result.update(reason=error.reason, unknown_outcome=error.uncertain)
        if error.confirmed is not None:
            result["confirmed_late_result"] = error.confirmed
        status = 3
    except (KeyboardInterrupt, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code == 0:
            return 0
        result.update(reason="interrupted", unknown_outcome=True)
        status = 130
    except Exception:
        result.update(reason="operation_failed", unknown_outcome=True)
        status = 3
    finally:
        if capture:
            result["requests"] = capture.receipts
        result["completed_at_unix_ms"] = int(time.time() * 1000)
        if journal:
            try:
                journal.finish(result)
            except Exception:
                result["evidence_error"] = "evidence_incomplete_preserve_directory"
                status = 3
            finally:
                journal.close()
    if json_output:
        print(encoded(result).decode().rstrip())
    else:
        brief(result)
    return status
