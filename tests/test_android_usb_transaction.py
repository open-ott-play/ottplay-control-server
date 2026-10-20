"""Verify that temporary device resources remain inside one host transaction."""
import importlib.util
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest import mock

if os.name != "posix":
    raise unittest.SkipTest("USB host transactions require POSIX file locks")

import test_android_usb_manager as manager_tests

SPEC = importlib.util.spec_from_file_location(
    "usb_transaction", Path(__file__).parents[1] / "tools/android_usb_transaction.py"
)
transaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transaction)
CONTAINER_ID = "b" * 64
CDP_SOURCE = "connect('__OTT_CDP_PORT__')"


class TransactionDocker(manager_tests.FakeDocker):
    def __init__(self, config):
        super().__init__(config)
        self.files = {}
        self.device_files = {"/sdcard/input.bin": b"\x00\xffdevice-file"}
        self.events = []
        self.next_port = 41000

    def __call__(self, argv):
        if argv[1] == "exec" and argv[4:6] == ["-s", "SERIAL-A"]:
            request = argv[6:]
            if request == ["shell", "ps"]:
                self.commands.append(argv)
                return b"USER PID PPID NAME\nshell 222 1 play.ott.foss"
            if request == ["shell", "cat", "/proc/net/unix"]:
                self.commands.append(argv)
                return b"0000: 0000 0000 0000 0000 @webview_devtools_remote_222"
            if request[:1] == ["pull"]:
                self.commands.append(argv)
                self.events.append(("pull", request[2]))
                self.files[request[2]] = self.device_files[request[1]]
                return b"1 file pulled"
            if request[:1] == ["install"]:
                self.commands.append(argv)
                if request[-1] not in self.files:
                    raise AssertionError("APK file must exist until install finishes")
                self.events.append(("install", request[-1], self.files[request[-1]]))
                return b"Success\n"
            if request[:1] == ["push"]:
                self.commands.append(argv)
                self.events.append(("push", request[1], self.files[request[1]]))
                self.device_files[request[2]] = self.files[request[1]]
                return b"1 file pushed"
        result = super().__call__(argv)
        if argv[1] == "run":
            self.details["Id"] = CONTAINER_ID
        return result.encode() if isinstance(result, str) else result

    def _forward(self, serial, args):
        if args[:2] == ["--no-rebind", "tcp:0"]:
            port = str(self.next_port)
            self.next_port += 1
            super()._forward(serial, ["--no-rebind", "tcp:" + port, args[2]])
            self.events.append(("forward", port))
            return port
        if args[:1] == ["--remove"]:
            self.events.append(("remove-forward", args[1]))
        return super()._forward(serial, args)


class TransactionTests(unittest.TestCase):
    attach = manager_tests.ManagerTests.attach

    def setUp(self):
        manager_tests.ManagerTests.setUp(self)
        self.docker = TransactionDocker(self.config)
        self.manager = manager_tests.mod.Manager(
            self.config, runner=self.checked_docker, sleep=lambda _: None
        )
        self.aux_commands = []
        self.aux_failure = None
        self.cdp_started = threading.Event()
        self.cdp_release = None
        self.transaction = transaction.Transactions(self.manager, runner=self.aux)
        self.attach()

    def checked_docker(self, argv):
        self.assertGreater(self.manager._lock_depth, 0, "host operation outside manager lock")
        return self.docker(argv)

    def aux(self, argv, *, input, capture_output, timeout):
        self.assertGreater(self.manager._lock_depth, 0, "auxiliary operation outside manager lock")
        self.assertTrue(capture_output)
        self.assertGreater(timeout, 0)
        self.aux_commands.append(argv)
        self.assertEqual(argv[:2], ["docker", "exec"])
        index = 3 if argv[2] == "-i" else 2
        self.assertEqual(argv[index], CONTAINER_ID, "auxiliary operation must pin container ID")
        command = argv[index + 1:]
        if command[:1] == ["rm"]:
            self.docker.events.append(("cleanup", command[-1]))
            self.docker.files.pop(command[-1], None)
            return subprocess.CompletedProcess(argv, 0, b"")
        if command[:1] == ["cat"]:
            self.docker.events.append(("read", command[-1]))
            if self.aux_failure == "cat":
                return subprocess.CompletedProcess(argv, 1, b"")
            return subprocess.CompletedProcess(argv, 0, self.docker.files[command[-1]])
        self.assertEqual(command[:2], ["python3", "-c"])
        if len(command) == 4:
            self.docker.events.append(("stage", command[-1]))
            self.assertIn("os.O_EXCL", command[2])
            self.assertIn("0o600", command[2])
            self.docker.files[command[-1]] = input
            if self.aux_failure == "stage":
                return subprocess.CompletedProcess(argv, 1, b"")
            return subprocess.CompletedProcess(argv, 0, b"")
        self.assertEqual(len(command), 3)
        self.assertNotIn(transaction.PORT_TOKEN, command[2])
        self.assertEqual(
            list(self.docker.forwards.values()),
            [("SERIAL-A", "localabstract:webview_devtools_remote_222")],
        )
        self.docker.events.append(("cdp",))
        self.cdp_started.set()
        if self.cdp_release is not None and not self.cdp_release.wait(5):
            raise AssertionError("test did not release CDP operation")
        return subprocess.CompletedProcess(
            argv, 1 if self.aux_failure == "cdp" else 0, b'{"value":42}'
        )

    def test_pull_keeps_read_and_cleanup_inside_one_lock(self):
        result = self.transaction.pull("alpha", "/sdcard/input.bin")
        self.assertEqual(result, b"\x00\xffdevice-file")
        self.assertEqual([event[0] for event in self.docker.events], ["pull", "read", "cleanup"])
        self.assertEqual(self.docker.files, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_cleans_up_when_read_fails(self):
        self.aux_failure = "cat"
        with self.assertRaises(transaction.TransactionError):
            self.transaction.pull("alpha", "/sdcard/input.bin")
        self.assertEqual(self.docker.files, {})
        self.assertEqual(self.docker.events[-1][0], "cleanup")
        self.assertEqual(self.manager._lock_depth, 0)

    def test_staging_uses_unique_files_and_retains_bytes_until_adb_finishes(self):
        for payload, arguments in (
            (b"apk-one", ["install", "-r", "@staged"]),
            (b"apk-two", ["push", "@staged", "/sdcard/copied.bin"]),
        ):
            self.transaction.staged_adb("alpha", payload, arguments)
        self.assertEqual(
            [event[0] for event in self.docker.events],
            ["stage", "install", "cleanup", "stage", "push", "cleanup"],
        )
        staged = [event[1] for event in self.docker.events if event[0] == "stage"]
        self.assertEqual(len(set(staged)), 2)
        self.assertEqual(self.docker.device_files["/sdcard/copied.bin"], b"apk-two")
        self.assertEqual(self.docker.files, {})

    def test_failed_staging_is_cleaned_up_without_installing(self):
        self.aux_failure = "stage"
        with self.assertRaises(transaction.TransactionError):
            self.transaction.staged_adb("alpha", b"apk", ["install", "@staged"])
        self.assertEqual([event[0] for event in self.docker.events], ["stage", "cleanup"])
        self.assertEqual(self.docker.files, {})

    def test_cdp_allocates_uses_and_removes_forward_inside_one_lock(self):
        result = self.transaction.cdp(
            "alpha", "play.ott.foss", CDP_SOURCE, {"expression": "1 + 1"},
            expected_model="TestFire",
        )
        self.assertEqual(result, b'{"value":42}')
        self.assertEqual(
            [event[0] for event in self.docker.events], ["forward", "cdp", "remove-forward"]
        )
        self.assertEqual(self.docker.forwards, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_cdp_failure_still_removes_only_its_forward(self):
        self.aux_failure = "cdp"
        with self.assertRaises(transaction.TransactionError):
            self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
        self.assertEqual(self.docker.events[-1][0], "remove-forward")
        self.assertEqual(self.docker.forwards, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_wrong_expected_model_fails_before_forward_or_auxiliary_program(self):
        with self.assertRaises(transaction.TransactionError):
            self.transaction.cdp(
                "alpha", "play.ott.foss", CDP_SOURCE, {}, expected_model="OtherModel"
            )
        self.assertEqual(self.docker.forwards, {})
        self.assertEqual(self.aux_commands, [])

    def test_invalid_staging_request_never_reaches_the_host(self):
        for arguments in (
            ["reboot"], ["shell", "@staged"], ["install", "--unexpected", "@staged"],
            ["push", "@staged", "relative"], ["push", "/unrelated", "/sdcard/file"],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(transaction.TransactionError):
                    self.transaction.staged_adb("alpha", b"data", arguments)
        self.assertEqual(self.docker.commands, [])
        self.assertEqual(self.aux_commands, [])

    def test_reconciliation_cannot_run_between_cdp_forward_and_cleanup(self):
        self.cdp_release = threading.Event()
        second_calls = []
        second = manager_tests.mod.Manager(
            self.config, runner=lambda argv: second_calls.append(argv) or self.docker(argv),
            sleep=lambda _: None,
        )
        blocked = threading.Event()
        failures = []
        real_flock = manager_tests.mod.fcntl.flock

        def flock(fd, flags):
            try:
                return real_flock(fd, flags)
            except BlockingIOError:
                blocked.set()
                raise

        def cdp():
            try:
                self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
            except Exception as exc:
                failures.append(exc)

        def reconcile():
            try:
                second.reconcile()
                self.docker.events.append(("reconciled",))
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(manager_tests.mod.fcntl, "flock", side_effect=flock):
            first_thread = threading.Thread(target=cdp)
            first_thread.start()
            try:
                self.assertTrue(self.cdp_started.wait(3), "CDP operation did not start")
                second_thread = threading.Thread(target=reconcile)
                second_thread.start()
                self.assertTrue(blocked.wait(3), "reconcile did not wait for transaction lock")
                self.assertEqual(second_calls, [])
                self.assertTrue(self.docker.forwards, "forward disappeared during CDP")
            finally:
                self.cdp_release.set()
                first_thread.join(4)
                if "second_thread" in locals():
                    second_thread.join(4)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            [event[0] for event in self.docker.events],
            ["forward", "cdp", "remove-forward", "reconciled"],
        )


if __name__ == "__main__":
    unittest.main()
