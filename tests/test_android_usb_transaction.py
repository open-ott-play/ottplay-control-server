"""Verify that temporary device resources remain inside one host transaction."""
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace
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
        self.ps_all = (0, "USER PID PPID VSZ RSS WCHAN ADDR S NAME\n"
                          "shell 222 1 100 10 0 0 S play.ott.foss")
        self.ps_legacy = (0, "USER PID PPID NAME\nshell 222 1 play.ott.foss")
        self.ps_calls = []
        self.ps_failure = None
        self.pull_limits = []
        self.pull_source_becomes_directory = False

    def __call__(self, argv):
        original = argv
        limit = None
        if argv[1:2] == ["exec"] and argv[3:5] == ["python3", "-c"]:
            limit = int(argv[6])
            self.pull_limits.append(limit)
            argv = argv[:3] + ["adb"] + argv[7:]
        if argv[1] == "exec" and argv[4:6] == ["-s", "SERIAL-A"]:
            request = argv[6:]
            if len(request) == 2 and request[0] == "shell" and request[1].startswith("if [ -f "):
                self.commands.append(original)
                path = shlex.split(request[1])[3]
                self.events.append(("probe", path))
                return (
                    b"__OTT_REGULAR_FILE__\r\n" if path in self.device_files
                    else b"__OTT_NOT_REGULAR__\r\n"
                )
            if len(request) == 2 and request[0] == "shell" and request[1].startswith(("ps -A ", "ps 2>")):
                self.commands.append(argv)
                if self.ps_failure:
                    raise self.ps_failure
                all_processes = request[1].startswith("ps -A ")
                self.ps_calls.append(all_processes)
                status, output = self.ps_all if all_processes else self.ps_legacy
                return (output + "\n__OTT_PS_STATUS__=" + str(status) + "\n").encode()
            if request == ["shell", "cat", "/proc/net/unix"]:
                self.commands.append(argv)
                return b"0000: 0000 0000 0000 0000 @webview_devtools_remote_222"
            if request[:1] == ["pull"]:
                self.commands.append(original)
                self.events.append(("pull", request[2]))
                if request[2] not in self.files:
                    raise AssertionError("Pull destination must be reserved before ADB")
                if self.pull_source_becomes_directory:
                    raise manager_tests.mod.RecoveryError("target is not a directory")
                contents = self.device_files[request[1]]
                self.files[request[2]] = contents if limit is None else contents[:limit]
                if limit is not None and len(contents) > limit:
                    raise manager_tests.mod.RecoveryError("File size limit exceeded")
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
            kind = "reserve" if input is None else "stage"
            self.docker.events.append((kind, command[-1]))
            if kind == "reserve":
                self.assertIn("os.O_EXCL", command[2])
                self.assertIn("0o600", command[2])
                if command[-1] in self.docker.files:
                    return subprocess.CompletedProcess(argv, 1, b"")
            else:
                self.assertIn("os.O_NOFOLLOW", command[2])
                self.assertNotIn("os.O_CREAT", command[2])
                self.assertNotIn("os.O_TRUNC", command[2])
                if self.docker.files.get(command[-1]) != b"":
                    return subprocess.CompletedProcess(argv, 1, b"")
            self.docker.files[command[-1]] = b"" if input is None else input
            if self.aux_failure == kind:
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
        self.assertEqual(
            [event[0] for event in self.docker.events],
            ["probe", "reserve", "pull", "read", "cleanup"],
        )
        self.assertEqual(self.docker.pull_limits, [transaction.MAX_PAYLOAD])
        self.assertEqual(self.docker.files, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_cleans_up_when_read_fails(self):
        self.aux_failure = "cat"
        with self.assertRaises(transaction.TransactionError):
            self.transaction.pull("alpha", "/sdcard/input.bin")
        self.assertEqual(self.docker.files, {})
        self.assertEqual(self.docker.events[-1][0], "cleanup")
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_rejects_nonregular_sources_before_reserving_or_transferring(self):
        for path in ("/sdcard", "/dev/zero", "/sdcard/socket", "/sdcard/missing"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(transaction.TransactionError, "regular device file"):
                    self.transaction.pull("alpha", path)
                self.assertEqual(self.aux_commands, [])
                self.assertEqual(self.docker.pull_limits, [])
                self.assertEqual(self.docker.files, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_quotes_shell_metacharacters_without_changing_file_identity(self):
        path = "/sdcard/a file'; $(touch unexpected) [1].bin"
        self.docker.device_files[path] = b"binary\x00data"
        self.assertEqual(self.transaction.pull("alpha", path), b"binary\x00data")
        self.assertEqual(self.docker.events[0], ("probe", path))
        self.assertEqual(self.docker.files, {})

    def test_pull_accepts_limit_but_discards_oversized_or_growing_source(self):
        limit = 1024
        for size in (0, limit, limit + 1, limit * 4):
            with self.subTest(size=size), mock.patch.object(transaction, "MAX_PAYLOAD", limit):
                self.docker.events.clear()
                self.docker.pull_limits.clear()
                self.docker.device_files["/sdcard/input.bin"] = b"x" * size
                if size <= limit:
                    self.assertEqual(
                        self.transaction.pull("alpha", "/sdcard/input.bin"), b"x" * size,
                    )
                else:
                    with self.assertRaisesRegex(manager_tests.mod.RecoveryError, "size limit"):
                        self.transaction.pull("alpha", "/sdcard/input.bin")
                    self.assertNotIn("read", [event[0] for event in self.docker.events])
                self.assertEqual(self.docker.pull_limits, [limit])
                self.assertEqual(self.docker.files, {})
                self.assertEqual(self.docker.events[-1][0], "cleanup")
                self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_directory_race_keeps_existing_file_and_cleans_up(self):
        self.docker.pull_source_becomes_directory = True
        with self.assertRaisesRegex(manager_tests.mod.RecoveryError, "not a directory"):
            self.transaction.pull("alpha", "/sdcard/input.bin")
        self.assertEqual(
            [event[0] for event in self.docker.events],
            ["probe", "reserve", "pull", "cleanup"],
        )
        self.assertEqual(self.docker.files, {})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_reservation_collision_preserves_existing_file_without_transfer(self):
        existing = "/tmp/ott-transaction-existing"
        self.docker.files[existing] = b"preserve unrelated file"
        with mock.patch.object(self.transaction, "_temporary", return_value=existing):
            with self.assertRaises(transaction.TransactionError):
                self.transaction.pull("alpha", "/sdcard/input.bin")
        self.assertEqual(
            [event[0] for event in self.docker.events], ["probe", "reserve"],
        )
        self.assertEqual(self.docker.pull_limits, [])
        self.assertEqual(self.docker.files, {existing: b"preserve unrelated file"})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_pull_reservation_is_a_private_regular_file_and_cannot_replace_one(self):
        self.transaction.pull("alpha", "/sdcard/input.bin")
        command = next(command for command in self.aux_commands if "-c" in command)
        source = command[command.index("-c") + 1]
        path = self.root / "reserved-pull"
        first = subprocess.run(
            [sys.executable, "-c", source, str(path)], capture_output=True, timeout=5,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_bytes(), b"")
        path.write_bytes(b"preserve unrelated contents")
        second = subprocess.run(
            [sys.executable, "-c", source, str(path)], capture_output=True, timeout=5,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(path.read_bytes(), b"preserve unrelated contents")

    def test_invalid_pull_path_is_rejected_before_host_operations(self):
        for path in (None, "", "relative", "/sdcard/a\0b"):
            with self.subTest(path=path):
                with self.assertRaises(transaction.TransactionError):
                    self.transaction.pull("alpha", path)
        self.assertEqual(self.docker.commands, [])
        self.assertEqual(self.aux_commands, [])

    def test_pull_and_staged_transfer_use_observed_container_id_not_mutable_name(self):
        self.transaction.pull("alpha", "/sdcard/input.bin")
        self.transaction.staged_adb("alpha", b"apk", ["install", "@staged"])
        self.transaction.staged_adb("alpha", b"data", ["push", "@staged", "/sdcard/output"])
        transfers = []
        for command in self.docker.commands:
            if command[1] != "exec":
                continue
            if command[3:5] == ["python3", "-c"]:
                request = command[9:]
            elif command[3:6] == ["adb", "-s", "SERIAL-A"]:
                request = command[6:]
            else:
                continue
            if request and request[0] in ("pull", "install", "push"):
                transfers.append(request[0])
                self.assertEqual(command[2], CONTAINER_ID)
        self.assertEqual(transfers, ["pull", "install", "push"])
        # The auxiliary runner independently enforces the same pinned ID for
        # reservation, reading, staging and cleanup.
        self.assertEqual(self.docker.files, {})

    def test_staging_uses_unique_files_and_retains_bytes_until_adb_finishes(self):
        for payload, arguments in (
            (b"apk-one", ["install", "-r", "@staged"]),
            (b"apk-two", ["push", "@staged", "/sdcard/copied.bin"]),
        ):
            self.transaction.staged_adb("alpha", payload, arguments)
        self.assertEqual(
            [event[0] for event in self.docker.events],
            ["reserve", "stage", "install", "cleanup", "reserve", "stage", "push", "cleanup"],
        )
        staged = [event[1] for event in self.docker.events if event[0] == "stage"]
        self.assertEqual(len(set(staged)), 2)
        self.assertEqual(self.docker.device_files["/sdcard/copied.bin"], b"apk-two")
        self.assertEqual(self.docker.files, {})

    def test_failed_staging_is_cleaned_up_without_installing(self):
        self.aux_failure = "stage"
        with self.assertRaises(transaction.TransactionError):
            self.transaction.staged_adb("alpha", b"apk", ["install", "@staged"])
        self.assertEqual(
            [event[0] for event in self.docker.events], ["reserve", "stage", "cleanup"],
        )
        self.assertEqual(self.docker.files, {})

    def test_staging_reservation_collision_does_not_write_install_or_delete(self):
        existing = "/tmp/ott-transaction-existing"
        self.docker.files[existing] = b"preserve unrelated file"
        with mock.patch.object(self.transaction, "_temporary", return_value=existing):
            with self.assertRaises(transaction.TransactionError):
                self.transaction.staged_adb("alpha", b"apk", ["install", "@staged"])
        self.assertEqual([event[0] for event in self.docker.events], ["reserve"])
        self.assertEqual(self.docker.files, {existing: b"preserve unrelated file"})
        self.assertEqual(self.manager._lock_depth, 0)

    def test_staging_writer_streams_bounded_chunks_into_private_exclusive_file(self):
        self.transaction.staged_adb("alpha", b"capture-source", ["install", "@staged"])
        command = next(command for command in self.aux_commands if "-i" in command)
        source = command[command.index("-c") + 1]
        payload = b"\x00\xff" * (1024 * 1024 + 19)
        reads = []

        class BoundedReader(io.BytesIO):
            def read(inner, size=-1):
                self.assertGreater(size, 0, "staging must not read the whole APK into RAM")
                self.assertLessEqual(size, 1024 * 1024)
                reads.append(size)
                return super().read(size)

        path = self.root / "streamed-apk"
        path.touch(mode=0o600)
        with mock.patch.object(sys, "stdin", SimpleNamespace(buffer=BoundedReader(payload))), \
                mock.patch.object(sys, "argv", ["staging", str(path)]):
            exec(compile(source, "<staging-writer>", "exec"), {})
        self.assertEqual(path.read_bytes(), payload)
        self.assertGreater(len(reads), 2)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with mock.patch.object(sys, "stdin", SimpleNamespace(buffer=BoundedReader(b"overwrite"))), \
                mock.patch.object(sys, "argv", ["staging", str(path)]):
            with self.assertRaises(ValueError):
                exec(compile(source, "<staging-writer>", "exec"), {})
        self.assertEqual(path.read_bytes(), payload)

    def test_staging_writer_refuses_missing_and_symlink_destinations(self):
        self.transaction.staged_adb("alpha", b"capture-source", ["install", "@staged"])
        command = next(command for command in self.aux_commands if "-i" in command)
        source = command[command.index("-c") + 1]
        missing = self.root / "missing-stage"
        outside = self.root / "unrelated-file"
        outside.write_bytes(b"preserve")
        linked = self.root / "linked-stage"
        linked.symlink_to(outside)
        for path in (missing, linked):
            with self.subTest(path=path):
                with mock.patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"overwrite"))), \
                        mock.patch.object(sys, "argv", ["staging", str(path)]):
                    with self.assertRaises(OSError):
                        exec(compile(source, "<staging-writer>", "exec"), {})
        self.assertFalse(missing.exists())
        self.assertTrue(linked.is_symlink())
        self.assertEqual(outside.read_bytes(), b"preserve")

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
        self.assertEqual(self.docker.ps_calls, [True])

    def test_old_ps_falls_back_only_for_unsupported_or_empty_all_processes(self):
        for status, output in (
            (1, "ps: invalid option -- 'A'\nusage: ps"),
            (1, 'ps: Unknown option "A"'),
            (1, "ps: bad pid '-A'"),
            (0, ""),
            (0, "USER PID PPID VSIZE RSS WCHAN PC NAME"),
        ):
            with self.subTest(output=output):
                self.docker.ps_all = status, output
                self.docker.ps_calls.clear()
                self.assertEqual(
                    self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {}),
                    b'{"value":42}',
                )
                self.assertEqual(self.docker.ps_calls, [True, False])
                self.assertEqual(self.docker.forwards, {})

    def test_genuine_ps_failures_do_not_fall_back_or_forward(self):
        for status, output in (
            (1, "ps: Permission denied"),
            (1, "ps: invalid argument for a process"),
            (1, "USER PID PPID NAME\nshell 222 1 play.ott.foss"),
            (0, "ps: cannot read /proc"),
        ):
            with self.subTest(output=output):
                self.docker.ps_all = status, output
                self.docker.ps_calls.clear()
                with self.assertRaises(transaction.TransactionError):
                    self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
                self.assertEqual(self.docker.ps_calls, [True])
                self.assertEqual(self.docker.forwards, {})
                self.assertEqual(self.aux_commands, [])

    def test_ps_transport_failure_is_not_hidden_by_a_fallback(self):
        self.docker.ps_failure = manager_tests.mod.RecoveryError("transport unavailable")
        with self.assertRaisesRegex(manager_tests.mod.RecoveryError, "transport unavailable"):
            self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
        self.assertEqual(self.docker.ps_calls, [])
        self.assertEqual(self.aux_commands, [])

    def test_nonempty_modern_listing_without_application_does_not_fall_back(self):
        self.docker.ps_all = 0, "USER PID PPID NAME\nshell 111 1 adbd"
        with self.assertRaisesRegex(transaction.TransactionError, "not unique"):
            self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
        self.assertEqual(self.docker.ps_calls, [True])
        self.assertEqual(self.aux_commands, [])

    def test_failed_legacy_fallback_is_not_accepted(self):
        self.docker.ps_all = 1, "ps: invalid option -- 'A'"
        self.docker.ps_legacy = 1, "ps: Permission denied"
        with self.assertRaisesRegex(transaction.TransactionError, "Process discovery failed"):
            self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
        self.assertEqual(self.docker.ps_calls, [True, False])
        self.assertEqual(self.aux_commands, [])

    def test_ambiguous_application_process_refused_for_modern_and_legacy_ps(self):
        listing = "USER PID PPID NAME\nshell 222 1 play.ott.foss\nshell 333 1 play.ott.foss"
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.docker.ps_all = (1, "ps: bad option '-A'") if legacy else (0, listing)
                self.docker.ps_legacy = 0, listing
                with self.assertRaisesRegex(transaction.TransactionError, "not unique"):
                    self.transaction.cdp("alpha", "play.ott.foss", CDP_SOURCE, {})
                self.assertEqual(self.docker.forwards, {})
                self.assertEqual(self.aux_commands, [])

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
