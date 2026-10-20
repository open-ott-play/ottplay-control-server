"""Exercise manager safety boundaries without Docker, USB hardware or SSH."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

if os.name != "posix":
    raise unittest.SkipTest("USB host management requires POSIX file locks")

SPEC = importlib.util.spec_from_file_location(
    "usb_manager", Path(__file__).parents[1] / "tools/android_usb_manager.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
REMOTE_PATH = Path(__file__).parents[1] / "tools/android_usb_manager_remote.py"
REMOTE_SPEC = importlib.util.spec_from_file_location("usb_manager_remote", REMOTE_PATH)
remote = importlib.util.module_from_spec(REMOTE_SPEC)
REMOTE_SPEC.loader.exec_module(remote)

BOOT_A = "00000000-0000-0000-0000-000000000001"
BOOT_B = "00000000-0000-0000-0000-000000000002"
BOOT_C = "00000000-0000-0000-0000-000000000003"


class FakeDocker:
    """A stateful Docker/ADB boundary, not an alternative manager."""

    def __init__(self, config):
        self.config = config
        self.commands = []
        self.details = None
        self.devices = {}
        self.extra_transports = []
        self.foreground = "play.ott.foss"
        self.keyguard = {"secure": False, "locked": False}
        self.capture = b"\x89PNG\r\n\x1a\n\x00\xffbinary-output"
        self.forwards = {}
        self.port_occupied_on_create = None
        self.dispatched = []

    def __call__(self, argv):
        self.commands.append(list(argv))
        command, args = argv[1], argv[2:]
        if command == "ps":
            return self.config["container_name"] if self.details else ""
        if command == "inspect":
            return json.dumps([self.details])
        if command == "rm":
            self.details = None
            return ""
        if command == "start":
            self.details["State"]["Running"] = True
            return ""
        if command == "run":
            self.details = self._created_container(args)
            return "synthetic-container-id"
        if command != "exec" or args[:2] != [self.config["container_name"], "adb"]:
            raise AssertionError("Unexpected host operation: " + repr(argv))
        adb = args[2:]
        if adb == ["forward", "--list"]:
            return self._forward(None, ["--list"])
        if adb == ["devices", "-l"]:
            rows = []
            for serial, device in self.devices.items():
                rows.append(
                    f"{serial} {device['state']} usb:{device['usb']} "
                    f"model:{device['model']} transport_id:{device['transport']}"
                )
            return "List of devices attached\n" + "\n".join(rows + self.extra_transports)
        if adb[:1] == ["-s"]:
            if any(line.split()[0] == adb[1] for line in self.extra_transports):
                raise mod.RecoveryError("ADB serial is ambiguous")
            device = self.devices.get(adb[1])
        elif adb[:1] == ["-t"]:
            device = next(
                (item for item in self.devices.values() if str(item["transport"]) == adb[1]),
                None,
            )
        else:
            raise AssertionError("Device operation lacked an explicit device selector")
        if device is None:
            raise mod.RecoveryError("Transport operation failed; inspect private host logs")
        request = adb[2:]
        self.dispatched.append((device["serial"], request))
        if request[:1] == ["forward"]:
            return self._forward(device["serial"], request[1:])
        if request == ["get-state"]:
            return device["state"]
        if request == ["shell", "getprop", "ro.product.model"]:
            return device["model"]
        if request == ["shell", "getprop", "ro.build.version.sdk"]:
            return str(device["sdk"])
        if request == ["shell", "settings", "get", "secure", "android_id"]:
            return device["android_id"]
        if request == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            return device["boot"]
        if request == ["shell", "getprop", "sys.boot_completed"]:
            return device["boot_completed"]
        if request[:3] == ["shell", "am", "broadcast"]:
            return "Broadcast completed: result=0, data=" + json.dumps(self.keyguard)
        if request == ["shell", "dumpsys", "activity", "activities"]:
            return "mResumedActivity: ActivityRecord{ " + self.foreground + "/.MainActivity }"
        if request == ["shell", "dumpsys", "window", "policy"]:
            return "mUnrestrictedScreen=(0,0) 1280x800\nmShowingLockscreen=false"
        if request == ["shell", "dumpsys", "trust"]:
            return "deviceSecure=false"
        if request == ["exec-out", "screencap", "-p"]:
            return self.capture
        if request == ["reboot"] or request[:2] in (
            ["shell", "input"], ["shell", "monkey"]
        ):
            return ""
        raise AssertionError("Unexpected device operation: " + repr(request))

    def _forward(self, serial, args):
        if args == ["--list"]:
            # ADB's forward list is host-global even with a device selector.
            return "\n".join(
                f"{owner} {local} {destination}"
                for local, (owner, destination) in self.forwards.items()
            )
        if args[:1] == ["--remove"]:
            self.forwards.pop(args[1], None)
            return ""
        if self.port_occupied_on_create:
            local, owner, destination = self.port_occupied_on_create
            self.forwards[local] = (owner, destination)
            self.port_occupied_on_create = None
        no_rebind = args[:1] == ["--no-rebind"]
        local, destination = args[1:] if no_rebind else args
        if no_rebind and local in self.forwards:
            raise mod.RecoveryError("Transport operation failed; inspect private host logs")
        self.forwards[local] = (serial, destination)
        return ""

    def _created_container(self, args):
        """Interpret launch flags so tests verify the actual container contract."""
        host = {
            "Devices": [], "CapDrop": [], "SecurityOpt": [],
            "Tmpfs": {}, "RestartPolicy": {},
        }
        config = {"Labels": {}, "Env": []}
        mounts = []
        position = 0
        value_flags = {
            "--name", "--label", "--network", "--user", "--cap-drop",
            "--security-opt", "--restart", "--tmpfs", "--env", "--mount",
            "--entrypoint", "--device", "--memory", "--memory-swap", "--cpu-shares",
        }
        while position < len(args):
            flag = args[position]
            if flag == self.config["image"]:
                config.update(Image=flag, Cmd=args[position + 1:])
                break
            if flag == "-d":
                position += 1
                continue
            if flag == "--read-only":
                host["ReadonlyRootfs"] = True
                position += 1
                continue
            if flag == "--no-healthcheck":
                config["Healthcheck"] = {"Test": ["NONE"]}
                position += 1
                continue
            if flag not in value_flags:
                raise AssertionError("Unexpected container option: " + flag)
            value = args[position + 1]
            if flag == "--label":
                key, val = value.split("=", 1)
                config["Labels"][key] = val
            elif flag == "--network":
                host["NetworkMode"] = value
            elif flag == "--user":
                config["User"] = value
            elif flag == "--cap-drop":
                host["CapDrop"].append(value)
            elif flag == "--security-opt":
                host["SecurityOpt"].append(value)
            elif flag == "--restart":
                host["RestartPolicy"]["Name"] = value
            elif flag == "--tmpfs":
                destination, options = value.split(":", 1)
                host["Tmpfs"][destination] = options
            elif flag == "--env":
                config["Env"].append(value)
            elif flag == "--mount":
                values = dict(
                    item.split("=", 1) for item in value.split(",") if "=" in item
                )
                mounts.append({
                    "Type": values["type"], "Source": values["src"],
                    "Destination": values["dst"],
                    "RW": "readonly" not in value.split(","),
                })
            elif flag == "--entrypoint":
                config["Entrypoint"] = [value]
            elif flag == "--device":
                source, destination, permissions = value.split(":")
                host["Devices"].append({
                    "PathOnHost": source, "PathInContainer": destination,
                    "CgroupPermissions": permissions,
                })
            elif flag in ("--memory", "--memory-swap", "--cpu-shares"):
                key = {"--memory": "Memory", "--memory-swap": "MemorySwap",
                       "--cpu-shares": "CpuShares"}[flag]
                host[key] = int(value)
            position += 2
        return {
            "Config": config, "HostConfig": host, "Mounts": mounts,
            "State": {"Running": True},
        }

    def device_mutations(self):
        requests = [
            cmd[6:] for cmd in self.commands
            if cmd[1] == "exec" and cmd[4] in ("-t", "-s")
        ]
        return [
            request for request in requests
            if request[:1] == ["reboot"]
            or request[:2] in (["shell", "input"], ["shell", "monkey"])
        ]

    def container_mutations(self):
        return [cmd for cmd in self.commands if cmd[1] in ("rm", "run", "start")]


class ManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "usb").mkdir()
        keys = self.root / "keys"
        (keys / "vendor").mkdir(parents=True)
        for name in ("adbkey", "adbkey.pub"):
            (keys / name).write_text("synthetic-test-key")
            (keys / name).chmod(0o600)
        self.config = {
            "version": 2, "docker_path": "docker",
            "image": "sha256:" + "a" * 64,
            "container_name": "test-android-manager",
            "adb_key_dir": str(keys), "state_dir": str(self.root / "state"),
            "sysfs_root": str(self.root / "usb"),
            "devices": {
                alias: {
                    "target": {
                        "serial": serial, "product": "Fire", "model": "TestFire",
                        "sdk": 22, "android_id": android_id,
                        "vendor_id": "1949", "product_id": "0001",
                    },
                    "package": "play.ott.foss", "recover_on_boot": True,
                }
                for alias, serial, android_id in (
                    ("alpha", "SERIAL-A", "0123456789abcdef"),
                    ("beta", "SERIAL-B", "fedcba9876543210"),
                )
            },
        }
        self.docker = FakeDocker(self.config)
        self.manager = mod.Manager(self.config, runner=self.docker, sleep=lambda _: None)

    def attach(self, alias="alpha", name="1-1", devnum=3, transport=1):
        target = self.config["devices"][alias]["target"]
        path = self.root / "usb" / name
        path.mkdir(exist_ok=True)
        values = {
            "serial": target["serial"], "product": target["product"],
            "idVendor": target["vendor_id"], "idProduct": target["product_id"],
            "busnum": "1", "devnum": str(devnum),
        }
        for key, value in values.items():
            (path / key).write_text(value)
        self.docker.devices[target["serial"]] = {
            **target, "usb": name, "transport": transport, "state": "device",
            "boot": BOOT_A if alias == "alpha" else BOOT_B,
            "boot_completed": "1",
        }
        return path

    def save_checkpoint(self, alias, boot=BOOT_A):
        self.manager.target(alias).save_state({
            "observed_boot": boot, "completed_boot": boot,
            "attempts": 0, "result": "recovered",
        })

    def test_absent_device_does_not_block_present_device(self):
        self.attach("beta", name="1-2", devnum=4, transport=2)
        result = self.manager.tick()
        self.assertEqual(result["alpha"]["result"], "unavailable")
        self.assertEqual(result["beta"]["result"], "armed_existing_boot")
        self.assertEqual(
            [item["PathOnHost"] for item in self.docker.details["HostConfig"]["Devices"]],
            ["/dev/bus/usb/001/004"],
        )
        self.assertEqual(self.docker.device_mutations(), [])

    def test_new_server_has_exact_usb_and_no_network_or_key_generation(self):
        self.attach()
        self.manager.reconcile()
        details = self.docker.details
        self.assertEqual(details["Config"]["Entrypoint"], ["adb"])
        self.assertEqual(
            details["Config"]["Cmd"],
            ["-L", "tcp:5037", "nodaemon", "server"],
        )
        self.assertEqual(details["HostConfig"]["NetworkMode"], "none")
        self.assertTrue(details["HostConfig"]["ReadonlyRootfs"])
        self.assertEqual(details["HostConfig"]["CapDrop"], ["ALL"])
        self.assertEqual(details["HostConfig"]["Memory"], 256 * 1024 * 1024)
        self.assertEqual(details["HostConfig"]["MemorySwap"], 256 * 1024 * 1024)
        self.assertEqual(details["HostConfig"]["CpuShares"], 128)
        self.assertNotIn("PidsLimit", details["HostConfig"])
        self.assertNotIn("CpuQuota", details["HostConfig"])
        self.assertEqual(details["Config"]["Healthcheck"]["Test"], ["NONE"])
        self.assertEqual(details["Mounts"], [{
            "Source": self.config["adb_key_dir"], "Destination": "/root/.android",
            "RW": False, "Type": "bind",
        }])
        self.assertEqual(details["HostConfig"]["Devices"], [{
            "PathOnHost": "/dev/bus/usb/001/003", "PathInContainer": "/dev/bus/usb/001/003",
            "CgroupPermissions": "rw",
        }])
        self.docker.commands.clear()
        self.manager.reconcile()
        self.assertEqual(self.docker.container_mutations(), [])

    def test_wrong_android_identity_prevents_wake_and_launch(self):
        self.attach()
        for field, wrong in (("model", "AnotherModel"), ("sdk", 99), ("android_id", "0" * 16)):
            with self.subTest(field=field):
                device = self.docker.devices["SERIAL-A"]
                correct = device[field]
                device[field] = wrong
                self.docker.commands.clear()
                with self.assertRaisesRegex(mod.RecoveryError, "identity mismatch"):
                    self.manager.recover("alpha")
                self.assertEqual(self.docker.device_mutations(), [])
                device[field] = correct

    def test_wrong_usb_identity_is_never_mapped(self):
        path = self.attach()
        for filename in ("product", "idVendor", "idProduct"):
            with self.subTest(filename=filename):
                original = (path / filename).read_text()
                (path / filename).write_text("ffff")
                self.assertNotIn("alpha", self.manager.reconcile()["mapped"])
                self.assertEqual(self.docker.details["HostConfig"]["Devices"], [])
                (path / filename).write_text(original)

    def test_duplicate_configured_serial_is_refused(self):
        self.config["devices"]["beta"]["target"]["serial"] = "SERIAL-A"
        with self.assertRaisesRegex(mod.RecoveryError, "Duplicate"):
            mod.Manager(self.config, runner=self.docker)
        self.assertEqual(self.docker.commands, [])

    def test_duplicate_physical_serial_fails_closed_without_blocking_other_alias(self):
        self.attach()
        self.attach(name="1-3", devnum=5, transport=3)
        self.attach("beta", name="1-2", devnum=4, transport=2)
        result = self.manager.tick()
        self.assertEqual(result["alpha"]["result"], "unavailable")
        self.assertIn("Ambiguous", result["alpha"]["reason"])
        self.assertEqual(result["beta"]["result"], "armed_existing_boot")
        self.assertEqual(self.docker.device_mutations(), [])

    def test_duplicate_adb_serial_and_wrong_usb_path_prevent_mutation(self):
        self.attach()
        self.docker.extra_transports = [
            "SERIAL-A device usb:1-9 model:TestFire transport_id:8"
        ]
        with self.assertRaisesRegex(mod.RecoveryError, "Ambiguous ADB serial"):
            self.manager.wake("alpha")
        self.assertEqual(self.docker.device_mutations(), [])
        self.docker.extra_transports = []
        self.docker.devices["SERIAL-A"]["usb"] = "1-9"
        with self.assertRaisesRegex(mod.RecoveryError, "USB path mismatch"):
            self.manager.wake("alpha")
        self.assertEqual(self.docker.device_mutations(), [])

    def test_foreign_container_is_not_changed(self):
        self.attach()
        self.manager.reconcile()
        self.docker.details["Config"]["Labels"] = {"another.owner": "somebody"}
        self.docker.commands.clear()
        with self.assertRaisesRegex(mod.RecoveryError, "unrelated owner"):
            self.manager.reconcile()
        self.assertEqual(self.docker.container_mutations(), [])

    def test_owned_container_with_excess_access_is_replaced(self):
        self.attach()
        self.manager.reconcile()
        safe = copy.deepcopy(self.docker.details)
        unsafe_host_settings = (
            ("Privileged", True), ("NetworkMode", "host"), ("ReadonlyRootfs", False),
            ("CapAdd", ["SYS_ADMIN"]), ("DeviceCgroupRules", ["c *:* rwm"]),
            ("PidMode", "host"), ("IpcMode", "host"), ("UTSMode", "host"),
            ("Binds", ["/:/host"]), ("PortBindings", {"5037/tcp": [{}]}),
            ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
            ("Tmpfs", {"/tmp": "rw,nosuid"}),
            ("Memory", 0), ("MemorySwap", -1), ("CpuShares", 1024),
            ("PidsLimit", 100), ("CpuQuota", 50000), ("NanoCpus", 500000000),
        )
        for key, value in unsafe_host_settings:
            with self.subTest(key=key):
                self.docker.details = copy.deepcopy(safe)
                self.docker.details["HostConfig"][key] = value
                self.docker.commands.clear()
                self.manager.reconcile()
                self.assertEqual(
                    [cmd[1] for cmd in self.docker.container_mutations()], ["rm", "run"]
                )
        self.docker.details = copy.deepcopy(safe)
        self.docker.details["Mounts"][0]["RW"] = True
        self.docker.commands.clear()
        self.manager.reconcile()
        self.assertEqual([cmd[1] for cmd in self.docker.container_mutations()], ["rm", "run"])

    def test_inherited_image_healthcheck_is_disabled(self):
        self.attach()
        self.manager.reconcile()
        inherited = {
            "Test": ["CMD", "python3", "/app/manager.py", "--config", "/config.json", "health"],
        }
        for healthcheck in (inherited, None, {}):
            with self.subTest(healthcheck=healthcheck):
                self.docker.details["Config"]["Healthcheck"] = healthcheck
                self.docker.commands.clear()
                self.manager.reconcile()
                self.assertEqual([cmd[1] for cmd in self.docker.container_mutations()], ["rm", "run"])
                self.assertEqual(self.docker.details["Config"]["Healthcheck"]["Test"], ["NONE"])

    def test_unavailable_pids_controller_does_not_cause_recreate_loop(self):
        self.attach()
        self.manager.reconcile()
        for value in (None, 0, -1):
            with self.subTest(pids_limit=value):
                self.docker.details["HostConfig"]["PidsLimit"] = value
                self.docker.commands.clear()
                self.manager.reconcile()
                self.assertEqual(self.docker.container_mutations(), [])

    def test_missing_keys_refused_before_deleting_owned_container(self):
        self.attach()
        self.manager.reconcile()
        self.docker.details["HostConfig"]["Privileged"] = True
        (self.root / "keys/adbkey").unlink()
        self.docker.commands.clear()
        with self.assertRaises(mod.RecoveryError):
            self.manager.reconcile()
        self.assertEqual(self.docker.commands, [])
        self.assertFalse((self.root / "keys/adbkey").exists())

    def test_private_keys_must_not_be_world_readable(self):
        (self.root / "keys/adbkey").chmod(0o644)
        with self.assertRaisesRegex(mod.RecoveryError, "owner-only"):
            self.manager.reconcile()
        self.assertEqual(self.docker.commands, [])

    def test_vendor_key_names_must_be_loaded_by_adb29(self):
        vendor = self.root / "keys/vendor"
        for name in ("previous-container", "previous-container.pub", "orphan.adb_key.pub"):
            with self.subTest(name=name):
                key = vendor / name
                key.write_text("synthetic-test-key")
                key.chmod(0o600)
                with self.assertRaisesRegex(mod.RecoveryError, r"\.adb_key"):
                    self.manager.reconcile()
                self.assertEqual(self.docker.commands, [])
                key.unlink()
        private = vendor / "previous-container.adb_key"
        private.write_text("synthetic-test-key")
        private.chmod(0o600)
        self.manager.reconcile()
        public = vendor / "previous-container.adb_key.pub"
        public.write_text("synthetic-public-key")
        public.chmod(0o644)
        self.docker.commands.clear()
        self.manager.reconcile()
        self.assertEqual(self.docker.container_mutations(), [])

    def test_status_does_not_start_a_missing_server(self):
        self.attach()
        status = self.manager.status("alpha")
        self.assertFalse(status["connected"])
        self.assertEqual(self.docker.container_mutations(), [])
        self.assertEqual(self.docker.device_mutations(), [])

    def test_boot_checkpoints_are_independent_and_survive_manager_restart(self):
        self.attach()
        self.attach("beta", name="1-2", devnum=4, transport=2)
        self.manager.tick()
        self.docker.devices["SERIAL-A"]["boot"] = BOOT_C
        self.docker.commands.clear()
        result = self.manager.tick()
        self.assertEqual(result["alpha"]["result"], "recovered")
        self.assertEqual(result["beta"]["result"], "same_boot_no_action")
        self.assertEqual(self.manager.target("alpha").read_state()["completed_boot"], BOOT_C)
        self.assertEqual(self.manager.target("beta").read_state()["completed_boot"], BOOT_B)
        restarted = mod.Manager(self.config, runner=self.docker, sleep=lambda _: None)
        self.docker.commands.clear()
        self.assertTrue(all(
            value["result"] == "same_boot_no_action" for value in restarted.tick().values()
        ))
        self.assertEqual(self.docker.device_mutations(), [])

    def test_manual_only_device_is_not_woken_on_new_boot(self):
        self.attach()
        self.manager.c["devices"]["alpha"]["recover_on_boot"] = False
        result = self.manager.tick()
        self.assertEqual(result["alpha"]["result"], "manual_only")
        self.assertEqual(self.docker.device_mutations(), [])
        self.assertFalse((self.root / "state/alpha/state.json").exists())

    def test_failed_recovery_has_bounded_retries_and_never_marks_success(self):
        self.attach()
        self.save_checkpoint("alpha")
        self.docker.devices["SERIAL-A"]["boot"] = BOOT_C
        self.docker.foreground = "other.application"
        for _ in range(3):
            self.assertEqual(self.manager.tick()["alpha"]["result"], "unavailable")
        self.docker.commands.clear()
        self.assertEqual(self.manager.tick()["alpha"]["result"], "attempts_exhausted")
        self.assertEqual(self.docker.device_mutations(), [])
        self.assertEqual(self.manager.target("alpha").read_state()["completed_boot"], BOOT_A)

    def test_malformed_checkpoints_fail_before_recovery_actions(self):
        self.attach()
        checkpoint = self.manager.target("alpha").root / "state.json"
        invalid = (
            "{", "[]", "{}", '{"observed_boot":"invalid"}',
            json.dumps({"observed_boot": BOOT_A, "attempts": -1}),
            json.dumps({"observed_boot": BOOT_A, "attempts": True}),
            json.dumps({"observed_boot": BOOT_A, "unknown": "field"}),
        )
        for raw in invalid:
            for command in ("tick", "recover"):
                with self.subTest(raw=raw, command=command):
                    checkpoint.write_text(raw)
                    self.docker.commands.clear()
                    if command == "tick":
                        self.assertEqual(self.manager.tick()["alpha"]["result"], "unavailable")
                    else:
                        with self.assertRaises(mod.RecoveryError):
                            self.manager.recover("alpha")
                    self.assertEqual(self.docker.device_mutations(), [])

    def test_state_symlink_is_not_followed(self):
        self.attach()
        directory = self.manager.target("alpha").root
        foreign = self.root / "foreign.json"
        original = json.dumps({"observed_boot": BOOT_A, "completed_boot": BOOT_A})
        foreign.write_text(original)
        (directory / "state.json").symlink_to(foreign)
        self.assertEqual(self.manager.tick()["alpha"]["result"], "unavailable")
        self.assertEqual(foreign.read_text(), original)
        self.assertEqual(self.docker.device_mutations(), [])

    def test_bad_checkpoint_on_one_device_does_not_block_another(self):
        self.attach()
        self.attach("beta", name="1-2", devnum=4, transport=2)
        (self.manager.target("alpha").root / "state.json").write_text("{")
        result = self.manager.tick()
        self.assertEqual(result["alpha"]["result"], "unavailable")
        self.assertEqual(result["beta"]["result"], "armed_existing_boot")
        self.assertEqual(self.docker.device_mutations(), [])

    def test_global_adb_commands_are_refused_before_any_host_operation(self):
        forbidden = [
            (name,) for name in (
                "start-server", "kill-server", "server", "nodaemon", "connect",
                "disconnect", "pair", "tcpip", "usb", "attach", "detach", "reconnect",
                "root", "unroot", "devices", "track-devices", "host-features",
            )
        ] + [
            ("-s", "SERIAL-B", "reboot"), ("-t", "2", "reboot"),
            ("-H", "remote", "devices"), ("forward", "--remove-all"),
            ("shell", "setprop", "service.adb.tcp.port", "5555"),
        ]
        for args in forbidden:
            with self.subTest(args=args):
                self.docker.commands.clear()
                with self.assertRaises(mod.RecoveryError):
                    self.manager.adb("alpha", *args)
                self.assertEqual(self.docker.commands, [])

    def test_raw_adb_selects_verified_serial_and_preserves_binary_output(self):
        self.attach(transport=42)
        result = self.manager.adb("alpha", "exec-out", "screencap", "-p")
        self.assertEqual(result, b"\x89PNG\r\n\x1a\n\x00\xffbinary-output")
        command = self.docker.commands[-1]
        self.assertEqual(command[4:], ["-s", "SERIAL-A", "exec-out", "screencap", "-p"])
        self.assertNotIn("-t", command)

    def test_server_restart_cannot_redirect_a_verified_alias_to_reused_transport_id(self):
        self.attach(transport=1)
        self.attach("beta", name="1-2", devnum=4, transport=2)
        with self.manager.locked():
            self.manager._reconcile()
            self.manager.target("alpha").connect()
            self.assertEqual(self.manager.transports["alpha"], "1")
            # ADB restarts while the physical USB paths and serials stay unchanged.
            # Its numeric IDs are reassigned, so the old ID now belongs to beta.
            self.docker.devices["SERIAL-A"]["transport"] = 2
            self.docker.devices["SERIAL-B"]["transport"] = 1
            self.docker.dispatched.clear()
            self.manager._device_adb("alpha", ("reboot",))
        self.assertEqual(self.docker.dispatched, [("SERIAL-A", ["reboot"])])

    def test_duplicate_serial_appearing_after_verification_is_rejected_at_dispatch(self):
        self.attach()
        with self.manager.locked():
            self.manager._reconcile()
            self.manager.target("alpha").connect()
            self.docker.extra_transports.append(
                "SERIAL-A device usb:1-9 model:TestFire transport_id:8"
            )
            self.docker.dispatched.clear()
            with self.assertRaises(mod.RecoveryError):
                self.manager._device_adb("alpha", ("reboot",))
        self.assertEqual(self.docker.dispatched, [])

    def test_forward_creation_forces_no_rebind_and_same_endpoint_is_idempotent(self):
        self.attach()
        self.manager.adb("alpha", "forward", "tcp:9222", "localabstract:webview")
        self.assertEqual(
            self.docker.forwards, {"tcp:9222": ("SERIAL-A", "localabstract:webview")}
        )
        self.assertIn(
            ["docker", "exec", self.config["container_name"], "adb", "-s", "SERIAL-A",
             "forward", "--no-rebind", "tcp:9222", "localabstract:webview"],
            self.docker.commands,
        )
        self.docker.commands.clear()
        self.manager.adb("alpha", "forward", "tcp:9222", "localabstract:webview")
        self.assertFalse(any("--no-rebind" in command for command in self.docker.commands))

    def test_forward_never_hijacks_or_removes_another_device_port(self):
        self.attach()
        self.manager.reconcile()
        self.docker.forwards["tcp:9222"] = ("SERIAL-B", "localabstract:other")
        for operation in (
            ("forward", "tcp:9222", "localabstract:webview"),
            ("forward", "--remove", "tcp:9222"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(mod.RecoveryError):
                    self.manager.adb("alpha", *operation)
                self.assertEqual(
                    self.docker.forwards["tcp:9222"], ("SERIAL-B", "localabstract:other")
                )

    def test_forward_list_filters_other_aliases_and_removal_preserves_them(self):
        self.attach()
        self.manager.reconcile()
        self.docker.forwards = {
            "tcp:9222": ("SERIAL-A", "localabstract:webview"),
            "tcp:9223": ("SERIAL-B", "localabstract:other"),
        }
        listing = self.manager.adb("alpha", "forward", "--list").decode()
        self.assertIn("SERIAL-A tcp:9222 localabstract:webview", listing)
        self.assertNotIn("SERIAL-B", listing)
        self.manager.adb("alpha", "forward", "--remove", "tcp:9222")
        self.assertEqual(
            self.docker.forwards, {"tcp:9223": ("SERIAL-B", "localabstract:other")}
        )

    def test_forward_no_rebind_also_protects_against_external_registration_race(self):
        self.attach()
        self.manager.reconcile()
        self.docker.port_occupied_on_create = (
            "tcp:9222", "SERIAL-B", "localabstract:other"
        )
        with self.assertRaises(mod.RecoveryError):
            self.manager.adb("alpha", "forward", "tcp:9222", "localabstract:webview")
        self.assertEqual(
            self.docker.forwards["tcp:9222"], ("SERIAL-B", "localabstract:other")
        )

    def test_swipe_requires_in_bounds_geometry_and_unprotected_keyguard(self):
        self.attach()
        self.assertEqual(
            self.manager.swipe("alpha", [640, 640, 640, 160], duration=600),
            {"result": "swipe_sent"},
        )
        self.assertEqual(self.docker.device_mutations(), [
            ["shell", "input", "swipe", "640", "640", "640", "160", "600"]
        ])
        self.docker.commands.clear()
        with self.assertRaisesRegex(mod.RecoveryError, "outside current display"):
            self.manager.swipe("alpha", [640, 800, 640, 160])
        self.assertEqual(self.docker.device_mutations(), [])
        self.docker.keyguard = {"secure": True, "locked": True}
        with self.assertRaisesRegex(mod.RecoveryError, "local authentication"):
            self.manager.swipe("alpha", [640, 640, 640, 160])
        self.assertEqual(self.docker.device_mutations(), [])

    def test_screenshot_is_private_and_never_overwrites_an_existing_path(self):
        self.attach()
        path = self.root / "screen.png"
        result = self.manager.screenshot("alpha", path)
        self.assertEqual(result["result"], "captured")
        self.assertEqual(path.read_bytes(), self.docker.capture)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        path.write_bytes(b"preserve-existing-file")
        with self.assertRaises(FileExistsError):
            self.manager.screenshot("alpha", path)
        self.assertEqual(path.read_bytes(), b"preserve-existing-file")
        self.assertEqual(self.docker.device_mutations(), [])

    def test_screenshot_rejects_invalid_or_oversized_capture_without_creating_file(self):
        path = self.root / "screen.png"
        for capture in (
            b"not-a-png",
            b"\x89PNG\r\n\x1a\n" + b"\0" * (32 * 1024 * 1024),
        ):
            with self.subTest(size=len(capture)):
                with mock.patch.object(self.manager, "adb", return_value=capture):
                    with self.assertRaisesRegex(mod.RecoveryError, "Invalid screenshot"):
                        self.manager.screenshot("alpha", path)
                self.assertFalse(path.exists())

    def test_usb_reenumeration_invalidates_bound_transport(self):
        path = self.attach()
        with self.manager.locked():
            self.manager._reconcile()
            self.manager.target("alpha").connect()
            (path / "devnum").write_text("7")
            self.docker.commands.clear()
            with self.assertRaisesRegex(mod.RecoveryError, "topology changed"):
                self.manager._device_adb("alpha", ("reboot",))
            self.assertEqual(self.docker.commands, [])

    def test_internal_operations_require_lock(self):
        for operation in (
            self.manager._reconcile,
            lambda: self.manager._bind("alpha"),
            lambda: self.manager._device_adb("alpha", ("reboot",)),
        ):
            with self.assertRaisesRegex(mod.RecoveryError, "lock required"):
                operation()
        self.assertEqual(self.docker.commands, [])

    def test_separate_manager_instances_serialize_container_reconciliation(self):
        second_calls = []
        second = mod.Manager(
            self.config,
            runner=lambda argv: second_calls.append(argv) or self.docker(argv),
            sleep=lambda _: None,
        )
        blocked = threading.Event()
        failures = []
        real_flock = mod.fcntl.flock

        def observe_flock(fd, flags):
            try:
                return real_flock(fd, flags)
            except BlockingIOError:
                blocked.set()
                raise

        def reconcile():
            try:
                second.reconcile()
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(mod.fcntl, "flock", side_effect=observe_flock):
            with self.manager.locked():
                thread = threading.Thread(target=reconcile)
                thread.start()
                self.assertTrue(blocked.wait(2), "second manager did not attempt its lock")
                self.assertEqual(second_calls, [])
            thread.join(3)
        self.assertFalse(thread.is_alive(), "second manager failed to acquire released lock")
        self.assertEqual(failures, [])
        self.assertTrue(second_calls)


class RemoteWrapperTests(unittest.TestCase):
    def test_remote_argv_quotes_paths_and_shell_metacharacters_literally(self):
        arguments = ["shell", "printf", "%s", "text with 'quotes'; $(touch /tmp/nope)"]
        command = remote.build_command([
            "--host", "nas-alias",
            "--remote-script", "/opt/usb tools/manager.py",
            "--remote-config", "/opt/usb tools/config.json",
            "adb", "alpha", "--", *arguments,
        ])
        self.assertEqual(command[:-1], [
            "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "--", "nas-alias",
        ])
        self.assertEqual(shlex.split(command[-1]), [
            "sudo", "-n", "/usr/bin/python3", "-B", "/opt/usb tools/manager.py",
            "--config", "/opt/usb tools/config.json", "adb", "alpha", "--", *arguments,
        ])

    def test_remote_rejects_host_injection_and_invalid_alias_or_paths(self):
        invalid = [
            ["--host=-oProxyCommand=bad", "status"],
            ["--host", "nas alias", "status"],
            ["--host", "nas", "status", "../other"],
            ["--host", "nas", "--remote-script", "relative.py", "status"],
            ["--host", "nas", "screenshot", "alpha", "relative.png"],
            ["--host", "nas", "swipe", "alpha", "1", "2", "3", "4", "--duration", "5"],
        ]
        for args in invalid:
            with self.subTest(args=args), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as caught:
                    remote.build_command(args)
                self.assertEqual(caught.exception.code, 2)

    def test_remote_main_preserves_binary_stdout_stderr_and_ssh_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ssh"
            path.write_text(
                "#!" + sys.executable + "\n"
                "import os\n"
                "os.write(1, b'\\x89PNG\\r\\n\\x1a\\n\\x00\\xff')\n"
                "os.write(2, b'synthetic ssh diagnostic\\n')\n"
                "raise SystemExit(23)\n"
            )
            path.chmod(0o700)
            result = subprocess.run(
                [
                    sys.executable, str(REMOTE_PATH),
                    "--host", "synthetic-host", "adb", "alpha", "--",
                    "exec-out", "screencap", "-p",
                ],
                env={**os.environ, "PATH": temporary + os.pathsep + os.environ["PATH"]},
                capture_output=True, timeout=10,
            )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, b"\x89PNG\r\n\x1a\n\x00\xff")
        self.assertEqual(result.stderr, b"synthetic ssh diagnostic\n")


if __name__ == "__main__":
    unittest.main()
