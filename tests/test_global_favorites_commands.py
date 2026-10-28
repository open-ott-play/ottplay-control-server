"""Global host grammar/storage tests: real CLI helpers, no live controller."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "cli"), *sys.path]):
    import global_favorites_commands as commands
spec = importlib.util.spec_from_file_location("global_host_real_ott", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
PRIVATE = "fixture-secret-token"
OPERATION = "10000000-0000-4000-8000-000000000001"
RECOVERY = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.fixtures.json").read_bytes())["positive"]


def row(identifier="1", title="Favorite"):
    return {"title": title, "origin": {"provider": "m3u", "profile": "TV", "subprofile": None, "sourceAlias": "Home"},
            "locator": {"kind": "channel", "channelId": identifier}}


def document(*rows):
    return {"version": 1, "entries": list(rows)}


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Client:
    server = "https://fixture-controller.invalid"

    def __init__(self, clock):
        self.clock, self.calls, self.responses = clock, [], {}
        self.capability = {"version": 1}
        self.recovery_capability = {"version": 1}
        self.recovery_page = {**copy.deepcopy(RECOVERY["empty_page_response"]), "runtime": "fixture-runtime"}
        self.inspection = {**copy.deepcopy(RECOVERY["inspect_response"]), "runtime": "fixture-runtime"}
        ack, plan = commands.recovery.recovery_digests(self.inspection["runtime"], self.inspection["operation_id"],
            self.inspection["record"]["operation_runtime"], self.inspection["record"]["original_entry_sha256"],
            self.inspection["ledger_revision"], self.inspection["store_revision"], self.inspection["context_revision"],
            self.inspection["backend_proof"], self.inspection["after_ledger_sha256"])
        self.inspection.update(acknowledgement_id=ack, plan_sha256=plan)
        self.global_requests = []
        self.status_requests = []
        self.status_response = None
        self.status_outer = "ok"
        self.lose_ack = False
        self.transform = lambda value: value
        self.receipt_error = None

    def api(self, path, payload=None, timeout=None):
        if payload and payload.get("action") == "favorites_global_recovery":
            return self.recovery_api(path, payload, timeout)
        if payload and payload.get("action") == "favorites_global":
            return self.status_api(path, payload, timeout)
        if payload is None or payload != {"action": "capabilities", "params": {}}:
            raise AssertionError("Only capability and recovery requests belong in this client fixture")
        device = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["device_id"][0]
        self.calls.append((device, "POST", timeout, self.clock()))
        identifier = f"{len(self.responses) + 1:032x}"
        data = {"version": 1, "player": {"version": "1.2.0", "platform": "browser", "runtime": "fixture-runtime"},
                "lifecycle": [], "input": [], "playback": []}
        if self.capability is not None:
            data["favorites_global"] = copy.deepcopy(self.capability)
        if self.recovery_capability is not None:
            data["favorites_global_recovery"] = copy.deepcopy(self.recovery_capability)
        self.responses[identifier] = self.transform({"id": identifier, "status": "ok", "data": data})
        return 202, {"id": identifier, "status": "queued"}

    def status_api(self, path, payload, timeout):
        params = payload["params"]
        if params["op"] != "status":
            raise AssertionError("Status-only path attempted a different action")
        self.status_requests.append(copy.deepcopy(params))
        device = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["device_id"][0]
        self.calls.append((device, "POST", timeout, self.clock()))
        identifier = f"{len(self.responses) + 1:032x}"
        data = self.status_response if self.status_response is not None else {
            "version": 1, "runtime": params["runtime"], "op": "status", "subject": "global",
            "operation_id": params["operation_id"], "operation_runtime": None, "transaction_id": None,
            "state": "not_found", "outcome": "unknown", "receipt": None}
        self.responses[identifier] = {"id": identifier, "status": self.status_outer, "data": copy.deepcopy(data)}
        return 202, {"id": identifier, "status": "queued"}

    def recovery_api(self, path, payload, timeout):
        params = commands.recovery.validate_request(payload["params"])
        self.global_requests.append(copy.deepcopy(params))
        device = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["device_id"][0]
        self.calls.append((device, "POST", timeout, self.clock()))
        identifier = f"{len(self.responses) + 1:032x}"
        if params["op"] == "inspect":
            data = self.recovery_page if "cursor" in params else self.inspection
        else:
            record = {**self.inspection["record"], "acknowledgement": commands.recovery.recovery_audit(params)}
            data = {**copy.deepcopy(RECOVERY["acknowledge_response"]), "runtime": params["runtime"],
                    "acknowledgement_id": params["acknowledgement_id"], "plan_sha256": params["plan_sha256"], "record": record}
            self.inspection = {**self.inspection, "record": record, "ledger_revision": params["after_ledger_sha256"],
                               "acknowledge_allowed": False, "acknowledgement_id": None, "after_ledger_sha256": None, "plan_sha256": None}
            if self.lose_ack:
                data = {"invalid": True}
        self.responses[identifier] = {"id": identifier, "status": "ok", "data": copy.deepcopy(data)}
        return 202, {"id": identifier, "status": "queued"}

    def api_raw(self, path, payload=None, timeout=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        self.calls.append((query["device_id"][0], "GET", timeout, self.clock()))
        if self.receipt_error is not None:
            raise self.receipt_error
        return 200, json.dumps(self.responses[query["id"][0]]).encode()


class Environment:
    def __init__(self, clock):
        self.clock, self.calls, self.instances = clock, [], []
        self.documents = {"one": document(row("1")), "two": document(row("2"))}
        self.before_export = lambda peer: None
        self.failure = None
        self.preview_ready = True
        self.before_apply = lambda peer: None
        self.apply_outcome = "unknown"
        self.recovery_record = None

    def factory(self, client, device, **options):
        peer = Peer(self, client, device, options)
        self.instances.append(peer)
        return peer


class Peer:
    def __init__(self, environment, client, device, options):
        self.environment, self.client, self.device, self.options = environment, client, device, options

    def _call(self, operation):
        self.environment.calls.append((self.device, operation, self.environment.clock()))
        if self.environment.clock() >= self.options["deadline"]:
            raise commands.remote.RemoteError("deadline")

    def describe(self):
        self._call("describe")
        return SimpleNamespace(data={"subject": "global", "write_ready": True, "locator_kinds": ["channel"]})

    def export(self):
        self._call("export")
        self.environment.before_export(self)
        if self.environment.failure is not None and self.device == "two":
            raise self.environment.failure
        value = copy.deepcopy(self.environment.documents[self.device])
        digest = hashlib.sha256(commands.document.canonical_bytes(value)).hexdigest()
        return SimpleNamespace(document=value, description=SimpleNamespace(data={"export": {"document_sha256": digest}}))

    def preview(self, snapshot, incoming, mode):
        self._call("preview")
        plan = commands.document.plan_documents(snapshot.document, [incoming], mode)
        return commands.remote.Preview(OPERATION, self.environment.preview_ready, "aborted",
                                       {"counts": plan["summary"], "resolution_counts": {"resolved": len(incoming["entries"])}})

    def apply(self, snapshot, incoming, mode):
        self._call("apply")
        self.environment.before_apply(self)
        if (self.environment.recovery_record is not None
                and self.options["save_context"](self.environment.recovery_record) is not True):
            raise AssertionError("Save context must return True")
        verified = self.environment.apply_outcome == "verified"
        return commands.remote.Outcome(OPERATION, self.environment.apply_outcome, verified, "fixture_result")

    def reconcile(self, record):
        self._call("status")
        return commands.remote.Outcome(record["operation_id"], "unknown", False, "not_ready")


class GlobalFavoritesCommandsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ott-global-host-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.path = self.directory / "cli.json"
        self.config = {"presets": {"Home": {}, "Empty": {}}, "private": PRIVATE,
                       "preset_favorites": {"version": 1, "presets": {}},
                       "preset_global_favorites": {"version": 1, "presets": {"Home": document(row("1"))}}}
        self.write_config(self.config)
        self.clock = Clock()
        self.client = Client(self.clock)
        self.environment = Environment(self.clock)
        self.patch = mock.patch.object(commands.remote, "RemoteGlobalFavorites", side_effect=self.environment.factory)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_config(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def run_command(self, words, targets=None, timeout=45):
        params = commands.parse(ott, words)
        return commands.run(ott, self.client, self.config, self.path, targets or [("first", "one")], params,
                            timeout, monotonic=self.clock, sleep=self.clock.sleep)

    def fail(self, words, targets=None):
        with self.assertRaises(ott.Error) as caught:
            self.run_command(words, targets)
        self.assertNotIn(PRIVATE, str(caught.exception))
        return str(caught.exception)

    def test_exact_grammar_and_explicit_modes(self):
        self.assertEqual(commands.parse(ott, []), {"op": "describe"})
        self.assertEqual(commands.parse(ott, ["STATUS", OPERATION]), {"op": "status", "operation_id": OPERATION})
        self.assertEqual(commands.parse(ott, ["clear", "--dry-run"]), {"op": "clear", "mode": "replace", "dry_run": True})
        for operation in ("load", "collect", "import"):
            key = "path" if operation == "import" else "preset"
            self.assertEqual(commands.parse(ott, [operation, "Home"]),
                             {"op": operation, key: "Home", "mode": "merge", "dry_run": False})
            self.assertEqual(commands.parse(ott, [operation, "Home", "replace", "--dry-run"]),
                             {"op": operation, key: "Home", "mode": "replace", "dry_run": True})
        for words in (["unknown"], ["status", OPERATION, "extra"], ["status", OPERATION.replace("4000", "1000")],
                      ["describe", "extra"], ["clear", "replace"], ["clear", "--dry-run", "--dry-run"],
                      ["load"], ["diff", "Home", "--dry-run"], ["load", "Home", "merge", "replace"],
                      ["load", "Home", "--unknown"], ["load", "Home", "--dry-run", "replace"],
                      ["export", "-file"], ["import", "dir/"], ["export", "bad\nname"]):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                commands.parse(ott, words)

    def test_missing_preset_and_companion_stop_before_network(self):
        for words in (["load", "Missing"], ["load", "Empty"], ["diff", "Empty"], ["collect", "Missing"]):
            self.fail(words)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.environment.calls, [])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_missing_or_invalid_global_capability_never_constructs_peer(self):
        for marker in (None, {"version": 2}, {"version": True}, {"version": 1, "extra": PRIVATE}):
            self.client.capability = marker
            self.fail(["describe"])
        self.assertEqual(self.environment.instances, [])
        self.assertEqual(self.environment.calls, [])
        self.assertEqual([call[1] for call in self.client.calls], ["POST", "GET"] * 4)

    def test_collection_deduplicates_targets_and_makes_one_cas_after_all_reads(self):
        before = copy.deepcopy(self.config)
        events = []
        original_apply = commands.storage.Store.apply
        def apply(store, plan):
            events.append(list(self.environment.calls))
            return original_apply(store, plan)
        with mock.patch.object(commands.storage.Store, "apply", apply):
            result = self.run_command(["collect", "Home", "merge"],
                                      [("first", "one"), ("alias", "one"), ("second", "two")])
        self.assertEqual(result["status"], "collected")
        self.assertEqual(result["players"], 2)
        self.assertEqual(len(events), 1)
        self.assertEqual([call[:2] for call in events[0]], [("one", "export"), ("two", "export")])
        current = json.loads(self.path.read_bytes())
        expected = copy.deepcopy(before)
        expected["preset_global_favorites"]["presets"]["Home"] = document(row("1"), row("2"))
        self.assertEqual(current, expected)
        self.assertEqual(result["summary"]["added"], 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_collection_failure_and_dry_run_leave_no_partial_profile_or_backup(self):
        before = self.path.read_bytes()
        self.environment.failure = commands.remote.RemoteError("not_ready")
        self.fail(["collect", "Home"], [("first", "one"), ("second", "two")])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])
        self.environment.failure = None
        result = self.run_command(["collect", "Home", "replace", "--dry-run"], [("first", "one"), ("second", "two")])
        self.assertEqual(result["status"], "planned")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_collection_cas_rejects_concurrent_config_edit(self):
        changed = {**self.config, "concurrent": "preserve"}
        def concurrent(peer):
            if peer.device == "two":
                self.write_config(changed)
        self.environment.before_export = concurrent
        self.fail(["collect", "Home"], [("first", "one"), ("second", "two")])
        self.assertEqual(json.loads(self.path.read_bytes()), changed)

    def test_collection_conflicting_titles_is_blocked_without_write(self):
        before = self.path.read_bytes()
        self.environment.documents["two"] = document(row("1", "Other title"))
        result = self.run_command(["collect", "Home"], [("first", "one"), ("second", "two")])
        self.assertEqual((result["status"], result["summary"]["title_conflicts"]), ("blocked", 1))
        self.assertEqual(self.path.read_bytes(), before)

    def test_import_file_is_bounded_regular_strict_json_before_network(self):
        candidates = []
        large = self.directory / "large.json"
        large.write_bytes(b" " * (1024 * 1024 + 1))
        candidates.append(large)
        duplicate = self.directory / "duplicate.json"
        duplicate.write_bytes(b'{"version":1,"version":1,"entries":[]}')
        candidates.append(duplicate)
        target = self.directory / "valid.json"
        target.write_text(json.dumps(document()))
        linked = self.directory / "linked.json"
        linked.symlink_to(target)
        candidates.extend((linked, self.directory))
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "pipe.json"
            os.mkfifo(fifo)
            candidates.append(fifo)
        for path in candidates:
            self.fail(["import", str(path)])
        self.assertEqual(self.client.calls, [])

    def test_export_is_private_and_existing_destination_never_overwritten(self):
        destination = self.directory / "export.json"
        result = self.run_command(["export", str(destination)])
        self.assertEqual(result["status"], "exported")
        self.assertEqual(json.loads(destination.read_bytes()), self.environment.documents["one"])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        before, calls = destination.read_bytes(), len(self.client.calls)
        self.fail(["export", str(destination)])
        self.assertEqual(destination.read_bytes(), before)
        self.assertEqual(len(self.client.calls), calls)

    def test_export_racing_destination_is_preserved(self):
        destination = self.directory / "racing.json"
        self.environment.before_export = lambda peer: destination.write_text("preserve concurrent file")
        self.fail(["export", str(destination)])
        self.assertEqual(destination.read_text(), "preserve concurrent file")
        self.assertFalse(list(self.directory.glob(".ott-*")))

    def test_diff_and_previews_never_commit_or_create_recovery_directory(self):
        before = self.path.read_bytes()
        self.assertEqual(self.run_command(["diff", "Home"])["status"], "matched")
        self.environment.documents["one"] = document(row("2"))
        self.assertEqual(self.run_command(["diff", "Home"])["status"], "different")
        for words in (["load", "Home", "--dry-run"], ["clear", "--dry-run"]):
            self.assertEqual(self.run_command(words)["status"], "planned")
        self.environment.preview_ready = False
        self.assertEqual(self.run_command(["clear", "--dry-run"])["status"], "blocked")
        self.assertFalse(any(call[1] == "apply" for call in self.environment.calls))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_stable_per_target_deadline_is_passed_after_capability_time(self):
        result = self.run_command(["describe"], timeout=10)
        self.assertEqual(result["status"], "available")
        options = self.environment.instances[0].options
        self.assertEqual(options["deadline"], 110)
        self.assertEqual(options["timeout"], 110 - self.clock())
        self.assertLess(options["timeout"], 10)
        self.assertTrue(all(0 < call[2] <= 10 for call in self.client.calls))

    def test_missing_status_context_has_no_network_or_recovery_directory(self):
        self.fail(["status", OPERATION])
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.environment.calls, [])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def _saved_status_fixture(self):
        spec = importlib.util.spec_from_file_location("global_status_host_fixture", ROOT / "tests/test_global_favorites_protocol.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        case = fixture.GlobalFavoritesProtocolTests()
        case.setUp()
        record = {"version": 1, "operation_id": OPERATION,
                  "context": fixture.protocol.export_context(case.context())}
        durable = commands._Durable(self.path, self.client, "one")
        durable.save(record)
        self.client.capability = None
        self.client.recovery_capability = None
        self.client.status_response = copy.deepcopy(case.responses["status"])
        return record, durable

    def test_status_without_mutation_markers_reads_saved_context_with_fresh_runtime(self):
        record, durable = self._saved_status_fixture()
        config = self.path.read_bytes()
        # A restarted player may return the original immutable receipt while
        # the outer response must match the fresh runtime read from metadata.
        self.client.transform = lambda value: {**value, "data": {**value["data"], "player": {
            **value["data"]["player"], "runtime": "fresh-runtime"}}}
        self.client.status_response["runtime"] = "fresh-runtime"
        result = self.run_command(["status", OPERATION])
        self.assertEqual((result["status"], result["verified"], result["operation_id"]), ("verified", True, OPERATION))
        self.assertEqual(self.client.status_requests, [{"op": "status", "runtime": "fresh-runtime", "operation_id": OPERATION}])
        self.assertEqual([call[1] for call in self.client.calls], ["POST", "GET", "POST", "GET"])
        self.assertEqual(self.client.global_requests, [])
        self.assertEqual(self.environment.instances, [])
        self.assertEqual(durable.store.load(OPERATION), record)
        self.assertEqual(self.path.read_bytes(), config)

    def test_status_without_mutation_markers_rejects_foreign_and_unsupported_receipts(self):
        _, durable = self._saved_status_fixture()
        original = copy.deepcopy(self.client.status_response)
        variants = [({}, "ok"), ({**original, "runtime": "stale-runtime"}, "ok"),
                    ({**original, "operation_id": "30000000-0000-4000-8000-000000000003"}, "ok"),
                    ({**original, "receipt": {**original["receipt"], "plan_digest": "f" * 64}}, "ok"),
                    ({"version": 1, "runtime": "fixture-runtime", "subject": "global", "op": "status", "error": "unsupported"}, "unsupported")]
        before = {p.name: p.read_bytes() for p in durable.store.directory.iterdir()}
        for response, status in variants:
            self.client.status_response, self.client.status_outer = response, status
            result = self.run_command(["status", OPERATION])
            self.assertEqual((result["status"], result["verified"], result["operation_id"]), ("unknown", False, OPERATION))
        self.assertEqual(len(self.client.status_requests), len(variants))
        self.assertEqual(self.client.global_requests, [])
        self.assertEqual(self.environment.instances, [])
        self.assertEqual({p.name: p.read_bytes() for p in durable.store.directory.iterdir()}, before)

    def test_status_requires_valid_fresh_metadata_even_with_saved_context(self):
        self._saved_status_fixture()
        self.client.transform = lambda value: {**value, "data": {**value["data"], "player": {
            **value["data"]["player"], "runtime": "INVALID PRIVATE RUNTIME"}}}
        self.fail(["status", OPERATION])
        self.assertEqual(self.client.status_requests, [])
        self.assertEqual(self.environment.instances, [])

    def test_remote_and_capability_errors_are_redacted(self):
        self.client.receipt_error = ott.TransportError(PRIVATE, "dns")
        message = self.fail(["describe"])
        self.assertIn("transport: dns", message)
        self.assertIn("was not sent", message)
        self.assertEqual(self.environment.calls, [])
        self.client.receipt_error = None
        self.environment.failure = commands.remote.RemoteError("fixture-secret")
        self.fail(["collect", "Home"], [("second", "two")])
        self.environment.failure = ValueError(PRIVATE)
        self.fail(["collect", "Home"], [("second", "two")])

    def test_unexpected_peer_failure_has_fixed_text_without_traceback_secret(self):
        self.environment.failure = RuntimeError(PRIVATE)
        self.fail(["collect", "Home"], [("second", "two")])

    def test_durable_global_directory_is_separate_and_status_stays_read_only(self):
        # Reuse only the pure context fixture, with no test execution or I/O.
        fixture_spec = importlib.util.spec_from_file_location("global_host_protocol_fixture", ROOT / "tests/test_global_favorites_protocol.py")
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        case = fixture.GlobalFavoritesProtocolTests()
        case.setUp()
        record = {"version": 1, "operation_id": OPERATION,
                  "context": fixture.protocol.export_context(case.context())}
        self.environment.recovery_record = record
        before = self.path.read_bytes()
        old = commands.shared._DurableContext(self.path, self.client, "one")
        old_dir = self.directory / old.name
        old_dir.mkdir(mode=0o700)
        old_file = old_dir / "preserved-scoped-proof"
        old_file.write_bytes(b"existing scoped receipt")
        old_file.chmod(0o600)
        result = self.run_command(["clear"])
        self.assertEqual((result["status"], result["verified"]), ("unknown", False))
        global_dirs = list(self.directory.glob(".favorites-global-operations-*"))
        self.assertEqual(len(global_dirs), 1)
        self.assertNotEqual(global_dirs[0], old_dir)
        self.assertEqual(global_dirs[0].stat().st_mode & 0o777, 0o700)
        store = commands.remote.ContextStore(global_dirs[0], server=self.client.server, device="one")
        self.assertEqual(store.load(OPERATION), record)
        for path in global_dirs[0].iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        saved = {path.name: path.read_bytes() for path in global_dirs[0].iterdir()}
        self.environment.calls.clear()
        result = self.run_command(["status", OPERATION])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.environment.calls, [])
        self.assertEqual([request["op"] for request in self.client.status_requests], ["status"])
        self.assertEqual({path.name: path.read_bytes() for path in global_dirs[0].iterdir()}, saved)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(old_file.read_bytes(), b"existing scoped receipt")
        calls = len(self.client.calls)
        self.fail(["status", OPERATION], [("other-target", "two")])
        self.assertEqual(len(self.client.calls), calls)

    def test_verified_result_at_deadline_skips_local_completion_without_losing_proof(self):
        self.environment.apply_outcome = "verified"
        self.environment.before_apply = lambda peer: setattr(self.clock, "value", peer.options["deadline"])
        with mock.patch.object(commands.remote.ContextStore, "mark_completed") as completed:
            result = self.run_command(["clear"])
        completed.assert_not_called()
        self.assertEqual((result["status"], result["verified"]), ("verified", True))
        self.assertIn("local_retention_unconfirmed", result["warnings"])
        self.assertEqual([call[1] for call in self.environment.calls], ["export", "apply"])

    def test_group_mutations_and_invalid_targets_fail_before_network(self):
        self.fail(["clear"], [("first", "one"), ("second", "two")])
        self.fail(["describe"], [("bad", "has space")])
        self.assertEqual(self.client.calls, [])

    def test_plain_status_and_summary_are_readable(self):
        result = self.run_command(["collect", "Home", "--dry-run"])
        out = io.StringIO()
        with redirect_stdout(out):
            commands.print_result(result)
        text = out.getvalue()
        self.assertIn("Global Favorites: planned.", text)
        self.assertIn("retained 1", text)
        self.assertNotIn("{'", text)
        self.assertNotIn(PRIVATE, text)
        out = io.StringIO()
        with redirect_stdout(out):
            commands.print_result({"status": "unknown", "reason": "invalid_response", "operation_id": OPERATION})
        self.assertIn("No automatic replay was attempted.", out.getvalue())

    def test_plain_output_keeps_resolution_cleanup_and_retention_warnings_visible(self):
        out = io.StringIO()
        with redirect_stdout(out):
            commands.print_result({"status": "planned", "resolution": {"resolved": 1, "missing": 2},
                                   "cleanup": "unconfirmed"})
            commands.print_result({"status": "verified", "warnings": ["local_retention_unconfirmed"]})
        self.assertIn("missing 2", out.getvalue())
        self.assertIn("unconfirmed", out.getvalue())
        self.assertIn("local retention unconfirmed", out.getvalue())

    def test_mutation_requires_independent_complete_recovery_but_readonly_does_not(self):
        self.client.recovery_capability = None
        message = self.fail(["clear"])
        self.assertIn("recovery", message)
        self.assertEqual([call[1] for call in self.environment.calls], ["export"])
        self.assertEqual(self.client.global_requests, [])
        self.run_command(["clear", "--dry-run"])
        self.run_command(["diff", "Home"])
        self.run_command(["collect", "Home", "--dry-run"])
        self.assertEqual(self.client.global_requests, [])
        self.client.recovery_capability = {"version": 1}
        self.client.recovery_page = {**copy.deepcopy(RECOVERY["page_response"]), "runtime": "fixture-runtime"}
        self.environment.calls.clear()
        self.fail(["load", "Home"])
        self.assertEqual([call[1] for call in self.environment.calls], ["export"])
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect"])
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_recovery_only_marker_lists_and_previews_without_files_or_core_peer(self):
        self.client.capability = None
        before = self.path.read_bytes()
        result = self.run_command(["recover"])
        self.assertEqual((result["status"], result["recovery"]["total"], result["replay_allowed"]), ("recovery_list", 0, False))
        operation = self.client.inspection["operation_id"]
        result = self.run_command(["ack", operation, "--dry-run"])
        self.assertEqual((result["status"], result["recovery"]["plan_sha256"]), ("planned", self.client.inspection["plan_sha256"]))
        self.assertEqual(self.environment.instances, [])
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect", "inspect"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_acknowledgement_saves_private_intent_once_and_retains_original_unknown(self):
        self.client.capability = None
        self.client.lose_ack = True
        operation, confirmation = self.client.inspection["operation_id"], self.client.inspection["plan_sha256"]
        result = self.run_command(["acknowledge", operation, "--confirm", confirmation])
        self.assertEqual((result["status"], result["original_outcome"], result["replay_allowed"]),
                         ("acknowledgement_observed", "unknown", False))
        self.assertNotIn("verified", result)
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect", "acknowledge", "inspect"])
        folders = list(self.directory.glob(".favorites-global-operations-*"))
        self.assertEqual(len(folders), 1)
        files = list(folders[0].glob("*.json"))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in files))
        retained = {path.name: path.read_bytes() for path in files}
        self.client.global_requests.clear()
        result = self.run_command(["ack", operation, "--confirm", confirmation])
        self.assertEqual(result["status"], "acknowledgement_observed")
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect"])
        self.assertEqual({path.name: path.read_bytes() for path in files}, retained)
        self.assertEqual(self.environment.instances, [])

    def test_changed_confirmation_and_failed_intent_never_send_ack(self):
        operation = self.client.inspection["operation_id"]
        result = self.run_command(["ack", operation, "--confirm", "0" * 64])
        self.assertEqual((result["status"], result["reason"]), ("blocked", "confirmation_changed"))
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect"])
        self.client.global_requests.clear()
        with mock.patch.object(commands.audit.AcknowledgementStore, "save_intent", side_effect=OSError(PRIVATE)):
            result = self.run_command(["ack", operation, "--confirm", self.client.inspection["plan_sha256"]])
        self.assertEqual((result["status"], result["reason"]), ("blocked", "context_not_saved"))
        self.assertEqual([request["op"] for request in self.client.global_requests], ["inspect"])


if __name__ == "__main__":
    unittest.main()
