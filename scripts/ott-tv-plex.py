#!/usr/bin/env python3
"""Wake LG2, open OttPlay, and confirm one Cocomelon shuffle request."""
import contextlib
import datetime
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

try:
    import fcntl
except ImportError:  # Bash shortcut locking is supported on macOS and Linux.
    fcntl = None

PLAYER = "lg2"
COLLECTION = "MultCocomelonAll"
TOTAL_SECONDS = 240
OUTPUT_LIMIT = 1024 * 1024
INSPECT = "Inspect 'ott lg2 tv status' and 'ott lg2 plex status' before retrying. No change was repeated."
NATIVE_INSPECT = "Inspect 'ott lg2 tv status' and native Plex in Home Assistant before retrying. No change was repeated."


class ShortcutError(Exception):
    pass


class ReadPending(Exception):
    """A read did not complete; polling it again cannot change playback."""


@contextlib.contextmanager
def cancellation_signals():
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)
    previous = {item: signal.signal(item, interrupt) for item in signals}
    try:
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


class Progress:
    def __init__(self, stream=None, clock=time.monotonic, name="coco"):
        self.stream = stream if stream is not None else sys.stderr
        self.clock = clock
        self.started = clock()
        self.next_update = self.started
        self.label = "Starting"
        self.name = name

    def phase(self, label):
        self.label = label
        self.tick(force=True)

    def tick(self, force=False):
        now = self.clock()
        if force or now >= self.next_update:
            print(f"[{self.name} {PLAYER}] {self.label} [{now - self.started:.1f}s]",
                  file=self.stream, flush=True)
            self.next_update = now + 5


@contextlib.contextmanager
def television_lock(path=None):
    """Keep one stable inode shared by coco and future native-Plex shortcuts."""
    if fcntl is None:
        raise ShortcutError("This shell shortcut requires macOS or Linux.")
    path = Path(path) if path is not None else Path.home() / ".local/state/ottplay-shortcuts/lg2.lock"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ShortcutError("The LG2 shortcut lock must be a private regular file owned by this user.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ShortcutError("Another LG2 shortcut is running; wait for it to finish.") from None
        yield
    finally:
        os.close(descriptor)


def load_cli(path):
    """Resolve once so a concurrent CLI update cannot mix protocol revisions."""
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ShortcutError("Install the ott CLI before using coco.")
    spec = importlib.util.spec_from_file_location("ott_shortcut_cli", path)
    if spec is None or spec.loader is None:
        raise ShortcutError("The installed ott command must resolve to the Python CLI.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def native_config(value):
    required = {"ha_url", "ha_token_file", "plex_entity", "scan_entity", "expected_app_id"}
    optional = {"library_count_entity", "player", "library"}
    if (not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional
            or any(not isinstance(item, str) or not item for item in value.values())):
        raise ShortcutError("Native Plex requires a complete shortcuts.json configuration.")
    try:
        url = urllib.parse.urlsplit(value["ha_url"])
        valid_url = (url.scheme in ("http", "https") and bool(url.hostname) and url.port != 0
                     and not url.username and not url.password and not url.query and not url.fragment)
    except ValueError:
        valid_url = False
    token_path = Path(value["ha_token_file"]).expanduser()
    entities = {"plex_entity": "media_player", "scan_entity": "button", "library_count_entity": "sensor"}
    if (not valid_url or not token_path.is_absolute()
            or any(key in value and not re.fullmatch(domain + r"\.[a-z0-9_]+", value[key])
                   for key, domain in entities.items())
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value["expected_app_id"])
            or value.get("player", PLAYER) != PLAYER or value.get("library", COLLECTION) != COLLECTION):
        raise ShortcutError("The native Plex shortcut configuration is invalid.")
    return dict(value, ha_url=value["ha_url"].rstrip("/"), ha_token_file=str(token_path))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def internal_ha_request():
    """One HTTP operation in a killable child; no token is passed on the command line."""
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        config = native_config(payload["config"])
        timeout = payload["timeout"]
        if type(timeout) not in (int, float) or not 1 <= timeout <= 45:
            raise ValueError("invalid timeout")
        path, method = payload["path"], payload["method"]
        allowed_states = {"/api/states/" + config[key] for key in
                          ("plex_entity", "scan_entity", "library_count_entity") if key in config}
        allowed_services = {"/api/services/button/press", "/api/services/media_player/play_media"}
        if not ((method == "GET" and path in allowed_states)
                or (method == "POST" and path in allowed_services)):
            raise ValueError("unsupported operation")
        if method == "POST":
            expected = {"entity_id": config["scan_entity"]}
            if path.endswith("/play_media"):
                expected = {"entity_id": config["plex_entity"], "media_content_type": "movie",
                            "media_content_id": json.dumps({"library_name": COLLECTION, "libtype": "movie",
                                                            "allow_multiple": True, "maxresults": 500, "shuffle": True})}
            if payload.get("body") != expected:
                raise ValueError("unsupported service payload")
        descriptor = os.open(config["ha_token_file"], os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 4096):
                raise ValueError("private token file required")
            token = stream.read(4097).strip()
        if not re.fullmatch(r"[A-Za-z0-9._~-]{20,4096}", token):
            raise ValueError("invalid token")
        body = json.dumps(payload["body"]).encode("utf-8") if method == "POST" else None
        request = urllib.request.Request(config["ha_url"] + path, data=body, method=method,
                                         headers={"Authorization": "Bearer " + token,
                                                  "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(OUTPUT_LIMIT + 1)
        if len(raw) > OUTPUT_LIMIT:
            raise ValueError("oversized response")
        print(json.dumps({"data": json.loads(raw)}, allow_nan=False))
        return 0
    except Exception:
        # Parent reports the stage and uncertainty, never a private URL or token.
        print("Home Assistant request did not complete.", file=sys.stderr)
        return 1


class HomeAssistant:
    def __init__(self, config, runner):
        self.config, self.runner = config, runner

    def request(self, method, path, body=None, seconds=6):
        # The configured Scan Clients button is idempotent discovery. Its HTTP
        # timeout is safe to poll again; playback submission is never retried.
        mutating = method == "POST" and path != "/api/services/button/press"
        return self.runner.ha_call({"config": self.config, "method": method, "path": path, "body": body},
                                   seconds=seconds, mutating=mutating)

    def state(self, entity, seconds=6):
        return self.request("GET", "/api/states/" + entity, seconds=seconds)

    def service(self, domain, service, body, seconds=6):
        return self.request("POST", "/api/services/" + domain + "/" + service, body, seconds)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.timestamp() if result.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def native_state(raw, entity):
    if (not isinstance(raw, dict) or raw.get("entity_id") != entity or not isinstance(raw.get("state"), str)
            or not isinstance(raw.get("attributes"), dict) or timestamp(raw.get("last_updated")) is None):
        raise ShortcutError("Home Assistant returned invalid native Plex state metadata.")
    attrs = raw["attributes"]
    features = attrs.get("supported_features", 0)
    library, content_id = attrs.get("media_library_title"), attrs.get("media_content_id")
    position = attrs.get("media_position")
    if (type(features) is not int or not 0 <= features < 2 ** 53
            or raw["state"] not in ("unknown", "unavailable", "off", "idle", "on", "playing", "paused", "buffering", "standby")
            or (library is not None and (not isinstance(library, str) or len(library) > 256))
            or (content_id is not None and not ((isinstance(content_id, str) and 0 < len(content_id) <= 2048)
                                               or (type(content_id) is int and content_id > 0)))
            or (attrs.get("media_position_updated_at") is not None
                and timestamp(attrs["media_position_updated_at"]) is None)
            or (position is not None and (type(position) not in (int, float)
                                           or position < 0 or position > 1e12 or not math.isfinite(position)))):
        raise ShortcutError("Home Assistant returned invalid native Plex capabilities.")
    return {"state": raw["state"], "features": features, "updated": timestamp(raw["last_updated"]),
            "position_updated": timestamp(attrs.get("media_position_updated_at")),
            "library": library, "content_id": content_id, "position": position}


class Runner:
    def __init__(self, cli_path, progress, deadline, clock=time.monotonic, inspect=INSPECT):
        self.cli_path = Path(cli_path)
        self.progress, self.deadline, self.clock = progress, deadline, clock
        self.inspect = inspect

    @staticmethod
    def stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()

    def budget(self, seconds):
        remaining = self.deadline - self.clock()
        timeout = min(seconds, remaining - 0.5)
        if timeout < 1:
            raise ShortcutError("The shortcut deadline expired. " + self.inspect)
        return timeout

    def call(self, words, seconds, mutating=False):
        timeout = self.budget(seconds)
        args = [sys.executable, str(self.cli_path), "--json", "-t", str(timeout), PLAYER, *words]
        return self.run(args, timeout, mutating)

    def ha_call(self, payload, seconds=6, mutating=False):
        timeout = self.budget(seconds)
        args = [sys.executable, str(Path(__file__).resolve()), "--internal-ha-request"]
        return self.run(args, timeout, mutating, dict(payload, timeout=timeout))["data"]

    def run(self, args, timeout, mutating=False, input_data=None):
        # Temporary files bound memory and prevent raw diagnostics or credentials
        # from reaching the terminal. Only validated JSON leaves this boundary.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as source:
            if input_data is not None:
                source.write(json.dumps(input_data, allow_nan=False).encode("utf-8"))
                source.seek(0)
            process = subprocess.Popen(args, stdin=source if input_data is not None else subprocess.DEVNULL, stdout=output,
                                       stderr=errors, start_new_session=True)
            until = min(self.deadline, self.clock() + timeout + 0.5)
            try:
                while process.poll() is None:
                    self.progress.tick()
                    if (os.fstat(output.fileno()).st_size > OUTPUT_LIMIT
                            or os.fstat(errors.fileno()).st_size > OUTPUT_LIMIT):
                        raise ShortcutError("The command output exceeded the safety limit. " + self.inspect)
                    left = until - self.clock()
                    if left <= 0:
                        if mutating:
                            raise ShortcutError("The command timed out; it may have been accepted. " + self.inspect)
                        raise ReadPending()
                    try:
                        process.wait(timeout=min(0.2, left))
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                self.stop(process)
            if process.returncode:
                if mutating:
                    raise ShortcutError("The command did not confirm success; it may have been accepted. " + self.inspect)
                raise ReadPending()
            if os.fstat(errors.fileno()).st_size > OUTPUT_LIMIT:
                raise ShortcutError("The command output exceeded the safety limit. " + self.inspect)
            output.seek(0)
            raw = output.read(OUTPUT_LIMIT + 1)
            if len(raw) > OUTPUT_LIMIT:
                raise ShortcutError("The command output exceeded the safety limit. " + self.inspect)
            try:
                result = json.loads(raw)
            except (UnicodeError, ValueError):
                raise ShortcutError("The command returned invalid JSON. " + self.inspect) from None
            if not isinstance(result, dict):
                raise ShortcutError("The command returned invalid metadata. " + self.inspect)
            return result


class Workflow:
    def __init__(self, api, runner, progress, deadline, clock=time.monotonic, sleep=time.sleep):
        self.api, self.runner, self.progress = api, runner, progress
        self.deadline, self.clock, self.sleep = deadline, clock, sleep
        self.inspect = INSPECT

    def validate(self, validator, *args, **kwargs):
        try:
            return validator(*args, **kwargs)
        except self.api.Error:
            raise ShortcutError("The player returned inconsistent protocol metadata. " + self.inspect) from None

    def read(self, words, until):
        left = min(until, self.deadline) - self.clock()
        if left < 1.5:
            raise ShortcutError("Timed out while " + self.progress.label.lower() + ". " + self.inspect)
        return self.runner.call(words, min(8, left - 0.5))

    def pause(self, until):
        self.progress.tick()
        self.sleep(max(0, min(1, until - self.clock(), self.deadline - self.clock())))

    def wait_player(self):
        self.progress.phase("Waiting for OttPlay and its Plex library connection")
        until = min(self.deadline, self.clock() + 60)
        while self.clock() < until:
            try:
                caps = self.validate(self.api.capabilities_metadata, self.read(["caps"], until))
                if ("play" in caps.get("plex_library", {}).get("operations", [])
                        and "status" in caps.get("plex_queue", {}).get("operations", [])):
                    libraries = self.read(["plex", "libraries"], until)
                    # Readiness can change during boot; poll again on a new runtime.
                    if libraries.get("runtime") == caps["player"]["runtime"]:
                        libraries = self.validate(self.api.plex_library_metadata, libraries,
                                                  caps["player"]["runtime"], {"op": "list"})
                        if libraries["state"] == "ready":
                            return caps
            except ReadPending:
                pass
            self.pause(until)
        raise ShortcutError("OttPlay or its Plex controls did not become ready within 60 seconds. " + INSPECT)

    def confirm(self, queue):
        self.progress.phase("Waiting for the requested Plex queue to play")
        until = min(self.deadline, self.clock() + 45)
        expected_runtime, expected_ids = queue["runtime"], list(queue["ids"])
        while True:
            if queue["runtime"] != expected_runtime or queue["ids"] != expected_ids:
                raise ShortcutError("The player or Plex queue changed during startup. " + INSPECT)
            if queue["state"] == "playing":
                return queue
            if queue["state"] in ("error", "idle", "ended"):
                raise ShortcutError("The requested Plex queue stopped before playback was confirmed. " + INSPECT)
            self.pause(until)
            try:
                queue = self.validate(self.api.plex_queue_metadata, self.read(["plex", "status"], until))
            except ReadPending:
                continue

    def run(self):
        self.progress.phase("Waking the television and opening OttPlay")
        television = self.runner.call(["tv", "go"], 90, mutating=True)
        device = television.get("device_id")
        if not isinstance(device, str) or not device:
            raise ShortcutError("The television returned invalid identity metadata. " + INSPECT)
        television = self.validate(self.api.television_module().result_metadata,
                                   self.api, television, device, "go", {"action": "go"})
        if television["outcome"] not in ("accepted", "confirmed"):
            raise ShortcutError("The television launch was not confirmed. " + INSPECT)
        caps = self.wait_player()
        self.progress.phase("Starting one shuffled MultCocomelonAll queue")
        result = self.runner.call(["plex", "shuffle", COLLECTION], 45, mutating=True)
        params = {"op": "play", "query": COLLECTION, "shuffle": True}
        if "plex_library_truncation" in caps:
            params["allow_truncate"] = True
        result = self.validate(self.api.plex_library_metadata, result, caps["player"]["runtime"],
                               params, caps["plex_library"]["max_items"])
        if result["state"] != "ready":
            raise ShortcutError("The Plex shuffle request failed. " + INSPECT)
        if result.get("truncated"):
            self.progress.phase(f"Using the supported queue limit of {len(result['queue']['ids'])} items")
        queue = self.confirm(result["queue"])
        self.progress.phase("Playback confirmed")
        return queue


class NativeWorkflow(Workflow):
    def __init__(self, api, runner, progress, deadline, config, ha=None,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(api, runner, progress, deadline, clock, sleep)
        self.inspect = NATIVE_INSPECT
        self.config = config
        self.ha = ha if ha is not None else HomeAssistant(config, runner)

    def state(self, until):
        remaining = min(until, self.deadline) - self.clock()
        if remaining < 1.5:
            raise ShortcutError("Timed out while " + self.progress.label.lower() + ". " + self.inspect)
        return native_state(self.ha.state(self.config["plex_entity"], min(6, remaining - 0.5)),
                            self.config["plex_entity"])

    def wait_native(self):
        until = min(self.deadline, self.clock() + 60)
        self.progress.phase("Waiting for the native Plex application to open")
        while True:
            try:
                raw = self.read(["tv", "status"], until)
                device = raw.get("device_id")
                if not isinstance(device, str) or not device:
                    raise ShortcutError("The television returned invalid identity metadata. " + self.inspect)
                status = self.validate(self.api.television_module().result_metadata, self.api, raw, device, "status")
                state = status.get("state", {})
                if state.get("power") == "on" and state.get("app_id") == self.config["expected_app_id"]:
                    break
            except ReadPending:
                pass
            self.pause(until)
        self.progress.phase("Waiting for the native Plex client to accept playback")
        next_scan = self.clock()
        while True:
            if self.clock() >= next_scan:
                remaining = min(until, self.deadline) - self.clock()
                if remaining < 1.5:
                    raise ShortcutError("Native Plex client discovery timed out. " + self.inspect)
                self.progress.phase("Discovering the native Plex client in Home Assistant")
                try:
                    self.ha.service("button", "press", {"entity_id": self.config["scan_entity"]},
                                    seconds=min(15, remaining - 0.5))
                except ReadPending:
                    pass
                next_scan = self.clock() + 10
                self.progress.phase("Waiting for the native Plex client to accept playback")
            try:
                state = self.state(until)
                if state["state"] not in ("unknown", "unavailable", "off", "standby") and state["features"] & 512:
                    return state
            except ReadPending:
                pass
            self.pause(until)

    def report_limit(self):
        entity = self.config.get("library_count_entity")
        if not entity:
            return
        try:
            raw = self.ha.state(entity)
            count = raw.get("state") if isinstance(raw, dict) and raw.get("entity_id") == entity else None
            if isinstance(count, str) and re.fullmatch(r"[0-9]{1,10}", count) and int(count) > 500:
                self.progress.phase("Native Plex will shuffle at most 500 matching library items")
        except ReadPending:
            pass

    def confirm_native(self, before):
        self.progress.phase("Waiting for fresh native Plex playback confirmation")
        until = min(self.deadline, self.clock() + 45)
        while True:
            self.pause(until)
            try:
                state = self.state(until)
            except ReadPending:
                continue
            fresh = (state["updated"] > before["updated"]
                     and (state["position_updated"] is None or before["position_updated"] is None
                          or state["position_updated"] > before["position_updated"]))
            content_id = state["content_id"]
            content_changed = str(content_id) != str(before["content_id"])
            position_reset = (state["position"] is not None and before["position"] is not None
                              and before["position"] - state["position"] > 2)
            if (state["state"] == "playing" and state["library"] == COLLECTION and fresh
                    and isinstance(content_id, (str, int)) and not isinstance(content_id, bool)
                    and bool(str(content_id)) and (content_changed or position_reset)):
                self.progress.phase("Native Plex playback confirmed")
                return state

    def run(self):
        self.progress.phase("Waking the television and opening native Plex")
        raw = self.runner.call(["tv", "go", "plex"], 90, mutating=True)
        device = raw.get("device_id")
        if not isinstance(device, str) or not device:
            raise ShortcutError("The television returned invalid identity metadata. " + self.inspect)
        result = self.validate(self.api.television_module().result_metadata, self.api, raw, device,
                               "go", {"action": "go", "app": "plex"})
        if result["outcome"] not in ("accepted", "confirmed"):
            raise ShortcutError("The native Plex launch was not confirmed. " + self.inspect)
        self.report_limit()
        before = self.wait_native()
        self.progress.phase("Sending one native Plex shuffle request")
        self.ha.service("media_player", "play_media", {
            "entity_id": self.config["plex_entity"], "media_content_type": "movie",
            "media_content_id": json.dumps({"library_name": COLLECTION, "libtype": "movie",
                                            "allow_multiple": True, "maxresults": 500, "shuffle": True})}, seconds=45)
        return self.confirm_native(before)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--internal-ha-request"]:
        return internal_ha_request()
    if argv in (["-h"], ["--help"]):
        print("Usage: coco | cocop\nWake LG2 and shuffle MultCocomelonAll in OttPlay or native Plex; wait up to 240 seconds.")
        return 0
    if argv not in ([], ["--mode", "ott"], ["--mode", "native"]):
        print("Usage: coco | cocop (no arguments)", file=sys.stderr)
        return 2
    native = argv == ["--mode", "native"]
    progress = Progress(name="cocop" if native else "coco")
    inspect = NATIVE_INSPECT if native else INSPECT
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        with cancellation_signals(), television_lock():
            path, api = load_cli(Path.home() / ".local/bin/ott")
            runner = Runner(path, progress, deadline, inspect=inspect)
            if native:
                config_path = Path.home() / ".config/ottplay-control/shortcuts.json"
                with config_path.open(encoding="utf-8") as stream:
                    config = native_config(json.loads(stream.read(65537)))
                NativeWorkflow(api, runner, progress, deadline, config).run()
                message = f"LG2 native Plex is playing {COLLECTION}."
            else:
                queue = Workflow(api, runner, progress, deadline).run()
                message = f"LG2 is playing {COLLECTION} ({len(queue['ids'])} queued items)."
        print(message)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. " + inspect, file=sys.stderr)
        return 130
    except ShortcutError as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    except (OSError, ImportError, AttributeError, ValueError):
        print("Error: Check the installed ott CLI and private shortcut configuration. " + inspect, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
