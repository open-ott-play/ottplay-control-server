"""Device shutdown selects an exact identity and never replays an uncertain effect."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import re
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("ott_device_power_test", Path(__file__).resolve().parents[1] / "cli/device_power.py")
power = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(power)


class Error(Exception):
    pass


class DevicePowerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config_path = str(Path(temporary.name) / "cli.json")
        self.device = "11111111-2222-4333-8444-555555555555"
        self.identity = {"version": 1, "backend": "adb", "serial": "192.0.2.10:5555",
                         "android_id": "0123456789abcdef", "hardware_serial": "PROJECTOR-123", "model": "Projector 2"}
        self.config = {"device_power": {self.device: copy.deepcopy(self.identity)}, "preserved": {"value": 7}}
        self.api = SimpleNamespace(Error=Error, write_private=mock.Mock(), television_module=mock.Mock(),
                                   read_json=mock.Mock(side_effect=lambda _: copy.deepcopy(self.config)))
        self.client = SimpleNamespace(group_cancelled=None)
        self.calls = []
        self.shutdown_reply = (0, b"", b"")

    def adb(self, executable, args, deadline, cancelled=None, mutation=False, on_wait=None):
        self.calls.append((list(args), mutation))
        if args == ["devices", "-l"]:
            return 0, b"List of devices attached\n192.0.2.10:5555 device product:p model:m transport_id:42\nOTHER device transport_id:9\n", b""
        if args == ["-t", "42", "shell", "settings", "get", "secure", "android_id"]:
            return 0, b"0123456789abcdef\n", b""
        if args[:4] == ["-t", "42", "shell", "getprop"]:
            return 0, (b"PROJECTOR-123\n" if args[4] == "ro.serialno" else b"Projector 2\n"), b""
        if args == ["-t", "42", "shell", "svc", "power"]:
            return 0, b"", b"usage: svc power shutdown\n Perform a runtime shutdown and power off the device.\n"
        if args == ["-t", "42", "shell", "dumpsys", "window"]:
            return 0, b"mCurrentFocus=Window{abc u0 play.ott.foss}\n", b""
        if args == ["-t", "42", "shell", "svc", "power", "shutdown"]:
            if isinstance(self.shutdown_reply, Exception):
                raise self.shutdown_reply
            return self.shutdown_reply
        raise AssertionError("Unexpected command: " + repr(args))

    def run_power(self, words=(), machine=True):
        out = io.StringIO()
        with mock.patch.object(power.shutil, "which", return_value="/sdk/adb"), \
                mock.patch.object(power, "_run", side_effect=self.adb), contextlib.redirect_stdout(out):
            code = power.run(self.api, self.client, self.config, self.config_path, self.device,
                             "p2", words, 10, machine)
        return code, out.getvalue()

    def test_grammar_is_closed_and_case_insensitive_for_commands(self):
        for words, expected in (([], "shutdown"), (["DEVICE"], "shutdown"), (["status"], "status"),
                                (["Unbind"], "unbind"), (["--help"], "help"), (["-h"], "help")):
            self.assertEqual(power.parse(self.api, words), {"action": expected})
        self.assertEqual(power.parse(self.api, ["BIND-ADB", "USB-A"]), {"action": "bind-adb", "serial": "USB-A"})
        for words in (["now"], ["device", "now"], ["status", "extra"], ["unbind", "extra"],
                      ["bind-adb"], ["bind-adb", "a", "b"], ["toggle"], ["--force"]):
            with self.subTest(words=words), self.assertRaises(Error):
                power.parse(self.api, words)

    def test_serials_reject_options_shell_text_and_unresolved_network_hosts(self):
        for value in ("-s", "a;shutdown", "x y", "a\nb", "host.example:5555", "192.0.2.3:0",
                      "192.0.2.3:65536", "0.0.0.0:5555", "224.0.0.1:5555", "[::]:5555", ""):
            with self.subTest(value=value), self.assertRaises(power.PowerError):
                power._serial(value)
        self.assertEqual(power._serial("[2001:db8::1]:5555"), ("[2001:db8::1]:5555", True))
        self.assertEqual(power._serial("USB-serial_A.0"), ("USB-serial_A.0", False))

    def test_bind_only_inspects_and_preserves_existing_config(self):
        self.config.pop("device_power")
        code, output = self.run_power(["bind-adb", "192.0.2.10:5555"])
        self.assertEqual(code, 0)
        self.assertEqual(self.config["device_power"][self.device], self.identity)
        self.assertEqual(self.config["preserved"], {"value": 7})
        self.api.write_private.assert_called_once_with(self.config_path, self.config)
        self.assertFalse(any(mutation for _, mutation in self.calls))
        self.assertTrue(json.loads(output)["identity_verified"])
        for secret in self.identity.values():
            if isinstance(secret, str) and secret not in ("adb",):
                self.assertNotIn(secret, output)

    def test_status_only_inspects_with_exact_transport_id(self):
        code, output = self.run_power(["status"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["supported"])
        self.assertEqual(len(self.calls), 5)
        self.assertTrue(all(args[:2] == ["-t", "42"] for args, _ in self.calls[1:]))
        self.assertFalse(any(mutation for _, mutation in self.calls))
        self.api.write_private.assert_not_called()
        self.api.television_module.assert_not_called()

    def test_shutdown_uses_one_graceful_command_after_identity_and_help(self):
        code, output = self.run_power()
        result = json.loads(output)
        self.assertEqual((code, result["outcome"], result["power_state_confirmed"]), (0, "accepted", False))
        effects = [args for args, mutation in self.calls if mutation]
        self.assertEqual(effects, [["-t", "42", "shell", "svc", "power", "shutdown"]])
        self.assertEqual(self.calls[4][0], ["-t", "42", "shell", "svc", "power"])
        self.api.television_module.assert_not_called()

    def test_timeout_disconnect_nonzero_or_output_is_uncertain_without_retry(self):
        for reply in (power.PowerError("deadline private-host", True), (1, b"", b"error: closed"),
                      (0, b"unexpected private data", b""), (0, b"", b"SecurityException private")):
            with self.subTest(reply=type(reply)):
                self.calls.clear()
                self.shutdown_reply = reply
                code, output = self.run_power()
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output)["outcome"], "uncertain")
                self.assertFalse(json.loads(output)["repeated"])
                self.assertEqual(sum(mutation for _, mutation in self.calls), 1)
                self.assertNotIn("private", output)
                self.api.television_module.assert_not_called()

    def test_identity_mismatch_blocks_before_help_and_mutation(self):
        for field, value in (("android_id", "abcdef0123456789"), ("hardware_serial", "OTHER"), ("model", "Other")):
            with self.subTest(field=field):
                self.config["device_power"][self.device] = {**self.identity, field: value}
                self.calls.clear()
                with self.assertRaisesRegex(Error, "identity changed"):
                    self.run_power()
                self.assertFalse(any(mutation for _, mutation in self.calls))
                self.assertFalse(any(args[-2:] == ["svc", "power"] for args, _ in self.calls))

    def test_invalid_binding_never_runs_adb_or_tv(self):
        bads = [{}, {**self.identity, "version": True}, {**self.identity, "backend": "shell"},
                {**self.identity, "android_id": "0" * 16}, {**self.identity, "android_id": "f" * 17},
                {**self.identity, "hardware_serial": "unknown"}, {**self.identity, "model": "bad\nmodel"},
                {**self.identity, "extra": "ignored?"}]
        for bad in bads:
            with self.subTest(bad=bad):
                self.config["device_power"][self.device] = bad
                with self.assertRaises(Error):
                    self.run_power()
                self.assertEqual(self.calls, [])
                self.api.television_module.assert_not_called()

    def test_no_method_support_means_no_effect(self):
        original = self.adb
        def no_support(*args, **kwargs):
            reply = original(*args, **kwargs)
            if args[1][-2:] == ["svc", "power"]:
                return 0, b"", b"usage: svc power stayon"
            return reply
        self.adb = no_support
        with self.assertRaisesRegex(Error, "does not advertise"):
            self.run_power()
        self.assertFalse(any(mutation for _, mutation in self.calls))

    def test_same_physical_device_cannot_be_bound_to_two_player_ids(self):
        self.config["device_power"]["another-player"] = copy.deepcopy(self.identity)
        with self.assertRaisesRegex(Error, "already bound"):
            self.run_power(["bind-adb", self.identity["serial"]])
        self.api.write_private.assert_not_called()
        self.assertFalse(any(mutation for _, mutation in self.calls))

    def test_duplicate_runtime_binding_also_blocks_shutdown(self):
        self.config["device_power"]["another-player"] = {**self.identity, "serial": "192.0.2.11:5555"}
        with self.assertRaisesRegex(Error, "already bound"):
            self.run_power()
        self.assertFalse(any(mutation for _, mutation in self.calls))

    def test_unbind_removes_only_local_exact_device(self):
        self.config["device_power"]["other"] = {"private": "unchanged"}
        code, _ = self.run_power(["unbind"])
        self.assertEqual(code, 0)
        self.assertEqual(self.config["device_power"], {"other": {"private": "unchanged"}})
        self.assertEqual(self.config["preserved"], {"value": 7})
        self.assertEqual(self.calls, [])
        self.api.television_module.assert_not_called()

    def test_failed_binding_write_does_not_change_in_memory_config(self):
        self.config.pop("device_power")
        before = copy.deepcopy(self.config)
        self.api.write_private.side_effect = OSError("write failed")
        with self.assertRaises(OSError):
            self.run_power(["bind-adb", self.identity["serial"]])
        self.assertEqual(self.config, before)

    def test_unpadded_android_id_is_valid_and_preserved(self):
        original = self.adb
        def unpadded(*args, **kwargs):
            reply = original(*args, **kwargs)
            if args[1][-1] == "android_id":
                return 0, b"f622cbdcdaa745a\n", b""
            return reply
        self.adb = unpadded
        self.run_power(["bind-adb", self.identity["serial"]])
        self.assertEqual(self.config["device_power"][self.device]["android_id"], "f622cbdcdaa745a")

    def test_concurrent_config_change_prevents_bind_and_unbind_overwrite(self):
        before = copy.deepcopy(self.config)
        self.api.read_json.side_effect = lambda _: {**before, "new_setting": "preserve"}
        for words in (["bind-adb", self.identity["serial"]], ["unbind"]):
            with self.subTest(words=words), self.assertRaisesRegex(Error, "configuration changed"):
                self.run_power(words)
            self.assertEqual(self.config, before)
            self.api.write_private.assert_not_called()

    def test_explicit_reboot_permission_denial_is_rejected_and_redacted(self):
        self.shutdown_reply = (1, b"", b"java.lang.SecurityException: private host requires android.permission.REBOOT\n")
        with self.assertRaisesRegex(Error, "lacks REBOOT permission") as failure:
            self.run_power()
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)
        self.api.television_module.assert_not_called()

    def test_missing_adb_blocks_local_binding_without_fallback(self):
        with mock.patch.object(power.shutil, "which", return_value=None), self.assertRaisesRegex(Error, "Platform Tools"):
            power.run(self.api, self.client, self.config, "unused", self.device, "p2", [], 10)
        self.api.television_module.assert_not_called()

    def test_tv_fallback_requires_configured_off_capability(self):
        self.config.pop("device_power")
        tv = self.api.television_module.return_value
        tv.read_binding.return_value = {"configured": True, "capabilities": ["status", "off"]}
        tv.run.return_value = 0
        code, output = self.run_power()
        self.assertEqual((code, output), (0, ""))
        self.assertEqual(tv.run.call_args.args[4], ["off"])
        self.assertEqual(tv.run.call_count, 1)
        self.assertEqual(self.calls, [])
        for binding in (None, {"configured": False, "capabilities": []}, {"configured": True, "capabilities": ["status"]}):
            tv.run.reset_mock()
            tv.read_binding.return_value = binding
            with self.assertRaisesRegex(Error, "unsupported or not configured"):
                self.run_power()
            tv.run.assert_not_called()

    def test_tv_status_never_sends_power_command(self):
        self.config.pop("device_power")
        tv = self.api.television_module.return_value
        tv.read_binding.return_value = {"configured": True, "capabilities": ["off"]}
        code, output = self.run_power(["status"])
        self.assertEqual((code, json.loads(output)["transport"]), (0, "television"))
        tv.run.assert_not_called()

    def test_tv_uncertainty_or_transport_error_is_not_replayed(self):
        self.config.pop("device_power")
        tv = self.api.television_module.return_value
        tv.read_binding.return_value = {"configured": True, "capabilities": ["off"]}
        tv.run.side_effect = Error("uncertain")
        with self.assertRaisesRegex(Error, "uncertain"):
            self.run_power()
        tv.run.assert_called_once()
        self.assertEqual(self.calls, [])

    def test_overview_prints_no_private_identity_and_does_no_io(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            power.overview_help(self.api, self.config, self.device, "p2")
        self.assertIn("ott p2 shutdown / status", out.getvalue())
        for value in (self.identity["serial"], self.identity["android_id"], self.identity["hardware_serial"]):
            self.assertNotIn(value, out.getvalue())
        self.api.television_module.assert_not_called()

    def test_explicit_network_target_connects_once_then_uses_transport(self):
        responses = iter([(0, b"List of devices attached\n", b""), (0, b"connected", b""),
                          self.adb("/sdk/adb", ["devices", "-l"], 0)])
        original = self.adb
        self.calls.clear()
        def connect(*args, **kwargs):
            if args[1] in (["devices", "-l"], ["connect", self.identity["serial"]]):
                self.calls.append((args[1], False))
                return next(responses)
            return original(*args, **kwargs)
        self.adb = connect
        code, _ = self.run_power(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[:3], [(["devices", "-l"], False), (["connect", "192.0.2.10:5555"], False),
                                         (["devices", "-l"], False)])

    def test_usb_absence_does_not_connect_or_scan(self):
        with mock.patch.object(power, "_run", return_value=(0, b"List of devices attached\n", b"")) as run:
            with self.assertRaisesRegex(power.PowerError, "not connected"):
                power._inspect("adb", "USB123", time.monotonic() + 10)
        run.assert_called_once()

    def test_unauthorized_offline_duplicate_and_missing_transport_are_rejected(self):
        for listing in (b"USB unauthorized\n", b"USB offline\n", b"USB device\n",
                        b"USB device transport_id:1\nUSB device transport_id:2\n",
                        b"USB device transport_id:1 transport_id:2\n"):
            with self.subTest(listing=listing), self.assertRaises(power.PowerError):
                power._listed_transport(listing, "USB")

    def test_help_and_invalid_timeouts_never_access_devices(self):
        code, output = self.run_power(["--help"], False)
        self.assertEqual(code, 0)
        self.assertIn("shutdown bind-adb", output)
        self.assertEqual(self.calls, [])
        for timeout in (0, 301, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(Error):
                power.run(self.api, self.client, self.config, "unused", self.device, "p2", [], timeout)


class BoundedProcessTests(unittest.TestCase):
    def test_read_command_returns_separate_bounded_output(self):
        child = "import sys;sys.stdout.buffer.write(b'ok\\n');sys.stderr.buffer.write(b'notice\\n')"
        code, out, err = power._run(sys.executable, ["-c", child], time.monotonic() + 5)
        self.assertEqual((code, out, err), (0, b"ok\n", b"notice\n"))

    def test_existing_deadline_prevents_process_creation(self):
        with mock.patch.object(power.subprocess, "Popen") as popen, self.assertRaises(power.PowerError) as failure:
            power._run(sys.executable, [], time.monotonic() - 1, mutation=True)
        self.assertFalse(failure.exception.uncertain)
        popen.assert_not_called()

    def test_timeout_after_spawn_is_uncertain_only_for_effects(self):
        for mutation in (False, True):
            started = time.monotonic()
            with self.subTest(mutation=mutation), self.assertRaises(power.PowerError) as failure:
                power._run(sys.executable, ["-c", "import time;time.sleep(10)"], started + .15, mutation=mutation)
            self.assertEqual(failure.exception.uncertain, mutation)
            self.assertLess(time.monotonic() - started, 2)

    def test_timeout_reaps_child_and_closes_both_pipes(self):
        processes = []
        original = power.subprocess.Popen
        def create(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        with mock.patch.object(power.subprocess, "Popen", side_effect=create), self.assertRaises(power.PowerError):
            power._run(sys.executable, ["-c", "import time;time.sleep(10)"], time.monotonic() + .15)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertTrue(processes[0].stderr.closed)

    def test_output_flood_stops_with_bounded_capture(self):
        started = time.monotonic()
        with self.assertRaisesRegex(power.PowerError, "output limit"):
            power._run(sys.executable, ["-c", "import sys;sys.stdout.write('x'*1000000);sys.stdout.flush()"], started + 5)
        self.assertLess(time.monotonic() - started, 2)

    def test_cancelled_group_never_spawns(self):
        event = threading.Event()
        event.set()
        with mock.patch.object(power.subprocess, "Popen") as popen, self.assertRaises(power.PowerError):
            power._run(sys.executable, [], time.monotonic() + 5, event, True)
        popen.assert_not_called()

    def test_spawn_failure_is_not_an_uncertain_mutation(self):
        with mock.patch.object(power.subprocess, "Popen", side_effect=OSError("private path")), self.assertRaises(power.PowerError) as failure:
            power._run("missing", [], time.monotonic() + 5, mutation=True)
        self.assertFalse(failure.exception.uncertain)
        self.assertNotIn("private", str(failure.exception))

    def test_wait_callback_can_finish_and_reap_blocked_command(self):
        processes = []
        original = power.subprocess.Popen
        def create(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        def confirmed():
            raise power._ConfirmationSubmitted()
        with mock.patch.object(power.subprocess, "Popen", side_effect=create), self.assertRaises(power._ConfirmationSubmitted):
            power._run(sys.executable, ["-c", "import time;time.sleep(10)"], time.monotonic() + 5,
                       mutation=True, on_wait=confirmed)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertTrue(processes[0].stderr.closed)


WINDOW = b"""WINDOW MANAGER WINDOWS
  Window #0 Window{abc123 u0 android}:
    mOwnerUid=1000 showForAllUsers=true package=android appop=NONE
    mAttrs={(0,0)(400x200) ty=KEYGUARD_DIALOG fmt=TRANSLUCENT}
  Window #1 Window{def456 u0 player}:
    mOwnerUid=12345 package=player
  mCurrentFocus=Window{abc123 u0 android}
"""
ABSENT_WINDOW = b"mCurrentFocus=Window{def456 u0 player}\n"
UI = b'''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0"><node package="android" class="android.widget.FrameLayout" bounds="[300,200][700,400]">
<node package="android" resource-id="android:id/alert_dialog_custom_title" class="android.widget.TextView" text="Power off" enabled="true" clickable="false" bounds="[320,210][680,250]"/>
<node package="android" resource-id="android:id/alert_dialog_custom_content" class="android.widget.TextView" text="Do you want to shut down?" enabled="true" clickable="false" bounds="[320,260][680,300]"/>
<node package="android" resource-id="android:id/alert_dialog_custom_btn_cancel" class="android.widget.Button" text="CANCEL" enabled="true" clickable="true" bounds="[320,350][400,380]"/>
<node package="android" resource-id="android:id/alert_dialog_custom_btn_comfirm" class="android.widget.Button" text="OK" enabled="true" clickable="true" bounds="[600,350][680,380]"/>
</node></hierarchy>'''


class ShutdownDialogTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.windows = [WINDOW, WINDOW, ABSENT_WINDOW]
        self.ui = UI
        self.tap_reply = (0, b"", b"")
        self.key_reply = (0, b"", b"")

    def adb(self, executable, args, deadline, cancelled=None, mutation=False, on_wait=None):
        self.calls.append((list(args), mutation))
        if args == ["-t", "42", "shell", "dumpsys", "window"]:
            return 0, self.windows.pop(0), b""
        if len(args) == 4 and args[:3] == ["-t", "42", "shell"] and args[3].startswith("umask 077;"):
            return 0, self.ui.pop(0) if isinstance(self.ui, list) else self.ui, b""
        if args[:5] == ["-t", "42", "shell", "input", "tap"]:
            return self.tap_reply
        if args == ["-t", "42", "shell", "input", "keyevent", "KEYCODE_DPAD_CENTER"]:
            return self.key_reply
        raise AssertionError("Unexpected ADB operation")

    def confirm(self):
        with mock.patch.object(power, "_run", side_effect=self.adb):
            return power._confirm_shutdown_dialog("adb", "42", time.monotonic() + 10)

    def test_system_owned_window_and_button_are_recognized(self):
        self.assertEqual(power._system_dialog_window(WINDOW), ("abc123", "u0"))
        self.assertEqual(power._shutdown_button(UI), (640, 365))

    def test_window_requires_exact_focus_uid_package_type_and_unique_section(self):
        for raw in (ABSENT_WINDOW, WINDOW.replace(b"mOwnerUid=1000", b"mOwnerUid=12345"),
                    WINDOW.replace(b"package=android", b"package=player"),
                    WINDOW.replace(b"ty=KEYGUARD_DIALOG", b"ty=APPLICATION_OVERLAY"),
                    WINDOW + b"mCurrentFocus=Window{abc123 u0 android}\n",
                    WINDOW.replace(b"Window #0 Window{abc123", b"Window #0 Window{another")):
            with self.subTest(raw=raw[:50]):
                self.assertIsNone(power._system_dialog_window(raw))

    def test_xml_requires_exact_system_dialog_no_guessed_buttons(self):
        bads = [UI.replace(b'package="android"', b'package="player"', 1),
                UI.replace(b"Power off", b"Restart"), UI.replace(b"Do you want to shut down?", b"Delete all data?"),
                UI.replace(b'btn_comfirm', b'button1'), UI.replace(b'text="OK"', b'text="YES"'),
                UI.replace(b'enabled="true"', b'enabled="false"'), UI.replace(b'clickable="true"', b'clickable="false"'),
                UI.replace(b'class="android.widget.Button"', b'class="android.widget.TextView"'),
                UI.replace(b'[600,350][680,380]', b'[600,350][900,380]'),
                UI.replace(b'[600,350][680,380]', b'[600,350][600,380]'),
                UI.replace(b'<hierarchy', b'<!DOCTYPE hierarchy []><hierarchy'), b'not XML',
                UI.replace(b'</node></hierarchy>', b'<node package="android" text="Unexpected"/></node></hierarchy>')]
        for raw in bads:
            with self.subTest(raw=raw[:60]):
                self.assertIsNone(power._shutdown_button(raw))

    def test_overlapping_cancel_and_confirm_buttons_are_rejected(self):
        for bounds in (b"[600,350][680,380]", b"[590,350][620,380]", b"[610,360][620,370]"):
            with self.subTest(bounds=bounds):
                changed = UI.replace(b"[320,350][400,380]", bounds)
                self.assertIsNone(power._shutdown_button(changed))
        self.assertIsNotNone(power._shutdown_button(UI.replace(b"[320,350][400,380]", b"[320,350][600,380]")))

    def test_plain_output_reports_confirmation_separately_from_power_state(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            power._print({"action": "shutdown", "outcome": "accepted", "confirmation_submitted": True}, False)
        self.assertIn("System power-off confirmation submitted.", out.getvalue())
        self.assertIn("Final power state has not been confirmed.", out.getvalue())

    def test_fresh_matching_dialog_taps_derived_positive_once(self):
        self.assertTrue(self.confirm())
        effects = [args for args, mutation in self.calls if mutation]
        self.assertEqual(effects, [["-t", "42", "shell", "input", "tap", "640", "365"]])
        self.assertEqual([args[-1] for args, _ in self.calls if args[-2:-1] == ["dumpsys"]], ["window", "window", "window"])
        script = self.calls[1][0][3]
        paths = re.findall(r"/data/local/tmp/\.ott-power-[0-9a-f]{32}\.xml", script)
        self.assertEqual(len(paths), 3)
        self.assertEqual(len(set(paths)), 1)
        self.assertIn("trap 'rm -f " + paths[0] + "' EXIT", script)
        self.assertTrue(script.startswith("umask 077;"))

    def test_changed_focus_never_taps(self):
        self.windows[1] = WINDOW.replace(b"abc123", b"abc124")
        self.assertFalse(self.confirm())
        self.assertFalse(any(mutation for _, mutation in self.calls))

    def test_unrecognized_dialog_never_taps(self):
        self.ui = UI.replace(b"Power off", b"Restart")
        self.assertFalse(self.confirm())
        self.assertFalse(any(mutation for _, mutation in self.calls))

    def test_app_window_never_dumps_ui_or_taps(self):
        self.windows = [ABSENT_WINDOW]
        self.assertFalse(self.confirm())
        self.assertEqual(len(self.calls), 1)

    def test_unconfirmed_tap_is_uncertain_and_not_repeated(self):
        self.tap_reply = (1, b"", b"closed")
        with self.assertRaises(power.PowerError) as failure:
            self.confirm()
        self.assertTrue(failure.exception.uncertain)
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)

    def test_existing_dialog_finishes_without_another_svc(self):
        with mock.patch.object(power, "_run", side_effect=self.adb):
            self.assertTrue(power._shutdown("adb", "42", time.monotonic() + 10))
        self.assertFalse(any("svc" in args for args, _ in self.calls))
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)

    def test_blocked_svc_is_confirmed_within_same_request(self):
        self.windows = [ABSENT_WINDOW, WINDOW, WINDOW, ABSENT_WINDOW]
        original = self.adb
        def blocked(executable, args, deadline, cancelled=None, mutation=False, on_wait=None):
            if args[-3:] == ["svc", "power", "shutdown"]:
                self.calls.append((args, mutation))
                self.assertTrue(callable(on_wait))
                on_wait()
                raise AssertionError("Confirmation must finish the blocked wait")
            return original(executable, args, deadline, cancelled, mutation, on_wait)
        with mock.patch.object(power, "_run", side_effect=blocked):
            self.assertTrue(power._shutdown("adb", "42", time.monotonic() + 10))
        effects = [args for args, mutation in self.calls if mutation]
        self.assertEqual(len(effects), 2)
        self.assertEqual(effects[0][-3:], ["svc", "power", "shutdown"])
        self.assertEqual(effects[1][-4:], ["input", "tap", "640", "365"])

    def test_post_svc_inspection_failure_stays_uncertain(self):
        self.windows = [ABSENT_WINDOW]
        original = self.adb
        def failed(executable, args, deadline, cancelled=None, mutation=False, on_wait=None):
            if args[-3:] == ["svc", "power", "shutdown"]:
                with mock.patch.object(power, "_confirm_shutdown_dialog", side_effect=power.PowerError("read timeout")):
                    on_wait()
            return original(executable, args, deadline, cancelled, mutation, on_wait)
        with mock.patch.object(power, "_run", side_effect=failed), self.assertRaises(power.PowerError) as failure:
            power._shutdown("adb", "42", time.monotonic() + 10)
        self.assertTrue(failure.exception.uncertain)

    def test_focused_positive_activates_with_one_guarded_center_and_no_tap(self):
        self.ui = UI.replace(b'text="OK"', b'text="OK" focused="true"')
        self.assertTrue(self.confirm())
        effects = [args for args, mutation in self.calls if mutation]
        self.assertEqual(effects, [["-t", "42", "shell", "input", "keyevent", "KEYCODE_DPAD_CENTER"]])

    def test_tap_that_only_focuses_is_followed_by_fresh_guarded_center(self):
        self.ui = [UI, UI.replace(b'text="OK"', b'text="OK" focused="true"')]
        self.windows = [WINDOW] * 4
        self.assertTrue(self.confirm())
        effects = [args for args, mutation in self.calls if mutation]
        self.assertEqual(effects, [["-t", "42", "shell", "input", "tap", "640", "365"],
                                  ["-t", "42", "shell", "input", "keyevent", "KEYCODE_DPAD_CENTER"]])

    def test_tap_without_positive_focus_never_sends_center(self):
        self.windows = [WINDOW] * 4
        with self.assertRaisesRegex(power.PowerError, "verified focus") as failure:
            self.confirm()
        self.assertTrue(failure.exception.uncertain)
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)

    def test_both_buttons_focused_are_not_validated_as_positive_focus(self):
        raw = UI.replace(b'text="OK"', b'text="OK" focused="true"').replace(b'text="CANCEL"', b'text="CANCEL" focused="true"')
        self.assertIsNone(power._shutdown_button(raw, require_focused=True))

    def test_changed_window_after_focus_tap_never_sends_center(self):
        self.ui = [UI, UI.replace(b'text="OK"', b'text="OK" focused="true"')]
        changed = WINDOW.replace(b"abc123", b"abc124")
        self.windows = [WINDOW, WINDOW, changed, changed]
        with self.assertRaises(power.PowerError):
            self.confirm()
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)

    def test_uncertain_center_is_not_retried(self):
        self.ui = UI.replace(b'text="OK"', b'text="OK" focused="true"')
        self.key_reply = (1, b"", b"closed")
        with self.assertRaises(power.PowerError) as failure:
            self.confirm()
        self.assertTrue(failure.exception.uncertain)
        self.assertEqual(sum(mutation for _, mutation in self.calls), 1)


class ConfigLockTests(unittest.TestCase):
    def test_two_concurrent_updates_cannot_both_compare_and_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli.json"
            original = {"original": True}
            path.write_text(json.dumps(original))
            entered, release = threading.Event(), threading.Event()
            failures = []
            def write(_, value):
                entered.set()
                release.wait(2)
                path.write_text(json.dumps(value))
            api = SimpleNamespace(read_json=lambda _: json.loads(path.read_text()), write_private=write)
            def first():
                try:
                    power._save(api, path, dict(original), original, {"winner": 1})
                except Exception as exc:
                    failures.append(exc)
            worker = threading.Thread(target=first)
            worker.start()
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(power.PowerError, "update is active"):
                    power._save(api, path, dict(original), original, {"loser": 2})
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(json.loads(path.read_text()), {"winner": 1})
            with self.assertRaisesRegex(power.PowerError, "configuration changed"):
                power._save(api, path, dict(original), original, {"loser": 2})

    @unittest.skipIf(sys.platform == "win32", "Unix private mode and symbolic link semantics")
    def test_symlink_or_public_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli.json"
            lock = Path(str(path) + ".device-power.lock")
            target = Path(directory) / "target"
            target.write_text("private")
            lock.symlink_to(target)
            with self.assertRaises(power.PowerError):
                with power._ConfigLock(path):
                    self.fail("unsafe lock entered")
            lock.unlink()
            lock.write_text("")
            lock.chmod(0o644)
            with self.assertRaises(power.PowerError):
                with power._ConfigLock(path):
                    self.fail("public lock entered")

    def test_lock_releases_after_comparison_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cli.json"
            api = SimpleNamespace(read_json=lambda _: {"changed": True}, write_private=mock.Mock())
            with self.assertRaises(power.PowerError):
                power._save(api, path, {}, {}, {"x": 1})
            with power._ConfigLock(path):
                pass
            api.write_private.assert_not_called()


if __name__ == "__main__":
    unittest.main()
