"""Unit tests for the attach-only helper; all CLI processes are inert doubles."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_cli_case_tested", ROOT / "scripts/native_cli_case.py")
case = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = case
SPEC.loader.exec_module(case)
# Synthetic marker for leak assertions; never a credential or environment input.
REDACTION_CANARY = "private-token-and-https://private.example/path"


class CaseFixtures:
    @staticmethod
    def receipt(action="capabilities", status="ok", identity="a"):
        return case.encoded({"action": action, "request_id": identity * 32, "status": status})

    @staticmethod
    def fixture(relative):
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))


@unittest.skipUnless(case.host_supported() and hasattr(case.os, "mkfifo"),
                     "Requires POSIX file ownership, FIFO and owned process-group APIs")
class PosixCliFilesystemTests(CaseFixtures, unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "repo"
        (self.source / "cli").mkdir(parents=True)
        self.write(self.source / "cli/ott.py", b"# must never be executed\n")
        self.write(self.source / "cli/module.py", b"# second pinned member\n")
        self.credentials = self.write(self.root / "credentials.json", case.encoded({"operator_token": REDACTION_CANARY}))
        self.config = self.write(self.root / "config.json", case.encoded({"server": "https://127.0.0.1:12345",
            "server_config": str(self.credentials), "players": {"fixture-v2": "fixture-device"}}))
        self.ca = self.write(self.root / "ca.pem", b"opaque fixture certificate")
        self.owner = self.write(self.root / "owner.txt", b"existing owner receipt; not our schema")
        self.handoff = {"version": 1, "cli_root": str(self.source), "cli_commit": "1" * 40,
            "cli_files": {"cli/" + path.name: self.pin(path)["sha256"] for path in (self.source / "cli").iterdir()},
            "config": self.pin(self.config), "credentials": self.pin(self.credentials), "ca": self.pin(self.ca),
            "owner_receipt": self.pin(self.owner), "origin": "https://127.0.0.1:12345", "alias": "fixture-v2",
            "device_id": "fixture-device", "deadline_monotonic": 100}

    @staticmethod
    def write(path, data):
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    @staticmethod
    def pin(path):
        return {"path": str(path), "sha256": case.hashlib.sha256(path.read_bytes()).hexdigest()}

    def prepare(self, name="caps", params=None, mutation=False):
        return case.prepare(self.handoff, name, params or {}, mutation, clock=lambda: 10)

    def run_inert(self, prepared, stdout, stderr, code=0, reason=None, cleanup=True):
        output = self.root / "case"
        with mock.patch.object(case, "capture", return_value=(code, stdout, stderr, reason, cleanup, True)) as capture:
            report, response = case.run_case(prepared, execute=True, output=str(output), clock=lambda: 10)
        self.assertEqual(capture.call_count, 1)
        return report, response, output, capture.call_args

    def test_default_only_validates_and_never_creates_output_or_child(self):
        output = self.root / "not-created"
        with mock.patch.object(case.subprocess, "Popen") as spawn:
            report, response = case.run_case(self.prepare(), output=str(output), clock=lambda: 10)
        spawn.assert_not_called()
        self.assertFalse(output.exists())
        self.assertEqual(report["result"], "not_executed")
        self.assertIsNone(response)

    def test_source_manifest_must_cover_every_python_member(self):
        del self.handoff["cli_files"]["cli/module.py"]
        with self.assertRaisesRegex(case.CaseError, "incomplete_cli_closure"):
            self.prepare()

    def test_fifo_is_rejected_without_a_blocking_open(self):
        fifo = self.root / "candidate.fifo"
        case.os.mkfifo(fifo, 0o600)
        original_open = case.os.open

        def guarded_open(path, flags, *args, **kwargs):
            # Fail before opening if the guard regresses; this test cannot hang
            # waiting for a writer even when run against the broken code.
            self.assertTrue(flags & case.os.O_NONBLOCK)
            self.assertTrue(flags & case.os.O_NOFOLLOW)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(case.os, "open", side_effect=guarded_open) as opened:
            with self.assertRaisesRegex(case.CaseError, "invalid_file"):
                case.regular(str(fifo), case.MAX_JSON, True)
        opened.assert_called_once()

    def test_changed_source_and_owner_receipt_fail_before_execution(self):
        for path in (self.source / "cli/ott.py", self.owner):
            original = path.read_bytes()
            self.write(path, original + b"changed")
            with self.assertRaises(case.CaseError):
                self.prepare()
            self.write(path, original)

    def test_private_config_and_exact_single_target_are_required(self):
        self.config.chmod(0o644)
        with self.assertRaisesRegex(case.CaseError, "file_not_private"):
            self.prepare()
        self.config.chmod(0o600)
        data = json.loads(self.config.read_text())
        data["players"]["other"] = "other-device"
        self.write(self.config, case.encoded(data))
        self.handoff["config"] = self.pin(self.config)
        with self.assertRaisesRegex(case.CaseError, "config_target_mismatch"):
            self.prepare()

    def test_nonloopback_credentials_path_and_noncanonical_origin_refused(self):
        for origin in ("https://example.com:12345", "https://x@127.0.0.1:12345",
                       "https://127.0.0.1:12345/path", "http://127.0.0.1:12345"):
            with self.subTest(origin=origin):
                self.handoff["origin"] = origin
                with self.assertRaisesRegex(case.CaseError, "invalid_origin"):
                    self.prepare()

    def test_expired_or_unbounded_deadline_is_not_admitted(self):
        for value in (10, 14, 611, float("inf"), True):
            with self.subTest(value=value):
                self.handoff["deadline_monotonic"] = value
                with self.assertRaisesRegex(case.CaseError, "invalid_deadline"):
                    self.prepare()

    def test_success_returns_data_privately_and_only_allowlisted_summary_publicly(self):
        data = {"version": 1, "player": {"runtime": "page-1", "private": REDACTION_CANARY}, "private": REDACTION_CANARY,
                "local_media": {"version": 1}}
        report, response, output, call = self.run_inert(self.prepare(), case.encoded(data), self.receipt() + REDACTION_CANARY.encode())
        self.assertEqual(report["result"], "response_received")
        self.assertEqual(response, data)
        self.assertNotIn(REDACTION_CANARY, json.dumps(report))
        self.assertNotIn(REDACTION_CANARY, json.dumps(call.args[0]))
        self.assertEqual(json.loads((output / "response.private.json").read_text()), data)
        self.assertEqual((output / "stdout.private").stat().st_mode & 0o777, 0o600)
        self.assertFalse((output / "credentials.json").exists())
        self.assertTrue(report["staging_cleanup_ok"])
        binding = json.loads((output / "binding.private.json").read_text())
        self.assertEqual(binding["owner_receipt_sha256"], self.handoff["owner_receipt"]["sha256"])
        self.assertNotIn(binding["owner_receipt_sha256"], json.dumps(report))
        self.assertEqual(report["native_qualification"], "not_evaluated")
        self.assertNotIn("PASS", json.dumps(report))

    def test_prespawn_write_failure_removes_only_new_private_staging(self):
        prepared = self.prepare()
        for failed_name in ("ca.pem", "cli.json"):
            with self.subTest(failed_name=failed_name):
                output = self.root / ("failed-" + failed_name)
                failure = OSError("injected staging failure")
                original_write = case.write_private

                def fail_after_credentials(path, data):
                    if path == output / failed_name:
                        self.assertTrue((output / "credentials.json").is_file())
                        raise failure
                    return original_write(path, data)

                with mock.patch.object(case, "write_private", side_effect=fail_after_credentials), \
                        mock.patch.object(case, "capture") as capture:
                    with self.assertRaises(OSError) as caught:
                        case.run_case(prepared, execute=True, output=str(output), clock=lambda: 10)
                self.assertIs(caught.exception, failure)
                capture.assert_not_called()
                self.assertFalse(output.exists())
                self.assertTrue(self.credentials.is_file())

    def test_prespawn_deadline_removes_private_staging_without_launch(self):
        output = self.root / "expired"
        moments = iter((10, 10, 56))  # Admission, end=55, then expiry before spawn.
        with mock.patch.object(case, "capture") as capture:
            with self.assertRaisesRegex(case.CaseError, "deadline_before_spawn"):
                case.run_case(self.prepare(), execute=True, output=str(output), clock=lambda: next(moments))
        capture.assert_not_called()
        self.assertFalse(output.exists())
        self.assertTrue(self.credentials.is_file())

    def test_prespawn_cleanup_failure_preserves_original_error(self):
        output = self.root / "cleanup-failed"
        prepared = self.prepare()
        failure = OSError("original staging failure")
        original_write = case.write_private

        def fail_after_credentials(path, data):
            if path == output / "ca.pem":
                self.assertTrue((output / "credentials.json").is_file())
                raise failure
            return original_write(path, data)

        with mock.patch.object(case, "write_private", side_effect=fail_after_credentials), \
                mock.patch.object(case.shutil, "rmtree", side_effect=OSError("cleanup failure")) as cleanup, \
                mock.patch.object(case, "capture") as capture:
            with self.assertRaises(OSError) as caught:
                case.run_case(prepared, execute=True, output=str(output), clock=lambda: 10)
        self.assertIs(caught.exception, failure)
        cleanup.assert_called_once_with(output)
        capture.assert_not_called()
        self.assertTrue((output / "credentials.json").is_file())

    def test_staging_uses_verified_bytes_not_later_source_changes(self):
        prepared = self.prepare()
        self.write(self.source / "cli/ott.py", REDACTION_CANARY.encode())
        self.handoff["origin"] = "https://private.example"
        output = self.root / "case"
        def capture(*args):
            self.assertEqual((output / "source/cli/ott.py").read_bytes(), b"# must never be executed\n")
            self.assertEqual(json.loads((output / "cli.json").read_text())["server"], "https://127.0.0.1:12345")
            return 0, b'{"version":1,"player":{"runtime":"p"}}', self.receipt(), None, True, True
        with mock.patch.object(case, "capture", side_effect=capture):
            report, _ = case.run_case(prepared, execute=True, output=str(output), clock=lambda: 10)
        self.assertEqual(report["result"], "response_received")
        self.assertFalse((output / "source").exists())

    def test_exit_zero_without_matching_receipt_is_unknown(self):
        report, response, _, _ = self.run_inert(self.prepare(), b'{"version":1,"player":{"runtime":"p"}}', b"")
        self.assertEqual(report["result"], "unknown")
        self.assertIsNone(response)

    def test_boolean_exit_is_not_success(self):
        report, _, _, _ = self.run_inert(self.prepare(), b'{"version":1,"player":{"runtime":"p"}}', self.receipt(), code=False)
        self.assertEqual(report["result"], "unknown")

    def test_capability_receipt_alone_does_not_confirm_operation(self):
        report, _, _, _ = self.run_inert(self.prepare("plex_status"), b"{}", self.receipt())
        self.assertEqual(report["reason"], "missing_operation_receipt")

    def test_timeout_is_one_invocation_unknown_and_no_response(self):
        report, response, _, _ = self.run_inert(
            self.prepare("local_handle_play", {"handle": "h1"}, True), b"", b"", code=-15, reason="deadline")
        self.assertEqual((report["result"], report["automatic_retries"], report["cli_invocations"]), ("unknown", 0, 1))
        self.assertIsNone(response)

    def test_cleanup_failure_overrides_successful_receipt(self):
        report, _, output, _ = self.run_inert(self.prepare(), b'{"version":1,"player":{"runtime":"p"}}', self.receipt(), cleanup=False)
        self.assertEqual((report["result"], report["reason"]), ("unknown", "cleanup_failed"))
        self.assertTrue((output / "credentials.json").exists())
        self.assertFalse(report["staging_cleanup_ok"])

    def test_settings_get_then_set_is_not_mislabelled_as_duplicate_mutation(self):
        data = {"version": 1, "runtime": "p", "op": "set", "persisted": True,
                "setting": {"id": "interfaceMode", "persisted_value": 1},
                "application": {"state": "deferred"}}
        stderr = self.receipt() + self.receipt("player_settings", identity="b") + self.receipt("player_settings", identity="c")
        report, _, _, _ = self.run_inert(self.prepare("settings_write", {"setting": "interfaceMode", "value": 1}, True),
                                        case.encoded(data), stderr)
        self.assertEqual(report["result"], "response_received")
        self.assertEqual(report["summary"], {"persisted": True, "application_state": "deferred"})

    def test_global_favorite_requires_real_operation_receipt_not_only_caps(self):
        data = {"collection": "global", "op": "add", "status": "verified"}
        report, _, output, _ = self.run_inert(self.prepare("favorites_add", {}, True), case.encoded(data), self.receipt())
        self.assertEqual((report["result"], report["reason"]), ("unknown", "missing_operation_receipt"))
        self.assertTrue((output / "cli.json").exists())  # An uncertain commit may need its private journal.

    def test_global_favorite_receipts_and_projection_do_not_publish_entry(self):
        fixture = self.fixture("docs/design/favorites-global-items-v1.fixtures.json")
        # The CLI joins its frozen list entry with the separate play receipt.
        data = dict(fixture["play_result"], collection="global", status="dispatched",
                    entry=fixture["list_result"]["entries"][0]["entry"], playback_confirmed=False)
        stderr = self.receipt() + self.receipt("favorites_global_items", identity="b") + self.receipt("favorites_global_items", identity="c")
        report, _, _, _ = self.run_inert(self.prepare("favorites_play", {"id": data["id"]}, True), case.encoded(data), stderr)
        self.assertEqual(report["result"], "response_received")
        self.assertEqual(report["summary"], {"dispatched": True, "playback_confirmed": False})
        self.assertNotIn(data["entry"]["title"], json.dumps(report))

    def test_rejected_receipt_is_distinct_from_unknown(self):
        report, _, _, _ = self.run_inert(self.prepare("plex_servers_list"), b"", self.receipt("plex_servers", "rejected"), code=1)
        self.assertEqual(report["result"], "rejected")

    def test_later_favorite_refusal_cannot_settle_earlier_unknown_commit(self):
        stderr = (self.receipt() + self.receipt("favorites_global", "unknown", "b") +
                  self.receipt("favorites_global", "rejected", "c"))
        report, response, output, _ = self.run_inert(self.prepare("favorites_add", {}, True), b"", stderr, code=1)
        self.assertEqual((report["result"], report["reason"]), ("unknown", "unconfirmed_operation"))
        self.assertEqual([row["status"] for row in report["receipts"]], ["ok", "unknown", "rejected"])
        self.assertIsNone(response)
        self.assertTrue((output / "cli.json").exists())

    def test_later_favorite_refusal_does_not_supply_missing_earlier_identity(self):
        missing = case.encoded({"action": "favorites_global", "status": "ok", "request_id": None})
        stderr = self.receipt() + missing + self.receipt("favorites_global", "unsupported", "c")
        report, response, _, _ = self.run_inert(self.prepare("favorites_add", {}, True), b"", stderr, code=1)
        self.assertEqual((report["result"], report["reason"]), ("unknown", "unconfirmed_operation"))
        self.assertIsNone(response)

    def test_terminal_refusal_needs_identity_and_normal_error_exit(self):
        for number, (identity, code) in enumerate(((None, 1), ("a" * 32, 0), ("a" * 32, -9), ("a" * 32, True))):
            stderr = case.encoded({"action": "plex_servers", "status": "rejected", "request_id": identity})
            with self.subTest(identity=identity, code=code), mock.patch.object(
                    case, "capture", return_value=(code, b"", stderr, None, True, True)):
                report, _ = case.run_case(self.prepare("plex_servers_list"), execute=True,
                                          output=str(self.root / ("refusal-" + str(number))), clock=lambda: 10)
            self.assertEqual((report["result"], report["reason"]), ("unknown", "unconfirmed_operation"))

    def test_existing_output_cannot_replay_or_overwrite_a_case(self):
        output = self.root / "exists"
        output.mkdir()
        preserved = output / "existing.private"
        preserved.write_bytes(b"existing case evidence")
        with mock.patch.object(case, "capture") as capture, mock.patch.object(case.shutil, "rmtree") as cleanup:
            with self.assertRaises(FileExistsError):
                case.run_case(self.prepare(), execute=True, output=str(output), clock=lambda: 10)
        capture.assert_not_called()
        cleanup.assert_not_called()
        self.assertEqual(preserved.read_bytes(), b"existing case evidence")

    def test_capture_spawn_error_is_reported_without_any_retry(self):
        with mock.patch.object(case.subprocess, "Popen", side_effect=OSError(REDACTION_CANARY)) as spawn:
            code, stdout, stderr, reason, cleanup, spawned = case.capture(
                ["inert"], {}, str(self.root), 20, 25, 1024, clock=lambda: 10)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual((reason, cleanup, spawned), ("process_failure", True, False))
        self.assertEqual((stdout, stderr), (b"", b""))

    def test_capture_output_limit_stops_owned_group_and_bounds_returned_bytes(self):
        process = mock.Mock(pid=12345, returncode=0)
        process.poll.return_value = None
        process.stdout.fileno.return_value = 101
        process.stderr.fileno.return_value = 102
        selector = mock.Mock()
        selector.get_map.return_value = {101: True}
        selector.select.return_value = [(SimpleNamespace(fd=101, data=0, fileobj=process.stdout), 1)]
        with mock.patch.object(case.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(case.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(case.os, "set_blocking"), \
                mock.patch.object(case.os, "read", return_value=b"x" * 1025) as read, \
                mock.patch.object(case.os, "killpg") as kill:
            _, stdout, _, reason, cleanup, launched = case.capture(
                ["inert"], {}, str(self.root), 20, 25, 1024, clock=lambda: 10)
        self.assertEqual((reason, cleanup, launched, len(stdout)), ("output_limit", True, True, 1024))
        self.assertEqual(spawn.call_count, 1)
        read.assert_called_once_with(101, 1025)
        self.assertEqual(kill.call_args_list, [mock.call(12345, case.signal.SIGTERM), mock.call(12345, case.signal.SIGKILL)])
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_capture_shared_deadline_does_not_start_a_second_process(self):
        process = mock.Mock(pid=12345, returncode=0)
        process.poll.return_value = None
        process.stdout.fileno.return_value = 101
        process.stderr.fileno.return_value = 102
        selector = mock.Mock()
        selector.get_map.return_value = {101: True}
        moments = iter((10, 21, 22, 22))
        with mock.patch.object(case.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(case.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(case.os, "set_blocking"), mock.patch.object(case.os, "killpg"):
            _, _, _, reason, cleanup, launched = case.capture(
                ["inert"], {}, str(self.root), 20, 25, 1024, clock=lambda: next(moments))
        self.assertEqual((reason, cleanup, launched), ("deadline", True, True))
        self.assertEqual(spawn.call_count, 1)
        selector.select.assert_not_called()



class PortableCliValidationTests(CaseFixtures, unittest.TestCase):
    """Pure contracts stay exercised on Windows without claiming Native support."""

    @staticmethod
    def prepare(name="caps", params=None, mutation=False):
        params = {} if params is None else params
        words, action, changes = case.command(name, params, mutation)
        return case.Prepared({}, name, params, words, action, changes, {}, {}, b"")

    def test_unsupported_host_fails_before_filesystem_or_process_operations(self):
        operations = (
            lambda: case.regular("unused", 10),
            lambda: case.prepare({}, "caps", {}),
            lambda: case.write_private("unused", b""),
            lambda: case.capture([], {}, "unused", 10, 15, 1024),
            lambda: case.run_case(self.prepare(), execute=True, output="unused"),
        )
        with mock.patch.object(case, "host_supported", return_value=False), \
                mock.patch.object(case.os, "open") as opened, \
                mock.patch.object(case.subprocess, "Popen") as spawned:
            for operation in operations:
                with self.assertRaisesRegex(case.CaseError, "unsupported_host"):
                    operation()
        opened.assert_not_called()
        spawned.assert_not_called()

    def test_unsupported_host_main_returns_explicit_safe_error(self):
        with mock.patch.object(case, "host_supported", return_value=False), mock.patch("builtins.print") as printed:
            code = case.main(["--handoff", "unused-private-handoff", "--case", "caps", "--execute"])
        report = json.loads(printed.call_args.args[0])
        self.assertEqual((code, report["result"], report["reason"]), (1, "not_executed", "unsupported_host"))
        self.assertNotIn("unused-private-handoff", json.dumps(report))

    def test_mutations_require_opt_in_and_closed_tokens(self):
        with self.assertRaisesRegex(case.CaseError, "mutation_not_enabled"):
            self.prepare("local_handle_play", {"handle": "opaque-1"})
        with self.assertRaisesRegex(case.CaseError, "invalid_case_parameters"):
            self.prepare("local_handle_play", {"handle": "opaque-1", "path": REDACTION_CANARY}, True)
        with self.assertRaisesRegex(case.CaseError, "setting_not_allowlisted"):
            self.prepare("setting_read", {"setting": "plexToken"})

    def test_reviewed_command_spellings_do_not_add_fallback_or_refresh(self):
        examples = (
            ("plex_servers_list", {}, ["plex", "servers"]),
            ("plex_servers_select", {"server_id": "PMS-1", "context": "c1"},
             ["plex", "servers", "select", "PMS-1", "--context", "c1"]),
            ("local_browse", {"entry_id": "e1", "generation": "g1"},
             ["local", "browse", "e1", "--generation", "g1"]),
            ("local_select", {"select_id": "s1", "generation": "g1"},
             ["local", "select", "s1", "--generation", "g1"]),
            ("local_handle_shuffle", {"handle": "h1"}, ["local", "shuffle", "--handle", "h1"]),
        )
        for name, params, expected in examples:
            with self.subTest(case=name):
                self.assertEqual(case.command(name, params, True)[0], expected)

    def test_additional_cases_use_existing_cli_syntax(self):
        examples = (
            ("settings_write", {"setting": "interfaceMode", "value": 1}, ["setting", "interfaceMode", "1"]),
            ("profile_select", {"number": 2}, ["profile", "2"]),
            ("plex_library_preview", {"library_id": "12"}, ["plex", "library", "--preview", "id:12"]),
            ("plex_play", {"ids": ["1", "2", "1"], "shuffle": True}, ["plex", "play", "--shuffle", "1", "2", "1"]),
            ("plex_previous", {}, ["plex", "prev"]),
            ("plex_repeat", {"mode": "all"}, ["plex", "repeat", "all"]),
            ("settings_write", {"setting": "uiFontScale", "value": 100}, ["setting", "uiFontScale", "100"]),
            ("relative_seek", {"seconds": -10}, ["seek", "-10s"]),
            ("channels_page", {"limit": 20, "offset": 0}, ["s", "--limit", "20", "--offset", "0"]),
            ("tracks_subtitle", {"id": "off"}, ["tracks", "subtitle", "id:off"]),
            ("favorites_remove", {"id": "fav-fixture"}, ["fav", "remove", "id:fav-fixture"]),
            ("local_connect_handle", {"handle": "h1"}, ["local", "connect", "--handle", "h1"]),
        )
        for name, params, expected in examples:
            with self.subTest(case=name):
                self.assertEqual(case.command(name, params, True)[0], expected)

    def test_numeric_and_identity_boundaries_reject_before_any_process(self):
        for name, params in (
            ("settings_write", {"setting": "interfaceMode", "value": True}),
            ("settings_write", {"setting": "bufSize", "value": 3601}),
            ("profile_select", {"number": 16}),
            ("plex_play", {"ids": ["1"] * 501}),
            ("plex_preview", {"ids": ["01"]}),
            ("relative_seek", {"seconds": 0}),
            ("plex_repeat", {"mode": "forever"}),
            ("channels_page", {"limit": 201, "offset": 0}),
            ("favorites_remove", {"id": "a bookmark title"}),
        ):
            with self.subTest(case=name):
                with self.assertRaises(case.CaseError):
                    case.command(name, params, True)
        self.assertEqual(len(case.command("plex_play", {"ids": ["1"] * 500}, True)[0]), 502)

    def test_json_duplicate_nonfinite_and_invalid_utf8_are_rejected(self):
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff'):
            with self.assertRaises(case.CaseError):
                case.decode(value)

    def test_duplicate_or_malformed_receipt_cannot_confirm(self):
        for stderr in (self.receipt() * 2, b'{"action":"capabilities","value":"secret"}\n'):
            with self.subTest(stderr=stderr):
                with self.assertRaises(case.CaseError):
                    case.receipts(stderr, "capabilities")

    def test_unknown_action_receipt_is_never_promoted_to_favorites_evidence(self):
        with self.assertRaisesRegex(case.CaseError, "invalid_receipt"):
            case.receipts(self.receipt("unknown"), "favorites", case.FAVORITE_ACTIONS)

    def test_setting_value_is_returned_only_for_nonsensitive_descriptor(self):
        prepared = self.prepare("setting_read", {"setting": "interfaceMode"})
        data = {"version": 1, "runtime": "p", "op": "get", "descriptor": {"id": "interfaceMode", "sensitive": True},
                "setting": {"id": "interfaceMode", "value": 1}}
        with self.assertRaisesRegex(case.CaseError, "invalid_response"):
            case.project(prepared, data)
        data["descriptor"]["sensitive"] = False
        self.assertEqual(case.project(prepared, data), {"value": 1})

    def test_local_select_requires_nonactivating_nonpersistent_result(self):
        prepared = self.prepare("local_select", {"select_id": "s", "generation": "g"}, True)
        data = {"version": 1, "runtime": "p", "op": "select", "generation": "g",
                "source": {"handle": "h"}, "persisted": False, "activated": True}
        with self.assertRaises(case.CaseError):
            case.project(prepared, data)

    def test_public_failure_does_not_echo_path_or_exception_text(self):
        with mock.patch.object(case, "regular", side_effect=OSError(REDACTION_CANARY)), mock.patch("builtins.print") as printed:
            result = case.main(["--handoff", "/private/path", "--case", "caps"])
        self.assertEqual(result, 1)
        self.assertNotIn(REDACTION_CANARY, printed.call_args.args[0])
        self.assertNotIn("/private/path", printed.call_args.args[0])

    def test_contract_queue_idle_status_and_stop_allow_null_index(self):
        fixtures = self.fixture("contracts/plex-queue-v1.json")
        idle = self.fixture("contracts/plex-queue-state-v2.json")["idle_response"]
        for name, data in (("plex_status", idle), ("plex_stop", fixtures["stop_result"]),
                           ("plex_status", fixtures["queue_result"]), ("plex_status", fixtures["ended_result"])):
            with self.subTest(case=name, state=data["state"]):
                projected = case.project(self.prepare(name, mutation=True), data)
                self.assertEqual(projected["index"], data["index"])
                self.assertEqual(projected["active"], data["active"])

    def test_contract_queue_index_and_active_corruption_are_rejected(self):
        idle = self.fixture("contracts/plex-queue-state-v2.json")["idle_response"]
        for changed in ({"index": 0}, {"active": True}, {"state": "playing"}):
            with self.subTest(changed=changed), self.assertRaises(case.CaseError):
                case.project(self.prepare("plex_status"), dict(idle, **changed))
        active = self.fixture("contracts/plex-queue-v1.json")["queue_result"]
        for changed in ({"index": None}, {"index": True}, {"index": len(active["ids"])}, {"active": False}):
            with self.subTest(changed=changed), self.assertRaises(case.CaseError):
                case.project(self.prepare("plex_status"), dict(active, **changed))

    def test_contract_settings_read_and_deferred_write_preserve_distinction(self):
        fixture = self.fixture("contracts/player-settings-v1.json")
        read = case.project(self.prepare("setting_read", {"setting": "bufSize"}), fixture["responses"]["get"])
        self.assertEqual(read["persisted_value"], 0)
        write = case.project(self.prepare("settings_write", {"setting": "bufSize", "value": 3}, True),
                             fixture["responses"]["set"])
        self.assertEqual(write, {"persisted": True, "application_state": "deferred"})

    def test_contract_plex_library_and_queue_previews(self):
        library = self.fixture("contracts/plex-library-v1.json")
        preview = case.project(self.prepare("plex_library_preview", {"library_id": "16"}), library["preview_result"])
        self.assertEqual(preview, {"item_count": 3})
        self.assertEqual(case.project(self.prepare("plex_libraries"), library["list_result"]), {"item_count": 1})
        queue = self.fixture("contracts/plex-queue-v1.json")
        self.assertEqual(case.project(self.prepare("plex_preview", {"ids": queue["preview_result"]["ids"]}),
                                      queue["preview_result"]), {"item_count": 3})

    def test_shared_local_browser_and_handle_fixtures(self):
        fixture = self.fixture("testdata/local_browser_v1.json")
        names = {"roots": "local_roots", "list": "local_browse", "select": "local_select"}
        for row in fixture["browser_cases"]:
            request = row["request"]
            if row["status"] != "ok" or request["op"] not in names or "cursor" in request:
                continue
            name = names[request["op"]]
            params = {key: request[key] for key in case.CASES[name][2]}
            with self.subTest(case=row["name"]):
                result = case.project(self.prepare(name, params, True), row["data"])
                self.assertNotIn("source", result)
        for row in fixture["handle_cases"]:
            request = row["request"]
            name = "local_connect_handle" if request["op"] == "connect" else "local_handle_" + request["op"]
            with self.subTest(case=row["name"]):
                result = case.project(self.prepare(name, {"handle": request["source_handle"]}, True), row["data"])
                self.assertEqual(result["item_count"], row["data"]["count"])
                self.assertFalse(result["playback_confirmed"])

    def test_shared_managed_server_reply_fixtures(self):
        for row in self.fixture("testdata/plex_servers_v1.json")["reply_cases"]:
            if row["id"] not in ("list-one", "list-empty", "select-applied"):
                continue
            request = row["request_params"]
            name = "plex_servers_" + request["op"]
            params = {key: request[key] for key in case.CASES[name][2]}
            data = json.loads(row["response_json"])["data"]
            with self.subTest(case=row["id"]):
                result = case.project(self.prepare(name, params, True), data)
                self.assertNotIn("context", result)

    def test_contract_categories_channels_and_tracks(self):
        categories = self.fixture("contracts/categories-v1.json")
        self.assertEqual(case.project(self.prepare("categories"), categories["list_result"]), {"item_count": 2})
        self.assertEqual(case.project(self.prepare("channels_page", {"limit": 2, "offset": 0}),
                                      categories["channels_result"]), {"item_count": 2})
        for row in self.fixture("contracts/media-tracks-v1.json")["valid"]:
            if row["status"] != "ok":
                continue
            request = row["params"]
            if request["op"] == "list":
                name, params = "tracks_list", {}
            else:
                target = request["target"]
                name = "tracks_" + target["kind"] + ("_off" if target["mode"] == "off" else "")
                params = {} if target["mode"] == "off" else {"id": target["id"]}
            with self.subTest(case=row["name"]):
                result = case.project(self.prepare(name, params, True), row["data"])
                self.assertNotIn("tracks", result)

    def test_contract_legacy_favorite_successes_do_not_publish_media(self):
        fixture = self.fixture("contracts/favorites-v1.json")
        for operation in ("list", "add", "play", "remove"):
            name = "favorites_" + operation
            params = {}
            if operation in ("play", "remove"):
                params = {"id": fixture[operation + "_request"]["params"]["id"]}
            with self.subTest(operation=operation):
                result = case.project(self.prepare(name, params, True), fixture[operation + "_result"])
                self.assertNotIn("entry", result)
                self.assertNotIn("entries", result)

    def test_queue_repeat_requires_matching_mode_without_claiming_playback(self):
        data = self.fixture("contracts/plex-queue-v1.json")["queue_result"]
        prepared = self.prepare("plex_repeat", {"mode": "all"}, True)
        with self.assertRaises(case.CaseError):
            case.project(prepared, data)
        result = case.project(prepared, dict(data, repeat="all"))
        self.assertEqual(result["repeat"], "all")
        self.assertNotIn("playback_confirmed", result)
        idle = self.fixture("contracts/plex-queue-state-v2.json")["idle_response"]
        with self.assertRaises(case.CaseError):
            case.project(prepared, dict(idle, repeat="all"))

    def test_global_current_projects_frozen_entry_without_private_details(self):
        fixture = self.fixture("docs/design/favorites-global-items-v1.fixtures.json")
        data = dict(fixture["current_result"], collection="global", status="ready")
        result = case.project(self.prepare("favorites_global_current"), data)
        self.assertEqual(result, {"current_available": True})


if __name__ == "__main__":
    unittest.main()
