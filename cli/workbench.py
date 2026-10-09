"""Read-only player workbench. Fixed observations, no eval or repair effects."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

COMMANDS = frozenset(("doctor", "inspect", "operation", "bundle", "test", "debug", "dbg"))
SAFE_INTEGER = 9007199254740991
MAX_REPORT_BYTES = 256 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
REQUEST = re.compile(r"[0-9a-f]{32}\Z")
PHASES = "idle loading playing paused stopped ended error unknown".split()
OWNERS = "list about dialog editor picker unknown".split()
PANES = OWNERS + ["pin", "launch", "osd"]
REASONS = "producer_unavailable producer_failed invalid_sample state_changed_during_snapshot build_identity_partial document_hidden document_unfocused owned_overlay_open video_element_missing video_css_hidden video_zero_rect decoder_not_ready decoder_paused decoder_ended decoder_error physical_display_unverified".split()
CAPABILITIES = "screenshot diagnostics input restart_stream reload_player restart_app exit_app reboot_device standby wake".split()
CAP_REASONS = "ready not_implemented producer_unavailable remote_disconnected source_selection_required busy no_active_media current_state_unsupported policy_restricted".split()
NATIVE_EVENTS = frozenset("result_ack_failed effect_claim_failed effect_failed operation_history_reset receipt_write_failed journal_write_failed result_write_failed poll_failed invalid_poll watchdog_recover watchdog_restart_app".split())


class InvalidData(Exception):
    pass


class ObservationError(Exception):
    def __init__(self, reason):
        self.reason = reason


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")


def check(condition):
    if not condition:
        raise InvalidData()


def number(low=0, high=SAFE_INTEGER, integral=False):
    def validate(value):
        check(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)
        check(not integral or type(value) is int)
        return value
    return validate


def boolean(value):
    check(type(value) is bool)
    return value


def enum(values):
    def validate(value):
        check(isinstance(value, str) and value in values)
        return value
    return validate


def token(value):
    check(isinstance(value, str) and TOKEN.fullmatch(value))
    return value


def version_label(value):
    check(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.+\-]{1,64}", value))
    return value


def revision(value):
    check(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value))
    return value


def nullable(rule):
    return lambda value: None if value is None else project(value, rule)


def array(rule, limit):
    def validate(value):
        check(isinstance(value, list) and len(value) <= limit)
        return [project(item, rule) for item in value]
    return validate


def project(value, rule):
    if isinstance(rule, dict):
        check(isinstance(value, dict) and all(key in value for key in rule))
        return {key: project(value[key], validator) for key, validator in rule.items()}
    return rule(value)


def exact(value, rule):
    check(isinstance(value, dict) and set(value) == set(rule))
    return project(value, rule)


DEBUG_NUMBERS = "uptimeMs loopSamples loopDelayMs loopMaxDelayMs loopLongDelays jsHeapUsedBytes jsHeapTotalBytes jsHeapLimitBytes hardwareConcurrency deviceMemoryGiB errorCount rejectionCount controlPendingRequests controlPendingResponses controlConsecutiveFailures".split()
DEBUG_BOOLS = "online focused visible secureContext controlActive".split()
MEDIA_NUMBERS = "positionSeconds durationSeconds bufferAheadSeconds videoWidth videoHeight totalFrames decodedFrames droppedFrames corruptedFrames readyState networkState mediaErrorCode volume".split()
MEDIA_BOOLS = "paused ended muted seeking".split()
NATIVE_NUMBERS = "uptimeMs systemUptimeMs residentBytes pssBytes footprintBytes heapUsedBytes heapLimitBytes systemAvailableBytes systemTotalBytes thermalState logicalProcessors nativeHlsSessions nativeHlsBytes nativeHlsErrors epgChannels epgProgrammes epgMappings epgShifts requestsTotal requestsActive requestsFailed".split()
NATIVE_BOOLS = "lowMemory foreground lowPower".split()
DEBUG_EVENTS = "started resumed suspended visible hidden online offline focus blur error unhandled_rejection loop_delay media_error media_waiting media_stalled media_playing media_ended".split()


def debug_metrics(numbers, bools, limits=None):
    def validate(value):
        check(isinstance(value, dict) and all(key in numbers or key in bools for key in value))
        return {key: (boolean(item) if key in bools else number(high=(limits or {}).get(key, SAFE_INTEGER))(item))
                for key, item in value.items()}
    return validate


def debug_native(value):
    check(isinstance(value, dict) and set(value) == {"state", "data"})
    state = enum("available unsupported unavailable timeout invalid".split())(value["state"])
    if state != "available":
        check(value["data"] is None)
        return {"state": state, "data": None}
    return {"state": state, "data": exact(value["data"], {
        "version": number(1, 1, True), "platform": enum("android ios tauri server".split()),
        "appVersion": nullable(version_label), "osVersion": nullable(version_label),
        "webviewVersion": nullable(version_label), "metrics": debug_metrics(NATIVE_NUMBERS, NATIVE_BOOLS)})}


def debug_metadata(raw, runtime):
    result = exact(inspection_data(raw, runtime, "debug"), {
        "version": number(1, 1, True), "runtime": token, "capturedAt": number(integral=True),
        "platform": enum("browser webos capacitor-android capacitor-ios tauri unknown".split()),
        "metrics": debug_metrics(DEBUG_NUMBERS, DEBUG_BOOLS),
        "media": array(lambda value: exact(value, {
            "lane": enum(["main", "pip"]), "generation": nullable(number(integral=True)),
            "handleId": nullable(number(integral=True)),
            "metrics": debug_metrics(MEDIA_NUMBERS, MEDIA_BOOLS, {"volume": 1, "readyState": 4, "networkState": 3, "mediaErrorCode": 4})}), 2),
        "events": array(lambda value: exact(value, {
            "sequence": number(1, SAFE_INTEGER, True), "elapsedMs": number(), "code": enum(DEBUG_EVENTS)}), 32),
        "eventsDropped": number(integral=True), "native": debug_native,
    })
    check(result["runtime"] == runtime)
    check(len({row["lane"] for row in result["media"]}) == len(result["media"]))
    sequence = [row["sequence"] for row in result["events"]]
    check(all(a < b for a, b in zip(sequence, sequence[1:])))
    return result


RECT = {"x": number(-32768, 32768, True), "y": number(-32768, 32768, True),
        "width": number(0, 32768, True), "height": number(0, 32768, True)}
VIDEO = {"exists": boolean, "cssVisible": nullable(boolean), "rect": nullable(RECT),
         "paused": nullable(boolean), "ended": nullable(boolean),
         "readyState": nullable(number(0, 4, True)), "networkState": nullable(number(0, 3, True)),
         "videoWidth": nullable(number(0, 32768, True)), "videoHeight": nullable(number(0, 32768, True))}
LANE = {"lane": enum(["main", "pip"]), "handleId": nullable(number(integral=True)),
        "phase": enum(PHASES), "position": nullable(number(0, 315576000)),
        "duration": nullable(number(0, 315576000)), "video": VIDEO}
SNAPSHOT = {
    "version": number(1, 1, True), "runtime": token, "capturedAt": number(integral=True),
    "collectionMs": nullable(number(0, 60000)), "consistent": boolean,
    "build": {"version": version_label, "sourceRevision": nullable(revision), "buildId": nullable(token),
              "identity": enum(["embedded", "partial"])},
    "ui": {"documentVisibility": enum(["visible", "hidden", "unknown"]), "documentFocused": nullable(boolean),
           "owner": nullable({"kind": enum(OWNERS), "id": number(integral=True)}),
           "revision": nullable(number(integral=True)),
           "panes": array({"kind": enum(PANES), "exists": boolean, "cssVisible": nullable(boolean), "rect": nullable(RECT)}, 8),
           "focus": enum(PANES + ["player", "body", "other"])},
    "media": {"generation": nullable(number(integral=True)), "kind": enum(["live", "archive", "vod", "none", "unknown"]),
              "phase": enum(PHASES), "lanes": array(LANE, 2), "displayEvidence": enum(["unavailable"])},
    "capabilities": array({"name": enum(CAPABILITIES), "state": enum(["available", "unavailable", "unknown"]),
                           "reason": enum(CAP_REASONS)}, 10),
    "reasons": array(enum(REASONS), 16),
}


def inspection_data(raw, runtime, section):
    check(isinstance(raw, dict) and raw.get("version") == 1 and type(raw.get("version")) is int)
    check(set(raw) == {"version", "runtime", "section", "error" if "error" in raw else "data"})
    check(len(json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) <= 32768)
    check(raw.get("runtime") == runtime and raw.get("section") == section)
    if "error" in raw:
        reason = raw["error"]
        check(reason in ("invalid_request", "runtime_mismatch", "unsupported", "unavailable"))
        raise ObservationError(reason)
    data = raw.get("data")
    # Measure the wire-shaped object, including unknown fields, before projection.
    check(len(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) <= (16384 if section == "debug" else 8192))
    return data


def snapshot_metadata(raw, runtime, section):
    data = inspection_data(raw, runtime, section)
    result = project(data, SNAPSHOT)
    check(result["runtime"] == runtime)
    check(len({row["lane"] for row in result["media"]["lanes"]}) == len(result["media"]["lanes"]))
    check(len({row["name"] for row in result["capabilities"]}) == len(result["capabilities"]))
    return result


def operation_metadata(raw, runtime, operation_id):
    result = project(inspection_data(raw, runtime, "operation"), {
        "operation_id": enum([operation_id]),
        "state": enum("unknown accepted invoked observed rejected unsupported expired".split()),
        "action": nullable(enum("command play provider profile profile_settings provider_settings kiosk restart lifecycle input playback aspect play_catalog play_archive_catalog vportal vportal_search vportal_random plex_queue vportal_queue maintenance".split())),
        "evidence": {"kind": enum("none handler_completed media_progress runtime_changed".split()),
                     "generation": nullable(number(integral=True)), "position": nullable(number(0, 315576000))},
    })
    evidence = result["evidence"]
    check(result["state"] != "observed" or evidence["kind"] in ("media_progress", "runtime_changed"))
    check(evidence["kind"] != "media_progress" or evidence["generation"] is not None and evidence["position"] is not None)
    return result


def native_metadata(api, raw, operation, expected_runtime=None):
    try:
        data = api.android_metadata(raw, "maintenance", {"operation": operation})
    except api.Error:
        raise InvalidData() from None
    if operation == "logs":
        # The legacy native projector permits null optional values. Log entries
        # used as workbench evidence must still have typed event/time fields.
        for row in data["events"]:
            check(isinstance(row, dict) and isinstance(row.get("event"), str))
            number()(row.get("time"))
        runtimes = [data.get("runtime")] + [row.get("runtime") for row in data["events"]]
        available = isinstance(expected_runtime, str) and all(isinstance(value, str) and value for value in runtimes)
        correlation = ("matched" if all(value == expected_runtime for value in runtimes) else "mismatch") if available else "unavailable"
        return {"version": 1, "events": [{key: row[key] for key in ("time", "event", "runtime", "boot_id") if key in row}
                for row in data["events"] if row.get("event") in NATIVE_EVENTS and "time" in row],
                "unknown_events_omitted": sum(row.get("event") not in NATIVE_EVENTS for row in data["events"]),
                "runtime_correlation": correlation}
    # Do not export provider titles, URLs, arbitrary event text or queue contents.
    result = {key: data[key] for key in ("version", "agent_version", "runtime", "boot_id", "operation_history_reset", "app_pid", "uptime_seconds",
              "battery_percent", "watchdog_suspended", "watchdog_attempts", "webview_responsive", "system_evidence") if key in data}
    player = data.get("player")
    if isinstance(player, dict):
        result["player"] = {}
        if type(player.get("ready")) is bool:
            result["player"]["ready"] = player["ready"]
        if isinstance(player.get("video"), dict):
            result["player"]["video"] = {key: value for key, value in player["video"].items() if key != "source"}
    if isinstance(player, dict) and isinstance(player.get("decoder"), dict):
        result["player"]["decoder"] = player["decoder"]
    result["media_identity_available"] = False
    identity = player.get("identity") if isinstance(player, dict) else None
    if identity is not None:
        check(type(identity.get("version")) is int and identity["version"] == 1 and type(identity.get("available")) is bool)
        if identity["available"]:
            check(identity.get("consistent") is True and isinstance(identity.get("web_runtime"), str)
                  and identity.get("kind") in ("live", "archive", "vod"))
            number(integral=True)(identity.get("generation"))
            number(integral=True)(identity.get("handle_id"))
            number(1)(identity.get("captured_at"))
        result["player"]["identity"] = identity
        result["media_identity_available"] = identity["available"]
    result["physical_display_verified"] = False
    return result


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Workbench:
    def __init__(self, api, client, device, timeout):
        self.api, self.client, self.device = api, client, device
        self.deadline = time.monotonic() + timeout

    def native_target(self):
        bindings = self.client.config.get("native_devices", {})
        if not isinstance(bindings, dict):
            raise ObservationError("invalid_native_binding")
        native = bindings.get(self.device)
        if native is None:
            raise ObservationError("not_bound")
        if (not isinstance(native, str) or native == self.device or native in bindings or self.device in bindings.values()
                or sum(target == native for target in bindings.values()) != 1
                or not any(row.get("id") == native for row in self.client.credentials.get("devices", []))):
            raise ObservationError("invalid_native_binding")
        return native

    def request(self, target, action, params, deadline, receipts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("deadline_exceeded")
        previous = self.client.timeout
        try:
            self.client.timeout = remaining
            receipt = self.client.call_receipt(target, action, params)
            check(isinstance(receipt, dict) and isinstance(receipt.get("request_id"), str)
                  and REQUEST.fullmatch(receipt["request_id"]) and receipt.get("status") == "ok")
            receipts.append({"request_id": receipt["request_id"], "action": action, "status": "ok"})
            if time.monotonic() >= deadline:
                raise ObservationError("deadline_exceeded")
            return receipt["data"]
        except self.api.Error as exc:
            request_id = getattr(exc, "request_id", None)
            if isinstance(request_id, str) and REQUEST.fullmatch(request_id):
                receipts.append({"request_id": request_id, "action": action, "status": "unconfirmed"})
            if action == "inspect" and isinstance(exc, (self.api.PlayerRejected, self.api.PlayerUnsupported)):
                # Even negative observations need the runtime/section fence.
                # Unbound legacy errors must not be attributed to this page.
                data = getattr(exc, "data", None)
                check(isinstance(data, dict) and "error" in data)
                inspection_data(data, params["runtime"], params["section"])
            raise
        finally:
            self.client.timeout = previous

    def reason(self, exc):
        if isinstance(exc, ObservationError):
            return exc.reason
        if isinstance(exc, (InvalidData, ValueError, TypeError, KeyError, OverflowError, RecursionError)):
            return "invalid_response"
        if isinstance(exc, self.api.PlayerUnsupported):
            return "unsupported"
        if isinstance(exc, self.api.PlayerRejected):
            reason = exc.data.get("error") if isinstance(exc.data, dict) else None
            return reason if reason in ("invalid_request", "runtime_mismatch", "unsupported", "unavailable") else "rejected"
        if isinstance(exc, self.api.HTTPError):
            if exc.code in (400, 404, 405, 422):
                return "unsupported_controller"
            if exc.code in (401, 403):
                return "unauthorized"
        return "unavailable"

    def observe_lane(self, lane, section, deadline, logs=False, operation_id=None):
        observation = {"lane": lane, "source": "runtime_reported", "observed_at": now(),
                       "status": "unknown", "requests": []}
        receipts = observation["requests"]
        try:
            if lane == "native":
                target = self.native_target()
                raw = self.request(target, "maintenance", {"operation": "health"}, deadline, receipts)
                if section == "operation":
                    try:
                        checked = self.api.android_metadata(raw, "maintenance", {"operation": "health"})
                        data = self.api.native_operation_metadata(checked, operation_id)
                    except self.api.Error:
                        raise InvalidData() from None
                else:
                    data = native_metadata(self.api, raw, "health")
            else:
                raw = self.request(self.device, "capabilities", {}, deadline, receipts)
                caps = self.api.capabilities_metadata(raw)
                observation["capabilities"] = caps
                supported = "debug" in caps if section == "debug" else section in caps.get("inspect", {}).get("sections", [])
                if not supported:
                    raise ObservationError("unsupported")
                params = {"version": 1, "runtime": caps["player"]["runtime"], "section": section}
                if section == "operation":
                    params["operation_id"] = operation_id
                raw = self.request(self.device, "inspect", params, deadline, receipts)
                data = (debug_metadata(raw, caps["player"]["runtime"]) if section == "debug"
                        else operation_metadata(raw, caps["player"]["runtime"], operation_id) if section == "operation"
                        else snapshot_metadata(raw, caps["player"]["runtime"], section))
            observation.update(status="observed", data=data, runtime=data["runtime"] if lane == "native" or section != "operation" else caps["player"]["runtime"])
            if logs and lane == "native":
                try:
                    raw = self.request(target, "maintenance", {"operation": "logs"}, deadline, receipts)
                    observation["logs"] = native_metadata(self.api, raw, "logs", data["runtime"])
                except (self.api.Error, ObservationError, InvalidData, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                    observation["logs_error"] = self.reason(exc)
        except (self.api.Error, ObservationError, InvalidData, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            observation["reason"] = self.reason(exc)
        return observation

    def observe(self, lane="auto", section="snapshot", logs=False, operation_id=None):
        # Split the original budget: an offline page must not starve a native agent.
        lanes = [lane] if lane != "auto" else ["web", "native"]
        results = []
        for index, name in enumerate(lanes):
            remaining = max(0, self.deadline - time.monotonic())
            deadline = time.monotonic() + remaining / (len(lanes) - index)
            results.append(self.observe_lane(name, section, deadline, logs, operation_id))
        return results


def health_verdict(observations):
    failed, unknown = False, False
    checked = 0
    for row in observations:
        if row.get("reason") == "not_bound":
            continue
        checked += 1
        if row["status"] != "observed":
            unknown = True
        elif row["lane"] == "web":
            unknown |= not row["data"]["consistent"]
        else:
            failed |= row["data"]["webview_responsive"] is False
    return "fail" if failed else "unknown" if unknown or not checked else "pass"


def native_progress_verdict(before, after, sample_interval=None):
    """Observe one native WebView's decoder; surface/audio counters are not proof."""
    if (len(before) != 1 or len(after) != 1
            or any(row.get("lane") != "native" or row.get("status") != "observed"
                   for row in (before[0], after[0]))):
        return "unknown", "media_identity_unavailable"
    schema = {
        "version": number(1, 1, True), "runtime": token, "boot_id": token,
        "app_pid": number(1, 2147483647, True), "uptime_seconds": number(0.001),
        "webview_responsive": boolean, "media_identity_available": boolean,
        "system_evidence": {"app_pid": number(1, 2147483647, True),
                            "captured_uptime_seconds": number(0.001)},
        "player": {
            "ready": boolean,
            "identity": {"version": number(1, 1, True), "available": boolean,
                         "consistent": boolean, "web_runtime": token,
                         "generation": number(integral=True), "handle_id": number(integral=True),
                         "kind": enum(["live", "archive", "vod"]),
                         "captured_at": number(1, SAFE_INTEGER, True)},
            "decoder": {"source": enum(["html_video"])},
            "video": {"position": number(0, 315576000), "paused": boolean, "ended": boolean,
                      "ready": number(0, 4, True), "error": number(0, 4, True),
                      "width": number(1, 32768, True), "height": number(1, 32768, True)},
        },
    }
    try:
        a, b = [project(row.get("data"), schema) for row in (before[0], after[0])]
        for value in (a, b):
            check(re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["boot_id"]))
    except InvalidData:
        # Old agents and incomplete health replies remain useful to inspect,
        # but cannot establish continuity for this scenario.
        return "unknown", "media_identity_unavailable"
    if (not isinstance(sample_interval, (tuple, list)) or len(sample_interval) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in sample_interval)
            or not 0 <= sample_interval[0] <= sample_interval[1] <= 300 or sample_interval[1] == 0):
        return "unknown", "host_sample_interval_unavailable"
    ia, ib = a["player"]["identity"], b["player"]["identity"]
    if (any(a[key] != b[key] for key in ("runtime", "boot_id", "app_pid"))
            or any(ia[key] != ib[key] for key in ("web_runtime", "generation", "handle_id", "kind"))
            or any(not value["media_identity_available"] or not value["player"]["identity"]["available"]
                   or not value["player"]["identity"]["consistent"]
                   or value["system_evidence"]["app_pid"] != value["app_pid"] for value in (a, b))):
        return "unknown", "media_identity_changed_or_unavailable"
    elapsed = (ib["captured_at"] - ia["captured_at"]) / 1000
    uptime_elapsed = b["uptime_seconds"] - a["uptime_seconds"]
    collected_elapsed = (b["system_evidence"]["captured_uptime_seconds"]
                         - a["system_evidence"]["captured_uptime_seconds"])
    if elapsed <= 0 or uptime_elapsed <= 0 or collected_elapsed <= 0:
        return "unknown", "observation_changed_or_stale"
    if (any(not sample_interval[0] - 1 <= delta <= sample_interval[1] + 1
            for delta in (elapsed, uptime_elapsed, collected_elapsed))
            or any(not 0 <= value["system_evidence"]["captured_uptime_seconds"] - value["uptime_seconds"]
                   <= sample_interval[1] + 1 for value in (a, b))):
        return "unknown", "sample_clock_discontinuity"
    if any(not value["webview_responsive"] or not value["player"]["ready"] for value in (a, b)):
        return "unknown", "decoder_state_unavailable"
    va, vb = a["player"]["video"], b["player"]["video"]
    if any(value["paused"] or value["ended"] or value["ready"] < 2 or value["error"] != 0 for value in (va, vb)):
        return "unknown", "media_not_continuously_playing"
    delta = vb["position"] - va["position"]
    if delta < 0 or delta > sample_interval[1] * 4 + 1:
        return "unknown", "position_discontinuity"
    if delta == 0:
        return "fail", "media_did_not_progress"
    return "pass", "same_native_media_progress_observed"


def progress_verdict(before, after, sample_interval=None):
    # Only an explicitly selected native lane has this shape. Auto retains web
    # authority even when its web observation fails and native health succeeds.
    if (len(before) == 1 and len(after) == 1
            and before[0].get("lane") == "native" and after[0].get("lane") == "native"):
        return native_progress_verdict(before, after, sample_interval)
    first = next((row for row in before if row["lane"] == "web" and row["status"] == "observed"), None)
    last = next((row for row in after if row["lane"] == "web" and row["status"] == "observed"), None)
    if not first or not last:
        return "unknown", "media_identity_unavailable"
    a, b = first["data"], last["data"]
    if any("invalid_sample" in value["reasons"]
           or type(value["capturedAt"]) not in (int, float)
           or not math.isfinite(value["capturedAt"]) or value["capturedAt"] <= 0
           or type(value["collectionMs"]) not in (int, float)
           or not math.isfinite(value["collectionMs"]) or not 0 <= value["collectionMs"] <= 60000
           for value in (a, b)):
        return "unknown", "sample_clock_unavailable"
    if (not isinstance(sample_interval, (tuple, list)) or len(sample_interval) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in sample_interval)
            or not 0 <= sample_interval[0] <= sample_interval[1] <= 300 or sample_interval[1] == 0):
        return "unknown", "host_sample_interval_unavailable"
    if (not a["consistent"] or not b["consistent"] or a["runtime"] != b["runtime"]
            or a["media"]["generation"] is None or a["media"]["generation"] != b["media"]["generation"]):
        return "unknown", "media_identity_changed_or_unavailable"
    la = next((row for row in a["media"]["lanes"] if row["lane"] == "main"), None)
    lb = next((row for row in b["media"]["lanes"] if row["lane"] == "main"), None)
    if (not la or not lb or la["handleId"] is None or la["handleId"] != lb["handleId"]
            or la["position"] is None or lb["position"] is None):
        return "unknown", "media_identity_changed_or_unavailable"
    if (b["capturedAt"] <= a["capturedAt"] or a["media"]["kind"] != b["media"]["kind"]
            or a["media"]["kind"] in ("none", "unknown")):
        return "unknown", "observation_changed_or_stale"
    elapsed = (b["capturedAt"] - a["capturedAt"]) / 1000
    # Device wall clocks can jump. Compare them with the host's monotonic
    # collection windows, allowing one second for coarse device clocks.
    if not sample_interval[0] - 1 <= elapsed <= sample_interval[1] + 1:
        return "unknown", "sample_clock_discontinuity"
    if la["phase"] != "playing" or lb["phase"] != "playing":
        return "unknown", "media_not_continuously_playing"
    if (la["video"]["paused"] is not False or lb["video"]["paused"] is not False
            or la["video"]["ended"] is not False or lb["video"]["ended"] is not False
            or not la["video"]["exists"] or not lb["video"]["exists"]
            or la["video"]["readyState"] is None or lb["video"]["readyState"] is None
            or la["video"]["readyState"] < 2 or lb["video"]["readyState"] < 2):
        return "unknown", "decoder_state_unavailable"
    delta = lb["position"] - la["position"]
    if delta < 0:
        return "unknown", "position_discontinuity"
    if delta == 0:
        return "fail", "media_did_not_progress"
    if delta > sample_interval[1] * 4 + 1:
        return "unknown", "position_discontinuity"
    return "pass", "same_media_progress_observed"


def write_report(directory, report):
    """Create private, non-overwriting evidence. Manifest is the commit marker."""
    raw = encoded(report)
    if len(raw) > MAX_REPORT_BYTES:
        raise ObservationError("report_too_large")
    target = Path(directory).expanduser().absolute()
    directory_fd = None
    try:
        if os.name == "posix":
            # Walk with dirfds so swapping an ancestor for a symlink cannot
            # redirect an export between its check and the next write.
            directory_fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for part in target.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            try:
                os.mkdir(target.name, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                raise ObservationError("output_exists") from None
            next_fd = os.open(target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        else:
            for parent in [*reversed(target.parents), target]:
                if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                    raise ObservationError("unsafe_output_path")
            try:
                target.mkdir(mode=0o700)
            except FileExistsError:
                raise ObservationError("output_exists") from None
        def save(name, data):
            temporary = "." + name + ".partial"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary if directory_fd is not None else target / temporary, flags, 0o600,
                         **({"dir_fd": directory_fd} if directory_fd is not None else {}))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if directory_fd is not None:
                # link is atomic and refuses to replace an existing name.
                os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=directory_fd)
            else:
                # Windows rename refuses an existing destination.
                os.rename(target / temporary, target / name)
        save("result.json", raw)
        manifest = {"version": 1, "complete": True, "files": [{"name": "result.json", "bytes": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest()}], "physical_display_verified": False}
        save("manifest.json", encoded(manifest))
        if directory_fd is not None:
            os.fsync(directory_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return str(target)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ObservationError("invalid_arguments")


def arguments(words, json_output):
    parser = Parser(prog="ott PLAYER " + words[0], add_help=True)
    parser.add_argument("-j", "--json", action="store_true", default=json_output)
    parser.add_argument("--lane", choices=("auto", "web", "native"), default="auto")
    command = words[0]
    if command == "debug":
        parser.set_defaults(lane="web")
    if command == "inspect":
        parser.add_argument("--view", default="ui,media")
    elif command == "operation":
        parser.add_argument("operation_id")
        parser.set_defaults(lane="web")
    elif command == "bundle":
        parser.add_argument("--out", required=True)
    elif command == "test":
        parser.add_argument("action", choices=("list", "run"))
        parser.add_argument("scenario", nargs="?", choices=("health", "media-progress"))
        parser.add_argument("--duration", type=float, default=5)
        parser.add_argument("--report")
    parsed = parser.parse_args(words[1:])
    if command == "debug" and parsed.lane != "web":
        raise ObservationError("invalid_arguments")
    if command == "operation" and (not REQUEST.fullmatch(parsed.operation_id) or parsed.lane not in ("web", "native")):
        raise ObservationError("invalid_arguments")
    if command == "inspect":
        views = parsed.view.split(",")
        if not views or len(set(views)) != len(views) or any(view not in ("ui", "media") for view in views):
            raise ObservationError("invalid_arguments")
    if command == "test":
        if (parsed.action == "run" and (not parsed.scenario or not parsed.report)
                or parsed.action == "list" and (parsed.scenario or parsed.report)
                or not math.isfinite(parsed.duration) or not 1 <= parsed.duration <= 30):
            raise ObservationError("invalid_arguments")
    return parsed


def run(api, client, device, words, timeout, json_output=False):
    """CLI entry point with bounded, machine-readable failures and no mutations."""
    command = words[0].casefold()
    command = "debug" if command == "dbg" else command
    json_output = json_output or any(word in ("-j", "--json") for word in words[1:])
    result = {"version": 1, "command": command, "device_id": device, "started_at": now(),
              "read_only": True, "physical_display_verified": False, "evaluator": "workbench-v2"}
    try:
        args = arguments([command, *words[1:]], json_output)
        json_output = args.json
        result["tooling"] = {"workbench_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                             "cli_sha256": hashlib.sha256(Path(api.__file__).read_bytes()).hexdigest()}
        if command == "test" and args.action == "list":
            result.update(verdict="observed", scenarios=[
                {"name": "health", "read_only": True, "checks": "management observations; native WebView responsiveness"},
                {"name": "media-progress", "read_only": True,
                 "checks": "same media identity and decoder position; web by default, explicit --lane native supported"}])
        else:
            bench = Workbench(api, client, device, timeout)
            if command == "bundle":
                result["evaluator"] = "workbench-v3"
            if command == "test" and args.scenario == "media-progress":
                if timeout <= args.duration + 1:
                    raise ObservationError("insufficient_timeout")
                # Reserve both sampling windows plus the requested observation gap.
                final_deadline = bench.deadline
                bench.deadline = time.monotonic() + (timeout - args.duration) / 2
                first_started = time.monotonic()
                before = bench.observe(args.lane)
                first_finished = time.monotonic()
                bench.deadline = final_deadline
                time.sleep(min(args.duration, max(0, bench.deadline - time.monotonic())))
                second_started = time.monotonic()
                after = bench.observe(args.lane)
                second_finished = time.monotonic()
                sample_interval = (second_started - first_finished, second_finished - first_started)
                verdict, reason = progress_verdict(before, after, sample_interval)
                result.update(scenario=args.scenario, verdict=verdict, reason=reason,
                              duration_seconds=args.duration, observations=before, final_observations=after,
                              sample_interval_seconds={"min": sample_interval[0], "max": sample_interval[1]},
                              evidence_level="decoder_progress_only")
            else:
                final_deadline = bench.deadline
                if command == "bundle" and args.lane != "native":
                    bench.deadline = time.monotonic() + max(0, final_deadline - time.monotonic()) * 0.75
                observations = bench.observe(args.lane, "debug" if command == "debug" else "doctor" if command == "doctor" else "operation" if command == "operation" else "snapshot",
                                             logs=command == "bundle", operation_id=args.operation_id if command == "operation" else None)
                bench.deadline = final_deadline
                if command == "bundle":
                    result["debug_observations"] = bench.observe("web", "debug") if args.lane != "native" else []
                verdict = "observed" if any(row["status"] == "observed" for row in observations) else "unknown"
                if command == "operation":
                    result["operation_id"] = args.operation_id
                    if observations[0]["status"] == "observed":
                        result["operation_state"] = observations[0]["data"]["state"]
                        if result["operation_state"] == "unknown":
                            verdict = "unknown"
                if command == "test":
                    verdict = health_verdict(observations)
                    result["scenario"] = args.scenario
                if command == "inspect":
                    for row in observations:
                        if row["lane"] == "web" and "data" in row:
                            for section in ("ui", "media"):
                                if section not in args.view.split(","):
                                    row["data"].pop(section, None)
                result.update(verdict=verdict, observations=observations)
            result["completed_at"] = now()
            output = args.out if command == "bundle" else args.report if command == "test" else None
            if output:
                result["report_directory"] = write_report(output, result)
        result.setdefault("completed_at", now())
    except (ObservationError, InvalidData, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        reason = exc.reason if isinstance(exc, ObservationError) else "output_or_validation_failed"
        result.update(verdict="error", reason=reason, completed_at=now())
    raw = encoded(result)
    if len(raw) > MAX_REPORT_BYTES:
        result = {"version": 1, "verdict": "error", "reason": "report_too_large", "read_only": True,
                  "physical_display_verified": False, "evaluator": "workbench-v2"}
    if json_output:
        print(encoded(result).decode("utf-8"), end="")
    else:
        print("Workbench " + command + ": " + result["verdict"])
        if result.get("reason"):
            print("Reason: " + result["reason"])
        for row in result.get("observations", []):
            print("  " + row["lane"] + ": " + row["status"] + (" (" + row["reason"] + ")" if row.get("reason") else ""))
        if result.get("scenarios"):
            for row in result["scenarios"]:
                print("  " + row["name"] + " — " + row["checks"])
        if result.get("report_directory"):
            print("Report: " + result["report_directory"])
        if command in ("doctor", "inspect", "operation", "debug"):
            print(encoded(result).decode("utf-8"), end="")
    return {"observed": 0, "pass": 0, "fail": 2, "unknown": 3, "error": 1}[result["verdict"]]
