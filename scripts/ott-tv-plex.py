#!/usr/bin/env python3
"""Wake LG2, open OttPlay, and confirm one Cocomelon shuffle request."""
import contextlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # Bash shortcut locking is supported on macOS and Linux.
    fcntl = None

PLAYER = "lg2"
COLLECTION = "MultCocomelonAll"
TOTAL_SECONDS = 240
OUTPUT_LIMIT = 1024 * 1024
INSPECT = "Inspect 'ott lg2 tv status' and 'ott lg2 plex status' before retrying. No change was repeated."
NATIVE_INSPECT = (
    "Inspect 'ott lg2 tv status' and the TV screen for a Plex authorization or Remote Watch Pass prompt, "
    "even on the LAN. Use 'coco' for playback through OttPlay; it is never started automatically. "
    "No change was repeated."
)


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
    required = {"plex_url", "plex_token_file", "plex_client_id", "plex_server_id", "expected_app_id"}
    optional = {"player", "library"}
    if (not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional
            or any(not isinstance(item, str) or not item for item in value.values())):
        raise ShortcutError("Native Plex requires a complete shortcuts.json configuration.")
    try:
        url = urllib.parse.urlsplit(value["plex_url"])
        valid_url = (url.scheme in ("http", "https") and bool(url.hostname)
                     and url.port is not None and 1 <= url.port <= 65535 and ":" not in url.hostname
                     and url.username is None and url.password is None and not url.query and not url.fragment
                     and url.path in ("", "/") and not re.search(r"[\s\\]", value["plex_url"]))
    except ValueError:
        valid_url = False
    token_path = Path(value["plex_token_file"]).expanduser()
    if (not valid_url or not token_path.is_absolute()
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[key])
                   for key in ("plex_client_id", "plex_server_id"))
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value["expected_app_id"])
            or value.get("player", PLAYER) != PLAYER or value.get("library", COLLECTION) != COLLECTION):
        raise ShortcutError("The native Plex shortcut configuration is invalid.")
    return dict(value, plex_url=value["plex_url"].rstrip("/"), plex_token_file=str(token_path))


class PlexAdapter:
    def __init__(self, config, runner):
        self.config = {key: config[key] for key in ("plex_url", "plex_token_file", "plex_client_id", "plex_server_id")}
        self.runner = runner
        self.controller_id = "ott-cocop-" + str(uuid.uuid4())
        self.command_id = 0

    def call(self, operation, seconds=6, queue_id=None):
        self.command_id += 1
        payload = {"config": self.config, "operation": operation, "controller_id": self.controller_id,
                   "command_id": self.command_id}
        if queue_id is not None:
            payload["queue_id"] = queue_id
        return self.runner.plex_call(payload, seconds, mutating=operation in ("prepare", "play"))


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

    def plex_call(self, payload, seconds=6, mutating=False):
        timeout = self.budget(seconds)
        args = [sys.executable, str(Path(__file__).resolve().with_name("ott_plex_native.py"))]
        return self.run(args, timeout, mutating, dict(payload, timeout=timeout), safe_rejection=True)["data"]

    def run(self, args, timeout, mutating=False, input_data=None, safe_rejection=False):
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
                if safe_rejection:
                    errors.seek(0)
                    rejected = re.fullmatch(
                        rb"Native Plex rejected the Companion request \(code ([1-9][0-9]{2})\); "
                        rb"check that the native Plex app is ready and supports this operation\. "
                        rb"The request was not repeated\.\n?", errors.read(2049))
                    if rejected:
                        code = rejected.group(1).decode("ascii")
                        raise ShortcutError(f"Native Plex rejected the Companion request (code {code}). " + self.inspect)
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
    def __init__(self, api, runner, progress, deadline, config, adapter=None,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(api, runner, progress, deadline, clock, sleep)
        self.inspect = NATIVE_INSPECT
        self.config = config
        self.adapter = adapter if adapter is not None else PlexAdapter(config, runner)

    def require(self, condition):
        if not condition:
            raise ShortcutError("Plex Server returned invalid native playback metadata. " + self.inspect)

    def read_adapter(self, operation, until, queue_id=None):
        remaining = min(until, self.deadline) - self.clock()
        if remaining < 1.5:
            raise ShortcutError("Timed out while " + self.progress.label.lower() + ". " + self.inspect)
        result = self.adapter.call(operation, min(8, remaining - 0.5), queue_id=queue_id)
        self.require(isinstance(result, dict))
        return result

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
        self.progress.phase("Waiting for Plex Server to discover the native player")
        while True:
            try:
                result = self.read_adapter("discover", until)
                capabilities = result.get("capabilities")
                self.require(type(result.get("ready")) is bool and isinstance(capabilities, list)
                             and len(capabilities) <= 32
                             and all(isinstance(item, str) and len(item) <= 64 for item in capabilities))
                if result["ready"] and {"playback", "playqueues"} <= set(capabilities):
                    return
            except ReadPending:
                pass
            self.pause(until)

    def confirm_native(self, queue_id):
        self.progress.phase("Waiting for native Plex to play the exact requested queue")
        until = min(self.deadline, self.clock() + 45)
        while True:
            self.pause(until)
            try:
                state = self.read_adapter("status", until, queue_id)
            except ReadPending:
                continue
            observed_queue, content_id, position = state.get("queue_id"), state.get("content_id"), state.get("position")
            self.require(state.get("state") in ("playing", "paused", "stopped", "buffering", "unknown", "idle")
                         and (observed_queue is None or (type(observed_queue) is int and 0 < observed_queue < 2 ** 53))
                         and (content_id is None or (isinstance(content_id, str) and 0 < len(content_id) <= 128))
                         and (position is None or (type(position) in (int, float) and 0 <= position <= 1e12
                                                   and math.isfinite(position))))
            if state["state"] == "playing" and observed_queue == queue_id and content_id:
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
        self.wait_native()
        self.progress.phase("Preparing one shuffled native Plex queue")
        prepared = self.adapter.call("prepare", 45)
        self.require(isinstance(prepared, dict))
        queue_id = prepared.get("queue_id")
        self.require(type(queue_id) is int and 0 < queue_id < 2 ** 53
                     and type(prepared.get("count")) is int and 1 <= prepared["count"] <= 500
                     and type(prepared.get("truncated")) is bool
                     and isinstance(prepared.get("first_id"), str)
                     and re.fullmatch(r"[1-9][0-9]{0,15}", prepared["first_id"]) is not None)
        self.progress.phase(f"Prepared native Plex queue {queue_id} with {prepared['count']} items "
                            f"(first item {prepared['first_id']})")
        if prepared["truncated"]:
            self.progress.phase("Using the supported native Plex queue limit of 500 items")
        self.progress.phase("Sending one native Plex playback request")
        accepted = self.adapter.call("play", 45, queue_id)
        self.require(isinstance(accepted, dict) and accepted.get("accepted") is True
                     and type(accepted.get("queue_id")) is int and accepted["queue_id"] == queue_id)
        return self.confirm_native(queue_id)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
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
                if (not Path(__file__).resolve().with_name("ott_plex_native.py").is_file()
                        or importlib.util.find_spec("plexapi") is None or importlib.util.find_spec("requests") is None):
                    raise ShortcutError("Install the native Plex adapter and pinned shortcut Python environment first.")
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
