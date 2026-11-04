"""Explicit device shutdown through a pinned local ADB or existing TV binding."""
import copy
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import secrets
import stat
import subprocess
import threading
import time
import xml.etree.ElementTree as ET


MAX_OUTPUT = 32768
FIELDS = frozenset(("version", "backend", "serial", "android_id", "hardware_serial", "model"))
WINDOW_END = "__OTT_SHUTDOWN_WINDOW_END_V1__"
# Android's "displays" subcommand has focus, while "windows" has ownership.
# Keep both from one snapshot, but do not transfer unbounded unrelated dumps.
# Window/section boundaries remain intact, including wrapped type attributes.
WINDOW_QUERY = ("{ dumpsys window && printf '\\n" + WINDOW_END + "\\n'; } | grep -E '"
                "^[[:space:]]*(mCurrentFocus=|Window #[0-9]+ Window[{]|mOwnerUid=|mAttrs=)"
                "|(^|[[:space:]])ty=|^WINDOW MANAGER|^" + WINDOW_END + "$'")


class PowerError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def help_text(name="NAME"):
    prefix = "ott " + shlex.quote(name) + " shutdown"
    return f"""Device shutdown (independent of playback):
  {prefix}                       request graceful device power off
  {prefix} device                same operation
  {prefix} status                inspect the configured shutdown route
  {prefix} bind-adb SERIAL       verify and bind a local authorized ADB device
  {prefix} unbind                remove this player's local ADB binding
  {prefix} --help                show this help

ADB requires Android SDK Platform Tools on this computer and an authorized
device. SERIAL is an exact USB serial or an IP address with port (IP:5555).
Binding verifies Android identity and shutdown support; it does not power off.
An explicit local ADB binding takes priority. Otherwise the controller's
existing television binding is used. Unsupported devices receive no power command.
Shutdown is separate from player standby, app exit and reboot. Powering back on
may require the remote or power button. A disconnect does not prove power off;
uncertain commands are never repeated or sent through another route.
Lists and groups support shutdown/status; binding changes require one player.
"""


def _serial(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or value.startswith("-"):
        raise PowerError("ADB serial must be an exact USB serial or IP:PORT")
    if ":" in value or value.startswith("["):
        match = re.fullmatch(r"(?:\[([^\]]+)\]|([^:]+)):([1-9][0-9]{0,4})", value)
        if not match or int(match[3]) > 65535:
            raise PowerError("Network ADB requires a literal IP address and port")
        try:
            address = ipaddress.ip_address(match[1] or match[2])
        except ValueError:
            raise PowerError("Network ADB requires a literal IP address and port") from None
        if address.is_unspecified or address.is_multicast or "%" in str(address):
            raise PowerError("Network ADB requires a device IP address")
        return value, True
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value):
        raise PowerError("ADB serial must be an exact USB serial or IP:PORT")
    return value, False


def parse(api, words):
    words = list(words)
    action = words[0].casefold() if words else "shutdown"
    if not words or action == "device" and len(words) == 1:
        return {"action": "shutdown"}
    if action in ("status", "unbind", "help", "-h", "--help") and len(words) == 1:
        return {"action": "help" if action in ("help", "-h", "--help") else action}
    if action == "bind-adb" and len(words) == 2:
        try:
            serial, _ = _serial(words[1])
        except PowerError as exc:
            raise api.Error(str(exc)) from None
        return {"action": action, "serial": serial}
    raise api.Error("Use shutdown [device|status|bind-adb SERIAL|unbind|--help]")


def _remaining(deadline, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise PowerError("Device shutdown operation was interrupted")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PowerError("Device shutdown operation exceeded --timeout")
    return remaining


def _run(adb, arguments, deadline, cancelled=None, mutation=False, on_wait=None):
    """Bound both output and wall time; never include command output in errors."""
    _remaining(deadline, cancelled)
    try:
        process = subprocess.Popen([adb, *arguments], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    except OSError:
        raise PowerError("Could not start Android SDK Platform Tools") from None
    buffers = [bytearray(), bytearray()]
    streams = {process.stdout: buffers[0], process.stderr: buffers[1]}
    next_check = time.monotonic() + 0.25
    try:
        try:
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
        except (AttributeError, OSError):
            raise PowerError("This Python runtime cannot safely capture ADB output; use Python 3.12 or newer", mutation) from None
        while process.poll() is None or streams:
            try:
                remaining = _remaining(deadline, cancelled)
            except PowerError as exc:
                raise PowerError(str(exc), mutation) from None
            for stream, target in list(streams.items()):
                try:
                    block = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                except OSError:
                    raise PowerError("Could not read the ADB response", mutation) from None
                if not block:
                    del streams[stream]
                elif len(target) + len(block) > MAX_OUTPUT:
                    raise PowerError("ADB response exceeded the bounded output limit", mutation)
                else:
                    target.extend(block)
            if process.poll() is not None and not streams:
                break
            if on_wait is not None and time.monotonic() >= next_check:
                on_wait()
                next_check = time.monotonic() + 1
            time.sleep(min(0.02, remaining))
        return process.returncode, bytes(buffers[0]), bytes(buffers[1])
    except KeyboardInterrupt:
        raise PowerError("Device shutdown operation was interrupted", mutation) from None
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        process.stdout.close()
        process.stderr.close()


def _text(raw):
    try:
        return raw.decode("utf-8", "strict").strip()
    except (AttributeError, UnicodeDecodeError):
        raise PowerError("ADB returned invalid device metadata") from None


def _binding(value):
    if (not isinstance(value, dict) or set(value) != FIELDS or type(value["version"]) is not int
            or value["version"] != 1 or value["backend"] != "adb"):
        raise PowerError("Invalid local device_power binding; inspect the private CLI configuration")
    _serial(value["serial"])
    if (not isinstance(value["android_id"], str)
            or not re.fullmatch(r"[0-9a-f]{1,16}", value["android_id"])
            or int(value["android_id"], 16) == 0
            or not isinstance(value["hardware_serial"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value["hardware_serial"])
            or value["hardware_serial"].casefold() in ("unknown", "null", "none")
            or not isinstance(value["model"], str) or not 1 <= len(value["model"]) <= 128
            or any(ord(char) < 32 or ord(char) == 127 for char in value["model"])):
        raise PowerError("ADB device does not provide a usable stable Android identity")
    return value


def _bindings(config):
    result = config.get("device_power", {})
    if not isinstance(result, dict) or len(result) > 256:
        raise PowerError("device_power must map player device IDs to local power bindings")
    return result


def _listed_transport(raw, serial):
    rows = []
    for line in _text(raw).splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == serial:
            rows.append(fields)
    if len(rows) > 1:
        raise PowerError("ADB reported an ambiguous device identity")
    if not rows:
        return None
    fields = rows[0]
    if fields[1] == "unauthorized":
        raise PowerError("Authorize this computer in the device's USB debugging prompt")
    if fields[1] != "device":
        raise PowerError("The bound ADB device is offline or not ready")
    ids = [word.partition(":")[2] for word in fields[2:] if word.startswith("transport_id:")]
    if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]{0,19}", ids[0]):
        raise PowerError("ADB did not provide a transport ID; update Android SDK Platform Tools")
    return ids[0]


def _inspect(adb, serial, deadline, cancelled=None, expected=None):
    _, network = _serial(serial)
    code, out, _ = _run(adb, ["devices", "-l"], deadline, cancelled)
    if code:
        raise PowerError("Could not read the local ADB device list")
    transport = _listed_transport(out, serial)
    if transport is None and network:
        # Connect only the explicit literal endpoint. Never scan or restart ADB.
        _run(adb, ["connect", serial], deadline, cancelled)
        code, out, _ = _run(adb, ["devices", "-l"], deadline, cancelled)
        if code:
            raise PowerError("Could not read the local ADB device list")
        transport = _listed_transport(out, serial)
    if transport is None:
        raise PowerError("The bound ADB device is not connected")
    prefix = ["-t", transport, "shell"]
    identity = {"version": 1, "backend": "adb", "serial": serial}
    for key, command in (("android_id", ["settings", "get", "secure", "android_id"]),
                         ("hardware_serial", ["getprop", "ro.serialno"]),
                         ("model", ["getprop", "ro.product.model"])):
        code, out, err = _run(adb, [*prefix, *command], deadline, cancelled)
        if code or err.strip():
            raise PowerError("Could not verify the bound Android device identity")
        identity[key] = _text(out)
    _binding(identity)
    if expected is not None and identity != expected:
        raise PowerError("Android device identity changed; no shutdown was sent. Verify the device before rebinding")
    code, out, err = _run(adb, [*prefix, "svc", "power"], deadline, cancelled)
    help_text = _text(out + b"\n" + err)
    if code or not re.search(r"\bsvc[ \t]+power[ \t]+shutdown\b", help_text):
        raise PowerError("This Android device does not advertise graceful shutdown support")
    return transport, identity


def _ensure_unique(bindings, device, identity):
    for other, candidate in bindings.items():
        if other == device:
            continue
        value = _binding(candidate)
        if (value["serial"] == identity["serial"] or
                (value["android_id"], value["hardware_serial"]) ==
                (identity["android_id"], identity["hardware_serial"])):
            raise PowerError("This Android device is already bound to another player; use an alias of that player")


def _system_dialog_window(raw):
    """Recognize a focused Android-owned keyguard dialog, never an app imitation."""
    text = _text(raw)
    focus = re.findall(r"(?m)^\s*mCurrentFocus=Window\{([a-f0-9]+) (u[0-9]+) android\}\s*$", text)
    if len(focus) != 1:
        return None
    token, user = focus[0]
    heading = re.escape("Window{" + token + " " + user + " android}:")
    sections = re.findall(r"(?ms)^\s*Window #\d+ " + heading + r"\s*\n(.*?)(?=^\s*Window #\d+ |^WINDOW MANAGER|\Z)", text)
    if len(sections) != 1:
        return None
    body = sections[0]
    if (not re.search(r"(?m)^\s*mOwnerUid=1000\b[^\n]*\bpackage=android(?:\s|$)", body)
            or not re.search(r"\bty=KEYGUARD_DIALOG\b", body)):
        return None
    return token, user


def _window_snapshot(adb, transport, deadline, cancelled=None):
    code, raw, err = _run(adb, ["-t", transport, "shell", WINDOW_QUERY], deadline, cancelled)
    lines = _text(raw).splitlines()
    # A pipeline may otherwise look successful after dumpsys failed partway
    # through a valid-looking window. Never treat partial output as a snapshot.
    if code or err.strip() or not lines or lines[-1] != WINDOW_END or lines.count(WINDOW_END) != 1:
        raise PowerError("Could not read a complete Android window snapshot")
    return _system_dialog_window("\n".join(lines[:-1]).encode("utf-8"))


def _shutdown_button(raw, require_focused=False):
    """Only the observed system Power off dialog is eligible for confirmation."""
    text = _text(raw)
    start, end = text.find("<?xml"), text.rfind("</hierarchy>")
    if start < 0 or end < start or "<!" in text:
        return None
    xml = text[start:end + len("</hierarchy>")]
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return None
    if root.tag != "hierarchy" or len(root) != 1:
        return None
    nodes = list(root.iter("node"))
    if not 4 <= len(nodes) <= 128 or any(node.get("package") != "android" for node in nodes):
        return None
    expected = {
        "android:id/alert_dialog_custom_title": ("Power off", "android.widget.TextView"),
        "android:id/alert_dialog_custom_content": ("Do you want to shut down?", "android.widget.TextView"),
        "android:id/alert_dialog_custom_btn_cancel": ("CANCEL", "android.widget.Button"),
        # This is the resource's actual spelling on the supported firmware.
        "android:id/alert_dialog_custom_btn_comfirm": ("OK", "android.widget.Button"),
    }
    selected = {}
    for node in nodes:
        resource = node.get("resource-id")
        if resource in expected:
            if (resource in selected or (node.get("text"), node.get("class")) != expected[resource]
                    or node.get("enabled") != "true"):
                return None
            selected[resource] = node
        elif node.get("text") or node.get("clickable") == "true":
            return None
    if set(selected) != set(expected):
        return None
    positive_node = selected["android:id/alert_dialog_custom_btn_comfirm"]
    negative_node = selected["android:id/alert_dialog_custom_btn_cancel"]
    if require_focused and (positive_node.get("focused") != "true" or negative_node.get("focused") == "true"):
        return None

    def bounds(node):
        match = re.fullmatch(r"\[([0-9]{1,5}),([0-9]{1,5})\]\[([0-9]{1,5}),([0-9]{1,5})\]", node.get("bounds", ""))
        if match is None:
            return None
        values = tuple(map(int, match.groups()))
        return values if 0 <= values[0] < values[2] <= 16384 and 0 <= values[1] < values[3] <= 16384 else None

    frame = bounds(nodes[0])
    if frame is None:
        return None
    for resource, node in selected.items():
        area = bounds(node)
        if area is None or not (frame[0] <= area[0] < area[2] <= frame[2] and frame[1] <= area[1] < area[3] <= frame[3]):
            return None
        if "_btn_" in resource and node.get("clickable") != "true":
            return None
    positive = bounds(selected["android:id/alert_dialog_custom_btn_comfirm"])
    negative = bounds(selected["android:id/alert_dialog_custom_btn_cancel"])
    if max(positive[0], negative[0]) < min(positive[2], negative[2]) and max(positive[1], negative[1]) < min(positive[3], negative[3]):
        return None
    return (positive[0] + positive[2]) // 2, (positive[1] + positive[3]) // 2


def _read_shutdown_dialog(adb, transport, deadline, cancelled=None):
    prefix = ["-t", transport, "shell"]
    before = _window_snapshot(adb, transport, deadline, cancelled)
    if before is None:
        return None
    # Every shell byte is fixed except this generated hexadecimal nonce. The
    # dump can otherwise expose the app underneath if the window changes, so
    # create it privately and remove that exact file on all normal shell exits.
    path = "/data/local/tmp/.ott-power-" + secrets.token_hex(16) + ".xml"
    script = ("umask 077; trap 'rm -f " + path + "' EXIT; "
              "uiautomator dump " + path + " >/dev/null && cat " + path)
    code, ui, err = _run(adb, [*prefix, script], deadline, cancelled)
    button = _shutdown_button(ui) if not code and not err.strip() else None
    if button is None:
        return None
    after = _window_snapshot(adb, transport, deadline, cancelled)
    if after != before:
        return None
    return before, button, _shutdown_button(ui, require_focused=True) is not None


def _confirm_shutdown_dialog(adb, transport, deadline, cancelled=None):
    dialog = _read_shutdown_dialog(adb, transport, deadline, cancelled)
    if dialog is None:
        return False
    window, button, focused = dialog
    prefix = ["-t", transport, "shell", "input"]
    if not focused:
        code, out, err = _run(adb, [*prefix, "tap", str(button[0]), str(button[1])],
                             deadline, cancelled, mutation=True)
        if code or out.strip() or err.strip():
            raise PowerError("System shutdown confirmation was not acknowledged", True)
        try:
            after = _read_shutdown_dialog(adb, transport, deadline, cancelled)
        except PowerError as exc:
            raise PowerError(str(exc), True) from None
        if after is None:
            # On touch-driven firmware the tap activates the button directly.
            # Once the recognized dialog disappears, no key may be sent.
            return True
        if after[0] != window or not after[2]:
            raise PowerError("The system power-off button did not acquire verified focus", True)
    # Some TV firmware treats a touch as focus only. Activation is allowed only
    # after a fresh snapshot proves the exact positive system button has focus.
    code, out, err = _run(adb, [*prefix, "keyevent", "KEYCODE_DPAD_CENTER"],
                         deadline, cancelled, mutation=True)
    if code or out.strip() or err.strip():
        raise PowerError("System shutdown confirmation was not acknowledged", True)
    return True


class _ConfirmationSubmitted(Exception):
    pass


def _shutdown(adb, transport, deadline, cancelled=None):
    # A new explicit shutdown can complete an already-open recognized dialog;
    # it must not send a second svc request to create or toggle another prompt.
    if _confirm_shutdown_dialog(adb, transport, deadline, cancelled):
        return True

    def check_dialog():
        try:
            if _confirm_shutdown_dialog(adb, transport, deadline, cancelled):
                raise _ConfirmationSubmitted()
        except PowerError as exc:
            # svc was already dispatched. A later inspection failure is never
            # permission to replay it or pretend no change may have happened.
            raise PowerError(str(exc), True) from None

    try:
        code, out, err = _run(adb, ["-t", transport, "shell", "svc", "power", "shutdown"],
                             deadline, cancelled, mutation=True, on_wait=check_dialog)
        if re.search(rb"(?:java\.lang\.)?SecurityException[^\r\n]*android\.permission\.REBOOT\b", out + b"\n" + err):
            raise PowerError("Android rejected shutdown: the ADB shell lacks REBOOT permission. The request was not repeated")
        if code or out.strip() or err.strip():
            raise PowerError("The Android shutdown request did not return a clean acknowledgement", True)
        # Some firmware returns before its confirmation dialog is answered.
        check_dialog()
    except _ConfirmationSubmitted:
        return True
    return False


def overview_help(api, config, device, name):
    try:
        binding = _bindings(config).get(device)
        if binding is None:
            return
        _binding(binding)
    except PowerError:
        return
    prefix = "ott " + shlex.quote(name) + " shutdown"
    print("Device power (local ADB binding):")
    print(f"  {prefix} / status  — graceful device power off / verify support and identity")
    print()


def _print(result, json_output):
    if json_output:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    elif result["action"] == "bind-adb":
        print("Android shutdown binding saved; identity and shutdown support verified. No shutdown was sent.")
    elif result["action"] == "unbind":
        print("Local Android shutdown binding removed.")
    elif result["action"] == "status":
        print("Shutdown route: " + result["transport"])
        print("  Supported: " + ("yes" if result["supported"] else "no"))
        if result.get("identity_verified"):
            print("  Android identity: verified")
            print("  Firmware advertises shutdown; permission is checked when requested.")
        print("  No shutdown was sent.")
    elif result["outcome"] == "accepted":
        if result.get("confirmation_submitted"):
            print("System power-off confirmation submitted.")
        print("Device shutdown request submitted through ADB. Final power state has not been confirmed.")
    else:
        print("Device shutdown outcome is uncertain. Inspect the device before another command.")
        print("The request was not repeated; no other shutdown route was used.")


class _ConfigLock:
    """Serialize binding writers without replacing the stable lock inode."""
    def __init__(self, config_path):
        self.path = Path(str(Path(config_path).expanduser().resolve()) + ".device-power.lock")
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                              | getattr(os, "O_NONBLOCK", 0), 0o600)
            info = os.fstat(self.fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                raise OSError("unsafe lock")
            if os.name == "nt":
                import msvcrt
                if info.st_size == 0:
                    os.write(self.fd, b"0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if not isinstance(exc, Exception):
                raise
            raise PowerError("Another device-power binding update is active or its private lock is unavailable") from None

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _save(api, config_path, config, original, updated):
    with _ConfigLock(config_path):
        if api.read_json(config_path) != original:
            raise PowerError("CLI configuration changed during device verification; no binding change was saved")
        api.write_private(config_path, updated)
        config.clear()
        config.update(updated)


def run(api, client, config, config_path, device, name, words, timeout, json_output=False):
    params = parse(api, words)
    action = params["action"]
    if action == "help":
        print(help_text(name))
        return 0
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise api.Error("Shutdown --timeout must be between 1 and 300 seconds")
    deadline = time.monotonic() + timeout
    cancelled = getattr(client, "group_cancelled", None)
    if not isinstance(cancelled, threading.Event):
        cancelled = None
    try:
        original = copy.deepcopy(config)
        bindings = _bindings(config)
        explicit = device in bindings
        if action == "unbind":
            updated = copy.deepcopy(config)
            updated.setdefault("device_power", {}).pop(device, None)
            _save(api, config_path, config, original, updated)
            _print({"version": 1, "action": action, "configured": False}, json_output)
            return 0
        if action != "bind-adb" and not explicit:
            television = api.television_module()
            binding = television.read_binding(api, client, device, min(2, _remaining(deadline, cancelled)))
            supported = bool(binding and binding["configured"] and "off" in binding["capabilities"])
            if action == "status":
                _print({"version": 1, "action": action, "transport": "television" if supported else "none",
                        "configured": supported, "supported": supported}, json_output)
                return 0
            if not supported:
                raise PowerError("Device shutdown is unsupported or not configured. Use shutdown bind-adb SERIAL for an authorized Android device")
            return television.run(api, client, device, name, ["off"],
                                  min(120, _remaining(deadline, cancelled)), json_output)
        expected = None if action == "bind-adb" else _binding(bindings[device])
        serial = params.get("serial") if expected is None else expected["serial"]
        adb = shutil.which("adb")
        if adb is None:
            raise PowerError("Install Android SDK Platform Tools and make adb available on PATH")
        transport, identity = _inspect(adb, serial, deadline, cancelled, expected)
        _ensure_unique(bindings, device, identity)
        if action == "bind-adb":
            updated = copy.deepcopy(config)
            updated.setdefault("device_power", {})[device] = identity
            _save(api, config_path, config, original, updated)
            _print({"version": 1, "action": action, "transport": "adb", "configured": True,
                    "supported": True, "identity_verified": True}, json_output)
            return 0
        if action == "status":
            _print({"version": 1, "action": action, "transport": "adb", "configured": True,
                    "supported": True, "identity_verified": True}, json_output)
            return 0
        confirmed = _shutdown(adb, transport, deadline, cancelled)
        _print({"version": 1, "action": action, "transport": "adb", "outcome": "accepted",
                "power_state_confirmed": False, "confirmation_submitted": confirmed, "repeated": False}, json_output)
        return 0
    except PowerError as exc:
        if exc.uncertain:
            _print({"version": 1, "action": "shutdown", "transport": "adb", "outcome": "uncertain",
                    "power_state_confirmed": False, "repeated": False}, json_output)
            return 1
        raise api.Error(str(exc)) from None
