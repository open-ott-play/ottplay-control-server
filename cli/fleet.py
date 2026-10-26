"""Finite, read-only player monitoring using the existing inspection protocol."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import copy
import datetime
import importlib.util
import json
import math
from pathlib import Path
import re
import threading
import time


DEVICE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
MAX_TARGETS = 64
MAX_OBSERVATIONS = 256
MAX_REPORT_BYTES = 1024 * 1024
ERROR_REASONS = frozenset(("controller_unavailable", "controller_http_error", "request_pending_timeout",
                           "receipt_missing", "unauthorized", "rate_limited", "invalid_response"))


class Failure(Exception):
    def __init__(self, reason):
        self.reason = reason


def check(value, reason="invalid_response"):
    if not value:
        raise Failure(reason)


def public_alias(value):
    check(isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= 512)
    value = "".join(character for character in value if character.isprintable()).strip()
    check(bool(value))
    return "[redacted alias]" if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", value) else value


def inventory_metadata(raw, config):
    """Project one server inventory without inferring liveness from last_seen."""
    check(isinstance(raw, dict) and isinstance(raw.get("devices"), list) and len(raw["devices"]) <= 128)
    registered = {}
    for item in raw["devices"]:
        check(isinstance(item, dict) and isinstance(item.get("id"), str) and DEVICE_ID.fullmatch(item["id"])
              and type(item.get("pending")) is int and 0 <= item["pending"] <= 9007199254740991)
        seen = item.get("last_seen")
        if seen is not None:
            check(isinstance(seen, str) and len(seen) <= 40)
            try:
                stamp = datetime.datetime.fromisoformat(seen.replace("Z", "+00:00"))
                check(stamp.tzinfo is not None and stamp.utcoffset() is not None)
                seen = stamp.isoformat()
            except (ValueError, OverflowError):
                raise Failure("invalid_response") from None
        row = {"id": item["id"], "aliases": [], "last_seen": seen, "pending": item["pending"]}
        # Older inventories can repeat an identical row. Never hide conflicts.
        check(item["id"] not in registered or registered[item["id"]] == row)
        registered[item["id"]] = row
    aliases = config.get("players", {})
    check(isinstance(aliases, dict) and len(aliases) <= 1024)
    stale, alias_bytes = [], 0
    for alias, device in aliases.items():
        alias = public_alias(alias)
        check(isinstance(device, str) and DEVICE_ID.fullmatch(device))
        alias_bytes += len(alias.encode("utf-8"))
        check(alias_bytes <= 65536)
        if device in registered:
            registered[device]["aliases"].append(alias)
        else:
            stale.append({"alias": alias, "id": device})
    for row in registered.values():
        row["aliases"].sort(key=str.casefold)
    return {"version": 1, "devices": [registered[key] for key in sorted(registered)],
            "stale_aliases": sorted(stale, key=lambda row: (row["alias"].casefold(), row["id"]))}


def error_reason(api, error):
    if isinstance(error, Failure):
        return error.reason
    if isinstance(error, api.RequestError):
        return error.reason if error.reason in ERROR_REASONS else "invalid_response"
    if isinstance(error, api.TransportError):
        return "controller_unavailable"
    if isinstance(error, api.HTTPError):
        return "unauthorized" if error.code in (401, 403) else "rate_limited" if error.code == 429 else "controller_http_error"
    return "invalid_response"


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise Failure("invalid_arguments")


def arguments(words, json_output=False):
    parser = Parser(prog="ott PLAYER[,PLAYER...] watch",
                    description="Observe player snapshots for a finite duration; never change playback.")
    parser.add_argument("--duration", type=float, default=30, help="Total collection deadline in seconds (2–300; default 30)")
    parser.add_argument("--interval", type=float, default=5, help="Minimum interval between round starts (1–60; default 5)")
    parser.add_argument("--request-timeout", type=float, default=5, help="Per-player observation budget (1–30 seconds; default 5)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent players (1–4; default 4)")
    parser.add_argument("-j", "--json", action="store_true", default=json_output)
    result = parser.parse_args(words)
    check(all(math.isfinite(value) for value in (result.duration, result.interval, result.request_timeout)), "invalid_arguments")
    check(2 <= result.duration <= 300 and 1 <= result.interval <= 60 and result.interval < result.duration
          and 1 <= result.request_timeout <= 30 and 1 <= result.workers <= 4, "invalid_arguments")
    return result


def workbench_module():
    path = Path(__file__).resolve().with_name("workbench.py")
    check(path.is_file(), "missing_workbench")
    spec = importlib.util.spec_from_file_location("ott_fleet_workbench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReadOnlyClient:
    """Keep each worker's timeout private and reject any additional RPC."""
    def __init__(self, api, client, device, stopped):
        self.api = api
        self.client = copy.copy(client)
        self.config = client.config
        self.timeout = client.timeout
        self.device = device
        self.stopped = stopped

    def call_receipt(self, device, action, params):
        allowed = ((action == "capabilities" and params == {}) or
                   (action == "inspect" and isinstance(params, dict)
                    and set(params) == {"version", "runtime", "section"}
                    and type(params["version"]) is int and params["version"] == 1
                    and params["section"] == "snapshot" and isinstance(params["runtime"], str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", params["runtime"])))
        if self.stopped.is_set() or device != self.device or not allowed:
            raise self.api.Error("Read-only observation stopped")
        self.client.timeout = self.timeout
        return self.client.call_receipt(device, action, params)


def collect_player(api, workbench, client, device, budget, deadline, stopped):
    started = time.monotonic()
    budget = min(budget, max(0, deadline - started))
    if budget <= 0 or stopped.is_set():
        return started, started, None
    reader = ReadOnlyClient(api, client, device, stopped)
    observation = workbench.Workbench(api, reader, device, budget).observe("web", "snapshot")[0]
    return started, time.monotonic(), observation


def project_sample(round_number, origin, collected, previous, workbench):
    started, finished, observation = collected
    sample = {"round": round_number, "started_seconds": round(max(0, started - origin), 3),
              "completed_seconds": round(max(0, finished - origin), 3), "status": "unknown", "requests": []}
    if observation is None:
        sample["reason"] = "deadline_exceeded"
        return sample
    sample["requests"] = observation.get("requests", [])
    sample["status"] = observation["status"]
    if observation["status"] != "observed":
        sample["reason"] = observation["reason"]
        return sample
    data = observation["data"]
    main = next((lane for lane in data["media"]["lanes"] if lane["lane"] == "main"), None)
    sample.update(runtime=data["runtime"], generation=data["media"]["generation"],
                  phase=data["media"]["phase"], kind=data["media"]["kind"], consistent=data["consistent"],
                  document_visibility=data["ui"]["documentVisibility"], captured_at_ms=data["capturedAt"], build=data["build"])
    activity = observation.get("capabilities", {}).get("player", {})
    if activity.get("runtime") == data["runtime"]:
        sample["player"] = {key: activity[key] for key in ("version", "platform")}
        for field in ("standby", "background"):
            if field in activity:
                sample[field] = activity[field]
        if "standby" in sample or "background" in sample:
            sample["activity_source"] = "capabilities_before_snapshot_not_atomic"
    if main:
        sample["decoder"] = {"handle_id": main["handleId"], "phase": main["phase"], "position": main["position"],
                             "paused": main["video"]["paused"], "ended": main["video"]["ended"],
                             "ready_state": main["video"]["readyState"], "css_visible": main["video"]["cssVisible"]}
    if previous is not None and previous[2] is not None:
        first_started, first_finished, before = previous
        interval = (max(0, started - first_finished), max(0, finished - first_started))
        verdict, reason = workbench.progress_verdict([before], [observation], interval)
        # Background throttling and standby are expected to stop a decoder.
        # These activity observations are runtime-correlated, not atomic.
        for current in (before, observation):
            if current.get("status") != "observed":
                continue
            if current.get("capabilities", {}).get("player", {}).get("standby") is True:
                verdict, reason = "unknown", "standby_reported"
                break
            visibility = current["data"]["ui"]["documentVisibility"]
            if visibility != "visible":
                verdict, reason = "unknown", "document_hidden" if visibility == "hidden" else "visibility_unavailable"
                break
            lane = next((item for item in current["data"]["media"]["lanes"] if item["lane"] == "main"), None)
            if lane and lane["video"]["cssVisible"] is False:
                verdict, reason = "unknown", "video_not_visible"
                break
        sample["progress"] = {"verdict": verdict, "reason": reason,
                              "sample_interval_seconds": {"min": interval[0], "max": interval[1]}}
    else:
        sample["progress"] = {"verdict": "unknown", "reason": "previous_observation_unavailable"}
    return sample


def summarize(players):
    total = {"targets": len(players), "samples": 0, "observed": 0, "unknown": 0,
             "progress_pass": 0, "progress_fail": 0, "progress_unknown": 0}
    for player in players:
        samples = player["samples"]
        summary = {"samples": len(samples), "observed": sum(row["status"] == "observed" for row in samples),
                   "unknown": sum(row["status"] == "unknown" for row in samples)}
        for verdict in ("pass", "fail", "unknown"):
            summary["progress_" + verdict] = sum(row.get("progress", {}).get("verdict") == verdict for row in samples)
        player["summary"] = summary
        for key, value in summary.items():
            total[key] += value
    return total


def print_sample(player, sample):
    label = ",".join(player["aliases"]) or player["device_id"]
    detail = sample.get("reason") or sample.get("phase", "unknown")
    if "standby" in sample:
        detail += "; standby=" + ("on" if sample["standby"] else "off")
    if "background" in sample:
        background = sample["background"]
        detail += "; background=" + background["state"] + ", pending=" + str(background["pending"])
    progress = sample.get("progress")
    if progress:
        detail += "; decoder " + progress["verdict"] + " (" + progress["reason"] + ")"
    print(label + ": " + sample["status"] + "; " + detail, flush=True)


def collect(api, client, targets, args, workbench, timeout):
    """Bound every round, serialize rounds, and retain finished peers on interruption."""
    rounds = math.ceil(args.duration / args.interval)
    check(len(targets) <= MAX_TARGETS and rounds * len(targets) <= MAX_OBSERVATIONS, "observation_limit")
    origin = time.monotonic()
    deadline = origin + args.duration
    result = {"version": 1, "command": "watch", "read_only": True, "physical_display_verified": False,
              "evidence_level": "decoder_progress_only", "started_at": workbench.now(), "status": "completed",
              "duration_seconds": args.duration, "interval_seconds": args.interval,
              "request_timeout_seconds": min(timeout, args.request_timeout), "workers": args.workers,
              "max_rounds": rounds, "planned_samples": rounds * len(targets), "controller": [], "players": []}
    for name, device in targets:
        aliases = sorted({public_alias(alias) for alias, value in client.config.get("players", {}).items() if value == device}, key=str.casefold)
        result["players"].append({"device_id": device, "aliases": aliases, "requested_as": public_alias(name), "samples": []})
    previous = {}
    stopped = threading.Event()
    pool = ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="ott-watch")
    next_start = origin
    futures, published = {}, set()

    def publish(future, collected):
        player = futures[future]
        sample = project_sample(round_number, origin, collected, previous.get(player["device_id"]), workbench)
        if collected[2] is None and result["status"] == "interrupted":
            sample["reason"] = "interrupted"
        if controller["status"] == "observed":
            row = inventory.get(player["device_id"])
            sample["inventory"] = {"registered": row is not None}
            if row is not None:
                sample["inventory"].update(last_seen=row["last_seen"], pending=row["pending"])
        player["samples"].append(sample)
        previous[player["device_id"]] = collected
        published.add(future)
        if not args.json:
            print_sample(player, sample)

    try:
        for round_number in range(1, rounds + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(0, next_start - time.monotonic()), remaining))
            round_started = time.monotonic()
            if round_started >= deadline:
                break
            next_start = round_started + args.interval
            controller = {"round": round_number, "elapsed_seconds": round(round_started - origin, 3), "status": "unknown"}
            inventory = {}
            try:
                status, raw = client.api("/api/devices", timeout=min(2, args.request_timeout, timeout, (deadline - round_started) / 2))
                check(status == 200)
                metadata = inventory_metadata(raw, client.config)
                inventory = {row["id"]: row for row in metadata["devices"]}
                controller.update(status="observed", registered_devices=len(inventory),
                                  stale_aliases=len(metadata["stale_aliases"]))
            except (api.Error, Failure, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
                controller["reason"] = error_reason(api, error)
            result["controller"].append(controller)
            if not args.json:
                print("Controller: " + controller["status"] + (" (" + controller["reason"] + ")" if "reason" in controller else ""), flush=True)
            batches = math.ceil(len(targets) / args.workers)
            budget = min(timeout, args.request_timeout, max(0, deadline - time.monotonic()) / batches)
            futures = {pool.submit(collect_player, api, workbench, client, player["device_id"], budget, deadline, stopped): player
                       for player in result["players"]}
            published = set()
            try:
                for future in as_completed(futures, timeout=max(0, deadline - time.monotonic())):
                    try:
                        collected = future.result()
                    except (api.Error, Failure, ValueError, TypeError, KeyError, OverflowError, RecursionError):
                        now = time.monotonic()
                        collected = (now, now, {"status": "unknown", "reason": "invalid_response", "requests": []})
                    publish(future, collected)
            except TimeoutError:
                result["status"] = "partial"
                break
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    finally:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)
        for future in futures:
            if future in published:
                continue
            now = time.monotonic()
            collected = (now, now, None)
            if future.done() and not future.cancelled():
                try:
                    collected = future.result()
                except (Exception, KeyboardInterrupt):
                    pass
            publish(future, collected)
    result["completed_at"] = workbench.now()
    result["elapsed_seconds"] = round(max(0, time.monotonic() - origin), 3)
    result["summary"] = summarize(result["players"])
    if result["status"] == "completed" and (result["summary"]["unknown"] or
            any(row["status"] != "observed" for row in result["controller"]) or
            any(len(player["samples"]) < rounds for player in result["players"])):
        result["status"] = "partial"
    return result


def run(api, client, selector, words, timeout, json_output=False):
    result = {"version": 1, "command": "watch", "read_only": True, "physical_display_verified": False}
    json_output = json_output or any(word in ("-j", "--json") for word in words)
    try:
        args = arguments(words, json_output)
        json_output = args.json
        targets = api.provider_group_targets(client, selector)
        if targets is None:
            targets = [(selector, client.device(selector))]
        check(bool(targets) and len(targets) <= MAX_TARGETS and all(DEVICE_ID.fullmatch(device) for _, device in targets), "invalid_targets")
        # Validate aliases before any controller or player request.
        inventory_metadata({"devices": []}, client.config)
        result = collect(api, client, targets, args, workbench_module(), timeout)
    except (api.Error, Failure, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        result.update(status="error", reason=error.reason if isinstance(error, Failure) else "invalid_arguments")
    raw = json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2)
    if len(raw.encode("utf-8")) > MAX_REPORT_BYTES:
        result = {"version": 1, "command": "watch", "read_only": True, "status": "error", "reason": "report_too_large"}
        raw = json.dumps(result)
    if json_output:
        print(raw)
    else:
        print("Watch: " + result["status"] + (" (" + result["reason"] + ")" if "reason" in result else ""))
        if "summary" in result:
            summary = result["summary"]
            print(f"Observations: {summary['observed']}/{summary['samples']}; decoder pass={summary['progress_pass']}, fail={summary['progress_fail']}, unknown={summary['progress_unknown']}.")
            print("Historical last_seen is not a heartbeat. Decoder progress does not verify the physical screen or sound.")
    if result["status"] == "interrupted":
        return 130
    if result["status"] == "error":
        return 1
    if result.get("summary", {}).get("progress_fail"):
        return 2
    return 3 if result["status"] == "partial" else 0
