"""Main favorites routing with synthetic targets and no external I/O."""
import contextlib
import copy
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_favorites_dispatch", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
PACKET = json.loads((ROOT / "contracts/favorites-current-plex-protocol-v1.json").read_bytes())
SCOPE = PACKET["bind_request"]["scope"]
OPERATION = "95e21aca-76b0-4db1-b3ce-bd9a4bd64770"
SECRET = "fixture-private-payload-token"


def summary():
    counts = {"before": 1, "after": 2, "added": 1, "removed": 0, "retained": 1}
    return {**counts, "skipped_duplicates": 0, "memberships": counts,
            "conflicts": {"resolved": 0, "unresolved": 0}}


def caps(marker=True):
    value = {"version": 1, "player": {"version": "test", "platform": "tauri", "runtime": "test-runtime"},
             "lifecycle": [], "input": [], "playback": []}
    if marker:
        value.update(copy.deepcopy(PACKET["capability"]))
    return value


class FavoritesDispatchTests(unittest.TestCase):
    def client(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {"players": {"t1": "one", "same": "one", "t2": "two"}}
        client.credentials = {"devices": [{"id": "one"}, {"id": "two"}]}
        client.server, client.timeout, client.on_receipt = "https://fixture.invalid", 45, None
        client.call = mock.Mock(side_effect=AssertionError("internal action must not be sent directly"))
        return client

    def run_cli(self, words, host, client=None):
        client = client or self.client()
        out, err = io.StringIO(), io.StringIO()
        def module(name):
            self.assertEqual(name, "favorites_commands")
            return host
        with mock.patch.object(ott, "read_json", return_value=client.config), mock.patch.object(ott, "Client", return_value=client) as constructor, \
                mock.patch.object(ott, "favorites_module", side_effect=module), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(words)
        return code, out.getvalue(), err.getvalue(), client, constructor

    def test_single_host_uses_authoritative_client_config_and_never_internal_rpc(self):
        host = SimpleNamespace(run=mock.Mock(return_value={"op": "load", "status": "verified", "verified": True,
            "scope": SCOPE, "summary": summary(), "operation_id": OPERATION, "warnings": []}))
        code, out, err, client, constructor = self.run_cli(["-c", "/fixture/cli.json", "-j", "t1", "fav", "load", "Home", "replace"], host)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "verified")
        constructor.assert_called_once_with(client.config, 45)
        args = host.run.call_args.args
        self.assertIs(args[1], client)
        self.assertIs(args[2], client.config)
        self.assertEqual(args[3:], ("/fixture/cli.json", [("t1", "one")], {"op": "load", "preset": "Home", "mode": "replace", "dry_run": False}, 45))
        client.call.assert_not_called()

    def test_group_collect_resolves_all_deduplicates_and_calls_host_once(self):
        host = SimpleNamespace(run=mock.Mock(return_value={"op": "collect", "status": "collected", "players": 2, "summary": summary()}))
        code, out, err, client, _ = self.run_cli(["-j", "t1,same,t2", "fav", "collect", "Home", "replace"], host)
        self.assertEqual((code, err, json.loads(out)["players"]), (0, "", 2))
        host.run.assert_called_once()
        self.assertEqual(host.run.call_args.args[4], [("t1", "one"), ("t2", "two")])
        client.call.assert_not_called()
        host.run.reset_mock()
        code, out, _, _, _ = self.run_cli(["t1,missing", "fav", "collect", "Home"], host)
        self.assertEqual((code, out), (1, ""))
        host.run.assert_not_called()

    def test_other_group_commands_keep_per_member_failures_and_parameters_isolated(self):
        def run(_api, _client, _config, _path, targets, params, _timeout):
            self.assertEqual(params, {"op": "clear", "dry_run": False})
            params["private_mutation"] = True
            state = "unknown" if targets[0][1] == "one" else "verified"
            return {"op": "clear", "status": state, "verified": state == "verified", "scope": SCOPE,
                    "operation_id": OPERATION, "replay_allowed": False}
        host = SimpleNamespace(run=mock.Mock(side_effect=run))
        code, out, err, client, _ = self.run_cli(["-j", "t1,t2", "fav", "clear"], host)
        result = json.loads(out)
        self.assertEqual((code, err, result["succeeded"], result["failed"]), (1, "", 1, 1))
        self.assertEqual([row["ok"] for row in result["results"]], [False, True])
        self.assertEqual(host.run.call_count, 2)
        client.call.assert_not_called()

    def test_exit_codes_and_plain_output_do_not_include_portable_payloads(self):
        for state in ("failed", "blocked", "unknown", "partial", "not_applied", "planned", "unchanged", "verified"):
            host = SimpleNamespace(run=mock.Mock(return_value={"op": "clear", "status": state, "verified": state == "verified",
                "scope": SCOPE, "summary": summary(), "operation_id": OPERATION,
                "document": {"token": SECRET}, "preview": {"title": SECRET}, "warnings": ["local_retention_unconfirmed"]}))
            code, out, err, _, _ = self.run_cli(["t1", "fav", "clear"], host)
            self.assertEqual(code, 0 if state in ("planned", "unchanged", "verified") else 1)
            self.assertNotIn(SECRET, out + err)
            self.assertIn("Scope: plex:", out)
            self.assertIn("Items: 1 -> 2", out)
            self.assertIn("Operation: " + OPERATION, out)
        for state in ("matched", "different"):
            host = SimpleNamespace(run=mock.Mock(return_value={"op": "diff", "status": state, "scope": SCOPE, "summary": summary()}))
            self.assertEqual(self.run_cli(["t1", "fav", "diff", "Home"], host)[0], 0)

    def test_unqualified_status_observation_exits_nonzero_without_claiming_verified(self):
        host = SimpleNamespace(run=mock.Mock(return_value={"op": "status", "status": "unknown",
            "outcome": "unknown", "operation_id": OPERATION, "verified": False,
            "reason": "incomplete_evidence", "replay_allowed": False, "warnings": []}))
        code, out, err, client, _ = self.run_cli(["-j", "t1", "fav", "status", OPERATION], host)
        result = json.loads(out)
        self.assertEqual((code, err, result["verified"], result["reason"]), (1, "", False, "incomplete_evidence"))
        self.assertEqual(host.run.call_args.args[5], {"op": "status", "operation_id": OPERATION})
        client.call.assert_not_called()
        code, out, err, client, _ = self.run_cli(["t1", "fav", "status", OPERATION], host)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("Status was read, but current scope and storage evidence are insufficient", out)
        client.call.assert_not_called()

    def test_capability_is_optional_strict_and_controls_help_examples(self):
        controls = ott.capabilities_metadata(caps())
        self.assertEqual(controls["favorites_transfer"], PACKET["capability"]["favorites_transfer"])
        self.assertNotIn("favorites_transfer", ott.capabilities_metadata(caps(False)))
        for marker in (None, {}, {**PACKET["capability"]["favorites_transfer"], "private": SECRET}):
            invalid = caps(False)
            invalid["favorites_transfer"] = marker
            with self.assertRaises(ott.Error) as caught:
                ott.capabilities_metadata(invalid)
            self.assertNotIn(SECRET, str(caught.exception))
        for present in (False, True):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(caps(present))}, "t1")
            self.assertEqual("fav bindings" in out.getvalue(), present)
            self.assertEqual("CURRENT VERIFIED PLEX ONLY" in out.getvalue(), present)

    def test_recovery_capability_preserves_partial_installation_guidance(self):
        value = caps(False)
        value["favorites_transfer_recovery"] = {"version": 1}
        original = Path.is_file
        def missing_protocol(path):
            return False if path == ROOT / "cli/favorites_protocol.py" else original(path)
        with mock.patch.object(ott.Path, "is_file", missing_protocol), self.assertRaises(ott.Error) as caught:
            ott.capabilities_metadata(value)
        self.assertEqual(str(caught.exception), "Install all CLI Python files from the same revision to synchronize favorites")
        self.assertNotIn("invalid capabilities", str(caught.exception))

    def test_legacy_favorites_remain_available_without_transfer_marker(self):
        contract = json.loads((ROOT / "contracts/favorites-v1.json").read_bytes())
        client = self.client()
        capabilities = caps(False)
        capabilities["player"]["runtime"] = "page-123"
        capabilities["favorites"] = {"version": 1, "operations": ["list", "add", "play", "remove"], "max_items": 100}
        client.call.side_effect = [capabilities, contract["list_result"]]
        host = SimpleNamespace(run=mock.Mock())
        code, out, err, _, _ = self.run_cli(["-j", "t1", "fav", "list"], host, client)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), contract["list_result"])
        self.assertEqual(client.call.call_args_list, [mock.call("one", "capabilities", {}),
            mock.call("one", "favorites", {"op": "list", "runtime": "page-123"})])
        host.run.assert_not_called()

    def test_module_loader_shares_actual_sibling_types_and_rejects_foreign_revision(self):
        host = ott.favorites_module("favorites_commands")
        protocol = ott.favorites_module("favorites_protocol")
        self.assertIs(host.protocol, protocol)
        self.assertIs(host.remote.protocol, protocol)
        self.assertEqual(Path(protocol.__file__).resolve(), ROOT / "cli/favorites_protocol.py")
        with mock.patch.dict(sys.modules, {"favorites_protocol": SimpleNamespace(__file__="/fixture/foreign/favorites_protocol.py")}), \
                self.assertRaisesRegex(ott.Error, "same CLI revision"):
            ott.favorites_module("favorites_commands")

    def test_group_collect_main_real_host_and_store_use_one_cas(self):
        spec = importlib.util.spec_from_file_location("dispatch_host_fixture", ROOT / "tests/test_favorites_commands.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        harness = fixture.FavoritesCommandsTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        client = harness.client
        aliases = {"t1": "one", "same": "one", "t2": "two"}
        client.config = {**harness.config, "players": aliases}
        client.credentials = {"devices": [{"id": "one"}, {"id": "two"}]}
        client.device = lambda name: aliases[name]
        client.call = mock.Mock(side_effect=AssertionError("internal action sent directly"))
        host = ott.favorites_module("favorites_commands")
        original_run = host.run
        def timed(*args):
            return original_run(*args, monotonic=harness.clock, sleep=harness.clock.sleep,
                                uuid_factory=lambda: OPERATION)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ott, "Client", return_value=client), mock.patch.object(host, "run", side_effect=timed) as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ott.main(["-c", str(harness.path), "-j", "t1,same,t2", "fav", "collect", "Home", "replace"])
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads(out.getvalue())["players"], 2)
        run.assert_called_once()
        self.assertEqual(len(list(harness.directory.glob("cli.json.favorites-backup-*"))), 1)
        saved = json.loads(harness.path.read_bytes())
        self.assertEqual(len(saved["preset_favorites"]["presets"]["Home"]["scopes"]), 2)
        self.assertEqual(client.posts(), ["capabilities", "bindingcatalog", "snapshot"] * 2)
        client.call.assert_not_called()

    def test_replace_current_flag_is_explicit_binding_only(self):
        for name in ("Home", "current"):
            self.assertEqual(ott.parse_command(["fav", "bind", name, "--replace-current"]),
                ("favorites_profile", {"op": "bind", "preset": name, "replace_current": True, "dry_run": False}))
        for words in (["bind", "current", "--new", "--replace-current"],
                      ["bind", "Home", "--replace-current", "--replace-current"],
                      ["clear", "--replace-current"], ["load", "Home", "--replace-current"],
                      ["bindings", "--replace-current"], ["status", OPERATION, "--replace-current"]):
            with self.subTest(words=words), self.assertRaises(ott.Error):
                ott.parse_command(["fav", *words])
        self.assertIn("--replace-current", ott.HELP)
        host = SimpleNamespace(run=mock.Mock(return_value={"op": "bind", "status": "paired", "scope": SCOPE}))
        self.assertEqual(self.run_cli(["t1", "fav", "bind", "Home", "--replace-current"], host)[0], 0)
        self.assertEqual(host.run.call_args.args[5],
                         {"op": "bind", "preset": "Home", "replace_current": True, "dry_run": False})

    def test_unacknowledged_pair_is_not_rendered_as_confirmed_and_plan_may_omit_scope(self):
        for result, expected in (({"status": "planned", "scope": None}, 0),
                ({"status": "unknown", "observed_pair": {"scope": SCOPE, "target_id": SECRET,
                                                          "binding_revision": SECRET}}, 1)):
            host = SimpleNamespace(run=mock.Mock(return_value={"op": "bind", **result}))
            code, out, err, _, _ = self.run_cli(["t1", "fav", "bind", "current", "--new", "--dry-run"], host)
            self.assertEqual((code, err), (expected, ""))
            self.assertNotIn(SECRET, out)
            self.assertNotIn("Scope:", out)
            if expected:
                self.assertIn("does not acknowledge this provisioning request", out)
                self.assertNotIn("pairing confirmed", out)

    def test_actual_host_rejects_missing_or_invalid_capability_before_transfer(self):
        spec = importlib.util.spec_from_file_location("dispatch_unsupported_fixture", ROOT / "tests/test_favorites_commands.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        for marker in (None, {"private": SECRET}):
            harness = fixture.FavoritesCommandsTests()
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            client = harness.client
            client.device = lambda _name: "one"
            client.config = harness.config
            client.credentials = {"devices": [{"id": "one"}]}
            client.call = mock.Mock(side_effect=AssertionError("internal action sent directly"))
            def changed(data, marker=marker):
                if marker is None:
                    data.pop("favorites_transfer")
                else:
                    data["favorites_transfer"] = marker
                return data
            client.caps_change = changed
            host = ott.favorites_module("favorites_commands")
            original_run = host.run
            def timed(*args, original_run=original_run, harness=harness):
                return original_run(*args, monotonic=harness.clock, sleep=harness.clock.sleep,
                                    uuid_factory=lambda: OPERATION)
            original = harness.path.read_bytes()
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(ott, "Client", return_value=client), mock.patch.object(host, "run", side_effect=timed), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = ott.main(["-c", str(harness.path), "t1", "fav", "clear"])
            self.assertEqual((code, out.getvalue()), (1, ""))
            self.assertNotIn(SECRET, err.getvalue())
            self.assertEqual(client.posts(), ["capabilities"])
            self.assertEqual(harness.path.read_bytes(), original)
            self.assertEqual(list(harness.directory.iterdir()), [harness.path])
            client.call.assert_not_called()

    def test_bindings_plain_shows_copyable_target_and_pms_without_other_fields(self):
        catalog = copy.deepcopy(PACKET["bindingcatalog"])
        catalog["private"] = {"token": SECRET}
        catalog["targets"][0]["private_selector"] = {"url": SECRET}
        host = SimpleNamespace(run=mock.Mock(return_value={"op": "bindings", "status": "available", "catalog": catalog}))
        code, out, err, _, _ = self.run_cli(["t1", "fav", "bindings"], host)
        target = catalog["targets"][0]
        self.assertEqual((code, err), (0, ""))
        for expected in ("Target: " + target["target_id"], "Display: " + target["display"], "PMS: " + target["serverId"],
                         "Scope: plex:" + SCOPE["profileBindingId"] + ":" + SCOPE["sourceBindingId"]):
            self.assertIn(expected, out)
        self.assertNotIn(SECRET, out)
        for field, value in (("target_id", "bad\n" + SECRET), ("display", "bad\x1b" + SECRET),
                             ("display", "bad\u202e" + SECRET), ("serverId", "https://private.invalid/" + SECRET)):
            changed = copy.deepcopy(catalog)
            changed["targets"][0][field] = value
            host.run.return_value["catalog"] = changed
            code, out, err, _, _ = self.run_cli(["t1", "fav", "bindings"], host)
            self.assertEqual((code, out), (1, ""))
            self.assertNotIn(SECRET, err)

    def test_collect_plain_shows_each_exact_scope_and_validated_local_backup(self):
        other = {**SCOPE, "sourceBindingId": "33333333-3333-4333-8333-333333333333"}
        result = {"op": "collect", "status": "collected", "players": 2, "summary": summary(),
                  "sources": [{"scope": scope, "coverage": "current-source"} for scope in (SCOPE, other)],
                  "backup_path": "/fixture/private/cli.json.favorites-backup.json", "document": {"token": SECRET}}
        host = SimpleNamespace(run=mock.Mock(return_value=result))
        code, out, err, _, _ = self.run_cli(["t1,t2", "fav", "collect", "Home"], host)
        self.assertEqual((code, err), (0, ""))
        for scope in (SCOPE, other):
            self.assertIn("Collected scope (current-source): plex:" + scope["profileBindingId"] + ":" + scope["sourceBindingId"], out)
        self.assertIn("Backup: " + result["backup_path"], out)
        self.assertNotIn(SECRET, out)
        for value in ("bad\n" + SECRET, "x" * 4097):
            host.run.return_value = {**result, "backup_path": value}
            code, out, err, _, _ = self.run_cli(["t1", "fav", "collect", "Home"], host)
            self.assertEqual((code, out), (1, ""))
            self.assertNotIn(SECRET, err)
        host.run.return_value = {**result, "status": "planned", "backup_path": SECRET}
        self.assertNotIn(SECRET, self.run_cli(["t1", "fav", "collect", "Home", "--dry-run"], host)[1])
        for source in ({"scope": SCOPE, "coverage": SECRET}, {"scope": {**SCOPE, "sourceBindingId": SECRET}, "coverage": "current-source"}):
            host.run.return_value = {**result, "sources": [source]}
            code, out, err, _, _ = self.run_cli(["t1", "fav", "collect", "Home"], host)
            self.assertEqual((code, out), (1, ""))
            self.assertNotIn(SECRET, err)

    def test_unknown_reasons_use_fixed_messages_and_never_upstream_text(self):
        for reason, message in (("local_context_unavailable", "retained local operation context is unavailable"),
                                ("scope_not_bound", "not bound to the current verified Plex source"),
                                ("transport", "Communication failed"), (SECRET, None), ({"token": SECRET}, None)):
            host = SimpleNamespace(run=mock.Mock(return_value={"op": "status", "status": "unknown", "operation_id": OPERATION,
                                                               "reason": reason, "document": {"token": SECRET}}))
            code, out, err, _, _ = self.run_cli(["t1", "fav", "status", OPERATION], host)
            self.assertEqual((code, err), (1, ""))
            self.assertNotIn(SECRET, out)
            if message:
                self.assertIn("Reason:", out)
                self.assertIn(message, out)
            else:
                self.assertNotIn("Reason:", out)
