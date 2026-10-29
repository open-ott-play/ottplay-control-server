"""Real CLI routing and private offline storage; no live requests."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_global_cli", ROOT / "cli/ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)
commands = ott.favorites_module("global_favorites_commands")
OPERATION = "10000000-0000-4000-8000-000000000001"
MIXED_SEQUENCE = json.loads((ROOT / "contracts/global-favorites-mixed-sequence.fixtures.json").read_bytes())


def entry(identifier="1", title="First"):
    return {"title": title, "origin": {"provider": "m3u", "profile": "TV", "subprofile": None, "sourceAlias": "Home"},
            "locator": {"kind": "channel", "channelId": identifier}}


def document(*entries):
    return {"version": 1, "entries": list(entries)}


class GlobalFavoritesCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.path = self.directory / "cli.json"
        self.config = {"players": {"t1": "one", "same": "one", "t2": "two"},
                       "presets": {"Home": {}, "Empty": {}}, "private": "fixture-secret-token",
                       "preset_favorites": {"version": 1, "presets": {}},
                       "preset_global_favorites": {"version": 1, "presets": {"Home": document(entry())}}}
        self.save()
        self.client = ott.Client.__new__(ott.Client)
        self.client.config = self.config
        self.client.credentials = {"devices": [{"id": "one"}, {"id": "two"}]}
        self.client.server, self.client.timeout, self.client.on_receipt = "https://fixture.invalid", 45, None
        self.client.call = mock.Mock(side_effect=AssertionError("internal command escaped the host"))

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        self.path.chmod(0o600)

    def cli(self, words, *, host=None, offline=False):
        out, err = io.StringIO(), io.StringIO()
        constructor = mock.Mock(side_effect=AssertionError("offline command constructed a client")) if offline else mock.Mock(return_value=self.client)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ott, "Client", constructor))
            if host is not None:
                stack.enter_context(mock.patch.object(commands, "run", host))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = ott.main(["-c", str(self.path), *words])
        return code, out.getvalue(), err.getvalue()

    def offline(self, *words):
        return self.cli(["-j", "presets", "Home", "fav", "global", *words], offline=True)

    def test_alias_grammar_reserves_global_without_changing_legacy_favorites(self):
        for alias in ("fav", "favs", "favorite", "favorites", "favourite", "favourites"):
            self.assertEqual(ott.parse_command([alias, "GLOBAL"]), ("favorites_global", {"op": "describe"}))
            self.assertEqual(ott.parse_command([alias, "global", "load", "Home", "replace", "--dry-run"]),
                             ("favorites_global", {"op": "load", "preset": "Home", "mode": "replace", "dry_run": True}))
            self.assertEqual(ott.parse_command([alias, "play", "global"]), ("favorites", {"op": "play", "query": "global"}))
            self.assertEqual(ott.parse_command([alias, "load", "Home"])[0], "favorites_profile")
        for words in (["global", "typo"], ["global", "load", "Home", "--unknown"], ["global", "status", "invalid"]):
            with self.assertRaises(ott.Error):
                ott.parse_command(["fav", *words])
        with self.assertRaises(ott.Error):
            ott.parse_presets_command(["Home", "fav", "global", "global", "validate"])

    def test_single_and_group_collect_use_actual_client_and_one_all_target_host_call(self):
        host = mock.Mock(return_value={"op": "collect", "status": "collected", "players": 2})
        code, out, err = self.cli(["-j", "t1,same,t2", "fav", "global", "collect", "Home"], host=host)
        self.assertEqual((code, err, json.loads(out)["players"]), (0, "", 2))
        self.assertEqual(host.call_count, 1)
        args = host.call_args.args
        self.assertIs(args[1], self.client)
        self.assertEqual(args[2], self.config)
        self.assertEqual(args[3:5], (str(self.path), [("t1", "one"), ("t2", "two")]))
        self.assertEqual(args[5:], ({"op": "collect", "preset": "Home", "mode": "merge", "dry_run": False}, 45))
        self.client.call.assert_not_called()

    def test_group_mutations_keep_independent_results_and_no_extra_post(self):
        def run(*args):
            self.assertEqual(args[5], {"op": "clear", "mode": "replace", "dry_run": False})
            args[5]["private"] = True
            verified = args[4][0][1] == "two"
            return {"op": "clear", "status": "verified" if verified else "unknown", "verified": verified}
        host = mock.Mock(side_effect=run)
        code, out, err = self.cli(["-j", "t1,t2", "fav", "global", "clear"], host=host)
        result = json.loads(out)
        self.assertEqual((code, err, result["succeeded"], result["failed"], host.call_count), (1, "", 1, 1, 2))
        self.client.call.assert_not_called()

    def test_group_exports_get_distinct_names_and_all_destinations_preflight(self):
        path = self.directory / "favorites.json"
        host = mock.Mock(return_value={"op": "export", "status": "exported", "entries": 1})
        code, _, err = self.cli(["-j", "t1,t2", "fav", "global", "export", str(path)], host=host)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([call.args[5]["path"] for call in host.call_args_list],
                         [str(self.directory / "favorites.t1.json"), str(self.directory / "favorites.t2.json")])
        blocked = self.directory / "favorites.t2.json"
        blocked.write_bytes(b"preserve existing export")
        host.reset_mock()
        code, _, err = self.cli(["t1,t2", "fav", "global", "export", str(path)], host=host)
        self.assertEqual(code, 1)
        self.assertIn("No player command was sent", err)
        host.assert_not_called()
        self.assertEqual(blocked.read_bytes(), b"preserve existing export")

    def test_unknown_unverified_and_blocked_results_exit_nonzero(self):
        for state in ("unknown", "blocked", "partial", "not_applied", "failed", "verified", "invented"):
            host = mock.Mock(return_value={"op": "clear", "status": state, "verified": False})
            code, _, _ = self.cli(["-j", "t1", "fav", "global", "clear"], host=host)
            self.assertEqual(code, 1, state)

    def test_recovery_aliases_and_group_ack_guard_precede_any_host_request(self):
        for alias in ("recovery", "recover"):
            self.assertEqual(ott.parse_command(["fav", "global", alias])[1], {"op": "recovery", "mode": "list"})
            self.assertEqual(ott.parse_command(["fav", "global", alias, "inspect", OPERATION])[1],
                             {"op": "recovery", "mode": "inspect", "operation_id": OPERATION})
        for alias in ("ack", "acknowledge"):
            params = ott.parse_command(["fav", "global", alias, OPERATION, "--dry-run"])[1]
            self.assertEqual(params, {"op": "acknowledge", "operation_id": OPERATION, "dry_run": True})
            host = mock.Mock()
            code, _, err = self.cli(["t1,t2", "fav", "global", alias, OPERATION, "--confirm", "a" * 64], host=host)
            self.assertEqual(code, 1)
            self.assertIn("exactly one player", err)
            host.assert_not_called()
        for words in (["ack", OPERATION], ["ack", OPERATION, "--confirm", "bad"], ["recovery", "all"]):
            with self.assertRaises(ott.Error):
                ott.parse_command(["fav", "global", *words])

    def test_recovery_only_help_and_plain_preview_expose_actionable_plan(self):
        data = {"version": 1, "player": {"version": "test", "platform": "browser", "runtime": "fixture-runtime"},
                "lifecycle": [], "input": [], "playback": [], "favorites_global_recovery": {"version": 1}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ott.print_player_help({"capabilities": ott.capabilities_metadata(data)}, "t1")
        self.assertIn("fav global recovery", out.getvalue())
        self.assertNotIn("fav global load", out.getvalue())
        self.assertNotIn("No controls", out.getvalue())
        packet = json.loads((ROOT / "docs/design/global-favorites-recovery-v1.fixtures.json").read_bytes())["positive"]
        host = mock.Mock(return_value={"op": "acknowledge", "status": "planned", "recovery": packet["inspect_response"],
                                      "operation_id": packet["inspect_request"]["operation_id"], "replay_allowed": False})
        code, out, err = self.cli(["t1", "fav", "global", "ack", OPERATION, "--dry-run"], host=host)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(packet["inspect_response"]["plan_sha256"], out)
        self.assertIn("not acknowledged", out)

    def test_all_ordinary_load_modes_block_global_companion_before_connection(self):
        for mode in ([], ["plan"], ["apply"], ["resume"], ["verify"]):
            with mock.patch.object(ott, "validate_preset", return_value=("Home", {})):
                code, _, err = self.cli(["t1", "load", "Home", *mode], offline=True)
            self.assertEqual(code, 1, mode)
            self.assertIn("--settings-only", err, mode)
        with mock.patch.object(ott, "validate_preset", return_value=("Home", {})), \
                mock.patch.object(ott, "load_preset", return_value={"status": "loaded", "preset": "Home", "active_profile": 1}):
            code, out, err = self.cli(["t1", "load", "Home", "--settings-only"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("favorites skipped", out)
        self.assertEqual(json.loads(self.path.read_bytes()), self.config)

    def test_capability_is_independent_strict_and_gates_english_help(self):
        base = {"version": 1, "player": {"version": "test", "platform": "browser", "runtime": "fixture-runtime"},
                "lifecycle": [], "input": [], "playback": []}
        for present in (False, True):
            data = {**base, **({"favorites_global": {"version": 1}} if present else {})}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ott.print_player_help({"capabilities": ott.capabilities_metadata(data)}, "t1")
            self.assertEqual("fav global" in out.getvalue(), present)
            self.assertNotIn("fav bindings", out.getvalue())
        for marker in ({}, {"version": True}, {"version": 1, "secret": "fixture-secret-token"}):
            with self.assertRaises(ott.Error) as caught:
                ott.capabilities_metadata({**base, "favorites_global": marker})
            self.assertNotIn("fixture-secret-token", str(caught.exception))

    def test_same_revision_loaders_share_storage_class_identity(self):
        old = ott.favorites_profile_dependencies()
        global_store = ott.favorites_module("preset_global_favorites")
        self.assertIs(old["storage_errors"][0], global_store.StorageError)
        self.assertIs(commands.storage.StorageError, old["storage_errors"][0])
        self.assertIs(sys.modules["preset_favorites"].Store, old["store_factory"])
        with mock.patch.dict(sys.modules, {"global_favorites_document": type("Foreign", (), {"__file__": "/other/global_favorites_document.py"})()}), \
                self.assertRaisesRegex(ott.Error, "same CLI revision"):
            ott.favorites_module("global_favorites_commands")

    def test_global_companion_is_gated_but_settings_only_and_orphan_legacy_remain(self):
        with mock.patch.object(ott, "favorites_module", side_effect=AssertionError("gate loaded helpers")):
            with self.assertRaisesRegex(ott.Error, "--settings-only"):
                ott.require_preset_favorites_support(self.config, "home")
            ott.require_preset_favorites_support(self.config, "Home", settings_only=True)
            ott.require_preset_favorites_support(self.config, "Empty")
            ott.require_preset_favorites_support({"presets": {"Home": {}}, "preset_global_favorites": {
                "version": 99, "presets": {"Orphan": {"future": True}}}}, "Home")

    def test_offline_roundtrip_dry_run_conflicts_diff_and_restore_preserve_other_config(self):
        before = self.path.read_bytes()
        destination = self.directory / "export.json"
        self.assertEqual(self.offline("validate")[0], 0)
        self.assertEqual(self.offline("export", str(destination))[0], 0)
        self.assertEqual(json.loads(destination.read_bytes()), document(entry()))
        if os.name != "nt":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        code, _, err = self.offline("export", str(destination))
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        incoming = self.directory / "incoming.json"
        incoming.write_text(json.dumps(document(entry("2", "Second"))), encoding="utf-8")
        entries_before = sorted(path.name for path in self.directory.iterdir())
        code, out, _ = self.offline("import", str(incoming), "replace", "--dry-run")
        self.assertEqual((code, json.loads(out)["status"]), (0, "planned"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), entries_before)
        code, out, _ = self.offline("diff", str(incoming))
        self.assertEqual((code, json.loads(out)["status"]), (0, "different"))
        code, out, err = self.offline("import", str(incoming), "replace")
        self.assertEqual((code, err), (0, ""))
        backup = json.loads(out)["backup_path"]
        changed = json.loads(self.path.read_bytes())
        self.assertEqual(changed["private"], self.config["private"])
        self.assertEqual(changed["preset_favorites"], self.config["preset_favorites"])
        changed["private"] = "new provider setting"
        self.config = changed
        self.save()
        current = self.path.read_bytes()
        self.assertEqual(self.offline("restore", backup, "--dry-run")[0], 0)
        self.assertEqual(self.path.read_bytes(), current)
        self.assertEqual(self.offline("restore", backup)[0], 0)
        restored = json.loads(self.path.read_bytes())
        self.assertEqual(restored["private"], "new provider setting")
        self.assertEqual(restored["preset_global_favorites"]["presets"]["Home"], document(entry()))
        incoming.write_text(json.dumps(document(entry(title="Changed title"))), encoding="utf-8")
        current = self.path.read_bytes()
        code, out, _ = self.offline("import", str(incoming), "merge")
        self.assertEqual((code, json.loads(out)["status"]), (1, "blocked"))
        self.assertEqual(self.path.read_bytes(), current)

    def test_mixed_sequence_roundtrip_keeps_order_and_receiver_metadata(self):
        value = copy.deepcopy(MIXED_SEQUENCE["portable_document"])
        self.config["preset_global_favorites"]["presets"]["Home"] = value
        self.config["fixture_host"] = copy.deepcopy(MIXED_SEQUENCE["hosts"]["origin"])
        self.save()
        exported = self.directory / "mixed.json"
        self.assertEqual(self.offline("export", str(exported))[0], 0)
        self.assertEqual(json.loads(exported.read_bytes()), value)
        self.assertEqual([row["origin"]["provider"] for row in value["entries"]],
                         ["local", "m3u", "vportal", "plex", "m3u"])
        receiver = copy.deepcopy(MIXED_SEQUENCE["hosts"]["receiver"])
        self.assertNotEqual(receiver, self.config["fixture_host"])
        self.config["fixture_host"] = receiver
        self.config["preset_global_favorites"]["presets"]["Home"] = document()
        self.save()
        code, out, err = self.offline("import", str(exported), "replace")
        self.assertEqual((code, err, json.loads(out)["status"]), (0, "", "imported"))
        saved = json.loads(self.path.read_bytes())
        self.assertEqual(saved["preset_global_favorites"]["presets"]["Home"], value)
        self.assertEqual(saved["fixture_host"], receiver)
        again = self.directory / "mixed-again.json"
        self.assertEqual(self.offline("export", str(again))[0], 0)
        self.assertEqual(again.read_bytes(), exported.read_bytes())

    def test_mixed_sequence_repeat_merge_and_title_conflict(self):
        value = copy.deepcopy(MIXED_SEQUENCE["portable_document"])
        incoming = self.directory / "mixed.json"
        incoming.write_text(json.dumps(value), encoding="utf-8")
        self.config["preset_global_favorites"]["presets"]["Home"] = document()
        self.save()
        self.assertEqual(self.offline("import", str(incoming), "merge")[0], 0)
        before = self.path.read_bytes()
        code, out, err = self.offline("import", str(incoming), "merge")
        result = json.loads(out)
        self.assertEqual((code, err, result["status"]), (0, "", "unchanged"))
        for key, expected in (("added", 0), ("retained", 5), ("skipped_duplicates", 5), ("moved", 0)):
            self.assertEqual(result["summary"][key], expected)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(before)["preset_global_favorites"]["presets"]["Home"], value)
        value["entries"][1]["title"] = MIXED_SEQUENCE["title_conflict"]["incoming_title"]
        incoming.write_text(json.dumps(value), encoding="utf-8")
        for flags in (("--dry-run",), ()):
            code, out, err = self.offline("import", str(incoming), "merge", *flags)
            self.assertEqual((code, err, json.loads(out)["status"]), (1, "", "blocked"))
            self.assertEqual(json.loads(out)["summary"]["title_conflicts"], 1)
            self.assertEqual(self.path.read_bytes(), before)

    def test_mixed_sequence_unresolved_receiver_setup_preserves_portable_data(self):
        # This tests offline preservation, not player resolution or playback.
        value = copy.deepcopy(MIXED_SEQUENCE["portable_document"])
        incoming = self.directory / "mixed.json"
        incoming.write_text(json.dumps(value), encoding="utf-8")
        for scenario in MIXED_SEQUENCE["receiver_scenarios"][1:]:
            with self.subTest(scenario=scenario["name"]):
                targets = copy.deepcopy(MIXED_SEQUENCE["hosts"]["receiver"]["targets"])
                targets = [row for row in targets if row["entry_index"] not in scenario["omit_target_entries"]]
                for row in list(targets):
                    if row["entry_index"] in scenario["duplicate_target_entries"]:
                        duplicate = dict(row, private_profile_id="duplicate-profile", private_source_id="duplicate-source")
                        if "private_root_id" in duplicate:
                            duplicate["private_root_id"] = "duplicate-root"
                        targets.append(duplicate)
                self.config["fixture_host"] = {"targets": targets}
                self.config["preset_global_favorites"]["presets"]["Home"] = document()
                self.save()
                before = self.path.read_bytes()
                entries_before = sorted(path.name for path in self.directory.iterdir())
                code, out, err = self.offline("import", str(incoming), "replace", "--dry-run")
                self.assertEqual((code, err, json.loads(out)["status"]), (0, "", "planned"))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(sorted(path.name for path in self.directory.iterdir()), entries_before)
                self.assertEqual(self.offline("import", str(incoming), "replace")[0], 0)
                saved = json.loads(self.path.read_bytes())
                self.assertEqual(saved["preset_global_favorites"]["presets"]["Home"], value)
                self.assertEqual(saved["fixture_host"], {"targets": targets})

    def test_offline_invalid_input_and_missing_companion_do_not_write(self):
        bad = self.directory / "bad.json"
        bad.write_bytes(b'{"version":1,"version":1,"entries":[]}')
        before = self.path.read_bytes()
        self.assertEqual(self.offline("import", str(bad))[0], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.cli(["presets", "Empty", "fav", "global", "validate"], offline=True)[0], 1)


if __name__ == "__main__":
    unittest.main()
