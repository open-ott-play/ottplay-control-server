#!/usr/bin/env python3
"""One explicitly scoped USB ADB server for a registry of Android devices.

Run on the USB host. The container has no network, broad USB mount or Docker
socket. Root inside it is required for the NAS's root-owned USB nodes; its
capabilities are dropped, root filesystem is read-only and mappings are exact.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time


_spec = importlib.util.spec_from_file_location(
    "_android_usb_recovery", Path(__file__).with_name("android_usb_supervisor.py")
)
_recovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_recovery)
RecoveryError = _recovery.RecoveryError
Supervisor = _recovery.Supervisor

OWNER_LABEL = "org.ottplay.android-manager"
# ADB 29's listener does not accept a hostname in -L. Without -a, tcp:5037
# binds loopback; the client socket below may still use 127.0.0.1 explicitly.
SERVER_COMMAND = ["-L", "tcp:5037", "nodaemon", "server"]
TMPFS_OPTIONS = "rw,noexec,nosuid,nodev,size=256m"
MEMORY_BYTES = 256 * 1024 * 1024
CPU_SHARES = 128
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ALIAS = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}")
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
FORBIDDEN_ADB = {
    "start-server", "kill-server", "server", "nodaemon", "connect",
    "disconnect", "pair", "tcpip", "usb", "attach", "detach", "reconnect",
    "root", "unroot", "devices", "track-devices", "host-features",
}


def _text(value):
    return value.decode("utf-8", errors="replace").strip() if isinstance(value, bytes) else str(value).strip()


def _string(value):
    return isinstance(value, str) and bool(value) and not any(ord(c) < 32 for c in value)


def validate_config(config):
    allowed = {
        "version", "docker_path", "image", "container_name", "adb_key_dir",
        "state_dir", "poll_seconds", "command_timeout_seconds", "devices",
        "max_attempts", "sysfs_root", "device_root",
    }
    if (not isinstance(config, dict) or set(config) - allowed
            or type(config.get("version")) is not int or config["version"] != 2):
        raise RecoveryError("Invalid manager configuration")
    for name in ("docker_path", "image", "container_name", "adb_key_dir", "state_dir"):
        if not _string(config.get(name)):
            raise RecoveryError("Missing configuration: " + name)
    if not ALIAS.fullmatch(config["container_name"]) or len(config["container_name"]) < 3:
        raise RecoveryError("Invalid container name")
    if not re.fullmatch(r"(?:sha256:|[^\s@]+@sha256:)[0-9a-f]{64}", config["image"]):
        raise RecoveryError("Image must be pinned by SHA256")
    for name in ("adb_key_dir", "state_dir", "sysfs_root", "device_root"):
        if name in config and (not _string(config[name]) or not Path(config[name]).is_absolute()
                               or "," in config[name]):
            raise RecoveryError("Invalid absolute path: " + name)
    for name, default, maximum in (
        ("poll_seconds", 5, 300), ("command_timeout_seconds", 60, 120), ("max_attempts", 3, 10)
    ):
        value = config.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise RecoveryError("Invalid policy: " + name)
    devices = config.get("devices")
    if not isinstance(devices, dict) or not devices:
        raise RecoveryError("Device registry must not be empty")
    serials = set()
    for alias, device in devices.items():
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias) or alias in (".", "..", "manager.lock"):
            raise RecoveryError("Invalid device alias")
        if not isinstance(device, dict) or set(device) - {"enabled", "target", "package", "recover_on_boot"}:
            raise RecoveryError("Invalid device configuration: " + alias)
        if type(device.get("enabled", True)) is not bool:
            raise RecoveryError("Invalid enabled flag: " + alias)
        if not device.get("enabled", True):
            continue
        target = device.get("target")
        required = {"serial", "product", "model", "sdk", "android_id"}
        if not isinstance(target, dict) or not required <= set(target) or set(target) - required - {"vendor_id", "product_id"}:
            raise RecoveryError("Incomplete device identity: " + alias)
        if not all(_string(target.get(k)) for k in ("serial", "product", "model")):
            raise RecoveryError("Invalid device identity: " + alias)
        if any(c.isspace() for c in target["serial"]) or target["serial"].startswith("-"):
            raise RecoveryError("Invalid USB serial: " + alias)
        if type(target["sdk"]) is not int or not 1 <= target["sdk"] <= 100:
            raise RecoveryError("Invalid Android SDK: " + alias)
        if not isinstance(target["android_id"], str) or not re.fullmatch(r"[0-9a-f]{16}", target["android_id"]):
            raise RecoveryError("Invalid Android identity: " + alias)
        for key in ("vendor_id", "product_id"):
            if key in target and (not isinstance(target[key], str) or not re.fullmatch(r"[0-9a-f]{4}", target[key])):
                raise RecoveryError("Invalid USB identity: " + alias)
        if target["serial"] in serials:
            raise RecoveryError("Duplicate configured USB serial")
        serials.add(target["serial"])
        if not isinstance(device.get("package"), str) or not PACKAGE.fullmatch(device["package"]):
            raise RecoveryError("Invalid package: " + alias)
        if type(device.get("recover_on_boot")) is not bool:
            raise RecoveryError("Missing recovery policy: " + alias)


class DeviceSupervisor(Supervisor):
    """Reuse the reviewed boot, sleep, keyguard and recovery implementation."""

    def __init__(self, manager, alias):
        self.manager, self.alias = manager, alias
        device = manager.c["devices"][alias]
        target = {k: device["target"][k] for k in ("serial", "product", "model", "sdk", "android_id")}
        config = {
            "version": 1, "target": target, "package": device["package"],
            "docker_path": manager.c["docker_path"], "image": manager.c["image"],
            "adb_key_dir": manager.c["adb_key_dir"], "container_prefix": manager.c["container_name"],
            "state_dir": str(manager.root / alias), "max_attempts": manager.c.get("max_attempts", 3),
            "poll_seconds": manager.c.get("poll_seconds", 5),
            "command_timeout_seconds": manager.c.get("command_timeout_seconds", 60),
        }
        super().__init__(config, runner=manager.runner, sleep=manager.sleep)

    def connect(self):
        self.manager._bind(self.alias)
        self.container = self.manager.c["container_name"]
        self.verify()

    def adb(self, *args):
        return _text(self.manager._device_adb(self.alias, args))

    def read_state(self):
        path = self.root / "state.json"
        if path.is_symlink():
            raise RecoveryError("Recovery state must not be a symlink")
        if not path.exists():
            return {}
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            raise RecoveryError("Invalid recovery state: " + self.alias) from None
        if not isinstance(state, dict):
            raise RecoveryError("Invalid recovery state: " + self.alias)
        allowed = {"observed_boot", "completed_boot", "attempts", "result"}
        if set(state) - allowed or "observed_boot" not in state:
            raise RecoveryError("Unknown recovery state fields: " + self.alias)
        for key in ("observed_boot", "completed_boot"):
            if key in state and (not isinstance(state[key], str) or not UUID.fullmatch(state[key])):
                raise RecoveryError("Invalid recovery boot identity: " + self.alias)
        if "completed_boot" in state and "observed_boot" not in state:
            raise RecoveryError("Incomplete recovery state: " + self.alias)
        if "attempts" in state and (type(state["attempts"]) is not int or not 0 <= state["attempts"] <= 10):
            raise RecoveryError("Invalid recovery attempts: " + self.alias)
        if "result" in state and not _string(state["result"]):
            raise RecoveryError("Invalid recovery result: " + self.alias)
        return state

    def save_state(self, state):
        # Preserve the reviewed state transitions, but never follow a stale or
        # externally supplied state.tmp symlink while replacing the checkpoint.
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=str(self.root))
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / "state.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class Manager:
    def __init__(self, config, runner=None, sleep=time.sleep):
        validate_config(config)
        self.c = json.loads(json.dumps(config))
        self.root = Path(config["state_dir"])
        if self.root.is_symlink():
            raise RecoveryError("State directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.runner = runner or self._run
        self.sleep = sleep
        self._mutex = threading.RLock()
        self._lock_depth = 0
        self.present = {}
        self.device_errors = {}
        # Binding evidence only; these IDs must never be used to dispatch commands.
        self.transports = {}
        self._targets = {}

    def _run(self, argv):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=self.c.get("command_timeout_seconds", 60))
        except (OSError, subprocess.TimeoutExpired):
            raise RecoveryError("Transport operation unavailable or timed out") from None
        if result.returncode:
            raise RecoveryError("Transport operation failed; inspect private host logs")
        return result.stdout

    def docker(self, *args):
        return self.runner([self.c["docker_path"], *args])

    @contextmanager
    def locked(self):
        with self._mutex:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            path = self.root / "manager.lock"
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "a") as lock:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise RecoveryError("Manager busy; no device action was sent") from None
                        time.sleep(0.1)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _require_lock(self):
        if not self._lock_depth:
            raise RecoveryError("Manager lock required")

    def _keys(self):
        directory = Path(self.c["adb_key_dir"])
        vendor = directory / "vendor"
        if directory.is_symlink() or not directory.is_dir() or vendor.is_symlink() or not vendor.is_dir():
            raise RecoveryError("Provision existing authorized ADB keys and vendor directory first")
        vendor_files = list(vendor.iterdir())
        for key in vendor_files:
            # ADB 29 silently skips other filenames while scanning a directory
            # named in ADB_VENDOR_KEYS. A matching public companion is optional.
            if key.name.endswith(".adb_key"):
                continue
            if key.name.endswith(".adb_key.pub") and key.with_suffix("").is_file():
                continue
            raise RecoveryError("Vendor private keys require .adb_key filenames")
        for key in [directory / "adbkey", directory / "adbkey.pub", *vendor_files]:
            if key.is_symlink() or not key.is_file() or not key.stat().st_size:
                raise RecoveryError("ADB keys must be nonempty regular files")
            if key.suffix != ".pub" and key.stat().st_mode & 0o077:
                raise RecoveryError("ADB private keys require owner-only permissions")

    def scan_usb(self):
        records = {}
        self.device_errors = {}
        root = Path(self.c.get("sysfs_root", "/sys/bus/usb/devices"))
        try:
            paths = sorted(root.iterdir())
        except OSError:
            raise RecoveryError("USB inventory unavailable") from None
        for alias, device in self.c["devices"].items():
            if not device.get("enabled", True):
                self.device_errors[alias] = "disabled"
                continue
            target = device["target"]
            candidates = []
            for path in paths:
                try:
                    if (path / "serial").read_text().strip() == target["serial"]:
                        candidates.append(path)
                except (OSError, UnicodeError):
                    continue
            if len(candidates) != 1:
                self.device_errors[alias] = "USB target absent" if not candidates else "Ambiguous USB serial"
                continue
            path = candidates[0]
            try:
                if (path / "product").read_text().strip() != target["product"]:
                    raise RecoveryError("USB product identity mismatch")
                for key, filename in (("vendor_id", "idVendor"), ("product_id", "idProduct")):
                    if key in target and (path / filename).read_text().strip().lower() != target[key]:
                        raise RecoveryError("USB vendor/product identity mismatch")
                bus, dev = int((path / "busnum").read_text()), int((path / "devnum").read_text())
                if not 1 <= bus <= 999 or not 1 <= dev <= 999:
                    raise RecoveryError("Invalid USB address")
                records[alias] = {
                    "node": str(Path(self.c.get("device_root", "/dev/bus/usb")) / f"{bus:03d}" / f"{dev:03d}"),
                    "usb_path": path.name,
                }
            except (OSError, UnicodeError, ValueError, RecoveryError) as exc:
                self.device_errors[alias] = str(exc) if isinstance(exc, RecoveryError) else "USB identity unavailable"
        nodes = [item["node"] for item in records.values()]
        for alias, item in list(records.items()):
            if nodes.count(item["node"]) != 1:
                self.device_errors[alias] = "Ambiguous USB address"
                del records[alias]
        return records

    def _inspect(self):
        name = self.c["container_name"]
        names = _text(self.docker("ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}"))
        if not names:
            return None
        if names != name:
            raise RecoveryError("Ambiguous container name")
        try:
            data = json.loads(_text(self.docker("inspect", name)))
            details = data[0]
            if len(data) != 1 or not isinstance(details, dict):
                raise ValueError()
        except (ValueError, IndexError, TypeError):
            raise RecoveryError("Invalid container inspection") from None
        if details.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != name:
            raise RecoveryError("Container name occupied by unrelated owner")
        return details

    def _matches(self, details):
        host, config = details.get("HostConfig", {}), details.get("Config", {})
        expected_nodes = sorted(item["node"] for item in self.present.values())
        devices = host.get("Devices") or []
        if sorted(d.get("PathOnHost", "") for d in devices) != expected_nodes:
            return False
        if any(d.get("PathInContainer") != d.get("PathOnHost")
               or d.get("CgroupPermissions") != "rw" for d in devices):
            return False
        mounts = details.get("Mounts", [])
        if any(m.get("Type") == "tmpfs" and m.get("Destination") != "/tmp" for m in mounts):
            return False
        binds = [m for m in mounts if m.get("Type") != "tmpfs"]
        mount_ok = (len(binds) == 1 and binds[0].get("Type") == "bind"
                    and binds[0].get("Source") == self.c["adb_key_dir"]
                    and binds[0].get("Destination") == "/root/.android" and binds[0].get("RW") is False)
        tmpfs = host.get("Tmpfs") or {}
        environment = dict(v.split("=", 1) for v in config.get("Env", []) if "=" in v)
        return (
            host.get("NetworkMode") == "none" and host.get("ReadonlyRootfs") is True
            and not host.get("Privileged") and (host.get("CapDrop") or []) == ["ALL"]
            and not host.get("CapAdd") and not host.get("DeviceCgroupRules")
            and not host.get("DeviceRequests") and not host.get("VolumesFrom")
            and not host.get("Binds") and not host.get("PortBindings")
            and host.get("PidMode", "") in ("", "private") and host.get("IpcMode", "") in ("", "private")
            and host.get("UTSMode", "") in ("", "private") and not host.get("UsernsMode")
            and host.get("Memory") == MEMORY_BYTES and host.get("MemorySwap") == MEMORY_BYTES
            and host.get("CpuShares") == CPU_SHARES and host.get("PidsLimit") in (None, 0, -1)
            and host.get("CpuQuota", 0) in (0, -1) and not host.get("NanoCpus")
            and set(host.get("SecurityOpt") or []) in ({"no-new-privileges"}, {"no-new-privileges:true"})
            and tmpfs == {"/tmp": TMPFS_OPTIONS}
            and mount_ok and config.get("Image") == self.c["image"]
            and config.get("User", "") in ("0", "0:0", "root")
            and config.get("Entrypoint") == ["adb"] and config.get("Cmd") == SERVER_COMMAND
            and (config.get("Healthcheck") or {}).get("Test") == ["NONE"]
            and environment.get("ADB_VENDOR_KEYS") == "/root/.android/vendor"
            and environment.get("ADB_LIBUSB") == "0"
            and environment.get("ADB_SERVER_SOCKET") == "tcp:127.0.0.1:5037"
            and environment.get("HOME") == "/root"
            and host.get("RestartPolicy", {}).get("Name") == "unless-stopped"
        )

    def _reconcile(self):
        self._require_lock()
        self._keys()  # Validate before removing any existing owned container.
        self.present = self.scan_usb()
        self.transports = {}
        details = self._inspect()
        if details and not self._matches(details):
            self.docker("rm", "-f", self.c["container_name"])
            details = None
        if details is None:
            argv = [
                "run", "-d", "--name", self.c["container_name"],
                "--label", OWNER_LABEL + "=" + self.c["container_name"],
                "--network", "none", "--user", "0:0", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", str(MEMORY_BYTES), "--memory-swap", str(MEMORY_BYTES),
                "--cpu-shares", str(CPU_SHARES), "--no-healthcheck",
                "--restart", "unless-stopped", "--tmpfs", "/tmp:" + TMPFS_OPTIONS,
                "--env", "ADB_VENDOR_KEYS=/root/.android/vendor", "--env", "ADB_LIBUSB=0",
                "--env", "ADB_SERVER_SOCKET=tcp:127.0.0.1:5037", "--env", "HOME=/root",
                "--mount", "type=bind,src=" + self.c["adb_key_dir"] + ",dst=/root/.android,readonly",
                "--entrypoint", "adb",
            ]
            for item in sorted(self.present.values(), key=lambda item: item["node"]):
                argv.extend(["--device", item["node"] + ":" + item["node"] + ":rw"])
            self.docker(*argv, self.c["image"], *SERVER_COMMAND)
        elif not details.get("State", {}).get("Running"):
            self.docker("start", self.c["container_name"])
        return {"container": self.c["container_name"], "mapped": sorted(self.present),
                "unavailable": dict(self.device_errors)}

    def reconcile(self):
        with self.locked():
            return self._reconcile()

    def _list_transports(self):
        output = _text(self.docker("exec", self.c["container_name"], "adb", "devices", "-l"))
        records = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] == "List" or parts[0].startswith("*"):
                continue
            attributes = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
            records.append({"serial": parts[0], "state": parts[1], **attributes})
        return records

    def _bind(self, alias):
        self._require_lock()
        if alias not in self.present:
            raise RecoveryError(self.device_errors.get(alias, "USB target unavailable"))
        target = self.c["devices"][alias]["target"]
        snapshot = self.present[alias]
        # Do not use an ADB transport after a physical re-enumeration.
        if self.scan_usb().get(alias) != snapshot:
            raise RecoveryError("USB topology changed; retry after reconciliation")
        for attempt in range(4):
            try:
                matches = [r for r in self._list_transports() if r["serial"] == target["serial"]]
                if len(matches) > 1:
                    raise RecoveryError("Ambiguous ADB serial")
                if matches:
                    item = matches[0]
                    if item.get("usb") != snapshot["usb_path"]:
                        raise RecoveryError("ADB USB path mismatch")
                    transport = item.get("transport_id", "")
                    if item["state"] == "device" and re.fullmatch(r"[1-9][0-9]*", transport):
                        self.transports[alias] = transport
                        return
            except RecoveryError as exc:
                if str(exc) not in ("Transport operation failed; inspect private host logs",
                                    "Transport operation unavailable or timed out"):
                    raise
            if attempt < 3:
                self.sleep(0.5)
        raise RecoveryError("ADB target not authorized or ready")

    def _device_adb(self, alias, args):
        self._require_lock()
        if alias not in self.transports:
            raise RecoveryError("ADB target not bound")
        if self.scan_usb().get(alias) != self.present.get(alias):
            raise RecoveryError("USB topology changed; no device action was sent")
        # Transport IDs are local to one server lifetime and can be reassigned
        # after an automatic container/server restart. Dispatch by the verified,
        # unique USB serial instead: ADB rejects an ambiguous serial itself,
        # including ambiguity introduced after our inventory check.
        serial = self.c["devices"][alias]["target"]["serial"]
        return self.docker("exec", self.c["container_name"], "adb", "-s", serial, *args)

    def target(self, alias):
        device = self.c["devices"].get(alias)
        if device is None:
            raise RecoveryError("Unknown device alias")
        if not device.get("enabled", True):
            raise RecoveryError("Device is disabled")
        if alias not in self._targets:
            if (self.root / alias).is_symlink():
                raise RecoveryError("Device state directory must not be a symlink")
            self._targets[alias] = DeviceSupervisor(self, alias)
        return self._targets[alias]

    def status(self, alias=None):
        with self.locked():
            aliases = [alias] if alias is not None else list(self.c["devices"])
            if any(a not in self.c["devices"] for a in aliases):
                raise RecoveryError("Unknown device alias")
            # Status does not create/restart/reconfigure the server or act on devices.
            self.present = self.scan_usb()
            self.transports = {}
            details = self._inspect()
            ready = bool(details and self._matches(details) and details.get("State", {}).get("Running"))
            result = {}
            for name in aliases:
                if name not in self.present:
                    result[name] = {"connected": False, "reason": self.device_errors.get(name)}
                elif not ready:
                    result[name] = {"connected": False, "reason": "Manager requires reconciliation"}
                else:
                    try:
                        device = self.target(name)
                        device.connect()
                        result[name] = {"connected": True, "boot": device.boot(),
                                        "boot_completed": device.shell("getprop", "sys.boot_completed") == "1",
                                        "recover_on_boot": self.c["devices"][name]["recover_on_boot"],
                                        "state": device.read_state()}
                    except RecoveryError as exc:
                        result[name] = {"connected": False, "reason": str(exc)}
            return result[alias] if alias is not None else result

    def tick(self):
        with self.locked():
            self._reconcile()
            result = {}
            for alias, config in self.c["devices"].items():
                if not config.get("enabled", True):
                    result[alias] = {"result": "disabled"}
                elif not config["recover_on_boot"]:
                    result[alias] = {"result": "manual_only"}
                elif alias not in self.present:
                    result[alias] = {"result": "unavailable", "reason": self.device_errors.get(alias)}
                else:
                    try:
                        result[alias] = self.target(alias).tick()
                    except RecoveryError as exc:
                        result[alias] = {"result": "unavailable", "reason": str(exc)}
            return result

    def _operate(self, alias, action):
        with self.locked():
            device = self.target(alias)
            device.read_state()  # Corrupt checkpoints must not lead to recovery actions.
            self._reconcile()
            device.connect()
            return action(device)

    def recover(self, alias):
        return self._operate(alias, lambda device: device.recover())

    def wake(self, alias):
        def action(device):
            device.wake()
            return {"result": "wake_sent"}
        return self._operate(alias, action)

    def reboot(self, alias):
        def action(device):
            device.adb("reboot")
            return {"result": "reboot_sent"}
        return self._operate(alias, action)

    def swipe(self, alias, coordinates, duration=500):
        def action(device):
            device.swipe(coordinates, duration)
            return {"result": "swipe_sent"}
        return self._operate(alias, action)

    def screenshot(self, alias, path):
        capture = self.adb(alias, "exec-out", "screencap", "-p")
        if not capture.startswith(b"\x89PNG\r\n\x1a\n") or len(capture) > 32 * 1024 * 1024:
            raise RecoveryError("Invalid screenshot")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(capture)
        return {"result": "captured", "path": str(path)}

    def _forward(self, alias, args):
        """Forward's local port namespace is server-wide, despite using -s."""
        text = _text(self._device_adb(alias, ("forward", "--list")))
        entries = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != 3:
                raise RecoveryError("Invalid ADB forward inventory")
            entries.append(parts)
        serial = self.c["devices"][alias]["target"]["serial"]
        options = list(args[1:])
        if options == ["--list"]:
            rows = [" ".join(entry) for entry in entries if entry[0] == serial]
            return ("\n".join(rows) + ("\n" if rows else "")).encode()
        if len(options) == 2 and options[0] == "--remove":
            owners = [entry for entry in entries if entry[1] == options[1]]
            if not owners:
                return b""
            if len(owners) != 1 or owners[0][0] != serial:
                raise RecoveryError("ADB forward belongs to another device")
            return self._device_adb(alias, ("forward", *options))
        if options[:1] == ["--no-rebind"]:
            options = options[1:]
        if len(options) != 2 or any(option.startswith("--") for option in options):
            raise RecoveryError("Unsupported ADB forward command")
        local, remote = options
        owners = [entry for entry in entries if entry[1] == local]
        if owners:
            if len(owners) == 1 and owners[0] == [serial, local, remote]:
                return b""
            raise RecoveryError("ADB forward already exists; remove the owning device's mapping first")
        # A competing client can create the local port after the inventory read.
        # --no-rebind makes that race fail rather than stealing its connection.
        return self._device_adb(alias, ("forward", "--no-rebind", local, remote))

    def adb(self, alias, *args):
        if not args or any(not isinstance(a, str) or "\0" in a for a in args):
            raise RecoveryError("ADB command required")
        if args[0].startswith("-") or args[0] in FORBIDDEN_ADB:
            raise RecoveryError("Global or transport-changing ADB command refused")
        if args[0] == "forward" and "--remove-all" in args[1:]:
            raise RecoveryError("Removing all ADB forwards is a global operation")
        # Trusted operator shell access remains available, but routine wrappers
        # must not enable network ADB by changing daemon properties.
        if args[0] == "shell" and any(re.search(r"(?:service\.adb\.tcp\.port|persist\.adb\.tcp\.port|adb\s+tcpip)", arg) for arg in args[1:]):
            raise RecoveryError("Network ADB configuration refused")
        output = self._operate(
            alias, lambda device: self._forward(alias, args) if args[0] == "forward"
            else self._device_adb(alias, args)
        )
        return output if isinstance(output, bytes) else output.encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("alias", nargs="?")
    for command in ("run", "tick"):
        sub.add_parser(command)
    for command in ("recover", "wake", "reboot"):
        sub.add_parser(command).add_argument("alias")
    swipe = sub.add_parser("swipe")
    swipe.add_argument("alias")
    swipe.add_argument("coordinates", type=int, nargs=4)
    swipe.add_argument("--duration", type=int, default=500)
    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("alias")
    screenshot.add_argument("path")
    adb = sub.add_parser("adb")
    adb.add_argument("alias")
    adb.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    manager = Manager(json.loads(Path(args.config).read_text()))
    if args.command == "run":
        previous = None
        while True:
            try:
                result = manager.tick()
            except (RecoveryError, OSError, ValueError) as exc:
                result = {"result": "unavailable", "reason": str(exc)}
            if result != previous:
                print(json.dumps(result), flush=True)
                previous = result
            manager.sleep(manager.c.get("poll_seconds", 5))
    elif args.command == "adb":
        raw = args.args[1:] if args.args[:1] == ["--"] else args.args
        sys.stdout.buffer.write(manager.adb(args.alias, *raw))
        sys.stdout.buffer.flush()
    elif args.command == "swipe":
        print(json.dumps(manager.swipe(args.alias, args.coordinates, args.duration)))
    elif args.command == "screenshot":
        print(json.dumps(manager.screenshot(args.alias, args.path)))
    else:
        method = getattr(manager, args.command)
        result = method(args.alias) if hasattr(args, "alias") else method()
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (RecoveryError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
